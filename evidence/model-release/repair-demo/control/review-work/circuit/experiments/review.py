from pathlib import Path
import subprocess, os, json, hashlib, math, re, struct, sys, platform
import xml.etree.ElementTree as ET
P=Path('/work/experiments')
commands=[]
env=os.environ.copy()
env.update(KICAD_CONFIG_HOME=str(P/'kicad-config'), KICAD10_SYMBOL_DIR='/candidate/stock/symbols', KICAD10_FOOTPRINT_DIR='/candidate/stock/footprints', KICAD10_TEMPLATE_DIR='/candidate/stock/template')
def run(name,args):
    r=subprocess.run(args,cwd=P,env=env,capture_output=True,text=True)
    (P/(name+'.stdout')).write_text(r.stdout)
    (P/(name+'.stderr')).write_text(r.stderr)
    commands.append(dict(name=name,argv=args,cwd=str(P),returncode=r.returncode,stdout=name+'.stdout',stderr=name+'.stderr'))
    (P/'commands.json').write_text(json.dumps({'python':sys.version,'platform':platform.platform(),'environment_overrides':{k:env[k] for k in ['KICAD_CONFIG_HOME','KICAD10_SYMBOL_DIR','KICAD10_FOOTPRINT_DIR','KICAD10_TEMPLATE_DIR']},'commands':commands},indent=2))
    return r
paths=[Path('/candidate/project/rc_filter.kicad_sch'),Path('/candidate/project/rc_filter.kicad_pcb'),Path('/candidate/project/rc_filter.kicad_pro'),Path('/candidate/requirements/fixture-requirements.md')]
def hashes(): return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
before=hashes()
run('kicad-version',['kicad-cli','version'])
run('ngspice-version',['ngspice','--version'])
run('netlist',['kicad-cli','sch','export','netlist','--format','kicadxml','-o',str(P/'native.xml'),str(paths[0])])
run('erc',['kicad-cli','sch','erc','--format','json','--severity-all','--units','mm','-o',str(P/'erc.json'),str(paths[0])])
run('drc',['kicad-cli','pcb','drc','--format','json','--severity-all','--schematic-parity','--units','mm','-o',str(P/'drc.json'),str(paths[1])])
# Parse native s-expressions independently of geometry-probe, without changing files.
def sexpr(path):
    tokens=re.findall(r'"(?:\\.|[^"\\])*"|[()]|[^\s()]+',path.read_text())
    stack=[]; root=None
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

def kids(n,k): return [x for x in n if isinstance(x,list) and x and x[0]==k]
def one(n,k): return kids(n,k)[0]
def props(n): return {x[1]:x[2] for x in kids(n,'property')}
sch=sexpr(paths[0]); pcb=sexpr(paths[1])
components=[]
for s in kids(sch,'symbol'):
    components.append({'lib_id':one(s,'lib_id')[1],'uuid':one(s,'uuid')[1],'properties':props(s),'at':one(s,'at')[1:],'dnp':one(s,'dnp')[1]})
segments=[{'uuid':one(s,'uuid')[1],'net':one(s,'net')[1],'start_mm':one(s,'start')[1:],'end_mm':one(s,'end')[1:],'width_mm':float(one(s,'width')[1])} for s in kids(pcb,'segment')+kids(pcb,'arc')]
pads={}
for fp in kids(pcb,'footprint'):
    ref=props(fp)['Reference']
    pads[ref]={p[1]:one(p,'net')[1] for p in kids(fp,'pad')}
def xml_summary(path):
    tree=ET.parse(path)
    return {'components':{c.attrib['ref']:{'value':c.findtext('value'),'libsource':c.find('libsource').attrib,'footprint':c.findtext('footprint')} for c in tree.findall('./components/comp')},'nets':{n.attrib['name']:sorted(x.attrib['ref']+'.'+x.attrib['pin'] for x in n.findall('node')) for n in tree.findall('./nets/net')}}
fresh=xml_summary(P/'native.xml'); supplied=xml_summary(Path('/evidence/work/netlist/design.xml'))
assert fresh==supplied
pcb_nets={}
for ref,ps in pads.items():
    for pin,net in ps.items(): pcb_nets.setdefault(net,[]).append(ref+'.'+pin)
