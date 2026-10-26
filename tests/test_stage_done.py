"""Done through declared stage steps (decision 1 amendment, AGENTEE-144).

Synthetic records exercise the provenance rule; campaigns in the real executor sandbox, with
scripted writers and an original test step, exercise the campaign done rule and verify-campaign.
No tool here is a router or placer; the step stands in for any declared stage step.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_ee.campaign import verify_campaign
from agent_ee.stage_done import SCHEMA, stage_done, verify_done
from agent_ee.storage import digest, read_json, write_json

try:
    import test_stage_receipts
    from toolchain import toolchain_test
except ImportError:  # module-qualified invocation from the repository root
    from tests import test_stage_receipts
    from tests.toolchain import toolchain_test

BOARD, SCH, NOTES = 'project/board.kicad_pcb', 'project/board.kicad_sch', 'project/notes.txt'


def spec(stage, step, writes):
    return {'schema': 'agent-ee.stage-step/1', 'stage': stage, 'step': step,
            'argv': ['/usr/bin/python3', '/tool/step.py'], 'mounts': [{'source': '/nonexistent', 'at': '/tool'}],
            'writes': {rel: f'design/{rel}' for rel in writes}, 'timeout_s': 10}


class Records:
    """Retained campaign records in the shapes the campaign writes: runs, indexes, receipts."""

    def __init__(self, root, files, *, staged=True, order=None):
        from agent_ee.stage_receipts import STAGES, validate_step
        self.order = STAGES if order is None else order
        self.validate = lambda s: validate_step(s, host=False)
        self.root = Path(root)
        self.staged = staged
        self.state = {'state': 'criteria_satisfied', 'history': [], 'stage_receipts': {}}
        self.files = dict(files)
        self.run(None)

    def run(self, turn, scope_path=None):
        n = len(self.state['history']) + 1
        path = self.root / 'runs' / str(n)
        write_json(path / 'candidate.json', {'candidate_id': f'candidate-{n}', 'files': dict(self.files),
            'scope': {'stages': {'order': list(self.order)} if self.staged else None}, 'scope_path': scope_path})
        self.state['history'].append({'run': str(path), 'earliest_failing_stage': turn})

    def implementation(self, writer=None, steps=(), after=None):
        """One implementation: writer edits, then coordinator steps, then the captured proposal."""
        n = len(self.state['history'])
        turn = self.state['history'][-1]['earliest_failing_stage']
        work = self.root / 'implementations' / str(n)
        self.files.update(writer or {})
        links = []
        for k, (declaration, outputs, *status) in enumerate(steps, 1):
            normalized = self.validate(declaration)
            receipt = {'spec': normalized, 'status': status[0] if status else 'completed',
                       'inputs': dict(self.files), 'outputs': {rel: outputs.get(rel, self.files[rel])
                                                               for rel in normalized['writes']}}
            path = work / 'receipts' / f"{k}-{normalized['step']}" / 'receipt.json'
            write_json(path, receipt)
            links.append({'path': path.relative_to(work).as_posix(), 'sha256': digest(path)})
            if receipt['status'] in ('completed', 'partial'):
                self.files.update(receipt['outputs'])
        write_json(work / 'stage-receipts.json', {'receipts': links})
        self.state['stage_receipts'][str(n)] = digest(work / 'stage-receipts.json')
        self.files.update(after or {})
        return work

    def next(self, turn, *, captured=True):
        work = self.root / 'implementations' / str(len(self.state['history']))
        self.run(turn, str(work / 'proposal.scope.json') if captured else None)

    def done(self, config):
        return stage_done(self.root, self.state, config)


class StageDoneRuleTests(unittest.TestCase):
    """The provenance rule on synthetic records; receipt validity itself is verify-campaign's job."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.place = spec('placement', 'build-board', [BOARD])
        self.route = spec('routing', 'route-krt', [BOARD])
        self.fab = spec('fab', 'export-fab', [BOARD])
        self.emit = spec('schematic', 'emit-schematic', [SCH])
        self.config = {'implementation_steps': [self.emit, self.place, self.route, self.fab]}

    def records(self, **kwargs):
        return Records(self.root, {BOARD: 'empty', SCH: 'empty', NOTES: 'n0'}, **kwargs)

    def rung1(self, *, verification_writer=None):
        """rung-1 shape: schematic, placement, routing, then fab in place on the shared board."""
        r = self.records()
        r.state['history'][0]['earliest_failing_stage'] = 'schematic'
        # The schematic-turn writer may prepare the board; build-board later replaces it.
        r.implementation(writer={BOARD: 'writer draft', NOTES: 'n1'}, steps=[(self.emit, {SCH: 'emitted'})])
        r.next('placement')
        r.implementation(steps=[(self.place, {BOARD: 'placed'})])
        r.next('routing')
        r.implementation(steps=[(self.route, {BOARD: 'routed'})])
        r.next('verification')
        r.implementation(writer={NOTES: 'n2', **(verification_writer or {})})
        r.next('fab')
        r.implementation(steps=[(self.fab, {})])
        r.next(None)
        return r

    def test_receipted_chain_across_shared_board_binds_every_declared_stage(self):
        r = self.rung1()
        record = r.done(self.config)
        self.assertEqual(record['schema'], SCHEMA)
        self.assertEqual(record['candidate_id'], 'candidate-6')
        board = record['paths'][BOARD]
        self.assertEqual(board['sha256'], 'routed')
        self.assertEqual(board['producer']['step'], 'export-fab')
        self.assertEqual({s: v['step'] for s, v in board['stages'].items()},
                         {'placement': 'build-board', 'routing': 'route-krt', 'fab': 'export-fab'})
        self.assertEqual(record['paths'][SCH]['producer']['implementation'], 1)
        # Stages with no declared step keep the writer as producer: the notes file is not bound.
        self.assertNotIn(NOTES, record['paths'])
        state = {**r.state, 'stage_done': record}
        verify_done(self.root, state, self.config)
        for forged in ({**record, 'identity': '0' * 64}, None):
            with self.subTest(forged=bool(forged)):
                with self.assertRaisesRegex(ValueError, 'done binding differs from the retained stage receipts'):
                    verify_done(self.root, {**r.state, 'stage_done': forged}, self.config)

    def test_step_of_a_stage_outside_the_scope_order_is_excluded_not_required(self):
        # rung-1 campaign shape: export-fab is declared, but fab is not a scope stage, so it never runs.
        r = Records(self.root, {BOARD: 'empty', SCH: 'empty', NOTES: 'n0'},
                    order=['schematic', 'placement', 'routing', 'verification'])
        r.implementation(steps=[(self.emit, {SCH: 'emitted'})])
        r.next('placement')
        r.implementation(steps=[(self.place, {BOARD: 'placed'})])
        r.next('routing')
        r.implementation(steps=[(self.route, {BOARD: 'routed'})])
        r.next(None)
        record = r.done(self.config)
        self.assertEqual(record['excluded'], [{'stage': 'fab', 'step': 'export-fab',
                                               'reason': 'stage not in the scope stage order'}])
        self.assertEqual(list(record['paths'][BOARD]['stages']), ['placement', 'routing'])
        self.assertEqual(record['paths'][BOARD]['producer']['step'], 'route-krt')

    def test_mutable_intent_changed_after_emission_fails_done(self):
        intent = 'generation/connectivity-intent.json'
        emit = {**self.emit, 'inputs': [intent]}
        r = Records(self.root, {SCH: 'empty', intent: 'intent-1'}, order=['schematic', 'verification'])
        r.implementation(writer={intent: 'intent-2'}, steps=[(emit, {SCH: 'emitted'})])
        r.next('verification')
        self.assertEqual(r.done({'implementation_steps': [emit]})['paths'][SCH]['sha256'], 'emitted')
        r.implementation(writer={intent: 'intent-3'})
        r.next(None)
        with self.assertRaisesRegex(ValueError, 'stage_step_stale_input: schematic/emit-schematic'):
            r.done({'implementation_steps': [emit]})

    def test_input_binding_requires_each_declared_producer(self):
        intent = 'generation/connectivity-intent.json'
        emit = {**self.emit, 'inputs': [intent]}
        annotate = {**spec('schematic', 'forward-annotate', [BOARD]), 'inputs': [intent]}
        r = Records(self.root, {SCH: 'empty', BOARD: 'empty', intent: 'i1'}, order=['schematic'])
        r.implementation(steps=[(emit, {SCH: 'emitted'})])
        r.next(None)
        with self.assertRaisesRegex(ValueError, 'stage_step_unreceipted: schematic/forward-annotate'):
            r.done({'implementation_steps': [emit, annotate]})

    def test_writer_hand_routing_after_the_routing_receipt_fails_done(self):
        # The verification writer edits the routed board; the in-place fab step passes it through.
        r = self.rung1(verification_writer={BOARD: 'hand-routed'})
        with self.assertRaisesRegex(ValueError, r'writer_made_stage_content: project/board.kicad_pcb changed by '
                                                r'writer in implementation 4 \(turn verification\) after the '
                                                r'last receipt of declared stage routing'):
            r.done(self.config)
        with self.assertRaisesRegex(ValueError, 'writer_made_stage_content'):
            verify_done(self.root, r.state, self.config)

    def test_declared_stage_without_its_receipt_fails_done(self):
        # The writer places the board itself in the schematic turn and placement then passes.
        r = self.records()
        r.implementation(writer={BOARD: 'writer placed'}, steps=[(self.emit, {SCH: 'emitted'})])
        r.next('routing')
        r.implementation(steps=[(self.route, {BOARD: 'routed'})])
        r.next('fab')
        r.implementation(steps=[(self.fab, {})])
        r.next(None)
        with self.assertRaisesRegex(ValueError, 'stage_step_unreceipted: placement/build-board has no adopted '
                                                'receipt producing project/board.kicad_pcb'):
            r.done(self.config)

    def test_initial_content_that_passes_without_any_step_fails_done(self):
        r = self.records()
        with self.assertRaisesRegex(ValueError, 'stage_step_unreceipted: schematic/emit-schematic has no adopted '
                                                'receipt producing project/board.kicad_sch'):
            r.done({'implementation_steps': [self.emit]})

    def test_unadoptable_receipt_never_counts(self):
        r = self.records()
        r.implementation(writer={SCH: 'writer schematic'}, steps=[(self.emit, {SCH: 'emitted'}, 'failed')])
        r.next(None)
        with self.assertRaisesRegex(ValueError, 'stage_step_unreceipted: schematic/emit-schematic'):
            r.done({'implementation_steps': [self.emit]})

    def test_writer_edit_to_schematic_after_emission_fails_even_in_an_undeclared_stage(self):
        r = self.records()
        r.implementation(steps=[(self.emit, {SCH: 'emitted'})])
        r.next('verification')
        r.implementation(writer={SCH: 'writer fixed a symbol'})
        r.next(None)
        with self.assertRaisesRegex(ValueError, 'writer_made_stage_content: project/board.kicad_sch changed by '
                                                'writer in implementation 2'):
            r.done({'implementation_steps': [self.emit]})

    def test_graded_content_must_be_the_last_step_output(self):
        r = self.records()
        r.implementation(steps=[(self.emit, {SCH: 'emitted'})], after={SCH: 'changed after adoption'})
        r.next(None)
        with self.assertRaisesRegex(ValueError, 'changed by coordinator in implementation 1'):
            r.done({'implementation_steps': [self.emit]})

    def test_run_not_captured_from_the_implementation_proposal_is_unattributed(self):
        r = self.records()
        r.implementation(steps=[(self.emit, {SCH: 'emitted'})])
        r.next('verification')
        r.files[SCH] = 'replaced outside an implementation'
        r.next(None, captured=False)
        with self.assertRaisesRegex(ValueError, 'changed by outside_implementation in implementation 2'):
            r.done({'implementation_steps': [self.emit]})

    def test_unstaged_writer_input_consumed_by_every_later_step_is_allowed(self):
        r = self.records(staged=False)
        r.implementation(writer={SCH: 'draft 1'}, steps=[(self.emit, {SCH: 'emitted 1'})])
        r.next(None)
        r.implementation(writer={SCH: 'draft 2'}, steps=[(self.emit, {SCH: 'emitted 2'})])
        r.next(None)
        record = r.done({'implementation_steps': [self.emit]})
        self.assertEqual(record['paths'][SCH]['sha256'], 'emitted 2')
        self.assertEqual(record['paths'][SCH]['producer']['implementation'], 2)

    def test_placement_adapter_moves_between_annotation_and_routing_are_allowed(self):
        # rung-1 qualified shape: forward annotation writes the board, the placement adapter moves
        # footprints (writer and helper), then the routing step routes.
        annotate = spec('schematic', 'forward-annotate', [BOARD])
        helper = spec('placement', 'placer', [BOARD])
        config = {'implementation_steps': [self.emit, annotate, self.route],
                  'placement_adapter': {'schema': 'agent-ee.placement-adapter/1'}}
        r = self.records()
        r.implementation(steps=[(self.emit, {SCH: 'emitted'}), (annotate, {BOARD: 'annotated'})])
        r.next('placement')
        r.implementation(writer={BOARD: 'writer moved'}, steps=[(helper, {BOARD: 'packed'})])
        r.next('routing')
        r.implementation(steps=[(self.route, {BOARD: 'routed'})])
        r.next(None)
        record = r.done(config)
        self.assertEqual(record['paths'][BOARD]['producer']['step'], 'route-krt')
        # A placement-adapter turn after routing would change routed content.
        r.state['history'][-1]['earliest_failing_stage'] = 'placement'
        r.implementation(writer={BOARD: 'moved after routing'})
        r.next(None)
        with self.assertRaisesRegex(ValueError, 'changed by adapter_writer in implementation 4 .* declared stage '
                                                'routing'):
            r.done(config)

    def test_campaign_without_declared_steps_is_unaffected(self):
        r = self.records()
        r.implementation(writer={BOARD: 'writer board', SCH: 'writer schematic'})
        r.next(None)
        self.assertIsNone(r.done({'implementation_steps': []}))
        verify_done(self.root, r.state, {})
        with self.assertRaisesRegex(ValueError, 'done binding differs'):
            verify_done(self.root, {**r.state, 'stage_done': {'schema': SCHEMA}}, {})

    def test_tampered_receipt_or_index_hash_refuses(self):
        r = self.rung1()
        receipt = self.root / 'implementations/3/receipts/1-route-krt/receipt.json'
        data = read_json(receipt)
        data['outputs'][BOARD] = 'hand-routed'
        write_json(receipt, data)
        with self.assertRaisesRegex(ValueError, 'stage receipt changed after indexing'):
            r.done(self.config)
        index = self.root / 'implementations/3/stage-receipts.json'
        links = read_json(index)
        links['receipts'][0]['sha256'] = digest(receipt)
        write_json(index, links)
        with self.assertRaisesRegex(ValueError, 'stage receipt index changed or missing'):
            r.done(self.config)

    def test_routing_adapter_spec_is_a_declared_routing_step(self):
        r = self.records()
        r.state['routing_binding'] = {'spec': self.route}
        r.implementation(writer={BOARD: 'hand-routed'})
        r.next(None)
        with self.assertRaisesRegex(ValueError, 'stage_step_unreceipted: routing/route-krt'):
            r.done({'routing_adapter': {}})
        with self.assertRaisesRegex(ValueError, 'routing adapter has no campaign binding'):
            stage_done(self.root, {**r.state, 'routing_binding': None}, {'routing_adapter': {}})


