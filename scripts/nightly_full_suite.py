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
import uuid

import nightly_evidence


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


def compare_problems(old: dict[str, str], new: dict[str, str], unexecuted: dict[str, str] | None = None) -> dict:
    """Keep prior identities unknown when a module's setup prevented execution."""
    old = {**(unexecuted or {}), **old}
    old_set = set(old.keys())
    new_set = set(new.keys())
    refused = {key for key, kind in new.items() if '.' not in key and kind.startswith(('MISSING-INPUT ', 'MODULE:'))}
    unknown = {key: old[key] for key in old_set - new_set if '.' in key and key.split('.')[0] in refused}
    gone = sorted(old_set - new_set - set(unknown))
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
        'unexecuted': unknown,
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
    if unknown := comp.get('unexecuted'):
        lines.append(f'UNEXECUTED prior problems: {len(unknown)} (status unknown)')
        lines += [f'  ? {key} | {kind}' for key, kind in sorted(unknown.items())]
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


def plane_assignee_uuid(value: str) -> str:
    """Normalize a nonzero assignee UUID without resolving an account name."""
    try:
        selected = uuid.UUID(value)
        if not selected.int:
            raise ValueError('nil UUID')
    except ValueError as error:
        raise ValueError('Plane assignee must be a nonzero user UUID') from error
    return str(selected)


def file_plane_issue(project: str, name: str, description: str, external_source: str, external_id: str,
                     *, assignee: str | None = None) -> bool:
    """Confirm creation or the same conflict ID, including requested assignment."""
    data = json.dumps({'external_source': external_source, 'external_id': external_id})
    cmd = [
        'plane', 'issue', 'create',
        '-p', project,
        '--name', name,
        '--description-html', '<pre>' + html.escape(description) + '</pre>',
        '--data', data
    ]
    try:
        if assignee is not None:
            assignee = plane_assignee_uuid(assignee)
            cmd += ['--assignee', assignee]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        conflict_id = None
        if proc.returncode == 5:
            failure = json.loads(proc.stdout, object_pairs_hook=unique_fields)
            error = failure.get('error') if isinstance(failure, dict) else None
            details = error.get('details') if isinstance(error, dict) else None
            if (not isinstance(error, dict) or failure.get('ok') is not False
                    or error.get('http_status') != 409 or not isinstance(details, dict)
                    or not isinstance(details.get('id'), str) or not details['id']):
                return False
            conflict_id = details['id']
            proc = subprocess.run(
                ['plane', 'issue', 'get', '-p', project, '--external-source', external_source,
                 '--external-id', external_id], capture_output=True, text=True, timeout=30)
        if proc.returncode != 0:
            return False
        payload = json.loads(proc.stdout, object_pairs_hook=unique_fields)
        issue = payload.get('data') if isinstance(payload, dict) else None
        if (not isinstance(issue, dict) or payload.get('ok') is not True
                or not isinstance(issue.get('id'), str) or not issue['id']
                or (conflict_id is not None and issue['id'] != conflict_id)
                or issue.get('external_source') != external_source or issue.get('external_id') != external_id):
            return False
        assigned = issue.get('assignees')
        return assignee is None or (isinstance(assigned, list) and any(
            (entry.get('id') if isinstance(entry, dict) else entry) == assignee for entry in assigned))
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
        if common != existing or git_output(worktree, 'status', '--porcelain', '--untracked-files=all', '--ignored=matching'):
            raise RuntimeError('nightly worktree is foreign or dirty; preserving it for inspection')
        git_output(worktree, 'checkout', '--detach', '--no-overwrite-ignore', commit)
    else:
        git_output(root, 'worktree', 'add', '--detach', str(worktree), commit)
    if git_output(worktree, 'rev-parse', 'HEAD') != commit:
        raise RuntimeError('nightly worktree does not match origin/main')


def unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate record field: {key}')
        result[key] = value
    return result


