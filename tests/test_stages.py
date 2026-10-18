"""Stage policy tests use independent expected transitions, not physical claims."""
import copy
import unittest

from agent_ee.convergence import advance


def candidate(number, failed, passing=(), outcome='defects_or_unresolved'):
    specs = [{'id': cid, 'stage': stage} for cid, stage in
             [('erc', 'schematic'), ('pins', 'schematic'), ('place', 'placement'), ('drc', 'routing')]]
    manifest = {'candidate_id': str(number), 'files': {'board': str(number)},
                'scope': {'schema': 'agent-ee.scope/2', 'schematic': 'schematic', 'checks': specs, 'stages': {
                    'schema': 'agent-ee.stages/1', 'order': ['schematic', 'placement', 'routing'],
                    'subjects': {'schematic': {'path': 'schematic', 'kind': 'symbols', 'references': ['R1', 'C1']}}, 'finding_retry_limit': 4}}}
    checks = [{'id': cid, 'execution': 'ok', 'outcome': 'defect' if cid in failed else 'pass'}
              for cid in [s['id'] for s in specs] if cid in failed or cid in passing]
    return {'run_id': str(number), 'checks': checks, 'outcome': outcome}, manifest


class StagedConvergenceTests(unittest.TestCase):
    def test_earliest_stage_reduction_then_stage_advance(self):
        first, diagnostic = advance([], *candidate(1, ['erc', 'pins', 'place', 'drc']), 8)
        self.assertEqual(diagnostic['state'], 'repair')
        self.assertEqual(first['earliest_failing_stage'], 'schematic')
        second, diagnostic = advance([first], *candidate(2, ['pins', 'place', 'drc'], ['erc']), 8)
        self.assertEqual(diagnostic['state'], 'repair')
        third, diagnostic = advance([first, second], *candidate(3, ['place', 'drc'], ['erc', 'pins']), 8)
        self.assertEqual(diagnostic['state'], 'repair')
        self.assertEqual(third['earliest_failing_stage'], 'placement')

    def test_completed_stage_regression_and_repeated_candidate(self):
        first, _ = advance([], *candidate(1, ['place', 'drc'], ['erc', 'pins']), 8)
        _, diagnostic = advance([first], *candidate(2, ['erc', 'drc'], ['pins', 'place']), 8)
        self.assertEqual(diagnostic['reason'], 'regression')
        _, diagnostic = advance([first], *candidate(1, ['drc'], ['erc', 'pins', 'place']), 8)
        self.assertEqual(diagnostic['reason'], 'repeated_design')

    def test_completed_stage_info_check_is_protected(self):
        result, manifest = candidate(1, ['place', 'drc'], ['erc', 'pins'])
        next(c for c in result['checks'] if c['id'] == 'erc')['outcome'] = 'info'
        first, diagnostic = advance([], result, manifest, 8)
        self.assertEqual(diagnostic['state'], 'repair')
        _, diagnostic = advance([first], *candidate(2, ['erc', 'drc'], ['pins', 'place']), 8)
        self.assertEqual(diagnostic['reason'], 'regression')
        self.assertIn('erc', diagnostic['protected_criteria'])

    def test_later_stage_improvement_cannot_mask_stall(self):
        first, _ = advance([], *candidate(1, ['pins', 'place', 'drc'], ['erc']), 8)
        _, diagnostic = advance([first], *candidate(2, ['pins', 'drc'], ['erc', 'place']), 8)
        self.assertEqual(diagnostic['reason'], 'no_monotonic_progress')

    def test_staged_budget_and_missing_coverage(self):
        _, diagnostic = advance([], *candidate(1, ['erc', 'pins', 'place', 'drc']), 1)
        self.assertEqual(diagnostic['reason'], 'iteration_limit')
        result, manifest = candidate(1, ['erc', 'pins', 'place', 'drc'], outcome='incomplete')
        result['checks'][0].update(execution='failed', outcome='unknown')
        _, diagnostic = advance([], result, manifest, 8)
        self.assertEqual(diagnostic, {'state': 'incomplete', 'reason': 'execution_incomplete'})

    def test_advisory_changes_cannot_reset_finding_retry_budget(self):
        history = []
        for number, margin in [(1, -10), (2, -5), (3, -1)]:
            result, manifest = candidate(number, ['erc', 'place', 'drc'], ['pins'])
            manifest['scope']['stages']['finding_retry_limit'] = 3
            result['checks'][0]['details'] = {
                'margins': {'physical': {'min': margin}}, 'gate_findings': [
                    {'check_id': 'erc', 'entity': 'R1', 'kind': 'collision',
                     'suggested_fix': {'op': 'move', 'params': {'x': number}}}]}
            record, diagnostic = advance(history, result, manifest, 8)
            history.append(record)
            if number < 3:
                self.assertEqual(diagnostic['state'], 'repair')
        self.assertEqual(diagnostic['reason'], 'finding_retry_limit')

    def test_minimum_retry_limit_allows_one_repair_and_counts_first_appearance(self):
        history = []
        for number, margin in [(1, -10), (2, -5)]:
            result, manifest = candidate(number, ['erc', 'place', 'drc'], ['pins'])
            manifest['scope']['stages']['finding_retry_limit'] = 2
            result['checks'][0]['details'] = {'margins': {'physical': {'min': margin}}}
            record, diagnostic = advance(history, result, manifest, 8)
            history.append(record)
            if number == 1:
                self.assertEqual(diagnostic['state'], 'repair')
        self.assertEqual(diagnostic['reason'], 'finding_retry_limit')

    def test_later_margin_improvement_cannot_mask_earliest_stage_stall(self):
        result, manifest = candidate(1, ['erc', 'place', 'drc'], ['pins'])
        result['checks'][-1]['details'] = {'margins': {'distance': {'min': -10}}}
        first, _ = advance([], result, manifest, 8)
        result, manifest = candidate(2, ['erc', 'place', 'drc'], ['pins'])
        result['checks'][-1]['details'] = {'margins': {'distance': {'min': -5}}}
        _, diagnostic = advance([first], result, manifest, 8)
        self.assertEqual(diagnostic['reason'], 'no_monotonic_progress')


