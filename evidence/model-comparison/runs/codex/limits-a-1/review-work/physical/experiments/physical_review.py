"""Read-only native review. Run: python3 /work/experiments/physical_review.py"""
import hashlib, json, math, pathlib, platform, re, subprocess
import xml.etree.ElementTree as ET

OUT = pathlib.Path('/work/experiments')
def sexpr(path):
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|[^\s()]+|[()]', path.read_text())
    stack, root = [], None
    for token in tokens:
        if token == '(':
            node = []
            if stack: stack[-1].append(node)
            stack.append(node)
        elif token == ')':
            root = stack.pop()
        else:
            stack[-1].append(json.loads(token) if token.startswith('"') else token)
    assert not stack
    return root
def fields(node, key):
    return [v for v in node if isinstance(v, list) and v[0] == key]
def one(node, key):
    return fields(node, key)[0]
def props(node):
    return {v[1]: v[2] for v in fields(node, 'property')}
def xy(node, key):
    return tuple(map(float, one(node, key)[1:3]))
def uuid(node): return one(node, 'uuid')[1]
def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()

candidate = pathlib.Path('/candidate')
board_path = candidate/'project/rc_filter.kicad_pcb'
sch_path = candidate/'project/rc_filter.kicad_sch'
board, sch = sexpr(board_path), sexpr(sch_path)
symbols = {props(s)['Reference']: s for s in fields(sch, 'symbol')}
r1 = symbols['R1']
assert props(r1)['Value'] == '10k'
assert one(r1, 'dnp')[1] == 'no'
pads = {}
footprints = []
for fp in fields(board, 'footprint'):
    pr = props(fp)
    origin = xy(fp, 'at')
    assert len(one(fp, 'at')) == 3, 'this probe only supports unrotated fixture footprints'
    footprints.append({'ref': pr['Reference'], 'value': pr['Value'], 'position_mm': origin, 'footprint': fp[1]})
    for pad in fields(fp, 'pad'):
        offset = xy(pad, 'at')
        pads[pr['Reference']+'.'+pad[1]] = {'position_mm': tuple(a+b for a,b in zip(origin, offset)), 'net': one(pad, 'net')[1], 'uuid': uuid(pad)}
tracks = [{'start': xy(s,'start'), 'end': xy(s,'end'), 'net': one(s,'net')[1], 'layer': one(s,'layer')[1], 'width_mm': float(one(s,'width')[1]), 'uuid': uuid(s)} for s in fields(board,'segment')]
assert pads['R1.1']['net'] == '/IN' and pads['R1.2']['net'] == '/OUT'
assert props(next(fp for fp in fields(board,'footprint') if props(fp)['Reference']=='R1'))['Value'] == '10k'
def on_segment(p, a, b):
    return abs(math.dist(a,p)+math.dist(p,b)-math.dist(a,b)) < 1e-8
def route(net, start, end):
    segs = [s for s in tracks if s['net']==net]
    assert all(s['layer']=='F.Cu' for s in segs)
    points = {tuple(pads[x]['position_mm']) for x in pads if pads[x]['net']==net}
    points |= {p for s in segs for p in [s['start'],s['end']]}
    graph = {p: [] for p in points}
    for s in segs:
        pts = sorted([p for p in points if on_segment(p,s['start'],s['end'])], key=lambda p: math.dist(p,s['start']))
        for a,b in zip(pts,pts[1:]):
            graph[a].append((b,s['uuid']))
            graph[b].append((a,s['uuid']))
    source, sink = tuple(pads[start]['position_mm']), tuple(pads[end]['position_mm'])
    queue = [(source,[source],[])]
    visited = set()
    for point, path, ids in queue:
        if point == sink:
            return {'from': start, 'to': end, 'points_mm': path, 'segments': ids, 'length_mm': sum(math.dist(a,b) for a,b in zip(path,path[1:]))}
        if point in visited: continue
        visited.add(point)
        queue += [(n,path+[n],ids+[i]) for n,i in graph[point] if n not in visited]
    raise AssertionError('no copper centerline route')
