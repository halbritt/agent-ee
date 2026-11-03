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


def validate_topology(document):
    """Generation feasibility is separate from measuring an existing board."""
    validate(document)
    plane_returns = {}
    for row in document['relations']:
        # Separate entries for a shared device pin necessarily bypass each
        # other's capacitor. Placement cannot repair this declared topology.
        for leg in ('supply', 'return'):
            first, second = row[leg]
            if first['net'] not in {p['net'] for p in document['planes']}:
                continue
            key = (first['reference'], first['pad'], first['net'])
            previous = plane_returns.get(key)
            if previous is not None and previous[1] != second:
                raise ValueError(row['id'] + ' and ' + previous[0] + ': shared plane-net device pad ' +
                                 first['reference'] + '.' + first['pad'] +
                                 ' cannot reach separate capacitor plane entries without bypass; declare one return cell')
            plane_returns[key] = (row['id'], second)
    adjacency, entries = {}, {}
    def terminal_key(endpoint):
        return tuple(endpoint[k] for k in ('reference', 'pad', 'net'))
    for row in document['relations']:
        for leg in ('supply', 'return'):
            first, second = map(terminal_key, row[leg])
            adjacency.setdefault(first, set()).add(second)
            adjacency.setdefault(second, set()).add(first)
            if second[2] in {p['net'] for p in document['planes']}:
                entries[second] = row['id']
    for row in document['relations']:
        for leg in ('supply', 'return'):
            first, blocked = map(terminal_key, row[leg])
            reached, pending = {first}, [first]
            while pending:
                node = pending.pop()
                if node in entries and node != blocked:
                    raise ValueError(row['id'] + ' and ' + entries[node] + ': declared local copper at ' +
                                     first[0] + '.' + first[1] + ' bypasses its capacitor through another plane entry')
                for other in adjacency.get(node, set()) - reached - {blocked}:
                    reached.add(other)
                    pending.append(other)
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


def _obstacles(board, net, layer, margin, endpoints=()):
    items = ([p for fp in board.GetFootprints() for p in fp.Pads()]
             + list(board.GetTracks()) + list(board.GetDrawings()))
    allowed = {p.m_Uuid.AsString() for p in endpoints}
    return [_box(item, margin) for item in items if item.IsOnLayer(layer)
            and item.m_Uuid.AsString() not in allowed
            and (item.GetClass() in ('PAD', 'PCB_VIA') or not hasattr(item, 'GetNetCode') or item.GetNetCode() != net)]


def _layers(board, terminal):
    import pcbnew as k
    layers = [layer for layer in board.GetEnabledLayers().CuStack() if terminal.FlashLayer(layer)]
    if (terminal.GetAttribute() == k.PAD_ATTRIB_SMD and len(layers) == 1
            or terminal.GetAttribute() == k.PAD_ATTRIB_PTH and len(layers) == 2):
        return layers
    raise ValueError('unsupported local pad ' + terminal.GetParentFootprint().GetReference() + '.' + terminal.GetNumber())


def _track(board, points, net, layer, width):
    import pcbnew as k
    result = []
    for a, b in zip(points, points[1:]):
        if a == b:
            continue
        existing = next((t for t in board.GetTracks() if t.GetClass() == 'PCB_TRACK'
                         and t.GetNetCode() == net and t.GetLayer() == layer and t.GetWidth() == width
                         and {position_point(t.GetStart()), position_point(t.GetEnd())} == {a, b}
                         and t.IsLocked()), None)
        if existing is not None:
            result.append(existing)
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


def position_point(p):
    return int(p.x), int(p.y)


