"""Two-level placement, stage B (AGENTEE-340): rigid blocks arranged as macros, then residual packing.

Every derived block needs one admitted ``agent-ee.block-candidate/2`` (search-derived
or a cited library layout alike). Each block moves as one rigid macro: four quarter
turns, its captured side, the board outline, edge margin, fixed parts, obstacles,
rule areas and drilled-pad reservations of the unchanged placer. Block-level rules
(declared keep-outs, opposite-side shadows, no block over a fixed part, a thermal
spread limit) are hard only with a citation; unsourced ones are advisory and are
graded and reported, never used to prune. The search is a deterministic ranked first
fit with conflict-directed backjumping over the blocks, with typed exhaustion. The
residual parts are then packed by ``placer.pack`` with every block member fixed, and
the complete plan is graded as one board. See docs/blocks.md.
"""
import argparse
import bisect
import collections
import copy
import hashlib
import json
import math
import sys
import time
from pathlib import Path

from . import advisory_regions, blocks, electrical_locality, placement_groups, placer
from .catalog import strict_json
from .footprint_library import digest
from .native_netlist import read_native_inventory, read_sheet_names
from .placement import convex, cross, edges, intersection_area, overhang_bands, qualified_polygons, simple_polygon
from .placement_predicates import point_segment2

POLICY = 'agent-ee.block-arrangement/1'
STRATEGY = 'ranked-first-fit-conflict-backjump/1'
# Blocked-rectangle cache entries kept across backjumps (search memory, not a design limit).
OWNED_CACHE = 4096
TERMS = ('pad-locality', 'inter-block-wire', 'thermal-spread')
PROVENANCE_ARTIFACT = 'agent-ee.block-provenance/1'
PROVENANCE_AUTHORITY = ('review input only: each placed block keeps its candidate provenance class and enforcement; '
                        'an advisory block confers no acceptance and is reviewed as a proposal')
DEFAULT_OBJECTIVE = {'terms': list(TERMS), 'provenance': {
    'enforcement': 'advisory', 'reason': 'default lexicographic order; no sourced objective weights'}}
POLICY_KEYS = frozenset(('schema', 'step_budget', 'search_timeout_s', 'priority_blocks', 'objective', 'thermal',
                         'keepouts', 'no_stacking', 'no_block_over_fixed'))


def _provenance(value, label):
    """``hard`` needs a citation; anything unsourced is ``advisory`` (AGENTEE-331/337)."""
    blocks._keys(value, {'enforcement'}, {'citation', 'reason'}, label=label + ' provenance')
    for key in ('citation', 'reason'):
        if key in value:
            blocks._text(value[key], f'{label} {key}')
    if value['enforcement'] == 'hard' and 'citation' not in value:
        raise ValueError(f'{label}: a hard rule needs a citation; an unsourced rule is advisory')
    if value['enforcement'] not in ('hard', 'advisory'):
        raise ValueError(f'{label}: enforcement must be hard or advisory')
    return value['enforcement']


def _refs(value, label, known):
    refs = blocks._texts(value, label)
    unknown = sorted(set(refs) - known)
    if unknown:
        raise ValueError(f"{label}: unknown references {', '.join(unknown)}")
    return refs


def _derived_blocks(ids, block_ids, label):
    """Refuse ids that are not derived blocks, naming them and the derived ids."""
    unknown = sorted(set(ids) - set(block_ids), key=blocks._natural)
    if unknown:
        derived = sorted(set(block_ids), key=blocks._natural)
        raise ValueError(f"{label} must be derived block ids: unknown {', '.join(map(repr, unknown))}; "
                         f"derived {', '.join(map(repr, derived)) or 'none'}")


def validate_policy(document, block_ids, refs):
    """The arrangement policy; raises ValueError. Returns it with defaults filled in."""
    if not isinstance(document, dict) or document.get('schema') != POLICY:
        raise ValueError(f'expected {POLICY}')
    blocks._keys(document, {'schema', 'step_budget'}, POLICY_KEYS - {'schema', 'step_budget'},
                 label='arrangement policy')
    if type(document['step_budget']) is not int or not 1 <= document['step_budget'] <= 1_000_000:
        raise ValueError('arrangement step budget must be 1..1000000')
    timeout = document.get('search_timeout_s')
    if 'search_timeout_s' in document and (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                                           or not 0 < timeout <= 86400):
        raise ValueError('search_timeout_s must be positive and at most 86400 seconds')
    priority = blocks._texts(document.get('priority_blocks', []), 'priority blocks')
    _derived_blocks(priority, block_ids, 'priority blocks')
    objective = document.get('objective', DEFAULT_OBJECTIVE)
    blocks._keys(objective, {'terms', 'provenance'}, label='objective')
    terms = blocks._texts(objective['terms'], 'objective terms')
    if not terms or set(terms) - set(TERMS):
        raise ValueError('objective terms must be a nonempty order of ' + ', '.join(TERMS))
    _provenance(objective['provenance'], 'objective')
    if 'thermal' in document:
        thermal = blocks._keys(document['thermal'], {'sources', 'provenance'}, {'min_distance_nm'}, label='thermal')
        if len(_refs(thermal['sources'], 'thermal sources', refs)) < 2:
            raise ValueError('thermal spread needs at least two sources')
        if 'min_distance_nm' in thermal and (type(thermal['min_distance_nm']) is not int
                                             or thermal['min_distance_nm'] <= 0):
            raise ValueError('thermal min_distance_nm must be a positive integer')
        _provenance(thermal['provenance'], 'thermal')
    ids = set()
    for keepout in document.get('keepouts', []):
        blocks._keys(keepout, {'id', 'side', 'polygons', 'provenance'}, label='keep-out')
        blocks._text(keepout['id'], 'keep-out id')
        if keepout['id'] in ids:
            raise ValueError(f"duplicate keep-out id {keepout['id']}")
        ids.add(keepout['id'])
        if keepout['side'] not in ('F', 'B'):
            raise ValueError('keep-out side must be F or B')
        qualified_polygons({'status': 'proven', 'polygons': keepout['polygons']})
        _provenance(keepout['provenance'], 'keep-out ' + keepout['id'])
    if 'no_stacking' in document:
        rule = blocks._keys(document['no_stacking'], {'references', 'margin_nm', 'provenance'}, label='no_stacking')
        if not _refs(rule['references'], 'no_stacking references', refs):
            raise ValueError('no_stacking needs references')
        if type(rule['margin_nm']) is not int or rule['margin_nm'] < 0:
            raise ValueError('no_stacking margin_nm must be a nonnegative integer')
        _provenance(rule['provenance'], 'no_stacking')
    if 'no_block_over_fixed' in document:
        rule = blocks._keys(document['no_block_over_fixed'], {'exempt_blocks', 'provenance'},
                            label='no_block_over_fixed')
        _derived_blocks(blocks._texts(rule['exempt_blocks'], 'exempt blocks'), block_ids, 'exempt blocks')
        _provenance(rule['provenance'], 'no_block_over_fixed')
    return {**document, 'objective': objective, 'priority_blocks': priority}


class EffectivePolicyError(ValueError):
    """The effective packing policy discards or relaxes a captured constraint."""

    def __init__(self, problems):
        super().__init__('the effective policy may only add board obstacles to the captured policy: '
                         + ', '.join(p['key'] for p in problems))
        self.problems = problems


def check_effective(policy, effective):
    """Refuse an effective policy that is not the captured one plus appended board obstacles.

    Candidates are packed and admitted against ``policy``; arrangement and the residual
    pass run on ``effective``. Every key must be equal except ``obstacles``, whose
    captured entries must remain, unchanged and in order, as its prefix.
    """
    problems = [{'key': key, 'kind': 'changed' if key in policy and key in effective else
                 'removed' if key in policy else 'added'}
                for key in sorted(set(policy) | set(effective)) if key != 'obstacles'
                and (key not in policy or key not in effective or policy[key] != effective[key])]
    captured, obstacles = policy.get('obstacles'), effective.get('obstacles')
    if not isinstance(obstacles, list) or not isinstance(captured, list) or obstacles[:len(captured)] != captured:
        problems.append({'key': 'obstacles', 'kind': 'captured_obstacles_not_kept'})
    if problems:
        raise EffectivePolicyError(problems)


def _hard(rule):
    return rule is not None and rule['provenance']['enforcement'] == 'hard'


def _obstacle(rule, shape, source):
    """A hard rule's stationary obstacle, carrying the rule's cited source to the placer.

    Capacity proofs that rest on it are then defects of a sourced rule, not unknowns
    about unsourced geometry (AGENTEE-365). Only hard rules, which need a citation,
    become obstacles.
    """
    return {'side': shape['side'], 'polygons': shape['polygons'], 'source': source,
            'provenance': {'class': 'arrangement_rule', 'enforcement': 'hard',
                           'citation': rule['provenance']['citation']}}


def bind_boundary(document, capture, mapping=None, declared=None):
    """Each candidate net role bound to the board net its pins land on.

    A boundary role must reach a footprint outside the block. On the candidate's own
    board the recorded net name must be the board net; a mapped (library) candidate
    must declare the target net of every boundary role in ``declared``. Returns
    ``{'roles': [...], 'problems': [...]}``.
    """
    rows = {r['reference']: r for r in document['members']}
    mapping = {r: r for r in rows} if mapping is None else mapping
    identity = all(k == v for k, v in mapping.items())
    board = {fp['ref']: fp for fp in capture['footprints']}
    members = set(mapping.values())
    outside = {}
    for fp in capture['footprints']:
        if fp['ref'] not in members:
            for pad in fp['pads']:
                if pad.get('net'):
                    outside.setdefault(pad['net'], set()).add(fp['ref'])
    roles, problems = {}, []
    for ref in sorted(rows, key=blocks._natural):
        nets = blocks._pad_nets(board[mapping[ref]])
        for pin in rows[ref]['pins']:
            if pin['role'] is None:
                continue
            net = nets.get(pin['pad'], '')
            record = roles.setdefault(pin['role'], {'role': pin['role'], 'net': net, 'boundary': pin['boundary'],
                                                    'recorded_net': pin['net']})
            if record['net'] != net:
                problems.append({'kind': 'net_role_split', 'role': pin['role'], 'nets': sorted({record['net'], net})})
    declared = {} if declared is None else declared
    if not isinstance(declared, dict) or any(not isinstance(v, str) for v in declared.values()):
        raise ValueError('boundary_nets must map roles to net names')
    unknown = sorted(set(declared) - {r for r, v in roles.items() if v['boundary']})
    if unknown:
        problems.append({'kind': 'boundary_unknown_role', 'roles': unknown})
    out = []
    for role in sorted(roles, key=lambda r: int(r[1:])):
        record = roles[role]
        reached = sorted(outside.get(record['net'], ()), key=blocks._natural)
        if record['boundary'] != bool(reached):
            problems.append({'kind': 'boundary_flag', 'role': role, 'net': record['net'], 'outside': reached})
        if record['boundary']:
            expected = declared.get(role, record['recorded_net'] if identity else None)
            if expected is None:
                problems.append({'kind': 'boundary_undeclared', 'role': role, 'net': record['net']})
            elif expected != record['net']:
                problems.append({'kind': 'boundary_net_mismatch', 'role': role, 'expected': expected,
                                 'actual': record['net']})
        out.append({'role': role, 'net': record['net'], 'boundary': record['boundary'], 'outside': reached})
    return {'roles': out, 'problems': problems}


