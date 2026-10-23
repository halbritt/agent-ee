//! Captured policy validation and constraint provenance. No inferred authority.
use crate::exact::{self, Int};
use crate::geometry::{self, Polygon};
use anyhow::{Context, Result, ensure};
use num_traits::{One, Zero};
use serde_json::{Value, json};
use std::collections::BTreeSet;

pub fn text<'a>(v: &'a Value, label: &str) -> Result<&'a str> {
    v.as_str()
        .filter(|s| !s.trim().is_empty())
        .with_context(|| format!("{label} must be a nonempty string"))
}
pub fn keys(v: &Value, required: &[&str], optional: &[&str]) -> bool {
    v.as_object().is_some_and(|m| {
        required.iter().all(|k| m.contains_key(*k))
            && m.keys()
                .all(|k| required.contains(&k.as_str()) || optional.contains(&k.as_str()))
    })
}
pub fn strings(v: &Value, label: &str) -> Result<BTreeSet<String>> {
    let a = v
        .as_array()
        .with_context(|| format!("expected unique explicit {label}"))?;
    let mut out = BTreeSet::new();
    for s in a {
        ensure!(
            s.as_str().is_some_and(|s| !s.is_empty()) && out.insert(s.as_str().unwrap().to_owned()),
            "expected unique explicit {label}"
        );
    }
    Ok(out)
}
pub fn edge_margin_nm(policy: &Value) -> Result<Int> {
    let v = &policy["min_courtyard_edge_mm"];
    if v.is_null() {
        return Ok(Int::zero());
    }
    ensure!(
        v.is_number(),
        "min_courtyard_edge_mm must be a nonnegative number"
    );
    let n = exact::nanometres(v)
        .map_err(|_| anyhow::anyhow!("min_courtyard_edge_mm must be exact to 1 nm"))?;
    ensure!(
        n >= Int::zero(),
        "min_courtyard_edge_mm must be nonnegative"
    );
    Ok(n)
}
pub fn sourced(entry: &Value, label: &str, value_nm: Option<&Int>) -> Result<Value> {
    let numeric = value_nm.is_some();
    let shape = if numeric {
        keys(
            entry,
            &["class", "enforcement", "citation", "value_nm"],
            &[],
        )
    } else {
        keys(entry, &["class", "enforcement", "citation"], &[])
    };
    let classes: &[&str] = if numeric {
        &["fixture_author", "manufacturer"]
    } else {
        &[
            "fixture_author",
            "manufacturer",
            "datasheet",
            "board",
            "arrangement_rule",
        ]
    };
    let numeric_ok = value_nm.is_none_or(|n| {
        exact::integer(&entry["value_nm"]).is_ok_and(|v| v >= Int::zero() && &v == n)
    });
    ensure!(
        shape
            && entry["class"]
                .as_str()
                .is_some_and(|c| classes.contains(&c))
            && entry["enforcement"] == "hard"
            && text(&entry["citation"], label).is_ok()
            && numeric_ok,
        "{label} needs a sourced hard value and citation matching the constraint"
    );
    Ok(entry.clone())
}
pub fn classify(
    container: &Value,
    key: &str,
    label: &str,
    value_nm: Option<&Int>,
) -> Result<Value> {
    if let Some(v) = container.get(key) {
        sourced(v, label, value_nm)
    } else {
        Ok(
            json!({"class":"unknown","enforcement":"soft","soft_reason":format!("unsourced {label} is advisory")}),
        )
    }
}
pub fn provenance_entry(v: &Value) -> Result<()> {
    ensure!(
        v.is_object()
            && v["class"].as_str().is_some_and(|c| [
                "owner_stated",
                "derived",
                "assumed",
                "unknown"
            ]
            .contains(&c))
            && v["enforcement"]
                .as_str()
                .is_some_and(|s| ["hard", "soft"].contains(&s))
            && (v["enforcement"] == "soft") == v["soft_reason"].is_string()
            && (v["class"] != "assumed" || v["enforcement"] == "soft")
            && (v["class"] != "unknown" || v["enforcement"] == "hard"),
        "constraint_provenance entries need a class, a hard/soft enforcement and a soft reason"
    );
    Ok(())
}
pub fn obstacle_polygons(raw: &Value) -> Result<Vec<Polygon>> {
    let a = raw.as_array().context("missing or excessive polygons")?;
    ensure!(
        !a.is_empty() && a.len() <= 64,
        "missing or excessive polygons"
    );
    a.iter()
        .map(|p| {
            geometry::qualified_polygons(&json!({"status":"proven","polygons":[p]}))
                .map(|mut p| p.remove(0))
        })
        .collect()
}
pub fn validate(policy: &Value, refs: &BTreeSet<String>) -> Result<()> {
    ensure!(
        policy["schema"] == "agent-ee.packing-policy/1",
        "unsupported packing policy"
    );
    ensure!(
        exact::integer(&policy["step_budget"])
            .is_ok_and(|n| n >= Int::one() && n <= Int::from(1_000_000)),
        "step budget must be 1..1000000"
    );
    ensure!(
        exact::integer(&policy["gap_nm"]).is_ok_and(|n| n >= Int::zero()),
        "gap must be nonnegative integer nanometres"
    );
    edge_margin_nm(policy)?;
    if let Some(v) = policy.get("search_timeout_s") {
        ensure!(
            v.as_f64().is_some_and(|n| n > 0.0 && n <= 86400.0),
            "search_timeout_s must be positive and at most 86400 seconds"
        );
    }
    for name in ["capacity_proof", "carry_seed_fixed_pairs"] {
        if let Some(v) = policy.get(name) {
            ensure!(v.is_boolean(), "{name} must be a boolean");
        }
    }
    for name in ["fixed_refs", "priority_refs", "ground_nets"] {
        let s = strings(&policy[name], name)?;
        ensure!(
            name == "ground_nets" || s.is_subset(refs),
            "unknown declared reference"
        );
    }
    for o in policy["obstacles"]
        .as_array()
        .context("explicit obstacles required")?
    {
        ensure!(
            o["side"] == "F" || o["side"] == "B",
            "obstacle side required"
        );
        obstacle_polygons(&o["polygons"])?;
        classify(o, "provenance", "obstacle", None)?;
    }
    if let Some(v) = policy.get("fixed_overhangs") {
        ensure!(
            v.as_array().is_some_and(|a| a.iter().all(
                |o| o.is_object() && o["reference"].as_str().is_some_and(|r| refs.contains(r))
            )),
            "fixed_overhangs must declare known references"
        );
    }
    if let Some(v) = policy.get("electrical_locality") {
        crate::locality::validate(v)?;
    }
    if let Some(v) = policy.get("groups") {
        crate::groups::validate(v, Some(refs))?;
    }
    if let Some(v) = policy.get("advisory_regions") {
        validate_advisory_regions(v, refs)?;
    }
    if let Some(v) = policy.get("constraint_provenance") {
        ensure!(
            keys(
                v,
                &[],
                &["outline", "fixed", "gap_nm", "min_courtyard_edge_mm"]
            ) && v.get("fixed").is_none_or(Value::is_object),
            "constraint_provenance declares only outline, fixed, gap_nm and min_courtyard_edge_mm"
        );
        for (name, actual) in [
            ("gap_nm", exact::integer(&policy["gap_nm"])?),
            ("min_courtyard_edge_mm", edge_margin_nm(policy)?),
        ] {
            if let Some(entry) = v.get(name) {
                let own = exact::integer(&entry["value_nm"]).unwrap_or_default();
                sourced(entry, &format!("constraint_provenance {name}"), Some(&own))?;
                ensure!(
                    own == actual,
                    "constraint_provenance {name} value differs from policy"
                );
            }
        }
        if let Some(e) = v.get("outline") {
            provenance_entry(e)?;
        }
        if let Some(es) = v["fixed"].as_object() {
            for e in es.values() {
                provenance_entry(e)?;
            }
        }
    }
    Ok(())
}
pub fn undeclared() -> Value {
    json!({"class":"unknown","enforcement":"hard","reason":"no envelope provenance supplied; enforced as before, never treated as owner-stated"})
}
pub fn binding_constraints(policy: &Value, margin: &Int, blocked_by: &[String]) -> Result<Value> {
    let d = &policy["constraint_provenance"];
    let mut entry = d.get("outline").cloned().unwrap_or_else(undeclared);
    entry["constraint"] = json!("outline");
    let mut rows = vec![entry];
    for name in blocked_by {
        let (name, mut e) = if name.starts_with("obstacle ") {
            let i: usize = name
                .split_whitespace()
                .nth(1)
                .context("invalid obstacle binding")?
                .parse()?;
            (
                name.clone(),
                classify(&policy["obstacles"][i], "provenance", name, None)?,
            )
        } else if name.contains(' ') {
            (
                name.clone(),
                json!({"class":"board","enforcement":"hard","reason":"captured native keepout geometry"}),
            )
        } else {
            (
                format!("fixed {name}"),
                d["fixed"].get(name).cloned().unwrap_or_else(undeclared),
            )
        };
        e["constraint"] = json!(name);
        rows.push(e);
    }
    if !margin.is_zero() {
        let mut e=d.get("min_courtyard_edge_mm").cloned().unwrap_or_else(||json!({"class":"unknown","enforcement":"soft","soft_reason":"unsourced courtyard edge margin is advisory"}));
        e["constraint"] = json!("min_courtyard_edge_mm");
        rows.push(e);
    }
    if !exact::integer(&policy["gap_nm"])?.is_zero() {
        let mut e = d.get("gap_nm").cloned().unwrap_or_else(undeclared);
        e["constraint"] = json!("gap_nm");
        rows.push(e);
    }
    Ok(json!(rows))
}
pub fn soft_proof(mut failure: Value, binding: &Value) -> Value {
    let mut soft = Vec::new();
    for row in binding.as_array().into_iter().flatten() {
        if row["enforcement"] == "soft" {
            soft.push(row.clone());
        } else if row["constraint"] == "gap_nm" && row["class"] == "unknown" {
            let mut r = row.clone();
            r["soft_reason"] = json!("unsourced packing gap is advisory");
            soft.push(r);
        }
    }
    failure["binding_constraints"] = binding.clone();
    if !soft.is_empty() {
        failure["outcome"] = json!("unknown");
        failure["owner_confirmation"] = json!(
            soft.iter()
                .map(|r| r["constraint"].clone())
                .collect::<Vec<_>>()
        );
        failure["reason"] = json!(format!(
            "{}; binds soft limit(s) {}, so it is not a design defect until the owner confirms them",
            failure["reason"].as_str().unwrap_or(""),
            soft.iter()
                .map(|r| format!(
                    "{} ({}: {})",
                    r["constraint"].as_str().unwrap_or(""),
                    r["class"].as_str().unwrap_or(""),
                    r["soft_reason"].as_str().unwrap_or("")
                ))
                .collect::<Vec<_>>()
                .join(", ")
        ));
    }
    failure
}
pub fn validate_advisory_regions(regions: &Value, refs: &BTreeSet<String>) -> Result<()> {
    let a = regions
        .as_array()
        .context("advisory_regions must be a list of at most 64 regions")?;
    ensure!(
        a.len() <= 64,
        "advisory_regions must be a list of at most 64 regions"
    );
    let mut ids = BTreeSet::new();
    for r in a {
        ensure!(
            keys(
                r,
                &[],
                &[
                    "id",
                    "source",
                    "proximity_nm",
                    "concern",
                    "subject",
                    "side",
                    "polygons",
                    "shadow_of"
                ]
            ),
            "advisory region declares only concern, id, polygons, proximity_nm, shadow_of, side, source, subject"
        );
        let id = text(&r["id"], "advisory region id")?;
        ensure!(ids.insert(id), "duplicate advisory region {id}");
        let label = format!("advisory region {id}");
        let s = &r["source"];
        ensure!(
            s["class"].as_str().is_some_and(|s| [
                "owner",
                "datasheet",
                "manufacturer",
                "agent-advisory"
            ]
            .contains(&s)),
            "{label} source class must be one of owner, datasheet, manufacturer, agent-advisory"
        );
        let field = if s["class"] == "agent-advisory" {
            "reason"
        } else {
            "citation"
        };
        ensure!(
            keys(s, &["class", field], &[]),
            "{label} {} source needs exactly class and {field}",
            s["class"].as_str().unwrap()
        );
        text(&s[field], &format!("{label} source {field}"))?;
        ensure!(
            exact::integer(&r["proximity_nm"]).is_ok_and(|n| n >= Int::zero()),
            "{label} proximity_nm must be a nonnegative integer"
        );
        if let Some(v) = r.get("concern") {
            text(v, &format!("{label} concern"))?;
        }
        if let Some(v) = r.get("subject") {
            ensure!(
                v.as_str().is_some_and(|s| refs.contains(s)),
                "{label} subject must be a board reference"
            );
        }
        if let Some(o) = r.get("shadow_of") {
            ensure!(
                r.get("side").is_none()
                    && r.get("polygons").is_none()
                    && r.get("subject").is_none(),
                "{label} is a shadow or absolute geometry, not both; a shadow's subject is its owner"
            );
            ensure!(
                keys(o, &["reference", "margin_nm"], &[])
                    && o["reference"].as_str().is_some_and(|s| refs.contains(s))
                    && exact::integer(&o["margin_nm"]).is_ok_and(|n| n >= Int::zero()),
                "{label} shadow_of needs a board reference and a nonnegative integer margin_nm"
            );
        } else {
            ensure!(
                r["side"] == "F" || r["side"] == "B",
                "{label} side must be F or B"
            );
            obstacle_polygons(&r["polygons"])
                .map_err(|e| anyhow::anyhow!("{label} polygons: {e}"))?;
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn numeric_sources_bind_exact_values_and_classes() {
        let source = json!({"class":"fixture_author","enforcement":"hard","citation":"fixture equation","value_nm":17});
        assert!(sourced(&source, "gap", Some(&17.into())).is_ok());
        assert!(sourced(&source, "gap", Some(&18.into())).is_err());
        assert!(sourced(&source, "outline", None).is_err());
        assert_eq!(
            classify(&json!({}), "gap", "gap", Some(&17.into())).unwrap()["enforcement"],
            "soft"
        );
    }
    #[test]
    fn soft_proof_records_only_limits_it_binds() {
        let p = json!({"gap_nm":12,"obstacles":[],"constraint_provenance":{"outline":{"class":"owner_stated","enforcement":"hard"}}});
        let binding = binding_constraints(&p, &0.into(), &[]).unwrap();
        let out = soft_proof(json!({"outcome":"defect","reason":"no space"}), &binding);
        assert_eq!(out["outcome"], "unknown");
        assert_eq!(out["owner_confirmation"], json!(["gap_nm"]));
        assert!(
            out["reason"]
                .as_str()
                .unwrap()
                .contains("unsourced packing gap is advisory")
        );
    }
    #[test]
    fn edge_margin_is_exact_to_one_nanometre() {
        assert_eq!(
            edge_margin_nm(&json!({"min_courtyard_edge_mm":0.000001})).unwrap(),
            1.into()
        );
        assert!(edge_margin_nm(&json!({"min_courtyard_edge_mm":0.0000001})).is_err());
        assert!(edge_margin_nm(&json!({"min_courtyard_edge_mm":"1"})).is_err());
    }
}
