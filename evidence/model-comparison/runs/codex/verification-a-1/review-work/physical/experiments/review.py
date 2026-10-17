#!/usr/bin/env python3
"""Read-only candidate review; all generated outputs remain beside this script."""
import hashlib, json, math, os, platform, re, subprocess, sys
from pathlib import Path
import xml.etree.ElementTree as ET

OUT = Path('/work/experiments')
ENV = dict(os.environ, KICAD_CONFIG_HOME=str(OUT/'kicad-config'),
           KICAD10_SYMBOL_DIR='/candidate/stock/symbols',
           KICAD10_FOOTPRINT_DIR='/candidate/stock/footprints',
           KICAD10_TEMPLATE_DIR='/candidate/stock/template')
commands = []
def run(name, argv):
    p = subprocess.run(argv, cwd=OUT, env=ENV, capture_output=True, text=True)
    (OUT/(name+'.stdout.txt')).write_text(p.stdout)
    (OUT/(name+'.stderr.txt')).write_text(p.stderr)
    commands.append(dict(name=name, argv=argv, exit_code=p.returncode,
                         stdout=name+'.stdout.txt', stderr=name+'.stderr.txt'))
    (OUT/'commands.json').write_text(json.dumps(commands, indent=2))
    return p

def parse(path):
    toks = re.findall(r'"(?:\\.|[^"\\])*"|\(|\)|[^\s()]+', Path(path).read_text())
    stack = [[]]
    for t in toks:
        if t == '(':
            x=[]; stack[-1].append(x); stack.append(x)
        elif t == ')': stack.pop()
        else: stack[-1].append(json.loads(t) if t.startswith('"') else t)
    assert len(stack)==1
    return stack[0][0]
def items(node, key): return [v for v in node if isinstance(v,list) and v and v[0]==key]
def item(node, key): return items(node,key)[0]
def val(node,key): return item(node,key)[1]
def xy(node,key): return tuple(map(float,item(node,key)[1:3]))
def inside(p, pts):
    x,y=p; result=False
    for (a,b),(c,d) in zip(pts,pts[1:]+pts[:1]):
        if (b>y)!=(d>y) and x < (c-a)*(y-b)/(d-b)+a: result=not result
    return result
def distance(p,a,b):
    dx,dy=b[0]-a[0],b[1]-a[1]
    t=max(0,min(1,((p[0]-a[0])*dx+(p[1]-a[1])*dy)/(dx*dx+dy*dy)))
    return math.hypot(p[0]-a[0]-t*dx,p[1]-a[1]-t*dy)

versions={'python':sys.version, 'platform':platform.platform()}
versions['kicad']=run('kicad_version',['kicad-cli','--version']).stdout.strip()
versions['ngspice']=run('ngspice_version',['ngspice','--version']).stdout.strip()
(OUT/'versions.json').write_text(json.dumps(versions,indent=2))
sources=[Path('/candidate/project')/('rc_filter.'+s) for s in ['kicad_pcb','kicad_sch','kicad_pro']]
hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
run('drc',['kicad-cli','pcb','drc','--format','json','--severity-all','--schematic-parity','--units','mm','-o',str(OUT/'drc.json'),str(sources[0])])
run('erc',['kicad-cli','sch','erc','--format','json','--severity-all','--units','mm','-o',str(OUT/'erc.json'),str(sources[1])])
run('netlist',['kicad-cli','sch','export','netlist','--format','kicadxml','-o',str(OUT/'design.xml'),str(sources[1])])
run('board_plot',['kicad-cli','pcb','export','svg','--layers','F.Cu,B.Cu,Edge.Cuts,F.CrtYd','--mode-single','--page-size-mode','2','--exclude-drawing-sheet','-o',str(OUT/'saved-board.svg'),str(sources[0])])

