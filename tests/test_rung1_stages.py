"""Rung-1 stage-ordered driver (AGENTEE-104): stage order, stop rule, sealed grading and record.

No test spends inference. Steps run through the real stage_receipts sandbox; graded stages
come from real checks-only reviews; the generation run uses a test-authored catalog.
"""
import json
from pathlib import Path
import tempfile
import unittest

from agent_ee import rung1_stages as st
from agent_ee.stage_receipts import STAGES as RECEIPT_STAGES
from agent_ee.storage import digest, identity, read_json, write_json
try:
    from toolchain import require_kicad_route, require_toolchain
    from test_generate import HEADER, ROWS, git_catalog
except ImportError:  # run as tests.test_rung1_stages from the repository root
    from tests.toolchain import require_kicad_route, require_toolchain
    from tests.test_generate import HEADER, ROWS, git_catalog

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / 'fixtures/rc/control.scope.json'
SPEC = ROOT / 'fixtures/rung1/spec.md'


def statuses(record):
    return [s['status'] for s in record['stages']]


def shell_step(stage, step, script, writes):
    return {'schema': 'agent-ee.stage-step/1', 'stage': stage, 'step': step,
            'argv': ['/bin/sh', '-c', 'set -e; mkdir -p /work/out; ' + script], 'mounts': [],
            'writes': writes, 'artifacts': [], 'adoptable_exits': [0], 'timeout_s': 60}


def rewrite(record_path, change):
    record = read_json(record_path)
    change(record)
    record['record_id'] = identity({k: v for k, v in record.items() if k != 'record_id'})
    write_json(record_path, record)


class DeclarationTest(unittest.TestCase):
    def test_fixed_order_maps_one_to_one_onto_receipt_stages(self):
        self.assertEqual(st.NAMES, ['requirements', 'parts', 'schematic', 'placement', 'routing',
                                    'verification', 'fab'])
        self.assertEqual(tuple(s[2] for s in st.STAGES), RECEIPT_STAGES)
        self.assertTrue(all(s[3].startswith('AGENTEE-') for s in st.STAGES))

    def test_requirements_step_is_a_valid_artifact_only_declaration(self):
        from agent_ee.stage_receipts import validate_step
        spec = validate_step(st.requirements_step())
        self.assertEqual((spec['stage'], spec['writes']), ('scaffold', {}))
        self.assertIn('compiled/scope.json', spec['artifacts'])

    def staged(self):
        return {'schema': 'agent-ee.scope/2', 'stages': {}, 'checks': [
            {'id': 'erc', 'kind': 'erc', 'stage': 'schematic'},
            {'id': 'netlist', 'kind': 'netlist', 'stage': 'schematic'},
            {'id': 'rc-step', 'kind': 'rc_transient', 'stage': 'verification'},
            {'id': 'binding', 'kind': 'part_binding', 'stage': 'fab'},
            {'id': 'fab', 'kind': 'fab_export', 'stage': 'fab', 'params': {'netlist_check': 'netlist'}},
            {'id': 'fab-consistency', 'kind': 'fab_consistency', 'stage': 'fab', 'params': {'export_check': 'fab'}},
            {'id': 'assembly', 'kind': 'assembly_readiness', 'stage': 'fab'}],
            'baseline': {'requirements': [{'id': 'R-fab', 'checks': ['fab']},
                                          {'id': 'R-fab-consistency', 'checks': ['fab-consistency']},
                                          {'id': 'R-native', 'checks': ['erc', 'netlist']}]}}

    def test_done_gate_replaces_export_and_consistency_with_fab_package_last(self):
        scope = st.done_gate(self.staged())
        self.assertEqual([c['id'] for c in scope['checks']], ['erc', 'netlist', 'rc-step', 'binding', 'assembly', 'fab'])
        self.assertEqual(scope['checks'][-1], {'id': 'fab', 'kind': 'fab_package', 'stage': 'fab',
                                               'params': {'netlist_check': 'netlist'}})
        self.assertEqual([r['checks'] for r in scope['baseline']['requirements']], [['fab'], ['fab'], ['erc', 'netlist']])
        self.assertEqual(scope['generation'], {'schema': 'agent-ee.generation/1', 'done_check': 'fab'})
        self.assertEqual(st.done_gate(scope), scope)
        grouped = st.stage_checks(scope)
        self.assertEqual((grouped['schematic'], grouped['verification'], grouped['fab']),
                         (['erc', 'netlist'], ['rc-step'], ['binding', 'assembly', 'fab']))

    def test_unstaged_scope_puts_the_done_check_in_fab_and_the_rest_in_verification(self):
        scope = st.done_gate(read_json(REFERENCE))
        self.assertNotIn('generation', scope)
        self.assertEqual(scope['checks'][-1]['kind'], 'fab_package')
        grouped = st.stage_checks(scope)
        self.assertEqual(grouped['verification'], ['erc', 'drc', 'netlist', 'geometry', 'rc-step'])
        self.assertEqual(grouped['fab'], ['fab'])

    def test_done_gate_and_grouping_refusals(self):
        scope = self.staged()
        scope['checks'].append({'id': 'netlist2', 'kind': 'netlist', 'stage': 'schematic'})
        with self.assertRaisesRegex(st.StageRefusal, 'exactly one netlist'):
            st.done_gate(scope)
        with self.assertRaisesRegex(st.StageRefusal, 'not a rung-1 stage'):
            st.stage_checks({'checks': [{'id': 'x', 'kind': 'erc', 'stage': 'bench'}]})


class GradeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-rung1-stages-grade-')
        self.run = Path(self.tmp.name)
        (self.run / 'checks').mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, cid, execution, outcome):
        write_json(self.run / 'checks' / f'{cid}.json', {'id': cid, 'execution': execution, 'outcome': outcome,
                                                         'summary': f'{cid} summary'})

    def test_only_ok_pass_or_info_grades_pass(self):
        self.put('erc', 'ok', 'pass')
        self.put('netlist', 'ok', 'info')
        status, reason, checks = st.grade(self.run, ['erc', 'netlist'])
        self.assertEqual((status, reason), ('pass', None))
        self.assertEqual(checks['erc']['sha256'], digest(self.run / 'checks' / 'erc.json'))

    def test_defect_fails_and_execution_other_than_ok_is_missing_coverage(self):
        self.put('erc', 'ok', 'pass')
        self.put('drc', 'ok', 'defect')
        self.put('fab', 'unsupported', 'unknown')
        self.assertEqual(st.grade(self.run, ['erc', 'drc'])[0], 'failed')
        status, reason, _ = st.grade(self.run, ['erc', 'fab'])
        self.assertEqual(status, 'missing_coverage')
        self.assertIn('fab: unsupported/unknown (fab summary)', reason)
        self.put('geometry', 'ok', 'unknown')
        self.assertEqual(st.grade(self.run, ['geometry'])[0], 'missing_coverage')

    def test_missing_result_or_no_check_is_never_pass(self):
        self.put('erc', 'ok', 'pass')
        status, reason, checks = st.grade(self.run, ['erc', 'drc'])
        self.assertEqual((status, checks['drc']), ('missing_coverage', None))
        self.assertIn('drc: no sealed result', reason)
        self.assertEqual(st.grade(self.run, [])[0], 'missing_coverage')


