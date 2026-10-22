"""AGENTEE-260: coordinator layer_policy derivation at the pre-routing boundary.

An original four-layer probe board (tests.test_pour_budget), never a pod design. Only the
subscription reviewers are scripted and the router is the kicad-route fixture with stated via
facts; the plane producer, the receipt executor, the via-budget pilot (AGENTEE-265) and the
native pour_budget measurement run for real. Every derived bound comes from a facts-bound
agent-ee.via-budget/2 basis; a counts-only agent-ee.via-budget/1 basis leaves it unknown.
"""
import contextlib
import copy
import hashlib
import io
from pathlib import Path
import shutil
import tempfile
import unittest

from agent_ee import campaign, layer_policy_lineage as lineage, pour_budget, stage_receipts
from agent_ee import via_budget_pilot as pilot
from agent_ee.storage import digest, read_json, write_json
from tests import test_pour_budget as probe
from tests import test_routing_campaign as routing_fixture
from tests.toolchain import require_toolchain

NAME = 'layer-policy'
POLICY = {'signal_layers': ['F.Cu', 'B.Cu']}
COUNTS = (3, 4, 4)


def sha(path):
    """Hash recomputed here from the bytes, independently of storage.digest."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def counts_only_basis(board_sha256, counts=COUNTS):
    """The legacy agent-ee.via-budget/1 shape: stated counts, no retained pilot facts."""
    margin = max(1, -(-max(counts) // 10))
    return {'schema': lineage.VIA_BUDGET, 'via_budget': max(counts) + margin, 'board_sha256': board_sha256,
            'basis': {'method': 'routed_demand', 'pilot': 'diagnostic_not_adoptable', 'signal_vias': list(counts)}}


def derivation(**extra):
    return {'schema': lineage.SCHEMA, 'directory': NAME, 'policy': dict(POLICY), **extra}


def via(net, x, via_type='through'):
    return {'net': net, 'position': [x, 1.0], 'diameter': 0.6, 'drill': 0.3, 'via_type': via_type}


def scenario(counts, name='complete'):
    """Each attempt keeps the input's /IN via, adds a /GND stitch, a blind via and V_i signal vias."""
    return {'name': name, 'reference_vias': [via('/IN', 0.0)],
            'vias_by_label': {f'campaign-pilot-{i}': [via('/IN', 0.0), via('/GND', 9.0), via('/OUT', 8.0, 'blind'),
                                                      *(via('/OUT', float(k)) for k in range(1, n + 1))]
                              for i, n in enumerate(counts, 1)}}


def routing_input(source, root, board=None, name='pilot-input'):
    """The candidate tree the routing step receives: the scope inputs under design/."""
    design = Path(root) / name
    shutil.copytree(source, design / 'design')
    if board is not None:
        shutil.copyfile(board, design / 'design/rc.kicad_pcb')
    return design


