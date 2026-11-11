"""AGENTEE-640: the placement job accepts a fully routed, clean board.

A routed board is eligible when native DRC with refilled zones reports no open
connection and no error beyond those of the input board, its protected
content is unchanged and its declared checks pass. Every board here is the
synthetic fixture of ``test_placement_job`` with its parts spread and locked,
routed by hand through pcbnew or by the job's own router; no third-party board
is used.
"""
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from agent_ee import native_run, placement_job as job
from agent_ee.storage import digest
import test_placement_job as fixtures
from test_placement_job import Fixture, groups, intent, MM
from test_placement_job_guards import materialize
from toolchain import require_kicad_route

# Millimetres. The fixture piles its three netted parts at the board centre.
PLACES = {'R1': (7, 9), 'C1': (7, 6), 'U1': (12, 6)}
# One F.Cu path per connection, through the pad centres of the spread parts.
ROUTES = {'A': [[(6.25, 9), (6.25, 6)], [(6.25, 6), (6.25, 2), (13.5, 2), (13.5, 6)]],
          'B': [[(7.75, 6), (9.5, 6), (9.5, 5.4), (10.8, 5.4)]],
          'GND': [[(7.75, 9), (10.8, 9), (10.8, 6.6)]]}
# A track of net A that ends on a GND pad.
SHORT = {'A': [[(13.5, 6), (10.9, 6.6)]]}


def spread_and_lock(pcbnew, board):
    for fp in board.GetFootprints():
        if fp.GetReference() in PLACES:
            x, y = PLACES[fp.GetReference()]
            fp.SetPosition(pcbnew.VECTOR2I(x * MM, y * MM))
        fp.SetLocked(True)


def enable_teardrops(pcbnew, board):
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if pad.GetNetname():
                pad.SetTeardropsEnabled(True)


def crowded_part(pcbnew, board):
    """A locked part whose two through-hole pads, each on a net of its own, sit 0.1 mm apart.

    The clearance is 0.2 mm, so the board reports an error before anything is routed."""
    part = pcbnew.FOOTPRINT(board)
    part.SetReference('X1')
    part.SetValue('X1')
    part.Reference().SetVisible(False)
    part.Value().SetVisible(False)
    board.Add(part)
    for number, (name, x) in enumerate((('P', -550_000), ('Q', 550_000)), 1):
        net = pcbnew.NETINFO_ITEM(board, name)
        board.Add(net)
        pad = pcbnew.PAD(part)
        pad.SetNumber(str(number))
        pad.SetAttribute(pcbnew.PAD_ATTRIB_PTH)
        pad.SetShape(pcbnew.PADSTACK.ALL_LAYERS, pcbnew.PAD_SHAPE_CIRCLE)
        pad.SetSize(pcbnew.PADSTACK.ALL_LAYERS, pcbnew.VECTOR2I(MM, MM))
        pad.SetDrillSize(pcbnew.VECTOR2I(500_000, 500_000))
        pad.SetLayerSet(pad.PTHMask())
        part.Add(pad)
        pad.SetFPRelativePosition(pcbnew.VECTOR2I(x, 0))
        pad.SetNet(net)
    part.SetPosition(pcbnew.VECTOR2I(16 * MM, 3 * MM))
    part.SetLocked(True)


def cut_corner(pcbnew, board):
    """Five straight edges: the fixture rectangle less its lower-left corner."""
    for item in [d for d in board.GetDrawings() if d.GetLayer() == pcbnew.Edge_Cuts]:
        board.Remove(item)
    corners = [(0, 0), (20, 0), (20, 12), (3, 12), (0, 9)]
    for a, b in zip(corners, corners[1:] + corners[:1]):
        board.Add(fixtures._segment(pcbnew, board, pcbnew.Edge_Cuts, (a[0] * MM, a[1] * MM), (b[0] * MM, b[1] * MM)))


