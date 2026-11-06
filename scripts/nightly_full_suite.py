#!/usr/bin/env python3
"""Nightly Full Suite Runner (AGENTEE-614 Part 2 / Defect #42).

Runs the full strict test suite nightly at weight 2 (keeping 2 of 4 host units
free to prevent starvation of reviews/builds per AGENTEE-610).
Operates in an isolated worktree and skips execution if origin/main has not moved.
Parses problems, computes comparisons, writes integrator failure records,
files deduplicated Plane issues on new regressions, and publishes GitHub commit
statuses without exposing logs or local machine paths.
"""
import argparse
import collections
from datetime import datetime, timezone
import fcntl
import hashlib
import html
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys


DEFAULT_STATE_DIR = Path.home() / '.local/share/agent-ee-full-suite'
DEFAULT_WORKTREE_DIR = Path.home() / '.cache/agent-worktrees/agent-ee/nightly-full-suite'
SUITE_TIMEOUT_SECONDS = 7200
SUITE_STOP_GRACE_SECONDS = 60


def split_skip_identity(text: str) -> tuple[str, str]:
    """The reason delimiter occurs outside unittest subtest brackets/quotes."""
    stack, quote, escaped = [], None, False
    for index, char in enumerate(text):
        if quote:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == quote:
                quote = None
        elif stack and char in ('\"', "'"):
            quote = char
        elif char in '([{':
            stack.append(char)
        elif char in ')]}':
            if not stack or stack.pop() != {')': '(', ']': '[', '}': '{'}[char]:
                raise ValueError('ambiguous strict-suite skip identity')
        elif char == ':' and not stack and text[index:index + 2] == ': ':
            return text[:index], text[index + 2:]
    raise ValueError('strict-suite skip has no reason delimiter')


def parse_problems_from_log(log_path: Path | str) -> dict[str, str]:
    """Read only the final strict-suite verdict and its exact problem inventory.

    Raw unittest output repeats these problems and uses a different subtest
    spelling. An absent or incomplete verdict cannot establish zero problems.
    """
    lines = Path(log_path).read_text(encoding='utf-8', errors='strict').splitlines()
    verdict_pattern = re.compile(
        r'strict suite FAILED: \d+ modules, \d+ tests, (\d+) problem\(s\), '
        r'\d+ required test\(s\) not executed for missing inputs, \d+ other problem\(s\)'
        r'(?:; \d+ required module setup\(s\) refused, unexecuted test count unknown)?')
    verdicts = []
    for index, line in enumerate(lines):
        if re.fullmatch(r'strict suite passed: \d+ modules, \d+ tests, no skips', line):
            verdicts.append((index, 0))
        elif match := verdict_pattern.fullmatch(line):
            if int(match[1]) == 0:
                raise ValueError('failed strict-suite verdict must name a problem')
            verdicts.append((index, int(match[1])))
    if len(verdicts) != 1:
        raise ValueError('log must contain one complete full strict-suite verdict')
    end, count = verdicts[0]
    if not count:
        return {}
    if end < count:
        raise ValueError('strict-suite problem inventory is truncated')
    out = {}
    occurrences = collections.Counter()
    for line in lines[end - count:end]:
        if match := re.fullmatch(r'(FAILURE|ERROR|UNEXPECTED SUCCESS|EXPECTED FAILURE refused in strict mode): (.+)', line):
            kind, key = match.groups()
            kind = 'FAIL' if kind == 'FAILURE' else kind
        elif line.startswith('SKIP refused in strict mode: '):
            key, reason = split_skip_identity(line.removeprefix('SKIP refused in strict mode: '))
            kind = 'SKIP: ' + reason
        elif match := re.fullmatch(r'NOT EXECUTED, required input (\S+) missing: (.+)', line):
            gap, key = match.groups()
            kind = 'MISSING-INPUT ' + gap
        elif match := re.fullmatch(r'(test_[\w.]+): (.+)', line):
            key, reason = match.groups()
            kind = 'MODULE: ' + reason
        elif match := re.fullmatch(r'\d+ module records for \d+ selected modules', line):
            key, kind = 'strict-suite.module-records', 'MODULE: ' + line
        else:
            raise ValueError('unrecognized strict-suite problem inventory line')
        occurrences[key] += 1
        if occurrences[key] > 1:
            key += f' [occurrence {occurrences[key]}]'
        if key in out:
            raise ValueError('ambiguous strict-suite problem identity')
        out[key] = kind
    return out


