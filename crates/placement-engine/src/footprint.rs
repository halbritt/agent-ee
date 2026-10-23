use crate::exact::{
    Int, Point, Rat, ceil, floor, integer, integer_value, point, point_value, rational,
    rational_value, rotate, rotation,
};
use crate::geometry::{
    self, Bounds, Polygon, PolygonBounds, bounds, contains, courtyard_bounds, polygons_value,
    qualified_polygons, rectangle,
};
use anyhow::{Context, Result, bail};
use num_traits::{Signed, Zero};
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};
use std::sync::Arc;

fn native_repr(value: &Value) -> String {
    match value {
        Value::Null => "None".into(),
        Value::Bool(v) => if *v { "True" } else { "False" }.into(),
        Value::String(s) => {
            let quote = if s.contains('\'') && !s.contains('"') {
                '"'
            } else {
                '\''
            };
            let mut out = String::new();
            out.push(quote);
            for c in s.chars() {
                match c {
                    '\\' => out.push_str("\\\\"),
                    '\n' => out.push_str("\\n"),
                    '\r' => out.push_str("\\r"),
                    '\t' => out.push_str("\\t"),
                    c if c == quote => {
                        out.push('\\');
                        out.push(c);
                    }
                    c => out.push(c),
                }
            }
            out.push(quote);
            out
        }
        _ => value.to_string(),
    }
}
fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(v) => *v,
        Value::Number(v) => integer(value)
            .map(|n| !n.is_zero())
            .unwrap_or_else(|_| v.as_f64().is_some_and(|n| n != 0.0)),
        Value::String(v) => !v.is_empty(),
        Value::Array(v) => !v.is_empty(),
        Value::Object(v) => !v.is_empty(),
    }
}

