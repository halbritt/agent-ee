"""Original, offline targeted planners; reuse the captured probe and placement judge.

Cluster grids are seeds and BGA escapes are geometric suggestions only. Neither
authorizes whole-board placement or copper writes. A decoupling row can enter
the existing disposable writer only after a strict score improvement.
"""
import copy
import math

from .board_envelope import inside
from .placement import judge, validate_capture
from .placement_groups import grade
from .placer import (_edge_margin_nm, _exact, _fits, _keepout_blocked, _keepouts,
                     _near, _qualified, _validate_policy, pad_shape_qualification, transform)


def _point(value, label, positive=False):
    if (not isinstance(value, list) or len(value) != 2 or
            any(type(n) is not int or (positive and n <= 0) for n in value)):
        raise ValueError(label + ' needs two integer nanometres' + (' > 0' if positive else ''))
    return value


def _refs(value, known):
    if (not isinstance(value, list) or not value or any(not isinstance(r, str) for r in value)
            or len(set(value)) != len(value) or not set(value) <= known):
        raise ValueError('explicit unique known references required')
    return sorted(value)


def _nets(fp, ground):
    return {p['net'] for p in fp['pads'] if p['net'] and p['net'] not in ground}


def _row(parts, spec, scoring):
    anchor = spec['anchor_ref']
    if anchor not in scoring['ic_refs']:
        raise ValueError('row anchor must be a declared IC')
    refs = _refs(spec['capacitor_refs'], set(parts))
    if not set(refs) <= set(scoring['capacitors']):
        raise ValueError('row capacitors must be explicitly declared to the judge')
    offset = _point(spec['offset_nm'], 'offset_nm')
    step = _point(spec['step_nm'], 'step_nm')
    if len(refs) > 1 and step == [0, 0]:
        raise ValueError('row step must separate multiple capacitors')
    origin = parts[anchor]['position_nm']
    targets = {}
    for i, ref in enumerate(refs):
        if not _nets(parts[ref], scoring['ground_nets']) & _nets(parts[anchor], scoring['ground_nets']):
            raise ValueError(f'{ref}: no shared non-ground net with declared anchor')
        targets[ref] = [origin[j] + offset[j] + i*step[j] for j in range(2)]
    return targets, {}


