import os, sys, re, json, math, hashlib, subprocess
from pathlib import Path
import xml.etree.ElementTree as ET
D=Path('/work/experiments')
log=[]
def run(argv, name, env=None):
    p=subprocess.run(argv,cwd=D,env=env,text=True,capture_output=True)
    (D/(name+'.stdout.log')).write_text(p.stdout)
    (D/(name+'.stderr.log')).write_text(p.stderr)
    log.append({'argv':argv,'cwd':str(D),'exit_code':p.returncode,'stdout':name+'.stdout.log','stderr':name+'.stderr.log'})
    return p
for argv, name in [(['python3','--version'],'python-version'),(['kicad-cli','--version'],'kicad-version'),(['ngspice','--version'],'ngspice-version')]:
    run(argv,name)
env=os.environ.copy()
env.update(KICAD_CONFIG_HOME=str(D/'kicad-config'),KICAD10_SYMBOL_DIR='/candidate/stock/symbols',KICAD10_FOOTPRINT_DIR='/candidate/stock/footprints',KICAD10_TEMPLATE_DIR='/candidate/stock/template')
log.append({'kicad_env_overrides':{k:env[k] for k in ['KICAD_CONFIG_HOME','KICAD10_SYMBOL_DIR','KICAD10_FOOTPRINT_DIR','KICAD10_TEMPLATE_DIR']}})
# Read all native schematic/PCB text, not just packet summaries. Minimal S-expression reader.
def sexpr(text):
    tokens=re.findall(r'"(?:\\.|[^"\\])*"|[()]|[^\s()]+',text)
    stack=[]; root=None
    for token in tokens:
        if token=='(':
            n=[]
            if stack: stack[-1].append(n)
            else: root=n
            stack.append(n)
        elif token==')': stack.pop()
        else: stack[-1].append(json.loads(token) if token.startswith('"') else token)
    return root
def children(x,key): return [v for v in x if isinstance(v,list) and v and v[0]==key]
def one(x,key):
    a=children(x,key)
    return a[0][1:] if a else []
files=['requirements.txt','project/rc_filter.kicad_sch','project/rc_filter.kicad_pcb','project/rc_filter.kicad_pro','project/sym-lib-table','project/fp-lib-table']
hashes={f:hashlib.sha256((Path('/candidate')/f).read_bytes()).hexdigest() for f in files}
(D/'native_hashes_before.json').write_text(json.dumps(hashes,indent=2)+'\n')
sch=sexpr(Path('/candidate/project/rc_filter.kicad_sch').read_text())
pcb=sexpr(Path('/candidate/project/rc_filter.kicad_pcb').read_text())
syms=[]
for s in children(sch,'symbol'):
    props={p[1]:p[2] for p in children(s,'property')}
    syms.append({'properties':props,'lib_id':one(s,'lib_id'),'at':one(s,'at'),'uuid':one(s,'uuid'),'dnp':one(s,'dnp')})
fps=[]
for fp in children(pcb,'footprint'):
    props={p[1]:p[2] for p in children(fp,'property')}
    fps.append({'properties':props,'pads':[{'number':p[1],'at':one(p,'at'),'net':one(p,'net')} for p in children(fp,'pad')]})
(D/'native_summary.json').write_text(json.dumps({'symbols':syms,'labels':[l[1] for l in children(sch,'label')],'footprints':fps,'tracks':[{'start':one(s,'start'),'end':one(s,'end'),'net':one(s,'net')} for s in children(pcb,'segment')]},indent=2)+'\n')
# Read original records and execution metadata; record only the relevant audit details.
checks={}
for name in ['erc','drc','netlist','geometry']:
    raw=json.loads(Path('/evidence/checks',name+'.json').read_text())
    ex=json.loads(Path('/evidence/work',name,'execution.json').read_text())
    checks[name]={'summary':raw['summary'],'details':raw['details'],'limitations':raw['limitations'],'argv':ex['argv'],'exit_code':ex['exit_code'],'console':Path('/evidence/work',name,'console.log').read_text()}
for name in ['erc','drc']:
    checks[name]['native_report']=json.loads(Path('/evidence/work',name,name+'.json').read_text())
