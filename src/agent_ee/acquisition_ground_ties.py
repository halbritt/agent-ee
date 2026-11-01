"""Pad-derived local ground-plane ties for the captured acquisition recipe.

Generation preserves existing board nodes. Native DRC and the final ground
assessment remain separate obligations; a seed is not engineering acceptance.
"""
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
import re
import uuid

from . import acquisition_local_return as local
from .placement_job import JobRefusal
from .catalog import strict_json
from .storage import canonical, digest
from .symbol_library import children, parse_sexpr

POLICY = dict(schema='agent-ee.acquisition-ground-ties/2',
    endpoints=[['U2', '3'], ['C4', '2'], ['C5', '2']],
    track_width_mm=.25, via_diameter_mm=.6, via_drill_mm=.3,
    pad_edge_gap_mm=.2, maximum_path_mm=3,
    exposed_pad_bars=dict(reference='U1', pad='9', width_mm=.81))
# The retained ground-tie model revision generates its original copper, without bars.
_GROUND_ONLY = {**{key: value for key, value in POLICY.items() if key != 'exposed_pad_bars'},
                'schema': 'agent-ee.acquisition-ground-ties/1'}
_NUMBER = re.compile(r'-?\d+(\.\d+)?')


def _copper_nodes(row):
    """The generated front segment and through via for one connection."""
    x, y = row['via_nm']
    a, b = row['pad_nm']
    return (f'\n(segment (start {local._mm(a)} {local._mm(b)}) '
            f'(end {local._mm(x)} {local._mm(y)}) (width 0.25) (layer "F.Cu") '
            f'(net "GND") (uuid "{row["segment_uuid"]}"))\n'
            f'(via (at {local._mm(x)} {local._mm(y)}) (size 0.6) (drill 0.3) '
            f'(layers "F.Cu" "B.Cu") (net "GND") (uuid "{row["via_uuid"]}"))\n').encode()


def _bar_node(row):
    """One generated B.Cu bar across a pair of exposed-pad holes."""
    a, b = row['start_nm']
    x, y = row['end_nm']
    return (f'\n(segment (start {local._mm(a)} {local._mm(b)}) (end {local._mm(x)} {local._mm(y)}) '
            f'(width 0.81) (layer "B.Cu") (net "GND") (uuid "{row["segment_uuid"]}"))\n').encode()


def _by_value(node):
    """KiCad drops trailing zeros on save, so numbers compare by value."""
    if isinstance(node, list):
        return [_by_value(child) for child in node]
    return Decimal(node) if _NUMBER.fullmatch(node) else node


def plan(board, policy):
    """Derive one outward connection per native pad, including vertical length."""
    import pcbnew as k
    if policy not in (POLICY, _GROUND_ONLY) or board.GetCopperLayerCount() != 2:
        raise JobRefusal('ground ties: unsupported policy or copper stack')
    footprints = list(board.GetFootprints())
    refs = [fp.GetReference() for fp in footprints]
    if len(refs) != len(set(refs)):
        raise JobRefusal('ground ties: duplicate footprint references')
    fps = dict(zip(refs, footprints))
    thickness = board.GetDesignSettings().GetBoardThickness()
    if thickness <= 0:
        raise JobRefusal('ground ties: positive board thickness required')
    rows = []
    for ref, number in policy['endpoints']:
        if ref not in fps:
            raise JobRefusal('ground ties: missing endpoint footprint ' + ref)
        fp = fps[ref]
        pins = list(fp.Pads())
        selected = [p for p in pins if p.GetNumber() == number]
        if len(selected) != 1:
            raise JobRefusal('ground ties: ambiguous endpoint ' + ref + '.' + number)
        pad = selected[0]
        if (fp.GetLayer() != k.F_Cu or fp.GetOrientationDegrees() % 90
                or pad.GetAttribute() != k.PAD_ATTRIB_SMD or pad.HasHole()
                or pad.GetNetname() != 'GND' or pad.GetNetCode() <= 0
                or not pad.IsOnLayer(k.F_Cu) or not pad.FlashLayer(k.F_Cu)
                or pad.IsOnLayer(k.B_Cu)
                or pad.GetShape() not in (k.PAD_SHAPE_RECT, k.PAD_SHAPE_ROUNDRECT)):
            raise JobRefusal('ground ties: endpoint needs an orthogonal front SMD ground pad')
        origin = fp.GetPosition()
        if ref != 'U2':
            other = [p for p in pins if p.GetNumber() == '1']
            if len(other) != 1 or other[0].GetNetname() == 'GND':
                raise JobRefusal('ground ties: capacitor pad frame is ambiguous')
            origin = other[0].GetPosition()
        point = pad.GetPosition()
        dx, dy = point.x - origin.x, point.y - origin.y
        if (dx == 0) == (dy == 0):
            raise JobRefusal('ground ties: outward pad frame is not orthogonal')
        ux = 0 if dx == 0 else (1 if dx > 0 else -1)
        uy = 0 if dy == 0 else (1 if dy > 0 else -1)
        box = pad.GetBoundingBox()
        half = (max(abs(box.GetLeft()-point.x), abs(box.GetRight()-point.x)) if ux
                else max(abs(box.GetTop()-point.y), abs(box.GetBottom()-point.y)))
        # Via radius plus the captured existing clearance, outside the pad.
        distance = half + 500_000
        if distance <= 500_000 or distance + thickness > 3_000_000:
            raise JobRefusal('ground ties: direct connection exceeds the declared 3 mm path')
        rows.append(dict(endpoint=[ref, number], pad_uuid=pad.m_Uuid.AsString(),
            footprint_uuid=fp.m_Uuid.AsString(), pad_nm=[point.x, point.y],
            via_nm=[point.x+ux*distance, point.y+uy*distance],
            front_length_mm=distance/1e6, barrel_length_mm=thickness/1e6))
    return rows


