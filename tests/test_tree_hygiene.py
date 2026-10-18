"""Tree hygiene: no committed merge-conflict markers anywhere in the tracked tree.

Added 2026-10-18 after a reviewer-prepared merged doc carrying git conflict
markers reached main unnoticed; scripts/check_docs.py does not look for them.
"""
import re
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MARKER = re.compile(rb'^(<<<<<<< |>>>>>>> |=======$)', re.M)
EXEMPT_SUFFIXES = ('.patch', '.diff')


class ConflictMarkerTest(unittest.TestCase):
    def test_no_tracked_file_contains_conflict_markers(self):
        tracked = subprocess.run(['git', 'ls-files', '-z'], cwd=REPO, check=True,
                                 capture_output=True).stdout.split(b'\0')
        offenders = []
        for raw in tracked:
            if not raw or raw.decode().endswith(EXEMPT_SUFFIXES):
                continue
            path = REPO / raw.decode()
            if not path.is_file():
                continue
            data = path.read_bytes()
            if b'\0' in data[:8192]:
                continue
            for match in MARKER.finditer(data):
                line = data.count(b'\n', 0, match.start()) + 1
                offenders.append(f'{raw.decode()}:{line}')
        self.assertEqual(offenders, [], 'committed merge-conflict markers')


if __name__ == '__main__':
    unittest.main()
