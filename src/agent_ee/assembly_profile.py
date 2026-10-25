"""Explicit assembly-profile orientation assessment over a sealed fab package (AGENTEE-524 part B).

A captured, immutable `agent-ee.assembly-profile/1` document names the supplier
CPL convention a package is assessed against, the `cpl_rotation` check and
policy digest it relies on, and the row sides and mount types it applies to.
`orientation_assessment` then derives one `agent-ee.assembly-orientation/1`
record per package from facts the package already sealed: the native export
CPL, the passing complete `cpl_rotation` report and the sealed pcbnew pad
plating. It computes no geometry of its own.

The record keeps two things apart for every CPL row. The **angle** basis is
`native_supported` (no angular correction, pin-1 frames agree at 0°),
`corrected` (a sourced policy rule applied, pin-1 frames agree with it),
`outside_profile` (side or mount the profile does not claim) or `unassessed`.
The **anchor** is reported separately: `cpl_rotation` replaces the native
footprint anchor with the pad-box centroid even at 0°, so `anchor.changed`
says whether the exported position differs from the native one, whatever the
angle basis says. A profile is a software assessment only; `supplier_confirmation`
stays `pending_preview` until a preview is actually performed, and nothing here
proves the supplier will assemble every declared mount type.
"""

from decimal import Decimal, InvalidOperation

from .cpl_rotation import read_native_cpl

SCHEMA = 'agent-ee.assembly-profile/1'
ASSESSMENT_SCHEMA = 'agent-ee.assembly-orientation/1'
SUPPORTED_PROFILES = frozenset({'jlcpcb-cpl-software/1'})
PROFILE_KEYS = frozenset({'schema', 'profile_id', 'supplier', 'service', 'applicability', 'rotation_check',
                          'policy_sha256', 'position_convention', 'sources', 'rationale'})
SIDES = ('Top', 'Bottom')
MOUNTS = ('smd', 'through_hole')
# The only CPL position/angle convention cpl_rotation produces (docs/cpl-rotation.md).
POSITION_CONVENTION = {'x': 'board_x_mm', 'y': 'negated_board_y_mm', 'anchor': 'pad_box_centroid',
                       'angle': 'native_plus_policy_delta_bottom_mirrored'}
# pcbnew pad attribute -> mount type; NPTH holes carry no part and never decide a mount type.
PAD_MOUNT = {'SMD': 'smd', 'PTH': 'through_hole', 'CONN': 'smd'}
ANGLE_BASES = ('native_supported', 'corrected', 'outside_profile', 'unassessed')
SUPPLIER_CONFIRMATION = 'pending_preview'


def validate_profile(document):
    """The profile document, or ValueError naming the first defect (fail closed)."""
    if not isinstance(document, dict) or set(document) != PROFILE_KEYS:
        raise ValueError('assembly profile fields must be exactly ' + ', '.join(sorted(PROFILE_KEYS)))
    if document['schema'] != SCHEMA:
        raise ValueError(f'assembly profile schema must be {SCHEMA}')
    if document['profile_id'] not in SUPPORTED_PROFILES:
        raise ValueError(f'unsupported assembly profile {document["profile_id"]!r}')
    for key in ('supplier', 'service', 'rotation_check', 'rationale'):
        if not isinstance(document[key], str) or not document[key].strip():
            raise ValueError(f'assembly profile {key} must be a nonempty string')
    sha = document['policy_sha256']
    if not isinstance(sha, str) or len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha):
        raise ValueError('assembly profile policy_sha256 must be a lowercase sha256')
    applicability = document['applicability']
    if not isinstance(applicability, dict) or set(applicability) != {'sides', 'mount'}:
        raise ValueError('assembly profile applicability must be exactly {sides, mount}')
    for key, allowed in (('sides', SIDES), ('mount', MOUNTS)):
        values = applicability[key]
        if (not isinstance(values, list) or not values or len(set(values)) != len(values)
                or any(v not in allowed for v in values)):
            raise ValueError(f'assembly profile applicability.{key} must be a nonempty subset of {list(allowed)}')
    if document['position_convention'] != POSITION_CONVENTION:
        raise ValueError(f'assembly profile position_convention must be exactly {POSITION_CONVENTION}')
    sources = document['sources']
    if not isinstance(sources, list) or not sources or any(not isinstance(s, str) or not s.strip() for s in sources):
        raise ValueError('assembly profile sources must be a nonempty list of citations')
    return document


def mount_types(pads):
    """{reference: mount type} from sealed pcbnew pad plating; ValueError on an unknown attribute."""
    mounts = {}
    for pad in pads:
        if not isinstance(pad, dict) or not isinstance(pad.get('ref'), str):
            raise ValueError('sealed geometry pad lacks its footprint reference')
        attribute = pad.get('attribute')
        if attribute == 'NPTH':
            continue
        if attribute not in PAD_MOUNT:
            raise ValueError(f'sealed geometry pad {pad["ref"]}/{pad.get("pad")} has unknown attribute {attribute!r}')
        mount = PAD_MOUNT[attribute]
        if mount == 'through_hole' or pad['ref'] not in mounts:
            mounts[pad['ref']] = mount
    return mounts


def _decimal(text, label):
    try:
        return Decimal(text)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f'{label} {text!r} is not a decimal') from exc


