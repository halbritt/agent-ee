//! Block identity, allocation and admission against captured native facts.
use crate::netlist::NativeInventory;
use crate::{exact, footprint, geometry, groups, locality, policy};
use anyhow::{ensure, Context, Result};
use num_bigint::BigInt;
use num_rational::BigRational;
use num_traits::{Signed, Zero};
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use std::cmp::Ordering;
use std::collections::{BTreeMap, BTreeSet};

pub fn rows(value: &Value) -> Result<&Vec<Value>> {
    value.as_array().context("expected array")
}
pub fn text(value: &Value) -> Result<&str> {
    let s = value.as_str().context("expected text")?;
    ensure!(!s.trim().is_empty(), "expected nonempty text");
    Ok(s)
}
pub fn refs(value: &Value) -> Result<Vec<String>> {
    let result: Vec<String> = rows(value)?
        .iter()
        .map(|v| text(v).map(str::to_owned))
        .collect::<Result<_>>()?;
    ensure!(
        result.iter().collect::<BTreeSet<_>>().len() == result.len(),
        "references must be unique"
    );
    Ok(result)
}
pub fn keys(value: &Value, required: &[&str], optional: &[&str]) -> Result<()> {
    let obj = value.as_object().context("expected object")?;
    ensure!(
        required.iter().all(|k| obj.contains_key(*k))
            && obj
                .keys()
                .all(|k| required.contains(&k.as_str()) || optional.contains(&k.as_str())),
        "unexpected or missing record keys"
    );
    Ok(())
}
fn python_float(number: &serde_json::Number) -> String {
    let value = number.as_f64().expect("finite JSON float");
    if value == 0.0 {
        return if value.is_sign_negative() {
            "-0.0".into()
        } else {
            "0.0".into()
        };
    }
    let encoded = serde_json::Number::from_f64(value)
        .expect("finite JSON float")
        .to_string();
    let negative = encoded.starts_with('-');
    let unsigned = encoded.trim_start_matches('-');
    let (mantissa, exponent) = unsigned
        .split_once(['e', 'E'])
        .map_or((unsigned, 0), |(m, e)| (m, e.parse::<i32>().unwrap()));
    let decimal = mantissa.find('.').unwrap_or(mantissa.len()) as i32;
    let all: String = mantissa.chars().filter(|c| *c != '.').collect();
    let first = all.find(|c| c != '0').unwrap();
    let digits = all[first..].trim_end_matches('0');
    let exponent = exponent + decimal - first as i32 - 1;
    let mut out = if negative {
        "-".to_owned()
    } else {
        String::new()
    };
    if (-4..16).contains(&exponent) {
        let split = exponent + 1;
        if split <= 0 {
            out.push_str("0.");
            out.extend(std::iter::repeat_n('0', (-split) as usize));
            out.push_str(digits);
        } else if split as usize >= digits.len() {
            out.push_str(digits);
            out.extend(std::iter::repeat_n('0', split as usize - digits.len()));
            out.push_str(".0");
        } else {
            out.push_str(&digits[..split as usize]);
            out.push('.');
            out.push_str(&digits[split as usize..]);
        }
    } else {
        out.push(digits.as_bytes()[0] as char);
        if digits.len() > 1 {
            out.push('.');
            out.push_str(&digits[1..]);
        }
        out.push('e');
        out.push(if exponent < 0 { '-' } else { '+' });
        out.push_str(&format!("{:02}", exponent.abs()));
    }
    out
}
pub fn canonical(value: &Value) -> Vec<u8> {
    fn write(value: &Value, out: &mut String) {
        match value {
            Value::Object(object) => {
                out.push('{');
                let ordered: BTreeMap<_, _> = object.iter().collect();
                for (i, (k, v)) in ordered.into_iter().enumerate() {
                    if i > 0 {
                        out.push(',');
                    }
                    out.push_str(&serde_json::to_string(k).unwrap());
                    out.push(':');
                    write(v, out);
                }
                out.push('}');
            }
            Value::Array(rows) => {
                out.push('[');
                for (i, v) in rows.iter().enumerate() {
                    if i > 0 {
                        out.push(',');
                    }
                    write(v, out);
                }
                out.push(']');
            }
            Value::Number(n) if n.is_f64() => out.push_str(&python_float(n)),
            Value::Number(n) => out.push_str(
                &n.to_string()
                    .parse::<BigInt>()
                    .expect("integer JSON number")
                    .to_string(),
            ),
            _ => out.push_str(&serde_json::to_string(value).unwrap()),
        }
    }
    let mut encoded = String::new();
    write(value, &mut encoded);
    encoded.into_bytes()
}

pub fn digest(value: &Value) -> String {
    format!("{:x}", Sha256::digest(canonical(value)))
}
pub fn natural_cmp(a: &str, b: &str) -> Ordering {
    fn split(s: &str) -> (&str, BigInt) {
        let head = s.trim_end_matches(|c: char| c.is_ascii_digit());
        let tail = &s[head.len()..];
        (
            head,
            if tail.is_empty() {
                BigInt::from(-1)
            } else {
                tail.parse().unwrap()
            },
        )
    }
    let (ah, an) = split(a);
    let (bh, bn) = split(b);
    ah.cmp(bh).then(an.cmp(&bn)).then(a.cmp(b))
}
pub fn natural(mut names: Vec<String>) -> Vec<String> {
    names.sort_by(|a, b| natural_cmp(a, b));
    names
}
pub fn board(capture: &Value) -> Result<BTreeMap<String, &Value>> {
    let mut result = BTreeMap::new();
    for fp in rows(&capture["footprints"])? {
        let r = text(&fp["ref"])?;
        ensure!(
            result.insert(r.to_owned(), fp).is_none(),
            "duplicate footprint reference"
        );
    }
    Ok(result)
}
fn field_refs(value: &Value, key: &str) -> Result<Vec<String>> {
    match value.get(key) {
        Some(v) => refs(v),
        None => Ok(Vec::new()),
    }
}
pub fn derive(
    inventory: &NativeInventory,
    capture: &Value,
    allocation: Option<&str>,
) -> Result<Value> {
    let board = board(capture)?;
    let parts: BTreeMap<_, _> = inventory
        .components
        .iter()
        .filter(|(_, v)| v["on_board"] == true)
        .collect();
    for (r, c) in &parts {
        let fp = board
            .get(*r)
            .with_context(|| format!("{r}: on-board component has no board footprint"))?;
        ensure!(
            c["footprint"] == fp["fpid"],
            "{r}: board footprint differs from the native netlist"
        );
    }
    let mut residual = BTreeMap::<String, String>::new();
    for (r, fp) in &board {
        if !parts.contains_key(r) {
            ensure!(
                !inventory.components.contains_key(r)
                    && !rows(&fp["pads"])?
                        .iter()
                        .any(|p| p["net"].as_str().is_some_and(|s| !s.is_empty())),
                "{r}: board footprint is not an on-board native component"
            );
            residual.insert(r.clone(), "board-only".into());
        }
    }
    let top: BTreeMap<String, String> = parts
        .iter()
        .map(|(r, c)| {
            let sheet = c["sheet"].as_str().unwrap();
            let first = sheet.trim_matches('/').split('/').next().unwrap_or("");
            (
                (*r).clone(),
                if first.is_empty() {
                    "/".into()
                } else {
                    format!("/{first}/")
                },
            )
        })
        .collect();
    let mut blocks = Vec::new();
    let mut claims = BTreeMap::<String, String>::new();
    if let Some(raw) = allocation {
        let doc: Value = serde_json::from_str(raw)?;
        keys(&doc, &["schema", "blocks"], &["residual"])?;
        ensure!(
            doc["schema"] == "agent-ee.block-allocation/1" && !rows(&doc["blocks"])?.is_empty(),
            "expected nonempty block allocation"
        );
        let by_name: BTreeMap<_, _> = inventory
            .sheet_names
            .iter()
            .map(|(k, v)| (v.clone(), k.clone()))
            .collect();
        let mut ids = BTreeSet::new();
        for entry in rows(&doc["blocks"])? {
            keys(entry, &["id"], &["sheets", "references", "anchor"])?;
            let id = text(&entry["id"])?;
            ensure!(ids.insert(id.to_owned()), "duplicate block id");
            let declared_sheets = field_refs(entry, "sheets")?;
            let declared_refs = field_refs(entry, "references")?;
            ensure!(
                !declared_sheets.is_empty() || !declared_refs.is_empty(),
                "block {id}: needs sheets or references"
            );
            let mut members = BTreeSet::new();
            let mut sheets = Vec::new();
            for sheet in declared_sheets {
                let path = by_name.get(&sheet).unwrap_or(&sheet);
                ensure!(
                    path != "/" && inventory.sheet_names.contains_key(path),
                    "block {id}: unknown child sheet {sheet}"
                );
                let inside: BTreeSet<String> = parts
                    .iter()
                    .filter(|(_, c)| c["sheet"].as_str().unwrap().starts_with(path))
                    .map(|(r, _)| (*r).clone())
                    .collect();
                ensure!(
                    !inside.is_empty(),
                    "block {id}: sheet {sheet} has no on-board footprints"
                );
                ensure!(
                    inside.is_disjoint(&members),
                    "block {id}: allocated twice by overlapping sheets"
                );
                members.extend(inside);
                sheets.push(path.clone());
            }
            for r in declared_refs {
                ensure!(
                    parts.contains_key(&r),
                    "block {id}: {r} is not an on-board native component"
                );
                ensure!(members.insert(r), "block member allocated twice");
            }
            for r in &members {
                ensure!(
                    claims.insert(r.clone(), id.to_owned()).is_none(),
                    "{r}: allocated to multiple blocks"
                );
            }
            let mut item = json!({"id":id,"origin":"allocation","sheets":sheets,"references":natural(members.iter().cloned().collect())});
            if let Some(anchor) = entry.get("anchor") {
                ensure!(
                    members.contains(text(anchor)?),
                    "block {id}: anchor must be a member"
                );
                item["anchor"] = anchor.clone();
            }
            item["sheets"]
                .as_array_mut()
                .unwrap()
                .sort_by(|a, b| a.as_str().cmp(&b.as_str()));
            blocks.push(item);
        }
        for r in field_refs(&doc, "residual")? {
            ensure!(
                board.contains_key(&r),
                "residual {r} is not a board footprint"
            );
            ensure!(
                claims.insert(r.clone(), "residual".into()).is_none(),
                "{r}: allocated twice"
            );
            residual.entry(r).or_insert("allocated-residual".into());
        }
        let loose = natural(
            top.iter()
                .filter(|(r, s)| s.as_str() != "/" && !claims.contains_key(*r))
                .map(|(r, _)| r.clone())
                .collect(),
        );
        ensure!(
            loose.is_empty(),
            "child-sheet components neither allocated to a block nor declared residual: {}",
            loose.join(", ")
        );
    } else {
        let mut sheets: Vec<String> = top
            .values()
            .filter(|s| s.as_str() != "/")
            .cloned()
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect();
        sheets.sort_by(|a, b| {
            natural_cmp(
                inventory.sheet_names.get(a).unwrap_or(a).trim_matches('/'),
                inventory.sheet_names.get(b).unwrap_or(b).trim_matches('/'),
            )
        });
        for sheet in sheets {
            blocks.push(json!({"id":inventory.sheet_names.get(&sheet).unwrap_or(&sheet).trim_matches('/'),"origin":"sheet","sheets":[sheet],"references":natural(top.iter().filter(|(_,s)|**s==sheet).map(|(r,_)|r.clone()).collect())}));
        }
    }
    for (r, s) in top {
        if s == "/" && !claims.contains_key(&r) {
            residual.insert(r, "root-sheet".into());
        }
    }
    let locked = natural(
        blocks
            .iter()
            .flat_map(|b| b["references"].as_array().unwrap())
            .filter_map(|r| {
                let r = r.as_str().unwrap();
                (board[r]["locked"] == true).then(|| r.to_owned())
            })
            .collect(),
    );
    ensure!(
        locked.is_empty(),
        "locked footprints cannot be rigid-block members; allocate them to the residual set: {}",
        locked.join(", ")
    );
    Ok(
        json!({"schema":"agent-ee.block-derivation/1","board_sha256":capture["board_sha256"],"inventory_sha256":inventory.sha256,"allocation_sha256":allocation.map(|s|format!("{:x}",Sha256::digest(s.as_bytes()))),"blocks":blocks,"residual":natural(residual.keys().cloned().collect()).into_iter().map(|r|json!({"reference":r,"reason":residual[&r]})).collect::<Vec<_>>()}),
    )
}

