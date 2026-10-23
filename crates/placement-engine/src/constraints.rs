//! Exact admission predicates shared by packing, arrangement and saved-plan grading.
use crate::exact::{
    Int, Point, Rat, floor, integer, integer_value, isqrt, point, point_value, rotate, rotation,
};
use crate::footprint::{
    Keepout, Land, PadKeepout, PreparedFootprint, copper_box, copper_layers, layers_meet,
};
use crate::geometry::{
    self, Polygon, area, bounds, edges, intersection_area, on_segment, polygon_value,
    polygons_value, positive_overlap, qualified_polygons, rectangle, segment_distance2,
};
use anyhow::{Context, Result, bail};
use num_traits::{Signed, ToPrimitive, Zero};
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Blockage {
    pub reason: String,
    pub blockers: BTreeSet<String>,
}
pub struct AdmissionContext<'a> {
    pub outline: &'a Polygon,
    pub margin: &'a Int,
    pub gap: &'a Int,
    pub fixed_exact: &'a [Keepout],
    pub board_keepouts: &'a [Keepout],
    pub board_pad_keepouts: &'a [PadKeepout],
    pub placed: &'a [PreparedFootprint],
    pub fixed: &'a BTreeSet<String>,
}
fn blockage(reason: &str, blockers: BTreeSet<String>) -> Option<Blockage> {
    Some(Blockage {
        reason: reason.into(),
        blockers,
    })
}
fn shapes_in(shapes: &BTreeMap<String, Vec<Polygon>>, obstacles: &[Keepout]) -> bool {
    obstacles.iter().any(|o| {
        shapes.get(&o.side).is_some_and(|polys| {
            polys
                .iter()
                .any(|c| o.polygons.iter().any(|p| positive_overlap(c, p)))
        })
    })
}
pub fn in_keepouts(fp: &PreparedFootprint, obstacles: &[Keepout]) -> bool {
    shapes_in(&fp.courtyards, obstacles)
}
pub fn keepout_blocked(
    fp: &PreparedFootprint,
    placed: &[PreparedFootprint],
    obstacles: &[Keepout],
) -> bool {
    in_keepouts(fp, obstacles)
        || placed
            .iter()
            .any(|p| in_keepouts(fp, &p.keepouts) || in_keepouts(p, &fp.keepouts))
}
pub fn near(fp: &PreparedFootprint, obstacles: &[Keepout], gap: &Int) -> bool {
    for o in obstacles {
        let Some(b) = fp.boxes.get(&o.side) else {
            continue;
        };
        let Ok(q) = bounds(&o.polygons) else {
            continue;
        };
        if &b.x1 + gap <= q.x0 || &q.x1 + gap <= b.x0 || &b.y1 + gap <= q.y0 || &q.y1 + gap <= b.y0
        {
            continue;
        }
        for c in &fp.courtyards[&o.side] {
            for p in &o.polygons {
                if positive_overlap(c, p)
                    || (!gap.is_zero()
                        && geometry::separation2(c, p) < Rat::from_integer(gap * gap))
                {
                    return true;
                }
            }
        }
    }
    false
}
pub fn hole_conflict(fp: &PreparedFootprint, peer: &PreparedFootprint) -> bool {
    [(fp, peer), (peer, fp)].into_iter().any(|(owner, other)| {
        owner.geometry_bounds.holes.overlaps(
            &owner.holes,
            &other.geometry_bounds.targets,
            &other.targets,
        )
    })
}
fn polygons_fit(
    polys: &[Polygon],
    outline: &Polygon,
    margin: &Int,
    bands: &[Polygon],
    exit_edges: &[(Point, Point)],
) -> bool {
    let vertex_test = bands.is_empty() && geometry::convex(outline);
    for p in polys {
        if vertex_test {
            if edges(outline).any(|(a, b)| {
                p.iter()
                    .any(|v| geometry::cross_sign(a, b, v) == std::cmp::Ordering::Less)
            }) {
                return false;
            }
        } else if intersection_area(p, outline)
            + bands.iter().map(|r| intersection_area(p, r)).sum::<Rat>()
            != area(p)
        {
            return false;
        }
        if margin.is_positive() {
            let b = bounds(std::slice::from_ref(p)).expect("qualified polygon bounds");
            for (c, d) in edges(outline) {
                if exit_edges
                    .iter()
                    .any(|(a, b)| on_segment(c, d, a) && on_segment(c, d, b))
                {
                    continue;
                }
                if c.x.clone().min(d.x.clone()) - margin >= b.x1
                    || b.x0 >= c.x.clone().max(d.x.clone()) + margin
                    || c.y.clone().min(d.y.clone()) - margin >= b.y1
                    || b.y0 >= c.y.clone().max(d.y.clone()) + margin
                {
                    continue;
                }
                if edges(p).any(|(a, b)| {
                    segment_distance2(a, b, c, d) < Rat::from_integer(margin * margin)
                }) {
                    return false;
                }
            }
        }
    }
    true
}
pub fn fits(
    fp: &PreparedFootprint,
    outline: &Polygon,
    margin: &Int,
    bands: &[Polygon],
) -> Result<bool> {
    let mut own_bands = Vec::new();
    let mut exit_edges = Vec::new();
    if fp.raw.get("access_direction").is_some() {
        let access = access_evaluate(fp, outline)?;
        if access["status"] != "pass" && access["status"] != "info" {
            return Ok(false);
        }
        if bands.is_empty()
            && access["provenance"]["enforcement"] == "hard"
            && access["relaxes_outline"] == true
        {
            own_bands = qualified_polygons(
                &json!({"status":"proven","polygons":access["lane_polygons_nm"]}),
            )?;
            for e in access["overhang"]["edges"]
                .as_array()
                .context("admitted access has no exit edge")?
            {
                exit_edges.push((point(&e["start_nm"])?, point(&e["end_nm"])?));
            }
        }
    }
    let band = if own_bands.is_empty() {
        bands
    } else {
        &own_bands
    };
    Ok(fp
        .courtyards
        .values()
        .all(|p| polygons_fit(p, outline, margin, band, &exit_edges)))
}
pub fn copper_meets(fp: &PreparedFootprint, peer: &PreparedFootprint) -> bool {
    fp.copper_sides.iter().any(|(side, polys)| {
        let own = &fp.geometry_bounds.copper_sides[side];
        peer.courtyards
            .get(side)
            .is_some_and(|other| own.overlaps(polys, &peer.geometry_bounds.courtyards[side], other))
            || peer.copper_sides.get(side).is_some_and(|other| {
                own.overlaps(polys, &peer.geometry_bounds.copper_sides[side], other)
            })
    })
}
fn lands_in(lands: &[Land], keepouts: &[PadKeepout]) -> bool {
    lands.iter().any(|land| {
        keepouts.iter().any(|k| {
            layers_meet(&land.layers, &k.layers)
                && k.polygons
                    .iter()
                    .any(|p| positive_overlap(&land.polygon, p))
        })
    })
}
pub fn copper_escapes(
    fp: &PreparedFootprint,
    outline: &Polygon,
    obstacles: &[Keepout],
    placed: &[PreparedFootprint],
    pad_keepouts: &[PadKeepout],
) -> Result<Option<Blockage>> {
    if !fp.copper_outline.is_empty() {
        let Some(setback) = &fp.setback else {
            return Ok(blockage("setback_unknown", BTreeSet::new()));
        };
        if !polygons_fit(&fp.copper_outline, outline, setback, &[], &[]) {
            return Ok(blockage("outline", BTreeSet::new()));
        }
    }
    if shapes_in(&fp.copper_sides, obstacles) {
        return Ok(blockage("rule_area", BTreeSet::new()));
    }
    let hit = placed
        .iter()
        .filter(|peer| {
            shapes_in(&fp.copper_sides, &peer.keepouts)
                || shapes_in(&peer.copper_sides, &fp.keepouts)
        })
        .map(|p| p.reference.clone())
        .collect::<BTreeSet<_>>();
    if !hit.is_empty() {
        return Ok(blockage("rule_area", hit));
    }
    let hit = placed
        .iter()
        .filter(|peer| copper_meets(fp, peer) || copper_meets(peer, fp))
        .map(|p| p.reference.clone())
        .collect::<BTreeSet<_>>();
    if !hit.is_empty() {
        return Ok(blockage("peer_courtyard", hit));
    }
    if lands_in(&fp.lands, pad_keepouts) {
        return Ok(blockage("pad_rule_area", BTreeSet::new()));
    }
    let hit = placed
        .iter()
        .filter(|peer| {
            lands_in(&fp.lands, &peer.pad_keepouts) || lands_in(&peer.lands, &fp.pad_keepouts)
        })
        .map(|p| p.reference.clone())
        .collect::<BTreeSet<_>>();
    Ok(if hit.is_empty() {
        None
    } else {
        blockage("pad_rule_area", hit)
    })
}
pub fn blockers(fp: &PreparedFootprint, ctx: &AdmissionContext<'_>) -> Result<Option<Blockage>> {
    let mut hit = BTreeSet::new();
    for peer in ctx
        .placed
        .iter()
        .filter(|p| !ctx.fixed.contains(&p.reference))
    {
        for (side, b) in &fp.boxes {
            if let Some(a) = peer.boxes.get(side) {
                if !(&b.x1 + ctx.gap <= a.x0
                    || &a.x1 + ctx.gap <= b.x0
                    || &b.y1 + ctx.gap <= a.y0
                    || &a.y1 + ctx.gap <= b.y0)
                {
                    hit.insert(peer.reference.clone());
                }
            }
        }
    }
    if !hit.is_empty() {
        return Ok(blockage("reserved", hit));
    }
    let hit = ctx
        .placed
        .iter()
        .filter(|p| hole_conflict(fp, p))
        .map(|p| p.reference.clone())
        .collect::<BTreeSet<_>>();
    if !hit.is_empty() {
        return Ok(blockage(
            "hole",
            hit.difference(ctx.fixed).cloned().collect(),
        ));
    }
    if !fits(fp, ctx.outline, ctx.margin, &[])?
        || near(fp, ctx.fixed_exact, ctx.gap)
        || in_keepouts(fp, ctx.board_keepouts)
    {
        return Ok(blockage("outline_or_fixed", BTreeSet::new()));
    }
    let hit = ctx
        .placed
        .iter()
        .filter(|p| in_keepouts(fp, &p.keepouts) || in_keepouts(p, &fp.keepouts))
        .map(|p| p.reference.clone())
        .collect::<BTreeSet<_>>();
    if !hit.is_empty() {
        return Ok(blockage(
            "owned_keepout",
            hit.difference(ctx.fixed).cloned().collect(),
        ));
    }
    let mut escaped = copper_escapes(
        fp,
        ctx.outline,
        ctx.board_keepouts,
        ctx.placed,
        ctx.board_pad_keepouts,
    )?;
    if let Some(b) = &mut escaped {
        b.blockers.retain(|r| !ctx.fixed.contains(r));
    }
    Ok(escaped)
}

