"""Advisory saved-board reserve costs; never a packing predicate (AGENTEE-380)."""
import copy
from fractions import Fraction

from .placement import area2, edges, qualified_polygons, simple_polygon

SCHEMA = 'agent-ee.reserved-area/1'
# Computational coverage guards, not physical limits or acceptance thresholds.
MAX_EDGE_PAIRS = 2_000_000
ADVISORY_FRACTION = Fraction(1, 20)


def _ratio(value):
    value = Fraction(value)
    return [value.numerator, value.denominator]


def _intervals(polygons, x):
    intervals = []
    for polygon in polygons:
        ys = sorted(Fraction(a[1]) + (x-a[0]) * Fraction(b[1]-a[1], b[0]-a[0])
                    for a, b in edges(polygon) if min(a[0], b[0]) < x < max(a[0], b[0]))
        intervals.extend(zip(ys[::2], ys[1::2]))
    return intervals


def _contains(intervals, y):
    return any(a < y < b for a, b in intervals)


def _area(outline, reserves):
    """Exact area of outline intersect union(positive minus negative polygon sets).

    Split x at vertices and edge crossings. Within each resulting slab, boundary
    ordering is constant and occupied height is linear, so midpoint integration
    is exact. A reserve's negative set is its owner's fabrication body only.
    """
    if not reserves:
        return Fraction(0)
    polygons = [outline] + [p for positive, negative in reserves for p in positive + negative]
    segments = sorted((min(a[0], b[0]), max(a[0], b[0]), a, b)
                      for p in polygons for a, b in edges(p) if a[0] != b[0])
    cuts = {Fraction(p[0]) for poly in polygons for p in poly}
    pairs = 0
    for i, (x0, x1, a, b) in enumerate(segments):
        for u0, u1, c, d in segments[i+1:]:
            if u0 >= x1:
                break
            pairs += 1
            if pairs > MAX_EDGE_PAIRS:
                raise ValueError('reserve area edge-pair work guard exhausted')
            slope = Fraction(b[1]-a[1], b[0]-a[0])
            other = Fraction(d[1]-c[1], d[0]-c[0])
            if slope != other:
                x = Fraction(c[1]-a[1] + slope*a[0] - other*c[0], slope-other)
                if max(x0, u0) < x < min(x1, u1):
                    cuts.add(x)
    ordered = sorted(cuts)
    total = Fraction(0)
    for left, right in zip(ordered, ordered[1:]):
        x = (left+right)/2
        board = _intervals([outline], x)
        if not board:
            continue
        spans = [(_intervals(pos, x), _intervals(neg, x)) for pos, neg in reserves]
        ys = sorted({y for interval in board + [v for pair in spans for seq in pair for v in seq]
                     for y in interval})
        for lo, hi in zip(ys, ys[1:]):
            y = (lo+hi)/2
            if _contains(board, y) and any(_contains(pos, y) and not _contains(neg, y)
                                         for pos, neg in spans):
                total += (right-left)*(hi-lo)
    return total


def fabrication_outlines(board_path):
    """Use the native inventory's existing primitive and contour qualification."""
    import pcbnew
    from .placement_probe import primitive, prove_contours
    board = pcbnew.LoadBoard(str(board_path))
    return {fp.GetReference(): {
        side: prove_contours([primitive(s) for s in fp.GraphicalItems()
                             if s.GetLayer() == layer and s.GetClass() == 'PCB_SHAPE'],
                             'fabrication_missing', 'fabrication_unproven')
        for side, layer in (('F', pcbnew.F_Fab), ('B', pcbnew.B_Fab))}
        for fp in board.GetFootprints()}


