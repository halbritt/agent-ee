import xml.etree.ElementTree as ET

print("==========================================================")
print("1. WIDEBAND MODULE (wideband_controller.xml) NETS & PINS")
print("==========================================================")
wb_tree = ET.parse("/work/experiments/ref_wideband/wideband_controller.xml")
wb_root = wb_tree.getroot()
for comp in wb_root.findall(".//comp"):
    ref = comp.attrib["ref"]
    val = comp.findtext("value", "")
    fp = comp.findtext("footprint", "")
    print(f"  {ref}: {val} ({fp})")

print("\nWideband Nets:")
for net in wb_root.findall(".//net"):
    name = net.attrib["name"]
    nodes = [f"{n.attrib['ref']}.{n.attrib['pin']}({n.attrib.get('pinfunction','')})" for n in net.findall("node")]
    print(f"  {name}: {', '.join(nodes)}")

print("\n==========================================================")
print("2. WIDEBAND FIRMWARE (io_pins.h, port.cpp, can.cpp)")
print("==========================================================")
for fn in ["io_pins.h", "port.cpp", "wideband_board_config.h", "can.cpp"]:
    p = f"/work/experiments/ref_wideband/fw/{fn}"
    print(f"\n--- {fn} ---")
    with open(p) as f:
        print(f.read()[:4000])

print("\n==========================================================")
print("3. POLYGONUS MODULE (polygonus.xml) MODULE EDGE PINS")
print("==========================================================")
poly_tree = ET.parse("/work/experiments/ref_polygonus/polygonus.xml")
poly_root = poly_tree.getroot()
for comp in poly_root.findall(".//comp"):
    ref = comp.attrib["ref"]
    val = comp.findtext("value", "")
    fp = comp.findtext("footprint", "")
    if ref.startswith(("J", "U", "R", "Q", "D")):
        print(f"  {ref}: {val} ({fp})")

print("\nPolygonus J1/J2/Module Connector Nets:")
for net in poly_root.findall(".//net"):
    name = net.attrib["name"]
    nodes = [f"{n.attrib['ref']}.{n.attrib['pin']}({n.attrib.get('pinfunction','')})" for n in net.findall("node")]
    # print nets that touch module connector or CAN or LS or IGN or 3V3 or VR
    if any(k in name.upper() for k in ["LS", "IGN", "CAN", "3V3", "3.3V", "5V", "VR", "DIG", "ETB"]) or any(n.startswith(("J1.", "J2.", "U1.")) for n in nodes):
        print(f"  {name}: {', '.join(nodes)}")
