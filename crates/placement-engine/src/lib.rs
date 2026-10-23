//! Complete placement planning over immutable captured board facts.
pub mod arrangement;
pub mod blocks;
pub mod block_contract;
pub mod block_refinement;
pub mod block_variants;
pub mod constraints;
pub mod exact;
pub mod footprint;
pub mod geometry;
pub mod grading;
pub mod groups;
pub mod locality;
pub mod mechanical;
pub mod netlist;
pub mod packing;
pub mod policy;
pub mod progress;
pub mod quantity;
pub mod source_authority;
mod rank;
mod search;
pub mod targeted;

use anyhow::{Context, Result, ensure};
use serde_json::Value;

pub fn plan(input: &Value) -> Result<Value> {
    ensure!(
        input["schema"] == "agent-ee.rust-planning-input/1",
        "unsupported planning input schema"
    );
    let capture = input.get("facts").context("missing captured facts")?;
    let request = input.get("request").context("missing planning request")?;
    // An explicitly supplied effective policy bypasses request policy derivation,
    // except that blocks also bind their candidates to the captured request policy.
    let policy = match input.get("policy").filter(|p| !p.is_null()) {
        Some(policy) => policy.clone(),
        None => effective_policy(request)?,
    };
    for global_policy in [&policy, &request["policy"]] {
        ensure!(global_policy.get("mechanical_reservations").and_then(|binding| binding.get("selection")).is_none(),
            "global policy cannot use a block mechanical selection");
    }
    ensure!(
        ["blocks", "targeted", "targeted_plans"]
            .iter()
            .filter(|key| request.get(**key).is_some())
            .count()
            <= 1,
        "targeted, targeted_plans and blocks cannot be combined"
    );
    if let Some(section) = request.get("blocks") {
        let xml = input
            .get("netlist_xml")
            .and_then(Value::as_str)
            .context("block arrangement needs the captured native netlist")?;
        arrangement::plan(
            capture,
            section,
            &effective_policy(request)?,
            &policy,
            xml.as_bytes(),
        )
    } else if let Some(spec) = request.get("targeted") {
        targeted::plan(capture, &policy, spec)
    } else if let Some(specs) = request.get("targeted_plans") {
        targeted::select(capture, &policy, specs)
    } else {
        packing::pack(capture, &policy, None)
    }
}

pub fn plan_with_events(input: &Value) -> Result<Value> {
    progress::with_events(|| plan(input))
}

fn effective_policy(request: &Value) -> Result<Value> {
    let mut policy = request
        .get("policy")
        .context("missing captured policy")?
        .clone();
    if let Some(object) = policy.as_object_mut() {
        if let Some(margin) = request
            .get("min_courtyard_edge_mm")
            .filter(|m| !m.is_null())
        {
            if let Some(existing) = object.get("min_courtyard_edge_mm").filter(|m| !m.is_null()) {
                ensure!(
                    python_equal(existing, margin),
                    "conflicting min_courtyard_edge_mm between request and policy"
                );
            }
            object.insert("min_courtyard_edge_mm".into(), margin.clone());
        }
    }
    Ok(policy)
}

// Python compares int/bool/float by numeric value, not JSON token spelling.
// Convert floats to their exact binary rational, so large integers never round.
fn python_equal(a: &Value, b: &Value) -> bool {
    fn number(value: &Value) -> Option<exact::Rat> {
        match value {
            Value::Bool(v) => Some(exact::Rat::from_integer(u8::from(*v).into())),
            Value::Number(v) => {
                if let Ok(integer) = v.to_string().parse::<exact::Int>() {
                    Some(exact::Rat::from_integer(integer))
                } else {
                    exact::Rat::from_float(v.as_f64()?)
                }
            }
            _ => None,
        }
    }
    match (number(a), number(b)) {
        (Some(a), Some(b)) => a == b,
        _ => match (a, b) {
            (Value::Array(a), Value::Array(b)) => {
                a.len() == b.len() && a.iter().zip(b).all(|(a, b)| python_equal(a, b))
            }
            (Value::Object(a), Value::Object(b)) => {
                a.len() == b.len()
                    && a.iter().all(|(key, value)| {
                        b.get(key).is_some_and(|other| python_equal(value, other))
                    })
            }
            _ => a == b,
        },
    }
}

#[cfg(test)]
mod protocol_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn effective_margin_uses_python_numeric_equality() {
        assert_eq!(
            effective_policy(&json!({"policy":{"min_courtyard_edge_mm":0},
            "min_courtyard_edge_mm":0.0}))
            .unwrap()["min_courtyard_edge_mm"],
            json!(0.0)
        );
        assert!(!python_equal(
            &json!(9007199254740993_i64),
            &json!(9007199254740992.0)
        ));
        assert!(python_equal(
            &json!(9007199254740992_i64),
            &json!(9007199254740992.0)
        ));
        assert!(!python_equal(&json!("0"), &json!(0)));
        assert_eq!(
            effective_policy(&json!({"policy":null,"min_courtyard_edge_mm":1})).unwrap(),
            Value::Null
        );
    }
}
