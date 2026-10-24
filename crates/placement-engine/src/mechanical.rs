//! Conservative source-bound mechanical projections. Python authenticates the
//! accepted envelope and constructs the binding; native search rechecks identity
//! and current geometry without delegating planning or granting source authority.
use crate::blocks::{digest, keys, rows, text};
use crate::exact::{
    Point, Rat, decimal, integer, point, point_value, rational, rational_value, rotate,
};
use crate::geometry::{courtyard_bounds, intersection_area, simple_polygon};
use anyhow::{Context, Result, ensure};
use num_traits::{ToPrimitive, Zero};
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};

const SCHEMA: &str = "agent-ee.mechanical-reservations/1";
const BINDING_SCHEMA: &str = "agent-ee.mechanical-reservation-binding/1";
const SELECTION_SCHEMA: &str = "agent-ee.mechanical-block-selection/1";

fn sha(value: &Value) -> Result<()> {
    let value = text(value)?;
    ensure!(
        value.len() == 64
            && value
                .bytes()
                .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c)),
        "mechanical SHA-256 required"
    );
    Ok(())
}

fn relative(value: &Value) -> Result<()> {
    let value = text(value)?;
    ensure!(
        !value.starts_with('/')
            && value
                .split('/')
                .all(|p| !p.is_empty() && p != "." && p != ".."),
        "mechanical source path must be a captured relative path"
    );
    Ok(())
}

fn strings(value: &Value, choices: &[&str], empty: bool) -> Result<Vec<String>> {
    let values = rows(value)?;
    ensure!(
        empty || !values.is_empty(),
        "mechanical list must not be empty"
    );
    let result = values
        .iter()
        .map(|v| text(v).map(str::to_owned))
        .collect::<Result<Vec<_>>>()?;
    ensure!(
        result.iter().collect::<BTreeSet<_>>().len() == result.len(),
        "duplicate mechanical list value"
    );
    ensure!(
        choices.is_empty() || result.iter().all(|s| choices.contains(&s.as_str())),
        "unsupported mechanical list value"
    );
    Ok(result)
}

fn angle(value: &Value) -> Result<i64> {
    let value = decimal(value)?;
    ensure!(
        value.is_integer(),
        "mechanical frame requires an exact native quarter-turn angle"
    );
    let value = value
        .to_integer()
        .to_i64()
        .context("mechanical angle out of range")?;
    ensure!(
        value % 90 == 0,
        "mechanical frame requires an exact native quarter-turn angle"
    );
    Ok(value.rem_euclid(360))
}

fn document(value: &Value) -> Result<()> {
    keys(
        value,
        &[
            "schema",
            "original_board_path",
            "original_board_sha256",
            "source",
            "reservations",
        ],
        &[],
    )?;
    ensure!(
        value["schema"] == SCHEMA,
        "unsupported mechanical reservation schema"
    );
    sha(&value["original_board_sha256"])?;
    relative(&value["original_board_path"])?;
    let source = &value["source"];
    keys(
        source,
        &["path", "sha256", "locator", "classification", "author"],
        &[],
    )?;
    relative(&source["path"])?;
    sha(&source["sha256"])?;
    text(&source["locator"])?;
    text(&source["author"])?;
    ensure!(
        source["classification"] == "engineering",
        "mechanical choices must retain engineering classification"
    );
    let entries = rows(&value["reservations"])?;
    ensure!(entries.len() <= 1024, "too many mechanical reservations");
    let mut ids = BTreeSet::new();
    for row in entries {
        keys(
            row,
            &[
                "id",
                "required",
                "kind",
                "frame",
                "geometry",
                "faces",
                "height_nm",
                "phases",
                "self_exclusion",
                "coverage",
                "uncertainty",
            ],
            &["owner"],
        )?;
        ensure!(
            ids.insert(text(&row["id"])?),
            "duplicate mechanical reservation id"
        );
        ensure!(
            row["required"].is_boolean() && row["self_exclusion"].is_boolean(),
            "mechanical booleans required"
        );
        ensure!(
            matches!(row["kind"].as_str(), Some("solid" | "access")),
            "unsupported mechanical kind"
        );
        let frame = text(&row["frame"])?;
        if frame == "board" {
            ensure!(
                row.get("owner").is_none() && row["self_exclusion"] == false,
                "fixed mechanical owner invalid"
            );
            strings(&row["faces"], &["F", "B"], false)?;
        } else {
            ensure!(
                frame == "footprint" || frame == "pad",
                "unsupported mechanical frame"
            );
            let required = if frame == "pad" {
                vec!["ref", "uuid", "pad"]
            } else {
                vec!["ref", "uuid"]
            };
            keys(&row["owner"], &required, &[])?;
            text(&row["owner"]["ref"])?;
            text(&row["owner"]["uuid"])?;
            if frame == "pad" {
                keys(&row["owner"]["pad"], &["number", "uuid"], &[])?;
                text(&row["owner"]["pad"]["number"])?;
                text(&row["owner"]["pad"]["uuid"])?;
            }
            strings(&row["faces"], &["same", "opposite"], false)?;
        }
        keys(&row["geometry"], &["polygons_nm"], &[])?;
        if !row["geometry"]["polygons_nm"].is_null() {
            let polygons = rows(&row["geometry"]["polygons_nm"])?;
            ensure!(
                !polygons.is_empty() && polygons.len() <= 64,
                "mechanical polygons missing or excessive"
            );
            for polygon in polygons {
                simple_polygon(polygon)?;
            }
        }
        if !row["height_nm"].is_null() {
            let interval = rows(&row["height_nm"])?;
            ensure!(interval.len() == 2, "mechanical height interval invalid");
            let low = integer(&interval[0])?;
            let high = integer(&interval[1])?;
            ensure!(
                low >= 0.into() && low < high,
                "mechanical height interval invalid"
            );
        }
        strings(&row["phases"], &[], false)?;
        strings(&row["uncertainty"], &[], true)?;
        let coverage = &row["coverage"];
        keys(coverage, &["projection", "height", "access", "basis"], &[])?;
        ensure!(
            coverage["projection"] == "conservative-per-face",
            "unsupported mechanical projection coverage"
        );
        for key in ["height", "access"] {
            ensure!(
                matches!(coverage[key].as_str(), Some("covered" | "unknown")),
                "unsupported mechanical coverage"
            );
        }
        text(&coverage["basis"])?;
    }
    Ok(())
}

