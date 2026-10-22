"""Coordinator carry of receipted placement defects (AGENTEE-333).

The baseline and check declarations stay sealed. This additive scope declaration
binds immutable receipt artifacts to each successor candidate, just as rating
provenance does. A writer cannot erase carry by changing a board or by running
DRC without a later adopted placement receipt.
"""
import copy
import hashlib
import json
import re
from pathlib import Path

from .candidate import safe_relative
from .storage import digest, identity, json_bytes, read_json, write_json

DIRECTORY = 'placement-evidence'
DECLARATION = 'placement_provenance'
REPORT = 'placement/placement-result.json'
BOARD = 'placement/placed/candidate.kicad_pcb'
DRC = 'placement/placed/native-drc.json'
ARTWORK = 'placement/placed/placement-silkscreen.json'
EXECUTION = 'placement/placed/native-drc-execution.json'
FILES = (REPORT, BOARD, DRC, EXECUTION)
GATED_KINDS = frozenset({
    'fab_export', 'fab_package', 'cpl_rotation', 'fab_consistency', 'jlc_dfm',
    'fab_roundtrip', 'fab_profile', 'dfm_geometry', 'dfa_dft', 'assembly_readiness',
    'jlc_cost_estimate', 'drc', 'part_binding', 'package_consistency', 'parts_subset',
    'land_pattern', 'catalog', 'part_selection',
})
# Board lineage stamp (AGENTEE-351): a title block comment names each carry digest of the
# placement results that published open defects on this board's ancestry. It travels with
# the board bytes, so a re-scoped copy cannot release without its placement evidence.
# KiCad 10 keeps board-level properties in the project, not the saved board, so the
# title block is the only persistent board field. The writer prefers comment 9 and keeps
# user text: an occupied slot moves the stamp to the highest free one (AGENTEE-412).
STAMP = 'agent-ee.placement-lineage/1'
STAMP_COMMENT = '9'
STAMP_SLOTS = tuple(str(n) for n in range(9, 0, -1))
# Gated admission recomputes native DRC on the captured board before any closure
# (AGENTEE-351 H2): imported evidence cannot close carry by self-consistency.
VERIFICATION = 'placement-lineage'
WITNESS = 'placement-lineage.json'
WITNESS_SCHEMA = 'agent-ee.placement-lineage-verification/1'


def carry_digest(defects):
    """Identity of one placement result's open defects, from their native item identities."""
    return identity(sorted(sorted(_items(d)) for d in defects))


def _stamp_digests(value):
    words = value.split(' ')
    if (words[0] != STAMP or len(words) < 2 or len(set(words[1:])) != len(words) - 1 or
            any(len(w) != 64 or w.strip('0123456789abcdef') for w in words[1:])):
        raise ValueError('malformed placement lineage stamp')
    return words[1:]


def _claims(node):
    """Whether any string in a parsed node, at any depth, begins with the stamp marker."""
    return any(_claims(v) if isinstance(v, list) else isinstance(v, str) and v.startswith(STAMP)
               for v in node)


def unstamped(nodes):
    """Board-level nodes without the lineage stamp, and its digests (None when unstamped)."""
    kept, found = [], None
    for node in nodes:
        if isinstance(node, list) and node and node[0] == 'title_block':
            comments = [c for c in node[1:] if isinstance(c, list) and c[:1] == ['comment']]
            # A comment that claims the stamp in any other shape (missing or invalid slot,
            # extra element or list) is a malformed claim, not user text (AGENTEE-412 H1).
            claims = [c for c in comments if _claims(c[1:])]
            if any(len(c) != 3 or not isinstance(c[2], str) or not c[2].startswith(STAMP) for c in claims):
                raise ValueError('malformed placement lineage stamp claim')
            if claims:
                # One claim in one unambiguous comment slot; a repeated slot hides which one KiCad reads.
                if (found is not None or len(claims) != 1 or claims[0][1] not in STAMP_SLOTS or
                        sum(c[1:2] == claims[0][1:2] for c in comments) != 1):
                    raise ValueError('ambiguous placement lineage stamp')
                found = _stamp_digests(claims[0][2])
                node = [c for c in node if c is not claims[0]]
                if len(node) == 1:
                    continue
        kept.append(node)
    return kept, found


def _root(data):
    from .symbol_library import parse_sexpr
    try:
        root = parse_sexpr(data)
    except ValueError:
        return None
    return root if root and root[0] == 'kicad_pcb' else None


def stamp(data):
    """Lineage stamp digests of saved board bytes; None when the board carries no stamp.

    Always parsed with the shared parser, so an escaped marker cannot evade a byte search.
    Only a title block comment that begins with the marker is the stamp; the marker text
    anywhere else (title, company, revision, a comment's later words, board text) is user text.
    """
    root = _root(data)
    if root is None:
        # No title block can be read, so any marker, escaped or not, refuses.
        if STAMP.encode() in data.replace(b'\\', b''):
            raise ValueError('placement lineage stamp in a board the shared parser cannot read')
        return None
    return unstamped(root[1:])[1]


