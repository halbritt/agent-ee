#!/usr/bin/env python3
"""Read-only review of native fixture; all generated files stay here."""
from pathlib import Path
import subprocess, os, sys, json, hashlib, re, xml.etree.ElementTree as ET, struct, math
P = Path('/work/experiments')
records=[]
env=os.environ.copy()
env.update(KICAD_CONFIG_HOME=str(P/'kicad-config'), KICAD10_SYMBOL_DIR='/candidate/stock/symbols', KICAD10_FOOTPRINT_DIR='/candidate/stock/footprints', KICAD10_TEMPLATE_DIR='/candidate/stock/template')
def run(name,argv):
    r=subprocess.run(argv,cwd=P,env=env,capture_output=True,text=True)
    (P/(name+'.stdout')).write_text(r.stdout)
    (P/(name+'.stderr')).write_text(r.stderr)
    records.append(dict(name=name,argv=argv,cwd=str(P),exit_code=r.returncode,stdout=name+'.stdout',stderr=name+'.stderr'))
    (P/'commands.json').write_text(json.dumps(dict(python=sys.version,environment_overrides={k:env[k] for k in ['KICAD_CONFIG_HOME','KICAD10_SYMBOL_DIR','KICAD10_FOOTPRINT_DIR','KICAD10_TEMPLATE_DIR']},commands=records),indent=2))
    return r
for name,args in [('kicad-version',['kicad-cli','version']),('ngspice-version',['ngspice','--version']),('python-version',['python3','--version'])]: run(name,args)
files=['requirements/fixture-requirements.md','project/rc_filter.kicad_sch','project/rc_filter.kicad_pcb','project/rc_filter.kicad_pro','project/sym-lib-table','project/fp-lib-table']
original={f:hashlib.sha256((Path('/candidate')/f).read_bytes()).hexdigest() for f in files}
# Preserve line-numbered native excerpts and the complete small native project configuration.
with (P/'native-inspection.txt').open('w') as out:
    for f in files:
        txt=(Path('/candidate')/f).read_text()
        out.write('\n### inputs/'+f+'\n')
        if f.endswith('.kicad_sch'):
            # Library definitions above line ~1000 and all placed instances inspected independently.
            for n,line in enumerate(txt.splitlines(),1):
                if n >= 990: out.write(f'{n}: {line}\n')
        elif f.endswith('.kicad_pcb'):
            for n,line in enumerate(txt.splitlines(),1): out.write(f'{n}: {line}\n')
        else:
            for n,line in enumerate(txt.splitlines(),1): out.write(f'{n}: {line}\n')
for name,args in [
('netlist',['kicad-cli','sch','export','netlist','--format','kicadxml','-o',str(P/'native-export.xml'),'/candidate/project/rc_filter.kicad_sch']),
('erc',['kicad-cli','sch','erc','--format','json','--severity-all','--units','mm','-o',str(P/'erc.json'),'/candidate/project/rc_filter.kicad_sch']),
('drc',['kicad-cli','pcb','drc','--format','json','--severity-all','--schematic-parity','--units','mm','-o',str(P/'drc.json'),'/candidate/project/rc_filter.kicad_pcb'])]:
    run(name,args)
# Parse native sexpressions without treating native prose as instructions.
def sexpr(path):
    tokens=re.findall(r'"(?:\\.|[^"\\])*"|[^\s()]+|[()]',path.read_text())
    stack=[]; root=None
    for tok in tokens:
        if tok=='(':
            a=[]
            if stack: stack[-1].append(a)
            stack.append(a)
        elif tok==')': root=stack.pop()
        else: stack[-1].append(json.loads(tok) if tok.startswith('"') else tok)
    return root
def children(x,key): return [a for a in x if isinstance(a,list) and a and a[0]==key]
def one(x,key): return children(x,key)[0]
board=sexpr(Path('/candidate/project/rc_filter.kicad_pcb'))
source=sexpr(Path('/candidate/project/rc_filter.kicad_sch'))
components=[]
for s in children(source,'symbol'):
    props={a[1]:a[2] for a in children(s,'property')}
    components.append(dict(ref=props['Reference'],value=props['Value'],lib_id=one(s,'lib_id')[1],at=one(s,'at')[1:],dnp=one(s,'dnp')[1],uuid=one(s,'uuid')[1]))
xml=ET.parse(P/'native-export.xml').getroot()
def nets_of(root): return {n.attrib['name']:sorted(f"{q.attrib['ref']}.{q.attrib['pin']}" for q in n.findall('node')) for n in root.findall('nets/net')}
nets=nets_of(xml)
prior=ET.parse('/evidence/work/netlist/design.xml').getroot()
board_nets={}; pads=[]
for f in children(board,'footprint'):
    props={a[1]:a[2] for a in children(f,'property')}; ref=props['Reference']; origin=one(f,'at')[1:]
    for p in children(f,'pad'):
        net=one(p,'net')[1]; board_nets.setdefault(net,[]).append(ref+'.'+p[1])
        pads.append(dict(ref=ref,pad=p[1],net=net,footprint=origin,local_at=one(p,'at')[1:],layers=one(p,'layers')[1:]))