// Mirror footprint_library._custom_primitives: only bounded, exact native
// primitives qualify. Numeric tokens are compared as nanometres, not spellings.
fn custom_number(value: &Value) -> Result<crate::exact::Int> {
    let token = value.as_str().context("invalid custom-pad numeric token")?;
    ensure!(token.len() <= 128, "invalid custom-pad numeric token");
    // Decimal (the Python qualifier) accepts whitespace and underscores.
    // Validate the finite decimal grammar before using exact::decimal, whose
    // internal callers already supply syntactically qualified numbers.
    let token = token.trim().replace('_', "");
    static NUMBER: std::sync::LazyLock<regex::Regex> = std::sync::LazyLock::new(|| {
        regex::Regex::new(r"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$").unwrap()
    });
    ensure!(NUMBER.is_match(&token), "invalid custom-pad numeric token");
    crate::exact::nanometres(&json!(token))
}

fn custom_point(value: &Value, tag: &str) -> Result<Point> {
    let values = rows(value)?;
    ensure!(
        values.len() == 3 && values[0] == tag,
        "unsupported custom-pad coordinate"
    );
    Ok(Point {
        x: custom_number(&values[1])?,
        y: custom_number(&values[2])?,
    })
}

fn ceil_hypot(p: &Point) -> crate::exact::Int {
    let square = &p.x * &p.x + &p.y * &p.y;
    let root = crate::exact::isqrt(&square);
    if &root * &root < square {
        root + 1
    } else {
        root
    }
}

fn custom_primitives(value: &Value, reflected: bool) -> Result<Value> {
    use num_traits::Signed;
    let primitives = rows(value)?;
    ensure!(
        (1..=128).contains(&primitives.len()),
        "custom pad requires 1..128 supported primitives"
    );
    let limit = crate::exact::Int::from(1_000_000_000);
    let mut result = Vec::new();
    for primitive in primitives {
        let nodes = rows(primitive)?;
        let kind = text(nodes.first().context("invalid custom-pad primitive")?)?;
        let expected: &[&str] = match kind {
            "gr_poly" => &["pts", "width", "fill"],
            "gr_rect" => &["start", "end", "width", "fill"],
            "gr_circle" => &["center", "end", "width", "fill"],
            "gr_line" => &["start", "end", "width"],
            _ => anyhow::bail!("unsupported custom-pad primitive/options"),
        };
        let mut fields = BTreeMap::new();
        for node in &nodes[1..] {
            let items = rows(node)?;
            let name = text(items.first().context("invalid custom-pad field")?)?;
            ensure!(
                expected.contains(&name) && fields.insert(name, node).is_none(),
                "unsupported custom-pad primitive/options"
            );
        }
        ensure!(
            fields.len() == expected.len(),
            "unsupported custom-pad primitive/options"
        );
        let width_node = rows(fields["width"])?;
        ensure!(width_node.len() == 2, "unsupported custom-pad width");
        let width = custom_number(&width_node[1])?;
        let fill = if kind == "gr_line" {
            "no"
        } else {
            let fill_node = rows(fields["fill"])?;
            ensure!(fill_node.len() == 2, "unsupported custom-pad fill");
            text(&fill_node[1])?
        };
        ensure!(
            width <= limit
                && width >= crate::exact::Int::zero()
                && ["yes", "no"].contains(&fill)
                && (fill != "no" || !width.is_zero()),
            "unsupported custom-pad primitive fill/width"
        );
        let points = if kind == "gr_poly" {
            let vertices = rows(fields["pts"])?;
            ensure!(
                (4..=1025).contains(&vertices.len()),
                "custom polygon requires 3..1024 vertices"
            );
            let points = vertices[1..]
                .iter()
                .map(|v| custom_point(v, "xy"))
                .collect::<Result<Vec<_>>>()?;
            simple_polygon(&json!(points.iter().map(point_value).collect::<Vec<_>>()))?;
            points
        } else {
            let first = if kind == "gr_circle" {
                "center"
            } else {
                "start"
            };
            let start = custom_point(fields[first], first)?;
            let end = custom_point(fields["end"], "end")?;
            ensure!(
                start != end && (kind != "gr_rect" || (start.x != end.x && start.y != end.y)),
                "degenerate custom-pad primitive"
            );
            vec![start, end]
        };
        ensure!(
            points
                .iter()
                .all(|p| p.x.abs() <= limit && p.y.abs() <= limit),
            "custom-pad primitive coordinate exceeds +/-1000 mm"
        );
        let (extent, reach) = if kind == "gr_circle" {
            let radius = ceil_hypot(&points[0].minus(&points[1]));
            (
                points[0].x.abs().max(points[0].y.abs()) + &radius,
                ceil_hypot(&points[0]) + radius,
            )
        } else {
            let extent = points
                .iter()
                .flat_map(|p| [p.x.abs(), p.y.abs()])
                .max()
                .unwrap();
            let corners = if kind == "gr_rect" {
                [&points[0].x, &points[1].x]
                    .into_iter()
                    .flat_map(|x| {
                        [&points[0].y, &points[1].y]
                            .into_iter()
                            .map(move |y| Point {
                                x: x.clone(),
                                y: y.clone(),
                            })
                    })
                    .collect()
            } else {
                points.clone()
            };
            (extent, corners.iter().map(ceil_hypot).max().unwrap())
        };
        ensure!(
            extent * 2 + &width <= &limit * 2 && reach * 2 + &width <= &limit * 2,
            "custom-pad copper reach exceeds 1000 mm"
        );
        let mut normalized = primitive.clone();
        for node in normalized.as_array_mut().unwrap().iter_mut().skip(1) {
            let tag = node[0].as_str().unwrap().to_owned();
            if tag == "width" {
                node[1] = crate::exact::integer_value(&width);
            } else if tag == "pts" {
                for vertex in node.as_array_mut().unwrap().iter_mut().skip(1) {
                    let mut p = custom_point(vertex, "xy")?;
                    if reflected {
                        p.y = -p.y;
                    }
                    *vertex = json!([
                        "xy",
                        crate::exact::integer_value(&p.x),
                        crate::exact::integer_value(&p.y)
                    ]);
                }
            } else if ["start", "end", "center"].contains(&tag.as_str()) {
                let mut p = custom_point(node, &tag)?;
                if reflected {
                    p.y = -p.y;
                }
                *node = json!([
                    tag,
                    crate::exact::integer_value(&p.x),
                    crate::exact::integer_value(&p.y)
                ]);
            }
        }
        if kind == "gr_rect" {
            let nodes = normalized.as_array_mut().unwrap();
            let a = nodes.iter().position(|n| n[0] == "start").unwrap();
            let b = nodes.iter().position(|n| n[0] == "end").unwrap();
            for axis in [1, 2] {
                if integer(&nodes[a][axis])? > integer(&nodes[b][axis])? {
                    let temp = nodes[a][axis].clone();
                    nodes[a][axis] = nodes[b][axis].clone();
                    nodes[b][axis] = temp;
                }
            }
        }
        result.push(normalized);
    }
    Ok(json!(result))
}

