#!/usr/bin/env python3
"""Verify schematic and saved PCB connectivity against external connector contract."""
import json
import math
import xml.etree.ElementTree as ET

def main():
    with open("/evidence/work/netlist/connectivity.json", "r", encoding="utf-8") as f:
        conn = json.load(f)
    with open("/evidence/work/geometry/geometry.json", "r", encoding="utf-8") as f:
        geom = json.load(f)
    with open("/evidence/work/erc/erc.json", "r", encoding="utf-8") as f:
        erc = json.load(f)
    with open("/evidence/work/drc/drc.json", "r", encoding="utf-8") as f:
        drc = json.load(f)

    # Check netlist connectivity
    expected_nets = {
        "/IN": ["J1.1", "R1.1"],
        "/OUT": ["C1.1", "J1.2", "R1.2"],
        "GND": ["C1.2", "J1.3"],
    }
    actual_nets = {k: sorted(v) for k, v in conn["nets"].items()}
    netlist_match = actual_nets == expected_nets

    # Check PCB pad net assignments
    pad_nets = {f"{p['ref']}.{p['pad']}": p["net"] for p in geom["pads"]}
    pcb_pad_match = (
        pad_nets.get("J1.1") == "/IN"
        and pad_nets.get("R1.1") == "/IN"
        and pad_nets.get("J1.2") == "/OUT"
        and pad_nets.get("R1.2") == "/OUT"
        and pad_nets.get("C1.1") == "/OUT"
        and pad_nets.get("J1.3") == "GND"
        and pad_nets.get("C1.2") == "GND"
    )

    # Analytical RC filter parameters
    r_ohm = 10e3
    c_farad = 100e-9
    tau_s = r_ohm * c_farad
    fc_hz = 1.0 / (2.0 * math.pi * r_ohm * c_farad)

    result = {
        "netlist_match": netlist_match,
        "actual_nets": actual_nets,
        "pcb_pad_match": pcb_pad_match,
        "pad_nets": pad_nets,
        "erc_violations": len(erc["sheets"][0]["violations"]),
        "drc_violations": len(drc["violations"]),
        "drc_unconnected_items": len(drc["unconnected_items"]),
        "drc_schematic_parity": len(drc["schematic_parity"]),
        "rc_parameters": {
            "R1_ohm": r_ohm,
            "C1_farad": c_farad,
            "tau_s": tau_s,
            "fc_hz": round(fc_hz, 4),
        },
    }
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
