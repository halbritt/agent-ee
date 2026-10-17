"""Read-only native board inspection. No zone refill or candidate writes."""
import hashlib
import json
import platform
from pathlib import Path
import pcbnew

root = Path('/candidate')
b = pcbnew.LoadBoard(str(root / 'project/rc_filter.kicad_pcb'))
b.BuildConnectivity()
c = b.GetConnectivity()
pads = {p.m_Uuid.AsString(): f'{f.GetReference()}.{p.GetNumber()}'
        for f in b.GetFootprints() for p in f.Pads()}
def point(v):
    return [pcbnew.ToMM(v.x), pcbnew.ToMM(v.y)]
def identity(item):
    uid = item.m_Uuid.AsString()
    return pads.get(uid, uid)
def connections(item):
    return {
        'pads': sorted(identity(x) for x in c.GetConnectedPads(item)),
        'tracks': sorted(identity(x) for x in c.GetConnectedTracks(item)),
    }
out = {
    'command': '/usr/bin/python3 /work/experiments/trace_saved.py',
    'python': platform.python_version(),
    'pcbnew': pcbnew.GetBuildVersion(),
    'assumptions': ['Saved copper only; BuildConnectivity does not refill zones.',
                    'Pad numbers, not IN/OUT names, are compared to the external contract.'],
    'input_sha256': {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in [root/'requirements.txt', root/'project/rc_filter.kicad_pcb',
                               root/'project/rc_filter.kicad_sch']},
    'pads': [], 'tracks': [], 'zones': []
}
for f in b.GetFootprints():
    for p in f.Pads():
        out['pads'].append({'pad': identity(p), 'net': p.GetNetname(),
            'position_mm': point(p.GetPosition()), 'size_mm': point(p.GetSize()),
            'uuid': p.m_Uuid.AsString(), 'connected': connections(p)})
for t in b.GetTracks():
    out['tracks'].append({'uuid': identity(t), 'type': t.GetClass(),
        'net': t.GetNetname(), 'layer': t.GetLayerName(),
        'start_mm': point(t.GetStart()), 'end_mm': point(t.GetEnd()),
        'width_mm': pcbnew.ToMM(t.GetWidth(pcbnew.F_Cu) if isinstance(t, pcbnew.PCB_VIA) else t.GetWidth()),
        'connected': connections(t)})
for z in b.Zones():
    out['zones'].append({'uuid': identity(z), 'net': z.GetNetname(),
        'layers': [b.GetLayerName(x) for x in z.GetLayerSet().Seq()],
        'filled': z.IsFilled(), 'connected': connections(z)})
out['contract'] = {'J1.2': 'input (resistor side without shunt capacitor)',
                   'J1.1': 'filtered output (resistor/capacitor junction)',
                   'J1.3': 'ground'}
graph = {}
for item in out['pads'] + out['tracks'] + out['zones']:
    key = item.get('pad', item['uuid'])
    for other in item['connected']['pads'] + item['connected']['tracks']:
        graph.setdefault(key, set()).add(other)
        graph.setdefault(other, set()).add(key)
def reachable(start):
    found, todo = set(), [start]
    while todo:
        node = todo.pop()
        if node not in found:
            found.add(node)
            todo.extend(graph.get(node, []))
    return sorted(x for x in found if x in pads.values())
out['copper_components_from_connector'] = {k: reachable(k) for k in out['contract']}
expected = {'J1.2': ['R1.1'], 'J1.1': ['R1.2', 'C1.1'], 'J1.3': ['C1.2']}
out['contract_checks'] = {k: all(x in reachable(k) for x in v) for k, v in expected.items()}
out['outcome'] = 'PASS' if all(out['contract_checks'].values()) else 'FAIL'
out['limitations'] = ['KiCad geometric connectivity is not an electrical measurement.',
    'No thermal/ampacity/manufacturing qualification; operating conditions and fabrication limits unspecified.',
    'No candidate changes; no refills; no parasitic extraction.']
print(json.dumps(out, indent=2))
