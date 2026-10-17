import hashlib, json, os, platform, re, subprocess, xml.etree.ElementTree as ET
from pathlib import Path
base=Path('/work/experiments')
records={'python_version':platform.python_version(), 'reproduction_command':'python3 /work/experiments/adjudicate.py', 'commands':[], 'assumptions':['Evaluate only the explicit connector contract and saved connectivity; no component ratings or load specification assumed.'], 'limitations':['No board modification or zone refill; no hardware measurement.']}
env=os.environ.copy()
env['KICAD_CONFIG_HOME']=str(base/'kicad-config')
def run(argv):
    r=subprocess.run(argv, capture_output=True, text=True, env=env)
    records['commands'].append({'argv':argv,'returncode':r.returncode,'stdout':r.stdout,'stderr':r.stderr})
    return r
run(['kicad-cli','--version'])
r=run(['kicad-cli','sch','export','netlist','--format','kicadxml','-o',str(base/'native-netlist.xml'),'/candidate/project/rc_filter.kicad_sch'])
if r.returncode: raise RuntimeError(r.stderr)
def parse(s):
    tokens=re.findall(r'"(?:\\.|[^"\\])*"|[()]|[^\s()]+',s)
    stack=[]
    for t in tokens:
        if t=='(': stack.append([])
        elif t==')':
            node=stack.pop()
            if not stack: return node
            stack[-1].append(node)
        else: stack[-1].append(json.loads(t) if t.startswith('"') else t)
def nodes(n,key): return [x for x in n if isinstance(x,list) and x and x[0]==key]
def one(n,key): return nodes(n,key)[0]
board=Path('/candidate/project/rc_filter.kicad_pcb')
tree=parse(board.read_text())
records['native_hashes']={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [board,Path('/candidate/project/rc_filter.kicad_sch'),Path('/candidate/requirements.txt')]}
records['requirement']=Path('/candidate/requirements.txt').read_text()
records['pcb_pad_nets']={}
for fp in nodes(tree,'footprint'):
    ref=next(x[2] for x in nodes(fp,'property') if x[1]=='Reference')
    for pad in nodes(fp,'pad'):
        records['pcb_pad_nets'][ref+'.'+pad[1]]=one(pad,'net')[1]
root=ET.parse(base/'native-netlist.xml').getroot()
records['exported_nets']={n.attrib['name']:[x.attrib['ref']+'.'+x.attrib['pin'] for x in n.findall('node')] for n in root.findall('./nets/net')}
records['packet_check_results']={}
for check in ['erc','drc']:
    data=json.loads(Path('/evidence/work/'+check+'/'+check+'.json').read_text())
    records['packet_check_results'][check]={k:data[k] for k in ['violations','unconnected_items','schematic_parity'] if k in data}
    if check=='erc': records['packet_check_results'][check]['sheet_violations']={s['path']:s['violations'] for s in data['sheets']}
records['outcome']='Contract violated: PCB and freshly exported native schematic both connect J1.1 to R1.1 (/IN), J1.2 to R1.2 and C1.1 (/OUT), J1.3 to C1.2 (GND). Required input J1.2 is therefore on the capacitor shunt node; required output J1.1 is across R1 from it. The capacitor is not on the required filtered output node. Clean ERC/DRC checks establish native rule/parity consistency, not compliance with the external connector contract.'
(base/'adjudication.json').write_text(json.dumps(records,indent=2)+'\n')
print(json.dumps(records,indent=2))
