"""Real receipted fab stages, corrected packages and fail-closed captured inputs."""

import csv
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from agent_ee import fab_stage, generate as g, rung1_campaign as rc, rung1_qualified as q
from agent_ee.stage_receipts import run_step, validate_receipt
from agent_ee.storage import digest, read_json, write_json
try:
    from toolchain import require_toolchain
except ImportError:
    from tests.toolchain import require_toolchain

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'fixtures/cpl-rotation'


def rotation_inputs(root):
    """Relocate owned supplier provenance, preserving the original raw bytes and hashes."""
    root = Path(root)
    root.mkdir()
    shutil.copyfile(FIXTURE / 'jlc-cpl-policy.json', root / 'jlc-cpl-policy.json')
    shutil.copytree(FIXTURE / 'supplier', root / 'supplier')
    for path in (root / 'supplier').glob('*.provenance.json'):
        record = read_json(path)
        record['path'] = record['path'].replace('design/supplier/', 'generation/cpl/supplier/')
        write_json(path, record)
    return root


def candidate(root):
    root = Path(root)
    project = root / 'project'
    project.mkdir(parents=True)
    for name in ('cpl.kicad_pcb', 'cpl.kicad_sch'):
        shutil.copyfile(FIXTURE / name, project / name)
    fab_stage.copy_rotation_inputs(rotation_inputs(root.parent / 'rotation-source'), root)
    return root


class InputTests(unittest.TestCase):
    def test_missing_policy_or_supplier_set_refuses_before_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = root / 'inputs'
            inputs.mkdir()
            with self.assertRaisesRegex(ValueError, 'policy is not captured'):
                fab_stage.export(inputs, root / 'out1', 'project/a.kicad_pcb', 'project/a.kicad_sch')
            cpl = inputs / fab_stage.CPL_ROOT
            cpl.mkdir(parents=True)
            shutil.copyfile(FIXTURE / 'jlc-cpl-policy.json', cpl / 'jlc-cpl-policy.json')
            with self.assertRaisesRegex(ValueError, 'supplier footprint set is missing'):
                fab_stage.export(inputs, root / 'out2', 'project/a.kicad_pcb', 'project/a.kicad_sch')
            self.assertFalse((root / 'out1/fab').exists())
            self.assertFalse((root / 'out2/fab').exists())

    def test_capture_refuses_links_and_changed_supplier_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = rotation_inputs(root / 'source')
            (source / 'link').symlink_to(source / 'jlc-cpl-policy.json')
            with self.assertRaisesRegex(ValueError, 'symlink'):
                fab_stage.copy_rotation_inputs(source, root / 'links')
            (source / 'link').unlink()
            raw = next((source / 'supplier').glob('*.raweasy.json'))
            raw.write_bytes(raw.read_bytes() + b' ')
            with self.assertRaisesRegex(ValueError, 'provenance size/sha256'):
                fab_stage.copy_rotation_inputs(source, root / 'tampered')

    def test_preview_required_cannot_mark_any_driver_done(self):
        reason = 'fab: cpl rotation PREVIEW_REQUIRED (bottom-side transform unvalidated)'
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = {'artifacts_id': 'a', 'candidate_id': 'c', 'files': {'work/cpl/cpl/cpl.csv': '1' * 64},
                         'rotation': {'status': 'PREVIEW_REQUIRED'}}
            record = rc.write_record(tmp, {}, verdict='criteria_satisfied', fab_artifacts=artifacts,
                                     recomputed=dict(artifacts), scaffold={'bound': True})
            self.assertEqual((record['done'], record['fab_rotation_blocker']), (False, reason))
            fab = {'rotation_status': 'PREVIEW_REQUIRED', 'manifest': 'x'}
            record = q.write_record(tmp, {}, verdict='criteria_satisfied', verify_exit=0, fab=fab)
            self.assertEqual((record['done'], record['fab_rotation_blocker']), (False, reason))
            # An allowed status still needs the derived package; an unknown or missing status never passes.
            self.assertFalse(q.write_record(tmp, {}, verdict='criteria_satisfied', verify_exit=0,
                                            fab={'rotation_status': 'ok', 'manifest': None})['done'])
            self.assertTrue(q.write_record(tmp, {}, verdict='criteria_satisfied', verify_exit=0,
                                           fab={'rotation_status': 'ok', 'manifest': 'x'})['done'])
            for fab in (None, {'manifest': 'x'}, {'rotation_status': 'unknown', 'manifest': 'x'}):
                self.assertFalse(q.write_record(tmp, {}, verdict='criteria_satisfied', verify_exit=0,
                                                fab=fab)['done'], fab)
            # Decision C: a qualified scope without cpl_rotation keeps main's done rule.
            self.assertTrue(q.write_record(tmp, {}, verdict='criteria_satisfied', verify_exit=0,
                                           fab={'rotation_status': 'not_declared', 'manifest': None})['done'])


class NativeFabStageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)
        cls.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-fab-stage-')
        cls.root = Path(cls.tmp.name)
        cls.inputs = candidate(cls.root / 'inputs')
        cls.stage = g.Stage(cls.root / 'generator', 7, 'fab')
        cls.stage.start()
        cls.state = {'name': 'cpl', 'reviewed_inputs': cls.inputs, 'verdict': 'criteria_satisfied'}
        cls.status = g.stage_fab(cls.stage, cls.state, {})
        cls.stage.finish(cls.status)
        cls.receipt_dir = cls.stage.dir / 'export-fab'
        cls.check_run = cls.receipt_dir / 'run/out/checks'

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_generator_exports_corrected_rotations_with_a_verified_stage_receipt(self):
        self.assertEqual(self.status, 'PREVIEW_REQUIRED')
        receipt = validate_receipt(self.receipt_dir)
        self.assertEqual(receipt['status'], 'completed')
        self.assertEqual(receipt['changed'], [])
        policy = 'generation/cpl/jlc-cpl-policy.json'
        self.assertEqual(receipt['inputs'][policy], digest(self.inputs / policy))
        baseline = read_json(self.check_run / 'baseline.json')
        self.assertIn(policy, baseline['files'])
        self.assertTrue(any(p.endswith('.raweasy.json') for p in baseline['files']))
        native = self.check_run / 'work/fab/fab'
        from agent_ee.fab_export import verify_manifest
        verify_manifest(native, read_json(native / 'manifest.json'))
        package = self.stage.dir / 'package'
        with (package / 'cpl.csv').open() as stream:
            corrected = {r['Designator']: r for r in csv.DictReader(stream)}
        with (native / 'cpl.csv').open() as stream:
            original = {r['Designator']: r for r in csv.DictReader(stream)}
        self.assertEqual((original['U1']['Rotation'], corrected['U1']['Rotation']), ('90.000000', '0.000000'))
        self.assertEqual(corrected['JF0']['Mid Y'], '-21.270000')
        self.assertEqual(corrected['R1'], original['R1'])
        self.assertEqual((corrected['JB0']['Layer'], corrected['JB0']['Rotation']), ('Bottom', '180.000000'))
        manifest = read_json(self.stage.dir / 'fab-manifest.json')
        self.assertEqual((manifest['status'], manifest['physical_validation']), ('PREVIEW_REQUIRED', False))
        self.assertTrue(manifest['rotation']['applied_corrections'])
        self.assertTrue(g.fab_exists(self.stage.dir / 'fab-manifest.json'))
        self.assertEqual(manifest['rotation']['policy']['sha256'], digest(self.inputs / policy))

    def test_qualified_run_publishes_the_corrected_package_with_preview_status(self):
        result = q.fab_outcome(self.check_run)
        self.assertEqual(result['rotation_status'], 'PREVIEW_REQUIRED')
        manifest = read_json(result['manifest'])
        self.assertEqual(manifest['rotation']['report_sha256'], digest(self.check_run / 'work/cpl/cpl-rotation.json'))
        target = Path(result['manifest']).parent / 'fab/cpl.csv'
        self.assertEqual(digest(target), digest(self.check_run / 'work/cpl/cpl/cpl.csv'))

    def test_generator_done_gate_preserves_preview_required_after_satisfied_review(self):
        def prepare(stage, state, options):
            state.update(name='cpl', reviewed_inputs=self.inputs, verdict='criteria_satisfied')
        functions = {name: (lambda *args: None) for _, name in g.STAGES}
        functions.update(requirements=prepare, fab=g.stage_fab)
        record = g.generate(ROOT / 'fixtures/rung1/spec.md', self.root / 'done-gate',
                            skip_review=True, functions=functions)
        self.assertEqual(record['stages'][-1]['status'], 'PREVIEW_REQUIRED', record['stages'][-1])
        self.assertTrue(record['fab_exports_exist'])
        self.assertEqual(record['verdict'], 'criteria_satisfied')
        self.assertFalse(record['done'])
        self.assertEqual(record['fab_rotation_blocker'],
                         'fab: cpl rotation PREVIEW_REQUIRED (bottom-side transform unvalidated)')

    def test_missing_policy_is_a_failed_stage_receipt(self):
        broken = self.root / 'missing-policy'
        shutil.copytree(self.inputs, broken)
        (broken / 'generation/cpl/jlc-cpl-policy.json').unlink()
        receipt_dir = self.root / 'missing-policy-receipt'
        receipt = run_step(receipt_dir, broken,
                           fab_stage.step(g.PACKAGE_ROOT, 'project/cpl.kicad_pcb', 'project/cpl.kicad_sch'),
                           {'test': 'missing-policy'})
        self.assertEqual((receipt['status'], receipt['exit_code']), ('failed', 3))
        validate_receipt(receipt_dir)
        self.assertFalse((receipt_dir / 'run/out/fab-manifest.json').exists())

    def test_corrupt_policy_cannot_publish_a_package(self):
        broken = self.root / 'bad-policy'
        shutil.copytree(self.inputs, broken)
        (broken / 'generation/cpl/jlc-cpl-policy.json').write_text('{"schema_version": 99}')
        out = self.root / 'bad-policy-out'
        with self.assertRaisesRegex(ValueError, 'policy keys'):
            fab_stage.export(broken, out, 'project/cpl.kicad_pcb', 'project/cpl.kicad_sch')
        self.assertFalse((out / 'fab-manifest.json').exists())

    def test_one_missing_supplier_keeps_the_receipt_failed_and_no_package_is_published(self):
        broken = self.root / 'missing-supplier'
        shutil.copytree(self.inputs, broken)
        (broken / 'generation/cpl/supplier/C910020.provenance.json').unlink()
        receipt_dir = self.root / 'missing-receipt'
        spec = fab_stage.step(g.PACKAGE_ROOT, 'project/cpl.kicad_pcb', 'project/cpl.kicad_sch')
        receipt = run_step(receipt_dir, broken, spec, {'test': 'missing-supplier'})
        self.assertEqual(receipt['status'], 'failed')
        self.assertEqual(receipt['exit_code'], 3)
        validate_receipt(receipt_dir)
        out = receipt_dir / 'run/out'
        self.assertFalse((out / 'fab').exists())
        report = read_json(out / 'checks/work/cpl/cpl-rotation.json')
        self.assertEqual((report['execution'], report['outcome'], report['coverage']),
                         ('unsupported', 'unknown', 'incomplete'))


if __name__ == '__main__':
    unittest.main()