def unassessed_record(rotation, native_cpl_text, reason):
    """The honest record of a package assessed under no profile: every row unassessed."""
    rows = [{'designator': ref, 'side': side, 'applicability': 'unassessed',
             'angle': {'native_degrees': angle, 'exported_degrees': None, 'delta_degrees': None,
                       'basis': 'unassessed', 'evidence': None},
             'anchor': {'native_mm': [x, y], 'exported_mm': None, 'changed': None, 'basis': 'footprint_anchor'}}
            for ref, x, y, side, angle in read_native_cpl(native_cpl_text)]
    return {'schema': ASSESSMENT_SCHEMA, 'profile_id': None, 'profile_sha256': None,
            'rotation_status': rotation.get('status'), 'reason': reason, 'rows': rows,
            'software_assessment': 'unassessed',
            'unassessed_rows': [{'designator': r['designator'], 'reason': reason} for r in rows],
            'supplier_confirmation': SUPPLIER_CONFIRMATION if rotation.get('status') == 'PREVIEW_REQUIRED'
            else 'unassessed'}


def orientation_assessment(profile, profile_sha256, rotation, report, native_cpl_text, pads):
    """`agent-ee.assembly-orientation/1` for a sealed package under `profile`.

    `rotation` is the package's recomputed `rotation_record`; `report` the sealed passing complete
    `cpl_rotation` report it names; `native_cpl_text` the package's native export CPL; `pads` the
    sealed pcbnew pads. Raises ValueError when the profile does not bind to the sealed rotation
    evidence or a row lacks the facts it needs; the caller reports that as unsupported.
    """
    validate_profile(profile)
    if rotation.get('status') == 'not_declared' or rotation.get('check_id') != profile['rotation_check']:
        raise ValueError(f'assembly profile names cpl_rotation {profile["rotation_check"]!r}, '
                         f'but the package sealed {rotation.get("check_id")!r}')
    if rotation.get('policy_sha256') != profile['policy_sha256']:
        raise ValueError('assembly profile policy_sha256 differs from the sealed rotation policy')
    if (report.get('execution'), report.get('outcome'), report.get('coverage')) != ('ok', 'pass', 'complete'):
        raise ValueError('assembly assessment needs a passing complete cpl_rotation report')
    records = {}
    for record in report.get('designators') or []:
        if not isinstance(record, dict) or record.get('designator') in records:
            raise ValueError('cpl_rotation report designator records are not unique')
        records[record.get('designator')] = record
    mounts = mount_types(pads)
    sides, mount_scope = profile['applicability']['sides'], profile['applicability']['mount']
    rows, unassessed = [], []
    for ref, x, y, side, angle in read_native_cpl(native_cpl_text):
        record = records.get(ref)
        if record is None:
            raise ValueError(f'cpl_rotation report lacks CPL row {ref}')
        if ref not in mounts:
            raise ValueError(f'sealed geometry has no plated pad for {ref}; mount type unknown')
        exported = record.get('cpl')
        if (not isinstance(exported, list) or len(exported) != 5 or exported[0] != ref
                or exported[3] != side or record.get('layer') != side):
            raise ValueError(f'cpl_rotation report row for {ref} does not match the native CPL row')
        if (record.get('execution'), record.get('outcome')) != ('ok', 'pass'):
            raise ValueError(f'cpl_rotation report row for {ref} did not pass')
        policy = record.get('policy') or None
        delta = _decimal(policy['rotation_degrees'] if policy else '0', f'{ref} rotation delta')
        pin1 = record.get('pin1') or {}
        if pin1.get('offset_degrees') is None or _decimal(pin1['offset_degrees'], f'{ref} pin-1 offset') != delta % 360:
            raise ValueError(f'{ref}: sealed pin-1 offset {pin1.get("offset_degrees")!r} does not support delta {delta}')
        mount = mounts[ref]
        in_profile = side in sides and mount in mount_scope
        if not in_profile:
            basis = 'outside_profile'
            unassessed.append({'designator': ref, 'reason': f'{side}/{mount} is outside the profile applicability'})
        else:
            basis = 'corrected' if delta % 360 else 'native_supported'
        native_xy = [_decimal(x, f'{ref} x'), _decimal(y, f'{ref} y')]
        exported_xy = [_decimal(exported[1], f'{ref} x'), _decimal(exported[2], f'{ref} y')]
        rows.append({
            'designator': ref, 'side': side, 'mount': mount,
            'applicability': 'in_profile' if in_profile else 'outside_profile',
            'angle': {'native_degrees': angle, 'exported_degrees': exported[4], 'delta_degrees': str(delta),
                      'basis': basis,
                      'evidence': {'board_pin1': pin1.get('board'), 'supplier_pin1': pin1.get('supplier'),
                                   'offset_degrees': pin1.get('offset_degrees'),
                                   'policy_id': report.get('policy', {}).get('policy_id') if policy else None,
                                   'rule_id': policy.get('rule_id') if policy else None,
                                   'polarity': record.get('polarity'), 'lcsc': record.get('lcsc'),
                                   'footprint': record.get('footprint')}},
            'anchor': {'native_mm': [x, y], 'exported_mm': [exported[1], exported[2]],
                       'changed': native_xy != exported_xy, 'basis': 'pad_box_centroid',
                       'native_basis': 'footprint_anchor'}})
    obligations = rotation.get('preview_obligations') or {}
    return {'schema': ASSESSMENT_SCHEMA, 'profile_id': profile['profile_id'], 'profile_sha256': profile_sha256,
            'supplier': profile['supplier'], 'service': profile['service'],
            'applicability': profile['applicability'], 'position_convention': POSITION_CONVENTION,
            'rotation_status': rotation['status'], 'rotation_check': rotation['check_id'],
            'policy_id': rotation.get('policy_id'), 'policy_sha256': rotation['policy_sha256'],
            'rows': rows, 'software_assessment': 'complete' if not unassessed else 'incomplete',
            'unassessed_rows': unassessed, 'supplier_confirmation': SUPPLIER_CONFIRMATION,
            'preview_obligation_count': len(obligations.get('designators') or [])}
