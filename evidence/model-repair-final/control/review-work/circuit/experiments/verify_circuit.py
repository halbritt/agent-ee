import os, re, json, math, struct, hashlib, subprocess, pathlib, xml.etree.ElementTree as ET
D=pathlib.Path('/work/experiments')
commands=[]
def run(args, name, env=None):
    p=subprocess.run(args,cwd=D,env=env,text=True,capture_output=True)
    (D/(name+'.stdout')).write_text(p.stdout)
    (D/(name+'.stderr')).write_text(p.stderr)
    commands.append({'argv':args,'cwd':str(D),'exit_code':p.returncode,'stdout':name+'.stdout','stderr':name+'.stderr'})
    return p
versions={}
for name,args in [('kicad',['kicad-cli','version']),('ngspice',['ngspice','--version']),('python',['python3','--version'])]:
    p=run(args,'version_'+name); versions[name]=p.stdout.strip()
env=os.environ.copy()
env.update(KICAD_CONFIG_HOME=str(D/'kicad-config'),KICAD10_SYMBOL_DIR='/candidate/stock/symbols',KICAD10_FOOTPRINT_DIR='/candidate/stock/footprints',KICAD10_TEMPLATE_DIR='/candidate/stock/template')
assert run(['kicad-cli','sch','export','netlist','--format','kicadxml','-o',str(D/'fresh.xml'),'/candidate/project/rc_filter.kicad_sch'],'netlist',env).returncode==0
# Independent S-expression reader: quoted strings remain atoms, no eval.
def parse_native(path):
    toks=re.findall(r'"(?:\\.|[^"\\])*"|[()]|[^\s()]+',path.read_text())
    root=[]; stack=[root]
    for t in toks:
        if t=='(': q=[]; stack[-1].append(q); stack.append(q)
        elif t==')': stack.pop()
        else: stack[-1].append(json.loads(t) if t.startswith('"') else t)
    assert len(stack)==1
    return root[0]
def children(x,key):return [a for a in x if isinstance(a,list) and a and a[0]==key]
def field(x,key):return children(x,key)[0][1:]
def props(x):return {a[1]:a[2] for a in children(x,'property')}
sch=parse_native(pathlib.Path('/candidate/project/rc_filter.kicad_sch'))
pcb=parse_native(pathlib.Path('/candidate/project/rc_filter.kicad_pcb'))
instances=[{'lib_id':field(s,'lib_id')[0],'uuid':field(s,'uuid')[0],'at':field(s,'at'),'properties':props(s),'dnp':field(s,'dnp')[0]} for s in children(sch,'symbol')]
board_pads={}
for fp in children(pcb,'footprint'):
    ref=props(fp)['Reference']
    for pad in children(fp,'pad'):board_pads[ref+'.'+pad[1]]=field(pad,'net')[0]
tracks=[{'uuid':field(s,'uuid')[0],'width_mm':float(field(s,'width')[0]),'net':field(s,'net')[0],'start_mm':field(s,'start'),'end_mm':field(s,'end')} for s in children(pcb,'segment')]
fresh=ET.parse(D/'fresh.xml').getroot(); prior=ET.parse('/evidence/work/netlist/design.xml').getroot()
def nets(x):return {n.attrib['name']:sorted(z.attrib['ref']+'.'+z.attrib['pin'] for z in n.findall('node')) for n in x.findall('./nets/net')}
def comps(x):return {c.attrib['ref']:{k:c.findtext(k) for k in ['value','footprint','tstamps']} for c in x.findall('./components/comp')}
nets_fresh=nets(fresh)
assert nets_fresh==nets(prior)
assert comps(fresh)==comps(prior)
assert {p:n for n,ps in nets_fresh.items() for p in ps}==board_pads
assert len(tracks)==5 and min(t['width_mm'] for t in tracks)>=0.2
# Parse the retained binary transient ourselves (real, native doubles).
b=pathlib.Path('/evidence/work/rc-step/result.raw').read_bytes()
head,data=b.split(b'Binary:\n',1)
header=head.decode(); nvar=int(re.search(r'No. Variables:\s*(\d+)',header)[1]); npts=int(re.search(r'No. Points:\s*(\d+)',header)[1])
assert len(data)==nvar*npts*8
rows=list(struct.iter_unpack('='+('d'*nvar),data))
assert all(rows[i][0]<rows[i+1][0] for i in range(npts-1))
def interp(t):
    for a,z in zip(rows,rows[1:]):
        if a[0]<=t<=z[0]:return [a[j]+(z[j]-a[j])*(t-a[0])/(z[0]-a[0]) for j in range(nvar)]
    raise ValueError(t)
