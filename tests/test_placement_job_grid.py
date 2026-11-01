"""The router search grid follows the board when a plan does not choose one."""
import unittest

from agent_ee import placement_job as job
from test_electrical_locality import board, declaration

MM = 1_000_000


def problem(pitch_nm, *, nets=('A', 'B')):
    pads = [dict(local_nm=[0, 0], net=nets[0]), dict(local_nm=[pitch_nm, 0], net=nets[1])]
    return dict(footprints=[dict(pads=pads)])


class RouterGridTests(unittest.TestCase):
    def test_grid_follows_declaration_pad_pitch_and_failed_trials(self):
        declared = dict(router=dict(grid_step_mm=.1))
        omitted = dict(router={})
        cases = [
            (declared, problem(MM // 2), 3, (.1, 'declared')),
            (omitted, problem(650_000), 0, (.025, 'fine-pitch-pads')),
            (omitted, problem(MM), 0, (None, 'router-default')),
            (omitted, problem(650_000, nets=('A', 'A')), 0, (None, 'router-default')),
            (omitted, problem(MM), 1, (.025, 'after-failed-route')),
        ]
        for config, board, trial, expected in cases:
            with self.subTest(expected=expected, trial=trial):
                self.assertEqual(job.router_grid(config, board, trial), expected)


class RepeatedPadRefusalTests(unittest.TestCase):
    def test_refusal_lists_the_lands_of_a_repeated_pad_number(self):
        # A SOT-223 lead and tab share one number.
        facts = board()
        pads = next(fp for fp in facts['footprints'] if fp['ref'] == 'U1')['pads']
        pads.append(dict(pads[0], uuid='tab-land', size_nm=[3_800_000, 2_150_000]))
        with self.assertRaisesRegex(job.JobRefusal, r'choose a land.*"U1\.1".*tab-land'):
            job._locality_grade(facts, declaration())


if __name__ == '__main__':
    unittest.main()
