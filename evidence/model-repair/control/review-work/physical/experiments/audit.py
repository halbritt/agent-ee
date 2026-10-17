"""Read-only physical review of the saved native fixture. No refill/save calls."""
import json, math, pathlib, re, struct, sys, hashlib

ROOT = pathlib.Path('/work/experiments')
def parse(path):
    tokens = iter(re.findall(r'"(?:\\.|[^"\\])*"|[()]|[^\s()]+', pathlib.Path(path).read_text()))
    def value(t):
        if t == '(':
            out = []
            for t in tokens:
                if t == ')': return out
                out.append(value(t))
            raise ValueError('unclosed list')
        return json.loads(t) if t.startswith('"') else t
    return value(next(tokens))
def children(node, name):
    return [n for n in node if isinstance(n, list) and n and n[0] == name]
def child(node, name): return children(node, name)[0]
def xy(node, name): return tuple(map(float, child(node, name)[1:3]))
def prop(node, name): return next(n[2] for n in children(node, 'property') if n[1] == name)
def dist_segment(p, a, b):
    v = (b[0]-a[0], b[1]-a[1]); w = (p[0]-a[0], p[1]-a[1])
    l2 = v[0]**2+v[1]**2
    t = min(1, max(0, (v[0]*w[0]+v[1]*w[1])/l2)) if l2 else 0
    return math.hypot(w[0]-t*v[0], w[1]-t*v[1])
def inside(p, poly):
    # Odd-even rule handles the saved bridged clearances without refilling.
    x,y = p; result = False
    for a,b in zip(poly, poly[1:]+poly[:1]):
        if (a[1]>y) != (b[1]>y):
            cross = a[0]+(y-a[1])*(b[0]-a[0])/(b[1]-a[1])
            if x < cross: result = not result
    return result
def boundary_distance(p, poly):
    return min(dist_segment(p,a,b) for a,b in zip(poly,poly[1:]+poly[:1]))

pcbpath = pathlib.Path('/candidate/project/rc_filter.kicad_pcb')
pcb = parse(pcbpath)
sch = parse('/candidate/project/rc_filter.kicad_sch')
tracks = children(pcb, 'segment')
footprints = children(pcb, 'footprint')
pads = []
for fp in footprints:
    origin = xy(fp,'at')
    assert len(child(fp,'at')) == 3, 'This audit only handles the unrotated fixture footprints'
    for pad in children(fp,'pad'):
        offset = xy(pad,'at')
        pads.append(dict(ref=prop(fp,'Reference'),pad=pad[1],net=child(pad,'net')[1],
                         pos=[origin[0]+offset[0],origin[1]+offset[1]],size=xy(pad,'size'),
                         kind=pad[2], shape=pad[3],layers=child(pad,'layers')[1:]))
segments = [dict(uuid=child(t,'uuid')[1],net=child(t,'net')[1],start=xy(t,'start'),end=xy(t,'end'),
                 width_mm=float(child(t,'width')[1]),layer=child(t,'layer')[1]) for t in tracks]
zone = child(pcb,'zone')
polys = [[tuple(map(float,p[1:])) for p in child(f,'pts')[1:]] for f in children(zone,'filled_polygon')]
assert len(polys) == 1
poly = polys[0]
via = child(pcb,'via'); vp = xy(via,'at')
gpad = next(p for p in pads if p['ref']=='J1' and p['pad']=='3')
gc = gpad['pos']; gr = gpad['size'][0]/2
spokes=[]
for angle in range(0,360,45):
    a=math.radians(angle)
    samples=[(gc[0]+r*math.cos(a),gc[1]+r*math.sin(a)) for r in [0.6,0.7,0.8,0.9,1.0,1.1,1.2,1.3,1.4,1.5]]
    spokes.append(dict(angle_deg=angle,all_samples_in_saved_fill=all(inside(p,poly) for p in samples)))
# Simple connected path witness: a thermal spoke crosses the copper annulus
# (r=.5 to .85 mm), and extends to bulk copper. Sample at 1 um spacing.
witness=[(gc[0]+r/math.sqrt(2),gc[1]+r/math.sqrt(2)) for r in [0.6,1.5]]
witness += [(113,106.2),vp]
path_ok = all(inside((a[0]+(b[0]-a[0])*t/10000,a[1]+(b[1]-a[1])*t/10000),poly)
              for a,b in zip(witness,witness[1:]) for t in range(10001) if a!=b)
