"""Retained local launch observations; no provider calls or acceptance authority."""
import math
import subprocess
import time
import uuid
from pathlib import Path

from .executor import execute
from .storage import digest, identity, read_json, write_json


def capture_expectation(result, manifest, definitions, *, inputs=None):
    """Capture once before checks/edits; callers supply the original runtime."""
    from .baseline import compile_baseline
    baseline = compile_baseline(manifest['scope'], manifest.get('files', {}), inputs)
    tools, installations = {}, {}
    required_runtime = {'/usr/bin/python3', 'bwrap'}
    native_requirements = {'erc': 'kicad-cli', 'drc': 'kicad-cli', 'netlist': 'kicad-cli',
                           'fab_export': 'kicad-cli', 'fab_package': 'kicad-cli',
                           'geometry': 'pcbnew', 'rc_transient': 'ngspice', 'active_spice': '/usr/bin/ngspice',
                           'passive_corners': '/usr/bin/ngspice', 'dc_path': 'pcbnew',
                           'assembly_readiness': 'kicad-cli',
                           'board_envelope': 'pcbnew', 'dfm_geometry': 'pcbnew'}
    for spec in manifest['scope'].get('checks', []):
        if spec.get('kind') in ('routing_rules', 'fab_profile'):
            required_runtime.update(('kicad-cli', 'pcbnew'))
        if spec.get('kind') in native_requirements:
            required_runtime.add(native_requirements[spec['kind']])
        params = spec.get('params', {})
        if spec.get('kind') == 'dc_path':
            from .dc_path import TOOLS
            tools.update(TOOLS)
        if spec.get('kind') == 'datasheet_facts':
            extractor = params['extractor']
            tools[extractor['path']] = extractor['sha256']
        if spec.get('kind') == 'kicad_happy':
            installations[str(Path(params['installation']).expanduser().resolve())] = params['installation_sha256']
        tool = params.get('tool')
        if tool:
            tools[tool['argv'][0]] = tool['sha256']
    value = {'schema': 'agent-ee.launch-expectation/1',
        'run_id': result['run_id'], 'candidate_id': manifest['candidate_id'],
        'baseline_id': baseline['baseline_id'] if baseline else None,
        'policy_files': result['policy_files'], 'runtime_files': result['runtime_files'],
        'profile': manifest['scope'].get('review_profile'),
        'definitions': definitions, 'declared_tools': tools, 'declared_installations': installations,
        'required_runtime': sorted(required_runtime)}
    return {**value, 'identity': identity(value)}


def validate_expectation(value):
    if (not isinstance(value, dict) or value.get('schema') != 'agent-ee.launch-expectation/1' or
            set(value) != {'schema', 'run_id', 'candidate_id', 'baseline_id', 'policy_files',
                           'runtime_files', 'profile', 'definitions', 'declared_tools', 'declared_installations', 'required_runtime', 'identity'} or
            identity({k: v for k, v in value.items() if k != 'identity'}) != value['identity'] or
            any(not isinstance(value[k], dict) for k in ('policy_files', 'runtime_files', 'declared_tools', 'declared_installations')) or
            not isinstance(value['definitions'], list) or
            not isinstance(value['required_runtime'], list) or
            any(not isinstance(name, str) for name in value['required_runtime'])):
        raise ValueError('malformed_expectation')