fn axis(name: &str) -> Result<Point> {
    match name {
        "x+" => Ok(Point::new(1, 0)),
        "x-" => Ok(Point::new(-1, 0)),
        "y+" => Ok(Point::new(0, 1)),
        "y-" => Ok(Point::new(0, -1)),
        _ => bail!("invalid outward axis"),
    }
}
fn axis_name(p: &Point) -> &'static str {
    if p.x.is_positive() {
        "x+"
    } else if p.x.is_negative() {
        "x-"
    } else if p.y.is_positive() {
        "y+"
    } else {
        "y-"
    }
}
fn dot(a: &Point, b: &Point) -> Int {
    &a.x * &b.x + &a.y * &b.y
}
fn exact_keys(value: &Value, keys: &[&str]) -> bool {
    value
        .as_object()
        .is_some_and(|v| v.len() == keys.len() && keys.iter().all(|k| v.contains_key(*k)))
}
fn overhang_edge(refname: &str, edge: &Value) -> Result<(Point, Point, Point)> {
    if !exact_keys(edge, &["name", "start_nm", "end_nm", "outward", "max_nm"])
        || edge["name"].as_str().is_none_or(str::is_empty)
    {
        bail!("{refname}: overhang edge needs name, start_nm, end_nm, outward axis and max_nm");
    }
    let a = point(&edge["start_nm"])?;
    let b = point(&edge["end_nm"])?;
    let u = axis(edge["outward"].as_str().unwrap_or(""))?;
    let d = integer(&edge["max_nm"])?;
    if !d.is_positive() {
        bail!("{refname}: overhang max_nm must be a positive integer");
    }
    Ok((
        a,
        b,
        Point {
            x: &u.x * &d,
            y: &u.y * &d,
        },
    ))
}
pub fn overhang_declaration(value: &Value) -> Result<String> {
    if !exact_keys(value, &["reference", "edges", "source"])
        || value["reference"].as_str().is_none_or(str::is_empty)
    {
        bail!("courtyard overhang needs exactly reference, edges and source");
    }
    let source = &value["source"];
    let hash = source["envelope_sha256"].as_str().unwrap_or("");
    if !exact_keys(source, &["envelope_id", "envelope_sha256", "pointer"])
        || source
            .as_object()
            .unwrap()
            .values()
            .any(|v| v.as_str().is_none_or(str::is_empty))
        || hash.len() != 64
        || !hash
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    {
        bail!("courtyard overhang must cite its envelope id, sha256 and pointer");
    }
    let reference = value["reference"].as_str().unwrap();
    let rows = value["edges"]
        .as_array()
        .context("overhang edges must be a nonempty list of distinct edges")?;
    let mut names = BTreeSet::new();
    if rows.is_empty() {
        bail!("{reference}: overhang edges must be a nonempty list of distinct edges");
    }
    for e in rows {
        overhang_edge(reference, e)?;
        if !names.insert(e["name"].as_str().unwrap()) {
            bail!("{reference}: overhang edges must be a nonempty list of distinct edges");
        }
    }
    Ok(reference.into())
}
pub fn overhang_regions(
    reference: &str,
    declared: &Value,
    outline: &Polygon,
) -> Result<Vec<Polygon>> {
    let mut swept = Vec::new();
    for e in declared
        .as_array()
        .context("overhang edges must be a list")?
    {
        let (a, b, d) = overhang_edge(reference, e)?;
        if !edges(outline).any(|(c, e)| (a == *c && b == *e) || (a == *e && b == *c))
            || !dot(&b.minus(&a), &d).is_zero()
        {
            bail!(
                "{reference}: overhang edge {} is not a proven outline edge perpendicular to its outward axis",
                e["name"].as_str().unwrap()
            );
        }
        swept.push((a, b, d));
    }
    let mut regions = Vec::new();
    for (a, b, d) in &swept {
        regions.push(vec![a.clone(), b.clone(), b.plus(d), a.plus(d)]);
    }
    for (i, (a, b, d)) in swept.iter().enumerate() {
        for (c, e, f) in &swept[i + 1..] {
            for v in [a, b] {
                if v == c || v == e {
                    regions.push(vec![v.clone(), v.plus(d), v.plus(d).plus(f), v.plus(f)]);
                }
            }
        }
    }
    let regions = regions
        .iter()
        .map(|p| geometry::simple_polygon(&polygon_value(p)))
        .collect::<Result<Vec<_>>>()?;
    if regions
        .iter()
        .any(|r| !intersection_area(r, outline).is_zero())
        || regions.iter().enumerate().any(|(i, r)| {
            regions[i + 1..]
                .iter()
                .any(|q| !intersection_area(r, q).is_zero())
        })
    {
        bail!("{reference}: declared overhang regions point into the board or overlap");
    }
    Ok(regions)
}
pub fn overhang_bands(
    declarations: &Value,
    outline: &Polygon,
) -> Result<BTreeMap<String, Vec<Polygon>>> {
    let mut out = BTreeMap::new();
    for d in declarations
        .as_array()
        .context("courtyard overhangs must be a list")?
    {
        let r = overhang_declaration(d)?;
        if out.contains_key(&r) {
            bail!("{r}: more than one courtyard overhang declared");
        }
        out.insert(r.clone(), overhang_regions(&r, &d["edges"], outline)?);
    }
    Ok(out)
}

