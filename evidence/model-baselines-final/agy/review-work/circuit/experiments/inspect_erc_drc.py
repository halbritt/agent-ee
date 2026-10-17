#!/usr/bin/env python3
"""
Inspect ERC and DRC JSON reports from /evidence/work/erc/erc.json and /evidence/work/drc/drc.json.
"""
import json
import sys

def main():
    print(f"Python version: {sys.version}")
    with open("/evidence/work/erc/erc.json") as f:
        erc = json.load(f)

    print("=== ERC SUMMARY ===")
    for sheet in erc.get("sheets", []):
        print("Sheet:", sheet.get("path"), "uuid:", sheet.get("uuid_path"))
        for v in sheet.get("violations", []):
            sev = v.get("severity")
            vtype = v.get("type")
            desc = v.get("description")
            if sev == "error" or vtype not in ("endpoint_off_grid", "lib_symbol_mismatch"):
                print(f"  [{sev}] {vtype}: {desc}")
                for item in v.get("items", []):
                    idesc = item.get("description")
                    pos = item.get("pos", {})
                    print(f"    - {idesc} @ ({pos.get('x')}, {pos.get('y')}) uuid={item.get('uuid')}")

    with open("/evidence/work/drc/drc.json") as f:
        drc = json.load(f)

    print("\n=== DRC SUMMARY ===")
    for section in ["violations", "unconnected_items", "schematic_parity"]:
        items_list = drc.get(section, [])
        print(f"Section: {section} ({len(items_list)})")
        for v in items_list:
            sev = v.get("severity")
            vtype = v.get("type")
            desc = v.get("description")
            if sev == "error" or vtype not in ("footprint_symbol_field_mismatch", "lib_footprint_mismatch"):
                print(f"  [{sev}] {vtype}: {desc}")
                for item in v.get("items", []):
                    idesc = item.get("description")
                    pos = item.get("pos", {})
                    print(f"    - {idesc} @ ({pos.get('x')}, {pos.get('y')}) uuid={item.get('uuid')}")

if __name__ == "__main__":
    main()
