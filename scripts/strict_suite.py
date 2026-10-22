#!/usr/bin/env python3
"""Run selected unittest regressions in strict toolchain mode, one process per module.

Every tests/test_*.py module runs in its own interpreter, loaded with the same
``discover("tests", pattern=...)`` semantics as the serial suite, through a
pool of --jobs workers with a private TMPDIR each. The run passes only when
every module reports a successful result with no skip. A failure, error, skip,
expected failure, unexpected success, or a module that exits without a valid
result fails the run; unknown is never pass. A test that failed because a required input
partition is absent (tests/toolchain.py REQUIRED_INPUTS, docs/container.md) is
listed by partition as not executed, on stderr, as a GitHub error annotation
and in the optional --summary file; it fails the run like any other failure.
Native 3D board render regressions may be omitted only by an explicit selection;
the summary names every test omitted that way. An unknown change base runs them.
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
RENDER_CHANGE_PATHS = {
    '.github/workflows/ci.yml', 'scripts/strict_suite.py', 'tests/toolchain.py',
    'src/agent_ee/candidate.py', 'src/agent_ee/candidate_visuals.py',
    'src/agent_ee/cli.py', 'src/agent_ee/executor.py', 'src/agent_ee/models3d.py',
    'src/agent_ee/placer_runner.py', 'src/agent_ee/viewer.py',
    'tests/test_candidate_visuals.py', 'tests/test_render3d.py',
    'tests/test_render_gallery.py', 'tests/test_placer_native.py',
    'tests/test_strict_suite.py', 'tests/test_toolchain.py',
}
# tests/toolchain.py REQUIRED_INPUTS and MISSING_INPUT (tests/test_strict_suite.py keeps them equal).
REQUIRED_INPUTS = ('fome-mc-store',)
MISSING = re.compile(r'AGENT_EE_REQUIRE_TOOLCHAIN=1: required input (?P<gap>%s) missing: (?P<reason>[^\n]*)'
                     % '|'.join(map(re.escape, REQUIRED_INPUTS)))
CHILD = r'''
import json, sys, unittest
from pathlib import Path
module, out, render_mode = sys.argv[1], Path(sys.argv[2]), sys.argv[3]
import tests  # Apply the shared test environment before loading any module.
suite = unittest.defaultTestLoader.discover('tests', pattern=module + '.py')
discovered = suite.countTestCases()
def cases(group):
    for child in group:
        if isinstance(child, unittest.TestSuite):
            yield from cases(child)
        else:
            yield child
excluded = []
if render_mode == 'omit':
    selected = []
    for test in cases(suite):
        if getattr(getattr(test, test._testMethodName), '_agent_ee_native_board_render', False):
            excluded.append(test.id())
        else:
            selected.append(test)
    suite = unittest.TestSuite(selected)
result = unittest.TextTestRunner(verbosity=2, stream=sys.stdout).run(suite)
out.write_text(json.dumps(dict(
    module=module, run=result.testsRun, discovered=discovered,
    excluded_native_board_render=excluded,
    failures=[[t.id(), text] for t, text in result.failures],
    errors=[[t.id(), text] for t, text in result.errors],
    skipped=[[t.id(), reason] for t, reason in result.skipped],
    expected_failures=[t.id() for t, _ in result.expectedFailures],
    unexpected_successes=[t.id() for t in result.unexpectedSuccesses])))
'''


def modules():
    return sorted(path.stem for path in TESTS.glob('test_*.py'))


def render_mode_from_change_base(base):
    """A missing base, Git failure or dirty checkout cannot justify omission."""
    if not base:
        return 'run'
    try:
        dirty = subprocess.run(['git', 'status', '--porcelain'], cwd=ROOT,
                               capture_output=True, check=True).stdout
        if dirty:
            return 'run'
        changed = subprocess.run(['git', 'diff', '--name-only', '-z', '--no-renames',
                                  base, 'HEAD'], cwd=ROOT, capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return 'run'
    for raw_path in changed.split(b'\0'):
        path = os.fsdecode(raw_path)
        if (path in RENDER_CHANGE_PATHS or path.startswith('fixtures/rc/control/')
                or ((path.startswith('src/agent_ee/') or path.startswith('tests/test_')) and
                    any(word in Path(path).stem for word in ('render', 'visual', 'viewer', '3d')))):
            return 'run'
    return 'omit'


def run_module(module, scratch, timeout, render_mode='run'):
    work = Path(tempfile.mkdtemp(prefix=module + '-', dir=scratch))
    tmp, out = work / 'tmp', work / 'result.json'
    tmp.mkdir()
    env = dict(os.environ, TMPDIR=str(tmp), PYTHONDONTWRITEBYTECODE='1')
    started = time.monotonic()
    try:
        proc = subprocess.run([sys.executable, '-c', CHILD, module, str(out), render_mode], cwd=ROOT, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
        log, code = proc.stdout.decode(errors='replace'), proc.returncode
    except subprocess.TimeoutExpired as exc:
        log, code = (exc.stdout or b'').decode(errors='replace'), f'timeout after {timeout}s'
    record = dict(module=module, exit=code, seconds=round(time.monotonic() - started, 1), log=log)
    try:
        result = json.loads(out.read_text())
        excluded = result.get('excluded_native_board_render')
        if (result.get('module') != module or not isinstance(result.get('run'), int)
                or not isinstance(result.get('discovered'), int)
                or not isinstance(excluded, list) or not all(isinstance(test, str) for test in excluded)
                or result['run'] + len(excluded) != result['discovered']):
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
    omitted = sum(len(r['result'].get('excluded_native_board_render', []))
                  for r in records if 'result' in r)
    if not problems:
        suffix = f'; {omitted} native 3D board render tests not selected' if omitted else ''
        return f'strict suite passed: {len(records)} modules, {run} tests, no skips{suffix}'
    return (f'strict suite FAILED: {len(records)} modules, {run} tests, {len(problems)} problem(s), '
            f'{len(missing)} required test(s) not executed for missing inputs, '
            f'{len(problems) - len(missing)} other problem(s)')


def summary_markdown(records, problems, missing, render_mode='run'):
    title = ('## Strict suite (native board renders change-selected)' if render_mode == 'omit'
             else '## Strict suite (native board renders included)')
    lines = [title, '', verdict(records, problems, missing) + '.', '']
    if render_mode == 'omit':
        omitted = sorted(test for record in records for test in
                         record.get('result', {}).get('excluded_native_board_render', []))
        lines += [f'### Native 3D board render tests not selected: {len(omitted)}', '']
        lines += [f'- `{test}`' for test in omitted] + ['']
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
    parser.add_argument('--render-change-base', help='compare this Git commit with HEAD; unknown base runs renders')
    parser.add_argument('--print-render-mode', action='store_true', help='print run or omit for the change base')
    parser.add_argument('--render-mode', choices=('run', 'omit'),
                        help='explicit native 3D board render selection (default: run)')
    parser.add_argument('modules', nargs='*', help='test modules (default: every tests/test_*.py)')
    args = parser.parse_args(argv)
    if args.print_render_mode:
        if args.modules or args.render_mode or args.summary:
            parser.error('--print-render-mode only accepts --render-change-base')
        print(render_mode_from_change_base(args.render_change_base))
        return 0
    if args.render_mode and args.render_change_base:
        parser.error('--render-mode and --render-change-base are mutually exclusive')
    render_mode = (render_mode_from_change_base(args.render_change_base) if args.render_change_base is not None
                   else args.render_mode or os.environ.get('AGENT_EE_NATIVE_RENDER_MODE', 'run'))
    if render_mode not in ('run', 'omit'):
        parser.error('AGENT_EE_NATIVE_RENDER_MODE must be run or omit')
    if os.environ.get('AGENT_EE_REQUIRE_TOOLCHAIN') != '1':
        parser.error('strict suite requires AGENT_EE_REQUIRE_TOOLCHAIN=1')
    selected = args.modules or modules()
    unknown = sorted(set(selected).difference(modules()))
    if unknown:
        parser.error('unknown test module(s): ' + ', '.join(unknown))
    lock, records = threading.Lock(), []
    with tempfile.TemporaryDirectory(prefix='agent-ee-strict-suite-') as scratch, \
            concurrent.futures.ThreadPoolExecutor(max(1, args.jobs)) as pool:
        futures = [pool.submit(run_module, m, scratch, args.module_timeout, render_mode) for m in selected]
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
            stream.write(summary_markdown(records, problems, missing, render_mode) + '\n')
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
