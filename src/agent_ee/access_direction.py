"""Footprint-local access lanes, qualified by declared evidence (AGENTEE-379).

The declaration travels with the native footprint, not with its packing policy.
All geometry is integer nm; board coordinates at zero native rotation on the
current mounting side define the local axes (no implicit back-side mirror).
"""
import json
import math
from fractions import Fraction

from . import placement_provenance
from .placement import (AXES, area2, courtyard_bounds, edges, intersection_area, on_segment,
                        overhang_regions, qualified_polygons)

DECLARATION = 'agent_ee_access_direction'
SCHEMA = 'agent-ee.access-direction/1'
# Source classes whose cited lane may also relax outline containment (AGENTEE-419).
# Every sourced class gates; board, arrangement_rule and fixture_author never relax.
RELAXING_CLASSES = ('manufacturer', 'datasheet')


def provenance(value):
    """A missing or blank citation is advisory even if a caller requested hard."""
    entry = value.get('provenance')
    if isinstance(entry, dict) and ('citation' not in entry or
            (isinstance(entry['citation'], str) and not entry['citation'].strip())):
        return placement_provenance.classify({}, 'provenance', 'access direction')
    return placement_provenance.classify(value, 'provenance', 'access direction')


def declaration(value):
    """Validate one bounded native property or captured equivalent; borrow input."""
    if isinstance(value, str):
        if len(value.encode('utf-8')) > 8192:
            raise ValueError('access declaration exceeds 8192 bytes')
        try:
            value = json.loads(value)
        except (ValueError, RecursionError) as exc:
            raise ValueError('invalid access declaration JSON') from exc
    if (not isinstance(value, dict)
            or set(value) - {'schema', 'mouth_nm', 'outward', 'lane_polygons_nm', 'travel_nm', 'provenance'}
            or value.get('schema') != SCHEMA):
        raise ValueError('unsupported access declaration')
    mouth = value.get('mouth_nm')
    if (not isinstance(mouth, list) or len(mouth) != 2 or any(type(v) is not int for v in mouth)
            or not isinstance(value.get('outward'), str) or value['outward'] not in AXES):
        raise ValueError('access needs integer mouth_nm and outward axis')
    polygons = qualified_polygons({'status': 'proven', 'polygons': value.get('lane_polygons_nm')})
    ux, uy = AXES[value['outward']]
    if any((x - mouth[0])*ux + (y - mouth[1])*uy < 0 for poly in polygons for x, y in poly):
        raise ValueError('access lane extends behind its mouth')
    if not any(on_segment(a, b, mouth) for poly in polygons for a, b in edges(poly)):
        raise ValueError('access mouth must touch its lane boundary')
    # The declared clearance travel binds the lane depth exactly, as value_nm binds a limit.
    if 'travel_nm' in value and (type(value['travel_nm']) is not int or value['travel_nm'] <= 0 or value['travel_nm']
            != max((x - mouth[0])*ux + (y - mouth[1])*uy for poly in polygons for x, y in poly)):
        raise ValueError('access travel_nm must be a positive integer equal to the lane travel')
    provenance(value)
    return {**value, 'lane_polygons_nm': polygons}