def add_tracks(pcbnew, board, routes=ROUTES):
    for net, paths in routes.items():
        for path in paths:
            for a, b in zip(path, path[1:]):
                track = pcbnew.PCB_TRACK(board)
                track.SetLayer(pcbnew.F_Cu)
                track.SetWidth(200_000)
                track.SetNet(board.FindNet(net))
                track.SetStart(pcbnew.VECTOR2I(int(a[0] * MM), int(a[1] * MM)))
                track.SetEnd(pcbnew.VECTOR2I(int(b[0] * MM), int(b[1] * MM)))
                board.Add(track)


def teardrop_zones(path):
    import pcbnew
    return sum(1 for zone in pcbnew.LoadBoard(str(path)).Zones() if zone.IsTeardropArea())


def error_counts(final):
    return {cause: row['count'] for cause, row in final['errors'].items()}


class RoutedBoard(Fixture):
    """The job's own steps around a routed board: input DRC, the checked candidate, the final check."""

    def setUp(self):
        super().setUp()
        self.change(self.board, spread_and_lock)

    def change(self, path, *changes):
        import pcbnew
        board = pcbnew.LoadBoard(str(path))
        for change in changes:
            change(pcbnew, board)
        pcbnew.SaveBoard(str(path), board)
        self.project_beside(path)

    @staticmethod
    def project_beside(path):
        """KiCad writes its own project file when it saves a board; put the authored one back."""
        Path(path).with_suffix('.kicad_pro').write_text('{}\n')
        Path(path).with_suffix('.kicad_prl').unlink(missing_ok=True)

    def candidate(self):
        """The unrouted board as the job keeps it before routing, and the native DRC of the input."""
        self.normalized = job.normalize_intent(intent(), groups())
        problem, record = job.capture(self.board, self.normalized)
        self.assertTrue(record['routing_only'])
        poses = fixtures.NativeTransformTests._evaluate(self, problem, {})['poses']
        placed = materialize(self.board, poses, self.dir / 'placed')
        self.assertEqual((placed['integrity'], placed['moved']), ('preserved', []))
        job.drc(Path(placed['board']), self.dir / 'placed', refill=True, label='native-drc')
        (self.dir / 'input-drc').mkdir()
        base, _ = job.drc(self.board, self.dir / 'input-drc', refill=True, label='native-drc')
        return dict(placed, directory=str(self.dir / 'placed'), moved_footprints=placed['moved']), base

    def routed(self, placed, *changes, name='routed', refill_and_save=False):
        """A router's result: the candidate with copper, saved beside the project it was given."""
        path = self.dir / name / 'board.kicad_pcb'
        path.parent.mkdir()
        shutil.copyfile(placed['board'], path)
        self.change(path, add_tracks, *changes)
        if refill_and_save:
            # What a router does before it grades its board: KiCad refills the zones and saves.
            done = job._run_child(native_run.drc_argv(path, path.with_name('refill.json'), refill=True, save_board=True),
                                  clock=job.Clock(120))
            self.assertEqual(done.returncode, 0, done.stderr)
            self.project_beside(path)
        return {'status': 'routed', 'board': str(path), 'router_accepted': True}

    def final(self, routed, placed, base, label='final'):
        import pcbnew
        return job.final_checks(routed, placed, job._owners(pcbnew.LoadBoard(str(self.board))), job.error_keys(base),
                                self.dir / label, self.normalized, input_board=self.board,
                                input_board_sha256=digest(self.board))


