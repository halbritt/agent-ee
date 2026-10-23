//! Finite same-face member refinement, independently witnessed from its starting candidate.
use crate::{
    arrangement, block_contract, blocks, constraints,
    exact::{self, Int, Point, Rat},
    footprint::{self, Keepout},
    geometry, grading, groups, locality, policy,
};
use anyhow::{Context, Result, ensure};
use num_traits::Zero;
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};

const SCHEMA: &str = "agent-ee.block-member-refinement/1";
fn bbox(fp: &Value) -> Result<geometry::Bounds> {
    let mut polygons = Vec::new();
    for record in fp["courtyards"]
        .as_object()
        .context("courtyards unavailable")?
        .values()
    {
        polygons.extend(geometry::courtyard_bounds(record)?.0);
    }
    geometry::bounds(&polygons)
}
fn number(v: &Rat) -> Value {
    if v.is_integer() {
        exact::integer_value(v.numer())
    } else {
        exact::fraction_record(v)
    }
}
fn wire(capture: &Value, refs: &BTreeSet<String>, grounds: &BTreeSet<String>) -> Result<Rat> {
    let fps = blocks::rows(&capture["footprints"])?;
    let mut nets = BTreeMap::<String, Vec<(String, [Rat; 2])>>::new();
    for fp in fps {
        let reference = blocks::text(&fp["ref"])?;
        if !refs.contains(reference) {
            continue;
        }
        for pad in blocks::rows(&fp["pads"])? {
            let Some(net) = pad["net"]
                .as_str()
                .filter(|s| !s.is_empty() && !grounds.contains(*s))
            else {
                continue;
            };
            let mut endpoint = json!({"reference":reference,"pad":pad["number"],"net":net});
            if let Some(uuid) = pad
                .get("uuid")
                .filter(|u| u.as_str().is_some_and(|s| !s.is_empty()))
            {
                endpoint["uuid"] = uuid.clone();
            }
            nets.entry(net.to_owned())
                .or_default()
                .push((reference.to_owned(), locality::resolve(fps, &endpoint)?));
        }
    }
    let mut total = Rat::zero();
    for points in nets.values() {
        if points.iter().map(|p| &p.0).collect::<BTreeSet<_>>().len() < 2 {
            continue;
        }
        for axis in 0..2 {
            total += points.iter().map(|p| &p.1[axis]).max().unwrap()
                - points.iter().map(|p| &p.1[axis]).min().unwrap();
        }
    }
    Ok(total)
}
struct Metrics {
    internal: Rat,
    whole: Rat,
    pairs: Vec<Rat>,
    thermal: Option<Int>,
}
impl Metrics {
    fn record(&self) -> Value {
        json!({"internal_hpwl_nm":number(&self.internal),"whole_board_hpwl_nm":number(&self.whole),
        "declared_pair_distance_squared_nm2":self.pairs.iter().map(number).collect::<Vec<_>>(),
        "thermal_minimum_distance_squared_nm2":self.thermal.as_ref().map(exact::integer_value)})
    }
    fn improves(&self, before: &Self) -> bool {
        self.internal < before.internal
            && self.whole <= before.whole
            && self.pairs.iter().zip(&before.pairs).all(|(b, a)| b <= a)
            && before
                .thermal
                .as_ref()
                .is_none_or(|a| self.thermal.as_ref().is_some_and(|b| b >= a))
    }
}
fn metrics(
    capture: &Value,
    policy: &Value,
    contract: &Value,
    record: &Value,
    refs: &BTreeSet<String>,
) -> Result<Metrics> {
    let board = blocks::board(capture)?;
    let fps = blocks::rows(&capture["footprints"])?;
    let mut pairs = Vec::new();
    for obligation in blocks::rows(&contract["relationships"]["obligations"])? {
        for role in locality::roles(obligation) {
            let pair = &obligation[role];
            let a = locality::resolve(fps, &pair["from"])?;
            let b = locality::resolve(fps, &pair["to"])?;
            let from = blocks::text(&pair["from"]["reference"])?;
            let to = blocks::text(&pair["to"]["reference"])?;
            let dz = if board[from]["side"] != board[to]["side"] {
                exact::integer(&contract["thickness_nm"])?
            } else {
                Int::zero()
            };
            let dx = &a[0] - &b[0];
            let dy = &a[1] - &b[1];
            pairs.push(&dx * &dx + &dy * &dy + Rat::from_integer(&dz * &dz));
        }
    }
    let sources = record["arrangement"]["thermal"]["sources"]
        .as_array()
        .cloned()
        .unwrap_or_default();
    let mut thermal = Vec::new();
    for (i, a) in sources.iter().enumerate() {
        for b in &sources[i + 1..] {
            let a = exact::point(&board[blocks::text(a)?]["position_nm"])?;
            let b = exact::point(&board[blocks::text(b)?]["position_nm"])?;
            let d = a.minus(&b);
            thermal.push(&d.x * &d.x + &d.y * &d.y);
        }
    }
    let grounds = policy::strings(&policy["ground_nets"], "ground_nets")?;
    Ok(Metrics {
        internal: wire(capture, refs, &grounds)?,
        whole: wire(capture, &board.keys().cloned().collect(), &grounds)?,
        pairs,
        thermal: thermal.into_iter().min(),
    })
}
fn move_member(capture: &Value, reference: &str, position: &Point) -> Result<Value> {
    let mut proposed = capture.clone();
    let source = blocks::rows(&capture["footprints"])?
        .iter()
        .find(|f| f["ref"] == reference)
        .context("member missing")?;
    let rotation = exact::rotation(&source["rotation_deg"])?;
    let position = exact::point_value(position);
    for fp in proposed["footprints"].as_array_mut().unwrap() {
        if fp["ref"] == reference {
            *fp = footprint::transform_value(fp, &position, rotation)?;
        }
    }
    for area in proposed
        .get_mut("rule_areas")
        .and_then(Value::as_array_mut)
        .into_iter()
        .flatten()
    {
        if area["owner"] == source["uuid"] {
            let temp = json!({"position_nm":source["position_nm"],"rotation_deg":source["rotation_deg"],"pads":[],"courtyards":{"zone":area["outline"]}});
            area["outline"] = footprint::transform_value(&temp, &position, rotation)?["courtyards"]
                ["zone"]
                .clone();
        }
    }
    Ok(proposed)
}
fn fixed(capture: &Value, policy: &Value) -> Result<BTreeSet<String>> {
    let mut out = policy::strings(&policy["fixed_refs"], "fixed_refs")?;
    for fp in blocks::rows(&capture["footprints"])? {
        if fp["locked"] == true {
            out.insert(blocks::text(&fp["ref"])?.to_owned());
        }
    }
    Ok(out)
}
fn admitted(
    proposed: &Value,
    reference: &str,
    policy: &Value,
    contract: &Value,
    record: &Value,
) -> Result<bool> {
    let prepared = footprint::qualify_capture(proposed)?;
    let fp = prepared
        .footprints
        .iter()
        .find(|f| f.reference == reference)
        .context("member missing")?;
    let peers: Vec<_> = prepared
        .footprints
        .iter()
        .filter(|f| f.reference != reference)
        .cloned()
        .collect();
    let mut obstacles = Vec::new();
    for o in blocks::rows(&policy["obstacles"])? {
        obstacles.push(Keepout {
            side: blocks::text(&o["side"])?.into(),
            polygons: policy::obstacle_polygons(&o["polygons"])?,
            name: String::new(),
        });
    }
    for peer in &peers {
        for (side, polygons) in &peer.courtyards {
            obstacles.push(Keepout {
                side: side.clone(),
                polygons: polygons.clone(),
                name: peer.reference.clone(),
            });
        }
    }
    if !constraints::fits(fp, &prepared.outline, &policy::edge_margin_nm(policy)?, &[])?
        || !constraints::access_admits(fp, &prepared.outline)?
        || constraints::near(fp, &obstacles, &exact::integer(&policy["gap_nm"])?)
        || constraints::keepout_blocked(fp, &peers, &prepared.board_keepouts)
        || peers.iter().any(|p| constraints::hole_conflict(fp, p))
        || constraints::copper_escapes(
            fp,
            &prepared.outline,
            &prepared.board_keepouts,
            &peers,
            &prepared.board_pad_keepouts,
        )?
        .is_some()
    {
        return Ok(false);
    }
    if grading::judge(proposed, None, policy.get("fixed_overhangs"))?["verdict"] != "pass" {
        return Ok(false);
    }
    if let Some(g) = policy
        .get("groups")
        .filter(|g| g.as_array().is_none_or(|a| !a.is_empty()))
    {
        if !groups::satisfied(&groups::grade(proposed, g)?) {
            return Ok(false);
        }
    }
    if let Some(l) = policy.get("electrical_locality") {
        let graded = locality::grade(proposed, l)?;
        if graded["status"] != "pass" && graded["status"] != "info" {
            return Ok(false);
        }
    }
    let mut owner = BTreeMap::new();
    for b in blocks::rows(&record["blocks"])? {
        for r in blocks::refs(&b["references"])? {
            owner.insert(r, blocks::text(&b["block"])?.to_owned());
        }
    }
    let rules = arrangement::grade_rules(
        proposed,
        &record["arrangement"],
        &owner,
        &fixed(proposed, policy)?,
    )?;
    if blocks::rows(&rules)?
        .iter()
        .any(|r| r["enforcement"] == "hard" && r["status"] != "pass")
    {
        return Ok(false);
    }
    Ok(block_contract::check_complete(proposed, contract)?["status"] == "pass")
}
fn targets(fp: &Value, peers: &[&Value], gap: &Int) -> Result<BTreeSet<Point>> {
    let p = exact::point(&fp["position_nm"])?;
    let a = bbox(fp)?;
    let mut out = BTreeSet::new();
    for peer in peers {
        let b = bbox(peer)?;
        let q = exact::point(&peer["position_nm"])?;
        let xs = BTreeSet::from([
            p.x.clone(),
            q.x,
            &p.x + &b.x0 - &a.x1 - gap,
            &p.x + &b.x1 - &a.x0 + gap,
        ]);
        let ys = BTreeSet::from([
            p.y.clone(),
            q.y,
            &p.y + &b.y0 - &a.y1 - gap,
            &p.y + &b.y1 - &a.y0 + gap,
        ]);
        for x in &xs {
            for y in &ys {
                out.insert(Point {
                    x: x.clone(),
                    y: y.clone(),
                });
            }
        }
    }
    out.remove(&p);
    Ok(out)
}

