"""Read-only circuit audit; run with python3 /work/experiments/audit.py."""
import hashlib
import json
import math
import pathlib
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = pathlib.Path('/work/experiments')
records = []

def run(argv):
    p = subprocess.run(argv, text=True, capture_output=True)
    records.append(dict(argv=argv, exit_code=p.returncode, stdout=p.stdout, stderr=p.stderr))
    return p

def sexpr(path):
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|[()]|[^\s()]+', path.read_text())
    stack = []
    for t in tokens:
        if t == '(':
            stack.append([])
        elif t == ')':
            node = stack.pop()
            if stack:
                stack[-1].append(node)
            else:
                return node
        else:
            stack[-1].append(json.loads(t) if t.startswith('"') else t)

def children(node, name):
    return [n for n in node if isinstance(n, list) and n and n[0] == name]

def child(node, name):
    return children(node, name)[0]

run(['kicad-cli', 'version'])
run(['python3', '--version'])
run(['ngspice', '--version'])
files = [pathlib.Path('/candidate/requirements.txt'),
         pathlib.Path('/candidate/project/rc_filter.kicad_sch'),
         pathlib.Path('/candidate/project/rc_filter.kicad_pcb')]
hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
export = run(['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml',
              '-o', str(ROOT / 'fresh.xml'), str(files[1])])
if export.returncode:
    (ROOT / 'commands.json').write_text(json.dumps(records, indent=2))
    sys.exit(export.returncode)
xml = ET.parse(ROOT / 'fresh.xml')
nets = {n.attrib['name']: sorted(f"{p.attrib['ref']}.{p.attrib['pin']}" for p in n.findall('node'))
        for n in xml.findall('./nets/net')}
supplied = json.loads(pathlib.Path('/evidence/work/netlist/connectivity.json').read_text())['nets']
board = sexpr(files[2])
board_nets = {}
pads = []
for fp in children(board, 'footprint'):
    ref = next(p[2] for p in children(fp, 'property') if p[1] == 'Reference')
    for p in children(fp, 'pad'):
        net = child(p, 'net')[1]
        board_nets.setdefault(net, []).append(f'{ref}.{p[1]}')
        pads.append(dict(ref=ref, pin=p[1], net=net, local_at=child(p, 'at')[1:],
                         footprint_at=child(fp, 'at')[1:], uuid=child(p, 'uuid')[1]))
board_nets = {k: sorted(v) for k, v in board_nets.items()}
sch = sexpr(files[1])
j1 = next(n for n in children(sch, 'symbol')
          if any(p[1:3] == ['Reference', 'J1'] for p in children(n, 'property')))
lib = next(n for n in children(child(sch, 'lib_symbols'), 'symbol')
           if n[1] == child(j1, 'lib_id')[1])
pin_units = children(lib, 'symbol')
pin_nodes = [p for u in pin_units for p in children(u, 'pin')]
# For this saved 180-degree connector, library x maps to -x and library y to +y
# in sheet coordinates (KiCad sheet coordinates have downward positive y).
assert child(j1, 'at')[3] == '180'
sx, sy = map(float, child(j1, 'at')[1:3])
pin_positions = {}
for p in pin_nodes:
    x, y = map(float, child(p, 'at')[1:3])
    pin_positions[child(p, 'number')[1]] = [sx-x, sy+y]

native_checks = {}
for name in ['erc', 'drc']:
    report = json.loads(pathlib.Path(f'/evidence/work/{name}/{name}.json').read_text())
    native_checks[name] = dict(version=report['kicad_version'],
        ignored_checks=report['ignored_checks'],
        violations=report.get('violations', [v for s in report.get('sheets', []) for v in s['violations']]),
        schematic_parity=report.get('schematic_parity'),
        unconnected_items=report.get('unconnected_items'))

out = dict(hashes=hashes, fresh_nets=nets, board_nets=board_nets,
    fresh_matches_supplied=nets == {k: sorted(v) for k,v in supplied.items()},
    board_matches_fresh=board_nets == nets,
    j1_native_sheet_pin_positions_mm=pin_positions, pads=pads,
    tracks=[dict(start=child(t,'start')[1:], end=child(t,'end')[1:],
                 net=child(t,'net')[1], layer=child(t,'layer')[1], uuid=child(t,'uuid')[1])
            for t in children(board, 'segment')],
    native_checks=native_checks,
    symbolic_analysis={
      'contract': 'Drive J1.2, observe J1.1 relative to J1.3.',
      'saved_topology': 'R1=10k between J1.1 and J1.2; C1=100n between J1.2 and J1.3.',
      'general_port_relation': '(V1-V2)/R1 + Yload(s)*V1 = 0, hence V1/V2 = 1/(1+R1*Yload(s)).',
      'consequence': 'C1 shunts the contracted input, not the output; it does not supply the output-side RC pole in V1/V2.',
      'limitations': 'Ideal nominal passive topology only. No source/load impedances, waveform, amplitude, tolerance, parasitics or component ratings assumed. No numerical operating-point or transient claim.'})
(ROOT / 'audit.json').write_text(json.dumps(out, indent=2) + '\n')
(ROOT / 'commands.json').write_text(json.dumps(records, indent=2) + '\n')
print(json.dumps({k:out[k] for k in ['fresh_nets','board_nets','fresh_matches_supplied','board_matches_fresh','j1_native_sheet_pin_positions_mm','symbolic_analysis']}, indent=2))
