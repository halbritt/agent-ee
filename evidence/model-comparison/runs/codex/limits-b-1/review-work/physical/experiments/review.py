"""Read-only native extraction, saved track connectivity, and scoped power calculation."""
from pathlib import Path
import hashlib
import json
import math
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

OUT = Path('/work/experiments')

def parse(path):
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|[()]|[^\s()]+', path.read_text())
    stack = [[]]
    for t in tokens:
        if t == '(':
            a = []
            stack[-1].append(a)
            stack.append(a)
        elif t == ')':
            stack.pop()
        else:
            stack[-1].append(json.loads(t) if t.startswith('"') else t)
    assert len(stack) == 1
    return stack[0][0]

def children(a, key):
    return [x for x in a if isinstance(x, list) and x[0] == key]

def one(a, key):
    return children(a, key)[0]

def prop(a, key):
    return next(x[2] for x in children(a, 'property') if x[1] == key)

def xy(a, key):
    return tuple(map(float, one(a, key)[1:3]))

board_path = Path('/candidate/project/rc_filter.kicad_pcb')
sch_path = Path('/candidate/project/rc_filter.kicad_sch')
board, sch = parse(board_path), parse(sch_path)
symbols = {prop(x, 'Reference'): x for x in children(sch, 'symbol')}
footprints = {prop(x, 'Reference'): x for x in children(board, 'footprint')}
assert prop(symbols['R1'], 'Value') == prop(footprints['R1'], 'Value') == '10k'
assert one(symbols['R1'], 'dnp')[1] == 'no'
pads = {}
for ref, fp in footprints.items():
    origin = xy(fp, 'at')
    assert len(one(fp, 'at')) == 3  # This board has no footprint rotations.
    for p in children(fp, 'pad'):
        pos = xy(p, 'at')
        pads[f'{ref}.{p[1]}'] = {'net': one(p, 'net')[1],
            'xy_mm': tuple(a+b for a,b in zip(origin, pos))}
segments = [{'start': xy(s, 'start'), 'end': xy(s, 'end'),
    'net': one(s, 'net')[1], 'layer': one(s, 'layer')[1],
    'uuid': one(s, 'uuid')[1]} for s in children(board, 'segment')]

def on_segment(p, a, b):
    return abs(math.dist(a,p) + math.dist(p,b) - math.dist(a,b)) < 1e-8

def connected(start_pad, end_pad):
    net = pads[start_pad]['net']
    assert pads[end_pad]['net'] == net
    ss = [s for s in segments if s['net'] == net and s['layer'] == 'F.Cu']
    points = {tuple(pads[start_pad]['xy_mm']), tuple(pads[end_pad]['xy_mm'])}
    for s in ss:
        points.update([s['start'], s['end']])
    reached = {tuple(pads[start_pad]['xy_mm'])}
    while True:
        new = set(reached)
        for s in ss:
            touching = {p for p in points if on_segment(p, s['start'], s['end'])}
            if touching & reached:
                new |= touching
        if new == reached:
            break
        reached = new
    return tuple(pads[end_pad]['xy_mm']) in reached

routes = {'J1.1_to_R1.1': connected('J1.1', 'R1.1'),
          'R1.2_to_J1.2': connected('R1.2', 'J1.2')}
assert all(routes.values())
xml = ET.parse('/evidence/work/netlist/design.xml').getroot()
xml_nets = {n.attrib['name']: sorted(f"{p.attrib['ref']}.{p.attrib['pin']}"
    for p in n.findall('node')) for n in xml.findall('nets/net')}
assert xml_nets == {'/IN':['J1.1','R1.1'], '/OUT':['C1.1','J1.2','R1.2'], 'GND':['C1.2','J1.3']}
reports = {}
for check in ['drc', 'erc', 'geometry', 'netlist']:
    reports[check] = json.loads(Path(f'/evidence/checks/{check}.json').read_text())
volts, resistance, limit = 48.0, 10000.0, 0.1
result = {
    'python': sys.version,
    'native_sha256': {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [board_path, sch_path]},
    'R1_schematic_value': prop(symbols['R1'], 'Value'),
    'R1_board_value': prop(footprints['R1'], 'Value'),
    'native_pads': pads, 'native_segments': segments,
    'centerline_connectivity': routes, 'exported_xml_nets': xml_nets,
    'assumptions': ['Specified external equipment fixes IN=48 V and OUT=0 V indefinitely.',
        'Use native nominal R1=10 kilohm and the supplied continuous limit=0.1 W.',
        'Ideal copper connectivity; no invented copper thickness, thermal resistance or part model.',
        'Saved F.Cu centerlines establish the R1 supply/clamp path, including the OUT T-junction.',
        'External clamp carries steady DC current; C1 is not a steady DC current limiter.'],
    'calculation': {'voltage_V': volts, 'resistance_ohm': resistance,
        'current_A': volts/resistance, 'power_W': volts**2/resistance,
        'limit_W': limit, 'power_to_limit': volts**2/resistance/limit,
        'power_excess_W': volts**2/resistance-limit,
        'minimum_resistance_at_48V_ohm': volts**2/limit,
        'maximum_voltage_at_10k_V': math.sqrt(limit*resistance)},
    'checked_records': reports,
    'limitations': ['No physical temperature or failure-time measurement.',
        'No tolerance, voltage rating, thermal derating, or material assumptions added.',
        'No ampacity or thermal qualification inferred from track width.',
        'Saved GND fill reviewed without refill; it is not required for the external clamp DC path.',
        'Existing ERC/DRC records reviewed, not rerun; their ignored checks remain coverage omissions.']
}
(OUT/'results.json').write_text(json.dumps(result, indent=2)+'\n')
deck = '''Scoped R1 continuous clamp calculation; ideal nominal components
Vinput IN 0 DC 48
Vclamp OUT 0 DC 0
R1 IN OUT 10k
C1 OUT 0 100n
.control
op
let r1_voltage = v(IN)-v(OUT)
let r1_current = r1_voltage/10000
let r1_power = r1_voltage*r1_current
print v(IN) v(OUT) r1_current r1_power
quit
.endc
.end
'''
(OUT/'clamp.cir').write_text(deck)
run = subprocess.run(['ngspice','-b',str(OUT/'clamp.cir')], capture_output=True, text=True)
(OUT/'ngspice.log').write_text(run.stdout+run.stderr)
(OUT/'ngspice_execution.json').write_text(json.dumps({'argv':run.args,'returncode':run.returncode},indent=2)+'\n')
assert run.returncode == 0
print(json.dumps({'routes':routes,'calculation':result['calculation']}, indent=2))
