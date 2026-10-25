//! Whole-board placement job over one prepared problem (AGENTEE-515).
//!
//! The job controller captures a native board once and supplies an
//! `agent-ee.placement-job-input/1` record. `prepare` validates identities,
//! geometry provenance and constraints once; `SearchState` then evaluates
//! moves incrementally through a spatial index and commits or rejects them as
//! generation-checked transactions. `full_evaluate` recomputes every term from
//! scratch and is the reference for the incremental path. The search returns a
//! shortlist of hard-feasible poses; it accepts nothing. Native
//! materialization, reload, rule checks and routing stay with the caller.
//!
//! Transaction, spatial-lookup and seeded-restart semantics follow the KRT
//! placer mechanisms selected in AGENTEE-514 (KiCadRoutingTools v0.23.7,
//! commit 83646c8b56c3571067c359cf5524167abfef4c0f: py_placer/placement/simple.py
//! touched/commit/rollback, bucketed spatial queries, simple_anneal.py seeded
//! starts). They were reimplemented here after source inspection, so the upstream
//! notice applies:
//! Edge-seat identity also follows floorplan.py at upstream commit
//! 222bb144744c3ddd979674e528bf8647e9ced8d6 (integer rectangular geometry;
//! no mating-face inference). Seat geometry is captured separately from occupancy.
//!
//! Copyright (c) 2026 drandyhaas
//!
//! Permission is hereby granted, free of charge, to any person obtaining a copy
//! of this software and associated documentation files (the "Software"), to deal
//! in the Software without restriction, including without limitation the rights
//! to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
//! copies of the Software, and to permit persons to whom the Software is
//! furnished to do so, subject to the following conditions:
//!
//! The above copyright notice and this permission notice shall be included in all
//! copies or substantial portions of the Software.
//!
//! THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
//! IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
//! FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
//! AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
//! LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
//! OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
//! SOFTWARE.
//!
//! Coordinates are integer nanometres (i64) in the native KiCad frame (y down).
//! Areas use i128. Every hard verdict and objective term is an integer, so the
//! incremental and full evaluations agree exactly; floats only drive the
//! Metropolis acceptance draw.
use anyhow::{Context, Result, bail, ensure};
use num_traits::{ToPrimitive, Zero};
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::time::Instant;
use crate::exact::{Point, Rat};
use crate::geometry::{self, Polygon};

pub const INPUT_SCHEMA: &str = "agent-ee.placement-job-input/1";
pub const OUTPUT_SCHEMA: &str = "agent-ee.placement-job-output/1";
const CELL_NM: i64 = 2_000_000;
const OCCUPANCY_RUNGS: [&str; 4] = ["courtyard", "fab+pads", "silk+pads", "pads"];

// Mirrored at the native capture boundary in placement_job.py; parity is tested.
pub const MAX_CONTOUR_VERTICES: usize = 2048;
pub const MAX_ITEM_CONTOURS: usize = 16;
pub const MAX_PROBLEM_CONTOUR_VERTICES: usize = 16384;

fn contour_count(g: &Value, total: &mut usize) -> Result<()> {
    let contours = g["polygons"].as_array().context("native copper polygons")?;
    ensure!(contours.len() <= MAX_ITEM_CONTOURS,
        "item contour count {} exceeds {}", contours.len(), MAX_ITEM_CONTOURS);
    for (i, contour) in contours.iter().enumerate() {
        let count = contour.as_array().context("native copper polygon")?.len();
        ensure!(count <= MAX_CONTOUR_VERTICES,
            "contour {i} vertex count {count} exceeds {MAX_CONTOUR_VERTICES}");
        *total += count;
        ensure!(*total <= MAX_PROBLEM_CONTOUR_VERTICES,
            "contour {i} prepared contour vertex total {} exceeds {} (contour count {count})",
            *total, MAX_PROBLEM_CONTOUR_VERTICES);
    }
    Ok(())
}

/// Axis-aligned box [x0, y0, x1, y1] in nanometres.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Rect {
    pub x0: i64,
    pub y0: i64,
    pub x1: i64,
    pub y1: i64,
}

impl Rect {
    fn parse(value: &Value, what: &str) -> Result<Self> {
        let items = value.as_array().with_context(|| format!("{what} must be [x0,y0,x1,y1]"))?;
        ensure!(items.len() == 4, "{what} must be [x0,y0,x1,y1]");
        let n: Vec<i64> = items
            .iter()
            .map(|v| v.as_i64().with_context(|| format!("{what} needs integer nanometres")))
            .collect::<Result<_>>()?;
        ensure!(n[0] < n[2] && n[1] < n[3], "{what} is empty or inverted");
        Ok(Self { x0: n[0], y0: n[1], x1: n[2], y1: n[3] })
    }
    fn grown(&self, by: i64) -> Self {
        Self { x0: self.x0 - by, y0: self.y0 - by, x1: self.x1 + by, y1: self.y1 + by }
    }
    fn union(&self, b: &Self) -> Self {
        Self { x0: self.x0.min(b.x0), y0: self.y0.min(b.y0), x1: self.x1.max(b.x1), y1: self.y1.max(b.y1) }
    }
    /// Interior intersection area (touching boxes do not overlap).
    fn overlap(&self, b: &Self) -> i128 {
        let w = (self.x1.min(b.x1) - self.x0.max(b.x0)) as i128;
        let h = (self.y1.min(b.y1) - self.y0.max(b.y0)) as i128;
        if w > 0 && h > 0 { w * h } else { 0 }
    }
    /// Area of self outside `outer`.
    fn outside(&self, outer: &Self) -> i128 {
        let area = (self.x1 - self.x0) as i128 * (self.y1 - self.y0) as i128;
        area - self.overlap(outer)
    }
    fn center2(&self) -> (i64, i64) {
        (self.x0 + self.x1, self.y0 + self.y1)
    }
    fn as_value(&self) -> Value {
        json!([self.x0, self.y0, self.x1, self.y1])
    }
}

/// KiCad orientation in quarter turns. A native point (x, y) at orientation
/// q turns to rotate(q): 90 degrees maps (x, y) to (y, -x), as pcbnew does in
/// its y-down frame (checked against native saved boards by the job tests).
pub fn rotate(x: i64, y: i64, q: i64) -> (i64, i64) {
    match q.rem_euclid(4) {
        0 => (x, y),
        1 => (y, -x),
        2 => (-x, -y),
        _ => (-y, x),
    }
}

