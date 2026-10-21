"""Owned fab-export and cpl-rotation fixtures regenerate deterministically (AGENTEE-367).

Each generator runs twice in the native sandbox, so the host's HOME, locale and
PATH cannot reach its output. The two runs must agree byte for byte, and every
output (schematics, supplier records and boards) must equal the committed file
byte for byte. The boards carry KIIDs from the seeded generator, so they are
pinned as generated.
"""
import shutil
import tempfile
import unittest
from pathlib import Path

from agent_ee.executor import execute

try:
    from toolchain import require_toolchain
except ImportError:
    from tests.toolchain import require_toolchain

FIXTURES = Path(__file__).resolve().parents[1] / 'fixtures'
GENERATED = {
    'fab-export': (('fab.kicad_sch',), ('fab.kicad_pcb', 'fab4.kicad_pcb', 'pod4.kicad_pcb')),
    'cpl-rotation': (('cpl.kicad_sch',) + tuple(
        f'supplier/{lcsc}.{kind}.json' for lcsc in ('C910002', 'C910008', 'C910010', 'C910020')
        for kind in ('raweasy', 'provenance')), ('cpl.kicad_pcb',)),
}


class FixtureGeneratorDeterminismTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)

    def regenerate(self, name, root, run):
        inputs = root / f'{run}-inputs'
        inputs.mkdir()
        shutil.copyfile(FIXTURES / name / 'generate.py', inputs / 'generate.py')
        record = execute(inputs, root / run, ['/usr/bin/python3', '-B', '/candidate/generate.py', '/work/out'],
                         timeout=300)
        self.assertEqual(record['exit_code'], 0, (root / run / 'console.log').read_text()[-2000:])
        return root / run / 'out'

    def test_generators_are_byte_deterministic(self):
        for name, (records, boards) in GENERATED.items():
            with self.subTest(fixture=name), tempfile.TemporaryDirectory(prefix='agent-ee-ee367-') as tmp:
                first = self.regenerate(name, Path(tmp), 'first')
                second = self.regenerate(name, Path(tmp), 'second')
                produced = sorted(str(path.relative_to(first)) for path in first.rglob('*') if path.is_file())
                self.assertEqual(produced, sorted(records + boards))
                for rel in produced:
                    self.assertEqual((first / rel).read_bytes(), (second / rel).read_bytes(), rel)
                for rel in boards:
                    self.assertIn(b'(uuid ', (first / rel).read_bytes(), rel)
                for rel in produced:
                    self.assertEqual((first / rel).read_bytes(), (FIXTURES / name / rel).read_bytes(), rel)


if __name__ == '__main__':
    unittest.main()
