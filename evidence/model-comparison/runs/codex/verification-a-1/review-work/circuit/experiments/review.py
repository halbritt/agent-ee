"""Reproduce this circuit review without modifying the read-only candidate."""
from pathlib import Path
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

HERE = Path('/work/experiments')
commands = []

def run(argv, name):
    env = dict(os.environ, KICAD_CONFIG_HOME=str(HERE / 'kicad-config'))
    p = subprocess.run(argv, cwd=HERE, env=env, text=True, capture_output=True)
    (HERE / (name + '.stdout')).write_text(p.stdout)
    (HERE / (name + '.stderr')).write_text(p.stderr)
    commands.append({'argv': argv, 'cwd': str(HERE), 'exit_code': p.returncode,
                     'stdout': name + '.stdout', 'stderr': name + '.stderr'})
    (HERE / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')
    if p.returncode:
        raise RuntimeError(f'{argv}: {p.stderr}')
    return p.stdout

versions = {'python': sys.version,
            'kicad': run(['kicad-cli', 'version'], 'kicad-version').strip(),
            'ngspice': run(['ngspice', '--version'], 'ngspice-version')}
(HERE / 'versions.json').write_text(json.dumps(versions, indent=2) + '\n')

# Read native source directly and extract nested lists without executing any prose.
def sexpr(path):
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

def children(node, key):
    return [x for x in node if isinstance(x, list) and x and x[0] == key]

def props(node):
    return {x[1]: x[2] for x in children(node, 'property')}

sch = sexpr(Path('/candidate/project/rc_filter.kicad_sch'))
pcb = sexpr(Path('/candidate/project/rc_filter.kicad_pcb'))
symbols = {props(x)['Reference']: {'value': props(x)['Value'],
            'lib_id': children(x, 'lib_id')[0][1], 'at': children(x, 'at')[0][1:]}
           for x in children(sch, 'symbol')}
board = {props(x)['Reference']: {'value': props(x)['Value'],
         'pads': {p[1]: children(p, 'net')[0][1] for p in children(x, 'pad')}}
         for x in children(pcb, 'footprint')}

run(['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml',
     '-o', str(HERE / 'native-export.xml'), '/candidate/project/rc_filter.kicad_sch'],
    'native-export')
root = ET.parse(HERE / 'native-export.xml').getroot()
components = {c.attrib['ref']: c.findtext('value') for c in root.findall('components/comp')}
nets = {n.attrib['name']: sorted(f"{x.attrib['ref']}.{x.attrib['pin']}" for x in n.findall('node'))
        for n in root.findall('nets/net')}
provided = json.loads(Path('/evidence/work/netlist/connectivity.json').read_text())
assert nets == {k: sorted(v) for k, v in provided['nets'].items()}
for ref, val in components.items():
    assert symbols[ref]['value'] == board[ref]['value'] == val
for net, pins in nets.items():
    for pin in pins:
        ref, number = pin.split('.')
        assert board[ref]['pads'][number] == net

hashes = {str(p).replace('/candidate/', 'inputs/'): hashlib.sha256(p.read_bytes()).hexdigest()
          for p in [Path('/candidate/requirements.txt'),
                    Path('/candidate/project/rc_filter.kicad_sch'),
                    Path('/candidate/project/rc_filter.kicad_pcb')]}
checks = {}
for name in ['erc', 'drc', 'netlist', 'geometry']:
    record = json.loads(Path(f'/evidence/checks/{name}.json').read_text())
    execution = json.loads(Path(f'/evidence/work/{name}/execution.json').read_text())
    checks[name] = {'outcome': record['outcome'], 'details': record['details'],
                    'limitations': record['limitations'], 'argv': execution['argv']}

(HERE / 'native-and-check-audit.json').write_text(json.dumps({
    'hashes': hashes, 'native_symbols': symbols, 'native_board': board,
    'fresh_export_components': components, 'fresh_export_nets': nets,
    'export_matches_supplied_connectivity': True,
    'board_pad_assignments_match_export': True, 'checks_read': checks}, indent=2) + '\n')

assert components['R1'] == '10k' and components['C1'] == '100n'
assert nets == {'/IN': ['J1.1', 'R1.1'], '/OUT': ['C1.1', 'J1.2', 'R1.2'],
                'GND': ['C1.2', 'J1.3']}
tau = 10000 * 100e-9
expected = -math.expm1(-0.001 / tau)
measurements = []
for maximum_step in ['1u', '100n']:
    # UIC skips the charged DC operating point. C1 IC=0 enforces the specified
    # discharged initial capacitor. A 1 V constant source for t>=0 is exactly
    # equivalent to the post-step initial-value problem, with no finite rise time.
    deck = f'''Ideal unloaded RC step initial-value problem
Vstep IN 0 DC 1
R1 IN OUT {components['R1']}
C1 OUT 0 {components['C1']} IC=0
.options reltol=1e-9 abstol=1e-14 vntol=1e-11
.tran {maximum_step} 2m 0 {maximum_step} uic
.measure tran vout_1ms FIND v(OUT) AT=1m
.measure tran vin_1ms FIND v(IN) AT=1m
.measure tran vout_1us FIND v(OUT) AT=1u
.end
'''
    filename = f'rc-{maximum_step}.cir'
    (HERE / filename).write_text(deck)
    output = run(['ngspice', '-b', str(HERE / filename)], f'rc-{maximum_step}')
    values = {k: float(v) for k, v in re.findall(
        r'^(vout_1ms|vin_1ms|vout_1us)\s*=\s*([\d.eE+-]+)', output, re.MULTILINE)}
    assert set(values) == {'vout_1ms', 'vin_1ms', 'vout_1us'}, output
    measurements.append({'maximum_step': maximum_step, **values,
                         'error_vs_analytic_V': values['vout_1ms'] - expected,
                         'within_requirement': 0.62 <= values['vout_1ms'] <= 0.65})

result = {'assumptions': ['Ideal components; no output load; ground is J1.3.',
    'Input is 1 V for t>=0; C1 voltage is zero at t=0.',
    'No DC operating-point precharge: transient uses UIC and C1 IC=0.',
    'Only stated 1 ms criterion; no finite source impedance or rise time added.'],
    'equation': 'C*dVout/dt=(1-Vout)/R; Vout(t)=1-exp(-t/(R*C))',
    'R_ohm': 10000, 'C_farad': 100e-9, 'tau_s': tau, 'time_s': 0.001,
    'analytic_vout_V': expected, 'required_V': [0.62, 0.65],
    'analytic_within_requirement': 0.62 <= expected <= 0.65,
    'simulations': measurements,
    'limitations': ['No tolerance, parasitic, loading, physical rating or qualification analysis (outside scope).',
       'ERC/DRC records inspected; those checks were not rerun or enabled beyond saved settings.',
       'Saved board fill only; no refill, fabrication or hardware measurement.',
       'No shutdown, reset, brownout or other input waveform requirement supplied.']}
(HERE / 'response-results.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
