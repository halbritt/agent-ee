"""Read-only native s-expression checks, independent of pcbnew geometry probe.
Only the saved two-layer fixture, zero footprint rotation, straight tracks,
round/rectangular two terminal pads and one filled ground polygon are handled.
No refill, parasitic extraction, fabrication or thermal qualification.
"""
from pathlib import Path
import re,json,math,xml.etree.ElementTree as ET,struct
P=Path('/work/experiments')
def parse(path):
 toks=re.findall(r'\(|\)|"(?:\\.|[^"\\])*"|[^\s()]+',Path(path).read_text())
 stack=[]; root=None
 for tok in toks:
  if tok=='(':
   x=[]
   if stack: stack[-1].append(x)
   else: root=x
   stack.append(x)
  elif tok==')': stack.pop()
  else: stack[-1].append(json.loads(tok) if tok.startswith('"') else tok)
 return root
def all_(x,k): return [a for a in x if isinstance(a,list) and a and a[0]==k]
def one(x,k): return all_(x,k)[0]
def xy(x,k): return list(map(float,one(x,k)[1:3]))
def val(x,k): return one(x,k)[1]
def uid(x): return val(x,'uuid')
b=parse('/candidate/project/rc_filter.kicad_pcb'); s=parse('/candidate/project/rc_filter.kicad_sch')
tracks=[dict(uuid=uid(t),start=xy(t,'start'),end=xy(t,'end'),width_mm=float(val(t,'width')),net=val(t,'net'),layer=val(t,'layer')) for t in all_(b,'segment')]
pads=[]; courtyards=[]
for f in all_(b,'footprint'):
 props={x[1]:x[2] for x in all_(f,'property')}; ref=props['Reference']; at=one(f,'at')
 assert len(at)==3 or float(at[3])==0,'Rotation handling intentionally absent'
 for pad in all_(f,'pad'):
  pos=[a+c for a,c in zip(xy(f,'at'),xy(pad,'at'))]
  size=xy(pad,'size'); drill=float(val(pad,'drill')) if all_(pad,'drill') else None
  pads.append(dict(ref=ref,pad=pad[1],net=val(pad,'net'),position_mm=pos,size_mm=size,drill_mm=drill,nominal_annular_ring_mm=(min(size)-drill)/2 if drill else None))
 for rect in all_(f,'fp_rect'):
  if val(rect,'layer')=='F.CrtYd':
   courtyards.append(dict(ref=ref,start=[a+c for a,c in zip(xy(f,'at'),xy(rect,'start'))],end=[a+c for a,c in zip(xy(f,'at'),xy(rect,'end'))]))
native_nets={n:sorted(p['ref']+'.'+p['pad'] for p in pads if p['net']==n) for n in {p['net'] for p in pads}}
x=ET.parse(P/'design.xml').getroot()
xml_nets={n.attrib['name']:sorted(a.attrib['ref']+'.'+a.attrib['pin'] for a in n.findall('node')) for n in x.findall('nets/net')}
values={}
for sym in all_(s,'symbol'):
 props={p[1]:p[2] for p in all_(sym,'property')}
 if props.get('Reference') in ['C1','R1']: values[props['Reference']]=props['Value']
# Even/odd polygon containment directly on the saved copper outline (no zone regeneration).
z=one(b,'zone'); poly=one(one(z,'filled_polygon'),'pts'); vertices=[list(map(float,p[1:])) for p in all_(poly,'xy')]
edges=list(zip(vertices,vertices[1:]+vertices[:1]))
def contains(p):
 x,y=p; inside=False
 for (ax,ay),(bx,by) in edges:
  if (ay>y)!=(by>y) and x < ax+(y-ay)*(bx-ax)/(by-ay): inside=not inside
 return inside
def pdist(p,a,b):
 dx=b[0]-a[0];dy=b[1]-a[1]
 t=max(0,min(1,((p[0]-a[0])*dx+(p[1]-a[1])*dy)/(dx*dx+dy*dy))) if dx*dx+dy*dy else 0
 return math.hypot(p[0]-a[0]-t*dx,p[1]-a[1]-t*dy)
# A physical path from via annulus to J1.3 annulus. Avoid drill voids explicitly.
path=[[113.2,100],[113.2,106.5],[101.42,106.5],[100.5,105.58]]
samples=[]
for a,c in zip(path,path[1:]):
 n=math.ceil(math.dist(a,c)/0.005)
 samples.extend([[a[i]+(c[i]-a[i])*k/n for i in [0,1]] for k in range(n+1)])