def compare_problems(old: dict[str, str], new: dict[str, str]) -> dict:
    """Compare old problem set with new problem set."""
    old_set = set(old.keys())
    new_set = set(new.keys())
    gone = sorted(old_set - new_set)
    added = sorted(new_set - old_set)
    def kind_class(kind):
        return kind.split(':', 1)[0] if kind.startswith(('SKIP:', 'MODULE:')) else kind.split(' ', 1)[0]
    changed = sorted(key for key in old_set & new_set if kind_class(old[key]) != kind_class(new[key]))
    kept = sorted((old_set & new_set).difference(changed))
    return {
        'old': old,
        'new': new,
        'fixed': gone,
        'added': added,
        'kept': kept,
        'changed': changed,
    }


def regressions(comp: dict) -> list[str]:
    return sorted(set(comp['added']) | set(comp.get('changed', [])))


def format_compare_summary(comp: dict) -> str:
    """Format human-readable comparison text matching baseline compare format."""
    old, new = comp['old'], comp['new']
    if comp.get('baseline'):
        return f'baseline {len(new)} problems; no prior run for comparison\n'
    gone, added, kept = comp['fixed'], comp['added'], comp['kept']
    lines = [
        f'old {len(old)} new {len(new)} fixed {len(gone)} added {len(added)} kept {len(kept)}'
    ]
    if added:
        mods = collections.Counter(k.split('.')[0] for k in added)
        lines.append(f'ADDED by module: {dict(mods)}')
        for k in added:
            lines.append(f'  + {k} | {new.get(k, "FAIL")}')
    if changed := comp.get('changed'):
        lines.append(f'CHANGED kind: {len(changed)}')
        lines += [f'  ! {key} | {old[key]} -> {new[key]}' for key in changed]
    if kept:
        kinds = collections.Counter(new.get(k, '').split(':')[0] for k in kept)
        lines.append(f'KEPT kinds: {dict(kinds)}')
        mods_kept = collections.Counter(k.split('.')[0] for k in kept)
        lines.append(f'KEPT modules: {dict(mods_kept)}')
    return '\n'.join(lines) + '\n'


def publish_github_status(repo: str, commit_sha: str, state: str, description: str, token: str | None = None) -> bool:
    """Publish commit status via gh api without logs or local paths."""
    # Description max length on GitHub API is 140 chars
    desc = description[:140]
    cmd = [
        'gh', 'api', f'repos/{repo}/statuses/{commit_sha}',
        '-f', f'state={state}',
        '-f', 'context=local-full-suite',
        '-f', f'description={desc}'
    ]
    env = os.environ.copy()
    if token:
        env['GH_TOKEN'] = token
    try:
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=30)
        return proc.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def file_plane_issue(project: str, name: str, description: str, external_source: str, external_id: str) -> bool:
    """File or deduplicate an issue in Plane via plane CLI."""
    data = json.dumps({'external_source': external_source, 'external_id': external_id})
    cmd = [
        'plane', 'issue', 'create',
        '-p', project,
        '-n', name,
        '--description-html', '<pre>' + html.escape(description) + '</pre>',
        '--data', data
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if proc.returncode == 0:
            return True
        if proc.returncode == 5:
            failure = json.loads(proc.stdout)
            if isinstance(failure, dict) and isinstance(failure.get('error'), dict) and failure['error'].get('http_status') == 409:
                found = subprocess.run(
                    ['plane', 'issue', 'get', '-p', project, '--external-source', external_source,
                     '--external-id', external_id], capture_output=True, text=True, timeout=30)
                payload = json.loads(found.stdout)
                issue = payload.get('data') if isinstance(payload, dict) else None
                return (found.returncode == 0 and isinstance(issue, dict) and payload.get('ok') is True
                        and issue.get('external_source') == external_source
                        and issue.get('external_id') == external_id)
        return False
    except (ValueError, TypeError):
        return False
    except (subprocess.SubprocessError, OSError):
        return False


def write_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + '.new')
    temporary.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def git_output(root: Path, *args: str) -> str:
    return subprocess.run(['git', *args], cwd=root, capture_output=True, text=True,
                          check=True, timeout=60).stdout.strip()


