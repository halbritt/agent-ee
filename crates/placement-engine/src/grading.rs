//! Independent exact geometric judgment and declared advisory reports.
use crate::{
    exact::{self, Int, Point, Rat},
    geometry::{self, Bounds, Polygon},
    policy,
};
use anyhow::{Context, Result, ensure};
use num_traits::{One, Signed, ToPrimitive, Zero};
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};

pub fn round_decimal(value: f64, places: usize) -> f64 {
    format!("{value:.places$}").parse().unwrap_or(value)
}
pub fn validate_capture(c: &Value) -> Result<()> {
    ensure!(
        c.is_object() && c["schema"] == "agent-ee.placement-probe/1",
        "unsupported placement capture schema"
    );
    ensure!(
        c["board_sha256"].as_str().is_some_and(|s| s.len() == 64
            && s.chars()
                .all(|c| c.is_ascii_digit() || ('a'..='f').contains(&c))),
        "missing captured board hash"
    );
    let fps = c["footprints"]
        .as_array()
        .filter(|a| !a.is_empty())
        .context("empty footprint inventory")?;
    let mut refs = BTreeSet::new();
    let mut uuids = BTreeSet::new();
    for fp in fps {
        ensure!(fp.is_object(), "footprint record must be an object");
        ensure!(
            fp["ref"]
                .as_str()
                .is_some_and(|s| !s.is_empty() && refs.insert(s))
                && fp["uuid"]
                    .as_str()
                    .is_some_and(|s| !s.is_empty() && uuids.insert(s)),
            "missing or ambiguous footprint identity"
        );
    }
    Ok(())
}
fn area_mm2(n: &Rat) -> Result<f64> {
    Ok((n / Rat::from_integer(Int::from(1_000_000_000_000_i64)))
        .to_f64()
        .context("area outside reporting float range")?)
}
fn area(p: &Polygon) -> Rat {
    Rat::new(geometry::area2(p).abs(), 2.into())
}
fn beyond(ps: &[Polygon], outline: &Polygon, bands: &[Polygon]) -> Rat {
    ps.iter()
        .map(|p| {
            area(p)
                - geometry::intersection_area(p, outline)
                - bands
                    .iter()
                    .map(|q| geometry::intersection_area(p, q))
                    .sum::<Rat>()
        })
        .sum()
}
fn parse_polygons(v: &Value) -> Result<Vec<Polygon>> {
    v.as_array()
        .context("polygons required")?
        .iter()
        .map(geometry::simple_polygon)
        .collect()
}
pub fn judge_mechanical(capture: &Value, scoring: Option<&Value>, overhangs: Option<&Value>, mechanical: Option<&Value>) -> Result<Value> {
    let mut result = judge(capture, scoring, overhangs)?;
    if let Some(binding) = mechanical {
        let mechanics = crate::mechanical::grade(capture, binding, false)?;
        result["hard_failures"].as_array_mut().context("judge failures missing")?
            .extend(mechanics["defects"].as_array().context("mechanical defects missing")?.iter().cloned());
        result["unknown"].as_array_mut().context("judge coverage missing")?
            .extend(mechanics["unknown"].as_array().context("mechanical coverage missing")?.iter().cloned());
        let (verdict, outcome) = if !result["hard_failures"].as_array().unwrap().is_empty() {
            ("hard_fail", "defect")
        } else if !result["unknown"].as_array().unwrap().is_empty() {
            ("unknown", "unknown")
        } else { ("pass", "pass") };
        result["verdict"] = json!(verdict);
        result["outcome"] = json!(outcome);
        result["mechanical_reservations"] = mechanics;
    }
    Ok(result)
}

