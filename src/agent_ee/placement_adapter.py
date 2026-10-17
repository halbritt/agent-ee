"""Placement-stage helper dispatch and placement-only write scope (AGENTEE-91).

The coordinator, not the writer, runs the pinned AGENTEE-54 placer once per
placement iteration as an AGENTEE-95 stage step, on a read-only snapshot of the
delivered design and into a fresh scratch destination. A strict comparison of the
complete saved boards admits only footprint translations and quarter turns.
Receipts, the helper's own judgment and its local DRC are provenance only; the
campaign's fresh captured checks grade every proposal.
"""
import argparse
import copy
import hashlib
import json
import re
import subprocess
import uuid
from decimal import Decimal, DecimalException
from pathlib import Path, PurePosixPath

from .storage import digest, identity, read_json, write_json
from .symbol_library import LibraryError, children, parse_sexpr

ADAPTER_SCHEMA = 'agent-ee.placement-adapter/1'
SCOPE_SCHEMA = 'agent-ee.placement-write-scope/2'
RECORD_SCHEMA = 'agent-ee.placement-adapter-record/2'
BINDING_SCHEMA = 'agent-ee.placement-binding/1'
CONTEXT_SCHEMA = 'agent-ee.placement-writer-context/1'
DRIVER_SCHEMA = 'agent-ee.placement-driver/1'
RECORD_KEYS = {'schema', 'implementation', 'source_candidate_id', 'stage', 'declaration_sha256', 'binding_sha256',
               'board', 'protected', 'protected_refs', 'helper', 'writer_scope', 'proposal_scope', 'status',
               'error', 'authority'}
ATTEMPT_STATUSES = ('incomplete', 'refused', 'cancelled')
HELPER_KEYS = {'receipt', 'request_sha256', 'receipt_id', 'status', 'receipt_sha256', 'result_sha256', 'scope'}
HELPERS = ('agent-ee.placer/1',)
STEP = 'agent-ee-placer'
TOOL_AT, REQUEST_AT, ROOT = '/placement-tool', '/placement-request', '/work/placement'
OUTPUT = 'placement/placed/candidate.kicad_pcb'
RESULT = 'run/placement/placement-result.json'
# Children whose saved `at` angle KiCad 10 stores as footprint plus local angle.
ROTATED_CHILDREN = ('pad', 'property', 'fp_text')
# Angles stay exact: saved tokens are scaled to integers without any Decimal context.
# Angles needing more than this many digits before or after the point are refused, never rounded.
MAX_PLACES = 32
FULL_TURN = 360 * 10 ** MAX_PLACES
QUARTER_TURNS = tuple(turn * 10 ** MAX_PLACES for turn in (0, 90, 180, 270))
MAX_TOKEN = 128
# The exact transform family a placement iteration may apply; anything else is refused.
TRANSFORM_FAMILY = 'agent-ee.placement-transforms/2'
TRANSFORMS = ('translations, quarter-turn rotations (0, 90, 180 or 270 degrees) and front/back side '
              'flips as pcbnew saves them (footprints without arcs, custom, chamfered, trapezoid or '
              'offset-drill pads, or vertically justified text)')
HALF_TURN = 180 * 10 ** MAX_PLACES
# Side flips, qualified against KiCad 10.0.7 FOOTPRINT::Flip in both directions from every
# quarter turn: footprint-local points are saved mirrored in y, front/back layers swap, the
# footprint angle is negated plus a quarter turn, and texts gain or lose `justify mirror`.
FLIP_LAYERS = {f'F.{n}': f'B.{n}' for n in ('Cu', 'Adhes', 'Paste', 'SilkS', 'Mask', 'CrtYd', 'Fab')}
FLIP_LAYERS.update({back: front for front, back in list(FLIP_LAYERS.items())})
SIDE_NEUTRAL_LAYERS = ('*.Cu', '*.Mask')
FLIP_GRAPHICS = ('fp_line', 'fp_rect', 'fp_circle', 'fp_poly')
FLIP_TEXTS = ('property', 'fp_text')
POINT_TAGS = ('start', 'end', 'center', 'xy')
FLIP_PAD_SHAPES = ('rect', 'roundrect', 'circle', 'oval')
# pcbnew re-derives arcs on a flip (endpoints swapped, midpoint recomputed), and these pad
# features are not stored as a plain mirror; a flip of a footprint using them is refused.
FLIP_UNSUPPORTED = ('fp_arc', 'offset', 'rect_delta', 'chamfer', 'chamfer_ratio', 'primitives', 'options')
JUSTIFY = ('left', 'right', 'top', 'bottom', 'mirror')
H_SWAP = {'left': 'right', 'right': 'left'}
# Only plain decimal and exponent forms, as pcbnew saves them, are read as numbers. Decimal()
# alone would also accept forms such as `9_0`, ` 90` or `nan` that KiCad's parser reads differently.
NUMBER = re.compile(r'-?[0-9]+(?:\.[0-9]+)?(?:[eE][-+]?[0-9]+)?\Z')
LAUNCHER = '''"""Constant launcher: run the hashed read-only agent_ee snapshot, not /app."""
import sys
sys.path.insert(0, '/placement-tool')
from agent_ee.placement_adapter import sandbox_main
raise SystemExit(sandbox_main(sys.argv[1:]))
'''


def _relative(value, label):
    path = PurePosixPath(value) if isinstance(value, str) and value else None
    if path is None or path.is_absolute() or '..' in path.parts or str(path) != value:
        raise ValueError(f'{label} must be a normalized relative candidate path')
    return value


