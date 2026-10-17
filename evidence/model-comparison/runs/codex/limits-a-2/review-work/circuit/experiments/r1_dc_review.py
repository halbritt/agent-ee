"""Read-only native/source-record inspection and the stated R1 DC calculation."""
from pathlib import Path
import hashlib
import json
import platform
import re
import subprocess
import xml.etree.ElementTree as ET

OUT = Path('/work/experiments')

def parse(path):
    tokens = iter(re.findall(r'"(?:\\.|[^"\\])*"|[()]|[^\s()]+', path.read_text()))
    def item(token):
        if token == '(':
            result = []
            for t in tokens:
                if t == ')':
                    return result
                result.append(item(t))
            raise ValueError('Unclosed expression')
        return json.loads(token) if token.startswith('"') else token
    return item(next(tokens))

def children(tree, tag):
    return [v for v in tree if isinstance(v, list) and v[0] == tag]

def child(tree, tag):
    return children(tree, tag)[0]

def props(tree):
    return {v[1]: v[2] for v in children(tree, 'property')}

schpath = Path('/candidate/project/rc_filter.kicad_sch')
pcbpath = Path('/candidate/project/rc_filter.kicad_pcb')
sch, pcb = parse(schpath), parse(pcbpath)
r1 = next(s for s in children(sch, 'symbol') if props(s).get('Reference') == 'R1')
assert props(r1)['Value'] == '10k'
assert child(r1, 'dnp')[1] == 'no'
assert child(r1, 'at')[1:] == ['86.36', '63.5', '0']
lib = next(s for s in children(child(sch, 'lib_symbols'), 'symbol') if s[1] == 'Device:R')
pins = children(next(s for s in children(lib, 'symbol') if s[1] == 'R_1_1'), 'pin')
labels = {tuple(map(float, child(s, 'at')[1:3])): s[1] for s in children(sch, 'label')}
wires = [[tuple(map(float, p[1:])) for p in child(w, 'pts')[1:]] for w in children(sch, 'wire')]
pin_nets = {}
for pin in pins:
    x, y = map(float, child(pin, 'at')[1:3])
    # KiCad schematic Y axis is opposite the library symbol Y axis; angle is 0.
    pos = (round(86.36 + x, 2), round(63.5 - y, 2))
    wire = next(w for w in wires if pos in w)
    other = next(p for p in wire if p != pos)
    pin_nets[child(pin, 'number')[1]] = '/' + labels[other]
assert pin_nets == {'1': '/IN', '2': '/OUT'}
pcb_r1 = next(f for f in children(pcb, 'footprint') if props(f).get('Reference') == 'R1')
pcb_nets = {p[1]: child(p, 'net')[1] for p in children(pcb_r1, 'pad')}
assert props(pcb_r1)['Value'] == '10k' and pcb_nets == pin_nets
xml = ET.parse('/evidence/work/netlist/design.xml').getroot()
assert xml.find("./components/comp[@ref='R1']/value").text == '10k'
xml_nets = {n.attrib['pin']: net.attrib['name'] for net in xml.findall('./nets/net') for n in net.findall('node') if n.attrib['ref'] == 'R1'}
assert xml_nets == pin_nets
records = {}
for name in ('erc', 'drc', 'netlist', 'geometry'):
    execution = json.loads(Path(f'/evidence/work/{name}/execution.json').read_text())
    record = json.loads(Path(f'/evidence/checks/{name}.json').read_text())
    records[name] = {'argv': execution['argv'], 'exit_code': execution['exit_code'],
                     'outcome': record['outcome'], 'limitations': record['limitations']}
erc = json.loads(Path('/evidence/work/erc/erc.json').read_text())
drc = json.loads(Path('/evidence/work/drc/drc.json').read_text())
records['erc']['violations'] = sum(len(s['violations']) for s in erc['sheets'])
records['erc']['ignored_checks'] = erc['ignored_checks']
for key in ('violations', 'unconnected_items', 'schematic_parity', 'ignored_checks'):
    records['drc'][key] = drc[key]
geometry = json.loads(Path('/evidence/work/geometry/geometry.json').read_text())
assert next(f for f in geometry['footprints'] if f['ref'] == 'R1')['value'] == '10k'
records['geometry']['console'] = Path('/evidence/work/geometry/console.log').read_text()
v, resistance, limit = 12.0 - 0.0, 10000.0, 0.1
result = {
    'command': 'python3 /work/experiments/r1_dc_review.py',
    'versions': {'python': platform.python_version(), 'kicad-cli': subprocess.check_output(['kicad-cli', 'version'], text=True).strip()},
    'native_sha256': {f'inputs/project/{p.name}': hashlib.sha256(p.read_bytes()).hexdigest() for p in (schpath, pcbpath)},
    'R1': {'schematic_value': props(r1)['Value'], 'schematic_pin_nets': pin_nets, 'pcb_pad_nets': pcb_nets, 'exported_pin_nets': xml_nets},
    'calculation': {'voltage_V': v, 'resistance_ohm': resistance, 'current_A': v/resistance,
                    'power_W': v*v/resistance, 'stated_continuous_limit_W': limit,
                    'limit_fraction': (v*v/resistance)/limit, 'margin_W': limit-v*v/resistance,
                    'formula': 'I=(VIN-VOUT)/R; P=(VIN-VOUT)^2/R',
                    'passes_stated_power_criterion': v*v/resistance <= limit},
    'records_inspected': records,
    'assumptions': ['Only the stated indefinitely held /IN=12 V, /OUT=0 V condition.',
                    'Use native nominal 10k resistance and stipulated 0.1 W continuous limit.',
                    'External equipment enforces both voltages; no board clamp is required.',
                    'No SPICE model is needed for this fixed-voltage resistor calculation.'],
    'limitations': ['No physical measurements or temperature prediction.',
                    'No invented tolerance, ambient, part voltage rating or derating model.',
                    'No startup, shutdown, brownout, reset, transient or other operating-condition assessment.',
                    'No ERC/DRC rerun or saved-zone refill; inspected supplied native reports, execution records and source.',
                    'Ignored ERC/DRC checks are listed above; zero reported violations is not an electrical power test.',
                    'Geometry console contains three PROPERTY_ENUM assertions; R1 data agree with native files. Geometry output is not electrical validation.']
}
(OUT/'r1_dc_results.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result['calculation'], indent=2))
