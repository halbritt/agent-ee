"""Offline placement judgment over saved-board facts; no heuristic proves a defect.

Coordinates are integer nanometres. Exact rational clipping avoids epsilon-based
passes at containment and overlap boundaries. Only simple, straight, closed
polygons qualify; unsupported geometry remains explicitly unknown. A curved
courtyard is judged through the probe's exact outer and inner straight bounds
(AGENTEE-212): pass on the outer bound, defect only on the inner one.
"""
from fractions import Fraction
import math

SCHEMA = 'agent-ee.placement-probe/1'


def cross(a, b, c):
    return (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])


def area2(points):
    return sum(a[0]*b[1]-a[1]*b[0] for a, b in edges(points))


def edges(points):
    return zip(points, points[1:] + points[:1])


def on_segment(a, b, p):
    return cross(a, b, p) == 0 and all(min(a[i], b[i]) <= p[i] <= max(a[i], b[i]) for i in (0, 1))


def intersects(a, b, c, d):
    turns = cross(a, b, c), cross(a, b, d), cross(c, d, a), cross(c, d, b)
    return (turns[0]*turns[1] < 0 and turns[2]*turns[3] < 0) or any((
        on_segment(a, b, c), on_segment(a, b, d), on_segment(c, d, a), on_segment(c, d, b)))


def simple_polygon(raw):
    if not isinstance(raw, list) or len(raw) < 3 or len(raw) > 512:
        raise ValueError('polygon needs 3..512 vertices')
    if any(not isinstance(p, (list, tuple)) or len(p) != 2 or
           any(type(v) is not int for v in p) for p in raw):
        raise ValueError('polygon coordinates must be integer nanometres')
    points = [tuple(p) for p in raw]
    if points[0] == points[-1]:
        points.pop()
    if len(set(points)) != len(points):
        raise ValueError('repeated polygon vertex')
    # Collinear continuation is harmless; collinear reversal is not a boundary.
    changed = True
    while changed and len(points) >= 3:
        changed = False
        for i, p in enumerate(points):
            a, b = points[i-1], points[(i+1) % len(points)]
            if cross(a, p, b) == 0:
                if not on_segment(a, b, p):
                    raise ValueError('polygon edge reverses')
                points.pop(i)
                changed = True
                break
    if len(points) < 3 or area2(points) == 0:
        raise ValueError('zero polygon area')
    segments = list(edges(points))
    # Two segments cross or touch only where their closed bounding boxes
    # overlap, so a sweep over boxes sorted by least x runs the exact test on
    # exactly the pairs that can intersect (AGENTEE-332 qualification cost).
    last = len(segments) - 1
    spans = sorted((min(a[0], b[0]), max(a[0], b[0]), min(a[1], b[1]), max(a[1], b[1]), i)
                   for i, (a, b) in enumerate(segments))
    for k, (x0, x1, y0, y1, i) in enumerate(spans):
        for u0, u1, v0, v1, j in spans[k+1:]:
            if u0 > x1:
                break
            if abs(i-j) == 1 or {i, j} == {0, last} or v0 > y1 or v1 < y0:
                continue
            if intersects(*segments[i], *segments[j]):
                raise ValueError('polygon self-intersection')
    return points if area2(points) > 0 else list(reversed(points))


def convex(points):
    """True for a counter-clockwise polygon that turns left at every vertex.

    Only meaningful for simple polygons: a self-winding star also turns left."""
    return len(points) >= 3 and all(cross(points[i-2], points[i-1], p) > 0 for i, p in enumerate(points))


def triangles(points):
    if convex(points):
        # A fan of a convex polygon; exact and linear, as for curve bounds.
        yield from ([points[0], points[i], points[i+1]] for i in range(1, len(points)-1))
        return
    remaining = list(points)
    while len(remaining) > 3:
        for i, b in enumerate(remaining):
            a, c = remaining[i-1], remaining[(i+1) % len(remaining)]
            if cross(a, b, c) <= 0:
                continue
            if any(all(cross(u, v, p) >= 0 for u, v in edges([a, b, c]))
                   for p in remaining if p not in (a, b, c)):
                continue
            yield [a, b, c]
            remaining.pop(i)
            break
        else:
            raise ValueError('polygon cannot be triangulated')
    yield remaining