def stamp_slot(data):
    """Title block comment the writer records the stamp in: the stamp's own slot, else
    comment 9, else the highest comment without user text. User text is never replaced."""
    root = _root(data)
    if root is None:
        raise ValueError('placement lineage stamp target is not a board the shared parser can read')
    stamp(data)
    occupied = set()
    for node in root[1:]:
        if isinstance(node, list) and node and node[0] == 'title_block':
            for c in node[1:]:
                if isinstance(c, list) and c[:1] == ['comment'] and len(c) >= 2:
                    if len(c) == 3 and isinstance(c[2], str) and c[2].startswith(STAMP):
                        return c[1]
                    if len(c) != 3 or c[2] != '':
                        occupied.add(c[1])
    for slot in STAMP_SLOTS:
        if slot not in occupied:
            return slot
    raise ValueError('every title block comment holds user text; placement lineage cannot be recorded')


def stamp_violations(before, after):
    """A writer may extend the stamp with a new digest; it may never drop or rewrite one."""
    if before and (after is None or after[:len(before)] != before):
        return ['placement lineage stamp removed or rewritten']
    return []


def _board_stamp(manifest, inputs):
    board = manifest.get('scope', {}).get('board')
    if inputs is None or not isinstance(board, str) or board not in manifest.get('files', {}):
        return None
    path = Path(inputs) / str(safe_relative(board))
    return stamp(path.read_bytes()) if path.is_file() and not path.is_symlink() else None


def validate_campaign(scope):
    if DECLARATION in scope or any(Path(i['at']).parts[0] == DIRECTORY for i in scope.get('inputs', [])):
        raise ValueError('placement provenance is coordinator-owned; sealed scope cannot supply it')


def adopted(root, state, number):
    """Validated helper artifact bundle of this turn, or None if no helper ran.

    Every receipt is read only through the coordinator's anchored stage-receipt
    index (state['stage_receipts']); placer receipts also need placement_records.
    """
    from .placement_adapter import STEP, qualify_receipt
    from .stage_done import _receipts
    from .stage_receipts import validate_receipt
    work = Path(root) / 'implementations' / str(number)
    found = None
    from . import routed_artwork
    for k, sha, receipt in _receipts(root, state, str(number)):
        artwork = receipt['spec']['step'] == routed_artwork.STEP
        if not artwork and str(number) not in state.get('placement_records', {}):
            continue
        if receipt['spec']['step'] not in (STEP, routed_artwork.STEP) or receipt['status'] != 'completed':
            continue
        link = read_json(work / 'stage-receipts.json')['receipts'][k - 1]
        path = work / link['path']
        source = read_json(Path(state['history'][number - 1]['run']) / 'candidate.json')
        receipt = validate_receipt(path.parent, binding={'implementation': number,
            'source_candidate_id': source['candidate_id'], 'receipt_index': k})
        if digest(path) != sha:
            raise ValueError('carried helper receipt differs from anchored receipt')
        if artwork:
            routed_artwork.qualify_receipt(path.parent, receipt, source['scope']['board'],
                                           state['launch_environment']['policy_files'])
        else:
            record_path = work / 'placement-adapter.json'
            if digest(record_path) != state.get('placement_records', {}).get(str(number)):
                raise ValueError('placement defect carry lacks its anchored adapter record')
            record = read_json(record_path)
            if record['status'] != 'proposed':
                raise ValueError('placement defect carry receipt is not an adopted proposal')
            qualified = qualify_receipt(path.parent, receipt, record['board'], record['protected'])
            if (record['helper']['receipt_sha256'] != sha or
                    record['helper']['result_sha256'] != qualified['result_sha256']):
                raise ValueError('placement defect carry differs from its qualified helper')
        names = [*FILES, *([ARTWORK] if ARTWORK in receipt['artifacts'] else [])]
        if artwork:
            names.append(routed_artwork.SOURCE)
        data = {'receipt.json': path.read_bytes()}
        for name in names:
            artifact = path.parent / 'run' / name
            if receipt['artifacts'].get(name) != digest(artifact):
                raise ValueError(f'placement defect carry artifact not sealed: {name}')
            data[name] = artifact.read_bytes()
        prefix = f'{DIRECTORY}/{sha}'
        found = {'receipt': f'{prefix}/receipt.json', 'sha256': sha}, {
            f'{prefix}/{name}': value for name, value in data.items()}
    return found


def proposal_scope(scope, work, root, state):
    """Append an adopted helper's artifacts; inherit all earlier evidence unchanged."""
    scope = copy.deepcopy(scope)
    found = adopted(root, state, int(Path(work).name))
    if found is None:
        return scope
    link, data = found
    # No carry is introduced for historically clean placement candidates.
    report = json.loads(next(v for p, v in data.items() if p.endswith('/' + REPORT)))
    if not scope.get(DECLARATION) and not report.get('open_defects'):
        return scope
    target = Path(work) / DIRECTORY
    target.mkdir()
    inherited = [i for i in scope['inputs'] if i['at'] == DIRECTORY]
    if inherited:
        import shutil
        shutil.copytree(inherited[0]['path'], target, dirs_exist_ok=True)
    for name, value in data.items():
        dest = Path(work) / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(value)
    scope['inputs'] = [i for i in scope['inputs'] if i['at'] != DIRECTORY]
    scope['inputs'].append({'path': str(target), 'at': DIRECTORY})
    scope[DECLARATION] = [*scope.get(DECLARATION, []), link]
    write_json(target / 'index.json', scope[DECLARATION])
    return scope


def delivered_view(captured, delivered):
    view = {p: sha for p, sha in captured.items() if p.split('/')[0] != DIRECTORY}
    view.update({p: sha for p, sha in delivered.items() if p.split('/')[0] == DIRECTORY})
    return dict(sorted(view.items()))


