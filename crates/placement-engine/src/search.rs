use std::cmp::Reverse;
use std::collections::{BTreeMap, BTreeSet, BinaryHeap};
use std::time::{Duration, Instant};

use anyhow::{Context, Result};
use num_traits::{Signed, Zero};
use serde_json::Value;

use crate::constraints::{self, AdmissionContext};
use crate::exact::{Int, Point, Rat, integer, point};
use crate::footprint::{Keepout, PreparedFootprint, QualifiedCapture};
use crate::geometry::{Bounds, Polygon, area2, intersection_area, qualified_polygons};
use crate::locality::{CandidateLocality, PlacementIndex};
use crate::rank;

#[derive(Debug)]
pub enum Exhausted {
    Candidates,
    Deadline,
    Cluster,
}
impl std::fmt::Display for Exhausted {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(match self {
            Self::Candidates => "deterministic candidate budget exhausted",
            Self::Deadline => "wall-time search budget exhausted",
            Self::Cluster => {
                "joint locality cluster exceeded its work guard; infeasibility unproved"
            }
        })
    }
}
impl std::error::Error for Exhausted {}

#[derive(Clone)]
pub struct Group {
    pub region: Vec<Polygon>,
    pub anchor_ref: Option<String>,
    pub anchor_point: Option<Point>,
    pub limit: Int,
}

pub fn membership(policy: &Value) -> Result<BTreeMap<String, Group>> {
    let mut members = BTreeMap::new();
    if let Some(groups) = policy.get("groups") {
        for group in groups.as_array().context("groups must be an array")? {
            let region = match group.get("region") {
                Some(region) => qualified_polygons(
                    &serde_json::json!({"status":"proven", "polygons":region["polygons"]}),
                )?,
                None => Vec::new(),
            };
            let parsed = Group {
                region,
                anchor_ref: group["anchor"]["reference"].as_str().map(str::to_owned),
                anchor_point: group["anchor"].get("point_nm").map(point).transpose()?,
                limit: group
                    .get("max_distance_nm")
                    .map(integer)
                    .transpose()?
                    .unwrap_or_default(),
            };
            for reference in group["references"]
                .as_array()
                .context("missing group references")?
            {
                members.insert(
                    reference
                        .as_str()
                        .context("invalid group reference")?
                        .into(),
                    parsed.clone(),
                );
            }
        }
    }
    Ok(members)
}

pub fn box_union<'a>(boxes: impl IntoIterator<Item = &'a Bounds>) -> Bounds {
    let mut boxes = boxes.into_iter();
    let first = boxes.next().expect("qualified footprint has a courtyard");
    let mut out = first.clone();
    for b in boxes {
        out.x0 = out.x0.min(b.x0.clone());
        out.y0 = out.y0.min(b.y0.clone());
        out.x1 = out.x1.max(b.x1.clone());
        out.y1 = out.y1.max(b.y1.clone());
    }
    out
}

pub fn polygon_box(polygons: &[Polygon]) -> Bounds {
    crate::geometry::bounds(polygons).expect("qualified polygon has vertices")
}

#[derive(Clone)]
struct Tie {
    reference: Option<String>,
    point: Point,
    limit: Int,
}

#[derive(Clone, Eq, PartialEq, Ord, PartialOrd)]
struct Key {
    locality: rank::Fraction,
    wire: rank::Integer,
    x: rank::Integer,
    y: rank::Integer,
    rotation: usize,
    doomed: bool,
}

pub struct Scan {
    heap: BinaryHeap<Reverse<(Key, bool)>>,
    origins: Vec<PreparedFootprint>,
    signal: Vec<Vec<(String, Point)>>,
    locality: Vec<Option<CandidateLocality>>,
    group: Option<Group>,
    ties: Vec<Tie>,
}

