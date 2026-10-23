//! Citation authority shared by native locality validation. Source labels alone
//! never grant authority; the production owner-decision pin table is empty.
use crate::{
    exact::{self, Rat},
    locality, quantity,
};
use num_traits::Zero;
use regex::Regex;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::sync::OnceLock;

fn advisory(reason: impl Into<String>) -> Value {
    json!({"enforcement":"advisory", "reason":reason.into()})
}
fn nonblank(value: &str) -> bool {
    // Python str.strip also recognizes the four ASCII information separators.
    value
        .chars()
        .any(|c| !c.is_whitespace() && !('\u{1c}'..='\u{1f}').contains(&c))
}
pub(crate) fn text(value: &Value) -> bool {
    value.as_str().is_some_and(nonblank)
}
fn lower_hex(value: &str, length: usize) -> bool {
    value.len() == length
        && value
            .bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
}
pub fn reference(value: &Value) -> bool {
    if !value.is_object()
        || ["path", "sha256", "locator"]
            .iter()
            .any(|k| !text(&value[k]))
    {
        return false;
    }
    let path = value["path"].as_str().unwrap();
    let parts: Vec<_> = path
        .split('/')
        .filter(|p| !p.is_empty() && *p != ".")
        .collect();
    !path.starts_with('/')
        && !parts.is_empty()
        && !parts.contains(&"..")
        && lower_hex(value["sha256"].as_str().unwrap(), 64)
}
pub fn verified_reference(value: &Value, records: &Value) -> Value {
    if !reference(value) {
        return advisory("unsourced numeric locality bound");
    }
    let Some(record) = records
        .get(value["path"].as_str().unwrap())
        .and_then(Value::as_str)
    else {
        return advisory("unsourced numeric locality bound");
    };
    if format!("{:x}", Sha256::digest(record.as_bytes())) != value["sha256"].as_str().unwrap() {
        return json!({"enforcement":"integrity", "reason":"source record sha256 mismatch"});
    }
    if !value["quote"]
        .as_str()
        .is_some_and(|q| nonblank(q) && record.contains(q))
    {
        return advisory("unsourced numeric locality bound");
    }
    json!({"enforcement":"verified", "reason":"source record and quote verified"})
}
fn agent_owner(owner: &str) -> bool {
    static WORD: OnceLock<Regex> = OnceLock::new();
    static DIGIT: OnceLock<Regex> = OnceLock::new();
    let word = WORD.get_or_init(|| Regex::new(r"^[\pL\pN_]$").unwrap());
    let digit = DIGIT.get_or_init(|| Regex::new(r"^\p{Nd}$").unwrap());
    let chars: Vec<char> = owner.chars().collect();
    let is_word = |c: char| word.is_match(&c.to_string());
    let is_digit = |c: char| digit.is_match(&c.to_string());
    let at = |start: usize, pattern: &str| {
        chars
            .get(start..start + pattern.len())
            .is_some_and(|slice| {
                slice.iter().copied().zip(pattern.chars()).all(|(c, p)| {
                    c.eq_ignore_ascii_case(&p)
                        || (p == 'i' && matches!(c, 'İ' | 'ı'))
                        || (p == 's' && c == 'ſ')
                })
            })
    };
    for i in 0..chars.len() {
        if at(i, "design") {
            let mut j = i + 6;
            if chars.get(j).is_some_and(|c| matches!(c, '-' | '_' | ' ')) {
                j += 1;
            }
            if at(j, "owner") {
                return true;
            }
        }
        if !at(i, "agent") || (i > 0 && is_word(chars[i - 1])) {
            continue;
        }
        let mut j = i + 5;
        if chars.get(j).is_none_or(|c| !is_word(*c)) {
            return true;
        }
        if chars.get(j) == Some(&'_') {
            j += 1;
        }
        let start = j;
        while chars.get(j).is_some_and(|c| is_digit(*c)) {
            j += 1;
        }
        if j > start && chars.get(j).is_none_or(|c| !is_word(*c)) {
            return true;
        }
    }
    false
}
fn owner_decision(decision: &Value, records: &Value) -> Value {
    let verification = verified_reference(decision, records);
    if verification["enforcement"] != "verified" {
        return verification;
    }
    if !decision["owner"]
        .as_str()
        .is_some_and(|owner| nonblank(owner) && !agent_owner(owner))
    {
        return advisory(
            "owner decision names no owner, or names an agent or the design-owner role",
        );
    }
    let Some(commit) = decision["commit"].as_str().filter(|c| nonblank(c)) else {
        return advisory("owner decision is not cited at a commit (uncommitted)");
    };
    if !lower_hex(commit, 40) || !text(&decision["repository"]) {
        return advisory("owner decision commit is not a full commit id in a named repository");
    }
    // Keep this aligned with source_authority.OWNER_DECISIONS. There are no
    // production pins. Request fields, captured text, and local git state may
    // not extend that table or establish owner authority during planning.
    advisory("owner decision commit does not resolve to a pinned owner commit")
}