def admit_entry(entry, capture, inventory, derivation, policy):
    """Admit one arrangement input: ``{'candidate', optional 'mapping', 'boundary_nets'}``.

    Without a mapping this is ``blocks.admit`` on the candidate's own board. With one
    (a library or reused layout), identity is ``blocks.match`` with the mapping, the
    mapped members must be one derived block with the same anchor, and the frame
    poses are re-graded with the target board's own footprints and isolated policy;
    its deferred obligations are the target board's (``blocks._isolate``). Either way every boundary role is bound to its board net.
    """
    blocks._keys(entry, {'candidate'}, {'mapping', 'boundary_nets'}, label='arrangement candidate')
    document = entry['candidate']
    mapping = entry.get('mapping')
    if mapping is None:
        admission = blocks.admit(document, capture, inventory, derivation, policy)
        if admission['status'] != 'admitted':
            return None, admission['problems']
        block = next(b for b in derivation['blocks'] if b['id'] == document['block_id'])
    else:
        result = blocks.match(document, capture, inventory, mapping)
        if result['status'] != 'match':
            return None, [{'kind': 'identity', 'mismatches': result['mismatches']}]
        found = [b for b in derivation['blocks'] if set(b['references']) == set(mapping.values())]
        if len(found) != 1:
            return None, [{'kind': 'block_members', 'actual': sorted(mapping.values(), key=blocks._natural)}]
        block = found[0]
        # The target board's own isolated policy and deferred record; the source's never apply.
        anchor, sub, deferred, _ = blocks._isolate(capture, policy, block)
        if mapping[document['frame']['anchor']] != anchor:
            return None, [{'kind': 'frame_anchor', 'expected': anchor,
                           'actual': mapping[document['frame']['anchor']]}]
        board = {fp['ref']: fp for fp in capture['footprints']}
        framed = [blocks.frame_member(board[mapping[r['reference']]], r['pose']['position_nm'], r['pose']['rotation_deg'])
                  for r in sorted(document['members'], key=lambda r: blocks._natural(mapping[r['reference']]))]
        _, grade, witness = blocks._evidence(capture, framed, sub)
        failed = [k for k, g in grade.items() if g and not blocks._grade_usable(k, g)] + (
            ['witness'] if witness['status'] != 'pass' else [])
        if failed:
            return None, [{'kind': 'evidence_failed', 'evidence': failed}]
    binding = bind_boundary(document, capture, mapping, entry.get('boundary_nets'))
    if binding['problems']:
        return None, binding['problems']
    if mapping is None:
        # ``blocks.admit`` refused any deferred record other than the recomputed one.
        mapping, deferred = {r['reference']: r['reference'] for r in document['members']}, document['deferred']
    poses = {mapping[r['reference']]: r['pose'] for r in document['members']}
    return {'block': block['id'], 'anchor': mapping[document['frame']['anchor']], 'poses': poses,
            'mapping': mapping, 'mapped': 'mapping' in entry, 'binding': binding['roles'],
            'candidate_sha256': digest(document), 'provenance': document['provenance'], 'deferred': deferred}, []


def _opposite(side):
    return 'B' if side == 'F' else 'F'


def _shadow(fp, margin):
    """The mounting-side courtyard box, grown by the margin, on the opposite side."""
    x0, y0, x1, y1 = placer.boxes(fp)[fp['side']]
    x0, y0, x1, y1 = x0 - margin, y0 - margin, x1 + margin, y1 + margin
    return {'side': _opposite(fp['side']), 'polygons': [[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]]}


def _overlaps(fp, entries):
    """Positive-area overlap of a courtyard with an exact entry on the same side.

    Probe polygons keep their native winding (a back-side courtyard may be
    clockwise); ``intersection_area`` needs them counter-clockwise.
    """
    for side, courtyard in fp['courtyards'].items():
        for entry in entries:
            if entry['side'] == side and any(intersection_area(simple_polygon(c), simple_polygon(p)) > 0
                                             for c in courtyard['polygons'] for p in entry['polygons']):
                return True
    return False


def _through(fp, entries):
    """Positive-area overlap of a mounting-side courtyard with entries on the opposite side."""
    side = fp['side']
    return _overlaps({'courtyards': {side: fp['courtyards'][side]}},
                     [dict(e, side=side) for e in entries if e['side'] == _opposite(side)])


def _strip(fp):
    return {k: v for k, v in fp.items() if not k.startswith('_')}


def _macro(block_id, members):
    """One rigid footprint-like object from the members at their frame poses."""
    courtyards, keepouts, copper, holes, geometry = {}, [], [], [], False
    for fp in members:
        for side, courtyard in fp['courtyards'].items():
            courtyards.setdefault(side, {'status': 'proven', 'polygons': []})['polygons'].extend(
                copy.deepcopy(courtyard['polygons']))
        keepouts += copy.deepcopy(fp.get('packing_keepouts', []))
        if 'packing_pad_geometry' in fp:
            geometry = True
            copper += copy.deepcopy(fp['packing_pad_geometry']['copper'])
            holes += copy.deepcopy(fp['packing_pad_geometry']['holes'])
    macro = {'ref': block_id, 'side': members[0]['side'], 'position_nm': [0, 0], 'rotation_deg': 0,
             'courtyards': courtyards, 'packing_keepouts': keepouts, 'pads': []}
    if geometry:
        macro['packing_pad_geometry'] = {'status': 'proven', 'copper': copper, 'holes': holes}
    return placer.transform(macro, [0, 0], 0)


def _shift(fp, dx, dy):
    """A rotated-at-origin macro or member translated rigidly, with its reservation caches.

    The same coordinates as ``placer.transform`` at the translated origin, without
    copying pads or pad geometry; the search only reads courtyards, keep-outs and caches.
    """
    def move(poly):
        return [[x + dx, y + dy] for x, y in poly]

    def boxed(entries):
        return [((x0 + dx, y0 + dy, x1 + dx, y1 + dy), move(poly)) for (x0, y0, x1, y1), poly in entries]
    out = {'ref': fp['ref'], 'side': fp['side'], 'rotation_deg': fp['rotation_deg'],
           'position_nm': [fp['position_nm'][0] + dx, fp['position_nm'][1] + dy],
           'courtyards': {side: {'status': 'proven', 'polygons': [move(poly) for poly in c['polygons']]}
                          for side, c in fp['courtyards'].items()},
           'packing_keepouts': [{'side': k['side'], 'polygons': [move(poly) for poly in k['polygons']]}
                                for k in fp.get('packing_keepouts', [])], 'pads': []}
    if 'packing_pad_keepouts' in fp:
        out['packing_pad_keepouts'] = [{**k, 'polygons': [move(poly) for poly in k['polygons']]}
                                       for k in fp['packing_pad_keepouts']]
    if 'access_direction' in fp:
        out['access_direction'] = fp['access_direction']
    for key in ('_packing_holes', '_packing_targets'):
        if key in fp:
            out[key] = boxed(fp[key])
        # placer._hole_conflict reads the union boxes too (AGENTEE-353); a stale or missing one is wrong.
        if key + '_box' in fp:
            box = fp[key + '_box']
            out[key + '_box'] = None if box is None else (box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy)
    if '_packing_copper' in fp:
        out['_packing_copper'] = placer._shift_copper(fp['_packing_copper'], dx, dy)
    if '_packing_lands' in fp:
        out['_packing_lands'] = placer._shift_lands(fp['_packing_lands'], dx, dy)
    if '_packing_copper_setback' in fp:
        out['_packing_copper_setback'] = fp['_packing_copper_setback']
    return out


def _copper_axes(members, outline, bbox, window):
    """Stage-B origins that put member copper outside its courtyards the setback S from an outline vertex.

    Member copper aligns to the outline as a courtyard does, held off it by the
    structural setback (AGENTEE-366e); a block without such copper adds none.
    Only origins keeping the block box in the necessary window are kept.
    """
    bx0, by0, bx1, by1 = bbox
    lo_x, lo_y, hi_x, hi_y = window
    copper = [(fp['_packing_copper_setback'], placer._box([poly])) for fp in members
              for poly in fp.get('_packing_copper', {}).get('outline', ())]
    xs = {x for s, (c0, _, c1, _) in copper for p in outline
          for x in (p[0] + s - c0, p[0] - s - c1) if lo_x <= bx0 + x and bx1 + x <= hi_x}
    ys = {y for s, (_, c0, _, c1) in copper for p in outline
          for y in (p[1] + s - c0, p[1] - s - c1) if lo_y <= by0 + y and by1 + y <= hi_y}
    return xs, ys


def _reach(fp):
    """Box of every drilled-pad reservation and target; disjoint reaches cannot conflict."""
    entries = fp.get('_packing_holes', []) + fp.get('_packing_targets', [])
    if not entries:
        return None
    return (min(b[0] for b, _ in entries), min(b[1] for b, _ in entries),
            max(b[2] for b, _ in entries), max(b[3] for b, _ in entries))


def _apart(a, b):
    return a is None or b is None or a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]


def _inscribed(polygon):
    """An axis-aligned square inside a convex polygon, or None.

    About an integer interior point at least r from every edge, a square of half
    side h with 2h^2 < r^2 lies in the disc of radius r, so inside the polygon:
    any positive-area overlap with the square is one with the polygon.
    """
    points = [tuple(p) for p in polygon]
    if not (convex(points) or convex(points[::-1])):
        return None
    cx, cy = sum(p[0] for p in points) // len(points), sum(p[1] for p in points) // len(points)
    turns = {cross(a, b, (cx, cy)) > 0 for a, b in edges(points)}
    if len(turns) != 1 or any(cross(a, b, (cx, cy)) == 0 for a, b in edges(points)):
        return None
    r2 = min(point_segment2((cx, cy), a, b) for a, b in edges(points))
    h = math.isqrt(math.floor(r2 / 2)) - 1
    return None if h <= 0 else (cx - h, cy - h, cx + h, cy + h)


