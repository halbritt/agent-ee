"""Known-good block library (AGENTEE-342): versioned entries with provenance receipts.

The ``blocks/`` catalogue holds immutable, versioned ``agent-ee.block-library-entry/1``
files. An entry stores only numeric layout records and source identities: member
footprint identities, anchor-relative quarter-turn poses, land-pattern and courtyard
hashes, net-role partitions and, for a board region, the block's framed copper. No
board file, Gerber, figure or courtyard polygon is stored. ``materialize`` rebuilds
an ``agent-ee.block-candidate/2`` (AGENTEE-339) from a board's own footprints at the
entry poses and re-grades it with the same graders, so every use is re-proven.

Three import paths: a KiCad board region, a datasheet reference-layout figure whose
scale is verified against independent dimensions, and a project's hand-placed
cluster. Every entry is ``search-derived`` and advisory. The hard classes
(manufacturer reference layout, manufacturer EVM, qualified owner board) are disabled
(coordinator ruling 2026-10-21): agents run as the same user and can author any record,
file, catalogue root or argument, so no caller-supplied input can confer one until a
verified owner authority mechanism exists (AGENTEE-348). Every path refuses a hard
class with ``authority_unavailable``; a datasheet figure imports as advisory. The
``agent-ee.block-authority/1`` binding (``verify_authority``) is kept for that
mechanism and binds the complete numeric payload. See docs/block-library.md.
"""
import argparse
import copy
import hashlib
import json
from fractions import Fraction
from pathlib import Path

from . import blocks, placer
from .catalog import strict_json
from .footprint_library import digest
from .native_netlist import read_native_inventory, read_sheet_names
from .placement import judge
from .symbol_library import LibraryError, children, nanometres, parse_sexpr

ENTRY = 'agent-ee.block-library-entry/1'
CATALOGUE = 'agent-ee.block-catalogue/1'
FIGURE = 'agent-ee.reference-layout-figure/1'
AUTHORITY_RECORD = 'agent-ee.block-authority/1'
IMPORTER = 'agent_ee.block_library/1'
STRATEGY = 'imported-layout/1'
CATALOGUE_PATH = 'blocks/catalogue.json'
# What a datasheet figure claims to be; its entry is search-derived while hard classes are disabled.
FIGURE_CLASSES = frozenset(('manufacturer-reference-layout', 'manufacturer-evm'))
# Which provenance classes each import path may carry. A project cluster is the
# engineer's or an agent's arrangement, never a reference layout (owner ruling 2026-10-21).
IMPORTS = {'board-region/1': frozenset(blocks.PROVENANCE),
           'reference-figure/1': FIGURE_CLASSES | {'search-derived'},
           'project-cluster/1': frozenset(('search-derived',))}
# Lookup order: the strongest authority first.
AUTHORITY = ('manufacturer-reference-layout', 'manufacturer-evm', 'qualified-owner-board', 'search-derived')
# Classes that only a verified owner authority can confer; disabled until AGENTEE-348.
HARD = frozenset(c for c, enforcement in blocks.PROVENANCE.items() if enforcement == 'hard')
ENTRY_KEYS = frozenset(('schema', 'id', 'version', 'part', 'provenance', 'layout', 'copper', 'receipt'))
MEMBER_KEYS = frozenset(('reference', 'fpid', 'side', 'value', 'pose', 'pad_geometry_sha256', 'courtyard_sha256',
                         'pin_functions_sha256', 'pins'))
RECEIPT_KEYS = frozenset(('importer', 'import', 'source', 'inputs', 'candidate_sha256'))
INPUT_KEYS = ('board_sha256', 'inventory_sha256', 'policy_sha256')


class AuthorityUnavailable(ValueError):
    """A hard class, or an authority record, was offered while no verified authority exists."""

    def __init__(self, what):
        super().__init__(f'authority_unavailable: {what}; hard classes are disabled until a verified owner '
                         'authority mechanism exists (AGENTEE-348), so library entries are search-derived only')


def _no_authority(provenance_class=None, authority_raw=None):
    """Refuse a hard class or any caller-supplied authority record (``authority_unavailable``)."""
    if provenance_class in HARD:
        raise AuthorityUnavailable(f'{provenance_class} cannot be conferred')
    if authority_raw is not None:
        raise AuthorityUnavailable('an authority record cannot confer a class')


def _keys(value, required, optional=(), label='record'):
    return blocks._keys(value, required, optional, label)


def _text(value, label):
    return blocks._text(value, label)


def _version(value):
    if type(value) is not int or value < 1:
        raise ValueError('entry version must be a positive integer')
    return value


def _id(value):
    _text(value, 'entry id')
    if any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in value) or value.startswith('-'):
        raise ValueError('entry id must be lowercase letters, digits and hyphens')
    return value


def _pose(pose, label):
    if (not isinstance(pose, dict) or set(pose) != {'position_nm', 'rotation_deg'}
            or not isinstance(pose['position_nm'], list) or len(pose['position_nm']) != 2
            or any(type(v) is not int for v in pose['position_nm'])
            or type(pose['rotation_deg']) is not int or pose['rotation_deg'] not in (0, 90, 180, 270)):
        raise ValueError(f'{label} pose needs integer nanometres and a quarter-turn rotation')


def _quarter(rotation, label):
    turns = Fraction(str(rotation)) / 90
    if turns.denominator != 1:
        raise ValueError(f'{label}: rotation {rotation} is not a quarter turn')
    return int(turns) % 4 * 90


def pin_functions_sha256(component, pads):
    """Identity of a member's pin names and electrical types, without storing their text."""
    return digest([[pad, component['pin_metadata'].get(pad)] for pad in pads])


def _role_partition(rows):
    """Per (member, pad): the role and boundary flag, the shape the entry stores."""
    return {row['reference']: {pin['pad']: (None if pin['role'] is None else [pin['role'], pin['boundary']])
                               for pin in row['pins']} for row in rows}


# Entry validation ----------------------------------------------------------------

def validate_entry(entry):
    """Structural and provenance contract of a library entry; raises ValueError. Needs no board."""
    try:
        return _validate_entry(entry)
    except (TypeError, KeyError, AttributeError, IndexError) as exc:
        raise ValueError(f'malformed block library entry: {type(exc).__name__}: {exc}') from None


