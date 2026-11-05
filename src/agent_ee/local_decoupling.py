"""Declared local copper, independent of reference names and recipes.

The visibility search proposes geometry. The caller runs native DRC before the
router sees it, and verifies the locked items again on the final saved board.
"""
import heapq
import math
from pathlib import Path
from .storage import digest

SCHEMA = 'agent-ee.local-decoupling/1'
BASIS = 'Agent-selected measurement requirement; not an owner or datasheet ruling'


def validate(document):
    from .hardware_interface import exact_keys, text
    exact_keys({k: v for k, v in document.items() if k != 'banks'} if isinstance(document, dict) else document,
               'schema relations planes tie_other_pads')
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
    if 'banks' in document and document['banks'] != banks(document):
        raise ValueError('banks must match automatically derived shared-device components')
    return document


def endpoint_key(endpoint):
    return tuple(endpoint[k] for k in ('reference', 'pad', 'net'))


def banks(document):
    """Connected components of relations sharing either device terminal."""
    components = []
    for relation in document['relations']:
        terminals = {endpoint_key(relation[leg][0]) for leg in ('supply', 'return')}
        joined = [group for group in components if terminals & group[1]]
        ids = {relation['id']}
        for group in joined:
            ids.update(group[0])
            terminals.update(group[1])
            components.remove(group)
        components.append((ids, terminals))
    order = {row['id']: i for i, row in enumerate(document['relations'])}
    components.sort(key=lambda group: min(order[rid] for rid in group[0]))
    return [dict(id='bank-' + str(i), relations=sorted(ids, key=order.get))
            for i, (ids, _) in enumerate(components, 1)]


def validate_topology(document):
    """Refuse a device terminal that would itself require a capacitor entry."""
    validate(document)
    plane_nets = {p['net'] for p in document['planes']}
    entries = {endpoint_key(row[leg][1]): row['id'] for row in document['relations']
               for leg in ('supply', 'return') if row[leg][1]['net'] in plane_nets}
    for row in document['relations']:
        for leg in ('supply', 'return'):
            first = row[leg][0]
            key = endpoint_key(first)
            if key in entries:
                raise ValueError(row['id'] + ' and ' + entries[key] + ': device plane pad ' +
                                 first['reference'] + '.' + first['pad'] + ' is also a capacitor entry')
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


def _separation_floor(board):
    from .placement_job import JobRefusal
    value = int(board.GetDesignSettings().m_MinClearance)
    if value <= 0 and board.GetFileName():
        # KiCad can retain a cached default project in a long-lived process.
        # Read the authored rule, never a class preference or an assumed fab.
        from .storage import read_json
        project = Path(board.GetFileName()).with_suffix('.kicad_pro')
        if project.is_file():
            authored = read_json(project).get('board', {}).get('design_settings', {}).get('rules', {}).get('min_clearance', 0)
            if type(authored) in (int, float) and math.isfinite(authored) and authored > 0:
                value = round(authored * 1e6)
                board.GetDesignSettings().m_MinClearance = value
    if value <= 0:
        raise JobRefusal('local copper requires a nonzero authored minimum copper clearance; '
                         'no applicable fabrication profile is bound to this board')
    return value


def _same_net_search_gap(board):
    return _separation_floor(board) + 25_000


def _junction_polygons(first, second, layer, shared_pads, *, copper_inside=False, pad_margin=0):
    import pcbnew as k
    inside = k.SHAPE_POLY_SET()
    for terminal in shared_pads:
        if terminal.IsOnLayer(layer) and terminal.GetNetCode() == first.GetNetCode() == second.GetNetCode():
            terminal.TransformShapeToPolygon(inside, layer, pad_margin, 1, k.ERROR_INSIDE)
    inside.Simplify()
    copper = []
    for item in (first, second):
        shape = k.SHAPE_POLY_SET()
        item.TransformShapeToPolygon(shape, layer, 0, 1, k.ERROR_INSIDE if copper_inside else k.ERROR_OUTSIDE)
        copper.append(shape)
    return inside, copper


def junction_in_pad(first, second, layer, shared_pads):
    """The actual copper overlap belongs entirely to a declared pad junction."""
    if not shared_pads or not first.GetEffectiveShape(layer).Collide(second.GetEffectiveShape(layer), 0):
        return False
    # Inscribed 1 nm polygons localize overlap without outward arc-rounding
    # slivers. Independent-copper gaps still use conservative outside polygons.
    inside, copper = _junction_polygons(first, second, layer, shared_pads, copper_inside=True)
    overlap = copper[0]
    overlap.BooleanIntersection(copper[1])
    if overlap.IsEmpty():
        return False
    overlap.BooleanSubtract(inside)
    return overlap.IsEmpty()


