"""Bounded same-face member refinement inside a selected block's original window.

The immutable candidate remains the starting layout. Every accepted local change
has a separate witness; this is neither flat packing nor reference-layout reuse.
"""
from copy import deepcopy
from fractions import Fraction
import math

from . import block_contract, electrical_locality, placement_groups, placer
from .footprint_library import digest
from .placement import box, courtyard_bounds, judge

SCHEMA = 'agent-ee.block-member-refinement/1'
CAPTURE_DIGEST_BASIS = 'public-capture-integral-angles/1'


def capture_digest(capture):
    """Seal public facts, excluding private caches and integral angle spelling.

    Every public field remains present, including raw owned areas and derived
    public geometry. Only finite, native-range quarter-turn footprint/pad angles
    share an integer spelling. Missing and nonintegral angles remain distinct.
    """
    public = deepcopy(capture)
    for fp in public.get('footprints', []):
        for key in [key for key in fp if key.startswith('_packing_')]:
            del fp[key]
        for item in [fp, *fp.get('pads', [])]:
            angle = item.get('rotation_deg')
            if (type(angle) is float and math.isfinite(angle) and angle.is_integer()
                    and angle % 90 == 0 and -(2**63) <= angle < 2**63):
                item['rotation_deg'] = int(angle)
    return digest(public)


def _bbox(fp):
    return box([p for c in fp['courtyards'].values() for p in courtyard_bounds(c)[0]])


def _point(fp, pad, all_fps):
    endpoint = {'reference': fp['ref'], 'pad': pad['number'], 'net': pad['net']}
    if pad.get('uuid'):
        endpoint['uuid'] = pad['uuid']
    return electrical_locality.resolve(all_fps, endpoint)


def _wire(capture, refs, grounds):
    nets = {}
    for fp in capture['footprints']:
        if fp['ref'] not in refs:
            continue
        for pad in fp['pads']:
            if pad.get('net') and pad['net'] not in grounds:
                nets.setdefault(pad['net'], []).append((fp['ref'], _point(fp, pad, capture['footprints'])))
    return sum((sum(max(p[axis] for _, p in points)-min(p[axis] for _, p in points) for axis in (0, 1))
                for points in nets.values() if len({ref for ref, _ in points}) > 1), Fraction(0))


def _metrics(capture, policy, contract, record, refs):
    board = {fp['ref']: fp for fp in capture['footprints']}
    pairs = []
    for obligation in contract['relationships']['obligations']:
        for role in electrical_locality.roles(obligation):
            pair = obligation[role]
            a, b = (electrical_locality.resolve(capture['footprints'], pair[k]) for k in ('from', 'to'))
            cross = board[pair['from']['reference']]['side'] != board[pair['to']['reference']]['side']
            dz = contract['thickness_nm'] if cross else 0
            pairs.append(sum((x-y)**2 for x, y in zip(a, b)) + dz**2)
    sources = record['arrangement'].get('thermal', {}).get('sources', [])
    thermal = [sum((x-y)**2 for x, y in zip(board[a]['position_nm'], board[b]['position_nm']))
               for i, a in enumerate(sources) for b in sources[i+1:]]
    grounds = set(policy['ground_nets'])
    return {'internal_hpwl_nm': _wire(capture, refs, grounds),
            'whole_board_hpwl_nm': _wire(capture, set(board), grounds),
            'declared_pair_distance_squared_nm2': pairs,
            'thermal_minimum_distance_squared_nm2': min(thermal) if thermal else None}


def _record_metrics(metrics):
    return {key: ([block_contract._number(n) for n in value] if isinstance(value, list)
                  else None if value is None else block_contract._number(value)) for key, value in metrics.items()}


def _improves(before, after):
    return (after['internal_hpwl_nm'] < before['internal_hpwl_nm']
            and after['whole_board_hpwl_nm'] <= before['whole_board_hpwl_nm']
            and all(b <= a for a, b in zip(before['declared_pair_distance_squared_nm2'], after['declared_pair_distance_squared_nm2']))
            and (before['thermal_minimum_distance_squared_nm2'] is None
                 or after['thermal_minimum_distance_squared_nm2'] >= before['thermal_minimum_distance_squared_nm2']))


def _move(capture, reference, position):
    proposed = deepcopy(capture)
    source = next(fp for fp in capture['footprints'] if fp['ref'] == reference)
    proposed['footprints'] = [placer.transform(fp, position, fp['rotation_deg']) if fp['ref'] == reference else fp
                              for fp in proposed['footprints']]
    for area in proposed.get('rule_areas', []):
        if area.get('owner') == source['uuid']:
            area['outline'] = placer.transform({'position_nm': source['position_nm'],
                'rotation_deg': source['rotation_deg'], 'pads': [], 'courtyards': {'zone': area['outline']}},
                position, source['rotation_deg'])['courtyards']['zone']
    return proposed


