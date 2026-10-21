"""Owned fab-export and cpl-rotation fixtures regenerate deterministically (AGENTEE-367).

Each generator runs twice in the native sandbox, so the host's HOME, locale and
PATH cannot reach its output. The two runs must agree byte for byte. The
schematics and supplier records must equal the committed files byte for byte.
The committed boards predate the seeded KIID generator, so they are compared
with KIID (uuid) nodes removed and top-level items unordered. KiCad sorts board
items by KIID, so that is the only difference a reseed can make.
"""
import shutil
import tempfile
import unittest
from pathlib import Path

from agent_ee.executor import execute
from agent_ee.symbol_library import parse_sexpr

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


def without_kiids(node):
    if isinstance(node, list):
        return [without_kiids(child) for child in node
                if not (isinstance(child, list) and child and child[0] == 'uuid')]
    return node


def board_content(data):
    """Board items without KIIDs, in KIID-independent top-level order."""
    root = without_kiids(parse_sexpr(data))
    return root[0], sorted(map(repr, root[1:]))


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
        for name, (exact, boards) in GENERATED.items():
            with self.subTest(fixture=name), tempfile.TemporaryDirectory(prefix='agent-ee-ee367-') as tmp:
                first = self.regenerate(name, Path(tmp), 'first')
                second = self.regenerate(name, Path(tmp), 'second')
                produced = sorted(str(path.relative_to(first)) for path in first.rglob('*') if path.is_file())
                self.assertEqual(produced, sorted(exact + boards))
                for rel in produced:
                    self.assertEqual((first / rel).read_bytes(), (second / rel).read_bytes(), rel)
                for rel in exact:
                    self.assertEqual((first / rel).read_bytes(), (FIXTURES / name / rel).read_bytes(), rel)
                for rel in boards:
                    data = (first / rel).read_bytes()
                    self.assertIn(b'(uuid ', data)
                    self.assertEqual(board_content(data), board_content((FIXTURES / name / rel).read_bytes()),
                                     rel)


if __name__ == '__main__':
    unittest.main()