/// One deterministic pass over search-derived, movable, nonanchor members.
/// Every trial is charged; unchanged/valid accepted state survives budget exhaustion.
pub fn refine(
    capture: &Value,
    policy: &Value,
    contract: &Value,
    block_records: &Value,
    step_budget: u64,
) -> Result<(Value, Value)> {
    ensure!(
        block_contract::check_complete(capture, contract)?["status"] == "pass",
        "member refinement requires a complete passing block contract"
    );
    let mut result = capture.clone();
    let fixed = fixed(capture, policy)?;
    let mut moves = Vec::new();
    let mut rejected = BTreeMap::<String, u64>::new();
    let mut skipped = Vec::new();
    let mut steps = 0_u64;
    let mut exhausted = false;
    let records = blocks::rows(&block_records["blocks"])?;
    let ids = blocks::natural(
        records
            .iter()
            .map(|b| blocks::text(&b["block"]).map(str::to_owned))
            .collect::<Result<_>>()?,
    );
    for id in ids {
        let block = records.iter().find(|b| b["block"] == id).unwrap();
        let refs: BTreeSet<_> = blocks::refs(&block["references"])?.into_iter().collect();
        let anchor = blocks::text(&block["anchor"])?;
        if block["provenance"]["class"] != "search-derived" {
            skipped.push(json!({"block":id,"reason":"reference_layout_immutable"}));
            continue;
        }
        let mut polygons = Vec::new();
        for fp in blocks::rows(&capture["footprints"])? {
            if fp["ref"].as_str().is_some_and(|r| refs.contains(r)) {
                for c in fp["courtyards"]
                    .as_object()
                    .context("courtyards unavailable")?
                    .values()
                {
                    polygons.extend(geometry::courtyard_bounds(c)?.0);
                }
            }
        }
        let window = geometry::bounds(&polygons)?;
        let candidates = blocks::natural(
            refs.iter()
                .filter(|r| !fixed.contains(*r) && r.as_str() != anchor)
                .cloned()
                .collect(),
        );
        for reference in candidates {
            let mut fp = blocks::rows(&result["footprints"])?
                .iter()
                .find(|f| f["ref"] == reference)
                .context("member missing")?
                .clone();
            let peers: Vec<_> = blocks::rows(&result["footprints"])?
                .iter()
                .filter(|p| {
                    p["ref"] != reference && p["ref"].as_str().is_some_and(|r| refs.contains(r))
                })
                .collect();
            let candidates = targets(&fp, &peers, &exact::integer(&policy["gap_nm"])?)?;
            let mut before = metrics(&result, policy, contract, block_records, &refs)?;
            for position in candidates {
                if steps >= step_budget {
                    exhausted = true;
                    break;
                }
                steps += 1;
                let proposed = move_member(&result, &reference, &position)?;
                let candidate = blocks::rows(&proposed["footprints"])?
                    .iter()
                    .find(|f| f["ref"] == reference)
                    .unwrap();
                let bounds = bbox(candidate)?;
                let mut after = None;
                let reason = if bounds.x0 < window.x0
                    || bounds.x1 > window.x1
                    || bounds.y0 < window.y0
                    || bounds.y1 > window.y1
                {
                    Some("outside_block_window")
                } else {
                    after = Some(metrics(&proposed, policy, contract, block_records, &refs)?);
                    if !after.as_ref().unwrap().improves(&before) {
                        Some("objective_not_improved")
                    } else if !admitted(&proposed, &reference, policy, contract, block_records)? {
                        Some("constraint_rejected")
                    } else {
                        None
                    }
                };
                if let Some(reason) = reason {
                    *rejected.entry(reason.to_owned()).or_default() += 1;
                    continue;
                }
                let after = after.unwrap();
                moves.push(json!({"block":id,"reference":reference,"starting_candidate_sha256":block["candidate_sha256"],
                    "before_position_nm":fp["position_nm"],"after_position_nm":exact::point_value(&position),"side":fp["side"],"rotation_deg":fp["rotation_deg"],
                    "block_window_nm":[exact::integer_value(&window.x0),exact::integer_value(&window.y0),exact::integer_value(&window.x1),exact::integer_value(&window.y1)],
                    "before_capture_sha256":blocks::digest(&result),"after_capture_sha256":blocks::digest(&proposed),"before":before.record(),"after":after.record()}));
                fp = candidate.clone();
                result = proposed;
                before = after;
            }
            if exhausted {
                break;
            }
        }
        if exhausted {
            break;
        }
    }
    let finalboard = blocks::board(&result)?;
    let members: BTreeSet<String> = records
        .iter()
        .map(|b| blocks::refs(&b["references"]))
        .collect::<Result<Vec<_>>>()?
        .into_iter()
        .flatten()
        .collect();
    let poses:Vec<_>=blocks::natural(finalboard.keys().filter(|r| members.contains(*r)).cloned().collect()).into_iter().map(|r| {
        let fp=finalboard[&r]; json!({"reference":r,"position_nm":fp["position_nm"],"rotation_deg":fp["rotation_deg"],"side":fp["side"]})
    }).collect();
    let witness = json!({"schema":SCHEMA,"source_capture_sha256":blocks::digest(capture),"policy_sha256":blocks::digest(policy),"contract_sha256":blocks::digest(contract),
        "step_budget":step_budget,"steps_used":steps,"status":if moves.is_empty(){"unchanged"}else{"refined"},"termination":if exhausted{"budget_exhausted"}else{"complete"},
        "moves":moves,"rejected":rejected,"skipped":skipped,"final_capture_sha256":blocks::digest(&result),"final_member_poses":poses});
    Ok((result, witness))
}