def _validate_entry(entry):
    if not isinstance(entry, dict) or entry.get('schema') != ENTRY:
        raise ValueError(f'expected {ENTRY}')
    _keys(entry, ENTRY_KEYS, label='library entry')
    _id(entry['id'])
    _version(entry['version'])
    _text(entry['part'], 'entry part')
    receipt = _keys(entry['receipt'], RECEIPT_KEYS, {'figure', 'scale', 'authority_sha256'}, label='entry receipt')
    if receipt['importer'] != IMPORTER or receipt['import'] not in IMPORTS:
        raise ValueError(f'receipt must name {IMPORTER} and one of ' + ', '.join(sorted(IMPORTS)))
    kind = receipt['import']
    provenance = _keys(entry['provenance'], {'class', 'enforcement', 'citation', 'label'}, {'source'},
                       label='entry provenance')
    if provenance['class'] not in blocks.PROVENANCE or provenance['enforcement'] != blocks.PROVENANCE[
            provenance['class']]:
        raise ValueError('entry provenance needs a known class and its enforcement')
    if provenance['class'] not in IMPORTS[kind]:
        raise ValueError(f"import {kind} cannot carry provenance class {provenance['class']}")
    _no_authority(provenance['class'])
    if 'authority_sha256' in receipt:
        raise AuthorityUnavailable('an entry cannot bind an authority record')
    _text(provenance['citation'], 'entry citation')
    _text(provenance['label'], 'entry label')
    source = _source_record(receipt['source'], 'receipt source')
    if provenance['class'] == 'search-derived':
        if 'source' in provenance:
            raise ValueError('a search-derived entry has no reference source')
    elif provenance.get('source') != {'locator': source['locator'], 'sha256': source['sha256']}:
        raise ValueError('a reference entry cites its receipt source locator and sha256')
    if kind == 'project-cluster/1' and not {'repository', 'commit', 'path'} <= set(source):
        raise ValueError('a project cluster names its repository, commit and path')
    inputs = _keys(receipt['inputs'], set(INPUT_KEYS), label='receipt inputs')
    for key in INPUT_KEYS:
        blocks._hex(inputs[key], 'input ' + key)
    if kind == 'board-region/1' and source['sha256'] != inputs['board_sha256']:
        raise ValueError('a board region source is the probed board: source sha256 must equal its board_sha256')
    blocks._hex(receipt['candidate_sha256'], 'candidate sha256')
    if kind == 'reference-figure/1':
        if 'figure' not in receipt or 'scale' not in receipt:
            raise ValueError('a reference figure entry keeps its figure record and scale verification')
        figure = validate_figure(receipt['figure'])
        if figure['document']['sha256'] != source['sha256']:
            raise ValueError('figure document must equal the receipt source')
        if verify_scale(figure) != receipt['scale']:
            raise ValueError('stored scale verification differs from the figure record')
    elif 'figure' in receipt or 'scale' in receipt:
        raise ValueError('only a reference figure entry keeps a figure record')
    layout = _keys(entry['layout'], {'anchor', 'members'}, label='entry layout')
    rows = layout['members']
    refs = [r.get('reference') if isinstance(r, dict) else None for r in rows] if isinstance(rows, list) else []
    if not refs or len(set(refs)) != len(refs) or layout['anchor'] not in refs:
        raise ValueError('layout members need unique references including the anchor')
    roles = {}
    for row in rows:
        _keys(row, MEMBER_KEYS, label='layout member')
        _text(row['reference'], 'member reference')
        _text(row['fpid'], 'member fpid')
        if row['side'] not in ('F', 'B'):
            raise ValueError('member side must be F or B')
        for key in ('pad_geometry_sha256', 'courtyard_sha256', 'pin_functions_sha256'):
            blocks._hex(row[key], key)
        _pose(row['pose'], row['reference'])
        if row['reference'] == layout['anchor'] and row['pose'] != {'position_nm': [0, 0], 'rotation_deg': 0}:
            raise ValueError('the anchor defines the layout frame at the origin, rotation 0')
        blocks._value_rule(row['value'])
        if not isinstance(row['pins'], dict) or not row['pins']:
            raise ValueError('member pins map pad numbers to roles')
        for pad, role in row['pins'].items():
            _text(pad, 'pin pad')
            if role is None:
                continue
            if (not isinstance(role, list) or len(role) != 2 or not isinstance(role[0], str)
                    or not role[0].startswith('n') or not role[0][1:].isdigit() or type(role[1]) is not bool):
                raise ValueError('pin role must be [n<k>, boundary] or null')
            if roles.setdefault(role[0], role[1]) != role[1]:
                raise ValueError(f'net role {role[0]} must have one boundary flag')
    _copper(entry['copper'], set(roles), kind)
    return entry


def _source_record(value, label):
    source = _keys(value, {'locator', 'sha256'}, {'repository', 'commit', 'path'}, label=label)
    _text(source['locator'], 'source locator')
    blocks._hex(source['sha256'], 'source sha256')
    for key in ('repository', 'path'):
        if key in source:
            _text(source[key], 'source ' + key)
    if 'commit' in source and (not isinstance(source['commit'], str) or len(source['commit']) != 40
                               or any(c not in '0123456789abcdef' for c in source['commit'])):
        raise ValueError('source commit must be a full lowercase Git object id')
    return source


# Owner authority binding (for AGENTEE-348) -------------------------------------------

def payload_sha256(entry):
    """Identity of an entry's complete immutable payload: everything but its authority hash.

    It covers id, version, part, provenance, the layout poses and identities, copper and
    every receipt identity (source, inputs, candidate hash, figure and scale).
    """
    body = copy.deepcopy(entry)
    body['receipt'].pop('authority_sha256', None)
    return digest(body)


def validate_authority(record):
    """An ``agent-ee.block-authority/1`` record: who confers which hard class on which payload.

    The record format the verified owner authority mechanism (AGENTEE-348) will deliver.
    Validating it confers nothing: no caller-supplied record yields a hard class (see
    ``AuthorityUnavailable``). A manufacturer class also names the independent capture of
    the source; a figure lists the documented dimensions its scale is checked against.
    Raises ValueError.
    """
    _keys(record, {'schema', 'class', 'import', 'source', 'references', 'payload_sha256', 'owner', 'disposition',
                   'reference'}, {'capture', 'dimensions'}, label='authority record')
    if record['schema'] != AUTHORITY_RECORD:
        raise ValueError(f'expected {AUTHORITY_RECORD}')
    if record['class'] not in HARD:
        raise ValueError('an authority record confers a hard class')
    if record['import'] not in IMPORTS or record['class'] not in IMPORTS[record['import']]:
        raise ValueError(f"authority import {record['import']} cannot carry class {record['class']}")
    source = _source_record(record['source'], 'authority source')
    if record['class'] == 'qualified-owner-board' and not {'repository', 'commit', 'path'} <= set(source):
        raise ValueError('a qualified owner board authority names its repository, commit and path')
    references = _texts(record['references'])
    if references != sorted(set(references), key=blocks._natural):
        raise ValueError('authority references must be unique and in natural order')
    blocks._hex(record['payload_sha256'], 'authority payload sha256')
    for key in ('owner', 'reference'):
        _text(record[key], 'authority ' + key)
    if record['disposition'] != 'approved':
        raise ValueError('authority disposition must be approved')
    if record['class'].startswith('manufacturer-'):
        if 'capture' not in record:
            raise ValueError('a manufacturer class needs the independent capture of its source')
        capture = _keys(record['capture'], {'origin', 'captured_by'}, label='authority capture')
        _text(capture['origin'], 'capture origin')
        _text(capture['captured_by'], 'capture captured_by')
        if capture['captured_by'] == IMPORTER:
            raise ValueError('the importer cannot be the capturer of its own source')
    elif 'capture' in record:
        raise ValueError('only a manufacturer class records a source capture')
    if record['import'] == 'reference-figure/1':
        dimensions = record.get('dimensions')
        if not isinstance(dimensions, list) or len(dimensions) < 2:
            raise ValueError('a figure authority lists the documented scale and check dimensions')
        for dimension in dimensions:
            _keys(dimension, {'basis', 'length_nm'}, label='authority dimension')
            _text(dimension['basis'], 'dimension basis')
            if type(dimension['length_nm']) is not int or dimension['length_nm'] <= 0:
                raise ValueError('authority dimension length_nm must be a positive integer')
    elif 'dimensions' in record:
        raise ValueError('only a figure authority lists dimensions')
    return record


