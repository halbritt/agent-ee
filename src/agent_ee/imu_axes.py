"""IMU transform and explicit silkscreen marking, over captured placement facts.

Axis mappings are engineer-interpreted datasheet facts tied to a footprint ID;
text citation verification does not independently interpret the source figure.
"""
from decimal import Decimal
import math
import uuid

from . import placement_predicates as predicates

MARKER_TEXT = 'IMU_AXES X forward Y left Z up'


def parameters(params):
    required = {'envelope', 'imu_ref', 'forward_axis', 'side', 'max_distance_mm',
                'marker_rotation_deg', 'facts_check', 'axis_fact'}
    if required - params.keys():
        raise ValueError('imu-axes/1 missing ' + ', '.join(sorted(required - params.keys())))
    if ('graphic_id' in params) == ('marker_ref' in params):
        raise ValueError('imu-axes/1 needs exactly one of graphic_id or marker_ref')
    for key in ('imu_ref', 'marker_ref', 'facts_check', 'axis_fact'):
        if key in params and (not isinstance(params[key], str) or not params[key].strip()):
            raise ValueError('imu-axes/1 needs nonempty ' + key)
    if 'graphic_id' in params:
        if not isinstance(params['graphic_id'], str) or str(uuid.UUID(params['graphic_id'])) != params['graphic_id']:
            raise ValueError('graphic_id must be a canonical native UUID')
    if params['forward_axis'] != 'x+' or params['side'] not in ('front', 'back'):
        raise ValueError('imu-axes/1 requires forward_axis x+ and side front/back')
    predicates.exact_nm(params['max_distance_mm'], 'max_distance_mm')
    # Arbitrary oblique text/footprint transforms are unqualified, never rounded.
    if predicates.native_rotation(params['marker_rotation_deg']) % 90:
        raise ValueError('marker_rotation_deg must be an exact quarter turn')


def envelope_declaration(validation, params):
    matches = [fp for fp in validation['fixed_footprints'] if fp['reference'] == params['imu_ref']]
    if len(matches) != 1 or matches[0]['side'] != params['side']:
        raise ValueError('IMU must select one envelope fixed footprint with the declared side')
    if Decimal(matches[0]['rotation_deg']) % 90:
        raise ValueError('IMU envelope rotation must be an exact quarter turn')
    return matches[0]


def captured_declaration(ctx):
    from .board_envelope import validate
    from .datasheet_facts import prerequisite
    from .placement_check import _captured
    from .storage import read_json
    from .checks import CheckError
    try:
        validation = validate(_captured(ctx, ctx.params['envelope']),
                              captured=(ctx.manifest['files'], ctx.inputs))
        fixed = envelope_declaration(validation, ctx.params)
        path = prerequisite(ctx, ctx.params['facts_check'], 'datasheet_facts', 'facts.json',
                            allow_unsupported=True)
        records = read_json(path)['facts']
        matches = [r['fact'] for r in records if r['status'] == 'verified' and r['id'] == ctx.params['axis_fact']]
        return {'fixed': fixed, 'fact': matches[0] if len(matches) == 1 else None}, validation
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise CheckError('malformed', 'IMU declaration unusable: ' + str(exc)) from exc


def mapped_axes(fact, fp):
    if (not isinstance(fact, dict) or fact.get('kind') != 'axis_orientation'
            or not fp['fpid'] or fact.get('part_number') != fp['value']
            or fact.get('footprint_id') != fp['fpid']):
        raise ValueError('axis fact unverified or inapplicable to native IMU part/footprint')
    axes = fact.get('axes')
    if (not isinstance(axes, dict) or set(axes) != {'X', 'Y', 'Z'}
            or axes['X'] not in predicates.AXES or axes['Y'] not in predicates.AXES
            or axes['Z'] not in ('z+', 'z-')):
        raise ValueError('axis fact needs X/Y planar directions and signed Z in the footprint frame')
    x, y = predicates.AXES[axes['X']], predicates.AXES[axes['Y']]
    # KiCad y points down: a right-handed X/Y/Z frame has negative planar determinant for Z up.
    if x[0]*y[1] - x[1]*y[0] != (-1 if axes['Z'] == 'z+' else 1):
        raise ValueError('axis fact is not an orthogonal right-handed frame')
    rotation = predicates.native_rotation(fp['rotation_deg'])
    if rotation % 90 or fp['side'] not in ('F', 'B'):
        raise ValueError('native IMU transform is not a qualified front/back quarter turn')
    z_up = (axes['Z'] == 'z+') != (fp['side'] == 'B')
    return {'X': predicates.board_axis(axes['X'], rotation, fp['side']),
            'Y': predicates.board_axis(axes['Y'], rotation, fp['side']), 'Z': 'z+' if z_up else 'z-'}


