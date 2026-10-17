"""Standalone, inert concern requests; never write a reviewed run."""

import shutil
import stat
from pathlib import Path

from .baseline import load_baseline
from .candidate import verify_snapshot
from .storage import digest, identity, read_json, write_json


SPEC_KEYS = {'mode', 'requirement_id', 'check_id', 'condition', 'observable', 'unit',
             'rationale', 'interpreter', 'resources'}


REQUEST_KEYS = {'schema', 'run', 'run_id', 'candidate_id', 'baseline_id', 'source_hashes',
                'concern', 'spec', 'script_sha256', 'interpreter_sha256',
                'acceptance_authority', 'request_id'}


def read_request(directory):
    path = Path(directory) / 'request.json'
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 2_000_000:
        raise ValueError('request must be a regular JSON file of at most 2000000 bytes')
    try:
        return read_json(path)
    except RecursionError as exc:
        raise ValueError('request JSON nesting exceeds the parser limit') from exc


def inventory(root):
    files = {}
    for path in sorted(Path(root).rglob('*')):
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            raise ValueError(f'operation contains nonregular path: {path}')
        if stat.S_ISREG(mode) and path != Path(root) / 'operation.json':
            files[path.relative_to(root).as_posix()] = digest(path)
    return files


def seal_operation(root, kind):
    record = {'schema': 'agent-ee.evidence-operation/1', 'kind': kind,
              'acceptance_authority': 'none', 'files': inventory(root)}
    write_json(Path(root) / 'operation.json', {**record, 'operation_id': identity(record)})


def verify_operation(root):
    root = Path(root)
    record = read_json(root / 'operation.json')
    if not isinstance(record, dict):
        raise ValueError('evidence operation must be a JSON object')
    body = {k: v for k, v in record.items() if k != 'operation_id'}
    if (set(record) != {'schema', 'kind', 'acceptance_authority', 'files', 'operation_id'} or
            record.get('kind') not in ('request', 'replay', 'graduation') or
            record.get('schema') != 'agent-ee.evidence-operation/1' or
            record.get('acceptance_authority') != 'none' or
            record.get('operation_id') != identity(body) or record.get('files') != inventory(root)):
        raise ValueError('evidence operation changed')
    return record


def fresh_directory(out, *sources):
    # No ancestor/descendant relationships: even an unused subdirectory would
    # mutate the retained run's directory tree and potentially its artifact seal.
    out = Path(out).absolute()
    for parent in (out, *out.parents):
        if parent.is_symlink():
            raise ValueError('output ancestors must not be symlinks')
    out = out.resolve()
    # Request locators are untrusted, including when producing failure receipts.
    # Inspect the destination ancestry before creating even intermediate paths.
    for parent in (out, *out.parents):
        if any((parent / name).exists() or (parent / name).is_symlink()
               for name in ('candidate.json', 'result.json', 'operation.json')):
            raise ValueError('operation output must be separate from retained runs and operations')
    for source in sources:
        source = Path(source).resolve()
        if out == source or source in out.parents or out in source.parents:
            raise ValueError('operation output must be separate from its sources')
    out.mkdir(parents=True, exist_ok=False)
    return out


def source_context(run, finding_id):
    from .cli import verify
    run = Path(run).resolve()
    result = verify(run, current_source=False)
    if result['state'] == 'running':
        raise ValueError('request export requires initial review scheduling to have settled')
    concern = next((f for f in result['findings'] if f['id'] == finding_id), None)
    if (concern is None or concern.get('source') != 'review' or
            concern.get('assessment') != 'concern' or
            concern.get('disposition', concern.get('status', 'unresolved')) not in ('unresolved', 'deferred')):
        raise ValueError('request requires an unresolved retained reviewer concern')
    from .findings import collect_findings
    original = next((f for f in collect_findings(result['reviews'], []) if f['id'] == finding_id), None)
    if original is None or any(concern.get(k) != v for k, v in original.items()
                               if k not in ('status', 'disposition', 'events')):
        raise ValueError('concern does not match an initial reviewer finding')
    role = concern.get('role', finding_id.split('/')[0])
    if role in ('check', 'adjudicator') or finding_id.split('/')[0] != role:
        raise ValueError('request origin is not an initial reviewer role')
    baseline = load_baseline(run)
    return result, concern, baseline