def verify_authority(raw, entry):
    """Whether authority record bytes bind to an entry: hash, class, import, source, members and payload.

    A binding check only, for the verified authority mechanism (AGENTEE-348); it confers
    no class, and ``validate_entry`` still refuses every hard-class entry. Any change to
    the layout, copper or receipt identities after approval is ``authority_mismatch``.
    """
    receipt = entry['receipt']
    if 'authority_sha256' not in receipt:
        raise ValueError('authority_required: the entry names no authority record')
    if raw is None:
        raise ValueError('authority_unverified: the hard-class entry needs its authority record bytes')
    if hashlib.sha256(raw).hexdigest() != receipt['authority_sha256']:
        raise ValueError('authority_mismatch: record bytes differ from the receipt authority_sha256')
    record = validate_authority(strict_json(raw))
    members = sorted((r['reference'] for r in entry['layout']['members']), key=blocks._natural)
    for key, expected in (('class', entry['provenance']['class']), ('import', receipt['import']),
                          ('source', receipt['source']), ('references', members),
                          ('payload_sha256', payload_sha256(entry))):
        if record[key] != expected:
            raise ValueError(f'authority_mismatch: record {key} differs from the entry')
    if receipt['import'] == 'reference-figure/1':
        documented = {(d['basis'], d['length_nm']) for d in record['dimensions']}
        scale = receipt['figure']['scale']
        cited = [(d['basis'], d['length_nm']) for d in [scale['reference']] + scale['checks']]
        missing = [basis for basis, length in cited if (basis, length) not in documented]
        if missing:
            raise ValueError('authority_mismatch: figure dimensions not documented by the authority: '
                             + '; '.join(missing))
    return record


def _copper(copper, roles, kind):
    if kind != 'board-region/1':
        if copper is not None:
            raise ValueError('only a board region entry carries copper')
        return
    _keys(copper, {'tracks', 'arcs', 'vias'}, label='entry copper')
    for key, required in (('tracks', ('start', 'end')), ('arcs', ('start', 'mid', 'end')), ('vias', ('at',))):
        if not isinstance(copper[key], list):
            raise ValueError(f'copper {key} must be a list')
        for item in copper[key]:
            sizes = ('size_nm', 'drill_nm') if key == 'vias' else ('width_nm',)
            _keys(item, set(required) | set(sizes) | {'role', 'layers' if key == 'vias' else 'layer'},
                  label='copper ' + key)
            for point in required:
                if (not isinstance(item[point], list) or len(item[point]) != 2
                        or any(type(v) is not int for v in item[point])):
                    raise ValueError(f'copper {key} {point} must be integer nanometres')
            for size in sizes:
                if type(item[size]) is not int or item[size] <= 0:
                    raise ValueError(f'copper {key} {size} must be a positive integer')
            if item['role'] not in roles:
                raise ValueError(f"copper role {item['role']} is not a member net role")


def validate_figure(figure):
    """A datasheet reference-layout figure as numeric records; raises ValueError."""
    _keys(figure, {'schema', 'document', 'class', 'axes', 'scale', 'anchor', 'members'}, label='figure record')
    if figure['schema'] != FIGURE:
        raise ValueError(f'expected {FIGURE}')
    document = _keys(figure['document'], {'locator', 'sha256', 'figure', 'page'}, label='figure document')
    for key in ('locator', 'figure'):
        _text(document[key], 'figure document ' + key)
    blocks._hex(document['sha256'], 'figure document sha256')
    if type(document['page']) is not int or document['page'] < 1:
        raise ValueError('figure document page must be a positive integer')
    if figure['class'] not in FIGURE_CLASSES:
        raise ValueError('a figure is a manufacturer reference layout or EVM')
    if figure['axes'] not in ('x-right-y-down', 'x-right-y-up'):
        raise ValueError('figure axes must be x-right-y-down or x-right-y-up')
    scale = _keys(figure['scale'], {'reference', 'checks', 'tolerance'}, label='figure scale')
    if not isinstance(scale['checks'], list) or not scale['checks']:
        raise ValueError('a figure scale needs at least one independent check dimension')
    for record in [scale['reference']] + scale['checks']:
        _keys(record, {'from', 'to', 'length_nm', 'basis'}, label='scale dimension')
        _point(record['from']), _point(record['to'])
        if type(record['length_nm']) is not int or record['length_nm'] <= 0:
            raise ValueError('scale dimension length_nm must be a positive integer')
        _text(record['basis'], 'scale dimension basis')
        if record['from'] == record['to']:
            raise ValueError('scale dimension endpoints must differ')
    tolerance = blocks._ratio(scale['tolerance'], 'scale tolerance')
    if not 0 < tolerance < 1:
        raise ValueError('scale tolerance must lie strictly between 0 and 1')
    members = figure['members']
    refs = [m.get('reference') if isinstance(m, dict) else None for m in members] if isinstance(members, list) else []
    if not refs or len(set(refs)) != len(refs) or figure['anchor'] not in refs:
        raise ValueError('figure members need unique references including the anchor')
    for member in members:
        _keys(member, {'reference', 'pads'}, label='figure member')
        _text(member['reference'], 'figure member reference')
        if not isinstance(member['pads'], dict) or len(member['pads']) < 2:
            raise ValueError(f"{member['reference']}: measure at least two pad centres")
        for point in member['pads'].values():
            _point(point)
    return figure


def _point(value):
    if (not isinstance(value, list) or len(value) != 2
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in value)):
        raise ValueError('figure point must be two numbers')
    return [Fraction(str(v)) for v in value]


def _distance2(a, b):
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)


def _encode(value):
    return [value.numerator, value.denominator]


def verify_scale(figure):
    """Scale from the reference dimension, verified against every check dimension.

    The scale is nanometres per figure unit, squared to stay exact. Each check passes
    when its scaled figure length is within the declared relative tolerance of its
    cited length. Raises ValueError (``scale_unverified``) when any check fails.
    """
    scale = figure['scale']
    reference = scale['reference']
    _independent(scale)
    scale2 = Fraction(reference['length_nm'] ** 2) / _distance2(_point(reference['from']), _point(reference['to']))
    tolerance = Fraction(*scale['tolerance'])
    checks = []
    for record in scale['checks']:
        measured2 = scale2 * _distance2(_point(record['from']), _point(record['to']))
        cited2 = Fraction(record['length_nm'] ** 2)
        low, high = ((1 - tolerance) ** 2) * cited2, ((1 + tolerance) ** 2) * cited2
        ok = low <= measured2 <= high
        checks.append({'basis': record['basis'], 'length_nm': record['length_nm'],
                       'measured_squared_nm2': _encode(measured2), 'status': 'pass' if ok else 'fail'})
    failed = [c['basis'] for c in checks if c['status'] != 'pass']
    if failed:
        raise ValueError('scale_unverified: check dimension outside tolerance: ' + '; '.join(failed))
    return {'method': 'reference-dimension-with-checks/1', 'nm_per_unit_squared': _encode(scale2),
            'tolerance': list(scale['tolerance']), 'checks': checks, 'status': 'scale-verified'}


def _independent(scale):
    """Refuse a check that is the reference dimension again: no repeated segment or basis,
    no check on the reference dimension's own line, and at least one check at 45 degrees
    or more to it, so the other figure axis is verified too (``scale_self_check``)."""
    reference = scale['reference']
    start, end = _point(reference['from']), _point(reference['to'])
    axis = [end[0] - start[0], end[1] - start[1]]
    segments = {frozenset((tuple(start), tuple(end)))}
    bases = {reference['basis']}
    crossing = False
    for record in scale['checks']:
        a, b = _point(record['from']), _point(record['to'])
        segment = frozenset((tuple(a), tuple(b)))
        if segment in segments or record['basis'] in bases:
            raise ValueError(f"scale_self_check: {record['basis']} repeats the reference or another check")
        segments.add(segment)
        bases.add(record['basis'])
        if all(axis[0] * (p[1] - start[1]) - axis[1] * (p[0] - start[0]) == 0 for p in (a, b)):
            raise ValueError(f"scale_self_check: {record['basis']} lies on the reference dimension's line")
        vector = [b[0] - a[0], b[1] - a[1]]
        dot = axis[0] * vector[0] + axis[1] * vector[1]
        crossing |= 2 * dot ** 2 <= (axis[0] ** 2 + axis[1] ** 2) * (vector[0] ** 2 + vector[1] ** 2)
    if not crossing:
        raise ValueError('scale_self_check: no check dimension lies at 45 degrees or more to the reference, '
                         'so the other figure axis is unverified')


