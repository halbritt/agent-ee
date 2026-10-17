"""Read-only native identity checks and stated-condition R1 calculation."""
import hashlib
import json
import platform
import re
import subprocess
from decimal import Decimal
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path('/work/experiments')

def parse_native(path):
    # KiCad S-expression reader; quoted strings decoded as JSON strings.
    stack = []
    root = None
    for tok in re.findall(r'"(?:\\.|[^"\\])*"|[()]|[^\s()]+', path.read_text()):
        if tok == '(':
            node = []
            if stack:
                stack[-1].append(node)
            else:
                root = node
            stack.append(node)
        elif tok == ')':
            stack.pop()
        else:
            stack[-1].append(json.loads(tok) if tok.startswith('"') else tok)
    assert not stack
    return root

def children(node, name):
    return [x for x in node if isinstance(x, list) and x and x[0] == name]

def props(node):
    return {x[1]: x[2] for x in children(node, 'property')}

sch_path = Path('/candidate/project/rc_filter.kicad_sch')
pcb_path = Path('/candidate/project/rc_filter.kicad_pcb')
sch = parse_native(sch_path)
pcb = parse_native(pcb_path)
r_sch = next(s for s in children(sch, 'symbol') if props(s).get('Reference') == 'R1')
r_pcb = next(f for f in children(pcb, 'footprint') if props(f).get('Reference') == 'R1')
pad_nets = {p[1]: children(p, 'net')[0][1] for p in children(r_pcb, 'pad')}
assert props(r_sch)['Value'] == props(r_pcb)['Value'] == '10k'
assert pad_nets == {'1': '/IN', '2': '/OUT'}
assert children(r_sch, 'dnp')[0][1] == 'no'

xml = ET.parse('/evidence/work/netlist/design.xml').getroot()
xml_value = xml.find("./components/comp[@ref='R1']/value").text
xml_nets = {n.attrib['name']: [(p.attrib['ref'], p.attrib['pin']) for p in n.findall('node')]
            for n in xml.findall('./nets/net')}
assert xml_value == '10k'
assert ('R1', '1') in xml_nets['/IN']
assert ('R1', '2') in xml_nets['/OUT']

vin, vout, resistance, limit = map(Decimal, ['12', '0', '10000', '0.1'])
voltage = vin - vout
current = voltage / resistance
power = voltage * current
checks = {}
for name in ['erc', 'drc', 'netlist', 'geometry']:
    directory = Path('/evidence/work') / name
    execution = json.loads((directory / 'execution.json').read_text())
    checks[name] = {k: execution[k] for k in ['argv', 'exit_code', 'execution']}
    if name in ['erc', 'drc']:
        report = json.loads((directory / f'{name}.json').read_text())
        checks[name]['report'] = report

result = {
    'command': 'python3 /work/experiments/r1_dc_review.py',
    'python_version': platform.python_version(),
    'kicad_version': subprocess.run(['kicad-cli', '--version'], capture_output=True, text=True).stdout.strip(),
    'native_sha256': {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in [sch_path, pcb_path]},
    'native_R1_value': props(r_sch)['Value'],
    'native_R1_pad_nets': pad_nets,
    'exported_R1_value': xml_value,
    'exported_nets': xml_nets,
    'stated_condition': {'IN_V': str(vin), 'OUT_V': str(vout), 'continuous_limit_W': str(limit), 'duration': 'indefinite'},
    'calculation': {'R_ohm': str(resistance), 'voltage_V': str(voltage), 'current_A': str(current),
                    'power_W': str(power), 'limit_fraction': str(power / limit),
                    'power_margin_W': str(limit - power), 'minimum_R_at_limit_ohm': str(voltage ** 2 / limit)},
    'outcome': 'R1 dissipates 0.0144 W, below the stated 0.1 W continuous limit; no defect identified.',
    'model': 'Ohmic 10 kohm resistor with externally fixed terminal voltages. C1 shunts OUT to GND and does not change R1 voltage under the imposed clamp.',
    'limitations': ['Nominal native resistance used; no resistance tolerance or temperature coefficient specified.',
                    'No hardware measurement, thermal model, or SPICE simulation performed; none is needed for this fixed-terminal DC power comparison.',
                    'Other components, other operating conditions, startup/shutdown/reset/brownout, qualification and unstated ratings are outside the exercise scope.',
                    'Existing native check reports inspected without rerun; saved zone fill only; ignored checks retained as recorded.'],
    'existing_checks': checks,
}
(ROOT / 'r1_dc_result.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({k: result[k] for k in ['python_version', 'kicad_version', 'native_R1_value', 'native_R1_pad_nets', 'calculation', 'outcome']}, indent=2))
