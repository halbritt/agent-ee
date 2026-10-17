"""Read-only audit of native RC fixture and fresh verification artifacts."""
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

OUT = Path('/work/experiments')
commands = []

def run(argv, name):
    p = subprocess.run(argv, cwd=OUT, env={**os.environ, 'KICAD_CONFIG_HOME': str(OUT/'kicad-config')}, capture_output=True, text=True)
    (OUT / (name + '.stdout')).write_text(p.stdout)
    (OUT / (name + '.stderr')).write_text(p.stderr)
    commands.append({'argv': argv, 'cwd': str(OUT), 'exit_code': p.returncode, 'stdout': name+'.stdout', 'stderr': name+'.stderr'})
    if p.returncode:
        raise RuntimeError(commands[-1])
    return p.stdout

def sexpr(path):
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|[()]|[^\s()]+', Path(path).read_text())
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
    return [x for x in a if isinstance(x, list) and x and x[0] == key]

def one(a, key):
    return children(a, key)[0]

def props(a):
    return {x[1]: x[2] for x in children(a, 'property')}

run(['python3', '--version'], 'python-version')
run(['kicad-cli', 'version'], 'kicad-version')
run(['ngspice', '--version'], 'ngspice-version')
run(['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml', '-o', str(OUT/'native-export.xml'), '/candidate/project/rc_filter.kicad_sch'], 'netlist-export')

sch = sexpr('/candidate/project/rc_filter.kicad_sch')
pcb = sexpr('/candidate/project/rc_filter.kicad_pcb')
symbols = {props(s)['Reference']: {'value': props(s)['Value'], 'lib_id': one(s,'lib_id')[1], 'uuid': one(s,'uuid')[1], 'dnp': one(s,'dnp')[1]} for s in children(sch,'symbol')}
board = {props(f)['Reference']: {'value': props(f)['Value'], 'pads': {p[1]: one(p,'net')[1] for p in children(f,'pad')}} for f in children(pcb,'footprint')}
tracks = [{'uuid': one(t,'uuid')[1], 'net': one(t,'net')[1], 'width_mm': float(one(t,'width')[1])} for t in children(pcb,'segment')]
xml = ET.parse(OUT/'native-export.xml').getroot()
values = {c.attrib['ref']: c.findtext('value') for c in xml.findall('./components/comp')}
nets = {n.attrib['name']: sorted(x.attrib['ref']+'.'+x.attrib['pin'] for x in n.findall('node')) for n in xml.findall('./nets/net')}
old = ET.parse('/evidence/work/netlist/design.xml').getroot()
assert values == {c.attrib['ref']: c.findtext('value') for c in old.findall('./components/comp')}
assert nets == {n.attrib['name']: sorted(x.attrib['ref']+'.'+x.attrib['pin'] for x in n.findall('node')) for n in old.findall('./nets/net')}
assert nets == {'/IN':['J1.1','R1.1'], '/OUT':['C1.1','J1.2','R1.2'], 'GND':['C1.2','J1.3']}
for ref in board:
    assert board[ref]['value'] == symbols[ref]['value'] == values[ref]
    for pad, net in board[ref]['pads'].items():
        assert ref+'.'+pad in nets[net]

def si(value):
    m = re.fullmatch(r'([\d.]+)([kn])', value)
    return float(m[1]) * {'k': 1e3, 'n': 1e-9}[m[2]]

resistance = si(values['R1'])
capacitance = si(values['C1'])
tau = resistance * capacitance
analytic = {str(t): -math.expm1(-t/tau) for t in [0.001, 0.005]}

