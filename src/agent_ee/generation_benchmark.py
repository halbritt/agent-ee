"""Frozen, offline generation trials over the ordinary candidate/check pipeline."""
import argparse
import hashlib
import shutil
import stat
import subprocess
import sys
import time
from fractions import Fraction
from pathlib import Path

from . import native_run
from .baseline import compile_baseline, freeze_evidence, verify_evidence
from .candidate import capture, safe_relative, verify_snapshot
from .catalog import exact_fields, nonempty, strict_json
from .design_diff import read_side, summarize
from .executor import RECORDS, SANDBOX_PATH
from .findings import safe_id
from .storage import digest, fingerprint, identity, json_bytes, write_json
from .run_record import pcbnew_extension, policy_files


class NonOrdinaryTree(ValueError):
    """An input tree contains a link, special file, or non-directory root."""


def tree_files(root):
    """Refuse links and special files before reading a task or writer output."""
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise NonOrdinaryTree('expected ordinary directory')
    files = {}
    for path in sorted(root.rglob('*')):
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode) or path.stat().st_nlink != 1:
            raise NonOrdinaryTree('non-ordinary file: ' + str(path))
        files[path.relative_to(root).as_posix()] = digest(path, follow_symlinks=False)
    return files


def output_files(root):
    try:
        return (tree_files(root) if root.exists() or root.is_symlink() else {}), None
    except NonOrdinaryTree:
        # Never follow unsafe writer output, including while preparing repair feedback.
        return {}, 'non_ordinary_output'


def pinned_task(root, *, expect_pins=None):
    root = Path(root)
    files = tree_files(root)
    if expect_pins is not None and files.get('pins.json') != expect_pins:
        raise ValueError('external pins digest mismatch')
    pin = strict_json((root / 'pins.json').read_bytes())
    exact_fields(pin, ('schema', 'files'))
    if pin['schema'] != 'agent-ee.generation-pins/1' or pin['files'] != {
            k: v for k, v in files.items() if k != 'pins.json'}:
        raise ValueError('task-file hash mismatch')
    task = strict_json((root / 'task.json').read_bytes())
    refusal = isinstance(task, dict) and 'expected_refusal' in task
    exact_fields(task, ('schema', 'id', 'domain', 'difficulty', 'prompt', 'reference',
                        'deliverables', *(['expected_refusal'] if refusal else ['rules', 'scope']),
                        *(['public_context'] if 'public_context' in task else [])))
    if task['schema'] != 'agent-ee.generation-task/1':
        raise ValueError('unknown generation task schema')
    safe_id(task['id'], 'task id')
    nonempty(task['domain'])
    nonempty(task['difficulty'])
    for key in ('prompt', 'reference'):
        if str(safe_relative(task[key])) != task[key] or task[key] not in files:
            raise ValueError('missing or non-canonical task ' + key)
    # Only the prompt reaches the generator: never evaluator inputs, the reference or task metadata.
    if task['prompt'] != 'prompt.md' or task['prompt'] == task['reference']:
        raise ValueError('public prompt must be prompt.md, separate from evaluator inputs')
    if any(files[task['prompt']] == sha for path, sha in files.items()
           if path.startswith('evaluator/') or path == task['reference']):
        raise ValueError('prompt duplicates private input')
    deliverables = task['deliverables']
    if not isinstance(deliverables, list) or not deliverables or len(set(deliverables)) != len(deliverables):
        raise ValueError('expected unique deliverables')
    for path in deliverables:
        safe_relative(path)
        if path in ('candidate.json', 'baseline.json', 'scope.json', '.generation') or path.startswith(('checks/', 'work/', '.generation/')):
            raise ValueError('reserved deliverable path')
    if 'public_context' in task:
        rel = task['public_context']
        if (not isinstance(rel, str) or str(safe_relative(rel)) != rel or rel not in files
                or rel.startswith('evaluator/') or rel in {task['prompt'], task['reference'], 'generator.py'}
                or rel in deliverables or rel in ('task.json', 'pins.json') or rel.startswith(('prompt.md/', 'generator.py/'))
                or (root / rel).stat().st_size > 65536):
            raise ValueError('invalid public case-context path')
        case_context(root / rel)
    if refusal:
        task['_refusal_contract'] = _refusal_contract(root, task, files)
        task['rules'] = [{'id': 'refusal', 'check': 'refusal', 'category': 'source_refusal',
                          'critical': True, 'weight': 1, 'source_criteria': ['unsupported_clause'],
                          'deliverables': ['refusal.json']}]
    else:
        scope = task['scope']
        if scope['schema'] not in ('agent-ee.scope/2', 'agent-ee.scope/3') or scope['inputs']:
            raise ValueError('benchmark scope must use scope/2 or scope/3 and empty inputs')
        # All immutable inputs live under evaluator/. The writer supplies only declared files.
        evaluator = {k.removeprefix('evaluator/'): v for k, v in files.items() if k.startswith('evaluator/')}
        if any(p == '.generation' or p.startswith('.generation/') for p in evaluator):
            raise ValueError('reserved generation evidence path')
        if set(evaluator) & set(deliverables):
            raise ValueError('deliverable overlaps evaluator input')
        for key in ('schematic', 'board'):
            if key in scope and scope[key] not in deliverables:
                raise ValueError('native subject must be a declared deliverable')
        compile_baseline(scope, {**evaluator, **dict.fromkeys(deliverables, '0' * 64)}, root / 'evaluator')
        checks = {c['id'] for c in scope['checks']}
        rules = task['rules']
        if not isinstance(rules, list) or not rules:
            raise ValueError('expected scoring rules')
        ids, traced = set(), set()
        requirements = {r['id']: set(r['checks']) for r in scope['baseline']['requirements']}
        for rule in rules:
            exact_fields(rule, ('id', 'check', 'category', 'critical', 'weight', 'source_criteria', 'deliverables'))
            safe_id(rule['id'], 'rule id')
            if rule['id'] in ids or rule['check'] not in checks:
                raise ValueError('duplicate rule or undeclared check')
            ids.add(rule['id'])
            nonempty(rule['category'])
            if type(rule['critical']) is not bool or type(rule['weight']) is not int or rule['weight'] < 1:
                raise ValueError('expected boolean critical and positive integer weight')
            if (not isinstance(rule['source_criteria'], list) or not rule['source_criteria'] or
                    len(set(rule['source_criteria'])) != len(rule['source_criteria'])):
                raise ValueError('expected unique source criteria')
            for rid in rule['source_criteria']:
                if rid not in requirements or rule['check'] not in requirements[rid]:
                    raise ValueError('scoring rule does not trace to baseline criterion')
                traced.add(rid)
            if (not isinstance(rule['deliverables'], list) or not rule['deliverables'] or
                    not set(rule['deliverables']) <= set(deliverables)):
                raise ValueError('rule must name required deliverables')
        if traced != set(requirements):
            raise ValueError('uncovered acceptance criterion or check')
        if set().union(*(set(r['deliverables']) for r in rules)) != set(deliverables):
            raise ValueError('unscored deliverable')
    task['files'] = files
    # Existing placement/UUID-insensitive structural projection, deliberately conservative.
    kind = {'.kicad_sch': 'schematic', '.kicad_pcb': 'board'}.get(Path(task['reference']).suffix)
    if kind is None:
        raise ValueError('reference must be a native schematic or board')
    node, _ = read_side((root / task['reference']).read_bytes(), kind, None)
    task['fingerprint'] = summarize(node, kind)['hashes']['structural']
    return task


