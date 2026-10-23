//! Rigid block arrangement and residual packing in one native planning operation.
use crate::exact::{self, Int, Point, Rat};
use crate::footprint::{Keepout, PreparedFootprint};
use crate::geometry::{Bounds, Polygon};
use crate::{
    block_contract, block_variants, blocks, constraints, footprint, geometry, groups, locality,
    netlist, packing, policy, progress, rank, search,
};
use anyhow::{Context, Result, ensure};
use num_integer::Integer;
use num_traits::{Signed, ToPrimitive, Zero};
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};
use std::sync::Arc;
use std::time::{Duration, Instant};
const SCHEMA: &str = "agent-ee.block-arrangement/1";
const STRATEGY: &str = "ranked-first-fit-conflict-backjump/1";

fn hard(rule: &Value) -> bool {
    rule["provenance"]["enforcement"] == "hard"
}
fn provenance(v: &Value) -> Result<()> {
    blocks::keys(v, &["enforcement"], &["citation", "reason"])?;
    for k in ["citation", "reason"] {
        if let Some(v) = v.get(k) {
            blocks::text(v)?;
        }
    }
    ensure!(
        v["enforcement"] == "hard" || v["enforcement"] == "advisory",
        "enforcement must be hard or advisory"
    );
    ensure!(
        v["enforcement"] != "hard" || v.get("citation").is_some(),
        "a hard rule needs a citation; an unsourced rule is advisory"
    );
    Ok(())
}
fn known(v: &Value, known: &BTreeSet<String>) -> Result<Vec<String>> {
    let rs = blocks::refs(v)?;
    ensure!(
        rs.iter().all(|r| known.contains(r)),
        "unknown references in arrangement rule"
    );
    Ok(rs)
}
pub fn validate(
    document: &Value,
    block_ids: &BTreeSet<String>,
    refs: &BTreeSet<String>,
) -> Result<Value> {
    blocks::keys(
        document,
        &["schema", "step_budget"],
        &[
            "search_timeout_s",
            "priority_blocks",
            "objective",
            "thermal",
            "keepouts",
            "no_stacking",
            "no_block_over_fixed",
            "side_choice",
        ],
    )?;
    ensure!(document["schema"] == SCHEMA, "expected {SCHEMA}");
    ensure!(
        document["step_budget"]
            .as_u64()
            .is_some_and(|n| (1..=1_000_000).contains(&n)),
        "arrangement step budget must be 1..1000000"
    );
    if let Some(t) = document.get("search_timeout_s") {
        ensure!(
            t.as_f64().is_some_and(|n| n > 0.0 && n <= 86400.0),
            "search_timeout_s must be positive and at most 86400 seconds"
        );
    }
    if let Some(value) = document.get("side_choice") {
        ensure!(value.is_boolean(), "side_choice must be boolean");
    }
    let mut d = document.clone();
    if d.get("priority_blocks").is_none() {
        d["priority_blocks"] = json!([]);
    }
    known(&d["priority_blocks"], block_ids)?;
    if d.get("objective").is_none() {
        d["objective"] = json!({"terms":["pad-locality","inter-block-wire","thermal-spread"],"provenance":{"enforcement":"advisory","reason":"default lexicographic order; no sourced objective weights"}});
    }
    let objective = &d["objective"];
    blocks::keys(objective, &["terms", "provenance"], &[])?;
    let terms = blocks::refs(&objective["terms"])?;
    ensure!(
        !terms.is_empty()
            && terms
                .iter()
                .all(|s| ["pad-locality", "inter-block-wire", "thermal-spread"]
                    .contains(&s.as_str())),
        "objective terms must be a nonempty order of pad-locality, inter-block-wire, thermal-spread"
    );
    provenance(&objective["provenance"])?;
    if let Some(t) = d.get("thermal") {
        blocks::keys(t, &["sources", "provenance"], &["min_distance_nm"])?;
        ensure!(
            known(&t["sources"], refs)?.len() >= 2,
            "thermal spread needs at least two sources"
        );
        if let Some(n) = t.get("min_distance_nm") {
            ensure!(
                exact::integer(n)? > Int::zero(),
                "thermal min_distance_nm must be a positive integer"
            );
        }
        provenance(&t["provenance"])?;
    }
    let mut ids = BTreeSet::new();
    if let Some(keepouts) = d.get("keepouts") {
        for k in blocks::rows(keepouts)? {
            blocks::keys(k, &["id", "side", "polygons", "provenance"], &[])?;
            ensure!(ids.insert(blocks::text(&k["id"])?), "duplicate keep-out id");
            ensure!(
                k["side"] == "F" || k["side"] == "B",
                "keep-out side must be F or B"
            );
            policy::obstacle_polygons(&k["polygons"])?;
            provenance(&k["provenance"])?;
        }
    }
    if let Some(r) = d.get("no_stacking") {
        blocks::keys(r, &["references", "margin_nm", "provenance"], &[])?;
        ensure!(
            !known(&r["references"], refs)?.is_empty(),
            "no_stacking needs references"
        );
        ensure!(
            exact::integer(&r["margin_nm"])? >= Int::zero(),
            "no_stacking margin_nm must be a nonnegative integer"
        );
        provenance(&r["provenance"])?;
    }
    if let Some(r) = d.get("no_block_over_fixed") {
        blocks::keys(r, &["exempt_blocks", "provenance"], &[])?;
        known(&r["exempt_blocks"], block_ids)?;
        provenance(&r["provenance"])?;
    }
    Ok(d)
}
pub fn check_effective(captured: &Value, effective: &Value) -> Result<()> {
    let a = captured.as_object().context("packing policy object")?;
    let b = effective.as_object().context("effective policy object")?;
    let mut changed: BTreeSet<String> = a
        .keys()
        .chain(b.keys())
        .filter(|k| k.as_str() != "obstacles" && a.get(*k) != b.get(*k))
        .cloned()
        .collect();
    let before = a.get("obstacles").and_then(Value::as_array);
    let after = b.get("obstacles").and_then(Value::as_array);
    if before.zip(after).is_none_or(|(a, b)| !b.starts_with(a)) {
        changed.insert("obstacles".into());
    }
    ensure!(
        changed.is_empty(),
        "the effective policy may only add board obstacles to the captured policy: {}",
        changed.into_iter().collect::<Vec<_>>().join(", ")
    );
    Ok(())
}
fn opposite(side: &str) -> &str {
    if side == "F" { "B" } else { "F" }
}
fn shadow(fp: &PreparedFootprint, margin: &Int) -> Keepout {
    Keepout {
        side: opposite(&fp.side).into(),
        polygons: vec![geometry::rectangle(&fp.boxes[&fp.side].grown(margin))],
        name: fp.reference.clone(),
    }
}
fn obstacle(rule: &Value, shape: &Keepout, source: String) -> Value {
    json!({"side":shape.side,"polygons":geometry::polygons_value(&shape.polygons),"source":source,"provenance":{"class":"arrangement_rule","enforcement":"hard","citation":rule["provenance"]["citation"]}})
}
fn overlaps(fp: &PreparedFootprint, entries: &[Keepout]) -> bool {
    entries.iter().any(|e| {
        fp.courtyards.get(&e.side).is_some_and(|ps| {
            ps.iter()
                .any(|p| e.polygons.iter().any(|q| geometry::positive_overlap(p, q)))
        })
    })
}
fn through(fp: &PreparedFootprint, entries: &[Keepout]) -> bool {
    entries
        .iter()
        .filter(|e| e.side == opposite(&fp.side))
        .any(|e| {
            fp.courtyards[&fp.side]
                .iter()
                .any(|p| e.polygons.iter().any(|q| geometry::positive_overlap(p, q)))
        })
}
fn courtyard_entries(fp: &PreparedFootprint) -> Vec<Keepout> {
    fp.courtyards
        .iter()
        .map(|(s, p)| Keepout {
            side: s.clone(),
            polygons: p.clone(),
            name: fp.reference.clone(),
        })
        .collect()
}
fn macro_fp(id: &str, members: &[PreparedFootprint]) -> Result<PreparedFootprint> {
    PreparedFootprint::from_members(id, members)
}

