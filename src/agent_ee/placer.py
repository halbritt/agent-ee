"""Deterministic net-aware greedy placement, over captured geometry only.

The search is deliberately conservative: inflated courtyard boxes reserve space
for parts it places. Fixed courtyards and declared obstacles are exact polygons
that keep the gap (AGENTEE-179). Full polygons and the independent placement
judge validate the proposed plan. A bounded search failure is unknown, never a
proved design defect.

AGENTEE-212: curved courtyards reserve their outer bound and prove
infeasibility only with their inner bound; a fixed courtyard may leave the
outline only inside its envelope-bound overhang band; declared groups (target
regions, anchor distances) are enforced and graded; any part without a
qualified mounting-side courtyard is refused by name.
"""
import copy
from decimal import Decimal
from fractions import Fraction

from . import placement_groups
from .placement import (area2, courtyard_bounds, cross, edges, intersection_area, judge, overhang_bands,
                        qualified_polygons, validate_capture)
from .placement_predicates import segment_distance2
from .symbol_library import LibraryError, nanometres


# Native pcbnew PAD_SHAPE values, read from the KiCad 10.0.7 bindings.
# TRAPEZOID (3) and CUSTOM (6) stay unqualified until their effect is reviewed.
PAD_SHAPES = {0: 'circle', 1: 'rect', 2: 'oval', 4: 'roundrect', 5: 'chamfered_rect'}
PAD_SHAPE_PCBNEW_MAJOR = '10'


def rotate(point, quarter_turns):
    x, y = point
    for _ in range(quarter_turns % 4):
        x, y = y, -x  # KiCad positive rotation, in board coordinates.
    return [x, y]


def transform(fp, position, rotation):
    placed = copy.deepcopy(fp)
    origin = fp['position_nm']
    turns = int((rotation-fp['rotation_deg']) / 90)
    def move(p):
        x, y = rotate([p[0]-origin[0], p[1]-origin[1]], turns)
        return [x+position[0], y+position[1]]
    for courtyard in placed['courtyards'].values():
        for key in ('polygons', 'inner_polygons'):
            if key in courtyard:
                courtyard[key] = [[move(p) for p in poly] for poly in courtyard[key]]
    for keepout in placed.get('packing_keepouts', []):
        keepout['polygons'] = [[move(p) for p in poly] for poly in keepout['polygons']]
    for pad in placed['pads']:
        pad['position_nm'] = move(pad['position_nm'])
        pad['rotation_deg'] = (pad['rotation_deg'] + rotation-fp['rotation_deg']) % 360
    placed.update(position_nm=list(position), rotation_deg=rotation)
    return placed


def boxes(fp):
    return {side: (min(p[0] for poly in courtyard['polygons'] for p in poly),
                   min(p[1] for poly in courtyard['polygons'] for p in poly),
                   max(p[0] for poly in courtyard['polygons'] for p in poly),
                   max(p[1] for poly in courtyard['polygons'] for p in poly))
            for side, courtyard in fp['courtyards'].items()}


def _edge_margin_nm(policy):
    value = policy.get('min_courtyard_edge_mm')
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError('min_courtyard_edge_mm must be a nonnegative number')
    try:
        margin_nm = nanometres(str(value))
    except LibraryError as exc:
        raise ValueError('min_courtyard_edge_mm must be exact to 1 nm') from exc
    if margin_nm < 0:
        raise ValueError('min_courtyard_edge_mm must be nonnegative')
    return margin_nm


