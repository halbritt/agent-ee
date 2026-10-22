"""Advisory saved-board reserve costs; never a packing predicate (AGENTEE-380)."""
import copy
from fractions import Fraction

from .placement import area2, edges, overhang_bands, qualified_polygons, simple_polygon

SCHEMA = 'agent-ee.reserved-area/1'
# Computational coverage guards, not physical limits or acceptance thresholds.
MAX_EDGE_PAIRS = 2_000_000
ADVISORY_FRACTION = Fraction(1, 20)
AUTHORITY = 'advisory; does not change packing, score or verdict'
STATUSES = ('measured', 'unknown')


def unmeasured(board_sha256, limitation, reason):
    """A typed record for a producer that has no saved board to measure; never zero."""
    return {'schema': SCHEMA, 'board_sha256': board_sha256,
            'authority': AUTHORITY,
            'status': 'unknown', 'limitation': limitation, 'reason': reason, 'sides': {}, 'findings': []}


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
              'authority': AUTHORITY,
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


DOMAIN = 'quarter turns at current origin and four outline-box edges'
BLOCK_DOMAIN = 'whole-block quarter turns about the anchor at its current origin and four outline-box edges'


def _rigid(members, anchor, position, angle):
    """Every member's pose when the anchor moves to (position, angle); relative poses are kept."""
    from .placer import rotate
    turns = int((angle - anchor['rotation_deg']) / 90)  # As placer.transform counts turns.
    moves = {}
    for fp in members:
        offset = rotate([fp['position_nm'][0] - anchor['position_nm'][0],
                         fp['position_nm'][1] - anchor['position_nm'][1]], turns)
        moves[fp['ref']] = ((position[0] + offset[0], position[1] + offset[1]),
                            angle if fp is anchor else (fp['rotation_deg'] + angle - anchor['rotation_deg']) % 360)
    return moves


def _poses(members, anchor, outline, margin, extents=(), rigid=False):
    """Anchor poses: quarter turns, then outline-box edges and declared band extents."""
    from . import placer
    xs, ys = [p[0] for p in outline], [p[1] for p in outline]
    limits = [(min(xs)+margin, min(ys)+margin, max(xs)-margin, max(ys)-margin)]
    limits += [placer._box([region]) for region in extents]
    for quarter in range(4):
        angle = (anchor['rotation_deg'] + 90*quarter) % 360 if rigid else 90*quarter
        moved = _rigid(members, anchor, anchor['position_nm'], angle)
        # A single part snaps its own-side courtyard; a rigid block snaps every member courtyard.
        polygons = [p for fp in members
                    for side, c in placer.transform(fp, list(moved[fp['ref']][0]),
                                                    moved[fp['ref']][1])['courtyards'].items()
                    if rigid or side == fp['side'] for p in c['polygons']]
        x0, y0, x1, y1 = placer._box(polygons)
        x, y = anchor['position_nm']
        positions = {(x, y)}
        for left, top, right, bottom in limits:
            positions |= {(x+left-x0, y), (x+right-x1, y), (x, y+top-y0), (x, y+bottom-y1)}
        for position in sorted(positions):
            yield position, angle


def _move_contours(fp, contours, position, angle):
    from .placer import transform
    proxy = {'position_nm': fp['position_nm'], 'rotation_deg': fp['rotation_deg'],
             'pads': [], 'courtyards': contours}
    return transform(proxy, list(position), angle)['courtyards']


