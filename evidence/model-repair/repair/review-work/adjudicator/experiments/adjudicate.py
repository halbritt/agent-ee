import json, re, math, hashlib, subprocess, os, sys
from pathlib import Path
import xml.etree.ElementTree as ET
base=Path('/work/experiments')
commands=[{'argv':['python3','/work/experiments/adjudicate.py'],'version':sys.version,'purpose':'Read-only native and check audit'}]
def run(argv):
    p=subprocess.run(argv,cwd=base,text=True,capture_output=True)
    commands.append(dict(argv=argv,exit_code=p.returncode,stdout=p.stdout,stderr=p.stderr))
    return p
for argv in [['kicad-cli','--version'],['ngspice','--version']]:run(argv)
def parse(path):
    toks=re.findall(r'"(?:\\.|[^"\\])*"|[^\s()]+|[()]',path.read_text())
    stack=[];root=None
    for t in toks:
        if t=='(':
            node=[]
            if stack:stack[-1].append(node)
            else:root=node
            stack.append(node)
        elif t==')':stack.pop()
        else:stack[-1].append(json.loads(t) if t.startswith('"') else t)
    assert not stack
    return root
def children(node,key):return [x for x in node if isinstance(x,list) and x[0]==key]
def one(node,key):return children(node,key)[0]
def props(node):return {x[1]:x[2] for x in children(node,'property')}
sch=Path('/candidate/project/rc_filter.kicad_sch');pcb=Path('/candidate/project/rc_filter.kicad_pcb')
s=parse(sch);b=parse(pcb)
native={props(x)['Reference']:{'value':props(x)['Value'],'dnp':one(x,'dnp')[1],'uuid':one(x,'uuid')[1]} for x in children(s,'symbol') if props(x)['Reference'] in ['R1','C1','J1']}
tracks=[dict(uuid=one(x,'uuid')[1],net=one(x,'net')[1],width_mm=float(one(x,'width')[1]),start=one(x,'start')[1:],end=one(x,'end')[1:]) for x in children(b,'segment')]
pad_nets={props(f)['Reference']+'.'+p[1]:one(p,'net')[1] for f in children(b,'footprint') for p in children(f,'pad')}
run(['kicad-cli','sch','export','netlist','--format','kicadxml','-o',str(base/'independent.xml'),str(sch)])
x=ET.parse(base/'independent.xml')
nets={n.attrib['name']:sorted(v.attrib['ref']+'.'+v.attrib['pin'] for v in n.findall('node')) for n in x.findall('./nets/net')}
provided=json.loads(Path('/evidence/work/netlist/connectivity.json').read_text())
assert nets==provided['nets']
assert pad_nets=={p:n for n,ps in nets.items() for p in ps}
assert len(tracks)==5 and all(t['width_mm']>=.2 for t in tracks)
assert native['R1']['value']=='10k' and native['C1']['value']=='100n'
assert all(v['dnp']=='no' for v in native.values())
checks={p.stem:json.loads(p.read_text()) for p in Path('/evidence/checks').glob('*.json')}
rc_log=Path('/evidence/work/rc-step/ngspice.log').read_text()
measured=float(re.search(r'vout_1ms\s*=\s*([^\s]+)',rc_log)[1])
tau=10000*100e-9
assert .62 <= measured <= .65
for typ in ['erc','drc']:
    report=json.loads(Path(f'/evidence/work/{typ}/{typ}.json').read_text())
    assert not report.get('violations',[])
    if typ=='erc':assert all(not sh['violations'] for sh in report['sheets'])
    else:assert not report['schematic_parity'] and not report['unconnected_items']
audit=dict(native_components=native,board_pad_nets=pad_nets,nets=nets,tracks=tracks,tau_s=tau,ideal_1ms=1-math.exp(-.001/tau),supplied_1ms=measured,check_outcomes={k:{a:v.get(a) for a in ['execution','outcome']} for k,v in checks.items()},native_sha256={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [sch,pcb]},finding_ids_in_packet=[],finding_ids_in_initial_reviews=[])
(base/'audit.json').write_text(json.dumps(audit,indent=2)+'\n')
(base/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
print(json.dumps(audit,indent=2))
