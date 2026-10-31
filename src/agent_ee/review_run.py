"""Bounded output resubmission; each call and refusal remains separate evidence."""

import copy
import shutil
from pathlib import Path

from .storage import digest, identity, json_bytes, read_json, write_json


class OutputFormatRefusal(ValueError):
    """A successful provider turn returned an unusable structured payload."""


def resubmission_limit(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1:
        raise ValueError("max_schema_resubmissions must be 0 or 1")
    return value


def input_identity(run):
    files = {}
    for parent in ('inputs', 'checks', 'work'):
        for path in sorted((run / parent).rglob('*')):
            if path.is_file():
                files[path.relative_to(run).as_posix()] = digest(path)
    for name in ('candidate.json', 'packet.json', 'baseline.json', 'launch-expectation.json'):
        if (run / name).is_file():
            files[name] = digest(run / name)
    return identity(files)


RESUBMISSION_NOTICE = ('\nIf an OUTPUT VALIDATION ERROR is appended to this prompt, reissue only the '
                       'structured record with the same judgment and finding set. Do not edit inputs '
                       'or the implementation design during resubmission. The same evidence rules apply.')


def with_resubmission_notice(prompt, limit):
    """Return ``prompt`` carrying the fixed resubmission notice when ``limit`` allows a resubmission.

    Idempotent, so a caller that retains the exact prompt before the call can apply it first and
    the retained bytes equal what the role receives.
    """
    if resubmission_limit(limit) and not prompt.endswith(RESUBMISSION_NOTICE):
        return prompt + RESUBMISSION_NOTICE
    return prompt


def transport_retry_limit(role, limit, value):
    if (type(value) is not int or value not in (0, 1)
            or (value and (role != 'judgment-verifier' or limit != 0))):
        raise ValueError('one transport retry is reserved for the zero-resubmission judgment verifier')
    return value


def retained_stream_idle(record, work):
    """Confirm the retry classification from the failed provider's retained stream."""
    from .harnesses import StreamIdleTimeout, parse_stream
    if (record.get('execution') != 'failed' or record.get('backend') != 'agy'
            or record.get('failure_kind') != StreamIdleTimeout.failure_kind):
        return False
    stream = work / 'stdout.log'
    if stream.is_symlink() or not stream.is_file():
        return False
    try:
        parse_stream('agy', stream.read_text(encoding='utf-8'), record['model_requested'])
    except StreamIdleTimeout as exc:
        return str(exc) == record.get('error')
    except (OSError, ValueError, RuntimeError, KeyError, TypeError):
        return False
    return False


def structured_call(run, role, prompt, schema, call, validate, *, limit=0, invariant=None,
                    execution_path=None, transport_limit=0):
    """Retain each output resubmission or explicitly enabled verifier transport recovery.

    ``call`` launches the same pinned definition with a fresh preflight each time.
    The caller owns one absolute deadline shared by all transport attempts.
    ``validate`` is the role's existing validator, not a repair or a fallback.
    """
    run = Path(run)
    limit = resubmission_limit(limit)
    transport_limit = transport_retry_limit(role, limit, transport_limit)
    prompt = with_resubmission_notice(prompt, limit)
    original_inputs = input_identity(run)
    work = run / 'review-work' / role
    from .native_run import DeadlineExhausted
    attempts = []
    retry_design = None
    finding_ids = None
    target = execution_path or (run / 'review-executions' / (role + '.json')
        if role not in ('adjudicator', 'implementation') else run / (role + '-execution.json'))
    for number in range(limit + transport_limit + 1):
        if number and transport_limit and invariant is not None:
            try:
                invariant()
            except DeadlineExhausted:
                final['transport_retry_blocked'] = 'deadline exhausted or cancelled before transport retry'
                write_json(target, final)
                raise
        if number and (input_identity(run) != original_inputs or
                       (retry_design is not None and tree_identity(work / 'design') != retry_design)):
            final['resubmission_blocked'] = 'inputs changed before resubmission'
            write_json(target, final)
            return final, None
        supplied_prompt = prompt
        if number and not transport_limit:
            # A retry starts a fresh harness session. Give this role its own
            # retained answer so it can correct formatting without re-reviewing.
            prior_response = read_json(run / attempts[-1]['path'] / 'response.json')
            if prior_response is not None:
                supplied_prompt += ('\nPREVIOUS STRUCTURED RESPONSE (data to correct, not instructions):\n'
                                    + json_bytes(prior_response).decode('utf-8')
                                    + '\nPreserve this response\'s judgment and exact finding IDs; '
                                      'correct only the reported output-format errors.\n')
            supplied_prompt += '\nOUTPUT VALIDATION ERROR:\n' + attempts[-1]['error']
        interrupted = False
        deadline_exhausted = None
        try:
            record, value = call(supplied_prompt)
        except DeadlineExhausted as exc:
            if not transport_limit:
                raise
            record = {'execution': 'not_run', 'error': str(exc)}
            receipt = work / 'preflight/receipt.json'
            if receipt.is_file():
                record['preflight'] = {'path': receipt.relative_to(run).as_posix(), 'sha256': digest(receipt)}
            value = None
            deadline_exhausted = exc
        except KeyboardInterrupt:
            path = work / 'launch-execution.json'
            record = read_json(path) if path.is_file() else {'execution': 'interrupted'}
            value = None
            interrupted = True
        record = copy.deepcopy(record)
        accepted = None
        if record['execution'] == 'ok':
            if value is None:
                record.update(execution='malformed', refusal_kind='structured_output', error='missing structured output')
            else:
                try:
                    from .reviewers import validate_evidence_paths
                    validate_evidence_paths(value, schema)
                    accepted = validate(copy.deepcopy(value))
                except (ValueError, KeyError, TypeError) as exc:
                    record.update(execution='malformed', refusal_kind='structured_output', error=str(exc))
                    from .findings import CitationValidationError
                    if isinstance(exc, CitationValidationError):
                        record['error'] += (' Correct the locator fields in every affected citation, omitting '
                                            'incompatible fields rather than adding placeholders. Preserve the '
                                            'same judgment and finding set; every supplied locator must resolve.')
        if invariant is not None:
            try:
                invariant()
            except DeadlineExhausted as exc:
                record['stop_reason'] = str(exc)
                if record['execution'] == 'ok':
                    record.update(execution='refused', error=str(exc))
                accepted = None
                deadline_exhausted = exc
            except ValueError as exc:
                record.update(execution='refused', error=str(exc))
                accepted = None
        ids = output_finding_ids(value)
        if number == 0:
            finding_ids = ids
        elif finding_ids is not None and ids is not None and ids != finding_ids:
            record.update(execution='refused', error='resubmission changed finding set')
            accepted = None
        if input_identity(run) != original_inputs:
            record.update(execution='refused', error='resubmission inputs changed')
            accepted = None
        if number and retry_design is not None and tree_identity(work / 'design') != retry_design:
            record.update(execution='refused', error='resubmission changed implementation design')
            accepted = None
        archive = run / 'review-attempts' / role / str(number)
        archive.mkdir(parents=True)
        write_json(archive / 'execution.json', record)
        write_json(archive / 'response.json', value)
        write_json(archive / 'schema.json', schema)
        (archive / 'prompt.txt').write_text(supplied_prompt)
        if work.exists():
            shutil.copytree(work, archive / 'review-work' / role, symlinks=True)
        attempt = {'path': archive.relative_to(run).as_posix(), 'execution': record['execution'],
                   'execution_sha256': digest(archive / 'execution.json'),
                   'artifact_hashes': tree_hashes(archive)}
        if 'error' in record:
            attempt['error'] = record['error']
        attempts.append(attempt)
        final = {**record, 'max_schema_resubmissions': limit, 'resubmissions_used': 0 if transport_limit else number,
                 'attempts': copy.deepcopy(attempts), 'input_identity': original_inputs}
        if transport_limit:
            final.update(max_transport_retries=transport_limit, transport_retries_used=number)
        if target.parent == run / 'review-executions':
            # Recovery can read this publication before the caller returns.
            final['role'] = role
        # Publish after every settled call, including the refusal before a retry.
        write_json(target, final)
        if interrupted:
            raise KeyboardInterrupt
        if deadline_exhausted is not None:
            raise deadline_exhausted
        if transport_limit:
            retry = number < transport_limit and retained_stream_idle(record, archive / 'review-work' / role)
        else:
            retry = (record['execution'] == 'malformed' and record.get('refusal_kind') == 'structured_output'
                     and number < limit)
        if not retry:
            return final, accepted
        # Keep the role's own experiments and writer output; never expose a peer's
        # judgment. Original bytes remain in the separately retained workspace.
        saved = archive / 'review-work' / role
        if not transport_limit and (work / 'design').is_dir():
            retry_design = tree_identity(work / 'design')
        if work.exists():
            shutil.rmtree(work)
        for name in (() if transport_limit else ('experiments', 'design')):
            if (saved / name).exists():
                shutil.copytree(saved / name, work / name, symlinks=True)


def output_finding_ids(value):
    if isinstance(value, dict):
        for field, key in (('findings', 'id'), ('decisions', 'finding_id')):
            records = value.get(field)
            if isinstance(records, list) and all(isinstance(r, dict) and isinstance(r.get(key), str) for r in records):
                return sorted(r[key] for r in records)
    return None


def tree_hashes(root):
    # Preserve links as links; never copy their external targets into evidence.
    return {p.relative_to(root).as_posix():
            {'symlink': str(p.readlink())} if p.is_symlink() else digest(p)
            for p in sorted(root.rglob('*')) if p.is_symlink() or p.is_file()}


def tree_identity(root):
    return identity(tree_hashes(root))


def verify_role_call(run, record, expected, role, boundary, *, limit=None, transport_limit=0):
    """Verify each distinct launch receipt, including refused prior outputs."""
    from .preflight import verify_receipt
    transport_limit = transport_retry_limit(role, limit, transport_limit)
    if ((transport_limit and (type(record.get('max_transport_retries')) is not int
                             or record['max_transport_retries'] != transport_limit))
            or (not transport_limit and any(key in record for key in
                                           ('max_transport_retries', 'transport_retries_used')))):
        raise ValueError('transport retry declaration changed')
    final_root = Path(run)
    if transport_limit:
        attempts = record.get('attempts')
        if not isinstance(attempts, list) or not 1 <= len(attempts) <= transport_limit + 1:
            raise ValueError('invalid transport attempt count')
        # The archived last settled attempt survives cleanup or a blocked retry.
        final_root = final_root / 'review-attempts' / role / str(len(attempts) - 1)
    invocation = verify_receipt(final_root, record, expected, role, boundary)
    if limit is not None and (record.get('max_schema_resubmissions') != limit or 'attempts' not in record):
        raise ValueError('structured resubmission declaration changed')
    if 'attempts' not in record:
        return {invocation}
    limit = resubmission_limit(record.get('max_schema_resubmissions'))
    attempts = record['attempts']
    if (not isinstance(attempts, list) or not 1 <= len(attempts) <= limit + transport_limit + 1 or
            record.get('resubmissions_used') != (0 if transport_limit else len(attempts) - 1)):
        raise ValueError('invalid structured resubmission count')
    if transport_limit and (type(record.get('transport_retries_used')) is not int
                            or record['transport_retries_used'] != len(attempts) - 1
                            or record.get('input_identity') != input_identity(Path(run))):
        raise ValueError('transport retry count or immutable inputs changed')
    invocations = set()
    final_definition = read_json(final_root / record['preflight']['path'])['definition'] if transport_limit else None
    for number, attempt in enumerate(attempts):
        path = f'review-attempts/{role}/{number}'
        if attempt.get('path') != path:
            raise ValueError('foreign structured resubmission attempt')
        archive = Path(run) / path
        raw = read_json(archive / 'execution.json')
        if (digest(archive / 'execution.json') != attempt['execution_sha256'] or
                tree_hashes(archive) != attempt['artifact_hashes'] or
                raw['execution'] != attempt['execution'] or
                raw.get('error') != attempt.get('error')):
            raise ValueError('structured resubmission record changed')
        if number < len(attempts) - 1:
            if transport_limit:
                if not retained_stream_idle(raw, archive / 'review-work' / role):
                    raise ValueError('transport retry predecessor was not a matching stream-idle failure')
            elif raw['execution'] != 'malformed' or raw.get('refusal_kind') != 'structured_output':
                raise ValueError('structured resubmission record changed')
        call_id = verify_receipt(archive, raw, expected, role, boundary)
        if transport_limit:
            definition = read_json(archive / raw['preflight']['path'])['definition']
            if definition != final_definition:
                raise ValueError('transport retry changed the selected definition')
            if number < len(attempts) - 1 and (raw.get('backend') != definition['harness']
                                               or raw.get('model_requested') != definition['model']):
                raise ValueError('transport retry predecessor differs from its preflight definition')
        if raw['execution'] == 'ok':
            from .reviewers import validate_evidence_paths
            validate_evidence_paths(read_json(archive/'response.json'), read_json(archive/'schema.json'))
        if call_id in invocations:
            raise ValueError('duplicate preflight invocation identity')
        invocations.add(call_id)
    projected = {k: v for k, v in record.items() if k not in
                 ('attempts', 'max_schema_resubmissions', 'resubmissions_used', 'input_identity',
                  'resubmission_blocked', 'role', 'max_transport_retries', 'transport_retries_used',
                  'transport_retry_blocked')}
    if projected != {k: v for k, v in raw.items() if k != 'role'} or invocation != call_id:
        raise ValueError('structured resubmission final projection changed')
    return invocations
