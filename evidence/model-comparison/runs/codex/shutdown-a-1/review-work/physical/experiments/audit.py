"""Read-only native RC audit; outputs confined to this experiment directory."""
from pathlib import Path
import re, json, math, hashlib, subprocess, os, sys
import xml.etree.ElementTree as ET

ROOT = Path('/work/experiments')
commands = []
def run(argv, name):
    p = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True,
                       env={**os.environ, 'KICAD_CONFIG_HOME': str(ROOT/'kicad-config')})
    (ROOT/(name+'.log')).write_text(p.stdout+p.stderr)
    commands.append({'argv': argv, 'cwd': str(ROOT), 'exit_code': p.returncode,
                     'log': name+'.log'})
    (ROOT/'commands.json').write_text(json.dumps(commands, indent=2)+'\n')
    return p

def parse(path):
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|[^\s()]+|[()]', path.read_text())
    stack=[]
    for t in tokens:
        if t == '(':
            item=[]
            if stack: stack[-1].append(item)
            stack.append(item)
        elif t == ')':
            result=stack.pop()
        else:
            stack[-1].append(json.loads(t) if t.startswith('"') else t)
    return result
def children(n,k): return [x for x in n if isinstance(x,list) and x[0]==k]
def child(n,k): return children(n,k)[0]
def xy(n): return tuple(map(float,n[1:3]))
def props(n): return {p[1]:p[2] for p in children(n,'property')}

pcbpath=Path('/candidate/project/rc_filter.kicad_pcb')
schpath=Path('/candidate/project/rc_filter.kicad_sch')
pcb,sch=parse(pcbpath),parse(schpath)
hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [pcbpath,schpath]}
components={props(s)['Reference']: props(s) for s in children(sch,'symbol')}
footprints={props(f)['Reference']:f for f in children(pcb,'footprint')}
values={r:components[r]['Value'] for r in ['R1','C1']}
assert values=={'R1':'10k','C1':'100n'}
assert all(props(footprints[r])['Value']==v for r,v in values.items())
netlist=ET.parse('/evidence/work/netlist/design.xml').getroot()
nets={n.attrib['name']:sorted(x.attrib['ref']+'.'+x.attrib['pin'] for x in n.findall('node')) for n in netlist.findall('./nets/net')}
pads={}
for ref,f in footprints.items():
    fx,fy=xy(child(f,'at'))
    assert len(child(f,'at'))==3, 'Only unrotated footprints supported'
    for p in children(f,'pad'):
        px,py=xy(child(p,'at'))
        pads[ref+'.'+p[1]]={'position':[fx+px,fy+py], 'net':child(p,'net')[1],
                            'size':xy(child(p,'size')), 'kind':p[2], 'shape':p[3]}
pcb_nets={n:sorted(r for r,p in pads.items() if p['net']==n) for n in nets}
assert pcb_nets==nets
tracks=[{'start':xy(child(t,'start')), 'end':xy(child(t,'end')),
         'width':float(child(t,'width')[1]), 'net':child(t,'net')[1]} for t in children(pcb,'segment')]

# Exact planar segment distance and even/odd polygon interior test, independent of pcbnew.
def point_dist(p,a,b):
    v=(b[0]-a[0],b[1]-a[1]); w=(p[0]-a[0],p[1]-a[1])
    d=v[0]**2+v[1]**2
    u=max(0,min(1,(v[0]*w[0]+v[1]*w[1])/d)) if d else 0
    return math.hypot(p[0]-a[0]-u*v[0],p[1]-a[1]-u*v[1])
def orient(a,b,c): return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
def segdist(a,b,c,d):
    if orient(a,b,c)*orient(a,b,d)<0 and orient(c,d,a)*orient(c,d,b)<0: return 0
    return min(point_dist(a,c,d),point_dist(b,c,d),point_dist(c,a,b),point_dist(d,a,b))