pub fn pad_nets(fp: &Value) -> Result<BTreeMap<String, String>> {
    let mut nets = BTreeMap::new();
    for pad in rows(&fp["pads"])? {
        let number = pad["number"].as_str().context("pad number must be text")?;
        if number.is_empty() {
            continue;
        }
        let net = pad["net"].as_str().unwrap_or("");
        if let Some(old) = nets.insert(number.to_owned(), net.to_owned()) {
            ensure!(
                old == net,
                "{}: pad {number} carries several nets",
                fp["ref"]
            );
        }
    }
    Ok(nets)
}
pub fn anchor(block: &Value, board: &BTreeMap<String, &Value>) -> Result<String> {
    if let Some(a) = block.get("anchor") {
        return Ok(text(a)?.to_owned());
    }
    let mut members = refs(&block["references"])?;
    members.sort_by(|a, b| {
        let count = |r: &String| {
            board[r]["pads"]
                .as_array()
                .unwrap()
                .iter()
                .filter(|p| p["number"].as_str().is_some_and(|v| !v.is_empty()))
                .count()
        };
        count(b).cmp(&count(a)).then(natural_cmp(a, b))
    });
    members.into_iter().next().context("empty block")
}
pub fn isolate(capture: &Value, policy: &Value, block: &Value) -> Result<(String, Value, Value)> {
    let board = board(capture)?;
    let members: BTreeSet<String> = refs(&block["references"])?.into_iter().collect();
    let fixed: BTreeSet<String> = field_refs(policy, "fixed_refs")?
        .into_iter()
        .chain(
            policy
                .get("fixed_overhangs")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .filter_map(|o| o["reference"].as_str().map(str::to_owned)),
        )
        .collect();
    ensure!(
        members.is_disjoint(&fixed),
        "block {}: fixed references cannot be rigid-block members",
        block["id"]
    );
    let anchor = anchor(block, &board)?;
    let mut sub = json!({"schema":"agent-ee.packing-policy/1","step_budget":policy["step_budget"],"gap_nm":policy["gap_nm"],"priority_refs":[],"ground_nets":policy["ground_nets"],"obstacles":[],"fixed_refs":[anchor]});
    if let Some(t) = policy.get("search_timeout_s") {
        sub["search_timeout_s"] = t.clone();
    }
    let mut deferred = json!({"groups":[],"obligations":[],"obstacles":rows(&policy["obstacles"])?.len(),"board_rule_areas":capture.get("rule_areas").and_then(Value::as_array).into_iter().flatten().filter(|a|a["owner"]=="board").count()});
    if let Some(bound) = policy.get("mechanical_reservations") {
        let selected = crate::mechanical::for_block(bound, &members)?;
        let included: BTreeSet<_> = refs(&selected["selection"]["reservation_ids"])?
            .into_iter()
            .collect();
        let mut excluded = Vec::new();
        for row in rows(&selected["document"]["reservations"])? {
            let id = text(&row["id"])?;
            if !included.contains(id) {
                excluded.push(id.to_owned());
            }
        }
        excluded.sort();
        deferred["mechanical_reservations"] = json!(excluded);
        sub["mechanical_reservations"] = selected;
    }
    let mut internal_groups = Vec::new();
    for group in policy
        .get("groups")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
    {
        let mut involved: BTreeSet<String> = refs(&group["references"])?.into_iter().collect();
        if let Some(a) = group["anchor"]["reference"].as_str() {
            involved.insert(a.to_owned());
        }
        if involved.is_disjoint(&members) {
            continue;
        }
        if involved.is_subset(&members)
            && group["anchor"].get("reference").is_some()
            && group.get("region").is_none()
        {
            internal_groups.push(group.clone());
        } else {
            deferred["groups"]
                .as_array_mut()
                .unwrap()
                .push(group["id"].clone());
        }
    }
    if !internal_groups.is_empty() {
        sub["groups"] = json!(internal_groups);
    }
    if let Some(local) = policy.get("electrical_locality") {
        let mut internal = Vec::new();
        for obligation in rows(&local["obligations"])? {
            let mut involved = BTreeSet::new();
            for (_, pair) in obligation
                .as_object()
                .context("obligation must be object")?
            {
                for endpoint in ["from", "to"] {
                    if let Some(r) = pair
                        .get(endpoint)
                        .and_then(|p| p.get("reference"))
                        .and_then(Value::as_str)
                    {
                        involved.insert(r.to_owned());
                    }
                }
            }
            if involved.is_subset(&members) {
                internal.push(obligation.clone());
            } else if !involved.is_disjoint(&members) {
                deferred["obligations"]
                    .as_array_mut()
                    .unwrap()
                    .push(obligation["id"].clone());
            }
        }
        if !internal.is_empty() {
            let mut v = local.clone();
            v["obligations"] = json!(internal);
            sub["electrical_locality"] = v;
        }
    }
    let grounds: BTreeSet<String> = refs(&policy["ground_nets"])?.into_iter().collect();
    let nets: BTreeMap<String, BTreeSet<String>> = members
        .iter()
        .map(|r| {
            Ok((
                r.clone(),
                pad_nets(&board[r])?
                    .into_values()
                    .filter(|n| !n.is_empty() && !grounds.contains(n))
                    .collect(),
            ))
        })
        .collect::<Result<_>>()?;
    let mut order: Vec<String> = refs(&policy["priority_refs"])?
        .into_iter()
        .filter(|r| members.contains(r) && r != &anchor)
        .collect();
    let mut seen: BTreeSet<String> = order.iter().cloned().chain([anchor.clone()]).collect();
    let mut frontier = vec![anchor.clone()];
    while !frontier.is_empty() {
        let mut reached: Vec<String> = members
            .iter()
            .filter(|r| {
                !seen.contains(*r) && frontier.iter().any(|f| !nets[*r].is_disjoint(&nets[f]))
            })
            .cloned()
            .collect();
        reached.sort_by(|a, b| {
            let count = |r: &String| {
                board[r]["pads"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .filter(|p| p["number"].as_str().is_some_and(|v| !v.is_empty()))
                    .count()
            };
            count(b).cmp(&count(a)).then(natural_cmp(a, b))
        });
        seen.extend(reached.clone());
        order.extend(reached.clone());
        frontier = reached;
    }
    sub["priority_refs"] = json!(order);
    Ok((anchor, sub, deferred))
}

fn integer_number(value: &Value) -> Value {
    match value {
        Value::Array(a) => json!(a.iter().map(integer_number).collect::<Vec<_>>()),
        Value::Object(o) => Value::Object(
            o.iter()
                .map(|(k, v)| (k.clone(), integer_number(v)))
                .collect(),
        ),
        Value::Number(n) => {
            if n.is_f64() {
                if let Some(f) = n.as_f64() {
                    if f.fract() == 0.0 {
                        return serde_json::from_str(&format!("{f:.0}"))
                            .unwrap_or_else(|_| value.clone());
                    }
                }
            }
            value.clone()
        }
        _ => value.clone(),
    }
}
pub fn rings(polygons: &Value) -> Result<Value> {
    let mut result: Vec<Vec<exact::Point>> = Vec::new();
    for ring in rows(polygons)? {
        let mut points = rows(ring)?
            .iter()
            .map(exact::point)
            .collect::<Result<Vec<_>>>()?;
        if points.is_empty() {
            result.push(points);
            continue;
        }
        let area = geometry::area2(&points);
        if area.is_negative() {
            points.reverse();
        }
        let mut variants = Vec::new();
        for i in 0..points.len() {
            variants.push(
                points[i..]
                    .iter()
                    .chain(&points[..i])
                    .cloned()
                    .collect::<Vec<_>>(),
            );
        }
        if area.is_zero() {
            points.reverse();
            for i in 0..points.len() {
                variants.push(points[i..].iter().chain(&points[..i]).cloned().collect());
            }
        }
        variants.sort_by(|a, b| {
            a.iter()
                .map(|p| (&p.x, &p.y))
                .cmp(b.iter().map(|p| (&p.x, &p.y)))
        });
        result.push(variants.remove(0));
    }
    result.sort_by(|a, b| {
        a.iter()
            .map(|p| (&p.x, &p.y))
            .cmp(b.iter().map(|p| (&p.x, &p.y)))
    });
    Ok(json!(result
        .iter()
        .map(|r| r.iter().map(exact::point_value).collect::<Vec<_>>())
        .collect::<Vec<_>>()))
}
pub fn local_geometry(fp: &Value) -> Result<(Value, Value)> {
    let local = footprint::transform_value(fp, &json!([0, 0]), 0)?;
    let mut pads = Vec::new();
    for p in rows(&local["pads"])? {
        let mut p = p.clone();
        for key in ["uuid", "net", "copper_centroid_reason"] {
            p.as_object_mut().context("pad object")?.remove(key);
        }
        if let Some(polys) = p["custom_geometry"].get("merged_polygons_nm") {
            let normalized = rings(polys)?;
            p["custom_geometry"]["merged_polygons_nm"] = normalized;
        }
        pads.push(integer_number(&p));
    }
    pads.sort_by_key(canonical);
    let mut courtyards = Map::new();
    for (side, c) in local["courtyards"]
        .as_object()
        .context("courtyards object")?
    {
        let mut record = Map::new();
        for k in ["status", "polygons", "inner_polygons", "approximation"] {
            if let Some(v) = c.get(k) {
                record.insert(
                    k.into(),
                    if k == "polygons" || k == "inner_polygons" {
                        rings(v)?
                    } else {
                        v.clone()
                    },
                );
            }
        }
        courtyards.insert(side.clone(), Value::Object(record));
    }
    Ok((
        json!({"side":fp["side"],"pads":pads}),
        json!({"side":fp["side"],"courtyards":courtyards}),
    ))
}
pub fn frame_member(fp: &Value, pose: &Value) -> Result<Value> {
    let (_, courtyard) = local_geometry(fp)?;
    let mut local = footprint::transform_value(fp, &json!([0, 0]), 0)?;
    local["courtyards"] = courtyard["courtyards"].clone();
    footprint::transform_value(
        &local,
        &pose["position_nm"],
        pose["rotation_deg"].as_i64().context("quarter rotation")?,
    )
}

pub fn evidence(capture: &Value, framed: &[Value], sub: &Value) -> Result<(Value, Value, Value)> {
    let mut polygons = BTreeMap::<String, Vec<geometry::Polygon>>::new();
    let mut courtyards = Vec::with_capacity(framed.len());
    for fp in framed {
        let mut member = Vec::new();
        for (side, c) in fp["courtyards"].as_object().context("courtyards object")? {
            let outer = geometry::courtyard_bounds(c)?.0;
            polygons
                .entry(side.clone())
                .or_default()
                .extend(outer.iter().cloned());
            member.push((side.clone(), outer));
        }
        member.sort_by(|a, b| a.0.cmp(&b.0));
        courtyards.push(member);
    }
    let mut outline = Map::new();
    for (side, polys) in polygons {
        let b = geometry::bounds(&polys)?;
        outline.insert(side,json!({"box_nm":([b.x0,b.y0,b.x1,b.y1].iter().map(exact::integer_value).collect::<Vec<_>>()),"courtyards":polys.iter().map(|r|r.iter().map(exact::point_value).collect::<Vec<_>>()).collect::<Vec<_>>()}));
    }
    let mut framed_capture = Value::Object(
        capture
            .as_object()
            .context("capture object")?
            .iter()
            .map(|(k, v)| {
                (
                    k.clone(),
                    if k == "footprints" {
                        Value::Null
                    } else {
                        v.clone()
                    },
                )
            })
            .collect(),
    );
    framed_capture["footprints"] = json!(framed);
    let mut grade = json!({"electrical_locality":match sub.get("electrical_locality"){Some(p)=>locality::grade(&framed_capture,p)?,None=>Value::Null},"groups":match sub.get("groups"){Some(p)=>groups::grade(&framed_capture,p)?,None=>Value::Null}});
    if let Some(bound) = sub.get("mechanical_reservations") {
        grade["mechanical_reservations"] = crate::mechanical::grade(&framed_capture, bound, false)?;
    }
    let gap = exact::integer(&sub["gap_nm"])?;
    let gap2 = BigRational::from_integer(&gap * &gap);
    let mut minimum = BTreeMap::<String, (BigRational, Value)>::new();
    let mut short = Vec::new();
    let mut checked = 0;
    for (i, a) in framed.iter().enumerate() {
        for (j, b) in framed.iter().enumerate().skip(i + 1) {
            for (side, left) in &courtyards[i] {
                let Some((_, right)) = courtyards[j].iter().find(|(other, _)| other == side) else {
                    continue;
                };
                let mut distance: Option<BigRational> = None;
                for p in left {
                    for q in right {
                        if geometry::positive_overlap(p, q) {
                            distance = Some(BigRational::zero());
                            break;
                        }
                        for j in 0..p.len() {
                            for k in 0..q.len() {
                                let d = geometry::segment_distance2(
                                    &p[j],
                                    &p[(j + 1) % p.len()],
                                    &q[k],
                                    &q[(k + 1) % q.len()],
                                );
                                if distance.as_ref().map_or(true, |old| d < *old) {
                                    distance = Some(d);
                                }
                            }
                        }
                    }
                }
                let d = distance.context("empty courtyard separation")?;
                let mut record = json!({"refs":[a["ref"],b["ref"]],"distance_squared_nm2":[serde_json::from_str::<Value>(&d.numer().to_string())?,serde_json::from_str::<Value>(&d.denom().to_string())?]});
                checked += 1;
                if minimum.get(side).map_or(true, |(old, _)| d < *old) {
                    minimum.insert(side.clone(), (d.clone(), record.clone()));
                }
                if d < gap2 {
                    record["side"] = json!(side);
                    short.push(record);
                }
            }
        }
    }
    let mut holes = Vec::new();
    let mut prepared: Vec<Option<footprint::PreparedFootprint>> =
        (0..framed.len()).map(|_| None).collect();
    for (i, a) in framed.iter().enumerate() {
        for (j, b) in framed.iter().enumerate().skip(i + 1) {
            if a.get("packing_pad_geometry").is_some() && b.get("packing_pad_geometry").is_some() {
                if prepared[i].is_none() {
                    prepared[i] = Some(footprint::PreparedFootprint::from_value(a, None)?);
                }
                if prepared[j].is_none() {
                    prepared[j] = Some(footprint::PreparedFootprint::from_value(b, None)?);
                }
                if crate::constraints::hole_conflict(
                    prepared[i].as_ref().unwrap(),
                    prepared[j].as_ref().unwrap(),
                ) {
                    holes.push(json!([a["ref"], b["ref"]]));
                }
            }
        }
    }
    let witness = json!({"method":"exact-courtyard-separation/1","gap_nm":sub["gap_nm"],"pairs_checked":checked,"status":if short.is_empty()&&holes.is_empty(){"pass"}else{"defect"},"minimum":minimum.into_iter().map(|(k,(_,v))|(k,v)).collect::<Map<_,_>>(),"below_gap":short,"drilled_pad_conflicts":holes});
    Ok((Value::Object(outline), grade, witness))
}

fn validate_candidate(doc: &Value) -> Result<()> {
    keys(
        doc,
        &[
            "schema",
            "block_id",
            "status",
            "provenance",
            "frame",
            "members",
            "outline",
            "grade",
            "witness",
            "placer_judgment",
            "deferred",
            "search",
            "block_policy",
            "sources",
        ],
        &[],
    )?;
    ensure!(
        doc["schema"] == "agent-ee.block-candidate/2" && doc["status"] == "packed",
        "only a packed block candidate /2 is usable"
    );
    text(&doc["block_id"])?;
    keys(
        &doc["provenance"],
        &["class", "enforcement", "citation"],
        &["method", "source"],
    )?;
    let class = text(&doc["provenance"]["class"])?;
    ensure!(
        [
            "search-derived",
            "manufacturer-reference-layout",
            "manufacturer-evm",
            "qualified-owner-board"
        ]
        .contains(&class),
        "unknown provenance class"
    );
    ensure!(
        doc["provenance"]["enforcement"]
            == if class == "search-derived" {
                "advisory"
            } else {
                "hard"
            },
        "block provenance class/enforcement mismatch"
    );
    text(&doc["provenance"]["citation"])?;
    if let Some(method) = doc["provenance"].get("method") {
        text(method)?;
    }
    if class == "search-derived" {
        ensure!(
            doc["provenance"].get("source").is_none(),
            "search-derived candidate has no reference source"
        );
    } else {
        keys(&doc["provenance"]["source"], &["locator", "sha256"], &[])?;
        text(&doc["provenance"]["source"]["locator"])?;
        hex(&doc["provenance"]["source"]["sha256"])?;
    }
    keys(&doc["frame"], &["convention", "anchor"], &[])?;
    ensure!(
        doc["frame"]["convention"] == "anchor-origin-quarter-turn/1",
        "invalid block frame"
    );
    let mut member_set = BTreeSet::new();
    let mut framed = Vec::new();
    for row in rows(&doc["members"])? {
        keys(
            row,
            &[
                "reference",
                "fpid",
                "side",
                "value",
                "pose",
                "pad_geometry_sha256",
                "courtyard_sha256",
                "courtyard",
                "pins",
            ],
            &[],
        )?;
        let reference = text(&row["reference"])?;
        ensure!(
            member_set.insert(reference.to_owned()),
            "duplicate block member"
        );
        text(&row["fpid"])?;
        ensure!(
            row["side"] == "F" || row["side"] == "B",
            "invalid member side"
        );
        for k in ["pad_geometry_sha256", "courtyard_sha256"] {
            hex(&row[k])?;
        }
        keys(&row["pose"], &["position_nm", "rotation_deg"], &[])?;
        exact::point(&row["pose"]["position_nm"])?;
        ensure!(
            [0, 90, 180, 270].contains(
                &row["pose"]["rotation_deg"]
                    .as_i64()
                    .context("integer quarter rotation")?
            ),
            "invalid quarter rotation"
        );
        if row["reference"] == doc["frame"]["anchor"] {
            ensure!(
                row["pose"] == json!({"position_nm":[0,0],"rotation_deg":0}),
                "the anchor defines the block frame at the origin, rotation 0"
            );
        }
        value_rule(&row["value"])?;
        ensure!(
            digest(&json!({"side":row["side"],"courtyards":row["courtyard"]}))
                == row["courtyard_sha256"].as_str().unwrap(),
            "courtyard record differs from its hash"
        );
        let courtyards = row["courtyard"].as_object().context("courtyard object")?;
        ensure!(
            courtyards.contains_key(row["side"].as_str().unwrap())
                && courtyards.keys().all(|s| s == "F" || s == "B"),
            "courtyard record needs its mounting side"
        );
        for (_, c) in courtyards {
            geometry::courtyard_bounds(c)?;
            for k in ["polygons", "inner_polygons"] {
                if let Some(p) = c.get(k) {
                    ensure!(*p == rings(p)?, "courtyard identity has noncanonical rings");
                }
            }
        }
        let local = json!({"ref":reference,"side":row["side"],"position_nm":[0,0],"rotation_deg":0,"courtyards":row["courtyard"],"pads":[]});
        framed.push(footprint::transform_value(
            &local,
            &row["pose"]["position_nm"],
            row["pose"]["rotation_deg"].as_i64().unwrap(),
        )?);
        let mut pins = BTreeSet::new();
        for pin in rows(&row["pins"])? {
            let pad = text(&pin["pad"])?;
            ensure!(pins.insert(pad), "duplicate member pin");
            if !pin["role"].is_null() {
                text(&pin["net"])?;
                ensure!(
                    pin["boundary"].is_boolean(),
                    "boundary flag must be boolean"
                );
            }
        }
    }
    ensure!(
        !member_set.is_empty() && member_set.contains(text(&doc["frame"]["anchor"])?),
        "block members need anchor"
    );
    validate_pins(rows(&doc["members"])?)?;
    keys(
        &doc["block_policy"],
        &[
            "schema",
            "step_budget",
            "gap_nm",
            "priority_refs",
            "ground_nets",
            "obstacles",
            "fixed_refs",
        ],
        &[
            "search_timeout_s",
            "groups",
            "electrical_locality",
            "mechanical_reservations",
        ],
    )?;
    policy::validate(&doc["block_policy"], &member_set)?;
    if let Some(bound) = doc["block_policy"].get("mechanical_reservations") {
        ensure!(
            crate::mechanical::for_block(bound, &member_set)? == *bound,
            "block policy mechanical selection must exactly cover block members"
        );
    }
    ensure!(
        doc["block_policy"]["fixed_refs"] == json!([doc["frame"]["anchor"]])
            && rows(&doc["block_policy"]["obstacles"])?.is_empty()
            && !rows(&doc["block_policy"]["priority_refs"])?.contains(&doc["frame"]["anchor"]),
        "block policy fixes exactly the anchor and has no obstacles"
    );
    if let Some(gs) = doc["block_policy"].get("groups") {
        ensure!(
            !rows(gs)?.is_empty()
                && rows(gs)?
                    .iter()
                    .all(|g| g.get("region").is_none() && g["anchor"].get("reference").is_some()),
            "block policy groups must be nonempty and anchored on a member"
        );
    }
    if let Some(d) = doc["block_policy"].get("electrical_locality") {
        ensure!(
            !rows(&d["obligations"])?.is_empty(),
            "block policy obligations must be nonempty and lie wholly inside the block"
        );
        for o in rows(&d["obligations"])? {
            for role in locality::roles(o) {
                for end in ["from", "to"] {
                    ensure!(
                        member_set.contains(text(&o[role][end]["reference"])?),
                        "block policy obligations must lie wholly inside the block"
                    );
                }
            }
        }
    }
    // Recompute the self-contained geometric evidence before binding the source capture.
    let blank = json!({"footprints":framed});
    let (outline, _, witness) = evidence(
        &blank,
        rows(&blank["footprints"])?,
        &json!({"gap_nm":doc["block_policy"]["gap_nm"]}),
    )?;
    ensure!(
        outline == doc["outline"],
        "block outline differs from framed courtyards"
    );
    ensure!(
        witness == doc["witness"],
        "block witness differs from framed courtyard separations"
    );
    ensure!(
        witness["status"] == "pass",
        "block witness must pass with no pair below the gap and no drilled-pad conflict"
    );
    validate_grade(&doc["grade"], &doc["block_policy"])?;
    keys(&doc["placer_judgment"], &["verdict", "basis"], &[])?;
    ensure!(
        doc["placer_judgment"]["verdict"] == "pass",
        "placer judgment must pass"
    );
    text(&doc["placer_judgment"]["basis"])?;
    keys(
        &doc["deferred"],
        &["groups", "obligations", "obstacles", "board_rule_areas"],
        &["mechanical_reservations"],
    )?;
    if let Some(ids) = doc["deferred"].get("mechanical_reservations") {
        refs(ids)?;
    }
    refs(&doc["deferred"]["groups"])?;
    refs(&doc["deferred"]["obligations"])?;
    for k in ["obstacles", "board_rule_areas"] {
        ensure!(
            exact::integer(&doc["deferred"][k])? >= BigInt::zero(),
            "negative deferred count"
        );
    }
    let internal: BTreeSet<_> = doc["block_policy"]["groups"]
        .as_array()
        .into_iter()
        .flatten()
        .chain(
            doc["block_policy"]["electrical_locality"]["obligations"]
                .as_array()
                .into_iter()
                .flatten(),
        )
        .filter_map(|r| r["id"].as_str())
        .collect();
    ensure!(
        !["groups", "obligations"]
            .into_iter()
            .flat_map(|k| doc["deferred"][k].as_array().unwrap())
            .any(|id| internal.contains(id.as_str().unwrap())),
        "an internal obligation cannot also be deferred"
    );
    keys(
        &doc["search"],
        &["strategy", "steps_used", "step_budget"],
        &["backjumps", "compaction", "recovery"],
    )?;
    text(&doc["search"]["strategy"])?;
    if let Some(n) = doc["search"].get("backjumps") {
        ensure!(
            exact::integer(n)? >= BigInt::zero(),
            "negative backjump count"
        );
    }
    ensure!(
        doc["search"]["step_budget"] == doc["block_policy"]["step_budget"]
            && exact::integer(&doc["search"]["steps_used"])? >= BigInt::zero()
            && exact::integer(&doc["search"]["steps_used"])?
                <= exact::integer(&doc["search"]["step_budget"])?,
        "search steps outside policy budget"
    );
    if let Some(c) = doc["search"].get("compaction") {
        validate_compaction(c)?;
    }
    if let Some(r) = doc["search"].get("recovery") {
        let mut movable = member_set.clone();
        movable.remove(text(&doc["frame"]["anchor"])?);
        validate_recovery(r, &doc["block_policy"], &movable)?;
    }
    keys(
        &doc["sources"],
        &[
            "board_sha256",
            "inventory_sha256",
            "allocation_sha256",
            "derivation_sha256",
            "policy_sha256",
        ],
        &[],
    )?;
    for key in [
        "board_sha256",
        "inventory_sha256",
        "allocation_sha256",
        "derivation_sha256",
        "policy_sha256",
    ] {
        if key != "allocation_sha256" || !doc["sources"][key].is_null() {
            hex(&doc["sources"][key])?;
        }
    }
    Ok(())
}
fn hex(value: &Value) -> Result<()> {
    let s = text(value)?;
    ensure!(
        s.len() == 64
            && s.bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)),
        "invalid sha256"
    );
    Ok(())
}

