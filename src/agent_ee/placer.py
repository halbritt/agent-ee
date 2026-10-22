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
import heapq
from decimal import Decimal
from fractions import Fraction
import math
import time

from . import (access_direction, advisory_regions, electrical_locality, placement_groups, placement_provenance,
               placement_trace)
from .placement import (area2, boxes_meet, convex, courtyard_bounds, cross, edges, intersection_area, judge,
                        on_segment, overhang_bands, qualified_polygons, validate_capture)
from .placement_predicates import segment_distance2
from .symbol_library import LibraryError, nanometres


# Native pcbnew PAD_SHAPE values, read from the KiCad 10.0.7 bindings.
# TRAPEZOID (3) is unqualified; CUSTOM (6) requires per-pad native evidence.
PAD_SHAPES = {0: 'circle', 1: 'rect', 2: 'oval', 4: 'roundrect', 5: 'chamfered_rect'}
PAD_SHAPE_PCBNEW_MAJOR = '10'
# AGENTEE-331 reports edge clearance to 4 decimals of a millimetre: below 50 nm
# touching cannot be told from crossing (design_rule_provenance), a structural defect.
COPPER_REPORT_WINDOW_NM = 50
# Every copper layer: a '*.Cu' pad or rule area (AGENTEE-418).
ALL_COPPER = frozenset({'*.Cu'})


def rotate(point, quarter_turns):
    x, y = point
    for _ in range(quarter_turns % 4):
        x, y = y, -x  # KiCad positive rotation, in board coordinates.
    return [x, y]


def _copy_footprint(fp):
    """A structural copy of a footprint record for transform() to write into.

    Every container transform() rewrites (courtyards, keepouts, packing pad
    geometry entries, pads and a proven custom-pad geometry) is copied one level
    deep; the leaves it leaves unchanged are shared with the source, which the
    search, judge, graders and writer only read. The ephemeral box caches are
    rebuilt by _cache_pad_reservations. The result equals copy.deepcopy(fp) with
    the cache keys removed (AGENTEE-332: deepcopy dominated per-candidate cost);
    the derived copper setback is a board fact, not a cache, and is kept.
    """
    placed = {key: value for key, value in fp.items() if not key.startswith('_packing_')}
    if '_packing_copper_setback' in fp:
        placed['_packing_copper_setback'] = fp['_packing_copper_setback']
    placed['courtyards'] = {side: dict(courtyard) for side, courtyard in fp['courtyards'].items()}
    for key in ('packing_keepouts', 'packing_pad_keepouts'):
        if key in fp:
            placed[key] = [dict(keepout) for keepout in fp[key]]
    geometry = fp.get('packing_pad_geometry')
    if isinstance(geometry, dict):
        placed['packing_pad_geometry'] = {**geometry, **{kind: [dict(entry) for entry in geometry[kind]]
                                                         for kind in ('copper', 'holes') if kind in geometry}}
    placed['pads'] = [dict(pad) for pad in fp['pads']]
    return placed


def _move_geometry(placed, move):
    for courtyard in placed['courtyards'].values():
        for key in ('polygons', 'inner_polygons'):
            if key in courtyard:
                courtyard[key] = [[move(p) for p in poly] for poly in courtyard[key]]
    for keepout in placed.get('packing_keepouts', []) + placed.get('packing_pad_keepouts', []):
        keepout['polygons'] = [[move(p) for p in poly] for poly in keepout['polygons']]
    for kind in ('copper', 'holes'):
        for entry in placed.get('packing_pad_geometry', {}).get(kind, []):
            entry['polygons'] = [[move(p) for p in poly] for poly in entry['polygons']]


def transform(fp, position, rotation):
    placed = _copy_footprint(fp)
    origin = fp['position_nm']
    turns = int((rotation-fp['rotation_deg']) / 90)
    def move(p):
        x, y = rotate([p[0]-origin[0], p[1]-origin[1]], turns)
        return [x+position[0], y+position[1]]
    _move_geometry(placed, move)
    for pad in placed['pads']:
        custom = pad.get('custom_geometry', {})
        if not isinstance(custom, dict):
            raise ValueError('custom_geometry must be an object')
        if custom.get('status') == 'proven':
            box = custom['bbox_nm']
            corners = [move([x, y]) for x in (box[0], box[2]) for y in (box[1], box[3])]
            pad['custom_geometry'] = {**custom, 'bbox_nm': [min(p[0] for p in corners), min(p[1] for p in corners),
                                                            max(p[0] for p in corners), max(p[1] for p in corners)]}
        if 'copper_centroid_offset_nm' in pad:
            offset = rotate([Fraction(*v) for v in pad['copper_centroid_offset_nm']], turns)
            pad['copper_centroid_offset_nm'] = [[v.numerator, v.denominator] for v in offset]
        if 'copper_land_offset_nm' in pad:
            pad['copper_land_offset_nm'] = rotate(pad['copper_land_offset_nm'], turns)
        pad['position_nm'] = move(pad['position_nm'])
        pad['rotation_deg'] = (pad['rotation_deg'] + rotation-fp['rotation_deg']) % 360
    placed.update(position_nm=list(position), rotation_deg=rotation)
    _cache_pad_reservations(placed)
    return placed


def _translate(at_origin, position):
    """transform(fp, position, rotation) computed from transform(fp, [0, 0], rotation).

    Identical output: every moved point is its rotated point plus the position,
    a translated box is the box of the translated corners, and pad rotations and
    centroid offsets are already rotated. No rotation pass runs per candidate.
    """
    dx, dy = position
    def move(p):
        return [p[0]+dx, p[1]+dy]
    placed = _copy_footprint(at_origin)
    _move_geometry(placed, move)
    for pad in placed['pads']:
        custom = pad.get('custom_geometry', {})
        if custom.get('status') == 'proven':
            box = custom['bbox_nm']
            pad['custom_geometry'] = {**custom, 'bbox_nm': [box[0]+dx, box[1]+dy, box[2]+dx, box[3]+dy]}
        pad['position_nm'] = move(pad['position_nm'])
    placed['position_nm'] = list(position)
    _cache_pad_reservations(placed)
    # A rigid translation keeps which copper boxes leave the courtyards.
    if '_packing_copper' in at_origin:
        placed['_packing_copper'] = _shift_copper(at_origin['_packing_copper'], dx, dy)
    if '_packing_lands' in at_origin:
        placed['_packing_lands'] = _shift_lands(at_origin['_packing_lands'], dx, dy)
    return placed


