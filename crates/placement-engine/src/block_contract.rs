//! Strict logical membership and exact complete-placement contract checks.
//! Native save/reload identity and the full independent report remain in Python.
use crate::{
    blocks,
    exact::{self, Int, Rat},
    footprint, geometry, locality,
};
use anyhow::{Context, Result, ensure};
use num_traits::Zero;
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};

const SCHEMA: &str = "agent-ee.block-contract/1";

fn allocation_schema(value: &Value) -> bool {
    matches!(
        value.as_str(),
        Some(
            "agent-ee.block-allocation/2"
                | "agent-ee.block-allocation/2 (proposed; /1 consumers ignore fixed/shared)"
        )
    )
}
fn eligibility_schema(value: &Value) -> bool {
    matches!(
        value.as_str(),
        Some(
            "fome-mc.side-eligibility/1"
                | "fome-mc.side-eligibility/1 (proposed consumer: agent-ee, AGENTEE-479)"
        )
    )
}
fn add_role(
    roles: &mut BTreeMap<String, Value>,
    inventory: &BTreeSet<String>,
    reference: &Value,
    role: Value,
) -> Result<()> {
    let reference = blocks::text(reference)?;
    ensure!(
        inventory.contains(reference),
        "allocation names unknown reference {reference}"
    );
    ensure!(
        roles.insert(reference.to_owned(), role).is_none(),
        "allocation gives multiple roles to {reference}"
    );
    Ok(())
}

