"""Bounded repairs of owned schematic pin diagnostics, with retained failed attempts.

Successful attempts keep the ordinary campaign proposal/receipt contract. Failed
attempts are evidence only; they never supply an adopted native output.
"""
import json
from pathlib import Path
import shutil
import time

from .storage import digest, identity, json_bytes, read_json, write_json

SCHEMA = 'agent-ee.intent-repair/1'
PIN_CODES = {'resolution_unknown_pin', 'resolution_unaccounted_pin'}


def _owned_spec(spec, manifest, editable):
    from .schematic_stage import configuration
    from .stage_receipts import validate_step
    argv = spec['argv']
    prefix = ['/usr/bin/env', 'PYTHONPATH=/tool/agent-ee', '/usr/bin/python3',
              '-B', '-P', '-m', 'agent_ee.schematic_stage', 'emit']
    names = ['board', 'schematic', 'intent', 'layout', 'captures', 'footprints']
    if (argv[:8] != prefix or len(argv) != 20 or
            argv[8::2] != ['--' + name for name in names] or
            len(spec['mounts']) != 1 or spec['mounts'][0]['at'] != '/tool/agent-ee'):
        return None
    args = dict(zip(names, argv[9::2]))
    if (args['board'] != manifest['scope']['board'] or
            args['schematic'] != manifest['scope']['schematic'] or args['intent'] not in editable):
        return None
    inputs = spec.get('inputs', [])
    if len(inputs) < 3 or inputs[0] != args['intent'] or inputs[-2:] != [args['layout'], args['footprints']]:
        return None
    # Generate the owned declaration from this installation, then substitute the
    # retained mount path. Replay needs the owned bytes, not the old host pathname.
    expected = configuration(Path(__file__).resolve().parents[1], **args, seeds=inputs[1:-2])['implementation_steps'][0]
    expected['mounts'] = spec['mounts']
    if validate_step(expected, host=False) != spec:
        return None
    return args


def _source_identity(record, policy):
    if (not isinstance(record, dict) or set(record) != {'schema', 'files'} or
            record['schema'] != 'agent-ee.intent-producer-source/1' or
            not isinstance(record['files'], dict)):
        raise ValueError('malformed intent producer source inventory')
    files = record['files']
    from .stage_receipts import _relative
    import re
    for rel, sha in files.items():
        _relative(rel, 'owned source file')
        if not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{64}', sha):
            raise ValueError('invalid intent producer source hash')
    if {p: sha for p, sha in files.items() if p.endswith('.py')} != {
            'agent_ee/' + p: sha for p, sha in policy.items()}:
        raise ValueError('intent producer source differs from frozen campaign policy')
    return {'sha256': identity(files), 'files': len(files)}


def pin_source(work, manifest, state, config, plan):
    """Bind the running owned code before producers, including its exact mount inventory."""
    editable = config.get('writer_editable')
    if editable is None or not any(e['kind'] == 'step' and
            _owned_spec(e['spec'], manifest, editable) is not None for e in plan):
        return
    from .stage_receipts import file_tree
    from .tool_source import snapshot
    files, other = file_tree(snapshot(Path(__file__).resolve().parents[1]))
    if other:
        raise ValueError('owned intent producer source contains non-files')
    record = {'schema': 'agent-ee.intent-producer-source/1', 'files': files}
    _source_identity(record, state['launch_environment']['policy_files'])
    write_json(work / 'intent-producer-source.json', record)


