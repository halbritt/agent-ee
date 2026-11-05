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
import json
import os
from pathlib import Path
import re
import subprocess
import sys


DEFAULT_STATE_DIR = Path.home() / '.local/share/agent-ee-full-suite'
DEFAULT_WORKTREE_DIR = Path.home() / '.cache/agent-worktrees/agent-ee/nightly-full-suite'


def parse_problems_from_log(log_path: Path | str) -> dict[str, str]:
    """Parse failed, skipped-refused, and missing-input tests from suite.log."""
    out = {}
    path = Path(log_path)
    if not path.exists():
        return out

    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            line = line.rstrip('\n')
            m = re.match(r'^(FAIL|ERROR): (\S+) \(([^)]+)\)(.*)$', line)
            if m:
                key = m.group(3) + (m.group(4).strip() and ' ' + m.group(4).strip() or '')
                out.setdefault(key, m.group(1))
                continue
            m = re.match(r'^SKIP refused in strict mode: ([^:]+): (.*)$', line)
            if m:
                out.setdefault(m.group(1), 'SKIP: ' + m.group(2)[:120])
                continue
            m = re.match(r'^::error title=required input ([^:]+) missing::(\S+) not executed', line)
            if m:
                out.setdefault(m.group(2), 'MISSING-INPUT ' + m.group(1))
    return out


def compare_problems(old: dict[str, str], new: dict[str, str]) -> dict:
    """Compare old problem set with new problem set."""
    old_set = set(old.keys())
    new_set = set(new.keys())
    gone = sorted(old_set - new_set)
    added = sorted(new_set - old_set)
    kept = sorted(old_set & new_set)
    return {
        'old': old,
        'new': new,
        'fixed': gone,
        'added': added,
        'kept': kept,
    }


def format_compare_summary(comp: dict) -> str:
    """Format human-readable comparison text matching baseline compare format."""
    old, new = comp['old'], comp['new']
    gone, added, kept = comp['fixed'], comp['added'], comp['kept']
    lines = [
        f'old {len(old)} new {len(new)} fixed {len(gone)} added {len(added)} kept {len(kept)}'
    ]
    if added:
        mods = collections.Counter(k.split('.')[0] for k in added)
        lines.append(f'ADDED by module: {dict(mods)}')
        for k in added:
            lines.append(f'  + {k} | {new.get(k, "FAIL")}')
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
        '--description', description,
        '--data', data
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return proc.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


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
) -> int:
    """Execute the nightly suite run lifecycle."""
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = state_dir / 'job.lock'
    lock_file = open(lock_path, 'w')
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print('Another nightly suite run is active; exiting.')
        return 0

    state_file = state_dir / 'state.json'
    state = {}
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, OSError):
            pass

    # Check origin/main
    subprocess.run(['git', 'fetch', 'origin', 'main'], cwd=root, capture_output=True, text=True)
    proc = subprocess.run(['git', 'rev-parse', 'origin/main'], cwd=root, capture_output=True, text=True)
    origin_head = proc.stdout.strip() if proc.returncode == 0 else ''
    if not origin_head:
        proc = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root, capture_output=True, text=True)
        origin_head = proc.stdout.strip()

    if not force and state.get('last_commit') == origin_head:
        print(f'origin/main ({origin_head[:8]}) has not moved since last run; skipping.')
        return 0

    # Ensure isolated worktree exists at origin/main
    worktree_dir.parent.mkdir(parents=True, exist_ok=True)
    if (worktree_dir / '.git').exists():
        subprocess.run(['git', 'fetch', 'origin', 'main'], cwd=worktree_dir, capture_output=True)
        subprocess.run(['git', 'checkout', '--detach', origin_head], cwd=worktree_dir, capture_output=True)
        subprocess.run(['git', 'reset', '--hard', origin_head], cwd=worktree_dir, capture_output=True)
        subprocess.run(['git', 'clean', '-fdx'], cwd=worktree_dir, capture_output=True)
    else:
        subprocess.run(['git', 'worktree', 'add', '--detach', str(worktree_dir), origin_head], cwd=root, capture_output=True)

    now = datetime.now(timezone.utc)
    run_id = now.strftime('%Y%m%d-%H%M%S')
    run_dir = state_dir / 'runs' / f'{run_id}-{origin_head[:8]}'
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / 'suite.log'

    print(f'Starting full suite run on {origin_head[:8]} (weight={weight}, jobs={jobs})...')
    cmd = ['ae-ci-exec', str(worktree_dir), '--', 'python3', 'scripts/strict_suite.py', f'--jobs={jobs}', '--full']
    env = os.environ.copy()
    env['AE_CI_WEIGHT'] = str(weight)

    with open(log_path, 'w', encoding='utf-8') as log_out:
        proc = subprocess.run(cmd, env=env, stdout=log_out, stderr=subprocess.STDOUT, text=True)

    finish_time = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    new_problems = parse_problems_from_log(log_path)
    old_problems = state.get('last_problems', {})
    comp = compare_problems(old_problems, new_problems)

    (run_dir / 'compare.json').write_text(json.dumps(comp, indent=2) + '\n', encoding='utf-8')
    summary_text = format_compare_summary(comp)
    (run_dir / 'compare.txt').write_text(summary_text, encoding='utf-8')
    (state_dir / 'latest-compare.json').write_text(json.dumps(comp, indent=2) + '\n', encoding='utf-8')

    if comp['added']:
        failures = {
            'commit': origin_head,
            'timestamp': finish_time,
            'added': {k: new_problems.get(k, '') for k in comp['added']},
        }
        (state_dir / 'latest-failures.json').write_text(json.dumps(failures, indent=2) + '\n', encoding='utf-8')
        (run_dir / 'failures.json').write_text(json.dumps(failures, indent=2) + '\n', encoding='utf-8')

        if not skip_plane:
            file_plane_issue(
                'AGENTEE',
                f'Nightly suite regressions on {origin_head[:8]}',
                summary_text,
                'agent-ee-nightly-suite',
                f'nightly-{origin_head}'
            )

    if not skip_gh_status:
        state_status = 'failure' if comp['added'] else 'success'
        desc = f"{finish_time}: {len(new_problems)} problems (+{len(comp['added'])}, -{len(comp['fixed'])}, ={len(comp['kept'])})"
        publish_github_status(repo, origin_head, state_status, desc)

    state_data = {
        'last_commit': origin_head,
        'last_run_timestamp': finish_time,
        'last_problems': new_problems,
        'last_added_count': len(comp['added']),
        'last_fixed_count': len(comp['fixed']),
        'last_kept_count': len(comp['kept']),
    }
    state_file.write_text(json.dumps(state_data, indent=2) + '\n', encoding='utf-8')
    print(f'Full suite run completed on {origin_head[:8]}: {summary_text.strip()}')
    return 0


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
    )


if __name__ == '__main__':
    sys.exit(main())
