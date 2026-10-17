"""Read-only circuit review. Run: python3 /work/experiments/review.py."""
import hashlib, json, math, os, pathlib, re, struct, subprocess, sys
import xml.etree.ElementTree as ET

ROOT = pathlib.Path('/work/experiments')
ROOT.mkdir(exist_ok=True)
env = os.environ.copy()
env.update(KICAD_CONFIG_HOME=str(ROOT/'kicad-config'),
           KICAD10_SYMBOL_DIR='/candidate/stock/symbols',
           KICAD10_FOOTPRINT_DIR='/candidate/stock/footprints',
           KICAD10_TEMPLATE_DIR='/candidate/stock/template')
commands = []
def run(argv, stem):
    p = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, text=True)
    (ROOT/(stem+'.stdout')).write_text(p.stdout)
    (ROOT/(stem+'.stderr')).write_text(p.stderr)
    commands.append(dict(argv=argv, cwd=str(ROOT), exit_code=p.returncode,
                         stdout=stem+'.stdout', stderr=stem+'.stderr'))
    (ROOT/'commands.json').write_text(json.dumps(commands, indent=2))
    if p.returncode:
        raise RuntimeError(f'{argv}: {p.returncode}: {p.stderr}')
    return p.stdout

def parse(path):
    text = path.read_text()
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|[()]|[^\s()]+', text)
    stack, root = [], None
    for token in tokens:
        if token == '(':
            node = []
            if stack: stack[-1].append(node)
            else: root = node
            stack.append(node)
        elif token == ')': stack.pop()
        else: stack[-1].append(json.loads(token) if token.startswith('"') else token)
    assert not stack
    return root
def children(n, name):
    return [x for x in n if isinstance(x, list) and x and x[0] == name]
def child(n, name):
    return children(n, name)[0]
def props(n):
    return {p[1]:p[2] for p in children(n, 'property')}

run(['python3', '--version'], 'python-version')
run(['kicad-cli', '--version'], 'kicad-version')
run(['ngspice', '--version'], 'ngspice-version')
source_paths = [pathlib.Path('/candidate/project/rc_filter.kicad_sch'),
                pathlib.Path('/candidate/project/rc_filter.kicad_pcb'),
                pathlib.Path('/candidate/requirements/fixture-requirements.md')]
hashes = {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths}
sch, pcb = map(parse, source_paths[:2])
native_symbols = []
for s in children(sch, 'symbol'):
    native_symbols.append(dict(properties=props(s), lib_id=child(s,'lib_id')[1],
        uuid=child(s,'uuid')[1], dnp=child(s,'dnp')[1],
        exclude_from_sim=child(s,'exclude_from_sim')[1]))
board_footprints = []
for f in children(pcb, 'footprint'):
    board_footprints.append(dict(properties=props(f), footprint=f[1],
        pads=[dict(number=p[1], net=child(p,'net')[1], at=child(p,'at')[1:])
              for p in children(f,'pad')]))
tracks = [dict(uuid=child(s,'uuid')[1], width_mm=float(child(s,'width')[1]),
               start=child(s,'start')[1:], end=child(s,'end')[1:], net=child(s,'net')[1])
          for s in children(pcb,'segment')]
native = dict(hashes=hashes, symbols=native_symbols, board_footprints=board_footprints,
              tracks=tracks, minimum_track_width_mm=min(t['width_mm'] for t in tracks))
