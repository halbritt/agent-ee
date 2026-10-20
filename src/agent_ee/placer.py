"""Deterministic net-aware placement search, over captured geometry only.

The search is deliberately conservative: inflated courtyard boxes reserve space
for parts it places. Fixed courtyards and declared obstacles are exact polygons
that keep the gap (AGENTEE-179). Full polygons and the independent placement
judge validate the proposed plan. A bounded search failure is unknown, never a
proved design defect.

AGENTEE-212: curved courtyards reserve their outer bound and prove
infeasibility only with their inner bound; a fixed courtyard may leave the
outline only inside its envelope-bound overhang band; declared groups (target
regions, anchor distances) are enforced and graded; any part without a
qualified mounting-side courtyard is refused by name. The greedy choice is kept
as a ranked first fit; a part left without a candidate backjumps over the parts
that blocked it, within the step budget, and a declared region provably too
small for its courtyards (with or without the packing gap) is proved infeasible.
Crowded clusters: candidates align to the slabs of concave fixed courtyards, and
parts bound to an anchor point are placed first.
"""
import bisect
import copy
from decimal import Decimal
from fractions import Fraction
import math

from . import electrical_locality, placement_groups
from .placement import (area2, convex, courtyard_bounds, cross, edges, intersection_area, judge, overhang_bands,
                        qualified_polygons, validate_capture)
from .placement_predicates import segment_distance2
from .symbol_library import LibraryError, nanometres


# Native pcbnew PAD_SHAPE values, read from the KiCad 10.0.7 bindings.
# TRAPEZOID (3) is unqualified; CUSTOM (6) requires per-pad native evidence.
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
    for kind in ('copper', 'holes'):
        for entry in placed.get('packing_pad_geometry', {}).get(kind, []):
            entry['polygons'] = [[move(p) for p in poly] for poly in entry['polygons']]
    for pad in placed['pads']:
        if pad.get('custom_geometry', {}).get('status') == 'proven':
            box = pad['custom_geometry']['bbox_nm']
            corners = [move([x, y]) for x in (box[0], box[2]) for y in (box[1], box[3])]
            pad['custom_geometry']['bbox_nm'] = [min(p[0] for p in corners), min(p[1] for p in corners),
                                                 max(p[0] for p in corners), max(p[1] for p in corners)]
        pad['position_nm'] = move(pad['position_nm'])
        pad['rotation_deg'] = (pad['rotation_deg'] + rotation-fp['rotation_deg']) % 360
    placed.update(position_nm=list(position), rotation_deg=rotation)
    _cache_pad_reservations(placed)
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
    if type(policy.get('capacity_proof', False)) is not bool:
        raise ValueError('capacity_proof must be a boolean')
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
    if 'electrical_locality' in policy:
        electrical_locality.validate(policy['electrical_locality'])
    if 'groups' in policy:
        placement_groups.validate(policy['groups'], refs)
    if 'constraint_provenance' in policy:
        _validate_provenance(policy['constraint_provenance'])


PROVENANCE_CLASSES = ('owner_stated', 'derived', 'assumed', 'unknown')
UNDECLARED = {'class': 'unknown', 'enforcement': 'hard',
              'reason': 'no envelope provenance supplied; enforced as before, never treated as owner-stated'}


def _validate_provenance(value):
    """Envelope constraint provenance (AGENTEE-266), bound by the placement adapter from captured envelopes."""
    if not isinstance(value, dict) or set(value) - {'outline', 'fixed'} or not isinstance(value.get('fixed', {}), dict):
        raise ValueError('constraint_provenance declares only outline and fixed')
    entries = ([value['outline']] if 'outline' in value else []) + list(value.get('fixed', {}).values())
    for entry in entries:
        if (not isinstance(entry, dict) or entry.get('class') not in PROVENANCE_CLASSES
                or entry.get('enforcement') not in ('hard', 'soft')
                or (entry['enforcement'] == 'soft') != isinstance(entry.get('soft_reason'), str)
                or (entry['class'] == 'assumed' and entry['enforcement'] != 'soft')
                or (entry['class'] == 'unknown' and entry['enforcement'] != 'hard')):
            raise ValueError('constraint_provenance entries need a class, a hard/soft enforcement and a soft reason')


def _binding_constraints(policy, margin_nm, blocked_by=()):
    """The limits a capacity row or proof binds, each with its provenance class (AGENTEE-266).

    Fixed parts and obstacles bind only where they block; the edge margin and gap come
    from the packing policy or a fab profile, not an envelope, so their provenance is unknown."""
    declared = policy.get('constraint_provenance', {})
    fixed = declared.get('fixed', {})
    out = [{'constraint': 'outline', **declared.get('outline', UNDECLARED)}]
    # References have no spaces; obstacles, rule areas and keepouts are named with one.
    out += [{'constraint': name, **UNDECLARED} if ' ' in name else
            {'constraint': f'fixed {name}', **fixed.get(name, UNDECLARED)} for name in blocked_by]
    if margin_nm:
        out.append({'constraint': 'min_courtyard_edge_mm', **UNDECLARED})
    if policy['gap_nm']:
        out.append({'constraint': 'gap_nm', **UNDECLARED})
    return out


