#!/usr/bin/env python3
"""
Parse /evidence/work/netlist/design.xml to extract full component metadata,
fields (LCSC, MPN, Datasheet, Value, Footprint), library pin definitions,
and net-by-net pin connections with pin names and types.
"""
import xml.etree.ElementTree as ET

def main():
    tree = ET.parse("/evidence/work/netlist/design.xml")
    root = tree.getroot()

    print("=== SHEETS ===")
    for sheet in root.findall("./design/sheet"):
        num = sheet.get("number")
        name = sheet.get("name")
        tst = sheet.get("tstamps")
        src = sheet.find("./title_block/source")
        src_text = src.text if src is not None else ""
        print(f"Sheet {num}: {name} (tstamps={tst}) -> {src_text}")

    print("\n=== COMPONENTS ===")
    comps = {}
    for comp in root.findall("./components/comp"):
        ref = comp.get("ref")
        val = comp.findtext("value", "")
        fp = comp.findtext("footprint", "")
        ds = comp.findtext("datasheet", "")
        lib = comp.find("libsource")
        lib_str = f"{lib.get('lib')}:{lib.get('part')}" if lib is not None else ""
        props = {}
        for prop in comp.findall("property"):
            pname = prop.get("name")
            pval = prop.get("value")
            if pname not in ("Sheetname", "Sheetfile", "ki_ description", "ki_keywords", "ki_fp_filters"):
                props[pname] = pval
        fields = {}
        for f in comp.findall("./fields/field"):
            fields[f.get("name")] = f.text
        comps[ref] = {
            "val": val, "fp": fp, "ds": ds, "lib": lib_str, "props": props, "fields": fields
        }
        print(f"{ref:6s} | val={val:22s} | lib={lib_str:32s} | fp={fp:40s} | fields={fields} | dnp={'dnp' in props}")

    print("\n=== LIBPARTS PIN DEFINITIONS ===")
    for lp in root.findall("./libparts/libpart"):
        lib = lp.get("lib")
        part = lp.get("part")
        pins = []
        for p in lp.findall("./pins/pin"):
            pins.append(f"{p.get('num')}:{p.get('name')}({p.get('type')})")
        print(f"{lib}:{part} -> {', '.join(pins)}")

    print("\n=== NETS WITH PIN DETAILS ===")
    for net in root.findall("./nets/net"):
        code = net.get("code")
        name = net.get("name")
        nodes = []
        for node in net.findall("node"):
            ref = node.get("ref")
            pin = node.get("pin")
            pfunc = node.get("pinfunction", "")
            ptype = node.get("pintype", "")
            nodes.append(f"{ref}.{pin}[{pfunc}:{ptype}]")
        print(f"Net {code:3s} {name:35s}: {', '.join(nodes)}")

if __name__ == "__main__":
    main()
