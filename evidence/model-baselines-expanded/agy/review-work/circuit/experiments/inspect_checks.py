#!/usr/bin/env python3
import json
import sys
from collections import defaultdict

with open('/evidence/work/erc/erc.json') as f:
    erc = json.load(f)

print("=== ERC SUMMARY ===")
print("KiCad version:", erc.get("kicad_version"))
for sheet in erc.get("sheets", []):
    path = sheet.get("path")
    v_by_type = defaultdict(list)
    for v in sheet.get("violations", []):
        v_by_type[v["type"]].append(v)
    print(f"\nSheet: {path} (total {len(sheet.get('violations', []))})")
    for t, vs in sorted(v_by_type.items()):
        print(f"  {t}: {len(vs)} ({vs[0]['severity']})")
        if t not in ("endpoint_off_grid", "lib_symbol_mismatch"):
            for item in vs:
                print(f"    - [{item['severity']}] {item['description']}")
                for el in item.get("items", []):
                    print(f"        * {el.get('description')} @ ({el.get('pos', {}).get('x')}, {el.get('pos', {}).get('y')}) uuid={el.get('uuid')}")

with open('/evidence/work/drc/drc.json') as f:
    drc = json.load(f)

print("\n=== DRC SUMMARY ===")
for section in ["violations", "unconnected_items", "schematic_parity"]:
    items = drc.get(section, [])
    v_by_type = defaultdict(list)
    for v in items:
        v_by_type[v["type"]].append(v)
    print(f"\nSection: {section} (total {len(items)})")
    for t, vs in sorted(v_by_type.items()):
        print(f"  {t}: {len(vs)} ({vs[0]['severity']})")
        if t not in ("footprint_symbol_field_mismatch", "lib_footprint_mismatch"):
            for item in vs:
                print(f"    - [{item['severity']}] {item['description']}")
                for el in item.get("items", []):
                    print(f"        * {el.get('description')} @ ({el.get('pos', {}).get('x')}, {el.get('pos', {}).get('y')}) uuid={el.get('uuid')}")