def completed_problems(log_path: Path, exit_code: int) -> dict[str, str]:
    """Validate the runner's observed exit against one complete strict verdict."""
    try:
        problems = parse_problems_from_log(log_path)
    except (OSError, ValueError) as error:
        raise RuntimeError(f'no complete strict-suite verdict: {error}') from error
    if exit_code != (1 if problems else 0):
        raise RuntimeError(f'runner exit status {exit_code} disagrees with the '
                           f'strict-suite verdict of {len(problems)} problem(s)')
    return problems


def execute_suite(worktree: Path, run_dir: Path, weight: int, jobs: int,
                  attempt_id: str, runner: str) -> dict[str, str]:
    """Run the installed runner once; a complete verdict and agreeing exit establish completion.

    The installed ae-ci-exec offers an ordinary command/exit/log contract: it
    prints its image identity and kept-scratch path to the log and returns the
    command's exit status. No companion program or private outcome journal
    exists, so completion is established only by the retained log's single
    complete strict verdict agreeing with the observed exit. Anything else is
    recorded as incomplete with its reason.
    """
    log_path = run_dir / 'suite.log'
    resolved = shutil.which(runner)
    if not resolved:
        raise RuntimeError(f'native runner not found: {runner}')
    runner_path = Path(resolved).resolve()
    command = [str(runner_path), str(worktree), '--', 'env', f'AGENT_EE_NIGHTLY_ATTEMPT_ID={attempt_id}',
               'python3', 'scripts/strict_suite.py', f'--jobs={jobs}', '--full']
    receipt = {
        'schema': 'agent-ee.nightly-execution/1', 'command': command,
        'nightly_attempt_id': attempt_id,
        'worktree': str(worktree),
        'commit': git_output(worktree, 'rev-parse', 'HEAD'),
        'tree': git_output(worktree, 'rev-parse', 'HEAD^{tree}'),
        'weight': weight, 'jobs': jobs, 'timeout_seconds': SUITE_TIMEOUT_SECONDS,
        'termination_grace_seconds': SUITE_STOP_GRACE_SECONDS,
        'nightly_runner': str(Path(__file__).resolve()),
        'nightly_runner_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'nightly_evidence_sha256': nightly_evidence.digest(Path(nightly_evidence.__file__)),
        'runner': str(runner_path),
        'runner_sha256': hashlib.sha256(runner_path.read_bytes()).hexdigest(),
        'command_sha256': hashlib.sha256(json.dumps(command).encode()).hexdigest(),
        'scratch_root': str(Path(os.environ.get('AE_CI_EXEC_HOME', str(Path.home() / '.cache/ae-ci-exec'))) / 'tmp'),
        'started_at': datetime.now(timezone.utc).isoformat(),
        'completion': 'started',
    }
    write_json(run_dir / 'execution.json', receipt)
    try:
        if run_dir.resolve().is_relative_to(worktree.resolve()):
            raise RuntimeError('nightly evidence must stay outside the writable native worktree')
        if log_path.exists() or log_path.is_symlink():
            raise RuntimeError('suite log already exists before this attempt')
        env = dict(os.environ, AE_CI_WEIGHT=str(weight), AE_CI_KEEP_TMP='1')
        with log_path.open('w', encoding='utf-8') as output:
            with subprocess.Popen(command, env=env,
                                  stdout=output, stderr=subprocess.STDOUT, text=True, start_new_session=True) as proc:
                try:
                    proc.wait(timeout=SUITE_TIMEOUT_SECONDS)
                except (subprocess.TimeoutExpired, KeyboardInterrupt, SystemExit) as interruption:
                    stop_suite(proc)
                    receipt['exit_code'] = proc.returncode
                    receipt['termination'] = ('timeout' if isinstance(interruption, subprocess.TimeoutExpired)
                                              else 'interrupted')
                    receipt['incomplete_reason'] = f'native attempt {receipt["termination"]} before a verdict'
                    raise
        receipt['exit_code'] = proc.returncode
        receipt['finished_at'] = datetime.now(timezone.utc).isoformat()
        try:
            problems = completed_problems(log_path, proc.returncode)
        except RuntimeError as error:
            receipt['incomplete_reason'] = str(error)
            raise
        receipt['problem_count'] = len(problems)
        receipt['full_suite'] = True
        receipt['completion'] = 'complete'
        return problems
    finally:
        receipt.setdefault('finished_at', datetime.now(timezone.utc).isoformat())
        if receipt['completion'] != 'complete':
            receipt['completion'] = 'incomplete'
            receipt.setdefault('incomplete_reason', 'native attempt did not reach completion validation')
        if log_path.exists():
            content = log_path.read_bytes()
            receipt['suite_log_sha256'] = hashlib.sha256(content).hexdigest()
            receipt['image_ids'] = sorted(set(re.findall(
                r'ae-ci-exec: image (sha256:[0-9a-f]{64})', content.decode('utf-8', errors='replace'))))
            receipt['retained_scratch'] = sorted(set(re.findall(
                r'^ae-ci-exec: kept scratch (.+)$', content.decode('utf-8', errors='replace'), re.MULTILINE)))
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