board=parse(sources[0]); sch=parse(sources[1])
pads=[]; footprints=[]
for fp in items(board,'footprint'):
    props={x[1]:x[2] for x in items(fp,'property')}; pos=xy(fp,'at')
    assert len(item(fp,'at'))==3, 'This simple extractor assumes unrotated footprints'
    ref=props['Reference']; footprints.append({'ref':ref,'value':props['Value'],'position_mm':pos})
    for pad in items(fp,'pad'):
        local=xy(pad,'at'); at=tuple(a+b for a,b in zip(pos,local))
        pads.append(dict(id=ref+'.'+pad[1], net=val(pad,'net'),at=at,
                         size=xy(pad,'size'), kind=pad[2],shape=pad[3]))
tracks=[dict(id=val(t,'uuid'),net=val(t,'net'),a=xy(t,'start'),b=xy(t,'end'),width_mm=float(val(t,'width')),layer=val(t,'layer')) for t in items(board,'segment')]
vias=[dict(id=val(v,'uuid'),net=val(v,'net'),at=xy(v,'at'),diameter_mm=float(val(v,'size')),drill_mm=float(val(v,'drill'))) for v in items(board,'via')]
zone=item(board,'zone'); filled=item(zone,'filled_polygon'); pts=[tuple(map(float,x[1:3])) for x in item(filled,'pts')[1:]]
assert val(zone,'net')=='GND' and val(zone,'layer')=='B.Cu'

# Independent geometric attachment tests, including a track's mid-segment T junction.
touches={p['id']:[t['id'] for t in tracks if t['net']==p['net'] and distance(p['at'],t['a'],t['b'])<1e-8] for p in pads}
tjunction=distance((107.5,100),tracks[1]['a'],tracks[1]['b'])<1e-8
via_inside=inside(vias[0]['at'],pts)
gpad=next(p for p in pads if p['id']=='J1.3')
def ground_copper(p):
    radius=math.dist(p,gpad['at'])
    return radius>=.5 and (inside(p,pts) or radius<gpad['size'][0]/2)
def sample_path(vertices, predicate, step=.01):
    failures=[]; count=0
    for a,b in zip(vertices,vertices[1:]):
        n=max(1,math.ceil(math.dist(a,b)/step))
        for i in range(n+1):
            p=tuple(x+(y-x)*i/n for x,y in zip(a,b)); count+=1
            if not predicate(p): failures.append(p)
    return dict(sample_spacing_max_mm=step, samples=count,failed_samples=len(failures),first_failures=failures[:5])
# Four diagonal thermal directions from pad annulus to surrounding saved zone.
thermals=[]
for sx,sy in [(1,1),(1,-1),(-1,1),(-1,-1)]:
    a=(100+sx*.55/math.sqrt(2),105.08+sy*.55/math.sqrt(2))
    b=(100+sx*1.6/math.sqrt(2),105.08+sy*1.6/math.sqrt(2))
    thermals.append(sample_path([a,b],ground_copper))
ground_path=[(100+.55/math.sqrt(2),105.08+.55/math.sqrt(2)),(101.5,106.58),(113,106.58),(113,100)]
return_test=sample_path(ground_path,ground_copper)
outline=[(xy(x,'start'),xy(x,'end')) for x in items(board,'gr_line') if val(x,'layer')=='Edge.Cuts']
min_pad_edge=min(min(p['at'][0]-p['size'][0]/2-96,116-p['at'][0]-p['size'][0]/2,p['at'][1]-p['size'][1]/2-96,108-p['at'][1]-p['size'][1]/2) for p in pads)
courtyards={}
for fp in items(board,'footprint'):
    ref=next(x[2] for x in items(fp,'property') if x[1]=='Reference'); pos=xy(fp,'at')
    rect=next(x for x in items(fp,'fp_rect') if val(x,'layer')=='F.CrtYd')
    a=xy(rect,'start'); b=xy(rect,'end')
    courtyards[ref]=[pos[0]+a[0],pos[1]+a[1],pos[0]+b[0],pos[1]+b[1]]
