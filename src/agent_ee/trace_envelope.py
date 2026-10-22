"""Shared additive versioned trace envelope ``pcb-trace-envelope/1`` (AGENTEE-430).

The envelope carries what any PCB pipeline trace needs to be compared: consumer
and stage identifiers, qualified-or-unknown identities, source/binary/native-tool
hashes, state, generation, objective, monotonic durations with explicit
boundaries, counters and sample accounting. Consumer data lives in ``payload``,
owned and versioned by the consumer; no consumer policy enters the envelope.

The published JSON Schema is ``schemas/pcb-trace-envelope-1.json``; kicad-route
keeps a byte-identical copy. ``validate`` checks the same field rules with the
standard library (a present field is checked whatever its record's status), plus the
cross-field rules the schema does not express: duration names are unique, every
parent and within name exists, and recorded bytes do not exceed the byte limit.

Version rule: a ``/1`` reader ignores unknown fields (additive), refuses any other
envelope version, and never reads a field by a changed meaning. Removing or
retyping a required field, or changing a meaning, needs ``pcb-trace-envelope/2``.
A truncated sample set is ``partial``, and a failed or interrupted run never
qualifies complete parity whatever its coverage label (``complete_parity_allowed``).
"""
import hashlib
import json
import re

SCHEMA = 'pcb-trace-envelope/1'
REQUIRED = ('schema', 'consumer', 'stage', 'identity', 'hashes', 'state', 'generation', 'objective',
            'clock', 'durations', 'counters', 'sampling', 'payload')
IDENTITIES = ('pcb', 'project', 'rules', 'experiment')
HASH_GROUPS = ('source', 'binary', 'native_tools')
STATES = ('completed', 'failed', 'interrupted')
BOUNDARIES = ('inclusive', 'exclusive')
OVERLAPS = ('disjoint', 'nested')
COVERAGE = ('complete', 'partial')
UNITS = ('ns',)
SHA256 = re.compile(r'^[0-9a-f]{64}$')
NAME = re.compile(r'^[a-z0-9][a-z0-9_.:/-]*$')


def sha256_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def canonical_sha256(value):
    """SHA-256 of canonical JSON (sorted keys, no whitespace)."""
    return sha256_bytes(json.dumps(value, sort_keys=True, separators=(',', ':')).encode())


def qualified(sha256=None, **extra):
    return {'status': 'qualified', **({'sha256': sha256} if sha256 is not None else {}), **extra}


def unknown(reason):
    return {'status': 'unknown', 'reason': reason}


def file_hash(name, path):
    """A named hash entry for a file, or an explicit unknown with the reason it could not be read."""
    try:
        with open(path, 'rb') as stream:
            return {'name': name, 'status': 'qualified', 'sha256': sha256_bytes(stream.read()), 'path': str(path)}
    except OSError as exc:
        return {'name': name, 'status': 'unknown', 'reason': f'unreadable: {exc.__class__.__name__}', 'path': str(path)}


def _text(value):
    return isinstance(value, str) and bool(value)


def _identity_problems(label, value):
    """The published ``$defs/identity`` rules: a present field is checked whatever the status."""
    if not isinstance(value, dict) or value.get('status') not in ('qualified', 'unknown'):
        return [f'{label}: status must be qualified or unknown']
    problems = []
    if 'sha256' in value and not (isinstance(value['sha256'], str) and SHA256.match(value['sha256'])):
        problems.append(f'{label}: sha256 must be 64 lowercase hex')
    if value['status'] == 'unknown':
        if not _text(value.get('reason')):
            problems.append(f'{label}: unknown needs a reason')
    elif 'sha256' not in value and not _text(value.get('id')):
        problems.append(f'{label}: qualified needs sha256 or id')
    return problems


def _labelled_problems(key, value):
    """The published ``$defs/labelled`` rules (generation, objective): an id, or unknown with a reason."""
    if not isinstance(value, dict):
        return [f'{key}: id required, or status unknown with a reason']
    problems = []
    if value.get('status') == 'unknown':
        if not _text(value.get('reason')):
            problems.append(f'{key}: unknown needs a reason')
    elif not _text(value.get('id')):
        problems.append(f'{key}: id required, or status unknown with a reason')
    if key == 'objective' and 'id' in value and not _text(value.get('version')):
        problems.append('objective: version required')
    return problems


