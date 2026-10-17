import hashlib
import json
from pathlib import Path
import platform
import re
import subprocess
import xml.etree.ElementTree as ET

root = Path('/work/experiments')
result = {
    'command': 'python3 /work/experiments/verify_connector.py',
    'python_version': platform.python_version(),
    'kicad_version_command': ['kicad-cli', '--version'],
    'kicad_version': subprocess.run(['kicad-cli', '--version'], capture_output=True, text=True).stdout.strip(),
    'assumptions': ['The saved candidate and stated external connector contract are authoritative; no claimed repair is assumed.',
                    'Net roles are checked by component topology, not just IN/OUT labels.'],
    'sha256': {},
}
for name in ['requirements.txt', 'project/rc_filter.kicad_sch', 'project/rc_filter.kicad_pcb']:
    result['sha256'][name] = hashlib.sha256((Path('/candidate') / name).read_bytes()).hexdigest()
result['requirement'] = Path('/candidate/requirements.txt').read_text()
board = Path('/candidate/project/rc_filter.kicad_pcb').read_text()
pads = {}
for footprint in board.split('\n\t(footprint ')[1:]:
    ref = re.search(r'\(property "Reference" "([^"]+)"', footprint).group(1)
    for pad in footprint.split('\n\t\t(pad ')[1:]:
        number = re.match(r'"([^"]+)"', pad).group(1)
        net = re.search(r'\(net "([^"]+)"\)', pad).group(1)
        pads[f'{ref}.{number}'] = net
result['native_pcb_pad_nets'] = pads
xml = ET.parse('/evidence/work/netlist/design.xml')
nets = {net.get('name'): [node.get('ref') + '.' + node.get('pin') for node in net.findall('node')]
        for net in xml.findall('./nets/net')}
result['fresh_check_schematic_nets'] = nets
result['pcb_matches_schematic_netlist'] = all(pads[node] == net for net, nodes in nets.items() for node in nodes)
result['erc_violations'] = sum(len(s['violations']) for s in json.loads(Path('/evidence/work/erc/erc.json').read_text())['sheets'])
drc = json.loads(Path('/evidence/work/drc/drc.json').read_text())
result['drc_counts'] = {key: len(drc[key]) for key in ['violations', 'unconnected_items', 'schematic_parity']}
result['topology'] = 'J1.1 connects to R1.1 alone; J1.2 connects directly to C1.1 and R1.2; C1.2 and J1.3 are GND.'
result['outcome'] = 'Established defect: J1.2 is the capacitor/shunt node and J1.1 is the opposite side of the series resistor, reversing the required input/output roles. With input applied at contractual J1.2, C1 shunts the input and contractual output J1.1 is reached through R1 without the intended output shunt capacitor.'
result['limitations'] = ['Read-only inspection only; no candidate modification, board refill, repair verification, or physical measurement.',
                         'Existing native ERC/DRC pass demonstrates internal consistency, not compliance with the external connector contract. No extra performance limits imposed.']
assert result['pcb_matches_schematic_netlist']
assert pads['J1.1'] == pads['R1.1'] == '/IN'
assert pads['J1.2'] == pads['R1.2'] == pads['C1.1'] == '/OUT'
assert pads['J1.3'] == pads['C1.2'] == 'GND'
out = root / 'connector_verification.json'
out.write_text(json.dumps(result, indent=2) + '\n')
print(out.read_text())