def marker_verdict(probe, params, fp):
    selector = {key: params[key] for key in ('graphic_id', 'marker_ref') if key in params}
    base = {'ref': params['imu_ref'], 'marker': selector}
    inventory = probe.get('markings')
    if not isinstance(inventory, list):
        return [], [{**base, 'kind': 'marking_inventory_unavailable'}], None
    if 'graphic_id' in params:
        matches = [m for m in inventory if m['uuid'] == params['graphic_id']]
    else:
        # A marker footprint must contain exactly one non-field text graphic.
        matches = [m for m in inventory if m['owner_ref'] == params['marker_ref']
                   and m['native_class'] != 'PCB_FIELD']
    if not matches:
        return [{**base, 'kind': 'marking_missing'}], [], None
    if len(matches) != 1:
        return [], [{**base, 'kind': 'marking_ambiguous'}], None
    marker, = matches
    defects, unknown = [], []
    if marker['layer'] != 'F.SilkS':
        defects.append({**base, 'kind': 'marking_layer', 'actual': marker['layer'], 'expected': 'F.SilkS'})
    if not readable(marker):
        unknown.append({**base, 'kind': 'marking_unreadable', 'uuid': marker['uuid']})
        return defects, unknown, marker
    if ' '.join(marker['text'].split()) != MARKER_TEXT:
        unknown.append({**base, 'kind': 'marking_content_unqualified', 'uuid': marker['uuid']})
    actual = predicates.native_rotation(marker['rotation_deg'])
    expected = predicates.native_rotation(params['marker_rotation_deg'])
    if actual != expected:
        defects.append({**base, 'kind': 'marking_rotation', 'actual_deg': float(actual),
                        'expected_deg': float(expected)})
    distance2 = sum((a-b)**2 for a, b in zip(marker['position_nm'], fp['position_nm']))
    maximum = predicates.exact_nm(params['max_distance_mm'], 'max_distance_mm')
    if distance2 > maximum**2:
        defects.append({**base, 'kind': 'marking_distance', 'distance_mm': math.sqrt(distance2)/10**6,
                        'maximum_mm': predicates.mm(maximum)})
    return defects, unknown, marker


def readable(marker):
    """Qualified stroke text only; unfamiliar rendering stays unknown."""
    return (isinstance(marker.get('text'), str) and marker.get('visible') is True
            and marker.get('mirrored') is False and marker.get('knockout') is False
            and marker.get('font') in (None, 'KiCad Font')
            and len(marker.get('size_nm', [])) == 2 and min(marker['size_nm']) >= 800000
            and 120000 <= marker.get('thickness_nm', 0) <= min(marker['size_nm']) // 4)


def evaluate(probe, params, declaration):
    ref = params['imu_ref']
    base = {'ref': ref, 'marker': {k: params[k] for k in ('graphic_id', 'marker_ref') if k in params}}
    defects, unknown = [], []
    matches = [fp for fp in probe['footprints'] if fp['ref'] == ref]
    measured = {'imu': None, 'marker': None, 'board_axes': None}
    if len(matches) != 1:
        unknown.append({**base, 'kind': 'imu_missing_or_ambiguous'})
    else:
        fp, = matches
        measured['imu'] = {k: fp[k] for k in ('ref', 'uuid', 'value', 'fpid', 'rotation_deg', 'side')}
        fixed = declaration['fixed']
        for key, actual, expected in (
                ('imu_side', fp['side'], {'front': 'F', 'back': 'B'}[fixed['side']]),
                ('imu_rotation', predicates.native_rotation(fp['rotation_deg']), (Decimal(fixed['rotation_deg']) % 360 + 360) % 360)):
            if actual != expected:
                defects.append({**base, 'kind': key, 'actual': str(actual), 'expected': str(expected)})
        try:
            measured['board_axes'] = mapped_axes(declaration['fact'], fp)
        except ValueError as exc:
            unknown.append({**base, 'kind': 'imu_axes_unqualified', 'reason': str(exc)})
        else:
            if measured['board_axes'] != {'X': 'x+', 'Y': 'y-', 'Z': 'z+'}:
                defects.append({**base, 'kind': 'imu_axis_alignment', 'actual': measured['board_axes']})
        marker_defects, marker_unknown, measured['marker'] = marker_verdict(probe, params, fp)
        defects.extend(marker_defects)
        unknown.extend(marker_unknown)
    return {'criterion': 'imu-axes/1', 'status': predicates.verdict(defects, unknown),
            'defects': defects, 'unknown': unknown, 'measured': measured, 'selection': base,
            'axis_fact': declaration['fact']}
