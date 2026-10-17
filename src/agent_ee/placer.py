"""Deterministic net-aware greedy placement, over captured geometry only.

The search is deliberately conservative: inflated courtyard boxes reserve space;
full polygons and the independent placement judge validate the proposed plan.
A bounded search failure is unknown, never a proved design defect.
"""
import copy
from fractions import Fraction

from .placement import area2, intersection_area, judge, qualified_polygons, validate_capture


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
        courtyard['polygons'] = [[move(p) for p in poly] for poly in courtyard['polygons']]
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


def _validate_policy(policy, refs):
    if policy.get('schema') != 'agent-ee.packing-policy/1':
        raise ValueError('unsupported packing policy')
    if type(policy.get('step_budget')) is not int or not 1 <= policy['step_budget'] <= 1_000_000:
        raise ValueError('step budget must be 1..1000000')
    if type(policy.get('gap_nm')) is not int or policy['gap_nm'] < 0:
        raise ValueError('gap must be nonnegative integer nanometres')
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


def _qualified(capture):
    validate_capture(capture)
    outline = qualified_polygons(capture['outline'])
    if len(outline) != 1:
        raise ValueError('single closed outline required')
    for fp in capture['footprints']:
        if fp['rotation_deg'] % 90:
            raise ValueError('initial footprint rotations must be quarter turns')
        if fp['side'] not in fp['courtyards']:
            raise ValueError('mounting-side courtyard missing')
        for courtyard in fp['courtyards'].values():
            courtyard['polygons'] = qualified_polygons(courtyard)
        for pad in fp['pads']:
            # Native PAD_SHAPE_RECT is 1 in the qualified KiCad 10 probe.
            if pad['shape'] != 1 or pad['rotation_deg'] % 90:
                raise ValueError('only axis-aligned rectangular pads supported')
    return outline[0]


def _blocked(candidate, reserved, gap):
    for side, (x0, y0, x1, y1) in boxes(candidate).items():
        for other_side, (a0, b0, a1, b1) in reserved:
            if side == other_side and not (x1+gap <= a0 or a1+gap <= x0 or y1+gap <= b0 or b1+gap <= y0):
                return True
    return False


def _fits(candidate, outline):
    return all(intersection_area(p, outline) == Fraction(abs(area2(p)), 2)
               for courtyard in candidate['courtyards'].values() for p in courtyard['polygons'])


def _candidate_positions(fp, rotation, outline, reserved, gap):
    at_origin = transform(fp, [0, 0], rotation)
    bounds = list(boxes(at_origin).values())
    x0, y0 = min(b[0] for b in bounds), min(b[1] for b in bounds)
    x1, y1 = max(b[2] for b in bounds), max(b[3] for b in bounds)
    xs = {p[0]-x0 for p in outline} | {p[0]-x1 for p in outline}
    ys = {p[1]-y0 for p in outline} | {p[1]-y1 for p in outline}
    for _, (a0, b0, a1, b1) in reserved:
        xs.update((a0-gap-x1, a1+gap-x0, a0-x0, a1-x1))
        ys.update((b0-gap-y1, b1+gap-y0, b0-y0, b1-y1))
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


def _failure(capture, policy, status, outcome, steps, reason):
    return {'schema': 'agent-ee.packing-plan/1', 'board_sha256': capture.get('board_sha256'),
            'status': status, 'outcome': outcome, 'steps_used': steps,
            'step_budget': policy['step_budget'], 'policy': copy.deepcopy(policy),
            'reason': reason, 'placements': [], 'authority': 'standalone-unsealed-plan'}


def _area_impossibility(capture, outline):
    board_area = Fraction(abs(area2(outline)), 2)
    for side in ('F', 'B'):
        area = sum(Fraction(abs(area2(p)), 2) for fp in capture['footprints']
                   for p in fp['courtyards'].get(side, {}).get('polygons', []))
        if area > board_area:
            return {'side': side, 'board_area_nm2': [board_area.numerator, board_area.denominator],
                    'required_courtyard_area_nm2': [area.numerator, area.denominator]}
    return None