class DriverDoneTests(unittest.TestCase):
    """The rung-1 drivers' done rule includes the stage-step binding of the verified campaign."""

    def test_driver_term_requires_a_binding_when_the_campaign_declares_steps(self):
        from agent_ee.stage_done import driver_term
        record = {'schema': SCHEMA, 'candidate_id': 'c', 'paths': {BOARD: {}}, 'identity': 'i'}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(driver_term(root, {}), {'required': None, 'stage_done': None, 'bound': True})
            write_json(root / 'config.json', {'implementation_steps': [spec('routing', 'route-krt', [BOARD])]})
            self.assertEqual(driver_term(root, {'state': 'criteria_satisfied'})['bound'], False)
            self.assertEqual(driver_term(root, {'stage_done': {'schema': SCHEMA, 'paths': {}}})['bound'], False)
            self.assertEqual(driver_term(root, {'stage_done': record}),
                             {'required': True, 'stage_done': record, 'bound': True})
            write_json(root / 'config.json', {'routing_adapter': {}})
            self.assertEqual(driver_term(root, None)['bound'], False)
            write_json(root / 'config.json', {'implementation_steps': []})
            self.assertEqual(driver_term(root, None), {'required': False, 'stage_done': None, 'bound': True})

    def test_rung1_records_are_not_done_without_the_binding(self):
        from agent_ee import rung1_campaign as rc, rung1_qualified as rq
        # AGENTEE-126: fab artifacts carry the sealed CPL rotation status; a record without one is never done.
        stored = {'artifacts_id': 'a', 'candidate_id': 'c', 'files': {'work/fab/fab/bom.csv': '1' * 64},
                  'rotation': {'status': 'not_declared'}}
        native = {'rotation_status': 'not_declared'}
        unbound = {'required': True, 'stage_done': None, 'bound': False}
        bound = {'required': True, 'stage_done': {'schema': SCHEMA}, 'bound': True}
        with tempfile.TemporaryDirectory() as tmp:
            for stage, done in ((unbound, False), (bound, True)):
                with self.subTest(bound=done):
                    record = rc.write_record(tmp, {}, verdict='criteria_satisfied', fab_artifacts=stored,
                                             recomputed=dict(stored), stage=stage, scaffold={'bound': True})
                    self.assertEqual((record['done'], record['stage_receipts_done']), (done, stage))
                    # AGENTEE-95: the scaffold receipt binding remains a separate done term.
                    self.assertFalse(rc.write_record(tmp, {}, verdict='criteria_satisfied', fab_artifacts=stored,
                                                     recomputed=dict(stored), stage=stage, scaffold=None)['done'])
                    # AGENTEE-126: the rotation status remains a separate done term.
                    unstated = {k: v for k, v in stored.items() if k != 'rotation'}
                    self.assertFalse(rc.write_record(tmp, {}, verdict='criteria_satisfied', fab_artifacts=unstated,
                                                     recomputed=dict(unstated), stage=stage,
                                                     scaffold={'bound': True})['done'])
                    self.assertIn('agent-ee.stage-done/1', record['done_rule'])
                    record = rq.write_record(tmp, {}, verdict='criteria_satisfied', verify_exit=0, fab=native, stage=stage)
                    self.assertEqual((record['done'], record['stage_receipts_done']), (done, stage))
                    self.assertFalse(rq.write_record(tmp, {}, verdict='criteria_satisfied', verify_exit=0, stage=stage)['done'])
                    self.assertIn('agent-ee.stage-done/1', record['done_rule'])
            self.assertFalse(rq.write_record(tmp, {}, verdict='criteria_satisfied', verify_exit=1, fab=native,
                                             stage=bound)['done'])


