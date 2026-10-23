use anyhow::{Context, Result, ensure};
use num_traits::{ToPrimitive, Zero};
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};
use std::time::Instant;

use crate::constraints;
use crate::exact::{
    Int, Point, Rat, floor, integer, integer_value, isqrt, point_value, rational_value,
};
use crate::footprint::{Keepout, PreparedFootprint, QualifiedCapture, qualify_capture};
use crate::geometry::{Polygon, qualified_polygons, separation2};
use crate::search::{Exhausted, Search};
use crate::{grading, groups, locality, policy};

pub struct Prepared {
    pub capture: QualifiedCapture,
    pub fixed: BTreeSet<String>,
    pub placed: Vec<PreparedFootprint>,
    pub exact: Vec<Keepout>,
    pub carried: Option<Vec<Value>>,
    pub conflict: Option<String>,
    pub bands: BTreeMap<String, Vec<Polygon>>,
}

#[derive(Debug)]
struct MovableOverhang(Vec<String>);
impl std::fmt::Display for MovableOverhang {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "courtyard overhang declared for movable reference {}",
            self.0.join(", ")
        )
    }
}
impl std::error::Error for MovableOverhang {}

pub fn failure(
    capture: &Value,
    policy: &Value,
    status: &str,
    outcome: &str,
    steps: u64,
    reason: &str,
) -> Result<Value> {
    let margin = policy::edge_margin_nm(policy)?;
    Ok(
        json!({"schema":"agent-ee.packing-plan/1","board_sha256":capture.get("board_sha256"),
        "status":status,"outcome":outcome,"steps_used":steps,"step_budget":policy["step_budget"],
        "min_courtyard_edge_mm":Rat::new(margin,Int::from(1_000_000)).to_f64().context("edge margin exceeds report range")?,
        "policy":policy,"reason":reason,"placements":[],"authority":"standalone-unsealed-plan"}),
    )
}

fn references(raw: &Value) -> Result<BTreeSet<String>> {
    raw.as_array()
        .context("references must be an array")?
        .iter()
        .map(|v| {
            v.as_str()
                .context("reference must be text")
                .map(str::to_owned)
        })
        .collect()
}

fn unchanged(fp: &Value, seed: Option<&Value>) -> bool {
    seed.is_some_and(|seed| {
        ["position_nm", "courtyards"]
            .iter()
            .all(|key| fp.get(*key) == seed.get(*key))
            && crate::exact::rotation(&fp["rotation_deg"]).is_ok_and(|rotation| {
                crate::exact::rotation(&seed["rotation_deg"]).is_ok_and(|other| rotation == other)
            })
    })
}

pub fn prepare(raw: &Value, policy: &Value, seed: Option<&Value>) -> Result<Prepared> {
    let capture = qualify_capture(raw)?;
    let mut fixed = references(&policy["fixed_refs"])?;
    fixed.extend(
        capture
            .footprints
            .iter()
            .filter(|fp| fp.raw["locked"].as_bool() == Some(true))
            .map(|fp| fp.reference.clone()),
    );
    let empty = json!([]);
    let bands = constraints::overhang_bands(
        policy.get("fixed_overhangs").unwrap_or(&empty),
        &capture.outline,
    )?;
    let loose: Vec<_> = bands
        .keys()
        .filter(|r| !fixed.contains(*r))
        .cloned()
        .collect();
    if !loose.is_empty() {
        return Err(MovableOverhang(loose).into());
    }
    let gap = integer(&policy["gap_nm"])?;
    let carry = policy["carry_seed_fixed_pairs"].as_bool() == Some(true)
        && gap > Int::zero()
        && policy["constraint_provenance"].get("gap_nm").is_none();
    let mut prepared = Prepared {
        capture,
        fixed,
        placed: Vec::new(),
        exact: Vec::new(),
        carried: carry.then(Vec::new),
        conflict: None,
        bands,
    };
    let mut origins: Vec<Option<Value>> = Vec::new();
    for (i, obstacle) in policy["obstacles"]
        .as_array()
        .context("missing obstacles")?
        .iter()
        .enumerate()
    {
        let mut polygons = Vec::new();
        for polygon in obstacle["polygons"]
            .as_array()
            .context("missing obstacle polygons")?
        {
            polygons.extend(qualified_polygons(
                &json!({"status":"proven","polygons":[polygon]}),
            )?);
        }
        prepared.exact.push(Keepout {
            side: obstacle["side"]
                .as_str()
                .context("missing obstacle side")?
                .into(),
            polygons,
            name: format!("obstacle {i}"),
        });
        let source = policy::classify(obstacle, "provenance", &format!("obstacle {i}"), None)?;
        origins.push(if carry && source["enforcement"] == "soft" {
            let mut label = json!({"kind":"fixed_obstacle","obstacle":i});
            if let Some(source) = obstacle.get("source") {
                label["source"] = source.clone();
            }
            Some(label)
        } else {
            None
        });
    }
    let fixed_fps: Vec<_> = prepared
        .capture
        .footprints
        .iter()
        .filter(|fp| prepared.fixed.contains(&fp.reference))
        .cloned()
        .collect();
    for fp in fixed_fps {
        let result = reserve_fixed(
            &mut prepared,
            fp,
            &gap,
            &mut origins,
            seed.unwrap_or(raw),
            policy,
        );
        if let Err(error) = result {
            prepared.conflict = Some(error.to_string());
            break;
        }
    }
    if prepared.conflict.is_none() {
        let positions: BTreeMap<String, Point> = prepared
            .placed
            .iter()
            .map(|fp| (fp.reference.clone(), fp.position.clone()))
            .collect();
        let members = crate::search::membership(policy)?;
        let declared = policy.get("groups").and_then(Value::as_array);
        for fp in &prepared.placed {
            if let Some(group) = members.get(&fp.reference) {
                let record = declared
                    .and_then(|gs| {
                        gs.iter().find(|g| {
                            g["references"]
                                .as_array()
                                .is_some_and(|rs| rs.iter().any(|r| r == &fp.reference))
                        })
                    })
                    .context("validated group missing")?;
                if group
                    .anchor_ref
                    .as_ref()
                    .is_some_and(|r| !positions.contains_key(r))
                {
                    prepared.conflict = Some(format!(
                        "fixed placement {} in group {} needs a fixed anchor",
                        fp.reference,
                        record["id"].as_str().unwrap_or("")
                    ));
                    break;
                }
                if !groups::admits(&fp.to_value()?, record, &positions)? {
                    prepared.conflict = Some(format!(
                        "fixed placement {} violates declared group {}",
                        fp.reference,
                        record["id"].as_str().unwrap_or("")
                    ));
                    break;
                }
            }
        }
    }
    if let Some(binding) = policy.get("mechanical_reservations") {
        let mut fixed_capture = raw.clone();
        fixed_capture["footprints"] = json!(prepared.placed.iter().map(PreparedFootprint::to_value).collect::<Result<Vec<_>>>()?);
        if crate::mechanical::grade(&fixed_capture, binding, true)?["status"] != "pass" {
            prepared.conflict = Some("fixed placement violates source-bound mechanical reservations".into());
        }
    }
    Ok(prepared)
}

