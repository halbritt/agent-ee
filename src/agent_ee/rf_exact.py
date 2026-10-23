"""Conservative exact-CAD CPW certificates; no fabrication or impedance allowance.

Integer/rational comparisons certify only ordinary axis-aligned straight intervals.
Native connectivity is evidence of ground membership, not a return-current model.
"""
from fractions import Fraction
import hashlib
import json
import math


def geometry_digest(facts):
    """Order/UUID-independent digest of copper geometry, nets, layers and filled polygons."""
    def clean(value):
        if isinstance(value, dict):
            return {k: clean(v) for k, v in sorted(value.items()) if k not in ('uuid', 'components')}
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value
    records = [clean(p) for p in facts['primitives']]
    records.sort(key=lambda p: json.dumps(p, sort_keys=True))
    return hashlib.sha256(json.dumps(records, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def extract_facts(k, board, request, ids):
    from .rf_geometry import _polyset
    signal, reference = request['signal_layer'], request['reference_layer']
    ground = request['ground_net']
    if signal not in ids or reference not in ids:
        return {'primitives': [], 'components': [], 'reference_zones': [], 'geometry_sha256': None}
    board.BuildConnectivity()
    conn = board.GetConnectivity()
    pads = [p for f in board.GetFootprints() for p in f.Pads()]
    components, covered = [], set()
    for pad in pads:
        uid = pad.m_Uuid.AsString()
        if str(pad.GetNetname()) != ground or uid in covered:
            continue
        members = {uid, *(i.m_Uuid.AsString() for i in conn.GetConnectedItems(pad))}
        covered.update(members)
        components.append({'anchor': uid, 'members': sorted(members)})
    primitives, reference_zones = [], []
    def point(p):
        return [int(p.x), int(p.y)]
    def record(item, kind):
        box = item.GetBoundingBox()
        return {'uuid': item.m_Uuid.AsString(), 'kind': kind,
                'net': str(item.GetNetname()) if isinstance(item, k.BOARD_CONNECTED_ITEM) else '',
                'bbox_nm': [int(box.GetX()), int(box.GetY()), int(box.GetRight()), int(box.GetBottom())],
                'layers': [name for name in (signal, reference) if item.IsOnLayer(ids[name])]}
    for item in board.GetTracks():
        cls = item.GetClass()
        if not any(item.IsOnLayer(ids[n]) for n in (signal, reference)):
            continue
        r = record(item, {'PCB_TRACK': 'segment', 'PCB_ARC': 'arc', 'PCB_VIA': 'via'}[cls])
        r['width_nm'] = int(item.GetWidth(ids[signal] if item.IsOnLayer(ids[signal]) else ids[reference])
                            if cls == 'PCB_VIA' else item.GetWidth())
        if cls == 'PCB_VIA':
            r['position_nm'] = point(item.GetPosition())
            r['drill_nm'] = int(item.GetDrillValue())
        else:
            r.update(start_nm=point(item.GetStart()), end_nm=point(item.GetEnd()))
            if cls == 'PCB_ARC':
                r['mid_nm'] = point(item.GetMid())
        primitives.append(r)
    for item in [*pads, *board.GetDrawings(), *(g for f in board.GetFootprints() for g in f.GraphicalItems())]:
        if any(item.IsOnLayer(ids[n]) for n in (signal, reference)):
            primitives.append(record(item, 'pad' if isinstance(item, k.PAD) else 'graphic'))
    for zone in [*board.Zones(), *(z for f in board.GetFootprints() for z in f.Zones())]:
        if zone.GetIsRuleArea():
            continue
        for name in (signal, reference):
            if not zone.IsOnLayer(ids[name]):
                continue
            r = record(zone, 'zone')
            r['layers'] = [name]
            r['filled'] = bool(zone.IsFilled() and zone.HasFilledPolysForLayer(ids[name]))
            r['polygons_nm'] = _polyset(zone.GetFilledPolysList(ids[name])) if r['filled'] else []
            primitives.append(r)
            if name == reference and r['net'] == ground:
                reference_zones.append(r['uuid'])
    result = {'primitives': primitives, 'components': components, 'reference_zones': sorted(reference_zones)}
    result['geometry_sha256'] = geometry_digest(result)
    return result


def reference_binding(facts, resolved):
    unknown = {'status': 'unknown', 'reason': 'reference_unanchored'}
    if not facts or not facts.get('reference_zones'):
        return unknown
    reference = set(facts['reference_zones'])
    tracks = [p for p in facts['primitives'] if p['net'] == resolved['net'] and
              p['kind'] in ('segment', 'arc') and resolved['signal_layer'] in p['layers']]
    radius = (Fraction(str(resolved['cpw']['width_mm'])) * 500000 +
              Fraction(str(resolved['cpw']['gap_mm'])) * 1000000)
    def supplies_strip(zone):
        for track in tracks:
            if track['kind'] == 'segment' and zone['filled']:
                dist = polygon_distance2(track['start_nm'], track['end_nm'], zone['polygons_nm'])
                if dist is not None and dist <= radius*radius:
                    return True
            else:
                # Arc or unfilled-zone supplier identity is not exact; over-include
                # intersecting bounding boxes so uncertainty cannot become a PASS.
                a,b = track['bbox_nm'],zone['bbox_nm']
                if all(a[i]-radius <= b[i+2] and b[i] <= a[i+2]+radius for i in (0,1)):
                    return True
        return False
    zones = [p for p in facts['primitives'] if p['uuid'] in reference and
             resolved['reference_layer'] in p['layers'] and supplies_strip(p)]
    reference = {p['uuid'] for p in zones}
    if not reference:
        return unknown
    # A zone UUID can cover disconnected islands. Native item membership alone cannot
    # establish which island is anchored: keep that case unknown.
    if any(not z['filled'] or len(z['polygons_nm']) != 1 for z in zones):
        return {**unknown, 'detail': 'reference zone has absent fill or multiple islands'}
    spanning = {p['uuid'] for p in facts['primitives'] if p['kind'] == 'via' and
                p['net'] == resolved['ground_net'] and
                {resolved['signal_layer'], resolved['reference_layer']} <= set(p['layers'])}
    valid = [c for c in facts['components'] if reference <= set(c['members']) and spanning & set(c['members'])]
    if not valid:
        return unknown
    return {'status': 'pass', 'anchors': [c['anchor'] for c in valid],
            'members': sorted(set().union(*(set(c['members']) for c in valid)))}


def cross(a, b, c):
    return (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])


def point_distance2(p, a, b):
    dx, dy = b[0]-a[0], b[1]-a[1]
    length2 = dx*dx + dy*dy
    dot = (p[0]-a[0])*dx + (p[1]-a[1])*dy
    if not length2 or dot <= 0:
        return (p[0]-a[0])**2 + (p[1]-a[1])**2
    if dot >= length2:
        return (p[0]-b[0])**2 + (p[1]-b[1])**2
    return Fraction(cross(a, b, p)**2, length2)


def distance2(a, b, c, d):
    # Include the collinear disjoint case: signs alone do not prove intersection.
    overlap = all(max(min(a[i], b[i]), min(c[i], d[i])) <=
                  min(max(a[i], b[i]), max(c[i], d[i])) for i in (0, 1))
    if overlap and cross(a,b,c)*cross(a,b,d) <= 0 and cross(c,d,a)*cross(c,d,b) <= 0:
        return 0
    return min(point_distance2(a,c,d), point_distance2(b,c,d),
               point_distance2(c,a,b), point_distance2(d,a,b))


def inside_ring(p, ring):
    inside = False
    for a,b in zip(ring, ring[1:]+ring[:1]):
        if (a[1] > p[1]) != (b[1] > p[1]):
            x = a[0] + Fraction((p[1]-a[1])*(b[0]-a[0]), b[1]-a[1])
            if p[0] < x:
                inside = not inside
    return inside


def polygon_distance2(a, b, polygons):
    values = []
    for poly in polygons:
        if any(inside_ring(p, poly['outline']) and not any(inside_ring(p,h) for h in poly['holes'])
               for p in (a,b)):
            return 0
        for ring in [poly['outline'], *poly['holes']]:
            values.extend(distance2(a,b,c,d) for c,d in zip(ring,ring[1:]+ring[:1]))
    return min(values) if values else None


def bbox_polygon(box):
    x,y,r,t = box
    return [{'outline': [[x,y],[r,y],[r,t],[x,t]], 'holes': []}]


def evaluate_exact_gap(resolved, native):
    """Whole-interval squared-distance checks, never a rounded distance acceptance.

    RF pad/via/junction bounding boxes conservatively delimit ungraded transitions.
    Explicit ground track flanks must span an interval and belong to the anchored
    reference component. Every other copper object is checked for intrusion. Unknown
    shapes use outer bounding boxes: intersection means UNKNOWN, never an allowance.
    """
    facts = native.get('exact_cpw')
    if not facts:
        return {'status': 'unknown', 'reason': 'exact primitive evidence absent'}
    if facts.get('geometry_sha256') != geometry_digest(facts):
        raise ValueError('exact CPW geometry digest mismatch')
    gap = resolved['cpw']['gap_nm']
    signal, rf = resolved['signal_layer'], resolved['net']
    primitives = [p for p in facts['primitives'] if signal in p['layers']]
    ground = [p for p in primitives if p['kind'] == 'segment' and p['net'] == resolved['ground_net']]
    binding = reference_binding(facts, resolved)
    anchored = set(binding.get('members', []))
    intervals = []
    for track in native['tracks']:
        if track['layer'] != signal:
            continue
        a,b = track['start_nm'],track['end_nm']
        base = {'track': track['uuid'], 'from_nm': a, 'to_nm': b}
        if track['kind'] != 'segment' or (a[0] != b[0] and a[1] != b[1]) or a == b:
            # Residuals are diagnostic floats; the exact squared quantities are retained.
            residuals = []
            length2 = sum((b[i]-a[i])**2 for i in (0,1))
            if track['kind'] == 'segment' and length2:
                for flank in ground:
                    c,d = flank['start_nm'],flank['end_nm']
                    if cross(a,b,c) != cross(a,b,d):
                        continue
                    squared = Fraction(cross(a,b,c)**2, length2)
                    target = Fraction(track['width_nm']+flank['width_nm'],2)+gap
                    residuals.append({'flank': flank['uuid'], 'centre_distance_squared_nm2': str(squared),
                                      'target_squared_nm2': str(target*target),
                                      'grid_residual_nm': math.sqrt(squared)-float(target)})
            intervals.append({**base, 'status': 'unknown', 'reason': 'non_axis_or_curve',
                              'grid_residuals': residuals})
            continue
        axis = 0 if a[1] == b[1] else 1
        perp = 1-axis
        lo,hi = sorted((a[axis],b[axis]))
        center = a[perp]
        radius = Fraction(track['width_nm'],2)+gap
        cuts = {Fraction(lo), Fraction(hi)}
        transitions = []
        parallel = [p for p in ground if p['start_nm'][perp] == p['end_nm'][perp]]
        for p in parallel:
            cuts.update(Fraction(v) for v in (p['start_nm'][axis],p['end_nm'][axis]) if lo < v < hi)
        for p in primitives:
            if p['net'] != rf or p['uuid'] == track['uuid']:
                continue
            box = p['bbox_nm']
            if box[perp]-radius <= center <= box[perp+2]+radius:
                left,right = max(Fraction(lo),box[axis]-radius),min(Fraction(hi),box[axis+2]+radius)
                if left < right:
                    transitions.append((left,right,p['uuid']))
                    cuts.update((left,right))
        cuts = sorted(cuts)
        for left,right in zip(cuts,cuts[1:]):
            x,y = list(a),list(a)
            x[axis], y[axis] = left,right
            # Decimal strings preserve half-nm interval endpoints without float rounding.
            row = {**base, 'from_nm': [str(v) for v in x], 'to_nm': [str(v) for v in y]}
            excluded = [uid for l,r,uid in transitions if l < right and r > left]
            if excluded:
                intervals.append({**row, 'status': 'unknown', 'reason': 'transition', 'objects': excluded})
                continue
            certificates, failures = [], []
            for side,sign in [('negative',-1),('positive',1)]:
                candidates = []
                for p in parallel:
                    c,d = p['start_nm'],p['end_nm']
                    if min(c[axis],d[axis]) <= left and max(c[axis],d[axis]) >= right and sign*(c[perp]-center) > 0:
                        actual = abs(c[perp]-center)-Fraction(track['width_nm']+p['width_nm'],2)
                        candidates.append((actual,p))
                if not candidates:
                    failures.append({'status': 'defect', 'reason': 'explicit_flank_missing', 'side': side})
                    continue
                actual,p = min(candidates,key=lambda pair: pair[0])
                target = Fraction(track['width_nm']+p['width_nm'],2)+gap
                certificates.append({'side': side, 'flank': p['uuid'], 'gap_nm': str(actual),
                                     'centre_distance_squared_nm2': str((p['start_nm'][perp]-center)**2),
                                     'target_squared_nm2': str(target*target)})
                if actual != gap:
                    failures.append({'status': 'defect', 'reason': 'exact_gap_mismatch', 'side': side})
                elif p['uuid'] not in anchored:
                    failures.append({'status': 'unknown', 'reason': 'reference_unanchored', 'side': side})
            for p in primitives:
                if p['net'] == rf:
                    continue
                kind = p['kind']
                if kind == 'segment':
                    dist = distance2(x,y,p['start_nm'],p['end_nm'])
                    limit = radius+Fraction(p['width_nm'],2)
                    obstructs, status = dist < limit*limit, 'defect'
                elif kind == 'via':
                    dist = point_distance2(p['position_nm'],x,y)
                    limit = radius+Fraction(p['width_nm'],2)
                    obstructs, status = dist < limit*limit, 'defect'
                elif kind == 'zone' and p['filled']:
                    dist = polygon_distance2(x,y,p['polygons_nm'])
                    obstructs, status = dist is not None and dist < radius*radius, 'defect'
                else:
                    dist = polygon_distance2(x,y,bbox_polygon(p['bbox_nm']))
                    obstructs, status = dist is not None and dist < radius*radius, 'unknown'
                if obstructs:
                    failures.append({'status': status, 'reason': 'copper_intrusion' if status == 'defect' else
                                     'unsupported_boundary_near_gap', 'object': p['uuid']})
            status = 'defect' if any(f['status']=='defect' for f in failures) else 'unknown' if failures else 'pass'
            intervals.append({**row, 'status': status, 'certificates': certificates, 'failures': failures})
    statuses = {r['status'] for r in intervals}
    status = 'defect' if 'defect' in statuses else 'unknown' if 'unknown' in statuses or not statuses else 'pass'
    return {'status': status, 'scope': 'exact CAD axis-aligned ordinary intervals only; transitions ungraded',
            'target_nm': gap, 'tolerance_nm': 0, 'intervals': intervals,
            'geometry_sha256': facts['geometry_sha256']}
