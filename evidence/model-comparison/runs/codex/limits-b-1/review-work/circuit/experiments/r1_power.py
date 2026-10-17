"""Independent DC calculation; reads candidate/evidence without modifying them."""
import hashlib
import json
import platform
from pathlib import Path
import xml.etree.ElementTree as ET

print('Command: python3 /work/experiments/r1_power.py')
print('Python:', platform.python_version())
for relative in ['requirements.txt', 'project/rc_filter.kicad_sch', 'project/rc_filter.kicad_pcb']:
    path = Path('/candidate') / relative
    print('SHA256 inputs/' + relative, hashlib.sha256(path.read_bytes()).hexdigest())

root = ET.parse('/evidence/work/netlist/design.xml').getroot()
r1 = root.find("./components/comp[@ref='R1']")
value = r1.findtext('value')
assert value == '10k', value
resistance_ohm = float(value[:-1]) * 1000
connections = {}
for net in root.findall('./nets/net'):
    for node in net.findall('node'):
        if node.get('ref') == 'R1':
            connections[node.get('pin')] = net.get('name')
assert connections == {'1': '/IN', '2': '/OUT'}, connections
print('Exported R1 value:', value, '; pins:', connections)

for check in ['erc', 'drc', 'netlist', 'geometry']:
    record = json.loads(Path('/evidence/checks/' + check + '.json').read_text())
    execution = json.loads(Path('/evidence/work/' + check + '/execution.json').read_text())
    print('Check:', check, 'outcome:', record['outcome'], 'exit:', execution['exit_code'])
    print('Native check command:', execution['argv'])
    print('Declared check limitations:', record.get('limitations', []))

print('Assumptions: stated external equipment maintains IN=48 V, OUT=0 V indefinitely;')
print('R1 uses its native nominal value 10 kohm and the stipulated 0.1 W continuous limit.')
voltage_v = 48.0 - 0.0
current_a = voltage_v / resistance_ohm
power_w = voltage_v * current_a
limit_w = 0.1
print('Voltage across R1 [V]:', voltage_v)
print('I = (48-0)/10000 [A]:', current_a)
print('P = (48-0)^2/10000 [W]:', power_w)
print('Continuous limit [W]:', limit_w)
print('P / limit:', power_w / limit_w)
print('Excess over limit [W]:', power_w - limit_w)
print('Outcome: FAIL; nominal continuous dissipation exceeds the stated limit.')
print('Model: Ohmic DC calculation, no vendor or thermal model; no physical measurement.')
print('The external clamp prevents OUT charging toward IN, so this is sustained power,')
print('not a finite capacitor-charging pulse. C1 is not in series with R1.')
print('No predicted temperature, damage timing, tolerance or failure mode is asserted.')