class StageDoneCampaignTests(unittest.TestCase):
    """Real campaigns: a routing-stage step, then a verification turn with no declared step."""

    # The stage-receipt campaign fixture, reused without re-running its tests.
    fixture = test_stage_receipts.CampaignStageReceiptTests
    setUp, agent, step, campaign, staged_scope = (fixture.setUp, fixture.agent, fixture.step, fixture.campaign,
                                                  fixture.staged_scope)

    def staged(self):
        self.staged_scope()
        source = self.root / 'source'
        write_json(source / 'verify.json', {'value': 15})
        (source / 'solver2.py').write_text((source / 'solver.py').read_text().replace('design.json', 'verify.json'))
        scope = read_json(self.root / 'scope.json')
        route = {**scope['checks'][0], 'stage': 'routing'}
        verify = json.loads(json.dumps({**route, 'id': 'verify', 'stage': 'verification'}))
        verify['params']['tool'].update(argv=['/usr/bin/python3', '/candidate/design/solver2.py'],
                                        inputs=['design/solver2.py'])
        scope['checks'] = [route, verify]
        scope['baseline']['requirements'][0]['checks'] = ['response', 'verify']
        scope['mutable_inputs'] = ['design/design.json', 'design/verify.json']
        scope['stages']['order'] = ['routing', 'verification']
        scope['stages']['subjects'] = {'routing': scope['stages']['subjects'].pop('placement')}
        write_json(self.root / 'scope.json', scope)

    def run_writer(self, hand_route):
        def writer(run, label, *args, **kwargs):
            if label == 'implementation' and Path(run).name == '2':
                design = Path(run) / 'review-work/implementation/design/design'
                write_json(design / 'verify.json', {'value': 5})
                if hand_route:
                    write_json(design / 'design.json', {'value': 6})
            return self.agent(run, label, *args, **kwargs)
        self.staged()
        with patch('agent_ee.reviewers.dispatch', side_effect=writer):
            return self.campaign([self.step(stage='routing', step='route-helper')])

    @toolchain_test
    def test_receipted_routing_step_is_done_and_writer_verification_content_is_unaffected(self):
        status, state = self.run_writer(hand_route=False)
        self.assertEqual((status, state['state']), (0, 'criteria_satisfied'), state.get('error'))
        self.assertEqual([h['earliest_failing_stage'] for h in state['history']], ['routing', 'verification', None])
        done = state['stage_done']
        final = read_json(Path(state['history'][-1]['run']) / 'candidate.json')
        self.assertEqual(done['candidate_id'], final['candidate_id'])
        self.assertEqual((list(done['paths']), done['excluded']), (['design/design.json'], []))
        root = self.root / 'campaign'
        receipt = root / 'implementations/1/receipts/1-route-helper/receipt.json'
        self.assertEqual(done['paths']['design/design.json'], {
            'sha256': read_json(receipt)['outputs']['design/design.json'],
            'producer': {'stage': 'routing', 'step': 'route-helper', 'implementation': 1, 'receipt_index': 1,
                         'receipt_sha256': digest(receipt)},
            'stages': {'routing': {'stage': 'routing', 'step': 'route-helper', 'implementation': 1,
                                   'receipt_index': 1, 'receipt_sha256': digest(receipt)}}})
        self.assertEqual(final['files']['design/design.json'], done['paths']['design/design.json']['sha256'])
        self.assertEqual(verify_campaign(root, host=self.host)['state'], 'criteria_satisfied')
        # A forged or dropped binding is refused; the binding is recomputed, never trusted.
        for forged in ({**done, 'candidate_id': 'forged'}, None):
            write_json(root / 'state.json', {**state, 'stage_done': forged})
            with self.assertRaisesRegex(ValueError, 'stage-step done binding differs'):
                verify_campaign(root, host=self.host)
        write_json(root / 'state.json', state)
        verify_campaign(root, host=self.host)
        # A changed receipt output hash is refused before any done decision.
        data = read_json(receipt)
        data['outputs']['design/design.json'] = '0' * 64
        write_json(receipt, data)
        with self.assertRaisesRegex(ValueError, 'stage receipt'):
            verify_campaign(root, host=self.host)

    @toolchain_test
    def test_writer_hand_edit_of_routing_output_fails_done_although_checks_pass(self):
        status, state = self.run_writer(hand_route=True)
        self.assertEqual(status, 1, state)
        self.assertEqual(state['state'], 'incomplete')
        self.assertEqual(state['diagnostic'], {'state': 'criteria_satisfied'})
        final = read_json(Path(state['history'][-1]['run']) / 'result.json')
        self.assertEqual(final['outcome'], 'criteria_satisfied')
        self.assertIn('writer_made_stage_content: design/design.json changed by writer in implementation 2 '
                      '(turn verification) after the last receipt of declared stage routing', state['error'])
        self.assertNotIn('stage_done', state)
        self.assertNotIn('fab_artifacts', state)
        root = self.root / 'campaign'
        self.assertEqual(verify_campaign(root, host=self.host)['state'], 'incomplete')
        # Relabelling the refusal as done is refused by verify-campaign.
        forged = {k: v for k, v in state.items() if k != 'error'}
        write_json(root / 'state.json', {**forged, 'state': 'criteria_satisfied'})
        with self.assertRaisesRegex(ValueError, 'writer_made_stage_content'):
            verify_campaign(root, host=self.host)

    @toolchain_test
    def test_campaign_without_declared_steps_keeps_the_writer_as_producer(self):
        def writer(run, label, *args, **kwargs):
            if label == 'implementation':
                design = Path(run) / 'review-work/implementation/design/design'
                write_json(design / ('design.json' if Path(run).name == '1' else 'verify.json'), {'value': 5})
            return self.agent(run, label, *args, **kwargs)
        self.staged()
        with patch('agent_ee.reviewers.dispatch', side_effect=writer):
            status, state = self.campaign([])
        self.assertEqual((status, state['state']), (0, 'criteria_satisfied'), state.get('error'))
        self.assertNotIn('stage_done', state)
        self.assertEqual(verify_campaign(self.root / 'campaign', host=self.host)['state'], 'criteria_satisfied')


if __name__ == '__main__':
    unittest.main()