fn custom_geometry(pad: &Value) -> Result<&Value> {
    let geometry = &pad["custom_geometry"];
    ensure!(
        pad["shape"] == 6 && geometry.is_object() && geometry["status"] == "proven",
        "mechanical custom-pad geometry is not native qualified"
    );
    keys(
        geometry,
        &[
            "status",
            "anchor",
            "primitives",
            "merged_polygons_nm",
            "bbox_nm",
        ],
        &[],
    )?;
    ensure!(
        matches!(geometry["anchor"].as_i64(), Some(0 | 1)),
        "mechanical custom-pad anchor is unsupported"
    );
    custom_primitives(&geometry["primitives"], false)?;
    let polygons = rows(&geometry["merged_polygons_nm"])?;
    ensure!(
        (1..=64).contains(&polygons.len()),
        "mechanical custom-pad merged copper is missing"
    );
    for polygon in polygons {
        ensure!(
            (3..=1024).contains(&rows(polygon)?.len()),
            "mechanical custom-pad merged contour is invalid"
        );
        simple_polygon(polygon)?;
    }
    let bbox = rows(&geometry["bbox_nm"])?;
    ensure!(bbox.len() == 4, "mechanical custom-pad bounds are invalid");
    let b = bbox.iter().map(integer).collect::<Result<Vec<_>>>()?;
    ensure!(
        b[0] < b[2] && b[1] < b[3],
        "mechanical custom-pad bounds are invalid"
    );
    Ok(geometry)
}

fn custom_contours(value: &Value, reflected: bool) -> Result<Vec<Vec<Point>>> {
    let mut result = Vec::new();
    for polygon in rows(value)? {
        let mut points = rows(polygon)?
            .iter()
            .map(point)
            .collect::<Result<Vec<_>>>()?;
        if reflected {
            for p in &mut points {
                p.y = -&p.y;
            }
        }
        let start = points.iter().enumerate().min_by_key(|(_, p)| *p).unwrap().0;
        points.rotate_left(start);
        let mut reverse = points.clone();
        reverse[1..].reverse();
        result.push(points.min(reverse));
    }
    result.sort();
    Ok(result)
}

fn custom_follows(
    before: &Value,
    after: &Value,
    source: &Value,
    current: &Value,
    flipped: bool,
) -> Result<()> {
    let old = custom_geometry(before)?;
    let new = custom_geometry(after)?;
    let b = rows(&old["bbox_nm"])?;
    let source_position = point(&source["position_nm"])?;
    let current_position = point(&current["position_nm"])?;
    let mut corners = Vec::new();
    for x in [0, 2] {
        for y in [1, 3] {
            let local = rotate(
                &Point {
                    x: integer(&b[x])?,
                    y: integer(&b[y])?,
                }
                .minus(&source_position),
                -angle(&source["rotation_deg"])? / 90,
            );
            corners.push(moved(
                &local,
                &current_position,
                angle(&current["rotation_deg"])?,
                flipped,
            ));
        }
    }
    let expected_box = json!([
        crate::exact::integer_value(corners.iter().map(|p| &p.x).min().unwrap()),
        crate::exact::integer_value(corners.iter().map(|p| &p.y).min().unwrap()),
        crate::exact::integer_value(corners.iter().map(|p| &p.x).max().unwrap()),
        crate::exact::integer_value(corners.iter().map(|p| &p.y).max().unwrap())
    ]);
    ensure!(
        old["anchor"] == new["anchor"]
            && custom_primitives(&old["primitives"], flipped)?
                == custom_primitives(&new["primitives"], false)?
            && custom_contours(&old["merged_polygons_nm"], flipped)?
                == custom_contours(&new["merged_polygons_nm"], false)?
            && expected_box == new["bbox_nm"],
        "mechanical native custom-pad geometry changed independently of owner"
    );
    Ok(())
}

fn native_owner(fp: &Value) -> Result<()> {
    text(&fp["ref"])?;
    text(&fp["uuid"])?;
    ensure!(
        matches!(fp["side"].as_str(), Some("F" | "B")),
        "mechanical footprint side is unknown"
    );
    point(&fp["position_nm"])?;
    angle(&fp["rotation_deg"])?;
    let mut seen = BTreeSet::new();
    for pad in rows(&fp["pads"])? {
        if pad["shape"] == 6 || pad.get("custom_geometry").is_some() {
            custom_geometry(pad)?;
        }
        ensure!(
            seen.insert(text(&pad["uuid"])?),
            "mechanical pad identity missing or ambiguous"
        );
        ensure!(
            pad["number"].is_string() && pad["net"].is_string(),
            "mechanical pad identity missing or ambiguous"
        );
        point(&pad["position_nm"])?;
        angle(&pad["rotation_deg"])?;
        for key in ["copper_centroid_offset_nm", "copper_land_offset_nm"] {
            if pad.get(key).is_some() {
                offset(pad, key)?;
            }
        }
    }
    Ok(())
}

