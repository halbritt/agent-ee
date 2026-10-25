"""Receipted silk repair on an already routed board: translation-only, or exact requested field poses."""
import argparse
import copy
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re
import shutil

from .symbol_library import children, parse_sexpr, nanometres
from .storage import digest, read_json, write_json

STEP = 'agent-ee-routed-artwork'
TOOL_AT = '/artwork-tool'
# Reuse the placement evidence envelope and its immutable carry capture.
from .placement_defects import REPORT, BOARD, ARTWORK
SOURCE = 'placement/placed/baseline/candidate.kicad_pcb'
LAUNCHER = '''import sys
sys.path.insert(0, '/artwork-tool')
from agent_ee.routed_artwork import main
raise SystemExit(main())
'''
GRAPHICS = frozenset(f'{prefix}_{kind}' for prefix in ('fp', 'gr')
                     for kind in ('line', 'arc', 'rect', 'circle', 'poly', 'text'))
ARTWORK_POLICY = 'agent-ee.artwork-policy/1'


def normalize_artwork_policy(policy):
    """Explicit free-group classification; every unlisted graphic stays anchored."""
    if policy is None:
        return None
    if (not isinstance(policy, dict) or set(policy) != {'schema', 'source_sha256', 'free_groups'} or
            policy['schema'] != ARTWORK_POLICY or
            not isinstance(policy['source_sha256'], str) or
            re.fullmatch('[0-9a-f]{64}', policy['source_sha256']) is None or
            not isinstance(policy['free_groups'], list) or len(policy['free_groups']) > 256):
        raise ValueError('malformed artwork policy')
    groups, seen = [], set()
    def identifier(value):
        return isinstance(value, str) and 0 < len(value) <= 128 and not any(c.isspace() for c in value)
    for group in policy['free_groups']:
        if (not isinstance(group, dict) or set(group) != {'owner_uuid', 'members'} or
                (group['owner_uuid'] is not None and not identifier(group['owner_uuid'])) or
                not isinstance(group['members'], list) or not 0 < len(group['members']) <= 1024 or
                any(not identifier(uid) or uid in seen for uid in group['members']) or
                len(set(group['members'])) != len(group['members'])):
            raise ValueError('ambiguous artwork group membership')
        seen.update(group['members'])
        groups.append(dict(owner_uuid=group['owner_uuid'], members=sorted(group['members'])))
    return dict(schema=ARTWORK_POLICY, source_sha256=policy['source_sha256'],
                free_groups=sorted(groups, key=lambda g: (g['owner_uuid'] or '', g['members'])))


def canonical_artwork_policy(policy):
    policy = normalize_artwork_policy(policy)
    if policy is None:
        raise ValueError('artwork policy cannot be null')
    return json.dumps(policy, sort_keys=True, separators=(',', ':'))


def decode_artwork_policy(text):
    try:
        value = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise ValueError('malformed artwork policy JSON') from exc
    policy = normalize_artwork_policy(value)
    if policy is None:
        raise ValueError('artwork policy cannot be null')
    return policy


def _artwork_inventory(root):
    result = {}
    def walk(node, owner=None):
        if node[0] == 'footprint':
            uid = children(node, 'uuid')
            owner = uid[0][1] if uid else None
        uid = children(node, 'uuid')
        if uid:
            result[uid[0][1]] = (owner, node)
        for child in node[1:]:
            if isinstance(child, list) and child:
                walk(child, owner)
    walk(root)
    return result


def _graphic_origin(node):
    translated_content(node)  # Validate supported coordinates even on unchanged members.
    kind = node[0].rsplit('_', 1)[-1]
    name = {'line': 'start', 'arc': 'start', 'rect': 'start', 'circle': 'center', 'text': 'at'}.get(kind)
    points = children(node, name) if name else children(children(node, 'pts')[0], 'xy')
    if not points:
        raise ValueError('artwork group member lacks its origin coordinate')
    return tuple(nanometres(v) for v in points[0][1:3])


def _check_artwork_groups(roots, before, policy):
    if policy['source_sha256'] != hashlib.sha256(before).hexdigest():
        raise ValueError('artwork policy names a different source board')
    inventories = [_artwork_inventory(root) for root in roots]
    allowed = set()
    for group in policy['free_groups']:
        deltas, layers = set(), set()
        for uid in group['members']:
            nodes = []
            for inventory in inventories:
                if uid not in inventory:
                    raise ValueError('artwork group member missing')
                owner, node = inventory[uid]
                layer = children(node, 'layer')
                if (owner != group['owner_uuid'] or node[0] not in GRAPHICS or
                        layer not in ([['layer', 'F.SilkS']], [['layer', 'B.SilkS']]) or
                        (owner is None and not node[0].startswith('gr_')) or
                        (owner is not None and not node[0].startswith('fp_'))):
                    raise ValueError('artwork group owner or kind differs from classification')
                if node[0].endswith('_text'):
                    attributes = node[2:] if node[0] == 'gr_text' else node[3:]
                    if ('hide' in attributes or children(node, 'hide') or
                            any('hide' in effect for effect in children(node, 'effects'))):
                        raise ValueError('hidden artwork cannot be a free group')
                layers.add(layer[0][1])
                nodes.append(node)
            if nodes[0][0] != nodes[1][0]:
                raise ValueError('artwork group member kind changed')
            # Preserve the ordinary raw-token proof below; this checks only rigidity.
            origins = [_graphic_origin(node) for node in nodes]
            deltas.add(tuple(a-b for b, a in zip(*origins)))
            allowed.add(uid)
        if len(deltas) != 1 or len(layers) != 1:
            raise ValueError('rigid artwork group must keep one side and common translation')
    return allowed


