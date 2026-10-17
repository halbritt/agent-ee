import re, json, sys
src = open('/candidate/project/rc_filter.kicad_pcb').read()
# raw s-expression parse of (segment ...) blocks
segs = re.findall(r'\(segment\s(.*?)\n\t\)', src, re.S)
out = []
for s in segs:
    w = float(re.search(r'\(width ([\d.]+)\)', s).group(1))
    net = re.search(r'\(net "([^"]+)"\)', s).group(1)
    uuid = re.search(r'\(uuid "([^"]+)"\)', s).group(1)
    layer = re.search(r'\(layer "([^"]+)"\)', s).group(1)
    out.append(dict(uuid=uuid, net=net, layer=layer, width_mm=w, pass_030=(w>=0.30)))
print(json.dumps(out, indent=1))
print("segments:", len(out), "failing(<0.30mm):", sum(1 for o in out if not o['pass_030']))