fn reserve_fixed(
    prepared: &mut Prepared,
    fp: PreparedFootprint,
    gap: &Int,
    origins: &mut Vec<Option<Value>>,
    seed: &Value,
    policy: &Value,
) -> Result<()> {
    let raw = fp.to_value()?;
    let seed_fp = seed["footprints"]
        .as_array()
        .and_then(|fps| fps.iter().find(|f| f["ref"] == fp.reference));
    let pre = prepared.carried.is_some() && unchanged(&raw, seed_fp);
    let held: Vec<_> = prepared
        .exact
        .iter()
        .zip(origins.iter())
        .filter(|(_, o)| !(pre && o.is_some()))
        .map(|(e, _)| e.clone())
        .collect();
    let facts: Vec<_> = prepared
        .exact
        .iter()
        .zip(origins.iter())
        .filter(|(_, o)| pre && o.is_some())
        .map(|(e, o)| (e.clone(), o.clone().unwrap()))
        .collect();
    ensure!(
        constraints::access_admits(&fp, &prepared.capture.outline)?,
        "fixed placement {}: access direction requires an outward edge and an outside lane",
        fp.reference
    );
    let bands = prepared
        .bands
        .get(&fp.reference)
        .map(Vec::as_slice)
        .unwrap_or(&[]);
    ensure!(
        constraints::fits(&fp, &prepared.capture.outline, &Int::zero(), bands)?
            && !constraints::near(&fp, &held, gap)
            && !constraints::near(
                &fp,
                &facts.iter().map(|(e, _)| e.clone()).collect::<Vec<_>>(),
                &Int::zero()
            )
            && !constraints::keepout_blocked(
                &fp,
                &prepared.placed,
                &prepared.capture.board_keepouts
            ),
        "fixed placement {} conflicts with outline, obstacle, keepout or reserved gap",
        fp.reference
    );
    for peer in &prepared.placed {
        ensure!(
            !constraints::hole_conflict(&fp, peer),
            "fixed placement {} conflicts with {} native through-hole reservation",
            fp.reference,
            peer.reference
        );
        ensure!(
            !constraints::copper_meets(&fp, peer) && !constraints::copper_meets(peer, &fp),
            "fixed placement {} copper outside its courtyard meets {}, or {}'s meets {}",
            fp.reference,
            peer.reference,
            peer.reference,
            fp.reference
        );
    }
    for (entry, origin) in facts {
        if let Some(courtyards) = fp.courtyards.get(&entry.side) {
            if constraints::near(&fp, std::slice::from_ref(&entry), gap) {
                let d2 = courtyards
                    .iter()
                    .flat_map(|p| entry.polygons.iter().map(move |r| separation2(p, r)))
                    .min()
                    .context("empty fixed polygons")?;
                let mut pair = if origin["kind"] == "fixed_fixed" {
                    json!({"kind":"fixed_fixed","references":[origin["reference"],fp.reference]})
                } else {
                    let mut label = origin;
                    label["reference"] = json!(fp.reference);
                    label
                };
                pair["side"] = json!(entry.side);
                pair["spacing_nm"] = integer_value(&isqrt(&floor(&d2)));
                pair["distance_squared_nm2"] = rational_value(&d2);
                pair["gap_nm"] = policy["gap_nm"].clone();
                pair["basis"] = json!("unchanged-from-seed");
                pair["authority"] = json!("carried-input-fact");
                pair["gap_enforcement"] = json!("advisory");
                prepared
                    .carried
                    .as_mut()
                    .expect("seed pairs requested")
                    .push(pair);
            }
        }
    }
    for (side, polygons) in &fp.courtyards {
        prepared.exact.push(Keepout {
            side: side.clone(),
            polygons: polygons.clone(),
            name: fp.reference.clone(),
        });
        origins.push(pre.then(|| json!({"kind":"fixed_fixed","reference":fp.reference})));
    }
    prepared.placed.push(fp);
    Ok(())
}