board_nets={n:sorted(p) for n,p in board_nets.items()}
tracks=[dict(uuid=one(s,'uuid')[1],net=one(s,'net')[1],width_mm=float(one(s,'width')[1]),start=one(s,'start')[1:],end=one(s,'end')[1:]) for s in children(board,'segment')]
# Check saved ground polygon membership at the actual ground via and header position.
poly=one(one(children(board,'zone')[0],'filled_polygon'),'pts')[1:]
poly=[(float(p[1]),float(p[2])) for p in poly]
def inside(point):
    x,y=point; hit=False
    for a,b in zip(poly,poly[1:]+poly[:1]):
        if (a[1]>y)!=(b[1]>y) and x < (b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0]: hit=not hit
    return hit
geometry=dict(components=components,nets=nets,export_matches_supplied=nets==nets_of(prior),board_pad_nets=board_nets,schematic_board_pad_identity_matches=nets==board_nets,pads=pads,tracks=tracks,minimum_width_mm=min(s['width_mm'] for s in tracks),all_tracks_meet_0p2mm=all(s['width_mm']>=0.2 for s in tracks),saved_ground_fill_membership={str(p):inside(p) for p in [(113,100),(100,105.08),(100,104.48),(100,105.68),(99.4,105.08),(100.6,105.08)]})
(P/'identity-geometry.json').write_text(json.dumps(geometry,indent=2))
# R and C numerical values independently taken from fresh native XML export.
values={c.attrib['ref']:c.findtext('value') for c in xml.findall('components/comp')}
assert values['R1']=='10k' and values['C1']=='100n'
assert nets=={'/IN':['J1.1','R1.1'],'/OUT':['C1.1','J1.2','R1.2'],'GND':['C1.2','J1.3']}
R=10000.; C=100e-9; tau=R*C; rise=1e-9
(P/'independent.cir').write_text('* Independent native-value RC check; grounded, unloaded, zero initial DC\nR1 in out 10k\nC1 out 0 100n\nVSTEP in 0 PWL(0 0 1n 1)\n.options reltol=1e-8 abstol=1e-15 vntol=1e-10\n.tran 0.5u 5m 0 0.5u\n.meas tran vout_1ms find v(out) at=1m\n.meas tran vout_5ms find v(out) at=5m\n.control\nset filetype=ascii\nrun\nwrite independent.raw\nquit\n.endc\n.end\n')
run('independent-ngspice',['ngspice','-n','-b','-o',str(P/'independent-ngspice.log'),str(P/'independent.cir')])
# Audit supplied binary raw waveform, rather than relying on the tool exit code or .meas text.
raw=Path('/evidence/work/rc-step/result.raw').read_bytes(); cut=raw.index(b'Binary:\n')+len(b'Binary:\n'); header=raw[:cut].decode()
(P/'supplied-raw-header.txt').write_text(header)
nvar=int(re.search(r'No. Variables:\s+(\d+)',header)[1]); npt=int(re.search(r'No. Points:\s+(\d+)',header)[1])
variables=re.findall(r'^\s*\d+\s+(\S+)\s+(\S+)\s*$',header,re.M)
assert [v[0] for v in variables]==['time','v(in)','v(out)','i(vstep)']
assert len(raw)-cut == nvar*npt*8
rows=list(struct.iter_unpack('<'+'d'*nvar,raw[cut:]))
def analytic(t):
    if t<=rise: return t/rise + tau/rise*math.expm1(-t/tau)
    ya=1 + tau/rise*math.expm1(-rise/tau)
    return 1-(1-ya)*math.exp(-(t-rise)/tau)
def interp(rows,t,col=2):
    for a,b in zip(rows,rows[1:]):
        if a[0]<=t<=b[0]: return a[col]+(b[col]-a[col])*(t-a[0])/(b[0]-a[0])
    raise ValueError(t)
summary=dict(assumptions=['Ideal 1 V source; no external load; populated R1=10k and C1=100n; capacitor initially uncharged as supported by initial DC solution.','Original stimulus is a 1 ns linear ramp; its difference from an instantaneous step is quantified.','No part-specific ratings or tolerance assumptions.'],R_ohm=R,C_farad=C,tau_seconds=tau,supplied_raw=dict(points=npt,variables=variables,first_point=rows[0],last_point=rows[-1],time_strictly_increasing=all(b[0]>a[0] for a,b in zip(rows,rows[1:])),maximum_output_error_vs_exact_ramp_V=max(abs(r[2]-analytic(r[0])) for r in rows)),measurements={})
for t in [.001,.005]:
    v=interp(rows,t)
    summary['measurements'][str(t)]=dict(raw_interpolated_V=v,ideal_instant_step_V=1-math.exp(-t/tau),exact_1ns_ramp_V=analytic(t),within_declared_1ms_limits=(.62<=v<=.65) if t==.001 else None)
summary['hashes_before']=original
summary['candidate_unchanged']=all(hashlib.sha256((Path('/candidate')/f).read_bytes()).hexdigest()==h for f,h in original.items())
(P/'response-audit.json').write_text(json.dumps(summary,indent=2))
print(json.dumps(dict(identity_matches=geometry['schematic_board_pad_identity_matches'],min_width=geometry['minimum_width_mm'],measurements=summary['measurements'],raw_max_error=summary['supplied_raw']['maximum_output_error_vs_exact_ramp_V'],candidate_unchanged=summary['candidate_unchanged']),indent=2))