def _refusal_contract(root, task, files):
    """Validate an independently authored oracle, without asking the producer."""
    if task['deliverables'] != ['refusal.json']:
        raise ValueError('expected-refusal task requires only refusal.json')
    exact_fields(task['expected_refusal'], ('contract',))
    path = task['expected_refusal']['contract']
    if (str(safe_relative(path)) != path or not path.startswith('evaluator/')
            or path not in files):
        raise ValueError('expected private pinned refusal contract')
    contract = strict_json((root / path).read_bytes())
    exact_fields(contract, ('outcome', 'code', 'brief_sha256', 'byte_start', 'byte_end',
                            'clause_sha256', 'context_files'))
    raw = (root / task['prompt']).read_bytes()
    raw.decode('utf-8')
    start, end = contract['byte_start'], contract['byte_end']
    if (contract['outcome'] != 'unresolved' or contract['code'] != 'unsupported_clause'
            or contract['brief_sha256'] != files[task['prompt']]
            or type(start) is not int or type(end) is not int or not 0 <= start < end <= len(raw)):
        raise ValueError('invalid expected unsupported-clause contract')
    # Validate the frozen locator only; this never classifies a clause as unsupported.
    offset, spans = 0, []
    for line in raw.decode('utf-8').splitlines(keepends=True):
        content = line.strip()
        begin = offset + len(line[:len(line) - len(line.lstrip())].encode('utf-8'))
        if content:
            spans.append((begin, begin + len(content.encode('utf-8'))))
        offset += len(line.encode('utf-8'))
    if ((start, end) not in spans or hashlib.sha256(raw[start:end]).hexdigest() != contract['clause_sha256']):
        raise ValueError('refusal clause span/hash must name a complete UTF-8 clause')
    context = contract['context_files']
    if not isinstance(context, dict) or not context:
        raise ValueError('expected nonempty frozen refusal capability context')
    for name, sha in context.items():
        if (str(safe_relative(name)) != name or not name.startswith('evaluator/')
                or name == path or name not in files or files[name] != sha
                or (root / name).stat().st_size == 0):
            raise ValueError('refusal context must match private task pins')
    return contract


def _output_entries(root):
    """Complete ordinary entry census, including empty directories."""
    root = Path(root)
    if not root.exists() and not root.is_symlink():
        return {}
    tree_files(root)
    entries = {}
    for path in sorted(root.rglob('*')):
        mode = path.lstat().st_mode
        if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            raise NonOrdinaryTree('non-ordinary output entry')
        entries[path.relative_to(root).as_posix()] = 'directory' if stat.S_ISDIR(mode) else 'file'
    return entries


def _refusal_check(task, snapshot, output_tree):
    valid = False
    if (output_tree['entries'] == {'refusal.json': 'file'}
            and set(output_tree['files']) == {'refusal.json'}):
        contract = task['_refusal_contract']
        raw = (snapshot.parent / 'task' / task['prompt']).read_bytes()
        expected = {'outcome': contract['outcome'], 'brief_sha256': contract['brief_sha256'],
                    'findings': [{'code': contract['code'],
                        'message': 'unsupported or ambiguous brief clause: ' +
                            raw[contract['byte_start']:contract['byte_end']].decode('utf-8'),
                        'byte_start': contract['byte_start'], 'byte_end': contract['byte_end']}]}
        try:
            actual = strict_json((snapshot / 'refusal.json').read_bytes())
        except ValueError:
            actual = None
        # Compare canonical JSON too: booleans/floats must not alias integer offsets.
        valid = json_bytes(actual) == json_bytes(expected)
    return {'id': 'refusal', 'kind': 'source_refusal', 'execution': 'ok',
            'outcome': 'pass' if valid else 'defect'}


def _refusal_identity():
    path = str(Path(sys.executable).resolve())
    return {'code': policy_files(), 'runtime': {'python': {'path': path, 'identity': fingerprint(path)}},
            'python': sys.version}


def case_context(path):
    """Public wire schema only; producer admits pairs against the frozen catalog."""
    data = strict_json(Path(path).read_bytes())
    exact_fields(data, ('schema', 'catalog_policy'))
    if data['schema'] != 'agent-ee.case-context/1':
        raise ValueError('unknown public case context')
    policy = data['catalog_policy']
    if policy is not None:
        exact_fields(policy, ('kind', 'label', 'exclude'))
        if (policy['kind'] != 'evaluation_exclusion' or policy['label'] !=
                'evaluation scenario; not an observed supplier stockout' or
                not isinstance(policy['exclude'], list) or not policy['exclude']):
            raise ValueError('invalid evaluation exclusion')
        pairs = set()
        for entry in policy['exclude']:
            exact_fields(entry, ('lcsc', 'mpn'))
            pair = (nonempty(entry['lcsc']), nonempty(entry['mpn']))
            if pair in pairs:
                raise ValueError('duplicate evaluation exclusion')
            pairs.add(pair)
    return data


def ratio(value):
    return {'numerator': value.numerator, 'denominator': value.denominator}


def score(rules, checks, missing=()):
    by_check = {c['id']: c for c in checks}
    records = []
    for rule in rules:
        check = by_check.get(rule['check'])
        absent = sorted(set(rule['deliverables']) & set(missing))
        status = 'unknown'
        if absent:
            status = 'fail'
        elif check and check['execution'] == 'ok':
            status = {'pass': 'pass', 'defect': 'fail'}.get(check['outcome'], 'unknown')
        records.append({**rule, 'status': status, 'missing_deliverables': absent})
    categories = {}
    for category in sorted({r['category'] for r in rules}):
        categories[category] = score_bounds([r for r in records if r['category'] == category])
    overall = score_bounds(records)
    critical_failed = any(r['critical'] and r['status'] == 'fail' for r in records)
    overall_pass = False if critical_failed else (None if any(r['status'] == 'unknown' for r in records)
                                                 else all(r['status'] == 'pass' for r in records))
    return {'rules': records, 'categories': categories, **overall, 'overall_pass': overall_pass}


def score_bounds(records):
    total = sum(r['weight'] for r in records)
    passed = sum(r['weight'] for r in records if r['status'] == 'pass')
    unknown = sum(r['weight'] for r in records if r['status'] == 'unknown')
    lower, upper = Fraction(passed, total), Fraction(passed + unknown, total)
    return {'score': None if unknown else ratio(lower), 'lower': ratio(lower), 'upper': ratio(upper)}