def validate_declaration(value):
    """Return the normalized campaign declaration; no placement default is implied."""
    keys = {'schema', 'helper', 'policy', 'fixed_footprints', 'timeout_s'}
    if not isinstance(value, dict) or value.get('schema') != ADAPTER_SCHEMA or set(value) != keys:
        raise ValueError(f'expected {ADAPTER_SCHEMA} with exactly {sorted(keys)}')
    helper, policy = value['helper'], value['policy']
    if helper is not None and helper not in HELPERS:
        raise ValueError(f'placement helper must be null or one of {HELPERS}')
    if (helper is None) != (policy is None):
        raise ValueError('a placement helper requires a captured packing policy, and only then')
    if policy is not None:
        _relative(policy, 'placement policy')
    fixed = value['fixed_footprints']
    if not isinstance(fixed, list) or len(set(map(str, fixed))) != len(fixed):
        raise ValueError('fixed_footprints must be a unique list of native footprint UUIDs')
    for item in fixed:
        try:
            if not isinstance(item, str) or str(uuid.UUID(item)) != item:
                raise ValueError
        except ValueError:
            raise ValueError(f'fixed footprint is not a canonical native UUID: {item!r}') from None
    timeout = value['timeout_s']
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 86400:
        raise ValueError('placement timeout_s must be an integer from 1 to 86400')
    return {'schema': ADAPTER_SCHEMA, 'helper': helper, 'policy': policy,
            'fixed_footprints': sorted(fixed), 'timeout_s': timeout}


def is_placement_iteration(state, manifest):
    """Placement-only dispatch: a staged scope whose earliest failing stage is placement."""
    return (manifest['scope'].get('stages') is not None and bool(state['history']) and
            state['history'][-1].get('earliest_failing_stage') == 'placement')


def bind_declaration(declaration, manifest, mutable_inputs):
    """Resolve the declaration against one captured candidate's immutable inputs."""
    declaration = validate_declaration(declaration)
    scope, files = manifest['scope'], manifest['files']
    board, schematic = scope.get('board'), scope.get('schematic')
    if board not in files or board not in mutable_inputs:
        raise ValueError('placement scope requires the captured board to be a mutable design input')
    if declaration['helper'] is not None:
        if schematic not in files:
            raise ValueError('placement helper requires the captured schematic for native netlist export')
        if declaration['policy'] not in files or declaration['policy'] in mutable_inputs:
            raise ValueError('placement policy must be a captured immutable input')
    return {**declaration, 'board': board, 'schematic': schematic}


def envelope_fixed(manifest, inputs, mutable_inputs):
    """Fixed references of every captured envelope that a placement check consumes.

    Read from the captured envelope bytes, never from an authored list, so an incomplete
    fixed_footprints declaration cannot leave an envelope-fixed connector movable."""
    from .board_envelope import validate
    found = {}
    for spec in manifest['scope'].get('checks', []):
        params = spec.get('params') if isinstance(spec.get('params'), dict) else {}
        rel = params.get('envelope') if spec.get('kind') == 'placement' else None
        if rel is None or rel in found:
            continue
        if not isinstance(rel, str) or rel not in manifest['files'] or rel in mutable_inputs:
            raise ValueError(f'placement envelope {rel!r} must be a captured immutable input')
        path = Path(inputs) / rel
        if path.is_symlink() or not path.is_file() or digest(path) != manifest['files'][rel]:
            raise ValueError(f'captured placement envelope {rel!r} changed')
        record = validate(path.read_bytes())
        found[rel] = {'path': rel, 'sha256': manifest['files'][rel], 'envelope_id': record['envelope_id'],
                      'references': sorted(f['reference'] for f in record['fixed_footprints'])}
    return [found[rel] for rel in sorted(found)]


def _policy(inputs, bound, manifest):
    if bound['policy'] is None:
        return {'fixed_refs': []}
    path = Path(inputs) / bound['policy']
    if path.is_symlink() or not path.is_file() or digest(path) != manifest['files'][bound['policy']]:
        raise ValueError('captured placement policy changed')
    policy = read_json(path)
    if (not isinstance(policy, dict) or not isinstance(policy.get('fixed_refs'), list) or
            any(not isinstance(r, str) for r in policy['fixed_refs'])):
        raise ValueError('placement policy must be an agent-ee.packing-policy/1 object with fixed_refs')
    return policy


def binding(declaration, manifest, inputs, mutable_inputs):
    """Coordinator binding of the declaration to one captured candidate's immutable inputs.

    Returns (binding record, bound declaration, policy, protected UUIDs, protected references).
    Every declared UUID, policy reference and envelope-fixed reference must resolve to exactly
    one footprint of the captured source board, or the binding is refused."""
    bound = bind_declaration(declaration, manifest, mutable_inputs)
    inputs = Path(inputs)
    policy = _policy(inputs, bound, manifest)
    envelopes = envelope_fixed(manifest, inputs, mutable_inputs)
    board = inputs / bound['board']
    if board.is_symlink() or not board.is_file() or digest(board) != manifest['files'][bound['board']]:
        raise ValueError('captured placement board changed')
    refs = sorted(set(policy['fixed_refs']) | {r for e in envelopes for r in e['references']})
    protected, protected_refs = protected_footprints(board.read_bytes(), bound['fixed_footprints'], refs)
    record = {'schema': BINDING_SCHEMA, 'declaration_sha256': identity(validate_declaration(declaration)),
              'board': bound['board'], 'schematic': bound['schematic'],
              'policy': None if bound['policy'] is None else {
                  'path': bound['policy'], 'sha256': manifest['files'][bound['policy']]},
              'envelopes': envelopes, 'policy_fixed_refs': sorted(set(policy['fixed_refs']))}
    return record, bound, policy, protected, protected_refs


# Strict structural comparison of complete saved boards.

def _board(data):
    try:
        root = parse_sexpr(data)
    except LibraryError as exc:
        raise ValueError(f'board unparseable: {exc}') from exc
    if root[0] != 'kicad_pcb':
        raise ValueError('board root is not kicad_pcb')
    return root


def _uuid(node):
    found = children(node, 'uuid')
    if len(found) != 1 or len(found[0]) != 2 or not isinstance(found[0][1], str):
        return None
    return found[0][1]