class StageDeclarationTests(unittest.TestCase):
    def test_finding_retry_limit_bounds_and_types(self):
        from agent_ee.stages import declaration
        _, manifest = candidate(1, [])
        scope = manifest['scope']
        for schema in ('agent-ee.scope/2', 'agent-ee.scope/3'):
            scope['schema'] = schema
            for limit in (-1, 0, 1, 101, True, False, 2.0, '2', None):
                with self.subTest(schema=schema, limit=limit):
                    scope['stages']['finding_retry_limit'] = limit
                    with self.assertRaisesRegex(ValueError, 'finding_retry_limit must be between 2 and 100'):
                        declaration(scope)
            for limit in (2, 100):
                with self.subTest(schema=schema, limit=limit):
                    scope['stages']['finding_retry_limit'] = limit
                    self.assertEqual(declaration(scope)['finding_retry_limit'], limit)

    def test_stage_configuration_is_part_of_baseline_identity(self):
        from agent_ee.baseline import compile_baseline
        _, manifest = candidate(1, [])
        scope = manifest['scope']
        scope['baseline'] = {'requirements': [{'id': 'R', 'statement': 'expected RC',
            'source': {'path': 'requirements', 'locator': 'RC'}, 'checks': [c['id'] for c in scope['checks']]}],
            'targets': ['requirements'], 'reference_architectures': ['requirements']}
        files = {'requirements': 'fixed', 'schematic': 'native'}
        first = compile_baseline(scope, files)
        scope['stages']['subjects']['schematic']['references'].append('J1')
        self.assertNotEqual(first['baseline_id'], compile_baseline(scope, files)['baseline_id'])


class GateFindingTests(unittest.TestCase):
    def test_malformed_advisory_is_rejected(self):
        from agent_ee.findings import validate_check_result
        check = {'id': 'erc', 'kind': 'erc', 'execution': 'ok', 'outcome': 'defect',
                 'summary': 'missing R1', 'evidence': [], 'details': {'gate_findings': [
                     {'check_id': 'foreign', 'entity': 'R1', 'kind': 'missing',
                      'suggested_fix': {'op': 'move', 'params': {}}}]}}
        with self.assertRaisesRegex(ValueError, 'gate finding'):
            validate_check_result(check)

    def test_explicit_null_findings_are_malformed(self):
        from agent_ee.findings import validate_check_result
        with self.assertRaisesRegex(ValueError, 'gate findings'):
            validate_check_result({'id': 'erc', 'kind': 'erc', 'execution': 'ok', 'outcome': 'defect',
                                   'summary': 'invalid', 'evidence': [], 'details': {'gate_findings': None}})