def translated_content(node):
    """Retain a silk graphic's exact shape/style with its translation removed."""
    node = copy.deepcopy(node)
    points = []
    kind = node[0].rsplit('_', 1)[-1]
    coordinates = {'line': {'start', 'end'}, 'arc': {'start', 'mid', 'end'},
                   'rect': {'start', 'end'}, 'circle': {'center', 'end'},
                   'poly': set(), 'text': {'at'}}[kind]
    for child in node[1:]:
        if not isinstance(child, list):
            continue
        if not child:
            raise ValueError('silk graphic refuses empty child')
        if not isinstance(child[0], str):
            raise ValueError('silk graphic refuses list-headed child')
        if child[0] in coordinates:
            if len(child) not in ((3, 4) if child[0] == 'at' else (3,)):
                raise ValueError('malformed silk coordinate')
            points.append(child)
        elif kind == 'poly' and child[0] == 'pts':
            if any(not isinstance(p, list) or len(p) != 3 or p[0] != 'xy' for p in child[1:]):
                raise ValueError('unsupported silk polygon coordinate')
            points.extend(child[1:])
    # Every other token, including unknown nested coordinates, stays exact.
    if not points:
        raise ValueError('silk graphic lacks translation coordinates')
    x, y = (nanometres(v) for v in points[0][1:3])
    for p in points:
        p[1:3] = [nanometres(p[1]) - x, nanometres(p[2]) - y]
    return node


# KiCad's writer emits exactly these string escapes, and the shared parser
# decodes them as KiCad's lexer does. Any other backslash is ambiguous.
WRITER_ESCAPES = frozenset('"\\nr')


def _writer_escapes(data, raw):
    """Refuse every backslash except a writer escape inside a quoted atom."""
    count = 0
    def walk(node):
        nonlocal count
        for atom in node:
            if isinstance(atom, list):
                walk(atom)
                continue
            if '\\' not in atom:
                continue
            if not atom.startswith('"'):
                raise ValueError('backslash outside a quoted string is unsupported in artwork comparison')
            body, i = atom[1:-1], 0
            while i < len(body):
                if body[i] == '\\':
                    if body[i + 1:i + 2] not in WRITER_ESCAPES or not body[i + 1:i + 2]:
                        raise ValueError('backslash escape outside the KiCad writer set is ambiguous')
                    i += 1
                i += 1
            count += atom.count('\\')
    walk(raw)
    # Backslashes the parser skipped (comments) are not proven either.
    if count != data.count(b'\\'):
        raise ValueError('backslash outside parsed atoms is unsupported in artwork comparison')


def normalize_targets(targets):
    """An explicit artwork target: footprint reference and field name pairs."""
    if targets is None:
        return None
    pairs = set()
    for target in targets:
        if (not isinstance(target, (list, tuple)) or len(target) != 2 or
                any(not isinstance(v, str) or not v or v.startswith('-') or
                    any(c in v for c in '\x00\n\r') for v in target)):
            raise ValueError('artwork target must be a reference and field name pair')
        pairs.add(tuple(target))
    if not pairs:
        raise ValueError('artwork target set is empty')
    return frozenset(pairs)


# An exact, candidate-bound field-pose request. Each entry names one visible
# movable silk field by footprint and field uuid, its board pose in the named
# source board, and the exact board pose to write; nothing else may change.
FIELD_POSE_SCHEMA = 'agent-ee.artwork-field-pose/1'
_ANGLE = re.compile(r'(0|[1-9][0-9]{0,2})(\.[0-9]*[1-9])?')
_UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')


