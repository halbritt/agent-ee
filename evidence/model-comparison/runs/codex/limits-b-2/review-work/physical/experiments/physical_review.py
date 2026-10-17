"""Read-only review of native RC board and externally clamped R1 load."""
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path('/work/experiments')
commands = []

def run(argv):
    env = dict(os.environ)
    env.update(KICAD_CONFIG_HOME=str(ROOT / 'kicad-config'),
               KICAD10_SYMBOL_DIR='/candidate/stock/symbols',
               KICAD10_FOOTPRINT_DIR='/candidate/stock/footprints')
    result = subprocess.run(argv, capture_output=True, text=True, env=env)
    commands.append(dict(argv=argv, exit_code=result.returncode,
                         stdout=result.stdout, stderr=result.stderr))
    return result

def parse(path):
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|[()]|[^\s()]+', path.read_text())
    stack = []
    root = None
    for token in tokens:
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
            stack[-1].append(json.loads(token) if token.startswith('"') else token)
    assert not stack
    return root

def nodes(node, key):
    return [x for x in node if isinstance(x, list) and x and x[0] == key]

def one(node, key):
    return nodes(node, key)[0]

def xy(node, key):
    return tuple(map(float, one(node, key)[1:3]))

def on_segment(p, a, b):
    cross = (p[0]-a[0])*(b[1]-a[1]) - (p[1]-a[1])*(b[0]-a[0])
    return abs(cross) < 1e-8 and all(min(a[i], b[i])-1e-8 <= p[i] <= max(a[i], b[i])+1e-8 for i in (0, 1))

versions = {'python': platform.python_version(), 'kicad': run(['kicad-cli', '--version']).stdout.strip()}
export = run(['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml',
              '-o', str(ROOT / 'native-netlist.xml'), '/candidate/project/rc_filter.kicad_sch'])
assert export.returncode == 0, export.stderr
xml = ET.parse(ROOT / 'native-netlist.xml').getroot()
netlist = {n.attrib['name']: sorted(f"{x.attrib['ref']}.{x.attrib['pin']}" for x in n.findall('node')) for n in xml.findall('./nets/net')}
assert netlist['/IN'] == ['J1.1', 'R1.1']
assert netlist['/OUT'] == ['C1.1', 'J1.2', 'R1.2']
resistance_text = xml.find("./components/comp[@ref='R1']/value").text
assert resistance_text == '10k'

board_path = Path('/candidate/project/rc_filter.kicad_pcb')
board = parse(board_path)
pads = {}
footprints = {}
for fp in nodes(board, 'footprint'):
    properties = {p[1]: p[2] for p in nodes(fp, 'property')}
    ref = properties['Reference']
    position = xy(fp, 'at')
    assert len(one(fp, 'at')) == 3, 'This script assumes unrotated footprints'
    footprints[ref] = {'value': properties['Value'], 'position_mm': position, 'footprint': fp[1]}
    for pad in nodes(fp, 'pad'):
        local = xy(pad, 'at')
        pads[f'{ref}.{pad[1]}'] = {'net': one(pad, 'net')[1],
                                  'position_mm': tuple(position[i]+local[i] for i in (0, 1))}
segments = [{'start': xy(s, 'start'), 'end': xy(s, 'end'), 'net': one(s, 'net')[1],
             'layer': one(s, 'layer')[1], 'width_mm': float(one(s, 'width')[1]),
             'uuid': one(s, 'uuid')[1]} for s in nodes(board, 'segment')]

def path(start_pad, end_pad, net):
    relevant = [s for s in segments if s['net'] == net]
    assert all(s['layer'] == 'F.Cu' for s in relevant)
    assert pads[start_pad]['net'] == pads[end_pad]['net'] == net
    start, end = (pads[p]['position_mm'] for p in (start_pad, end_pad))
    points = {start, end} | {s[k] for s in relevant for k in ('start', 'end')}
    graph = {p: set() for p in points}
    for s in relevant:
        ordered = sorted([p for p in points if on_segment(p, s['start'], s['end'])],
                         key=lambda p: math.dist(p, s['start']))
        for a, b in zip(ordered, ordered[1:]):
            graph[a].add(b)
            graph[b].add(a)
    todo = [(start, [start])]
    visited = set()
    while todo:
        p, route = todo.pop()
        if p == end:
            return {'from': start_pad, 'to': end_pad, 'net': net, 'route_mm': route,
                    'length_mm': sum(math.dist(a, b) for a, b in zip(route, route[1:]))}
        if p not in visited:
            visited.add(p)
            todo.extend((q, route+[q]) for q in graph[p] if q not in visited)
    raise AssertionError('No saved copper route')

routes = [path('J1.1', 'R1.1', '/IN'), path('R1.2', 'J1.2', '/OUT')]
resistance = 10000.0
vin, vout, limit = 48.0, 0.0, 0.1
current = (vin-vout)/resistance
power = (vin-vout)**2/resistance
checks = {}
for kind in ('erc', 'drc', 'netlist', 'geometry'):
    checks[kind] = json.loads(Path(f'/evidence/checks/{kind}.json').read_text())
native_reports = {kind: json.loads(Path(f'/evidence/work/{kind}/{kind}.json').read_text()) for kind in ('erc', 'drc')}
geometry = json.loads(Path('/evidence/work/geometry/geometry.json').read_text())
result = {
    'versions': versions,
    'native_hashes': {str(p).replace('/candidate/', 'inputs/'): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in [board_path, Path('/candidate/project/rc_filter.kicad_sch'), Path('/candidate/requirements.txt')]},
    'fresh_native_netlist': netlist, 'footprints': footprints, 'pads': pads,
    'saved_segments': segments, 'verified_routes': routes,
    'calculation': {'R1_ohm': resistance, 'IN_V': vin, 'OUT_V': vout,
                    'R1_voltage_V': vin-vout, 'R1_current_A': current,
                    'R1_power_W': power, 'continuous_limit_W': limit,
                    'limit_ratio': power/limit, 'excess_W': power-limit,
                    'equation': 'P = (VIN - VOUT)^2 / R1',
                    'minimum_R_for_limit_at_48V_ohm': (vin-vout)**2/limit,
                    'maximum_voltage_for_limit_with_10k_V': math.sqrt(resistance*limit)},
    'native_reports': native_reports,
    'check_records': checks,
    'saved_geometry_crosscheck': {'segments': len([t for t in geometry['tracks'] if t['type']=='track']),
                                  'vias': len([t for t in geometry['tracks'] if t['type']=='via']),
                                  'zones': geometry['zones'], 'unconnected_items': geometry['unconnected_items']}
}
(ROOT / 'physical-results.json').write_text(json.dumps(result, indent=2)+'\n')
(ROOT / 'commands.json').write_text(json.dumps(commands, indent=2)+'\n')
print(json.dumps({'versions': versions, 'routes': routes, 'calculation': result['calculation']}, indent=2))
