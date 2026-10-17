"""Read-only native RC review; all generated files stay beside this script."""
from pathlib import Path
import hashlib, json, math, os, re, shlex, struct, subprocess, sys
import xml.etree.ElementTree as ET

OUT = Path(__file__).resolve().parent
commands = []
env = os.environ.copy()
env.update(KICAD_CONFIG_HOME=str(OUT / 'kicad-config'),
           KICAD10_SYMBOL_DIR='/candidate/stock/symbols',
           KICAD10_FOOTPRINT_DIR='/candidate/stock/footprints')

def run(argv, name):
    p = subprocess.run(argv, cwd=OUT, env=env, capture_output=True, text=True)
    (OUT / (name + '.console.txt')).write_text(p.stdout + p.stderr)
    commands.append({'argv': argv, 'exit_code': p.returncode,
                     'console': name + '.console.txt'})
    (OUT / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')
    if p.returncode:
        raise RuntimeError((argv, p.stderr))
    return p.stdout

def sexpr(path):
    stack = [[]]
    for t in re.findall(r'"(?:\\.|[^"\\])*"|[()]|[^\s()]+', path.read_text()):
        if t == '(':
            child = []
            stack[-1].append(child)
            stack.append(child)
        elif t == ')':
            stack.pop()
        else:
            stack[-1].append(json.loads(t) if t.startswith('"') else t)
    assert len(stack) == 1
    return stack[0][0]

def children(obj, key):
    return [x for x in obj if isinstance(x, list) and x and x[0] == key]

def one(obj, key):
    return children(obj, key)[0]

def props(obj):
    return {x[1]: x[2] for x in children(obj, 'property')}

run(['python3', '--version'], 'python-version')
run(['kicad-cli', '--version'], 'kicad-version')
run(['ngspice', '--version'], 'ngspice-version')
hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in [
    Path('/candidate/project/rc_filter.kicad_sch'),
    Path('/candidate/project/rc_filter.kicad_pcb'),
    Path('/candidate/requirements/fixture-requirements.md')]}
sch = sexpr(Path('/candidate/project/rc_filter.kicad_sch'))
pcb = sexpr(Path('/candidate/project/rc_filter.kicad_pcb'))
native_components = {props(s)['Reference']: {
    'value': props(s)['Value'], 'lib_id': one(s, 'lib_id')[1],
    'dnp': one(s, 'dnp')[1], 'uuid': one(s, 'uuid')[1]}
    for s in children(sch, 'symbol')}
board_components = {props(f)['Reference']: {
    'value': props(f)['Value'],
    'pads': {p[1]: one(p, 'net')[1] for p in children(f, 'pad')}}
    for f in children(pcb, 'footprint')}
tracks = [{'uuid': one(t, 'uuid')[1], 'width_mm': float(one(t, 'width')[1]),
           'net': one(t, 'net')[1]} for t in children(pcb, 'segment')]
assert not children(pcb, 'arc')
run(['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml',
     '-o', str(OUT / 'independent-netlist.xml'),
     '/candidate/project/rc_filter.kicad_sch'], 'netlist-export')

def nets(path):
    root = ET.parse(path).getroot()
    return {n.attrib['name']: sorted(x.attrib['ref'] + '.' + x.attrib['pin']
            for x in n.findall('node')) for n in root.findall('./nets/net')}

exported_nets = nets(OUT / 'independent-netlist.xml')
assert exported_nets == nets('/evidence/work/netlist/design.xml')
assert exported_nets == {'/IN': ['J1.1', 'R1.1'],
    '/OUT': ['C1.1', 'J1.2', 'R1.2'], 'GND': ['C1.2', 'J1.3']}
for ref, bc in board_components.items():
    assert bc['value'] == native_components[ref]['value']
    for pin, net in bc['pads'].items():
        assert ref + '.' + pin in exported_nets[net]

# Unit conversion is explicit for the two actual native passive values.
assert native_components['R1']['value'] == '100k'
assert native_components['C1']['value'] == '100n'
R, C = 100e3, 100e-9
tau = R*C
calc = {'R_ohm': R, 'C_F': C, 'tau_s': tau,
        'formula': 'Vout(t) = 1 V * (1 - exp(-t/(R*C)))',
        'vout_1ms_V': -math.expm1(-0.001/tau),
        'vout_5ms_V': -math.expm1(-0.005/tau),
        'required_1ms_V': [0.62, 0.65],
        'allowed_tau_s': [-0.001/math.log(1-0.65), -0.001/math.log(1-0.62)]}

# Independent simulation: delay edge by 1 ms so the initial DC solution and
# measurement reference are explicit. Rise 1 ns; sample 1/5 ms after edge onset.
deck = '''* Independent native-value unloaded RC response
V1 in 0 PWL(0 0 1m 0 1.000001m 1 6.1m 1)
R1 in out 100k
C1 out 0 100n
.options reltol=1e-8 abstol=1e-14 vntol=1e-10
.tran 1u 6.1m 0 1u
.meas tran vout_1ms FIND v(out) AT=2m
.meas tran vout_5ms FIND v(out) AT=6m
.print tran v(in) v(out)
.end
'''
(OUT / 'independent.cir').write_text(deck)
run(['ngspice', '-n', '-b', '-o', str(OUT / 'independent.log'),
     str(OUT / 'independent.cir')], 'independent-spice')
log = (OUT / 'independent.log').read_text()
measurements = {name: float(value) for name, value in re.findall(
    r'(vout_[15]ms)\s*=\s*([\deE+.-]+)', log)}
assert len(measurements) == 2
assert abs(measurements['vout_1ms'] - calc['vout_1ms_V']) < 1e-6

# Directly inspect supplied binary raw waveform, rather than trust check status.
raw = Path('/evidence/work/rc-step/result.raw').read_bytes()
header, data = raw.split(b'Binary:\n', 1)
header = header.decode()
nvar = int(re.search(r'No. Variables:\s*(\d+)', header)[1])
npoint = int(re.search(r'No. Points:\s*(\d+)', header)[1])
assert nvar == 4 and len(data) == nvar*npoint*8
rows = list(struct.iter_unpack('<' + 'd'*nvar, data))
def sample(t):
    for a, b in zip(rows, rows[1:]):
        if a[0] <= t <= b[0]:
            w = (t-a[0])/(b[0]-a[0])
            return [a[i] + w*(b[i]-a[i]) for i in range(nvar)]
    raise ValueError(t)
raw_summary = {'header': header, 'encoding': 'little-endian IEEE doubles',
               'initial': rows[0], 'final': rows[-1],
               'at_1ms': sample(.001), 'at_5ms': sample(.005)}
result = {'input_sha256': hashes, 'native_components': native_components,
          'board_components': board_components, 'exported_nets': exported_nets,
          'tracks': tracks, 'track_min_width_mm': min(t['width_mm'] for t in tracks),
          'calculation': calc, 'independent_measurements_V': measurements,
          'supplied_raw_waveform': raw_summary}
(OUT / 'audit-results.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({'calculation': calc, 'independent_measurements_V': measurements,
                  'track_min_width_mm': result['track_min_width_mm'],
                  'raw_at_1ms': raw_summary['at_1ms']}, indent=2))