def exposed_pad_bars(board, policy):
    """Pair the regulator's thermal holes along their native rows for bottom-copper bars.

    The supplier land stays in the footprint, and F.Cu holes already sit inside the
    exposed pad. Only B.Cu needs copper to reach the supplier's annular-ring recommendation.
    """
    import pcbnew as k
    spec = policy.get('exposed_pad_bars')
    if spec is None:
        return []
    footprints = [fp for fp in board.GetFootprints() if fp.GetReference() == spec['reference']]
    if len(footprints) != 1:
        raise JobRefusal('ground ties: exposed-pad footprint is missing or ambiguous')
    holes = [pad for pad in footprints[0].Pads() if pad.GetNumber() == spec['pad'] and pad.HasHole()]
    if (len(holes) != 4 or any(pad.GetAttribute() != k.PAD_ATTRIB_PTH or pad.GetNetname() != 'GND'
                               or not pad.FlashLayer(k.B_Cu) for pad in holes)):
        raise JobRefusal('ground ties: exposed pad needs four plated ground holes on B.Cu')
    points = sorted(([pad.GetPosition().x, pad.GetPosition().y], pad.m_Uuid.AsString()) for pad in holes)
    pairs = [points[:2], points[2:]]
    if any(a[0] != b[0] for (a, _), (b, _) in pairs):
        points.sort(key=lambda row: (row[0][1], row[0][0]))
        pairs = [points[:2], points[2:]]
        if any(a[1] != b[1] for (a, _), (b, _) in pairs):
            raise JobRefusal('ground ties: exposed-pad holes are not an orthogonal pair of rows')
    lands = {(pad.GetDrillSize().x, pad.GetDrillSize().y, pad.GetSize(k.B_Cu).x, pad.GetSize(k.B_Cu).y) for pad in holes}
    if len(lands) != 1:
        raise JobRefusal('ground ties: exposed-pad holes differ in drill or land')
    return [dict(start_nm=a, end_nm=b, pad_uuids=[first, second], land_nm=list(next(iter(lands))))
            for (a, first), (b, second) in pairs]


def seed(board_path, data, policy):
    """Return bytes and a reproducible receipt; do not write the source board."""
    import pcbnew as k
    path = Path(board_path)
    before = path.read_bytes()
    if data != before:
        raise JobRefusal('ground ties: native source and captured bytes differ')
    board = k.LoadBoard(str(path))
    if board is None:
        raise JobRefusal('ground ties: native board load failed')
    rows = plan(board, policy)
    bars = exposed_pad_bars(board, policy)
    parent = sha256(data).hexdigest()
    tree = parse_sexpr(data)
    if tree[0] != 'kicad_pcb' or children(tree, 'version') != [['version', '20260206']]:
        raise JobRefusal('ground ties: pinned KiCad 10 board required')
    existing = {n[1] for node in tree[1:] if isinstance(node, list)
                for n in children(node, 'uuid')}
    nodes = []
    for row in rows:
        for kind in ('segment', 'via'):
            identity = str(uuid.uuid5(uuid.NAMESPACE_URL,
                'agent-ee/ground-ties/' + parent + '/' + str(row['endpoint']) + '/' + kind))
            if identity in existing:
                raise JobRefusal('ground ties: generated UUID already present')
            row[kind+'_uuid'] = identity
        nodes.append(_copper_nodes(row))
    for index, row in enumerate(bars):
        row['segment_uuid'] = str(uuid.uuid5(uuid.NAMESPACE_URL,
            'agent-ee/ground-ties/' + parent + '/exposed-pad-bar/' + str(index)))
        if row['segment_uuid'] in existing:
            raise JobRefusal('ground ties: generated UUID already present')
        nodes.append(_bar_node(row))
    end = data.rstrip().rfind(b')')
    result = data[:end] + b''.join(nodes) + data[end:]
    after = parse_sexpr(result)
    added = 2*len(rows) + len(bars)
    if after[:-added] != tree or [n[0] for n in after[-added:]] != ['segment', 'via']*len(rows) + ['segment']*len(bars):
        raise JobRefusal('ground ties: generation changed original board nodes')
    if path.read_bytes() != before:
        raise JobRefusal('ground ties: native inspection changed source')
    return result, dict(schema=policy['schema'], policy=strict_json(canonical(policy)), connections=rows, bars=bars,
        native_source_sha256=sha256(before).hexdigest(), input_sha256=parent,
        output_sha256=sha256(result).hexdigest(), helper_sha256=digest(Path(__file__)),
        acceptance_assessed=False)