type SideBoxes = Vec<(String, Bounds)>;
fn slabs(fp: &PreparedFootprint, any_side: bool) -> SideBoxes {
    fp.courtyards
        .iter()
        .flat_map(|(s, ps)| {
            ps.iter().flat_map(move |p| {
                geometry::slabs(p)
                    .unwrap_or_default()
                    .into_iter()
                    .map(move |b| (if any_side { "*".into() } else { s.clone() }, b))
            })
        })
        .collect()
}
fn inscribed(p: &Polygon) -> Option<Bounds> {
    let mut reversed = p.clone();
    reversed.reverse();
    if !geometry::convex(p) && !geometry::convex(&reversed) {
        return None;
    }
    let n = Int::from(p.len());
    let center = Point {
        x: p.iter().map(|p| p.x.clone()).sum::<Int>().div_floor(&n),
        y: p.iter().map(|p| p.y.clone()).sum::<Int>().div_floor(&n),
    };
    let signs: BTreeSet<_> = geometry::edges(p)
        .map(|(a, b)| geometry::cross(a, b, &center).signum())
        .collect();
    if signs.len() != 1 || signs.contains(&Int::zero()) {
        return None;
    }
    let r = geometry::edges(p)
        .map(|(a, b)| geometry::point_segment2(&center, a, b))
        .min()?;
    let h = exact::isqrt(&exact::floor(&(r / Rat::from_integer(2.into())))) - 1;
    if h <= Int::zero() {
        None
    } else {
        Some(Bounds {
            x0: &center.x - &h,
            y0: &center.y - &h,
            x1: &center.x + &h,
            y1: &center.y + &h,
        })
    }
}
#[derive(Clone)]
struct Rect {
    x0: Int,
    x1: Int,
    y0: Int,
    y1: Int,
    owner: Option<String>,
    doom: Option<(Int, Int)>,
}
fn gapped(candidates: &SideBoxes, placed: &SideBoxes, gap: &Int, owner: Option<&str>) -> Vec<Rect> {
    let mut out = Vec::new();
    for (s, c) in candidates {
        for (t, a) in placed {
            if s == t || s == "*" || t == "*" {
                out.push(Rect {
                    x0: &a.x0 - gap - &c.x1,
                    x1: &a.x1 + gap - &c.x0,
                    y0: &a.y0 - gap - &c.y1,
                    y1: &a.y1 + gap - &c.y0,
                    owner: owner.map(str::to_owned),
                    doom: None,
                });
            }
        }
    }
    out
}
fn solid(candidates: &SideBoxes, placed: &SideBoxes, gap: &Int) -> Vec<Rect> {
    let mut out = Vec::new();
    for (s, c) in candidates {
        for (t, a) in placed {
            if s != t {
                continue;
            }
            let r = Rect {
                x0: &a.x0 - &c.x1,
                x1: &a.x1 - &c.x0,
                y0: &a.y0 - &c.y1,
                y1: &a.y1 - &c.y0,
                owner: None,
                doom: None,
            };
            out.push(r.clone());
            if gap > &Int::zero() {
                out.push(Rect {
                    x0: &r.x0 - gap,
                    x1: &r.x1 + gap,
                    doom: Some((r.x0.clone(), r.x1.clone())),
                    ..r
                });
            }
        }
    }
    out
}
fn frontier(boxes: &SideBoxes) -> (SideBoxes, SideBoxes) {
    let (mut left, mut right) = (Vec::new(), Vec::new());
    for (i, (s, b)) in boxes.iter().enumerate() {
        let others: Vec<_> = boxes
            .iter()
            .enumerate()
            .filter(|(j, (t, a))| *j != i && s == t && a.y0 < b.y1 && b.y0 < a.y1)
            .map(|(_, (_, a))| a)
            .collect();
        if !others.iter().any(|a| a.x1 <= b.x0) {
            left.push((s.clone(), b.clone()));
        }
        if !others.iter().any(|a| a.x0 >= b.x1) {
            right.push((s.clone(), b.clone()));
        }
    }
    (left, right)
}
struct Rotated {
    members: Vec<PreparedFootprint>,
    macro_fp: PreparedFootprint,
    boxes: SideBoxes,
    slabs: SideBoxes,
    holes: SideBoxes,
    shadows: SideBoxes,
    frontier: (SideBoxes, SideBoxes),
    entries: Vec<Rect>,
    bbox: Bounds,
}
#[derive(Clone)]
struct Placed {
    block: String,
    variant: usize,
    selected_face: Option<String>,
    position: Point,
    rotation: i64,
    members: Vec<PreparedFootprint>,
    boxes: SideBoxes,
    slabs: SideBoxes,
    holes: SideBoxes,
    shadows: SideBoxes,
}
impl Placed {
    fn record(&self) -> Value {
        let mut row = json!({"block":self.block,"position_nm":exact::point_value(&self.position),"rotation_deg":self.rotation});
        if let Some(face) = &self.selected_face {
            row["selected_face"] = json!(face);
        }
        row
    }
}
#[derive(Eq, PartialEq, Ord, PartialOrd)]
struct Ranked {
    terms: Vec<rank::Fraction>,
    position: (rank::Integer, rank::Integer),
    rotation: i64,
    variant: usize,
    doomed: bool,
}
#[derive(Debug)]
struct Stopped(&'static str, &'static str);
impl std::fmt::Display for Stopped {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.1)
    }
}
impl std::error::Error for Stopped {}
struct Search<'a> {
    prepared: &'a packing::Prepared,
    policy: &'a Value,
    frames: BTreeMap<String, Vec<Vec<PreparedFootprint>>>,
    faces: BTreeMap<String, Vec<Option<String>>>,
    disabled_variants: BTreeSet<(String, usize)>,
    rotated: BTreeMap<(String, usize, i64), Arc<Rotated>>,
    owner: BTreeMap<String, String>,
    placed: Vec<Placed>,
    fixed_exact: Vec<Keepout>,
    solid: SideBoxes,
    forbid: SideBoxes,
    targets: SideBoxes,
    stacking: BTreeSet<String>,
    stack_margin: Int,
    over_fixed: BTreeSet<String>,
    sources: BTreeSet<String>,
    thermal_min: Option<Int>,
    grounds: BTreeSet<String>,
    groups: BTreeMap<String, search::Group>,
    terms: Vec<String>,
    steps: u64,
    budget: u64,
    started: Instant,
    deadline: Option<Instant>,
    clock: progress::Clock,
    current: Option<String>,
    backjumps: u64,
    rejected: BTreeMap<String, u64>,
    access_rejections: BTreeMap<String, u64>,
}
impl<'a> Search<'a> {
    fn new(
        prepared: &'a packing::Prepared,
        policy: &'a Value,
        arrangement: &Value,
        admitted: &BTreeMap<String, Value>,
    ) -> Result<Self> {
        let started = Instant::now();
        let clock = progress::Clock::new();
        let deadline = arrangement["search_timeout_s"]
            .as_f64()
            .map(|s| started + Duration::from_secs_f64(s));
        let by: BTreeMap<_, _> = prepared
            .capture
            .footprints
            .iter()
            .map(|f| (f.reference.clone(), f))
            .collect();
        let mut frames = BTreeMap::new();
        let mut faces = BTreeMap::new();
        let mut owner = BTreeMap::new();
        for (b, a) in admitted {
            let variants = a
                .get("variants")
                .and_then(Value::as_array)
                .map(|v| v.iter().collect::<Vec<_>>())
                .unwrap_or_else(|| vec![a]);
            let mut block_frames = Vec::new();
            let mut block_faces = Vec::new();
            for variant in variants {
                let native = variant
                    .get("capture")
                    .map(footprint::qualify_capture)
                    .transpose()?;
                let native_by: BTreeMap<_, _> = native
                    .as_ref()
                    .map(|c| {
                        c.footprints
                            .iter()
                            .map(|f| (f.reference.clone(), f))
                            .collect()
                    })
                    .unwrap_or_else(|| by.clone());
                let poses = variant["poses"].as_object().context("admitted poses")?;
                let mut members = Vec::new();
                for r in blocks::natural(poses.keys().cloned().collect()) {
                    let pose = &poses[&r];
                    members.push(native_by[&r].at(
                        &exact::point(&pose["position_nm"])?,
                        exact::rotation(&pose["rotation_deg"])?,
                    )?);
                    owner.insert(r, b.clone());
                }
                block_frames.push(members);
                block_faces.push(variant["selected_face"].as_str().map(str::to_owned));
            }
            frames.insert(b.clone(), block_frames);
            faces.insert(b.clone(), block_faces);
        }
        let mut disabled_variants = BTreeSet::new();
        if arrangement["side_choice"] == false {
            for (bid, variants) in &frames {
                for (index, members) in variants.iter().enumerate() {
                    if members.iter().any(|f| f.side != by[&f.reference].side) {
                        disabled_variants.insert((bid.clone(), index));
                    }
                }
            }
        }
        let fixed_exact = prepared.placed.iter().flat_map(courtyard_entries).collect();
        let solid: SideBoxes = prepared
            .exact
            .iter()
            .flat_map(|e| {
                e.polygons.iter().flat_map(|p| {
                    geometry::slabs(p)
                        .unwrap_or_default()
                        .into_iter()
                        .map(|b| (e.side.clone(), b))
                })
            })
            .collect();
        let mut forbid: SideBoxes = prepared
            .capture
            .board_keepouts
            .iter()
            .chain(prepared.placed.iter().flat_map(|f| f.keepouts.iter()))
            .flat_map(|e| {
                e.polygons.iter().flat_map(|p| {
                    geometry::slabs(p)
                        .unwrap_or_default()
                        .into_iter()
                        .map(|b| (e.side.clone(), b))
                })
            })
            .collect();
        for fp in &prepared.placed {
            for p in &fp.holes {
                if let Some(b) = inscribed(p) {
                    for side in ["F", "B"] {
                        forbid.push((side.into(), b.clone()));
                    }
                }
            }
        }
        let targets = prepared
            .placed
            .iter()
            .flat_map(|f| slabs(f, true))
            .collect();
        let stacking = if hard(&arrangement["no_stacking"]) {
            blocks::refs(&arrangement["no_stacking"]["references"])?
                .into_iter()
                .collect()
        } else {
            BTreeSet::new()
        };
        let stack_margin = arrangement["no_stacking"]
            .get("margin_nm")
            .map(exact::integer)
            .transpose()?
            .unwrap_or_default();
        let over_fixed = if hard(&arrangement["no_block_over_fixed"]) {
            let exempt: BTreeSet<_> =
                blocks::refs(&arrangement["no_block_over_fixed"]["exempt_blocks"])?
                    .into_iter()
                    .collect();
            admitted
                .keys()
                .filter(|b| !exempt.contains(*b))
                .cloned()
                .collect()
        } else {
            BTreeSet::new()
        };
        let sources = arrangement
            .get("thermal")
            .map(|t| blocks::refs(&t["sources"]))
            .transpose()?
            .unwrap_or_default()
            .into_iter()
            .collect();
        let thermal_min = if hard(&arrangement["thermal"]) {
            arrangement["thermal"]
                .get("min_distance_nm")
                .map(exact::integer)
                .transpose()?
        } else {
            None
        };
        Ok(Self {
            prepared,
            policy,
            frames,
            faces,
            disabled_variants,
            rotated: BTreeMap::new(),
            owner,
            placed: Vec::new(),
            fixed_exact,
            solid,
            forbid,
            targets,
            stacking,
            stack_margin,
            over_fixed,
            sources,
            thermal_min,
            grounds: blocks::refs(&policy["ground_nets"])?.into_iter().collect(),
            groups: search::membership(policy)?,
            terms: blocks::refs(&arrangement["objective"]["terms"])?,
            steps: 0,
            budget: arrangement["step_budget"].as_u64().unwrap(),
            started,
            deadline,
            clock,
            current: None,
            backjumps: 0,
            rejected: BTreeMap::new(),
            access_rejections: BTreeMap::new(),
        })
    }
    fn checkpoint(&self) -> Result<()> {
        if let Some(elapsed) = self.clock.elapsed_if_due() {
            progress::emit(json!({
                "status": "arranging",
                "steps_used": self.steps,
                "blocks_placed": self.placed.len(),
                "block": self.current,
                "backjumps": self.backjumps,
                "rejected": self.rejected,
                "elapsed_s": elapsed,
            }))?;
        }
        if self.deadline.is_some_and(|d| Instant::now() >= d) {
            return Err(Stopped(
                "arrangement_budget_exhausted",
                "wall-time arrangement budget exhausted",
            )
            .into());
        }
        Ok(())
    }
    fn members_at(
        &self,
        bid: &str,
        variant: usize,
        p: &Point,
        rotation: i64,
    ) -> Result<Vec<PreparedFootprint>> {
        self.frames[bid][variant]
            .iter()
            .map(|f| {
                f.at(
                    &exact::rotate(&f.position, rotation / 90).plus(p),
                    (f.rotation + rotation) % 360,
                )
            })
            .collect()
    }
    fn rotated(&mut self, bid: &str, variant: usize, rotation: i64) -> Result<Arc<Rotated>> {
        let key = (bid.to_owned(), variant, rotation);
        if let Some(r) = self.rotated.get(&key) {
            return Ok(r.clone());
        }
        let members = self.members_at(bid, variant, &Point::zero(), rotation)?;
        let mut macro_fp = macro_fp(bid, &members)?;
        macro_fp.rotation = rotation;
        macro_fp.rotation_json = json!(rotation);
        Arc::make_mut(&mut macro_fp.raw)["rotation_deg"] = json!(rotation);
        let boxes: SideBoxes = members
            .iter()
            .flat_map(|f| f.boxes.iter().map(|(s, b)| (s.clone(), b.clone())))
            .collect();
        let all_slabs: SideBoxes = members.iter().flat_map(|f| slabs(f, false)).collect();
        let holes: SideBoxes = macro_fp
            .holes
            .iter()
            .filter_map(inscribed)
            .map(|b| ("*".into(), b))
            .collect();
        let shadows = members
            .iter()
            .filter(|f| self.stacking.contains(&f.reference))
            .map(|f| {
                let s = shadow(f, &self.stack_margin);
                Ok((s.side, geometry::bounds(&s.polygons)?))
            })
            .collect::<Result<_>>()?;
        let mut entries = solid(
            &all_slabs,
            &self.solid,
            &exact::integer(&self.policy["gap_nm"])?,
        );
        entries.extend(gapped(&all_slabs, &self.forbid, &Int::zero(), None));
        entries.extend(gapped(&holes, &self.targets, &Int::zero(), None));
        let result = Arc::new(Rotated {
            frontier: frontier(&boxes),
            bbox: search::box_union(boxes.iter().map(|(_, b)| b)),
            slabs: members
                .iter()
                .filter(|f| !f.targets.is_empty())
                .flat_map(|f| slabs(f, true))
                .collect(),
            members,
            macro_fp,
            boxes,
            holes,
            shadows,
            entries,
        });
        self.rotated.insert(key, result.clone());
        Ok(result)
    }
    fn placed_fps(&self) -> Vec<PreparedFootprint> {
        self.prepared
            .placed
            .iter()
            .chain(self.placed.iter().flat_map(|p| p.members.iter()))
            .cloned()
            .collect()
    }
    fn push(&mut self, bid: &str, variant: usize, p: Point, rotation: i64) -> Result<()> {
        let members = self.members_at(bid, variant, &p, rotation)?;
        let boxes = members
            .iter()
            .flat_map(|f| f.boxes.iter().map(|(s, b)| (s.clone(), b.clone())))
            .collect();
        let slabs = members
            .iter()
            .filter(|f| !f.targets.is_empty())
            .flat_map(|f| slabs(f, true))
            .collect();
        let holes = members
            .iter()
            .flat_map(|f| {
                f.holes
                    .iter()
                    .filter_map(inscribed)
                    .map(|b| ("*".into(), b))
            })
            .collect();
        let shadows = members
            .iter()
            .filter(|f| self.stacking.contains(&f.reference))
            .map(|f| {
                let s = shadow(f, &self.stack_margin);
                Ok((s.side, geometry::bounds(&s.polygons)?))
            })
            .collect::<Result<_>>()?;
        self.placed.push(Placed {
            block: bid.into(),
            variant,
            selected_face: self.faces[bid][variant].clone(),
            position: p,
            rotation,
            members,
            boxes,
            slabs,
            holes,
            shadows,
        });
        Ok(())
    }
    fn owners(&self, refs: BTreeSet<String>, bid: &str) -> BTreeSet<String> {
        refs.into_iter()
            .filter_map(|r| self.owner.get(&r))
            .filter(|b| b.as_str() != bid)
            .cloned()
            .collect()
    }
    fn ranked(&mut self, bid: &str, conflicts: &mut BTreeSet<String>) -> Result<Vec<Ranked>> {
        self.checkpoint()?;
        let gap = exact::integer(&self.policy["gap_nm"])?;
        let margin = policy::edge_margin_nm(self.policy)?;
        let outline = &self.prepared.capture.outline;
        let window = geometry::bounds(std::slice::from_ref(outline))?.grown(&(-&margin));
        let placed = self.placed_fps();
        let positions: BTreeMap<_, _> = placed
            .iter()
            .map(|f| (f.reference.clone(), f.position.clone()))
            .collect();
        let mut peers = BTreeMap::<String, Vec<Point>>::new();
        for f in &placed {
            for p in &f.pads {
                if !p.net.is_empty() {
                    peers
                        .entry(p.net.clone())
                        .or_default()
                        .push(p.position.clone());
                }
            }
        }
        let mut index = match self.policy.get("electrical_locality") {
            Some(p) => Some(locality::PlacementIndex::new(p)?),
            None => None,
        };
        if let Some(i) = &mut index {
            for f in &placed {
                i.push(&f.to_value()?)?;
            }
        }
        let mut anchors: SideBoxes = self
            .placed
            .iter()
            .flat_map(|p| p.boxes.iter().cloned())
            .collect();
        anchors.extend(
            self.prepared
                .exact
                .iter()
                .map(|e| Ok((e.side.clone(), geometry::bounds(&e.polygons)?)))
                .collect::<Result<SideBoxes>>()?,
        );
        anchors.extend(self.solid.clone());
        anchors.extend(
            self.prepared
                .capture
                .board_keepouts
                .iter()
                .chain(placed.iter().flat_map(|f| f.keepouts.iter()))
                .map(|e| Ok((e.side.clone(), geometry::bounds(&e.polygons)?)))
                .collect::<Result<SideBoxes>>()?,
        );
        let mut ranked = Vec::new();
        for variant in 0..self.frames[bid].len() {
            if self.disabled_variants.contains(&(bid.to_owned(), variant)) {
                continue;
            }
            for rotation in [0, 90, 180, 270] {
                let own = self.rotated(bid, variant, rotation)?;
                let mut evaluator = locality::CandidateLocality::default();
                if let Some(index) = &index {
                    for f in &own.members {
                        evaluator.pairs.extend(index.prepare(&f.to_value()?)?.pairs);
                    }
                }
                let mut targets = evaluator.targets()?;
                for f in &own.members {
                    if let Some(g) = self.groups.get(&f.reference) {
                        let anchor = g
                            .anchor_point
                            .as_ref()
                            .or_else(|| g.anchor_ref.as_ref().and_then(|r| positions.get(r)));
                        if let Some(a) = anchor {
                            for (dx, dy) in [
                                (Int::zero(), Int::zero()),
                                (-&g.limit, Int::zero()),
                                (g.limit.clone(), Int::zero()),
                                (Int::zero(), -&g.limit),
                                (Int::zero(), g.limit.clone()),
                            ] {
                                targets.push(Point {
                                    x: &a.x - &f.position.x + dx,
                                    y: &a.y - &f.position.y + dy,
                                });
                            }
                        }
                        for p in g.region.iter().flatten() {
                            targets.push(p.minus(&f.position));
                        }
                    }
                }
                let overhang = own
                    .members
                    .iter()
                    .map(constraints::access_overhangs)
                    .collect::<Result<Vec<_>>>()?
                    .into_iter()
                    .any(|v| v);
                let (xs, ys) = search::candidate_axes(
                    &own.macro_fp,
                    outline,
                    &anchors,
                    &gap,
                    &margin,
                    &targets,
                    if overhang { None } else { Some(&window) },
                )?;
                let (mut xs, mut ys): (BTreeSet<Int>, BTreeSet<Int>) =
                    (xs.into_iter().collect(), ys.into_iter().collect());
                let in_x = |x: &Int| window.x0 <= &own.bbox.x0 + x && &own.bbox.x1 + x <= window.x1;
                let in_y = |y: &Int| window.y0 <= &own.bbox.y0 + y && &own.bbox.y1 + y <= window.y1;
                for f in &own.members {
                    let (ax, ay) = constraints::access_axes(f, outline)?;
                    xs.extend(ax.into_iter().filter(|x| overhang || in_x(x)));
                    ys.extend(ay.into_iter().filter(|y| overhang || in_y(y)));
                    for poly in &f.copper_outline {
                        let b = geometry::bounds(std::slice::from_ref(poly))?;
                        if let Some(s) = &f.setback {
                            for p in outline {
                                for x in [&p.x + s - &b.x0, &p.x - s - &b.x1] {
                                    if in_x(&x) {
                                        xs.insert(x);
                                    }
                                }
                                for y in [&p.y + s - &b.y0, &p.y - s - &b.y1] {
                                    if in_y(&y) {
                                        ys.insert(y);
                                    }
                                }
                            }
                        }
                    }
                }
                for (side, m) in &own.frontier.0 {
                    for (other, a) in &anchors {
                        if side == other {
                            let x = &a.x1 + &gap - &m.x0;
                            if in_x(&x) {
                                xs.insert(x);
                            }
                        }
                    }
                }
                for (side, m) in &own.frontier.1 {
                    for (other, a) in &anchors {
                        if side == other {
                            let x = &a.x0 - &gap - &m.x1;
                            if in_x(&x) {
                                xs.insert(x);
                            }
                        }
                    }
                }
                let mut entries = own.entries.clone();
                for p in &self.placed {
                    let mut reserved = p.boxes.clone();
                    reserved.extend(p.shadows.clone());
                    entries.extend(gapped(&own.boxes, &reserved, &gap, Some(&p.block)));
                    entries.extend(gapped(&own.shadows, &p.boxes, &gap, Some(&p.block)));
                    entries.extend(gapped(&own.holes, &p.slabs, &Int::zero(), Some(&p.block)));
                    entries.extend(gapped(&own.slabs, &p.holes, &Int::zero(), Some(&p.block)));
                }
                let mut on_net = BTreeMap::<String, Vec<Point>>::new();
                for f in &own.members {
                    for p in &f.pads {
                        if !p.net.is_empty()
                            && !self.grounds.contains(&p.net)
                            && peers.contains_key(&p.net)
                        {
                            on_net
                                .entry(p.net.clone())
                                .or_default()
                                .push(p.position.clone());
                        }
                    }
                }
                let pads: Vec<_> = on_net
                    .into_iter()
                    .map(|(n, ps)| {
                        let count = Int::from(ps.len());
                        (
                            n,
                            Point {
                                x: ps
                                    .iter()
                                    .map(|p| p.x.clone())
                                    .sum::<Int>()
                                    .div_floor(&count),
                                y: ps
                                    .iter()
                                    .map(|p| p.y.clone())
                                    .sum::<Int>()
                                    .div_floor(&count),
                            },
                        )
                    })
                    .collect();
                let sources: Vec<_> = own
                    .members
                    .iter()
                    .filter(|f| self.sources.contains(&f.reference))
                    .map(|f| f.position.clone())
                    .collect();
                let placed_sources: Vec<_> = self
                    .sources
                    .iter()
                    .filter_map(|r| positions.get(r).map(|p| (r, p)))
                    .collect();
                let limit2 = self.thermal_min.as_ref().map(|n| n * n);
                let contacts = (&window.y0 - &own.bbox.y0, &window.y1 - &own.bbox.y1);
                let low = ys
                    .first()
                    .cloned()
                    .unwrap_or_else(|| contacts.0.clone())
                    .min(contacts.0.clone())
                    .min(contacts.1.clone());
                let high = ys
                    .last()
                    .cloned()
                    .unwrap_or_else(|| contacts.1.clone())
                    .max(contacts.0.clone())
                    .max(contacts.1.clone());
                entries.retain(|e| &e.y1 + &gap >= low && &e.y0 - &gap <= high);
                entries.sort_by(|a, b| a.x0.cmp(&b.x0));
                let (mut plain, mut dooms, mut cursor): (Vec<usize>, Vec<usize>, usize) =
                    (Vec::new(), Vec::new(), 0);
                for x in xs {
                    self.checkpoint()?;
                    while cursor < entries.len() && entries[cursor].x0 < x {
                        if entries[cursor].doom.is_none() {
                            plain.push(cursor);
                        } else {
                            dooms.push(cursor);
                        }
                        cursor += 1;
                    }
                    plain.retain(|i| entries[*i].x1 > x);
                    dooms.retain(|i| entries[*i].x1 > x);
                    plain.sort_by(|i, j| entries[*i].y0.cmp(&entries[*j].y0));
                    let mut runs = Vec::<(Int, Int)>::new();
                    for i in &plain {
                        let e = &entries[*i];
                        if let Some(last) = runs.last_mut() {
                            if e.y0 < last.1 {
                                last.1 = last.1.clone().max(e.y1.clone());
                                continue;
                            }
                        }
                        runs.push((e.y0.clone(), e.y1.clone()));
                    }
                    let mut here = ys.clone();
                    for (lo, hi) in &runs {
                        for y in [lo, hi] {
                            if contacts.0 <= *y && *y <= contacts.1 {
                                here.insert(y.clone());
                            }
                        }
                    }
                    for i in &plain {
                        let e = &entries[*i];
                        if let Some(owner) = &e.owner {
                            if here
                                .range((
                                    std::ops::Bound::Excluded(&e.y0),
                                    std::ops::Bound::Excluded(&e.y1),
                                ))
                                .next()
                                .is_some()
                            {
                                conflicts.insert(owner.clone());
                            }
                        }
                    }
                    for y in here {
                        if runs.iter().any(|(lo, hi)| *lo < y && y < *hi) {
                            continue;
                        }
                        let mut doomed = false;
                        for i in &dooms {
                            let e = &entries[*i];
                            let (lo, hi) = e.doom.as_ref().unwrap();
                            let dx = Int::zero().max(lo - &x).max(&x - hi);
                            if dx >= gap {
                                continue;
                            }
                            let reach = exact::isqrt(&(&gap * &gap - &dx * &dx - 1));
                            if &e.y0 - &reach <= y && y <= &e.y1 + &reach {
                                doomed = true;
                                break;
                            }
                        }
                        let position = Point { x: x.clone(), y };
                        let (hit, local_cost) = evaluator.constraint(&position);
                        if !hit.is_empty() {
                            conflicts.extend(self.owners(hit, bid));
                            continue;
                        }
                        if let Some(limit2) = &limit2 {
                            let hit: BTreeSet<_> = sources
                                .iter()
                                .flat_map(|p| {
                                    placed_sources.iter().filter_map(|(r, q)| {
                                        (exact::squared_distance(&p.plus(&position), q) < *limit2)
                                            .then(|| (*r).clone())
                                    })
                                })
                                .collect();
                            if !hit.is_empty() {
                                conflicts.extend(self.owners(hit, bid));
                                continue;
                            }
                        }
                        let wire = search::wire_cost(&pads, &peers, &position.x, &position.y);
                        let thermal = -sources
                            .iter()
                            .flat_map(|p| {
                                placed_sources
                                    .iter()
                                    .map(|(_, q)| exact::squared_distance(&p.plus(&position), q))
                            })
                            .min()
                            .unwrap_or_default();
                        let terms = self
                            .terms
                            .iter()
                            .map(|term| match term.as_str() {
                                "pad-locality" => local_cost.clone(),
                                "inter-block-wire" => Rat::from_integer(wire.clone()),
                                "thermal-spread" => Rat::from_integer(thermal.clone()),
                                _ => unreachable!(),
                            })
                            .map(rank::Fraction::from)
                            .collect();
                        ranked.push(Ranked {
                            terms,
                            position: (position.x.into(), position.y.into()),
                            rotation,
                            variant,
                            doomed,
                        });
                    }
                }
                self.checkpoint()?;
            }
        }
        ranked.sort();
        Ok(ranked)
    }
    fn blockers(
        &mut self,
        bid: &str,
        proposed: &PreparedFootprint,
        members: &[PreparedFootprint],
    ) -> Result<Option<(String, BTreeSet<String>)>> {
        let placed = self.placed_fps();
        let gap = exact::integer(&self.policy["gap_nm"])?;
        let no = BTreeSet::new();
        let mut hit = BTreeSet::new();
        let mut any = false;
        for f in &placed {
            if constraints::hole_conflict(proposed, f) {
                any = true;
                if let Some(b) = self.owner.get(&f.reference) {
                    hit.insert(b.clone());
                }
            }
        }
        if any {
            return Ok(Some(("drilled_pad".into(), hit)));
        }
        let mut bad_access = false;
        for f in members {
            if !constraints::access_admits(f, &self.prepared.capture.outline)? {
                *self
                    .access_rejections
                    .entry(f.reference.clone())
                    .or_default() += 1;
                bad_access = true;
            }
        }
        if bad_access {
            return Ok(Some(("access_direction".into(), no)));
        }
        let margin = policy::edge_margin_nm(self.policy)?;
        let over = members
            .iter()
            .map(constraints::access_overhangs)
            .collect::<Result<Vec<_>>>()?
            .into_iter()
            .any(|v| v);
        let fits = if over {
            let mut all = true;
            for f in members {
                all &= constraints::fits(f, &self.prepared.capture.outline, &margin, &[])?;
            }
            all
        } else {
            constraints::fits(proposed, &self.prepared.capture.outline, &margin, &[])?
        };
        if !fits {
            return Ok(Some(("outline".into(), no)));
        }
        if constraints::near(proposed, &self.prepared.exact, &gap) {
            return Ok(Some(("fixed_or_obstacle".into(), no)));
        }
        if constraints::keepout_blocked(proposed, &[], &self.prepared.capture.board_keepouts) {
            return Ok(Some(("rule_area".into(), no)));
        }
        let mut owners = BTreeSet::new();
        let mut any = false;
        for f in &placed {
            if constraints::keepout_blocked(proposed, std::slice::from_ref(f), &[]) {
                any = true;
                if let Some(b) = self.owner.get(&f.reference) {
                    owners.insert(b.clone());
                }
            }
        }
        if any {
            return Ok(Some(("rule_area".into(), owners)));
        }
        for f in members {
            if let Some(blocked) = constraints::copper_escapes(
                f,
                &self.prepared.capture.outline,
                &self.prepared.capture.board_keepouts,
                &placed,
                &self.prepared.capture.board_pad_keepouts,
            )? {
                return Ok(Some((blocked.reason, self.owners(blocked.blockers, bid))));
            }
        }
        let mut hits = BTreeSet::new();
        for p in &self.placed {
            for f in &p.members {
                if self.stacking.contains(&f.reference)
                    && constraints::near(proposed, &[shadow(f, &self.stack_margin)], &gap)
                {
                    hits.insert(p.block.clone());
                }
            }
        }
        for f in members {
            if self.stacking.contains(&f.reference) {
                let shape = shadow(f, &self.stack_margin);
                if members.iter().any(|other| {
                    other.reference != f.reference
                        && constraints::near(other, std::slice::from_ref(&shape), &gap)
                }) {
                    return Ok(Some(("no_stacking".into(), no)));
                }
                let mut own = f.clone();
                own.courtyards = [(shape.side.clone(), shape.polygons.clone())]
                    .into_iter()
                    .collect();
                own.boxes = [(shape.side, geometry::bounds(&shape.polygons)?)]
                    .into_iter()
                    .collect();
                own.refresh_geometry_bounds()?;
                if constraints::near(&own, &self.fixed_exact, &gap) {
                    return Ok(Some(("no_stacking".into(), no)));
                }
                for p in &self.placed {
                    if p.members
                        .iter()
                        .any(|f| constraints::near(&own, &courtyard_entries(f), &gap))
                    {
                        hits.insert(p.block.clone());
                    }
                }
            }
        }
        if !hits.is_empty() {
            return Ok(Some(("no_stacking".into(), hits)));
        }
        if self.over_fixed.contains(bid) && members.iter().any(|f| through(f, &self.fixed_exact)) {
            return Ok(Some(("no_block_over_fixed".into(), no)));
        }
        let mut positions: BTreeMap<_, _> = placed
            .iter()
            .map(|f| (f.reference.clone(), f.position.clone()))
            .collect();
        let before = positions.clone();
        positions.extend(
            members
                .iter()
                .map(|f| (f.reference.clone(), f.position.clone())),
        );
        for f in members {
            if let Some(g) = self.groups.get(&f.reference) {
                if !g.region.is_empty()
                    && f.courtyards.values().flatten().any(|p| {
                        g.region
                            .iter()
                            .map(|r| geometry::intersection_area(p, r))
                            .sum::<Rat>()
                            != geometry::area(p)
                    })
                {
                    return Ok(Some(("group".into(), no)));
                }
                let anchor = g
                    .anchor_point
                    .as_ref()
                    .or_else(|| g.anchor_ref.as_ref().and_then(|r| positions.get(r)));
                if let Some(p) = anchor {
                    if exact::squared_distance(&f.position, p) > &g.limit * &g.limit {
                        let refs = g.anchor_ref.iter().cloned().collect();
                        return Ok(Some(("group".into(), self.owners(refs, bid))));
                    }
                }
            }
            for (r, g) in &self.groups {
                if g.anchor_ref.as_ref() == Some(&f.reference) {
                    if let Some(p) = before.get(r) {
                        if exact::squared_distance(p, &f.position) > &g.limit * &g.limit {
                            return Ok(Some((
                                "group".into(),
                                self.owners([r.clone()].into_iter().collect(), bid),
                            )));
                        }
                    }
                }
            }
        }
        Ok(None)
    }
    fn candidate(
        &mut self,
        bid: &str,
        ranked: &mut std::vec::IntoIter<Ranked>,
        conflicts: &mut BTreeSet<String>,
    ) -> Result<Option<(Point, i64, usize)>> {
        for row in ranked {
            self.checkpoint()?;
            if self.steps == self.budget {
                return Err(Stopped(
                    "arrangement_budget_exhausted",
                    "deterministic arrangement candidate budget exhausted",
                )
                .into());
            }
            self.steps += 1;
            if row.doomed {
                continue;
            }
            let position = Point {
                x: row.position.0.exact(),
                y: row.position.1.exact(),
            };
            let own = self.rotated(bid, row.variant, row.rotation)?;
            let proposed = own.macro_fp.at(&position, row.rotation)?;
            let members = own
                .members
                .iter()
                .map(|f| f.at(&f.position.plus(&position), f.rotation))
                .collect::<Result<Vec<_>>>()?;
            if let Some((reason, hit)) = self.blockers(bid, &proposed, &members)? {
                *self.rejected.entry(reason).or_default() += 1;
                conflicts.extend(hit);
            } else {
                return Ok(Some((position, row.rotation, row.variant)));
            }
        }
        Ok(None)
    }
}