def _soft_proof(failure, binding):
    """A proof against a soft (assumed or owner-weakened) limit is not a design defect.

    It stays a proof about that limit, reported as unknown with the limits the owner
    must confirm; a proof bound only by hard or undeclared limits keeps its verdict."""
    soft = [b for b in binding if b['enforcement'] == 'soft']
    if not soft:
        return {**failure, 'binding_constraints': binding}
    return {**failure, 'outcome': 'unknown', 'binding_constraints': binding,
            'owner_confirmation': [b['constraint'] for b in soft],
            'reason': failure['reason'] + '; binds soft limit(s) ' + ', '.join(
                f"{b['constraint']} ({b['class']}: {b['soft_reason']})" for b in soft)
                      + ', so it is not a design defect until the owner confirms them'}


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
    has_holes = any(isinstance(p, dict) and p.get('attribute') in ('PTH', 'NPTH')
                    for f in capture['footprints'] for p in f['pads'])
    for fp in capture['footprints']:
        geometry = fp.get('packing_pad_geometry')
        if geometry is not None:
            if not isinstance(geometry, dict) or geometry.get('status') != 'proven':
                reason = geometry.get('reason', '') if isinstance(geometry, dict) else ''
                raise ValueError(f"{fp['ref']}: native packing pad geometry unqualified {reason}")
            for kind in ('copper', 'holes'):
                if not isinstance(geometry.get(kind), list):
                    raise ValueError(f"{fp['ref']}: native packing {kind} inventory missing")
                for entry in geometry[kind]:
                    if (not isinstance(entry, dict) or not isinstance(entry.get('layers'), list)
                            or not entry['layers'] or any(not isinstance(l, str) or not l.endswith('.Cu')
                                                         for l in entry['layers'])):
                        raise ValueError(f"{fp['ref']}: native packing copper layers missing")
                    entry['polygons'] = qualified_polygons({'status': 'proven', 'polygons': entry.get('polygons')})
            if (any(p.get('attribute') in ('PTH', 'NPTH') for p in fp['pads']) and not geometry['holes']):
                raise ValueError(f"{fp['ref']}: native packing drill reservations empty")
        elif has_holes:
            raise ValueError(f"{fp['ref']}: native packing drill reservations missing")
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
            # Only qualified native shapes enter the packing geometry inventory.
            custom = pad.get('custom_geometry', {})
            qualified_custom = (pad.get('shape') == 6 and isinstance(custom, dict)
                                and custom.get('status') == 'proven'
                                and isinstance(custom.get('primitives'), list) and custom['primitives']
                                and isinstance(custom.get('merged_polygons_nm'), list) and custom['merged_polygons_nm']
                                and isinstance(custom.get('bbox_nm'), list) and len(custom['bbox_nm']) == 4
                                and all(type(v) is int for v in custom['bbox_nm'])
                                and custom['bbox_nm'][0] < custom['bbox_nm'][2]
                                and custom['bbox_nm'][1] < custom['bbox_nm'][3])
            if type(pad.get('shape')) is not int or (pad['shape'] not in PAD_SHAPES and not qualified_custom):
                raise ValueError(f"{fp['ref']} pad {pad.get('number')!r}: native pad shape "
                                 f"{pad.get('shape')!r} not qualified for packing")
            if type(pad.get('rotation_deg')) not in (int, float) or pad['rotation_deg'] % 90:
                raise ValueError(f"{fp['ref']} pad {pad.get('number')!r}: pad rotation must be a quarter turn")
        _cache_pad_reservations(fp)
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


def _cache_pad_reservations(fp):
    """Ephemeral box prefilters; the native polygons remain the collision authority."""
    geometry = fp.get('packing_pad_geometry')
    if geometry is None:
        return

    def inventory(polygons):
        return [(boxes({'courtyards': {'F': {'polygons': [p]}}})['F'], p) for p in polygons]

    fp['_packing_holes'] = inventory(p for e in geometry['holes'] for p in e['polygons'])
    fp['_packing_targets'] = inventory([p for c in fp['courtyards'].values() for p in c['polygons']] +
                                       [p for e in geometry['copper'] for p in e['polygons']])


def _hole_conflict(candidate, peer):
    """Symmetric PTH/NPTH exclusion, including copper outside courtyards."""
    for owner, other in ((candidate, peer), (peer, candidate)):
        holes = owner.get('_packing_holes', [])
        if not holes:
            continue
        for (x0, y0, x1, y1), polygon in holes:
            for (a0, b0, a1, b1), target in other['_packing_targets']:
                if x1 <= a0 or a1 <= x0 or y1 <= b0 or b1 <= y0:
                    continue
                if intersection_area(polygon, target) > 0:
                    return True
    return False


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


def _candidate_axes(fp, rotation, outline, reserved, gap, margin_nm=0, targets=(), window=None):
    """Sorted candidate origin x and y values; every pairing is a candidate position."""
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
    if window is not None:
        # Origin limits from necessary conditions only (see _window): nothing admissible is dropped.
        x0, y0, x1, y1 = bounds[0]
        lo_x, lo_y, hi_x, hi_y = window['box']
        xs = {x for x in xs if lo_x <= x0+x and x1+x <= hi_x}
        ys = {y for y in ys if lo_y <= y0+y and y1+y <= hi_y}
        if 'origin' in window:
            ox0, oy0, ox1, oy1 = window['origin']
            xs = {x for x in xs if ox0 <= x <= ox1}
            ys = {y for y in ys if oy0 <= y <= oy1}
    return sorted(xs), sorted(ys)


def _slabs(polygon):
    """Boxes whose union is a simple rectilinear polygon, else None.

    Between consecutive vertex x values the polygon's vertical section is fixed:
    the horizontal edges spanning the slab, paired bottom to top.
    """
    if any(a[0] != b[0] and a[1] != b[1] for a, b in edges(polygon)):
        return None
    flats = [(min(a[0], b[0]), max(a[0], b[0]), a[1]) for a, b in edges(polygon) if a[1] == b[1]]
    xs = sorted({p[0] for p in polygon})
    out = []
    for xa, xb in zip(xs, xs[1:]):
        ys = sorted(y for lo, hi, y in flats if lo <= xa and xb <= hi)
        out.extend((xa, y0, xb, y1) for y0, y1 in zip(ys[::2], ys[1::2]))
    return out