class Lane:
    """A routing campaign whose plane producer adopts a real planed probe board.

    The fixture router states ``counts`` signal vias for the pilot attempts (AGENTEE-265)."""

    def __init__(self, boards, counts=COUNTS):
        self.fixture = routing_fixture.RoutingCampaignTests()
        self.fixture.setUp()
        self.root = self.fixture.root
        source = self.fixture.source
        shutil.copyfile(boards / 'placed-fixture/project/probe.kicad_pcb', source / 'rc.kicad_pcb')
        write_json(source / 'rc.kicad_pro', probe.PROJECT)
        self.planed = boards / 'placed-candidate/project/probe.kicad_pcb'
        script = self.root / 'add-planes.py'
        script.write_text('from pathlib import Path\n'
                          'Path("/work/board").write_bytes(Path("/tool/planed.kicad_pcb").read_bytes())\n')
        self.fixture.config['implementation_steps'] = [dict(
            schema='agent-ee.stage-step/1', stage='routing', step='add-planes',
            argv=['/usr/bin/python3', '-B', '/tool/add-planes.py'],
            mounts=[dict(source=str(script), at='/tool/add-planes.py'),
                    dict(source=str(self.planed), at='/tool/planed.kicad_pcb')],
            writes={'design/rc.kicad_pcb': 'board'}, adoptable_exits=[0], timeout_s=120)]
        self.fixture.config['max_seconds'] = 1800
        self.declaration = self.fixture.spec(scenario(counts))

    def pilot(self, board, out='pilot'):
        """Run the coordinator pilot on ``board`` in the scope tree; its agent-ee.via-budget/2 declaration."""
        design = routing_input(self.fixture.source, self.root, board, f'{out}-input')
        basis = pilot.run(design, self.declaration, POLICY, self.root / out)
        return pilot.via_budget(basis, self.root / out / pilot.BASIS_FILE)

    def run(self, layer_policy_derivation, *, declaration=None):
        write_json(self.root / 'scope.json', self.fixture.scope)
        write_json(self.root / 'campaign.json', {**self.fixture.config,
                                                 'routing_adapter': declaration or self.declaration,
                                                 'layer_policy_derivation': layer_policy_derivation})
        with contextlib.redirect_stdout(io.StringIO()):
            status = campaign.run_campaign(self.root / 'campaign.json', self.root / 'campaign')
        return status, read_json(self.root / 'campaign/state.json'), self.root / 'campaign'

    def close(self):
        self.fixture.doCleanups()


class LayerPolicyCampaignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)
        cls.stack = contextlib.ExitStack()
        try:
            cls.boards = Path(cls.stack.enter_context(tempfile.TemporaryDirectory(prefix='agent-ee-260-')))
            probe.build(cls.boards, probe.PADS, 'placed')
            cls.planed_sha = sha(cls.boards / 'placed-candidate/project/probe.kicad_pcb')
            cls.lane = Lane(cls.boards)
            cls.stack.callback(cls.lane.close)
            # N comes from a pilot on the planed routing input, re-derived at campaign start.
            cls.via_budget = cls.lane.pilot(cls.boards / 'placed-candidate/project/probe.kicad_pcb')
            cls.status, cls.state, cls.campaign_root = cls.lane.run(derivation(via_budget=cls.via_budget))
        except BaseException:
            cls.stack.close()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.stack.close()

    def work(self):
        return self.campaign_root / 'implementations/1'

    def refused(self, message):
        with self.assertRaisesRegex(ValueError, message):
            campaign.verify_campaign(self.campaign_root)

    def turn_refused(self, message):
        """A re-anchored record: the next candidate's capture no longer matches (lineage), and the
        turn's own re-derivation refuses it independently."""
        self.refused('not the coordinator binding of the pre-routing derivation')
        state = read_json(self.campaign_root / 'state.json')
        receipt = read_json(self.work() / 'receipts/2-kicad-route-builtin/receipt.json')
        with self.assertRaisesRegex(ValueError, message):
            lineage.verify_turn(self.campaign_root, state, read_json(self.campaign_root / 'config.json'), 1,
                                self.planed_sha, receipt)

    @contextlib.contextmanager
    def tampered(self, path, change, *, reanchor=False):
        """Edit a retained file; with reanchor, also move the state anchor to the new bytes."""
        original = path.read_bytes()
        state_path = self.campaign_root / 'state.json'
        state_bytes = state_path.read_bytes()
        try:
            path.write_bytes(change(original))
            if reanchor:
                state = read_json(state_path)
                state['layer_policy_records']['1'] = digest(path)
                write_json(state_path, state)
            yield
        finally:
            path.write_bytes(original)
            state_path.write_bytes(state_bytes)

    def edit_record(self, change):
        def apply(data):
            import json
            record = json.loads(data)
            change(record)
            return json.dumps(record).encode()
        return apply

    def test_derived_policy_is_bound_before_routing_and_into_a_new_derived_scope(self):
        self.assertEqual((self.status, self.state['state']), (0, 'criteria_satisfied'), self.state.get('error'))
        campaign.verify_campaign(self.campaign_root)
        work = self.work()
        record = read_json(work / 'layer-policy/record.json')
        self.assertEqual(self.state['layer_policy_records'], {'1': sha(work / 'layer-policy/record.json')})
        # The routing input is the adopted planed board, measured by the coordinator.
        planes = stage_receipts.validate_receipt(work / 'receipts/1-add-planes')
        routing_input = stage_receipts.after_tree(planes)['design/rc.kicad_pcb']
        self.assertEqual(routing_input, self.planed_sha)
        self.assertEqual((record['status'], record['board_sha256']), ('derived', self.planed_sha))
        measured = read_json(work / 'layer-policy/measure/pour-budget.json')
        self.assertEqual(record['measurement']['sha256'], sha(work / 'layer-policy/measure/pour-budget.json'))
        self.assertEqual((self.via_budget['schema'], self.via_budget['board_sha256'],
                          self.via_budget['basis']['signal_vias']), (lineage.VIA_BUDGET_PILOT, self.planed_sha,
                                                                     list(COUNTS)))
        self.assertEqual(record['via_budget'], read_json(self.campaign_root / 'config.json')
                         ['layer_policy_derivation']['via_budget'])
        expected = pour_budget.derive(measured, self.via_budget['via_budget'])
        self.assertEqual(record['derivation'], expected)
        self.assertEqual(record['layer_policy'], {**POLICY, 'pour_area_loss_pct': expected['pour_area_loss_pct']})
        self.assertGreater(expected['pour_area_loss_pct'], 0)
        # The router ran with exactly that bound; only --layer-policy differs from the frozen binding.
        receipt = stage_receipts.validate_receipt(work / 'receipts/2-kicad-route-builtin')
        self.assertEqual(receipt['inputs']['design/rc.kicad_pcb'], self.planed_sha)
        report = read_json(work / 'receipts/2-kicad-route-builtin/run/routing/adapter-report.json')
        self.assertEqual(report['request']['layer_policy'], record['layer_policy'])
        self.assertEqual(lineage.declared_form(receipt['spec']), self.state['routing_binding']['spec'])
        self.assertNotIn('--layer-policy', self.state['routing_binding']['spec']['argv'])
        # The next candidate carries the coordinator capture in a new derived scope; the sealed scope does not.
        first = read_json(self.campaign_root / 'runs/1/candidate.json')
        second = read_json(self.campaign_root / 'runs/2/candidate.json')
        self.assertNotIn(lineage.DECLARATION, first['scope'])
        self.assertNotIn(lineage.DECLARATION, read_json(self.lane.root / 'scope.json'))
        self.assertEqual(second['scope_path'], str(work / 'proposal.scope.json'))
        self.assertEqual(second['scope'][lineage.DECLARATION], {
            'record': f'{NAME}/record.json', 'record_sha256': sha(work / 'layer-policy/record.json'),
            'measurement': f'{NAME}/pour-budget.json',
            'measurement_sha256': sha(work / 'layer-policy/measure/pour-budget.json')})
        self.assertEqual(sha(self.campaign_root / 'runs/2/inputs' / NAME / 'record.json'),
                         sha(work / 'layer-policy/record.json'))
        self.assertEqual({k: v for k, v in second['files'].items() if k.startswith(NAME + '/')},
                         {f'{NAME}/pour-budget.json': record['measurement']['sha256'],
                          f'{NAME}/record.json': self.state['layer_policy_records']['1']})

    def test_tampered_measurement_is_refused(self):
        path = self.work() / 'layer-policy/measure/pour-budget.json'
        with self.tampered(path, lambda b: b.replace(b'"execution": "ok"', b'"execution": "ok" ')):
            self.refused('measurement changed after the layer policy record')

    def test_tampered_record_is_refused_even_when_reanchored(self):
        path = self.work() / 'layer-policy/record.json'
        with self.tampered(path, lambda b: b + b' '):
            self.refused('layer policy record changed after it was anchored')
        # A re-anchored record still has to be the derivation of its retained measurement.
        def halve(record):
            record['derivation']['pours'] = {}
        with self.tampered(path, self.edit_record(halve), reanchor=True):
            self.turn_refused('differs from the derivation of its retained measurement')

    def test_post_hoc_relaxation_is_refused(self):
        path = self.work() / 'layer-policy/record.json'
        def relax(record):
            record['layer_policy']['pour_area_loss_pct'] = 100
            record['derivation']['pour_area_loss_pct'] = 100
        with self.tampered(path, self.edit_record(relax), reanchor=True):
            self.turn_refused('differs from the derivation of its retained measurement')
        # A larger N than the frozen basis is not the coordinator's either.
        def larger(record):
            record['via_budget']['via_budget'] += 10
        with self.tampered(path, self.edit_record(larger), reanchor=True):
            self.turn_refused('via budget differs from the frozen configuration')
        # Across attempts on the same routing input, a later bound may never exceed an earlier one.
        record = read_json(path)
        looser = copy.deepcopy(record)
        looser['layer_policy']['pour_area_loss_pct'] += 1
        config = read_json(self.campaign_root / 'config.json')
        with self.assertRaisesRegex(ValueError, 'relaxed after an earlier attempt'):
            lineage.verify_records(self.campaign_root, {'layer_policy_records': {'1': 'x', '2': 'y'}}, config,
                                   {'1': record, '2': looser})
        lineage.verify_records(self.campaign_root, {'layer_policy_records': {'1': 'x', '2': 'y'}}, config,
                               {'1': looser, '2': record})

    def test_stale_measurement_is_refused(self):
        path = self.work() / 'layer-policy/record.json'
        def stale(record):
            record['board_sha256'] = '0' * 64
        with self.tampered(path, self.edit_record(stale), reanchor=True):
            self.turn_refused('layer policy record is stale')
        report = read_json(self.work() / 'layer-policy/measure/pour-budget.json')
        config = read_json(self.campaign_root / 'config.json')
        status, reason, _, policy = lineage.decide(report, '1' * 64, config)
        self.assertEqual((status, policy), ('unknown', None))
        self.assertIn('not of this routing input', reason)

    def test_routing_receipt_with_another_bound_is_refused(self):
        work = self.work()
        config = read_json(self.campaign_root / 'config.json')
        source = read_json(self.campaign_root / 'runs/1/candidate.json')
        record = read_json(work / 'layer-policy/record.json')
        receipt = read_json(work / 'receipts/2-kicad-route-builtin/receipt.json')
        relaxed = lineage.routing_spec(config, source, self.state['mutable_inputs'],
                                       {**record['layer_policy'], 'pour_area_loss_pct': 100})
        with self.assertRaisesRegex(ValueError, 'not the coordinator derivation'):
            lineage.verify_turn(self.campaign_root, self.state, config, 1, self.planed_sha,
                                {**receipt, 'spec': relaxed})
        with self.assertRaisesRegex(ValueError, 'not the measured routing input'):
            lineage.verify_turn(self.campaign_root, self.state, config, 1, self.planed_sha,
                                {**receipt, 'inputs': {**receipt['inputs'], 'design/rc.kicad_pcb': '2' * 64}})
        other = copy.deepcopy(self.state['routing_binding'])
        other['spec']['argv'] = [*other['spec']['argv'][:-1], '9']
        with self.assertRaisesRegex(ValueError, 'differs from the frozen campaign routing binding'):
            lineage.turn_binding(other, receipt['spec'])

    def test_writer_planted_or_foreign_provenance_is_refused(self):
        config = read_json(self.campaign_root / 'config.json')
        state = read_json(self.campaign_root / 'state.json')
        first = read_json(self.campaign_root / 'runs/1/candidate.json')
        second = read_json(self.campaign_root / 'runs/2/candidate.json')
        lineage.verify_candidate(self.campaign_root, {**state, 'history': state['history'][:1]}, config, 2, second)
        planted = copy.deepcopy(first)
        planted['scope'][lineage.DECLARATION] = second['scope'][lineage.DECLARATION]
        with self.assertRaisesRegex(ValueError, 'not the coordinator binding'):
            lineage.verify_candidate(self.campaign_root, state, config, 1, planted)
        forged = copy.deepcopy(second)
        forged['scope'][lineage.DECLARATION]['record_sha256'] = '3' * 64
        with self.assertRaisesRegex(ValueError, 'not the coordinator binding'):
            lineage.verify_candidate(self.campaign_root, {**state, 'history': state['history'][:1]}, config, 2, forged)
        bare = copy.deepcopy(second)
        del bare['scope'][lineage.DECLARATION]
        with self.assertRaisesRegex(ValueError, 'not the coordinator binding'):
            lineage.verify_candidate(self.campaign_root, {**state, 'history': state['history'][:1]}, config, 2, bare)