pub struct Search {
    pub capture: QualifiedCapture,
    pub fixed: BTreeSet<String>,
    pub exact: Vec<Keepout>,
    pub placed: Vec<PreparedFootprint>,
    pub best: Vec<PreparedFootprint>,
    pub steps: u64,
    pub budget: u64,
    pub deadline: Option<Instant>,
    pub members: BTreeMap<String, Group>,
    pub copper_axes: bool,
    gap: Int,
    margin: Int,
    grounds: BTreeSet<String>,
    reserved: Vec<(String, Bounds, String)>,
    solid: Vec<(String, Bounds)>,
    origins: BTreeMap<(String, i64), PreparedFootprint>,
    positions: BTreeMap<String, Point>,
    peers: BTreeMap<String, Vec<Point>>,
    locality: Option<PlacementIndex>,
    mechanical: Option<Value>,
    cluster_start: Option<(u64, u64)>,
    cluster_spent: BTreeMap<Vec<String>, u64>,
    progress: crate::progress::Clock,
}

impl Search {
    pub fn new(
        capture: QualifiedCapture,
        policy: &Value,
        fixed: BTreeSet<String>,
        exact: Vec<Keepout>,
        placed: Vec<PreparedFootprint>,
    ) -> Result<Self> {
        let solid = exact
            .iter()
            .flat_map(|o| {
                o.polygons.iter().flat_map(move |p| {
                    crate::geometry::slabs(p)
                        .unwrap_or_default()
                        .into_iter()
                        .map(|b| (o.side.clone(), b))
                })
            })
            .collect();
        let deadline = policy
            .get("search_timeout_s")
            .map(|seconds| {
                let seconds = seconds.as_f64().context("invalid search timeout")?;
                Ok::<_, anyhow::Error>(Instant::now() + Duration::from_secs_f64(seconds))
            })
            .transpose()?;
        let mut search = Self {
            mechanical: policy.get("mechanical_reservations").cloned(),
            capture,
            fixed,
            exact,
            placed: Vec::new(),
            best: Vec::new(),
            steps: 0,
            budget: policy["step_budget"]
                .as_u64()
                .context("missing step budget")?,
            deadline,
            members: membership(policy)?,
            copper_axes: false,
            gap: integer(&policy["gap_nm"])?,
            margin: crate::policy::edge_margin_nm(policy)?,
            grounds: policy["ground_nets"]
                .as_array()
                .context("missing ground nets")?
                .iter()
                .map(|v| v.as_str().context("invalid ground net").map(str::to_owned))
                .collect::<Result<_>>()?,
            reserved: Vec::new(),
            solid,
            origins: BTreeMap::new(),
            positions: BTreeMap::new(),
            peers: BTreeMap::new(),
            locality: policy
                .get("electrical_locality")
                .map(PlacementIndex::new)
                .transpose()?,
            cluster_start: None,
            cluster_spent: BTreeMap::new(),
            progress: crate::progress::Clock::new(),
        };
        for fp in placed {
            search.push(fp)?;
        }
        Ok(search)
    }

    fn checkpoint(&self) -> Result<()> {
        if let Some(elapsed) = self.progress.elapsed_if_due() {
            crate::progress::emit(
                serde_json::json!({"status":"packing", "steps_used":self.steps,
                "placed_count":self.placed.len(), "placed_peak":self.best.len(), "elapsed_s":elapsed}),
            )?;
        }
        if self.deadline.is_some_and(|d| Instant::now() >= d) {
            return Err(Exhausted::Deadline.into());
        }
        if self
            .cluster_start
            .is_some_and(|(start, spent)| self.steps - start + spent >= 4096)
        {
            return Err(Exhausted::Cluster.into());
        }
        Ok(())
    }

    pub fn push(&mut self, fp: PreparedFootprint) -> Result<()> {
        if let Some(index) = self.locality.as_mut() {
            index.push(&fp.to_value()?)?;
        }
        self.positions
            .insert(fp.reference.clone(), fp.position.clone());
        if !self.fixed.contains(&fp.reference) {
            self.reserved.extend(
                fp.boxes
                    .iter()
                    .map(|(side, b)| (side.clone(), b.clone(), fp.reference.clone())),
            );
        }
        for pad in &fp.pads {
            if !pad.net.is_empty() {
                self.peers
                    .entry(pad.net.clone())
                    .or_default()
                    .push(pad.position.clone());
            }
        }
        self.placed.push(fp);
        if self.placed.len() > self.best.len() {
            self.best.clone_from(&self.placed);
        }
        Ok(())
    }

