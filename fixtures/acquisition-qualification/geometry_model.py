"""Acquisition bench-board engineering geometry; not a general PCB field solver.

Native polygons and saved coordinates are authoritative. Package envelopes,
placement allowance, conductor dimensions and loop limits are reviewed model
inputs. Unknown footprints, rotations, routing primitives or missing paths fail.
"""
import hashlib,itertools,json,math,sys,heapq
from pathlib import Path
import pcbnew as k
from agent_ee.geometry_probe import extract
from agent_ee.placement_probe import extract as placement
root=Path(sys.argv[1]);out=Path(sys.argv[2]);boardpath=root/'project/acquisition.kicad_pcb'
raw=boardpath.read_bytes();board=k.LoadBoard(str(boardpath));g=extract(str(boardpath));p=placement(boardpath)
model=json.loads((root/'analysis/geometry-model.json').read_text())
def poly(points):
 r=k.SHAPE_POLY_SET();r.NewOutline()
 for x,y in points:r.Append(round(x*1e6),round(y*1e6))
 return r
def rect(bounds):
 a,b,c,d=bounds;return poly([(a,b),(c,b),(c,d),(a,d)])
def area(a,b,op):
 r=k.SHAPE_POLY_SET(a);getattr(r,op)(b);r.Simplify();return 0 if r.IsEmpty() else r.Area()/1e12
def gap(a,b):return math.hypot(max(a[0]-b[2],b[0]-a[2],0),max(a[1]-b[3],b[1]-a[3],0))
def overlap(a,b):return max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
outline=k.SHAPE_POLY_SET()
if not board.GetBoardPolygonOutlines(outline,False):raise ValueError('closed native outline required')
body={};parts=[]
if {f['ref'] for f in p['footprints']}!=set(model['packages']):raise ValueError('package model/native inventory differ')
for f in p['footprints']:
 ref=f['ref'];d=model['packages'][ref];w,h,z=d['size_mm'];ox,oy=d['offset_mm'];angle=f['rotation_deg']
 if angle%90!=0 or f['side']!='F':raise ValueError('reviewed model requires orthogonal front placement')
 x,y=[v/1e6 for v in f['position_nm']];a=-math.radians(angle)
 pts=[(x+u*math.cos(a)-v*math.sin(a),y+u*math.sin(a)+v*math.cos(a)) for u,v in [(ox-w/2,oy-h/2),(ox+w/2,oy-h/2),(ox+w/2,oy+h/2),(ox-w/2,oy+h/2)]]
 b=[min(v[0] for v in pts),min(v[1] for v in pts),max(v[0] for v in pts),max(v[1] for v in pts)];body[ref]=b
 parts.append(dict(reference=ref,side=f['side'],anchor_mm=[x,y],rotation_deg=angle,body_bounds_mm=b,height_mm=z,outside_area_mm2=area(poly(pts),outline,'BooleanSubtract')))
pairs=[dict(refs=[a,b],gap_mm=gap(body[a],body[b]),overlap_mm2=overlap(body[a],body[b])) for a,b in itertools.combinations(sorted(body),2)]
access=[]
for ref in ['J1','J2','J3']:
 a,b,c,d=body[ref];col=[a-.5,b-.5,c+.5,d+.5]
 access.append(dict(reference=ref,column_mm=col,obstruction_mm2=sum(overlap(col,v) for r,v in body.items() if r!=ref)))
groups=json.loads((root/'groups.json').read_text())['groups'];membership={};blocks=[]
for group in groups:
 for ref in group['refs']:
  if ref in membership:raise ValueError('multiple block memberships')
  membership[ref]=group['name']
 items=[body[r] for r in group['refs']];bounds=[min(x[0] for x in items),min(x[1] for x in items),max(x[2] for x in items),max(x[3] for x in items)]
 blocks.append(dict(name=group['name'],anchor=group['anchor'],members=group['refs'],side='F',body_bounds_mm=bounds))
if set(membership)!=set(body):raise ValueError('missing block membership')
# Exact native B.Cu fills plus conservative inside pad polygons, minus holes.
ground=k.SHAPE_POLY_SET();holes=k.SHAPE_POLY_SET();error=int(board.GetDesignSettings().m_MaxError)
for z in board.Zones():
 if not z.GetIsRuleArea() and z.GetNetname()=='GND' and z.HasFilledPolysForLayer(k.B_Cu):ground.Append(z.GetFilledPolysList(k.B_Cu))
for fp in board.GetFootprints():
 for pad in fp.Pads():
  if pad.GetNetname()=='GND' and pad.IsOnLayer(k.B_Cu) and pad.FlashLayer(k.B_Cu):pad.TransformShapeToPolygon(ground,k.B_Cu,0,error,k.ERROR_INSIDE)
  if pad.HasHole():pad.TransformHoleToPolygon(holes,0,error,k.ERROR_OUTSIDE)
ground.Simplify();holes.Simplify();ground.BooleanSubtract(holes);ground.Simplify()
strips=[]
for name,pts in model['ground_strips'].items():
 strip=k.SHAPE_POLY_SET();length=0
 for a,b in zip(pts,pts[1:]):
  if a[0]!=b[0] and a[1]!=b[1]:raise ValueError('strip model requires orthogonal segments')
  # Slightly enlarged square end joins conservatively test continuous width.
  strip.Append(rect([min(a[0],b[0])-.1,min(a[1],b[1])-.1,max(a[0],b[0])+.1,max(a[1],b[1])+.1]));length+=math.dist(a,b)
 strip.Simplify();strips.append(dict(name=name,points_mm=pts,width_mm=.2,length_mm=length,outside_area_mm2=area(strip,ground,'BooleanSubtract')))
