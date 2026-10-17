import hashlib, json, math, pathlib, re, subprocess, sys, xml.etree.ElementTree as ET
base=pathlib.Path('/work/experiments')
commands=[]
def run(argv,name):
    p=subprocess.run(argv,cwd=base,text=True,capture_output=True)
    (base/(name+'.stdout.txt')).write_text(p.stdout)
    (base/(name+'.stderr.txt')).write_text(p.stderr)
    commands.append({'argv':argv,'cwd':str(base),'exit_code':p.returncode,'stdout':name+'.stdout.txt','stderr':name+'.stderr.txt'})
    (base/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
    if p.returncode: raise RuntimeError(argv)
    return p.stdout

def parse(path):
    tokens=re.findall(r'"(?:\\.|[^"\\])*"|[^\s()]+|[()]',path.read_text())
    stack=[]
    root=None
    for t in tokens:
        if t=='(':
            node=[]
            if stack: stack[-1].append(node)
            else: root=node
            stack.append(node)
        elif t==')': stack.pop()
        else: stack[-1].append(json.loads(t) if t.startswith('"') else t)
    assert not stack
    return root

def children(node,tag): return [n for n in node if isinstance(n,list) and n and n[0]==tag]
def child(node,tag): return children(node,tag)[0]
def properties(node): return {n[1]:n[2] for n in children(node,'property')}
run(['python3','--version'],'python-version')
run(['kicad-cli','--version'],'kicad-version')
run(['ngspice','--version'],'ngspice-version')
schpath=pathlib.Path('/candidate/project/rc_filter.kicad_sch')
pcbpath=pathlib.Path('/candidate/project/rc_filter.kicad_pcb')
sch=parse(schpath); pcb=parse(pcbpath)
syms={properties(n)['Reference']: {'value':properties(n)['Value'],'uuid':child(n,'uuid')[1],'dnp':child(n,'dnp')[1],'at':child(n,'at')[1:]} for n in children(sch,'symbol')}
fps={properties(n)['Reference']: {'value':properties(n)['Value'],'pads':{p[1]:child(p,'net')[1:] for p in children(n,'pad')}} for n in children(pcb,'footprint')}
xml=ET.parse('/evidence/work/netlist/design.xml')
values={n.attrib['ref']:n.findtext('value') for n in xml.findall('./components/comp')}
nets={n.attrib['name']:sorted(x.attrib['ref']+'.'+x.attrib['pin'] for x in n.findall('node')) for n in xml.findall('./nets/net')}
assert syms['R1']['value']==fps['R1']['value']==values['R1']=='100k'
assert syms['C1']['value']==fps['C1']['value']==values['C1']=='100n'
assert nets=={'/IN':['J1.1','R1.1'],'/OUT':['C1.1','J1.2','R1.2'],'GND':['C1.2','J1.3']}
assert fps['R1']['pads']=={'1':['/IN'],'2':['/OUT']}
assert fps['C1']['pads']=={'1':['/OUT'],'2':['GND']}
widths=[float(child(s,'width')[1]) for s in children(pcb,'segment')]
assert len(widths)==5 and all(w>=0.2 for w in widths)
checks={p.stem:json.loads(p.read_text()) for p in pathlib.Path('/evidence/checks').glob('*.json')}
(base/'check-records.json').write_text(json.dumps(checks,indent=2)+'\n')
# Explicit 1 ms pre-step interval independently tests timing and initial state.
deck='''* Independent unchanged native RC values, 1 ms pre-step
R1 IN OUT 100k
C1 OUT 0 100n
VSTEP IN 0 PWL(0 0 1m 0 1.000001m 1 6.1m 1)
.tran 1u 6.1m 0 1u
.meas tran vout_1ms FIND v(OUT) AT=2.000001m
.meas tran vout_5ms FIND v(OUT) AT=6.000001m
.end
'''
(base/'independent.cir').write_text(deck)
run(['ngspice','-n','-b','-o',str(base/'independent.log'),str(base/'independent.cir')],'independent-spice')
log=(base/'independent.log').read_text()
measures={name:float(value) for name,value in re.findall(r'(vout_\w+)\s*=\s*([\d.eE+-]+)',log)}
assert measures['vout_1ms']<0.62
result={'hashes':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [schpath,pcbpath,pathlib.Path('/candidate/requirements/fixture-requirements.md')]},'native_symbols':syms,'native_footprints':fps,'native_wires':[child(n,'pts')[1:] for n in children(sch,'wire')],'native_labels':[{ 'name':n[1], 'at':child(n,'at')[1:]} for n in children(sch,'label')],'exported_values':values,'exported_nets':nets,'track_widths_mm':widths,'calculation':{'R_ohm':100000,'C_F':1e-7,'tau_s':0.01,'vout_1ms_V':-math.expm1(-0.001/0.01),'vout_5ms_V':-math.expm1(-0.005/0.01),'required_1ms_V':[0.62,0.65]},'independent_measurements_V':measures,'supplied_spice_deck':pathlib.Path('/evidence/work/rc-step/deck.cir').read_text(),'supplied_spice_log':pathlib.Path('/evidence/work/rc-step/ngspice.log').read_text()}
(base/'audit-results.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'values':values,'nets':nets,'widths_mm':widths,'calculation':result['calculation'],'independent_measurements_V':measures},indent=2))
