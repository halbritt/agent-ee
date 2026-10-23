"""Semantic architect producer (AGENTEE-389 S2, opt-in, not wired at runtime).

One configured subscription call proposes the assertions of an
``agent-ee.physical-intent/1`` document for one sealed review run. The
coordinator composes the document (schema and source hashes are its own, never
the model's) and the unchanged S1 compiler is the only judge: a refusal is fed
back once as a deterministic validation finding, so a stage makes at most one
proposal plus one bounded correction, within the caller's deadline. The call
goes through the existing role boundary (``reviewers.reviewer_call``, its
preflight receipt and ``review_run.structured_call``); an execution, auth or
quota failure stops the operation with no other route attempted. When a
captured reference intent is supplied, every assertion that differs from it, is
missing or is extra stays unresolved and nothing is published. Manual and
disabled modes keep the S1 behaviour. Runtime activation remains gated by
KICADROUTE-91.
"""
import json
import math
import shutil
import time
import uuid
from pathlib import Path

from .baseline import load_baseline
from .candidate import safe_relative
from . import locality_provenance, topology_rules
from .physical_intent import (DIAGNOSTIC_KINDS, DIAGNOSTIC_STATUSES, LOCALITY_METRICS, MOVES, SCHEMA as INTENT_SCHEMA,
                              UNSUPPORTED_KINDS, IntentRefused, compile_and_publish, compile_plan, native_board,
                              publish, verify_receipt)
from .profiles import validate_definition
from .storage import digest, identity, read_json, rename_noreplace, write_json

MODES = ('manual', 'architect', 'disabled')
RECEIPT_SCHEMA = 'agent-ee.physical-architect-receipt/1'
ROLE = 'architect'
# One proposal plus at most one correction for deterministic validation findings.
CORRECTIONS = 1
# Seconds of the caller's deadline kept for compilation, comparison, verification and publication;
# no call launches into it, and nothing publishes once the deadline itself has passed.
RESERVE_S = 5.0
# A required obligation's semantic restriction; a correction keeps all of it (review ee389-s2 H1).
OBLIGATION_CONTENT = ('kind', 'requirement', 'terminals', 'joins', 'forbidden_shared', 'proxy')
LOCALITY_IDENTITY = ('metric', 'kind', 'ic', 'inductor', 'return_net')
ASSERTIONS = ('terminals', 'blocks', 'residual', 'moves', 'locality', 'obligations', 'diagnostics')
# Concrete nested shapes for every admitted assertion (review ee389-s2 H3). harnesses.strict_schema
# closes each object for Codex and turns each optional property into a nullable one; compose drops
# those nulls, since no intent field is ever null. Only shapes the S1 compiler can admit are offered.
TEXT, TEXTS = {'type': 'string'}, {'type': 'array', 'items': {'type': 'string'}}


def _object(required, optional=None):
    properties = {**required, **(optional or {})}
    return {'type': 'object', 'additionalProperties': False, 'required': list(required), 'properties': properties}


_SOURCE = {'path': TEXT, 'sha256': TEXT, 'locator': TEXT, 'quote': TEXT}
_ENDPOINT = _object({'reference': TEXT, 'pad': TEXT, 'net': TEXT, 'uuid': TEXT})
_PROVENANCE = _object({'class': {'type': 'string', 'enum': sorted(locality_provenance.CLASSES)}, 'basis': TEXT}, {
    'decision_reference': _object({**_SOURCE, 'owner': TEXT}),
    'derivation': _object({'kind': {'type': 'string', 'enum': ['numeric']}, 'value_nm': {'type': 'integer'},
                           'source': _object(_SOURCE)})})
_PAIR = _object({'from': _ENDPOINT, 'to': _ENDPOINT, 'max_distance_nm': {'type': 'integer'}, 'citation': TEXT},
                {'provenance': _PROVENANCE})
