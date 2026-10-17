"""Read-only physical review. Run: /usr/bin/python3 /work/experiments/review.py"""
import hashlib, json, math, re, struct, subprocess, sys
from pathlib import Path
import xml.etree.ElementTree as ET
import pcbnew

OUT = Path('/work/experiments')
report = {'commands': ['/usr/bin/python3 /work/experiments/review.py'],
          'python': sys.version, 'pcbnew_version': pcbnew.Version(),
          'assumptions': ['Native files read only; no fill or save performed.',
                          'Ideal initially discharged unloaded RC; 1 V step.',
                          'No ampacity or thermal qualification from track width.']}
for argv in [['kicad-cli', 'version'], ['ngspice', '--version']]:
    p = subprocess.run(argv, capture_output=True, text=True)
    report.setdefault('tools', []).append({'argv': argv, 'returncode': p.returncode,
                                          'stdout': p.stdout, 'stderr': p.stderr})
paths = ['/candidate/requirements/fixture-requirements.md',
         '/candidate/project/rc_filter.kicad_pcb',
         '/candidate/project/rc_filter.kicad_sch',
         '/evidence/work/netlist/design.xml', '/evidence/work/rc-step/result.raw']
report['input_sha256'] = {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in paths}

# Minimal native S-expression reader, independent of the supplied geometry probe.
def sexp(path):
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|[()]|[^\s()]+', Path(path).read_text())
    stack = [[]]
    for t in tokens:
        if t == '(':
            item = []; stack[-1].append(item); stack.append(item)
        elif t == ')': stack.pop()
        else: stack[-1].append(json.loads(t) if t.startswith('"') else t)
    return stack[0][0]
def children(obj, key): return [x for x in obj if isinstance(x, list) and x[0] == key]
def one(obj, key): return children(obj, key)[0][1:]
native = sexp('/candidate/project/rc_filter.kicad_pcb')
segments = [{'start_mm': one(s, 'start'), 'end_mm': one(s, 'end'),
             'width_mm': float(one(s, 'width')[0]), 'net': one(s, 'net')[0],
             'uuid': one(s, 'uuid')[0]} for s in children(native, 'segment')]
report['native_segments'] = segments
report['width_requirement_mm'] = .2
report['width_pass'] = all(s['width_mm'] >= .2 for s in segments)
report['edge_lines'] = [{'start_mm': one(e, 'start'), 'end_mm': one(e, 'end')}
                        for e in children(native, 'gr_line')]

board = pcbnew.LoadBoard('/candidate/project/rc_filter.kicad_pcb')
board.BuildConnectivity()
conn = board.GetConnectivity()
def ident(item):
    if isinstance(item, pcbnew.PAD):
        return item.GetParentFootprint().GetReference() + '.' + item.GetNumber()
    return item.m_Uuid.AsString()
all_items = list(board.GetPads()) + list(board.GetTracks()) + list(board.Zones())
report['physical_adjacency'] = {}
for item in all_items:
    report['physical_adjacency'][ident(item)] = {
        'net': item.GetNetname(),
        'connected': sorted(ident(i) for i in conn.GetConnectedItems(item))}
report['populated_values'] = {f.GetReference(): f.GetValue() for f in board.GetFootprints()}
report['saved_fill'] = []
for z in board.Zones():
    poly = z.GetFilledPolysList(pcbnew.B_Cu)
    # Samples at via and in the J1.3 thermal spokes, outside the drill.
    samples = [(113,100), (100.5,105.58), (101,106.08), (101.5,106.58),
               (99.5,104.58), (99,104.08), (98.5,103.58)]
    report['saved_fill'].append({'uuid': ident(z), 'net': z.GetNetname(),
        'outline_count': poly.OutlineCount(), 'area_mm2': poly.Area()/1e12,
        'point_samples': [{'xy_mm': [x,y], 'inside_saved_fill':
          poly.Contains(pcbnew.VECTOR2I(round(x*1e6),round(y*1e6)))} for x,y in samples]})

xml = ET.parse('/evidence/work/netlist/design.xml').getroot()
report['exported_components'] = {c.attrib['ref']: c.findtext('value') for c in xml.find('components')}
report['exported_nets'] = {n.attrib['name']: sorted(x.attrib['ref']+'.'+x.attrib['pin']
                             for x in n.findall('node')) for n in xml.find('nets')}
R, C = 100000., 100e-9
tau = R*C
report['analytic_rc'] = {'R_ohm': R, 'C_farad': C, 'tau_ms': tau*1000,
    'formula': 'Vout(t)=1-exp(-t/(R*C))',
    'vout_1ms_V': -math.expm1(-.001/tau), 'vout_5ms_V': -math.expm1(-.005/tau),
    'requirement_1ms_V': [.62,.65],
    'allowed_tau_ms': [-1/math.log(1-v) for v in [.65,.62]]}

raw = Path('/evidence/work/rc-step/result.raw').read_bytes()
header, data = raw.split(b'Binary:\n', 1)
header = header.decode()
nv = int(re.search(r'No. Variables:\s*(\d+)', header)[1])
np = int(re.search(r'No. Points:\s*(\d+)', header)[1])
rows = list(struct.iter_unpack('<'+'d'*nv, data))
assert len(rows) == np
def interp(t, col):
    for a,b in zip(rows,rows[1:]):
        if a[0] <= t <= b[0]: return a[col]+(b[col]-a[col])*(t-a[0])/(b[0]-a[0])
    raise ValueError(t)
report['raw_waveform'] = {'header': header, 'binary_format_assumption': 'little-endian real IEEE754 doubles',
    'points_read': len(rows), 'initial_row': rows[0], 'final_row': rows[-1],
    'vout_1ms_interpolated_V': interp(.001,2), 'vout_5ms_interpolated_V': interp(.005,2),
    'vin_1ms_V': interp(.001,1), 'max_error_against_ideal_step_V':
    max(abs(r[2]+math.expm1(-r[0]/tau)) for r in rows)}
report['limitations'] = ['pcbnew connectivity shares KiCad implementation with supplied DRC.',
    'Saved zone fill inspected without regeneration; fill samples are not a field solution.',
    'No manufacturer capability, stackup copper thickness, exact part models or mechanical mating data supplied.',
    'No tolerance, temperature, external loading, ADC sampling, EMI, fabricated-board or thermal tests.',
    '5 ms measurement has no acceptance bound; it is informational.']
(OUT/'review-results.json').write_text(json.dumps(report, indent=2)+'\n')
print(json.dumps({'width_pass': report['width_pass'], 'analytic_rc': report['analytic_rc'],
                  'raw_waveform': report['raw_waveform'], 'saved_fill': report['saved_fill']}, indent=2))