pub fn judge(capture: &Value, scoring: Option<&Value>, overhangs: Option<&Value>) -> Result<Value> {
    validate_capture(capture)?;
    let (access, access_unknown) = crate::constraints::grade_access(capture)?;
    let mut bands = BTreeMap::new();
    for row in &access {
        if row.get("overhang").is_some()
            && row["provenance"]["enforcement"] == "hard"
            && row["relaxes_outline"] == true
        {
            bands.insert(
                row["ref"]
                    .as_str()
                    .context("access reference required")?
                    .to_owned(),
                parse_polygons(&row["lane_polygons_nm"])?,
            );
        }
    }
    let access_refs: BTreeSet<_> = bands.keys().cloned().collect();
    let (mut unknown, mut failures, mut declared) = (Vec::new(), Vec::new(), Vec::new());
    let outline = match geometry::qualified_polygons(&capture["outline"]) {
        Ok(p) if p.len() == 1 => Some(p[0].clone()),
        Ok(_) => {
            unknown
                .push(json!({"kind":"outline","reason":"multiple outline contours unsupported"}));
            None
        }
        Err(e) => {
            unknown.push(json!({"kind":"outline","reason":e.to_string()}));
            None
        }
    };
    let explicit = match (overhangs, outline.as_ref()) {
        (Some(ds), Some(o)) => crate::constraints::overhang_bands(ds, o)?,
        _ => BTreeMap::new(),
    };
    let lane_only: BTreeSet<_> = access_refs
        .difference(&explicit.keys().cloned().collect())
        .cloned()
        .collect();
    bands.extend(explicit);
    let mut fps: Vec<_> = capture["footprints"].as_array().unwrap().iter().collect();
    fps.sort_by_key(|fp| fp["ref"].as_str().unwrap());
    let mut courtyards = Vec::new();
    for fp in fps {
        let r = fp["ref"].as_str().unwrap();
        let cs = fp["courtyards"].as_object();
        if cs.is_none_or(|cs| !cs.contains_key(fp["side"].as_str().unwrap_or(""))) {
            unknown.push(json!({"kind":"courtyard","ref":r,"reason":"missing"}));
        }
        let mut entries: Vec<_> = cs.into_iter().flat_map(|m| m.iter()).collect();
        entries.sort_by_key(|(side, _)| *side);
        for (side, c) in entries {
            let result = if side != "F" && side != "B" {
                Err(anyhow::anyhow!("unsupported courtyard side"))
            } else {
                geometry::courtyard_bounds(c)
            };
            let (outer, inner) = match result {
                Ok(p) => p,
                Err(e) => {
                    unknown.push(
                        json!({"kind":"courtyard","ref":r,"side":side,"reason":e.to_string()}),
                    );
                    continue;
                }
            };
            if let Some(outline) = &outline {
                let band = if lane_only.contains(r) && fp["side"] != *side {
                    None
                } else {
                    bands.get(r)
                };
                let band = band.map(Vec::as_slice).unwrap_or(&[]);
                let outside = beyond(&inner, outline, band);
                if outside > Rat::zero() {
                    failures.push(json!({"kind":"outside_outline","ref":r,"side":side,"outside_area_mm2":area_mm2(&outside)?}));
                } else if outer != inner && beyond(&outer, outline, band) > Rat::zero() {
                    unknown.push(json!({"kind":"courtyard_approximation","ref":r,"side":side,"reason":"outside area lies only between the declared curve bounds"}));
                }
                let overhang: Rat = outer
                    .iter()
                    .flat_map(|p| band.iter().map(move |q| geometry::intersection_area(p, q)))
                    .sum();
                if overhang > Rat::zero() {
                    declared.push(
                        json!({"ref":r,"side":side,"overhang_area_mm2":area_mm2(&overhang)?}),
                    );
                }
            }
            courtyards.push((r.to_owned(), side.clone(), outer, inner));
        }
    }
    for (i, (r, side, left, li)) in courtyards.iter().enumerate() {
        for (other, os, right, ri) in &courtyards[i + 1..] {
            if side != os || r == other {
                continue;
            }
            let overlap: Rat = li
                .iter()
                .flat_map(|p| ri.iter().map(move |q| geometry::intersection_area(p, q)))
                .sum();
            if overlap > Rat::zero() {
                failures.push(json!({"kind":"courtyard_overlap","refs":[r,other],"side":side,"overlap_area_mm2":area_mm2(&overlap)?}));
            } else if (left != li || right != ri)
                && left.iter().any(|p| {
                    right
                        .iter()
                        .any(|q| geometry::intersection_area(p, q) > Rat::zero())
                })
            {
                unknown.push(json!({"kind":"courtyard_approximation","refs":[r,other],"side":side,"reason":"overlap lies only between the declared curve bounds"}));
            }
        }
    }
    failures.extend(access.iter().filter(|r| r["status"] == "defect").cloned());
    unknown.extend(access_unknown.clone());
    let (associations, mut advisories, score) = advisories(capture, scoring)?;
    advisories.extend(access.iter().filter(|r| r["status"] == "info").cloned());
    let (verdict, outcome) = if !failures.is_empty() {
        ("hard_fail", "defect")
    } else if !unknown.is_empty() {
        ("unknown", "unknown")
    } else {
        ("pass", "pass")
    };
    let off: BTreeSet<_> = failures
        .iter()
        .filter(|r| r["kind"] == "outside_outline")
        .map(|r| r["ref"].as_str().unwrap().to_owned())
        .collect();
    let mut out = json!({"schema":"agent-ee.placement-judgment/1","board_sha256":capture["board_sha256"],"verdict":verdict,"outcome":outcome,"score":score,"hard_failures":failures,"off_board":off,"unknown":unknown,"associations":associations,"advisories":advisories,"policy":scoring,"authority":"standalone-unsealed"});
    if !access.is_empty() || !access_unknown.is_empty() {
        out["access_directions"] = json!(access);
    }
    if !access_refs.is_empty() || overhangs.is_some() {
        out["declared_overhangs"] = json!(declared);
    }
    if let Some(o) = overhangs {
        out["overhangs"] = o.clone();
    }
    Ok(out)
}
pub fn keep_apart_rules(p: &Value) -> Result<Value> {
    ensure!(
        p.is_object() && p["schema"] == "agent-ee.placement-policy/1",
        "unsupported placement policy"
    );
    let empty = json!([]);
    let a = p
        .get("keep_apart")
        .unwrap_or(&empty)
        .as_array()
        .context("keep_apart must be a list")?;
    let mut pairs = BTreeSet::new();
    for r in a {
        ensure!(
            policy::keys(r, &["ref", "others", "min_distance_mm"], &[]),
            "keep_apart needs ref, others and min_distance_mm"
        );
        let name = r["ref"]
            .as_str()
            .filter(|s| !s.trim().is_empty())
            .context("keep_apart ref must be nonempty text")?;
        let others = r["others"]
            .as_array()
            .filter(|a| !a.is_empty())
            .context("keep_apart others must be distinct nonempty references, excluding ref")?;
        let mut seen = BTreeSet::new();
        for o in others {
            let s = o
                .as_str()
                .filter(|s| !s.trim().is_empty() && *s != name)
                .context("keep_apart others must be distinct nonempty references, excluding ref")?;
            ensure!(
                seen.insert(s),
                "keep_apart others must be distinct nonempty references, excluding ref"
            );
            let mut pair = [name, s];
            pair.sort();
            ensure!(pairs.insert(pair), "duplicate keep_apart pair");
        }
        ensure!(
            r["min_distance_mm"].is_number() || r["min_distance_mm"].is_string(),
            "min_distance_mm must be a number"
        );
        let nm = exact::nanometres(&r["min_distance_mm"])
            .map_err(|_| anyhow::anyhow!("min_distance_mm must be exact to 1 nm"))?;
        ensure!(nm > Int::zero(), "min_distance_mm must be positive");
    }
    Ok(json!(a))
}
pub fn validate_scoring(p: &Value, refs: &BTreeSet<String>) -> Result<()> {
    ensure!(
        p.is_object() && p["schema"] == "agent-ee.placement-policy/1",
        "unsupported placement policy"
    );
    for n in ["ic_refs", "connector_refs", "ground_nets"] {
        let s = policy::strings(&p[n], n)
            .map_err(|_| anyhow::anyhow!("policy needs explicit unique {n}"))?;
        ensure!(
            n == "ground_nets" || s.is_subset(refs),
            "unknown policy reference"
        );
    }
    let ics = policy::strings(&p["ic_refs"], "ic_refs")?;
    let cs = policy::strings(&p["connector_refs"], "connector_refs")?;
    let caps = p["capacitors"]
        .as_object()
        .context("invalid capacitor inventory")?;
    ensure!(
        caps.keys()
            .all(|r| refs.contains(r) && !ics.contains(r) && !cs.contains(r)),
        "invalid capacitor inventory"
    );
    ensure!(ics.is_disjoint(&cs), "ambiguous component role");
    ensure!(
        exact::integer(&p["max_shared_refs"]).is_ok_and(|n| n >= Int::one() && n <= 8.into()),
        "max_shared_refs must be 1..8"
    );
    for c in caps.values() {
        ensure!(c.is_object(), "capacitor declaration must be an object");
        for k in ["capacitance_nf", "max_distance_mm", "deduction"] {
            ensure!(
                c[k].as_f64()
                    .is_some_and(|v| v.is_finite() && v >= 0.0 && (k == "deduction" || v > 0.0)),
                "invalid declared capacitor {k}"
            );
        }
    }
    keep_apart_rules(p)?;
    Ok(())
}
fn distance(a: &Value, b: &Value) -> Result<f64> {
    let a = exact::point(a)?;
    let b = exact::point(b)?;
    // math.dist converts the input coordinates to doubles before subtraction.
    // Decisions elsewhere use exact coordinates; this is advisory report math.
    let x = a.x.to_f64().context("distance outside float range")?
        - b.x.to_f64().context("distance outside float range")?;
    let y = a.y.to_f64().context("distance outside float range")?
        - b.y.to_f64().context("distance outside float range")?;
    Ok(x.hypot(y) / 1e6)
}
fn deduct(score: &mut Value, d: &Value) -> Result<()> {
    if let (Ok(s), Ok(d)) = (exact::integer(score), exact::integer(d)) {
        *score = exact::integer_value(&(s - d));
    } else {
        *score = json!(
            score.as_f64().context("score outside float range")?
                - d.as_f64().context("deduction outside float range")?
        );
    }
    Ok(())
}
fn merged(mut row: Value, more: &Value) -> Value {
    if let Some(o) = more.as_object() {
        for (k, v) in o {
            row[k] = v.clone();
        }
    }
    row
}
fn advisories(c: &Value, p: Option<&Value>) -> Result<(Vec<Value>, Vec<Value>, Value)> {
    let Some(p) = p else {
        return Ok((Vec::new(), Vec::new(), Value::Null));
    };
    let fps: BTreeMap<_, _> = c["footprints"]
        .as_array()
        .unwrap()
        .iter()
        .map(|f| (f["ref"].as_str().unwrap().to_owned(), f))
        .collect();
    validate_scoring(p, &fps.keys().cloned().collect())?;
    let grounds = policy::strings(&p["ground_nets"], "ground_nets")?;
    let connectors = policy::strings(&p["connector_refs"], "connector_refs")?;
    let ics = policy::strings(&p["ic_refs"], "ic_refs")?;
    let mut nets: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    for (r, fp) in &fps {
        for pad in fp["pads"].as_array().context("pads required")? {
            let n = pad["net"].as_str().context("pad net required")?;
            if !n.is_empty() && !grounds.contains(n) {
                nets.entry(n.to_owned()).or_default().insert(r.clone());
            }
        }
    }
    let (mut associations, mut advisories, mut score) = (Vec::new(), Vec::new(), json!(100));
    let mut caps: Vec<_> = p["capacitors"].as_object().unwrap().iter().collect();
    caps.sort_by_key(|(r, _)| *r);
    let shared = exact::integer(&p["max_shared_refs"])?.to_usize().unwrap();
    for (r, d) in caps {
        let ns: Vec<_> = nets
            .iter()
            .filter(|(_, rs)| rs.contains(r) && rs.len() <= shared)
            .map(|(n, _)| n.clone())
            .collect();
        let cs: BTreeSet<_> = ns
            .iter()
            .flat_map(|n| nets[n].intersection(&connectors).cloned())
            .collect();
        let is: BTreeSet<_> = ns
            .iter()
            .flat_map(|n| nets[n].intersection(&ics).cloned())
            .collect();
        let near = |rs: &BTreeSet<String>| -> Result<Option<(f64, String)>> {
            let mut ds = rs
                .iter()
                .map(|s| {
                    Ok((
                        distance(&fps[r]["position_nm"], &fps[s]["position_nm"])?,
                        s.clone(),
                    ))
                })
                .collect::<Result<Vec<_>>>()?;
            ds.sort_by(|a, b| a.0.total_cmp(&b.0).then(a.1.cmp(&b.1)));
            Ok(ds.into_iter().next())
        };
        let nearest = near(&cs)?;
        if let Some((distance, n)) =
            nearest.filter(|(distance, _)| *distance <= d["max_distance_mm"].as_f64().unwrap())
        {
            associations.push(json!({"ref":r,"kind":"interface_filter","connectors":cs,"nearest_connector":n,"distance_mm":distance,"nets":ns}));
        } else if let Some((distance, ic)) = near(&is)? {
            associations.push(merged(
                json!({"ref":r,"kind":"decoupling","ic":ic,"nets":ns,"distance_mm":distance}),
                d,
            ));
            if distance > d["max_distance_mm"].as_f64().unwrap() {
                advisories.push(merged(
                    json!({"kind":"decoupling_distance","ref":r,"ic":ic,"distance_mm":distance}),
                    d,
                ));
                deduct(&mut score, &d["deduction"])?;
            }
        } else {
            associations.push(json!({"ref":r,"kind":"unassociated","nets":ns}));
        }
    }
    if let Some(edge) = p.get("connector_edge").filter(|v| !v.is_null()) {
        for k in ["max_distance_mm", "deduction"] {
            ensure!(
                edge[k].as_f64().is_some_and(|v| v.is_finite() && v >= 0.0),
                "invalid connector edge policy"
            );
        }
        if let Ok(outline) = geometry::qualified_polygons(&c["outline"]) {
            if outline.len() == 1 {
                for r in connectors {
                    let point = exact::point(&fps[&r]["position_nm"])?;
                    let mut distance = f64::INFINITY;
                    let ps = &outline[0];
                    for i in 0..ps.len() {
                        let a = &ps[i];
                        let b = &ps[(i + 1) % ps.len()];
                        let dx = (&b.x - &a.x)
                            .to_f64()
                            .context("distance outside float range")?;
                        let dy = (&b.y - &a.y)
                            .to_f64()
                            .context("distance outside float range")?;
                        let numerator =
                            (&point.x - &a.x) * (&b.x - &a.x) + (&point.y - &a.y) * (&b.y - &a.y);
                        let denominator = (&b.x - &a.x).pow(2) + (&b.y - &a.y).pow(2);
                        let t = Rat::new(numerator, denominator)
                            .to_f64()
                            .context("distance outside float range")?
                            .clamp(0.0, 1.0);
                        let x = a.x.to_f64().context("coordinate outside float range")? + t * dx;
                        let y = a.y.to_f64().context("coordinate outside float range")? + t * dy;
                        distance = distance.min(
                            (point.x.to_f64().context("coordinate outside float range")? - x)
                                .hypot(
                                    point.y.to_f64().context("coordinate outside float range")? - y,
                                )
                                / 1e6,
                        );
                    }
                    if distance > edge["max_distance_mm"].as_f64().unwrap() {
                        advisories.push(merged(json!({"kind":"connector_edge_distance","ref":r,"distance_mm":distance}),edge));
                        deduct(&mut score, &edge["deduction"])?;
                    }
                }
            }
        }
    }
    // Python's max(0, score) preserves the integer zero when a deduction exhausts the score.
    if score.as_f64().is_some_and(|v| v <= 0.0) {
        score = json!(0);
    }
    Ok((associations, advisories, score))
}
fn binding(fp: &Value) -> Value {
    json!({"reference":fp["ref"],"uuid":fp["uuid"],"fpid":fp.get("fpid").cloned().unwrap_or(json!("")),"side":fp["side"],"position_nm":fp["position_nm"],"rotation_deg":fp["rotation_deg"]})
}
fn natural(r: &str) -> (String, Int, String) {
    let head = r.trim_end_matches(|c: char| c.is_ascii_digit());
    let tail = &r[head.len()..];
    (
        head.to_owned(),
        if tail.is_empty() {
            (-1).into()
        } else {
            tail.parse().unwrap()
        },
        r.to_owned(),
    )
}
pub fn advisory_regions(c: &Value, regions: &Value, fixed: &BTreeSet<String>) -> Result<Value> {
    validate_capture(c)?;
    let mut by = BTreeMap::new();
    for fp in c["footprints"].as_array().unwrap() {
        let r = fp["ref"].as_str().unwrap();
        ensure!(
            (fp["side"] == "F" || fp["side"] == "B") && fp["courtyards"].is_object(),
            "{r}: mounting side and courtyards required"
        );
        ensure!(
            fp["courtyards"].get(fp["side"].as_str().unwrap()).is_some(),
            "{r}: mounting-side courtyard missing"
        );
        let mut cs = BTreeMap::new();
        for (s, c) in fp["courtyards"].as_object().unwrap() {
            ensure!(
                s == "F" || s == "B",
                "{r}: unsupported courtyard side {s:?}"
            );
            cs.insert(
                s.clone(),
                geometry::courtyard_bounds(c)
                    .map_err(|e| anyhow::anyhow!("{r} {s} courtyard not qualified: {e}"))?
                    .0,
            );
        }
        by.insert(r.to_owned(), (fp, cs));
    }
    let mut refs: Vec<_> = by.keys().cloned().collect();
    refs.sort_by_key(|r| natural(r));
    let mut rows = Vec::new();
    for region in regions.as_array().context("advisory regions required")? {
        let (side, polygons, subject) = if let Some(shadow) = region.get("shadow_of") {
            let r = shadow["reference"]
                .as_str()
                .context("shadow reference required")?;
            let (fp, cs) = by.get(r).context("shadow reference absent")?;
            let b = geometry::bounds(&cs[fp["side"].as_str().unwrap()])?;
            let m = exact::integer(&shadow["margin_nm"])?;
            let expanded = Bounds {
                x0: &b.x0 - &m,
                y0: &b.y0 - &m,
                x1: &b.x1 + &m,
                y1: &b.y1 + &m,
            };
            let mut subject = binding(fp);
            subject["courtyard_box_nm"] = json!([
                exact::integer_value(&b.x0),
                exact::integer_value(&b.y0),
                exact::integer_value(&b.x1),
                exact::integer_value(&b.y1)
            ]);
            (
                if fp["side"] == "F" { "B" } else { "F" }.to_owned(),
                vec![geometry::rectangle(&expanded)],
                Some(subject),
            )
        } else {
            let subject = region
                .get("subject")
                .map(|r| {
                    by.get(r.as_str().unwrap())
                        .map(|(fp, _)| binding(fp))
                        .context("subject reference absent")
                })
                .transpose()?;
            (
                region["side"]
                    .as_str()
                    .context("region side required")?
                    .to_owned(),
                policy::obstacle_polygons(&region["polygons"])?,
                subject,
            )
        };
        let limit = exact::integer(&region["proximity_nm"])?;
        let mut intruders = Vec::new();
        let mut near = Vec::new();
        for r in &refs {
            if subject.as_ref().is_some_and(|s| s["reference"] == *r) {
                continue;
            }
            let Some(ps) = by[r].1.get(&side) else {
                continue;
            };
            let item = json!({"reference":r,"fixed":fixed.contains(r)});
            if ps
                .iter()
                .any(|p| polygons.iter().any(|q| geometry::positive_overlap(p, q)))
            {
                intruders.push(item);
            } else {
                let mut nearest: Option<Rat> = None;
                for p in ps {
                    for q in &polygons {
                        for i in 0..p.len() {
                            for j in 0..q.len() {
                                let d = geometry::segment_distance2(
                                    &p[i],
                                    &p[(i + 1) % p.len()],
                                    &q[j],
                                    &q[(j + 1) % q.len()],
                                );
                                nearest = Some(match nearest {
                                    Some(n) => n.min(d),
                                    None => d,
                                });
                            }
                        }
                    }
                }
                if let Some(n) = nearest.filter(|n| n <= &Rat::from_integer(&limit * &limit)) {
                    let mut item = item;
                    item["distance_nm"] = exact::integer_value(&exact::isqrt(&exact::floor(&n)));
                    near.push(item);
                }
            }
        }
        let mut row = json!({"id":region["id"],"kind":if region.get("shadow_of").is_some(){"shadow"}else{"absolute"},"source":region["source"],"enforcement":"advisory","side":side,"polygons":crate::groups::polygon_values(&polygons),"proximity_nm":region["proximity_nm"],"status":if !intruders.is_empty()||!near.is_empty(){"info"}else{"pass"},"intruders":intruders,"near":near});
        if let Some(c) = region.get("concern") {
            row["concern"] = c.clone();
        }
        if let Some(s) = subject {
            row["subject"] = s;
        }
        if region.get("shadow_of").is_some() {
            row["margin_nm"] = region["shadow_of"]["margin_nm"].clone();
        }
        rows.push(row);
    }
    Ok(
        json!({"schema":"agent-ee.advisory-regions/1","board_sha256":c["board_sha256"],"search":"never-pruned","regions":rows}),
    )
}

