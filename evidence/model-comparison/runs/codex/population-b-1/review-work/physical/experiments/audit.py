"""Read-only native population and topology audit; no candidate writes or refill."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path('/work/experiments')
CAND = Path('/candidate/project')
env = dict(os.environ)
env.update(KICAD_CONFIG_HOME=str(ROOT / 'kicad-config'),
           KICAD10_SYMBOL_DIR='/candidate/stock/symbols',
           KICAD10_FOOTPRINT_DIR='/candidate/stock/footprints',
           KICAD10_TEMPLATE_DIR='/candidate/stock/template')

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def run(argv, stem):
    result = subprocess.run(argv, capture_output=True, text=True, env=env)
    (ROOT / (stem + '.stdout.txt')).write_text(result.stdout)
    (ROOT / (stem + '.stderr.txt')).write_text(result.stderr)
    return dict(argv=argv, exit_code=result.returncode,
                stdout=stem + '.stdout.txt', stderr=stem + '.stderr.txt')

def parse(path):
    # KiCad's quoted atoms and parentheses, without evaluating native content.
    toks = iter(re.findall(r'"(?:\\.|[^"\\])*"|[()]|[^\s()]+', path.read_text()))
    def node(first):
        if first != '(':
            return json.loads(first) if first.startswith('"') else first
        children = []
        for tok in toks:
            if tok == ')':
                return children
            children.append(node(tok))
        raise ValueError('Unclosed native expression')
    return node(next(toks))

def children(n, key):
    return [x for x in n if isinstance(x, list) and x and x[0] == key]

def field(n, key):
    return children(n, key)[0][1:]

def prop(n, key):
    return next(x[2] for x in children(n, 'property') if x[1] == key)

paths = [CAND / name for name in ('rc_filter.kicad_sch', 'rc_filter.kicad_pcb', 'rc_filter.kicad_pro')]
before = {str(p): digest(p) for p in paths}
commands = [run(['kicad-cli', 'version'], 'kicad-version'),
            run(['/usr/bin/python3', '--version'], 'python-version')]
commands.append(run(['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml',
                     '-o', str(ROOT / 'fresh-netlist.xml'), str(paths[0])], 'netlist'))
commands.append(run(['kicad-cli', 'pcb', 'drc', '--format', 'json', '--severity-all',
                     '--schematic-parity', '--units', 'mm', '-o', str(ROOT / 'fresh-drc.json'),
                     str(paths[1])], 'drc'))
commands.append(run(['kicad-cli', 'sch', 'erc', '--format', 'json', '--severity-all',
                     '--units', 'mm', '-o', str(ROOT / 'fresh-erc.json'), str(paths[0])], 'erc'))

sch, pcb = parse(paths[0]), parse(paths[1])
symbols = {prop(x, 'Reference'): {'dnp': field(x, 'dnp')[0], 'value': prop(x, 'Value')}
           for x in children(sch, 'symbol') if not prop(x, 'Reference').startswith('#')}
footprints = {}
for fp in children(pcb, 'footprint'):
    x, y = map(float, field(fp, 'at')[:2])
    assert len(field(fp, 'at')) == 2, 'Audit assumes saved unrotated footprints'
    pads = {}
    for pad in children(fp, 'pad'):
        px, py = map(float, field(pad, 'at')[:2])
        pads[pad[1]] = {'position_mm': [x + px, y + py],
                        'size_mm': list(map(float, field(pad, 'size'))),
                        'net': field(pad, 'net')[0]}
    footprints[prop(fp, 'Reference')] = {'attr': field(fp, 'attr'), 'pads': pads}

tracks = [{'start_mm': list(map(float, field(t, 'start'))),
           'end_mm': list(map(float, field(t, 'end'))),
           'width_mm': float(field(t, 'width')[0]), 'net': field(t, 'net')[0],
           'layer': field(t, 'layer')[0]} for t in children(pcb, 'segment')]
zone_info = [{'net': field(z, 'net')[0], 'layer': field(z, 'layer')[0],
              'saved_fill_polygons': len(children(z, 'filled_polygon'))}
             for z in children(pcb, 'zone')]
assert set(symbols) == set(footprints) == {'R1', 'C1', 'J1'}
for ref in symbols:
    assert (symbols[ref]['dnp'] == 'yes') == ('dnp' in footprints[ref]['attr'])

# Compare fresh XML nets against native board pad assignments, including DNP pads.
xml = ET.parse(ROOT / 'fresh-netlist.xml').getroot()
xml_nets = {net.attrib['name']: sorted((n.attrib['ref'], n.attrib['pin']) for n in net.findall('node'))
            for net in xml.findall('./nets/net')}
native_nets = {name: sorted((ref, num) for ref, fp in footprints.items()
                            for num, pad in fp['pads'].items() if pad['net'] == name)
               for name in xml_nets}
assert xml_nets == native_nets

# A net node represents its saved routed copper. Add only fitted resistors as
# inter-net DC edges. Connector contacts remain separate; a capacitor adds no
# resistive DC edge. No external wiring, accidental solder bridges, or leakage
# is credited as the required fitted resistor path.
edges = []
excluded = []
for ref, fp in footprints.items():
    if ref.startswith('R'):
        pair = [fp['pads'][num]['net'] for num in ('1', '2')]
        if 'dnp' in fp['attr']:
            excluded.append({'ref': ref, 'nets': pair, 'reason': 'DNP in schematic and PCB'})
        else:
            edges.append({'ref': ref, 'nets': pair})
reached = {'/IN'}
while True:
    old = set(reached)
    for edge in edges:
        if reached.intersection(edge['nets']):
            reached.update(edge['nets'])
    if old == reached:
        break
r1 = footprints['R1']['pads']
gap = r1['2']['position_mm'][0] - r1['1']['position_mm'][0] - (r1['1']['size_mm'][0] + r1['2']['size_mm'][0])/2
out = {'schematic_symbols': symbols, 'native_footprints': footprints,
       'native_segments': tracks, 'native_zones': zone_info,
       'fresh_xml_matches_native_board_pad_nets': xml_nets == native_nets,
       'R1_pad_horizontal_copper_gap_mm': round(gap, 9),
       'gap_calculation': '(105.9125 - 104.0875) - (1.025 + 1.025)/2 = 0.8 mm; aligned roundrect pads',
       'fitted_resistor_edges': edges, 'excluded_resistors': excluded,
       'dc_reachable_from_IN': sorted(reached),
       'required_fitted_resistive_IN_OUT_path': '/OUT' in reached}
(ROOT / 'native-topology.json').write_text(json.dumps(out, indent=2) + '\n')
after = {str(p): digest(p) for p in paths}
(ROOT / 'commands.json').write_text(json.dumps({'invocation': '/usr/bin/python3 /work/experiments/audit.py',
    'python_version': sys.version, 'environment_overrides': {k: env[k] for k in env if k.startswith('KICAD')},
    'commands': commands, 'candidate_hashes_before': before, 'candidate_hashes_after': after,
    'candidate_unchanged': before == after}, indent=2) + '\n')
print(json.dumps({'gap_mm': round(gap, 9), 'fitted_resistor_edges': edges,
                  'required_path': '/OUT' in reached, 'candidate_unchanged': before == after,
                  'command_exit_codes': [x['exit_code'] for x in commands]}, indent=2))
