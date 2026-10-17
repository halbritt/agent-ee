import collections
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

OUT = Path('/work/experiments')
CAND = Path('/candidate/project')
commands = []

def run(argv, name, env=None):
    start = time.monotonic()
    p = subprocess.run(argv, capture_output=True, text=True, env=env)
    (OUT / (name + '.log')).write_text(p.stdout + p.stderr)
    commands.append(dict(argv=argv, exit_code=p.returncode,
                         duration_s=time.monotonic()-start, log=name+'.log'))
    return p

def parse(path):
    stack = [[]]
    for m in re.finditer(r'\(|\)|"(?:\\.|[^"\\])*"|[^\s()]+', path.read_text()):
        t = m.group()
        if t == '(':
            a = []; stack[-1].append(a); stack.append(a)
        elif t == ')':
            stack.pop()
        else:
            stack[-1].append(json.loads(t) if t.startswith('"') else t)
    assert len(stack) == 1
    return stack[0][0]

def children(node, key):
    return [n for n in node if isinstance(n, list) and n and n[0] == key]

def child(node, key):
    return children(node, key)[0]

def prop(node, key):
    return next(n[2] for n in children(node, 'property') if n[1] == key)

config = OUT / 'kicad-config' / '10.0'
config.mkdir(parents=True, exist_ok=True)
for table, src in [('sym-lib-table','erc'), ('fp-lib-table','drc')]:
    shutil.copyfile(Path('/evidence/work') / src / 'kicad-config/10.0' / table, config / table)
env = dict(os.environ, KICAD_CONFIG_HOME=str(config.parent),
           KICAD10_SYMBOL_DIR='/candidate/stock/symbols',
           KICAD10_FOOTPRINT_DIR='/candidate/stock/footprints',
           KICAD10_TEMPLATE_DIR='/candidate/stock/template')
result = {'python':sys.version, 'kicad_version':run(['kicad-cli','--version'],'version').stdout.strip()}
for kind, extra, suffix in [('erc', [], '.kicad_sch'), ('drc', ['--schematic-parity'], '.kicad_pcb')]:
    argv = ['kicad-cli', 'sch' if kind=='erc' else 'pcb', kind, '--format','json','--severity-all', *extra, '--units','mm','-o',str(OUT / (kind+'.json')),str(CAND / ('Polygonus-Universal-Base'+suffix))]
    run(argv, kind, env)
    fresh=json.loads((OUT/(kind+'.json')).read_text())
    original=json.loads(Path(f'/evidence/work/{kind}/{kind}.json').read_text())
    keys=['sheets'] if kind=='erc' else ['violations','unconnected_items','schematic_parity']
    # Compare complete check records, excluding only report timestamp/source metadata.
    result[kind]={'identical_records':{k:fresh[k]==original[k] for k in keys}}
    vs=[v for s in fresh['sheets'] for v in s['violations']] if kind=='erc' else fresh['violations']
    result[kind]['counts']=dict(collections.Counter(v['severity'] for v in vs))
    result[kind]['errors']=[v for v in vs if v['severity']=='error']

run(['kicad-cli','sch','export','netlist','--format','kicadxml','-o',str(OUT/'netlist.xml'),str(CAND/'Polygonus-Universal-Base.kicad_sch')], 'netlist', env)
x=ET.parse(OUT/'netlist.xml').getroot()
nets={n.attrib['name']:sorted(z.attrib['ref']+'.'+z.attrib['pin'] for z in n.findall('node')) for n in x.findall('nets/net')}
packet=json.loads(Path('/evidence/work/netlist/connectivity.json').read_text())
result['netlist']={'sheets':len(x.findall('design/sheet')),'components':len(x.findall('components/comp')),'nets':len(nets),'identical_connectivity':nets=={k:sorted(v) for k,v in packet['nets'].items()}}

root=parse(CAND/'Polygonus-Universal-Base.kicad_sch')
lib=child(root,'lib_symbols')
selected={}
for sym in children(root,'symbol'):
    ref=prop(sym,'Reference')
    if ref not in ['A1','J2','#PWR01']: continue
    libid=child(sym,'lib_id')[1]
    source=next(s for s in children(lib,'symbol') if s[1]==libid)
    selected[ref]={'lib_id':libid,'at':child(sym,'at')[1:],'pins':[]}
    wanted={'A1':['2','8'],'J2':['40','41','90'],'#PWR01':['1']}[ref]
    for unit in children(source,'symbol'):
        for pin in children(unit,'pin'):
            num=child(pin,'number')[1]
            if num in wanted:
                selected[ref]['pins'].append({'number':num,'type':pin[1],'name':child(pin,'name')[1]})
result['native_erc_declarations']=selected
result['alias_identical']=(CAND/'VR Conditioner.kicad_sch').read_bytes()==(CAND/'VR conditioner.kicad_sch').read_bytes()

pcb=parse(CAND/'Polygonus-Universal-Base.kicad_pcb')
result['board_sha256']=hashlib.sha256((CAND/'Polygonus-Universal-Base.kicad_pcb').read_bytes()).hexdigest()
fps={prop(f,'Reference'):f for f in children(pcb,'footprint')}
result['holes']=[]
for p in children(fps['J1'],'pad'):
    if p[1]!='':continue
    size=list(map(float,child(p,'size')[1:])); drill=float(child(p,'drill')[1])
    result['holes'].append({'type':p[2],'uuid':child(p,'uuid')[1], 'local_at':child(p,'at')[1:],'size_mm':size,'drill_mm':drill,'annulus_mm':(min(size)-drill)/2})
pro=json.loads((CAND/'Polygonus-Universal-Base.kicad_pro').read_text())
result['annular_rule_mm']=pro['board']['design_settings']['rules']['min_via_annular_width']
result['can2']={'nets':{k:nets[k] for k in ['/CAN2+','/CAN2-']},'resistors':{},'direct_resistors':[]}
for ref in ['R36','R37']:
    f=fps[ref]
    result['can2']['resistors'][ref]={'board_attributes':child(f,'attr')[1:], 'board_value':prop(f,'Value'),'netlist':packet['components'][ref]}
for ref,c in packet['components'].items():
    if not ref.startswith('R'):continue
    if any(z.startswith(ref+'.') for z in nets['/CAN2+']) and any(z.startswith(ref+'.') for z in nets['/CAN2-']):
        result['can2']['direct_resistors'].append({'ref':ref,'value':c['value'],'dnp':c['dnp']})
result['can2']['populated_carrier_direct_conductance_S']=sum(1/float(r['value']) for r in result['can2']['direct_resistors'] if not r['dnp'])
result['commands']=commands
(OUT/'audit.json').write_text(json.dumps(result,indent=2))
(OUT/'commands.json').write_text(json.dumps(commands,indent=2))
print(json.dumps({k:v for k,v in result.items() if k not in ['erc','drc','commands']},indent=2))
print('Check comparisons:',{k:{j:v for j,v in result[k].items() if j!='errors'} for k in ['erc','drc']})