def figure_poses(figure, capture):
    """Anchor-relative quarter-turn poses from measured pad centres and each member's own lands.

    Each member's rotation is the quarter turn that maps its footprint-local pad vector
    onto the measured one within the scale tolerance; its origin is the mean of the
    pad-implied origins. A member that fits no quarter turn refuses.
    """
    validate_figure(figure)
    scale = verify_scale(figure)
    scale2 = Fraction(*scale['nm_per_unit_squared'])
    tolerance = Fraction(*figure['scale']['tolerance'])
    flip = -1 if figure['axes'] == 'x-right-y-up' else 1
    board = {fp['ref']: fp for fp in capture['footprints']}
    poses = {}
    for member in figure['members']:
        ref = member['reference']
        if ref not in board:
            raise ValueError(f'{ref}: figure member has no board footprint')
        measured = {}
        for pad, point in member['pads'].items():
            x, y = _point(point)
            # Exact scale is a square root; scale each axis by the nearest exact
            # rational within one part in 10^12, then compare in squared form.
            factor = _root(scale2)
            measured[pad] = [x * factor, flip * y * factor]
        best = None
        for rotation in (0, 90, 180, 270):
            local = {p['number']: p['position_nm'] for p in placer.transform(board[ref], [0, 0], rotation)['pads']
                     if p['number']}
            if set(measured) - set(local):
                raise ValueError(f"{ref}: measured pads {', '.join(sorted(set(measured) - set(local)))} "
                                 'are not footprint pads')
            origins = [[measured[p][0] - local[p][0], measured[p][1] - local[p][1]] for p in sorted(measured)]
            mean = [sum(o[0] for o in origins) / len(origins), sum(o[1] for o in origins) / len(origins)]
            error2 = max(_distance2(o, mean) for o in origins)
            span2 = max(_distance2(local[a], local[b]) for a in measured for b in measured)
            if span2 == 0:
                raise ValueError(f'{ref}: measured pads coincide in the footprint')
            if best is None or error2 < best[0]:
                best = (error2, span2, rotation, mean)
        error2, span2, rotation, mean = best
        if error2 > tolerance ** 2 * span2:
            raise ValueError(f'member_orientation_unresolved: {ref} fits no quarter turn within the scale tolerance')
        poses[ref] = {'position_nm': [round(mean[0]), round(mean[1])], 'rotation_deg': rotation}
    anchor = poses[figure['anchor']]
    turns = anchor['rotation_deg'] // 90
    framed = {}
    for ref, pose in poses.items():
        position, rotation = blocks._frame_pose(pose, anchor['position_nm'], turns)
        framed[ref] = {'position_nm': [int(v) for v in position], 'rotation_deg': rotation}
    return framed


def _root(square):
    """A rational within 10^-12 relative of sqrt(square), by Newton iteration on Fractions."""
    if square <= 0:
        raise ValueError('scale must be positive')
    guess = Fraction(int(float(square) ** 0.5) or 1)
    for _ in range(64):
        nxt = (guess + square / guess) / 2
        nxt = nxt.limit_denominator(10 ** 15)
        if abs(nxt * nxt - square) <= square / 10 ** 24:
            return nxt
        guess = nxt
    return guess


# Materialization -------------------------------------------------------------------

def _unlocked(capture, refs):
    """A capture copy whose block members are unlocked: import reads poses and moves nothing."""
    copied = copy.deepcopy(capture)
    for fp in copied['footprints']:
        if fp['ref'] in refs:
            fp['locked'] = False
    return copied


def _import_policy(policy, refs):
    """The source board policy without fixes on block members; nothing is moved by an import."""
    stripped = copy.deepcopy(policy)
    stripped['fixed_refs'] = [r for r in policy.get('fixed_refs', []) if r not in refs]
    if 'fixed_overhangs' in policy:
        stripped['fixed_overhangs'] = [o for o in policy['fixed_overhangs'] if o.get('reference') not in refs]
    return stripped


def _derivation(capture, inventory, sheet_names, inventory_sha256, block_id, refs, anchor):
    members = sorted(refs, key=blocks._natural)
    allocation = {'schema': blocks.ALLOCATION, 'blocks': [{'id': block_id, 'references': members, 'anchor': anchor}],
                  'residual': sorted((fp['ref'] for fp in capture['footprints'] if fp['ref'] not in refs),
                                     key=blocks._natural)}
    raw = json.dumps(allocation, sort_keys=True).encode()
    return blocks.derive(inventory, sheet_names, capture, inventory_sha256=inventory_sha256, allocation_raw=raw)


def _judgment(capture, framed, gap_nm):
    """The placer's full-polygon judge on the framed members alone, on a plane around them."""
    sides = [b for fp in framed for b in placer.boxes(fp).values()]
    margin = gap_nm + 1_000_000
    x0, y0 = min(b[0] for b in sides) - margin, min(b[1] for b in sides) - margin
    x1, y1 = max(b[2] for b in sides) + margin, max(b[3] for b in sides) + margin
    isolated = {'schema': capture['schema'], 'board_sha256': capture['board_sha256'],
                'outline': {'status': 'proven', 'polygons': [[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]]},
                'footprints': framed}
    result = judge(isolated)
    return {'verdict': result['verdict'], 'basis': 'transformed-import-not-native',
            'hard_failures': result['hard_failures'], 'unknown': result['unknown']}


def _build(capture, inventory, sheet_names, inventory_sha256, block_id, poses, anchor, policy, provenance, *,
           unlock=False):
    """A block candidate of the board's own footprints at the given anchor-relative poses.

    Only a read-only source import unlocks the members (``unlock``); a target board is
    used with its own lock state, so a locked target member refuses in ``blocks.derive``.
    """
    refs = set(poses)
    unlocked = _unlocked(capture, refs) if unlock else capture
    derivation = _derivation(unlocked, inventory, sheet_names, inventory_sha256, block_id, refs, anchor)
    block = next(b for b in derivation['blocks'] if b['id'] == block_id)
    board = {fp['ref']: fp for fp in unlocked['footprints']}
    _, sub, deferred, _ = blocks._isolate(unlocked, policy, block)
    framed = [placer.transform(board[r], poses[r]['position_nm'], poses[r]['rotation_deg'])
              for r in sorted(refs, key=blocks._natural)]
    judgment = _judgment(unlocked, framed, sub['gap_nm'])
    plan = {'search': {'strategy': STRATEGY}, 'steps_used': 0, 'step_budget': sub['step_budget'],
            'judgment': judgment}
    candidate = blocks.candidate(unlocked, inventory, derivation, block, anchor, poses, policy, sub, deferred, plan)
    candidate['provenance'] = provenance
    problems = []
    if judgment['verdict'] != 'pass':
        problems.append({'kind': 'placer_judgment', 'verdict': judgment['verdict'],
                         'hard_failures': judgment['hard_failures'], 'unknown': judgment['unknown']})
    if candidate['witness']['status'] != 'pass':
        problems.append({'kind': 'witness', 'below_gap': candidate['witness']['below_gap'],
                         'drilled_pad_conflicts': candidate['witness']['drilled_pad_conflicts']})
    for name, grade in candidate['grade'].items():
        if grade and grade['status'] != 'pass':
            problems.append({'kind': 'grade', 'evidence': name, 'defects': grade.get('defects'),
                             'unknown': grade.get('unknown')})
    if not problems:
        blocks.validate_candidate(candidate)
    return candidate, problems, derivation


