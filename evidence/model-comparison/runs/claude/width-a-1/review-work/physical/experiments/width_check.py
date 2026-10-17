import re, json, sys
src = open('/candidate/project/rc_filter.kicad_pcb').read()
# Extract all (segment ...) blocks at top level (no arcs present; also check)
segs = re.findall(r'\(segment\s(.*?)\n\t\)', src, re.S)
arcs = re.findall(r'\(arc\s', src)
out = []
for s in segs:
    w = float(re.search(r'\(width ([\d.]+)\)', s).group(1))
    net = re.search(r'\(net "([^"]+)"\)', s).group(1)
    layer = re.search(r'\(layer "([^"]+)"\)', s).group(1)
    st = re.search(r'\(start ([\d.]+) ([\d.]+)\)', s).groups()
    en = re.search(r'\(end ([\d.]+) ([\d.]+)\)', s).groups()
    uuid = re.search(r'\(uuid "([^"]+)"\)', s).group(1)
    out.append(dict(uuid=uuid, net=net, layer=layer, start=st, end=en, width_mm=w, meets_0p20=w>=0.20))
print(json.dumps({"segment_count": len(out), "arc_count": len(arcs),
                  "min_width_mm": min(o['width_mm'] for o in out),
                  "all_ge_0.20": all(o['meets_0p20'] for o in out),
                  "segments": out}, indent=1))