    pub fn pop(&mut self) {
        let fp = self
            .placed
            .pop()
            .expect("search stack contains pushed footprint");
        if let Some(index) = self.locality.as_mut() {
            index.pop(&fp.reference);
        }
        self.positions.remove(&fp.reference);
        while self
            .reserved
            .last()
            .is_some_and(|(_, _, r)| *r == fp.reference)
        {
            self.reserved.pop();
        }
        for pad in fp.pads.iter().rev().filter(|pad| !pad.net.is_empty()) {
            let peers = self.peers.get_mut(&pad.net).expect("pushed net exists");
            peers.pop();
            if peers.is_empty() {
                self.peers.remove(&pad.net);
            }
        }
    }

    fn ties(&self, reference: &str) -> Vec<Tie> {
        let mut ties = Vec::new();
        if let Some(group) = self.members.get(reference) {
            if let Some(p) = &group.anchor_point {
                ties.push(Tie {
                    reference: None,
                    point: p.clone(),
                    limit: group.limit.clone(),
                });
            } else if let Some(anchor) = &group.anchor_ref {
                if let Some(p) = self.positions.get(anchor) {
                    ties.push(Tie {
                        reference: Some(anchor.clone()),
                        point: p.clone(),
                        limit: group.limit.clone(),
                    });
                } else {
                    for (sibling, other) in &self.members {
                        if sibling != reference && other.anchor_ref.as_ref() == Some(anchor) {
                            if let Some(p) = self.positions.get(sibling) {
                                ties.push(Tie {
                                    reference: Some(sibling.clone()),
                                    point: p.clone(),
                                    limit: &group.limit + &other.limit,
                                });
                            }
                        }
                    }
                }
            }
        }
        for (member, group) in &self.members {
            if group.anchor_ref.as_deref() == Some(reference) {
                if let Some(p) = self.positions.get(member) {
                    ties.push(Tie {
                        reference: Some(member.clone()),
                        point: p.clone(),
                        limit: group.limit.clone(),
                    });
                }
            }
        }
        ties
    }