ASSERTION_SCHEMAS = {
    'terminals': _object({key: TEXT for key in ('id', 'reference', 'pin', 'pad_uuid', 'function', 'fact', 'net',
                                                 'requirement')}
                         | {'class': {'type': 'string', 'enum': sorted(topology_rules.CLASSES)}}),
    'blocks': _object({'id': TEXT, 'references': TEXTS}, {'anchor': TEXT}),
    'residual': TEXT,
    'moves': _object({'reference': TEXT, 'allow': {'type': 'array', 'items': {'type': 'string', 'enum': sorted(MOVES)}}}),
    'locality': _object({'id': TEXT, 'metric': {'type': 'string', 'enum': sorted(LOCALITY_METRICS)},
                         'supply': _PAIR, 'return': _PAIR},
                        {'switch': _PAIR, 'kind': {'type': 'string', 'enum': ['buck-output-loop']}, 'ic': TEXT,
                         'inductor': TEXT, 'return_net': TEXT}),
    'obligations': _object({'id': TEXT, 'kind': {'type': 'string', 'enum': sorted(UNSUPPORTED_KINDS)},
                            'required': {'type': 'boolean'}, 'requirement': TEXT, 'terminals': TEXTS,
                            'statement': TEXT},
                           {'joins': TEXTS, 'forbidden_shared': {'type': 'array', 'items': TEXTS}, 'proxy': TEXT}),
    'diagnostics': _object({'id': TEXT, 'kind': {'type': 'string', 'enum': sorted(DIAGNOSTIC_KINDS)},
                            'status': {'type': 'string', 'enum': sorted(DIAGNOSTIC_STATUSES)}, 'statement': TEXT}),
}
PROPOSAL_SCHEMA = _object({'terminals': {'type': 'array', 'items': ASSERTION_SCHEMAS['terminals']}},
                          {**{key: {'type': 'array', 'items': ASSERTION_SCHEMAS[key]}
                              for key in ASSERTIONS if key != 'terminals'}, 'records': TEXTS})
STAGE_COPIES = ('inputs', 'checks', 'work', 'baseline.json', 'candidate.json')
INSTRUCTIONS = (
    'You are the semantic architect for one sealed electronics review run. Propose only the assertions '
    'of an agent-ee.physical-intent/1 document: terminals, and when the captured evidence supports them, '
    'blocks, residual, moves, locality, obligations and diagnostics. The coordinator adds the schema, mode '
    'and source hashes. Bind every terminal to a verified pin_function fact, its pin_map comparison, the '
    'exact native pad UUID and its native net; use only baseline requirement IDs. Do not invent facts, '
    'nets, limits, owner decisions or requirements; leave unsupported or unknown semantics out or state '
    'them as kelvin/current_loop obligations. A locality pair needs a cited max_distance_nm; list each '
    'cited captured input path in records. You may not change any design file, net, pin or constraint. '
    'Only the deterministic compiler grades the proposal. If an OUTPUT VALIDATION ERROR follows, it is the '
    'compiler finding: return one corrected proposal that keeps every required obligation and does not '
    'loosen any locality bound.\n')


def produce(mode, run, out, *, intent=None, **architect):
    """``physical_intent.mode``: disabled publishes nothing, manual compiles an explicitly selected intent."""
    if mode not in MODES:
        raise IntentRefused(f'physical intent mode must be one of {list(MODES)}')
    if mode == 'disabled':
        return None
    if mode == 'manual':
        if intent is None or architect:
            raise IntentRefused('manual mode compiles one explicitly selected intent and starts no architect call')
        return compile_and_publish(run, intent, out)
    if intent is not None:
        raise IntentRefused('architect mode composes its own intent; a manual intent is never its substitute')
    return propose(run, out, **architect)