def prepare_worktree(root: Path, worktree: Path, commit: str) -> None:
    worktree.parent.mkdir(parents=True, exist_ok=True)
    if worktree.exists():
        common = (root / git_output(root, 'rev-parse', '--git-common-dir')).resolve()
        existing = (worktree / git_output(worktree, 'rev-parse', '--git-common-dir')).resolve()
        if common != existing or git_output(worktree, 'status', '--porcelain', '--untracked-files=all'):
            raise RuntimeError('nightly worktree is foreign or dirty; preserving it for inspection')
        git_output(worktree, 'checkout', '--detach', '--no-overwrite-ignore', commit)
    else:
        git_output(root, 'worktree', 'add', '--detach', str(worktree), commit)
    if git_output(worktree, 'rev-parse', 'HEAD') != commit:
        raise RuntimeError('nightly worktree does not match origin/main')


def execute_suite(worktree: Path, run_dir: Path, weight: int, jobs: int) -> dict[str, str]:
    log_path = run_dir / 'suite.log'
    command = ['ae-ci-exec', str(worktree), '--', 'python3', 'scripts/strict_suite.py', f'--jobs={jobs}', '--full']
    runner = shutil.which('ae-ci-exec')
    receipt = {
        'schema': 'agent-ee.nightly-execution/1', 'command': command,
        'commit': git_output(worktree, 'rev-parse', 'HEAD'),
        'tree': git_output(worktree, 'rev-parse', 'HEAD^{tree}'),
        'weight': weight, 'jobs': jobs, 'timeout_seconds': SUITE_TIMEOUT_SECONDS,
        'termination_grace_seconds': SUITE_STOP_GRACE_SECONDS,
        'nightly_runner': str(Path(__file__).resolve()),
        'nightly_runner_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'runner': runner,
        'runner_sha256': hashlib.sha256(Path(runner).read_bytes()).hexdigest() if runner else None,
        'started_at': datetime.now(timezone.utc).isoformat(),
    }
    write_json(run_dir / 'execution.json', receipt)
    try:
        with log_path.open('w', encoding='utf-8') as output:
            with subprocess.Popen(command, env=dict(os.environ, AE_CI_WEIGHT=str(weight), AE_CI_KEEP_TMP='1'),
                                  stdout=output, stderr=subprocess.STDOUT, text=True, start_new_session=True) as proc:
                try:
                    proc.wait(timeout=SUITE_TIMEOUT_SECONDS)
                except (subprocess.TimeoutExpired, KeyboardInterrupt, SystemExit):
                    stop_suite(proc)
                    receipt['exit_code'] = proc.returncode
                    raise
        receipt['exit_code'] = proc.returncode
        problems = parse_problems_from_log(log_path)
        if proc.returncode != (1 if problems else 0):
            raise RuntimeError('runner exit status disagrees with the strict-suite verdict')
        receipt['problem_count'] = len(problems)
        return problems
    finally:
        receipt['finished_at'] = datetime.now(timezone.utc).isoformat()
        if log_path.exists():
            content = log_path.read_bytes()
            receipt['suite_log_sha256'] = hashlib.sha256(content).hexdigest()
            receipt['image_ids'] = sorted(set(re.findall(
                r'ae-ci-exec: image (sha256:[0-9a-f]{64})', content.decode('utf-8', errors='replace'))))
        write_json(run_dir / 'execution.json', receipt)