def _validate_policy(policy, refs):
    if policy.get('schema') != 'agent-ee.packing-policy/1':
        raise ValueError('unsupported packing policy')
    if type(policy.get('step_budget')) is not int or not 1 <= policy['step_budget'] <= 1_000_000:
        raise ValueError('step budget must be 1..1000000')
    if type(policy.get('gap_nm')) is not int or policy['gap_nm'] < 0:
        raise ValueError('gap must be nonnegative integer nanometres')
    _edge_margin_nm(policy)
    for name in ('fixed_refs', 'priority_refs', 'ground_nets'):
        seq = policy.get(name)
        if not isinstance(seq, list) or any(not isinstance(s, str) or not s for s in seq) or len(set(seq)) != len(seq):
            raise ValueError('expected unique explicit ' + name)
        if name != 'ground_nets' and not set(seq) <= refs:
            raise ValueError('unknown declared reference')
    if not isinstance(policy.get('obstacles'), list):
        raise ValueError('explicit obstacles required')
    for obstacle in policy['obstacles']:
        if obstacle.get('side') not in ('F', 'B'):
            raise ValueError('obstacle side required')
        qualified_polygons({'status': 'proven', 'polygons': obstacle['polygons']})
    overhangs = policy.get('fixed_overhangs', [])
    if not isinstance(overhangs, list) or any(not isinstance(o, dict) or o.get('reference') not in refs
                                              for o in overhangs):
        raise ValueError('fixed_overhangs must declare known references')
    if 'groups' in policy:
        placement_groups.validate(policy['groups'], refs)


def _qualified(capture):
    validate_capture(capture)
    outline = qualified_polygons(capture['outline'])
    if len(outline) != 1:
        raise ValueError('single closed outline required')
    # Shape numbers are only meaningful for the pcbnew enum they came from.
    version = capture.get('pcbnew_version')
    if any(fp['pads'] for fp in capture['footprints']) and (
            not isinstance(version, str) or version.split('.')[0] != PAD_SHAPE_PCBNEW_MAJOR):
        raise ValueError(f'native pad shape enum not qualified for pcbnew {version!r}')
    # Fixed or movable, a part without a qualified courtyard is refused by name;
    # no courtyard is invented from pads, silkscreen or a bounding box.
    missing = sorted(fp['ref'] for fp in capture['footprints'] if fp['side'] not in fp['courtyards'])
    if missing:
        raise ValueError(f"mounting-side courtyard missing: {', '.join(missing)}")
    for fp in capture['footprints']:
        if fp['rotation_deg'] % 90:
            raise ValueError('initial footprint rotations must be quarter turns')
        for side, courtyard in sorted(fp['courtyards'].items()):
            try:
                outer, inner = courtyard_bounds(courtyard)
            except ValueError as exc:
                reason = courtyard.get('reason') if isinstance(courtyard, dict) else None
                raise ValueError(f"{fp['ref']} {side} courtyard not qualified: {exc}" +
                                 (f' ({reason})' if reason else '')) from None
            courtyard['polygons'] = outer
            if inner is not outer:
                courtyard['inner_polygons'] = inner
        for pad in fp['pads']:
            if not isinstance(pad, dict):
                raise ValueError('pad record must be an object')
            # Pad outlines never enter the search or the judge; they still must be
            # a reviewed native shape so that the plan states what it covers.
            if type(pad.get('shape')) is not int or pad['shape'] not in PAD_SHAPES:
                raise ValueError(f"{fp['ref']} pad {pad.get('number')!r}: native pad shape "
                                 f"{pad.get('shape')!r} not qualified for packing")
            if type(pad.get('rotation_deg')) not in (int, float) or pad['rotation_deg'] % 90:
                raise ValueError(f"{fp['ref']} pad {pad.get('number')!r}: pad rotation must be a quarter turn")
    return outline[0]


def _keepouts(capture):
    """Reuse the native rule-area inventory; unqualified exclusions stop packing."""
    owners = {fp['uuid']: fp for fp in capture['footprints']}
    for fp in owners.values():
        fp['packing_keepouts'] = []
    obstacles = []
    areas = capture.get('rule_areas', [])
    if not isinstance(areas, list):
        raise ValueError('malformed rule-area inventory')
    for area in areas:
        if (not isinstance(area, dict) or not isinstance(area.get('forbid'), dict) or
                type(area['forbid'].get('footprints')) is not bool):
            raise ValueError('malformed rule-area footprint flag')
        if not area['forbid']['footprints']:
            continue
        owner = area.get('owner')
        if not isinstance(owner, str) or (owner != 'board' and owner not in owners):
            raise ValueError('unknown rule-area owner')
        layers = area.get('layers')
        copper = {'F.Cu', 'B.Cu', '*.Cu'} | {f'In{i}.Cu' for i in range(1, 31)}
        if (not isinstance(layers, list) or not layers or
                any(not isinstance(l, str) or l not in copper for l in layers) or
                len(set(layers)) != len(layers)):
            raise ValueError('malformed rule-area layers')
        polygons = qualified_polygons(area.get('outline'))
        target = obstacles if owner == 'board' else owners[owner]['packing_keepouts']
        for side in ('F', 'B'):
            if side + '.Cu' in layers or '*.Cu' in layers:
                target.append({'side': side, 'polygons': polygons})
    return obstacles


