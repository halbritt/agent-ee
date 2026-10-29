"""Fresh authored engineering testimony and an independent subscription witness."""
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import time

from . import qualification, retained_run
from .acquisition_completion import checkpoint
from .baseline import compile_baseline
from .catalog import exact_fields
from .profiles import validate_definition
from .requirement_map import input_inventory
from .storage import digest, identity, read_json, write_json

SCHEMA = 'agent-ee.judgment-seat-production/1'
TEXT = {'type': 'string', 'minLength': 1}
TEXTS = {'type': 'array', 'items': TEXT}

def fixed(value):
    kind = 'string' if isinstance(value, str) else 'integer' if type(value) is int else 'number'
    return {'type': kind, 'enum': [value]}


def obj(properties):
    return dict(type='object', properties=properties, required=list(properties), additionalProperties=False)


def schemas(metadata, binding, facts):
    citation = obj({k: TEXT for k in ('path', 'sha256', 'quote', 'locator')})
    author = obj({'schema': fixed('agent-ee.engineering-judgment/1'), 'requirement': TEXT,
        'span': obj({'byte_start': {'type': 'integer'}, 'byte_end': {'type': 'integer'}, 'text_sha256': TEXT}),
        'conclusion': TEXT, 'assumptions': TEXTS,
        'sources': {'type': 'array', 'minItems': 1, 'items': obj({'kind': {'type': 'string', 'enum': ['datasheet', 'manufacturer', 'owner', 'engineering']}, 'citation': citation})},
        'applicability': obj({k: fixed(v) for k, v in facts.items() if v is not None}),
        'inputs': {'type': 'array', 'items': obj({'path': TEXT, 'sha256': TEXT})},
        'candidate': obj({k: fixed(v) for k, v in binding.items()}),
        'author': obj({k: fixed(v) for k, v in metadata['author'].items()}), 'unresolved_defects': TEXTS})
    witness = obj({'schema': fixed('agent-ee.judgment-verification/1'), 'judgment_sha256': TEXT,
        'verifier': obj({k: fixed(v) for k, v in metadata['verifier'].items()}),
        'checked': {'type': 'array', 'items': {'type': 'string', 'enum': list(qualification.WITNESS_CHECKS)},
                    'minItems': len(qualification.WITNESS_CHECKS), 'maxItems': len(qualification.WITNESS_CHECKS)},
        'result': {'type': 'string', 'enum': ['concur', 'dissent']}, 'defects': TEXTS})
    return obj({'judgments': {'type': 'array', 'items': author}}), obj({'verifications': {'type': 'array', 'items': witness}})


def seat_policy(author, verifier, private_evaluator_pair):
    seats = [copy.deepcopy(validate_definition(d)) for d in (author, verifier)]
    pairs = [(d['harness'], d['model']) for d in seats]
    if (not isinstance(private_evaluator_pair, (list, tuple)) or len(private_evaluator_pair) != 2
            or not all(isinstance(v, str) and v for v in private_evaluator_pair)
            or pairs[0] == pairs[1] or tuple(private_evaluator_pair) in pairs):
        raise ValueError('judgment seats must differ and exclude the private evaluator pair')
    # Tools read the actual public source; no caller-selected executable or module.
    for seat in seats:
        if set(seat) - {'harness', 'model', 'effort', 'timeout_s', 'role', 'tool_mode'}:
            raise ValueError('unsupported judgment seat option')
        seat['tool_mode'] = seat.get('tool_mode', 'investigate')
        if seat['tool_mode'] != 'investigate':
            raise ValueError('judgment seats require source-inspection tools')
    return seats


