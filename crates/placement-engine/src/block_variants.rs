//! Native face alternatives. Hashes bind evidence; only the native writer can
//! establish that a supplied capture actually came from the source board.
use crate::{blocks, footprint, netlist::NativeInventory};
use anyhow::{Context, Result, ensure};
use serde_json::{Value, json};
use std::collections::BTreeSet;

pub const SCHEMA: &str = "agent-ee.block-candidate-set/1";

fn faces(value: &Value) -> Result<Vec<String>> {
    let values = blocks::refs(value)?;
    ensure!(
        !values.is_empty() && values.iter().all(|f| f == "F" || f == "B"),
        "eligibility must name unique F/B faces"
    );
    Ok(["F", "B"]
        .into_iter()
        .filter(|f| values.iter().any(|v| v == f))
        .map(str::to_owned)
        .collect())
}
fn eligible(record: &Value, block: &str, reference: Option<&str>) -> Result<Vec<String>> {
    ensure!(
        matches!(
            record["schema"].as_str(),
            Some(
                "fome-mc.side-eligibility/1"
                    | "fome-mc.side-eligibility/1 (proposed consumer: agent-ee, AGENTEE-479)"
            )
        ),
        "expected bound side eligibility"
    );
    let default = faces(&record["default"]["faces"])?;
    let entry = &record["blocks"][block];
    let allowed = faces(&entry["eligible_faces"])?;
    ensure!(
        allowed.iter().all(|f| default.contains(f)),
        "block faces exceed default eligibility"
    );
    ensure!(
        allowed == default
            || entry["face_restrictions"]
                .as_array()
                .is_some_and(|r| !r.is_empty()),
        "narrowed faces require explicit restrictions"
    );
    if let Some(members) = record.get("members") {
        ensure!(
            members.is_object(),
            "eligibility members must be a reference mapping"
        );
    }
    if let Some(r) = reference {
        if let Some(member) = record.get("members").and_then(|v| v.get(r)) {
            let member_faces = faces(&member["eligible_faces"])?;
            ensure!(
                member_faces.iter().all(|f| default.contains(f)),
                "member faces exceed default eligibility"
            );
            return Ok(member_faces);
        }
    }
    Ok(allowed)
}
fn sha(value: &Value) -> Result<()> {
    let s = blocks::text(value)?;
    ensure!(
        s.len() == 64
            && s.bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)),
        "expected SHA-256"
    );
    Ok(())
}