def evaluator_identity():
    native = {}
    for name in ('kicad-cli', 'ngspice', 'bwrap', '/usr/bin/python3'):
        path = shutil.which(name, path=SANDBOX_PATH)
        native[name] = {'path': path, 'identity': fingerprint(path) if path else None}
    extension, diagnostic = pcbnew_extension()
    native['pcbnew'] = {'path': extension, 'identity': fingerprint(extension) if extension else None,
                        'diagnostic': diagnostic}
    return {'code': policy_files(), 'runtime': native, 'python': sys.version}


def publish_report(store, report):
    key = identity(report['binding'])
    directory = Path(store) / key
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'report.json'
    data = json_bytes(report)
    try:
        with path.open('xb') as stream:
            stream.write(data)
    except FileExistsError:
        if path.is_symlink() or path.read_bytes() != data:
            raise ValueError('differing saved evaluation record; refusing overwrite')
    return path


def evaluate(task_root, outputs, trial, store, *, generation_execution='ok', timeout=120,
             expect_pins=None, execution_work=None, deadline=None, cancel=None, _case=None, completion_work=None):
    """Evaluate one retained output; generator result.json claims are never read."""
    from .checks import run_checks
    if type(timeout) is not int or timeout < 1:
        raise ValueError('timeout must be a positive integer')
    deadline = min(time.monotonic() + timeout, deadline) if deadline is not None else time.monotonic() + timeout
    with native_run.using(deadline=deadline, cancel=cancel):
        deadline = native_run._deadline.get()
        cancel = native_run._cancel.get()
        def checkpoint():
            if cancel is not None and cancel.cancelled.is_set():
                raise native_run.DeadlineExhausted('evaluation cancelled')
            if time.monotonic() >= deadline:
                raise native_run.DeadlineExhausted('evaluation deadline exhausted')
        def observe_identity():
            try:
                return _refusal_identity() if 'expected_refusal' in task else evaluator_identity()
            except subprocess.TimeoutExpired:
                checkpoint()
                raise
        checkpoint()
        if generation_execution not in ('ok', 'failed'):
            raise ValueError('generation execution must be ok or failed')
        task_root, outputs, trial = Path(task_root), Path(outputs), Path(trial)
        task = pinned_task(task_root, expect_pins=expect_pins)
        checkpoint()
        refusal = 'expected_refusal' in task
        completion = None
        if completion_work is not None:
            from . import acquisition_completion
            completion = acquisition_completion.verify(completion_work, deadline=deadline, cancel=cancel)
            if (completion['brief_sha256'] != task['files'][task['prompt']] or
                    (_case is not None and (completion['attempt'] != _case['attempt']
                     or completion['context_pins'] != _case.get('completion_context_pins')))):
                raise ValueError('completion differs from current public brief/attempt')
            if completion['execution'] == 'ok':
                outputs = Path(completion['outputs'])
            else:
                outputs = trial.parent / (trial.name + '-no-completion-output')
                outputs.mkdir(exist_ok=False)
                generation_execution = 'failed'
        if _case is not None and _case.get('completion_required') and completion is None:
            outputs = trial.parent / (trial.name + '-no-completion-output')
            outputs.mkdir(exist_ok=False)
        observed, output_error = output_files(outputs)
        output_tree = {'files': observed, 'entries': _output_entries(outputs)} if refusal and output_error is None else None
        if output_error:
            generation_execution = 'failed'
        missing = [] if output_error else sorted(set(task['deliverables']) - set(observed))
        selected = {k: observed[k] for k in task['deliverables'] if k in observed}
        environment = observe_identity()
        checkpoint()
        trial.mkdir(parents=True, exist_ok=False)
        generation_records = None
        if execution_work is not None:
            generation = trial / 'generation'
            generation.mkdir()
            for name in RECORDS:
                checkpoint()
                source = Path(execution_work) / name
                if source.is_symlink() or not source.is_file() or source.stat().st_nlink != 1:
                    raise ValueError('non-ordinary executor record')
                shutil.copyfile(source, generation / name)
            generation_records = tree_files(generation)
            generation_execution = executor_outcome(generation)
            if output_error or (completion is not None and completion['execution'] != 'ok') or (
                    _case is not None and _case.get('completion_required') and completion is None):
                generation_execution = 'failed'
        if completion is not None:
            shutil.copytree(completion_work, trial / 'completion')
            completion_files = tree_files(trial / 'completion')
            if tree_files(completion_work) != completion_files:
                raise ValueError('completion records changed during capture')
        checkpoint()
        shutil.copytree(task_root, trial / 'task')
        if pinned_task(trial / 'task')['files'] != task['files']:
            raise ValueError('task changed during capture')
        checks = []
        capture_error = None
        if refusal and output_error is None:
            snapshot = trial / 'outputs'
            snapshot.mkdir()
            for rel, kind in output_tree['entries'].items():
                checkpoint()
                dest = snapshot / rel
                if kind == 'directory':
                    dest.mkdir()
                else:
                    shutil.copyfile(outputs / rel, dest, follow_symlinks=False)
            if (tree_files(snapshot) != observed or _output_entries(snapshot) != output_tree['entries']):
                raise ValueError('complete refusal output changed during capture')
            checks = [_refusal_check(task, snapshot, output_tree)]
        if not refusal and not missing and output_error is None:
            inputs = trial / 'evaluation-inputs'
            shutil.copytree(task_root / 'evaluator', inputs)
            if generation_records is not None:
                shutil.copytree(trial / 'generation', inputs / '.generation')
            for rel in task['deliverables']:
                checkpoint()
                dest = inputs / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(outputs / rel, dest)
                if digest(dest) != selected[rel]:
                    raise ValueError('output changed during capture')
            scope = {**task['scope'], 'inputs': [
                {'path': 'evaluation-inputs/' + rel, 'at': rel} for rel in tree_files(inputs)]}
            write_json(trial / 'scope.json', scope)
            run = trial / 'run'
            run.mkdir()
            # A generated malformed native file may fail scope/3 capture. Keep that failure unknown.
            try:
                checkpoint()
                capture(trial / 'scope.json', run)
            except (ValueError, FileNotFoundError) as exc:
                capture_error = type(exc).__name__
            else:
                checkpoint()
                checks = run_checks(run, deadline, **({} if cancel is None else {"cancel": cancel}))
                freeze_evidence(run, checks)
                verify_evidence(run, checks, required=True)
        checkpoint()
        current, current_error = output_files(outputs)
        if (pinned_task(task_root)['files'] != task['files'] or
                (current, current_error) != (observed, output_error) or
                (refusal and output_error is None and _output_entries(outputs) != output_tree['entries'])):
            raise ValueError('task or output changed during evaluation')
        if observe_identity() != environment:
            raise ValueError('evaluator runtime changed during evaluation')
        report = {'schema': 'agent-ee.generation-evaluation/1', 'task': task['id'],
                  'domain': task['domain'], 'difficulty': task['difficulty'], 'fingerprint': task['fingerprint'],
                  'binding': {'task_files': task['files'], 'outputs': selected, 'evaluator': environment,
                              'generation_execution': generation_execution, 'timeout_s': timeout,
                              'output_error': output_error},
                  'generation': {'execution': generation_execution, 'missing_deliverables': missing,
                                 'output_error': output_error},
                  'evaluation': {'capture_error': capture_error,
                                 'checks': [{k: c[k] for k in ('id', 'kind', 'execution', 'outcome')} for c in checks]},
                  'scoring': score(task['rules'], checks, missing)}
        if refusal:
            report['kind'] = 'source_refusal'
            report['binding'].update(kind='source_refusal', output_tree=output_tree)
        if generation_records is not None:
            report['binding']['generation_records'] = generation_records
        if 'public_context' in task:
            report['binding']['public_context_sha256'] = task['files'][task['public_context']]
        if completion is not None:
            report['binding']['completion_files'] = completion_files
        if _case is not None:
            report['binding']['case'] = _case
        checkpoint()
        report['result'] = combined_result(generation_execution, report['scoring'])
        path = publish_report(store, report)
        receipt = {'schema': 'agent-ee.generation-evaluation-record/1',
                   'report': str(path.resolve()), 'report_sha256': digest(path)}
        write_json(trial / 'evaluation.json', receipt)
        # Keep mutable location metadata outside the deterministic report bytes.
        records = path.parent / 'trials'
        records.mkdir(exist_ok=True)
        write_json(records / (identity(str(trial.resolve())) + '.json'),
                   {'trial': str(trial.resolve()), **receipt})
        checkpoint()
        return path


