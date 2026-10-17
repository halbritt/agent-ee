"""Read-only verification of the alleged J1.3 ground disconnection."""
import hashlib
import json
import platform
from pathlib import Path
import xml.etree.ElementTree as ET
import pcbnew

root = Path('/candidate')
board_path = root / 'project/rc_filter.kicad_pcb'
board = pcbnew.LoadBoard(str(board_path))
connectivity = board.GetConnectivity()
pads = {(f.GetReference(), p.GetNumber()): p
        for f in board.GetFootprints() for p in f.Pads()}
start = pads['J1', '3']
queue = [start]
seen = {}
while queue:
    item = queue.pop()
    uid = item.m_Uuid.AsString()
    if uid in seen:
        continue
    seen[uid] = item
    queue.extend(connectivity.GetConnectedItems(item))

connected_pads = sorted(f'{ref}.{num}' for (ref, num), pad in pads.items()
                        if pad.m_Uuid.AsString() in seen)
xml = ET.parse('/evidence/work/netlist/design.xml')
nets = {n.attrib['name']: sorted(f"{p.attrib['ref']}.{p.attrib['pin']}" for p in n)
        for n in xml.findall('./nets/net')}
drc = json.loads(Path('/evidence/work/drc/drc.json').read_text())
result = {
    'python_version': platform.python_version(),
    'pcbnew_version': pcbnew.GetBuildVersion(),
    'board_sha256': hashlib.sha256(board_path.read_bytes()).hexdigest(),
    'requirements': (root / 'requirements.txt').read_text(),
    'J1_pad_nets': {n: pads['J1', n].GetNetname() for n in ('1','2','3')},
    'connected_pads_from_J1_3': connected_pads,
    'connected_items_from_J1_3': sorted(seen),
    'exported_schematic_nets': nets,
    'supplied_drc': {k: drc[k] for k in ('kicad_version', 'violations', 'unconnected_items', 'schematic_parity')},
    'limitations': ['Saved connectivity and saved zone fill only; no refill, save, repair, or hardware measurement performed.'],
}
print(json.dumps(result, indent=2))
assert result['J1_pad_nets']['3'] == 'GND'
assert 'J1.3' in nets['GND'] and 'C1.2' in nets['GND']
assert 'C1.2' in connected_pads
assert not drc['unconnected_items']