def clipped_area(subject, clip):
    output = subject
    for a, b in edges(clip):
        incoming, output = output, []
        if not incoming:
            return 0
        for p, q in edges(incoming):
            cp, cq = cross(a, b, p), cross(a, b, q)
            if cp >= 0:
                output.append(p)
            if (cp < 0) != (cq < 0):
                t = Fraction(cp, cp-cq)
                output.append((p[0]+t*(q[0]-p[0]), p[1]+t*(q[1]-p[1])))
    return Fraction(abs(area2(output)), 2) if output else 0


def intersection_area(left, right):
    if convex(left) and convex(right):
        return clipped_area(list(left), right)
    return sum(clipped_area(a, b) for a in triangles(left) for b in triangles(right))


def qualified_polygons(record):
    if not isinstance(record, dict) or record.get('status') != 'proven':
        raise ValueError(record.get('status', 'missing') if isinstance(record, dict) else 'missing')
    raw = record.get('polygons')
    if not isinstance(raw, list) or not raw or len(raw) > 64:
        raise ValueError('missing or excessive polygons')
    polygons = [simple_polygon(p) for p in raw]
    for i, left in enumerate(polygons):
        for right in polygons[i+1:]:
            if intersection_area(left, right) or any(intersects(a, b, c, d)
                    for a, b in edges(left) for c, d in edges(right)):
                raise ValueError('nested or touching polygon contours unsupported')
    return polygons


# Largest curve approximation tolerance a courtyard bound may declare.
MAX_CURVE_TOLERANCE_NM = 50000
AXES = {'x+': (1, 0), 'x-': (-1, 0), 'y+': (0, 1), 'y-': (0, -1)}
SHA256_HEX = set('0123456789abcdef')


def courtyard_bounds(record):
    """(outer, inner) qualified polygons of one courtyard record.

    A straight courtyard is its own bound, returned as the same list. A curved one
    (AGENTEE-212) carries ``inner_polygons`` and a declared tolerance: the outer
    bound contains the true region and the inner bound lies within it, so a pass
    is decided on the outer bound and a defect is proved only on the inner one."""
    outer = qualified_polygons(record)
    if 'inner_polygons' not in record:
        return outer, outer
    approximation = record.get('approximation')
    tolerance = approximation.get('tolerance_nm') if isinstance(approximation, dict) else None
    if type(tolerance) is not int or not 0 < tolerance <= MAX_CURVE_TOLERANCE_NM:
        raise ValueError('curve bound without a declared approximation tolerance')
    inner = qualified_polygons({'status': 'proven', 'polygons': record['inner_polygons']})
    if len(inner) != len(outer):
        raise ValueError('inner and outer curve bounds differ in contours')
    return outer, inner


def box(polygons):
    return (min(p[0] for poly in polygons for p in poly), min(p[1] for poly in polygons for p in poly),
            max(p[0] for poly in polygons for p in poly), max(p[1] for poly in polygons for p in poly))


def boxes_meet(left, right):
    """False when two polygon sets have disjoint or merely touching bounding boxes (zero overlap)."""
    a, b = box(left), box(right)
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _overhang_source(source):
    if (not isinstance(source, dict) or set(source) != {'envelope_id', 'envelope_sha256', 'pointer'}
            or any(not isinstance(v, str) or not v for v in source.values())
            or len(source['envelope_sha256']) != 64 or not set(source['envelope_sha256']) <= SHA256_HEX):
        raise ValueError('courtyard overhang must cite its envelope id, sha256 and pointer')