def _unreserved(side_boxes, rectangles, xs, ys, reserved, solid, gap, conflicts, doomed=None):
    """Positions whose courtyard boxes clear every same-side reserved box by the gap.

    The reservation test applied as a sweep: for each x, a reserved box that the
    candidate meets in x blocks exactly the y in the open interval
    (b0 - gap - y1, b1 + gap - y0). The owners of boxes that block a position are
    added to ``conflicts``. ``rectangles`` maps a side to boxes whose union is the
    candidate's courtyard there (rectilinear courtyards only), so a positive-area
    overlap of one with a ``solid`` box (a slab of a rectilinear fixed courtyard or
    obstacle) is a polygon overlap, which the exact obstacle test rejects; such positions are
    dropped too (gap 0, no owner).

    A kept position where such a pair of boxes is nearer than the gap
    (dx² + dy² < gap², with dx, dy their nonnegative separations) is added to
    ``doomed`` when given: the distance between two unions of boxes is the least
    distance between a pair, so the exact obstacle test would reject it too. The
    caller still counts it as a step and rejects it without the polygon test.
    """
    for x in xs:
        blocked, near = [], []
        for side, (x0, y0, x1, y1) in side_boxes.items():
            for other, (a0, b0, a1, b1), ref in reserved:
                if other == side and not (x1+x+gap <= a0 or a1+gap <= x0+x):
                    i, j = bisect.bisect_right(ys, b0-gap-y1), bisect.bisect_left(ys, b1+gap-y0)
                    if i < j:
                        blocked.append((i, j))
                        conflicts.add(ref)
        for side, parts in rectangles.items():
            for r0, s0, r1, s1 in parts:
                for other, (a0, b0, a1, b1) in solid:
                    if other != side:
                        continue
                    if not (r1+x <= a0 or a1 <= r0+x):
                        i, j = bisect.bisect_right(ys, b0-s1), bisect.bisect_left(ys, b1-s0)
                        if i < j:
                            blocked.append((i, j))
                    dx = max(0, a0-r1-x, r0+x-a1)
                    if doomed is not None and gap and dx < gap:
                        # For an integer separation dy, dy² < gap² - dx² exactly when dy <= reach.
                        reach = math.isqrt(gap*gap - dx*dx - 1)
                        i, j = bisect.bisect_left(ys, b0-s1-reach), bisect.bisect_right(ys, b1-s0+reach)
                        if i < j:
                            near.append((i, j))
        near = {k for i, j in near for k in range(i, j)}
        free, start = [], 0
        for i, j in sorted(blocked):
            free.extend(range(start, i))
            start = max(start, j)
        free.extend(range(start, len(ys)))
        for k in free:
            if k in near:
                doomed.add((x, ys[k]))
            yield x, ys[k]


def _window(outline, margin_nm, group, ties):
    """Exact necessary limits on a movable candidate; never sufficient.

    Its courtyard box must lie in the outline's box shrunk by the edge margin (a
    courtyard point nearer a box side than the margin is nearer the outline, which
    the ray toward that side must cross) and in the group region's box; its
    origin must lie in the distance box of every placed anchor partner. The full
    predicates still decide every candidate the window keeps.
    """
    box = [min(p[0] for p in outline) + margin_nm, min(p[1] for p in outline) + margin_nm,
           max(p[0] for p in outline) - margin_nm, max(p[1] for p in outline) - margin_nm]
    window = {}
    if group is not None and 'region' in group:
        points = [p for poly in group['region']['polygons'] for p in poly]
        box = [max(box[0], min(p[0] for p in points)), max(box[1], min(p[1] for p in points)),
               min(box[2], max(p[0] for p in points)), min(box[3], max(p[1] for p in points))]
    for _, (x, y), d in ties:
        x0, y0, x1, y1 = window.get('origin', (x-d, y-d, x+d, y+d))
        window['origin'] = (max(x0, x-d), max(y0, y-d), min(x1, x+d), min(y1, y+d))
    window['box'] = tuple(box)
    return window


def _pad_nets(fp, position, rotation, grounds):
    """Signal pad positions exactly as transform() moves them."""
    origin, turns = fp['position_nm'], int((rotation-fp['rotation_deg']) / 90)
    out = []
    for pad in fp['pads']:
        if pad['net'] and pad['net'] not in grounds:
            x, y = rotate([pad['position_nm'][0]-origin[0], pad['position_nm'][1]-origin[1]], turns)
            out.append((pad['net'], x+position[0], y+position[1]))
    return out


def _wire_cost(pads, peers):
    # Integer Manhattan pad-to-nearest-same-net-pad cost: deterministic ties.
    cost = 0
    for net, x, y in pads:
        if net in peers:
            cost += min(abs(x-a) + abs(y-b) for a, b in peers[net])
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


def _inside(polygon, outline):
    return intersection_area(polygon, outline) == Fraction(abs(area2(polygon)), 2)


def _length_bounds(a, b):
    """Integer lower and upper bounds of a segment's Euclidean length."""
    root = math.isqrt((a[0]-b[0])**2 + (a[1]-b[1])**2)
    return root, root + 1


def _swept_area_low(polygon, r):
    """Exact lower bound of the area within distance r of a polygon.

    A convex polygon's parallel body is A + perimeter*r + pi*r^2 (Steiner);
    perimeter and pi are bounded below. Any other polygon still gains at least
    2r per line across its larger extent.
    """
    area = Fraction(abs(area2(polygon)), 2)
    if convex(polygon) or convex(polygon[::-1]):
        return area + sum(_length_bounds(a, b)[0] for a, b in edges(polygon)) * r + 3 * r * r
    xs, ys = [p[0] for p in polygon], [p[1] for p in polygon]
    return area + 2 * r * max(max(xs) - min(xs), max(ys) - min(ys))


def _swept_area_high(polygon, r):
    """Exact upper bound of the area within distance r of a polygon.

    Outside the polygon, that area is covered by an outward r-strip on every edge
    and a sector of angle below pi at each convex vertex.
    """
    return (Fraction(abs(area2(polygon)), 2) + sum(_length_bounds(a, b)[1] for a, b in edges(polygon)) * r
            + len(polygon) * Fraction(22, 7) * r * r / 2)


