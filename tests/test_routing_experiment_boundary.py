"""KICADROUTE-91 routability-experiment/1 evidence at the routing adapter boundary (AGENTEE-442).

Every attempt goes through the consumer path the pipeline uses:
routing_adapter.step() declares it, stage_receipts.run_step() executes it in the
networkless sandbox, and routing_adapter.verify() re-derives it from the
receipt's retained bytes. The router is the coordinator fixture in
kicad_route_fixture.py. ExperimentBoundaryTest's sidecar is SYNTHETIC: it has the
run-relative reference result.experiment {path: 'routability-experiment.json',
sha256, schema} and the key sets of the real producer's sidecar, but only the
values the adapter reads are meaningful. RealReceiptReplayTest replays the REAL
kicad-route 4f06368f receipts in tests/fixtures/k91-real through the same path.
The policy declarations are the reviewed five-field routability-policy/1 form of
kicad-route experiment_policy.load.
"""
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_ee import kicad_route_attempt as attempt
from agent_ee import routing_adapter
from agent_ee import stage_receipts
from tests.kicad_route_fixture import FAKE_PIN, make_candidate, make_checkout
from tests.toolchain import require_toolchain

# HOOK (AGENTEE-442), REAL NATIVE REPLAY. The producer commit whose real receipts
# RealReceiptReplayTest replays; the sha256 of its schemas/routability-experiment-1.json
# (computed from the git object at that commit); and the real native receipt:
# the positive-current sidecar's file digest (its result.json binding) and its
# identity_sha256. Synthetic fixture evidence cannot match these. Nothing here is
# a re-pin or a router release claim: when agent-507 supplies a reviewed or
# composed producer SHA, rerun the replay against that producer's receipts.
K91_REVIEWED = {'producer_commit': '4f06368fbd68bab44cbfde839647aa5b3f7ef7c5',
                'schema_sha256': '81969b52c90f9e0f2c7aa4aa22759cc5a072420eba1910377048d673d629aee5',
                'native_receipt': {
                    'sidecar_sha256': 'cea85ed8b5f274e48446330883c16e897e61edc54bf5a54b30bb351947064bea',
                    'identity_sha256': 'df960e1c4bb87c621cd84d506052bb5a2bcea3c8878ebc0dbb87eb05d095a144'}}
REAL = Path(__file__).resolve().parent / 'fixtures/k91-real'

# Reviewed five-field declarations. The rule-area digest is a placeholder: only
# the router can bind it to the board's native rule areas.
AREAS = hashlib.sha256(b'agent-ee synthetic rule areas').hexdigest()
CURRENT = {'schema': 'routability-policy/1', 'experiment_class': 'current-policy',
           'allowed_trace_layers': ['F.Cu', 'B.Cu'], 'allowed_via_spans': [['F.Cu', 'B.Cu']],
           'rule_areas_sha256': AREAS}
HISTORICAL = {**CURRENT, 'experiment_class': 'historical-all-layer'}
DRIVER = ('import json, sys\nsys.path.insert(0, "/opt/agent-ee-routing")\n'
          'import kicad_route_attempt as a\n'
          'cfg = json.load(open("/opt/driver/layout.json"))\n'
          'layout = a.default_layout(a.parse_request(sys.argv[1:]))\n'
          'layout.update(pin=cfg["pin"], code=cfg["code"])\n'
          'sys.exit(a.main(sys.argv[1:], layout))\n')
REFUSALS = {'tamper': 'tampered', 'missing_file': 'missing', 'omit': 'missing', 'malformed': 'malformed',
            'malformed_reference': 'malformed', 'escape': 'malformed', 'cwd_relative': 'malformed',
            'second_sidecar': 'malformed', 'no_policy_identity': 'malformed', 'unknown_schema': 'unknown_schema',
            'incompatible_schema': 'incompatible_schema', 'incompatible_policy': 'incompatible_policy',
            'policy_copy_mismatch': 'incompatible_policy'}
