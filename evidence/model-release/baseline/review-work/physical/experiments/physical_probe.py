import pcbnew as k, json, pathlib, xml.etree.ElementTree as E, math, collections, hashlib
import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon, Rectangle, Circle
root=pathlib.Path('/work/experiments'); src=pathlib.Path('/candidate/project/Polygonus-Universal-Base.kicad_pcb'); b=k.LoadBoard(str(src)); conn=b.GetConnectivity()
mm=lambda v: [v.x/1e6,v.y/1e6]
pads={(f.GetReference(),p.GetNumber()):p for f in b.GetFootprints() for p in f.Pads() if p.GetNumber()}
foot={f.GetReference():f for f in b.GetFootprints()}
r=E.parse(root/'independent-netlist.xml').getroot(); expected={}
for net in r.findall('./nets/net'):
 for node in net.findall('node'): expected[(node.get('ref'),node.get('pin'))]=net.get('name')
mismatches=[];missing=[]
for key,name in expected.items():
 if key not in pads: missing.append([*key,name])
 elif pads[key].GetNetname()!=name:mismatches.append([*key,name,pads[key].GetNetname()])
extra=[list(key) for key in pads if key not in expected]
checks=json.load(open('/evidence/work/drc/drc.json'))
errors=[dict(index=i,**v) for i,v in enumerate(checks['violations']) if v['severity']=='error']
holes=[]
for p in foot['J1'].Pads():
 if not p.GetNumber():
  size=mm(p.GetSize()); drill=mm(p.GetDrillSize());holes.append(dict(uuid=str(p.m_Uuid.AsString()),position_mm=mm(p.GetPosition()),size_mm=size,drill_mm=drill,attribute=int(p.GetAttribute()),radial_annulus_mm=(min(size)-max(drill))/2))
thermal=[]
for key in [('C15','2'),('U7','4'),('C14','2')]:
 p=pads[key]; x,y=mm(p.GetPosition()); tracks=[]
 for t in conn.GetConnectedTracks(p):
  tracks.append(dict(uuid=t.m_Uuid.AsString(),type=t.GetClass(),layer=b.GetLayerName(t.GetLayer()),start_mm=mm(t.GetStart()),end_mm=mm(t.GetEnd()),width_mm=t.GetWidth()/1e6))
 nearby=sorted([dict(distance_mm=math.dist((x,y),mm(t.GetPosition())),position_mm=mm(t.GetPosition()),uuid=t.m_Uuid.AsString()) for t in b.GetTracks() if t.GetClass()=='PCB_VIA' and t.GetNetname()=='GND'],key=lambda a:a['distance_mm'])[:3]
 thermal.append(dict(ref=key[0],pad=key[1],position_mm=[x,y],net=p.GetNetname(),connected_tracks=tracks,nearest_ground_vias=nearby))