fn binding(value: &Value) -> Result<BTreeMap<String, &Value>> {
    keys(
        value,
        &[
            "schema",
            "document",
            "document_sha256",
            "canonical_document_sha256",
            "source_board_sha256",
            "owners",
        ],
        &["selection"],
    )?;
    ensure!(
        value["schema"] == BINDING_SCHEMA,
        "unsupported mechanical binding schema"
    );
    document(&value["document"])?;
    sha(&value["document_sha256"])?;
    ensure!(
        digest(&value["document"]) == text(&value["canonical_document_sha256"])?
            && value["source_board_sha256"] == value["document"]["original_board_sha256"],
        "mechanical binding document identity changed"
    );
    let mut owners = BTreeMap::new();
    for fp in rows(&value["owners"])? {
        native_owner(fp)?;
        ensure!(
            owners.insert(text(&fp["uuid"])?.to_owned(), fp).is_none(),
            "duplicate mechanical bound owner"
        );
    }
    let expected = rows(&value["document"]["reservations"])?
        .iter()
        .filter_map(|row| row.get("owner"))
        .map(|owner| text(&owner["uuid"]).map(str::to_owned))
        .collect::<Result<BTreeSet<_>>>()?;
    ensure!(
        owners.keys().cloned().collect::<BTreeSet<_>>() == expected,
        "mechanical bound owner coverage differs"
    );
    if let Some(selection) = value.get("selection") {
        keys(selection, &["schema", "references", "reservation_ids"], &[])?;
        let refs = strings(&selection["references"], &[], true)?;
        let sorted: BTreeSet<_> = refs.iter().cloned().collect();
        let expected = selected_ids(&value["document"], &sorted)?;
        ensure!(
            selection["schema"] == SELECTION_SCHEMA
                && refs == sorted.into_iter().collect::<Vec<_>>()
                && selection["reservation_ids"] == json!(expected),
            "mechanical block selection must include every attached member reservation"
        );
    }
    Ok(owners)
}

fn selected_ids(document: &Value, references: &BTreeSet<String>) -> Result<Vec<String>> {
    let mut ids = Vec::new();
    for row in rows(&document["reservations"])? {
        if row
            .get("owner")
            .and_then(|o| o["ref"].as_str())
            .is_some_and(|r| references.contains(r))
        {
            ids.push(text(&row["id"])?.to_owned());
        }
    }
    ids.sort();
    Ok(ids)
}

/// Restrict evaluation to attached block members while retaining all source evidence.
pub fn for_block(value: &Value, references: &BTreeSet<String>) -> Result<Value> {
    binding(value)?;
    if let Some(selection) = value.get("selection") {
        let prior: BTreeSet<_> = strings(&selection["references"], &[], true)?
            .into_iter()
            .collect();
        ensure!(
            references.is_subset(&prior),
            "mechanical block selection cannot widen a prior selection"
        );
    }
    let mut result = value.clone();
    result["selection"] = json!({"schema":SELECTION_SCHEMA,"references":references,
        "reservation_ids":selected_ids(&value["document"], references)?});
    Ok(result)
}

fn moved(p: &Point, origin: &Point, rotation: i64, flipped: bool) -> Point {
    let local = Point {
        x: p.x.clone(),
        y: if flipped { -&p.y } else { p.y.clone() },
    };
    rotate(&local, rotation / 90).plus(origin)
}

fn offset(pad: &Value, key: &str) -> Result<[Rat; 2]> {
    if key == "copper_land_offset_nm" {
        let p = point(&pad[key])?;
        return Ok([Rat::from_integer(p.x), Rat::from_integer(p.y)]);
    }
    let pair = rows(&pad[key])?;
    ensure!(
        pair.len() == 2,
        "mechanical copper centroid offset must be exact rational nanometres"
    );
    Ok([rational(&pair[0])?, rational(&pair[1])?])
}

fn rotate_offset(mut p: [Rat; 2], turns: i64) -> [Rat; 2] {
    for _ in 0..turns.rem_euclid(4) {
        p = [p[1].clone(), -&p[0]];
    }
    p
}

fn current_owner(source: &Value, current: &Value) -> Result<()> {
    native_owner(current)?;
    for key in ["ref", "uuid", "fpid", "value"] {
        ensure!(
            source.get(key) == current.get(key),
            "mechanical native owner identity changed"
        );
    }
    let old = rows(&source["pads"])?
        .iter()
        .map(|p| Ok((text(&p["uuid"])?, p)))
        .collect::<Result<BTreeMap<_, _>>>()?;
    let new = rows(&current["pads"])?
        .iter()
        .map(|p| Ok((text(&p["uuid"])?, p)))
        .collect::<Result<BTreeMap<_, _>>>()?;
    ensure!(
        old.keys().collect::<Vec<_>>() == new.keys().collect::<Vec<_>>(),
        "mechanical native pad UUID inventory changed"
    );
    let flipped = current["side"] != source["side"];
    let source_position = point(&source["position_nm"])?;
    let current_position = point(&current["position_nm"])?;
    let source_angle = angle(&source["rotation_deg"])?;
    let current_angle = angle(&current["rotation_deg"])?;
    for (uuid, before) in old {
        let after = new[uuid];
        for key in [
            "number",
            "net",
            "shape",
            "size_nm",
            "drill_size_nm",
            "attribute",
            "copper_centroid_reason",
        ] {
            ensure!(
                before.get(key) == after.get(key),
                "mechanical native pad signature changed"
            );
        }
        if let Some(layers) = before.get("layers") {
            let mut expected = strings(layers, &[], true)?
                .into_iter()
                .map(|layer| {
                    if flipped {
                        if let Some(tail) = layer.strip_prefix("F.") {
                            return format!("B.{tail}");
                        }
                        if let Some(tail) = layer.strip_prefix("B.") {
                            return format!("F.{tail}");
                        }
                    }
                    layer
                })
                .collect::<Vec<_>>();
            expected.sort();
            ensure!(
                json!(expected) == after["layers"],
                "mechanical native pad layers changed"
            );
        }
        let local = rotate(
            &point(&before["position_nm"])?.minus(&source_position),
            -source_angle / 90,
        );
        let expected = moved(&local, &current_position, current_angle, flipped);
        let relative = (angle(&before["rotation_deg"])? - source_angle).rem_euclid(360);
        let expected_angle =
            (current_angle + if flipped { -relative } else { relative }).rem_euclid(360);
        ensure!(
            expected == point(&after["position_nm"])?
                && expected_angle == angle(&after["rotation_deg"])?,
            "mechanical native pad pose changed independently of owner"
        );
        if before["shape"] == 6 {
            custom_follows(before, after, source, current, flipped)?;
        }
        for key in ["copper_centroid_offset_nm", "copper_land_offset_nm"] {
            ensure!(
                before.get(key).is_some() == after.get(key).is_some(),
                "mechanical native pad copper offset coverage changed"
            );
            if before.get(key).is_some() {
                let mut local = rotate_offset(offset(before, key)?, -source_angle / 90);
                if flipped {
                    local[1] = -&local[1];
                }
                let expected = rotate_offset(local, current_angle / 90);
                ensure!(
                    expected == offset(after, key)?,
                    "mechanical native pad copper offset changed independently of owner"
                );
            }
        }
    }
    Ok(())
}

