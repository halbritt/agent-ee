import json
import xml.etree.ElementTree as ET
from pathlib import Path

with open("/evidence/work/netlist/connectivity.json") as f:
    conn = json.load(f)

print(f"Components count: {len(conn['components'])}")
print(f"Nets count: {len(conn['nets'])}")

# Group components by sheet
by_sheet = {}
for ref, data in sorted(conn["components"].items()):
    by_sheet.setdefault(data["sheet"], []).append((ref, data["value"], data["libsource"]["lib"] + ":" + data["libsource"]["part"], data["footprint"], data["dnp"]))

for sheet, comps in sorted(by_sheet.items()):
    print(f"\n=== Sheet: {sheet} ({len(comps)} components) ===")
    for ref, val, part, fp, dnp in comps:
        dnp_str = " [DNP]" if dnp else ""
        print(f"  {ref:6s}: {val:25s} | {part:35s} | {fp}{dnp_str}")