courtyard_gaps={}
for a,b in [('J1','R1'),('R1','C1'),('J1','C1')]:
    aa,bb=courtyards[a],courtyards[b]
    courtyard_gaps[a+'-'+b]=max(bb[0]-aa[2],aa[0]-bb[2],bb[1]-aa[3],aa[1]-bb[3])
native_nets={}
for p in pads: native_nets.setdefault(p['net'],[]).append(p['id'])
root=ET.parse(OUT/'design.xml').getroot()
sch_nets={n.attrib['name']:sorted(x.attrib['ref']+'.'+x.attrib['pin'] for x in n.findall('node')) for n in root.findall('nets/net')}
values={c.attrib['ref']:c.findtext('value') for c in root.findall('components/comp')}
geometry=dict(source_hashes=hashes,footprints=footprints,pads=pads,tracks=tracks,vias=vias,
              pcb_pad_nets={k:sorted(v) for k,v in native_nets.items()},schematic_nets=sch_nets,
              pcb_and_schematic_pad_net_equality={k:sorted(v) for k,v in native_nets.items()}==sch_nets,
              track_centerline_attachments=touches, out_track_T_junction=tjunction,
              saved_ground_polygon_vertices=len(pts),via_center_inside_saved_fill=via_inside,
              saved_ground_thermal_samples=thermals,saved_ground_return_path_mm=ground_path,
              saved_ground_return_samples=return_test,outline_edges_mm=outline,
              board_size_mm=[20,12],minimum_pad_copper_to_outline_mm=min_pad_edge,
              courtyard_rectangles_mm=courtyards,courtyard_separating_gaps_mm=courtyard_gaps,
              limitations=['No refill; saved polygon tested directly.', 'Path sampling at 0.01 mm establishes sampled continuity, not a fabrication or tolerance qualification.', 'Centerline tests used only for the simple, unrotated native geometry; native DRC separately checks clearances.'])
(OUT/'geometry-analysis.json').write_text(json.dumps(geometry,indent=2))

assert values['R1']=='10k' and values['C1']=='100n'
R=10000.; C=100e-9; t=.001; tau=R*C; v=1-math.exp(-t/tau)
calc=dict(source='design.xml, components R1/C1 and nets /IN,/OUT,GND',R_ohm=R,C_farad=C,
          assumptions=['Ideal 1 V step at t=0 relative to GND','C1 initially discharged','No output load; ideal R,C and interconnect'],
          equation='Vout(t)=1-exp(-t/(R*C))',tau_s=tau,time_s=t,Vout_volt=v,
          permitted_volt=[.62,.65],passes=.62<=v<=.65,
          required_tau_interval_s=[-t/math.log(1-.65),-t/math.log(1-.62)],
          initial_current_amp=1/R,initial_resistor_power_watt=1/R,final_capacitor_energy_joule=.5*C)
(OUT/'step-calculation.json').write_text(json.dumps(calc,indent=2))
# With UIC and IC=0, a constant 1 V source is the post-step condition, avoiding finite-rise/delay ambiguity.
deck='''Ideal unloaded native RC, post-step initial condition
Vstep in 0 DC 1
R1 in out 10k
C1 out 0 100n IC=0
.options reltol=1e-8 abstol=1e-14 vntol=1e-10
.tran 0.1u 1.1m 0 0.1u UIC
.measure tran vout_1ms FIND v(out) AT=1m
.end
'''
(OUT/'step.cir').write_text(deck)
run('step',['ngspice','-b',str(OUT/'step.cir')])
assert hashes=={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
print(json.dumps({'geometry':{k:geometry[k] for k in ['pcb_and_schematic_pad_net_equality','out_track_T_junction','via_center_inside_saved_fill','saved_ground_thermal_samples','saved_ground_return_samples','minimum_pad_copper_to_outline_mm']},'step':calc},indent=2))
