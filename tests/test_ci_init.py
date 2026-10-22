"""CI container init contract: tini as PID 1 reaps, forwards signals and the exit status.

The image sets `ENTRYPOINT ["/usr/bin/tini", "--"]` instead of `docker run --init`
(whose /usr/sbin/docker-init bind mount is a /usr submount that the stage-receipt
refusal rejects). Behaviour is proven with the real tini binary run as a subreaper
(`-s`), which is the role PID 1 plays in the container.
"""
import os
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
    init = os.getppid()
    def zombies(init):
        out = []
        for entry in os.listdir('/proc'):
            if entry.isdigit():
                try:
                    stat = open(f'/proc/{entry}/stat').read()
                except OSError:
                    continue
                rest = stat.rsplit(')', 1)[1].split()
                if rest[0] == 'Z' and int(rest[1]) in (init, os.getpid()):
                    out.append(entry)
        return out
    # Orphans: a middle process forks a grandchild and exits at once, so the
    # grandchild is reparented to the init (the subreaper) and later exits.
    r, w = os.pipe()
    middle = os.fork()
    if middle == 0:
        grand = os.fork()
        if grand == 0:
            os.close(r)
            time.sleep(0.3)
            os.write(w, b'x')
            os._exit(0)
        os._exit(0)
    os.close(w)
    os.waitpid(middle, 0)
    # An already-exited sibling whose parent never waits on it, then orphaned.
    sibling = os.fork()
    if sibling == 0:
        os._exit(0)
    os.read(r, 1)                      # the orphan has now exited
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        # The only zombie allowed is our own unwaited sibling.
        leftover = [p for p in zombies(init) if p != str(sibling)]
        if not leftover:
            break
        time.sleep(0.05)
    else:
        print('unreaped:', leftover, flush=True)
        sys.exit(3)
    os.waitpid(sibling, 0)
    sys.exit(int(sys.argv[1]))
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
        result = self.run_tini(REAP_CHILD, 37)
        self.assertEqual((result.returncode, result.stdout), (37, ''), result.stderr)

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
                    self.assertEqual(proc.stdout.readline().strip(), 'ready')
                    proc.send_signal(sig)
                    out, _ = proc.communicate(timeout=TIMEOUT)
                finally:
                    if proc.poll() is None:
                        proc.kill()
                        proc.wait()
                self.assertEqual(out.strip(), f'got {sig.name}')
                self.assertEqual(proc.returncode, 40 + sig)


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
