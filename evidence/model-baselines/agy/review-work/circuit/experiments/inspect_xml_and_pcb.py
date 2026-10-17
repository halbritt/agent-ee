import xml.etree.ElementTree as ET
import json

tree = ET.parse("/evidence/work/netlist/design.xml")
root = tree.getroot()

print("=== Components with fields from design.xml ===")
for comp in root.findall(".//components/comp"):
    ref = comp.get("ref")
    val = comp.findtext("value")
    fp = comp.findtext("footprint")
    fields = {f.get("name"): f.text for f in comp.findall(".//fields/field")}
    props = {p.get("name"): p.get("value") for p in comp.findall("property")}
    dnp = "dnp" in [p.get("name") for p in comp.findall("property") if p.get("name") == "dnp"] or comp.find("fields/field[@name='DNP']") is not None
    # check libpart
    libsource = comp.find("libsource")
    lib = libsource.get("lib") if libsource is not None else ""
    part = libsource.get("part") if libsource is not None else ""
    print(f"{ref:6s} | val={val:22s} | lib={lib}:{part:25s} | fp={fp:45s} | fields={fields}")

print("\n=== Libparts pin definitions ===")
for lp in root.findall(".//libparts/libpart"):
    lib = lp.get("lib")
    part = lp.get("part")
    pins = [(p.get("num"), p.get("name"), p.get("type")) for p in lp.findall(".//pins/pin")]
    print(f"{lib}:{part} -> {pins}")
