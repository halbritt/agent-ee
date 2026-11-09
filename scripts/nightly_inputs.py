"""Nightly consumer of the runner's native input receipt, and fresh stock preparation.

Part B (AGENTEE-619/621/622/624): the installed runner, given AE_CI_INPUT_MANIFEST and
AE_CI_EVIDENCE_DIR, prepares finite read-only input snapshots through its helper,
binds the resolved image into one receipt (agent-ee.native-inputs/1) and announces
`ae-ci-exec: native input receipt /absolute/path` in the log. This module reads that
announcement and binds the receipt to the attempt: selected manifest bytes, source
commit/tree, runner hash and image. It parses no helper internals beyond that
contract and validates no mounts; the runner-invoked helper owns mount policy.

Part C (AGENTEE-621 freshness): before an actually selected native attempt, refresh
the acquisition stock context with the EXISTING producer (agent_ee.jlc_stock_capture)
and the EXISTING conservative union algebra (agent_ee.acquisition.alternative_context),
generalizing the reviewed one-off restoration. Requested IDs come from the current
context's two retained parent catalogs; raw producer bytes and provenance are kept;
there is no retry, no backdating and no stale fallback. Any failure raises, which the
controller records as a failed preparation before any native work.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

RECEIPT_SCHEMA = 'agent-ee.native-inputs/1'
STOCK_CONFIG_SCHEMA = 'agent-ee.nightly-stock-config/1'
RECEIPT_LINE = re.compile(r'^ae-ci-exec: native input receipt (/\S.*)$', re.MULTILINE)


def unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate record field: {key}')
        result[key] = value
    return result


def digest_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def digest_file(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def inventory(root: Path) -> dict:
    """Regular files only, relative POSIX paths; links and special entries refuse."""
    result, pending = {}, [root]
    while pending:
        for path in sorted(pending.pop().iterdir()):
            if path.is_symlink():
                raise ValueError(f'prepared input contains a symlink: {path}')
            if path.is_dir():
                pending.append(path)
            elif path.is_file():
                result[path.relative_to(root).as_posix()] = digest_file(path)
            else:
                raise ValueError(f'prepared input contains a special entry: {path}')
    return result


# --- Part B: receipt binding -----------------------------------------------------------

def announced_receipts(log_bytes: bytes) -> list[str]:
    return RECEIPT_LINE.findall(log_bytes.decode('utf-8', errors='replace'))


def bind_receipt(log_bytes: bytes, *, manifest: Path, evidence_dir: Path, commit: str, tree: str,
                 runner_sha256: str, image_ids: list[str]) -> dict:
    """Bind the one announced receipt to this attempt; anything else is a refusal."""
    announced = announced_receipts(log_bytes)
    if len(announced) != 1:
        raise ValueError(f'expected exactly one native input receipt announcement, found {len(announced)}')
    path = Path(announced[0])
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError('announced native input receipt is not a regular absolute file')
    if not path.resolve().is_relative_to(evidence_dir.resolve()):
        raise ValueError('announced native input receipt is outside this attempt evidence directory')
    raw = path.read_bytes()
    record = json.loads(raw, object_pairs_hook=unique_fields)
    if not isinstance(record, dict) or record.get('schema') != RECEIPT_SCHEMA:
        raise ValueError('native input receipt has an unsupported schema')
    if record.get('status') != 'prepared':
        raise ValueError(f"native input receipt status is {record.get('status')!r}, not prepared")
    manifest_sha256 = digest_file(manifest)
    if record.get('manifest_sha256') != manifest_sha256:
        raise ValueError('native input receipt binds a different manifest than this attempt selected')
    if record.get('image') not in image_ids:
        raise ValueError('native input receipt image differs from the image the runner announced')
    source = record.get('agent_ee')
    if not isinstance(source, dict) or source.get('commit') != commit or source.get('tree') != tree:
        raise ValueError('native input receipt binds a different source commit/tree')
    runner = record.get('runner')
    if not isinstance(runner, dict) or runner.get('sha256') != runner_sha256:
        raise ValueError('native input receipt binds a different runner')
    inputs = record.get('inputs')
    if not isinstance(inputs, list) or not inputs:
        raise ValueError('native input receipt lists no inputs')
    for item in inputs:
        if (not isinstance(item, dict) or item.get('read_only') is not True or not isinstance(item.get('name'), str)
                or not isinstance(item.get('env'), str) or not (item.get('files') or item.get('git'))):
            raise ValueError('native input receipt input entry is incomplete or writable')
    for field in ('policy_sha256', 'preparer'):
        if not record.get(field):
            raise ValueError(f'native input receipt lacks {field}')
    return {
        'path': str(path), 'sha256': digest_bytes(raw), 'manifest': str(manifest),
        'manifest_sha256': manifest_sha256, 'image': record['image'],
        'policy_sha256': record['policy_sha256'], 'module_env_sha256': record.get('module_env_sha256'),
        'preparer_sha256': (record['preparer'] or {}).get('sha256') if isinstance(record['preparer'], dict) else None,
        'output_directory': record.get('output_directory'),
        'inputs': [{'name': item['name'], 'env': item['env'], 'identity': item.get('git') or item.get('files')}
                   for item in inputs],
    }


def verify_bound_receipt(binding: dict, *, manifest_sha256: str) -> None:
    """Re-verify a retained binding (import-baseline): bytes unchanged, manifest identical."""
    path = Path(binding['path'])
    if path.is_symlink() or not path.is_file() or digest_file(path) != binding['sha256']:
        raise ValueError('retained native input receipt is missing or changed')
    if binding['manifest_sha256'] != manifest_sha256:
        raise ValueError('selected input manifest differs from the retained receipt binding')


# --- Part C: fresh stock preparation ---------------------------------------------------

def load_stock_config(path: Path) -> dict:
    document = json.loads(path.read_bytes(), object_pairs_hook=unique_fields)
    required = {'schema', 'context_path', 'source_root', 'per_request_timeout_seconds',
                'invocation_timeout_seconds', 'min_interval_seconds', 'max_bytes'}
    allowed = required | {'endpoint'}
    if (not isinstance(document, dict) or document.get('schema') != STOCK_CONFIG_SCHEMA
            or not required <= set(document) <= allowed):
        raise ValueError(f'stock config must be {STOCK_CONFIG_SCHEMA} with exactly the documented fields')
    for key in ('context_path', 'source_root'):
        if not isinstance(document[key], str) or not Path(document[key]).is_absolute():
            raise ValueError(f'stock config {key} must be an absolute path')
    for key in ('per_request_timeout_seconds', 'invocation_timeout_seconds', 'min_interval_seconds', 'max_bytes'):
        value = document[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise ValueError(f'stock config {key} must be a positive number')
    if 'endpoint' in document and (not isinstance(document['endpoint'], str)
                                   or not document['endpoint'].startswith(('https://', 'http://127.0.0.1:'))):
        raise ValueError('stock config endpoint must be an https URL (or a loopback test stub)')
    return document


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def prepare_fresh_stock(config_path: Path, worktree: Path, stock_dir: Path, *, python: str = sys.executable) -> dict:
    """Refresh the acquisition stock context for one selected native attempt.

    Returns {'context_path', 'source_root', 'replaced': {old_source_root, old_context_path}, 'record'}.
    Raises ValueError/RuntimeError on any refusal; nothing is retried or backdated.
    """
    config = load_stock_config(config_path)
    sys.path.insert(0, str(worktree / 'src'))
    try:
        from agent_ee import jlc_stock_capture as stock
        from agent_ee.acquisition import alternative_context
        from agent_ee.storage import canonical, json_bytes
    finally:
        sys.path.pop(0)
    old_context_path, old_source = Path(config['context_path']), Path(config['source_root'])
    old_raw = old_context_path.read_bytes()
    old_context, old_captures = alternative_context.read(old_source, old_raw)
    old_catalog = json.loads(old_captures[old_context['catalog']['path']])
    if stock_dir.exists():
        raise ValueError('stock preparation directory must start absent')
    stock_dir.mkdir(parents=True, mode=0o700)
    home = stock_dir / 'home'
    home.mkdir(mode=0o700)
    (stock_dir / 'captures').mkdir()
    environment = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'LANG': 'C.UTF-8', 'HOME': str(home),
                   'PYTHONPATH': str(worktree / 'src'), 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONSAFEPATH': '1'}
    endpoint = config.get('endpoint', stock.DEFAULT_ENDPOINT)
    record = {'schema': 'agent-ee.nightly-stock-preparation/1', 'started_at': _now(), 'status': 'started',
              'config': str(config_path), 'config_sha256': digest_file(config_path),
              'source': str(worktree), 'endpoint': endpoint,
              'replaced_context': {'context_path': str(old_context_path), 'context_sha256': digest_bytes(old_raw),
                                   'source_root': str(old_source)},
              'invocations': []}
    record_path = stock_dir / 'record.json'
    record_path.write_text(json.dumps(record, indent=2) + '\n')
    try:
        parents = []
        for index, parent_ref in enumerate(old_context['parents'], 1):
            parent_catalog = json.loads(old_captures[parent_ref['path']])
            ids = [request['lcsc'] for request in parent_catalog['requests']]
            if not ids or len(set(ids)) != len(ids):
                raise ValueError(f'parent {index} catalog has no unique requested ids')
            out = stock_dir / 'captures' / f'parent-{index}'
            argv = [python, '-B', '-P', '-m', 'agent_ee.jlc_stock_capture', '--allow-network',
                    '--endpoint', endpoint, '--out', str(out),
                    '--timeout', str(config['per_request_timeout_seconds']),
                    '--min-interval', str(config['min_interval_seconds']),
                    '--max-bytes', str(int(config['max_bytes'])), *ids]
            invocation = {'argv': argv, 'cwd': str(home), 'started_at': _now(), 'requested': ids}
            record['invocations'].append(invocation)
            record_path.write_text(json.dumps(record, indent=2) + '\n')
            with (stock_dir / f'parent-{index}.log').open('wb') as log:
                try:
                    completed = subprocess.run(argv, cwd=home, env=environment, stdout=log, stderr=subprocess.STDOUT,
                                               timeout=config['invocation_timeout_seconds'])
                except subprocess.TimeoutExpired as error:
                    invocation.update(exit_code=None, finished_at=_now(), timeout=True)
                    raise RuntimeError(f'stock capture {index} exceeded its bounded time') from error
            invocation.update(exit_code=completed.returncode, finished_at=_now())
            if completed.returncode != 0:
                raise RuntimeError(f'stock capture {index} failed: exit {completed.returncode}')
            raw = (out / 'catalog.json').read_bytes()
            catalog = json.loads(raw)
            validation = stock.validate_capture(catalog, lambda rel, out=out: (out / rel).read_bytes())
            invocation['validation'] = validation
            if validation['requested'] != ids or validation['failed'] or validation['not_found']:
                raise RuntimeError(f'stock capture {index} is incomplete: {validation}')
            if not all(row['http_status'] == 200 and row['status'] == 'found' for row in catalog['requests']):
                raise RuntimeError(f'stock capture {index} has a non-found response')
            for lcsc, part in catalog['parts'].items():
                for field in ('mpn', 'mfr', 'library_tier'):
                    if part[field] != old_catalog['parts'][lcsc][field]:
                        raise RuntimeError(f'stock capture {index} changed part identity for {lcsc}: {field}')
            parents.append((out, raw, catalog))
        target = stock_dir / 'addr-current'
        shutil.copytree(old_source, target, symlinks=False)
        context = json.loads(old_raw)
        union = dict(schema=stock.SCHEMA,
                     as_of=min((p[2]['as_of'] for p in parents), key=lambda t: stock._utc(t, 'as_of')),
                     completed_at=max((p[2]['completed_at'] for p in parents), key=lambda t: stock._utc(t, 'completed_at')),
                     stale_before=None, source=parents[0][2]['source'], requests=[], parts={})
        derivation = {'kind': 'Conservative offline union of actual captures; NOT a fresh capture or one original invocation',
                      'parents': [], 'requests': [], 'physical_validation_performed': False,
                      'supplier_preview_performed': False, 'supplier_eligibility': 'unknown'}
        for index, (out, raw, catalog) in enumerate(parents, 1):
            parent_ref = context['parents'][index - 1]
            (target / parent_ref['path']).write_bytes(raw)
            parent_ref['sha256'] = digest_bytes(raw)
            derivation['parents'].append({'as_of': catalog['as_of'], 'completed_at': catalog['completed_at'],
                                          'copy': parent_ref['path'], 'sha256': parent_ref['sha256'],
                                          'source': str(out / 'catalog.json')})
            union['requests'] += catalog['requests']
            union['parts'].update(catalog['parts'])
            for request in catalog['requests']:
                relative = request['response']['path']
                shutil.copy2(out / relative, target / Path(context['catalog']['path']).parent / relative)
                body = stock.request_body(request['lcsc'])
                derivation['requests'].append({
                    'lcsc': request['lcsc'], 'parent': index, 'fetched_at': request['fetched_at'],
                    'request_body_utf8': body.decode(), 'request_body_sha256': digest_bytes(body),
                    'request_record_canonical_sha256': digest_bytes(canonical(request)),
                    'response_sha256': request['response']['sha256'],
                    'response_size_bytes': request['response']['size_bytes']})
        (target / context['catalog']['path']).write_bytes(json_bytes(union))
        (target / context['derivation']['path']).write_bytes(json_bytes(derivation))
        context['files'] = {name: digest_file(target / name) for name in context['files']}
        for key in ('catalog', 'derivation'):
            context[key]['sha256'] = context['files'][context[key]['path']]
        context_path = stock_dir / 'addr-current-context.json'
        context_path.write_bytes(json_bytes(context))
        alternative_context.read(target, context_path.read_bytes())  # existing admission algebra
        if old_context_path.read_bytes() != old_raw:
            raise RuntimeError('original stock context changed during preparation')
        record.update(status='prepared', context_path=str(context_path), source_root=str(target),
                      as_of=union['as_of'], requested_parts=list(union['parts']), originals_unchanged=True)
        return {'context_path': context_path, 'source_root': target,
                'replaced': {'source_root': old_source, 'context_path': old_context_path}, 'record': record}
    except BaseException as error:
        record.update(status='failed', failure=type(error).__name__ + ': ' + str(error))
        raise
    finally:
        record['finished_at'] = _now()
        record_path.write_text(json.dumps(record, indent=2) + '\n')


def rebind_manifest(manifest: Path, destination: Path, prepared: dict) -> Path:
    """Write the per-attempt manifest: only the two stock-context entries change paths/hashes."""
    document = json.loads(manifest.read_bytes(), object_pairs_hook=unique_fields)
    if not isinstance(document, dict) or document.get('schema') != RECEIPT_SCHEMA or not isinstance(document.get('inputs'), list):
        raise ValueError('input manifest must be agent-ee.native-inputs/1 with an inputs list')
    old_source, old_context = str(prepared['replaced']['source_root']), str(prepared['replaced']['context_path'])
    updated = []
    for item in document['inputs']:
        if not isinstance(item, dict):
            raise ValueError('input manifest entry must be an object')
        if item.get('path') == old_source:
            item['path'], item['files'] = str(prepared['source_root']), inventory(prepared['source_root'])
            updated.append(item.get('name'))
        elif item.get('path') == old_context:
            item['path'], item['files'] = str(prepared['context_path']), {'.': digest_file(prepared['context_path'])}
            updated.append(item.get('name'))
    if len(updated) != 2:
        raise ValueError(f'input manifest must reference the replaced stock source root and context exactly once each; updated {updated}')
    if destination.exists() or destination.is_symlink():
        raise ValueError('per-attempt input manifest must start absent')
    destination.write_text(json.dumps(document, indent=2, sort_keys=True) + '\n')
    return destination