zone=child(pcb,'zone')
assert child(zone,'layer')[1]=='B.Cu' and child(zone,'net')[1]=='GND'
poly=[xy(p) for p in children(child(child(zone,'filled_polygon'),'pts'),'xy')]
edges=list(zip(poly,poly[1:]+poly[:1]))
def inside(p):
    x,y=p; hit=False
    for a,b in edges:
        if (a[1]>y)!=(b[1]>y) and x < (b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0]: hit=not hit
    return hit
# This witness traverses an existing J1.3 thermal spoke into the saved zone,
# then reaches the GND via. The first point is within the plated pad annulus.
route=[(100.55,105.63),(101.5,106.58),(113,106.58),(113,100)]
route_results=[]
for a,b in zip(route,route[1:]):
    margin=min(segdist(a,b,c,d) for c,d in edges)
    route_results.append({'start':a,'end':b,'inside_saved_fill':inside(a) and inside(b),
                          'minimum_distance_to_fill_boundary_mm':margin})
    assert inside(a) and inside(b) and margin>0
assert 0.5<math.dist(route[0],pads['J1.3']['position'])<0.85
assert xy(child(child(pcb,'via'),'at'))==route[-1]
assert child(child(pcb,'via'),'net')[1]=='GND'
assert any(t['net']=='GND' and t['start']==tuple(pads['C1.2']['position']) and t['end']==route[-1] for t in tracks)
# Other saved tracks contact pad centers or form the OUT tee at (107.5,100).
assert any(t['net']=='/IN' and t['start']==tuple(pads['J1.1']['position']) and t['end']==tuple(pads['R1.1']['position']) for t in tracks)
outmain=next(t for t in tracks if t['start']==tuple(pads['R1.2']['position']))
assert outmain['end']==tuple(pads['C1.1']['position'])
assert point_dist((107.5,100),outmain['start'],outmain['end'])==0

R,C=1e4,100e-9
calc={'R_ohm':R,'C_farad':C,'V_initial':1,'t_seconds':0.005,
      'tau_seconds':R*C,'V_OUT_5ms':math.exp(-0.005/(R*C)),
      'time_to_10mV_seconds':R*C*math.log(100),
      'initial_current_ampere':1/R,'initial_resistor_power_watt':1/R,
      'stored_energy_joule':C/2,
      'assumptions':['Ideal R and C; ideal zero volt clamp on /IN from t=0; no external output load; C1 OUT relative to GND initially +1 V.',
                     'Saved connected copper treated as ideal conductors; no parasitic or thermal qualification.']}
report={'hashes':hashes,'native_values':values,'netlist':nets,'native_pcb_pad_nets':pcb_nets,
        'pads':pads,'tracks':tracks,'saved_ground_return_witness':route_results,'calculation':calc}
(ROOT/'native_audit.json').write_text(json.dumps(report,indent=2)+'\n')

for argv,name in [(['kicad-cli','--version'],'kicad-version'),
                  (['ngspice','--version'],'ngspice-version'),
                  ([sys.executable,'--version'],'python-version'),
                  (['kicad-cli','pcb','drc','--format','json','--severity-all','--schematic-parity','--units','mm','-o',str(ROOT/'drc.json'),str(pcbpath)],'drc'),
                  (['kicad-cli','sch','erc','--format','json','--severity-all','--units','mm','-o',str(ROOT/'erc.json'),str(schpath)],'erc')]:
    run(argv,name)

(ROOT/'shutdown.cir').write_text('''Ideal shutdown from saved R1=10k and C1=100n; node names from native nets
VIN IN 0 0
R1 IN OUT 10k
C1 OUT 0 100n IC=1
.options reltol=1e-8 abstol=1e-14 vntol=1e-11
.tran 1u 6m 0 1u uic
.measure tran out_5ms FIND v(OUT) AT=5m
.measure tran out_initial FIND v(OUT) AT=1n
.end
''')
run(['ngspice','-b','shutdown.cir'],'shutdown')
print(json.dumps(report,indent=2))