#[cfg(test)]
mod tests {
    use super::*;
    use sha2::{Digest, Sha256};

    fn fixture() -> (Value, Value, Value, Value) {
        let fp = |reference: &str, x: i64| {
            json!({"ref":reference,"uuid":reference,"value":"fixture","fpid":"Owned:Square","locked":false,"side":"F","position_nm":[x,2000000],"rotation_deg":0,
            "courtyards":{"F":{"status":"proven","polygons":[[[x,2000000],[x+2000000,2000000],[x+2000000,4000000],[x,4000000]]]}},
            "pads":[{"uuid":format!("{reference}-1"),"number":"1","net":"VCC","attribute":"SMD","layers":["F.Cu"],"shape":1,"rotation_deg":0,"size_nm":[400000,400000],"copper_centroid_offset_nm":[[0,1],[0,1]],"position_nm":[x+500000,2500000]},
                    {"uuid":format!("{reference}-2"),"number":"2","net":"GND","attribute":"SMD","layers":["F.Cu"],"shape":1,"rotation_deg":0,"size_nm":[400000,400000],"copper_centroid_offset_nm":[[0,1],[0,1]],"position_nm":[x+1500000,2500000]}]})
        };
        let capture = json!({"schema":"agent-ee.placement-probe/1","board_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","pcbnew_version":"10.0.7","max_error_nm":5000,
            "outline":{"status":"proven","polygons":[[[0,0],[12000000,0],[12000000,8000000],[0,8000000]]]},"footprints":[fp("U1",2000000),fp("C1",6000000)]});
        let quote = "Original fixture numeric requirement: 6 mm";
        let source = json!({"path":"fixture/refinement-distance.txt","sha256":format!("{:x}",Sha256::digest(quote.as_bytes())),"locator":"numeric requirement","quote":quote});
        let pair = |number, net| {
            json!({"from":{"reference":"U1","pad":number,"net":net},"to":{"reference":"C1","pad":number,"net":net},"max_distance_nm":6000000,"citation":"fixture/refinement-distance.txt numeric requirement",
            "provenance":{"class":"datasheet","basis":"authored native refinement fixture","derivation":{"kind":"numeric","value_nm":6000000,"source":source}}})
        };
        let document = json!({"schema":"agent-ee.electrical-locality/1","source_records":{"fixture/refinement-distance.txt":quote},"obligations":[{"id":"support","metric":"pad-centre-to-centre","supply":pair("1","VCC"),"return":pair("2","GND")}]});
        let contract = json!({"schema":"agent-ee.block-contract/1","allocation":{"schema":"agent-ee.block-allocation/2","board_sha256":capture["board_sha256"],
            "blocks":[{"id":"device","references":["U1","C1"],"anchor":{"member":"U1"},"interfaces_to_fixed":null}],"fixed":[],"shared":[]},
            "eligibility":{"schema":"fome-mc.side-eligibility/1","sources":{"board_sha256":capture["board_sha256"]},"default":{"faces":["F","B"]},"blocks":{"device":{"eligible_faces":["F","B"]}}},
            "relationships":document,"templates":[],"thickness_nm":1600000});
        let policy = json!({"schema":"agent-ee.packing-policy/1","step_budget":10000,"gap_nm":500000,"fixed_refs":["U1"],"priority_refs":[],"ground_nets":["GND"],"obstacles":[],"electrical_locality":document});
        let record = json!({"blocks":[{"block":"device","references":["U1","C1"],"anchor":"U1","candidate_sha256":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","provenance":{"class":"search-derived"}}],"arrangement":{}});
        (capture, policy, contract, record)
    }
    #[test]
    fn exact_member_improvement_keeps_anchor_and_window() {
        let (capture, policy, contract, record) = fixture();
        let (after, witness) = refine(&capture, &policy, &contract, &record, 64).unwrap();
        assert_eq!(witness["status"], "refined");
        assert_eq!(after["footprints"][0], capture["footprints"][0]);
        assert_eq!(
            after["footprints"][1]["position_nm"],
            json!([4500000, 2000000])
        );
        assert_eq!(
            block_contract::check_complete(&after, &contract).unwrap()["status"],
            "pass"
        );
        let (again, repeat) = refine(&after, &policy, &contract, &record, 64).unwrap();
        assert_eq!(again, after);
        assert_eq!(repeat["status"], "unchanged");
    }
    #[test]
    fn zero_budget_and_reference_layout_leave_input_unchanged() {
        let (capture, policy, contract, mut record) = fixture();
        let (after, witness) = refine(&capture, &policy, &contract, &record, 0).unwrap();
        assert_eq!(after, capture);
        assert_eq!(witness["termination"], "budget_exhausted");
        assert_eq!(witness["steps_used"], 0);
        record["blocks"][0]["provenance"]["class"] = json!("manufacturer-reference-layout");
        let (after, witness) = refine(&capture, &policy, &contract, &record, 64).unwrap();
        assert_eq!(after, capture);
        assert_eq!(
            witness["skipped"][0]["reason"],
            "reference_layout_immutable"
        );
    }
    #[test]
    fn moved_owned_pad_keepout_rejects_stationary_peer_land() {
        let (mut capture, policy, contract, record) = fixture();
        capture["rule_areas"] = json!([{"uuid":"owned-pad-zone","owner":"C1","layers":["F.Cu"],
            "forbid":{"footprints":false,"pads":true},"outline":{"status":"proven","polygons":[[[4750000,2250000],[5250000,2250000],[5250000,2750000],[4750000,2750000]]]}}]);
        let (after, witness) = refine(&capture, &policy, &contract, &record, 64).unwrap();
        assert_eq!(after, capture);
        assert!(
            witness["rejected"]["constraint_rejected"]
                .as_u64()
                .unwrap_or(0)
                > 0
        );
        let proposed = move_member(&capture, "C1", &Point::new(4500000, 2000000)).unwrap();
        assert!(!admitted(&proposed, "C1", &policy, &contract, &record).unwrap());
    }
    #[test]
    fn any_declared_pair_regression_rejects_local_gain() {
        let before = Metrics {
            internal: Rat::from_integer(10.into()),
            whole: Rat::from_integer(10.into()),
            pairs: vec![Rat::from_integer(9.into())],
            thermal: Some(25.into()),
        };
        let after = Metrics {
            internal: Rat::from_integer(9.into()),
            whole: Rat::from_integer(10.into()),
            pairs: vec![Rat::from_integer(10.into())],
            thermal: Some(25.into()),
        };
        assert!(!after.improves(&before));
    }
}
