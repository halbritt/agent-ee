"""Independent text parsing and geometry contacts; no KiCad connectivity API."""
import re, json, math, hashlib, sys, xml.etree.ElementTree as ET
from pathlib import Path
from itertools import combinations

def parse(path):
    ts = re.findall(r'\(|\)|"(?:\\.|[^"\\])*"|[^\s()]+', Path(path).read_text())
    stack=[]
    for t in ts:
        if t=='(':
            v=[]
            if stack: stack[-1].append(v)
            stack.append(v)
        elif t==')':
            root=stack.pop()
        else: stack[-1].append(json.loads(t) if t.startswith('"') else t)
    return root
def children(n,key): return [v for v in n if isinstance(v,list) and v[0]==key]
def one(n,key): return children(n,key)[0]
def nums(n,key): return tuple(map(float,one(n,key)[1:]))
def prop(n,key): return next(v[2] for v in children(n,'property') if v[1]==key)
def xy(p): return tuple(map(float,p[1:]))

board=parse('/candidate/project/rc_filter.kicad_pcb')
sch=parse('/candidate/project/rc_filter.kicad_sch')
items=[]
def circle(x,y,r,count=256):
    return [(x+r*math.cos(i*2*math.pi/count), y+r*math.sin(i*2*math.pi/count)) for i in range(count)]
def roundrect(x,y,w,h,r):
    if not r: return [(x-w/2,y-h/2),(x+w/2,y-h/2),(x+w/2,y+h/2),(x-w/2,y+h/2)]
    pts=[]
    for dx,dy,initial in [(1,1,0),(-1,1,90),(-1,-1,180),(1,-1,270)]:
        for i in range(65):
            a=math.radians(initial+i*90/64)
            pts.append((x+dx*(w/2-r)+r*math.cos(a), y+dy*(h/2-r)+r*math.sin(a)))
    return pts
def add(name,net,layers,poly,hole=None,**details):
    items.append(dict(name=name,net=net,layers=layers,poly=poly,hole=hole,**details))

for fp in children(board,'footprint'):
    ref=prop(fp,'Reference'); a=nums(fp,'at'); assert len(a)==2, 'This probe supports zero footprint rotation only'
    for p in children(fp,'pad'):
        at=nums(p,'at'); x,y=a[0]+at[0],a[1]+at[1]; w,h=nums(p,'size')
        shape=p[3]
        if shape=='circle': poly=circle(x,y,w/2)
        else:
            assert shape in ('rect','roundrect')
            r=float(one(p,'roundrect_rratio')[1])*min(w,h) if shape=='roundrect' else 0
            poly=roundrect(x,y,w,h,r)
        ls=one(p,'layers')[1:]; ls=['F.Cu','B.Cu'] if '*.Cu' in ls else [v for v in ls if v.endswith('.Cu')]
        hole=(x,y,float(one(p,'drill')[1])/2) if children(p,'drill') else None
        add(ref+'.'+p[1],one(p,'net')[1],ls,poly,hole,center_mm=[x,y],size_mm=[w,h])
for idx,s in enumerate(children(board,'segment')):
    x,y=nums(s,'start'); u,v=nums(s,'end'); r=nums(s,'width')[0]/2
    assert x==u or y==v, 'This probe supports axis aligned tracks only'
    add('track'+str(idx+1),one(s,'net')[1],[one(s,'layer')[1]],roundrect((x+u)/2,(y+v)/2,abs(u-x)+2*r,abs(v-y)+2*r,r),start_mm=[x,y],end_mm=[u,v],length_mm=math.hypot(x-u,y-v))
for idx,v in enumerate(children(board,'via')):
    x,y=nums(v,'at'); d=nums(v,'size')[0]; drill=nums(v,'drill')[0]
    add('via'+str(idx+1),one(v,'net')[1],['F.Cu','B.Cu'],circle(x,y,d/2),(x,y,drill/2),center_mm=[x,y],size_mm=[d,d])
for z in children(board,'zone'):
    for idx,p in enumerate(children(z,'filled_polygon')):
        add('saved_fill'+str(idx+1),one(z,'net')[1],[one(p,'layer')[1]],[xy(q) for q in one(p,'pts')[1:]])