checks['geometry']['native_geometry']=json.loads(Path('/evidence/work/geometry/geometry.json').read_text())
(D/'check_records_audit.json').write_text(json.dumps(checks,indent=2)+'\n')
run(['kicad-cli','sch','export','netlist','--format','kicadxml','-o',str(D/'independent.xml'),'/candidate/project/rc_filter.kicad_sch'],'export',env)
x=ET.parse(D/'independent.xml').getroot()
vals={c.attrib['ref']:c.findtext('value') for c in x.findall('./components/comp')}
nets={n.attrib['name']:sorted(q.attrib['ref']+'.'+q.attrib['pin'] for q in n.findall('node')) for n in x.findall('./nets/net')}
original=ET.parse('/evidence/work/netlist/design.xml').getroot()
original_nets={n.attrib['name']:sorted(q.attrib['ref']+'.'+q.attrib['pin'] for q in n.findall('node')) for n in original.findall('./nets/net')}
assert nets==original_nets
board_nets={}
for fp in fps:
    ref=fp['properties']['Reference']
    for p in fp['pads']:
        board_nets.setdefault(p['net'][0],[]).append(ref+'.'+p['number'])
board_nets={k:sorted(v) for k,v in board_nets.items()}
assert board_nets==nets
(D/'connectivity_comparison.json').write_text(json.dumps({'values':vals,'nets':nets,'matches_original_export':nets==original_nets,'matches_native_board_pad_assignment':board_nets==nets},indent=2)+'\n')
# Validate nominal unloaded baseline with only values actually declared in the native file.
assert vals['R1']=='10k' and vals['C1']=='100n'
R=10000.; C=100e-9; t=.001
V=1-math.exp(-t/(R*C))
calc={'assumptions':['Nominal ideal R1 and C1, no ADC load or parasitics.','Ideal 0-to-1 V step at /IN; C1 initially at 0 V.','These assumptions establish an unloaded baseline only, not the unspecified switched ADC operating condition.'], 'R_ohm':R,'C_farad':C,'tau_second':R*C,'time_second':t,'formula':'Vout(t)=1-exp(-t/(R*C))','Vout_volt':V,'baseline_in_window':.62<=V<=.65,'tau_window_second':{'minimum':-t/math.log(1-.65),'maximum':-t/math.log(1-.62)},'symbolic_charge_sharing':'If an ideal switch connects a sampling capacitor Cs initially at Vs to C1 at Vminus, charge conservation gives Vplus=(C1*Vminus+Cs*Vs)/(C1+Cs). No Cs, Vs, Rsw or connection times assigned; this is an explanatory limiting relation, not an ADC model.'}
(D/'calculation.json').write_text(json.dumps(calc,indent=2)+'\n')
# DC input with explicit initial capacitor voltage and UIC represents the t=0 ideal step.
deck='''Nominal unloaded native RC; no ADC model, not a loaded-interface acceptance test
Vin in 0 DC 1
R1 in out 10k
C1 out 0 100n IC=0
.options reltol=1e-8 abstol=1e-12 vntol=1e-10
.tran 0.1u 1.1m 0 0.1u uic
.meas tran out_1ms FIND v(out) AT=1m
.meas tran in_1ms FIND v(in) AT=1m
.end
'''
(D/'nominal.cir').write_text(deck)
sp=run(['ngspice','-b','nominal.cir'],'nominal')
m=re.search(r'out_1ms\s*=\s*([\d.eE+-]+)',sp.stdout)
measured=float(m.group(1)) if m else None
(D/'measurement.json').write_text(json.dumps({'Vout_1ms_volt':measured,'analytic_Vout_volt':V,'difference_volt':None if measured is None else measured-V,'acceptance':'Unloaded nominal baseline only; not switched ADC verification.','initialization':'UIC and C1 IC=0. DC 1 V input is applied from the transient start; avoids an operating point that precharges C1 to 1 V.','maximum_step_second':1e-7},indent=2)+'\n')
assert measured is not None and abs(measured-V)<1e-6
# Original files are never edited. Verify byte hashes unchanged after tools.
after={f:hashlib.sha256((Path('/candidate')/f).read_bytes()).hexdigest() for f in files}
assert after==hashes
(D/'native_hashes_after.json').write_text(json.dumps(after,indent=2)+'\n')
(D/'commands.json').write_text(json.dumps(log,indent=2)+'\n')
print(json.dumps({'connectivity':nets,'calculation':calc,'measurement_volt':measured,'native_unchanged':after==hashes},indent=2))
