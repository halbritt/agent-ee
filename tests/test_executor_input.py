"""Actual networkless subprocess input, output and cancellation boundaries."""
import hashlib
import tempfile
import threading
import unittest
from pathlib import Path

from agent_ee.executor import Cancellation, execute
from agent_ee.storage import read_json
from toolchain import require_toolchain


class ExecutorInputTests(unittest.TestCase):
    def setUp(self):
        require_toolchain()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.inputs = self.root / 'inputs'
        self.inputs.mkdir()

    def run_child(self, name, script, **kwargs):
        work = self.root / name
        record = execute(self.inputs, work, ['/usr/bin/python3', '-c', script], **kwargs)
        return record, (work / 'stdout.log').read_bytes(), (work / 'stderr.log').read_bytes()

    def test_delayed_large_input_reaches_eof_without_loss(self):
        payload = 'large prompt \u03a9\n' * 20000
        record, output, _ = self.run_child('delayed',
            'import hashlib,sys,time; time.sleep(.2); '
            'print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())',
            stdin=payload, timeout=3)
        self.assertEqual(record['execution'], 'ok')
        self.assertEqual(output.strip().decode(), hashlib.sha256(payload.encode()).hexdigest())

    def test_duplex_output_is_drained_before_input_read(self):
        record, output, error = self.run_child('duplex',
            'import sys; sys.stdout.write("o"*200000); sys.stdout.flush(); '
            'sys.stderr.write("e"*200000); sys.stderr.flush(); '
            'print(len(sys.stdin.buffer.read()))', stdin='i'*200000, timeout=3)
        self.assertEqual(record['execution'], 'ok')
        self.assertEqual(output, b'o'*200000+b'200000\n')
        self.assertEqual(error, b'e'*200000)

    def test_nonreader_timeout_preserves_diagnostic(self):
        record, output, _ = self.run_child('nonreader',
            'import time; print("started",flush=True); time.sleep(60)',
            stdin='i'*200000, timeout=.5)
        self.assertEqual(record['execution'], 'timeout')
        self.assertIn(b'started', output)
        self.assertLess(record['duration_s'], 2)

    def test_cancellation_large_input_and_subsequent_call(self):
        cancellation = Cancellation()
        timer = threading.Timer(.3, cancellation.cancel)
        timer.start()
        try:
            with self.assertRaises(KeyboardInterrupt):
                self.run_child('cancelled', 'import time; time.sleep(60)',
                               stdin='i'*200000, timeout=10, cancellation=cancellation)
        finally:
            timer.cancel()
            timer.join()
        record = read_json(self.root / 'cancelled/execution.json')
        self.assertEqual(record['execution'], 'interrupted')
        self.assertLess(record['duration_s'], 2)
        following, output, _ = self.run_child('following',
            'import sys; print(sys.stdin.read())', stdin='next', timeout=3)
        self.assertEqual(following['execution'], 'ok')
        self.assertEqual(output, b'next\n')

    def test_empty_and_absent_input_have_eof(self):
        for index, payload in enumerate((None, '')):
            with self.subTest(payload=payload):
                record, output, _ = self.run_child(str(index),
                    'import sys; print(len(sys.stdin.buffer.read()))', stdin=payload, timeout=3)
                self.assertEqual(record['execution'], 'ok')
                self.assertEqual(output, b'0\n')

    def test_early_exit_does_not_wait_for_input(self):
        record, output, _ = self.run_child('early', 'print("done")',
                                          stdin='i'*200000, timeout=3)
        self.assertEqual(record['execution'], 'ok')
        self.assertEqual(output, b'done\n')