fn add_issue(result: &mut Value, required: bool, issue: Value) {
    result[if required { "unknown" } else { "advisories" }]
        .as_array_mut()
        .unwrap()
        .push(issue);
}

/// Evaluate all declared phases. Initial source authentication is exclusively at
/// the accepted-envelope boundary; partial captures contain placed members only.
pub fn grade(capture: &Value, bound: &Value, partial: bool) -> Result<Value> {
    PreparedMechanical::new(bound, capture)?.grade(capture, partial)
}

/// Owned, immutable qualification. No reference aliases the caller's JSON.
pub struct PreparedMechanical {
    bound: Value,
    owners: BTreeMap<String, Value>,
    entries: Vec<Value>,
    phases: BTreeSet<String>,
    identity: String,
}

impl PreparedMechanical {
    pub fn new(bound: &Value, capture: &Value) -> Result<Self> {
    let owners = binding(bound)?;
    let doc = &bound["document"];
    let selected = bound
        .get("selection")
        .map(|s| strings(&s["reservation_ids"], &[], true))
        .transpose()?;
    let entries: Vec<_> = rows(&doc["reservations"])?
        .iter()
        .filter(|r| {
            selected.as_ref().is_none_or(|ids| {
                r["id"]
                    .as_str()
                    .is_some_and(|id| ids.iter().any(|s| s == id))
            })
        })
        .cloned()
        .collect();
    let phases = entries
        .iter()
        .map(|r| strings(&r["phases"], &[], false))
        .collect::<Result<Vec<_>>>()?
        .into_iter()
        .flatten()
        .collect::<BTreeSet<_>>();
        Ok(Self {
            bound: bound.clone(),
            owners: owners.into_iter().map(|(id, fp)| (id, fp.clone())).collect(),
            entries,
            phases,
            identity: digest(&json!({"binding":bound, "capture":capture})),
        })
    }

    pub fn identity(&self) -> &str {
        &self.identity
    }