fn python_value_repr(value: &Value) -> String {
    match value {
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::String(s) => quantity::python_repr(s),
        Value::Array(v) => format!(
            "[{}]",
            v.iter()
                .map(python_value_repr)
                .collect::<Vec<_>>()
                .join(", ")
        ),
        Value::Object(v) => format!(
            "{{{}}}",
            v.iter()
                .map(|(k, v)| format!("{}: {}", quantity::python_repr(k), python_value_repr(v)))
                .collect::<Vec<_>>()
                .join(", ")
        ),
        Value::Number(_) => String::from_utf8(crate::blocks::canonical(value))
            .unwrap()
            .trim_end()
            .into(),
    }
}
fn manufacturer_capture(citation: &Value, values_mm: &[Rat]) -> Value {
    if !citation.is_object()
        || ["capture", "sha256", "locator"]
            .iter()
            .any(|k| !text(&citation[k]))
    {
        return advisory("manufacturer citation needs a capture name, its sha256 and a locator");
    }
    let record = match locality::manufacturing_record() {
        Ok(record) => record,
        // This can only arise from corrupt embedded source, not caller data.
        // Preserve a hard integrity failure rather than downgrade authority.
        Err(error) => return json!({"enforcement":"integrity", "reason":error.to_string()}),
    };
    let name = citation["capture"].as_str().unwrap();
    let Some(capture) = record["sources"].get(name) else {
        return advisory(format!("no captured manufacturer record named {name}"));
    };
    if capture["sha256"] != citation["sha256"] {
        return advisory("manufacturer capture sha256 differs from the pinned capture (stale)");
    }
    if record["locators"][name] != citation["locator"] {
        return advisory("manufacturer citation locator differs from the captured record locator");
    }
    let fact = &citation["fact"];
    if name != "pcb" || fact != "different_net_clearance_nm" {
        let fact = fact
            .as_str()
            .map(str::to_owned)
            .unwrap_or_else(|| python_value_repr(fact));
        return advisory(format!(
            "manufacturer capture {name} records no fact {fact}"
        ));
    }
    let fact = fact.as_str().unwrap();
    let fact_mm = match exact::integer(&record[fact]) {
        Ok(value) => Rat::new(value, 1_000_000.into()),
        Err(error) => return json!({"enforcement":"integrity", "reason":error.to_string()}),
    };
    if values_mm.iter().any(|v| *v != fact_mm) {
        return advisory(format!(
            "declared value differs from the captured manufacturer fact {fact}"
        ));
    }
    json!({"enforcement":"hard", "reason":"captured manufacturer fact verified by hash"})
}