    pub fn scan(&mut self, reference: &str, conflicts: &mut BTreeSet<String>) -> Result<Scan> {
        self.checkpoint()?;
        let group = self.members.get(reference).cloned();
        let ties = self.ties(reference);
        let mut anchors: Vec<Bounds> = self.reserved.iter().map(|(_, b, _)| b.clone()).collect();
        anchors.extend(self.exact.iter().map(|o| polygon_box(&o.polygons)));
        anchors.extend(self.solid.iter().map(|(_, b)| b.clone()));
        anchors.extend(
            self.capture
                .board_keepouts
                .iter()
                .map(|o| polygon_box(&o.polygons)),
        );
        anchors.extend(
            self.capture
                .board_pad_keepouts
                .iter()
                .map(|o| polygon_box(&o.polygons)),
        );
        for fp in &self.placed {
            anchors.extend(fp.keepouts.iter().map(|o| polygon_box(&o.polygons)));
            anchors.extend(fp.pad_keepouts.iter().map(|o| polygon_box(&o.polygons)));
        }
        let mut targets: Vec<Point> = group
            .as_ref()
            .map(|g| g.region.iter().flatten().cloned().collect())
            .unwrap_or_default();
        targets.extend(ties.iter().map(|t| t.point.clone()));
        let mut window = polygon_box(std::slice::from_ref(&self.capture.outline));
        window.x0 += &self.margin;
        window.y0 += &self.margin;
        window.x1 -= &self.margin;
        window.y1 -= &self.margin;
        if let Some(g) = &group {
            if !g.region.is_empty() {
                let b = polygon_box(&g.region);
                window.x0 = window.x0.max(b.x0);
                window.y0 = window.y0.max(b.y0);
                window.x1 = window.x1.min(b.x1);
                window.y1 = window.y1.min(b.y1);
            }
        }
        let mut scan = Scan {
            heap: BinaryHeap::new(),
            origins: Vec::new(),
            signal: Vec::new(),
            locality: Vec::new(),
            group,
            ties,
        };
        let mut candidates = Vec::new();
        for rotation in 0..4 {
            let degrees = rotation as i64 * 90;
            let cache_key = (reference.to_owned(), degrees);
            if !self.origins.contains_key(&cache_key) {
                let source = self
                    .capture
                    .footprints
                    .iter()
                    .find(|f| f.reference == reference)
                    .context("unknown search reference")?;
                self.origins
                    .insert(cache_key.clone(), source.at(&Point::new(0, 0), degrees)?);
            }
            let origin = self.origins[&cache_key].clone();
            let evaluator = match &self.locality {
                Some(index) if index.contains(reference) => {
                    Some(index.prepare(&origin.to_value()?)?)
                }
                _ => None,
            };
            let mut rotation_targets = targets.clone();
            if let Some(e) = &evaluator {
                rotation_targets.extend(e.targets()?);
            }
            let (mut xs, mut ys) = axes(
                &origin,
                &self.capture.outline,
                &anchors,
                &self.gap,
                &self.margin,
                &rotation_targets,
                Some(&window),
                &scan.ties,
                self.copper_axes,
            )?;
            if self.cluster_start.is_some() {
                if let Some(e) = &evaluator {
                    for pair in &e.pairs {
                        if !pair.hard {
                            continue;
                        }
                        let limit = Rat::from_integer(pair.limit.clone());
                        xs.retain(|x| {
                            (Rat::from_integer(x.clone()) + &pair.offset[0] - &pair.target[0]).abs()
                                <= limit
                        });
                        ys.retain(|y| {
                            (Rat::from_integer(y.clone()) + &pair.offset[1] - &pair.target[1]).abs()
                                <= limit
                        });
                        conflicts.extend(
                            pair.involved
                                .iter()
                                .filter(|r| !self.fixed.contains(*r) && r.as_str() != reference)
                                .cloned(),
                        );
                    }
                }
            }
            let signal: Vec<_> = origin
                .pads
                .iter()
                .filter(|pad| !pad.net.is_empty() && !self.grounds.contains(&pad.net))
                .map(|pad| (pad.net.clone(), pad.position.clone()))
                .collect();
            let (bx, by) = wire_bounds(&signal, &self.peers, &xs, &ys);
            for (xi, x) in xs.iter().enumerate() {
                self.checkpoint()?;
                let (blocked, doomed) = unreserved(
                    &origin,
                    x,
                    &ys,
                    &self.reserved,
                    &self.solid,
                    &self.gap,
                    conflicts,
                );
                for (yi, y) in ys.iter().enumerate() {
                    if blocked[yi] {
                        continue;
                    }
                    self.checkpoint()?;
                    let (hit, locality) = if let Some(e) = &evaluator {
                        let (hit, cost) = e.constraint(&Point {
                            x: x.clone(),
                            y: y.clone(),
                        });
                        (hit, rank::Fraction::from(cost))
                    } else {
                        (BTreeSet::new(), rank::Fraction::Integer(0))
                    };
                    if self.cluster_start.is_some() && !hit.is_empty() {
                        conflicts.extend(
                            hit.into_iter()
                                .filter(|r| !self.fixed.contains(r) && r != reference),
                        );
                        continue;
                    }
                    candidates.push(Reverse((
                        Key {
                            locality,
                            wire: rank::Integer::sum(&bx[xi], &by[yi]),
                            x: x.into(),
                            y: y.into(),
                            rotation,
                            doomed: doomed[yi],
                        },
                        false,
                    )));
                }
            }
            scan.origins.push(origin);
            scan.signal.push(signal);
            scan.locality.push(evaluator);
        }
        scan.heap = BinaryHeap::from(candidates);
        Ok(scan)
    }