    pub fn grade(&self, capture: &Value, partial: bool) -> Result<Value> {
    let owners = &self.owners;
    let doc = &self.bound["document"];
    let entries = &self.entries;
    let phases = &self.phases;
    let mut result = json!({"schema":"agent-ee.mechanical-reservation-grade/1", "phase":null,
        "phases":phases, "reservations":[], "unknown":[], "advisories":[], "status":"pass", "defects":[]});
    let capture_check = if partial && capture["footprints"] == json!([]) {
        if capture["schema"] == "agent-ee.placement-probe/1" {
            Ok(())
        } else {
            Err(anyhow::anyhow!("invalid partial mechanical capture"))
        }
    } else {
        crate::footprint::validate_capture(capture)
    };
    if let Err(error) = capture_check {
        add_issue(
            &mut result,
            true,
            json!({"reservation":null,"kind":"capture","reason":error.to_string()}),
        );
        result["status"] = json!("unknown");
        return Ok(result);
    }
    let current = rows(&capture["footprints"])?
        .iter()
        .map(|fp| Ok((text(&fp["uuid"])?.to_owned(), fp)))
        .collect::<Result<BTreeMap<_, _>>>()?;
    let mut verified: BTreeMap<String, std::result::Result<(), String>> = BTreeMap::new();
    for row in entries {
        let mut problems: Vec<String> = Vec::new();
        if row["geometry"]["polygons_nm"].is_null() {
            problems.push("geometry is unresolved".into());
        }
        if row["height_nm"].is_null() || row["coverage"]["height"] != "covered" {
            problems.push("height coverage is unresolved".into());
        }
        if row["coverage"]["access"] != "covered" {
            problems.push("access coverage is unresolved".into());
        }
        let mut origin = Point::zero();
        let mut rotation = 0;
        let mut flipped = false;
        let mut native = None;
        if let Some(owner) = row.get("owner") {
            let uuid = text(&owner["uuid"])?;
            let source = &owners[uuid];
            if source["ref"] != owner["ref"] {
                problems.push("bound owner reference differs".into());
            } else if let Some(fp) = current.get(uuid) {
                let verification = verified
                    .entry(uuid.to_owned())
                    .or_insert_with(|| current_owner(source, fp).map_err(|e| e.to_string()));
                if let Err(error) = verification {
                    problems.push(error.clone());
                } else {
                    native = Some(*fp);
                    let frame = if row["frame"] == "pad" {
                        let matches = rows(&fp["pads"])?
                            .iter()
                            .filter(|p| {
                                p["uuid"] == owner["pad"]["uuid"]
                                    && p["number"] == owner["pad"]["number"]
                            })
                            .collect::<Vec<_>>();
                        if matches.len() != 1 {
                            problems.push("mechanical pad frame is missing or stale".into());
                            None
                        } else {
                            Some(matches[0])
                        }
                    } else {
                        Some(*fp)
                    };
                    if let Some(frame) = frame {
                        origin = point(&frame["position_nm"])?;
                        rotation = angle(&frame["rotation_deg"])?;
                        flipped = fp["side"] != source["side"];
                    }
                }
            } else {
                let stale_ref = current.values().any(|fp| fp["ref"] == owner["ref"]);
                if partial && problems.is_empty() && !stale_ref {
                    continue;
                }
                problems.push("required native owner is missing".into());
            }
        }
        if !problems.is_empty() {
            add_issue(
                &mut result,
                row["required"] == true,
                json!({"reservation":row["id"],"kind":"unresolved","reason":problems.join("; ")}),
            );
            continue;
        }
        let faces = strings(&row["faces"], &[], false)?
            .into_iter()
            .map(|face| {
                if let Some(fp) = native {
                    if face == "same" {
                        fp["side"].clone()
                    } else if fp["side"] == "F" {
                        json!("B")
                    } else {
                        json!("F")
                    }
                } else {
                    json!(face)
                }
            })
            .collect::<Vec<_>>();
        let polygons = rows(&row["geometry"]["polygons_nm"])?
            .iter()
            .map(|polygon| {
                rows(polygon)?
                    .iter()
                    .map(|p| Ok(point_value(&moved(&point(p)?, &origin, rotation, flipped))))
                    .collect::<Result<Vec<_>>>()
            })
            .collect::<Result<Vec<_>>>()?;
        result["reservations"].as_array_mut().unwrap().push(json!({"id":row["id"],"kind":row["kind"],
            "required":row["required"],"owner_uuid":row["owner"]["uuid"],"owner_ref":row["owner"]["ref"],
            "faces":faces,"polygons_nm":polygons,"height_nm":row["height_nm"],"phases":row["phases"],
            "self_exclusion":row["self_exclusion"],"source":doc["source"],"coverage":row["coverage"],
            "uncertainty":row["uncertainty"]}));
    }
    let projected = result["reservations"].as_array().unwrap().clone();
    let mut footprints = rows(&capture["footprints"])?.iter().collect::<Vec<_>>();
    footprints.sort_by(|a, b| a["ref"].as_str().cmp(&b["ref"].as_str()));
    for row in &projected {
        let polygons = rows(&row["polygons_nm"])?
            .iter()
            .map(simple_polygon)
            .collect::<Result<Vec<_>>>()?;
        for fp in &footprints {
            if row["self_exclusion"] == true && fp["uuid"] == row["owner_uuid"] {
                continue;
            }
            for side in strings(&row["faces"], &[], false)? {
                let record = &fp["courtyards"][&side];
                if record.is_null() && fp["side"] != side {
                    continue;
                }
                match courtyard_bounds(record) {
                    Err(error) => add_issue(
                        &mut result,
                        row["required"] == true,
                        json!({"reservation":row["id"],
                        "kind":"courtyard","ref":fp["ref"],"side":side,"reason":error.to_string()}),
                    ),
                    Ok((outer, inner)) => {
                        let inner_area: Rat = polygons
                            .iter()
                            .flat_map(|a| inner.iter().map(move |b| intersection_area(a, b)))
                            .sum();
                        let outer_area: Rat = polygons
                            .iter()
                            .flat_map(|a| outer.iter().map(move |b| intersection_area(a, b)))
                            .sum();
                        if !inner_area.is_zero() {
                            result[if row["required"] == true {"defects"} else {"advisories"}].as_array_mut().unwrap().push(json!({
                                "kind":"mechanical_reservation_overlap","reservation":row["id"],"ref":fp["ref"],"uuid":fp["uuid"],
                                "side":side,"overlap_area_nm2":rational_value(&inner_area)}));
                        } else if !outer_area.is_zero() {
                            add_issue(
                                &mut result,
                                row["required"] == true,
                                json!({"reservation":row["id"],"kind":"courtyard_approximation",
                                "ref":fp["ref"],"side":side,"reason":"mechanical overlap lies only between native curve bounds"}),
                            );
                        }
                    }
                }
            }
        }
    }
    for (index, left) in projected.iter().enumerate() {
        for right in &projected[index + 1..] {
            if left["kind"] == "access" && right["kind"] == "access" {
                continue;
            }
            if !left["owner_uuid"].is_null()
                && left["owner_uuid"] == right["owner_uuid"]
                && left["self_exclusion"] == true
                && right["self_exclusion"] == true
            {
                continue;
            }
            let left_phases = strings(&left["phases"], &[], false)?
                .into_iter()
                .collect::<BTreeSet<_>>();
            let right_phases = strings(&right["phases"], &[], false)?
                .into_iter()
                .collect::<BTreeSet<_>>();
            let phases = left_phases
                .intersection(&right_phases)
                .cloned()
                .collect::<Vec<_>>();
            if phases.is_empty() {
                continue;
            }
            let left_faces = strings(&left["faces"], &[], false)?
                .into_iter()
                .collect::<BTreeSet<_>>();
            let right_faces = strings(&right["faces"], &[], false)?
                .into_iter()
                .collect::<BTreeSet<_>>();
            let a = rows(&left["polygons_nm"])?
                .iter()
                .map(simple_polygon)
                .collect::<Result<Vec<_>>>()?;
            let b = rows(&right["polygons_nm"])?
                .iter()
                .map(simple_polygon)
                .collect::<Result<Vec<_>>>()?;
            for side in left_faces.intersection(&right_faces) {
                let area: Rat = a
                    .iter()
                    .flat_map(|x| b.iter().map(move |y| intersection_area(x, y)))
                    .sum();
                if !area.is_zero() {
                    let required = left["required"] == true || right["required"] == true;
                    result[if required { "defects" } else { "advisories" }]
                        .as_array_mut()
                        .unwrap()
                        .push(json!({
                        "kind":"mechanical_reservation_pair_overlap","reservation":left["id"],
                        "other_reservation":right["id"],"side":side,"phases":phases,
                        "overlap_area_nm2":rational_value(&area)}));
                }
            }
        }
    }
    result["status"] = json!(if !rows(&result["defects"])?.is_empty() {
        "defect"
    } else if !rows(&result["unknown"])?.is_empty() {
        "unknown"
    } else {
        "pass"
    });
    Ok(result)
}
}

#[cfg(test)]
mod tests {
    use super::*;

