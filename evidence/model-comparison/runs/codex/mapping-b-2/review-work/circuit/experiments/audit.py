"""Read-only native connectivity audit; run with python3 /work/experiments/audit.py."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

OUT = Path('/work/experiments')
log = []
def run(argv):
    p = subprocess.run(argv, capture_output=True, text=True, env={
        **os.environ,
        'KICAD_CONFIG_HOME': str(OUT / 'kicad-config'),
        'KICAD10_SYMBOL_DIR': '/candidate/stock/symbols',
        'KICAD10_FOOTPRINT_DIR': '/candidate/stock/footprints',
        'KICAD10_TEMPLATE_DIR': '/candidate/stock/template',
    })
    log.append({'argv': argv, 'exit_code': p.returncode,
                'stdout': p.stdout, 'stderr': p.stderr})
    (OUT / 'commands.json').write_text(json.dumps(log, indent=2) + '\n')
    if p.returncode:
        raise RuntimeError(argv)
    return p.stdout

def parse(path):
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|[()]|[^\s()]+', path.read_text())
    stack = []
    root = None
    for token in tokens:
        if token == '(':
            n = []
            if stack:
                stack[-1].append(n)
            else:
                root = n
            stack.append(n)
        elif token == ')':
            stack.pop()
        else:
            stack[-1].append(json.loads(token) if token.startswith('"') else token)
    assert not stack
    return root

def children(n, key):
    return [a for a in n if isinstance(a, list) and a and a[0] == key]
def child(n, key):
    return children(n, key)[0]
def point(n):
    return tuple(round(float(x), 6) for x in n[1:3])

run(['kicad-cli', 'version'])
run(['python3', '--version'])
run(['ngspice', '--version'])
run(['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml',
     '-o', str(OUT / 'fresh.xml'), '/candidate/project/rc_filter.kicad_sch'])

native_files = [Path('/candidate/requirements.txt'),
                *sorted(Path('/candidate/project').glob('*'))]
hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in native_files}
sch = parse(Path('/candidate/project/rc_filter.kicad_sch'))
pcb = parse(Path('/candidate/project/rc_filter.kicad_pcb'))
libs = {n[1]: n for n in children(child(sch, 'lib_symbols'), 'symbol')}

# All wires in this fixture join at endpoints. Merge endpoints and named labels.
parent = {}
def find(a):
    parent.setdefault(a, a)
    if parent[a] != a:
        parent[a] = find(parent[a])
    return parent[a]
def union(a, b):
    parent[find(a)] = find(b)
for w in children(sch, 'wire'):
    ends = child(w, 'pts')[1:]
    assert len(ends) == 2
    union(point(ends[0]), point(ends[1]))
labels = []
for lab in children(sch, 'label'):
    name, at = lab[1], point(child(lab, 'at'))
    union(at, 'label:' + name)
    labels.append({'name': name, 'at_mm': at})

pins = []
values = {}
for sym in children(sch, 'symbol'):
    props = {n[1]: n[2] for n in children(sym, 'property')}
    ref = props['Reference']
    lib = child(sym, 'lib_id')[1]
    at = child(sym, 'at')
    origin = point(at)
    angle = float(at[3])
    assert angle in (0, 180) and not children(sym, 'mirror')
    for unit in children(libs[lib], 'symbol'):
        for pin in children(unit, 'pin'):
            xy = point(child(pin, 'at'))
            # Library coordinates are y-up; schematic page coordinates are y-down.
            sign = 1 if angle == 0 else -1
            pos = (round(origin[0] + sign * xy[0], 6),
                   round(origin[1] - sign * xy[1], 6))
            num = child(pin, 'number')[1]
            pins.append({'ref': ref, 'pin': num, 'position_mm': pos})
            if lib == 'power:GND':
                union(pos, 'label:GND')
    if not ref.startswith('#'):
        values[ref] = props['Value']

schematic_nets = {}
for pin in pins:
    if pin['ref'].startswith('#'):
        continue
    names = [n for n in ('IN', 'OUT', 'GND')
             if find(pin['position_mm']) == find('label:' + n)]
    assert len(names) == 1
    name = names[0] if names[0] == 'GND' else '/' + names[0]
    pin['net'] = name
    schematic_nets.setdefault(name, []).append(pin['ref'] + '.' + pin['pin'])

board_nets = {}
board_pads = []
for fp in children(pcb, 'footprint'):
    ref = next(n[2] for n in children(fp, 'property') if n[1] == 'Reference')
    for pad in children(fp, 'pad'):
        net = child(pad, 'net')[1]
        board_nets.setdefault(net, []).append(ref + '.' + pad[1])
        board_pads.append({'ref': ref, 'pad': pad[1], 'net': net,
                           'uuid': child(pad, 'uuid')[1]})

def xmlnets(path):
    return {net.attrib['name']: sorted(n.attrib['ref'] + '.' + n.attrib['pin']
                                       for n in net.findall('node'))
            for net in ET.parse(path).getroot().findall('./nets/net')}
schematic_nets = {k: sorted(v) for k, v in schematic_nets.items()}
board_nets = {k: sorted(v) for k, v in board_nets.items()}
fresh = xmlnets(OUT / 'fresh.xml')
supplied = xmlnets(Path('/evidence/work/netlist/design.xml'))
assert schematic_nets == board_nets == fresh == supplied

geometry = json.loads(Path('/evidence/work/geometry/geometry.json').read_text())
geometry_nets = {}
for pad in geometry['pads']:
    geometry_nets.setdefault(pad['net'], []).append(pad['ref'] + '.' + pad['pad'])
assert {k: sorted(v) for k, v in geometry_nets.items()} == board_nets

result = {
    'python': sys.version,
    'candidate_sha256': hashes,
    'schematic_pins': [p for p in pins if not p['ref'].startswith('#')],
    'schematic_labels': labels,
    'values': values,
    'board_pads': board_pads,
    'confirmed_nets': fresh,
    'native_schematic_board_fresh_and_supplied_xml_geometry_agree': True,
    'contract': {'J1.1': 'filtered output', 'J1.2': 'input', 'J1.3': 'ground'},
    'actual': {'J1.1': 'R1.1 only, resistor side without capacitor',
               'J1.2': 'R1.2 and C1.1, capacitor shunt node',
               'J1.3': 'C1.2 ground'},
    'diagnostic_calculation': {
        'assumptions': 'Ideal R and C; algebraic topology analysis only, no asserted external operating conditions. RL denotes an optional purely resistive load from J1.1 to J1.3.',
        'R_ohm': 10000, 'C_F': 1e-7, 'R_times_C_s': 10000 * 1e-7,
        'intended_ideal_unloaded_transfer_pin1_over_pin2': '1/(1+s*R*C)',
        'intended_ideal_pole_Hz': 1 / (2 * math.pi * 10000 * 1e-7),
        'saved_transfer_pin1_over_pin2': 'RL/(R+RL), tending to 1 for unloaded output; C is connected at pin2 input',
        'limitation': 'External source impedance may itself filter the capacitor node; no actual source/load model, waveform, ratings, tolerances or measured bandwidth are provided.'
    },
    'audit_limitations': ['Schematic parser checks endpoint-connected wires and 0/180 degree unmirrored symbols in this fixture.',
                          'Board net membership checked against saved pad assignments; physical saved routing and fill assessed through native source, supplied geometry and DRC, without refill.',
                          'No simulation or hardware measurement; ngspice availability recorded only.']
}
(OUT / 'connectivity-audit.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
