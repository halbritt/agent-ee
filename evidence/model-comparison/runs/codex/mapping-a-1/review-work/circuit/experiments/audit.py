"""Read-only native connectivity audit for this single-sheet RC fixture."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

OUT = Path('/work/experiments')
ENV = dict(os.environ, KICAD_CONFIG_HOME=str(OUT / 'kicad-config'),
           KICAD10_SYMBOL_DIR='/candidate/stock/symbols',
           KICAD10_FOOTPRINT_DIR='/candidate/stock/footprints',
           KICAD10_TEMPLATE_DIR='/candidate/stock/template')
commands = []
def run(args, stem):
    result = subprocess.run(args, env=ENV, text=True, capture_output=True)
    (OUT / (stem + '.stdout')).write_text(result.stdout)
    (OUT / (stem + '.stderr')).write_text(result.stderr)
    commands.append(dict(argv=args, exit_code=result.returncode,
                         stdout=stem+'.stdout', stderr=stem+'.stderr'))
    return result

run(['kicad-cli', '--version'], 'kicad-version')
run([sys.executable, '--version'], 'python-version')
run(['kicad-cli','sch','export','netlist','--format','kicadxml','-o',
     str(OUT/'design.xml'),'/candidate/project/rc_filter.kicad_sch'], 'netlist')
run(['kicad-cli','sch','erc','--format','json','--severity-all','--units','mm',
     '-o',str(OUT/'erc.json'),'/candidate/project/rc_filter.kicad_sch'], 'erc')
run(['kicad-cli','pcb','drc','--format','json','--severity-all','--schematic-parity',
     '--units','mm','-o',str(OUT/'drc.json'),
     '/candidate/project/rc_filter.kicad_pcb'], 'drc')
(OUT/'commands.json').write_text(json.dumps(commands, indent=2)+'\n')

def parse(path):
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|[()]|[^\s()]+',path.read_text())
    stack=[]
    for token in tokens:
        if token == '(':
            node=[]
            if stack: stack[-1].append(node)
            stack.append(node)
        elif token == ')':
            node=stack.pop()
            if not stack: return node
        else: stack[-1].append(json.loads(token) if token.startswith('"') else token)
def children(node,key): return [x for x in node if isinstance(x,list) and x[0]==key]
def one(node,key): return children(node,key)[0]
def props(node): return {x[1]:x[2] for x in children(node,'property')}
def xy(node): return tuple(round(float(v),6) for v in node[1:3])

SCH=Path('/candidate/project/rc_filter.kicad_sch')
PCB=Path('/candidate/project/rc_filter.kicad_pcb')
sch=parse(SCH)
pcb=parse(PCB)
parent={}
def root(p):
    parent.setdefault(p,p)
    if parent[p]!=p: parent[p]=root(parent[p])
    return parent[p]
def join(a,b): parent[root(a)]=root(b)
wires=[tuple(xy(p) for p in one(w,'pts')[1:]) for w in children(sch,'wire')]
for a,b in wires: join(a,b)
labels={}
for label in children(sch,'label'):
    p=xy(one(label,'at'))
    if label[1] in labels: join(p,labels[label[1]])
    labels[label[1]]=p
libs={s[1]:s for s in children(one(sch,'lib_symbols'),'symbol')}
pins={}
grounds=[]
for symbol in children(sch,'symbol'):
    properties=props(symbol)
    ref=properties['Reference']
    lib_id=one(symbol,'lib_id')[1]
    at=one(symbol,'at')
    origin=xy(at)
    angle=float(at[3])
    assert not children(symbol,'mirror'), 'Mirrored symbols not supported by this audit'
    assert angle in (0,180), 'Only observed rotations are supported'
    for unit in children(libs[lib_id],'symbol'):
        for pin in children(unit,'pin'):
            x,y=xy(one(pin,'at'))
            dx,dy=(x,-y) if angle==0 else (-x,y)
            p=(round(origin[0]+dx,6),round(origin[1]+dy,6))
            pins[ref+'.'+one(pin,'number')[1]]=p
            if lib_id=='power:GND': grounds.append(p)
for p in grounds[1:]: join(p,grounds[0])
# This fixture has only endpoint connections in the schematic; check the assumption.
for p in list(pins.values())+list(labels.values()):
    for a,b in wires:
        cross=(p[0]-a[0])*(b[1]-a[1])-(p[1]-a[1])*(b[0]-a[0])
        if abs(cross)<1e-8 and min(a[0],b[0])<=p[0]<=max(a[0],b[0]) and min(a[1],b[1])<=p[1]<=max(a[1],b[1]):
            assert p in (a,b), 'Interior wire contact needs separate handling'
net_points={'/IN':labels['IN'],'/OUT':labels['OUT'],'GND':grounds[0]}
native={name:sorted(ref for ref,p in pins.items() if not ref.startswith('#') and root(p)==root(anchor))
        for name,anchor in net_points.items()}
expected={'/IN':['J1.1','R1.1'],'/OUT':['C1.1','J1.2','R1.2'],'GND':['C1.2','J1.3']}
xml_nets={n.attrib['name']:sorted(p.attrib['ref']+'.'+p.attrib['pin'] for p in n.findall('node'))
          for n in ET.parse(OUT/'design.xml').findall('./nets/net')}
pcb_nets={}
pad_positions={}
for footprint in children(pcb,'footprint'):
    ref=props(footprint)['Reference']
    at=one(footprint,'at')
    assert len(at)==3, 'Rotated PCB footprint needs separate handling'
    ox,oy=xy(at)
    for pad in children(footprint,'pad'):
        pin=ref+'.'+pad[1]
        pcb_nets.setdefault(one(pad,'net')[1],[]).append(pin)
        x,y=xy(one(pad,'at'))
        pad_positions[pin]=[round(ox+x,6),round(oy+y,6)]
pcb_nets={name:sorted(nodes) for name,nodes in pcb_nets.items()}
supplied=json.loads(Path('/evidence/work/netlist/connectivity.json').read_text())['nets']
report={
    'hashes':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [SCH,PCB]},
    'schematic_native_pin_positions_mm':pins,
    'schematic_native_nets':native,
    'fresh_export_nets':xml_nets,
    'pcb_native_pad_nets':pcb_nets,
    'pcb_native_pad_positions_mm':pad_positions,
    'connector_contract_and_supplied_netlist_agree':native==expected==xml_nets==pcb_nets==supplied,
    'ideal_topology_calculation':{
        'assumptions':'Ideal R and C; unloaded output; zero source impedance. Illustrative topology calculation, not specified operating conditions or a performance requirement.',
        'R_ohm':10000, 'C_F':100e-9, 'tau_seconds':10000*100e-9,
        'fc_Hz':1/(2*math.pi*10000*100e-9), 'transfer':'Vout/Vin = 1/(1+s*R*C)'},
    'limits':['Single-sheet fixture only; parser explicitly checks observed rotations and endpoint-only schematic contacts.',
              'PCB audit compares pad identities/net assignments; physical continuity uses saved native copper inspection and fresh DRC without refill.',
              'No electrical simulation, hardware measurements, part ratings, tolerances, source/load range, or environmental qualification.']}
(OUT/'connectivity-audit.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
