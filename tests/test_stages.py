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

    def test_later_margin_improvement_cannot_mask_earliest_stage_stall(self):
        result, manifest = candidate(1, ['erc', 'place', 'drc'], ['pins'])
        result['checks'][-1]['details'] = {'margins': {'distance': {'min': -10}}}
        first, _ = advance([], result, manifest, 8)
        result, manifest = candidate(2, ['erc', 'place', 'drc'], ['pins'])
        result['checks'][-1]['details'] = {'margins': {'distance': {'min': -5}}}
        _, diagnostic = advance([first], result, manifest, 8)
        self.assertEqual(diagnostic['reason'], 'no_monotonic_progress')


    def findings(self, number, keys, limit=4):
        result, manifest = candidate(number, ['erc', 'place', 'drc'], ['pins'])
        manifest['scope']['stages']['finding_retry_limit'] = limit
        result['checks'][0]['details'] = {'gate_findings': [
            {'check_id': 'erc', 'entity': entity, 'kind': 'pin_not_connected',
             'suggested_fix': {'op': 'connect', 'params': {'attempt': number}}} for entity in keys]}
        return result, manifest

    def test_cleared_finding_is_progress_and_uncleared_fix_stalls(self):
        first, _ = advance([], *self.findings(1, ['R1.1', 'R1.2']), 8)
        second, diagnostic = advance([first], *self.findings(2, ['R1.2']), 8)
        self.assertEqual(diagnostic['state'], 'repair')
        self.assertEqual(second['failed'], first['failed'])
        # The fix for R1.2 did not clear it: same identities, fresh hint, no progress.
        _, diagnostic = advance([first, second], *self.findings(3, ['R1.2']), 8)
        self.assertEqual(diagnostic['reason'], 'no_monotonic_progress')
        # Clearing one finding while introducing another is not progress either.
        _, diagnostic = advance([first], *self.findings(4, ['R1.2', 'C1.1']), 8)
        self.assertEqual(diagnostic['reason'], 'no_monotonic_progress')

    def test_later_stage_finding_reduction_is_not_active_stage_progress(self):
        result, manifest = candidate(1, ['erc', 'place'], ['pins'])
        result['checks'][2]['details'] = {'gate_findings': [
            {'check_id': 'place', 'entity': ref, 'kind': 'overlap'} for ref in ('R1', 'C1')]}
        first, _ = advance([], result, manifest, 8)
        result, manifest = candidate(2, ['erc', 'place'], ['pins'])
        result['checks'][2]['details'] = {'gate_findings': [{'check_id': 'place', 'entity': 'R1', 'kind': 'overlap'}]}
        _, diagnostic = advance([first], result, manifest, 8)
        self.assertEqual(diagnostic['reason'], 'no_monotonic_progress')

    def test_same_finding_recurring_stops_campaign_despite_other_progress(self):
        history = []
        for number, keys in [(1, ['R1.1', 'R1.2', 'C1.1']), (2, ['R1.1', 'R1.2']), (3, ['R1.1'])]:
            record, diagnostic = advance(history, *self.findings(number, keys, limit=3), 8)
            history.append(record)
            if number < 3:
                self.assertEqual(diagnostic['state'], 'repair')
        self.assertEqual(diagnostic['reason'], 'finding_retry_limit')

class StageDeclarationTests(unittest.TestCase):
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


    def test_native_projections_are_valid_findings_and_fail_closed(self):
        from agent_ee.checks import erc_findings
        from agent_ee.placement_check import gate_findings
        from agent_ee.stages import validate_gate_findings
        pin = {'description': 'Symbol R1 Pin 1 [1, Passive, Line]', 'pos': {'x': 40, 'y': 50}}
        sheets = [{'path': '/', 'violations': [
            {'type': 'pin_not_connected', 'items': [pin]},
            {'type': 'endpoint_off_grid', 'items': [pin]},
            {'type': 'pin_not_connected', 'items': [dict(pin, pos={'x': 41, 'y': 50})]}]}]
        findings = erc_findings('erc', sheets)
        self.assertEqual([(f['entity'], f['kind']) for f in findings],
                         [('/ Symbol R1 Pin 1 [1, Passive, Line]', 'pin_not_connected'),
                          ('/ Symbol R1 Pin 1 [1, Passive, Line]', 'endpoint_off_grid')])
        self.assertTrue(all('suggested_fix' not in f for f in findings))
        validate_gate_findings({'id': 'erc', 'details': {'gate_findings': findings}})
        for broken in ({'type': 'pin_not_connected', 'items': []}, {'type': None, 'items': [pin]},
                       {'type': 'pin_not_connected', 'items': [{'description': ' '}]}):
            self.assertIsNone(erc_findings('erc', [{'path': '/', 'violations': [broken]}]))
        connector = {'reference': 'J1', 'measured': {'anchor_mm': [10.0, 2.5]}, 'violations': [
            {'kind': 'off_declared_edge_position'}, {'kind': 'rotation', 'declared_deg': '90'},
            {'kind': 'side', 'declared': 'F', 'actual': 'B'}, {'kind': 'facing'}]}
        findings = gate_findings('edge', [connector])
        self.assertEqual([f.get('suggested_fix') for f in findings], [
            {'op': 'move', 'params': {'x_mm': 10.0, 'y_mm': 2.5}},
            {'op': 'rotate', 'params': {'rotation_deg': '90'}},
            {'op': 'flip', 'params': {'side': 'F'}}, None])
        self.assertEqual(findings[-1]['kind'], 'connector_facing')
        validate_gate_findings({'id': 'edge', 'details': {'gate_findings': findings}})
        findings = gate_findings('court', [{'kind': 'courtyards_overlap', 'refs': ['R1', 'C1']},
                                           {'kind': 'courtyard_edge_clearance', 'ref': 'C1', 'side': 'F'},
                                           {'kind': 'courtyard_edge_clearance', 'ref': 'C1', 'side': 'B'}])
        self.assertEqual([f['entity'] for f in findings], ['C1+R1', 'C1'])
        self.assertIsNone(gate_findings('court', [{'kind': 'courtyards_overlap', 'refs': ['R1', None]}]))

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