class LayerPolicyUnknownCampaignTests(unittest.TestCase):
    """Without a measurement or an N basis for the routing input the policy is unknown; nothing routes."""

    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)
        cls.boards = Path(tempfile.mkdtemp(prefix='agent-ee-260-unknown-'))
        probe.build(cls.boards, probe.PADS, 'placed')
        cls.planed_sha = sha(cls.boards / 'placed-candidate/project/probe.kicad_pcb')

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.boards)

    def lane(self):
        lane = Lane(self.boards)
        self.addCleanup(lane.close)
        return lane

    def unknown(self, value, reason, lane=None):
        status, state, root = (lane or self.lane()).run(value)
        self.assertEqual((status, state['state']), (1, 'incomplete'))
        self.assertIn('layer policy unknown at the pre-routing boundary', state['error'])
        self.assertIn(reason, state['error'])
        work = root / 'implementations/1'
        record = read_json(work / 'layer-policy/record.json')
        self.assertEqual((record['status'], record['layer_policy'], record['derivation']), ('unknown', None, None))
        self.assertIn(reason, record['reason'])
        self.assertTrue((work / 'receipts/1-add-planes/receipt.json').is_file())
        self.assertFalse((work / 'receipts/2-kicad-route-builtin').exists())
        self.assertFalse((work / 'proposal.scope.json').exists())
        # The retained unknown record verifies; it is evidence, not acceptance.
        campaign.verify_campaign(root)
        return record

    def test_unknown_without_a_via_budget_basis(self):
        record = self.unknown(derivation(), 'no via budget N with a recorded basis')
        self.assertEqual(record['board_sha256'], self.planed_sha)
        self.assertIsNotNone(record['measurement'])

    def test_unknown_with_a_basis_from_another_board(self):
        # A genuine pilot, honestly declared, but run on the unplaned board instead of the routing input.
        lane = self.lane()
        unplaned = self.boards / 'placed-fixture/project/probe.kicad_pcb'
        via_budget = lane.pilot(unplaned)
        self.assertEqual(via_budget['board_sha256'], sha(unplaned))
        self.assertNotEqual(via_budget['board_sha256'], self.planed_sha)
        self.unknown(derivation(via_budget=via_budget), 'measured on another board', lane)

    def test_unknown_with_a_counts_only_v1_basis(self):
        # AGENTEE-265: stated counts of the right board are no longer enough to derive a bound.
        record = self.unknown(derivation(via_budget=counts_only_basis(self.planed_sha)),
                              f'counts-only {lineage.VIA_BUDGET} declaration')
        self.assertEqual((record['board_sha256'], record['via_budget']['schema']), (self.planed_sha, lineage.VIA_BUDGET))
        self.assertIsNotNone(record['measurement'])

    def test_unknown_when_the_measurement_fails(self):
        lane = self.lane()
        # A custom via size rule makes the measurement refuse (AGENTEE-257 fail-closed rule).
        (lane.fixture.source / 'rc.kicad_dru').write_text(
            '(version 1)\n(rule big (condition "A.NetName == \'SIG_A\'") (constraint via_diameter (min 0.8mm)))\n')
        via_budget = lane.pilot(self.boards / 'placed-candidate/project/probe.kicad_pcb')
        self.assertEqual(via_budget['board_sha256'], self.planed_sha)
        status, state, root = lane.run(derivation(via_budget=via_budget))
        self.assertEqual((status, state['state']), (1, 'incomplete'))
        self.assertIn('pour budget measurement failed', state['error'])
        record = read_json(root / 'implementations/1/layer-policy/record.json')
        self.assertEqual((record['status'], record['layer_policy']), ('unknown', None))
        self.assertFalse((root / 'implementations/1/receipts/2-kicad-route-builtin').exists())
        campaign.verify_campaign(root)


