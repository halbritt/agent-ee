"""Explicit block roles and final native-coordinate relationship evidence.

This module never moves a footprint, infers a role from connectivity, or grants
numeric authority. The caller supplies a reloaded native capture, not plan poses.
"""
from collections import Counter, defaultdict
from copy import deepcopy
from fractions import Fraction
import math
import json

from . import electrical_locality, locality_provenance
from .footprint_library import digest
from .placement import box, courtyard_bounds, edges, validate_capture
from .placement_predicates import point_segment2

SCHEMA = 'agent-ee.block-contract/1'
REPORT = 'agent-ee.block-contract-report/1'
ALLOCATION = ('agent-ee.block-allocation/2',
              'agent-ee.block-allocation/2 (proposed; /1 consumers ignore fixed/shared)')
ELIGIBILITY = ('fome-mc.side-eligibility/1',
               'fome-mc.side-eligibility/1 (proposed consumer: agent-ee, AGENTEE-479)')


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{label} must be nonempty text')
    return value


def _list(value, label):
    if not isinstance(value, list):
        raise ValueError(f'{label} must be an explicit list')
    return value


def membership(capture, allocation):
    """Require exactly one explicit role for every native footprint, board-only too.

    ``fixed`` is a logical role. Only separate writer/packing policy may constrain
    that reference's pose. Anchors describe block interfaces, not pose authority.
    """
    validate_capture(capture)
    if not isinstance(allocation, dict) or allocation.get('schema') not in ALLOCATION:
        raise ValueError('expected agent-ee.block-allocation/2')
    if allocation.get('board_sha256') != capture['board_sha256']:
        raise ValueError('allocation is not bound to this original board')
    refs = {fp['ref'] for fp in capture['footprints']}
    roles, normalized, ids = {}, [], set()

    def add(ref, role):
        _text(ref, 'reference')
        if ref not in refs:
            raise ValueError(f'allocation names unknown reference {ref}')
        if ref in roles:
            raise ValueError(f'allocation gives multiple roles to {ref}')
        roles[ref] = role

    for block in _list(allocation.get('blocks'), 'blocks'):
        if not isinstance(block, dict):
            raise ValueError('block must be an object')
        bid = _text(block.get('id'), 'block id')
        if bid in ids:
            raise ValueError(f'duplicate block {bid}')
        ids.add(bid)
        members = _list(block.get('references'), f'{bid} references')
        if not members:
            raise ValueError(f'block {bid} has no members')
        for ref in members:
            add(ref, {'role': 'block', 'block_id': bid})
        anchor = block.get('anchor')
        if not isinstance(anchor, dict) or len(anchor) != 1 or not set(anchor) <= {'member', 'fixed', 'none'}:
            raise ValueError(f'block {bid} needs an explicit member, fixed or none anchor')
        key, value = next(iter(anchor.items()))
        _text(value, f'{bid} anchor')
        if key == 'member' and value not in members:
            raise ValueError(f'block {bid} member anchor is not a member')
        if key == 'none' and value != 'centroid of members':
            raise ValueError(f'block {bid} none anchor must declare centroid of members')
        if 'interfaces_to_fixed' not in block:
            raise ValueError(f'block {bid} needs explicit interfaces_to_fixed')
        interface = block['interfaces_to_fixed']
        if interface is not None:
            _text(interface, f'{bid} fixed interface')
        normalized.append({'id': bid, 'references': sorted(members), 'anchor': deepcopy(anchor),
                           'interfaces_to_fixed': interface})
    for row in _list(allocation.get('fixed'), 'fixed'):
        if not isinstance(row, dict):
            raise ValueError('fixed role must be an object with reference')
        add(row.get('reference'), {'role': 'fixed'})
    for row in _list(allocation.get('shared'), 'shared'):
        if not isinstance(row, dict):
            raise ValueError('shared role must be an object with reference and role')
        add(row.get('reference'), {'role': 'shared', 'shared_role': _text(row.get('role'), 'shared role')})
    missing = sorted(refs - roles.keys())
    if missing:
        raise ValueError('unallocated native footprints: ' + ', '.join(missing))
    for block in normalized:
        for ref in (block['anchor'].get('fixed'), block['interfaces_to_fixed']):
            if ref is not None and roles.get(ref, {}).get('role') != 'fixed':
                raise ValueError(f"block {block['id']} names non-fixed interface {ref}")
    return {'schema': 'agent-ee.block-membership/1', 'board_sha256': capture['board_sha256'],
            'allocation_sha256': digest(allocation), 'blocks': sorted(normalized, key=lambda b: b['id']),
            'roles': dict(sorted(roles.items())),
            'anchors': {b['id']: b['anchor'] for b in sorted(normalized, key=lambda b: b['id'])}}


