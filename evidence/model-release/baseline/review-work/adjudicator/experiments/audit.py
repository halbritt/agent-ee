import re, json, hashlib, collections, subprocess, sys
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT=Path('/candidate/project'); OUT=Path('/work/experiments')
def parse(p):
    stack=[]; root=None
    for t in re.findall(r'"(?:\\.|[^"\\])*"|[()]|[^\s()]+',p.read_text()):
        if t=='(':
            n=[]
            if stack: stack[-1].append(n)
            else: root=n
            stack.append(n)
        elif t==')': stack.pop()
        else: stack[-1].append(json.loads(t) if t.startswith('"') else t)
    return root
def children(n,k): return [v for v in n if isinstance(v,list) and v and v[0]==k]
def first(n,k): return next(iter(children(n,k)),None)
def props(n): return {p[1]:p[2] for p in children(n,'property')}
def digest(p): return hashlib.sha256(p.read_bytes()).hexdigest()
pcb=parse(ROOT/'Polygonus-Universal-Base.kicad_pcb')
fp={props(f).get('Reference'):f for f in children(pcb,'footprint')}
selected=['U11','U12','U13','U14','U16','U17','U18','U19','R36','R37','R1','R2','RN1','RN2','RN3','RN4','U20','U21']
result={'versions':{'python':sys.version,'kicad':subprocess.check_output(['kicad-cli','version'],text=True).strip()},
 'board_sha256':digest(ROOT/'Polygonus-Universal-Base.kicad_pcb'),
 'alias_identical':digest(ROOT/'VR Conditioner.kicad_sch')==digest(ROOT/'VR conditioner.kicad_sch')}
result['board_footprints']={r:{'properties':props(fp[r]),'attr':first(fp[r],'attr'),'uuid':first(fp[r],'uuid'),
 'pads':[{'number':p[1],'type':p[2],'size':first(p,'size'),'drill':first(p,'drill'),'net':first(p,'net')} for p in children(fp[r],'pad')]} for r in selected}
result['native_schematics']={}
for name in ['Lowside Adder.kicad_sch','wbo-can-trans.kicad_sch']:
    ss=children(parse(ROOT/name),'symbol')
    result['native_schematics'][name]=[{'properties':props(s),'dnp':first(s,'dnp'),'in_bom':first(s,'in_bom'),
     'uuid':first(s,'uuid'),'instances':first(s,'instances')} for s in ss if props(s).get('Reference','').startswith(('U','RN','R'))]
pro=json.loads((ROOT/'Polygonus-Universal-Base.kicad_pro').read_text())
result['rules']=pro['board']['design_settings']['rules']
result['exclusions']=pro['board']['design_settings']['drc_exclusions']
result['j1_holes']=[]
for p in children(fp['J1'],'pad'):
    if p[1]=='':
        size=first(p,'size')[1:]; drill=first(p,'drill')[1:]
        result['j1_holes'].append({'type':p[2],'local_at':first(p,'at'),'size':size,'drill':drill,'uuid':first(p,'uuid'),
         'radial_annulus_mm':(float(size[0])-float(drill[0]))/2})
result['j1_at']=first(fp['J1'],'at')
drc=json.loads(Path('/evidence/work/drc/drc.json').read_text())
erc=json.loads(Path('/evidence/work/erc/erc.json').read_text())
result['drc_summary']={'violations':len(drc['violations']),'parity':len(drc['schematic_parity']),'unconnected':len(drc['unconnected_items']),
 'types':dict(collections.Counter(v['type'] for v in drc['violations'])),
 'severity':dict(collections.Counter(v['severity'] for v in drc['violations']+drc['schematic_parity']))}
result['drc_errors']=[{'index':i,**v} for i,v in enumerate(drc['violations']) if v['severity']=='error']
ev=[{'sheet':s['path'],'index':i,**v} for s in erc['sheets'] for i,v in enumerate(s['violations'])]
result['erc_summary']={'sheets':len(erc['sheets']),'violations':len(ev),'severity':dict(collections.Counter(v['severity'] for v in ev))}
result['erc_errors']=[v for v in ev if v['severity']=='error']
result['erc_ignored']=erc['ignored_checks'];result['drc_ignored']=drc['ignored_checks']
xml=ET.parse('/evidence/work/netlist/design.xml').getroot()
nets={n.attrib['name']:sorted(z.attrib['ref']+'.'+z.attrib['pin'] for z in n) for n in xml.find('nets')}
comps={c.attrib['ref']:c for c in xml.find('components')}
padnets={r+'.'+p[1]:first(p,'net')[-1] for r,f in fp.items() for p in children(f,'pad') if p[1] and first(p,'net')}
result['netlist']={'sheets':len(xml.find('design').findall('sheet')),'components':len(comps),'nets':len(nets),
 'pad_net_mismatches':[(n,p,padnets.get(p)) for n,ps in nets.items() for p in ps if padnets.get(p)!=n]}
result['selected_nets']={n:ps for n,ps in nets.items() if n.startswith(('/CAN','/LS9','/LS10','/LS11','/LS12','/IGN9','/IGN10','/IGN11','/IGN12','Net-(RN'))}
pin_net={p:n for n,ps in nets.items() for p in ps}
result['can2_direct_resistors']=[{'ref':r,'value':c.findtext('value'),'dnp':any(p.attrib['name']=='dnp' for p in c.findall('property'))} for r,c in comps.items() if r.startswith('R') and not r.startswith('RN') and {pin_net.get(r+'.1'),pin_net.get(r+'.2')}=={'/CAN2+','/CAN2-'}]
result['calculations']={'3V3_case_input_upper_bound_V':3.3,'published_N3_minimum_V':3.5,'margin_V':3.3-3.5,
 'LS9_LS10_unloaded_highZ_V':5*1000/(1000+1000),'highZ_Rth_ohm':500,'CAN_local_termination_conductance_S':0,
 'assumptions':['3.3 V GPIO case is conditional; exact mated module is absent.','High-Z bias neglects switch and stepper loading; it predicts no switch state.','DNP resistor population treated as omitted; modules and external harness not flattened.']}
(OUT/'audit.json').write_text(json.dumps(result,indent=2))
print(json.dumps({k:result[k] for k in ['versions','board_sha256','alias_identical','erc_summary','drc_summary','netlist','can2_direct_resistors','j1_holes']},indent=2))
