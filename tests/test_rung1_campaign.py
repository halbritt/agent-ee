"""Rung 1 as a staged repair campaign from an empty candidate: scaffold, scope, steps, dry run.

No test here runs the campaign or any inference. The scaffold is written by the sandboxed
`write-scaffold` step, so every scaffold test needs Bubblewrap. The native tests load the
empty scaffold in kicad-cli and run a checks-only review of the empty candidate.
"""
import shutil
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from agent_ee import generate as g
from agent_ee import rung1_campaign as rc
from agent_ee.stage_receipts import validate_receipt
from agent_ee.stages import declaration, native_references
from agent_ee.storage import digest
try:
    from toolchain import require_toolchain
    from test_generate import HEADER, ROWS, git_catalog
except ImportError:  # run as tests.test_rung1_campaign from the repository root
    from tests.toolchain import require_toolchain
    from tests.test_generate import HEADER, ROWS, git_catalog

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / 'fixtures/rung1/spec.md'
STOCK_SYMBOLS = [g.STOCK / 'symbols' / f'{name}.kicad_sym' for name in g.SYMBOL_LIBRARIES]


def require_stock():
    missing = [str(p) for p in STOCK_SYMBOLS if not p.is_file()]
    if missing or not (g.STOCK / 'footprints' / 'Resistor_SMD.pretty').is_dir():
        reason = 'toolchain prerequisite: KiCad stock libraries missing: ' + ', '.join(missing or ['footprints'])
        if os.environ.get('AGENT_EE_REQUIRE_TOOLCHAIN') == '1':
            raise AssertionError('AGENT_EE_REQUIRE_TOOLCHAIN=1: ' + reason)
        raise unittest.SkipTest(reason)


class Prepared(unittest.TestCase):
    """Generation inputs from the real stage-1/2 functions over an owned synthetic catalog."""

    @classmethod
    def setUpClass(cls):
        require_stock()
        require_toolchain()
        cls.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-rung1-campaign-')
        root = Path(cls.tmp.name)
        catalog = git_catalog(root / 'catalog', HEADER + '\n'.join(ROWS) + '\n')
        cls.rundir = root / 'run'
        cls.rundir.mkdir()
        from tests.test_fab_stage import rotation_inputs
        cls.prep = rc.prepare(SPEC, cls.rundir, catalog_checkout=catalog,
                              cpl_inputs=rotation_inputs(root / 'cpl-inputs'))
        cls.scaffold, cls.record = rc.write_scaffold(cls.rundir, cls.prep)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()


