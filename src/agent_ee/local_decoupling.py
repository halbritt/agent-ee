"""Declared local copper, independent of reference names and recipes.

The visibility search proposes geometry. The caller runs native DRC before the
router sees it, and verifies the locked items again on the final saved board.
"""
import math
from pathlib import Path
from .storage import digest

SCHEMA = 'agent-ee.local-decoupling/1'
BASIS = 'Agent-selected measurement requirement; not an owner or datasheet ruling'


def validate(document):
    from .hardware_interface import exact_keys, text
    exact_keys(document, 'schema relations planes tie_other_pads')
    if document['schema'] != SCHEMA or type(document['tie_other_pads']) is not bool:
        raise ValueError('expected local-decoupling/1 and boolean tie_other_pads')
    if not isinstance(document['planes'], list) or not isinstance(document['relations'], list):
        raise ValueError('planes and relations must be lists')
    for plane in document['planes']:
        exact_keys(plane, 'net layer')
        text(plane['net'])
        if plane['layer'] not in ('F.Cu', 'B.Cu'):
            raise ValueError('local copper currently supports a two-layer stack')
    ids = set()
    for row in document['relations']:
        required = {'id', 'supply', 'return'}
        if not isinstance(row, dict) or not required <= row.keys() or row.keys() - required - {'max_loop_mm', 'basis'}:
            raise ValueError('relation needs id, supply, return and optional max_loop_mm, basis')
        text(row['id'])
        if row['id'] in ids:
            raise ValueError('duplicate local relation ' + row['id'])
        ids.add(row['id'])
        for leg in ('supply', 'return'):
            if not isinstance(row[leg], list) or len(row[leg]) != 2:
                raise ValueError(row['id'] + ': leg needs device and capacitor endpoints')
            for endpoint in row[leg]:
                exact_keys(endpoint, 'reference pad net')
                for value in endpoint.values():
                    text(value)
            if row[leg][0]['net'] != row[leg][1]['net'] or row[leg][0] == row[leg][1]:
                raise ValueError(row['id'] + ': leg needs distinct endpoints on one net')
        if row['supply'][0]['net'] == row['return'][0]['net']:
            raise ValueError(row['id'] + ': supply and return must differ')
        if 'max_loop_mm' in row:
            limit = row['max_loop_mm']
            if type(limit) not in (int, float) or not math.isfinite(limit) or limit <= 0 or row.get('basis') != BASIS:
                raise ValueError(row['id'] + ': positive finite max_loop_mm and measurement basis required')
        elif 'basis' in row:
            raise ValueError('basis requires a loop limit')
    return document


def pad(board, endpoint):
    selected = [p for fp in board.GetFootprints() if fp.GetReference() == endpoint['reference']
                for p in fp.Pads() if p.GetNumber() == endpoint['pad']]
    if len(selected) != 1:
        raise ValueError('missing/ambiguous pad ' + endpoint['reference'] + '.' + endpoint['pad'])
    result = selected[0]
    if result.GetNetname() != endpoint['net'] or result.GetNetCode() <= 0:
        raise ValueError('native net differs at ' + endpoint['reference'] + '.' + endpoint['pad'])
    return result


def position(item):
    p = item.GetPosition()
    return int(p.x), int(p.y)


def _box(item, margin):
    b = item.GetBoundingBox()
    return (int(b.GetLeft()) - margin, int(b.GetTop()) - margin,
            int(b.GetRight()) + margin, int(b.GetBottom()) + margin)


def _rules(board, terminal):
    rules = board.GetDesignSettings()
    cls = rules.m_NetSettings.GetEffectiveNetClass(terminal.GetNetname())
    return dict(width=max(int(rules.m_TrackMinWidth), int(cls.GetTrackWidth())),
                clearance=max(int(rules.m_MinClearance), int(cls.GetClearance())),
                diameter=max(int(rules.m_ViasMinSize), int(cls.GetViaDiameter())),
                drill=max(int(rules.m_MinThroughDrill), int(cls.GetViaDrill())))


def _obstacles(board, net, layer, margin):
    items = [p for fp in board.GetFootprints() for p in fp.Pads()] + list(board.GetTracks())
    return [_box(item, margin) for item in items if item.IsOnLayer(layer) and item.GetNetCode() != net]


def _surface(board, terminal):
    import pcbnew as k
    layers = [layer for layer in board.GetEnabledLayers().CuStack() if terminal.FlashLayer(layer)]
    if terminal.GetAttribute() != k.PAD_ATTRIB_SMD or len(layers) != 1:
        raise ValueError('local leg needs unambiguous surface pads')
    return layers[0]