pub trait AccessFootprint {
    fn access_raw(&self) -> &Value;
    fn access_value(&self) -> Result<Value>;
}
impl AccessFootprint for Value {
    fn access_raw(&self) -> &Value {
        self
    }
    fn access_value(&self) -> Result<Value> {
        Ok(self.clone())
    }
}
impl AccessFootprint for PreparedFootprint {
    fn access_raw(&self) -> &Value {
        &self.raw
    }
    fn access_value(&self) -> Result<Value> {
        self.to_value()
    }
}
#[derive(Clone, Debug)]
pub struct AccessGeometry {
    pub declaration: Value,
    pub mouth: Point,
    pub vector: Point,
    pub lanes: Vec<Polygon>,
}
pub fn access_provenance(value: &Value) -> Result<Value> {
    let soft = json!({"class":"unknown","enforcement":"soft","soft_reason":"unsourced access direction is advisory"});
    let Some(p) = value.get("provenance") else {
        return Ok(soft);
    };
    if p.is_object()
        && (p.get("citation").is_none()
            || p["citation"].as_str().is_some_and(|s| s.trim().is_empty()))
    {
        return Ok(soft);
    }
    if !exact_keys(p, &["class", "enforcement", "citation"])
        || !matches!(
            p["class"].as_str(),
            Some("fixture_author" | "manufacturer" | "datasheet" | "board" | "arrangement_rule")
        )
        || p["enforcement"] != "hard"
        || p["citation"].as_str().is_none_or(|s| s.trim().is_empty())
    {
        bail!("access direction needs a sourced hard value and citation matching the constraint");
    }
    Ok(p.clone())
}
pub fn access_declaration(value: &Value) -> Result<Value> {
    let mut value = if let Some(s) = value.as_str() {
        if s.len() > 8192 {
            bail!("access declaration exceeds 8192 bytes");
        }
        serde_json::from_str(s).context("invalid access declaration JSON")?
    } else {
        value.clone()
    };
    if value["schema"] != "agent-ee.access-direction/1"
        || value.as_object().is_none_or(|m| {
            m.keys().any(|k| {
                !matches!(
                    k.as_str(),
                    "schema"
                        | "mouth_nm"
                        | "outward"
                        | "lane_polygons_nm"
                        | "travel_nm"
                        | "provenance"
                )
            })
        })
    {
        bail!("unsupported access declaration");
    }
    let mouth =
        point(&value["mouth_nm"]).context("access needs integer mouth_nm and outward axis")?;
    let vector = axis(value["outward"].as_str().unwrap_or(""))
        .map_err(|_| anyhow::anyhow!("access needs integer mouth_nm and outward axis"))?;
    let lanes =
        qualified_polygons(&json!({"status":"proven","polygons":value["lane_polygons_nm"]}))?;
    if lanes
        .iter()
        .flatten()
        .any(|p| dot(&p.minus(&mouth), &vector).is_negative())
    {
        bail!("access lane extends behind its mouth");
    }
    if !lanes
        .iter()
        .any(|p| edges(p).any(|(a, b)| on_segment(a, b, &mouth)))
    {
        bail!("access mouth must touch its lane boundary");
    }
    if let Some(t) = value.get("travel_nm") {
        let travel = integer(t).map_err(|_| {
            anyhow::anyhow!("access travel_nm must be a positive integer equal to the lane travel")
        })?;
        let depth = lanes
            .iter()
            .flatten()
            .map(|p| dot(&p.minus(&mouth), &vector))
            .max()
            .unwrap();
        if !travel.is_positive() || travel != depth {
            bail!("access travel_nm must be a positive integer equal to the lane travel");
        }
    }
    access_provenance(&value)?;
    value["lane_polygons_nm"] = polygons_value(&lanes);
    Ok(value)
}
pub fn access_geometry<F: AccessFootprint + ?Sized>(fp: &F) -> Result<AccessGeometry> {
    let fp = fp.access_value()?;
    let declaration = access_declaration(&fp["access_direction"])?;
    if fp["side"] != "F" && fp["side"] != "B" {
        bail!("access needs a qualified mounting side and integer native position");
    }
    let origin = point(&fp["position_nm"]).map_err(|_| {
        anyhow::anyhow!("access needs a qualified mounting side and integer native position")
    })?;
    let turns = rotation(&fp["rotation_deg"])
        .map_err(|_| anyhow::anyhow!("access direction needs a quarter-turn native pose"))?
        / 90;
    let mouth = rotate(&point(&declaration["mouth_nm"])?, turns).plus(&origin);
    let vector = rotate(&axis(declaration["outward"].as_str().unwrap())?, turns);
    let lanes =
        qualified_polygons(&json!({"status":"proven","polygons":declaration["lane_polygons_nm"]}))?
            .into_iter()
            .map(|p| {
                p.into_iter()
                    .map(|p| rotate(&p, turns).plus(&origin))
                    .collect()
            })
            .collect();
    Ok(AccessGeometry {
        declaration,
        mouth,
        vector,
        lanes,
    })
}
pub fn access_hard<F: AccessFootprint + ?Sized>(fp: &F) -> Result<bool> {
    let Some(d) = fp.access_raw().get("access_direction") else {
        return Ok(false);
    };
    Ok(access_provenance(&access_declaration(d)?)?["enforcement"] == "hard")
}
fn relaxing(d: &Value) -> Result<bool> {
    Ok(access_provenance(d)?["enforcement"] == "hard"
        && matches!(
            d["provenance"]["class"].as_str(),
            Some("manufacturer" | "datasheet")
        )
        && d.get("travel_nm").is_some())
}
pub fn access_relaxes<F: AccessFootprint + ?Sized>(fp: &F) -> Result<bool> {
    let Some(d) = fp.access_raw().get("access_direction") else {
        return Ok(false);
    };
    relaxing(&access_declaration(d)?)
}
pub fn access_overhangs<F: AccessFootprint + ?Sized>(fp: &F) -> Result<bool> {
    if !access_relaxes(fp)? {
        return Ok(false);
    }
    let access = access_geometry(fp)?;
    let value = fp.access_value()?;
    for c in value["courtyards"]
        .as_object()
        .context("courtyards missing")?
        .values()
    {
        for p in qualified_polygons(c)? {
            if access.lanes.iter().any(|q| positive_overlap(&p, q)) {
                return Ok(true);
            }
        }
    }
    Ok(false)
}
pub fn access_overhangs_courtyard<F: AccessFootprint + ?Sized>(fp: &F) -> Result<bool> {
    access_overhangs(fp)
}
pub fn access_minimum_inside_area<F: AccessFootprint + ?Sized>(
    fp: &F,
    polygons: &[Polygon],
) -> Result<Rat> {
    let total = polygons.iter().map(area).sum::<Rat>();
    if !access_relaxes(fp)? {
        return Ok(total);
    }
    let g = access_geometry(fp)?;
    Ok(total
        - polygons
            .iter()
            .flat_map(|p| g.lanes.iter().map(move |q| intersection_area(p, q)))
            .sum::<Rat>())
}
pub fn access_axes<F: AccessFootprint + ?Sized>(
    fp: &F,
    outline: &Polygon,
) -> Result<(Vec<Int>, Vec<Int>)> {
    if !access_hard(fp)? {
        return Ok((Vec::new(), Vec::new()));
    }
    let g = access_geometry(fp)?;
    let points = std::iter::once(&g.mouth)
        .chain(g.lanes.iter().flatten())
        .collect::<Vec<_>>();
    Ok((
        outline
            .iter()
            .flat_map(|v| points.iter().map(move |p| &v.x - &p.x))
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect(),
        outline
            .iter()
            .flat_map(|v| points.iter().map(move |p| &v.y - &p.y))
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect(),
    ))
}
pub fn access_evaluate<F: AccessFootprint + ?Sized>(fp: &F, outline: &Polygon) -> Result<Value> {
    let value = fp.access_value()?;
    let d = access_declaration(&value["access_direction"])?;
    let source = access_provenance(&d)?;
    let g = match access_geometry(&value) {
        Ok(g) => g,
        Err(e) => {
            if source["enforcement"] == "hard" {
                return Err(e);
            }
            return Ok(
                json!({"kind":"access_direction","ref":value["ref"],"status":"info","advisory":true,"provenance":source,"reason":format!("{}; {e}",source["soft_reason"].as_str().unwrap())}),
            );
        }
    };
    let inboard = g
        .lanes
        .iter()
        .map(|p| intersection_area(p, outline))
        .sum::<Rat>();
    let mut row = json!({"kind":"access_direction","ref":value["ref"],"mouth_nm":point_value(&g.mouth),"vector":point_value(&g.vector),"lane_polygons_nm":polygons_value(&g.lanes),"inboard_area_mm2":(&inboard/Rat::from_integer(1_000_000_000_000_i64.into())).to_f64().context("access area is not representable")?,"provenance":source,"relaxes_outline":relaxing(&d)?,"status":"defect","reason":"access mouth cannot reach an outward outline edge with its full lane outside"});
    let mut reachable = false;
    if inboard.is_zero() {
        for (index, (a, b)) in edges(outline).enumerate() {
            if !on_segment(a, b, &g.mouth) || !dot(&b.minus(a), &g.vector).is_zero() {
                continue;
            }
            reachable = true;
            let distance = g
                .lanes
                .iter()
                .flatten()
                .map(|p| dot(&p.minus(&g.mouth), &g.vector))
                .max()
                .unwrap();
            let tangent_y = !g.vector.x.is_zero();
            let component = |p: &Point| if tangent_y { p.y.clone() } else { p.x.clone() };
            let low = g.lanes.iter().flatten().map(&component).min().unwrap();
            let high = g.lanes.iter().flatten().map(&component).max().unwrap();
            if low < component(a).min(component(b)) || high > component(a).max(component(b)) {
                continue;
            }
            let (mut start, mut end) = (a.clone(), b.clone());
            let (v, w) = if component(a) < component(b) {
                (low, high)
            } else {
                (high, low)
            };
            if tangent_y {
                start.y = v;
                end.y = w;
            } else {
                start.x = v;
                end.x = w;
            }
            let mut split = Vec::new();
            for (j, (p, _)) in edges(outline).enumerate() {
                let insert = if j == index {
                    vec![p.clone(), start.clone(), end.clone()]
                } else {
                    vec![p.clone()]
                };
                for p in insert {
                    if split.last() != Some(&p) {
                        split.push(p);
                    }
                }
            }
            if split.last() == split.first() {
                split.pop();
            }
            let edge = json!({"name":format!("outline-{index}"),"start_nm":point_value(&start),"end_nm":point_value(&end),"outward":axis_name(&g.vector),"max_nm":integer_value(&distance)});
            let Ok(bands) =
                overhang_regions(value["ref"].as_str().unwrap_or(""), &json!([edge]), &split)
            else {
                continue;
            };
            if g.lanes
                .iter()
                .all(|p| bands.iter().map(|r| intersection_area(p, r)).sum::<Rat>() == area(p))
            {
                row["status"] = json!("pass");
                row["reason"] = json!("access lane lies outside a proven outline edge");
                row["overhang"] = json!({"reference":value["ref"],"edges":[edge],"provenance":source,"regions_nm":polygons_value(&bands)});
                break;
            }
        }
    }
    if inboard.is_zero() && reachable && row["status"] != "pass" {
        row["status"] = json!("unknown");
        row["reason"] = json!("outside lane has no qualified exterior overhang band");
    }
    if source["enforcement"] == "soft" {
        row["status"] = json!("info");
        row["advisory"] = json!(true);
        row["reason"] = json!(format!(
            "{}; {}",
            source["soft_reason"].as_str().unwrap(),
            row["reason"].as_str().unwrap()
        ));
    }
    Ok(row)
}
pub fn access_admits<F: AccessFootprint + ?Sized>(fp: &F, outline: &Polygon) -> Result<bool> {
    if fp.access_raw().get("access_direction").is_none() {
        return Ok(true);
    }
    let row = access_evaluate(fp, outline)?;
    Ok(row["status"] == "pass" || row["status"] == "info")
}
pub fn grade_access(capture: &Value) -> Result<(Vec<Value>, Vec<Value>)> {
    let fps = capture["footprints"]
        .as_array()
        .context("footprints missing")?;
    let mut declared = fps
        .iter()
        .filter(|f| f.get("access_direction").is_some())
        .collect::<Vec<_>>();
    if declared.is_empty() {
        return Ok((vec![], vec![]));
    }
    let outlines = qualified_polygons(&capture["outline"]).and_then(|p| {
        if p.len() == 1 {
            Ok(p)
        } else {
            bail!("single closed outline required")
        }
    });
    let outline = match outlines {
        Ok(mut p) => p.remove(0),
        Err(e) => return Ok((
            vec![],
            declared
                .iter()
                .map(|fp| json!({"kind":"access_direction","ref":fp["ref"],"reason":e.to_string()}))
                .collect(),
        )),
    };
    declared.sort_by_key(|f| f["ref"].as_str().unwrap_or(""));
    let (mut rows, mut unknown) = (Vec::new(), Vec::new());
    for fp in declared {
        let result = (|| -> Result<Value> {
            let mut row = access_evaluate(fp, &outline)?;
            let mut blocked = Vec::new();
            let mut approximate = Vec::new();
            let lane = if row.get("lane_polygons_nm").is_some() {
                qualified_polygons(&json!({"status":"proven","polygons":row["lane_polygons_nm"]}))?
            } else {
                Vec::new()
            };
            for peer in fps {
                if std::ptr::eq(fp, peer) {
                    continue;
                }
                let Some(c) = fp["side"].as_str().and_then(|s| peer["courtyards"].get(s)) else {
                    continue;
                };
                let Ok((outer, inner)) = geometry::courtyard_bounds(c) else {
                    continue;
                };
                if lane
                    .iter()
                    .any(|p| inner.iter().any(|q| positive_overlap(p, q)))
                {
                    blocked.push(peer["ref"].clone());
                } else if c.get("inner_polygons").is_some()
                    && lane
                        .iter()
                        .any(|p| outer.iter().any(|q| positive_overlap(p, q)))
                {
                    approximate.push(peer["ref"].clone());
                }
            }
            blocked.sort_by_key(|v| v.as_str().unwrap_or("").to_owned());
            approximate.sort_by_key(|v| v.as_str().unwrap_or("").to_owned());
            if !blocked.is_empty() {
                row["blocked_by"] = json!(blocked);
                row["reason"] = json!("access lane obstructed by peer courtyard");
                row["status"] = json!(if row["provenance"]["enforcement"] == "soft" {
                    "info"
                } else {
                    "defect"
                });
            }
            if !approximate.is_empty() {
                row["approximate_peers"] = json!(approximate);
                if row["status"] == "pass" {
                    row["status"] = json!("unknown");
                    row["reason"] = json!("peer clearance lies only in a courtyard curve bound");
                }
            }
            Ok(row)
        })();
        match result {
            Ok(row) => {
                if row["status"] == "unknown" {
                    unknown.push(row.clone());
                }
                rows.push(row);
            }
            Err(e) => unknown
                .push(json!({"kind":"access_direction","ref":fp["ref"],"reason":e.to_string()})),
        }
    }
    Ok((rows, unknown))
}