class StopRuleTest(unittest.TestCase):
    """Real sandboxed steps; no stage after a stop is reached and nothing passes without a seal."""

    def setUp(self):
        require_toolchain()
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-rung1-stages-stop-')
        self.rundir = Path(self.tmp.name) / 'run'
        self.rundir.mkdir()
        self.design = self.rundir / 'design'
        self.design.mkdir()
        (self.design / 'board.txt').write_text('empty\n')

    def tearDown(self):
        self.tmp.cleanup()

    def setup(self, plan, entry='requirements'):
        return {'entry': entry, 'scope': None, 'scope_path': None, 'design': self.design, 'plan': plan,
                'supplied': {}}

    def step(self, stage, script='cp /candidate/board.txt /work/out/board.txt; echo routed >> /work/out/board.txt'):
        return shell_step(stage, stage + '-step', script, {'board.txt': 'out/board.txt'})

    def test_removed_stage_step_makes_every_later_stage_not_reached(self):
        setup = self.setup({'requirements': {'steps': [self.step('scaffold')]},
                            'schematic': {'steps': [self.step('schematic')]}})
        record = st.write_record(self.rundir, setup, st.run_plan(self.rundir, setup), mode='requirements')
        self.assertEqual(statuses(record), ['completed', 'absent'] + ['not_reached'] * 5)
        self.assertIn('AGENTEE-134', record['stages'][1]['reason'])
        self.assertTrue(all(s['reason'] == 'stopped at stage 2' for s in record['stages'][2:]))
        self.assertFalse((self.rundir / 'stages' / '3-schematic').exists())
        self.assertFalse((self.rundir / 'reviews').exists())
        self.assertFalse(record['done'])
        self.assertEqual((self.design / 'board.txt').read_text(), 'empty\nrouted\n')
        receipt = record['stages'][0]['receipts'][0]
        self.assertEqual((receipt['step'], receipt['status']), ('scaffold-step', 'completed'))
        self.assertEqual(record['files'][receipt['receipt']], digest(self.rundir / receipt['receipt']))
        self.assertEqual(st.verify_record(self.rundir)['problems'], [])

    def test_failed_step_stops_without_adoption(self):
        setup = self.setup({'requirements': {'steps': [self.step('scaffold', 'echo broken >&2; exit 1')]}})
        record = st.write_record(self.rundir, setup, st.run_plan(self.rundir, setup), mode='requirements')
        self.assertEqual(statuses(record), ['failed'] + ['not_reached'] * 6)
        self.assertIn('"status": "failed"', record['stages'][0]['reason'])
        self.assertEqual((self.design / 'board.txt').read_text(), 'empty\n')
        self.assertTrue(st.verify_record(self.rundir)['verified'])

    def test_stages_before_the_entry_are_supplied_not_reached(self):
        setup = self.setup({'schematic': {'steps': [self.step('schematic')]}}, entry='schematic')
        record = st.write_record(self.rundir, setup, st.run_plan(self.rundir, setup), mode='generation')
        self.assertEqual(statuses(record), ['not_reached', 'not_reached', 'completed', 'absent'] + ['not_reached'] * 3)
        self.assertTrue(record['stages'][0]['reason'].startswith('supplied: the run entered at schematic'))

    def test_verify_refuses_forged_pass_tampered_receipt_and_foreign_commit(self):
        setup = self.setup({'requirements': {'steps': [self.step('scaffold')]}})
        st.write_record(self.rundir, setup, st.run_plan(self.rundir, setup), mode='requirements')
        path = self.rundir / st.RECORD
        original = path.read_bytes()

        def forge(record):
            record['stages'][2]['status'] = 'pass'
        rewrite(path, forge)
        problems = st.verify_record(self.rundir)['problems']
        self.assertIn('stage schematic: pass without a sealed result', problems)
        self.assertIn('a stage after the stopping stage is not not_reached', problems)

        path.write_bytes(original)
        rewrite(path, lambda r: r['agent_ee'].update(commit='0' * 40))
        self.assertTrue(any('differs from the recorded ' + '0' * 40 in p for p in st.verify_record(self.rundir)['problems']))

        path.write_bytes(original)
        receipt = self.rundir / read_json(path)['stages'][0]['receipts'][0]['receipt']
        data = read_json(receipt)
        data['exit_code'] = 0 if data['exit_code'] else 1
        write_json(receipt, data)
        problems = st.verify_record(self.rundir)['problems']
        self.assertTrue(any(p.startswith('bound file changed or missing') for p in problems))
        self.assertTrue(any('receipt' in p and 'stage requirements' in p for p in problems))

        receipt.unlink()
        path.write_bytes(original)
        self.assertTrue(any(p.startswith('bound file changed or missing') for p in st.verify_record(self.rundir)['problems']))

    def test_record_identity_covers_the_record(self):
        setup = self.setup({'requirements': {'steps': [self.step('scaffold')]}})
        st.write_record(self.rundir, setup, st.run_plan(self.rundir, setup), mode='requirements')
        record = read_json(self.rundir / st.RECORD)
        record['done'] = True
        write_json(self.rundir / st.RECORD, record)
        problems = st.verify_record(self.rundir)['problems']
        self.assertIn('record identity mismatch', problems)
        self.assertIn('done flag contradicts the stage statuses', problems)