def normalize_field_poses(request):
    """Validate a field-pose request into its single canonical form."""
    if request is None:
        return None
    def pose(value):
        if (not isinstance(value, dict) or set(value) != {'x_nm', 'y_nm', 'angle_deg'} or
                any(type(value[c]) is not int or abs(value[c]) > 10**12 for c in ('x_nm', 'y_nm')) or
                not isinstance(value['angle_deg'], str) or not _ANGLE.fullmatch(value['angle_deg']) or
                Decimal(value['angle_deg']) >= 360):
            raise ValueError('field pose must be integer x_nm/y_nm and a canonical angle_deg in [0, 360)')
        return dict(value)
    if (not isinstance(request, dict) or set(request) != {'schema', 'source_sha256', 'fields'} or
            request['schema'] != FIELD_POSE_SCHEMA or not isinstance(request['source_sha256'], str) or
            not re.fullmatch('[0-9a-f]{64}', request['source_sha256']) or
            not isinstance(request['fields'], list) or not request['fields']):
        raise ValueError('field-pose request malformed')
    fields = []
    for entry in request['fields']:
        if (not isinstance(entry, dict) or set(entry) != {'footprint_uuid', 'field_uuid', 'from', 'to'} or
                any(not isinstance(entry[u], str) or not _UUID.fullmatch(entry[u])
                    for u in ('footprint_uuid', 'field_uuid'))):
            raise ValueError('field-pose entry malformed')
        entry = dict(entry, **{'from': pose(entry['from']), 'to': pose(entry['to'])})
        if entry['from'] == entry['to']:
            raise ValueError('field-pose entry does not change the pose')
        fields.append(entry)
    if len({e['field_uuid'] for e in fields}) != len(fields):
        raise ValueError('field-pose request names a field twice')
    return {'schema': FIELD_POSE_SCHEMA, 'source_sha256': request['source_sha256'],
            'fields': sorted(fields, key=lambda e: e['field_uuid'])}


def canonical_field_poses(request):
    # Absence of a request is spelled by omitting it, never as JSON null.
    if request is None:
        raise ValueError('field-pose request is null')
    return json.dumps(normalize_field_poses(request), sort_keys=True, separators=(',', ':'))


def decode_field_poses(text):
    """Decode an explicitly supplied field-pose argument; JSON null never means absent."""
    try:
        request = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise ValueError('field-pose request malformed') from exc
    if request is None:
        raise ValueError('field-pose request is null')
    return normalize_field_poses(request)


# The pose tokens a requested field's footprint and field `at` nodes may be
# spelled with: unquoted plain decimals, which KiCad's lexer reads as numbers
# and Decimal reads identically. Quoted, signed-plus, exponent and bare-point
# spellings refuse rather than being compared by decoded value.
_POSE_NUMBER = re.compile(r'-?[0-9]{1,15}(\.[0-9]{1,15})?')


def _native_at(raw):
    """Refuse a raw `at` node unless every pose token is a plain native number."""
    if raw[:1] != ['at'] or any(not isinstance(t, str) or not _POSE_NUMBER.fullmatch(t) for t in raw[1:]):
        raise ValueError('field-pose position token is not a native number')


def _angle(token):
    try:
        value = Decimal(token)
    except (InvalidOperation, TypeError) as exc:
        raise ValueError('field angle is not a decimal') from exc
    if not value.is_finite():
        raise ValueError('field angle is not finite')
    return Fraction(value) % 360


def field_pose(footprint, prop):
    """Exact board pose of a saved footprint field: (x_nm, y_nm, angle degrees).

    Field coordinates are footprint-relative before the footprint rotation and
    the saved field angle is the board angle. Only quarter-turn footprints have
    an exact rational transform; any other orientation refuses.
    """
    from .symbol_library import LibraryError
    fp_at, at = children(footprint, 'at'), children(prop, 'at')
    if len(fp_at) != 1 or len(fp_at[0]) not in (3, 4) or len(at) != 1 or len(at[0]) not in (3, 4):
        raise ValueError('field pose requires one footprint and one field position')
    try:
        fx, fy, lx, ly = (nanometres(v) for v in (*fp_at[0][1:3], *at[0][1:3]))
    except (LibraryError, TypeError) as exc:
        raise ValueError('field pose coordinate is not exact') from exc
    turn = _angle(fp_at[0][3]) if len(fp_at[0]) == 4 else Fraction(0)
    if turn % 90:
        raise ValueError('field-pose request requires a quarter-turn footprint')
    c, s = {0: (1, 0), 90: (0, 1), 180: (-1, 0), 270: (0, -1)}[int(turn)]
    return (fx + lx * c + ly * s, fy - lx * s + ly * c,
            _angle(at[0][3]) if len(at[0]) == 4 else Fraction(0))


def _request_pose(value):
    return (value['x_nm'], value['y_nm'], Fraction(Decimal(value['angle_deg'])))