def import_baseline(root: Path, state_dir: Path, selection_file: Path) -> int:
    """Record one reviewed gate's original completion; never execute or publish.

    The installed runner writes no native input receipt the controller could
    read, so the selection's input_manifest_sha256 is recorded as the reviewer's
    explicit identity rather than cross-checked against a companion record.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / 'job.lock').open('a') as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            if (state_dir / 'state.json').exists() or (state_dir / 'active-run.json').exists():
                raise ValueError('baseline import requires uninitialized state and no active attempt')
            selection = json.loads(selection_file.read_text(), object_pairs_hook=unique_fields)
            if (selection.get('schema') != 'agent-ee.baseline-import/1'
                    or not isinstance(selection.get('review_reference'), str) or not selection['review_reference'].strip()):
                raise ValueError('baseline import needs an explicit selected review reference')
            for field, length in (('commit', 40), ('tree', 40), ('output_manifest_sha256', 64), ('input_manifest_sha256', 64)):
                if not isinstance(selection.get(field), str) or not re.fullmatch('[0-9a-f]{' + str(length) + '}', selection[field]):
                    raise ValueError(f'baseline import needs an exact {field}')
            git_output(root, 'fetch', 'origin', 'main')
            if (git_output(root, 'rev-parse', 'origin/main^{commit}') != selection['commit']
                    or git_output(root, 'rev-parse', selection['commit'] + '^{tree}') != selection['tree']):
                raise ValueError('selected baseline commit/tree does not match intended main')
            gate = Path(selection['gate_directory'])
            if not gate.is_absolute() or gate.is_symlink() or state_dir.resolve().is_relative_to(gate.resolve()):
                raise ValueError('gate must be an independent retained absolute directory')
            nightly_evidence.verify_manifest(gate, selection['output_manifest_sha256'])
            receipt = json.loads((gate / 'execution.json').read_text(), object_pairs_hook=unique_fields)
            if (receipt.get('schema') != 'agent-ee.nightly-execution/1' or receipt.get('full_suite') is not True
                    or receipt.get('completion') != 'complete'
                    or receipt.get('commit') != selection['commit'] or receipt.get('tree') != selection['tree']
                    or not receipt.get('nightly_runner_sha256') or not receipt.get('runner_sha256')
                    or not receipt.get('image_ids')):
                raise ValueError('gate lacks complete source/runtime lineage')
            for name, field in (('nightly_full_suite.py', 'nightly_runner_sha256'),
                                ('nightly_evidence.py', 'nightly_evidence_sha256')):
                source = subprocess.run(['git', 'show', selection['commit'] + ':scripts/' + name],
                                        cwd=root, capture_output=True, check=True, timeout=60).stdout
                if hashlib.sha256(source).hexdigest() != receipt.get(field):
                    raise ValueError('gate controller differs from its selected source commit')
            log = gate / 'suite.log'
            if nightly_evidence.digest(log) != receipt.get('suite_log_sha256'):
                raise ValueError('gate log hash differs from its execution receipt')
            problems = parse_problems_from_log(log)
            if receipt.get('problem_count') != len(problems) or receipt.get('exit_code') != (1 if problems else 0):
                raise ValueError('gate verdict/count/exit disagree')
            run_dir = state_dir / 'runs' / ('import-' + str(uuid.uuid4()))
            run_dir.mkdir(parents=True)
            comp = compare_problems({}, problems)
            comp.update(baseline=True, added=[], fixed=[], kept=[], changed=[])
            write_json(run_dir / 'compare.json', comp)
            write_json(run_dir / 'baseline-origin.json', {**selection,
                'original_started_at': receipt['started_at'], 'original_finished_at': receipt['finished_at'],
                'original_attempt_id': receipt['nightly_attempt_id'],
                'imported_at': datetime.now(timezone.utc).isoformat(), 'native_execution': False,
                'external_publication': False})
            nightly_evidence.register_run(state_dir, run_dir, receipt['nightly_attempt_id'], state='imported')
            write_json(state_dir / 'latest-compare.json', comp)
            write_json(state_dir / 'state.json', {
                'schema': 'agent-ee.nightly-state/1', 'last_commit': selection['commit'],
                'last_run_dir': str(run_dir), 'last_run_timestamp': receipt['finished_at'],
                'last_problems': problems, 'unexecuted_problems': {},
                'last_added_count': None, 'last_fixed_count': None, 'last_kept_count': None, 'last_changed_count': None,
                'reporting': {'plane': True, 'github': True},
                'reporting_owner': 'original reviewed gate; import never publishes or refreshes statuses'})
            return 0
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
            print(f'Baseline import refused: {error}', file=sys.stderr)
            return 1


def qualify_gate(root: Path, run_dir: Path, commit: str, tree: str, runner: str,
                 weight: int, jobs: int, repo: str, skip_gh_status: bool) -> int:
    """Capture one candidate full gate before landing; baseline import is separate."""
    created = False
    try:
        if (weight < 1 or jobs < 1 or git_output(root, 'rev-parse', 'HEAD') != commit
                or git_output(root, 'rev-parse', 'HEAD^{tree}') != tree
                or git_output(root, 'status', '--porcelain', '--untracked-files=all', '--ignored=matching')):
            raise ValueError('full gate needs its exact clean candidate commit/tree')
        if run_dir.is_relative_to(root) or root.is_relative_to(run_dir):
            raise ValueError('gate evidence must be independent of the writable checkout')
        run_dir.mkdir(parents=True, exist_ok=False)
        created = True
        attempt = str(uuid.uuid4())
        write_json(run_dir / 'attempt.json', {'schema': 'agent-ee.nightly-attempt/1',
            'attempt_id': attempt, 'commit': commit, 'tree': tree, 'run_dir': str(run_dir),
            'started_at': datetime.now(timezone.utc).isoformat(), 'purpose': 'pre-landing full qualification'})
        if not skip_gh_status:
            publish_github_status(repo, commit, 'pending', 'Native full qualification in progress; no complete verdict yet')
        problems = execute_suite(root, run_dir, weight, jobs, attempt, runner)
        receipt = json.loads((run_dir / 'execution.json').read_text())
        write_json(run_dir / 'problem-inventory.json', {'commit': commit, 'tree': tree, 'problems': problems})
        reporting = {'required': not skip_gh_status, 'published': False}
        if not skip_gh_status:
            reporting['published'] = publish_github_status(repo, commit, 'failure' if problems else 'success',
                f"strict-complete {commit} {receipt['finished_at']} problems={len(problems)} gate")
        write_json(run_dir / 'reporting.json', reporting)
        nightly_evidence.write_manifest(run_dir)
        return 1 if problems or (reporting['required'] and not reporting['published']) else 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        if created:
            write_json(run_dir / 'attempt-failure.json', {'error': str(error), 'status': 'incomplete',
                'timestamp': datetime.now(timezone.utc).isoformat()})
        print(f'Full qualification failed: {error}', file=sys.stderr)
        return 1


def report_run(state: dict, state_dir: Path, repo: str, skip_plane: bool, skip_gh_status: bool,
               plane_assignee: str | None = None) -> int:
    """Resume unfinished reporting without repeating a completed native run."""
    run_dir = Path(state['last_run_dir'])
    comp = json.loads((run_dir / 'compare.json').read_text(encoding='utf-8'))
    done = state['reporting']
    if not done['plane'] and not skip_plane:
        done['plane'] = file_plane_issue(
            'AGENTEE', f"Nightly suite regressions on {state['last_commit'][:8]}",
            format_compare_summary(comp), 'agent-ee-nightly-suite', f"nightly-{state['last_commit']}",
            assignee=plane_assignee)
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


def observed_exit(run_dir: Path) -> bool:
    """A normal runner exit permits retry; death by signal leaves cleanup uncertain."""
    try:
        receipt = json.loads((run_dir / 'execution.json').read_text(), object_pairs_hook=unique_fields)
    except (OSError, ValueError):
        return False
    return (isinstance(receipt, dict) and type(receipt.get('exit_code')) is int
            and receipt['exit_code'] >= 0)


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
    runner: str = 'ae-ci-exec',
    fresh_worktree: bool = False,
    max_retained_runs: int | None = None,
    max_retained_bytes: int | None = None,
    plane_assignee: str | None = None,
) -> int:
    """Run moved main once; retain completed tests separately from reporting.

    An attempt whose runner exited normally is finalized as incomplete and
    cleared, so a later invocation may attempt the commit again. A missing exit
    or death by signal leaves cleanup uncertain and retains the marker for
    inspection. A timeout can retry only when the runner's TERM cleanup returned.
    """
    if weight < 1 or jobs < 1:
        raise ValueError('weight and jobs must be positive')
    if plane_assignee is not None:
        plane_assignee = plane_assignee_uuid(plane_assignee)
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
                raise RuntimeError('previous native attempt is unresolved (controller did not observe its exit); '
                                   'reconcile active-run.json before retrying')
            state = json.loads(state_file.read_text(encoding='utf-8')) if state_file.exists() else {}
            if not isinstance(state, dict) or (state and state.get('schema') != 'agent-ee.nightly-state/1'):
                raise RuntimeError('unversioned nightly state requires baseline reconciliation')
            if state and initialize_baseline:
                raise RuntimeError('baseline already initialized; refusing to replace its history')
            if not state and not initialize_baseline:
                raise RuntimeError('nightly state is missing; lead must select a reviewed --initialize-baseline run')
            if state and report_run(state, state_dir, repo, skip_plane, skip_gh_status, plane_assignee):
                return 1
            git_output(root, 'fetch', 'origin', 'main')
            origin_head = git_output(root, 'rev-parse', '--verify', 'origin/main^{commit}')
            if not force and state.get('last_commit') == origin_head:
                return report_run(state, state_dir, repo, skip_plane, skip_gh_status, plane_assignee)
            nightly_evidence.guard_retention(state_dir, max_retained_runs, max_retained_bytes)
            run_id = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
            selected_worktree = worktree_dir / run_id if fresh_worktree else worktree_dir
            prepare_worktree(root, selected_worktree, origin_head)
            run_dir = state_dir / 'runs' / f'{run_id}-{origin_head[:8]}'
            run_dir.mkdir(parents=True)
            attempt_id = str(uuid.uuid4())
            active = {'schema': 'agent-ee.nightly-attempt/1', 'attempt_id': attempt_id,
                      'commit': origin_head, 'tree': git_output(selected_worktree, 'rev-parse', 'HEAD^{tree}'),
                      'worktree': str(selected_worktree),
                      'run_dir': str(run_dir), 'started_at': datetime.now(timezone.utc).isoformat()}
            write_json(run_dir / 'attempt.json', active)
            nightly_evidence.register_run(state_dir, run_dir, attempt_id, selected_worktree)
            write_json(active_file, active)
            if not skip_gh_status:
                publish_github_status(repo, origin_head, 'pending', 'Native full suite in progress; no complete verdict yet')
            print(f'Starting full suite run on {origin_head[:8]} (weight={weight}, jobs={jobs})...')
            new_problems = execute_suite(selected_worktree, run_dir, weight, jobs, attempt_id, runner)
            finish_time = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
            comp = compare_problems(state.get('last_problems', {}), new_problems, state.get('unexecuted_problems', {}))
            comp['baseline'] = not state
            if comp['baseline']:
                for field in ('added', 'fixed', 'kept', 'changed'):
                    comp[field] = []
            write_json(run_dir / 'compare.json', comp)
            write_json(state_dir / 'latest-compare.json', comp)
            summary = format_compare_summary(comp)
            (run_dir / 'compare.txt').write_text(summary, encoding='utf-8')
            if regressions(comp) or comp['unexecuted']:
                failures = {'commit': origin_head, 'timestamp': finish_time,
                            'added': {k: new_problems[k] for k in comp['added']},
                            'changed': {k: {'old': comp['old'][k], 'new': new_problems[k]}
                                        for k in comp.get('changed', [])},
                            'unexecuted': comp['unexecuted']}
                write_json(run_dir / 'failures.json', failures)
                write_json(state_dir / 'latest-failures.json', failures)
            state = {
                'schema': 'agent-ee.nightly-state/1', 'last_commit': origin_head,
                'last_run_dir': str(run_dir), 'last_run_timestamp': finish_time,
                'last_problems': new_problems,
                'unexecuted_problems': comp['unexecuted'],
                'last_added_count': None if comp['baseline'] else len(comp['added']),
                'last_fixed_count': None if comp['baseline'] else len(comp['fixed']),
                'last_kept_count': None if comp['baseline'] else len(comp['kept']),
                'last_changed_count': None if comp['baseline'] else len(comp.get('changed', [])),
                'reporting': {'plane': not regressions(comp), 'github': False},
            }
            write_json(run_dir / 'retention.json', {
                'schema': 'agent-ee.nightly-retention/1', 'status': 'retained-awaiting-lead-release',
                'worktree': str(selected_worktree),
                'max_retained_runs': max_retained_runs, 'max_retained_bytes': max_retained_bytes,
                'automatic_deletion': False})
            nightly_evidence.write_manifest(run_dir)
            nightly_evidence.update_run(state_dir, run_dir, 'complete')
            write_json(state_file, state)
            active_file.unlink()
            print(f'Full suite run completed on {origin_head[:8]}: {summary.strip()}')
            return report_run(state, state_dir, repo, skip_plane, skip_gh_status, plane_assignee)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            failure = {'commit': origin_head, 'run_dir': str(run_dir) if run_dir else None,
                       'timestamp': datetime.now(timezone.utc).isoformat(), 'status': 'incomplete',
                       'error': str(error)}
            if run_dir and active_file.exists():
                # A normal runner exit includes its cleanup. A killed runner may leave native
                # work alive, so its marker remains unresolved even though wait() returned.
                failure['exit_observed'] = observed_exit(run_dir)
                if failure['exit_observed']:
                    write_json(run_dir / 'reconciliation.json', {
                        'reason': 'exit_observed_incomplete', 'run_dir': str(run_dir),
                        'reconciled_at': datetime.now(timezone.utc).isoformat()})
                    active_file.unlink()
            if origin_head and not skip_gh_status:
                failure['github_error_reported'] = publish_github_status(
                    repo, origin_head, 'error', 'Native full suite did not complete; inspect local attempt failure evidence')
            write_json(state_dir / 'latest-attempt-failure.json', failure)
            if run_dir:
                write_json(run_dir / 'attempt-failure.json', failure)
                try:
                    nightly_evidence.update_run(state_dir, run_dir, 'incomplete')
                except (OSError, ValueError, KeyError, TypeError) as index_error:
                    failure['retention_index_error'] = str(index_error)
                    write_json(state_dir / 'latest-attempt-failure.json', failure)
                    write_json(run_dir / 'attempt-failure.json', failure)
            print(f'Nightly suite failed: {error}', file=sys.stderr)
            return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Nightly Full Suite Runner (AGENTEE-614 Part 2)')
    subparsers = parser.add_subparsers(dest='subcommand')
    run_parser = subparsers.add_parser('run', help='Execute nightly full suite run')
    import_parser = subparsers.add_parser('import-baseline', help='Import a reviewed retained full gate without native work')
    import_parser.add_argument('--root', required=True)
    import_parser.add_argument('--state-dir', required=True)
    import_parser.add_argument('--selection', required=True, help='Explicit agent-ee.baseline-import/1 JSON')
    gate_parser = subparsers.add_parser('qualify', help='Execute one exact full gate without initializing nightly state')
    gate_parser.add_argument('--root', required=True)
    gate_parser.add_argument('--commit', required=True)
    gate_parser.add_argument('--tree', required=True)
    gate_parser.add_argument('--evidence-dir', required=True)
    gate_parser.add_argument('--runner', required=True)
    gate_parser.add_argument('--weight', type=int, default=2)
    gate_parser.add_argument('--jobs', type=int, default=2)
    gate_parser.add_argument('--skip-gh-status', action='store_true')
    gate_parser.add_argument('--repo', default='halbritt/agent-ee')

    for p in (parser, run_parser):
        p.add_argument('--force', action='store_true', help='Force run even if origin/main has not moved')
        p.add_argument('--state-dir', default=str(DEFAULT_STATE_DIR), help='Directory for state and logs')
        p.add_argument('--worktree', default=str(DEFAULT_WORKTREE_DIR), help='Worktree path for test execution')
        p.add_argument('--weight', type=int, default=2, help='Admission weight (default: 2)')
        p.add_argument('--jobs', type=int, default=2, help='Parallel test jobs (default: 2)')
        p.add_argument('--skip-plane', action='store_true', help='Skip filing Plane issues')
        # A subparser default must not erase an assignment supplied before "run".
        p.add_argument('--plane-assignee', type=plane_assignee_uuid, default=argparse.SUPPRESS,
                       help='Explicit Plane assignee user UUID; omission leaves assignment unspecified')
        p.add_argument('--skip-gh-status', action='store_true', help='Skip updating GitHub commit status')
        p.add_argument('--repo', default='halbritt/agent-ee', help='GitHub repository name (owner/repo)')
        p.add_argument('--root', default='.', help='Repository root path')
        p.add_argument('--initialize-baseline', action='store_true',
                       help='Explicit first native run recording a baseline; refuses existing state')
        p.add_argument('--runner', default='ae-ci-exec', help='Reviewed ae-ci-exec executable path')
        p.add_argument('--fresh-worktree', action='store_true', help='Use a new retained worktree for each native attempt')
        p.add_argument('--max-retained-runs', type=int, default=None,
                       help='Optional: refuse a new native attempt at this retained run count (default: no limit)')
        p.add_argument('--max-retained-bytes', type=int, default=None,
                       help='Optional: refuse a new native attempt at this retained byte count; no automatic deletion')

    args = parser.parse_args(argv)
    plane_assignee = getattr(args, 'plane_assignee', None)
    if plane_assignee is not None and args.subcommand in ('import-baseline', 'qualify'):
        parser.error('--plane-assignee applies only to nightly run reporting')
    root = Path(args.root).resolve()
    if args.subcommand == 'import-baseline':
        return import_baseline(root, Path(args.state_dir).expanduser().resolve(), Path(args.selection))
    if args.subcommand == 'qualify':
        return qualify_gate(root, Path(args.evidence_dir).expanduser().resolve(), args.commit, args.tree,
                            args.runner, args.weight, args.jobs, args.repo, args.skip_gh_status)
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
        runner=args.runner,
        fresh_worktree=args.fresh_worktree,
        max_retained_runs=args.max_retained_runs,
        max_retained_bytes=args.max_retained_bytes,
        plane_assignee=plane_assignee,
    )


if __name__ == '__main__':
    sys.exit(main())