def _overhang_edge_fields(ref, edge):
    """One declared edge's closed fields and types, without the outline it must lie on."""
    if (not isinstance(edge, dict) or set(edge) != {'name', 'start_nm', 'end_nm', 'outward', 'max_nm'}
            or not isinstance(edge['outward'], str) or edge['outward'] not in AXES
            or not isinstance(edge['name'], str) or not edge['name']):
        raise ValueError(f'{ref}: overhang edge needs name, start_nm, end_nm, outward axis and max_nm')
    ends = []
    for point in (edge['start_nm'], edge['end_nm']):
        if not isinstance(point, list) or len(point) != 2 or any(type(v) is not int for v in point):
            raise ValueError(f'{ref}: overhang edge points must be integer nanometres')
        ends.append(tuple(point))
    distance = edge['max_nm']
    if type(distance) is not int or distance <= 0:
        raise ValueError(f'{ref}: overhang max_nm must be a positive integer')
    return ends, distance


def _overhang_edge(ref, edge, sides, outline):
    ends, distance = _overhang_edge_fields(ref, edge)
    (sx, sy), (ex, ey) = ends
    ux, uy = AXES[edge['outward']]
    if frozenset(ends) not in sides or (ex-sx)*ux + (ey-sy)*uy != 0:
        raise ValueError(f"{ref}: overhang edge {edge['name']} is not a proven outline edge "
                         'perpendicular to its outward axis')
    return ends, (ux*distance, uy*distance)


def overhang_bands(declarations, outline):
    """Declared fixed-courtyard overhangs bound to an envelope: {reference: [region, ...]}.

    Each declaration names a reference and one or more straight edges of the
    proven outline, each with its outward axis and maximum distance in
    nanometres. A band is an edge swept outward by its distance; where two
    declared edges meet at a vertex, the rectangle beyond that corner spanned by
    both distances is admitted too. The regions share no area with the board or
    each other, so a courtyard may leave the outline only across declared edges
    and only that far. Malformed declarations raise ValueError; nothing is
    exempted by default."""
    if not isinstance(declarations, list):
        raise ValueError('courtyard overhangs must be a list')
    bands = {}
    for declaration in declarations:
        ref = overhang_declaration(declaration)
        if ref in bands:
            raise ValueError(f'{ref}: more than one courtyard overhang declared')
        bands[ref] = overhang_regions(ref, declaration['edges'], outline)
    return bands


def overhang_declaration(declaration):
    """One overhang declaration's closed schema, independent of any outline; its reference.

    Exactly reference, source and a nonempty list of distinct, fully typed edges.
    Shared by ``overhang_bands`` and by evidence that copies a declaration."""
    if (not isinstance(declaration, dict) or set(declaration) != {'reference', 'edges', 'source'}
            or not isinstance(declaration['reference'], str) or not declaration['reference']):
        raise ValueError('courtyard overhang needs exactly reference, edges and source')
    ref, declared = declaration['reference'], declaration['edges']
    _overhang_source(declaration['source'])
    if (not isinstance(declared, list) or not declared or
            len({e.get('name') if isinstance(e, dict) and isinstance(e.get('name'), str) else None
                 for e in declared}) != len(declared)):
        raise ValueError(f'{ref}: overhang edges must be a nonempty list of distinct edges')
    for edge in declared:
        _overhang_edge_fields(ref, edge)
    return ref


def overhang_regions(ref, declared, outline):
    """Shared exact edge-sweep geometry; callers qualify their declaration source."""
    vertices = [tuple(p) for p in outline]
    sides = {frozenset((vertices[i-1], p)) for i, p in enumerate(vertices)}
    swept = [_overhang_edge(ref, edge, sides, outline) for edge in declared]
    regions = [[a, b, (b[0]+dx, b[1]+dy), (a[0]+dx, a[1]+dy)] for (a, b), (dx, dy) in swept]
    for i, ((a, b), (dx, dy)) in enumerate(swept):
        for (c, d), (fx, fy) in swept[i+1:]:
            for v in {a, b} & {c, d}:
                regions.append([v, (v[0]+dx, v[1]+dy), (v[0]+dx+fx, v[1]+dy+fy), (v[0]+fx, v[1]+fy)])
    regions = [simple_polygon(r) for r in regions]
    if any(intersection_area(r, outline) for r in regions) or any(
            intersection_area(r, q) for i, r in enumerate(regions) for q in regions[i+1:]):
        raise ValueError(f'{ref}: declared overhang regions point into the board or overlap')
    return regions