def _candidate_provenance(entry):
    record = entry['provenance']
    return {'class': record['class'], 'enforcement': record['enforcement'], 'citation': record['citation'],
            'method': entry['receipt']['import'], **({'source': record['source']} if 'source' in record else {})}


def materialize(entry, capture, inventory, sheet_names, inventory_sha256, policy, mapping=None, authority_raw=None):
    """Rebuild and re-grade an entry on a board's own footprints, mapped by reference.

    Identity follows ``blocks.match``: footprint, side, land pattern, courtyard, pin
    functions, value rule and the one-to-one net-role partition; net names are not
    identities. A figure entry's poses are re-derived from the figure and the target
    lands. A hard-class entry or an ``authority_raw`` record raises
    ``AuthorityUnavailable``. The candidate is built on the target's own lock state and must be admitted by ``blocks.admit``
    (binding sources, isolated policy, identity and a native re-grade); anything other
    than ``admitted`` refuses with the admission evidence. Returns ``{'status':
    'materialized' | 'refused', 'provenance', 'label', 'problems', 'candidate'}``;
    the candidate is present only when materialized.
    """
    _no_authority(authority_raw=authority_raw)
    validate_entry(entry)
    rows = {r['reference']: r for r in entry['layout']['members']}
    mapping = {r: r for r in rows} if mapping is None else mapping
    if set(mapping) != set(rows) or len(set(mapping.values())) != len(mapping):
        raise ValueError('mapping must pair every entry member with a distinct target reference')
    board = {fp['ref']: fp for fp in capture['footprints']}
    result = {'status': 'refused', 'id': entry['id'], 'version': entry['version'],
              'provenance': entry['provenance']['class'], 'enforcement': entry['provenance']['enforcement'],
              'label': entry['provenance']['label'], 'problems': []}
    problems = result['problems']
    for ref, target in sorted(mapping.items()):
        if target not in board or target not in inventory['components']:
            problems.append({'reference': ref, 'target': target, 'kind': 'target_missing'})
    if problems:
        return result
    for ref in sorted(rows, key=blocks._natural):
        row, target = rows[ref], mapping[ref]
        fp, component = board[target], inventory['components'][target]
        def mismatch(kind, expected, actual):
            problems.append({'reference': ref, 'target': target, 'kind': kind, 'expected': expected, 'actual': actual})
        if fp.get('fpid') != row['fpid']:
            mismatch('fpid', row['fpid'], fp.get('fpid'))
        if fp['side'] != row['side']:
            mismatch('side', row['side'], fp['side'])
        for key, compute in (('pad_geometry_sha256', blocks.pad_geometry_sha256),
                             ('courtyard_sha256', blocks.courtyard_sha256)):
            if compute(fp) != row[key]:
                mismatch(key.removesuffix('_sha256'), row[key], compute(fp))
        pads = sorted(blocks._pad_nets(fp), key=blocks._natural)
        if pads != sorted(row['pins'], key=blocks._natural):
            mismatch('pins', sorted(row['pins'], key=blocks._natural), pads)
        elif pin_functions_sha256(component, pads) != row['pin_functions_sha256']:
            mismatch('pin_function', row['pin_functions_sha256'], pin_functions_sha256(component, pads))
        if not blocks._value_matches(row['value'], component['value']):
            mismatch('value', row['value'], component['value'])
    if problems:
        return result
    anchor = mapping[entry['layout']['anchor']]
    poses = {mapping[r]: copy.deepcopy(rows[r]['pose']) for r in rows}
    if entry['receipt']['import'] == 'reference-figure/1':
        figure = copy.deepcopy(entry['receipt']['figure'])
        for member in figure['members']:
            member['reference'] = mapping[member['reference']]
        figure['anchor'] = mapping[figure['anchor']]
        derived = figure_poses(figure, capture)
        if derived != poses:
            problems.append({'kind': 'figure_pose_mismatch', 'expected': poses, 'actual': derived})
            return result
    try:
        candidate, failed, derivation = _build(capture, inventory, sheet_names, inventory_sha256, entry['id'], poses,
                                               anchor, policy, _candidate_provenance(entry))
    except ValueError as exc:
        problems.append({'kind': 'admission', 'status': 'refused', 'reason': str(exc)})
        return result
    problems.extend(failed)
    if not failed:
        try:
            admission = blocks.admit(candidate, capture, inventory, derivation, policy)
        except ValueError as exc:
            admission = {'status': 'refused', 'problems': [{'kind': 'admission_error', 'reason': str(exc)}]}
        if admission.get('status') != 'admitted':
            problems.append({'kind': 'admission', 'status': admission.get('status'),
                             'problems': admission.get('problems')})
    found = _role_partition(candidate['members'])
    images = {}
    for ref, row in rows.items():
        for pad, role in row['pins'].items():
            have = found[mapping[ref]][pad]
            if (role is None) != (have is None) or (role and role[1] != have[1]):
                problems.append({'reference': ref, 'target': mapping[ref], 'kind': 'net_role', 'pad': pad,
                                 'expected': role, 'actual': have})
            if role is not None and have is not None:
                images.setdefault(('entry', role[0]), set()).add(have[0])
                images.setdefault(('target', have[0]), set()).add(role[0])
    for (side, role), image in sorted(images.items()):
        if len(image) != 1:
            problems.append({'kind': 'net_role_split' if side == 'entry' else 'net_role_merged', 'role': role,
                             'actual': sorted(image)})
    if not problems:
        result.update(status='materialized', candidate=candidate)
    return result


# Imports ---------------------------------------------------------------------------

def _entry(block_id, version, part, provenance, built, copper, receipt):
    candidate, functions = built
    roles = _role_partition(candidate['members'])
    members = [{'reference': r['reference'], 'fpid': r['fpid'], 'side': r['side'], 'value': r['value'],
                'pose': r['pose'], 'pad_geometry_sha256': r['pad_geometry_sha256'],
                'courtyard_sha256': r['courtyard_sha256'], 'pin_functions_sha256': functions[r['reference']],
                'pins': roles[r['reference']]} for r in candidate['members']]
    receipt['candidate_sha256'] = digest(candidate)
    entry = {'schema': ENTRY, 'id': block_id, 'version': version, 'part': part, 'provenance': provenance,
             'layout': {'anchor': candidate['frame']['anchor'], 'members': members}, 'copper': copper,
             'receipt': receipt}
    return validate_entry(entry)


def _import(capture, inventory, sheet_names, inventory_sha256, policy, block_id, poses, anchor, provenance):
    """The source candidate at the imported poses; it must pass its own evidence."""
    source_policy = _import_policy(policy, set(poses))
    candidate, problems, _ = _build(capture, inventory, sheet_names, inventory_sha256, block_id, poses, anchor,
                                    source_policy, provenance, unlock=True)
    if problems:
        raise ValueError('import_refused: the imported layout fails its own evidence: ' + json.dumps(problems))
    functions = {row['reference']: pin_functions_sha256(inventory['components'][row['reference']],
                                                        [p['pad'] for p in row['pins']])
                 for row in candidate['members']}
    inputs = {'board_sha256': capture['board_sha256'], 'inventory_sha256': inventory_sha256,
              'policy_sha256': digest(source_policy)}
    return (candidate, functions), inputs