fn validate_pins(members: &[Value]) -> Result<()> {
    let mut nets = BTreeMap::<String, String>::new();
    let mut roles = BTreeMap::<String, (String, bool)>::new();
    for row in members {
        let mut pads = BTreeSet::new();
        for pin in rows(&row["pins"])? {
            let role = pin.get("role").context("pin role required")?;
            keys(
                pin,
                &["pad", "function", "role"],
                if role.is_null() {
                    &[]
                } else {
                    &["boundary", "net"]
                },
            )?;
            let pad = text(&pin["pad"])?;
            ensure!(pads.insert(pad), "pin listed twice");
            if !pin["function"].is_null() {
                keys(&pin["function"], &["name", "electrical_type"], &[])?;
                ensure!(
                    pin["function"]
                        .as_object()
                        .unwrap()
                        .values()
                        .all(Value::is_string),
                    "pin function must be text"
                );
            }
            if role.is_null() {
                continue;
            }
            let role = text(role)?;
            ensure!(
                role.starts_with('n')
                    && role.len() > 1
                    && role[1..].chars().all(|c| c.is_ascii_digit()),
                "pin role must be n1, n2, ... or null"
            );
            let net = text(&pin["net"])?;
            let boundary = pin["boundary"]
                .as_bool()
                .context("pin boundary must be a boolean")?;
            if let Some(old) = roles.insert(role.into(), (net.into(), boundary)) {
                ensure!(
                    old == (net.into(), boundary),
                    "net role must name one net with one boundary flag"
                );
            }
            if let Some(old) = nets.insert(net.into(), role.into()) {
                ensure!(old == role, "net must have one role");
            }
        }
    }
    Ok(())
}
fn validate_grade(grade: &Value, sub: &Value) -> Result<()> {
    keys(
        grade,
        &["electrical_locality", "groups"],
        &["mechanical_reservations"],
    )?;
    ensure!(
        sub.get("mechanical_reservations").is_some()
            == grade.get("mechanical_reservations").is_some(),
        "mechanical grade must match block policy coverage"
    );
    if let Some(r) = grade.get("mechanical_reservations") {
        ensure!(
            r["schema"] == "agent-ee.mechanical-reservation-grade/1"
                && r["status"] == "pass"
                && r["defects"] == json!([])
                && r["unknown"] == json!([]),
            "block mechanical grade must pass with no defects or unknowns"
        );
    }
    for kind in ["electrical_locality", "groups"] {
        let r = &grade[kind];
        if sub.get(kind).is_none() {
            ensure!(
                r.is_null(),
                "block grade must be null without internal {kind}"
            );
            continue;
        }
        ensure!(
            r.is_object()
                && (r["status"] == "pass"
                    || kind == "electrical_locality" && r["status"] == "info")
                && r["defects"] == json!([])
                && r["unknown"] == json!([])
                && r["measured"].is_array(),
            "block grade {kind} must pass with no defects or unknowns"
        );
        if kind == "groups" {
            ensure!(
                groups::violations(r).is_empty(),
                "block grade groups has violations"
            );
        }
    }
    if let Some(d) = sub.get("electrical_locality") {
        let r = &grade["electrical_locality"];
        ensure!(
            r["declaration"] == *d,
            "locality grade must declare the block policy obligations"
        );
        let mut pairs = BTreeMap::new();
        for o in rows(&d["obligations"])? {
            for role in locality::roles(o) {
                pairs.insert((text(&o["id"])?.to_owned(), role.to_owned()), &o[role]);
            }
        }
        let mut seen = BTreeSet::new();
        let mut advisories = Vec::new();
        for row in rows(&r["measured"])? {
            let key = (
                text(&row["obligation"])?.to_owned(),
                text(&row["role"])?.to_owned(),
            );
            let pair = pairs
                .get(&key)
                .context("locality grade measures an unknown pair")?;
            ensure!(seen.insert(key), "locality grade measures a repeated pair");
            let authority = locality::classify(pair, &d["source_records"]);
            ensure!(
                authority["enforcement"] != "integrity",
                "locality pair source record integrity failure"
            );
            ensure!(
                row["constraint_provenance"] == authority,
                "locality pair has stale provenance"
            );
            let distance = &row["distance_squared_nm"];
            let squared = if distance.is_number() {
                BigRational::from_integer(exact::integer(distance)?)
            } else {
                keys(distance, &["numerator", "denominator"], &[])?;
                let n = exact::integer(&distance["numerator"])?;
                let den = exact::integer(&distance["denominator"])?;
                ensure!(
                    n >= BigInt::zero() && den > BigInt::zero(),
                    "invalid locality distance"
                );
                BigRational::new(n, den)
            };
            ensure!(squared >= BigRational::zero(), "negative locality distance");
            let limit = exact::integer(&pair["max_distance_nm"])?;
            let exceeded = squared > BigRational::from_integer(&limit * &limit);
            ensure!(
                authority["enforcement"] != "hard" || !exceeded,
                "locality pair exceeds its limit"
            );
            locality::validate_floor_record(row)?;
            let floor = &row["manufacturing_floor"];
            ensure!(
                !(authority["enforcement"] == "feasibility"
                    && floor["status"] == "unknown"
                    && floor["reason"]
                        .as_str()
                        .is_some_and(|s| s.starts_with("captured JLCPCB numeric record"))),
                "manufacturer floor unavailable"
            );
            let below = floor["status"] == "known" && limit < exact::integer(&floor["floor_nm"])?;
            ensure!(
                !below
                    || (authority["enforcement"] != "hard"
                        && authority["enforcement"] != "feasibility"),
                "locality pair is infeasible"
            );
            if authority["enforcement"] != "hard" {
                let mut a = json!({"kind":"unsourced","severity":"defect"});
                for (k, v) in row.as_object().context("locality measurement object")? {
                    a[k] = v.clone();
                }
                a["exceeded"] = json!(exceeded);
                a["below_floor"] = json!(below);
                advisories.push(a);
            }
        }
        ensure!(
            seen == pairs.into_keys().collect(),
            "locality grade must measure every internal pair"
        );
        ensure!(
            r["advisories"] == json!(advisories)
                && r["status"]
                    == if advisories.is_empty() {
                        "pass"
                    } else {
                        "info"
                    },
            "locality grade must report every advisory and its truthful status"
        );
    }
    if let Some(gs) = sub.get("groups") {
        let r = &grade["groups"];
        ensure!(
            r["groups"] == *gs,
            "group grade must declare the block policy groups"
        );
        let mut want = Vec::new();
        for g in rows(gs)? {
            for member in refs(&g["references"])? {
                want.push((text(&g["id"])?.to_owned(), member));
            }
        }
        let mut got = rows(&r["measured"])?
            .iter()
            .map(|m| Ok((text(&m["group"])?.to_owned(), text(&m["ref"])?.to_owned())))
            .collect::<Result<Vec<_>>>()?;
        want.sort();
        got.sort();
        ensure!(
            want == got,
            "group grade must measure every internal group member"
        );
    }
    Ok(())
}
fn nonnegative(v: &Value) -> Result<BigInt> {
    let n = exact::integer(v)?;
    ensure!(n >= BigInt::zero(), "count must be a nonnegative integer");
    Ok(n)
}
fn validate_compaction(r: &Value) -> Result<()> {
    keys(
        r,
        &[
            "objective",
            "first_box_area_nm2",
            "box_area_nm2",
            "step_cap",
            "trials",
            "steps_total",
        ],
        &[],
    )?;
    ensure!(
        r["objective"] == "outline-bisection-min-box-area/1",
        "invalid compaction objective"
    );
    for k in [
        "first_box_area_nm2",
        "box_area_nm2",
        "step_cap",
        "steps_total",
    ] {
        nonnegative(&r[k])?;
    }
    ensure!(
        exact::integer(&r["box_area_nm2"])? <= exact::integer(&r["first_box_area_nm2"])?,
        "compaction area must be at most the first run's"
    );
    ensure!(
        rows(&r["trials"])?.len() <= 12,
        "compaction trials must be a list of at most 12"
    );
    for row in rows(&r["trials"])? {
        keys(
            row,
            &["family", "parameter", "status", "steps_used"],
            &["box_area_nm2"],
        )?;
        ensure!(
            row["family"] == "anchor-square" || row["family"] == "box-scale",
            "unknown compaction family"
        );
        for k in ["parameter", "steps_used", "box_area_nm2"] {
            if let Some(n) = row.get(k) {
                nonnegative(n)?;
            }
        }
        ensure!(
            exact::integer(&row["steps_used"])? <= exact::integer(&r["step_cap"])?,
            "a compaction trial stays within the step cap"
        );
        text(&row["status"])?;
        ensure!(
            row.get("box_area_nm2").is_some() == (row["status"] == "packed"),
            "a compaction trial records its box area exactly when it packed"
        );
    }
    Ok(())
}
fn validate_recovery(r: &Value, sub: &Value, movable: &BTreeSet<String>) -> Result<()> {
    keys(
        r,
        &[
            "method",
            "status",
            "stranded",
            "references",
            "max_distance_nm",
            "trials",
            "steps_total",
        ],
        &[],
    )?;
    ensure!(
        r["method"] == "anchor-proximity-retry/1" && r["status"] == "recovered",
        "recovery must be a recovered anchor-proximity-retry/1 record"
    );
    let grouped: BTreeSet<_> = sub["groups"]
        .as_array()
        .into_iter()
        .flatten()
        .flat_map(|g| g["references"].as_array().into_iter().flatten())
        .filter_map(Value::as_str)
        .collect();
    ensure!(
        !sub["groups"]
            .as_array()
            .into_iter()
            .flatten()
            .any(|g| g["id"] == "stage-a-anchor-proximity"),
        "the recovery group never reaches the block policy"
    );
    let members = |v: &Value| -> Result<BTreeSet<String>> {
        let rs = refs(v)?;
        ensure!(
            !rs.is_empty()
                && natural(rs.clone()) == rs
                && rs
                    .iter()
                    .all(|r| movable.contains(r) && !grouped.contains(r.as_str())),
            "recovery needs distinct movable block members in natural order outside declared groups"
        );
        Ok(rs.into_iter().collect())
    };
    for k in ["stranded", "references"] {
        members(&r[k])?;
    }
    ensure!(
        exact::integer(&r["max_distance_nm"])? > BigInt::zero(),
        "recovery distance must be positive"
    );
    nonnegative(&r["steps_total"])?;
    let trials = rows(&r["trials"])?;
    ensure!(
        !trials.is_empty() && trials.len() <= 6,
        "recovery trials must be a list of 1 to 6"
    );
    let mut previous: Option<&Value> = None;
    let mut total = BigInt::zero();
    for (i, t) in trials.iter().enumerate() {
        keys(
            t,
            &["references", "max_distance_nm", "status", "steps_used"],
            &[],
        )?;
        let members_now = members(&t["references"])?;
        let distance = exact::integer(&t["max_distance_nm"])?;
        ensure!(
            distance > BigInt::zero(),
            "recovery distance must be positive"
        );
        let steps = nonnegative(&t["steps_used"])?;
        ensure!(
            steps <= exact::integer(&sub["step_budget"])?,
            "a recovery trial stays within the block step budget"
        );
        total += steps;
        ensure!(
            t["status"]
                == if i + 1 == trials.len() {
                    "recovered"
                } else {
                    "synthetic_plane_reached"
                },
            "recovery trial status sequence invalid"
        );
        if let Some(p) = previous {
            let old = members(&p["references"])?;
            if members_now.is_superset(&old) && members_now != old {
                ensure!(
                    t["max_distance_nm"] == p["max_distance_nm"],
                    "a recovery trial that grows the group keeps its distance"
                );
            } else {
                ensure!(
                    t["references"] == p["references"]
                        && distance == exact::integer(&p["max_distance_nm"])? / 2,
                    "a recovery trial that keeps the group halves its distance"
                );
            }
        } else {
            ensure!(
                t["references"] == r["stranded"],
                "the first recovery trial groups exactly the stranded members"
            );
        }
        previous = Some(t);
    }
    let last = trials.last().unwrap();
    ensure!(
        last["references"] == r["references"] && last["max_distance_nm"] == r["max_distance_nm"],
        "the recovery record names its last trial"
    );
    ensure!(
        total == exact::integer(&r["steps_total"])?,
        "recovery steps_total is the sum of its trials"
    );
    Ok(())
}