def verify_candidate(root, state, number, manifest, inputs=None):
    """Bind carry to the adopted receipt lineage; with `inputs`, the board stamp is kept too."""
    expected, owed = [], {}
    if number > 1:
        previous = read_json(Path(state['history'][number - 2]['run']) / 'candidate.json')
        if inputs is not None:
            before = _board_stamp(previous, Path(state['history'][number - 2]['run']) / 'inputs')
            if stamp_violations(before, _board_stamp(manifest, inputs)):
                raise ValueError('candidate dropped or rewrote its placement lineage stamp')
        expected = list(previous['scope'].get(DECLARATION, []))
        owed = {p: sha for p, sha in previous['files'].items() if p.split('/')[0] == DIRECTORY}
        work = Path(root) / 'implementations' / str(number - 1)
        if manifest.get('scope_path') == str(work / 'proposal.scope.json'):
            found = adopted(root, state, number - 1)
            if found:
                link, data = found
                report = json.loads(next(v for p, v in data.items() if p.endswith('/' + REPORT)))
                if expected or report.get('open_defects'):
                    expected.append(link)
                    owed.update({p: hashlib.sha256(v).hexdigest() for p, v in data.items()})
                    owed[f'{DIRECTORY}/index.json'] = hashlib.sha256(json_bytes(expected)).hexdigest()
    files = {p: sha for p, sha in manifest['files'].items() if p.split('/')[0] == DIRECTORY}
    if manifest['scope'].get(DECLARATION, []) != expected or files != owed:
        raise ValueError('candidate placement provenance differs from its adopted receipt lineage')


def _read(manifest, inputs, rel, sha=None):
    rel = str(safe_relative(rel))
    path = Path(inputs) / rel
    wanted = manifest['files'].get(rel)
    if not wanted or (sha is not None and sha != wanted) or path.is_symlink() or digest(path) != wanted:
        raise ValueError(f'carried placement evidence missing or changed: {rel}')
    return path


def _bundle(manifest, inputs, link):
    if not isinstance(link, dict) or set(link) != {'receipt', 'sha256'}:
        raise ValueError('malformed carried placement receipt link')
    path = _read(manifest, inputs, link['receipt'], link['sha256'])
    receipt = read_json(path)
    from .stage_receipts import RECEIPT_SCHEMAS, legacy_source_refusal
    if (receipt.get('schema') not in RECEIPT_SCHEMAS or receipt.get('status') != 'completed' or
            receipt.get('identity') != identity({k: v for k, v in receipt.items() if k != 'identity'})):
        raise ValueError('carried placement receipt identity or status invalid')
    legacy_source_refusal(receipt)
    prefix = str(Path(link['receipt']).parent)
    from . import routed_artwork
    artwork = receipt['spec']['step'] == routed_artwork.STEP
    loaded = {}
    names = [*FILES, *([ARTWORK] if ARTWORK in receipt['artifacts'] else [])]
    if artwork:
        names.append(routed_artwork.SOURCE)
    for name in names:
        loaded[name] = _read(manifest, inputs, f'{prefix}/{name}', receipt['artifacts'].get(name))
        if receipt['artifacts'].get(name) is None:
            raise ValueError('carried placement receipt lacks required artifact')
    report = read_json(loaded[REPORT])
    board = manifest['scope']['board']
    if (report.get('schema') != 'agent-ee.placed-candidate/1' or report.get('status') != 'placed' or
            report.get('board_sha256') != digest(loaded[BOARD]) or
            report.get('board_sha256') != receipt['outputs'].get(board) or
            report.get('source_sha256') != receipt['inputs'].get(board) or
            report.get('open_defects') != report.get('native_drc_comparison', {}).get('open_defects') or
            (report.get('open_defects') and (report.get('outcome'), report.get('verdict')) != ('defect', 'defect'))):
        raise ValueError('carried placement result is not bound to its receipt')
    if artwork:
        proof = routed_artwork.write_scope(loaded[routed_artwork.SOURCE].read_bytes(), loaded[BOARD].read_bytes(),
                                           **routed_artwork.receipt_scope(receipt['spec']['argv']))
        if (report.get('producer') != routed_artwork.STEP or report.get('write_scope') != proof or
                digest(loaded[routed_artwork.SOURCE]) != receipt['inputs'].get(board)):
            raise ValueError('carried artwork scope differs from its receipt')
    drc, execution = read_json(loaded[DRC]), read_json(loaded[EXECUTION])
    if (execution.get('execution') != 'ok' or execution.get('exit_code') != 0 or
            set(drc.get('included_severities', [])) != {'error', 'warning', 'exclusion'} or
            not isinstance(drc.get('violations'), list) or not isinstance(drc.get('ignored_checks'), list) or
            any(c.get('key', '').startswith('silk_') for c in drc.get('ignored_checks', []))):
        raise ValueError('carried placement DRC evidence incomplete')
    return report, loaded, drc


class _SavedIdentities(dict):
    """Saved identities with the receipt's board-bound native containment witness."""
    def __init__(self, path, witness):
        super().__init__()
        self.board_bytes = Path(path).read_bytes()
        self._contained = set()
        if not isinstance(witness, dict) or set(witness) != {'board_sha256', 'markings'}:
            return
        markings = witness['markings']
        if (witness['board_sha256'] == hashlib.sha256(self.board_bytes).hexdigest() and
                isinstance(markings, list) and all(isinstance(u, str) and u for u in markings) and
                len(markings) == len(set(markings))):
            self._contained = set(markings)

    def contained(self):
        # Replay consumes sealed evidence only; missing/invalid witnesses close nothing.
        return self._contained


