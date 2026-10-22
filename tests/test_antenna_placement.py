"""AGENTEE-453: decidable ESP32-S3-WROOM-1 outside-board antenna placement gate."""
import copy
import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from toolchain import require_toolchain

from agent_ee import antenna_placement as ap
from agent_ee import candidate, checks
from agent_ee import footprint_library as fl
from agent_ee.baseline import freeze_evidence, validate_witnesses, verify_evidence
from agent_ee.capabilities import KINDS
from agent_ee.storage import write_json

import antenna_placement_fixture as fx


def _sha(data):
    return hashlib.sha256(data).hexdigest()


class DeclarationTests(unittest.TestCase):
    def setUp(self):
        for name, data in (('ESP32_GUIDELINES_SHA256', fx.GUIDANCE_BYTES),
                           ('ESP32_DATASHEET_SHA256', fx.DATASHEET_BYTES)):
            patcher = patch.object(fl, name, _sha(data))
            patcher.start()
            self.addCleanup(patcher.stop)

    def parse(self, doc):
        return ap.parse_declaration(json.dumps(doc).encode())

    def test_registered_kind_and_predicates(self):
        self.assertIn('antenna_placement', KINDS)
        self.assertIn('antenna_placement', checks.CHECK_FUNCS)
        doc = self.parse(fx.declaration())
        self.assertEqual(ap.predicates(doc)[0], 'antenna-placement:U1:sources')
        self.assertEqual(len(ap.predicates(doc)), len(ap.SECTIONS))

    def test_only_the_reviewed_outside_board_snapshot_is_accepted(self):
        mutations = {
            'cutout-case': lambda d: d.update(policy=fl.esp32_s3_wroom_1_placement_policy(
                'board_cutout', pdf_path=fx.GUIDANCE)),
            'figure-11-1-page': lambda d: d['policy']['source'].update(page=45, locator='Figure 11-1'),
            'guidance-hash': lambda d: d['policy']['source'].update(sha256=fl.ESP32_DATASHEET_SHA256),
            'datasheet-hash': lambda d: d['module_datasheet'].update(sha256=_sha(b'other')),
            'footprint-hash': lambda d: d['footprint'].update(sha256=_sha(b'other')),
            'footprint-name': lambda d: d['footprint'].update(path='cad/RF_Module.pretty/Other.kicad_mod'),
            'unsafe-path': lambda d: d['module_datasheet'].update(path='../datasheet.pdf'),
            'same-source': lambda d: d['module_datasheet'].update(path=fx.GUIDANCE),
            'bool-edge': lambda d: d.update(edge=dict(start_mm=[True, 0], end_mm=[60, 0])),
            'point-edge': lambda d: d.update(edge=dict(start_mm=[0, 0], end_mm=[0, 0])),
            'clearance-field': lambda d: d.update(clearance_mm=15),
            'schema': lambda d: d.update(schema='agent-ee.antenna-placement-declaration/0'),
        }
        self.parse(fx.declaration())
        for name, mutate in mutations.items():
            doc = fx.declaration()
            mutate(doc)
            with self.subTest(name), self.assertRaises(ValueError):
                self.parse(doc)

    def test_geometry_is_the_pinned_figure_11_1_module_frame(self):
        geometry = ap.module_geometry()
        self.assertEqual(geometry['antenna'], [-9_000_000, -12_750_000, 9_000_000, -6_750_000])
        self.assertEqual(geometry['body'], [-9_000_000, -6_750_000, 9_000_000, 12_750_000])
        self.assertNotIn('esp32_s3_wroom_1_keepout_clearance_mm', geometry['facts'])

    def test_frame_transform_round_trips_all_quarter_turns(self):
        for turns in range(4):
            for point in ([9, -6], [-3, 12], [0, 0]):
                self.assertEqual(ap.to_local(ap.to_board(point, [5, 7], turns), [5, 7], turns), point)
        self.assertEqual(ap.quarter_turns(-90.0), 3)
        self.assertIsNone(ap.quarter_turns(45.0))


