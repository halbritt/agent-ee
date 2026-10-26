"""AGENTEE-265: the coordinator via-budget pilot and its re-derivation at campaign start.

The router is the kicad-route fixture (tests.kicad_route_fixture) with stated via facts;
the sandbox, stage receipts, adapter verification, plane producer and native pour budget
measurement run for real. Boards are the original four-layer probe (tests.test_pour_budget).
"""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from agent_ee import campaign, kicad_route_attempt as attempt, layer_policy_lineage as lineage
from agent_ee import routing_adapter as routing, stage_receipts, via_budget_pilot as pilot
from agent_ee.storage import digest, read_json, write_json
from tests import test_layer_policy_lineage as layer_tests
from tests import test_pour_budget as probe
from tests import test_routing_campaign as routing_fixture
from tests.toolchain import require_toolchain

POLICY = {'signal_layers': ['F.Cu', 'B.Cu']}


def sha(path):
    """Hash recomputed here from the bytes, independently of storage.digest."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


via, scenario, routing_input = layer_tests.via, layer_tests.scenario, layer_tests.routing_input


@contextlib.contextmanager
def edited(path, change):
    original = path.read_bytes()
    try:
        path.write_bytes(change(original))
        yield
    finally:
        path.write_bytes(original)


def json_edit(change):
    def apply(data):
        value = json.loads(data)
        change(value)
        return json.dumps(value).encode()
    return apply


class SignalViaRuleTests(unittest.TestCase):
    def test_the_257_rule(self):
        ref = {'vias': [via('/IN', 0.0), via('/IN', 0.0), via('/OUT', 5.0)]}
        cand = {'vias': [via('/IN', 0.0), via('/IN', 0.0), via('/IN', 0.0),   # one new /IN via
                         via('/OUT', 5.0),                                      # existing
                         {**via('/OUT', 5.0), 'drill': 0.25},                  # changed drill: new
                         via('/GND', 7.0), via('/OUT', 6.0, 'micro')]}          # plane net, not through
        self.assertEqual(pilot.signal_vias(cand, ref), 2)
        self.assertEqual(pilot.signal_vias({'vias': []}, {'vias': []}), 0)
        for cand, ref, message in [({'tracks': []}, {'vias': []}, 'candidate facts has no via list'),
                                   ({'vias': []}, {}, 'reference facts has no via list'),
                                   ({'vias': [{'net': '/X'}]}, {'vias': []}, 'malformed via'),
                                   ({'vias': [{**via('/X', 1.0), 'drill': True}]}, {'vias': []}, 'malformed')]:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                pilot.signal_vias(cand, ref)

    def test_pilot_policy_is_permissive_and_keeps_signal_layers_and_costs(self):
        policy = {'signal_layers': ['F.Cu', 'B.Cu'], 'layer_costs': [1, 2],
                  'reference': {'net': '/GND', 'layers': ['In1.Cu'], 'rf_nets': ['/RF']}}
        self.assertEqual(pilot.pilot_policy(policy), {'signal_layers': ['F.Cu', 'B.Cu'], 'layer_costs': [1, 2],
                                                      'pour_area_loss_pct': 100})
        with self.assertRaisesRegex(ValueError, 'pilot layer policy'):
            pilot.pilot_policy({'signal_layers': []})

    def test_v2_declaration_shape(self):
        board = '5' * 64
        value = {**layer_tests.counts_only_basis(board), 'schema': lineage.VIA_BUDGET_PILOT,
                 'pilot': {'path': '/p/via-budget-basis.json', 'sha256': '6' * 64}}
        self.assertEqual(lineage.validate_via_budget(value), value)
        for change, message in [({'pilot': {'path': '/p/basis.json', 'sha256': '6' * 64}}, 'must name'),
                                ({'pilot': {'path': '/p/via-budget-basis.json'}}, 'path and sha256'),
                                ({'via_budget': 6}, 'is not V_max')]:
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, message):
                lineage.validate_via_budget({**value, **change})
        with self.assertRaisesRegex(ValueError, 'via_budget must be'):
            lineage.validate_via_budget({k: v for k, v in value.items() if k != 'pilot'})


class PilotTests(unittest.TestCase):
    """Pilots on the fixture router, outside any campaign."""

    def pilot(self, counts, name='complete', *, attempts=3, policy=POLICY):
        fixture = routing_fixture.RoutingCampaignTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        declaration = fixture.spec(scenario(counts, name))
        design = routing_input(fixture.source, fixture.root)
        out = fixture.root / 'pilot'
        basis = pilot.run(design, declaration, policy, out, attempts=attempts)
        return basis, out, design, declaration

    def test_pilot_derives_v_i_from_retained_facts_and_never_adopts(self):
        basis, out, design, declaration = self.pilot((3, 4, 4))
        self.assertEqual((basis['status'], basis['reason']), ('derived', None))
        self.assertEqual((basis['signal_vias'], basis['margin'], basis['via_budget']), ([3, 4, 4], 1, 5))
        self.assertEqual((basis['pilot'], basis['adoptable'], basis['acceptance']),
                         ('diagnostic_not_adoptable', False, 'not_accepted'))
        board_sha = sha(design / 'design/rc.kicad_pcb')
        self.assertEqual(basis['board_sha256'], board_sha)
        self.assertEqual(basis['pilot_policy'], {**POLICY, 'pour_area_loss_pct': 100})
        for index, entry in enumerate(basis['attempts'], 1):
            receipt_dir = out / 'attempts' / str(index)
            receipt = stage_receipts.validate_receipt(receipt_dir)
            self.assertEqual(entry['receipt_sha256'], sha(receipt_dir / 'receipt.json'))
            self.assertEqual(receipt['binding'], {'via_budget_pilot': index, 'adoptable': False})
            self.assertEqual(receipt['inputs']['design/rc.kicad_pcb'], board_sha)
            self.assertEqual(entry['classification'], 'complete')
            for key in ('candidate_facts', 'reference_facts'):
                self.assertEqual(entry[key]['sha256'], sha(out / entry[key]['path']))
            self.assertEqual(entry['signal_vias'], pilot.signal_vias(
                read_json(out / entry['candidate_facts']['path']), read_json(out / entry['reference_facts']['path'])))
            report = read_json(receipt_dir / 'run/routing/adapter-report.json')
            self.assertEqual(report['request']['label'], f'campaign-pilot-{index}')
            self.assertEqual(report['request']['layer_policy'], basis['pilot_policy'])
            # Only the label and layer policy differ from the campaign routing declaration.
            final = attempt.parse_request(routing.declaration_spec(pilot.declaration(declaration))['argv'][4:])
            self.assertEqual({k: v for k, v in report['request'].items() if k not in ('label', 'layer_policy')},
                             {k: v for k, v in final.items() if k != 'label'})
            self.assertNotIn('layer_policy', final)
        # The routing input is never written; nothing is adopted.
        self.assertEqual(sha(design / 'design/rc.kicad_pcb'), board_sha)
        self.assertNotIn('routed by fixture', (design / 'design/rc.kicad_pcb').read_text())
        self.assertEqual(pilot.verify(out / pilot.BASIS_FILE), basis)
        # The basis survives a move: verify re-derives from the copy.
        moved = out.parent / 'moved'
        pilot.copy(out, moved)
        self.assertEqual(pilot.verify(moved / pilot.BASIS_FILE), basis)
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(pilot.main(['verify', '--basis', str(out / pilot.BASIS_FILE)]), 0)
        declared = json.loads(printed.getvalue())
        self.assertEqual(declared['pilot'], {'path': str(out / pilot.BASIS_FILE), 'sha256': sha(out / pilot.BASIS_FILE)})
        self.assertEqual(lineage.validate_via_budget(declared)['via_budget'], 5)

    def test_tampered_facts_or_basis_are_refused(self):
        basis, out, _, _ = self.pilot((3, 4, 4))
        path = out / pilot.BASIS_FILE
        facts = out / basis['attempts'][0]['candidate_facts']['path']
        with edited(facts, json_edit(lambda v: v['vias'].append(via('/OUT', 99.0)))):
            with self.assertRaisesRegex(ValueError, 'differs from the re-derivation'):
                pilot.verify(path)
        # Facts and the adapter report's own hash moved together: the receipt still refuses.
        report = out / 'attempts/1/run/routing/adapter-report.json'
        with edited(facts, json_edit(lambda v: v['vias'].append(via('/OUT', 99.0)))):
            with edited(report, json_edit(lambda v: v['retained'].update(
                    {pilot.FACTS['candidate_facts']: digest(facts)}))):
                with self.assertRaisesRegex(ValueError, 'differs from the re-derivation'):
                    pilot.verify(path)
        for change in (lambda v: v.update(signal_vias=[4, 4, 4]),
                       lambda v: v.update(via_budget=6),
                       lambda v: v.update(board_sha256='4' * 64),
                       lambda v: v.update(adoptable=True),
                       lambda v: v['attempts'][0].update(signal_vias=4),
                       lambda v: v.update(pilot_policy={**POLICY, 'pour_area_loss_pct': 5})):
            with self.subTest(change=change), edited(path, json_edit(change)):
                with self.assertRaises(ValueError):
                    pilot.verify(path)
        shutil.rmtree(out / 'attempts/3')
        with self.assertRaisesRegex(ValueError, 'attempts differ from the basis'):
            pilot.verify(path)

    def test_an_incomplete_attempt_leaves_n_unknown(self):
        basis, out, _, _ = self.pilot((3, 4, 4), 'incomplete')
        self.assertEqual((basis['status'], basis['via_budget'], basis['signal_vias']), ('unknown', None, None))
        self.assertIn('classified incomplete, not complete', basis['reason'])
        self.assertEqual(pilot.verify(out / pilot.BASIS_FILE), basis)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(pilot.main(['verify', '--basis', str(out / pilot.BASIS_FILE)]), 1)
        with self.assertRaisesRegex(ValueError, 'derived no via budget'):
            pilot.via_budget(basis, out / pilot.BASIS_FILE)

    def test_unrepeatable_attempts_leave_n_unknown(self):
        basis, _, _, _ = self.pilot((1, 4, 4))
        self.assertEqual((basis['status'], basis['signal_vias'], basis['via_budget']), ('unknown', [1, 4, 4], None))
        self.assertIn('not repeatable', basis['reason'])

    def test_facts_without_vias_leave_n_unknown(self):
        no_vias = {'name': 'complete'}
        fixture = routing_fixture.RoutingCampaignTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        declaration = fixture.spec(no_vias)
        basis = pilot.run(routing_input(fixture.source, fixture.root), declaration, POLICY, fixture.root / 'pilot')
        self.assertEqual((basis['status'], basis['via_budget']), ('unknown', None))
        self.assertIn('no via list', basis['reason'])

    def test_run_refuses_bad_requests(self):
        fixture = routing_fixture.RoutingCampaignTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        declaration = fixture.spec(scenario((1, 1, 1)))
        design = routing_input(fixture.source, fixture.root)
        for kwargs, message in [({'attempts': 2}, '3 to 20 attempts'),
                                ({'declaration_value': {**declaration, 'layer_policy': {
                                    **POLICY, 'pour_area_loss_pct': 5}}}, 'without a literal layer_policy'),
                                ({'out': design / 'pilot'}, 'outside the routing input')]:
            call = {'declaration_value': declaration, 'out': fixture.root / 'pilot', 'attempts': 3, **kwargs}
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                pilot.run(design, call['declaration_value'], POLICY, call['out'], attempts=call['attempts'])
        self.assertFalse((fixture.root / 'pilot').exists())

    def test_a_pilot_attempt_is_never_an_adoptable_pre_receipt(self):
        basis, out, _, _ = self.pilot((0, 0, 0))
        link = {'path': 'attempts/1/receipt.json', 'sha256': basis['attempts'][0]['receipt_sha256'],
                'binding': {'via_budget_pilot': 1, 'adoptable': False}}
        with self.assertRaisesRegex(ValueError, 'never adoptable'):
            stage_receipts.validate_pre_receipt(out, link)


class PilotLane(layer_tests.Lane):
    """The AGENTEE-260 routing lane (its fixture router states via facts) with a named campaign root."""

    def start(self, layer_policy_derivation, out, *, declaration=None):
        write_json(self.root / 'scope.json', self.fixture.scope)
        write_json(self.root / 'campaign.json', {**self.fixture.config,
                                                 'routing_adapter': declaration or self.declaration,
                                                 'layer_policy_derivation': layer_policy_derivation})
        with contextlib.redirect_stdout(io.StringIO()):
            status = campaign.run_campaign(self.root / 'campaign.json', self.root / out, host=self.host)
        return status, read_json(self.root / out / 'state.json'), self.root / out


class PilotCampaignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)
        cls.stack = contextlib.ExitStack()
        try:
            cls.boards = Path(cls.stack.enter_context(tempfile.TemporaryDirectory(prefix='agent-ee-265-')))
            probe.build(cls.boards, probe.PADS, 'placed')
            planed = cls.boards / 'placed-candidate/project/probe.kicad_pcb'
            cls.planed_sha = sha(planed)
            cls.lane = PilotLane(cls.boards, (3, 4, 4))
            cls.stack.callback(cls.lane.close)
            # The pilot runs on the planed routing input the campaign's add-planes step delivers.
            design = routing_input(cls.lane.fixture.source, cls.lane.root, planed)
            cls.pilot_dir = cls.lane.root / 'pilot'
            cls.basis = pilot.run(design, cls.lane.declaration, POLICY, cls.pilot_dir)
            cls.basis_path = cls.pilot_dir / pilot.BASIS_FILE
            cls.via_budget = pilot.via_budget(cls.basis, cls.basis_path)
            cls.status, cls.state, cls.campaign_root = cls.lane.start(
                layer_tests.derivation(via_budget=cls.via_budget), 'campaign')
        except BaseException:
            cls.stack.close()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.stack.close()

    def refused_at_start(self, via_budget, message, out, *, declaration=None):
        with self.assertRaisesRegex(ValueError, message):
            self.lane.start(layer_tests.derivation(via_budget=via_budget), out, declaration=declaration)
        self.assertFalse((self.lane.root / out).exists())

    def test_campaign_start_rederives_v_i_and_binds_the_imported_pilot(self):
        self.assertEqual((self.basis['status'], self.basis['signal_vias'], self.basis['via_budget']),
                         ('derived', [3, 4, 4], 5))
        self.assertEqual(self.basis['board_sha256'], self.planed_sha)
        self.assertEqual((self.status, self.state['state']), (0, 'criteria_satisfied'), self.state.get('error'))
        campaign.verify_campaign(self.campaign_root, host=self.lane.host)
        config = read_json(self.campaign_root / 'config.json')
        link = config['layer_policy_derivation']['via_budget']['pilot']
        self.assertEqual(link, {'path': 'via-budget-pilot/via-budget-basis.json', 'sha256': sha(self.basis_path)})
        self.assertEqual(sha(self.campaign_root / link['path']), sha(self.basis_path))
        # The derived bound used the pilot's N; the pilot attempts are not campaign routing receipts.
        record = read_json(self.campaign_root / 'implementations/1/layer-policy/record.json')
        self.assertEqual((record['status'], record['derivation']['via_budget']), ('derived', 5))
        self.assertEqual(record['via_budget'], config['layer_policy_derivation']['via_budget'])
        self.assertEqual(sorted(p.name for p in (self.campaign_root / 'implementations/1/receipts').iterdir()),
                         ['1-add-planes', '2-kicad-route-builtin'])
        self.assertEqual([a['tool'] for a in self.basis['attempts']], [
            {k: self.state['routing_binding']['tool'][k] for k in ('executable', 'mounts', 'source_tree')}] * 3)

    def test_start_refuses_stated_counts_that_differ_from_the_retained_facts(self):
        stated = {**self.via_budget, 'via_budget': 6,
                  'basis': {**self.via_budget['basis'], 'signal_vias': [4, 5, 5]}}
        self.refused_at_start(stated, 'differs from the V_i re-derived', 'refused-counts')
        self.refused_at_start({**self.via_budget, 'board_sha256': '4' * 64}, 'differs from the V_i re-derived',
                              'refused-board')

    def test_start_refuses_tampered_pilot_facts_or_basis(self):
        facts = self.pilot_dir / self.basis['attempts'][1]['candidate_facts']['path']
        with edited(facts, json_edit(lambda v: v['vias'].pop())):
            self.refused_at_start(self.via_budget, 'differs from the re-derivation', 'refused-facts')
        with edited(self.basis_path, json_edit(lambda v: v.update(reason='edited'))):
            self.refused_at_start(self.via_budget, 'basis changed, unsafe or missing', 'refused-basis')
        # A rewritten basis with its new hash declared still has to re-derive from the facts.
        with edited(self.basis_path, json_edit(lambda v: (v.update(signal_vias=[4, 4, 4]),
                                                          v['attempts'][0].update(signal_vias=4)))):
            rehashed = {**self.via_budget, 'pilot': {**self.via_budget['pilot'], 'sha256': digest(self.basis_path)}}
            self.refused_at_start(rehashed, 'differs from the re-derivation', 'refused-rehashed')

    def test_start_refuses_a_pilot_of_another_routing_declaration_or_policy(self):
        self.refused_at_start(self.via_budget, 'forked another routing declaration', 'refused-declaration',
                              declaration={**self.lane.declaration, 'budget': 6})
        with self.assertRaisesRegex(ValueError, 'not the pilot form of the campaign policy'):
            self.lane.start({**layer_tests.derivation(via_budget=self.via_budget),
                             'policy': {'signal_layers': ['F.Cu']}}, 'refused-policy')

    def test_verify_campaign_refuses_a_tampered_imported_pilot(self):
        imported = self.campaign_root / 'via-budget-pilot'
        facts = imported / self.basis['attempts'][0]['reference_facts']['path']
        with edited(facts, json_edit(lambda v: v['vias'].clear())):
            with self.assertRaisesRegex(ValueError, 'differs from the re-derivation'):
                campaign.verify_campaign(self.campaign_root, host=self.lane.host)
        stray = self.campaign_root / 'via-budget-pilot'
        with patch.object(lineage, 'pilot_link', return_value=None):
            with self.assertRaisesRegex(ValueError, 'retained without a declared pilot basis'):
                lineage.verify_pilot(self.campaign_root, read_json(self.campaign_root / 'config.json'),
                                     imported=True)
        self.assertTrue(stray.is_dir())
        campaign.verify_campaign(self.campaign_root, host=self.lane.host)


if __name__ == '__main__':
    unittest.main()