def _candidate_cost(facts, policy, fabrication, plan, moves):
    """Measured rows of the moved owners, or None when a placement predicate refuses.

    ``moves`` maps each moved reference to its (position, rotation). A fixed owner
    keeps its own contract: no edge margin, and only its declared envelope band.
    """
    from . import placer
    outline = qualified_polygons(facts['outline'])[0]
    fixed = placer._fixed_refs(facts, policy)
    bands = overhang_bands(policy.get('fixed_overhangs', []), outline)
    footprints = {f['ref']: f for f in facts['footprints']}
    candidates = {}
    for ref, (position, angle) in moves.items():
        candidate = placer.transform(footprints[ref], list(position), angle)
        if not (placer._fits(candidate, outline, 0, bands.get(ref, ())) if ref in fixed else
                placer._fits(candidate, outline, placer._edge_margin_nm(policy))):
            return None
        candidates[ref] = candidate
    proposed = copy.deepcopy(facts)
    proposed['footprints'] = [candidates.get(f['ref'], f) for f in proposed['footprints']]
    owners = {footprints[ref]['uuid']: ref for ref in moves}
    # Zone polygons are absolute board coordinates in the native inventory.
    for area in proposed.get('rule_areas', []):
        if area['owner'] in owners:
            ref = owners[area['owner']]
            area['outline'] = _move_contours(footprints[ref], {'zone': area['outline']}, *moves[ref])['zone']
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
    moved_fab = {ref: _move_contours(footprints[ref], fabrication.get(ref, {}), *moves[ref]) for ref in moves}
    selected = {**proposed, 'footprints': [candidates[ref] for ref in moves],
                'rule_areas': [a for a in proposed.get('rule_areas', []) if a['owner'] in owners]}
    measured = report(selected, {**policy, 'obstacles': []}, moved_fab)
    return {(r['side'], r['kind'], r.get('id'), r['owner']): r for side in measured['sides'].values()
            for r in side['owners'] if r['status'] == 'measured'}


def _cheapest(row, trials, native_validator=None, domain=DOMAIN):
    """``native_validator(position, angle)`` validates one anchor pose natively."""
    key = (row['side'], row['kind'], row.get('id'), row['owner'])
    choices = [(Fraction(*match['area_mm2']), position, angle)
               for (position, angle), measured in trials if measured is not None
               for match in [measured.get(key)] if match is not None]
    attempts = []
    eligible = []
    for area, position, angle in sorted(choices):
        if area >= Fraction(*row['area_mm2']):
            break
        if native_validator is None:
            eligible.append((area, position, angle))
            break
        validation = native_validator(position, angle)
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
                'domain': domain}
    if any(a['validation']['status'] == 'unknown' for a in attempts):
        return {'status': 'unknown', 'qualification': 'hypothetical, unverified', 'search_complete': False,
                'tested': len(trials), 'native_attempts': attempts, 'domain': domain,
                'reason': 'a cheaper pose could not be natively validated; minimum unresolved'}
    return {'status': 'none_found', 'tested': len(trials), 'native_attempts': attempts,
            'search_complete': native_validator is not None, 'domain': domain,
            'reason': 'finite diagnostic set; not a continuous optimality proof'}


def _anchor(block, members):
    return block.get('anchor', members[0])


def _fixed_source(fp, policy):
    """Where the owner's fixed pose is declared; the envelope band it may use, if any."""
    from .placer import UNDECLARED
    declared = [name for name, present in (('policy fixed_refs', fp['ref'] in policy['fixed_refs']),
                                           ('native footprint lock', fp.get('locked'))) if present]
    source = {'declared_by': declared,
              'provenance': copy.deepcopy(policy.get('constraint_provenance', {}).get('fixed', {})
                                          .get(fp['ref'], UNDECLARED))}
    overhang = next((o for o in policy.get('fixed_overhangs', []) if o['reference'] == fp['ref']), None)
    if overhang is not None:
        source['overhang_envelope'] = copy.deepcopy(overhang)
    return source


def _owner_decision(alternative, fp, policy):
    """A cheaper pose for a fixed owner is a named owner decision, never applied here."""
    source = _fixed_source(fp, policy)
    record = {**alternative, 'fixed_source': source, 'applied': False, 'enforcement': FIXED_ENFORCEMENT}
    if 'saving_mm2' not in alternative:
        return record
    return {**record, 'status': 'decision_required', 'validation_status': alternative['status'],
            'decision_required': {
                'decision': 'change owner-fixed pose', 'reference': fp['ref'], 'fixed_source': source,
                'current_position_nm': list(fp['position_nm']), 'current_rotation_deg': fp['rotation_deg'],
                'candidate_position_nm': alternative['position_nm'],
                'candidate_rotation_deg': alternative['rotation_deg'],
                'saving_mm2': alternative['saving_mm2'], 'qualification': alternative['qualification']}}


