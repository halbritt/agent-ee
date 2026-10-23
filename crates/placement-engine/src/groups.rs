//! Declared group geometry and exact anchor constraints.
use crate::exact::{self, Int, Point, Rat};
use crate::{
    geometry::{self, Polygon},
    policy,
};
use anyhow::{Context, Result, ensure};
use num_traits::{Signed, ToPrimitive, Zero};
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};

pub fn constraint_source(group: &Value, name: &str) -> Result<Value> {
    let n = if name == "max_distance_nm" {
        Some(exact::integer(&group[name])?)
    } else {
        None
    };
    policy::classify(
        &group["constraint_provenance"],
        name,
        &format!("group {} {name}", group["id"].as_str().unwrap_or("")),
        n.as_ref(),
    )
}
pub fn validate(groups: &Value, refs: Option<&BTreeSet<String>>) -> Result<Value> {
    let a = groups
        .as_array()
        .filter(|a| !a.is_empty())
        .context("placement groups must be a nonempty list")?;
    let (mut out, mut ids, mut members) = (Vec::new(), BTreeSet::new(), BTreeSet::new());
    for g in a {
        ensure!(
            policy::keys(
                g,
                &["id", "references"],
                &[
                    "region",
                    "anchor",
                    "max_distance_nm",
                    "constraint_provenance"
                ]
            ),
            "placement group needs id and references, optionally ['anchor', 'constraint_provenance', 'max_distance_nm', 'region']"
        );
        let id = g["id"]
            .as_str()
            .filter(|s| !s.is_empty())
            .context("group id must be nonempty text")?;
        ensure!(ids.insert(id), "duplicate placement group {id}");
        let rs = policy::strings(&g["references"], &format!("group {id}: references"))?;
        ensure!(
            !rs.is_empty(),
            "group {id}: references must be unique nonempty text"
        );
        ensure!(
            rs.is_disjoint(&members),
            "group {id}: a reference belongs to at most one group"
        );
        members.extend(rs.clone());
        if let Some(refs) = refs {
            ensure!(
                rs.is_subset(refs),
                "group {id}: unknown reference {:?}",
                rs.difference(refs).collect::<Vec<_>>()
            );
        }
        ensure!(
            g.get("region").is_some() || g.get("anchor").is_some(),
            "group {id}: needs a region or an anchor; membership alone constrains nothing"
        );
        let mut n = json!({"id":id,"references":rs});
        if let Some(region) = g.get("region") {
            ensure!(
                policy::keys(region, &["polygons"], &[]),
                "group {id}: region must be exactly {{\"polygons\": [...]}}"
            );
            let p = geometry::qualified_polygons(
                &json!({"status":"proven","polygons":region["polygons"]}),
            )
            .map_err(|e| anyhow::anyhow!("group {id}: region {e}"))?;
            n["region"] = json!({"polygons":polygon_values(&p)});
        }
        ensure!(
            g.get("anchor").is_some() == g.get("max_distance_nm").is_some(),
            "group {id}: anchor and max_distance_nm are declared together"
        );
        if let Some(anchor) = g.get("anchor") {
            ensure!(
                anchor
                    .as_object()
                    .is_some_and(|o| o.len() == 1
                        && (o.contains_key("reference") || o.contains_key("point_nm"))),
                "group {id}: anchor is exactly one of reference or point_nm"
            );
            if let Some(r) = anchor.get("reference") {
                let r = r.as_str().filter(|s| !s.is_empty()).with_context(|| {
                    format!("group {id} anchor reference must be nonempty text")
                })?;
                ensure!(
                    !rs.contains(r) && refs.is_none_or(|refs| refs.contains(r)),
                    "group {id}: anchor {r} must be a known non-member reference"
                );
            } else {
                exact::point(&anchor["point_nm"]).map_err(|_| {
                    anyhow::anyhow!("group {id}: anchor point_nm must be integer nanometres")
                })?;
            }
            ensure!(
                exact::integer(&g["max_distance_nm"]).is_ok_and(|n| n > Int::zero()),
                "group {id}: max_distance_nm must be a positive integer"
            );
            n["anchor"] = anchor.clone();
            n["max_distance_nm"] = g["max_distance_nm"].clone();
        }
        if let Some(p) = g.get("constraint_provenance") {
            ensure!(
                p.as_object().is_some_and(|o| o
                    .keys()
                    .all(|k| ["region", "max_distance_nm"].contains(&k.as_str())
                        && g.get(k).is_some())),
                "group {id}: constraint_provenance must name region or max_distance_nm constraints"
            );
            let mut m = serde_json::Map::new();
            for k in p.as_object().unwrap().keys() {
                m.insert(k.clone(), constraint_source(g, k)?);
            }
            n["constraint_provenance"] = Value::Object(m);
        }
        out.push(n);
    }
    let anchored: BTreeSet<&str> = out
        .iter()
        .filter(|g| g.get("anchor").is_some())
        .flat_map(|g| {
            g["references"]
                .as_array()
                .unwrap()
                .iter()
                .map(|r| r.as_str().unwrap())
        })
        .collect();
    for g in &out {
        if let Some(r) = g["anchor"]["reference"].as_str() {
            ensure!(
                !anchored.contains(r),
                "group {}: anchor {r} is itself an anchored member",
                g["id"].as_str().unwrap()
            );
        }
    }
    Ok(json!(out))
}
pub fn polygon_values(ps: &[Polygon]) -> Value {
    json!(
        ps.iter()
            .map(|p| p.iter().map(exact::point_value).collect::<Vec<_>>())
            .collect::<Vec<_>>()
    )
}
pub fn membership(groups: &Value) -> BTreeMap<String, Value> {
    let mut m = BTreeMap::new();
    for g in groups.as_array().into_iter().flatten() {
        for r in g["references"].as_array().into_iter().flatten() {
            if let Some(r) = r.as_str() {
                m.insert(r.to_owned(), g.clone());
            }
        }
    }
    m
}
pub fn outside_region(polygons: &[Polygon], region: &[Polygon]) -> Rat {
    polygons
        .iter()
        .map(|c| {
            Rat::new(geometry::area2(c).abs(), 2.into())
                - region
                    .iter()
                    .map(|r| geometry::intersection_area(c, r))
                    .sum::<Rat>()
        })
        .sum()
}
pub fn region(group: &Value) -> Result<Vec<Polygon>> {
    geometry::qualified_polygons(&json!({"status":"proven","polygons":group["region"]["polygons"]}))
}
pub fn anchor_point(group: &Value, positions: &BTreeMap<String, Point>) -> Result<Option<Point>> {
    if let Some(p) = group["anchor"].get("point_nm") {
        Ok(Some(exact::point(p)?))
    } else {
        Ok(positions
            .get(
                group["anchor"]["reference"]
                    .as_str()
                    .context("group anchor reference missing")?,
            )
            .cloned())
    }
}
pub fn admits(fp: &Value, group: &Value, positions: &BTreeMap<String, Point>) -> Result<bool> {
    if group.get("region").is_some() {
        let rs = region(group)?;
        for c in fp["courtyards"]
            .as_object()
            .context("courtyards required")?
            .values()
        {
            if !outside_region(&geometry::courtyard_bounds(c)?.0, &rs).is_zero() {
                return Ok(false);
            }
        }
    }
    if group.get("anchor").is_some() {
        let p = anchor_point(group, positions)?.with_context(|| {
            format!(
                "group {}: anchor {} is not placed yet",
                group["id"].as_str().unwrap_or(""),
                group["anchor"]["reference"].as_str().unwrap_or("")
            )
        })?;
        let d = exact::integer(&group["max_distance_nm"])?;
        if exact::squared_distance(&exact::point(&fp["position_nm"])?, &p) > &d * &d {
            return Ok(false);
        }
    }
    Ok(true)
}
pub fn violations(record: &Value) -> Vec<Value> {
    let mut rows = Vec::new();
    for k in ["defects", "unknown"] {
        rows.extend(record[k].as_array().into_iter().flatten().cloned());
    }
    rows.extend(
        record["advisories"]
            .as_array()
            .into_iter()
            .flatten()
            .filter(|r| r["exceeded"] != false)
            .cloned(),
    );
    rows
}
pub fn satisfied(record: &Value) -> bool {
    (record["status"] == "pass" || record["status"] == "info") && violations(record).is_empty()
}
fn unique<'a>(
    by: &BTreeMap<String, Vec<&'a Value>>,
    r: &str,
    id: &Value,
    kind: &str,
    unknown: &mut Vec<Value>,
) -> Option<&'a Value> {
    let found = by.get(r);
    if found.is_none_or(|f| f.len() != 1) {
        unknown.push(json!({"kind":kind,"group":id,"ref":r,"reason":if found.is_none(){"reference missing"}else{"reference duplicated"}}));
        None
    } else {
        Some(found.unwrap()[0])
    }
}
fn finding(
    mut row: Value,
    g: &Value,
    name: &str,
    exceeded: bool,
    defects: &mut Vec<Value>,
    advisories: &mut Vec<Value>,
) -> Result<()> {
    let a = constraint_source(g, name)?;
    row["constraint_provenance"] = a.clone();
    if a["enforcement"] != "hard" {
        row["advisory_kind"] = row["kind"].clone();
        row["kind"] = json!("unsourced_limit");
        row["exceeded"] = json!(exceeded);
        row["constraint"] = json!(format!("group {} {name}", g["id"].as_str().unwrap_or("")));
        row["reason"] = a["soft_reason"].clone();
        advisories.push(row);
    } else if exceeded {
        defects.push(row);
    }
    Ok(())
}
pub fn grade(capture: &Value, groups: &Value) -> Result<Value> {
    let mut by: BTreeMap<String, Vec<&Value>> = BTreeMap::new();
    for fp in capture["footprints"]
        .as_array()
        .context("footprints required")?
    {
        by.entry(fp["ref"].as_str().context("reference required")?.to_owned())
            .or_default()
            .push(fp);
    }
    let (mut defects, mut unknown, mut measured, mut advisories) =
        (Vec::new(), Vec::new(), Vec::new(), Vec::new());
    for g in groups.as_array().context("groups required")? {
        let id = &g["id"];
        let anchor = if g.get("anchor").is_none() {
            None
        } else if let Some(p) = g["anchor"].get("point_nm") {
            Some(exact::point(p)?)
        } else {
            unique(
                &by,
                g["anchor"]["reference"]
                    .as_str()
                    .context("anchor reference required")?,
                id,
                "anchor_missing",
                &mut unknown,
            )
            .map(|fp| exact::point(&fp["position_nm"]))
            .transpose()?
        };
        for rv in g["references"]
            .as_array()
            .context("group references required")?
        {
            let r = rv.as_str().context("reference required")?;
            let Some(fp) = unique(&by, r, id, "member_missing", &mut unknown) else {
                continue;
            };
            if g.get("region").is_some() {
                let region = region(g)?;
                let cs = fp["courtyards"].as_object();
                if cs.is_none_or(|cs| !cs.contains_key(fp["side"].as_str().unwrap_or(""))) {
                    unknown.push(json!({"kind":"courtyard","group":id,"ref":r,"reason":"courtyard missing on mounting side"}));
                }
                let mut entries: Vec<_> = cs.into_iter().flat_map(|m| m.iter()).collect();
                entries.sort_by_key(|(s, _)| *s);
                for (side, c) in entries {
                    let (outer, inner) = match geometry::courtyard_bounds(c) {
                        Ok(v) => v,
                        Err(e) => {
                            unknown.push(json!({"kind":"courtyard","group":id,"ref":r,"side":side,"reason":e.to_string()}));
                            continue;
                        }
                    };
                    let outside = outside_region(&inner, &region);
                    if outside > Rat::zero() {
                        finding(
                            json!({"kind":"outside_group_region","group":id,"ref":r,"side":side,"outside_area_mm2":outside.to_f64().context("area outside float range")?/1e12}),
                            g,
                            "region",
                            true,
                            &mut defects,
                            &mut advisories,
                        )?;
                    } else if outer != inner && outside_region(&outer, &region) > Rat::zero() {
                        unknown.push(json!({"kind":"courtyard_approximation","group":id,"ref":r,"side":side,"reason":"region boundary lies between the declared curve bounds"}));
                    } else {
                        finding(
                            json!({"kind":"outside_group_region","group":id,"ref":r,"side":side,"outside_area_mm2":0.0}),
                            g,
                            "region",
                            false,
                            &mut defects,
                            &mut advisories,
                        )?;
                    }
                }
            }
            if let Some(anchor) = &anchor {
                let d2 = exact::squared_distance(&exact::point(&fp["position_nm"])?, anchor);
                let limit = exact::integer(&g["max_distance_nm"])?;
                let distance = d2.to_f64().context("distance outside float range")?.sqrt() / 1e6;
                let row = json!({"group":id,"ref":r,"distance_mm":crate::grading::round_decimal(distance,6),"max_distance_mm":limit.to_f64().context("distance outside float range")?/1e6,"constraint_provenance":constraint_source(g,"max_distance_nm")?});
                measured.push(row.clone());
                let mut finding_row = row;
                finding_row["kind"] = json!("group_anchor_distance");
                finding(
                    finding_row,
                    g,
                    "max_distance_nm",
                    d2 > &limit * &limit,
                    &mut defects,
                    &mut advisories,
                )?;
            }
        }
    }
    let status = if !defects.is_empty() {
        "defect"
    } else if !unknown.is_empty() {
        "unknown"
    } else if advisories.iter().any(|r| r["exceeded"] == true) {
        "info"
    } else {
        "pass"
    };
    Ok(
        json!({"criterion":"placement-groups/1","status":status,"defects":defects,"unknown":unknown,"advisories":advisories,"measured":measured,"groups":groups}),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    fn probe() -> Value {
        json!({"footprints":[{"ref":"R1","position_nm":[4,0],"side":"F","courtyards":{"F":{"status":"proven","polygons":[[[3,-1],[5,-1],[5,1],[3,1]]]}}}]})
    }
    #[test]
    fn unsourced_anchor_violation_remains_advisory_but_search_rejects_it() {
        let g=validate(&json!([{"id":"g","references":["R1"],"anchor":{"point_nm":[0,0]},"max_distance_nm":3}]),None).unwrap();
        let report = grade(&probe(), &g).unwrap();
        assert_eq!(report["status"], "info");
        assert!(!satisfied(&report));
        assert!(report["defects"].as_array().unwrap().is_empty());
        assert!(!admits(&probe()["footprints"][0], &g[0], &BTreeMap::new()).unwrap());
        let mut sourced = g.clone();
        sourced[0]["constraint_provenance"] = json!({"max_distance_nm":{"class":"manufacturer","enforcement":"hard","citation":"distance limit","value_nm":3}});
        assert_eq!(grade(&probe(), &sourced).unwrap()["status"], "defect");
    }
    #[test]
    fn satisfied_unsourced_region_reports_zero_area_and_stays_pass() {
        let g=validate(&json!([{"id":"g","references":["R1"],"region":{"polygons":[[[0,-2],[6,-2],[6,2],[0,2]]]}}]),None).unwrap();
        let report = grade(&probe(), &g).unwrap();
        assert_eq!(report["status"], "pass");
        assert!(satisfied(&report));
        assert_eq!(report["advisories"][0]["outside_area_mm2"], json!(0.0));
        assert_eq!(report["advisories"][0]["exceeded"], false);
    }
    #[test]
    fn anchored_member_cannot_be_another_groups_anchor() {
        assert!(validate(&json!([{"id":"a","references":["A"],"anchor":{"point_nm":[0,0]},"max_distance_nm":2},{"id":"b","references":["B"],"anchor":{"reference":"A"},"max_distance_nm":2}]),None).is_err());
    }
}
