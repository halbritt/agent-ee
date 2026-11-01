"""Choices a placement job makes from the board when a plan does not state them."""
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
            (declared, problem(MM // 2), True, (.1, 'declared')),
            (omitted, problem(650_000), False, (.025, 'fine-pitch-pads')),
            (omitted, problem(MM), False, (None, 'router-default')),
            (omitted, problem(650_000, nets=('A', 'A')), False, (None, 'router-default')),
            (omitted, problem(MM), True, (.025, 'after-failed-route')),
        ]
        for config, board, failed, expected in cases:
            with self.subTest(expected=expected, failed=failed):
                self.assertEqual(job.router_grid(config, board, after_failed_route=failed), expected)


class SilkGraphicConflictTests(unittest.TestCase):
    def test_only_findings_that_name_no_field_make_a_board_ineligible(self):
        def finding(kind, *descriptions):
            return dict(type=kind, items=[dict(description=text) for text in descriptions])
        graphics = finding('silk_overlap', 'Segment of R1 on F.Silkscreen', 'Polygon of U2 on F.Silkscreen')
        over_pad = finding('silk_over_copper', 'Segment of R1 on F.Silkscreen', 'Pad 1 [GND] of C3 on F.Cu')
        movable = finding('silk_overlap', 'Reference field of J2', 'Segment of R1 on F.Silkscreen')
        other = finding('lib_footprint_issues', 'Footprint U1')
        # Review counterexample: board text whose content happens to read like a field.
        text = finding('silk_over_copper', "PCB Text 'electric field of sensor' on F.Silkscreen", 'Pad 2 [A] of R1 on F.Cu')
        report = dict(violations=[graphics, over_pad, movable, other, text])
        self.assertEqual(job._silk_graphic_conflicts(report), [graphics, over_pad, text])


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