def _inventory(facts, policy, fabrication):
    rows = []
    owners = {fp['uuid']: fp['ref'] for fp in facts['footprints']}
    for i, obstacle in enumerate(policy['obstacles']):
        rows.append((obstacle.get('id', obstacle.get('source', f'obstacle {i}')),
                     'declared_obstacle', obstacle['side'], obstacle['polygons'], []))
    for area in facts.get('rule_areas', []):
        if not any(area['forbid'].values()):
            continue
        for side in ('F', 'B'):
            if side+'.Cu' in area['layers'] or '*.Cu' in area['layers']:
                rows.append((owners.get(area['owner'], area['uuid']), 'rule_area', side,
                             area['outline'], area['uuid']))
    for fp in facts['footprints']:
        for side, courtyard in sorted(fp['courtyards'].items()):
            rows.append((fp['ref'], 'courtyard_excess', side, courtyard,
                         fabrication.get(fp['ref'], {}).get(side)))
    return rows


def report(facts, policy, fabrication):
    """Measure each owner separately and each side as a union (no overlap inflation).

    Unknown rows remain named, make the side total unknown, and never become zero.
    Known per-owner areas may overlap and must not be summed to infer a side total.
    """
    result = {'schema': SCHEMA, 'board_sha256': facts['board_sha256'],
              'authority': 'advisory; does not change packing, score or verdict',
              'threshold': {'fraction': _ratio(ADVISORY_FRACTION), 'enforcement': 'advisory',
                            'provenance': 'AGENTEE-380 suggestion; unsourced'},
              'sides': {}, 'findings': []}
    try:
        outlines = qualified_polygons(facts['outline'])
        if len(outlines) != 1:
            raise ValueError('single board contour required')
        outline = outlines[0]
    except ValueError as exc:
        return {**result, 'status': 'unknown', 'reason': str(exc)}
    board_area = Fraction(abs(area2(outline)), 2)
    result['board_area_mm2'] = _ratio(board_area / 10**12)
    for side in ('F', 'B'):
        rows, shapes = [], []
        for owner, kind, layer, positive, negative in _inventory(facts, policy, fabrication):
            if side != layer:
                continue
            row = {'owner': owner, 'kind': kind, 'side': side}
            try:
                if kind == 'declared_obstacle':
                    pos = [simple_polygon(p) for p in positive]
                    if not pos:
                        raise ValueError('missing obstacle polygons')
                    neg = []
                else:
                    pos = qualified_polygons(positive)
                    if kind == 'courtyard_excess':
                        if 'inner_polygons' in positive:
                            raise ValueError('curved courtyard area is bounded, not exact')
                        neg = qualified_polygons(negative)
                    else:
                        row['id'] = negative
                        row['name'] = next(a.get('name', '') for a in facts.get('rule_areas', [])
                                           if a['uuid'] == negative)
                        neg = []
                area = _area(outline, [(pos, neg)])
                row.update(status='measured', area_mm2=_ratio(area/10**12),
                           side_fraction=_ratio(area/board_area))
                shapes.append((pos, neg))
                if area/board_area > ADVISORY_FRACTION:
                    result['findings'].append({'kind': 'large_reserve', 'enforcement': 'advisory',
                                               'reserve': row})
            except ValueError as exc:
                row.update(status='unknown', reason=str(exc))
            rows.append(row)
        measured = {'owners': rows}
        if any(row['status'] == 'unknown' for row in rows):
            measured.update(status='unknown', reason='unmeasured owners; known areas are not a complete total')
        else:
            try:
                area = _area(outline, shapes)
                measured.update(status='measured', union_area_mm2=_ratio(area/10**12),
                                side_fraction=_ratio(area/board_area))
            except ValueError as exc:
                measured.update(status='unknown', reason=str(exc))
        result['sides'][side] = measured
    result['status'] = 'unknown' if any(s['status'] == 'unknown' for s in result['sides'].values()) else 'measured'
    return result


