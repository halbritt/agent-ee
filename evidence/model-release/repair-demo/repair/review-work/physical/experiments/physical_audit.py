import json, re, pathlib, math, hashlib, struct, sys
import pcbnew

ROOT = pathlib.Path('/work/experiments')
SOURCE = pathlib.Path('/candidate/project/rc_filter.kicad_pcb')

def parse(s):
    tokens = re.findall(r'\(|\)|"(?:\\.|[^"\\])*"|[^\s()]+', s)
    stack=[]
    for t in tokens:
        if t=='(':
            a=[]
            if stack: stack[-1].append(a)
            stack.append(a)
        elif t==')':
            root=stack.pop()
        else: stack[-1].append(json.loads(t) if t.startswith('"') else t)
    return root

def children(a,k): return [x for x in a if isinstance(x,list) and x[0]==k]
def child(a,k): return children(a,k)[0]
def xy(a,k): return [float(x) for x in child(a,k)[1:3]]
def uuid(a): return child(a,'uuid')[1]
def inside(x,y,pts):
    result=False
    for a,b in zip(pts,pts[1:]+pts[:1]):
        if (a[1]>y)!=(b[1]>y) and x<(b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0]: result=not result
    return result

tree=parse(SOURCE.read_text())
board=pcbnew.LoadBoard(str(SOURCE))
conn=board.GetConnectivity()
conn.Build(board)
pads=[]
for f in children(tree,'footprint'):
    props={p[1]:p[2] for p in children(f,'property')}
    origin=xy(f,'at')
    for p in children(f,'pad'):
        pos=[a+b for a,b in zip(origin,xy(p,'at'))]
        pads.append({'ref':props['Reference'],'pin':p[1],'net':child(p,'net')[1], 'xy_mm':pos,'size_mm':xy(p,'size'),'uuid':uuid(p),'value':props['Value']})
tracks=[{'start':xy(t,'start'),'end':xy(t,'end'),'width_mm':float(child(t,'width')[1]),'net':child(t,'net')[1],'uuid':uuid(t)} for t in children(tree,'segment')]
z=child(tree,'zone')
poly=[list(map(float,x[1:3])) for x in child(child(z,'filled_polygon'),'pts')[1:]]
samples=[]
for deg in [45,135,225,315]:
    for r in [.7,1.,1.2,1.4]:
        a=math.radians(deg)
        x,y=100+r*math.cos(a),105.08+r*math.sin(a)
        samples.append({'angle_deg':deg,'radius_mm':r,'xy_mm':[x,y],'inside_saved_copper_polygon':inside(x,y,poly)})
route=[[100+.7/math.sqrt(2),105.08-.7/math.sqrt(2)],[102,103.08],[113,100]]
route_checks=[]
for a,b in zip(route,route[1:]):
    route_checks.append({'start':a,'end':b,'samples':1001,'all_inside':all(inside(a[0]+(b[0]-a[0])*t/1000,a[1]+(b[1]-a[1])*t/1000,poly) for t in range(1001))})
contacts=[]
for p in board.GetPads():
    contacts.append({'pad':p.GetParentFootprint().GetReference()+'.'+p.GetNumber(), 'directly_connected_items':[{'uuid':str(x.m_Uuid.AsString()),'type':x.GetClass(),'net':x.GetNetname()} for x in conn.GetConnectedItems(p)]})
expected=json.loads(pathlib.Path('/evidence/work/netlist/connectivity.json').read_text())['nets']
actual={n:sorted(p['ref']+'.'+p['pin'] for p in pads if p['net']==n) for n in expected}
result={'method':'Independent native S-expression parsing; saved polygon even-odd point tests; pcbnew connectivity corroboration. No zone refill or candidate writes.', 'versions':{'python':sys.version,'pcbnew':pcbnew.Version()}, 'board_sha256':hashlib.sha256(SOURCE.read_bytes()).hexdigest(),'pads':pads,'tracks':tracks,'native_pad_net_sets':actual,'matches_schematic_netlist':actual=={k:sorted(v) for k,v in expected.items()}, 'all_native_tracks_ge_0_20mm':all(t['width_mm']>=.2 for t in tracks), 'via':{'at':xy(child(tree,'via'),'at'),'diameter_mm':float(child(child(tree,'via'),'size')[1]),'drill_mm':float(child(child(tree,'via'),'drill')[1])}, 'saved_zone_polygon':poly,'saved_zone_J1_3_thermal_samples':samples,'saved_zone_return_route':route_checks,'pad_connectivity':contacts,'notes':['Thermal and return samples corroborate continuity, but finite samples are not an exhaustive polygon proof; native DRC supplies complementary coverage.','No ampacity or thermal qualification is inferred.','No rotations exist on these footprint placements; parser adds local offsets directly.']}
(ROOT/'physical-audit.json').write_text(json.dumps(result,indent=2)+'\n')

raw=pathlib.Path('/evidence/work/rc-step/result.raw').read_bytes()
header,data=raw.split(b'Binary:\n',1)
text=header.decode()
n=int(re.search(r'No. Points:\s*(\d+)',text)[1]); nv=int(re.search(r'No. Variables:\s*(\d+)',text)[1])
assert nv==4 and len(data)==n*nv*8
rows=list(struct.iter_unpack('<dddd',data))
tau=10000*100e-9
def interp(t,k):
    for a,b in zip(rows,rows[1:]):
        if a[0]<=t<=b[0]: return a[k]+(b[k]-a[k])*(t-a[0])/(b[0]-a[0])
def ideal(t): return -math.expm1(-t/tau)
errors=[abs(r[2]-ideal(r[0])) for r in rows]
analysis={'method':'Parse original binary raw data independently, little-endian four doubles per point; linear interpolation; compare to Vout=1-exp(-t/(R*C)).','header':text,'R_ohm':10000,'C_F':100e-9,'tau_s':tau,'declared_at_1ms_bounds_V':[.62,.65], 'points':n,'time_start_stop_s':[rows[0][0],rows[-1][0]],'measurements':{str(t):{'vin_V':interp(t,1),'vout_V':interp(t,2),'analytic_V':ideal(t),'error_V':interp(t,2)-ideal(t)} for t in [.001,.005]},'max_abs_error_vs_instantaneous_step_V':max(errors),'stimulus':'Provided deck uses PWL(0 0 1n 1), finite 1 ns rise; C starts at 0 V. Ratio rise/tau=1e-6.','peak_abs_source_current_A':max(abs(r[3]) for r in rows),'ideal_peak_R_power_W':1/10000,'ideal_C_final_energy_J':.5*100e-9,'limitations':['Ideal unloaded values only. No PCB parasitics or fabricated assembly measurements.','5 ms measurement has no acceptance bound; informative only.','Current/power calculations are specific to the 1 V step; no component or copper thermal qualification.']}
(ROOT/'response-audit.json').write_text(json.dumps(analysis,indent=2)+'\n')
print(json.dumps({'track_count':len(tracks),'tracks_pass':result['all_native_tracks_ge_0_20mm'],'netlist_matches':result['matches_schematic_netlist'],'thermal_samples_all_inside':all(s['inside_saved_copper_polygon'] for s in samples),'return_route':route_checks,'response':analysis},indent=2))