struct Attempt {
    plan: Value,
    steps: u64,
    deadline: Option<Instant>,
    copper: bool,
    capacity_failure: Option<Value>,
}
impl Attempt {
    fn early(plan: Value) -> Self {
        Self {
            plan,
            steps: 0,
            deadline: None,
            copper: false,
            capacity_failure: None,
        }
    }
}

pub fn pack(capture: &Value, policy: &Value, seed: Option<&Value>) -> Result<Value> {
    pack_arranged(capture, policy, seed, &BTreeMap::new())
}

pub fn pack_arranged(
    capture: &Value,
    policy: &Value,
    seed: Option<&Value>,
    arranged: &BTreeMap<String, PreparedFootprint>,
) -> Result<Value> {
    let legacy = attempt(capture, policy, seed, arranged, false, false, None, None)?;
    if legacy.plan["status"] == "planned" {
        return Ok(legacy.plan);
    }
    let budget = policy["step_budget"]
        .as_u64()
        .context("missing step budget")?;
    let mut used = legacy.steps;
    let deadline = legacy.deadline;
    let copper = legacy.copper;
    let capacity_failure = legacy.capacity_failure;
    let legacy_record = json!({"status":legacy.plan["status"],"steps_used":legacy.steps});
    let mut result = legacy.plan;
    if result["search"].get("locality_clusters").is_some() {
        if used == budget {
            result["search"]["joint_cluster_attempt"] = json!({"status":"not_attempted","reason":"no remaining deterministic candidate budget","steps_used":0});
        } else {
            let joint = attempt(
                capture,
                policy,
                seed,
                arranged,
                true,
                false,
                Some(budget - used),
                deadline,
            )?;
            used += joint.steps;
            if joint.plan["status"] == "planned" {
                let mut result = joint.plan;
                result["steps_used"] = json!(used);
                result["search"]["legacy_attempt"] = legacy_record;
                return Ok(result);
            }
            let mut diagnostic = joint.plan["search"]
                .as_object()
                .cloned()
                .unwrap_or_default();
            diagnostic.insert("status".into(), joint.plan["status"].clone());
            diagnostic.insert("steps_used".into(), json!(joint.steps));
            diagnostic.insert("reason".into(), joint.plan["reason"].clone());
            result["search"]["joint_cluster_attempt"] = Value::Object(diagnostic);
        }
    }
    if copper && used < budget {
        let aligned = attempt(
            capture,
            policy,
            seed,
            arranged,
            false,
            true,
            Some(budget - used),
            deadline,
        )?;
        if aligned.plan["status"] == "planned" {
            let mut result = aligned.plan;
            result["steps_used"] = json!(used + aligned.steps);
            result["search"]["legacy_attempt"] = legacy_record;
            result["search"]["copper_aligned"] = json!({"prior_steps_used":used,"copper_setback":constraints::copper_setback_record(capture)?});
            return Ok(result);
        }
        if result.get("search").is_some() {
            result["search"]["copper_aligned_attempt"] = json!({"status":aligned.plan["status"],"outcome":aligned.plan["outcome"],
                "reason":aligned.plan["reason"],"steps_used":aligned.steps,"prior_steps_used":used});
        }
    }
    Ok(proved_after_search(result, capacity_failure))
}

fn proved_after_search(mut result: Value, proof: Option<Value>) -> Value {
    if !matches!(result["status"].as_str(), Some("packing_search_exhausted" | "packing_budget_exhausted" | "locality_cluster_exhausted")) {
        return result;
    }
    if let Some(proof) = proof {
        let steps = result["steps_used"].clone();
        let search_failure = json!({"status":result["status"], "outcome":result["outcome"], "reason":result["reason"]});
        result.as_object_mut().unwrap().extend(proof.as_object().unwrap().clone());
        result["steps_used"] = steps;
        result["search_failure"] = search_failure;
    }
    result
}

