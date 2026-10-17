from pathlib import Path
import re,json,hashlib,math,subprocess,struct,sys
root=Path('/work/experiments')
def parse(path):
    tokens=iter(re.findall(r'"(?:\\.|[^"\\])*"|[^\s()]+|[()]',Path(path).read_text()))
    def one(t):
        if t!='(': return json.loads(t) if t.startswith('"') else t
        out=[]
        for x in tokens:
            if x==')':return out
            out.append(one(x))
        raise ValueError('Unclosed list')
    return one(next(tokens))
def children(x,key):return [i for i in x if isinstance(i,list) and i and i[0]==key]
def prop(x,key):return next((i[2] for i in children(x,'property') if i[1]==key),None)
def child(x,key):return children(x,key)[0]
sch=parse('/candidate/project/rc_filter.kicad_sch'); pcb=parse('/candidate/project/rc_filter.kicad_pcb')
instances={prop(s,'Reference'):{'value':prop(s,'Value'),'lib':child(s,'lib_id')[1],'dnp':child(s,'dnp')[1]} for s in children(sch,'symbol')}
assert instances['R1']['value']=='10k' and instances['C1']['value']=='100n'
assert instances['R1']['dnp']==instances['C1']['dnp']=='no'
segments=[{'uuid':child(s,'uuid')[1],'width_mm':float(child(s,'width')[1]),'net':child(s,'net')[1]} for s in children(pcb,'segment')]
assert len(segments)==5 and all(s['width_mm']>=0.2 for s in segments)
pads={prop(f,'Reference')+'.'+p[1]:child(p,'net')[1] for f in children(pcb,'footprint') for p in children(f,'pad')}
checks={n:json.loads(Path('/evidence/checks/'+n+'.json').read_text()) for n in ['erc','drc','netlist','geometry','rc-step']}
for n,c in checks.items(): assert c['execution']=='ok' and c['outcome'] in ['pass','info']
net=json.loads(Path('/evidence/work/netlist/connectivity.json').read_text())
assert pads=={pin:name for name,pins in net['nets'].items() for pin in pins}
assert net['nets']=={'/IN':['J1.1','R1.1'],'/OUT':['C1.1','J1.2','R1.2'],'GND':['C1.2','J1.3']}
reports={n:json.loads(Path('/evidence/work/'+n+'/'+n+'.json').read_text()) for n in ['erc','drc']}
assert all(not s['violations'] for s in reports['erc']['sheets'])
assert all(not reports['drc'][k] for k in ['violations','unconnected_items','schematic_parity'])
commands=[]
def run(argv):
    p=subprocess.run(argv,capture_output=True,text=True)
    commands.append({'argv':argv,'exit_code':p.returncode,'stdout':p.stdout,'stderr':p.stderr})
    assert p.returncode==0
    return p.stdout
versions={'python':sys.version,'kicad':run(['kicad-cli','--version']).strip(),'ngspice':run(['ngspice','--version']).strip()}
deck=Path('/evidence/work/rc-step/deck.cir').read_text()
assert 'C1 OUT 0 100n' in deck and 'R1 IN OUT 10k' in deck and 'VSTEP IN 0 PWL(0 0 1n 1)' in deck
(root/'deck.cir').write_text(deck)
run(['ngspice','-n','-b','-o',str(root/'ngspice.log'),str(root/'deck.cir')])
log=(root/'ngspice.log').read_text()
meas={name:float(re.search(name+r'\s*=\s*([\deE+.-]+)',log)[1]) for name in ['vout_1ms','vout_5ms']}
assert 0.62<=meas['vout_1ms']<=0.65
raw=Path('/evidence/work/rc-step/result.raw').read_bytes(); header,binary=raw.split(b'Binary:\n',1)
num=int(re.search(rb'No. Points:\s*(\d+)',header)[1]); nv=int(re.search(rb'No. Variables:\s*(\d+)',header)[1])
assert nv==4 and len(binary)==num*nv*8
rows=list(struct.iter_unpack('<'+'d'*nv,binary))
# Closed form for the recorded finite ramp at 0..1 ns; tau = R*C.
tau=10000*100e-9; ramp=1e-9
def expected(t):return (t-tau*(-math.expm1(-t/tau)))/ramp if t<=ramp else 1-(tau/ramp)*(-math.expm1(-ramp/tau))*math.exp(-(t-ramp)/tau)
err=max(abs(row[2]-expected(row[0])) for row in rows)
assert err<1e-5
hashes={p:hashlib.sha256(Path('/candidate/'+p).read_bytes()).hexdigest() for p in ['requirements/fixture-requirements.md','project/rc_filter.kicad_sch','project/rc_filter.kicad_pcb']}
assert hashes['project/rc_filter.kicad_sch']=='ecc56a68a96b9297d8751ae7686a9c8818e9cac84faefafa364b06a0af8a342b'
assert hashes['project/rc_filter.kicad_pcb']=='f8264d95e52dc3c3d48358696c87294c6bbe7ff83102ecc9e0c4c3c27c7e72de'
result={'versions':versions,'commands':commands,'hashes':hashes,'native_instances':instances,'native_segments':segments,'native_pad_nets':pads,'netlist_nets':net['nets'],'check_outcomes':{n:{'execution':c['execution'],'outcome':c['outcome']} for n,c in checks.items()},'reports':reports,'tau_s':tau,'ideal_vout_1ms':1-math.exp(-.001/tau),'rerun_measurements':meas,'retained_raw':{'header':header.decode(),'points':num,'max_error_vs_finite_ramp_V':err},'finding_ids_supplied':[],'decision':'No finding IDs supplied; decisions array remains empty. No criterion failure established.'}
(root/'audit.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:result[k] for k in ['check_outcomes','tau_s','ideal_vout_1ms','rerun_measurements','decision']},indent=2))