def guard(run, role, definition, context, cancellation=None):
    from .cli import LOADED_POLICY_FILES, policy_files, runtime_files
    run = Path(run)
    work = run / 'review-work' / role / 'preflight'
    receipt_path = work / 'receipt.json'
    receipt = {'schema': 'agent-ee.preflight/1', 'invocation_id': str(uuid.uuid4()),
        'role': role, 'boundary': context.get('boundary') if isinstance(context, dict) else None,
        'result': 'refused', 'reasons': [], 'observed': {}, 'started_unix': time.time()}
    start = time.monotonic()
    executable = None
    try:
        if receipt_path.exists():
            raise ValueError('duplicate_receipt')
        if not isinstance(context, dict) or 'expected' not in context:
            raise ValueError('missing_context')
        expected = context['expected']
        validate_expectation(expected)
        receipt.update(expectation_id=expected['identity'], run_id=expected['run_id'],
                       candidate_id=expected['candidate_id'], baseline_id=expected['baseline_id'],
                       definition=definition)
        if context['boundary'] not in ('initial', 'adjudicator', 'implementation'):
            raise ValueError('malformed_boundary')
        if definition not in expected['definitions']:
            raise ValueError('definition_drift')
        if cancellation and cancellation.cancelled.is_set():
            raise ValueError('cancelled')
        if time.monotonic() >= context['deadline']:
            raise ValueError('deadline_exhausted')
        observed = receipt['observed']
        observed['policy_files'] = policy_files()
        if observed['policy_files'] != expected['policy_files'] or LOADED_POLICY_FILES != expected['policy_files']:
            raise ValueError('policy_drift')
        def checkpoint():
            if cancellation and cancellation.cancelled.is_set():
                raise ValueError('cancelled')
            if time.monotonic() >= context['deadline']:
                raise ValueError('deadline_exhausted')
        observed['runtime_files'] = runtime_files(checkpoint=checkpoint)
        harness = definition['harness']
        executable = observed['runtime_files'][harness]['path']
        required = [harness, *expected['required_runtime']]
        if harness == 'codex' and definition.get('tool_mode', 'investigate') == 'investigate':
            required.append('codex_tool_host')
        missing = [name for name in required if not observed['runtime_files'].get(name, {}).get('sha256')]
        if missing:
            raise ValueError('missing_binary:' + ','.join(missing))
        if observed['runtime_files'] != expected['runtime_files']:
            raise ValueError('runtime_drift')
        if harness != 'claude':
            with Path(executable).open('rb') as binary:
                if binary.read(4) != b'\x7fELF':
                    raise ValueError('native_binary_required')
        observed['declared_tools'] = {path: digest(path) for path in expected['declared_tools']}
        if observed['declared_tools'] != expected['declared_tools']:
            raise ValueError('declared_tool_drift')
        from .kicad_happy import installation
        observed['declared_installations'] = {}
        for root in expected['declared_installations']:
            checkpoint()
            observed['declared_installations'][root] = installation(root)['sha256']
        if observed['declared_installations'] != expected['declared_installations']:
            raise ValueError('declared_installation_drift')
        remaining = context['deadline'] - time.monotonic()
        if remaining <= 0:
            raise ValueError('deadline_exhausted')
        probe = execute(run / 'inputs', work / 'probe', ['/usr/bin/true'],
                        timeout=min(10, remaining), network=False, cancellation=cancellation)
        receipt['probe'] = probe
        if not isinstance(probe, dict) or not {'execution', 'exit_code', 'argv', 'sandbox_argv'} <= probe.keys():
            raise ValueError('malformed_probe_receipt')
        if probe['execution'] != 'ok' or probe['exit_code'] != 0:
            diagnostic = (work / 'probe' / 'stderr.log').read_text()
            if 'namespace' in diagnostic and ('not permitted' in diagnostic or 'No permissions' in diagnostic):
                raise ValueError('sandbox_namespace_denied')
            if 'Unknown option' in diagnostic or 'unknown option' in diagnostic:
                raise ValueError('sandbox_invalid_option')
            raise ValueError('sandbox_probe_' + probe['execution'])
        if (probe != read_json(work / 'probe/execution.json') or
                digest(work / 'probe/console.log') != probe.get('console_sha256') or
                probe.get('argv') != ['/usr/bin/true'] or
                '--unshare-net' not in probe.get('sandbox_argv', []) or
                any(digest(work / 'probe' / name) != probe.get(key) for name, key in
                    [('stdout.log', 'stdout_sha256'), ('stderr.log', 'stderr_sha256')])):
            raise ValueError('malformed_probe_receipt')
        if (work / 'probe/stdout.log').read_bytes() or (work / 'probe/stderr.log').read_bytes():
            raise ValueError('malformed_probe_output')
        if cancellation and cancellation.cancelled.is_set():
            raise ValueError('cancelled')
        if time.monotonic() >= context['deadline']:
            raise ValueError('deadline_exhausted')
        if context.get('admit') and not context['admit']():
            raise ValueError('operation_stopped')
        receipt['result'] = 'ok'
    except subprocess.TimeoutExpired:
        receipt['reasons'].append('runtime_probe_timeout')
        if isinstance(context, dict) and context.get('refuse'):
            context['refuse']('runtime_probe_timeout')
    except (ValueError, KeyError, TypeError, OSError) as exc:
        receipt['reasons'].append(str(exc))
        if isinstance(context, dict) and context.get('refuse'):
            context['refuse'](str(exc))
    except KeyboardInterrupt:
        receipt['reasons'].append('cancelled')
        raise
    finally:
        receipt['duration_s'] = time.monotonic() - start
        # A prior receipt is evidence, never permission to launch or overwrite it.
        if receipt_path.exists():
            receipt_path = work / ('refused-' + receipt['invocation_id'] + '.json')
        write_json(receipt_path, receipt)
    link = {'path': receipt_path.relative_to(run).as_posix(), 'sha256': digest(receipt_path)}
    return receipt, link, executable