def footprints(root):
    """Footprints by native UUID; refuses missing or duplicate identities."""
    found = {}
    for node in children(root, 'footprint'):
        key = _uuid(node)
        if key is None or key in found:
            raise ValueError('footprint without one unique native UUID')
        found[key] = node
    return found


def _reference(node):
    props = [p for p in children(node, 'property') if len(p) >= 3 and p[1] == 'Reference']
    return props[0][2] if len(props) == 1 and isinstance(props[0][2], str) else None


def _locked(node):
    return 'locked' in node[1:] or ['locked', 'yes'] in node[1:]


def _number(token):
    """Decimal of a bounded plain numeric token, else None; construction is exact."""
    if len(token) > MAX_TOKEN or not NUMBER.match(token):
        return None
    try:
        return Decimal(token)
    except DecimalException:
        return None


def _scaled(number, token):
    """Integer n with number == n / 10**MAX_PLACES exactly; no Decimal context is used."""
    sign, digits, exponent = number.as_tuple()
    coefficient = ''.join(map(str, digits)).rstrip('0')
    if not coefficient:
        return 0
    exponent += len(digits) - len(coefficient)
    if exponent < -MAX_PLACES or len(coefficient) + exponent > MAX_PLACES:
        raise ValueError(f'angle {token} is not exactly representable with at most '
                         f'{MAX_PLACES} digits before and after the point')
    n = int(coefficient) * 10 ** (exponent + MAX_PLACES)
    return -n if sign else n


def _at(node):
    """(x, y, scaled angle, tail) of a direct `at` child; the KiCad angle defaults to zero.

    None when the position is missing or malformed. An angle that cannot be held exactly
    raises ValueError rather than being rounded."""
    found = children(node, 'at')
    if len(found) != 1 or len(found[0]) < 3 or not all(isinstance(t, str) for t in found[0][1:]):
        return None
    tokens = found[0][1:]
    values = [_number(t) for t in tokens[:2]]
    angle, tail = Decimal(0), tokens[2:]
    if tail and _number(tail[0]) is not None:
        angle, tail = _number(tail[0]), tail[1:]
    if not all(v is not None and v.is_finite() for v in (*values, angle)):
        return None
    return values[0], values[1], _scaled(angle, tokens[2] if len(tokens) > 2 else '0'), tuple(tail)


def _turn(angle):
    """Exact scaled angle normalized to [0, FULL_TURN); Python integer modulo never rounds."""
    return angle % FULL_TURN


def _degrees(angle):
    """Exact text of a scaled angle, without exponent or trailing zeros."""
    whole, fraction = divmod(abs(angle), 10 ** MAX_PLACES)
    fraction = str(fraction).rjust(MAX_PLACES, '0').rstrip('0')
    return ('-' if angle < 0 else '') + str(whole) + ('.' + fraction if fraction else '')


def _canonical(value):
    return json.dumps(value, separators=(',', ':'))


def _label(node):
    key = _uuid(node) if isinstance(node, list) else None
    tag = node[0] if isinstance(node, list) and node else repr(node)
    return f'{tag} {key}' if key else str(tag)


def _sequence_violations(before, after, where):
    if before == after:
        return []
    removed, added = list(map(_canonical, before)), list(map(_canonical, after))
    for item in list(removed):
        if item in added:
            removed.remove(item)
            added.remove(item)
    if not removed and not added:
        return [f'{where}: item order changed']
    labels = {_canonical(n): _label(n) for n in (*before, *after)}
    return ([f'{where}: removed or changed {labels[i]}' for i in removed] +
            [f'{where}: added or changed {labels[i]}' for i in added])


def _child_follows(before, after, rotation):
    """Whether a keyed child is unchanged, or a rotated child whose `at` angle advanced exactly
    by the footprint's quarter turn. A malformed or inexact child angle never follows a turn."""
    if before[0] not in ROTATED_CHILDREN or not children(before, 'at'):
        return before == after
    if rotation == 0 and before == after:
        return True
    b_at, a_at = _at(before), _at(after)
    if b_at is None or a_at is None or rotation not in QUARTER_TURNS:
        return False
    rest = [[c for c in node if not (isinstance(c, list) and c and c[0] == 'at')] for node in (before, after)]
    return (rest[0] == rest[1] and b_at[:2] == a_at[:2] and b_at[3] == a_at[3] and
            _turn(a_at[2] - b_at[2] - rotation) == 0)


def _flip_layer(name):
    return FLIP_LAYERS.get(name, name if name in SIDE_NEUTRAL_LAYERS else None)


def _same_number(a, b):
    return isinstance(a, str) and isinstance(b, str) and _number(a) is not None and _number(a) == _number(b)


def _opposite_number(a, b):
    return isinstance(a, str) and isinstance(b, str) and _number(a) is not None and _number(b) is not None and \
        _number(a) == -_number(b)


def _uses(node, tags):
    return isinstance(node, list) and bool(node) and (node[0] in tags or any(_uses(c, tags) for c in node[1:]))


def _mirrored(before, after):
    """Whether after is before with every layer swapped and every point mirrored in y, exactly."""
    if isinstance(before, str) or isinstance(after, str):
        return before == after
    if len(before) != len(after) or not before or before[0] != after[0] or before[0] == 'at':
        return False
    tag = before[0]
    if tag == 'layer':
        return len(before) == 2 and _flip_layer(before[1]) == after[1]
    if tag == 'layers':
        return all(isinstance(b, str) and _flip_layer(b) == a for b, a in zip(before[1:], after[1:]))
    if tag in POINT_TAGS:
        return len(before) == 3 and _same_number(before[1], after[1]) and _opposite_number(before[2], after[2])
    return all(_mirrored(b, a) for b, a in zip(before[1:], after[1:]))


def _mirrored_at(before, after):
    """(before angle, after angle) when the local x/y are mirrored exactly, else None."""
    b_at, a_at = _at(before), _at(after)
    if b_at is None or a_at is None or b_at[3] or a_at[3] or b_at[0] != a_at[0] or b_at[1] != -a_at[1]:
        return None
    return b_at[2], a_at[2]