def brief(build_run, out, deadline, cancellation):
    checkpoint(deadline, cancellation)
    root, out = Path(build_run).resolve(), Path(out).absolute()
    selected = retained_run.review_source(root)
    if selected.selection['selected']['stage'] != 'artwork':
        raise ValueError('judgment producer requires actual completed artwork')
    report = retained_run.snapshot(root)
    plan = retained_run.read_plan(root, report)
    if plan is None or plan['schema'] != qualification.TYPED_SCHEMA or 'judgments' not in plan:
        raise ValueError('judgment producer requires a typed judgment plan')
    candidate = selected.candidate
    files = input_inventory(candidate)
    prefix = plan['judgments'] + '/'
    if any(name.startswith(prefix) or '/' + prefix in name for name in files):
        raise ValueError('old judgment claims must not enter the public candidate')
    entries, names = [], set()
    for row in retained_run.requirements(root, report, True)['requirements']:
        if row['mode'] != 'judgment':
            continue
        locator = row['locator']
        span = dict(byte_start=locator['byte_start'], byte_end=locator['byte_end'], text_sha256=row['obligation_text_sha256'])
        name = str(retained_run.retained_path(Path('/judgments'), row['mapping']['record']).relative_to('/judgments'))
        if name.startswith('verification/') or name in names:
            raise ValueError('duplicate/reserved judgment output path')
        names.add(name)
        # Compiler receipt hashes refer to the exact captured specification slice.
        source = candidate / locator.get('path', plan['requirements']['spec'])
        if not source.is_file() or not source.resolve().is_relative_to(candidate):
            raise ValueError('missing/escaping exact judgment requirement source')
        raw = source.read_bytes()[span['byte_start']:span['byte_end']]
        import hashlib
        if hashlib.sha256(raw).hexdigest() != span['text_sha256']:
            raise ValueError('judgment requirement source span changed')
        entries.append(dict(id=row['id'], requirement=row.get('parent_id', row['id']), span=span,
                            record=name, source=source.relative_to(candidate).as_posix(), text=raw.decode('utf-8')))
    if not entries:
        raise ValueError('no captured engineering judgment spans')
    out.mkdir(parents=True, exist_ok=False)
    capsule = out / 'calls'; capsule.mkdir()
    shutil.copytree(candidate, capsule / 'inputs')
    if input_inventory(capsule / 'inputs') != files:
        raise ValueError('artwork changed while capturing public seat inputs')
    # Public, receipted native/calculation artifacts accompany the exact candidate.
    # Never expose the case root, private oracle, prior seats, or future package review.
    from .stage_receipts import tree_identity
    evidence = {}
    for row in report['completed']:
        checkpoint(deadline, cancellation)
        relative = 'work/build-stages/' + row['stage']
        shutil.copytree(retained_run.retained_path(root, row['directory']), capsule / relative, symlinks=True)
        for path in (capsule / relative).rglob('*'):
            if path.is_symlink() and not path.resolve().is_relative_to(capsule / relative):
                raise ValueError('public native evidence link escapes captured stage')
        if tree_identity(capsule / relative) != row['tree']:
            raise ValueError('public native stage evidence changed during capture')
        evidence[row['stage']] = dict(path=relative, tree=row['tree'])
    scope = selected.scope
    manifest = dict(schema='agent-ee.candidate/1', scope=scope, files=files,
                    candidate_id=identity(dict(scope=scope, files=files)))
    write_json(capsule / 'candidate.json', manifest)
    baseline = compile_baseline(scope, files, capsule / 'inputs')
    if baseline is not None:
        write_json(capsule / 'baseline.json', baseline)
    binding = dict(board_sha256=files[scope['board']], scope_sha256=selected.selection['scope_sha256'])
    facts = qualification.applicability_facts(candidate, plan)
    data = dict(selection=selected.selection, binding=binding, applicability=facts, files=files, requirements=entries, evidence=evidence,
                sources='Read actual readonly /candidate files, including complete manufacturer text and provenance. No prior judgments or private evaluator is supplied.')
    write_json(capsule / 'packet.json', data)
    checkpoint(deadline, cancellation)
    return capsule, manifest, data


def source_text(capsule, files, deadline=None, cancellation=None):
    result = {}
    for path in files:
        if deadline is not None: checkpoint(deadline, cancellation)
        try:
            result[path] = (capsule / 'inputs' / path).read_text(encoding='utf-8')
        except UnicodeError:
            pass
    return result