def _check_field_poses(roots, raws, before, request):
    """Bind each requested field's identity, source pose and destination pose."""
    from .silk_artwork import DECLARATION, movable_names
    if hashlib.sha256(before).hexdigest() != request['source_sha256']:
        raise ValueError('field-pose request names a different source board')
    def ats(node):
        return [i for i, c in enumerate(node) if isinstance(c, list) and c[:1] == ['at']]
    for entry in request['fields']:
        poses = []
        for root, raw in zip(roots, raws):
            found = [(i, j) for i, fp in enumerate(root) if isinstance(fp, list) and fp[:1] == ['footprint'] and
                     children(fp, 'uuid') == [['uuid', entry['footprint_uuid']]]
                     for j, p in enumerate(fp) if isinstance(p, list) and p[:1] == ['property'] and
                     children(p, 'uuid') == [['uuid', entry['field_uuid']]]]
            if len(found) != 1:
                raise ValueError('field-pose request names no unique footprint field')
            i, j = found[0]
            fp, prop = root[i], root[i][j]
            # The decoded tree loses quoting; check the exact saved pose spelling first.
            for node, spelled in ((fp, raw[i]), (prop, raw[i][j])):
                for k in ats(node):
                    _native_at(spelled[k])
            decl = [p[2] for p in children(fp, 'property') if p[1:2] == [DECLARATION] and len(p) > 2]
            hidden = [h for h in children(prop, 'hide') if h != ['hide', 'no']] or [
                a for e in children(prop, 'effects') for a in e[1:] if a == 'hide' or a == ['hide', 'yes']]
            if (len(prop) < 3 or not isinstance(prop[1], str) or
                    prop[1] not in movable_names(decl[0] if len(decl) == 1 else None) or
                    children(prop, 'layer') not in ([['layer', 'F.SilkS']], [['layer', 'B.SilkS']]) or hidden):
                raise ValueError('field-pose request names a field that is not a movable visible silk field')
            poses.append(field_pose(fp, prop))
        source, destination = poses
        if source != _request_pose(entry['from']):
            raise ValueError('field-pose source pose differs from the request')
        want = _request_pose(entry['to'])
        if destination[:2] != want[:2]:
            raise ValueError('field-pose destination position differs from the request')
        if destination[2] != want[2]:
            raise ValueError('field-pose destination angle differs from the request')


def _target_argv(targets):
    return [v for ref, field in sorted(targets) for v in ('--target', ref, field)]


def receipt_scope(argv):
    """Recover a receipt's write-scope authority (targets or field poses) from its argv."""
    rest = argv[5:]
    if rest[:1] == ['--artwork-policy']:
        if len(rest) != 2 or not isinstance(rest[1], str):
            raise ValueError('artwork receipt policy arguments malformed')
        policy = decode_artwork_policy(rest[1])
        if canonical_artwork_policy(policy) != rest[1]:
            raise ValueError('artwork receipt policy is not canonical')
        return {'artwork_policy': policy}
    if rest[:1] != ['--field-pose']:
        return {'targets': receipt_targets(argv)}
    if len(rest) != 2 or not isinstance(rest[1], str):
        raise ValueError('artwork receipt field-pose arguments malformed')
    request = decode_field_poses(rest[1])
    if canonical_field_poses(request) != rest[1]:
        raise ValueError('artwork receipt field-pose request is not canonical')
    return {'field_poses': request}


def receipt_targets(argv):
    """Recover a receipt's canonical target set from its producer invocation."""
    rest = argv[5:]
    if not rest:
        return None
    if len(rest) % 3 or any(rest[i] != '--target' for i in range(0, len(rest), 3)):
        raise ValueError('artwork receipt target arguments malformed')
    targets = normalize_targets([rest[i + 1:i + 3] for i in range(0, len(rest), 3)])
    if _target_argv(targets) != rest:
        raise ValueError('artwork receipt target arguments are not canonical')
    return targets