def _poses(fp, outline, margin):
    from . import placer
    xs, ys = [p[0] for p in outline], [p[1] for p in outline]
    for angle in (0, 90, 180, 270):
        bounds = placer.boxes(placer.transform(fp, [0, 0], angle))[fp['side']]
        positions = {tuple(fp['position_nm'])}
        for x in (min(xs)-bounds[0]+margin, max(xs)-bounds[2]-margin):
            positions.add((x, fp['position_nm'][1]))
        for y in (min(ys)-bounds[1]+margin, max(ys)-bounds[3]-margin):
            positions.add((fp['position_nm'][0], y))
        for position in sorted(positions):
            yield position, angle


def _move_contours(fp, contours, position, angle):
    from .placer import transform
    proxy = {'position_nm': fp['position_nm'], 'rotation_deg': fp['rotation_deg'],
             'pads': [], 'courtyards': contours}
    return transform(proxy, list(position), angle)['courtyards']


def _candidate_cost(facts, policy, fabrication, plan, fp, pose):
    from . import placer
    position, angle = pose
    candidate = placer.transform(fp, list(position), angle)
    outline = qualified_polygons(facts['outline'])[0]
    if not placer._fits(candidate, outline, placer._edge_margin_nm(policy)):
        return None
    proposed = copy.deepcopy(facts)
    proposed['footprints'] = [candidate if f['ref'] == fp['ref'] else f for f in proposed['footprints']]
    # Zone polygons are absolute board coordinates in the native inventory.
    for area in proposed.get('rule_areas', []):
        if area['owner'] == fp['uuid']:
            area['outline'] = _move_contours(fp, {'zone': area['outline']}, position, angle)['zone']
    check_policy = {**policy, 'fixed_refs': [f['ref'] for f in facts['footprints']]}
    # No search or clock budget is added: every part is fixed for grading.
    check_policy.pop('search_timeout_s', None)
    if placer.pack(proposed, check_policy)['status'] != 'planned':
        return None
    if 'block_arrangement' in plan:
        from .block_arrangement import saved_rules
        if any(r['enforcement'] == 'hard' and r['status'] != 'pass'
               for r in saved_rules(proposed, plan, policy)):
            return None
    moved_fab = {fp['ref']: _move_contours(fp, fabrication.get(fp['ref'], {}), position, angle)}
    selected = {**proposed, 'footprints': [candidate],
                'rule_areas': [a for a in proposed.get('rule_areas', []) if a['owner'] == fp['uuid']]}
    measured = report(selected, {**policy, 'obstacles': []}, moved_fab)
    return {(r['side'], r['kind'], r.get('id')): r for side in measured['sides'].values()
            for r in side['owners'] if r['status'] == 'measured'}


def _cheapest(row, trials, native_validator=None):
    choices = [(Fraction(*match['area_mm2']), position, angle)
               for (position, angle), measured in trials if measured is not None
               for match in [measured.get((row['side'], row['kind'], row.get('id')))]
               if match is not None]
    attempts = []
    eligible = []
    for area, position, angle in sorted(choices):
        if area >= Fraction(*row['area_mm2']):
            break
        if native_validator is None:
            eligible.append((area, position, angle))
            break
        validation = native_validator(row['owner'], position, angle)
        attempts.append({'position_nm': list(position), 'rotation_deg': angle, 'validation': validation})
        if validation['status'] == 'validated':
            eligible.append((area, position, angle))
            break
    best = eligible[0] if eligible else None
    if best is not None and best[0] < Fraction(*row['area_mm2']):
        return {'status': 'native_validated' if native_validator else 'geometry_qualified_native_unverified',
                'qualification': 'legal under captured rules' if native_validator else 'hypothetical, unverified',
                'native_attempts': attempts,
                'search_complete': native_validator is not None and not any(
                    a['validation']['status'] == 'unknown' for a in attempts),
                'position_nm': list(best[1]), 'rotation_deg': best[2], 'area_mm2': _ratio(best[0]),
                'saving_mm2': _ratio(Fraction(*row['area_mm2'])-best[0]), 'tested': len(trials),
                'domain': 'quarter turns at current origin and four outline-box edges'}
    if any(a['validation']['status'] == 'unknown' for a in attempts):
        return {'status': 'unknown', 'qualification': 'hypothetical, unverified', 'search_complete': False,
                'tested': len(trials), 'native_attempts': attempts,
                'domain': 'quarter turns at current origin and four outline-box edges',
                'reason': 'a cheaper pose could not be natively validated; minimum unresolved'}
    return {'status': 'none_found', 'tested': len(trials), 'native_attempts': attempts,
            'search_complete': native_validator is not None,
            'domain': 'quarter turns at current origin and four outline-box edges',
            'reason': 'finite diagnostic set; not a continuous optimality proof'}