class RequirementsStageTest(unittest.TestCase):
    def test_compiler_step_reproduces_the_committed_compiled_scope_and_parts_is_absent(self):
        require_toolchain()
        with tempfile.TemporaryDirectory(prefix='agent-ee-rung1-stages-req-') as tmp:
            rundir = Path(tmp) / 'run'
            record = st.run(rundir, requirement_record=st.REQUIREMENT_RECORD)
            self.assertEqual(statuses(record), ['completed', 'absent'] + ['not_reached'] * 5)
            receipt = record['stages'][0]['receipts'][0]
            self.assertEqual((receipt['step'], receipt['stage'], receipt['exit_code']),
                             ('compile-requirements', 'scaffold', 0))
            self.assertEqual(receipt['artifacts']['compiled/scope.json'],
                             digest(ROOT / 'fixtures/rung1/compiled.scope.json'))
            self.assertEqual(st.verify_record(rundir)['problems'], [])

    def test_existing_output_and_ambiguous_sources_are_refused(self):
        with tempfile.TemporaryDirectory(prefix='agent-ee-rung1-stages-refuse-') as tmp:
            with self.assertRaises(FileExistsError):
                st.run(tmp, reference=REFERENCE)
            with self.assertRaisesRegex(ValueError, 'exactly one'):
                st.run(Path(tmp) / 'new', reference=REFERENCE, spec=SPEC)
            with self.assertRaisesRegex(ValueError, 'unknown stage'):
                st.run(Path(tmp) / 'new', reference=REFERENCE, omit=['bench'])


class ReferenceBoardTest(unittest.TestCase):
    """Acceptance 1: the hand-made reference enters at verification."""

    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)
        cls.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-rung1-stages-ref-')
        cls.rundir = Path(cls.tmp.name) / 'run'
        cls.record = st.run(cls.rundir, reference=REFERENCE)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_stages_one_to_five_not_reached_with_owners_six_pass_seven_missing_lcsc(self):
        stages = self.record['stages']
        self.assertEqual(statuses(self.record), ['not_reached'] * 5 + ['pass', 'missing_coverage'])
        self.assertEqual([s['owner'] for s in stages[:5]],
                         ['AGENTEE-100', 'AGENTEE-101', 'AGENTEE-102', 'AGENTEE-91', 'AGENTEE-93'])
        checks = stages[5]['checks']
        self.assertEqual({k: (v['execution'], v['outcome']) for k, v in checks.items()},
                         {'erc': ('ok', 'pass'), 'drc': ('ok', 'pass'), 'netlist': ('ok', 'info'),
                          'geometry': ('ok', 'pass'), 'rc-step': ('ok', 'pass')})
        fab = stages[6]['checks']['fab']
        self.assertEqual((fab['execution'], fab['outcome']), ('unsupported', 'unknown'))
        self.assertIn('C1, J1, R1', fab['summary'])
        self.assertEqual(stages[5]['review'], stages[6]['review'])
        self.assertEqual(stages[5]['graded_design'], {'applicability': 'supplied', 'differences': []})
        self.assertFalse(self.record['done'])
        self.assertIsNone(self.record['fab_manifest']['manifest_sha256'])

    def test_record_verifies_with_the_same_commit(self):
        result = st.verify_record(self.rundir)
        self.assertEqual(result['problems'], [])
        self.assertEqual(result['commit'], self.record['agent_ee']['commit'])
        self.assertEqual(self.record['tools']['kicad-cli']['exit_code'], 0)

    def test_tampered_sealed_check_fails_verification(self):
        with tempfile.TemporaryDirectory(prefix='agent-ee-rung1-stages-ref-copy-') as tmp:
            import shutil
            copy = Path(tmp) / 'run'
            shutil.copytree(self.rundir, copy, symlinks=True)
            check = copy / self.record['stages'][5]['review']['run'] / 'checks' / 'rc-step.json'
            data = read_json(check)
            data['summary'] = 'edited'
            write_json(check, data)
            problems = st.verify_record(copy)['problems']
            self.assertTrue(any('bound file changed' in p and p.endswith('rc-step.json') for p in problems))
            self.assertTrue(any(p.startswith('stage verification: review does not verify') for p in problems))

    def test_removing_the_verification_step_leaves_fab_not_reached(self):
        with tempfile.TemporaryDirectory(prefix='agent-ee-rung1-stages-ref-omit-') as tmp:
            record = st.run(Path(tmp) / 'run', reference=REFERENCE, omit=['verification'])
            self.assertEqual(statuses(record), ['not_reached'] * 5 + ['absent', 'not_reached'])
            self.assertFalse((Path(tmp) / 'run' / 'reviews').exists())
            self.assertEqual(record['supplied']['omitted_stages'], ['verification'])


