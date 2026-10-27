"""Frozen, offline generation trials over the ordinary candidate/check pipeline."""
import argparse
import shutil
import stat
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
    exact_fields(task, ('schema', 'id', 'domain', 'difficulty', 'prompt', 'reference',
                        'deliverables', 'rules', 'scope'))
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
             expect_pins=None, execution_work=None):
    """Evaluate one retained output; generator result.json claims are never read."""
    from .checks import run_checks
    if type(timeout) is not int or timeout < 1:
        raise ValueError('timeout must be a positive integer')
    if generation_execution not in ('ok', 'failed'):
        raise ValueError('generation execution must be ok or failed')
    task_root, outputs, trial = Path(task_root), Path(outputs), Path(trial)
    task = pinned_task(task_root, expect_pins=expect_pins)
    observed, output_error = output_files(outputs)
    if output_error:
        generation_execution = 'failed'
    missing = [] if output_error else sorted(set(task['deliverables']) - set(observed))
    selected = {k: observed[k] for k in task['deliverables'] if k in observed}
    environment = evaluator_identity()
    trial.mkdir(parents=True, exist_ok=False)
    generation_records = None
    if execution_work is not None:
        generation = trial / 'generation'
        generation.mkdir()
        for name in RECORDS:
            source = Path(execution_work) / name
            if source.is_symlink() or not source.is_file() or source.stat().st_nlink != 1:
                raise ValueError('non-ordinary executor record')
            shutil.copyfile(source, generation / name)
        generation_records = tree_files(generation)
        generation_execution = executor_outcome(generation)
        if output_error:
            generation_execution = 'failed'
    shutil.copytree(task_root, trial / 'task')
    if pinned_task(trial / 'task')['files'] != task['files']:
        raise ValueError('task changed during capture')
    checks = []
    capture_error = None
    if not missing and output_error is None:
        inputs = trial / 'evaluation-inputs'
        shutil.copytree(task_root / 'evaluator', inputs)
        if generation_records is not None:
            shutil.copytree(trial / 'generation', inputs / '.generation')
        for rel in task['deliverables']:
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
            capture(trial / 'scope.json', run)
        except (ValueError, FileNotFoundError) as exc:
            capture_error = type(exc).__name__
        else:
            checks = run_checks(run, time.monotonic() + timeout)
            freeze_evidence(run, checks)
            verify_evidence(run, checks, required=True)
    current, current_error = output_files(outputs)
    if (pinned_task(task_root)['files'] != task['files'] or
            (current, current_error) != (observed, output_error)):
        raise ValueError('task or output changed during evaluation')
    if evaluator_identity() != environment:
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
    if generation_records is not None:
        report['binding']['generation_records'] = generation_records
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


def run_task(task_root, generator, scratch, store, *, attempts=3, timeout=120, expect_pins=None):
    """One initial attempt plus at most two repairs, each in its own offline sandbox."""
    if type(attempts) is not int or not 1 <= attempts <= 3:
        raise ValueError('attempts must be 1..3')
    if type(timeout) is not int or timeout < 1:
        raise ValueError('timeout must be a positive integer')
    task_root, scratch = Path(task_root), Path(scratch)
    task = pinned_task(task_root, expect_pins=expect_pins)
    scratch.mkdir(parents=True, exist_ok=False)
    public = scratch / 'public'
    public.mkdir()
    shutil.copyfile(task_root / task['prompt'], public / 'prompt.md')
    shutil.copyfile(generator, public / 'generator.py')
    generator_hash = digest(public / 'generator.py')
    reports = []
    previous = None
    for index in range(attempts):
        work = scratch / f'attempt-{index + 1}'
        work.mkdir()
        (work / 'output').mkdir()
        if previous is not None:
            prior = strict_json(reports[-1].read_bytes())
            if previous.exists() and prior['generation']['output_error'] is None:
                shutil.copytree(previous, work / 'previous')
            else:
                (work / 'previous').mkdir()
            write_json(work / 'feedback.json', {
                'checks': prior['evaluation']['checks'],
                'missing_deliverables': prior['generation']['missing_deliverables']})
        record = native_run.run(
            native_run.Intent(
                'generation',
                ['/usr/bin/python3', '/candidate/generator.py', '/candidate/prompt.md', '/work/output'],
                timeout=timeout,
            ),
            inputs=public,
            work=work,
        )
        path = evaluate(task_root, work / 'output', scratch / f'evaluation-{index + 1}', store,
                        generation_execution='ok' if record['execution'] == 'ok' else 'failed', timeout=timeout,
                        expect_pins=task['files']['pins.json'], execution_work=work)
        reports.append(path)
        # Repair feedback reveals only check status, never evaluator rules or criterion text.
        report = strict_json(path.read_bytes())
        if record['execution'] == 'ok' and report['scoring']['overall_pass'] is True:
            break
        previous = work / 'output'
    write_json(scratch / 'trial.json', {'schema': 'agent-ee.generation-trial/1',
                                      'generator_sha256': generator_hash,
                                      'evaluations': [str(p) for p in reports]})
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
            return report, trial
    raise ValueError('report is not bound to a recorded evaluation trial')


def recorded_report(path):
    return recorded_evaluation(path)[0]


def series_entry(path, report, trial):
    seal = trial / 'run/work/verification/evidence.json'
    return {'binding_id': Path(path).parent.name, 'report_sha256': digest(path),
            'task_pins': report['binding']['task_files']['pins.json'],
            'evidence_sha256': digest(seal) if seal.exists() else None,
            'generation_files': tree_files(trial / 'generation')
            if 'generation_records' in report['binding'] else {}}


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
        exact_fields(entry, ('binding_id', 'report_sha256', 'task_pins', 'evidence_sha256', 'generation_files'))
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
    selected = binding['outputs']
    if not set(selected) <= set(task['deliverables']):
        raise ValueError('unexpected bound outputs')
    output_error = binding['output_error']
    execution = binding['generation_execution']
    if execution not in ('ok', 'failed') or output_error not in (None, 'non_ordinary_output'):
        raise ValueError('invalid generation outcome')
    if output_error is not None and (execution != 'failed' or selected):
        raise ValueError('unsafe outputs cannot provide evidence')
    generation_records = binding.get('generation_records')
    if generation_records is not None:
        if (set(generation_records) != set(RECORDS) or
                tree_files(trial / 'generation') != generation_records):
            raise ValueError('executor records differ from binding')
        observed_execution = executor_outcome(trial / 'generation')
        if execution != ('failed' if output_error else observed_execution):
            raise ValueError('generation claim contradicts retained executor')
    missing = [] if output_error else sorted(set(task['deliverables']) - set(selected))
    checks = []
    capture_error = None
    if not missing and output_error is None:
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
    for dimension in ('domain', 'difficulty'):
        for value in sorted({r[dimension] for r in reports}):
            group = [r for r in reports if r[dimension] == value]
            categories = sorted({c for r in group for c in r['scoring']['categories']})
            rows.append({'dimension': dimension, 'value': value,
                         **average_scores([r['scoring'] for r in group]),
                         'categories': {c: average_scores([r['scoring']['categories'][c] for r in group
                                                          if c in r['scoring']['categories']]) for c in categories},
                         'passed': sum(r['result']['overall_pass'] is True for r in group),
                         'failed': sum(r['result']['overall_pass'] is False for r in group),
                         'unknown': sum(r['result']['overall_pass'] is None for r in group),
                         'generation_failed': sum(r['generation']['execution'] == 'failed' for r in group)})
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
    run.add_argument('--expect-pins', help='externally retained SHA-256 of task pins.json')
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
                                         expect_pins=args.expect_pins)]
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