def _track(board, points, net, layer, width):
    import pcbnew as k
    result = []
    for a, b in zip(points, points[1:]):
        if a == b:
            continue
        item = k.PCB_TRACK(board)
        item.SetStart(k.VECTOR2I(*a))
        item.SetEnd(k.VECTOR2I(*b))
        item.SetWidth(width)
        item.SetLayer(layer)
        item.SetNetCode(net)
        item.SetLocked(True)
        board.Add(item)
        result.append(item)
    return result


def _leg(board, first, second, *, clock, cancel_dir):
    from .acquisition_local_return import _obstacle_path
    layer = _surface(board, first)
    if _surface(board, second) != layer:
        raise ValueError('local leg endpoints must share a copper face')
    rules = _rules(board, first)
    # A routing default can neck into a small land; the fabrication minimum
    # remains the board rule, not the class's preferred routing width.
    width = max(int(board.GetDesignSettings().m_TrackMinWidth),
                min(rules['width'], *(min(p.GetSize().x, p.GetSize().y) for p in (first, second))))
    boxes = _obstacles(board, first.GetNetCode(), layer, rules['clearance'] + width // 2 + 25_000)
    path = _obstacle_path(position(first), position(second), boxes, clock=clock, cancel_dir=cancel_dir)
    if path is None:
        raise ValueError('no clear local surface path within the 12 mm search budget')
    return _track(board, path, first.GetNetCode(), layer, width)


def _tie(board, terminal, plane, *, clock, cancel_dir):
    import pcbnew as k
    from .placement_job import _check_stop
    if terminal.FlashLayer(plane):
        return []  # A plated land already reaches this layer.
    layer = _surface(board, terminal)
    rules = _rules(board, terminal)
    width = max(int(board.GetDesignSettings().m_TrackMinWidth),
                min(rules['width'], terminal.GetSize().x, terminal.GetSize().y))
    a = position(terminal)
    box = terminal.GetBoundingBox()
    # Place the drill outside the solder land. Search short radial escapes;
    # all copper layers constrain a through-via, not only the pad's face.
    radius = rules['diameter'] // 2
    margin = rules['clearance'] + radius + 25_000
    obstacles = [b for l in board.GetEnabledLayers().CuStack()
                 for b in _obstacles(board, terminal.GetNetCode(), l, margin)]
    front = _obstacles(board, terminal.GetNetCode(), layer, rules['clearance'] + width // 2 + 25_000)
    edge = board.GetBoardEdgesBoundingBox()
    for distance in (600_000, 800_000, 1_000_000, 1_250_000, 1_500_000, 2_000_000):
        for angle in range(0, 360, 45):
            _check_stop(clock, cancel_dir)
            t = math.radians(angle)
            b = (a[0] + round(distance * math.cos(t)), a[1] + round(distance * math.sin(t)))
            if (box.GetLeft() - radius - rules['clearance'] < b[0] < box.GetRight() + radius + rules['clearance']
                    and box.GetTop() - radius - rules['clearance'] < b[1] < box.GetBottom() + radius + rules['clearance']):
                continue
            if any(x < b[0] < u and y < b[1] < v for x, y, u, v in obstacles):
                continue
            if not (edge.GetLeft() + margin < b[0] < edge.GetRight() - margin
                    and edge.GetTop() + margin < b[1] < edge.GetBottom() - margin):
                continue
            # Reuse the same visibility primitive for a short escape, and keep
            # its bound tighter than the general leg's bounded search.
            from .acquisition_local_return import _obstacle_path
            path = _obstacle_path(a, b, front, clock=clock, cancel_dir=cancel_dir)
            if path is None or sum(math.dist(p, q) for p, q in zip(path, path[1:])) > 2_000_000:
                continue
            via = k.PCB_VIA(board)
            via.SetPosition(k.VECTOR2I(*b))
            via.SetWidth(rules['diameter'])
            via.SetDrill(rules['drill'])
            via.SetViaType(k.VIATYPE_THROUGH)
            via.SetLayerPair(k.F_Cu, k.B_Cu)
            via.SetNetCode(terminal.GetNetCode())
            via.SetLocked(True)
            board.Add(via)
            return [*_track(board, path, terminal.GetNetCode(), layer, width), via]
    raise ValueError('no local plane tie outside the land within 2 mm')


def copper_identity(item):
    import pcbnew as k
    result = dict(uuid=item.m_Uuid.AsString(), kind=item.GetClass(), net=item.GetNetname(), locked=item.IsLocked())
    if item.GetClass() == 'PCB_VIA':
        result.update(at_nm=position(item), diameter_nm=int(item.GetWidth(k.PADSTACK.ALL_LAYERS)),
                      drill_nm=int(item.GetDrillValue()), layers=[int(item.TopLayer()), int(item.BottomLayer())])
    else:
        result.update(start_nm=[item.GetStart().x, item.GetStart().y], end_nm=[item.GetEnd().x, item.GetEnd().y],
                      width_nm=int(item.GetWidth()), layer=int(item.GetLayer()))
    return result


def seed(board_path, document, *, clock, cancel_dir=None):
    """Write only a disposable candidate. Required failures name the relation."""
    import pcbnew as k
    from .placement_job import JobRefusal, _check_stop
    validate(document)
    board = k.LoadBoard(str(board_path))
    if board is None or board.GetCopperLayerCount() != 2:
        raise JobRefusal('local decoupling requires a native two-layer board')
    before = digest(board_path)
    rows, protected, tied = [], [], set()
    for relation in document['relations']:
        _check_stop(clock, cancel_dir)
        added = []
        try:
            for leg in ('supply', 'return'):
                first, second = [pad(board, p) for p in relation[leg]]
                added += _leg(board, first, second, clock=clock, cancel_dir=cancel_dir)
                for plane in document['planes']:
                    if plane['net'] == second.GetNetname():
                        target = board.GetLayerID(plane['layer'])
                        added += _tie(board, second, target, clock=clock, cancel_dir=cancel_dir)
                        tied.add(second.m_Uuid.AsString())
                tied.add(first.m_Uuid.AsString())
        except ValueError as exc:
            raise JobRefusal('local decoupling ' + relation['id'] + ': ' + str(exc)) from exc
        protected += [copper_identity(item) for item in added]
        rows.append(dict(id=relation['id'], copper=[item.m_Uuid.AsString() for item in added]))
    other = []
    if document['tie_other_pads']:
        for fp in sorted(board.GetFootprints(), key=lambda fp: fp.GetReference()):
            for terminal in sorted(fp.Pads(), key=lambda p: (p.GetNumber(), p.m_Uuid.AsString())):
                for plane in document['planes']:
                    if terminal.GetNetname() != plane['net'] or terminal.m_Uuid.AsString() in tied:
                        continue
                    try:
                        added = _tie(board, terminal, board.GetLayerID(plane['layer']), clock=clock, cancel_dir=cancel_dir)
                    except ValueError as exc:
                        other.append(dict(pad=terminal.m_Uuid.AsString(), status='left_to_router', reason=str(exc)))
                        continue
                    tied.add(terminal.m_Uuid.AsString())
                    protected += [copper_identity(item) for item in added]
                    other.append(dict(pad=terminal.m_Uuid.AsString(), status='seeded' if added else 'already_on_layer'))
    # Native SaveBoard also rewrites the sibling project with defaults. The
    # operation owns copper only; preserve the exact authored project and PRL.
    siblings = {Path(board_path).with_suffix(suffix): None for suffix in ('.kicad_pro', '.kicad_prl')}
    siblings = {path: path.read_bytes() if path.exists() else None for path in siblings}
    try:
        if not k.SaveBoard(str(board_path), board):
            raise JobRefusal('local decoupling: native board save failed')
    finally:
        for path, raw in siblings.items():
            if raw is not None:
                path.write_bytes(raw)
            elif path.exists():
                path.unlink()
    receipt = dict(schema='agent-ee.local-decoupling-seed/1', source_sha256=before,
                   board_sha256=digest(board_path), contract=document, relations=rows,
                   protected=protected, other_pads=other)
    verify(board_path, receipt)
    return receipt


def verify(board_path, receipt):
    import pcbnew as k
    from .placement_job import JobRefusal
    board = k.LoadBoard(str(board_path))
    items = {t.m_Uuid.AsString(): copper_identity(t) for t in board.GetTracks()}
    # JSON roundtrip normalizes tuples in the in-memory native receipt.
    import json
    for row in json.loads(json.dumps(receipt['protected'])):
        actual = items.get(row['uuid'])
        if actual is None or json.loads(json.dumps(actual)) != row:
            raise JobRefusal('local decoupling protected copper missing/changed: ' + row['uuid'])
    return dict(status='pass', board_sha256=digest(board_path), count=len(receipt['protected']))
