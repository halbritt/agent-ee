"""Tree hygiene: no committed merge-conflict markers anywhere in the tracked tree.

Added 2026-10-18 after a reviewer-prepared merged doc carrying git conflict
markers reached main unnoticed; scripts/check_docs.py does not look for them.
"""
import os
import re
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MARKER = re.compile(rb'^(<<<<<<< |>>>>>>> |=======$)', re.M)
EXEMPT_SUFFIXES = ('.patch', '.diff')


def tree_files():
    """Tracked files of a Git checkout; every file of a tree without .git.

    The container image copies the build context (which mirrors .gitignore)
    without .git, so there every present file is scanned. A checkout whose Git
    cannot list its files fails rather than falling back."""
    if (REPO / '.git').exists():
        tracked = subprocess.run(['git', 'ls-files', '-z'], cwd=REPO, check=True,
                                 capture_output=True).stdout.split(b'\0')
        return [raw.decode() for raw in tracked if raw]
    return sorted(str(Path(base, name).relative_to(REPO))
                  for base, _, names in os.walk(REPO) for name in names)


class ConflictMarkerTest(unittest.TestCase):
    def test_no_tracked_file_contains_conflict_markers(self):
        files = tree_files()
        self.assertIn('tests/test_tree_hygiene.py', files)
        offenders = []
        for name in files:
            if name.endswith(EXEMPT_SUFFIXES):
                continue
            path = REPO / name
            if not path.is_file():
                continue
            data = path.read_bytes()
            if b'\0' in data[:8192]:
                continue
            for match in MARKER.finditer(data):
                line = data.count(b'\n', 0, match.start()) + 1
                offenders.append(f'{name}:{line}')
        self.assertEqual(offenders, [], 'committed merge-conflict markers')


if __name__ == '__main__':
    unittest.main()