pub fn membership(capture: &Value, allocation: &Value) -> Result<Value> {
    footprint::validate_capture(capture)?;
    ensure!(
        allocation_schema(&allocation["schema"]),
        "expected agent-ee.block-allocation/2"
    );
    ensure!(
        allocation["board_sha256"] == capture["board_sha256"],
        "allocation is not bound to this original board"
    );
    let inventory: BTreeSet<String> = blocks::board(capture)?.keys().cloned().collect();
    let mut roles = BTreeMap::new();
    let mut normalized = BTreeMap::new();
    for block in blocks::rows(&allocation["blocks"])? {
        let id = blocks::text(&block["id"])?;
        let references = blocks::refs(&block["references"])?;
        ensure!(!references.is_empty(), "block {id} has no members");
        ensure!(!normalized.contains_key(id), "duplicate block {id}");
        for reference in &references {
            add_role(
                &mut roles,
                &inventory,
                &json!(reference),
                json!({"role":"block","block_id":id}),
            )?;
        }
        let anchor = block["anchor"]
            .as_object()
            .context("block needs explicit anchor")?;
        ensure!(
            anchor.len() == 1
                && anchor
                    .keys()
                    .all(|k| ["member", "fixed", "none"].contains(&k.as_str())),
            "block needs member, fixed or none anchor"
        );
        let (kind, value) = anchor.iter().next().unwrap();
        let value = blocks::text(value)?;
        ensure!(
            kind != "member" || references.iter().any(|r| r == value),
            "member anchor is not a member"
        );
        ensure!(
            kind != "none" || value == "centroid of members",
            "none anchor must declare centroid of members"
        );
        let interface = block
            .get("interfaces_to_fixed")
            .context("explicit interfaces_to_fixed required")?;
        if !interface.is_null() {
            blocks::text(interface)?;
        }
        let mut references = references;
        references.sort();
        normalized.insert(id.to_owned(), json!({"id":id,"references":references,"anchor":block["anchor"],"interfaces_to_fixed":interface}));
    }
    for row in blocks::rows(&allocation["fixed"])? {
        add_role(
            &mut roles,
            &inventory,
            &row["reference"],
            json!({"role":"fixed"}),
        )?;
    }
    for row in blocks::rows(&allocation["shared"])? {
        let role = blocks::text(&row["role"])?;
        add_role(
            &mut roles,
            &inventory,
            &row["reference"],
            json!({"role":"shared","shared_role":role}),
        )?;
    }
    let missing: Vec<_> = inventory
        .iter()
        .filter(|r| !roles.contains_key(*r))
        .collect();
    ensure!(
        missing.is_empty(),
        "unallocated native footprints: {}",
        missing
            .iter()
            .map(|r| r.as_str())
            .collect::<Vec<_>>()
            .join(", ")
    );
    for block in normalized.values() {
        for reference in [&block["anchor"]["fixed"], &block["interfaces_to_fixed"]] {
            if let Some(reference) = reference.as_str() {
                ensure!(
                    roles.get(reference).is_some_and(|r| r["role"] == "fixed"),
                    "block names non-fixed interface {reference}"
                );
            }
        }
    }
    let anchors: BTreeMap<_, _> = normalized
        .iter()
        .map(|(id, b)| (id, &b["anchor"]))
        .collect();
    Ok(
        json!({"schema":"agent-ee.block-membership/1","board_sha256":capture["board_sha256"],
        "allocation_sha256":blocks::digest(allocation),"blocks":normalized.values().collect::<Vec<_>>(),"roles":roles,"anchors":anchors}),
    )
}
fn faces(value: &Value) -> Result<BTreeSet<String>> {
    let values = blocks::refs(value)?;
    ensure!(
        !values.is_empty() && values.iter().all(|v| v == "F" || v == "B"),
        "eligibility needs unique F/B faces"
    );
    Ok(values.into_iter().collect())
}
fn block_faces(eligibility: &Value, id: &str) -> Result<BTreeSet<String>> {
    let default = faces(&eligibility["default"]["faces"])?;
    let entry = &eligibility["blocks"][id];
    let result = faces(&entry["eligible_faces"])?;
    ensure!(
        result.is_subset(&default),
        "block faces exceed default eligibility"
    );
    ensure!(
        result == default
            || entry["face_restrictions"]
                .as_array()
                .is_some_and(|v| !v.is_empty()),
        "narrowed faces require explicit restrictions"
    );
    Ok(result)
}
fn member_faces(
    eligibility: &Value,
    reference: &str,
    role: &Value,
) -> Result<Option<BTreeSet<String>>> {
    let mut base = if role["role"] == "block" {
        Some(block_faces(eligibility, blocks::text(&role["block_id"])?)?)
    } else {
        eligibility
            .get(blocks::text(&role["role"])?)
            .and_then(|v| v.get(reference))
            .map(|entry| faces(&entry["eligible_faces"]))
            .transpose()?
    };
    if let Some(member) = eligibility.get("members").and_then(|v| v.get(reference)) {
        let allowed = faces(&member["eligible_faces"])?;
        ensure!(
            allowed.is_subset(&faces(&eligibility["default"]["faces"])?),
            "member eligibility exceeds default permission"
        );
        base = Some(allowed);
    }
    Ok(base)
}
fn declaration(capture: &Value, contract: &Value) -> Result<Value> {
    blocks::keys(
        contract,
        &[
            "schema",
            "allocation",
            "eligibility",
            "relationships",
            "templates",
            "thickness_nm",
        ],
        &[],
    )?;
    ensure!(
        contract["schema"] == SCHEMA,
        "expected agent-ee.block-contract/1"
    );
    let members = membership(capture, &contract["allocation"])?;
    let eligibility = &contract["eligibility"];
    ensure!(
        eligibility_schema(&eligibility["schema"]),
        "expected fome-mc.side-eligibility/1"
    );
    ensure!(
        eligibility["sources"]["board_sha256"] == capture["board_sha256"],
        "side eligibility is not bound to this original board"
    );
    faces(&eligibility["default"]["faces"])?;
    let expected: BTreeSet<_> = blocks::rows(&members["blocks"])?
        .iter()
        .map(|b| blocks::text(&b["id"]))
        .collect::<Result<_>>()?;
    let actual: BTreeSet<_> = eligibility["blocks"]
        .as_object()
        .context("eligibility blocks must be explicit")?
        .keys()
        .map(String::as_str)
        .collect();
    ensure!(
        expected == actual,
        "eligibility block ids differ from allocation"
    );
    for key in ["members", "fixed", "shared"] {
        if let Some(value) = eligibility.get(key) {
            ensure!(value.is_object(), "eligibility {key} must be an object");
        }
    }
    if let Some(rows) = eligibility.get("members").and_then(Value::as_object) {
        ensure!(
            rows.keys().all(|r| members["roles"].get(r).is_some()),
            "eligibility names unknown member"
        );
    }
    for (reference, role) in members["roles"].as_object().unwrap() {
        member_faces(eligibility, reference, role)?;
    }
    if !contract["thickness_nm"].is_null() {
        ensure!(
            exact::integer(&contract["thickness_nm"])? > Int::zero(),
            "thickness_nm must be positive"
        );
    }
    let document = &contract["relationships"];
    if document != &json!({"schema":"agent-ee.electrical-locality/1","obligations":[]}) {
        locality::validate(document)?;
    }
    let pairs: BTreeSet<_> = blocks::rows(&document["obligations"])?
        .iter()
        .flat_map(|o| {
            locality::roles(o).map(move |r| (o["id"].as_str().unwrap().to_owned(), r.to_owned()))
        })
        .collect();
    let mut seen = BTreeSet::new();
    for row in blocks::rows(&contract["templates"])? {
        blocks::keys(row, &["relationship", "role", "template"], &[])?;
        let key = (
            blocks::text(&row["relationship"])?.to_owned(),
            blocks::text(&row["role"])?.to_owned(),
        );
        ensure!(
            pairs.contains(&key) && seen.insert(key),
            "template instance is duplicate or names an unknown relationship/role"
        );
        ensure!(
            matches!(
                row["template"].as_str(),
                Some("same-face-only" | "decoupler-under-device")
            ),
            "unsupported cross-face template"
        );
    }
    Ok(members)
}

