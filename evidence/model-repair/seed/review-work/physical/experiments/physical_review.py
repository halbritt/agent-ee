#!/usr/bin/python3
"""Read-only native inspection; write review results only under experiments."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path('/work/experiments')
commands = []
def run(argv, name):
    env = os.environ.copy()
    env.update(KICAD10_SYMBOL_DIR='/candidate/stock/symbols',
               KICAD10_FOOTPRINT_DIR='/candidate/stock/footprints',
               KICAD10_TEMPLATE_DIR='/candidate/stock/template',
               KICAD_CONFIG_HOME=str(ROOT/'kicad-config'))
    p = subprocess.run(argv, capture_output=True, text=True, cwd=ROOT, env=env)
    (ROOT/(name+'.stdout.txt')).write_text(p.stdout)
    (ROOT/(name+'.stderr.txt')).write_text(p.stderr)
    commands.append(dict(argv=argv, cwd=str(ROOT), exit_code=p.returncode,
                         stdout=name+'.stdout.txt', stderr=name+'.stderr.txt'))
    (ROOT/'commands.json').write_text(json.dumps(commands, indent=2)+'\n')
    return p

run(['/usr/bin/python3', '--version'], 'python-version')
run(['kicad-cli', 'version'], 'kicad-version')
run(['ngspice', '--version'], 'ngspice-version')
native_files = [Path('/candidate/project')/('rc_filter.'+ext)
                for ext in ('kicad_sch','kicad_pcb','kicad_pro')]
before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in native_files}
for kind in ('erc', 'drc'):
    argv = ['kicad-cli', 'sch' if kind=='erc' else 'pcb', kind,
            '--format','json','--severity-all','--units','mm']
    if kind=='drc':
        argv.append('--schematic-parity')
    argv += ['-o',str(ROOT/(kind+'.json')), str(native_files[0 if kind=='erc' else 1])]
    run(argv, kind)
run(['kicad-cli','sch','export','netlist','--format','kicadxml',
     '-o',str(ROOT/'design.xml'),str(native_files[0])], 'netlist')

# Parse original source expressions as an independent cross-check of native values.
def sexpr(path):
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|[()]|[^\s()]+', path.read_text())
    stack = []; result = None
    for t in tokens:
        if t == '(':
            item=[]
            if stack: stack[-1].append(item)
            stack.append(item)
        elif t == ')': result=stack.pop()
        else: stack[-1].append(json.loads(t) if t.startswith('"') else t)
    return result
def children(obj, key): return [x for x in obj if isinstance(x,list) and x and x[0]==key]
def first(obj,key): return children(obj,key)[0]
sch = sexpr(native_files[0]); pcb = sexpr(native_files[1])
sch_values = {}
for s in children(sch,'symbol'):
    props={p[1]:p[2] for p in children(s,'property')}
    if props.get('Reference') in ('R1','C1','J1'):
        sch_values[props['Reference']] = dict(value=props['Value'],uuid=first(s,'uuid')[1])
pcb_values = {}
for f in children(pcb,'footprint'):
    props={p[1]:p[2] for p in children(f,'property')}
    pcb_values[props['Reference']] = dict(value=props['Value'], uuid=first(f,'uuid')[1])

import pcbnew
b=pcbnew.LoadBoard(str(native_files[1]))
b.BuildConnectivity()
c=b.GetConnectivity()
mm=lambda n: pcbnew.ToMM(n)
xy=lambda v: [mm(v.x),mm(v.y)]
padid=lambda p: p.GetParentFootprint().GetReference()+'.'+p.GetNumber()
tracks=[]; vias=[]; pads=[]
for t in b.GetTracks():
    data=dict(uuid=t.m_Uuid.AsString(),net=t.GetNetname(),width_mm=mm(t.GetWidth(pcbnew.F_Cu) if isinstance(t,pcbnew.PCB_VIA) else t.GetWidth()),
              start_mm=xy(t.GetStart()),end_mm=xy(t.GetEnd()),
              connected_pads=sorted(padid(p) for p in c.GetConnectedPads(t)))
    if isinstance(t,pcbnew.PCB_VIA):
        data['drill_mm']=mm(t.GetDrillValue()); vias.append(data)
    else:
        data['layer']=b.GetLayerName(t.GetLayer()); tracks.append(data)
for f in b.GetFootprints():
    for p in f.Pads():
        pads.append(dict(pad=padid(p), net=p.GetNetname(),position_mm=xy(p.GetPosition()),
                         size_mm=xy(p.GetSize()),drill_mm=xy(p.GetDrillSize()),
                         connected_pads=sorted(padid(q) for q in c.GetConnectedPads(p)),
                         connected_tracks=[t.m_Uuid.AsString() for t in c.GetConnectedTracks(p)]))
zones=[]
for z in b.Zones():
    poly=z.GetFilledPolysList(pcbnew.B_Cu)
    zones.append(dict(uuid=z.m_Uuid.AsString(),net=z.GetNetname(),filled=z.IsFilled(),
                      outline_count=poly.OutlineCount(),
                      point_tests=[dict(position_mm=q, in_saved_fill=poly.Contains(
                          pcbnew.VECTOR2I(pcbnew.FromMM(q[0]),pcbnew.FromMM(q[1]))))
                          for q in ([113,100],[100,105.08],[100.65,105.73],
                                    [102,105.08],[112,101],[100,100],[100,102.54])]))
geometry = dict(pcbnew_version=pcbnew.Version(),schematic_values=sch_values,pcb_values=pcb_values,
                tracks=tracks,vias=vias,pads=pads,zones=zones,
                unconnected_count=c.GetUnconnectedCount(False),
                minimum_track_width_mm=min(t['width_mm'] for t in tracks),
                required_minimum_track_width_mm=0.2,
                edge_lines=[dict(start=first(g,'start')[1:],end=first(g,'end')[1:])
                            for g in children(pcb,'gr_line') if first(g,'layer')[1]=='Edge.Cuts'])
(ROOT/'native-inspection.json').write_text(json.dumps(geometry,indent=2)+'\n')

def value(s):
    m=re.fullmatch(r'([0-9.]+)([knum]?)',s)
    return float(m[1])*{'':1,'k':1e3,'n':1e-9,'u':1e-6,'m':1e-3}[m[2]]
R=value(pcb_values['R1']['value']); C=value(pcb_values['C1']['value']); tau=R*C
calc=dict(source='Original saved PCB values, independently compared with schematic instances',
          assumptions=['Ideal 1 V input step at t=0','Zero initial capacitor voltage',
                       'No external output load','Ideal R/C, interconnect treated as ideal wires'],
          formula='Vout(t) = 1 - exp(-t/(R*C))',R_ohm=R,C_farad=C,tau_s=tau,
          vout_1ms=-math.expm1(-.001/tau),vout_5ms=-math.expm1(-.005/tau),
          required_vout_1ms=[.62,.65],
          allowed_tau_s=[-.001/math.log(1-.65),-.001/math.log(1-.62)],
          initial_current_A=1/R,initial_resistor_power_W=1/R,
          note='Nominal ideal stimulus current/power only; no ampacity or thermal qualification inferred.')
(ROOT/'rc-calculation.json').write_text(json.dumps(calc,indent=2)+'\n')

# Independent deck uses PCB-derived values, delayed step and a smaller maximum time step.
# Delay permits an explicit pre-step zero operating state. Measurements are relative to
# the end of the 1 ns ramp; analytic ramp correction is negligible here.
(ROOT/'independent-step.cir').write_text(
    '* Physical review: original PCB values; unloaded ideal RC\n'
    f'Rnative IN OUT {R:.12g}\nCnative OUT 0 {C:.12g}\n'
    'Vdrive IN 0 PWL(0 0 100u 0 100.001u 1 5.100001m 1)\n'
    '.tran 0.1u 5.100001m 0 0.1u\n'
    '.meas tran at_1ms FIND v(OUT) AT=1.100001m\n'
    '.meas tran at_5ms FIND v(OUT) AT=5.100001m\n'
    '.meas tran input_at_1ms FIND v(IN) AT=1.100001m\n.end\n')
run(['ngspice','-n','-b','-o',str(ROOT/'independent-step.log'),
     str(ROOT/'independent-step.cir')], 'independent-step')
after={str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in native_files}
(ROOT/'native-hashes.json').write_text(json.dumps(dict(before=before,after=after,unchanged=before==after),indent=2)+'\n')
print(json.dumps(dict(min_width_mm=geometry['minimum_track_width_mm'],
                     unconnected=geometry['unconnected_count'],calculation=calc,zones=zones),indent=2))