def executor_outcome(root):
    """Derive generation status from retained executor bytes, never a report claim."""
    root = Path(root)
    record = strict_json((root / 'execution.json').read_bytes())
    for stream in ('stdout', 'stderr', 'console'):
        if record[stream + '_sha256'] != digest(root / (stream + '.log')):
            raise ValueError('executor log digest mismatch')
    if (root / 'console.log').read_bytes() != ((root / 'stdout.log').read_bytes() +
                                               (root / 'stderr.log').read_bytes()):
        raise ValueError('executor console differs from streams')
    status = record['execution']
    if status not in ('ok', 'failed', 'timeout', 'unavailable', 'not_run', 'interrupted'):
        raise ValueError('invalid executor execution')
    if status == 'ok' and (type(record['exit_code']) is not int or record['exit_code'] != 0):
        raise ValueError('executor success requires zero exit code')
    return 'ok' if status == 'ok' else 'failed'


def combined_result(generation_execution, scoring):
    return {'generation_execution': generation_execution,
            'overall_pass': False if generation_execution == 'failed' else scoring['overall_pass']}


def run_task(task_root, generator, scratch, store, *, attempts=3, timeout=120, expect_pins=None,
             case_timeout=None, grading_reserve=None, max_work=None, cancel=None,
             completion_context=None, expect_context=None, completion_policy=None, expect_completion_policy=None):
    """One finite case; a work credit admits one generation subtree or grading phase.

    The generation sandbox's PID namespace contains its child preparation/build/review.
    This counter is phase admissions, not a token or internal native-call meter.
    """
    if type(attempts) is not int or not 1 <= attempts <= 3:
        raise ValueError('attempts must be 1..3')
    if type(timeout) is not int or timeout < 1:
        raise ValueError('timeout must be a positive integer')
    case_timeout = 2 * attempts * timeout if case_timeout is None else case_timeout
    grading_reserve = timeout if grading_reserve is None else grading_reserve
    acquisition = completion_context is not None
    if acquisition != (expect_context is not None):
        raise ValueError('acquisition completion needs public context and its external pins')
    if (completion_policy is None) != (expect_completion_policy is None) or (completion_policy is not None and not acquisition):
        raise ValueError('completion policy needs acquisition context and external policy pin')
    if acquisition and attempts > 2:
        raise ValueError('acquisition case allows at most two complete attempts')
    phases = 3 if acquisition else 2
    max_work = phases * attempts if max_work is None else max_work
    if type(case_timeout) is not int or case_timeout < 2:
        raise ValueError('case_timeout must be an integer >= 2')
    if type(grading_reserve) is not int or not 1 <= grading_reserve < case_timeout:
        raise ValueError('grading_reserve must be an integer in [1, case_timeout)')
    if acquisition and type(max_work) is int and max_work > phases * attempts:
        raise ValueError('acquisition work credits cannot exceed three per complete attempt')
    if type(max_work) is not int or max_work < phases:
        raise ValueError('max_work must admit generation/completion/grading phases')
    started = time.monotonic()
    deadline, productive = started + case_timeout, started + case_timeout - grading_reserve
    cancel = cancel if cancel is not None else native_run.Cancellation()
    task_root, scratch = Path(task_root), Path(scratch)
    task = pinned_task(task_root, expect_pins=expect_pins)
    if acquisition:
        from . import acquisition_completion
        context_path = Path(completion_context).absolute()
        task_path = task_root.resolve()
        if context_path.resolve() == task_path or task_path in context_path.resolve().parents or context_path.resolve() in task_path.parents:
            raise ValueError('public completion context must be separate from the private task')
        acquisition_completion.context(context_path, expect_context)
    scratch.mkdir(parents=True, exist_ok=False)
    ledger = {'schema': 'agent-ee.generation-trial/1', 'generator_sha256': None, 'evaluations': [],
              'task_pins': task['files']['pins.json'], 'limits': {'attempts': attempts, 'timeout_s': timeout,
              'case_timeout_s': case_timeout, 'grading_reserve_s': grading_reserve, 'max_work': max_work},
              'started': started, 'deadline': deadline, 'productive_deadline': productive,
              'work_started': 0, 'attempts_started': 0, 'attempt_records': [], 'status': 'running'}
    if acquisition:
        ledger['completion_context_pins'] = expect_context
    public_pin = task['files'].get(task.get('public_context'))
    if public_pin is not None:
        ledger['public_context_sha256'] = public_pin
    if expect_completion_policy is not None:
        ledger['completion_policy_sha256'] = expect_completion_policy
    def save():
        ledger['elapsed_s'] = time.monotonic() - started
        write_json(scratch / 'trial.json', ledger)
    def admit(phase):
        if cancel.cancelled.is_set():
            raise native_run.DeadlineExhausted('case cancelled')
        limit = deadline if phase == 'grading' else productive
        if time.monotonic() >= limit or ledger['work_started'] >= max_work - (phase != 'grading'):
            raise native_run.DeadlineExhausted('case ' + phase + ' time/work exhausted')
        ledger['work_started'] += 1
        save()  # No refund after a failed launch, timeout, or missing output.
    reports, previous = [], None
    try:
        if cancel.cancelled.is_set() or time.monotonic() >= productive:
            raise native_run.DeadlineExhausted('case stopped before public preparation')
        if acquisition:
            shutil.copytree(context_path, scratch / 'completion-context')
            acquisition_completion.context(scratch / 'completion-context', expect_context)
            if expect_completion_policy is not None:
                acquisition_completion.seat_policy(completion_policy, expect_completion_policy)
                shutil.copyfile(completion_policy, scratch / 'completion-policy.json')
                completion_policy = scratch / 'completion-policy.json'
                acquisition_completion.seat_policy(completion_policy, expect_completion_policy)
        public = scratch / 'public'
        public.mkdir()
        shutil.copyfile(task_root / task['prompt'], public / 'prompt.md')
        shutil.copyfile(generator, public / 'generator.py')
        if public_pin is not None:
            destination = public / task['public_context']
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(task_root / task['public_context'], destination)
        if acquisition:
            prepared_config, _ = acquisition_completion.context(scratch / 'completion-context', expect_context)
            prepared_options = acquisition_completion.preparation_options(scratch / 'completion-context', prepared_config)
            effective_public = public / task['public_context'] if public_pin else prepared_options.get('public_context')
            effective_pin = public_pin or prepared_options.get('public_context_sha256')
            if public_pin and 'public_context_sha256' in prepared_options and prepared_options['public_context_sha256'] != public_pin:
                raise ValueError('task/public preparation context conflicts')
            if effective_public is not None:
                acquisition_completion.admit_public_context(scratch / 'completion-context', expect_context,
                    public / 'prompt.md', effective_public, effective_pin, deadline=productive, cancel=cancel)
        ledger['generator_sha256'] = digest(public / 'generator.py')
        save()
        for index in range(attempts):
            # Keep the last work credit and final wall reserve for grading.
            if (cancel.cancelled.is_set() or time.monotonic() >= productive or
                    ledger['work_started'] >= max_work - (phases - 1)):
                ledger['status'] = 'cancelled' if cancel.cancelled.is_set() else 'budget_exhausted'
                break
            entry = {'attempt': index + 1, 'status': 'preparing', 'evaluation_report': None,
                     'generation_execution': None, 'error': None}
            ledger['attempt_records'].append(entry)
            ledger['attempts_started'] += 1
            save()  # Count preparation before touching its work directory.
            admit('generation')
            work = scratch / f'attempt-{index + 1}'
            work.mkdir()
            (work / 'output').mkdir()
            if previous is not None:
                prior = strict_json(reports[-1].read_bytes())
                if not acquisition and previous.exists() and prior['generation']['output_error'] is None:
                    shutil.copytree(previous, work / 'previous')
                else:
                    (work / 'previous').mkdir()
                # Private evaluator feedback stays outside the acquisition writer view.
                # The trusted ADDR writer receives only its verified PUBLIC diagnostic finding.
                write_json(work / 'feedback.json', {'checks': [] if acquisition else prior['evaluation']['checks'],
                    'missing_deliverables': [] if acquisition else prior['generation']['missing_deliverables']})
            entry['status'] = 'generating'
            save()
            record = native_run.run(native_run.Intent('generation',
                ['/usr/bin/python3', '/candidate/generator.py', '/candidate/prompt.md', '/work/output'],
                timeout=timeout), inputs=public, work=work, deadline=productive, cancel=cancel)
            entry['generation_execution'] = record['execution']
            completion_work = None
            if acquisition:
                entry['qualification_execution'] = 'not_run'
                if record['execution'] == 'ok':
                    try:
                        admit('completion')
                    except native_run.DeadlineExhausted:
                        # Preserve grading for this admitted attempt; no late production.
                        pass
                    else:
                        entry['status'] = 'qualifying'; save()
                        completion_record = acquisition_completion.complete(public, work, scratch / f'production-{index + 1}',
                            scratch / 'completion-context', expect_context, attempt=index + 1,
                            deadline=productive, cancel=cancel, policy=completion_policy, expect_policy=expect_completion_policy,
                            public_context=public / task['public_context'] if public_pin else None,
                            public_context_sha256=public_pin)
                        entry['qualification_execution'] = completion_record['execution']
                        finished = time.monotonic()
                        completion_work = acquisition_completion.retain(scratch / f'completion-{index + 1}',
                            scratch / 'completion-context', expect_context, scratch / f'production-{index + 1}',
                            brief=public / 'prompt.md', attempt=index + 1, productive_deadline=productive, finished=finished,
                            deadline=deadline, cancel=cancel,
                            public_context=public / task['public_context'] if public_pin else None,
                            public_context_sha256=public_pin, completion_policy_sha256=expect_completion_policy)
                if completion_work is None:
                    # Even successful generation cannot bypass the required trusted phase.
                    record = dict(record, execution='failed')
            entry['status'] = 'grading'
            save()
            # Cancellation retains the generation attempt without claiming an evaluation.
            if not cancel.cancelled.is_set():
                admit('grading')
            else:
                entry['status'] = 'cancelled'
                ledger['status'] = 'cancelled'
                break
            path = evaluate(task_root, work / 'output', scratch / f'evaluation-{index + 1}', store,
                generation_execution='ok' if record['execution'] == 'ok' else 'failed', timeout=timeout,
                expect_pins=task['files']['pins.json'], execution_work=work, deadline=deadline, cancel=cancel,
                _case={'trial': str(scratch.resolve()), 'attempt': index + 1,
                       **({'completion_required': True, 'completion_context_pins': expect_context} if acquisition else {}),
                       **({'public_context_sha256': public_pin} if public_pin else {}),
                       **({'completion_policy_sha256': expect_completion_policy} if expect_completion_policy else {})}, completion_work=completion_work)
            reports.append(path)
            ledger['evaluations'].append(str(path))
            entry.update(status='evaluated', evaluation_report=str(path))
            report = strict_json(path.read_bytes())
            if time.monotonic() >= deadline or cancel.cancelled.is_set():
                raise native_run.DeadlineExhausted('case expired during grading/publication')
            if record['execution'] == 'ok' and report['result']['overall_pass'] is True:
                ledger['status'] = 'completed'
                break
            previous = work / 'output'
        else:
            ledger['status'] = 'attempts_exhausted'
    except native_run.DeadlineExhausted as exc:
        ledger['status'] = 'cancelled' if cancel.cancelled.is_set() else 'budget_exhausted'
        if ledger['attempt_records']:
            ledger['attempt_records'][-1].update(status=ledger['status'], error=str(exc))
        # Only the enclosing hard expiry cancels the case; a generation cutoff leaves grading time.
        if time.monotonic() >= deadline:
            cancel.cancel()
    except BaseException as exc:
        ledger['status'] = ('cancelled' if cancel.cancelled.is_set() else 'interrupted') if isinstance(exc, KeyboardInterrupt) else 'error'
        if ledger['attempt_records']:
            ledger['attempt_records'][-1].update(status=ledger['status'], error=type(exc).__name__ + ': ' + str(exc))
        cancel.cancel()
        raise
    finally:
        for entry in ledger['attempt_records']:
            record_path = scratch / f"attempt-{entry['attempt']}" / 'execution.json'
            if entry['generation_execution'] is None and record_path.is_file() and not record_path.is_symlink():
                entry['generation_execution'] = strict_json(record_path.read_bytes()).get('execution')
            if acquisition:
                partial = scratch / f"completion-{entry['attempt']}" / 'execution.json'
                if partial.is_file() and not partial.is_symlink():
                    entry['qualification_execution'] = strict_json(partial.read_bytes()).get('execution')
        if ledger['status'] == 'completed' and (time.monotonic() >= deadline or cancel.cancelled.is_set()):
            ledger['status'] = 'budget_exhausted' if time.monotonic() >= deadline else 'cancelled'
        save()
        if ledger['status'] == 'completed' and time.monotonic() >= deadline:
            ledger['status'] = 'budget_exhausted'
            save()
    return reports