def _identities(path, witness=None):
    """Saved native item identities and marking content, using the shared parser."""
    from .symbol_library import children, parse_sexpr
    result = _SavedIdentities(path, witness)
    root = parse_sexpr(result.board_bytes)
    def visit(node, owner):
        uid = children(node, 'uuid')
        if uid:
            key = uid[0][1]
            if key in result:
                raise ValueError('ambiguous carried artwork identity')
            # Position may change; marking content, layer, style and ownership may not.
            content = [v for v in node if not isinstance(v, list) or v[0] not in ('at', 'uuid')]
            if node[0] == 'footprint':
                owner = key
                content = [node[0], node[1]]
            result[key] = owner, content, children(node, 'at')
        for child in node[1:]:
            if isinstance(child, list):
                visit(child, owner)
    visit(root, None)
    return result


def _items(defect):
    if defect.get('type') != 'placement_silkscreen_conflict' or defect.get('status') != 'open' or defect.get('verdict') != 'defect':
        raise ValueError('malformed carried placement defect')
    if 'native' in defect:
        items = defect['native'].get('items')
        if not isinstance(items, list) or not items or any(not i.get('uuid') for i in items):
            raise ValueError('carried silk finding lacks native field/graphic identity')
        return {i['uuid'] for i in items}
    if not isinstance(defect.get('uuid'), str) or not defect['uuid']:
        raise ValueError('carried side-flip diagnostic lacks field identity')
    return {defect['uuid']}


# This recognizes only the lexical form of writer-produced track spans. Structural
# parsing and bounds remain the shared S-expression reader's responsibility.
_WRITER_TOKEN = re.compile(r'(?:[ \t\n]+|\r\n|[()]|[A-Za-z0-9_.+-]+(?=[ \t\r\n()]|$)|'
                           r'"[^"\\#;\x00-\x1f\x7f-\x9f]*"(?=[ \t\r\n()]|$))')


def _writer_track_span(source):
    depth, pos = 0, 0
    while pos < len(source):
        token = _WRITER_TOKEN.match(source, pos)
        if token is None:
            return False
        value = token.group()
        if value == '(':
            depth += 1
        elif value == ')':
            depth -= 1
            if depth < 0 or (depth == 0 and token.end() != len(source)):
                return False
        pos = token.end()
    return depth == 0


def same_artwork(before, after):
    """Prove identical markings and obstructions except masked board copper tracks.

    Unknown track children (including solder-mask exposure) stay in the comparison.
    Vias, zones, footprint transforms, pads, mask, setup and every artwork token
    must remain identical. No electrical or routing verdict follows from this.
    """
    from .symbol_library import children, parse_sexpr
    def spans(text):
        # Byte spans of the root's direct children; quoted strings skip escaped characters.
        result, depth, start, pos, line_start = [], 0, None, 0, True
        while pos < len(text):
            char = text[pos]
            # KiCad's lexer skips a line whose first non-blank (space, tab, CR, NUL) byte is '#'.
            if char == '\n':
                line_start = True
            elif char not in ' \t\r\0':
                if line_start and char == '#':
                    raise ValueError('artwork comparison refuses comment syntax')
                line_start = False
            if char == '"':
                begin = pos
                pos += 1
                while text[pos] != '"':
                    pos += 2 if text[pos] == '\\' else 1
                if depth == 1:
                    result.append(text[begin:pos + 1])
            elif char == ';':
                raise ValueError('artwork comparison refuses comment syntax')
            elif char == '(':
                depth += 1
                if depth == 2:
                    start = pos
            elif char == ')':
                if depth == 2:
                    result.append(text[start:pos + 1])
                depth -= 1
            elif not char.isspace() and depth == 1:
                end = pos
                while end < len(text) and not text[end].isspace() and text[end] not in '();"':
                    end += 1
                result.append(text[pos:end])
                pos = end - 1
            pos += 1
        return result
    def subject(path):
        data = Path(path).read_bytes()
        root = parse_sexpr(data)
        if root[0] != 'kicad_pcb':
            raise ValueError('artwork comparison requires a saved board')
        text = data.decode('utf-8')
        raw = spans(text)
        if len(raw) != len(root):
            raise ValueError('artwork comparison could not align saved-board nodes')
        kept = []
        # Raw bytes, not decoded atoms: the shared parser's escape decoding differs from KiCad's.
        for node, source in zip(root, raw):
            if isinstance(node, list) and node and node[0] in ('segment', 'arc'):
                if any(isinstance(n, list) and n and isinstance(n[0], list) for n in node[1:]):
                    raise ValueError('artwork comparison refuses nested-list track child')
                allowed = {'start', 'end', 'mid', 'width', 'layer', 'net', 'uuid', 'locked'}
                # KiCad refuses such boards; an exempt span must never hide a nested value.
                if any(isinstance(n, list) and n and n[0] in allowed and
                       any(isinstance(v, list) for v in n[1:]) for n in node[1:]):
                    raise ValueError('artwork comparison refuses nested-list track child value')
                layers = children(node, 'layer')
                if (len(layers) == 1 and len(layers[0]) == 2 and
                        layers[0][1] in ('F.Cu', 'B.Cu', *(f'In{i}.Cu' for i in range(1, 31))) and
                        all(isinstance(n, list) and n and n[0] in allowed for n in node[1:]) and
                        _writer_track_span(source)):
                    continue
            kept.append(source.encode('utf-8'))
        return kept
    return subject(before) == subject(after)


