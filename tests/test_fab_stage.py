"""Real receipted fab stages, corrected packages and fail-closed captured inputs."""

import csv
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_ee import native_run
from native_hooks import after
from agent_ee import fab_stage, generate as g, rung1_campaign as rc, rung1_qualified as q
from agent_ee.fab_package import PREVIEW_ACTION
from agent_ee.stage_receipts import run_step, validate_receipt
from agent_ee.storage import digest, read_json, write_json
try:
    from toolchain import require_toolchain
except ImportError:
    from tests.toolchain import require_toolchain

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'fixtures/cpl-rotation'


def setUpModule():
    # Other shards import from src and can create bytecode during a receipted step.
    # Keep the mounted tool bytes private; the receipt must still reject any change.
    tmp = tempfile.TemporaryDirectory(prefix='agent-ee-fab-tool-')
    unittest.addModuleCleanup(tmp.cleanup)
    source = Path(tmp.name) / 'src'
    shutil.copytree(q.PACKAGE_ROOT, source, ignore=shutil.ignore_patterns('__pycache__'))
    for driver in (g, q, rc):
        patch = mock.patch.object(driver, 'PACKAGE_ROOT', source)
        patch.start()
        unittest.addModuleCleanup(patch.stop)


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
    def test_receipted_tools_use_a_private_source_without_shared_bytecode(self):
        self.assertNotEqual(q.PACKAGE_ROOT, ROOT / 'src')
        self.assertEqual(g.PACKAGE_ROOT, q.PACKAGE_ROOT)
        self.assertEqual(rc.PACKAGE_ROOT, q.PACKAGE_ROOT)
        self.assertEqual(list(q.PACKAGE_ROOT.rglob('__pycache__')), [])
        self.assertEqual((q.PACKAGE_ROOT / 'agent_ee/fab_stage.py').read_bytes(),
                         (ROOT / 'src/agent_ee/fab_stage.py').read_bytes())

    def test_bytecode_added_to_the_actual_tool_mount_still_refuses_adoption(self):
        import py_compile
        from agent_ee import stage_receipts
        require_toolchain()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tool, design = root / 'tool', root / 'design'
            tool.mkdir()
            design.mkdir()
            script = tool / 'export.py'
            script.write_text("from pathlib import Path; Path('result.txt').write_text('exported')\n")
            spec = {'schema': 'agent-ee.stage-step/1', 'stage': 'fab', 'step': 'cache-boundary',
                    'argv': ['/usr/bin/python3', '-B', '/tool/export.py'],
                    'version_argv': ['/usr/bin/python3', '--version'],
                    'mounts': [{'source': str(tool), 'at': '/tool'}], 'source': '/tool',
                    'writes': {}, 'artifacts': ['result.txt']}
            def execute_then_import(call, record):
                # Deterministically model a neighboring host import after the initial
                # identity observation. Every execution and digest remains real.
                if call.work.name == 'version':
                    py_compile.compile(str(script), doraise=True)

            with native_run.using(after(execute_then_import)):
                receipt = run_step(root / 'receipt', design, spec, {'test': 'cache-boundary'})
            self.assertEqual((receipt['exit_code'], receipt['status']), (0, 'refused'))
            self.assertEqual(receipt['violations'], ['mount_changed:/tool', 'source_changed'])
            self.assertEqual(validate_receipt(root / 'receipt'), receipt)
            with self.assertRaisesRegex(ValueError, 'no output adopted'):
                stage_receipts.adopt(root / 'receipt', receipt, design)

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

    def test_preview_required_is_done_only_with_its_obligations_in_every_driver(self):
        missing = 'fab: cpl rotation PREVIEW_REQUIRED: no preview obligations recorded'
        policy = '2' * 64
        obligations = {'status': 'PREVIEW_REQUIRED', 'policy_sha256': policy, 'action': PREVIEW_ACTION,
                       'designators': [{'designator': 'C1', 'side': 'Top'}, {'designator': 'R1', 'side': 'Bottom'}]}
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = {'artifacts_id': 'a', 'candidate_id': 'c', 'files': {'work/cpl/cpl/cpl.csv': '1' * 64},
                         'rotation': {'status': 'PREVIEW_REQUIRED', 'policy_sha256': policy}}
            record = rc.write_record(tmp, {}, verdict='criteria_satisfied', fab_artifacts=artifacts,
                                     recomputed={**artifacts, 'preview_obligations': None}, scaffold={'bound': True})
            self.assertEqual((record['done'], record['fab_rotation_blocker']), (False, missing))
            listed = {**artifacts, 'rotation': {**artifacts['rotation'], 'preview_obligations': obligations}}
            record = rc.write_record(tmp, {}, verdict='criteria_satisfied', fab_artifacts=listed,
                                     recomputed={**listed, 'preview_obligations': obligations},
                                     scaffold={'bound': True})
            self.assertEqual((record['done'], record['preview_obligations']), (True, obligations))
            self.assertEqual(record['fab_rotation']['status'], 'PREVIEW_REQUIRED')
            fab = {'rotation_status': 'PREVIEW_REQUIRED', 'manifest': 'x', 'policy_sha256': policy}
            record = q.write_record(tmp, {}, verdict='criteria_satisfied', verify_exit=0, fab=fab)
            self.assertEqual((record['done'], record['fab_rotation_blocker']), (False, missing))
            record = q.write_record(tmp, {}, verdict='criteria_satisfied', verify_exit=0,
                                    fab={**fab, 'preview_obligations': obligations})
            self.assertEqual((record['done'], record['preview_obligations']), (True, obligations))
            # Obligations bound to another policy, or an unverified review, never mark done.
            self.assertFalse(q.write_record(tmp, {}, verdict='criteria_satisfied', verify_exit=0,
                                            fab={**fab, 'preview_obligations': obligations,
                                                 'policy_sha256': '3' * 64})['done'])
            self.assertFalse(q.write_record(tmp, {}, verdict='criteria_satisfied', verify_exit=1,
                                            fab={**fab, 'preview_obligations': obligations})['done'])
            # An allowed status still needs the derived package; an unknown or missing status never passes.
            self.assertFalse(q.write_record(tmp, {}, verdict='criteria_satisfied', verify_exit=0,
                                            fab={'rotation_status': 'ok', 'manifest': None})['done'])
            self.assertTrue(q.write_record(tmp, {}, verdict='criteria_satisfied', verify_exit=0,
                                           fab={'rotation_status': 'ok', 'manifest': 'x'})['done'])
            for fab in (None, {'manifest': 'x'}, {'rotation_status': 'unknown', 'manifest': 'x'},
                        {'rotation_status': 'pass', 'manifest': 'x', 'preview_obligations': obligations}):
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
        # AGENTEE-151: the qualified fab outcome recomputes the obligations from the sealed run.
        with (self.check_run / 'work/cpl/cpl/cpl.csv').open() as stream:
            sides = sorted((r['Designator'], r['Layer']) for r in csv.DictReader(stream))
        self.assertEqual(sorted((e['designator'], e['side']) for e in result['preview_obligations']['designators']),
                         sides)
        self.assertEqual(result['preview_obligations'], manifest['preview_obligations'])
        self.assertEqual(result['policy_sha256'], manifest['rotation']['policy']['sha256'])
        (self.root / 'qualified-record').mkdir()
        record = q.write_record(self.root / 'qualified-record', {}, verdict='criteria_satisfied', verify_exit=0,
                                fab=result)
        self.assertEqual((record['done'], record['preview_obligations']), (True, result['preview_obligations']))
        forged = {**manifest, 'preview_obligations': {**manifest['preview_obligations'], 'designators': []}}
        write_json(Path(result['manifest']), forged)
        try:
            with self.assertRaisesRegex(ValueError, 'preview obligations differ'):
                q.fab_outcome(self.check_run)
        finally:
            write_json(Path(result['manifest']), manifest)
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
        # AGENTEE-151: done with the obligations listed; the stage status stays PREVIEW_REQUIRED.
        self.assertTrue(record['done'])
        self.assertIsNone(record['fab_rotation_blocker'])
        self.assertEqual(record['preview_obligations'],
                         read_json(self.root / 'done-gate/stages/7-fab/fab-manifest.json')['preview_obligations'])

    def test_generator_recomputes_the_stage_obligations_and_refuses_tampering(self):
        package = self.stage.dir / 'package'
        manifest = read_json(self.stage.dir / 'fab-manifest.json')
        obligations = g.stage_obligations(package, manifest)
        self.assertEqual(obligations, manifest['preview_obligations'])
        self.assertEqual(self.state['preview_obligations'], obligations)
        self.assertIn({'designator': 'JB0', 'side': 'Bottom'}, obligations['designators'])
        forged = {**manifest, 'preview_obligations': {**obligations, 'designators': obligations['designators'][1:]}}
        with self.assertRaisesRegex(g.StageFailure, 'preview obligations differ'):
            g.stage_obligations(package, forged)
        with self.assertRaisesRegex(g.StageFailure, 'preview obligations differ'):
            g.stage_obligations(package, {k: v for k, v in manifest.items() if k != 'preview_obligations'})
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / 'package'
            shutil.copytree(package, copy)
            (copy / 'cpl.csv').write_text((copy / 'cpl.csv').read_text() + 'Z9,0,0,Top,0\n')
            with self.assertRaisesRegex(g.StageFailure, 'not the corrected CPL'):
                g.stage_obligations(copy, manifest)

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