# Render filled copper exactly as saved. No fill operation or board save.
fig,axes=plt.subplots(1,3,figsize=(15,5))
for ax,key in zip(axes,[('C15','2'),('U7','4'),('C14','2')]):
 p=pads[key];x,y=mm(p.GetPosition());xmin,xmax,ymin,ymax=x-2,x+2,y-2,y+2
 for z in b.Zones():
  if z.IsOnLayer(k.F_Cu):
   polys=z.GetFilledPolysList(k.F_Cu)
   for i in range(polys.OutlineCount()):
    outline=polys.COutline(i); pts=[mm(outline.CPoint(j)) for j in range(outline.PointCount())]
    if pts and min(a[0] for a in pts)<xmax and max(a[0] for a in pts)>xmin and min(a[1] for a in pts)<ymax and max(a[1] for a in pts)>ymin:
     ax.add_patch(Polygon(pts,facecolor='#cbbd73' if z.GetNetname()=='GND' else '#e08080',edgecolor='none'))
 for f in b.GetFootprints():
  for pad in f.Pads():
   px,py=mm(pad.GetPosition())
   if pad.IsOnLayer(k.F_Cu) and xmin-1<px<xmax+1 and ymin-1<py<ymax+1:
    shape=pad.GetEffectivePolygon(k.F_Cu)
    for i in range(shape.OutlineCount()):
     out=shape.COutline(i); pts=[mm(out.CPoint(j)) for j in range(out.PointCount())];ax.add_patch(Polygon(pts,facecolor='#607ca8',edgecolor='black',linewidth=.3))
    ax.text(px,py,f'{f.GetReference()}.{pad.GetNumber()}',ha='center',va='center',fontsize=7,clip_on=True)
 for t in b.GetTracks():
  if t.GetClass()=='PCB_VIA':
   vx,vy=mm(t.GetPosition())
   if xmin<vx<xmax and ymin<vy<ymax:ax.add_patch(Circle((vx,vy),t.GetWidth()/2e6,color='#cc7722'));ax.add_patch(Circle((vx,vy),t.GetDrillValue()/2e6,color='white'))
  elif t.IsOnLayer(k.F_Cu):
   a=mm(t.GetStart());e=mm(t.GetEnd());w=t.GetWidth()/2e6;length=math.dist(a,e)
   if length:
    nx=-(e[1]-a[1])/length*w;ny=(e[0]-a[0])/length*w
    ax.add_patch(Polygon([[a[0]+nx,a[1]+ny],[e[0]+nx,e[1]+ny],[e[0]-nx,e[1]-ny],[a[0]-nx,a[1]-ny]],color='#607ca8',linewidth=0));ax.add_patch(Circle(a,w,color='#607ca8',linewidth=0));ax.add_patch(Circle(e,w,color='#607ca8',linewidth=0))
 ax.set(xlim=(xmin,xmax),ylim=(ymax,ymin),title=f'{key[0]}.{key[1]}: saved F.Cu',xlabel='x (mm)',ylabel='y (mm)');ax.set_aspect('equal');ax.grid(alpha=.15)
fig.tight_layout();fig.savefig(root/'saved-thermal-copper.png',dpi=180)
path_nets=['/CAN2+','/CAN2-','/12V_MR_FUSE','/HeaterNeg','/HeaterNeg2','GND']
routes={}
for name in path_nets:
 ts=[t for t in b.GetTracks() if t.GetNetname()==name]; seg=[t for t in ts if t.GetClass()=='PCB_TRACK'];vias=[t for t in ts if t.GetClass()=='PCB_VIA']
 routes[name]=dict(track_segments=len(seg),track_total_length_mm=sum(t.GetLength()/1e6 for t in seg),widths_mm=dict(collections.Counter(str(t.GetWidth()/1e6) for t in seg)),vias=len(vias),zones=[z.m_Uuid.AsString() for z in b.Zones() if z.GetNetname()==name])
components={}
for ref in ['R36','R37','U20','U21','J2','U1','U2']:
 f=foot[ref];components[ref]=dict(dnp=f.IsDNP(),value=f.GetValue(),position_mm=mm(f.GetPosition()),pads=[dict(pad=p.GetNumber(),net=p.GetNetname(),position_mm=mm(p.GetPosition())) for p in f.Pads()])
report=dict(pcbnew_version=k.Version(),board_sha256=hashlib.sha256(src.read_bytes()).hexdigest(),assumptions=['Saved copper only; no refill, editing, or save.','Pad net comparison checks native names, not correct external part pinouts.','Route totals and widths do not establish ampacity, impedance, return impedance or thermal limits.','Nearby via distance is geometry only, not proof of a qualified return path.'],pad_net_comparison=dict(expected_pins=len(expected),missing=missing,mismatches=mismatches,extra_board_pins=extra),native_errors=errors,j1_mechanical_holes=holes,thermal_pads=thermal,route_summary=routes,selected_components=components)
(root/'physical-measurements.json').write_text(json.dumps(report,indent=2));print(json.dumps({key:report[key] for key in ['pcbnew_version','pad_net_comparison','j1_mechanical_holes','thermal_pads','route_summary']},indent=2))
