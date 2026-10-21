"""Coordinator carry of receipted placement defects (AGENTEE-333).

The baseline and check declarations stay sealed. This additive scope declaration
binds immutable receipt artifacts to each successor candidate, just as rating
provenance does. A writer cannot erase carry by changing a board or by running
DRC without a later adopted placement receipt.
"""
import copy
import hashlib
import json
from pathlib import Path
import re

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
GATED_KINDS = frozenset({'fab_export', 'fab_package', 'cpl_rotation', 'fab_consistency', 'jlc_dfm'})
# Board lineage stamp (AGENTEE-351): title block comment 9 names each carry digest of the
# placement results that published open defects on this board's ancestry. It travels with
# the board bytes, so a re-scoped copy cannot release without its placement evidence.
# KiCad 10 keeps board-level properties in the project, not the saved board, so the
# title block is the only persistent board field; an occupied comment 9 refuses.
STAMP = 'agent-ee.placement-lineage/1'
STAMP_COMMENT = '9'
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


def unstamped(nodes):
    """Board-level nodes without the lineage stamp, and its digests (None when unstamped)."""
    kept, found = [], None
    for node in nodes:
        if isinstance(node, list) and node and node[0] == 'title_block':
            comments = [c for c in node[1:] if isinstance(c, list) and c[:2] == ['comment', STAMP_COMMENT]]
            if any(len(c) == 3 and isinstance(c[2], str) and c[2].startswith(STAMP) for c in comments):
                if found is not None or len(comments) != 1:
                    raise ValueError('ambiguous placement lineage stamp')
                found = _stamp_digests(comments[0][2])
                node = [c for c in node if c is not comments[0]]
                if len(node) == 1:
                    continue
        kept.append(node)
    return kept, found


def stamp(data):
    """Lineage stamp digests of saved board bytes; None when the board carries no stamp.

    Always parsed with the shared parser, so an escaped marker cannot evade a byte search.
    Only title block comment 9 is the stamp; the marker text anywhere else is user text.
    """
    from .symbol_library import parse_sexpr
    try:
        root = parse_sexpr(data)
    except ValueError:
        root = None
    if root is None or root[0] != 'kicad_pcb':
        # No title block can be read, so any marker, escaped or not, refuses.
        if STAMP.encode() in data.replace(b'\\', b''):
            raise ValueError('placement lineage stamp in a board the shared parser cannot read')
        return None
    return unstamped(root[1:])[1]


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
    """Validated helper artifact bundle of this turn, or None if no helper ran."""
    from .placement_adapter import STEP, qualify_receipt
    from .stage_done import _receipts
    from .stage_receipts import validate_receipt
    if str(number) not in state.get('placement_records', {}):
        return None
    work = Path(root) / 'implementations' / str(number)
    found = None
    for k, sha, receipt in _receipts(root, state, str(number)):
        if receipt['spec']['step'] != STEP or receipt['status'] != 'completed':
            continue
        record_path = work / 'placement-adapter.json'
        if digest(record_path) != state.get('placement_records', {}).get(str(number)):
            raise ValueError('placement defect carry lacks its anchored adapter record')
        record = read_json(record_path)
        link = read_json(work / 'stage-receipts.json')['receipts'][k - 1]
        path = work / link['path']
        source = read_json(Path(state['history'][number - 1]['run']) / 'candidate.json')
        receipt = validate_receipt(path.parent, binding={'implementation': number,
            'source_candidate_id': source['candidate_id'], 'receipt_index': k})
        if digest(path) != sha or record['status'] != 'proposed':
            raise ValueError('placement defect carry receipt is not an adopted proposal')
        qualified = qualify_receipt(path.parent, receipt, record['board'], record['protected'])
        if (record['helper']['receipt_sha256'] != sha or
                record['helper']['result_sha256'] != qualified['result_sha256']):
            raise ValueError('placement defect carry differs from its qualified helper')
        names = [*FILES, *([ARTWORK] if ARTWORK in receipt['artifacts'] else [])]
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
    from .stage_receipts import RECEIPT_SCHEMA
    if (receipt.get('schema') != RECEIPT_SCHEMA or receipt.get('status') != 'completed' or
            receipt.get('identity') != identity({k: v for k, v in receipt.items() if k != 'identity'})):
        raise ValueError('carried placement receipt identity or status invalid')
    prefix = str(Path(link['receipt']).parent)
    loaded = {}
    for name in [*FILES, *([ARTWORK] if ARTWORK in receipt['artifacts'] else [])]:
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
    drc, execution = read_json(loaded[DRC]), read_json(loaded[EXECUTION])
    if (execution.get('execution') != 'ok' or execution.get('exit_code') != 0 or
            set(drc.get('included_severities', [])) != {'error', 'warning', 'exclusion'} or
            not isinstance(drc.get('violations'), list) or not isinstance(drc.get('ignored_checks'), list) or
            any(c.get('key', '').startswith('silk_') for c in drc.get('ignored_checks', []))):
        raise ValueError('carried placement DRC evidence incomplete')
    return report, loaded, drc


def _identities(path):
    """Saved native item identities and marking content, using the shared parser."""
    from .symbol_library import children, parse_sexpr
    root = parse_sexpr(Path(path).read_bytes())
    result = {}
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
        identities = _identities(loaded[BOARD])
        moved = {m['uuid'] for m in read_json(loaded[ARTWORK]).get('moved_fields', [])} if ARTWORK in loaded else set()
        conflicts = {i['uuid'] for v in drc['violations'] if v['type'].startswith('silk_') for i in v['items']}
        diagnostics = {d['uuid'] for d in report['open_defects'] if 'uuid' in d}
        current = report['board_sha256'] == manifest['files'].get(manifest['scope']['board'])
        remaining = [(d, source, original) for d, source, original in active
                     if not _closes(d, original, identities, conflicts | diagnostics, moved, current)]
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


def _closes(defect, original, current, conflicts, moved, current_board):
    ids = _items(defect)
    markings = {u for u in ids & current.keys() if current[u][1][0] == 'property' or
                current[u][1][0].startswith(('fp_', 'gr_'))}
    return (current_board and ids <= current.keys() and ids <= original.keys() and
            all(current[u][:2] == original[u][:2] for u in ids) and
            bool(markings) and not (markings & conflicts) and
            ('native' in defect or all(u in moved or current[u][2] != original[u][2] for u in ids)))


def gated(spec):
    return spec.get('stage') in ('verification', 'fab', 'release') or spec.get('kind') in GATED_KINDS


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
    if not lineage(manifest):
        if (check.get('execution') == 'ok' and
                gated(next(c for c in manifest['scope']['checks'] if c['id'] == check['id'])) and
                _board_stamp(manifest, Path(run) / 'inputs')):
            raise ValueError('stamped board passed a downstream gate without its placement lineage')
        return
    spec = next(c for c in manifest['scope']['checks'] if c['id'] == check['id'])
    if not gated(spec):
        return
    defects = open_defects(manifest, Path(run) / 'inputs', lambda: _recorded(run, check['id'], manifest))
    if not defects:
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
    """Open carry for a release artifact; a closure needs the check's native witness."""
    return open_defects(manifest, Path(run) / 'inputs', lambda: _recorded(run, check.get('id'), manifest))