fn net_roles(
    capture: &Value,
    inventory: &NativeInventory,
    ordered: &[String],
) -> Result<BTreeMap<String, Vec<Value>>> {
    let board = board(capture)?;
    let member_set: BTreeSet<_> = ordered.iter().cloned().collect();
    let outside: BTreeSet<_> = board
        .iter()
        .filter(|(r, _)| !member_set.contains(*r))
        .flat_map(|(_, f)| {
            f["pads"]
                .as_array()
                .unwrap()
                .iter()
                .filter_map(|p| p["net"].as_str())
        })
        .collect();
    let mut nets = BTreeMap::new();
    for r in ordered {
        nets.insert(r.clone(), pad_nets(&board[r])?);
    }
    let mut labels = BTreeMap::<String, (String, bool)>::new();
    for r in ordered {
        for pad in natural(nets[r].keys().cloned().collect()) {
            let net = &nets[r][&pad];
            if !net.is_empty() && !labels.contains_key(net) {
                labels.insert(
                    net.clone(),
                    (
                        format!("n{}", labels.len() + 1),
                        outside.contains(net.as_str()),
                    ),
                );
            }
        }
    }
    let mut result = BTreeMap::new();
    for r in ordered {
        let mut pins = Vec::new();
        for pad in natural(nets[r].keys().cloned().collect()) {
            let net = &nets[r][&pad];
            let mut pin = json!({"pad":pad,"function":inventory.components[r]["pin_metadata"][&pad],"role":null});
            if !net.is_empty() {
                let (role, boundary) = &labels[net];
                pin["role"] = json!(role);
                pin["boundary"] = json!(boundary);
                pin["net"] = json!(net);
            }
            pins.push(pin);
        }
        result.insert(r.clone(), pins);
    }
    Ok(result)
}
type Role = (Option<String>, Option<bool>);
fn pin_role(pin: &Value) -> Role {
    (
        pin["role"].as_str().map(str::to_owned),
        pin.get("boundary").and_then(Value::as_bool),
    )
}
fn role_string(role: &Role) -> String {
    format!(
        "({}, {})",
        role.0.as_ref().map_or("None".into(), |s| format!("'{s}'")),
        match role.1 {
            Some(true) => "True",
            Some(false) => "False",
            None => "None",
        }
    )
}
fn role_value(role: &Role) -> Value {
    json!([role.0, role.1])
}
fn match_candidate(
    doc: &Value,
    capture: &Value,
    inventory: &NativeInventory,
    mapping: &BTreeMap<String, String>,
) -> Result<Vec<Value>> {
    let board = board(capture)?;
    let rows_by: BTreeMap<_, _> = rows(&doc["members"])?
        .iter()
        .map(|m| (m["reference"].as_str().unwrap().to_owned(), m))
        .collect();
    let mut problems = Vec::new();
    for (r, t) in mapping {
        if !board.contains_key(t) || !inventory.components.contains_key(t) {
            problems.push(json!({"reference":r,"target":t,"kind":"target_missing"}));
        }
    }
    if !problems.is_empty() {
        return Ok(problems);
    }
    let anchor = text(&doc["frame"]["anchor"])?;
    let ordered: Vec<_> = std::iter::once(anchor.to_owned())
        .chain(natural(
            rows_by
                .keys()
                .filter(|r| r.as_str() != anchor)
                .cloned()
                .collect(),
        ))
        .collect();
    let found = net_roles(
        capture,
        inventory,
        &ordered
            .iter()
            .map(|r| mapping[r].clone())
            .collect::<Vec<_>>(),
    )?;
    let mut pairs = Vec::<(Role, BTreeSet<Role>)>::new();
    for r in ordered {
        let row = rows_by[&r];
        let target = &mapping[&r];
        let fp = &board[target];
        let component = &inventory.components[target];
        let mut mismatch = |kind: &str, expected: Value, actual: Value| {
            problems.push(json!({"reference":r,"target":target,"kind":kind,"expected":expected,"actual":actual}))
        };
        for key in ["fpid", "side"] {
            if fp[key] != row[key] {
                mismatch(key, row[key].clone(), fp[key].clone());
            }
        }
        let (pads, courtyard) = local_geometry(fp)?;
        for (key, actual) in [
            ("pad_geometry", digest(&pads)),
            ("courtyard", digest(&courtyard)),
        ] {
            if row[format!("{key}_sha256")] != actual {
                mismatch(key, row[format!("{key}_sha256")].clone(), json!(actual));
            }
        }
        let value_ok = if row["value"]["rule"] == "exact" {
            row["value"]["text"] == component["value"]
        } else {
            quantity_matches(
                &row["value"],
                component["value"].as_str().context("native value text")?,
            )?
        };
        if !value_ok {
            mismatch("value", row["value"].clone(), component["value"].clone());
        }
        let have: BTreeMap<_, _> = found[target]
            .iter()
            .map(|p| (p["pad"].as_str().unwrap().to_owned(), p))
            .collect();
        let want: BTreeMap<_, _> = rows(&row["pins"])?
            .iter()
            .map(|p| (p["pad"].as_str().unwrap().to_owned(), p))
            .collect();
        if have.keys().collect::<BTreeSet<_>>() != want.keys().collect() {
            mismatch(
                "pins",
                json!(natural(want.keys().cloned().collect())),
                json!(natural(have.keys().cloned().collect())),
            );
        }
        for pad in natural(
            want.keys()
                .filter(|p| have.contains_key(*p))
                .cloned()
                .collect(),
        ) {
            let a = have[&pad];
            let b = want[&pad];
            if a["function"] != b["function"] {
                let expected =
                    Value::Object([(pad.clone(), b["function"].clone())].into_iter().collect());
                let actual =
                    Value::Object([(pad.clone(), a["function"].clone())].into_iter().collect());
                mismatch("pin_function", expected, actual);
            }
            let expected = pin_role(b);
            let actual = pin_role(a);
            if let Some((_, set)) = pairs.iter_mut().find(|(r, _)| r == &expected) {
                set.insert(actual.clone());
            } else {
                pairs.push((expected.clone(), [actual.clone()].into_iter().collect()));
            }
            if expected.0.is_none() != actual.0.is_none() || expected.1 != actual.1 {
                mismatch(
                    "net_role",
                    Value::Object([(pad.clone(), role_value(&expected))].into_iter().collect()),
                    Value::Object([(pad, role_value(&actual))].into_iter().collect()),
                );
            }
        }
    }
    let mut images = BTreeMap::<Role, BTreeSet<Role>>::new();
    for (expected, actual) in pairs {
        if expected.0.is_none() {
            continue;
        }
        if actual.len() != 1 {
            let mut labels: Vec<_> = actual.iter().map(role_string).collect();
            labels.sort();
            problems.push(json!({"kind":"net_role_split","role":expected.0,"actual":labels}));
        }
        for a in actual {
            images.entry(a).or_default().insert(expected.clone());
        }
    }
    let mut images: Vec<_> = images.into_iter().collect();
    images.sort_by_key(|(actual, _)| role_string(actual));
    for (actual, expected) in images {
        if expected.len() != 1 && actual.0.is_some() {
            let mut roles: Vec<_> = expected.into_iter().filter_map(|r| r.0).collect();
            roles.sort();
            problems.push(json!({"kind":"net_role_merged","roles":roles,"actual":actual.0}));
        }
    }
    Ok(problems)
}
fn boundary(
    doc: &Value,
    capture: &Value,
    mapping: &BTreeMap<String, String>,
    declared: Option<&Value>,
) -> Result<(Vec<Value>, Vec<Value>)> {
    let board = board(capture)?;
    let rows_by: BTreeMap<_, _> = rows(&doc["members"])?
        .iter()
        .map(|m| (m["reference"].as_str().unwrap().to_owned(), m))
        .collect();
    let members: BTreeSet<_> = mapping.values().cloned().collect();
    let mut outside = BTreeMap::<String, BTreeSet<String>>::new();
    for (r, fp) in &board {
        if members.contains(r) {
            continue;
        }
        for p in rows(&fp["pads"])? {
            if let Some(n) = p["net"].as_str().filter(|s| !s.is_empty()) {
                outside.entry(n.into()).or_default().insert(r.clone());
            }
        }
    }
    let mut roles = BTreeMap::<String, (String, bool, String)>::new();
    let mut problems = Vec::new();
    for r in natural(rows_by.keys().cloned().collect()) {
        let nets = pad_nets(&board[&mapping[&r]])?;
        for p in rows(&rows_by[&r]["pins"])? {
            let Some(role) = p["role"].as_str() else {
                continue;
            };
            let net = nets.get(text(&p["pad"])?).cloned().unwrap_or_default();
            let record = roles.entry(role.into()).or_insert_with(|| {
                (
                    net.clone(),
                    p["boundary"].as_bool().unwrap(),
                    p["net"].as_str().unwrap().to_owned(),
                )
            });
            if record.0 != net {
                let values: BTreeSet<_> = [record.0.clone(), net].into_iter().collect();
                problems.push(json!({"kind":"net_role_split","role":role,"nets":values}));
            }
        }
    }
    let empty = json!({});
    let declared = declared.filter(|v| !v.is_null()).unwrap_or(&empty);
    ensure!(
        declared
            .as_object()
            .is_some_and(|o| o.values().all(Value::is_string)),
        "boundary_nets must map roles to net names"
    );
    let mut unknown: Vec<_> = declared
        .as_object()
        .unwrap()
        .keys()
        .filter(|r| !roles.get(*r).is_some_and(|v| v.1))
        .cloned()
        .collect();
    unknown.sort();
    if !unknown.is_empty() {
        problems.push(json!({"kind":"boundary_unknown_role","roles":unknown}));
    }
    let mut roles: Vec<_> = roles.into_iter().collect();
    roles.sort_by(|(a, _), (b, _)| {
        a[1..]
            .parse::<BigInt>()
            .unwrap()
            .cmp(&b[1..].parse::<BigInt>().unwrap())
    });
    let identity = mapping.iter().all(|(a, b)| a == b);
    let mut bindings = Vec::new();
    for (role, (net, boundary, recorded)) in roles {
        let reached = natural(outside.get(&net).into_iter().flatten().cloned().collect());
        if boundary != !reached.is_empty() {
            problems.push(json!({"kind":"boundary_flag","role":role,"net":net,"outside":reached}));
        }
        if boundary {
            let expected = declared
                .get(&role)
                .and_then(Value::as_str)
                .or_else(|| identity.then_some(recorded.as_str()));
            match expected{None=>problems.push(json!({"kind":"boundary_undeclared","role":role,"net":net})),Some(expected)if expected!=net=>problems.push(json!({"kind":"boundary_net_mismatch","role":role,"expected":expected,"actual":net})),_=>{}}
        }
        bindings.push(json!({"role":role,"net":net,"boundary":boundary,"outside":reached}));
    }
    Ok((bindings, problems))
}
fn extent(fp: &Value) -> Result<BigInt> {
    let origin = exact::point(&fp["position_nm"])?;
    let mut polygons = Vec::new();
    for c in fp["courtyards"]
        .as_object()
        .context("courtyard inventory")?
        .values()
    {
        if let Ok((outer, _)) = geometry::courtyard_bounds(c) {
            polygons.extend(outer);
        }
    }
    if let Some(g) = fp.get("packing_pad_geometry").filter(|v| v.is_object()) {
        for kind in ["copper", "holes"] {
            for e in g[kind].as_array().into_iter().flatten() {
                for p in e["polygons"].as_array().into_iter().flatten() {
                    polygons.push(rows(p)?.iter().map(exact::point).collect::<Result<_>>()?);
                }
            }
        }
    }
    Ok(polygons
        .iter()
        .flatten()
        .map(|p| (&p.x - &origin.x).abs().max((&p.y - &origin.y).abs()))
        .max()
        .unwrap_or_default())
}
fn admission_evidence(
    doc: &Value,
    capture: &Value,
    board: &BTreeMap<String, &Value>,
    block: &Value,
    anchor: &str,
    sub: &Value,
    framed: &[Value],
    own: bool,
) -> Result<Vec<Value>> {
    let (outline, grade, witness) = evidence(capture, framed, sub)?;
    let mut problems = Vec::new();
    if own {
        for (name, value) in [
            ("outline", &outline),
            ("grade", &grade),
            ("witness", &witness),
        ] {
            if *value != doc[name] {
                problems.push(json!({"kind":"stale_evidence","evidence":name}));
            }
        }
        if let Some(r) = doc["search"].get("recovery") {
            let members = refs(&block["references"])?;
            let gap = exact::integer(&sub["gap_nm"])?;
            let mut reach = extent(&board[anchor])?;
            for member in &members {
                if member != anchor {
                    reach += 2 * extent(&board[member])? + &gap;
                }
            }
            reach += gap + 1;
            let start = reach / 2;
            let grouped: BTreeSet<_> = sub["groups"]
                .as_array()
                .into_iter()
                .flatten()
                .flat_map(|g| g["references"].as_array().into_iter().flatten())
                .filter_map(Value::as_str)
                .collect();
            let mut ineligible = Vec::new();
            for member in refs(&r["references"])? {
                if grouped.contains(member.as_str())
                    || pad_nets(&board[&member])?
                        .into_values()
                        .filter(|n| !n.is_empty())
                        .collect::<BTreeSet<_>>()
                        .len()
                        < 2
                {
                    ineligible.push(member);
                }
            }
            if exact::integer(&r["trials"][0]["max_distance_nm"])? != start
                || !ineligible.is_empty()
            {
                problems.push(json!({"kind":"recovery_mismatch","start_distance_nm":exact::integer_value(&start),"actual":r["trials"][0]["max_distance_nm"],"ineligible":ineligible}));
            }
        }
        if let Some(c) = doc["search"].get("compaction") {
            let mut area = BigInt::zero();
            for side in outline.as_object().unwrap().values() {
                let b = rows(&side["box_nm"])?;
                area += (exact::integer(&b[2])? - exact::integer(&b[0])?)
                    * (exact::integer(&b[3])? - exact::integer(&b[1])?);
            }
            if exact::integer(&c["box_area_nm2"])? != area {
                problems.push(json!({"kind":"stale_evidence","evidence":"compaction"}));
            }
        }
    }
    let mut failed = Vec::new();
    for kind in ["electrical_locality", "groups", "mechanical_reservations"] {
        let g = &grade[kind];
        if g.is_null() {
            continue;
        }
        let usable = if kind == "groups" {
            g["status"] == "pass" && groups::violations(g).is_empty()
        } else {
            g["status"] == "pass" || kind == "electrical_locality" && g["status"] == "info"
        };
        if !usable {
            failed.push(kind);
        }
    }
    if witness["status"] != "pass" {
        failed.push("witness");
    }
    if !failed.is_empty() {
        problems.push(json!({"kind":"evidence_failed","evidence":failed}));
    }
    Ok(problems)
}
pub fn admit(
    entry: &Value,
    capture: &Value,
    inventory: &NativeInventory,
    derivation: &Value,
    policy: &Value,
) -> Result<Value> {
    keys(entry, &["candidate"], &["mapping", "boundary_nets"])?;
    let doc = &entry["candidate"];
    validate_candidate(doc)?;
    ensure!(
        derivation["board_sha256"] == capture["board_sha256"],
        "derivation belongs to another board capture"
    );
    let declared_mapping = entry.get("mapping").filter(|v| !v.is_null());
    let mapped = declared_mapping.is_some();
    let board = board(capture)?;
    let rows_by: BTreeMap<_, _> = rows(&doc["members"])?
        .iter()
        .map(|m| (m["reference"].as_str().unwrap().to_owned(), m))
        .collect();
    let mapping: BTreeMap<String, String> = if let Some(m) = declared_mapping {
        m.as_object()
            .context("mapping must pair every candidate member with a distinct target reference")?
            .iter()
            .map(|(r, t)| Ok((r.clone(), text(t)?.to_owned())))
            .collect::<Result<_>>()?
    } else {
        rows_by.keys().map(|r| (r.clone(), r.clone())).collect()
    };
    ensure!(
        mapping.keys().collect::<BTreeSet<_>>() == rows_by.keys().collect()
            && mapping.values().collect::<BTreeSet<_>>().len() == mapping.len(),
        "mapping must pair every candidate member with a distinct target reference"
    );
    let refused = |problems: Vec<Value>| json!({"status":"refused","problems":problems});
    let mut problems = Vec::new();
    let block;
    if !mapped {
        let expected = json!({"board_sha256":capture["board_sha256"],"inventory_sha256":derivation["inventory_sha256"],"allocation_sha256":derivation["allocation_sha256"],"derivation_sha256":digest(derivation),"policy_sha256":digest(policy)});
        for key in [
            "board_sha256",
            "inventory_sha256",
            "allocation_sha256",
            "derivation_sha256",
            "policy_sha256",
        ] {
            if doc["sources"][key] != expected[key] {
                problems.push(json!({"kind":"source_mismatch","source":key,"expected":expected[key],"actual":doc["sources"][key]}));
            }
        }
        let found: Vec<_> = rows(&derivation["blocks"])?
            .iter()
            .filter(|b| b["id"] == doc["block_id"])
            .collect();
        if problems.is_empty() && found.len() != 1 {
            problems.push(json!({"kind":"block_unknown","block":doc["block_id"]}));
        }
        if !problems.is_empty() {
            return Ok(refused(problems));
        }
        block = found[0];
        if refs(&block["references"])?
            .into_iter()
            .collect::<BTreeSet<_>>()
            != rows_by.keys().cloned().collect()
        {
            return Ok(refused(vec![
                json!({"kind":"block_members","expected":natural(refs(&block["references"])?),"actual":natural(rows_by.keys().cloned().collect())}),
            ]));
        }
    } else {
        let mismatches = match_candidate(doc, capture, inventory, &mapping)?;
        if !mismatches.is_empty() {
            return Ok(refused(vec![
                json!({"kind":"identity","mismatches":mismatches}),
            ]));
        }
        let targets: BTreeSet<_> = mapping.values().cloned().collect();
        let found: Vec<_> = rows(&derivation["blocks"])?
            .iter()
            .filter(|b| {
                refs(&b["references"])
                    .is_ok_and(|rs| rs.into_iter().collect::<BTreeSet<_>>() == targets)
            })
            .collect();
        if found.len() != 1 {
            return Ok(refused(vec![
                json!({"kind":"block_members","actual":natural(mapping.values().cloned().collect())}),
            ]));
        }
        block = found[0];
    }
    let (anchor, sub, deferred) = isolate(capture, policy, block)?;
    let expected_anchor = &mapping[text(&doc["frame"]["anchor"])?];
    if expected_anchor != &anchor {
        problems.push(json!({"kind":"frame_anchor","expected":anchor,"actual":expected_anchor}));
    }
    if !mapped {
        if sub != doc["block_policy"] {
            problems.push(json!({"kind":"block_policy_mismatch"}));
        }
        if deferred != doc["deferred"] {
            problems.push(
                json!({"kind":"deferred_mismatch","expected":deferred,"actual":doc["deferred"]}),
            );
        }
        let mismatches = match_candidate(doc, capture, inventory, &mapping)?;
        if !mismatches.is_empty() {
            problems.push(json!({"kind":"identity","mismatches":mismatches}));
        }
    }
    if !problems.is_empty() {
        return Ok(refused(problems));
    }
    let mut ordered: Vec<_> = rows_by.keys().cloned().collect();
    ordered.sort_by(|a, b| natural_cmp(&mapping[a], &mapping[b]));
    let framed = ordered
        .iter()
        .map(|r| frame_member(&board[&mapping[r]], &rows_by[r]["pose"]))
        .collect::<Result<Vec<_>>>()?;
    problems = admission_evidence(doc, capture, &board, block, &anchor, &sub, &framed, !mapped)?;
    if !problems.is_empty() {
        return Ok(refused(problems));
    }
    let (binding, problems) = boundary(doc, capture, &mapping, entry.get("boundary_nets"))?;
    if !problems.is_empty() {
        return Ok(refused(problems));
    }
    let poses: Map<_, _> = rows(&doc["members"])?
        .iter()
        .map(|r| {
            (
                mapping[r["reference"].as_str().unwrap()].clone(),
                r["pose"].clone(),
            )
        })
        .collect();
    Ok(
        json!({"status":"admitted","block":block["id"],"anchor":expected_anchor,"poses":poses,"mapping":mapping,"mapped":entry.get("mapping").is_some(),"binding":binding,"candidate_sha256":digest(doc),"provenance":doc["provenance"],"deferred":deferred}),
    )
}

