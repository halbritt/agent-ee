"""Executor records never write through what a tool left in its writable /work.

Each tool runs in the real no-network sandbox and plants entries at the record names.
Link targets are an owned host-only directory that is not mounted in the sandbox, so
only a coordinator-side write could reach them.
"""
import hashlib
import json
import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path

from agent_ee.executor import RECORDS, Cancellation, execute
from agent_ee.storage import json_digest, read_json, write_json
from toolchain import toolchain_test

SENTINEL = b'OWNED-HOST-SENTINEL-NOT-A-SECRET\n'

# Plan keys: links, dirs (each holding a link to plan["inner"]), fifos, files, hardlinks
# (name -> existing name), chmod, then stdout/stderr text, sleep and exit.
TOOL = '''import json, os, sys, time
plan = json.loads(sys.argv[1])
work = "/work/"
for name, text in plan.get("files", {}).items():
    os.makedirs(os.path.dirname(work + name), exist_ok=True)
    with open(work + name, plan.get("file_mode", "w")) as stream:
        stream.write(text)
for name, target in plan.get("links", {}).items():
    os.symlink(target, work + name)
for name in plan.get("dirs", []):
    os.mkdir(work + name)
    os.symlink(plan["inner"], work + name + "/inner")
for name in plan.get("fifos", []):
    os.mkfifo(work + name)
for name, existing in plan.get("hardlinks", {}).items():
    os.link(work + existing, work + name)
for name, mode in plan.get("chmod", {}).items():
    os.chmod(work + name, mode)
sys.stdout.write(plan.get("stdout", "")); sys.stdout.flush()
sys.stderr.write(plan.get("stderr", "")); sys.stderr.flush()
time.sleep(plan.get("sleep", 0))
raise SystemExit(plan.get("exit", 0))
'''


def entries(root):
    """Every entry below root: file bytes, link targets and directory markers, never following links."""
    found = {}
    for current, dirs, names in os.walk(root, followlinks=False):
        for name in [*dirs, *names]:
            path = Path(current) / name
            rel = path.relative_to(root).as_posix()
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                found[rel] = ('link', os.readlink(path))
            elif stat.S_ISREG(mode):
                found[rel] = ('file', path.read_bytes())
            else:
                found[rel] = ('directory' if stat.S_ISDIR(mode) else 'special', None)
    return found


class ExecutorRecordTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-executor-records-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.inputs = self.root / 'inputs'
        self.inputs.mkdir()
        self.host = self.root / 'host-only'
        self.host.mkdir()
        (self.host / 'sentinel').write_bytes(SENTINEL)
        self.host_before = entries(self.host)

    def argv(self, plan):
        return ['/usr/bin/python3', '-c', TOOL, json.dumps(plan)]

    def run_tool(self, work, plan, **kwargs):
        """execute() in a thread, so a write that blocks (for example on a FIFO) fails rather than hangs."""
        outcome = {}

        def target():
            try:
                outcome['record'] = execute(self.inputs, work, self.argv(plan), **kwargs)
            except BaseException as exc:  # Reported to the test below, never swallowed.
                outcome['error'] = exc
        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        thread.join(60)
        self.assertFalse(thread.is_alive(), 'executor did not return')
        if 'error' in outcome:
            raise outcome['error']
        return outcome['record']

    def restore(self, path):
        self.addCleanup(lambda: path.exists() and os.chmod(path, 0o700))

    def assert_host_untouched(self):
        self.assertEqual(entries(self.host), self.host_before)

    def assert_published(self, work, record, stdout, stderr):
        """The record names hold exactly the executor's captured bytes, as fresh single-link files."""
        expected = {'stdout.log': stdout, 'stderr.log': stderr, 'console.log': stdout + stderr}
        for name in RECORDS:
            info = (work / name).lstat()
            self.assertTrue(stat.S_ISREG(info.st_mode), name)
            self.assertEqual(info.st_nlink, 1, name)
        for name, data in expected.items():
            self.assertEqual((work / name).read_bytes(), data, name)
        self.assertEqual(record['stdout_sha256'], hashlib.sha256(stdout).hexdigest())
        self.assertEqual(record['stderr_sha256'], hashlib.sha256(stderr).hexdigest())
        self.assertEqual(record['console_sha256'], hashlib.sha256(stdout + stderr).hexdigest())
        self.assertEqual(read_json(work / 'execution.json'), record)
        self.assertEqual(hashlib.sha256((work / 'execution.json').read_bytes()).hexdigest(), json_digest(record))
        self.assertEqual([p.name for p in work.iterdir() if p.name.endswith('.tmp') and p.name.startswith('.')], [])

    @toolchain_test
    def test_links_at_every_record_name_never_reach_the_host_on_each_outcome(self):
        sentinel, absent = str(self.host / 'sentinel'), str(self.host / 'created')
        links = {'stdout.log': sentinel, 'stderr.log': sentinel, 'console.log': absent,
                 'execution.json': sentinel, 'execution.json.tmp': absent}
        cases = {'ok': ({'exit': 0}, {}), 'failed': ({'exit': 3}, {}),
                 'timeout': ({'sleep': 30}, {'timeout': 2}),
                 'interrupted': ({'sleep': 30}, {'timeout': 30})}
        for execution, (extra, kwargs) in cases.items():
            with self.subTest(execution):
                work = self.root / execution
                plan = {'links': links, 'stdout': 'TOOL-OUTPUT\n', 'stderr': 'TOOL-ERROR\n', **extra}
                if execution == 'interrupted':
                    cancellation = Cancellation()
                    timer = threading.Timer(1.5, cancellation.cancel)
                    timer.start()
                    self.addCleanup(timer.cancel)
                    with self.assertRaises(KeyboardInterrupt):
                        self.run_tool(work, plan, cancellation=cancellation, **kwargs)
                    record = read_json(work / 'execution.json')
                else:
                    record = self.run_tool(work, plan, **kwargs)
                self.assert_host_untouched()
                self.assertEqual(record['execution'], execution)
                self.assertEqual(record['exit_code'], {'ok': 0, 'failed': 3}.get(execution))
                self.assert_published(work, record, b'TOOL-OUTPUT\n', b'TOOL-ERROR\n')
                displaced = record['displaced_records']
                self.assertEqual(set(displaced), set(RECORDS))
                for name, moved in displaced.items():
                    # Kept as the tool left it: the interference stays visible.
                    self.assertEqual(moved['kind'], 'link')
                    self.assertEqual(os.readlink(work / moved['moved_to']), links[name])
                # Not an executor name any more, so it is the tool's own entry and is left alone.
                self.assertEqual(os.readlink(work / 'execution.json.tmp'), absent)

    @toolchain_test
    def test_special_entries_and_hard_links_move_aside_and_tool_outputs_stay_intact(self):
        work = self.root / 'special'
        forged = json.dumps({'execution': 'ok', 'exit_code': 0, 'argv': ['forged']})
        plan = {'files': {'result.txt': 'TOOL RESULT', 'nested/kept.txt': 'kept', 'execution.json': forged},
                'dirs': ['stdout.log'], 'inner': str(self.host / 'sentinel'), 'fifos': ['stderr.log'],
                'hardlinks': {'console.log': 'result.txt'}, 'chmod': {'stdout.log': 0o500},
                'stdout': 'out', 'exit': 0}
        record = self.run_tool(work, plan, timeout=20)
        moved = {name: work / value['moved_to'] for name, value in record['displaced_records'].items()}
        self.restore(moved['stdout.log'])
        self.assert_host_untouched()
        self.assertEqual(record['execution'], 'ok')
        self.assert_published(work, record, b'out', b'')
        self.assertEqual({name: value['kind'] for name, value in record['displaced_records'].items()},
                         {'stdout.log': 'directory', 'stderr.log': 'fifo', 'console.log': 'file',
                          'execution.json': 'file'})
        self.assertEqual(os.readlink(moved['stdout.log'] / 'inner'), str(self.host / 'sentinel'))
        self.assertTrue(stat.S_ISFIFO(moved['stderr.log'].lstat().st_mode))
        self.assertEqual(moved['execution.json'].read_text(), forged)
        # The hard-linked tool output was neither truncated nor overwritten by the console.
        self.assertEqual((work / 'result.txt').read_text(), 'TOOL RESULT')
        self.assertEqual((work / 'result.txt').stat().st_ino, moved['console.log'].stat().st_ino)
        self.assertEqual((work / 'nested/kept.txt').read_text(), 'kept')

    @toolchain_test
    def test_records_from_an_earlier_run_are_replaced_but_tool_changes_to_them_are_reported(self):
        work = self.root / 'repeated'
        first = self.run_tool(work, {'stdout': 'first\n'}, timeout=20)
        self.assertNotIn('displaced_records', first)
        second = self.run_tool(work, {'stdout': 'second\n'}, timeout=20)
        self.assertNotIn('displaced_records', second)
        self.assert_published(work, second, b'second\n', b'')
        self.assertFalse([p for p in work.iterdir() if '.displaced-' in p.name])
        # Appending in place keeps the inode, so the change is in its size and change time.
        third = self.run_tool(work, {'files': {'stdout.log': 'appended\n'}, 'file_mode': 'a', 'stdout': 'third\n'},
                              timeout=20)
        self.assert_published(work, third, b'third\n', b'')
        moved = third['displaced_records']['stdout.log']
        self.assertEqual(moved['kind'], 'file')
        self.assertEqual((work / moved['moved_to']).read_text(), 'second\nappended\n')
        self.assertEqual(set(third['displaced_records']), {'stdout.log'})

    @toolchain_test
    def test_tool_planted_link_at_a_work_directory_or_its_home_is_refused_before_launch(self):
        outer = self.root / 'outer'
        planted = self.run_tool(outer, {'links': {'inner': str(self.host)}}, timeout=20)
        self.assertEqual(planted['execution'], 'ok')
        with self.assertRaises(OSError):
            self.run_tool(outer / 'inner', {'files': {'escaped': 'x'}, 'stdout': 'never'}, timeout=20)
        self.assert_host_untouched()
        # A home/ replaced by a link is not reused as the next run's home.
        script = 'import os; os.rmdir("/work/home"); os.symlink(%r, "/work/home")' % str(self.host)
        replaced = execute(self.inputs, outer, ['/usr/bin/python3', '-c', script], timeout=20)
        self.assertEqual(replaced['execution'], 'ok')
        with self.assertRaises(FileExistsError):
            self.run_tool(outer, {'files': {'home/escaped': 'x'}}, timeout=20)
        self.assert_host_untouched()
        self.assertEqual(read_json(outer / 'execution.json'), replaced)

    @toolchain_test
    def test_unwritable_scratch_raises_with_the_planted_link_unfollowed(self):
        work = self.root / 'unwritable'
        self.restore(work)
        plan = {'links': {'stdout.log': str(self.host / 'sentinel')}, 'chmod': {'.': 0o500}, 'stdout': 'x'}
        with self.assertRaises(PermissionError):
            self.run_tool(work, plan, timeout=20)
        self.assert_host_untouched()
        self.assertEqual(os.readlink(work / 'stdout.log'), str(self.host / 'sentinel'))
        self.assertEqual(sorted(p.name for p in work.iterdir()), ['home', 'stdout.log'])
        # An interrupt still propagates as one, with the record failure as its cause.
        work = self.root / 'unwritable-interrupted'
        self.restore(work)
        cancellation = Cancellation()
        timer = threading.Timer(1.5, cancellation.cancel)
        timer.start()
        self.addCleanup(timer.cancel)
        with self.assertRaises(KeyboardInterrupt) as raised:
            self.run_tool(work, {**plan, 'sleep': 30}, timeout=30, cancellation=cancellation)
        self.assertIsInstance(raised.exception.__cause__, PermissionError)
        self.assert_host_untouched()


class WriteJsonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-write-json-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.host = self.root / 'host-only'
        self.host.mkdir()
        (self.host / 'sentinel').write_bytes(SENTINEL)
        self.dir = self.root / 'scratch'
        self.dir.mkdir()

    def test_planted_leaf_and_temporary_links_are_replaced_never_followed(self):
        os.symlink(self.host / 'sentinel', self.dir / 'record.json')
        os.symlink(self.host / 'created', self.dir / 'record.json.tmp')
        os.symlink(self.host / 'sentinel', self.dir / 'other.json.tmp')
        value = {'execution': 'ok', 'values': [1, 2]}
        write_json(self.dir / 'record.json', value)
        write_json(self.dir / 'other.json', value)
        self.assertEqual(entries(self.host), {'sentinel': ('file', SENTINEL)})
        for name in ('record.json', 'other.json'):
            self.assertFalse((self.dir / name).is_symlink())
            self.assertEqual(hashlib.sha256((self.dir / name).read_bytes()).hexdigest(), json_digest(value))
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ['other.json', 'record.json'])

    def test_failed_write_keeps_the_previous_record_and_no_temporary_file(self):
        write_json(self.dir / 'record.json', {'kept': True})
        with self.assertRaises(ValueError):
            write_json(self.dir / 'record.json', {'value': float('nan')})
        (self.dir / 'busy').mkdir()
        with self.assertRaises(IsADirectoryError):
            write_json(self.dir / 'busy', {'value': 1})
        self.assertEqual(read_json(self.dir / 'record.json'), {'kept': True})
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ['busy', 'record.json'])


class LaunchInterruptTests(unittest.TestCase):
    """AGENTEE-269: an operator SIGINT that lands while the sandbox is being launched.

    The sandbox is visible in /proc once bwrap has exec'd, before Popen returns its
    handle; an interrupt raised there must still kill and reap the sandbox."""

    @toolchain_test
    def test_interrupt_inside_the_launch_still_reaps_the_sandbox(self):
        import signal
        import subprocess
        from unittest import mock
        root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix='agent-ee-launch-interrupt-')))
        (root / 'inputs').mkdir()
        launched, real = [], subprocess.Popen

        def interrupted_launch(*args, **kwargs):
            proc = real(*args, **kwargs)
            launched.append(proc)
            self.addCleanup(lambda: proc.poll() is None and (proc.kill(), proc.wait()))
            os.kill(os.getpid(), signal.SIGINT)
            return proc
        with mock.patch.object(subprocess, 'Popen', interrupted_launch), self.assertRaises(KeyboardInterrupt):
            execute(root / 'inputs', root / 'work', ['/usr/bin/python3', '-c', 'import time; time.sleep(30)'],
                    timeout=30)
        self.assertEqual(len(launched), 1)
        self.assertIsNotNone(launched[0].returncode, 'the launched sandbox was never reaped')
        with self.assertRaises(ProcessLookupError):
            os.kill(launched[0].pid, 0)
        self.assertEqual(read_json(root / 'work/execution.json')['execution'], 'interrupted')


if __name__ == '__main__':
    unittest.main()