def _failure(work, manifest, state, config, plan):
    """Return bound feedback only for the two owned, writer-repairable pin errors."""
    from .campaign import check_writer_files
    from .stage_receipts import (ADOPTABLE, after_tree,
                                 validate_receipt, verify_implementation)
    editable = config.get('writer_editable')
    index_path = work / 'stage-receipts.json'
    if editable is None or not index_path.is_file() or (work / 'proposal.scope.json').exists():
        return None
    if not plan or any(entry['kind'] != 'step' or entry['spec']['stage'] != 'schematic' for entry in plan):
        return None
    index = verify_implementation(work, digest(index_path), len(state['history']), manifest['candidate_id'],
                                  steps=[e['spec'] for e in plan])
    if not index['receipts']:
        return None
    receipts = [validate_receipt(work / link['path'].rsplit('/', 1)[0]) for link in index['receipts']]
    final = receipts[-1]
    args = _owned_spec(final['spec'], manifest, editable)
    if args is None or final['status'] != 'failed' or final['exit_code'] != 1:
        return None
    if (final['violations'] != ['missing_output:' + args['schematic']] or
            any(value is not None for value in final['outputs'].values()) or
            final['execution']['execution'] != 'failed' or
            final['version_execution']['execution'] != 'ok'):
        return None
    # Compare the receipt against a pre-producer inventory of the owned source.
    # Rehashing the live tree here would mistake newly created Python bytecode
    # caches for a different producer, and would make offline replay host-dependent.
    source_path = work / 'intent-producer-source.json'
    if not source_path.is_file():
        return None
    owned = _source_identity(read_json(source_path), state['launch_environment']['policy_files'])
    if (final['tool']['source_tree'] != {'path': '/tool/agent-ee', **owned} or
            final['tool']['mounts'] != [{'at': '/tool/agent-ee', **owned}] or
            final['tool']['executable']['sha256'] != digest('/usr/bin/env')):
        return None
    check_writer_files(receipts[0]['inputs'], manifest, editable)
    expected = receipts[0]['inputs']
    for receipt in receipts:
        if receipt['inputs'] != expected:
            raise ValueError('intent retry producer prefix input chain differs')
        if receipt is not final:
            if receipt['status'] not in ADOPTABLE:
                raise ValueError('intent retry producer followed a failed predecessor')
            expected = after_tree(receipt)
    if index['delivered'] != expected:
        raise ValueError('intent retry delivered tree differs from failed prefix')
    receipt_rel = index['receipts'][-1]['path']
    receipt_dir = (work / receipt_rel).parent
    from .connectivity_ir import _read_json
    from .footprint_library import read_captures
    from .schematic_emitter import emit_schematic
    inputs = receipt_dir / 'inputs'
    raw = (inputs / args['intent']).read_bytes()
    libraries = read_captures(inputs / args['captures'], [lib['path'] for lib in _read_json(raw)['libraries']])
    emission, files = emit_schematic(raw, libraries, (inputs / args['layout']).read_bytes())
    emission_rel = str(Path(receipt_rel).parent / 'run/out/emission.json')
    if emission != read_json(work / emission_rel):
        raise ValueError('intent retry diagnostic differs from owned emitter replay')
    codes = {f['code'] for f in emission['findings']}
    if files is not None or not codes & PIN_CODES or not codes <= PIN_CODES | {'resolution_rejected'}:
        return None
    return {'schema': SCHEMA, 'candidate_id': manifest['candidate_id'],
            'receipt': receipt_rel, 'receipt_sha256': digest(work / receipt_rel),
            'emission': emission_rel, 'emission_sha256': digest(work / emission_rel),
            'intent': args['intent'], 'intent_sha256': final['inputs'][args['intent']],
            'findings': emission['findings']}


def seed(work, archive, editable, feedback):
    """Carry only the previous writer's inputs, never predecessor/native output."""
    index = read_json(archive / 'stage-receipts.json')
    before_producers = (archive / index['receipts'][0]['path']).parent / 'inputs'
    for rel in editable:
        shutil.copyfile(before_producers / rel, work / 'review-work/implementation/design' / rel)
    write_json(work / 'work/intent-feedback.json', feedback)
    return ('\nOWNED SCHEMATIC PRODUCER FEEDBACK (advisory; no output adopted):\n' +
            json.dumps(feedback, sort_keys=True) +
            '\nRepair only your allowed intent inputs. The coordinator will rerun the complete '
            'schematic producer sequence. Never infer an electrical no-connect decision from this diagnostic. '
            'The bound record is at /evidence/work/intent-feedback.json.')


def implement(root, previous, definition, state, deadline, attempt):
    from .campaign import step_plan
    from .review_run import tree_hashes
    manifest = read_json(previous / 'candidate.json')
    config = read_json(root / 'config.json')
    plan = step_plan(config, manifest, state['history'][-1], state.get('step_plan'))
    limit = (manifest['scope'].get('stages') or {}).get('finding_retry_limit', 1)
    name = str(len(state['history']))
    work = root / 'implementations' / name
    prior = []
    retry = None
    while True:
        if time.monotonic() >= deadline:
            raise ValueError('intent repair deadline exhausted')
        try:
            return attempt(root, previous, definition, state, deadline, retry=retry)
        except ValueError:
            feedback = _failure(work, manifest, state, config, plan)
            if feedback is None:
                raise
            if feedback['intent_sha256'] in [entry['feedback']['intent_sha256'] for entry in prior]:
                raise ValueError('intent repair made no progress: repeated failed intent')
            if len(prior) + 1 >= limit:
                raise ValueError('intent repair finding_retry_limit exhausted')
            if time.monotonic() >= deadline:
                raise ValueError('intent repair deadline exhausted')
            archive_rel = f'intent-retries/{name}/{len(prior) + 1}'
            archive = root / archive_rel
            feedback = {**feedback, 'attempt': len(prior) + 1, 'archive': archive_rel}
            entry = {'path': archive_rel, 'tree_sha256': identity(tree_hashes(work)), 'feedback': feedback}
            archive.parent.mkdir(parents=True, exist_ok=True)
            try:
                # Same-filesystem rename retains complete custody, including if
                # interrupted before the next writer has started.
                work.rename(archive)
            finally:
                if archive.is_dir() and not work.exists():
                    prior.append(entry)
                    state.setdefault('intent_retries', {})[name] = list(prior)
                    state['implementation_records'].pop(name, None)
                    state['stage_receipts'].pop(name, None)
                    write_json(root / 'state.json', state)
            retry = (archive, config['writer_editable'], feedback)