def _faces(value, label):
    if (not isinstance(value, list) or not value or any(v not in ('F', 'B') for v in value)
            or len(value) != len(set(value))):
        raise ValueError(f'{label} needs unique F/B eligible_faces')
    return [side for side in ('F', 'B') if side in value]


def _contract(original, contract):
    if (not isinstance(contract, dict) or contract.get('schema') != SCHEMA
            or set(contract) != {'schema', 'allocation', 'eligibility', 'relationships', 'templates', 'thickness_nm'}):
        raise ValueError('block contract needs schema, allocation, eligibility, relationships, templates and thickness_nm')
    result = membership(original, contract['allocation'])
    eligibility = contract['eligibility']
    if not isinstance(eligibility, dict) or eligibility.get('schema') not in ELIGIBILITY:
        raise ValueError('expected fome-mc.side-eligibility/1')
    if not isinstance(eligibility.get('sources'), dict) or eligibility['sources'].get('board_sha256') != original['board_sha256']:
        raise ValueError('side eligibility is not bound to this original board')
    if not isinstance(eligibility.get('default'), dict):
        raise ValueError('default eligibility must be an object')
    _faces(eligibility['default'].get('faces'), 'default eligibility')
    if not isinstance(eligibility.get('blocks'), dict):
        raise ValueError('eligibility blocks must be explicit')
    for key in ('members', 'fixed', 'shared'):
        if not isinstance(eligibility.get(key, {}), dict):
            raise ValueError(f'eligibility {key} must be an object')
    if set(eligibility['blocks']) != {b['id'] for b in result['blocks']}:
        raise ValueError('eligibility block ids differ from allocation')
    for ref in eligibility.get('members', {}):
        if ref not in result['roles']:
            raise ValueError(f'eligibility names unknown member {ref}')
    for ref, role in result['roles'].items():
        _eligible(eligibility, ref, role)
    if contract['thickness_nm'] is not None and (type(contract['thickness_nm']) is not int or contract['thickness_nm'] <= 0):
        raise ValueError('thickness_nm must be a positive integer or null (missing requirement)')
    document = contract['relationships']
    # An explicitly empty relationship set is meaningful for singleton blocks.
    if document == {'schema': electrical_locality.SCHEMA, 'obligations': []}:
        document = deepcopy(document)
    else:
        electrical_locality.validate(document)
    pairs = {(o['id'], role): o[role] for o in document['obligations'] for role in electrical_locality.roles(o)}
    templates = {}
    for row in _list(contract['templates'], 'templates'):
        if (not isinstance(row, dict) or set(row) != {'relationship', 'role', 'template'}
                or row.get('template') not in ('same-face-only', 'decoupler-under-device')):
            raise ValueError('template instance needs relationship, role and a supported template')
        key = (row['relationship'], row['role'])
        if key not in pairs or key in templates:
            raise ValueError('template instance is duplicate or names an unknown relationship/role')
        templates[key] = row['template']
    return result, document, templates