def validate_authors(value, data, texts, metadata):
    exact_fields(value, ('judgments',))
    rows = value['judgments']
    if not isinstance(rows, list) or len(rows) != len(data['requirements']):
        raise ValueError('judgment batch omitted/added required spans')
    expected = {(r['requirement'], identity(r['span'])): r for r in data['requirements']}
    found = {}
    for document in rows:
        if not isinstance(document, dict):
            raise ValueError('judgment must be an object')
        key = (document.get('requirement'), identity(document.get('span')))
        if key not in expected or key in found or document.get('author') != metadata:
            raise ValueError('duplicate/stale judgment span or foreign author')
        if document.get('candidate') != data['binding']:
            raise ValueError('judgment is not bound to actual artwork')
        # Contract validation must not mistake honest negative testimony for malformed output.
        neutral = copy.deepcopy(document); neutral['unresolved_defects'] = []
        defects = document.get('unresolved_defects')
        if not isinstance(defects, list) or any(not isinstance(v, str) or not v.strip() for v in defects):
            raise ValueError('invalid unresolved defects')
        result = qualification.admit_judgment(neutral, requirement=key[0], span=expected[key]['span'],
            files=data['files'], source_text=texts, applicability=data['applicability'], binding=data['binding'], judgment_sha256='0'*64)
        if result.get('record_status') != 'recorded':
            raise ValueError(result.get('reason', 'invalid judgment'))
        found[key] = document
    return [found[(r['requirement'], identity(r['span']))] for r in data['requirements']]


def validate_witnesses(value, hashes, metadata):
    exact_fields(value, ('verifications',))
    rows = value['verifications']
    if not isinstance(rows, list) or len(rows) != len(hashes):
        raise ValueError('witness batch omitted/added authored records')
    found = {}
    for witness in rows:
        exact_fields(witness, ('schema', 'judgment_sha256', 'verifier', 'checked', 'result', 'defects'))
        pin = witness['judgment_sha256']
        if (witness['schema'] != 'agent-ee.judgment-verification/1' or pin not in hashes or pin in found
                or witness['verifier'] != metadata or witness['result'] not in ('concur', 'dissent')
                or not isinstance(witness['checked'], list) or len(witness['checked']) != len(qualification.WITNESS_CHECKS)
                or any(not isinstance(v, str) for v in witness['checked'])
                or set(witness['checked']) != set(qualification.WITNESS_CHECKS)
                or not isinstance(witness['defects'], list)
                or any(not isinstance(v, str) or not v.strip() for v in witness['defects'])):
            raise ValueError('invalid/foreign/duplicate judgment witness')
        qualification._author(witness['verifier'])
        found[pin] = witness
    return found