def _separation_shapes(first, second, layer, shared_pads):
    if not shared_pads:
        return [first.GetEffectiveShape(layer), second.GetEffectiveShape(layer)], None
    # Junction arms need room to separate at the land edge. Bound that opening
    # to one fabrication floor; distant portions must still keep the floor.
    margin = _separation_floor(first.GetBoard()) if junction_in_pad(first, second, layer, shared_pads) else 0
    inside, copper = _junction_polygons(first, second, layer, shared_pads, pad_margin=margin)
    shapes = []
    for outside in copper:
        outside.BooleanSubtract(inside)
        outside.Simplify()
        if outside.IsEmpty():
            return None, None
        shapes.append(outside)
    cross = None
    if margin > 0:
        bare_inside, bare_copper = _junction_polygons(first, second, layer, shared_pads, pad_margin=0)
        cross = []
        for outside in bare_copper:
            outside.BooleanSubtract(bare_inside)
            outside.Simplify()
            cross.append(outside)
    return shapes, cross


def _too_close(first, second, layer, shared_pads, gap):
    gap = max(1, gap)  # A zero native floor still cannot permit contact.
    if not first.GetEffectiveShape(layer).Collide(second.GetEffectiveShape(layer), gap):
        return False
    shapes, cross = _separation_shapes(first, second, layer, shared_pads)
    if shapes is None:
        return False
    if shapes[0].Collide(shapes[1], gap):
        return True
    if cross is not None:
        cross_gap = min(gap, _separation_floor(first.GetBoard()) - 500)
        if not cross[1].IsEmpty() and shapes[0].Collide(cross[1], cross_gap):
            return True
        if not cross[0].IsEmpty() and shapes[1].Collide(cross[0], cross_gap):
            return True
    return False


def _copper_boxes(item, margin):
    if item.GetClass() != 'PCB_TRACK':
        return [_box(item, margin)]
    a, b = position_point(item.GetStart()), position_point(item.GetEnd())
    count = max(1, math.ceil(math.dist(a, b) / 1_000_000)) if a[0] != b[0] and a[1] != b[1] else 1
    radius = int(item.GetWidth()) // 2 + margin + 1
    points = [tuple(round(x + i*(y-x)/count) for x,y in zip(a,b)) for i in range(count+1)]
    return [(min(a[0],b[0])-radius, min(a[1],b[1])-radius,
             max(a[0],b[0])+radius, max(a[1],b[1])+radius) for a,b in zip(points,points[1:])]


def _subtract_box(box, opening):
    x, y, u, v = box
    a, b, c, d = opening
    if c <= x or a >= u or d <= y or b >= v:
        return [box]
    a, b, c, d = max(x, a), max(y, b), min(u, c), min(v, d)
    return [r for r in ((x, y, a, v), (c, y, u, v), (a, y, c, b), (a, d, c, v))
            if r[0] < r[2] and r[1] < r[3]]


def _obstacles(board, net, layer, margin, endpoints=(), *, joinable=()):
    items = ([p for fp in board.GetFootprints() for p in fp.Pads()]
             + list(board.GetTracks()) + list(board.GetDrawings()))
    allowed = {p.m_Uuid.AsString() for p in endpoints}
    boxes = []
    for item in items:
        if not item.IsOnLayer(layer) or item.m_Uuid.AsString() in allowed:
            continue
        if item.GetClass() == 'PCB_TRACK' and item.GetNetCode() == net and item.m_Uuid.AsString() in joinable:
            continue
        track_margin = margin
        if item.GetClass() in ('PCB_TRACK', 'PCB_VIA') and item.GetNetCode() == net:
            # Isolated same-net copper needs the fabrication floor, not the class preference.
            track_margin = max(0, margin - _rules(board, item)['clearance'] + _separation_floor(board))
        pieces = _copper_boxes(item, track_margin)
        if item.GetClass() == 'PCB_TRACK' and item.GetNetCode() == net:
            # Allow a new branch to leave its declared shared pad. The rest
            # of this track, including an escape's interior, remains blocked.
            for terminal in endpoints:
                if item.GetEffectiveShape(layer).Collide(terminal.GetEffectiveShape(layer), 1):
                    pieces = [part for box in pieces for part in _subtract_box(box, _box(terminal, track_margin))]
        boxes.extend(pieces)
    return boxes