def _count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def validate(document):
    """Problems (a list of strings) for a ``pcb-trace-envelope/1`` document; [] when valid."""
    if not isinstance(document, dict):
        return ['envelope must be an object']
    if document.get('schema') != SCHEMA:
        return [f'unsupported envelope version {document.get("schema")!r}; this reader accepts {SCHEMA}']
    problems = [f'missing required field {key}' for key in REQUIRED if key not in document]
    if problems:
        return problems
    for key in ('consumer', 'stage'):
        value = document[key]
        if not isinstance(value, dict) or not (isinstance(value.get('id'), str) and NAME.match(value['id'])):
            problems.append(f'{key}: id must be a lowercase identifier')
        elif key == 'consumer' and not (isinstance(value.get('version'), str) and value['version']):
            problems.append('consumer: version must be a non-empty string')
    identity = document['identity']
    if not isinstance(identity, dict):
        problems.append('identity must be an object')
    else:
        problems += [f'identity.{key}: missing' for key in IDENTITIES if key not in identity]
        for key, value in identity.items():
            problems += _identity_problems(f'identity.{key}', value)
    hashes = document['hashes']
    if not isinstance(hashes, dict):
        problems.append('hashes must be an object')
    else:
        for group in HASH_GROUPS:
            entries = hashes.get(group)
            if not isinstance(entries, list):
                problems.append(f'hashes.{group}: must be a list')
                continue
            for i, entry in enumerate(entries):
                label = f'hashes.{group}[{i}]'
                if not isinstance(entry, dict) or not (isinstance(entry.get('name'), str) and entry['name']):
                    problems.append(f'{label}: name required')
                else:
                    problems += _identity_problems(label, entry)
    state = document['state']
    if not isinstance(state, dict) or state.get('status') not in STATES:
        problems.append(f'state.status must be one of {", ".join(STATES)}')
    elif state['status'] != 'completed' and not _text(state.get('reason')):
        problems.append('state: a run that did not complete needs a reason')
    elif 'reason' in state and not _text(state['reason']):
        problems.append('state: reason must be a non-empty string')
    for key in ('generation', 'objective'):
        problems += _labelled_problems(key, document[key])
    clock = document['clock']
    if not isinstance(clock, dict) or clock.get('source') != 'monotonic' or clock.get('unit') not in UNITS:
        problems.append('clock: source must be monotonic with a named unit')
    durations, names = document['durations'], set()
    if not isinstance(durations, list):
        problems.append('durations must be a list')
        durations = []
    for i, row in enumerate(durations):
        label = f'durations[{i}]'
        if not isinstance(row, dict) or not (isinstance(row.get('name'), str) and NAME.match(row['name'])):
            problems.append(f'{label}: name must be a lowercase identifier')
            continue
        if row['name'] in names:
            problems.append(f'{label}: duplicate name {row["name"]}')
        names.add(row['name'])
        if row.get('unit') not in UNITS or not _count(row.get('value')) or not _count(row.get('count')):
            problems.append(f'{label}: unit, non-negative integer value and count required')
        if row.get('boundary') not in BOUNDARIES or row.get('overlap') not in OVERLAPS:
            problems.append(f'{label}: boundary must be inclusive|exclusive and overlap disjoint|nested')
        if 'parent' not in row or not (row['parent'] is None or isinstance(row['parent'], str)):
            problems.append(f'{label}: parent must be a duration name or null')
        within = row.get('within', [])
        if not (isinstance(within, list) and all(isinstance(n, str) and NAME.match(n) for n in within)):
            problems.append(f'{label}: within must list duration names')
        elif row.get('overlap') == 'nested' and not within:
            problems.append(f'{label}: nested overlap must name the durations it may run within')
    for i, row in enumerate(durations):
        if isinstance(row, dict) and isinstance(row.get('name'), str):
            # Only type-valid references are cross-checked; the first pass already reported the others.
            for other in [row.get('parent')] + list(row.get('within', []) if isinstance(row.get('within'), list) else []):
                if isinstance(other, str) and other not in names:
                    problems.append(f'durations[{i}]: names unknown duration {other}')
    counters = document['counters']
    if not isinstance(counters, dict) or not all(_count(v) for v in counters.values()):
        problems.append('counters must map names to non-negative integers')
    sampling = document['sampling']
    problems += sampling_problems(sampling)
    payload = document['payload']
    if not isinstance(payload, dict) or not (isinstance(payload.get('schema'), str) and payload['schema']):
        problems.append('payload must be an object naming its consumer-owned schema')
    return problems


def sampling_problems(sampling):
    if not isinstance(sampling, dict):
        return ['sampling must be an object']
    problems = []
    limits = sampling.get('limits')
    if not isinstance(limits, dict) or not limits or not all(_count(v) for v in limits.values()):
        problems.append('sampling.limits must map names to non-negative integers')
    for key in ('recorded', 'bytes', 'dropped'):
        if not _count(sampling.get(key)):
            problems.append(f'sampling.{key}: non-negative integer required')
    reasons = sampling.get('partial_reasons')
    if sampling.get('coverage') not in COVERAGE or not isinstance(reasons, list) or not all(
            isinstance(r, str) and r for r in reasons):
        return problems + ['sampling: coverage complete|partial and a partial_reasons list required']
    if sampling['coverage'] == 'complete' and (reasons or sampling.get('dropped')):
        problems.append('sampling: complete coverage cannot drop samples or carry partial reasons')
    if sampling['coverage'] == 'partial' and not reasons:
        problems.append('sampling: partial coverage needs at least one reason')
    if isinstance(limits, dict) and _count(limits.get('bytes')) and _count(sampling.get('bytes')) \
            and sampling['bytes'] > limits['bytes']:
        problems.append('sampling: recorded bytes exceed the byte limit')
    return problems


def check(document):
    problems = validate(document)
    if problems:
        raise ValueError('invalid trace envelope: ' + '; '.join(problems))
    return document


def complete_parity_allowed(document):
    """Only a valid envelope of a completed run with complete sample coverage can qualify complete
    parity. A failed or interrupted run never does, whatever its coverage label says."""
    return (not validate(document) and document['state']['status'] == 'completed'
            and document['sampling']['coverage'] == 'complete')