def acceptance(run, diagnostic):
    """Campaign acceptance is a gate even when no downstream check was declared."""
    if diagnostic['state'] != 'criteria_satisfied':
        return diagnostic
    run = Path(run)
    manifest = read_json(run / 'candidate.json')

    def native():
        # A closure is verified only by a gated check's sealed native DRC witness (AGENTEE-351 H2).
        for spec in manifest['scope'].get('checks', []):
            if gated(spec) and (run / 'work' / str(safe_relative(spec['id'])) / WITNESS).is_file():
                return _recorded(run, spec['id'], manifest)
        # Fail closed: acceptance never closes carry on unverified evidence.
        raise ValueError('placement carry closure at campaign acceptance is unverifiable: no gated check '
                         'sealed native DRC on the captured board')
    if open_defects(manifest, run / 'inputs', native):
        return {'state': 'escalated', 'reason': 'open_placement_defects'}
    return diagnostic


def silk_findings(report):
    """Every native silkscreen finding of a complete DRC report, by type and item identities."""
    if (not isinstance(report, dict) or not isinstance(report.get('violations'), list) or
            not isinstance(report.get('ignored_checks'), list) or
            not isinstance(report.get('included_severities'), list) or
            set(report['included_severities']) != {'error', 'warning', 'exclusion'}):
        raise ValueError('native DRC report did not evaluate every severity')
    if any(not isinstance(c, dict) or not isinstance(c.get('key'), str) or c['key'].startswith('silk_')
           for c in report['ignored_checks']):
        raise ValueError('native silkscreen DRC evaluation unavailable or explicitly ignored')
    found = []
    for finding in report['violations']:
        if not isinstance(finding, dict) or not isinstance(finding.get('type'), str):
            raise ValueError('native DRC contains malformed violations')
        if finding['type'].startswith('silk_'):
            items = finding.get('items')
            if (not isinstance(items, list) or not items or
                    any(not isinstance(i, dict) or not isinstance(i.get('uuid'), str) or not i['uuid'] for i in items)):
                raise ValueError('native silkscreen finding lacks item identities')
            found.append([finding['type'], sorted(i['uuid'] for i in items)])
    return sorted(found)


def _board(manifest):
    board = manifest.get('scope', {}).get('board')
    return str(safe_relative(board)) if isinstance(board, str) and board in manifest.get('files', {}) else None


def _argv(board):
    return ['kicad-cli', 'pcb', 'drc', '--format', 'json', '--severity-all',
            '--output', '/work/native-drc.json', f'/candidate/{board}']


def _recompute(ctx):
    """Native DRC on the captured board bytes, run in the tool sandbox; the witness binds it."""
    from . import executor
    from .checks import CheckError
    board = _board(ctx.manifest)
    path = ctx.inputs / board
    if path.is_symlink() or not path.is_file() or digest(path) != ctx.manifest['files'][board]:
        raise ValueError('captured board missing or changed; placement lineage cannot be verified')
    rules = path.with_suffix('.kicad_dru')
    if rules.is_file() and re.search(rb'\(\s*severity\s+"?ignore\b', rules.read_bytes(), re.I):
        raise ValueError('a custom DRC rule ignores findings; placement lineage cannot be verified natively')
    if ctx.remaining() <= 1:
        raise CheckError('interrupted', 'deadline exhausted before placement lineage verification could run')
    work = ctx.work / VERIFICATION
    rec = executor.execute(ctx.inputs, work, _argv(board), timeout=min(900, ctx.remaining()))
    ctx.evidence(*(f'{VERIFICATION}/{name}' for name in ('execution.json', 'console.log', 'native-drc.json')))
    if rec['execution'] != 'ok' or rec['exit_code'] != 0:
        raise CheckError('interrupted' if rec['execution'] == 'timeout' else 'malformed',
                         f"placement lineage verification: native DRC {rec['execution']} (exit {rec['exit_code']})")
    findings = silk_findings(read_json(work / 'native-drc.json'))
    write_json(ctx.work / WITNESS, {'schema': WITNESS_SCHEMA, 'board': board,
                                    'board_sha256': ctx.manifest['files'][board], 'argv': _argv(board),
                                    'native_drc_sha256': digest(work / 'native-drc.json'),
                                    'silk_findings': findings})
    ctx.evidence(WITNESS)
    return findings


def _recorded(run, cid, manifest):
    """Silk findings of a check's sealed native verification, rechecked from its report."""
    board = _board(manifest)
    work = Path(run) / 'work' / str(safe_relative(cid))
    report = work / VERIFICATION / 'native-drc.json'
    try:
        witness, execution = read_json(work / WITNESS), read_json(work / VERIFICATION / 'execution.json')
    except (OSError, ValueError) as exc:
        raise ValueError('gate admitted without native placement lineage verification') from exc
    if (board is None or witness.get('schema') != WITNESS_SCHEMA or witness.get('board') != board or
            witness.get('board_sha256') != manifest['files'][board] or witness.get('argv') != _argv(board) or
            execution.get('argv') != _argv(board) or execution.get('execution') != 'ok' or
            execution.get('exit_code') != 0 or report.is_symlink() or
            witness.get('native_drc_sha256') != digest(report) or
            silk_findings(read_json(report)) != witness.get('silk_findings')):
        raise ValueError('native placement lineage verification witness missing or changed')
    return witness['silk_findings']


