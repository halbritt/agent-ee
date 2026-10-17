"""Independent read-only circuit checks; all generated files stay beside this script."""
import bisect
import concurrent.futures
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

OUT = Path(__file__).resolve().parent
ROOT = Path('/candidate')
EVID = Path('/evidence')
commands = []
env = dict(os.environ, KICAD_CONFIG_HOME=str(OUT / 'kicad-config'),
           KICAD10_SYMBOL_DIR='/candidate/stock/symbols',
           KICAD10_FOOTPRINT_DIR='/candidate/stock/footprints',
           KICAD10_TEMPLATE_DIR='/candidate/stock/template')

def run(name, argv):
    p = subprocess.run(argv, cwd=OUT, env=env, capture_output=True, text=True)
    (OUT / (name + '.stdout')).write_text(p.stdout)
    (OUT / (name + '.stderr')).write_text(p.stderr)
    record = dict(name=name, argv=argv, cwd=str(OUT), exit_code=p.returncode,
                  stdout=name+'.stdout', stderr=name+'.stderr')
    commands.append(record)
    return record

def sexp(path):
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|[^\s()]+|[()]', path.read_text())
    stack = []
    root = None
    for token in tokens:
        if token == '(':
            item = []
            if stack:
                stack[-1].append(item)
            else:
                root = item
            stack.append(item)
        elif token == ')':
            stack.pop()
        else:
            stack[-1].append(json.loads(token) if token.startswith('"') else token)
    assert not stack
    return root

def children(node, key):
    return [x for x in node if isinstance(x, list) and x and x[0] == key]

def child(node, key):
    return children(node, key)[0]

def props(node):
    return {x[1]: x[2] for x in children(node, 'property')}

for name, argv in [('kicad-version', ['kicad-cli', 'version']),
                   ('ngspice-version', ['ngspice', '--version']),
                   ('python-version', [sys.executable, '--version'])]:
    run(name, argv)

schpath = ROOT / 'project/rc_filter.kicad_sch'
pcbpath = ROOT / 'project/rc_filter.kicad_pcb'
tasks = [
    ('netlist', ['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml', '-o', str(OUT/'design.xml'), str(schpath)]),
    ('erc', ['kicad-cli', 'sch', 'erc', '--format', 'json', '--severity-all', '--units', 'mm', '-o', str(OUT/'erc.json'), str(schpath)]),
    ('drc', ['kicad-cli', 'pcb', 'drc', '--format', 'json', '--severity-all', '--schematic-parity', '--units', 'mm', '-o', str(OUT/'drc.json'), str(pcbpath)])]
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
    runs = list(pool.map(lambda task: run(*task), tasks))

sch = sexp(schpath)
pcb = sexp(pcbpath)
symbols = {props(s)['Reference']: {'properties': props(s), 'uuid': child(s,'uuid')[1],
             'library': child(s,'lib_id')[1], 'position_rotation': child(s,'at')[1:],
             'dnp': child(s,'dnp')[1]} for s in children(sch,'symbol')}
footprints = {}
for fp in children(pcb,'footprint'):
    p = props(fp)
    footprints[p['Reference']] = {'properties':p, 'library':fp[1],
        'at':child(fp,'at')[1:], 'pads':[
            {'number':pad[1], 'at':child(pad,'at')[1:], 'net':child(pad,'net')[1]}
            for pad in children(fp,'pad')]}
segments = [{'uuid':child(s,'uuid')[1], 'net':child(s,'net')[1],
             'start_mm':child(s,'start')[1:], 'end_mm':child(s,'end')[1:],
             'width_mm':float(child(s,'width')[1])}
             for s in children(pcb,'segment') + children(pcb,'arc')]
native = dict(symbols=symbols, footprints=footprints, segments=segments,
              via_count=len(children(pcb,'via')), zone_count=len(children(pcb,'zone')))
(OUT/'native_extract.json').write_text(json.dumps(native, indent=2)+'\n')

def xml_nets(path):
    tree = ET.parse(path)
    return {n.get('name'): sorted(x.get('ref')+'.'+x.get('pin') for x in n.findall('node'))
            for n in tree.findall('./nets/net')}

fresh_nets = xml_nets(OUT/'design.xml')
expected_nets = {'/IN':['J1.1','R1.1'], '/OUT':['C1.1','J1.2','R1.2'], 'GND':['C1.2','J1.3']}
board_nets = {}
for ref, fp in footprints.items():
    for pad in fp['pads']:
        board_nets.setdefault(pad['net'], []).append(ref+'.'+pad['number'])
board_nets = {k: sorted(v) for k,v in board_nets.items()}

