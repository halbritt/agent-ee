from pathlib import Path
import re,json,math,struct,xml.etree.ElementTree as ET
out=Path('/work/experiments')
def parse(path):
 tokens=re.findall(r'\(|\)|"(?:\\.|[^"\\])*"|[^\s()]+',Path(path).read_text())
 stack=[];root=None
 for t in tokens:
  if t=='(':
   a=[]
   if stack:stack[-1].append(a)
   else:root=a
   stack.append(a)
  elif t==')':stack.pop()
  else:stack[-1].append(json.loads(t) if t.startswith('"') else t)
 return root
def subs(a,k):return [x for x in a if isinstance(x,list) and x and x[0]==k]
def one(a,k):return subs(a,k)[0]
def pos(a,k):return list(map(float,one(a,k)[1:3]))
def distseg(p,a,b):
 d=[b[i]-a[i] for i in (0,1)];v=sum(x*x for x in d)
 t=max(0,min(1,sum((p[i]-a[i])*d[i] for i in (0,1))/v)) if v else 0
 return math.hypot(*[p[i]-a[i]-t*d[i] for i in (0,1)])
def inside(p,poly):
 hit=False;x,y=p
 for a,b in zip(poly,poly[1:]+poly[:1]):
  if (a[1]>y)!=(b[1]>y) and x<(b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0]:hit=not hit
 return hit
b=parse('/candidate/project/rc_filter.kicad_pcb')
pads=[];components=[]
for f in subs(b,'footprint'):
 ref=next(p[2] for p in subs(f,'property') if p[1]=='Reference');value=next(p[2] for p in subs(f,'property') if p[1]=='Value');origin=pos(f,'at')
 assert len(one(f,'at'))==3,'Script assumes fixture unrotated footprints'
 components.append({'ref':ref,'value':value,'position_mm':origin})
 for p in subs(f,'pad'):
  lp=pos(p,'at');pads.append({'id':ref+'.'+p[1],'position_mm':[origin[i]+lp[i] for i in (0,1)],'net':one(p,'net')[1],'size_mm':pos(p,'size'),'type':p[2],'shape':p[3],'uuid':one(p,'uuid')[1]})
tracks=[{'uuid':one(t,'uuid')[1],'net':one(t,'net')[1],'start_mm':pos(t,'start'),'end_mm':pos(t,'end'),'width_mm':float(one(t,'width')[1]),'layer':one(t,'layer')[1]} for t in subs(b,'segment')]
vias=[{'uuid':one(v,'uuid')[1],'net':one(v,'net')[1],'position_mm':pos(v,'at'),'diameter_mm':float(one(v,'size')[1]),'drill_mm':float(one(v,'drill')[1])} for v in subs(b,'via')]
z=one(b,'zone');poly=[list(map(float,p[1:])) for p in one(one(z,'filled_polygon'),'pts')[1:]]
# Sample an explicit ground path from the via through saved fill to J1.3's diagonal thermal spoke.
route=[[113,100],[113,106.5],[101.42,106.5],[100.45,105.53]]
route_samples=[]
for a,c in zip(route,route[1:]):
 for n in range(1001):
  p=[a[i]+(c[i]-a[i])*n/1000 for i in (0,1)]
  route_samples.append(inside(p,poly))
polar=[]
for angle in [0,45,90,135,180,225,270,315]:
 radii=[0.65,0.8,1.,1.2,1.4]
 polar.append({'angle_deg':angle,'radii_mm':radii,'inside_saved_fill':[inside([100+r*math.cos(math.radians(angle)),105.08+r*math.sin(math.radians(angle))],poly) for r in radii]})
xml=ET.parse(out/'design.xml').getroot()
nets={n.attrib['name']:sorted(nod.attrib['ref']+'.'+nod.attrib['pin'] for nod in n.findall('node')) for n in xml.findall('./nets/net')}
boardnets={net:sorted(p['id'] for p in pads if p['net']==net) for net in nets}
# Simple analytic step uses values inspected from both native files; no tolerance/model assumptions added.
values={c.findtext('value') for c in xml.findall('./components/comp')}
assert {'10k','100n'}.issubset(values)
tau=10000*100e-9
measurements={str(t):1-math.exp(-t/tau) for t in [.001,.005]}
# Independently inspect the provided binary raw vectors, rather than relying on exit status or measurement text.
data=Path('/evidence/work/rc-step/result.raw').read_bytes();header,payload=data.split(b'Binary:\n',1)
header=header.decode();nv=int(re.search(r'No. Variables:\s*(\d+)',header).group(1));np=int(re.search(r'No. Points:\s*(\d+)',header).group(1))
assert nv==4 and len(payload)==8*nv*np
rows=list(struct.iter_unpack('<'+'d'*nv,payload))
def interp(t,col):
 for a,c in zip(rows,rows[1:]):
  if a[0]<=t<=c[0]:return a[col]+(c[col]-a[col])*(t-a[0])/(c[0]-a[0])
 raise ValueError('time not bracketed')
rawmeasure={str(t):{'input_v':interp(t,1),'output_v':interp(t,2),'analytic_v':measurements[str(t)],'error_v':interp(t,2)-measurements[str(t)]} for t in [.001,.005]}
result={'commands':['python3 /work/experiments/inspect_native.py'],'assumptions':['Exact native pad coordinates; this fixture has no footprint rotation.','Point-in-polygon samples saved copper without refill; sample route does not establish minimum clearance or thermal qualification.','Raw binary uses four little-endian float64 vectors as checked against header/byte count.','Ideal initially discharged capacitor, ideal unloaded 1 V step; RC values from native export and PCB.'], 'components':components,'pads':pads,'tracks':tracks,'vias':vias,'minimum_track_mm':min(t['width_mm'] for t in tracks),'all_tracks_meet_0_20_mm':all(t['width_mm']>=.2 for t in tracks),'schematic_nets':nets,'board_nets':boardnets,'pad_net_parity':nets==boardnets,'saved_fill_vertices':len(poly),'saved_ground_path_mm':route,'ground_path_samples_in_fill':sum(route_samples),'ground_path_samples_total':len(route_samples),'thermal_polar_samples':polar,'tau_s':tau,'ideal_step_v':measurements,'peak_input_current_a':1/10000,'peak_resistor_power_w':1/10000,'capacitor_final_energy_j':.5*100e-9,'raw_header':header,'raw_measurements':rawmeasure,'limitations':['No PCB parasitic extraction, manufactured continuity measurement or thermal qualification.','All samples are geometric observations, not fabrication tolerance guarantees.']}
(out/'native_inspection.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