def clean_provenance(findings, lineaged=False):
    """A release needs native DRC without any silkscreen conflict."""
    conflicts = [finding for finding in findings if finding[0].startswith('silk_')]
    if conflicts and lineaged:
        raise ValueError(f'board shows {len(conflicts)} native silkscreen conflict(s); its captured placement '
                         'lineage and stamp are not yet authenticated (AGENTEE-348), so release requires a '
                         'natively clean board')
    if conflicts:
        raise ValueError(f'board shows {len(conflicts)} native silkscreen conflict(s) but captured '
                         'no placement lineage; release requires its placement-evidence or a clean board')


def _needs_clean_provenance(manifest, inputs):
    # A stamp and an imported index are both self-asserted until AGENTEE-348, so
    # neither can waive the clean native witness at release (AGENTEE-351 H1).
    return bool(_board(manifest))


def prepare_release(ctx):
    """Capture clean provenance only at the package release boundary, never per check."""
    if _needs_clean_provenance(ctx.manifest, ctx.inputs):
        _recompute(ctx)


def open_defects(manifest, inputs, native=None):
    """Reconstruct open carry; only later receipted current artwork can close it.

    `native` returns silk findings natively recomputed on the captured board. Closure
    evidence must agree with it; without it, a would-be closure is unverifiable.
    """
    index = f'{DIRECTORY}/index.json'
    stamped = _board_stamp(manifest, inputs)
    if not lineage(manifest):
        if stamped:
            raise ValueError('board carries a placement lineage stamp but its campaign placement '
                             'evidence was not captured; release requires that lineage')
        return []
    indexed = read_json(_read(manifest, inputs, index))
    links = manifest['scope'].get(DECLARATION, indexed)
    if links != indexed:
        raise ValueError('placement provenance differs from its captured index')
    if not isinstance(links, list):
        raise ValueError('placement provenance must be ordered receipt links')
    active, bound = [], set()
    for link in links:
        report, loaded, drc = _bundle(manifest, inputs, link)
        if report['open_defects']:
            bound.add(carry_digest(report['open_defects']))
        identities = _identities(loaded[BOARD], report.get('containment'))
        moved = {m['uuid'] for m in read_json(loaded[ARTWORK]).get('moved_fields', [])} if ARTWORK in loaded else set()
        conflicts = {i['uuid'] for v in drc['violations'] if v['type'].startswith('silk_') for i in v['items']}
        diagnostics = {d['uuid'] for d in report['open_defects'] if 'uuid' in d}
        board = manifest['scope']['board']
        current = report['board_sha256'] == manifest['files'].get(board)
        if not current:
            # A dishonest candidate digest must not turn a known defect into unknown.
            path = Path(inputs) / str(safe_relative(board))
            try:
                if path.is_file() and not path.is_symlink() and digest(path) == manifest['files'].get(board):
                    current = same_artwork(loaded[BOARD], path)
            except (OSError, ValueError):
                # An unprovable edit cannot downgrade a known carried defect.
                current = False
        receipt = read_json(_read(manifest, inputs, link['receipt'], link['sha256']))
        # Bind a conservative superset of DRC's project/rule inputs, including
        # non-board stems. Added, removed or changed settings invalidate closure.
        def rules(files):
            return {p: sha for p, sha in files.items()
                    if not p.startswith(DIRECTORY + '/') and
                    Path(p).suffix in ('.kicad_pro', '.kicad_dru')}
        current = current and rules(receipt['inputs']) == rules(manifest['files'])
        remaining = [(d, source, original) for d, source, original in active
                     if not _closes(d, original, identities, conflicts | diagnostics, moved, current,
                                    report.get('producer') == 'agent-ee-routed-artwork' and
                                    receipt.get('spec', {}).get('step') == 'agent-ee-routed-artwork')]
        if len(remaining) != len(active):
            # Imported evidence never closes carry by self-consistency: native DRC on the
            # captured board must reproduce the claimed silkscreen result.
            if native is None:
                raise ValueError('placement carry closure is unverifiable without native DRC on the captured board')
            if native() != silk_findings(drc):
                raise ValueError('placement closure evidence differs from native DRC on the captured board')
        active = remaining
        for defect in report['open_defects']:
            _items(defect)
            active.append((defect, link, identities))
    if stamped and not set(stamped) <= bound:
        raise ValueError('placement lineage stamp is not bound to the captured placement evidence')
    defects = []
    for defect, source, original in active:
        value = dict(defect, source_receipt=source)
        if 'uuid' in defect and defect['uuid'] in original:
            node = original[defect['uuid']][1]
            if node[0] == 'property':
                value['field'] = node[1]
        defects.append(value)
    return defects