def verify_receipt(run, record, expected, role, boundary):
    validate_expectation(expected)
    link = record.get('preflight')
    if not isinstance(link, dict) or link.get('path') != f'review-work/{role}/preflight/receipt.json':
        raise ValueError('missing or foreign preflight receipt')
    path = Path(run) / link['path']
    if digest(path) != link.get('sha256'):
        raise ValueError('preflight receipt changed')
    receipt = read_json(path)
    if not isinstance(receipt, dict):
        raise ValueError('malformed preflight receipt')
    if (receipt.get('schema') != 'agent-ee.preflight/1' or receipt.get('role') != role or
            receipt.get('boundary') != boundary or receipt.get('expectation_id') != expected['identity'] or
            receipt.get('candidate_id') != expected['candidate_id'] or
            receipt.get('baseline_id') != expected['baseline_id'] or
            receipt.get('run_id') != expected['run_id'] or not receipt.get('invocation_id') or
            receipt.get('definition') not in expected['definitions'] or
            receipt.get('result') not in ('ok', 'refused') or
            not isinstance(receipt.get('reasons'), list) or
            any(not isinstance(receipt.get(k), (int, float)) or isinstance(receipt[k], bool) or
                not math.isfinite(receipt[k]) or receipt[k] < 0 for k in ('started_unix', 'duration_s')) or
            (record.get('execution') != 'not_run' and receipt['result'] != 'ok')):
        raise ValueError('malformed or foreign preflight receipt')
    try:
        uuid.UUID(receipt['invocation_id'])
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError('malformed preflight invocation identity') from exc
    if receipt['result'] == 'ok':
        if receipt['reasons']:
            raise ValueError('successful preflight contains refusal reasons')
        observed = receipt.get('observed', {})
        if any(observed.get(k) != expected[k] for k in ('policy_files', 'runtime_files', 'declared_tools', 'declared_installations')):
            raise ValueError('preflight observation mismatch')
        probe = receipt.get('probe', {})
        if not isinstance(probe, dict) or probe != read_json(path.parent / 'probe/execution.json'):
            raise ValueError('malformed preflight probe record')
        if probe.get('argv') != ['/usr/bin/true'] or probe.get('execution') != 'ok' or probe.get('exit_code') != 0 or '--unshare-net' not in probe.get('sandbox_argv', []):
            raise ValueError('malformed preflight probe')
        for name, key in [('stdout.log', 'stdout_sha256'), ('stderr.log', 'stderr_sha256'), ('console.log', 'console_sha256')]:
            if digest(path.parent / 'probe' / name) != probe.get(key):
                raise ValueError('preflight probe artifacts changed')
    elif not receipt.get('reasons'):
        raise ValueError('preflight refusal lacks diagnostic')
    return receipt['invocation_id']


def verify_calls(run, result, manifest):
    """Validate launch evidence before adjudication, completion or historical use."""
    if 'launch_expectation' not in result:
        if 'preflight.py' in result.get('policy_files', {}):
            raise ValueError('missing captured launch expectation')
        return
    expected = result['launch_expectation']
    validate_expectation(expected)
    selected = manifest['scope'].get('review_profile', {})
    definitions = [{**d, 'tool_mode': selected.get('tool_mode', 'investigate')}
                   for d in [*selected.get('reviewers', []),
                             *([selected['adjudicator']] if selected.get('adjudicator') else [])]]
    if expected != capture_expectation(result, manifest, definitions, inputs=Path(run) / 'inputs'):
        raise ValueError('launch expectation differs from captured run')
    invocation_ids = set()
    for record in result.get('review_executions', []):
        if record.get('admission') == 'not_attempted' and not record.get('preflight'):
            continue
        from .review_run import verify_role_call
        calls = verify_role_call(run, record, expected, record['role'], 'initial',
                                 limit=result.get('max_schema_resubmissions'))
        if calls & invocation_ids:
            raise ValueError('duplicate preflight invocation identity')
        invocation_ids.update(calls)
    if (Path(run) / 'adjudicator-execution.json').is_file():
        from .review_run import verify_role_call
        calls = verify_role_call(run, read_json(Path(run) / 'adjudicator-execution.json'),
                                 expected, 'adjudicator', 'adjudicator',
                                 limit=result.get('max_schema_resubmissions'))
        if calls & invocation_ids:
            raise ValueError('duplicate preflight invocation identity')