pub fn copper_setback_record(capture: &Value) -> Result<Value> {
    let max = integer(&capture["max_error_nm"])?;
    if max.is_negative() {
        bail!("board max_error_nm must be a nonnegative integer");
    }
    Ok(
        json!({"setback_nm":integer_value(&(&max+50_u8)),"max_error_nm":integer_value(&max),"report_window_nm":50,"source":"saved board m_MaxError (ERROR_OUTSIDE) plus the AGENTEE-331 edge-clearance reporting window"}),
    )
}
pub fn copper_setback_missing(fps: &[PreparedFootprint]) -> Result<Option<String>> {
    let mut refs = fps
        .iter()
        .filter(|fp| !fp.copper_outline.is_empty() && fp.setback.is_none())
        .map(|fp| fp.reference.clone())
        .collect::<Vec<_>>();
    refs.sort();
    Ok(if refs.is_empty() {
        None
    } else {
        Some(format!(
            "copper setback unknown: the placement facts carry no board max_error_nm (m_MaxError), so the copper of {} is not proven to stay the setback inside the outline",
            refs.join(", ")
        ))
    })
}
fn bounds_value(b: &geometry::Bounds) -> Value {
    Value::Array(
        [&b.x0, &b.y0, &b.x1, &b.y1]
            .into_iter()
            .map(integer_value)
            .collect(),
    )
}
fn edge_key(a: &Point, b: &Point) -> (Point, Point) {
    if a <= b {
        (a.clone(), b.clone())
    } else {
        (b.clone(), a.clone())
    }
}
fn extend_row(row: &Value, fields: Value) -> Value {
    let mut out = row.clone();
    out.as_object_mut()
        .unwrap()
        .extend(fields.as_object().unwrap().clone());
    out
}
pub fn fixed_input_copper(
    fixed: &[PreparedFootprint],
    outline: &Polygon,
    obstacles: &[Keepout],
    pad_keepouts: &[PadKeepout],
    overhangs: &Value,
) -> Result<(Option<Value>, Vec<Value>)> {
    let (mut findings, mut unproven, mut carried) = (Vec::new(), Vec::new(), Vec::new());
    let mut declared: BTreeMap<String, BTreeMap<(Point, Point), (Value, Value)>> = BTreeMap::new();
    for d in overhangs
        .as_array()
        .context("courtyard overhangs must be a list")?
    {
        for e in d["edges"].as_array().context("overhang edges missing")? {
            declared
                .entry(
                    d["reference"]
                        .as_str()
                        .context("overhang reference missing")?
                        .into(),
                )
                .or_default()
                .insert(
                    edge_key(&point(&e["start_nm"])?, &point(&e["end_nm"])?),
                    (e.clone(), d.clone()),
                );
        }
    }
    for fp in fixed {
        let raw = fp.to_value()?;
        let empty = BTreeMap::new();
        let exits = declared.get(&fp.reference).unwrap_or(&empty);
        let pad_areas = pad_keepouts
            .iter()
            .chain(
                fixed
                    .iter()
                    .filter(|p| !std::ptr::eq(*p, fp))
                    .flat_map(|p| &p.pad_keepouts),
            )
            .collect::<Vec<_>>();
        let all_obstacles = obstacles
            .iter()
            .chain(
                fixed
                    .iter()
                    .filter(|p| !std::ptr::eq(*p, fp))
                    .flat_map(|p| &p.keepouts),
            )
            .collect::<Vec<_>>();
        for pad in raw["pads"].as_array().context("pad inventory missing")? {
            let layers = if pad.get("layers").is_none_or(Value::is_null) {
                BTreeSet::from(["*.Cu".to_owned()])
            } else {
                copper_layers(&pad["layers"])?
            };
            let b = if layers.is_empty() {
                None
            } else {
                copper_box(&raw, pad)?
            };
            let row = json!({"kind":"fixed_input_copper","reference":fp.reference,"pad":pad["number"],"copper_bounds_nm":b.as_ref().map(bounds_value),"setback_nm":fp.setback.as_ref().map(integer_value)});
            if !layers.is_empty() {
                let lands = match &b {
                    None => fp
                        .courtyards
                        .values()
                        .flatten()
                        .cloned()
                        .collect::<Vec<_>>(),
                    Some(b) if b.x0 < b.x1 && b.y0 < b.y1 => vec![rectangle(b)],
                    _ => vec![],
                };
                for k in &pad_areas {
                    if layers_meet(&layers, &k.layers)
                        && lands
                            .iter()
                            .any(|p| k.polygons.iter().any(|q| positive_overlap(p, q)))
                    {
                        let finding = extend_row(
                            &row,
                            json!({"keepout":k.name,"violation":if b.is_none(){"pad_keepout_unproven"}else{"in_pad_keepout"}}),
                        );
                        if b.is_none() {
                            unproven.push(finding);
                        } else {
                            findings.push(finding);
                        }
                    }
                }
            }
            let Some(b) = b else {
                continue;
            };
            if b.x0 == b.x1 || b.y0 == b.y1 {
                continue;
            }
            let poly = rectangle(&b);
            if !polygons_fit(std::slice::from_ref(&poly), outline, &Int::zero(), &[], &[]) {
                findings.push(extend_row(
                    &row,
                    json!({"violation":"outside_outline","outline":"board outline"}),
                ));
            } else if let Some(s) = &fp.setback {
                if !polygons_fit(std::slice::from_ref(&poly), outline, s, &[], &[]) {
                    let gaps = edges(outline)
                        .map(|(c, d)| {
                            (
                                edge_key(c, d),
                                edges(&poly)
                                    .map(|(a, b)| segment_distance2(a, b, c, d))
                                    .min()
                                    .unwrap(),
                            )
                        })
                        .collect::<Vec<_>>();
                    let near = gaps
                        .iter()
                        .filter(|(_, d)| *d < Rat::from_integer(s * s))
                        .collect::<Vec<_>>();
                    let binding = near
                        .iter()
                        .filter(|(e, _)| !exits.contains_key(e))
                        .map(|(_, d)| d)
                        .collect::<Vec<_>>();
                    if !binding.is_empty() || near.is_empty() {
                        let closest = if binding.is_empty() {
                            gaps.iter().map(|(_, d)| d).min().unwrap()
                        } else {
                            *binding.iter().min().unwrap()
                        };
                        findings.push(extend_row(&row,json!({"violation":"within_setback","outline":"board outline","outline_distance_nm":integer_value(&isqrt(&floor(closest)))})));
                    } else {
                        for (e, d2) in near {
                            let (edge, declaration) = &exits[e];
                            carried.push(extend_row(&row,json!({"kind":"fixed_overhang_copper","edge":edge["name"],"outline_distance_nm":integer_value(&isqrt(&floor(d2))),"declaration":{"reference":declaration["reference"],"edge":edge,"source":declaration["source"]}})));
                        }
                    }
                }
            } else {
                unproven.push(extend_row(&row, json!({"violation":"setback_unknown"})));
            }
            for side in ["F", "B"] {
                if layers.contains("*.Cu") || layers.contains(&format!("{side}.Cu")) {
                    for o in &all_obstacles {
                        if o.side == side && o.polygons.iter().any(|p| positive_overlap(&poly, p)) {
                            findings.push(extend_row(
                                &row,
                                json!({"violation":"in_keepout","side":side,"keepout":o.name}),
                            ));
                        }
                    }
                }
            }
        }
    }
    if !findings.is_empty() {
        let refs = findings
            .iter()
            .map(|f| {
                format!(
                    "{} pad {}",
                    f["reference"].as_str().unwrap_or(""),
                    f["pad"].as_str().unwrap_or("")
                )
            })
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect::<Vec<_>>()
            .join(", ");
        return Ok((
            Some(
                json!({"outcome":"defect","findings":findings,"reason":format!("fixed part copper outside the outline, within the copper setback of it, or in a keep-out: {refs}")}),
            ),
            carried,
        ));
    }
    if !unproven.is_empty() {
        let mut reasons = Vec::new();
        for violation in ["setback_unknown", "pad_keepout_unproven"] {
            let refs = unproven
                .iter()
                .filter(|f| f["violation"] == violation)
                .map(|f| f["reference"].as_str().unwrap_or(""))
                .collect::<BTreeSet<_>>()
                .into_iter()
                .collect::<Vec<_>>()
                .join(", ");
            if !refs.is_empty() {
                reasons.push(if violation=="setback_unknown"{format!("copper setback unknown: the placement facts carry no board max_error_nm (m_MaxError), so the copper of fixed {refs} is not proven to stay the setback inside the outline")}else{format!("pad keep-out unproven: fixed {refs} has lands without native land facts whose courtyard meets a rule area forbidding pads")});
            }
        }
        return Ok((
            Some(json!({"outcome":"unknown","findings":unproven,"reason":reasons.join("; ")})),
            carried,
        ));
    }
    Ok((None, carried))
}

