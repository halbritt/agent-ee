"""KICADROUTE-91 routability-experiment/1 evidence at the routing adapter boundary (AGENTEE-442).

Every attempt goes through the consumer path the pipeline uses:
routing_adapter.step() declares it, stage_receipts.run_step() executes it in the
networkless sandbox, and routing_adapter.verify() re-derives it from the
receipt's retained bytes. The router is the coordinator fixture in
kicad_route_fixture.py. Its sidecar is SYNTHETIC: it has the agreed reference
shape result.experiment {path: 'routability-experiment.json', sha256, schema},
run-relative, and only the sidecar fields the adapter reads (schema and
identity.policy, after the unfrozen krt91c draft). It is not KICADROUTE-91
output. The policy declarations are the reviewed five-field routability-policy/1
form of kicad-route a5fa636d experiment_policy.load.
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

# HOOK (AGENTEE-442), FINAL REAL NATIVE REPLAY: when agent-507 sends the final
# reviewed routability-experiment/1 schema, the producer commit and a real native
# receipt, record them here, update EXPERIMENT_CONTRACT and the fields
# experiment_grade reads (identity.policy, the experiment-policy.json copy, any
# settings echo), and rerun test_current_policy_evidence_is_retained against a
# sidecar from that producer on a re-pinned router. Until then the positive case
# is synthetic and qualifies nothing.
K91_REVIEWED = {'producer_commit': None, 'schema_sha256': None, 'native_receipt': None}

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
            'policy_copy': {'path': 'attempt/experiment-policy.json', 'sha256': sha256(copy)}})
        self.assertEqual(report['retained']['attempt/experiment-policy.json'], sha256(copy))
        self.assertEqual(record['sha256'], result['experiment']['sha256'])
        self.assertEqual(report['retained']['attempt/routability-experiment.json'], sha256(sidecar))
        self.assertEqual(report['retained']['attempt/agent-ee-experiment-policy.json'],
                         hashlib.sha256(policy_bytes).hexdigest())
        # The receipt anchors the report, so the record is bound to the coordinator execution.
        self.assertEqual(receipt['artifacts']['routing/adapter-report.json'],
                         sha256(receipt_dir / 'run/routing/adapter-report.json'))
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
                ([copy, ',', ', ', 'first'], 'malformed', 'not the canonical bytes'),
                ([copy, '{', '{"extra":1,', 'first'], 'malformed', 'exactly the routability-policy/1 fields'),
                ([copy, '"routability-policy/1"', '"routability-policy/2"', 'first'], 'malformed',
                 'incompatible experiment-policy.json schema'),
                ([copy, '{', '[{', 'first'], 'malformed', 'must be JSON'),
                ([copy, '"F.Cu","B.Cu"]]', '"B.Cu","F.Cu"]]', 'last'], 'incompatible_policy',
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

    def test_contract_stays_provisional_until_the_reviewed_k91_schema_is_recorded(self):
        self.assertEqual('provisional' in attempt.EXPERIMENT_CONTRACT, K91_REVIEWED['producer_commit'] is None)


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