def _keepout_boxes(obstacles):
    return [entry for obstacle in obstacles for entry in
            boxes({'courtyards': {obstacle['side']: obstacle}}).items()]


def _in_keepouts(fp, obstacles):
    return any(intersection_area(c, p) > 0
               for obstacle in obstacles
               for c in fp['courtyards'].get(obstacle['side'], {}).get('polygons', [])
               for p in obstacle['polygons'])


def _keepout_blocked(candidate, placed, obstacles):
    # An owner is exempt from its own rule area. Check both directions when
    # placing a module after peers: its moving keepout must not engulf them.
    return (_in_keepouts(candidate, obstacles) or
            any(_in_keepouts(candidate, fp.get('packing_keepouts', [])) or
                _in_keepouts(fp, candidate.get('packing_keepouts', [])) for fp in placed))


def pad_shape_qualification():
    """The pad-shape domain recorded in every complete plan."""
    return {'pcbnew_major': PAD_SHAPE_PCBNEW_MAJOR,
            'accepted': {str(k): v for k, v in sorted(PAD_SHAPES.items())},
            'pad_rotation': 'quarter-turn', 'pad_outline_in_geometry': False}


def _blocked(candidate, reserved, gap):
    for side, (x0, y0, x1, y1) in boxes(candidate).items():
        for other_side, (a0, b0, a1, b1) in reserved:
            if side == other_side and not (x1+gap <= a0 or a1+gap <= x0 or y1+gap <= b0 or b1+gap <= y0):
                return True
    return False


def _near(candidate, exact, gap):
    """True when a courtyard overlaps, or comes closer than the gap to, an exact obstacle.

    Boxes only prefilter; a concave courtyard's notch stays usable.
    """
    for side, (x0, y0, x1, y1) in boxes(candidate).items():
        for obstacle in exact:
            if obstacle['side'] != side:
                continue
            a0, b0, a1, b1 = obstacle['box']
            if x1+gap <= a0 or a1+gap <= x0 or y1+gap <= b0 or b1+gap <= y0:
                continue
            for c in candidate['courtyards'][side]['polygons']:
                for p in obstacle['polygons']:
                    if intersection_area(c, p) > 0 or (gap and min(
                            segment_distance2(a, b, d, e) for a, b in edges(c) for d, e in edges(p)) < gap*gap):
                        return True
    return False


def _exact(side, polygons):
    entry = {'side': side, 'polygons': polygons}
    entry['box'] = boxes({'courtyards': {side: entry}})[side]
    return entry


def _fits(candidate, outline, margin_nm=0, band=()):
    # A band is a declared overhang across outline edges; its regions share no area with the board.
    for courtyard in candidate['courtyards'].values():
        for p in courtyard['polygons']:
            inside = intersection_area(p, outline) + sum(intersection_area(p, r) for r in band)
            if inside != Fraction(abs(area2(p)), 2):
                return False
            if margin_nm > 0 and min(segment_distance2(a, b, c, d)
                                     for a, b in edges(p) for c, d in edges(outline)) < margin_nm * margin_nm:
                return False
    return True


