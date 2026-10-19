"""Declared placement groups: target regions and anchor cohesion (AGENTEE-212).

A group is captured input. It names references and at least one constraint:
``region``, straight polygons in integer nanometres that must contain every
courtyard of each member, and/or ``anchor`` (a reference or an exact point) with
``max_distance_nm``, the largest footprint-origin Euclidean distance of each
member from it. Membership alone constrains nothing, so a group without a
constraint is refused rather than accepted as electrical intent. The packer
enforces groups during its search and the ``placement-groups/1`` criterion grades
them on a saved board with exact rationals.
"""
from fractions import Fraction
import math

from .placement import area2, boxes_meet, courtyard_bounds, intersection_area, qualified_polygons

SCHEMA = 'agent-ee.placement-groups/1'
CRITERION = 'placement-groups/1'
KEYS = {'id', 'references', 'region', 'anchor', 'max_distance_nm'}


def _text(value, label):
    if not isinstance(value, str) or not value:
        raise ValueError(f'{label} must be nonempty text')
    return value


def validate(groups, refs=None):
    """Normalized groups; raises ValueError. ``refs`` is the known reference set, if any."""
    if not isinstance(groups, list) or not groups:
        raise ValueError('placement groups must be a nonempty list')
    out, ids, members = [], set(), set()
    for group in groups:
        if not isinstance(group, dict) or not {'id', 'references'} <= set(group) or set(group) - KEYS:
            raise ValueError(f'placement group needs id and references, optionally {sorted(KEYS - {"id", "references"})}')
        gid = _text(group['id'], 'group id')
        if gid in ids:
            raise ValueError(f'duplicate placement group {gid}')
        ids.add(gid)
        references = group['references']
        if (not isinstance(references, list) or not references or len(set(map(str, references))) != len(references)
                or any(not isinstance(r, str) or not r for r in references)):
            raise ValueError(f'group {gid}: references must be unique nonempty text')
        if members & set(references):
            raise ValueError(f'group {gid}: a reference belongs to at most one group')
        members |= set(references)
        if refs is not None and not set(references) <= refs:
            raise ValueError(f'group {gid}: unknown reference {sorted(set(references) - refs)}')
        if 'region' not in group and 'anchor' not in group:
            raise ValueError(f'group {gid}: needs a region or an anchor; membership alone constrains nothing')
        normalized = {'id': gid, 'references': sorted(references)}
        if 'region' in group:
            region = group['region']
            if not isinstance(region, dict) or set(region) != {'polygons'}:
                raise ValueError(f'group {gid}: region must be exactly {{"polygons": [...]}}')
            try:
                polygons = qualified_polygons({'status': 'proven', 'polygons': region['polygons']})
            except ValueError as exc:
                raise ValueError(f'group {gid}: region {exc}') from None
            normalized['region'] = {'polygons': [[list(p) for p in poly] for poly in polygons]}
        if ('anchor' in group) != ('max_distance_nm' in group):
            raise ValueError(f'group {gid}: anchor and max_distance_nm are declared together')
        if 'anchor' in group:
            anchor, distance = group['anchor'], group['max_distance_nm']
            if not isinstance(anchor, dict) or len(anchor) != 1 or not set(anchor) <= {'reference', 'point_nm'}:
                raise ValueError(f'group {gid}: anchor is exactly one of reference or point_nm')
            if 'reference' in anchor:
                ref = _text(anchor['reference'], f'group {gid} anchor reference')
                if ref in references or (refs is not None and ref not in refs):
                    raise ValueError(f'group {gid}: anchor {ref} must be a known non-member reference')
            elif (not isinstance(anchor['point_nm'], list) or len(anchor['point_nm']) != 2 or
                  any(type(v) is not int for v in anchor['point_nm'])):
                raise ValueError(f'group {gid}: anchor point_nm must be integer nanometres')
            if type(distance) is not int or distance <= 0:
                raise ValueError(f'group {gid}: max_distance_nm must be a positive integer')
            normalized.update(anchor=dict(anchor), max_distance_nm=distance)
        out.append(normalized)
    anchored = {r for g in out if 'anchor' in g for r in g['references']}
    for g in out:
        if g.get('anchor', {}).get('reference') in anchored:
            raise ValueError(f"group {g['id']}: anchor {g['anchor']['reference']} is itself an anchored member")
    return out