class ScaffoldTest(Prepared):
    def test_empty_native_files_have_qualified_versions_and_no_content(self):
        project = self.scaffold / 'project'
        sch, pcb = project / 'rung1_rc.kicad_sch', project / 'rung1_rc.kicad_pcb'
        self.assertEqual(native_references(sch, 'symbols'), [])
        self.assertEqual(native_references(pcb, 'footprints'), [])
        self.assertEqual(native_references(pcb, 'tracks'), [])
        text = pcb.read_text()
        for item in ('(footprint', '(segment', '(via', '(zone', '(net '):
            self.assertNotIn(item, text)
        self.assertEqual(text.count('(layer "Edge.Cuts")'), 4)
        self.assertIn('(start 100 100)', text)
        self.assertIn('(end 150 130)', text)
        self.assertEqual(self.record['board_items_dropped_from_template'],
                         {'footprint': 3, 'gr_line': 4, 'segment': 5, 'via': 1, 'zone': 1})

    def test_scaffold_is_deterministic(self):
        board, _ = rc.empty_board((rc.FIXTURE_PROJECT / 'rc_filter.kicad_pcb').read_text(), self.prep['envelope'])
        self.assertEqual(board, (self.scaffold / 'project/rung1_rc.kicad_pcb').read_bytes())
        root = rc.root_sheet_uuid(self.prep['resolved'])
        self.assertEqual(rc.empty_schematic(root), (self.scaffold / 'project/rung1_rc.kicad_sch').read_bytes())

    def test_project_and_tables_equal_the_emitter_output(self):
        from agent_ee.schematic_emit import emit
        captures = {p.name: p.read_bytes() for p in STOCK_SYMBOLS}
        outputs = emit(self.prep['resolved_bytes'], captures, self.prep['parts_path'].read_bytes(),
                       (rc.FIXTURE_PROJECT / 'rc_filter.kicad_pro').read_bytes(), rc.NAME)
        project = self.scaffold / 'project'
        for name in ('rung1_rc.kicad_pro', 'sym-lib-table', 'fp-lib-table'):
            self.assertEqual(outputs[name], (project / name).read_bytes(), name)
        root = rc.root_sheet_uuid(self.prep['resolved'])
        self.assertIn(f'(uuid "{root}")'.encode(), outputs['rung1_rc.kicad_sch'])

    def test_generation_inputs_are_captured_and_immutable_template_matches(self):
        generation = self.scaffold / 'generation'
        for name in ('intent.json', 'resolved.json', 'parts-manifest.json', 'envelope.json',
                     'placement-request.json', 'template/rung1_rc.kicad_pcb', 'template/rung1_rc.kicad_pro'):
            self.assertTrue((generation / name).is_file(), name)
        self.assertEqual((generation / 'template/rung1_rc.kicad_pcb').read_bytes(),
                         (self.scaffold / 'project/rung1_rc.kicad_pcb').read_bytes())
        parts = json.loads((generation / 'parts-manifest.json').read_text())
        self.assertEqual(parts['parts']['J1']['JLCPCB_Assembly'], 'not_assembled')

    def test_scaffold_files_are_the_adopted_outputs_of_a_retained_scaffold_receipt(self):
        summary = self.record['stage_receipt']
        receipt_dir = Path(summary['receipt']).parent
        self.assertEqual(receipt_dir, self.rundir / 'scaffold-receipt')
        receipt = validate_receipt(receipt_dir, binding={'rung1': 'scaffold', 'receipt_index': 1})
        self.assertEqual((summary['stage'], summary['step'], summary['status'], summary['exit_code']),
                         ('scaffold', 'write-scaffold', 'completed', 0))
        self.assertEqual(summary['receipt_sha256'], digest(receipt_dir / 'receipt.json'))
        self.assertEqual(receipt['violations'], [])
        self.assertIn('--unshare-net', receipt['execution']['sandbox_argv'])
        self.assertEqual(receipt['tool']['executable']['sha256'], digest('/usr/bin/python3'))
        self.assertEqual(receipt['tool']['reported_version']['status'], 'reported')
        self.assertEqual([m['at'] for m in receipt['tool']['mounts']],
                         ['/tool/steps', '/tool/agent-ee', '/tool/template'])
        self.assertIsNotNone(receipt['tool']['source_tree']['sha256'])
        # Inputs are the immutable generation inputs only; every project file is a step output.
        self.assertFalse(any(rel.startswith('project/') for rel in receipt['inputs']))
        self.assertIn('generation/resolved.json', receipt['inputs'])
        self.assertEqual(sorted(receipt['outputs']), sorted(receipt['changed']))
        self.assertEqual(len(receipt['outputs']), 7)
        for rel, sha in receipt['outputs'].items():
            self.assertEqual(digest(self.scaffold / rel), sha, rel)
            self.assertEqual(self.record['files'][rel], sha, rel)

    def test_scaffold_binding_is_exact_against_the_first_capture(self):
        base = {'scaffold': {'stage_receipt': self.record['stage_receipt']}}
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            self.assertEqual(rc.first_capture_binding(base, run)['bound'], False)
            files = dict(self.record['files'], **{'stock/symbols/Device.kicad_sym': 'x'})
            (run / 'candidate.json').write_text(json.dumps({'files': files}))
            binding = rc.first_capture_binding(base, run)
            self.assertEqual((binding['bound'], binding['differences']), (True, []))
            # A file planted in a scaffold directory before capture is not in the receipt: unbound.
            for planted in ('project/rung1_rc.kicad_dru', 'generation/extra.json', 'requirements/extra.md'):
                (run / 'candidate.json').write_text(json.dumps({'files': dict(files, **{planted: '1' * 64})}))
                binding = rc.first_capture_binding(base, run)
                self.assertEqual((binding['bound'], binding['differences']), (False, [planted]))
            files['project/rung1_rc.kicad_pcb'] = '0' * 64
            (run / 'candidate.json').write_text(json.dumps({'files': files}))
            binding = rc.first_capture_binding(base, run)
            self.assertEqual((binding['bound'], binding['differences']), (False, ['project/rung1_rc.kicad_pcb']))
            del files['project/rung1_rc.kicad_pcb']
            (run / 'candidate.json').write_text(json.dumps({'files': files}))
            self.assertFalse(rc.first_capture_binding(base, run)['bound'])
            self.assertEqual(rc.first_capture_binding({}, run),
                             {'bound': False, 'error': 'no scaffold stage receipt recorded'})
            forged = {'scaffold': {'stage_receipt': dict(self.record['stage_receipt'], receipt_sha256='0' * 64)}}
            self.assertIn('changed after it was recorded', rc.first_capture_binding(forged, run)['error'])

    def test_failed_scaffold_step_keeps_its_receipt_and_writes_no_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            template = Path(tmp) / 'template'
            shutil.copytree(rc.FIXTURE_PROJECT, template)
            (template / 'fp-lib-table').write_text('(fp_lib_table (lib (name "x") (uri "${KIPRJMOD}/x")))\n')
            rundir = Path(tmp) / 'run'
            rundir.mkdir()
            with self.assertRaisesRegex(rc.PrepareFailure, 'write-scaffold step failed'):
                rc.write_scaffold(rundir, self.prep, template_dir=template)
            receipt = validate_receipt(rundir / 'scaffold-receipt')
            self.assertEqual((receipt['status'], receipt['exit_code']), ('failed', 3))
            self.assertIn(b'must use stock references only',
                          (rundir / 'scaffold-receipt/run/stderr.log').read_bytes())
            self.assertFalse((rundir / 'scaffold' / 'project').exists())
            self.assertFalse((rundir / 'scaffold.json').exists())

    def test_staged_scope_validates_with_the_real_loaders(self):
        scope = rc.compile_staged_scope(self.prep['params'], self.rundir, self.scaffold)
        self.assertEqual([(c['id'], c['stage']) for c in scope['checks']],
                         [('erc', 'schematic'), ('netlist', 'schematic'), ('drc', 'placement'),
                          ('geometry', 'routing'), ('rc-step', 'verification'), ('fab-export', 'fab'),
                          ('cpl', 'fab'), ('fab', 'fab')])
        self.assertEqual(scope['checks'][-1]['kind'], 'fab_package')
        self.assertEqual(scope['generation'], {'schema': 'agent-ee.generation/1', 'done_check': 'fab'})
        self.assertIn({**rc.FAB_REQUIREMENT}, scope['baseline']['requirements'])
        self.assertIn({**rc.CPL_REQUIREMENT}, scope['baseline']['requirements'])
        cpl = next(c for c in scope['checks'] if c['id'] == 'cpl')
        self.assertEqual((cpl['kind'], cpl['params']['export_check']), ('cpl_rotation', 'fab-export'))
        self.assertEqual(declaration(scope)['order'], ['schematic', 'placement', 'routing', 'verification', 'fab'])
        self.assertEqual(scope['mutable_inputs'], ['project/rung1_rc.kicad_sch', 'project/rung1_rc.kicad_pcb'])
        rc_step = next(c for c in scope['checks'] if c['id'] == 'rc-step')
        self.assertEqual((rc_step['params']['step_v'], rc_step['params']['measurements'][0]),
                         (1.0, {'name': 'vout_1ms', 'at_ms': 1.0, 'min': 0.62, 'max': 0.65}))
        path = self.rundir / 'scope-test.json'
        path.write_text(json.dumps(scope))
        summary = rc.validate_scope_file(path)
        self.assertIn('generation/resolved.json', summary['immutable_generation_inputs'])
        self.assertNotIn('project/rung1_rc.kicad_pcb', summary['baseline_files'])

    def test_scope_without_a_cpl_capture_keeps_the_native_done_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            scaffold = Path(tmp) / 'scaffold'
            shutil.copytree(self.scaffold, scaffold)
            shutil.rmtree(scaffold / 'generation' / 'cpl')
            (scaffold / 'requirements' / 'fab.md').unlink()
            scope = rc.compile_staged_scope(self.prep['params'], tmp, scaffold)
            self.assertEqual([(c['id'], c['stage']) for c in scope['checks']],
                             [('erc', 'schematic'), ('netlist', 'schematic'), ('drc', 'placement'),
                              ('geometry', 'routing'), ('rc-step', 'verification'), ('fab', 'verification')])
            self.assertEqual(declaration(scope)['order'], ['schematic', 'placement', 'routing', 'verification'])
            self.assertNotIn('R-cpl', [r['id'] for r in scope['baseline']['requirements']])
            path = Path(tmp) / 'scope.json'
            path.write_text(json.dumps(scope))
            summary = rc.validate_scope_file(path)
            self.assertFalse([f for f in summary['immutable_generation_inputs'] if f.startswith('generation/cpl/')])

    def test_removing_the_fab_package_requirement_is_refused_at_capture(self):
        scope = rc.compile_staged_scope(self.prep['params'], self.rundir, self.scaffold)
        scope['checks'] = [c for c in scope['checks'] if c['kind'] != 'fab_package']
        scope['baseline']['requirements'] = [r for r in scope['baseline']['requirements'] if r['id'] != 'R-fab']
        path = self.rundir / 'scope-no-fab.json'
        path.write_text(json.dumps(scope))
        with self.assertRaisesRegex(ValueError, 'fab_package done check last'):
            rc.validate_scope_file(path)
        scope = rc.compile_staged_scope(self.prep['params'], self.rundir, self.scaffold)
        scope['baseline']['requirements'] = [r for r in scope['baseline']['requirements'] if r['id'] != 'R-fab']
        path = self.rundir / 'scope-unmapped-fab.json'
        path.write_text(json.dumps(scope))
        with self.assertRaisesRegex(ValueError, 'trace to a baseline requirement'):
            rc.validate_scope_file(path)

    def test_scope_with_widened_write_authority_is_refused(self):
        scope = rc.compile_staged_scope(self.prep['params'], self.rundir, self.scaffold)
        scope['mutable_inputs'].append('generation/envelope.json')
        path = self.rundir / 'scope-widened.json'
        path.write_text(json.dumps(scope))
        with self.assertRaises(rc.PrepareFailure):
            rc.validate_scope_file(path)


class StepTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='agent-ee-rung1-steps-')
        self.addCleanup(tmp.cleanup)
        self.route = Path(tmp.name) / 'kicad-route'
        self.route.mkdir()
        self.mutable = ['project/rung1_rc.kicad_sch', 'project/rung1_rc.kicad_pcb']
        self.steps = rc.step_specs(rc.PACKAGE_ROOT, rc.STEPS_DIR, self.route)

    def test_declared_steps_validate_in_order(self):
        steps = rc.check_steps(self.steps, self.mutable)
        self.assertEqual([(s['stage'], s['step']) for s in steps],
                         [('schematic', 'emit-schematic'), ('placement', 'build-board'),
                          ('routing', 'route-krt'), ('fab', 'export-fab')])
        for step in steps:
            self.assertEqual(step['adoptable_exits'], [0])
            for mount in step['mounts']:
                self.assertTrue(mount['at'].startswith('/tool/'))
        self.assertEqual(steps[2]['backend_report'], {'artifact': 'out/result.json', 'key': 'backend'})
        self.assertEqual(steps[3]['writes'], {'project/rung1_rc.kicad_pcb': 'design/project/rung1_rc.kicad_pcb'})

    def test_scaffold_step_is_a_valid_networkless_scaffold_stage_declaration(self):
        from agent_ee.stage_receipts import validate_step
        step = validate_step(rc.scaffold_step(rc.PACKAGE_ROOT, rc.STEPS_DIR, rc.FIXTURE_PROJECT))
        self.assertEqual((step['stage'], step['step'], step['adoptable_exits']), ('scaffold', 'write-scaffold', [0]))
        self.assertEqual(sorted(step['writes']), [
            'generation/template/rung1_rc.kicad_pcb', 'generation/template/rung1_rc.kicad_pro',
            'project/fp-lib-table', 'project/rung1_rc.kicad_pcb', 'project/rung1_rc.kicad_pro',
            'project/rung1_rc.kicad_sch', 'project/sym-lib-table'])
        self.assertTrue(all(source == 'out/' + target for target, source in step['writes'].items()))
        self.assertEqual(step['source'], '/tool/agent-ee')

    def test_step_with_adoptable_exit_3_is_rejected(self):
        route = dict(self.steps[2], adoptable_exits=[0, 3])
        with self.assertRaisesRegex(ValueError, 'never adoptable'):
            rc.check_steps([route], self.mutable)
        other = dict(self.steps[1], adoptable_exits=[0, 3])
        with self.assertRaisesRegex(ValueError, 'exit 0 only'):
            rc.check_steps([other], self.mutable)

    def test_write_outside_mutable_inputs_is_rejected(self):
        step = dict(self.steps[0], writes={'generation/resolved.json': 'out/sch/rung1_rc.kicad_sch'})
        with self.assertRaisesRegex(ValueError, 'outside mutable_inputs'):
            rc.check_steps([step], self.mutable)

    def test_reserved_mount_point_is_rejected(self):
        step = dict(self.steps[2], mounts=[{'source': str(self.route), 'at': '/home/kicad-route'}])
        with self.assertRaisesRegex(ValueError, 'reserved'):
            rc.check_steps([step], self.mutable)

    def test_campaign_config_uses_the_adjudicator_route_and_budgets(self):
        profile = json.loads((ROOT / 'profiles/claude-baseline.json').read_text())
        config = rc.campaign_config(Path('/x/scope.json'), profile, rc.check_steps(self.steps, self.mutable))
        self.assertEqual(config['implementation'], {'harness': 'claude', 'model': 'claude-fable-5',
                                                    'effort': 'high', 'timeout_s': 900})
        self.assertEqual((config['scope'], config['max_iterations'], config['max_seconds']), ('scope.json', 2, 3600))
        self.assertEqual(config['review_profile'], profile)


