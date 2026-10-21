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


def write_scope(before, after):
    """Compare every saved token except allowed silk translations; unknowns refuse."""
    from .silk_artwork import DECLARATION, movable_names
    # The shared parser decodes escapes; token equality alone cannot prove the
    # saved spelling KiCad renders. Refuse this unsupported input conservatively.
    if any(b'\\' in data for data in (before, after)):
        raise ValueError('backslash in artwork comparison is unsupported')
    roots = [parse_sexpr(b) for b in (before, after)]
    moves = []
    def compare(b, a, names=frozenset()):
        if b == a:
            return
        if not isinstance(b, list) or not isinstance(a, list) or b[:1] != a[:1]:
            raise ValueError('non-silk board token changed')
        if b[0] == 'footprint':
            decl = [p[2] for p in children(b, 'property') if p[1] == DECLARATION]
            if len(decl) > 1:
                raise ValueError('duplicate movable silk declaration')
            names = movable_names(decl[0] if decl else None)
        silk = children(b, 'layer') in ([['layer', 'F.SilkS']], [['layer', 'B.SilkS']])
        field = b[0] == 'property' and len(b) > 2 and b[1] in names
        if silk and (field or b[0] in GRAPHICS):
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
                equal = content(b) == content(a)
            else:
                equal = translated_content(b) == translated_content(a)
            if not equal:
                raise ValueError('silk edit exceeds translation authority')
            moves.append(uid[0][1])
            return
        if len(b) != len(a):
            raise ValueError('board item added or removed')
        for old, new in zip(b, a):
            if old != new:
                compare(old, new, names)
    if any(r[0] != 'kicad_pcb' for r in roots):
        raise ValueError('artwork requires saved boards')
    # Reject duplicate identities even on otherwise unchanged subtrees.
    for root in roots:
        seen = set()
        def identities(n):
            uid = children(n, 'uuid')
            if uid:
                if len(uid) != 1 or len(uid[0]) != 2 or uid[0][1] in seen:
                    raise ValueError('ambiguous artwork identity')
                seen.add(uid[0][1])
            for c in n[1:]:
                if isinstance(c, list):
                    identities(c)
        identities(root)
    compare(*roots)
    return {'status': 'allowed', 'before_sha256': hashlib.sha256(before).hexdigest(),
            'after_sha256': hashlib.sha256(after).hexdigest(), 'moved_identities': sorted(moves)}


def step(tool_dir, board, *, stage='verification', timeout_s=300):
    """Create an immutable source snapshot and an ordinary campaign implementation step."""
    from .placement_adapter import tool_snapshot, _relative
    _relative(board, 'artwork board')
    tool_dir = tool_snapshot(Path(tool_dir).resolve())
    (tool_dir / 'artwork.py').write_text(LAUNCHER)
    return {'schema': 'agent-ee.stage-step/1', 'stage': stage, 'step': STEP,
            'argv': ['/usr/bin/python3', '-B', '-I', f'{TOOL_AT}/artwork.py', f'/candidate/{board}'],
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
    if (spec['argv'] != ['/usr/bin/python3', '-B', '-I', f'{TOOL_AT}/artwork.py', f'/candidate/{board}'] or
            spec.get('source') != TOOL_AT or len(spec['mounts']) != 1 or
            spec['mounts'][0]['at'] != TOOL_AT):
        raise ValueError('artwork receipt invokes a different producer')
    tree = receipt['tool']['source_tree']
    # Bind the entire retained tool tree to the campaign's package pin.
    if tree != {'path': TOOL_AT, **tree_identity_from_files(expected)}:
        raise ValueError('artwork source differs from campaign source pin')
    root = Path(receipt_dir)
    proof = write_scope((root / 'inputs' / board).read_bytes(), (root / 'run' / BOARD).read_bytes())
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


def produce(source, root):
    """Write a disposable candidate, retain native DRC and fail on any new non-silk finding."""
    import pcbnew as k
    from .placer_runner import _native_drc, _drc_comparison
    from .silk_artwork import relocate, SILK_CONFLICTS
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
    artwork = relocate(board, before['violations'], graphics=True)
    if not k.SaveBoard(str(output), board, True):
        raise ValueError('native artwork save failed')
    for suffix, payload in payloads.items():
        if payload is None:
            output.with_suffix(suffix).unlink(missing_ok=True)
        else:
            output.with_suffix(suffix).write_bytes(payload)
    proof = write_scope(source.read_bytes(), output.read_bytes())
    after = _native_drc(output, output.parent)
    comparison = _drc_comparison(before, after)
    if any(v['type'] not in SILK_CONFLICTS for v in comparison['added_findings']):
        raise ValueError('artwork introduced a non-silk native DRC finding')
    defects = [dict(type='placement_silkscreen_conflict', status='open', verdict='defect', native=v)
               for v in after['violations'] if v['type'] in SILK_CONFLICTS]
    comparison['open_defects'] = defects
    artwork.update(schema='agent-ee.placement-silkscreen/1', write_scope=proof)
    write_json(root / ARTWORK, artwork)
    report = {'schema': 'agent-ee.placed-candidate/1', 'status': 'placed', 'producer': STEP,
              'authority': 'disposable-unsealed-proposal', 'source_sha256': digest(source),
              'board_sha256': digest(output), 'write_scope': proof, 'open_defects': defects,
              'native_drc_comparison': comparison,
              'outcome': 'defect' if defects else 'informational',
              'verdict': 'defect' if defects else 'informational'}
    write_json(root / REPORT, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('board', type=Path)
    args = parser.parse_args()
    produce(args.board, Path('/work'))
    return 0
