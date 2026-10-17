"""Read-only native population audit; run with python3 check_population.py."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

OUT = Path('/work/experiments')
commands = []

def run(argv):
    env = dict(os.environ)
    env.update(KICAD_CONFIG_HOME=str(OUT / 'kicad-config'),
               KICAD10_SYMBOL_DIR='/candidate/stock/symbols',
               KICAD10_FOOTPRINT_DIR='/candidate/stock/footprints')
    p = subprocess.run(argv, text=True, capture_output=True, env=env)
    commands.append(dict(argv=argv, exit_code=p.returncode, stdout=p.stdout, stderr=p.stderr))
    return p

def parse(path):
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|[()]|[^\s()]+', Path(path).read_text())
    stack = []
    for token in tokens:
        if token == '(':
            node = []
            if stack:
                stack[-1].append(node)
            stack.append(node)
        elif token == ')':
            root = stack.pop()
        else:
            stack[-1].append(json.loads(token) if token.startswith('"') else token)
    return root

def children(node, key):
    return [c for c in node if isinstance(c, list) and c and c[0] == key]

def child(node, key):
    return children(node, key)[0]

versions = {key: run(argv).stdout.strip() for key, argv in {
    'python': ['python3', '--version'], 'kicad': ['kicad-cli', 'version']}.items()}
export = run(['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml',
              '-o', str(OUT / 'fresh.xml'), '/candidate/project/rc_filter.kicad_sch'])
assert export.returncode == 0
sch = parse('/candidate/project/rc_filter.kicad_sch')
pcb = parse('/candidate/project/rc_filter.kicad_pcb')
native_symbols = {}
for symbol in children(sch, 'symbol'):
    props = {p[1]: p[2] for p in children(symbol, 'property')}
    if not props['Reference'].startswith('#'):
        native_symbols[props['Reference']] = dict(value=props['Value'],
            lib_id=child(symbol, 'lib_id')[1], dnp=child(symbol, 'dnp')[1] == 'yes')
native_footprints = {}
for fp in children(pcb, 'footprint'):
    props = {p[1]: p[2] for p in children(fp, 'property')}
    native_footprints[props['Reference']] = dict(dnp='dnp' in child(fp, 'attr'),
        pads={pad[1]: dict(net=child(pad, 'net')[1], at=child(pad, 'at')[1:],
                          size=child(pad, 'size')[1:]) for pad in children(fp, 'pad')})

def normalized_netlist(path):
    xml = ET.parse(path).getroot()
    comps = {c.attrib['ref']: dict(value=c.findtext('value'),
             part=c.find('libsource').attrib['part'],
             dnp=any(p.attrib['name'] == 'dnp' for p in c.findall('property')))
             for c in xml.findall('./components/comp')}
    nets = {n.attrib['name']: sorted(p.attrib['ref'] + '.' + p.attrib['pin']
            for p in n.findall('node')) for n in xml.findall('./nets/net')}
    return comps, nets

components, nets = normalized_netlist(OUT / 'fresh.xml')
assert (components, nets) == normalized_netlist('/evidence/work/netlist/design.xml')
pin_net = {pin: net for net, pins in nets.items() for pin in pins}
resistors = []
edges = []
for ref, comp in components.items():
    assert comp['dnp'] == native_symbols[ref]['dnp'] == native_footprints[ref]['dnp']
    if comp['part'] == 'R':
        ends = [pin_net[ref + '.' + pin] for pin in ['1', '2']]
        resistors.append(dict(ref=ref, value=comp['value'], dnp=comp['dnp'], nets=ends))
        if not comp['dnp']:
            edges.append(ends)
reachable = {'/IN'}
while True:
    more = reachable | {b for a, b in edges if a in reachable} | {a for a, b in edges if b in reachable}
    if more == reachable:
        break
    reachable = more
pads = native_footprints['R1']['pads']
gap = abs(float(pads['2']['at'][0]) - float(pads['1']['at'][0])) - sum(float(p['size'][0])/2 for p in pads.values())
reports = {}
for kind, file in [('erc', 'erc.json'), ('drc', 'drc.json'), ('geometry', 'geometry.json')]:
    reports[kind] = json.loads((Path('/evidence/work') / kind / file).read_text())
hashes = {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in [
    '/candidate/requirements.txt', '/candidate/project/rc_filter.kicad_sch',
    '/candidate/project/rc_filter.kicad_pcb']}
result = dict(versions=versions, native_sha256=hashes, symbols=native_symbols,
    footprints=native_footprints, fresh_export_matches_supplied_population_and_nets=True,
    nets=nets, resistors=resistors, fitted_resistor_edges=edges,
    reachable_dc_resistive_nets_from_IN=sorted(reachable),
    fitted_resistive_path_IN_to_OUT='/OUT' in reachable,
    R1_centerline_pad_gap_mm=round(gap, 6),
    supplied_erc_violations=sum(len(s['violations']) for s in reports['erc']['sheets']),
    supplied_drc_violations=len(reports['drc']['violations']),
    supplied_drc_unconnected=len(reports['drc']['unconnected_items']),
    supplied_drc_parity_issues=len(reports['drc']['schematic_parity']),
    supplied_ignored_checks={k: reports[k]['ignored_checks'] for k in ['erc', 'drc']})
(OUT / 'population_result.json').write_text(json.dumps(result, indent=2) + '\n')
(OUT / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')
print(json.dumps(result, indent=2))