def validate(capture, section, effective_policy):
    """Bind strict declarations to the exact policy and allocation used by search."""
    sets = any(entry.get('candidate', {}).get('schema') == 'agent-ee.block-candidate-set/1'
               for entry in section.get('candidates', []))
    contract = section.get('contract')
    if contract is None:
        if sets:
            raise ValueError('candidate sets require block contract')
        return None
    members, _, _ = _contract(capture, contract)
    if digest(section.get('side_eligibility')) != digest(contract['eligibility']):
        raise ValueError('contract eligibility differs from section side_eligibility')
    empty = {'schema': electrical_locality.SCHEMA, 'obligations': []}
    if digest(effective_policy.get('electrical_locality', empty)) != digest(contract['relationships']):
        raise ValueError('contract relationships differ from effective policy electrical_locality')
    raw = section.get('allocation_text')
    if not isinstance(raw, str):
        raise ValueError('contract requires explicit allocation_text')
    allocation = json.loads(raw)
    if not isinstance(allocation, dict) or allocation.get('schema') != 'agent-ee.block-allocation/1':
        raise ValueError('expected allocation/1 bridge')
    bridge = {}
    expected = {b['id']: set(b['references']) for b in members['blocks']}
    for row in _list(allocation.get('blocks'), 'allocation bridge blocks'):
        if not isinstance(row, dict):
            raise ValueError('allocation bridge block must be an object')
        bid = _text(row.get('id'), 'allocation bridge id')
        if bid in bridge or bid not in expected:
            raise ValueError('allocation bridge has duplicate or unknown block')
        if row.get('sheets', []) != []:
            raise ValueError('contract allocation bridge requires explicit references, not sheet selectors')
        references = _list(row.get('references'), 'allocation bridge references')
        if any(not isinstance(ref, str) for ref in references) or len(set(references)) != len(references):
            raise ValueError('allocation bridge references must be unique text')
        bridge[bid] = set(references)
        anchor = members['anchors'][bid].get('member')
        if anchor is not None and row.get('anchor') != anchor:
            raise ValueError('allocation bridge member anchor differs')
    if bridge != expected:
        raise ValueError('allocation/1 bridge membership differs from strict allocation/2')
    return members


def _number(value):
    value = Fraction(value)
    return int(value) if value.denominator == 1 else {'numerator': value.numerator, 'denominator': value.denominator}


def _position(fp):
    position = fp.get('position_nm')
    if not isinstance(position, list) or len(position) != 2 or any(type(x) is not int for x in position):
        raise ValueError('native footprint position unavailable')
    return position


def _inside(point, polygon):
    """Closed polygon containment with exact ray crossings."""
    x, y = point
    inside = False
    for a, b in edges(polygon):
        cross = (b[0]-a[0])*(y-a[1]) - (b[1]-a[1])*(x-a[0])
        if cross == 0 and min(a[0], b[0]) <= x <= max(a[0], b[0]) and min(a[1], b[1]) <= y <= max(a[1], b[1]):
            return True
        if (a[1] > y) != (b[1] > y):
            crossing = a[0] + Fraction((y-a[1])*(b[0]-a[0]), b[1]-a[1])
            if x < crossing:
                inside = not inside
    return inside


def _distance_to_region2(point, polygons):
    if any(_inside(point, polygon) for polygon in polygons):
        return Fraction(0)
    return min(point_segment2(point, a, b) for polygon in polygons for a, b in edges(polygon))


def _eligible(record, ref, role):
    """Explicit member faces may differ from anchor faces within default authority."""
    base = None
    if role['role'] == 'block':
        from .block_variants import eligible_faces
        base = eligible_faces(record, role['block_id'])
    elif ref in record.get(role['role'], {}):
        entry = record[role['role']][ref]
        if not isinstance(entry, dict):
            raise ValueError(f'{ref}: role eligibility must be an object')
        base = _faces(entry.get('eligible_faces'), ref)
    member = record.get('members', {}).get(ref)
    if member is not None:
        if not isinstance(member, dict):
            raise ValueError(f'{ref}: member eligibility must be an object')
        narrowed = _faces(member.get('eligible_faces'), ref)
        default = _faces(record.get('default', {}).get('faces'), 'default eligibility')
        if not set(narrowed) <= set(default):
            raise ValueError(f'{ref}: member eligibility exceeds default permission')
        base = narrowed
    return base


def _internal_nets(fps, thickness):
    """3-D Manhattan HPWL of internal native copper centroids; not route length."""
    nets = defaultdict(list)
    for fp in fps:
        for pad in fp.get('pads', []):
            if not pad.get('net'):
                continue
            nets[pad['net']].append((fp, pad))
    measured, unknown = [], []
    for name, members in sorted(nets.items()):
        if len({fp['ref'] for fp, _ in members}) < 2:
            continue
        try:
            positions = []
            for fp, pad in members:
                endpoint = {'reference': fp['ref'], 'pad': pad['number'], 'net': name}
                if pad.get('uuid'):
                    endpoint['uuid'] = pad['uuid']
                positions.append(electrical_locality.resolve(fps, endpoint))
            sides = {fp['side'] for fp, _ in members}
            if not sides <= {'F', 'B'} or (len(sides) > 1 and thickness is None):
                raise ValueError('native side or board thickness unavailable')
            span = sum(max(p[axis] for p in positions)-min(p[axis] for p in positions) for axis in (0, 1))
            if len(sides) > 1:
                span += thickness
            measured.append({'net': name, 'references': sorted({fp['ref'] for fp, _ in members}),
                             'hpwl_3d_nm': _number(span), 'hpwl_3d_mm': float(span / 10**6)})
        except (ValueError, KeyError) as exc:
            unknown.append({'kind': 'internal_net_geometry_missing', 'net': name, 'reason': str(exc)})
    return {'metric': 'native copper-centroid 3D Manhattan half-perimeter; not routed length',
            'nets': measured, 'unknown': unknown}


