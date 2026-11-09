"""Child process for one fresh-stock preparation; imports the producer only from the selected source.

Invoked by scripts/nightly_inputs.prepare_fresh_stock as
    python3 -B -P -s nightly_stock_prepare.py CONFIG WORKTREE STOCK_DIR
with PYTHONPATH=WORKTREE/src and PYTHONSAFEPATH=1, so agent_ee resolves only from the
selected checkout (clean import custody) and the controller process never imports it.
Prints the result JSON on stdout and exits 0; any refusal writes record.json with the
failure, prints the reason on stderr and exits 1. Nothing is retried or backdated.
"""
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

MAX_INVOCATION_SECONDS = 420  # finite budget for each selected parent population


def _now():
    return datetime.now(timezone.utc).isoformat()


def _digest_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def _digest_file(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _strict_constant(name):
    raise ValueError(f'stock config contains a non-finite number: {name}')


def load_config(path, limits):
    """Exact field set, finite positive bounds, producer limits; values are preserved, never coerced."""
    document = json.loads(Path(path).read_bytes(), parse_constant=_strict_constant,
                          object_pairs_hook=_unique)
    required = {'schema', 'context_path', 'source_root', 'per_request_timeout_seconds',
                'invocation_timeout_seconds', 'min_interval_seconds', 'max_bytes'}
    if (not isinstance(document, dict) or document.get('schema') != 'agent-ee.nightly-stock-config/1'
            or not required <= set(document) <= required | {'endpoint'}):
        raise ValueError('stock config must be agent-ee.nightly-stock-config/1 with exactly the documented fields')
    for key in ('context_path', 'source_root'):
        if not isinstance(document[key], str) or not Path(document[key]).is_absolute():
            raise ValueError(f'stock config {key} must be an absolute path')
    for key in ('per_request_timeout_seconds', 'invocation_timeout_seconds', 'min_interval_seconds'):
        value = document[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f'stock config {key} must be a finite positive number')
    if type(document['max_bytes']) is not int or document['max_bytes'] < 1:
        raise ValueError('stock config max_bytes must be a positive integer')
    if 'endpoint' in document and (not isinstance(document['endpoint'], str)
                                   or not document['endpoint'].startswith(('https://', 'http://127.0.0.1:'))):
        raise ValueError('stock config endpoint must be an https URL (or a loopback test stub)')
    if document['invocation_timeout_seconds'] > MAX_INVOCATION_SECONDS:
        raise ValueError(f'stock config invocation_timeout_seconds must be <= {MAX_INVOCATION_SECONDS}')
    if document['per_request_timeout_seconds'] >= document['invocation_timeout_seconds']:
        raise ValueError('stock config per-request timeout must be below the invocation timeout')
    if limits is not None:
        if document['per_request_timeout_seconds'] > limits['max_seconds']:
            raise ValueError(f"stock config per_request_timeout_seconds exceeds the producer limit {limits['max_seconds']}")
        if document['min_interval_seconds'] < limits['min_interval']:
            raise ValueError(f"stock config min_interval_seconds is below the producer minimum {limits['min_interval']}")
        if document['max_bytes'] > limits['max_bytes']:
            raise ValueError(f"stock config max_bytes exceeds the producer limit {limits['max_bytes']}")
    return document


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate stock config field: {key}')
        result[key] = value
    return result


def prepare(config_path, worktree, stock_dir):
    from agent_ee import jlc_stock_capture as stock
    from agent_ee.acquisition import alternative_context
    from agent_ee.storage import canonical, json_bytes
    config = load_config(config_path, {'max_seconds': stock.MAX_SECONDS, 'min_interval': stock.MIN_INTERVAL,
                                       'max_bytes': stock.MAX_BYTES})
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
              'config': str(config_path), 'config_sha256': _digest_file(config_path), 'source': str(worktree),
              'producer_sha256': _digest_file(Path(stock.__file__)), 'endpoint': endpoint,
              'replaced_context': {'context_path': str(old_context_path), 'context_sha256': _digest_bytes(old_raw),
                                   'source_root': str(old_source)},
              'invocations': []}
    record_path = stock_dir / 'record.json'
    record_path.write_text(json.dumps(record, indent=2) + '\n')
    try:
        parents, last_capture_end = [], None
        for index, parent_ref in enumerate(old_context['parents'], 1):
            parent_catalog = json.loads(old_captures[parent_ref['path']])
            ids = [request['lcsc'] for request in parent_catalog['requests']]
            if not ids or len(set(ids)) != len(ids):
                raise ValueError(f'parent {index} catalog has no unique requested ids')
            if last_capture_end is not None:
                time.sleep(max(0.0, config['min_interval_seconds'] - (time.monotonic() - last_capture_end)))
            out = stock_dir / 'captures' / f'parent-{index}'
            argv = [sys.executable, '-B', '-P', '-m', 'agent_ee.jlc_stock_capture', '--allow-network',
                    '--endpoint', endpoint, '--out', str(out),
                    '--timeout', json.dumps(config['per_request_timeout_seconds']),
                    '--min-interval', json.dumps(config['min_interval_seconds']),
                    '--max-bytes', str(config['max_bytes']), *ids]
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
            last_capture_end = time.monotonic()
            if completed.returncode != 0:
                raise RuntimeError(f'stock capture {index} failed: exit {completed.returncode}')
            raw = (out / 'catalog.json').read_bytes()
            catalog = json.loads(raw)
            validation = stock.validate_capture(catalog, lambda rel, out=out: (out / rel).read_bytes())
            invocation['validation'] = validation
            if validation['requested'] != sorted(ids, key=lambda i: int(i[1:])) or validation['failed'] or validation['not_found']:
                raise RuntimeError(f'stock capture {index} is incomplete: failed={validation["failed"]} not_found={validation["not_found"]}')
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
            parent_ref['sha256'] = _digest_bytes(raw)
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
                    'request_body_utf8': body.decode(), 'request_body_sha256': _digest_bytes(body),
                    'request_record_canonical_sha256': _digest_bytes(canonical(request)),
                    'response_sha256': request['response']['sha256'],
                    'response_size_bytes': request['response']['size_bytes']})
        (target / context['catalog']['path']).write_bytes(json_bytes(union))
        (target / context['derivation']['path']).write_bytes(json_bytes(derivation))
        context['files'] = {name: _digest_file(target / name) for name in context['files']}
        for key in ('catalog', 'derivation'):
            context[key]['sha256'] = context['files'][context[key]['path']]
        context_path = stock_dir / 'addr-current-context.json'
        context_path.write_bytes(json_bytes(context))
        alternative_context.read(target, context_path.read_bytes())  # existing admission algebra
        if old_context_path.read_bytes() != old_raw:
            raise RuntimeError('original stock context changed during preparation')
        record.update(status='prepared', context_path=str(context_path), source_root=str(target),
                      as_of=union['as_of'], requested_parts=list(union['parts']), originals_unchanged=True)
        return {'context_path': str(context_path), 'source_root': str(target),
                'replaced': {'source_root': str(old_source), 'context_path': str(old_context_path)}, 'record': record}
    except BaseException as error:
        record.update(status='failed', failure=type(error).__name__ + ': ' + str(error))
        raise
    finally:
        record['finished_at'] = _now()
        record_path.write_text(json.dumps(record, indent=2) + '\n')


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 3:
        print('usage: nightly_stock_prepare.py CONFIG WORKTREE STOCK_DIR', file=sys.stderr)
        return 2
    try:
        result = prepare(Path(argv[0]), Path(argv[1]), Path(argv[2]))
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
        print(f'stock preparation refused: {type(error).__name__}: {error}', file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    sys.exit(main())