#[derive(Clone, Debug)]
pub struct Keepout {
    pub side: String,
    pub polygons: Vec<Polygon>,
    pub name: String,
}
#[derive(Clone, Debug)]
pub struct PadKeepout {
    pub layers: BTreeSet<String>,
    pub polygons: Vec<Polygon>,
    pub name: String,
}
#[derive(Clone, Debug)]
pub struct Land {
    pub layers: BTreeSet<String>,
    pub polygon: Polygon,
    pub proven: bool,
}
#[derive(Clone, Debug)]
pub struct Pad {
    pub number: String,
    pub net: String,
    pub uuid: String,
    pub position: Point,
    pub rotation: i64,
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct FootprintBounds {
    pub courtyards: BTreeMap<String, PolygonBounds>,
    pub holes: PolygonBounds,
    pub targets: PolygonBounds,
    pub copper_sides: BTreeMap<String, PolygonBounds>,
}
impl FootprintBounds {
    fn new(
        courtyards: &BTreeMap<String, Vec<Polygon>>,
        holes: &[Polygon],
        targets: &[Polygon],
        copper_sides: &BTreeMap<String, Vec<Polygon>>,
    ) -> Result<Self> {
        fn sides(rows: &BTreeMap<String, Vec<Polygon>>) -> Result<BTreeMap<String, PolygonBounds>> {
            rows.iter()
                .map(|(side, polys)| Ok((side.clone(), PolygonBounds::new(polys)?)))
                .collect()
        }
        Ok(Self {
            courtyards: sides(courtyards)?,
            holes: PolygonBounds::new(holes)?,
            targets: PolygonBounds::new(targets)?,
            copper_sides: sides(copper_sides)?,
        })
    }
    fn at(&self, origin: &Point, position: &Point, turns: i64) -> Self {
        let sides = |rows: &BTreeMap<String, PolygonBounds>| {
            rows.iter()
                .map(|(side, bounds)| (side.clone(), bounds.at(origin, position, turns)))
                .collect()
        };
        Self {
            courtyards: sides(&self.courtyards),
            holes: self.holes.at(origin, position, turns),
            targets: self.targets.at(origin, position, turns),
            copper_sides: sides(&self.copper_sides),
        }
    }
}
#[derive(Clone, Debug)]
pub struct PreparedFootprint {
    pub reference: String,
    pub uuid: String,
    pub side: String,
    pub position: Point,
    pub rotation: i64,
    pub rotation_json: Value,
    pub courtyards: BTreeMap<String, Vec<Polygon>>,
    pub inner_courtyards: BTreeMap<String, Vec<Polygon>>,
    pub boxes: BTreeMap<String, Bounds>,
    pub slabs: BTreeMap<String, Vec<Bounds>>,
    pub pads: Vec<Pad>,
    pub keepouts: Vec<Keepout>,
    pub pad_keepouts: Vec<PadKeepout>,
    pub holes: Vec<Polygon>,
    pub targets: Vec<Polygon>,
    pub copper_outline: Vec<Polygon>,
    pub copper_sides: BTreeMap<String, Vec<Polygon>>,
    pub lands: Vec<Land>,
    pub setback: Option<Int>,
    pub raw: Arc<Value>,
    pub(crate) geometry_bounds: FootprintBounds,
}
#[derive(Clone, Debug)]
pub struct QualifiedCapture {
    pub raw: Value,
    pub outline: Polygon,
    pub footprints: Vec<PreparedFootprint>,
    pub board_keepouts: Vec<Keepout>,
    pub board_pad_keepouts: Vec<PadKeepout>,
    pub setback: Option<Int>,
}

pub fn copper_layers(value: &Value) -> Result<BTreeSet<String>> {
    let rows = value.as_array().context("malformed copper layers")?;
    let mut out = BTreeSet::new();
    for row in rows {
        let layer = row.as_str().context("malformed copper layers")?;
        if layer.ends_with(".Cu") {
            out.insert(layer.to_owned());
        }
    }
    if out.contains("*.Cu") {
        return Ok(BTreeSet::from(["*.Cu".to_owned()]));
    }
    Ok(out)
}
pub fn layers_meet(a: &BTreeSet<String>, b: &BTreeSet<String>) -> bool {
    !a.is_empty()
        && !b.is_empty()
        && (a.contains("*.Cu") || b.contains("*.Cu") || !a.is_disjoint(b))
}
fn outer_sides(pad: &Value) -> Result<(Vec<String>, bool, BTreeSet<String>)> {
    let Some(layers) = pad.get("layers").filter(|v| !v.is_null()) else {
        return Ok((
            vec!["F".into(), "B".into()],
            true,
            BTreeSet::from(["*.Cu".into()]),
        ));
    };
    let layers = copper_layers(layers)
        .map_err(|_| anyhow::anyhow!("pad {}: malformed layers", native_repr(&pad["number"])))?;
    let sides = ["F", "B"]
        .into_iter()
        .filter(|s| layers.contains("*.Cu") || layers.contains(&format!("{s}.Cu")))
        .map(str::to_owned)
        .collect();
    Ok((sides, !layers.is_empty(), layers))
}

pub fn copper_box(fp: &Value, pad: &Value) -> Result<Option<Bounds>> {
    let where_ = format!(
        "{} pad {}",
        fp["ref"].as_str().unwrap_or(""),
        native_repr(&pad["number"])
    );
    let malformed = || anyhow::anyhow!("{where_}: copper land size or centroid malformed");
    if pad.get("shape").and_then(Value::as_i64) == Some(6)
        && pad["custom_geometry"]["status"] == "proven"
    {
        let b = pad["custom_geometry"]["bbox_nm"]
            .as_array()
            .context("custom copper box missing")?;
        if b.len() != 4 {
            bail!("custom copper box malformed");
        }
        return Ok(Some(Bounds {
            x0: integer(&b[0])?,
            y0: integer(&b[1])?,
            x1: integer(&b[2])?,
            y1: integer(&b[3])?,
        }));
    }
    if [
        "copper_centroid_offset_nm",
        "copper_centroid_reason",
        "copper_land_offset_nm",
    ]
    .iter()
    .all(|k| pad.get(k).is_none())
    {
        return Ok(None);
    }
    let size = point(&pad["size_nm"]).map_err(|_| malformed())?;
    if size.x.is_negative() || size.y.is_negative() {
        return Err(malformed());
    }
    let pos = point(&pad["position_nm"]).map_err(|_| malformed())?;
    let rot = rotation(&pad["rotation_deg"])?;
    let shape = pad["shape"].as_i64().context("pad shape missing")?;
    let (mut w, mut h) = if rot.rem_euclid(180) == 0 {
        (size.x.clone(), size.y.clone())
    } else {
        (size.y.clone(), size.x.clone())
    };
    if shape == 0 {
        w = size.x.clone();
        h = size.x;
    }
    let (cx, cy, reach) = if let Some(offset) = pad
        .get("copper_centroid_offset_nm")
        .filter(|v| !v.is_null())
    {
        let offset = offset.as_array().ok_or_else(malformed)?;
        if offset.len() != 2 {
            return Err(malformed());
        }
        (
            Rat::from_integer(pos.x) + rational(&offset[0]).map_err(|_| malformed())?,
            Rat::from_integer(pos.y) + rational(&offset[1]).map_err(|_| malformed())?,
            if shape == 5 {
                Rat::from_integer(1.into())
            } else {
                Rat::new(1.into(), 2.into())
            },
        )
    } else if shape == 5
        && pad
            .get("copper_land_offset_nm")
            .is_some_and(|v| !v.is_null())
    {
        let offset = point(&pad["copper_land_offset_nm"])
            .map_err(|_| anyhow::anyhow!("{where_}: copper land offset malformed"))?;
        (
            Rat::from_integer(pos.x + offset.x),
            Rat::from_integer(pos.y + offset.y),
            Rat::new(1.into(), 2.into()),
        )
    } else {
        bail!(
            "{where_}: copper extent unproven ({})",
            pad["copper_centroid_reason"]
                .as_str()
                .unwrap_or("no native land fact")
        );
    };
    let dx = &reach * Rat::from_integer(w);
    let dy = &reach * Rat::from_integer(h);
    Ok(Some(Bounds {
        x0: floor(&(&cx - &dx)),
        y0: floor(&(&cy - &dy)),
        x1: ceil(&(&cx + &dx)),
        y1: ceil(&(&cy + &dy)),
    }))
}

fn parse_keepouts(raw: &Value, key: &str) -> Result<Vec<Keepout>> {
    let Some(rows) = raw.get(key) else {
        return Ok(Vec::new());
    };
    rows.as_array()
        .context("keepouts must be a list")?
        .iter()
        .map(|v| {
            Ok(Keepout {
                side: v["side"].as_str().context("keepout side missing")?.into(),
                polygons: qualified_polygons(&json!({"status":"proven","polygons":v["polygons"]}))?,
                name: v["name"].as_str().unwrap_or("owned keepout").into(),
            })
        })
        .collect()
}
fn parse_pad_keepouts(raw: &Value) -> Result<Vec<PadKeepout>> {
    let Some(rows) = raw.get("packing_pad_keepouts") else {
        return Ok(Vec::new());
    };
    rows.as_array()
        .context("pad keepouts must be a list")?
        .iter()
        .map(|v| {
            Ok(PadKeepout {
                layers: copper_layers(&v["layers"])?,
                polygons: qualified_polygons(&json!({"status":"proven","polygons":v["polygons"]}))?,
                name: v["name"].as_str().unwrap_or("owned pad keepout").into(),
            })
        })
        .collect()
}

impl PreparedFootprint {
    /// A rigid block is a union of qualified member contours, not a new courtyard
    /// record. Members may touch, and their total contour count may exceed a
    /// single footprint's qualification limit.
    pub fn from_members(id: &str, members: &[PreparedFootprint]) -> Result<Self> {
        let first = members.first().context("rigid block has no members")?;
        let mut courtyards: BTreeMap<String, Vec<Polygon>> = BTreeMap::new();
        let (mut keepouts, mut holes, mut native_copper) = (Vec::new(), Vec::new(), Vec::new());
        let (mut raw_keepouts, mut raw_copper, mut raw_holes) =
            (Vec::new(), Vec::new(), Vec::new());
        let mut has_geometry = false;
        for fp in members {
            for (side, polys) in &fp.courtyards {
                courtyards
                    .entry(side.clone())
                    .or_default()
                    .extend(polys.iter().cloned());
            }
            keepouts.extend(fp.keepouts.iter().cloned());
            holes.extend(fp.holes.iter().cloned());
            let raw = fp.to_value()?;
            raw_keepouts.extend(
                raw.get("packing_keepouts")
                    .and_then(Value::as_array)
                    .into_iter()
                    .flatten()
                    .cloned(),
            );
            if let Some(g) = raw.get("packing_pad_geometry") {
                has_geometry = true;
                raw_copper.extend(
                    g["copper"]
                        .as_array()
                        .context("native copper inventory missing")?
                        .iter()
                        .cloned(),
                );
                raw_holes.extend(
                    g["holes"]
                        .as_array()
                        .context("native hole inventory missing")?
                        .iter()
                        .cloned(),
                );
                let own_count = fp.courtyards.values().map(Vec::len).sum::<usize>();
                native_copper.extend(fp.targets.iter().skip(own_count).cloned());
            }
        }
        let boxes = courtyards
            .iter()
            .map(|(s, p)| Ok((s.clone(), bounds(p)?)))
            .collect::<Result<_>>()?;
        let mut slabs = BTreeMap::new();
        for (s, p) in &courtyards {
            let all: Option<Vec<Vec<Bounds>>> = p.iter().map(geometry::slabs).collect();
            if let Some(rows) = all {
                slabs.insert(s.clone(), rows.into_iter().flatten().collect());
            }
        }
        let targets: Vec<Polygon> = if has_geometry {
            courtyards
                .values()
                .flatten()
                .cloned()
                .chain(native_copper)
                .collect()
        } else {
            Vec::new()
        };
        let raw_courtyards: serde_json::Map<String, Value> = courtyards
            .iter()
            .map(|(s, p)| {
                (
                    s.clone(),
                    json!({"status":"proven","polygons":polygons_value(p)}),
                )
            })
            .collect();
        let mut raw = json!({"ref":id,"side":first.side,"position_nm":[0,0],"rotation_deg":0,"courtyards":raw_courtyards,"packing_keepouts":raw_keepouts,"pads":[]});
        if has_geometry {
            raw["packing_pad_geometry"] =
                json!({"status":"proven","copper":raw_copper,"holes":raw_holes});
        }
        let geometry_bounds =
            FootprintBounds::new(&courtyards, &holes, &targets, &BTreeMap::new())?;
        Ok(Self {
            reference: id.into(),
            uuid: String::new(),
            side: first.side.clone(),
            position: Point::zero(),
            rotation: 0,
            rotation_json: json!(0),
            inner_courtyards: courtyards.clone(),
            courtyards,
            boxes,
            slabs,
            pads: Vec::new(),
            keepouts,
            pad_keepouts: Vec::new(),
            holes,
            targets,
            copper_outline: Vec::new(),
            copper_sides: BTreeMap::new(),
            lands: Vec::new(),
            setback: None,
            raw: Arc::new(raw),
            geometry_bounds,
        })
    }
    pub fn from_value(raw: &Value, setback: Option<Int>) -> Result<Self> {
        let reference = raw["ref"]
            .as_str()
            .context("footprint reference missing")?
            .to_owned();
        let side = raw["side"]
            .as_str()
            .context("footprint side missing")?
            .to_owned();
        let mut courtyards = BTreeMap::new();
        let mut inner_courtyards = BTreeMap::new();
        let mut boxes = BTreeMap::new();
        let mut slabs = BTreeMap::new();
        for (side, record) in raw["courtyards"]
            .as_object()
            .context("courtyard inventory missing")?
        {
            let (outer, inner) = courtyard_bounds(record)?;
            boxes.insert(side.clone(), bounds(&outer)?);
            let all: Option<Vec<Vec<Bounds>>> = outer.iter().map(geometry::slabs).collect();
            if let Some(rows) = all {
                slabs.insert(side.clone(), rows.into_iter().flatten().collect());
            }
            courtyards.insert(side.clone(), outer);
            inner_courtyards.insert(side.clone(), inner);
        }
        let raw_pads = raw["pads"].as_array().context("pad inventory missing")?;
        let pads = raw_pads
            .iter()
            .map(|p| {
                Ok(Pad {
                    number: p["number"].as_str().unwrap_or("").into(),
                    net: p["net"].as_str().unwrap_or("").into(),
                    uuid: p["uuid"].as_str().unwrap_or("").into(),
                    position: point(&p["position_nm"])?,
                    rotation: rotation(&p["rotation_deg"])?,
                })
            })
            .collect::<Result<Vec<_>>>()?;
        let mut holes = Vec::new();
        let mut targets = Vec::new();
        if let Some(g) = raw.get("packing_pad_geometry") {
            if g["status"] != "proven" {
                bail!("{reference}: native packing pad geometry unqualified");
            }
            targets.extend(courtyards.values().flatten().cloned());
            for kind in ["holes", "copper"] {
                for e in g[kind]
                    .as_array()
                    .context("native packing inventory missing")?
                {
                    let polys =
                        qualified_polygons(&json!({"status":"proven","polygons":e["polygons"]}))?;
                    if kind == "holes" {
                        holes.extend(polys);
                    } else {
                        targets.extend(polys);
                    }
                }
            }
        }
        let mut copper_outline = Vec::new();
        let mut copper_sides: BTreeMap<String, Vec<Polygon>> = BTreeMap::new();
        let mut lands = Vec::new();
        for pad in raw_pads {
            let (on, copper, layers) = outer_sides(pad)?;
            if !copper {
                continue;
            }
            let b = copper_box(raw, pad)?;
            let Some(b) = b else {
                for p in courtyards.values().flatten() {
                    lands.push(Land {
                        layers: layers.clone(),
                        polygon: p.clone(),
                        proven: false,
                    });
                }
                continue;
            };
            if b.x0 >= b.x1 || b.y0 >= b.y1 {
                continue;
            }
            let poly = rectangle(&b);
            lands.push(Land {
                layers,
                polygon: poly.clone(),
                proven: true,
            });
            if setback.as_ref().is_none_or(|s| {
                !courtyards
                    .values()
                    .flatten()
                    .any(|p| contains(p, &rectangle(&b.grown(s))))
            }) {
                copper_outline.push(poly.clone());
            }
            for side in on {
                if !courtyards
                    .get(&side)
                    .is_some_and(|polys| polys.iter().any(|p| contains(p, &poly)))
                {
                    copper_sides.entry(side).or_default().push(poly.clone());
                }
            }
        }
        let geometry_bounds = FootprintBounds::new(&courtyards, &holes, &targets, &copper_sides)?;
        Ok(Self {
            reference,
            uuid: raw["uuid"].as_str().unwrap_or("").into(),
            side,
            position: point(&raw["position_nm"])?,
            rotation: rotation(&raw["rotation_deg"])?,
            rotation_json: raw["rotation_deg"].clone(),
            courtyards,
            inner_courtyards,
            boxes,
            slabs,
            pads,
            keepouts: parse_keepouts(raw, "packing_keepouts")?,
            pad_keepouts: parse_pad_keepouts(raw)?,
            holes,
            targets,
            copper_outline,
            copper_sides,
            lands,
            setback,
            raw: Arc::new(raw.clone()),
            geometry_bounds,
        })
    }

