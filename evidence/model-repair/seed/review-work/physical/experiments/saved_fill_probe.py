#!/usr/bin/python3
"""Test original saved fill at the GND connector's copper annulus and outward rays."""
import json
from pathlib import Path
import pcbnew
b=pcbnew.LoadBoard('/candidate/project/rc_filter.kicad_pcb')
z=list(b.Zones())[0]
p=z.GetFilledPolysList(pcbnew.B_Cu)
rays=[]
for sx,sy in ((1,1),(1,-1),(-1,1),(-1,-1)):
    samples=[]
    for i in range(46):
        offset=.45+i*.01
        x,y=100+sx*offset,105.08+sy*offset
        samples.append(dict(position_mm=[x,y],in_saved_fill=p.Contains(
            pcbnew.VECTOR2I(pcbnew.FromMM(x),pcbnew.FromMM(y)))))
    rays.append(dict(direction=[sx,sy],samples=samples,
                     all_in_saved_fill=all(q['in_saved_fill'] for q in samples)))
result=dict(zone_uuid=z.m_Uuid.AsString(),pad='J1.3',pad_center_mm=[100,105.08],
            pad_radius_mm=.85,drill_radius_mm=.5,
            first_sample_radius_mm=2**.5*.45,
            explanation='First sample lies on the copper annulus; later samples run diagonally through thermal spokes into plane. This is a sampled geometry check, not field or manufacturing verification.',
            rays=rays)
Path('/work/experiments/saved-fill-probe.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps([dict(direction=r['direction'],all_in_saved_fill=r['all_in_saved_fill']) for r in rays]))