class NativePresenceTests(unittest.TestCase):
    def test_native_empty_and_nested_library_inventory_are_distinct(self):
        import tempfile
        from pathlib import Path
        from agent_ee.stages import native_references
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'design.kicad_sch'
            path.write_text('(kicad_sch (version 20250114) (lib_symbols '
                            '(symbol "Owned:R" (property "Reference" "R"))))')
            self.assertEqual(native_references(path, 'symbols'), [])
            path.write_text('(kicad_sch (version 20250114) '
                            '(symbol (property "Reference" "R1")) '
                            '(symbol (property "Reference" "C1")))')
            self.assertEqual(native_references(path, 'symbols'), ['C1', 'R1'])
            path.write_text('(kicad_sch (version 20250114) '
                            '(symbol (property "Reference" "R1")) '
                            '(symbol (in_bom no) (on_board no) (property "Reference" "#PWR01")))')
            self.assertEqual(native_references(path, 'symbols'), ['R1'])
            # Emitted power symbols and flags keep the library's in_bom/on_board yes.
            path.write_text('(kicad_sch (version 20250114) '
                            '(symbol (property "Reference" "R1")) '
                            '(symbol (in_bom yes) (on_board yes) (property "Reference" "#FLG01")))')
            self.assertEqual(native_references(path, 'symbols'), ['R1'])
            path.write_text('(kicad_sch (version 20250114) (sheet (uuid "child")))')
            with self.assertRaisesRegex(ValueError, 'hierarchical'):
                native_references(path, 'symbols')


from toolchain import toolchain_test


class StageExecutionTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        from agent_ee.candidate import capture
        from agent_ee.storage import write_json
        from toolchain import require_toolchain
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        source = self.root / 'source'
        source.mkdir()
        fixtures = Path(__file__).resolve().parents[1] / 'fixtures/stages'
        (source / 'design.kicad_sch').write_bytes((fixtures / 'empty.kicad_sch').read_bytes())
        (source / 'design.kicad_pcb').write_bytes((fixtures / 'empty.kicad_pcb').read_bytes())
        (source / 'requirements').write_text('Original synthetic RC: R1 and C1 must be present.')
        self.scope = {'schema': 'agent-ee.scope/2', 'name': 'stage-protocol',
            'board': 'design/design.kicad_pcb', 'schematic': 'design/design.kicad_sch',
            'inputs': [{'path': str(source), 'at': 'design'}],
            'checks': [{'id': 'erc', 'kind': 'erc', 'stage': 'schematic'},
                       {'id': 'place', 'kind': 'geometry', 'stage': 'placement'},
                       {'id': 'drc', 'kind': 'drc', 'stage': 'routing'}],
            'stages': {'schema': 'agent-ee.stages/1', 'order': ['schematic', 'placement', 'routing'],
                'subjects': {'schematic': {'path': 'design/design.kicad_sch', 'kind': 'symbols',
                                         'references': ['R1', 'C1']},
                             'placement': {'path': 'design/design.kicad_pcb', 'kind': 'footprints',
                                           'references': ['R1', 'C1']}}, 'finding_retry_limit': 4},
            'baseline': {'requirements': [{'id': 'RC', 'statement': 'Expected RC exists',
                'source': {'path': 'design/requirements', 'locator': 'RC'}, 'checks': ['erc', 'place', 'drc']}],
                'targets': ['design/requirements'], 'reference_architectures': ['design/requirements']}}
        write_json(self.root / 'scope.json', self.scope)
        self.run = self.root / 'run'
        self.run.mkdir()
        self.manifest = capture(self.root / 'scope.json', self.run)

    def checks(self):
        import time
        from agent_ee.checks import run_checks
        return run_checks(self.run, time.monotonic() + 60)

    @toolchain_test
    def test_empty_candidate_records_sealed_absence_and_blocks_later_handlers(self):
        from unittest.mock import patch
        from agent_ee.baseline import freeze_evidence, verify_evidence
        from agent_ee.storage import read_json, write_json
        with patch.dict('agent_ee.checks.CHECK_FUNCS', {kind: self.fail_handler for kind in ('erc', 'geometry', 'drc')}):
            results = self.checks()
        self.assertEqual([(c['execution'], c['outcome']) for c in results], [('ok', 'defect')] * 3)
        self.assertEqual([c['details']['reason'] for c in results],
                         ['stage_not_reached', 'stage_not_reached', 'stage_blocked'])
        witness = read_json(self.run / 'work/erc/stage-subject.json')
        self.assertEqual(witness['missing'], ['C1', 'R1'])
        self.assertEqual(witness['candidate_id'], self.manifest['candidate_id'])
        freeze_evidence(self.run, results)
        verify_evidence(self.run, results, required=True)
        witness['missing'] = []
        write_json(self.run / 'work/erc/stage-subject.json', witness)
        with self.assertRaisesRegex(ValueError, 'expected-content witness'):
            freeze_evidence(self.run, results)

    def fail_handler(self, ctx):
        self.fail('later native handler must not run on an empty subject')

    def populated_capture(self):
        from agent_ee.candidate import capture
        path = self.root / 'source/design.kicad_sch'
        from pathlib import Path
        path.write_bytes((Path(__file__).resolve().parents[1] / 'fixtures/stages/schematic.kicad_sch').read_bytes())
        self.run = self.root / 'populated'
        self.run.mkdir()
        self.manifest = capture(self.root / 'scope.json', self.run)

    @toolchain_test
    def test_handler_crash_and_missing_output_are_unknown_and_never_absence(self):
        from unittest.mock import patch
        from agent_ee.checks import CheckError
        self.populated_capture()
        def failed(ctx):
            raise CheckError('failed', 'scripted native failure')
        with patch.dict('agent_ee.checks.CHECK_FUNCS', {'erc': failed, 'geometry': self.fail_handler, 'drc': self.fail_handler}):
            results = self.checks()
        self.assertEqual(results[0]['execution'], 'failed')
        self.assertEqual(results[0]['outcome'], 'unknown')
        self.assertNotIn('reason', results[0]['details'])
        self.assertEqual([(c['execution'], c['outcome']) for c in results[1:]], [('not_run', 'unknown')] * 2)
        self.run = self.root / 'missing-output'
        self.run.mkdir()
        from agent_ee.candidate import capture
        capture(self.root / 'scope.json', self.run)
        def missing(ctx):
            ctx.load_json('missing.json', 'native result')
        with patch.dict('agent_ee.checks.CHECK_FUNCS', {'erc': missing, 'geometry': self.fail_handler, 'drc': self.fail_handler}):
            results = self.checks()
        self.assertEqual(results[0]['execution'], 'malformed')
        self.assertEqual(results[0]['outcome'], 'unknown')
        self.assertNotIn('reason', results[0]['details'])

    @toolchain_test
    def test_missing_probe_output_is_unknown(self):
        from unittest.mock import patch
        def no_output(inputs, work, argv, **kwargs):
            return {'execution': 'ok'}
        with patch('agent_ee.executor.execute', side_effect=no_output):
            results = self.checks()
        self.assertEqual(results[0]['execution'], 'malformed')
        self.assertEqual(results[0]['outcome'], 'unknown')
        self.assertNotIn('reason', results[0]['details'])
        self.assertEqual(results[1]['execution'], 'not_run')

    @toolchain_test
    def test_malformed_finding_from_handler_becomes_unknown(self):
        from unittest.mock import patch
        self.populated_capture()
        def malformed(ctx):
            ctx.result.update(execution='ok', outcome='defect', summary='bad findings')
            ctx.result['details']['gate_findings'] = [{'check_id': ctx.cid, 'entity': 'R1', 'kind': 'bad',
                                                     'suggested_fix': {'op': 'move', 'params': []}}]
        with patch.dict('agent_ee.checks.CHECK_FUNCS', {'erc': malformed}):
            results = self.checks()
        self.assertEqual(results[0]['execution'], 'malformed')
        self.assertEqual(results[0]['outcome'], 'unknown')
        self.assertNotIn('gate_findings', results[0]['details'])
        self.assertEqual(results[1]['execution'], 'not_run')