def geometry(fp):
    """Transform a declaration with the native pose; only quarter turns qualify."""
    value = declaration(fp['access_direction'])
    rotation = fp.get('rotation_deg')
    position = fp.get('position_nm')
    if (not isinstance(position, list) or len(position) != 2 or any(type(v) is not int for v in position)
            or fp.get('side') not in ('F', 'B')):
        raise ValueError('access needs a qualified mounting side and integer native position')
    if (type(rotation) not in (int, float) or (type(rotation) is float and not math.isfinite(rotation))
            or rotation % 90):
        raise ValueError('access direction needs a quarter-turn native pose')
    # Same KiCad rotation convention as placer.rotate, without importing search.
    def rotate(point):
        x, y = point
        for _ in range(int(rotation // 90) % 4):
            x, y = y, -x
        return [x, y]
    def move(point):
        x, y = rotate(point)
        return [x + fp['position_nm'][0], y + fp['position_nm'][1]]
    return (value, move(value['mouth_nm']), rotate(AXES[value['outward']]),
            [[move(p) for p in poly] for poly in value['lane_polygons_nm']])


def hard(fp):
    if 'access_direction' not in fp:
        return False
    value = declaration(fp['access_direction'])
    return provenance(value)['enforcement'] == 'hard'


def relaxes(fp):
    """Whether a hard lane may also hold courtyard area outside the outline.

    Gating needs any sourced class. Relaxing containment needs a manufacturer or
    datasheet source and a declared clearance travel bounding the lane depth."""
    return 'access_direction' in fp and _relaxing(declaration(fp['access_direction']))


def _relaxing(value):
    return provenance(value)['enforcement'] == 'hard' and (
        value['provenance']['class'] in RELAXING_CLASSES and 'travel_nm' in value)


def overhangs_courtyard(fp):
    """Whether a relaxing lane intersects courtyard area that can leave the board."""
    if not relaxes(fp):
        return False
    _, _, _, lane = geometry(fp)
    return any(intersection_area(p, q) > 0 for record in fp['courtyards'].values()
               for p in qualified_polygons(record) for q in lane)


def minimum_inside_area(fp, polygons):
    """Rigid-invariant lower bound: only the declared lane may lie outside."""
    area = sum(Fraction(abs(area2(p)), 2) for p in polygons)
    if not relaxes(fp):
        return area
    _, _, _, lane = geometry(fp)
    return area - sum(intersection_area(p, q) for p in polygons for q in lane)


def axes(fp, outline):
    """Additional origin coordinates aligning a sourced mouth and lane to an edge."""
    if not hard(fp):
        return set(), set()
    _, mouth, _, polygons = geometry(fp)
    points = [mouth] + [p for poly in polygons for p in poly]
    return ({v[0] - p[0] for v in outline for p in points},
            {v[1] - p[1] for v in outline for p in points})


def evaluate(fp, outline):
    """Return one pose's measurement, source and outside lane band; never mutate."""
    value = declaration(fp['access_direction'])
    source = provenance(value)
    try:
        _, mouth, vector, polygons = geometry(fp)
    except ValueError as exc:
        if source['enforcement'] == 'hard':
            raise
        return {'kind': 'access_direction', 'ref': fp['ref'], 'status': 'info', 'advisory': True,
                'provenance': source, 'reason': source['soft_reason'] + '; ' + str(exc)}
    inboard = sum(intersection_area(p, outline) for p in polygons)
    row = {'kind': 'access_direction', 'ref': fp['ref'], 'mouth_nm': mouth, 'vector': vector,
           'lane_polygons_nm': polygons, 'inboard_area_mm2': float(inboard / 10**12),
           'provenance': source, 'relaxes_outline': _relaxing(value), 'status': 'defect',
           'reason': 'access mouth cannot reach an outward outline edge with its full lane outside'}
    reachable_edge = False
    if inboard == 0:
        for index, (a, b) in enumerate(edges(outline)):
            if not on_segment(a, b, mouth) or (b[0]-a[0])*vector[0] + (b[1]-a[1])*vector[1]:
                continue
            reachable_edge = True
            distance = max((p[0]-mouth[0])*vector[0] + (p[1]-mouth[1])*vector[1]
                           for poly in polygons for p in poly)
            # Use the lane's transverse span of this proven edge. Sweeping an
            # entire concave edge could hit board material away from the lane.
            tangent = 1 if vector[0] else 0
            low = min(p[tangent] for poly in polygons for p in poly)
            high = max(p[tangent] for poly in polygons for p in poly)
            if low < min(a[tangent], b[tangent]) or high > max(a[tangent], b[tangent]):
                continue
            start, end = list(a), list(b)
            start[tangent], end[tangent] = (low, high) if a[tangent] < b[tangent] else (high, low)
            split_outline = []
            for j, (point, _) in enumerate(edges(outline)):
                for p in ([point, start, end] if j == index else [point]):
                    if not split_outline or list(p) != split_outline[-1]:
                        split_outline.append(list(p))
            if split_outline[-1] == split_outline[0]:
                split_outline.pop()
            edge = {'name': f'outline-{index}', 'start_nm': start, 'end_nm': end,
                    'outward': next(name for name, axis in AXES.items() if list(axis) == vector), 'max_nm': distance}
            try:
                bands = overhang_regions(fp['ref'], [edge], split_outline)
            except ValueError:
                continue  # Inward normals and unsupported outline sweeps are not admissible.
            if all(sum(intersection_area(p, r) for r in bands) == Fraction(abs(area2(p)), 2) for p in polygons):
                row.update(status='pass', reason='access lane lies outside a proven outline edge',
                           overhang={'reference': fp['ref'], 'edges': [edge], 'provenance': source,
                                     'regions_nm': [[list(point) for point in region] for region in bands]})
                break
    if inboard == 0 and reachable_edge and row['status'] != 'pass':
        row.update(status='unknown', reason='outside lane has no qualified exterior overhang band')
    if source['enforcement'] == 'soft':
        row.update(status='info', advisory=True, reason=source['soft_reason'] + '; ' + row['reason'])
    return row


def admits(fp, outline):
    """Uncited declarations never prune. Malformed declarations fail closed."""
    if 'access_direction' not in fp:
        return True
    return evaluate(fp, outline)['status'] in ('pass', 'info')


def grade(capture):
    rows, unknown = [], []
    declared = [fp for fp in capture['footprints'] if 'access_direction' in fp]
    if not declared:
        return rows, unknown
    try:
        outline, = qualified_polygons(capture.get('outline'))
    except ValueError as exc:
        return rows, [{'kind': 'access_direction', 'ref': fp['ref'], 'reason': str(exc)} for fp in declared]
    for fp in sorted(declared, key=lambda f: f['ref']):
        try:
            row = evaluate(fp, outline)
            # Outside lanes still reserve space against overhanging peer bodies.
            blocked, approximate = [], []
            for peer in capture['footprints']:
                if peer is fp or fp.get('side') not in peer.get('courtyards', {}):
                    continue
                try:
                    outer, inner = courtyard_bounds(peer['courtyards'][fp['side']])
                except ValueError:
                    # The polygon judge independently retains this peer's unknown
                    # geometry; do not erase a determinate access defect here.
                    continue
                lane = row.get('lane_polygons_nm', [])
                if any(intersection_area(p, q) > 0 for p in lane for q in inner):
                    blocked.append(peer['ref'])
                elif inner is not outer and any(intersection_area(p, q) > 0 for p in lane for q in outer):
                    approximate.append(peer['ref'])
            if blocked:
                row.update(blocked_by=sorted(blocked), reason='access lane obstructed by peer courtyard',
                           status='info' if row['provenance']['enforcement'] == 'soft' else 'defect')
            if approximate:
                row['approximate_peers'] = sorted(approximate)
                if row['status'] == 'pass':
                    row.update(status='unknown', reason='peer clearance lies only in a courtyard curve bound')
            rows.append(row)
            if row['status'] == 'unknown':
                unknown.append(row)
        except ValueError as exc:
            unknown.append({'kind': 'access_direction', 'ref': fp['ref'], 'reason': str(exc)})
    return rows, unknown
