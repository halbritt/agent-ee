#!/usr/bin/python3
"""Read-only native checks; outputs stay in this experiments directory."""
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
env = dict(os.environ)
env.update(KICAD_CONFIG_HOME=str(ROOT / 'kicad-config'),
           KICAD10_SYMBOL_DIR='/candidate/stock/symbols',
           KICAD10_FOOTPRINT_DIR='/candidate/stock/footprints',
           KICAD10_TEMPLATE_DIR='/candidate/stock/template')
commands = []
def run(name, argv):
    p = subprocess.run(argv, env=env, capture_output=True, text=True)
    (ROOT / (name + '.stdout.txt')).write_text(p.stdout)
    (ROOT / (name + '.stderr.txt')).write_text(p.stderr)
    commands.append(dict(name=name, argv=argv, exit_code=p.returncode,
                         stdout=name+'.stdout.txt', stderr=name+'.stderr.txt'))
    (ROOT / 'commands.json').write_text(json.dumps(commands, indent=2)+'\n')
    return p

run('python-version', ['/usr/bin/python3', '--version'])
run('kicad-version', ['kicad-cli', '--version'])
run('netlist', ['kicad-cli','sch','export','netlist','--format','kicadxml',
    '-o',str(ROOT/'design.xml'),'/candidate/project/rc_filter.kicad_sch'])
run('erc', ['kicad-cli','sch','erc','--format','json','--severity-all',
    '--units','mm','-o',str(ROOT/'erc.json'),'/candidate/project/rc_filter.kicad_sch'])
run('drc', ['kicad-cli','pcb','drc','--format','json','--severity-all',
    '--schematic-parity','--units','mm','-o',str(ROOT/'drc.json'),
    '/candidate/project/rc_filter.kicad_pcb'])

def nets(path):
    return {n.attrib['name']: sorted(x.attrib['ref']+'.'+x.attrib['pin']
            for x in n.findall('node')) for n in ET.parse(path).findall('./nets/net')}
expected = {'/IN':['J1.1','R1.1'], '/OUT':['C1.1','J1.2','R1.2'],
            'GND':['C1.2','J1.3']}
result = dict(python=sys.version, schematic_nets=nets(ROOT/'design.xml'),
              supplied_nets=nets('/evidence/work/netlist/design.xml'),
              expected_nets=expected)

import pcbnew
result['pcbnew_version'] = pcbnew.Version()
b = pcbnew.LoadBoard('/candidate/project/rc_filter.kicad_pcb')
conn = b.GetConnectivity()
pads = {fp.GetReference()+'.'+p.GetNumber():p
        for fp in b.GetFootprints() for p in fp.Pads()}
items = list(pads.values()) + list(b.GetTracks()) + list(b.Zones())
labels = {p.m_Uuid.AsString():name for name,p in pads.items()}
for i in items:
    labels.setdefault(i.m_Uuid.AsString(), i.GetClass()+':'+i.m_Uuid.AsString())
def label(i):
    return labels.get(i.m_Uuid.AsString(), i.GetClass()+':'+i.m_Uuid.AsString())
adj = {label(i):sorted(label(j) for j in conn.GetConnectedItems(i)) for i in items}
result['saved_copper_adjacency'] = adj
result['board_nets'] = {n:sorted(name for name,p in pads.items() if p.GetNetname()==n)
                        for n in expected}
result['pads'] = {name:dict(net=p.GetNetname(),
    position_mm=[pcbnew.ToMM(p.GetPosition().x),pcbnew.ToMM(p.GetPosition().y)],
    uuid=p.m_Uuid.AsString()) for name,p in pads.items()}
def reachable(start):
    seen=set()
    queue=[start]
    while queue:
        x=queue.pop()
        if x in seen: continue
        seen.add(x)
        queue.extend(y for y in adj.get(x,[]) if y not in seen)
    return sorted(x for x in seen if x in pads)
result['reachable_pads_from_each_connector_pad']={name:reachable(name)
    for name in ['J1.1','J1.2','J1.3']}
result['saved_zone'] = [dict(uuid=z.m_Uuid.AsString(), net=z.GetNetname(),
    filled=z.IsFilled(), area_mm2=z.GetFilledArea()/1e12) for z in b.Zones()]
result['analytic_topology_only'] = dict(
    assumptions='Ideal R=10k and C=100n, ideal zero-impedance drive, unloaded output; no operating stimulus or acceptance limit assumed.',
    transfer='V(J1.2)/V(J1.1)=1/(1+s*R*C), referenced to J1.3',
    tau_seconds=1e4*100e-9, pole_hz=1/(2*math.pi*1e4*100e-9))
result['comparisons'] = dict(native_export_matches_contract=result['schematic_nets']==expected,
    supplied_export_matches_fresh=result['schematic_nets']==result['supplied_nets'],
    board_net_assignments_match_contract=result['board_nets']==expected,
    physical_reachability_matches_contract=all(reachable('J1.'+str(i))==expected[n]
        for i,n in [(1,'/IN'),(2,'/OUT'),(3,'GND')]))
result['source_sha256'] = {str(p):hashlib.sha256(p.read_bytes()).hexdigest()
    for p in [Path('/candidate/project/rc_filter.kicad_sch'),
              Path('/candidate/project/rc_filter.kicad_pcb')]}
(ROOT/'connectivity-review.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result['comparisons'],indent=2))
