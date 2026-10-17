"""Read-only native population and centerline connectivity audit (no SPICE model)."""
import hashlib
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET


def parse(path):
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|[()]|[^\s()]+', path.read_text())
    stack = [[]]
    for token in tokens:
        if token == '(':
            item = []
            stack[-1].append(item)
            stack.append(item)
        elif token == ')':
            stack.pop()
        else:
            stack[-1].append(json.loads(token) if token.startswith('"') else token)
    assert len(stack) == 1
    return stack[0][0]


def children(item, key):
    return [x for x in item if isinstance(x, list) and x and x[0] == key]


def child(item, key):
    return children(item, key)[0]


def props(item):
    return {p[1]: p[2] for p in children(item, 'property')}


root = Path('/candidate')
sch = parse(root / 'project/rc_filter.kicad_sch')
pcb = parse(root / 'project/rc_filter.kicad_pcb')
population = {}
for s in children(sch, 'symbol'):
    p = props(s)
    if not p['Reference'].startswith('#'):
        population[p['Reference']] = {
            'lib_id': child(s, 'lib_id')[1], 'value': p['Value'],
            **{k: child(s, k)[1] for k in ('dnp', 'in_bom', 'on_board')},
        }

pads = {}
footprints = {}
for fp in children(pcb, 'footprint'):
    p = props(fp)
    ref = p['Reference']
    at = child(fp, 'at')[1:]
    assert len(at) == 2, 'This audit assumes unrotated fixture footprints'
    x, y = map(float, at)
    footprints[ref] = {'value': p['Value'], 'attributes': child(fp, 'attr')[1:]}
    for pad in children(fp, 'pad'):
        px, py = map(float, child(pad, 'at')[1:3])
        pads[f'{ref}.{pad[1]}'] = {'xy': (x + px, y + py), 'net': child(pad, 'net')[1]}

segments = []
for s in children(pcb, 'segment'):
    a = tuple(map(float, child(s, 'start')[1:3]))
    b = tuple(map(float, child(s, 'end')[1:3]))
    assert child(s, 'layer')[1] == 'F.Cu'
    assert a[0] == b[0] or a[1] == b[1], 'Only axis-aligned fixture traces supported'
    segments.append({'a': a, 'b': b, 'net': child(s, 'net')[1], 'uuid': child(s, 'uuid')[1]})

points = {p['xy'] for p in pads.values()}
points.update(p for s in segments for p in (s['a'], s['b']))
graph = {p: set() for p in points}
for s in segments:
    a, b = s['a'], s['b']
    on = [p for p in points if
          abs((b[0]-a[0])*(p[1]-a[1])-(b[1]-a[1])*(p[0]-a[0])) < 1e-9
          and min(a[0], b[0])-1e-9 <= p[0] <= max(a[0], b[0])+1e-9
          and min(a[1], b[1])-1e-9 <= p[1] <= max(a[1], b[1])+1e-9]
    for p in on:
        graph[p].update(q for q in on if p != q)


def reachable(start, end):
    seen, pending = {start}, [start]
    while pending:
        for p in graph[pending.pop()]:
            if p not in seen:
                seen.add(p)
                pending.append(p)
    return end in seen


assert population['R1'] == {'lib_id': 'Device:R', 'value': '10k', 'dnp': 'no', 'in_bom': 'yes', 'on_board': 'yes'}
assert footprints['R1'] == {'value': '10k', 'attributes': ['smd']}
assert pads['R1.1']['net'] == '/IN' and pads['R1.2']['net'] == '/OUT'
copper_before = reachable(pads['J1.1']['xy'], pads['J1.2']['xy'])
assert reachable(pads['J1.1']['xy'], pads['R1.1']['xy'])
assert reachable(pads['J1.2']['xy'], pads['R1.2']['xy'])
# Only the fitted resistor bridges its two pads; C1 is open for DC topology.
a, b = pads['R1.1']['xy'], pads['R1.2']['xy']
graph[a].add(b)
graph[b].add(a)
fitted_path = reachable(pads['J1.1']['xy'], pads['J1.2']['xy'])
assert not copper_before and fitted_path

xml = ET.parse('/evidence/work/netlist/design.xml').getroot()
xml_nets = {n.attrib['name']: sorted(f"{x.attrib['ref']}.{x.attrib['pin']}" for x in n) for n in xml.find('nets')}
native_nets = {net: sorted(ref for ref, p in pads.items() if p['net'] == net) for net in {p['net'] for p in pads.values()}}
assert xml_nets == native_nets
checks = {k: json.loads(Path(f'/evidence/checks/{k}.json').read_text()) for k in ('erc', 'drc', 'netlist', 'geometry')}
reports = {k: json.loads(Path(f'/evidence/work/{k}/{k}.json').read_text()) for k in ('erc', 'drc', 'geometry')}
executions = {k: json.loads(Path(f'/evidence/work/{k}/execution.json').read_text()) for k in checks}
consoles = {k: Path(f'/evidence/work/{k}/console.log').read_text() for k in checks}
result = {
    'python': sys.version,
    'native_sha256': {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in (root/'requirements.txt', root/'project/rc_filter.kicad_sch', root/'project/rc_filter.kicad_pcb', root/'project/rc_filter.kicad_pro')},
    'schematic_population': population, 'board_population': footprints,
    'native_pads': pads, 'native_segments': segments,
    'xml_matches_native_pad_nets': xml_nets == native_nets,
    'copper_only_IN_to_OUT': copper_before,
    'fitted_resistive_IN_to_OUT': fitted_path,
    'nominal_resistance_ohm_ignoring_copper': 10000,
    'conductance_siemens': 1/10000,
    'path': 'J1.1 -> F.Cu ae12bf6f -> R1.1 -> fitted R1 10k -> R1.2 -> F.Cu 69a2edc1 / a6dab0b4 / 8b528360 -> J1.2',
    'checks': checks, 'reports': reports, 'executions': executions, 'consoles': consoles,
    'limitations': ['Topology calculation, no physical measurement or SPICE simulation.', 'Nominal ideal resistor; copper resistance, tolerance and leakage not calculated.', 'Centerline audit supports this unrotated, axis-aligned fixture and does not replace clearance DRC.', 'No zone refill; DC IN-to-OUT path is entirely on F.Cu and independent of GND zone.'],
}
Path('/work/experiments/dc_path_result.json').write_text(json.dumps(result, indent=2)+'\n')
print(json.dumps({k: result[k] for k in ('xml_matches_native_pad_nets', 'copper_only_IN_to_OUT', 'fitted_resistive_IN_to_OUT', 'nominal_resistance_ohm_ignoring_copper', 'conductance_siemens', 'path')}, indent=2))