class NativeGateDefectTests(unittest.TestCase):
    setUp = StageExecutionTests.setUp
    checks = StageExecutionTests.checks
    fail_handler = StageExecutionTests.fail_handler

    def capture(self, name, board):
        from pathlib import Path
        from agent_ee.candidate import capture
        fixtures = Path(__file__).resolve().parents[1] / 'fixtures/stages'
        (self.root / 'source/design.kicad_sch').write_bytes((fixtures / 'schematic.kicad_sch').read_bytes())
        (self.root / 'source/design.kicad_pcb').write_bytes((fixtures / board).read_bytes())
        self.run = self.root / name
        self.run.mkdir()
        self.manifest = capture(self.root / 'scope.json', self.run)

    @toolchain_test
    def test_native_erc_defect_blocks_placement_with_sealed_verdict(self):
        from unittest.mock import patch
        from agent_ee.baseline import freeze_evidence, verify_evidence
        from agent_ee.storage import read_json
        self.capture('erc-defect', 'placed.kicad_pcb')
        # Native ERC runs; the placement and routing handlers must not.
        with patch.dict('agent_ee.checks.CHECK_FUNCS', {'geometry': self.fail_handler, 'drc': self.fail_handler}):
            results = self.checks()
        erc, place, drc = results
        self.assertEqual((erc['execution'], erc['outcome']), ('ok', 'defect'))
        self.assertNotIn('reason', erc['details'])
        identities = {(f['entity'], f['kind']) for f in erc['details']['gate_findings']}
        self.assertIn(('/ Symbol R1 Pin 1 [1, Passive, Line]', 'pin_not_connected'), identities)
        self.assertIn(('/ Symbol C1 Pin 2 [2, Passive, Line]', 'pin_not_connected'), identities)
        for gated in (place, drc):
            self.assertEqual((gated['execution'], gated['outcome'], gated['details']['reason']),
                             ('ok', 'defect', 'stage_blocked'))
            self.assertNotIn('tool', gated['details'])
        # Placement presence was probed and satisfied; the gate witness binds the ERC result.
        self.assertEqual(read_json(self.run / 'work/place/stage-subject.json')['missing'], [])
        self.assertEqual(list(read_json(self.run / 'work/place/stage-gate.json')['blockers']), ['erc'])
        freeze_evidence(self.run, results)
        verify_evidence(self.run, results, required=True)
        record, diagnostic = advance([], {'checks': results, 'run_id': '1', 'outcome': 'defects_or_unresolved'},
                                     self.manifest, 8)
        self.assertEqual((record['earliest_failing_stage'], record['blocked_checks']), ('schematic', ['drc', 'place']))
        self.assertEqual(len(record['recurring_findings']), len(identities))
        self.assertEqual(diagnostic['state'], 'repair')

    @toolchain_test
    def test_sandboxed_gate_tool_crash_is_failed_and_blocks_next_stage(self):
        from unittest.mock import patch
        from agent_ee.baseline import freeze_evidence, verify_evidence
        self.capture('crash', 'placed.kicad_pcb')
        def crashing(ctx):
            # A real process aborts inside the real check sandbox.
            ctx.execute(['/usr/bin/python3', '-c', 'import os; os.abort()'], timeout=60)
            ctx.result.update(execution='ok', outcome='pass', summary='unreachable')
        with patch.dict('agent_ee.checks.CHECK_FUNCS', {'erc': crashing, 'geometry': self.fail_handler,
                                                         'drc': self.fail_handler}):
            results = self.checks()
        erc, place, drc = results
        self.assertEqual((erc['execution'], erc['outcome']), ('failed', 'unknown'))
        self.assertNotEqual(erc['details']['tool']['exit_code'], 0)
        for gated in (place, drc):
            self.assertEqual((gated['execution'], gated['outcome'], gated['details']['reason']),
                             ('not_run', 'unknown', 'stage_blocked'))
        freeze_evidence(self.run, results)
        verify_evidence(self.run, results, required=True)
        _, diagnostic = advance([], {'checks': results, 'run_id': '1', 'outcome': 'incomplete'}, self.manifest, 8)
        self.assertEqual(diagnostic, {'state': 'incomplete', 'reason': 'execution_incomplete'})