def reproduce(entry, source_raw, capture, inventory, sheet_names, inventory_sha256, policy, authority_raw=None):
    """Replay an entry's receipt: re-run its import on the source bytes and recorded inputs.

    ``source_raw`` is what the import read: the board bytes, the captured document or
    the cluster-poses file. The rebuilt entry must equal the stored one field for
    field, so poses, copper, labels and the candidate hash are all bound. Returns
    ``{'status': 'reproduced' | 'refused', 'problems': [...]}``. An ``authority_raw``
    record raises ``AuthorityUnavailable``.
    """
    _no_authority(authority_raw=authority_raw)
    validate_entry(entry)
    receipt, source = entry['receipt'], entry['receipt']['source']
    source_policy = _import_policy(policy, {r['reference'] for r in entry['layout']['members']})
    actual = {'board_sha256': capture['board_sha256'], 'inventory_sha256': inventory_sha256,
              'policy_sha256': digest(source_policy)}
    problems = [{'kind': 'source_mismatch', 'input': k, 'expected': receipt['inputs'][k], 'actual': actual[k]}
                for k in INPUT_KEYS if receipt['inputs'][k] != actual[k]]
    if hashlib.sha256(source_raw).hexdigest() != source['sha256']:
        problems.append({'kind': 'source_mismatch', 'input': 'source_sha256', 'expected': source['sha256'],
                         'actual': hashlib.sha256(source_raw).hexdigest()})
    if problems:
        return {'status': 'refused', 'problems': problems}
    common = dict(block_id=entry['id'], version=entry['version'], part=entry['part'],
                  citation=entry['provenance']['citation'])
    kind = receipt['import']
    try:
        if kind == 'board-region/1':
            rebuilt = import_board_region(
                source_raw, capture, inventory, sheet_names, inventory_sha256, policy,
                references=[r['reference'] for r in entry['layout']['members']], anchor=entry['layout']['anchor'],
                provenance_class=entry['provenance']['class'], locator=source['locator'],
                repository=source.get('repository'), commit=source.get('commit'), path=source.get('path'), **common)
        elif kind == 'reference-figure/1':
            rebuilt = import_reference_figure(copy.deepcopy(receipt['figure']), source_raw, capture, inventory,
                                              sheet_names, inventory_sha256, policy, **common)
        else:
            rebuilt = import_project_cluster(source_raw, entry['layout']['anchor'], capture, inventory, sheet_names,
                                             inventory_sha256, policy, repository=source['repository'],
                                             commit=source['commit'], path=source['path'], **common)
    except ValueError as exc:
        return {'status': 'refused', 'problems': [{'kind': 'import_refused', 'reason': str(exc)}]}
    differ = sorted(k for k in set(entry) | set(rebuilt) if entry.get(k) != rebuilt.get(k))
    if differ or entry_bytes(entry) != entry_bytes(rebuilt):
        problems.append({'kind': 'entry_mismatch', 'fields': differ})
    return {'status': 'refused' if problems else 'reproduced', 'problems': problems}


def _with_enforcement(provenance):
    """The candidate provenance record: the entry's class, enforcement, citation, method and source."""
    return {'enforcement': blocks.PROVENANCE[provenance['class']], **provenance}


def _source(locator, sha256, **identity):
    return {'locator': locator, 'sha256': sha256, **{k: v for k, v in identity.items() if v is not None}}


def _label(kind, provenance_class, source, detail=None):
    """What a reviewer sees beside the block, e.g. ``TI SNVSBF0C Fig. 8-22, scale-verified``."""
    if kind == 'reference-figure/1':
        return (f"search-derived (advisory): {detail['document']['locator']} {detail['document']['figure']}, "
                'scale-verified')
    where = (source.get('path') or source['locator']) + (f"@{source['commit'][:12]}" if 'commit' in source else '')
    if kind == 'project-cluster/1':
        return f'search-derived (advisory): project cluster {detail} from {where}'
    if provenance_class == 'search-derived':
        return f'search-derived (advisory): board region from {where}'
    return f'{where}, {provenance_class}'


def board_copper(board_raw, capture, candidate, anchor_pose):
    """Tracks, arcs and vias of the block's nets wholly inside its courtyard extent, framed.

    Coordinates use the block frame; nets are recorded by role, never by name. Zones
    are not captured.
    """
    try:
        root = parse_sexpr(board_raw)
    except LibraryError as exc:
        raise ValueError(f'board is not a KiCad S-expression: {exc}') from None
    if root[0] != 'kicad_pcb':
        raise ValueError('board is not a kicad_pcb file')
    table = {}
    for node in children(root, 'net'):
        atoms = [x for x in node[1:] if isinstance(x, str)]
        if len(atoms) == 2:
            table[atoms[0]] = atoms[1]
    roles = {pin['net']: pin['role'] for row in candidate['members'] for pin in row['pins'] if pin['role']}
    board = {fp['ref']: fp for fp in capture['footprints']}
    placed = [board[r['reference']] for r in candidate['members']]
    sides = [b for fp in placed for b in placer.boxes(fp).values()]
    box = (min(b[0] for b in sides), min(b[1] for b in sides), max(b[2] for b in sides), max(b[3] for b in sides))
    origin, turns = anchor_pose['position_nm'], int(anchor_pose['rotation_deg']) // 90

    def atoms(node, key):
        found = children(node, key)
        return [x for x in found[0][1:] if isinstance(x, str)] if len(found) == 1 else None

    def point(node, key):
        values = atoms(node, key)
        if values is None or len(values) < 2:
            raise ValueError(f'copper item lacks {key}')
        return [nanometres(values[0]), nanometres(values[1])]

    def inside(p):
        return box[0] <= p[0] <= box[2] and box[1] <= p[1] <= box[3]

    def frame(p):
        return [int(v) for v in placer.rotate([p[0] - origin[0], p[1] - origin[1]], -turns)]

    copper = {'tracks': [], 'arcs': [], 'vias': []}
    for key, kind, points in (('segment', 'tracks', ('start', 'end')), ('arc', 'arcs', ('start', 'mid', 'end')),
                              ('via', 'vias', ('at',))):
        for node in children(root, key):
            net = atoms(node, 'net')
            name = None if not net else table.get(net[0], net[0])
            if name not in roles:
                continue
            coords = {p: point(node, p) for p in points}
            if not all(inside(p) for p in coords.values()):
                continue
            item = {p: frame(v) for p, v in coords.items()}
            item['role'] = roles[name]
            if kind == 'vias':
                item.update(size_nm=nanometres(atoms(node, 'size')[0]), drill_nm=nanometres(atoms(node, 'drill')[0]),
                            layers=sorted(atoms(node, 'layers')))
            else:
                item.update(width_nm=nanometres(atoms(node, 'width')[0]), layer=atoms(node, 'layer')[0])
            copper[kind].append(item)
    for kind in copper:
        copper[kind].sort(key=lambda item: json.dumps(item, sort_keys=True))
    return copper


