#!/usr/bin/python3
"""Read-only native PCB/response audit. Run: /usr/bin/python3 /work/experiments/review.py"""
from pathlib import Path
import re, json, math, sys, subprocess, hashlib, struct, os
W=Path('/work/experiments'); C=Path('/candidate/project')
commands=[]
def run(argv, name):
    env=os.environ.copy(); env.update(KICAD_CONFIG_HOME=str(W/'kicad-config'), KICAD10_SYMBOL_DIR='/candidate/stock/symbols',KICAD10_FOOTPRINT_DIR='/candidate/stock/footprints')
    p=subprocess.run(argv, capture_output=True, text=True,env=env,cwd=W)
    (W/(name+'.log')).write_text(p.stdout+p.stderr)
    commands.append({'argv':argv,'exit_code':p.returncode,'output':name+'.log'})
    return p
hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in C.iterdir() if p.is_file()}
run(['kicad-cli','version'],'kicad-version'); run(['ngspice','--version'],'ngspice-version')
run(['kicad-cli','pcb','drc','--format','json','--severity-all','--schematic-parity','--units','mm','-o',str(W/'drc.json'),str(C/'rc_filter.kicad_pcb')],'drc')
run(['kicad-cli','sch','export','netlist','--format','kicadxml','-o',str(W/'design.xml'),str(C/'rc_filter.kicad_sch')],'netlist')
# Independent, minimal S-expression parser preserves native data without relying on geometry_probe.
def parse(text):
    toks=re.findall(r'"(?:\\.|[^"\\])*"|[^\s()]+|[()]',text); stack=[]; root=None
    for t in toks:
        if t=='(':
            n=[]
            if stack: stack[-1].append(n)
            stack.append(n)
        elif t==')':
            n=stack.pop()
            if not stack: root=n
        else: stack[-1].append(json.loads(t) if t.startswith('"') else t)
    assert not stack
    return root
def children(n,k):return [a for a in n if isinstance(a,list) and a and a[0]==k]
def child(n,k):return children(n,k)[0]
def pt(n,k):return list(map(float,child(n,k)[1:3]))
b=parse((C/'rc_filter.kicad_pcb').read_text())
segments=[{'start':pt(s,'start'),'end':pt(s,'end'),'width_mm':float(child(s,'width')[1]),'net':child(s,'net')[1],'layer':child(s,'layer')[1],'uuid':child(s,'uuid')[1]} for s in children(b,'segment')]
assert len(segments)==5 and all(s['width_mm']>=.2 for s in segments)
pads=[]; footprints=[]
for f in children(b,'footprint'):
    props={p[1]:p[2] for p in children(f,'property')}; origin=pt(f,'at'); angle=math.radians(float(child(f,'at')[3]) if len(child(f,'at'))>3 else 0)
    assert angle==0 # fixture-only transform
    footprints.append({'ref':props['Reference'],'value':props['Value'],'position':origin})
    for p in children(f,'pad'):
        pos=[sum(v) for v in zip(origin,pt(p,'at'))]
        pads.append({'ref':props['Reference'],'pad':p[1],'net':child(p,'net')[1],'position_mm':pos,'size_mm':pt(p,'size'),'kind':p[2]})
# Inspect saved zone membership with native pcbnew. No fill or save call.
import pcbnew
board=pcbnew.LoadBoard(str(C/'rc_filter.kicad_pcb')); zone=list(board.Zones())[0]; polys=zone.GetFilledPolysList(pcbnew.B_Cu)
def contains(x,y):return bool(polys.Contains(pcbnew.VECTOR2I(round(x*1e6),round(y*1e6))))
# Continuous route candidates sampled <=0.01 mm, ending inside the J1.3 annular ring.
route=[(113,100),(103,100),(103,102.08),(100.55,104.53)]
fail=[]; count=0
for a,z in zip(route,route[1:]):
    steps=math.ceil(math.dist(a,z)/.01)
    for i in range(steps+1):
        x=a[0]+(z[0]-a[0])*i/steps; y=a[1]+(z[1]-a[1])*i/steps
        count+=1
        if not contains(x,y): fail.append([x,y])
# J1.3 circular copper radius=.85, drill radius=.5; end radius=.7778 lies within copper.
zone_report={'uuid':zone.m_Uuid.AsString(),'filled_outline_count':polys.OutlineCount(),'sample_route_mm':route,'route_samples':count,'samples_outside_saved_zone':fail,'route_end_radius_from_J1_3_mm':math.dist(route[-1],(100,105.08)),'J1_3_annulus_radii_mm':[.5,.85],'via_center_in_saved_fill':contains(113,100),'limitations':'Point sampling is supplementary; DRC supplies native saved-fill connectivity. Not a return impedance or manufacturing qualification.'}
# Decode actual simulator waveform rather than trusting execution exit or textual summary.
raw=Path('/evidence/work/rc-step/result.raw').read_bytes(); header,payload=raw.split(b'Binary:\n',1); h=header.decode(); nv=int(re.search(r'No. Variables:\s+(\d+)',h)[1]); np=int(re.search(r'No. Points:\s+(\d+)',h)[1]); assert nv==4
rows=list(struct.iter_unpack('<'+'d'*nv,payload));assert len(rows)==np
# Exact response for a linear 0->1 ramp of duration tr followed by constant 1 V.
R=10000.;cap=100e-9;tau=R*cap;tr=1e-9
def exact(t):
    if t<=tr:return t/tr-tau/tr*(-math.expm1(-t/tau))
    return 1-(tau/tr)*(-math.expm1(-tr/tau))*math.exp(-(t-tr)/tau)
def interp(at):
    for a,z in zip(rows,rows[1:]):
        if a[0]<=at<=z[0]:return a[2]+(z[2]-a[2])*(at-a[0])/(z[0]-a[0])
wave={'header':h,'tau_s':tau,'ideal_instant_step_1ms_V':1-math.exp(-.001/tau),'finite_1ns_ramp_exact_1ms_V':exact(.001),'raw_interpolated_1ms_V':interp(.001),'raw_final_time_s':rows[-1][0],'raw_final_output_V':rows[-1][2],'exact_5ms_V':exact(.005),'maximum_sample_abs_error_V':max(abs(r[2]-exact(r[0])) for r in rows),'v_in_min_max_V':[min(r[1] for r in rows),max(r[1] for r in rows)],'initial_row':rows[0],'scope':'Ideal unloaded response only; values cross-checked to native schematic and XML. Five-ms measurement has no declared acceptance bound.'}
result={'python_version':sys.version,'pcbnew_version':pcbnew.Version(),'segments':segments,'pads':pads,'footprints':footprints,'zone':zone_report,'waveform':wave,'input_hashes_before':hashes,'candidate_files_unchanged':all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==v for p,v in hashes.items()),'commands':commands}
(W/'audit.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