def validate_spec(spec, baseline, manifest):
    if not isinstance(spec, dict) or set(spec) != SPEC_KEYS:
        raise ValueError('request specification has missing or unknown fields')
    if spec['mode'] not in ('declared_check', 'experiment', 'new_check_proposal'):
        raise ValueError('unknown evidence request mode')
    for key in ('condition', 'observable', 'unit', 'rationale'):
        if not isinstance(spec[key], str) or not spec[key].strip() or len(spec[key]) > 10000:
            raise ValueError(f'request requires bounded nonempty {key}')
    rid, cid = spec['requirement_id'], spec['check_id']
    if any(value is not None and not isinstance(value, str) for value in (rid, cid)):
        raise ValueError('requirement_id and check_id must be strings or null')
    requirements = {r['id']: r for r in baseline['requirements']} if baseline else {}
    if rid is not None and rid not in requirements:
        raise ValueError('request requirement is not in the retained baseline')
    checks = {c['id'] for c in manifest['scope']['checks']}
    if cid is not None and cid not in checks:
        raise ValueError('request check is not declared')
    if cid is not None and baseline and (rid is None or cid not in requirements[rid]['checks']):
        raise ValueError('request check does not map to the requirement')
    if spec['mode'] == 'declared_check' and cid is None:
        raise ValueError('declared_check mode requires a declared check')
    identity(spec)  # Reject non-finite/non-JSON proposal data before creating output.
    if not isinstance(spec['resources'], dict):
        raise ValueError('resources must be an object')
    if spec['interpreter'] is not None and not isinstance(spec['interpreter'], str):
        raise ValueError('interpreter must be a string or null')


def export_request(run, finding_id, spec, out, *, script=None):
    run = Path(run).resolve()
    result, concern, baseline = source_context(run, finding_id)
    manifest = verify_snapshot(run)
    validate_spec(spec, baseline, manifest)
    if spec['mode'] == 'experiment' and script is None:
        raise ValueError('experiment requires a captured Python script')
    if script is not None:
        script = Path(script)
        if script.is_symlink() or not script.is_file() or script.stat().st_size > 1_000_000:
            raise ValueError('script must be a regular file of at most 1000000 bytes')
        script_bytes = script.read_bytes()
    out = fresh_directory(out, run, *([script] if script is not None else []))
    if script is not None:
        (out / 'script.py').write_bytes(script_bytes)
    runtime = Path('/usr/bin/python3')
    request = {'schema': 'agent-ee.evidence-request/1', 'run': str(run),
               'run_id': result['run_id'], 'candidate_id': manifest['candidate_id'],
               'baseline_id': baseline['baseline_id'] if baseline else None,
               'source_hashes': {name: digest(run / name) for name in
                   ('result.json', 'candidate.json', *(['baseline.json'] if baseline else []))},
               'concern': concern, 'spec': spec,
               'script_sha256': digest(out / 'script.py') if script else None,
               'interpreter_sha256': digest(runtime) if spec['interpreter'] == str(runtime) and runtime.is_file() else None,
               'acceptance_authority': 'none'}
    request['request_id'] = identity(request)
    write_json(out / 'request.json', request)
    read_request(out)
    (out / 'REQUEST.md').write_text(
        f"Evidence request {request['request_id']}\n\nConcern: {finding_id}\n"
        f"Mode: {spec['mode']}\nCondition: {spec['condition']}\n"
        f"Measure: {spec['observable']} ({spec['unit']})\nRationale: {spec['rationale']}\n\n"
        'Pending / unaccepted proposal. Acceptance authority: none.\n'
        'A declared failed check can be cited only through normal sealed witness adjudication.\n')
    seal_operation(out, 'request')
    return request