def _closes(defect, original, current, conflicts, moved, current_board, graphic_translation=False):
    ids = _items(defect)
    markings = {u for u in ids & current.keys() if current[u][1][0] == 'property' or
                current[u][1][0].startswith(('fp_', 'gr_'))}
    def same(u):
        if current[u][:2] == original[u][:2]:
            # Text angle lives in the position node; only its x/y may move.
            return ([at[3:] for at in current[u][2]] ==
                    [at[3:] for at in original[u][2]])
        from .routed_artwork import GRAPHICS, translated_content
        from .symbol_library import children
        old_owner, old, _ = original[u]
        new_owner, new, _ = current[u]
        return (graphic_translation and old_owner == new_owner and old[0] == new[0] and old[0] in GRAPHICS and
                old[0] not in ('fp_text', 'gr_text') and
                children(old, 'layer') in ([['layer', 'F.SilkS']], [['layer', 'B.SilkS']]) and
                translated_content(old) == translated_content(new))
    return (current_board and ids <= current.keys() and ids <= original.keys() and
            all(same(u) for u in ids) and
            bool(markings) and not (markings & conflicts) and
            (callable(getattr(current, 'contained', None)) and markings <= current.contained()) and
            ('native' in defect or all(u in moved or current[u][2] != original[u][2] for u in ids)))


def gated(spec):
    if placement_projection(spec):
        return False
    return spec.get('stage') in ('verification', 'fab', 'release') or spec.get('kind') in GATED_KINDS


def placement_projection(spec):
    """The placement-stage DRC projection grades carry instead of refusing it (AGENTEE-346)."""
    params = spec.get('params')
    return (spec.get('kind') == 'drc' and isinstance(params, dict) and
            params.get('projection') == 'placement-stage/1' and
            spec.get('stage') in (None, 'placement'))


def _carried_identity(violation):
    from .silk_artwork import SILK_CONFLICTS
    items = violation.get('items') if isinstance(violation, dict) else None
    if (violation.get('type') not in SILK_CONFLICTS or not isinstance(items, list) or not items or
            any(not isinstance(i, dict) or not isinstance(i.get('uuid'), str) or not i['uuid'] for i in items)):
        return None
    return violation['type'], tuple(sorted(i['uuid'] for i in items))


def carried_silk(defects, unwaived):
    """Raw locators of unwaived native silk conflicts that are exactly an open carried defect."""
    carried = {_carried_identity(d['native']) for d in defects if isinstance(d.get('native'), dict)}
    carried.discard(None)
    return sorted(i['raw_locator'] for i in unwaived if i['raw_locator'].startswith('/violations/') and
                  _carried_identity(i) in carried)


def projection_record(ctx, unwaived):
    """Open carry for the placement projection; its silk conflicts grade as carried, not violations."""
    from .checks import CheckError
    cache = []

    def native():
        # The full gate's closure contract (AGENTEE-351): native DRC on the captured board.
        if not cache:
            cache.append(_recompute(ctx))
        return cache[0]
    try:
        defects = open_defects(ctx.manifest, ctx.inputs, native)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CheckError('malformed', f'carried placement evidence refused: {exc}') from exc
    if not defects:
        return []
    carried = carried_silk(defects, unwaived)
    witness = dict(gate_record(ctx.cid, ctx.manifest['candidate_id'], defects), carried_findings=carried)
    write_json(ctx.work / 'placement-defects.json', witness)
    ctx.evidence('placement-defects.json')
    ctx.result['details'].update(open_defects=defects, carried_findings=carried)
    ctx.result['limitations'].append(
        f'{len(defects)} carried open placement silkscreen defect(s); {len(carried)} matching native '
        'silk finding(s) graded as carried, not stage-blocking; verification, fab and release refuse '
        'until a receipted closure')
    return carried


def before_check(ctx):
    """Return False with typed refusal when carried open defects block this gate."""
    if not gated(ctx.spec):
        return True
    from .checks import CheckError
    cache = []

    def native():
        if not cache:
            cache.append(_recompute(ctx))
        return cache[0]
    try:
        defects = open_defects(ctx.manifest, ctx.inputs, native)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CheckError('malformed', f'carried placement evidence refused: {exc}') from exc
    if not defects:
        return True
    witness = gate_record(ctx.cid, ctx.manifest['candidate_id'], defects)
    findings = witness['gate_findings']
    write_json(ctx.work / 'placement-defects.json', witness)
    ctx.evidence('placement-defects.json')
    ctx.result.update(execution='ok', outcome='defect', summary=f'gate refused: {len(defects)} carried open placement silkscreen defect(s)')
    ctx.result['details'].update(reason='open_placement_defects', open_defects=defects, gate_findings=findings)
    return False


def gate_record(cid, candidate_id, defects):
    findings = []
    for defect in defects:
        source = defect['source_receipt']['receipt']
        native = defect.get('native', {})
        names = [f"{i.get('description', 'field/graphic')} ({i['uuid']})" for i in native.get('items', [])]
        entity = '; '.join(names) if names else f"{defect.get('reference', '')} {defect.get('field', 'field')} ({defect['uuid']})"
        finding = {'check_id': cid, 'entity': f'{entity}; source receipt {source}',
                   'kind': 'placement_silkscreen_conflict'}
        if finding not in findings:
            findings.append(finding)
    witness = {'candidate_id': candidate_id, 'open_defects': defects, 'gate_findings': findings}
    return witness


def lineage(manifest):
    return DECLARATION in manifest.get('scope', {}) or any(
        p.startswith(DIRECTORY + '/') for p in manifest.get('files', {}))