def _admitted(proposed, reference, policy, contract, record):
    """Full final judges plus the moved member's exact board/peer admission gates."""
    from .block_arrangement import grade_rules
    work = deepcopy(proposed)
    outline = placer._qualified(work)
    pad_keepouts = []
    keepouts = placer._keepouts(work, pads=pad_keepouts)
    fp = next(fp for fp in work['footprints'] if fp['ref'] == reference)
    peers = [other for other in work['footprints'] if other['ref'] != reference]
    obstacles = [placer._exact(o['side'], o['polygons']) for o in policy['obstacles']]
    exact = obstacles + [placer._exact(side, c['polygons']) for peer in peers for side, c in peer['courtyards'].items()]
    if (not placer._fits(fp, outline, placer._edge_margin_nm(policy))
            or not placer.access_direction.admits(fp, outline)
            or placer._near(fp, exact, policy['gap_nm'])
            or placer._in_keepouts(fp, keepouts)
            or any(placer._hole_conflict(fp, peer) or placer._in_keepouts(fp, peer.get('packing_keepouts', []))
                   or placer._in_keepouts(peer, fp.get('packing_keepouts', [])) for peer in peers)
            or placer._copper_escapes(fp, outline, keepouts, peers, pad_keepouts) is not None):
        return False
    if judge(proposed, overhangs=policy.get('fixed_overhangs'))['verdict'] != 'pass':
        return False
    if policy.get('groups') and not placement_groups.satisfied(placement_groups.grade(proposed, policy['groups'])):
        return False
    if policy.get('electrical_locality') and electrical_locality.grade(proposed, policy['electrical_locality'])['status'] not in ('pass', 'info'):
        return False
    owner = {ref: block['block'] for block in record['blocks'] for ref in block['references']}
    if any(row['enforcement'] == 'hard' and row['status'] != 'pass'
           for row in grade_rules(proposed, record['arrangement'], owner, placer._fixed_refs(proposed, policy))):
        return False
    return block_contract.check_complete(proposed, contract)['status'] == 'pass'


def _targets(fp, peers, gap):
    x, y = fp['position_nm']
    a = _bbox(fp)
    targets = set()
    for peer in peers:
        b = _bbox(peer)
        xs = {x, peer['position_nm'][0], x+b[0]-a[2]-gap, x+b[2]-a[0]+gap}
        ys = {y, peer['position_nm'][1], y+b[1]-a[3]-gap, y+b[3]-a[1]+gap}
        targets.update((px, py) for px in xs for py in ys)
    targets.discard((x, y))
    return sorted(targets)


def refine(capture, policy, contract, block_records, *, step_budget):
    if type(step_budget) is not int or step_budget < 0:
        raise ValueError('refinement step_budget must be a nonnegative integer')
    if block_contract.check_complete(capture, contract)['status'] != 'pass':
        raise ValueError('member refinement requires a complete passing block contract')
    result = deepcopy(capture)
    fixed = placer._fixed_refs(capture, policy)
    witness = {'schema': SCHEMA, 'capture_digest_basis': CAPTURE_DIGEST_BASIS, 'source_capture_sha256': capture_digest(capture),
               'policy_sha256': digest(policy), 'contract_sha256': digest(contract),
               'step_budget': step_budget, 'steps_used': 0, 'moves': [], 'rejected': {}, 'skipped': []}
    exhausted = False
    for block in sorted(block_records['blocks'], key=lambda b: blocks_key(b['block'])):
        bid, refs, anchor = block['block'], set(block['references']), block['anchor']
        if block['provenance']['class'] != 'search-derived':
            witness['skipped'].append({'block': bid, 'reason': 'reference_layout_immutable'})
            continue
        originals = [fp for fp in capture['footprints'] if fp['ref'] in refs]
        window = box([p for fp in originals for c in fp['courtyards'].values() for p in courtyard_bounds(c)[0]])
        for reference in sorted(refs - fixed - {anchor}, key=blocks_key):
            fp = next(fp for fp in result['footprints'] if fp['ref'] == reference)
            peers = [peer for peer in result['footprints'] if peer['ref'] in refs and peer['ref'] != reference]
            before = _metrics(result, policy, contract, block_records, refs)
            for position in _targets(fp, peers, policy['gap_nm']):
                if witness['steps_used'] >= step_budget:
                    exhausted = True
                    break
                witness['steps_used'] += 1
                proposed = _move(result, reference, list(position))
                candidate = next(fp for fp in proposed['footprints'] if fp['ref'] == reference)
                bounds = _bbox(candidate)
                reason = None
                if not (window[0] <= bounds[0] <= bounds[2] <= window[2] and window[1] <= bounds[1] <= bounds[3] <= window[3]):
                    reason = 'outside_block_window'
                else:
                    after = _metrics(proposed, policy, contract, block_records, refs)
                    if not _improves(before, after):
                        reason = 'objective_not_improved'
                    elif not _admitted(proposed, reference, policy, contract, block_records):
                        reason = 'constraint_rejected'
                if reason:
                    witness['rejected'][reason] = witness['rejected'].get(reason, 0) + 1
                    continue
                witness['moves'].append({'block': bid, 'reference': reference, 'starting_candidate_sha256': block['candidate_sha256'],
                    'before_position_nm': fp['position_nm'], 'after_position_nm': list(position),
                    'side': fp['side'], 'rotation_deg': fp['rotation_deg'], 'block_window_nm': list(window),
                    'before_capture_sha256': capture_digest(result), 'after_capture_sha256': capture_digest(proposed),
                    'before': _record_metrics(before), 'after': _record_metrics(after)})
                result, before, fp = proposed, after, candidate
            if exhausted:
                break
        if exhausted:
            break
    witness.update(status='refined' if witness['moves'] else 'unchanged',
                   termination='budget_exhausted' if exhausted else 'complete',
                   final_capture_sha256=capture_digest(result),
                   final_member_poses=[{'reference': fp['ref'], 'position_nm': fp['position_nm'],
                                        'rotation_deg': fp['rotation_deg'], 'side': fp['side']}
                                       for fp in sorted(result['footprints'], key=lambda fp: blocks_key(fp['ref']))
                                       if any(fp['ref'] in b['references'] for b in block_records['blocks'])])
    return result, witness


def blocks_key(value):
    from .blocks import _natural
    return _natural(value)