def validate_request(directory):
    directory = Path(directory)
    operation = verify_operation(directory)
    if operation['kind'] != 'request':
        raise ValueError('expected a request operation')
    request = read_request(directory)
    if not isinstance(request, dict) or set(request) != REQUEST_KEYS:
        raise ValueError('request has missing or unknown fields')
    if request.get('schema') != 'agent-ee.evidence-request/1' or request.get('acceptance_authority') != 'none':
        raise ValueError('invalid request schema/authority')
    if identity({k: v for k, v in request.items() if k != 'request_id'}) != request.get('request_id'):
        raise ValueError('request content identity changed')
    if (not isinstance(request['run'], str) or not request['run'] or
            not isinstance(request['concern'], dict) or not isinstance(request['concern'].get('id'), str)):
        raise ValueError('request needs a run locator and concern identity')
    run = Path(request['run'])
    expected_sources = {'result.json', 'candidate.json'} | ({'baseline.json'} if request['baseline_id'] else set())
    if not isinstance(request['source_hashes'], dict) or set(request['source_hashes']) != expected_sources:
        raise ValueError('request has incomplete source bindings')
    for name, sha in request['source_hashes'].items():
        if name not in ('result.json', 'candidate.json', 'baseline.json') or digest(run / name) != sha:
            raise ValueError('request source changed')
    result, concern, baseline = source_context(run, request['concern']['id'])
    manifest = verify_snapshot(run)
    if (request['run_id'] != result['run_id'] or request['candidate_id'] != manifest['candidate_id'] or
            request['baseline_id'] != (baseline['baseline_id'] if baseline else None) or concern != request['concern']):
        raise ValueError('request identity does not match retained concern')
    validate_spec(request['spec'], baseline, manifest)
    if request['script_sha256'] is not None and digest(directory / 'script.py') != request['script_sha256']:
        raise ValueError('captured script changed')
    return request


def export_graduation(request_dir, proposal, out, *, replay_dir=None):
    request = validate_request(request_dir)
    required = {'adapter_contract', 'supported_scope', 'unsupported_scope', 'requirement_mapping',
                'numerical_criteria', 'expected_control', 'expected_seed', 'limitations'}
    if not isinstance(proposal, dict) or set(proposal) != required or any(not proposal[k] for k in required):
        raise ValueError('graduation proposal requires contract, scopes, mapping, criteria, expectations and limitations')
    receipt = None
    if replay_dir is not None:
        operation = verify_operation(replay_dir)
        receipt = read_json(Path(replay_dir) / 'receipt.json')
        if (operation['kind'] != 'replay' or not isinstance(receipt, dict) or
                receipt.get('schema') != 'agent-ee.experiment-receipt/1' or
                receipt.get('request_id') != request['request_id'] or
                receipt.get('acceptance_authority') != 'none'):
            raise ValueError('graduation replay does not belong to request')
    out = fresh_directory(out, request['run'], request_dir, *([replay_dir] if replay_dir else []))
    shutil.copyfile(Path(request_dir) / 'request.json', out / 'request.json')
    if request['script_sha256']:
        shutil.copyfile(Path(request_dir) / 'script.py', out / 'script.py')
    write_json(out / 'proposal.json', {'schema': 'agent-ee.graduation-proposal/1',
        'request_id': request['request_id'], 'status': 'proposed_unaccepted',
        'acceptance_authority': 'none', 'proposed': proposal, 'replay_receipt': receipt,
        'replay_operation_id': operation['operation_id'] if replay_dir else None})
    (out / 'README.txt').write_text('Inert proposal only. No check, requirement or baseline is installed.\n'
        'Explicit authorization, a registered check, new baseline/campaign, fresh checks/seal,\n'
        'independent initial reviews and centralized witness adjudication are required.\n')
    seal_operation(out, 'graduation')
    return read_json(out / 'proposal.json')