# Independently inspect the retained binary waveform, little-endian doubles on this host.
raw = (EVID/'work/rc-step/result.raw').read_bytes()
head, payload = raw.split(b'Binary:\n', 1)
header = head.decode()
nvars = int(re.search(r'No. Variables:\s*(\d+)',header)[1])
npoints = int(re.search(r'No. Points:\s*(\d+)',header)[1])
assert len(payload) == 8*nvars*npoints
variables = re.findall(r'^\s*\d+\s+(\S+)\s+(\S+)', header, re.M)
rows = list(struct.iter_unpack('<'+'d'*nvars, payload))
times = [r[0] for r in rows]
outindex = [v[0] for v in variables].index('v(out)')
inindex = [v[0] for v in variables].index('v(in)')
assert all(times[i] < times[i+1] for i in range(len(times)-1))
def sample(t, index):
    j = bisect.bisect_left(times, t)
    if j == 0:
        return rows[j][index]
    if j == len(rows):
        assert abs(t-times[-1]) < 1e-14
        return rows[-1][index]
    a,b = rows[j-1],rows[j]
    return a[index]+(b[index]-a[index])*(t-a[0])/(b[0]-a[0])

R = float(symbols['R1']['properties']['Value'].removesuffix('k'))*1e3
C = float(symbols['C1']['properties']['Value'].removesuffix('n'))*1e-9
tau = R*C
rise = 1e-9
def ideal(t):
    return -math.expm1(-t/tau)
def ramp(t):
    if t <= rise:
        return (t + tau*math.expm1(-t/tau))/rise
    y_at_rise = (rise+tau*math.expm1(-rise/tau))/rise
    return 1-(1-y_at_rise)*math.exp(-(t-rise)/tau)
measurements = {str(t): {'ideal_step_V':ideal(t), 'one_ns_ramp_V':ramp(t),
                        'retained_waveform_V':sample(t,outindex)} for t in (1e-3,5e-3)}

# Separate deck constructed after checking native topology. Use a delayed rise to
# expose initial conditions and measure elapsed time from the stimulus edge.
deck = '''Independent ideal RC step validation from checked native values/topology
Vinput IN 0 PWL(0 0 100u 0 100.001u 1)
R1 IN OUT 10k
C1 OUT 0 100n
.options reltol=1e-8 abstol=1e-14 vntol=1e-10
.tran 0.5u 5.100001m 0 0.5u
.meas tran before_step FIND v(OUT) AT=99u
.meas tran vout_1ms FIND v(OUT) AT=1.100001m
.meas tran vout_5ms FIND v(OUT) AT=5.100001m
.end
'''
(OUT/'independent.cir').write_text(deck)
run('independent-spice', ['ngspice','-n','-b','-o',str(OUT/'independent-spice.log'),str(OUT/'independent.cir')])
native_checks = {}
for name in ('erc','drc'):
    native_checks[name] = json.loads((OUT/(name+'.json')).read_text())
check_records = {name:json.loads((EVID/'checks'/(name+'.json')).read_text())
                 for name in ('erc','drc','geometry','netlist','rc-step')}
summary = dict(native_hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
                             for p in (schpath,pcbpath,ROOT/'requirements/fixture-requirements.md')},
    fresh_nets=fresh_nets, board_pad_nets=board_nets,
    netlist_agrees_with_retained=fresh_nets==xml_nets(EVID/'work/netlist/design.xml'),
    topology_matches=board_nets==fresh_nets==expected_nets,
    segment_count=len(segments), minimum_width_mm=min(s['width_mm'] for s in segments),
    all_segments_meet_0_20_mm=all(s['width_mm']>=0.2 for s in segments),
    tau_s=tau, measurements=measurements,
    waveform=dict(variables=variables, points=npoints, start_s=times[0], stop_s=times[-1],
                  initial_vout_V=rows[0][outindex], final_input_V=rows[-1][inindex],
                  max_abs_error_vs_one_ns_ramp_V=max(abs(r[outindex]-ramp(r[0])) for r in rows)),
    fresh_native_checks=native_checks,
    captured_outcomes={k: {'execution':v['execution'], 'outcome':v['outcome']} for k,v in check_records.items()})
(OUT/'results.json').write_text(json.dumps(summary,indent=2)+'\n')
(OUT/'commands.json').write_text(json.dumps({'launch':['python3','/work/experiments/review.py'],
    'environment_overrides':{k:env[k] for k in ('KICAD_CONFIG_HOME','KICAD10_SYMBOL_DIR','KICAD10_FOOTPRINT_DIR','KICAD10_TEMPLATE_DIR')},
    'commands':commands},indent=2)+'\n')
print(json.dumps(summary,indent=2))
