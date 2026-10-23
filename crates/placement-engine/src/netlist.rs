//! Bounded native KiCad XML inventory. No native process or Python callback is used.
use anyhow::{Context, Result, bail, ensure};
use quick_xml::{Reader, events::Event};
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};

#[derive(Clone, Debug)]
pub struct NativeInventory {
    pub components: BTreeMap<String, Value>,
    pub nets: BTreeMap<String, Value>,
    pub sheet_names: BTreeMap<String, String>,
    pub sha256: String,
}

#[derive(Default, Debug)]
struct Node {
    name: String,
    attrs: BTreeMap<String, String>,
    text: String,
    children: Vec<Node>,
}
impl Node {
    fn all<'a>(&'a self, name: &str) -> impl Iterator<Item = &'a Node> {
        let name = name.to_owned();
        self.children.iter().filter(move |n| n.name == name)
    }
    fn one(&self, name: &str) -> Result<&Node> {
        let mut matches = self.all(name);
        let first = matches
            .next()
            .with_context(|| format!("expected one native {name}"))?;
        ensure!(matches.next().is_none(), "expected one native {name}");
        Ok(first)
    }
    fn attr(&self, key: &str) -> Result<&str> {
        let text = self
            .attrs
            .get(key)
            .with_context(|| format!("missing native {} {key}", self.name))?;
        ensure!(!text.trim().is_empty(), "empty native {} {key}", self.name);
        Ok(text)
    }
}

fn document(raw: &[u8]) -> Result<Node> {
    ensure!(raw.len() <= 8_000_000, "native XML byte limit exceeded");
    let text = std::str::from_utf8(raw)?.trim_start_matches('\u{feff}');
    let upper = text.to_ascii_uppercase();
    ensure!(
        !upper.contains("<!DOCTYPE") && !upper.contains("<!ENTITY"),
        "XML declarations/entities are not supported"
    );
    let mut reader = Reader::from_str(text);
    let mut stack = vec![Node::default()];
    let mut count = 0usize;
    loop {
        match reader.read_event()? {
            Event::Start(tag) | Event::Empty(tag) => {
                // Empty elements are distinguished by the exact event on the reader's source.
                let empty =
                    text.as_bytes().get(reader.buffer_position() as usize - 2) == Some(&b'/');
                count += 1;
                ensure!(
                    count <= 100_000 && stack.len() <= 33,
                    "native XML structure limit exceeded"
                );
                let mut node = Node {
                    name: std::str::from_utf8(tag.name().as_ref())?.to_owned(),
                    ..Node::default()
                };
                for attribute in tag.attributes() {
                    let attribute = attribute?;
                    let key = std::str::from_utf8(attribute.key.as_ref())?.to_owned();
                    let value = attribute
                        .decode_and_unescape_value(reader.decoder())?
                        .into_owned();
                    ensure!(
                        node.attrs.insert(key, value).is_none(),
                        "duplicate XML attribute"
                    );
                }
                if empty {
                    stack.last_mut().unwrap().children.push(node);
                } else {
                    stack.push(node);
                }
            }
            Event::End(_) => {
                ensure!(stack.len() > 1, "unbalanced XML");
                let node = stack.pop().unwrap();
                stack.last_mut().unwrap().children.push(node);
            }
            Event::Text(text) => {
                let encoded = std::str::from_utf8(text.as_ref())?;
                stack
                    .last_mut()
                    .unwrap()
                    .text
                    .push_str(&quick_xml::escape::unescape(encoded)?);
            }
            Event::CData(text) => stack
                .last_mut()
                .unwrap()
                .text
                .push_str(std::str::from_utf8(text.as_ref())?),
            Event::GeneralRef(reference) => {
                let name = reference.decode()?;
                let entity = format!("&{name};");
                stack
                    .last_mut()
                    .unwrap()
                    .text
                    .push_str(&quick_xml::escape::unescape(&entity)?);
            }
            Event::Eof => break,
            Event::DocType(_) => bail!("XML declarations/entities are not supported"),
            _ => {}
        }
    }
    ensure!(
        stack.len() == 1 && stack[0].children.len() == 1,
        "expected one XML root"
    );
    let root = stack.pop().unwrap().children.pop().unwrap();
    ensure!(
        root.name == "export" && root.attrs.get("version").map(String::as_str) == Some("E"),
        "unsupported native XML export schema"
    );
    for name in ["design", "components", "libparts", "nets"] {
        root.one(name)?;
    }
    Ok(root)
}