def _initial_reservations(capture, policy, outline):
    fixed = set(policy['fixed_refs']) | {f['ref'] for f in capture['footprints'] if f.get('locked')}
    placed = [copy.deepcopy(f) for f in capture['footprints'] if f['ref'] in fixed]
    reserved = []
    for obstacle in policy['obstacles']:
        shell = {'courtyards': {obstacle['side']: {'polygons': obstacle['polygons']}}}
        reserved.extend(boxes(shell).items())
    for fp in placed:
        if not _fits(fp, outline) or _blocked(fp, reserved, policy['gap_nm']):
            raise ValueError('fixed placement conflicts with outline, obstacle or reserved gap')
        reserved.extend(boxes(fp).items())
    return fixed, placed, reserved


def pack(capture, policy):
    """Return a complete deterministic plan or typed failure; never mutate inputs."""
    capture = copy.deepcopy(capture)
    validate_capture(capture)
    _validate_policy(policy, {f['ref'] for f in capture['footprints']})
    try:
        outline = _qualified(capture)
    except ValueError as exc:
        return _failure(capture, policy, 'unsupported_geometry', 'unknown', 0, str(exc))
    proof = _area_impossibility(capture, outline)
    if proof:
        failure = _failure(capture, policy, 'packing_infeasible', 'defect', 0,
                           f"{proof['side']} courtyard area exceeds board area")
        return {**failure, 'proof': proof}
    try:
        fixed, placed, reserved = _initial_reservations(capture, policy, outline)
    except ValueError as exc:
        return _failure(capture, policy, 'fixed_conflict', 'unknown', 0, str(exc))
    priorities = {ref: i for i, ref in enumerate(policy['priority_refs'])}
    queue = sorted((fp for fp in capture['footprints'] if fp['ref'] not in fixed),
                   key=lambda f: (priorities.get(f['ref'], len(priorities)), -len(f['pads']), f['ref']))
    steps = 0
    for fp in queue:
        best = None
        for rotation in (0, 90, 180, 270):
            for position in _candidate_positions(fp, rotation, outline, reserved, policy['gap_nm']):
                if steps == policy['step_budget']:
                    return _failure(capture, policy, 'packing_budget_exhausted', 'unknown', steps,
                                    'deterministic candidate budget exhausted')
                steps += 1
                proposed = transform(fp, position, rotation)
                if _blocked(proposed, reserved, policy['gap_nm']) or not _fits(proposed, outline):
                    continue
                key = (_wire_cost(proposed, placed, policy['ground_nets']), *position, rotation)
                if best is None or key < best[0]:
                    best = key, proposed
        if best is None:
            return _failure(capture, policy, 'packing_search_exhausted', 'unknown', steps,
                            'no candidate in the finite greedy search; infeasibility unproved')
        placed.append(best[1])
        reserved.extend(boxes(best[1]).items())
    proposed_capture = {**capture, 'footprints': placed}
    judgment = judge(proposed_capture)
    judgment['basis'] = 'transformed-proposal-not-native'
    if judgment['verdict'] != 'pass':
        return _failure(capture, policy, 'judge_rejected', 'unknown', steps,
                        'independent full-polygon judge rejected complete proposal')
    return {'schema': 'agent-ee.packing-plan/1', 'board_sha256': capture['board_sha256'],
            'status': 'planned', 'outcome': 'info', 'authority': 'standalone-unsealed-plan', 'steps_used': steps, 'step_budget': policy['step_budget'],
            'policy': copy.deepcopy(policy), 'judgment': judgment,
            'placements': [{'ref': f['ref'], 'uuid': f['uuid'], 'position_nm': f['position_nm'],
                            'rotation_deg': f['rotation_deg'], 'fixed': f['ref'] in fixed}
                           for f in sorted(placed, key=lambda f: f['ref'])]}