def produce(build_run, out, *, author, verifier, private_evaluator_pair, deadline, cancellation,
            launch_environment, host=None):
    from .preflight import capture_expectation
    from .review_run import structured_call, verify_role_call
    from .reviewers import reviewer_call
    definitions = seat_policy(author, verifier, private_evaluator_pair)
    capsule, manifest, data = brief(build_run, out, deadline, cancellation)
    out = Path(out).absolute()
    texts = source_text(capsule, data['files'], deadline, cancellation)
    expected = capture_expectation(launch_environment, manifest, definitions, inputs=capsule / 'inputs')
    write_json(capsule / 'launch-expectation.json', expected)
    stamp = datetime.now(timezone.utc).isoformat()
    metadata = {name: dict(agent=launch_environment['run_id'] + ':' + name, model=d['model'], date_utc=stamp)
                for name, d in zip(('author', 'verifier'), definitions)}
    author_schema, witness_schema = schemas(metadata, data['binding'], data['applicability'])
    receipt = dict(schema=SCHEMA, execution='not_run', outcome='incomplete', brief_sha256=digest(capsule/'packet.json'),
        expectation=expected, definitions=definitions, private_evaluator_pair=list(private_evaluator_pair),
        metadata=metadata, calls=[], judgment_inventory={}, binding=data['binding'], fallback=None)
    directory = out / 'judgments'
    def invariant():
        checkpoint(deadline, cancellation)
        selected = data['selection']['selected']
        from .stage_receipts import tree_identity
        if (tree_identity(Path(build_run)/selected['directory']) != selected['tree']
                or digest(retained_run.scope(Path(build_run)).path) != data['binding']['scope_sha256']
                or input_inventory(Path(build_run) / selected['candidate']) != data['files']
                or input_inventory(capsule/'inputs') != data['files'] or digest(capsule/'packet.json') != receipt['brief_sha256']
                or any(tree_identity(capsule/v['path']) != v['tree'] for v in data['evidence'].values())):
            raise ValueError('judgment artwork/brief changed during call')
    def call(role, definition, prompt, schema, validate):
        invariant()
        context = dict(expected=expected, boundary='initial', deadline=deadline, **({'host': host} if host is not None else {}))
        record, value = structured_call(capsule, role, prompt, schema,
            lambda supplied: reviewer_call(capsule, role, definition, supplied, schema,
                min(definition['timeout_s'], max(0, deadline-time.monotonic())),
                cancellation=cancellation, launch_context=context), validate, limit=0, invariant=invariant)
        verify_role_call(capsule, record, expected, role, 'initial', limit=0)
        receipt['calls'].append(dict(role=role, path=f'calls/review-executions/{role}.json',
                                    sha256=digest(capsule/'review-executions'/f'{role}.json')))
        if record['execution'] == 'ok':
            check_call_identity(capsule, record, definition)
        if record['execution'] != 'ok' or value is None:
            receipt['execution'] = record['execution']
            return None
        return value
    instructions = ('You are an engineering judgment seat. Inspect the actual readonly candidate and complete captured sources. '
        'Do not synthesize testimony from check pass/fail, reuse prior judgments, modify inputs, read credentials, or invent acceptance. '
        'Return evidence-supported analysis and honest unresolved defects. Sources use candidate-relative path/SHA256/exact quote/locator. '
        'The supplied packet is at /evidence/packet.json; /candidate contains the exact native artwork and source records. '
        'Actual completed stage receipts, native stdout/stderr and calculation outputs are readonly at /evidence/work/build-stages. '
        'Use them to challenge conclusions; pass/fail statuses alone are not engineering testimony.\n')
    try:
        rows = call('judgment-author', definitions[0], instructions + 'Author EVERY required span.\n' + json.dumps(dict(brief=data, author=metadata['author'])),
                    author_schema, lambda v: validate_authors(v, data, texts, metadata['author']))
        if rows is not None:
            staging = capsule/'work'/'authored-records'; staging.mkdir(parents=True)
            hashes = {}
            for entry, document in zip(data['requirements'], rows):
                path = staging / entry['record']; path.parent.mkdir(parents=True, exist_ok=True); write_json(path, document)
                hashes[digest(path)] = document
            pin_inventory = input_inventory(staging)
            prompt = instructions + ('Independently verify EVERY authored record: resolve sources, recompute applicability, challenge conclusions against bound evidence. '
                'Concur or dissent honestly. Author bytes are readonly at /evidence/work/authored-records.\n') + json.dumps(dict(brief=data, records=hashes, verifier=metadata['verifier']))
            witnesses = call('judgment-verifier', definitions[1], prompt, witness_schema,
                             lambda v: validate_witnesses(v, hashes, metadata['verifier']))
            if witnesses is not None:
                invariant()
                if input_inventory(staging) != pin_inventory:
                    raise ValueError('authored bytes changed before witness publication')
                directory.mkdir(); (directory/'verification').mkdir()
                states = []
                for entry, document in zip(data['requirements'], rows):
                    pin = digest(staging/entry['record']); witness = witnesses[pin]
                    target = directory/entry['record']; target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(staging/entry['record'], target)
                    write_json(directory/'verification'/f'{identity(entry["span"])}.json', witness)
                    states.append(qualification.admit_judgment(document, requirement=entry['requirement'], span=entry['span'],
                        files=data['files'], source_text=texts, applicability=data['applicability'], binding=data['binding'], judgment_sha256=pin, witness=witness))
                invariant()
                receipt.update(execution='ok', outcome='criteria_satisfied' if all(s==dict(status='recorded', witness='concur') for s in states) else 'criteria_failed',
                               judgment_inventory=input_inventory(directory), admissions=states)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        receipt.update(execution='failed', error=f'{type(exc).__name__}: {exc}')
    finally:
        write_json(out/'producer-receipt.json', receipt)
    checkpoint(deadline, cancellation)
    return dict(execution=receipt['execution'], outcome=receipt['outcome'], judgment_directory=str(directory),
                judgment_inventory=receipt['judgment_inventory'], producer_receipt=str(out/'producer-receipt.json'),
                producer_receipt_sha256=digest(out/'producer-receipt.json'))