def _region_impossibility(capture, outline, groups, fixed, placed, obstacles, policy):
    """A declared region too small for the movable courtyards that must lie in it.

    Every movable member of a group whose region lies inside this group's region
    (one polygon) or equals it must put each courtyard, by its inner bound, inside
    the region and the outline, clear of fixed courtyards, declared obstacles and
    footprint keepouts. Only pairwise disjoint blockers are subtracted, by their
    inner bounds. ``region_capacity`` takes area alone: blockers count only when
    wholly inside the outline. ``region_capacity_gap`` adds the packing gap g:
    courtyards grown by g/2 stay disjoint inside the region grown by g/2, apart
    from fixed courtyards and declared obstacles (which keep the gap; keepouts do
    not); a part with several courtyards on one side counts them ungrown. Every
    quantity is an exact rational, so either bound is a proof, never a guess.
    """
    by_ref = {fp['ref']: fp for fp in capture['footprints']}
    regions = [(g, [[tuple(p) for p in poly] for poly in g['region']['polygons']]) for g in groups if 'region' in g]
    r = Fraction(policy['gap_nm'], 2)
    gapped = [(fp['ref'], side, _inner(c)) for fp in placed for side, c in sorted(fp['courtyards'].items())]
    gapped += [(f"obstacle {i}", o['side'], o['polygons']) for i, o in enumerate(policy['obstacles'])]
    others = [(f"rule area {i}", o['side'], o['polygons']) for i, o in enumerate(obstacles)]
    others += [(f"{fp['ref']} keepout", o['side'], o['polygons']) for fp in placed for o in fp.get('packing_keepouts', [])]
    def disjoint(candidates, inside):
        kept = {'F': [], 'B': []}
        for name, side, polygons in candidates:
            for poly in polygons:
                if (not inside or _inside(poly, outline)) and all(intersection_area(poly, q) == 0 for _, q in kept[side]):
                    kept[side].append((name, poly))
        return kept
    plain, grown = disjoint(gapped + others, True), disjoint(gapped, False)
    def blocked(blockers, region):
        total, names = 0, set()
        for name, poly in blockers:
            area = sum(intersection_area(poly, q) for q in region)
            if area:
                total += area
                names.add(name)
        return total, sorted(names)
    for group, region in regions:
        contained = [g for g, q in regions if q == region or (len(region) == 1 and all(
            _inside(p, region[0]) for p in q))]
        refs = sorted({ref for g in contained for ref in g['references']} - fixed)
        board_area = sum(intersection_area(q, outline) for q in region)
        for side in ('F', 'B'):
            courtyards = [_inner(by_ref[ref]['courtyards'][side]) for ref in refs if side in by_ref[ref]['courtyards']]
            required = sum(Fraction(abs(area2(p)), 2) for polys in courtyards for p in polys)
            if not required:
                continue
            record = {'group': group['id'], 'side': side, 'groups': sorted(g['id'] for g in contained),
                      'references': refs}
            area, names = blocked(plain[side], region)
            if required > board_area - area:
                return {'kind': 'region_capacity', **record, 'region_board_area_nm2': _fraction(board_area),
                        'blocked_area_nm2': _fraction(area), 'blocked_by': names,
                        'available_area_nm2': _fraction(board_area - area),
                        'required_courtyard_area_nm2': _fraction(required)}
            if not r:
                continue
            swept = sum(_swept_area_low(polys[0], r) if len(polys) == 1 else
                        sum(Fraction(abs(area2(p)), 2) for p in polys) for polys in courtyards)
            space = sum(_swept_area_high(q, r) for q in region)
            area, names = blocked(grown[side], region)
            if swept > space - area:
                return {'kind': 'region_capacity_gap', **record, 'gap_nm': policy['gap_nm'],
                        'grown_region_area_upper_nm2': _fraction(space), 'blocked_area_nm2': _fraction(area),
                        'blocked_by': names, 'available_area_nm2': _fraction(space - area),
                        'required_grown_courtyard_area_nm2': _fraction(swept)}
    return None


def _union_area(rects):
    """Exact area of a union of closed axis-aligned boxes (x0, y0, x1, y1), by an x sweep."""
    events = sorted({v for x0, _, x1, _ in rects for v in (x0, x1)})
    rects = sorted(r for r in rects if r[0] < r[2] and r[1] < r[3])
    total, active, k = 0, [], 0
    for xa, xb in zip(events, events[1:]):
        while k < len(rects) and rects[k][0] <= xa:
            active.append(rects[k])
            k += 1
        active = [r for r in active if r[2] > xa]
        top = None
        for _, y0, _, y1 in sorted(active, key=lambda r: r[1]):
            if top is None or y0 >= top:
                total += (xb - xa) * (y1 - y0)
                top = y1
            elif y1 > top:
                total += (xb - xa) * (y1 - top)
                top = y1
    return total


def _admissible(box, forbid):
    """A closed box less open boxes, as (x0, y0, x1, y1) pieces whose union is that set.

    Between consecutive breakpoints in x (and at each of them) the admitted y
    values are fixed closed intervals, which may be single points.
    """
    bx0, by0, bx1, by1 = box
    if bx0 > bx1 or by0 > by1:
        return []
    xs = sorted({bx0, bx1} | {v for f in forbid for v in (f[0], f[2]) if bx0 < v < bx1})
    pieces = []
    for lo, hi in [(x, x) for x in xs] + list(zip(xs, xs[1:])):
        mid2 = lo + hi  # Twice the element's midpoint: exact in integers.
        cur = by0
        for b0, b1 in sorted((f[1], f[3]) for f in forbid if 2 * f[0] < mid2 < 2 * f[2]):
            if b0 >= cur:
                if cur <= by1:
                    pieces.append((lo, cur, hi, min(b0, by1)))
                cur = b1
            elif b1 > cur:
                cur = b1
        if cur <= by1:
            pieces.append((lo, cur, hi, by1))
    return pieces


def _square_cover_area(box, blockers, a):
    """Exact area of the union of every closed a x a axis square inside box that overlaps no blocker's interior.

    That union (the morphological opening of the free space by the square) holds
    every set in the free space each of whose points lies in such a square. The
    squares' lower left corners range over a closed box less one open box per
    blocker.
    """
    bx0, by0, bx1, by1 = box
    forbid = [(x0 - a, y0 - a, x1, y1) for x0, y0, x1, y1 in blockers if x0 < x1 and y0 < y1]
    return _union_area([(x0, y0, x1 + a, y1 + a) for x0, y0, x1, y1 in _admissible((bx0, by0, bx1 - a, by1 - a), forbid)])