# Inspect actual supplied binary waveform, not only its .meas log.
raw = Path('/evidence/work/rc-step/result.raw').read_bytes()
header, payload = raw.split(b'Binary:\n', 1)
header = header.decode()
(OUT/'supplied-raw-header.txt').write_text(header)
nv = int(re.search(r'No. Variables:\s*(\d+)', header)[1])
np = int(re.search(r'No. Points:\s*(\d+)', header)[1])
assert nv == 4 and len(payload) == nv*np*8
assert 'real' in header and 'v(out)' in header and 'v(in)' in header
rows = list(struct.iter_unpack('<'+'d'*nv, payload))
assert all(rows[i][0] < rows[i+1][0] for i in range(len(rows)-1))
def interp(t):
    for a,b in zip(rows, rows[1:]):
        if a[0] <= t <= b[0]:
            return a[2]+(b[2]-a[2])*(t-a[0])/(b[0]-a[0])
    raise ValueError(t)

# Independent run uses native-exported values; 1 us maximum time step.
deck = '\n'.join(['* Independent native-value RC response; zero initial operating point, no load',
    'R1 IN OUT '+values['R1'], 'C1 OUT 0 '+values['C1'],
    'VSTEP IN 0 PWL(0 0 1n 1)', '.tran 1u 5m 0 1u',
    '.meas tran vout_1ms FIND v(OUT) AT=1m',
    '.meas tran vout_5ms FIND v(OUT) AT=5m', '.end', ''])
(OUT/'independent.cir').write_text(deck)
run(['ngspice','-n','-b','-o',str(OUT/'independent-ngspice.log'),str(OUT/'independent.cir')], 'independent-ngspice')

records = {}
for check, filename in [('erc','erc.json'),('drc','drc.json'),('geometry','geometry.json'),('rc-step','execution.json')]:
    p = Path('/evidence/work')/check/filename
    records[str(p)] = json.loads(p.read_text())
hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path('/candidate/project/rc_filter.kicad_sch'), Path('/candidate/project/rc_filter.kicad_pcb'),Path('/candidate/requirements/fixture-requirements.md')]}
result = {
    'native_hashes': hashes, 'native_symbols': symbols, 'board_components': board,
    'fresh_export_values': values, 'fresh_export_nets': nets,
    'native_track_segments': tracks,
    'geometry': {'count':len(tracks), 'narrowest_mm':min(t['width_mm'] for t in tracks), 'required_min_mm':0.20},
    'calculation': {'R_ohm':resistance,'C_F':capacitance,'tau_s':tau,'formula':'Vout(t) = 1 V * (1-exp(-t/(R*C)))', 'analytic_V_by_time_s':analytic,
        'allowed_tau_s_for_1ms_limit':[-0.001/math.log(1-0.65), -0.001/math.log(1-0.62)],
        'requirement_V_at_1ms':[0.62,0.65], 'shortfall_below_min_V':0.62-analytic['0.001']},
    'supplied_raw': {'points':np, 'variables':nv,'initial_row':rows[0], 'final_row':rows[-1],
        'interpolated_vout_1ms':interp(0.001),'interpolated_vout_5ms':interp(0.005),
        'max_abs_error_vs_ideal_instant_step_V':max(abs(row[2]+math.expm1(-row[0]/tau)) for row in rows)},
    'verification_model': 'Ideal R and C values and topology match the native export. PWL ramps from 0 to 1 V over 1 ns; initial operating point is 0 V; no external load. Ramp error is negligible against 0.5248 V failure margin. The 5 ms measurement has no acceptance limit.',
    'assumptions': ['Initially discharged capacitor and zero input before step.', 'Ideal 1 V source, ideal passive values, no external load.'],
    'limitations': ['No tolerance, temperature, loading, ADC sampling, EMI, parasitics or hardware tests.', 'No device-specific ratings or manufacturer models are provided or inferred.', 'ERC/DRC records inspected; not rerun. Their ignored checks remain untested.', 'Saved zone fill only; no refill performed.', 'No active reset, brownout, supply startup or shutdown circuitry exists; no additional operating conditions were introduced.'],
    'records_inspected': records,
}
(OUT/'audit-results.json').write_text(json.dumps(result, indent=2)+'\n')
(OUT/'commands.json').write_text(json.dumps({'entry_command':'python3 /work/experiments/audit.py','python':sys.version,'commands':commands},indent=2)+'\n')
print(json.dumps({k:result[k] for k in ['geometry','calculation','supplied_raw','verification_model']},indent=2))
