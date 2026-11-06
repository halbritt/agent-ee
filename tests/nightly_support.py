"""Real Git fixtures; executable doubles at native and reporting boundaries."""
from contextlib import contextmanager, nullcontext
import os
from pathlib import Path
import subprocess
import sys
import shutil
import tempfile
from unittest.mock import patch


@contextmanager
def nightly_fixture():
    cache = Path.home() / '.cache/agent-ee-test-fixtures'
    cache.mkdir(parents=True, exist_ok=True)
    evidence = os.environ.get('AGENT_EE_EVIDENCE_DIR')
    if evidence:
        destination = Path(evidence) / 'nightly-fixtures'
        destination.mkdir(parents=True, exist_ok=True)
        workspace = nullcontext(tempfile.mkdtemp(prefix='nightly-', dir=destination))
    else:
        workspace = tempfile.TemporaryDirectory(prefix='nightly-', dir=cache)
    with workspace as tmp:
        base = Path(tmp)
        root, remote, bins = (base / name for name in ('source', 'remote', 'bin'))
        bins.mkdir()
        env = dict(os.environ, GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_NOSYSTEM='1')
        def git(*args):
            return subprocess.run(['git', *args], env=env, capture_output=True,
                                  text=True, check=True).stdout.strip()
        git('init', '--bare', '-q', str(remote))
        git('clone', '-q', str(remote), str(root))
        (root / 'owned.txt').write_text('owned nightly fixture\n')
        (root / 'scripts').mkdir()
        for name in ('nightly_full_suite.py', 'nightly_evidence.py'):
            shutil.copyfile(Path(__file__).resolve().parents[1] / 'scripts' / name, root / 'scripts' / name)
        git('-C', str(root), 'add', 'owned.txt', 'scripts')
        git('-C', str(root), '-c', 'user.name=agent-ee test', '-c', 'user.email=test@invalid',
            'commit', '-q', '-m', 'owned fixture')
        git('-C', str(root), 'push', '-q', 'origin', 'HEAD:main')
        (base / 'report').write_text('strict suite passed: 1 modules, 1 tests, no skips\n')
        runner = bins / 'ae-ci-exec'
        runner.write_text('#!' + sys.executable + '\n' + '''
from datetime import datetime, timezone
import hashlib, json, os, subprocess, sys, uuid
from pathlib import Path
base = Path(os.environ['NIGHTLY_PROBE'])
with (base / 'runner-args').open('a') as out:
    out.write('\\n'.join(sys.argv[1:]) + '\\n')
with (base / 'weights').open('a') as out:
    out.write(os.environ['AE_CI_WEIGHT'] + '\\n')
status = int(os.environ.get('NIGHTLY_SUITE_EXIT', '0'))
state = os.environ.get('NIGHTLY_START_STATE', 'started_or_uncertain')
print('ae-ci-exec: image sha256:' + 'a' * 64, file=sys.stderr)
if 'AE_CI_OUTCOME_FILE' in os.environ:
    token = str(uuid.uuid4())
    record = dict(schema='agent-ee.exec-outcome/1', attempt_id=token, token=token,
        complete=True, state=state, exit_code=status,
        created_at=datetime.now(timezone.utc).isoformat(),
        runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        supervisor_sha256=hashlib.sha256(Path(__file__).with_name('ae-ci-outcome.py').read_bytes()).hexdigest(),
        command_sha256=hashlib.sha256(json.dumps([str(Path(__file__).resolve()), *sys.argv[1:]]).encode()).hexdigest())
    record['finished_at'] = datetime.now(timezone.utc).isoformat()
    fault = os.environ.get('NIGHTLY_JOURNAL_FAULT')
    if fault == 'stale': record['created_at'] = '2000-01-01T00:00:00Z'
    if fault == 'future': record['finished_at'] = '2099-01-01T00:00:00Z'
    if fault in ('runner', 'supervisor', 'command'): record[fault + '_sha256'] = '0' * 64
    if fault == 'token': record['attempt_id'] = str(uuid.uuid4())
    if fault == 'incomplete': record['complete'] = False
    if fault == 'exit': record['exit_code'] = 0
    if fault == 'missing-fields': record.pop('finished_at')
    if fault == 'not-a-bool': record['complete'] = 'true'
    if fault != 'missing':
        path = Path(os.environ['AE_CI_OUTCOME_FILE'])
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as out:
            raw = 'not json' if fault == 'malformed' else json.dumps(record)
            if fault == 'duplicate-fields': raw = raw[:-1] + ', "state": "not_started"}'
            out.write(raw)
    if os.environ.get('NIGHTLY_CAPTURE_INPUTS'):
        native = Path(os.environ['AE_CI_EVIDENCE_DIR']) / 'native-inputs-owned'
        native.mkdir(parents=True)
        source = Path(sys.argv[1])
        def git(ref):
            return subprocess.check_output(['git', '-C', str(source), 'rev-parse', ref], text=True).strip()
        (native / 'native-inputs.json').write_text(json.dumps({
            'schema': 'agent-ee.native-inputs/1', 'status': 'prepared',
            'agent_ee': {'commit': git('HEAD'), 'tree': git('HEAD^{tree}'), 'clean': True},
            'runner': {'sha256': record['runner_sha256']}, 'image': 'sha256:' + 'a' * 64,
            'manifest_sha256': 'b' * 64}))
if state != 'not_started':
    print((base / 'report').read_text(), end='')
raise SystemExit(status)
''')
        (bins / 'ae-ci-outcome.py').write_text('# portable boundary double; no native execution\n')
        plane = bins / 'plane'
        plane.write_text('#!/bin/sh\n'
                         'printf "%s\\n" "$@" > "$NIGHTLY_PROBE/plane-args"\n'
                         'exit "${NIGHTLY_PLANE_EXIT:-0}"\n')
        gh = bins / 'gh'
        gh.write_text('#!/bin/sh\n'
                      'printf "%s\\n" "$@" > "$NIGHTLY_PROBE/gh-args"\n'
                      'printf invoked\\n >> "$NIGHTLY_PROBE/gh-calls"\n'
                      'exit "${NIGHTLY_GH_EXIT:-0}"\n')
        for command in (runner, plane, gh):
            command.chmod(0o755)
        with patch.dict(os.environ, dict(env, PATH=str(bins) + os.pathsep + env['PATH'],
                                        NIGHTLY_PROBE=str(base))):
            yield dict(base=base, root=root, state_dir=base / 'state', worktree_dir=base / 'worktree',
                       commit=git('-C', str(root), 'rev-parse', 'HEAD'), git=git)
