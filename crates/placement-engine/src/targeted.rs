//! Targeted placement proposals, with the same strict improvement gate as packing.
use crate::exact::{self, Int, Point, Rat};
use crate::footprint::{Keepout, PreparedFootprint};
use crate::{blocks, constraints, footprint, geometry, grading, groups, locality, policy};
use anyhow::{Context, Result, ensure};
use num_traits::{ToPrimitive, Zero};
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};

pub fn pad_shapes() -> Value {
    json!({"pcbnew_major":"10","accepted":{"0":"circle","1":"rect","2":"oval","4":"roundrect","5":"chamfered_rect"},"pad_rotation":"quarter-turn","pad_outline_in_geometry":false})
}
fn references(v: &Value, known: &BTreeMap<String, Value>) -> Result<Vec<String>> {
    let mut r = blocks::refs(v)?;
    ensure!(
        !r.is_empty() && r.iter().all(|r| known.contains_key(r)),
        "explicit unique known references required"
    );
    r.sort();
    Ok(r)
}
fn nets(fp: &Value, ground: &BTreeSet<String>) -> BTreeSet<String> {
    fp["pads"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|p| p["net"].as_str())
        .filter(|n| !n.is_empty() && !ground.contains(*n))
        .map(str::to_owned)
        .collect()
}
fn targets(
    parts: &BTreeMap<String, Value>,
    spec: &Value,
    scoring: &Value,
) -> Result<(BTreeMap<String, Point>, Value)> {
    let ground: BTreeSet<_> = blocks::refs(&scoring["ground_nets"])?.into_iter().collect();
    let mut targets = BTreeMap::new();
    match spec["mode"].as_str().unwrap() {
        "decoupling_row" => {
            let anchor = blocks::text(&spec["anchor_ref"])?;
            ensure!(
                blocks::refs(&scoring["ic_refs"])?
                    .iter()
                    .any(|r| r == anchor),
                "row anchor must be a declared IC"
            );
            let rs = references(&spec["capacitor_refs"], parts)?;
            ensure!(
                rs.iter().all(|r| scoring["capacitors"].get(r).is_some()),
                "row capacitors must be explicitly declared to the judge"
            );
            let offset = exact::point(&spec["offset_nm"])?;
            let step = exact::point(&spec["step_nm"])?;
            ensure!(
                rs.len() == 1 || step != Point::zero(),
                "row step must separate multiple capacitors"
            );
            let origin = exact::point(&parts[anchor]["position_nm"])?;
            for (i, r) in rs.into_iter().enumerate() {
                ensure!(
                    !nets(&parts[&r], &ground).is_disjoint(&nets(&parts[anchor], &ground)),
                    "{r}: no shared non-ground net with declared anchor"
                );
                targets.insert(
                    r,
                    Point {
                        x: &origin.x + &offset.x + Int::from(i) * &step.x,
                        y: &origin.y + &offset.y + Int::from(i) * &step.y,
                    },
                );
            }
            Ok((targets, json!({})))
        }
        "net_cluster_seed" => {
            let rs = references(&spec["refs"], parts)?;
            let origin = exact::point(&spec["origin_nm"])?;
            let spacing = exact::point(&spec["spacing_nm"])?;
            ensure!(
                spacing.x > Int::zero() && spacing.y > Int::zero(),
                "spacing_nm needs two integer nanometres > 0"
            );
            let mut parent: BTreeMap<String, String> =
                rs.iter().map(|r| (r.clone(), r.clone())).collect();
            fn root(parent: &BTreeMap<String, String>, r: &str) -> String {
                let mut r = r;
                while parent[r] != r {
                    r = &parent[r];
                }
                r.to_owned()
            }
            let mut owners = BTreeMap::<String, String>::new();
            for r in &rs {
                for net in nets(&parts[r], &ground) {
                    if let Some(peer) = owners.get(&net) {
                        let mut roots = [root(&parent, r), root(&parent, peer)];
                        roots.sort();
                        parent.insert(roots[1].clone(), roots[0].clone());
                    }
                    owners.insert(net, r.clone());
                }
            }
            let mut grouped: Vec<(String, Vec<String>)> = Vec::new();
            for r in rs {
                let k = root(&parent, &r);
                if let Some((_, members)) = grouped.iter_mut().find(|(g, _)| g == &k) {
                    members.push(r);
                } else {
                    grouped.push((k, vec![r]));
                }
            }
            let mut column = 0usize;
            for (_, members) in &grouped {
                let width = exact::isqrt(&Int::from(members.len() - 1))
                    .to_usize()
                    .unwrap()
                    + 1;
                for (i, r) in members.iter().enumerate() {
                    targets.insert(
                        r.clone(),
                        Point {
                            x: &origin.x + Int::from(column + i % width) * &spacing.x,
                            y: &origin.y + Int::from(i / width) * &spacing.y,
                        },
                    );
                }
                column += width + 1;
            }
            Ok((
                targets,
                json!({"clusters":grouped.into_iter().map(|(_,v)|v).collect::<Vec<_>>()}),
            ))
        }
        "bga_fanout" => {
            let r = blocks::text(&spec["bga_ref"])?;
            let fp = parts.get(r).context("unknown BGA reference")?;
            let pads = blocks::rows(&fp["pads"])?;
            let positions: Vec<_> = pads
                .iter()
                .map(|p| exact::point(&p["position_nm"]))
                .collect::<Result<_>>()?;
            let numbers: Vec<_> = pads
                .iter()
                .map(|p| p["number"].as_str().unwrap_or(""))
                .collect();
            ensure!(
                !pads.is_empty()
                    && positions.iter().collect::<BTreeSet<_>>().len() == pads.len()
                    && numbers.iter().collect::<BTreeSet<_>>().len() == pads.len()
                    && numbers.iter().all(|s| !s.is_empty())
                    && pads
                        .iter()
                        .all(|p| p["shape"] == 0 && p["attribute"] == "SMD"),
                "BGA requires unique numbered circular SMD pads"
            );
            let xs: Vec<_> = positions
                .iter()
                .map(|p| p.x.clone())
                .collect::<BTreeSet<_>>()
                .into_iter()
                .collect();
            let ys: Vec<_> = positions
                .iter()
                .map(|p| p.y.clone())
                .collect::<BTreeSet<_>>()
                .into_iter()
                .collect();
            ensure!(
                xs.len() >= 2 && ys.len() >= 2 && xs.len() * ys.len() == pads.len(),
                "BGA pitch requires a complete axis-aligned pad grid"
            );
            let pitches: BTreeSet<Int> = [&xs, &ys]
                .into_iter()
                .flat_map(|a| a.windows(2).map(|p| &p[1] - &p[0]))
                .collect();
            ensure!(pitches.len() == 1, "BGA pitch is not uniform and square");
            let pitch = pitches.into_iter().next().unwrap();
            let offset = exact::integer(&spec["escape_offset_nm"])?;
            ensure!(
                offset > Int::zero() && offset < &pitch / 2,
                "escape offset must be positive and less than half pitch"
            );
            let mut pads = pads.iter().collect::<Vec<_>>();
            pads.sort_by_key(|p| p["number"].as_str().unwrap());
            let mut escapes = Vec::new();
            for p in pads {
                let pnt = exact::point(&p["position_nm"])?;
                let x = &pnt.x
                    + if &pnt.x * 2 >= &xs[0] + xs.last().unwrap() {
                        offset.clone()
                    } else {
                        -&offset
                    };
                let y = &pnt.y
                    + if &pnt.y * 2 >= &ys[0] + ys.last().unwrap() {
                        offset.clone()
                    } else {
                        -&offset
                    };
                escapes.push(json!({"ref":r,"pad":p["number"],"net":p["net"],"position_nm":exact::point_value(&Point{x,y})}));
            }
            Ok((
                targets,
                json!({"pitch_nm":exact::integer_value(&pitch),"escape_targets":escapes,"plan_only":true}),
            ))
        }
        _ => unreachable!(),
    }
}