def _geometry(capture, overhangs=None, access_lanes=None):
    unknown, failures, courtyard_records, declared = [], [], [], []
    try:
        outline = qualified_polygons(capture.get('outline'))
        if len(outline) != 1:
            raise ValueError('multiple outline contours unsupported')
    except ValueError as exc:
        outline = []
        unknown.append({'kind': 'outline', 'reason': str(exc)})
    bands = dict(access_lanes or {})
    # Explicit envelope maxima retain precedence over the access allowance.
    bands.update(overhang_bands(overhangs, outline[0]) if outline and overhangs is not None else {})
    for fp in sorted(capture['footprints'], key=lambda f: f['ref']):
        courtyards = fp.get('courtyards', {})
        if fp.get('side') not in courtyards:
            unknown.append({'kind': 'courtyard', 'ref': fp['ref'], 'reason': 'missing'})
        for side, record in sorted(courtyards.items()):
            try:
                if side not in ('F', 'B'):
                    raise ValueError('unsupported courtyard side')
                polygons, inner = courtyard_bounds(record)
            except ValueError as exc:
                unknown.append({'kind': 'courtyard', 'ref': fp['ref'], 'side': side, 'reason': str(exc)})
                continue
            courtyard_records.append((fp['ref'], side, polygons, inner))
            if outline:
                band = bands.get(fp['ref'])
                def beyond(contours):
                    return sum(Fraction(abs(area2(p)), 2) - intersection_area(p, outline[0]) -
                               sum(intersection_area(p, r) for r in band or ()) for p in contours)
                outside = beyond(inner)
                if outside > 0:
                    failures.append({'kind': 'outside_outline', 'ref': fp['ref'], 'side': side,
                                     'outside_area_mm2': float(outside / 10**12)})
                elif inner is not polygons and beyond(polygons) > 0:
                    unknown.append({'kind': 'courtyard_approximation', 'ref': fp['ref'], 'side': side,
                                    'reason': 'outside area lies only between the declared curve bounds'})
                overhang = sum(intersection_area(p, r) for p in polygons for r in band or ())
                if overhang > 0:
                    declared.append({'ref': fp['ref'], 'side': side, 'overhang_area_mm2': float(overhang / 10**12)})
    for i, (ref, side, left, left_inner) in enumerate(courtyard_records):
        for other, other_side, right, right_inner in courtyard_records[i+1:]:
            if side != other_side or ref == other or not boxes_meet(left, right):
                continue
            overlap = sum(intersection_area(a, b) for a in left_inner for b in right_inner)
            if overlap > 0:
                failures.append({'kind': 'courtyard_overlap', 'refs': [ref, other], 'side': side,
                                 'overlap_area_mm2': float(overlap / 10**12)})
            elif (left_inner is not left or right_inner is not right) and any(
                    intersection_area(a, b) for a in left for b in right):
                unknown.append({'kind': 'courtyard_approximation', 'refs': [ref, other], 'side': side,
                                'reason': 'overlap lies only between the declared curve bounds'})
    return failures, unknown, declared


def validate_capture(capture):
    if not isinstance(capture, dict) or capture.get('schema') != SCHEMA:
        raise ValueError('unsupported placement capture schema')
    sha = capture.get('board_sha256', '')
    if not isinstance(sha, str) or len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha):
        raise ValueError('missing captured board hash')
    footprints = capture.get('footprints')
    if not isinstance(footprints, list) or not footprints:
        raise ValueError('empty footprint inventory')
    refs, uuids = set(), set()
    for fp in footprints:
        if not isinstance(fp, dict):
            raise ValueError('footprint record must be an object')
        for key, seen in (('ref', refs), ('uuid', uuids)):
            identity = fp.get(key)
            if not isinstance(identity, str) or not identity or identity in seen:
                raise ValueError('missing or ambiguous footprint identity')
            seen.add(identity)


