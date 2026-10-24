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
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::time::Instant;

pub const INPUT_SCHEMA: &str = "agent-ee.placement-job-input/1";
pub const OUTPUT_SCHEMA: &str = "agent-ee.placement-job-output/1";
const CELL_NM: i64 = 2_000_000;
const OCCUPANCY_RUNGS: [&str; 4] = ["courtyard", "fab+pads", "silk+pads", "pads"];

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
    pub copper: Vec<Rect>,
    pub pads: Vec<Pad>,
    pub group: Option<usize>,
    pub edge: Option<EdgeRule>,
}

#[derive(Clone, Debug)]
pub struct EdgeRule {
    pub edge: Edge,
    pub max_setback_nm: Option<i64>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Edge {
    West,
    East,
    North,
    South,
}

impl Edge {
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
}

#[derive(Clone, Debug)]
pub struct Weights {
    pub wire: i64,
    pub group: i64,
    pub proximity: i64,
    pub edge: i64,
    /// Penalty per square micrometre of hard overlap during exploration.
    pub hard: i64,
}

/// Immutable, validated problem. Built once per job input.
#[derive(Debug)]
pub struct Prepared {
    pub identity: String,
    pub footprints: Vec<Footprint>,
    pub outline: Rect,
    pub copper_edge_nm: i64,
    pub clearance_nm: i64,
    pub nets: Vec<String>,
    pub net_pads: Vec<Vec<(usize, usize)>>,
    pub groups: Vec<(String, usize, Vec<usize>)>,
    pub proximity: Vec<Proximity>,
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
    ensure!(clearance_nm >= 0 && copper_edge_nm >= 0, "negative rule");
    let rows = input["footprints"].as_array().context("footprints must be an array")?;
    let uuids: Vec<String> = rows.iter().map(|f| text(f, "uuid").map(str::to_string)).collect::<Result<_>>()?;
    let supplied: Vec<Option<String>> =
        rows.iter().map(|f| f.get("alias").and_then(Value::as_str).map(str::to_string)).collect();
    let alias = aliases(&uuids, &supplied)?;
    let mut nets: Vec<String> = Vec::new();
    let mut net_index: HashMap<String, usize> = HashMap::new();
    let groups_in = input["groups"].as_array().cloned().unwrap_or_default();
    let mut footprints = Vec::new();
    let mut ref_index: BTreeMap<String, usize> = BTreeMap::new();
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
            .iter().map(|r| Rect::parse(r, "copper")).collect::<Result<Vec<_>>>()?;
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
            pads.push(Pad {
                uuid: text(pad, "uuid")?.to_string(),
                net,
                at: (at[0].as_i64().context("pad x")?, at[1].as_i64().context("pad y")?),
            });
        }
        let rotations: Vec<i64> = match row.get("rotations") {
            Some(v) => v.as_array().context("rotations")?.iter()
                .map(|q| q.as_i64().context("rotation").map(|q| q.rem_euclid(4))).collect::<Result<_>>()?,
            None => vec![initial.q],
        };
        ensure!(!rotations.is_empty(), "{}: no eligible rotation", uuids[i]);
        let edge = match row.get("edge").filter(|e| !e.is_null()) {
            Some(e) => Some(EdgeRule {
                edge: Edge::parse(text(e, "edge")?)?,
                max_setback_nm: e.get("max_setback_nm").and_then(Value::as_i64),
            }),
            None => None,
        };
        let native_ref = text(row, "native_ref")?.to_string();
        if role == Role::Movable {
            ensure!(ref_index.insert(native_ref.clone(), i).is_none(), "duplicate movable reference {native_ref}");
        }
        footprints.push(Footprint {
            uuid: uuids[i].clone(), alias: alias[i].clone(), native_ref, role, initial, rotations,
            occupancy, occupancy_rung: rung, copper, pads, group: None, edge,
        });
    }
    let mut groups = Vec::new();
    for (g, group) in groups_in.iter().enumerate() {
        let name = text(group, "name")?.to_string();
        let member = |r: &Value| -> Result<usize> {
            let r = r.as_str().context("group member")?;
            ref_index.get(r).copied().with_context(|| format!("group {name}: unknown movable member {r}"))
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
        let a = *ref_index.get(text(&row, "ref")?).context("proximity ref")?;
        let b = *ref_index.get(text(&row, "near")?).context("proximity near")?;
        let a_pads = row["ref_pads"].as_array().context("ref_pads")?.iter().map(|u| pad_of(a, u)).collect::<Result<_>>()?;
        let b_pads = row["near_pads"].as_array().context("near_pads")?.iter().map(|u| pad_of(b, u)).collect::<Result<_>>()?;
        proximity.push(Proximity { a, a_pads, b, b_pads, max_nm: int(&row, "max_nm")? });
    }
    let w = &input["weights"];
    let weights = Weights {
        wire: w.get("wire").and_then(Value::as_i64).unwrap_or(1),
        group: w.get("group").and_then(Value::as_i64).unwrap_or(1),
        proximity: w.get("proximity").and_then(Value::as_i64).unwrap_or(4),
        edge: w.get("edge").and_then(Value::as_i64).unwrap_or(4),
        hard: w.get("hard").and_then(Value::as_i64).unwrap_or(50),
    };
    let movable = (0..footprints.len()).filter(|&i| footprints[i].role == Role::Movable).collect();
    Ok(Prepared { identity, footprints, outline, copper_edge_nm, clearance_nm, nets, net_pads, groups, proximity, weights, movable })
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
    pub edge: i128,
}

