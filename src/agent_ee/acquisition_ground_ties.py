"""Pad-derived local ground-plane ties for the captured acquisition recipe.

Generation preserves existing board nodes. Native DRC and the final ground
assessment remain separate obligations; a seed is not engineering acceptance.
"""
from hashlib import sha256
from pathlib import Path
import uuid

from . import acquisition_local_return as local
from .placement_job import JobRefusal
from .catalog import strict_json
from .storage import canonical, digest
from .symbol_library import children, parse_sexpr

POLICY = dict(schema='agent-ee.acquisition-ground-ties/1',
    endpoints=[['U2', '3'], ['C4', '2'], ['C5', '2']],
    track_width_mm=.25, via_diameter_mm=.6, via_drill_mm=.3,
    pad_edge_gap_mm=.2, maximum_path_mm=3)


def plan(board, policy):
    """Derive one outward connection per native pad, including vertical length."""
    import pcbnew as k
    if policy != POLICY or board.GetCopperLayerCount() != 2:
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
        x, y = row['via_nm']
        a, b = row['pad_nm']
        nodes.append((f'\n(segment (start {local._mm(a)} {local._mm(b)}) '
            f'(end {local._mm(x)} {local._mm(y)}) (width 0.25) (layer "F.Cu") '
            f'(net "GND") (uuid "{row["segment_uuid"]}"))\n'
            f'(via (at {local._mm(x)} {local._mm(y)}) (size 0.6) (drill 0.3) '
            f'(layers "F.Cu" "B.Cu") (net "GND") (uuid "{row["via_uuid"]}"))\n').encode())
    end = data.rstrip().rfind(b')')
    result = data[:end] + b''.join(nodes) + data[end:]
    after = parse_sexpr(result)
    if after[:-6] != tree or [n[0] for n in after[-6:]] != ['segment', 'via']*3:
        raise JobRefusal('ground ties: generation changed original board nodes')
    if path.read_bytes() != before:
        raise JobRefusal('ground ties: native inspection changed source')
    return result, dict(schema=POLICY['schema'], policy=strict_json(canonical(policy)), connections=rows,
        native_source_sha256=sha256(before).hexdigest(), input_sha256=parent,
        output_sha256=sha256(result).hexdigest(), helper_sha256=digest(Path(__file__)),
        acceptance_assessed=False)


def verify(board_path, record):
    """Verify actual saved copper and endpoint frames, including exact via centres."""
    import pcbnew as k
    path = Path(board_path)
    before = digest(path)
    if (record.get('schema') != POLICY['schema'] or record.get('policy') != POLICY
            or record.get('helper_sha256') != digest(Path(__file__))):
        raise JobRefusal('ground ties: policy/helper custody differs')
    tree=parse_sexpr(path.read_bytes())
    # KiCad can infer a via's net from touching copper on load/save. Check the
    # persisted assignment first so that normalization cannot hide a mutation.
    for row in record.get('connections',[]):
        for kind in ('segment','via'):
            nodes=[node for node in children(tree,kind)
                   if children(node,'uuid')==[['uuid',row.get(kind+'_uuid')]]]
            if len(nodes)!=1 or children(nodes[0],'net')!=[['net','GND']]:
                raise JobRefusal('ground ties: saved copper identity/net differs')
    board = k.LoadBoard(str(path))
    if board is None:
        raise JobRefusal('ground ties: native verification load failed')
    expected = plan(board, record['policy'])
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
                or via.GetWidth(k.F_Cu) != 600_000 or via.GetDrillValue() != 300_000
                or [via.GetPosition().x, via.GetPosition().y] != row['via_nm']):
            raise JobRefusal('ground ties: native via differs')
        if (track is None or track.GetClass() != 'PCB_TRACK' or track.GetNetname() != 'GND'
                or track.GetLayer() != k.F_Cu or track.GetWidth() != 250_000
                or sorted(([track.GetStart().x,track.GetStart().y],
                           [track.GetEnd().x,track.GetEnd().y])) != sorted((row['pad_nm'],row['via_nm']))):
            raise JobRefusal('ground ties: native stub differs')
    if digest(path) != before:
        raise JobRefusal('ground ties: verification changed board')
    return dict(status='pass', board_sha256=before, connections=recorded,
                acceptance_assessed=False)
