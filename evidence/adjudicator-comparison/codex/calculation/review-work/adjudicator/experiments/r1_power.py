"""Read-only verification of R1 under the declared external clamp condition."""
import hashlib
import json
import platform
import re
from decimal import Decimal
from pathlib import Path
import xml.etree.ElementTree as ET

root = Path('/candidate')
sch = (root / 'project/rc_filter.kicad_sch').read_text()
pcb = (root / 'project/rc_filter.kicad_pcb').read_text()
r1 = next(b for b in sch.split('\n\t(symbol\n')[1:]
          if '(property "Reference" "R1"' in b)
value = re.search(r'\(property "Value" "([^"]+)"', r1).group(1)
assert value == '10k'
assert '(dnp no)' in r1
board_r1 = next(b for b in pcb.split('\n\t(footprint ')[1:]
                if '(property "Reference" "R1"' in b)
assert '(property "Value" "10k"' in board_r1
assert re.search(r'\(pad "1".*?\(net "/IN"\)', board_r1, re.S)
assert re.search(r'\(pad "2".*?\(net "/OUT"\)', board_r1, re.S)
tree = ET.parse('/evidence/work/netlist/design.xml')
assert tree.find("./components/comp[@ref='R1']/value").text == value
nets = {n.attrib['name']: [f"{x.attrib['ref']}.{x.attrib['pin']}"
                         for x in n.findall('node')]
        for n in tree.findall('./nets/net')}
assert 'R1.1' in nets['/IN'] and 'R1.2' in nets['/OUT']
vin, vout, resistance, limit = map(Decimal, ['48', '0', '10000', '0.1'])
voltage = vin - vout
current = voltage / resistance
power = voltage ** 2 / resistance
assert power == current ** 2 * resistance == voltage * current
result = {
    'command': 'python3 /work/experiments/r1_power.py',
    'python_version': platform.python_version(),
    'input_sha256': {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in [root / 'requirements.txt',
                               root / 'project/rc_filter.kicad_sch',
                               root / 'project/rc_filter.kicad_pcb',
                               Path('/evidence/work/netlist/design.xml')]},
    'native_value': value,
    'verified_nets': nets,
    'assumptions': 'Use the declared 0.1 W fitted-part limit; external equipment maintains IN=48 V and OUT=0 V indefinitely. Use native nominal 10k value. No extra rating, tolerance, or derating requirement is imposed.',
    'voltage_across_R1_V': str(voltage),
    'R1_ohm': str(resistance),
    'R1_current_A': str(current),
    'R1_power_W': str(power),
    'continuous_limit_W': str(limit),
    'power_over_limit_ratio': str(power / limit),
    'excess_W': str(power - limit),
    'assessment': 'defect' if power > limit else 'no_defect',
    'limitations': 'Analytical nominal DC evaluation, no hardware or thermal simulation. Establishes continuous power-limit violation, not temperature, damage time, or failure mode. External clamp need not appear in native board. No candidate modifications.'
}
print(json.dumps(result, indent=2))
Path('/work/experiments/r1_power_result.json').write_text(json.dumps(result, indent=2) + '\n')
