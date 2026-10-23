//! Exact electrical-locality validation, source qualification, grading and search state.
use crate::{
    exact::{self, Int, Point, Rat},
    policy,
};
use anyhow::{Context, Result, bail, ensure};
use num_traits::{One, ToPrimitive, Zero};
use regex::Regex;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    sync::OnceLock,
};

pub fn roles(o: &Value) -> impl Iterator<Item = &'static str> + '_ {
    ["supply", "return", "switch"]
        .into_iter()
        .filter(|r| o.get(*r).is_some())
}
pub fn validate(d: &Value) -> Result<()> {
    let mut walk = vec![(d, 0_usize)];
    let mut nodes = 0;
    while let Some((v, depth)) = walk.pop() {
        nodes += 1;
        ensure!(nodes <= 200_000, "locality document exceeds 200000 nodes");
        match v {
            Value::Array(a) => {
                ensure!(depth <= 64, "locality document nesting exceeds 64 levels");
                walk.extend(a.iter().map(|v| (v, depth + 1)));
            }
            Value::Object(m) => {
                ensure!(depth <= 64, "locality document nesting exceeds 64 levels");
                walk.extend(m.values().map(|v| (v, depth + 1)));
            }
            _ => {}
        }
    }
    ensure!(
        policy::keys(d, &["schema", "obligations"], &["source_records"])
            && d["schema"] == "agent-ee.electrical-locality/1"
            && d["obligations"].as_array().is_some_and(|a| !a.is_empty()),
        "expected agent-ee.electrical-locality/1 with nonempty obligations"
    );
    if let Some(r) = d.get("source_records") {
        ensure!(
            r.as_object()
                .is_some_and(|m| m.values().all(Value::is_string)),
            "locality source_records must map source paths to captured UTF-8 text"
        );
    }
    let mut ids = BTreeSet::new();
    for o in d["obligations"].as_array().unwrap() {
        ensure!(
            policy::keys(
                o,
                &["id", "metric", "supply", "return"],
                &["switch", "kind", "ic", "inductor", "return_net"]
            ),
            "locality obligation needs id, metric, supply and return; optional switch"
        );
        ensure!(
            o["id"]
                .as_str()
                .is_some_and(|s| !s.trim().is_empty() && ids.insert(s)),
            "locality obligation ids must be unique nonempty text"
        );
        ensure!(o["metric"].is_string(), "locality metric must be text");
        for role in roles(o) {
            let p = &o[role];
            ensure!(
                policy::keys(
                    p,
                    &["from", "to"],
                    &["max_distance_nm", "citation", "provenance"]
                ),
                "locality pair needs from/to, optionally max_distance_nm and citation"
            );
            ensure!(
                p.get("provenance").is_none_or(Value::is_object),
                "locality provenance must be an object"
            );
            for end in ["from", "to"] {
                let e = &p[end];
                ensure!(
                    policy::keys(e, &["reference", "pad", "net"], &["uuid"])
                        && e.as_object()
                            .unwrap()
                            .values()
                            .all(|v| v.as_str().is_some_and(|s| !s.trim().is_empty())),
                    "locality endpoint needs explicit reference, pad and net; optional captured uuid"
                );
                if let Some(uuid) = e.get("uuid") {
                    let s = uuid.as_str().unwrap();
                    ensure!(
                        s.len() == 36
                            && s.chars().enumerate().all(
                                |(i, c)| if [8, 13, 18, 23].contains(&i) {
                                    c == '-'
                                } else {
                                    c.is_ascii_digit() || ('a'..='f').contains(&c)
                                }
                            ),
                        "locality uuid must use canonical captured spelling"
                    );
                }
            }
            ensure!(
                p["from"]["reference"] != p["to"]["reference"],
                "locality endpoints must name distinct footprints"
            );
            if let Some(v) = p.get("max_distance_nm") {
                ensure!(
                    exact::integer(v).is_ok_and(|n| n >= Int::zero()),
                    "locality limit must be nonnegative integer nanometres"
                );
            }
            if let Some(v) = p.get("citation") {
                ensure!(
                    v.as_str().is_some_and(|s| !s.trim().is_empty()),
                    "locality citation must be nonempty text"
                );
            }
        }
        let s = &o["supply"];
        let g = &o["return"];
        ensure!(
            s["to"]["reference"] == g["to"]["reference"],
            "supply and return must name the same capacitor reference"
        );
        ensure!(
            !["from", "to"]
                .iter()
                .any(|k| s[k]["reference"] == g[k]["reference"] && s[k]["pad"] == g[k]["pad"]),
            "supply and return must select distinct physical pads"
        );
        if s["from"]["reference"] != g["from"]["reference"] {
            ensure!(
                !["from", "to"]
                    .iter()
                    .any(|a| ["from", "to"].iter().any(|b| s[a]["net"] == g[b]["net"])),
                "distinct-source supply and return nets must differ"
            );
        }
        if ["kind", "ic", "inductor", "return_net"]
            .iter()
            .any(|k| o.get(*k).is_some())
        {
            ensure!(
                o["kind"] == "buck-output-loop"
                    && ["ic", "inductor", "return_net"]
                        .iter()
                        .all(|k| o[k].as_str().is_some_and(|s| !s.trim().is_empty())),
                "buck-output-loop needs explicit kind, ic, inductor and return_net"
            );
            ensure!(
                o["ic"] != o["inductor"],
                "loop IC and inductor must be distinct footprints"
            );
            ensure!(
                s["from"]["reference"] == o["inductor"],
                "loop supply must start at the declared inductor"
            );
            ensure!(
                g["from"]["reference"] == o["ic"],
                "loop return must start at the declared IC"
            );
            ensure!(
                ["from", "to"]
                    .iter()
                    .all(|k| g[k]["net"] == o["return_net"]),
                "loop return must use the declared return_net"
            );
            ensure!(
                ["from", "to"]
                    .iter()
                    .all(|k| s[k]["net"] != o["return_net"]),
                "loop supply and return nets must differ"
            );
            if let Some(w) = o.get("switch") {
                ensure!(
                    w["to"]["reference"] == o["inductor"],
                    "loop switch must end at the declared inductor"
                );
                ensure!(
                    !["from", "to"].iter().any(|a| w[a]["net"] == o["return_net"]
                        || ["from", "to"].iter().any(|b| w[a]["net"] == s[b]["net"])),
                    "loop switch nets must differ from supply and return"
                );
            }
        }
        if let Some(w) = o.get("switch") {
            ensure!(
                w["from"]["reference"] == g["from"]["reference"],
                "switch pair must start at the declared IC"
            );
        }
    }
    Ok(())
}
fn pads(fp: &Value) -> Result<&Vec<Value>> {
    fp["pads"].as_array().context("native pads unavailable")
}
pub fn resolve(footprints: &[Value], e: &Value) -> Result<[Rat; 2]> {
    let fps: Vec<_> = footprints
        .iter()
        .filter(|f| f["ref"] == e["reference"])
        .collect();
    ensure!(fps.len() == 1, "reference missing or duplicated");
    let fp = fps[0];
    let p = if let Some(uuid) = e.get("uuid") {
        let found: Vec<_> = footprints
            .iter()
            .flat_map(|f| {
                f["pads"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .map(move |p| (f, p))
            })
            .filter(|(_, p)| p.get("uuid") == Some(uuid))
            .collect();
        ensure!(
            found.len() == 1 && std::ptr::eq(found[0].0, fp),
            "captured pad UUID missing, duplicated or belongs to another reference"
        );
        ensure!(
            found[0].1["number"] == e["pad"],
            "captured pad UUID number differs from declaration"
        );
        found[0].1
    } else {
        let found: Vec<_> = pads(fp)?
            .iter()
            .filter(|p| p["number"] == e["pad"])
            .collect();
        ensure!(found.len() == 1, "pad missing or duplicated");
        found[0]
    };
    ensure!(
        p["net"] == e["net"],
        "native pad net differs from declaration"
    );
    let pos = exact::point(&p["position_nm"])
        .map_err(|_| anyhow::anyhow!("native pad centre unavailable"))?;
    let reason = format!(
        "native copper centroid unavailable: {}",
        p["copper_centroid_reason"]
            .as_str()
            .unwrap_or("missing evidence")
    );
    let off = p["copper_centroid_offset_nm"]
        .as_array()
        .filter(|a| a.len() == 2)
        .with_context(|| reason.clone())?;
    let x = exact::rational(&off[0]).map_err(|_| anyhow::anyhow!(reason.clone()))?;
    let y = exact::rational(&off[1]).map_err(|_| anyhow::anyhow!(reason.clone()))?;
    Ok([Rat::from_integer(pos.x) + x, Rat::from_integer(pos.y) + y])
}
fn reference(v: &Value) -> bool {
    if !v.is_object()
        || ["path", "sha256", "locator"]
            .iter()
            .any(|k| v[k].as_str().is_none_or(|s| s.trim().is_empty()))
    {
        return false;
    }
    let p = v["path"].as_str().unwrap();
    let parts: Vec<_> = p
        .split('/')
        .filter(|s| !s.is_empty() && *s != ".")
        .collect();
    !p.starts_with('/')
        && !parts.is_empty()
        && !parts.contains(&"..")
        && v["sha256"].as_str().is_some_and(|s| {
            s.len() == 64
                && s.chars()
                    .all(|c| c.is_ascii_digit() || ('a'..='f').contains(&c))
        })
}
fn verified(v: &Value, records: &Value) -> (&'static str, &'static str) {
    let fallback = ("advisory", "unsourced numeric locality bound");
    if !reference(v) {
        return fallback;
    }
    let Some(text) = records
        .get(v["path"].as_str().unwrap())
        .and_then(Value::as_str)
    else {
        return fallback;
    };
    if format!("{:x}", Sha256::digest(text.as_bytes())) != v["sha256"].as_str().unwrap() {
        return ("integrity", "source record sha256 mismatch");
    }
    if !v["quote"]
        .as_str()
        .is_some_and(|s| !s.trim().is_empty() && text.contains(s))
    {
        return fallback;
    }
    ("verified", "source record and quote verified")
}
pub fn classify(pair: &Value, records: &Value) -> Value {
    let p = &pair["provenance"];
    let class = p.get("class").and_then(Value::as_str).unwrap_or("default");
    let mut out =
        json!({"class":class,"enforcement":"advisory","reason":"unsourced numeric locality bound"});
    if ![
        "datasheet",
        "manufacturer",
        "owner",
        "engineering",
        "default",
    ]
    .contains(&class)
    {
        out["class"] = json!("default");
        return out;
    }
    if !p["basis"].as_str().is_some_and(|s| !s.trim().is_empty()) {
        return out;
    }
    if class == "manufacturer" {
        out["enforcement"] = json!("feasibility");
        out["reason"] =
            json!("Captured manufacturing minimum cannot establish an electrical maximum");
        return out;
    }
    if class == "owner" {
        let d = &p["decision_reference"];
        let (status, reason) = verified(d, records);
        if status == "integrity" {
            out["enforcement"] = json!(status);
            out["reason"] = json!(reason);
            return out;
        }
        static OWNER: OnceLock<Regex> = OnceLock::new();
        let re = OWNER
            .get_or_init(|| Regex::new(r"(?i)\bagent(?:[- _]?\d+)?\b|design[-_ ]?owner").unwrap());
        if status == "verified"
            && d["owner"]
                .as_str()
                .is_some_and(|s| !s.trim().is_empty() && !re.is_match(s))
        {
            out["enforcement"] = json!("hard");
            out["reason"] = json!("explicit owner decision reference");
        }
    }
    if class == "datasheet" {
        let d = &p["derivation"];
        if !d.is_object() {
            return out;
        }
        let (status, reason) = verified(&d["source"], records);
        if status == "integrity" {
            out["enforcement"] = json!(status);
            out["reason"] = json!(reason);
            return out;
        }
        if status != "verified" {
            return out;
        }
        let value = if d["kind"] == "numeric" {
            exact::integer(&d["value_nm"])
                .ok()
                .filter(|n| n >= &Int::zero())
                .filter(|n| {
                    static TOKENS: OnceLock<Regex> = OnceLock::new();
                    let re = TOKENS
                        .get_or_init(|| Regex::new(r"([0-9]+(?:\.[0-9]+)?)\s*(nm|mm)\b").unwrap());
                    let quote = d["source"]["quote"].as_str().unwrap_or("");
                    re.captures_iter(quote).any(|cap| {
                        let m = cap.get(0).unwrap();
                        if quote[..m.start()]
                            .chars()
                            .next_back()
                            .is_some_and(|c| c.is_alphanumeric() || c == '_' || c == '.')
                        {
                            return false;
                        }
                        let Ok(v) = exact::decimal(&json!(&cap[1])) else {
                            return false;
                        };
                        v * Rat::from_integer(if &cap[2] == "mm" {
                            1_000_000.into()
                        } else {
                            1.into()
                        }) == Rat::from_integer(n.clone())
                    })
                })
        } else if d["kind"] == "layout" {
            match (
                exact::integer(&d["distance_units"]),
                exact::integer(&d["scale_nm_per_unit"]),
            ) {
                (Ok(n), Ok(scale))
                    if n >= Int::zero()
                        && scale > Int::zero()
                        && d["scale_reference"]
                            .as_str()
                            .is_some_and(|s| !s.trim().is_empty()) =>
                {
                    Some(n * scale)
                }
                _ => None,
            }
        } else {
            None
        };
        if value.is_some() && value == exact::integer(&pair["max_distance_nm"]).ok() {
            out["enforcement"] = json!("hard");
            out["reason"] = json!("numeric derivation equals declared bound");
        }
    }
    out
}
fn manufacturing_record() -> Result<&'static Value> {
    static RECORD: OnceLock<Result<Value, String>> = OnceLock::new();
    let r = RECORD.get_or_init(|| {
        let bytes = include_bytes!("../../../src/agent_ee/data/jlcpcb-locality.json");
        if format!("{:x}", Sha256::digest(bytes))
            != "92561871360fe5652778275ab31009e80e70b2e15af3d905a8d16eaa67aa063d"
        {
            return Err("captured JLCPCB numeric record hash mismatch".to_owned());
        }
        serde_json::from_slice(bytes).map_err(|e| e.to_string())
    });
    r.as_ref().map_err(|e| anyhow::anyhow!(e.clone()))
}

