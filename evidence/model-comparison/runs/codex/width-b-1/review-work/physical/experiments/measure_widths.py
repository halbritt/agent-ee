"""Read-only independent measurement of saved track widths; no save or refill."""
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
from decimal import Decimal
import pcbnew

OUT = Path('/work/experiments')
BOARD = Path('/candidate/project/rc_filter.kicad_pcb')
data = BOARD.read_text()
# Every top-level segment/arc in this native file has only flat child fields.
blocks = list(re.finditer(r'^\t\((segment|arc)\n.*?^\t\)', data, re.M | re.S))
threshold = Decimal('0.30')
measurements = []
for match in blocks:
    block = match.group()
    def field(name):
        return re.search(r'\(' + name + r' ([^\n()]*)\)', block).group(1).strip('"')
    width = Decimal(field('width'))
    measurements.append({
        'uuid': field('uuid'), 'native_line': data.count('\n', 0, match.start()) + 1,
        'type': match.group(1), 'net': field('net'), 'layer': field('layer'),
        'start_mm': field('start'), 'end_mm': field('end'),
        'width_mm': str(width), 'required_mm': str(threshold),
        'shortfall_mm': str(threshold - width), 'pass': width >= threshold,
    })
board = pcbnew.LoadBoard(str(BOARD))
api_tracks = []
vias = []
for track in board.GetTracks():
    is_via = isinstance(track, pcbnew.PCB_VIA)
    width_nm = track.GetWidth(pcbnew.F_Cu) if is_via else track.GetWidth()
    row = {'uuid': track.m_Uuid.AsString(), 'type': track.GetClass(),
           'width_nm': width_nm, 'width_mm': pcbnew.ToMM(width_nm),
           'net': track.GetNetname()}
    (vias if is_via else api_tracks).append(row)
native = {row['uuid']: Decimal(row['width_mm']) for row in measurements}
api = {row['uuid']: Decimal(str(row['width_mm'])) for row in api_tracks}
assert native == api, (native, api)
supplied = json.loads(Path('/evidence/work/geometry/geometry.json').read_text())
supplied_tracks = {r['uuid']: Decimal(str(r['width_mm'])) for r in supplied['tracks'] if r['type'] != 'via'}
assert supplied_tracks == native
summary = {
    'command': '/usr/bin/python3 -B /work/experiments/measure_widths.py',
    'python_version': platform.python_version(), 'pcbnew_version': pcbnew.Version(),
    'board_sha256_before': hashlib.sha256(BOARD.read_bytes()).hexdigest(),
    'criterion': 'Every saved copper track segment must have width >= 0.30 mm.',
    'assumptions': ['Native width values are in mm.', 'Vias and zone minimum thickness are not track segments.'],
    'measurements': measurements, 'pcbnew_tracks': api_tracks, 'pcbnew_vias': vias,
    'native_api_and_supplied_geometry_agree': True,
    'segment_count': len(measurements), 'failing_count': sum(not r['pass'] for r in measurements),
}
env = dict(os.environ)
env.update(KICAD_CONFIG_HOME=str(OUT / 'kicad-config'),
           KICAD10_SYMBOL_DIR='/candidate/stock/symbols',
           KICAD10_FOOTPRINT_DIR='/candidate/stock/footprints',
           KICAD10_TEMPLATE_DIR='/candidate/stock/template')
cmd = ['kicad-cli', 'pcb', 'drc', '--format', 'json', '--severity-all',
       '--schematic-parity', '--units', 'mm', '-o', str(OUT / 'drc.json'), str(BOARD)]
run = subprocess.run(cmd, capture_output=True, text=True, env=env)
(OUT / 'drc-console.log').write_text(run.stdout + run.stderr)
summary['drc'] = {'argv': cmd, 'exit_code': run.returncode, 'stdout': run.stdout,
                  'stderr': run.stderr, 'environment_overrides': {k: env[k] for k in (
                      'KICAD_CONFIG_HOME', 'KICAD10_SYMBOL_DIR', 'KICAD10_FOOTPRINT_DIR', 'KICAD10_TEMPLATE_DIR')}}
if (OUT / 'drc.json').exists():
    report = json.loads((OUT / 'drc.json').read_text())
    summary['drc']['counts'] = {k: len(report.get(k, [])) for k in ('violations', 'unconnected_items', 'schematic_parity')}
summary['board_sha256_after'] = hashlib.sha256(BOARD.read_bytes()).hexdigest()
assert summary['board_sha256_before'] == summary['board_sha256_after']
(OUT / 'width-measurements.json').write_text(json.dumps(summary, indent=2) + '\n')
print(json.dumps({'segments': summary['segment_count'], 'failures': summary['failing_count'],
                  'native_api_and_supplied_geometry_agree': True, 'drc': summary['drc']}))