def _gapped(cand, placed, gap, owner):
    """Open origin rectangles where a candidate box comes within the gap of a placed box.

    ``cand`` and ``placed`` hold ``(side, box)``; side ``'*'`` meets both sides. A
    rectangle is ``(x_lo, x_hi, y_lo, y_hi, owner, None)``: an origin strictly inside it
    on both axes is blocked, and its owner (if any) is a conflict.
    """
    out = []
    for side, (x0, y0, x1, y1) in cand:
        for other, (a0, b0, a1, b1) in placed:
            if side == other or '*' in (side, other):
                out.append((a0 - gap - x1, a1 + gap - x0, b0 - gap - y1, b1 + gap - y0, owner, None))
    return out


def _solid(slabs, solid, gap):
    """Rectilinear member slabs against fixed courtyard and obstacle slabs.

    An overlap blocks the origin (no gap); nearer than the gap dooms it, since the exact
    obstacle test would reject it. A doom rectangle carries ``(c0, c1)`` with
    ``dx = max(0, c0 - x, x - c1)``, valid for ``dx < gap``.
    """
    out = []
    for side, (r0, s0, r1, s1) in slabs:
        for other, (a0, b0, a1, b1) in solid:
            if side != other:
                continue
            out.append((a0 - r1, a1 - r0, b0 - s1, b1 - s0, None, None))
            if gap:
                out.append((a0 - r1 - gap, a1 - r0 + gap, b0 - s1, b1 - s0, None, (a0 - r1, a1 - r0)))
    return out


def _scan(entries, xs, ys, gap, conflicts, doomed, checkpoint, contacts=None):
    """Positions on the axes that no blocked rectangle covers, in x then y order.

    With ``contacts = (y_lo, y_hi)`` each x also tries the ends of its blocked y runs
    inside that window: the origins where some member box touches a reserved box or
    obstacle in y (member-aligned axes, AGENTEE-355).
    """
    if not xs or not (ys or contacts):
        return
    # A rectangle wholly outside every candidate y (doom rectangles reach at most the gap) never acts.
    bounds = ([ys[0], ys[-1]] if ys else []) + (list(contacts) if contacts is not None else [])
    low, high = min(bounds), max(bounds)
    entries = sorted((e for e in entries if e[3] + gap >= low and e[2] - gap <= high), key=lambda e: e[0])
    right, left = bisect.bisect_right, bisect.bisect_left
    # Active rectangles: plain ones kept in y_lo order (insertion), doom ones apart.
    plain, dooms, k, n = [], [], 0, len(entries)
    for x in xs:
        checkpoint()
        while k < n and entries[k][0] < x:
            if entries[k][5] is None:
                bisect.insort(plain, entries[k], key=lambda e: e[2])
            else:
                dooms.append(entries[k])
            k += 1
        plain = [e for e in plain if e[1] > x]
        dooms = [e for e in dooms if e[1] > x]
        runs = []
        for _, _, lo, hi, _, _ in plain:
            if runs and lo < runs[-1][1]:
                if hi > runs[-1][1]:
                    runs[-1][1] = hi
            else:
                runs.append([lo, hi])
        here = ys
        if contacts is not None:
            ends = {y for run in runs for y in run if contacts[0] <= y <= contacts[1]}.difference(ys)
            if ends:
                here = sorted(ends.union(ys))
        count = len(here)
        for _, _, lo, hi, owner, _ in plain:
            if owner is not None and owner not in conflicts:
                i = right(here, lo)
                if i < count and here[i] < hi:
                    conflicts.add(owner)
        close = set()
        for e in dooms:
            dx = max(0, e[5][0] - x, x - e[5][1])
            if dx >= gap:
                continue
            reach = math.isqrt(gap * gap - dx * dx - 1)
            close.update(range(left(here, e[2] - reach), right(here, e[3] + reach)))
        start = 0
        for lo, hi in runs + [[math.inf, math.inf]]:
            stop = right(here, lo) if lo != math.inf else count
            for c in range(start, stop):
                if c in close:
                    doomed.add((x, here[c]))
                yield x, here[c]
            if lo != math.inf:
                start = max(start, left(here, hi))


def _sweep(boxes, rectangles, xs, ys, reserved, solid, gap, conflicts, doomed, checkpoint, forbid=(),
           holes=(), targets=(), shadows=(), shaded=()):
    """Positions where every member box clears every reserved member box by the gap.

    ``placer._unreserved`` for a candidate made of many boxes: a reserved box that a
    member box meets in x blocks the open y interval (b0 - gap - y1, b1 + gap - y0),
    and its owner joins ``conflicts``. A rectilinear member slab overlapping a solid
    slab of a fixed courtyard or obstacle blocks the position; one nearer than the
    gap marks it ``doomed`` (the exact obstacle test would reject it). One overlapping a
    ``forbid`` slab of a footprint-forbidding rule area is blocked (no gap applies), and so
    is one where a square inside one of the candidate's drilled-pad reservations overlaps
    a ``targets`` courtyard slab on either side; that slab's owner joins ``conflicts``.
    Opposite-side shadows reserve like members: the candidate's ``shadows`` against the
    reserved member boxes, and placed ``shaded`` boxes against the candidate's members.
    The search itself scans cached rectangles (``_Arrangement.entries``); this is the
    same scan from plain lists.
    """
    entries = []
    for side, box, owner in list(reserved) + list(shaded):
        entries += _gapped(boxes, [(side, box)], gap, owner)
    if shadows:
        for side, box, owner in reserved:
            entries += _gapped(shadows, [(side, box)], gap, owner)
    entries += _solid(rectangles, solid, gap)
    entries += _gapped(rectangles, forbid, 0, None)
    for box, owner in targets:
        entries += _gapped([('*', h) for h in holes], [('*', box)], 0, owner)
    return _scan(entries, xs, ys, gap, conflicts, doomed, checkpoint)


def _frontier(member_boxes):
    """``(side, box)`` member boxes with no other box of their side wholly to the left, and to the right.

    Only these can touch, in x, a reserved box or obstacle with their outer edge.
    """
    left, right = [], []
    for k, (side, box) in enumerate(member_boxes):
        x0, y0, x1, y1 = box
        others = [b for j, (s, b) in enumerate(member_boxes) if j != k and s == side and b[1] < y1 and y0 < b[3]]
        if not any(b[2] <= x0 for b in others):
            left.append((side, box))
        if not any(b[0] >= x1 for b in others):
            right.append((side, box))
    return left, right


class _Stopped(Exception):
    def __init__(self, status, reason):
        super().__init__(reason)
        self.status, self.reason = status, reason