all_inside=all(contains(p) for p in samples)
margin=min(pdist(p,a,c) for p in samples for a,c in edges)
# If margin > sample pitch/2, no unsampled crossing of a polygon edge is possible.
via=one(b,'via'); vp=xy(via,'at')
via_end_contact=float(val(via,'drill'))/2 < math.dist(path[0],vp) < float(val(via,'size'))/2
jp=next(p for p in pads if p['ref']=='J1' and p['pad']=='3')
j_end_contact=jp['drill_mm']/2 < math.dist(path[-1],jp['position_mm']) < min(jp['size_mm'])/2
# Graph physical centerline intersections and pad centers for this straight-track fixture.
track_nodes=[('track:'+t['uuid'],t['net']) for t in tracks]
pad_nodes=[(p['ref']+'.'+p['pad'],p['net']) for p in pads]
adj={k:set() for k,n in track_nodes+pad_nodes}
for i,t in enumerate(tracks):
 tn=track_nodes[i][0]
 for j,p in enumerate(pads):
  if p['net']==t['net'] and pdist(p['position_mm'],t['start'],t['end'])<1e-8:
   pn=pad_nodes[j][0];adj[tn].add(pn);adj[pn].add(tn)
 for j,u in enumerate(tracks):
  if i<j and t['net']==u['net'] and min(pdist(t['start'],u['start'],u['end']),pdist(t['end'],u['start'],u['end']),pdist(u['start'],t['start'],t['end']),pdist(u['end'],t['start'],t['end']))<1e-8:
   un=track_nodes[j][0];adj[tn].add(un);adj[un].add(tn)
def reachable(start):
 seen={start}; todo=[start]
 while todo:
  for n in adj[todo.pop()]-seen: seen.add(n);todo.append(n)
 return sorted(seen)
# Analytic check derived from saved R1/C1, with zero initial capacitor voltage.
assert values=={'C1':'100n','R1':'10k'}
r=10000.;c=100e-9;tau=r*c;tr=1e-9
# PWL ramp correction: y(t>=tr)=1 - (tau/tr)*(1-exp(-tr/tau))*exp(-(t-tr)/tau).
def step(t): return -math.expm1(-t/tau)
def ramp(t): return 1-(-math.expm1(-tr/tau))/(tr/tau)*math.exp(-(t-tr)/tau)
measurements={str(t):dict(ideal_step_v=step(t),pwl_1ns_v=ramp(t)) for t in [0.001,0.005]}
# Parse supplied raw waveform rather than relying on exit status or .meas text.
raw=Path('/evidence/work/rc-step/result.raw').read_bytes();split=raw.index(b'Binary:\n')+len(b'Binary:\n'); header=raw[:split].decode()
nvar=int(re.search(r'No. Variables:\s*(\d+)',header)[1]); npt=int(re.search(r'No. Points:\s*(\d+)',header)[1])
assert nvar==4
rawrows=[struct.unpack_from('<4d',raw,split+32*i) for i in range(npt)]
interpolated={}
for t in [0.001,0.005]:
 for a,b_ in zip(rawrows,rawrows[1:]):
  if a[0]<=t<=b_[0]:
   f=(t-a[0])/(b_[0]-a[0]); interpolated[str(t)]={k:a[j]+f*(b_[j]-a[j]) for j,k in [(1,'input_v'),(2,'output_v'),(3,'source_current_a')]};break
 if str(t) not in interpolated and abs(t-rawrows[-1][0])<1e-14:
  interpolated[str(t)]=dict(input_v=rawrows[-1][1],output_v=rawrows[-1][2],source_current_a=rawrows[-1][3])
result=dict(method='python3 /work/experiments/physical_probe.py; standard-library native parser, even/odd saved-polygon containment, centerline graph, analytic RC and little-endian binary raw reader',native_schematic_values=values,tracks=tracks,minimum_track_width_mm=min(t['width_mm'] for t in tracks),all_track_widths_meet_0p20mm=all(t['width_mm']>=0.2 for t in tracks),pads=pads,board_pad_nets=native_nets,exported_schematic_nets=xml_nets,net_membership_match=native_nets==xml_nets,courtyards=courtyards,outline_mm=[xy(e,'start') for e in all_(b,'gr_line') if val(e,'layer')=='Edge.Cuts'],signal_connectivity={net:reachable(native_nets[net][0]) for net in ['/IN','/OUT']},saved_ground_return=dict(zone_uuid=uid(z),via_uuid=uid(via),polyline_mm=path,all_samples_inside_saved_fill=all_inside,max_sample_spacing_mm=0.005,min_sample_distance_to_polygon_edge_mm=margin,continuous_path_certified_by_distance_bound=all_inside and margin>0.0025,starts_in_via_annulus=via_end_contact,ends_in_J1p3_annulus=j_end_contact),rc=dict(assumptions=['Ideal 1V input, zero initial capacitor voltage, no external load; values verified in native schematic and exported netlist','PWL input rises from 0 to 1V in 1ns; model contains only R1 and C1.'],tau_s=tau,analytic=measurements,initial_peak_current_a=1/r,initial_peak_R_power_w=1/r,stored_C_energy_j=c/2),supplied_raw=dict(header=header,first_point=rawrows[0],last_point=rawrows[-1],interpolated=interpolated),limitations=['The polygon path establishes saved ground connectivity only, not RF return quality or thermal qualification.','No zones refilled; no board edits; native project has no specified fabricator process.','No manufacturing outputs, copper thickness, drill tolerance or component part-number qualification evaluated.','Binary format decoded for this ngspice-42 real transient only.'])
(P/'physical_probe.json').write_text(json.dumps(result,indent=2))
print(json.dumps({k:result[k] for k in ['minimum_track_width_mm','all_track_widths_meet_0p20mm','net_membership_match','saved_ground_return','rc','supplied_raw']},indent=2))