def _policy(policy, footprints):
    if not isinstance(policy, dict) or policy.get('schema') != 'agent-ee.placement-policy/1':
        raise ValueError('unsupported placement policy')
    refs = set(footprints)
    for name in ('ic_refs', 'connector_refs', 'ground_nets'):
        seq = policy.get(name)
        if not isinstance(seq, list) or len(seq) != len(set(seq)) or any(not isinstance(s, str) or not s for s in seq):
            raise ValueError('policy needs explicit unique ' + name)
        if name != 'ground_nets' and not set(seq) <= refs:
            raise ValueError('unknown policy reference')
    caps = policy.get('capacitors')
    if not isinstance(caps, dict) or not set(caps) <= refs or set(caps) & (set(policy['ic_refs']) | set(policy['connector_refs'])):
        raise ValueError('invalid capacitor inventory')
    if set(policy['ic_refs']) & set(policy['connector_refs']):
        raise ValueError('ambiguous component role')
    if type(policy.get('max_shared_refs')) is not int or not 1 <= policy['max_shared_refs'] <= 8:
        raise ValueError('max_shared_refs must be 1..8')
    for cap in caps.values():
        if not isinstance(cap, dict):
            raise ValueError('capacitor declaration must be an object')
        for key in ('capacitance_nf', 'max_distance_mm', 'deduction'):
            v = cap.get(key)
            if type(v) not in (int, float) or not math.isfinite(v) or v < 0 or (key != 'deduction' and v == 0):
                raise ValueError('invalid declared capacitor ' + key)
    keep_apart_rules(policy)
    return caps


def keep_apart_rules(policy):
    """Validate declared separation inputs without guessing component roles or distances."""
    from .placement_predicates import exact_nm
    if not isinstance(policy, dict) or policy.get('schema') != 'agent-ee.placement-policy/1':
        raise ValueError('unsupported placement policy')
    rules = policy.get('keep_apart', [])
    if not isinstance(rules, list):
        raise ValueError('keep_apart must be a list')
    pairs = set()
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) != {'ref', 'others', 'min_distance_mm'}:
            raise ValueError('keep_apart needs ref, others and min_distance_mm')
        ref, others = rule['ref'], rule['others']
        if not isinstance(ref, str) or not ref.strip():
            raise ValueError('keep_apart ref must be nonempty text')
        if (not isinstance(others, list) or not others or
                any(not isinstance(r, str) or not r.strip() or r == ref for r in others) or
                len(set(others)) != len(others)):
            raise ValueError('keep_apart others must be distinct nonempty references, excluding ref')
        exact_nm(rule['min_distance_mm'], 'min_distance_mm')
        for other in others:
            pair = tuple(sorted((ref, other)))
            if pair in pairs:
                raise ValueError('duplicate keep_apart pair')
            pairs.add(pair)
    return rules


