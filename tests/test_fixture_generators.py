"""Owned fab-export and cpl-rotation fixtures regenerate deterministically (AGENTEE-367).

Each generator runs twice in the native sandbox, so the host's HOME, locale and
PATH cannot reach its output. The two runs must agree byte for byte, and every
output (schematics, supplier records and boards) must equal the committed file
byte for byte. The boards carry KIIDs from the seeded generator, so they are
pinned as generated.

Each board's bytes must not depend on which boards were built before it: every
board is generated alone in a fresh process, and all boards are generated in
one shared process in reordered and repeated sequence across both generators.
The boards are pinned to pcbnew 10.0.7; this test refuses another installed
version, and both generators refuse an unknown or other version before writing.
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
QUALIFIED_PCBNEW = '10.0.7'
GENERATED = {
    'fab-export': (('fab.kicad_sch',), ('fab.kicad_pcb', 'fab4.kicad_pcb', 'pod4.kicad_pcb')),
    'cpl-rotation': (('cpl.kicad_sch',) + tuple(
        f'supplier/{lcsc}.{kind}.json' for lcsc in ('C910002', 'C910008', 'C910010', 'C910020')
        for kind in ('raweasy', 'provenance')), ('cpl.kicad_pcb',)),
}
# board: (generator module in the driver, make_board arguments)
BOARDS = {
    'fab-export/fab.kicad_pcb': ('fab', '2'),
    'fab-export/fab4.kicad_pcb': ('fab', '4'),
    'fab-export/pod4.kicad_pcb': ('fab', '4, pod=True'),
    'cpl-rotation/cpl.kicad_pcb': ('cpl', ''),
}
DRIVER = """import runpy
from pathlib import Path
fab = runpy.run_path('/candidate/fab.py')
cpl = runpy.run_path('/candidate/cpl.py')
out = Path('/work/out')
out.mkdir()
"""


class FixtureGeneratorDeterminismTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)
        with tempfile.TemporaryDirectory(prefix='agent-ee-ee367-') as tmp:
            inputs = Path(tmp) / 'inputs'
            inputs.mkdir()
            record = execute(inputs, Path(tmp) / 'run',
                             ['/usr/bin/python3', '-B', '-c', 'import pcbnew; print(pcbnew.Version())'])
            version = (Path(tmp) / 'run' / 'stdout.log').read_text().strip()
        if record['exit_code'] != 0 or version != QUALIFIED_PCBNEW:
            raise AssertionError(f'fixture boards are pinned to pcbnew {QUALIFIED_PCBNEW}; '
                                 f'native pcbnew reports {version!r} (exit {record["exit_code"]})')

    def run_sandboxed(self, root, label, files, argv):
        inputs = root / f'{label}-inputs'
        inputs.mkdir()
        for name, source in files.items():
            if isinstance(source, Path):
                shutil.copyfile(source, inputs / name)
            else:
                (inputs / name).write_text(source)
        record = execute(inputs, root / label, argv, timeout=300)
        return record, root / label

    def regenerate(self, name, root, run):
        record, work = self.run_sandboxed(root, run, {'generate.py': FIXTURES / name / 'generate.py'},
                                          ['/usr/bin/python3', '-B', '/candidate/generate.py', '/work/out'])
        self.assertEqual(record['exit_code'], 0, (work / 'console.log').read_text()[-2000:])
        return work / 'out'

    def drive(self, root, label, sequence):
        """Build the boards in sequence in one process; return the output path of each step."""
        script = DRIVER
        outputs = []
        for step, board in enumerate(sequence):
            module, arguments = BOARDS[board]
            target = f'{step}-{board.replace("/", "-")}'
            script += f"{module}['make_board'](out / {target!r}{', ' + arguments if arguments else ''})\n"
            outputs.append((board, target))
        record, work = self.run_sandboxed(
            root, label, {'fab.py': FIXTURES / 'fab-export' / 'generate.py',
                          'cpl.py': FIXTURES / 'cpl-rotation' / 'generate.py', 'run.py': script},
            ['/usr/bin/python3', '-B', '/candidate/run.py'])
        self.assertEqual(record['exit_code'], 0, (work / 'console.log').read_text()[-2000:])
        return [(board, work / 'out' / target) for board, target in outputs]

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

    def test_board_bytes_are_independent_of_generation_order(self):
        reordered = ['cpl-rotation/cpl.kicad_pcb', 'fab-export/pod4.kicad_pcb', 'fab-export/fab4.kicad_pcb',
                     'fab-export/fab.kicad_pcb']
        repeated = ['fab-export/pod4.kicad_pcb', 'fab-export/fab.kicad_pcb', 'cpl-rotation/cpl.kicad_pcb',
                    'fab-export/pod4.kicad_pcb', 'fab-export/fab4.kicad_pcb', 'cpl-rotation/cpl.kicad_pcb',
                    'fab-export/fab4.kicad_pcb', 'fab-export/fab.kicad_pcb']
        runs = {f'alone-{board.replace("/", "-")}': [board] for board in BOARDS}
        runs.update({'shared-reordered': reordered, 'shared-repeated': repeated})
        with tempfile.TemporaryDirectory(prefix='agent-ee-ee367-') as tmp:
            for label, sequence in runs.items():
                with self.subTest(run=label):
                    for board, produced in self.drive(Path(tmp), label, sequence):
                        self.assertEqual(produced.read_bytes(), (FIXTURES / board).read_bytes(),
                                         f'{label}: {produced.name}')

    def test_generators_refuse_unqualified_pcbnew(self):
        reports = {'10.0.8': "lambda: '10.0.8'", '99.0.0': "lambda: '99.0.0'", 'empty': "lambda: ''",
                   'none': 'lambda: None', 'raises': "lambda: (_ for _ in ()).throw(RuntimeError('no version'))"}
        with tempfile.TemporaryDirectory(prefix='agent-ee-ee367-') as tmp:
            for name in GENERATED:
                generator = FIXTURES / name / 'generate.py'
                for report, patch in reports.items():
                    with self.subTest(fixture=name, mode='cli', version=report):
                        label = f'{name}-cli-{report}'
                        record, work = self.run_sandboxed(Path(tmp), label, {'generate.py': generator, 'run.py': (
                            f'import pcbnew, runpy, sys\npcbnew.Version = {patch}\n'
                            "sys.argv = ['generate.py', '/work/out']\n"
                            "runpy.run_path('/candidate/generate.py', run_name='__main__')\n")},
                            ['/usr/bin/python3', '-B', '/candidate/run.py'])
                        console = (work / 'console.log').read_text()
                        self.assertNotEqual(record['exit_code'], 0, console[-2000:])
                        self.assertRegex(console, r'not the qualified 10\.0\.7|version unknown')
                        self.assertFalse((work / 'out').exists(), 'refused generator wrote output')
                with self.subTest(fixture=name, mode='make_board', version='10.0.8'):
                    label = f'{name}-make-board'
                    arguments = "Path('/work/out.kicad_pcb')" + (', 2' if name == 'fab-export' else '')
                    record, work = self.run_sandboxed(Path(tmp), label, {'generate.py': generator, 'run.py': (
                        "import pcbnew, runpy\nfrom pathlib import Path\npcbnew.Version = lambda: '10.0.8'\n"
                        f"runpy.run_path('/candidate/generate.py')['make_board']({arguments})\n")},
                        ['/usr/bin/python3', '-B', '/candidate/run.py'])
                    console = (work / 'console.log').read_text()
                    self.assertNotEqual(record['exit_code'], 0, console[-2000:])
                    self.assertIn('not the qualified 10.0.7', console)
                    self.assertFalse((work / 'out.kicad_pcb').exists(), 'refused generator wrote a board')


if __name__ == '__main__':
    unittest.main()
