"""Receipted, translation-only silk repair on an already routed board."""
import argparse
import copy
import hashlib
from pathlib import Path
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


def _target_argv(targets):
    return [v for ref, field in sorted(targets) for v in ('--target', ref, field)]


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


def write_scope(before, after, targets=None):
    """Compare every saved token except allowed silk translations; unknowns refuse.

    Unchanged nodes must keep their exact raw spelling. Backslashes are
    accepted only as KiCad writer escapes inside quoted strings, where the
    shared parser's decoding equals KiCad's; any other backslash refuses.
    With targets, only those footprint fields may move and graphics may not.
    """
    from .silk_artwork import DECLARATION, movable_names
    targets = normalize_targets(targets)
    roots = [parse_sexpr(b) for b in (before, after)]
    raws = [parse_sexpr(b, raw=True) for b in (before, after)]
    for data, raw in zip((before, after), raws):
        _writer_escapes(data, raw)
    moves = []
    def compare(b, a, rb, ra, names=frozenset(), owner=None):
        if rb == ra:
            return
        if not isinstance(rb, list) or not isinstance(ra, list) or rb[:1] != ra[:1]:
            raise ValueError('non-silk board token changed')
        # A list-headed node is not KiCad syntax; refuse it typed, not by hashing it.
        if not isinstance(b[0], str):
            raise ValueError('artwork comparison refuses list-headed node')
        if b[0] == 'footprint':
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
            if field:
                def content(n):
                    n = copy.deepcopy(n)
                    at = children(n, 'at')
                    if len(at) != 1 or len(at[0]) < 3:
                        raise ValueError('field position missing')
                    at[0][1:3] = [0, 0]
                    return n
                equal = content(rb) == content(ra)
            else:
                equal = translated_content(rb) == translated_content(ra)
            if not equal:
                raise ValueError('silk edit exceeds translation authority')
            moves.append(uid[0][1])
            return
        if len(b) != len(a):
            raise ValueError('board item added or removed')
        for old, new, rold, rnew in zip(b, a, rb, ra):
            if rold != rnew:
                compare(old, new, rold, rnew, names, owner)
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
    compare(*roots, *raws)
    proof = {'status': 'allowed', 'before_sha256': hashlib.sha256(before).hexdigest(),
             'after_sha256': hashlib.sha256(after).hexdigest(), 'moved_identities': sorted(moves)}
    if targets is not None:
        proof['targets'] = [list(t) for t in sorted(targets)]
    return proof


def step(tool_dir, board, *, stage='verification', timeout_s=300, targets=None):
    """Create an immutable source snapshot and an ordinary campaign implementation step.

    targets restricts the repair to named (reference, field) pairs, receipted
    in canonical order in the producer argv.
    """
    from .placement_adapter import tool_snapshot, _relative
    _relative(board, 'artwork board')
    targets = normalize_targets(targets)
    tool_dir = tool_snapshot(Path(tool_dir).resolve())
    (tool_dir / 'artwork.py').write_text(LAUNCHER)
    return {'schema': 'agent-ee.stage-step/1', 'stage': stage, 'step': STEP,
            'argv': ['/usr/bin/python3', '-B', '-I', f'{TOOL_AT}/artwork.py', f'/candidate/{board}',
                     *(_target_argv(targets) if targets else [])],
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
    targets = receipt_targets(spec['argv'])
    root = Path(receipt_dir)
    proof = write_scope((root / 'inputs' / board).read_bytes(), (root / 'run' / BOARD).read_bytes(),
                        targets=targets)
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


def produce(source, root, targets=None):
    """Write a disposable candidate, retain native DRC and fail on any new non-silk finding.

    With targets only the named footprint fields may move; graphics stay put.
    """
    import pcbnew as k
    from .placer_runner import _native_drc, _drc_comparison
    from .silk_artwork import relocate, SILK_CONFLICTS, containment_witness
    targets = normalize_targets(targets)
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
    board = k.LoadBoard(str(output))
    artwork = relocate(board, before['violations'], graphics=targets is None, targets=targets)
    if not k.SaveBoard(str(output), board, True):
        raise ValueError('native artwork save failed')
    for suffix, payload in payloads.items():
        if payload is None:
            output.with_suffix(suffix).unlink(missing_ok=True)
        else:
            output.with_suffix(suffix).write_bytes(payload)
    proof = write_scope(source.read_bytes(), output.read_bytes(), targets=targets)
    after = _native_drc(output, output.parent)
    # The silk-only write scope is the copper-unchanged witness that lets unmeasured
    # collisions keep baseline treatment; it must cover exactly the DRC'd bytes.
    if (digest(original), digest(output)) != (proof['before_sha256'], proof['after_sha256']):
        raise ValueError('artwork DRC boards differ from the write-scope proof')
    comparison = _drc_comparison(before, after, unchanged_copper=proof)
    if any(v['type'] not in SILK_CONFLICTS for v in comparison['added_findings']):
        raise ValueError('artwork introduced a non-silk native DRC finding')
    defects = [dict(type='placement_silkscreen_conflict', status='open', verdict='defect', native=v)
               for v in after['violations'] if v['type'] in SILK_CONFLICTS]
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
    args = parser.parse_args()
    produce(args.board, Path('/work'), targets=args.target)
    return 0
