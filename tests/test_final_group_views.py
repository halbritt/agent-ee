"""AGENTEE-479 part 2: final_checks renders supplemental group views of the saved final board.

Every board here is the synthetic native fixture of test_placement_job; the retained esp_prog
final-a1 control runs only when AGENT_EE_ESP_PROG_INPUTS / AGENT_EE_ESP_PROG_FINAL name it.
"""
import hashlib
import json
import os
from pathlib import Path
import unittest

from agent_ee import placement_job as job
from agent_ee.storage import digest, identity
from test_placement_job import Fixture, groups, intent
from test_placement_job_guards import materialize
import test_placement_job as fixtures
from toolchain import require_toolchain


class FinalGroupViewTests(Fixture):
    def _final(self, label, out_groups=None, **kwargs):
        import pcbnew
        problem, _ = self.problem()
        poses = fixtures.NativeTransformTests._evaluate(self, problem, {})['poses']
        placed = materialize(self.board, poses, self.dir / f'placed-{label}')
        placed['directory'] = str(self.dir / f'placed-{label}')
        job.drc(Path(placed['board']), self.dir / f'placed-{label}', label='native-drc')
        routed = {'status': 'routed', 'board': placed['board']}
        final = job.final_checks(routed, placed, job._owners(pcbnew.LoadBoard(placed['board'])), set(),
                                 self.dir / f'final-{label}', out_groups or groups(), intent(),
                                 input_board=self.board, input_board_sha256=digest(self.board), **kwargs)
        return placed, routed, final

    def test_final_board_views_metrics_and_input_identities_agree(self):
        placed, routed, final = self._final('bound')
        views = final['group_views']
        self.assertEqual(views['status'], 'rendered', views['problems'])
        self.assertTrue(final['evidence_complete'])
        out = Path(views['directory'])
        manifest = json.loads((out / views['manifest']).read_bytes())
        self.assertEqual(digest(out / views['manifest']), views['manifest_sha256'])
        # Same saved board for the record, the metrics and both annotated views.
        self.assertEqual(views['board_sha256'], final['board_sha256'])
        self.assertEqual(manifest['board_sha256'], digest(Path(final['board'])))
        self.assertEqual(manifest['schema'], job.GROUP_VIEWS_SCHEMA)
        # Raw input identity is bound separately from the final board (the synthetic fixture saves
        # the unchanged poses, so here both files happen to carry the same bytes; the distinct-bytes
        # case is covered below).
        self.assertEqual(views['input_board_sha256'], digest(self.board))
        self.assertEqual(manifest['input_board_sha256'], digest(self.board))
        self.assertEqual(manifest['snapshots']['input_board'], 'source/input-board/board.kicad_pcb')
        self.assertEqual(digest(out / manifest['snapshots']['input_board']), digest(self.board))
        self.assertEqual(manifest['canonical_groups_sha256'], identity(groups()))
        self.assertEqual(views['canonical_groups_sha256'], identity(groups()))
        for side, face in (('top', 'F'), ('bottom', 'B')):
            path = out / views['views'][side]['path']
            self.assertTrue(path.is_file())
            self.assertEqual(digest(path), views['views'][side]['sha256'])
            self.assertEqual(manifest['views'][side]['sha256'], views['views'][side]['sha256'])
            self.assertEqual(views['views'][side]['face'], face)
        declared = {r for g in groups()['groups'] for r in g['refs']}
        self.assertEqual({m['reference'] for m in manifest['members']}, declared)
        self.assertIs(views['acceptance_assessed'], False)
        self.assertIs(manifest['acceptance_assessed'], False)
        self.assertEqual(job.verify_group_views(final), [])
        # Views are supplemental: the native verdicts are unchanged by their presence.
        self.assertGreater(final['opens'], 0)
        self.assertEqual(job._completion_status(routed, placed, final), 'verification_failed')
        self.assertIn('R2_member_to_anchor_links', json.dumps(final['metrics']['group_links']))

    def test_changed_or_missing_view_artifact_is_visible(self):
        _, _, final = self._final('tamper')
        out = Path(final['group_views']['directory'])
        top = out / final['group_views']['views']['top']['path']
        original = top.read_bytes()
        top.write_bytes(original + b'<!-- edited -->\n')
        self.assertEqual(job.verify_group_views(final), ['top view file changed: top.svg'])
        top.write_bytes(original)
        bottom = out / final['group_views']['views']['bottom']['path']
        bottom.unlink()
        self.assertEqual(job.verify_group_views(final), ['bottom view file missing: bottom.svg'])
        (out / 'manifest.json').write_bytes(b'{}')
        self.assertIn('group view manifest missing or changed', job.verify_group_views(final))

    def test_missing_view_evidence_is_reported_not_silent(self):
        # The helper refuses an output directory that already exists: a realistic failed render.
        (self.dir / 'final-refused-group-views').mkdir()
        _, _, final = self._final('refused')
        views = final['group_views']
        self.assertEqual(views['status'], 'missing')
        self.assertFalse(final['evidence_complete'])
        self.assertTrue(views['problems'] and 'group view helper exit' in views['problems'][0], views['problems'])
        self.assertIn('FileExistsError', views['problems'][0])
        self.assertEqual(views['views'], {})
        self.assertIsNone(views['manifest'])
        self.assertTrue(job.verify_group_views(final)[0].startswith('group views were not rendered'))
        # A raw input whose bytes differ from the final board keeps its own hash in the record.
        raw = self.dir / 'raw-input.kicad_pcb'
        raw.write_bytes(self.board.read_bytes() + b'\n')
        result = job._group_views(Path(final['board']), groups(), self.dir / 'views-distinct-input', input_board=raw,
                                  input_board_sha256=digest(raw), clock=job.Clock(120))
        self.assertEqual(result['status'], 'rendered', result['problems'])
        self.assertEqual(result['input_board_sha256'], digest(raw))
        self.assertNotEqual(result['input_board_sha256'], result['board_sha256'])
        self.assertEqual(result['board_sha256'], final['board_sha256'])
        # Eligibility still comes from the native checks alone.
        self.assertEqual(final['eligibility'], 'INELIGIBLE')
        self.assertEqual(final['integrity'], 'preserved')
        # A raw input that no longer matches its captured identity is refused before rendering.
        result = job._group_views(Path(final['board']), groups(), self.dir / 'views-wrong-input', input_board=self.board,
                                  input_board_sha256='0' * 64, clock=job.Clock(60))
        self.assertEqual((result['status'], result['problems']),
                         ('missing', ['raw input board on disk does not match the captured input identity']))
        self.assertFalse((self.dir / 'views-wrong-input').exists())

    def test_cancellation_and_deadline_stop_the_view_child_and_preserve_the_incumbent(self):
        import pcbnew
        problem, _ = self.problem()
        poses = fixtures.NativeTransformTests._evaluate(self, problem, {})['poses']
        placed = materialize(self.board, poses, self.dir / 'placed-stop')
        before = digest(Path(placed['board']))
        cancel_dir = self.dir / 'job-stop'
        cancel_dir.mkdir()
        (cancel_dir / 'CANCEL').write_bytes(b'')
        with self.assertRaisesRegex(job.JobStopped, 'cancelled'):
            job._group_views(Path(placed['board']), groups(), self.dir / 'views-cancelled', input_board=self.board,
                             clock=job.Clock(60), cancel_dir=cancel_dir)
        self.assertFalse((self.dir / 'views-cancelled' / 'manifest.json').exists())
        with self.assertRaisesRegex(job.JobStopped, 'deadline'):
            job._group_views(Path(placed['board']), groups(), self.dir / 'views-expired', input_board=self.board,
                             clock=job.Clock(0))
        self.assertFalse((self.dir / 'views-expired' / 'manifest.json').exists())
        # A deadline that expires while the child renders kills it; nothing is reported as rendered.
        with self.assertRaisesRegex(job.JobStopped, 'deadline'):
            job._group_views(Path(placed['board']), groups(), self.dir / 'views-cut', input_board=self.board,
                             clock=job.Clock(0.3))
        self.assertFalse((self.dir / 'views-cut' / 'manifest.json').exists())
        self.assertEqual(digest(Path(placed['board'])), before)


