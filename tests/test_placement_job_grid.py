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
        footprint_text = finding('silk_overlap', 'Footprint text of U1 (electric field of sensor)', 'Segment of R1 on F.Silkscreen')
        apostrophe = finding('silk_overlap', "Bob's field of U1 (note)", 'Segment of R1 on F.Silkscreen')
        report = dict(violations=[graphics, over_pad, movable, other, text, footprint_text, apostrophe])
        self.assertEqual(job._silk_graphic_conflicts(report), [graphics, over_pad, text, footprint_text])


class PackageTrackConflictTests(unittest.TestCase):
    def test_build_policy_rejects_a_dangling_track_that_artwork_cannot_fix(self):
        # Real design-compile I2C run r3: placement called this advisory,
        # then the unchanged package DRC rejected it with zero open nets.
        dangling = dict(type='track_dangling', severity='warning',
                        items=[dict(description='Track [/SDA]', uuid='stub')])
        library = dict(type='lib_footprint_issues', severity='warning', items=[])
        report = dict(violations=[library, dangling])
        self.assertEqual(job._unrepairable_track_warnings(report, reject_track_dangling=True), [dangling])
        self.assertEqual(job._unrepairable_track_warnings(report), [])
        self.assertEqual(job._unrepairable_track_warnings(dict(violations=[library]),
                                                         reject_track_dangling=True), [])


class CompletionStatusTests(unittest.TestCase):
    def test_no_hard_feasible_engine_state_is_reported_before_route_and_native_outcomes(self):
        # Benchmark r4 led-flasher attempt-004: every seed was engine-infeasible, the exploratory
        # route read verification_failed, and the generate loop retried the same search.
        passing = dict(integrity='preserved', edge_intent=dict(status='pass'), eligibility='eligible')
        routed = dict(status='routed', router_accepted=True)
        cases = [(dict(status='failed'), passing, 'route_failed'),
                 (dict(routed, router_accepted=False), passing, 'verification_failed'),
                 (routed, dict(passing, integrity='violated'), 'integrity_failed'),
                 (routed, dict(passing, edge_intent=dict(status='failed')), 'intent_failed'),
                 (routed, dict(passing, eligibility='ineligible'), 'verification_failed'),
                 (routed, passing, 'placement_failed')]
        for route, final, without_incumbent in cases:
            with self.subTest(expected=without_incumbent, route=route, final=final):
                self.assertEqual(job._completion_status(route, None, final, engine_feasible=False),
                                 'placement_infeasible')
                # Engine-feasible candidates that native checks rejected keep their own outcome.
                self.assertEqual(job._completion_status(route, None, final, engine_feasible=True),
                                 without_incumbent)


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