impl Terms {
    fn add(&mut self, o: &Terms, sign: i128) {
        self.hard += sign * o.hard;
        self.hard_count += sign as i64 * o.hard_count;
        self.wire += sign * o.wire;
        self.group += sign * o.group;
        self.proximity += sign * o.proximity;
        self.edge += sign * o.edge;
    }
    pub fn cost(&self, w: &Weights) -> i128 {
        // hard overlap in nm^2 is scaled to um^2 so the penalty stays commensurate with nm lengths.
        w.hard as i128 * (self.hard / 1_000_000 + self.hard_count as i128 * 1_000)
            + w.wire as i128 * self.wire
            + w.group as i128 * self.group
            + w.proximity as i128 * self.proximity
            + w.edge as i128 * self.edge
    }
    fn value(&self) -> Value {
        json!({"hard": self.hard.to_string(), "hard_count": self.hard_count, "wire_nm": self.wire.to_string(),
               "group_nm": self.group.to_string(), "proximity_excess_nm": self.proximity.to_string(),
               "edge_setback_nm": self.edge.to_string()})
    }
}

struct Placed {
    body: Option<Rect>,
    copper: Vec<Rect>,
    reach: Option<Rect>,
}

fn placed(p: &Prepared, f: usize, pose: &Pose) -> Placed {
    let fp = &p.footprints[f];
    let body = fp.occupancy.map(|r| place_rect(&r, pose));
    let copper: Vec<Rect> = fp.copper.iter().map(|r| place_rect(r, pose)).collect();
    let mut reach = body;
    for c in &copper {
        let g = c.grown(p.clearance_nm);
        reach = Some(reach.map_or(g, |r| r.union(&g)));
    }
    Placed { body, copper, reach }
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
    if let (Some(x), Some(y)) = (&a.body, &b.body) {
        let o = x.overlap(y);
        if o > 0 {
            t.hard += o;
            t.hard_count += 1;
        }
    }
    // Conservative copper boxes; a clearance-grown overlap is refused.
    for ca in &a.copper {
        let g = ca.grown(p.clearance_nm);
        for cb in &b.copper {
            let o = g.overlap(cb);
            if o > 0 {
                t.hard += o;
                t.hard_count += 1;
            }
        }
    }
    t
}