def _probe_cover(box, blockers, inner, outer):
    """Boxes (reach, solid): every free point a set can cover lies in reach and in no solid box's interior.

    A set inside the free space (box less the blockers' interiors) that holds a
    disc of radius ``inner`` and lies within ``outer`` of that disc's centre puts
    the centre where the axis cross of half-length ``inner`` is free, and covers
    only points within ``outer`` of it (an axis box) that are themselves free.
    This holds at any angle of the set.
    """
    bx0, by0, bx1, by1 = box
    solid = [b for b in blockers if b[0] < b[2] and b[1] < b[3]]
    forbid = [f for x0, y0, x1, y1 in solid for f in ((x0 - inner, y0, x1 + inner, y1), (x0, y0 - inner, x1, y1 + inner))]
    reach = [(max(x0 - outer, bx0), max(y0 - outer, by0), min(x1 + outer, bx1), min(y1 + outer, by1))
             for x0, y0, x1, y1 in _admissible((bx0 + inner, by0 + inner, bx1 - inner, by1 - inner), forbid)]
    return reach, [(max(x0, bx0), max(y0, by0), min(x1, bx1), min(y1, by1)) for x0, y0, x1, y1 in solid]


def _probe_cover_area(box, blockers, inner, outer):
    """Upper bound of the free area that the sets of ``_probe_cover`` can cover."""
    reach, solid = _probe_cover(box, blockers, inner, outer)
    return _union_area(reach + solid) - _union_area(solid)


def _complement_boxes(polygon):
    """Boxes covering a rectilinear polygon's bounding box less the polygon, else None."""
    slabs = _slabs(polygon)
    if slabs is None:
        return None
    y0, y1 = min(p[1] for p in polygon), max(p[1] for p in polygon)
    columns = {}
    for a0, b0, a1, b1 in slabs:
        columns.setdefault((a0, a1), []).append((b0, b1))
    out = []
    for (a0, a1), spans in sorted(columns.items()):
        cur = y0
        for b0, b1 in sorted(spans):
            if b0 > cur:
                out.append((a0, cur, a1, b0))
            cur = max(cur, b1)
        if cur < y1:
            out.append((a0, cur, a1, y1))
    return out


def _grown_boxes(boxes_, t):
    """Boxes inside the union of the given boxes grown by distance t (each grown along x and along y)."""
    return [b for a0, b0, a1, b1 in boxes_ for b in ((a0 - t, b0, a1 + t, b1), (a0, b0 - t, a1, b1 + t))]


def _square_size(polygon):
    """Largest a such that every point of a rectilinear polygon lies in an a x a axis square inside it, else 0.

    A rigid motion carries each square with the polygon, so the size holds at every
    angle. Candidates are the polygon's coordinate differences, checked exactly
    against its own square cover.
    """
    holes = _complement_boxes(polygon)
    if holes is None:
        return 0
    xs, ys = [p[0] for p in polygon], [p[1] for p in polygon]
    box = (min(xs), min(ys), max(xs), max(ys))
    area = Fraction(abs(area2(polygon)), 2)
    if not holes:
        return min(box[2] - box[0], box[3] - box[1])
    limit = min(box[2] - box[0], box[3] - box[1])
    sizes = sorted({abs(u - v) for coords in (xs, ys) for u in coords for v in coords
                    if 0 < abs(u - v) <= limit}, reverse=True)
    return next((a for a in sizes if _square_cover_area(box, holes, a) == area), 0)


def _grown_area_low(polygons, r):
    """Lower bound of the area a part's courtyards on one side cover when grown by r."""
    if len(polygons) == 1:
        return _swept_area_low(polygons[0], r)
    return sum(Fraction(abs(area2(p)), 2) for p in polygons)


def _board_capacity(capture, outline, fixed, policy, margin_nm):
    """Board-wide capacity at the packing gap, per side, with exact rationals (AGENTEE-249).

    With r = gap/2, the movable courtyards grown by r are pairwise disjoint, clear
    of fixed courtyards and declared obstacles grown by r, and lie in the
    outline's box offset inward by margin - r. Blocked area counts only boxes
    inside those grown blockers (a rectilinear polygon's slabs grown along each
    axis; other polygons count nothing), and, when margin >= r and the outline is
    rectilinear, the outline's box less the outline, grown by margin - r. So
    ``available`` is an upper bound and ``required`` (Steiner lower bounds of the
    grown courtyards) a lower bound, whatever rigid motion each part takes.

    Fragmentation: when every point of a part's courtyards lies in an a x a square
    inside them, at whatever angle the part is turned, every point of its grown
    courtyards lies in that square grown by r: a set holding a disc of radius
    a/2 + r and within a/sqrt(2) + r of its centre (bounded above by an integer
    ceiling), inside the free space. For every square size a among the parts, the
    parts with at least that size must fit in the free area such sets can cover.
    Each row is a necessary condition for every rigid motion; exceeding one is a proof.
    """
    r2, t2 = policy['gap_nm'], 2 * margin_nm - policy['gap_nm']  # Half-nanometre units keep r exact.
    xs, ys = [2 * p[0] for p in outline], [2 * p[1] for p in outline]
    container = (min(xs) + t2, min(ys) + t2, max(xs) - t2, max(ys) - t2)
    edge = _complement_boxes([(2 * x, 2 * y) for x, y in outline]) if t2 >= 0 else None
    sides = {}
    for side in ('F', 'B'):
        blockers, names = [], set()
        sources = [(fp['ref'], _inner(fp['courtyards'][side])) for fp in capture['footprints']
                   if fp['ref'] in fixed and side in fp['courtyards']]
        sources += [(f'obstacle {i}', o['polygons']) for i, o in enumerate(policy['obstacles']) if o['side'] == side]
        for name, polygons in sources:
            for poly in polygons:
                slabs = _slabs([(2 * x, 2 * y) for x, y in poly])
                grown = [b for b in _grown_boxes(slabs or [], r2) if b[0] < container[2] and container[0] < b[2]
                         and b[1] < container[3] and container[1] < b[3]]
                if grown:
                    blockers += grown
                    names.add(name)
        if edge:
            blockers += _grown_boxes(edge, t2)
        parts = [(fp['ref'], min(_square_size(p) for p in _inner(fp['courtyards'][side])),
                  _grown_area_low(_inner(fp['courtyards'][side]), Fraction(r2, 2)))
                 for fp in sorted(capture['footprints'], key=lambda f: f['ref'])
                 if fp['ref'] not in fixed and side in fp['courtyards']]
        if not parts:
            continue
        rows = []
        for a in sorted({0} | {a for _, a, _ in parts}):
            required = sum(area for _, size, area in parts if size >= a)
            if a:
                root = math.isqrt(2 * a * a)  # a * sqrt(2) half-nanometres: a/sqrt(2) nm, rounded up.
                available = _probe_cover_area(container, blockers, a + r2, root + (root * root < 2 * a * a) + r2)
            else:
                available = _square_cover_area(container, blockers, 0)
            available = Fraction(available, 4)
            row = {'square_nm': a, 'references': sorted(ref for ref, size, _ in parts if size >= a),
                   'required_grown_courtyard_area_nm2': _fraction(required),
                   'available_area_upper_nm2': _fraction(available)}
            if available:
                ratio = required / available
                row.update(ratio=_fraction(ratio), ratio_display=f'{math.floor(ratio * 10**6) / 10**6:.6f}')
            rows.append((required > available, row))
        sides[side] = {'blocked_by': sorted(names), 'rows': rows}
    return sides