def verify(root, state, config):
    """Verify archived failures and the exact feedback used by each following writer."""
    from .campaign import step_plan
    from .preflight import capture_expectation
    from .review_run import tree_hashes, verify_role_call, with_resubmission_notice
    anchors = state.get('intent_retries', {})
    area = root / 'intent-retries'
    if area.is_symlink():
        raise ValueError('intent retry archive directory is a link')
    present = {p.name for p in area.iterdir()} if area.exists() else set()
    if not isinstance(anchors, dict) or set(anchors) != present:
        raise ValueError('intent retry archive inventory changed')
    for name, entries in anchors.items():
        if not name.isdigit() or str(int(name)) != name or not 1 <= int(name) <= len(state['history']):
            raise ValueError('intent retry names no implementation')
        source = Path(state['history'][int(name) - 1]['run'])
        manifest = read_json(source / 'candidate.json')
        plan = step_plan(config, manifest, state['history'][int(name) - 1], state.get('step_plan'))
        limit = manifest['scope']['stages']['finding_retry_limit']
        if not isinstance(entries, list) or not 1 <= len(entries) < limit:
            raise ValueError('intent retry attempt bound exceeded')
        if (area / name).is_symlink():
            raise ValueError('intent retry implementation directory is a link')
        if {p.name for p in (area / name).iterdir()} != {str(i) for i in range(1, len(entries) + 1)}:
            raise ValueError('intent retry attempt inventory changed')
        seen, call_ids = set(), set()
        expected = capture_expectation({**read_json(source / 'result.json'), **state['launch_environment']},
            manifest, [state['implementation_definition']], inputs=source / 'inputs')
        for i, entry in enumerate(entries, 1):
            rel = f'intent-retries/{name}/{i}'
            archive = root / rel
            if (set(entry) != {'path', 'tree_sha256', 'feedback'} or entry['path'] != rel or
                    archive.is_symlink() or identity(tree_hashes(archive)) != entry['tree_sha256']):
                raise ValueError('intent retry archived attempt changed')
            if read_json(archive / 'launch-expectation.json') != expected:
                raise ValueError('intent retry launch expectation changed')
            record = read_json(archive / 'implementation-execution.json')
            if record['execution'] != 'ok':
                raise ValueError('intent retry followed an unsuccessful writer')
            ids = verify_role_call(archive, record, expected, 'implementation', 'implementation',
                                   limit=state['max_schema_resubmissions'])
            if call_ids & ids:
                raise ValueError('intent retry reused a writer invocation')
            call_ids.update(ids)
            feedback = _failure(archive, manifest, {**state, 'history': state['history'][:int(name)]}, config, plan)
            if feedback is None:
                raise ValueError('intent retry followed a nonrecoverable producer failure')
            feedback = {**feedback, 'attempt': i, 'archive': rel}
            if entry['feedback'] != feedback or feedback['intent_sha256'] in seen:
                raise ValueError('intent retry feedback or progress changed')
            seen.add(feedback['intent_sha256'])
            following = root / f'intent-retries/{name}/{i+1}' if i < len(entries) else root / 'implementations' / name
            if not following.exists():
                if int(name) == len(state['history']) and state['state'] == 'incomplete':
                    continue
                raise ValueError('intent retry lacks a following implementation')
            if not (following / 'work/intent-feedback.json').exists():
                if (int(name) == len(state['history']) and state['state'] == 'incomplete' and
                        not any((following / p).exists() for p in
                                ('implementation-execution.json', 'stage-receipts.json', 'proposal.scope.json'))):
                    continue  # Interrupted while preparing the next call, before any writer ran.
                raise ValueError('intent retry lacks feedback for the following writer')
            if (following / 'work/intent-feedback.json').read_bytes() != json_bytes(feedback):
                raise ValueError('intent retry following feedback changed')
            prompt = (following / 'implementation-prompt.txt').read_text()
            if json.dumps(feedback, sort_keys=True) not in prompt:
                raise ValueError('intent retry prompt omitted its bound feedback')
            if i == len(entries):
                from .stage_receipts import ADOPTABLE
                index_path = following / 'stage-receipts.json'
                failed = False
                if index_path.exists():
                    current = read_json(index_path)
                    failed = any(read_json(following / link['path'])['status'] not in ADOPTABLE
                                 for link in current['receipts'])
                execution_path = following / 'implementation-execution.json'
                if execution_path.exists():
                    failed = failed or read_json(execution_path)['execution'] != 'ok'
                if failed and (int(name) != len(state['history']) or state['state'] != 'incomplete' or
                               (following / 'proposal.scope.json').exists()):
                    raise ValueError('intent retry failure must be terminal incomplete')
            if (following / 'implementation-execution.json').exists():
                execution = read_json(following / 'implementation-execution.json')
                if i == len(entries):
                    ids = verify_role_call(following, execution, expected, 'implementation', 'implementation',
                                           limit=state['max_schema_resubmissions'])
                    if call_ids & ids:
                        raise ValueError('intent retry reused a writer invocation')
                for call in execution['attempts']:
                    supplied = (following / call['path'] / 'prompt.txt').read_text()
                    if not supplied.startswith(with_resubmission_notice(prompt, state['max_schema_resubmissions'])):
                        raise ValueError('intent retry writer did not receive bound feedback')
