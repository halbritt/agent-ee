#!/usr/bin/env python3
"""Read-only native geometry audit and independent RC calculation."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path('/work/experiments')
NATIVE = Path('/candidate/project')
records = []

def run(argv, name):
    env = dict(os.environ, KICAD_CONFIG_HOME=str(ROOT / 'kicad-config'))
    p = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, text=True)
    (ROOT / (name + '.stdout.txt')).write_text(p.stdout)
    (ROOT / (name + '.stderr.txt')).write_text(p.stderr)
    records.append(dict(argv=argv, cwd=str(ROOT), exit_code=p.returncode,
                        stdout=name+'.stdout.txt', stderr=name+'.stderr.txt'))
    return p

def parse(path):
    tokens = iter(re.findall(r'\(|\)|"(?:\\.|[^"\\])*"|[^\s()]+', path.read_text()))
    def item(token):
        if token == '(':
            out = []
            for tok in tokens:
                if tok == ')':
                    return out
                out.append(item(tok))
            raise ValueError('unclosed expression')
        return json.loads(token) if token.startswith('"') else token
    return item(next(tokens))

def children(node, key):
    return [v for v in node if isinstance(v, list) and v and v[0] == key]

def child(node, key):
    return children(node, key)[0]

def xy(node, key):
    return list(map(float, child(node, key)[1:3]))

def prop(node, name):
    return next(p[2] for p in children(node, 'property') if p[1] == name)

def in_poly(p, poly):
    # Even/odd ray test, including exact edge points; no polygon repair/refill.
    x, y = p
    inside = False
    for a, b in zip(poly, poly[1:] + poly[:1]):
        ax, ay = a
        bx, by = b
        cross = (x-ax)*(by-ay) - (y-ay)*(bx-ax)
        if abs(cross) < 1e-12 and min(ax,bx) <= x <= max(ax,bx) and min(ay,by) <= y <= max(ay,by):
            return True
        if (ay > y) != (by > y) and x < ax + (y-ay)*(bx-ax)/(by-ay):
            inside = not inside
    return inside

run(['kicad-cli', 'version'], 'kicad-version')
run(['ngspice', '--version'], 'ngspice-version')
run(['kicad-cli','pcb','drc','--format','json','--severity-all','--schematic-parity',
     '--units','mm','-o',str(ROOT/'independent-drc.json'),str(NATIVE/'rc_filter.kicad_pcb')], 'drc')
run(['kicad-cli','sch','export','netlist','--format','kicadxml','-o',
     str(ROOT/'independent-netlist.xml'),str(NATIVE/'rc_filter.kicad_sch')], 'netlist')

board = parse(NATIVE/'rc_filter.kicad_pcb')
sch = parse(NATIVE/'rc_filter.kicad_sch')
report = dict(python=sys.version, source_sha256={})
for path in [NATIVE/'rc_filter.kicad_pcb',NATIVE/'rc_filter.kicad_sch',NATIVE/'rc_filter.kicad_pro',
             Path('/candidate/requirements/fixture-requirements.md')]:
    report['source_sha256'][str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
report['schematic_values'] = {prop(n,'Reference'):prop(n,'Value') for n in children(sch,'symbol')}
report['footprints'] = []
for fp in children(board,'footprint'):
    origin = xy(fp,'at')
    assert len(child(fp,'at')) == 3, 'audit assumes zero footprint rotation'
    pads=[]
    for pad in children(fp,'pad'):
        offset=xy(pad,'at')
        pads.append(dict(number=pad[1], shape=pad[3], net=child(pad,'net')[1],
                         center_mm=[origin[i]+offset[i] for i in (0,1)],size_mm=xy(pad,'size')))
    report['footprints'].append(dict(ref=prop(fp,'Reference'),value=prop(fp,'Value'),pads=pads))
report['segments'] = [dict(uuid=child(n,'uuid')[1],net=child(n,'net')[1],
                           layer=child(n,'layer')[1],start_mm=xy(n,'start'),end_mm=xy(n,'end'),
                           width_mm=float(child(n,'width')[1])) for n in children(board,'segment')]
report['track_width_check'] = dict(required_mm=.2, count=len(report['segments']),
                                  minimum_mm=min(s['width_mm'] for s in report['segments']),
                                  all_pass=all(s['width_mm']>=.2 for s in report['segments']))
report['outline'] = [dict(start_mm=xy(n,'start'),end_mm=xy(n,'end')) for n in children(board,'gr_line')
                     if child(n,'layer')[1]=='Edge.Cuts']

zone=child(board,'zone')
polygon=[list(map(float,p[1:3])) for p in child(child(zone,'filled_polygon'),'pts')[1:]]
angles=[0,45,90,135,180,225,270,315]
report['ground_fill'] = dict(uuid=child(zone,'uuid')[1],layer=child(zone,'layer')[1],
                            saved_polygon_vertices=len(polygon))
report['ground_fill']['J1_3_zone_membership_at_radius_1mm'] = {
    str(a):in_poly([100+math.cos(math.radians(a)),105.08+math.sin(math.radians(a))],polygon) for a in angles}
# Annular copper is modeled without counting drill centers as conducting material.
def ground_copper(p):
    r=math.hypot(p[0]-100,p[1]-105.08)
    return .5 < r <= .85 or in_poly(p, polygon)

start=[100+.6/math.sqrt(2),105.08+.6/math.sqrt(2)]
end=[100+1.6/math.sqrt(2),105.08+1.6/math.sqrt(2)]
waypoints=[start,end,[113,end[1]],[113,100.2]]
route=[]
for a,b in zip(waypoints,waypoints[1:]):
    steps=math.ceil(math.dist(a,b)/.005)
    route.extend([[a[j]+(b[j]-a[j])*i/steps for j in (0,1)] for i in range(steps+1)])
report['ground_fill']['J1_3_to_via_route'] = dict(waypoints_mm=waypoints,
    sample_spacing_max_mm=.005,samples=len(route),all_on_copper=all(map(ground_copper,route)),
    failed_points=[p for p in route if not ground_copper(p)][:10],
    limitation='Sampled geometric continuity; not thermal/ampacity qualification. Starts and ends on annuli, outside drills.')
report['ground_fill']['via_annulus_in_zone']={str(a):in_poly([113+.2*math.cos(math.radians(a)),100+.2*math.sin(math.radians(a))],polygon) for a in angles}

xml=ET.parse(ROOT/'independent-netlist.xml').getroot()
report['fresh_export_nets']={n.get('name'):[p.get('ref')+'.'+p.get('pin') for p in n.findall('node')] for n in xml.findall('./nets/net')}
# Values read independently from the native board/schematic, not the supplied deck.
assert report['schematic_values']['R1']=='100k'
assert report['schematic_values']['C1']=='100n'
R=100000.
C=100e-9
tau=R*C
report['rc_calculation']=dict(R_ohm=R,C_F=C,tau_s=tau,
    formula='Vout(t) = 1 V * (1 - exp(-t/(R*C))), initial Vc=0, ideal unloaded step',
    vout_1ms_V=-math.expm1(-.001/tau),vout_5ms_V=-math.expm1(-.005/tau),
    required_vout_1ms_V=[.62,.65],passes_1ms=False,
    allowable_tau_s=[-.001/math.log(1-.65),-.001/math.log(1-.62)],
    step_current_initial_A=1/R,resistor_peak_power_W=1/R,
    stimulus_note='Supplied PWL rise is 1 ns; maximum error from 0..1 ns step timing uncertainty at 1 ms is below 1e-7 V.')
report['check_records']={name:json.loads(Path('/evidence/work',name,filename).read_text()) for name,filename in [
    ('erc','erc.json'),('drc','drc.json')]}
(ROOT/'native-audit.json').write_text(json.dumps(report,indent=2)+'\n')
(ROOT/'commands.json').write_text(json.dumps(records,indent=2)+'\n')
print(json.dumps({k:report[k] for k in ['track_width_check','ground_fill','fresh_export_nets','rc_calculation']},indent=2))
