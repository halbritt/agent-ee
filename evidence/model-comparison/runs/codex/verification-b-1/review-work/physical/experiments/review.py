"""Read-only physical review. Run: /usr/bin/python3 /work/experiments/review.py"""
from pathlib import Path
import hashlib
import json
import math
import os
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path('/work/experiments')
LOG = ROOT / 'commands.log'
LOG.write_text('Physical review; candidate remains read-only.\n'
               'Preliminary inspection: pwd; rg --files for AGENTS.md in /work, /candidate, /evidence (none found); '
               'ls relevant roots; read requirements, project configuration, check JSON records; '
               'probe pcbnew connectivity/polygon API; no candidate edits or zone refill.\n'
               'Preliminary tools: kicad-cli 10.0.7; ngspice 42; Python 3.12.3; '
               'pcbnew 10.0.7; shapely unavailable (not needed).\n')

def run(argv):
    p = subprocess.run(argv, text=True, capture_output=True)
    with LOG.open('a') as f:
        f.write('\nCOMMAND ' + json.dumps(argv) + '\nEXIT ' + str(p.returncode)
                + '\nSTDOUT\n' + p.stdout + '\nSTDERR\n' + p.stderr + '\n')
    return p

run(['kicad-cli', 'version'])
run(['ngspice', '--version'])
run(['/usr/bin/python3', '--version'])

