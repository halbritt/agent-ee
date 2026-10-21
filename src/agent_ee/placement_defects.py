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


def verify_candidate(root, state, number, manifest):
    expected, owed = [], {}
    if number > 1:
        previous = read_json(Path(state['history'][number - 2]['run']) / 'candidate.json')
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
                allowed = {'start', 'end', 'mid', 'width', 'layer', 'net', 'uuid', 'locked'}
                layers = children(node, 'layer')
                if (len(layers) == 1 and len(layers[0]) == 2 and
                        layers[0][1] in ('F.Cu', 'B.Cu', *(f'In{i}.Cu' for i in range(1, 31))) and
                        all(isinstance(n, list) and n and n[0] in allowed for n in node[1:]) and
                        '\\' not in source):
                    continue
            kept.append(source.encode('utf-8'))
        return kept
    return subject(before) == subject(after)


def acceptance(run, diagnostic):
    """Campaign acceptance is a gate even when no downstream check was declared."""
    if diagnostic['state'] != 'criteria_satisfied':
        return diagnostic
    run = Path(run)
    if open_defects(read_json(run / 'candidate.json'), run / 'inputs'):
        return {'state': 'escalated', 'reason': 'open_placement_defects'}
    return diagnostic


def open_defects(manifest, inputs):
    """Reconstruct open carry; only later receipted current artwork can close it."""
    index = f'{DIRECTORY}/index.json'
    if DECLARATION not in manifest.get('scope', {}) and not any(
            p.startswith(DIRECTORY + '/') for p in manifest.get('files', {})):
        return []
    indexed = read_json(_read(manifest, inputs, index))
    links = manifest['scope'].get(DECLARATION, indexed)
    if links != indexed:
        raise ValueError('placement provenance differs from its captured index')
    if not isinstance(links, list):
        raise ValueError('placement provenance must be ordered receipt links')
    active = []
    for link in links:
        report, loaded, drc = _bundle(manifest, inputs, link)
        identities = _identities(loaded[BOARD])
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
        active = [(d, source, original) for d, source, original in active
                  if not _closes(d, original, identities, conflicts | diagnostics, moved, current)]
        for defect in report['open_defects']:
            _items(defect)
            active.append((defect, link, identities))
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
    try:
        defects = open_defects(ctx.manifest, ctx.inputs)
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


def verify_gate(run, check, manifest):
    """Reconstruct refusals from captured artifacts before sealing or releasing evidence."""
    if DECLARATION not in manifest.get('scope', {}) and not any(
            p.startswith(DIRECTORY + '/') for p in manifest.get('files', {})):
        return
    spec = next(c for c in manifest['scope']['checks'] if c['id'] == check['id'])
    if not gated(spec):
        return
    defects = open_defects(manifest, Path(run) / 'inputs')
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