/// Admission binding, before any search. Legacy plain candidate requests without
/// a contract remain unchanged; candidate sets always require the strict contract.
pub fn validate(capture: &Value, section: &Value, effective_policy: &Value) -> Result<()> {
    let sets = blocks::rows(&section["candidates"])?
        .iter()
        .any(|e| e["candidate"]["schema"] == crate::block_variants::SCHEMA);
    let Some(contract) = section.get("contract") else {
        ensure!(!sets, "candidate sets require block contract");
        return Ok(());
    };
    let members = declaration(capture, contract)?;
    ensure!(
        section.get("side_eligibility") == Some(&contract["eligibility"]),
        "contract eligibility differs from section side_eligibility"
    );
    let empty = json!({"schema":"agent-ee.electrical-locality/1","obligations":[]});
    ensure!(
        effective_policy
            .get("electrical_locality")
            .unwrap_or(&empty)
            == &contract["relationships"],
        "contract relationships differ from effective policy electrical_locality"
    );
    let raw = section["allocation_text"]
        .as_str()
        .context("contract requires explicit allocation_text")?;
    let allocation: Value = serde_json::from_str(raw)?;
    ensure!(
        allocation["schema"] == "agent-ee.block-allocation/1",
        "expected allocation/1 bridge"
    );
    let mut bridge = BTreeMap::new();
    for row in blocks::rows(&allocation["blocks"])? {
        let id = blocks::text(&row["id"])?;
        ensure!(
            row.get("sheets")
                .is_none_or(|v| v.as_array().is_some_and(Vec::is_empty)),
            "contract allocation bridge requires explicit references, not sheet selectors"
        );
        let references: BTreeSet<_> = blocks::refs(&row["references"])?.into_iter().collect();
        ensure!(
            bridge.insert(id.to_owned(), references).is_none(),
            "duplicate allocation bridge block"
        );
        let block = blocks::rows(&members["blocks"])?
            .iter()
            .find(|b| b["id"] == id)
            .context("allocation bridge has unknown block")?;
        if let Some(anchor) = block["anchor"].get("member") {
            ensure!(
                row.get("anchor") == Some(anchor),
                "allocation bridge member anchor differs"
            );
        }
    }
    let expected: BTreeMap<_, _> = blocks::rows(&members["blocks"])?
        .iter()
        .map(|b| {
            Ok((
                blocks::text(&b["id"])?.to_owned(),
                blocks::refs(&b["references"])?
                    .into_iter()
                    .collect::<BTreeSet<_>>(),
            ))
        })
        .collect::<Result<_>>()?;
    ensure!(
        bridge == expected,
        "allocation/1 bridge membership differs from strict allocation/2"
    );
    Ok(())
}