def check_call_identity(capsule, record, definition):
    preflight = read_json(capsule / record['preflight']['path'])
    if (preflight['definition'] != definition or record.get('backend') != definition['harness']
            or record.get('model_requested') != definition['model']
            or record.get('billing_route') != 'subscription'):
        raise ValueError('actual judgment call differs from selected subscription seat')


def verify(out, result):
    """Replay fresh call/output custody without another provider/native execution."""
    from .review_run import verify_role_call
    out = Path(out).absolute(); capsule = out/'calls'
    path = out/'producer-receipt.json'
    if path.is_symlink() or digest(path) != result['producer_receipt_sha256']:
        raise ValueError('judgment producer receipt changed')
    receipt = read_json(path); data = read_json(capsule/'packet.json')
    if (receipt['schema'] != SCHEMA or receipt['brief_sha256'] != digest(capsule/'packet.json')
            or receipt['binding'] != data['binding'] or receipt['execution'] != result['execution']
            or receipt['outcome'] != result['outcome'] or receipt['judgment_inventory'] != result['judgment_inventory']
            or input_inventory(capsule/'inputs') != data['files']):
        raise ValueError('judgment producer public binding changed')
    from .stage_receipts import tree_identity
    if any(tree_identity(capsule/v['path']) != v['tree'] for v in data['evidence'].values()):
        raise ValueError('judgment native evidence changed')
    seat_policy(*receipt['definitions'], receipt['private_evaluator_pair'])
    if len(receipt['calls']) != 2:
        raise ValueError('judgment producer requires both actual seat calls')
    invocations = set()
    values = []
    for index, row in enumerate(receipt['calls']):
        role = ('judgment-author', 'judgment-verifier')[index]
        rel = f'calls/review-executions/{role}.json'
        if row['role'] != role or row['path'] != rel or digest(out/rel) != row['sha256']:
            raise ValueError('judgment call execution changed')
        record = read_json(out/rel)
        found = verify_role_call(capsule, record, receipt['expectation'], role, 'initial', limit=0)
        if invocations & found: raise ValueError('judgment seats reused a launch')
        invocations |= found
        if record['execution'] != 'ok': raise ValueError('judgment call did not execute successfully')
        check_call_identity(capsule, record, receipt['definitions'][index])
        name = ('author', 'verifier')[index]
        if receipt['metadata'][name]['model'] != receipt['definitions'][index]['model']:
            raise ValueError('judgment testimony model differs from actual seat')
        values.append(read_json(capsule/'review-attempts'/role/'0/response.json'))
    texts = source_text(capsule, data['files'])
    authors = validate_authors(values[0], data, texts, receipt['metadata']['author'])
    expected_files = {}
    hashes = {}
    for entry, author in zip(data['requirements'], authors):
        path = out/'judgments'/entry['record']
        if read_json(path) != author: raise ValueError('published judgment differs from actual author output')
        hashes[digest(path)] = author
        expected_files[entry['record']] = digest(path)
    witnesses = validate_witnesses(values[1], hashes, receipt['metadata']['verifier'])
    states = []
    for entry, author in zip(data['requirements'], authors):
        pin = expected_files[entry['record']]; witness = witnesses[pin]
        rel = 'verification/' + identity(entry['span']) + '.json'
        path = out/'judgments'/rel
        if read_json(path) != witness: raise ValueError('published witness differs from actual verifier output')
        expected_files[rel] = digest(path)
        states.append(qualification.admit_judgment(author, requirement=entry['requirement'], span=entry['span'],
            files=data['files'], source_text=texts, applicability=data['applicability'], binding=data['binding'],
            judgment_sha256=pin, witness=witness))
    outcome = 'criteria_satisfied' if all(s == dict(status='recorded', witness='concur') for s in states) else 'criteria_failed'
    if (receipt['admissions'] != states or receipt['outcome'] != outcome
            or input_inventory(out/'judgments') != expected_files or expected_files != receipt['judgment_inventory']):
        raise ValueError('judgment producer outcome/inventory changed')
    return receipt
