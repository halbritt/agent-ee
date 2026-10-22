"""CI container init contract: tini as PID 1 reaps, forwards signals and the exit status.

The image sets `ENTRYPOINT ["/usr/bin/tini", "--"]` instead of `docker run --init`
(whose /usr/sbin/docker-init bind mount is a /usr submount that the stage-receipt
refusal rejects). Behaviour is proven with the real tini binary run as a subreaper
(`-s`), which is the role PID 1 plays in the container.
"""
import os
import select
import shutil
import signal
import subprocess
import sys
import textwrap
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TIMEOUT = 20

REAP_CHILD = textwrap.dedent('''
    import os, sys, time
    exit_code, deadline_s = int(sys.argv[1]), float(sys.argv[2])
    # Orphans: a middle process forks grandchildren and exits at once, so they are
    # reparented to the init (the subreaper). Some exit immediately, one lingers.
    # Each grandchild reports its PID first; this observer never waits on them.
    r, w = os.pipe()
    middle = os.fork()
    if middle == 0:
        os.close(r)
        for delay in (0, 0, 0.4):
            if os.fork() == 0:
                os.write(w, b'%d\\n' % os.getpid())
                time.sleep(delay)
                os._exit(0)
        os._exit(0)
    os.close(w)
    os.waitpid(middle, 0)
    with os.fdopen(r) as pipe:
        orphans = [int(line) for line in pipe]     # EOF: every grandchild has closed w
    assert len(orphans) == 3, orphans
    # Gone from /proc means exited AND reaped (a live or zombie entry still exists).
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        remaining = [p for p in orphans if os.path.exists(f'/proc/{p}')]
        if not remaining:
            sys.exit(exit_code)
        time.sleep(0.05)
    print('unreaped:', remaining, flush=True)
    sys.exit(3)
''')

# Subreaper that never waits on adopted orphans: the fixture must fail under it.
NON_REAPING_INIT = textwrap.dedent('''
    import ctypes, os, subprocess, sys
    ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0)    # PR_SET_CHILD_SUBREAPER
    child = subprocess.run([sys.executable, '-c', sys.argv[1], *sys.argv[2:]])
    sys.exit(child.returncode)
''')

SIGNAL_CHILD = textwrap.dedent('''
    import os, signal, sys, time
    def handler(num, frame):
        os.write(1, b'got ' + signal.Signals(num).name.encode() + b'\\n')
        os._exit(40 + num)
    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)
    print('ready', flush=True)
    time.sleep(30)
    sys.exit(99)
''')


def read_line(proc, timeout):
    """First stdout line of proc, failing (the caller kills proc) if none arrives in time."""
    ready, _, _ = select.select([proc.stdout], [], [], timeout)
    if not ready:
        raise AssertionError(f'no output from child within {timeout}s')
    return proc.stdout.readline().strip()


def find_tini():
    return shutil.which('tini') or ('/usr/bin/tini' if os.access('/usr/bin/tini', os.X_OK) else None)


class CiInitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tini = find_tini()
        if cls.tini is None:
            if os.environ.get('AGENT_EE_REQUIRE_TOOLCHAIN') == '1':
                raise AssertionError('tini is required (installed in the CI image)')
            raise unittest.SkipTest('tini not installed')

    def run_tini(self, script, *args):
        return subprocess.run([self.tini, '-s', '--', sys.executable, '-c', script, *map(str, args)],
                              capture_output=True, text=True, timeout=TIMEOUT)

    def test_orphaned_grandchildren_are_reaped_and_status_forwarded(self):
        result = self.run_tini(REAP_CHILD, 37, 10)
        self.assertEqual((result.returncode, result.stdout), (37, ''), result.stderr)

    def test_reaping_fixture_fails_under_a_non_reaping_init(self):
        result = subprocess.run([sys.executable, '-c', NON_REAPING_INIT, REAP_CHILD, '37', '2'],
                                capture_output=True, text=True, timeout=TIMEOUT)
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn('unreaped:', result.stdout)

    def test_exit_codes_forwarded_exactly(self):
        for code in (0, 1, 2, 42, 255):
            with self.subTest(code=code):
                result = self.run_tini(f'import sys; sys.exit({code})')
                self.assertEqual(result.returncode, code)

    def test_child_killed_by_signal_reports_128_plus_signal(self):
        result = self.run_tini('import os, signal; os.kill(os.getpid(), signal.SIGKILL)')
        self.assertEqual(result.returncode, 128 + signal.SIGKILL)

    def test_sigterm_and_sigint_reach_the_child(self):
        for sig in (signal.SIGTERM, signal.SIGINT):
            with self.subTest(signal=sig.name):
                proc = subprocess.Popen([self.tini, '-s', '--', sys.executable, '-c', SIGNAL_CHILD],
                                        stdout=subprocess.PIPE, text=True)
                try:
                    self.assertEqual(read_line(proc, TIMEOUT), 'ready')
                    proc.send_signal(sig)
                    out, _ = proc.communicate(timeout=TIMEOUT)
                finally:
                    if proc.poll() is None:
                        proc.kill()
                        proc.wait()
                self.assertEqual(out.strip(), f'got {sig.name}')
                self.assertEqual(proc.returncode, 40 + sig)


class ReadinessBoundTests(unittest.TestCase):
    def test_silent_live_child_fails_within_the_deadline(self):
        proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],
                                stdout=subprocess.PIPE, text=True)
        try:
            started = time.monotonic()
            with self.assertRaisesRegex(AssertionError, 'no output'):
                read_line(proc, 0.3)
            self.assertLess(time.monotonic() - started, 5)
        finally:
            proc.kill()
            proc.wait()


class DockerfileInitTests(unittest.TestCase):
    def test_image_entrypoint_is_pinned_tini_not_docker_init(self):
        dockerfile = (ROOT / 'Dockerfile').read_text()
        self.assertIn('ENTRYPOINT ["/usr/bin/tini", "--"]', dockerfile)
        self.assertRegex(dockerfile, r'(?m)^ARG TINI_VERSION=\d[\w.+~-]*$')
        self.assertIn('"tini=${TINI_VERSION}"', dockerfile)
        self.assertIn('apt-mark hold tini;', dockerfile)

    def test_canonical_launchers_do_not_use_docker_init(self):
        for path in ('.github/workflows/ci.yml',):
            with self.subTest(path=path):
                self.assertNotIn('--init', (ROOT / path).read_text())


if __name__ == '__main__':
    unittest.main()
