"""Source-bound conservative mechanical projections, not a general 3-D solver.

The accepted-envelope boundary authenticates the captured document and cited
source bytes. This module binds native identities and evaluates geometry; a
caller-provided hash is never an authority decision. Engineering declarations
remain engineering declarations, including estimates and their uncertainty.

Attached polygons use coordinates relative to the native owner (or pad) origin,
with its source angle removed, on its captured side. On a side change, reflect
local Y, then apply the current native angle and origin. KiCad's LEFT_RIGHT flip
changes the native angle to 180-old; this local reflection therefore reproduces
the native board-space X reflection. No native pad geometry is synthesized.

Every declared height interval is projected onto its declared faces. We never
use missing component heights to grant clearance above/below a reservation.
All phases are checked by default. These conservative projections can reject a
physically feasible arrangement; they cannot establish complete 3-D acceptance.
"""
import copy
import hashlib
import json
import math
from fractions import Fraction
from pathlib import PurePosixPath

from .placement import courtyard_bounds, intersection_area, simple_polygon, validate_capture

SCHEMA = 'agent-ee.mechanical-reservations/1'
BINDING_SCHEMA = 'agent-ee.mechanical-reservation-binding/1'
SELECTION_SCHEMA = 'agent-ee.mechanical-block-selection/1'
PROJECTION = 'conservative-per-face'


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _fields(value, required, optional=(), label='mechanical reservation'):
    if not isinstance(value, dict) or set(value) - set(required) - set(optional) or set(required) - set(value):
        raise ValueError(f'{label}: invalid fields')


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{label}: nonempty string required')
    return value


def _sha(value, label):
    if not isinstance(value, str) or len(value) != 64 or set(value) - set('0123456789abcdef'):
        raise ValueError(f'{label}: SHA-256 required')
    return value


def _strings(value, label, choices=None, *, empty=False):
    if not isinstance(value, list) or (not value and not empty):
        raise ValueError(f'{label}: list required')
    for item in value:
        _text(item, label)
        if choices is not None and item not in choices:
            raise ValueError(f'{label}: unsupported value {item!r}')
    if len(set(value)) != len(value):
        raise ValueError(f'{label}: duplicate value')


def _point(value):
    if not isinstance(value, (tuple, list)) or len(value) != 2 or any(type(v) is not int for v in value):
        raise ValueError('mechanical origin must be integer nanometres')
    return list(value)