/// Structural validation of retained floor evidence. Native admission still regrades lands.
pub fn validate_floor_record(row: &Value) -> Result<()> {
    let floor = &row["manufacturing_floor"];
    ensure!(
        policy::keys(
            floor,
            &["status"],
            &[
                "reason",
                "floor_nm",
                "kind",
                "source",
                "assembly",
                "different_net_clearance_nm",
                "limitation"
            ]
        ),
        "locality manufacturing_floor has unexpected or missing keys"
    );
    if floor["status"] == "unknown" {
        ensure!(
            policy::keys(floor, &["status", "reason"], &[]),
            "unknown locality manufacturing_floor needs exactly status and reason"
        );
        policy::text(
            &floor["reason"],
            "unknown locality manufacturing_floor reason",
        )?;
    } else if floor["status"] == "known" {
        ensure!(
            policy::keys(
                floor,
                &[
                    "status",
                    "floor_nm",
                    "kind",
                    "source",
                    "assembly",
                    "different_net_clearance_nm",
                    "limitation"
                ],
                &[]
            ),
            "known locality manufacturing_floor has unexpected or missing keys"
        );
        ensure!(
            exact::integer(&floor["floor_nm"]).is_ok_and(|n| n >= Int::zero()),
            "locality manufacturing_floor floor_nm must be a nonnegative integer"
        );
        let record = manufacturing_record()?;
        ensure!(
            floor["kind"] == "necessary_copper_floor"
                && floor["source"] == record["sources"]["pcb"]
                && exact::integer(&floor["different_net_clearance_nm"]).ok()
                    == exact::integer(&record["different_net_clearance_nm"]).ok(),
            "locality manufacturing_floor has stale provenance"
        );
        policy::text(
            &floor["limitation"],
            "locality manufacturing_floor limitation",
        )?;
        ensure!(
            floor["assembly"].is_object() && floor["assembly"]["status"] == "unknown",
            "locality manufacturing_floor assembly remains unknown"
        );
        policy::text(
            &floor["assembly"]["reason"],
            "locality manufacturing_floor assembly reason",
        )?;
    } else {
        bail!("locality manufacturing_floor status must be known or unknown");
    }
    Ok(())
}
fn package(fp: &Value) -> Result<String> {
    let name = fp["fpid"]
        .as_str()
        .unwrap_or("")
        .rsplit(':')
        .next()
        .unwrap_or("");
    static RE: OnceLock<Vec<(Regex, &str)>> = OnceLock::new();
    let rs = RE.get_or_init(|| {
        [
            (r"^[CRL]_(0201|0402|0603|0805|1206)(?:_|$)", "chip"),
            (r"^(?:H?SOIC|SOP)-", "SOIC"),
            (r"^(?:QFN|VQFN|WQFN)-", "QFN"),
            (r"^(?:LQFP|TQFP|QFP)-", "QFP"),
            (r"^SOT-(?:23|363)(?:_|-|$)", "SOT"),
            (r"^BGA-", "BGA"),
        ]
        .into_iter()
        .map(|(r, n)| (Regex::new(r).unwrap(), n))
        .collect()
    });
    for (re, f) in rs {
        if let Some(c) = re.captures(name) {
            return Ok(if *f == "chip" {
                format!("chip {}", &c[1])
            } else {
                (*f).to_owned()
            });
        }
    }
    bail!("package family absent from captured JLCPCB spacing table")
}
fn pad_radius(p: &Value) -> Result<Rat> {
    ensure!(
        p["attribute"] == "SMD" || p["attribute"] == "smd",
        "pad geometry unsupported for inscribed copper disk"
    );
    ensure!(
        p["shape"]
            .as_i64()
            .is_some_and(|v| [0, 1, 2, 4].contains(&v)),
        "pad geometry unsupported for inscribed copper disk"
    );
    let a = p["size_nm"]
        .as_array()
        .filter(|a| a.len() == 2)
        .context("native pad size unavailable")?;
    let x = exact::integer(&a[0]).map_err(|_| anyhow::anyhow!("native pad size unavailable"))?;
    let y = exact::integer(&a[1]).map_err(|_| anyhow::anyhow!("native pad size unavailable"))?;
    ensure!(
        x > Int::zero() && y > Int::zero(),
        "native pad size unavailable"
    );
    Ok(Rat::new(x.min(y), 2.into()))
}
#[derive(Clone)]
struct Disk {
    pad: Value,
    position: [Rat; 2],
    radius: Rat,
}
fn disks(fp: &Value) -> Vec<Disk> {
    fp["pads"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|p| {
            if p["net"].as_str().is_none_or(str::is_empty) {
                return None;
            }
            let radius = pad_radius(p).ok()?;
            let mut e = json!({"reference":fp["ref"],"pad":p["number"],"net":p["net"]});
            if p["uuid"].as_str().is_some_and(|s| !s.is_empty()) {
                e["uuid"] = p["uuid"].clone();
            }
            let position = resolve(std::slice::from_ref(fp), &e).ok()?;
            Some(Disk {
                pad: p.clone(),
                position,
                radius,
            })
        })
        .collect()
}
fn squared(a: &[Rat; 2], b: &[Rat; 2]) -> Rat {
    let x = &a[0] - &b[0];
    let y = &a[1] - &b[1];
    &x * &x + &y * &y
}
fn ceil_distance(a: &[Rat; 2], b: &[Rat; 2]) -> Int {
    let s = squared(a, b);
    let root = exact::isqrt(&exact::floor(&s));
    if &root * &root * s.denom() < *s.numer() {
        root + 1
    } else {
        root
    }
}
fn floor_inner(
    fps: &[Value],
    pair: &Value,
    centers: &[[Rat; 2]; 2],
    cache: &mut BTreeMap<String, Vec<Disk>>,
) -> Result<Value> {
    let mut selected = Vec::new();
    let mut radii = Vec::new();
    for end in ["from", "to"] {
        let e = &pair[end];
        let found: Vec<_> = fps.iter().filter(|f| f["ref"] == e["reference"]).collect();
        ensure!(found.len() == 1, "floor needs unique native footprints");
        let fp = found[0];
        let ps: Vec<_> = pads(fp)?
            .iter()
            .filter(|p| {
                p["number"] == e["pad"] && (e.get("uuid").is_none() || p["uuid"] == e["uuid"])
            })
            .collect();
        ensure!(ps.len() == 1, "floor needs unique native pads");
        radii.push(pad_radius(ps[0])?);
        selected.push(fp);
    }
    ensure!(
        selected[0]["side"] == selected[1]["side"],
        "opposite-side assembly spacing not qualified"
    );
    let record = manufacturing_record()?;
    ensure!(
        pads(selected[0])?
            .len()
            .saturating_mul(pads(selected[1])?.len())
            <= 4096,
        "manufacturing pad-pair work budget exceeded"
    );
    for fp in &selected {
        let r = fp["ref"].as_str().context("reference missing")?;
        cache.entry(r.to_owned()).or_insert_with(|| disks(fp));
    }
    let ds = [
        &cache[selected[0]["ref"].as_str().unwrap()],
        &cache[selected[1]["ref"].as_str().unwrap()],
    ];
    let clearance = exact::integer(&record["different_net_clearance_nm"])?;
    let mut bound = Rat::zero();
    for a in ds[0] {
        for b in ds[1] {
            if a.pad["net"] == b.pad["net"] {
                continue;
            }
            let common = a.pad["layers"].as_array().into_iter().flatten().any(|v| {
                v.as_str().is_some_and(|s| s.ends_with(".Cu"))
                    && b.pad["layers"].as_array().is_some_and(|ls| ls.contains(v))
            });
            if !common {
                continue;
            }
            let offset =
                ceil_distance(&a.position, &centers[0]) + ceil_distance(&b.position, &centers[1]);
            bound = bound.max(&a.radius + &b.radius + Rat::from_integer(&clearance - offset));
        }
    }
    let mut assembly = json!({"status":"unknown","reason":"native solder lands do not establish physical body/lead extents"});
    let spacing = (|| -> Result<(Vec<String>, &Value)> {
        let mut fs = selected
            .iter()
            .map(|f| package(f))
            .collect::<Result<Vec<_>>>()?;
        fs.sort();
        let row = record["component_spacing"]
            .as_array()
            .context("spacing table missing")?
            .iter()
            .find(|r| r["packages"] == json!(fs))
            .context("package pair absent")?;
        Ok((fs, row))
    })();
    match spacing {
        Ok((families, row)) => {
            assembly["packages"] = json!(families);
            assembly["pad_envelope_estimate_nm"] = exact::integer_value(&exact::floor(
                &(&radii[0] + &radii[1] + Rat::from_integer(exact::integer(&row["spacing_nm"])?)),
            ));
            assembly["component_spacing_nm"] = row["spacing_nm"].clone();
            assembly["source"] = record["sources"]["spacing"].clone();
        }
        Err(_) => {
            assembly["reason"] = json!("package pair absent from captured JLCPCB spacing table")
        }
    }
    Ok(
        json!({"status":"known","floor_nm":exact::integer_value(&exact::floor(&bound)),"kind":"necessary_copper_floor","source":record["sources"]["pcb"],"assembly":assembly,"different_net_clearance_nm":record["different_net_clearance_nm"],"limitation":"Native different-net inscribed disks only; exact whole-footprint minimum unknown"}),
    )
}
pub fn grade(capture: &Value, d: &Value) -> Result<Value> {
    validate(d)?;
    let fps = capture["footprints"]
        .as_array()
        .context("footprints required")?;
    let (mut measured, mut defects, mut unknown, mut advisories) =
        (Vec::new(), Vec::new(), Vec::new(), Vec::new());
    let mut cache = BTreeMap::new();
    for o in d["obligations"].as_array().unwrap() {
        for role in roles(o) {
            let p = &o[role];
            let mut refs = vec![p["from"]["reference"].clone(), p["to"]["reference"].clone()];
            refs.sort_by(|a, b| a.as_str().cmp(&b.as_str()));
            let mut row =
                json!({"obligation":o["id"],"role":role,"metric":o["metric"],"refs":refs});
            for (k, v) in p.as_object().unwrap() {
                row[k] = v.clone();
            }
            let result = (|| -> Result<()> {
                ensure!(
                    o["metric"] == "pad-centre-to-centre",
                    "unsupported locality metric"
                );
                ensure!(
                    p["from"]["net"] == p["to"]["net"],
                    "locality endpoints must declare the same net"
                );
                let centers = [resolve(fps, &p["from"])?, resolve(fps, &p["to"])?];
                let square = squared(&centers[0], &centers[1]);
                row["distance_squared_nm"] = if square.denom().is_one() {
                    exact::integer_value(square.numer())
                } else {
                    exact::fraction_record(&square)
                };
                row["distance_mm"] = json!(
                    square
                        .to_f64()
                        .context("distance outside float range")?
                        .sqrt()
                        / 1e6
                );
                row["geometry"] = json!("native copper-land centroid (before drill subtraction)");
                let authority = classify(p, &d["source_records"]);
                let floor = floor_inner(fps, p, &centers, &mut cache)
                    .unwrap_or_else(|e| json!({"status":"unknown","reason":e.to_string()}));
                row["constraint_provenance"] = authority.clone();
                row["manufacturing_floor"] = floor.clone();
                measured.push(row.clone());
                if authority["enforcement"] == "integrity" {
                    let mut r = row.clone();
                    r["kind"] = json!("source_record_integrity");
                    r["reason"] = authority["reason"].clone();
                    unknown.push(r);
                    return Ok(());
                }
                ensure!(
                    p.get("max_distance_nm").is_some()
                        && (p.get("citation").is_some() || authority["enforcement"] != "hard"),
                    "declared numeric limit and citation required"
                );
                if authority["enforcement"] == "feasibility"
                    && floor["status"] != "known"
                    && floor["reason"]
                        .as_str()
                        .is_some_and(|s| s.starts_with("captured JLCPCB numeric record"))
                {
                    bail!(
                        "manufacturer floor unavailable: {}",
                        floor["reason"].as_str().unwrap()
                    );
                }
                let limit = exact::integer(&p["max_distance_nm"])?;
                let below =
                    floor["status"] == "known" && limit < exact::integer(&floor["floor_nm"])?;
                let exceeded = square > Rat::from_integer(&limit * &limit);
                let mut r = row.clone();
                if (authority["enforcement"] == "hard" || authority["enforcement"] == "feasibility")
                    && below
                {
                    r["kind"] = json!("locality_infeasible");
                    defects.push(r);
                } else if authority["enforcement"] == "hard" {
                    if exceeded {
                        r["kind"] = json!("electrical_locality_distance");
                        defects.push(r);
                    }
                } else {
                    r["kind"] = json!("unsourced");
                    r["severity"] = json!("defect");
                    r["exceeded"] = json!(exceeded);
                    r["below_floor"] = json!(below);
                    advisories.push(r);
                }
                Ok(())
            })();
            if let Err(e) = result {
                row["kind"] = json!("electrical_locality_unavailable");
                row["reason"] = json!(e.to_string());
                unknown.push(row);
            }
        }
    }
    let status = if !defects.is_empty() {
        "defect"
    } else if !unknown.is_empty() {
        "unknown"
    } else if !advisories.is_empty() {
        "info"
    } else {
        "pass"
    };
    Ok(
        json!({"criterion":"electrical-locality/1","status":status,"measured":measured,"defects":defects,"unknown":unknown,"advisories":advisories,"declaration":d}),
    )
}