class GenerationTest(unittest.TestCase):
    """The RC candidate from rung1_qualified's preparation through every declared step."""

    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)
        cls.kicad_route = require_kicad_route()
        cls.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-rung1-stages-gen-')
        root = Path(cls.tmp.name)
        cls.catalog = git_catalog(root / 'catalog', HEADER + '\n'.join(ROWS) + '\n')
        cls.rundir = root / 'run'
        cls.record = st.run(cls.rundir, spec=SPEC, kicad_route=cls.kicad_route, catalog_checkout=cls.catalog)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_schematic_stage_passes_through_its_declared_steps(self):
        stages = self.record['stages']
        self.assertIsNone(self.record['error'])
        self.assertEqual(statuses(self.record)[:3], ['not_reached', 'not_reached', 'pass'],
                         json.dumps([(s['stage'], s['status'], s['reason']) for s in stages], default=str)[:3000])
        schematic = stages[2]
        self.assertEqual([r['step'] for r in schematic['receipts']], ['emit-schematic', 'forward-annotate'])
        self.assertEqual(schematic['graded_design'], {'applicability': 'applicable', 'differences': []})
        self.assertEqual({k: v['outcome'] for k, v in schematic['checks'].items()},
                         {'erc': 'pass', 'netlist': 'info', 'parity': 'pass'})
        self.assertEqual([r['step'] for r in stages[3]['receipts']], ['agent-ee-placer'])

    def test_stop_rule_receipts_and_done_gate_hold_wherever_the_run_stops(self):
        stages = self.record['stages']
        stop = next(i for i, s in enumerate(stages) if s['status'] not in ('pass', 'completed', 'not_reached')
                    or (i > 2 and s['status'] == 'not_reached'))
        self.assertTrue(all(s['status'] == 'not_reached' and s['reason'].startswith('stopped at stage')
                            for s in stages[stop + 1:]))
        for stage in stages[2:stop]:
            self.assertEqual(stage['status'], 'pass')
            self.assertIsNotNone(stage['review'])
            self.assertIn(stage['graded_design']['applicability'], ('applicable', 'supplied'))
        expected = {'schematic': ['emit-schematic', 'forward-annotate'], 'placement': ['agent-ee-placer'],
                    'routing': ['kicad-route-krt']}
        for stage in stages[2:stop + 1]:
            if stage['stage'] in expected:
                self.assertEqual([r['step'] for r in stage['receipts']], expected[stage['stage']])
        if stages[5]['status'] != 'not_reached':
            self.assertEqual(stages[4]['review'], stages[5]['review'])
            self.assertEqual(set(stages[5]['checks']), {'rc-step'})
        # The default parts route supplies no manufacturer or MPN: the fab stage is never passed.
        self.assertNotEqual(stages[6]['status'], 'pass')
        self.assertFalse(self.record['done'])
        scope = read_json(self.rundir / 'scope.json')
        self.assertEqual(scope['checks'][-1], {'id': 'fab', 'kind': 'fab_package', 'stage': 'fab',
                                               'params': {'netlist_check': 'netlist'}})
        self.assertNotIn('fab_consistency', {c['kind'] for c in scope['checks']})

    def test_record_verifies(self):
        self.assertEqual(st.verify_record(self.rundir)['problems'], [])

    def test_earlier_review_source_is_fixed_and_tampering_still_refuses(self):
        stage = self.record['stages'][2]
        run = self.rundir / stage['review']['run']
        manifest = read_json(run / 'candidate.json')
        scope = read_json(manifest['scope_path'])
        sources = [Path(item['path']) for item in scope['inputs']]
        self.assertTrue(all(self.rundir / 'review-sources' in p.parents for p in sources))
        source = next(p for p in sources if p.name == 'project') / Path(scope['schematic']).name
        original = source.read_bytes()
        self.assertIsNone(st.sealed(run))
        try:
            source.write_bytes(original + b'\n')
            self.assertIn('declared source inputs changed', st.sealed(run))
        finally:
            source.write_bytes(original)

    def test_removing_the_schematic_step_makes_every_later_stage_not_reached(self):
        with tempfile.TemporaryDirectory(prefix='agent-ee-rung1-stages-gen-omit-') as tmp:
            record = st.run(Path(tmp) / 'run', spec=SPEC, kicad_route=self.kicad_route,
                            catalog_checkout=self.catalog, omit=['schematic'])
            self.assertEqual(statuses(record), ['not_reached', 'not_reached', 'absent'] + ['not_reached'] * 4)
            self.assertFalse((Path(tmp) / 'run' / 'stages').exists())
            self.assertEqual(st.verify_record(Path(tmp) / 'run')['problems'], [])


if __name__ == '__main__':
    unittest.main()
