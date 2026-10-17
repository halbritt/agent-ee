import hashlib
import json
import math
import pathlib
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

out = pathlib.Path('/work/experiments')
root = pathlib.Path('/candidate')
evidence = pathlib.Path('/evidence')
hashes = {}
for name in ['requirements/fixture-requirements.md', 'project/rc_filter.kicad_sch', 'project/rc_filter.kicad_pcb']:
    hashes['inputs/' + name] = hashlib.sha256((root / name).read_bytes()).hexdigest()
pcb = (root / 'project/rc_filter.kicad_pcb').read_text()
sch = (root / 'project/rc_filter.kicad_sch').read_text()
segments = re.findall(r'\(segment\s+(.*?)\n\t\)', pcb, re.S)
widths = [float(re.search(r'\(width ([\d.]+)\)', segment)[1]) for segment in segments]
assert len(widths) == 5 and min(widths) >= .2
assert '(property "Value" "10k"' in sch
assert '(property "Value" "100n"' in sch
netlist = ET.parse(evidence / 'work/netlist/design.xml').getroot()
values = {c.attrib['ref']: c.findtext('value') for c in netlist.findall('components/comp')}
nets = {n.attrib['name']: sorted(node.attrib['ref'] + '.' + node.attrib['pin'] for node in n.findall('node')) for n in netlist.findall('nets/net')}
assert values == {'C1': '100n', 'J1': 'Conn_01x03', 'R1': '10k'}
assert nets == {'/IN': ['J1.1', 'R1.1'], '/OUT': ['C1.1', 'J1.2', 'R1.2'], 'GND': ['C1.2', 'J1.3']}
reports = {name: json.loads((evidence / ('checks/' + name + '.json')).read_text()) for name in ['erc', 'drc', 'netlist', 'geometry', 'rc-step']}
erc = json.loads((evidence / 'work/erc/erc.json').read_text())
drc = json.loads((evidence / 'work/drc/drc.json').read_text())
assert all(not s['violations'] for s in erc['sheets'])
assert not any(drc[key] for key in ['violations', 'unconnected_items', 'schematic_parity'])
log = (evidence / 'work/rc-step/ngspice.log').read_text()
measured = float(re.search(r'vout_1ms\s*=\s*([\deE.+-]+)', log)[1])
analytic = 1 - math.exp(-.001 / (10000 * 100e-9))
assert .62 <= measured <= .65
assert abs(analytic - measured) < 1e-6
versions = {}
for argv in [['kicad-cli', '--version'], ['ngspice', '--version']]:
    p = subprocess.run(argv, text=True, capture_output=True)
    versions[argv[0]] = {'argv': argv, 'returncode': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr}
result = {'command': 'python3 /work/experiments/audit.py', 'python': sys.version, 'versions': versions, 'hashes': hashes, 'values': values, 'nets': nets, 'track_widths_mm': widths, 'analytic_vout_1ms': analytic, 'recorded_vout_1ms': measured, 'check_outcomes': {name: {'execution': r['execution'], 'outcome': r['outcome']} for name, r in reports.items()}, 'outcome': 'All audited declared criteria pass; no supplied finding IDs.', 'limitations': ['No native check reruns were necessary; fresh execution reports were inspected.', 'No zone refill or candidate mutation.', 'Ideal nominal unloaded response only; no claims about hardware, tolerances, loading, temperature, ADC sampling or EMI.']}
(out / 'audit.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