def _obstacle_path(a, b, boxes, *, clock, cancel_dir):
    """Shortest visibility path around expanded copper rectangles, at most 12 mm.

    This proposes one front-copper seed. Only the subsequent full native DRC
    can establish clearance; the rectangles are a conservative search model.
    """
    from .placement_job import JobRefusal, _check_stop
    a, b = tuple(a), tuple(b)
    if len(boxes) > 1024:
        raise JobRefusal('local return: local obstacle population exceeds 1024')
    limit = 12_000_000
    def inside(p, box):
        x, y, u, v = box
        return x < p[0] < u and y < p[1] < v
    def crosses(p, q, box):
        low, high = 0.0, 1.0
        for origin, end, left, right in zip(p, q, box[:2], box[2:]):
            delta = end - origin
            if not delta:
                if not left < origin < right:
                    return False
            else:
                start, stop = sorted(((left-origin)/delta, (right-origin)/delta))
                low, high = max(low, start), min(high, stop)
        return low < high
    _check_stop(clock, cancel_dir)
    if any(inside(p, box) for p in (a, b) for box in boxes):
        return None
    nodes = {a, b}
    for x, y, u, v in boxes:
        _check_stop(clock, cancel_dir)
        for point in ((x, y), (u, y), (u, v), (x, v)):
            _check_stop(clock, cancel_dir)
            if math.dist(a, point) + math.dist(point, b) <= limit and not any(inside(point, r) for r in boxes):
                nodes.add(point)
            if len(nodes) > 512:
                raise JobRefusal('local return: local obstacle search exceeds 512 vertices')
    nodes = sorted(nodes)
    queue, best, previous = [(0.0, a)], {a: 0.0}, {}
    while queue:
        _check_stop(clock, cancel_dir)
        length, point = heapq.heappop(queue)
        if length != best[point]:
            continue
        if point == b:
            path = [b]
            while path[-1] != a:
                path.append(previous[path[-1]])
            return list(reversed(path))
        for other in nodes:
            _check_stop(clock, cancel_dir)
            distance = length + math.dist(point, other)
            if (other == point or distance >= best.get(other, math.inf)
                    or distance + math.dist(other, b) > limit
                    or any(crosses(point, other, box) for box in boxes)):
                continue
            best[other], previous[other] = distance, point
            heapq.heappush(queue, (distance, other))
    return None


def _local_path(a, b, boxes, *, clock, cancel_dir):
    from .placement_job import JobRefusal
    # Each accepted path stays inside the region whose obstacles were counted.
    # The final region covers every path allowed by the unchanged 12 mm bound.
    refusal = None
    for margin in (0, 500_000, 1_000_000, 2_000_000, 4_000_000, 6_000_000):
        region = (min(a[0], b[0])-margin, min(a[1], b[1])-margin,
                  max(a[0], b[0])+margin, max(a[1], b[1])+margin)
        x, y, u, v = region
        local = sorted({box for box in boxes if box[0] <= u and box[2] >= x and box[1] <= v and box[3] >= y})
        try:
            path = _obstacle_path(a, b, local, clock=clock, cancel_dir=cancel_dir)
        except JobRefusal as exc:
            if not any(text in str(exc) for text in ('obstacle population', 'obstacle search exceeds')):
                raise
            refusal = exc
            continue
        if path is not None and all(x <= p[0] <= u and y <= p[1] <= v for p in path):
            return path
    if refusal is not None:
        raise refusal
    return None


def _layers(board, terminal):
    import pcbnew as k
    layers = [layer for layer in board.GetEnabledLayers().CuStack() if terminal.FlashLayer(layer)]
    if (terminal.GetAttribute() == k.PAD_ATTRIB_SMD and len(layers) == 1
            or terminal.GetAttribute() == k.PAD_ATTRIB_PTH and len(layers) == 2):
        return layers
    raise ValueError('unsupported local pad ' + terminal.GetParentFootprint().GetReference() + '.' + terminal.GetNumber())


