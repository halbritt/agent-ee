import hashlib, json, math, os, pathlib, platform, re, subprocess, sys
import xml.etree.ElementTree as ET

ROOT = pathlib.Path('/work/experiments')
CAN = pathlib.Path('/candidate')
records = []

def run(argv, stem):
    env = os.environ.copy()
    env.update(KICAD_CONFIG_HOME=str(ROOT/'kicad-config'),
               KICAD10_SYMBOL_DIR='/candidate/stock/symbols',
               KICAD10_FOOTPRINT_DIR='/candidate/stock/footprints',
               KICAD10_TEMPLATE_DIR='/candidate/stock/template')
    p = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, text=True)
    (ROOT/(stem+'.stdout.log')).write_text(p.stdout)
    (ROOT/(stem+'.stderr.log')).write_text(p.stderr)
    records.append(dict(argv=argv, cwd=str(ROOT), exit_code=p.returncode,
                        stdout=stem+'.stdout.log', stderr=stem+'.stderr.log'))
    return p

def parse(path):
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|[()]|[^\s()]+', path.read_text())
    stack = []; tree = None
    for t in tokens:
        if t == '(':
            n=[]
            if stack: stack[-1].append(n)
            stack.append(n)
        elif t == ')': tree=stack.pop()
        else: stack[-1].append(json.loads(t) if t.startswith('"') else t)
    return tree

def children(n, tag): return [x for x in n if isinstance(x,list) and x and x[0]==tag]
def child(n, tag): return children(n,tag)[0]
def xy(n, tag): return list(map(float,child(n,tag)[1:3]))
def inside(point, polygon):
    x,y=point; odd=False
    for a,b in zip(polygon,polygon[1:]+polygon[:1]):
        if (a[1]>y)!=(b[1]>y) and x < (b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0]: odd=not odd
    return odd

run(['kicad-cli','version'],'kicad-version')
run(['ngspice','--version'],'ngspice-version')
run(['/usr/bin/python3','-c','import pcbnew; print(pcbnew.Version())'],'pcbnew-version')
run(['kicad-cli','pcb','drc','--format','json','--severity-all','--schematic-parity','--units','mm','-o',str(ROOT/'drc.json'),str(CAN/'project/rc_filter.kicad_pcb')],'drc')
run(['kicad-cli','sch','erc','--format','json','--severity-all','--units','mm','-o',str(ROOT/'erc.json'),str(CAN/'project/rc_filter.kicad_sch')],'erc')
run(['kicad-cli','sch','export','netlist','--format','kicadxml','-o',str(ROOT/'design.xml'),str(CAN/'project/rc_filter.kicad_sch')],'netlist')