fn inner(c: &Value) -> Result<Vec<Polygon>> {
    parse_polygons(c.get("inner_polygons").unwrap_or(&c["polygons"]))
}
fn outline(c: &Value) -> Result<Polygon> {
    let mut ps = geometry::qualified_polygons(&c["outline"])?;
    ensure!(ps.len() == 1, "single closed outline required");
    Ok(ps.remove(0))
}
fn fixed_refs(c: &Value, p: &Value) -> Result<BTreeSet<String>> {
    let mut fixed = policy::strings(&p["fixed_refs"], "fixed_refs")?;
    for fp in c["footprints"].as_array().context("footprints required")? {
        if fp["locked"].as_bool().unwrap_or(false) {
            fixed.insert(fp["ref"].as_str().context("reference required")?.to_owned());
        }
    }
    Ok(fixed)
}
fn proof_failure(
    c: &Value,
    p: &Value,
    proof: Value,
    reason: &str,
    binding: Value,
) -> Result<Value> {
    let mut failure = crate::packing::failure(c, p, "packing_infeasible", "defect", 0, reason)?;
    failure["proof"] = proof;
    Ok(policy::soft_proof(failure, &binding))
}
fn hull(points: impl IntoIterator<Item = Point>) -> Polygon {
    let ps: Vec<_> = points
        .into_iter()
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect();
    let half = |ps: Vec<Point>| {
        let mut chain: Vec<Point> = Vec::new();
        for p in ps {
            while chain.len() >= 2
                && geometry::cross(&chain[chain.len() - 2], &chain[chain.len() - 1], &p)
                    <= Int::zero()
            {
                chain.pop();
            }
            chain.push(p);
        }
        chain.pop();
        chain
    };
    let mut out = half(ps.clone());
    out.extend(half(ps.into_iter().rev().collect()));
    out
}
fn extent(points: impl IntoIterator<Item = Point>) -> Result<[Rat; 2]> {
    let h = hull(points);
    ensure!(h.len() >= 3, "courtyard extent is degenerate");
    let width = geometry::edges(&h)
        .map(|(a, b)| {
            let cross = h.iter().map(|p| geometry::cross(a, b, p)).max().unwrap();
            Rat::new(&cross * &cross, exact::squared_distance(a, b))
        })
        .min()
        .unwrap();
    let diameter = h
        .iter()
        .flat_map(|a| h.iter().map(move |b| exact::squared_distance(a, b)))
        .max()
        .unwrap();
    Ok([width, Rat::from_integer(diameter)])
}
pub fn intrinsic_proof(c: &Value, p: &Value) -> Result<Option<Value>> {
    let outline = outline(c)?;
    let bands = crate::constraints::overhang_bands(
        p.get("fixed_overhangs").unwrap_or(&json!([])),
        &outline,
    )?;
    let mut stripped = p.clone();
    stripped["gap_nm"] = json!(0);
    let binding = policy::binding_constraints(&stripped, &Int::zero(), &[])?;
    let fps = c["footprints"].as_array().context("footprints required")?;
    for side in ["F", "B"] {
        let mut required = Rat::zero();
        for fp in fps {
            if let Some(cs) = fp["courtyards"].get(side) {
                for poly in inner(cs)? {
                    required += if bands.contains_key(fp["ref"].as_str().unwrap()) {
                        geometry::intersection_area(&poly, &outline)
                    } else {
                        crate::constraints::access_minimum_inside_area(
                            fp,
                            std::slice::from_ref(&poly),
                        )?
                    };
                }
            }
        }
        let board = area(&outline);
        if required > board {
            let proof = json!({"kind":"courtyard_area","side":side,"board_area_nm2":exact::rational_value(&board),"required_courtyard_area_nm2":exact::rational_value(&required)});
            return Ok(Some(proof_failure(
                c,
                p,
                proof,
                &format!("{side} courtyard area exceeds board area"),
                binding,
            )?));
        }
    }
    let board = extent(outline)?;
    let mut fps: Vec<_> = fps.iter().collect();
    fps.sort_by_key(|fp| fp["ref"].as_str().unwrap());
    for fp in fps {
        let r = fp["ref"].as_str().unwrap();
        if bands.contains_key(r) || crate::constraints::access_overhangs_courtyard(fp)? {
            continue;
        }
        let mut ps = Vec::new();
        for cs in fp["courtyards"]
            .as_object()
            .context("courtyards required")?
            .values()
        {
            ps.extend(inner(cs)?.into_iter().flatten());
        }
        let part = extent(ps)?;
        for (i, measure) in ["min_width_squared_nm2", "diameter_squared_nm2"]
            .iter()
            .enumerate()
        {
            if part[i] > board[i] {
                let kind = measure.strip_suffix("_squared_nm2").unwrap();
                let mut proof = json!({"kind":kind,"ref":r});
                proof[format!("board_{measure}")] = exact::rational_value(&board[i]);
                proof[format!("footprint_{measure}")] = exact::rational_value(&part[i]);
                return Ok(Some(proof_failure(
                    c,
                    p,
                    proof,
                    &format!(
                        "{r} courtyard {} exceeds the board's",
                        kind.replace('_', " ")
                    ),
                    binding,
                )?));
            }
        }
    }
    Ok(None)
}
fn swept_low(poly: &Polygon, r: &Rat) -> Rat {
    let a = area(poly);
    let mut reverse = poly.clone();
    reverse.reverse();
    if geometry::convex(poly) || geometry::convex(&reverse) {
        a + Rat::from_integer(
            geometry::edges(poly)
                .map(|(a, b)| exact::isqrt(&exact::squared_distance(a, b)))
                .sum::<Int>(),
        ) * r
            + Rat::from_integer(3.into()) * r * r
    } else {
        let b = geometry::bounds(std::slice::from_ref(poly)).expect("qualified nonempty polygon");
        a + Rat::from_integer(2 * (&b.x1 - &b.x0).max(&b.y1 - &b.y0)) * r
    }
}
fn swept_high(poly: &Polygon, r: &Rat) -> Rat {
    area(poly)
        + Rat::from_integer(
            geometry::edges(poly)
                .map(|(a, b)| exact::isqrt(&exact::squared_distance(a, b)) + 1)
                .sum::<Int>(),
        ) * r
        + Rat::new((poly.len() * 11).into(), 7.into()) * r * r
}
fn inside(p: &Polygon, o: &Polygon) -> bool {
    geometry::intersection_area(p, o) == area(p)
}
#[derive(Clone)]
struct Blocker {
    name: String,
    side: String,
    polygons: Vec<Polygon>,
}
fn region_proof(
    c: &Value,
    o: &Polygon,
    gs: &Value,
    fixed: &BTreeSet<String>,
    p: &Value,
    sourced_only: bool,
) -> Result<Option<Value>> {
    let fps = c["footprints"].as_array().context("footprints required")?;
    let by: BTreeMap<_, _> = fps
        .iter()
        .map(|fp| (fp["ref"].as_str().unwrap(), fp))
        .collect();
    let mut regions = Vec::new();
    for g in gs.as_array().into_iter().flatten() {
        if g.get("region").is_some()
            && (!sourced_only
                || crate::groups::constraint_source(g, "region")?["enforcement"] == "hard")
        {
            regions.push((g, crate::groups::region(g)?));
        }
    }
    let r = Rat::new(exact::integer(&p["gap_nm"])?, 2.into());
    let mut gapped = Vec::new();
    for fp in fps
        .iter()
        .filter(|fp| fixed.contains(fp["ref"].as_str().unwrap()))
    {
        let mut cs: Vec<_> = fp["courtyards"].as_object().unwrap().iter().collect();
        cs.sort_by_key(|(s, _)| *s);
        for (side, cs) in cs {
            gapped.push(Blocker {
                name: fp["ref"].as_str().unwrap().to_owned(),
                side: side.clone(),
                polygons: inner(cs)?,
            });
        }
    }
    for (i, obstacle) in p["obstacles"]
        .as_array()
        .context("obstacles required")?
        .iter()
        .enumerate()
    {
        let name = format!("obstacle {i}");
        if !sourced_only
            || policy::classify(obstacle, "provenance", &name, None)?["enforcement"] == "hard"
        {
            gapped.push(Blocker {
                name,
                side: obstacle["side"].as_str().unwrap().to_owned(),
                polygons: policy::obstacle_polygons(&obstacle["polygons"])?,
            });
        }
    }
    let mut others = Vec::new();
    let mut i = 0;
    for a in c["rule_areas"].as_array().into_iter().flatten() {
        if a["owner"] != "board" || a["forbid"]["footprints"] != true {
            continue;
        }
        for side in ["F", "B"] {
            if a["layers"].as_array().is_some_and(|ls| {
                ls.contains(&json!(format!("{side}.Cu"))) || ls.contains(&json!("*.Cu"))
            }) {
                others.push(Blocker {
                    name: format!("rule area {i}"),
                    side: side.to_owned(),
                    polygons: geometry::qualified_polygons(&a["outline"])?,
                });
                i += 1;
            }
        }
    }
    for fp in fps
        .iter()
        .filter(|fp| fixed.contains(fp["ref"].as_str().unwrap()))
    {
        for k in fp["packing_keepouts"].as_array().into_iter().flatten() {
            others.push(Blocker {
                name: format!("{} keepout", fp["ref"].as_str().unwrap()),
                side: k["side"]
                    .as_str()
                    .context("keepout side required")?
                    .to_owned(),
                polygons: parse_polygons(&k["polygons"])?,
            });
        }
    }
    let disjoint = |candidates: Vec<&Blocker>, within: bool| {
        let mut kept: BTreeMap<String, Vec<(String, Polygon)>> =
            BTreeMap::from([("F".into(), Vec::new()), ("B".into(), Vec::new())]);
        for candidate in candidates {
            for poly in &candidate.polygons {
                let ks = kept
                    .get_mut(&candidate.side)
                    .expect("qualified blocker side");
                if (!within || inside(poly, o))
                    && ks
                        .iter()
                        .all(|(_, q)| geometry::intersection_area(poly, q).is_zero())
                {
                    ks.push((candidate.name.clone(), poly.clone()));
                }
            }
        }
        kept
    };
    let plain = disjoint(gapped.iter().chain(others.iter()).collect(), true);
    let grown = disjoint(gapped.iter().collect(), false);
    for (g, region) in &regions {
        let contained: Vec<_> = regions
            .iter()
            .filter(|(_, q)| {
                q == region || (region.len() == 1 && q.iter().all(|p| inside(p, &region[0])))
            })
            .collect();
        let mut refs: BTreeSet<String> = contained
            .iter()
            .flat_map(|(g, _)| {
                g["references"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|r| r.as_str().unwrap().to_owned())
            })
            .filter(|r| !fixed.contains(r))
            .collect();
        if !region.iter().all(|q| inside(q, o)) {
            let mut kept = BTreeSet::new();
            for r in refs {
                if !crate::constraints::access_overhangs_courtyard(by[r.as_str()])? {
                    kept.insert(r);
                }
            }
            refs = kept;
        }
        let board: Rat = region
            .iter()
            .map(|q| geometry::intersection_area(q, o))
            .sum();
        for side in ["F", "B"] {
            let courtyards = refs
                .iter()
                .filter_map(|r| by[r.as_str()]["courtyards"].get(side))
                .map(inner)
                .collect::<Result<Vec<_>>>()?;
            let required: Rat = courtyards.iter().flatten().map(area).sum();
            if required.is_zero() {
                continue;
            }
            let mut group_ids: Vec<_> = contained.iter().map(|(g, _)| g["id"].clone()).collect();
            group_ids.sort_by(|a, b| a.as_str().cmp(&b.as_str()));
            let mut sorted = contained.clone();
            sorted.sort_by_key(|(g, _)| g["id"].as_str().unwrap());
            let mut bindings = Vec::new();
            for (g, _) in sorted {
                let mut b = crate::groups::constraint_source(g, "region")?;
                b["constraint"] = json!(format!("group {} region", g["id"].as_str().unwrap()));
                bindings.push(b);
            }
            let record = json!({"group":g["id"],"side":side,"groups":group_ids,"references":refs,"binding_regions":bindings});
            let blocked = |bs: &Vec<(String, Polygon)>| {
                let mut total = Rat::zero();
                let mut names = BTreeSet::new();
                for (name, poly) in bs {
                    let a: Rat = region
                        .iter()
                        .map(|q| geometry::intersection_area(poly, q))
                        .sum();
                    if !a.is_zero() {
                        total += a;
                        names.insert(name.clone());
                    }
                }
                (total, names)
            };
            let (block, names) = blocked(&plain[side]);
            if required > &board - &block {
                return Ok(Some(merged(
                    json!({"kind":"region_capacity","region_board_area_nm2":exact::rational_value(&board),"blocked_area_nm2":exact::rational_value(&block),"blocked_by":names,"available_area_nm2":exact::rational_value(&(&board-&block)),"required_courtyard_area_nm2":exact::rational_value(&required)}),
                    &record,
                )));
            }
            if r.is_zero() {
                continue;
            }
            let swept: Rat = courtyards
                .iter()
                .map(|ps| {
                    if ps.len() == 1 {
                        swept_low(&ps[0], &r)
                    } else {
                        ps.iter().map(area).sum()
                    }
                })
                .sum();
            let space: Rat = region.iter().map(|q| swept_high(q, &r)).sum();
            let (block, names) = blocked(&grown[side]);
            if swept > &space - &block {
                return Ok(Some(merged(
                    json!({"kind":"region_capacity_gap","gap_nm":p["gap_nm"],"grown_region_area_upper_nm2":exact::rational_value(&space),"blocked_area_nm2":exact::rational_value(&block),"blocked_by":names,"available_area_nm2":exact::rational_value(&(&space-&block)),"required_grown_courtyard_area_nm2":exact::rational_value(&swept)}),
                    &record,
                )));
            }
        }
    }
    Ok(None)
}