def alternatives(facts, policy, fabrication, costs, plan, native_validator=None):
    """Cheapest geometry-qualified pose of an owner, or of its whole rigid block, in a finite set.

    Other parts stay put. A rigid block moves as one: every member follows its anchor
    with internal relative poses kept. A fixed owner is never moved: a cheaper pose
    becomes a ``decision_required`` record naming its fixed declaration. A fixed
    owner may use only its declared envelope band, without an edge margin.
    ``native_validator(moves)`` takes {reference: (position, rotation)}. Without one,
    poses are hypothetical and unverified. With one, cost-ordered poses must pass
    saved-board DRC and preservation before selection. Only the supplied advisory
    cost record is mutated; design facts are borrowed.
    """
    from . import placer
    outline = qualified_polygons(facts['outline'])[0]
    fixed = placer._fixed_refs(facts, policy)
    bands = overhang_bands(policy.get('fixed_overhangs', []), outline)
    blocks = {ref: block for block in plan.get('block_arrangement', {}).get('blocks', [])
              for ref in block['references']}
    footprints = {f['ref']: f for f in facts['footprints']}
    trials = {}
    for side in costs['sides'].values():
        for row in side['owners']:
            ref = row['owner']
            if row['status'] != 'measured' or row['kind'] == 'declared_obstacle' or ref not in footprints:
                continue
            block = blocks.get(ref)
            members = block['references'] if block else [ref]
            if Fraction(*row['area_mm2']) == 0:
                row['alternative'] = {'status': 'already_zero', 'reason': 'no cheaper nonnegative area'}
            elif block and set(members) & fixed:
                row['alternative'] = {'status': 'not_searched',
                                      'reason': 'rigid block with an owner-fixed member'}
            elif row['kind'] == 'courtyard_excess' and not bands.get(ref):
                # Every pose in this domain keeps the whole courtyard inside.
                # Its rigid courtyard-minus-body area is invariant, and cannot
                # be smaller than an already clipped current reserve. Only a
                # fixed owner's declared envelope band admits a partly outside pose.
                row['alternative'] = {'status': 'no_cheaper_contained_pose',
                                      'reason': 'rigid courtyard-minus-body area is invariant under full containment'}
            else:
                mover = ('block', block['block']) if block else ('part', ref)
                if mover not in trials:
                    anchor = footprints[_anchor(block, members) if block else ref]
                    parts = [footprints[r] for r in members]
                    expand = (lambda pose, parts=parts, anchor=anchor: _rigid(parts, anchor, *pose))
                    margin = 0 if ref in fixed else placer._edge_margin_nm(policy)
                    poses = _poses(parts, anchor, outline, margin, bands.get(ref, ()), rigid=bool(block))
                    trials[mover] = (expand, [(pose, _candidate_cost(facts, policy, fabrication, plan, expand(pose)))
                                              for pose in poses])
                expand, tried = trials[mover]
                validate = None if native_validator is None else (
                    lambda position, angle, expand=expand: native_validator(expand((position, angle))))
                alternative = _cheapest(row, tried, validate, BLOCK_DOMAIN if block else DOMAIN)
                if block:
                    alternative['rigid_block'] = {'block': block['block'], 'anchor': _anchor(block, members),
                                                  'references': list(members)}
                    if 'position_nm' in alternative:
                        moves = expand((tuple(alternative['position_nm']), alternative['rotation_deg']))
                        alternative['moves'] = [{'reference': r, 'position_nm': list(moves[r][0]),
                                                 'rotation_deg': moves[r][1]} for r in members]
                elif ref in fixed:
                    alternative = _owner_decision(alternative, footprints[ref], policy)
                row['alternative'] = alternative
    return costs


def _valid_ratio(value, label):
    if (not isinstance(value, list) or len(value) != 2
            or any(isinstance(v, bool) or not isinstance(v, int) for v in value)
            or value[1] <= 0 or value[0] < 0):
        raise ValueError(f'{label} must be an exact non-negative ratio [numerator, positive denominator]')


KINDS = ('declared_obstacle', 'rule_area', 'courtyard_excess')
QUALIFICATION = {'validated': 'legal under captured rules', 'unknown': 'hypothetical, unverified',
                 'rejected': 'not legal'}
# Alternative statuses with no pose or saving, and those that carry one.
UNSEARCHED = ('already_zero', 'not_searched', 'no_cheaper_contained_pose')
SAVINGS = {'native_validated': 'legal under captured rules',
           'geometry_qualified_native_unverified': 'hypothetical, unverified'}