def split_tasks(task_roots, exclusions, *, seed='0'):
    """Assign whole structural design groups, retaining versioned exclusion reasons."""
    exact_fields(exclusions, ('schema', 'version', 'tasks'))
    if exclusions['schema'] != 'agent-ee.generation-exclusions/1':
        raise ValueError('unknown exclusion schema')
    nonempty(exclusions['version'])
    tasks = [pinned_task(root) for root in task_roots]
    ids = {t['id'] for t in tasks}
    if len(ids) != len(tasks) or not set(exclusions['tasks']) <= ids:
        raise ValueError('duplicate task or exclusion for unknown task')
    for reason in exclusions['tasks'].values():
        nonempty(reason)
    # Excluding a degenerate design excludes every prompt alias of that design.
    excluded_fingerprints = {}
    for task in tasks:
        if task['id'] in exclusions['tasks']:
            excluded_fingerprints.setdefault(task['fingerprint'], set()).add(exclusions['tasks'][task['id']])
    result = {'schema': 'agent-ee.generation-split/1', 'seed': seed,
              'exclusion_version': exclusions['version'], 'train': [], 'test': [], 'excluded': []}
    for task in sorted(tasks, key=lambda t: t['id']):
        fp = task['fingerprint']
        if fp in excluded_fingerprints:
            result['excluded'].append({'id': task['id'], 'fingerprint': fp, 'reason': '; '.join(sorted(excluded_fingerprints[fp]))})
        else:
            side = 'test' if int(identity([seed, fp]), 16) % 5 == 0 else 'train'
            result[side].append({'id': task['id'], 'fingerprint': fp})
    return result