class RecordTest(unittest.TestCase):
    def test_done_requires_verdict_and_fab_hashes_recomputed_by_verify_campaign(self):
        # A scope without a cpl_rotation check: the done gate seals the native CPL.
        stored = {'artifacts_id': 'a', 'candidate_id': 'c', 'files': {'work/fab/fab/bom.csv': '1' * 64},
                  'rotation': {'status': 'not_declared'}}
        with tempfile.TemporaryDirectory() as tmp:
            bound = {'bound': True}
            done = lambda verdict, recomputed, artifacts=stored, scaffold=bound: rc.write_record(
                tmp, {}, verdict=verdict, fab_artifacts=artifacts, recomputed=recomputed, scaffold=scaffold)['done']
            self.assertTrue(done('criteria_satisfied', dict(stored)))
            self.assertFalse(done('criteria_satisfied', None))
            self.assertFalse(done('criteria_satisfied', None, None))
            self.assertFalse(done('criteria_satisfied', {**stored, 'files': {'work/fab/fab/bom.csv': '2' * 64}}))
            self.assertFalse(done('criteria_satisfied', {**stored, 'candidate_id': 'other'}))
            self.assertFalse(done('escalated', dict(stored)))
            for scaffold in (None, {'bound': False, 'differences': ['project/rung1_rc.kicad_pcb']}):
                self.assertFalse(done('criteria_satisfied', dict(stored), scaffold=scaffold))
            record = json.loads((Path(tmp) / 'rung1-campaign-record.json').read_text())
            self.assertEqual(record['done_rule'].split(' AND ')[0], 'campaign verdict criteria_satisfied')

    def test_done_with_a_declared_cpl_requires_an_allowed_rotation_status(self):
        stored = {'artifacts_id': 'a', 'candidate_id': 'c', 'files': {'work/cpl/cpl/cpl.csv': '1' * 64}}
        with tempfile.TemporaryDirectory() as tmp:
            def record(rotation):
                artifacts = {**stored, 'rotation': rotation} if rotation is not None else dict(stored)
                return rc.write_record(tmp, {}, verdict='criteria_satisfied', fab_artifacts=artifacts,
                                       recomputed=dict(stored), scaffold={'bound': True})
            preview = record({'status': 'PREVIEW_REQUIRED', 'policy_sha256': '2' * 64})
            self.assertFalse(preview['done'])
            self.assertTrue(preview['fab_artifacts_verified'])
            self.assertEqual(preview['fab_rotation_blocker'],
                             'fab: cpl rotation PREVIEW_REQUIRED (bottom-side transform unvalidated)')
            self.assertTrue(record({'status': 'ok', 'policy_sha256': '2' * 64})['done'])
            for rotation in (None, {}, {'status': 'pass'}):
                self.assertFalse(record(rotation)['done'], rotation)

    def test_verify_campaign_output_parsing_is_strict(self):
        run = '/r/runs/1'
        good = f"criteria_satisfied\nfab_artifacts {'a' * 64} candidate c\n{'1' * 64}  {run}/work/fab/fab/bom.csv\n"
        rotation = ('criteria_satisfied\nfab_rotation PREVIEW_REQUIRED policy ' + '2' * 64 +
                    '\nfab: cpl rotation PREVIEW_REQUIRED (bottom-side transform unvalidated)\n')
        self.assertEqual(rc.recomputed_artifacts(good.replace('criteria_satisfied\n', rotation), run),
                         rc.recomputed_artifacts(good, run))
        self.assertEqual(rc.recomputed_artifacts(good, run),
                         {'artifacts_id': 'a' * 64, 'candidate_id': 'c', 'files': {'work/fab/fab/bom.csv': '1' * 64}})
        self.assertIsNone(rc.recomputed_artifacts('criteria_satisfied\n', run))
        self.assertIsNone(rc.recomputed_artifacts(good.replace(run, '/other/runs/1'), run))
        self.assertIsNone(rc.recomputed_artifacts(good.replace('1' * 64, 'x' * 64), run))

    def test_fab_of_an_unaccepted_final_candidate_is_not_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'campaign'
            (root / 'runs' / '1').mkdir(parents=True)
            (root / 'state.json').write_text(json.dumps({'state': 'incomplete', 'history': [], 'error': 'x'}))
            with mock.patch.object(rc, 'run_logged', return_value={'exit_code': 2, 'stdout_tail': '',
                                                                    'stderr_tail': 'x'}), \
                    mock.patch.object(rc, 'campaign_summary', return_value=(
                        {'state': 'incomplete', 'error': 'x'}, [{'run': str(root / 'runs/1'), 'checks': {}}],
                        [{'fab': {'exports_exist': True, 'board_sha256': 'b', 'schematic_sha256': 's'}}])):
                (root / 'runs/1/candidate.json').write_text(json.dumps({'files': {
                    'project/rung1_rc.kicad_pcb': 'b', 'project/rung1_rc.kicad_sch': 's'}}))
                record = rc.campaign_record(tmp, {}, None)
            self.assertTrue(record['fab_matches_final_captured_candidate'])
            self.assertFalse(record['fab_artifacts_verified'])
            self.assertIsNone(record['fab_artifacts'])
            self.assertFalse(record['done'])
            self.assertEqual((record['schema'], record['qualification']),
                             ('agent-ee.rung1-campaign-record/0-experimental', 'experimental-unqualified'))