def _justify(effects):
    found = children(effects, 'justify')
    if not found:
        return set()
    tokens = found[0][1:]
    if len(found) != 1 or any(t not in JUSTIFY for t in tokens) or len(set(tokens)) != len(tokens):
        return None
    return set(tokens)


def _flip_text(before, after, turn):
    angles = _mirrored_at(before, after)
    rest = [[c for c in node if not (isinstance(c, list) and c and c[0] in ('at', 'effects'))]
            for node in (before, after)]
    effects = [children(node, 'effects') for node in (before, after)]
    if angles is None or not _mirrored(*rest) or len(effects[0]) != 1 or len(effects[1]) != 1:
        return False
    plain = [[c for c in e[0][1:] if not (isinstance(c, list) and c and c[0] == 'justify')] for e in effects]
    old, new = _justify(effects[0][0]), _justify(effects[1][0])
    # Vertical justification under a flip is not qualified.
    if plain[0] != plain[1] or old is None or new is None or old & {'top', 'bottom'}:
        return False
    mirrored = old ^ {'mirror'}
    expected = _turn(HALF_TURN - angles[0] + turn)
    # KiCad may also keep the flipped text upright: a further half turn with left/right swapped.
    return ((_turn(angles[1] - expected) == 0 and new == mirrored) or
            (_turn(angles[1] - expected - HALF_TURN) == 0 and new == {H_SWAP.get(t, t) for t in mirrored}))


def _flip_pad(before, after, theta, theta_after):
    if len(before) < 4 or before[1:4] != after[1:4] or before[3] not in FLIP_PAD_SHAPES:
        return False
    angles = _mirrored_at(before, after)
    rest = [[c for c in node if not (isinstance(c, list) and c and c[0] == 'at')] for node in (before, after)]
    # The saved pad angle is footprint plus local angle; a flip negates the local angle.
    return (angles is not None and _mirrored(*rest) and
            _turn(angles[1] - theta_after + angles[0] - theta) == 0)


def _flip_unsupported(before, after):
    """Whether a keyed child lies outside the qualified side-flip family."""
    if before[0] not in (*FLIP_GRAPHICS, *FLIP_TEXTS, 'pad') or any(_uses(n, FLIP_UNSUPPORTED) for n in (before, after)):
        return True
    return before[0] in FLIP_TEXTS and any((_justify(e) or set()) & {'top', 'bottom'}
                                            for e in children(before, 'effects'))


def _flip_follows(before, after, theta, theta_after):
    """Whether a keyed child is exactly the side-flipped form of before."""
    if _flip_unsupported(before, after):
        return False
    if before[0] in FLIP_GRAPHICS:
        return _mirrored(before, after)
    if before[0] in FLIP_TEXTS:
        return _flip_text(before, after, _turn(theta_after + theta))
    if before[0] == 'pad':
        return _flip_pad(before, after, theta, theta_after)
    return False


def _footprint(key, before, after, protected, violations):
    label = f'footprint {key} ({_reference(before) or "no reference"})'
    count = len(violations)
    if [t for t in before[1:] if isinstance(t, str)] != [t for t in after[1:] if isinstance(t, str)]:
        violations.append(f'{label}: library identity or header changed')
    sides = children(before, 'layer'), children(after, 'layer')
    flipped = sides[0] != sides[1]
    if flipped and sorted(map(_canonical, sides)) != [_canonical([['layer', 'B.Cu']]), _canonical([['layer', 'F.Cu']])]:
        violations.append(f'{label}: layer changed other than a front/back side flip')
    try:
        b_at, a_at = _at(before), _at(after)
    except ValueError as exc:
        violations.append(f'{label}: footprint {exc}')
        return None
    if b_at is None or a_at is None:
        violations.append(f'{label}: missing or malformed footprint position')
        return None
    # A flip negates the saved footprint angle; what remains must be a quarter turn.
    rotation = _turn(a_at[2] + b_at[2]) if flipped else _turn(a_at[2] - b_at[2])
    moved = b_at[:2] != a_at[:2] or rotation != 0 or flipped
    if b_at[3] != a_at[3]:
        violations.append(f'{label}: position tokens changed')
    if key in protected and (flipped or children(before, 'at') != children(after, 'at')):
        violations.append(f'{label}: protected footprint transform changed')
    if rotation not in QUARTER_TURNS:
        violations.append(f'{label}: rotation delta {_degrees(rotation)}{" after the side flip" if flipped else ""} '
                          'is not a quarter turn')
    skip = ('at', 'layer') if flipped else ('at',)
    unkeyed = [[c for c in node[1:] if isinstance(c, list) and c and c[0] not in skip and _uuid(c) is None]
               for node in (before, after)]
    violations += _sequence_violations(*unkeyed, label)
    keyed = []
    for node in (before, after):
        items = {}
        for child in node[1:]:
            if isinstance(child, list) and child and _uuid(child) is not None:
                if (child[0], _uuid(child)) in items:
                    violations.append(f'{label}: duplicate child identity {_label(child)}')
                items[child[0], _uuid(child)] = child
        keyed.append(items)
    for item in sorted(keyed[0].keys() ^ keyed[1].keys()):
        violations.append(f'{label}: {"removed" if item in keyed[0] else "added"} {item[0]} {item[1]}')
    for item in sorted(keyed[0].keys() & keyed[1].keys()):
        b_child, a_child = keyed[0][item], keyed[1][item]
        try:
            if (_flip_follows(b_child, a_child, b_at[2], a_at[2]) if flipped else
                    _child_follows(b_child, a_child, rotation)):
                continue
        except ValueError as exc:
            violations.append(f'{label}: {item[0]} {item[1]} {exc}')
            continue
        if flipped and _flip_unsupported(b_child, a_child):
            violations.append(f'{label}: {item[0]} {item[1]} is not supported under a side flip')
        else:
            violations.append(f'{label}: {item[0]} {item[1]} changed beyond the footprint transform')
    if len(violations) != count or not moved:
        return None
    return {'uuid': key, 'reference': _reference(before), 'rotation_deg': rotation // 10 ** MAX_PLACES,
            'flipped': flipped, 'side': 'back' if sides[1] == [['layer', 'B.Cu']] else 'front',
            'from': [str(b_at[0]), str(b_at[1]), _degrees(b_at[2])],
            'to': [str(a_at[0]), str(a_at[1]), _degrees(a_at[2])]}