def load(document):
    """Groups from a captured agent-ee.placement-groups/1 or packing-policy/1 document."""
    if isinstance(document, dict) and document.get('schema') == SCHEMA and set(document) == {'schema', 'groups'}:
        return validate(document['groups'])
    if isinstance(document, dict) and document.get('schema') == 'agent-ee.packing-policy/1' and 'groups' in document:
        return validate(document['groups'])
    raise ValueError(f'expected {SCHEMA} with exactly schema and groups, or a packing policy with groups')


def membership(groups):
    return {ref: group for group in groups for ref in group['references']}


def outside_region(polygons, region):
    """Exact courtyard area outside a group's region polygons (disjoint by validation)."""
    return sum(Fraction(abs(area2(c)), 2) - sum(intersection_area(c, r) for r in region
                                                 if boxes_meet([c], [r])) for c in polygons)


def anchor_point(group, positions):
    anchor = group['anchor']
    return tuple(anchor['point_nm']) if 'point_nm' in anchor else positions.get(anchor['reference'])


def distance2(a, b):
    return (a[0]-b[0])**2 + (a[1]-b[1])**2


def admits(fp, group, positions):
    """Search predicate over a candidate's outer courtyard bound; never weaker than grade()."""
    if 'region' in group:
        region = [[tuple(p) for p in poly] for poly in group['region']['polygons']]
        if any(outside_region(c['polygons'], region) for c in fp['courtyards'].values()):
            return False
    if 'anchor' in group:
        point = anchor_point(group, positions)
        if point is None:
            raise ValueError(f"group {group['id']}: anchor {group['anchor']['reference']} is not placed yet")
        if distance2(fp['position_nm'], point) > group['max_distance_nm'] ** 2:
            return False
    return True


def grade(probe, groups):
    """placement-groups/1 over a saved-board probe: pass, defect or unknown, never inferred."""
    by_ref = {}
    for fp in probe['footprints']:
        by_ref.setdefault(fp['ref'], []).append(fp)
    defects, unknown, measured = [], [], []
    def unique(ref, gid, kind):
        found = by_ref.get(ref, [])
        if len(found) != 1:
            unknown.append({'kind': kind, 'group': gid, 'ref': ref,
                            'reason': 'reference missing' if not found else 'reference duplicated'})
            return None
        return found[0]
    for group in groups:
        gid = group['id']
        point = None
        if 'anchor' in group:
            if 'point_nm' in group['anchor']:
                point = tuple(group['anchor']['point_nm'])
            else:
                anchor = unique(group['anchor']['reference'], gid, 'anchor_missing')
                point = None if anchor is None else tuple(anchor['position_nm'])
        for ref in group['references']:
            fp = unique(ref, gid, 'member_missing')
            if fp is None:
                continue
            if 'region' in group:
                region = [[tuple(p) for p in poly] for poly in group['region']['polygons']]
                courtyards = fp.get('courtyards') if isinstance(fp.get('courtyards'), dict) else {}
                if fp.get('side') not in courtyards:
                    unknown.append({'kind': 'courtyard', 'group': gid, 'ref': ref, 'reason': 'courtyard missing on mounting side'})
                for side, record in sorted(courtyards.items()):
                    try:
                        outer, inner = courtyard_bounds(record)
                    except ValueError as exc:
                        unknown.append({'kind': 'courtyard', 'group': gid, 'ref': ref, 'side': side, 'reason': str(exc)})
                        continue
                    outside = outside_region(inner, region)
                    if outside > 0:
                        defects.append({'kind': 'outside_group_region', 'group': gid, 'ref': ref, 'side': side,
                                        'outside_area_mm2': float(outside / 10**12)})
                    elif inner is not outer and outside_region(outer, region) > 0:
                        unknown.append({'kind': 'courtyard_approximation', 'group': gid, 'ref': ref, 'side': side,
                                        'reason': 'region boundary lies between the declared curve bounds'})
            if 'anchor' in group and point is not None:
                d2 = distance2(fp['position_nm'], point)
                # The decision is exact on squared integers; the millimetre distance is a report.
                record = {'group': gid, 'ref': ref, 'distance_mm': round(math.sqrt(d2) / 10**6, 6),
                          'max_distance_mm': group['max_distance_nm'] / 10**6}
                measured.append(record)
                if d2 > group['max_distance_nm'] ** 2:
                    defects.append({'kind': 'group_anchor_distance', **record})
    status = 'defect' if defects else 'unknown' if unknown else 'pass'
    return {'criterion': CRITERION, 'status': status, 'defects': defects, 'unknown': unknown,
            'measured': measured, 'groups': groups}