fn attempt(
    capture: &Value,
    policy: &Value,
    seed: Option<&Value>,
    arranged: &BTreeMap<String, PreparedFootprint>,
    joint: bool,
    copper_axes: bool,
    remaining: Option<u64>,
    deadline: Option<Instant>,
) -> Result<Attempt> {
    crate::progress::emit(json!({"status":"qualifying", "steps_used":0, "placed_count":0}))?;
    crate::footprint::validate_capture(capture)?;
    let refs = capture["footprints"]
        .as_array()
        .context("footprints must be an array")?
        .iter()
        .map(|fp| {
            fp["ref"]
                .as_str()
                .context("footprint reference must be text")
                .map(str::to_owned)
        })
        .collect::<Result<BTreeSet<_>>>()?;
    policy::validate(policy, &refs)?;
    if let Some(binding) = policy.get("mechanical_reservations") {
        let mechanics = crate::mechanical::grade(capture, binding, false)?;
        if !mechanics["unknown"].as_array().context("mechanical coverage missing")?.is_empty() {
            let mut plan = failure(capture, policy, "mechanical_geometry_unavailable", "unknown", 0,
                "required mechanical geometry or identity is unresolved")?;
            plan["mechanical_reservations"] = mechanics;
            return Ok(Attempt::early(plan));
        }
    }
    if let Some(locality) = policy.get("electrical_locality") {
        let initial = locality::grade(capture, locality)?;
        let infeasible = initial["defects"]
            .as_array()
            .is_some_and(|ds| ds.iter().any(|d| d["kind"] == "locality_infeasible"));
        if infeasible || initial["unknown"].as_array().is_some_and(|u| !u.is_empty()) {
            let (status, outcome, reason) = if infeasible {
                (
                    "locality_infeasible",
                    "defect",
                    "declared bound below computed manufacturing floor",
                )
            } else {
                (
                    "electrical_locality_unavailable",
                    "unknown",
                    "pad locality needs unambiguous native pads, limits and citations",
                )
            };
            let mut plan = failure(capture, policy, status, outcome, 0, reason)?;
            plan["locality_grading"] = initial;
            return Ok(Attempt::early(plan));
        }
    }
    let prepared = match prepare(capture, policy, seed) {
        Ok(prepared) => prepared,
        Err(error) if error.is::<MovableOverhang>() => return Err(error),
        Err(error) => {
            return Ok(Attempt::early(failure(
                capture,
                policy,
                "unsupported_geometry",
                "unknown",
                0,
                &error.to_string(),
            )?));
        }
    };
    let mut overhang_copper = Vec::new();
    if prepared.conflict.is_none() {
        let mut checked = Vec::new();
        for fp in &prepared.placed {
            let admitted = arranged
                .get(&fp.reference)
                .map(PreparedFootprint::to_value)
                .transpose()?;
            if !unchanged(&fp.to_value()?, admitted.as_ref()) {
                checked.push(fp.clone());
            }
        }
        let (refusal, overhang) = constraints::fixed_input_copper(
            &checked,
            &prepared.capture.outline,
            &prepared.capture.board_keepouts,
            &prepared.capture.board_pad_keepouts,
            policy.get("fixed_overhangs").unwrap_or(&json!([])),
        )?;
        overhang_copper = overhang;
        if let Some(refusal) = refusal {
            let outcome = refusal["outcome"]
                .as_str()
                .context("copper refusal outcome missing")?;
            let mut plan = failure(
                capture,
                policy,
                if outcome == "defect" {
                    "fixed_input_copper"
                } else {
                    "unsupported_geometry"
                },
                outcome,
                0,
                refusal["reason"]
                    .as_str()
                    .context("copper refusal reason missing")?,
            )?;
            plan["findings"] = refusal["findings"].clone();
            if let Some(carried) = prepared.carried {
                plan["carried_fixed_pairs"] = json!(carried);
            }
            return Ok(Attempt::early(plan));
        }
    }
    if let Some(plan) = grading::intrinsic_proof(&prepared.capture.raw, policy)? {
        return Ok(Attempt::early(plan));
    }
    if let Some(reason) = prepared.conflict {
        let mut plan = failure(capture, policy, "fixed_conflict", "unknown", 0, &reason)?;
        if let Some(carried) = prepared.carried {
            plan["carried_fixed_pairs"] = json!(carried);
        }
        return Ok(Attempt::early(plan));
    }
    if let Some(locality) = policy.get("electrical_locality") {
        let fixed_capture = json!({"footprints":prepared.placed.iter().map(PreparedFootprint::to_value).collect::<Result<Vec<_>>>()?});
        let fixed_grade = locality::grade(&fixed_capture, locality)?;
        if fixed_grade["defects"]
            .as_array()
            .is_some_and(|d| !d.is_empty())
        {
            let mut plan = failure(
                capture,
                policy,
                "fixed_conflict",
                "unknown",
                0,
                "fixed pads violate electrical locality",
            )?;
            plan["locality_grading"] = fixed_grade;
            if let Some(carried) = prepared.carried {
                plan["carried_fixed_pairs"] = json!(carried);
            }
            return Ok(Attempt::early(plan));
        }
    }
    // Only the first pass retains a default proof. Search and retries keep their
    // budgets; successful plans and judge refusals retain their own verdicts.
    let proofs = grading::capacity_proofs(&prepared.capture.raw, policy,
        remaining.is_none() && policy.get("capacity_proof").is_none())?;
    if let Some(mut plan) = proofs.early {
        if let Some(carried) = prepared.carried {
            plan["carried_fixed_pairs"] = json!(carried);
        }
        return Ok(Attempt::early(plan));
    }
    if let Some(reason) = constraints::copper_setback_missing(
        &prepared
            .capture
            .footprints
            .iter()
            .filter(|fp| !prepared.fixed.contains(&fp.reference))
            .cloned()
            .collect::<Vec<_>>(),
    )? {
        let mut plan = failure(
            capture,
            policy,
            "unsupported_geometry",
            "unknown",
            0,
            &reason,
        )?;
        if let Some(carried) = prepared.carried {
            plan["carried_fixed_pairs"] = json!(carried);
        }
        return Ok(Attempt::early(plan));
    }
    let copper = prepared
        .capture
        .footprints
        .iter()
        .any(|fp| !prepared.fixed.contains(&fp.reference) && !fp.copper_outline.is_empty());
    let carried = prepared.carried;
    let mut search = Search::new(
        prepared.capture,
        policy,
        prepared.fixed,
        prepared.exact,
        prepared.placed,
    )?;
    search.copper_axes = copper_axes;
    if let Some(remaining) = remaining {
        search.budget = remaining;
        search.deadline = deadline;
    }
    let queue = queue(&search, policy)?;
    let clusters = clusters(&queue, &search.fixed, policy)?;
    let mut plan = run(&mut search, policy, &queue, &clusters, joint, &proofs.diagnostic)?;
    if let Some(carried) = carried {
        plan["carried_fixed_pairs"] = json!(carried);
    }
    if plan["status"] == "planned" && !overhang_copper.is_empty() {
        plan["fixed_overhang_copper"] = json!(overhang_copper);
    }
    Ok(Attempt {
        plan,
        steps: search.steps,
        deadline: search.deadline,
        copper,
        capacity_failure: proofs.deferred,
    })
}

