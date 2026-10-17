"""Offline placement judgment over saved-board facts; no heuristic proves a defect.

Coordinates are integer nanometres. Exact rational clipping avoids epsilon-based
passes at containment and overlap boundaries. Only simple, straight, closed
polygons qualify; unsupported geometry remains explicitly unknown.
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
    for i, (a, b) in enumerate(segments):
        for j in range(i+1, len(segments)):
            if j == i+1 or (i == 0 and j == len(segments)-1):
                continue
            if intersects(a, b, *segments[j]):
                raise ValueError('polygon self-intersection')
    return points if area2(points) > 0 else list(reversed(points))


def triangles(points):
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


def _geometry(capture):
    unknown, failures, courtyard_records = [], [], []
    try:
        outline = qualified_polygons(capture.get('outline'))
        if len(outline) != 1:
            raise ValueError('multiple outline contours unsupported')
    except ValueError as exc:
        outline = []
        unknown.append({'kind': 'outline', 'reason': str(exc)})
    for fp in sorted(capture['footprints'], key=lambda f: f['ref']):
        courtyards = fp.get('courtyards', {})
        if fp.get('side') not in courtyards:
            unknown.append({'kind': 'courtyard', 'ref': fp['ref'], 'reason': 'missing'})
        for side, record in sorted(courtyards.items()):
            try:
                if side not in ('F', 'B'):
                    raise ValueError('unsupported courtyard side')
                polygons = qualified_polygons(record)
            except ValueError as exc:
                unknown.append({'kind': 'courtyard', 'ref': fp['ref'], 'side': side, 'reason': str(exc)})
                continue
            courtyard_records.append((fp['ref'], side, polygons))
            if outline:
                outside = sum(Fraction(abs(area2(p)), 2)-intersection_area(p, outline[0]) for p in polygons)
                if outside > 0:
                    failures.append({'kind': 'outside_outline', 'ref': fp['ref'], 'side': side,
                                     'outside_area_mm2': float(outside / 10**12)})
    for i, (ref, side, left) in enumerate(courtyard_records):
        for other, other_side, right in courtyard_records[i+1:]:
            if side != other_side or ref == other:
                continue
            overlap = sum(intersection_area(a, b) for a in left for b in right)
            if overlap > 0:
                failures.append({'kind': 'courtyard_overlap', 'refs': [ref, other], 'side': side,
                                 'overlap_area_mm2': float(overlap / 10**12)})
    return failures, unknown


def validate_capture(capture):
    if capture.get('schema') != SCHEMA:
        raise ValueError('unsupported placement capture schema')
    sha = capture.get('board_sha256', '')
    if len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha):
        raise ValueError('missing captured board hash')
    footprints = capture.get('footprints')
    if not isinstance(footprints, list) or not footprints:
        raise ValueError('empty footprint inventory')
    refs, uuids = set(), set()
    for fp in footprints:
        for key, seen in (('ref', refs), ('uuid', uuids)):
            identity = fp.get(key)
            if not isinstance(identity, str) or not identity or identity in seen:
                raise ValueError('missing or ambiguous footprint identity')
            seen.add(identity)


def _policy(policy, footprints):
    if policy.get('schema') != 'agent-ee.placement-policy/1':
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
        for key in ('capacitance_nf', 'max_distance_mm', 'deduction'):
            v = cap.get(key)
            if type(v) not in (int, float) or not math.isfinite(v) or v < 0 or (key != 'deduction' and v == 0):
                raise ValueError('invalid declared capacitor ' + key)
    return caps


def _advisories(capture, policy):
    if policy is None:
        return [], [], 100
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
        if connectors:
            distance, connector = min((math.dist(footprints[ref]['position_nm'],
                footprints[r]['position_nm'])/10**6, r) for r in connectors)
            associations.append({'ref': ref, 'kind': 'interface_filter', 'connectors': connectors,
                                 'nearest_connector': connector, 'distance_mm': distance, 'nets': nets})
        elif ics:
            pos = footprints[ref]['position_nm']
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


def judge(capture, policy=None):
    """Return geometric facts and advisory score; malformed envelopes raise ValueError.

    ``policy`` is a captured declaration, never inferred from reference prefixes
    or capacitor value text. Distances are footprint-centre Euclidean distances.
    This standalone record is not a sealed coordinator check finding.
    """
    validate_capture(capture)
    failures, unknown = _geometry(capture)
    associations, advisories, score = _advisories(capture, policy)
    verdict = 'hard_fail' if failures else 'unknown' if unknown else 'pass'
    return {'schema': 'agent-ee.placement-judgment/1', 'board_sha256': capture['board_sha256'],
            'verdict': verdict, 'outcome': {'hard_fail': 'defect', 'unknown': 'unknown', 'pass': 'pass'}[verdict],
            'score': score, 'hard_failures': failures, 'unknown': unknown,
            'associations': associations, 'advisories': advisories, 'policy': policy,
            'authority': 'standalone-unsealed'}