def import_board_region(board_raw, capture, inventory, sheet_names, inventory_sha256, policy, *, references,
                        anchor, block_id, version, part, provenance_class, citation, locator,
                        repository=None, commit=None, path=None, authority_raw=None):
    """Import path (a): a KiCad board region, its relative poses, copper and source hash.

    The region is ``search-derived``; a hard ``provenance_class`` or any
    ``authority_raw`` raises ``AuthorityUnavailable`` before anything is read.
    """
    if provenance_class not in IMPORTS['board-region/1']:
        raise ValueError(f'unknown provenance class {provenance_class}')
    _no_authority(provenance_class, authority_raw)
    sha = hashlib.sha256(board_raw).hexdigest()
    if sha != capture['board_sha256']:
        raise ValueError('board bytes differ from the placement probe board_sha256')
    board = {fp['ref']: fp for fp in capture['footprints']}
    refs = set(_texts(references))
    if anchor not in refs or refs - set(board):
        raise ValueError('references must be board footprints and include the anchor')
    origin = board[anchor]['position_nm']
    turns = _quarter(board[anchor]['rotation_deg'], anchor) // 90
    poses = {}
    for ref in refs:
        rotation = _quarter(board[ref]['rotation_deg'], ref)
        position, rotation = blocks._frame_pose({'position_nm': board[ref]['position_nm'], 'rotation_deg': rotation},
                                                origin, turns)
        poses[ref] = {'position_nm': [int(v) for v in position], 'rotation_deg': rotation}
    source = _source(locator, sha, repository=repository, commit=commit, path=path)
    provenance = {'class': provenance_class, 'citation': citation, 'method': 'board-region/1'}
    built, inputs = _import(capture, inventory, sheet_names, inventory_sha256, policy, block_id, poses, anchor,
                            _with_enforcement(provenance))
    copper = board_copper(board_raw, capture, built[0],
                          {'position_nm': origin, 'rotation_deg': turns * 90})
    entry_provenance = {'class': provenance_class, 'enforcement': blocks.PROVENANCE[provenance_class],
                        'citation': citation, 'label': _label('board-region/1', provenance_class, source)}
    receipt = {'importer': IMPORTER, 'import': 'board-region/1', 'source': source, 'inputs': inputs}
    return _entry(block_id, version, part, entry_provenance, built, copper, receipt)


def import_reference_figure(figure, document_raw, capture, inventory, sheet_names, inventory_sha256, policy, *,
                            block_id, version, part, citation, authority_raw=None):
    """Import path (b): a datasheet reference-layout figure, scale-verified, realized on the board's lands.

    The figure's ``class`` is what the document claims to be; while hard classes are
    disabled the entry is ``search-derived`` and advisory, and any ``authority_raw``
    raises ``AuthorityUnavailable``.
    """
    _no_authority(authority_raw=authority_raw)
    validate_figure(figure)
    sha = hashlib.sha256(document_raw).hexdigest()
    if sha != figure['document']['sha256']:
        raise ValueError('captured document bytes differ from the figure document sha256')
    scale = verify_scale(figure)
    poses = figure_poses(figure, capture)
    document = figure['document']
    source = _source(document['locator'], sha)
    provenance = {'class': 'search-derived', 'citation': citation, 'method': 'reference-figure/1'}
    built, inputs = _import(capture, inventory, sheet_names, inventory_sha256, policy, block_id, poses,
                            figure['anchor'], _with_enforcement(provenance))
    entry_provenance = {'class': 'search-derived', 'enforcement': blocks.PROVENANCE['search-derived'],
                        'citation': citation,
                        'label': _label('reference-figure/1', 'search-derived', source, figure)}
    receipt = {'importer': IMPORTER, 'import': 'reference-figure/1', 'source': source, 'inputs': inputs,
               'figure': copy.deepcopy(figure), 'scale': scale}
    return _entry(block_id, version, part, entry_provenance, built, None, receipt)


def import_project_cluster(cluster_raw, cluster, capture, inventory, sheet_names, inventory_sha256, policy, *,
                           block_id, version, part, citation, repository, commit, path):
    """Import path (c): a project's anchored cluster poses; always search-derived and advisory.

    ``cluster_raw`` maps anchor -> {reference: [dx_nm, dy_nm, rotation_deg]} with the
    anchor at the origin, rotation 0. No caller can raise its class.
    """
    document = strict_json(cluster_raw)
    if not isinstance(document, dict) or cluster not in document or not isinstance(document[cluster], dict):
        raise ValueError(f'cluster poses have no cluster {cluster}')
    poses = {}
    for ref, value in document[cluster].items():
        if (not isinstance(value, list) or len(value) != 3 or any(type(v) is not int for v in value[:2])
                or isinstance(value[2], bool) or not isinstance(value[2], (int, float))):
            raise ValueError(f'{ref}: cluster pose must be [dx_nm, dy_nm, rotation_deg]')
        poses[ref] = {'position_nm': list(value[:2]), 'rotation_deg': _quarter(value[2], ref)}
    if poses.get(cluster) != {'position_nm': [0, 0], 'rotation_deg': 0}:
        raise ValueError(f'cluster {cluster} must hold its anchor at the origin, rotation 0')
    sha = hashlib.sha256(cluster_raw).hexdigest()
    source = _source(f'{repository} {path}', sha, repository=repository, commit=commit, path=path)
    provenance = {'class': 'search-derived', 'citation': citation, 'method': 'project-cluster/1'}
    built, inputs = _import(capture, inventory, sheet_names, inventory_sha256, policy, block_id, poses,
                            cluster, _with_enforcement(provenance))
    entry_provenance = {'class': 'search-derived', 'enforcement': blocks.PROVENANCE['search-derived'],
                        'citation': citation, 'label': _label('project-cluster/1', 'search-derived', source, cluster)}
    receipt = {'importer': IMPORTER, 'import': 'project-cluster/1', 'source': source, 'inputs': inputs}
    return _entry(block_id, version, part, entry_provenance, built, None, receipt)


def _texts(value):
    if isinstance(value, str):
        value = [v for v in value.split(',') if v]
    return blocks._texts(value, 'references')


# Catalogue -------------------------------------------------------------------------

def entry_path(block_id, version):
    return f'blocks/entries/{block_id}/v{version}.json'


def entry_bytes(entry):
    return (json.dumps(entry, indent=1, sort_keys=True) + '\n').encode()


def _catalogue(root):
    path = Path(root) / CATALOGUE_PATH
    document = strict_json(path.read_bytes())
    _keys(document, {'schema', 'entries'}, label='block catalogue')
    if document['schema'] != CATALOGUE or not isinstance(document['entries'], list):
        raise ValueError(f'expected {CATALOGUE} with an entry list')
    return document