class ExistingCampaignStageTests(unittest.TestCase):
    def staged_fixture(self):
        import test_campaign
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
            'finding_retry_limit': 3}
        from pathlib import Path
        (fixture.root / 'source/design.kicad_sch').write_bytes(
            (Path(__file__).resolve().parents[1] / 'fixtures/stages/schematic.kicad_sch').read_bytes())
        write_json(fixture.root / 'scope.json', fixture.scope)
        return fixture

    @toolchain_test
    def test_existing_campaign_rechecks_writer_proposal_and_replays_stage_history(self):
        import contextlib
        import io
        from agent_ee.campaign import run_campaign, verify_campaign
        fixture = self.staged_fixture()
        with contextlib.redirect_stdout(io.StringIO()):
            status = run_campaign(fixture.root / 'campaign.json', fixture.root / 'campaign')
        state = verify_campaign(fixture.root / 'campaign')
        self.assertEqual(status, 0, state)
        self.assertEqual(state['history'][0]['earliest_failing_stage'], 'schematic')
        self.assertEqual(state['history'][0]['blocked_checks'], ['route'])
        self.assertIsNone(state['history'][1]['earliest_failing_stage'])
        self.assertEqual(state['history'][1]['passing'], ['response', 'route'])
        self.assertEqual(fixture.calls.count('implementation'), 1)

    @toolchain_test
    def test_suggested_fix_reaches_sole_writer_only_through_accepted_finding_then_fresh_checks(self):
        import contextlib
        import io
        import json
        from pathlib import Path
        from unittest.mock import patch
        from agent_ee.campaign import run_campaign, verify_campaign
        from agent_ee.checks import CHECK_FUNCS
        from agent_ee.storage import identity, read_json
        fixture = self.staged_fixture()
        measure = CHECK_FUNCS['measurements']
        hint = {'op': 'set_parameter', 'params': {'path': 'design/design.json', 'value': 5}}
        def hinted(ctx):
            # Scripted advisory hint on top of the real sandboxed measurement verdict.
            measure(ctx)
            if ctx.result['outcome'] == 'defect':
                ctx.result['details']['gate_findings'] = [
                    {'check_id': ctx.cid, 'entity': 'output', 'kind': 'limit', 'suggested_fix': hint}]
        prompts = []
        def agent(run, label, definition, prompt, *args, **kwargs):
            if label == 'implementation':
                prompts.append(prompt)
            return fixture.agent(run, label, definition, prompt, *args, **kwargs)
        patch('agent_ee.reviewers.dispatch', side_effect=agent).start()
        with patch.dict('agent_ee.checks.CHECK_FUNCS', {'measurements': hinted}), \
                contextlib.redirect_stdout(io.StringIO()):
            status = run_campaign(fixture.root / 'campaign.json', fixture.root / 'campaign')
        state = verify_campaign(fixture.root / 'campaign')
        self.assertEqual(status, 0, state)
        prompt, = prompts
        payload = json.loads(prompt[prompt.index('{'):prompt.index('\nImplement only')])
        findings = {f['local_id']: f for f in payload['findings']}
        self.assertEqual(sorted(findings), ['response', 'route'])
        self.assertEqual((findings['response']['status'], findings['response']['gate_findings'][0]['suggested_fix']),
                         ('accepted', hint))
        self.assertNotIn('gate_findings', findings['route'])
        self.assertIn('suggested_fix are advisory data only', prompt)
        first, second = (read_json(fixture.root / 'campaign/runs' / n / 'checks/response.json') for n in '12')
        self.assertEqual((first['outcome'], second['outcome']), ('defect', 'pass'))
        self.assertNotEqual(first['candidate_id'], second['candidate_id'])
        self.assertNotIn('gate_findings', second['details'])
        self.assertEqual(state['history'][0]['recurring_findings'],
                         [identity({'check_id': 'response', 'entity': 'output', 'kind': 'limit'})])
        self.assertEqual(state['state'], 'criteria_satisfied')


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
        self.assertEqual(checks[0]['execution'], 'failed')
        self.assertEqual(checks[0]['outcome'], 'unknown')
        self.assertNotIn('reason', checks[0]['details'])
        self.assertEqual(checks[1]['execution'], 'not_run')