def edges(poly): return zip(poly,poly[1:]+poly[:1])
def cross(a,b): return a[0]*b[1]-a[1]*b[0]
def sub(a,b): return a[0]-b[0],a[1]-b[1]
def intersect(a,b,c,d):
    r,s=sub(b,a),sub(d,c); den=cross(r,s)
    if abs(den)<1e-15: return None
    t,u=cross(sub(c,a),s)/den,cross(sub(c,a),r)/den
    if -1e-10<=t<=1+1e-10 and -1e-10<=u<=1+1e-10: return a[0]+t*r[0],a[1]+t*r[1]
def in_poly(p,poly):
    inside=False; x,y=p
    for (a,b),(c,d) in edges(poly):
        if (b>y)!=(d>y) and x<(c-a)*(y-b)/(d-b)+a: inside=not inside
    return inside
def outside_hole(p,item):
    h=item['hole']
    return h is None or math.hypot(p[0]-h[0],p[1]-h[1])>h[2]+1e-9
def contact(a,b):
    for p in a['poly']:
        if in_poly(p,b['poly']) and outside_hole(p,a) and outside_hole(p,b): return p
    for p in b['poly']:
        if in_poly(p,a['poly']) and outside_hole(p,a) and outside_hole(p,b): return p
    for c,d in edges(a['poly']):
        for e,f in edges(b['poly']):
            p=intersect(c,d,e,f)
            if p and outside_hole(p,a) and outside_hole(p,b): return p

adj={v['name']:set() for v in items}; contacts=[]
for a,b in combinations(items,2):
    layers=sorted(set(a['layers']) & set(b['layers']))
    if not layers: continue
    p=contact(a,b)
    if p:
        adj[a['name']].add(b['name']); adj[b['name']].add(a['name'])
        contacts.append(dict(a=a['name'],b=b['name'],layers=layers,witness_mm=p,same_declared_net=a['net']==b['net']))
components=[]; remain=set(adj)
while remain:
    group=set(); todo=[min(remain)]
    while todo:
        v=todo.pop()
        if v in group: continue
        group.add(v); todo.extend(adj[v]-group)
    remain-=group
    components.append(dict(items=sorted(group), pads=sorted(v for v in group if re.fullmatch(r'[JRC]1\.[123]',v)),declared_nets=sorted({v['net'] for v in items if v['name'] in group})))

# Independent schematic connector rotation, followed through direct wires.
lib=next(v for v in children(one(sch,'lib_symbols'),'symbol') if v[1]=='Connector_Generic:Conn_01x03')
pins=[p for s in children(lib,'symbol') for p in children(s,'pin')]
j1=next(s for s in children(sch,'symbol') if prop(s,'Reference')=='J1')
ox,oy,angle=nums(j1,'at'); theta=math.radians(angle)
def coord(v): return tuple(round(x,6) for x in v)
wire_adj={}
for wire in children(sch,'wire'):
    a,b=[coord(xy(p)) for p in one(wire,'pts')[1:]]
    wire_adj.setdefault(a,set()).add(b); wire_adj.setdefault(b,set()).add(a)
labels={coord(nums(v,'at')[:2]):v[1] for v in children(sch,'label')}
labels.update({coord(nums(s,'at')[:2]):'GND' for s in children(sch,'symbol') if one(s,'lib_id')[1]=='power:GND'})
pin_map={}
for p in pins:
    x,y,_=nums(p,'at'); start=coord((ox+x*math.cos(theta)+y*math.sin(theta),oy+x*math.sin(theta)-y*math.cos(theta)))
    todo=[start]; seen=set()
    while todo:
        v=todo.pop()
        if v in seen: continue
        seen.add(v); todo.extend(wire_adj.get(v,set())-seen)
    pin_map[one(p,'number')[1]]=dict(position_mm=start, labels=sorted({labels[v] for v in seen if v in labels}))