fn slabs(poly: &Polygon) -> Option<Vec<Bounds>> {
    if geometry::edges(poly).any(|(a, b)| a.x != b.x && a.y != b.y) {
        return None;
    }
    let flats: Vec<_> = geometry::edges(poly)
        .filter(|(a, b)| a.y == b.y)
        .map(|(a, b)| {
            (
                a.x.clone().min(b.x.clone()),
                a.x.clone().max(b.x.clone()),
                a.y.clone(),
            )
        })
        .collect();
    let xs: Vec<_> = poly
        .iter()
        .map(|p| p.x.clone())
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect();
    let mut out = Vec::new();
    for pair in xs.windows(2) {
        let mut ys: Vec<_> = flats
            .iter()
            .filter(|(lo, hi, _)| lo <= &pair[0] && &pair[1] <= hi)
            .map(|(_, _, y)| y.clone())
            .collect();
        ys.sort();
        for ys in ys.chunks_exact(2) {
            out.push(Bounds {
                x0: pair[0].clone(),
                x1: pair[1].clone(),
                y0: ys[0].clone(),
                y1: ys[1].clone(),
            });
        }
    }
    Some(out)
}
fn union_area(rects: &[Bounds]) -> Int {
    let xs: Vec<_> = rects
        .iter()
        .flat_map(|b| [b.x0.clone(), b.x1.clone()])
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect();
    let mut total = Int::zero();
    for xs in xs.windows(2) {
        let mut ys: Vec<_> = rects
            .iter()
            .filter(|b| b.x0 <= xs[0] && xs[0] < b.x1 && b.y0 < b.y1)
            .map(|b| (b.y0.clone(), b.y1.clone()))
            .collect();
        ys.sort();
        let mut top: Option<Int> = None;
        for (y0, y1) in ys {
            let lo = match &top {
                Some(top) => top.clone().max(y0),
                None => y0,
            };
            if lo < y1 {
                total += (&xs[1] - &xs[0]) * (&y1 - lo);
            }
            top = Some(match top {
                Some(top) => top.max(y1),
                None => y1,
            });
        }
    }
    total
}
fn admissible(b: &Bounds, forbid: &[Bounds]) -> Vec<Bounds> {
    if b.x0 > b.x1 || b.y0 > b.y1 {
        return Vec::new();
    }
    let mut xs: BTreeSet<_> = [b.x0.clone(), b.x1.clone()].into_iter().collect();
    xs.extend(
        forbid
            .iter()
            .flat_map(|f| [f.x0.clone(), f.x1.clone()])
            .filter(|x| &b.x0 < x && x < &b.x1),
    );
    let xs: Vec<_> = xs.into_iter().collect();
    let elements: Vec<_> = xs
        .iter()
        .map(|x| (x.clone(), x.clone()))
        .chain(xs.windows(2).map(|p| (p[0].clone(), p[1].clone())))
        .collect();
    let mut pieces = Vec::new();
    for (lo, hi) in elements {
        let mid = &lo + &hi;
        let mut cur = b.y0.clone();
        let mut spans: Vec<_> = forbid
            .iter()
            .filter(|f| &f.x0 * 2 < mid && mid < &f.x1 * 2)
            .map(|f| (f.y0.clone(), f.y1.clone()))
            .collect();
        spans.sort();
        for (b0, b1) in spans {
            if b0 >= cur {
                if cur <= b.y1 {
                    pieces.push(Bounds {
                        x0: lo.clone(),
                        x1: hi.clone(),
                        y0: cur.clone(),
                        y1: b0.min(b.y1.clone()),
                    });
                }
                cur = b1;
            } else if b1 > cur {
                cur = b1;
            }
        }
        if cur <= b.y1 {
            pieces.push(Bounds {
                x0: lo,
                x1: hi,
                y0: cur,
                y1: b.y1.clone(),
            });
        }
    }
    pieces
}
fn square_cover_area(b: &Bounds, blockers: &[Bounds], a: &Int) -> Int {
    let forbid: Vec<_> = blockers
        .iter()
        .filter(|f| f.x0 < f.x1 && f.y0 < f.y1)
        .map(|f| Bounds {
            x0: &f.x0 - a,
            y0: &f.y0 - a,
            x1: f.x1.clone(),
            y1: f.y1.clone(),
        })
        .collect();
    let origins = Bounds {
        x0: b.x0.clone(),
        y0: b.y0.clone(),
        x1: &b.x1 - a,
        y1: &b.y1 - a,
    };
    let covers: Vec<_> = admissible(&origins, &forbid)
        .into_iter()
        .map(|b| Bounds {
            x1: b.x1 + a,
            y1: b.y1 + a,
            ..b
        })
        .collect();
    union_area(&covers)
}
fn probe_cover_area(b: &Bounds, blockers: &[Bounds], inner: &Int, outer: &Int) -> Int {
    let solid: Vec<_> = blockers
        .iter()
        .filter(|b| b.x0 < b.x1 && b.y0 < b.y1)
        .cloned()
        .collect();
    let forbid: Vec<_> = solid
        .iter()
        .flat_map(|f| {
            [
                Bounds {
                    x0: &f.x0 - inner,
                    x1: &f.x1 + inner,
                    ..f.clone()
                },
                Bounds {
                    y0: &f.y0 - inner,
                    y1: &f.y1 + inner,
                    ..f.clone()
                },
            ]
        })
        .collect();
    let origins = Bounds {
        x0: &b.x0 + inner,
        y0: &b.y0 + inner,
        x1: &b.x1 - inner,
        y1: &b.y1 - inner,
    };
    let mut reach: Vec<_> = admissible(&origins, &forbid)
        .into_iter()
        .map(|f| Bounds {
            x0: (f.x0 - outer).max(b.x0.clone()),
            y0: (f.y0 - outer).max(b.y0.clone()),
            x1: (f.x1 + outer).min(b.x1.clone()),
            y1: (f.y1 + outer).min(b.y1.clone()),
        })
        .collect();
    let solid: Vec<_> = solid
        .into_iter()
        .map(|f| Bounds {
            x0: f.x0.max(b.x0.clone()),
            y0: f.y0.max(b.y0.clone()),
            x1: f.x1.min(b.x1.clone()),
            y1: f.y1.min(b.y1.clone()),
        })
        .collect();
    reach.extend(solid.clone());
    union_area(&reach) - union_area(&solid)
}
fn complement_boxes(p: &Polygon) -> Option<Vec<Bounds>> {
    let ss = slabs(p)?;
    let y0 = p.iter().map(|p| p.y.clone()).min()?;
    let y1 = p.iter().map(|p| p.y.clone()).max()?;
    let mut columns: BTreeMap<(Int, Int), Vec<(Int, Int)>> = BTreeMap::new();
    for b in ss {
        columns.entry((b.x0, b.x1)).or_default().push((b.y0, b.y1));
    }
    let mut out = Vec::new();
    for ((x0, x1), mut spans) in columns {
        let mut cur = y0.clone();
        spans.sort();
        for (b0, b1) in spans {
            if b0 > cur {
                out.push(Bounds {
                    x0: x0.clone(),
                    x1: x1.clone(),
                    y0: cur.clone(),
                    y1: b0,
                });
            }
            cur = cur.max(b1);
        }
        if cur < y1 {
            out.push(Bounds {
                x0,
                x1,
                y0: cur,
                y1: y1.clone(),
            });
        }
    }
    Some(out)
}
fn grown_boxes(bs: &[Bounds], r: &Int) -> Vec<Bounds> {
    bs.iter()
        .flat_map(|b| {
            [
                Bounds {
                    x0: &b.x0 - r,
                    x1: &b.x1 + r,
                    ..b.clone()
                },
                Bounds {
                    y0: &b.y0 - r,
                    y1: &b.y1 + r,
                    ..b.clone()
                },
            ]
        })
        .collect()
}
fn square_size(p: &Polygon) -> Int {
    let Some(holes) = complement_boxes(p) else {
        return Int::zero();
    };
    let b = geometry::bounds(std::slice::from_ref(p)).unwrap();
    let limit = (&b.x1 - &b.x0).min(&b.y1 - &b.y0);
    if holes.is_empty() {
        return limit;
    }
    let mut sizes = BTreeSet::new();
    for axis in [0, 1] {
        for a in p {
            for b in p {
                let d = if axis == 0 {
                    (&a.x - &b.x).abs()
                } else {
                    (&a.y - &b.y).abs()
                };
                if d > Int::zero() && d <= limit {
                    sizes.insert(d);
                }
            }
        }
    }
    for a in sizes.into_iter().rev() {
        if Rat::from_integer(square_cover_area(&b, &holes, &a)) == area(p) {
            return a;
        }
    }
    Int::zero()
}
#[derive(Clone)]
struct CapacitySide {
    blocked: Vec<String>,
    rows: Vec<(bool, Value)>,
}
fn board_capacity(
    c: &Value,
    o: &Polygon,
    fixed: &BTreeSet<String>,
    p: &Value,
    margin: &Int,
    sourced_only: bool,
) -> Result<BTreeMap<String, CapacitySide>> {
    let r = exact::integer(&p["gap_nm"])?;
    let t = margin * 2 - &r;
    let doubled: Polygon = o
        .iter()
        .map(|p| Point {
            x: &p.x * 2,
            y: &p.y * 2,
        })
        .collect();
    let b = geometry::bounds(std::slice::from_ref(&doubled))?;
    let container = Bounds {
        x0: &b.x0 + &t,
        y0: &b.y0 + &t,
        x1: &b.x1 - &t,
        y1: &b.y1 - &t,
    };
    let edge = if t >= Int::zero() {
        complement_boxes(&doubled)
    } else {
        None
    };
    let fps = c["footprints"].as_array().context("footprints required")?;
    let mut sides = BTreeMap::new();
    for side in ["F", "B"] {
        let mut sources = Vec::new();
        for fp in fps {
            if fixed.contains(fp["ref"].as_str().unwrap()) {
                if let Some(cs) = fp["courtyards"].get(side) {
                    sources.push((fp["ref"].as_str().unwrap().to_owned(), inner(cs)?));
                }
            }
        }
        for (i, obstacle) in p["obstacles"]
            .as_array()
            .context("obstacles required")?
            .iter()
            .enumerate()
        {
            let name = format!("obstacle {i}");
            if obstacle["side"] == side
                && (!sourced_only
                    || policy::classify(obstacle, "provenance", &name, None)?["enforcement"]
                        == "hard")
            {
                sources.push((name, policy::obstacle_polygons(&obstacle["polygons"])?));
            }
        }
        let mut blockers = Vec::new();
        let mut names = BTreeSet::new();
        for (name, ps) in sources {
            for poly in ps {
                let double: Polygon = poly
                    .iter()
                    .map(|p| Point {
                        x: &p.x * 2,
                        y: &p.y * 2,
                    })
                    .collect();
                let grown: Vec<_> = grown_boxes(&slabs(&double).unwrap_or_default(), &r)
                    .into_iter()
                    .filter(|b| b.meets(&container))
                    .collect();
                if !grown.is_empty() {
                    blockers.extend(grown);
                    names.insert(name.clone());
                }
            }
        }
        if let Some(edge) = &edge {
            blockers.extend(grown_boxes(edge, &t));
        }
        let mut parts = Vec::new();
        let mut sorted: Vec<_> = fps.iter().collect();
        sorted.sort_by_key(|fp| fp["ref"].as_str().unwrap());
        for fp in sorted {
            let r = fp["ref"].as_str().unwrap();
            if fixed.contains(r) || crate::constraints::access_overhangs_courtyard(fp)? {
                continue;
            }
            if let Some(cs) = fp["courtyards"].get(side) {
                let ps = inner(cs)?;
                let size = ps
                    .iter()
                    .map(square_size)
                    .min()
                    .context("empty courtyard")?;
                let grown = if ps.len() == 1 {
                    swept_low(&ps[0], &Rat::new(exact::integer(&p["gap_nm"])?, 2.into()))
                } else {
                    ps.iter().map(area).sum()
                };
                parts.push((r.to_owned(), size, grown));
            }
        }
        if parts.is_empty() {
            continue;
        }
        let mut sizes: BTreeSet<_> = parts.iter().map(|(_, s, _)| s.clone()).collect();
        sizes.insert(Int::zero());
        let mut rows = Vec::new();
        for a in sizes {
            let required: Rat = parts
                .iter()
                .filter(|(_, s, _)| s >= &a)
                .map(|(_, _, a)| a.clone())
                .sum();
            let available = if a.is_zero() {
                square_cover_area(&container, &blockers, &Int::zero())
            } else {
                let twice = &a * &a * 2;
                let root = exact::isqrt(&twice);
                let ceil = &root + if &root * &root < twice { 1 } else { 0 };
                probe_cover_area(&container, &blockers, &(&a + &r), &(ceil + &r))
            };
            let available = Rat::new(available, 4.into());
            let refs: Vec<_> = parts
                .iter()
                .filter(|(_, s, _)| s >= &a)
                .map(|(r, _, _)| r.clone())
                .collect();
            let mut row = json!({"square_nm":exact::integer_value(&a),"references":refs,"required_grown_courtyard_area_nm2":exact::rational_value(&required),"available_area_upper_nm2":exact::rational_value(&available)});
            if !available.is_zero() {
                let ratio = &required / &available;
                row["ratio"] = exact::rational_value(&ratio);
                let floor = exact::floor(&(&ratio * Rat::from_integer(1_000_000.into())));
                let whole = &floor / 1_000_000;
                let frac = (&floor % Int::from(1_000_000)).abs().to_string();
                row["ratio_display"] = json!(format!("{whole}.{frac:0>6}"));
            }
            rows.push((required > available, row));
        }
        sides.insert(
            side.to_owned(),
            CapacitySide {
                blocked: names.into_iter().collect(),
                rows,
            },
        );
    }
    Ok(sides)
}
fn capacity_diagnostic(
    sides: &BTreeMap<String, CapacitySide>,
    p: &Value,
    margin: &Int,
) -> Result<Value> {
    let mut ss = serde_json::Map::new();
    for (side, s) in sides {
        ss.insert(side.clone(),json!({"blocked_by":s.blocked,"free_area":s.rows[0].1,"fragmentation":s.rows[1..].iter().map(|(_,r)|r.clone()).collect::<Vec<_>>(),"binding_constraints":policy::binding_constraints(p,margin,&s.blocked)?}));
    }
    Ok(
        json!({"basis":"diagnostic-only","method":"gapped-free-area/1","gap_nm":p["gap_nm"],"min_courtyard_edge_nm":exact::integer_value(margin),"sides":ss}),
    )
}
fn capacity_impossibility(sides: &BTreeMap<String, CapacitySide>, p: &Value) -> Option<Value> {
    for side in ["F", "B"] {
        if let Some(s) = sides.get(side) {
            for (exceeded, row) in &s.rows {
                if *exceeded {
                    return Some(merged(
                        json!({"kind":if row["square_nm"]==0{"board_capacity_gap"}else{"board_capacity_fragmented"},"side":side,"gap_nm":p["gap_nm"],"blocked_by":s.blocked}),
                        row,
                    ));
                }
            }
        }
    }
    None
}
pub struct CapacityProofs {
    pub early: Option<Value>,
    pub deferred: Option<Value>,
    pub diagnostic: Value,
}