class NativeAntennaPlacementTests(unittest.TestCase):
    """Registered check on native placed boards, run through capture and the sandbox."""

    def setUp(self):
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name, data in (('ESP32_GUIDELINES_SHA256', fx.GUIDANCE_BYTES),
                           ('ESP32_DATASHEET_SHA256', fx.DATASHEET_BYTES)):
            patcher = patch.object(fl, name, _sha(data))
            patcher.start()
            self.addCleanup(patcher.stop)
        self.runs = 0

    def run_board(self, doc=None, *, guidance=True, datasheet=True, footprint=True, guidance_bytes=None,
                  **board):
        self.runs += 1
        source = self.root / f'source-{self.runs}'
        source.mkdir()
        fl.capture_esp32_s3_wroom_1(source / 'cad')
        fx.make_board(source / 'board.kicad_pcb', source / 'cad/RF_Module.pretty', **board)
        if not footprint:
            (source / fx.FOOTPRINT).unlink()
        (source / 'sources').mkdir()
        if guidance:
            (source / fx.GUIDANCE).write_bytes(guidance_bytes or fx.GUIDANCE_BYTES)
        if datasheet:
            (source / fx.DATASHEET).write_bytes(fx.DATASHEET_BYTES)
        write_json(source / 'antenna.json', doc or fx.declaration())
        (source / 'requirements.md').write_text('R-ant: ESP32 antenna outside-board placement (bare board)')
        scope = {'schema': 'agent-ee.scope/2', 'inputs': [{'path': str(source), 'at': 'project'}],
                 'board': 'project/board.kicad_pcb',
                 'checks': [{'id': 'antenna', 'kind': 'antenna_placement',
                             'params': {'declaration': 'project/antenna.json'}}],
                 'baseline': {'requirements': [{'id': 'R-ant', 'statement': 'Outside-board antenna placement',
                     'source': {'path': 'project/requirements.md', 'locator': 'R-ant'}, 'checks': ['antenna']}],
                     'targets': ['project/requirements.md'],
                     'reference_architectures': ['project/requirements.md']}}
        scope_path = self.root / f'scope-{self.runs}.json'
        scope_path.write_text(json.dumps(scope))
        run = self.root / f'run-{self.runs}'
        run.mkdir()
        candidate.capture(scope_path, run)
        [result] = checks.run_checks(run, time.monotonic() + 600)
        return run, result

    def declaration(self, **kwargs):
        doc = fx.declaration(**kwargs)
        for blob in (doc['policy']['source'], doc['module_datasheet'], doc['footprint']):
            blob['path'] = 'project/' + blob['path']
        return doc

    def sections(self, result):
        return {name: s['status'] for name, s in result['details']['sections'].items()}

    def assert_only(self, result, outcome, failing):
        self.assertEqual(result['outcome'], outcome, result['summary'])
        bad = {n for n, s in self.sections(result).items() if s != 'pass'}
        self.assertEqual(bad, set(failing), result['details']['sections'])

    def test_correct_outside_board_pose_with_retained_keepout_passes(self):
        run, result = self.run_board(self.declaration())
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result['summary'])
        self.assertEqual(set(self.sections(result).values()), {'pass'})
        report = json.loads((run / 'work/antenna/antenna-placement.json').read_text())
        manifest = json.loads((run / 'candidate.json').read_text())
        self.assertEqual(report['board_sha256'], manifest['files']['project/board.kicad_pcb'])
        self.assertEqual(report['declaration_sha256'], manifest['files']['project/antenna.json'])
        self.assertEqual(report['candidate_id'], result['candidate_id'])
        self.assertEqual(report['context']['source']['page'], 33)
        self.assertIsNone(report['context']['source_derived_minimum'])
        self.assertEqual(report['sections']['feed_side_corner']['feed_side_gap_nm'], 2_000_000)
        self.assertEqual(report['sections']['feed_point_edge']['feed_point_offset_nm'], 0)
        self.assertEqual(set(report['obligations']),
                         {'housing-pocket', 'housing-clearance', 'nearby-metal', 'support', 'rf-performance'})
        self.assertEqual(result['limitations'], [ap.LIMITATIONS])
        self.assertEqual(result['details']['gate_findings'], [])
        freeze_evidence(run, [result])
        verify_evidence(run, [result], required=True)
        claim = ap.bare_board_claim(run, result)
        self.assertEqual(claim['claim'], 'bare-board-antenna-placement')
        self.assertEqual(claim['open_obligations']['rf-performance'], 'bench')

    def test_rotated_module_on_the_declared_right_edge_passes(self):
        # -90 degrees points the antenna at +x; the feed side (+x local) faces the bottom-right corner.
        _, result = self.run_board(self.declaration(edge=fx.RIGHT), anchor=(53.25, 29.0), rotation=-90)
        self.assert_only(result, 'pass', [])

    def test_antenna_on_a_different_edge_than_declared_is_a_defect(self):
        run, result = self.run_board(self.declaration(edge=fx.TOP), anchor=(53.25, 29.0), rotation=-90)
        self.assert_only(result, 'defect', ['feed_side_corner'])
        self.assertIn('different board edge', result['details']['sections']['feed_side_corner']['reason'])
        self.assertEqual(result['details']['gate_findings'],
                         [{'check_id': 'antenna', 'entity': 'U1:feed-side-corner',
                           'kind': 'antenna-placement/feed-side-corner'}])
        freeze_evidence(run, [result])
        verify_evidence(run, [result], required=True)
        validate_witnesses(run, [{'requirement_id': 'R-ant', 'check_id': 'antenna'}],
                           [{'path': 'checks/antenna.json', 'locator': 'details.gate_findings'}])

    def test_antenna_pointing_into_the_board_is_a_defect(self):
        _, result = self.run_board(self.declaration(), rotation=180)
        self.assertEqual(result['outcome'], 'defect')
        self.assertEqual(self.sections(result)['antenna_outside_board'], 'defect')

    def test_feed_point_away_from_the_corner_is_a_defect(self):
        for name, x in (('opposite-corner', 11.0), ('mid-edge', 30.0)):
            with self.subTest(name):
                _, result = self.run_board(self.declaration(), anchor=(x, 6.75))
                self.assert_only(result, 'defect', ['feed_side_corner'])

    def test_collinear_edge_pieces_are_one_edge_for_the_corner_gap(self):
        # A vertex at x=30 must not shorten the 40 mm feed-side gap of the opposite-corner pose.
        _, result = self.run_board(self.declaration(), anchor=(11.0, 6.75), split_top=30.0)
        self.assert_only(result, 'defect', ['feed_side_corner'])
        self.assertEqual(result['details']['sections']['feed_side_corner']['feed_side_gap_nm'], 40_000_000)
        _, result = self.run_board(self.declaration(), split_top=30.0)
        self.assert_only(result, 'pass', [])

    def test_body_overhang_leaves_feed_point_distance_unknown(self):
        # The pod's earlier boards placed U1 0.25 mm further out; no source bounds "close".
        _, result = self.run_board(self.declaration(), anchor=(49.0, 6.5))
        self.assert_only(result, 'unknown', ['feed_point_edge'])
        self.assertEqual(result['execution'], 'unsupported')
        self.assertEqual(result['details']['sections']['feed_point_edge']['feed_point_offset_nm'], 250_000)

    def test_removed_or_altered_keepout_is_a_defect(self):
        for option in ('drop_zone', 'loosen_zone'):
            with self.subTest(option):
                _, result = self.run_board(self.declaration(), **{option: True})
                self.assert_only(result, 'defect', ['keepout_retained'])

    def test_copper_inside_the_keepout_is_a_defect(self):
        _, result = self.run_board(self.declaration(), intrusion=True)
        self.assert_only(result, 'defect', ['keepout_clear'])
        [item] = result['details']['sections']['keepout_clear']['items']
        self.assertEqual((item['native_class'], item['layers']), ('PCB_TRACK', ['F.Cu']))

    def test_missing_guidance_or_feed_point_source_is_unknown_never_pass(self):
        for missing in ('guidance', 'datasheet'):
            with self.subTest(missing):
                run, result = self.run_board(self.declaration(), **{missing: False})
                self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'))
                self.assertNotIn('pass', self.sections(result).values())
                freeze_evidence(run, [result])
                with self.assertRaisesRegex(ValueError, 'failed criterion'):
                    validate_witnesses(run, [{'requirement_id': 'R-ant', 'check_id': 'antenna'}],
                                       [{'path': 'checks/antenna.json', 'locator': 'outcome'}])
                with self.assertRaisesRegex(ValueError, 'did not pass'):
                    ap.bare_board_claim(run, result)

    def test_missing_footprint_capture_is_unknown(self):
        _, result = self.run_board(self.declaration(), footprint=False)
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'))
        self.assertEqual(set(self.sections(result).values()), {'unknown'})

    def test_missing_edge_declaration_and_back_side_pose_are_unknown(self):
        _, result = self.run_board(self.declaration(edge=None))
        self.assert_only(result, 'unknown', ['feed_side_corner'])
        _, result = self.run_board(self.declaration(), flipped=True)
        self.assertEqual(result['outcome'], 'unknown')
        self.assertEqual(self.sections(result)['footprint'], 'unknown')

    def test_absent_reference_is_a_defect(self):
        _, result = self.run_board(self.declaration(), reference='U9')
        self.assertEqual(result['outcome'], 'defect')
        self.assertEqual(self.sections(result)['footprint'], 'defect')

    def test_substituted_guidance_bytes_refuse_capture(self):
        with self.assertRaisesRegex(ValueError, 'differs from its declared sha256'):
            self.run_board(self.declaration(), guidance_bytes=b'Owned synthetic lookalike stand-in.\n')

    def test_sealed_report_tamper_and_cross_candidate_replay_refuse(self):
        run, result = self.run_board(self.declaration())
        freeze_evidence(run, [result])
        other_run, other = self.run_board(self.declaration(), anchor=(48.0, 6.75))
        self.assertEqual(other['outcome'], 'pass')
        self.assertNotEqual(other['candidate_id'], result['candidate_id'])
        with self.assertRaisesRegex(ValueError, 'not bound to this candidate'):
            ap.bare_board_claim(other_run, result)
        forged = copy.deepcopy(result)
        forged['candidate_id'] = other['candidate_id']
        with self.assertRaisesRegex(ValueError, 'differs from the candidate'):
            ap.bare_board_claim(other_run, dict(forged, details=other['details'] | {'board_sha256': 'f' * 64}))
        path = run / 'work/antenna/antenna-placement.json'
        report = json.loads(path.read_text())
        report['sections']['keepout_clear']['status'] = 'pass'
        report['board_sha256'] = '0' * 64
        path.write_text(json.dumps(report))
        with self.assertRaises(ValueError):
            verify_evidence(run, [result], required=True)
        with self.assertRaisesRegex(ValueError, 'differs from the candidate'):
            ap.bare_board_claim(run, result)


if __name__ == '__main__':
    unittest.main()