class NativeBomVariantStageTests(unittest.TestCase):
    """`generate --bom-variant` through the receipted stage 7 (AGENTEE-66 composed with AGENTEE-126)."""

    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)
        cls.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-fab-variant-stage-')
        cls.root = Path(cls.tmp.name)
        cls.inputs = candidate(cls.root / 'inputs')
        project = cls.inputs / 'project'
        (project / 'cpl.kicad_pro').write_text(json.dumps(
            {'meta': {'filename': 'cpl.kicad_pro', 'version': 3},
             'schematic': {'variants': [{'name': 'FULL'}, {'name': 'LITE'}]}}))
        sch = project / 'cpl.kicad_sch'
        old = '(reference "R1") (unit 1)'
        sch.write_text(sch.read_text().replace(old, old + ' (variant (name "LITE") (dnp yes))', 1))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def stage_fab(self, label, variant):
        stage = g.Stage(self.root / label, 7, 'fab')
        stage.start()
        state = {'name': 'cpl', 'reviewed_inputs': self.inputs, 'cpl_inputs': self.inputs / 'generation/cpl'}
        try:
            status, error = g.stage_fab(stage, state, {'bom_variant': variant}), None
        except g.StageFailure as exc:
            status, error = 'failed', str(exc)
        return stage, state, stage.finish(status, error)

    def test_variant_is_bound_into_the_step_the_sealed_manifest_and_the_stage_receipt(self):
        stage, state, receipt = self.stage_fab('full', 'FULL')
        self.assertEqual(receipt['status'], 'PREVIEW_REQUIRED', receipt['error'])
        g.validate_receipt(receipt)
        receipt_dir = stage.dir / 'export-fab'
        step = validate_receipt(receipt_dir)
        self.assertEqual(step['argv'][-2:], ['--bom-variant', 'FULL'])
        manifest = read_json(stage.dir / 'fab-manifest.json')
        self.assertEqual((manifest['bom_variant']['name'], manifest['bom_variant']['registered']),
                         ('FULL', ['FULL', 'LITE']))
        self.assertEqual(manifest['bom_variant']['project'],
                         {'path': 'project/cpl.kicad_pro', 'sha256': digest(self.inputs / 'project/cpl.kicad_pro')})
        self.assertEqual(sorted(manifest['bom_designators']), sorted(manifest['cpl_designators']))
        self.assertIn('R1', manifest['bom_designators'])
        self.assertEqual(receipt['details']['bom_variant']['requested'], ['FULL'])
        self.assertEqual(receipt['details']['bom_variant_export'], manifest['bom_variant'])
        commands = {c['label']: c for c in receipt['commands']}
        self.assertEqual(commands['export-fab']['argv'], step['argv'])
        self.assertEqual(commands['bom']['argv'][-3:-1], ['--variant', 'FULL'])
        bom_input = Path(commands['bom']['argv'][-1])
        self.assertEqual(bom_input, Path('/work/out/checks/work/fab/steps/bom/project/project/cpl.kicad_sch'))
        self.assertEqual(digest(receipt_dir / 'run' / bom_input.relative_to('/work')),
                         digest(self.inputs / 'project/cpl.kicad_sch'))
        self.assertEqual((commands['bom']['within'], commands['bom']['exit_code']), ('export-fab', 0))
        self.assertTrue((self.root / 'full' / commands['bom']['execution_record']).is_file())
        self.assertNotIn('--variant', commands['pos']['argv'])
        # A scope whose declared variant differs from the native export is refused: by the evidence
        # seal first, and by the package step's own variant binding behind it.
        run = receipt_dir / 'run/out/checks'
        candidate_path = run / 'candidate.json'
        record = read_json(candidate_path)
        for spec in record['scope']['checks']:
            if spec['id'] == 'fab':
                spec['params']['bom_variant'] = 'LITE'
        write_json(candidate_path, record)
        with self.assertRaisesRegex(ValueError, 'immutable baseline changed'):
            fab_stage.package(run, self.root / 'repackaged', self.root / 'repackaged.json')
        with mock.patch.object(fab_stage, 'verify_evidence'), \
                self.assertRaisesRegex(ValueError, "did not honour the declared BOM variant 'LITE'"):
            fab_stage.package(run, self.root / 'repackaged', self.root / 'repackaged.json')
        self.assertFalse((self.root / 'repackaged.json').exists())

    def test_variant_that_changes_the_populated_set_fails_stage_seven_by_name(self):
        stage, state, receipt = self.stage_fab('lite', 'LITE')
        self.assertEqual(receipt['status'], 'failed')
        self.assertIn('fab stage failed (exit 3)', receipt['error'])
        console = (stage.dir / 'export-fab/run/console.log').read_text()
        self.assertIn("BOM variant 'LITE' changes the populated set (adds [], removes ['R1'])", console)
        self.assertFalse((stage.dir / 'package').exists())
        self.assertNotIn('fab_manifest', state)
        self.assertEqual({c['label']: c['exit_code'] for c in receipt['commands']}['bom'], 0)