    fn footprint(reference: &str, x: i64) -> Value {
        json!({"ref":reference,"uuid":format!("fp-{reference}"),"side":"F", "position_nm":[x,0],
            "rotation_deg":0,"pads":[{"uuid":format!("pad-{reference}"),"number":"1","net":"N",
                "position_nm":[x+2,3],"rotation_deg":0}],
            "courtyards":{"F":{"status":"proven","polygons":[[[x-1,-1],[x+1,-1],[x+1,1],[x-1,1]]]}}})
    }

    fn reservation(reference: &str, polygon: Value) -> Value {
        json!({"id":format!("body-{reference}"),"required":true,"kind":"solid","frame":"footprint",
            "owner":{"ref":reference,"uuid":format!("fp-{reference}")},"geometry":{"polygons_nm":[polygon]},
            "faces":["same"],"height_nm":[0,20],"phases":["installed"],"self_exclusion":true,
            "coverage":{"projection":"conservative-per-face","height":"covered","access":"covered",
                "basis":"authored unit-test projection covers the declared height"},"uncertainty":[]})
    }

    fn fixture() -> (Value, Value) {
        let fp = footprint("U1", 0);
        let capture = json!({"schema":"agent-ee.placement-probe/1","board_sha256":"a".repeat(64),
            "footprints":[fp.clone()]});
        let doc = json!({"schema":SCHEMA,"original_board_path":"original.kicad_pcb",
            "original_board_sha256":"a".repeat(64),"source":{"path":"engineering.json","sha256":"b".repeat(64),
                "locator":"/body","classification":"engineering","author":"authored unit test"},
            "reservations":[reservation("U1",json!([[0,0],[4,0],[4,2],[0,2]]))]});
        let bound = json!({"schema":BINDING_SCHEMA,"document":doc,"document_sha256":"c".repeat(64),
            "canonical_document_sha256":digest(&doc),"source_board_sha256":"a".repeat(64),"owners":[fp]});
        (capture, bound)
    }

    fn reseal(bound: &mut Value) {
        bound["canonical_document_sha256"] = json!(digest(&bound["document"]));
    }

    #[test]
    fn prepared_snapshot_has_no_mutable_alias_and_binds_complete_input_identity() {
        let (mut capture, mut bound) = fixture();
        let prepared = PreparedMechanical::new(&bound, &capture).unwrap();
        let identity = prepared.identity().to_owned();
        let original = prepared.grade(&capture, false).unwrap();
        assert_eq!(original, grade(&capture, &bound, false).unwrap());
        bound["document"]["reservations"][0]["height_nm"][1] = json!(21);
        assert!(PreparedMechanical::new(&bound, &capture).is_err());
        assert_eq!(prepared.grade(&capture, false).unwrap(), original);
        reseal(&mut bound);
        assert_ne!(PreparedMechanical::new(&bound, &capture).unwrap().identity(), identity);
        capture["footprints"][0]["side"] = json!("B");
        assert_ne!(PreparedMechanical::new(&bound, &capture).unwrap().identity(), identity);
    }

    #[test]
    fn custom_numeric_reflection_preserves_geometry_not_token_spelling() {
        let before = json!([
            [
                "gr_circle",
                ["center", "0", "10"],
                ["end", "1", "10"],
                ["width", "0"],
                ["fill", "yes"]
            ],
            [
                "gr_rect",
                ["start", "0", "10"],
                ["end", "1", "20"],
                ["width", "0"],
                ["fill", "yes"]
            ],
            [
                "gr_line",
                ["start", "0", "10"],
                ["end", "1", "20"],
                ["width", "0.1"]
            ],
            [
                "gr_poly",
                [
                    "pts",
                    ["xy", "0", "10"],
                    ["xy", "1", "10"],
                    ["xy", "1", "20"]
                ],
                ["width", "0"],
                ["fill", "yes"]
            ]
        ]);
        let after = json!([
            [
                "gr_circle",
                ["center", "0", "-1E+1"],
                ["end", "1", "-10"],
                ["width", "0.0"],
                ["fill", "yes"]
            ],
            [
                "gr_rect",
                ["start", "0", "-20"],
                ["end", "1", "-10"],
                ["width", "0"],
                ["fill", "yes"]
            ],
            [
                "gr_line",
                ["start", "0", "-10"],
                ["end", "1", "-20"],
                ["width", "0.1"]
            ],
            [
                "gr_poly",
                [
                    "pts",
                    ["xy", "0", "-10"],
                    ["xy", "1", "-10"],
                    ["xy", "1", "-20"]
                ],
                ["width", "0"],
                ["fill", "yes"]
            ]
        ]);
        assert_eq!(
            custom_primitives(&before, true).unwrap(),
            custom_primitives(&after, false).unwrap()
        );
        let mut changed = after.clone();
        changed[0][1][2] = json!("-10.000001");
        assert_ne!(
            custom_primitives(&before, true).unwrap(),
            custom_primitives(&changed, false).unwrap()
        );
        for invalid in [
            json!([["gr_arc", ["start", "0", "0"]]]),
            json!([[
                "gr_line",
                ["start", "0", "0"],
                ["end", "1", "1"],
                ["width", "0"]
            ]]),
            json!([[
                "gr_circle",
                ["center", "1000", "0"],
                ["end", "1001", "0"],
                ["width", "0"],
                ["fill", "yes"]
            ]]),
            json!([[
                "gr_rect",
                ["start", "0", "0"],
                ["end", "1", "0"],
                ["width", "0"],
                ["fill", "yes"]
            ]]),
            json!([[
                "gr_line",
                ["start", "0", "0"],
                ["end", "1", "1"],
                ["width", "0.0000001"]
            ]]),
            json!([[
                "gr_line",
                ["start", "0", "0"],
                ["end", "1", "1"],
                ["width", "NaN"]
            ]]),
            json!([[
                "gr_line",
                ["start", "0", "0"],
                ["end", "1.2.3", "1"],
                ["width", "1"]
            ]]),
        ] {
            assert!(custom_primitives(&invalid, false).is_err(), "{invalid}");
        }
    }