def write_scope(before, after, protected=()):
    """Compare complete saved boards; only movable footprint transforms are allowed."""
    protected = sorted(set(protected))
    report = {'schema': SCOPE_SCHEMA, 'family': TRANSFORM_FAMILY, 'before_sha256': hashlib.sha256(before).hexdigest(),
              'after_sha256': hashlib.sha256(after).hexdigest(), 'protected': protected,
              'transforms': [], 'violations': []}
    violations = report['violations']
    try:
        roots = _board(before), _board(after)
        prints = footprints(roots[0]), footprints(roots[1])
    except ValueError as exc:
        violations.append(str(exc))
    else:
        others = [[n for n in root[1:] if not (isinstance(n, list) and n and n[0] == 'footprint')]
                  for root in roots]
        violations += _sequence_violations(*others, 'board')
        violations += [f'protected footprint {key} absent from source board'
                       for key in protected if key not in prints[0]]
        violations += [f'footprint {key} removed' for key in sorted(prints[0].keys() - prints[1].keys())]
        violations += [f'footprint {key} added' for key in sorted(prints[1].keys() - prints[0].keys())]
        for key in sorted(prints[0].keys() & prints[1].keys()):
            moved = _footprint(key, prints[0][key], prints[1][key], protected, violations)
            if moved:
                report['transforms'].append(moved)
    report['status'] = 'refused' if violations else 'allowed'
    return report


def protected_footprints(board, fixed_footprints, fixed_refs=()):
    """Envelope-fixed UUIDs, policy-fixed references and locked parts on the source board."""
    prints = footprints(_board(board))
    by_ref = {}
    for key, node in prints.items():
        by_ref.setdefault(_reference(node), []).append(key)
    missing = sorted(set(fixed_footprints) - prints.keys())
    if missing:
        raise ValueError(f'envelope-fixed footprints absent from source board: {missing}')
    uuids = set(fixed_footprints) | {k for k, node in prints.items() if _locked(node)}
    for ref in fixed_refs:
        if len(by_ref.get(ref, [])) != 1:
            raise ValueError(f'fixed reference {ref!r} does not name exactly one source footprint')
        uuids.update(by_ref[ref])
    refs = [_reference(prints[k]) for k in uuids]
    if None in refs or len(set(refs)) != len(refs):
        raise ValueError('protected footprints need unique native references')
    return sorted(uuids), sorted(refs)


def _files(root):
    files = {}
    for path in sorted(Path(root).rglob('*')):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError(f'design contains a link or special file: {path.relative_to(root)}')
        if path.is_file():
            files[path.relative_to(root).as_posix()] = digest(path)
    return files


def proposal_scope(source_inputs, design, board, protected):
    """Placement-iteration write scope of a delivered design against its source candidate."""
    source_inputs, design = Path(source_inputs), Path(design)
    before, after = _files(source_inputs), _files(design)
    violations = [f'non-board file {"removed" if rel in before else "added"}: {rel}'
                  for rel in sorted(before.keys() ^ after.keys())]
    violations += [f'non-board file changed: {rel}' for rel in sorted(before.keys() & after.keys())
                   if rel != board and before[rel] != after[rel]]
    if board not in before or board not in after:
        violations.append(f'board missing: {board}')
        report = {'schema': SCOPE_SCHEMA, 'family': TRANSFORM_FAMILY, 'status': 'refused', 'protected': sorted(protected),
                  'transforms': [], 'violations': []}
    else:
        report = write_scope((source_inputs / board).read_bytes(), (design / board).read_bytes(), protected)
    report['violations'] = violations + report['violations']
    report.update(board=board, status='refused' if report['violations'] else 'allowed')
    return report


# Coordinator-run helper step (AGENTEE-95 stage receipt).

def helper_request(board, policy, protected_refs):
    """Coordinator-derived placer request bound to the exact delivered board bytes."""
    policy = copy.deepcopy(policy)
    if not isinstance(policy, dict) or not isinstance(policy.get('fixed_refs'), list):
        raise ValueError('placement policy must be an agent-ee.packing-policy/1 object')
    policy['fixed_refs'] += [r for r in protected_refs if r not in policy['fixed_refs']]
    return {'policy': policy, 'authorization': {
        'schema': 'agent-ee.placement-write/1', 'single_design_writer': True,
        'source_sha256': hashlib.sha256(board).hexdigest(), 'writable_root': ROOT}}


def tool_snapshot(destination):
    """Copy the agent_ee sources into a coordinator-owned tree the step mounts read-only."""
    destination = Path(destination)
    package = destination / 'agent_ee'
    package.mkdir(parents=True)
    for source in sorted(Path(__file__).resolve().parent.glob('*.py')):
        package.joinpath(source.name).write_bytes(source.read_bytes())
    (destination / 'launch.py').write_text(LAUNCHER)
    return destination


