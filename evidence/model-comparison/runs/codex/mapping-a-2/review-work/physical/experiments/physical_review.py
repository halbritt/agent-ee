"""Read-only saved copper contact graph; intentionally do not filter contacts by net."""
import hashlib
import itertools
import json
import platform
from pathlib import Path
import xml.etree.ElementTree as ET
import pcbnew

OUT = Path('/work/experiments')
board_path = Path('/candidate/project/rc_filter.kicad_pcb')
b = pcbnew.LoadBoard(str(board_path))
layers = [pcbnew.F_Cu, pcbnew.B_Cu]
mm = lambda p: [pcbnew.ToMM(p.x), pcbnew.ToMM(p.y)]
items = []
pads = []
tracks = []
for fp in b.GetFootprints():
    for p in fp.Pads():
        name = fp.GetReference()+'.'+p.GetNumber()
        items.append((name, p, {l:p.GetEffectiveShape(l) for l in layers if p.IsOnLayer(l)}))
        pads.append(dict(name=name, uuid=p.m_Uuid.AsString(), net=p.GetNetname(), position_mm=mm(p.GetPosition()), size_mm=mm(p.GetSize()), drill_mm=mm(p.GetDrillSize())))
for t in b.GetTracks():
    name = t.m_Uuid.AsString()
    items.append((name, t, {l:t.GetEffectiveShape(l) for l in layers if t.IsOnLayer(l)}))
    tracks.append(dict(uuid=name, net=t.GetNetname(), type=type(t).__name__, start_mm=mm(t.GetStart()), end_mm=mm(t.GetEnd()), width_mm=pcbnew.ToMM(t.GetWidth())))
zone_info = []
for z in b.Zones():
    shapes = {l:z.GetFilledPolysList(l) for l in layers if z.IsOnLayer(l) and z.HasFilledPolysForLayer(l)}
    items.append((z.m_Uuid.AsString(), z, shapes))
    zone_info.append(dict(uuid=z.m_Uuid.AsString(), net=z.GetNetname(), layers={b.GetLayerName(l):dict(outlines=s.OutlineCount(), holes=[s.HoleCount(i) for i in range(s.OutlineCount())], vertices=s.VertexCount()) for l,s in shapes.items()}))
contacts=[]
adj={n:set() for n,_,_ in items}
for (an,ao,ash),(bn,bo,bsh) in itertools.combinations(items,2):
    for l in set(ash)&set(bsh):
        if ash[l].Collide(bsh[l],0):
            contacts.append(dict(a=an,b=bn,layer=b.GetLayerName(l),same_assigned_net=ao.GetNetname()==bo.GetNetname()))
            adj[an].add(bn); adj[bn].add(an)
groups=[]
seen=set()
for name in adj:
    if name in seen: continue
    stack=[name]; group=set()
    while stack:
        n=stack.pop()
        if n in group: continue
        group.add(n); stack.extend(adj[n]-group)
    seen.update(group)
    groups.append(dict(pads=sorted(x for x in group if '.' in x), items=sorted(group)))
expected=sorted([['J1.1','R1.1'],['C1.1','J1.2','R1.2'],['C1.2','J1.3']])
actual=sorted(g['pads'] for g in groups)
netlist=ET.parse('/evidence/work/netlist/design.xml').getroot()
schematic_nets={n.attrib['name']:sorted(x.attrib['ref']+'.'+x.attrib['pin'] for x in n.findall('node')) for n in netlist.findall('nets/net')}
report=dict(python=platform.python_version(),pcbnew=pcbnew.Version(),board_sha256=hashlib.sha256(board_path.read_bytes()).hexdigest(),pads=pads,tracks=tracks,zones=zone_info,contacts_ignoring_net_names=contacts,physical_groups=groups,expected_pad_groups=expected,physical_groups_match_contract=actual==expected,schematic_nets=schematic_nets,schematic_groups_match_contract=sorted(schematic_nets.values())==expected,limitations=['Native KiCad effective-shape and collision engine shared with DRC; this is an explicit contact-graph cross-check, not an independent geometry kernel.','PTH pads and via are treated as plated interlayer connections, as saved. Effective copper shapes do not qualify actual fabrication.','No zone refill, save, thermal/ampacity inference, or physical specimen measurement.'])
(OUT/'saved_copper_graph.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(dict(physical_groups=actual,physical_groups_match_contract=actual==expected,schematic_groups_match_contract=report['schematic_groups_match_contract'],cross_net_contacts=[c for c in contacts if not c['same_assigned_net']],zones=zone_info),indent=2))