def _advisories(capture, policy):
    if policy is None:
        return [], [], None
    footprints = {f['ref']: f for f in capture['footprints']}
    caps = _policy(policy, footprints)
    net_refs = {}
    for ref, fp in footprints.items():
        for pad in fp['pads']:
            net = pad['net']
            if net and net not in policy['ground_nets']:
                net_refs.setdefault(net, set()).add(ref)
    associations, advisories, score = [], [], 100
    for ref, declaration in sorted(caps.items()):
        nets = sorted(n for n, rs in net_refs.items() if ref in rs and len(rs) <= policy['max_shared_refs'])
        connectors = sorted({r for n in nets for r in net_refs[n] if r in policy['connector_refs']})
        ics = sorted({r for n in nets for r in net_refs[n] if r in policy['ic_refs']})
        pos = footprints[ref]['position_nm']
        # The waiver holds only within the capacitor's own declared limit, so
        # a rail shared with a distant connector still gets its IC check.
        nearest = min(((math.dist(pos, footprints[r]['position_nm'])/10**6, r) for r in connectors), default=None)
        if nearest is not None and nearest[0] <= declaration['max_distance_mm']:
            associations.append({'ref': ref, 'kind': 'interface_filter', 'connectors': connectors,
                                 'nearest_connector': nearest[1], 'distance_mm': nearest[0], 'nets': nets})
        elif ics:
            distance, ic = min((math.dist(pos, footprints[r]['position_nm'])/10**6, r) for r in ics)
            associations.append({'ref': ref, 'kind': 'decoupling', 'ic': ic, 'nets': nets,
                                 'distance_mm': distance, **declaration})
            if distance > declaration['max_distance_mm']:
                advisories.append({'kind': 'decoupling_distance', 'ref': ref, 'ic': ic,
                                   'distance_mm': distance, **declaration})
                score -= declaration['deduction']
        else:
            associations.append({'ref': ref, 'kind': 'unassociated', 'nets': nets})
    edge_policy = policy.get('connector_edge')
    if edge_policy is not None:
        for key in ('max_distance_mm', 'deduction'):
            v = edge_policy.get(key)
            if type(v) not in (int, float) or not math.isfinite(v) or v < 0:
                raise ValueError('invalid connector edge policy')
        try:
            outline = qualified_polygons(capture.get('outline'))
        except ValueError:
            outline = []
        if len(outline) == 1:
            for ref in sorted(policy['connector_refs']):
                point = footprints[ref]['position_nm']
                distance = min(_point_segment_distance(point, a, b) for a, b in edges(outline[0])) / 10**6
                if distance > edge_policy['max_distance_mm']:
                    advisories.append({'kind': 'connector_edge_distance', 'ref': ref,
                                       'distance_mm': distance, **edge_policy})
                    score -= edge_policy['deduction']
    return associations, advisories, max(0, score)


def _point_segment_distance(p, a, b):
    dx, dy = b[0]-a[0], b[1]-a[1]
    t = max(0, min(1, ((p[0]-a[0])*dx + (p[1]-a[1])*dy)/(dx*dx + dy*dy)))
    return math.dist(p, (a[0]+t*dx, a[1]+t*dy))


def judge(capture, policy=None, overhangs=None):
    """Return geometric facts and advisory score; malformed envelopes raise ValueError.

    ``policy`` is a captured declaration, never inferred from reference prefixes
    or capacitor value text. Distances are footprint-centre Euclidean distances.
    ``overhangs`` are envelope-bound fixed-courtyard overhang declarations
    (``overhang_bands``); area inside a declared band is reported, not failed.
    This standalone record is not a sealed coordinator check finding.
    """
    validate_capture(capture)
    from .access_direction import grade as access_grade
    access, access_unknown = access_grade(capture)
    access_lanes = {row['ref']: row['lane_polygons_nm'] for row in access
                    if 'overhang' in row and row['provenance']['enforcement'] == 'hard' and row['relaxes_outline']}
    failures, unknown, declared = _geometry(capture, overhangs, access_lanes)
    failures += [row for row in access if row['status'] == 'defect']
    unknown += access_unknown
    associations, advisories, score = _advisories(capture, policy)
    advisories += [row for row in access if row['status'] == 'info']
    verdict = 'hard_fail' if failures else 'unknown' if unknown else 'pass'
    off_board = sorted({f['ref'] for f in failures if f['kind'] == 'outside_outline'})
    return {'schema': 'agent-ee.placement-judgment/1', 'board_sha256': capture['board_sha256'],
            'verdict': verdict, 'outcome': {'hard_fail': 'defect', 'unknown': 'unknown', 'pass': 'pass'}[verdict],
            'score': score, 'hard_failures': failures, 'off_board': off_board, 'unknown': unknown,
            'associations': associations, 'advisories': advisories, 'policy': policy,
            'authority': 'standalone-unsealed',
            **({'access_directions': access} if access or access_unknown else {}),
            **({'declared_overhangs': declared} if access_lanes and overhangs is None else {}),
            **({} if overhangs is None else {'declared_overhangs': declared, 'overhangs': overhangs})}