type Rank = (bool, usize, ReverseCount, String);
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
struct ReverseCount(std::cmp::Reverse<usize>);
fn queue(search: &Search, policy: &Value) -> Result<Vec<String>> {
    let priorities = policy["priority_refs"]
        .as_array()
        .context("missing priorities")?;
    let leaders: BTreeSet<_> = search
        .members
        .values()
        .filter_map(|g| g.anchor_ref.clone())
        .collect();
    let ranks: BTreeMap<String, Rank> = search
        .capture
        .footprints
        .iter()
        .map(|fp| {
            (
                fp.reference.clone(),
                (
                    !leaders.contains(&fp.reference),
                    priorities
                        .iter()
                        .position(|r| r == &fp.reference)
                        .unwrap_or(priorities.len()),
                    ReverseCount(std::cmp::Reverse(fp.pads.len())),
                    fp.reference.clone(),
                ),
            )
        })
        .collect();
    let mut queue: Vec<_> = search
        .capture
        .footprints
        .iter()
        .filter(|fp| !search.fixed.contains(&fp.reference))
        .map(|fp| fp.reference.clone())
        .collect();
    queue.sort_by_key(|reference| {
        let rank = ranks[reference].clone();
        if let Some(group) = search.members.get(reference) {
            if group.anchor_point.is_some() {
                return ((0, group.limit.clone()), rank.clone(), 0, rank);
            }
            if let Some(anchor) = group
                .anchor_ref
                .as_ref()
                .filter(|a| !search.fixed.contains(*a))
            {
                return ((1, Int::zero()), ranks[anchor].clone(), 1, rank);
            }
        }
        ((1, Int::zero()), rank.clone(), 0, rank)
    });
    Ok(queue)
}