ESP = Path(os.environ.get('AGENT_EE_ESP_PROG_INPUTS', '/nonexistent'))
ESP_FINAL = Path(os.environ.get('AGENT_EE_ESP_PROG_FINAL', '/nonexistent'))


@unittest.skipUnless((ESP / 'groups.json').exists() and (ESP_FINAL / 'board.kicad_pcb').exists(),
                     'retained esp_prog inputs/final-a1 are not present on this host')
class RetainedEspProgViewTests(unittest.TestCase):
    def test_final_a1_views_bind_the_retained_final_and_raw_input_boards(self):
        import tempfile
        require_toolchain(native=True)
        self.assertEqual(digest(ESP_FINAL / 'board.kicad_pcb'), 'dfffc042e9f4c60dce9da0a25523443dd63dd3ecf56ee50a0a744a6cbdde92a1')
        self.assertEqual(digest(ESP / 'board.kicad_pcb'), 'b4aa540d031e18283216d806aa6288cf51260e7328f9aaeb13b55d4eac93eef7')
        groups_document = json.loads((ESP / 'groups.json').read_bytes())
        with tempfile.TemporaryDirectory() as tmp:
            result = job._group_views(ESP_FINAL / 'board.kicad_pcb', groups_document, Path(tmp) / 'views',
                                      input_board=ESP / 'board.kicad_pcb', input_board_sha256=digest(ESP / 'board.kicad_pcb'),
                                      clock=job.Clock(240))
            self.assertEqual(result['status'], 'rendered', result['problems'])
            self.assertEqual(result['board_sha256'], 'dfffc042e9f4c60dce9da0a25523443dd63dd3ecf56ee50a0a744a6cbdde92a1')
            self.assertEqual(result['input_board_sha256'], 'b4aa540d031e18283216d806aa6288cf51260e7328f9aaeb13b55d4eac93eef7')
            self.assertEqual(result['canonical_groups_sha256'], identity(groups_document))
            self.assertEqual(result['members'], 16)
            self.assertIs(result['acceptance_assessed'], False)
            manifest = json.loads((Path(tmp) / 'views' / result['manifest']).read_bytes())
            self.assertEqual(sorted(manifest['views']), ['bottom', 'top'])
            self.assertEqual(digest(ESP_FINAL / 'board.kicad_pcb'), result['board_sha256'])


if __name__ == '__main__':
    unittest.main()