(ROOT/'native-inspection.json').write_text(json.dumps(native, indent=2))
run(['kicad-cli','sch','export','netlist','--format','kicadxml','-o',str(ROOT/'native.xml'),str(source_paths[0])], 'export')
run(['kicad-cli','sch','erc','--format','json','--severity-all','--units','mm','-o',str(ROOT/'erc.json'),str(source_paths[0])], 'erc')
run(['kicad-cli','pcb','drc','--format','json','--severity-all','--schematic-parity','--units','mm','-o',str(ROOT/'drc.json'),str(source_paths[1])], 'drc')
xml = ET.parse(ROOT/'native.xml').getroot()
values = {c.attrib['ref']:c.findtext('value') for c in xml.findall('./components/comp')}
nets = {n.attrib['name']:sorted((p.attrib['ref'],p.attrib['pin']) for p in n.findall('node')) for n in xml.findall('./nets/net')}
old = ET.parse('/evidence/work/netlist/design.xml').getroot()
old_nets = {n.attrib['name']:sorted((p.attrib['ref'],p.attrib['pin']) for p in n.findall('node')) for n in old.findall('./nets/net')}
assert nets == old_nets
assert values == {s['properties']['Reference']:s['properties']['Value'] for s in native_symbols if not s['properties']['Reference'].startswith('#')}
assert {n:sorted((f['properties']['Reference'],p['number']) for f in board_footprints for p in f['pads'] if p['net']==n) for n in nets} == nets
assert values['R1']=='100k' and values['C1']=='100n'
assert nets == {'/IN':[('J1','1'),('R1','1')], '/OUT':[('C1','1'),('J1','2'),('R1','2')], 'GND':[('C1','2'),('J1','3')]}

# Validate supplied raw data independently of .meas text (native doubles, real transient).
raw = pathlib.Path('/evidence/work/rc-step/result.raw').read_bytes()
header, data = raw.split(b'Binary:\n',1)
header = header.decode()
nv = int(re.search(r'No. Variables:\s*(\d+)',header)[1])
np = int(re.search(r'No. Points:\s*(\d+)',header)[1])
assert nv == 4 and len(data)==nv*np*8
assert 'real' in header and re.search(r'2\s+v\(out\)',header)
rows = list(struct.iter_unpack('=dddd',data))
def interp(t):
    for a,b in zip(rows,rows[1:]):
        if a[0] <= t <= b[0]: return a[2]+(b[2]-a[2])*(t-a[0])/(b[0]-a[0])
    raise ValueError(t)
R, C = 100000.0, 100e-9
tau = R*C
calc = dict(assumptions=['Ideal 0-to-1 V input, initially discharged capacitor, no external load.',
    'Only populated ideal R1 and C1 modeled; connector and power flags carry no element.',
    'Existing and independent decks approximate the step by a 1 ns ramp; error relative to ideal at 1 ms is about 45 nV.',
    'No candidate edits or zone refill performed.'],
    values=values, nets=nets, tau_s=tau, formula='Vout(t) = 1 - exp(-t/(R*C))',
    analytic_V={str(t):1-math.exp(-t/tau) for t in [0.001,0.005]},
    allowed_R_ohm_for_C_100n=[-0.001/(C*math.log(1-v)) for v in [0.65,0.62]],
    supplied_raw_header=header, supplied_raw_interpolated_V={str(t):interp(t) for t in [0.001,0.005]},
    supplied_raw_max_abs_error_from_ideal_V=max(abs(row[2]-(1-math.exp(-row[0]/tau))) for row in rows),
    required_1ms_range_V=[0.62,0.65])
(ROOT/'calculation.json').write_text(json.dumps(calc, indent=2))

# Generate solely from the independently re-exported native component values and verified nodes.
deck = '* Independent native-derived unloaded RC check\n'
deck += f'R1 IN OUT {values["R1"]}\nC1 OUT 0 {values["C1"]}\n'
deck += 'VSTEP IN 0 PWL(0 0 1n 1)\n.options reltol=1e-7 abstol=1e-14\n.tran 1u 5m 0 1u\n'
deck += '.meas tran vout_1ms FIND v(OUT) AT=1m\n.meas tran vout_5ms FIND v(OUT) AT=5m\n.end\n'
(ROOT/'independent.cir').write_text(deck)
run(['ngspice','-n','-b','-o',str(ROOT/'independent.log'),str(ROOT/'independent.cir')], 'independent')
after = {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths}
assert hashes==after
print(json.dumps(calc,indent=2))
print((ROOT/'independent.log').read_text())
print('Native track widths:',[t['width_mm'] for t in tracks])