fn order(
    admitted: &BTreeMap<String, Value>,
    by: &BTreeMap<String, PreparedFootprint>,
    fixed: &BTreeSet<String>,
    priority: &Value,
    grounds: &BTreeSet<String>,
    through: &BTreeSet<String>,
) -> Result<Vec<String>> {
    let nets: BTreeMap<String, BTreeSet<String>> = admitted
        .iter()
        .map(|(b, a)| {
            (
                b.clone(),
                a["poses"]
                    .as_object()
                    .unwrap()
                    .keys()
                    .flat_map(|r| {
                        by[r]
                            .pads
                            .iter()
                            .filter(|p| !p.net.is_empty() && !grounds.contains(&p.net))
                            .map(|p| p.net.clone())
                    })
                    .collect(),
            )
        })
        .collect();
    let area: BTreeMap<String, Int> = admitted
        .iter()
        .map(|(b, a)| {
            (
                b.clone(),
                a["poses"]
                    .as_object()
                    .unwrap()
                    .keys()
                    .flat_map(|r| {
                        by[r]
                            .boxes
                            .values()
                            .map(|b| (&b.x1 - &b.x0) * (&b.y1 - &b.y0))
                    })
                    .sum(),
            )
        })
        .collect();
    let mut reached: BTreeSet<_> = fixed
        .iter()
        .flat_map(|r| {
            by[r]
                .pads
                .iter()
                .filter(|p| !p.net.is_empty() && !grounds.contains(&p.net))
                .map(|p| p.net.clone())
        })
        .collect();
    let mut out = blocks::refs(priority)?;
    for b in &out {
        reached.extend(nets[b].clone());
    }
    while out.len() < admitted.len() {
        let b = admitted
            .keys()
            .filter(|b| !out.contains(b))
            .min_by(|a, b| {
                (!through.contains(*a))
                    .cmp(&(!through.contains(*b)))
                    .then(
                        nets[*b]
                            .intersection(&reached)
                            .count()
                            .cmp(&nets[*a].intersection(&reached).count()),
                    )
                    .then(area[*b].cmp(&area[*a]))
                    .then(blocks::natural_cmp(a, b))
            })
            .unwrap()
            .clone();
        reached.extend(nets[&b].clone());
        out.push(b);
    }
    Ok(out)
}
fn net_counts(
    admitted: &BTreeMap<String, Value>,
    by: &BTreeMap<String, PreparedFootprint>,
    fixed: &BTreeSet<String>,
    grounds: &BTreeSet<String>,
) -> Value {
    let mut owner: BTreeMap<String, String> = admitted
        .iter()
        .flat_map(|(b, a)| {
            a["poses"]
                .as_object()
                .unwrap()
                .keys()
                .map(|r| (r.clone(), b.clone()))
        })
        .collect();
    owner.extend(fixed.iter().map(|r| (r.clone(), r.clone())));
    let mut touch = BTreeMap::<String, BTreeSet<String>>::new();
    for (r, b) in owner {
        for p in &by[&r].pads {
            if !p.net.is_empty() && !grounds.contains(&p.net) {
                touch.entry(p.net.clone()).or_default().insert(b.clone());
            }
        }
    }
    let mut counts = BTreeMap::<(String, String), u64>::new();
    for owners in touch.values() {
        for a in owners {
            for b in owners {
                if blocks::natural_cmp(a, b).is_lt() {
                    *counts.entry((a.clone(), b.clone())).or_default() += 1;
                }
            }
        }
    }
    let mut rows: Vec<_> = counts.into_iter().collect();
    rows.sort_by(|((a, b), _), ((c, d), _)| {
        blocks::natural_cmp(a, c).then(blocks::natural_cmp(b, d))
    });
    json!(
        rows.into_iter()
            .map(|((a, b), n)| json!({"between":[a,b],"nets":n}))
            .collect::<Vec<_>>()
    )
}
pub fn grade_rules(
    capture: &Value,
    arrangement: &Value,
    owner: &BTreeMap<String, String>,
    fixed: &BTreeSet<String>,
) -> Result<Value> {
    let by: BTreeMap<String, PreparedFootprint> = blocks::board(capture)?
        .into_iter()
        .map(|(r, f)| Ok((r, PreparedFootprint::from_value(&f, None)?)))
        .collect::<Result<_>>()?;
    let natural = blocks::natural(by.keys().cloned().collect());
    let mut rows = Vec::new();
    let make = |rule: &str, r: &Value, findings: Vec<Value>| json!({"rule":rule,"enforcement":r["provenance"]["enforcement"],"status":if findings.is_empty(){"pass"}else{"defect"},"findings":findings});
    for k in arrangement
        .get("keepouts")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
    {
        let entry = Keepout {
            side: blocks::text(&k["side"])?.into(),
            polygons: policy::obstacle_polygons(&k["polygons"])?,
            name: String::new(),
        };
        let found = natural
            .iter()
            .filter(|r| overlaps(&by[*r], std::slice::from_ref(&entry)))
            .map(|r| json!(r))
            .collect();
        let mut row = make("keepout", k, found);
        row["id"] = k["id"].clone();
        rows.push(row);
    }
    if let Some(r) = arrangement.get("no_stacking") {
        let margin = exact::integer(&r["margin_nm"])?;
        let mut found = Vec::new();
        for source in blocks::refs(&r["references"])? {
            let shadow = shadow(&by[&source], &margin);
            for other in &natural {
                if *other != source && overlaps(&by[other], std::slice::from_ref(&shadow)) {
                    found.push(json!([source, other]));
                }
            }
        }
        rows.push(make("no_stacking", r, found));
    }
    if let Some(r) = arrangement.get("no_block_over_fixed") {
        let exempt: BTreeSet<_> = blocks::refs(&r["exempt_blocks"])?.into_iter().collect();
        let mut found = Vec::new();
        for member in blocks::natural(owner.keys().cloned().collect()) {
            if exempt.contains(&owner[&member]) {
                continue;
            }
            for f in blocks::natural(fixed.iter().cloned().collect()) {
                if through(&by[&member], &courtyard_entries(&by[&f])) {
                    found.push(json!([owner[&member], member, f]));
                }
            }
        }
        rows.push(make("no_block_over_fixed", r, found));
    }
    if let Some(r) = arrangement.get("thermal") {
        let sources = blocks::refs(&r["sources"])?;
        let limit = r
            .get("min_distance_nm")
            .map(exact::integer)
            .transpose()?
            .map(|n| n.to_f64().context("thermal limit outside float range"))
            .transpose()?;
        let mut pairs = Vec::new();
        let mut findings = Vec::new();
        let mut min = f64::INFINITY;
        for (i, a) in sources.iter().enumerate() {
            for b in &sources[i + 1..] {
                let distance = exact::squared_distance(&by[a].position, &by[b].position)
                    .to_f64()
                    .context("thermal distance outside float range")?
                    .sqrt()
                    / 1e6;
                min = min.min(distance);
                let pair = json!({"refs":[a,b],"distance_mm":distance});
                if limit.is_some_and(|l| distance * 1e6 < l) {
                    findings.push(pair["refs"].clone());
                }
                pairs.push(pair);
            }
        }
        let mut row = make("thermal_spread", r, findings);
        row["pairs"] = json!(pairs);
        row["min_distance_mm"] = json!(min);
        rows.push(row);
    }
    Ok(json!(rows))
}
fn deferred_grading(plan: &Value, admitted: &BTreeMap<String, Value>) -> Value {
    let mut out = Vec::new();
    for b in blocks::natural(admitted.keys().cloned().collect()) {
        let d = &admitted[&b]["deferred"];
        for id in d["obligations"].as_array().unwrap() {
            let grade = &plan["locality_grading"];
            let measured = grade["measured"]
                .as_array()
                .into_iter()
                .flatten()
                .filter(|m| m["obligation"] == *id)
                .count();
            let bad = ["defects", "unknown"]
                .into_iter()
                .flat_map(|k| grade[k].as_array().into_iter().flatten())
                .any(|r| r["obligation"] == *id);
            let advisories: Vec<_> = grade["advisories"]
                .as_array()
                .into_iter()
                .flatten()
                .filter(|r| r["obligation"] == *id)
                .cloned()
                .collect();
            let mut row = json!({"block":b,"kind":"obligation","id":id,"measured":measured,"status":if bad{"defect"}else if measured==0{"unknown"}else if advisories.iter().any(|r|r["exceeded"]==true){"info"}else{"pass"}});
            if !advisories.is_empty() {
                row["advisories"] = json!(advisories);
            }
            out.push(row);
        }
        for id in d["groups"].as_array().unwrap() {
            let grade = &plan["group_grading"];
            let measured = grade["measured"]
                .as_array()
                .into_iter()
                .flatten()
                .filter(|m| m["group"] == *id)
                .count();
            let bad = groups::violations(grade).iter().any(|r| r["group"] == *id);
            out.push(json!({"block":b,"kind":"group","id":id,"measured":measured,"status":if measured>0&&!bad{"pass"}else if bad{"defect"}else{"unknown"}}));
        }
    }
    json!(out)
}
fn failure(
    capture: &Value,
    policy: &Value,
    record: &Value,
    status: &str,
    reason: &str,
    steps: u64,
) -> Result<Value> {
    let mut result = packing::failure(capture, policy, status, "unknown", steps, reason)?;
    result["block_arrangement"] = record.clone();
    Ok(result)
}
fn stopped(
    capture: &Value,
    policy: &Value,
    record: &Value,
    search: &Search,
    status: &str,
    reason: &str,
    unplaced: &str,
    backjumps: u64,
    peak: usize,
) -> Result<Value> {
    let mut record = record.clone();
    let mut reason = reason.to_owned();
    if !search.access_rejections.is_empty() {
        reason.push_str("; access direction rejected poses for ");
        reason.push_str(
            &search
                .access_rejections
                .keys()
                .cloned()
                .collect::<Vec<_>>()
                .join(", "),
        );
    }
    record["search"] = json!({"strategy":STRATEGY,"steps_used":search.steps,"step_budget":search.budget,"backjumps":backjumps,"placed_peak":peak,"rejected":search.rejected,"unplaced":unplaced,"partial":{"authority":"diagnostic-only-unvalidated-partial-arrangement","blocks":search.placed.iter().map(Placed::record).collect::<Vec<_>>()}});
    if !search.access_rejections.is_empty() {
        record["search"]["access_rejections"] = json!(search.access_rejections);
    }
    let steps = search.steps
        + record["residual_budget"]["steps_used"]
            .as_u64()
            .unwrap_or(0);
    failure(capture, policy, &record, status, &reason, steps)
}