class NativeScaffoldTest(Prepared):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)
        super().setUpClass()

    def test_kicad_cli_loads_the_empty_files(self):
        loads = rc.scaffold_load_checks(self.rundir, self.scaffold)
        self.assertTrue(all(entry['loaded'] for entry in loads.values()), loads)
        self.assertEqual(loads['erc']['violations'], 0)
        self.assertEqual(loads['netlist']['components'], 0)
        self.assertEqual((loads['drc']['violations'], loads['drc']['schematic_parity']), (0, 0))

    def test_checks_only_review_of_the_empty_candidate_witnesses_stage_absence(self):
        scope = rc.compile_staged_scope(self.prep['params'], self.rundir, self.scaffold)
        path = self.rundir / 'scope-dry.json'
        path.write_text(json.dumps(scope))
        run = self.rundir / 'dry-review'
        env = {**os.environ, 'PYTHONPATH': str(rc.PACKAGE_ROOT), 'PYTHONDONTWRITEBYTECODE': '1'}
        proc = subprocess.run([sys.executable, '-B', '-m', 'agent_ee.cli', 'review', str(path), '--out', str(run),
                               '--checks-only'], capture_output=True, text=True, env=env, cwd=ROOT, timeout=1800)
        self.assertEqual(proc.returncode, 1, proc.stderr)  # checks-only never satisfies the review
        checks = {c['id']: c for c in json.loads((run / 'result.json').read_text())['checks']}
        for cid in ('erc', 'netlist', 'drc', 'geometry'):
            self.assertEqual((checks[cid]['execution'], checks[cid]['outcome'], checks[cid]['details']['reason']),
                             ('ok', 'defect', 'stage_not_reached'), cid)
        self.assertEqual(sorted(f['entity'] for f in checks['erc']['details']['gate_findings']), ['C1', 'J1', 'R1'])
        rc_step = checks['rc-step']
        self.assertEqual((rc_step['execution'], rc_step['outcome'], rc_step['details']['reason']),
                         ('ok', 'defect', 'stage_blocked'))
        verify = subprocess.run([sys.executable, '-B', '-m', 'agent_ee.cli', 'verify', str(run)],
                                capture_output=True, text=True, env=env, cwd=ROOT, timeout=600)
        self.assertEqual(verify.returncode, 0, verify.stdout + verify.stderr)


if __name__ == '__main__':
    unittest.main()
