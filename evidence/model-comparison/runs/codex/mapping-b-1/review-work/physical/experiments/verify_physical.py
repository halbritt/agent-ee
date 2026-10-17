#!/usr/bin/python3
"""Read-only saved-board review; no candidate saves, changes, or zone refill."""
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path('/work/experiments')
SRC = Path('/candidate/project')
commands = []
env = dict(os.environ, KICAD_CONFIG_HOME=str(ROOT / 'kicad-config'),
           KICAD10_SYMBOL_DIR='/candidate/stock/symbols',
           KICAD10_FOOTPRINT_DIR='/candidate/stock/footprints')

def run(argv, log):
    p = subprocess.run(argv, cwd=ROOT, env=env, text=True, capture_output=True)
    (ROOT / log).write_text(p.stdout + p.stderr)
    commands.append({'argv': argv, 'cwd': str(ROOT), 'exit_code': p.returncode,
                     'log': log})
    return p

run(['kicad-cli', 'version'], 'kicad-version.txt')
run(['kicad-cli', 'pcb', 'drc', '--format', 'json', '--severity-all',
     '--schematic-parity', '--units', 'mm', '-o', str(ROOT / 'drc.json'),
     str(SRC / 'rc_filter.kicad_pcb')], 'drc-console.txt')
run(['kicad-cli', 'sch', 'erc', '--format', 'json', '--severity-all',
     '--units', 'mm', '-o', str(ROOT / 'erc.json'),
     str(SRC / 'rc_filter.kicad_sch')], 'erc-console.txt')
run(['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml',
     '-o', str(ROOT / 'design.xml'), str(SRC / 'rc_filter.kicad_sch')],
    'netlist-console.txt')

import pcbnew
b = pcbnew.LoadBoard(str(SRC / 'rc_filter.kicad_pcb'))
c = b.GetConnectivity()
built = c.Build(b)  # Rebuild in-memory connectivity only; do not refill zones.
pads = {}
uuid_to_pad = {}
for f in b.GetFootprints():
    for p in f.Pads():
        name = f'{f.GetReference()}.{p.GetNumber()}'
        pads[name] = p
        uuid_to_pad[p.m_Uuid.AsString()] = name

def xy(v):
    return [v.x / 1e6, v.y / 1e6]

groups = {}
for name, p in sorted(pads.items()):
    items = list(c.GetConnectedItems(p))
    groups[name] = {
        'net': p.GetNetname(), 'position_mm': xy(p.GetPosition()),
        'copper_connected_pads': sorted(uuid_to_pad[x.m_Uuid.AsString()]
             for x in items if x.m_Uuid.AsString() in uuid_to_pad),
        'copper_connected_items': sorted(
            [{'class': x.GetClass(), 'uuid': x.m_Uuid.AsString()} for x in items],
            key=lambda x: x['uuid'])}

# Independent F.Cu centerline graph, ignoring every assigned net name.
# All seven pad centers and every track endpoint are nodes; a node lying
# anywhere on a track joins that track, including the OUT T-junction.
tracks = [t for t in b.GetTracks() if t.GetClass() == 'PCB_TRACK'
          and t.GetLayer() == pcbnew.F_Cu]
def coord(v):
    return (v.x, v.y)
points = set(coord(p.GetPosition()) for p in pads.values())
for t in tracks:
    points.update([coord(t.GetStart()), coord(t.GetEnd())])
adj = {p: set() for p in points}
for t in tracks:
    a, z = coord(t.GetStart()), coord(t.GetEnd())
    on = [p for p in points
          if (p[0]-a[0])*(z[1]-a[1]) == (p[1]-a[1])*(z[0]-a[0])
          and min(a[0],z[0]) <= p[0] <= max(a[0],z[0])
          and min(a[1],z[1]) <= p[1] <= max(a[1],z[1])]
    for p in on:
        adj[p].update(on)
independent_groups = {}
for name, p in sorted(pads.items()):
    reached, pending = set(), [coord(p.GetPosition())]
    while pending:
        node = pending.pop()
        if node not in reached:
            reached.add(node)
            pending.extend(adj[node] - reached)
    independent_groups[name] = sorted(n for n, q in pads.items()
                                      if coord(q.GetPosition()) in reached)

netlist = ET.parse(ROOT / 'design.xml')
sch_nets = {n.get('name'): sorted(f"{p.get('ref')}.{p.get('pin')}"
            for p in n.findall('node')) for n in netlist.findall('./nets/net')}
report = {
    'python_version': sys.version, 'pcbnew_version': pcbnew.Version(),
    'candidate_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in [SRC/'rc_filter.kicad_pcb', SRC/'rc_filter.kicad_sch']},
    'connectivity_build_return': built,
    'saved_copper_connectivity': groups,
    'independent_front_centerline_groups_ignoring_net_names': independent_groups,
    'schematic_exported_nets': sch_nets,
    'track_lengths_mm': {t.m_Uuid.AsString(): t.GetLength()/1e6 for t in tracks},
    'manual_path_lengths_mm': {
        'J1.1_to_R1.1': 4.0875,
        'J1.2_to_C1.1': 7.5 + 2.54 + (109.05-107.5),
        'C1.2_to_ground_via': 2.05},
    'contract_check': {
        'J1.2_input_on_series_only_side_of_R1': False,
        'J1.1_output_on_C1.1_shunt_node': False,
        'J1.3_ground_connected_to_C1.2_through_saved_zone': True},
    'illustrative_ideal_calculation': {
        'assumptions': ['R1=10 kohm, C1=100 nF from native values',
                        'Zero-impedance AC source at contractual input J1.2',
                        'Open-circuit measurement at contractual output J1.1',
                        'Ignore parasitics; illustrative only, not a specified load'],
        'equation': 'I_R1=0, so V(J1.1)=V(J1.2); C1 shunts the driven input',
        'external_voltage_transfer': '1 (0 dB), independent of frequency in this ideal model',
        'RC_product_s': 10000*100e-9,
        'RC_corner_if_driven_from_the_series_only_side_Hz': 1/(2*math.pi*10000*100e-9)},
    'limitations': ['Centerline graph checks this exact front routing only; it is not a general clearance or finite-pad intersection engine.',
                    'Ground plane connectivity uses KiCad saved-fill connectivity, corroborated by native polygon inspection; no refill.',
                    'No fabrication, assembly, electrical, or thermal hardware measurements.']}
(ROOT / 'physical-connectivity.json').write_text(json.dumps(report, indent=2)+'\n')
(ROOT / 'commands.json').write_text(json.dumps({
    'entry_command': '/usr/bin/python3 /work/experiments/verify_physical.py > /work/experiments/verify-console.txt 2>&1',
    'environment_overrides': {k: env[k] for k in ['KICAD_CONFIG_HOME', 'KICAD10_SYMBOL_DIR', 'KICAD10_FOOTPRINT_DIR']},
    'commands': commands}, indent=2)+'\n')
print(json.dumps({'connectivity_build': built, 'front_groups': independent_groups,
                  'schematic_nets': sch_nets, 'contract_check': report['contract_check']}, indent=2))