fn clusters(queue: &[String], fixed: &BTreeSet<String>, policy: &Value) -> Result<Vec<Value>> {
    let Some(document) = policy.get("electrical_locality") else {
        return Ok(Vec::new());
    };
    let mut edges: BTreeMap<String, Vec<(String, Int)>> = BTreeMap::new();
    for obligation in document["obligations"]
        .as_array()
        .context("missing locality obligations")?
    {
        for role in obligation
            .as_object()
            .context("invalid obligation")?
            .values()
            .filter(|v| v.get("from").is_some() && v.get("to").is_some())
        {
            let a = role["from"]["reference"]
                .as_str()
                .context("missing locality reference")?
                .to_owned();
            let b = role["to"]["reference"]
                .as_str()
                .context("missing locality reference")?
                .to_owned();
            let limit = integer(&role["max_distance_nm"])?;
            edges
                .entry(a.clone())
                .or_default()
                .push((b.clone(), limit.clone()));
            edges.entry(b).or_default().push((a, limit));
        }
    }
    let order: BTreeMap<_, _> = queue
        .iter()
        .enumerate()
        .map(|(i, r)| (r.clone(), i as i64))
        .collect();
    let mut remaining: BTreeSet<_> = edges.keys().cloned().collect();
    let mut out = Vec::new();
    while let Some(first) = remaining.first().cloned() {
        let mut refs = BTreeSet::new();
        let mut pending = vec![first];
        while let Some(r) = pending.pop() {
            if !refs.insert(r.clone()) {
                continue;
            }
            pending.extend(edges[&r].iter().map(|(p, _)| p.clone()));
        }
        remaining.retain(|r| !refs.contains(r));
        let mut movable: Vec<_> = refs.difference(fixed).cloned().collect();
        if refs.len() < 3 || movable.is_empty() {
            continue;
        }
        let hub = refs
            .iter()
            .min_by_key(|r| {
                (
                    std::cmp::Reverse(
                        edges[*r]
                            .iter()
                            .map(|(p, _)| p)
                            .collect::<BTreeSet<_>>()
                            .len(),
                    ),
                    order.get(*r).copied().unwrap_or(-1),
                    (*r).clone(),
                )
            })
            .unwrap();
        movable.sort_by_key(|r| {
            (
                r != hub,
                edges[r].iter().map(|(_, d)| d).min().unwrap().clone(),
                order[r],
                r.clone(),
            )
        });
        let mut all = vec![hub.clone()];
        all.extend(refs.iter().filter(|r| *r != hub).cloned());
        let obligations: BTreeSet<_> = document["obligations"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|o| {
                o.as_object().unwrap().values().any(|role| {
                    role["from"]["reference"]
                        .as_str()
                        .is_some_and(|r| refs.contains(r))
                })
            })
            .map(|o| o["id"].as_str().unwrap().to_owned())
            .collect();
        out.push(json!({"references":movable,"all_references":all,"strategy":"joint-tightest-first/1","step_budget":4096,"obligations":obligations}));
    }
    out.sort_by_key(|c| {
        c["references"]
            .as_array()
            .unwrap()
            .iter()
            .map(|r| order[r.as_str().unwrap()])
            .min()
            .unwrap()
    });
    Ok(out)
}

struct Progress<'a> {
    policy: &'a Value,
    clusters: &'a [Value],
    capacity: &'a Value,
    backjumps: u64,
    peak: usize,
}
impl Progress<'_> {
    fn stopped(
        &self,
        search: &Search,
        status: &str,
        reason: &str,
        unplaced: Option<&str>,
        joint: bool,
    ) -> Result<Value> {
        let mut plan = failure(
            &search.capture.raw,
            self.policy,
            status,
            "unknown",
            search.steps,
            reason,
        )?;
        let mut record =
            json!({"strategy":"ranked-first-fit-backjump/1","backjumps":self.backjumps});
        if !self.clusters.is_empty() {
            record["locality_clusters"] = json!(self.clusters);
        }
        if let Some(unplaced) = unplaced {
            record["unplaced"] = json!(unplaced);
            record["placed_peak"] = json!(self.peak);
            if joint {
                if let Some(cluster) = self.clusters.iter().find(|c| {
                    c["references"]
                        .as_array()
                        .unwrap()
                        .iter()
                        .any(|r| r == unplaced)
                }) {
                    let mut blocking = cluster.clone();
                    blocking["references"] = blocking["all_references"].clone();
                    record["blocking_cluster"] = blocking;
                }
            }
        }
        if [
            "packing_search_exhausted",
            "packing_budget_exhausted",
            "locality_cluster_exhausted",
        ]
        .contains(&status)
        {
            record["termination"] = json!(status.strip_prefix("packing_").unwrap_or(status));
            record["partial"] = json!({"authority":"diagnostic-only-unvalidated-partial-placement","placements":placements(&search.best,None)});
        }
        plan["search"] = record;
        plan["capacity"] = self.capacity.clone();
        Ok(plan)
    }
}

fn placements(fps: &[PreparedFootprint], fixed: Option<&BTreeSet<String>>) -> Vec<Value> {
    let mut fps: Vec<_> = fps.iter().collect();
    fps.sort_by_key(|fp| &fp.reference);
    fps.into_iter().map(|fp| {
        let mut pose=json!({"ref":fp.reference,"uuid":fp.uuid,"position_nm":point_value(&fp.position),"rotation_deg":fp.rotation_json});
        if let Some(fixed)=fixed {pose["fixed"]=json!(fixed.contains(&fp.reference));}pose
    }).collect()
}