def stop_suite(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=SUITE_STOP_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def report_run(state: dict, state_dir: Path, repo: str, skip_plane: bool, skip_gh_status: bool) -> int:
    """Resume unfinished reporting without repeating a completed native run."""
    run_dir = Path(state['last_run_dir'])
    comp = json.loads((run_dir / 'compare.json').read_text(encoding='utf-8'))
    done = state['reporting']
    if not done['plane'] and not skip_plane:
        done['plane'] = file_plane_issue(
            'AGENTEE', f"Nightly suite regressions on {state['last_commit'][:8]}",
            format_compare_summary(comp), 'agent-ee-nightly-suite', f"nightly-{state['last_commit']}")
        write_json(state_dir / 'state.json', state)
    if not done['github'] and not skip_gh_status:
        description = (f"strict-complete {state['last_commit']} {state['last_run_timestamp']} "
                       f"problems={len(comp['new'])} +{len(comp['added'])} "
                       f"~{len(comp.get('changed', []))} -{len(comp['fixed'])} ={len(comp['kept'])}")
        if comp.get('baseline'):
            description = (f"strict-complete {state['last_commit']} {state['last_run_timestamp']} "
                           f"problems={len(comp['new'])} baseline")
        done['github'] = publish_github_status(
            repo, state['last_commit'], 'failure' if comp['new'] else 'success', description)
        write_json(state_dir / 'state.json', state)
    failed = (not skip_plane and not done['plane']) or (not skip_gh_status and not done['github'])
    if failed:
        print('Nightly reporting incomplete; retained state will resume reporting without rerunning the suite.', file=sys.stderr)
    return 1 if failed else 0


def run_nightly_suite(
    root: Path,
    state_dir: Path = DEFAULT_STATE_DIR,
    weight: int = 2,
    jobs: int = 2,
    force: bool = False,
    worktree_dir: Path = DEFAULT_WORKTREE_DIR,
    skip_plane: bool = False,
    skip_gh_status: bool = False,
    repo: str = 'halbritt/agent-ee',
    initialize_baseline: bool = False,
) -> int:
    """Run moved main once; retain completed tests separately from reporting."""
    if weight < 1 or jobs < 1:
        raise ValueError('weight and jobs must be positive')
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / 'job.lock').open('a') as lock_file:
        origin_head, run_dir = None, None
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Another nightly suite run is active; exiting.')
            return 0
        try:
            state_file = state_dir / 'state.json'
            active_file = state_dir / 'active-run.json'
            if active_file.exists():
                raise RuntimeError('previous native attempt is unresolved; reconcile active-run.json before retrying')
            state = json.loads(state_file.read_text(encoding='utf-8')) if state_file.exists() else {}
            if not isinstance(state, dict) or (state and state.get('schema') != 'agent-ee.nightly-state/1'):
                raise RuntimeError('unversioned nightly state requires baseline reconciliation')
            if state and initialize_baseline:
                raise RuntimeError('baseline already initialized; refusing to replace its history')
            if not state and not initialize_baseline:
                raise RuntimeError('nightly state is missing; lead must select a reviewed --initialize-baseline run')
            if state and report_run(state, state_dir, repo, skip_plane, skip_gh_status):
                return 1
            git_output(root, 'fetch', 'origin', 'main')
            origin_head = git_output(root, 'rev-parse', '--verify', 'origin/main^{commit}')
            if not force and state.get('last_commit') == origin_head:
                return report_run(state, state_dir, repo, skip_plane, skip_gh_status)
            prepare_worktree(root, worktree_dir, origin_head)
            run_id = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
            run_dir = state_dir / 'runs' / f'{run_id}-{origin_head[:8]}'
            run_dir.mkdir(parents=True)
            write_json(active_file, {'commit': origin_head, 'run_dir': str(run_dir), 'started_at': run_id})
            if not skip_gh_status:
                publish_github_status(repo, origin_head, 'pending', 'Native full suite in progress; no complete verdict yet')
            print(f'Starting full suite run on {origin_head[:8]} (weight={weight}, jobs={jobs})...')
            new_problems = execute_suite(worktree_dir, run_dir, weight, jobs)
            finish_time = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
            comp = compare_problems(state.get('last_problems', {}), new_problems)
            comp['baseline'] = not state
            if comp['baseline']:
                for field in ('added', 'fixed', 'kept', 'changed'):
                    comp[field] = []
            write_json(run_dir / 'compare.json', comp)
            write_json(state_dir / 'latest-compare.json', comp)
            summary = format_compare_summary(comp)
            (run_dir / 'compare.txt').write_text(summary, encoding='utf-8')
            if regressions(comp):
                failures = {'commit': origin_head, 'timestamp': finish_time,
                            'added': {k: new_problems[k] for k in comp['added']},
                            'changed': {k: {'old': comp['old'][k], 'new': new_problems[k]}
                                        for k in comp.get('changed', [])}}
                write_json(run_dir / 'failures.json', failures)
                write_json(state_dir / 'latest-failures.json', failures)
            state = {
                'schema': 'agent-ee.nightly-state/1', 'last_commit': origin_head,
                'last_run_dir': str(run_dir), 'last_run_timestamp': finish_time,
                'last_problems': new_problems,
                'last_added_count': None if comp['baseline'] else len(comp['added']),
                'last_fixed_count': None if comp['baseline'] else len(comp['fixed']),
                'last_kept_count': None if comp['baseline'] else len(comp['kept']),
                'last_changed_count': None if comp['baseline'] else len(comp.get('changed', [])),
                'reporting': {'plane': not regressions(comp), 'github': False},
            }
            write_json(state_file, state)
            active_file.unlink()
            print(f'Full suite run completed on {origin_head[:8]}: {summary.strip()}')
            return report_run(state, state_dir, repo, skip_plane, skip_gh_status)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            failure = {'commit': origin_head, 'run_dir': str(run_dir) if run_dir else None,
                       'timestamp': datetime.now(timezone.utc).isoformat(), 'status': 'incomplete',
                       'error': str(error)}
            if origin_head and not skip_gh_status:
                failure['github_error_reported'] = publish_github_status(
                    repo, origin_head, 'error', 'Native full suite did not complete; inspect local attempt failure evidence')
            write_json(state_dir / 'latest-attempt-failure.json', failure)
            if run_dir:
                write_json(run_dir / 'attempt-failure.json', failure)
            print(f'Nightly suite failed: {error}', file=sys.stderr)
            return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Nightly Full Suite Runner (AGENTEE-614 Part 2)')
    subparsers = parser.add_subparsers(dest='subcommand')
    run_parser = subparsers.add_parser('run', help='Execute nightly full suite run')

    for p in (parser, run_parser):
        p.add_argument('--force', action='store_true', help='Force run even if origin/main has not moved')
        p.add_argument('--state-dir', default=str(DEFAULT_STATE_DIR), help='Directory for state and logs')
        p.add_argument('--worktree', default=str(DEFAULT_WORKTREE_DIR), help='Worktree path for test execution')
        p.add_argument('--weight', type=int, default=2, help='Admission weight (default: 2)')
        p.add_argument('--jobs', type=int, default=2, help='Parallel test jobs (default: 2)')
        p.add_argument('--skip-plane', action='store_true', help='Skip filing Plane issues')
        p.add_argument('--skip-gh-status', action='store_true', help='Skip updating GitHub commit status')
        p.add_argument('--repo', default='halbritt/agent-ee', help='GitHub repository name (owner/repo)')
        p.add_argument('--root', default='.', help='Repository root path')
        p.add_argument('--initialize-baseline', action='store_true',
                       help='Explicit first native run recording a baseline; refuses existing state')

    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    state_dir = Path(args.state_dir).expanduser().resolve()
    worktree_dir = Path(args.worktree).expanduser().resolve()

    return run_nightly_suite(
        root=root,
        state_dir=state_dir,
        weight=args.weight,
        jobs=args.jobs,
        force=args.force,
        worktree_dir=worktree_dir,
        skip_plane=args.skip_plane,
        skip_gh_status=args.skip_gh_status,
        repo=args.repo,
        initialize_baseline=args.initialize_baseline,
    )


if __name__ == '__main__':
    sys.exit(main())
