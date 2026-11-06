"""Real Git fixtures; executable doubles at native and reporting boundaries."""
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch


@contextmanager
def nightly_fixture():
    cache = Path.home() / '.cache/agent-ee-test-fixtures'
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='nightly-', dir=cache) as tmp:
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
        git('-C', str(root), 'add', 'owned.txt')
        git('-C', str(root), '-c', 'user.name=agent-ee test', '-c', 'user.email=test@invalid',
            'commit', '-q', '-m', 'owned fixture')
        git('-C', str(root), 'push', '-q', 'origin', 'HEAD:main')
        (base / 'report').write_text('strict suite passed: 1 modules, 1 tests, no skips\n')
        runner = bins / 'ae-ci-exec'
        runner.write_text('#!/bin/sh\n'
                          'printf "%s\\n" "$@" >> "$NIGHTLY_PROBE/runner-args"\n'
                          'printf "%s\\n" "$AE_CI_WEIGHT" >> "$NIGHTLY_PROBE/weights"\n'
                          'cat "$NIGHTLY_PROBE/report"\n'
                          'exit "${NIGHTLY_SUITE_EXIT:-0}"\n')
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