fn place_rect(local: &Rect, pose: &Pose) -> Rect {
    let (ax, ay) = rotate(local.x0, local.y0, pose.q);
    let (bx, by) = rotate(local.x1, local.y1, pose.q);
    Rect { x0: ax.min(bx) + pose.x, y0: ay.min(by) + pose.y, x1: ax.max(bx) + pose.x, y1: ay.max(by) + pose.y }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct Pose {
    pub x: i64,
    pub y: i64,
    pub q: i64,
}

#[derive(Clone, Debug)]
pub struct Pad {
    pub uuid: String,
    pub net: Option<usize>,
    pub at: (i64, i64),
    /// Axis-aligned half extents of the copper rect in the local frame at quarter turn 0 (AGENTEE-479).
    pub half: (i64, i64),
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Role {
    /// Packed, searched part with a body occupancy.
    Movable,
    /// Fixed body: obstacle for bodies and copper.
    FixedBody,
    /// Fixed copper/mark (e.g. a copper fiducial): copper obstacle only, never a body.
    FixedCopperMark,
    /// Demonstrably silk-only, non-electrical graphic: excluded from packing.
    SilkOnly,
}

#[derive(Clone, Debug)]
pub struct Footprint {
    pub uuid: String,
    pub alias: String,
    pub native_ref: String,
    pub role: Role,
    pub initial: Pose,
    pub rotations: Vec<i64>,
    pub occupancy: Option<Rect>,
    pub occupancy_rung: Option<String>,
    /// Copper boxes with the native clearance each item requires.
    pub copper: Vec<Copper>,
    pub holes: Vec<Copper>,
    /// Solder-mask apertures (pad box grown by its mask expansion, mask graphics).
    pub mask: Vec<Rect>,
    /// Inferred routing space; soft preference only, never a hard owner rule.
    pub escape: Vec<Rect>,
    pub pads: Vec<Pad>,
    pub group: Option<usize>,
    pub edge: Option<EdgeRule>,
}

#[derive(Clone, Debug)]
pub struct Copper {
    bounds: Rect,
    clearance: i64,
    /// None preserves the legacy conservative-box contract and objective.
    polygons: Option<Vec<Polygon>>,
    // Convex pieces and boundary edges are prepared once, then only transformed.
    parts: Vec<Polygon>,
    part_bounds: geometry::PolygonBounds,
    edges: Vec<(Point, Point, geometry::Bounds)>,
}

fn rect_polygon(r: &Rect) -> Polygon {
    vec![Point::new(r.x0, r.y0), Point::new(r.x1, r.y0),
         Point::new(r.x1, r.y1), Point::new(r.x0, r.y1)]
}

impl Copper {
    fn parse(value: &Value, default_clearance: i64) -> Result<Self> {
        let bounds = Rect::parse(value.get("rect_nm").unwrap_or(value), "copper")?;
        let clearance = if value.get("rect_nm").is_some() {
            int(value, "clearance_nm")?
        } else { default_clearance };
        ensure!(clearance >= 0, "negative copper clearance");
        let polygons = if let Some(g) = value.get("geometry") {
            contour_count(g, &mut 0)?;
            ensure!(g["status"] == "conservative_native_polygon", "unsupported copper geometry status");
            ensure!(matches!(text(g, "kind")?, "circle" | "rect" | "oval" | "roundrect" | "custom" | "hole"),
                "unsupported copper geometry kind");
            ensure!(int(g, "tolerance_nm")? == 1000, "unsupported copper polygon tolerance");
            let contours = g["polygons"].as_array().context("native copper polygons")?
                .iter().map(|p| geometry::simple_polygon_bounded(p, MAX_CONTOUR_VERTICES))
                .collect::<Result<Vec<_>>>()?;
            ensure!(!contours.is_empty(), "empty native copper polygons");
            let envelope = rect_polygon(&bounds);
            ensure!(contours.iter().all(|p| geometry::contains(&envelope, p)),
                "native copper polygon exceeds indexed bounds");
            Some(contours)
        } else { None };
        Self::prepared(bounds, clearance.max(default_clearance), polygons)
    }

    fn prepared(bounds: Rect, clearance: i64, polygons: Option<Vec<Polygon>>) -> Result<Self> {
        let fallback = vec![rect_polygon(&bounds)];
        let contours = polygons.as_ref().unwrap_or(&fallback);
        let parts: Vec<Polygon> = contours.iter().flat_map(|p| {
            if geometry::convex(p) { vec![p.clone()] } else { geometry::triangles(p) }
        }).collect();
        let part_bounds = geometry::PolygonBounds::new(&parts)?;
        let edges = contours.iter().flat_map(geometry::edges).map(|(a, b)| {
            let bounds = geometry::Bounds {
                x0: a.x.clone().min(b.x.clone()), x1: a.x.clone().max(b.x.clone()),
                y0: a.y.clone().min(b.y.clone()), y1: a.y.clone().max(b.y.clone()),
            };
            (a.clone(), b.clone(), bounds)
        }).collect();
        Ok(Self { bounds, clearance, polygons, parts, part_bounds, edges })
    }

    fn at(&self, pose: &Pose) -> Self {
        let origin = Point::new(0, 0);
        let position = Point::new(pose.x, pose.y);
        let transform = |p: &Point| crate::exact::rotate(p, pose.q).plus(&position);
        let polygons = self.polygons.as_ref().map(|contours| contours.iter()
            .map(|p| p.iter().map(&transform).collect()).collect());
        let parts = self.parts.iter().map(|p| p.iter().map(&transform).collect()).collect();
        let part_bounds = self.part_bounds.at(&origin, &position, pose.q);
        let edges = self.edges.iter().map(|(a, b, bounds)|
            (transform(a), transform(b), bounds.at(&origin, &position, pose.q))).collect();
        Self { bounds: place_rect(&self.bounds, pose), clearance: self.clearance,
               polygons, parts, part_bounds, edges }
    }

    fn overlap(&self, other: &Self) -> i128 {
        let clearance = self.clearance.max(other.clearance);
        self.overlap_at_clearance(other, clearance)
    }

    fn overlap_at_clearance(&self, other: &Self, clearance: i64) -> i128 {
        let boxed = self.bounds.grown(clearance).overlap(&other.bounds);
        if boxed == 0 || (self.polygons.is_none() && other.polygons.is_none()) {
            return boxed;
        }
        if self.part_bounds.overlaps(&self.parts, &other.part_bounds, &other.parts) {
            return boxed;
        }
        // Positive-area overlap is already excluded. Only original boundary
        // edges contribute to separation; triangulation diagonals must not.
        if clearance == 0 { return 0; }
        let margin = crate::exact::Int::from(clearance);
        let limit = Rat::from_integer(margin.pow(2));
        if self.edges.iter().any(|(a, b, bounds)| {
            let expanded = bounds.grown(&margin);
            other.edges.iter().any(|(c, d, other_bounds)| expanded.meets(other_bounds)
                && geometry::segment_distance2(a, b, c, d) < limit)
        }) { boxed } else { 0 }
    }
}

#[derive(Clone, Debug)]
pub struct EdgeRule {
    pub edge: Edge,
    pub seat: Option<Rect>,
    pub max_setback_nm: Option<i64>,
    pub overhang_nm: Option<(i64, Option<i64>)>,
}

impl EdgeRule {
    fn occupancy_outline(&self, outline: &Rect) -> Rect {
        let mut allowed = *outline;
        if let Some((_, Some(max))) = self.overhang_nm {
            match self.edge {
                Edge::West => allowed.x0 -= max,
                Edge::East => allowed.x1 += max,
                Edge::North => allowed.y0 -= max,
                Edge::South => allowed.y1 += max,
            }
        }
        allowed
    }
    fn short_overhang(&self, body: &Rect, outline: &Rect) -> i64 {
        self.overhang_nm.map(|(min, _)| (min - (-self.edge.setback(body, outline)).max(0)).max(0)).unwrap_or(0)
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Edge {
    West,
    East,
    North,
    South,
}

impl Edge {
    const ALL: [Self; 4] = [Self::West, Self::North, Self::East, Self::South];

    fn name(self) -> &'static str {
        match self { Self::West => "west", Self::North => "north", Self::East => "east", Self::South => "south" }
    }

    fn nearest(rect: &Rect, outline: &Rect, margin: i64) -> Self {
        let reached: Vec<Self> = Self::ALL.into_iter().filter(|e| e.setback(rect, outline) <= margin).collect();
        if reached.len() > 1 {
            let outside: Vec<Self> = reached.iter().copied().filter(|e| e.setback(rect, outline) < -margin).collect();
            if outside.len() == 1 { return outside[0]; }
            let span = |e: &Self| match e {
                Self::West | Self::East => rect.y1 - rect.y0,
                Self::North | Self::South => rect.x1 - rect.x0,
            };
            let longest = reached.iter().map(span).max().unwrap();
            return reached.into_iter().filter(|e| span(e) == longest)
                .min_by_key(|e| e.setback(rect, outline)).unwrap();
        }
        Self::ALL.into_iter().min_by_key(|e| e.setback(rect, outline)).unwrap()
    }

    fn parse(text: &str) -> Result<Self> {
        Ok(match text {
            "west" => Self::West,
            "east" => Self::East,
            "north" => Self::North,
            "south" => Self::South,
            other => bail!("unsupported edge {other:?}"),
        })
    }
    fn setback(&self, r: &Rect, outline: &Rect) -> i64 {
        match self {
            Self::West => r.x0 - outline.x0,
            Self::East => outline.x1 - r.x1,
            Self::North => r.y0 - outline.y0,
            Self::South => outline.y1 - r.y1,
        }
    }
}

#[derive(Clone, Debug)]
pub struct Proximity {
    pub a: usize,
    pub a_pads: Vec<usize>,
    pub b: usize,
    pub b_pads: Vec<usize>,
    pub max_nm: i64,
    /// `pad_edge`: upstream signed rect gap per declared subject pad, net-narrowed (AGENTEE-479).
    pub pad_edge: bool,
}

/// A source-bound group relation measured as the signed pad-edge gap (AGENTEE-479 R1/R2).
#[derive(Clone, Debug)]
pub struct Link {
    pub kind: String,
    pub group: String,
    pub a: usize,
    pub a_pads: Vec<usize>,
    pub b: usize,
    pub b_pads: Vec<usize>,
}

#[derive(Clone, Debug)]
pub struct Locality {
    pub a: usize,
    pub b: usize,
    pub a_offset: [Rat; 2],
    pub b_offset: [Rat; 2],
    pub limit_squared: Rat,
    pub hard: bool,
    pub obligation: String,
    pub role: String,
}

#[derive(Clone, Debug)]
pub struct Weights {
    pub wire: i64,
    pub group: i64,
    pub proximity: i64,
    /// Group relation links (AGENTEE-479 R1/R2); defaults to the group weight.
    pub link: i64,
    pub edge: i64,
    /// Penalty per square micrometre of hard overlap during exploration.
    pub hard: i64,
    /// Penalty per square micrometre of copper in a terminal escape region.
    pub escape: i64,
}

/// Immutable, validated problem. Built once per job input.
#[derive(Debug)]
pub struct Prepared {
    pub identity: String,
    pub footprints: Vec<Footprint>,
    pub outline: Rect,
    pub copper_edge_nm: i64,
    pub clearance_nm: i64,
    pub mask_web_nm: i64,
    pub hole_to_hole_nm: i64,
    pub nets: Vec<String>,
    pub net_pads: Vec<Vec<(usize, usize)>>,
    pub groups: Vec<(String, usize, Vec<usize>)>,
    pub proximity: Vec<Proximity>,
    pub links: Vec<Link>,
    pub locality: Vec<Locality>,
    pub weights: Weights,
    pub movable: Vec<usize>,
}

fn text<'a>(v: &'a Value, key: &str) -> Result<&'a str> {
    v.get(key).and_then(Value::as_str).with_context(|| format!("missing string {key}"))
}

fn int(v: &Value, key: &str) -> Result<i64> {
    v.get(key).and_then(Value::as_i64).with_context(|| format!("missing integer {key}"))
}

/// Deterministic, collision-checked solver aliases over the full native UUID.
/// Aliases are positional in UUID order, never a UUID prefix: legacy KiCad UUIDs
/// can share long prefixes (`00000000-0000-...`).
pub fn aliases(uuids: &[String], supplied: &[Option<String>]) -> Result<Vec<String>> {
    let mut seen = BTreeSet::new();
    for uuid in uuids {
        ensure!(
            uuid.len() == 36 && uuid.bytes().all(|b| b == b'-' || b.is_ascii_hexdigit()),
            "footprint identity {uuid:?} is not a full native UUID"
        );
        ensure!(seen.insert(uuid.to_ascii_lowercase()), "duplicate native footprint UUID {uuid}");
    }
    let mut order: Vec<usize> = (0..uuids.len()).collect();
    order.sort_by(|&a, &b| uuids[a].cmp(&uuids[b]));
    let mut out = vec![String::new(); uuids.len()];
    for (rank, &index) in order.iter().enumerate() {
        out[index] = format!("fp{rank:03}");
    }
    for (index, alias) in supplied.iter().enumerate() {
        if let Some(alias) = alias {
            ensure!(*alias == out[index], "supplied alias {alias:?} for {} differs from the derived {}", uuids[index], out[index]);
        }
    }
    let unique: BTreeSet<_> = out.iter().collect();
    ensure!(unique.len() == out.len(), "solver alias collision");
    Ok(out)
}

pub fn prepare(input: &Value) -> Result<Prepared> {
    ensure!(input["schema"] == INPUT_SCHEMA, "unsupported placement job schema");
    let identity = text(input, "problem_sha256")?.to_string();
    ensure!(identity.len() == 64, "problem_sha256 must be a full SHA-256");
    let outline = Rect::parse(&input["outline"]["rect_nm"], "outline")?;
    ensure!(input["outline"]["kind"] == "rectangle", "only a proven rectangular board outline is supported");
    let rules = &input["rules"];
    let clearance_nm = int(rules, "copper_clearance_nm")?;
    let copper_edge_nm = int(rules, "copper_edge_clearance_nm")?;
    let mask_web_nm = rules.get("mask_min_web_nm").and_then(Value::as_i64).unwrap_or(0);
    let hole_to_hole_nm = match rules.get("hole_to_hole_clearance_nm") {
        Some(_) => int(rules, "hole_to_hole_clearance_nm")?,
        None => 0,
    };
    ensure!(hole_to_hole_nm >= 0, "negative hole-to-hole clearance");
    ensure!(clearance_nm >= 0 && copper_edge_nm >= 0 && mask_web_nm >= 0, "negative rule");
    let rows = input["footprints"].as_array().context("footprints must be an array")?;
    let mut vertices = 0;
    for row in rows {
        for key in ["copper_local_nm", "holes_local_nm"] {
            for item in row[key].as_array().into_iter().flatten() {
                if let Some(g) = item.get("geometry") {
                    contour_count(g, &mut vertices).with_context(|| format!(
                        "{} ({}) pad {}", row["native_ref"], row["uuid"], g["pad_uuid"]))?;
                }
            }
        }
    }
    let uuids: Vec<String> = rows.iter().map(|f| text(f, "uuid").map(str::to_string)).collect::<Result<_>>()?;
    let supplied: Vec<Option<String>> =
        rows.iter().map(|f| f.get("alias").and_then(Value::as_str).map(str::to_string)).collect();
    let alias = aliases(&uuids, &supplied)?;
    let mut nets: Vec<String> = Vec::new();
    let mut net_index: HashMap<String, usize> = HashMap::new();
    let groups_in = input["groups"].as_array().cloned().unwrap_or_default();
    let mut footprints: Vec<Footprint> = Vec::new();
    let mut ref_index: BTreeMap<String, Vec<usize>> = BTreeMap::new();
    for (i, row) in rows.iter().enumerate() {
        let role = match text(row, "role")? {
            "movable" => Role::Movable,
            "fixed_body" => Role::FixedBody,
            "fixed_copper_mark" => Role::FixedCopperMark,
            "silk_only" => Role::SilkOnly,
            other => bail!("{}: unsupported role {other:?}", uuids[i]),
        };
        let pose = &row["pose"];
        let initial = Pose { x: int(pose, "x_nm")?, y: int(pose, "y_nm")?, q: int(pose, "quarter_turns")? };
        let occ = &row["occupancy"];
        let (occupancy, rung) = match occ.get("status").and_then(Value::as_str) {
            Some("derived") | Some("courtyard") => {
                let rung = text(occ, "rung")?;
                ensure!(OCCUPANCY_RUNGS.contains(&rung), "{}: unknown occupancy rung {rung:?}", uuids[i]);
                ensure!(occ["label"] == "derived_occupancy" || rung == "courtyard",
                    "{}: derived occupancy must be labelled derived_occupancy", uuids[i]);
                (Some(Rect::parse(&occ["local_rect_nm"], "occupancy")?), Some(rung.to_string()))
            }
            Some("none") => (None, None),
            other => bail!("{}: occupancy status {other:?} refused", uuids[i]),
        };
        ensure!(role != Role::Movable || occupancy.is_some(), "{}: movable part without occupancy", uuids[i]);
        ensure!(!matches!(role, Role::FixedCopperMark | Role::SilkOnly) || occupancy.is_none(),
            "{}: a fixed copper mark or silk-only graphic has no body occupancy", uuids[i]);
        let copper = row["copper_local_nm"].as_array().context("copper_local_nm")?
            .iter().map(|r| Copper::parse(r, clearance_nm)).collect::<Result<Vec<_>>>()?;
        let holes = match row.get("holes_local_nm") {
            Some(v) => v.as_array().context("holes_local_nm")?.iter()
                .map(|r| {
                    ensure!(r["geometry"]["kind"] == "hole", "unqualified hole reservation");
                    let pad_uuid = text(&r["geometry"], "pad_uuid")?;
                    ensure!(row["pads"].as_array().context("pads")?.iter().any(|p| p["uuid"] == pad_uuid),
                        "hole identity is absent from native pads");
                    Copper::parse(r, 0)
                }).collect::<Result<Vec<_>>>()?,
            None => Vec::new(),
        };
        ensure!(role != Role::SilkOnly || holes.is_empty(), "silk-only graphic carries holes");
        let mask = row.get("mask_local_nm").and_then(Value::as_array).cloned().unwrap_or_default()
            .iter().map(|r| Rect::parse(r, "mask")).collect::<Result<Vec<_>>>()?;
        let escape = match row.get("escape_local_nm") {
            Some(v) => v.as_array().context("escape_local_nm")?.iter().map(|r| {
                ensure!(r["authority"] == "agent-heuristic/1", "escape space must be labelled agent-heuristic/1");
                Rect::parse(&r["rect_nm"], "escape space")
            }).collect::<Result<Vec<_>>>()?,
            None => Vec::new(),
        };
        ensure!(role != Role::SilkOnly || copper.is_empty(), "{}: silk-only graphic carries copper", uuids[i]);
        let mut pads = Vec::new();
        for pad in row["pads"].as_array().context("pads")? {
            let net = match pad.get("net").and_then(Value::as_str) {
                Some(n) if !n.is_empty() => Some(*net_index.entry(n.to_string()).or_insert_with(|| {
                    nets.push(n.to_string());
                    nets.len() - 1
                })),
                _ => None,
            };
            let at = pad["local_nm"].as_array().context("pad local_nm")?;
            let half = match pad.get("half_nm").and_then(Value::as_array) {
                Some(h) => { let (hx, hy) = (h[0].as_i64().context("pad half x")?, h[1].as_i64().context("pad half y")?);
                    ensure!(hx >= 0 && hy >= 0, "negative pad half extent"); (hx, hy) },
                None => (0, 0),
            };
            pads.push(Pad {
                uuid: text(pad, "uuid")?.to_string(),
                net,
                at: (at[0].as_i64().context("pad x")?, at[1].as_i64().context("pad y")?),
                half,
            });
        }
        let rotations: Vec<i64> = match row.get("rotations") {
            Some(v) => v.as_array().context("rotations")?.iter()
                .map(|q| q.as_i64().context("rotation").map(|q| q.rem_euclid(4))).collect::<Result<_>>()?,
            None => vec![initial.q],
        };
        ensure!(!rotations.is_empty(), "{}: no eligible rotation", uuids[i]);
        let edge = match row.get("edge").filter(|e| !e.is_null()) {
            Some(e) => {
                let max_setback_nm = match e.get("max_setback_nm").filter(|v| !v.is_null()) {
                    Some(_) => { let n = int(e, "max_setback_nm")?; ensure!(n >= 0, "negative edge setback"); Some(n) },
                    None => None,
                };
                let overhang_nm = match e.get("overhang_nm") {
                    Some(v) => { let lo = int(v, "min")?;
                        let hi = match v.get("max") {
                            Some(Value::Null) => None,
                            Some(_) => Some(int(v, "max")?),
                            None => bail!("overhang maximum must be explicit or null"),
                        };
                        ensure!(lo >= 0 && hi.is_none_or(|h| lo <= h), "malformed overhang interval");
                        ensure!(hi.is_some() || lo == 0, "positive overhang minimum needs a finite maximum");
                        Some((lo, hi)) },
                    None => None,
                };
                Some(EdgeRule {
                edge: Edge::parse(text(e, "edge")?)?,
                seat: e.get("seat_local_nm").map(|r| Rect::parse(r, "edge seat")).transpose()?,
                max_setback_nm, overhang_nm,
            }) },
            None => None,
        };
        let native_ref = text(row, "native_ref")?.to_string();
        if role != Role::SilkOnly {
            let named = ref_index.entry(native_ref.clone()).or_default();
            // Mark labels are display text; their UUIDs still identify distinct obstacles.
            ensure!(named.is_empty() || (role == Role::FixedCopperMark
                    && named.iter().all(|&other| footprints[other].role == Role::FixedCopperMark)),
                "duplicate physical reference {native_ref}");
            named.push(i);
        }
        footprints.push(Footprint {
            uuid: uuids[i].clone(), alias: alias[i].clone(), native_ref, role, initial, rotations,
            occupancy, occupancy_rung: rung, copper, holes, mask, escape, pads, group: None, edge,
        });
    }
    let physical_reference = |reference: &str, context: &str| -> Result<usize> {
        let named = ref_index.get(reference)
            .with_context(|| format!("{context}: unknown physical reference {reference}"))?;
        ensure!(named.len() == 1, "{context}: ambiguous physical reference {reference} ({} fixed copper marks)", named.len());
        Ok(named[0])
    };
    let mut groups = Vec::new();
    for (g, group) in groups_in.iter().enumerate() {
        let name = text(group, "name")?.to_string();
        let member = |r: &Value| -> Result<usize> {
            let r = r.as_str().context("group member")?;
            physical_reference(r, &format!("group {name}"))
        };
        let anchor = member(&group["anchor"])?;
        let members = group["refs"].as_array().context("group refs")?.iter().map(member).collect::<Result<Vec<_>>>()?;
        ensure!(members.contains(&anchor), "group {name}: anchor not a member");
        for &m in &members {
            ensure!(footprints[m].group.is_none(), "{} is in two groups", footprints[m].native_ref);
            footprints[m].group = Some(g);
        }
        groups.push((name, anchor, members));
    }
    let mut net_pads = vec![Vec::new(); nets.len()];
    for (f, fp) in footprints.iter().enumerate() {
        for (p, pad) in fp.pads.iter().enumerate() {
            if let Some(n) = pad.net {
                net_pads[n].push((f, p));
            }
        }
    }
    let pad_of = |f: usize, number_uuid: &Value| -> Result<usize> {
        let u = number_uuid.as_str().context("pad uuid")?;
        footprints[f].pads.iter().position(|p| p.uuid == u).with_context(|| format!("unknown pad {u}"))
    };
    let mut proximity = Vec::new();
    for row in input["proximity"].as_array().cloned().unwrap_or_default() {
        let a = physical_reference(text(&row, "ref")?, "proximity ref")?;
        let b = physical_reference(text(&row, "near")?, "proximity near")?;
        let a_pads: Vec<usize> = row["ref_pads"].as_array().context("ref_pads")?.iter().map(|u| pad_of(a, u)).collect::<Result<_>>()?;
        let b_pads: Vec<usize> = row["near_pads"].as_array().context("near_pads")?.iter().map(|u| pad_of(b, u)).collect::<Result<_>>()?;
        let pad_edge = row.get("basis").and_then(Value::as_str) == Some("pad_edge");
        if pad_edge {
            ensure!(a_pads.iter().all(|&i| footprints[a].pads[i].half != (0, 0))
                    && b_pads.iter().all(|&i| footprints[b].pads[i].half != (0, 0)),
                "pad_edge proximity needs captured pad half extents");
        }
        proximity.push(Proximity { a, a_pads, b, b_pads, max_nm: int(&row, "max_nm")?, pad_edge });
    }
    let links = compile_links(input, &footprints, &groups, &nets, &net_pads, &physical_reference)?;
    let mut locality = Vec::new();
    if let Some(raw) = input.get("locality") {
        let document = &raw["document"];
        crate::locality::validate(document)?;
        let facts = raw["footprints"].as_array().context("locality footprints required")?;
        for obligation in document["obligations"].as_array().unwrap() {
            ensure!(obligation["metric"] == "pad-centre-to-centre", "unsupported locality metric");
            for role in crate::locality::roles(obligation) {
                let pair = &obligation[role];
                ensure!(pair["from"]["net"] == pair["to"]["net"], "locality endpoint nets differ");
                let authority = crate::locality::classify(pair, &document["source_records"]);
                ensure!(authority["enforcement"] != "integrity", "locality source integrity failed");
                let endpoint = |key: &str| -> Result<(usize, [Rat; 2])> {
                    let e = &pair[key];
                    let f = physical_reference(text(e, "reference")?, "locality endpoint")?;
                    let center = crate::locality::resolve(facts, e)?;
                    let initial = footprints[f].initial;
                    Ok((f, rotate_rational([center[0].clone() - Rat::from_integer(initial.x.into()),
                        center[1].clone() - Rat::from_integer(initial.y.into())], -initial.q)))
                };
                let (a, a_offset) = endpoint("from")?;
                let (b, b_offset) = endpoint("to")?;
                let limit = int(pair, "max_distance_nm")?;
                ensure!(limit >= 0, "negative locality limit");
                locality.push(Locality { a, b, a_offset, b_offset,
                    limit_squared: Rat::from_integer((limit as i128 * limit as i128).into()),
                    hard: authority["enforcement"] == "hard",
                    obligation: text(obligation, "id")?.to_string(), role: role.to_string() });
            }
        }
    }
    let w = &input["weights"];
    let weights = Weights {
        wire: w.get("wire").and_then(Value::as_i64).unwrap_or(1),
        group: w.get("group").and_then(Value::as_i64).unwrap_or(1),
        proximity: w.get("proximity").and_then(Value::as_i64).unwrap_or(4),
        link: w.get("link").and_then(Value::as_i64).or_else(|| w.get("group").and_then(Value::as_i64)).unwrap_or(1),
        edge: w.get("edge").and_then(Value::as_i64).unwrap_or(4),
        hard: w.get("hard").and_then(Value::as_i64).unwrap_or(50),
        escape: w.get("escape").and_then(Value::as_i64).unwrap_or(20),
    };
    let movable = (0..footprints.len()).filter(|&i| footprints[i].role == Role::Movable).collect();
    Ok(Prepared { identity, footprints, outline, copper_edge_nm, clearance_nm, mask_web_nm, hole_to_hole_nm, nets, net_pads, groups, proximity, links, locality, weights, movable })
}

/// Integer cost breakdown. `hard` is the exact infeasibility measure: the sum
/// of body overlaps, grown-copper overlaps and outline/edge excursions (nm^2 or
/// nm). Zero means every encoded hard constraint holds for these poses.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Terms {
    pub hard: i128,
    pub hard_count: i64,
    pub wire: i128,
    pub group: i128,
    pub proximity: i128,
    pub link: i128,
    pub edge: i128,
    pub escape: i128,
}