def _cohesion_allowed(members, source, support):
    a, b = (members['roles'].get(ref, {}) for ref in (source, support))
    if a.get('role') == b.get('role') == 'block' and a['block_id'] == b['block_id']:
        return True
    if a.get('role') == 'fixed' and b.get('role') == 'block':
        block = next(bk for bk in members['blocks'] if bk['id'] == b['block_id'])
        return block['anchor'].get('fixed') == source or block['interfaces_to_fixed'] == source
    return False


def judge(saved_capture, contract, *, original_capture, plan=None):
    """Judge final native geometry; a defect survives unrelated missing evidence.

    Malformed declarations raise ValueError before admission. Changed saved
    inventory, forbidden sides and governed relationship violations are typed
    report defects. Missing geometry/authority is explicit, never acceptance.
    """
    contract = deepcopy(contract)
    members, document, templates = _contract(original_capture, contract)
    report = {'schema': REPORT, 'original_board_sha256': original_capture['board_sha256'],
              'board_sha256': saved_capture.get('board_sha256'), 'contract_sha256': digest(contract),
              'membership': members, 'eligibility_sha256': digest(contract['eligibility']),
              'plan_sha256': digest(plan) if plan is not None else None,
              'measurement_source': 'saved native capture', 'blocks': [], 'relationships': [],
              'roles': [], 'defects': [], 'unknown': [], 'advisories': []}
    defects, unknown, advisories = (report[k] for k in ('defects', 'unknown', 'advisories'))

    def finish():
        report['status'] = 'defect' if defects else 'unknown' if unknown else 'info' if advisories else 'pass'
        return report

    try:
        validate_capture(saved_capture)
    except ValueError as exc:
        defects.append({'kind': 'saved_inventory_invalid', 'reason': str(exc)})
        return finish()
    original = {fp['ref']: fp for fp in original_capture['footprints']}
    saved = {fp['ref']: fp for fp in saved_capture['footprints']}
    if set(saved) != set(original):
        defects.append({'kind': 'saved_membership_changed', 'missing': sorted(original.keys()-saved.keys()),
                        'extra': sorted(saved.keys()-original.keys())})
    for ref in sorted(saved.keys() & original.keys()):
        if saved[ref]['uuid'] != original[ref]['uuid']:
            defects.append({'kind': 'saved_identity_changed', 'reference': ref})
        def pads(fp):
            return Counter((p.get('uuid'), p.get('number'), p.get('net')) for p in fp.get('pads', []))
        if pads(saved[ref]) != pads(original[ref]):
            defects.append({'kind': 'saved_pad_identity_changed', 'reference': ref})
        faces = _eligible(contract['eligibility'], ref, members['roles'][ref])
        row = {'reference': ref, **members['roles'][ref], 'side': saved[ref].get('side'), 'eligible_faces': faces}
        report['roles'].append(row)
        if faces is None:
            unknown.append({'kind': 'side_eligibility_missing', 'reference': ref})
        elif saved[ref].get('side') not in faces:
            defects.append({'kind': 'forbidden_side', **row})
    eligibility = contract['eligibility']
    for bid, entry in sorted(eligibility['blocks'].items()):
        if not isinstance(entry, dict):
            raise ValueError('block eligibility must be an object')
        from .block_variants import eligible_faces
        anchor = members['anchors'][bid]
        anchor_ref = anchor.get('member', anchor.get('fixed'))
        if anchor_ref in saved and saved[anchor_ref].get('side') not in eligible_faces(eligibility, bid):
            defects.append({'kind': 'forbidden_anchor_side', 'block': bid, 'reference': anchor_ref,
                            'side': saved[anchor_ref].get('side')})
        for declaration in entry.get('material_unknowns', []):
            unknown.append({'kind': 'eligibility_material_unknown', 'block': bid, 'declaration': deepcopy(declaration)})
        for key in ('region_restrictions', 'same_face_relationships'):
            if entry.get(key):
                unknown.append({'kind': 'eligibility_requirement_unresolved', 'block': bid,
                                'requirement': key, 'declaration': deepcopy(entry[key])})
    for label, entry in sorted(eligibility.get('shared', {}).items()):
        if label not in members['roles'] or entry.get('constraint'):
            unknown.append({'kind': 'shared_eligibility_requirement_unresolved', 'label': label,
                            'declaration': deepcopy(entry)})
    thickness = contract['thickness_nm']
    if thickness is None:
        unknown.append({'kind': 'board_thickness_missing'})
    for block in members['blocks']:
        fps = [saved[ref] for ref in block['references'] if ref in saved]
        row = {**block, 'members': [{'reference': fp['ref'], 'side': fp.get('side'),
                                   'position_nm': fp.get('position_nm')} for fp in fps]}
        try:
            positions = [_position(fp) for fp in fps]
            if len(positions) != len(block['references']):
                raise ValueError('block member missing')
            row['anchor_bbox_nm'] = [min(p[0] for p in positions), min(p[1] for p in positions),
                                     max(p[0] for p in positions), max(p[1] for p in positions)]
            row['anchor_spread_squared_nm2'] = max((sum((x-y)**2 for x, y in zip(a, b))
                                                    for a in positions for b in positions), default=0)
            polygons = [p for fp in fps for p in courtyard_bounds(fp.get('courtyards', {}).get(fp.get('side')))[0]]
            row['courtyard_bbox_nm'] = list(box(polygons))
        except ValueError as exc:
            unknown.append({'kind': 'block_geometry_missing', 'block': block['id'], 'reason': str(exc)})
        row['internal_connections'] = _internal_nets(fps, thickness)
        unknown.extend({'block': block['id'], **u} for u in row['internal_connections']['unknown'])
        report['blocks'].append(row)
    for obligation in document['obligations']:
        for role in electrical_locality.roles(obligation):
            pair = obligation[role]
            key = (obligation['id'], role)
            authority = locality_provenance.classify(pair, document.get('source_records'))
            row = {'relationship': key[0], 'role': role, 'from': deepcopy(pair['from']), 'to': deepcopy(pair['to']),
                   'constraint_provenance': authority, 'max_distance_nm': pair.get('max_distance_nm'),
                   'template': templates.get(key), 'classification': 'unknown'}
            report['relationships'].append(row)
            try:
                if obligation['metric'] != 'pad-centre-to-centre':
                    raise ValueError('unsupported locality metric')
                if pair['from']['net'] != pair['to']['net']:
                    raise ValueError('locality endpoints must declare the same net')
                a, b = (electrical_locality.resolve(saved_capture['footprints'], pair[k]) for k in ('from', 'to'))
                device, support = (saved[pair[k]['reference']] for k in ('from', 'to'))
                if device.get('side') not in ('F', 'B') or support.get('side') not in ('F', 'B'):
                    raise ValueError('native mounting side unavailable')
                cross_face = device['side'] != support['side']
                if cross_face and thickness is None:
                    raise ValueError('board thickness required for cross-face distance')
                dz = thickness if cross_face else 0
                squared = sum((x-y)**2 for x, y in zip(a, b)) + dz**2
                row.update(distance_squared_nm2=_number(squared), distance_mm=math.sqrt(squared)/10**6,
                           dz_nm=dz, geometry='native copper-land centroid; mounting-face separation uses declared board thickness')
                bound = pair.get('max_distance_nm')
                if bound is None:
                    raise ValueError('numeric relationship bound missing')
                if authority['enforcement'] == 'hard' and 'citation' not in pair:
                    raise ValueError('declared numeric limit and citation required')
                row['exceeded'] = squared > bound**2
                if authority['enforcement'] == 'integrity':
                    raise ValueError(authority['reason'])
                if authority['enforcement'] == 'hard':
                    if row['exceeded']:
                        defects.append({'kind': 'pin_distance_exceeded', **row})
                else:
                    advisories.append({'kind': 'ungoverned_numeric_bound', **row})
                    unknown.append({'kind': 'required_numeric_authority_missing', 'relationship': key[0], 'role': role,
                                    'reason': authority['reason']})
                if not cross_face:
                    row['classification'] = 'same_face'
                    continue
                if not _cohesion_allowed(members, device['ref'], support['ref']):
                    row['classification'] = 'lost_cohesion'
                    defects.append({'kind': 'cross_face_membership', **row})
                    continue
                if templates.get(key) != 'decoupler-under-device':
                    row['classification'] = 'lost_cohesion'
                    defects.append({'kind': 'cross_face_template_missing' if key not in templates else 'same_face_required', **row})
                    continue
                if row['exceeded'] or bound**2 < dz**2:
                    row['classification'] = 'lost_cohesion'
                    continue
                if authority['enforcement'] != 'hard':
                    continue
                allowance2 = bound**2 - dz**2
                outer, inner = courtyard_bounds(device.get('courtyards', {}).get(device['side']))
                lower = _distance_to_region2(b, outer)
                upper = _distance_to_region2(b, inner)
                row['template_xy_allowance_squared_nm2'] = allowance2
                row['support_to_device_courtyard_squared_nm2_bounds'] = [_number(lower), _number(upper)]
                if lower > allowance2:
                    row['classification'] = 'lost_cohesion'
                    defects.append({'kind': 'cross_face_template_outside', **row})
                elif upper <= allowance2:
                    row['classification'] = 'deliberate_cross_face'
                else:
                    raise ValueError('cross-face template lies within courtyard approximation uncertainty')
            except (ValueError, KeyError) as exc:
                unknown.append({'kind': 'relationship_requirement_missing', 'relationship': key[0],
                                'role': role, 'reason': str(exc)})
    # An omitted pin declaration cannot turn a multi-member block into a pass.
    covered = {p[k]['reference'] for o in document['obligations']
               for role in electrical_locality.roles(o) for p in (o[role],) for k in ('from', 'to')}
    for block in members['blocks']:
        if len(block['references']) > 1:
            electrical = {ref for ref in block['references'] if ref in saved
                          and any(p.get('net') for p in saved[ref].get('pads', []))}
            missing = sorted(electrical - covered)
            if missing:
                unknown.append({'kind': 'block_pin_relationships_missing', 'block': block['id'], 'references': missing})
    return finish()


