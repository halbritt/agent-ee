import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_ee.locking import writer_lock


class WriterTests(unittest.TestCase):
    def test_competing_process_refused_and_exception_releases_lease(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"
            command = [sys.executable, "-c", "from agent_ee.locking import writer_lock; "
                       "import sys;\nwith writer_lock(sys.argv[1]): pass", str(run)]
            with self.assertRaisesRegex(RuntimeError, "stop"):
                with writer_lock(run):
                    proc = subprocess.run(command, capture_output=True, text=True)
                    self.assertNotEqual(proc.returncode, 0)
                    self.assertIn("another writer owns", proc.stderr)
                    raise RuntimeError("stop")
            proc = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)


    def test_campaign_ancestor_lease_blocks_nested_writer_but_allows_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "campaign"
            root.mkdir()
            (root / ".campaign-root").touch()
            run = root / "runs/1"
            command = [sys.executable, "-c", "from agent_ee.locking import writer_lock; "
                       "import sys;\nwith writer_lock(sys.argv[1]): pass", str(run)]
            with writer_lock(root):
                with writer_lock(run):
                    proc = subprocess.run(command, capture_output=True, text=True)
                    self.assertNotEqual(proc.returncode, 0)
                    self.assertIn("another writer owns", proc.stderr)
            self.assertEqual(subprocess.run(command, capture_output=True).returncode, 0)
