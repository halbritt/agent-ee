"""Per-requirement placement predicates over a saved-board probe and native DRC.

Pure functions over integer nanometres and exact rationals. Each predicate
returns ``status`` pass, defect or unknown. A proved defect survives unrelated
unknown coverage; pass requires complete coverage. Helper geometry never
manufactures a native DRC item: where native DRC is the authority, a helper
disagreement leaves the predicate unknown.
"""
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import hashlib
import math
import re

from .placement import (area2, boxes_meet, courtyard_bounds, edges, intersection_area, intersects, judge,
                        on_segment, qualified_polygons)
from .symbol_library import LibraryError, nanometres

CRITERIA = ('courtyard-overlap/1', 'outline-containment/1', 'edge-clearance/1',
            'footprint-keepout/1', 'connector-edge/1', 'ratsnest-mst/1', 'imu-axes/1',
            'placement-groups/1', 'keep-apart/1', 'electrical-locality/1')
SIDE_LAYER = {'F': 'F.Cu', 'B': 'B.Cu'}
AXES = {'x+': (1, 0), 'x-': (-1, 0), 'y+': (0, 1), 'y-': (0, -1)}
INSERTIONS = ('through_edge', 'from_top', 'from_bottom')
SHA256 = re.compile(r'[0-9a-f]{64}\Z')
# kicad-route 08179cd result.json: routing.blocked_by.top footprint entries.
ROUTE_FOOTPRINT = re.compile(r'footprint:(.+)\Z')


def verdict(defects, unknown):
    return 'defect' if defects else 'unknown' if unknown else 'pass'


def mm(nm):
    return float(Fraction(nm) / 10**6)


