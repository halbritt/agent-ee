"""Frozen, offline generation trials over the ordinary candidate/check pipeline."""
import argparse
import shutil
import stat
import sys
import time
from fractions import Fraction
from pathlib import Path

from .baseline import compile_baseline, freeze_evidence, verify_evidence
from .candidate import capture, safe_relative
from .catalog import exact_fields, nonempty, strict_json
from .design_diff import read_side, summarize
from .executor import SANDBOX_PATH, execute
from .findings import safe_id
from .storage import digest, fingerprint, identity, json_bytes, write_json


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


def pinned_task(root):
    root = Path(root)
    files = tree_files(root)
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
    if (task['prompt'].startswith('evaluator/') or
            task['prompt'] in (task['reference'], 'task.json', 'pins.json')):
        raise ValueError('public prompt must be separate from evaluator inputs')
    deliverables = task['deliverables']
    if not isinstance(deliverables, list) or not deliverables or len(set(deliverables)) != len(deliverables):
        raise ValueError('expected unique deliverables')
    for path in deliverables:
        safe_relative(path)
        if path in ('candidate.json', 'baseline.json', 'scope.json') or path.startswith(('checks/', 'work/')):
            raise ValueError('reserved deliverable path')
    scope = task['scope']
    if scope['schema'] not in ('agent-ee.scope/2', 'agent-ee.scope/3') or scope['inputs']:
        raise ValueError('benchmark scope must use scope/2 or scope/3 and empty inputs')
    # All immutable inputs live under evaluator/. The writer supplies only declared files.
    evaluator = {k.removeprefix('evaluator/'): v for k, v in files.items() if k.startswith('evaluator/')}
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
    node, _ = read_side((root / task['reference']).read_bytes(), 'schematic', None)
    task['fingerprint'] = summarize(node, 'schematic')['hashes']['structural']
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
    from .cli import pcbnew_extension, policy_files
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


def evaluate(task_root, outputs, trial, store, *, generation_execution='ok', timeout=120):
    """Evaluate one retained output; generator result.json claims are never read."""
    from .checks import run_checks
    if type(timeout) is not int or timeout < 1:
        raise ValueError('timeout must be a positive integer')
    if generation_execution not in ('ok', 'failed'):
        raise ValueError('generation execution must be ok or failed')
    task_root, outputs, trial = Path(task_root), Path(outputs), Path(trial)
    task = pinned_task(task_root)
    observed, output_error = output_files(outputs)
    if output_error:
        generation_execution = 'failed'
    missing = [] if output_error else sorted(set(task['deliverables']) - set(observed))
    selected = {k: observed[k] for k in task['deliverables'] if k in observed}
    environment = evaluator_identity()
    trial.mkdir(parents=True, exist_ok=False)
    checks = []
    capture_error = None
    if not missing and output_error is None:
        inputs = trial / 'evaluation-inputs'
        shutil.copytree(task_root / 'evaluator', inputs)
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


def run_task(task_root, generator, scratch, store, *, attempts=3, timeout=120):
    """One initial attempt plus at most two repairs, each in its own offline sandbox."""
    if type(attempts) is not int or not 1 <= attempts <= 3:
        raise ValueError('attempts must be 1..3')
    if type(timeout) is not int or timeout < 1:
        raise ValueError('timeout must be a positive integer')
    task_root, scratch = Path(task_root), Path(scratch)
    task = pinned_task(task_root)
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
        record = execute(public, work, ['/usr/bin/python3', '/candidate/generator.py',
                                        '/candidate/prompt.md', '/work/output'], timeout=timeout)
        path = evaluate(task_root, work / 'output', scratch / f'evaluation-{index + 1}', store,
                        generation_execution='ok' if record['execution'] == 'ok' else 'failed', timeout=timeout)
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


def recorded_report(path):
    path = Path(path).resolve()
    report = strict_json(path.read_bytes())
    if (report.get('schema') != 'agent-ee.generation-evaluation/1' or
            'binding' not in report or path.name != 'report.json' or
            path.parent.name != identity(report['binding'])):
        raise ValueError('expected identity-bound generation evaluation report')
    expected = {'schema': 'agent-ee.generation-evaluation-record/1',
                'report': str(path), 'report_sha256': digest(path)}
    for record in sorted((path.parent / 'trials').glob('*.json')):
        receipt = strict_json(record.read_bytes())
        trial = Path(receipt['trial'])
        if ({k: v for k, v in receipt.items() if k != 'trial'} == expected and
                (trial / 'evaluation.json').is_file() and
                strict_json((trial / 'evaluation.json').read_bytes()) == expected):
            return report
    raise ValueError('report is not bound to a recorded evaluation trial')


def aggregate(paths):
    reports = [recorded_report(p) for p in paths]
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
                         'passed': sum(r['scoring']['overall_pass'] is True for r in group),
                         'failed': sum(r['scoring']['overall_pass'] is False for r in group),
                         'unknown': sum(r['scoring']['overall_pass'] is None for r in group),
                         'generation_failed': sum(r['generation']['execution'] == 'failed' for r in group)})
    return {'schema': 'agent-ee.generation-summary/1', 'rows': rows}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    run = sub.add_parser('run')
    for name in ('task', 'generator', 'scratch', 'store'):
        run.add_argument('--' + name, required=True)
    run.add_argument('--attempts', type=int, default=3)
    run.add_argument('--timeout', type=int, default=120)
    split = sub.add_parser('split')
    split.add_argument('--exclusions', required=True)
    split.add_argument('--seed', default='0')
    split.add_argument('tasks', nargs='+')
    summary = sub.add_parser('aggregate')
    summary.add_argument('reports', nargs='+')
    args = parser.parse_args(argv)
    if args.command == 'run':
        result = [str(p) for p in run_task(args.task, args.generator, args.scratch, args.store,
                                         attempts=args.attempts, timeout=args.timeout)]
    elif args.command == 'split':
        result = split_tasks(args.tasks, strict_json(Path(args.exclusions).read_bytes()), seed=args.seed)
    else:
        result = aggregate(args.reports)
    print(json_bytes(result).decode(), end='')


if __name__ == '__main__':
    main()
