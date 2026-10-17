import hashlib
import json
import math
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path('/work/experiments')

def parse(path):
    tokens = iter(re.findall(r'"(?:\\.|[^"\\])*"|[()]|[^\s()]+', Path(path).read_text()))
    def walk():
        result = []
        for token in tokens:
            if token == ')':
                return result
            result.append(walk() if token == '(' else json.loads(token) if token.startswith('"') else token)
        return result
    return walk()[0]

def nodes(parent, tag):
    return [x for x in parent if isinstance(x, list) and x and x[0] == tag]

def field(parent, tag):
    return nodes(parent, tag)[0][1]

sch = parse('/candidate/project/rc_filter.kicad_sch')
pcb = parse('/candidate/project/rc_filter.kicad_pcb')
symbols = {}
for symbol in nodes(sch, 'symbol'):
    props = {x[1]: x[2] for x in nodes(symbol, 'property')}
    symbols[props['Reference']] = props['Value']
assert symbols['R1'] == '10k' and symbols['C1'] == '100n'
tracks = [{'uuid': field(x, 'uuid'), 'width_mm': float(field(x, 'width')), 'net': field(x, 'net')} for x in nodes(pcb, 'segment')]
assert len(tracks) == 5 and all(x['width_mm'] >= 0.20 for x in tracks)
xml = ET.parse('/evidence/work/netlist/design.xml').getroot()
nets = {n.attrib['name']: sorted(x.attrib['ref'] + '.' + x.attrib['pin'] for x in n.findall('node')) for n in xml.findall('nets/net')}
assert nets == {'/IN': ['J1.1', 'R1.1'], '/OUT': ['C1.1', 'J1.2', 'R1.2'], 'GND': ['C1.2', 'J1.3']}
pad_nets = {}
for fp in nodes(pcb, 'footprint'):
    ref = next(p[2] for p in nodes(fp, 'property') if p[1] == 'Reference')
    for p in nodes(fp, 'pad'):
        pad_nets[ref + '.' + p[1]] = field(p, 'net')
assert all(pad_nets[pin] == net for net, pins in nets.items() for pin in pins)
erc = json.loads(Path('/evidence/work/erc/erc.json').read_text())
drc = json.loads(Path('/evidence/work/drc/drc.json').read_text())
assert all(not s['violations'] for s in erc['sheets'])
assert all(not drc[k] for k in ('violations', 'unconnected_items', 'schematic_parity'))
log = Path('/evidence/work/rc-step/ngspice.log').read_text()
measured = float(re.search(r'vout_1ms\s*=\s*([\d.eE+-]+)', log)[1])
analytic = 1 - math.exp(-0.001 / (10000 * 100e-9))
assert 0.62 <= measured <= 0.65 and 0.62 <= analytic <= 0.65
versions = {}
for argv in [['python3', '--version'], ['kicad-cli', 'version'], ['ngspice', '--version']]:
    result = subprocess.run(argv, capture_output=True, text=True)
    versions[' '.join(argv)] = {'exit_code': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}
hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path('/candidate/project/rc_filter.kicad_sch'), Path('/candidate/project/rc_filter.kicad_pcb'), Path('/candidate/requirements/fixture-requirements.md')]}
result = {'command': 'python3 /work/experiments/audit.py', 'versions': versions, 'candidate_sha256': hashes, 'native_values': symbols, 'native_tracks': tracks, 'exported_nets': nets, 'native_pad_nets': pad_nets, 'analytic_vout_1ms': analytic, 'fresh_check_vout_1ms': measured, 'erc_violations': 0, 'drc_violations': 0, 'drc_unconnected': 0, 'drc_parity_issues': 0, 'outcome': 'All audited declared criteria pass; no supplied finding IDs require a decision.'}
(ROOT / 'audit.json').write_text(json.dumps(result, indent=2) + '\n')
print(result['outcome'])