    pub fn next(
        &mut self,
        scan: &mut Scan,
        conflicts: &mut BTreeSet<String>,
    ) -> Result<Option<PreparedFootprint>> {
        while let Some(Reverse((mut key, resolved))) = scan.heap.pop() {
            self.checkpoint()?;
            if !resolved {
                key.wire = wire_cost(
                    &scan.signal[key.rotation],
                    &self.peers,
                    &key.x.exact(),
                    &key.y.exact(),
                )
                .into();
                scan.heap.push(Reverse((key, true)));
                continue;
            }
            if self.steps == self.budget {
                return Err(Exhausted::Candidates.into());
            }
            self.steps += 1;
            if key.doomed {
                continue;
            }
            let position = Point {
                x: key.x.exact(),
                y: key.y.exact(),
            };
            let proposed = scan.origins[key.rotation].at(&position, key.rotation as i64 * 90)?;
            let context = AdmissionContext {
                outline: &self.capture.outline,
                margin: &self.margin,
                gap: &self.gap,
                fixed_exact: &self.exact,
                board_keepouts: &self.capture.board_keepouts,
                board_pad_keepouts: &self.capture.board_pad_keepouts,
                placed: &self.placed,
                fixed: &self.fixed,
            };
            if let Some(blockage) = constraints::blockers(&proposed, &context)? {
                conflicts.extend(blockage.blockers);
                continue;
            }
            if let Some(binding) = &self.mechanical {
                let mut current = self.capture.raw.clone();
                let mut members = self.placed.iter().map(PreparedFootprint::to_value).collect::<Result<Vec<_>>>()?;
                members.push(proposed.to_value()?);
                current["footprints"] = Value::Array(members);
                if crate::mechanical::grade(&current, binding, true)?["status"] != "pass" {
                    conflicts.extend(self.placed.iter().filter(|fp| !self.fixed.contains(&fp.reference)).map(|fp| fp.reference.clone()));
                    continue;
                }
            }
            if let Some(group) = &scan.group {
                if !group.region.is_empty()
                    && proposed.courtyards.values().flatten().any(|p| {
                        group
                            .region
                            .iter()
                            .map(|r| intersection_area(p, r))
                            .sum::<Rat>()
                            != Rat::new(area2(p).abs(), Int::from(2))
                    })
                {
                    continue;
                }
            }
            if scan.ties.iter().any(|t| {
                let dx = &position.x - &t.point.x;
                let dy = &position.y - &t.point.y;
                &dx * &dx + &dy * &dy > &t.limit * &t.limit
            }) {
                continue;
            }
            if let Some(evaluator) = &scan.locality[key.rotation] {
                let (hit, _) = evaluator.constraint(&position);
                if !hit.is_empty() {
                    conflicts.extend(
                        hit.into_iter()
                            .filter(|r| !self.fixed.contains(r) && *r != proposed.reference),
                    );
                    continue;
                }
            }
            return Ok(Some(proposed));
        }
        conflicts.extend(
            scan.ties
                .iter()
                .filter_map(|tie| tie.reference.as_ref())
                .filter(|r| !self.fixed.contains(*r))
                .cloned(),
        );
        Ok(None)
    }

    pub fn place_unit(
        &mut self,
        unit: &[String],
    ) -> Result<(Option<Vec<PreparedFootprint>>, BTreeSet<String>)> {
        let mut conflicts = BTreeSet::new();
        let mut scan = self.scan(&unit[0], &mut conflicts)?;
        while let Some(lead) = self.next(&mut scan, &mut conflicts)? {
            let base = self.placed.len();
            let outcome = (|| {
                self.push(lead)?;
                for reference in &unit[1..] {
                    let mut next = self.scan(reference, &mut conflicts)?;
                    match self.next(&mut next, &mut conflicts)? {
                        Some(fp) => self.push(fp)?,
                        None => return Ok(None),
                    }
                }
                Ok::<_, anyhow::Error>(Some(self.placed[base..].to_vec()))
            })();
            while self.placed.len() > base {
                self.pop();
            }
            if let Some(done) = outcome? {
                return Ok((Some(done), BTreeSet::new()));
            }
        }
        conflicts.retain(|r| !unit.contains(r));
        Ok((None, conflicts))
    }

    fn visit_cluster(
        &mut self,
        unit: &[String],
        index: usize,
        conflicts: &mut BTreeSet<String>,
    ) -> Result<Option<Vec<PreparedFootprint>>> {
        if index == unit.len() {
            return Ok(Some(Vec::new()));
        }
        let mut scan = self.scan(&unit[index], conflicts)?;
        while let Some(proposed) = self.next(&mut scan, conflicts)? {
            self.push(proposed.clone())?;
            let outcome = self.visit_cluster(unit, index + 1, conflicts);
            self.pop();
            if let Some(mut tail) = outcome? {
                tail.insert(0, proposed);
                return Ok(Some(tail));
            }
        }
        Ok(None)
    }