#[cfg(test)]
mod tests {
    use super::*;
    fn poly(v: Value) -> Polygon {
        geometry::simple_polygon(&v).unwrap()
    }
    #[test]
    fn exact_gap_and_containment() {
        let outline = poly(json!([[0, 0], [100, 0], [100, 100], [0, 100]]));
        let p = poly(json!([[10, 10], [20, 10], [20, 20], [10, 20]]));
        assert!(polygons_fit(&[p.clone()], &outline, &10.into(), &[], &[]));
        assert!(!polygons_fit(&[p], &outline, &11.into(), &[], &[]));
    }
    #[test]
    fn sourced_access_edge_and_wrong_direction() {
        let outline = poly(json!([[0, 0], [100, 0], [100, 100], [0, 100]]));
        let fp = json!({"ref":"J1","side":"F","position_nm":[100,50],"rotation_deg":0,"access_direction":{"schema":"agent-ee.access-direction/1","mouth_nm":[0,0],"outward":"x+","lane_polygons_nm":[[[0,-10],[20,-10],[20,10],[0,10]]],"travel_nm":20,"provenance":{"class":"manufacturer","enforcement":"hard","citation":"fixture"}}});
        assert!(access_admits(&fp, &outline).unwrap());
        let mut moved = fp;
        moved["position_nm"] = json!([0, 50]);
        assert!(!access_admits(&moved, &outline).unwrap());
    }
    #[test]
    fn overhang_cannot_point_into_board() {
        let outline = poly(json!([[0, 0], [100, 0], [100, 100], [0, 100]]));
        let edge = json!([{"name":"right","start_nm":[100,0],"end_nm":[100,100],"outward":"x+","max_nm":10}]);
        assert_eq!(overhang_regions("J1", &edge, &outline).unwrap().len(), 1);
        let mut inward = edge;
        inward[0]["outward"] = json!("x-");
        assert!(overhang_regions("J1", &inward, &outline).is_err());
    }
    fn part(reference: &str, side: &str, outline: Value) -> PreparedFootprint {
        PreparedFootprint::from_value(&json!({"ref":reference,"uuid":reference,"side":side,"position_nm":[0,0],"rotation_deg":0,"pads":[],"courtyards":{side:{"status":"proven","polygons":[outline]}}}),Some(1.into())).unwrap()
    }
    #[test]
    fn holes_are_symmetric_across_sides_and_keepout_ownership_is_exempt() {
        let mut front = part("H1", "F", json!([[0, 0], [2, 0], [2, 2], [0, 2]]));
        let mut back = part("C1", "B", json!([[5, 5], [7, 5], [7, 7], [5, 7]]));
        front
            .holes
            .push(poly(json!([[4, 4], [8, 4], [8, 8], [4, 8]])));
        back.targets
            .extend(back.courtyards.values().flatten().cloned());
        front.refresh_geometry_bounds().unwrap();
        back.refresh_geometry_bounds().unwrap();
        assert!(hole_conflict(&front, &back));
        assert!(hole_conflict(&back, &front));
        back.keepouts.push(Keepout {
            side: "B".into(),
            polygons: back.targets.clone(),
            name: "owned".into(),
        });
        assert!(!keepout_blocked(&back, &[], &[]));
        let peer = part("C2", "B", json!([[6, 6], [8, 6], [8, 8], [6, 8]]));
        assert!(keepout_blocked(&back, &[peer], &[]));
    }
    #[test]
    fn copper_setback_exception_keeps_outline_binding() {
        let outline = poly(json!([[0, 0], [100, 0], [100, 100], [0, 100]]));
        let mut raw = part("J1", "F", json!([[90, 40], [110, 40], [110, 60], [90, 60]]))
            .to_value()
            .unwrap();
        raw["pads"] = json!([{"uuid":"pad1","number":"1","net":"","position_nm":[99,50],"rotation_deg":0,"shape":1,"size_nm":[2,2],"layers":["F.Cu"],"copper_centroid_offset_nm":[[0,1],[0,1]]}]);
        let fp = PreparedFootprint::from_value(&raw, Some(2.into())).unwrap();
        let declarations = json!([{"reference":"J1","edges":[{"name":"right","start_nm":[100,0],"end_nm":[100,100],"outward":"x+","max_nm":10}],"source":{"envelope_id":"fixture","envelope_sha256":"a".repeat(64),"pointer":"/fixed/0"}}]);
        let (refusal, carried) =
            fixed_input_copper(&[fp.clone()], &outline, &[], &[], &declarations).unwrap();
        assert!(refusal.is_none());
        assert_eq!(carried.len(), 1);
        assert_eq!(carried[0]["outline_distance_nm"], 0);
        let (refusal, _) = fixed_input_copper(&[fp], &outline, &[], &[], &json!([])).unwrap();
        assert_eq!(
            refusal.unwrap()["findings"][0]["violation"],
            "within_setback"
        );
        raw["pads"][0]["position_nm"] = json!([100, 50]);
        let fp = PreparedFootprint::from_value(&raw, Some(2.into())).unwrap();
        let (refusal, _) = fixed_input_copper(&[fp], &outline, &[], &[], &declarations).unwrap();
        assert_eq!(
            refusal.unwrap()["findings"][0]["violation"],
            "outside_outline"
        );
    }
}