impl Terms {
    fn add(&mut self, o: &Terms, sign: i128) {
        self.hard += sign * o.hard;
        self.hard_count += sign as i64 * o.hard_count;
        self.wire += sign * o.wire;
        self.group += sign * o.group;
        self.proximity += sign * o.proximity;
        self.link += sign * o.link;
        self.edge += sign * o.edge;
        self.escape += sign * o.escape;
    }
    pub fn cost(&self, w: &Weights) -> i128 {
        // hard overlap in nm^2 is scaled to um^2 so the penalty stays commensurate with nm lengths.
        w.hard as i128 * (self.hard / 1_000_000 + self.hard_count as i128 * 1_000_000)
            + w.wire as i128 * self.wire
            + w.group as i128 * self.group
            + w.proximity as i128 * self.proximity
            + w.link as i128 * self.link
            + w.edge as i128 * self.edge
            + w.escape as i128 * (self.escape / 1_000_000)
    }
    fn value(&self) -> Value {
        json!({"hard": self.hard.to_string(), "hard_count": self.hard_count, "wire_nm": self.wire.to_string(),
               "group_nm": self.group.to_string(), "proximity_excess_nm": self.proximity.to_string(),
               "link_gap_nm": self.link.to_string(),
               "edge_setback_nm": self.edge.to_string(), "escape_overlap_nm2": self.escape.to_string()})
    }
}

struct Placed {
    body: Option<Rect>,
    edge_seat: Option<Rect>,
    copper: Vec<Copper>,
    holes: Vec<Copper>,
    mask: Vec<Rect>,
    escape: Vec<Rect>,
    reach: Option<Rect>,
}

fn placed(p: &Prepared, f: usize, pose: &Pose) -> Placed {
    let fp = &p.footprints[f];
    let body = fp.occupancy.map(|r| place_rect(&r, pose));
    let edge_seat = fp.edge.as_ref().and_then(|e| e.seat).map(|r| place_rect(&r, pose)).or(body);
    let copper: Vec<Copper> = fp.copper.iter().map(|c| c.at(pose)).collect();
    let holes: Vec<Copper> = fp.holes.iter().map(|c| c.at(pose)).collect();
    let mask: Vec<Rect> = fp.mask.iter().map(|r| place_rect(r, pose)).collect();
    let escape: Vec<Rect> = fp.escape.iter().map(|r| place_rect(r, pose)).collect();
    let mut reach = body;
    for g in copper.iter().map(|c| c.bounds.grown(c.clearance))
        .chain(holes.iter().map(|h| h.bounds.grown(h.clearance.max(p.hole_to_hole_nm))))
        .chain(mask.iter().map(|m| m.grown(p.mask_web_nm)))
        .chain(escape.iter().copied()) {
        reach = Some(reach.map_or(g, |r| r.union(&g)));
    }
    Placed { body, edge_seat, copper, holes, mask, escape, reach }
}

fn pad_at(p: &Prepared, f: usize, pad: usize, pose: &Pose) -> (i64, i64) {
    let (x, y) = p.footprints[f].pads[pad].at;
    let (rx, ry) = rotate(x, y, pose.q);
    (rx + pose.x, ry + pose.y)
}

fn interacts(p: &Prepared, a: usize, b: usize) -> bool {
    let (ra, rb) = (&p.footprints[a].role, &p.footprints[b].role);
    *ra != Role::SilkOnly && *rb != Role::SilkOnly && (*ra == Role::Movable || *rb == Role::Movable)
}

/// Pair term between two different footprints.
fn pair_terms(p: &Prepared, a: &Placed, b: &Placed) -> Terms {
    let mut t = Terms::default();
    // Conservative boxes are a congestion preference, not a clearance verdict.
    for (spaces, copper) in [(&a.escape, &b.copper), (&b.escape, &a.copper)] {
        for space in spaces {
            for item in copper {
                t.escape += space.overlap(&item.bounds);
            }
        }
    }
    if let (Some(x), Some(y)) = (&a.body, &b.body) {
        let o = x.overlap(y);
        if o > 0 {
            t.hard += o;
            t.hard_count += 1;
        }
    }
    // Bounds prune distant items; native outer polygons decide pad clearance.
    for ca in &a.copper {
        for cb in &b.copper {
            let o = ca.overlap(cb);
            if o > 0 {
                t.hard += o;
                t.hard_count += 1;
            }
        }
    }
    // Drills use board hole rules, independently of pad copper overrides.
    for (holes, copper) in [(&a.holes, &b.copper), (&b.holes, &a.copper)] {
        for hole in holes {
            for c in copper {
                let o = hole.overlap_at_clearance(c, hole.clearance);
                if o > 0 { t.hard += o; t.hard_count += 1; }
            }
        }
    }
    for ha in &a.holes {
        for hb in &b.holes {
            let o = ha.overlap_at_clearance(hb, p.hole_to_hole_nm);
            if o > 0 { t.hard += o; t.hard_count += 1; }
        }
    }
    // Mask apertures of different footprints keep the native minimum web.
    for ma in &a.mask {
        let g = ma.grown(p.mask_web_nm);
        for mb in &b.mask {
            let o = g.overlap(mb);
            if o > 0 {
                t.hard += o;
                t.hard_count += 1;
            }
        }
    }
    t
}

/// Pair terms are evaluated in footprint-index order, as the full evaluator does,
/// because a clearance-grown overlap is not symmetric in its arguments.
fn ordered_pair(p: &Prepared, a: usize, at_a: &Placed, b: usize, at_b: &Placed) -> Terms {
    if a < b { pair_terms(p, at_a, at_b) } else { pair_terms(p, at_b, at_a) }
}

/// Unary term for one movable footprint: containment and edge rules.
fn unary_terms(p: &Prepared, f: usize, at: &Placed) -> Terms {
    let mut t = Terms::default();
    if let (Some(rule), Some(seat)) = (&p.footprints[f].edge, &at.edge_seat) {
        let nearest = Edge::nearest(seat, &p.outline, p.copper_edge_nm);
        if nearest != rule.edge {
            let distance = (rule.edge.setback(seat, &p.outline) - nearest.setback(seat, &p.outline)).max(1);
            t.hard += distance as i128 * 1_000_000;
            t.hard_count += 1;
        }
    }
    if p.footprints[f].role != Role::Movable {
        return t;
    }
    if let Some(body) = &at.body {
        let allowed = p.footprints[f].edge.as_ref().map(|e| e.occupancy_outline(&p.outline)).unwrap_or(p.outline);
        let o = body.outside(&allowed);
        if o > 0 {
            t.hard += o;
            t.hard_count += 1;
        }
        if let Some(rule) = &p.footprints[f].edge {
            let short = rule.short_overhang(body, &p.outline);
            if short > 0 { t.hard += short as i128 * 1_000_000; t.hard_count += 1; }
            let setback = rule.edge.setback(body, &p.outline);
            match rule.max_setback_nm {
                Some(max) if setback > max => {
                    t.hard += (setback - max) as i128 * 1_000_000;
                    t.hard_count += 1;
                }
                _ => {}
            }
            t.edge += setback.max(0) as i128;
        }
    }
    let inner = p.outline.grown(-p.copper_edge_nm);
    for c in &at.copper {
        let o = c.bounds.outside(&inner);
        if o > 0 {
            t.hard += o;
            t.hard_count += 1;
        }
    }
    t
}

fn net_terms(p: &Prepared, n: usize, poses: &[Pose]) -> i128 {
    let pads = &p.net_pads[n];
    if pads.len() < 2 {
        return 0;
    }
    let (mut x0, mut y0, mut x1, mut y1) = (i64::MAX, i64::MAX, i64::MIN, i64::MIN);
    for &(f, pad) in pads {
        let (x, y) = pad_at(p, f, pad, &poses[f]);
        x0 = x0.min(x);
        y0 = y0.min(y);
        x1 = x1.max(x);
        y1 = y1.max(y);
    }
    ((x1 - x0) + (y1 - y0)) as i128
}

fn group_terms(p: &Prepared, f: usize, poses: &[Pose]) -> i128 {
    // Member-to-anchor Manhattan distance of body centres; the anchor carries its whole group.
    let Some(g) = p.footprints[f].group else { return 0 };
    let (_, anchor, members) = &p.groups[g];
    let centre = |i: usize| {
        let at = placed(p, i, &poses[i]);
        at.body.map(|b| b.center2()).unwrap_or((2 * poses[i].x, 2 * poses[i].y))
    };
    let (ax, ay) = centre(*anchor);
    if f == *anchor {
        members.iter().filter(|&&m| m != f).map(|&m| {
            let (mx, my) = centre(m);
            (((mx - ax).abs() + (my - ay).abs()) / 2) as i128
        }).sum()
    } else {
        0
    }
}

/// Axis-aligned copper rect of a pad under a pose (quarter turns keep rects axis-aligned).
fn pad_rect(p: &Prepared, f: usize, pad: usize, pose: &Pose) -> Rect {
    let (cx, cy) = pad_at(p, f, pad, pose);
    let (hx, hy) = p.footprints[f].pads[pad].half;
    let (hx, hy) = if pose.q.rem_euclid(2) == 1 { (hy, hx) } else { (hx, hy) };
    Rect { x0: cx - hx, y0: cy - hy, x1: cx + hx, y1: cy + hy }
}

/// Upstream kicad-route@c4af095 signed axis-aligned gap: negative overlap depth is never clamped.
pub fn rect_gap(a: &Rect, b: &Rect) -> i64 {
    let dx = (a.x0 - b.x1).max(b.x0 - a.x1);
    let dy = (a.y0 - b.y1).max(b.y0 - a.y1);
    if dx < 0 && dy < 0 { dx.max(dy) }
    else if dx > 0 && dy > 0 { ((dx as f64).hypot(dy as f64)).round() as i64 }
    else { dx.max(dy) }
}

/// Per-subject-pad signed pad-edge legs with net narrowing (one leg per declared subject pad).
fn pad_edge_legs(p: &Prepared, a: usize, a_pads: &[usize], b: usize, b_pads: &[usize], poses: &[Pose]) -> Vec<i64> {
    a_pads.iter().map(|&pa| {
        let net = p.footprints[a].pads[pa].net;
        let same: Vec<usize> = b_pads.iter().copied().filter(|&pb| net.is_some() && p.footprints[b].pads[pb].net == net).collect();
        let partners = if same.is_empty() { b_pads.to_vec() } else { same };
        let ra = pad_rect(p, a, pa, &poses[a]);
        partners.iter().map(|&pb| rect_gap(&ra, &pad_rect(p, b, pb, &poses[b]))).min().unwrap_or(i64::MAX)
    }).collect()
}

fn proximity_terms(p: &Prepared, k: usize, poses: &[Pose]) -> i128 {
    let r = &p.proximity[k];
    if r.pad_edge {
        return pad_edge_legs(p, r.a, &r.a_pads, r.b, &r.b_pads, poses).iter()
            .map(|&gap| (gap - r.max_nm).max(0) as i128).sum();
    }
    let mut best = i64::MAX;
    for &pa in &r.a_pads {
        for &pb in &r.b_pads {
            let (ax, ay) = pad_at(p, r.a, pa, &poses[r.a]);
            let (bx, by) = pad_at(p, r.b, pb, &poses[r.b]);
            best = best.min((ax - bx).abs().max((ay - by).abs()));
        }
    }
    (best - r.max_nm).max(0) as i128
}

/// Non-negative signed pad-edge gap of one group link (R1 interface or R2 intra-group/anchor link).
fn link_gap(p: &Prepared, k: usize, poses: &[Pose]) -> i64 {
    let l = &p.links[k];
    let mut best = i64::MAX;
    for &pa in &l.a_pads {
        let ra = pad_rect(p, l.a, pa, &poses[l.a]);
        for &pb in &l.b_pads {
            best = best.min(rect_gap(&ra, &pad_rect(p, l.b, pb, &poses[l.b])));
        }
    }
    best
}

fn link_terms(p: &Prepared, k: usize, poses: &[Pose]) -> i128 {
    link_gap(p, k, poses).max(0) as i128
}

/// Compile R2 intra-group signal/anchor links and R1 declared-near interface links.
///
/// Shared rails are an explicit evidence-bound input (`rails.shared`, `rails.scoped`); a net
/// spanning more than two groups outside them is never linked (it is reported upstream as an
/// unknown multi-group net), so rails cannot merge blocks.
fn compile_links(input: &Value, footprints: &[Footprint], groups: &[(String, usize, Vec<usize>)], nets: &[String],
                 net_pads: &[Vec<(usize, usize)>], physical_reference: &dyn Fn(&str, &str) -> Result<usize>) -> Result<Vec<Link>> {
    let rails = input.get("rails").cloned().unwrap_or(Value::Null);
    let shared: BTreeSet<String> = rails.get("shared").and_then(Value::as_array).cloned().unwrap_or_default()
        .iter().filter_map(|v| v.as_str().map(String::from)).collect();
    let scoped = rails.get("scoped").cloned().unwrap_or(Value::Null);
    let group_of = |f: usize| footprints[f].group;
    let mut span: Vec<BTreeSet<usize>> = vec![BTreeSet::new(); nets.len()];
    for (n, pads) in net_pads.iter().enumerate() {
        for &(f, _) in pads {
            if let Some(g) = group_of(f) { span[n].insert(g); }
        }
    }
    let group_index: BTreeMap<&str, usize> = groups.iter().enumerate().map(|(g, (name, _, _))| (name.as_str(), g)).collect();
    let pads_on = |f: usize, n: usize| -> Vec<usize> {
        footprints[f].pads.iter().enumerate().filter(|(_, pad)| pad.net == Some(n)).map(|(i, _)| i).collect()
    };
    let all_pads = |f: usize| -> Vec<usize> { (0..footprints[f].pads.len()).collect() };
    let mut links = Vec::new();
    for (g, (name, anchor, members)) in groups.iter().enumerate() {
        for (n, net) in nets.iter().enumerate() {
            let scoped_here = scoped.get(net).and_then(|s| s.get("intra_group")).and_then(Value::as_str) == Some(name.as_str());
            if shared.contains(net) && !scoped_here { continue; }
            if !shared.contains(net) && span[n].len() > 2 { continue; }
            let on: Vec<usize> = members.iter().copied().filter(|&m| !pads_on(m, n).is_empty()).collect();
            for i in 0..on.len() {
                for j in i + 1..on.len() {
                    links.push(Link { kind: "R2_intra_group_signal".into(), group: name.clone(), a: on[i],
                                      a_pads: pads_on(on[i], n), b: on[j], b_pads: pads_on(on[j], n) });
                }
            }
        }
        for &m in members {
            if m != *anchor && !footprints[m].pads.is_empty() && !footprints[*anchor].pads.is_empty() {
                links.push(Link { kind: "R2_member_to_anchor".into(), group: name.clone(), a: m,
                                  a_pads: all_pads(m), b: *anchor, b_pads: all_pads(*anchor) });
            }
        }
        let group_row = &input["groups"][g];
        for target in group_row.get("near").and_then(Value::as_array).cloned().unwrap_or_default() {
            let target = target.as_str().context("group near")?;
            let &h = group_index.get(target).with_context(|| format!("group {name}: unknown near group {target}"))?;
            let (_, _, others) = &groups[h];
            let mut best: Option<(i64, usize, Vec<usize>, usize, Vec<usize>)> = None;
            for (n, net) in nets.iter().enumerate() {
                let pair_ok = scoped.get(net).and_then(|s| s.get("near_pairs")).and_then(Value::as_array)
                    .map(|pairs| pairs.iter().any(|pr| pr[0] == name.as_str() && pr[1] == target)).unwrap_or(false);
                if shared.contains(net) && !pair_ok { continue; }
                if !shared.contains(net) && span[n].len() > 2 { continue; }
                for &a in members {
                    let ap = pads_on(a, n);
                    if ap.is_empty() { continue; }
                    for &b in others {
                        let bp = pads_on(b, n);
                        if bp.is_empty() { continue; }
                        let mut gap = i64::MAX;
                        for &pa in &ap { for &pb in &bp {
                            gap = gap.min(rect_gap(&pad_rect_initial(footprints, a, pa), &pad_rect_initial(footprints, b, pb)));
                        } }
                        if best.as_ref().is_none_or(|bst| gap < bst.0) { best = Some((gap, a, ap.clone(), b, bp.clone())); }
                    }
                }
            }
            if let Some((_, a, ap, b, bp)) = best {
                links.push(Link { kind: "R1_declared_near_interface".into(), group: name.clone(), a, a_pads: ap, b, b_pads: bp });
            }
        }
    }
    let _ = physical_reference;
    Ok(links)
}