def write_scope(before, after, targets=None, field_poses=None, artwork_policy=None):
    """Compare every saved token except allowed silk translations; unknowns refuse.

    Unchanged nodes must keep their exact raw spelling. Backslashes are
    accepted only as KiCad writer escapes inside quoted strings, where the
    shared parser's decoding equals KiCad's; any other backslash refuses.
    With targets, only those footprint fields may move and graphics may not.
    With a field-pose request, exactly the requested fields change, each from
    its bound source pose to its exact requested pose (an angle change only
    where the request names one), and every other field token stays exact.
    Supported UUID-bearing siblings may reorder inside the same board or
    footprint parent; unknown syntax keeps its exact position and spelling.
    Graphics are anchored unless a source-bound artwork policy explicitly
    classifies their whole free group. Every group member keeps one translation.
    """
    from .silk_artwork import DECLARATION, movable_names
    targets = normalize_targets(targets)
    field_poses = normalize_field_poses(field_poses)
    artwork_policy = normalize_artwork_policy(artwork_policy)
    if artwork_policy is not None and (targets is not None or field_poses is not None):
        raise ValueError('artwork policy and field-only scopes are exclusive')
    if targets is not None and field_poses is not None:
        raise ValueError('artwork target and field-pose request are exclusive')
    posed = {e['field_uuid']: e['footprint_uuid'] for e in field_poses['fields']} if field_poses else None
    roots = [parse_sexpr(b) for b in (before, after)]
    raws = [parse_sexpr(b, raw=True) for b in (before, after)]
    for data, raw in zip((before, after), raws):
        _writer_escapes(data, raw)
    moves = []
    def sibling_pairs(b, a, rb, ra, scope):
        kinds = ({'footprint'} | {k for k in GRAPHICS if k.startswith('gr_')}
                 if scope == 'board' else
                 {'property'} | {k for k in GRAPHICS if k.startswith('fp_')})
        def keyed(n):
            if (not isinstance(n, list) or not n or not isinstance(n[0], str)
                    or n[0] not in kinds):
                return None
            uid = children(n, 'uuid')
            return (n[0], uid[0][1]) if uid else None
        old = {i: key for i, n in enumerate(b) if (key := keyed(n)) is not None}
        new = {i: key for i, n in enumerate(a) if (key := keyed(n)) is not None}
        if old.keys() != new.keys() or set(old.values()) != set(new.values()):
            raise ValueError('artwork sibling identity or parent changed')
        positions = {key: i for i, key in new.items()}
        for i, (n, raw) in enumerate(zip(b, rb)):
            if i in old:
                j = positions[old[i]]
                yield n, a[j], raw, ra[j]
            elif raw != ra[i]:
                if isinstance(n, list) and n and isinstance(n[0], list):
                    raise ValueError('artwork comparison refuses list-headed node')
                # Legacy nodes without a UUID retain positional comparison only.
                if isinstance(n, list) and n and n[0] in kinds:
                    yield n, a[i], raw, ra[i]
                else:
                    raise ValueError('non-silk board token changed')

    def compare(b, a, rb, ra, names=frozenset(), owner=None, fp_uid=None, scope='board'):
        if rb == ra:
            return
        if not isinstance(rb, list) or not isinstance(ra, list) or rb[:1] != ra[:1]:
            raise ValueError('non-silk board token changed')
        # A list-headed node is not KiCad syntax; refuse it typed, not by hashing it.
        if not isinstance(b[0], str):
            raise ValueError('artwork comparison refuses list-headed node')
        if scope == 'footprint':
            if any(len(p) < 2 for p in children(b, 'property')):
                raise ValueError('artwork comparison refuses nameless property')
            decl = [p[2:3] for p in children(b, 'property') if p[1:2] == [DECLARATION]]
            if any(len(d) != 1 or not isinstance(d[0], str) for d in decl):
                raise ValueError('malformed movable silk declaration')
            decl = [d[0] for d in decl]
            if len(decl) > 1:
                raise ValueError('duplicate movable silk declaration')
            names = movable_names(decl[0] if decl else None)
            refs = [p[2] for p in children(b, 'property') if p[1:2] == ['Reference'] and len(p) > 2]
            owner = refs[0] if len(refs) == 1 and isinstance(refs[0], str) else None
            fp_uid = children(b, 'uuid')
        silk = children(b, 'layer') in ([['layer', 'F.SilkS']], [['layer', 'B.SilkS']])
        if b[0] == 'property' and len(b) > 2 and not isinstance(b[1], str):
            raise ValueError('artwork comparison refuses non-atom field name')
        field = b[0] == 'property' and len(b) > 2 and b[1] in names
        if silk and (field or b[0] in GRAPHICS):
            if targets is not None and (not field or (owner, b[1]) not in targets):
                raise ValueError('silk edit outside the artwork target')
            if children(b, 'render_cache') or children(a, 'render_cache'):
                raise ValueError('changed silk render_cache requires regenerated comparison')
            uid = children(b, 'uuid')
            if len(uid) != 1 or uid != children(a, 'uuid'):
                raise ValueError('silk identity changed')
            if posed is not None and (not field or uid[0][1] not in posed or
                                      fp_uid != [['uuid', posed[uid[0][1]]]]):
                raise ValueError('silk edit outside the field-pose request')
            if field:
                def content(n):
                    n = copy.deepcopy(n)
                    at = children(n, 'at')
                    if len(at) != 1 or len(at[0]) < 3:
                        raise ValueError('field position missing')
                    # A requested pose is checked exactly on its own; the rest stays exact.
                    if posed is None:
                        at[0][1:3] = [0, 0]
                    else:
                        del at[0][1:]
                    return n
                equal = content(rb) == content(ra)
            else:
                equal = translated_content(rb) == translated_content(ra)
            if not equal:
                raise ValueError('silk edit exceeds translation authority')
            if not field and uid[0][1] not in free_artwork:
                raise ValueError('anchored artwork cannot move without a source-bound policy')
            moves.append(uid[0][1])
            return
        if len(b) != len(a):
            raise ValueError('board item added or removed')
        if scope not in ('board', 'footprint'):
            raise ValueError('non-silk board token changed')
        for old, new, rold, rnew in sibling_pairs(b, a, rb, ra, scope):
            if rold != rnew:
                child_scope = 'footprint' if scope == 'board' and old[0] == 'footprint' else 'item'
                compare(old, new, rold, rnew, names, owner, fp_uid, child_scope)
    if any(r[0] != 'kicad_pcb' for r in roots):
        raise ValueError('artwork requires saved boards')
    # Reject duplicate identities even on otherwise unchanged subtrees.
    for root in roots:
        seen = set()
        def identities(n):
            uid = children(n, 'uuid')
            if uid:
                if (len(uid) != 1 or len(uid[0]) != 2 or not isinstance(uid[0][1], str) or
                        uid[0][1] in seen):
                    raise ValueError('ambiguous artwork identity')
                seen.add(uid[0][1])
            for c in n[1:]:
                if isinstance(c, list):
                    identities(c)
        identities(root)
    free_artwork = _check_artwork_groups(roots, before, artwork_policy) if artwork_policy is not None else set()
    if targets is not None:
        # Each target names exactly one footprint and one of its fields.
        for ref, name in targets:
            owners = [fp for fp in children(roots[0], 'footprint')
                      if [p[2:3] for p in children(fp, 'property') if p[1:2] == ['Reference']] == [[ref]]]
            if len(owners) != 1 or len([p for p in children(owners[0], 'property')
                                        if p[1:2] == [name]]) != 1:
                raise ValueError('artwork target must name exactly one footprint field')
        if [fp for fp in children(roots[0], 'footprint')
                if len([p for p in children(fp, 'property') if p[1:2] == ['Reference']]) != 1]:
            raise ValueError('artwork target requires one Reference per footprint')
    if field_poses is not None:
        _check_field_poses(roots, raws, before, field_poses)
    compare(*roots, *raws)
    if posed is not None and sorted(moves) != sorted(posed):
        raise ValueError('field-pose request not applied exactly')
    proof = {'status': 'allowed', 'before_sha256': hashlib.sha256(before).hexdigest(),
             'after_sha256': hashlib.sha256(after).hexdigest(), 'moved_identities': sorted(moves)}
    if targets is not None:
        proof['targets'] = [list(t) for t in sorted(targets)]
    if field_poses is not None:
        proof['field_poses'] = field_poses
    if artwork_policy is not None:
        proof['artwork_policy'] = artwork_policy
    return proof