def _track(board, points, net, layer, width, *, reusable=()):
    import pcbnew as k
    result = []
    for a, b in zip(points, points[1:]):
        if a == b:
            continue
        existing = next((t for t in board.GetTracks() if t.GetClass() == 'PCB_TRACK'
                         and t.m_Uuid.AsString() in reusable
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


def _leg(board, first, second, *, clock, cancel_dir, reusable=(), joinable=()):
    common = set(_layers(board, first)) & set(_layers(board, second))
    if not common:
        raise ValueError('local leg endpoints must share a copper face')
    layer = min(common)
    rules = _rules(board, first)
    # A routing default can neck into a small land; the fabrication minimum
    # remains the board rule, not the class's preferred routing width.
    width = max(int(board.GetDesignSettings().m_TrackMinWidth),
                min(rules['width'], *(min(p.GetSize().x, p.GetSize().y) for p in (first, second))))
    boxes = _obstacles(board, first.GetNetCode(), layer, rules['clearance'] + width // 2 + 25_000, (first, second), joinable=joinable)
    path = _local_path(position(first), position(second), boxes, clock=clock, cancel_dir=cancel_dir)
    if path is None:
        raise ValueError('no clear local surface path within the 12 mm search budget')
    return _track(board, path, first.GetNetCode(), layer, width, reusable=reusable)


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
        segment.SetNetCode(terminal.GetNetCode())
        for other in board.GetTracks():
            if other.IsOnLayer(layer) and _too_close(segment, other, layer, [terminal], _same_net_search_gap(board)):
                return False
    return True


def _via_pad_collision(board, via, terminal, rules):
    gap = max(rules['clearance'], _rules(board, terminal)['clearance']) + 25_000
    for layer in board.GetEnabledLayers().CuStack():
        if terminal.IsOnLayer(layer):
            shape = terminal.GetEffectiveShape(layer)
            if (via.GetEffectiveShape(layer).Collide(shape, gap)
                    or shape.Collide(via.GetPosition(), rules['drill'] // 2 + gap)):
                return True
    return False


def _via_obstacles(board, terminal, rules):
    """Collect a conservative obstacle set once for the complete 2 mm via disk."""
    a = position(terminal)
    radius = max(rules['diameter'], rules['drill']) // 2
    def near(item, gap):
        x, y, u, v = _box(item, radius + gap)
        return x <= a[0]+2_000_000 and u >= a[0]-2_000_000 and y <= a[1]+2_000_000 and v >= a[1]-2_000_000
    pads = [p for fp in board.GetFootprints() for p in fp.Pads()
            if near(p, max(rules['clearance'], _rules(board, p)['clearance']) + 25_000)]
    tracks = [t for t in board.GetTracks()
              if near(t, max(rules['clearance'], _same_net_search_gap(board)) + 25_000)]
    drawings = [d for d in board.GetDrawings() if near(d, rules['clearance'] + 25_000)]
    return pads, tracks, drawings


def _via_site_blocked(board, via, terminal, rules, obstacles=None):
    pads, tracks, drawings = _via_obstacles(board, terminal, rules) if obstacles is None else obstacles
    if any(_via_pad_collision(board, via, p, rules) for p in pads):
        return True
    for item in tracks:
        gap = _same_net_search_gap(board) if item.GetNetCode() == terminal.GetNetCode() else rules['clearance'] + 25_000
        for layer in board.GetEnabledLayers().CuStack():
            if item.IsOnLayer(layer) and via.GetEffectiveShape(layer).Collide(item.GetEffectiveShape(layer), gap):
                return True
    point = position(via)
    for item in drawings:
        if any(item.IsOnLayer(layer) for layer in board.GetEnabledLayers().CuStack()):
            x, y, u, v = _box(item, rules['clearance'] + rules['diameter'] // 2 + 25_000)
            if x < point[0] < u and y < point[1] < v:
                return True
    return False


def _via_centres(a, angles):
    seen = set()
    for distance in (600_000, 800_000, 1_000_000, 1_250_000, 1_500_000, 2_000_000):
        for angle in angles:
            t = math.radians(angle)
            point = (a[0] + round(distance * math.cos(t)), a[1] + round(distance * math.sin(t)))
            if point not in seen:
                seen.add(point)
                yield point
    # Resolve narrow windows between the original radial samples, within the
    # same disk. Native shapes, clearance and the full path guard still decide.
    grid = [(x, y) for x in range(-20, 21) for y in range(-20, 21) if 0 < x*x + y*y <= 400]
    for x, y in sorted(grid, key=lambda p: (p[0]*p[0]+p[1]*p[1], p)):
        point = (a[0] + x*100_000, a[1] + y*100_000)
        if point not in seen:
            seen.add(point)
            yield point


def _tie(board, terminal, plane, *, clock, cancel_dir, away_from=None, reusable=()):
    import pcbnew as k
    from .placement_job import _check_stop
    if terminal.FlashLayer(plane):
        return []  # A plated land already reaches this layer.
    layer = _layers(board, terminal)[0]
    rules = _rules(board, terminal)
    width = max(int(board.GetDesignSettings().m_TrackMinWidth),
                min(rules['width'], terminal.GetSize().x, terminal.GetSize().y))
    a = position(terminal)
    margin = rules['clearance'] + rules['diameter'] // 2 + 25_000
    front = _obstacles(board, terminal.GetNetCode(), layer, rules['clearance'] + width // 2 + 25_000, (terminal,))
    edge = board.GetBoardEdgesBoundingBox()
    angles = sorted(set(range(0, 360, 10)) | set(range(0, 360, 45)), key=lambda angle: (
        0 if away_from is None else (away_from[0]-a[0])*math.cos(math.radians(angle))
        + (away_from[1]-a[1])*math.sin(math.radians(angle)), angle))
    via_obstacles = _via_obstacles(board, terminal, rules)
    for b in _via_centres(a, angles):
        _check_stop(clock, cancel_dir)
        if away_from is not None and sum((v-u)*(w-u) for u, v, w in zip(a, b, away_from)) >= 0:
            continue
        via = k.PCB_VIA(board)
        via.SetPosition(k.VECTOR2I(*b))
        via.SetWidth(rules['diameter'])
        via.SetDrill(rules['drill'])
        via.SetViaType(k.VIATYPE_THROUGH)
        via.SetLayerPair(k.F_Cu, k.B_Cu)
        via.SetNetCode(terminal.GetNetCode())
        if _via_pad_collision(board, via, terminal, rules):
            continue
        if _via_site_blocked(board, via, terminal, rules, via_obstacles):
            continue
        if not (edge.GetLeft() + margin < b[0] < edge.GetRight() - margin
                and edge.GetTop() + margin < b[1] < edge.GetBottom() - margin):
            continue
        path = _local_path(a, b, front, clock=clock, cancel_dir=cancel_dir)
        if path is None or sum(math.dist(p, q) for p, q in zip(path, path[1:])) > 2_000_000:
            continue
        if not _clear_tie_path(board, path, terminal, layer, width):
            continue
        via.SetLocked(True)
        board.Add(via)
        return [*_track(board, path, terminal.GetNetCode(), layer, width, reusable=reusable), via]
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


def _seed_copper(board, document, *, order, clock, cancel_dir):
    from .placement_job import JobRefusal, _check_stop
    rows, protected, tied, plane_ties = [], [], set(), {}
    bank_ids = {rid: bank['id'] for bank in banks(document) for rid in bank['relations']}
    bank_copper = {bank_id: set() for bank_id in bank_ids.values()}
    device_legs = {}; escapes = set()
    bank_order = {bank['id']: i for i, bank in enumerate(banks(document))}
    def relation_order(relation):
        try:
            length = sum(math.dist(*(position(pad(board, endpoint)) for endpoint in relation[kind]))
                         for kind in ('supply', 'return'))
        except ValueError as exc:
            raise JobRefusal(f"local decoupling {relation['id']}: {exc}") from exc
        return bank_order[bank_ids[relation['id']]], length
    # Reaching nearby capacitors first avoids enclosing a later bank endpoint.
    ordered = sorted(document['relations'], key=relation_order) if order == 'nearest-first' else document['relations']
    for relation in ordered:
        _check_stop(clock, cancel_dir)
        added, legs = [], []
        reusable = bank_copper[bank_ids[relation['id']]]
        try:
            for leg in ('supply', 'return'):
                first, second = [pad(board, p) for p in relation[leg]]
                shared = device_legs.setdefault((bank_ids[relation['id']], leg, first.GetNetCode()), set())
                leg_items = _leg(board, first, second, clock=clock, cancel_dir=cancel_dir, reusable=reusable,
                                 joinable=shared - escapes)
                shared.update(t.m_Uuid.AsString() for t in leg_items)
                reusable.update(t.m_Uuid.AsString() for t in leg_items)
                added += leg_items
                legs.append(dict(kind=leg, pads=[p.m_Uuid.AsString() for p in (first, second)],
                                 tracks=[t.m_Uuid.AsString() for t in leg_items]))
                for plane in document['planes']:
                    if plane['net'] == second.GetNetname():
                        target = board.GetLayerID(plane['layer'])
                        tie_key = (bank_ids[relation['id']], second.m_Uuid.AsString(), target)
                        if tie_key not in plane_ties:
                            plane_ties[tie_key] = _tie(board, second, target, clock=clock, cancel_dir=cancel_dir,
                                                       away_from=position(first), reusable=reusable)
                        tie_items = plane_ties[tie_key]
                        escapes.update(t.m_Uuid.AsString() for t in tie_items)
                        added += tie_items
                        reusable.update(t.m_Uuid.AsString() for t in tie_items)
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
    return rows, protected, other


def seed(board_path, document, *, clock, cancel_dir=None):
    """Write only a disposable candidate. Required failures name the relation."""
    import pcbnew as k
    from .placement_job import JobRefusal, _check_stop
    validate_topology(document)
    board = k.LoadBoard(str(board_path))
    if board is None or board.GetCopperLayerCount() != 2:
        raise JobRefusal('local decoupling requires a native two-layer board')
    before = digest(board_path)
    attempts = []
    for order in ('nearest-first', 'document'):
        _check_stop(clock, cancel_dir)
        board = k.LoadBoard(str(board_path))
        _separation_floor(board)
        try:
            rows, protected, other = _seed_copper(board, document, order=order, clock=clock, cancel_dir=cancel_dir)
            trial = dict(contract=document, relations=rows, protected=protected, other_pads=other)
            verify_geometry(board, trial)
        except JobRefusal as exc:
            attempts.append(dict(order=order, status='refused', reason=str(exc)))
        else:
            attempts.append(dict(order=order, status='seeded'))
            break
    else:
        raise JobRefusal('; '.join(item['order'] + ': ' + item['reason'] for item in attempts))
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
                   protected=list({r['uuid']: r for r in protected}.values()), other_pads=other, banks=banks(document), generation_order=dict(selected=order, attempts=attempts))
    verify(board_path, receipt)
    return receipt


GENERATION_FILE = 'local-decoupling-generation.json'


def bank_fallbacks(bank, members, refusal):
    """Attribute the actual refusal and distinguish its bank-mates' rollback."""
    failed = next((r['id'] for r in members if 'local decoupling ' + r['id'] + ':' in refusal), None)
    if failed is None:
        return [dict(relation=r['id'], bank=bank['id'], refused_relation=None,
                     reason='bank ' + bank['id'] + ' refused without a named relation: ' + refusal)
                for r in members]
    return [dict(relation=r['id'], bank=bank['id'],
                 reason=refusal if r['id'] == failed else
                    'rolled back with bank ' + bank['id'] + ' after relation ' + failed + ' refused')
            for r in members]


def fallback_receipt(board_path, document, reason):
    """An atomic fallback owns no copper; every relation keeps its refusal."""
    relation_banks = {rid: bank['id'] for bank in banks(document) for rid in bank['relations']}
    return dict(schema='agent-ee.local-decoupling-seed/1', source_sha256=digest(board_path),
                board_sha256=digest(board_path), contract=document, relations=[], protected=[], other_pads=[],
                banks=banks(document), generation_order=dict(selected='router-fallback', attempts=[]),
                fallbacks=[dict(relation=r['id'], bank=relation_banks[r['id']], reason=reason)
                           for r in document['relations']])


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


def _geometry_groups(board, receipt):
    pads = [p for fp in board.GetFootprints() for p in fp.Pads()]
    generated = {r['uuid'] for r in receipt['protected']}
    tracks = {t.m_Uuid.AsString(): t for t in board.GetTracks() if t.m_Uuid.AsString() in generated}
    allowed, ties, groups = {}, {}, {}
    owners = {}
    relation_banks = {rid: bank['id'] for bank in banks(receipt['contract']) for rid in bank['relations']}
    for relation in receipt['relations']:
        for uid in relation['copper']:
            owners.setdefault(uid, set()).add(relation['id'])
        for index, leg in enumerate(relation['legs']):
            for uid in leg['tracks']:
                allowed.setdefault(uid, set()).update(leg['pads'])
                groups.setdefault(uid, set()).add((relation['id'], index))
                if leg['kind'] in ('supply', 'return'):
                    groups[uid].add(('bank-leg', relation_banks[relation['id']], leg['kind'], tracks[uid].GetNetname()))
                if leg['kind'] == 'plane_tie':
                    ties.setdefault(uid, set()).update(leg['tracks'])
    for row in receipt['other_pads']:
        for uid in row.get('copper', []):
            allowed.setdefault(uid, set()).add(row['pad'])
            ties[uid] = set(row['copper'])
            groups.setdefault(uid, set()).add(('other', row['pad']))
    return pads, tracks, allowed, ties, groups, owners


def verify_geometry(board, receipt):
    """Same-net pad contacts are assembly/topology defects even when DRC permits them."""
    from .placement_job import JobRefusal
    pads, tracks, allowed, ties, groups, owners = _geometry_groups(board, receipt)
    required_gap = _separation_floor(board)
    for relation in receipt.get('relations', []):
        for leg in relation.get('legs', []):
            if leg.get('kind') == 'plane_tie':
                for pid in leg.get('pads', []):
                    is_junction = any(pid in other_leg.get('pads', [])
                                      for other_leg in relation.get('legs', [])
                                      if other_leg.get('kind') in ('supply', 'return'))
                    if not is_junction:
                        continue
                    terminal = next((p for p in pads if p.m_Uuid.AsString() == pid), None)
                    if terminal is None:
                        continue
                    tie_tracks = [tracks[uid] for uid in leg.get('tracks', [])
                                  if uid in tracks and tracks[uid].GetClass() == 'PCB_TRACK']
                    if len(tie_tracks) > 1:
                        endpoints = {}
                        for t in tie_tracks:
                            for pt in ((t.GetStart().x, t.GetStart().y), (t.GetEnd().x, t.GetEnd().y)):
                                endpoints[pt] = endpoints.get(pt, 0) + 1
                        import pcbnew as k
                        for pt, count in endpoints.items():
                            if count >= 2:
                                for layer in board.GetEnabledLayers().CuStack():
                                    if terminal.IsOnLayer(layer):
                                        if terminal.GetEffectiveShape(layer).Collide(k.VECTOR2I(pt[0], pt[1]), required_gap):
                                            raise JobRefusal(
                                                f"local decoupling {relation['id']} geometry: "
                                                "escape track splits or has intermediate vertex within 1-floor junction opening"
                                            )
    for uid, item in tracks.items():
        via = item.GetClass() == 'PCB_VIA'
        clearance = _rules(board, item)['clearance']
        label = 'local decoupling ' + ', '.join(sorted(owners.get(uid, {'plane ties'}))) + ' geometry: '
        for terminal in pads:
            for layer in board.GetEnabledLayers().CuStack():
                if not item.IsOnLayer(layer) or not terminal.IsOnLayer(layer):
                    continue
                gap = max(clearance, _rules(board, terminal)['clearance']) if via else required_gap
                shape = terminal.GetEffectiveShape(layer)
                collision = item.GetEffectiveShape(layer).Collide(shape, gap)
                if via:
                    # An invalid drill wider than its annulus still cannot enter a pad.
                    collision |= shape.Collide(item.GetPosition(), int(item.GetDrillValue()) // 2 + gap)
                if collision:
                    if via or terminal.m_Uuid.AsString() not in allowed.get(uid, set()):
                        raise JobRefusal(label + uid + ' touches/violates pad clearance ' +
                                         terminal.GetParentFootprint().GetReference() + '.' + terminal.GetNumber())
        for other_uid, other in tracks.items():
            if (other_uid == uid or groups.get(uid, set()) & groups.get(other_uid, set())
                    or other_uid in ties.get(uid, set())):
                continue
            shared = allowed.get(uid, set()) & allowed.get(other_uid, set())
            shared_pads = [p for p in pads if p.m_Uuid.AsString() in shared]
            for layer in board.GetEnabledLayers().CuStack():
                if (item.IsOnLayer(layer) and other.IsOnLayer(layer)
                        and _too_close(item, other, layer, shared_pads, required_gap)):
                    kind = ('plane via touches another generated leg' if via or other.GetClass() == 'PCB_VIA'
                            else 'generated legs meet outside a declared shared pad')
                    raise JobRefusal(label + kind + ': ' + uid + ', ' + other_uid +
                                     '; native clearance floor ' + str(required_gap / 1e6) + ' mm')
    return dict(status='pass', generated_vias=sum(t.GetClass() == 'PCB_VIA' for t in tracks.values()),
                pad_clearance='effective net-class clearance on every pad, including same-net pads',
                tracks='declared endpoint pads only', separation_floor_mm=required_gap / 1e6,
                separation_source='native design rules: m_MinClearance')


def _gap_lower_bound(first, second, shapes):
    if shapes is None:
        return None
    a, b = (obj.GetBoundingBox() for obj in (first, second))
    high = math.ceil(math.hypot(max(a.GetRight(), b.GetRight()) - min(a.GetLeft(), b.GetLeft()),
                               max(a.GetBottom(), b.GetBottom()) - min(a.GetTop(), b.GetTop()))) + 4
    if not shapes[0].Collide(shapes[1], high):
        raise ValueError('native pair distance could not be bounded')
    low = 0
    while high - low > 1:
        middle = (low + high) // 2
        if shapes[0].Collide(shapes[1], middle):
            high = middle
        else:
            low = middle
    return low / 1e6


def separation_report(board_path, receipt):
    """Conservative minimum distance for every non-exempt generated pair/layer."""
    import pcbnew as k
    board = k.LoadBoard(str(board_path))
    pads, tracks, allowed, ties, groups, _ = _geometry_groups(board, receipt)
    floor = _separation_floor(board)
    rows, exempt, separate_layers = [], [], []
    ids = sorted(tracks)
    for index, uid in enumerate(ids):
        item = tracks[uid]
        for other_uid in ids[index + 1:]:
            other = tracks[other_uid]
            if groups.get(uid, set()) & groups.get(other_uid, set()) or other_uid in ties.get(uid, set()):
                exempt.append([uid, other_uid])
                continue
            layers = set(item.GetLayerSet().CuStack()) & set(other.GetLayerSet().CuStack())
            if not layers:
                separate_layers.append([uid, other_uid])
            shared = allowed.get(uid, set()) & allowed.get(other_uid, set())
            terminals = [p for p in pads if p.m_Uuid.AsString() in shared]
            for layer in sorted(layers):
                shapes, cross = _separation_shapes(item, other, layer, terminals)
                low = _gap_lower_bound(item, other, shapes)
                failed = _too_close(item, other, layer, terminals, floor)
                rows.append(dict(items=[uid, other_uid], layer=board.GetLayerName(layer), shared_pads=sorted(shared),
                                 minimum_gap_mm_lower_bound=low,
                                 status='defect' if failed else 'pass',
                                 scope='no copper beyond junction region' if shapes is None else 'outside junction region'))
    pad_rows = []
    for uid, item in tracks.items():
        via = item.GetClass() == 'PCB_VIA'
        for terminal in pads:
            if not via and terminal.m_Uuid.AsString() in allowed.get(uid, set()):
                continue
            gap = max(_rules(board, item)['clearance'], _rules(board, terminal)['clearance']) if via else floor
            for layer in board.GetEnabledLayers().CuStack():
                if item.IsOnLayer(layer) and terminal.IsOnLayer(layer):
                    shapes = (item.GetEffectiveShape(layer), terminal.GetEffectiveShape(layer))
                    pad_rows.append(dict(items=[uid, terminal.m_Uuid.AsString()],
                        pad=terminal.GetParentFootprint().GetReference()+'.'+terminal.GetNumber(),
                        layer=board.GetLayerName(layer), required_gap_mm=gap/1e6,
                        minimum_gap_mm_lower_bound=_gap_lower_bound(item, terminal, shapes),
                        status='defect' if shapes[0].Collide(shapes[1], max(1,gap)) else 'pass'))
    return dict(schema='agent-ee.local-separation/1', board_sha256=digest(board_path),
                floor_mm=floor / 1e6, source='native design rules: m_MinClearance',
                distance_resolution_nm=1, polygon_approximation_nm=1, pairs=rows,
                exempt_pairs=exempt, separate_layer_pairs=separate_layers, pad_pairs=pad_rows,
                outcome='defect' if any(r['status'] == 'defect' for r in rows + pad_rows) else 'pass')


def prepare_stub_trial(request):
    """Remove only unlocked warned tracks whose centreline is already on one generated track."""
    import json
    import shutil
    import pcbnew as k
    from .storage import write_json
    source, output = Path(request['board']), Path(request['output'])
    if digest(source) != request['board_sha256']:
        raise ValueError('stub trial source changed')
    board = k.LoadBoard(str(source))
    protected = {row['uuid']: row for row in request['protected']}
    items = {t.m_Uuid.AsString(): t for t in board.GetTracks()}
    receivers = [t for uid,t in items.items() if uid in protected and t.GetClass() == 'PCB_TRACK']
    removed = []
    for uid in request['warnings']:
        item = items.get(uid)
        if item is None or uid in protected or item.IsLocked() or item.GetClass() != 'PCB_TRACK':
            continue
        if any(t.GetNetCode() == item.GetNetCode() and t.GetLayer() == item.GetLayer()
               and item.GetWidth() <= t.GetWidth()
               and all(t.GetEffectiveShape(t.GetLayer()).Collide(p,0) for p in (item.GetStart(),item.GetEnd()))
               for t in receivers):
            board.Remove(item)
            removed.append(uid)
    if not removed:
        return dict(status='not_applicable', removed=[])
    board.BuildConnectivity()
    k.ZONE_FILLER(board).Fill(board.Zones())
    if not k.SaveBoard(str(output), board):
        raise ValueError('stub trial native save failed')
    for suffix in ('.kicad_pro','.kicad_dru'):
        if source.with_suffix(suffix).exists():
            shutil.copyfile(source.with_suffix(suffix), output.with_suffix(suffix))
    actual = {t.m_Uuid.AsString(): copper_identity(t) for t in board.GetTracks() if t.m_Uuid.AsString() in protected}
    if json.loads(json.dumps(actual)) != protected:
        raise ValueError('stub trial changed generated copper')
    write_json(output.with_suffix('.removed.json'), removed)
    return dict(status='prepared', removed=removed, board_sha256=digest(output), source_sha256=request['board_sha256'])


if __name__ == '__main__':
    import argparse
    from .storage import read_json, write_json
    parser = argparse.ArgumentParser()
    parser.add_argument('request')
    args = parser.parse_args()
    request = read_json(args.request)
    write_json(request['result'], prepare_stub_trial(request))