class CleanBoardTests(RoutedBoard):
    def test_fully_routed_clean_board_is_eligible_and_completes(self):
        placed, base = self.candidate()
        self.assertEqual((len(base['unconnected_items']), sum(job.error_keys(base).values())), (4, 0))
        routed = self.routed(placed)
        final = self.final(routed, placed, base)
        self.assertEqual((final['opens'], final['identity_problems'], final['rejections']), (0, [], []))
        self.assertEqual(error_counts(final), {'pre_existing': 0, 'placement_introduced': 0, 'routing_introduced': 0})
        self.assertEqual((final['integrity'], final['eligibility'], final['physical_checks']),
                         ('preserved', 'eligible', 'no native errors'))
        self.assertEqual(job._completion_status(routed, placed, final), 'completed')
        # The same board without its copper is rejected, and the record says why.
        unrouted = {'status': 'routed', 'board': placed['board'], 'router_accepted': True}
        bare = self.final(unrouted, placed, base, 'final-unrouted')
        self.assertEqual((bare['eligibility'], bare['physical_checks'], bare['rejections']),
                         ('INELIGIBLE', 'FAILED', ['4 open connection(s)']))
        self.assertEqual(job._completion_status(unrouted, placed, bare), 'verification_failed')
        self.assertEqual(job._rejection_reason('verification_failed', unrouted, bare), '4 open connection(s)')

    def test_teardrop_zones_kicad_builds_on_a_routed_board_are_routing_copper(self):
        self.change(self.board, enable_teardrops)
        placed, base = self.candidate()
        routed = self.routed(placed, refill_and_save=True)
        self.assertEqual(teardrop_zones(placed['board']), 0)
        built = teardrop_zones(routed['board'])
        self.assertGreater(built, 0)             # from the pad settings and the tracks that reach the pads
        final = self.final(routed, placed, base)
        self.assertEqual((final['identity_problems'], final['rejections']), ([], []))
        self.assertEqual((final['integrity'], final['eligibility']), ('preserved', 'eligible'))
        self.assertEqual(job._completion_status(routed, placed, final), 'completed')
        self.assertEqual(teardrop_zones(final['board']), built)      # the board handed on keeps them
        # Only the comparison of a routed board leaves them out. Where nothing was routed they are content.
        data = Path(routed['board']).read_bytes()
        zones = lambda **options: Counter(node[0] for node in job._non_footprint_nodes(data, **options))['zone']
        self.assertEqual((zones(), zones(routing_owned=True)), (built, 0))

    def test_the_teardrop_setting_of_a_pad_stays_protected(self):
        self.change(self.board, enable_teardrops)
        placed, base = self.candidate()

        def switch_off(pcbnew, board):
            next(iter(board.FindFootprintByReference('R1').Pads())).SetTeardropsEnabled(False)
        routed = self.routed(placed, switch_off, refill_and_save=True)
        final = self.final(routed, placed, base)
        self.assertEqual(final['identity_problems'], [
            'router changed protected native content (footprints, pads, graphics, setup, outline or drawings): '
            'only on the placed board: footprint 1; only on the routed board: footprint 1'])
        self.assertEqual((final['integrity'], final['eligibility']), ('violated', 'INELIGIBLE'))
        self.assertEqual(job._completion_status(routed, placed, final), 'integrity_failed')

    def test_errors_the_unrouted_input_already_has_do_not_reject_the_routed_board(self):
        self.change(self.board, crowded_part)
        placed, base = self.candidate()
        existing = sum(job.error_keys(base).values())
        self.assertGreater(existing, 0)          # the crowded pads, as native DRC reports the input
        routed = self.routed(placed)
        final = self.final(routed, placed, base)
        self.assertEqual(error_counts(final), {'pre_existing': existing, 'placement_introduced': 0, 'routing_introduced': 0})
        self.assertEqual((final['opens'], final['rejections'], final['eligibility']), (0, [], 'eligible'))
        self.assertEqual(final['physical_checks'], f'no introduced native errors ({existing} already in the input)')
        self.assertEqual(job._completion_status(routed, placed, final), 'completed')
        # An error the routing adds still rejects the board, beside the ones the input had.
        shorted = self.routed(placed, lambda pcbnew, board: add_tracks(pcbnew, board, SHORT), name='shorted')
        bad = self.final(shorted, placed, base, 'final-shorted')
        self.assertEqual((bad['errors']['pre_existing']['count'], bad['errors']['placement_introduced']['count']),
                         (existing, 0))
        self.assertGreater(bad['errors']['routing_introduced']['by_type'].get('shorting_items', 0), 0)
        self.assertEqual((bad['opens'], bad['integrity'], bad['eligibility'], bad['physical_checks']),
                         (0, 'preserved', 'INELIGIBLE', 'FAILED'))
        self.assertEqual(len(bad['rejections']), 1)
        self.assertRegex(bad['rejections'][0], r'^\d+ routing-introduced DRC error\(s\): .*shorting_items \d+')
        self.assertEqual(job._completion_status(shorted, placed, bad), 'verification_failed')
        self.assertEqual(job._rejection_reason('verification_failed', shorted, bad), bad['rejections'][0])

    def test_any_closed_outline_gets_its_views_and_a_missing_picture_decides_nothing(self):
        self.change(self.board, cut_corner)
        placed, base = self.candidate()
        routed = self.routed(placed)
        final = self.final(routed, placed, base)
        self.assertEqual(final['group_views']['status'], 'rendered', final['group_views']['problems'])
        self.assertTrue(final['evidence_complete'])
        self.assertEqual(job.verify_group_views(final), [])
        self.assertEqual((final['eligibility'], job._completion_status(routed, placed, final)), ('eligible', 'completed'))
        # The view helper refuses a directory that exists. The board is as good as before.
        (self.dir / 'final-unrendered-group-views').mkdir()
        final = self.final(routed, placed, base, 'final-unrendered')
        self.assertEqual(final['group_views']['status'], 'missing')
        self.assertFalse(final['evidence_complete'])
        self.assertEqual((final['rejections'], final['eligibility'], job._completion_status(routed, placed, final)),
                         ([], 'eligible', 'completed'))

    def test_a_view_helper_that_crashes_is_a_note_and_a_deadline_still_stops_the_job(self):
        crash = job.EnvironmentFailure('native-tool', 'python3 crashed (exit -11): ')
        with patch.object(job, '_run_child', side_effect=crash):
            views = job._group_views(self.board, groups(), self.dir / 'crashed-views', clock=job.Clock(60))
        self.assertEqual((views['status'], views['problems']),
                         ('missing', ['group view helper did not finish: python3 crashed (exit -11): ']))
        with patch.object(job, '_run_child', side_effect=job.JobStopped('deadline')):
            with self.assertRaisesRegex(job.JobStopped, 'deadline'):
                job._group_views(self.board, groups(), self.dir / 'late-views', clock=job.Clock(60))