def step(tool_dir, board, *, stage='verification', timeout_s=300, targets=None, field_poses=None, artwork_policy=None):
    """Create an immutable source snapshot and an ordinary campaign implementation step.

    targets restricts the repair to named (reference, field) pairs, receipted
    in canonical order in the producer argv. A field-pose request is receipted
    whole, as its canonical JSON. Free rigid groups use --artwork-policy with
    canonical source-bound membership, independently replayed by the qualifier.
    """
    from .placement_adapter import tool_snapshot, _relative
    _relative(board, 'artwork board')
    targets = normalize_targets(targets)
    artwork_policy = normalize_artwork_policy(artwork_policy)
    if artwork_policy is not None and (targets is not None or field_poses is not None):
        raise ValueError('artwork policy and field-only scopes are exclusive')
    if targets is not None and field_poses is not None:
        raise ValueError('artwork target and field-pose request are exclusive')
    scope = (_target_argv(targets) if targets else
             ['--field-pose', canonical_field_poses(field_poses)] if field_poses is not None else
             ['--artwork-policy', canonical_artwork_policy(artwork_policy)] if artwork_policy is not None else [])
    tool_dir = tool_snapshot(Path(tool_dir).resolve())
    (tool_dir / 'artwork.py').write_text(LAUNCHER)
    return {'schema': 'agent-ee.stage-step/1', 'stage': stage, 'step': STEP,
            'argv': ['/usr/bin/python3', '-B', '-I', f'{TOOL_AT}/artwork.py', f'/candidate/{board}',
                     *scope],
            'mounts': [{'source': str(tool_dir), 'at': TOOL_AT}], 'source': TOOL_AT,
            'version_argv': ['/usr/bin/kicad-cli', 'version'],
            'writes': {board: BOARD}, 'artifacts': ['placement'], 'timeout_s': timeout_s}


def qualify_receipt(receipt_dir, receipt, board, source_files):
    """Authenticate the producer snapshot and independently recompute its silk-only scope."""
    from .placement_adapter import LAUNCHER as PLACER_LAUNCHER
    if receipt['status'] != 'completed' or set(receipt['spec']['writes']) != {board}:
        raise ValueError('artwork receipt incomplete or writes other inputs')
    expected = {f'agent_ee/{name}': sha for name, sha in source_files.items()}
    expected['launch.py'] = hashlib.sha256(PLACER_LAUNCHER.encode()).hexdigest()
    expected['artwork.py'] = hashlib.sha256(LAUNCHER.encode()).hexdigest()
    spec = receipt['spec']
    if (spec['argv'][:5] != ['/usr/bin/python3', '-B', '-I', f'{TOOL_AT}/artwork.py', f'/candidate/{board}'] or
            spec.get('source') != TOOL_AT or len(spec['mounts']) != 1 or
            spec['mounts'][0]['at'] != TOOL_AT):
        raise ValueError('artwork receipt invokes a different producer')
    tree = receipt['tool']['source_tree']
    # Bind the entire retained tool tree to the campaign's package pin.
    if tree != {'path': TOOL_AT, **tree_identity_from_files(expected)}:
        raise ValueError('artwork source differs from campaign source pin')
    root = Path(receipt_dir)
    proof = write_scope((root / 'inputs' / board).read_bytes(), (root / 'run' / BOARD).read_bytes(),
                        **receipt_scope(spec['argv']))
    if digest(root / 'run' / SOURCE) != receipt['inputs'].get(board):
        raise ValueError('artwork source artifact differs from receipt input')
    report = read_json(root / 'run' / REPORT)
    if (report.get('producer') != STEP or report.get('write_scope') != proof or
            report.get('source_sha256') != receipt['inputs'].get(board) or
            report.get('board_sha256') != receipt['outputs'].get(board) or
            receipt['spec']['writes'] != {board: BOARD} or not set(receipt['changed']) <= {board}):
        raise ValueError('artwork receipt does not bind its saved board and scope')
    return proof