# Read original native text and all supplied check records, including commands and results.
paths = list(Path('/candidate/project').glob('*')) + [Path('/candidate/requirements.txt')]
paths += list(Path('/evidence/checks').glob('*.json'))
paths += [p for p in Path('/evidence/work').glob('*/*') if p.is_file()]
manifest = {}
for p in sorted(paths):
    data = p.read_bytes()
    manifest[str(p)] = {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
    if p.suffix == '.json':
        json.loads(data)
(ROOT / 'read_manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')

run(['kicad-cli', 'pcb', 'drc', '--format', 'json', '--severity-all',
     '--schematic-parity', '--units', 'mm', '-o', str(ROOT / 'drc.json'),
     '/candidate/project/rc_filter.kicad_pcb'])
run(['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml',
     '-o', str(ROOT / 'design.xml'), '/candidate/project/rc_filter.kicad_sch'])

import pcbnew
board = pcbnew.LoadBoard('/candidate/project/rc_filter.kicad_pcb')
cn = board.GetConnectivity()
cn.Build(board)
def xy(v):
    return [v.x / 1e6, v.y / 1e6]
def padname(p):
    return p.GetParentFootprint().GetReference() + '.' + p.GetNumber()
geometry = {'pcbnew_version': pcbnew.GetBuildVersion(),
            'saved_zone_only': True, 'pads': [], 'tracks': [], 'zones': []}
for fp in board.GetFootprints():
    for pad in fp.Pads():
        geometry['pads'].append({
            'pad': padname(pad), 'net': pad.GetNetname(), 'position_mm': xy(pad.GetPosition()),
            'size_mm': xy(pad.GetSize()), 'drill_mm': xy(pad.GetDrillSize()),
            'directly_touching_pads': sorted(padname(p) for p in cn.GetConnectedPads(pad)),
            'directly_touching_tracks': [t.m_Uuid.AsString() for t in cn.GetConnectedTracks(pad)],
        })
for t in board.GetTracks():
    geometry['tracks'].append({'uuid': t.m_Uuid.AsString(), 'net': t.GetNetname(),
                              'start_mm': xy(t.GetStart()), 'end_mm': xy(t.GetEnd()),
                              'width_mm': (t.GetWidth(pcbnew.F_Cu) if t.GetClass() == 'PCB_VIA' else t.GetWidth())/1e6,
                              'type': t.GetClass()})
for z in board.Zones():
    poly = z.GetFilledPolysList(pcbnew.B_Cu)
    geometry['zones'].append({'uuid': z.m_Uuid.AsString(), 'net': z.GetNetname(),
                              'outline_count': poly.OutlineCount(),
                              'filled_area_mm2': poly.Area()/1e12,
                              'saved_copper_samples': {
                                  str(pt): poly.Contains(pcbnew.VECTOR2I(round(pt[0]*1e6), round(pt[1]*1e6)))
                                  for pt in [(113,100), (110,103), (102,105.08),
                                             (100,105.88), (100,106.08), (100,106.58)]},
                              'J1_3_annulus_sample_by_angle_degrees': {
                                  str(a): poly.Contains(pcbnew.VECTOR2I(
                                      round((100+0.8*math.cos(math.radians(a)))*1e6),
                                      round((105.08+0.8*math.sin(math.radians(a)))*1e6)))
                                  for a in range(0,360,15)}})
(ROOT / 'native_geometry.json').write_text(json.dumps(geometry, indent=2) + '\n')

xml = ET.parse(ROOT / 'design.xml').getroot()
values = {c.attrib['ref']: c.findtext('value') for c in xml.findall('./components/comp')}
nets = {n.attrib['name']: sorted(x.attrib['ref'] + '.' + x.attrib['pin'] for x in n.findall('node'))
        for n in xml.findall('./nets/net')}
assert values['R1'] == '10k' and values['C1'] == '100n'
assert nets == {'/IN': ['J1.1', 'R1.1'], '/OUT': ['C1.1', 'J1.2', 'R1.2'],
                'GND': ['C1.2', 'J1.3']}
R, C, t = 10000., 100e-9, 1e-3
v = 1 - math.exp(-t/(R*C))
calc = {
    'native_values': values, 'native_nets': nets,
    'assumptions': ['Ideal 0-to-1 V input step; ideal zero-impedance source.',
                    'C1 initially at 0 V; nominal linear R1/C1; no ADC or other load.',
                    'These assumptions establish only an unloaded baseline, not the specified ADC case.'],
    'tau_s': R*C, 'v_out_at_1ms_V': v,
    'margin_to_lower_V': v - 0.62, 'margin_to_upper_V': 0.65 - v,
    'ideal_tau_interval_s': [-t/math.log(1-0.65), -t/math.log(1-0.62)],
    'initial_resistor_current_A': 1/R, 'initial_resistor_power_W': 1/R,
    'R1_total_step_energy_J': C/2,
    'switched_load_equations': [
        'Switch open: C1*dVo/dt=(Vin-Vo)/R1.',
        'Switch closed: C1*dVo/dt=(Vin-Vo)/R1-(Vo-Vs)/Ron; Cs*dVs/dt=(Vo-Vs)/Ron.',
        'These equations omit ADC leakage, switch charge injection and PCB parasitics.',
        'Fast charge-sharing limit only: Vafter=(C1*Vbefore+Cs*Vs_initial)/(C1+Cs).',
        'Actual Cs, Ron, Vs_initial, switch schedule and acquisition duration are absent; no values are assigned.'],
    'conclusion': 'Unloaded nominal baseline meets band. ADC-connected response remains undetermined. '
                  'No ampacity or thermal qualification inferred from trace width or these ideal calculations.'
}
(ROOT / 'calculations.json').write_text(json.dumps(calc, indent=2) + '\n')

# Independent numerical check of baseline only. Step is delayed by 1 us with 1 ns rise;
# sample is exactly 1 ms after the rise midpoint. No ADC model is invented.
deck = '''Unloaded nominal RC baseline, not ADC validation
Vin in 0 PWL(0 0 1u 0 1.001u 1 2m 1)
R1 in out 10k
C1 out 0 100n
.tran 0.1u 1.1m 0 0.1u
.measure tran v_out_1ms FIND v(out) AT=1.0010005m
.end
'''
(ROOT / 'baseline.cir').write_text(deck)
run(['ngspice', '-b', str(ROOT / 'baseline.cir')])
print(json.dumps({'geometry': geometry, 'calculations': calc}, indent=2))
