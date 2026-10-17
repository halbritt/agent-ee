#!/usr/bin/python3
"""Read-only native parsing and geometric centerline trace; no candidate edits/refill."""
import hashlib, json, math, pathlib, re, subprocess, sys
import xml.etree.ElementTree as ET
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.path import Path
from matplotlib.patches import Circle, Rectangle, Polygon
BASE=pathlib.Path('/candidate'); OUT=pathlib.Path('/work/experiments')
def parse(p):
    toks=re.findall(r'"(?:\\.|[^"\\])*"|[^\s()]+|[()]',p.read_text())
    root=[]; stack=[root]
    for t in toks:
        if t=='(': a=[]; stack[-1].append(a); stack.append(a)
        elif t==')': stack.pop()
        else: stack[-1].append(json.loads(t) if t.startswith('"') else t)
    assert len(stack)==1
    return root[0]
def children(x,k): return [y for y in x[1:] if isinstance(y,list) and y[0]==k]
def one(x,k,default=None):
    a=children(x,k); return a[0] if a else default
def xy(x): return tuple(map(float,x[1:3]))
def prop(x,n): return next(y[2] for y in children(x,'property') if y[1]==n)
def distseg(p,a,b):
    dx,dy=b[0]-a[0],b[1]-a[1]; l=dx*dx+dy*dy
    t=((p[0]-a[0])*dx+(p[1]-a[1])*dy)/l if l else 0
    t=max(0,min(1,t)); return math.dist(p,(a[0]+t*dx,a[1]+t*dy))
sch=parse(BASE/'project/rc_filter.kicad_sch'); pcb=parse(BASE/'project/rc_filter.kicad_pcb')
report={'versions':{'python':sys.version,'matplotlib':matplotlib.__version__,'kicad_cli':subprocess.check_output(['kicad-cli','version'],text=True).strip()},'sha256':{},'components':{},'checks':{}}
for f in ['requirements.txt','project/rc_filter.kicad_sch','project/rc_filter.kicad_pcb','project/rc_filter.kicad_pro']:
    report['sha256'][f]=hashlib.sha256((BASE/f).read_bytes()).hexdigest()
for kind in ['erc','drc','netlist','geometry']:
    p=pathlib.Path('/evidence/checks')/(kind+'.json'); report['checks'][kind]=json.loads(p.read_text())
report['native_reports']={kind:json.loads((pathlib.Path('/evidence/work')/kind/(kind+'.json')).read_text()) for kind in ['erc','drc']}
xml=ET.parse('/evidence/work/netlist/design.xml').getroot()
report['exported_xml_nets']={n.attrib['name']:[v.attrib['ref']+'.'+v.attrib['pin'] for v in n.findall('node')] for n in xml.findall('./nets/net')}
syms={prop(s,'Reference'):s for s in children(sch,'symbol')}
pads={}; fps={}
for fp in children(pcb,'footprint'):
    ref=prop(fp,'Reference'); fps[ref]=fp; at=one(fp,'at'); assert len(at)==3, 'This review parser requires unrotated footprints'
    pos=xy(at); attr=one(fp,'attr')[1:]; s=syms[ref]
    report['components'][ref]={'value':prop(fp,'Value'),'board_attributes':attr,'board_dnp':'dnp' in attr,'schematic_dnp':one(s,'dnp')[1], 'schematic_in_bom':one(s,'in_bom')[1], 'schematic_on_board':one(s,'on_board')[1], 'pads':{}}
    for pad in children(fp,'pad'):
        q=xy(one(pad,'at')); q=(q[0]+pos[0],q[1]+pos[1]); n=ref+'.'+pad[1]
        pads[n]={'position':q,'size':xy(one(pad,'size')),'shape':pad[3],'net':one(pad,'net')[1],'layers':one(pad,'layers')[1:],'drill':float(one(pad,'drill',['drill',0])[1])}
        report['components'][ref]['pads'][pad[1]]=pads[n]
tracks=[{'a':xy(one(s,'start')),'b':xy(one(s,'end')),'width':float(one(s,'width')[1]),'net':one(s,'net')[1],'layer':one(s,'layer')[1]} for s in children(pcb,'segment')]
# Make a geometry-only graph from centerlines, independently of assigned net names.
# All F.Cu joins in this fixture occur at segment endpoints/pad centers or an endpoint on a segment.
assert all(t['layer']=='F.Cu' for t in tracks)
points=set(p['position'] for p in pads.values())
for t in tracks: points.update([t['a'],t['b']])
report['xml_vs_native_pad_net_membership_agrees']=all(sorted(nodes)==sorted(n for n,p in pads.items() if p['net']==net) for net,nodes in report['exported_xml_nets'].items())
graph={p:set() for p in points}
for t in tracks:
    hit=[p for p in points if distseg(p,t['a'],t['b'])<1e-8]
    hit.sort(key=lambda p:math.dist(p,t['a']))
    for a,b in zip(hit,hit[1:]): graph[a].add(b); graph[b].add(a)