class LiteralLayerPolicyCampaignTests(unittest.TestCase):
    """Campaigns without the opt-in keep their exact routing binding and its replay."""

    def test_literal_policy_routing_receipt_is_still_verified_against_the_binding(self):
        require_toolchain()
        lane = routing_fixture.RoutingCampaignTests()
        lane.setUp()
        self.addCleanup(lane.doCleanups)
        declaration = {**lane.spec(), 'layer_policy': {**POLICY, 'pour_area_loss_pct': 50}}
        status, state, work = lane.run_campaign(declaration)
        self.assertEqual((status, state['state']), (0, 'criteria_satisfied'), state.get('error'))
        self.assertIn('--layer-policy', state['routing_binding']['spec']['argv'])
        self.assertNotIn('layer_policy_records', state)
        from unittest.mock import patch
        from agent_ee import routing_adapter
        with patch.object(routing_adapter, 'verify_campaign_receipt',
                          wraps=routing_adapter.verify_campaign_receipt) as verified:
            campaign.verify_campaign(lane.root / 'campaign')
        self.assertEqual([c.kwargs['binding'] for c in verified.call_args_list], [state['routing_binding']])
        from agent_ee.rf_review import campaign_routing_context
        context = campaign_routing_context(lane.root / 'campaign', state)
        self.assertEqual(context['receipt']['path'], str(work / 'receipts/1-kicad-route-builtin/receipt.json'))