def declared_fab_scope(inputs):
    """Owned CPL fixture with the actual rung-1 fab producer's candidate paths."""
    board, sch = 'project/rung1_rc.kicad_pcb', 'project/rung1_rc.kicad_sch'
    checks = [{'id': 'netlist', 'kind': 'netlist', 'stage': 'verification'},
              {'id': 'geometry', 'kind': 'geometry', 'stage': 'verification'},
              {'id': 'fab-export', 'kind': 'fab_export', 'stage': 'fab', 'params': {'netlist_check': 'netlist'}},
              {**fab_stage.rotation_check(inputs), 'stage': 'fab'},
              {'id': 'fab', 'kind': 'fab_package', 'stage': 'fab', 'params': {'netlist_check': 'netlist'}}]
    checks[3]['params']['export_check'] = 'fab-export'
    requirement = 'project/requirements.md'
    return {'schema': 'agent-ee.scope/2', 'name': 'Declared fab producer control',
            'scope': 'Original CPL fixture; declared fab output must equal fresh native output.',
            'board': board, 'schematic': sch, 'checks': checks,
            'inputs': [{'path': str(p), 'at': p.name} for p in sorted(inputs.iterdir())],
            'stages': {'schema': 'agent-ee.stages/1', 'order': ['verification', 'fab'],
                       'subjects': {'verification': {'path': board, 'kind': 'footprints', 'references': ['R1']}},
                       'finding_retry_limit': 3},
            'generation': {'schema': 'agent-ee.generation/1', 'done_check': 'fab'},
            'baseline': {'requirements': [{'id': 'R-cpl', 'statement': 'Check the declared fab output',
                          'source': {'path': requirement, 'locator': 'R-cpl'},
                          'checks': [c['id'] for c in checks]}],
                         'targets': [requirement], 'reference_architectures': [requirement]}}