def sources(run):
    """Coordinator-computed source binding for the compiler; the model never states a hash."""
    run = Path(run)
    manifest = read_json(run / 'candidate.json')
    pin_maps = [c for c in manifest['scope']['checks'] if c['kind'] == 'pin_map']
    if len(pin_maps) != 1:
        raise IntentRefused('architect mode needs exactly one pin_map check in the run')
    spec = pin_maps[0]
    params = spec['params']
    board = run / 'inputs' / str(safe_relative(manifest['scope']['board']))
    return {'candidate_id': manifest['candidate_id'],
            'evidence_sha256': digest(run / 'work/verification/evidence.json'),
            'pin_map_check': spec['id'], 'board_sha256': digest(board),
            'netlist_sha256': digest(run / 'work' / params['netlist_check'] / 'design.xml'),
            'facts_sha256': digest(run / 'work' / params['facts_check'] / 'facts.json'),
            'pin_map_sha256': digest(run / 'work' / spec['id'] / 'pin-map.json')}


def context(run, bound):
    """Captured evidence supplied to the architect: facts, pin map, native pads and requirements."""
    run = Path(run)
    manifest = read_json(run / 'candidate.json')
    spec = next(c for c in manifest['scope']['checks'] if c['id'] == bound['pin_map_check'])
    baseline = load_baseline(run)
    if baseline is None:
        raise IntentRefused('architect mode requires a run with an immutable baseline')
    board = native_board(run / 'inputs' / str(safe_relative(manifest['scope']['board'])))
    return {'requirements': [{'id': r['id'], **({'statement': r['statement']} if 'statement' in r else {})}
                             for r in baseline['requirements']],
            'facts': read_json(run / 'work' / spec['params']['facts_check'] / 'facts.json'),
            'pin_map': read_json(run / 'work' / spec['id'] / 'pin-map.json'),
            'native_footprints': {ref: {'locked': fp['locked'], 'pads': fp['pads']}
                                  for ref, fp in sorted(board.items())},
            'captured_records': sorted(manifest['files'])}