def validate_library(root):
    """Every catalogued entry: hash-bound, valid, at its id/version path, versions contiguous.

    Returns the loaded entries in catalogue order; raises ValueError.
    """
    document = _catalogue(root)
    loaded, seen = [], {}
    for record in document['entries']:
        _keys(record, {'id', 'version', 'path', 'sha256'}, label='catalogue entry')
        _id(record['id'])
        _version(record['version'])
        blocks._hex(record['sha256'], 'catalogue sha256')
        if record['path'] != entry_path(record['id'], record['version']):
            expected = entry_path(record['id'], record['version'])
            raise ValueError(f"{record['id']} v{record['version']}: path must be {expected}")
        if seen.get(record['id'], 0) + 1 != record['version']:
            raise ValueError(f"{record['id']}: versions must be catalogued in order from 1")
        seen[record['id']] = record['version']
        raw = (Path(root) / record['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != record['sha256']:
            raise ValueError(f"{record['path']}: bytes differ from the catalogued sha256")
        entry = validate_entry(strict_json(raw))
        if (entry['id'], entry['version']) != (record['id'], record['version']):
            raise ValueError(f"{record['path']}: entry id and version differ from the catalogue")
        if raw != entry_bytes(entry):
            raise ValueError(f"{record['path']}: entry is not in canonical form")
        loaded.append(entry)
    return loaded


def add_entry(root, entry):
    """Append a new version; an existing id/version is immutable and never rewritten."""
    validate_entry(entry)
    entries = validate_library(root)
    latest = max((e['version'] for e in entries if e['id'] == entry['id']), default=0)
    if entry['version'] != latest + 1:
        raise ValueError(f"{entry['id']}: next version is {latest + 1}; catalogued versions are immutable")
    relative = entry_path(entry['id'], entry['version'])
    path = Path(root) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = entry_bytes(entry)
    with path.open('xb') as stream:
        stream.write(raw)
    document = _catalogue(root)
    document['entries'].append({'id': entry['id'], 'version': entry['version'], 'path': relative,
                                'sha256': hashlib.sha256(raw).hexdigest()})
    (Path(root) / CATALOGUE_PATH).write_text(json.dumps(document, indent=1, sort_keys=True) + '\n')
    return relative


def lookup(root, *, part=None, block_id=None, version=None, classes=None):
    """Library entries with their provenance class, strongest authority first.

    Without ``version`` only each id's latest version is returned.
    """
    entries = validate_library(root)
    latest = {}
    for entry in entries:
        latest[entry['id']] = max(latest.get(entry['id'], 0), entry['version'])
    found = []
    for entry in entries:
        if part is not None and entry['part'] != part or block_id is not None and entry['id'] != block_id:
            continue
        if (entry['version'] != version) if version is not None else entry['version'] != latest[entry['id']]:
            continue
        if classes is not None and entry['provenance']['class'] not in classes:
            continue
        found.append({'id': entry['id'], 'version': entry['version'], 'part': entry['part'],
                      'provenance': entry['provenance']['class'], 'enforcement': entry['provenance']['enforcement'],
                      'label': entry['provenance']['label'], 'entry': entry})
    return sorted(found, key=lambda r: (AUTHORITY.index(r['provenance']), r['id'], r['version']))


# CLI -------------------------------------------------------------------------------

def _inputs(args):
    raw = args.netlist.read_bytes()
    return (strict_json(args.probe.read_bytes()), read_native_inventory(args.netlist), read_sheet_names(args.netlist),
            hashlib.sha256(raw).hexdigest(), strict_json(args.policy.read_bytes()))


def _write(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=1, sort_keys=True)
        stream.write('\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest='command', required=True)
    validate = sub.add_parser('validate')
    validate.add_argument('--root', type=Path, default=Path('.'))
    find = sub.add_parser('lookup')
    find.add_argument('--root', type=Path, default=Path('.'))
    find.add_argument('--part')
    find.add_argument('--id')
    find.add_argument('--version', type=int)
    add = sub.add_parser('add')
    add.add_argument('--root', type=Path, default=Path('.'))
    add.add_argument('--entry', required=True, type=Path)
    for name in ('import-board', 'import-figure', 'import-cluster', 'materialize'):
        command = sub.add_parser(name)
        command.add_argument('--probe', required=True, type=Path, help='native placement-probe.json')
        command.add_argument('--netlist', required=True, type=Path, help='captured native XML export')
        command.add_argument('--policy', required=True, type=Path, help='agent-ee.packing-policy/1')
        command.add_argument('--out', required=True, type=Path, help='new output JSON file')
        if name != 'materialize':
            command.add_argument('--id', required=True)
            command.add_argument('--version', required=True, type=int)
            command.add_argument('--part', required=True)
            command.add_argument('--citation', required=True)
    board = sub.choices['import-board']
    board.add_argument('--board', required=True, type=Path)
    board.add_argument('--references', required=True)
    board.add_argument('--anchor', required=True)
    board.add_argument('--class', dest='provenance_class', required=True, choices=sorted(blocks.PROVENANCE))
    board.add_argument('--locator', required=True)
    for key in ('repository', 'commit', 'path'):
        board.add_argument('--' + key)
    for name in ('import-board', 'import-figure', 'materialize'):
        sub.choices[name].add_argument('--authority', type=Path,
                                       help=f'{AUTHORITY_RECORD} record; refused (authority_unavailable) '
                                            'until AGENTEE-348')
    figure = sub.choices['import-figure']
    figure.add_argument('--figure', required=True, type=Path, help=FIGURE)
    figure.add_argument('--document', required=True, type=Path, help='captured datasheet bytes (not committed)')
    cluster = sub.choices['import-cluster']
    cluster.add_argument('--cluster-poses', required=True, type=Path)
    cluster.add_argument('--cluster', required=True)
    for key in ('repository', 'commit', 'path'):
        cluster.add_argument('--' + key, required=True)
    use = sub.choices['materialize']
    use.add_argument('--entry', required=True, type=Path)
    use.add_argument('--mapping', type=Path, help='JSON object: entry reference -> target reference')
    args = parser.parse_args(argv)
    try:
        if args.command == 'validate':
            entries = validate_library(args.root)
            print(json.dumps({'status': 'valid', 'entries': len(entries)}))
            return 0
        if args.command == 'lookup':
            found = lookup(args.root, part=args.part, block_id=args.id, version=args.version)
            print(json.dumps([{k: v for k, v in r.items() if k != 'entry'} for r in found], sort_keys=True))
            return 0 if found else 1
        if args.command == 'add':
            path = add_entry(args.root, strict_json(args.entry.read_bytes()))
            print(json.dumps({'status': 'added', 'path': path}))
            return 0
        capture, inventory, names, sha, policy = _inputs(args)
        authority = args.authority.read_bytes() if getattr(args, 'authority', None) else None
        common = dict(block_id=getattr(args, 'id', None), version=getattr(args, 'version', None),
                      part=getattr(args, 'part', None), citation=getattr(args, 'citation', None))
        if args.command == 'import-board':
            result = import_board_region(args.board.read_bytes(), capture, inventory, names, sha, policy,
                                         references=args.references, anchor=args.anchor,
                                         provenance_class=args.provenance_class, locator=args.locator,
                                         repository=args.repository, commit=args.commit, path=args.path,
                                         authority_raw=authority, **common)
        elif args.command == 'import-figure':
            result = import_reference_figure(strict_json(args.figure.read_bytes()), args.document.read_bytes(),
                                             capture, inventory, names, sha, policy, authority_raw=authority,
                                             **common)
        elif args.command == 'import-cluster':
            result = import_project_cluster(args.cluster_poses.read_bytes(), args.cluster, capture, inventory, names,
                                            sha, policy, repository=args.repository, commit=args.commit,
                                            path=args.path, **common)
        else:
            mapping = strict_json(args.mapping.read_bytes()) if args.mapping else None
            result = materialize(strict_json(args.entry.read_bytes()), capture, inventory, names, sha, policy, mapping,
                                 authority)
        _write(args.out, result)
    except (OSError, ValueError) as exc:
        kind = {'kind': 'authority_unavailable'} if isinstance(exc, AuthorityUnavailable) else {}
        print(json.dumps({'status': 'refused', **kind, 'reason': str(exc)}))
        return 1
    status = result.get('status', 'imported') if args.command == 'materialize' else 'imported'
    print(json.dumps({'status': status, 'out': str(args.out)}))
    return 0 if status in ('imported', 'materialized') else 1


if __name__ == '__main__':
    raise SystemExit(main())