# Every refusal that has a regular root sidecar keeps its recomputed identity.
KEEPS_SIDECAR = set(REFUSALS) - {'missing_file', 'omit'}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class ExperimentBoundaryTest(unittest.TestCase):
    def setUp(self):
        require_toolchain()
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-route-k91-')
        self.addCleanup(self.tmp.cleanup)
        self.count = 0

    def receipt(self, scenario, experiment_policy=None):
        self.count += 1
        root = Path(self.tmp.name) / str(self.count)
        checkout, code = make_checkout(root / 'checkout', scenario)
        self.pin = {'pin': FAKE_PIN, 'code': code}
        design = make_candidate(root / 'design')
        driver = root / 'driver'
        driver.mkdir()
        (driver / 'layout.json').write_text(json.dumps(self.pin))
        (driver / 'run.py').write_text(DRIVER)
        with patch.object(attempt, 'PIN', FAKE_PIN), patch.object(attempt, 'PINNED_CODE', code):
            spec = routing_adapter.step(project='project', board='rc.kicad_pcb', schematic='rc.kicad_sch',
                                        name='rc', label='step-1', checkout=checkout, budget=5, timeout=30,
                                        experiment_policy=experiment_policy)
        spec['argv'][3] = '/opt/driver/run.py'
        spec['mounts'].append({'source': str(driver), 'at': '/opt/driver'})
        receipt_dir = root / 'receipts/1-kicad-route-krt'
        receipt = stage_receipts.run_step(receipt_dir, design, spec, {'implementation': 1, 'receipt_index': 1})
        self.assertEqual(stage_receipts.validate_receipt(receipt_dir), receipt)
        run = receipt_dir / 'run'
        report = json.loads((run / 'routing/adapter-report.json').read_text())
        return receipt, receipt_dir, design, report

    def verify(self, receipt_dir, receipt):
        return routing_adapter.verify(receipt_dir / 'run', receipt=receipt, layout=self.pin)

    def test_current_policy_evidence_is_retained_in_the_adopted_receipt(self):
        receipt, receipt_dir, design, report = self.receipt({'name': 'complete'}, CURRENT)
        self.assertEqual((receipt['exit_code'], receipt['status'], receipt['violations']), (0, 'completed', []))
        attempt_dir = receipt_dir / 'run/routing/attempt'
        argv = report['process']['argv']
        self.assertEqual(argv[argv.index('--experiment-policy') + 1],
                         '/work/routing/attempt/agent-ee-experiment-policy.json')
        policy_bytes = (attempt_dir / 'agent-ee-experiment-policy.json').read_bytes()
        # The sealed bytes are the canonical five-field declaration.
        self.assertEqual(policy_bytes, json.dumps(CURRENT, sort_keys=True, separators=(',', ':')).encode())
        result = json.loads((attempt_dir / 'result.json').read_text())
        # The binding is run-relative: the canonical root file name, not a cwd-relative path.
        self.assertEqual(result['experiment']['path'], 'routability-experiment.json')
        sidecar = attempt_dir / 'routability-experiment.json'
        copy = attempt_dir / 'experiment-policy.json'
        self.assertIn('SYNTHETIC'.lower(), sidecar.read_text().lower())
        record = report['semantics']['experiment']
        self.assertEqual(record, {
            'status': 'retained', 'qualified': False, 'contract': attempt.EXPERIMENT_CONTRACT,
            'schema': 'routability-experiment/1', 'reference': result['experiment'],
            'path': 'attempt/routability-experiment.json', 'sha256': sha256(sidecar),
            'policy': {'declared': CURRENT, 'sha256': hashlib.sha256(policy_bytes).hexdigest(),
                       'policy_sha256': hashlib.sha256(json.dumps(CURRENT, sort_keys=True).encode()).hexdigest()},
            'policy_file': 'attempt/agent-ee-experiment-policy.json',
            'policy_copy': {'path': 'attempt/experiment-policy.json', 'sha256': sha256(copy)},
            'identity_sha256': json.loads(sidecar.read_text())['identity_sha256'],
            'comparison_eligible': True, 'ineligible_reasons': []})
        self.assertNotEqual(record['identity_sha256'], K91_REVIEWED['native_receipt']['identity_sha256'])
        self.assertEqual(report['retained']['attempt/experiment-policy.json'], sha256(copy))
        self.assertEqual(record['sha256'], result['experiment']['sha256'])
        self.assertEqual(report['retained']['attempt/routability-experiment.json'], sha256(sidecar))
        self.assertEqual(report['retained']['attempt/agent-ee-experiment-policy.json'],
                         hashlib.sha256(policy_bytes).hexdigest())
        # The receipt anchors the report, so the record is bound to the coordinator execution.
        self.assertEqual(receipt['artifacts']['routing/adapter-report.json'],
                         sha256(receipt_dir / 'run/routing/adapter-report.json'))
        # The coherent sidecar is bound to this execution: the same record after the router's output is gone.
        shutil.rmtree(receipt_dir / 'run/routing/out')
        summary = self.verify(receipt_dir, receipt)
        self.assertEqual((summary['classification'], summary['experiment']), ('complete', record))
        self.assertFalse(summary['experiment']['qualified'])
        stage_receipts.adopt(receipt_dir, receipt, design)

    def test_historical_and_current_policy_evidence_keep_distinct_identities(self):
        records = {}
        for policy in (CURRENT, HISTORICAL):
            receipt, receipt_dir, _, report = self.receipt({'name': 'complete'}, policy)
            self.assertEqual(receipt['exit_code'], 0, report['semantics'])
            records[policy['experiment_class']] = self.verify(receipt_dir, receipt)['experiment']
        self.assertEqual({k: v['policy']['declared']['experiment_class'] for k, v in records.items()},
                         {'current-policy': 'current-policy', 'historical-all-layer': 'historical-all-layer'})
        for key in ('sha256', 'policy_sha256'):
            self.assertNotEqual(records['current-policy']['policy'][key],
                                records['historical-all-layer']['policy'][key])

    def test_unknown_malformed_missing_tampered_or_incompatible_evidence_is_refused(self):
        for scenario, reason in REFUSALS.items():
            with self.subTest(scenario):
                receipt, receipt_dir, design, report = self.receipt({'name': 'complete', 'experiment': scenario},
                                                                    CURRENT)
                sem = report['semantics']
                self.assertEqual((receipt['exit_code'], receipt['status']),
                                 (attempt.EXIT_CONTRADICTION, 'failed'), sem)
                self.assertEqual((sem['classification'], sem['proposal']), ('contradiction', 'none'))
                self.assertIsNone(report['candidate'])
                record = sem['experiment']
                self.assertEqual((record['status'], record['reason'], record['qualified']),
                                 ('refused', reason, False))
                self.assertIn(reason, attempt.EXPERIMENT_REFUSALS)
                self.assertTrue(any(r.startswith(f'routability experiment evidence refused ({reason})')
                                    for r in sem['reasons']), sem['reasons'])
                self.assertEqual(record['policy']['declared'], CURRENT)
                if scenario in KEEPS_SIDECAR:
                    # Refused evidence is still retained with its recomputed identity, never dropped.
                    kept = receipt_dir / 'run/routing/attempt/routability-experiment.json'
                    self.assertEqual((record['path'], record['sha256']),
                                     ('attempt/routability-experiment.json', sha256(kept)))
                if scenario == 'tamper':
                    self.assertNotEqual(record['sha256'], record['reference']['sha256'])
                with self.assertRaises(ValueError):
                    stage_receipts.adopt(receipt_dir, receipt, design)
                summary = self.verify(receipt_dir, receipt)
                self.assertEqual((summary['classification'], summary['experiment']), ('contradiction', record))

    def assert_refused(self, scenario, reason, detail, replay=True):
        """The actual consumer refuses the evidence with a typed reason and keeps no candidate.

        replay=False: the receipt's own strict result.json parser already reports
        the backend as unknown, so verify refuses the whole receipt instead."""
        receipt, receipt_dir, design, report = self.receipt(scenario, CURRENT)
        sem = report['semantics']
        self.assertEqual((receipt['exit_code'], receipt['status'], sem['classification'], sem['proposal']),
                         (attempt.EXIT_CONTRADICTION, 'failed', 'contradiction', 'none'), sem)
        self.assertIsNone(report['candidate'])
        record = sem['experiment']
        self.assertEqual((record['status'], record['reason'], record['qualified']), ('refused', reason, False))
        self.assertIn(detail, record['detail'])
        self.assertTrue(any(r.startswith(f'routability experiment evidence refused ({reason})')
                            for r in sem['reasons']), sem['reasons'])
        # The refused bytes are still retained with their recomputed identities.
        attempt_dir = receipt_dir / 'run/routing/attempt'
        self.assertEqual((record['path'], record['sha256']),
                         ('attempt/routability-experiment.json', sha256(attempt_dir / 'routability-experiment.json')))
        self.assertEqual(record['policy_copy'],
                         {'path': 'attempt/experiment-policy.json', 'sha256': sha256(attempt_dir / 'experiment-policy.json')})
        with self.assertRaises(ValueError):
            stage_receipts.adopt(receipt_dir, receipt, design)
        if not replay:
            self.assertEqual(receipt['tool']['backend']['status'], 'unknown')
            with self.assertRaisesRegex(ValueError, 'backend differs from the retained result.json'):
                self.verify(receipt_dir, receipt)
            return receipt_dir
        summary = self.verify(receipt_dir, receipt)
        self.assertEqual((summary['classification'], summary['experiment']), ('contradiction', record))
        # Re-derived from the sealed retained bytes alone, after the router's output is gone.
        shutil.rmtree(receipt_dir / 'run/routing/out')
        self.assertEqual(self.verify(receipt_dir, receipt)['experiment'], record)
        return receipt_dir

    def test_a_duplicate_key_policy_copy_is_refused_not_adopted(self):
        # Review H1 (ee442c): the copy starts with a wrong schema key followed by the
        # whole valid declaration, so a last-key-wins read decodes it as the declaration.
        copy = 'experiment-policy.json'
        receipt_dir = self.assert_refused(
            {'name': 'complete', 'edits': [[copy, '{', '{"schema":"routability-policy/999",', 'first']]},
            'malformed', 'duplicate key "schema"')
        text = (receipt_dir / 'run/routing/attempt' / copy).read_text()
        self.assertTrue(text.startswith('{"schema":"routability-policy/999",'), text)
        with self.assertRaisesRegex(attempt.RequestError, 'duplicate key'):
            attempt.parse_experiment_policy(text)
        wrong = {'schema': '"routability-policy/999"', 'experiment_class': '"historical-all-layer"',
                 'allowed_trace_layers': '["In1.Cu"]', 'allowed_via_spans': '[]',
                 'rule_areas_sha256': json.dumps('0' * 64)}
        self.assertEqual(set(wrong), attempt.EXPERIMENT_POLICY_KEYS)
        for key, value in wrong.items():
            with self.subTest(copy_field=key):
                self.assert_refused(
                    {'name': 'complete', 'edits': [[copy, f'"{key}":', f'"{key}":{value},"{key}":', 'first']]},
                    'malformed', f'duplicate key "{key}"')

    def test_a_malformed_or_noncanonical_policy_copy_is_refused(self):
        copy = 'experiment-policy.json'
        for edit, reason, detail in (
                ([copy, ',', ', ', 'first'], 'malformed', 'not the bytes kicad-route writes'),
                ([copy, '{', '{"extra":1,', 'first'], 'malformed', 'exactly the routability-policy/1 fields'),
                ([copy, '"routability-policy/1"', '"routability-policy/2"', 'first'], 'malformed',
                 'incompatible experiment-policy.json schema'),
                ([copy, '{', '[{', 'first'], 'malformed', 'must be JSON'),
                ([copy, '"F.Cu",\n      "B.Cu"', '"B.Cu",\n      "F.Cu"', 'last'], 'incompatible_policy',
                 'differs from the declared policy')):
            with self.subTest(edit=edit):
                self.assert_refused({'name': 'complete', 'edits': [edit]}, reason, detail)

    def test_duplicate_keys_in_the_sidecar_or_its_binding_are_refused(self):
        sidecar = 'routability-experiment.json'
        cases = [
            ([sidecar, '"schema": ', '"schema": "routability-experiment/9", "schema": ', 'first'],
             'duplicate key "schema"'),
            ([sidecar, '"identity": ', '"identity": {}, "identity": ', 'first'], 'duplicate key "identity"'),
            ([sidecar, '"policy": ', '"policy": null, "policy": ', 'first'], 'duplicate key "policy"'),
            ([sidecar, '"declared": ', '"declared": null, "declared": ', 'first'], 'duplicate key "declared"'),
            ([sidecar, '"declared_sha256": ', '"declared_sha256": "0", "declared_sha256": ', 'first'],
             'duplicate key "declared_sha256"'),
            ([sidecar, '"experiment_class": ', '"experiment_class": "x", "experiment_class": ', 'last'],
             'duplicate key "experiment_class"')]
        # Every field of the declaration the sidecar's identity claims.
        # (The declared schema is the sidecar's last "schema" key.)
        cases += [([sidecar, f'"{key}": ', f'"{key}": null, "{key}": ', 'last' if key == 'schema' else 'first'],
                   f'duplicate key "{key}"') for key in sorted(attempt.EXPERIMENT_POLICY_KEYS - {'experiment_class'})]
        cases.append(([sidecar, '"experiment_class": ', '"experiment_class": "x", "experiment_class": ', 'first'],
                      'duplicate key "experiment_class"'))
        # The result.json binding: a repeated experiment key, or a repeated key in the reference.
        cases += [(['result.json', '"experiment": ', '"experiment": null, "experiment": ', 'last'],
                   'duplicate key "experiment"')]
        cases += [(['result.json', f'"{key}": ', f'"{key}": "x", "{key}": ', 'last'],
                   'experiment reference has a duplicate key') for key in sorted(attempt.EXPERIMENT_REFERENCE_KEYS)]
        for edit, detail in cases:
            with self.subTest(edit=edit):
                self.assert_refused({'name': 'complete', 'edits': [edit]}, 'malformed', detail,
                                    replay=edit[0] != 'result.json')

    def test_a_router_that_rewrites_the_policy_file_is_a_contradiction(self):
        receipt, receipt_dir, _, report = self.receipt({'name': 'complete', 'experiment': 'rewrite_policy'},
                                                       CURRENT)
        sem = report['semantics']
        self.assertEqual((receipt['exit_code'], sem['classification']),
                         (attempt.EXIT_CONTRADICTION, 'contradiction'))
        self.assertIn('experiment policy file changed during the attempt', sem['reasons'])
        self.assertEqual(self.verify(receipt_dir, receipt)['classification'], 'contradiction')

    def test_incompatible_policy_versions_are_refused_before_the_router_runs(self):
        def without(key):
            return {k: v for k, v in CURRENT.items() if k != key}
        bad = (
            # The superseded two-field draft is never accepted.
            {'schema': 'routability-policy/1', 'policy': 'current-policy'},
            {'schema': 'routability-policy/1', 'experiment_class': 'current-policy'},
            {**CURRENT, 'schema': 'routability-policy/2'}, {**CURRENT, 'experiment_class': 'all-layer'},
            {**CURRENT, 'policy': 'current-policy'}, without('rule_areas_sha256'), without('allowed_via_spans'),
            {**CURRENT, 'allowed_trace_layers': []}, {**CURRENT, 'allowed_trace_layers': ['F.Cu', 'F.Cu']},
            {**CURRENT, 'allowed_trace_layers': ['F.Cu', '']}, {**CURRENT, 'allowed_trace_layers': 'F.Cu'},
            {**CURRENT, 'allowed_via_spans': [['F.Cu']]}, {**CURRENT, 'allowed_via_spans': [['F.Cu', 'F.Cu']]},
            {**CURRENT, 'allowed_via_spans': [['F.Cu', 'B.Cu'], ['F.Cu', 'B.Cu']]},
            {**CURRENT, 'rule_areas_sha256': AREAS.upper()}, {**CURRENT, 'rule_areas_sha256': AREAS[:63]},
            {**CURRENT, 'rule_areas_sha256': None}, ['current-policy'])
        declaration = {'schema': 'agent-ee.routing-adapter/1', 'project': 'project', 'board': 'rc.kicad_pcb',
                       'schematic': 'rc.kicad_sch', 'name': 'rc', 'label': 'step-1', 'checkout': '/opt/kr'}
        self.assertEqual(routing_adapter.validate_declaration(
            {**declaration, 'experiment_policy': CURRENT})['experiment_policy'], CURRENT)
        for policy in bad:
            with self.subTest(policy=policy):
                with self.assertRaises(ValueError):
                    routing_adapter.validate_declaration({**declaration, 'experiment_policy': policy})
                with self.assertRaises(attempt.RequestError):
                    attempt.parse_request(['--project', 'p', '--board', 'b.kicad_pcb', '--schematic', 's.kicad_sch',
                                           '--name', 'n', '--label', 'l', '--budget', '5', '--timeout', '30',
                                           '--experiment-policy', json.dumps(policy)])
        with self.assertRaisesRegex(ValueError, 'exactly the routability-policy/1 fields'):
            routing_adapter.validate_declaration({**declaration, 'experiment_policy': bad[0]})
        with self.assertRaisesRegex(ValueError, 'incompatible experiment policy schema'):
            routing_adapter.validate_declaration({**declaration, 'experiment_policy': bad[2]})
        # An empty via-span list is the reviewed way to prohibit all vias.
        no_vias = {**CURRENT, 'allowed_via_spans': []}
        self.assertEqual(routing_adapter.validate_declaration(
            {**declaration, 'experiment_policy': no_vias})['experiment_policy'], no_vias)
        argv = ['--project', 'p', '--board', 'b.kicad_pcb', '--schematic', 's.kicad_sch', '--name', 'n',
                '--label', 'l', '--budget', '5', '--timeout', '30', '--experiment-policy']
        canonical = attempt.canonical_json(CURRENT)
        for text, message in ((canonical[:-1] + ',"schema":"routability-policy/1"}', 'duplicate key'),
                              (json.dumps(CURRENT), 'canonical JSON')):
            with self.subTest(text=text):
                with self.assertRaisesRegex(attempt.RequestError, message):
                    attempt.parse_request(argv + [text])

    def test_undeclared_policy_retains_or_refuses_router_evidence_and_is_otherwise_unchanged(self):
        receipt, receipt_dir, _, report = self.receipt({'name': 'complete'})
        self.assertNotIn('experiment', report['semantics'])
        self.assertNotIn(f'attempt/{attempt.EXPERIMENT_SIDECAR}', report['retained'])
        self.assertNotIn('--experiment-policy', report['process']['argv'])
        self.assertNotIn('experiment', self.verify(receipt_dir, receipt))
        receipt, receipt_dir, _, report = self.receipt({'name': 'complete', 'experiment': 'ok'})
        record = report['semantics']['experiment']
        self.assertEqual((receipt['exit_code'], record['status'], record['policy'], record['policy_file'],
                          record['policy_copy'], record['qualified']),
                         (0, 'retained', None, None, None, False))
        self.assertEqual(self.verify(receipt_dir, receipt)['experiment'], record)
        # A sidecar that claims a policy the coordinator never declared is incompatible.
        receipt, receipt_dir, _, report = self.receipt({'name': 'complete', 'experiment': 'incompatible_policy'})
        self.assertEqual((receipt['exit_code'], report['semantics']['experiment']['reason']),
                         (attempt.EXIT_CONTRADICTION, 'incompatible_policy'))

    def test_an_unreferenced_root_sidecar_is_retained_and_refused_as_unbound(self):
        for scenario, policy in (('orphan', None), ('orphan', CURRENT)):
            with self.subTest(scenario=scenario, policy=policy):
                receipt, receipt_dir, design, report = self.receipt({'name': 'complete', 'experiment': scenario},
                                                                    policy)
                sem = report['semantics']
                self.assertEqual((receipt['exit_code'], sem['classification'], sem['proposal']),
                                 (attempt.EXIT_CONTRADICTION, 'contradiction', 'none'), sem)
                self.assertIsNone(report['candidate'])
                record = sem['experiment']
                self.assertEqual((record['status'], record['reason'], record['qualified']),
                                 ('refused', 'unbound', False))
                self.assertTrue(any(r.startswith('routability experiment evidence refused (unbound)')
                                    for r in sem['reasons']), sem['reasons'])
                originals = list((receipt_dir / 'run/routing/out').rglob(attempt.EXPERIMENT_SIDECAR))
                self.assertEqual(len(originals), 1)
                kept = receipt_dir / 'run/routing/attempt' / attempt.EXPERIMENT_SIDECAR
                # The observed artifact identity is preserved, bound to the receipt-anchored report.
                self.assertEqual((record['path'], record['sha256']),
                                 (f'attempt/{attempt.EXPERIMENT_SIDECAR}', sha256(originals[0])))
                self.assertEqual(sha256(kept), sha256(originals[0]))
                self.assertEqual(report['retained'][f'attempt/{attempt.EXPERIMENT_SIDECAR}'], sha256(kept))
                with self.assertRaises(ValueError):
                    stage_receipts.adopt(receipt_dir, receipt, design)
                # The decision is re-derived from the sealed retained bytes alone.
                shutil.rmtree(receipt_dir / 'run/routing/out')
                summary = self.verify(receipt_dir, receipt)
                self.assertEqual((summary['classification'], summary['experiment']), ('contradiction', record))

    def test_an_unreferenced_policy_copy_is_retained_and_refused_as_unbound(self):
        receipt, receipt_dir, design, report = self.receipt({'name': 'complete', 'experiment': 'orphan_copy'},
                                                            CURRENT)
        sem = report['semantics']
        self.assertEqual((receipt['exit_code'], sem['classification']), (attempt.EXIT_CONTRADICTION, 'contradiction'))
        record = sem['experiment']
        copy = receipt_dir / 'run/routing/attempt/experiment-policy.json'
        self.assertEqual((record['status'], record['reason'], record['path'], record['sha256']),
                         ('refused', 'unbound', 'attempt/experiment-policy.json', sha256(copy)))
        self.assertEqual(record['policy_copy'], {'path': 'attempt/experiment-policy.json', 'sha256': sha256(copy)})
        with self.assertRaises(ValueError):
            stage_receipts.adopt(receipt_dir, receipt, design)
        self.assertEqual(self.verify(receipt_dir, receipt)['experiment'], record)

    def test_an_unreferenced_root_sidecar_link_is_a_contradiction(self):
        receipt, receipt_dir, design, report = self.receipt({'name': 'complete', 'experiment': 'orphan_link'})
        sem = report['semantics']
        self.assertEqual((receipt['exit_code'], sem['classification']), (attempt.EXIT_CONTRADICTION, 'contradiction'))
        self.assertIn(f'link or special file in attempt diagnostics: {attempt.EXPERIMENT_SIDECAR}', sem['reasons'])
        self.assertIsNone(report['candidate'])
        with self.assertRaises(ValueError):
            stage_receipts.adopt(receipt_dir, receipt, design)
        self.assertEqual(self.verify(receipt_dir, receipt)['classification'], 'contradiction')

    def test_a_validation_failure_with_refused_evidence_replays_its_typed_reason(self):
        for scenario, reason in (('missing_file', 'missing'), ('tamper', 'tampered'), ('orphan', 'unbound')):
            with self.subTest(scenario):
                receipt, receipt_dir, design, report = self.receipt(
                    {'name': 'validation_failed', 'experiment': scenario}, CURRENT)
                sem = report['semantics']
                self.assertEqual((receipt['exit_code'], sem['classification'], sem['experiment']['reason']),
                                 (attempt.EXIT_CONTRADICTION, 'contradiction', reason))
                # A contradiction rejects no candidate, so the replay invariant holds.
                self.assertIsNone(report['rejected_candidate_sha256'])
                self.assertIsNone(report['candidate'])
                with self.assertRaises(ValueError):
                    stage_receipts.adopt(receipt_dir, receipt, design)
                summary = self.verify(receipt_dir, receipt)
                self.assertEqual((summary['classification'], summary['experiment']),
                                 ('contradiction', sem['experiment']))

    def test_a_non_eligible_attempt_keeps_its_class_and_its_evidence_record(self):
        receipt, receipt_dir, _, report = self.receipt({'name': 'validation_failed'}, CURRENT)
        self.assertEqual((receipt['exit_code'], report['semantics']['classification']), (3, 'validation_failed'))
        self.assertEqual(report['semantics']['experiment']['status'], 'retained')
        receipt, receipt_dir, _, report = self.receipt({'name': 'refused'}, CURRENT)
        self.assertEqual(receipt['exit_code'], 4)
        record = report['semantics']['experiment']
        self.assertEqual((record['status'], record['qualified']), ('absent', False))
        self.assertEqual(self.verify(receipt_dir, receipt)['experiment'], record)

    def test_forged_experiment_records_are_refused_on_replay(self):
        receipt, receipt_dir, _, _ = self.receipt({'name': 'complete'}, CURRENT)
        routing = receipt_dir / 'run/routing'
        honest = (routing / 'adapter-report.json').read_bytes()

        def forge(change_files, change_report):
            (routing / 'adapter-report.json').write_bytes(honest)
            report = json.loads(honest)
            change_files(report)
            change_report(report)
            attempt.write_report(routing / 'adapter-report.json', report)

        sidecar = routing / 'attempt/routability-experiment.json'
        original = sidecar.read_bytes()

        def edit_sidecar(report):
            body = json.loads(original)
            body['identity']['policy']['declared'] = HISTORICAL
            sidecar.write_text(json.dumps(body))
            report['retained']['attempt/routability-experiment.json'] = attempt.digest(sidecar)

        cases = (('edited sidecar', edit_sidecar, lambda r: None),
                 ('qualified claim', lambda r: None, lambda r: r['semantics']['experiment'].update(qualified=True)),
                 ('dropped record', lambda r: None, lambda r: r['semantics'].pop('experiment')))
        message = 'experiment evidence record differs'
        for label, files, change in cases:
            with self.subTest(label):
                forge(files, change)
                # The receipt anchors the honest report bytes.
                with self.assertRaisesRegex(ValueError, 'does not anchor'):
                    self.verify(receipt_dir, receipt)
                # Without the receipt, re-derivation still refuses the self-consistent forgery.
                with self.assertRaisesRegex(ValueError, message):
                    routing_adapter.verify(receipt_dir / 'run', layout=self.pin)
                sidecar.write_bytes(original)
        (routing / 'adapter-report.json').write_bytes(honest)
        self.assertEqual(self.verify(receipt_dir, receipt)['experiment']['status'], 'retained')
        policy = routing / 'attempt/agent-ee-experiment-policy.json'
        policy.write_text(attempt.canonical_json(HISTORICAL))
        report = json.loads(honest)
        report['retained']['attempt/agent-ee-experiment-policy.json'] = attempt.digest(policy)
        attempt.write_report(routing / 'adapter-report.json', report)
        with self.assertRaisesRegex(ValueError, 'experiment policy file differs'):
            routing_adapter.verify(receipt_dir / 'run', layout=self.pin)

    def test_the_synthetic_sidecar_is_schema_valid_and_requalified_like_a_real_one(self):
        receipt, receipt_dir, _, report = self.receipt({'name': 'complete'}, CURRENT)
        body = json.loads((receipt_dir / 'run/routing/attempt/routability-experiment.json').read_text())
        self.assertIsNone(attempt._shape_error(body, attempt.EXPERIMENT_SHAPE))
        self.assertEqual(report['semantics']['experiment']['status'], 'retained')
        for scenario, detail in (
                ({'manifest': {'execution': {'outer_batches': 1, 'later_subruns': 2}}}, 'execution is not one outer'),
                ({'manifest': {'request_sha256': '0' * 64}}, 'request_sha256 is not the retained'),
                ({'self_cycle': True}, 'origin is a self-cycle'),
                ({'identity': {'origin': {'root_input_fingerprint': None, 'depth': 0, 'predecessors': []}}},
                 'root_input_fingerprint is not the sidecar input fingerprint')):
            with self.subTest(scenario=scenario):
                self.assert_refused({'name': 'complete', **scenario}, 'unqualified', detail)

    def test_every_artifact_the_sidecar_names_is_bound_by_digest(self):
        # Review H1 (ee442f): the attempt record and facts the boundary reads must be the
        # files the sidecar names, recomputed from the retained bytes; never the adapter's own.
        wrong = '0' * 64
        cases = []
        for name in ('attempt.json', 'facts/reference-facts.json', 'validate/candidate-facts.json',
                     *attempt.EXPERIMENT_MANIFESTS):
            cases += [([[name, wrong]], 'tampered', f'{name} sha256 differs from the sidecar artifacts digest'),
                      ([[name, None]], 'unbound', f'the eligible sidecar binds no {name} digest'),
                      ([[name, 'delete']], 'unbound', f'the eligible sidecar binds no {name} digest')]
        self.assertEqual([ops[0][0] for ops, *_ in cases[::3]], list(attempt.EXPERIMENT_BOUND))
        cases += [
            # Moved under another name: the named file is not retained, or is not a retained report.
            ([['attempt.json', 'rename:validate/attempt.json']], 'missing',
             'binds validate/attempt.json but no regular file was retained'),
            ([['attempt.json', 'rename:attempt-of-another-run.json']], 'malformed',
             "sidecar artifact 'attempt-of-another-run.json' is not a run-relative retained report"),
            ([['attempt.json', 'rename:../attempt.json']], 'malformed', "'../attempt.json' is not a run-relative"),
            ([['attempt.json', 'rename:facts//attempt.json']], 'malformed', 'is not a run-relative'),
            # Any other named report is bound too, not only the ones the checks read.
            ([['validate/preservation.json', wrong]], 'tampered', 'validate/preservation.json sha256 differs'),
            ([['validate/never-written.json', wrong]], 'missing', 'binds validate/never-written.json but no regular')]
        for ops, reason, detail in cases:
            with self.subTest(ops=ops):
                self.assert_refused({'name': 'complete', 'artifacts': ops}, reason, detail)

    def test_a_same_policy_cross_run_transplant_is_refused(self):
        # agent-509 ruling (ee442f): a coherent receipt of another run under the same policy,
        # transplanted with every file it seals, is not this execution's evidence.
        receipt, receipt_dir, _, report = self.receipt({'name': 'complete'}, CURRENT)
        self.assertEqual(report['semantics']['experiment']['status'], 'retained')
        donor = receipt_dir / 'run/routing/attempt'
        result = json.loads((donor / 'result.json').read_text())
        sealed = [n for n, v in json.loads((donor / attempt.EXPERIMENT_SIDECAR).read_text())['artifacts'].items()
                  if v is not None and n != attempt.EXPERIMENT_POLICY_COPY]
        self.assertEqual(set(sealed), set(attempt.EXPERIMENT_BOUND))
        replay = {'sidecar': (donor / attempt.EXPERIMENT_SIDECAR).read_text(),
                  'copy': (donor / attempt.EXPERIMENT_POLICY_COPY).read_text()}
        whole = {'payload': {n: (donor / n).read_text() for n in sealed},
                 'effective_binding': result['process']['effective_configuration']}
        for label, scenario, reason, detail in (
                # Same design, same policy, every sealed file: only the run differs.
                ('with every sealed file', whole, 'run_mismatch', 'the sidecar requested output'),
                # The sidecar alone: this run's own manifest is not the one it seals.
                ('sidecar only', {}, 'tampered', 'work/effective-configuration.json sha256 differs')):
            with self.subTest(label):
                self.assert_refused({'name': 'complete', 'replay': replay, **scenario}, reason, detail)

    def test_an_aliased_self_origin_is_a_self_cycle_and_other_sources_are_not_this_run(self):
        # Review H2 (ee442f): the artifact digests are all correct; the source_dir is a
        # producer-namespace string, normalised lexically and never followed on this host.
        cycle = "origin is a self-cycle: attempt.json source_dir is this run's own candidate"
        for scenario, reason, detail in (
                ({'source_dir': '{run}/hop/../candidate/project'}, 'unqualified', cycle),
                ({'source_dir': '{cand}/'}, 'unqualified', cycle),
                ({'source_dir': '{run}//candidate//project'}, 'unqualified', cycle),
                ({'source_dir': '{run}/./candidate/./project/.'}, 'unqualified', cycle),
                ({'source_dir': '/{run}/candidate/project'}, 'unqualified', cycle),
                ({'source_dir': '{run}/../{run.name}/candidate/project'}, 'unqualified', cycle),
                # A link inside the run reaching its candidate, and a case variant inside the run.
                ({'links': {'{run}/alias': '{cand}'}, 'source_dir': '{run}/alias'}, 'unqualified', cycle),
                ({'source_dir': '{run}/Candidate/project'}, 'unqualified', cycle),
                ({'source_dir': '{run}'}, 'unqualified', cycle),
                # Outside the run, anything but the exact input directory is another execution's source.
                ({'links': {'{out}/alias': '{cand}'}, 'source_dir': '{out}/alias'}, 'run_mismatch',
                 "source_dir '/work/routing/out/alias' is not this attempt's input /candidate/project"),
                ({'source_dir': '{src}/'}, 'run_mismatch', 'is not this attempt\'s input'),
                ({'source_dir': '/candidate/./project'}, 'run_mismatch', 'is not this attempt\'s input'),
                ({'source_dir': '/CANDIDATE/project'}, 'run_mismatch', 'is not this attempt\'s input'),
                ({'source_dir': 'candidate/project'}, 'run_mismatch', 'is not this attempt\'s input'),
                ({'source_dir': '/elsewhere/project'}, 'run_mismatch', 'is not this attempt\'s input')):
            with self.subTest(scenario=scenario):
                receipt_dir = self.assert_refused({'name': 'complete', **scenario}, reason, detail)
                kept = json.loads((receipt_dir / 'run/routing/attempt/attempt.json').read_text())
                body = json.loads((receipt_dir / 'run/routing/attempt' / attempt.EXPERIMENT_SIDECAR).read_text())
                # The sidecar names exactly these bytes: the refusal is the origin, not the binding.
                self.assertEqual(body['artifacts']['attempt.json'],
                                 sha256(receipt_dir / 'run/routing/attempt/attempt.json'))
                self.assertEqual(body['identity']['input']['source'], kept['source'])

    def test_the_synthetic_sidecar_must_describe_this_execution(self):
        # Each identity field resealed self-consistently, with every digest correct.
        for scenario, detail in (
                ({'identity': {'request': {'protect': 'locked'}}}, 'request protect'),
                ({'identity': {'budgets': {'backend_s': 6, 'stall_s': None, 'command_budget_s': None,
                                           'grace_s': None}}}, 'backend budget'),
                ({'identity': {'protection': {'mode': 'locked', 'only_sha256': None}}}, 'protection mode'),
                ({'manifest': {'requested_output': '/work/routing/out/runs/rc/step-1/other/work/krt-out/rc.kicad_pcb'}},
                 'requested output')):
            with self.subTest(detail=detail):
                self.assert_refused({'name': 'complete', **scenario}, 'run_mismatch', f'the sidecar {detail}')

    def test_the_contract_names_the_replayed_producer_and_claims_no_qualification(self):
        self.assertIn(K91_REVIEWED['producer_commit'], attempt.EXPERIMENT_CONTRACT)
        self.assertIn('unpinned, not qualified', attempt.EXPERIMENT_CONTRACT)
        self.assertNotEqual(K91_REVIEWED['producer_commit'], attempt.PIN)


    def test_json_number_and_nesting_limits_are_typed_refusals_not_crashes(self):
        # Review H1 (ee442d): an integer beyond the interpreter's digit limit raised a
        # bare ValueError that escaped the typed refusal (exit 70, no report).
        sidecar, copy = 'routability-experiment.json', 'experiment-policy.json'
        value = json.dumps(AREAS)
        deep = '[' * 5000 + ']' * 5000
        cases = []
        for file in (copy, sidecar):
            cases += [(file, '1' * 5000, 'out-of-range number'), (file, '-' + '9' * 4301, 'out-of-range number'),
                      (file, 'NaN', 'non-finite number NaN'), (file, 'Infinity', 'non-finite number Infinity'),
                      (file, '-Infinity', 'non-finite number -Infinity'), (file, '1e999', 'non-finite number 1e999'),
                      (file, '-1e400', 'non-finite number -1e400'), (file, deep, 'nesting exceeds'),
                      (file, '[' * 70 + ']' * 70, f'nesting exceeds {attempt.EXPERIMENT_JSON_DEPTH}')]
        # Wrong numeric types are value refusals, not parse refusals.
        cases += [(copy, '5', 'rule_areas_sha256 must be 64 lowercase hex digits'),
                  (copy, '5.0', 'rule_areas_sha256 must be 64 lowercase hex digits')]
        for file, new, detail in cases:
            with self.subTest(file=file, value=new[:12], length=len(new)):
                self.assert_refused({'name': 'complete', 'edits': [[file, value, new, 'first']]}, 'malformed', detail)
        for edit, reason, detail in (
                # Re-bound by result.json, but the sealed identity digest no longer matches.
                ([sidecar, value, '5', 'first'], 'tampered', 'identity_sha256 differs'),
                ([sidecar, '"comparison_eligible": true', '"comparison_eligible": 1', 'first'], 'malformed',
                 'comparison_eligible must be a boolean'),
                ([sidecar, '"replay_authority": false', '"replay_authority": 0', 'first'], 'malformed',
                 'replay_authority must be false')):
            with self.subTest(edit=edit):
                self.assert_refused({'name': 'complete', 'edits': [edit]}, reason, detail)

    def test_an_out_of_range_number_in_the_result_json_binding_is_refused(self):
        # An integer past the digit limit makes result.json unreadable, so the retained
        # sidecar is unbound; an overflowing float decodes, but the reference is malformed.
        for new, reason in (('1' * 5000, 'unbound'), ('1e999', 'malformed')):
            with self.subTest(length=len(new)):
                receipt, receipt_dir, design, report = self.receipt(
                    {'name': 'complete', 'edits': [['result.json', '"schema": "routability-experiment/1"',
                                                    f'"schema": "routability-experiment/1", "n": {new}', 'last']]},
                    CURRENT)
                sem = report['semantics']
                self.assertEqual((receipt['exit_code'], receipt['status'], sem['classification']),
                                 (attempt.EXIT_CONTRADICTION, 'failed', 'contradiction'), sem)
                self.assertIsNone(report['candidate'])
                self.assertEqual((sem['experiment']['status'], sem['experiment']['reason']), ('refused', reason))
                self.assertIsNone(sem['experiment']['reference'])
                with self.assertRaises(ValueError):
                    stage_receipts.adopt(receipt_dir, receipt, design)