fn number(value: &Rat) -> Value {
    if value.is_integer() {
        exact::integer_value(value.numer())
    } else {
        exact::fraction_record(value)
    }
}
fn contains(point: &[Rat; 2], polygon: &geometry::Polygon) -> bool {
    let [x, y] = point;
    let mut inside = false;
    for (a, b) in geometry::edges(polygon) {
        let ax = Rat::from_integer(a.x.clone());
        let ay = Rat::from_integer(a.y.clone());
        let bx = Rat::from_integer(b.x.clone());
        let by = Rat::from_integer(b.y.clone());
        let cross = (&bx - &ax) * (y - &ay) - (&by - &ay) * (x - &ax);
        if cross.is_zero()
            && x >= std::cmp::min(&ax, &bx)
            && x <= std::cmp::max(&ax, &bx)
            && y >= std::cmp::min(&ay, &by)
            && y <= std::cmp::max(&ay, &by)
        {
            return true;
        }
        if (&Rat::from_integer(a.y.clone()) > y) != (&Rat::from_integer(b.y.clone()) > y) {
            let crossing = Rat::from_integer(a.x.clone())
                + (y - Rat::from_integer(a.y.clone())) * Rat::new(&b.x - &a.x, &b.y - &a.y);
            if x < &crossing {
                inside = !inside;
            }
        }
    }
    inside
}
fn distance2(point: &[Rat; 2], polygons: &[geometry::Polygon]) -> Rat {
    if polygons.iter().any(|p| contains(point, p)) {
        return Rat::zero();
    }
    polygons
        .iter()
        .flat_map(|p| geometry::edges(p))
        .map(|(a, b)| {
            let dx = &b.x - &a.x;
            let dy = &b.y - &a.y;
            let px = &point[0] - Rat::from_integer(a.x.clone());
            let py = &point[1] - Rat::from_integer(a.y.clone());
            let length = Rat::from_integer(&dx * &dx + &dy * &dy);
            let t = ((&px * Rat::from_integer(dx.clone()) + &py * Rat::from_integer(dy.clone()))
                / length)
                .max(Rat::zero())
                .min(Rat::from_integer(1.into()));
            let x = px - t.clone() * Rat::from_integer(dx);
            let y = py - t * Rat::from_integer(dy);
            &x * &x + &y * &y
        })
        .min()
        .expect("qualified polygons have edges")
}
fn compatible(members: &Value, from: &str, to: &str) -> bool {
    let a = &members["roles"][from];
    let b = &members["roles"][to];
    if a["role"] == "block" && b["role"] == "block" && a["block_id"] == b["block_id"] {
        return true;
    }
    a["role"] == "fixed"
        && b["role"] == "block"
        && members["blocks"].as_array().unwrap().iter().any(|block| {
            block["id"] == b["block_id"]
                && (block["anchor"]["fixed"] == from || block["interfaces_to_fixed"] == from)
        })
}

