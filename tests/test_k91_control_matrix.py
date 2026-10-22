"""The K91 harness control matrix as a test: the lane's regression gate (AGENTEE-442, third-HOLD root redesign).

scripts/k91_control_matrix.py runs every row through the actual production routing consumer on the
synthetic fixture router and this checkout's scripts; every row must have its expected outcome.
Running the same script with --scripts pointing at another tree's scripts (57083ef8) shows that
tree's differing rows instead.
"""
import importlib.util
import unittest
from pathlib import Path

from tests.toolchain import require_toolchain

ROOT = Path(__file__).resolve().parents[1]


def load():
    spec = importlib.util.spec_from_file_location('k91_control_matrix', ROOT / 'scripts/k91_control_matrix.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ControlMatrixTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain()
        cls.matrix = load()
        cls.rows = cls.matrix.run_matrix()

    def test_every_row_has_its_expected_outcome(self):
        differ = [row for row in self.rows if not self.matrix.matches(row)]
        self.assertEqual(differ, [], '\n'.join(f"{r['id']}: expected {r['expected']} actual {r['actual']}"
                                               for r in differ))

    def test_the_matrix_covers_every_slot_and_failure(self):
        ids = {row['id'] for row in self.rows}
        for slot in self.matrix.SLOTS:
            for case in ('intended refusal', 'untouched replay', *self.matrix.PLUMBING, 'skipped consumer call',
                         'round-2 skipped adoption, bare adopt succeeded', 'malformed skip record',
                         *(f'missing {key}' for key in self.matrix.MISSING + self.matrix.PRECONDITION_FIELDS[slot])):
                with self.subTest(slot=slot, case=case):
                    self.assertIn(f'{slot}: {case}', ids)
        # Only the intended rows refuse; every plumbing row is incomplete and keeps controls_refused false.
        refused = sorted(row['id'] for row in self.rows if row['slot'] and row['actual'] == 'refused')
        self.assertEqual(refused, sorted(f'{slot}: intended refusal' for slot in self.matrix.SLOTS))


if __name__ == '__main__':
    unittest.main()