def step_declaration(step_dir, bound):
    """The one placement stage-step declaration for a coordinator step directory."""
    step_dir = Path(step_dir)
    return {'schema': 'agent-ee.stage-step/1', 'stage': 'placement', 'step': STEP,
            'argv': ['/usr/bin/python3', '-B', '-I', f'{TOOL_AT}/launch.py',
                     f"/candidate/{bound['board']}", f"/candidate/{bound['schematic']}",
                     '--request', f'{REQUEST_AT}/request.json', '--root', ROOT],
            'mounts': [{'source': str(step_dir / 'tool'), 'at': TOOL_AT},
                       {'source': str(step_dir / 'request'), 'at': REQUEST_AT}],
            'version_argv': ['/usr/bin/kicad-cli', 'version'], 'source': TOOL_AT,
            'writes': {bound['board']: OUTPUT}, 'artifacts': ['placement'],
            'adoptable_exits': [0], 'timeout_s': bound['timeout_s']}


def prepare_step(step_dir, board, policy, bound, protected_refs):
    """Write the tool snapshot and request for the delivered board bytes; return the step."""
    request = helper_request(board, policy, protected_refs)
    tool_snapshot(Path(step_dir) / 'tool')
    write_json(Path(step_dir) / 'request' / 'request.json', request)
    return step_declaration(step_dir, bound), request


def qualify_receipt(receipt_dir, receipt, board, protected):
    """Refuse a helper outcome unless its exact retained proposal is complete and in scope."""
    if receipt['status'] != 'completed':
        raise ValueError(f"placement helper {receipt['status']} (exit {receipt['exit_code']}); "
                         'iteration incomplete, no default placement substituted')
    receipt_dir = Path(receipt_dir)
    result_path = receipt_dir / RESULT
    try:
        result = read_json(result_path)
    except (OSError, ValueError) as exc:
        raise ValueError(f'placement helper result missing or malformed: {exc}') from exc
    if (not isinstance(result, dict) or result.get('status') != 'placed' or
            result.get('authority') != 'disposable-unsealed-proposal' or
            result.get('source_sha256') != receipt['inputs'].get(board) or
            result.get('board_sha256') != receipt['outputs'].get(board) or not set(receipt['changed']) <= {board}):
        raise ValueError('placement helper result does not bind the receipt input and output board')
    scope = write_scope((receipt_dir / 'inputs' / board).read_bytes(),
                        (receipt_dir / 'run' / OUTPUT).read_bytes(), protected)
    if scope['status'] != 'allowed':
        raise ValueError(f"placement helper proposal outside placement scope: {scope['violations']}")
    return {'result_sha256': digest(result_path), 'scope': scope}


def _record(work, manifest, declaration, binding_record, bound, protected, refs):
    return {'schema': RECORD_SCHEMA, 'implementation': int(Path(work).name),
            'source_candidate_id': manifest['candidate_id'], 'stage': 'placement',
            'declaration_sha256': identity(validate_declaration(declaration)),
            'binding_sha256': identity(binding_record), 'board': bound['board'], 'protected': protected,
            'protected_refs': refs, 'helper': None, 'writer_scope': None, 'proposal_scope': None,
            'status': 'incomplete', 'error': None, 'authority': 'provenance-only'}


def _receipt_name(index):
    if isinstance(index, bool) or not isinstance(index, int) or index < 1:
        raise ValueError('placement receipt index must be a positive integer')
    return f'receipts/{index}-{STEP}'


