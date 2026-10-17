"""Read-only native geometry and connectivity audit; standard library only."""
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET
from decimal import Decimal

ROOT = Path('/candidate')
OUT = Path('/work/experiments')

def parse(path):
    text = path.read_text()
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|[()]|[^\s()]+', text)
    stack, roots = [], []
    for token in tokens:
        if token == '(':
            node = []
            (stack[-1] if stack else roots).append(node)
            stack.append(node)
        elif token == ')':
            stack.pop()
        else:
            stack[-1].append(json.loads(token) if token.startswith('"') else token)
    assert not stack and len(roots) == 1
    return roots[0]

def children(node, key):
    return [x for x in node if isinstance(x, list) and x and x[0] == key]

def one(node, key):
    matches = children(node, key)
    assert len(matches) == 1, (key, len(matches))
    return matches[0][1:]

def locator(path, uuid):
    return next(i for i, s in enumerate(path.read_text().splitlines(), 1) if f'"{uuid}"' in s)

board_path = ROOT / 'project/rc_filter.kicad_pcb'
sch_path = ROOT / 'project/rc_filter.kicad_sch'
board = parse(board_path)
sch = parse(sch_path)
geometry = json.loads(Path('/evidence/work/geometry/geometry.json').read_text())
segments = children(board, 'segment') + children(board, 'arc')
measurements = []
for segment in segments:
    uuid = one(segment, 'uuid')[0]
    width = Decimal(one(segment, 'width')[0])
    exported = next(x for x in geometry['tracks'] if x['uuid'] == uuid)
    measurements.append({
        'uuid': uuid, 'native_uuid_line': locator(board_path, uuid),
        'layer': one(segment, 'layer')[0], 'net': one(segment, 'net')[0],
        'start_mm': one(segment, 'start'), 'end_mm': one(segment, 'end'),
        'width_mm': str(width), 'margin_mm': str(width - Decimal('0.20')),
        'requirement_pass': width >= Decimal('0.20'),
        'matches_geometry_record': width == Decimal(str(exported['width_mm']))
    })
assert len(segments) == len([x for x in geometry['tracks'] if x['type'] != 'via'])

board_nets = {}
for footprint in children(board, 'footprint'):
    properties = {x[1]: x[2] for x in children(footprint, 'property')}
    for pad in children(footprint, 'pad'):
        board_nets.setdefault(one(pad, 'net')[0], []).append(properties['Reference'] + '.' + pad[1])
board_nets = {k: sorted(v) for k, v in board_nets.items()}
xml = ET.parse('/evidence/work/netlist/design.xml').getroot()
xml_nets = {n.attrib['name']: sorted(p.attrib['ref'] + '.' + p.attrib['pin'] for p in n.findall('node')) for n in xml.findall('./nets/net')}
connectivity = json.loads(Path('/evidence/work/netlist/connectivity.json').read_text())
symbols = []
for s in children(sch, 'symbol'):
    properties = {x[1]: x[2] for x in children(s, 'property')}
    if properties['Reference'] in ('R1', 'C1', 'J1'):
        symbols.append({'ref': properties['Reference'], 'value': properties['Value'], 'lib_id': one(s, 'lib_id')[0], 'at': one(s, 'at')})

checks = {}
for name in ('erc', 'drc', 'netlist', 'geometry'):
    execution = json.loads(Path(f'/evidence/work/{name}/execution.json').read_text())
    checks[name] = {'argv': execution['argv'], 'exit_code': execution['exit_code'], 'console_hash_matches': hashlib.sha256(Path(f'/evidence/work/{name}/console.log').read_bytes()).hexdigest() == execution['console_sha256']}
for name in ('erc', 'drc'):
    report = json.loads(Path(f'/evidence/work/{name}/{name}.json').read_text())
    checks[name]['ignored_checks'] = [x['key'] for x in report['ignored_checks']]
    checks[name]['violations'] = sum(len(s['violations']) for s in report['sheets']) if name == 'erc' else len(report['violations'])
    if name == 'drc':
        checks[name]['unconnected_items'] = len(report['unconnected_items'])
        checks[name]['schematic_parity'] = len(report['schematic_parity'])

result = {
    'python_version': sys.version, 'method': 'Independent balanced S-expression parser of saved native source; Decimal width comparisons, no candidate writes.',
    'sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in (board_path, sch_path)},
    'track_measurements': measurements, 'all_saved_tracks_meet_0_20_mm': all(x['requirement_pass'] for x in measurements),
    'arcs': len(children(board, 'arc')), 'vias_separate_from_segments': len(children(board, 'via')),
    'board_pad_nets': board_nets, 'matches_xml_netlist': board_nets == xml_nets,
    'matches_connectivity_record': board_nets == {k: sorted(v) for k, v in connectivity['nets'].items()},
    'native_symbols': symbols, 'checks': checks,
    'nominal_ideal_RC_calculation': {'R_ohm': 10000, 'C_farad': 100e-9, 'tau_seconds': 10000 * 100e-9, 'pole_hz': 1 / (2 * math.pi * 10000 * 100e-9), 'assumptions': 'Analytical topology sanity check only: ideal R/C, zero source impedance, unloaded output. These are not stated operating conditions or validation requirements.'},
    'limitations': ['No hardware or SPICE measurements; supplied checks contain no electrical behavioral models or stimulus.', 'No voltage, current, load, temperature or tolerance operating conditions supplied; no ratings, ampacity, transient or qualification conclusion.', 'Saved zone fill only, no refill. Ignored ERC/DRC categories remain outside reported check coverage.']
}
(OUT / 'native_audit.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
