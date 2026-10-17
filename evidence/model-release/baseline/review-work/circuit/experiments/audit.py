"""Read-only native/connectivity audit; run with python3 audit.py."""
import hashlib
import json
import re
from pathlib import Path
import xml.etree.ElementTree as ET
import pcbnew

OUT = Path('/work/experiments')
ROOT = Path('/candidate/project')
def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def sexpr(path):
    stack = []
    root = None
    for t in re.findall(r'"(?:\\.|[^"\\])*"|[()]|[^\s()]+', path.read_text()):
        if t == '(':
            n = []
            if stack:
                stack[-1].append(n)
            else:
                root = n
            stack.append(n)
        elif t == ')':
            stack.pop()
        else:
            stack[-1].append(json.loads(t) if t.startswith('"') else t)
    return root

def children(x, tag):
    return [y for y in x if isinstance(y, list) and y and y[0] == tag]

def first(x, tag):
    return next(iter(children(x, tag)), None)

sources = {}
for filename in ['Polygonus-Universal-Base.kicad_sch', 'Lowside Adder.kicad_sch',
                 'wbo-can-trans.kicad_sch', 'VR Conditioner.kicad_sch',
                 'vr_module_poly.kicad_sch', 'etb-9201.kicad_sch']:
    path = ROOT / filename
    parsed = sexpr(path)
    comps = []
    for s in children(parsed, 'symbol'):
        props = {p[1]: p[2] for p in children(s, 'property')}
        if props.get('Reference', '').startswith('#'):
            continue
        comps.append({'properties': props, 'dnp': first(s, 'dnp'),
                      'uuid': first(s, 'uuid'), 'position': first(s, 'at')})
    sources[filename] = {'sha256': digest(path), 'components': comps}

r = ET.parse(OUT / 'independent-netlist.xml').getroot()
ref = ET.parse('/evidence/work/netlist/design.xml').getroot()
def nets(tree):
    return {n.attrib['name']: sorted(z.attrib['ref']+'.'+z.attrib['pin'] for z in n)
            for n in tree.find('nets')}
ns = nets(r)
board = pcbnew.LoadBoard(str(ROOT / 'Polygonus-Universal-Base.kicad_pcb'))
pads = {f.GetReference()+'.'+p.GetNumber(): p.GetNetname()
        for f in board.GetFootprints() for p in f.Pads() if p.GetNumber()}
mismatch = [[pin,n,pads.get(pin)] for n, pins in ns.items()
            for pin in pins if pads.get(pin) != n]
selected = {'R1','R2','RN1','RN2','RN3','RN4','U11','U12','U13','U14',
            'U16','U17','U18','U19','U20','U21','R36','R37','J2','A1'}
fp = {}
for f in board.GetFootprints():
    if f.GetReference() in selected:
        fp[f.GetReference()] = {
            'value': f.GetValue(), 'footprint': str(f.GetFPID().GetUniStringLibId()),
            'dnp': f.IsDNP(), 'uuid': str(f.m_Uuid.AsString()),
            'pads': {p.GetNumber(): p.GetNetname() for p in f.Pads() if p.GetNumber()}}

comps = {c.attrib['ref']: c for c in r.find('components')}
pin_net = {pin:n for n,pins in ns.items() for pin in pins}
bridges=[]
for name,c in comps.items():
    if name.startswith('R') and not name.startswith('RN'):
        a,b = pin_net.get(name+'.1'),pin_net.get(name+'.2')
        if {a,b} == {'/CAN2+','/CAN2-'}:
            bridges.append({'ref':name,'value':c.findtext('value'),
                            'dnp':any(p.attrib['name']=='dnp' for p in c.findall('property'))})

result = {
    'commands': ['kicad-cli sch export netlist --format kicadxml -o /work/experiments/independent-netlist.xml /candidate/project/Polygonus-Universal-Base.kicad_sch',
                 'python3 /work/experiments/audit.py'],
    'kicad_version':pcbnew.Version(), 'board_sha256':digest(ROOT/'Polygonus-Universal-Base.kicad_pcb'),
    'alias_byte_identical':(ROOT/'VR conditioner.kicad_sch').read_bytes()==(ROOT/'VR Conditioner.kicad_sch').read_bytes(),
    'sheet_instances':len(r.find('design').findall('sheet')),
    'component_count':len(comps),'net_count':len(ns),
    'reference_netlist_identical':ns==nets(ref), 'native_board_pad_net_mismatches':mismatch,
    'selected_board_footprints':fp,
    'can2_direct_resistors':bridges,
    'selected_nets':{n:pins for n,pins in ns.items() if n in ['/LS9','/LS10','/LS11','/LS12',
        '/IGN9','/IGN10','/IGN11','/IGN12','/CAN2+','/CAN2-','/CAN_TX','/CAN_RX','/CAN_TX2','/CAN_RX2',
        'Net-(RN2-R4.2)','Net-(RN2-R3.2)']},
    'native_sources':sources,
    'limitations':['Pad net identity is not a physical continuity or waveform measurement.',
                  'No PCB edits, zone refill or candidate modifications.',
                  'Modules J2, U1, U2 and A1 are treated as interfaces, not flattened circuits.']}
(OUT/'audit.json').write_text(json.dumps(result,indent=2))
print(json.dumps({k:result[k] for k in ['board_sha256','alias_byte_identical','sheet_instances',
    'component_count','net_count','reference_netlist_identical','native_board_pad_net_mismatches','can2_direct_resistors']},indent=2))