def _box(polygons):
    xs = [p[0] for poly in polygons for p in poly]
    ys = [p[1] for poly in polygons for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def boxes(fp):
    return {side: _box(courtyard['polygons']) for side, courtyard in fp['courtyards'].items()}


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


def _obstacle_polygons(raw):
    """Reserve the union by testing every member; shared edges never open a gap.

    Unlike courtyard contours, declared obstacles may overlap or touch. Qualify
    each member with the shared validator, keeping its syntax and work limits.
    """
    if not isinstance(raw, list) or not raw or len(raw) > 64:
        raise ValueError('missing or excessive polygons')
    return [qualified_polygons({'status': 'proven', 'polygons': [p]})[0] for p in raw]


def _validate_policy(policy, refs):
    if policy.get('schema') != 'agent-ee.packing-policy/1':
        raise ValueError('unsupported packing policy')
    if type(policy.get('step_budget')) is not int or not 1 <= policy['step_budget'] <= 1_000_000:
        raise ValueError('step budget must be 1..1000000')
    if type(policy.get('gap_nm')) is not int or policy['gap_nm'] < 0:
        raise ValueError('gap must be nonnegative integer nanometres')
    _edge_margin_nm(policy)
    timeout = policy.get('search_timeout_s')
    if 'search_timeout_s' in policy and (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                                       or not 0 < timeout <= 86400):
        raise ValueError('search_timeout_s must be positive and at most 86400 seconds')
    if type(policy.get('capacity_proof', False)) is not bool:
        raise ValueError('capacity_proof must be a boolean')
    if type(policy.get('carry_seed_fixed_pairs', False)) is not bool:
        raise ValueError('carry_seed_fixed_pairs must be a boolean')
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
        _obstacle_polygons(obstacle['polygons'])
        placement_provenance.classify(obstacle, 'provenance', 'obstacle')
    overhangs = policy.get('fixed_overhangs', [])
    if not isinstance(overhangs, list) or any(not isinstance(o, dict) or o.get('reference') not in refs
                                              for o in overhangs):
        raise ValueError('fixed_overhangs must declare known references')
    if 'electrical_locality' in policy:
        electrical_locality.validate(policy['electrical_locality'])
    if 'groups' in policy:
        placement_groups.validate(policy['groups'], refs)
    if 'advisory_regions' in policy:
        # Reported on the complete placement only; search never reads them (AGENTEE-436).
        advisory_regions.validate(policy['advisory_regions'], refs)
    if 'constraint_provenance' in policy:
        _validate_provenance(policy['constraint_provenance'])
        for name, actual in (('gap_nm', policy['gap_nm']),
                             ('min_courtyard_edge_mm', _edge_margin_nm(policy))):
            source = policy['constraint_provenance'].get(name)
            if source is not None and source['value_nm'] != actual:
                raise ValueError(f'constraint_provenance {name} value differs from policy')


PROVENANCE_CLASSES = ('owner_stated', 'derived', 'assumed', 'unknown')
UNDECLARED = {'class': 'unknown', 'enforcement': 'hard',
              'reason': 'no envelope provenance supplied; enforced as before, never treated as owner-stated'}


def _validate_provenance(value):
    """Envelope limits (AGENTEE-266) and caller-qualified gap sources (AGENTEE-338)."""
    if not isinstance(value, dict) or set(value) - {'outline', 'fixed', 'gap_nm', 'min_courtyard_edge_mm'} or not isinstance(value.get('fixed', {}), dict):
        raise ValueError('constraint_provenance declares only outline, fixed, gap_nm and min_courtyard_edge_mm')
    for name in ('gap_nm', 'min_courtyard_edge_mm'):
        if name not in value:
            continue
        gap = value[name]
        placement_provenance.sourced(gap, f'constraint_provenance {name}',
                                      gap.get('value_nm', 0) if isinstance(gap, dict) else 0)
    entries = ([value['outline']] if 'outline' in value else []) + list(value.get('fixed', {}).values())
    for entry in entries:
        provenance_entry(entry)


def provenance_entry(entry):
    """One outline or fixed-part provenance entry's class, enforcement and soft reason."""
    if (not isinstance(entry, dict) or not isinstance(entry.get('class'), str)
            or entry['class'] not in PROVENANCE_CLASSES
            or not isinstance(entry.get('enforcement'), str) or entry['enforcement'] not in ('hard', 'soft')
            or (entry['enforcement'] == 'soft') != isinstance(entry.get('soft_reason'), str)
            or (entry['class'] == 'assumed' and entry['enforcement'] != 'soft')
            or (entry['class'] == 'unknown' and entry['enforcement'] != 'hard')):
        raise ValueError('constraint_provenance entries need a class, a hard/soft enforcement and a soft reason')


def _binding_constraints(policy, margin_nm, blocked_by=()):
    """The limits a capacity row or proof binds, each with its provenance class (AGENTEE-266).

    Fixed parts and obstacles bind only where they block; the edge margin and gap come
    from the packing policy or a fab profile. Gaps need an explicit source to establish a defect."""
    declared = policy.get('constraint_provenance', {})
    fixed = declared.get('fixed', {})
    out = [{'constraint': 'outline', **declared.get('outline', UNDECLARED)}]
    for name in blocked_by:
        if name.startswith('obstacle '):
            obstacle = policy['obstacles'][int(name.split()[1])]
            entry = placement_provenance.classify(obstacle, 'provenance', name)
        elif ' ' in name:
            entry = {'class': 'board', 'enforcement': 'hard', 'reason': 'captured native keepout geometry'}
        else:
            entry = fixed.get(name, UNDECLARED)
            name = f'fixed {name}'
        out.append({'constraint': name, **entry})
    if margin_nm:
        out.append({'constraint': 'min_courtyard_edge_mm', **declared.get('min_courtyard_edge_mm', {
            'class': 'unknown', 'enforcement': 'soft',
            'soft_reason': 'unsourced courtyard edge margin is advisory'})})
    if policy['gap_nm']:
        out.append({'constraint': 'gap_nm', **declared.get('gap_nm', UNDECLARED)})
    return out


def _soft_proof(failure, binding):
    """A proof against a soft (assumed or owner-weakened) limit is not a design defect.

    It stays a proof about that limit, reported as unknown with the limits the owner
    must confirm; an unsourced gap is advisory even though search enforces it."""
    soft = [b if b['enforcement'] == 'soft' else
            {**b, 'soft_reason': 'unsourced packing gap is advisory'}
            for b in binding if b['enforcement'] == 'soft'
            or (b['constraint'] == 'gap_nm' and b['class'] == 'unknown')]
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
    setback = copper_setback(capture)
    def drilled(p):
        return p.get('attribute') in ('PTH', 'NPTH') or any(p.get('drill_size_nm', []))

    has_holes = any(isinstance(p, dict) and drilled(p)
                    for f in capture['footprints'] for p in f['pads'])
    for fp in capture['footprints']:
        # Reservation caches are derived here from qualified inventories only, never taken from input.
        for key in [key for key in fp if key.startswith('_packing_')]:
            del fp[key]
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
            required = {p['uuid'] for p in fp['pads'] if drilled(p)}
            drills = [entry.get('pad_uuid') for entry in geometry['holes']
                      if entry.get('reservation_kind') == 'drill']
            if len(drills) != len(set(drills)) or set(drills) != required:
                raise ValueError(f"{fp['ref']}: native packing per-pad drill reservations incomplete")
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
            if not isinstance(custom, dict):
                raise ValueError(f"{fp['ref']} pad {pad.get('number')!r}: custom_geometry must be an object")
            qualified_custom = (pad.get('shape') == 6 and isinstance(custom, dict)
                                and custom.get('status') == 'proven'
                                and isinstance(custom.get('primitives'), list) and custom['primitives']
                                and isinstance(custom.get('merged_polygons_nm'), list) and custom['merged_polygons_nm']
                                and isinstance(custom.get('bbox_nm'), list) and len(custom['bbox_nm']) == 4
                                and all(type(v) is int for v in custom['bbox_nm'])
                                and custom['bbox_nm'][0] < custom['bbox_nm'][2]
                                and custom['bbox_nm'][1] < custom['bbox_nm'][3])
            if custom.get('status') == 'proven' and not qualified_custom:
                raise ValueError(f"{fp['ref']} pad {pad.get('number')!r}: custom_geometry not qualified")
            if type(pad.get('shape')) is not int or (pad['shape'] not in PAD_SHAPES and not qualified_custom):
                raise ValueError(f"{fp['ref']} pad {pad.get('number')!r}: native pad shape "
                                 f"{pad.get('shape')!r} not qualified for packing")
            if type(pad.get('rotation_deg')) not in (int, float) or pad['rotation_deg'] % 90:
                raise ValueError(f"{fp['ref']} pad {pad.get('number')!r}: pad rotation must be a quarter turn")
        fp['_packing_copper_setback'] = setback
        _cache_pad_reservations(fp)
        _cache_copper_extent(fp)
    # A hole excludes every peer's courtyard and copper: a peer without a native
    # target inventory leaves the exclusion unproven, which is unknown, not clear.
    geometries = [fp.get('packing_pad_geometry') for fp in capture['footprints']]
    if any(g is not None and g['holes'] for g in geometries) and any(g is None for g in geometries):
        raise ValueError('native packing target reservations missing')
    return outline[0]


def _copper_layers(layers):
    """The copper layers a rule area or pad spans: a set of names, or ALL_COPPER for every copper layer."""
    return ALL_COPPER if '*.Cu' in layers else frozenset(l for l in layers if l.endswith('.Cu'))


def _layers_meet(a, b):
    return bool(a) and bool(b) and (a == ALL_COPPER or b == ALL_COPPER or bool(a & b))


def _keepouts(capture, labels=None, pads=None):
    """Reuse the native rule-area inventory; unqualified exclusions stop packing.

    ``labels``, when given, receives per owner ('board' or a reference) the
    name of each exclusion, parallel to the board obstacles or that part's
    ``packing_keepouts``, for typed findings.

    A footprint exclusion binds a footprint on its own side's outer copper
    layer only: KiCad 10 never reports a footprint against an inner-layer-only
    area (AGENTEE-418, NativeRuleAreaSemanticsTest). A rule area forbidding
    pads binds every pad whose copper layers meet its layers, inner layers
    included; each part records those it owns as ``packing_pad_keepouts`` and
    the board's are appended to ``pads``, which every caller must check
    (_copper_escapes, fixed_input_copper). Native extraction always records
    ``forbid.pads``; a record without it is not evidence that pads are
    permitted, so a missing or non-boolean flag stops packing as unknown
    (ruling 418b).
    """
    labels = {} if labels is None else labels
    pads = [] if pads is None else pads
    owners = {fp['uuid']: fp for fp in capture['footprints']}
    labels['board'] = []
    for fp in owners.values():
        fp['packing_keepouts'] = []
        fp['packing_pad_keepouts'] = []
        labels[fp['ref']] = []
        if access_direction.hard(fp):
            _, _, _, lane = access_direction.geometry(fp)
            fp['packing_keepouts'].append({'side': fp['side'], 'polygons': lane})
            labels[fp['ref']].append(f"{fp['ref']} access lane")
    obstacles = []
    areas = capture.get('rule_areas', [])
    if not isinstance(areas, list):
        raise ValueError('malformed rule-area inventory')
    for area in areas:
        if (not isinstance(area, dict) or not isinstance(area.get('forbid'), dict) or
                type(area['forbid'].get('footprints')) is not bool):
            raise ValueError('malformed rule-area footprint flag')
        if 'pads' not in area['forbid']:
            raise ValueError('missing rule-area pad flag')
        if type(area['forbid']['pads']) is not bool:
            raise ValueError('malformed rule-area pad flag')
        if not area['forbid']['footprints'] and not area['forbid']['pads']:
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
        if area['forbid']['pads']:
            entry = {'layers': _copper_layers(layers), 'polygons': polygons, 'name': f"rule area {area.get('uuid')}"}
            (pads if owner == 'board' else owners[owner]['packing_pad_keepouts']).append(entry)
        if not area['forbid']['footprints']:
            continue
        target = obstacles if owner == 'board' else owners[owner]['packing_keepouts']
        names = labels['board' if owner == 'board' else owners[owner]['ref']]
        for side in ('F', 'B'):
            if side + '.Cu' in layers or '*.Cu' in layers:
                target.append({'side': side, 'polygons': polygons})
                names.append(f"rule area {area.get('uuid')}")
    return obstacles


def _keepout_boxes(obstacles):
    return [entry for obstacle in obstacles for entry in
            boxes({'courtyards': {obstacle['side']: obstacle}}).items()]


def _overlaps(left, right):
    """intersection_area(left, right) > 0, exactly, for qualified polygons.

    Boxes that do not overlap with positive area share no area. Two convex
    counter-clockwise polygons share positive area exactly when neither has an
    edge with every vertex of the other on or right of it (a separating edge
    line exists for convex polygons with disjoint interiors, and a shared
    boundary alone puts the other polygon on the edge's closed right side).
    Any other pair keeps the exact rational clipping.
    """
    if not boxes_meet([left], [right]):
        return False
    if convex(left) and convex(right):
        return not any(all(cross(a, b, v) <= 0 for v in other)
                       for own, other in ((left, right), (right, left)) for a, b in edges(own))
    return intersection_area(left, right) > 0


def _in_keepouts(fp, obstacles):
    return any(_overlaps(c, p)
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
        return [(_box([p]), p) for p in polygons]

    fp['_packing_holes'] = inventory(p for e in geometry['holes'] for p in e['polygons'])
    fp['_packing_targets'] = inventory([p for c in fp['courtyards'].values() for p in c['polygons']] +
                                       [p for e in geometry['copper'] for p in e['polygons']])
    # Union boxes: two inventories whose unions do not meet have no meeting pair.
    for key in ('_packing_holes', '_packing_targets'):
        fp[key + '_box'] = _box([[(x0, y0), (x1, y1)] for (x0, y0, x1, y1), _ in fp[key]]) if fp[key] else None


def _hole_conflict(candidate, peer):
    """Symmetric PTH/NPTH exclusion, including copper outside courtyards."""
    for owner, other in ((candidate, peer), (peer, candidate)):
        holes = owner.get('_packing_holes', [])
        if not holes:
            continue
        # Qualification proves every peer's target inventory; a missing one fails loudly here.
        (x0, y0, x1, y1), targets = owner['_packing_holes_box'], other['_packing_targets_box']
        if targets is None or x1 <= targets[0] or targets[2] <= x0 or y1 <= targets[1] or targets[3] <= y0:
            continue
        for (x0, y0, x1, y1), polygon in holes:
            for (a0, b0, a1, b1), target in other['_packing_targets']:
                if x1 <= a0 or a1 <= x0 or y1 <= b0 or b1 <= y0:
                    continue
                if _overlaps(polygon, target):
                    return True
    return False


def _copper_box(fp, pad):
    """Integer box containing the pad's copper land, or None for a hand-built record without native land facts.

    A proven custom pad carries its native box (anchor and every primitive). A
    symmetric stock land is its size, quarter-turned, about the native shape
    centre (anchor plus copper centroid offset); a circle is native, its X
    diameter on both axes. A chamfered land contains its area centroid, so the
    box twice its size about that centroid contains it. A land whose centroid
    native extraction could not qualify (copper_centroid_reason) is bounded by
    its size box about its native land offset when extraction proved one, and
    otherwise has no proven extent: that, or a partial land record, raises, so
    missing copper extent is never taken as no constraint. Rational bounds
    round outward.
    """
    custom = pad.get('custom_geometry', {})
    if pad['shape'] == 6 and custom.get('status') == 'proven':
        return tuple(custom['bbox_nm'])
    # Native extraction records a centroid or the reason it has none for every
    # pad; a record with neither is a hand-built one whose size alone places nothing.
    if all(key not in pad for key in ('copper_centroid_offset_nm', 'copper_centroid_reason', 'copper_land_offset_nm')):
        return None
    size, offset, land = pad.get('size_nm'), pad.get('copper_centroid_offset_nm'), pad.get('copper_land_offset_nm')
    where = f"{fp['ref']} pad {pad.get('number')!r}"
    if (not isinstance(size, list) or len(size) != 2 or any(type(v) is not int or v < 0 for v in size)
            or type(pad.get('position_nm')) is not list or len(pad['position_nm']) != 2
            or any(type(v) is not int for v in pad['position_nm'])):
        raise ValueError(f'{where}: copper land size or centroid malformed')
    w, h = size if pad['rotation_deg'] % 180 == 0 else size[::-1]
    if pad['shape'] == 0:
        w = h = size[0]
    if offset is not None:
        if (not isinstance(offset, list) or len(offset) != 2
                or any(not isinstance(v, list) or len(v) != 2 or any(type(t) is not int for t in v) or v[1] <= 0
                       for v in offset)):
            raise ValueError(f'{where}: copper land size or centroid malformed')
        reach = 1 if pad['shape'] == 5 else Fraction(1, 2)
        cx, cy = (p + Fraction(*v) for p, v in zip(pad['position_nm'], offset))
    elif land is not None and pad['shape'] == 5:
        if not isinstance(land, list) or len(land) != 2 or any(type(v) is not int for v in land):
            raise ValueError(f'{where}: copper land offset malformed')
        reach = Fraction(1, 2)
        cx, cy = (p + v for p, v in zip(pad['position_nm'], land))
    else:
        raise ValueError(f"{where}: copper extent unproven ({pad.get('copper_centroid_reason', 'no native land fact')})")
    return (math.floor(cx - reach*w), math.floor(cy - reach*h), math.ceil(cx + reach*w), math.ceil(cy + reach*h))


def _copper_sides(pad):
    """Outer copper sides of a pad; a record without layers is taken to reach both."""
    layers = pad.get('layers')
    if layers is None:
        return ('F', 'B'), True
    if not isinstance(layers, list) or any(not isinstance(l, str) for l in layers):
        raise ValueError(f"pad {pad.get('number')!r}: malformed layers")
    sides = tuple(s for s in ('F', 'B') if s + '.Cu' in layers or '*.Cu' in layers)
    return sides, any(l.endswith('.Cu') for l in layers)


def copper_setback(capture):
    """Structural copper-to-outline setback S in nm, or None when the facts lack it (AGENTEE-366e).

    S = the saved board's m_MaxError (the ERROR_OUTSIDE arc inflation KiCad
    applies when it polygonises pads; placement_probe records it as
    ``max_error_nm``) plus the AGENTEE-331 reporting window. It is a derived
    structural tolerance, not an engineering clearance: copper closer than S to
    the outline is reported by the native publication gate as touching it.
    """
    if 'max_error_nm' not in capture:
        return None
    value = capture['max_error_nm']
    if type(value) is not int or value < 0:
        raise ValueError('board max_error_nm must be a nonnegative integer')
    return value + COPPER_REPORT_WINDOW_NM


def copper_setback_record(capture):
    """The setback and its sources, for a plan's search record."""
    setback = copper_setback(capture)
    return {'setback_nm': setback, 'max_error_nm': setback - COPPER_REPORT_WINDOW_NM,
            'report_window_nm': COPPER_REPORT_WINDOW_NM,
            'source': 'saved board m_MaxError (ERROR_OUTSIDE) plus the AGENTEE-331 edge-clearance reporting window'}


def copper_setback_missing(footprints):
    """Typed-unknown reason when a part has bounded copper and the board setback is unknown."""
    refs = sorted(fp['ref'] for fp in footprints
                  if fp.get('_packing_copper', {}).get('outline') and fp.get('_packing_copper_setback') is None)
    if refs:
        return (f"copper setback unknown: the placement facts carry no board max_error_nm (m_MaxError), "
                f"so the copper of {', '.join(refs)} is not proven to stay the setback inside the outline")
    return None


def _inflate(poly, by):
    x0, y0, x1, y1 = _box([poly])
    return [(x0 - by, y0 - by), (x1 + by, y0 - by), (x1 + by, y1 + by), (x0 - by, y1 + by)]


def _cache_copper_extent(fp):
    """Ephemeral copper boxes that leave the courtyards (AGENTEE-366).

    ``outline`` holds every copper box that lies in no courtyard polygon of the
    part at the copper setback S (the box grown by S fits the courtyard): such a
    box is at least S inside the outline whenever that courtyard is inside it.
    Without a known S no courtyard discharges it: every box is kept, and the
    search is typed unknown (copper_setback_missing). A hand-built record with
    no native land facts has no box (_copper_box) and caches nothing.
    ``sides`` holds, per outer copper side, the boxes outside that side's
    courtyards, for that side's footprint keep-outs. With S known, a part whose
    copper lies in its courtyards caches nothing, and its search is the
    courtyard-only one.

    ``_packing_lands`` holds, for pad keep-outs (AGENTEE-418), every copper
    land as (copper layers, box, proven); a hand-built land without native
    facts is bounded by the part's courtyards instead, unproven.
    """
    fp.pop('_packing_copper', None)
    fp.pop('_packing_lands', None)
    setback = fp.get('_packing_copper_setback')
    polygons = [(side, p) for side, c in fp['courtyards'].items() for p in c['polygons']]
    outline, sides, lands = [], {}, []
    for pad in fp['pads']:
        on, copper = _copper_sides(pad)
        box = _copper_box(fp, pad) if copper else None
        if copper:
            layers = ALL_COPPER if pad.get('layers') is None else _copper_layers(pad['layers'])
            if box is None:
                lands += [(layers, p, False) for _, p in polygons]
            elif box[0] < box[2] and box[1] < box[3]:
                x0, y0, x1, y1 = box
                lands.append((layers, [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], True))
        if box is None or box[0] == box[2] or box[1] == box[3]:
            continue
        x0, y0, x1, y1 = box
        poly = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        inside = {side for side, p in polygons if _fits({'courtyards': {'': {'polygons': [poly]}}}, p)}
        if setback is None or not any(_fits({'courtyards': {'': {'polygons': [_inflate(poly, setback)]}}}, p)
                                      for _, p in polygons):
            outline.append(poly)
        for side in on:
            if side not in inside:
                sides.setdefault(side, {'polygons': []})['polygons'].append(poly)
    if outline or sides:
        fp['_packing_copper'] = {'outline': outline, 'sides': sides}
    if lands:
        fp['_packing_lands'] = lands


def _shift_lands(lands, dx, dy):
    return [(layers, [(x + dx, y + dy) for x, y in poly], proven) for layers, poly, proven in lands]


def _shift_copper(copper, dx, dy):
    def move(poly):
        return [(x + dx, y + dy) for x, y in poly]
    return {'outline': [move(p) for p in copper['outline']],
            'sides': {side: {'polygons': [move(p) for p in c['polygons']]} for side, c in copper['sides'].items()}}


def _copper_meets(fp, peer):
    """Copper of ``fp`` outside its courtyards meets a courtyard, or such copper, of ``peer`` on its side."""
    copper = fp.get('_packing_copper')
    if copper is None or not copper['sides']:
        return False
    targets = [{'side': side, 'polygons': c['polygons']} for side, c in peer['courtyards'].items()]
    targets += [{'side': side, 'polygons': c['polygons']}
                for side, c in peer.get('_packing_copper', {}).get('sides', {}).items()]
    return _in_keepouts({'courtyards': copper['sides']}, targets)


def _lands_in(lands, keepouts):
    """Lands meeting, with positive area, a pad keep-out on a shared copper layer."""
    return [land for land in lands for keepout in keepouts
            if _layers_meet(land[0], keepout['layers']) and any(_overlaps(land[1], p) for p in keepout['polygons'])]


def _copper_escapes(candidate, outline, obstacles, placed=(), pad_keepouts=()):
    """A typed reason when copper leaves the board or meets a keep-out or a peer, else None.

    'outline' or 'rule_area' when copper outside the courtyards leaves the
    board or enters a keep-out. Copper must stay the structural setback S
    inside the outline (copper_setback); with S unknown, such copper is never
    admitted ('setback_unknown'). The owner of a keep-out stays exempt from it;
    a placed peer's copper is checked against the candidate's own keep-outs,
    and the candidate's copper against every placed owner's.

    AGENTEE-418: 'peer_courtyard' when copper outside the courtyards of either
    part meets the other's courtyard or such copper on its side; 'pad_rule_area'
    when a land meets a rule area forbidding pads on one of its copper layers,
    inner layers included (``pad_keepouts`` for the board's, each placed
    owner's ``packing_pad_keepouts`` for theirs). An owner's own lands are
    rigid in its own area at every pose: native DRC reports them on the
    baseline, and they are not a placement constraint. Returns (reason,
    blocking refs) or None.
    """
    copper = candidate.get('_packing_copper')
    if copper is not None:
        if copper['outline']:
            setback = candidate.get('_packing_copper_setback')
            if setback is None:
                return 'setback_unknown', set()
            if not _fits({'courtyards': {'': {'polygons': copper['outline']}}}, outline, setback):
                return 'outline', set()
        if _in_keepouts({'courtyards': copper['sides']}, obstacles):
            return 'rule_area', set()
    own = candidate.get('packing_keepouts', [])
    hit = {fp['ref'] for fp in placed
           if (copper is not None and fp.get('packing_keepouts')
               and _in_keepouts({'courtyards': copper['sides']}, fp['packing_keepouts']))
           or (own and '_packing_copper' in fp and _in_keepouts({'courtyards': fp['_packing_copper']['sides']}, own))}
    if hit:
        return 'rule_area', hit
    hit = {fp['ref'] for fp in placed if _copper_meets(candidate, fp) or _copper_meets(fp, candidate)}
    if hit:
        return 'peer_courtyard', hit
    lands = candidate.get('_packing_lands', ())
    if lands and _lands_in(lands, pad_keepouts):
        return 'pad_rule_area', set()
    own = candidate.get('packing_pad_keepouts', [])
    hit = {fp['ref'] for fp in placed
           if (lands and _lands_in(lands, fp.get('packing_pad_keepouts', ())))
           or (own and _lands_in(fp.get('_packing_lands', ()), own))}
    return ('pad_rule_area', hit) if hit else None


def fixed_input_copper(placed, outline, obstacles, labels, overhangs=(), carried=None, pad_keepouts=()):
    """Typed refusal for fixed parts whose copper is an input defect (AGENTEE-371, ruling 371a), or None.

    A fixed footprint is the designer's pose and is never moved. Each native
    copper box of a fixed part (_copper_box, at its fixed pose) must lie in the
    outline at least the structural setback S (copper_setback) from every edge,
    and on each of its outer copper sides out of every board keep-out and every
    other fixed part's keep-out; an owner stays exempt from its own. Unlike the
    search gate no courtyard discharges a box, so a declared courtyard overhang
    or a carried seed pair never admits copper off the board. Determinate
    findings are a defect; with none, a copper box under an unknown S is typed
    unknown. Returns {'outcome', 'reason', 'findings'} or None.

    Ruling 371c: a reference's declared ``fixed_overhangs`` exit edges (the
    validated policy declarations) are exempt from S for that reference's
    copper only; the outline itself and keep-outs still bind there, and every
    other edge still needs S. Each exempted box is appended to ``carried`` as
    a reported input fact naming the edge, its distance and the declaration.

    AGENTEE-418: each copper land must also stay out of every rule area
    forbidding pads on one of its copper layers, inner layers included: the
    board's (``pad_keepouts``) and every other fixed part's (an owner's own
    lands are rigid baseline findings, as in _copper_escapes). A native land
    box there is a defect; a hand-built land without native facts, bounded only
    by its courtyards, is unproven there and typed unknown.
    """
    findings, unproven = [], []
    declared = {}
    for declaration in overhangs:
        for edge in declaration['edges']:
            side = frozenset((tuple(edge['start_nm']), tuple(edge['end_nm'])))
            declared.setdefault(declaration['reference'], {})[side] = (edge, declaration)
    for fp in placed:
        setback = fp.get('_packing_copper_setback')
        exits = declared.get(fp['ref'], {})
        peers = [(o, name) for peer in placed if peer is not fp
                 for o, name in zip(peer.get('packing_keepouts', []), labels.get(peer['ref'], []))]
        pad_areas = list(pad_keepouts) + [k for peer in placed if peer is not fp
                                          for k in peer.get('packing_pad_keepouts', [])]
        for pad in fp['pads']:
            on, copper = _copper_sides(pad)
            box = _copper_box(fp, pad) if copper else None
            if copper and pad_areas:
                layers = ALL_COPPER if pad.get('layers') is None else _copper_layers(pad['layers'])
                if box is None:
                    lands = [(layers, p, False) for c in fp['courtyards'].values() for p in c['polygons']]
                elif box[0] < box[2] and box[1] < box[3]:
                    lands = [(layers, [(box[0], box[1]), (box[2], box[1]), (box[2], box[3]), (box[0], box[3])], True)]
                else:
                    lands = []
                for area in pad_areas:
                    if _lands_in(lands, [area]):
                        row = {'kind': 'fixed_input_copper', 'reference': fp['ref'], 'pad': pad['number'],
                               'copper_bounds_nm': None if box is None else list(box), 'setback_nm': setback,
                               'keepout': area['name']}
                        if box is None:
                            unproven.append({**row, 'violation': 'pad_keepout_unproven'})
                        else:
                            findings.append({**row, 'violation': 'in_pad_keepout'})
            if box is None or box[0] == box[2] or box[1] == box[3]:
                continue
            x0, y0, x1, y1 = box
            poly = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
            shape = {'courtyards': {'': {'polygons': [poly]}}}
            row = {'kind': 'fixed_input_copper', 'reference': fp['ref'], 'pad': pad['number'],
                   'copper_bounds_nm': list(box), 'setback_nm': setback}
            if not _fits(shape, outline):
                findings.append({**row, 'violation': 'outside_outline', 'outline': 'board outline'})
            elif setback is not None and not _fits(shape, outline, setback):
                gaps = [(frozenset((tuple(c), tuple(d))), min(segment_distance2(a, b, c, d) for a, b in edges(poly)))
                        for c, d in edges(outline)]
                near = [(side, d2) for side, d2 in gaps if d2 < setback * setback]
                binding = [d2 for side, d2 in near if side not in exits]
                if binding or not near:
                    findings.append({**row, 'violation': 'within_setback', 'outline': 'board outline',
                                     'outline_distance_nm': math.isqrt(math.floor(min(
                                         binding or [d2 for _, d2 in gaps])))})
                elif carried is not None:
                    for side, d2 in near:
                        edge, declaration = exits[side]
                        carried.append({**row, 'kind': 'fixed_overhang_copper', 'edge': edge['name'],
                                        'outline_distance_nm': math.isqrt(math.floor(d2)),
                                        'declaration': {'reference': declaration['reference'],
                                                        'edge': copy.deepcopy(edge),
                                                        'source': copy.deepcopy(declaration['source'])}})
            elif setback is None:
                unproven.append({**row, 'violation': 'setback_unknown'})
            for side in on:
                for obstacle, name in [*zip(obstacles, labels.get('board', [])), *peers]:
                    if obstacle['side'] == side and any(_overlaps(poly, p) for p in obstacle['polygons']):
                        findings.append({**row, 'violation': 'in_keepout', 'side': side, 'keepout': name})
    if findings:
        refs = ', '.join(sorted({f"{f['reference']} pad {f['pad']}" for f in findings}))
        return {'outcome': 'defect', 'findings': findings,
                'reason': f'fixed part copper outside the outline, within the copper setback of it, '
                          f'or in a keep-out: {refs}'}
    reasons = []
    refs = ', '.join(sorted({f['reference'] for f in unproven if f['violation'] == 'setback_unknown'}))
    if refs:
        reasons.append(f'copper setback unknown: the placement facts carry no board max_error_nm (m_MaxError), '
                       f'so the copper of fixed {refs} is not proven to stay the setback inside the outline')
    refs = ', '.join(sorted({f['reference'] for f in unproven if f['violation'] == 'pad_keepout_unproven'}))
    if refs:
        reasons.append(f'pad keep-out unproven: fixed {refs} has lands without native land facts '
                       f'whose courtyard meets a rule area forbidding pads')
    if unproven:
        return {'outcome': 'unknown', 'findings': unproven, 'reason': '; '.join(reasons)}
    return None


def _fits(candidate, outline, margin_nm=0, band=()):
    exit_edges = []
    if 'access_direction' in candidate:
        access = access_direction.evaluate(candidate, outline)
        if access['status'] not in ('pass', 'info'):
            return False
        if not band and access['provenance']['enforcement'] == 'hard' and access['relaxes_outline']:
            band = access['lane_polygons_nm']
            exit_edges = access['overhang']['edges']
    # A band is a declared overhang across outline edges; its regions share no area with the board.
    # Without a band, a courtyard lies in a convex counter-clockwise outline exactly
    # when every vertex is on or left of every outline edge (closed sets, exact
    # integers): the same answer as the area test, without clipping.
    vertex_test = not band and convex(outline)
    for courtyard in candidate['courtyards'].values():
        for p in courtyard['polygons']:
            if vertex_test:
                if any(cross(a, b, v) < 0 for a, b in edges(outline) for v in p):
                    return False
            else:
                inside = intersection_area(p, outline) + sum(intersection_area(p, r) for r in band)
                if inside != Fraction(abs(area2(p)), 2):
                    return False
            if margin_nm > 0:
                # An outline edge whose box is at least the margin from the
                # courtyard box in x or y is at least the margin from every courtyard
                # point; only the remaining edges can decide the exact test.
                x0, y0, x1, y1 = _box([p])
                near = [(c, d) for c, d in edges(outline)
                        if not any(on_segment(c, d, e['start_nm']) and on_segment(c, d, e['end_nm'])
                                   for e in exit_edges)
                        and min(c[0], d[0]) - margin_nm < x1 and x0 < max(c[0], d[0]) + margin_nm
                        and min(c[1], d[1]) - margin_nm < y1 and y0 < max(c[1], d[1]) + margin_nm]
                if near and min(segment_distance2(a, b, c, d)
                                for a, b in edges(p) for c, d in near) < margin_nm * margin_nm:
                    return False
    return True


def _candidate_axes(fp, rotation, outline, reserved, gap, margin_nm=0, targets=(), window=None, at_origin=None,
                    copper_axes=False):
    """Sorted candidate origin x and y values; every pairing is a candidate position."""
    if at_origin is None:
        at_origin = transform(fp, [0, 0], rotation)
    bounds = list(boxes(at_origin).values())
    bounds = [(min(b[0] for b in bounds), min(b[1] for b in bounds),
               max(b[2] for b in bounds), max(b[3] for b in bounds))]
    bounds.extend(b for _, b in _keepout_boxes(at_origin.get('packing_keepouts', [])))
    if copper_axes:
        # Copper outside every courtyard aligns to the outline as a courtyard does (AGENTEE-366),
        # held off it by the structural setback S (copper_setback).
        setback = at_origin.get('_packing_copper_setback') or 0
        bounds.extend(_box([_inflate(p, setback)]) for p in at_origin.get('_packing_copper', {}).get('outline', []))
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
    access_x, access_y = access_direction.axes(at_origin, outline)
    xs.update(access_x)
    ys.update(access_y)
    if window is not None:
        # Origin limits from necessary conditions only (see _window): nothing admissible is dropped.
        x0, y0, x1, y1 = bounds[0]
        lo_x, lo_y, hi_x, hi_y = window['box']
        if not access_direction.overhangs_courtyard(at_origin):
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


def _unreserved(side_boxes, rectangles, xs, ys, reserved, solid, gap, conflicts, doomed=None, checkpoint=None):
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
        if checkpoint is not None:
            checkpoint()
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


def _wire_bounds(signal, peers, xs, ys):
    """Lower bounds on _wire_cost split by axis, one value per candidate axis value.

    ``signal`` holds the at-origin signal pads of one rotation. A pad's nearest
    same-net peer is at least as far as the nearest peer x coordinate plus the
    nearest peer y coordinate (each a minimum over all peers), so the sum over
    pads bounds the cost from below and separates into a function of x and a
    function of y, found by bisection in the sorted peer coordinates (AGENTEE-332).
    """
    along_x = [(ox, sorted({a for a, _ in peers[net]})) for net, ox, _ in signal if net in peers]
    along_y = [(oy, sorted({b for _, b in peers[net]})) for net, _, oy in signal if net in peers]
    def nearest(value, coordinates):
        i = bisect.bisect_left(coordinates, value)
        return min(abs(value-c) for c in coordinates[max(0, i-1):i+1])
    def bound(values, spans):
        return {v: sum(nearest(o+v, coordinates) for o, coordinates in spans) for v in values}
    return bound(xs, along_x), bound(ys, along_y)


def _ranked(bounds, exact):
    """Keys in exact order, resolving each exact key only when its entry may be next.

    Every bound compares at most equal to its exact key and no two entries share
    a key (candidate x, y and rotation are part of it), so a popped exact key
    precedes every remaining entry, whose exact key is at least its own bound.
    The sequence equals sorted(map(exact, bounds)).
    """
    heap = [(key, False) for key in bounds]
    heapq.heapify(heap)
    while heap:
        key, resolved = heapq.heappop(heap)
        if resolved:
            yield key
        else:
            heapq.heappush(heap, (exact(key), True))


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
    # declared overhang counts its captured onboard area. A cited access lane
    # supplies a rigid-invariant lower bound for a movable overhang instead.
    board_area = Fraction(abs(area2(outline)), 2)
    for side in ('F', 'B'):
        area = sum(intersection_area(p, outline) if fp['ref'] in bands else access_direction.minimum_inside_area(fp, [p])
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
    # Fixed envelope overhangs and cited access lanes invalidate whole-part extents.
    board = _extent(outline)
    for fp in sorted(capture['footprints'], key=lambda f: f['ref']):
        if fp['ref'] in bands or access_direction.overhangs_courtyard(fp):
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


def _region_impossibility(capture, outline, groups, fixed, placed, obstacles, policy, *, sourced_only=False):
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
    if sourced_only:
        groups = [g for g in groups if placement_groups.constraint_source(g, 'region')['enforcement'] == 'hard']
    regions = [(g, [[tuple(p) for p in poly] for poly in g['region']['polygons']]) for g in groups if 'region' in g]
    r = Fraction(policy['gap_nm'], 2)
    gapped = [(fp['ref'], side, _inner(c)) for fp in placed for side, c in sorted(fp['courtyards'].items())]
    gapped += [(f"obstacle {i}", o['side'], o['polygons']) for i, o in enumerate(policy['obstacles'])
               if not sourced_only or placement_provenance.classify(o, 'provenance', f'obstacle {i}')['enforcement'] == 'hard']
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
        if not all(_inside(q, outline) for q in region):
            # Only these parts may use the portion of a declared region outside
            # the board; omitting them leaves a valid lower bound for the rest.
            refs = [ref for ref in refs if not access_direction.overhangs_courtyard(by_ref[ref])]
        board_area = sum(intersection_area(q, outline) for q in region)
        for side in ('F', 'B'):
            courtyards = [_inner(by_ref[ref]['courtyards'][side]) for ref in refs if side in by_ref[ref]['courtyards']]
            required = sum(Fraction(abs(area2(p)), 2) for polys in courtyards for p in polys)
            if not required:
                continue
            record = {'group': group['id'], 'side': side, 'groups': sorted(g['id'] for g in contained),
                      'references': refs, 'binding_regions': [
                          {'constraint': f"group {g['id']} region", **placement_groups.constraint_source(g, 'region')}
                          for g in sorted(contained, key=lambda g: g['id'])]}
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


def _board_capacity(capture, outline, fixed, policy, margin_nm, *, sourced_only=False):
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
        sources += [(f'obstacle {i}', o['polygons']) for i, o in enumerate(policy['obstacles']) if o['side'] == side
                    and (not sourced_only or placement_provenance.classify(o, 'provenance', f'obstacle {i}')['enforcement'] == 'hard')]
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
                 if fp['ref'] not in fixed and side in fp['courtyards']
                 and not access_direction.overhangs_courtyard(fp)]
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


def _capacity_proof(capture, outline, fixed, policy, margin_nm, prove):
    """Keep diagnostic inputs, but prefer proofs independent of advisory constraints."""
    sides = _board_capacity(capture, outline, fixed, policy, margin_nm)
    capacity = _capacity_diagnostic(sides, policy, margin_nm)
    omitted = sorted(fp['ref'] for fp in capture['footprints']
                     if fp['ref'] not in fixed and access_direction.overhangs_courtyard(fp))
    if omitted:
        capacity['excluded_access_overhang_refs'] = omitted
    proof = _capacity_impossibility(sides, policy) if prove else None
    if proof:
        proof_policy, proof_margin = policy, margin_nm
        declared = policy.get('constraint_provenance', {})
        unsourced_gap = policy['gap_nm'] and 'gap_nm' not in declared
        unsourced_margin = margin_nm and 'min_courtyard_edge_mm' not in declared
        # First remove all advisory limits, then each alone. A proof independent
        # of one limit must not ask for that limit's confirmation.
        advisory_obstacles = any(placement_provenance.classify(o, 'provenance', f'obstacle {i}')['enforcement'] == 'soft'
                                 for i, o in enumerate(policy['obstacles']))
        retries = []
        if advisory_obstacles:
            retries.append(({**policy, 'gap_nm': 0} if unsourced_gap else policy,
                            0 if unsourced_margin else margin_nm, True))
            retries.append((policy, margin_nm, True))
        if unsourced_gap and unsourced_margin:
            retries.append(({**policy, 'gap_nm': 0}, 0, False))
        if unsourced_gap:
            retries.append(({**policy, 'gap_nm': 0}, margin_nm, False))
        if unsourced_margin:
            retries.append((policy, 0, False))
        for retry_policy, retry_margin, sourced_only in retries:
            retry_proof = _capacity_impossibility(
                _board_capacity(capture, outline, fixed, retry_policy, retry_margin, sourced_only=sourced_only), retry_policy)
            if retry_proof:
                proof, proof_policy, proof_margin = retry_proof, retry_policy, retry_margin
                break
        return capacity, proof, _binding_constraints(proof_policy, proof_margin, proof['blocked_by'])
    return capacity, None, []


def _fixed_refs(capture, policy):
    return set(policy['fixed_refs']) | {f['ref'] for f in capture['footprints'] if f.get('locked')}


def _carries_seed_pairs(policy):
    """Seed fixed pairs are carried only under an unsourced, agent-chosen gap (AGENTEE-338/376)."""
    return (policy.get('carry_seed_fixed_pairs', False) and policy['gap_nm'] > 0
            and 'gap_nm' not in policy.get('constraint_provenance', {}))


def _unchanged(fp, seed):
    """True when a fixed footprint stands exactly as in the seed: same pose and courtyards."""
    def plain(value):  # Qualification turns vertex lists into tuples; compare values only.
        if isinstance(value, dict):
            return {k: plain(v) for k, v in value.items()}
        return [plain(v) for v in value] if isinstance(value, (list, tuple)) else value
    return seed is not None and all(plain(fp.get(k)) == plain(seed.get(k))
                                    for k in ('position_nm', 'rotation_deg', 'courtyards'))


def _spacing(fp, entry):
    """Exact smallest edge distance squared between a courtyard and an obstacle on its side."""
    return min(segment_distance2(a, b, d, e) for c in fp['courtyards'][entry['side']]['polygons']
               for p in entry['polygons'] for a, b in edges(c) for d, e in edges(p))


def _initial_reservations(capture, policy, outline, obstacles, bands, groups, carried=None, seed=None):
    """Fixed parts and declared obstacles become exact polygon obstacles, never boxes.

    With ``carry_seed_fixed_pairs`` and an unsourced gap, a fixed part unchanged from
    ``seed`` (default: the capture itself) is held to the gap only against reservations
    that are not themselves seed facts. Against another unchanged fixed part or an
    unsourced declared obstacle only structural overlap refuses; each pair closer than
    the gap is appended to ``carried`` with its measured spacing (AGENTEE-376).
    """
    fixed = _fixed_refs(capture, policy)
    placed = [copy.deepcopy(f) for f in capture['footprints'] if f['ref'] in fixed]
    exact = [_exact(o['side'], _obstacle_polygons(o['polygons']))
             for o in policy['obstacles']]
    carry = _carries_seed_pairs(policy)
    seeds = {f['ref']: f for f in (capture if seed is None else seed)['footprints']} if carry else {}
    # Per reservation: the carried-pair label when it is a seed fact, else None. A sourced
    # (hard) obstacle, such as a block-arrangement keep-out or shadow, is never a seed fact.
    origins = [{'kind': 'fixed_obstacle', 'obstacle': i, **({'source': o['source']} if 'source' in o else {})}
               if carry and placement_provenance.classify(o, 'provenance', f'obstacle {i}')['enforcement'] == 'soft'
               else None for i, o in enumerate(policy['obstacles'])]
    gap = policy['gap_nm']
    for i, fp in enumerate(placed):
        if not access_direction.admits(fp, outline):
            raise ValueError(f"fixed placement {fp['ref']}: access direction requires an outward edge and an outside lane")
        pre = carry and _unchanged(fp, seeds.get(fp['ref']))
        held = [e for e, o in zip(exact, origins) if not (pre and o)]
        facts = [(e, o) for e, o in zip(exact, origins) if pre and o]
        # Fixed footprints (such as edge-mounted connectors) are exempt from min_courtyard_edge_mm.
        # Envelope bands and cited access lanes bound permitted courtyard overhang.
        if (not _fits(fp, outline, 0, bands.get(fp['ref'], ())) or _near(fp, held, gap)
                or _near(fp, [e for e, _ in facts], 0) or _keepout_blocked(fp, placed[:i], obstacles)):
            raise ValueError(f"fixed placement {fp['ref']} conflicts with outline, obstacle, keepout or reserved gap")
        for peer in placed[:i]:
            if _hole_conflict(fp, peer):
                raise ValueError(f"fixed placement {fp['ref']} conflicts with {peer['ref']} native through-hole reservation")
            if _copper_meets(fp, peer) or _copper_meets(peer, fp):
                # AGENTEE-418: copper outside a courtyard binds a peer's courtyard as a courtyard does.
                raise ValueError(f"fixed placement {fp['ref']} copper outside its courtyard meets {peer['ref']}, "
                                 f"or {peer['ref']}'s meets {fp['ref']}")
        for entry, origin in facts:
            if entry['side'] in fp['courtyards'] and _near(fp, [entry], gap):
                d2 = _spacing(fp, entry)
                pair = ({'kind': 'fixed_fixed', 'references': [origin['reference'], fp['ref']]}
                        if origin['kind'] == 'fixed_fixed' else
                        {**{k: v for k, v in origin.items() if k != 'kind'}, 'kind': 'fixed_obstacle',
                         'reference': fp['ref']})
                if carried is not None:
                    carried.append({**pair, 'side': entry['side'], 'spacing_nm': math.isqrt(math.floor(d2)),
                                    'distance_squared_nm2': _fraction(Fraction(d2)), 'gap_nm': gap,
                                    'basis': 'unchanged-from-seed', 'authority': 'carried-input-fact',
                                    'gap_enforcement': 'advisory'})
        own = sorted(fp['courtyards'].items())
        exact.extend(_exact(side, c['polygons']) for side, c in own)
        origins.extend({'kind': 'fixed_fixed', 'reference': fp['ref']} if pre else None for _ in own)
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


class _SearchTimedOut(Exception):
    pass


class _ClusterExhausted(Exception):
    pass


# A failed joint search is unknown coverage, never a geometric proof. This
# deterministic work guard prevents a single cluster consuming the board budget.
CLUSTER_STEP_BUDGET = 4096


def _locality_clusters(index, queue, fixed, document):
    """Connected multi-member obligations, with the hub before tightest peers.

    Two-footprint obligations retain their established independent search. Each
    endpoint belongs to exactly one connected component, including shared
    inductors and peers connected by return/switch roles.
    """
    if index is None:
        return []
    order = {fp['ref']: i for i, fp in enumerate(queue)}
    remaining, clusters = set(index.pairs), []
    while remaining:
        pending, refs = [min(remaining)], set()
        while pending:
            ref = pending.pop()
            if ref in refs:
                continue
            refs.add(ref)
            pending.extend(peer['reference'] for _, peer, *_ in index.pairs[ref]
                           if peer['reference'] not in refs)
        remaining -= refs
        movable = refs - fixed
        if len(refs) < 3 or not movable:
            continue
        hub = min(refs, key=lambda ref: (-len({peer['reference'] for _, peer, *_ in index.pairs[ref]}),
                                        order.get(ref, -1), ref))
        tightness = lambda ref: min(limit for _, _, limit, *_ in index.pairs[ref])
        unit = tuple(sorted(movable, key=lambda ref: (ref != hub, tightness(ref), order[ref], ref)))
        clusters.append({'references': list(unit), 'all_references': [hub] + sorted(refs - {hub}),
                         'strategy': 'joint-tightest-first/1',
                         'step_budget': CLUSTER_STEP_BUDGET,
                         'obligations': sorted({obligation['id']
                             for obligation in document['obligations']
                             if any(obligation[role]['from']['reference'] in refs
                                    for role in electrical_locality.roles(obligation))})})
    return sorted(clusters, key=lambda cluster: min(order[ref] for ref in cluster['references']))


class _Search:
    """Candidate scans over the current partial placement (AGENTEE-212).

    Candidates are ranked by (wire cost, x, y, rotation) and the first one that
    passes every predicate is taken: the same choice as the minimum over all
    admissible candidates, with one counted step per candidate submitted to the
    predicates. A rejection records which placed movable parts blocked it, so a
    failed part names the parts a backjump may move.
    """
    def __init__(self, capture, placed, exact, obstacles, outline, margin_nm, policy, members, heartbeat=None,
                 pad_keepouts=()):
        self.started = (time.monotonic() if heartbeat is not None or 'search_timeout_s' in policy else None)
        self.deadline = (self.started + policy['search_timeout_s']
                         if 'search_timeout_s' in policy else None)
        self.heartbeat, self.last_heartbeat = heartbeat, self.started
        self.locality = (electrical_locality.PlacementIndex(policy['electrical_locality'])
                         if 'electrical_locality' in policy else None)
        self.best = []
        self.policy = policy
        self.fps = {fp['ref']: fp for fp in capture['footprints']}
        self.fixed = {fp['ref'] for fp in placed}
        self.exact, self.obstacles, self.outline, self.margin_nm = exact, obstacles, outline, margin_nm
        self.pad_keepouts = list(pad_keepouts)
        self.solid = [(o['side'], slab) for o in exact for poly in o['polygons'] for slab in _slabs(poly) or ()]
        self.gap, self.budget, self.grounds = policy['gap_nm'], policy['step_budget'], policy['ground_nets']
        self.members, self.steps, self.placed, self.reserved = members, 0, [], []
        self.origins = {}
        self.positions, self.peers = {}, {}
        self.cluster_active = None
        self.cluster_start_steps = None
        self.copper_axes = False
        self.copper_setback = copper_setback(capture)
        self.cluster_work = {}
        # Placement generation (AGENTEE-430): one id per partial-placement state. push
        # allocates a new id and pop restores the previous one, as pop restores peers.
        self.generations, self.generations_allocated, self.trace_index = [0], 0, None
        # Anchor distance is symmetric: it binds whichever of the pair is placed second.
        self.anchored = {}
        for ref, group in members.items():
            if 'reference' in group.get('anchor', {}):
                self.anchored.setdefault(group['anchor']['reference'], []).append((ref, group['max_distance_nm']))
        for fp in placed:
            self.push(fp)

    def checkpoint(self):
        now = time.monotonic() if self.started is not None else None
        if self.heartbeat is not None and now - self.last_heartbeat >= 5:
            self.heartbeat({'status': 'packing', 'steps_used': self.steps,
                            'placed_count': len(self.placed), 'placed_peak': len(self.best),
                            'elapsed_s': now - self.started})
            self.last_heartbeat = now
        if self.deadline is not None and now >= self.deadline:
            raise _SearchTimedOut
        if self.cluster_active is not None and self.steps - self.cluster_start_steps >= CLUSTER_STEP_BUDGET:
            raise _ClusterExhausted

    def at_origin(self, fp, rotation):
        """The part at origin per rotation, once per search: candidates are translations of it."""
        key = (fp['ref'], rotation)
        if key not in self.origins:
            self.origins[key] = transform(fp, [0, 0], rotation)
            self.origins[key]['_packing_copper_setback'] = self.copper_setback
            _cache_copper_extent(self.origins[key])
        return self.origins[key]

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
        self.generations_allocated += 1
        self.generations.append(self.generations_allocated)
        if self.locality is not None:
            self.locality.push(fp)
        if len(self.placed) > len(self.best):
            self.best = list(self.placed)
        self.positions[fp['ref']] = fp['position_nm']
        if fp['ref'] not in self.fixed:
            self.reserved.extend((side, box, fp['ref']) for side, box in boxes(fp).items())
        for pad in fp['pads']:
            if pad['net']:
                self.peers.setdefault(pad['net'], []).append(tuple(pad['position_nm']))

    def pop(self):
        fp = self.placed.pop()
        self.generations.pop()
        if self.locality is not None:
            self.locality.pop(fp['ref'])
        del self.positions[fp['ref']]
        while self.reserved and self.reserved[-1][2] == fp['ref']:
            self.reserved.pop()
        for pad in reversed(fp['pads']):
            if pad['net']:
                self.peers[pad['net']].pop()
                if not self.peers[pad['net']]:
                    del self.peers[pad['net']]

    def blockers(self, proposed, group, ties, candidate_locality=None):
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
        own = proposed.get('packing_keepouts', [])
        hit = [fp['ref'] for fp in self.placed if (fp.get('packing_keepouts') and
               _in_keepouts(proposed, fp['packing_keepouts'])) or (own and _in_keepouts(fp, own))]
        if hit:
            return set(hit) - self.fixed
        escaped = _copper_escapes(proposed, self.outline, self.obstacles, self.placed, self.pad_keepouts)
        if escaped is not None:
            return escaped[1] - self.fixed
        if group is not None and 'region' in group and not placement_groups.admits(
                proposed, {'region': group['region']}, {}):
            return set()
        if any(placement_groups.distance2(proposed['position_nm'], point) > d*d for _, point, d in ties):
            return set()
        if (candidate_locality is None and self.locality is not None
                and proposed['ref'] in self.locality.pairs):
            candidate_locality = self.locality.prepare(transform(proposed, [0, 0], proposed['rotation_deg']))
        if candidate_locality is not None:
            hit, _ = candidate_locality.constraint(proposed['position_nm'])
            if hit:
                return hit - self.fixed - {proposed['ref']}
        return None

    def scan(self, fp, conflicts):
        """Admissible candidates in rank order; rejections add their blockers to ``conflicts``."""
        trace = placement_trace.active()  # None unless tracing is on (AGENTEE-430); no decision reads it.
        if trace is not None:
            t, recorded_request = placement_trace.now(), 0
            try:
                request = trace.scan_request(self.trace_index, self.generations[-1], fp, self.grounds, self.peers,
                                             _pad_nets)
                recorded_request = 1
            finally:
                # Recording is the observer's cost, not Stage A's; a raising request keeps its time uncounted.
                started = placement_trace.now()
                trace.add('stage_a.trace', started - t, recorded_request)
            bounds = trace.durations['stage_a.bounds']
            bounds_before = bounds[0]
        try:
            group = self.members.get(fp['ref'])
            participating = self.locality is not None and fp['ref'] in self.locality.pairs
            self.checkpoint()
            anchors = [(side, box) for side, box, _ in self.reserved] + [(o['side'], o['box']) for o in self.exact]
            # A concave fixed courtyard's box can lie far from its edges; its slabs carry them.
            anchors += self.solid
            anchors += _keepout_boxes(self.obstacles + [area for f in self.placed
                                                        for area in f.get('packing_keepouts', [])])
            # Pad keep-outs (AGENTEE-418) align candidates as footprint keep-outs do; a courtyard may enter them.
            anchors += [(None, _box(k['polygons'])) for k in self.pad_keepouts + [
                k for f in self.placed for k in f.get('packing_pad_keepouts', [])]]
            ties = self.ties(fp['ref'])
            targets = [] if group is None else [p for poly in group.get('region', {}).get('polygons', [])
                                                for p in poly]
            targets += [point for _, point, _ in ties]
            window = _window(self.outline, self.margin_nm, group, ties)
            ranked, evaluators = [], {}
            for rotation in (0, 90, 180, 270):
                at_origin = self.at_origin(fp, rotation)
                evaluator = self.locality.prepare(at_origin) if participating else None
                evaluators[rotation] = evaluator
                local_targets = evaluator.targets() if evaluator is not None else []
                xs, ys = _candidate_axes(fp, rotation, self.outline, anchors, self.gap, self.margin_nm,
                                         targets + local_targets, window, at_origin, self.copper_axes)
                if self.cluster_active is not None and evaluator is not None:
                    # Intersections of axis projections of the exact pad discs are
                    # necessary only; the full coupled predicates still judge every point.
                    for offset, target, limit, _, hard in evaluator.pairs:
                        if not hard:
                            continue  # AGENTEE-337: advisory bounds never prune candidates
                        xs = [x for x in xs if abs(x + offset[0] - target[0]) <= limit]
                        ys = [y for y in ys if abs(y + offset[1] - target[1]) <= limit]
                    conflicts.update(ref for _, _, _, involved, hard in evaluator.pairs if hard
                                     for ref in involved if ref not in self.fixed and ref != fp['ref'])
                rectangles = {side: [r for poly in c['polygons'] for r in _slabs(poly)]
                              for side, c in at_origin['courtyards'].items()
                              if all(_slabs(poly) is not None for poly in c['polygons'])}
                doomed = set()
                # Bound keys enter the ranking; the exact wire cost is resolved lazily
                # by _ranked, in the same order as sorting every exact key.
                if trace is not None:
                    t, bounded = placement_trace.now(), 0
                try:
                    bound_x, bound_y = _wire_bounds(_pad_nets(fp, (0, 0), rotation, self.grounds), self.peers,
                                                    xs, ys)
                    bounded = 1
                finally:
                    if trace is not None:
                        # A raising bound keeps its time (geometry excludes it) and is not counted.
                        trace.add('stage_a.bounds', placement_trace.now() - t, bounded)
                for x, y in _unreserved(boxes(at_origin), rectangles, xs, ys, self.reserved, self.solid,
                                        self.gap, conflicts, doomed, self.checkpoint):
                    self.checkpoint()
                    hit, local_cost = evaluator.constraint((x, y)) if evaluator is not None else (set(), 0)
                    if self.cluster_active is not None and hit:
                        conflicts.update(hit - self.fixed - {fp['ref']})
                        continue
                    ranked.append((local_cost, bound_x[x] + bound_y[y], x, y, rotation, (x, y) in doomed))
        finally:
            if trace is not None:
                # Closed on failure too: an exception never loses the entered span.
                trace.add('stage_a.geometry', placement_trace.now() - started - (bounds[0] - bounds_before))
        def exact(key):
            self.checkpoint()  # Each exact cost is checked as promptly as when generation computed it.
            local_cost, _, x, y, rotation, doomed = key
            return (local_cost, _wire_cost(_pad_nets(fp, (x, y), rotation, self.grounds), self.peers),
                    x, y, rotation, doomed)
        order = _ranked(ranked, exact)
        if trace is not None:
            untraced, timed, sample, clock = exact, trace.durations['stage_a.exact'], trace.sample, placement_trace.now
            recorded = trace.durations['stage_a.trace']
            scan_generation = self.generations[-1]

            def exact(key):
                t = clock()
                try:
                    resolved = untraced(key)
                finally:
                    t1 = clock()
                    timed[0] += t1 - t  # A raising resolution's time is kept; only resolved ones are counted.
                timed[1] += 1
                try:
                    sample(request, scan_generation, self.generations[-1], key, resolved[1])
                finally:
                    recorded[0] += clock() - t1  # The sample is the observer's cost, not ranking's, raising too.
                recorded[1] += 1
                return resolved
            order = trace.timed_rank(_ranked(ranked, exact))
            if request is not None:
                request['generated'] = len(ranked)
            trace.counters['stage_a.candidates_generated'] += len(ranked)
        for _, _, x, y, rotation, doomed in order:
            self.checkpoint()
            if self.steps == self.budget:
                raise _BudgetExhausted
            self.steps += 1
            if doomed:
                if trace is not None:
                    trace.counters['stage_a.doomed'] += 1
                continue  # Nearer than the gap to a fixed courtyard or obstacle: the exact test rejects it.
            if trace is not None:
                t = placement_trace.now()
            try:
                proposed = _translate(self.at_origin(fp, rotation), (x, y))
                hit = self.blockers(proposed, group, ties, evaluators[rotation])
            except BaseException:
                if trace is not None:
                    trace.counters['stage_a.aborted'] += 1  # The raising step is neither admitted nor rejected.
                raise
            finally:
                if trace is not None:
                    trace.add('stage_a.predicates', placement_trace.now() - t)
            if trace is not None:
                trace.counters['stage_a.admitted' if hit is None else 'stage_a.rejected'] += 1
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

    def place_cluster(self, unit):
        """Backtrack every member's candidates against a single partial state."""
        conflicts, done = set(), []
        spent_steps = self.cluster_work.get(unit, 0)
        self.cluster_start_steps = self.steps - spent_steps
        self.cluster_active = True

        def visit(index):
            if index == len(unit):
                return list(done)
            for proposed in self.scan(self.fps[unit[index]], conflicts):
                self.push(proposed)
                done.append(proposed)
                try:
                    found = visit(index + 1)
                    if found is not None:
                        return found
                finally:
                    done.pop()
                    self.pop()
            return None

        try:
            # Bound recursion independently of the candidate guard.
            if len(unit) > 64:
                raise _ClusterExhausted
            self.checkpoint()
            return visit(0), conflicts - set(unit)
        finally:
            self.cluster_work[unit] = self.steps - self.cluster_start_steps
            self.cluster_active = self.cluster_start_steps = None


def pack(capture, policy, *, heartbeat=None, seed=None):
    """Preserve legacy plans; try joint clusters, then copper-aligned candidates, only with the remaining budget."""
    return _pack_gated(capture, policy, heartbeat=heartbeat, seed=seed, arranged=None)


def _pack_gated(capture, policy, *, heartbeat, seed, arranged):
    """``pack`` with ``arranged``: block members whose exact pose the arrangement copper gate admitted.

    Only block arrangement passes ``arranged`` (reference -> the admitted member
    footprint); every other fixed part, and an arranged member at any other pose,
    has its copper validated as an input (AGENTEE-371, ruling 371d).
    """
    runtime = {}
    result = _pack_joint(capture, policy, heartbeat, runtime, seed, arranged)
    if result['status'] == 'planned' or not runtime.get('copper_escapes'):
        return _proved_after_search(result, runtime)
    # AGENTEE-366: every pass gates copper; only a failed search adds the copper
    # alignment axes, so an earlier admissible plan is never replaced. A failed
    # aligned pass leaves the earlier (legacy or joint-annotated) result unchanged.
    used = runtime['steps']
    remaining = policy['step_budget'] - used
    if remaining == 0:
        return _proved_after_search(result, runtime)
    aligned = _pack(capture, policy, heartbeat=heartbeat, joint=False, remaining_steps=remaining,
                    deadline=runtime['search'].deadline, copper_axes=True, seed=seed, arranged=arranged)
    if aligned['status'] != 'planned':
        # AGENTEE-371 (366b gap 4): a diagnostic only; the earlier result and its steps are unchanged.
        if 'search' in result:
            result['search']['copper_aligned_attempt'] = {
                'status': aligned['status'], 'outcome': aligned['outcome'], 'reason': aligned['reason'],
                'steps_used': aligned['steps_used'], 'prior_steps_used': used}
        return _proved_after_search(result, runtime)
    aligned['steps_used'] += used
    aligned['search']['legacy_attempt'] = runtime['legacy']
    aligned['search']['copper_aligned'] = {'prior_steps_used': used, 'copper_setback': copper_setback_record(capture)}
    return aligned


SEARCH_FAILURES = ('packing_search_exhausted', 'packing_budget_exhausted', 'locality_cluster_exhausted',
                   'judge_rejected')


def _proved_after_search(result, runtime):
    """The default capacity proof (AGENTEE-249): applied only once every search pass has failed.

    The legacy result is kept unless the search failed and a capacity row is exceeded;
    the proof then replaces the search's verdict, keeping its record and steps as diagnostics.
    """
    proved = runtime.get('capacity_failure')
    if result['status'] not in SEARCH_FAILURES or proved is None:
        return result
    return {**result, **proved, 'steps_used': result['steps_used'],
            'search_failure': {key: result[key] for key in ('status', 'outcome', 'reason')}}


def _pack_joint(capture, policy, heartbeat, runtime, seed, arranged):
    legacy = _pack(capture, policy, heartbeat=heartbeat, joint=False, runtime=runtime, seed=seed, arranged=arranged)
    runtime['steps'] = runtime['search'].steps if 'search' in runtime else 0
    runtime['legacy'] = {'status': legacy['status'], 'steps_used': runtime['steps']}
    if legacy['status'] == 'planned' or not legacy.get('search', {}).get('locality_clusters'):
        return legacy
    previous = runtime['search']
    remaining = policy['step_budget'] - previous.steps
    if remaining == 0:
        legacy['search']['joint_cluster_attempt'] = {
            'status': 'not_attempted', 'reason': 'no remaining deterministic candidate budget', 'steps_used': 0}
        return legacy
    joint = _pack(capture, policy, heartbeat=heartbeat, joint=True,
                  remaining_steps=remaining, deadline=previous.deadline, seed=seed, arranged=arranged)
    runtime['steps'] += joint['steps_used']
    if joint['status'] != 'planned':
        legacy['search']['joint_cluster_attempt'] = {
            'status': joint['status'], 'steps_used': joint['steps_used'], 'reason': joint['reason'],
            **joint.get('search', {})}
        return legacy
    # _pack independently grades every complete proposal before returning planned.
    joint['steps_used'] += previous.steps
    joint['search']['legacy_attempt'] = {'status': legacy['status'], 'steps_used': previous.steps}
    return joint


def _pack(capture, policy, *, seed=None, **options):
    state = {}
    trace = placement_trace.active()
    if trace is not None:
        started = placement_trace.now()
    try:
        result = _pack_run(capture, policy, state=state, seed=seed, **options)
    finally:
        if trace is not None:
            # Preparation until the search starts, the search, then complete-proposal judging
            # (AGENTEE-430); a raising run still closes the spans it entered.
            ended = placement_trace.now()
            searched, judged = state.get('trace_search', ended), state.get('trace_judge', ended)
            trace.add('prep', searched - started)
            if 'trace_search' in state:
                trace.add('stage_a.search', judged - searched)
                trace.add('stage_a.judge', ended - judged, int('trace_judge' in state))
    if 'carried' in state:
        # Seed fixed pairs closer than the unsourced gap: reported input facts, never refusals.
        result['carried_fixed_pairs'] = state['carried']
    if state.get('overhang_copper') and result['status'] == 'planned':
        # Fixed copper within S of its declared exit edge: reported input facts (ruling 371c).
        result['fixed_overhang_copper'] = state['overhang_copper']
    return result


def _pack_run(capture, policy, *, heartbeat, joint, state, seed, arranged=None, runtime=None, remaining_steps=None,
              deadline=None, copper_axes=False):
    if heartbeat is not None:
        heartbeat({'status': 'qualifying', 'steps_used': 0, 'placed_count': 0})
    capture = copy.deepcopy(capture)
    validate_capture(capture)
    _validate_policy(policy, {f['ref'] for f in capture['footprints']})
    margin_nm = _edge_margin_nm(policy)
    locality = policy.get('electrical_locality')
    if locality:
        initial = electrical_locality.grade(capture, locality)
        if any(d['kind'] == 'locality_infeasible' for d in initial['defects']):
            return {**_failure(capture, policy, 'locality_infeasible', 'defect', 0,
                               'declared bound below computed manufacturing floor', margin_nm),
                    'locality_grading': initial}
        if initial['unknown']:
            return {**_failure(capture, policy, 'electrical_locality_unavailable', 'unknown', 0,
                               'pad locality needs unambiguous native pads, limits and citations', margin_nm),
                    'locality_grading': initial}
    groups = placement_groups.validate(policy['groups']) if 'groups' in policy else []
    members = placement_groups.membership(groups)
    try:
        outline = _qualified(capture)
        for fp in capture['footprints']:
            if access_direction.hard(fp):
                access_direction.geometry(fp)
        labels, pad_keepouts = {}, []
        obstacles = _keepouts(capture, labels, pad_keepouts)
        bands = overhang_bands(policy.get('fixed_overhangs', []), outline)
    except ValueError as exc:
        return _failure(capture, policy, 'unsupported_geometry', 'unknown', 0, str(exc), margin_nm)
    fixed = _fixed_refs(capture, policy)
    loose = sorted(set(bands) - fixed)
    if loose:
        raise ValueError(f"courtyard overhang declared for movable reference {', '.join(loose)}")
    # The carried list is published to ``state`` only after the area and part proofs, so a proof
    # result never gains a carried_fixed_pairs field the pre-371 result lacked.
    carried = [] if _carries_seed_pairs(policy) else None
    conflict = None
    try:
        fixed, placed, exact = _initial_reservations(capture, policy, outline, obstacles, bands, members,
                                                     carried, seed)
    except ValueError as exc:
        conflict = exc
    # Every fixed part's copper is an input, whatever the seed (ruling 371d), and is reported before
    # any area proof can mask it. The one exclusion is a block member at exactly the pose the
    # arrangement copper gate admitted (AGENTEE-366). A fixed conflict keeps its place after the proofs.
    if conflict is None:
        arranged = arranged or {}
        state['overhang_copper'] = []
        refusal = fixed_input_copper([fp for fp in placed if not _unchanged(fp, arranged.get(fp['ref']))],
                                     outline, obstacles, labels, policy.get('fixed_overhangs', []),
                                     state['overhang_copper'], pad_keepouts)
        if refusal is not None:
            if carried is not None:
                state['carried'] = carried
            status = 'fixed_input_copper' if refusal['outcome'] == 'defect' else 'unsupported_geometry'
            return {**_failure(capture, policy, status, refusal['outcome'], 0, refusal['reason'], margin_nm),
                    'findings': refusal['findings']}
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
    if carried is not None:
        state['carried'] = carried
    if conflict is not None:
        return _failure(capture, policy, 'fixed_conflict', 'unknown', 0, str(conflict), margin_nm)
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
    region_failure = None
    proof = _region_impossibility(capture, outline, groups, fixed, placed, obstacles, policy)
    if proof:
        independent = _region_impossibility(capture, outline, groups, fixed, placed, obstacles,
                                            policy, sourced_only=True)
        proof = independent or proof
        if proof.get('gap_nm') and 'gap_nm' not in policy.get('constraint_provenance', {}):
            proof = (_region_impossibility(capture, outline, groups, fixed, placed, obstacles,
                                           {**policy, 'gap_nm': 0}, sourced_only=True)
                     or _region_impossibility(capture, outline, groups, fixed, placed, obstacles,
                                              {**policy, 'gap_nm': 0}) or proof)
        failure = _failure(capture, policy, 'packing_infeasible', 'defect', 0,
                           f"group {proof['group']} region cannot hold its {proof['side']} courtyards", margin_nm)
        binding = (_binding_constraints({**policy, 'gap_nm': proof.get('gap_nm', 0)}, 0, proof['blocked_by'])
                   + proof['binding_regions'])
        region_failure = _soft_proof({**failure, 'proof': proof}, binding)
    if region_failure is not None and (region_failure['outcome'] == 'defect' or not policy.get('capacity_proof')):
        return region_failure
    # Default (key absent): computed here but applied by ``_pack_gated`` only after every
    # search pass fails (legacy first); ``capacity_proof: true`` applies it before the search.
    deferred = runtime is not None and 'capacity_proof' not in policy
    capacity, proof, binding = _capacity_proof(capture, outline, fixed, policy, margin_nm,
                                               deferred or policy.get('capacity_proof', False))
    if proof:
        failure = _failure(capture, policy, 'packing_infeasible', 'defect', 0,
                           f"{proof['side']} courtyards grown by half the gap exceed the board's free area"
                           + (f" usable by {proof['square_nm']} nm squares" if proof['square_nm'] else ''), margin_nm)
        board_failure = _soft_proof({**failure, 'proof': proof, 'capacity': capacity}, binding)
        if deferred:
            runtime['capacity_failure'] = board_failure
        elif board_failure['outcome'] == 'defect' or region_failure is None:
            return board_failure
    if region_failure is not None:
        return region_failure
    missing = copper_setback_missing(fp for fp in capture['footprints'] if fp['ref'] not in fixed)
    if missing:
        return _failure(capture, policy, 'unsupported_geometry', 'unknown', 0, missing, margin_nm)
    trace = placement_trace.active()
    if trace is not None:
        state['trace_search'] = placement_trace.now()
    search = _Search(capture, placed, exact, obstacles, outline, margin_nm, policy, members, heartbeat, pad_keepouts)
    search.copper_axes = copper_axes
    if trace is not None:
        search.trace_index = trace.search_started(search, joint=joint, copper_axes=copper_axes)
    if runtime is not None:
        runtime['search'] = search
        runtime['copper_escapes'] = any(fp.get('_packing_copper', {}).get('outline')
                                        for fp in capture['footprints'] if fp['ref'] not in fixed)
    if remaining_steps is not None:
        search.budget = remaining_steps
        search.deadline = deadline  # Absolute legacy search deadline; qualification never resets it.
    clusters = _locality_clusters(search.locality, queue, fixed, locality)
    cluster_by_ref = {ref: cluster for cluster in clusters for ref in cluster['references']} if joint else {}
    units = []
    for fp in queue:
        unit = tuple(cluster_by_ref.get(fp['ref'], {}).get('references', (fp['ref'],)))
        if unit not in units:
            units.append(unit)
    stack, seen, backjumps, failures, peak = [], {tuple(units)}, 0, {}, 0
    def stopped(status, reason, unplaced=None):
        failure = _failure(capture, policy, status, 'unknown', search.steps, reason, margin_nm)
        record = search.record(backjumps, unplaced)
        if clusters:
            record['locality_clusters'] = clusters
        if unplaced in cluster_by_ref:
            record['blocking_cluster'] = {**cluster_by_ref[unplaced],
                                         'references': cluster_by_ref[unplaced]['all_references']}
        if unplaced is not None:
            record['placed_peak'] = peak  # Diagnostic only: a plan is complete or empty.
        if status in ('packing_search_exhausted', 'packing_budget_exhausted', 'locality_cluster_exhausted'):
            record['termination'] = status.removeprefix('packing_')
            record['partial'] = {'authority': 'diagnostic-only-unvalidated-partial-placement',
                                 'placements': [{'ref': f['ref'], 'uuid': f['uuid'],
                                     'position_nm': f['position_nm'], 'rotation_deg': f['rotation_deg']}
                                    for f in sorted(search.best, key=lambda f: f['ref'])]}
        return {**failure, 'search': record, 'capacity': capacity}
    if heartbeat is not None:
        heartbeat({'status': 'packing', 'steps_used': 0, 'placed_count': len(placed)})
    while len(stack) < len(units):
        unit = units[len(stack)]
        try:
            fps, conflicts = (search.place_cluster(unit) if unit[0] in cluster_by_ref
                              else search.place_unit(unit))
        except _ClusterExhausted:
            return stopped('locality_cluster_exhausted',
                           f'joint locality cluster {cluster_by_ref[unit[0]]["obligations"]} exceeded its work guard; '
                           'infeasibility unproved', unit[0])
        except electrical_locality.TargetBudgetExhausted:
            return stopped('packing_budget_exhausted', 'locality target budget exhausted', unit[0])
        except _SearchTimedOut:
            return stopped('packing_budget_exhausted', 'wall-time search budget exhausted', unit[0])
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
            return stopped('locality_cluster_exhausted' if unit[0] in cluster_by_ref else 'packing_search_exhausted',
                           f'no candidate for {unit[0]} in the finite search; infeasibility unproved'
                           + ('; access direction requires an outward edge and an outside lane'
                              if access_direction.hard(by_ref[unit[0]]) else ''), unit[0])
        i = len(stack)
        anchor = members.get(unit[0], {}).get('anchor', {}).get('reference')
        failures[unit[0]] = failures.get(unit[0], 0) + 1
        for k in culprits[min(2 ** (failures[unit[0]] - 1), len(culprits)) - 1:]:
            if len(unit) == 1 and anchor in units[k] and units[k][0] not in cluster_by_ref:
                reordered = units[:k] + [unit + units[k]] + units[k+1:i] + units[i+1:]
            else:
                reordered = units[:k] + [unit] + units[k:i] + units[i+1:]
            if tuple(reordered) not in seen:
                break
        else:
            return stopped('packing_search_exhausted',
                           f'every backjump for {unit[0]} revisits a part order; infeasibility unproved', unit[0])
        if backjumps == search.budget:
            return stopped('packing_budget_exhausted', 'deterministic backjump budget exhausted', unit[0])
        seen.add(tuple(reordered))
        units, backjumps = reordered, backjumps + 1
        for done in reversed(stack[k:]):
            for _ in done:
                search.pop()
        del stack[k:]
    if trace is not None:
        state['trace_judge'] = placement_trace.now()
    placed, steps = search.placed, search.steps
    proposed_capture = {**capture, 'footprints': placed}
    judgment = judge(proposed_capture, overhangs=policy.get('fixed_overhangs'))
    judgment['basis'] = 'transformed-proposal-not-native'
    if judgment['verdict'] != 'pass':
        return stopped('judge_rejected', 'independent full-polygon judge rejected complete proposal')
    grading = placement_groups.grade(proposed_capture, groups) if groups else None
    if grading is not None and not placement_groups.satisfied(grading):
        return stopped('judge_rejected', 'declared placement groups not satisfied by the complete proposal')
    extra = {} if grading is None else {'group_grading': grading}
    if locality:
        extra['locality_grading'] = electrical_locality.grade(proposed_capture, locality)
        if extra['locality_grading']['status'] not in ('pass', 'info'):
            return {**stopped('judge_rejected', 'complete proposal failed pad locality'), **extra}
    if 'advisory_regions' in policy:
        extra['advisory_regions'] = advisory_regions.grade(proposed_capture, policy['advisory_regions'], fixed)
    custom_pads = [{'reference': f['ref'], 'uuid': p['uuid'], 'number': p['number']}
                   for f in capture['footprints'] for p in f['pads'] if p['shape'] == 6]
    if custom_pads:
        extra['qualified_custom_pads'] = custom_pads
    search_record = search.record(backjumps)
    if clusters:
        search_record['locality_clusters'] = clusters
    return {**extra, 'search': search_record, 'capacity': capacity, 'schema': 'agent-ee.packing-plan/1',
            'board_sha256': capture['board_sha256'], 'status': 'planned', 'outcome': 'info', 'authority': 'standalone-unsealed-plan',
            'steps_used': steps, 'step_budget': policy['step_budget'],
            'min_courtyard_edge_mm': _margin_mm(margin_nm),
            'policy': copy.deepcopy(policy), 'pad_shapes': pad_shape_qualification(), 'judgment': judgment,
            'placements': [{'ref': f['ref'], 'uuid': f['uuid'], 'position_nm': f['position_nm'],
                            'rotation_deg': f['rotation_deg'], 'fixed': f['ref'] in fixed}
                           for f in sorted(placed, key=lambda f: f['ref'])]}
