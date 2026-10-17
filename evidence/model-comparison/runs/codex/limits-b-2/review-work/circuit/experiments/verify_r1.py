"""Read-only circuit review; outputs remain in /work/experiments."""
from pathlib import Path
from decimal import Decimal
import hashlib
import json
import platform
import subprocess
import xml.etree.ElementTree as ET

OUT = Path('/work/experiments')
OUT.mkdir(exist_ok=True)
notes = [
    'Command: python3 /work/experiments/verify_r1.py',
    'Python: ' + platform.python_version(),
    'Scope: R1 only, /IN held at 48 V DC, /OUT held at 0 V indefinitely;',
    'external clamp; fitted R1 continuous power limit is the stated 0.1 W.',
    'No rating inferred from the 0805 footprint. No vendor part model assumed.',
]
for cmd in [['kicad-cli', 'version'], ['ngspice', '--version']]:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    notes += ['Command: ' + ' '.join(cmd), 'Exit: ' + str(proc.returncode), proc.stdout + proc.stderr]

expected = {
    '/candidate/requirements.txt': '459b1c6ae69288bce5fd646e3a27c598f38f3eb6274699277927b254f3153d1c',
    '/candidate/project/rc_filter.kicad_sch': 'ecc56a68a96b9297d8751ae7686a9c8818e9cac84faefafa364b06a0af8a342b',
    '/candidate/project/rc_filter.kicad_pcb': 'f8264d95e52dc3c3d48358696c87294c6bbe7ff83102ecc9e0c4c3c27c7e72de',
}
for filename, digest in expected.items():
    p = Path(filename)
    actual = hashlib.sha256(p.read_bytes()).hexdigest()
    assert actual == digest, filename
    notes += [f'SHA256 confirmed: {filename} {actual}']

notes += ['Requirements read: ' + Path('/candidate/requirements.txt').read_text()]
for filename, start, end in [
    ('/candidate/project/rc_filter.kicad_sch', 1250, 1295),
    ('/candidate/project/rc_filter.kicad_sch', 723, 853),
    ('/candidate/project/rc_filter.kicad_pcb', 239, 265),
    ('/candidate/project/rc_filter.kicad_pcb', 358, 374),
]:
    lines = Path(filename).read_text().splitlines()
    notes += [filename, '\n'.join(f'{i}: {lines[i-1]}' for i in range(start, end+1))]

# Re-export from the original, unchanged schematic to verify the supplied netlist.
cmd = ['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml',
       '-o', str(OUT / 'native_netlist.xml'), '/candidate/project/rc_filter.kicad_sch']
proc = subprocess.run(cmd, capture_output=True, text=True)
notes += ['Command: ' + ' '.join(cmd), 'Exit: ' + str(proc.returncode), proc.stdout + proc.stderr]
assert proc.returncode == 0

def extract(path):
    root = ET.parse(path).getroot()
    values = {c.attrib['ref']: c.findtext('value') for c in root.findall('components/comp')}
    nets = {n.attrib['name']: sorted((v.attrib['ref'], v.attrib['pin']) for v in n.findall('node'))
            for n in root.findall('nets/net')}
    return values, nets

values, nets = extract(OUT / 'native_netlist.xml')
assert (values, nets) == extract('/evidence/work/netlist/design.xml')
assert values['R1'] == '10k'
assert ('R1', '1') in nets['/IN'] and ('R1', '2') in nets['/OUT']
notes += ['Fresh export matches supplied netlist component values and all net memberships.',
          json.dumps({'values': values, 'nets': nets}, indent=2)]

for name in ['erc', 'drc', 'netlist', 'geometry']:
    check = json.loads(Path(f'/evidence/checks/{name}.json').read_text())
    execution = json.loads(Path(f'/evidence/work/{name}/execution.json').read_text())
    console = Path(f'/evidence/work/{name}/console.log').read_bytes()
    assert hashlib.sha256(console).hexdigest() == execution['console_sha256']
    notes += [f'{name}: ' + json.dumps(check),
              'Recorded command: ' + json.dumps(execution['argv']),
              'Recorded exit: ' + str(execution['exit_code']),
              'Console (hash confirmed): ' + console.decode()]
for filename in ['/evidence/work/erc/erc.json', '/evidence/work/drc/drc.json']:
    notes += [filename, Path(filename).read_text()]
geometry = json.loads(Path('/evidence/work/geometry/geometry.json').read_text())
notes += ['Saved geometry R1: ' + json.dumps([f for f in geometry['footprints'] if f['ref'] == 'R1'])]

v = Decimal('48') - Decimal('0')
r = Decimal('10000')
limit = Decimal('0.1')
results = {
    'method': 'Ohm law and resistor dissipation; steady DC with both terminal voltages imposed externally',
    'R1_ohm': str(r), 'R1_voltage_V': str(v),
    'R1_current_A': str(v/r), 'R1_power_W': str(v*v/r),
    'continuous_limit_W': str(limit), 'power_to_limit_ratio': str(v*v/r/limit),
    'excess_power_W': str(v*v/r-limit),
    'outcome': 'FAIL: R1 exceeds the stated continuous power limit',
}
(OUT / 'r1_calculation.json').write_text(json.dumps(results, indent=2) + '\n')
notes += [json.dumps(results, indent=2),
    'Model/stimulus review: supplied checks contain no electrical simulation, clamp stimulus or power measurement.',
    'ERC/DRC report zero violations for enabled checks, but do not demonstrate this power criterion.',
    'C1 is between /OUT and GND; it cannot relieve R1 while /OUT remains externally clamped.',
    'No ngspice run needed: fixed DC terminal voltages and a linear resistor give the result directly.',
    'Omissions: no physical measurements, thermal model, temperature rise, time-to-failure, tolerance or vendor rating verification.',
    'No startup, shutdown, reset, brownout, AC filter or other operating conditions evaluated: outside stated scope.',
    'ERC/DRC not rerun; inspected fresh native reports and execution records, including ignored checks.',
    'Geometry uses saved fill; no refill, fabrication review or unstated geometric requirement applied.',
    'Geometry console contains PROPERTY_ENUM assertions; R1 pad data agrees with native PCB. Not treated as an electrical finding.',
    'All candidate files remained unchanged. No prior reviewer experiments or credential files read.',
]
(OUT / 'review_log.txt').write_text('\n\n'.join(notes) + '\n')
print(json.dumps(results, indent=2))
print('Review record: /work/experiments/review_log.txt')