pub fn capacity_proofs(c: &Value, p: &Value, defer: bool) -> Result<CapacityProofs> {
    let o = outline(c)?;
    let fixed = fixed_refs(c, p)?;
    let empty = json!([]);
    let gs = p
        .get("groups")
        .map(|g| crate::groups::validate(g, None))
        .transpose()?
        .unwrap_or(empty);
    let margin = policy::edge_margin_nm(p)?;
    let mut region_failure = None;
    if let Some(mut proof) = region_proof(c, &o, &gs, &fixed, p, false)? {
        if let Some(independent) = region_proof(c, &o, &gs, &fixed, p, true)? {
            proof = independent;
        }
        if proof.get("gap_nm").is_some_and(|n| n != 0)
            && p["constraint_provenance"].get("gap_nm").is_none()
        {
            let mut no_gap = p.clone();
            no_gap["gap_nm"] = json!(0);
            let independent = match region_proof(c, &o, &gs, &fixed, &no_gap, true)? {
                Some(proof) => Some(proof),
                None => region_proof(c, &o, &gs, &fixed, &no_gap, false)?,
            };
            if let Some(independent) = independent {
                proof = independent;
            }
        }
        let reason = format!(
            "group {} region cannot hold its {} courtyards",
            proof["group"].as_str().unwrap(),
            proof["side"].as_str().unwrap()
        );
        let mut bound_policy = p.clone();
        bound_policy["gap_nm"] = proof.get("gap_nm").cloned().unwrap_or(json!(0));
        let blocked = proof["blocked_by"]
            .as_array()
            .unwrap()
            .iter()
            .map(|r| r.as_str().unwrap().to_owned())
            .collect::<Vec<_>>();
        let mut binding = policy::binding_constraints(&bound_policy, &Int::zero(), &blocked)?;
        binding
            .as_array_mut()
            .unwrap()
            .extend(proof["binding_regions"].as_array().unwrap().clone());
        region_failure = Some(proof_failure(c, p, proof, &reason, binding)?);
    }
    if region_failure
        .as_ref()
        .is_some_and(|r| r["outcome"] == "defect" || p["capacity_proof"] != true)
    {
        return Ok(CapacityProofs { early: region_failure, deferred: None, diagnostic: Value::Null });
    }
    let sides = board_capacity(c, &o, &fixed, p, &margin, false)?;
    let mut capacity = capacity_diagnostic(&sides, p, &margin)?;
    let mut omitted = Vec::new();
    for fp in c["footprints"].as_array().unwrap() {
        let r = fp["ref"].as_str().unwrap();
        if !fixed.contains(r) && crate::constraints::access_overhangs_courtyard(fp)? {
            omitted.push(r.to_owned());
        }
    }
    omitted.sort();
    if !omitted.is_empty() {
        capacity["excluded_access_overhang_refs"] = json!(omitted);
    }
    let mut proof = if defer || p["capacity_proof"] == true {
        capacity_impossibility(&sides, p)
    } else {
        None
    };
    let mut proof_policy = p.clone();
    let mut proof_margin = margin.clone();
    if proof.is_some() {
        let unsourced_gap = !exact::integer(&p["gap_nm"])?.is_zero()
            && p["constraint_provenance"].get("gap_nm").is_none();
        let unsourced_margin = !margin.is_zero()
            && p["constraint_provenance"]
                .get("min_courtyard_edge_mm")
                .is_none();
        let mut advisory_obstacles = false;
        for (i, o) in p["obstacles"].as_array().unwrap().iter().enumerate() {
            advisory_obstacles |=
                policy::classify(o, "provenance", &format!("obstacle {i}"), None)?["enforcement"]
                    == "soft";
        }
        let mut retries = Vec::new();
        let mut no_gap = p.clone();
        no_gap["gap_nm"] = json!(0);
        if advisory_obstacles {
            retries.push((
                if unsourced_gap {
                    no_gap.clone()
                } else {
                    p.clone()
                },
                if unsourced_margin {
                    Int::zero()
                } else {
                    margin.clone()
                },
                true,
            ));
            retries.push((p.clone(), margin.clone(), true));
        }
        if unsourced_gap && unsourced_margin {
            retries.push((no_gap.clone(), Int::zero(), false));
        }
        if unsourced_gap {
            retries.push((no_gap, margin.clone(), false));
        }
        if unsourced_margin {
            retries.push((p.clone(), Int::zero(), false));
        }
        for (rp, rm, sourced) in retries {
            if let Some(retry) =
                capacity_impossibility(&board_capacity(c, &o, &fixed, &rp, &rm, sourced)?, &rp)
            {
                proof = Some(retry);
                proof_policy = rp;
                proof_margin = rm;
                break;
            }
        }
    }
    if let Some(proof) = proof {
        let blocked = proof["blocked_by"]
            .as_array()
            .unwrap()
            .iter()
            .map(|r| r.as_str().unwrap().to_owned())
            .collect::<Vec<_>>();
        let binding = policy::binding_constraints(&proof_policy, &proof_margin, &blocked)?;
        let square = exact::integer(&proof["square_nm"])?;
        let reason = format!(
            "{} courtyards grown by half the gap exceed the board's free area{}",
            proof["side"].as_str().unwrap(),
            if square.is_zero() {
                String::new()
            } else {
                format!(" usable by {square} nm squares")
            }
        );
        let mut failure =
            crate::packing::failure(c, p, "packing_infeasible", "defect", 0, &reason)?;
        failure["proof"] = proof;
        failure["capacity"] = capacity.clone();
        let failure = policy::soft_proof(failure, &binding);
        if defer {
            return Ok(CapacityProofs { early: region_failure, deferred: Some(failure), diagnostic: capacity });
        }
        if failure["outcome"] == "defect" || region_failure.is_none() {
            return Ok(CapacityProofs { early: Some(failure), deferred: None, diagnostic: capacity });
        }
    }
    Ok(CapacityProofs { early: region_failure, deferred: None, diagnostic: capacity })
}