def declared_fab_inputs(root):
    inputs = candidate(root)
    for extension in ('kicad_pcb', 'kicad_sch'):
        (inputs / f'project/cpl.{extension}').rename(inputs / f'project/rung1_rc.{extension}')
    shutil.copyfile(FIXTURE / 'requirements.md', inputs / 'project/requirements.md')
    return inputs


class DeclaredFabStepTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import time
        from agent_ee.candidate import capture
        from agent_ee.checks import run_checks
        from agent_ee.baseline import freeze_evidence
        require_toolchain(native=True)
        cls.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-declared-fab-')
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.root = Path(cls.tmp.name)
        cls.inputs = declared_fab_inputs(cls.root / 'inputs')
        cls.scope = declared_fab_scope(cls.inputs)
        write_json(cls.root / 'scope.json', cls.scope)
        cls.spec = fab_stage.step(q.PACKAGE_ROOT, cls.scope['board'], cls.scope['schematic'])
        cls.receipt_dir = cls.root / 'receipt'
        cls.receipt = run_step(cls.receipt_dir, cls.inputs, cls.spec, {'test': 'declared-fab'})
        from tests.receipt_diagnostics import retain_refusal
        retain_refusal(cls.receipt_dir, cls.receipt)
        cls.context = {'spec': cls.receipt['spec'], 'receipt': {
            'path': str(cls.receipt_dir / 'receipt.json'),
            'sha256': digest(cls.receipt_dir / 'receipt.json'), 'binding': cls.receipt['binding']}}
        cls.run_dir = cls.root / 'run'
        cls.run_dir.mkdir()
        capture(cls.root / 'scope.json', cls.run_dir)
        cls.results = run_checks(cls.run_dir, time.monotonic() + 300, fab_context=cls.context)
        freeze_evidence(cls.run_dir, cls.results)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_all_fab_checks_grade_the_receipted_package_and_preserve_preview(self):
        from agent_ee.baseline import verify_evidence
        self.assertEqual(self.receipt['status'], 'completed')
        self.assertEqual([(r['id'], r['execution'], r['outcome']) for r in self.results],
                         [(c['id'], 'ok', 'info' if c['kind'] in ('geometry', 'netlist') else 'pass') for c in self.scope['checks']])
        for result in self.results[2:]:
            self.assertEqual(result['details']['fab_step'], self.context)
            self.assertIn(f"work/{result['id']}/fab-step.json", result['evidence'])
        verify_evidence(self.run_dir, self.results, required=True)
        corrected = self.receipt_dir / 'run/out/fab/cpl.csv'
        self.assertEqual(digest(corrected), digest(self.run_dir / 'work/cpl/cpl/cpl.csv'))
        self.assertEqual(self.results[-1]['details']['rotation']['status'], 'PREVIEW_REQUIRED')
        self.assertEqual(self.results[-1]['details']['fab_step_files']['cpl.csv'], digest(corrected))

    def test_missing_receipt_is_unknown_for_every_fab_check_and_only_schedules_fab(self):
        import time
        from agent_ee.candidate import capture
        from agent_ee.checks import run_checks
        from agent_ee.convergence import advance
        pending_run = self.root / 'pending'
        pending_run.mkdir()
        capture(self.root / 'scope.json', pending_run)
        results = run_checks(pending_run, time.monotonic() + 120,
                             fab_context={'spec': self.receipt['spec'], 'receipt': None})
        for result in results[2:]:
            self.assertEqual((result['execution'], result['outcome']), ('not_run', 'unknown'))
            self.assertEqual(result['details']['reason'], 'fab_step_pending')
            self.assertFalse((pending_run / 'work' / result['id'] / 'fab').exists())
        result = {'checks': results, 'outcome': 'incomplete', 'run_id': 'pending'}
        record, decision = advance([], result, read_json(pending_run / 'candidate.json'), 2,
                                   pending_fab=[r['id'] for r in results[2:]])
        self.assertEqual((record['earliest_failing_stage'], decision['state']), ('fab', 'repair'))
        self.assertEqual(record['outcome'], 'incomplete')

    def context_for(self, spec, label):
        folder = self.root / label
        receipt = run_step(folder, self.inputs, spec, {'test': label})
        from tests.receipt_diagnostics import retain_refusal
        retain_refusal(folder, receipt)
        self.assertEqual(receipt['status'], 'completed', receipt)
        return {'spec': receipt['spec'], 'receipt': {'path': str(folder / 'receipt.json'),
                'sha256': digest(folder / 'receipt.json'), 'binding': receipt['binding']}}

    def test_changed_or_inapplicable_receipt_is_refused_not_pending(self):
        import time
        from agent_ee.checks import Ctx, CheckError
        manifest = read_json(self.run_dir / 'candidate.json')
        context = json.loads(json.dumps(self.context))
        context['receipt']['sha256'] = '0' * 64
        ctx = Ctx(self.run_dir, manifest, self.scope['checks'][2], time.monotonic() + 120,
                  context={'fab_context': context})
        ctx.work = self.root / 'changed-context'
        ctx.work.mkdir()
        with self.assertRaisesRegex(CheckError, 'receipt changed'):
            fab_stage.before_fab_check(ctx)
        context['receipt']['sha256'] = self.context['receipt']['sha256']
        ctx.manifest = {**manifest, 'files': {**manifest['files'], self.scope['board']: '0' * 64}}
        with self.assertRaisesRegex(CheckError, 'not applicable'):
            fab_stage.before_fab_check(ctx)
        self.assertNotEqual(ctx.result['details'].get('reason'), 'fab_step_pending')

    def test_a_sealed_but_different_package_is_refused_against_fresh_export(self):
        import time
        from agent_ee.checks import Ctx, CheckError
        # An original wrapper runs the real producer and changes its output before receipt sealing.
        code = ("from agent_ee.fab_stage import _stage_main as main; main(['--board','project/rung1_rc.kicad_pcb',"
                "'--schematic','project/rung1_rc.kicad_sch']); "
                "from pathlib import Path; from agent_ee.storage import digest,read_json,write_json; "
                "p=Path('/work/out/fab/bom.csv'); p.write_bytes(p.read_bytes()+b'\\n'); q=Path('/work/out/fab/cpl.csv'); q.write_bytes(q.read_bytes()+b'\\n'); "
                "m=Path('/work/out/fab-manifest.json'); d=read_json(m); "
                "[f.update(sha256=digest(p),bytes=p.stat().st_size) for f in d['files'] if f['path']=='bom.csv']; "
                "[f.update(sha256=digest(q),bytes=q.stat().st_size) for f in d['files'] if f['path']=='cpl.csv']; "
                "write_json(m,d)")
        spec = {**self.spec, 'argv': self.spec['argv'][:5] + ['-c', code]}
        context = self.context_for(spec, 'different-package')
        ctx = Ctx(self.run_dir, read_json(self.run_dir / 'candidate.json'), self.scope['checks'][2], time.monotonic()+120,
                  context={'fab_context': context})
        ctx.work = self.root / 'fresh-comparison'
        shutil.copytree(self.run_dir / 'work/fab-export', ctx.work)
        fab_stage.before_fab_check(ctx)
        with self.assertRaisesRegex(CheckError, 'differs from the fresh sandbox export'):
            fab_stage.grade_native(ctx)
        shutil.rmtree(ctx.work)
        shutil.copytree(self.run_dir / 'work/cpl', ctx.work)
        with self.assertRaisesRegex(CheckError, 'CPL differs from the fresh sandbox correction'):
            fab_stage.grade_cpl(ctx)

    def test_malformed_sealed_manifests_return_unknown_instead_of_crashing(self):
        import time
        from agent_ee.checks import Ctx, CheckError
        for label, relative in (('package', 'out/fab-manifest.json'),
                                ('native', 'out/checks/work/fab/fab/manifest.json')):
            with self.subTest(manifest=label):
                code = ("from agent_ee.fab_stage import _stage_main as main; main(['--board','project/rung1_rc.kicad_pcb',"
                        "'--schematic','project/rung1_rc.kicad_sch']); from pathlib import Path; "
                        f"Path('/work/{relative}').write_text('[]')")
                spec = {**self.spec, 'argv': self.spec['argv'][:5] + ['-c', code]}
                context = self.context_for(spec, 'malformed-' + label)
                ctx = Ctx(self.run_dir, read_json(self.run_dir / 'candidate.json'),
                          self.scope['checks'][2], time.monotonic() + 120,
                          context={'fab_context': context})
                ctx.work = self.root / ('malformed-comparison-' + label)
                shutil.copytree(self.run_dir / 'work/fab-export', ctx.work)
                fab_stage.before_fab_check(ctx)
                with self.assertRaisesRegex(CheckError, 'manifest') as error:
                    fab_stage.grade_native(ctx)
                self.assertEqual(error.exception.execution, 'malformed')

    def test_nonvolatile_manifest_metadata_must_match_the_fresh_export(self):
        import time
        from agent_ee.checks import Ctx, CheckError
        ctx = Ctx(self.run_dir, read_json(self.run_dir / 'candidate.json'),
                  self.scope['checks'][2], time.monotonic() + 120,
                  context={'fab_context': self.context})
        ctx.work = self.root / 'metadata-comparison'
        shutil.copytree(self.run_dir / 'work/fab-export', ctx.work)
        fab_stage.before_fab_check(ctx)
        path = ctx.work / 'fab/manifest.json'
        manifest = read_json(path)
        write_json(path, {**manifest, 'board_sha256': '0' * 64})
        with self.assertRaisesRegex(CheckError, 'manifest differs from the fresh sandbox export'):
            fab_stage.grade_native(ctx)

    def test_missing_or_changed_sealed_artifact_stays_unknown(self):
        import time
        from agent_ee.checks import Ctx, CheckError
        ctx = Ctx(self.run_dir, read_json(self.run_dir / 'candidate.json'), self.scope['checks'][2], time.monotonic()+120,
                  context={'fab_context': self.context})
        ctx.work = self.root / 'missing-artifact'
        ctx.work.mkdir()
        artifact = self.receipt_dir / 'run/out/fab/cpl.csv'
        data = artifact.read_bytes()
        try:
            artifact.unlink()
            with self.assertRaisesRegex(CheckError, 'artifacts changed'):
                fab_stage.before_fab_check(ctx)
        finally:
            artifact.write_bytes(data)
        self.assertNotEqual(ctx.result['details'].get('reason'), 'fab_step_pending')


if __name__ == '__main__':
    unittest.main()