def run_placement_iteration(work, design, source_inputs, manifest, state, declaration, deadline, *,
                            receipts=None, cancellation=None, receipt_index=1):
    """Guard the writer's proposal, run the declared helper once, adopt only a qualified proposal,
    then prove the original-to-final write scope.

    Called by the campaign under its existing writer lease, after the writer session and
    the existing inventory checks. The writer's own delivered board is held to the placement
    scope before any helper runs, so a helper cannot overwrite and conceal an illegal edit.
    `receipt_index` comes from the campaign's one step-plan allocator. Any refusal, failure,
    timeout or cancellation raises and leaves the iteration incomplete; the retained record
    keeps the original exception, and a failure to write the record never replaces it.
    """
    work, design, source_inputs = Path(work), Path(design), Path(source_inputs)
    if not is_placement_iteration(state, manifest):
        raise ValueError('placement adapter runs only when the earliest failing stage is placement')
    binding_record, bound, policy, protected, refs = binding(declaration, manifest, source_inputs,
                                                             state['mutable_inputs'])
    board = bound['board']
    record = _record(work, manifest, declaration, binding_record, bound, protected, refs)
    ran = False
    failure = None
    try:
        record['writer_scope'] = proposal_scope(source_inputs, design, board, protected)
        if record['writer_scope']['status'] != 'allowed':
            record['status'] = 'refused'
            raise ValueError('placement writer proposal changed more than footprint transforms: '
                             f"{record['writer_scope']['violations']}")
        if bound['helper'] is not None:
            if receipts is None:
                from . import stage_receipts as receipts
            if 'stage_receipts' not in state:
                raise ValueError('campaign predates stage receipts; start a fresh campaign to run placement helpers')
            name = _receipt_name(receipt_index)
            step_dir = work / 'placement' / str(receipt_index)
            spec, request = prepare_step(step_dir, (design / board).read_bytes(), policy, bound, refs)
            receipt_dir = work / name
            record['helper'] = {'receipt': name, 'request_sha256': identity(request), 'status': None}
            ran = True
            receipt = receipts.run_step(receipt_dir, design, spec, {
                'implementation': record['implementation'], 'source_candidate_id': manifest['candidate_id'],
                'receipt_index': receipt_index}, deadline=deadline, cancellation=cancellation)
            record['helper'].update(receipt_id=receipt['receipt_id'], status=receipt['status'],
                                    receipt_sha256=digest(receipt_dir / 'receipt.json'))
            record['helper'].update(qualify_receipt(receipt_dir, receipt, board, protected))
            receipts.adopt(receipt_dir, receipt, design)
        scope = proposal_scope(source_inputs, design, board, protected)
        record['proposal_scope'] = scope
        if scope['status'] != 'allowed':
            record['status'] = 'refused'
            raise ValueError(f"placement iteration changed more than footprint transforms: {scope['violations']}")
        record['status'] = 'proposed'
    except KeyboardInterrupt as exc:
        failure = exc
        record.update(status='cancelled', error='KeyboardInterrupt')
        raise
    except (ValueError, OSError, KeyError, TypeError) as exc:
        failure = exc
        record['error'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        if ran and record['helper'].get('receipt_id') is None:
            # The step raised (for example on cancellation) after retaining its receipt.
            _retained_helper(record['helper'], work, receipts)
        try:
            write_json(work / 'placement-adapter.json', record)
        except OSError as exc:
            if failure is None:
                raise
            # The original failure stays the reported cause; the missing record is verified as absent.
            failure.add_note(f'placement adapter record not retained: {type(exc).__name__}: {exc}')
        if ran:
            state['stage_receipts'][work.name] = receipts.bind_implementation(
                work, design, record['implementation'], manifest['candidate_id'])
    return record


def _retained_helper(helper, work, receipts):
    receipt_dir = Path(work) / helper['receipt']
    if receipt_dir.is_symlink() or not (receipt_dir / 'receipt.json').is_file():
        return
    try:
        receipt = receipts.validate_receipt(receipt_dir)
    except (ValueError, OSError):
        return
    helper.update(receipt_id=receipt['receipt_id'], status=receipt['status'],
                  receipt_sha256=digest(receipt_dir / 'receipt.json'))


def _stored(work, kind):
    path = Path(work) / 'placement-adapter.json'
    if path.is_symlink() or not path.is_file():
        raise ValueError('placement adapter record missing')
    stored = read_json(path)
    if (not isinstance(stored, dict) or set(stored) != RECORD_KEYS or stored['schema'] != RECORD_SCHEMA or
            stored['authority'] != 'provenance-only'):
        raise ValueError(f'placement record is not {kind}')
    return stored


def _helper_receipt(work, bound, manifest, receipts, receipt_index):
    """Validated coordinator receipt, its step directory and the coordinator-derived step."""
    name = _receipt_name(receipt_index)
    receipt_dir = Path(work) / name
    if receipt_dir.is_symlink() or not (receipt_dir / 'receipt.json').is_file():
        raise ValueError('placement helper receipt missing')
    receipt = receipts.validate_receipt(receipt_dir, binding={
        'implementation': int(Path(work).name), 'source_candidate_id': manifest['candidate_id'],
        'receipt_index': receipt_index})
    step_dir = Path(work) / 'placement' / str(receipt_index)
    if receipt['spec'] != receipts.validate_step(step_declaration(step_dir, bound), host=False):
        raise ValueError('placement receipt step differs from the placement declaration')
    return name, receipt_dir, receipt, step_dir


def verify_record(work, source_inputs, proposal_inputs, manifest, declaration, mutable_inputs, *, receipts=None,
                  receipt_index=1):
    """Recompute a proposed placement record from retained receipts and the captured proposal.

    Every field is re-derived; none is copied from the stored record. The captured proposal
    must be inside the placement write scope now, whatever the record claims, and so must the
    writer's own delivered design that the helper started from.
    """
    work, source_inputs = Path(work), Path(source_inputs)
    stored = _stored(work, 'a proposed placement iteration')
    if stored['status'] != 'proposed' or stored['error'] is not None:
        raise ValueError('placement record is not a proposed placement iteration')
    binding_record, bound, policy, protected, refs = binding(declaration, manifest, source_inputs, mutable_inputs)
    scope = proposal_scope(source_inputs, proposal_inputs, bound['board'], protected)
    if scope['status'] != 'allowed' or scope['violations']:
        raise ValueError(f"captured proposal is outside the placement write scope: {scope['violations']}")
    expected = {**_record(work, manifest, declaration, binding_record, bound, protected, refs),
                'writer_scope': scope, 'proposal_scope': scope, 'status': 'proposed'}
    if bound['helper'] is not None:
        if receipts is None:
            from . import stage_receipts as receipts
        helper = stored['helper']
        if not isinstance(helper, dict) or set(helper) != HELPER_KEYS:
            raise ValueError('placement record lacks the declared helper receipt')
        if helper['receipt'] != _receipt_name(receipt_index):
            raise ValueError('placement record names no coordinator placement receipt')
        name, receipt_dir, receipt, step_dir = _helper_receipt(work, bound, manifest, receipts, receipt_index)
        # The writer's delivered design is the receipt's retained input copy.
        expected['writer_scope'] = proposal_scope(source_inputs, receipt_dir / 'inputs', bound['board'], protected)
        if expected['writer_scope']['status'] != 'allowed':
            raise ValueError('placement writer proposal before the helper is outside the placement write scope: '
                             f"{expected['writer_scope']['violations']}")
        # The request is re-derived from the receipt's retained input board, never trusted.
        request = helper_request((receipt_dir / 'inputs' / bound['board']).read_bytes(), policy, refs)
        if read_json(step_dir / 'request' / 'request.json') != request:
            raise ValueError('placement helper request differs from the coordinator derivation')
        # The captured proposal is exactly the adopted helper output over the delivered design.
        if {**receipt['inputs'], **receipt['outputs']} != _files(proposal_inputs):
            raise ValueError('captured proposal differs from the adopted placement helper output')
        expected['helper'] = {'receipt': name, 'request_sha256': identity(request),
                              'receipt_id': receipt['receipt_id'], 'status': receipt['status'],
                              'receipt_sha256': digest(receipt_dir / 'receipt.json'),
                              **qualify_receipt(receipt_dir, receipt, bound['board'], protected)}
    if expected != stored:
        raise ValueError('placement record differs from retained receipts or captured proposal')
    return stored


def verify_attempt(work, source_inputs, design, manifest, declaration, mutable_inputs, *, receipts=None,
                   receipt_index=1):
    """Verify the retained record of an incomplete placement iteration.

    Asserts nothing about adoption or completion. The bindings, protected set, any helper
    receipt and every scope proof are recomputed from retained coordinator records and the
    unchanged scratch design; a forged or relabelled outcome is refused.
    """
    work, source_inputs, design = Path(work), Path(source_inputs), Path(design)
    stored = _stored(work, 'an incomplete placement attempt')
    if (stored['status'] not in ATTEMPT_STATUSES or not isinstance(stored['error'], str) or
            not stored['error'] or (stored['status'] == 'cancelled') != (stored['error'] == 'KeyboardInterrupt')):
        raise ValueError('placement record is not an incomplete placement attempt')
    binding_record, bound, policy, protected, refs = binding(declaration, manifest, source_inputs, mutable_inputs)
    expected = {**_record(work, manifest, declaration, binding_record, bound, protected, refs),
                'status': stored['status'], 'error': stored['error']}
    helper = stored['helper']
    receipt = None
    if helper is not None:
        if bound['helper'] is None or not isinstance(helper, dict):
            raise ValueError('placement attempt names an undeclared helper')
        if receipts is None:
            from . import stage_receipts as receipts
        name = _receipt_name(receipt_index)
        receipt_dir = work / name
        step_dir = work / 'placement' / str(receipt_index)
        if (receipt_dir / 'receipt.json').exists():
            name, receipt_dir, receipt, step_dir = _helper_receipt(work, bound, manifest, receipts, receipt_index)
            delivered = receipt_dir / 'inputs'
        elif receipt_dir.exists() or receipt_dir.is_symlink():
            raise ValueError('placement attempt receipt directory without a coordinator receipt')
        else:
            # The step never launched (for example an exhausted deadline); nothing was adopted.
            delivered = design
        request = helper_request((delivered / bound['board']).read_bytes(), policy, refs)
        if read_json(step_dir / 'request' / 'request.json') != request:
            raise ValueError('placement helper request differs from the coordinator derivation')
        expected['helper'] = {'receipt': name, 'request_sha256': identity(request), 'status': None}
        if receipt is not None:
            expected['helper'].update(receipt_id=receipt['receipt_id'], status=receipt['status'],
                                      receipt_sha256=digest(receipt_dir / 'receipt.json'))
            try:
                expected['helper'].update(qualify_receipt(receipt_dir, receipt, bound['board'], protected))
            except ValueError:
                pass
        expected['writer_scope'] = proposal_scope(source_inputs, delivered, bound['board'], protected)
    elif stored['writer_scope'] is not None:
        expected['writer_scope'] = proposal_scope(source_inputs, design, bound['board'], protected)
    if stored['proposal_scope'] is not None:
        expected['proposal_scope'] = proposal_scope(source_inputs, design, bound['board'], protected)
    if stored['status'] == 'refused' and not any(
            s is not None and s['status'] == 'refused' for s in (expected['writer_scope'], expected['proposal_scope'])):
        raise ValueError('refused placement attempt has no refused scope proof')
    if expected['proposal_scope'] is not None and expected['proposal_scope']['status'] == 'allowed':
        raise ValueError('placement attempt with an allowed final scope is not incomplete')
    if expected != stored:
        raise ValueError('placement attempt record differs from retained receipts or scratch design')
    return stored


def writer_context(run, manifest):
    """Sealed placement-stage facts for the writer prompt; context, never evidence of a pass."""
    run = Path(run)
    result = read_json(run / 'result.json')
    stages = {c['id']: c.get('stage') for c in manifest['scope']['checks']}
    hashes = result.get('artifact_hashes', {})
    checks = []
    for check in result['checks']:
        if stages.get(check['id']) != 'placement':
            continue
        entry = {k: check.get(k) for k in ('id', 'kind', 'execution', 'outcome', 'summary', 'details', 'evidence')}
        probe = f"work/{check['id']}/placement-probe.json"
        # Only a retained artifact matching the sealed result hash is shown as geometry.
        if probe in hashes and (run / probe).is_file() and digest(run / probe) == hashes[probe]:
            entry['probe'] = read_json(run / probe)
        checks.append(entry)
    ids = {c['id'] for c in checks}
    # A collected check finding carries id `check/<check id>` and local_id `<check id>`.
    findings = [f for f in result.get('findings', []) if f.get('source') == 'check' and
                f.get('status') == 'accepted' and f.get('local_id') in ids and
                f.get('id') == f"check/{f['local_id']}"]
    return {'schema': CONTEXT_SCHEMA, 'stage': 'placement', 'checks': checks, 'findings': findings,
            'routing_feedback': {'status': 'unavailable',
                                 'reason': 'no captured AGENTEE-93 routed-attempt receipt is integrated'},
            'authority': 'context-only'}


# In-sandbox driver: native netlist export, then the unchanged AGENTEE-54 writer.

def sandbox_main(argv=None):
    parser = argparse.ArgumentParser(description='placement helper step (sandbox side)')
    parser.add_argument('board')
    parser.add_argument('schematic')
    parser.add_argument('--request', required=True)
    parser.add_argument('--root', required=True)
    args = parser.parse_args(argv)
    root = Path(args.root)
    root.mkdir()
    netlist = root / 'netlist.xml'
    command = ['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml', '-o', str(netlist), args.schematic]
    record = {'schema': DRIVER_SCHEMA, 'netlist_export': {'argv': command, 'exit_code': None},
              'status': 'running', 'error': None}
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
        (root / 'netlist-export.stdout.txt').write_text(completed.stdout)
        (root / 'netlist-export.stderr.txt').write_text(completed.stderr)
        record['netlist_export']['exit_code'] = completed.returncode
        if completed.returncode != 0 or not netlist.is_file():
            record['status'] = 'netlist_export_failed'
            return 3
        from .placer_runner import main
        code = main([args.board, str(netlist), '--request', args.request,
                     '--destination', str(root / 'placed'), '--result', str(root / 'placement-result.json')])
        record['status'] = 'placed' if code == 0 else 'not_placed'
        return code
    except BaseException as exc:
        record.update(status='error', error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        write_json(root / 'driver.json', record)
