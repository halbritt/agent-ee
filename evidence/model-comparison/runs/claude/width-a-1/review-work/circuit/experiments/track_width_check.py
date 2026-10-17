#!/usr/bin/env python3
"""Independent track-width check against inputs/project/rc_filter.kicad_pcb.
Requirement (inputs/requirements.txt): every saved copper track segment width >= 0.20 mm.
Parses the native s-expression text directly (no pcbnew dependency)."""
import re, sys, json

path = "/candidate/project/rc_filter.kicad_pcb"
text = open(path).read()

# segments: (segment (start x y) (end x y) (width w) (layer "L") (net "N") ...)
seg_re = re.compile(
    r'\(segment\s*\(start ([\d.-]+) ([\d.-]+)\)\s*\(end ([\d.-]+) ([\d.-]+)\)\s*'
    r'\(width ([\d.]+)\)\s*\(layer "([^"]+)"\)\s*\(net "([^"]+)"\)\s*\(uuid "([^"]+)"\)',
    re.S)
segs = seg_re.findall(text)
via_re = re.compile(r'\(via\s*\(at ([\d.-]+) ([\d.-]+)\)\s*\(size ([\d.]+)\)\s*\(drill ([\d.]+)\)', re.S)
vias = via_re.findall(text)
zone_min = re.findall(r'\(min_thickness ([\d.]+)\)', text)

REQ = 0.20
report = {"requirement_mm": REQ, "segments": [], "violations": [], "vias": [], "zone_min_thickness_mm": zone_min}
for s in segs:
    w = float(s[4])
    entry = {"start": [float(s[0]), float(s[1])], "end": [float(s[2]), float(s[3])],
             "width_mm": w, "layer": s[5], "net": s[6], "uuid": s[7], "pass": w >= REQ}
    report["segments"].append(entry)
    if w < REQ:
        report["violations"].append(entry)
for v in vias:
    report["vias"].append({"at": [float(v[0]), float(v[1])], "size_mm": float(v[2]), "drill_mm": float(v[3])})

report["segment_count"] = len(segs)
report["all_pass"] = not report["violations"]
print(json.dumps(report, indent=1))