def exact_nm(value, label):
    """Declared millimetres as exact integer nanometres; never rounded."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise ValueError(f'{label} must be a number')
    try:
        nm = nanometres(str(value))
    except LibraryError as exc:
        raise ValueError(f'{label} must be exact to 1 nm') from exc
    if nm <= 0:
        raise ValueError(f'{label} must be positive')
    return nm


# ------------------------------------------------------------------ native DRC

def validate_drc(report):
    """Structural shape of a kicad-cli DRC JSON report; raises ValueError."""
    if not isinstance(report, dict) or not isinstance(report.get('kicad_version'), str):
        raise ValueError('DRC report lacks kicad_version')
    for key in ('violations', 'unconnected_items', 'schematic_parity', 'ignored_checks'):
        if not isinstance(report.get(key), list):
            raise ValueError(f'DRC report lacks {key}')
    for violation in report['violations']:
        if (not isinstance(violation, dict) or not isinstance(violation.get('type'), str)
                or not isinstance(violation.get('items'), list)):
            raise ValueError('malformed DRC violation record')
        for item in violation['items']:
            if not isinstance(item, dict) or not isinstance(item.get('uuid'), str):
                raise ValueError('DRC violation item lacks a native UUID')
    severities = report.get('included_severities')
    if not isinstance(severities, list) or not {'error', 'warning', 'exclusion'} <= set(severities):
        raise ValueError('DRC report was not produced with all severities')
    return report


def native_items(report, kind):
    """Every record of one native type, whatever its severity (exclusions included)."""
    return [{'type': v['type'], 'severity': v.get('severity'), 'description': v.get('description'),
             'items': [{'uuid': i['uuid'], 'description': i.get('description'), 'pos': i.get('pos')}
                       for i in v['items']]}
            for v in report['violations'] if v['type'] == kind]


def ignored(report, kind):
    return any(isinstance(c, dict) and c.get('key') == kind for c in report['ignored_checks'])


# ---------------------------------------------------------------- courtyards

def courtyards(fp, inner=False):
    """Qualified courtyard polygons by side, or (None, reason) for unknown coverage.

    The outer bound by default; ``inner`` selects the subset bound of a curved
    courtyard (AGENTEE-212), the one that may prove a helper-side intersection."""
    records = fp.get('courtyards')
    if not isinstance(records, dict):
        return None, 'courtyard inventory missing'
    if fp.get('side') not in SIDE_LAYER:
        return None, 'unsupported footprint side'
    if fp['side'] not in records:
        return None, 'courtyard missing on mounting side'
    polygons = {}
    for side, record in sorted(records.items()):
        if side not in SIDE_LAYER:
            return None, 'unsupported courtyard side'
        try:
            polygons[side] = courtyard_bounds(record)[1 if inner else 0]
        except ValueError as exc:
            return None, f'courtyard {side}: {exc}'
    return polygons, None


def _overlap(left, right):
    return sum(intersection_area(a, b) for a in left for b in right)


def courtyard_overlap(probe, report):
    """Native courtyards_overlap items are the defect authority.

    A pair whose exact helper bounds overlap only in a curved courtyard's
    tolerance band (outer bounds overlap, inner bounds do not) is unknown
    (courtyard_approximation): native DRC silence never proves it clear."""
    names = {f['uuid']: f['ref'] for f in probe['footprints']}
    defects, unknown, disagreements = [], [], []
    native_pairs = set()
    for record in native_items(report, 'courtyards_overlap'):
        uuids = [i['uuid'] for i in record['items']]
        native_pairs.add(frozenset(uuids))
        defects.append({'kind': 'courtyards_overlap', 'refs': [names.get(u) for u in uuids], 'native': record})
    for kind in ('courtyards_overlap', 'malformed_courtyard'):
        if ignored(report, kind):
            unknown.append({'kind': 'native_check_ignored', 'check': kind})
    for record in native_items(report, 'malformed_courtyard'):
        for item in record['items']:
            unknown.append({'kind': 'malformed_courtyard', 'ref': names.get(item['uuid']),
                            'uuid': item['uuid'], 'native': record})
    qualified = []
    for fp in sorted(probe['footprints'], key=lambda f: (f['ref'], f['uuid'])):
        polygons, reason = courtyards(fp, inner=True)
        if polygons is None:
            if reason == 'courtyard missing on mounting side' or reason == 'unsupported footprint side':
                unknown.append({'kind': 'courtyard_missing', 'ref': fp['ref'], 'uuid': fp['uuid'], 'reason': reason})
            else:
                disagreements.append({'kind': 'cross_check_unavailable', 'ref': fp['ref'], 'reason': reason})
            continue
        qualified.append((fp, polygons, courtyards(fp)[0]))
    for i, (fp, left, left_outer) in enumerate(qualified):
        for other, right, right_outer in qualified[i+1:]:
            for side in sorted(set(left) & set(right)):
                pair = frozenset((fp['uuid'], other['uuid']))
                if pair in native_pairs or not boxes_meet(left_outer[side], right_outer[side]):
                    continue
                area = _overlap(left[side], right[side])
                if area > 0:
                    unknown.append({'kind': 'helper_disagreement', 'refs': [fp['ref'], other['ref']], 'side': side,
                                    'overlap_area_mm2': float(area / 10**12),
                                    'reason': 'helper geometry overlaps but native DRC reported no item'})
                elif ((left[side] != left_outer[side] or right[side] != right_outer[side]) and
                      _overlap(left_outer[side], right_outer[side]) > 0):
                    unknown.append({'kind': 'courtyard_approximation', 'refs': [fp['ref'], other['ref']],
                                    'side': side, 'reason': 'overlap lies only between the declared curve bounds'})
    return {'criterion': 'courtyard-overlap/1', 'status': verdict(defects, unknown),
            'defects': defects, 'unknown': unknown, 'notes': disagreements}


def outline_containment(probe, overhangs=None):
    """The reviewed 53 exact polygon judge, restricted to outside-outline area.

    ``overhangs`` are envelope-bound fixed-courtyard overhang declarations
    (``envelope_overhangs``): area inside a declared band is reported, area
    beyond it, or for an undeclared reference, remains a defect."""
    report = judge(probe, overhangs=overhangs)
    defects = [f for f in report['hard_failures'] if f['kind'] == 'outside_outline']
    unknown = [u for u in report['unknown'] if 'refs' not in u]
    result = {'criterion': 'outline-containment/1', 'status': verdict(defects, unknown),
              'defects': defects, 'unknown': unknown}
    if overhangs is not None:
        result['declared_overhangs'] = report['declared_overhangs']
    return result


# --------------------------------------------------------------- edge clearance

def point_segment2(p, a, b):
    dx, dy = b[0]-a[0], b[1]-a[1]
    t = min(max(Fraction((p[0]-a[0])*dx + (p[1]-a[1])*dy, dx*dx + dy*dy), Fraction(0)), Fraction(1))
    x, y = a[0] + t*dx - p[0], a[1] + t*dy - p[1]
    return x*x + y*y


def segment_distance2(a, b, c, d):
    if intersects(a, b, c, d):
        return Fraction(0)
    return min(point_segment2(a, c, d), point_segment2(b, c, d), point_segment2(c, a, b), point_segment2(d, a, b))


def keep_apart(probe, policy):
    """Minimum projected courtyard separation, including opposite board sides."""
    from .placement import keep_apart_rules

    def separation2(left, right):
        return (Fraction(0) if _overlap(left, right) else
                min(segment_distance2(a, b, c, d) for p in left for q in right
                    for a, b in edges(p) for c, d in edges(q)))

    rules = keep_apart_rules(policy)
    defects, unknown, measured = [], [], []
    if not rules:
        unknown.append({'kind': 'keep_apart_rules_missing'})
    for rule in rules:
        minimum = exact_nm(rule['min_distance_mm'], 'min_distance_mm')
        for other in rule['others']:
            record = {'ref': rule['ref'], 'other': other, 'minimum_mm': mm(minimum)}
            polygons, inner_polygons = [], []
            for ref in (rule['ref'], other):
                matches = [fp for fp in probe['footprints'] if fp['ref'] == ref]
                if len(matches) != 1:
                    unknown.append({**record, 'kind': 'footprint_missing_or_ambiguous', 'reference': ref})
                    break
                sides, reason = courtyards(matches[0])
                if sides is None:
                    unknown.append({**record, 'kind': 'courtyard', 'reference': ref, 'reason': reason})
                    break
                polygons.append([p for contours in sides.values() for p in contours])
                inner, _ = courtyards(matches[0], inner=True)
                inner_polygons.append([p for contours in inner.values() for p in contours])
            if len(polygons) != 2:
                continue
            # Outer separation is a lower bound; inner separation is an upper
            # bound. Only the corresponding side can prove pass or defect.
            distance2 = separation2(*polygons)
            inner_distance2 = separation2(*inner_polygons)
            status = ('pass' if distance2 >= minimum**2 else
                      'defect' if inner_distance2 < minimum**2 else 'unknown')
            record.update(distance_mm=math.sqrt(distance2) / 10**6,
                          distance_squared_nm={'numerator': distance2.numerator,
                                               'denominator': distance2.denominator},
                          inner_distance_mm=math.sqrt(inner_distance2) / 10**6,
                          inner_distance_squared_nm={'numerator': inner_distance2.numerator,
                                                     'denominator': inner_distance2.denominator},
                          status=status)
            measured.append(record)
            if record['status'] == 'defect':
                defects.append({'kind': 'keep_apart', 'refs': [rule['ref'], other], **record})
            elif record['status'] == 'unknown':
                unknown.append({'kind': 'courtyard_approximation', 'refs': [rule['ref'], other], **record,
                                'reason': 'separation lies between the declared curve bounds'})
    return {'criterion': 'keep-apart/1', 'status': verdict(defects, unknown),
            'defects': defects, 'unknown': unknown, 'measured': measured, 'policy': policy}


def proven_outline(probe):
    polygons = qualified_polygons(probe.get('outline'))
    if len(polygons) != 1:
        raise ValueError('multiple outline contours unsupported')
    return polygons[0]


def edge_clearance(probe, minimum_nm):
    """Exact minimum courtyard-to-Edge.Cuts distance against a declared minimum."""
    try:
        outline = proven_outline(probe)
    except ValueError as exc:
        return {'criterion': 'edge-clearance/1', 'status': 'unknown', 'defects': [], 'measured': [],
                'unknown': [{'kind': 'outline', 'reason': str(exc)}], 'minimum_mm': mm(minimum_nm)}
    defects, unknown, measured = [], [], []
    def clearance(polygon):
        outside = Fraction(abs(area2(polygon)), 2) - intersection_area(polygon, outline)
        distance2 = min(segment_distance2(a, b, c, d) for a, b in edges(polygon) for c, d in edges(outline))
        return outside, distance2, outside > 0 or distance2 < minimum_nm * minimum_nm
    for fp in sorted(probe['footprints'], key=lambda f: (f['ref'], f['uuid'])):
        polygons, reason = courtyards(fp)
        if polygons is None:
            unknown.append({'kind': 'courtyard', 'ref': fp['ref'], 'uuid': fp['uuid'], 'reason': reason})
            continue
        inner, _ = courtyards(fp, inner=True)
        for side, contours in polygons.items():
            for index, polygon in enumerate(contours):
                outside, distance2, short = clearance(polygon)
                record = {'ref': fp['ref'], 'uuid': fp['uuid'], 'side': side, 'inside_outline': outside == 0,
                          'distance_mm': round(math.sqrt(distance2) / 10**6, 6), 'minimum_mm': mm(minimum_nm)}
                measured.append(record)
                if not short:
                    continue
                # A curved courtyard's outer bound may be short only by its tolerance;
                # the defect is proved on the inner bound, which lies within the true curve.
                if clearance(inner[side][index])[2]:
                    defects.append({'kind': 'courtyard_edge_clearance', **record})
                else:
                    unknown.append({'kind': 'courtyard_approximation', 'ref': fp['ref'], 'uuid': fp['uuid'],
                                    'side': side, 'reason': 'edge clearance lies between the declared curve bounds'})
    return {'criterion': 'edge-clearance/1', 'status': verdict(defects, unknown), 'defects': defects,
            'unknown': unknown, 'measured': measured, 'minimum_mm': mm(minimum_nm)}


# ---------------------------------------------------------------- keepouts

def footprint_keepout(probe, report):
    """Native items_not_allowed citing a footprint, over a complete rule-area inventory.

    A curved courtyard meeting a keepout only in its tolerance band (outer bound
    intersects, inner bound does not) is unknown (courtyard_approximation)."""
    areas = probe.get('rule_areas')
    if not isinstance(areas, list):
        raise ValueError('probe has no rule-area inventory')
    names = {f['uuid']: f['ref'] for f in probe['footprints']}
    defects, unknown, inventory, applicable = [], [], [], []
    if ignored(report, 'items_not_allowed'):
        unknown.append({'kind': 'native_check_ignored', 'check': 'items_not_allowed'})
    for area in areas:
        if not isinstance(area, dict) or not isinstance(area.get('forbid'), dict) or not isinstance(area.get('layers'), list):
            raise ValueError('malformed rule-area record')
        entry = {'uuid': area.get('uuid'), 'name': area.get('name'), 'owner': area.get('owner'),
                 'layers': area['layers'], 'forbids_footprints': area['forbid'].get('footprints') is True}
        inventory.append(entry)
        if area['forbid'].get('footprints') is not True:
            entry['status'] = 'not_applicable'
            continue
        if area.get('owner') != 'board':
            entry['status'] = 'unsupported'
            unknown.append({'kind': 'rule_area', 'uuid': area.get('uuid'),
                            'reason': 'footprint-owned footprint keepout semantics unqualified'})
            continue
        try:
            polygon, = qualified_polygons(area.get('outline'))
        except ValueError as exc:
            entry['status'] = 'unsupported'
            unknown.append({'kind': 'rule_area', 'uuid': area.get('uuid'), 'reason': str(exc)})
            continue
        entry['status'] = 'evaluated'
        applicable.append((area, polygon))
    flagged = {}
    for record in native_items(report, 'items_not_allowed'):
        for item in record['items']:
            if item['uuid'] in names:
                flagged.setdefault(item['uuid'], []).append(record)
    for fp in sorted(probe['footprints'], key=lambda f: (f['ref'], f['uuid'])):
        layer = SIDE_LAYER.get(fp.get('side'))
        here = [(a, p) for a, p in applicable if layer in a['layers']]
        hits, band = [], []
        if here:
            polygons, reason = courtyards(fp, inner=True)
            if polygons is None:
                unknown.append({'kind': 'courtyard', 'ref': fp['ref'], 'uuid': fp['uuid'], 'reason': reason})
            else:
                outer = courtyards(fp)[0][fp['side']]
                def meets(contours, p):
                    return any(intersection_area(c, p) > 0 for c in contours)
                hits = sorted(a['uuid'] for a, p in here if meets(polygons[fp['side']], p))
                if outer != polygons[fp['side']]:
                    band = sorted(a['uuid'] for a, p in here if a['uuid'] not in hits and meets(outer, p))
        if fp['uuid'] in flagged:
            defects.append({'kind': 'footprint_in_keepout', 'ref': fp['ref'], 'uuid': fp['uuid'],
                            'helper_areas': hits, 'native': flagged[fp['uuid']]})
        elif hits:
            unknown.append({'kind': 'helper_disagreement', 'ref': fp['ref'], 'areas': hits,
                            'reason': 'helper geometry intersects a footprint keepout but native DRC reported no item'})
        elif band:
            unknown.append({'kind': 'courtyard_approximation', 'ref': fp['ref'], 'uuid': fp['uuid'], 'areas': band,
                            'reason': 'keepout intersection lies only between the declared curve bounds'})
    return {'criterion': 'footprint-keepout/1', 'status': verdict(defects, unknown), 'defects': defects,
            'unknown': unknown, 'rule_areas': inventory}


def envelope_overhangs(validation):
    """Fixed-courtyard overhang declarations of an AGENTEE-85 envelope validation record.

    One entry per fixed footprint that declares ``courtyard_overhangs``: each
    named outline edge in exact nanometres with its outward axis and maximum
    distance, citing the envelope id, hash and field pointer (AGENTEE-212)."""
    if not isinstance(validation, dict) or validation.get('schema') != 'agent-ee.envelope-validation/1':
        raise ValueError('expected an agent-ee.envelope-validation/1 record')
    named = {e['name']: e for e in validation['outline']['edges']}
    out = []
    for index, fixed in enumerate(validation['fixed_footprints']):
        if 'courtyard_overhangs' not in fixed:
            continue
        edges = []
        for overhang in fixed['courtyard_overhangs']:
            edge = named[overhang['edge']]
            try:
                distance = nanometres(overhang['max_mm'])
            except (LibraryError, TypeError) as exc:
                raise ValueError('envelope numbers must be exact decimal text') from exc
            edges.append({'name': edge['name'], 'start_nm': list(_point_nm(edge['start_mm'], 'edge start')),
                          'end_nm': list(_point_nm(edge['end_mm'], 'edge end')), 'outward': edge['outward'],
                          'max_nm': distance})
        out.append({'reference': fixed['reference'], 'edges': edges,
                    'source': {'envelope_id': validation['envelope_id'],
                               'envelope_sha256': validation['envelope_sha256'],
                               'pointer': f'/fixed_footprints/{index}/courtyard_overhangs'}})
    return out


# ------------------------------------------------------ connector edge/access

def _point_nm(value, label):
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f'{label} must be [x, y]')
    try:
        return tuple(nanometres(str(v)) for v in value)
    except LibraryError as exc:
        raise ValueError(f'{label} must be exact nanometres') from exc


def envelope_declarations(validation):
    """Normalized declarations from an AGENTEE-85 agent-ee.envelope-validation/1 record."""
    if not isinstance(validation, dict) or validation.get('schema') != 'agent-ee.envelope-validation/1':
        raise ValueError('expected an agent-ee.envelope-validation/1 record')
    named = {e['name']: e for e in validation['outline']['edges']}
    out = []
    for index, fixed in enumerate(validation['fixed_footprints']):
        edge = named[fixed['edge']]
        if edge.get('outward') not in AXES:
            raise ValueError(f"edge {edge['name']} has no axis-aligned outward direction")
        try:
            rotation = Decimal(fixed['rotation_deg'])
            tolerance = nanometres(fixed['tolerance_mm'])
            inset = nanometres(fixed['inset_mm'])
        except (InvalidOperation, LibraryError, TypeError) as exc:
            raise ValueError('envelope numbers must be exact decimal text') from exc
        out.append({'reference': fixed['reference'], 'side': {'front': 'F', 'back': 'B'}[fixed['side']],
                    'edge': {'name': edge['name'], 'start': _point_nm(edge['start_mm'], 'edge start'),
                             'end': _point_nm(edge['end_mm'], 'edge end'), 'outward': edge['outward']},
                    'anchor': _point_nm(fixed['anchor_mm'], 'anchor'), 'inset_nm': inset,
                    'tolerance_nm': tolerance, 'rotation_deg': rotation, 'insertion': fixed['insertion'],
                    'local_mating_axis': None,
                    'source': {'envelope_id': validation['envelope_id'],
                               'envelope_sha256': validation['envelope_sha256'],
                               'pointer': f'/fixed_footprints/{index}'}})
    return out


def board_axis(local_axis, rotation, side):
    """Footprint-local axis in board axes: mirror y on the back, then rotate.

    Qualified against pcbnew pad transforms (front/back, all quarter turns).
    Returns None for a rotation that is not an exact quarter turn.
    """
    if rotation % 90:
        return None
    x, y = AXES[local_axis]
    if side == 'B':
        y = -y
    cos, sin = {0: (1, 0), 90: (0, 1), 180: (-1, 0), 270: (0, -1)}[int(rotation % 360)]
    vector = (x*cos + y*sin, -x*sin + y*cos)
    return next(name for name, v in AXES.items() if v == vector)


def decimal_text(value):
    """Exact positional decimal text without exponent or trailing zeros."""
    out = format(value, 'f')
    out = out.rstrip('0').rstrip('.') if '.' in out else out
    return '0' if out in ('', '-0') else out


def native_rotation(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('footprint rotation must be finite')
    # Decimal remainder keeps the dividend's sign; KiCad saves 270 as -90.
    return (Decimal(repr(float(value))) % 360 + 360) % 360


def connector_access(declaration, footprints, outline):
    """One declared connector: side, anchor within tolerance, rotation, facing.

    The declaration is authority; nothing is inferred from the nearest edge.
    """
    ref, edge = declaration['reference'], declaration['edge']
    result = {'reference': ref, 'edge': edge['name'], 'uuid': None, 'measured': {},
              'violations': [], 'reasons': [], 'source': declaration.get('source')}
    matches = [f for f in footprints if f['ref'] == ref]
    if len(matches) != 1:
        result['reasons'].append('declared reference ' + ('missing' if not matches else 'duplicated') + ' on the board')
        result['status'] = 'unknown'
        return result
    fp = matches[0]
    result['uuid'] = fp['uuid']
    try:
        polygon, = qualified_polygons(outline)
        if not any(on_segment(a, b, edge['start']) and on_segment(a, b, edge['end']) for a, b in edges(polygon)):
            result['reasons'].append(f"declared edge {edge['name']} is not part of the saved outline")
    except ValueError as exc:
        result['reasons'].append(f'saved outline not proven: {exc}')
    if fp['side'] != declaration['side']:
        result['violations'].append({'kind': 'side', 'declared': declaration['side'], 'actual': fp['side']})
    (x, y), (ax, ay) = fp['position_nm'], declaration['anchor']
    dx, dy = x - ax, y - ay
    sx, sy = edge['start']
    ox, oy = AXES[edge['outward']]
    # Signed inward distance of the footprint origin from the declared edge line.
    inset = -((x - sx) * ox + (y - sy) * oy)
    tolerance = declaration['tolerance_nm']
    declared_inset = declaration.get('inset_nm')
    result['measured'] = {'origin_mm': [mm(x), mm(y)], 'anchor_mm': [mm(ax), mm(ay)],
                          'distance_to_anchor_mm': round(math.hypot(dx, dy) / 10**6, 6),
                          'inset_mm': mm(inset), 'tolerance_mm': mm(tolerance),
                          'declared_inset_mm': None if declared_inset is None else mm(declared_inset)}
    if dx*dx + dy*dy > tolerance * tolerance:
        result['violations'].append({'kind': 'off_declared_edge_position', 'edge': edge['name'],
                                     'distance_mm': result['measured']['distance_to_anchor_mm'],
                                     'inset_mm': mm(inset), 'declared_inset_mm': result['measured']['declared_inset_mm'],
                                     'tolerance_mm': mm(tolerance)})
    rotation = native_rotation(fp['rotation_deg'])
    result['measured']['rotation_deg'] = decimal_text(rotation)
    declared_rotation = declaration.get('rotation_deg')
    if declared_rotation is not None and rotation != declared_rotation % 360:
        result['violations'].append({'kind': 'rotation', 'declared_deg': decimal_text(declared_rotation),
                                     'actual_deg': decimal_text(rotation)})
    insertion = declaration.get('insertion')
    local_axis = declaration.get('local_mating_axis')
    if insertion not in INSERTIONS:
        result['reasons'].append('insertion direction undeclared')
    elif insertion in ('from_top', 'from_bottom'):
        required = 'F' if insertion == 'from_top' else 'B'
        if fp['side'] != required:
            result['violations'].append({'kind': 'insertion_side', 'insertion': insertion, 'actual': fp['side']})
    elif local_axis is not None:
        faces = board_axis(local_axis, rotation, fp['side'])
        if faces is None:
            result['reasons'].append('facing undeterminable at a non-quarter-turn rotation')
        else:
            result['measured']['faces'] = faces
            if faces != edge['outward']:
                result['violations'].append({'kind': 'facing', 'faces': faces, 'required': edge['outward'],
                                             'edge': edge['name']})
    elif declared_rotation is None:
        result['reasons'].append('through-edge facing undeterminable without a declared rotation or local mating axis')
    result['status'] = verdict(result['violations'], result['reasons'])
    return result


def connector_edge(probe, declarations):
    if not declarations:
        raise ValueError('no declared connectors to evaluate')
    results = [connector_access(d, probe['footprints'], probe.get('outline')) for d in declarations]
    defects = [r for r in results if r['status'] == 'defect']
    unknown = [r for r in results if r['status'] == 'unknown']
    return {'criterion': 'connector-edge/1', 'status': verdict(defects, unknown),
            'defects': defects, 'unknown': unknown, 'connectors': results}


# ---------------------------------------------------------------- ratsnest

def _mst(terminals, weight):
    """Kruskal with exact integer weights; ties by sorted terminal index."""
    parent = list(range(len(terminals)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    candidates = sorted((weight(terminals[i][1], terminals[j][1]), i, j)
                        for i in range(len(terminals)) for j in range(i+1, len(terminals)))
    chosen = []
    for w, i, j in candidates:
        a, b = root(i), root(j)
        if a != b:
            parent[max(a, b)] = min(a, b)
            chosen.append((w, i, j))
            if len(chosen) == len(terminals) - 1:
                break
    return chosen


def ratsnest(probe):
    """Pad-centre MST per net: exact Manhattan nm and an integer-nm Euclidean interval.

    A placement proxy, not routed length; every netted pad is a terminal and
    no net is excluded by its name.
    """
    nets, unassigned = {}, []
    for fp in probe['footprints']:
        for pad in fp['pads']:
            identity = [fp['ref'], fp['uuid'], pad['number'], pad['uuid']]
            position = pad.get('position_nm')
            if (not isinstance(pad.get('net'), str) or not isinstance(position, list) or len(position) != 2
                    or any(type(v) is not int for v in position)):
                raise ValueError('pad record lacks net or integer position')
            if pad['net']:
                nets.setdefault(pad['net'], []).append((identity, tuple(position)))
            else:
                unassigned.append(identity)
    records = {}
    manhattan_total, euclid_low, euclid_high = 0, 0, 0
    for net in sorted(nets):
        terminals = sorted(nets[net])
        euclid = _mst(terminals, lambda p, q: (p[0]-q[0])**2 + (p[1]-q[1])**2)
        manhattan = _mst(terminals, lambda p, q: abs(p[0]-q[0]) + abs(p[1]-q[1]))
        low = sum(math.isqrt(w) for w, _, _ in euclid)
        high = sum(math.isqrt(w) + (math.isqrt(w)**2 != w) for w, _, _ in euclid)
        length = sum(w for w, _, _ in manhattan)
        records[net] = {'terminals': [t[0] for t in terminals],
                        'euclidean_edges': [[i, j] for _, i, j in euclid],
                        'manhattan_edges': [[i, j] for _, i, j in manhattan],
                        'euclidean_nm': [low, high], 'manhattan_nm': length}
        manhattan_total += length
        euclid_low += low
        euclid_high += high
    return {'schema': 'agent-ee.ratsnest-proxy/1', 'board_sha256': probe['board_sha256'],
            'method': 'pad-centre minimum spanning tree per net; not routed length or routability',
            'nets': records, 'unassigned_pads': sorted(unassigned),
            'totals': {'nets': len(records), 'terminals': sum(len(v['terminals']) for v in records.values()),
                       'manhattan_nm': manhattan_total, 'euclidean_nm': [euclid_low, euclid_high]}}


def ratsnest_bounds(proxy, maxima):
    """Declared maxima in nm; a Euclidean interval straddling a maximum is unknown."""
    totals = proxy['totals']
    defects, unknown, margins, metrics = [], [], {}, {}
    measured = {'manhattan': (totals['manhattan_nm'], totals['manhattan_nm']),
                'euclidean': tuple(totals['euclidean_nm'])}
    for metric, maximum in sorted(maxima.items()):
        low, high = measured[metric]
        name = f'ratsnest_{metric}_total'
        metrics[name] = {'value': mm(high), 'lower': mm(low), 'upper': mm(high), 'unit': 'mm',
                         'condition': 'pad-centre-mst-proxy'}
        margins[name] = {'max': mm(maximum - high)}
        record = {'metric': name, 'lower_mm': mm(low), 'upper_mm': mm(high), 'max_mm': mm(maximum)}
        if low > maximum:
            defects.append({'kind': 'ratsnest_above_maximum', **record})
        elif high > maximum:
            unknown.append({'kind': 'interval_straddles_maximum', **record})
    if not totals['terminals']:
        unknown.append({'kind': 'no_net_terminals', 'reason': 'no netted pads to measure'})
    return {'criterion': 'ratsnest-mst/1', 'status': verdict(defects, unknown), 'defects': defects,
            'unknown': unknown, 'metrics': metrics, 'margins': margins}


# ------------------------------------------------------ routability advisory

def route_fingerprint(digests):
    """kicad-route 08179cd cli.input_fingerprint over the attempt's input files."""
    h = hashlib.sha256()
    for name, digest in sorted(digests.items()):
        h.update(f'{name}\0{digest}\n'.encode())
    return h.hexdigest()