def real(name):
    return (REAL / name).read_text()


class RealReceiptReplayTest(unittest.TestCase):
    """The REAL kicad-route 4f06368f receipts through step, run_step, verify and adopt.

    The fixture router writes the real sidecar and policy-copy bytes verbatim and
    binds them as the real run's result.json did; agent-ee declares the real
    five-field policy. The router routes the fixture design, so these are
    PRODUCER-FORMAT fixtures attached to another run: the real sidecar describes
    the route_demo run, not this execution, and every eligible one is refused
    (agent-509 ruling on ee442f: the old hybrid positive must refuse). The
    end-to-end positive is the coherent synthetic receipt until the corrected
    producer supplies a fresh native one. Each EI case is also run coherently on
    the synthetic receipt, where its own check is what refuses.
    """
    setUp, receipt, verify, assert_refused = (ExperimentBoundaryTest.setUp, ExperimentBoundaryTest.receipt,
                                              ExperimentBoundaryTest.verify, ExperimentBoundaryTest.assert_refused)
    HYBRID = ('tampered', 'attempt.json sha256 differs from the sidecar artifacts digest')
    MANIFEST = json.loads((REAL / 'receipts.json').read_text())
    POLICY = {'current-policy': json.loads(real('current-policy.experiment-policy.json')),
              'historical-all-layer': json.loads(real('historical-all-layer.experiment-policy.json'))}

    def replay(self, run, policy, copy='default', sidecar=None, **scenario):
        """The real run's sidecar, policy copy and (for the positives) its sealed manifest,
        request and result.json binding of them; scenario keys override or extend."""
        spec = self.MANIFEST['runs'][run]
        sealed = {}
        if 'effective_configuration' in spec:
            sealed = {'payload': {attempt.EXPERIMENT_MANIFESTS[0]: real(spec['effective_configuration']),
                                  attempt.EXPERIMENT_MANIFESTS[1]: real(spec['effective_configuration_request'])},
                      'effective_binding': spec['result_effective_configuration']}
        payload = {**sealed.get('payload', {}), **scenario.pop('payload', {})}
        return self.receipt({'name': 'complete', **sealed, 'payload': payload, 'replay': {
            'sidecar': real(spec['sidecar']) if sidecar is None else sidecar,
            'copy': real(spec['policy_copy']) if copy == 'default' else copy}, **scenario},
            self.POLICY[policy])

    def refused(self, outcome, reason, detail):
        receipt, receipt_dir, design, report = outcome
        sem = report['semantics']
        self.assertEqual((receipt['exit_code'], receipt['status'], sem['classification'], sem['proposal']),
                         (attempt.EXIT_CONTRADICTION, 'failed', 'contradiction', 'none'), sem)
        self.assertIsNone(report['candidate'])
        record = sem['experiment']
        self.assertEqual((record['status'], record['reason'], record['qualified']), ('refused', reason, False))
        self.assertIn(detail, record['detail'])
        kept = receipt_dir / 'run/routing/attempt' / attempt.EXPERIMENT_SIDECAR
        self.assertEqual((record['path'], record['sha256']), ('attempt/routability-experiment.json', sha256(kept)))
        with self.assertRaises(ValueError):
            stage_receipts.adopt(receipt_dir, receipt, design)
        summary = self.verify(receipt_dir, receipt)
        self.assertEqual((summary['classification'], summary['experiment']), ('contradiction', record))
        shutil.rmtree(receipt_dir / 'run/routing/out')
        self.assertEqual(self.verify(receipt_dir, receipt)['experiment'], record)
        return record

    def test_the_fixture_bytes_are_the_verified_real_receipts(self):
        sums = dict(reversed(line.split('  ')) for line in real('SHA256SUMS').splitlines())
        for name, digest in sums.items():
            self.assertEqual(sha256(REAL / name), digest, name)
        self.assertEqual(self.MANIFEST['producer']['commit'], K91_REVIEWED['producer_commit'])
        self.assertEqual(self.MANIFEST['producer']['schema_sha256'], K91_REVIEWED['schema_sha256'])
        positive = self.MANIFEST['runs']['positive-current']
        self.assertEqual(positive['result_experiment']['sha256'], K91_REVIEWED['native_receipt']['sidecar_sha256'])
        body = json.loads(real(positive['sidecar']))
        self.assertEqual(body['identity_sha256'], K91_REVIEWED['native_receipt']['identity_sha256'])
        self.assertEqual(body['identity']['build']['wrapper']['commit'], K91_REVIEWED['producer_commit'])
        for run in self.MANIFEST['runs'].values():
            self.assertEqual(run['result_experiment']['sha256'], sha256(REAL / run['sidecar']))

    def test_the_real_positive_receipts_on_another_run_are_refused_not_adopted(self):
        # Formerly retained and adopted unqualified: a real sidecar of another run attached to
        # this run's synthetic attempt record, facts and candidate is not this execution's.
        for run, policy in (('positive-current', 'current-policy'), ('positive-historical', 'historical-all-layer')):
            with self.subTest(run):
                spec = self.MANIFEST['runs'][run]
                outcome = self.replay(run, policy)
                result = json.loads((outcome[1] / 'run/routing/attempt/result.json').read_text())
                # The fixture binds the real bytes exactly as the real run's result.json did.
                self.assertEqual(result['experiment'], spec['result_experiment'])
                record = self.refused(outcome, *self.HYBRID)
                body = json.loads(real(spec['sidecar']))
                self.assertEqual((record['sha256'], record['identity_sha256'], record['comparison_eligible']),
                                 (spec['result_experiment']['sha256'], body['identity_sha256'], True))
                self.assertEqual(record['policy_copy']['sha256'], body['artifacts']['experiment-policy.json'])
                if run == 'positive-current':
                    self.assertEqual((record['sha256'], record['identity_sha256']),
                                     tuple(K91_REVIEWED['native_receipt'].values()))
                self.assertNotEqual(sha256(outcome[1] / 'run/routing/attempt/attempt.json'),
                                    body['artifacts']['attempt.json'])

    def test_a_self_consistent_non_identity_sidecar_forgery_is_refused(self):
        # Coordinator ruling (ee442g): the real sidecar with its digests rewritten to this run's
        # attempt record, facts and policy copy, its own sealed manifests, and resealed. Every
        # digest and the identity seal verify; the run it describes is not this one.
        for forge, detail in (({'forge': True}, 'the sidecar input fingerprint'),
                              ({'forge': True, 'input': True}, 'the sidecar request protect')):
            with self.subTest(forge=forge):
                spec = self.MANIFEST['runs']['positive-current']
                sidecar = real(spec['sidecar'])
                outcome = self.receipt({'name': 'complete', 'payload': {
                    attempt.EXPERIMENT_MANIFESTS[0]: real(spec['effective_configuration']),
                    attempt.EXPERIMENT_MANIFESTS[1]: real(spec['effective_configuration_request'])},
                    'effective_binding': spec['result_effective_configuration'],
                    'replay': {'sidecar': sidecar, 'copy': real(spec['policy_copy']), **forge}},
                    self.POLICY['current-policy'])
                attempt_dir = outcome[1] / 'run/routing/attempt'
                body = json.loads((attempt_dir / attempt.EXPERIMENT_SIDECAR).read_text())
                self.assertEqual(body['identity_sha256'], attempt._sha256_of(body['identity']))
                for name in attempt.EXPERIMENT_BOUND:
                    self.assertEqual(body['artifacts'][name], sha256(attempt_dir / name), name)
                self.assertIsNone(attempt._shape_error(body, attempt.EXPERIMENT_SHAPE))
                self.refused(outcome, 'run_mismatch', detail)

    def test_the_real_stale_runtime_receipt_is_refused_as_ineligible(self):
        record = self.refused(self.replay('negative-stale-runtime', 'current-policy'), 'ineligible',
                              'routing_environment binding mismatch')
        body = json.loads(real('negative-stale-runtime.routability-experiment.json'))
        self.assertEqual((record['comparison_eligible'], record['ineligible_reasons'], record['identity_sha256']),
                         (False, body['ineligible_reasons'], body['identity_sha256']))

    def test_tampered_real_receipts_are_refused(self):
        sidecar, copy = attempt.EXPERIMENT_SIDECAR, attempt.EXPERIMENT_POLICY_COPY
        commit = K91_REVIEWED['producer_commit']
        current = self.POLICY['current-policy']
        sorted_copy = json.dumps(current, indent=2, sort_keys=True) + '\n'
        self.assertNotEqual(sorted_copy, real('current-policy.experiment-policy.json'))
        cases = (
            # The sidecar changed after result.json bound it.
            ({'experiment': 'tamper'}, 'default', 'tampered', 'differs from the result.json reference'),
            # An identity field edited and re-bound: identity_sha256 no longer matches.
            ({'edits': [[sidecar, f'"commit": "{commit}"', f'"commit": "{"0" * 40}"', 'first']]}, 'default',
             'tampered', 'identity_sha256 differs'),
            ({'edits': [[sidecar, '"backend_s": 20', '"backend_s": 21', 'first']]}, 'default', 'tampered',
             'identity_sha256 differs'),
            # The producer's own policy-copy tamper (kicad-route tests/test_experiment.py).
            ({'edits': [[copy, '"F.Cu",\n    "B.Cu"\n  ],\n  "allowed_via', '"F.Cu"\n  ],\n  "allowed_via',
                         'first']]}, 'default', 'incompatible_policy', 'differs from the declared policy'),
            # The same policy in another valid kicad-route byte form is not the bound copy.
            ({}, sorted_copy, 'tampered', 'differs from the sidecar artifacts digest'),
            ({}, None, 'missing', 'binds experiment-policy.json but no regular copy'),
            # The real bytes with numbers past the parser limits.
            ({'edits': [[sidecar, '"backend_s": 20', '"backend_s": ' + '2' * 5000, 'first']]}, 'default',
             'malformed', 'out-of-range number'),
            ({'edits': [[sidecar, '"backend_s": 20', '"backend_s": 1e999', 'first']]}, 'default', 'malformed',
             'non-finite number 1e999'),
            ({'edits': [[copy, '"current-policy"', '1' + '0' * 5000, 'first']]}, 'default', 'malformed',
             'out-of-range number'))
        for scenario, policy_copy, reason, detail in cases:
            with self.subTest(scenario=scenario, copy=policy_copy if policy_copy != sorted_copy else 'sorted'):
                self.refused(self.replay('positive-current', 'current-policy', copy=policy_copy, **scenario),
                             reason, detail)

    def test_real_receipts_under_the_other_declared_policy_are_incompatible(self):
        for run, policy in (('positive-current', 'historical-all-layer'),
                            ('positive-historical', 'current-policy'),
                            ('negative-stale-runtime', 'historical-all-layer')):
            with self.subTest(run=run, declared=policy):
                self.refused(self.replay(run, policy), 'incompatible_policy', 'differs from the declared policy')

    def resealed(self, change_manifest=None, change_body=None, run='positive-current'):
        """The real positive with its effective-configuration manifest edited and the sidecar
        resealed as kicad-route's experiment.write would: the manifest digest in artifacts and
        in the result.json binding, identity.effective restating it, a fresh identity_sha256.
        This is how the K91 review's EI2/EI3 cases were built (k91-ei-review challenges.py)."""
        spec = self.MANIFEST['runs'][run]
        name = attempt.EXPERIMENT_MANIFESTS[0]
        config = json.loads(real(spec['effective_configuration']))
        (change_manifest or (lambda doc: None))(config)
        text = json.dumps(config)
        body = json.loads(real(spec['sidecar']))
        body['artifacts'][name] = hashlib.sha256(text.encode()).hexdigest()
        body['identity']['effective'].update({k: config.get(k) for k in attempt.EXPERIMENT_RESTATED})
        (change_body or (lambda doc: None))(body)
        body['identity_sha256'] = attempt._sha256_of(body['identity'])
        return {'sidecar': json.dumps(body, indent=2, sort_keys=True) + '\n', 'payload': {name: text},
                'effective_binding': {**spec['result_effective_configuration'],
                                      'sha256': body['artifacts'][name]}}

    def test_eligible_receipts_are_requalified_from_the_sealed_manifest(self):
        # K91 review EI2: the stored 'bound' status and comparison_eligible are claims. Each
        # case is resealed self-consistently and the producer still calls it eligible.
        def batches(doc):
            doc['execution'] = {'outer_batches': 2, 'later_subruns': 1}

        def request(doc):
            doc['request_sha256'] = '0' * 64

        def capture(doc):
            doc.update(capture_status='incomplete', issues=['later unknown'])

        def issues(doc):
            doc['issues'] = ['later unknown']

        def coverage(doc):
            doc['coverage'] = 'all_batches'

        def source(doc):
            doc['source_sha256'] = '1' * 64

        def unrestated(body):
            body['identity']['effective']['execution'] = {'outer_batches': 1, 'later_subruns': 0}
        cases = (
            (self.resealed(batches), 'unqualified', 'execution is not one outer batch without later subruns'),
            (self.resealed(batches, unrestated), 'unqualified', 'execution is not one outer batch'),
            (self.resealed(request), 'unqualified', 'request_sha256 is not the retained'),
            # The resealed identity restates capture_status 'incomplete', which the producer schema forbids.
            (self.resealed(capture), 'malformed', 'identity.effective.capture_status must be "complete"'),
            (self.resealed(issues), 'unqualified', 'capture is not complete without issues'),
            (self.resealed(coverage), 'unqualified', 'coverage is not resolved_outer_batch_only'),
            (self.resealed(source), 'unqualified', 'source_sha256 differs from'),
            # The manifest edited without a reseal: not the sealed bytes.
            ({'payload': {attempt.EXPERIMENT_MANIFESTS[0]: '{}'}}, 'tampered',
             'effective-configuration.json sha256 differs from the sidecar artifacts digest'),
            ({'payload': {attempt.EXPERIMENT_MANIFESTS[1]: '{}'}}, 'tampered',
             'effective-configuration-request.json sha256 differs'),
            # The real sealed manifest, but a result.json binding that does not bind it.
            ({'effective_binding': {**self.MANIFEST['runs']['positive-current']['result_effective_configuration'],
                                    'status': 'mismatch'}}, 'unqualified',
             'process.effective_configuration does not bind'),
            ({'effective_binding': {**self.MANIFEST['runs']['positive-current']['result_effective_configuration'],
                                    'sha256': '2' * 64}}, 'unqualified',
             'process.effective_configuration does not bind'))
        for scenario, reason, detail in cases:
            with self.subTest(detail=detail, reason=reason):
                # The K91 review's real bytes on this run are another run's evidence: refused at the
                # binding, before the manifest is read, except where the shape already refuses.
                reason, detail = (reason, detail) if reason == 'malformed' else self.HYBRID
                record = self.refused(self.replay('positive-current', 'current-policy', **scenario), reason, detail)
                if reason == 'malformed':
                    # Outside the producer schema: only the file identity is retained.
                    self.assertNotIn('identity_sha256', record)
                    continue
                # The observed claim and identity are retained with the refusal.
                self.assertTrue(record['comparison_eligible'])
                self.assertEqual(record['identity_sha256'], json.loads(
                    scenario.get('sidecar') or real('positive-current.routability-experiment.json'))['identity_sha256'])
        # The same EI2 cases on the coherent synthetic receipt, sealed self-consistently.
        config, request = attempt.EXPERIMENT_MANIFESTS
        for scenario, reason, detail in (
                ({'manifest': {'execution': {'outer_batches': 2, 'later_subruns': 1}}}, 'unqualified',
                 'execution is not one outer batch without later subruns'),
                ({'manifest': {'execution': {'outer_batches': 2, 'later_subruns': 1}},
                  'effective': {'execution': {'outer_batches': 1, 'later_subruns': 0}}}, 'unqualified',
                 'execution is not one outer batch'),
                ({'manifest': {'request_sha256': '0' * 64}}, 'unqualified', 'request_sha256 is not the retained'),
                ({'manifest': {'capture_status': 'incomplete', 'issues': ['later unknown']}}, 'malformed',
                 'identity.effective.capture_status must be "complete"'),
                ({'manifest': {'issues': ['later unknown']}}, 'unqualified', 'capture is not complete without issues'),
                ({'manifest': {'coverage': 'all_batches'}}, 'unqualified', 'coverage is not resolved_outer_batch_only'),
                ({'manifest': {'source_sha256': '1' * 64}}, 'unqualified', 'source_sha256 differs from'),
                ({'edits': [[config, '"stage"', '"stage" ', 'first']]}, 'tampered',
                 'effective-configuration.json sha256 differs from the sidecar artifacts digest'),
                ({'edits': [[request, '"field_names"', '"field_names" ', 'first']]}, 'tampered',
                 'effective-configuration-request.json sha256 differs'),
                ({'edits': [['result.json', '"status": "bound"', '"status": "mismatch"', 'first']]}, 'unqualified',
                 'process.effective_configuration does not bind'),
                ({'edits': [['result.json', '"coverage": "resolved_outer_batch_only"', '"coverage": "all"', 'first']]},
                 'unqualified', 'process.effective_configuration does not bind')):
            with self.subTest(synthetic=detail, reason=reason):
                self.assert_refused({'name': 'complete', **scenario}, reason, detail)

    def test_an_origin_chain_this_boundary_cannot_requalify_is_refused(self):
        # K91 review EI3: a source_dir that is the run's own candidate truncates the producer's
        # origin chain to depth 0 and stays eligible. A real predecessor chain is unsupported here.
        for scenario, detail in (
                ({'self_cycle': True}, "origin is a self-cycle: attempt.json source_dir is this run's own candidate"),
                (self.resealed(change_body=lambda b: b['identity']['origin'].update(depth=1, predecessors=[
                    {'attempt_sha256': '3' * 64, 'candidate_board_sha256': None, 'experiment_sha256': None}])),
                 'the origin chain has 1 predecessor(s)'),
                (self.resealed(change_body=lambda b: b['identity']['origin'].update(root_input_fingerprint='x')),
                 'root_input_fingerprint is not the sidecar input fingerprint')):
            with self.subTest(detail=detail):
                # The real bytes on this run refuse at the binding (refused() also replays
                # the decision after the router's output is removed).
                self.refused(self.replay('positive-current', 'current-policy', **scenario), *self.HYBRID)
        for scenario, detail in (
                ({'self_cycle': True}, "origin is a self-cycle: attempt.json source_dir is this run's own candidate"),
                ({'identity': {'origin': {'root_input_fingerprint': None, 'depth': 1, 'predecessors': [
                    {'attempt_sha256': '3' * 64, 'candidate_board_sha256': None, 'experiment_sha256': None}]}}},
                 'the origin chain has 1 predecessor(s)'),
                ({'identity': {'origin': {'root_input_fingerprint': 'x', 'depth': 0, 'predecessors': []}}},
                 'root_input_fingerprint is not the sidecar input fingerprint')):
            with self.subTest(synthetic=detail):
                self.assert_refused({'name': 'complete', **scenario}, 'unqualified', detail)

    def test_malformed_attempt_or_facts_are_typed_refusals(self):
        # K91 review EI4: a non-empty JSON list where the producer reads an object raised an
        # AttributeError in kicad-route's qualify; here it is a typed refusal.
        attempt_detail = 'attempt.json is not a kicad-route attempt record'
        for rel, text, detail in (
                ('attempt.json', '[{"wrong":"shape"}]', attempt_detail),
                ('attempt.json', '{"schema":"kicad-route/attempt/1"}', attempt_detail),
                ('attempt.json', '{"a":1,"a":2}', 'duplicate key "a"'),
                ('validate/candidate-facts.json', '[{"wrong":"shape"}]', 'candidate-facts.json is not a native facts'),
                ('validate/candidate-facts.json', '{"tracks":{"0":1}}', 'candidate-facts.json is not a native facts'),
                ('facts/reference-facts.json', '[{"wrong":"shape"}]', 'reference-facts.json is not a native facts'),
                ('facts/reference-facts.json', '{"footprints":[]}', 'reference-facts.json is not a native facts'),
                ('validate/candidate-facts.json', 'NaN', 'non-finite number NaN')):
            with self.subTest(rel=rel, text=text):
                # On the real hybrid the named attempt record is not this run's; refused at the binding.
                self.refused(self.replay('positive-current', 'current-policy', payload={rel: text}), *self.HYBRID)
                # Sealed coherently by the synthetic sidecar, the shape check refuses it.
                self.assert_refused({'name': 'complete', 'payload': {rel: text}}, 'malformed', detail)

    def test_schema_invalid_real_sidecar_members_are_malformed(self):
        # Review H2 (ee442e): members outside the producer schema, with identity untouched.
        base = json.loads(real('positive-current.routability-experiment.json'))
        cases = [('policy_qualification', [], 'sidecar.policy_qualification must be object'),
                 ('candidate', 42, 'sidecar.candidate must be object'),
                 ('native_terminals', 'wrong', 'sidecar.native_terminals must be object'),
                 ('outcome', False, 'sidecar.outcome must be object')]

        def nested(path, value):
            body = json.loads(json.dumps(base))
            parent = body
            for key in path[:-1]:
                parent = parent[key]
            parent[path[-1]] = value
            return body
        cases += [(('outcome', 'wall_clock_stop'), 0, 'sidecar.outcome.wall_clock_stop must be boolean'),
                  (('candidate', 'file_sha256'), 'X' * 64, 'sidecar.candidate.file_sha256 must be 64 lowercase hex'),
                  (('policy_qualification', 'status'), 'MAYBE', 'sidecar.policy_qualification.status must be one of'),
                  (('native_terminals', 'candidate', 'terminal_nets'), None, None),
                  (('outcome', 'extra'), 1, 'sidecar.outcome has unsupported fields extra'),
                  (('artifacts', 'attempt.json'), 7, 'sidecar artifacts must map names to sha256 digests')]
        for field, value, detail in cases:
            with self.subTest(field=field):
                if isinstance(field, tuple):
                    body = nested(field, value)
                    if detail is None:
                        del body['native_terminals']['candidate']['terminal_nets']
                        detail = 'sidecar.native_terminals.candidate lacks terminal_nets'
                else:
                    body = {**base, field: value}
                self.assertEqual(body['identity_sha256'], attempt._sha256_of(body['identity']))
                record = self.refused(self.replay('positive-current', 'current-policy',
                                                  sidecar=json.dumps(body, indent=2) + '\n'), 'malformed', detail)
                self.assertIsNone(record.get('identity_sha256'))

    def test_the_real_shapes_conform_to_the_restated_producer_schema(self):
        for run in self.MANIFEST['runs'].values():
            self.assertIsNone(attempt._shape_error(json.loads(real(run['sidecar'])), attempt.EXPERIMENT_SHAPE))