/// Resolve a citation's authority and its complete typed quote. Counts are
/// `(native rule kind, JSON integer)`; malformed counts cannot grant authority.
pub fn cite(
    class: &str,
    citation: &Value,
    records: &Value,
    values_mm: &[Rat],
    counts: &[(&str, Value)],
) -> Value {
    let kinds: &[&str] = match class {
        "owner" => &["owner_decision"],
        "manufacturer" => &["manufacturer_capture", "manufacturer_source"],
        "datasheet" => &["captured_source"],
        "engineering" => &[
            "captured_source",
            "manufacturer_capture",
            "manufacturer_source",
            "owner_decision",
        ],
        _ => return advisory("default class is never hard"),
    };
    if !citation.is_object() {
        return advisory(format!(
            "free-text {class} label without a resolvable citation"
        ));
    }
    let kind = &citation["kind"];
    if !kind.as_str().is_some_and(|k| kinds.contains(&k)) {
        return advisory(format!(
            "{class} cannot resolve through citation kind {}",
            python_value_repr(kind)
        ));
    }
    if values_mm.is_empty() && counts.is_empty() {
        return advisory("no declared value to bind to the citation");
    }
    if kind == "manufacturer_capture" {
        return if counts.is_empty() {
            manufacturer_capture(citation, values_mm)
        } else {
            advisory("a manufacturer capture records no count")
        };
    }
    if kind == "manufacturer_source" && !text(&citation["manufacturer"]) {
        return advisory("manufacturer source names no manufacturer");
    }
    let mut result = if kind == "owner_decision" {
        owner_decision(citation, records)
    } else {
        verified_reference(citation, records)
    };
    if result["enforcement"] == "verified" {
        result = json!({"enforcement":"hard", "reason":"captured source record verified"});
    } else if kind != "owner_decision" && result["enforcement"] == "advisory" {
        result = advisory("captured source record, locator or quote not verified");
    }
    if result["enforcement"] == "hard" {
        let governed: Vec<_> = values_mm
            .iter()
            .map(|v| Some(quantity::length_mm(v)))
            .chain(counts.iter().map(|(k, n)| quantity::count(k, n)))
            .collect();
        if let Some(reason) = quantity::unqualified(citation["quote"].as_str().unwrap(), &governed)
        {
            return advisory(reason);
        }
    }
    result
}