class ExistingCampaignStageTests(unittest.TestCase):
    @toolchain_test
    def test_existing_campaign_rechecks_writer_proposal_and_replays_stage_history(self):
        import contextlib
        import io
        import test_campaign
        from agent_ee.campaign import run_campaign, verify_campaign
        from agent_ee.storage import write_json
        fixture = test_campaign.CampaignIntegrationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        from toolchain import require_toolchain
        require_toolchain(native=True)
        fixture.scope['schematic'] = 'design/design.kicad_sch'
        fixture.scope['checks'][0]['stage'] = 'schematic'
        route = copy.deepcopy(fixture.scope['checks'][0])
        route.update(id='route', stage='routing')
        fixture.scope['checks'].append(route)
        fixture.scope['baseline']['requirements'][0]['checks'].append('route')
        fixture.scope['stages'] = {'schema': 'agent-ee.stages/1', 'order': ['schematic', 'routing'],
            'subjects': {'schematic': {'path': 'design/design.kicad_sch', 'kind': 'symbols', 'references': ['R1']}},
            'finding_retry_limit': 2}
        from pathlib import Path
        (fixture.root / 'source/design.kicad_sch').write_bytes(
            (Path(__file__).resolve().parents[1] / 'fixtures/stages/schematic.kicad_sch').read_bytes())
        write_json(fixture.root / 'scope.json', fixture.scope)
        with contextlib.redirect_stdout(io.StringIO()):
            status = run_campaign(fixture.root / 'campaign.json', fixture.root / 'campaign')
        state = verify_campaign(fixture.root / 'campaign')
        self.assertEqual(status, 0, state)
        self.assertEqual(state['history'][0]['earliest_failing_stage'], 'schematic')
        self.assertEqual(state['history'][0]['blocked_checks'], ['route'])
        self.assertIsNone(state['history'][1]['earliest_failing_stage'])
        self.assertEqual(state['history'][1]['passing'], ['response', 'route'])
        self.assertEqual(fixture.calls.count('implementation'), 1)


class NativeStageTransitionTests(unittest.TestCase):
    setUp = StageExecutionTests.setUp
    checks = StageExecutionTests.checks
    @toolchain_test
    def test_original_snapshots_advance_only_after_native_presence_and_copper_checks(self):
        from pathlib import Path
        from unittest.mock import patch
        from agent_ee.candidate import capture
        from agent_ee.storage import write_json
        fixtures = Path(__file__).resolve().parents[1] / 'fixtures/stages'
        self.scope['checks'][-1].update(kind='geometry', params={'min_track_width_mm': 0.2})
        self.scope['stages']['subjects']['routing'] = {'path': 'design/design.kicad_pcb',
                                                      'kind': 'tracks', 'minimum': 1}
        write_json(self.root / 'scope.json', self.scope)
        history = []
        # ERC is explicitly scripted here. Presence and board geometry run in real sandboxes.
        def scripted_erc(ctx):
            ctx.result.update(execution='ok', outcome='pass', summary='scripted ERC control')
        snapshots = [('empty.kicad_sch', 'empty.kicad_pcb', 'schematic'),
                     ('schematic.kicad_sch', 'empty.kicad_pcb', 'placement'),
                     ('schematic.kicad_sch', 'placed.kicad_pcb', 'routing'),
                     ('schematic.kicad_sch', 'routed.kicad_pcb', None)]
        for index, (schematic, board, earliest) in enumerate(snapshots):
            (self.root / 'source/design.kicad_sch').write_bytes((fixtures / schematic).read_bytes())
            (self.root / 'source/design.kicad_pcb').write_bytes((fixtures / board).read_bytes())
            self.run = self.root / f'snapshot-{index}'
            self.run.mkdir()
            manifest = capture(self.root / 'scope.json', self.run)
            with patch.dict('agent_ee.checks.CHECK_FUNCS', {'erc': scripted_erc}):
                checks = self.checks()
            self.assertTrue(all(c['execution'] == 'ok' for c in checks), checks)
            if index == 0:
                self.assertEqual([c['details']['reason'] for c in checks], ['stage_not_reached'] * 3)
                self.assertTrue(all(c['outcome'] == 'defect' for c in checks))
            outcome = 'defects_or_unresolved' if earliest else 'criteria_satisfied'
            record, diagnostic = advance(history, {'checks': checks, 'run_id': str(index), 'outcome': outcome}, manifest, 8)
            self.assertEqual(record['earliest_failing_stage'], earliest)
            self.assertEqual(diagnostic['state'], 'repair' if earliest else 'criteria_satisfied')
            history.append(record)


class NativeFailureBoundaryTests(unittest.TestCase):
    setUp = StageExecutionTests.setUp
    checks = StageExecutionTests.checks
    @toolchain_test
    def test_parseable_but_native_unsupported_subject_is_unknown(self):
        from agent_ee.candidate import capture
        (self.root / 'source/design.kicad_sch').write_text('(kicad_sch (version 99999999) (lib_symbols))')
        self.run = self.root / 'unsupported'
        self.run.mkdir()
        capture(self.root / 'scope.json', self.run)
        checks = self.checks()
        self.assertNotEqual(checks[0]['execution'], 'ok')
        self.assertEqual(checks[0]['outcome'], 'unknown')
        self.assertNotIn('reason', checks[0]['details'])
        self.assertEqual(checks[1]['execution'], 'not_run')
