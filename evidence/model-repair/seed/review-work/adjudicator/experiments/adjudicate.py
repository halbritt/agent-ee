import hashlib
import json
import math
from pathlib import Path
import platform
import re
import subprocess
import xml.etree.ElementTree as ET

OUT = Path('/work/experiments')
commands = []

def run(argv):
    p = subprocess.run(argv, cwd=OUT, text=True, capture_output=True)
    commands.append({'argv': argv, 'cwd': str(OUT), 'exit_code': p.returncode,
                     'stdout': p.stdout, 'stderr': p.stderr})
    return p

def parse(path):
    tokens = iter(re.findall(r'"(?:\\.|[^"\\])*"|[()]|[^\s()]+', path.read_text()))
    def group():
        result = []
        for t in tokens:
            if t == ')':
                return result
            result.append(group() if t == '(' else json.loads(t) if t.startswith('"') else t)
        return result
    assert next(tokens) == '('
    return group()

def children(node, name):
    return [x for x in node if isinstance(x, list) and x[0] == name]

def child(node, name):
    return children(node, name)[0]

def properties(node):
    return {p[1]: p[2] for p in children(node, 'property')}

sch_path = Path('/candidate/project/rc_filter.kicad_sch')
pcb_path = Path('/candidate/project/rc_filter.kicad_pcb')
sch, pcb = parse(sch_path), parse(pcb_path)
schematic = {properties(s)['Reference']: {'value': properties(s)['Value'],
              'at': child(s, 'at')[1:], 'uuid': child(s, 'uuid')[1],
              'dnp': child(s, 'dnp')[1]} for s in children(sch, 'symbol')}
board = {properties(f)['Reference']: {'value': properties(f)['Value'],
         'pads': {p[1]: child(p, 'net')[1] for p in children(f, 'pad')}}
         for f in children(pcb, 'footprint')}
xml = ET.parse('/evidence/work/netlist/design.xml').getroot()
xml_values = {c.attrib['ref']: c.findtext('value') for c in xml.findall('components/comp')}
xml_nets = {n.attrib['name']: [x.attrib['ref'] + '.' + x.attrib['pin'] for x in n]
            for n in xml.findall('nets/net')}
assert schematic['R1']['value'] == board['R1']['value'] == xml_values['R1'] == '100k'
assert schematic['C1']['value'] == board['C1']['value'] == xml_values['C1'] == '100n'
assert board['R1']['pads'] == {'1': '/IN', '2': '/OUT'}
assert board['C1']['pads'] == {'1': '/OUT', '2': 'GND'}
assert xml_nets == {'/IN': ['J1.1', 'R1.1'], '/OUT': ['C1.1', 'J1.2', 'R1.2'], 'GND': ['C1.2', 'J1.3']}

R, C = 100000.0, 100e-9
tau = R * C
widths = [float(child(s, 'width')[1]) for s in children(pcb, 'segment')]
results = {
    'python_version': platform.python_version(),
    'hashes': {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in [sch_path, pcb_path]},
    'schematic_instances': schematic, 'board_components': board,
    'schematic_wires': children(sch, 'wire'), 'schematic_labels': children(sch, 'label'),
    'exported_values': xml_values, 'exported_nets': xml_nets,
    'R_ohm': R, 'C_farad': C, 'tau_s': tau,
    'ideal_vout_1ms': -math.expm1(-0.001 / tau),
    'ideal_vout_5ms': -math.expm1(-0.005 / tau),
    'required_vout_1ms_V': [0.62, 0.65],
    'allowed_tau_s': [-0.001 / math.log(1 - x) for x in [0.65, 0.62]],
    'native_track_widths_mm': widths,
    'track_width_compliance': all(w >= 0.2 for w in widths),
    'fresh_checks': {p.stem: json.loads(p.read_text()) for p in Path('/evidence/checks').glob('*.json')}
}
run(['kicad-cli', '--version'])
run(['ngspice', '--version'])
# Independent numerical confirmation. Delay the edge by 1 us to establish
# a zero-voltage operating point, and measure 1 ms after that edge.
deck = ('* Adjudication: saved native RC values, ideal unloaded response\n'
        'R1 in out 100k\nC1 out 0 100n\n'
        'V1 in 0 PWL(0 0 1u 0 1.001u 1)\n'
        '.tran 0.1u 5.001m 0 0.1u\n'
        '.meas tran vout_1ms FIND v(out) AT=1.001m\n'
        '.meas tran input_1ms FIND v(in) AT=1.001m\n.end\n')
(OUT / 'independent.cir').write_text(deck)
sim = run(['ngspice', '-n', '-b', '-o', str(OUT / 'independent.log'), str(OUT / 'independent.cir')])
results['simulation_exit_code'] = sim.returncode
(OUT / 'audit-results.json').write_text(json.dumps(results, indent=2) + '\n')
(OUT / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')
print(json.dumps({k: results[k] for k in ['python_version', 'tau_s', 'ideal_vout_1ms', 'ideal_vout_5ms', 'allowed_tau_s', 'native_track_widths_mm', 'simulation_exit_code']}, indent=2))