def _capacity_diagnostic(sides, policy, margin_nm):
    """The capacity rows as a diagnostic: never a pass or fail.

    Each side names its binding constraints with their provenance class, so an
    assumed outline is visible instead of silently blocking (AGENTEE-266)."""
    return {'basis': 'diagnostic-only', 'method': 'gapped-free-area/1', 'gap_nm': policy['gap_nm'],
            'min_courtyard_edge_nm': margin_nm,
            'sides': {side: {'blocked_by': s['blocked_by'], 'free_area': s['rows'][0][1],
                             'fragmentation': [row for _, row in s['rows'][1:]],
                             'binding_constraints': _binding_constraints(policy, margin_nm, s['blocked_by'])}
                      for side, s in sides.items()}}


def _capacity_impossibility(sides, policy):
    for side, s in sides.items():
        for exceeded, row in s['rows']:
            if exceeded:
                kind = 'board_capacity_fragmented' if row['square_nm'] else 'board_capacity_gap'
                return {'kind': kind, 'side': side, 'gap_nm': policy['gap_nm'],
                        'blocked_by': s['blocked_by'], **row}
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
        for peer in placed[:i]:
            if _hole_conflict(fp, peer):
                raise ValueError(f"fixed placement {fp['ref']} conflicts with {peer['ref']} native through-hole reservation")
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


class _BudgetExhausted(Exception):
    pass


class _Search:
    """Candidate scans over the current partial placement (AGENTEE-212).

    Candidates are ranked by (wire cost, x, y, rotation) and the first one that
    passes every predicate is taken: the same choice as the minimum over all
    admissible candidates, with one counted step per candidate submitted to the
    predicates. A rejection records which placed movable parts blocked it, so a
    failed part names the parts a backjump may move.
    """
    def __init__(self, capture, placed, exact, obstacles, outline, margin_nm, policy, members):
        self.policy = policy
        self.fps = {fp['ref']: fp for fp in capture['footprints']}
        self.fixed = {fp['ref'] for fp in placed}
        self.exact, self.obstacles, self.outline, self.margin_nm = exact, obstacles, outline, margin_nm
        self.solid = [(o['side'], slab) for o in exact for poly in o['polygons'] for slab in _slabs(poly) or ()]
        self.gap, self.budget, self.grounds = policy['gap_nm'], policy['step_budget'], policy['ground_nets']
        self.members, self.steps, self.placed, self.reserved = members, 0, [], []
        self.positions, self.peers = {}, {}
        # Anchor distance is symmetric: it binds whichever of the pair is placed second.
        self.anchored = {}
        for ref, group in members.items():
            if 'reference' in group.get('anchor', {}):
                self.anchored.setdefault(group['anchor']['reference'], []).append((ref, group['max_distance_nm']))
        for fp in placed:
            self.push(fp)

    def ties(self, ref):
        """(partner, point, max distance) for every anchor relation whose other end is placed.

        Before its anchor is placed, a member must lie within the sum of both
        distances of each placed member of the same anchor (triangle inequality).
        """
        group, out = self.members.get(ref), []
        if group is not None and 'anchor' in group:
            point = placement_groups.anchor_point(group, self.positions)
            if point is not None:
                out.append((group['anchor'].get('reference'), tuple(point), group['max_distance_nm']))
            else:
                out += [(sibling, tuple(self.positions[sibling]), d + group['max_distance_nm'])
                        for sibling, d in self.anchored[group['anchor']['reference']]
                        if sibling != ref and sibling in self.positions]
        out += [(member, tuple(self.positions[member]), d) for member, d in self.anchored.get(ref, [])
                if member in self.positions]
        return out

    def record(self, backjumps, unplaced=None):
        extra = {} if unplaced is None else {'unplaced': unplaced}
        return {**extra, 'strategy': 'ranked-first-fit-backjump/1', 'backjumps': backjumps}

    def push(self, fp):
        self.placed.append(fp)
        self.positions[fp['ref']] = fp['position_nm']
        if fp['ref'] not in self.fixed:
            self.reserved.extend((side, box, fp['ref']) for side, box in boxes(fp).items())
        for pad in fp['pads']:
            if pad['net']:
                self.peers.setdefault(pad['net'], []).append(tuple(pad['position_nm']))

    def pop(self):
        fp = self.placed.pop()
        del self.positions[fp['ref']]
        while self.reserved and self.reserved[-1][2] == fp['ref']:
            self.reserved.pop()
        for pad in reversed(fp['pads']):
            if pad['net']:
                self.peers[pad['net']].pop()
                if not self.peers[pad['net']]:
                    del self.peers[pad['net']]

    def blockers(self, proposed, group, ties):
        """None when every predicate admits the candidate, else the movable parts that blocked it."""
        hit = {ref for side, (x0, y0, x1, y1) in boxes(proposed).items()
               for other, (a0, b0, a1, b1), ref in self.reserved
               if side == other and not (x1+self.gap <= a0 or a1+self.gap <= x0 or
                                         y1+self.gap <= b0 or b1+self.gap <= y0)}
        if hit:
            return hit
        hole_hits = {fp['ref'] for fp in self.placed if _hole_conflict(proposed, fp)}
        if hole_hits:
            return hole_hits - self.fixed
        if (not _fits(proposed, self.outline, self.margin_nm) or _near(proposed, self.exact, self.gap) or
                _in_keepouts(proposed, self.obstacles)):
            return set()
        hit = [fp['ref'] for fp in self.placed if _in_keepouts(proposed, fp.get('packing_keepouts', [])) or
               _in_keepouts(fp, proposed.get('packing_keepouts', []))]
        if hit:
            return set(hit) - self.fixed
        if group is not None and 'region' in group and not placement_groups.admits(
                proposed, {'region': group['region']}, {}):
            return set()
        if any(placement_groups.distance2(proposed['position_nm'], point) > d*d for _, point, d in ties):
            return set()
        if 'electrical_locality' in self.policy:
            hit, _ = electrical_locality.constraint(self.placed, proposed, self.policy['electrical_locality'])
            if hit:
                return hit - self.fixed - {proposed['ref']}
        return None

    def scan(self, fp, conflicts):
        """Admissible candidates in rank order; rejections add their blockers to ``conflicts``."""
        group = self.members.get(fp['ref'])
        locality = self.policy.get('electrical_locality')
        anchors = [(side, box) for side, box, _ in self.reserved] + [(o['side'], o['box']) for o in self.exact]
        # A concave fixed courtyard's box can lie far from its edges; its slabs carry them.
        anchors += self.solid
        anchors += _keepout_boxes(self.obstacles + [area for f in self.placed for area in f.get('packing_keepouts', [])])
        ties = self.ties(fp['ref'])
        targets = [] if group is None else [p for poly in group.get('region', {}).get('polygons', []) for p in poly]
        targets += [point for _, point, _ in ties]
        window = _window(self.outline, self.margin_nm, group, ties)
        ranked = []
        for rotation in (0, 90, 180, 270):
            at_origin = transform(fp, [0, 0], rotation)
            local_targets = electrical_locality.targets(self.placed, at_origin, locality) if locality else []
            xs, ys = _candidate_axes(fp, rotation, self.outline, anchors, self.gap, self.margin_nm,
                                     targets + local_targets, window)
            rectangles = {side: [r for poly in c['polygons'] for r in _slabs(poly)]
                          for side, c in at_origin['courtyards'].items()
                          if all(_slabs(poly) is not None for poly in c['polygons'])}
            doomed = set()
            for x, y in _unreserved(boxes(at_origin), rectangles, xs, ys, self.reserved, self.solid,
                                    self.gap, conflicts, doomed):
                local_cost = electrical_locality.constraint(
                    self.placed, transform(fp, [x, y], rotation), locality)[1] if locality else 0
                ranked.append((local_cost, _wire_cost(_pad_nets(fp, (x, y), rotation, self.grounds), self.peers),
                               x, y, rotation, (x, y) in doomed))
        ranked.sort()
        for _, _, x, y, rotation, doomed in ranked:
            if self.steps == self.budget:
                raise _BudgetExhausted
            self.steps += 1
            if doomed:
                continue  # Nearer than the gap to a fixed courtyard or obstacle: the exact test rejects it.
            proposed = transform(fp, [x, y], rotation)
            hit = self.blockers(proposed, group, ties)
            if hit is None:
                yield proposed
            else:
                conflicts |= hit
        conflicts.update(partner for partner, _, _ in ties if partner is not None and partner not in self.fixed)

    def place_unit(self, unit):
        """(fps, None) or (None, conflicts): the lead in rank order, each later part first-fit beside it."""
        conflicts = set()
        for lead in self.scan(self.fps[unit[0]], conflicts):
            done = [lead]
            self.push(lead)
            for ref in unit[1:]:
                member = next(self.scan(self.fps[ref], conflicts), None)
                if member is None:
                    break
                done.append(member)
                self.push(member)
            for _ in done:
                self.pop()
            if len(done) == len(unit):
                return done, None
        return None, conflicts - set(unit)


