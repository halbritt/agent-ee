import json

with open("/evidence/work/netlist/connectivity.json") as f:
    sch = json.load(f)

with open("/evidence/work/geometry/geometry.json") as f:
    pcb = json.load(f)

sch_comps = sch["components"]
pcb_fps = {fp["ref"]: fp for fp in pcb["footprints"]}

print("In SCH but not PCB:", set(sch_comps.keys()) - set(pcb_fps.keys()))
print("In PCB but not SCH:", set(pcb_fps.keys()) - set(sch_comps.keys()))

# Compare pad nets between schematic and PCB
sch_pad_net = {}
for net, pins in sch["nets"].items():
    for p in pins:
        sch_pad_net[p] = net

pcb_pad_net = {}
for ref, fp in pcb_fps.items():
    for pad in fp["pads"]:
        key = f"{ref}.{pad['pad']}"
        pcb_pad_net.setdefault(key, set()).add(pad["net"])

for key in sorted(set(sch_pad_net.keys()) | set(pcb_pad_net.keys())):
    s_net = sch_pad_net.get(key)
    p_nets = pcb_pad_net.get(key)
    if p_nets is None:
        print(f"Pad {key} in SCH ({s_net}) but not in PCB")
    elif s_net is None:
        print(f"Pad {key} in PCB ({p_nets}) but not in SCH")
    elif p_nets != {s_net}:
        print(f"Pad {key} net mismatch: SCH={s_net} vs PCB={p_nets}")