    pub fn place_cluster(
        &mut self,
        unit: &[String],
    ) -> Result<(Option<Vec<PreparedFootprint>>, BTreeSet<String>)> {
        if unit.len() > 64 {
            return Err(Exhausted::Cluster.into());
        }
        let already = self.cluster_spent.get(unit).copied().unwrap_or(0);
        // The previous per-cluster work can exceed this pass's step counter.
        let start = self.steps;
        if already >= 4096 {
            return Err(Exhausted::Cluster.into());
        }
        self.cluster_start = Some((self.steps, already));
        let mut conflicts = BTreeSet::new();
        let outcome = self.visit_cluster(unit, 0, &mut conflicts);
        self.cluster_spent
            .insert(unit.to_vec(), already + self.steps - start);
        self.cluster_start = None;
        conflicts.retain(|r| !unit.contains(r));
        Ok((outcome?, conflicts))
    }
}

pub fn candidate_axes(
    origin: &PreparedFootprint,
    outline: &Polygon,
    anchors: &[(String, Bounds)],
    gap: &Int,
    margin: &Int,
    targets: &[Point],
    window: Option<&Bounds>,
) -> Result<(Vec<Int>, Vec<Int>)> {
    axes(
        origin,
        outline,
        &anchors.iter().map(|(_, b)| b.clone()).collect::<Vec<_>>(),
        gap,
        margin,
        targets,
        window,
        &[],
        false,
    )
}

fn axes(
    fp: &PreparedFootprint,
    outline: &Polygon,
    anchors: &[Bounds],
    gap: &Int,
    margin: &Int,
    targets: &[Point],
    window: Option<&Bounds>,
    ties: &[Tie],
    copper_axes: bool,
) -> Result<(Vec<Int>, Vec<Int>)> {
    let whole = box_union(fp.boxes.values());
    let mut boxes = vec![whole.clone()];
    boxes.extend(fp.keepouts.iter().map(|o| polygon_box(&o.polygons)));
    if copper_axes {
        let setback = fp.setback.clone().unwrap_or_default();
        boxes.extend(fp.copper_outline.iter().map(|p| {
            let mut b = polygon_box(std::slice::from_ref(p));
            b.x0 -= &setback;
            b.y0 -= &setback;
            b.x1 += &setback;
            b.y1 += &setback;
            b
        }));
    }
    let mut xs = BTreeSet::new();
    let mut ys = BTreeSet::new();
    for b in boxes {
        for p in outline {
            xs.extend([
                &p.x + margin - &b.x0,
                &p.x - margin - &b.x1,
                &p.x - &b.x0,
                &p.x - &b.x1,
            ]);
            ys.extend([
                &p.y + margin - &b.y0,
                &p.y - margin - &b.y1,
                &p.y - &b.y0,
                &p.y - &b.y1,
            ]);
        }
        for a in anchors {
            xs.extend([
                &a.x0 - gap - &b.x1,
                &a.x1 + gap - &b.x0,
                &a.x0 - &b.x0,
                &a.x1 - &b.x1,
            ]);
            ys.extend([
                &a.y0 - gap - &b.y1,
                &a.y1 + gap - &b.y0,
                &a.y0 - &b.y0,
                &a.y1 - &b.y1,
            ]);
        }
        for p in targets {
            xs.extend([&p.x - &b.x0, &p.x - &b.x1, p.x.clone()]);
            ys.extend([&p.y - &b.y0, &p.y - &b.y1, p.y.clone()]);
        }
    }
    let (access_x, access_y) = constraints::access_axes(fp, outline)?;
    xs.extend(access_x);
    ys.extend(access_y);
    if let Some(window) = window {
        if !constraints::access_overhangs(fp)? {
            xs.retain(|x| window.x0 <= &whole.x0 + x && &whole.x1 + x <= window.x1);
            ys.retain(|y| window.y0 <= &whole.y0 + y && &whole.y1 + y <= window.y1);
        }
    }
    for t in ties {
        xs.retain(|x| &t.point.x - &t.limit <= *x && *x <= &t.point.x + &t.limit);
        ys.retain(|y| &t.point.y - &t.limit <= *y && *y <= &t.point.y + &t.limit);
    }
    Ok((xs.into_iter().collect(), ys.into_iter().collect()))
}