class NotRunExperimentTest(unittest.TestCase):
    """The in-sandbox entry point refuses before the router runs and still records the policy."""

    def test_a_not_run_attempt_records_absent_evidence_without_a_policy_file(self):
        with tempfile.TemporaryDirectory(prefix='agent-ee-route-k91-') as tmp:
            root = Path(tmp)
            checkout, code = make_checkout(root / 'checkout', 'complete')
            candidate = make_candidate(root / 'candidate')
            work = root / 'work'
            work.mkdir()
            layout = {'work': work, 'candidate': candidate, 'checkout': checkout, 'state': checkout,
                      'pin': 'e' * 40, 'code': code, 'probe': []}
            argv = ['--project', 'project', '--board', 'rc.kicad_pcb', '--schematic', 'rc.kicad_sch',
                    '--name', 'rc', '--label', 'step-1', '--budget', '5', '--timeout', '30',
                    '--experiment-policy', attempt.canonical_json(CURRENT)]
            with patch('sys.stdout'):
                self.assertEqual(attempt.main(argv, layout), attempt.EXIT_REQUEST)
            report = json.loads((work / 'routing/adapter-report.json').read_text())
            record = report['semantics']['experiment']
            self.assertEqual((report['semantics']['classification'], record['status']), ('not_run', 'absent'))
            self.assertFalse((work / 'routing/attempt/agent-ee-experiment-policy.json').exists())
            self.assertEqual(routing_adapter.verify(work, layout=layout)['experiment'], record)


if __name__ == '__main__':
    unittest.main()