    #[test]
    fn declarations_cover_full_board_inventory_with_finite_cap() {
        let (_, bound) = fixture();
        let mut doc = bound["document"].clone();
        let template = doc["reservations"][0].clone();
        let inventory = (0..1024)
            .map(|index| {
                let mut row = template.clone();
                row["id"] = json!(format!("reservation-{index}"));
                if index >= 313 {
                    row["required"] = json!(false);
                    row["geometry"]["polygons_nm"] = Value::Null;
                }
                row
            })
            .collect::<Vec<_>>();
        doc["reservations"] = json!(&inventory[..313]);
        document(&doc).unwrap();
        doc["reservations"] = json!(inventory);
        document(&doc).unwrap();
        let mut extra = template;
        extra["id"] = json!("reservation-1024");
        doc["reservations"].as_array_mut().unwrap().push(extra);
        assert!(document(&doc).unwrap_err().to_string().contains("too many"));
    }

    #[test]
    fn required_unknown_geometry_never_passes() {
        let (capture, mut bound) = fixture();
        bound["document"]["reservations"][0]["geometry"]["polygons_nm"] = Value::Null;
        reseal(&mut bound);
        let report = grade(&capture, &bound, false).unwrap();
        assert_eq!(report["status"], "unknown");
        assert_eq!(report["unknown"][0]["reason"], "geometry is unresolved");
        assert_eq!(report["reservations"], json!([]));
    }

    #[test]
    fn current_native_angle_and_face_transform_attached_geometry() {
        let (mut capture, bound) = fixture();
        let fp = &mut capture["footprints"][0];
        fp["position_nm"] = json!([10, 20]);
        fp["rotation_deg"] = json!(90.0);
        fp["side"] = json!("B");
        fp["pads"][0]["position_nm"] = json!([7, 18]);
        fp["pads"][0]["rotation_deg"] = json!(90.0);
        let report = grade(&capture, &bound, false).unwrap();
        assert_eq!(report["status"], "pass");
        assert_eq!(report["phases"], json!(["installed"]));
        assert_eq!(report["reservations"][0]["faces"], json!(["B"]));
        assert_eq!(
            report["reservations"][0]["polygons_nm"],
            json!([[[10, 20], [10, 16], [8, 16], [8, 20]]])
        );
    }

    #[test]
    fn missing_pad_and_stale_partial_owner_are_unknown() {
        let (mut capture, bound) = fixture();
        capture["footprints"][0]["pads"] = json!([]);
        let report = grade(&capture, &bound, false).unwrap();
        assert_eq!(report["status"], "unknown");
        assert_eq!(
            report["unknown"][0]["reason"],
            "mechanical native pad UUID inventory changed"
        );
        capture["footprints"][0]["uuid"] = json!("replacement");
        assert_eq!(grade(&capture, &bound, true).unwrap()["status"], "unknown");
        capture["footprints"] = json!([]);
        assert_eq!(grade(&capture, &bound, true).unwrap()["status"], "pass");
        assert_eq!(grade(&capture, &bound, false).unwrap()["status"], "unknown");
    }

    #[test]
    fn unqualified_custom_pads_refuse_and_exact_copper_offsets_follow_native_transform() {
        let (mut capture, mut bound) = fixture();
        let source = &mut bound["owners"][0]["pads"][0];
        source["copper_centroid_offset_nm"] = json!([[1, 2], [3, 4]]);
        source["copper_land_offset_nm"] = json!([2, 3]);
        let fp = &mut capture["footprints"][0];
        fp["side"] = json!("B");
        fp["position_nm"] = json!([10, 20]);
        fp["rotation_deg"] = json!(90);
        fp["pads"][0]["position_nm"] = json!([7, 18]);
        fp["pads"][0]["rotation_deg"] = json!(90);
        fp["pads"][0]["copper_centroid_offset_nm"] = json!([[-3, 4], [-1, 2]]);
        fp["pads"][0]["copper_land_offset_nm"] = json!([-3, -2]);
        assert_eq!(grade(&capture, &bound, false).unwrap()["status"], "pass");
        capture["footprints"][0]["pads"][0]["copper_centroid_offset_nm"] = json!([[3, 4], [-1, 2]]);
        let report = grade(&capture, &bound, false).unwrap();
        assert_eq!(report["status"], "unknown");
        assert_eq!(
            report["unknown"][0]["reason"],
            "mechanical native pad copper offset changed independently of owner"
        );
        capture["footprints"][0]["pads"][0]["custom_geometry"] = json!({"status":"proven"});
        let report = grade(&capture, &bound, false).unwrap();
        assert_eq!(report["status"], "unknown");
        assert_eq!(
            report["unknown"][0]["reason"],
            "mechanical custom-pad geometry is not native qualified"
        );
        bound["owners"][0]["pads"][0]["shape"] = json!(6);
        assert!(
            grade(&capture, &bound, false)
                .unwrap_err()
                .to_string()
                .contains("custom-pad")
        );
    }

    #[test]
    fn body_overlap_outside_courtyards_is_detected_and_phase_qualified() {
        let (mut capture, mut bound) = fixture();
        let other = footprint("U2", 10);
        capture["footprints"]
            .as_array_mut()
            .unwrap()
            .push(other.clone());
        bound["owners"].as_array_mut().unwrap().push(other);
        bound["document"]["reservations"] = json!([
            reservation("U1", json!([[2, -1], [6, -1], [6, 1], [2, 1]])),
            reservation("U2", json!([[-6, -1], [-2, -1], [-2, 1], [-6, 1]]))
        ]);
        reseal(&mut bound);
        let report = grade(&capture, &bound, false).unwrap();
        assert_eq!(report["status"], "defect");
        assert_eq!(report["defects"].as_array().unwrap().len(), 1);
        assert_eq!(
            report["defects"][0]["kind"],
            "mechanical_reservation_pair_overlap"
        );
        assert_eq!(report["defects"][0]["overlap_area_nm2"], json!([4, 1]));
        bound["document"]["reservations"][1]["phases"] = json!(["bench"]);
        reseal(&mut bound);
        let report = grade(&capture, &bound, false).unwrap();
        assert_eq!(report["status"], "pass");
        assert_eq!(report["phases"], json!(["bench", "installed"]));
    }
}