def _candidate_positions(fp, rotation, outline, reserved, gap, margin_nm=0, targets=()):
    at_origin = transform(fp, [0, 0], rotation)
    bounds = list(boxes(at_origin).values())
    bounds = [(min(b[0] for b in bounds), min(b[1] for b in bounds),
               max(b[2] for b in bounds), max(b[3] for b in bounds))]
    bounds.extend(b for _, b in _keepout_boxes(at_origin.get('packing_keepouts', [])))
    xs, ys = set(), set()
    for x0, y0, x1, y1 in bounds:
        xs.update(p[0] + margin_nm - x0 for p in outline)
        xs.update(p[0] - margin_nm - x1 for p in outline)
        xs.update(p[0]-x for p in outline for x in (x0, x1))
        ys.update(p[1] + margin_nm - y0 for p in outline)
        ys.update(p[1] - margin_nm - y1 for p in outline)
        ys.update(p[1]-y for p in outline for y in (y0, y1))
        for _, (a0, b0, a1, b1) in reserved:
            xs.update((a0-gap-x1, a1+gap-x0, a0-x0, a1-x1))
            ys.update((b0-gap-y1, b1+gap-y0, b0-y0, b1-y1))
        # Declared group region vertices and anchor points align candidates as outline vertices do.
        for p in targets:
            xs.update((p[0]-x0, p[0]-x1, p[0]))
            ys.update((p[1]-y0, p[1]-y1, p[1]))
    for x in sorted(xs):
        for y in sorted(ys):
            yield x, y


def _wire_cost(candidate, placed, grounds):
    # Integer Manhattan pad-to-nearest-same-net-pad cost: deterministic ties.
    cost = 0
    peers = [pad for fp in placed for pad in fp['pads']]
    for pad in candidate['pads']:
        if not pad['net'] or pad['net'] in grounds:
            continue
        distances = [sum(abs(a-b) for a, b in zip(pad['position_nm'], other['position_nm']))
                     for other in peers if other['net'] == pad['net']]
        if distances:
            cost += min(distances)
    return cost


def _margin_mm(margin_nm):
    return float(Fraction(margin_nm, 1_000_000))


def _failure(capture, policy, status, outcome, steps, reason, margin_nm=0):
    return {'schema': 'agent-ee.packing-plan/1', 'board_sha256': capture.get('board_sha256'),
            'status': status, 'outcome': outcome, 'steps_used': steps,
            'step_budget': policy['step_budget'], 'min_courtyard_edge_mm': _margin_mm(margin_nm),
            'policy': copy.deepcopy(policy),
            'reason': reason, 'placements': [], 'authority': 'standalone-unsealed-plan'}


def _fraction(value):
    return [value.numerator, value.denominator]


def _inner(courtyard):
    return courtyard.get('inner_polygons', courtyard['polygons'])


def _area_impossibility(capture, outline, bands):
    # Inner bounds only: an outer curve bound overstates area. A fixed part with a
    # declared overhang counts only its area on the board; every other courtyard,
    # fixed or movable, must lie wholly inside the outline.
    board_area = Fraction(abs(area2(outline)), 2)
    for side in ('F', 'B'):
        area = sum(intersection_area(p, outline) if fp['ref'] in bands else Fraction(abs(area2(p)), 2)
                   for fp in capture['footprints'] if side in fp['courtyards']
                   for p in _inner(fp['courtyards'][side]))
        if area > board_area:
            return {'kind': 'courtyard_area', 'side': side, 'board_area_nm2': _fraction(board_area),
                    'required_courtyard_area_nm2': _fraction(area)}
    return None


def _hull(points):
    points = sorted(set(map(tuple, points)))
    def half(seq):
        chain = []
        for p in seq:
            while len(chain) >= 2 and cross(chain[-2], chain[-1], p) <= 0:
                chain.pop()
            chain.append(p)
        return chain[:-1]
    return half(points) + half(reversed(points))


def _extent(points):
    """Exact squared minimum width and squared diameter of a point set's hull.

    Both are invariant under rotation and translation, so containment in the
    outline needs each to be no larger than the outline's own.
    """
    hull = _hull(points)
    width = min(Fraction(max(cross(a, b, p) for p in hull)**2, (b[0]-a[0])**2 + (b[1]-a[1])**2)
                for a, b in zip(hull, hull[1:] + hull[:1]))
    diameter = max((a[0]-b[0])**2 + (a[1]-b[1])**2 for a in hull for b in hull)
    return {'min_width_squared_nm2': width, 'diameter_squared_nm2': Fraction(diameter)}