def pack(capture, policy):
    """Return a complete deterministic plan or typed failure; never mutate inputs."""
    capture = copy.deepcopy(capture)
    validate_capture(capture)
    _validate_policy(policy, {f['ref'] for f in capture['footprints']})
    margin_nm = _edge_margin_nm(policy)
    locality = policy.get('electrical_locality')
    if locality:
        initial = electrical_locality.grade(capture, locality)
        if initial['unknown']:
            return {**_failure(capture, policy, 'electrical_locality_unavailable', 'unknown', 0,
                               'pad locality needs unambiguous native pads, limits and citations', margin_nm),
                    'locality_grading': initial}
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
        return _soft_proof({**failure, 'proof': proof}, _binding_constraints({**policy, 'gap_nm': 0}, 0))
    proof = _part_impossibility(capture, outline, bands)
    if proof:
        failure = _failure(capture, policy, 'packing_infeasible', 'defect', 0,
                           f"{proof['ref']} courtyard {proof['kind'].replace('_', ' ')} exceeds the board's", margin_nm)
        return _soft_proof({**failure, 'proof': proof}, _binding_constraints({**policy, 'gap_nm': 0}, 0))
    try:
        fixed, placed, exact = _initial_reservations(capture, policy, outline, obstacles, bands, members)
    except ValueError as exc:
        return _failure(capture, policy, 'fixed_conflict', 'unknown', 0, str(exc), margin_nm)
    if locality:
        fixed_grade = electrical_locality.grade({'footprints': placed}, locality)
        if fixed_grade['defects']:
            return {**_failure(capture, policy, 'fixed_conflict', 'unknown', 0,
                               'fixed pads violate electrical locality', margin_nm), 'locality_grading': fixed_grade}
    priorities = {ref: i for i, ref in enumerate(policy['priority_refs'])}
    # Parts bound to a declared anchor point come first, tightest distance first: each
    # has only a small disc of positions. Then a movable group anchor is placed before
    # every member (anchors are never anchored members), and its members directly after
    # it, so a cluster claims its space first.
    leaders = {g['anchor']['reference'] for g in groups if 'reference' in g.get('anchor', {})}
    by_ref = {fp['ref']: fp for fp in capture['footprints']}
    def rank(f):
        return f['ref'] not in leaders, priorities.get(f['ref'], len(priorities)), -len(f['pads']), f['ref']
    def cluster(f):
        group = members.get(f['ref'], {})
        if 'point_nm' in group.get('anchor', {}):
            return (0, group['max_distance_nm']), rank(f), 0, rank(f)
        anchor = group.get('anchor', {}).get('reference')
        if anchor is None or anchor in fixed:
            return (1,), rank(f), 0, rank(f)
        return (1,), rank(by_ref[anchor]), 1, rank(f)
    queue = sorted((fp for fp in capture['footprints'] if fp['ref'] not in fixed), key=cluster)
    proof = _region_impossibility(capture, outline, groups, fixed, placed, obstacles, policy)
    if proof:
        failure = _failure(capture, policy, 'packing_infeasible', 'defect', 0,
                           f"group {proof['group']} region cannot hold its {proof['side']} courtyards", margin_nm)
        binding = _binding_constraints({**policy, 'gap_nm': proof.get('gap_nm', 0)}, 0, proof['blocked_by'])
        return _soft_proof({**failure, 'proof': proof}, binding)
    sides = _board_capacity(capture, outline, fixed, policy, margin_nm)
    capacity = _capacity_diagnostic(sides, policy, margin_nm)
    # Opt-in: before AGENTEE-249 such inputs ended as search failures (unknown).
    proof = _capacity_impossibility(sides, policy) if policy.get('capacity_proof') else None
    if proof:
        failure = _failure(capture, policy, 'packing_infeasible', 'defect', 0,
                           f"{proof['side']} courtyards grown by half the gap exceed the board's free area"
                           + (f" usable by {proof['square_nm']} nm squares" if proof['square_nm'] else ''), margin_nm)
        return _soft_proof({**failure, 'proof': proof, 'capacity': capacity},
                           _binding_constraints(policy, margin_nm, proof['blocked_by']))
    search = _Search(capture, placed, exact, obstacles, outline, margin_nm, policy, members)
    units = [(fp['ref'],) for fp in queue]
    stack, seen, backjumps, failures, peak = [], {tuple(units)}, 0, {}, 0
    def stopped(status, reason, unplaced=None):
        failure = _failure(capture, policy, status, 'unknown', search.steps, reason, margin_nm)
        record = search.record(backjumps, unplaced)
        if unplaced is not None:
            record['placed_peak'] = peak  # Diagnostic only: a plan is complete or empty.
        return {**failure, 'search': record, 'capacity': capacity}
    while len(stack) < len(units):
        unit = units[len(stack)]
        try:
            fps, conflicts = search.place_unit(unit)
        except _BudgetExhausted:
            return stopped('packing_budget_exhausted', 'deterministic candidate budget exhausted', unit[0])
        if fps is not None:
            for fp in fps:
                search.push(fp)
            stack.append(fps)
            peak = max(peak, sum(map(len, stack)))
            continue
        # Backjump to the latest placed part that blocked the unit and place the unit
        # before it; a member blocked through its anchor leads the anchor's unit, so
        # the anchor is placed beside it. The n-th failure of the same part starts
        # at its 2**(n-1)-th latest blocker; an order already tried jumps further
        # back; every order is tried at most once.
        index = {fp['ref']: k for k, done in enumerate(stack) for fp in done}
        culprits = sorted({index[ref] for ref in conflicts if ref in index}, reverse=True)
        if not culprits:
            return stopped('packing_search_exhausted',
                           f'no candidate for {unit[0]} in the finite search; infeasibility unproved', unit[0])
        i = len(stack)
        anchor = members.get(unit[0], {}).get('anchor', {}).get('reference')
        failures[unit[0]] = failures.get(unit[0], 0) + 1
        for k in culprits[min(2 ** (failures[unit[0]] - 1), len(culprits)) - 1:]:
            if len(unit) == 1 and anchor in units[k]:
                reordered = units[:k] + [unit + units[k]] + units[k+1:i] + units[i+1:]
            else:
                reordered = units[:k] + [unit] + units[k:i] + units[i+1:]
            if tuple(reordered) not in seen:
                break
        else:
            return stopped('packing_search_exhausted',
                           f'every backjump for {unit[0]} revisits a part order; infeasibility unproved', unit[0])
        if backjumps == policy['step_budget']:
            return stopped('packing_budget_exhausted', 'deterministic backjump budget exhausted', unit[0])
        seen.add(tuple(reordered))
        units, backjumps = reordered, backjumps + 1
        for done in reversed(stack[k:]):
            for _ in done:
                search.pop()
        del stack[k:]
    placed, steps = search.placed, search.steps
    proposed_capture = {**capture, 'footprints': placed}
    judgment = judge(proposed_capture, overhangs=policy.get('fixed_overhangs'))
    judgment['basis'] = 'transformed-proposal-not-native'
    if judgment['verdict'] != 'pass':
        return stopped('judge_rejected', 'independent full-polygon judge rejected complete proposal')
    grading = placement_groups.grade(proposed_capture, groups) if groups else None
    if grading is not None and grading['status'] != 'pass':
        return stopped('judge_rejected', 'declared placement groups not satisfied by the complete proposal')
    extra = {} if grading is None else {'group_grading': grading}
    if locality:
        extra['locality_grading'] = electrical_locality.grade(proposed_capture, locality)
        if extra['locality_grading']['status'] != 'pass':
            return {**stopped('judge_rejected', 'complete proposal failed pad locality'), **extra}
    custom_pads = [{'reference': f['ref'], 'uuid': p['uuid'], 'number': p['number']}
                   for f in capture['footprints'] for p in f['pads'] if p['shape'] == 6]
    if custom_pads:
        extra['qualified_custom_pads'] = custom_pads
    return {**extra, 'search': search.record(backjumps), 'capacity': capacity, 'schema': 'agent-ee.packing-plan/1',
            'board_sha256': capture['board_sha256'], 'status': 'planned', 'outcome': 'info', 'authority': 'standalone-unsealed-plan',
            'steps_used': steps, 'step_budget': policy['step_budget'],
            'min_courtyard_edge_mm': _margin_mm(margin_nm),
            'policy': copy.deepcopy(policy), 'pad_shapes': pad_shape_qualification(), 'judgment': judgment,
            'placements': [{'ref': f['ref'], 'uuid': f['uuid'], 'position_nm': f['position_nm'],
                            'rotation_deg': f['rotation_deg'], 'fixed': f['ref'] in fixed}
                           for f in sorted(placed, key=lambda f: f['ref'])]}
