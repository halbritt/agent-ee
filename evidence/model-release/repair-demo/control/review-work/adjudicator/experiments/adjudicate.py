import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

out = Path('/work/experiments')
def sexp(path):
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|[^\s()]+|[()]', Path(path).read_text())
    stack = []
    root = None
    for t in tokens:
        if t == '(':
            x = []
            if stack:
                stack[-1].append(x)
            else:
                root = x
            stack.append(x)
        elif t == ')':
            stack.pop()
        else:
            stack[-1].append(json.loads(t) if t.startswith('"') else t)
    return root

def children(x, key):
    return [v for v in x if isinstance(v, list) and v[0] == key]

def one(x, key):
    return children(x, key)[0]

pcb = sexp('/candidate/project/rc_filter.kicad_pcb')
sch = sexp('/candidate/project/rc_filter.kicad_sch')
symbols = {}
for s in children(sch, 'symbol'):
    props = {p[1]: p[2] for p in children(s, 'property')}
    symbols[props['Reference']] = props['Value']
assert symbols['R1'] == '10k' and symbols['C1'] == '100n'
segments = [{'uuid': one(s, 'uuid')[1], 'width_mm': float(one(s, 'width')[1]),
             'net': one(s, 'net')[1]} for s in children(pcb, 'segment')]
assert len(segments) == 5 and all(s['width_mm'] >= .2 for s in segments)
xml = ET.parse('/evidence/work/netlist/design.xml').getroot()
nets = {n.attrib['name']: sorted(f"{p.attrib['ref']}.{p.attrib['pin']}" for p in n.findall('node'))
        for n in xml.findall('nets/net')}
assert nets == {'/IN': ['J1.1', 'R1.1'], '/OUT': ['C1.1', 'J1.2', 'R1.2'], 'GND': ['C1.2', 'J1.3']}
pads = {}
for fp in children(pcb, 'footprint'):
    ref = next(p[2] for p in children(fp, 'property') if p[1] == 'Reference')
    for pad in children(fp, 'pad'):
        pads.setdefault(one(pad, 'net')[1], []).append(f'{ref}.{pad[1]}')
assert {k: sorted(v) for k, v in pads.items()} == nets
checks = {k: json.loads(Path(f'/evidence/checks/{k}.json').read_text())
          for k in ['erc', 'drc', 'netlist', 'geometry', 'rc-step']}
assert all(c['execution'] == 'ok' for c in checks.values())
assert all(checks[k]['outcome'] == 'pass' for k in ['erc', 'drc', 'geometry', 'rc-step'])
erc = json.loads(Path('/evidence/work/erc/erc.json').read_text())
drc = json.loads(Path('/evidence/work/drc/drc.json').read_text())
assert all(not s['violations'] for s in erc['sheets'])
assert all(not drc[k] for k in ['violations', 'unconnected_items', 'schematic_parity'])
deck = Path('/evidence/work/rc-step/deck.cir').read_text()
assert 'C1 OUT 0 100n' in deck and 'R1 IN OUT 10k' in deck
assert 'VSTEP IN 0 PWL(0 0 1n 1)' in deck
analytic = 1 - math.exp(-.001/(10000*100e-9))
measured = checks['rc-step']['details']['measurements']['vout_1ms']['measured']
assert .62 <= analytic <= .65 and abs(analytic-measured) < 1e-6
commands = []
for args in [['python3', '--version'], ['kicad-cli', 'version'], ['ngspice', '--version']]:
    r = subprocess.run(args, capture_output=True, text=True)
    commands.append({'argv': args, 'exit_code': r.returncode, 'stdout': r.stdout, 'stderr': r.stderr})
result = {
    'native_sha256': {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in
                      [Path('/candidate/project/rc_filter.kicad_sch'), Path('/candidate/project/rc_filter.kicad_pcb')]},
    'symbols': symbols, 'nets': nets, 'pcb_pads_match_exported_netlist': True,
    'segments': segments, 'analytic_vout_1ms': analytic, 'supplied_vout_1ms': measured,
    'check_execution_and_outcome': {k: [v['execution'], v['outcome']] for k, v in checks.items()},
    'erc_ignored_checks': erc['ignored_checks'], 'drc_ignored_checks': drc['ignored_checks'],
    'versions': commands, 'finding_ids_supplied': [], 'decisions': []
}
(out/'analysis.json').write_text(json.dumps(result, indent=2)+'\n')
print(json.dumps(result, indent=2))