def path(g,a,b):
    queue=[(a,[a])]; seen=set()
    while queue:
        p,seq=queue.pop(0)
        if p==b:return seq
        if p in seen:continue
        seen.add(p)
        queue.extend((q,seq+[q]) for q in sorted(g[p]) if q not in seen)
    return None
start=pads['J1.1']['position']; end=pads['J1.2']['position']
report['copper_only_IN_to_OUT_path']=path(graph,start,end)
r1=report['components']['R1']; assert r1['schematic_dnp']=='no' and not r1['board_dnp']
a=pads['R1.1']['position']; b=pads['R1.2']['position']
graph[a].add(b); graph[b].add(a)
report['fitted_R1_IN_to_OUT_path']=path(graph,start,end)
report['nominal_resistive_path_ohms']=10000
report['resistance_assumption']='Native R1 value 10k; ignores trace/contact resistance. This is topology analysis, not physical resistance measurement.'
report['track_centerline_lengths_mm']=[dict(t,length_mm=math.dist(t['a'],t['b'])) for t in tracks]
report['R1_exposed_pad_edge_gap_mm']=abs(a[0]-b[0])-(pads['R1.1']['size'][0]+pads['R1.2']['size'][0])/2
# Examine only saved fill; never call a zone filler.
zone=children(pcb,'zone')[0]; fill=one(zone,'filled_polygon'); vertices=[xy(v) for v in one(fill,'pts')[1:]]
fillpath=Path(vertices,closed=True)
report['saved_zone']={'layer':one(fill,'layer')[1],'net':one(zone,'net')[1],'vertices':len(vertices),'annulus_overlap_samples':{}}
for name,center,inner,outer in [('J1.3',pads['J1.3']['position'],0.5,0.85),('GND_via',xy(one(children(pcb,'via')[0],'at')),0.15,0.3)]:
    tests=[(center[0]+r*math.cos(t*math.pi/180),center[1]+r*math.sin(t*math.pi/180)) for r in [inner+(outer-inner)*0.25,inner+(outer-inner)*0.75] for t in range(360)]
    hits=fillpath.contains_points(tests)
    report['saved_zone']['annulus_overlap_samples'][name]={'inside_saved_fill':int(sum(hits)),'total':len(tests),'meaning':'Positive sampled overlap between saved fill and plated copper annulus, not an ampacity qualification.'}
report['board_outline']=[{'a':xy(one(s,'start')),'b':xy(one(s,'end'))} for s in children(pcb,'gr_line') if one(s,'layer')[1]=='Edge.Cuts']
report['omissions']=['No physical board/assembly resistance or thermal measurements.','No fabrication-specific capability, copper thickness, plating, part rating or temperature qualification.','No zone refill; saved polygon only.','Centerline graph is fixture-specific and does not prove general polygon clearance. Native DRC reviewed separately.','No SPICE stimulus simulation: no load, voltage, bandwidth, or operating envelope is specified; criterion evaluated as fitted resistive topology.','Saved ERC/DRC ignored checks remain excluded; no claim of all-rules coverage.']
(OUT/'physical_results.json').write_text(json.dumps(report,indent=2)+'\n')
fig,ax=plt.subplots(figsize=(10,6)); ax.add_patch(Polygon(vertices,closed=True,color='#acdae8',alpha=.7))
for t in tracks:ax.plot([t['a'][0],t['b'][0]],[t['a'][1],t['b'][1]],color='#b33535',linewidth=t['width']*8,solid_capstyle='round')
for n,p in pads.items():
    x,y=p['position']; w,h=p['size']
    ax.add_patch(Circle((x,y),w/2,color='#bb9a30') if p['shape']=='circle' else Rectangle((x-w/2,y-h/2),w,h,color='#bb9a30'))
    if p['drill']:ax.add_patch(Circle((x,y),p['drill']/2,color='white'))
    ax.text(x,y+.95,n,fontsize=8,ha='center')
for e in report['board_outline']: ax.plot([e['a'][0],e['b'][0]],[e['a'][1],e['b'][1]],color='black')
ax.add_patch(Circle((113,100),.3,color='#bb9a30'));ax.add_patch(Circle((113,100),.15,color='white'))
ax.plot([104.6,105.4],[100,100],color='black',linewidth=5);ax.text(105,98.4,'Fitted R1: 10k',ha='center')
ax.set(xlim=(95,117),ylim=(109,95),xlabel='x (mm)',ylabel='y (mm)',title='Native saved geometry (F.Cu red, B.Cu fill blue); schematic R1 bridge black')
ax.set_aspect('equal');fig.tight_layout();fig.savefig(OUT/'saved_geometry.png',dpi=150)
print(json.dumps({k:report[k] for k in ['versions','sha256','copper_only_IN_to_OUT_path','fitted_R1_IN_to_OUT_path','nominal_resistive_path_ohms','R1_exposed_pad_edge_gap_mm','saved_zone']},indent=2))
