"""The K91 era verifier fixture is agent-ee's own modules at 7552f6db, byte for byte (AGENTEE-442).

scripts/k91_control_matrix.py replays the round-1 record under the verifier of its era, loaded only
from tests/fixtures/k91-era-7552f6db, because the gate image is built from a git-archive context
with no .git. With git history the fixture must equal `git show ERA:<path>`; without it, each file's
git blob id, computed here as git hash-object does, must be the manifest's. Neither case skips.
"""
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load():
    spec = importlib.util.spec_from_file_location('k91_control_matrix', ROOT / 'scripts/k91_control_matrix.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def blob_id(data):
    return hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()


def git_show(commit, path):
    """The blob bytes at commit:path, or None when this checkout has no git history holding it."""
    try:
        out = subprocess.run(['git', '-C', str(ROOT), 'show', f'{commit}:{path}'], capture_output=True)
    except OSError:
        return None
    return out.stdout if out.returncode == 0 else None


class EraFixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.matrix = load()
        cls.manifest = json.loads((cls.matrix.ERA_FIXTURE / 'manifest.json').read_text())

    def test_the_manifest_names_the_era_and_its_modules(self):
        self.assertEqual(self.manifest['commit'], self.matrix.ERA)
        self.assertEqual(sorted(self.manifest['modules']), sorted(self.matrix.ERA_MODULES))
        for name, entry in self.manifest['modules'].items():
            self.assertEqual(entry['path'], f'src/agent_ee/{name}.py')

    def test_the_fixture_is_the_era_blobs(self):
        modes = set()
        for name, entry in self.manifest['modules'].items():
            with self.subTest(name):
                data = (self.matrix.ERA_FIXTURE / f'{name}.py').read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), entry['sha256'])
                self.assertEqual(blob_id(data), entry['git_blob'])
                shown = git_show(self.manifest['commit'], entry['path'])
                if shown is not None:
                    self.assertEqual(shown, data)
                    modes.add('git show')
                else:
                    modes.add('blob id')
        print(f'era fixture identity checked by: {", ".join(sorted(modes))}', file=sys.stderr)

    def test_the_matrix_loads_only_matching_fixture_bytes(self):
        self.assertEqual(set(self.matrix.era_modules()), set(self.matrix.ERA_MODULES))
        with tempfile.TemporaryDirectory() as temp:
            copy = Path(temp) / 'fixture'
            shutil.copytree(self.matrix.ERA_FIXTURE, copy)
            target = copy / 'storage.py'
            target.write_bytes(target.read_bytes() + b'\n')
            with mock.patch.object(self.matrix, 'ERA_FIXTURE', copy):
                with self.assertRaisesRegex(ValueError, 'storage.py differs from its manifest sha256'):
                    self.matrix.era_modules()


if __name__ == '__main__':
    unittest.main()