type EndpointKey = (String, String, Option<String>, String);
fn key(e: &Value) -> Result<EndpointKey> {
    Ok((
        e["reference"]
            .as_str()
            .context("reference required")?
            .to_owned(),
        e["pad"].as_str().context("pad required")?.to_owned(),
        e.get("uuid").and_then(Value::as_str).map(str::to_owned),
        e["net"].as_str().context("net required")?.to_owned(),
    ))
}
#[derive(Clone, Debug)]
pub struct LocalityPair {
    pub offset: [Rat; 2],
    pub target: [Rat; 2],
    pub limit: Int,
    pub involved: BTreeSet<String>,
    pub hard: bool,
}
#[derive(Clone, Debug, Default)]
pub struct CandidateLocality {
    pub pairs: Vec<LocalityPair>,
}
#[derive(Clone)]
struct Incident {
    own: Value,
    peer: Value,
    limit: Int,
    hard: bool,
}
#[derive(Clone, Default)]
pub struct PlacementIndex {
    pairs: BTreeMap<String, Vec<Incident>>,
    endpoints: BTreeMap<String, BTreeMap<EndpointKey, Value>>,
    positions: BTreeMap<String, BTreeMap<EndpointKey, [Rat; 2]>>,
}
impl PlacementIndex {
    pub fn new(document: &Value) -> Result<Self> {
        validate(document)?;
        let mut out = Self::default();
        for o in document["obligations"].as_array().unwrap() {
            for role in roles(o) {
                let p = &o[role];
                let limit = exact::integer(&p["max_distance_nm"])?;
                let hard = classify(p, &document["source_records"])["enforcement"] == "hard";
                for (own, peer) in [(&p["from"], &p["to"]), (&p["to"], &p["from"])] {
                    let r = own["reference"].as_str().unwrap().to_owned();
                    out.pairs.entry(r.clone()).or_default().push(Incident {
                        own: own.clone(),
                        peer: peer.clone(),
                        limit: limit.clone(),
                        hard,
                    });
                    out.endpoints
                        .entry(r)
                        .or_default()
                        .insert(key(own)?, own.clone());
                }
            }
        }
        Ok(out)
    }
    pub fn push(&mut self, fp: &Value) -> Result<()> {
        let r = fp["ref"].as_str().context("reference required")?;
        if let Some(es) = self.endpoints.get(r) {
            let mut positions = BTreeMap::new();
            for (k, e) in es {
                positions.insert(k.clone(), resolve(std::slice::from_ref(fp), e)?);
            }
            self.positions.insert(r.to_owned(), positions);
        }
        Ok(())
    }
    pub fn pop(&mut self, r: &str) {
        self.positions.remove(r);
    }
    pub fn contains(&self, r: &str) -> bool {
        self.pairs.contains_key(r)
    }
    pub fn prepare(&self, fp: &Value) -> Result<CandidateLocality> {
        let r = fp["ref"].as_str().context("reference required")?;
        let mut own = BTreeMap::new();
        let mut pairs = Vec::new();
        for p in self.pairs.get(r).into_iter().flatten() {
            let peer = p.peer["reference"].as_str().unwrap();
            let Some(positions) = self.positions.get(peer) else {
                continue;
            };
            let k = key(&p.own)?;
            if !own.contains_key(&k) {
                own.insert(k.clone(), resolve(std::slice::from_ref(fp), &p.own)?);
            }
            pairs.push(LocalityPair {
                offset: own[&k].clone(),
                target: positions[&key(&p.peer)?].clone(),
                limit: p.limit.clone(),
                involved: [r.to_owned(), peer.to_owned()].into_iter().collect(),
                hard: p.hard,
            });
        }
        Ok(CandidateLocality { pairs })
    }
}
#[derive(Debug)]
pub struct TargetBudgetExhausted;
impl std::fmt::Display for TargetBudgetExhausted {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "locality target budget exhausted")
    }
}
impl std::error::Error for TargetBudgetExhausted {}
impl CandidateLocality {
    pub fn constraint(&self, p: &Point) -> (BTreeSet<String>, Rat) {
        let mut blocked = BTreeSet::new();
        let mut cost = Rat::zero();
        for q in &self.pairs {
            let x = &q.offset[0] + Rat::from_integer(p.x.clone()) - &q.target[0];
            let y = &q.offset[1] + Rat::from_integer(p.y.clone()) - &q.target[1];
            let s = &x * &x + &y * &y;
            let lim = &q.limit * &q.limit;
            cost += &s / Rat::from_integer(lim.clone().max(Int::one()));
            if q.hard && s > Rat::from_integer(lim) {
                blocked.extend(q.involved.clone());
            }
        }
        (blocked, cost)
    }
    pub fn targets(&self) -> Result<Vec<Point>> {
        let mut points = BTreeSet::new();
        for q in &self.pairs {
            if !q.hard {
                continue;
            }
            let x = exact::round_even(&(&q.target[0] - &q.offset[0]));
            let y = exact::round_even(&(&q.target[1] - &q.offset[1]));
            for p in [
                Point {
                    x: x.clone(),
                    y: y.clone(),
                },
                Point {
                    x: &x - &q.limit,
                    y: y.clone(),
                },
                Point {
                    x: &x + &q.limit,
                    y: y.clone(),
                },
                Point {
                    x: x.clone(),
                    y: &y - &q.limit,
                },
                Point {
                    x,
                    y: &y + &q.limit,
                },
            ] {
                points.insert(p);
            }
            if points.len() > 256 {
                return Err(TargetBudgetExhausted.into());
            }
        }
        Ok(points.into_iter().collect())
    }
}
pub fn constraint(
    footprints: &[Value],
    proposed: &Value,
    document: &Value,
) -> Result<(BTreeSet<String>, Rat)> {
    let mut index = PlacementIndex::new(document)?;
    for fp in footprints {
        if fp["ref"] != proposed["ref"] {
            index.push(fp)?;
        }
    }
    let position = exact::point(&proposed["position_nm"])?;
    let at_origin = crate::footprint::transform_value(
        proposed,
        &json!([0, 0]),
        exact::rotation(&proposed["rotation_deg"])?,
    )?;
    Ok(index.prepare(&at_origin)?.constraint(&position))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn source_integrity_cannot_be_downgraded_to_an_unsourced_limit() {
        let p = json!({"max_distance_nm":1,"provenance":{"class":"owner","basis":"decision","decision_reference":{"path":"owner.txt","sha256":"0".repeat(64),"locator":"line 1","quote":"one nm","owner":"Human"}}});
        assert_eq!(
            classify(&p, &json!({"owner.txt":"one nm"}))["enforcement"],
            "integrity"
        );
        assert_eq!(classify(&p, &json!({}))["enforcement"], "advisory");
    }
    #[test]
    fn numeric_datasheet_derivation_is_exact_and_quote_bound() {
        let quote = "Maximum distance 1.25 mm.";
        let sha = format!("{:x}", Sha256::digest(quote));
        let mut p = json!({"max_distance_nm":1250000,"provenance":{"class":"datasheet","basis":"numeric maximum","derivation":{"kind":"numeric","value_nm":1250000,"source":{"path":"data.txt","sha256":sha,"locator":"table","quote":quote}}}});
        assert_eq!(
            classify(&p, &json!({"data.txt":quote}))["enforcement"],
            "hard"
        );
        p["max_distance_nm"] = json!(1250001);
        assert_eq!(
            classify(&p, &json!({"data.txt":quote}))["enforcement"],
            "advisory"
        );
    }
    #[test]
    fn agent_label_is_not_an_owner_decision() {
        let quote = "limit 1 nm";
        let sha = format!("{:x}", Sha256::digest(quote));
        let mut p = json!({"max_distance_nm":1,"provenance":{"class":"owner","basis":"decision","decision_reference":{"path":"owner.txt","sha256":sha,"locator":"line 1","quote":quote,"owner":"agent-524"}}});
        assert_eq!(
            classify(&p, &json!({"owner.txt":quote}))["enforcement"],
            "advisory"
        );
        p["provenance"]["decision_reference"]["owner"] = json!("Hal");
        assert_eq!(
            classify(&p, &json!({"owner.txt":quote}))["enforcement"],
            "hard"
        );
    }
    #[test]
    fn rational_cost_and_targets_preserve_ties_even_and_hardness() {
        let pair = LocalityPair {
            offset: [Rat::new(1.into(), 2.into()), Rat::zero()],
            target: [Rat::from_integer(2.into()), Rat::zero()],
            limit: 1.into(),
            involved: ["A".into(), "B".into()].into_iter().collect(),
            hard: true,
        };
        let c = CandidateLocality {
            pairs: vec![pair.clone()],
        };
        assert!(c.targets().unwrap().contains(&Point::new(2, 0)));
        let (blocked, cost) = c.constraint(&Point::zero());
        assert_eq!(cost, Rat::new(9.into(), 4.into()));
        assert_eq!(blocked.len(), 2);
        let soft = CandidateLocality {
            pairs: vec![LocalityPair {
                hard: false,
                ..pair
            }],
        };
        assert!(soft.targets().unwrap().is_empty());
        assert!(soft.constraint(&Point::zero()).0.is_empty());
        assert_eq!(soft.constraint(&Point::zero()).1, cost);
    }
    #[test]
    fn target_guard_refuses_without_truncating() {
        let pairs = (0..60)
            .map(|n| LocalityPair {
                offset: [Rat::zero(), Rat::zero()],
                target: [Rat::from_integer((n * 10).into()), Rat::zero()],
                limit: 1.into(),
                involved: BTreeSet::new(),
                hard: true,
            })
            .collect();
        assert!(
            CandidateLocality { pairs }
                .targets()
                .unwrap_err()
                .is::<TargetBudgetExhausted>()
        );
    }
    #[test]
    fn native_centroid_resolution_is_fractional_and_identity_bound() {
        let mut fp = json!({"ref":"A","pads":[{"number":"1","uuid":"00000000-0000-0000-0000-000000000001","net":"VCC","position_nm":[10,-10],"copper_centroid_offset_nm":[[1,3],[-1,2]]}]});
        let e = json!({"reference":"A","pad":"1","net":"VCC"});
        let p = resolve(std::slice::from_ref(&fp), &e).unwrap();
        assert_eq!(
            p,
            [
                Rat::new(31.into(), 3.into()),
                Rat::new((-21).into(), 2.into())
            ]
        );
        fp["pads"][0]["net"] = json!("GND");
        assert_eq!(
            resolve(&[fp], &e).unwrap_err().to_string(),
            "native pad net differs from declaration"
        );
    }
}