fn pad_rect_initial(footprints: &[Footprint], f: usize, pad: usize) -> Rect {
    let fp = &footprints[f];
    let (x, y) = fp.pads[pad].at;
    let (rx, ry) = rotate(x, y, fp.initial.q);
    let (cx, cy) = (rx + fp.initial.x, ry + fp.initial.y);
    let (hx, hy) = fp.pads[pad].half;
    let (hx, hy) = if fp.initial.q.rem_euclid(2) == 1 { (hy, hx) } else { (hx, hy) };
    Rect { x0: cx - hx, y0: cy - hy, x1: cx + hx, y1: cy + hy }
}

/// Deterministic post-anneal member refinement (AGENTEE-479 step 3).
///
/// Members are visited by largest link gap; each is offered seats adjacent to its linked
/// partner pad in every eligible rotation. A seat is accepted only when the full evaluation
/// stays hard-legal (zero overlap, outline, hole and edge violations) and the cost strictly
/// decreases. Fixed, anchor and non-movable footprints never move.
pub fn refine_members(p: &Prepared, poses: &mut Vec<Pose>, passes: usize) -> Vec<Value> {
    let mut rows = Vec::new();
    let mut current = full_evaluate(p, poses);
    if current.hard != 0 || current.hard_count != 0 { return rows; }
    let mut cost = current.cost(&p.weights);
    for pass in 0..passes {
        let mut improved = false;
        let mut order: Vec<(i64, usize)> = (0..p.links.len()).map(|k| (link_gap(p, k, poses), k)).collect();
        order.sort_by(|x, y| y.0.cmp(&x.0).then(x.1.cmp(&y.1)));
        for (gap, k) in order {
            if gap <= 0 { continue; }
            let l = &p.links[k];
            let (member, partner, member_pads, partner_pads) = if p.movable.contains(&l.a) && l.kind != "R2_member_to_anchor" || (l.kind == "R2_member_to_anchor") {
                (l.a, l.b, &l.a_pads, &l.b_pads)
            } else if p.movable.contains(&l.b) { (l.b, l.a, &l.b_pads, &l.a_pads) } else { continue };
            if !p.movable.contains(&member) || p.footprints[member].group.map(|g| p.groups[g].1) == Some(member) { continue; }
            let before = poses[member];
            let mut best: Option<(i128, Pose, Terms)> = None;
            for &q in &p.footprints[member].rotations {
                for &pm in member_pads.iter() {
                    for &pp in partner_pads.iter() {
                        let target = pad_rect(p, partner, pp, &poses[partner]);
                        let local = &p.footprints[member].pads[pm];
                        let (lx, ly) = rotate(local.at.0, local.at.1, q);
                        let (hx, hy) = if q.rem_euclid(2) == 1 { (local.half.1, local.half.0) } else { local.half };
                        let gap = p.clearance_nm;
                        let seats = [((target.x1 + gap + hx), (target.y0 + target.y1) / 2), ((target.x0 - gap - hx), (target.y0 + target.y1) / 2),
                                     ((target.x0 + target.x1) / 2, (target.y1 + gap + hy)), ((target.x0 + target.x1) / 2, (target.y0 - gap - hy))];
                        for (cx, cy) in seats {
                            let trial = Pose { x: cx - lx, y: cy - ly, q };
                            if trial == before { continue; }
                            poses[member] = trial;
                            let terms = full_evaluate(p, poses);
                            poses[member] = before;
                            if terms.hard != 0 || terms.hard_count != 0 { continue; }
                            let c = terms.cost(&p.weights);
                            if c < cost && best.as_ref().is_none_or(|b| c < b.0) { best = Some((c, trial, terms)); }
                        }
                    }
                }
            }
            if let Some((c, trial, terms)) = best {
                poses[member] = trial;
                let gap_after = link_gap(p, k, poses);
                poses[member] = before;
                rows.push(json!({"pass": pass, "link": l.kind, "group": l.group, "member": p.footprints[member].native_ref,
                                 "partner": p.footprints[partner].native_ref, "gap_before_nm": gap,
                                 "gap_after_nm": gap_after,
                                 "cost_before": cost.to_string(), "cost_after": c.to_string(),
                                 "from": {"x_nm": before.x, "y_nm": before.y, "quarter_turns": before.q.rem_euclid(4)},
                                 "to": {"x_nm": trial.x, "y_nm": trial.y, "quarter_turns": trial.q.rem_euclid(4)}}));
                poses[member] = trial;
                cost = c;
                current = terms;
                improved = true;
            }
        }
        if !improved { break; }
    }
    let _ = current;
    rows
}

fn rotate_rational([x, y]: [Rat; 2], q: i64) -> [Rat; 2] {
    match q.rem_euclid(4) { 0 => [x, y], 1 => [y, -x], 2 => [-x, -y], _ => [-y, x] }
}

fn locality_terms(p: &Prepared, k: usize, poses: &[Pose]) -> Terms {
    let r = &p.locality[k];
    let at = |offset: &[Rat; 2], f: usize| {
        let [x, y] = rotate_rational(offset.clone(), poses[f].q);
        [x + Rat::from_integer(poses[f].x.into()), y + Rat::from_integer(poses[f].y.into())]
    };
    let [ax, ay] = at(&r.a_offset, r.a);
    let [bx, by] = at(&r.b_offset, r.b);
    let (dx, dy) = (ax - bx, ay - by);
    let excess = &dx * &dx + &dy * &dy - &r.limit_squared;
    if excess <= Rat::zero() { return Terms::default(); }
    // The exact rational comparison decides feasibility. Cap only the search
    // penalty, keeping extreme diagnostic inputs from overflowing i128 totals.
    let penalty = excess.ceil().to_integer().to_i128().unwrap_or(i128::MAX).min(1_000_000_000_000_000_000);
    if r.hard { Terms { hard: penalty, hard_count: 1, ..Terms::default() } }
    else { Terms { proximity: penalty, ..Terms::default() } }
}

/// Exact hard violations of one pose vector, named by native reference (diagnostic).
pub fn violations(p: &Prepared, poses: &[Pose]) -> Vec<Value> {
    let at: Vec<Placed> = (0..p.footprints.len()).map(|f| placed(p, f, &poses[f])).collect();
    let mut out = Vec::new();
    for a in 0..at.len() {
        let fp = &p.footprints[a];
        if let (Some(rule), Some(seat)) = (&fp.edge, &at[a].edge_seat) {
            let nearest = Edge::nearest(seat, &p.outline, p.copper_edge_nm);
            if nearest != rule.edge {
                out.push(json!({"ref": fp.native_ref, "uuid": fp.uuid, "kind": "edge_identity",
                    "detail": {"expected": rule.edge.name(), "actual": nearest.name()}}));
            }
        }
        if fp.role == Role::Movable {
            let named = |kind: &str, detail: Value| json!({"ref": fp.native_ref, "uuid": fp.uuid, "kind": kind, "detail": detail});
            if let Some(body) = &at[a].body {
                let allowed = fp.edge.as_ref().map(|e| e.occupancy_outline(&p.outline)).unwrap_or(p.outline);
                if body.outside(&allowed) > 0 {
                    out.push(named("occupancy_outside_outline", json!(body.as_value())));
                }
                if let Some(rule) = &fp.edge {
                    if rule.short_overhang(body, &p.outline) > 0 {
                        out.push(named("edge_overhang_min", json!(rule.overhang_nm)));
                    }
                    let setback = rule.edge.setback(body, &p.outline);
                    if rule.max_setback_nm.is_some_and(|m| setback > m) {
                        out.push(named("edge_setback", json!({"setback_nm": setback, "max_setback_nm": rule.max_setback_nm})));
                    }
                }
            }
            let inner = p.outline.grown(-p.copper_edge_nm);
            if at[a].copper.iter().any(|c| c.bounds.outside(&inner) > 0) {
                out.push(named("copper_edge_clearance", Value::Null));
            }
        }
        for b in a + 1..at.len() {
            if interacts(p, a, b) {
                let t = pair_terms(p, &at[a], &at[b]);
                if t.hard_count > 0 {
                    out.push(json!({"ref": p.footprints[a].native_ref, "with": p.footprints[b].native_ref,
                        "uuid": p.footprints[a].uuid, "with_uuid": p.footprints[b].uuid, "kind": "pair", "count": t.hard_count}));
                }
            }
        }
    }
    for (k, r) in p.locality.iter().enumerate() {
        if locality_terms(p, k, poses).hard_count > 0 {
            out.push(json!({"kind": "electrical_locality", "ref": p.footprints[r.a].native_ref,
                "with": p.footprints[r.b].native_ref, "obligation": r.obligation, "role": r.role}));
        }
    }
    out
}

/// Reference evaluator: every term from scratch, no cache or index.
pub fn full_evaluate(p: &Prepared, poses: &[Pose]) -> Terms {
    let at: Vec<Placed> = (0..p.footprints.len()).map(|f| placed(p, f, &poses[f])).collect();
    let mut t = Terms::default();
    for a in 0..at.len() {
        t.add(&unary_terms(p, a, &at[a]), 1);
        for b in a + 1..at.len() {
            if interacts(p, a, b) {
                t.add(&pair_terms(p, &at[a], &at[b]), 1);
            }
        }
    }
    for n in 0..p.nets.len() {
        t.wire += net_terms(p, n, poses);
    }
    for f in 0..p.footprints.len() {
        t.group += group_terms(p, f, poses);
    }
    for k in 0..p.proximity.len() {
        t.proximity += proximity_terms(p, k, poses);
    }
    for k in 0..p.links.len() {
        t.link += link_terms(p, k, poses);
    }
    for k in 0..p.locality.len() { t.add(&locality_terms(p, k, poses), 1); }
    t
}

/// Mutable search state: poses, spatial index and cached contributions.
#[derive(Clone)]
pub struct SearchState {
    pub poses: Vec<Pose>,
    pub generation: u64,
    pub totals: Terms,
    net_cost: Vec<i128>,
    group_cost: Vec<i128>,
    prox_cost: Vec<i128>,
    link_cost: Vec<i128>,
    locality_cost: Vec<Terms>,
    cells: HashMap<(i64, i64), BTreeSet<usize>>,
    cell_of: Vec<Vec<(i64, i64)>>,
    pub counters: Counters,
}

#[derive(Clone, Debug, Default)]
pub struct Counters {
    pub evaluations: u64,
    pub commits: u64,
    pub rejects: u64,
    pub stale_refusals: u64,
    pub full_evaluations: u64,
}

/// One evaluated move. It carries the generation it was evaluated against.
#[derive(Clone, Debug)]
pub struct Trial {
    pub generation: u64,
    pub moves: Vec<(usize, Pose)>,
    pub delta: Terms,
    after_nets: Vec<(usize, i128)>,
    after_groups: Vec<(usize, i128)>,
    after_prox: Vec<(usize, i128)>,
    after_links: Vec<(usize, i128)>,
    after_locality: Vec<(usize, Terms)>,
}

fn cells_for(r: &Rect) -> Vec<(i64, i64)> {
    let mut out = Vec::new();
    for cx in r.x0.div_euclid(CELL_NM)..=r.x1.div_euclid(CELL_NM) {
        for cy in r.y0.div_euclid(CELL_NM)..=r.y1.div_euclid(CELL_NM) {
            out.push((cx, cy));
        }
    }
    out
}

impl SearchState {
    pub fn new(p: &Prepared, poses: Vec<Pose>) -> Result<Self> {
        ensure!(poses.len() == p.footprints.len(), "pose vector does not cover every footprint");
        for (f, fp) in p.footprints.iter().enumerate() {
            if fp.role != Role::Movable {
                ensure!(poses[f] == fp.initial, "{} is fixed and cannot move", fp.native_ref);
            } else {
                ensure!(fp.rotations.contains(&poses[f].q.rem_euclid(4)), "{}: ineligible rotation", fp.native_ref);
            }
        }
        let mut s = SearchState {
            net_cost: (0..p.nets.len()).map(|n| net_terms(p, n, &poses)).collect(),
            group_cost: (0..p.footprints.len()).map(|f| group_terms(p, f, &poses)).collect(),
            prox_cost: (0..p.proximity.len()).map(|k| proximity_terms(p, k, &poses)).collect(),
            link_cost: (0..p.links.len()).map(|k| link_terms(p, k, &poses)).collect(),
            locality_cost: (0..p.locality.len()).map(|k| locality_terms(p, k, &poses)).collect(),
            totals: full_evaluate(p, &poses),
            poses,
            generation: 0,
            cells: HashMap::new(),
            cell_of: vec![Vec::new(); p.footprints.len()],
            counters: Counters::default(),
        };
        s.counters.full_evaluations += 1;
        for f in 0..p.footprints.len() {
            s.index(p, f);
        }
        Ok(s)
    }

    fn index(&mut self, p: &Prepared, f: usize) {
        for c in std::mem::take(&mut self.cell_of[f]) {
            if let Some(set) = self.cells.get_mut(&c) {
                set.remove(&f);
            }
        }
        if p.footprints[f].role == Role::SilkOnly {
            return;
        }
        if let Some(reach) = placed(p, f, &self.poses[f]).reach {
            let cells = cells_for(&reach);
            for c in &cells {
                self.cells.entry(*c).or_default().insert(f);
            }
            self.cell_of[f] = cells;
        }
    }

    fn neighbours(&self, reach: &Rect) -> BTreeSet<usize> {
        let mut out = BTreeSet::new();
        for c in cells_for(reach) {
            if let Some(set) = self.cells.get(&c) {
                out.extend(set.iter().copied());
            }
        }
        out
    }

