"""Read-only physical review; run with python3 /work/experiments/physical_review.py.

Only writes experiment outputs. No candidate edits or zone refill.
"""
import ast
import hashlib
import heapq
import json
from pathlib import Path
import re
import subprocess
import sys

OUT = Path('/work/experiments')
CANDIDATE = Path('/candidate')

def run(name, argv):
    result = subprocess.run(argv, capture_output=True, text=True)
    (OUT / (name + '.log')).write_text(result.stdout + result.stderr)
    return {'argv': argv, 'exit_code': result.returncode, 'log': name + '.log'}

def parse_sexpr(text):
    stack = []
    root = None
    for token in re.findall(r'"(?:\\.|[^"\\])*"|[()]|[^\s()]+', text):
        if token == '(':
            node = []
            if stack:
                stack[-1].append(node)
            else:
                root = node
            stack.append(node)
        elif token == ')':
            stack.pop()
        else:
            stack[-1].append(ast.literal_eval(token) if token.startswith('"') else token)
    assert not stack
    return root

def children(node, key):
    return [c for c in node if isinstance(c, list) and c[0] == key]

def one(node, key):
    return children(node, key)[0]

def point(node, key):
    return tuple(map(float, one(node, key)[1:3]))

def on_segment(p, a, b):
    cross = (p[0]-a[0])*(b[1]-a[1]) - (p[1]-a[1])*(b[0]-a[0])
    return abs(cross) < 1e-8 and all(min(a[i], b[i])-1e-8 <= p[i] <= max(a[i], b[i])+1e-8 for i in (0, 1))

commands = [
    run('python_version', [sys.executable, '--version']),
    run('kicad_version', ['kicad-cli', '--version']),
    run('fresh_netlist', ['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml', '-o', str(OUT/'fresh_design.xml'), str(CANDIDATE/'project/rc_filter.kicad_sch')]),
    run('fresh_drc', ['kicad-cli', 'pcb', 'drc', '--format', 'json', '--severity-all', '--schematic-parity', '--units', 'mm', '-o', str(OUT/'fresh_drc.json'), str(CANDIDATE/'project/rc_filter.kicad_pcb')]),
    run('fresh_erc', ['kicad-cli', 'sch', 'erc', '--format', 'json', '--severity-all', '--units', 'mm', '-o', str(OUT/'fresh_erc.json'), str(CANDIDATE/'project/rc_filter.kicad_sch')]),
]

board = parse_sexpr((CANDIDATE/'project/rc_filter.kicad_pcb').read_text())
pads = {}
values = {}
for fp in children(board, 'footprint'):
    properties = {p[1]: p[2] for p in children(fp, 'property')}
    ref = properties['Reference']
    values[ref] = properties['Value']
    origin = point(fp, 'at')
    # All fitted footprints are unrotated in this candidate.
    assert len(one(fp, 'at')) == 3 or float(one(fp, 'at')[3]) == 0
    for pad in children(fp, 'pad'):
        local = point(pad, 'at')
        pads[ref+'.'+pad[1]] = {'net': one(pad, 'net')[1], 'xy_mm': tuple(origin[i]+local[i] for i in (0, 1))}

segments = [{'net': one(s, 'net')[1], 'start': point(s, 'start'), 'end': point(s, 'end'), 'width_mm': float(one(s, 'width')[1]), 'layer': one(s, 'layer')[1]} for s in children(board, 'segment')]

def route(net, start_ref, end_ref):
    # Split at all segment endpoints and pad centers, including interior T joins.
    ss = [s for s in segments if s['net'] == net]
    assert all(s['layer'] == 'F.Cu' for s in ss)
    pts = {p['xy_mm'] for p in pads.values() if p['net'] == net}
    pts.update(p for s in ss for p in (s['start'], s['end']))
    graph = {p: [] for p in pts}
    for s in ss:
        pp = sorted(p for p in pts if on_segment(p, s['start'], s['end']))
        for a, b in zip(pp, pp[1:]):
            length = sum((a[i]-b[i])**2 for i in (0, 1))**0.5
            graph[a].append((b, length))
            graph[b].append((a, length))
    start, target = pads[start_ref]['xy_mm'], pads[end_ref]['xy_mm']
    q = [(0.0, start, [start])]
    seen = set()
    while q:
        distance, p, path = heapq.heappop(q)
        if p in seen:
            continue
        seen.add(p)
        if p == target:
            return {'from': start_ref, 'to': end_ref, 'net': net, 'centerline_length_mm': distance, 'points_mm': path}
        for nxt, length in graph[p]:
            heapq.heappush(q, (distance+length, nxt, path+[nxt]))
    raise AssertionError('Disconnected physical route')

assert values['R1'] == '10k'
voltage = 12.0 - 0.0
resistance = 10000.0
limit = 0.1
power = voltage**2 / resistance
report = {
    'commands': commands,
    'source_sha256': {str(p.relative_to(CANDIDATE)): hashlib.sha256(p.read_bytes()).hexdigest() for p in [CANDIDATE/'requirements.txt', CANDIDATE/'project/rc_filter.kicad_pcb', CANDIDATE/'project/rc_filter.kicad_sch']},
    'values': values,
    'pads': pads,
    'physical_routes': [route('/IN', 'J1.1', 'R1.1'), route('/OUT', 'R1.2', 'J1.2'), route('/OUT', 'R1.2', 'C1.1')],
    'calculation': {
        'condition': '/IN held at 12 V DC; /OUT held at 0 V indefinitely by external equipment',
        'R1_ohms': resistance,
        'voltage_across_R1_V': voltage,
        'current_A': voltage/resistance,
        'formula': 'P_R1 = (V_IN - V_OUT)^2 / R1',
        'power_W': power,
        'continuous_limit_W': limit,
        'fraction_of_limit': power/limit,
        'margin_W': limit-power,
        'minimum_resistance_for_limit_ohms': voltage**2/limit,
        'result': 'Below the stated continuous power limit',
    },
    'assumptions': [
        '10k denotes 10000 ohms as saved in schematic and board; no invented tolerance or part model.',
        'The exercise supplies a 0.1 W continuous limit for the fitted R1; no package-based rating inference.',
        'Ideal conductors conservatively place the entire applied 12 V across R1; positive series copper/contact resistance reduces R1 power.',
        'The load current returns through J1.2 to the external clamp, not through C1 or the on-board GND plane.',
        'At the specified steady DC condition C1 is not a series element or current limiter.',
    ],
    'limitations': [
        'No physical temperature, lifetime, thermal-resistance or ampacity qualification; width is not used to infer any of these.',
        'No zone refill, candidate modification, transient/startup test, component tolerance sweep or out-of-scope qualification.',
        'No fabrication stackup, solder joint or assembled hardware measurements.',
        'Native ERC/DRC are rule/connectivity checks and do not test the specified dissipation condition.',
    ],
}
(OUT/'physical_results.json').write_text(json.dumps(report, indent=2)+'\n')
print(json.dumps(report['calculation'], indent=2))
print(json.dumps(commands, indent=2))