def average_scores(scores):
    def mean(key):
        return ratio(sum((Fraction(s[key]['numerator'], s[key]['denominator']) for s in scores),
                         Fraction()) / len(scores))
    lower, upper = mean('lower'), mean('upper')
    return {'tasks': len(scores), 'score': None if any(s['score'] is None for s in scores) else lower,
            'lower': lower, 'upper': upper}


def recorded_evaluation(path, *, expected=None):
    path = Path(path).resolve()
    report = strict_json(path.read_bytes())
    if (report.get('schema') != 'agent-ee.generation-evaluation/1' or
            'binding' not in report or path.name != 'report.json' or
            path.parent.name != identity(report['binding'])):
        raise ValueError('expected identity-bound generation evaluation report')
    expected_receipt = {'schema': 'agent-ee.generation-evaluation-record/1',
                'report': str(path), 'report_sha256': digest(path)}
    for record in sorted((path.parent / 'trials').glob('*.json')):
        receipt = strict_json(record.read_bytes())
        trial = Path(receipt['trial'])
        if ({k: v for k, v in receipt.items() if k != 'trial'} == expected_receipt and
                (trial / 'evaluation.json').is_file() and
                strict_json((trial / 'evaluation.json').read_bytes()) == expected_receipt):
            if expected is not None and series_entry(path, report, trial) != expected:
                raise ValueError('external series evidence digest mismatch')
            replay_report(trial, report)
            case_files(report, path)
            return report, trial
    raise ValueError('report is not bound to a recorded evaluation trial')


def recorded_report(path):
    return recorded_evaluation(path)[0]


def case_files(report, path):
    """Bind all admitted attempts alongside the selected report, including absent records."""
    case = report['binding'].get('case')
    if case is None:
        return None
    root = Path(case['trial'])
    ledger = strict_json((root / 'trial.json').read_bytes())
    entries = ledger['attempt_records']
    if (ledger['schema'] != 'agent-ee.generation-trial/1' or
            ledger['task_pins'] != report['binding']['task_files']['pins.json'] or
            ledger['attempts_started'] != len(entries) or
            [e['attempt'] for e in entries] != list(range(1, len(entries) + 1)) or
            not 1 <= case['attempt'] <= len(entries) or
            Path(entries[case['attempt'] - 1]['evaluation_report'] or '').resolve() != Path(path).resolve()):
        raise ValueError('selected report differs from case attempt ledger')
    if report['result']['overall_pass'] is True and (ledger['status'] != 'completed' or
            ledger['elapsed_s'] >= ledger['limits']['case_timeout_s']):
        raise ValueError('exhausted or incomplete case cannot supply success')
    if case.get('completion_required') and ledger.get('completion_context_pins') != case.get('completion_context_pins'):
        raise ValueError('case public completion context differs from binding')
    public_pin = case.get('public_context_sha256')
    if ledger.get('public_context_sha256') != public_pin:
        raise ValueError('case public context differs from ledger')
    if public_pin is not None:
        task = pinned_task(root / f"evaluation-{case['attempt']}/task", expect_pins=ledger['task_pins'])
        if task['files'].get(task.get('public_context')) != public_pin or digest(root / 'public' / task['public_context']) != public_pin:
            raise ValueError('case public context changed')
    policy_pin = case.get('completion_policy_sha256')
    if ledger.get('completion_policy_sha256') != policy_pin or (policy_pin is not None and
            digest(root / 'completion-policy.json') != policy_pin):
        raise ValueError('case completion policy differs from requested frozen policy')
    names = ['trial.json'] + (['completion-policy.json'] if policy_pin is not None else [])
    for entry in entries:
        n = entry['attempt']
        names += [f'attempt-{n}/' + name for name in RECORDS]
        names += [f'evaluation-{n}/evaluation.json']
        if 'completion_context_pins' in ledger:
            names += [f'completion-{n}/' + name for name in (*RECORDS, 'receipt.json')]
        if report.get('kind') == 'source_refusal' and entry['evaluation_report'] is not None:
            evaluation = root / f'evaluation-{n}'
            prior_path = Path(entry['evaluation_report']).resolve()
            receipt = strict_json((evaluation / 'evaluation.json').read_bytes())
            if receipt != {'schema': 'agent-ee.generation-evaluation-record/1',
                           'report': str(prior_path), 'report_sha256': digest(prior_path)}:
                raise ValueError('case refusal evaluation differs from its receipt')
            prior_report = strict_json(prior_path.read_bytes())
            if (prior_path.parent.name != identity(prior_report['binding'])
                    or prior_report['binding']['task_files'] != report['binding']['task_files']):
                raise ValueError('case refusal task or identity differs from selected report')
            replay_report(evaluation, prior_report)
    files = {}
    for name in names:
        source = root / name
        if source.is_symlink():
            raise ValueError('non-ordinary case record')
        if source.exists() and (not source.is_file() or source.stat().st_nlink != 1):
            raise ValueError('non-ordinary case record')
        files[name] = digest(source) if source.is_file() else None
    if 'completion_context_pins' in ledger:
        from .stage_receipts import tree_identity
        for entry in entries:
            name = f"completion-{entry['attempt']}"
            source = root / name
            if source.is_symlink():
                raise ValueError('non-ordinary completion root')
            files[name + '/'] = tree_identity(source) if source.exists() else None
            name = f"production-{entry['attempt']}"
            source = root / name
            if source.is_symlink():
                raise ValueError('non-ordinary production root')
            files[name + '/'] = tree_identity(source) if source.exists() else None
        files['completion-context/'] = tree_files(root / 'completion-context')
    return files