def _angle(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value % 90:
        raise ValueError('mechanical frame requires an exact native quarter-turn angle')
    return int(value) % 360


def _offset(pad, key):
    value = pad[key]
    if key == 'copper_land_offset_nm':
        return _point(value)
    if (not isinstance(value, list) or len(value) != 2 or any(
            not isinstance(v, list) or len(v) != 2 or any(type(n) is not int for n in v)
            or v[1] <= 0 for v in value)):
        raise ValueError('mechanical copper centroid offset must be exact rational nanometres')
    return [Fraction(*v) for v in value]


def validate(document):
    """Validate a closed declaration without asserting that its source is trusted."""
    _fields(document, ('schema', 'original_board_path', 'original_board_sha256', 'source', 'reservations'), label='mechanical document')
    if document['schema'] != SCHEMA:
        raise ValueError('unsupported mechanical reservation schema')
    _sha(document['original_board_sha256'], 'mechanical source board')
    board_path = PurePosixPath(_text(document['original_board_path'], 'mechanical source board path'))
    if (board_path.is_absolute() or '..' in board_path.parts
            or str(board_path) != document['original_board_path'] or str(board_path) == '.'):
        raise ValueError('mechanical original board path must be a captured relative path')
    source = document['source']
    _fields(source, ('path', 'sha256', 'locator', 'classification', 'author'), label='mechanical source')
    path = PurePosixPath(_text(source['path'], 'mechanical source path'))
    if path.is_absolute() or '..' in path.parts or str(path) != source['path'] or str(path) == '.':
        raise ValueError('mechanical source path must be a captured relative path')
    _sha(source['sha256'], 'mechanical source')
    for key in ('locator', 'author'):
        _text(source[key], 'mechanical source ' + key)
    if source['classification'] != 'engineering':
        raise ValueError('mechanical choices must retain engineering classification')
    rows = document['reservations']
    if not isinstance(rows, list) or len(rows) > 1024:
        raise ValueError('mechanical reservations must be a bounded list')
    seen = set()
    for row in rows:
        _fields(row, ('id', 'required', 'kind', 'frame', 'geometry', 'faces', 'height_nm',
                      'phases', 'self_exclusion', 'coverage', 'uncertainty'), ('owner',))
        rid = _text(row['id'], 'mechanical reservation id')
        if rid in seen:
            raise ValueError('duplicate mechanical reservation id')
        seen.add(rid)
        if type(row['required']) is not bool or type(row['self_exclusion']) is not bool:
            raise ValueError('mechanical required/self_exclusion must be explicit booleans')
        if row['kind'] not in ('solid', 'access') or row['frame'] not in ('board', 'footprint', 'pad'):
            raise ValueError('unsupported mechanical kind or frame')
        if row['frame'] == 'board':
            if 'owner' in row or row['self_exclusion']:
                raise ValueError('fixed board reservations have no owner or self exclusion')
            _strings(row['faces'], 'mechanical faces', ('F', 'B'))
        else:
            owner = row.get('owner')
            _fields(owner, ('ref', 'uuid') + (('pad',) if row['frame'] == 'pad' else ()), label='mechanical owner')
            _text(owner['ref'], 'mechanical owner ref')
            _text(owner['uuid'], 'mechanical owner UUID')
            if row['frame'] == 'pad':
                _fields(owner['pad'], ('number', 'uuid'), label='mechanical pad owner')
                _text(owner['pad']['number'], 'mechanical pad number')
                _text(owner['pad']['uuid'], 'mechanical pad UUID')
            _strings(row['faces'], 'mechanical attached faces', ('same', 'opposite'))
        _fields(row['geometry'], ('polygons_nm',), label='mechanical geometry')
        polygons = row['geometry']['polygons_nm']
        if polygons is not None:
            if not isinstance(polygons, list) or not polygons or len(polygons) > 64:
                raise ValueError('mechanical geometry needs 1..64 polygons or explicit null')
            for polygon in polygons:
                simple_polygon(polygon)
        height = row['height_nm']
        if height is not None and (not isinstance(height, list) or len(height) != 2
                                   or any(type(v) is not int for v in height) or not 0 <= height[0] < height[1]):
            raise ValueError('mechanical height needs a positive explicit interval or null')
        _strings(row['phases'], 'mechanical phases')
        _strings(row['uncertainty'], 'mechanical uncertainty', empty=True)
        coverage = row['coverage']
        _fields(coverage, ('projection', 'height', 'access', 'basis'), label='mechanical coverage')
        if coverage['projection'] != PROJECTION or any(coverage[k] not in ('covered', 'unknown')
                                                      for k in ('height', 'access')):
            raise ValueError('unsupported mechanical projection coverage')
        _text(coverage['basis'], 'mechanical coverage basis')
    return copy.deepcopy(document)


def _native_owner(fp):
    """Keep immutable identity and source poses, not courtyard/cache state."""
    for key in ('ref', 'uuid'):
        _text(fp.get(key), 'mechanical footprint ' + key)
    if fp.get('side') not in ('F', 'B'):
        raise ValueError('mechanical footprint side is unknown')
    _point(fp.get('position_nm'))
    _angle(fp.get('rotation_deg'))
    pads = fp.get('pads')
    if not isinstance(pads, list):
        raise ValueError('mechanical footprint pad inventory missing')
    seen = set()
    for pad in pads:
        if pad.get('shape') == 6 or 'custom_geometry' in pad:
            raise ValueError('mechanical attachments do not support custom-pad geometry')
        _text(pad.get('uuid'), 'mechanical pad uuid')
        if not isinstance(pad.get('number'), str) or not isinstance(pad.get('net'), str) or pad['uuid'] in seen:
            raise ValueError('mechanical pad identity missing or ambiguous')
        seen.add(pad['uuid'])
        _point(pad.get('position_nm'))
        _angle(pad.get('rotation_deg'))
        for key in ('copper_centroid_offset_nm', 'copper_land_offset_nm'):
            if key in pad:
                _offset(pad, key)
    keys = ('ref', 'uuid', 'side', 'position_nm', 'rotation_deg', 'pads', 'fpid', 'value')
    return copy.deepcopy({key: fp[key] for key in keys if key in fp})


def bind(document, capture, *, source_sha256=None):
    """Bind against the original native capture after envelope source verification.

    source_sha256 is the captured *raw document* digest. Its authentication is
    the caller's envelope-boundary duty; canonical_document_sha256 separately
    detects accidental changes to the parsed document carried into planning.
    """
    document = validate(document)
    validate_capture(capture)
    if capture['board_sha256'] != document['original_board_sha256']:
        raise ValueError('mechanical original board hash differs from native capture')
    if source_sha256 is not None:
        _sha(source_sha256, 'mechanical captured document')
    by_ref = {fp['ref']: fp for fp in capture['footprints']}
    owners = {}
    for row in document['reservations']:
        owner = row.get('owner')
        if owner is None:
            continue
        fp = by_ref.get(owner['ref'])
        if fp is None or fp['uuid'] != owner['uuid']:
            raise ValueError('mechanical owner ref/UUID differs from native capture')
        native = _native_owner(fp)
        if row['frame'] == 'pad' and len([p for p in native['pads']
                if p['uuid'] == owner['pad']['uuid'] and p['number'] == owner['pad']['number']]) != 1:
            raise ValueError('mechanical pad number/UUID differs from native capture')
        owners[fp['uuid']] = native
    canonical = _digest(document)
    return {'schema': BINDING_SCHEMA, 'document': document,
            'document_sha256': source_sha256 or canonical, 'canonical_document_sha256': canonical,
            'source_board_sha256': capture['board_sha256'],
            'owners': [owners[key] for key in sorted(owners)]}


def _binding(binding):
    _fields(binding, ('schema', 'document', 'document_sha256', 'canonical_document_sha256',
                      'source_board_sha256', 'owners'), ('selection',), label='mechanical binding')
    if binding['schema'] != BINDING_SCHEMA:
        raise ValueError('unsupported mechanical binding schema')
    document = validate(binding['document'])
    _sha(binding['document_sha256'], 'mechanical bound document')
    if (_digest(document) != binding['canonical_document_sha256']
            or binding['source_board_sha256'] != document['original_board_sha256']):
        raise ValueError('mechanical binding document identity changed')
    if not isinstance(binding['owners'], list):
        raise ValueError('mechanical bound owners missing')
    owners = {}
    for fp in binding['owners']:
        native = _native_owner(fp)
        if native['uuid'] in owners:
            raise ValueError('duplicate mechanical bound owner')
        owners[native['uuid']] = native
    expected = {row['owner']['uuid'] for row in document['reservations'] if 'owner' in row}
    if set(owners) != expected:
        raise ValueError('mechanical bound owner coverage differs')
    if 'selection' in binding:
        selection = binding['selection']
        _fields(selection, ('schema', 'references', 'reservation_ids'), label='mechanical block selection')
        refs = selection['references']
        _strings(refs, 'mechanical block references', empty=True)
        expected_ids = sorted(row['id'] for row in document['reservations']
                              if row.get('owner', {}).get('ref') in refs)
        if (selection['schema'] != SELECTION_SCHEMA or selection['references'] != sorted(refs)
                or selection['reservation_ids'] != expected_ids):
            raise ValueError('mechanical block selection must include every attached member reservation')
    return document, owners


def for_block(binding, references):
    """Select attached member rows without rewriting the authenticated document.

    Fixed board rows and external owners remain global constraints. The full
    source-native inventory remains bound, including after a native face change.
    """
    document, _ = _binding(binding)
    refs = sorted(set(references))
    _strings(refs, 'mechanical block references', empty=True)
    if 'selection' in binding and not set(refs) <= set(binding['selection']['references']):
        raise ValueError('mechanical block selection cannot widen a prior selection')
    result = copy.deepcopy(binding)
    result['selection'] = {'schema': SELECTION_SCHEMA, 'references': refs,
                           'reservation_ids': sorted(row['id'] for row in document['reservations']
                                                     if row.get('owner', {}).get('ref') in refs)}
    return result


def _selected_rows(binding, document):
    selected = binding.get('selection')
    if selected is None:
        return document['reservations']
    ids = set(selected['reservation_ids'])
    return [row for row in document['reservations'] if row['id'] in ids]


def _move(point, origin, angle, flipped):
    from .placer import rotate
    x, y = point
    x, y = rotate([x, -y if flipped else y], _angle(angle) // 90)
    return [x + origin[0], y + origin[1]]


def _current_owner(source, current):
    from .placer import rotate
    current = _native_owner(current)
    if any(source.get(k) != current.get(k) for k in ('ref', 'uuid', 'fpid', 'value')):
        raise ValueError('mechanical native owner identity changed')
    old = {p['uuid']: p for p in source['pads']}
    new = {p['uuid']: p for p in current['pads']}
    if set(old) != set(new):
        raise ValueError('mechanical native pad UUID inventory changed')
    flipped = current['side'] != source['side']
    for uuid, before in old.items():
        after = new[uuid]
        for key in ('number', 'net', 'shape', 'size_nm', 'drill_size_nm', 'attribute', 'copper_centroid_reason'):
            if before.get(key) != after.get(key):
                raise ValueError('mechanical native pad signature changed')
        if 'layers' in before:
            expected = sorted(('B.' + layer[2:] if layer.startswith('F.') else
                               'F.' + layer[2:] if layer.startswith('B.') else layer)
                              if flipped else layer for layer in before['layers'])
            if expected != after.get('layers'):
                raise ValueError('mechanical native pad layers changed')
        local = rotate([before['position_nm'][i] - source['position_nm'][i] for i in (0, 1)],
                       -_angle(source['rotation_deg']) // 90)
        expected = _move(local, current['position_nm'], current['rotation_deg'], flipped)
        relative = (_angle(before['rotation_deg']) - _angle(source['rotation_deg'])) % 360
        angle = (_angle(current['rotation_deg']) + (-relative if flipped else relative)) % 360
        if expected != after['position_nm'] or angle != _angle(after['rotation_deg']):
            raise ValueError('mechanical native pad pose changed independently of owner')
        for key in ('copper_centroid_offset_nm', 'copper_land_offset_nm'):
            if (key in before) != (key in after):
                raise ValueError('mechanical native pad copper offset coverage changed')
            if key in before:
                local = rotate(_offset(before, key), -_angle(source['rotation_deg']) // 90)
                expected = _move(local, [0, 0], current['rotation_deg'], flipped)
                if expected != _offset(after, key):
                    raise ValueError('mechanical native pad copper offset changed independently of owner')
    return current


def materialize(binding, capture, phase=None, partial=False):
    """Project fixed and attached geometry onto every applicable current face.

    Partial search captures may omit unplaced owners only after initial bind()
    checked their identities. Full final/saved checks require every bound owner.
    """
    document, owners = _binding(binding)
    if type(partial) is not bool:
        raise ValueError('mechanical partial must be boolean')
    entries = _selected_rows(binding, document)
    phases = sorted({p for row in entries for p in row['phases']})
    result = {'schema': 'agent-ee.materialized-mechanical-reservations/1', 'phase': phase,
              'phases': phases if phase is None else [phase], 'reservations': [],
              'unknown': [], 'advisories': [], 'status': 'pass'}
    if phase is not None and phase not in phases:
        result['unknown'].append({'reservation': None, 'kind': 'phase', 'reason': 'requested mechanical phase is undeclared'})
    try:
        if capture.get('footprints') or not partial:
            validate_capture(capture)
        elif capture.get('schema') != 'agent-ee.placement-probe/1' or capture.get('footprints') != []:
            raise ValueError('invalid partial mechanical capture')
        current = {fp['uuid']: fp for fp in capture['footprints']}
    except (ValueError, KeyError, TypeError) as exc:
        result['unknown'].append({'reservation': None, 'kind': 'capture', 'reason': str(exc)})
        result['status'] = 'unknown'
        return result
    verified = {}
    for row in entries:
        if phase is not None and phase not in row['phases']:
            continue
        problems = []
        if row['geometry']['polygons_nm'] is None:
            problems.append('geometry is unresolved')
        if row['height_nm'] is None or row['coverage']['height'] != 'covered':
            problems.append('height coverage is unresolved')
        if row['coverage']['access'] != 'covered':
            problems.append('access coverage is unresolved')
        owner = row.get('owner')
        origin, angle, flipped, fp = [0, 0], 0, False, None
        if owner is not None:
            source = owners[owner['uuid']]
            if source['ref'] != owner['ref']:
                problems.append('bound owner reference differs')
            elif owner['uuid'] not in current:
                stale_ref = any(fp.get('ref') == owner['ref'] for fp in current.values())
                if partial and not problems and not stale_ref:
                    continue
                problems.append('required native owner is missing')
            else:
                try:
                    if owner['uuid'] not in verified:
                        verified[owner['uuid']] = _current_owner(source, current[owner['uuid']])
                    fp = verified[owner['uuid']]
                    frame = fp
                    if row['frame'] == 'pad':
                        matches = [p for p in fp['pads'] if p['uuid'] == owner['pad']['uuid']
                                   and p['number'] == owner['pad']['number']]
                        if len(matches) != 1:
                            raise ValueError('mechanical pad frame is missing or stale')
                        frame = matches[0]
                    origin, angle = frame['position_nm'], frame['rotation_deg']
                    flipped = fp['side'] != source['side']
                except ValueError as exc:
                    problems.append(str(exc))
        if problems:
            target = result['unknown'] if row['required'] else result['advisories']
            target.append({'reservation': row['id'], 'kind': 'unresolved', 'reason': '; '.join(problems)})
            continue
        faces = row['faces'] if fp is None else [fp['side'] if f == 'same' else
                                                ('B' if fp['side'] == 'F' else 'F') for f in row['faces']]
        polygons = [[_move(p, origin, angle, flipped) for p in polygon]
                    for polygon in row['geometry']['polygons_nm']]
        result['reservations'].append({'id': row['id'], 'kind': row['kind'], 'required': row['required'],
            'owner_uuid': None if owner is None else owner['uuid'],
            'owner_ref': None if owner is None else owner['ref'], 'faces': faces,
            'polygons_nm': polygons, 'height_nm': row['height_nm'], 'phases': row['phases'],
            'self_exclusion': row['self_exclusion'], 'source': document['source'], 'coverage': row['coverage'],
            'uncertainty': row['uncertainty']})
    result['status'] = 'unknown' if result['unknown'] else 'pass'
    return result


def grade(capture, binding, phase=None, partial=False):
    """Compare projections to native courtyard bounds, preserving every unknown."""
    result = materialize(binding, capture, phase, partial)
    result['schema'] = 'agent-ee.mechanical-reservation-grade/1'
    result['defects'] = []
    for row in result['reservations']:
        polygons = [simple_polygon(p) for p in row['polygons_nm']]
        for fp in sorted(capture.get('footprints', []), key=lambda f: f['ref']):
            if row['self_exclusion'] and fp.get('uuid') == row['owner_uuid']:
                continue
            for side in row['faces']:
                record = fp.get('courtyards', {}).get(side)
                if record is None and fp.get('side') != side:
                    continue
                try:
                    outer, inner = courtyard_bounds(record)
                    inner_area = sum(intersection_area(a, b) for a in polygons for b in inner)
                    outer_area = sum(intersection_area(a, b) for a in polygons for b in outer)
                except (ValueError, TypeError) as exc:
                    target = result['unknown'] if row['required'] else result['advisories']
                    target.append({'reservation': row['id'], 'kind': 'courtyard',
                                   'ref': fp['ref'], 'side': side, 'reason': str(exc)})
                    continue
                if inner_area:
                    target = result['defects'] if row['required'] else result['advisories']
                    target.append({'kind': 'mechanical_reservation_overlap', 'reservation': row['id'],
                                   'ref': fp['ref'], 'uuid': fp['uuid'], 'side': side,
                                   'overlap_area_nm2': [inner_area.numerator, inner_area.denominator]})
                elif outer_area:
                    target = result['unknown'] if row['required'] else result['advisories']
                    target.append({'reservation': row['id'], 'kind': 'courtyard_approximation',
                                   'ref': fp['ref'], 'side': side,
                                   'reason': 'mechanical overlap lies only between native curve bounds'})
    # Bodies and access envelopes can meet beyond both native courtyards. Two
    # access envelopes alone occupy no solid volume, so their overlap is not an
    # obstruction without a separate simultaneous-service requirement.
    for index, left in enumerate(result['reservations']):
        for right in result['reservations'][index + 1:]:
            if left['kind'] == right['kind'] == 'access':
                continue
            if (left['owner_uuid'] is not None and left['owner_uuid'] == right['owner_uuid']
                    and left['self_exclusion'] and right['self_exclusion']):
                continue
            phases = sorted(set(left['phases']) & set(right['phases']))
            if phase is not None:
                phases = [phase] if phase in phases else []
            if not phases:
                continue
            for side in sorted(set(left['faces']) & set(right['faces'])):
                area = sum(intersection_area(simple_polygon(a), simple_polygon(b))
                           for a in left['polygons_nm'] for b in right['polygons_nm'])
                if area:
                    target = result['defects'] if left['required'] or right['required'] else result['advisories']
                    target.append({'kind': 'mechanical_reservation_pair_overlap',
                                   'reservation': left['id'], 'other_reservation': right['id'],
                                   'side': side, 'phases': phases,
                                   'overlap_area_nm2': [area.numerator, area.denominator]})
    result['status'] = 'defect' if result['defects'] else 'unknown' if result['unknown'] else 'pass'
    return result
