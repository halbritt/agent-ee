"""Tree hygiene: no committed merge-conflict markers or merge leftovers anywhere
in the tracked tree.

Added 2026-10-18 after a reviewer-prepared merged doc carrying git conflict
markers reached main unnoticed; scripts/check_docs.py does not look for them.
AGENTEE-377 added the merge leftovers after a composition merge committed stray
.orig files that the marker scan missed.
"""
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MARKER = re.compile(rb'^(<<<<<<< |>>>>>>> |=======$)', re.M)
EXEMPT_SUFFIXES = ('.patch', '.diff')
# Files left by `patch`/`git apply --reject` (.orig, .rej) and by git mergetool,
# which writes name.BASE.123.ext (older Git) or name_BASE_123.ext (newer Git)
# beside the conflicted file, for BASE, LOCAL, REMOTE and BACKUP.
LEFTOVER = re.compile(r'(\.orig|\.rej)$|[._](BASE|LOCAL|REMOTE|BACKUP)[._]')


def tree_files(root=REPO):
    """Tracked files of a Git checkout; every file of a tree without .git.

    The container image copies the build context (which mirrors .gitignore)
    without .git, so there every present file is scanned. A checkout whose Git
    cannot list its files fails rather than falling back."""
    root = Path(root)
    if (root / '.git').exists():
        tracked = subprocess.run(['git', 'ls-files', '-z'], cwd=root, check=True,
                                 capture_output=True).stdout.split(b'\0')
        return [raw.decode() for raw in tracked if raw]
    return sorted(str(Path(base, name).relative_to(root))
                  for base, _, names in os.walk(root) for name in names)


def marker_offenders(root, files):
    offenders = []
    for name in files:
        if name.endswith(EXEMPT_SUFFIXES):
            continue
        path = Path(root) / name
        if not path.is_file():
            continue
        data = path.read_bytes()
        if b'\0' in data[:8192]:
            continue
        for match in MARKER.finditer(data):
            line = data.count(b'\n', 0, match.start()) + 1
            offenders.append(f'{name}:{line}')
    return offenders


def leftover_offenders(files):
    return [name for name in files if LEFTOVER.search(Path(name).name)]


class ConflictMarkerTest(unittest.TestCase):
    def test_no_tracked_file_contains_conflict_markers(self):
        files = tree_files()
        self.assertIn('tests/test_tree_hygiene.py', files)
        self.assertEqual(marker_offenders(REPO, files), [],
                         'committed merge-conflict markers')

    def test_refuses_markers_in_temporary_tree(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, 'a.md').write_text('ok\n' + '<' * 7 + ' HEAD\nx\n' + '=' * 7 + '\n')
            Path(root, 'b.patch').write_text('>' * 7 + ' theirs\n')
            Path(root, 'c.md').write_text('=' * 8 + '\n')
            files = tree_files(root)
            self.assertEqual(marker_offenders(root, files), ['a.md:2', 'a.md:4'])


class MergeLeftoverTest(unittest.TestCase):
    LEFTOVERS = ['src/a.py.orig', 'docs/b.md.rej', 'c.BACKUP.4242.py',
                 'd/e.BASE.4242.md', 'f.LOCAL.1.json', 'g.REMOTE.17.txt',
                 'h_BASE_4242.py', 'i_LOCAL_9.md', 'j_REMOTE_9', 'k_BACKUP_9.c']
    CLEAN = ['src/origin.py', 'docs/file-origins.json', 'evidence/BASELINE.md',
             'rejects.md', 'database.py', 'LOCAL.md', 'x.original', 'y.rejected',
             'evidence/donor-baseline-v2/REPORT.md']

    def test_no_tracked_merge_leftovers(self):
        files = tree_files()
        self.assertIn('tests/test_tree_hygiene.py', files)
        self.assertEqual(leftover_offenders(files), [], 'committed merge leftovers')

    def _populate(self, root):
        for name in self.LEFTOVERS + self.CLEAN:
            path = Path(root, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('x\n')

    def test_refuses_leftovers_in_tree_without_git(self):
        with tempfile.TemporaryDirectory() as root:
            self._populate(root)
            files = tree_files(root)
            self.assertEqual(sorted(files), sorted(self.LEFTOVERS + self.CLEAN))
            self.assertEqual(sorted(leftover_offenders(files)), sorted(self.LEFTOVERS))

    def test_refuses_tracked_leftovers_but_not_untracked_in_git_checkout(self):
        with tempfile.TemporaryDirectory() as root:
            subprocess.run(['git', 'init', '-q', root], check=True)
            self._populate(root)
            subprocess.run(['git', 'add', '--', 'src/a.py.orig', 'h_BASE_4242.py',
                            'docs/file-origins.json'], cwd=root, check=True)
            Path(root, 'untracked.py.rej').write_text('x\n')
            files = tree_files(root)
            self.assertNotIn('untracked.py.rej', files)
            self.assertNotIn('.git/HEAD', files)
            self.assertEqual(sorted(leftover_offenders(files)),
                             ['h_BASE_4242.py', 'src/a.py.orig'])

    def test_failed_git_listing_fails_rather_than_falling_back(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, '.git').write_text('gitdir: /nonexistent/agent-ee-377\n')
            Path(root, 'a.py.orig').write_text('x\n')
            with self.assertRaises(subprocess.CalledProcessError):
                tree_files(root)


if __name__ == '__main__':
    unittest.main()