pub fn admit(
    entry: &Value,
    capture: &Value,
    inventory: &NativeInventory,
    derivation: &Value,
    policy: &Value,
    eligibility: Option<&Value>,
) -> Result<Value> {
    if entry["candidate"]["schema"] != SCHEMA {
        return blocks::admit(entry, capture, inventory, derivation, policy);
    }
    blocks::keys(entry, &["candidate"], &[])?;
    let doc = &entry["candidate"];
    blocks::keys(
        doc,
        &[
            "schema",
            "block_id",
            "sources",
            "eligibility",
            "captured_members",
            "variants",
        ],
        &[],
    )?;
    let bid = blocks::text(&doc["block_id"])?;
    let binding = &doc["eligibility"];
    blocks::keys(binding, &["record", "record_sha256", "eligible_faces"], &[])?;
    let record = eligibility.context("candidate sets require bound side_eligibility")?;
    ensure!(
        record == &binding["record"] && binding["record_sha256"] == blocks::digest(record),
        "eligibility record binding mismatch"
    );
    ensure!(
        record["sources"]["board_sha256"] == capture["board_sha256"],
        "eligibility belongs to another source board"
    );
    let allowed = eligible(record, bid, None)?;
    ensure!(
        binding["eligible_faces"] == json!(allowed),
        "eligibility face order mismatch"
    );
    let variants = doc["variants"]
        .as_object()
        .context("expected face variants")?;
    ensure!(
        variants.keys().cloned().collect::<BTreeSet<_>>() == allowed.iter().cloned().collect(),
        "every eligible face needs one explicit result"
    );
    let expected = json!({"board_sha256":capture["board_sha256"],"inventory_sha256":derivation["inventory_sha256"],"allocation_sha256":derivation["allocation_sha256"],"derivation_sha256":blocks::digest(derivation),"policy_sha256":blocks::digest(policy)});
    ensure!(
        doc["sources"] == expected,
        "candidate-set original source binding mismatch"
    );
    let found: Vec<_> = blocks::rows(&derivation["blocks"])?
        .iter()
        .filter(|b| b["id"] == bid)
        .collect();
    ensure!(found.len() == 1, "unknown or duplicate candidate-set block");
    let board = blocks::board(capture)?;
    let references = blocks::natural(blocks::refs(&found[0]["references"])?);
    let originals: Vec<_> = references
        .iter()
        .map(|r| json!({"ref":r,"uuid":board[r]["uuid"],"side":board[r]["side"]}))
        .collect();
    ensure!(
        doc["captured_members"] == json!(originals),
        "candidate-set captured membership mismatch"
    );
    let mut admitted = Vec::new();
    for face in &allowed {
        let variant = &variants[face];
        blocks::keys(
            variant,
            &[
                "status",
                "capture",
                "capture_sha256",
                "board_sha256",
                "derivation",
                "members",
                "candidate",
                "failure",
            ],
            &[],
        )?;
        let members = blocks::rows(&variant["members"])?;
        ensure!(
            members.len() == originals.len(),
            "variant membership differs"
        );
        for (member, original) in members.iter().zip(&originals) {
            blocks::keys(
                member,
                &[
                    "ref",
                    "uuid",
                    "source_side",
                    "side",
                    "flipped",
                    "pad_inventory_sha256",
                ],
                &[],
            )?;
            ensure!(
                member["ref"] == original["ref"]
                    && member["uuid"] == original["uuid"]
                    && member["source_side"] == original["side"],
                "variant member identity differs"
            );
            let side = blocks::text(&member["side"])?;
            ensure!(
                eligible(record, bid, member["ref"].as_str())?
                    .iter()
                    .any(|f| f == side),
                "variant member forbidden side"
            );
            ensure!(
                member["flipped"].as_bool() == Some(member["side"] != original["side"]),
                "variant native flip declaration differs"
            );
        }
        if variant["status"] == "native_flip_refused" {
            ensure!(
                [
                    "capture",
                    "capture_sha256",
                    "board_sha256",
                    "derivation",
                    "candidate"
                ]
                .iter()
                .all(|k| variant[k].is_null())
                    && members.iter().all(|m| m["pad_inventory_sha256"].is_null())
                    && variant["failure"]["status"] == variant["status"],
                "inconsistent native refusal evidence"
            );
            continue;
        }
        footprint::validate_capture(&variant["capture"])?;
        ensure!(
            variant["capture_sha256"] == blocks::digest(&variant["capture"])
                && variant["board_sha256"] == variant["capture"]["board_sha256"],
            "variant capture hash mismatch"
        );
        let mut native_derivation = derivation.clone();
        native_derivation["board_sha256"] = variant["board_sha256"].clone();
        ensure!(
            variant["derivation"] == native_derivation,
            "variant derivation differs beyond board binding"
        );
        for member in members {
            sha(&member["pad_inventory_sha256"])?;
        }
        if variant["status"] != "packed" {
            ensure!(
                variant["candidate"].is_null()
                    && variant["failure"].is_object()
                    && variant["failure"]["status"] == variant["status"],
                "failed face needs complete typed failure"
            );
            continue;
        }
        ensure!(
            variant["failure"].is_null(),
            "packed face cannot have failure"
        );
        let candidate = &variant["candidate"];
        ensure!(
            candidate["block_id"] == bid,
            "variant belongs to another block"
        );
        let candidate_members = blocks::rows(&candidate["members"])?;
        ensure!(
            candidate_members.len() == members.len(),
            "candidate variant membership differs"
        );
        for member in members {
            ensure!(
                candidate_members
                    .iter()
                    .any(|m| m["reference"] == member["ref"] && m["side"] == member["side"]),
                "candidate native side mismatch"
            );
        }
        ensure!(
            members
                .iter()
                .any(|m| m["ref"] == candidate["frame"]["anchor"] && m["side"] == face.as_str()),
            "variant face is not its anchor face"
        );
        let mut a = blocks::admit(
            &json!({"candidate":candidate}),
            &variant["capture"],
            inventory,
            &native_derivation,
            policy,
        )?;
        ensure!(
            a["status"] == "admitted",
            "variant candidate admission refused: {}",
            a["problems"]
        );
        a["selected_face"] = json!(face);
        a["capture"] = variant["capture"].clone();
        admitted.push(a);
    }
    if admitted.is_empty() {
        return Ok(
            json!({"status":"refused", "problems":[{"kind":"no_packed_native_variant"}]}),
        );
    }
    let mut result = admitted[0].clone();
    result.as_object_mut().unwrap().remove("capture");
    result.as_object_mut().unwrap().remove("selected_face");
    result["variants"] = json!(admitted);
    result["candidate_set_sha256"] = json!(blocks::digest(doc));
    Ok(result)
}