def tree_identity_from_files(files):
    from .storage import identity
    return {'files': len(files), 'sha256': identity(files)}


def _apply_field_poses(k, board, request):
    """Write each requested pose natively after checking the native source pose."""
    moved = []
    for entry in request['fields']:
        found = [(fp, f) for fp in board.GetFootprints() if fp.m_Uuid.AsString() == entry['footprint_uuid']
                 for f in fp.GetFields() if f.m_Uuid.AsString() == entry['field_uuid']]
        if len(found) != 1:
            raise ValueError('field-pose request names no unique footprint field')
        fp, field = found[0]
        if _native_pose(field) != _request_pose(entry['from']):
            raise ValueError('field-pose source pose differs from the request')
        to = entry['to']
        field.SetPosition(k.VECTOR2I(to['x_nm'], to['y_nm']))
        field.SetTextAngle(k.EDA_ANGLE(float(Decimal(to['angle_deg'])), k.DEGREES_T))
        moved.append(dict(uuid=entry['field_uuid'], reference=fp.GetReference(), field=field.GetName(),
                          **{'from': entry['from'], 'to': to}))
    return moved


def _native_pose(field):
    pos = field.GetPosition()
    return (pos.x, pos.y, Fraction(Decimal(repr(field.GetTextAngle().AsDegrees()))) % 360)