FIXED_ENFORCEMENT = 'owner-fixed pose kept on the proposal; changing it needs the named owner decision'
FIXED_DECLARATIONS = ('policy fixed_refs', 'native footprint lock')


def _text(value, label):
    if not isinstance(value, str):
        raise ValueError(f'{label} must be text')


def _count(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f'{label} must be a non-negative integer count')


def _flag(value, label):
    if not isinstance(value, bool):
        raise ValueError(f'{label} must be a boolean')


def _sha(value, label):
    if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
        raise ValueError(f'{label} must be a lowercase SHA-256')


def _position(value, label):
    if (not isinstance(value, list) or len(value) != 2
            or any(isinstance(v, bool) or not isinstance(v, int) for v in value)):
        raise ValueError(f'{label} must be two integer nanometres')


def _angle(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or abs(value) == float('inf'):
        raise ValueError(f'{label} must be a finite number')


def _texts(value, label):
    if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
        raise ValueError(f'{label} must be a list of text')


def _object(value, label):
    if not isinstance(value, dict):
        raise ValueError(f'{label} must be a record')


def _list(value, label):
    if not isinstance(value, list):
        raise ValueError(f'{label} must be a list')


# The type of every field any reserve record may carry. Content rules follow in each validator.
FIELD_TYPES = {
    'schema': _text, 'authority': _text, 'board_sha256': _sha, 'status': _text, 'reason': _text,
    'limitation': _text, 'threshold': _object, 'sides': _object, 'findings': _list,
    'board_area_mm2': _valid_ratio, 'fraction': _valid_ratio, 'enforcement': _text, 'provenance': _object,
    'owners': _list, 'union_area_mm2': _valid_ratio, 'side_fraction': _valid_ratio, 'area_mm2': _valid_ratio,
    'saving_mm2': _valid_ratio, 'owner': _text, 'kind': _text, 'side': _text, 'id': _text, 'name': _text,
    'alternative': _object, 'reserve': _object, 'qualification': _text, 'tested': _count,
    'search_complete': _flag, 'domain': _text, 'native_attempts': _list, 'position_nm': _position,
    'rotation_deg': _angle, 'rigid_block': _object, 'moves': _list, 'validation': _object,
    'validation_status': _text, 'applied': _flag, 'fixed_source': _object, 'decision_required': _object,
    'block': _text, 'anchor': _text, 'references': _texts, 'reference': _text, 'decision': _text,
    'declared_by': _texts, 'overhang_envelope': _object, 'current_position_nm': _position,
    'current_rotation_deg': _angle, 'candidate_position_nm': _position, 'candidate_rotation_deg': _angle,
    'source_sha256': _sha, 'rules': _object, 'directory': _text, 'witness': _text,
    'write_scope': _object, 'write_scope_sha256': _sha, 'blocking_types': _texts, 'advisory_types': _texts,
    'drc_content_sha256': _sha, 'baseline_drc_content_sha256': _sha,
}


def _closed(record, required, optional, label, types=None):
    """Refuse a record missing a required field or carrying any other field; type every field."""
    if not isinstance(record, dict):
        raise ValueError(f'{label} must be a record')
    missing, extra = set(required) - set(record), set(record) - set(required) - set(optional)
    if missing or extra:
        raise ValueError(f'{label} fields do not match its status: '
                         + '; '.join(part for part in (
                             'missing ' + ', '.join(sorted(missing)) if missing else '',
                             'not allowed ' + ', '.join(sorted(extra)) if extra else '') if part))
    types = {**FIELD_TYPES, **(types or {})}
    for key, value in record.items():
        types[key](value, f'{label} {key}')


# Closed per-status allow-lists: (required, optional) fields of each record kind.
RECORD_FIELDS = {
    'measured': ({'schema', 'board_sha256', 'authority', 'status', 'threshold', 'sides', 'findings',
                  'board_area_mm2'}, set()),
    'unknown': ({'schema', 'board_sha256', 'authority', 'status', 'sides', 'findings'},
                {'threshold', 'board_area_mm2', 'reason', 'limitation'}),
}
THRESHOLD_FIELDS = ({'fraction', 'enforcement', 'provenance'}, set())
SIDE_FIELDS = {'measured': ({'status', 'owners', 'union_area_mm2', 'side_fraction'}, set()),
               'unknown': ({'status', 'owners', 'reason'}, set())}
OWNER_FIELDS = {'measured': ({'owner', 'kind', 'side', 'status', 'area_mm2', 'side_fraction'}, {'alternative'}),
                'unknown': ({'owner', 'kind', 'side', 'status', 'reason'}, set())}
FINDING_FIELDS = ({'kind', 'enforcement', 'reserve'}, set())
_SEARCH = {'status', 'reason', 'tested', 'native_attempts', 'search_complete', 'domain'}
_POSE = {'status', 'qualification', 'native_attempts', 'search_complete', 'position_nm', 'rotation_deg',
         'area_mm2', 'saving_mm2', 'tested', 'domain'}
# A fixed owner's search without a saving keeps its fixed declaration (never with a rigid block).
_FIXED = {'fixed_source', 'applied', 'enforcement'}
ALTERNATIVE_FIELDS = {
    **{status: ({'status', 'reason'}, set()) for status in UNSEARCHED},
    'none_found': (_SEARCH, {'rigid_block'} | _FIXED),
    'unknown': (_SEARCH | {'qualification'}, {'rigid_block'} | _FIXED),
    **{status: (_POSE, {'rigid_block', 'moves'}) for status in SAVINGS},
    'decision_required': (_POSE | _FIXED | {'validation_status', 'decision_required'}, set()),
}
ATTEMPT_FIELDS = ({'position_nm', 'rotation_deg', 'validation'}, set())
# Native witness summaries (reserved_area_native): status and its fixed qualification are
# required; digests, paths and finding types are optional typed witness fields.
_WITNESS = {'source_sha256', 'rules', 'directory', 'moves', 'reason', 'witness', 'board_sha256',
            'write_scope_sha256', 'blocking_types', 'advisory_types', 'drc_content_sha256',
            'baseline_drc_content_sha256'}
VALIDATION_FIELDS = {'validated': ({'status', 'qualification'}, _WITNESS),
                     'unknown': ({'status', 'qualification'}, _WITNESS),
                     'rejected': ({'status', 'qualification'}, _WITNESS | {'write_scope'})}
MOVE_FIELDS = ({'reference', 'position_nm', 'rotation_deg'}, set())
RIGID_FIELDS = ({'block', 'anchor', 'references'}, set())
FIXED_SOURCE_FIELDS = ({'declared_by', 'provenance'}, {'overhang_envelope'})
DECISION_FIELDS = ({'decision', 'reference', 'fixed_source', 'current_position_nm', 'current_rotation_deg',
                    'candidate_position_nm', 'candidate_rotation_deg', 'saving_mm2', 'qualification'}, set())


def _by_status(record, table, label):
    status = record.get('status') if isinstance(record, dict) else None
    if not isinstance(status, str) or status not in table:
        raise ValueError(f'{label} status must be one of {", ".join(table)}')
    _closed(record, *table[status], label)
    return status


def _valid_move(move, label):
    _closed(move, *MOVE_FIELDS, label)
    return move['reference'], move['position_nm'], move['rotation_deg']


def _valid_attempts(alternative, anchor, members, label):
    """Each native attempt has a pose and a known validation status with its fixed qualification.

    A witness's moves, when present, cover the moved members with the anchor at the attempted pose.
    """
    statuses = []
    for attempt in alternative['native_attempts']:
        _closed(attempt, *ATTEMPT_FIELDS, f'{label} native attempt')
        validation = attempt['validation']
        status = validation.get('status')
        if not isinstance(status, str) or status not in QUALIFICATION or validation.get('qualification') != QUALIFICATION[status]:
            raise ValueError(f'{label} native attempt validation must be validated, rejected or unknown '
                             'with its fixed qualification')
        _closed(validation, *VALIDATION_FIELDS[status], f'{label} native attempt validation')
        if 'rules' in validation:
            rules = validation['rules']
            if set(rules) != {'.kicad_pro', '.kicad_dru'}:
                raise ValueError(f'{label} native attempt rules must name .kicad_pro and .kicad_dru')
            for suffix, value in rules.items():
                if value is not None:
                    _sha(value, f'{label} native attempt rules {suffix}')
        if status == 'validated' and validation.get('blocking_types', []):
            raise ValueError(f'{label} validated native attempt has blocking findings')
        if 'moves' in validation:
            moves = {}
            for move in validation['moves']:
                reference, position, angle = _valid_move(move, f'{label} native attempt move')
                moves[reference] = (position, angle)
            if (set(moves) != set(members)
                    or moves[anchor] != (attempt['position_nm'], attempt['rotation_deg'])):
                raise ValueError(f'{label} native attempt witness moves disagree with the attempted pose')
        statuses.append(status)
    return statuses


def _valid_alternative(alternative, row, label):
    """Refuse an alternative outside its status's closed allow-list, or internally inconsistent.

    Every field is allowed, required and typed by status before any status rule runs.
    Unsearched, unknown and none_found records carry no selected pose, no saving and no
    legal qualification. ``native_validated`` needs its selected pose to be the one
    validated attempt, after only rejected or unknown cheaper attempts; an unknown attempt
    makes ``search_complete`` false. Unknown-only attempts are never validated or complete.
    """
    status = _by_status(alternative, ALTERNATIVE_FIELDS, label)
    if row.get('status') != 'measured':
        raise ValueError(f'{label} belongs to an unmeasured owner')
    if status == 'already_zero' and Fraction(*row['area_mm2']) != 0:
        raise ValueError(f'{label} already_zero but the owner area is not zero')
    if status in UNSEARCHED:
        return
    if status == 'decision_required' and alternative['validation_status'] not in SAVINGS:
        raise ValueError(f'{label} decision_required needs a saving validation_status and applied false')
    if 'qualification' in alternative:
        expected = (SAVINGS.get(alternative['validation_status']) if status == 'decision_required'
                    else SAVINGS.get(status, QUALIFICATION['unknown']))
        if alternative['qualification'] != expected:
            raise ValueError(f'{label} {status} must be qualified {expected!r}')
    fixed = set(alternative) & _FIXED
    if fixed and fixed != _FIXED:
        raise ValueError(f'{label} fixed declaration needs fixed_source, applied and enforcement')
    if fixed:
        if 'rigid_block' in alternative:
            raise ValueError(f'{label} a fixed owner is never searched within a rigid block')
        if alternative['applied'] is not False or alternative['enforcement'] != FIXED_ENFORCEMENT:
            raise ValueError(f'{label} owner-fixed record must have applied false and enforcement {FIXED_ENFORCEMENT!r}')
        source = alternative['fixed_source']
        _closed(source, *FIXED_SOURCE_FIELDS, f'{label} fixed_source')
        if not source['declared_by'] or any(d not in FIXED_DECLARATIONS for d in source['declared_by']):
            raise ValueError(f'{label} fixed_source declared_by must name {" or ".join(FIXED_DECLARATIONS)}')
        if (not isinstance(source['provenance'].get('class'), str)
                or source['provenance'].get('enforcement') not in ('hard', 'soft')):
            raise ValueError(f'{label} fixed_source provenance needs a class and a hard/soft enforcement')
    block = alternative.get('rigid_block')
    if block is not None:
        _closed(block, *RIGID_FIELDS, f'{label} rigid_block')
        if (not block['references'] or len(set(block['references'])) != len(block['references'])
                or block['anchor'] not in block['references'] or row['owner'] not in block['references']):
            raise ValueError(f'{label} rigid_block must name its anchor and owner among distinct references')
        anchor, members = block['anchor'], block['references']
    else:
        anchor, members = row['owner'], [row['owner']]
    if alternative['domain'] != (BLOCK_DOMAIN if block is not None else DOMAIN):
        raise ValueError(f'{label} domain must be the declared {"rigid block" if block else "single part"} pose set')
    statuses = _valid_attempts(alternative, anchor, members, label)
    complete = alternative['search_complete']
    if status == 'decision_required':
        return _valid_decision(alternative, row, statuses, label)
    if status not in SAVINGS:
        if 'validated' in statuses:
            raise ValueError(f'{label} {status} carries no saving and no validated attempt')
        if status == 'unknown' and ('unknown' not in statuses or complete):
            raise ValueError(f'{label} unknown needs an unknown native attempt, an incomplete search '
                             'and hypothetical qualification')
        if status == 'none_found' and 'unknown' in statuses:
            raise ValueError(f'{label} none_found cannot follow an unknown native attempt')
        if status == 'none_found' and statuses and not complete:
            raise ValueError(f'{label} none_found after native attempts is a complete search')
        return
    _valid_saving(alternative, row, statuses, status, label)
    if block is not None:
        if 'moves' not in alternative:
            raise ValueError(f'{label} rigid block moves must cover every member')
        moves = [_valid_move(m, f'{label} rigid block move') for m in alternative['moves']]
        if [m[0] for m in moves] != members or next(
                (p, a) for r, p, a in moves if r == anchor) != (alternative['position_nm'],
                                                                alternative['rotation_deg']):
            raise ValueError(f'{label} rigid block moves must cover every member with the anchor at the selected pose')
    elif 'moves' in alternative:
        raise ValueError(f'{label} moves belong only to a rigid block alternative')


def _valid_saving(alternative, row, statuses, status, label):
    """A selected pose's saving and, for native_validated, its validated witness."""
    saving = Fraction(*alternative['saving_mm2'])
    if saving <= 0 or Fraction(*alternative['area_mm2']) + saving != Fraction(*row['area_mm2']):
        raise ValueError(f'{label} saving_mm2 must be positive and equal the owner area less the alternative area')
    complete = alternative['search_complete']
    if status == 'geometry_qualified_native_unverified':
        if statuses or complete:
            raise ValueError(f'{label} geometry-only alternative has no native attempt and no complete search')
    elif (not statuses or statuses[-1] != 'validated' or 'validated' in statuses[:-1]
          or alternative['native_attempts'][-1]['position_nm'] != alternative['position_nm']
          or alternative['native_attempts'][-1]['rotation_deg'] != alternative['rotation_deg']):
        raise ValueError(f'{label} native_validated needs its selected pose to be the validated native attempt')
    elif complete != ('unknown' not in statuses):
        raise ValueError(f'{label} search_complete must be false exactly when a native attempt is unknown')


def _valid_decision(record, row, statuses, label):
    """A fixed owner's cheaper pose is a named, unapplied decision consistent with its validation."""
    _valid_saving(record, row, statuses, record['validation_status'], label)
    decision = record['decision_required']
    _closed(decision, *DECISION_FIELDS, f'{label} decision_required')
    expected = {'decision': 'change owner-fixed pose', 'reference': row['owner'],
                'fixed_source': record['fixed_source'],
                'candidate_position_nm': record['position_nm'], 'candidate_rotation_deg': record['rotation_deg'],
                'saving_mm2': record['saving_mm2'], 'qualification': record['qualification']}
    if any(decision[k] != v for k, v in expected.items()):
        raise ValueError(f'{label} decision_required disagrees with its alternative: '
                         + ', '.join(sorted(k for k, v in expected.items() if decision[k] != v)))


def _valid_owner(row, side, label):
    """One reserve owner, wherever it is shown: its identity, status, measurements and alternative.

    A rule-area owner also names its rule-area id and name once measured.
    """
    if not isinstance(row, dict) or row.get('kind') not in KINDS:
        raise ValueError(f'{label} kind must be one of {", ".join(KINDS)}')
    if not isinstance(row.get('status'), str) or row['status'] not in OWNER_FIELDS:
        raise ValueError(f'{label} status must be one of {", ".join(OWNER_FIELDS)}')
    required, optional = OWNER_FIELDS[row['status']]
    if row['kind'] == 'rule_area':
        required, optional = ((required | {'id', 'name'}, optional) if row['status'] == 'measured'
                              else (required, optional | {'id', 'name'}))
    _closed(row, required, optional, label)
    if row['side'] != side:
        raise ValueError(f'{label} side {row["side"]!r} is not side {side}')
    if 'alternative' in row:
        _valid_alternative(row['alternative'], row, f'{label} alternative')
    return row['status']


def validate(record):
    """Refuse a reserve record outside its closed allow-lists, not advisory, or incompletely measured.

    Every record kind (the record, its threshold, sides, owners, findings, alternatives,
    owner decisions, rigid blocks and native attempts) has a closed per-status set of
    required and optional fields, each typed; any other field, a missing one or a wrong
    type refuses before any status rule. Typed unknowns pass as unknown. ``measured``
    needs both board sides measured with exact ratios, and a side is measured only when
    each of its owners is. Each finding's reserve must equal its side's owner row.
    Shared by every packet ingress.
    """
    if not isinstance(record, dict) or record.get('schema') != SCHEMA:
        raise ValueError(f'reserved area must be a {SCHEMA} record')
    if record.get('authority') != AUTHORITY:
        raise ValueError(f'reserved area authority must be exactly {AUTHORITY!r}')
    _sha(record.get('board_sha256'), 'reserved area board_sha256')
    status = _by_status(record, RECORD_FIELDS, 'reserved area')
    if 'threshold' in record:
        threshold = record['threshold']
        if threshold.get('enforcement') != 'advisory':
            raise ValueError('reserved area threshold must be advisory')
        _closed(threshold, *THRESHOLD_FIELDS, 'reserved area threshold', {'provenance': _text})
    findings, sides = record['findings'], record['sides']
    if any(not isinstance(f, dict) or f.get('enforcement') != 'advisory' for f in findings):
        raise ValueError('reserved area findings must be a list of advisory findings')
    if not set(sides) <= {'F', 'B'}:
        raise ValueError('reserved area sides must map board sides F/B')
    rows = {}
    for side, measured in sides.items():
        label = f'reserved area side {side}'
        side_status = _by_status(measured, SIDE_FIELDS, label)
        for row in measured['owners']:
            if not isinstance(row, dict):
                raise ValueError(f'{label} owner must be a record')
            if _valid_owner(row, side, f'{label} owner') != 'measured' and side_status == 'measured':
                raise ValueError(f'{label} is measured but an owner is unknown')
            rows.setdefault((side, row['kind'], row.get('id'), row['owner']), []).append(row)
    for finding in findings:
        reserve = finding.get('reserve')
        if finding.get('kind') != 'large_reserve' or not isinstance(reserve, dict):
            raise ValueError('reserved area finding must be a large_reserve finding naming its reserve')
        _closed(finding, *FINDING_FIELDS, 'reserved area finding')
        side = reserve.get('side')
        if not isinstance(side, str) or side not in sides:
            raise ValueError('reserved area finding reserve names no measured board side')
        if _valid_owner(reserve, side, 'reserved area finding reserve') != 'measured':
            raise ValueError('reserved area finding reserve must be measured')
        # Full equality, so two owners sharing a key cannot lend each other a measurement.
        if reserve not in rows.get((side, reserve['kind'], reserve.get('id'), reserve['owner']), []):
            raise ValueError('reserved area finding reserve disagrees with its side owner (side, owner, kind, id)')
    if status == 'measured':
        if set(sides) != {'F', 'B'} or any(s['status'] != 'measured' for s in sides.values()):
            raise ValueError('reserved area is measured only when both sides are measured')
    return record


def review_input(run_dir, manifest):
    """The scope's saved-board reserve record, read from the sealed capture and bound to its board.

    ``scope.reserved_area`` names a captured ``agent-ee.reserved-area/1`` document, or a
    ``placement-judgment.json``/``placement-result.json`` carrying one. A missing,
    uncaptured, changed, malformed or other-board record raises, so the packet is not
    built and the review stays incomplete; it is never silently dropped. The scope board
    must itself be captured: two absent identities never bind.
    """
    import hashlib
    from pathlib import Path
    from .catalog import strict_json
    scope = manifest['scope']
    rel, board = scope['reserved_area'], scope['board']
    if not isinstance(rel, str) or rel not in manifest['files']:
        raise ValueError(f'reserved_area {rel!r} is not a captured input')
    if not isinstance(board, str) or board not in manifest['files']:
        raise ValueError(f'scope board {board!r} is not a captured input; the reserve binds to no board')
    raw = (Path(run_dir) / 'inputs' / rel).read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest['files'][rel]:
        raise ValueError('captured reserved_area changed; evidence is stale')
    document = strict_json(raw)
    record = document if isinstance(document, dict) and document.get('schema') == SCHEMA else (
        document.get('reserved_area') if isinstance(document, dict) else None)
    if not isinstance(record, dict) or record.get('schema') != SCHEMA:
        raise ValueError(f'reserved_area {rel!r} carries no {SCHEMA} record')
    validate(record)
    if record['board_sha256'] != manifest['files'][board]:
        raise ValueError(f"reserved area describes board {record.get('board_sha256')}, not the captured {board}")
    return {'path': 'inputs/' + rel, 'sha256': manifest['files'][rel], 'record': record}


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