fn unreserved(
    fp: &PreparedFootprint,
    x: &Int,
    ys: &[Int],
    reserved: &[(String, Bounds, String)],
    solid: &[(String, Bounds)],
    gap: &Int,
    conflicts: &mut BTreeSet<String>,
) -> (Vec<bool>, Vec<bool>) {
    let mut blocked = vec![false; ys.len()];
    let mut doomed = vec![false; ys.len()];
    for (side, b) in &fp.boxes {
        for (other, a, reference) in reserved {
            if other != side || &b.x1 + x + gap <= a.x0 || &a.x1 + gap <= &b.x0 + x {
                continue;
            }
            let lo = &a.y0 - gap - &b.y1;
            let hi = &a.y1 + gap - &b.y0;
            let first = ys.partition_point(|y| y <= &lo);
            let last = ys.partition_point(|y| y < &hi);
            if first < last {
                blocked[first..last].fill(true);
                conflicts.insert(reference.clone());
            }
        }
    }
    for (side, slabs) in &fp.slabs {
        for b in slabs {
            for (other, a) in solid {
                if other != side {
                    continue;
                }
                if !(&b.x1 + x <= a.x0 || a.x1 <= &b.x0 + x) {
                    let lo = &a.y0 - &b.y1;
                    let hi = &a.y1 - &b.y0;
                    let first = ys.partition_point(|y| y <= &lo);
                    let last = ys.partition_point(|y| y < &hi);
                    if first < last {
                        blocked[first..last].fill(true);
                    }
                }
                let dx = Int::zero().max(&a.x0 - &b.x1 - x).max(&b.x0 + x - &a.x1);
                if !gap.is_zero() && &dx < gap {
                    let reach = (gap * gap - &dx * &dx - Int::from(1)).sqrt();
                    let lo = &a.y0 - &b.y1 - &reach;
                    let hi = &a.y1 - &b.y0 + &reach;
                    let first = ys.partition_point(|y| y < &lo);
                    let last = ys.partition_point(|y| y <= &hi);
                    if first < last {
                        doomed[first..last].fill(true);
                    }
                }
            }
        }
    }
    (blocked, doomed)
}

fn nearest(coordinate: &Int, sorted: &[Int]) -> Int {
    let at = sorted.partition_point(|p| p < coordinate);
    sorted[at.saturating_sub(1)..(at + 1).min(sorted.len())]
        .iter()
        .map(|p| (coordinate - p).abs())
        .min()
        .expect("net has peers")
}

fn wire_bounds(
    signal: &[(String, Point)],
    peers: &BTreeMap<String, Vec<Point>>,
    xs: &[Int],
    ys: &[Int],
) -> (Vec<Int>, Vec<Int>) {
    let mut xspans = Vec::new();
    let mut yspans = Vec::new();
    for (net, p) in signal {
        if let Some(others) = peers.get(net) {
            xspans.push((
                &p.x,
                others
                    .iter()
                    .map(|p| p.x.clone())
                    .collect::<BTreeSet<_>>()
                    .into_iter()
                    .collect::<Vec<_>>(),
            ));
            yspans.push((
                &p.y,
                others
                    .iter()
                    .map(|p| p.y.clone())
                    .collect::<BTreeSet<_>>()
                    .into_iter()
                    .collect::<Vec<_>>(),
            ));
        }
    }
    (
        xs.iter()
            .map(|x| {
                xspans
                    .iter()
                    .map(|(offset, span)| nearest(&(*offset + x), span))
                    .sum()
            })
            .collect(),
        ys.iter()
            .map(|y| {
                yspans
                    .iter()
                    .map(|(offset, span)| nearest(&(*offset + y), span))
                    .sum()
            })
            .collect(),
    )
}

pub fn wire_cost(
    signal: &[(String, Point)],
    peers: &BTreeMap<String, Vec<Point>>,
    x: &Int,
    y: &Int,
) -> Int {
    signal
        .iter()
        .filter_map(|(net, p)| {
            peers.get(net).map(|others| {
                let px = &p.x + x;
                let py = &p.y + y;
                others
                    .iter()
                    .map(|other| (&px - &other.x).abs() + (&py - &other.y).abs())
                    .min()
                    .expect("net has peers")
            })
        })
        .sum()
}
