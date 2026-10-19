#!/usr/bin/env python3
"""Run the whole unittest suite in strict toolchain mode, one process per module.

Every tests/test_*.py module runs in its own interpreter, loaded with the same
``discover("tests", pattern=...)`` semantics as the serial suite, through a
pool of --jobs workers with a private TMPDIR each. The run passes only when
every module reports a successful result with no skip. A failure, error, skip,
expected failure, unexpected success, or a module that exits without a valid
result fails the run; unknown is never pass. A test that failed because a required input
partition is absent (tests/toolchain.py REQUIRED_INPUTS, docs/container.md) is
listed by partition as not executed, on stderr, as a GitHub error annotation
and in the optional --summary file; it fails the run like any other failure.
"""
import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / 'tests'
# tests/toolchain.py REQUIRED_INPUTS and MISSING_INPUT (tests/test_strict_suite.py keeps them equal).
REQUIRED_INPUTS = ('fome-mc-store',)
MISSING = re.compile(r'AGENT_EE_REQUIRE_TOOLCHAIN=1: required input (?P<gap>%s) missing: (?P<reason>[^\n]*)'
                     % '|'.join(map(re.escape, REQUIRED_INPUTS)))
CHILD = r'''
import json, sys, unittest
from pathlib import Path
module, out = sys.argv[1], Path(sys.argv[2])
suite = unittest.defaultTestLoader.discover('tests', pattern=module + '.py')
result = unittest.TextTestRunner(verbosity=2, stream=sys.stdout).run(suite)
out.write_text(json.dumps(dict(
    module=module, run=result.testsRun,
    failures=[[t.id(), text] for t, text in result.failures],
    errors=[[t.id(), text] for t, text in result.errors],
    skipped=[[t.id(), reason] for t, reason in result.skipped],
    expected_failures=[t.id() for t, _ in result.expectedFailures],
    unexpected_successes=[t.id() for t in result.unexpectedSuccesses])))
'''


def modules():
    return sorted(path.stem for path in TESTS.glob('test_*.py'))


def run_module(module, scratch, timeout):
    work = Path(tempfile.mkdtemp(prefix=module + '-', dir=scratch))
    tmp, out = work / 'tmp', work / 'result.json'
    tmp.mkdir()
    env = dict(os.environ, TMPDIR=str(tmp), PYTHONDONTWRITEBYTECODE='1')
    started = time.monotonic()
    try:
        proc = subprocess.run([sys.executable, '-c', CHILD, module, str(out)], cwd=ROOT, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
        log, code = proc.stdout.decode(errors='replace'), proc.returncode
    except subprocess.TimeoutExpired as exc:
        log, code = (exc.stdout or b'').decode(errors='replace'), f'timeout after {timeout}s'
    record = dict(module=module, exit=code, seconds=round(time.monotonic() - started, 1), log=log)
    try:
        result = json.loads(out.read_text())
        if result.get('module') != module or not isinstance(result.get('run'), int):
            raise ValueError('result does not describe this module')
        record['result'] = result
    except (OSError, ValueError) as exc:
        record['problem'] = f'no valid result ({type(exc).__name__}: {exc}); exit {code}'
    return record


def judge(records):
    """Return (problems, missing) over every module record; any problem fails the run.

    missing lists (partition, test, reason) for failures caused by an absent
    required input; each of them is also a problem."""
    problems, missing = [], []
    for record in records:
        if 'problem' in record:
            problems.append(f"{record['module']}: {record['problem']}")
            continue
        result = record['result']
        for kind in ('failures', 'errors'):
            for test, text in result[kind]:
                found = MISSING.search(text)
                if found:
                    missing.append((found['gap'], test, found['reason']))
                    problems.append(f"NOT EXECUTED, required input {found['gap']} missing: {test}")
                else:
                    problems.append(f'{kind[:-1].upper()}: {test}')
        problems += [f'UNEXPECTED SUCCESS: {test}' for test in result['unexpected_successes']]
        # unittest counts an expected failure as success; the strict gate does not.
        problems += [f'EXPECTED FAILURE refused in strict mode: {test}' for test in result['expected_failures']]
        problems += [f'SKIP refused in strict mode: {test}: {reason}' for test, reason in result['skipped']]
        if record['exit'] != 0 and not (result['failures'] or result['errors'] or result['unexpected_successes']):
            problems.append(f"{record['module']}: exit {record['exit']} with a successful result")
    return problems, missing


def verdict(records, problems, missing):
    run = sum(r['result']['run'] for r in records if 'result' in r)
    if not problems:
        return f'strict suite passed: {len(records)} modules, {run} tests, no skips'
    return (f'strict suite FAILED: {len(records)} modules, {run} tests, {len(problems)} problem(s), '
            f'{len(missing)} required test(s) not executed for missing inputs, '
            f'{len(problems) - len(missing)} other problem(s)')


def summary_markdown(records, problems, missing):
    lines = ['## Strict suite (required full gate)', '', verdict(records, problems, missing) + '.', '']
    for gap in REQUIRED_INPUTS:
        named = [(test, reason) for g, test, reason in missing if g == gap]
        if named:
            lines += [f'### Required input `{gap}` missing: {len(named)} required test(s) not executed', '']
            lines += [f'- `{test}`: {reason}' for test, reason in named] + ['']
    other = [p for p in problems if not p.startswith('NOT EXECUTED, required input ')]
    if other:
        lines += ['### Other problems', ''] + [f'- `{p}`' for p in other] + ['']
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n', 1)[0])
    parser.add_argument('--jobs', type=int, default=os.cpu_count() or 1)
    parser.add_argument('--module-timeout', type=int, default=3600)
    parser.add_argument('--summary', type=Path, help='append a Markdown summary to this file')
    parser.add_argument('modules', nargs='*', help='test modules (default: every tests/test_*.py)')
    args = parser.parse_args(argv)
    if os.environ.get('AGENT_EE_REQUIRE_TOOLCHAIN') != '1':
        parser.error('strict suite requires AGENT_EE_REQUIRE_TOOLCHAIN=1')
    selected = args.modules or modules()
    unknown = sorted(set(selected).difference(modules()))
    if unknown:
        parser.error('unknown test module(s): ' + ', '.join(unknown))
    lock, records = threading.Lock(), []
    with tempfile.TemporaryDirectory(prefix='agent-ee-strict-suite-') as scratch, \
            concurrent.futures.ThreadPoolExecutor(max(1, args.jobs)) as pool:
        futures = [pool.submit(run_module, m, scratch, args.module_timeout) for m in selected]
        for future in concurrent.futures.as_completed(futures):
            record = future.result()
            with lock:
                records.append(record)
                print(f"===== {record['module']} ({record['seconds']}s, exit {record['exit']}) "
                      f'[{len(records)}/{len(selected)}]', flush=True)
                print(record['log'], end='' if record['log'].endswith('\n') else '\n', flush=True)
    records.sort(key=lambda r: r['module'])
    problems, missing = judge(records)
    if len(records) != len(selected):
        problems.append(f'{len(records)} module records for {len(selected)} selected modules')
    for gap, test, reason in missing:
        print(f'::error title=required input {gap} missing::{test} not executed: {reason}')
    for problem in problems:
        print(problem, file=sys.stderr)
    print(verdict(records, problems, missing), file=sys.stderr)
    if args.summary:
        with args.summary.open('a') as stream:
            stream.write(summary_markdown(records, problems, missing) + '\n')
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