def _clusters(parts, spec, scoring):
    refs = _refs(spec['refs'], set(parts))
    origin = _point(spec['origin_nm'], 'origin_nm')
    spacing = _point(spec['spacing_nm'], 'spacing_nm', positive=True)
    parent = {r: r for r in refs}

    def root(ref):
        while parent[ref] != ref:
            ref = parent[ref]
        return ref

    owners = {}
    for ref in refs:
        for net in sorted(_nets(parts[ref], scoring['ground_nets'])):
            if net in owners:
                a, b = sorted((root(ref), root(owners[net])))
                parent[b] = a
            owners[net] = ref
    groups = {}
    for ref in refs:
        groups.setdefault(root(ref), []).append(ref)
    targets, column = {}, 0
    for members in groups.values():
        width = math.isqrt(len(members)-1)+1
        for i, ref in enumerate(members):
            targets[ref] = [origin[0] + (column+i % width)*spacing[0],
                            origin[1] + (i//width)*spacing[1]]
        column += width+1
    return targets, {'clusters': list(groups.values()), 'seed_only': True}


def _fanout(parts, spec):
    ref = spec['bga_ref']
    if ref not in parts:
        raise ValueError('unknown BGA reference')
    pads = parts[ref]['pads']
    positions = [tuple(_point(p['position_nm'], 'pad position')) for p in pads]
    numbers = [p['number'] for p in pads]
    if (not pads or len(set(positions)) != len(pads) or len(set(numbers)) != len(pads)
            or any(not n for n in numbers) or any(p.get('shape') != 0 or p.get('attribute') != 'SMD' for p in pads)):
        raise ValueError('BGA requires unique numbered circular SMD pads')
    xs, ys = sorted({p[0] for p in positions}), sorted({p[1] for p in positions})
    if len(xs) < 2 or len(ys) < 2 or len(xs)*len(ys) != len(pads):
        raise ValueError('BGA pitch requires a complete axis-aligned pad grid')
    pitches = {b-a for axis in (xs, ys) for a, b in zip(axis, axis[1:])}
    if len(pitches) != 1:
        raise ValueError('BGA pitch is not uniform and square')
    pitch = pitches.pop()
    offset = spec['escape_offset_nm']
    if type(offset) is not int or not 0 < offset < pitch//2:
        raise ValueError('escape offset must be positive and less than half pitch')
    escapes = []
    for pad in sorted(pads, key=lambda p: p['number']):
        x, y = pad['position_nm']
        escapes.append({'ref': ref, 'pad': pad['number'], 'net': pad['net'],
                        'position_nm': [x + (offset if 2*x >= xs[0]+xs[-1] else -offset),
                                        y + (offset if 2*y >= ys[0]+ys[-1] else -offset)]})
    return {}, {'pitch_nm': pitch, 'escape_targets': escapes, 'plan_only': True}


def plan(capture, policy, spec):
    """Borrow inputs; return a deterministic unsealed plan, without I/O or mutation.

    Policy is the existing packing policy. Spec adds mode and the existing judge
    policy; malformed declarations raise ValueError. Missing geometry blocks as
    unknown, preserving the judge's determinate defects verbatim.
    """
    validate_capture(capture)
    parts = {f['ref']: f for f in capture['footprints']}
    _validate_policy(policy, set(parts))
    if not isinstance(spec, dict) or spec.get('mode') not in ('decoupling_row', 'net_cluster_seed', 'bga_fanout'):
        raise ValueError('expected decoupling_row, net_cluster_seed or bga_fanout mode')
    scoring = copy.deepcopy(spec['judge_policy'])
    if not isinstance(scoring, dict):
        raise ValueError('explicit placement judge policy required')
    before = judge(capture, scoring)
    record = {'schema': 'agent-ee.packing-plan/1', 'mode': spec['mode'],
              'board_sha256': capture['board_sha256'], 'policy': copy.deepcopy(policy),
              'request': copy.deepcopy(spec), 'judge_policy': copy.deepcopy(scoring),
              'before': before, 'after': None, 'judgment': None, 'placements': [], 'reasons': [],
              'status': 'blocked', 'outcome': 'unknown', 'steps_used': 0,
              'step_budget': policy['step_budget'], 'pad_shapes': pad_shape_qualification(),
              'min_courtyard_edge_mm': _edge_margin_nm(policy)/1_000_000,
              'authority': 'standalone-unsealed-plan'}
    proposed = copy.deepcopy(capture)
    try:
        outline = _qualified(proposed)
        obstacles = _keepouts(proposed) + policy['obstacles']
    except ValueError as exc:
        record['reasons'].append('missing coverage: ' + str(exc))
        return record
    parts = {f['ref']: f for f in proposed['footprints']}
    if spec['mode'] == 'decoupling_row':
        targets, extra = _row(parts, spec, scoring)
    elif spec['mode'] == 'net_cluster_seed':
        targets, extra = _clusters(parts, spec, scoring)
    else:
        targets, extra = _fanout(parts, spec)
    record.update(extra)
    fixed = set(policy['fixed_refs']) | {r for r, f in parts.items() if f.get('locked')}
    reasons = record['reasons']
    for ref, position in targets.items():
        if ref in fixed and position != parts[ref]['position_nm']:
            reasons.append(f'{ref}: held or locked footprint cannot move')
        else:
            parts[ref] = transform(parts[ref], position, parts[ref]['rotation_deg'])
    # Untargeted footprints are obstacles too, never silently moved to make room.
    for ref, fp in sorted(parts.items()):
        if ref in targets:
            if not inside(fp['position_nm'], outline) or not _fits(fp, outline, _edge_margin_nm(policy)):
                reasons.append(f'{ref}: target outside outline or edge margin')
            peers = [p for r, p in parts.items() if r != ref]
            exact = [_exact(side, c['polygons']) for p in peers for side, c in p['courtyards'].items()]
            exact += [_exact(o['side'], o['polygons']) for o in obstacles]
            if _near(fp, exact, policy['gap_nm']) or _keepout_blocked(fp, peers, obstacles):
                reasons.append(f'{ref}: target conflicts with obstacle or gap')
    for escape in record.get('escape_targets', []):
        if not inside(escape['position_nm'], outline):
            reasons.append(f"{escape['ref']} pad {escape['pad']}: escape target outside outline")
    proposed['footprints'] = list(parts.values())
    after = judge(proposed, scoring)
    after['basis'] = 'transformed-proposal-not-native'
    record['after'] = after
    record['judgment'] = after
    if before['unknown'] or after['unknown']:
        reasons.append('missing coverage in placement judge')
    if after['verdict'] != 'pass':
        reasons.append('placement judge rejected proposed geometry')
    if after['score'] <= before['score']:
        reasons.append('placement score must strictly improve')
    if policy.get('groups') and grade(proposed, policy['groups'])['status'] != 'pass':
        reasons.append('declared placement groups not satisfied')
    if spec['mode'] != 'decoupling_row':
        reasons.append('plan only: no autonomous whole-board or copper apply')
    record['placements'] = [{'ref': r, 'uuid': fp['uuid'], 'position_nm': fp['position_nm'],
                             'rotation_deg': fp['rotation_deg'], 'fixed': r in fixed or r not in targets}
                            for r, fp in sorted(parts.items())]
    if not reasons:
        record.update(status='planned', outcome='info')
    return record
