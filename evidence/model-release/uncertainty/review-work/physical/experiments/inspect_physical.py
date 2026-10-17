from pathlib import Path
import re, json, math, hashlib, xml.etree.ElementTree as ET
import numpy as np
from matplotlib.path import Path as MPath
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Rectangle, Polygon
root=Path('/work/experiments')
def parse(path):
 tokens=re.findall(r'"(?:\\.|[^"\\])*"|[()]|[^\s()]+',Path(path).read_text())
 stack=[]
 for tok in tokens:
  if tok=='(':
   node=[]
   if stack: stack[-1].append(node)
   stack.append(node)
  elif tok==')':
   result=stack.pop()
  else: stack[-1].append(json.loads(tok) if tok.startswith('"') else tok)
 return result

def children(node,key):return [x for x in node if isinstance(x,list) and x[0]==key]
def one(node,key):return children(node,key)[0]
def floats(node):return list(map(float,node[1:]))
b=parse('/candidate/project/rc_filter.kicad_pcb')
s=parse('/candidate/project/rc_filter.kicad_sch')
report={}
report['schematic_components']=[{'ref':next(x[2] for x in children(n,'property') if x[1]=='Reference'),'value':next(x[2] for x in children(n,'property') if x[1]=='Value')} for n in children(s,'symbol')]
xml=ET.parse(root/'independent_netlist.xml').getroot()
report['exported_nets']={n.attrib['name']:sorted(x.attrib['ref']+'.'+x.attrib['pin'] for x in n.findall('node')) for n in xml.findall('nets/net')}
original=json.loads(Path('/evidence/work/netlist/connectivity.json').read_text())['nets']
report['export_matches_supplied_netlist']=report['exported_nets']=={k:sorted(v) for k,v in original.items()}
fig,ax=plt.subplots(figsize=(10,6))
z=one(b,'zone'); coords=np.array([floats(x) for x in children(one(one(z,'filled_polygon'),'pts'),'xy')])
poly=MPath(coords,closed=True)
ax.add_patch(Polygon(coords,closed=True,color='#d7dfd0',label='Saved B.Cu ground fill'))
report['saved_fill']={'net':one(z,'net')[1], 'vertices':len(coords),'via_center_inside':bool(poly.contains_point((113,100))), 'simple_return_polyline':[[113,100],[113,106.8],[101.72,106.8],[100.49497475,105.57497475]]}
route=np.array(report['saved_fill']['simple_return_polyline'])
samples=np.concatenate([np.linspace(a,c,501) for a,c in zip(route[:-1],route[1:])])
report['saved_fill']['return_polyline_all_1503_samples_inside']=bool(poly.contains_points(samples).all())
# Samples establish geometric contact only, not a parasitic or thermal model.
report['saved_fill']['ground_header_contact_at_0_7mm_radius']={str(deg):bool(poly.contains_point((100+0.7*math.cos(math.radians(deg)),105.08+0.7*math.sin(math.radians(deg))))) for deg in range(0,360,45)}
report['pads']=[]; courtyard=[]
for fp in children(b,'footprint'):
 ref=next(x[2] for x in children(fp,'property') if x[1]=='Reference'); pos=np.array(floats(one(fp,'at'))[:2])
 ax.text(pos[0],pos[1]-1.4,ref,ha='center')
 cr=next(x for x in children(fp,'fp_rect') if one(x,'layer')[1]=='F.CrtYd') if ref!='J1' else one(fp,'fp_rect')
 lo=pos+np.array(floats(one(cr,'start')));hi=pos+np.array(floats(one(cr,'end')))
 courtyard.append({'ref':ref,'bounds_mm':[lo.tolist(),hi.tolist()]})
 ax.add_patch(Rectangle(lo,*(hi-lo),fill=False,ls=':',color='gray'))
 for pad in children(fp,'pad'):
  xy=pos+np.array(floats(one(pad,'at'))[:2]);size=floats(one(pad,'size'));net=one(pad,'net')[1]
  entry={'ref':ref,'pin':pad[1],'type':pad[2],'shape':pad[3],'xy_mm':xy.tolist(),'size_mm':size,'net':net}
  if children(pad,'drill'):entry['drill_mm']=float(one(pad,'drill')[1])
  report['pads'].append(entry)
  color={'GND':'#40734b','/IN':'#cf9d39','/OUT':'#bd5252'}[net]
  if pad[3]=='circle':ax.add_patch(Circle(xy,size[0]/2,color=color))
  else:ax.add_patch(Rectangle(xy-np.array(size)/2,*size,color=color))
  if 'drill_mm' in entry:ax.add_patch(Circle(xy,entry['drill_mm']/2,color='white'))
  ax.text(xy[0],xy[1],pad[1],ha='center',va='center',fontsize=7)
report['courtyards']=courtyard
report['tracks']=[]
for t in children(b,'segment'):
 a=floats(one(t,'start'));c=floats(one(t,'end'));w=float(one(t,'width')[1]);net=one(t,'net')[1]
 report['tracks'].append({'uuid':one(t,'uuid')[1],'net':net,'start_mm':a,'end_mm':c,'length_mm':math.dist(a,c),'width_mm':w})
 ax.plot([a[0],c[0]],[a[1],c[1]],lw=w*4,color={'GND':'#40734b','/IN':'#cf9d39','/OUT':'#bd5252'}[net])
report['via']={'xy_mm':floats(one(one(b,'via'),'at')),'diameter_mm':float(one(one(b,'via'),'size')[1]),'drill_mm':float(one(one(b,'via'),'drill')[1])}
ax.add_patch(Circle((113,100),.3,color='#40734b'));ax.add_patch(Circle((113,100),.15,color='white'))
report['outline']=[{'start_mm':floats(one(g,'start')),'end_mm':floats(one(g,'end'))} for g in children(b,'gr_line') if one(g,'layer')[1]=='Edge.Cuts']
ends=[tuple(x[k]) for x in report['outline'] for k in ['start_mm','end_mm']]
report['outline_all_corners_degree_2']=all(ends.count(p)==2 for p in set(ends))
for g in report['outline']:
 a,c=g.values();ax.plot([a[0],c[0]],[a[1],c[1]],color='black')
report['nominal_route_notes']=['IN: J1.1 to R1.1 on F.Cu.', 'OUT: R1.2 to C1.1; branch at (107.5,100) to J1.2 on F.Cu.', 'GND: C1.2 via 2.05 mm F.Cu trace to via (113,100), saved B.Cu fill to plated J1.3.', 'T-junction at (107.5,100) lies on segment 69a2edc1-cbed-4fb8-a0fc-ac38c986e5f5; it is not an unrouted gap.']
report['limitations']=['Rounded rectangular pads plotted as rectangles; plot is a topology aid, not a clearance measurement.','Fill path is sampled, not a rigorous continuous-path proof; saved-board native DRC independently reports no unconnected items.','No zone refill performed; no process capability or copper thickness/ampacity/thermal qualification inferred.','No extracted interconnect RLC or external ADC/cable/ground model.']
ax.set_aspect('equal');ax.set_xlim(95.5,116.5);ax.set_ylim(108.5,95.5);ax.set_xlabel('x (mm)');ax.set_ylabel('y (mm)');ax.set_title('Native saved copper topology (simplified pad shapes)');fig.tight_layout();fig.savefig(root/'saved_copper.png',dpi=150)
(root/'physical_inspection.json').write_text(json.dumps(report,indent=2))
print(json.dumps({k:report[k] for k in ['exported_nets','export_matches_supplied_netlist','saved_fill','outline_all_corners_degree_2','nominal_route_notes']},indent=2))