def alternatives(facts, policy, fabrication, costs, plan, native_validator=None):
    """Cheapest geometry-qualified single-part pose in a finite diagnostic set.

    Other parts stay put. Fixed poses and rigid block members are not relaxed.
    Without a native validator, poses are hypothetical and unverified. With one,
    cost-ordered poses must pass saved-board DRC and preservation before selection.
    Only the supplied advisory cost record is mutated; design facts are borrowed.
    """
    from . import placer
    outline = qualified_polygons(facts['outline'])[0]
    fixed = placer._fixed_refs(facts, policy)
    block_members = {ref for block in plan.get('block_arrangement', {}).get('blocks', [])
                     for ref in block['references']}
    footprints = {f['ref']: f for f in facts['footprints']}
    trials = {}
    for side in costs['sides'].values():
        for row in side['owners']:
            ref = row['owner']
            if row['status'] != 'measured' or row['kind'] == 'declared_obstacle' or ref not in footprints:
                continue
            if Fraction(*row['area_mm2']) == 0:
                row['alternative'] = {'status': 'already_zero', 'reason': 'no cheaper nonnegative area'}
            elif ref in fixed or ref in block_members:
                row['alternative'] = {'status': 'not_searched',
                                      'reason': 'fixed owner pose' if ref in fixed else 'rigid block member'}
            elif row['kind'] == 'courtyard_excess':
                # Every pose in this domain keeps the whole courtyard inside.
                # Its rigid courtyard-minus-body area is invariant, and cannot
                # be smaller than an already clipped current reserve.
                row['alternative'] = {'status': 'no_cheaper_contained_pose',
                                      'reason': 'rigid courtyard-minus-body area is invariant under full containment'}
            else:
                if ref not in trials:
                    fp = footprints[ref]
                    trials[ref] = [(pose, _candidate_cost(facts, policy, fabrication, plan, fp, pose))
                                   for pose in _poses(fp, outline, placer._edge_margin_nm(policy))]
                row['alternative'] = _cheapest(row, trials[ref], native_validator)
    return costs


def saved_report(output, facts, plan):
    """Isolate all optional reserve diagnostics from the placement verdict.

    Discard partial measurements on failure so no stale legal alternative or
    apparent zero survives an unavailable advisory pipeline. Hard placement
    checks run outside this boundary in the single writer.
    """
    try:
        from .reserved_area_native import NativeAlternatives
        fabrication = fabrication_outlines(output)
        policy = {**plan['policy'], 'obstacles': [*plan['policy']['obstacles'],
            *plan.get('block_arrangement', {}).get('arrangement', {}).get('keepouts', [])]}
        costs = report(facts, policy, fabrication)
        if costs.get('sides'):
            alternatives(facts, plan['policy'], fabrication, costs, plan,
                         NativeAlternatives(output, output.parent / 'reserve-alternatives',
                                            facts, plan['policy'], plan))
        return costs
    except Exception as exc:
        return {'schema': SCHEMA, 'board_sha256': facts['board_sha256'],
                'authority': 'advisory; does not change packing, score or verdict',
                'status': 'unknown', 'sides': {}, 'findings': [],
                'reason': f'reserve diagnostics unavailable: {type(exc).__name__}: {exc}'}