/// Locality bounds are JSON integers in nanometres, not booleans or floats.
pub fn bound_mm(pair: &Value) -> Vec<Rat> {
    exact::integer(&pair["max_distance_nm"])
        .ok()
        .filter(|v| v >= &crate::exact::Int::zero())
        .map(|v| vec![Rat::new(v, 1_000_000.into())])
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;
    fn captured(kind: &str, quote: &str) -> (Value, Value) {
        (
            json!({"kind":kind,"path":"source.txt","sha256":format!("{:x}", Sha256::digest(quote)),
            "locator":"line 1","quote":quote}),
            json!({"source.txt":quote}),
        )
    }
    fn one_mm() -> Vec<Rat> {
        vec![Rat::from_integer(1.into())]
    }

    #[test]
    fn authority_requires_complete_capture_and_governed_value() {
        let (mut citation, mut records) = captured("captured_source", "1 mm");
        assert_eq!(
            cite("datasheet", &citation, &records, &one_mm(), &[])["enforcement"],
            "hard"
        );
        assert_eq!(
            cite("datasheet", &citation, &records, &[], &[])["reason"],
            "no declared value to bind to the citation"
        );
        citation["locator"] = json!("");
        assert_eq!(
            cite("datasheet", &citation, &records, &one_mm(), &[])["reason"],
            "captured source record, locator or quote not verified"
        );
        citation["locator"] = json!("line 1");
        records["source.txt"] = json!("1 mm modified");
        assert_eq!(
            cite("datasheet", &citation, &records, &one_mm(), &[]),
            json!({"enforcement":"integrity","reason":"source record sha256 mismatch"})
        );
        let (citation, records) = captured("captured_source", "1 mm/s");
        assert_eq!(
            cite("datasheet", &citation, &records, &one_mm(), &[])["reason"],
            "cited quote does not state the declared value"
        );
        let (mut citation, records) = captured("manufacturer_source", "1 mm");
        assert_eq!(
            cite("manufacturer", &citation, &records, &one_mm(), &[])["reason"],
            "manufacturer source names no manufacturer"
        );
        citation["manufacturer"] = json!("Captured vendor");
        assert_eq!(
            cite("manufacturer", &citation, &records, &one_mm(), &[])["enforcement"],
            "hard"
        );
        assert_eq!(
            cite("engineering", &citation, &records, &one_mm(), &[])["enforcement"],
            "hard"
        );
        assert_eq!(
            cite("default", &citation, &records, &one_mm(), &[])["reason"],
            "default class is never hard"
        );
    }

    #[test]
    fn owner_claims_cannot_extend_the_empty_production_pin_table() {
        let (mut citation, records) = captured("owner_decision", "1 mm");
        citation["owner"] = json!("Hal");
        assert_eq!(
            cite("owner", &citation, &records, &one_mm(), &[])["reason"],
            "owner decision is not cited at a commit (uncommitted)"
        );
        citation["commit"] = json!("abc");
        assert_eq!(
            cite("owner", &citation, &records, &one_mm(), &[])["reason"],
            "owner decision commit is not a full commit id in a named repository"
        );
        citation["commit"] = json!("a".repeat(40));
        citation["repository"] = json!("owner/repo");
        assert_eq!(
            cite("owner", &citation, &records, &one_mm(), &[])["reason"],
            "owner decision commit does not resolve to a pinned owner commit"
        );
        for owner in [
            "agent-524",
            "agent524",
            "agent_524",
            "AGENT",
            "design-owner",
            "agent\u{301}",
            "deſİgn_owner",
        ] {
            citation["owner"] = json!(owner);
            assert_eq!(
                cite("owner", &citation, &records, &one_mm(), &[])["reason"],
                "owner decision names no owner, or names an agent or the design-owner role",
                "{owner}"
            );
        }
        for owner in ["Hal", "reagent", "agentine", "agent_524x"] {
            assert!(!agent_owner(owner), "{owner}");
        }
    }

    #[test]
    fn builtin_manufacturer_requires_its_hash_locator_and_recorded_fact() {
        let record = locality::manufacturing_record().unwrap();
        let original = json!({"kind":"manufacturer_capture","capture":"pcb",
            "sha256":record["sources"]["pcb"]["sha256"],"locator":record["locators"]["pcb"],"fact":"different_net_clearance_nm"});
        let values = vec![Rat::new(15.into(), 100.into())];
        assert_eq!(
            cite("manufacturer", &original, &json!({}), &values, &[])["enforcement"],
            "hard"
        );
        assert_eq!(
            cite("manufacturer", &original, &json!({}), &one_mm(), &[])["reason"],
            "declared value differs from the captured manufacturer fact different_net_clearance_nm"
        );
        for (key, value, reason) in [
            (
                "capture",
                json!("missing"),
                "no captured manufacturer record named missing",
            ),
            (
                "sha256",
                json!("0".repeat(64)),
                "manufacturer capture sha256 differs from the pinned capture (stale)",
            ),
            (
                "locator",
                json!("other"),
                "manufacturer citation locator differs from the captured record locator",
            ),
            (
                "fact",
                Value::Null,
                "manufacturer capture pcb records no fact None",
            ),
        ] {
            let mut citation = original.clone();
            citation[key] = value;
            assert_eq!(
                cite("manufacturer", &citation, &json!({}), &values, &[])["reason"],
                reason
            );
        }
        assert_eq!(
            cite(
                "manufacturer",
                &original,
                &json!({}),
                &values,
                &[("via_count", json!(1))]
            )["reason"],
            "a manufacturer capture records no count"
        );
    }

    #[test]
    fn unsafe_paths_and_non_python_whitespace_do_not_verify() {
        let (mut citation, records) = captured("captured_source", "1 mm");
        for path in [
            "/source.txt",
            "../source.txt",
            "nested/../source.txt",
            ".",
            "",
        ] {
            citation["path"] = json!(path);
            assert!(!reference(&citation), "{path}");
        }
        citation["path"] = json!("source.txt");
        citation["locator"] = json!("\u{1c}");
        assert_eq!(
            verified_reference(&citation, &records)["enforcement"],
            "advisory"
        );
        citation["locator"] = json!("line 1");
        citation["quote"] = json!("\u{1c}");
        assert_eq!(
            verified_reference(&citation, &records)["enforcement"],
            "advisory"
        );
    }
}