values={s['properties']['Reference']:s['properties'].get('Value') for s in instances}
assert values['R1']=='10k' and values['C1']=='100n'
R=10000.; C=100e-9; tau=R*C; rise=1e-9
# Exact RC response for the verification's finite 1 ns linear input rise.
def exact(t):
    if t<=rise:return t/rise-tau/rise*(-math.expm1(-t/tau))
    return 1-(tau/rise)*(-math.expm1(-rise/tau))*math.exp(-(t-rise)/tau)
raw_results={'header':header,'initial':rows[0],'last':rows[-1],'sample_1ms':interp(.001),'sample_5ms':interp(.005),'maximum_absolute_vout_error_vs_exact_1ns_ramp_v':max(abs(row[2]-exact(row[0])) for row in rows),'maximum_abs_kcl_error_a':max(abs(row[3]+(row[1]-row[2])/R) for row in rows)}
# Finer-timestep independent simulator run; values/connectivity checked above.
deck='''* Independent unloaded RC verification, zero-volt initial operating point
R1 IN OUT 10k
C1 OUT 0 100n
VSTEP IN 0 PWL(0 0 1n 1)
.options reltol=1e-8 abstol=1e-14 vntol=1e-10
.tran 0.1u 5m 0 0.1u
.meas tran vout_1ms FIND v(OUT) AT=1m
.meas tran vout_5ms FIND v(OUT) AT=5m
.end
'''
(D/'independent.cir').write_text(deck)
p=run(['ngspice','-n','-b','-o',str(D/'independent.log'),str(D/'independent.cir')],'simulation')
assert p.returncode==0
log=(D/'independent.log').read_text()
measurements={k:float(v) for k,v in re.findall(r'(vout_\dms)\s*=\s*([\deE+.\-]+)',log)}
assert 0.62<=measurements['vout_1ms']<=0.65
hashes={str(p).replace('/candidate/','inputs/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in [pathlib.Path('/candidate/requirements/fixture-requirements.md'),pathlib.Path('/candidate/project/rc_filter.kicad_sch'),pathlib.Path('/candidate/project/rc_filter.kicad_pcb')]}
result={'versions':versions,'candidate_sha256':hashes,'native_instances':instances,'native_board_pin_nets':board_pads,'fresh_netlist_matches_supplied':True,'nets':nets_fresh,'track_segments':tracks,'tau_s':tau,'ideal_step_vout_1ms':1-math.exp(-.001/tau),'ideal_step_vout_5ms':1-math.exp(-.005/tau),'finite_1ns_rise_vout_1ms':exact(.001),'retained_raw_analysis':raw_results,'independent_measurements':measurements,'commands':commands,'assumptions':['Ideal nominal R and C, ideal 0-to-1 V source, no external load, initially settled at 0 V.','Generic passive symbols only: no manufacturer model or rating is assumed.','Finite 1 ns source rise is compared against the exact ramp response and the required ideal step.'],'limitations':['No candidate modification or zone refill.','Not hardware, tolerance, temperature, loading, ADC, EMI or parasitic verification.','Native ERC/DRC records inspected separately; not rerun by this script.']}
(D/'verification.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:result[k] for k in ['fresh_netlist_matches_supplied','nets','tau_s','ideal_step_vout_1ms','ideal_step_vout_5ms','retained_raw_analysis','independent_measurements']},indent=2))
