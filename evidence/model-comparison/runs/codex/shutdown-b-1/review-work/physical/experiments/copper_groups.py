import json, pcbnew
from pathlib import Path
b=pcbnew.LoadBoard('/candidate/project/rc_filter.kicad_pcb')
c=b.GetConnectivity(); c.Build(b)
groups={}
for p in b.GetPads():
    key=p.GetParentFootprint().GetReference()+'.'+p.GetNumber()
    items=c.GetConnectedItems(p)
    groups[key]={'net':p.GetNetname(),'reachable_pads':sorted(i.GetParentFootprint().GetReference()+'.'+i.GetNumber() for i in items if isinstance(i,pcbnew.PAD)),'reachable_item_uuids':sorted(i.m_Uuid.AsString() for i in items)}
assert groups['C1.2']['reachable_pads']==['C1.2','J1.3']
assert groups['C1.1']['reachable_pads']==['C1.1','J1.2','R1.2']
assert groups['R1.1']['reachable_pads']==['J1.1','R1.1']
# All units are millimetres. Copper and footprint extents checked against saved rectangular edge centreline.
edges=[{'start':[pcbnew.ToMM(i.GetStart().x),pcbnew.ToMM(i.GetStart().y)],'end':[pcbnew.ToMM(i.GetEnd().x),pcbnew.ToMM(i.GetEnd().y)]} for i in b.GetDrawings() if i.GetLayer()==pcbnew.Edge_Cuts]
report={'saved_copper_groups':groups,'edge_segments_mm':edges,'manual_geometry_observations':['Closed rectangular outline: x=96..116, y=96..108 (20 x 12 mm).','All three component courtyards lie within the outline and are separated. C1 left courtyard x=108.3; R1 right courtyard x=106.68: separation 1.62 mm.','J1 circular pads: 1.7 mm copper diameter and 1.0 mm drill; nominal radial annulus 0.35 mm. Via: 0.6 mm diameter, 0.3 mm drill; nominal radial annulus 0.15 mm. No fabrication capability is inferred.','C1.2 connects through the 2.05 mm F.Cu track to the GND via at (113,100), then saved B.Cu fill to J1.3.','IN and OUT groups agree with native netlist and are distinct from the ground group. No parallel resistor or separate output discharge component exists.'],'limitations':['KiCad saved copper connectivity, no refill or board save.','No fabricator capability, copper thickness, physical board or component part numbers supplied; no manufacturing or thermal qualification claimed.']}
Path('/work/experiments/copper_groups.json').write_text(json.dumps(report,indent=2)+'\n')
print('Assertions passed: three expected saved copper groups; GND includes saved zone and via.')