pcb_nets={k:sorted(v) for k,v in pcb_nets.items()}
assert pcb_nets==fresh['nets']
R=float(fresh['components']['R1']['value'].removesuffix('k'))*1e3
C=float(fresh['components']['C1']['value'].removesuffix('n'))*1e-9
tau=R*C
exact={str(t):1-math.exp(-t/tau) for t in [.001,.005]}
# For a linear 0-to-1 rise over tr, t>=tr: y=1-exp(-t/tau)*expm1(tr/tau)/(tr/tau).
tr=1e-9
ramp={str(t):1-math.exp(-t/tau)*math.expm1(tr/tau)/(tr/tau) for t in [.001,.005]}
# Decode retained ngspice real binary waveform using its own variable and point counts.
raw=Path('/evidence/work/rc-step/result.raw').read_bytes()
head,body=raw.split(b'Binary:\n',1)
header=head.decode('ascii')
(P/'supplied-raw-header.txt').write_text(header)
nv=int(re.search(r'No. Variables:\s*(\d+)',header)[1]); np=int(re.search(r'No. Points:\s*(\d+)',header)[1])
assert 'Flags: real' in header and len(body)==np*nv*8
rows=list(struct.iter_unpack('='+('d'*nv),body))
assert all(rows[i+1][0]>rows[i][0] for i in range(len(rows)-1))
assert abs(rows[-1][0]-.005)<1e-14
variables=[line.split()[1] for line in header.split('Variables:\n')[1].splitlines() if line.strip()]
idx=variables.index('v(out)'); in_idx=variables.index('v(in)')
def interp(t):
    for a,b in zip(rows,rows[1:]):
        if a[0]<=t<=b[0]: return a[idx]+(b[idx]-a[idx])*(t-a[0])/(b[0]-a[0])
raw_meas={str(t):interp(t) for t in [.001,.005]}
# Independent numerical check with 1 ps rising edge and maximum timestep 0.1 us.
(P/'independent.cir').write_text(f'''* Independent native-value RC check; unloaded, initially discharged
R1 IN OUT {R:.17g}
C1 OUT 0 {C:.17g}
V1 IN 0 PWL(0 0 1p 1)
.tran 0.1u 5m 0 0.1u
.meas tran vout_1ms FIND v(OUT) AT=1m
.meas tran vout_5ms FIND v(OUT) AT=5m
.end
''')
run('independent-ngspice',['ngspice','-n','-b','-o',str(P/'independent.log'),str(P/'independent.cir')])
results={'native_hashes_before':before,'native_hashes_after':hashes(),'native_unchanged':before==hashes(),'schematic_components':components,'fresh_netlist':fresh,'fresh_matches_supplied':fresh==supplied,'pcb_pads':pads,'pcb_matches_netlist':pcb_nets==fresh['nets'],'segments':segments,'segment_count':len(segments),'minimum_width_mm':min(s['width_mm'] for s in segments),'geometry_pass':all(s['width_mm']>=.2 for s in segments),'tau_seconds':tau,'analytical_ideal_step_v':exact,'analytical_1ns_ramp_v':ramp,'ramp_vs_ideal_difference_v':{t:ramp[t]-exact[t] for t in exact},'supplied_waveform':{'variables':variables,'points':np,'first':rows[0],'last':rows[-1],'interpolated_measurements':raw_meas,'input_range': [min(r[in_idx] for r in rows),max(r[in_idx] for r in rows)],'maximum_error_from_ramp_after_edge':max(abs(r[idx]-(1-math.exp(-r[0]/tau)*math.expm1(tr/tau)/(tr/tau))) for r in rows if r[0]>=tr)},'assumptions':['Ideal linear passive R and C; 1 V input and no external load, as declared.','Initially discharged C, verified in original raw first row and log.','Generic symbols have no specified manufacturer ratings or parasitics; none invented.','Native ERC/DRC reruns do not refill zones or save candidate.','Binary raw decoded as host-native IEEE double real rows, validated by header, byte length, monotonic times and final time.']}
(P/'analysis.json').write_text(json.dumps(results,indent=2))
print(json.dumps({k:results[k] for k in ['native_unchanged','fresh_matches_supplied','pcb_matches_netlist','segment_count','minimum_width_mm','tau_seconds','analytical_ideal_step_v','ramp_vs_ideal_difference_v','supplied_waveform']},indent=2))
print((P/'independent.log').read_text())
