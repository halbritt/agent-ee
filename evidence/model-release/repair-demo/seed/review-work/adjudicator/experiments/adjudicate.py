import hashlib
import json
import math
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import xml.etree.ElementTree as ET

BASE = Path('/work/experiments')
commands = []
env = dict(os.environ, KICAD_CONFIG_HOME=str(BASE / 'kicad-config'),
           KICAD10_SYMBOL_DIR='/candidate/stock/symbols',
           KICAD10_FOOTPRINT_DIR='/candidate/stock/footprints')

def run(argv, name):
    p = subprocess.run(argv, cwd=BASE, env=env, capture_output=True, text=True)
    (BASE / (name + '.stdout')).write_text(p.stdout)
    (BASE / (name + '.stderr')).write_text(p.stderr)
    commands.append({'argv': argv, 'cwd': str(BASE), 'exit_code': p.returncode,
                     'stdout': name + '.stdout', 'stderr': name + '.stderr'})
    (BASE / 'commands.json').write_text(json.dumps(commands, indent=2))
    if p.returncode:
        raise RuntimeError(name + ' failed')
    return p.stdout

run(['python3', '--version'], 'python-version')
run(['kicad-cli', '--version'], 'kicad-version')
run(['ngspice', '--version'], 'ngspice-version')
run(['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml', '-o',
     str(BASE / 'native.xml'), '/candidate/project/rc_filter.kicad_sch'], 'export')

def parse_sexp(path):
    stack = []
    root = None
    for token in re.findall(r'"(?:\\.|[^"\\])*"|[()]|[^\s()]+', path.read_text()):
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
    return root

def children(node, key):
    return [x for x in node if isinstance(x, list) and x[0] == key]

def props(node):
    return {p[1]: p[2] for p in children(node, 'property')}

sch = parse_sexp(Path('/candidate/project/rc_filter.kicad_sch'))
pcb = parse_sexp(Path('/candidate/project/rc_filter.kicad_pcb'))
sch_values = {props(x)['Reference']: props(x)['Value'] for x in children(sch, 'symbol')}
pcb_values = {props(x)['Reference']: props(x)['Value'] for x in children(pcb, 'footprint')}
pcb_pads = {props(x)['Reference']: {p[1]: children(p, 'net')[0][1]
            for p in children(x, 'pad')} for x in children(pcb, 'footprint')}
widths = [float(children(x, 'width')[0][1]) for x in children(pcb, 'segment')]
root = ET.parse(BASE / 'native.xml').getroot()
values = {x.attrib['ref']: x.findtext('value') for x in root.findall('./components/comp')}
nets = {x.attrib['name']: sorted(n.attrib['ref'] + '.' + n.attrib['pin']
        for n in x.findall('node')) for x in root.findall('./nets/net')}
original = ET.parse('/evidence/work/netlist/design.xml').getroot()
original_nets = {x.attrib['name']: sorted(n.attrib['ref'] + '.' + n.attrib['pin']
                 for n in x.findall('node')) for x in original.findall('./nets/net')}
assert nets == original_nets
assert values['R1'] == sch_values['R1'] == pcb_values['R1'] == '100k'
assert values['C1'] == sch_values['C1'] == pcb_values['C1'] == '100n'
assert pcb_pads == {'R1': {'1': '/IN', '2': '/OUT'},
                    'C1': {'1': '/OUT', '2': 'GND'},
                    'J1': {'1': '/IN', '2': '/OUT', '3': 'GND'}}
tau = 100000 * 100e-9
analytic = {str(t): -math.expm1(-t/tau) for t in [0.001, 0.005]}
raw = Path('/evidence/work/rc-step/result.raw').read_bytes()
header, binary = raw.split(b'Binary:\n', 1)
nv = int(re.search(rb'No. Variables:\s*(\d+)', header)[1])
np = int(re.search(rb'No. Points:\s*(\d+)', header)[1])
assert nv == 4 and len(binary) == np * nv * 8
points = list(struct.iter_unpack('=4d', binary))
def interp(t):
    for a, b in zip(points, points[1:]):
        if a[0] <= t <= b[0]:
            f = (t-a[0])/(b[0]-a[0])
            return {'vin': a[1]+f*(b[1]-a[1]), 'vout': a[2]+f*(b[2]-a[2])}
    raise ValueError(t)
deck = ('* Independent adjudication, unchanged native R/C values\n'
        'R1 IN OUT ' + values['R1'] + '\nC1 OUT 0 ' + values['C1'] + '\n'
        'VSTEP IN 0 PWL(0 0 1n 1)\n.tran 0.0005m 5m 0 0.0005m\n'
        '.meas tran vout_1ms FIND v(OUT) AT=1m\n'
        '.meas tran vout_5ms FIND v(OUT) AT=5m\n.end\n')
(BASE / 'independent.cir').write_text(deck)
run(['ngspice', '-n', '-b', '-o', str(BASE / 'independent.log'),
     str(BASE / 'independent.cir')], 'independent')
results = {'python': sys.version, 'native_schematic_values': sch_values,
           'native_board_values': pcb_values, 'native_board_pad_nets': pcb_pads,
           'fresh_export_nets': nets, 'fresh_export_matches_supplied_nets': True,
           'native_segment_widths_mm': widths, 'tau_s': tau,
           'analytic_ideal_step_V': analytic, 'original_raw_header': header.decode(),
           'original_raw_interpolated': {str(t): interp(t) for t in [0.001, 0.005]},
           'requirements_1ms_V': [0.62, 0.65],
           'input_sha256': {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in [Path('/candidate/requirements/fixture-requirements.md'),
                       Path('/candidate/project/rc_filter.kicad_sch'),
                       Path('/candidate/project/rc_filter.kicad_pcb')]}}
(BASE / 'results.json').write_text(json.dumps(results, indent=2))
print(json.dumps(results, indent=2))