def check_complete(capture, contract):
    """Minimal pure planning check, shared byte-for-value with the Rust protocol.

    Admission must call validate first. Saved inventory identity and richer spread
    and internal-net evidence are independently checked by judge after native I/O.
    """
    original = deepcopy(capture)
    original['board_sha256'] = contract['allocation']['board_sha256']
    report = judge(capture, contract, original_capture=original)
    rows = [{key: row[key] for key in ('relationship', 'role', 'classification', 'constraint_provenance',
             'template', 'distance_squared_nm2', 'dz_nm', 'exceeded') if key in row}
            for row in report['relationships']]
    defects = []
    for row in report['defects']:
        kind = row['kind']
        keys = ('kind', 'relationship', 'role')
        if kind == 'pin_distance_exceeded': keys += ('distance_squared_nm2',)
        elif kind == 'forbidden_side': keys = ('kind', 'reference', 'side')
        elif kind == 'forbidden_anchor_side': keys = ('kind', 'block', 'reference')
        if kind in ('pin_distance_exceeded', 'forbidden_side', 'forbidden_anchor_side',
                    'cross_face_membership', 'cross_face_template_missing', 'same_face_required',
                    'cross_face_template_outside'):
            defects.append({key: row[key] for key in keys})
    unknown = []
    if contract['thickness_nm'] is None:
        unknown.append({'kind': 'board_thickness_missing'})
    unknown.extend(u for u in report['unknown'] if u['kind'] == 'side_eligibility_missing')
    for bid, entry in sorted(contract['eligibility']['blocks'].items()):
        for key in ('material_unknowns', 'region_restrictions', 'same_face_relationships'):
            if key in entry and entry[key] != []:
                unknown.append({'kind': 'eligibility_requirement_unresolved', 'block': bid,
                                'requirement': key, 'declaration': deepcopy(entry[key])})
    unknown.extend(u for u in report['unknown'] if u['kind'] == 'shared_eligibility_requirement_unresolved')
    unknown.extend(u for u in report['unknown'] if u['kind'] in (
        'required_numeric_authority_missing', 'relationship_requirement_missing', 'block_pin_relationships_missing'))
    return {'schema': 'agent-ee.block-contract-check/1',
            'status': 'defect' if defects else 'unknown' if unknown else 'pass',
            'contract_sha256': digest(contract), 'board_sha256': capture['board_sha256'],
            'defects': defects, 'unknown': unknown, 'relationships': rows}