#[cfg(test)]
mod tests {
    use super::*;
    fn rect(x0: i64, y0: i64, x1: i64, y1: i64) -> Bounds {
        Bounds {
            x0: x0.into(),
            y0: y0.into(),
            x1: x1.into(),
            y1: y1.into(),
        }
    }
    #[test]
    fn union_and_closed_admission_keep_zero_width_boundaries() {
        assert_eq!(union_area(&[rect(0, 0, 4, 4), rect(2, 2, 6, 6)]), 28.into());
        let pieces = admissible(&rect(0, 0, 4, 4), &[rect(0, 0, 4, 4)]);
        assert!(
            pieces.iter().any(|b| b.x0 == 0.into()
                && b.x1 == 0.into()
                && b.y0 == 0.into()
                && b.y1 == 4.into())
        );
        assert_eq!(union_area(&pieces), 0.into());
    }
    #[test]
    fn square_cover_detects_fragmentation() {
        let board = rect(0, 0, 10, 10);
        let blockers = [rect(4, 0, 6, 10)];
        assert_eq!(square_cover_area(&board, &blockers, &4.into()), 80.into());
        assert_eq!(square_cover_area(&board, &blockers, &5.into()), 0.into());
    }
    #[test]
    fn nonrectilinear_shapes_never_get_a_spurious_square_proof() {
        let triangle = vec![Point::new(0, 0), Point::new(5, 0), Point::new(0, 5)];
        assert_eq!(square_size(&triangle), 0.into());
        assert_eq!(
            square_size(&geometry::rectangle(&rect(0, 0, 8, 3))),
            3.into()
        );
    }
    #[test]
    fn extent_is_rigid_invariant_and_uses_exact_rationals() {
        let p = geometry::rectangle(&rect(0, 0, 3, 4));
        let a = extent(p.clone()).unwrap();
        let transformed = p
            .iter()
            .map(|p| exact::rotate(p, 1).plus(&Point::new(13, -7)))
            .collect::<Vec<_>>();
        assert_eq!(a, extent(transformed).unwrap());
        assert_eq!(
            a,
            [Rat::from_integer(9.into()), Rat::from_integer(25.into())]
        );
    }
    #[test]
    fn separation_declarations_accept_exact_strings_but_reject_zero() {
        let mut p = json!({"schema":"agent-ee.placement-policy/1","keep_apart":[{"ref":"A","others":["B"],"min_distance_mm":"0.000001"}]});
        assert!(keep_apart_rules(&p).is_ok());
        p["keep_apart"][0]["min_distance_mm"] = json!(0);
        assert!(keep_apart_rules(&p).is_err());
    }
}
