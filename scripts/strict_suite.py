#!/usr/bin/env python3
"""Run the whole unittest suite in strict toolchain mode, one process per module.

Every tests/test_*.py module runs in its own interpreter, loaded with the same
``discover("tests", pattern=...)`` semantics as the serial suite, through a
pool of --jobs workers with a private TMPDIR each. The run passes only when
every module reports a successful result and every skip names a gap declared
in AGENT_EE_DECLARED_GAPS (tests/toolchain.py, docs/container.md). A module
that exits without its result file, a malformed result or an unexpected success
fails the run; unknown is never pass. Declared-gap skips are listed by name on
stderr, as GitHub warning annotations and in the optional --summary file.
"""
import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / 'tests'
DECLARED_GAP = 'declared gap '  # tests/toolchain.py DECLARED_GAP
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


def judge(records, declared):
    """Return (problems, gaps) over every module record; any problem fails the run."""
    problems, gaps = [], []
    for record in records:
        if 'problem' in record:
            problems.append(f"{record['module']}: {record['problem']}")
            continue
        result = record['result']
        for kind in ('failures', 'errors'):
            problems += [f'{kind[:-1].upper()}: {test}' for test, _ in result[kind]]
        problems += [f'UNEXPECTED SUCCESS: {test}' for test in result['unexpected_successes']]
        for test, reason in result['skipped']:
            gap = reason[len(DECLARED_GAP):].split(':', 1)[0] if reason.startswith(DECLARED_GAP) else None
            if gap and gap in declared:
                gaps.append((gap, test, reason))
            else:
                problems.append(f'SKIP refused in strict mode: {test}: {reason}')
        if record['exit'] != 0 and not (result['failures'] or result['errors'] or result['unexpected_successes']):
            problems.append(f"{record['module']}: exit {record['exit']} with a successful result")
    return problems, gaps


def summary_markdown(records, problems, gaps, declared):
    run = sum(r['result']['run'] for r in records if 'result' in r)
    lines = ['## Strict suite', '',
             f"{len(records)} modules, {run} tests; {'FAILED' if problems else 'passed'}; "
             f'{len(gaps)} test(s) not executed as declared gaps.', '']
    if problems:
        lines += ['### Problems', ''] + [f'- `{p}`' for p in problems] + ['']
    for gap in sorted(declared):
        named = [(test, reason) for g, test, reason in gaps if g == gap]
        lines += [f'### Declared gap `{gap}`: {len(named)} test(s) not executed', '']
        lines += [f'- `{test}`: {reason}' for test, reason in named] or ['- none: every prerequisite was present']
        lines.append('')
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
    sys.path.insert(0, str(TESTS))
    from toolchain import declared_gaps
    declared = declared_gaps()
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
    problems, gaps = judge(records, declared)
    run = sum(r['result']['run'] for r in records if 'result' in r)
    for gap, test, reason in gaps:
        print(f'::warning title=declared gap {gap}::{test} not executed: {reason}')
    for problem in problems:
        print(problem, file=sys.stderr)
    print(f'strict suite: {len(records)} modules, {run} tests, {len(problems)} problem(s), '
          f'{len(gaps)} declared-gap skip(s) {sorted(declared) or "[]"}', file=sys.stderr)
    if args.summary:
        with args.summary.open('a') as stream:
            stream.write(summary_markdown(records, problems, gaps, declared) + '\n')
    return 1 if problems or len(records) != len(selected) else 0


if __name__ == '__main__':
    sys.exit(main())
