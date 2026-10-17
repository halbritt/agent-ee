"""Read-only, independent native S-expression and check-record audit."""
import hashlib
import json
import re
import sys
from decimal import Decimal
from pathlib import Path


def parse(path):
    source = Path(path).read_text()
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|[^\s()]+|[()]', source)
    stack, root = [], None
    for token in tokens:
        if token == '(':
            node = []
            if stack:
                stack[-1].append(node)
            stack.append(node)
        elif token == ')':
            root = stack.pop()
        else:
            stack[-1].append(json.loads(token) if token.startswith('"') else token)
    assert not stack
    return source, root


def children(node, kind):
    return [x for x in node[1:] if isinstance(x, list) and x[0] == kind]


def field(node, kind):
    return children(node, kind)[0][1:]


board_path = Path('/candidate/project/rc_filter.kicad_pcb')
source, board = parse(board_path)
tracks = []
for kind in ('segment', 'arc'):
    for node in children(board, kind):
        width = Decimal(field(node, 'width')[0])
        tracks.append(dict(type=kind, uuid=field(node, 'uuid')[0],
                           start_mm=field(node, 'start'), end_mm=field(node, 'end'),
                           layer=field(node, 'layer')[0], net=field(node, 'net')[0],
                           width_mm=str(width), margin_mm=str(width - Decimal('0.20')),
                           passes=width >= Decimal('0.20')))
geometry = json.loads(Path('/evidence/work/geometry/geometry.json').read_text())
measured = {t['uuid']: Decimal(str(t['width_mm'])) for t in geometry['tracks']
            if t['type'] != 'via'}
native = {t['uuid']: Decimal(t['width_mm']) for t in tracks}
pad_nets = {}
placements = []
for fp in children(board, 'footprint'):
    props = {p[1]: p[2] for p in children(fp, 'property')}
    ref = props['Reference']
    placements.append(dict(ref=ref, position_mm=field(fp, 'at'),
                           has_courtyard=any('F.CrtYd' in field(item, 'layer')
                              for item in fp[1:] if isinstance(item, list)
                              and children(item, 'layer'))))
    for pad in children(fp, 'pad'):
        pad_nets.setdefault(field(pad, 'net')[0], []).append(ref + '.' + pad[1])
for pins in pad_nets.values():
    pins.sort()
netlist = json.loads(Path('/evidence/work/netlist/connectivity.json').read_text())
netlist_nets = {net: sorted(pins) for net, pins in netlist['nets'].items()}
zones = []
for zone in children(board, 'zone'):
    filled = children(zone, 'filled_polygon')
    zones.append(dict(uuid=field(zone, 'uuid')[0], net=field(zone, 'net')[0],
                      layer=field(zone, 'layer')[0], saved_filled_polygons=len(filled)))
drc = json.loads(Path('/evidence/work/drc/drc.json').read_text())
erc = json.loads(Path('/evidence/work/erc/erc.json').read_text())
result = dict(python_version=sys.version,
              board_sha256=hashlib.sha256(source.encode()).hexdigest(),
              source='inputs/project/rc_filter.kicad_pcb',
              requirement='inputs/requirements.txt:1; every saved track >= 0.20 mm',
              tracks=tracks, segment_count=len(children(board, 'segment')),
              arc_count=len(children(board, 'arc')), via_count=len(children(board, 'via')),
              minimum_track_width_mm=str(min(native.values())),
              all_tracks_pass=all(t['passes'] for t in tracks),
              agrees_with_supplied_geometry=native == measured,
              pad_nets=pad_nets, agrees_with_exported_netlist=pad_nets == netlist_nets,
              placements=placements, zones=zones,
              native_drc_violations=len(drc['violations']),
              native_drc_unconnected=len(drc['unconnected_items']),
              native_drc_parity=len(drc['schematic_parity']),
              native_drc_ignored=[c['key'] for c in drc['ignored_checks']],
              native_erc_violations=sum(len(s['violations']) for s in erc['sheets']),
              native_erc_ignored=[c['key'] for c in erc['ignored_checks']])
print(json.dumps(result, indent=2))
