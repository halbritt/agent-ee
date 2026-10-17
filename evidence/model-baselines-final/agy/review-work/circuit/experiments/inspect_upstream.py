import subprocess
import xml.etree.ElementTree as ET

def dump_xml(sch_path, xml_path, out_f):
    subprocess.run(["kicad-cli", "sch", "export", "netlist", "--format", "kicadxml", "-o", xml_path, sch_path], check=True)
    tree = ET.parse(xml_path)
    root = tree.getroot()
    out_f.write(f"=== {sch_path} ===\n")
    for comp in root.findall(".//comp"):
        ref = comp.get("ref")
        val = comp.findtext("value")
        fields = {f.get("name"): (f.text or "") for f in comp.findall(".//field")}
        out_f.write(f"  COMP {ref}: val={val} fields={fields}\n")
    for net in root.findall(".//net"):
        nname = net.get("name")
        nodes = [f"{n.get('ref')}.{n.get('pin')}[{n.get('pinfunction','')}]" for n in net.findall("node")]
        if not nname.startswith("unconnected-") or len(nodes) > 1:
            out_f.write(f"  NET {nname}: " + ", ".join(nodes) + "\n")

with open("/work/experiments/refs/upstream_nets.txt", "w") as out_f:
    for name in ["wideband_controller", "wideband_board_carrier", "polygonus_psu", "polygonus_triggers", "polygonus_ign4", "polygonus_lowside_quad", "polygonus_mcu"]:
        dump_xml(f"/work/experiments/refs/{name}.kicad_sch", f"/work/experiments/refs/{name}.xml", out_f)

print("Done!")