def routing_feedback(result, digests, board_sha256, footprints):
    """Informational footprint blockers; never a pass, defect or acceptance input."""
    out = {'status': 'unavailable', 'authority': 'advisory', 'blockers': [], 'unattributed': []}
    if not isinstance(result, dict) or result.get('kind') != 'route' or not isinstance(result.get('input'), dict):
        return {**out, 'reason': 'not a kicad-route result.json'}
    attempt_input = result['input']
    fingerprint, board = attempt_input.get('fingerprint'), attempt_input.get('board')
    if not isinstance(fingerprint, str) or not SHA256.fullmatch(fingerprint) or not isinstance(board, str):
        return {**out, 'reason': 'result.json lacks input fingerprint or board'}
    if route_fingerprint(digests) != fingerprint:
        return {**out, 'reason': 'captured reference files do not reproduce the attempt input fingerprint'}
    if digests.get(board) != board_sha256:
        return {**out, 'status': 'stale', 'reason': 'attempt input board differs from this candidate board'}
    routing = result.get('routing')
    if not isinstance(routing, dict):
        return {**out, 'reason': 'result.json lacks routing'}
    blocked = routing.get('blocked_by')
    if blocked is None:
        return {**out, 'status': 'applicable', 'reason': 'attempt reported no blocked_by summary'}
    if not isinstance(blocked, dict) or blocked.get('error') or not isinstance(blocked.get('top'), list):
        return {**out, 'reason': 'blocked_by summary unusable'}
    by_ref = {}
    for fp in footprints:
        by_ref.setdefault(fp['ref'], []).append(fp['uuid'])
    blockers, unattributed = [], []
    for entry in blocked['top']:
        if not isinstance(entry, dict) or entry.get('category') != 'footprint':
            continue
        match = ROUTE_FOOTPRINT.fullmatch(entry.get('id', '')) if isinstance(entry.get('id'), str) else None
        uuids = by_ref.get(match.group(1), []) if match else []
        record = {k: entry.get(k) for k in ('id', 'label', 'blocks', 'blocks_any_layer')}
        if len(uuids) == 1:
            blockers.append({**record, 'reference': match.group(1), 'uuid': uuids[0]})
        else:
            unattributed.append({**record, 'reason': 'no unique board footprint for the blocker reference'})
    return {**out, 'status': 'applicable', 'blockers': blockers, 'unattributed': unattributed,
            'method': blocked.get('method')}