    /// Rebuild after a deliberate typed-geometry replacement. Ordinary poses
    /// use `at`, which transforms the prepared bounds without rescanning shapes.
    pub fn refresh_geometry_bounds(&mut self) -> Result<()> {
        self.geometry_bounds = FootprintBounds::new(
            &self.courtyards,
            &self.holes,
            &self.targets,
            &self.copper_sides,
        )?;
        Ok(())
    }

    pub fn at(&self, position: &Point, rotation: i64) -> Result<Self> {
        if rotation % 90 != 0 {
            bail!("rotation must be a quarter turn");
        }
        let turns = (rotation.rem_euclid(360) - self.rotation.rem_euclid(360)) / 90;
        let delta = position.minus(&self.position);
        let move_point = |p: &Point| {
            if turns == 0 {
                p.plus(&delta)
            } else {
                rotate(&p.minus(&self.position), turns).plus(position)
            }
        };
        let move_poly = |p: &Polygon| p.iter().map(&move_point).collect::<Polygon>();
        let move_polys = |p: &Vec<Polygon>| p.iter().map(&move_poly).collect::<Vec<_>>();
        // These caches are axis-aligned native copper boxes. Python derives
        // them from the newly rotated pad facts, always starting at (x0,y0).
        let move_box = |p: &Polygon| {
            if turns == 0 {
                move_poly(p)
            } else {
                rectangle(&bounds(&[move_poly(p)]).expect("qualified native copper box"))
            }
        };
        let move_boxes = |p: &Vec<Polygon>| p.iter().map(&move_box).collect::<Vec<_>>();
        let courtyards: BTreeMap<_, _> = self
            .courtyards
            .iter()
            .map(|(s, p)| (s.clone(), move_polys(p)))
            .collect();
        let move_bounds = |b: &Bounds| {
            if turns == 0 {
                b.translated(&delta)
            } else {
                b.at(&self.position, position, turns)
            }
        };
        let boxes = self
            .boxes
            .iter()
            .map(|(s, b)| (s.clone(), move_bounds(b)))
            .collect();
        let mut slabs = BTreeMap::new();
        for (s, rows) in &self.slabs {
            let moved = rows.iter().map(&move_bounds).collect();
            slabs.insert(s.clone(), moved);
        }
        Ok(Self {
            reference: self.reference.clone(),
            uuid: self.uuid.clone(),
            side: self.side.clone(),
            position: position.clone(),
            rotation,
            rotation_json: json!(rotation),
            courtyards,
            inner_courtyards: self
                .inner_courtyards
                .iter()
                .map(|(s, p)| (s.clone(), move_polys(p)))
                .collect(),
            boxes,
            slabs,
            pads: self
                .pads
                .iter()
                .map(|p| Pad {
                    position: move_point(&p.position),
                    rotation: (p.rotation.rem_euclid(360) + turns * 90).rem_euclid(360),
                    ..p.clone()
                })
                .collect(),
            keepouts: self
                .keepouts
                .iter()
                .map(|k| Keepout {
                    polygons: move_polys(&k.polygons),
                    ..k.clone()
                })
                .collect(),
            pad_keepouts: self
                .pad_keepouts
                .iter()
                .map(|k| PadKeepout {
                    polygons: move_polys(&k.polygons),
                    ..k.clone()
                })
                .collect(),
            holes: move_polys(&self.holes),
            targets: move_polys(&self.targets),
            copper_outline: move_boxes(&self.copper_outline),
            copper_sides: self
                .copper_sides
                .iter()
                .map(|(s, p)| (s.clone(), move_boxes(p)))
                .collect(),
            lands: self
                .lands
                .iter()
                .map(|l| Land {
                    polygon: if l.proven {
                        move_box(&l.polygon)
                    } else {
                        move_poly(&l.polygon)
                    },
                    ..l.clone()
                })
                .collect(),
            setback: self.setback.clone(),
            raw: Arc::clone(&self.raw),
            geometry_bounds: self.geometry_bounds.at(&self.position, position, turns),
        })
    }
    pub fn to_value(&self) -> Result<Value> {
        if self.position == point(&self.raw["position_nm"])?
            && self.rotation_json == self.raw["rotation_deg"]
        {
            return Ok((*self.raw).clone());
        }
        let mut out = transform_value(&self.raw, &point_value(&self.position), self.rotation)?;
        out["rotation_deg"] = self.rotation_json.clone();
        Ok(out)
    }
}

pub fn transform_value(fp: &Value, position: &Value, rotation_degrees: i64) -> Result<Value> {
    if rotation_degrees % 90 != 0 {
        bail!("rotation must be a quarter turn");
    }
    let origin = point(&fp["position_nm"])?;
    let target = point(position)?;
    let old = rotation(&fp["rotation_deg"])?;
    let turns = (rotation_degrees.rem_euclid(360) - old.rem_euclid(360)) / 90;
    let move_point = |p: &Value| -> Result<Value> {
        Ok(point_value(
            &rotate(&point(p)?.minus(&origin), turns).plus(&target),
        ))
    };
    let move_polygons = |polys: &Value| -> Result<Value> {
        Ok(Value::Array(
            polys
                .as_array()
                .context("polygons missing")?
                .iter()
                .map(|poly| {
                    Ok(Value::Array(
                        poly.as_array()
                            .context("polygon missing")?
                            .iter()
                            .map(&move_point)
                            .collect::<Result<_>>()?,
                    ))
                })
                .collect::<Result<_>>()?,
        ))
    };
    let mut out = fp.clone();
    out.as_object_mut()
        .context("footprint must be object")?
        .retain(|k, _| !k.starts_with("_packing_"));
    for c in out["courtyards"]
        .as_object_mut()
        .context("courtyards missing")?
        .values_mut()
    {
        for key in ["polygons", "inner_polygons"] {
            if let Some(p) = c.get_mut(key) {
                *p = move_polygons(p)?;
            }
        }
    }
    for key in ["packing_keepouts", "packing_pad_keepouts"] {
        if let Some(rows) = out.get_mut(key) {
            for k in rows.as_array_mut().context("keepouts missing")? {
                k["polygons"] = move_polygons(&k["polygons"])?;
            }
        }
    }
    if let Some(g) = out.get_mut("packing_pad_geometry") {
        for kind in ["copper", "holes"] {
            for e in g[kind]
                .as_array_mut()
                .context("pad geometry inventory missing")?
            {
                e["polygons"] = move_polygons(&e["polygons"])?;
            }
        }
    }
    for p in out["pads"]
        .as_array_mut()
        .context("pad inventory missing")?
    {
        if let Some(custom) = p.get_mut("custom_geometry") {
            if !custom.is_object() {
                bail!("custom_geometry must be an object");
            }
            if custom["status"] == "proven" {
                let b = custom["bbox_nm"]
                    .as_array()
                    .context("custom bbox missing")?;
                if b.len() != 4 {
                    bail!("custom bbox malformed");
                }
                let b = Bounds {
                    x0: integer(&b[0])?,
                    y0: integer(&b[1])?,
                    x1: integer(&b[2])?,
                    y1: integer(&b[3])?,
                };
                let moved: Polygon = rectangle(&b)
                    .iter()
                    .map(|p| rotate(&p.minus(&origin), turns).plus(&target))
                    .collect();
                let b = bounds(&[moved])?;
                custom["bbox_nm"] =
                    Value::Array([b.x0, b.y0, b.x1, b.y1].iter().map(integer_value).collect());
            }
        }
        if let Some(offset) = p.get_mut("copper_centroid_offset_nm") {
            let rows = offset.as_array().context("centroid malformed")?;
            if rows.len() != 2 {
                bail!("centroid malformed");
            }
            let (x, y) = (rational(&rows[0])?, rational(&rows[1])?);
            let (x, y) = match turns.rem_euclid(4) {
                0 => (x, y),
                1 => (y, -x),
                2 => (-x, -y),
                _ => (-y, x),
            };
            *offset = Value::Array(vec![rational_value(&x), rational_value(&y)]);
        }
        if let Some(offset) = p.get_mut("copper_land_offset_nm") {
            *offset = point_value(&rotate(&point(offset)?, turns));
        }
        p["position_nm"] = move_point(&p["position_nm"])?;
        p["rotation_deg"] =
            json!((rotation(&p["rotation_deg"])?.rem_euclid(360) + turns * 90).rem_euclid(360));
    }
    out["position_nm"] = point_value(&target);
    out["rotation_deg"] = json!(rotation_degrees);
    Ok(out)
}

pub fn validate_capture(capture: &Value) -> Result<()> {
    if capture["schema"] != "agent-ee.placement-probe/1" {
        bail!("unsupported placement capture schema");
    }
    let hash = capture["board_sha256"].as_str().unwrap_or("");
    if hash.len() != 64
        || !hash
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    {
        bail!("missing captured board hash");
    }
    let fps = capture["footprints"]
        .as_array()
        .context("empty footprint inventory")?;
    if fps.is_empty() {
        bail!("empty footprint inventory");
    }
    let (mut refs, mut uuids) = (BTreeSet::new(), BTreeSet::new());
    for f in fps {
        if !f.is_object() {
            bail!("footprint record must be an object");
        }
        for (key, seen) in [("ref", &mut refs), ("uuid", &mut uuids)] {
            let id = f[key]
                .as_str()
                .context("missing or ambiguous footprint identity")?;
            if id.is_empty() || !seen.insert(id.to_owned()) {
                bail!("missing or ambiguous footprint identity");
            }
        }
    }
    Ok(())
}
fn drilled(p: &Value) -> bool {
    matches!(p["attribute"].as_str(), Some("PTH" | "NPTH"))
        || match &p["drill_size_nm"] {
            Value::Array(size) => size.iter().any(truthy),
            Value::String(size) => !size.is_empty(),
            Value::Object(size) => size.keys().any(|key| !key.is_empty()),
            _ => false,
        }
}
pub fn qualify_capture(capture: &Value) -> Result<QualifiedCapture> {
    validate_capture(capture)?;
    let mut raw = capture.clone();
    let mut outlines = qualified_polygons(&raw["outline"])?;
    if outlines.len() != 1 {
        bail!("single closed outline required");
    }
    let outline = outlines.remove(0);
    let fps = raw["footprints"].as_array().unwrap();
    let has_pads = fps
        .iter()
        .any(|f| f["pads"].as_array().is_some_and(|p| !p.is_empty()));
    if has_pads
        && raw["pcbnew_version"]
            .as_str()
            .is_none_or(|s| s.split('.').next() != Some("10"))
    {
        bail!(
            "native pad shape enum not qualified for pcbnew {}",
            native_repr(&raw["pcbnew_version"])
        );
    }
    let has_holes = fps.iter().any(|f| {
        f["pads"]
            .as_array()
            .is_some_and(|pads| pads.iter().any(drilled))
    });
    let mut missing: Vec<_> = fps
        .iter()
        .filter(|f| {
            f["side"]
                .as_str()
                .is_none_or(|s| f["courtyards"].get(s).is_none())
        })
        .map(|f| f["ref"].as_str().unwrap_or("").to_owned())
        .collect();
    missing.sort();
    if !missing.is_empty() {
        bail!("mounting-side courtyard missing: {}", missing.join(", "));
    }
    let setback = if let Some(v) = raw.get("max_error_nm") {
        let n = integer(v)
            .map_err(|_| anyhow::anyhow!("board max_error_nm must be a nonnegative integer"))?;
        if n.is_negative() {
            bail!("board max_error_nm must be a nonnegative integer");
        }
        Some(n + 50_u8)
    } else {
        None
    };
    for fp in raw["footprints"].as_array_mut().unwrap() {
        let reference = fp["ref"].as_str().unwrap().to_owned();
        fp.as_object_mut()
            .unwrap()
            .retain(|k, _| !k.starts_with("_packing_"));
        if let Some(g) = fp.get("packing_pad_geometry") {
            if g["status"] != "proven" {
                bail!(
                    "{reference}: native packing pad geometry unqualified {}",
                    g["reason"].as_str().unwrap_or("")
                );
            }
            for kind in ["copper", "holes"] {
                for e in g[kind].as_array().with_context(|| {
                    format!("{reference}: native packing {kind} inventory missing")
                })? {
                    let layers = e["layers"].as_array().with_context(|| {
                        format!("{reference}: native packing copper layers missing")
                    })?;
                    if layers.is_empty()
                        || layers
                            .iter()
                            .any(|l| l.as_str().is_none_or(|l| !l.ends_with(".Cu")))
                    {
                        bail!("{reference}: native packing copper layers missing");
                    }
                    qualified_polygons(&json!({"status":"proven","polygons":e["polygons"]}))?;
                }
            }
            let required: BTreeSet<_> = fp["pads"]
                .as_array()
                .context("pads missing")?
                .iter()
                .filter(|p| drilled(p))
                .map(|p| p["uuid"].as_str().unwrap_or(""))
                .collect();
            let drills: Vec<_> = g["holes"]
                .as_array()
                .unwrap()
                .iter()
                .filter(|e| e["reservation_kind"] == "drill")
                .map(|e| e["pad_uuid"].as_str().unwrap_or(""))
                .collect();
            if drills.len() != drills.iter().collect::<BTreeSet<_>>().len()
                || drills.into_iter().collect::<BTreeSet<_>>() != required
            {
                bail!("{reference}: native packing per-pad drill reservations incomplete");
            }
        } else if has_holes {
            bail!("{reference}: native packing drill reservations missing");
        }
        rotation(&fp["rotation_deg"])
            .map_err(|_| anyhow::anyhow!("initial footprint rotations must be quarter turns"))?;
        let sides = fp["courtyards"]
            .as_object()
            .context("courtyard inventory missing")?
            .keys()
            .cloned()
            .collect::<BTreeSet<_>>();
        for side in sides {
            let c = &mut fp["courtyards"][&side];
            let (outer, inner) = courtyard_bounds(c).map_err(|e| {
                let reason = c["reason"]
                    .as_str()
                    .filter(|s| !s.is_empty())
                    .map(|r| format!(" ({r})"))
                    .unwrap_or_default();
                anyhow::anyhow!("{reference} {side} courtyard not qualified: {e}{reason}")
            })?;
            c["polygons"] = polygons_value(&outer);
            if c.get("inner_polygons").is_some() {
                c["inner_polygons"] = polygons_value(&inner);
            }
        }
        for pad in fp["pads"].as_array().context("pad inventory missing")? {
            if !pad.is_object() {
                bail!("pad record must be an object");
            }
            let where_ = format!("{reference} pad {}", native_repr(&pad["number"]));
            let shape = pad["shape"].as_i64();
            let custom = pad
                .get("custom_geometry")
                .cloned()
                .unwrap_or_else(|| json!({}));
            if !custom.is_object() {
                bail!("{where_}: custom_geometry must be an object");
            }
            let qualified = shape == Some(6)
                && custom["status"] == "proven"
                && custom["primitives"]
                    .as_array()
                    .is_some_and(|v| !v.is_empty())
                && custom["merged_polygons_nm"]
                    .as_array()
                    .is_some_and(|v| !v.is_empty())
                && custom["bbox_nm"].as_array().is_some_and(|v| {
                    v.len() == 4
                        && v.iter().all(|n| integer(n).is_ok())
                        && integer(&v[0]).unwrap() < integer(&v[2]).unwrap()
                        && integer(&v[1]).unwrap() < integer(&v[3]).unwrap()
                });
            if custom["status"] == "proven" && !qualified {
                bail!("{where_}: custom_geometry not qualified");
            }
            if !shape.is_some_and(|s| [0, 1, 2, 4, 5].contains(&s)) && !qualified {
                bail!(
                    "{where_}: native pad shape {} not qualified for packing",
                    native_repr(&pad["shape"])
                );
            }
            rotation(&pad["rotation_deg"])
                .map_err(|_| anyhow::anyhow!("{where_}: pad rotation must be a quarter turn"))?;
            if outer_sides(pad)?.1 {
                copper_box(fp, pad)?;
            }
        }
        fp["packing_keepouts"] = json!([]);
        fp["packing_pad_keepouts"] = json!([]);
        if crate::constraints::access_hard(fp)? {
            let evaluated = crate::constraints::access_geometry(fp)?;
            let side = fp["side"].clone();
            fp["packing_keepouts"].as_array_mut().unwrap().push(json!({"side":side,"polygons":polygons_value(&evaluated.lanes),"name":format!("{reference} access lane")}));
        }
    }
    let mut board_keepouts = Vec::new();
    let mut board_pad_keepouts = Vec::new();
    let areas = raw.get("rule_areas").cloned().unwrap_or_else(|| json!([]));
    for area in areas.as_array().context("malformed rule-area inventory")? {
        let forbid = area["forbid"]
            .as_object()
            .context("malformed rule-area footprint flag")?;
        let footprints = forbid
            .get("footprints")
            .and_then(Value::as_bool)
            .context("malformed rule-area footprint flag")?;
        let pads = forbid
            .get("pads")
            .context("missing rule-area pad flag")?
            .as_bool()
            .context("malformed rule-area pad flag")?;
        if !footprints && !pads {
            continue;
        }
        let owner = area["owner"].as_str().context("unknown rule-area owner")?;
        let owner_index = raw["footprints"]
            .as_array()
            .unwrap()
            .iter()
            .position(|f| f["uuid"].as_str() == Some(owner));
        if owner != "board" && owner_index.is_none() {
            bail!("unknown rule-area owner");
        }
        let layers = area["layers"]
            .as_array()
            .context("malformed rule-area layers")?;
        let mut valid = BTreeSet::from(["F.Cu".to_owned(), "B.Cu".to_owned(), "*.Cu".to_owned()]);
        valid.extend((1..=30).map(|n| format!("In{n}.Cu")));
        let set: BTreeSet<String> = layers
            .iter()
            .map(|v| {
                v.as_str()
                    .map(str::to_owned)
                    .context("malformed rule-area layers")
            })
            .collect::<Result<_>>()?;
        if set.is_empty() || set.len() != layers.len() || !set.is_subset(&valid) {
            bail!("malformed rule-area layers");
        }
        let polygons = qualified_polygons(&area["outline"])?;
        let name = format!("rule area {}", area["uuid"].as_str().unwrap_or("None"));
        if pads {
            if owner == "board" {
                board_pad_keepouts.push(PadKeepout {
                    layers: copper_layers(&area["layers"])?,
                    polygons: polygons.clone(),
                    name: name.clone(),
                });
            } else {
                raw["footprints"][owner_index.unwrap()]["packing_pad_keepouts"].as_array_mut().unwrap().push(json!({"layers":area["layers"],"polygons":polygons_value(&polygons),"name":name}));
            }
        }
        if footprints {
            for side in ["F", "B"] {
                if set.contains("*.Cu") || set.contains(&format!("{side}.Cu")) {
                    if owner == "board" {
                        board_keepouts.push(Keepout {
                            side: side.into(),
                            polygons: polygons.clone(),
                            name: name.clone(),
                        });
                    } else {
                        raw["footprints"][owner_index.unwrap()]["packing_keepouts"].as_array_mut().unwrap().push(json!({"side":side,"polygons":polygons_value(&polygons),"name":name}));
                    }
                }
            }
        }
    }
    let footprints = raw["footprints"]
        .as_array()
        .unwrap()
        .iter()
        .map(|f| PreparedFootprint::from_value(f, setback.clone()))
        .collect::<Result<Vec<_>>>()?;
    if footprints.iter().any(|f| !f.holes.is_empty())
        && raw["footprints"]
            .as_array()
            .unwrap()
            .iter()
            .any(|f| f.get("packing_pad_geometry").is_none())
    {
        bail!("native packing target reservations missing");
    }
    Ok(QualifiedCapture {
        raw,
        outline,
        footprints,
        board_keepouts,
        board_pad_keepouts,
        setback,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    fn part() -> Value {
        json!({"ref":"U1","uuid":"u1","side":"B","position_nm":[10,20],"rotation_deg":0,
        "courtyards":{"B":{"status":"proven","polygons":[[[8,18],[12,18],[12,22],[8,22]]]}},
        "pads":[{"uuid":"p1","number":"1","net":"N","position_nm":[11,20],"rotation_deg":0,"shape":1,"size_nm":[3,2],"layers":["B.Cu"],"copper_centroid_offset_nm":[[1,2],[1,3]]}],
        "packing_pad_geometry":{"status":"proven","holes":[],"copper":[{"layers":["B.Cu"],"polygons":[[[10,19],[13,19],[13,22],[10,22]]]}]},
        "packing_keepouts":[{"side":"B","polygons":[[[15,18],[17,18],[17,22],[15,22]]],"name":"owned"}]})
    }
    #[test]
    fn prepared_pose_matches_materialized_geometry_at_every_turn() {
        let source = part();
        let fp = PreparedFootprint::from_value(&source, Some(1.into())).unwrap();
        for angle in [0, 90, 180, 270] {
            let at_origin = fp.at(&Point::zero(), angle).unwrap();
            let moved = at_origin.at(&Point::new(40, 50), angle).unwrap();
            let expected = PreparedFootprint::from_value(
                &transform_value(&source, &json!([40, 50]), angle).unwrap(),
                Some(1.into()),
            )
            .unwrap();
            assert_eq!(moved.courtyards, expected.courtyards);
            assert_eq!(moved.boxes, expected.boxes);
            assert_eq!(moved.copper_outline, expected.copper_outline);
            assert_eq!(moved.copper_sides, expected.copper_sides);
            assert_eq!(moved.targets, expected.targets);
            assert_eq!(moved.geometry_bounds, expected.geometry_bounds);
            assert_eq!(moved.pads[0].position, expected.pads[0].position);
            assert_eq!(moved.to_value().unwrap(), expected.to_value().unwrap());
            assert!(Arc::ptr_eq(&fp.raw, &moved.raw));
        }
        assert_eq!(source, part());
    }
    #[test]
    fn native_shape_bounds_follow_large_poses_and_rigid_macro_unions() {
        let mut source = part();
        source["packing_pad_geometry"]["holes"] = json!([
            {"polygons":[[[9,19],[10,19],[10,21],[9,21]]]},
            {"polygons":[[[13,18],[14,18],[14,19],[13,19]]]}
        ]);
        let fp = PreparedFootprint::from_value(&source, Some(1.into())).unwrap();
        let wide = Int::from(1) << 150_usize;
        let position = Point {
            x: wide.clone(),
            y: -wide,
        };
        for rotation in [0, 90, 180, 270] {
            let moved = fp
                .at(&Point::zero(), rotation)
                .unwrap()
                .at(&position, rotation)
                .unwrap();
            let expected = PreparedFootprint::from_value(
                &transform_value(&source, &point_value(&position), rotation).unwrap(),
                Some(1.into()),
            )
            .unwrap();
            assert_eq!(moved.geometry_bounds, expected.geometry_bounds);
            let other = moved
                .at(&position.plus(&Point::new(4, 0)), rotation)
                .unwrap();
            let block = PreparedFootprint::from_members("union", &[moved, other]).unwrap();
            assert_eq!(
                block.geometry_bounds.holes,
                PolygonBounds::new(&block.holes).unwrap()
            );
            assert_eq!(
                block.geometry_bounds.targets,
                PolygonBounds::new(&block.targets).unwrap()
            );
            let rotated = block.at(&Point::new(-4, 7), rotation).unwrap();
            assert_eq!(
                rotated.geometry_bounds.holes,
                PolygonBounds::new(&rotated.holes).unwrap()
            );
            assert_eq!(
                rotated.geometry_bounds.targets,
                PolygonBounds::new(&rotated.targets).unwrap()
            );
        }
    }
    #[test]
    fn rational_copper_rounds_outward_and_circle_uses_x_diameter() {
        let fp = part();
        let pad = &fp["pads"][0];
        assert_eq!(
            copper_box(&fp, pad).unwrap().unwrap(),
            Bounds {
                x0: 10.into(),
                y0: 19.into(),
                x1: 13.into(),
                y1: 22.into()
            }
        );
        let mut circle = pad.clone();
        circle["shape"] = json!(0);
        circle["rotation_deg"] = json!(90);
        circle["size_nm"] = json!([2, 80]);
        circle["copper_centroid_offset_nm"] = json!([[0, 1], [0, 1]]);
        assert_eq!(
            copper_box(&fp, &circle).unwrap().unwrap(),
            Bounds {
                x0: 10.into(),
                y0: 19.into(),
                x1: 12.into(),
                y1: 21.into()
            }
        );
    }
    #[test]
    fn qualification_rejects_unproven_hole_targets_and_forged_custom_box() {
        let mut fp = part();
        fp["pads"][0]["attribute"] = json!("PTH");
        let mut capture = json!({"schema":"agent-ee.placement-probe/1","board_sha256":"a".repeat(64),"pcbnew_version":"10.0.7","max_error_nm":5,"outline":{"status":"proven","polygons":[[[0,0],[100,0],[100,100],[0,100]]]},"footprints":[fp]});
        assert!(
            qualify_capture(&capture)
                .unwrap_err()
                .to_string()
                .contains("drill reservations incomplete")
        );
        capture["footprints"][0]["pads"][0]
            .as_object_mut()
            .unwrap()
            .remove("attribute");
        capture["footprints"][0]["pads"][0]["shape"] = json!(6);
        capture["footprints"][0]["pads"][0]["custom_geometry"] = json!({"status":"proven","bbox_nm":[0,0,0,5],"primitives":[["gr_rect"]],"merged_polygons_nm":[[[0,0],[1,0],[1,1],[0,1]]]});
        assert!(
            qualify_capture(&capture)
                .unwrap_err()
                .to_string()
                .contains("custom_geometry not qualified")
        );
    }
    #[test]
    fn rigid_macro_joins_touching_member_contours_without_requalifying_union() {
        let left = PreparedFootprint::from_value(&part(), Some(1.into())).unwrap();
        let right = left.at(&Point::new(14, 20), 0).unwrap();
        let block = PreparedFootprint::from_members("block", &[left, right]).unwrap();
        assert_eq!(block.courtyards["B"].len(), 2);
        assert_eq!(block.boxes["B"].x1, 16.into());
        assert_eq!(block.targets.len(), 4);
        assert!(block.pads.is_empty());
        assert_eq!(block.rotation, 0);
        assert!(qualified_polygons(&block.raw["courtyards"]["B"]).is_err());
    }
    #[test]
    fn untouched_native_rotation_representation_is_preserved() {
        let mut source = part();
        source["rotation_deg"] = json!(0.0);
        source["pads"][0]["rotation_deg"] = json!(0.0);
        let fixed = PreparedFootprint::from_value(&source, Some(1.into())).unwrap();
        assert_eq!(fixed.rotation_json, json!(0.0));
        assert_eq!(fixed.to_value().unwrap(), source);
        let moved = fixed.at(&Point::new(40, 50), 0).unwrap();
        assert_eq!(moved.rotation_json, json!(0));
        assert_eq!(moved.to_value().unwrap()["rotation_deg"], json!(0));
    }
    #[test]
    fn noninteger_drill_facts_do_not_silently_remove_hole_reservations() {
        for size in [
            json!([0.5, 0]),
            json!(["unqualified", 0]),
            json!([true, 0]),
            json!("unqualified"),
            json!({"unqualified":0}),
        ] {
            assert!(drilled(&json!({"drill_size_nm":size})));
        }
        assert!(!drilled(&json!({"drill_size_nm":[0,0]})));
        let mut fp = part();
        fp.as_object_mut().unwrap().remove("packing_pad_geometry");
        fp["pads"][0]["drill_size_nm"] = json!([0.5, 0]);
        let capture = json!({"schema":"agent-ee.placement-probe/1","board_sha256":"a".repeat(64),"pcbnew_version":"10.0.7","outline":{"status":"proven","polygons":[[[0,0],[100,0],[100,100],[0,100]]]},"footprints":[fp]});
        assert_eq!(
            qualify_capture(&capture).unwrap_err().to_string(),
            "U1: native packing drill reservations missing"
        );
    }
}
