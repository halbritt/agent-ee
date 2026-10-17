import pcbnew,json,pathlib,hashlib,xml.etree.ElementTree as ET,collections,math
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon,Circle,Rectangle
root=pathlib.Path('/work/experiments')
boardpath='/candidate/project/Polygonus-Universal-Base.kicad_pcb'
b=pcbnew.LoadBoard(boardpath)
mm=lambda p:[p.x/1e6,p.y/1e6]
fps={f.GetReference():f for f in b.GetFootprints()}
report={'pcbnew_version':pcbnew.Version(),'board_sha256':hashlib.sha256(pathlib.Path(boardpath).read_bytes()).hexdigest(),'assumptions':['Read-only LoadBoard; no SaveBoard and no zone refill. Native saved fills only.','Pad net comparison checks names and pin numbers, not internal component behavior.','Annulus calculation uses nominal circular pad and drill diameters; no fabrication tolerances assumed.'],'mechanical_holes':[]}
for p in fps['J1'].Pads():
 if not p.GetNumber():
  report['mechanical_holes'].append({'uuid':p.m_Uuid.AsString(),'position_mm':mm(p.GetPosition()),'pad_type': 'PTH' if p.GetAttribute()==pcbnew.PAD_ATTRIB_PTH else str(p.GetAttribute()),'size_mm':mm(p.GetSize()),'drill_mm':mm(p.GetDrillSize()),'nominal_annulus_mm':[(a-d)/2 for a,d in zip(mm(p.GetSize()),mm(p.GetDrillSize()))]})
expected={}
for net in ET.parse('/evidence/work/netlist/design.xml').findall('./nets/net'):
 for n in net.findall('node'):expected[(n.get('ref'),n.get('pin'))]=net.get('name')
mismatches=[];compared=0
for f in fps.values():
 for p in f.Pads():
  key=(f.GetReference(),p.GetNumber())
  if key in expected:
   compared+=1
   if p.GetNetname()!=expected[key]:mismatches.append({'ref':key[0],'pin':key[1],'pcb':p.GetNetname(),'xml':expected[key]})
report['pad_net_comparison']={'pads_compared':compared,'mismatches':mismatches}
report['can2']={'pads':[],'track_length_mm_by_net':{},'via_count_by_net':{},'termination_population':{}}
for r in ['J1','J2','U20','U21','R36','R37']:
 f=fps[r]
 if r in ['R36','R37']:report['can2']['termination_population'][r]={'value':f.GetValue(),'dnp':f.IsDNP()}
 for p in f.Pads():
  if p.GetNetname() in ['/CAN2+','/CAN2-']:
   report['can2']['pads'].append({'ref':r,'pad':p.GetNumber(),'net':p.GetNetname(),'position_mm':mm(p.GetPosition()),'uuid':p.m_Uuid.AsString()})
for net in ['/CAN2+','/CAN2-','/12V_MR','/12V_MR_FUSE','GND','/HeaterNeg','/HeaterNeg2']:
 ts=[t for t in b.GetTracks() if t.GetNetname()==net]
 report.setdefault('copper_summary',{})[net]={'segments':sum(not isinstance(t,pcbnew.PCB_VIA) for t in ts),'vias':sum(isinstance(t,pcbnew.PCB_VIA) for t in ts),'total_track_length_mm':sum(t.GetLength()/1e6 for t in ts if not isinstance(t,pcbnew.PCB_VIA)),'track_widths_mm':sorted(set(t.GetWidth()/1e6 for t in ts if not isinstance(t,pcbnew.PCB_VIA)))}
drc=json.load(open('/evidence/work/drc/drc.json'))
report['thermal_native_checks']=[{'index':i,**v} for i,v in enumerate(drc['violations']) if v['type']=='starved_thermal']
(root/'board-measurements.json').write_text(json.dumps(report,indent=2))
# Visualize exact saved zone polygons plus nominal pad bounding geometry and tracks.
fig,axs=plt.subplots(1,3,figsize=(15,5))
for ax,(ref,num) in zip(axs,[('C15','2'),('U7','4'),('C14','2')]):
 p=next(p for p in fps[ref].Pads() if p.GetNumber()==num); x,y=mm(p.GetPosition())
 ax.set_xlim(x-1.8,x+1.8);ax.set_ylim(y+1.8,y-1.8)
 for z in b.Zones():
  if z.GetNetname()!='GND' or not z.IsOnLayer(pcbnew.F_Cu):continue
  poly=z.GetFilledPolysList(pcbnew.F_Cu)
  for i in range(poly.OutlineCount()):
   line=poly.COutline(i); pts=[mm(line.CPoint(j)) for j in range(line.PointCount())]
   ax.add_patch(Polygon(pts,facecolor='#84b8dc',edgecolor='none'))
 for f in fps.values():
  for pad in f.Pads():
   px,py=mm(pad.GetPosition())
   if abs(px-x)>3 or abs(py-y)>3 or not pad.IsOnLayer(pcbnew.F_Cu):continue
   poly=pad.GetEffectivePolygon(pcbnew.F_Cu)
   for i in range(poly.OutlineCount()):
    line=poly.COutline(i);pts=[mm(line.CPoint(j)) for j in range(line.PointCount())]
    ax.add_patch(Polygon(pts,facecolor='#17587e' if pad.GetNetname()=='GND' else '#cb8070',edgecolor='black',linewidth=.3))
 for t in b.GetTracks():
  if isinstance(t,pcbnew.PCB_VIA):
   px,py=mm(t.GetPosition())
   if abs(px-x)<3 and abs(py-y)<3:ax.add_patch(Circle((px,py),t.GetWidth()/2e6,facecolor='#17587e' if t.GetNetname()=='GND' else '#b2a2a0'))
  elif t.IsOnLayer(pcbnew.F_Cu):
   a=mm(t.GetStart());c=mm(t.GetEnd())
   if min(a[0],c[0])<x+3 and max(a[0],c[0])>x-3 and min(a[1],c[1])<y+3 and max(a[1],c[1])>y-3:
    ax.plot([a[0],c[0]],[a[1],c[1]],color='#17587e' if t.GetNetname()=='GND' else '#a58f89',linewidth=t.GetWidth()/1e6*24,solid_capstyle='round')
 ax.set_title(f'{ref} pad {num}: GND\n({x:.4f}, {y:.4f}) mm');ax.set_aspect('equal');ax.grid(alpha=.2);ax.set_xlabel('mm')
fig.suptitle('Saved F.Cu ground fills (light blue); ground pads/tracks (dark blue)\nIllustrative overlay; native DRC supplies spoke counts. No refill.')
fig.tight_layout();fig.savefig(root/'thermal-reliefs.png',dpi=160)
print(json.dumps({'holes':report['mechanical_holes'],'pad_net_comparison':report['pad_net_comparison'],'copper_summary':report['copper_summary']},indent=2))