pub fn plan(capture: &Value, packing_policy: &Value, spec: &Value) -> Result<Value> {
    footprint::validate_capture(capture)?;
    let source = blocks::board(capture)?;
    policy::validate(packing_policy, &source.keys().cloned().collect())?;
    let mode = spec["mode"]
        .as_str()
        .context("expected decoupling_row, net_cluster_seed or bga_fanout mode")?;
    let required: &[&str] = match mode {
        "decoupling_row" => &[
            "judge_policy",
            "anchor_ref",
            "capacitor_refs",
            "offset_nm",
            "step_nm",
        ],
        "net_cluster_seed" => &["judge_policy", "refs", "origin_nm", "spacing_nm"],
        "bga_fanout" => &["judge_policy", "bga_ref", "escape_offset_nm"],
        _ => anyhow::bail!("expected decoupling_row, net_cluster_seed or bga_fanout mode"),
    };
    let mut missing: Vec<_> = required
        .iter()
        .filter(|k| spec.get(**k).is_none())
        .copied()
        .collect();
    missing.sort();
    ensure!(
        missing.is_empty(),
        "targeted request missing required keys: {}",
        missing.join(", ")
    );
    let scoring = &spec["judge_policy"];
    ensure!(
        scoring.is_object(),
        "explicit placement judge policy required"
    );
    let before = grading::judge_mechanical(
        capture,
        Some(scoring),
        packing_policy.get("fixed_overhangs"),
        packing_policy.get("mechanical_reservations"),
    )?;
    let margin = policy::edge_margin_nm(packing_policy)?;
    let mut record = json!({"schema":"agent-ee.packing-plan/1","mode":mode,"board_sha256":capture["board_sha256"],"policy":packing_policy,"request":spec,"judge_policy":scoring,"before":before,"after":null,"judgment":null,"placements":[],"reasons":[],"status":"blocked","outcome":"unknown","steps_used":0,"step_budget":packing_policy["step_budget"],"pad_shapes":pad_shapes(),"min_courtyard_edge_mm":Rat::new(margin.clone(),1_000_000.into()).to_f64().context("edge margin outside float range")?,"authority":"standalone-unsealed-plan"});
    let qualified = match footprint::qualify_capture(capture) {
        Ok(q) => q,
        Err(e) => {
            record["reasons"] = json!([format!("missing coverage: {e}")]);
            return Ok(record);
        }
    };
    let rawparts: BTreeMap<_, _> = qualified
        .footprints
        .iter()
        .map(|fp| Ok((fp.reference.clone(), fp.to_value()?)))
        .collect::<Result<_>>()?;
    let (targets, extra) = targets(&rawparts, spec, scoring)?;
    for (k, v) in extra.as_object().unwrap() {
        record[k] = v.clone();
    }
    let fixed: BTreeSet<_> = blocks::refs(&packing_policy["fixed_refs"])?
        .into_iter()
        .chain(
            qualified
                .footprints
                .iter()
                .filter(|f| f.raw["locked"] == true)
                .map(|f| f.reference.clone()),
        )
        .collect();
    let mut parts: BTreeMap<String, PreparedFootprint> = qualified
        .footprints
        .iter()
        .map(|f| (f.reference.clone(), f.clone()))
        .collect();
    let mut reasons = Vec::<String>::new();
    for (r, p) in &targets {
        if fixed.contains(r) && p != &parts[r].position {
            reasons.push(format!("{r}: held or locked footprint cannot move"));
        } else {
            parts.insert(r.clone(), parts[r].at(p, parts[r].rotation)?);
        }
    }
    let mut obstacles = qualified.board_keepouts.clone();
    for o in blocks::rows(&packing_policy["obstacles"])? {
        obstacles.push(Keepout {
            side: blocks::text(&o["side"])?.into(),
            polygons: policy::obstacle_polygons(&o["polygons"])?,
            name: String::new(),
        });
    }
    let gap = exact::integer(&packing_policy["gap_nm"])?;
    for (r, fp) in &parts {
        if !targets.contains_key(r) {
            continue;
        }
        if !geometry::inside(&fp.position, &qualified.outline)
            || !constraints::fits(fp, &qualified.outline, &margin, &[])?
        {
            reasons.push(format!("{r}: target outside outline or edge margin"));
        }
        let peers: Vec<_> = parts
            .iter()
            .filter(|(other, _)| *other != r)
            .map(|(_, p)| p.clone())
            .collect();
        let mut exact = obstacles.clone();
        for p in &peers {
            exact.extend(p.courtyards.iter().map(|(s, polys)| Keepout {
                side: s.clone(),
                polygons: polys.clone(),
                name: p.reference.clone(),
            }));
        }
        if constraints::near(fp, &exact, &gap)
            || constraints::keepout_blocked(fp, &peers, &obstacles)
            || peers.iter().any(|p| constraints::hole_conflict(fp, p))
        {
            reasons.push(format!("{r}: target conflicts with obstacle or gap"));
        }
        if let Some(escaped) = constraints::copper_escapes(
            fp,
            &qualified.outline,
            &qualified.board_keepouts,
            &peers,
            &qualified.board_pad_keepouts,
        )? {
            reasons.push(match escaped.reason.as_str() {
                "setback_unknown" => {
                    format!("missing coverage: {r}: copper setback unknown (no board max_error_nm)")
                }
                "peer_courtyard" => {
                    format!("{r}: target copper meets a peer courtyard or peer copper")
                }
                "pad_rule_area" => format!("{r}: target pad in a rule area forbidding pads"),
                _ => format!("{r}: target copper outside outline or in keep-out"),
            });
        }
    }
    for escape in record
        .get("escape_targets")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
    {
        if !geometry::inside(&exact::point(&escape["position_nm"])?, &qualified.outline) {
            reasons.push(format!(
                "{} pad {}: escape target outside outline",
                escape["ref"].as_str().unwrap(),
                escape["pad"].as_str().unwrap()
            ));
        }
    }
    let mut proposed = capture.clone();
    proposed["footprints"] = json!(
        qualified
            .footprints
            .iter()
            .map(|fp| parts[&fp.reference].to_value())
            .collect::<Result<Vec<_>>>()?
    );
    let mut after = grading::judge_mechanical(
        &proposed,
        Some(scoring),
        packing_policy.get("fixed_overhangs"),
        packing_policy.get("mechanical_reservations"),
    )?;
    after["basis"] = json!("transformed-proposal-not-native");
    if before["unknown"].as_array().is_some_and(|a| !a.is_empty())
        || after["unknown"].as_array().is_some_and(|a| !a.is_empty())
    {
        reasons.push("missing coverage in placement judge".into());
    }
    if after["verdict"] != "pass" {
        reasons.push("placement judge rejected proposed geometry".into());
    }
    if exact::decimal(&after["score"])? <= exact::decimal(&before["score"])? {
        reasons.push("placement score must strictly improve".into());
    }
    if let Some(g) = packing_policy.get("groups") {
        if !g.as_array().is_some_and(Vec::is_empty)
            && groups::grade(&proposed, g)?["status"] != "pass"
        {
            reasons.push("declared placement groups not satisfied".into());
        }
    }
    if let Some(d) = packing_policy.get("electrical_locality") {
        record["locality_grading"] = locality::grade(&proposed, d)?;
        if record["locality_grading"]["status"] != "pass"
            && record["locality_grading"]["status"] != "info"
        {
            reasons.push("declared electrical locality not satisfied".into());
        }
    }
    if mode == "bga_fanout" {
        reasons.push("plan only: no autonomous whole-board or copper apply".into());
    }
    record["placements"]=json!(parts.iter().map(|(r,f)|json!({"ref":r,"uuid":f.uuid,"position_nm":exact::point_value(&f.position),"rotation_deg":f.rotation_json,"fixed":fixed.contains(r)||!targets.contains_key(r)})).collect::<Vec<_>>());
    record["after"] = after.clone();
    record["judgment"] = after;
    if reasons.is_empty() {
        record["status"] = json!("planned");
        record["outcome"] = json!("info");
    }
    record["reasons"] = json!(reasons);
    Ok(record)
}

pub fn select(capture: &Value, policy: &Value, specs: &Value) -> Result<Value> {
    let specs = blocks::rows(specs)?;
    ensure!(
        !specs.is_empty() && specs.len() <= 64,
        "targeted_plans needs 1..64 targeted requests"
    );
    let plans: Vec<_> = specs
        .iter()
        .map(|s| plan(capture, policy, s))
        .collect::<Result<_>>()?;
    ensure!(
        plans
            .iter()
            .all(|p| p["judge_policy"] == plans[0]["judge_policy"]),
        "targeted_plans requires one shared judge_policy"
    );
    let mut selected = None;
    for (i, p) in plans.iter().enumerate() {
        if p["status"] == "planned"
            && selected.is_none_or(|old: usize| {
                exact::decimal(&p["after"]["score"]).unwrap()
                    > exact::decimal(&plans[old]["after"]["score"]).unwrap()
            })
        {
            selected = Some(i);
        }
    }
    let mut result = plans[selected.unwrap_or(0)].clone();
    result["selection"] = json!({"selected_index":selected,"plans":plans});
    Ok(result)
}
