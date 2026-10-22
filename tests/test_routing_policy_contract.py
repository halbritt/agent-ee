"""The KICADROUTE-91 routing policy contract summary agrees with the adapter (AGENTEE-388).

docs/routing-policy-contract.json is documentation. These checks make a re-pin
or a protocol change fail here until the contract page and summary are updated.
"""
import hashlib
import json
import unittest
from pathlib import Path

from agent_ee import kicad_route_attempt as attempt
from agent_ee import routing_adapter
from tests.toolchain import require_kicad_route

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / 'docs/routing-policy-contract.json'


def load():
    return json.loads(CONTRACT.read_text())


def refusal(reason, changes=None):
    record = {'schema': 'kicad-route/refusal/1', 'kind': 'refusal', 'exit_code': 4, 'command': 'run',
              'reason_class': reason, 'message': 'refused'}
    return json.dumps({**record, **(changes or {})})


class ContractSummaryTests(unittest.TestCase):
    def setUp(self):
        self.contract = load()
        self.adapter = self.contract['adapter']

    def test_pin_and_pinned_code_digest(self):
        self.assertEqual(self.adapter['pin'], attempt.PIN)
        self.assertEqual(self.contract['sources']['kicad_route_pin'], attempt.PIN)
        self.assertEqual(self.adapter['pinned_code_files'], len(attempt.PINNED_CODE))
        digest = hashlib.sha256(attempt.canonical_json(attempt.PINNED_CODE).encode()).hexdigest()
        self.assertEqual(self.adapter['pinned_code_sha256'], digest)
        # campaign.py is pinned although agent-ee never runs the campaign command.
        self.assertEqual(self.adapter['pinned_campaign_py_sha256'], attempt.PINNED_CODE['src/kicad_route/campaign.py'])

    def test_consumed_schema_versions(self):
        consumes = self.adapter['consumes']
        self.assertEqual(attempt.RESULT_SCHEMA, consumes['result'][0])
        self.assertEqual(attempt.REPORT_SCHEMA, 'agent-ee.kicad-route-attempt/1')
        policy = {'project': 'p', 'board': 'b.kicad_pcb', 'name': 'n', 'backend': 'builtin'}
        # Version 2 only with declared pairs; any other version is a contradiction either way.
        for version, paired in ((1, False), (2, True), (2, False), (3, True)):
            with self.subTest(version=version, paired=paired):
                request = {**policy, **({'diff_pairs': 'auto'} if paired else {})}
                result = {'schema': f'kicad-route/route/{version}'}
                outcome, reasons = attempt.semantics(0, result, None, False, True, request)
                self.assertEqual(outcome, 'contradiction')
                expected = (f'kicad-route/route/{version}' in consumes['result'] and
                            (version == 1) != paired)
                self.assertEqual('unknown result schema version' not in reasons, expected)

    def test_consumes_entries_match_adapter_behaviour(self):
        consumes = self.adapter['consumes']
        # version_2_only_with is "<request key> <value>"; it must be the request key the adapter
        # turns into the version-2 expectation and the router flag.
        key, value = consumes['version_2_only_with'].split()
        self.assertEqual((key, value), ('diff_pairs', 'auto'))
        declaration = self.contract['request_example']['declaration']
        self.assertEqual(routing_adapter.validate_declaration({**declaration, key: value})[key], value)
        with self.assertRaises(ValueError):
            routing_adapter.validate_declaration({**declaration, key: 'other'})
        request = {'project': 'p', 'board': 'b.kicad_pcb', 'name': 'n', 'backend': 'builtin', 'budget_s': 10}
        versions, results = {}, {}
        for paired in (False, True):
            asked = {**request, **({key: value} if paired else {})}
            result = dict.fromkeys(attempt.RESULT_KEYS)
            result.update(kind='route', exit_code=0, measurement=False,
                          backend={'name': 'builtin', 'runtime': {'source': '/src', 'venv': '/venv',
                                                                  'generation': None}},
                          input={'board': 'b.kicad_pcb', 'project': 'n'},
                          settings=attempt.expected_settings(asked, None, [] if paired else None),
                          process={'status': 'PASS', 'measurement_qualification': {
                              'measurement': False, 'build_reproducible': None, 'reasons': ['synthetic']}},
                          routing={'status': 'COMPLETE'},
                          validation={'status': 'PASS'}, **({'pairs': []} if paired else {}),
                          # The pin's custody pointer on every builtin process-PASS result.
                          copper_ownership={'status': 'unavailable', 'reasons': ['synthetic'],
                                            'replay_authority': False})
            keys = attempt.ATTEMPT_KEYS | ({'pairs', 'pair_routing'} if paired else set())
            # Result and attempt schema versions vary independently: the attempt check is only reached
            # once the result schema is accepted, so each grid cell is judged on its own reason.
            for result_version in (1, 2, 3):
                for attempt_version in (1, 2, 3):
                    record = dict.fromkeys(keys, None)
                    record['schema'] = f'kicad-route/attempt/{attempt_version}'
                    outcome = attempt.semantics(0, {**result, 'schema': f'kicad-route/route/{result_version}'},
                                                record, False, True, asked)
                    self.assertNotIn('result.json shape differs from the pinned tool', outcome[1])
                    self.assertNotIn('process.measurement_qualification differs from the pinned tool', outcome[1])
                    self.assertNotIn('backend.runtime differs from the pinned tool', outcome[1])
                    if 'unknown result schema version' not in outcome[1]:
                        results.setdefault(paired, set()).add(result_version)
                        if 'unknown attempt schema version' not in outcome[1]:
                            versions.setdefault(paired, set()).add((result_version, attempt_version))
        self.assertEqual(results, {False: {1}, True: {2}})
        self.assertEqual(versions, {False: {(1, 1)}, True: {(2, 2)}})
        self.assertEqual(sorted(f'kicad-route/attempt/{v}' for v in (1, 2)), sorted(consumes['attempt']))
        self.assertEqual(sorted(f'kicad-route/route/{v}' for v in (1, 2)), sorted(consumes['result']))
        # The refusal schema is exactly one version; neighbours are not read as refusals.
        self.assertEqual(consumes['refusal'], ['kicad-route/refusal/1'])
        for version in (0, 1, 2, 3):
            with self.subTest(refusal=version):
                self.assertEqual(
                    bool(attempt.refused_output(refusal('usage', {'schema': f'kicad-route/refusal/{version}'}))),
                    f'kicad-route/refusal/{version}' in consumes['refusal'])

    def test_refusal_reason_classes(self):
        classes = self.adapter['refusal_reason_classes']
        for reason in classes:
            self.assertTrue(attempt.refused_output(refusal(reason)), reason)
        for changed in ({'reason_class': 'experiment'}, {'schema': 'kicad-route/refusal/2'},
                        {'command': 'campaign'}, {'exit_code': 5}):
            with self.subTest(changed=changed):
                self.assertFalse(attempt.refused_output(refusal(classes[0], changed)))

    def test_retained_reports(self):
        self.assertEqual(self.adapter['retained_files'], list(attempt.RETAINED_FILES))
        self.assertEqual(self.adapter['retained_dirs'], list(attempt.RETAINED_DIRS))
        self.assertNotIn('routability-experiment.json', attempt.RETAINED_FILES)

    def test_request_example_translation(self):
        example = self.contract['request_example']
        declaration = routing_adapter.validate_declaration(example['declaration'])
        self.assertNotIn('layer_policy', declaration)
        policy = example['layer_policy_d_pcb_7_v2']
        self.assertIsNone(policy['pour_area_loss_pct'])
        # A null bound is unknown, never a value the adapter accepts.
        with self.assertRaises(attempt.RequestError):
            attempt.validate_layer_policy(policy)
        # The derived bound is the only change; the translation does not depend on it.
        derived = {**policy, 'pour_area_loss_pct': 100}
        attempt.validate_layer_policy(derived)
        self.assertEqual(attempt.policy_backend_args(derived), example['expected_backend_args'])
        routing_adapter.validate_declaration({**example['declaration'], 'layer_policy': derived})
        request = {'backend': 'builtin', 'budget_s': example['declaration']['budget']}
        self.assertEqual(attempt.expected_settings(request, None), example['expected_settings_without_policy'])
        with_policy = attempt.expected_settings({**request, 'layer_policy': derived}, None)
        self.assertEqual(with_policy['backend_args'], ['--layers', 'F.Cu', 'In2.Cu', 'B.Cu'])
        self.assertEqual(with_policy['backend_env'], {})

    def test_documentation_references_the_summary(self):
        page = (ROOT / 'docs/routing-policy-contract.md').read_text()
        self.assertIn('routing-policy-contract.json', page)
        self.assertIn(attempt.PIN, page)
        self.assertIn(self.adapter['pinned_campaign_py_sha256'], page)
        self.assertIn(f'{len(attempt.PINNED_CODE)} files', page)


class PinnedCheckoutContractTests(unittest.TestCase):
    """The configured pinned checkout matches what the summary says about it."""

    @classmethod
    def setUpClass(cls):
        cls.checkout = require_kicad_route()

    def test_campaign_bytes_and_refusal_schema(self):
        self.assertEqual(attempt.digest(self.checkout / 'src/kicad_route/campaign.py'),
                         load()['adapter']['pinned_campaign_py_sha256'])
        schema = json.loads((self.checkout / 'schemas/refusal-1.json').read_text())
        self.assertEqual(schema['properties']['reason_class']['enum'], load()['adapter']['refusal_reason_classes'])
        # The pin's campaign record is a different kind that agent-ee never reads.
        source = (self.checkout / 'src/kicad_route/campaign.py').read_text()
        self.assertIn('contract.schema_id("route-campaign", major=2)', source)
        self.assertIn('kicad-route/route-campaign/2', load()['adapter']['never_consumed'])


if __name__ == '__main__':
    unittest.main()