routes = [route('/IN','J1.1','R1.1'), route('/OUT','R1.2','J1.2'), route('/OUT','R1.2','C1.1')]
root = ET.parse('/evidence/work/netlist/design.xml').getroot()
nets = {n.attrib['name']: sorted(x.attrib['ref']+'.'+x.attrib['pin'] for x in n.findall('node')) for n in root.findall('./nets/net')}
assert {net: sorted(key for key,p in pads.items() if p['net']==net) for net in nets} == nets
records = {}
for check in ['erc','drc','netlist','geometry']:
    directory = pathlib.Path('/evidence/work')/check
    execution = json.loads((directory/'execution.json').read_text())
    records[check] = {'argv': execution['argv'], 'exit_code': execution['exit_code'], 'console': (directory/'console.log').read_text(), 'check_record': json.loads((pathlib.Path('/evidence/checks')/(check+'.json')).read_text())}
    assert digest(directory/'console.log') == execution['console_sha256']
for check in ['erc','drc']:
    records[check]['native_report'] = json.loads((pathlib.Path('/evidence/work')/check/(check+'.json')).read_text())
records['geometry']['saved_geometry'] = json.loads(pathlib.Path('/evidence/work/geometry/geometry.json').read_text())
vin, vout, resistance, limit = 12., 0., 10000., .1
current = (vin-vout)/resistance
power = (vin-vout)**2/resistance
result = {
    'command': 'python3 /work/experiments/physical_review.py',
    'versions': {'python': platform.python_version(), 'kicad_cli': subprocess.check_output(['kicad-cli','version'],text=True).strip()},
    'inputs': {str(p): digest(p) for p in [board_path,sch_path,candidate/'requirements.txt']},
    'requirements_text': (candidate/'requirements.txt').read_text(),
    'R1_schematic': {'uuid': uuid(r1), 'properties': props(r1)},
    'footprints': footprints, 'pads': pads, 'tracks': tracks,
    'independent_centerline_routes': routes, 'exported_nets_agree_with_native_pad_assignment': nets,
    'calculation': {'vin_V':vin, 'vout_V':vout, 'R1_ohm':resistance, 'I_A':current, 'P_W':power, 'continuous_limit_W':limit, 'fraction_of_limit':power/limit, 'margin_W':limit-power, 'minimum_resistance_to_meet_limit_ohm':(vin-vout)**2/limit, 'equations':'I=(VIN-VOUT)/R1; P=(VIN-VOUT)^2/R1'},
    'assumptions': ['Specified voltages imposed indefinitely by external test equipment; nominal native R1 value used.', 'External clamp current exits through J1.2; the board ground zone is not a series return element for R1.', 'Copper treated as ideal for this DC resistor power calculation. Passive series copper resistance reduces R1 dissipation for fixed connector voltage difference.', 'No part-specific rating inferred from the 0805 footprint; the exercise supplies the 0.1 W continuous limit.'],
    'outcome': 'R1 dissipates 0.0144 W, below 0.1 W; no supported power-limit defect.',
    'check_records_reviewed': records,
    'limitations': ['Centerline route probe is specific to the unrotated, coplanar fixture and does not replace clearance DRC.', 'Saved zone fill inspected in native file and supplied geometry/DRC records; no refill performed.', 'No physical measurements, thermal model, resistor tolerance/temperature law, manufacturing capability or ampacity qualification; these are not supplied requirements.', 'No transient, AC, unclamped-load or ECU qualification assessment; scope is the specified indefinite DC clamp condition.', 'Native ignored checks remain omitted as listed in supplied ERC/DRC reports; successful exits alone do not verify power.']
}
(OUT/'physical_review.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'calculation':result['calculation'],'routes':routes,'outcome':result['outcome']},indent=2))