# Centerline graph includes native vias, pad connections and copper T junctions.
adj={};padnodes={};pads={(fp.GetReference(),pad.GetNumber()):pad for fp in board.GetFootprints() for pad in fp.Pads()}
def edge(a,b,d):adj.setdefault(a,[]).append((b,d));adj.setdefault(b,[]).append((a,d))
def project(point,a,b):
 dx,dy=b[0]-a[0],b[1]-a[1];l=dx*dx+dy*dy
 t=0 if l==0 else max(0,min(1,((point[0]-a[0])*dx+(point[1]-a[1])*dy)/l));return (a[0]+t*dx,a[1]+t*dy)
tracks=g['tracks'];segments=[]
for t in tracks:
 if t['type']=='via':
  xy=tuple(t['start_mm']);edge((t['net'],'F.Cu',xy),(t['net'],'B.Cu',xy),k.ToMM(board.GetDesignSettings().GetBoardThickness()));continue
 if t['type']!='track':raise ValueError('unsupported routing primitive')
 segments.append(t);edge((t['net'],t['layer'],tuple(t['start_mm'])),(t['net'],t['layer'],tuple(t['end_mm'])),t['length_mm'])
for t in segments:
 a,b=t['start_mm'],t['end_mm'];ka=(t['net'],t['layer'],tuple(a));kb=(t['net'],t['layer'],tuple(b))
 for other in tracks:
  if other['net']!=t['net'] or (other['type']=='track' and other['layer']!=t['layer']):continue
  for xy in (other['start_mm'],other['end_mm']):
   q=project(xy,a,b)
   if math.dist(q,xy)>(t['width_mm']+other['width_mm'])/2-.000001:continue
   j=(t['net'],t['layer'],q);edge((t['net'],t['layer'],tuple(xy)),j,math.dist(q,xy));edge(j,ka,math.dist(q,a));edge(j,kb,math.dist(q,b))
for p in g['pads']:
 pad=pads[(p['ref'],p['pad'])]
 for layer in p['flash_layers']:
  n=('pad',p['ref'],p['pad'],layer);padnodes[(p['ref'],p['pad'],layer)]=n
  def inside(xy):return pad.HitTest(k.VECTOR2I(round(xy[0]*1e6),round(xy[1]*1e6)))
  for node in list(adj):
   if node[0]==p['net'] and node[1]==layer and inside(node[2]):edge(n,node,math.dist(p['position_mm'],node[2]))
  for t in segments:
   if t['net']!=p['net'] or t['layer']!=layer:continue
   q=project(p['position_mm'],t['start_mm'],t['end_mm'])
   if not inside(q):continue
   j=(t['net'],layer,q);edge(n,j,math.dist(p['position_mm'],q));edge(j,(t['net'],layer,tuple(t['start_mm'])),math.dist(q,t['start_mm']));edge(j,(t['net'],layer,tuple(t['end_mm'])),math.dist(q,t['end_mm']))
def length(a,b):
 start=padnodes[(*a,'F.Cu')];end=padnodes[(*b,'F.Cu')];queue=[(0,start)];best={start:0}
 while queue:
  dist,n=heapq.heappop(queue)
  if n==end:return dist
  if dist!=best[n]:continue
  for other,delta in adj.get(n,[]):
   d=dist+delta
   if d<best.get(other,math.inf):best[other]=d;heapq.heappush(queue,(d,other))
 raise ValueError('no local trace path for '+str((a,b)))
connections=[];loops={}
for name,paths in model['loops'].items():
 total=0
 for a,b in paths:
  dist=length(a,b);total+=dist;connections.append(dict(pads=[a,b],length_mm=dist))
 loops[name]=total
rails=[t for t in segments if t['net'] in ['/V3V3','GND']]
if any(t['width_mm']<.25 for t in rails):raise ValueError('rail narrower than reviewed0.25mm')
r=model['dc_estimate'];tl=sum(t['length_mm'] for t in rails)
resistance=r['rho_ohm_m']*(tl*.001/(r['track_width_mm']*.001*r['copper_um']*1e-6)+max(s['length_mm'] for s in strips)*.001/(r['plane_strip_width_mm']*.001*r['copper_um']*1e-6))+r['contact_and_joint_ohm']
metrics={'body_gap':min(x['gap_mm'] for x in pairs),'placement_gap':min(x['gap_mm'] for x in pairs)-2*model['placement_allowance_mm'],'body_overlap':sum(x['overlap_mm2'] for x in pairs),'body_outside':sum(x['outside_area_mm2'] for x in parts),'access_obstruction':sum(x['obstruction_mm2'] for x in access),'ground_strip_outside':sum(s['outside_area_mm2'] for s in strips),'rail_drop':resistance*r['input_current_a'],**{n:v for n,v in loops.items()}}
if boardpath.read_bytes()!=raw:raise ValueError('native inspection modified board')
result=dict(board_sha256=hashlib.sha256(raw).hexdigest(),model_sha256=hashlib.sha256((root/'analysis/geometry-model.json').read_bytes()).hexdigest(),parts=parts,closest_pairs=sorted(pairs,key=lambda x:x['gap_mm'])[:12],blocks=blocks,connector_access=access,ground_strips=strips,local_connections=connections,rail_trace_length_mm=tl,estimated_rail_return_ohm=resistance,metrics=metrics,limits=model['limits'])
out.write_text(json.dumps(result,indent=2)+'\n')