def without_pending_plane_warnings(report, record):
    """Drop one dangling-via warning per generated via, before the plane exists.

    The bottom plane is added after routing. Final native DRC runs on the filled
    board without this allowance, so a tie left unconnected still fails there.
    """
    pending = {row['via_uuid'] for row in record['connections']}
    kept = []
    for finding in report['violations']:
        items = finding['items']
        if (finding['type'] == 'via_dangling' and finding['severity'] == 'warning'
                and len(items) == 1 and items[0]['uuid'] in pending):
            pending.remove(items[0]['uuid'])
        else:
            kept.append(finding)
    return {**report, 'violations': kept}


def verify(board_path, record):
    """Verify actual saved copper and endpoint frames, including exact via centres."""
    import pcbnew as k
    path = Path(board_path)
    before = digest(path)
    if (record.get('policy') not in (POLICY, _GROUND_ONLY) or record.get('schema') != record['policy']['schema']
            or record.get('helper_sha256') != digest(Path(__file__))):
        raise JobRefusal('ground ties: policy/helper custody differs')
    tree=parse_sexpr(path.read_bytes())
    # Compare whole saved nodes before native loading: KiCad can infer a via's net from
    # touching copper, and any other saved property, such as a backdrill, must refuse.
    generated_nodes=b''.join([_copper_nodes(row) for row in record.get('connections',[])]
                             +[_bar_node(row) for row in record.get('bars',[])])
    for generated in parse_sexpr(b'(generated'+generated_nodes+b')')[1:]:
        saved=[node for node in children(tree,generated[0])
               if children(node,'uuid')==children(generated,'uuid')]
        if len(saved)!=1 or _by_value(saved[0])!=_by_value(generated):
            raise JobRefusal('ground ties: saved copper differs from the generated form')
    board = k.LoadBoard(str(path))
    if board is None:
        raise JobRefusal('ground ties: native verification load failed')
    expected = plan(board, record['policy'])
    bars = exposed_pad_bars(board, record['policy'])
    keys = ('start_nm', 'end_nm', 'pad_uuids', 'land_nm')
    if [{key: row.get(key) for key in keys} for row in record.get('bars', [])] != bars:
        raise JobRefusal('ground ties: exposed-pad hole frame differs')
    recorded = record.get('connections')
    if not isinstance(recorded, list) or len(recorded) != len(expected):
        raise JobRefusal('ground ties: connection inventory differs')
    inventory = {}
    for item in board.GetTracks():
        uid = item.m_Uuid.AsString()
        if uid in inventory:
            raise JobRefusal('ground ties: duplicate native copper UUID')
        inventory[uid] = item
    for row, original in zip(expected, recorded):
        if {key: original.get(key) for key in row} != row:
            raise JobRefusal('ground ties: native endpoint frame differs')
        via, track = (inventory.get(original.get(kind+'_uuid')) for kind in ('via', 'segment'))
        if (via is None or via.GetClass() != 'PCB_VIA' or via.GetNetname() != 'GND'
                or via.GetViaType() != k.VIATYPE_THROUGH
                or via.TopLayer() != k.F_Cu or via.BottomLayer() != k.B_Cu
                or via.GetWidth(k.F_Cu) != 600_000 or via.GetWidth(k.B_Cu) != 600_000
                or via.GetDrillValue() != 300_000
                or [via.GetPosition().x, via.GetPosition().y] != row['via_nm']):
            raise JobRefusal('ground ties: native via differs')
        if (track is None or track.GetClass() != 'PCB_TRACK' or track.GetNetname() != 'GND'
                or track.GetLayer() != k.F_Cu or track.GetWidth() != 250_000
                or sorted(([track.GetStart().x,track.GetStart().y],
                           [track.GetEnd().x,track.GetEnd().y])) != sorted((row['pad_nm'],row['via_nm']))):
            raise JobRefusal('ground ties: native stub differs')
    if digest(path) != before:
        raise JobRefusal('ground ties: verification changed board')
    return dict(status='pass', board_sha256=before, connections=recorded, bars=record['bars'],
                acceptance_assessed=False)
