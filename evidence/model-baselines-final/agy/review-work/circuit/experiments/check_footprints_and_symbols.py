#!/usr/bin/env python3
"""
Compare schematic connectivity (from /evidence/work/netlist/connectivity.json)
with board pad connectivity and coordinates (from /evidence/work/geometry/geometry.json)
and inspect footprint pad mappings.
"""
import json

def main():
    with open("/evidence/work/netlist/connectivity.json") as f:
        conn = json.load(f)
    with open("/evidence/work/geometry/geometry.json") as f:
        geom = json.load(f)

    sch_comps = conn["components"]
    sch_nets = conn["nets"]

    # Build sch pin -> net map
    sch_pin_net = {}
    for net, pins in sch_nets.items():
        for p in pins:
            ref, pad = p.split(".", 1)
            sch_pin_net[(ref, pad)] = net

    pcb_fps = {fp["ref"]: fp for fp in geom["footprints"]}

    print(f"Schematic components: {len(sch_comps)}, PCB footprints: {len(pcb_fps)}")
    missing_on_pcb = set(sch_comps.keys()) - set(pcb_fps.keys())
    extra_on_pcb = set(pcb_fps.keys()) - set(sch_comps.keys())
    print("Missing on PCB:", missing_on_pcb)
    print("Extra on PCB:", extra_on_pcb)

    print("\n=== CHECKING PAD NET PARITY & MULTIPLE PADS ===")
    for ref, fp in sorted(pcb_fps.items()):
        pads = fp.get("pads", [])
        by_num = {}
        for p in pads:
            by_num.setdefault(p["pad"], []).append(p)
        for pnum, plist in sorted(by_num.items()):
            pcb_nets = set(x["net"] for x in plist)
            sch_net = sch_pin_net.get((ref, pnum))
            if len(pcb_nets) > 1 or (sch_net is not None and list(pcb_nets)[0] != sch_net):
                print(f"  MISMATCH {ref} pad '{pnum}': PCB nets={pcb_nets}, SCH net={sch_net}")
            elif sch_net is None and pnum != "" and list(pcb_nets)[0] not in ("", "<no net>"):
                print(f"  EXTRA PCB PAD {ref} pad '{pnum}': PCB nets={pcb_nets}, not in SCH")

    print("\n=== CHECKING SCH PINS MISSING ON PCB ===")
    for (ref, pnum), snet in sorted(sch_pin_net.items()):
        if ref in pcb_fps:
            pcb_pnums = set(p["pad"] for p in pcb_fps[ref].get("pads", []))
            if pnum not in pcb_pnums:
                print(f"  MISSING PCB PAD {ref}.{pnum} (sch net={snet}), available PCB pads={sorted(pcb_pnums)}")

if __name__ == "__main__":
    main()