    /// Hard and objective delta of moving `moves` together; the state is untouched.
    pub fn evaluate(&mut self, p: &Prepared, moves: &[(usize, Pose)]) -> Result<Trial> {
        self.counters.evaluations += 1;
        let movers: BTreeSet<usize> = moves.iter().map(|m| m.0).collect();
        ensure!(movers.len() == moves.len(), "a footprint moves twice in one trial");
        for &(f, pose) in moves {
            let fp = &p.footprints[f];
            ensure!(fp.role == Role::Movable, "{} is fixed", fp.native_ref);
            ensure!(fp.rotations.contains(&pose.q.rem_euclid(4)), "{}: ineligible rotation", fp.native_ref);
        }
        let mut after = self.poses.clone();
        for &(f, pose) in moves {
            after[f] = pose;
        }
        let mut delta = Terms::default();
        let old: BTreeMap<usize, Placed> = movers.iter().map(|&f| (f, placed(p, f, &self.poses[f]))).collect();
        let new: BTreeMap<usize, Placed> = movers.iter().map(|&f| (f, placed(p, f, &after[f]))).collect();
        for &f in &movers {
            delta.add(&unary_terms(p, f, &old[&f]), -1);
            delta.add(&unary_terms(p, f, &new[&f]), 1);
            // Old contributors come from the committed index; new neighbours from a
            // query at the new pose. Movers pair with each other separately.
            let old_n = old[&f].reach.map(|r| self.neighbours(&r)).unwrap_or_default();
            let new_n = new[&f].reach.map(|r| self.neighbours(&r)).unwrap_or_default();
            for &g in old_n.iter().filter(|g| !movers.contains(g)) {
                if interacts(p, f, g) {
                    delta.add(&ordered_pair(p, f, &old[&f], g, &placed(p, g, &self.poses[g])), -1);
                }
            }
            for &g in new_n.iter().filter(|g| !movers.contains(g)) {
                if interacts(p, f, g) {
                    delta.add(&ordered_pair(p, f, &new[&f], g, &placed(p, g, &self.poses[g])), 1);
                }
            }
        }
        let list: Vec<usize> = movers.iter().copied().collect();
        for (i, &a) in list.iter().enumerate() {
            for &b in &list[i + 1..] {
                if interacts(p, a, b) {
                    delta.add(&pair_terms(p, &old[&a], &old[&b]), -1);
                    delta.add(&pair_terms(p, &new[&a], &new[&b]), 1);
                }
            }
        }
        let nets: BTreeSet<usize> = movers.iter()
            .flat_map(|&f| p.footprints[f].pads.iter().filter_map(|pad| pad.net)).collect();
        let mut after_nets = Vec::new();
        for n in nets {
            let v = net_terms(p, n, &after);
            delta.wire += v - self.net_cost[n];
            after_nets.push((n, v));
        }
        let anchors: BTreeSet<usize> = movers.iter()
            .filter_map(|&f| p.footprints[f].group.map(|g| p.groups[g].1)).collect();
        let mut after_groups = Vec::new();
        for a in anchors {
            let v = group_terms(p, a, &after);
            delta.group += v - self.group_cost[a];
            after_groups.push((a, v));
        }
        let mut after_prox = Vec::new();
        for (k, r) in p.proximity.iter().enumerate() {
            if movers.contains(&r.a) || movers.contains(&r.b) {
                let v = proximity_terms(p, k, &after);
                delta.proximity += v - self.prox_cost[k];
                after_prox.push((k, v));
            }
        }
        let mut after_links = Vec::new();
        for (k, l) in p.links.iter().enumerate() {
            if movers.contains(&l.a) || movers.contains(&l.b) {
                let v = link_terms(p, k, &after);
                delta.link += v - self.link_cost[k];
                after_links.push((k, v));
            }
        }
        let mut after_locality = Vec::new();
        for (k, r) in p.locality.iter().enumerate() {
            if movers.contains(&r.a) || movers.contains(&r.b) {
                let value = locality_terms(p, k, &after);
                delta.add(&value, 1);
                delta.add(&self.locality_cost[k], -1);
                after_locality.push((k, value));
            }
        }
        Ok(Trial { generation: self.generation, moves: moves.to_vec(), delta, after_nets, after_groups, after_prox, after_links, after_locality })
    }

    /// Apply a trial atomically. A trial evaluated against another generation is refused.
    pub fn commit(&mut self, p: &Prepared, trial: Trial) -> Result<()> {
        if trial.generation != self.generation {
            self.counters.stale_refusals += 1;
            bail!("stale trial: evaluated at generation {}, state is at {}", trial.generation, self.generation);
        }
        for &(f, pose) in &trial.moves {
            self.poses[f] = pose;
        }
        for &(f, _) in &trial.moves {
            self.index(p, f);
        }
        for (n, v) in trial.after_nets {
            self.net_cost[n] = v;
        }
        for (a, v) in trial.after_groups {
            self.group_cost[a] = v;
        }
        for (k, v) in trial.after_prox {
            self.prox_cost[k] = v;
        }
        for (k, v) in trial.after_links {
            self.link_cost[k] = v;
        }
        for (k, value) in trial.after_locality { self.locality_cost[k] = value; }
        self.totals.add(&trial.delta, 1);
        self.generation += 1;
        self.counters.commits += 1;
        Ok(())
    }

    pub fn reject(&mut self, _trial: Trial) {
        self.counters.rejects += 1;
    }

    /// Digest of the committed semantic state (poses, totals, caches, index).
    pub fn digest(&self) -> String {
        let mut cells: Vec<_> = self.cells.iter().filter(|(_, s)| !s.is_empty())
            .map(|(c, s)| (c, s.iter().copied().collect::<Vec<_>>())).collect();
        cells.sort();
        crate::blocks::digest(&json!({
            "poses": self.poses.iter().map(|p| [p.x, p.y, p.q]).collect::<Vec<_>>(),
            "generation": self.generation, "totals": self.totals.value(),
            "net": self.net_cost.iter().map(|v| v.to_string()).collect::<Vec<_>>(),
            "group": self.group_cost.iter().map(|v| v.to_string()).collect::<Vec<_>>(),
            "prox": self.prox_cost.iter().map(|v| v.to_string()).collect::<Vec<_>>(),
            "locality": self.locality_cost.iter().map(|v| v.value()).collect::<Vec<_>>(),
            "cells": format!("{cells:?}"),
        }))
    }
}

/// splitmix64: a named, versioned PRNG; each seed owns its stream.
pub struct Rng(u64);

impl Rng {
    pub fn new(seed: u64) -> Self {
        Self(seed ^ 0x5157_4ee5_1500_0001)
    }
    pub fn next(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9e37_79b9_7f4a_7c15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
        z ^ (z >> 31)
    }
    fn below(&mut self, n: u64) -> u64 {
        self.next() % n.max(1)
    }
    fn range(&mut self, lo: i64, hi: i64) -> i64 {
        if hi <= lo { lo } else { lo + self.below((hi - lo) as u64 + 1) as i64 }
    }
    fn unit(&mut self) -> f64 {
        (self.next() >> 11) as f64 / (1u64 << 53) as f64
    }
}

const GRID_NM: i64 = 50_000;

fn snap(v: i64) -> i64 {
    v.div_euclid(GRID_NM) * GRID_NM
}

fn start_poses(p: &Prepared, seed: u64, clustered: bool, rng: &mut Rng) -> Vec<Pose> {
    let mut poses: Vec<Pose> = p.footprints.iter().map(|f| f.initial).collect();
    let o = &p.outline;
    let centres: Vec<(i64, i64)> = p.groups.iter()
        .map(|(_, anchor, _)| {
            let fp = &p.footprints[*anchor];
            if fp.role != Role::Movable {
                (fp.initial.x, fp.initial.y)
            } else {
                (rng.range(o.x0, o.x1), rng.range(o.y0, o.y1))
            }
        }).collect();
    for &f in &p.movable {
        let fp = &p.footprints[f];
        let q = fp.rotations[rng.below(fp.rotations.len() as u64) as usize];
        let (x, y) = match (clustered, fp.group) {
            (true, Some(g)) => {
                let (cx, cy) = centres[g];
                (cx + rng.range(-2_000_000, 2_000_000), cy + rng.range(-2_000_000, 2_000_000))
            }
            _ => (rng.range(o.x0, o.x1), rng.range(o.y0, o.y1)),
        };
        poses[f] = Pose { x: snap(x.clamp(o.x0, o.x1)), y: snap(y.clamp(o.y0, o.y1)), q };
    }
    let _ = seed;
    poses
}

#[derive(Clone, Debug)]
pub struct Candidate {
    pub seed: u64,
    pub poses: Vec<Pose>,
    pub terms: Terms,
    pub cost: i128,
    pub step: u64,
}

pub struct Limits {
    pub steps_per_seed: u64,
    pub deadline: Option<Instant>,
    pub cancel_file: Option<std::path::PathBuf>,
    pub check_every: u64,
    pub initial_temperature: f64,
    /// Differential check: compare against the full evaluator every N commits (0 = off).
    pub verify_every: u64,
    /// Hard-penalty multiplier reached at the end of a seed (linear ramp).
    pub penalty_ramp: u64,
}

pub enum Stop {
    Completed,
    Deadline,
    Cancelled,
}

impl Limits {
    fn stop(&self) -> Option<Stop> {
        if self.cancel_file.as_ref().is_some_and(|f| f.exists()) {
            return Some(Stop::Cancelled);
        }
        if self.deadline.is_some_and(|d| Instant::now() >= d) {
            return Some(Stop::Deadline);
        }
        None
    }
}

/// One seeded annealing run. Infeasible intermediate states are internal
/// exploration; only a state whose hard measure is exactly zero is retained,
/// and a better feasible state replaces the seed's best only on commit.
pub fn anneal(p: &Prepared, seed: u64, clustered: bool, limits: &Limits) -> Result<(Option<Candidate>, Counters, Stop, u64, ((i64, i128), Vec<Value>, Option<Candidate>))> {
    let mut rng = Rng::new(seed);
    let mut state = SearchState::new(p, start_poses(p, seed, clustered, &mut rng))?;
    let mut best: Option<Candidate> = None;
    let w = &p.weights;
    let span = (p.outline.x1 - p.outline.x0).max(p.outline.y1 - p.outline.y0);
    let total = limits.steps_per_seed.max(1);
    let t0 = limits.initial_temperature;
    let mut least_hard: (i64, i128) = (i64::MAX, i128::MAX);
    let mut least_poses = state.poses.clone();
    let mut stop = Stop::Completed;
    let mut step = 0;
    // A fully anchored board has exactly one placement. Grade it without
    // selecting a move from an empty set; native checks still judge the board.
    if p.movable.is_empty() {
        least_hard = (state.totals.hard_count, state.totals.hard);
        if least_hard == (0, 0) {
            best = Some(Candidate { seed, poses: state.poses.clone(), terms: state.totals.clone(),
                                   cost: state.totals.cost(w), step });
        }
    }
    while step < total && !p.movable.is_empty() {
        if step % limits.check_every.max(1) == 0 {
            if let Some(s) = limits.stop() {
                stop = s;
                break;
            }
        }
        let frac = step as f64 / total as f64;
        let temperature = t0 * (1.0 - frac).powi(3) + 1.0;
        let reach = ((span as f64) * (0.5 * (1.0 - frac) + 0.01)) as i64;
        let f = p.movable[rng.below(p.movable.len() as u64) as usize];
        let fp = &p.footprints[f];
        let kind = rng.below(10);
        let moves: Vec<(usize, Pose)> = if kind < 6 {
            let cur = state.poses[f];
            vec![(f, Pose { x: snap(cur.x + rng.range(-reach, reach)), y: snap(cur.y + rng.range(-reach, reach)), q: cur.q })]
        } else if kind < 8 && fp.rotations.len() > 1 {
            let cur = state.poses[f];
            vec![(f, Pose { q: fp.rotations[rng.below(fp.rotations.len() as u64) as usize], ..cur })]
        } else if let Some(g) = fp.group {
            // Translate the movable members; mechanical anchors stay fixed.
            let (dx, dy) = (snap(rng.range(-reach, reach)), snap(rng.range(-reach, reach)));
            p.groups[g].2.iter().filter(|&&m| p.footprints[m].role == Role::Movable).map(|&m| {
                let c = state.poses[m];
                (m, Pose { x: c.x + dx, y: c.y + dy, q: c.q })
            }).collect()
        } else {
            let cur = state.poses[f];
            vec![(f, Pose { x: snap(cur.x + rng.range(-reach, reach)), y: snap(cur.y + rng.range(-reach, reach)), q: cur.q })]
        };
        let draw = rng.unit();
        let trial = state.evaluate(p, &moves)?;
        let mut next = state.totals.clone();
        next.add(&trial.delta, 1);
        let ramp = 1 + (frac * limits.penalty_ramp as f64) as i128;
        let next_cost = next.cost(w) + (ramp - 1) * w.hard as i128 * (next.hard / 1_000_000 + next.hard_count as i128 * 1_000_000);
        let cost = state.totals.cost(w) + (ramp - 1) * w.hard as i128 * (state.totals.hard / 1_000_000 + state.totals.hard_count as i128 * 1_000_000);
        let d = (next_cost - cost) as f64;
        if d <= 0.0 || draw < (-d / temperature).exp() {
            state.commit(p, trial)?;
            if limits.verify_every > 0 && state.counters.commits % limits.verify_every == 0 {
                state.counters.full_evaluations += 1;
                let full = full_evaluate(p, &state.poses);
                ensure!(full == state.totals, "incremental terms diverged from the full evaluator at step {step}: {:?} vs {:?}", state.totals, full);
            }
            let plain = state.totals.cost(w);
            if state.totals.hard == 0 && state.totals.hard_count == 0
                && best.as_ref().is_none_or(|b| plain < b.cost) {
                best = Some(Candidate { seed, poses: state.poses.clone(), terms: state.totals.clone(), cost: plain, step });
            }
            if (state.totals.hard_count, state.totals.hard) < least_hard {
                least_hard = (state.totals.hard_count, state.totals.hard);
                least_poses = state.poses.clone();
            }
        } else {
            state.reject(trial);
        }
        step += 1;
    }
    // The retained candidate must survive the independent full evaluator.
    if let Some(b) = &best {
        let check = full_evaluate(p, &b.poses);
        state.counters.full_evaluations += 1;
        ensure!(check == b.terms, "incremental terms diverged from the full evaluator");
    }
    let least = if best.is_some() || least_hard.0 == i64::MAX { Vec::new() } else { violations(p, &least_poses) };
    let diagnostic = (best.is_none() && least_hard.0 != i64::MAX).then(|| {
        let terms = full_evaluate(p, &least_poses);
        Candidate { seed, cost: terms.cost(w), terms, poses: least_poses, step }
    });
    Ok((best, state.counters, stop, step, (least_hard, least, diagnostic)))
}

/// Measured group links of a pose vector (signed pad-edge gaps in nm), for reports.
pub fn link_rows(p: &Prepared, poses: &[Pose]) -> Value {
    Value::Array(p.links.iter().enumerate().map(|(k, l)| json!({
        "kind": l.kind, "group": l.group, "a": p.footprints[l.a].native_ref, "b": p.footprints[l.b].native_ref,
        "gap_nm": link_gap(p, k, poses)})).collect())
}

fn pose_rows(p: &Prepared, poses: &[Pose]) -> Value {
    Value::Array(p.footprints.iter().enumerate().map(|(f, fp)| {
        let pose = poses[f];
        let at = placed(p, f, &pose);
        json!({"uuid": fp.uuid, "alias": fp.alias, "native_ref": fp.native_ref,
               "x_nm": pose.x, "y_nm": pose.y, "quarter_turns": pose.q.rem_euclid(4),
               "moved": pose != fp.initial,
               "occupancy_rect_nm": at.body.map(|b| b.as_value()),
               "pads": fp.pads.iter().enumerate().map(|(i, pad)| {
                   let (x, y) = pad_at(p, f, i, &pose);
                   json!({"uuid": pad.uuid, "x_nm": x, "y_nm": y})
               }).collect::<Vec<_>>()})
    }).collect())
}

/// Independent re-evaluation of explicit pose vectors (`{uuid: [x_nm, y_nm, quarter_turns]}`):
/// predicted native pad positions, exact terms and named violations. Fixed parts must be unchanged.
fn evaluate_poses(p: &Prepared, rows: &Value, prepare_s: f64) -> Result<Value> {
    let started = Instant::now();
    let index: BTreeMap<&str, usize> = p.footprints.iter().enumerate().map(|(i, f)| (f.uuid.as_str(), i)).collect();
    let mut out = Vec::new();
    for row in rows.as_array().context("evaluate must be an array of pose maps")? {
        let mut poses: Vec<Pose> = p.footprints.iter().map(|f| f.initial).collect();
        for (uuid, pose) in row.as_object().context("pose map")? {
            let f = *index.get(uuid.as_str()).with_context(|| format!("unknown footprint UUID {uuid}"))?;
            let v = pose.as_array().context("pose [x, y, q]")?;
            ensure!(v.len() == 3, "pose [x, y, q]");
            poses[f] = Pose { x: v[0].as_i64().context("x")?, y: v[1].as_i64().context("y")?, q: v[2].as_i64().context("q")?.rem_euclid(4) };
        }
        // The same structural checks a search state enforces.
        SearchState::new(p, poses.clone())?;
        let terms = full_evaluate(p, &poses);
        out.push(json!({"terms": terms.value(), "feasible": terms.hard == 0 && terms.hard_count == 0,
                        "violations": violations(p, &poses), "poses": pose_rows(p, &poses)}));
    }
    Ok(json!({"schema": OUTPUT_SCHEMA, "problem_sha256": p.identity, "status": "evaluated", "evaluations": out,
              "timing": {"prepare_s": prepare_s, "evaluate_s": started.elapsed().as_secs_f64()},
              "engine_version": env!("CARGO_PKG_VERSION")}))
}