def series_entry(path, report, trial):
    seal = trial / 'run/work/verification/evidence.json'
    entry = {'binding_id': Path(path).parent.name, 'report_sha256': digest(path),
            'task_pins': report['binding']['task_files']['pins.json'],
            'evidence_sha256': digest(seal) if seal.exists() else None,
            'generation_files': tree_files(trial / 'generation')
            if 'generation_records' in report['binding'] else {}}
    files = case_files(report, path)
    if files is not None:
        entry['case_files'] = files
    return entry


def freeze_series(paths, task_pins):
    """Freeze trusted evaluations; the returned manifest and digest need external custody."""
    if not isinstance(task_pins, dict):
        raise ValueError('external task pins must be a task ID map')
    entries, tasks = [], set()
    for path in paths:
        report, trial = recorded_evaluation(path)
        task = report['task']
        if task in tasks:
            raise ValueError('freeze requires one selected attempt per task')
        tasks.add(task)
        if task_pins.get(task) != report['binding']['task_files']['pins.json']:
            raise ValueError('external task pins required at freeze boundary')
        if 'generation_records' not in report['binding']:
            raise ValueError('freeze requires retained executor records')
        entries.append(series_entry(path, report, trial))
    if not tasks or tasks != set(task_pins):
        raise ValueError('external task pins must cover exactly the frozen tasks')
    return {'schema': 'agent-ee.generation-series/1',
            'evaluations': sorted(entries, key=lambda e: e['binding_id'])}


def expected_series(paths, manifest, expect_pins):
    if (manifest is None) != (expect_pins is None):
        raise ValueError('external series requires both manifest and expect-pins')
    if manifest is None:
        return None
    manifest = Path(manifest)
    if manifest.is_symlink() or digest(manifest) != expect_pins:
        raise ValueError('external series manifest digest mismatch')
    series = strict_json(manifest.read_bytes())
    exact_fields(series, ('schema', 'evaluations'))
    if series['schema'] != 'agent-ee.generation-series/1':
        raise ValueError('unknown external series schema')
    entries = series['evaluations']
    if not isinstance(entries, list) or not entries:
        raise ValueError('empty external series')
    expected = {}
    for entry in entries:
        exact_fields(entry, ('binding_id', 'report_sha256', 'task_pins', 'evidence_sha256', 'generation_files',
                            *(['case_files'] if 'case_files' in entry else [])))
        if entry['binding_id'] in expected or not entry['generation_files']:
            raise ValueError('duplicate or unauthenticated external series entry')
        expected[entry['binding_id']] = entry
    if sorted(Path(p).parent.name for p in paths) != sorted(expected):
        raise ValueError('external series selection mismatch')
    for path in paths:
        if digest(path) != expected[Path(path).parent.name]['report_sha256']:
            raise ValueError('external series report digest mismatch')
    return expected


def replay_report(trial, report):
    """Recompute derived claims without invoking tools or trusting report verdicts."""
    binding = report['binding']
    task = pinned_task(trial / 'task', expect_pins=binding['task_files']['pins.json'])
    if task['files'] != binding['task_files']:
        raise ValueError('retained task differs from report binding')
    public_pin = task['files'].get(task.get('public_context'))
    if binding.get('public_context_sha256') != public_pin or (binding.get('case') is not None and
            binding['case'].get('public_context_sha256') != public_pin):
        raise ValueError('public case context replay pin mismatch')
    refusal = 'expected_refusal' in task
    if (report.get('kind') != ('source_refusal' if refusal else None)
            or binding.get('kind') != ('source_refusal' if refusal else None)):
        raise ValueError('report kind differs from task contract')
    selected = binding['outputs']
    if not set(selected) <= set(task['deliverables']):
        raise ValueError('unexpected bound outputs')
    output_error = binding['output_error']
    execution = binding['generation_execution']
    if execution not in ('ok', 'failed') or output_error not in (None, 'non_ordinary_output'):
        raise ValueError('invalid generation outcome')
    if output_error is not None and (execution != 'failed' or selected):
        raise ValueError('unsafe outputs cannot provide evidence')
    completion = None
    if 'completion_files' in binding:
        from . import acquisition_completion
        if tree_files(trial / 'completion') != binding['completion_files']:
            raise ValueError('completion evidence differs from binding')
        # Replay is not a new productive run. Production verification launches no tools.
        completion = acquisition_completion.verify(trial / 'completion', deadline=sys.float_info.max, replay=True)
        if (completion['brief_sha256'] != task['files'][task['prompt']] or
                completion.get('public_context_sha256') != public_pin or
                completion.get('completion_policy_sha256') != binding.get('case', {}).get('completion_policy_sha256')):
            raise ValueError('completion public brief/context differs from frozen task')
    generation_records = binding.get('generation_records')
    if generation_records is not None:
        if (set(generation_records) != set(RECORDS) or
                tree_files(trial / 'generation') != generation_records):
            raise ValueError('executor records differ from binding')
        observed_execution = executor_outcome(trial / 'generation')
        completion_failed = (completion is not None and completion['execution'] != 'ok') or (
            binding.get('case', {}).get('completion_required') and completion is None)
        if execution != ('failed' if output_error or completion_failed else observed_execution):
            raise ValueError('generation claim contradicts retained executor')
    if completion is not None and completion['execution'] == 'ok':
        current = tree_files(completion['outputs'])
        if selected != {k: v for k, v in current.items() if k in task['deliverables']}:
            raise ValueError('evaluated deliverables differ from production package')
    missing = [] if output_error else sorted(set(task['deliverables']) - set(selected))
    checks = []
    capture_error = None
    if refusal:
        if (trial / 'run').exists():
            raise ValueError('source refusal cannot claim native run evidence')
        output_tree = binding['output_tree']
        if output_error is None:
            snapshot = trial / 'outputs'
            if (tree_files(snapshot) != output_tree['files']
                    or _output_entries(snapshot) != output_tree['entries']
                    or selected != {k: v for k, v in output_tree['files'].items() if k in task['deliverables']}):
                raise ValueError('complete retained refusal output differs from binding')
            checks = [_refusal_check(task, snapshot, output_tree)]
        elif output_tree is not None:
            raise ValueError('unsafe refusal output cannot provide a snapshot')
    if not refusal and not missing and output_error is None:
        inputs = tree_files(trial / 'evaluation-inputs')
        expected_inputs = {k.removeprefix('evaluator/'): v for k, v in task['files'].items()
                           if k.startswith('evaluator/')}
        expected_inputs.update(selected)
        if generation_records is not None:
            expected_inputs.update({'.generation/' + k: v for k, v in generation_records.items()})
        if inputs != expected_inputs:
            raise ValueError('retained evaluation inputs differ from binding')
        run = trial / 'run'
        if (run / 'work/verification/evidence.json').exists():
            manifest = verify_snapshot(run)
            expected_scope = {**task['scope'], 'inputs': [
                {'path': 'evaluation-inputs/' + rel, 'at': rel} for rel in inputs]}
            if manifest['scope'] != expected_scope or manifest['files'] != expected_inputs:
                raise ValueError('retained candidate differs from task or outputs')
            checks = [strict_json((run / 'checks' / (c['id'] + '.json')).read_bytes())
                      for c in task['scope']['checks']]
            verify_evidence(run, checks, required=True)
        else:
            # Failed capture has no check evidence and can never establish a pass.
            capture_error = report['evaluation']['capture_error']
            if capture_error not in ('ValueError', 'FileNotFoundError'):
                raise ValueError('missing retained evidence seal')
    scoring = score(task['rules'], checks, missing)
    derived = {'task': task['id'], 'domain': task['domain'], 'difficulty': task['difficulty'],
               'fingerprint': task['fingerprint'], 'scoring': scoring,
               'result': combined_result(execution, scoring),
               'generation': {'execution': execution, 'missing_deliverables': missing,
                              'output_error': output_error},
               'evaluation': {'capture_error': capture_error,
                              'checks': [{k: c[k] for k in ('id', 'kind', 'execution', 'outcome')}
                                         for c in checks]}}
    if any(report.get(k) != v for k, v in derived.items()):
        raise ValueError('report contradicts retained evidence')


