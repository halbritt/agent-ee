#!/usr/bin/python3
"""Read-only audit of native saved segments and exported connectivity."""
import hashlib
import json
import platform
import re
import subprocess
import xml.etree.ElementTree as ET
from decimal import Decimal
from pathlib import Path
import pcbnew

OUT = Path('/work/experiments')
board_path = Path('/candidate/project/rc_filter.kicad_pcb')
sch_path = Path('/candidate/project/rc_filter.kicad_sch')
text = board_path.read_text()
native = []
for m in re.finditer(r'^\t\(segment\n.*?^\t\)', text, re.M | re.S):
    block = m.group()
    def scalar(key):
        return re.search(r'\(' + key + r' ([^)]+)\)', block).group(1).strip('"')
    width = Decimal(scalar('width'))
    native.append({
        'uuid': scalar('uuid'), 'native_line': text[:m.start()].count('\n') + 1,
        'start_mm': scalar('start'), 'end_mm': scalar('end'),
        'net': scalar('net'), 'layer': scalar('layer'),
        'width_mm': str(width), 'required_min_mm': '0.30',
        'shortfall_mm': str(Decimal('0.30') - width),
        'passes': width >= Decimal('0.30')})
board = pcbnew.LoadBoard(str(board_path))
api_tracks = []
api_vias = []
for t in board.GetTracks():
    width = t.GetWidth(pcbnew.F_Cu) if isinstance(t, pcbnew.PCB_VIA) else t.GetWidth()
    row = {'uuid': t.m_Uuid.AsString(), 'width_mm': pcbnew.ToMM(width),
           'type': t.GetClass(), 'net': t.GetNetname()}
    (api_vias if isinstance(t, pcbnew.PCB_VIA) else api_tracks).append(row)
api_widths = {t['uuid']: t['width_mm'] for t in api_tracks}
geometry = json.loads(Path('/evidence/work/geometry/geometry.json').read_text())
reported_widths = {t['uuid']: t['width_mm'] for t in geometry['tracks'] if t['type'] == 'track'}
assert set(api_widths) == {t['uuid'] for t in native} == set(reported_widths)
assert all(float(t['width_mm']) == api_widths[t['uuid']] == reported_widths[t['uuid']] for t in native)

xml_path = Path('/evidence/work/netlist/design.xml')
root = ET.parse(xml_path).getroot()
exported_nets = {n.attrib['name']: sorted(f"{v.attrib['ref']}.{v.attrib['pin']}" for v in n.findall('node')) for n in root.findall('./nets/net')}
pcb_nets = {}
for fp in board.GetFootprints():
    for pad in fp.Pads():
        pcb_nets.setdefault(pad.GetNetname(), []).append(f'{fp.GetReference()}.{pad.GetNumber()}')
pcb_nets = {k: sorted(v) for k, v in pcb_nets.items()}
connectivity = json.loads(Path('/evidence/work/netlist/connectivity.json').read_text())
assert exported_nets == pcb_nets == {k: sorted(v) for k, v in connectivity['nets'].items()}
assert {c.attrib['ref']: c.findtext('value') for c in root.findall('./components/comp')} == {f.GetReference(): f.GetValue() for f in board.GetFootprints()}

records = {}
for check in ('erc', 'drc', 'netlist', 'geometry'):
    execution = json.loads(Path(f'/evidence/work/{check}/execution.json').read_text())
    console = Path(f'/evidence/work/{check}/console.log').read_bytes()
    assert hashlib.sha256(console).hexdigest() == execution['console_sha256']
    records[check] = {'argv': execution['argv'], 'exit_code': execution['exit_code'], 'console_hash_verified': True}

result = {
    'command': '/usr/bin/python3 -B /work/experiments/review.py',
    'python_version': platform.python_version(),
    'pcbnew_version': pcbnew.Version(),
    'kicad_cli_version': subprocess.check_output(['kicad-cli', 'version'], text=True).strip(),
    'native_sha256': {str(p).replace('/candidate/', 'inputs/'): hashlib.sha256(p.read_bytes()).hexdigest() for p in (board_path, sch_path)},
    'native_segment_count': len(native),
    'native_arc_count': len(re.findall(r'^\t\(arc\b', text, re.M)),
    'violating_segments': sum(not t['passes'] for t in native),
    'segments': native,
    'pcbnew_tracks': api_tracks,
    'pcbnew_vias': api_vias,
    'three_way_width_agreement': True,
    'schematic_export_board_pad_connectivity_agreement': True,
    'nets': exported_nets,
    'check_execution_records': records,
    'limitations': [
        'Only saved track segments are compared with the explicit width requirement; vias, pad dimensions and zone min_thickness are different geometric entities.',
        'No candidate files written; zone fill examined as saved, not refilled.',
        'No circuit simulation or hardware measurements performed; no operating voltages, loads, limits, tolerances or part numbers were supplied.',
        'ERC and DRC results inspected as supplied, including ignored checks; not rerun by this script.',
        'No ampacity, ECU qualification, startup, shutdown, reset, brownout or external interface compliance inferred.'
    ]}
(OUT / 'measurements.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