class WholeJobTests(RoutedBoard):
    def test_job_completes_on_a_board_with_teardrops_and_errors_of_its_own(self):
        """The whole job with the real router: teardrop pads, and a part that breaks clearance by itself."""
        require_kicad_route()
        self.change(self.board, enable_teardrops, crowded_part)
        files = {'board': self.board, 'project': self.project, 'intent': self.dir / 'intent.json',
                 'groups': self.dir / 'groups.json'}
        files['intent'].write_text(json.dumps(intent()))
        files['groups'].write_text(json.dumps(groups()))
        config = {'schema': job.CONFIG_SCHEMA, 'budget_s': 300,
                  'inputs': {key: {'path': str(path), 'sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest()}
                             for key, path in files.items()},
                  'search': self.search(2, 1000),
                  'router': {'checkout': os.environ['AGENT_EE_KICAD_ROUTE_CHECKOUT'], 'budget_s': 60}}
        report = job.run_job(config, self.dir / 'job')
        final = report.get('final') or {}
        self.assertEqual(report['status'], 'completed', (report.get('reason'), report.get('route')))
        self.assertNotIn('reason', report)
        self.assertGreater(report['input_drc']['errors'], 0)
        self.assertEqual(error_counts(final), {'pre_existing': report['input_drc']['errors'],
                                               'placement_introduced': 0, 'routing_introduced': 0})
        self.assertEqual((final['opens'], final['identity_problems'], final['rejections'], final['eligibility']),
                         (0, [], [], 'eligible'))
        self.assertGreater(teardrop_zones(final['board']), 0)
        self.assertEqual(report['validity'], 'integrity-valid placement; physical checks ' + final['physical_checks'])
        self.assertIn('already in the input', final['physical_checks'])


class ErrorCountTests(unittest.TestCase):
    def test_a_finding_reported_more_often_than_in_the_input_is_introduced(self):
        # KiCad reports one pair of through-hole pads once per copper layer: same type, same items.
        pair = {'type': 'clearance', 'severity': 'error', 'items': [{'uuid': 'pad-1'}, {'uuid': 'pad-2'}]}
        note = {'type': 'lib_footprint_issues', 'severity': 'warning', 'items': [{'uuid': 'pad-1'}]}
        owners = {'pad-1': 'part', 'pad-2': 'part'}
        report = lambda times: {'violations': [pair] * times + [note]}
        count = lambda split: [len(split[cause]) for cause in ('pre_existing', 'placement_introduced', 'routing_introduced')]
        twice, thrice = job.error_keys(report(2)), job.error_keys(report(3))
        self.assertEqual((len(twice), sum(twice.values())), (1, 2))
        self.assertEqual(count(job.classify_errors(report(2), owners, twice, twice, moved=[])), [2, 0, 0])
        self.assertEqual(count(job.classify_errors(report(1), owners, twice, twice, moved=[])), [1, 0, 0])
        self.assertEqual(count(job.classify_errors(report(3), owners, twice, twice, moved=[])), [2, 0, 1])
        self.assertEqual(count(job.classify_errors(report(4), owners, twice, thrice, moved=[])), [2, 1, 1])
        self.assertEqual(count(job.classify_errors(report(3), owners, twice, moved=[])), [2, 1, 0])
        # A plain set of keys counts each finding once.
        self.assertEqual(count(job.classify_errors(report(2), owners, set(twice), set(twice), moved=[])), [1, 0, 1])
        # The caller's multisets are not consumed.
        self.assertEqual((sum(twice.values()), sum(thrice.values())), (2, 3))


class VerdictTests(unittest.TestCase):
    ELIGIBLE = {'integrity': 'preserved', 'edge_intent': {'status': 'pass'}, 'eligibility': 'eligible', 'rejections': []}

    def test_the_report_names_what_rejected_a_routed_board(self):
        accepted = {'status': 'routed', 'router_accepted': True}
        self.assertEqual(job._completion_status(accepted, {}, self.ELIGIBLE), 'completed')
        # The router grades its own result. A rejection there still decides, and the job repeats its words.
        graded = {'status': 'routed', 'router_accepted': False,
                  'classification': {'outcome': 'policy_failed', 'reasons': ['2 new vias below their net class', 'second']}}
        self.assertEqual(job._completion_status(graded, {}, self.ELIGIBLE), 'verification_failed')
        self.assertEqual(job._rejection_reason('verification_failed', graded, self.ELIGIBLE),
                         "the router's own grading rejected its result: 2 new vias below their net class; second")
        failed = dict(self.ELIGIBLE, eligibility='INELIGIBLE', rejections=['3 open connection(s)', 'edge intent check: failed'])
        self.assertEqual(job._rejection_reason('verification_failed', graded, failed),
                         "the router's own grading rejected its result: 2 new vias below their net class; second; "
                         '3 open connection(s); edge intent check: failed')
        self.assertEqual(job._rejection_reason('route_failed', {'status': 'no_candidate'}, {'status': 'unavailable'}),
                         'the router delivered no board: no_candidate')
        self.assertIn('exploratory route', job._rejection_reason('placement_failed', accepted, self.ELIGIBLE))
        # A final record that gives no reasons still yields a sentence.
        self.assertEqual(job._rejection_reason('verification_failed', accepted, {'eligibility': 'ineligible'}),
                         'final board is ineligible')


if __name__ == '__main__':
    unittest.main()