def aggregate(paths, *, manifest=None, expect_pins=None):
    paths = list(paths)
    expected = expected_series(paths, manifest, expect_pins)
    reports = [recorded_evaluation(p, expected=expected[Path(p).parent.name] if expected else None)[0]
               for p in paths]
    if len({r['task'] for r in reports}) != len(reports):
        raise ValueError('aggregate requires one selected attempt per task')
    rows = []
    for kind in sorted({r.get('kind', 'native_design') for r in reports}):
        kind_reports = [r for r in reports if r.get('kind', 'native_design') == kind]
        for dimension in ('domain', 'difficulty'):
            for value in sorted({r[dimension] for r in kind_reports}):
                group = [r for r in kind_reports if r[dimension] == value]
                categories = sorted({c for r in group for c in r['scoring']['categories']})
                rows.append({'dimension': dimension, 'value': value,
                             **average_scores([r['scoring'] for r in group]),
                             'categories': {c: average_scores([r['scoring']['categories'][c] for r in group
                                                              if c in r['scoring']['categories']]) for c in categories},
                             'passed': sum(r['result']['overall_pass'] is True for r in group),
                             'failed': sum(r['result']['overall_pass'] is False for r in group),
                             'unknown': sum(r['result']['overall_pass'] is None for r in group),
                             'generation_failed': sum(r['generation']['execution'] == 'failed' for r in group)})
                if any(r.get('kind') == 'source_refusal' for r in reports):
                    rows[-1]['kind'] = kind
    # Unanchored summaries are local consistency checks only; label them so they cannot
    # be mistaken for an externally authenticated series.
    return {'schema': 'agent-ee.generation-summary/1',
            'authentication': {'series_sha256': expect_pins} if expected else 'diagnostic-unauthenticated',
            'rows': rows}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    run = sub.add_parser('run')
    for name in ('task', 'generator', 'scratch', 'store'):
        run.add_argument('--' + name, required=True)
    run.add_argument('--attempts', type=int, default=3)
    run.add_argument('--timeout', type=int, default=120)
    run.add_argument('--case-timeout', type=int, help='one enclosing case limit in seconds (default: 2*attempts*timeout)')
    run.add_argument('--grading-reserve', type=int, help='final seconds unavailable to generation (default: timeout)')
    run.add_argument('--max-work', type=int, help='case phase admissions: generation subtree or grading (default: 2*attempts)')
    run.add_argument('--expect-pins', help='externally retained SHA-256 of task pins.json')
    run.add_argument('--completion-context', help='trusted pinned public acquisition context, separate from private task')
    run.add_argument('--expect-context', help='externally retained SHA-256 of public context pins.json')
    run.add_argument('--completion-policy', help='trusted parent-only judgment seat policy; never writer supplied')
    run.add_argument('--expect-completion-policy', help='external SHA-256 of trusted seat policy')
    split = sub.add_parser('split')
    split.add_argument('--exclusions', required=True)
    split.add_argument('--seed', default='0')
    split.add_argument('tasks', nargs='+')
    freeze = sub.add_parser('freeze')
    freeze.add_argument('--manifest', required=True)
    freeze.add_argument('--expect-pins', required=True, help='JSON map of task IDs to externally retained pins digests')
    freeze.add_argument('reports', nargs='+')
    summary = sub.add_parser('aggregate')
    summary.add_argument('--manifest', help='externally retained frozen series manifest')
    summary.add_argument('--expect-pins', help='externally retained SHA-256 of the series manifest')
    summary.add_argument('reports', nargs='+')
    args = parser.parse_args(argv)
    if args.command == 'run':
        result = [str(p) for p in run_task(args.task, args.generator, args.scratch, args.store,
                                         attempts=args.attempts, timeout=args.timeout,
                                         expect_pins=args.expect_pins, case_timeout=args.case_timeout,
                                         grading_reserve=args.grading_reserve, max_work=args.max_work,
                                         completion_context=args.completion_context, expect_context=args.expect_context,
                                         completion_policy=args.completion_policy, expect_completion_policy=args.expect_completion_policy)]
    elif args.command == 'split':
        result = split_tasks(args.tasks, strict_json(Path(args.exclusions).read_bytes()), seed=args.seed)
    elif args.command == 'freeze':
        manifest = freeze_series(args.reports, strict_json(Path(args.expect_pins).read_bytes()))
        path = Path(args.manifest)
        with path.open('xb') as stream:
            stream.write(json_bytes(manifest))
        result = {'manifest': str(path), 'sha256': digest(path)}
    else:
        result = aggregate(args.reports, manifest=args.manifest, expect_pins=args.expect_pins)
    print(json_bytes(result).decode(), end='')


if __name__ == '__main__':
    main()