class _Arrangement:
    """Ranked macro candidates over the current partial arrangement."""

    def __init__(self, work, policy, arrangement, admitted, outline, keepouts, fixed, placed, exact, heartbeat,
                 pad_keepouts=()):
        self.policy, self.arrangement, self.heartbeat = policy, arrangement, heartbeat
        self.outline, self.keepouts, self.pad_keepouts = outline, keepouts, list(pad_keepouts)
        self.margin = placer._edge_margin_nm(policy)
        self.gap, self.grounds = policy['gap_nm'], set(policy['ground_nets'])
        self.budget, self.steps = arrangement['step_budget'], 0
        self.current, self.backjumps, self.rejected = None, 0, {}
        self.access_rejections = {}
        self.started = time.monotonic()
        self.deadline = (self.started + arrangement['search_timeout_s'] if 'search_timeout_s' in arrangement
                         else None)
        self.last_heartbeat = self.started
        self.by_ref = {fp['ref']: fp for fp in work['footprints']}
        self.fixed = fixed
        self.fixed_fps = placed
        self.exact = exact
        self.solid = [(o['side'], slab) for o in exact for poly in o['polygons'] for slab in placer._slabs(poly) or ()]
        # Footprint-forbidding rule areas of the board and of fixed parts: exact overlap prefilter.
        self.forbid = [(k['side'], slab) for k in keepouts + [a for fp in placed for a in fp.get('packing_keepouts', [])]
                       for poly in k['polygons'] for slab in placer._slabs(poly) or ()]
        # Fixed courtyard slabs on either side that a candidate's drilled-pad reservation may not overlap.
        self.targets = [('*', slab) for fp in placed for c in fp['courtyards'].values() for poly in c['polygons']
                        for slab in placer._slabs(poly) or ()]
        # A fixed drilled-pad reservation excludes courtyards on both sides (placer._hole_conflict).
        self.forbid += [(side, square) for fp in placed for _, poly in fp.get('_packing_holes', ())
                        for square in [_inscribed(poly)] if square for side in ('F', 'B')]
        self.owner = {ref: a['block'] for a in admitted.values() for ref in a['poses']}
        self.frames, self.macros, self.rotated = {}, {}, {}
        # Incremental ranking cache (AGENTEE-355): rotation-only geometry per (block, rotation),
        # and blocked rectangles per (block, rotation, placed owner, owner pose).
        self.static, self.owned, self.geometry = {}, collections.OrderedDict(), {}
        for bid, entry in admitted.items():
            members = [placer.transform(self.by_ref[r], entry['poses'][r]['position_nm'],
                                        entry['poses'][r]['rotation_deg'])
                       for r in sorted(entry['poses'], key=blocks._natural)]
            self.frames[bid] = members
            self.macros[bid] = _macro(bid, members)
        groups = placement_groups.validate(policy['groups']) if 'groups' in policy else []
        self.groups = placement_groups.membership(groups)
        self.anchored = {}
        for ref, group in self.groups.items():
            if 'reference' in group.get('anchor', {}):
                self.anchored.setdefault(group['anchor']['reference'], []).append((ref, group))
        self.locality = (electrical_locality.PlacementIndex(policy['electrical_locality'])
                         if 'electrical_locality' in policy else None)
        stacking = arrangement.get('no_stacking')
        self.stacking = set(stacking['references']) if _hard(stacking) else set()
        self.stack_margin = stacking['margin_nm'] if stacking else 0
        over = arrangement.get('no_block_over_fixed')
        self.over_fixed = (set(admitted) - set(over['exempt_blocks'])) if _hard(over) else set()
        self.fixed_courtyards = [placer._exact(side, c['polygons']) for fp in placed
                                 for side, c in sorted(fp['courtyards'].items())]
        thermal = arrangement.get('thermal')
        self.sources = set(thermal['sources']) if thermal else set()
        self.thermal_min = (thermal['min_distance_nm'] if _hard(thermal) and 'min_distance_nm' in thermal else None)
        self.terms = arrangement['objective']['terms']
        # Partial state.
        self.placed_blocks, self.placed_fps, self.reserved = [], list(placed), []
        self.member_exact, self.shadow_exact = [], []
        self.positions = {fp['ref']: fp['position_nm'] for fp in placed}
        self.peers, self.reach = {}, {}
        for fp in placed:
            self._push_fp(fp)

    def checkpoint(self):
        now = time.monotonic()
        if self.heartbeat is not None and now - self.last_heartbeat >= 5:
            self.heartbeat({'status': 'arranging', 'steps_used': self.steps, 'blocks_placed': len(self.placed_blocks),
                            'block': self.current, 'backjumps': self.backjumps, 'rejected': dict(sorted(self.rejected.items())),
                            'elapsed_s': now - self.started})
            self.last_heartbeat = now
        if self.deadline is not None and now >= self.deadline:
            raise _Stopped('arrangement_budget_exhausted', 'wall-time arrangement budget exhausted')

    def _push_fp(self, fp):
        self.reach[fp['ref']] = _reach(fp)
        if self.locality is not None:
            self.locality.push(fp)
        self.positions[fp['ref']] = fp['position_nm']
        for pad in fp['pads']:
            if pad.get('net'):
                self.peers.setdefault(pad['net'], []).append(tuple(pad['position_nm']))

    def members_at(self, bid, position, rotation):
        turns = rotation // 90
        members = [placer.transform(fp, [position[0] + p[0], position[1] + p[1]],
                                    (fp['rotation_deg'] + rotation) % 360)
                   for fp in self.frames[bid] for p in [placer.rotate(fp['position_nm'], turns)]]
        for fp in members:
            placer._cache_copper_extent(fp)
        return members

    def push(self, bid, position, rotation, proposed):
        members = self.members_at(bid, position, rotation)
        self.placed_blocks.append({'block': bid, 'position_nm': list(position), 'rotation_deg': rotation,
                                   'members': members})
        geometry = {'pose': (position[0], position[1], rotation), 'boxes': [], 'slabs': [], 'holes': [],
                    'shaded': []}
        self.geometry[bid] = geometry
        for fp in members:
            self.reserved.extend((side, box, bid) for side, box in sorted(placer.boxes(fp).items()))
            geometry['boxes'] += [(side, box) for side, box in sorted(placer.boxes(fp).items())]
            # Courtyards are drilled-pad targets only with pad geometry (placer._hole_conflict).
            geometry['slabs'] += [('*', slab) for _, c in sorted(fp['courtyards'].items()) for poly in c['polygons']
                                  for slab in placer._slabs(poly) or () if '_packing_targets' in fp]
            geometry['holes'] += [('*', square) for _, poly in fp.get('_packing_holes', ())
                                  for square in [_inscribed(poly)] if square]
            self.placed_fps.append(fp)
            self._push_fp(fp)
            self.member_exact.extend((bid, placer._exact(side, c['polygons'])) for side, c in sorted(fp['courtyards'].items()))
            if fp['ref'] in self.stacking:
                shadow = _shadow(fp, self.stack_margin)
                self.shadow_exact.append((bid, placer._exact(shadow['side'], shadow['polygons'])))
                geometry['shaded'].append((shadow['side'], self.shadow_exact[-1][1]['box']))

    def pop(self):
        record = self.placed_blocks.pop()
        bid = record['block']
        del self.geometry[bid]
        self.reserved = [r for r in self.reserved if r[2] != bid]
        self.member_exact = [e for e in self.member_exact if e[0] != bid]
        self.shadow_exact = [e for e in self.shadow_exact if e[0] != bid]
        for fp in reversed(record['members']):
            self.placed_fps.pop()
            if self.locality is not None:
                self.locality.pop(fp['ref'])
            del self.positions[fp['ref']]
            for pad in reversed(fp['pads']):
                if pad.get('net'):
                    self.peers[pad['net']].pop()
                    if not self.peers[pad['net']]:
                        del self.peers[pad['net']]

    def _locality(self, members):
        """Cross-block pairs whose peer is placed, as one translated evaluator."""
        if self.locality is None:
            return electrical_locality.CandidateLocality([])
        pairs = []
        for fp in members:
            for own, peer, limit, hard in self.locality.pairs.get(fp['ref'], ()):
                if peer['reference'] in self.positions:
                    offset = electrical_locality.resolve([fp], own)
                    target = self.locality.positions[peer['reference']][self.locality.key(peer)]
                    pairs.append((offset, target, limit, {fp['ref'], peer['reference']}, hard))
        return electrical_locality.CandidateLocality(pairs)

    def _group_targets(self, members):
        points = []
        for fp in members:
            group = self.groups.get(fp['ref'])
            if group is None:
                continue
            point = placement_groups.anchor_point(group, self.positions) if 'anchor' in group else None
            if point is not None:
                ox, oy = fp['position_nm']
                d = group['max_distance_nm']
                points += [(point[0] - ox + dx, point[1] - oy + dy) for dx, dy in ((0, 0), (-d, 0), (d, 0),
                                                                                   (0, -d), (0, d))]
            for poly in group.get('region', {}).get('polygons', []):
                points += [(p[0] - fp['position_nm'][0], p[1] - fp['position_nm'][1]) for p in poly]
        return points

    def rotated_static(self, bid, rotation):
        """Geometry of a block turned at the origin; it depends on nothing placed (cached)."""
        key = (bid, rotation)
        if key not in self.static:
            members = self.members_at(bid, [0, 0], rotation)
            boxes = [(side, box) for fp in members for side, box in sorted(placer.boxes(fp).items())]
            slabs = [(side, slab) for fp in members for side, c in sorted(fp['courtyards'].items())
                     for poly in c['polygons'] for slab in placer._slabs(poly) or ()]
            at_origin = placer.transform(self.macros[bid], [0, 0], rotation)
            holes = [('*', square) for _, poly in at_origin.get('_packing_holes', ()) for square in [_inscribed(poly)]
                     if square]
            shadows = [(shadow['side'], placer.boxes({'courtyards': {shadow['side']: shadow}})[shadow['side']])
                       for fp in members if fp['ref'] in self.stacking for shadow in [_shadow(fp, self.stack_margin)]]
            # Against fixed parts, obstacles, rule areas and fixed courtyards: placement-independent.
            entries = (_solid(slabs, self.solid, self.gap) + _gapped(slabs, self.forbid, 0, None)
                       + _gapped(holes, self.targets, 0, None))
            self.static[key] = {'members': members, 'at_origin': at_origin, 'boxes': boxes, 'holes': holes,
                                'slabs': [('*', slab) for fp in members if '_packing_targets' in fp
                                          for c in fp['courtyards'].values() for poly in c['polygons']
                                          for slab in placer._slabs(poly) or ()], 'shadows': shadows,
                                'frontier': _frontier(boxes), 'entries': entries,
                                'bbox': (min(b[0] for _, b in boxes), min(b[1] for _, b in boxes),
                                         max(b[2] for _, b in boxes), max(b[3] for _, b in boxes))}
        return self.static[key]

    def entries(self, bid, rotation, owner):
        """Blocked rectangles of a block turned at the origin against one placed block (cached)."""
        placed = self.geometry[owner]
        key = (bid, rotation, owner, placed['pose'])
        if key in self.owned:
            self.owned.move_to_end(key)
            return self.owned[key]
        own = self.rotated_static(bid, rotation)
        entries = (_gapped(own['boxes'], placed['boxes'] + placed['shaded'], self.gap, owner)
                   + _gapped(own['shadows'], placed['boxes'], self.gap, owner)
                   + _gapped(own['holes'], placed['slabs'], 0, owner)
                   # A placed block's drilled pads pierce the board: no courtyard may overlap them.
                   + _gapped(own['slabs'], placed['holes'], 0, owner))
        self.owned[key] = entries
        if len(self.owned) > OWNED_CACHE:
            self.owned.popitem(last=False)
        return entries

    def ranked(self, bid, conflicts):
        """Admissible-by-box candidates in rank order; box blockers join ``conflicts``."""
        self.checkpoint()
        macro = self.macros[bid]
        anchors = [(side, box) for side, box, _ in self.reserved] + [(o['side'], o['box']) for o in self.exact]
        anchors += self.solid
        anchors += placer._keepout_boxes(self.keepouts + [a for f in self.placed_fps for a in f.get('packing_keepouts', [])])
        window = placer._window(self.outline, self.margin, None, ())
        lo_x, lo_y, hi_x, hi_y = window['box']
        ranked = []
        for rotation in (0, 90, 180, 270):
            own = self.rotated_static(bid, rotation)
            members, at_origin = own['members'], own['at_origin']
            self.rotated[bid, rotation] = at_origin, members
            evaluator = self._locality(members)
            targets = evaluator.targets() + self._group_targets(members)
            access_overhang = any(placer.access_direction.overhangs_courtyard(fp) for fp in members)
            xs, ys = placer._candidate_axes(macro, rotation, self.outline, anchors, self.gap, self.margin,
                                            targets, None if access_overhang else window)
            access_x, access_y = set(), set()
            for fp in members:
                ax, ay = placer.access_direction.axes(fp, self.outline)
                access_x.update(ax)
                access_y.update(ay)
            xs = sorted(set(xs) | {x for x in access_x if access_overhang or lo_x <= own['bbox'][0] + x and own['bbox'][2] + x <= hi_x})
            ys = sorted(set(ys) | {y for y in access_y if access_overhang or lo_y <= own['bbox'][1] + y and own['bbox'][3] + y <= hi_y})
            # Member-aligned axes: a frontier member box touching a reserved box or obstacle in x.
            bx0, by0, bx1, by1 = own['bbox']
            left, right = own['frontier']
            touch = {a[2] + self.gap - m[0] for side, m in left for other, a in anchors if other == side}
            touch |= {a[0] - self.gap - m[2] for side, m in right for other, a in anchors if other == side}
            xs = sorted(set(xs) | {x for x in touch if lo_x <= bx0 + x and bx1 + x <= hi_x})
            cx, cy = _copper_axes(members, self.outline, own['bbox'], window['box'])
            xs, ys = sorted(set(xs) | cx), sorted(set(ys) | cy)
            entries = own['entries'] + [e for placed in self.placed_blocks for e in self.entries(
                bid, rotation, placed['block'])]
            # One term per net shared with placed parts: the block's pad centroid on that net.
            on_net = {}
            for fp in members:
                for pad in fp['pads']:
                    if pad.get('net') and pad['net'] not in self.grounds and pad['net'] in self.peers:
                        on_net.setdefault(pad['net'], []).append(pad['position_nm'])
            pads = [(net, sum(p[0] for p in points) // len(points), sum(p[1] for p in points) // len(points))
                    for net, points in sorted(on_net.items())]
            sources = [fp['position_nm'] for fp in members if fp['ref'] in self.sources]
            placed_refs = sorted(r for r in self.sources if r in self.positions)
            placed_sources = [self.positions[r] for r in placed_refs]
            doomed = set()
            limit2 = None if self.thermal_min is None else self.thermal_min ** 2
            for x, y in _scan(entries, xs, ys, self.gap, conflicts, doomed, self.checkpoint,
                              contacts=(lo_y - by0, hi_y - by1)):
                # Exact and cheap: a violating position is dropped here, its blockers recorded.
                hit, local_cost = evaluator.constraint((x, y))
                if hit:
                    conflicts.update({self.owner.get(r) for r in hit} - {None, bid})
                    continue
                if limit2 is not None:
                    near = {r for (sx, sy) in sources for r in placed_refs
                            if (sx + x - self.positions[r][0]) ** 2 + (sy + y - self.positions[r][1]) ** 2 < limit2}
                    if near:
                        conflicts.update({self.owner.get(r) for r in near} - {None, bid})
                        continue
                values = {'inter-block-wire': placer._wire_cost([(n, px + x, py + y) for n, px, py in pads],
                                                                self.peers),
                          'thermal-spread': -min((((sx + x - a) ** 2 + (sy + y - b) ** 2) for sx, sy in sources
                                                  for a, b in placed_sources), default=0)}
                values['pad-locality'] = local_cost
                ranked.append((*(values[t] for t in self.terms), x, y, rotation, (x, y) in doomed))
            self.checkpoint()
        ranked.sort()
        return ranked

    def blockers(self, bid, proposed, members):
        """None when every hard predicate admits the macro, else (predicate, blocking blocks)."""
        hits, reach = set(), _reach(proposed)
        for fp in self.placed_fps:
            if not _apart(reach, self.reach[fp['ref']]) and placer._hole_conflict(proposed, fp):
                hits.add(self.owner.get(fp['ref']))
        if hits:
            return 'drilled_pad', hits - {None}
        rejected_access = [fp['ref'] for fp in members if not placer.access_direction.admits(fp, self.outline)]
        if rejected_access:
            for ref in rejected_access:
                self.access_rejections[ref] = self.access_rejections.get(ref, 0) + 1
            return 'access_direction', set()
        if any(placer.access_direction.overhangs_courtyard(fp) for fp in members):
            fits = all(placer._fits(fp, self.outline, self.margin) for fp in members)
        else:
            fits = placer._fits(proposed, self.outline, self.margin)
        if not fits:
            return 'outline', set()
        if placer._near(proposed, self.exact, self.gap):
            return 'fixed_or_obstacle', set()
        if placer._in_keepouts(proposed, self.keepouts):
            return 'rule_area', set()
        hits = {self.owner.get(fp['ref']) for fp in self.placed_fps
                if placer._in_keepouts(proposed, fp.get('packing_keepouts', []))
                or placer._in_keepouts(fp, proposed.get('packing_keepouts', []))}
        if hits:
            return 'rule_area', hits - {None}
        # Member copper outside its courtyards (AGENTEE-366), against peer courtyards and member
        # lands against pad keep-outs (AGENTEE-418); the macro carries no pads.
        for fp in members:
            escaped = placer._copper_escapes(fp, self.outline, self.keepouts, self.placed_fps, self.pad_keepouts)
            if escaped is not None:
                return escaped[0], {self.owner.get(r) for r in escaped[1]} - {None, bid}
        hits = {owner for owner, entry in self.shadow_exact if placer._near(proposed, [entry], self.gap)}
        for fp in members:
            if fp['ref'] in self.stacking:
                shadow = _shadow(fp, self.stack_margin)
                own = {'courtyards': {shadow['side']: {'polygons': shadow['polygons']}}}
                if placer._near(own, self.fixed_courtyards, self.gap):
                    return 'no_stacking', set()
                hits |= {owner for owner, entry in self.member_exact if placer._near(own, [entry], self.gap)}
        if hits:
            return 'no_stacking', hits
        if bid in self.over_fixed and any(_through(fp, self.fixed_courtyards) for fp in members):
            return 'no_block_over_fixed', set()
        positions = dict(self.positions, **{fp['ref']: fp['position_nm'] for fp in members})
        for fp in members:
            group = self.groups.get(fp['ref'])
            if group is not None:
                if 'region' in group and not placement_groups.admits(fp, {'region': group['region']}, {}):
                    return 'group', set()
                point = placement_groups.anchor_point(group, positions) if 'anchor' in group else None
                if point is not None and placement_groups.distance2(fp['position_nm'], point) > \
                        group['max_distance_nm'] ** 2:
                    return 'group', {self.owner.get(group['anchor'].get('reference'))} - {None, bid}
            for ref, g in self.anchored.get(fp['ref'], ()):
                if ref in self.positions and placement_groups.distance2(
                        self.positions[ref], fp['position_nm']) > g['max_distance_nm'] ** 2:
                    return 'group', {self.owner.get(ref)} - {None}
        return None

    def candidates(self, bid, conflicts):
        """Proposed macros in rank order; rejections add their blocking blocks to ``conflicts``."""
        for *_, x, y, rotation, doomed in self.ranked(bid, conflicts):
            self.checkpoint()
            if self.steps == self.budget:
                raise _Stopped('arrangement_budget_exhausted', 'deterministic arrangement candidate budget exhausted')
            self.steps += 1
            if doomed:
                continue
            at_origin, members = self.rotated[bid, rotation]
            proposed = _shift(at_origin, x, y)
            members = [_shift(fp, x, y) for fp in members]
            hit = self.blockers(bid, proposed, members)
            if hit is None:
                yield [x, y], rotation, proposed
            else:
                self.rejected[hit[0]] = self.rejected.get(hit[0], 0) + 1
                conflicts |= hit[1]


def _order(admitted, by_ref, fixed, priority, grounds, through=frozenset()):
    """Declared priorities, then blocks in ``through`` (they reserve both board sides),
    then the rest; within each, the block sharing most signal nets with what is placed."""
    nets = {bid: {p['net'] for r in a['poses'] for p in by_ref[r]['pads']
                  if p.get('net') and p['net'] not in grounds} for bid, a in admitted.items()}
    area = {bid: sum((b[2] - b[0]) * (b[3] - b[1]) for r in a['poses']
                     for b in placer.boxes(by_ref[r]).values()) for bid, a in admitted.items()}
    reached = {p['net'] for r in fixed for p in by_ref[r]['pads'] if p.get('net') and p['net'] not in grounds}
    order = list(priority)
    for bid in order:
        reached |= nets[bid]
    while len(order) < len(admitted):
        bid = min((b for b in admitted if b not in order),
                  key=lambda b: (b not in through, -len(nets[b] & reached), -area[b], blocks._natural(b)))
        order.append(bid)
        reached |= nets[bid]
    return order


def _net_counts(admitted, by_ref, fixed, grounds):
    """Shared signal nets between every pair of blocks and fixed parts: the block-level wire graph."""
    owner = {r: a['block'] for a in admitted.values() for r in a['poses']}
    owner.update({r: r for r in fixed})
    touch = {}
    for ref, unit in owner.items():
        for pad in by_ref[ref]['pads']:
            if pad.get('net') and pad['net'] not in grounds:
                touch.setdefault(pad['net'], set()).add(unit)
    counts = {}
    for units in touch.values():
        for a in units:
            for b in units:
                if blocks._natural(a) < blocks._natural(b):
                    counts[(a, b)] = counts.get((a, b), 0) + 1
    return [{'between': list(k), 'nets': v} for k, v in sorted(counts.items(), key=lambda kv: (
        blocks._natural(kv[0][0]), blocks._natural(kv[0][1])))]


def grade_rules(capture, arrangement, block_refs, fixed):
    """Block-level rules on a complete placement: every rule graded, hard and advisory.

    ``block_refs`` maps each block member to its block. Returns rows with
    ``status`` ``pass`` or ``defect``; a hard defect fails the plan, an advisory one
    is reported only.
    """
    fps = {fp['ref']: fp for fp in capture['footprints']}
    rows = []

    def row(rule, enforcement, findings, **extra):
        rows.append({'rule': rule, 'enforcement': enforcement, 'status': 'defect' if findings else 'pass',
                     'findings': findings, **extra})
    for keepout in arrangement.get('keepouts', []):
        entry = {'side': keepout['side'], 'polygons': keepout['polygons']}
        found = sorted((r for r, fp in fps.items() if _overlaps(fp, [entry])), key=blocks._natural)
        row('keepout', keepout['provenance']['enforcement'], found, id=keepout['id'])
    stacking = arrangement.get('no_stacking')
    if stacking:
        found = []
        for ref in stacking['references']:
            shadow = _shadow(fps[ref], stacking['margin_nm'])
            found += [[ref, r] for r, fp in sorted(fps.items(), key=lambda kv: blocks._natural(kv[0]))
                      if r != ref and _overlaps(fp, [shadow])]
        row('no_stacking', stacking['provenance']['enforcement'], found)
    over = arrangement.get('no_block_over_fixed')
    if over:
        found = []
        for ref, bid in sorted(block_refs.items(), key=lambda kv: blocks._natural(kv[0])):
            if bid in over['exempt_blocks']:
                continue
            found += [[bid, ref, f] for f in sorted(fixed, key=blocks._natural) if _through(fps[ref], [
                {'side': side, 'polygons': c['polygons']} for side, c in fps[f]['courtyards'].items()])]
        row('no_block_over_fixed', over['provenance']['enforcement'], found)
    thermal = arrangement.get('thermal')
    if thermal:
        pairs = []
        for i, a in enumerate(thermal['sources']):
            for b in thermal['sources'][i + 1:]:
                pairs.append({'refs': [a, b], 'distance_mm': math.sqrt(placement_groups.distance2(
                    fps[a]['position_nm'], fps[b]['position_nm'])) / 10 ** 6})
        limit = thermal.get('min_distance_nm')
        found = [p['refs'] for p in pairs if limit is not None and p['distance_mm'] * 10 ** 6 < limit]
        row('thermal_spread', thermal['provenance']['enforcement'], found, pairs=pairs,
            min_distance_mm=min(p['distance_mm'] for p in pairs))
    return rows


def saved_rules(facts, plan, policy):
    """Re-grade the block rules of an arranged plan on the native saved board."""
    record = plan['block_arrangement']
    members = {r: b['block'] for b in record['blocks'] for r in b['references']}
    fixed = {p['ref'] for p in plan['placements'] if p['fixed']} | placer._fixed_refs(facts, policy)
    return grade_rules(facts, record['arrangement'], members, fixed)


def _deferred_grading(plan, admitted):
    """Each block's deferred obligation and group, as graded on the complete plan."""
    locality = plan.get('locality_grading') or {'measured': [], 'defects': [], 'unknown': []}
    groups = plan.get('group_grading') or {'measured': [], 'defects': [], 'unknown': []}
    out = []
    for bid in sorted(admitted, key=blocks._natural):
        deferred = admitted[bid]['deferred']
        for oid in deferred['obligations']:
            measured = [m for m in locality['measured'] if m['obligation'] == oid]
            bad = [d for d in locality['defects'] + locality['unknown'] if d.get('obligation') == oid]
            advisories = [a for a in locality.get('advisories', []) if a.get('obligation') == oid]
            row = {'block': bid, 'kind': 'obligation', 'id': oid, 'measured': len(measured),
                   'status': ('defect' if bad else 'unknown' if not measured else
                              'info' if any(a.get('exceeded') for a in advisories) else 'pass')}
            if advisories:
                row['advisories'] = copy.deepcopy(advisories)
            out.append(row)
        for gid in deferred['groups']:
            measured = [m for m in groups.get('measured', []) if m.get('group') == gid]
            bad = [d for d in placement_groups.violations(groups) if d.get('group') == gid]
            out.append({'block': bid, 'kind': 'group', 'id': gid, 'measured': len(measured),
                        'status': 'pass' if measured and not bad else 'defect' if bad else 'unknown'})
    return out


def arrange(capture, inventory, derivation, policy, arrangement, entries, *, heartbeat=None, effective_policy=None):
    """Arrange every derived block, pack the residual parts, grade the complete plan.

    ``policy`` is the captured packing policy the candidates were packed and are
    admitted against; ``effective_policy`` (default ``policy``) may only append board
    obstacles, such as the runner's IMU marking reservations; anything else raises
    ``EffectivePolicyError``. Returns a complete
    ``agent-ee.packing-plan/1`` with a ``block_arrangement`` record, or a typed
    failure; no search-derived arrangement is ever reported as a pass.
    """
    effective = policy if effective_policy is None else effective_policy
    refs = {fp['ref'] for fp in capture['footprints']}
    placer._validate_policy(policy, refs)
    placer._validate_policy(effective, refs)
    check_effective(policy, effective)
    if derivation['board_sha256'] != capture['board_sha256']:
        raise ValueError('derivation belongs to another board capture')
    block_ids = [b['id'] for b in derivation['blocks']]
    arrangement = validate_policy(arrangement, block_ids, refs)
    margin_nm = placer._edge_margin_nm(effective)
    record = {'schema': POLICY, 'strategy': STRATEGY, 'arrangement': arrangement,
              'derivation_sha256': digest(derivation), 'policy_sha256': digest(policy)}

    def failure(status, reason, steps=0, **extra):
        return {**placer._failure(capture, effective, status, 'unknown', steps, reason, margin_nm),
                'block_arrangement': {**record, **extra}}
    if not isinstance(entries, list):
        raise ValueError('arrangement candidates must be a list')
    admitted, problems = {}, []
    for index, entry in enumerate(entries):
        result, found = admit_entry(entry, capture, inventory, derivation, policy)
        if found:
            problems.append({'candidate': index, 'block': entry['candidate'].get('block_id'), 'problems': found})
        elif result['block'] in admitted:
            problems.append({'candidate': index, 'block': result['block'], 'problems': [{'kind': 'duplicate_block'}]})
        else:
            admitted[result['block']] = result
    missing = [b for b in block_ids if b not in admitted]
    if missing and not problems:
        problems.append({'kind': 'block_candidate_missing', 'blocks': missing})
    if problems:
        return failure('arrangement_refused', 'block candidates not admitted for arrangement', problems=problems)
    record['blocks'] = [{'block': b, 'provenance': admitted[b]['provenance'],
                         'candidate_sha256': admitted[b]['candidate_sha256'], 'anchor': admitted[b]['anchor'],
                         'references': sorted(admitted[b]['poses'], key=blocks._natural),
                         'boundary': admitted[b]['binding'],
                         'deferred': admitted[b]['deferred'],
                         # A library or reused layout keeps its source-to-board reference mapping.
                         **({'mapping': dict(sorted(admitted[b]['mapping'].items(),
                                                    key=lambda item: blocks._natural(item[0])))}
                            if admitted[b]['mapped'] else {})} for b in block_ids]
    locality = effective.get('electrical_locality')
    if locality and electrical_locality.grade(capture, locality)['unknown']:
        return failure('electrical_locality_unavailable', 'pad locality needs unambiguous native pads, limits and citations')
    work = copy.deepcopy(capture)
    try:
        outline = placer._qualified(work)
        for fp in work['footprints']:
            if placer.access_direction.hard(fp):
                placer.access_direction.geometry(fp)
        labels, pad_keepouts = {}, []
        keepouts = placer._keepouts(work, labels, pad_keepouts)
        bands = overhang_bands(effective.get('fixed_overhangs', []), outline)
    except ValueError as exc:
        return failure('unsupported_geometry', str(exc))
    by_ref = {fp['ref']: fp for fp in work['footprints']}
    fixed = placer._fixed_refs(work, effective)
    members = {r for a in admitted.values() for r in a['poses']}
    movable = refs - fixed - members
    groups = placement_groups.validate(effective['groups']) if 'groups' in effective else []
    stranded = sorted(g['id'] for g in groups if g.get('anchor', {}).get('reference') in movable
                      and set(g['references']) & members)
    if stranded:
        return failure('arrangement_refused', 'block members anchored to a residual movable part',
                       problems=[{'kind': 'group_anchor_residual', 'groups': stranded}])
    # Hard keep-outs and fixed parts' shadows are stationary obstacles; so is every board obstacle.
    obstacles = list(effective['obstacles'])
    obstacles += [_obstacle(k, k, 'block-arrangement keep-out ' + k['id'])
                  for k in arrangement.get('keepouts', []) if _hard(k)]
    stacking = arrangement.get('no_stacking')
    if _hard(stacking):
        obstacles += [_obstacle(stacking, _shadow(by_ref[r], stacking['margin_nm']), 'block-arrangement shadow ' + r)
                      for r in stacking['references'] if r in fixed]
        for bid, entry in admitted.items():
            frame = [placer.transform(by_ref[r], entry['poses'][r]['position_nm'], entry['poses'][r]['rotation_deg'])
                     for r in entry['poses']]
            for fp in frame:
                if fp['ref'] in stacking['references']:
                    shadow = _shadow(fp, stacking['margin_nm'])
                    inside = sorted((o['ref'] for o in frame if o is not fp and placer._near(
                        o, [placer._exact(shadow['side'], shadow['polygons'])], effective['gap_nm'])),
                        key=blocks._natural)
                    if inside:
                        return failure('arrangement_refused', f'block {bid} stacks parts in the shadow of {fp["ref"]}',
                                       problems=[{'kind': 'block_internal_stacking', 'block': bid,
                                                  'reference': fp['ref'], 'inside': inside}])
    # Seed fixed pairs closer than an unsourced gap are carried input facts (AGENTEE-376).
    if placer._carries_seed_pairs(effective):
        record['carried_fixed_pairs'] = []
    try:
        _, placed, exact = placer._initial_reservations(work, {**effective, 'obstacles': obstacles}, outline,
                                                        keepouts, bands, {}, record.get('carried_fixed_pairs'))
    except ValueError as exc:
        return failure('fixed_conflict', str(exc))
    placed = [fp for fp in placed if fp['ref'] not in members]
    # A fixed part's copper off the board or in a keep-out is an input defect (AGENTEE-371).
    exempt = []
    refusal = placer.fixed_input_copper(placed, outline, keepouts, labels, effective.get('fixed_overhangs', []), exempt,
                                        pad_keepouts)
    if refusal is not None:
        status = 'fixed_input_copper' if refusal['outcome'] == 'defect' else 'unsupported_geometry'
        return {**failure(status, refusal['reason']), 'outcome': refusal['outcome'], 'findings': refusal['findings']}
    if exempt:
        # Copper within S of its reference's declared exit edge: reported input facts (ruling 371c).
        record['fixed_overhang_copper'] = exempt
    missing = placer.copper_setback_missing(fp for fp in work['footprints'] if fp['ref'] not in fixed)
    if missing:
        return failure('unsupported_geometry', missing)
    search = _Arrangement(work, effective, arrangement, admitted, outline, keepouts, fixed, placed, exact, heartbeat,
                          pad_keepouts)
    # Blocks with drilled pads or a hard opposite-side shadow constrain both sides: place them first.
    through = {bid for bid, entry in admitted.items() if any(
        any(by_ref[r].get('_packing_holes', ())) or r in search.stacking for r in entry['poses'])}
    order = _order(admitted, by_ref, fixed, arrangement['priority_blocks'], set(effective['ground_nets']), through)
    record.update(order=order, inter_block_nets=_net_counts(admitted, by_ref, fixed, set(effective['ground_nets'])),
                  objective=arrangement['objective'])
    if heartbeat is not None:
        heartbeat({'status': 'arranging', 'steps_used': 0, 'blocks_placed': 0})
    levels, backjumps, peak = [], 0, 0

    def stopped(status, reason, unplaced=None):
        if search.access_rejections:
            reason += '; access direction rejected poses for ' + ', '.join(sorted(search.access_rejections))
        partial = [{'block': p['block'], 'position_nm': p['position_nm'], 'rotation_deg': p['rotation_deg']}
                   for p in search.placed_blocks]
        return failure(status, reason, search.steps, search={
            'strategy': STRATEGY, 'steps_used': search.steps, 'step_budget': search.budget, 'backjumps': backjumps,
            'placed_peak': peak, 'rejected': dict(sorted(search.rejected.items())),
            **({'access_rejections': dict(sorted(search.access_rejections.items()))} if search.access_rejections else {}),
            **({'unplaced': unplaced} if unplaced else {}),
            'partial': {'authority': 'diagnostic-only-unvalidated-partial-arrangement', 'blocks': partial}})
    try:
        while len(search.placed_blocks) < len(order):
            i = len(search.placed_blocks)
            if len(levels) == i:
                conflicts = set()
                levels.append({'block': order[i], 'conflicts': conflicts,
                               'candidates': search.candidates(order[i], conflicts)})
            level = levels[i]
            search.current = level['block']
            found = next(level['candidates'], None)
            if found is not None:
                search.push(level['block'], found[0], found[1], found[2])
                peak = max(peak, len(search.placed_blocks))
                continue
            index = {p['block']: k for k, p in enumerate(search.placed_blocks)}
            culprits = sorted({index[b] for b in level['conflicts'] if b in index}, reverse=True)
            if not culprits:
                return stopped('arrangement_search_exhausted',
                               f"no candidate for block {level['block']} in the finite search; infeasibility unproved",
                               level['block'])
            j = culprits[0]
            if backjumps == search.budget:
                return stopped('arrangement_budget_exhausted', 'deterministic backjump budget exhausted',
                               level['block'])
            backjumps += 1
            search.backjumps = backjumps
            while len(search.placed_blocks) > j:
                search.pop()
            levels[j]['conflicts'] |= level['conflicts'] - {levels[j]['block']}
            del levels[j + 1:]
    except _Stopped as stop:
        return stopped(stop.status, stop.reason, order[len(search.placed_blocks)])
    except electrical_locality.TargetBudgetExhausted:
        return stopped('arrangement_budget_exhausted', 'locality target budget exhausted',
                       order[len(search.placed_blocks)])
    elapsed = time.monotonic() - search.started
    poses = {fp['ref']: fp for p in search.placed_blocks for fp in p['members']}
    record['search'] = {'strategy': STRATEGY, 'steps_used': search.steps, 'step_budget': search.budget,
                        'backjumps': backjumps, 'rejected': dict(sorted(search.rejected.items())), 'elapsed_s': elapsed}
    record['arranged'] = [{'block': p['block'], 'position_nm': p['position_nm'], 'rotation_deg': p['rotation_deg']}
                          for p in search.placed_blocks]
    # Residual packing: the unchanged placer with every block member fixed at its arranged pose.
    residual_capture = copy.deepcopy(capture)
    residual_capture['footprints'] = [
        _strip(placer.transform(fp, poses[fp['ref']]['position_nm'], poses[fp['ref']]['rotation_deg']))
        if fp['ref'] in poses else fp for fp in residual_capture['footprints']]
    # An arranged member's own rule areas move with it, exactly once from the source pose,
    # so residual packing and its final land check bind them where the member stands (ruling 418b).
    sources = {fp['uuid']: fp for fp in capture['footprints'] if fp['ref'] in poses}
    try:
        for area in residual_capture.get('rule_areas', []):
            if area.get('owner') in sources:
                fp = sources[area['owner']]
                pose = poses[fp['ref']]
                area['outline'] = placer.transform(
                    {'position_nm': fp['position_nm'], 'rotation_deg': fp['rotation_deg'], 'pads': [],
                     'courtyards': {'zone': area['outline']}},
                    list(pose['position_nm']), pose['rotation_deg'])['courtyards']['zone']
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return failure('unsupported_geometry', 'an arranged member rule area cannot be moved to its pose')
    residual_policy = {**copy.deepcopy(effective), 'obstacles': obstacles + [
        _obstacle(stacking, _shadow(poses[r], stacking['margin_nm']), 'block-arrangement shadow ' + r)
        for r in (stacking['references'] if _hard(stacking) else ()) if r in poses]}
    residual_policy['fixed_refs'] = sorted(set(effective['fixed_refs']) | members, key=blocks._natural)
    record['residual'] = {'references': sorted(movable, key=blocks._natural), 'policy_sha256': digest(residual_policy),
                          'obstacles': residual_policy['obstacles'][len(effective['obstacles']):]}
    # Block members are re-posed, so only parts unchanged from the capture stay seed facts.
    # Members at exactly their admitted poses passed the arrangement copper gate (ruling 371d).
    plan = placer._pack_gated(residual_capture, residual_policy, heartbeat=heartbeat, seed=capture, arranged=poses)
    if plan['status'] != 'planned':
        return {**plan, 'block_arrangement': record}
    plan['placements'] = [{**p, 'fixed': p['ref'] in fixed} for p in plan['placements']]
    complete = {**capture, 'footprints': [
        placer.transform(fp, *next(([p['position_nm'], p['rotation_deg']] for p in plan['placements']
                                    if p['ref'] == fp['ref'])))
        for fp in copy.deepcopy(capture['footprints'])]}
    rows = grade_rules(complete, arrangement, {r: search.owner[r] for r in members}, fixed)
    record['rules'] = rows
    if 'advisory_regions' in effective:
        # The residual pass graded members as fixed; report the complete plan's own fixed parts.
        plan['advisory_regions'] = advisory_regions.grade(complete, effective['advisory_regions'], fixed)
    record['deferred_grading'] = _deferred_grading(plan, admitted)
    record['residual']['steps_used'] = plan['steps_used']
    plan['steps_used'] += search.steps
    plan['policy'] = copy.deepcopy(effective)
    plan['block_arrangement'] = record
    failed = [r for r in rows if r['enforcement'] == 'hard' and r['status'] != 'pass']
    if failed or any(d['status'] not in ('pass', 'info') for d in record['deferred_grading']):
        return failure('judge_rejected', 'complete plan failed a hard block rule or a deferred obligation',
                       plan['steps_used'])
    return plan


def _roster(plan):
    """Every placed block's provenance row from a complete arranged plan; raises on an incomplete roster.

    The provenance records, the arranged poses and the plan's block members must name
    the same blocks exactly once, so no placed block can be dropped from review.
    """
    record = plan.get('block_arrangement') if isinstance(plan, dict) else None
    if not isinstance(record, dict) or plan.get('status') != 'planned' or 'arranged' not in record:
        raise ValueError('a block provenance artifact needs a complete arranged plan')
    ids = [b.get('block') if isinstance(b, dict) else None for b in record.get('blocks', [])]
    placed = [a.get('block') if isinstance(a, dict) else None for a in record['arranged']]
    if (not ids or any(not isinstance(b, str) or not b.strip() for b in ids + placed)
            or len(set(ids)) != len(ids) or len(set(placed)) != len(placed)):
        raise ValueError('arranged plan block identities must be distinct nonempty strings')
    if set(ids) != set(placed):
        raise ValueError('arranged plan provenance records and arranged poses name different blocks: '
                         + ', '.join(sorted(set(ids) ^ set(placed))))
    # Block members are the placed references that are neither fixed nor residual.
    members = ({p['ref'] for p in plan['placements'] if not p['fixed']}
               - set(record['residual']['references']))
    listed = [r for b in record['blocks'] for r in b['references']]
    if len(listed) != len(set(listed)) or set(listed) != members:
        raise ValueError('arranged plan provenance records do not cover exactly the placed block members')
    poses = {a['block']: a for a in record['arranged']}
    rows = []
    for block in record['blocks']:
        provenance, pose = block['provenance'], poses[block['block']]
        rows.append({'block': block['block'], 'references': block['references'], 'anchor': block['anchor'],
                     'candidate_sha256': block['candidate_sha256'], 'class': provenance['class'],
                     'enforcement': provenance['enforcement'], 'citation': provenance['citation'],
                     **{k: provenance[k] for k in ('method', 'source') if k in provenance},
                     'reuse': 'mapped' if 'mapping' in block else 'own-board',
                     **({'mapping': block['mapping']} if 'mapping' in block else {}),
                     'position_nm': pose['position_nm'], 'rotation_deg': pose['rotation_deg']})
    return rows


def provenance_artifact(plan, board_sha256=None, rules=None, *, plan_sha256, reserved_area=None):
    """The review input of a complete arranged plan: every placed block's identity, class and enforcement.

    ``plan_sha256`` is the SHA-256 of the written placement-plan.json bytes, which the
    reviewers receive beside the artifact. ``board_sha256`` names the board the
    reviewers see (the runner's saved board; default the plan's source board) and
    ``rules`` the block rules graded on it (default the plan's). The artifact confers
    no authority: a search-derived block stays advisory and is judged by the
    reviewers as a proposal.
    """
    rows = _roster(plan)
    record = plan['block_arrangement']
    document = {'schema': PROVENANCE_ARTIFACT, 'authority': PROVENANCE_AUTHORITY,
                'source_sha256': plan['board_sha256'], 'board_sha256': board_sha256 or plan['board_sha256'],
                'derivation_sha256': record['derivation_sha256'], 'policy_sha256': record['policy_sha256'],
                'plan_sha256': plan_sha256, 'blocks': rows,
                'rules': [{k: r[k] for k in ('rule', 'id', 'enforcement', 'status') if k in r}
                          for r in (record['rules'] if rules is None else rules)]}
    if reserved_area is not None:
        document['reserved_area'] = reserved_area
    return validate_provenance(document)


def check_roster(document, plan):
    """Refuse a provenance artifact whose roster differs from its complete arranged plan.

    Every block, its references, anchor, candidate hash, class, enforcement, citation,
    mapping and pose must equal the plan's, and every rule keeps the plan's enforcement.
    """
    expected = {r['block']: r for r in _roster(plan)}
    for key, value in (('source_sha256', plan['board_sha256']),
                       ('derivation_sha256', plan['block_arrangement']['derivation_sha256']),
                       ('policy_sha256', plan['block_arrangement']['policy_sha256'])):
        if document[key] != value:
            raise ValueError(f"block provenance {key} is not the placement plan's")
    found = {r['block']: r for r in document['blocks']}
    if set(found) != set(expected):
        raise ValueError('block provenance roster differs from the placement plan: '
                         + ', '.join(sorted(set(found) ^ set(expected))))
    for bid, row in expected.items():
        if found[bid] != row:
            fields = sorted(k for k in row.keys() | found[bid].keys() if row.get(k) != found[bid].get(k))
            raise ValueError(f'block provenance row {bid!r} differs from the placement plan: ' + ', '.join(fields))
    enforcement = {(r['rule'], r.get('id')): r['enforcement'] for r in plan['block_arrangement']['rules']}
    for rule in document['rules']:
        if enforcement.get((rule['rule'], rule.get('id'))) != rule['enforcement']:
            raise ValueError(f"block provenance rule {rule['rule']!r} is not graded with the plan's enforcement")
    return document


def _sha256(value, label):
    if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
        raise ValueError(f'{label} must be a lowercase SHA-256')


def validate_provenance(document):
    """Refuse a block provenance artifact whose classes, enforcement or identities are inconsistent."""
    blocks._keys(document, {'schema', 'authority', 'source_sha256', 'board_sha256', 'derivation_sha256',
                            'policy_sha256', 'plan_sha256', 'blocks', 'rules'}, {'reserved_area'}, label='block provenance')
    if document['schema'] != PROVENANCE_ARTIFACT or document['authority'] != PROVENANCE_AUTHORITY:
        raise ValueError(f'block provenance must be {PROVENANCE_ARTIFACT} with its fixed authority statement')
    for key in ('source_sha256', 'board_sha256', 'derivation_sha256', 'policy_sha256', 'plan_sha256'):
        _sha256(document[key], key)
    if not isinstance(document['blocks'], list) or not document['blocks']:
        raise ValueError('block provenance names no placed block')
    if 'reserved_area' in document:
        from .reserved_area import validate as validate_reserve
        if validate_reserve(document['reserved_area'])['board_sha256'] != document['board_sha256']:
            raise ValueError('reserved area must name the provenance saved board')
    seen, references = set(), set()
    for row in document['blocks']:
        blocks._keys(row, {'block', 'references', 'anchor', 'candidate_sha256', 'class', 'enforcement', 'citation',
                           'reuse', 'position_nm', 'rotation_deg'}, {'method', 'source', 'mapping'},
                     label='block provenance row')
        label = f"block provenance row {row['block']!r}"
        if not isinstance(row['block'], str) or not row['block'].strip() or row['block'] in seen:
            raise ValueError(f'{label}: block ids must be distinct nonempty strings')
        seen.add(row['block'])
        if (not isinstance(row['references'], list) or not row['references']
                or row['anchor'] not in row['references'] or references & set(row['references'])):
            raise ValueError(f'{label}: references must be nonempty, disjoint and contain the anchor')
        if any(not isinstance(r, str) or not r for r in row['references']):
            raise ValueError(f'{label}: references must be nonempty text')
        references |= set(row['references'])
        position = row['position_nm']
        if (not isinstance(position, list) or len(position) != 2 or any(type(v) is not int for v in position)
                or type(row['rotation_deg']) is not int or row['rotation_deg'] not in (0, 90, 180, 270)):
            raise ValueError(f'{label}: pose needs integer position_nm [x, y] and a rotation of 0, 90, 180 or 270')
        _sha256(row['candidate_sha256'], label + ' candidate_sha256')
        # Fail closed: an unknown class, or a class whose enforcement was relabelled, refuses.
        if row['class'] not in blocks.PROVENANCE or row['enforcement'] != blocks.PROVENANCE[row['class']]:
            raise ValueError(f"{label}: class {row['class']!r} does not carry enforcement {row['enforcement']!r}")
        if not isinstance(row['citation'], str) or not row['citation'].strip():
            raise ValueError(f'{label}: citation must be nonempty text')
        if row['reuse'] not in ('own-board', 'mapped') or (row['reuse'] == 'mapped') != ('mapping' in row):
            raise ValueError(f'{label}: a mapped block, and only one, carries its mapping')
        if 'mapping' in row and (not isinstance(row['mapping'], dict)
                                 or sorted(row['mapping'].values()) != sorted(row['references'])):
            raise ValueError(f'{label}: the mapping must cover exactly the placed references')
    for rule in document['rules']:
        blocks._keys(rule, {'rule', 'enforcement', 'status'}, {'id'}, label='block provenance rule')
        if rule['enforcement'] not in ('hard', 'advisory') or rule['status'] not in ('pass', 'defect'):
            raise ValueError(f"block provenance rule {rule['rule']!r} has an unknown enforcement or status")
    return document


def review_input(run_dir, manifest):
    """The scope's declared block provenance, read from the sealed capture and bound to its board and plan.

    The scope also declares the complete placement-plan.json the artifact was made from
    (``block_plan``); ``plan_sha256`` must identify those captured bytes and the
    artifact's whole roster must equal the plan's. A missing, uncaptured, malformed,
    partial, other-plan or other-board artifact raises, so the packet is not built and
    the review stays incomplete; it is never silently dropped.
    """
    scope = manifest['scope']
    rel, plan_rel, board = scope['block_provenance'], scope.get('block_plan'), scope['board']
    captured = {}
    for key, value in (('block_provenance', rel), ('block_plan', plan_rel)):
        if not isinstance(value, str) or value not in manifest['files']:
            raise ValueError(f'{key} {value!r} is not a captured input')
        raw = (Path(run_dir) / 'inputs' / value).read_bytes()
        if hashlib.sha256(raw).hexdigest() != manifest['files'][value]:
            raise ValueError(f'captured {key} changed; evidence is stale')
        captured[key] = raw
    document = validate_provenance(strict_json(captured['block_provenance']))
    if document['board_sha256'] != manifest['files'].get(board):
        raise ValueError(f'block provenance describes board {document["board_sha256"]}, not the captured {board}')
    if document['plan_sha256'] != manifest['files'][plan_rel]:
        raise ValueError(f'block provenance describes plan {document["plan_sha256"]}, not the captured {plan_rel}')
    check_roster(document, strict_json(captured['block_plan']))
    return {'path': 'inputs/' + rel, 'sha256': manifest['files'][rel], 'record': document,
            'plan': {'path': 'inputs/' + plan_rel, 'sha256': manifest['files'][plan_rel]}}


def _candidate_file(path, raw=None):
    """A bare candidate file; a ``block_library materialize`` record is refused, not unwrapped."""
    document = strict_json(Path(path).read_bytes() if raw is None else raw)
    if not isinstance(document, dict) or document.get('schema') != blocks.CANDIDATE:
        raise ValueError(f'{path}: expected a bare {blocks.CANDIDATE} file '
                         '(block_library materialize --out writes one; its result record is not a candidate)')
    return document


def load_entries(paths):
    return [{'candidate': _candidate_file(p)} for p in paths]


def request_section(allocation, arrangement, candidates):
    """The ``blocks`` section of a placer_runner request from file paths.

    ``allocation`` is the same allocation file every candidate was packed or
    materialized against (``blocks pack --allocation``, ``block_library materialize
    --allocation``); the runner derives from its text and admission binds its hash.
    """
    return request_section_from_bytes(None if allocation is None else Path(allocation).read_bytes(),
                                      Path(arrangement).read_bytes(), [Path(c).read_bytes() for c in candidates],
                                      names=list(map(str, candidates)))


def request_section_from_bytes(allocation, arrangement, candidates, names=None):
    """The same section from bytes a caller has already bound by digest (AGENTEE-439)."""
    names = names or [f'candidate {i + 1}' for i in range(len(candidates))]
    return {'allocation_text': None if allocation is None else allocation.decode('utf-8'),
            'arrangement': strict_json(arrangement),
            'candidates': [{'candidate': _candidate_file(name, raw)} for name, raw in zip(names, candidates)]}


def plan_from_request(facts, section, policy, effective, netlist, heartbeat=None):
    """placer_runner entry: derive from the captured netlist, admit, arrange, pack."""
    blocks._keys(section, {'arrangement', 'candidates'}, {'allocation_text'}, label='blocks request')
    raw = Path(netlist).read_bytes()
    allocation = section.get('allocation_text')
    if allocation is not None and not isinstance(allocation, str):
        raise ValueError('allocation_text must be the allocation file text')
    derivation = blocks.derive(read_native_inventory(netlist), read_sheet_names(netlist), facts,
                               inventory_sha256=hashlib.sha256(raw).hexdigest(),
                               allocation_raw=None if allocation is None else allocation.encode('utf-8'))
    return arrange(facts, read_native_inventory(netlist), derivation, policy, section['arrangement'],
                   section['candidates'], heartbeat=heartbeat, effective_policy=effective)


OFFLINE_RESERVE = ('offline arrangement writes no native board and has no fabrication inventory; '
                   'run placer_runner with the --runner-section blocks request to measure the saved board')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--probe', required=True, type=Path, help='native placement-probe.json')
    parser.add_argument('--netlist', required=True, type=Path, help='captured native XML export')
    parser.add_argument('--allocation', type=Path, help=f'{blocks.ALLOCATION} JSON')
    parser.add_argument('--policy', required=True, type=Path, help='agent-ee.packing-policy/1')
    parser.add_argument('--arrangement', required=True, type=Path, help=f'{POLICY} JSON')
    parser.add_argument('--candidate', required=True, type=Path, action='append', help='admitted block candidate')
    parser.add_argument('--out', required=True, type=Path, help='new output JSON file')
    parser.add_argument('--provenance', type=Path, help=f'new {PROVENANCE_ARTIFACT} file, written when planned')
    parser.add_argument('--runner-section', type=Path,
                        help='new placer_runner "blocks" request section from the same files, written when planned')
    args = parser.parse_args(argv)
    try:
        capture, inventory, derivation = blocks._load(args)
        result = arrange(capture, inventory, derivation, strict_json(args.policy.read_bytes()),
                         strict_json(args.arrangement.read_bytes()), load_entries(args.candidate),
                         heartbeat=lambda event: print(json.dumps(event), file=sys.stderr, flush=True))
        blocks._write(args.out, result)
        if args.provenance is not None and result['status'] == 'planned':
            # No saved board or fabrication inventory exists offline; the reserve stays a
            # typed unknown. placer_runner with --runner-section measures the saved board.
            from .reserved_area import unmeasured
            blocks._write(args.provenance, provenance_artifact(
                result, plan_sha256=hashlib.sha256(args.out.read_bytes()).hexdigest(),
                reserved_area=unmeasured(result['board_sha256'], 'offline_unsaved_plan', OFFLINE_RESERVE)))
        if args.runner_section is not None and result['status'] == 'planned':
            blocks._write(args.runner_section, request_section(args.allocation, args.arrangement, args.candidate))
    except (OSError, ValueError) as exc:
        print(json.dumps({'status': 'refused', 'reason': str(exc)}))
        return 1
    print(json.dumps({'status': result['status'], 'out': str(args.out)}))
    return 0 if result['status'] == 'planned' else 1


if __name__ == '__main__':
    raise SystemExit(main())