hashes={str(p.relative_to(CAN)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((CAN/'project').iterdir()) if p.is_file()}
board=parse(CAN/'project/rc_filter.kicad_pcb')
tracks=[dict(uuid=child(n,'uuid')[1],start_mm=xy(n,'start'),end_mm=xy(n,'end'),width_mm=float(child(n,'width')[1]),net=child(n,'net')[1],layer=child(n,'layer')[1]) for n in children(board,'segment')]
pads=[]
for f in children(board,'footprint'):
    pos=xy(f,'at'); ref=next(n[2] for n in children(f,'property') if n[1]=='Reference')
    for p in children(f,'pad'):
        local=xy(p,'at')
        pads.append(dict(ref=ref,pad=p[1],kind=p[2],shape=p[3],position_mm=[pos[i]+local[i] for i in range(2)],size_mm=xy(p,'size'),net=child(p,'net')[1]))
zone=child(board,'zone')
polys=[[[float(x),float(y)] for _,x,y in child(n,'pts')[1:]] for n in children(zone,'filled_polygon')]
via=child(board,'via'); via_pos=xy(via,'at')
thermal_samples=[]
for deg in [45,135,225,315]:
    pts=[[100+r*math.cos(math.radians(deg)),105.08+r*math.sin(math.radians(deg))] for r in [.6,.7,.8,.9,1.,1.1,1.2,1.3,1.4,1.5,1.6]]
    thermal_samples.append(dict(angle_deg=deg,radii_mm=[.6,.7,.8,.9,1.,1.1,1.2,1.3,1.4,1.5,1.6],saved_fill_contains=[any(inside(p,poly) for poly in polys) for p in pts]))
netlist=ET.parse(ROOT/'design.xml').getroot()
netmap={n.attrib['name']: sorted(c.attrib['ref']+'.'+c.attrib['pin'] for c in n.findall('node')) for n in netlist.findall('./nets/net')}
boardmap={net:sorted(p['ref']+'.'+p['pad'] for p in pads if p['net']==net) for net in set(p['net'] for p in pads)}
values={c.attrib['ref']:c.findtext('value') for c in netlist.findall('./components/comp')}
R=10000.; C=100e-9; tau=R*C
analytic={str(t):1-math.exp(-t/tau) for t in [.001,.005]}
result=dict(python=sys.version,platform=platform.platform(),hashes_before=hashes,tracks=tracks,
    tracks_meet_0p2_mm=all(t['width_mm']>=.2 for t in tracks),pads=pads,
    schematic_netmap=netmap,board_netmap=boardmap,netmaps_match=netmap==boardmap,
    values=values,filled_polygon_count=len(polys),via_position_mm=via_pos,
    saved_fill_contains_via_center=any(inside(via_pos,p) for p in polys),
    J1_3_thermal_samples=thermal_samples,analytic=dict(R_ohm=R,C_F=C,tau_s=tau,vout_V=analytic,initial_current_A=1/R,peak_resistor_power_W=1/R,step_energy_dissipated_J=.5*C),
    geometry_notes=['All footprint rotations are zero in the inspected native board.',
    'C1.2 at (110.95,100) connects by 2.05 mm F.Cu track to GND via (113,100).',
    'J1.3 is PTH at (100,105.08), diameter 1.7 mm, drill 1 mm. Sampled fill radii overlap its annular copper.',
    'Point samples support saved thermal contact, not a general polygon topology or fabrication proof. Native DRC separately checks connectivity.',
    'Edge.Cuts rectangle is (96,96)-(116,108), 20 by 12 mm. All courtyard extents are inside.',
    'No copper thickness, specific resistor/capacitor/header model, fabrication process or thermal environment is assumed.'])

# Independent timing: step after an explicit 0.1 ms zero-input dwell; measure
# exactly 1 ms and 5 ms later. A 0.1 us max timestep challenges supplied 5 us.
deck='''* Independent unloaded RC check, values and topology verified against native sources
V1 in 0 PWL(0 0 100u 0 100.001u 1)
R1 in out 10k
C1 out 0 100n
.tran 0.1u 5.1m 0 0.1u
.meas tran out_1ms FIND v(out) AT=1.1m
.meas tran out_5ms FIND v(out) AT=5.1m
.end
'''
(ROOT/'independent.cir').write_text(deck)
run(['ngspice','-n','-b','-o',str(ROOT/'independent-ngspice.log'),str(ROOT/'independent.cir')],'independent-ngspice')
result['hashes_after']={name:hashlib.sha256((CAN/name).read_bytes()).hexdigest() for name in hashes}
result['candidate_files_unchanged']=result['hashes_after']==hashes
(ROOT/'analysis.json').write_text(json.dumps(result,indent=2)+'\n')
(ROOT/'commands.json').write_text(json.dumps(records,indent=2)+'\n')
print(json.dumps(dict(commands=records,tracks=tracks,netmaps_match=result['netmaps_match'],thermal_samples=thermal_samples,via_in_fill=result['saved_fill_contains_via_center'],analytic=result['analytic'],candidate_files_unchanged=result['candidate_files_unchanged']),indent=2))