xml=ET.parse('/work/experiments/design.xml')
netlist={n.attrib['name']:sorted(v.attrib['ref']+'.'+v.attrib['pin'] for v in n.findall('node')) for n in xml.findall('./nets/net')}
report=dict(python=sys.version, method='Native S-expression parse; copper-only graph from layer overlap and geometric contact, WITHOUT using declared net names to create edges. PTH/via barrels join the two layers. Saved fill contour used verbatim; drills excluded from contact witnesses. Circles use 256 inscribed edges; rounded corners 64 edges per quadrant.', assumptions=['Only saved connectivity evaluated; PTH/via barrels assumed conductive as designed.', 'No track arcs, rotations, custom pads or zone holes present; unsupported shapes asserted.', 'Coordinates in mm; approximation sagitta <=0.0000641 mm for largest circle (r=0.85 mm). Contacts have substantial overlap; no near-gap acceptance tolerance used.'],schematic_connector_rotation=pin_map, copper_items=[{k:v for k,v in a.items() if k not in ('poly','hole')} for a in items], contacts=contacts,copper_components=components,independent_exported_netlist=netlist,checks=dict(no_cross_net_copper_contacts=all(v['same_declared_net'] for v in contacts), pad_groups_match_export=sorted(v['pads'] for v in components)==sorted(netlist.values()),contract_schematic=all(pin_map[k]['labels']==[v] for k,v in {'1':'IN','2':'OUT','3':'GND'}.items())),nominal_ideal_rc=dict(R_ohm=10000,C_F=1e-7,tau_s=.001,fc_Hz=1/(2*math.pi*.001),assumption='Ideal components, zero source impedance and infinite load; analytic topology sanity check only, not measured behavior.'),limits=['No thermal/ampacity qualification or assembly hardware measurements.', 'No refilling or alteration of saved PCB.'])
# Concrete positive-width return route, avoiding reliance on treating a zone as a node.
route=[(113,100.2),(113,106.58),(101.5,106.58),(100.55,105.63)]
fill=next(a['poly'] for a in items if a['name']=='saved_fill1')
samples=[]
for a,b in zip(route,route[1:]):
    count=math.ceil(math.dist(a,b)/.01)
    samples.extend((a[0]+(b[0]-a[0])*i/count,a[1]+(b[1]-a[1])*i/count) for i in range(count+1))
def segdist(p,a,b):
    dx,dy=b[0]-a[0],b[1]-a[1]; l=dx*dx+dy*dy
    t=max(0,min(1,((p[0]-a[0])*dx+(p[1]-a[1])*dy)/l)) if l else 0
    return math.hypot(p[0]-a[0]-t*dx,p[1]-a[1]-t*dy)
sample_clearance=min(segdist(p,a,b) for p in samples for a,b in edges(fill))
report['explicit_saved_ground_fill_route']=dict(vertices_mm=route,all_samples_inside=all(in_poly(p,fill) for p in samples),sample_spacing_max_mm=.01,sample_count=len(samples),minimum_sample_distance_to_contour_mm=sample_clearance,conservative_continuous_lower_bound_mm=sample_clearance-.005,endpoint_radius_from_J1_3_mm=math.dist(route[-1],(100,105.08)),J1_3_copper_annulus_radii_mm=[.5,.85],startpoint_radius_from_via_mm=.2,via_copper_annulus_radii_mm=[.15,.3],note='Polyline joins a point in the GND via annulus to a point in the J1.3 annulus along a diagonal thermal spoke; contour clearance minus half sampling step bounds the entire route. This bound applies to the saved zone contour, not the via drill.')
outline=children(board,'gr_line')
outline_segments=[(nums(s,'start'),nums(s,'end')) for s in outline if one(s,'layer')[1]=='Edge.Cuts']
assert len(outline_segments)==4
report['outline']=dict(segments_mm=outline_segments,nominal_size_mm=[20,12],all_endpoints_degree_two=all(sum(p in e for e in outline_segments)==2 for e in outline_segments for p in e),minimum_copper_to_edge_mm=min(segdist(p,a,b) for item in items for p in item['poly'] for a,b in outline_segments),nominal_J1_annular_ring_mm=.35,nominal_via_annular_ring_mm=.15,qualification='Geometry only; no fabrication process or current/temperature limit assumed.')
Path('/work/experiments/saved_connectivity.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({k:report[k] for k in ['schematic_connector_rotation','copper_components','checks','nominal_ideal_rc','explicit_saved_ground_fill_route','outline']},indent=2))