def _present(value):
    """Drop the nulls a strict (Codex) schema uses for absent optional properties."""
    if isinstance(value, dict):
        return {k: _present(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_present(v) for v in value]
    return value


def compose(proposal, bound, files):
    """The intent the compiler grades: proposed assertions under coordinator source hashes."""
    proposal = _present(proposal)
    if not isinstance(proposal, dict) or not set(proposal) <= {*ASSERTIONS, 'records'}:
        raise ValueError(f'architect proposal may contain only {[*ASSERTIONS, "records"]}')
    records = proposal.get('records', [])
    if not isinstance(records, list) or any(not isinstance(r, str) for r in records):
        raise ValueError('records must list captured input paths')
    unknown = sorted(r for r in records if r not in files)
    if unknown:
        raise ValueError(f'records {unknown} are not captured inputs of the run')
    intent = {'schema': INTENT_SCHEMA, 'mode': 'manual',
              'sources': {**bound, **({'records': {r: files[r] for r in sorted(set(records))}} if records else {})}}
    intent.update({key: proposal[key] for key in ASSERTIONS if key in proposal})
    return intent


def _keyed(intent):
    """Contract-bearing assertions by receipt ID, in canonical form."""
    keyed = {}
    for t in intent.get('terminals', []):
        keyed[t['id']] = t
    for o in intent.get('locality') or []:
        keyed[o['id']] = o
    for b in intent.get('blocks', []):
        keyed[f'block:{b["id"]}'] = {**b, 'references': sorted(b['references'])}
    if 'residual' in intent:
        keyed['allocation:residual'] = sorted(intent['residual'])
    for m in intent.get('moves', []):
        keyed[f'move:{m["reference"]}'] = {**m, 'allow': sorted(m['allow'])}
    for o in intent.get('obligations', []):
        keyed[o['id']] = o
    for d in intent.get('diagnostics', []):
        keyed[d['id']] = d
    return keyed


def compare(proposed, reference):
    """Every proposed or reference assertion that does not match exactly is unresolved."""
    mine, theirs = _keyed(proposed), _keyed(reference)
    differs = [{'id': key, 'proposed': mine[key], 'reference': theirs[key]}
               for key in sorted(mine.keys() & theirs.keys()) if identity(mine[key]) != identity(theirs[key])]
    result = {'matched': sorted(k for k in mine.keys() & theirs.keys() if identity(mine[k]) == identity(theirs[k])),
              'differs': differs, 'missing': sorted(theirs.keys() - mine.keys()),
              'extra': sorted(mine.keys() - theirs.keys())}
    result['unresolved'] = sorted([d['id'] for d in differs] + result['missing'] + result['extra'])
    return result


def _content(obligation):
    """A required obligation's semantic restriction, with order-free lists canonical."""
    value = {key: obligation.get(key) for key in OBLIGATION_CONTENT}
    for key in ('terminals', 'joins'):
        if isinstance(value[key], list) and all(isinstance(x, str) for x in value[key]):
            value[key] = sorted(value[key])
    if isinstance(value['forbidden_shared'], list) and all(
            isinstance(e, list) and all(isinstance(x, str) for x in e) for e in value['forbidden_shared']):
        value['forbidden_shared'] = sorted(sorted(e) for e in value['forbidden_shared'])
    return value


def _relationship(obligation):
    """A locality obligation's identity: metric, loop fields and each role's endpoint pads and nets."""
    def endpoint(value):
        return [value.get(k) for k in ('reference', 'pad', 'net')] if isinstance(value, dict) else value
    roles = {role: {side: endpoint(pair.get(side)) for side in ('from', 'to')} if isinstance(pair, dict) else pair
             for role in ('supply', 'return', 'switch') if (pair := obligation.get(role)) is not None}
    return {**{key: obligation.get(key) for key in LOCALITY_IDENTITY}, 'roles': roles}


def weakened(first, second):
    """A correction keeps every required obligation's content and every locality relationship and bound.

    Only a refused proposal is corrected, so a correction may repair what the compiler refused (a
    pin, a pad UUID) but never removes, replaces or relaxes a restriction the first proposal stated.
    """
    problems = []
    required = {o['id']: o for o in first.get('obligations', []) if isinstance(o, dict) and o.get('required') is True}
    kept = {o['id']: o for o in second.get('obligations', []) if isinstance(o, dict) and o.get('required') is True}
    for key in sorted(required):
        if key not in kept:
            problems.append(f'correction drops required obligation {key}')
        elif identity(_content(required[key])) != identity(_content(kept[key])):
            problems.append(f'correction changes required obligation {key}: {_content(required[key])} -> '
                            f'{_content(kept[key])}')
    before = {o['id']: o for o in first.get('locality') or [] if isinstance(o, dict) and 'id' in o}
    after = {o['id']: o for o in second.get('locality') or [] if isinstance(o, dict) and 'id' in o}
    problems += [f'correction drops locality obligation {i}' for i in sorted(before.keys() - after.keys())]
    for key in sorted(before.keys() & after.keys()):
        if identity(_relationship(before[key])) != identity(_relationship(after[key])):
            problems.append(f'correction changes locality relationship {key}')
        for role in ('supply', 'return', 'switch'):
            old, new = (o.get(role) if isinstance(o.get(role), dict) else {} for o in (before[key], after[key]))
            old, new = old.get('max_distance_nm'), new.get('max_distance_nm')
            if isinstance(old, int) and (not isinstance(new, int) or new > old):
                problems.append(f'correction loosens {key} {role} max_distance_nm {old} -> {new}')
    return problems


def bindings(compiled, intent):
    """Every proposed assertion maps to compiled output or an explicit unsupported/diagnostic record."""
    receipt = compiled['receipt.json']
    bound = {key: pointers for key, pointers in receipt['assertions'].items()}
    bound.update({o['id']: ['unsupported'] for o in receipt['unsupported']})
    bound.update({d['id']: ['diagnostic'] for d in receipt['diagnostics']})
    bound.update({f'move:{m["reference"]}': ['receipt:allowed_moves'] for m in receipt['allowed_moves']})
    proposed = set(_keyed(intent)) | {d['id'] for d in intent.get('diagnostics', [])}
    unbound = sorted(proposed - bound.keys())
    if unbound:
        raise IntentRefused(f'proposed assertions {unbound} are not bound by the compiled package')
    return {key: bound[key] for key in sorted(proposed)}


def _stage(run, stage):
    if stage.exists() or stage.is_symlink():
        raise IntentRefused(f'architect stage directory exists: {stage}')
    stage.mkdir(parents=True)
    for name in STAGE_COPIES:
        source = run / name
        if source.is_dir():
            shutil.copytree(source, stage / name, symlinks=True)
        elif source.is_file():
            shutil.copyfile(source, stage / name)


def propose(run, out, *, definition, deadline, stage, reference=None, cancellation=None, launch_environment=None,
            reserve_s=RESERVE_S):
    """One architect proposal plus at most one correction; publish only an accepted, fully bound package.

    ``stage`` is the architect's own directory: a read-only evidence copy for the call, every launch
    and attempt record, and the stage receipt. ``deadline`` is the caller's monotonic deadline and
    ``cancellation`` its stop; both bound the whole stage. Calls launch only before ``deadline -
    reserve_s``; validation, comparison, verification and publication must finish before
    ``deadline`` itself, or the stage stops and publishes nothing. ``reference`` is a captured
    manual intent to compare against.
    """
    from .cli import policy_files, runtime_files
    from .preflight import capture_expectation
    from .review_run import structured_call, verify_role_call
    from .reviewers import reviewer_call

    run, out, stage = Path(run), Path(out), Path(stage)
    definition = {**validate_definition(definition), 'tool_mode': 'fixed_packet'}
    if (isinstance(reserve_s, bool) or not isinstance(reserve_s, (int, float)) or not math.isfinite(reserve_s)
            or reserve_s < 0):
        raise IntentRefused('reserve_s must be finite, nonnegative seconds')
    launch_deadline = deadline - reserve_s

    def stopped():
        if cancellation is not None and cancellation.cancelled.is_set():
            return 'cancelled'
        return 'deadline_exhausted' if time.monotonic() >= deadline else None
    if out.exists() or out.is_symlink():
        raise IntentRefused(f'compiled package destination exists: {out}')
    bound = sources(run)
    manifest = read_json(run / 'candidate.json')
    if reference is not None:
        compile_plan(run, reference)  # the reference must itself be an admissible captured contract
    evidence = context(run, bound)
    _stage(run, stage)
    prompt = INSTRUCTIONS + json.dumps({'captured_evidence': evidence}, sort_keys=True)
    (stage / 'architect-prompt.txt').write_text(prompt, encoding='utf-8')
    environment = launch_environment or {'run_id': f'physical-architect-{uuid.uuid4()}',
                                         'policy_files': policy_files(), 'runtime_files': runtime_files()}
    expected = capture_expectation(environment, manifest, [definition], inputs=stage / 'inputs')
    write_json(stage / 'launch-expectation.json', expected)

    proposals, intents, state = [], [], {}

    def validate(value):
        proposals.append(value)
        if (why := stopped()):
            state['stop'] = f'{why} before validation'  # no compilation starts after a stop
            return value
        intent = compose(value, bound, manifest['files'])
        intents.append((len(proposals), intent))
        if len(proposals) > 1 and intents[0][0] == 1:
            problems = weakened(intents[0][1], intent)
            if problems:
                raise IntentRefused('; '.join(problems))
        compiled = compile_plan(run, intent)
        state.update(intent=intent, compiled=compiled, bindings=bindings(compiled, intent))
        if (why := stopped()):
            state['stop'] = f'{why} during validation'
        return value

    started = time.monotonic()
    record, response = structured_call(
        stage, ROLE, prompt, PROPOSAL_SCHEMA,
        lambda supplied: reviewer_call(stage, ROLE, definition, supplied, PROPOSAL_SCHEMA,
            min(definition['timeout_s'], max(.001, launch_deadline - time.monotonic())), cancellation=cancellation,
            launch_context={'expected': expected, 'boundary': ROLE, 'deadline': launch_deadline}),
        validate, limit=CORRECTIONS, execution_path=stage / 'architect-execution.json')
    model_s = time.monotonic() - started
    verify_role_call(stage, record, expected, ROLE, ROLE, limit=CORRECTIONS)
    receipt = {'schema': RECEIPT_SCHEMA, 'mode': 'architect', 'definition': definition,
               'expectation_id': expected['identity'], 'sources': bound,
               'execution': record['execution'], 'error': record.get('error'),
               'attempts': len(record['attempts']), 'corrections_used': record['resubmissions_used'],
               'max_corrections': CORRECTIONS, 'model_stage_s': model_s, 'reserve_s': reserve_s,
               'model_usage': record.get('model_usage'),
               'proposals': [identity(p) for p in proposals], 'fallback': None}
    if record['execution'] != 'ok' or response is None:
        # Execution, auth and quota failures stop here; a refused correction is final. Nothing publishes.
        receipt['status'] = 'refused' if record['execution'] == 'malformed' else 'stopped'
    elif state.get('stop'):
        receipt.update(status='stopped', error=state['stop'])
    else:
        compiled, intent = state['compiled'], state['intent']
        receipt.update(intent_sha256=identity(intent), package_id=compiled['receipt.json']['package_id'],
                       artifacts=compiled['receipt.json']['artifacts'], bindings=state['bindings'],
                       semantic_completion=compiled['receipt.json']['semantic_completion'],
                       required_unsupported=compiled['receipt.json']['required_unsupported'])
        if reference is not None:
            receipt['comparison'] = compare(intent, reference)
        if receipt.get('comparison', {}).get('unresolved'):
            receipt['status'] = 'unresolved'
            write_json(stage / 'proposed-intent.json', intent)
        else:
            receipt.update(_publish(compiled, out, run, intent, stopped))
            if receipt['status'] == 'accepted':
                write_json(stage / 'proposed-intent.json', intent)
    receipt['receipt_id'] = identity(receipt)
    write_json(stage / 'architect-receipt.json', receipt)
    return receipt


def _publish(compiled, out, run, intent, stopped):
    """Stage and verify the package beside ``out``; rename it into place only if the stage is not stopped."""
    if (why := stopped()):
        return {'status': 'stopped', 'error': f'{why} before publication'}
    staging = out.parent / f'.{out.name}.{uuid.uuid4().hex}'
    try:
        publish(compiled, staging)
        verify_receipt(staging, run, intent)
        if (why := stopped()):
            return {'status': 'stopped', 'error': f'{why} before publication'}
        rename_noreplace(staging, out)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return {'status': 'accepted', 'package': out.name}


def verify_stage(stage, run, out=None):
    """Recheck a stage receipt: its launch records, and for an accepted stage the published package."""
    from .review_run import verify_role_call
    stage = Path(stage)
    receipt = read_json(stage / 'architect-receipt.json')
    body = {k: v for k, v in receipt.items() if k != 'receipt_id'}
    if receipt.get('schema') != RECEIPT_SCHEMA or identity(body) != receipt.get('receipt_id'):
        raise IntentRefused('architect stage receipt changed')
    expected = read_json(stage / 'launch-expectation.json')
    record = read_json(stage / 'architect-execution.json')
    verify_role_call(stage, record, expected, ROLE, ROLE, limit=CORRECTIONS)
    if expected['identity'] != receipt['expectation_id'] or record['execution'] != receipt['execution']:
        raise IntentRefused('architect stage records differ from its receipt')
    if receipt['status'] == 'accepted':
        intent = read_json(stage / 'proposed-intent.json')
        if identity(intent) != receipt['intent_sha256']:
            raise IntentRefused('architect proposed intent changed')
        package = verify_receipt(out, run, intent)
        if package['package_id'] != receipt['package_id']:
            raise IntentRefused('published package differs from the architect receipt')
    return receipt