class LayerPolicyConfigurationTests(unittest.TestCase):
    def test_via_budget_basis_is_recomputed(self):
        board = '5' * 64
        self.assertEqual(lineage.validate_via_budget(counts_only_basis(board))['via_budget'], 5)
        self.assertEqual(lineage.validate_via_budget(counts_only_basis(board, (0, 0, 0)))['via_budget'], 1)
        self.assertEqual(lineage.validate_via_budget(counts_only_basis(board, (20, 21, 22)))['via_budget'], 25)
        for change, message in [
                ({'via_budget': 9}, 'is not V_max'),
                ({'board_sha256': 'abc'}, 'sha256'),
                ({'basis': {'method': 'routed_demand', 'pilot': 'adopted', 'signal_vias': [3, 4, 4]}},
                 'diagnostic, non-adoptable pilot'),
                ({'basis': {'method': 'guess', 'pilot': 'diagnostic_not_adoptable', 'signal_vias': [3, 4, 4]}},
                 'routed_demand'),
                ({'basis': {'method': 'routed_demand', 'pilot': 'diagnostic_not_adoptable', 'signal_vias': [4, 4]}},
                 'at least 3'),
                ({'basis': {'method': 'routed_demand', 'pilot': 'diagnostic_not_adoptable', 'signal_vias': [1, 4, 4]},
                  'via_budget': 5}, 'not repeatable'),
                ({'via_budget': True}, 'integer')]:
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, message):
                lineage.validate_via_budget({**counts_only_basis(board), **change})

    def test_opt_in_refuses_literal_or_sealed_bounds(self):
        scope = {'inputs': [{'path': '/x', 'at': 'design'}]}
        routing = {'schema': 'agent-ee.routing-adapter/1'}
        config = {'routing_adapter': routing, 'layer_policy_derivation': derivation()}
        lineage.validate_campaign(config, scope)
        with self.assertRaisesRegex(ValueError, 'exclusive'):
            lineage.validate_campaign({**config, 'routing_adapter': {**routing, 'layer_policy': {
                **POLICY, 'pour_area_loss_pct': 5}}}, scope)
        with self.assertRaisesRegex(ValueError, 'cannot state pour_area_loss_pct'):
            lineage.validate_campaign({**config, 'layer_policy_derivation': {
                **derivation(), 'policy': {**POLICY, 'pour_area_loss_pct': 5}}}, scope)
        with self.assertRaisesRegex(ValueError, 'sealed scope cannot declare'):
            lineage.validate_campaign(config, {**scope, lineage.DECLARATION: {}})
        with self.assertRaisesRegex(ValueError, 'without a campaign layer_policy_derivation'):
            lineage.validate_campaign({'routing_adapter': routing}, {**scope, lineage.DECLARATION: {}})
        with self.assertRaisesRegex(ValueError, 'collides'):
            lineage.validate_campaign(config, {'inputs': [{'path': '/x', 'at': NAME}]})
        with self.assertRaisesRegex(ValueError, 'different capture directories'):
            lineage.validate_campaign({**config, 'rating_provenance': NAME}, scope)
        with self.assertRaisesRegex(ValueError, 'requires the owned routing_adapter'):
            lineage.validate_campaign({'layer_policy_derivation': derivation()}, scope)
        with self.assertRaisesRegex(ValueError, 'policy'):
            lineage.config_of({'layer_policy_derivation': {**derivation(), 'policy': {'signal_layers': []}}})
        with self.assertRaisesRegex(ValueError, 'directory'):
            lineage.config_of({'layer_policy_derivation': {**derivation(), 'directory': '../x'}})

    def test_unknown_without_a_measurement(self):
        config = {'layer_policy_derivation': derivation(via_budget=counts_only_basis('6' * 64))}
        for report, reason in [(None, 'no pour budget measurement'),
                               ({'schema': pour_budget.SCHEMA, 'execution': 'failed', 'reason': 'x'},
                                'measurement failed')]:
            status, why, derived, policy = lineage.decide(report, '6' * 64, config)
            self.assertEqual((status, derived, policy), ('unknown', None, None))
            self.assertIn(reason, why)

    def test_a_counts_only_v1_basis_is_unknown_even_for_this_routing_input(self):
        board = '6' * 64
        config = {'layer_policy_derivation': derivation(via_budget=counts_only_basis(board))}
        # Still valid configuration (the shape and N re-check), never a derived bound.
        self.assertEqual(lineage.config_of(config)['via_budget']['schema'], lineage.VIA_BUDGET)
        report = {'schema': pour_budget.SCHEMA, 'execution': 'ok', 'board_sha256': board}
        status, why, derived, policy = lineage.decide(report, board, config)
        self.assertEqual((status, derived, policy), ('unknown', None, None))
        self.assertIn(f'counts-only {lineage.VIA_BUDGET}', why)
        self.assertIn(lineage.VIA_BUDGET_PILOT, why)
        # A legacy basis of another board is unknown too, for the same reason.
        self.assertEqual(lineage.decide(report, board, {'layer_policy_derivation': derivation(
            via_budget=counts_only_basis('7' * 64))}), ('unknown', why, None, None))


if __name__ == '__main__':
    unittest.main()