fn quantity(s: &str) -> Option<(BigRational, Option<String>)> {
    let s = s.trim();
    let end = s.find(|c: char| !c.is_ascii_digit()).unwrap_or(s.len());
    if end == 0 {
        return None;
    }
    let whole = &s[..end];
    let mut fraction = String::new();
    let mut suffix = &s[end..];
    let mut prefix = "";
    const PREFIXES: [&str; 14] = [
        "meg", "Meg", "MEG", "p", "n", "u", "µ", "μ", "m", "k", "K", "M", "G", "R",
    ];
    let mut rkm = false;
    if let Some(p) = PREFIXES.iter().find(|p| suffix.starts_with(**p)) {
        let after = &suffix[p.len()..];
        let count = after
            .find(|c: char| !c.is_ascii_digit())
            .unwrap_or(after.len());
        if count > 0 {
            rkm = true;
            prefix = p;
            fraction = after[..count].into();
            suffix = after[count..].trim_start();
        }
    }
    if !rkm {
        if suffix.starts_with('.') {
            suffix = &suffix[1..];
            let n = suffix
                .find(|c: char| !c.is_ascii_digit())
                .unwrap_or(suffix.len());
            if n == 0 {
                return None;
            }
            fraction = suffix[..n].into();
            suffix = &suffix[n..];
        }
        suffix = suffix.trim_start();
        if let Some(p) = PREFIXES[..13].iter().find(|p| suffix.starts_with(**p)) {
            prefix = p;
            suffix = &suffix[p.len()..];
        }
        suffix = suffix.trim_start();
    }
    let unit = match suffix {
        "" => None,
        "Ω" => Some("ohm"),
        "F" => Some("F"),
        "H" => Some("H"),
        "ohm" | "ohms" | "Ohm" | "R" if !rkm => Some("ohm"),
        _ => return None,
    };
    let exponent = match prefix {
        "p" => -12,
        "n" => -9,
        "u" | "µ" | "μ" => -6,
        "m" => -3,
        "k" | "K" => 3,
        "M" | "meg" | "Meg" | "MEG" => 6,
        "G" => 9,
        "R" | "" => 0,
        _ => return None,
    };
    let n: BigInt = format!("{whole}{fraction}").parse().ok()?;
    let mut n = BigRational::new(n, BigInt::from(10u8).pow(fraction.len() as u32));
    if exponent < 0 {
        n /= BigInt::from(10u8).pow((-exponent) as u32)
    } else {
        n *= BigInt::from(10u8).pow(exponent as u32)
    }
    Some((n, unit.map(str::to_owned)))
}
fn value_rule(rule: &Value) -> Result<()> {
    ensure!(rule["text"].is_string(), "member value text required");
    if rule["rule"] == "exact" {
        keys(rule, &["rule", "text"], &[])?;
        return Ok(());
    }
    ensure!(
        rule["rule"] == "tolerance",
        "member value needs rule exact or tolerance"
    );
    keys(
        rule,
        &["rule", "text", "relative_tolerance", "citation"],
        &[],
    )?;
    text(&rule["citation"])?;
    ensure!(
        quantity(rule["text"].as_str().unwrap()).is_some(),
        "tolerance value needs a written quantity"
    );
    let tolerance = rows(&rule["relative_tolerance"])?;
    ensure!(
        tolerance.len() == 2,
        "relative tolerance needs numerator and denominator"
    );
    ensure!(
        exact::integer(&tolerance[0])? >= BigInt::zero()
            && exact::integer(&tolerance[1])? > BigInt::zero(),
        "invalid tolerance"
    );
    Ok(())
}
fn quantity_matches(rule: &Value, actual: &str) -> Result<bool> {
    value_rule(rule)?;
    let tolerance = rows(&rule["relative_tolerance"])?;
    let n = exact::integer(&tolerance[0])?;
    let d = exact::integer(&tolerance[1])?;
    let nominal = quantity(rule["text"].as_str().unwrap()).unwrap();
    Ok(quantity(actual).is_some_and(|a| {
        a.1 == nominal.1 && (a.0 - &nominal.0).abs() <= BigRational::new(n, d) * nominal.0.abs()
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn witness_preserves_pair_order_minimum_ties_and_request_local_drill_geometry() {
        let member = |reference: &str, x: i64| {
            let polygon = json!([[x, 0], [x + 2, 0], [x + 2, 2], [x, 2]]);
            let courtyard = json!({"status":"proven","polygons":[polygon]});
            json!({"ref":reference,"side":"F","position_nm":[x,0],"rotation_deg":0,
                "courtyards":{"F":courtyard,"B":courtyard},"pads":[],
                "packing_pad_geometry":{"status":"proven","holes":[],"copper":[]}})
        };
        let mut members = vec![member("A", 0), member("B", 3), member("C", 6)];
        members[0]["packing_pad_geometry"]["holes"] =
            json!([{"polygons":[[[3,0],[5,0],[5,2],[3,2]]]}]);
        let (_, _, witness) = evidence(&json!({}), &members, &json!({"gap_nm":2})).unwrap();
        assert_eq!(witness["pairs_checked"], 6);
        assert_eq!(witness["minimum"]["B"]["refs"], json!(["A", "B"]));
        assert_eq!(witness["minimum"]["F"]["refs"], json!(["A", "B"]));
        assert_eq!(witness["drilled_pad_conflicts"], json!([["A", "B"]]));
        assert_eq!(
            witness["below_gap"],
            json!([
                {"refs":["A","B"],"side":"B","distance_squared_nm2":[1,1]},
                {"refs":["A","B"],"side":"F","distance_squared_nm2":[1,1]},
                {"refs":["B","C"],"side":"B","distance_squared_nm2":[1,1]},
                {"refs":["B","C"],"side":"F","distance_squared_nm2":[1,1]}
            ])
        );
        members[0]["packing_pad_geometry"]["holes"] = json!([]);
        let (_, _, next) = evidence(&json!({}), &members, &json!({"gap_nm":2})).unwrap();
        assert_eq!(next["drilled_pad_conflicts"], json!([]));
        assert_eq!(next["below_gap"], witness["below_gap"]);
    }
    #[test]
    fn canonical_bytes_preserve_python_float_and_integer_identity() {
        let value:Value=serde_json::from_str(r#"{"z":[-0.0,1e-6,1e-5,1e-4,1e15,1e16,1.25e20,5e-324],"µ":999999999999999999999999999999999999,"a":"é"}"#).unwrap();
        assert_eq!(
            String::from_utf8(canonical(&value)).unwrap(),
            "{\"a\":\"é\",\"z\":[-0.0,1e-06,1e-05,0.0001,1000000000000000.0,1e+16,1.25e+20,5e-324],\"µ\":999999999999999999999999999999999999}"
        );
    }
    #[test]
    fn engineering_quantities_keep_units_prefix_case_and_exact_tolerance_edges() {
        assert_eq!(quantity("4k7"), quantity("4700"));
        assert_eq!(quantity("4R7Ω"), quantity("4.7ohm"));
        assert_eq!(quantity("22µF"), quantity("22u0F"));
        assert_eq!(quantity("1Meg"), quantity("1M"));
        assert_ne!(quantity("1m"), quantity("1M"));
        for bad in ["+4.7uF", "-1uF", "22uF/25V", ".5F", "1e-6F", "4k7ohm"] {
            assert_eq!(quantity(bad), None, "{bad}");
        }
        let r = json!({"rule":"tolerance","text":"22uF","relative_tolerance":[1,10],"citation":"authored test tolerance"});
        assert!(quantity_matches(&r, "24.2uF").unwrap());
        assert!(quantity_matches(&r, "19.8uF").unwrap());
        assert!(!quantity_matches(&r, "24.2001uF").unwrap());
        assert!(!quantity_matches(&r, "22uH").unwrap());
    }
    #[test]
    fn recovery_history_preserves_growth_then_halving_and_total_work() {
        let sub = json!({"step_budget":20});
        let members = ["C1".into(), "C2".into()].into_iter().collect();
        let r = json!({"method":"anchor-proximity-retry/1","status":"recovered","stranded":["C1"],"references":["C1","C2"],"max_distance_nm":50,"steps_total":6,"trials":[{"references":["C1"],"max_distance_nm":100,"status":"synthetic_plane_reached","steps_used":1},{"references":["C1","C2"],"max_distance_nm":100,"status":"synthetic_plane_reached","steps_used":2},{"references":["C1","C2"],"max_distance_nm":50,"status":"recovered","steps_used":3}]});
        validate_recovery(&r, &sub, &members).unwrap();
        let mut lost = r.clone();
        lost["trials"][1]["references"] = json!(["C2"]);
        assert!(validate_recovery(&lost, &sub, &members).is_err());
        let mut total = r.clone();
        total["steps_total"] = json!(5);
        assert!(validate_recovery(&total, &sub, &members).is_err());
        let mut distance = r;
        distance["trials"][1]["max_distance_nm"] = json!(90);
        assert!(validate_recovery(&distance, &sub, &members).is_err());
    }
}