def _leg(board, first, second, *, clock, cancel_dir):
    from .acquisition.local_return import _obstacle_path
    common = set(_layers(board, first)) & set(_layers(board, second))
    if not common:
        raise ValueError('local leg endpoints must share a copper face')
    layer = min(common)
    rules = _rules(board, first)
    # A routing default can neck into a small land; the fabrication minimum
    # remains the board rule, not the class's preferred routing width.
    width = max(int(board.GetDesignSettings().m_TrackMinWidth),
                min(rules['width'], *(min(p.GetSize().x, p.GetSize().y) for p in (first, second))))
    boxes = _obstacles(board, first.GetNetCode(), layer, rules['clearance'] + width // 2 + 25_000, (first, second))
    path = _obstacle_path(position(first), position(second), boxes, clock=clock, cancel_dir=cancel_dir)
    if path is None:
        raise ValueError('no clear local surface path within the 12 mm search budget')
    return _track(board, path, first.GetNetCode(), layer, width)


def _junction_in_pad(first, second, terminal, layer):
    from . import local_loop_geometry as geo
    import pcbnew as k
    def line(item):
        if item.GetClass() == 'PCB_TRACK':
            return position_point(item.GetStart()), position_point(item.GetEnd())
        return position(item), position(item)
    a, b = line(first)
    c, d = line(second)
    contacts = list(geo.closest((a, b), (c, d)))
    for t in geo.intersections(a, b, c, d):
        point = tuple(x + t * (y-x) for x, y in zip(a, b))
        if math.dist(point, geo.project(point, c, d)) < 1:
            contacts.append(point)
    return all(terminal.GetEffectiveShape(layer).Collide(k.VECTOR2I(round(p[0]), round(p[1])), 0)
               for p in contacts)


def _clear_tie_path(board, path, terminal, layer, width):
    import pcbnew as k
    for a, b in zip(path, path[1:]):
        segment = k.PCB_TRACK(board)
        segment.SetStart(k.VECTOR2I(*a))
        segment.SetEnd(k.VECTOR2I(*b))
        segment.SetWidth(width)
        segment.SetLayer(layer)
        for other in board.GetTracks():
            if other.IsOnLayer(layer) and segment.GetEffectiveShape(layer).Collide(other.GetEffectiveShape(layer), 1):
                if not _junction_in_pad(segment, other, terminal, layer):
                    return False
    return True


def _tie(board, terminal, plane, *, clock, cancel_dir, away_from=None):
    import pcbnew as k
    from .placement_job import _check_stop
    if terminal.FlashLayer(plane):
        return []  # A plated land already reaches this layer.
    layer = _layers(board, terminal)[0]
    rules = _rules(board, terminal)
    width = max(int(board.GetDesignSettings().m_TrackMinWidth),
                min(rules['width'], terminal.GetSize().x, terminal.GetSize().y))
    a = position(terminal)
    box = terminal.GetBoundingBox()
    # Place the drill outside the solder land. Search short radial escapes;
    # all copper layers constrain a through-via, not only the pad's face.
    radius = rules['diameter'] // 2
    margin = rules['clearance'] + radius + 25_000
    obstacles = [_box(p, max(rules['clearance'], _rules(board, p)['clearance']) + radius + 25_000)
                 for fp in board.GetFootprints() for p in fp.Pads()]
    obstacles += [_box(t, margin) for t in board.GetTracks()]
    obstacles += [b for l in board.GetEnabledLayers().CuStack()
                  for b in _obstacles(board, terminal.GetNetCode(), l, margin)]
    front = _obstacles(board, terminal.GetNetCode(), layer, rules['clearance'] + width // 2 + 25_000, (terminal,))
    edge = board.GetBoardEdgesBoundingBox()
    angles = sorted(range(0, 360, 45), key=lambda angle: (
        0 if away_from is None else (away_from[0]-a[0])*math.cos(math.radians(angle))
        + (away_from[1]-a[1])*math.sin(math.radians(angle)), angle))
    for distance in (600_000, 800_000, 1_000_000, 1_250_000, 1_500_000, 2_000_000):
        for angle in angles:
            _check_stop(clock, cancel_dir)
            t = math.radians(angle)
            b = (a[0] + round(distance * math.cos(t)), a[1] + round(distance * math.sin(t)))
            if away_from is not None and sum((v-u)*(w-u) for u, v, w in zip(a, b, away_from)) >= 0:
                continue
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
            from .acquisition.local_return import _obstacle_path
            path = _obstacle_path(a, b, front, clock=clock, cancel_dir=cancel_dir)
            if path is None or sum(math.dist(p, q) for p, q in zip(path, path[1:])) > 2_000_000:
                continue
            if not _clear_tie_path(board, path, terminal, layer, width):
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
    validate_topology(document)
    board = k.LoadBoard(str(board_path))
    if board is None or board.GetCopperLayerCount() != 2:
        raise JobRefusal('local decoupling requires a native two-layer board')
    before = digest(board_path)
    rows, protected, tied, plane_ties = [], [], set(), {}
    for relation in document['relations']:
        _check_stop(clock, cancel_dir)
        added, legs = [], []
        try:
            for leg in ('supply', 'return'):
                first, second = [pad(board, p) for p in relation[leg]]
                leg_items = _leg(board, first, second, clock=clock, cancel_dir=cancel_dir)
                added += leg_items
                legs.append(dict(kind=leg, pads=[p.m_Uuid.AsString() for p in (first, second)],
                                 tracks=[t.m_Uuid.AsString() for t in leg_items]))
                for plane in document['planes']:
                    if plane['net'] == second.GetNetname():
                        target = board.GetLayerID(plane['layer'])
                        tie_key = (second.m_Uuid.AsString(), target)
                        if tie_key not in plane_ties:
                            plane_ties[tie_key] = _tie(board, second, target, clock=clock, cancel_dir=cancel_dir,
                                                       away_from=position(first))
                        tie_items = plane_ties[tie_key]
                        added += tie_items
                        legs.append(dict(kind='plane_tie', pads=[second.m_Uuid.AsString()],
                                         tracks=[t.m_Uuid.AsString() for t in tie_items]))
                        tied.add(second.m_Uuid.AsString())
                tied.add(first.m_Uuid.AsString())
        except ValueError as exc:
            raise JobRefusal('local decoupling ' + relation['id'] + ': ' + str(exc)) from exc
        protected += [copper_identity(item) for item in added]
        rows.append(dict(id=relation['id'], copper=[item.m_Uuid.AsString() for item in added], legs=legs))
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
                    other.append(dict(pad=terminal.m_Uuid.AsString(), status='seeded' if added else 'already_on_layer',
                                      copper=[t.m_Uuid.AsString() for t in added]))
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
                   protected=list({r['uuid']: r for r in protected}.values()), other_pads=other)
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
    geometry = verify_geometry(board, receipt)
    return dict(status='pass', board_sha256=digest(board_path), count=len(receipt['protected']), geometry=geometry)


def verify_geometry(board, receipt):
    """Same-net pad contacts are assembly/topology defects even when DRC permits them."""
    from .placement_job import JobRefusal
    pads = [p for fp in board.GetFootprints() for p in fp.Pads()]
    generated = {r['uuid'] for r in receipt['protected']}
    tracks = {t.m_Uuid.AsString(): t for t in board.GetTracks() if t.m_Uuid.AsString() in generated}
    allowed, ties, groups = {}, {}, {}
    owners = {}
    for relation in receipt['relations']:
        for uid in relation['copper']:
            owners.setdefault(uid, set()).add(relation['id'])
        for index, leg in enumerate(relation['legs']):
            for uid in leg['tracks']:
                allowed.setdefault(uid, set()).update(leg['pads'])
                groups.setdefault(uid, set()).add((relation['id'], index))
                if leg['kind'] == 'plane_tie':
                    ties.setdefault(uid, set()).update(leg['tracks'])
    for row in receipt['other_pads']:
        for uid in row.get('copper', []):
            allowed.setdefault(uid, set()).add(row['pad'])
            ties[uid] = set(row['copper'])
            groups.setdefault(uid, set()).add(('other', row['pad']))
    for uid, item in tracks.items():
        via = item.GetClass() == 'PCB_VIA'
        clearance = _rules(board, item)['clearance']
        label = 'local decoupling ' + ', '.join(sorted(owners.get(uid, {'plane ties'}))) + ' geometry: '
        for terminal in pads:
            for layer in board.GetEnabledLayers().CuStack():
                if not item.IsOnLayer(layer) or not terminal.IsOnLayer(layer):
                    continue
                gap = max(clearance, _rules(board, terminal)['clearance']) if via else 1
                shape = terminal.GetEffectiveShape(layer)
                collision = item.GetEffectiveShape(layer).Collide(shape, gap)
                if via:
                    # An invalid drill wider than its annulus still cannot enter a pad.
                    collision |= shape.Collide(item.GetPosition(), int(item.GetDrillValue()) // 2 + gap)
                if collision:
                    if via or terminal.m_Uuid.AsString() not in allowed.get(uid, set()):
                        raise JobRefusal(label + uid + ' touches/violates pad clearance ' +
                                         terminal.GetParentFootprint().GetReference() + '.' + terminal.GetNumber())
        if via:
            for other_uid, other in tracks.items():
                if other_uid == uid or other_uid in ties.get(uid, set()):
                    continue
                for layer in board.GetEnabledLayers().CuStack():
                    if other.IsOnLayer(layer) and item.GetEffectiveShape(layer).Collide(other.GetEffectiveShape(layer), 1):
                        raise JobRefusal(label + 'plane via touches another generated leg: ' + uid + ', ' + other_uid)
        else:
            layer = item.GetLayer()
            for other_uid, other in tracks.items():
                if other_uid == uid or groups.get(uid, set()) & groups.get(other_uid, set()) or not other.IsOnLayer(layer):
                    continue
                if item.GetEffectiveShape(layer).Collide(other.GetEffectiveShape(layer), 1):
                    shared = allowed.get(uid, set()) & allowed.get(other_uid, set())
                    if not any(p.m_Uuid.AsString() in shared and _junction_in_pad(item, other, p, layer) for p in pads):
                        raise JobRefusal(label + 'generated legs meet outside a declared shared pad: ' + uid + ', ' + other_uid)
    return dict(status='pass', generated_vias=sum(t.GetClass() == 'PCB_VIA' for t in tracks.values()),
                pad_clearance='effective net-class clearance on every pad, including same-net pads',
                tracks='declared endpoint pads only')