pub fn plan(
    capture: &Value,
    section: &Value,
    captured_policy: &Value,
    effective_policy: &Value,
    netlist_raw: &[u8],
) -> Result<Value> {
    blocks::keys(
        section,
        &["arrangement", "candidates"],
        &["allocation_text", "side_eligibility", "contract"],
    )?;
    block_contract::validate(capture, section, effective_policy)?;
    let inventory = netlist::parse(netlist_raw)?;
    let allocation = section
        .get("allocation_text")
        .filter(|v| !v.is_null())
        .map(|v| v.as_str().context("allocation_text must be text"))
        .transpose()?;
    let derivation = blocks::derive(&inventory, capture, allocation)?;
    arrange(
        capture,
        &inventory,
        &derivation,
        captured_policy,
        effective_policy,
        &section["arrangement"],
        &section["candidates"],
        section.get("side_eligibility"),
        section.get("contract"),
    )
}
pub fn arrange(
    capture: &Value,
    inventory: &netlist::NativeInventory,
    derivation: &Value,
    captured: &Value,
    effective: &Value,
    document: &Value,
    entries: &Value,
    eligibility: Option<&Value>,
    contract: Option<&Value>,
) -> Result<Value> {
    let refs: BTreeSet<_> = blocks::board(capture)?.into_keys().collect();
    policy::validate(captured, &refs)?;
    policy::validate(effective, &refs)?;
    check_effective(captured, effective)?;
    ensure!(
        derivation["board_sha256"] == capture["board_sha256"],
        "derivation belongs to another board capture"
    );
    let block_ids: Vec<String> = blocks::rows(&derivation["blocks"])?
        .iter()
        .map(|b| blocks::text(&b["id"]).map(str::to_owned))
        .collect::<Result<_>>()?;
    let arrangement = validate(document, &block_ids.iter().cloned().collect(), &refs)?;
    let mut record = json!({"schema":SCHEMA,"strategy":STRATEGY,"arrangement":arrangement,"derivation_sha256":blocks::digest(derivation),"policy_sha256":blocks::digest(captured)});
    let mut admitted = BTreeMap::<String, Value>::new();
    let mut problems = Vec::new();
    for (i, entry) in blocks::rows(entries)?.iter().enumerate() {
        let a =
            block_variants::admit(entry, capture, inventory, derivation, captured, eligibility)?;
        if a["status"] == "refused" {
            problems.push(json!({"candidate":i,"block":entry["candidate"]["block_id"],"problems":a["problems"]}));
        } else {
            let b = blocks::text(&a["block"])?.to_owned();
            if admitted.contains_key(&b) {
                problems
                    .push(json!({"candidate":i,"block":b,"problems":[{"kind":"duplicate_block"}]}));
            } else {
                admitted.insert(b, a);
            }
        }
    }
    let missing: Vec<_> = block_ids
        .iter()
        .filter(|b| !admitted.contains_key(*b))
        .cloned()
        .collect();
    if !missing.is_empty() && problems.is_empty() {
        problems.push(json!({"kind":"block_candidate_missing","blocks":missing}));
    }
    if !problems.is_empty() {
        record["problems"] = json!(problems);
        return failure(
            capture,
            effective,
            &record,
            "arrangement_refused",
            "block candidates not admitted for arrangement",
            0,
        );
    }
    record["blocks"]=json!(block_ids.iter().map(|b|{let a=&admitted[b];let mut row=json!({"block":b,"provenance":a["provenance"],"candidate_sha256":a["candidate_sha256"],"anchor":a["anchor"],"references":blocks::natural(a["poses"].as_object().unwrap().keys().cloned().collect()),"boundary":a["binding"],"deferred":a["deferred"]});if a["mapped"]==true{row["mapping"]=a["mapping"].clone();}row}).collect::<Vec<_>>());
    if let Some(local) = effective.get("electrical_locality") {
        if !locality::grade(capture, local)?["unknown"]
            .as_array()
            .unwrap()
            .is_empty()
        {
            return failure(
                capture,
                effective,
                &record,
                "electrical_locality_unavailable",
                "pad locality needs unambiguous native pads, limits and citations",
                0,
            );
        }
    }
    let qualified = match footprint::qualify_capture(capture) {
        Ok(q) => q,
        Err(e) => {
            return failure(
                capture,
                effective,
                &record,
                "unsupported_geometry",
                &e.to_string(),
                0,
            );
        }
    };
    for fp in &qualified.footprints {
        if constraints::access_hard(fp)? {
            if let Err(e) = constraints::access_geometry(fp) {
                return failure(
                    capture,
                    effective,
                    &record,
                    "unsupported_geometry",
                    &e.to_string(),
                    0,
                );
            }
        }
    }
    let by: BTreeMap<_, _> = qualified
        .footprints
        .iter()
        .map(|f| (f.reference.clone(), f.clone()))
        .collect();
    let fixed: BTreeSet<_> = blocks::refs(&effective["fixed_refs"])?
        .into_iter()
        .chain(
            qualified
                .footprints
                .iter()
                .filter(|f| f.raw["locked"] == true)
                .map(|f| f.reference.clone()),
        )
        .collect();
    let members: BTreeSet<String> = admitted
        .values()
        .flat_map(|a| a["poses"].as_object().unwrap().keys().cloned())
        .collect();
    let movable: BTreeSet<_> = refs
        .difference(&fixed)
        .filter(|r| !members.contains(*r))
        .cloned()
        .collect();
    let mut stranded = Vec::new();
    if let Some(gs) = effective.get("groups") {
        for g in blocks::rows(&groups::validate(gs, None)?)? {
            if g["anchor"]["reference"]
                .as_str()
                .is_some_and(|r| movable.contains(r))
                && blocks::refs(&g["references"])?
                    .iter()
                    .any(|r| members.contains(r))
            {
                stranded.push(g["id"].clone());
            }
        }
    }
    stranded.sort_by(|a, b| a.as_str().cmp(&b.as_str()));
    if !stranded.is_empty() {
        record["problems"] = json!([{"kind":"group_anchor_residual","groups":stranded}]);
        return failure(
            capture,
            effective,
            &record,
            "arrangement_refused",
            "block members anchored to a residual movable part",
            0,
        );
    }
    let mut obstacles = blocks::rows(&effective["obstacles"])?.clone();
    for k in arrangement
        .get("keepouts")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter(|k| hard(k))
    {
        let shape = Keepout {
            side: blocks::text(&k["side"])?.into(),
            polygons: policy::obstacle_polygons(&k["polygons"])?,
            name: String::new(),
        };
        obstacles.push(obstacle(
            k,
            &shape,
            format!("block-arrangement keep-out {}", blocks::text(&k["id"])?),
        ));
    }
    let stacking = &arrangement["no_stacking"];
    if hard(stacking) {
        let margin = exact::integer(&stacking["margin_nm"])?;
        let rs = blocks::refs(&stacking["references"])?;
        for r in &rs {
            if fixed.contains(r) {
                obstacles.push(obstacle(
                    stacking,
                    &shadow(&by[r], &margin),
                    format!("block-arrangement shadow {r}"),
                ));
            }
        }
        for (b, a) in &admitted {
            // Each native alternative is checked at candidate evaluation. A bad
            // first alternative must not discard a valid face of the same block.
            if a.get("variants").is_some() {
                continue;
            }
            let frames = a["poses"]
                .as_object()
                .unwrap()
                .iter()
                .map(|(r, p)| {
                    by[r].at(
                        &exact::point(&p["position_nm"])?,
                        exact::rotation(&p["rotation_deg"])?,
                    )
                })
                .collect::<Result<Vec<_>>>()?;
            for fp in &frames {
                if !rs.contains(&fp.reference) {
                    continue;
                }
                let shape = shadow(fp, &margin);
                let inside = blocks::natural(
                    frames
                        .iter()
                        .filter(|f| {
                            f.reference != fp.reference
                                && constraints::near(
                                    f,
                                    std::slice::from_ref(&shape),
                                    &exact::integer(&effective["gap_nm"]).unwrap(),
                                )
                        })
                        .map(|f| f.reference.clone())
                        .collect(),
                );
                if !inside.is_empty() {
                    record["problems"] = json!([{"kind":"block_internal_stacking","block":b,"reference":fp.reference,"inside":inside}]);
                    return failure(
                        capture,
                        effective,
                        &record,
                        "arrangement_refused",
                        &format!("block {b} stacks parts in the shadow of {}", fp.reference),
                        0,
                    );
                }
            }
        }
    }
    let mut reservation_policy = effective.clone();
    reservation_policy["obstacles"] = json!(obstacles);
    let prepared = match packing::prepare(capture, &reservation_policy, None) {
        Ok(p) => p,
        Err(e) => {
            return failure(
                capture,
                effective,
                &record,
                "unsupported_geometry",
                &e.to_string(),
                0,
            );
        }
    };
    if let Some(carried) = &prepared.carried {
        record["carried_fixed_pairs"] = json!(carried);
    }
    if let Some(reason) = &prepared.conflict {
        return failure(capture, effective, &record, "fixed_conflict", reason, 0);
    }
    let (refusal, exempt) = constraints::fixed_input_copper(
        &prepared.placed,
        &prepared.capture.outline,
        &prepared.capture.board_keepouts,
        &prepared.capture.board_pad_keepouts,
        effective.get("fixed_overhangs").unwrap_or(&json!([])),
    )?;
    if let Some(refusal) = refusal {
        let mut out = failure(
            capture,
            effective,
            &record,
            if refusal["outcome"] == "defect" {
                "fixed_input_copper"
            } else {
                "unsupported_geometry"
            },
            blocks::text(&refusal["reason"])?,
            0,
        )?;
        out["outcome"] = refusal["outcome"].clone();
        out["findings"] = refusal["findings"].clone();
        return Ok(out);
    }
    if !exempt.is_empty() {
        record["fixed_overhang_copper"] = json!(exempt);
    }
    if let Some(reason) = constraints::copper_setback_missing(
        &prepared
            .capture
            .footprints
            .iter()
            .filter(|f| !fixed.contains(&f.reference))
            .cloned()
            .collect::<Vec<_>>(),
    )? {
        return failure(
            capture,
            effective,
            &record,
            "unsupported_geometry",
            &reason,
            0,
        );
    }
    let mut search = Search::new(&prepared, effective, &arrangement, &admitted)?;
    let through: BTreeSet<_> = admitted
        .iter()
        .filter(|(_, a)| {
            a["poses"]
                .as_object()
                .unwrap()
                .keys()
                .any(|r| !by[r].holes.is_empty() || search.stacking.contains(r))
        })
        .map(|(b, _)| b.clone())
        .collect();
    let order = order(
        &admitted,
        &by,
        &fixed,
        &arrangement["priority_blocks"],
        &search.grounds,
        &through,
    )?;
    record["order"] = json!(order);
    record["inter_block_nets"] = net_counts(&admitted, &by, &fixed, &search.grounds);
    record["objective"] = arrangement["objective"].clone();
    progress::emit(json!({"status":"arranging","steps_used":0,"blocks_placed":0}))?;
    struct Level {
        block: String,
        conflicts: BTreeSet<String>,
        candidates: std::vec::IntoIter<Ranked>,
    }
    let mut levels = Vec::<Level>::new();
    let (mut backjumps, mut peak) = (0u64, 0usize);
    let has_variants = admitted.values().any(|a| a.get("variants").is_some());
    let residual_budget = effective["step_budget"]
        .as_u64()
        .context("residual step budget")?;
    let mut residual_used = 0u64;
    let mut previous_failure: Option<(Value, Value, &str)> = None;
    if has_variants {
        record["adjustments"] = json!([]);
        record["residual_failures"] = json!([]);
    }
    loop {
        while search.placed.len() < order.len() {
            let i = search.placed.len();
            search.current = Some(order[i].clone());
            if levels.len() == i {
                let mut conflicts = BTreeSet::new();
                let ranked = match search.ranked(&order[i], &mut conflicts) {
                    Ok(r) => r,
                    Err(e) => {
                        if let Some(stop) = e.downcast_ref::<Stopped>() {
                            return stopped(
                                capture, effective, &record, &search, stop.0, stop.1, &order[i],
                                backjumps, peak,
                            );
                        }
                        if e.is::<locality::TargetBudgetExhausted>() {
                            return stopped(
                                capture,
                                effective,
                                &record,
                                &search,
                                "arrangement_budget_exhausted",
                                "locality target budget exhausted",
                                &order[i],
                                backjumps,
                                peak,
                            );
                        }
                        return Err(e);
                    }
                };
                levels.push(Level {
                    block: order[i].clone(),
                    conflicts,
                    candidates: ranked.into_iter(),
                });
            }
            let level = &mut levels[i];
            let candidate =
                match search.candidate(&level.block, &mut level.candidates, &mut level.conflicts) {
                    Ok(c) => c,
                    Err(e) => {
                        if let Some(stop) = e.downcast_ref::<Stopped>() {
                            return stopped(
                                capture, effective, &record, &search, stop.0, stop.1, &order[i],
                                backjumps, peak,
                            );
                        }
                        return Err(e);
                    }
                };
            if let Some((p, r, variant)) = candidate {
                search.push(&level.block, variant, p, r)?;
                peak = peak.max(search.placed.len());
                continue;
            }
            let culprit = search
                .placed
                .iter()
                .enumerate()
                .rev()
                .find(|(_, p)| level.conflicts.contains(&p.block))
                .map(|(i, _)| i);
            let Some(j) = culprit else {
                return stopped(
                    capture,
                    effective,
                    &record,
                    &search,
                    "arrangement_search_exhausted",
                    &format!(
                        "no candidate for block {} in the finite search; infeasibility unproved",
                        level.block
                    ),
                    &level.block,
                    backjumps,
                    peak,
                );
            };
            if backjumps == search.budget {
                return stopped(
                    capture,
                    effective,
                    &record,
                    &search,
                    "arrangement_budget_exhausted",
                    "deterministic backjump budget exhausted",
                    &level.block,
                    backjumps,
                    peak,
                );
            }
            backjumps += 1;
            search.backjumps = backjumps;
            let conflicts = level.conflicts.clone();
            while search.placed.len() > j {
                search.placed.pop();
            }
            let parent = levels[j].block.clone();
            levels[j]
                .conflicts
                .extend(conflicts.into_iter().filter(|b| b != &parent));
            levels.truncate(j + 1);
        }
        record["search"] = json!({"strategy":STRATEGY,"steps_used":search.steps,"step_budget":search.budget,"backjumps":backjumps,"rejected":search.rejected,"elapsed_s":search.started.elapsed().as_secs_f64()});
        record["arranged"] = json!(search.placed.iter().map(Placed::record).collect::<Vec<_>>());
        if has_variants {
            if record.get("first_arranged").is_none() {
                record["first_arranged"] = record["arranged"].clone();
            }
            if let Some((before, status, reason)) = previous_failure.take() {
                let after = record["arranged"].clone();
                record["adjustments"].as_array_mut().unwrap().push(json!({"reason":reason, "before":before,"after":after,"residual_status":status}));
            }
            for row in record["blocks"].as_array_mut().unwrap() {
                let bid = row["block"].as_str().unwrap();
                let a = &admitted[bid];
                if let Some(variants) = a.get("variants").and_then(Value::as_array) {
                    let selected = search.placed.iter().find(|p| p.block == bid).unwrap();
                    let variant = &variants[selected.variant];
                    for key in [
                        "candidate_sha256",
                        "provenance",
                        "anchor",
                        "deferred",
                        "selected_face",
                    ] {
                        row[key] = variant[key].clone();
                    }
                    row["boundary"] = variant["binding"].clone();
                    row["candidate_set_sha256"] = a["candidate_set_sha256"].clone();
                }
            }
        }
        let poses: BTreeMap<_, _> = search
            .placed
            .iter()
            .flat_map(|p| p.members.iter().map(|f| (f.reference.clone(), f.clone())))
            .collect();
        let mut residual_capture = capture.clone();
        for fp in residual_capture["footprints"].as_array_mut().unwrap() {
            if let Some(p) = poses.get(fp["ref"].as_str().unwrap()) {
                *fp = if has_variants {
                    p.to_value()?
                } else {
                    footprint::transform_value(fp, &exact::point_value(&p.position), p.rotation)?
                };
            }
        }
        let source_uuids: BTreeMap<_, _> = poses.values().map(|f| (f.uuid.clone(), f)).collect();
        let mut native_areas = BTreeMap::new();
        for placed in &search.placed {
            if let Some(variants) = admitted[&placed.block]
                .get("variants")
                .and_then(Value::as_array)
            {
                for area in variants[placed.variant]["capture"]["rule_areas"]
                    .as_array()
                    .into_iter()
                    .flatten()
                {
                    if placed.members.iter().any(|f| area["owner"] == f.uuid) {
                        native_areas.insert(area["uuid"].clone().to_string(), area.clone());
                    }
                }
            }
        }
        if let Some(areas) = residual_capture
            .get_mut("rule_areas")
            .and_then(Value::as_array_mut)
        {
            for area in areas {
                if let Some(native) = native_areas.get(&area["uuid"].clone().to_string()) {
                    *area = native.clone();
                }
                if let Some(f) = area["owner"].as_str().and_then(|u| source_uuids.get(u)) {
                    let pose = &poses[&f.reference];
                    let placeholder = json!({"position_nm":f.raw["position_nm"],"rotation_deg":f.raw["rotation_deg"],"pads":[],"courtyards":{"zone":area["outline"]}});
                    match footprint::transform_value(
                        &placeholder,
                        &exact::point_value(&pose.position),
                        pose.rotation,
                    ) {
                        Ok(p) => area["outline"] = p["courtyards"]["zone"].clone(),
                        Err(_) => {
                            return failure(
                                capture,
                                effective,
                                &record,
                                "unsupported_geometry",
                                "an arranged member rule area cannot be moved to its pose",
                                0,
                            );
                        }
                    }
                }
            }
        }
        let mut residual_policy = effective.clone();
        let mut residual_obstacles = obstacles.clone();
        if hard(stacking) {
            let margin = exact::integer(&stacking["margin_nm"])?;
            for r in blocks::refs(&stacking["references"])? {
                if let Some(p) = poses.get(&r) {
                    residual_obstacles.push(obstacle(
                        stacking,
                        &shadow(p, &margin),
                        format!("block-arrangement shadow {r}"),
                    ));
                }
            }
        }
        residual_policy["obstacles"] = json!(residual_obstacles);
        residual_policy["fixed_refs"] = json!(blocks::natural(
            blocks::refs(&effective["fixed_refs"])?
                .into_iter()
                .chain(members.iter().cloned())
                .collect::<BTreeSet<_>>()
                .into_iter()
                .collect()
        ));
        if has_variants {
            residual_policy["step_budget"] = json!(residual_budget - residual_used);
        }
        record["residual"] = json!({"references":blocks::natural(movable.iter().cloned().collect()),"policy_sha256":blocks::digest(&residual_policy),"obstacles":residual_obstacles[blocks::rows(&effective["obstacles"])?.len()..]});
        let mut plan =
            packing::pack_arranged(&residual_capture, &residual_policy, Some(capture), &poses)?;
        if has_variants {
            residual_used += plan["steps_used"]
                .as_u64()
                .unwrap_or(0)
                .max(if plan["status"] == "planned" { 0 } else { 1 });
            record["residual_budget"] =
                json!({"step_budget":residual_budget,"steps_used":residual_used});
        }
        if plan["status"] != "planned" {
            let retryable = matches!(
                plan["status"].as_str(),
                Some(
                    "packing_search_exhausted"
                        | "packing_budget_exhausted"
                        | "locality_cluster_exhausted"
                        | "packing_infeasible"
                        | "fixed_conflict"
                )
            );
            if has_variants && retryable && !search.placed.is_empty() {
                let mut attempt = json!({"arranged":record["arranged"],"status":plan["status"],"reason":plan["reason"],"steps_used":plan["steps_used"]});
                for key in ["proof", "binding_constraints", "search_failure"] {
                    if let Some(value) = plan.get(key) {
                        attempt[key] = value.clone();
                    }
                }
                record["residual_failures"]
                    .as_array_mut()
                    .unwrap()
                    .push(attempt);
                if residual_used >= residual_budget {
                    return failure(
                        capture,
                        effective,
                        &record,
                        "arrangement_budget_exhausted",
                        "residual candidate budget exhausted across block adjustments",
                        search.steps + residual_used,
                    );
                }
                previous_failure = Some((
                    record["arranged"].clone(),
                    plan["status"].clone(),
                    "residual_packing_failed",
                ));
                let last = search.placed.len() - 1;
                levels[last]
                    .conflicts
                    .extend(search.placed[..last].iter().map(|p| p.block.clone()));
                search.placed.pop();
                continue;
            }
            plan["block_arrangement"] = record;
            return Ok(plan);
        }
        for p in plan["placements"].as_array_mut().unwrap() {
            p["fixed"] = json!(fixed.contains(p["ref"].as_str().unwrap()));
            if has_variants {
                if let Some(fp) = poses.get(p["ref"].as_str().unwrap()) {
                    p["side"] = json!(fp.side);
                }
            }
        }
        let complete_poses: BTreeMap<_, _> = plan["placements"]
            .as_array()
            .unwrap()
            .iter()
            .map(|p| (p["ref"].as_str().unwrap(), p))
            .collect();
        let mut complete = if has_variants {
            residual_capture.clone()
        } else {
            capture.clone()
        };
        for fp in complete["footprints"].as_array_mut().unwrap() {
            let p = complete_poses[fp["ref"].as_str().unwrap()];
            *fp = footprint::transform_value(
                fp,
                &p["position_nm"],
                exact::rotation(&p["rotation_deg"])?,
            )?;
        }
        if let Some(contract) = contract {
            record["contract_check"] = block_contract::check_complete(&complete, contract)?;
            if record["contract_check"]["status"] == "unknown" {
                return failure(
                    capture,
                    effective,
                    &record,
                    "block_contract_unavailable",
                    "complete plan has unresolved required block constraints",
                    search.steps + residual_used,
                );
            }
            if record["contract_check"]["status"] != "pass" {
                if !has_variants || search.placed.is_empty() {
                    return failure(
                        capture,
                        effective,
                        &record,
                        "judge_rejected",
                        "complete plan failed block constraints",
                        search.steps + residual_used,
                    );
                }
                if plan["steps_used"] == 0 {
                    residual_used += 1;
                }
                record["residual_budget"]["steps_used"] = json!(residual_used);
                if record.get("contract_failures").is_none() {
                    record["contract_failures"] = json!([]);
                }
                let failed =
                    json!({"arranged":record["arranged"],"check":record["contract_check"]});
                record["contract_failures"]
                    .as_array_mut()
                    .unwrap()
                    .push(failed);
                if residual_used >= residual_budget {
                    return failure(
                        capture,
                        effective,
                        &record,
                        "arrangement_budget_exhausted",
                        "residual candidate budget exhausted across block adjustments",
                        search.steps + residual_used,
                    );
                }
                previous_failure = Some((
                    record["arranged"].clone(),
                    json!("block_contract_rejected"),
                    "block_contract_failed",
                ));
                let last = search.placed.len() - 1;
                levels[last]
                    .conflicts
                    .extend(search.placed[..last].iter().map(|p| p.block.clone()));
                search.placed.pop();
                continue;
            }
        }
        record["rules"] = grade_rules(&complete, &arrangement, &search.owner, &fixed)?;
        if let Some(regions) = effective.get("advisory_regions") {
            plan["advisory_regions"] =
                crate::grading::advisory_regions(&complete, regions, &fixed)?;
        }
        record["deferred_grading"] = deferred_grading(&plan, &admitted);
        record["residual"]["steps_used"] = plan["steps_used"].clone();
        let steps = if has_variants {
            residual_used
        } else {
            plan["steps_used"].as_u64().unwrap()
        } + search.steps;
        plan["steps_used"] = json!(steps);
        plan["policy"] = effective.clone();
        let failed = blocks::rows(&record["rules"])?
            .iter()
            .any(|r| r["enforcement"] == "hard" && r["status"] != "pass")
            || blocks::rows(&record["deferred_grading"])?
                .iter()
                .any(|r| r["status"] != "pass" && r["status"] != "info");
        if failed {
            return failure(
                capture,
                effective,
                &record,
                "judge_rejected",
                "complete plan failed a hard block rule or a deferred obligation",
                steps,
            );
        }
        plan["block_arrangement"] = record;
        return Ok(plan);
    }
}