pub fn parse(raw: &[u8]) -> Result<NativeInventory> {
    let root = document(raw)?;
    let mut sheet_names = BTreeMap::new();
    let mut names = BTreeSet::new();
    for sheet in root.one("design")?.all("sheet") {
        let path = sheet.attr("tstamps")?;
        let name = sheet.attr("name")?;
        ensure!(
            path.starts_with('/')
                && path.ends_with('/')
                && !sheet_names.contains_key(path)
                && names.insert(name.to_owned()),
            "missing or ambiguous native hierarchy inventory"
        );
        sheet_names.insert(path.to_owned(), name.to_owned());
    }
    ensure!(
        sheet_names.contains_key("/"),
        "missing or ambiguous native hierarchy inventory"
    );
    let mut library = BTreeMap::<String, BTreeMap<String, Value>>::new();
    for part in root.one("libparts")?.all("libpart") {
        let id = format!("{}:{}", part.attr("lib")?, part.attr("part")?);
        let mut pins = BTreeMap::new();
        for pin in part.one("pins")?.all("pin") {
            let number = pin.attr("num")?.to_owned();
            let metadata = json!({"name": pin.attrs.get("name").map(String::as_str).unwrap_or(""), "electrical_type":pin.attr("type")?});
            ensure!(
                pins.insert(number, metadata).is_none(),
                "duplicate native library pin"
            );
        }
        ensure!(
            !pins.is_empty() && library.insert(id, pins).is_none(),
            "duplicate or empty native library part"
        );
    }
    let mut components = BTreeMap::new();
    let mut identities = BTreeSet::new();
    for comp in root.one("components")?.all("comp") {
        let reference = comp.attr("ref")?.to_owned();
        let sheet = comp.one("sheetpath")?.attr("tstamps")?;
        let uuid = comp.one("tstamps")?.text.clone();
        ensure!(
            !uuid.trim().is_empty()
                && sheet_names.contains_key(sheet)
                && !components.contains_key(&reference),
            "duplicate or undeclared native component identity"
        );
        for uid in uuid.split_whitespace() {
            ensure!(
                identities.insert((sheet.to_owned(), uid.to_owned())),
                "duplicate or undeclared native component identity"
            );
        }
        let lib = comp.one("libsource")?;
        let symbol = format!("{}:{}", lib.attr("lib")?, lib.attr("part")?);
        let metadata = library
            .get(&symbol)
            .context("missing native library inventory")?;
        let mut units = BTreeSet::new();
        let mut selected = BTreeSet::new();
        for unit in comp.one("units")?.all("unit") {
            ensure!(
                units.insert(unit.attr("name")?.to_owned()),
                "ambiguous native selected unit inventory"
            );
            let mut pins = BTreeSet::new();
            for pin in unit.one("pins")?.all("pin") {
                ensure!(
                    pins.insert(pin.attr("num")?.to_owned()),
                    "ambiguous native selected unit inventory"
                );
            }
            ensure!(!pins.is_empty(), "ambiguous native selected unit inventory");
            selected.extend(pins);
        }
        ensure!(
            !selected.is_empty() && selected.iter().all(|pin| metadata.contains_key(pin)),
            "missing native selected physical pin inventory"
        );
        let mut properties = Map::new();
        for prop in comp.all("property") {
            let name = prop.attr("name")?;
            ensure!(
                !properties.contains_key(name),
                "duplicate native component property"
            );
            if ["dnp", "exclude_from_board", "exclude_from_bom"].contains(&name) {
                ensure!(
                    prop.attrs.len() == 1
                        && prop.children.is_empty()
                        && prop.text.trim().is_empty(),
                    "unqualified native instance flag form"
                );
            }
            properties.insert(
                name.to_owned(),
                prop.attrs.get("value").map_or(Value::Null, |v| json!(v)),
            );
        }
        let footprints: Vec<_> = comp.all("footprint").collect();
        ensure!(
            footprints.len() <= 1
                && footprints
                    .iter()
                    .all(|p| p.attrs.is_empty() && p.children.is_empty()),
            "duplicate or structured native footprint"
        );
        let footprint = footprints.first().map(|f| f.text.as_str()).unwrap_or("");
        let fields: Vec<_> = comp.all("fields").collect();
        ensure!(fields.len() <= 1, "duplicate native field container");
        let mut field_names = BTreeSet::new();
        if let Some(fields) = fields.first() {
            for field in fields.all("field") {
                let name = field.attr("name")?;
                ensure!(field_names.insert(name), "duplicate native component field");
                if name == "Footprint" {
                    ensure!(
                        field.text == footprint,
                        "native Footprint field contradicts footprint element"
                    );
                }
            }
        }
        let mut bindings = Map::new();
        for name in ["Datasheet", "LCSC", "Manufacturer", "Partnumber"] {
            let mut values = Vec::<String>::new();
            if name == "Datasheet" {
                let nodes: Vec<_> = comp.all("datasheet").collect();
                ensure!(
                    nodes.len() <= 1
                        && nodes
                            .iter()
                            .all(|n| n.attrs.is_empty() && n.children.is_empty()),
                    "duplicate or structured native datasheet"
                );
                values.extend(nodes.iter().map(|n| n.text.clone()));
            }
            if let Some(container) = fields.first() {
                for field in container
                    .all("field")
                    .filter(|f| f.attrs.get("name").map(String::as_str) == Some(name))
                {
                    ensure!(
                        field.attrs.len() == 1 && field.children.is_empty(),
                        "ambiguous native {name} field"
                    );
                    values.push(field.text.clone());
                }
            }
            if properties.contains_key(name) {
                let prop = comp
                    .all("property")
                    .find(|p| p.attrs.get("name").map(String::as_str) == Some(name))
                    .unwrap();
                ensure!(
                    prop.attrs.len() == 2
                        && prop.attrs.contains_key("value")
                        && prop.children.is_empty()
                        && prop.text.trim().is_empty(),
                    "unqualified native {name} property"
                );
                values.push(prop.attrs["value"].clone());
            }
            ensure!(
                values
                    .first()
                    .is_none_or(|first| values.iter().all(|v| v == first)),
                "contradictory native {name} representations"
            );
            bindings.insert(
                name.into(),
                values.first().map_or(Value::Null, |v| json!(v)),
            );
        }
        let pins: Map<String, Value> = selected
            .iter()
            .map(|p| (p.clone(), metadata[p].clone()))
            .collect();
        components.insert(reference.clone(), json!({"reference":reference,"sheet":sheet,"uuid":uuid,
            "lib_id":symbol,"value":comp.one("value")?.text,"footprint":footprint,
            "populated":!properties.contains_key("dnp"),"on_board":!properties.contains_key("exclude_from_board"),
            "in_bom":!properties.contains_key("exclude_from_bom"),"properties":properties,
            "pins":selected,"units":units,"pin_metadata":pins,"datasheet":bindings["Datasheet"],"binding_fields":bindings}));
    }
    let mut nets = BTreeMap::new();
    let mut names = BTreeSet::new();
    let mut memberships = BTreeSet::new();
    for net in root.one("nets")?.all("net") {
        let id = net.attr("code")?.to_owned();
        let name = net.attr("name")?;
        ensure!(
            !nets.contains_key(&id) && names.insert(name.to_owned()),
            "duplicate native net identity"
        );
        let nodes: Vec<_> = net.all("node").collect();
        ensure!(!nodes.is_empty(), "empty net or duplicate terminal");
        let mut terminals = Vec::new();
        for node in &nodes {
            let reference = node.attr("ref")?;
            let pin = node.attr("pin")?;
            let component = components
                .get(reference)
                .context("unknown component or physical pin")?;
            ensure!(
                component["pin_metadata"].get(pin).is_some(),
                "unknown component or physical pin"
            );
            ensure!(
                memberships.insert((reference.to_owned(), pin.to_owned())),
                "physical pin belongs to multiple nets"
            );
            terminals.push(json!([reference, pin]));
        }
        let no_connect = nodes.len() == 1
            && nodes[0]
                .attrs
                .get("pintype")
                .map(|s| s.split('+').skip(1).any(|p| p == "no_connect"))
                .unwrap_or(false);
        nets.insert(id,json!({"name":name,"terminals":terminals,"no_connect_marker":no_connect,
            "auto_unconnected_singleton":nodes.len()==1 && name.starts_with("unconnected-(") && name.ends_with(')')}));
    }
    ensure!(!components.is_empty(), "empty component inventory");
    ensure!(
        components
            .values()
            .map(|c| c["pins"].as_array().unwrap().len())
            .sum::<usize>()
            <= 8192,
        "terminal inventory limit exceeded"
    );
    ensure!(
        components.len() <= 1024,
        "component inventory limit exceeded"
    );
    ensure!(nets.len() <= 2048, "net inventory limit exceeded");
    Ok(NativeInventory {
        components,
        nets,
        sheet_names,
        sha256: format!("{:x}", Sha256::digest(raw)),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn xml_text_entities_remain_text_and_unknown_entities_refuse() {
        let raw=br#"<export version="E"><design><source>A&amp;B&lt;C&gt;&#34;&#x27;<![CDATA[&raw]]></source></design><components/><libparts/><nets/></export>"#;
        let root = document(raw).unwrap();
        assert_eq!(
            root.one("design").unwrap().one("source").unwrap().text,
            "A&B<C>\"'&raw"
        );
        let bad = String::from_utf8(raw.to_vec())
            .unwrap()
            .replace("&amp;", "&undeclared;");
        assert!(document(bad.as_bytes()).is_err());
        let declared = String::from_utf8(raw.to_vec())
            .unwrap()
            .replace("<export", "<!DOCTYPE export><export");
        assert!(document(declared.as_bytes()).is_err());
    }
}