def verify_gate(run, check, manifest):
    """Reconstruct refusals from captured artifacts before sealing or releasing evidence."""
    if placement_projection(next(c for c in manifest['scope']['checks'] if c['id'] == check['id'])):
        # Structural containment and parity are reconstructed with or without lineage.
        return verify_projection(run, check, manifest)
    if not lineage(manifest):
        if (check.get('execution') == 'ok' and
                gated(next(c for c in manifest['scope']['checks'] if c['id'] == check['id'])) and
                _board_stamp(manifest, Path(run) / 'inputs')):
            raise ValueError('stamped board passed a downstream gate without its placement lineage')
        spec = next(c for c in manifest['scope']['checks'] if c['id'] == check['id'])
        if (spec.get('kind') == 'fab_package' and _board(manifest) and
                (check.get('execution'), check.get('outcome')) == ('ok', 'pass')):
            release_defects(run, check, manifest)
        return
    spec = next(c for c in manifest['scope']['checks'] if c['id'] == check['id'])
    if not gated(spec):
        return
    defects = open_defects(manifest, Path(run) / 'inputs', lambda: _recorded(run, check['id'], manifest))
    if not defects:
        if (spec.get('kind') == 'fab_package' and
                (check.get('execution'), check.get('outcome')) == ('ok', 'pass') and
                _needs_clean_provenance(manifest, Path(run) / 'inputs')):
            release_defects(run, check, manifest)
        if check.get('details', {}).get('reason') == 'open_placement_defects':
            raise ValueError('carried placement refusal without open defects')
        return
    expected = gate_record(check['id'], manifest['candidate_id'], defects)
    if (check['execution'], check['outcome']) != ('ok', 'defect') or any(
            check.get('details', {}).get(k) != expected[k] for k in ('open_defects', 'gate_findings')):
        raise ValueError('downstream check did not retain its carried placement defects')
    path = Path(run) / 'work' / check['id'] / 'placement-defects.json'
    if read_json(path) != expected:
        raise ValueError('carried placement gate witness changed')


def release_defects(run, check, manifest):
    """Open carry for release; missing lineage requires a clean native witness."""
    def native():
        findings = _recorded(run, check.get('id'), manifest)
        _read(manifest, Path(run) / 'inputs', _board(manifest))
        return findings
    defects = open_defects(manifest, Path(run) / 'inputs', native)
    if not defects and _needs_clean_provenance(manifest, Path(run) / 'inputs'):
        clean_provenance(native(), lineage(manifest))
    return defects


def verify_projection(run, check, manifest):
    """Reconstruct the projection verdict with the runtime policy (checks.grade_drc): raw DRC
    admissibility (checks.drc_admissible), captured waivers, numeric provenance, retained
    structural containment and receipt-backed carry."""
    defects = open_defects(manifest, Path(run) / 'inputs', lambda: _recorded(run, check['id'], manifest))
    details = check.get('details', {})
    if not defects and ('open_defects' in details or 'carried_findings' in details):
        raise ValueError('carried placement grading without open defects')
    if check['execution'] != 'ok' or details.get('reason') in ('stage_blocked', 'stage_not_reached'):
        return
    from .violation_waivers import replay_unwaived
    from .checks import drc_admissible, drc_net_binding, grade_drc, require_later_full_drc
    spec = next(c for c in manifest['scope']['checks'] if c['id'] == check['id'])
    inputs, work = Path(run) / 'inputs', Path(run) / 'work' / check['id']
    # The runtime's unsupported and malformed refusals hold at replay too (AGENTEE-407):
    # an ok verdict never seals over a report the runtime check could not grade.
    require_later_full_drc(manifest['scope']['checks'], spec)
    drc_net_binding(inputs, manifest['scope']['board'])
    report = read_json(work / 'drc.json')
    if not isinstance(report, dict) or any(
            not isinstance(report.get(k), list) or any(not isinstance(v, dict) for v in report[k])
            for k in ('violations', 'unconnected_items', 'schematic_parity')):
        raise ValueError('placement projection native report is malformed')
    sources = drc_admissible(inputs, manifest['scope']['board'], report,
                             (work / 'console.log').read_text(errors='replace'))[0]
    unwaived = replay_unwaived(spec, manifest['scope'], manifest, inputs, report)
    carried = carried_silk(defects, unwaived) if defects else []
    failing = grade_drc(unwaived, sources, spec['params']['projection'], carried,
                        replay_containment(run, check, sources))[2]
    if check['outcome'] != ('defect' if failing else 'pass') or (
            check['outcome'] == 'pass' and details.get('containment') is not None):
        raise ValueError('placement projection verdict differs from its unwaived native findings, '
                         'structural containment and receipt-backed carry')
    if not defects:
        return
    witness = read_json(Path(run) / 'work' / check['id'] / 'placement-defects.json')
    if (details.get('open_defects') != defects or details.get('carried_findings') != carried or
            witness != dict(gate_record(check['id'], manifest['candidate_id'], defects),
                            carried_findings=carried)):
        raise ValueError('placement projection carried grading differs from open carry')


def replay_containment(run, check, sources):
    """Retained supplemental copper containment, or None when the check recorded it unavailable."""
    from .design_rule_provenance import classify_finding
    if check.get('details', {}).get('containment') is not None:
        return None
    work = Path(run) / 'work' / check['id'] / 'containment'
    record = read_json(work / 'execution.json')
    if not isinstance(record, dict) or record.get('execution') != 'ok' or record.get('exit_code') != 0:
        raise ValueError('placement projection copper containment did not complete')
    findings = read_json(work / 'copper-containment.json')
    if not isinstance(findings, list) or any(not isinstance(f, dict) for f in findings):
        raise ValueError('placement projection copper containment is malformed')
    return [classify_finding(f, sources) for f in findings]