def _report_digest(report):
    return hashlib.sha256(json.dumps(report, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def artwork_drc_pin(board, report):
    """Capture immediately after native DRC, before comparison annotates findings."""
    return dict(board_sha256=digest(board), report_sha256=_report_digest(report),
                payloads={suffix: digest(board.with_suffix(suffix)) if board.with_suffix(suffix).is_file() else None
                          for suffix in ('.kicad_pro', '.kicad_dru')})


def compare_artwork_drc(source, output, before, after, *, native_pins, **scope):
    """Reprove exact saved boards/rules before matching intrinsic text positions."""
    from .placer_runner import _drc_comparison
    pins = [artwork_drc_pin(path, report) for path, report in ((source, before), (output, after))]
    if pins != native_pins or pins[0]['payloads'] != pins[1]['payloads']:
        raise ValueError('artwork native board, report or rule pin changed')
    proof = write_scope(source.read_bytes(), output.read_bytes(), **scope)
    inventories = [_artwork_inventory(parse_sexpr(path.read_bytes())) for path in (source, output)]
    def text_content(node):
        node = copy.deepcopy(node)
        at = children(node, 'at')
        if len(at) != 1 or len(at[0]) < 3:
            raise ValueError('intrinsic text position missing')
        at[0][1:3] = [0, 0]
        return node
    text_ids = []
    for uid in proof['moved_identities']:
        owner, old = inventories[0][uid]
        new_owner, new = inventories[1][uid]
        if (old[0] in ('property', 'fp_text', 'gr_text') and owner == new_owner and
                text_content(old) == text_content(new)):
            text_ids.append(uid)
    context = dict(schema='agent-ee.intrinsic-text/1', proof=proof,
                   reports=[pin['report_sha256'] for pin in pins], identities=text_ids)
    comparison = _drc_comparison(before, after, unchanged_copper=proof, artwork_text=context)
    comparison['artwork_drc_pins'] = pins
    return comparison


def produce(source, root, targets=None, field_poses=None, artwork_policy=None):
    """Write a disposable candidate, retain native DRC and fail on any new non-silk finding.

    With targets only the named footprint fields may move; graphics stay put.
    A field-pose request writes exactly its requested poses, without a search,
    and the reloaded saved board must carry exactly those poses. Graphics stay
    anchored unless a source-bound policy explicitly names free rigid groups.
    """
    import pcbnew as k
    from .placer_runner import _native_drc
    from .silk_artwork import relocate, ARTWORK_CONFLICTS, containment_witness
    targets = normalize_targets(targets)
    field_poses = normalize_field_poses(field_poses)
    artwork_policy = normalize_artwork_policy(artwork_policy)
    scope = dict(targets=targets, field_poses=field_poses, artwork_policy=artwork_policy)
    if artwork_policy is not None and (targets is not None or field_poses is not None):
        raise ValueError('artwork policy and field-only scopes are exclusive')
    # Classification applies even to unchanged groups and is source-bound.
    write_scope(source.read_bytes(), source.read_bytes(), targets=targets, artwork_policy=artwork_policy)
    if targets is not None and field_poses is not None:
        raise ValueError('artwork target and field-pose request are exclusive')
    if field_poses is not None and digest(source) != field_poses['source_sha256']:
        raise ValueError('field-pose request names a different source board')
    root = Path(root)
    output = root / BOARD
    output.parent.mkdir(parents=True)
    baseline = output.parent / 'baseline'
    baseline.mkdir()
    original = baseline / 'candidate.kicad_pcb'
    shutil.copyfile(source, original)
    shutil.copyfile(source, output)
    payloads = {s: source.with_suffix(s).read_bytes() if source.with_suffix(s).is_file() else None
                for s in ('.kicad_pro', '.kicad_dru')}
    for suffix, payload in payloads.items():
        if payload is not None:
            if suffix == '.kicad_dru':
                import re
                if re.search(rb'\(\s*severity\s+"?ignore\b', payload, re.I):
                    raise ValueError('artwork DRC explicitly ignored by a custom rule')
            original.with_suffix(suffix).write_bytes(payload)
            output.with_suffix(suffix).write_bytes(payload)
    before = _native_drc(original, baseline)
    before_pin = artwork_drc_pin(original, before)
    board = k.LoadBoard(str(output))
    if field_poses is not None:
        artwork = dict(field_poses=field_poses, moved_fields=_apply_field_poses(k, board, field_poses))
    else:
        artwork = relocate(board, before['violations'], graphics=targets is None, targets=targets,
                           free_groups=artwork_policy['free_groups'] if artwork_policy else (),
                           conflict_types=ARTWORK_CONFLICTS)
    if not k.SaveBoard(str(output), board, True):
        raise ValueError('native artwork save failed')
    if field_poses is not None:
        # Save/reload equality: the saved board carries exactly the requested poses.
        reloaded = k.LoadBoard(str(output))
        poses = {f.m_Uuid.AsString(): _native_pose(f) for fp in reloaded.GetFootprints() for f in fp.GetFields()}
        if any(poses.get(e['field_uuid']) != _request_pose(e['to']) for e in field_poses['fields']):
            raise ValueError('reloaded artwork board differs from the requested field poses')
    for suffix, payload in payloads.items():
        if payload is None:
            output.with_suffix(suffix).unlink(missing_ok=True)
        else:
            output.with_suffix(suffix).write_bytes(payload)
    proof = write_scope(source.read_bytes(), output.read_bytes(), **scope)
    after = _native_drc(output, output.parent)
    after_pin = artwork_drc_pin(output, after)
    # The silk-only write scope is the copper-unchanged witness that lets unmeasured
    # collisions keep baseline treatment; it must cover exactly the DRC'd bytes.
    if (digest(original), digest(output)) != (proof['before_sha256'], proof['after_sha256']):
        raise ValueError('artwork DRC boards differ from the write-scope proof')
    comparison = compare_artwork_drc(original, output, before, after,
                                     native_pins=[before_pin, after_pin], **scope)
    if any(v['type'] not in ARTWORK_CONFLICTS for v in comparison['added_findings']):
        raise ValueError('artwork introduced a non-silk native DRC finding')
    defects = [dict(type='placement_silkscreen_conflict', status='open', verdict='defect', native=v)
               for v in after['violations'] if v['type'] in ARTWORK_CONFLICTS]
    comparison['open_defects'] = defects
    artwork.update(schema='agent-ee.placement-silkscreen/1', write_scope=proof)
    write_json(root / ARTWORK, artwork)
    report = {'schema': 'agent-ee.placed-candidate/1', 'status': 'placed', 'producer': STEP,
              'authority': 'disposable-unsealed-proposal', 'source_sha256': digest(source),
              'board_sha256': digest(output), 'containment': containment_witness(output),
              'write_scope': proof, 'open_defects': defects,
              'native_drc_comparison': comparison,
              'outcome': 'defect' if defects else 'informational',
              'verdict': 'defect' if defects else 'informational'}
    write_json(root / REPORT, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('board', type=Path)
    parser.add_argument('--target', nargs=2, action='append', metavar=('REFERENCE', 'FIELD'))
    parser.add_argument('--field-pose', metavar='JSON', help='canonical exact field-pose request')
    parser.add_argument('--artwork-policy', metavar='JSON', help='source-bound free rigid groups')
    args = parser.parse_args()
    produce(args.board, Path('/work'), targets=args.target,
            field_poses=None if args.field_pose is None else decode_field_poses(args.field_pose),
            artwork_policy=None if args.artwork_policy is None else decode_artwork_policy(args.artwork_policy))
    return 0