endpoint_contacts=[]
for s in segments:
    for p in pads:
        if p['net']==s['net'] and dist_segment(p['pos'],s['start'],s['end'])<1e-9:
            endpoint_contacts.append(dict(track=s['uuid'],pad=p['ref']+'.'+p['pad']))
out_segments=[s for s in segments if s['net']=='/OUT']
out_T_contact=any(dist_segment((107.5,100),s['start'],s['end'])<1e-9 for s in out_segments if s['start']==(105.9125,100))
values = {prop(s,'Reference'):prop(s,'Value') for s in children(sch,'symbol')}
assert values['R1']=='10k' and values['C1']=='100n'
R=10000.; C=100e-9; tau=R*C; tr=1e-9
def expected(t):
    if t<=tr: return (t+tau*math.expm1(-t/tau))/tr
    factor=-math.expm1(-tr/tau)/(tr/tau)
    return 1-factor*math.exp(-(t-tr)/tau)
raw=pathlib.Path('/evidence/work/rc-step/result.raw').read_bytes()
header, payload=raw.split(b'Binary:\n',1)
header=header.decode()
nv=int(re.search(r'No. Variables:\s*(\d+)',header)[1]); np=int(re.search(r'No. Points:\s*(\d+)',header)[1])
assert nv==4 and len(payload)==8*nv*np
rows=list(struct.iter_unpack('<4d',payload))
errors=[abs(row[2]-expected(row[0])) for row in rows]
measurements={}
for t in [1e-3,5e-3]:
    a,b=next((a,b) for a,b in zip(rows,rows[1:]) if a[0]<=t<=b[0])
    val=a[2]+(b[2]-a[2])*(t-a[0])/(b[0]-a[0])
    measurements[str(t)]=dict(ideal_instantaneous_step_v=1-math.exp(-t/tau), finite_1ns_ramp_v=expected(t), raw_interpolated_v=val)
result=dict(
    assumptions=['Unrotated native footprint geometry; mm units.',
                 'Odd-even point-in-polygon evaluation of saved filled_polygon vertices; no refill.',
                 'Ground path witness sampled at <=1 um on thermal spoke, <=1.4 um on bulk segments; this is a geometric connectivity witness, not field or thermal analysis.',
                 'Raw transient file interpreted as little-endian real doubles in declared variable order time,v(in),v(out),i(vstep).',
                 'Ideal nominal R=10k, C=100n from native schematic; no load or parasitic model.'],
    board_sha256=hashlib.sha256(pcbpath.read_bytes()).hexdigest(),
    native_pads=pads, native_segments=segments,
    minimum_track_width_mm=min(s['width_mm'] for s in segments),
    all_tracks_meet_0_20_mm=all(s['width_mm']>=.2 for s in segments),
    track_pad_center_contacts=endpoint_contacts, output_T_junction_present=out_T_contact,
    saved_ground_fill=dict(net=child(zone,'net')[1],layer=child(zone,'layer')[1],vertices=len(poly),
                           via_center_in_fill=inside(vp,poly),via_center_distance_to_boundary_mm=boundary_distance(vp,poly),
                           j1_pad3_radius_mm=gr,thermal_radial_samples=spokes,
                           pad3_to_via_sampled_path_inside_fill=path_ok,
                           pad1_center_in_fill=inside((100,100),poly),pad2_center_in_fill=inside((100,102.54),poly)),
    geometry_measurements=dict(board_size_mm=[20,12],
                               via_annular_ring_mm=(float(child(via,'size')[1])-float(child(via,'drill')[1]))/2,
                               header_annular_ring_mm=(1.7-1)/2,
                               nominal_min_saved_pour_edge_setback_mm=min(min(x-96,116-x,y-96,108-y) for x,y in poly),
                               resistor_capacitor_courtyard_gap_mm=(110-1.7)-(105+1.68)),
    response=dict(native_values=values,tau_s=tau,raw_header=header,points=np,
                  payload_size_checked=True,first_sample=rows[0],last_sample=rows[-1],
                  input_sample_range_v=[min(r[1] for r in rows),max(r[1] for r in rows)],
                  max_raw_error_vs_exact_finite_ramp_v=max(errors),measurements=measurements,
                  ideal_initial_current_a=1/R,initial_resistor_power_w=1/R,
                  final_capacitor_energy_j=.5*C),
    limitations=['No copper thickness, material, part ratings or thermal boundaries assumed; no ampacity/thermal qualification.',
                 'Manufacturing process capability and actual assembly fit not qualified by nominal geometry.',
                 'Thermal witness sampling does not prove every possible contour is valid; native DRC remains separate evidence.'])
(ROOT/'audit.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
