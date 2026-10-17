"""Read-only circuit review; all generated artifacts stay beside this script."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import xml.etree.ElementTree as ET

OUT = Path(__file__).parent
records = []
env = dict(os.environ, KICAD_CONFIG_HOME=str(OUT / 'kicad-config'))

def run(argv):
    p = subprocess.run(argv, cwd=OUT, env=env, capture_output=True, text=True)
    records.append({'argv': argv, 'exit_code': p.returncode,
                    'stdout': p.stdout, 'stderr': p.stderr})
    return p

def sexpr(path):
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|[^\s()]+|[()]', path.read_text())
    stack, root = [], None
    for t in tokens:
        if t == '(':
            node = []
            if stack:
                stack[-1].append(node)
            else:
                root = node
            stack.append(node)
        elif t == ')':
            stack.pop()
        else:
            stack[-1].append(json.loads(t) if t.startswith('"') else t)
    return root

def children(node, name):
    return [n for n in node if isinstance(n, list) and n[0] == name]

def child(node, name):
    return children(node, name)[0]

run(['kicad-cli', '--version'])
run(['ngspice', '--version'])
run([sys.executable, '--version'])
native = {name: sexpr(Path('/candidate/project') / ('rc_filter.' + name))
          for name in ['kicad_sch', 'kicad_pcb']}
hashes = {name: hashlib.sha256((Path('/candidate/project') / ('rc_filter.' + name)).read_bytes()).hexdigest()
          for name in native}
p = run(['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml',
         '-o', str(OUT / 'independent.xml'), '/candidate/project/rc_filter.kicad_sch'])
assert p.returncode == 0
xml = ET.parse(OUT / 'independent.xml').getroot()
net_members = {n.attrib['name']: sorted(x.attrib['ref'] + '.' + x.attrib['pin'] for x in n)
               for n in xml.find('nets')}
values = {n.attrib['ref']: n.findtext('value') for n in xml.find('components')}
reported = json.loads(Path('/evidence/work/netlist/connectivity.json').read_text())
assert net_members == {n: sorted(m) for n, m in reported['nets'].items()}
assert values == {r: c['value'] for r, c in reported['components'].items()}
schematic_components = []
for s in children(native['kicad_sch'], 'symbol'):
    props = {p[1]: p[2] for p in children(s, 'property')}
    schematic_components.append({'ref': props['Reference'], 'value': props['Value'],
                                  'lib_id': child(s, 'lib_id')[1], 'uuid': child(s, 'uuid')[1],
                                  'dnp': child(s, 'dnp')[1]})
board_pads, tracks = {}, []
for fp in children(native['kicad_pcb'], 'footprint'):
    props = {p[1]: p[2] for p in children(fp, 'property')}
    for pad in children(fp, 'pad'):
        board_pads[props['Reference'] + '.' + pad[1]] = child(pad, 'net')[1]
assert board_pads == {p: n for n, members in net_members.items() for p in members}
for s in children(native['kicad_pcb'], 'segment'):
    tracks.append({'uuid': child(s, 'uuid')[1], 'width_mm': float(child(s, 'width')[1]),
                   'net': child(s, 'net')[1], 'start': child(s, 'start')[1:],
                   'end': child(s, 'end')[1:]})
assert len(tracks) == 5 and all(t['width_mm'] >= 0.2 for t in tracks)

# Inspect the retained ngspice binary, rather than inferring behavior from exit status.
raw = Path('/evidence/work/rc-step/result.raw').read_bytes()
header_bytes, payload = raw.split(b'Binary:\n', 1)
header = header_bytes.decode()
nv = int(re.search(r'No. Variables:\s*(\d+)', header)[1])
np = int(re.search(r'No. Points:\s*(\d+)', header)[1])
assert len(payload) == 8 * nv * np
data = list(struct.iter_unpack('<' + 'd' * nv, payload))
assert all(data[i][0] <= data[i+1][0] for i in range(len(data)-1))
sample = {}
tau = 10000 * 100e-9
for t in [0.001, 0.005]:
    hi = next(i for i, row in enumerate(data) if row[0] >= t)
    a, b = data[max(0, hi-1)], data[hi]
    y = b[2] if a[0] == b[0] else a[2] + (b[2]-a[2])*(t-a[0])/(b[0]-a[0])
    ideal = 1 - math.exp(-t/tau)
    ramp = 1 - tau/1e-9 * math.expm1(1e-9/tau) * math.exp(-t/tau)
    sample[str(t)] = {'raw_interpolated_vout_V': y, 'ideal_step_V': ideal,
                      'exact_1ns_ramp_V': ramp, 'raw_minus_ramp_V': y-ramp}

# Independent ideal step: capacitor initially discharged, no load, explicit UIC.
deck = '''Independent ideal RC step, native R=10k and C=100n
V1 IN 0 DC 1
R1 IN OUT 10k
C1 OUT 0 100n IC=0
.tran 1u 5m 0 1u UIC
.meas tran vout_1ms FIND v(OUT) AT=1m
.meas tran vout_5ms FIND v(OUT) AT=5m
.end
'''
(OUT / 'independent.cir').write_text(deck)
run(['ngspice', '-n', '-b', '-o', str(OUT / 'independent-ngspice.log'), str(OUT / 'independent.cir')])
result = {'native_sha256': hashes, 'schematic_components': schematic_components,
          'values': values, 'nets': net_members, 'board_pad_nets': board_pads,
          'track_segments': tracks, 'tau_s': tau, 'raw_header': header,
          'raw_samples_and_calculation': sample,
          'assumptions': ['Ideal nominal linear R and C, zero initial capacitor voltage, ideal source, no external load.',
                          'Retained raw file uses ngspice real little-endian float64 format; header and byte length validated.',
                          'Original PWL rises in 1 ns, with 0 V DC initialization. Difference from ideal step evaluated analytically.'],
          'outcome': 'Native values, pin/net mapping, track widths and captured response agree with stated fixture requirements.',
          'limitations': ['No tolerance, temperature, parasitics, component ratings, loading, ADC, EMI or hardware verification.',
                          'No zone refill; relied on saved fill and supplied DRC for complete copper continuity.',
                          'No new full ERC/DRC run; original reports inspected including ignored checks.',
                          'No active power/reset/brownout circuitry in this passive fixture; no such operating criteria supplied.']}
(OUT / 'audit.json').write_text(json.dumps(result, indent=2) + '\n')
(OUT / 'commands.json').write_text(json.dumps(records, indent=2) + '\n')
print(json.dumps({'values': values, 'nets': net_members, 'tau_s': tau,
                  'measurements': sample, 'min_width_mm': min(t['width_mm'] for t in tracks)}, indent=2))