/// Complete-candidate feasibility, called after preflight on transformed native
/// facts. Decisions use rational 3-D distances; no Python planner or callbacks.
pub fn check_complete(capture: &Value, contract: &Value) -> Result<Value> {
    let mut binding = capture.clone();
    // A final saved hash naturally differs; declaration was bound at preflight.
    binding["board_sha256"] = contract["allocation"]["board_sha256"].clone();
    let members = declaration(&binding, contract)?;
    let board = blocks::board(capture)?;
    let footprints = blocks::rows(&capture["footprints"])?;
    let document = &contract["relationships"];
    let eligibility = &contract["eligibility"];
    let thickness = if contract["thickness_nm"].is_null() {
        None
    } else {
        Some(exact::integer(&contract["thickness_nm"])?)
    };
    let mut defects = Vec::new();
    let mut unknown = Vec::new();
    let mut measured = Vec::new();
    if thickness.is_none() {
        unknown.push(json!({"kind":"board_thickness_missing"}));
    }
    for (reference, role) in members["roles"].as_object().unwrap() {
        let allowed = member_faces(eligibility, reference, role)?;
        if let Some(allowed) = allowed {
            if !board[reference]["side"]
                .as_str()
                .is_some_and(|s| allowed.contains(s))
            {
                defects.push(json!({"kind":"forbidden_side","reference":reference,"side":board[reference]["side"]}));
            }
        } else {
            unknown.push(json!({"kind":"side_eligibility_missing","reference":reference}));
        }
    }
    for block in blocks::rows(&members["blocks"])? {
        let id = blocks::text(&block["id"])?;
        let anchor = block["anchor"]
            .get("member")
            .or_else(|| block["anchor"].get("fixed"));
        if let Some(anchor) = anchor.and_then(Value::as_str) {
            if !board[anchor]["side"]
                .as_str()
                .is_some_and(|side| block_faces(eligibility, id).is_ok_and(|s| s.contains(side)))
            {
                defects.push(json!({"kind":"forbidden_anchor_side","block":id,"reference":anchor}));
            }
        }
        let entry = &eligibility["blocks"][id];
        for key in [
            "material_unknowns",
            "region_restrictions",
            "same_face_relationships",
        ] {
            if entry
                .get(key)
                .is_some_and(|v| v.as_array().is_none_or(|v| !v.is_empty()))
            {
                unknown.push(json!({"kind":"eligibility_requirement_unresolved","block":id,"requirement":key,"declaration":entry[key]}));
            }
        }
    }
    if let Some(shared) = eligibility.get("shared").and_then(Value::as_object) {
        for (label, entry) in shared {
            if members["roles"].get(label).is_none()
                || entry.get("constraint").is_some_and(|v| !v.is_null())
            {
                unknown.push(json!({"kind":"shared_eligibility_requirement_unresolved","label":label,"declaration":entry}));
            }
        }
    }
    let templates: BTreeMap<_, _> = blocks::rows(&contract["templates"])?
        .iter()
        .map(|r| {
            (
                (
                    r["relationship"].as_str().unwrap(),
                    r["role"].as_str().unwrap(),
                ),
                r["template"].as_str().unwrap(),
            )
        })
        .collect();
    let mut covered = BTreeSet::new();
    for obligation in blocks::rows(&document["obligations"])? {
        for role in locality::roles(obligation) {
            let pair = &obligation[role];
            let id = blocks::text(&obligation["id"])?;
            let from = blocks::text(&pair["from"]["reference"])?;
            let to = blocks::text(&pair["to"]["reference"])?;
            covered.insert(from.to_owned());
            covered.insert(to.to_owned());
            let authority = locality::classify(pair, &document["source_records"]);
            let template = templates.get(&(id, role)).copied();
            let mut row = json!({"relationship":id,"role":role,"classification":"unknown","constraint_provenance":authority,"template":template});
            let outcome: Result<()> = (|| {
                ensure!(
                    obligation["metric"] == "pad-centre-to-centre",
                    "unsupported locality metric"
                );
                ensure!(
                    pair["from"]["net"] == pair["to"]["net"],
                    "locality endpoints must declare the same net"
                );
                let a = locality::resolve(footprints, &pair["from"])?;
                let b = locality::resolve(footprints, &pair["to"])?;
                let device = board.get(from).context("device footprint missing")?;
                let support = board.get(to).context("support footprint missing")?;
                ensure!(
                    ["F", "B"].iter().any(|s| device["side"] == *s)
                        && ["F", "B"].iter().any(|s| support["side"] == *s),
                    "native mounting side unavailable"
                );
                let cross = device["side"] != support["side"];
                let dz = if cross {
                    thickness
                        .clone()
                        .context("board thickness required for cross-face distance")?
                } else {
                    Int::zero()
                };
                let dx = &a[0] - &b[0];
                let dy = &a[1] - &b[1];
                let squared = &dx * &dx + &dy * &dy + Rat::from_integer(&dz * &dz);
                row["distance_squared_nm2"] = number(&squared);
                row["dz_nm"] = exact::integer_value(&dz);
                let bound = exact::integer(
                    pair.get("max_distance_nm")
                        .context("numeric relationship bound missing")?,
                )?;
                let exceeded = squared > Rat::from_integer(&bound * &bound);
                row["exceeded"] = json!(exceeded);
                ensure!(
                    authority["enforcement"] != "integrity",
                    "{}",
                    authority["reason"]
                        .as_str()
                        .unwrap_or("source integrity failure")
                );
                let hard = authority["enforcement"] == "hard";
                if hard && exceeded {
                    defects.push(json!({"kind":"pin_distance_exceeded","relationship":id,"role":role,"distance_squared_nm2":number(&squared)}));
                }
                if !hard {
                    unknown.push(json!({"kind":"required_numeric_authority_missing","relationship":id,"role":role,"reason":authority["reason"]}));
                }
                if !cross {
                    row["classification"] = json!("same_face");
                    return Ok(());
                }
                if !compatible(&members, from, to) {
                    row["classification"] = json!("lost_cohesion");
                    defects.push(
                        json!({"kind":"cross_face_membership","relationship":id,"role":role}),
                    );
                    return Ok(());
                }
                if template != Some("decoupler-under-device") {
                    row["classification"] = json!("lost_cohesion");
                    defects.push(json!({"kind":if template.is_none(){"cross_face_template_missing"}else{"same_face_required"},"relationship":id,"role":role}));
                    return Ok(());
                }
                if exceeded || &bound * &bound < &dz * &dz {
                    row["classification"] = json!("lost_cohesion");
                    return Ok(());
                }
                if !hard {
                    return Ok(());
                }
                let allowance = Rat::from_integer(&bound * &bound - &dz * &dz);
                let (outer, inner) = geometry::courtyard_bounds(
                    &device["courtyards"][device["side"].as_str().unwrap()],
                )?;
                let lower = distance2(&b, &outer);
                let upper = distance2(&b, &inner);
                if lower > allowance {
                    row["classification"] = json!("lost_cohesion");
                    defects.push(
                        json!({"kind":"cross_face_template_outside","relationship":id,"role":role}),
                    );
                } else if upper <= allowance {
                    row["classification"] = json!("deliberate_cross_face");
                } else {
                    anyhow::bail!(
                        "cross-face template lies within courtyard approximation uncertainty"
                    );
                }
                Ok(())
            })();
            if let Err(error) = outcome {
                unknown.push(json!({"kind":"relationship_requirement_missing","relationship":id,"role":role,"reason":format!("{error:#}")}));
            }
            measured.push(row);
        }
    }
    for block in blocks::rows(&members["blocks"])? {
        let references = blocks::refs(&block["references"])?;
        let missing: Vec<_> = references
            .iter()
            .filter(|r| {
                !covered.contains(*r)
                    && board[*r]["pads"].as_array().is_some_and(|pads| {
                        pads.iter()
                            .any(|p| p["net"].as_str().is_some_and(|net| !net.is_empty()))
                    })
            })
            .collect();
        if references.len() > 1 && !missing.is_empty() {
            unknown.push(json!({"kind":"block_pin_relationships_missing","block":block["id"],"references":missing}));
        }
    }
    Ok(
        json!({"schema":"agent-ee.block-contract-check/1","status":if !defects.is_empty(){"defect"}else if !unknown.is_empty(){"unknown"}else{"pass"},
        "contract_sha256":blocks::digest(contract),"board_sha256":capture["board_sha256"],"defects":defects,"unknown":unknown,"relationships":measured}),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use sha2::{Digest, Sha256};

    fn fixture() -> (Value, Value) {
        let mut capture = json!({"schema":"agent-ee.placement-probe/1","board_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "footprints":[
                {"ref":"U1","uuid":"device","side":"F","position_nm":[0,0],"pads":[],"courtyards":{"F":{"status":"proven","polygons":[[[0,0],[2000000,0],[2000000,2000000],[0,2000000]]]}}},
                {"ref":"C1","uuid":"support","side":"F","position_nm":[2000000,0],"pads":[],"courtyards":{"F":{"status":"proven","polygons":[[[2000000,0],[4000000,0],[4000000,2000000],[2000000,2000000]]]}}},
                {"ref":"H1","uuid":"board-only","side":"F","position_nm":[9000000,0],"pads":[]} ]});
        for (i, x) in [0, 2000000].into_iter().enumerate() {
            capture["footprints"][i]["pads"] = json!([
                {"number":"1","net":"VCC","position_nm":[x,0],"copper_centroid_offset_nm":[[0,1],[0,1]]},
                {"number":"2","net":"GND","position_nm":[x,1000000],"copper_centroid_offset_nm":[[0,1],[0,1]]}]);
        }
        let quote = "Original synthetic numeric requirement: 3 mm";
        let source = json!({"path":"fixture/limits.txt","sha256":format!("{:x}",Sha256::digest(quote.as_bytes())),"locator":"numeric row","quote":quote});
        let pair = |pad, net| {
            json!({"from":{"reference":"U1","pad":pad,"net":net},"to":{"reference":"C1","pad":pad,"net":net},"max_distance_nm":3000000,
            "provenance":{"class":"datasheet","basis":"authored numeric fixture","derivation":{"kind":"numeric","value_nm":3000000,"source":source}}})
        };
        let contract = json!({"schema":SCHEMA,"allocation":{"schema":"agent-ee.block-allocation/2","board_sha256":capture["board_sha256"],
            "blocks":[{"id":"device","references":["U1","C1"],"anchor":{"member":"U1"},"interfaces_to_fixed":null}],"fixed":[{"reference":"H1"}],"shared":[]},
            "eligibility":{"schema":"fome-mc.side-eligibility/1","sources":{"board_sha256":capture["board_sha256"]},"default":{"faces":["F","B"]},
                "blocks":{"device":{"eligible_faces":["F","B"]}},"fixed":{"H1":{"eligible_faces":["F","B"]}}},
            "relationships":{"schema":"agent-ee.electrical-locality/1","source_records":{"fixture/limits.txt":quote},"obligations":[{"id":"support","metric":"pad-centre-to-centre","supply":pair("1","VCC"),"return":pair("2","GND")}]},
            "templates":[],"thickness_nm":1600000});
        (capture, contract)
    }
    fn flip(capture: &mut Value, x: i64) {
        let fp = &mut capture["footprints"][1];
        fp["side"] = json!("B");
        fp["courtyards"] = json!({"B":fp["courtyards"]["F"]});
        for pad in fp["pads"].as_array_mut().unwrap() {
            pad["position_nm"][0] = json!(x);
        }
    }
    #[test]
    fn strict_roles_and_policy_binding() {
        let (capture, contract) = fixture();
        let mut section = json!({"contract":contract,"side_eligibility":contract["eligibility"],"candidates":[],
            "allocation_text":serde_json::to_string(&json!({"schema":"agent-ee.block-allocation/1","blocks":[{"id":"device","references":["U1","C1"],"anchor":"U1"}]})).unwrap()});
        let policy = json!({"electrical_locality":contract["relationships"]});
        validate(&capture, &section, &policy).unwrap();
        section["contract"]["allocation"]["fixed"] = json!([]);
        assert!(
            validate(&capture, &section, &policy)
                .unwrap_err()
                .to_string()
                .contains("unallocated")
        );
        section["contract"] = contract;
        assert!(validate(&capture, &section, &json!({"electrical_locality":{}})).is_err());
    }
    #[test]
    fn split_needs_template_and_exact_thickness() {
        let (mut capture, mut contract) = fixture();
        assert_eq!(
            check_complete(&capture, &contract).unwrap()["status"],
            "pass"
        );
        flip(&mut capture, 2000000);
        let report = check_complete(&capture, &contract).unwrap();
        assert_eq!(report["status"], "defect");
        assert_eq!(
            report["relationships"][0]["distance_squared_nm2"],
            json!(6560000000000_i64)
        );
        contract["templates"] = json!([{"relationship":"support","role":"supply","template":"decoupler-under-device"},
            {"relationship":"support","role":"return","template":"decoupler-under-device"}]);
        assert_eq!(
            check_complete(&capture, &contract).unwrap()["status"],
            "pass"
        );
        capture["footprints"][1]["pads"][0]["position_nm"][0] = json!(2800000);
        assert_eq!(
            check_complete(&capture, &contract).unwrap()["status"],
            "defect"
        );
    }
    #[test]
    fn ungoverned_number_and_missing_thickness_are_unknown() {
        let (capture, mut contract) = fixture();
        contract["relationships"]["obligations"][0]["supply"]["provenance"] =
            json!({"class":"engineering","basis":"estimate"});
        let report = check_complete(&capture, &contract).unwrap();
        assert_eq!(report["status"], "unknown");
        assert!(report["defects"].as_array().unwrap().is_empty());
        contract["thickness_nm"] = Value::Null;
        assert_eq!(
            check_complete(&capture, &contract).unwrap()["unknown"][0]["kind"],
            "board_thickness_missing"
        );
    }
    #[test]
    fn rational_region_distance_and_closed_boundary() {
        let polygon = vec![
            exact::Point::new(0, 0),
            exact::Point::new(2, 0),
            exact::Point::new(2, 2),
            exact::Point::new(0, 2),
        ];
        assert_eq!(
            distance2(
                &[Rat::new(1.into(), 3.into()), Rat::from_integer(2.into())],
                std::slice::from_ref(&polygon)
            ),
            Rat::zero()
        );
        assert_eq!(
            distance2(
                &[Rat::new(7.into(), 3.into()), Rat::from_integer(2.into())],
                &[polygon]
            ),
            Rat::new(1.into(), 9.into())
        );
    }
}
