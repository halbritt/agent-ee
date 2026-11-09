"""Real Git fixtures; executable doubles at native and reporting boundaries."""
from contextlib import contextmanager, nullcontext
import hashlib
import json
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
        (base / 'owned-input').mkdir()
        (base / 'owned-input/owned.txt').write_text('owned finite input\n')
        manifest = base / 'input-manifest.json'
        manifest.write_text(json.dumps({'schema': 'agent-ee.native-inputs/1', 'inputs': [
            {'name': 'owned-input', 'env': 'AGENT_EE_ACQUISITION_INPUTS', 'path': str(base / 'owned-input'),
             'files': {'owned.txt': hashlib.sha256(b'owned finite input\n').hexdigest()}}]}, indent=2) + '\n')
        runner = bins / 'ae-ci-exec'
        # Double of the INSTALLED runner contract only: arguments, AE_CI_WEIGHT, an image
        # line on stderr, the command's output and its exit status. No companion program and
        # no private outcome journal exist in the installed runner, so none are modelled.
        runner.write_text('#!' + sys.executable + '\n' + '''
import os, sys
from pathlib import Path
base = Path(os.environ['NIGHTLY_PROBE'])
with (base / 'runner-args').open('a') as out:
    out.write('\\n'.join(sys.argv[1:]) + '\\n')
with (base / 'weights').open('a') as out:
    out.write(os.environ['AE_CI_WEIGHT'] + '\\n')
with (base / 'inputs-env').open('a') as out:
    out.write(repr((os.environ.get('AE_CI_INPUT_MANIFEST'), os.environ.get('AE_CI_EVIDENCE_DIR'))) + '\\n')
status = int(os.environ.get('NIGHTLY_SUITE_EXIT', '0'))
image = 'sha256:' + 'a' * 64
print('ae-ci-exec: image ' + image, file=sys.stderr)
if os.environ.get('NIGHTLY_KEPT_SCRATCH'):
    print('ae-ci-exec: kept scratch ' + os.environ['NIGHTLY_KEPT_SCRATCH'], file=sys.stderr)
if 'AE_CI_INPUT_MANIFEST' in os.environ:
    # Installed-runner input contract (U4 A): the runner-invoked helper prepares snapshots under
    # AE_CI_EVIDENCE_DIR, binds the resolved image into one receipt and the runner announces it.
    import hashlib, json, subprocess
    manifest = Path(os.environ['AE_CI_INPUT_MANIFEST'])
    evidence = Path(os.environ['AE_CI_EVIDENCE_DIR'])
    run = evidence / 'run-owned'
    (run / 'inputs').mkdir(parents=True)
    (run / 'output').mkdir()
    source = Path(sys.argv[1])
    def git(ref):
        return subprocess.check_output(['git', '-C', str(source), 'rev-parse', ref], text=True).strip()
    fault = os.environ.get('NIGHTLY_RECEIPT_FAULT')
    record = {'schema': 'agent-ee.native-inputs/1', 'status': 'failed' if fault == 'status' else 'prepared',
              'manifest': str(manifest),
              'manifest_sha256': '0' * 64 if fault == 'manifest' else hashlib.sha256(manifest.read_bytes()).hexdigest(),
              'image': 'sha256:' + 'b' * 64 if fault == 'image' else image,
              'agent_ee': {'path': str(source), 'commit': '0' * 40 if fault == 'commit' else git('HEAD'),
                           'tree': git('HEAD^{tree}'), 'clean': True},
              'runner': {'path': __file__, 'sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
              'preparer': {'path': 'native-inputs.py', 'sha256': 'c' * 64},
              'policy_sha256': 'd' * 64, 'module_env_sha256': 'e' * 64, 'output_directory': str(run / 'output'),
              'mount_plan_sha256': '1' * 64,
              'launch': ['docker', 'run', '--mount', 'type=bind,src=' + str(run / 'inputs/owned-input') + ',dst=/opt/agent-ee-inputs/owned-input,readonly'],
              'validated_at': '2026-11-09T00:00:00+00:00',
              'inputs': [{'name': 'owned-input', 'env': 'AGENT_EE_ACQUISITION_INPUTS', 'read_only': fault != 'writable',
                          'files': {'owned.txt': 'f' * 64}, 'snapshot': str(run / 'inputs/owned-input'),
                          'container_path': '/opt/agent-ee-inputs/owned-input', 'modules': ['*']}]}
    record['launch_sha256'] = '0' * 64 if fault == 'launch' else hashlib.sha256(json.dumps(record['launch']).encode()).hexdigest()
    if fault == 'unvalidated':
        del record['validated_at']
    receipt = run / 'native-inputs.json'
    receipt.write_text(json.dumps(record, indent=2) + '\\n')
    if fault != 'silent':
        print('ae-ci-exec: native input receipt ' + str(receipt), file=sys.stderr)
    if fault == 'twice':
        print('ae-ci-exec: native input receipt ' + str(receipt), file=sys.stderr)
elif os.environ.get('NIGHTLY_RECEIPT_FAULT') == 'unselected':
    print('ae-ci-exec: native input receipt /nonexistent/receipt.json', file=sys.stderr)
print((base / 'report').read_text(), end='')
raise SystemExit(status)
''')
        plane = bins / 'plane'
        plane.write_text('#!' + sys.executable + '\n' + '''
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
(Path(os.environ['NIGHTLY_PROBE']) / 'plane-args').write_text('\\n'.join(args) + '\\n')
status = int(os.environ.get('NIGHTLY_PLANE_EXIT', '0'))
if status:
    print(json.dumps({'ok': False, 'error': {'http_status': 400}}))
else:
    issue = json.loads(args[args.index('--data') + 1])
    issue['id'] = 'owned-reporting-issue'
    issue['assignees'] = ([args[args.index('--assignee') + 1]]
        if '--assignee' in args and not os.environ.get('NIGHTLY_PLANE_IGNORE_ASSIGNEE') else [])
    print(json.dumps({'ok': True, 'data': issue}))
raise SystemExit(status)
''')
        gh = bins / 'gh'
        gh.write_text('#!/bin/sh\n'
                      'printf "%s\\n" "$@" > "$NIGHTLY_PROBE/gh-args"\n'
                      'printf invoked\\n >> "$NIGHTLY_PROBE/gh-calls"\n'
                      'exit "${NIGHTLY_GH_EXIT:-0}"\n')
        for command in (runner, plane, gh):
            command.chmod(0o755)
        with patch.dict(os.environ, dict(env, PATH=str(bins) + os.pathsep + env['PATH'],
                                        NIGHTLY_PROBE=str(base))):
            yield dict(base=base, root=root, state_dir=base / 'state', worktree_dir=base / 'worktree', manifest=manifest,
                       commit=git('-C', str(root), 'rev-parse', 'HEAD'), git=git)