fn run(
    search: &mut Search,
    policy: &Value,
    queue: &[String],
    clusters: &[Value],
    joint: bool,
    capacity: &Value,
) -> Result<Value> {
    let mut cluster_by_ref = BTreeMap::new();
    if joint {
        for cluster in clusters {
            let unit: Vec<String> = cluster["references"]
                .as_array()
                .unwrap()
                .iter()
                .map(|r| r.as_str().unwrap().to_owned())
                .collect();
            for r in &unit {
                cluster_by_ref.insert(r.clone(), unit.clone());
            }
        }
    }
    let mut units = Vec::new();
    for r in queue {
        let unit = cluster_by_ref
            .get(r)
            .cloned()
            .unwrap_or_else(|| vec![r.clone()]);
        if !units.contains(&unit) {
            units.push(unit);
        }
    }
    let mut seen = BTreeSet::from([units.clone()]);
    let mut stack: Vec<Vec<String>> = Vec::new();
    let mut failures: BTreeMap<String, usize> = BTreeMap::new();
    let mut progress = Progress {
        policy,
        clusters,
        capacity,
        backjumps: 0,
        peak: 0,
    };
    crate::progress::emit(
        json!({"status":"packing", "steps_used":0, "placed_count":search.placed.len()}),
    )?;
    while stack.len() < units.len() {
        let unit = units[stack.len()].clone();
        let is_cluster = cluster_by_ref.contains_key(&unit[0]);
        let outcome = if is_cluster {
            search.place_cluster(&unit)
        } else {
            search.place_unit(&unit)
        };
        let (fps, conflicts) = match outcome {
            Ok(outcome) => outcome,
            Err(error) => {
                if let Some(exhausted) = error.downcast_ref::<Exhausted>() {
                    let status = if matches!(exhausted, Exhausted::Cluster) {
                        "locality_cluster_exhausted"
                    } else {
                        "packing_budget_exhausted"
                    };
                    let reason = if matches!(exhausted, Exhausted::Cluster) {
                        let cluster = clusters
                            .iter()
                            .find(|c| {
                                c["references"]
                                    .as_array()
                                    .unwrap()
                                    .iter()
                                    .any(|r| r == &unit[0])
                            })
                            .context("active cluster missing")?;
                        format!(
                            "joint locality cluster {} exceeded its work guard; infeasibility unproved",
                            repr_strings(&cluster["obligations"])
                        )
                    } else {
                        error.to_string()
                    };
                    return progress.stopped(search, status, &reason, Some(&unit[0]), joint);
                }
                if error.to_string().contains("locality target budget") {
                    return progress.stopped(
                        search,
                        "packing_budget_exhausted",
                        "locality target budget exhausted",
                        Some(&unit[0]),
                        joint,
                    );
                }
                return Err(error);
            }
        };
        if let Some(fps) = fps {
            let refs = fps.iter().map(|fp| fp.reference.clone()).collect();
            for fp in fps {
                search.push(fp)?;
            }
            stack.push(refs);
            progress.peak = progress.peak.max(stack.iter().map(Vec::len).sum());
            continue;
        }
        let index: BTreeMap<_, _> = stack
            .iter()
            .enumerate()
            .flat_map(|(i, refs)| refs.iter().map(move |r| (r.clone(), i)))
            .collect();
        let culprits: Vec<_> = conflicts
            .iter()
            .filter_map(|r| index.get(r).copied())
            .collect::<BTreeSet<_>>()
            .into_iter()
            .rev()
            .collect();
        if culprits.is_empty() {
            let fp = search
                .capture
                .footprints
                .iter()
                .find(|fp| fp.reference == unit[0])
                .context("search footprint missing")?;
            let suffix = if constraints::access_hard(fp)? {
                "; access direction requires an outward edge and an outside lane"
            } else {
                ""
            };
            return progress.stopped(
                search,
                if is_cluster {
                    "locality_cluster_exhausted"
                } else {
                    "packing_search_exhausted"
                },
                &format!(
                    "no candidate for {} in the finite search; infeasibility unproved{suffix}",
                    unit[0]
                ),
                Some(&unit[0]),
                joint,
            );
        }
        let failure_count = failures.entry(unit[0].clone()).or_default();
        *failure_count += 1;
        let skip = (1usize
            .checked_shl((*failure_count - 1).try_into().unwrap_or(u32::MAX))
            .unwrap_or(usize::MAX))
        .min(culprits.len())
            - 1;
        let mut reordered = None;
        for k in culprits.into_iter().skip(skip) {
            let i = stack.len();
            let mut order = units[..k].to_vec();
            let anchored = unit.len() == 1
                && search
                    .members
                    .get(&unit[0])
                    .and_then(|g| g.anchor_ref.as_ref())
                    .is_some_and(|a| units[k].contains(a))
                && !cluster_by_ref.contains_key(&units[k][0]);
            if anchored {
                let mut combined = unit.clone();
                combined.extend(units[k].clone());
                order.push(combined);
                order.extend_from_slice(&units[k + 1..i]);
            } else {
                order.push(unit.clone());
                order.extend_from_slice(&units[k..i]);
            }
            order.extend_from_slice(&units[i + 1..]);
            if !seen.contains(&order) {
                reordered = Some((k, order));
                break;
            }
        }
        let Some((k, order)) = reordered else {
            return progress.stopped(
                search,
                "packing_search_exhausted",
                &format!(
                    "every backjump for {} revisits a part order; infeasibility unproved",
                    unit[0]
                ),
                Some(&unit[0]),
                joint,
            );
        };
        if progress.backjumps == search.budget {
            return progress.stopped(
                search,
                "packing_budget_exhausted",
                "deterministic backjump budget exhausted",
                Some(&unit[0]),
                joint,
            );
        }
        seen.insert(order.clone());
        units = order;
        progress.backjumps += 1;
        for done in stack.drain(k..).rev() {
            for _ in done {
                search.pop();
            }
        }
    }
    finish(search, &progress)
}