def _part_impossibility(capture, outline, bands):
    # Every courtyard of a part, on either side, must lie inside the one outline,
    # whatever rigid motion is chosen; this proof is not limited to quarter turns.
    # A fixed part with a declared overhang need not fit the outline.
    board = _extent(outline)
    for fp in sorted(capture['footprints'], key=lambda f: f['ref']):
        if fp['ref'] in bands:
            continue
        part = _extent([p for courtyard in fp['courtyards'].values()
                        for poly in _inner(courtyard) for p in poly])
        for measure in ('min_width_squared_nm2', 'diameter_squared_nm2'):
            if part[measure] > board[measure]:
                return {'kind': measure.removesuffix('_squared_nm2'), 'ref': fp['ref'],
                        'board_' + measure: _fraction(board[measure]),
                        'footprint_' + measure: _fraction(part[measure])}
    return None


def _fixed_refs(capture, policy):
    return set(policy['fixed_refs']) | {f['ref'] for f in capture['footprints'] if f.get('locked')}


def _initial_reservations(capture, policy, outline, obstacles, bands, groups):
    """Fixed parts and declared obstacles become exact polygon obstacles, never boxes."""
    fixed = _fixed_refs(capture, policy)
    placed = [copy.deepcopy(f) for f in capture['footprints'] if f['ref'] in fixed]
    exact = [_exact(o['side'], qualified_polygons({'status': 'proven', 'polygons': o['polygons']}))
             for o in policy['obstacles']]
    for i, fp in enumerate(placed):
        # Fixed footprints (such as edge-mounted connectors) are exempt from min_courtyard_edge_mm.
        # Only an envelope-bound overhang band lets a fixed courtyard leave the outline.
        if (not _fits(fp, outline, 0, bands.get(fp['ref'], ())) or _near(fp, exact, policy['gap_nm'])
                or _keepout_blocked(fp, placed[:i], obstacles)):
            raise ValueError(f"fixed placement {fp['ref']} conflicts with outline, obstacle, keepout or reserved gap")
        exact.extend(_exact(side, c['polygons']) for side, c in sorted(fp['courtyards'].items()))
    positions = {fp['ref']: fp['position_nm'] for fp in placed}
    for fp in placed:
        group = groups.get(fp['ref'])
        if group is None:
            continue
        if 'anchor' in group and placement_groups.anchor_point(group, positions) is None:
            raise ValueError(f"fixed placement {fp['ref']} in group {group['id']} needs a fixed anchor")
        if not placement_groups.admits(fp, group, positions):
            raise ValueError(f"fixed placement {fp['ref']} violates declared group {group['id']}")
    return fixed, placed, exact