/// Process entry: prepare once, run the declared seed portfolio, emit a shortlist.
pub fn run(input: &Value) -> Result<Value> {
    let started = Instant::now();
    let p = prepare(input)?;
    let prepare_s = started.elapsed().as_secs_f64();
    if let Some(rows) = input.get("evaluate") {
        return evaluate_poses(&p, rows, prepare_s);
    }
    let search = &input["search"];
    let seeds: Vec<(u64, bool)> = search["seeds"].as_array().context("search.seeds")?.iter()
        .map(|s| Ok((s["seed"].as_u64().context("seed")?, s["start"] == "clustered")))
        .collect::<Result<_>>()?;
    let skip: BTreeSet<u64> = search["completed_seeds"].as_array().cloned().unwrap_or_default()
        .iter().filter_map(Value::as_u64).collect();
    let deadline_ms = search.get("deadline_ms").and_then(Value::as_u64);
    let limits = Limits {
        steps_per_seed: search["steps_per_seed"].as_u64().context("steps_per_seed")?,
        deadline: deadline_ms.map(|ms| started + std::time::Duration::from_millis(ms)),
        cancel_file: search.get("cancel_file").and_then(Value::as_str).map(Into::into),
        check_every: search.get("check_every").and_then(Value::as_u64).unwrap_or(256),
        verify_every: search.get("verify_every").and_then(Value::as_u64).unwrap_or(0),
        penalty_ramp: search.get("penalty_ramp").and_then(Value::as_u64).unwrap_or(20),
        initial_temperature: search.get("initial_temperature_nm").and_then(Value::as_f64).unwrap_or(5_000_000.0),
    };
    let shortlist_size = search["shortlist"].as_u64().unwrap_or(3) as usize;
    let mut found: Vec<Candidate> = Vec::new();
    let mut infeasible: Vec<(Candidate, Vec<Value>)> = Vec::new();
    let mut seed_rows = Vec::new();
    let mut status = "completed";
    let mut counters = Counters::default();
    for &(seed, clustered) in &seeds {
        if skip.contains(&seed) {
            seed_rows.push(json!({"seed": seed, "status": "skipped_completed"}));
            continue;
        }
        if let Some(s) = limits.stop() {
            status = match s { Stop::Cancelled => "cancelled", _ => "deadline" };
            seed_rows.push(json!({"seed": seed, "status": "not_started"}));
            continue;
        }
        let t = Instant::now();
        let (best, c, stop, steps, least) = anneal(&p, seed, clustered, &limits)?;
        // AGENTEE-479: deterministic post-anneal member refinement on the feasible result.
        let (best, refinement) = match best {
            Some(mut b) if !p.links.is_empty() => {
                let rows = refine_members(&p, &mut b.poses, 3);
                if !rows.is_empty() {
                    b.terms = full_evaluate(&p, &b.poses);
                    b.cost = b.terms.cost(&p.weights);
                }
                (Some(b), rows)
            }
            other => (other, Vec::new()),
        };
        counters.evaluations += c.evaluations;
        counters.commits += c.commits;
        counters.rejects += c.rejects;
        counters.stale_refusals += c.stale_refusals;
        counters.full_evaluations += c.full_evaluations;
        let seed_status = match stop { Stop::Completed => "completed", Stop::Deadline => "deadline", Stop::Cancelled => "cancelled" };
        if seed_status != "completed" {
            status = seed_status;
        }
        seed_rows.push(json!({"seed": seed, "start": if clustered {"clustered"} else {"scattered"},
            "status": seed_status, "steps": steps, "elapsed_s": t.elapsed().as_secs_f64(),
            "feasible": best.is_some(), "best_cost": best.as_ref().map(|b| b.cost.to_string()),
            "least_hard_count": if least.0.0 == i64::MAX { Value::Null } else { json!(least.0.0) },
            "least_hard_violations": least.1.clone(),
            "best_terms": best.as_ref().map(|b| b.terms.value()),
            "refinement": refinement,
            "links": best.as_ref().map(|b| link_rows(&p, &b.poses))}));
        let _ = crate::progress::emit(json!({"event": "seed", "seed": seed, "status": seed_status, "feasible": best.is_some(),
            "poses": best.as_ref().map(|b| pose_rows(&p, &b.poses)), "cost": best.as_ref().map(|b| b.cost.to_string())}));
        found.extend(best);
        if let Some(d) = least.2 {
            infeasible.push((d, least.1));
        }
    }
    found.sort_by(|a, b| a.cost.cmp(&b.cost).then(a.seed.cmp(&b.seed)));
    let mut shortlist = Vec::new();
    let mut seen = BTreeSet::new();
    for c in &found {
        if shortlist.len() >= shortlist_size {
            break;
        }
        let key = format!("{:?}", c.poses);
        if seen.insert(key) {
            shortlist.push(json!({"seed": c.seed, "step": c.step, "cost": c.cost.to_string(), "terms": c.terms.value(),
                "full_evaluation_equal": full_evaluate(&p, &c.poses) == c.terms, "poses": pose_rows(&p, &c.poses)}));
        }
    }
    // No feasible state: the least-infeasible states are DIAGNOSTIC candidates with
    // their exact violations. They are never feasible and never promoted as valid.
    infeasible.sort_by(|a, b| (a.0.terms.hard_count, a.0.terms.hard, a.0.cost).cmp(&(b.0.terms.hard_count, b.0.terms.hard, b.0.cost)).then(a.0.seed.cmp(&b.0.seed)));
    // Diversity first: the best state of each distinct violation-kind set, then the rest by rank.
    let signature = |v: &Vec<Value>| v.iter().map(|x| format!("{}:{}", x["kind"], x["ref"])).collect::<BTreeSet<_>>();
    let mut chosen: Vec<usize> = Vec::new();
    let mut kinds = BTreeSet::new();
    for (i, (_, v)) in infeasible.iter().enumerate() {
        if chosen.len() < shortlist_size && kinds.insert(signature(v)) {
            chosen.push(i);
        }
    }
    for i in 0..infeasible.len() {
        if chosen.len() < shortlist_size && !chosen.contains(&i) {
            chosen.push(i);
        }
    }
    chosen.sort();
    let diagnostic: Vec<Value> = chosen.iter().map(|&i| &infeasible[i]).map(|(c, v)| json!({
        "seed": c.seed, "step": c.step, "cost": c.cost.to_string(), "terms": c.terms.value(), "feasible": false,
        "violations": v, "poses": pose_rows(&p, &c.poses)})).collect();
    Ok(json!({
        "schema": OUTPUT_SCHEMA, "problem_sha256": p.identity, "status": status,
        "diagnostic_shortlist": diagnostic,
        "aliases": p.footprints.iter().map(|f| json!({"uuid": f.uuid, "alias": f.alias, "native_ref": f.native_ref})).collect::<Vec<_>>(),
        "occupancy": p.footprints.iter().map(|f| json!({"uuid": f.uuid, "rung": f.occupancy_rung,
            "role": format!("{:?}", f.role)})).collect::<Vec<_>>(),
        "seeds": seed_rows, "shortlist": shortlist,
        "counters": {"evaluations": counters.evaluations, "commits": counters.commits, "rejects": counters.rejects,
                     "stale_refusals": counters.stale_refusals, "full_evaluations": counters.full_evaluations},
        "timing": {"prepare_s": prepare_s, "total_s": started.elapsed().as_secs_f64()},
        "prng": "splitmix64/agent-ee-515-v1", "engine_version": env!("CARGO_PKG_VERSION"),
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn star(n: usize) -> Value {
        json!((0..n).map(|i| {
            let angle = 2.0 * std::f64::consts::PI * i as f64 / n as f64;
            let radius = if i % 2 == 0 { 900_000.0 } else { 1_000_000.0 };
            [(radius * angle.cos()).round() as i64, (radius * angle.sin()).round() as i64]
        }).collect::<Vec<_>>())
    }

    fn native_item(polygons: Value) -> Value {
        json!({"rect_nm": [-1_000_000, -1_000_000, 1_000_000, 1_000_000], "clearance_nm": 0,
            "geometry": {"status": "conservative_native_polygon", "kind": "custom", "pad_uuid": "pad-1",
                         "tolerance_nm": 1000, "polygons": polygons}})
    }

    fn assert_same_boundary(raw: &Value, normalized: &Polygon) {
        let mut original: Polygon = raw.as_array().unwrap().iter()
            .map(|p| crate::exact::point(p).unwrap()).collect();
        if original.first() == original.last() { original.pop(); }
        assert_eq!(geometry::area(&original), geometry::area(normalized));
        // Every original edge is a subdivision of a retained edge; normalization
        // can change orientation and subdivision, but no part of the region.
        for (a, b) in geometry::edges(&original) {
            assert!(geometry::edges(normalized).any(|(c, d)|
                geometry::on_segment(c, d, a) && geometry::on_segment(c, d, b)));
        }
        assert!(normalized.iter().all(|p| original.contains(p)));
    }

    #[test]
    fn prepared_copper_matches_exact_reference_under_moves() {
        let shape = Copper::parse(&native_item(json!([star(32)])), 0).unwrap();
        let neighbour = Copper::prepared(
            Rect { x0: -150_000, y0: -100_000, x1: 150_000, y1: 100_000 }, 0, None).unwrap();
        let mut rng = Rng::new(544);
        for turn in 0..4 {
            let a = shape.at(&Pose { x: 30_000, y: -70_000, q: turn });
            for _ in 0..24 {
                let b = neighbour.at(&Pose { x: rng.range(-1_300_000, 1_300_000),
                    y: rng.range(-1_300_000, 1_300_000), q: turn });
                for clearance in [0, 50_000, 200_000] {
                    let boxed = a.bounds.grown(clearance).overlap(&b.bounds);
                    let rectangle = rect_polygon(&b.bounds);
                    let limit = Rat::from_integer(crate::exact::Int::from(clearance).pow(2));
                    let exact = a.polygons.as_ref().unwrap().iter().any(|p|
                        geometry::positive_overlap(p, &rectangle)
                        || geometry::separation2(p, &rectangle) < limit);
                    assert_eq!(a.overlap_at_clearance(&b, clearance), if exact { boxed } else { 0 });
                }
            }
        }
        // Coincident boundaries, edge contact, exact clearance, and one nm inside.
        let a = Copper::parse(&native_item(json!([[[0, 0], [100, 0], [100, 100], [0, 100]]])), 0).unwrap();
        for (x, clearance, violation) in [(0, 0, true), (100, 0, false),
                                         (100, 1, true), (150, 50, false), (149, 50, true)] {
            let b = Copper::prepared(Rect { x0: x, y0: 0, x1: x + 100, y1: 100 }, 0, None).unwrap();
            assert_eq!(a.overlap_at_clearance(&b, clearance) > 0, violation);
        }
    }

    #[test]
    fn native_complexity_boundaries_and_region_preservation() {
        for count in [1118, MAX_CONTOUR_VERTICES] {
            let raw = star(count);
            let c = Copper::parse(&native_item(json!([raw])), 0).unwrap();
            assert_same_boundary(&raw, &c.polygons.unwrap()[0]);
        }
        let over = native_item(json!([star(MAX_CONTOUR_VERTICES + 1)]));
        assert_eq!(Copper::parse(&over, 0).unwrap_err().to_string(),
            format!("contour 0 vertex count {} exceeds {}", MAX_CONTOUR_VERTICES + 1, MAX_CONTOUR_VERTICES));
        let triangle = json!([[0, 0], [100, 0], [0, 100]]);
        Copper::parse(&native_item(json!(vec![triangle.clone(); MAX_ITEM_CONTOURS])), 0).unwrap();
        assert_eq!(Copper::parse(&native_item(json!(vec![triangle.clone(); MAX_ITEM_CONTOURS + 1])), 0)
            .unwrap_err().to_string(), format!("item contour count {} exceeds {}", MAX_ITEM_CONTOURS + 1, MAX_ITEM_CONTOURS));
        let mut i = input(vec![]);
        let count = MAX_PROBLEM_CONTOUR_VERTICES / MAX_CONTOUR_VERTICES;
        i["footprints"][0]["copper_local_nm"] = json!([native_item(json!(vec![star(MAX_CONTOUR_VERTICES); count]))]);
        prepare(&i).unwrap();
        i["footprints"][0]["copper_local_nm"][0]["geometry"]["polygons"][count - 1] = star(MAX_CONTOUR_VERTICES - 2);
        i["footprints"][0]["copper_local_nm"].as_array_mut().unwrap().push(native_item(json!([triangle])));
        let message = format!("{:#}", prepare(&i).unwrap_err());
        assert!(message.contains(&format!("prepared contour vertex total {} exceeds {}",
            MAX_PROBLEM_CONTOUR_VERTICES + 1, MAX_PROBLEM_CONTOUR_VERTICES)), "{message}");
    }

    #[test]
    fn native_polygon_invalid_classes_keep_specific_refusals() {
        for (raw, message) in [
            (json!([[0, 0], [1, 1]]), format!("polygon needs 3..{MAX_CONTOUR_VERTICES} vertices")),
            (json!([[0, 0], [100, 0], [100, 100], [100, 0], [0, 100]]), "repeated polygon vertex".into()),
            (json!([[0, 0], [100, 0], [50, 0], [100, 100], [0, 100]]), "polygon edge reverses".into()),
            (json!([[0, 0], [100, 100], [0, 100], [100, 0]]), "zero polygon area".into()),
            (json!([[0, 0], [100, 100], [0, 100], [80, 0]]), "polygon self-intersection".into()),
            (json!([[0, 0], [100.5, 0], [0, 100]]), "polygon coordinates must be integer nanometres".into()),
        ] {
            assert_eq!(Copper::parse(&native_item(json!([raw])), 0).unwrap_err().to_string(), message);
        }
        for raw in [json!([[0, 0], [0, 50], [0, 100], [100, 100], [100, 0], [0, 0]]),
                    json!([[0, 0], [100, 0], [100, 100], [50, 100], [0, 100]])] {
            let c = Copper::parse(&native_item(json!([raw])), 0).unwrap();
            assert_same_boundary(&raw, &c.polygons.unwrap()[0]);
        }
    }

    #[test]
    fn retained_native_problem_region_prepare_and_evaluation() {
        let path = match std::env::var("AGENT_EE_515_E2E_PROBLEM") {
            Ok(path) => path,
            Err(_) => {
                assert_ne!(std::env::var("AGENT_EE_REQUIRE_TOOLCHAIN").ok().as_deref(), Some("1"),
                    "AGENT_EE_515_E2E_PROBLEM required in strict mode");
                return;
            }
        };
        use sha2::{Digest, Sha256};
        let bytes = std::fs::read(path).unwrap();
        assert_eq!(format!("{:x}", Sha256::digest(&bytes)),
            "2ccd682b4bf36ea2aa2d2dcc8bca05f1770cd5927df6eb67142ba70bbf626f11");
        let input: Value = serde_json::from_slice(&bytes).unwrap();
        let started = Instant::now();
        let p = prepare(&input).unwrap();
        let prepare_s = started.elapsed().as_secs_f64();
        let u2 = input["footprints"].as_array().unwrap().iter().position(|f| f["native_ref"] == "U2").unwrap();
        let raw = &input["footprints"][u2]["copper_local_nm"][1]["geometry"]["polygons"][0];
        assert_eq!(raw.as_array().unwrap().len(), 1118);
        assert_same_boundary(raw, &p.footprints[u2].copper[1].polygons.as_ref().unwrap()[0]);
        let started = Instant::now();
        evaluate_poses(&p, &json!([{}]), prepare_s).unwrap();
        eprintln!("retained prepare_s={prepare_s:.6} evaluate_s={:.6}", started.elapsed().as_secs_f64());
    }

    fn fp(uuid: &str, r: &str, role: &str, x: i64, y: i64, net: &str) -> Value {
        let occ = if role == "movable" || role == "fixed_body" {
            json!({"status": "derived", "label": "derived_occupancy", "rung": "pads", "local_rect_nm": [-1_000_000, -500_000, 1_000_000, 500_000]})
        } else { json!({"status": "none"}) };
        json!({"uuid": uuid, "native_ref": r, "role": role, "pose": {"x_nm": x, "y_nm": y, "quarter_turns": 0},
               "rotations": [0, 1, 2, 3], "occupancy": occ,
               "copper_local_nm": [[-900_000, -300_000, -300_000, 300_000], [300_000, -300_000, 900_000, 300_000]],
               "pads": [{"uuid": format!("{uuid}-1"), "net": net, "local_nm": [-600_000, 0]},
                        {"uuid": format!("{uuid}-2"), "net": "GND", "local_nm": [600_000, 0]}]})
    }

    fn uuid(n: u32) -> String {
        format!("00000000-0000-0000-0000-0000{n:08x}")
    }

    fn input(extra: Vec<Value>) -> Value {
        let mut fps = vec![
            fp(&uuid(1), "R1", "movable", 5_000_000, 5_000_000, "A"),
            fp(&uuid(2), "R2", "movable", 5_000_000, 5_000_000, "A"),
            fp(&uuid(3), "R3", "movable", 9_000_000, 3_000_000, "B"),
            fp(&uuid(4), "R4", "movable", 2_000_000, 8_000_000, "B"),
            fp(&uuid(5), "FID", "fixed_copper_mark", 1_500_000, 1_500_000, ""),
        ];
        // Unequal copper so a non-canonical pair order would change the grown-overlap area.
        fps[2]["copper_local_nm"] = json!([[-1_200_000, -700_000, -100_000, 700_000], [400_000, -200_000, 700_000, 200_000]]);
        fps[2]["mask_local_nm"] = json!([[-1_250_000, -750_000, -50_000, 750_000]]);
        fps[3]["mask_local_nm"] = json!([[-950_000, -350_000, -250_000, 350_000]]);
        fps[4]["copper_local_nm"] = json!([{"rect_nm": [-500_000, -500_000, 500_000, 500_000], "clearance_nm": 1_016_000}]);
        fps.extend(extra);
        json!({"schema": INPUT_SCHEMA, "problem_sha256": "a".repeat(64),
               "outline": {"kind": "rectangle", "rect_nm": [0, 0, 12_000_000, 10_000_000]},
               "rules": {"copper_clearance_nm": 200_000, "copper_edge_clearance_nm": 300_000, "mask_min_web_nm": 100_000},
               "footprints": fps,
               "groups": [{"name": "g", "anchor": "R1", "refs": ["R1", "R2"]}],
               "search": {"seeds": [{"seed": 0, "start": "scattered"}, {"seed": 1, "start": "clustered"}],
                          "steps_per_seed": 3000, "shortlist": 2, "verify_every": 1}})
    }

    fn with_half_extents(mut raw: Value, rails: Value) -> Value {
        for row in raw["footprints"].as_array_mut().unwrap() {
            for pad in row["pads"].as_array_mut().unwrap() { pad["half_nm"] = json!([300_000, 300_000]); }
        }
        raw["rails"] = rails;
        raw
    }

    fn legal_dispersed(p: &Prepared) -> Vec<Pose> {
        let at = |r: &str, x: i64, y: i64| (p.footprints.iter().position(|f| f.native_ref == r).unwrap(), Pose { x, y, q: 0 });
        let mut poses: Vec<Pose> = p.footprints.iter().map(|f| f.initial).collect();
        for (f, pose) in [at("R1", 3_000_000, 5_000_000), at("R2", 9_500_000, 5_000_000),
                          at("R3", 9_000_000, 2_000_000), at("R4", 2_000_000, 8_000_000)] {
            poses[f] = pose;
        }
        poses
    }

    #[test]
    fn group_links_compile_from_nets_and_rails_never_merge_blocks() {
        let p = prepare(&with_half_extents(input(vec![]), json!({"shared": ["GND"], "scoped": {}}))).unwrap();
        let kinds: Vec<(&str, &str, &str)> = p.links.iter()
            .map(|l| (l.kind.as_str(), p.footprints[l.a].native_ref.as_str(), p.footprints[l.b].native_ref.as_str())).collect();
        assert_eq!(kinds, vec![("R2_intra_group_signal", "R1", "R2"), ("R2_member_to_anchor", "R2", "R1")]);
        // GND is a declared rail: no link is compiled on it even though both members carry it.
        assert!(p.links.iter().all(|l| l.a_pads.iter().all(|&i| p.footprints[l.a].pads[i].net != p.nets.iter().position(|n| n == "GND"))
                                   || l.kind == "R2_member_to_anchor"));
    }

    #[test]
    fn pad_edge_proximity_is_the_signed_rect_gap_per_subject_pad() {
        let mut raw = with_half_extents(input(vec![]), json!({"shared": ["GND"], "scoped": {}}));
        raw["proximity"] = json!([{"ref": "R2", "near": "R1", "ref_pads": [format!("{}-1", uuid(2))],
                                   "near_pads": [format!("{}-1", uuid(1)), format!("{}-2", uuid(1))],
                                   "max_nm": 0, "basis": "pad_edge"}]);
        let p = prepare(&raw).unwrap();
        let poses = legal_dispersed(&p);
        // R2 pad 1 (net A) at x = 9.5e6 - 0.6e6 = 8.9e6; R1 pad 1 (net A) at 2.4e6: net narrowing picks pad 1, not the nearer GND pad 2.
        let a = pad_rect(&p, p.proximity[0].a, p.proximity[0].a_pads[0], &poses[p.proximity[0].a]);
        let b = pad_rect(&p, p.proximity[0].b, p.proximity[0].b_pads[0], &poses[p.proximity[0].b]);
        assert_eq!(proximity_terms(&p, 0, &poses), rect_gap(&a, &b) as i128);
        assert_eq!(rect_gap(&a, &b), 8_900_000 - 300_000 - (2_400_000 + 300_000));
        assert_eq!(rect_gap(&Rect { x0: 0, y0: 0, x1: 100, y1: 100 }, &Rect { x0: 75, y0: 0, x1: 175, y1: 100 }), -25);
    }

    #[test]
    fn refinement_moves_a_dispersed_member_next_to_its_partner_without_losing_legality() {
        let p = prepare(&with_half_extents(input(vec![]), json!({"shared": ["GND"], "scoped": {}}))).unwrap();
        let mut poses = legal_dispersed(&p);
        let before = full_evaluate(&p, &poses);
        assert_eq!((before.hard, before.hard_count), (0, 0), "the dispersed start must be legal");
        let gap_before = link_gap(&p, 0, &poses);
        assert!(gap_before > 3_000_000, "member R2 starts far from anchor R1: {gap_before}");
        let rows = refine_members(&p, &mut poses, 3);
        assert!(!rows.is_empty(), "refinement must act on the dispersed member");
        let after = full_evaluate(&p, &poses);
        assert_eq!((after.hard, after.hard_count), (0, 0), "refinement never accepts an illegal seat");
        assert!(after.cost(&p.weights) < before.cost(&p.weights));
        assert!(link_gap(&p, 0, &poses) < gap_before);
        assert!(link_gap(&p, 0, &poses) >= p.clearance_nm, "members keep the native clearance");
        let anchor = p.groups[0].1;
        assert_eq!(poses[anchor], legal_dispersed(&p)[anchor], "the anchor never moves");
        let fid = p.footprints.iter().position(|f| f.native_ref == "FID").unwrap();
        assert_eq!(poses[fid], p.footprints[fid].initial, "fixed marks never move");
        assert_eq!(rows[0]["member"], "R2");
    }

    #[test]
    fn aliases_use_full_uuid_and_refuse_collisions() {
        let shared: Vec<String> = (0..3).map(|n| uuid(0x5d8a3e00 + n)).collect();
        assert!(shared.iter().all(|u| u[..8] == shared[0][..8]), "fixture shares an 8-char prefix");
        let a = aliases(&shared, &[None, None, None]).unwrap();
        assert_eq!(a.iter().collect::<BTreeSet<_>>().len(), 3);
        let dup = vec![shared[0].clone(), shared[0].to_uppercase()];
        assert!(aliases(&dup, &[None, None]).unwrap_err().to_string().contains("duplicate native footprint UUID"));
        let wrong = aliases(&shared, &[Some("fp001".into()), None, None]).unwrap_err().to_string();
        assert!(wrong.contains("differs from the derived"), "{wrong}");
        assert!(aliases(&["0000".into()], &[None]).is_err());
    }

    fn repeated_marks() -> Value {
        let mut raw = input(vec![fp(&uuid(6), "FID", "fixed_copper_mark", 3_000_000, 2_000_000, "")]);
        for row in raw["footprints"].as_array_mut().unwrap().iter_mut().filter(|r| r["native_ref"] == "FID") {
            for pad in row["pads"].as_array_mut().unwrap() { pad["net"] = json!(""); }
            row["mask_local_nm"] = json!([[-500_000, -500_000, 500_000, 500_000]]);
        }
        raw
    }

    #[test]
    fn repeated_fixed_mark_labels_keep_uuid_identity_and_protected_poses() {
        // REC-30: the real esp_prog article has two fixed copper marks named Ref*.
        let mut raw = repeated_marks();
        raw["evaluate"] = json!([{}]);
        let output = run(&raw).unwrap();
        let poses = output["evaluations"][0]["poses"].as_array().unwrap();
        let marks: Vec<_> = poses.iter().filter(|r| r["native_ref"] == "FID").collect();
        assert_eq!(marks.len(), 2);
        assert_ne!(marks[0]["uuid"], marks[1]["uuid"]);
        assert_ne!(marks[0]["alias"], marks[1]["alias"]);
        assert!(marks.iter().all(|r| r["moved"] == false));
        for id in [uuid(5), uuid(6)] {
            let old = raw["footprints"].as_array().unwrap().iter().find(|r| r["uuid"] == id).unwrap();
            let pose = &old["pose"];
            let at = [pose["x_nm"].as_i64().unwrap(), pose["y_nm"].as_i64().unwrap(), 0];
            for axis in 0..3 {
                let mut changed = at;
                changed[axis] += 1;
                raw["evaluate"] = json!([{id.clone(): changed}]);
                assert!(run(&raw).unwrap_err().to_string().contains("fixed and cannot move"));
            }
        }
    }

    #[test]
    fn repeated_marks_do_not_admit_component_or_uuid_collisions() {
        for role in ["movable", "fixed_body"] {
            let mut raw = repeated_marks();
            raw["footprints"][1]["native_ref"] = json!("R1");
            raw["footprints"][1]["role"] = json!(role);
            assert!(prepare(&raw).unwrap_err().to_string().contains("duplicate physical reference R1"));
            raw = repeated_marks();
            raw["footprints"][0]["native_ref"] = json!("FID");
            raw["footprints"][0]["role"] = json!(role);
            assert!(prepare(&raw).unwrap_err().to_string().contains("duplicate physical reference FID"));
        }
        let mut raw = repeated_marks();
        raw["footprints"][5]["uuid"] = raw["footprints"][4]["uuid"].clone();
        assert!(prepare(&raw).unwrap_err().to_string().contains("duplicate native footprint UUID"));
    }

    #[test]
    fn ambiguous_mark_selectors_refuse_for_groups_proximity_and_locality() {
        for selector in ["group", "proximity ref", "proximity near", "locality from", "locality to"] {
            let mut raw = repeated_marks();
            match selector {
                "group" => raw["groups"][0]["refs"] = json!(["R1", "FID"]),
                "proximity ref" | "proximity near" => {
                    let (a, b) = if selector == "proximity ref" { ("FID", "R1") } else { ("R1", "FID") };
                    raw["proximity"] = json!([{"ref": a, "near": b, "ref_pads": [], "near_pads": [], "max_nm": 1}]);
                }
                _ => {
                    let (a, b) = if selector == "locality from" { ("FID", "R1") } else { ("R1", "FID") };
                    raw["locality"] = json!({"document": {"schema": "agent-ee.electrical-locality/1",
                        "obligations": [{"id": "ambiguous", "kind": "single-leg", "metric": "pad-centre-to-centre",
                            "leg": {"from": {"reference": a, "pad": "1", "net": "A"},
                                    "to": {"reference": b, "pad": "1", "net": "A"}, "max_distance_nm": 1}}]},
                        "footprints": [{"ref": "R1", "pads": [{"number": "1", "net": "A",
                            "position_nm": [4_400_000, 5_000_000], "copper_centroid_offset_nm": [[0, 1], [0, 1]]}]}]});
                }
            }
            let error = prepare(&raw).unwrap_err().to_string();
            assert!(error.contains("ambiguous physical reference FID"), "{selector}: {error}");
        }
    }

    #[test]
    fn fully_anchored_board_has_one_unchanged_candidate_and_honors_deadline() {
        let mut raw = input(vec![]);
        for fp in raw["footprints"].as_array_mut().unwrap() {
            fp["role"] = json!("fixed_body");
        }
        let result = run(&raw).unwrap();
        assert_eq!(result["status"], "completed");
        let shortlist = result["shortlist"].as_array().unwrap();
        assert_eq!(shortlist.len(), 1);
        assert_eq!(shortlist[0]["full_evaluation_equal"], true);
        assert!(shortlist[0]["poses"].as_array().unwrap().iter().all(|p| p["moved"] == false));
        assert!(result["seeds"].as_array().unwrap().iter().all(|s| s["steps"] == 0));
        raw["search"]["deadline_ms"] = json!(0);
        let expired = run(&raw).unwrap();
        assert_eq!(expired["status"], "deadline");
        assert_eq!(expired["shortlist"], json!([]));
    }

    #[test]
    fn refuses_malformed_or_unlabelled_occupancy() {
        let mut bad = input(vec![]);
        bad["footprints"][0]["occupancy"] = json!({"status": "courtyard_unproven"});
        assert!(prepare(&bad).unwrap_err().to_string().contains("refused"));
        let mut unlabelled = input(vec![]);
        unlabelled["footprints"][0]["occupancy"]["label"] = json!("courtyard");
        assert!(prepare(&unlabelled).is_err());
        let mut silk = input(vec![]);
        silk["footprints"][4]["role"] = json!("silk_only");
        assert!(prepare(&silk).unwrap_err().to_string().contains("silk-only graphic carries copper"));
    }

    #[test]
    fn incremental_matches_full_and_rollback_is_exact() {
        let mut raw = input(vec![]);
        for index in [0, 2, 4] {
            raw["footprints"][index]["escape_local_nm"] = json!([{
                "authority": "agent-heuristic/1", "rect_nm": [1_000_000, -500_000, 4_000_000, 500_000]}]);
        }
        let p = prepare(&raw).unwrap();
        let mut rng = Rng::new(7);
        let mut state = SearchState::new(&p, start_poses(&p, 7, false, &mut rng)).unwrap();
        for i in 0..2000 {
            let f = p.movable[rng.below(p.movable.len() as u64) as usize];
            let mut moves = vec![(f, Pose { x: rng.range(-1_000_000, 13_000_000), y: rng.range(-1_000_000, 11_000_000), q: rng.range(0, 3) })];
            if i % 5 == 0 {
                moves.push((p.movable[(f + 1) % p.movable.len()], Pose { x: rng.range(0, 12_000_000), y: rng.range(0, 10_000_000), q: 1 }));
                moves.dedup_by_key(|m| m.0);
            }
            let before = state.digest();
            let trial = state.evaluate(&p, &moves).unwrap();
            assert_eq!(state.digest(), before, "evaluate must not mutate committed state");
            if i % 3 == 0 {
                state.reject(trial);
                assert_eq!(state.digest(), before, "reject must leave committed state unchanged");
            } else {
                state.commit(&p, trial).unwrap();
            }
            assert_eq!(state.totals, full_evaluate(&p, &state.poses), "step {i}");
        }
    }

    #[test]
    fn escape_space_changes_preference_not_feasibility() {
        let mut raw = input(vec![]);
        raw["groups"] = json!([]);
        raw["footprints"] = json!([
            fp(&uuid(1), "J1", "fixed_body", 5_000_000, 5_000_000, "P"),
            fp(&uuid(2), "R9", "movable", 8_000_000, 5_000_000, "N")]);
        raw["footprints"][0]["escape_local_nm"] = json!([{
            "authority": "agent-heuristic/1", "rect_nm": [1_000_000, -500_000, 4_000_000, 500_000]}]);
        let p = prepare(&raw).unwrap();
        let poses: Vec<_> = p.footprints.iter().map(|f| f.initial).collect();
        let blocked = full_evaluate(&p, &poses);
        assert_eq!(blocked.hard_count, 0);
        assert!(blocked.escape > 0);
        let mut state = SearchState::new(&p, poses).unwrap();
        let trial = state.evaluate(&p, &[(1, Pose { x: 10_500_000, y: 5_000_000, q: 0 })]).unwrap();
        state.commit(&p, trial).unwrap();
        assert_eq!(state.totals, full_evaluate(&p, &state.poses));
        assert_eq!(state.totals.escape, 0);
        assert_eq!(state.totals.hard_count, 0);
        raw["footprints"][0]["escape_local_nm"][0]["authority"] = json!("owner");
        assert!(prepare(&raw).is_err(), "a hint cannot masquerade as owner authority");
    }

    #[test]
    fn hole_rules_are_distinct_and_indexed_for_incremental_moves() {
        let mut i = input(vec![]);
        i["rules"]["hole_to_hole_clearance_nm"] = json!(400_000);
        for f in [0, 2, 4] {
            i["footprints"][f]["holes_local_nm"] = json!([{
                "rect_nm": [-500_000, -250_000, 500_000, 250_000], "clearance_nm": 150_000,
                "geometry": {"status": "conservative_native_polygon", "kind": "hole", "tolerance_nm": 1000,
                    "pad_uuid": i["footprints"][f]["pads"][0]["uuid"],
                    "polygons": [[[-500_000, -250_000], [500_000, -250_000], [500_000, 250_000], [-500_000, 250_000]]]}}]);
        }
        let p = prepare(&i).unwrap();
        let mut state = SearchState::new(&p, p.footprints.iter().map(|f| f.initial).collect()).unwrap();
        let mut rng = Rng::new(515);
        for n in 0..300 {
            let f = if n % 2 == 0 { 0 } else { 2 };
            let pose = Pose { x: rng.range(0, 12_000_000), y: rng.range(0, 10_000_000), q: n % 4 };
            let before = state.digest();
            let trial = state.evaluate(&p, &[(f, pose)]).unwrap();
            if n % 3 == 0 { state.reject(trial); assert_eq!(state.digest(), before); }
            else { state.commit(&p, trial).unwrap(); }
            assert_eq!(state.totals, full_evaluate(&p, &state.poses), "hole move {n}");
        }
        let hole = &p.footprints[0].holes[0];
        let copper = Copper::prepared(Rect { x0: 800_000, y0: -250_000, x1: 1_800_000, y1: 250_000 },
                                      1_016_000, None).unwrap();
        assert_eq!(hole.overlap_at_clearance(&copper, hole.clearance), 0,
            "a copper override is not the drill rule");
        assert!(hole.overlap_at_clearance(&copper, p.hole_to_hole_nm) > 0);
    }

    #[test]
    fn edge_identity_without_numeric_setback_is_hard_and_incremental() {
        let mut i = input(vec![]);
        i["footprints"] = json!([fp(&uuid(1), "J1", "movable", 6_000_000, 8_000_000, "A")]);
        i["groups"] = json!([]);
        i["footprints"][0]["edge"] = json!({"edge": "east",
            "seat_local_nm": [-1_000_000, -500_000, 1_000_000, 500_000]});
        let p = prepare(&i).unwrap();
        let mut state = SearchState::new(&p, vec![p.footprints[0].initial]).unwrap();
        assert!(violations(&p, &state.poses).iter().any(|v| v["kind"] == "edge_identity"));
        let trial = state.evaluate(&p, &[(0, Pose { x: 10_000_000, y: 5_000_000, q: 0 })]).unwrap();
        state.commit(&p, trial).unwrap();
        assert_eq!(state.totals, full_evaluate(&p, &state.poses));
        assert_eq!(state.totals.hard_count, 0);
        assert!(violations(&p, &state.poses).is_empty());
        for seat in [json!([0, 0, 0, 0]), json!([0, 0, 1.5, 2])] {
            i["footprints"][0]["edge"]["seat_local_nm"] = seat;
            assert!(prepare(&i).is_err());
        }
    }

    #[test]
    fn corner_edge_uses_overhang_then_long_side_with_stable_ties() {
        let outline = Rect { x0: 0, y0: 0, x1: 30_000_000, y1: 20_000_000 };
        for (rect, expected) in [
            (Rect { x0: 200_000, y0: 100_000, x1: 2_000_000, y1: 19_900_000 }, Edge::West),
            (Rect { x0: 100_000, y0: 200_000, x1: 29_900_000, y1: 2_000_000 }, Edge::North),
            (Rect { x0: -500_000, y0: 100_000, x1: 10_000_000, y1: 2_000_000 }, Edge::West),
            (Rect { x0: 100_000, y0: 100_000, x1: 2_000_000, y1: 2_000_000 }, Edge::West),
        ] {
            assert_eq!(Edge::nearest(&rect, &outline, 300_000), expected);
        }
    }

    #[test]
    fn declared_overhang_interval_is_validated_and_minimum_is_enforced() {
        let mut i = input(vec![]);
        i["footprints"][0]["edge"] = json!({"edge": "west", "max_setback_nm": 750_000,
            "overhang_nm": {"min": 100_000, "max": 2_000_000}});
        let p = prepare(&i).unwrap();
        let mut poses: Vec<Pose> = p.footprints.iter().map(|f| f.initial).collect();
        poses[0].x = 1_000_000;
        assert!(violations(&p, &poses).iter().any(|v| v["kind"] == "edge_overhang_min"));
        for interval in [json!({"min": -1, "max": 2}), json!({"min": 3, "max": 2}),
                         json!({"min": 0, "max": 1.5}), Value::Null] {
            i["footprints"][0]["edge"]["overhang_nm"] = interval;
            assert!(prepare(&i).is_err());
        }
        i["footprints"][0]["edge"] = json!({"edge": "diagonal"});
        assert!(prepare(&i).is_err());
        i["footprints"][0]["holes_local_nm"] = json!([{"rect_nm": [0, 0, 1, 1], "clearance_nm": -1}]);
        i["footprints"][0]["edge"] = Value::Null;
        assert!(prepare(&i).is_err());
    }

    #[test]
    fn stale_trial_is_refused_without_side_effects() {
        let p = prepare(&input(vec![])).unwrap();
        let mut state = SearchState::new(&p, p.footprints.iter().map(|f| f.initial).collect()).unwrap();
        let a = state.evaluate(&p, &[(0, Pose { x: 3_000_000, y: 3_000_000, q: 0 })]).unwrap();
        let b = state.evaluate(&p, &[(1, Pose { x: 8_000_000, y: 6_000_000, q: 0 })]).unwrap();
        state.commit(&p, a).unwrap();
        let before = state.digest();
        assert!(state.commit(&p, b).unwrap_err().to_string().contains("stale trial"));
        assert_eq!(state.digest(), before);
        assert_eq!(state.counters.stale_refusals, 1);
        // A fixed copper mark cannot be moved by any trial.
        assert!(state.evaluate(&p, &[(4, Pose { x: 0, y: 0, q: 0 })]).is_err());
    }

    #[test]
    fn copper_mark_is_copper_obstacle_not_body() {
        let mut i = input(vec![]);
        // R3's derived body reaches 3.5 mm above its origin; its copper stays near the origin.
        i["footprints"][2]["occupancy"]["local_rect_nm"] = json!([-1_300_000, -3_500_000, 1_300_000, 800_000]);
        let p = prepare(&i).unwrap();
        let mut poses: Vec<Pose> = p.footprints.iter().map(|f| f.initial).collect();
        let base = full_evaluate(&p, &poses);
        // R3's body over the fiducial origin but its pads clear of the fiducial copper: no new body overlap.
        poses[2] = Pose { x: 1_500_000, y: 4_500_000, q: 0 };
        let t = full_evaluate(&p, &poses);
        assert!(violations(&p, &poses).iter().all(|v| v["with"] != "FID"), "{:?}", violations(&p, &poses));
        assert!(t.hard_count <= base.hard_count + 1);
        poses[2] = Pose { x: 1_500_000, y: 1_500_000, q: 0 };
        let hit = full_evaluate(&p, &poses);
        assert!(hit.hard > 0, "copper-on-copper with the mark must be a hard violation");
    }

    #[test]
    fn functional_groups_keep_fixed_anchors_and_members_stationary() {
        // A real generated acquisition board failed because each block includes
        // its mechanically fixed connector. Search must keep those references
        // in the block without translating them during a group move.
        for anchor in ["R1", "R2"] {
            let mut raw = input(vec![]);
            raw["footprints"][0]["role"] = json!("fixed_body");
            raw["groups"][0]["anchor"] = json!(anchor);
            let p = prepare(&raw).unwrap();
            let initial = p.footprints[0].initial;
            let mut rng = Rng::new(9);
            let poses = start_poses(&p, 9, true, &mut rng);
            assert_eq!(poses[0], initial);
            if anchor == "R1" {
                assert!((poses[1].x - initial.x).abs() <= 2_050_000);
                assert!((poses[1].y - initial.y).abs() <= 2_050_000);
            }
            let out = run(&raw).unwrap();
            assert_eq!(out["status"], "completed");
            assert!(!out["shortlist"].as_array().unwrap().is_empty());
            for candidate in out["shortlist"].as_array().unwrap() {
                assert_eq!(candidate["full_evaluation_equal"], true);
                let fixed = candidate["poses"].as_array().unwrap().iter()
                    .find(|v| v["native_ref"] == "R1").unwrap();
                assert_eq!(fixed["x_nm"], initial.x);
                assert_eq!(fixed["y_nm"], initial.y);
                assert_eq!(fixed["moved"], false);
            }
        }
    }

    #[test]
    fn locality_is_exact_euclidean_and_incremental_for_both_endpoints() {
        let mut p = prepare(&input(vec![])).unwrap();
        p.locality.push(Locality { a: 0, b: 1,
            a_offset: [Rat::new(1.into(), 2.into()), Rat::zero()],
            b_offset: [Rat::zero(), Rat::zero()],
            limit_squared: Rat::from_integer(9_000_000_000_000i64.into()), hard: true,
            obligation: "three-mm".into(), role: "leg".into() });
        let mut state = SearchState::new(&p, p.footprints.iter().map(|f| f.initial).collect()).unwrap();
        for (f, x, y, q, violated) in [
            (1, 8_000_000, 5_000_000, 0, false),
            (1, 8_000_001, 5_000_000, 0, true),
            (1, 7_400_000, 7_400_000, 0, true),
            (0, 7_400_000, 4_400_000, 1, true),
            (0, 7_400_000, 4_400_001, 1, false),
        ] {
            let trial = state.evaluate(&p, &[(f, Pose {x, y, q})]).unwrap();
            state.commit(&p, trial).unwrap();
            assert_eq!(state.totals, full_evaluate(&p, &state.poses));
            assert_eq!(locality_terms(&p, 0, &state.poses).hard_count > 0, violated);
            assert_eq!(violations(&p, &state.poses).iter().any(|v| v["kind"] == "electrical_locality"), violated);
        }
    }

    #[test]
    fn search_returns_only_feasible_candidates_and_honours_cancel() {
        let out = run(&input(vec![])).unwrap();
        assert_eq!(out["status"], "completed");
        let shortlist = out["shortlist"].as_array().unwrap();
        assert!(!shortlist.is_empty());
        for c in shortlist {
            assert_eq!(c["terms"]["hard"], "0");
            assert_eq!(c["full_evaluation_equal"], true);
            let fid = c["poses"].as_array().unwrap().iter().find(|r| r["native_ref"] == "FID").unwrap();
            assert_eq!((fid["x_nm"].as_i64(), fid["moved"].as_bool()), (Some(1_500_000), Some(false)));
        }
        // Deterministic replay of the same seed portfolio.
        assert_eq!(run(&input(vec![])).unwrap()["shortlist"], out["shortlist"]);
        let dir = std::env::temp_dir().join(format!("ee515-cancel-{}", std::process::id()));
        std::fs::write(&dir, b"").unwrap();
        let mut cancelled = input(vec![]);
        cancelled["search"]["cancel_file"] = json!(dir.to_str().unwrap());
        let c = run(&cancelled).unwrap();
        std::fs::remove_file(&dir).unwrap();
        assert_eq!(c["status"], "cancelled");
        assert!(c["shortlist"].as_array().unwrap().is_empty());
        let mut resumed = input(vec![]);
        resumed["search"]["completed_seeds"] = json!([0]);
        let r = run(&resumed).unwrap();
        assert_eq!(r["seeds"][0]["status"], "skipped_completed");
        assert_eq!(r["seeds"][1], out["seeds"][1].clone().as_object().map(|o| {
            let mut o = o.clone(); o["elapsed_s"] = r["seeds"][1]["elapsed_s"].clone(); Value::Object(o)
        }).unwrap());
        let mut late = input(vec![]);
        late["search"]["deadline_ms"] = json!(0);
        assert_eq!(run(&late).unwrap()["status"], "deadline");
    }

    #[test]
    fn infeasible_problem_yields_only_labelled_diagnostics() {
        let mut i = input(vec![]);
        // Four 2 x 1 mm bodies cannot fit a 2.5 x 1.5 mm board: no candidate may be called feasible.
        i["outline"]["rect_nm"] = json!([0, 0, 2_500_000, 1_500_000]);
        i["footprints"][4]["pose"] = json!({"x_nm": 1_000_000, "y_nm": 1_000_000, "quarter_turns": 0});
        let out = run(&i).unwrap();
        assert!(out["shortlist"].as_array().unwrap().is_empty());
        let diagnostic = out["diagnostic_shortlist"].as_array().unwrap();
        assert!(!diagnostic.is_empty());
        for d in diagnostic {
            assert_eq!(d["feasible"], false);
            assert!(!d["violations"].as_array().unwrap().is_empty());
            assert_ne!(d["terms"]["hard_count"], 0);
        }
    }

    #[test]
    fn evaluate_mode_reports_terms_and_refuses_moving_fixed_parts() {
        let mut i = input(vec![]);
        i["evaluate"] = json!([{uuid(1): [3_000_000, 3_000_000, 1]}]);
        let out = run(&i).unwrap();
        let row = &out["evaluations"][0]["poses"][0];
        assert_eq!((row["x_nm"].as_i64(), row["quarter_turns"].as_i64()), (Some(3_000_000), Some(1)));
        // Pad at local (-0.6 mm, 0) turned a quarter: (0, +0.6 mm) relative to the origin.
        assert_eq!((row["pads"][0]["x_nm"].as_i64(), row["pads"][0]["y_nm"].as_i64()), (Some(3_000_000), Some(3_600_000)));
        i["evaluate"] = json!([{uuid(5): [0, 0, 0]}]);
        assert!(run(&i).unwrap_err().to_string().contains("fixed"));
    }

    #[test]
    fn rotation_matches_kicad_quarter_turn_convention() {
        assert_eq!(rotate(1, 0, 1), (0, -1));
        assert_eq!(rotate(1, 2, 2), (-1, -2));
        assert_eq!(rotate(1, 0, 3), (0, 1));
        let r = place_rect(&Rect { x0: -2, y0: -1, x1: 4, y1: 1 }, &Pose { x: 10, y: 10, q: 1 });
        assert_eq!(r, Rect { x0: 9, y0: 6, x1: 11, y1: 12 });
    }

    #[test]
    fn native_copper_distance_override_rotation_and_legacy_contract() {
        let legacy = json!({"rect_nm": [0, 0, 100, 100], "clearance_nm": 10});
        let mut native = legacy.clone();
        native["geometry"] = json!({"status": "conservative_native_polygon", "kind": "custom",
            "tolerance_nm": 1000, "polygons": [[[0, 0], [100, 0], [0, 100]]]});
        let a = Copper::parse(&native, 5).unwrap();
        let peer = Copper::parse(&json!({"rect_nm": [80, 80, 90, 90], "clearance_nm": 10}), 5).unwrap();
        assert_eq!(a.overlap(&peer), 0, "empty triangle corner is not copper");
        assert!(Copper::parse(&legacy, 5).unwrap().overlap(&peer) > 0,
            "legacy input keeps the conservative box contract");
        let peer = Copper::parse(&json!({"rect_nm": [80, 80, 90, 90], "clearance_nm": 50}), 5).unwrap();
        assert!(a.overlap(&peer) > 0, "larger peer override wins");
        for q in 0..4 {
            let pose = Pose { x: 1000, y: 2000, q };
            assert!(a.at(&pose).overlap(&peer.at(&pose)) > 0);
        }
        let touching = Copper::parse(&json!([110, 0, 210, 100]), 10).unwrap();
        assert_eq!(Copper::parse(&native, 10).unwrap().overlap(&touching), 0);
        assert!(Copper::parse(&native, 11).unwrap().overlap(&touching) > 0);
    }

    #[test]
    fn malformed_or_unbounded_native_copper_is_refused() {
        let mut v = json!({"rect_nm": [0, 0, 100, 100], "clearance_nm": 10,
            "geometry": {"status": "conservative_native_polygon", "kind": "circle",
                "tolerance_nm": 1000, "polygons": [[[0, 0], [101, 0], [0, 100]]]}});
        assert!(Copper::parse(&v, 5).unwrap_err().to_string().contains("exceeds indexed bounds"));
        v["geometry"]["polygons"] = json!([]);
        assert!(Copper::parse(&v, 5).is_err());
        v["geometry"]["status"] = json!("unqualified");
        assert!(Copper::parse(&v, 5).is_err());
    }
}