fn repr_strings(strings: &Value) -> String {
    let repr = |text: &str| {
        let quote = if text.contains('\'') && !text.contains('"') {
            '"'
        } else {
            '\''
        };
        let mut output = String::from(quote);
        for c in text.chars() {
            match c {
                '\\' => output.push_str("\\\\"),
                '\n' => output.push_str("\\n"),
                '\r' => output.push_str("\\r"),
                '\t' => output.push_str("\\t"),
                c if c == quote => {
                    output.push('\\');
                    output.push(c);
                }
                c if c.is_control() => output.push_str(&format!("\\x{:02x}", c as u32)),
                c => output.push(c),
            }
        }
        output.push(quote);
        output
    };
    format!(
        "[{}]",
        strings
            .as_array()
            .unwrap()
            .iter()
            .map(|s| repr(s.as_str().unwrap()))
            .collect::<Vec<_>>()
            .join(", ")
    )
}

fn finish(search: &Search, progress: &Progress<'_>) -> Result<Value> {
    let policy = progress.policy;
    let mut proposed = search.capture.raw.clone();
    proposed["footprints"] = json!(
        search
            .placed
            .iter()
            .map(PreparedFootprint::to_value)
            .collect::<Result<Vec<_>>>()?
    );
    let mut judgment = grading::judge_mechanical(&proposed, None, policy.get("fixed_overhangs"), policy.get("mechanical_reservations"))?;
    judgment["basis"] = json!("transformed-proposal-not-native");
    if judgment["verdict"] != "pass" {
        return progress.stopped(
            search,
            "judge_rejected",
            "independent full-polygon judge rejected complete proposal",
            None,
            false,
        );
    }
    let mut plan = json!({"schema":"agent-ee.packing-plan/1","board_sha256":search.capture.raw["board_sha256"],
        "status":"planned","outcome":"info","authority":"standalone-unsealed-plan","steps_used":search.steps,"step_budget":policy["step_budget"],
        "min_courtyard_edge_mm":Rat::new(policy::edge_margin_nm(policy)?,Int::from(1_000_000)).to_f64().context("margin exceeds report range")?,
        "policy":policy,"judgment":judgment,"capacity":progress.capacity,
        "pad_shapes":{"pcbnew_major":"10","accepted":{"0":"circle","1":"rect","2":"oval","4":"roundrect","5":"chamfered_rect"},"pad_rotation":"quarter-turn","pad_outline_in_geometry":false},
        "search":{"strategy":"ranked-first-fit-backjump/1","backjumps":progress.backjumps},
        "placements":placements(&search.placed,Some(&search.fixed))});
    if !progress.clusters.is_empty() {
        plan["search"]["locality_clusters"] = json!(progress.clusters);
    }
    if let Some(declared) = policy.get("groups") {
        let grade = groups::grade(&proposed, &groups::validate(declared, None)?)?;
        if !groups::satisfied(&grade) {
            return progress.stopped(
                search,
                "judge_rejected",
                "declared placement groups not satisfied by the complete proposal",
                None,
                false,
            );
        }
        plan["group_grading"] = grade;
    }
    if let Some(document) = policy.get("electrical_locality") {
        let grade = locality::grade(&proposed, document)?;
        if grade["status"] != "pass" && grade["status"] != "info" {
            let mut failure = progress.stopped(
                search,
                "judge_rejected",
                "complete proposal failed pad locality",
                None,
                false,
            )?;
            failure["locality_grading"] = grade;
            return Ok(failure);
        }
        plan["locality_grading"] = grade;
    }
    if let Some(document) = policy.get("advisory_regions") {
        plan["advisory_regions"] = grading::advisory_regions(&proposed, document, &search.fixed)?;
    }
    let custom: Vec<_> = search.capture.raw["footprints"]
        .as_array()
        .unwrap()
        .iter()
        .flat_map(|fp| {
            fp["pads"]
                .as_array()
                .unwrap()
                .iter()
                .filter(|p| p["shape"] == 6)
                .map(|p| json!({"reference":fp["ref"],"uuid":p["uuid"],"number":p["number"]}))
        })
        .collect();
    if !custom.is_empty() {
        plan["qualified_custom_pads"] = json!(custom);
    }
    Ok(plan)
}