def pack(capture, policy):
    """Return a complete deterministic plan or typed failure; never mutate inputs."""
    capture = copy.deepcopy(capture)
    validate_capture(capture)
    _validate_policy(policy, {f['ref'] for f in capture['footprints']})
    margin_nm = _edge_margin_nm(policy)
    groups = placement_groups.validate(policy['groups']) if 'groups' in policy else []
    members = placement_groups.membership(groups)
    try:
        outline = _qualified(capture)
        obstacles = _keepouts(capture)
        bands = overhang_bands(policy.get('fixed_overhangs', []), outline)
    except ValueError as exc:
        return _failure(capture, policy, 'unsupported_geometry', 'unknown', 0, str(exc), margin_nm)
    fixed = _fixed_refs(capture, policy)
    loose = sorted(set(bands) - fixed)
    if loose:
        raise ValueError(f"courtyard overhang declared for movable reference {', '.join(loose)}")
    proof = _area_impossibility(capture, outline, bands)
    if proof:
        failure = _failure(capture, policy, 'packing_infeasible', 'defect', 0,
                           f"{proof['side']} courtyard area exceeds board area", margin_nm)
        return {**failure, 'proof': proof}
    proof = _part_impossibility(capture, outline, bands)
    if proof:
        failure = _failure(capture, policy, 'packing_infeasible', 'defect', 0,
                           f"{proof['ref']} courtyard {proof['kind'].replace('_', ' ')} exceeds the board's", margin_nm)
        return {**failure, 'proof': proof}
    try:
        fixed, placed, exact = _initial_reservations(capture, policy, outline, obstacles, bands, members)
    except ValueError as exc:
        return _failure(capture, policy, 'fixed_conflict', 'unknown', 0, str(exc), margin_nm)
    priorities = {ref: i for i, ref in enumerate(policy['priority_refs'])}
    # A movable group anchor is placed before every member (anchors are never anchored members).
    leaders = {g['anchor']['reference'] for g in groups if 'reference' in g.get('anchor', {})}
    queue = sorted((fp for fp in capture['footprints'] if fp['ref'] not in fixed),
                   key=lambda f: (f['ref'] not in leaders, priorities.get(f['ref'], len(priorities)),
                                  -len(f['pads']), f['ref']))
    positions = {fp['ref']: fp['position_nm'] for fp in placed}
    steps = 0
    reserved = []
    for fp in queue:
        best = None
        anchors = reserved + [(o['side'], o['box']) for o in exact]
        anchors += _keepout_boxes(obstacles + [area for f in placed for area in f.get('packing_keepouts', [])])
        group = members.get(fp['ref'])
        targets = [] if group is None else [p for poly in group.get('region', {}).get('polygons', []) for p in poly]
        if group is not None and 'anchor' in group:
            targets.append(placement_groups.anchor_point(group, positions))
        for rotation in (0, 90, 180, 270):
            for position in _candidate_positions(fp, rotation, outline, anchors, policy['gap_nm'], margin_nm,
                                                 targets):
                if steps == policy['step_budget']:
                    return _failure(capture, policy, 'packing_budget_exhausted', 'unknown', steps,
                                    'deterministic candidate budget exhausted', margin_nm)
                steps += 1
                proposed = transform(fp, position, rotation)
                if (_blocked(proposed, reserved, policy['gap_nm']) or not _fits(proposed, outline, margin_nm) or
                        _near(proposed, exact, policy['gap_nm']) or _keepout_blocked(proposed, placed, obstacles) or
                        (group is not None and not placement_groups.admits(proposed, group, positions))):
                    continue
                key = (_wire_cost(proposed, placed, policy['ground_nets']), *position, rotation)
                if best is None or key < best[0]:
                    best = key, proposed
        if best is None:
            return _failure(capture, policy, 'packing_search_exhausted', 'unknown', steps,
                            'no candidate in the finite greedy search; infeasibility unproved', margin_nm)
        placed.append(best[1])
        positions[fp['ref']] = best[1]['position_nm']
        reserved.extend(boxes(best[1]).items())
    proposed_capture = {**capture, 'footprints': placed}
    judgment = judge(proposed_capture, overhangs=policy.get('fixed_overhangs'))
    judgment['basis'] = 'transformed-proposal-not-native'
    if judgment['verdict'] != 'pass':
        return _failure(capture, policy, 'judge_rejected', 'unknown', steps,
                        'independent full-polygon judge rejected complete proposal', margin_nm)
    grading = placement_groups.grade(proposed_capture, groups) if groups else None
    if grading is not None and grading['status'] != 'pass':
        return _failure(capture, policy, 'judge_rejected', 'unknown', steps,
                        'declared placement groups not satisfied by the complete proposal', margin_nm)
    extra = {} if grading is None else {'group_grading': grading}
    return {**extra, 'schema': 'agent-ee.packing-plan/1', 'board_sha256': capture['board_sha256'],
            'status': 'planned', 'outcome': 'info', 'authority': 'standalone-unsealed-plan',
            'steps_used': steps, 'step_budget': policy['step_budget'],
            'min_courtyard_edge_mm': _margin_mm(margin_nm),
            'policy': copy.deepcopy(policy), 'pad_shapes': pad_shape_qualification(), 'judgment': judgment,
            'placements': [{'ref': f['ref'], 'uuid': f['uuid'], 'position_nm': f['position_nm'],
                            'rotation_deg': f['rotation_deg'], 'fixed': f['ref'] in fixed}
                           for f in sorted(placed, key=lambda f: f['ref'])]}