/// Unary term for one movable footprint: containment and edge rules.
fn unary_terms(p: &Prepared, f: usize, at: &Placed) -> Terms {
    let mut t = Terms::default();
    if p.footprints[f].role != Role::Movable {
        return t;
    }
    if let Some(body) = &at.body {
        let o = body.outside(&p.outline);
        if o > 0 {
            t.hard += o;
            t.hard_count += 1;
        }
        if let Some(rule) = &p.footprints[f].edge {
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
        let o = c.outside(&inner);
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

fn proximity_terms(p: &Prepared, k: usize, poses: &[Pose]) -> i128 {
    let r = &p.proximity[k];
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
                    delta.add(&pair_terms(p, &old[&f], &placed(p, g, &self.poses[g])), -1);
                }
            }
            for &g in new_n.iter().filter(|g| !movers.contains(g)) {
                if interacts(p, f, g) {
                    delta.add(&pair_terms(p, &new[&f], &placed(p, g, &self.poses[g])), 1);
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
        Ok(Trial { generation: self.generation, moves: moves.to_vec(), delta, after_nets, after_groups, after_prox })
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
        .map(|_| (rng.range(o.x0, o.x1), rng.range(o.y0, o.y1))).collect();
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
pub fn anneal(p: &Prepared, seed: u64, clustered: bool, limits: &Limits) -> Result<(Option<Candidate>, Counters, Stop, u64)> {
    let mut rng = Rng::new(seed);
    let mut state = SearchState::new(p, start_poses(p, seed, clustered, &mut rng))?;
    let mut best: Option<Candidate> = None;
    let w = &p.weights;
    let span = (p.outline.x1 - p.outline.x0).max(p.outline.y1 - p.outline.y0);
    let total = limits.steps_per_seed.max(1);
    let mut cost = state.totals.cost(w);
    let t0 = 2_000_000.0_f64;
    let mut stop = Stop::Completed;
    let mut step = 0;
    while step < total {
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
            // Rigid group translation: every member moves by the same vector.
            let (dx, dy) = (snap(rng.range(-reach, reach)), snap(rng.range(-reach, reach)));
            p.groups[g].2.iter().map(|&m| {
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
        let next_cost = next.cost(w);
        let d = (next_cost - cost) as f64;
        if d <= 0.0 || draw < (-d / temperature).exp() {
            state.commit(p, trial)?;
            cost = next_cost;
            if state.totals.hard == 0 && state.totals.hard_count == 0
                && best.as_ref().is_none_or(|b| cost < b.cost) {
                best = Some(Candidate { seed, poses: state.poses.clone(), terms: state.totals.clone(), cost, step });
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
    Ok((best, state.counters, stop, step))
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

/// Process entry: prepare once, run the declared seed portfolio, emit a shortlist.
pub fn run(input: &Value) -> Result<Value> {
    let started = Instant::now();
    let p = prepare(input)?;
    let prepare_s = started.elapsed().as_secs_f64();
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
    };
    let shortlist_size = search["shortlist"].as_u64().unwrap_or(3) as usize;
    let mut found: Vec<Candidate> = Vec::new();
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
        let (best, c, stop, steps) = anneal(&p, seed, clustered, &limits)?;
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
            "best_terms": best.as_ref().map(|b| b.terms.value())}));
        let _ = crate::progress::emit(json!({"event": "seed", "seed": seed, "status": seed_status, "feasible": best.is_some(),
            "poses": best.as_ref().map(|b| pose_rows(&p, &b.poses)), "cost": best.as_ref().map(|b| b.cost.to_string())}));
        found.extend(best);
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
    Ok(json!({
        "schema": OUTPUT_SCHEMA, "problem_sha256": p.identity, "status": status,
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
        fps.extend(extra);
        json!({"schema": INPUT_SCHEMA, "problem_sha256": "a".repeat(64),
               "outline": {"kind": "rectangle", "rect_nm": [0, 0, 12_000_000, 10_000_000]},
               "rules": {"copper_clearance_nm": 200_000, "copper_edge_clearance_nm": 300_000},
               "footprints": fps,
               "groups": [{"name": "g", "anchor": "R1", "refs": ["R1", "R2"]}],
               "search": {"seeds": [{"seed": 0, "start": "scattered"}, {"seed": 1, "start": "clustered"}],
                          "steps_per_seed": 3000, "shortlist": 2}})
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
        let p = prepare(&input(vec![])).unwrap();
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
        let p = prepare(&input(vec![])).unwrap();
        let mut poses: Vec<Pose> = p.footprints.iter().map(|f| f.initial).collect();
        let base = full_evaluate(&p, &poses);
        // R3's body over the fiducial origin but its pads clear of the fiducial copper: no new body overlap.
        poses[2] = Pose { x: 1_500_000, y: 3_200_000, q: 0 };
        let t = full_evaluate(&p, &poses);
        assert!(t.hard_count <= base.hard_count + 1);
        poses[2] = Pose { x: 1_500_000, y: 1_500_000, q: 0 };
        let hit = full_evaluate(&p, &poses);
        assert!(hit.hard > 0, "copper-on-copper with the mark must be a hard violation");
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
    fn rotation_matches_kicad_quarter_turn_convention() {
        assert_eq!(rotate(1, 0, 1), (0, -1));
        assert_eq!(rotate(1, 2, 2), (-1, -2));
        assert_eq!(rotate(1, 0, 3), (0, 1));
        let r = place_rect(&Rect { x0: -2, y0: -1, x1: 4, y1: 1 }, &Pose { x: 10, y: 10, q: 1 });
        assert_eq!(r, Rect { x0: 9, y0: 6, x1: 11, y1: 12 });
    }
}
