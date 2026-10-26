"""KICADROUTE-91 routability-experiment/1 evidence at the routing adapter boundary (AGENTEE-442).

Every attempt goes through the consumer path the pipeline uses:
routing_adapter.step() declares it, stage_receipts.run_step() executes it in the
networkless sandbox, and routing_adapter.verify() re-derives it from the
receipt's retained bytes. The router is the coordinator fixture in
kicad_route_fixture.py. ExperimentBoundaryTest's sidecar is SYNTHETIC: it has the
run-relative reference result.experiment {path: 'routability-experiment.json',
sha256, schema} and the key sets of the real producer's sidecar, but only the
values the adapter reads are meaningful. RealReceiptReplayTest replays the REAL
kicad-route a0ed434 clean native receipt's sidecar and sealed manifests in
tests/fixtures/k91-real through the same path, and retires the interim
4f06368f receipts. FinalNativeReceiptTest grades that receipt with its own
retained run, source, input and candidate, and ProtectedExistingReceiptTest and
ProtectedHumanFallbackReceiptTest grade the protected native receipts, whose
prepared input differs from the original (external artifacts; helper evidence,
not an adapter-run positive: integration activation stays gated).
The policy declarations are the reviewed five-field routability-policy/1 form of
kicad-route experiment_policy.load.
"""
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from unittest.mock import patch

from agent_ee import kicad_route_attempt as attempt
from agent_ee import routing_adapter
from agent_ee import stage_receipts
from tests.kicad_route_fixture import FAKE_PIN, make_candidate, make_checkout, make_layout
from tests.toolchain import require_toolchain

# HOOK (AGENTEE-442), REAL NATIVE REPLAY. agent-507's final frozen K91 producer
# commit, the sha256 of its schemas/routability-experiment-1.json (computed from the
# git object at that commit), and its clean native receipt (tool_identity a0ed434,
# dirty false): the sidecar's file digest (its result.json binding) and its
# identity_sha256. Synthetic fixture evidence cannot match these. Nothing here is
# a re-pin or a router release claim.
K91_REVIEWED = {'producer_commit': 'a0ed43494d44fd8312054b204083881fe76f3a81',
                'schema_sha256': '66df791f04c1c7282d70eccfecc237fdaf49c10f4c3b160d4e49035444128af5',
                'native_receipt': {
                    'sidecar_sha256': '833fac9d41953609b404e836b378c89c3454f71a288141c4103ec5c17e7caddc',
                    'identity_sha256': 'edd33d5a69338b16bf0a681543f216729e22e4f1f751f68d34ecfb86bb3f1ba5'}}
# The interim producer whose receipts are retired: refused, never reinterpreted.
K91_INTERIM = '4f06368fbd68bab44cbfde839647aa5b3f7ef7c5'
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

    def routing_step(self, root, scenario, experiment_policy=None, project='project', board='rc.kicad_pcb',
                     schematic='rc.kicad_sch', name='rc'):
        """The adapter's step declaration over the fixture router, driven with its pin."""
        checkout, code = make_checkout(root / 'checkout', scenario)
        self.pin = {'pin': FAKE_PIN, 'code': code}
        driver = root / 'driver'
        driver.mkdir()
        (driver / 'layout.json').write_text(json.dumps(self.pin))
        (driver / 'run.py').write_text(DRIVER)
        spec = routing_adapter.step(project=project, board=board, schematic=schematic,
                                    name=name, label='step-1', checkout=checkout, budget=5, timeout=30,
                                    experiment_policy=experiment_policy, layout=make_layout(root / 'pin', code))
        spec['argv'][3] = '/opt/driver/run.py'
        spec['mounts'].append({'source': str(driver), 'at': '/opt/driver'})
        return spec

    def receipt(self, scenario, experiment_policy=None, design_files=None):
        self.count += 1
        root = Path(self.tmp.name) / str(self.count)
        design = make_candidate(root / 'design')
        for name, text in (design_files or {}).items():
            (design / 'project' / name).write_text(text)
        spec = self.routing_step(root, scenario, experiment_policy)
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

    def test_the_staged_original_is_an_eligible_origin_through_the_actual_consumer(self):
        # AGENTEE-442 (agent-507 origin adjudication): the router is given a staged copy of
        # /candidate/<project>, which kicad-route _origin reads as an original (depth 0).
        receipt, receipt_dir, design, report = self.receipt({'name': 'complete'}, CURRENT)
        self.assertEqual((receipt['exit_code'], receipt['status']), (0, 'completed'), report['semantics'])
        staged = '/work/routing/agent-ee-original/project'
        argv = report['process']['argv']
        self.assertEqual(argv[argv.index('--dir') + 1], staged)
        self.assertEqual(report['inputs']['staged'], {
            'path': staged, 'root': '/work/routing/agent-ee-original', 'files': receipt['inputs'],
            'sha256': report['inputs']['sha256']})
        attempt_dir = receipt_dir / 'run/routing/attempt'
        self.assertEqual(json.loads((attempt_dir / 'attempt.json').read_text())['source']['source_dir'], staged)
        body = json.loads((attempt_dir / attempt.EXPERIMENT_SIDECAR).read_text())
        self.assertEqual((body['identity']['origin']['depth'], body['identity']['origin']['predecessors']), (0, []))
        record = report['semantics']['experiment']
        self.assertEqual((record['status'], record['comparison_eligible'], record['ineligible_reasons']),
                         ('retained', True, []))
        # verify re-hashes the staged bytes the run kept; they are the receipt's inputs.
        kept = receipt_dir / 'run/routing' / attempt.ORIGINAL_STAGE
        self.assertEqual(attempt.files_of(attempt.tree(kept)), receipt['inputs'])
        self.assertEqual(self.verify(receipt_dir, receipt)['experiment'], record)
        stage_receipts.adopt(receipt_dir, receipt, design)
        board = kept / 'project/rc.kicad_pcb'
        original = board.read_bytes()
        board.write_bytes(original + b'\n')
        with self.assertRaisesRegex(ValueError, 'staged original bytes differ from the adapter report'):
            self.verify(receipt_dir, receipt)
        board.write_bytes(original)
        self.assertEqual(self.verify(receipt_dir, receipt)['experiment'], record)

    def test_the_old_mount_or_an_input_beside_a_run_record_is_ineligible_and_refused(self):
        # The producer contract (kicad-route bd1e633 experiment._origin, restated by the
        # fixture): an input whose parent is named candidate, or whose grandparent holds a
        # run record, without a readable predecessor, is not an original project.
        missing = 'origin predecessor is missing or does not own this input'
        for label, scenario in (('the /candidate/<project> mount as --dir', {'read_mount': True}),
                                ('attempt.json beside the input', {'grandparent_record': 'attempt.json'}),
                                ('result.json beside the input', {'grandparent_record': 'result.json'})):
            with self.subTest(label):
                receipt_dir = self.assert_refused({'name': 'complete', **scenario}, 'ineligible', missing)
                record = json.loads((receipt_dir / 'run/routing/adapter-report.json').read_text())[
                    'semantics']['experiment']
                self.assertEqual((record['comparison_eligible'], record['ineligible_reasons']), (False, [missing]))

    def test_an_absent_or_stale_staged_replay_tree_refuses_verify_and_adopt(self):
        # Review H1 (Sol, ee442d2): the staged tree is required, retained replay evidence.
        # Deleting it, or one file of it, or replacing it with other bytes, after the
        # receipt is sealed, refuses through the actual verify and adopt paths.
        def stale_file(kept, run):
            (kept / 'project/rc.kicad_pcb').write_bytes((run / 'routing/candidate/project/rc.kicad_pcb').read_bytes())

        def stale_tree(kept, run):
            shutil.rmtree(kept)
            stale = make_candidate(kept)
            (stale / 'project/rc.kicad_pcb').write_text('stale board bytes\n')

        for label, mutate in (('whole staged tree deleted', lambda kept, run: shutil.rmtree(kept)),
                              ('one staged file deleted', lambda kept, run: (kept / 'project/rc.kicad_sch').unlink()),
                              ('one staged file replaced by stale bytes', stale_file),
                              ('staged tree replaced by a stale tree', stale_tree)):
            with self.subTest(label):
                receipt, receipt_dir, design, report = self.receipt({'name': 'complete'}, CURRENT)
                self.assertEqual(self.verify(receipt_dir, receipt)['proposal'], 'eligible')
                before = attempt.files_of(attempt.tree(design))
                mutate(receipt_dir / 'run/routing' / attempt.ORIGINAL_STAGE, receipt_dir / 'run')
                with self.assertRaises(ValueError):
                    self.verify(receipt_dir, receipt)
                with self.assertRaises(ValueError):
                    stage_receipts.adopt(receipt_dir, receipt, design)
                self.assertEqual(attempt.files_of(attempt.tree(design)), before)

    def test_every_file_of_the_staged_tree_is_bound(self):
        # Review H1 (Sol, ee442d2): every staged file is bound (sibling library, custom
        # rules, project, schematic) through verify and adopt.
        def assets(path, stock=make_candidate):
            design = stock(path)
            (design / 'library').mkdir()
            (design / 'library/asset.bin').write_bytes(b'owned sibling asset\x00')
            (design / 'project/rc.kicad_dru').write_text('(version 1)\n')
            return design

        with patch.object(sys.modules[__name__], 'make_candidate', assets):
            receipt, receipt_dir, design, report = self.receipt({'name': 'complete'}, CURRENT)
        kept = receipt_dir / 'run/routing' / attempt.ORIGINAL_STAGE
        self.assertEqual(attempt.files_of(attempt.tree(kept)), receipt['inputs'])
        for rel in ('library/asset.bin', 'project/rc.kicad_dru', 'project/rc.kicad_pro', 'project/rc.kicad_sch'):
            with self.subTest(rel):
                path = kept / rel
                old = path.read_bytes()
                path.write_bytes(old + b'edited')
                try:
                    with self.assertRaisesRegex(ValueError, 'staged original bytes differ'):
                        self.verify(receipt_dir, receipt)
                    with self.assertRaises(ValueError):
                        stage_receipts.adopt(receipt_dir, receipt, design)
                finally:
                    path.write_bytes(old)
        self.assertEqual(self.verify(receipt_dir, receipt)['proposal'], 'eligible')
        stage_receipts.adopt(receipt_dir, receipt, design)
        self.assertEqual(receipt['spec']['writes'], {'project/rc.kicad_pcb': 'routing/candidate/project/rc.kicad_pcb'})

    def test_a_router_input_write_seals_its_observed_post_run_tree(self):
        # Review H3 (Sol, ee442d2): a router that wrote its input is an honest,
        # non-adoptable contradiction whose observed post-run tree is sealed in the
        # report; a later edit of the retained staged bytes is evidence corruption.
        receipt, receipt_dir, design, report = self.receipt({'name': 'touch_input'}, CURRENT)
        sem = report['semantics']
        self.assertEqual((sem['classification'], sem['proposal']), ('contradiction', 'none'))
        self.assertIn('the staged original changed during the attempt', sem['reasons'])
        staged, after = report['inputs']['staged'], report['inputs']['staged_after']
        kept = receipt_dir / 'run/routing' / attempt.ORIGINAL_STAGE
        entries = attempt.tree(kept)
        # The observed post-run tree is sealed, and it is not the staged input.
        self.assertEqual(after, {'status': 'observed', 'files': attempt.files_of(entries),
                                 'sha256': attempt.canonical_identity(entries)})
        self.assertNotEqual(after['sha256'], staged['sha256'])
        self.assertEqual(self.verify(receipt_dir, receipt)['classification'], 'contradiction')
        with self.assertRaises(ValueError):
            stage_receipts.adopt(receipt_dir, receipt, design)
        board = kept / 'project/rc.kicad_pcb'
        board.write_bytes(board.read_bytes() + b'POST-RECEIPT EDIT')
        with self.assertRaisesRegex(ValueError, 'staged original bytes differ'):
            self.verify(receipt_dir, receipt)
        with self.assertRaises(ValueError):
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

    def assert_refused(self, scenario, reason, detail, replay=True, design_files=None):
        """The actual consumer refuses the evidence with a typed reason and keeps no candidate.

        replay=False: the receipt's own strict result.json parser already reports
        the backend as unknown, so verify refuses the whole receipt instead."""
        receipt, receipt_dir, design, report = self.receipt(scenario, CURRENT, design_files)
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

    def test_the_run_settings_and_attempt_must_record_the_declared_policy(self):
        # kicad-route a0ed434 onward writes settings.experiment_policy_sha256 (the native
        # bd1e633 run, evidence/k91-native-positive): a run that omits it, or records another
        # declaration, is not the attempt the adapter requested.
        # The same run records the loaded declaration in attempt.json.
        settings, recorded = ('result settings differ from the fixed adapter settings',
                              'attempt.json experiment_policy differs from the declared policy')
        for label, scenario, reason in (
                ('settings omit', {'settings_policy': 'omit'}, settings),
                ('settings other', {'settings_policy_edit': {'experiment_class': 'historical-all-layer'}}, settings),
                ('attempt omit', {'attempt_policy': 'omit'},
                 'attempt.json missing or shaped differently from the pinned tool'),
                ('attempt other', {'attempt_policy_edit': {'allowed_via_spans': []}}, recorded)):
            with self.subTest(label):
                receipt, receipt_dir, design, report = self.receipt({'name': 'complete', **scenario}, CURRENT)
                sem = report['semantics']
                self.assertEqual((receipt['exit_code'], sem['classification']),
                                 (attempt.EXIT_CONTRADICTION, 'contradiction'))
                self.assertIn(reason, sem['reasons'])
                self.assertIsNone(report['candidate'])
                with self.assertRaises(ValueError):
                    stage_receipts.adopt(receipt_dir, receipt, design)
                self.assertEqual(self.verify(receipt_dir, receipt)['classification'], 'contradiction')
        receipt, receipt_dir, _, report = self.receipt({'name': 'complete'}, CURRENT)
        result = json.loads((receipt_dir / 'run/routing/attempt/result.json').read_text())
        self.assertEqual(result['settings']['experiment_policy_sha256'],
                         report['semantics']['experiment']['policy']['policy_sha256'])
        recorded = json.loads((receipt_dir / 'run/routing/attempt/attempt.json').read_text())
        self.assertEqual(recorded['experiment_policy'], CURRENT)
        self.assertEqual(report['semantics']['classification'], 'complete')

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
        for name in attempt.EXPERIMENT_BOUND:
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
            ([['validate/never-written.json', wrong]], 'missing', 'binds validate/never-written.json but no regular'),
            # The raw backend output is never retained: its digest is the adapter's own
            # observation of the run, and no other design file can be bound.
            ([['work/krt-out/rc.kicad_pcb', wrong]], 'malformed', "'work/krt-out/rc.kicad_pcb' is a design file"),
            ([['work/other-output.kicad_pcb', wrong]], 'malformed', 'is a design file, which is never retained')]
        for ops, reason, detail in cases:
            with self.subTest(ops=ops):
                self.assert_refused({'name': 'complete', 'artifacts': ops}, reason, detail)
        with self.subTest('raw output sealed but never observed'):
            self.assert_refused({'name': 'complete', 'remove': [attempt.EXPERIMENT_RAW_OUTPUT]}, 'missing',
                                f'binds {attempt.EXPERIMENT_RAW_OUTPUT} but the adapter observed no such regular file')

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
        # The raw output is a design file the adapter does not retain; the fixture writes
        # the same board for the same design, so the donor's digest is this run's too.
        whole = {'payload': {n: (donor / n).read_text() for n in sealed if n != attempt.EXPERIMENT_RAW_OUTPUT},
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
                 "source_dir '/work/routing/out/alias' is not this attempt's input /work/routing/agent-ee-original/project"),
                # The read-only mount is not the input the router was given (AGENTEE-442).
                ({'source_dir': '/candidate/project'}, 'run_mismatch', 'is not this attempt\'s input'),
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

    def test_the_named_input_files_are_this_executions(self):
        # Review H1 (ee442g): the project and rules files the sidecar names, with their
        # digests, are reconciled with the retained snapshot and attempt record. Each case
        # is sealed self-consistently (every digest and the identity seal verify).
        rules = {'rc.kicad_dru': '(version 1)\n'}
        receipt, receipt_dir, design, report = self.receipt({'name': 'complete'}, CURRENT, rules)
        self.assertEqual(report['semantics']['experiment']['status'], 'retained', report['semantics'])
        body = json.loads((receipt_dir / 'run/routing/attempt' / attempt.EXPERIMENT_SIDECAR).read_text())
        files = json.loads((receipt_dir / 'run/routing/attempt/snapshot.json').read_text())['files']
        self.assertEqual(body['identity']['input']['project'], {'name': 'rc.kicad_pro', 'sha256': files['rc.kicad_pro']})
        self.assertEqual(body['identity']['input']['rules'],
                         {'name': 'rc.kicad_dru', 'sha256': files['rc.kicad_dru'], 'not_loaded': []})
        self.assertEqual(body['identity']['effective']['input'], {
            'board_sha256': files['rc.kicad_pcb'],
            'rules': {'.kicad_pro': files['rc.kicad_pro'], '.kicad_dru': files['rc.kicad_dru']}})
        shutil.rmtree(receipt_dir / 'run/routing/out')
        self.assertEqual(self.verify(receipt_dir, receipt)['experiment'], report['semantics']['experiment'])
        stage_receipts.adopt(receipt_dir, receipt, design)
        pro, dru, wrong = files['rc.kicad_pro'], files['rc.kicad_dru'], '0' * 64
        captured = {'board_sha256': files['rc.kicad_pcb'], 'rules': {'.kicad_pro': pro, '.kicad_dru': dru}}
        other_board = {**captured, 'board_sha256': wrong}
        for scenario, detail in (
                ({'input': {'project': {'name': 'other.kicad_pro', 'sha256': pro}}}, 'input project'),
                ({'input': {'project': {'name': 'rc.kicad_pro', 'sha256': wrong}}}, 'input project'),
                ({'input': {'project': {'name': 'rc.kicad_pro', 'sha256': None}}}, 'input project'),
                ({'input': {'rules': {'name': 'other.kicad_dru', 'sha256': dru, 'not_loaded': []}}}, 'input rules'),
                ({'input': {'rules': {'name': 'rc.kicad_dru', 'sha256': wrong, 'not_loaded': []}}}, 'input rules'),
                ({'input': {'rules': {'name': 'rc.kicad_dru', 'sha256': dru, 'not_loaded': ['x']}}}, 'input rules'),
                ({'input': {'rules': {'name': None, 'sha256': None, 'not_loaded': []}}}, 'input rules'),
                # kicad-route always restates not_loaded; an omitted one is not the attempt's.
                ({'input': {'rules': {'name': 'rc.kicad_dru', 'sha256': dru}}}, 'input rules'),
                # The attempt record and the sidecar agree on a rules file KiCad does not load.
                ({'rules_files': {'custom_rules': 'other.kicad_dru'}}, 'input rules file'),
                ({'manifest': {'input': other_board}, 'effective': {'input': other_board}}, 'effective input'),
                ({'request_manifest': {'input': other_board}}, 'effective input')):
            with self.subTest(scenario=scenario):
                self.assert_refused({'name': 'complete', **scenario}, 'run_mismatch', f'the sidecar {detail}',
                                    design_files=rules)
        # Without a rules file the null rules identity is the proven state, not a claim.
        receipt, receipt_dir, _, report = self.receipt({'name': 'complete'}, CURRENT)
        self.assertEqual(report['semantics']['experiment']['status'], 'retained')
        self.assertEqual(json.loads((receipt_dir / 'run/routing/attempt' / attempt.EXPERIMENT_SIDECAR).read_text())[
            'identity']['input']['rules'], {'name': None, 'sha256': None, 'not_loaded': []})
        self.assert_refused({'name': 'complete', 'input': {'rules': {'name': 'rc.kicad_dru', 'sha256': None,
                                                                    'not_loaded': []}}},
                            'run_mismatch', 'the sidecar input rules')

    def test_the_rules_inventory_is_derived_from_the_snapshot(self):
        # agent-510 R1: which rules file KiCad loads and which siblings it does not are
        # derived from the snapshot (kicad-route a0ed434 cli.rules_files), never taken
        # from the attempt record or the sidecar. Each negative states the same false
        # inventory in BOTH, sealed self-consistently.
        rules = {'rc.kicad_dru': '(version 1)\n'}
        for scenario, detail in (
                ({'rules_files': {'custom_rules': None}}, 'input rules file'),
                ({'rules_files': {'not_loaded': ['invented.kicad_dru']}}, 'input rules file'),
                ({'rules_files': {'project': 'other.kicad_pro'}}, 'input rules file')):
            with self.subTest(scenario=scenario):
                directory = self.assert_refused({'name': 'complete', **scenario}, 'run_mismatch',
                                                f'the sidecar {detail}', design_files=rules)
                body = json.loads((directory / 'run/routing/attempt' / attempt.EXPERIMENT_SIDECAR).read_text())
                record = json.loads((directory / 'run/routing/attempt/attempt.json').read_text())
                self.assertEqual([body['identity']['input']['rules'][k] for k in ('name', 'not_loaded')],
                                 [record['rules_files'][k] for k in ('custom_rules', 'not_loaded')])
        # Siblings KiCad does not load are the derived not_loaded set: retained and adopted
        # after OUT removal when stated, refused when omitted.
        siblings = {**rules, 'old.kicad_dru': '(version 1)\n', 'old.kicad_pro': '{}\n'}
        receipt, receipt_dir, design, report = self.receipt({'name': 'complete'}, CURRENT, siblings)
        self.assertEqual(report['semantics']['experiment']['status'], 'retained', report['semantics'])
        body = json.loads((receipt_dir / 'run/routing/attempt' / attempt.EXPERIMENT_SIDECAR).read_text())
        self.assertEqual(body['identity']['input']['rules']['not_loaded'], ['old.kicad_dru', 'old.kicad_pro'])
        shutil.rmtree(receipt_dir / 'run/routing/out')
        self.assertEqual(self.verify(receipt_dir, receipt)['experiment'], report['semantics']['experiment'])
        stage_receipts.adopt(receipt_dir, receipt, design)
        self.assert_refused({'name': 'complete', 'rules_files': {'not_loaded': []}}, 'run_mismatch',
                            'the sidecar input rules file', design_files=siblings)
        # Without a rules file the derived inventory is null rules, the legitimate absent-rules
        # positive (test_the_named_input_files_are_this_executions); claiming one refuses.
        self.assert_refused({'name': 'complete', 'rules_files': {'custom_rules': 'rc.kicad_dru'}}, 'run_mismatch',
                            'the sidecar input rules file')

    def test_the_prepared_input_is_bound_to_the_observed_work_input(self):
        # agent-510 R2: protection and preprocessing change the backend input, so the
        # ORIGINAL input (identity.input) is the snapshot and the PREPARED input
        # (effective.input, both manifests' input, captured_input_sha256) is the
        # work/input the adapter observed by digest. The fixture's 'prepared' bytes
        # stand in for protection locking copper in the work input.
        locked = {'rc.kicad_pcb': 'SYNTHETIC board, copper locked by protection\n'}
        receipt, receipt_dir, design, report = self.receipt({'name': 'complete', 'prepared': locked}, CURRENT)
        self.assertEqual(report['semantics']['experiment']['status'], 'retained', report['semantics'])
        kept = receipt_dir / 'run/routing/attempt'
        body = json.loads((kept / attempt.EXPERIMENT_SIDECAR).read_text())
        files = json.loads((kept / 'snapshot.json').read_text())['files']
        prepared = hashlib.sha256(locked['rc.kicad_pcb'].encode()).hexdigest()
        original = {'board_sha256': files['rc.kicad_pcb'],
                    'rules': {'.kicad_pro': files['rc.kicad_pro'], '.kicad_dru': None}}
        self.assertEqual(body['identity']['input']['board']['sha256'], files['rc.kicad_pcb'])
        self.assertEqual(body['identity']['effective']['input'], {**original, 'board_sha256': prepared})
        self.assertNotEqual(prepared, files['rc.kicad_pcb'])
        # Observed by digest, never retained.
        self.assertEqual(report['omitted_design_files']['work/input/project/rc.kicad_pcb'], prepared)
        self.assertFalse((kept / 'work/input').exists())
        shutil.rmtree(receipt_dir / 'run/routing/out')
        self.assertEqual(self.verify(receipt_dir, receipt)['experiment'], report['semantics']['experiment'])
        stage_receipts.adopt(receipt_dir, receipt, design)
        absent = 'the adapter observed no regular prepared input board and project in work/input'
        rules = {'rc.kicad_dru': '(version 1)\n'}
        for scenario, detail, design_files in (
                # The stage conflation, stated consistently: every prepared-stage record claims
                # the original input while the observed work input is the locked one.
                ({'prepared': locked, 'manifest': {'input': original}, 'request_manifest': {'input': original},
                  'effective': {'input': original}, 'binding': {'captured_input_sha256': original['board_sha256']}},
                 'the sidecar effective input', None),
                ({'prepared': locked, 'binding': {'captured_input_sha256': original['board_sha256']}},
                 'the sidecar captured input board', None),
                # The work input changed after the manifests captured it.
                ({'prepared_after': {'rc.kicad_pcb': 'SYNTHETIC other board\n'}}, 'the sidecar effective input', None),
                ({'prepared_after': {'rc.kicad_pro': '{"other": 1}\n'}}, 'the sidecar effective input', None),
                ({'prepared_after': {'rc.kicad_dru': '(version 1) (rule other)\n'}}, 'the sidecar effective input',
                 rules),
                ({'prepared_after': {'rc.kicad_dru': None}}, 'the sidecar effective input', rules),
                # No regular prepared board or project to observe.
                ({'prepared_after': {'rc.kicad_pcb': None}}, absent, None),
                ({'prepared_after': {'rc.kicad_pro': None}}, absent, None),
                ({'prepared_after': {'rc.kicad_pcb': 'link:../../../reference/project/rc.kicad_pcb'}}, absent, None),
                ({'prepared_after': {'rc.kicad_pcb': 'link:rc.kicad_pcb'}}, absent, None)):
            with self.subTest(scenario=scenario):
                self.assert_refused({'name': 'complete', **scenario}, 'run_mismatch', detail,
                                    design_files=design_files)

    def test_an_optional_prepared_rules_link_is_not_absence(self):
        # agent-510 R4: the original input loads rc.kicad_dru; every prepared-stage record
        # (both manifests, the effective identity, the binding) claims the prepared
        # .kicad_dru null. Only an actually absent one is that input. A link, valid or
        # dangling, or a directory there is refused, never followed, as kicad-route a0ed434
        # experiment._retained_backend_input rejects it; retain() records its path type.
        rules = {'rc.kicad_dru': '(version 1)\n'}
        name = 'work/input/project/rc.kicad_dru'
        for prepared, observed in ((None, None), ('(version 1) (rule prepared)\n',
                                                  hashlib.sha256(b'(version 1) (rule prepared)\n').hexdigest())):
            with self.subTest(prepared=prepared):
                receipt, receipt_dir, design, report = self.receipt(
                    {'name': 'complete', 'prepared': {'rc.kicad_dru': prepared}}, CURRENT, rules)
                self.assertEqual(report['semantics']['experiment']['status'], 'retained', report['semantics'])
                body = json.loads((receipt_dir / 'run/routing/attempt' / attempt.EXPERIMENT_SIDECAR).read_text())
                self.assertEqual(body['identity']['effective']['input']['rules']['.kicad_dru'], observed)
                self.assertEqual(body['identity']['input']['rules']['name'], 'rc.kicad_dru')
                self.assertEqual(report['omitted_design_files'].get(name), observed)
                shutil.rmtree(receipt_dir / 'run/routing/out')
                self.assertEqual(self.verify(receipt_dir, receipt)['experiment'], report['semantics']['experiment'])
                stage_receipts.adopt(receipt_dir, receipt, design)
        for entry, kind in (('link:../../../reference/project/rc.kicad_dru', 'link'), ('link:missing.kicad_dru', 'link'),
                            ('link:rc.kicad_dru', 'link'), ('dir:', 'special')):
            with self.subTest(entry=entry):
                receipt_dir = self.assert_refused({'name': 'complete', 'prepared': {'rc.kicad_dru': entry}},
                                                  'run_mismatch', f'the prepared {name} is a ', design_files=rules)
                report = json.loads((receipt_dir / 'run/routing/adapter-report.json').read_text())
                self.assertEqual(report['omitted_design_files'][name], kind)
                self.assertIn('neither absent nor a regular file', report['semantics']['experiment']['detail'])

    def test_the_three_board_seals_are_bound_to_their_own_stage_records(self):
        # kicad-route a0ed434: the raw backend output, the wrapper output before native
        # refill and the graded refilled board are distinct seals, each bound to its record.
        other = '7' * 64
        for scenario, detail in (
                ({'seals': {'raw_backend_output_board_sha256': other}}, 'raw backend output board'),
                ({'binding': {'backend_output_sha256': other}}, 'raw backend output board'),
                ({'binding': {'backend_output_sha256': None}}, 'raw backend output board'),
                ({'manifest': {'output': {'board_sha256': other, 'stage': attempt.EXPERIMENT_RAW_STAGE}}},
                 'raw backend output board'),
                ({'binding': {'output_stage': attempt.EXPERIMENT_WRAPPER_STAGE}}, 'raw backend output stage'),
                ({'seals': {'backend_output_board_sha256': other}}, 'wrapper output board'),
                ({'seals': {'pre_refill_board_sha256': other}}, 'wrapper output board'),
                ({'binding': {'wrapper_output_board_sha256': other}}, 'wrapper output board'),
                ({'binding': {'wrapper_output_stage': attempt.EXPERIMENT_RAW_STAGE}}, 'wrapper output stage'),
                ({'seals': {'validated_board_sha256': other}}, 'graded refilled board'),
                ({'seals': {'file_sha256': other}}, 'graded refilled board'),
                ({'binding': {'captured_input_sha256': other}}, 'captured input board'),
                ({'binding': {'captured_input_sha256': None}}, 'captured input board')):
            with self.subTest(scenario=scenario):
                self.assert_refused({'name': 'complete', **scenario}, 'run_mismatch', f'the sidecar {detail}')
        # The raw output the adapter observed is not the one the sidecar seals.
        self.assert_refused({'name': 'complete', 'artifacts': [[attempt.EXPERIMENT_RAW_OUTPUT, other]]}, 'tampered',
                            f'{attempt.EXPERIMENT_RAW_OUTPUT} sha256 differs from the sidecar artifacts digest')

    def test_a_malformed_snapshot_is_a_typed_refusal_not_a_crash(self):
        # Review H2 (ee442g): a snapshot.json whose files is not a digest map raised
        # AttributeError (exit 70, no report); it is a typed, replayable refusal.
        shape = 'snapshot.json files is not a non-empty map of file names to sha256 digests'
        for text, reason, detail in (
                ('{"files": ["bad"]}', 'malformed', shape), ('{"files": "bad"}', 'malformed', shape),
                ('{"files": 42}', 'malformed', shape), ('{"files": {}}', 'malformed', shape),
                ('{"files": null}', 'malformed', shape), ('{}', 'malformed', shape), ('[]', 'malformed', shape),
                ('{"files": {"rc.kicad_pcb": "x"}}', 'malformed', shape),
                ('{"files": {"a": 1, "a": 2}}', 'malformed', 'duplicate key "a"'),
                ('not json {', 'malformed', 'snapshot.json')):
            with self.subTest(text=text):
                self.assert_refused({'name': 'complete', 'payload': {'snapshot.json': text}}, reason, detail)
        self.assert_refused({'name': 'complete', 'remove': ['snapshot.json']}, 'missing',
                            'no regular snapshot.json was retained')

    def test_an_attempt_board_that_is_not_a_plain_name_is_a_typed_refusal_not_a_crash(self):
        # Review H1 (ee442j): attempt.board '.' or '/' raised ValueError in the rules
        # inventory's suffix derivation (exit 70, no report). Every name that is not a
        # plain file name is refused before any name is derived from it, the sidecar
        # sealing the record as written; assert_refused replays it after OUT removal.
        for board in ('.', '/', '..', 'sub/rc.kicad_pcb', 'rc.kicad_pcb/', './rc.kicad_pcb'):
            with self.subTest(board=board):
                receipt_dir = self.assert_refused({'name': 'complete', 'attempt_board': board}, 'run_mismatch',
                                                  f'attempt.json board {board!r} is not a plain file name')
                attempt_dir = receipt_dir / 'run/routing/attempt'
                body = json.loads((attempt_dir / attempt.EXPERIMENT_SIDECAR).read_text())
                self.assertEqual(json.loads((attempt_dir / 'attempt.json').read_text())['board'], board)
                self.assertEqual(body['artifacts']['attempt.json'], sha256(attempt_dir / 'attempt.json'))
                self.assertEqual(body['identity_sha256'], attempt._sha256_of(body['identity']))
                self.assertIsNone(attempt._sidecar_shape_refusal(body))

    def test_host_paths_aliases_and_link_loops_are_typed_refusals(self):
        # Coordinator H2 (ee442h): an absolute path that exists on the verifier host, a
        # host link to the design, and link loops inside and outside the run. The path is
        # compared as a producer-namespace string and never followed, so a loop is a typed
        # refusal, not an exception, and the decision replays after OUT is removed.
        host = Path(self.tmp.name) / 'producer/fixtures/project'
        host.mkdir(parents=True)
        (Path(self.tmp.name) / 'host-alias').symlink_to(host)
        cycle = "origin is a self-cycle: attempt.json source_dir is this run's own candidate"
        other = "is not this attempt's input /work/routing/agent-ee-original/project"
        for scenario, reason, detail in (
                ({'source_dir': str(host)}, 'run_mismatch', other),
                ({'source_dir': str(Path(self.tmp.name) / 'host-alias')}, 'run_mismatch', other),
                ({'source_dir': '/home/halbritt/.cache/agent-worktrees/kicad-route/krt91-final2-agent507/'
                                'fixtures/route_demo'}, 'run_mismatch', other),
                ({'links': {'{run}/loop': '{run}/loop'}, 'source_dir': '{run}/loop'}, 'unqualified', cycle),
                ({'links': {'{run}/hop': '{run}/hop'}, 'source_dir': '{run}/hop/../candidate/project'},
                 'unqualified', cycle),
                ({'links': {'{out}/loop': '{out}/loop'}, 'source_dir': '{out}/loop'}, 'run_mismatch', other),
                ({'links': {'{out}/dangling': '/nonexistent/project'}, 'source_dir': '{out}/dangling'},
                 'run_mismatch', other)):
            with self.subTest(scenario=scenario):
                self.assert_refused({'name': 'complete', **scenario}, reason, detail)

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
    """The REAL kicad-route a0ed434 receipt bytes through step, run_step, verify and adopt.

    The fixture router writes the real sidecar, sealed manifests and policy-copy
    bytes verbatim and binds them as the real run's result.json did; agent-ee
    declares the real five-field policy. The router routes the fixture design, so
    these are PRODUCER-FORMAT bytes attached to another run: the real sidecar
    describes the route_demo run, not this execution, and is refused (agent-509
    ruling on ee442f). The real receipt's own positive, with its own run, source,
    input and candidate, is FinalNativeReceiptTest. The interim 4f06368f receipts
    lack the final producer's effective.input and raw-output seal: they are
    retired, refused as malformed and never reinterpreted. Each EI case is also
    run coherently on the synthetic receipt, where its own check is what refuses.
    """
    setUp, receipt, verify, assert_refused = (ExperimentBoundaryTest.setUp, ExperimentBoundaryTest.receipt,
                                              ExperimentBoundaryTest.verify, ExperimentBoundaryTest.assert_refused)
    routing_step = ExperimentBoundaryTest.routing_step
    HYBRID = ('tampered', 'attempt.json sha256 differs from the sidecar artifacts digest')
    MANIFEST = json.loads((REAL / 'receipts.json').read_text())
    FINAL = 'final-current'
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
        self.assertEqual(self.MANIFEST['retired_interim']['producer_commit'], K91_INTERIM)
        positive = self.MANIFEST['runs'][self.FINAL]
        self.assertEqual(positive['result_experiment']['sha256'], K91_REVIEWED['native_receipt']['sidecar_sha256'])
        body = json.loads(real(positive['sidecar']))
        self.assertEqual(body['identity_sha256'], K91_REVIEWED['native_receipt']['identity_sha256'])
        self.assertEqual(body['identity']['build']['wrapper']['commit'], K91_REVIEWED['producer_commit'])
        self.assertEqual(body['identity']['build']['wrapper']['dirty'], False)
        for run in (*self.MANIFEST['runs'].values(), *self.MANIFEST['retired_interim']['runs'].values()):
            self.assertEqual(run['result_experiment']['sha256'], sha256(REAL / run['sidecar']))

    def test_the_real_final_receipt_on_another_run_is_refused_not_adopted(self):
        # A real sidecar of another run attached to this run's synthetic attempt record,
        # facts and candidate is not this execution's.
        for run, policy in ((self.FINAL, 'current-policy'),):
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
                self.assertEqual((record['sha256'], record['identity_sha256']),
                                 tuple(K91_REVIEWED['native_receipt'].values()))
                self.assertNotEqual(sha256(outcome[1] / 'run/routing/attempt/attempt.json'),
                                    body['artifacts']['attempt.json'])

    def test_a_self_consistent_non_identity_sidecar_forgery_is_refused(self):
        # Coordinator ruling (ee442g): the real sidecar with its digests rewritten to this run's
        # attempt record, facts and policy copy, its own sealed manifests, and resealed. Every
        # digest and the identity seal verify; the run it describes is not this one.
        # With the input restated too, the named project file (route_demo.kicad_pro) is
        # the first claim that is not this run's (review H1, ee442g).
        for forge, detail in (({'forge': True}, 'the sidecar input fingerprint'),
                              ({'forge': True, 'input': True}, 'the sidecar input project')):
            with self.subTest(forge=forge):
                spec = self.MANIFEST['runs'][self.FINAL]
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
                    self.assertEqual(body['artifacts'][name], sha256(attempt_dir / name) if
                                     name != attempt.EXPERIMENT_RAW_OUTPUT else
                                     outcome[3]['omitted_design_files'][name], name)
                self.assertIsNone(attempt._shape_error(body, attempt.EXPERIMENT_SHAPE))
                self.refused(outcome, 'run_mismatch', detail)

    def test_the_retired_interim_receipts_are_refused_as_malformed(self):
        # The 4f06368f sidecars predate the final producer's raw backend output seal and
        # effective.input: never reinterpreted as the final shape, refused whatever they claim.
        for run, spec in self.MANIFEST['retired_interim']['runs'].items():
            body = json.loads(real(spec['sidecar']))
            self.assertEqual(body['identity']['build']['wrapper']['commit'], K91_INTERIM)
            policy = body['identity']['policy']['experiment_class']
            with self.subTest(run=run):
                record = self.refused(self.receipt({'name': 'complete', 'replay': {
                    'sidecar': real(spec['sidecar']), 'copy': real(spec['policy_copy'])}}, self.POLICY[policy]),
                    'malformed', 'sidecar.candidate lacks raw_backend_output_board_sha256')
                self.assertNotIn('identity_sha256', record)
                body['candidate']['raw_backend_output_board_sha256'] = None
                self.assertEqual(attempt._shape_error(body, attempt.EXPERIMENT_SHAPE),
                                 None if body['identity']['effective'] is None else
                                 'sidecar.identity.effective lacks input')

    def test_a_real_receipt_the_producer_marks_ineligible_is_refused(self):
        # Derived from the real final bytes: the producer's eligibility verdict is not in
        # the sealed identity, so marking it ineligible keeps identity_sha256 valid.
        body = json.loads(real('final-current.routability-experiment.json'))
        body.update(comparison_eligible=False, ineligible_reasons=['routing_environment binding mismatch'])
        record = self.refused(self.replay(self.FINAL, 'current-policy', sidecar=json.dumps(body, indent=2) + '\n'),
                              'ineligible', 'routing_environment binding mismatch')
        self.assertEqual((record['comparison_eligible'], record['ineligible_reasons'], record['identity_sha256']),
                         (False, body['ineligible_reasons'], K91_REVIEWED['native_receipt']['identity_sha256']))

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
                self.refused(self.replay(self.FINAL, 'current-policy', copy=policy_copy, **scenario),
                             reason, detail)

    def test_real_receipts_under_the_other_declared_policy_are_incompatible(self):
        for run, policy in ((self.FINAL, 'historical-all-layer'),):
            with self.subTest(run=run, declared=policy):
                self.refused(self.replay(run, policy), 'incompatible_policy', 'differs from the declared policy')

    def resealed(self, change_manifest=None, change_body=None, run='final-current'):
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
            ({'effective_binding': {**self.MANIFEST['runs'][self.FINAL]['result_effective_configuration'],
                                    'status': 'mismatch'}}, 'unqualified',
             'process.effective_configuration does not bind'),
            ({'effective_binding': {**self.MANIFEST['runs'][self.FINAL]['result_effective_configuration'],
                                    'sha256': '2' * 64}}, 'unqualified',
             'process.effective_configuration does not bind'))
        for scenario, reason, detail in cases:
            with self.subTest(detail=detail, reason=reason):
                # The K91 review's real bytes on this run are another run's evidence: refused at the
                # binding, before the manifest is read, except where the shape already refuses.
                reason, detail = (reason, detail) if reason == 'malformed' else self.HYBRID
                record = self.refused(self.replay(self.FINAL, 'current-policy', **scenario), reason, detail)
                if reason == 'malformed':
                    # Outside the producer schema: only the file identity is retained.
                    self.assertNotIn('identity_sha256', record)
                    continue
                # The observed claim and identity are retained with the refusal.
                self.assertTrue(record['comparison_eligible'])
                self.assertEqual(record['identity_sha256'], json.loads(
                    scenario.get('sidecar') or real('final-current.routability-experiment.json'))['identity_sha256'])
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
                self.refused(self.replay(self.FINAL, 'current-policy', **scenario), *self.HYBRID)
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
                self.refused(self.replay(self.FINAL, 'current-policy', payload={rel: text}), *self.HYBRID)
                # Sealed coherently by the synthetic sidecar, the shape check refuses it.
                self.assert_refused({'name': 'complete', 'payload': {rel: text}}, 'malformed', detail)

    def test_schema_invalid_real_sidecar_members_are_malformed(self):
        # Review H2 (ee442e): members outside the producer schema, with identity untouched.
        base = json.loads(real('final-current.routability-experiment.json'))
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
                record = self.refused(self.replay(self.FINAL, 'current-policy',
                                                  sidecar=json.dumps(body, indent=2) + '\n'), 'malformed', detail)
                self.assertIsNone(record.get('identity_sha256'))

    def test_the_real_shapes_conform_to_the_restated_producer_schema(self):
        for run in self.MANIFEST['runs'].values():
            self.assertIsNone(attempt._shape_error(json.loads(real(run['sidecar'])), attempt.EXPERIMENT_SHAPE))
        for run in self.MANIFEST['retired_interim']['runs'].values():
            self.assertIsNotNone(attempt._shape_error(json.loads(real(run['sidecar'])), attempt.EXPERIMENT_SHAPE))


# The clean native receipt's own execution (agent-507 qualification-summary.json
# clean_receipt): the run directory kicad-route a0ed434 wrote and the input
# directory it was given (the route_demo fixture of the producer worktree at that
# commit). These are the execution identity the receipt is graded against; they
# are never followed on this host. The receipt holds route_demo geometry, so it is
# not committed: AGENT_EE_K91_FINAL_RECEIPT names a byte copy (default: the
# ignored build/k91-final-clean-receipt in this checkout, then the original).
K91_FINAL_RUN = '/home/halbritt/git/kicad-route/build/krt91-final-native-agent507/final-clean-receipt'
K91_FINAL_SOURCE = '/home/halbritt/.cache/agent-worktrees/kicad-route/krt91-final2-agent507/fixtures/route_demo'
K91_FINAL_PINNED = {
    'routability-experiment.json': K91_REVIEWED['native_receipt']['sidecar_sha256'],
    'experiment-policy.json': 'bbba9c43d37ecb7fa96ba3ce7cf387656f7f6cde52dd2143651150c6e982c59e',
    'attempt.json': 'eda15c237ba6431e6c9bf6a80a13ba96f3f184b4e0046081ced350bd44a19717',
    'reference/route_demo/route_demo.kicad_pcb': '1477600bf32bf50fc348651c8317f4f6772d208ea3a03ea3eaf955ff25800478',
    'candidate/route_demo/route_demo.kicad_pcb': '0a81e3e24840dddeb671f6ac5b17eb56e4622b1354458b15dda151b245494f5d',
    'work/effective-configuration-output.kicad_pcb':
        '7495a5771807d594621710bc39de7f1d274449b01504cdd9d1bd2cf627788219'}


# agent-507's protected native a0ed434 receipts (native delivery 33be1109, the
# composed experiment0 runs): --protect existing, and --protect human, which this
# input makes fall back to existing protection (no selective-human claim). Protection
# locks copper in the PREPARED input (work/input), so its board differs from the
# ORIGINAL snapshot board (agent-510 R2). Same external-artifact rules as above;
# AGENT_EE_K91_PROTECTED_<EXISTING|HUMAN> names a byte copy.
K91_PROTECTED_BUILD = '/home/halbritt/git/kicad-route/build/krt91-final-native-agent507/composed-tmp/experiment0'
K91_ORIGINAL_BOARD = '0cfd162b0677e865e0308547c671eda39f15e965ae5c4f3cdf59b0a410f1dff0'
K91_PREPARED_BOARD = '1477600bf32bf50fc348651c8317f4f6772d208ea3a03ea3eaf955ff25800478'
K91_PROTECTED = {
    'existing': {'run': f'{K91_PROTECTED_BUILD}/protected-existing',
                 'source': f'{K91_PROTECTED_BUILD}/existing-fixture', 'pinned': {
                     'routability-experiment.json':
                         'e37530a27b5f80215207fe624260bc51e515efb98494442683a759b0204a20b2',
                     'experiment-policy.json': 'bbba9c43d37ecb7fa96ba3ce7cf387656f7f6cde52dd2143651150c6e982c59e',
                     'attempt.json': 'a76fb8bfef16a278b960906b5fdeb862556f972636aa90e023362e0c98641032',
                     'reference/existing-fixture/route_demo.kicad_pcb': K91_ORIGINAL_BOARD,
                     'work/input/existing-fixture/route_demo.kicad_pcb': K91_PREPARED_BOARD,
                     'candidate/existing-fixture/route_demo.kicad_pcb':
                         'e2b57728f995ef3b3b619b48d5c14f418c1d6b0ceb9a564e0821efa533f1f541',
                     'work/effective-configuration-output.kicad_pcb':
                         '1a22beded37a68a8e2d5c32f0171802d7fb03e7c7d11a34fe1870ab685e019a3'},
                 'identity_sha256': '9a799925bf71dbc6613614ce26de64ac744f85f361a983edefba9606bb73e751'},
    'human': {'run': f'{K91_PROTECTED_BUILD}/protected-human',
              'source': f'{K91_PROTECTED_BUILD}/human-fixture', 'pinned': {
                  'routability-experiment.json': 'ea32ebf4b96a616e11f81bc893f2b72ae7f6233744312b19306474c123cc7fff',
                  'experiment-policy.json': 'bbba9c43d37ecb7fa96ba3ce7cf387656f7f6cde52dd2143651150c6e982c59e',
                  'attempt.json': '89bc71c4b6795abde0d6546264e8f1f0e633ae25b28c8b3a76fbd36039887ed0',
                  'reference/human-fixture/route_demo.kicad_pcb': K91_ORIGINAL_BOARD,
                  'work/input/human-fixture/route_demo.kicad_pcb': K91_PREPARED_BOARD,
                  'candidate/human-fixture/route_demo.kicad_pcb':
                      'c15e6ea3f95323cfc40036787fae4a2ff65b6d25a1a0f97019295e650802d030',
                  'work/effective-configuration-output.kicad_pcb':
                      '3d5914d39851da6c80fcf66c4e3f2809dc645d90dfe6d9f9e4ea5dd4c698973a'},
              'identity_sha256': 'a946d9a381fe89612f9345952c8c657695e2f26e4d88605f1ee3e53e9227f937'}}


_KEEP = object()


def disposable_copy(src, dst):
    """copytree(src, dst, symlinks=True), then the COPY made owner-writable and traversable: the
    external receipt stores are deployed read-only (:ro mounts, a-w byte copies) and copytree keeps
    their modes, so the test could not edit, reseal or remove its own copy. Only entries of dst are
    changed: a symlink is copied as a link and never chmodded or followed, so its target (the
    original, or external evidence) keeps its mode. The chmod never follows a link either: one
    swapped in after the lstat check is refused (NotImplementedError here), not followed. The
    source is never written."""
    shutil.copytree(src, dst, symlinks=True)
    # os.walk never descends through a link; a linked directory is listed and skipped below.
    entries = [Path(dst)]
    for parent, dirs, files in os.walk(dst):
        entries += [Path(parent) / name for name in dirs + files]
    for entry in entries:
        info = entry.lstat()
        if stat.S_ISLNK(info.st_mode):
            continue
        os.chmod(entry, stat.S_IMODE(info.st_mode) | (0o700 if stat.S_ISDIR(info.st_mode) else 0o600),
                 follow_symlinks=False)
    return dst


def external_receipt(variable, build_name, original):
    """An external native receipt copy, or None when it is absent."""
    for where in (os.environ.get(variable), Path(__file__).resolve().parents[1] / 'build' / build_name, original):
        if where and (Path(where) / 'routability-experiment.json').is_file():
            return Path(where)
    return None


def final_receipt():
    """The external clean receipt copy, or None when it is absent."""
    return external_receipt('AGENT_EE_K91_FINAL_RECEIPT', 'k91-final-clean-receipt', K91_FINAL_RUN)


class DisposableCopyTest(unittest.TestCase):
    def test_a_read_only_store_copy_is_writable_and_the_store_and_link_targets_are_untouched(self):
        # agent-521 fd37d2b0: the deployed K91 stores are read-only. The test's own copy must be
        # editable and removable, while the store and anything a link in it names keep their modes
        # and bytes.
        tmp = tempfile.TemporaryDirectory(prefix='agent-ee-k91-ro-')
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        store, outside = root / 'store', root / 'outside.json'
        (store / 'work/input').mkdir(parents=True)
        (store / 'work/input/rules.kicad_dru').write_text('rules')
        (store / 'snapshot.json').write_text('{}')
        outside.write_text('external')
        (store / 'external-link').symlink_to(outside)
        (store / 'dir-link').symlink_to(store / 'work')
        read_only = [outside, store / 'snapshot.json', store / 'work/input/rules.kicad_dru',
                     store / 'work/input', store / 'work', store]
        for path in read_only:
            os.chmod(path, 0o555 if path.is_dir() else 0o444)
        self.addCleanup(lambda: [os.chmod(path, 0o755) for path in reversed(read_only)])
        before = {path: (stat.S_IMODE(path.lstat().st_mode), path.read_bytes() if path.is_file() else None)
                  for path in read_only}
        copy = disposable_copy(store, root / 'copy')
        self.assertTrue((copy / 'external-link').is_symlink())
        self.assertTrue((copy / 'dir-link').is_symlink())
        (copy / 'snapshot.json').write_text('{"edited": true}')
        (copy / 'work/input/rules.kicad_dru').write_text('edited')
        (copy / 'work/input/rules.kicad_dru').unlink()
        shutil.rmtree(copy)
        self.assertEqual({path: (stat.S_IMODE(path.lstat().st_mode), path.read_bytes() if path.is_file() else None)
                          for path in read_only}, before)

    def test_a_link_swapped_in_after_the_lstat_check_is_refused_not_followed(self):
        # Review H1 (ee442-compose-stack): a copied file replaced by a link to a read-only
        # target between disposable_copy's lstat and its chmod. The chmod must not follow
        # it: the target keeps 0444, and the copy is refused, never silently made.
        tmp = tempfile.TemporaryDirectory(prefix='agent-ee-k91-swap-')
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        store, target = root / 'store', root / 'target.json'
        store.mkdir()
        (store / 'snapshot.json').write_text('{}')
        target.write_text('target')
        os.chmod(target, 0o444)
        self.addCleanup(os.chmod, target, 0o644)
        swapped, real_lstat = root / 'copy/snapshot.json', Path.lstat

        def lstat_then_swap(path, *args, **kwargs):
            info = real_lstat(path, *args, **kwargs)
            if path == swapped and not os.path.islink(swapped):
                os.unlink(swapped)
                os.symlink(target, swapped)
            return info
        with patch.object(Path, 'lstat', lstat_then_swap):
            with self.assertRaises((NotImplementedError, OSError)):
                disposable_copy(store, root / 'copy')
        self.assertTrue(swapped.is_symlink())
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o444)


class NativeReceiptCase:
    """Grading one external native receipt with the consumer's own functions (see
    FinalNativeReceiptTest): RUN and SOURCE are its execution identity, PINNED the
    digests its copy must have, located by locate()."""
    RUN, SOURCE, PINNED = K91_FINAL_RUN, K91_FINAL_SOURCE, K91_FINAL_PINNED

    def locate(self):
        return final_receipt()

    def setUp(self):
        self.source = self.locate()
        if self.source is None:
            self.skipTest('external artifact absent: agent-507 native receipt (kicad-route a0ed434); set '
                          f'the AGENT_EE_K91_* variable to a byte copy of {self.RUN}')
        for name, digest in self.PINNED.items():
            self.assertEqual(sha256(self.source / name), digest, name)
        tmp = tempfile.TemporaryDirectory(prefix='agent-ee-k91-native-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.run_copy = self.root / 'run'
        disposable_copy(self.source, self.run_copy)
        self.policy = attempt.validate_experiment_policy(json.loads((self.run_copy / 'experiment-policy.json').read_text()))

    def retained(self, run=None):
        """retain() and retain_experiment() of the run, as the adapter does: (attempt dir, observed)."""
        routing = self.root / f'routing-{len(list(self.root.glob("routing-*")))}'
        (routing / 'attempt').mkdir(parents=True)
        _, omitted, links = attempt.retain(run or self.run_copy, routing / 'attempt')
        kept, sidecar_links = attempt.retain_experiment(run or self.run_copy, routing)
        self.assertEqual((links, sidecar_links, kept), ([], [], [attempt.EXPERIMENT_SIDECAR,
                                                                 attempt.EXPERIMENT_POLICY_COPY]))
        # The prepared input is observed by digest only: none of its bytes are retained.
        self.assertFalse((routing / 'attempt/work/input').exists())
        return routing / 'attempt', omitted

    def grade(self, attempt_dir, observed, run_dir=None, source_dir=None, result=None):
        result = result or json.loads((attempt_dir / 'result.json').read_text())
        return attempt.experiment_grade({'experiment_policy': self.policy}, result, attempt_dir,
                                        {'run_dir': run_dir or self.RUN, 'source_dir': source_dir or self.SOURCE,
                                         'observed': observed})

    def assert_refused(self, record, reason, detail, attempt_dir, observed, **grade):
        self.assertEqual((record['status'], record['reason'], record['qualified']), ('refused', reason, False), record)
        self.assertIn(detail, record['detail'])
        # The same decision from the retained bytes after the run's output is removed.
        if self.run_copy.exists():
            shutil.rmtree(self.run_copy)
        self.assertEqual(self.grade(attempt_dir, observed, **grade), record)

    def resealed(self, attempt_dir, edit_attempt=None, edit_body=None, swap=None):
        """The retained attempt record and facts edited or swapped, the sidecar resealed over
        them as kicad-route would (every artifact digest and the identity seal correct), and
        result.json re-bound to the resealed sidecar."""
        for name, other in (swap or {}).items():
            shutil.copyfile(attempt_dir / other, attempt_dir / name)
        record = json.loads((attempt_dir / 'attempt.json').read_text())
        body = json.loads((attempt_dir / attempt.EXPERIMENT_SIDECAR).read_text())
        if edit_attempt is not None:
            edit_attempt(record, body)
            (attempt_dir / 'attempt.json').write_text(json.dumps(record, indent=2) + '\n')
        for name in body['artifacts']:
            if (attempt_dir / name).is_file():
                body['artifacts'][name] = sha256(attempt_dir / name)
        (edit_body or (lambda b: None))(body)
        body['identity_sha256'] = attempt._sha256_of(body['identity'])
        text = json.dumps(body, indent=2) + '\n'
        (attempt_dir / attempt.EXPERIMENT_SIDECAR).write_text(text)
        result = json.loads((attempt_dir / 'result.json').read_text())
        result['experiment']['sha256'] = hashlib.sha256(text.encode()).hexdigest()
        self.assertIsNone(attempt._sidecar_shape_refusal(body))
        return result


class FinalNativeReceiptTest(NativeReceiptCase, unittest.TestCase):
    """agent-507's clean native a0ed434 receipt with its OWN run, source, input and candidate.

    The routing adapter executes only its pinned router, which writes no experiment
    evidence and cannot issue this receipt's request (protect locked, mode selected,
    --dir the producer fixture), so this receipt cannot be an adapter execution
    without a re-pin. It is graded here by the consumer's own functions: retain()
    copies the run's diagnostics exactly as the adapter does after a run, and
    experiment_grade() grades the retained bytes against the receipt's execution
    identity (K91_FINAL_RUN, K91_FINAL_SOURCE, the design-file digests retain()
    observed). Every record is qualified: false. This qualifies the two-layer
    route_demo current-policy evidence only: not a target, not a re-pin.
    """

    def test_the_clean_receipt_is_retained_with_its_own_run_source_input_and_candidate(self):
        snapshot = json.loads((self.run_copy / 'snapshot.json').read_text())
        # Its own matching source: the reference copy the producer graded is the snapshot input.
        reference = self.run_copy / 'reference/route_demo'
        self.assertEqual({p.name: sha256(p) for p in reference.iterdir()}, snapshot['files'])
        self.assertEqual(sha256(reference / 'route_demo.kicad_pcb'),
                         sha256(self.run_copy / 'work/input/route_demo/route_demo.kicad_pcb'))
        attempt_dir, observed = self.retained()
        self.assertEqual(observed[attempt.EXPERIMENT_RAW_OUTPUT], K91_FINAL_PINNED[attempt.EXPERIMENT_RAW_OUTPUT])
        record = self.grade(attempt_dir, observed)
        body = json.loads((attempt_dir / attempt.EXPERIMENT_SIDECAR).read_text())
        self.assertEqual(record['status'], 'retained', record.get('detail'))
        self.assertEqual((record['qualified'], record['comparison_eligible'], record['sha256'], record['identity_sha256']),
                         (False, True, *K91_REVIEWED['native_receipt'].values()))
        self.assertEqual(record['policy']['policy_sha256'], body['identity']['policy']['declared_sha256'])
        self.assertEqual(record['policy']['declared']['allowed_trace_layers'], ['F.Cu', 'B.Cu'])
        # Its own candidate: the graded refilled board is the delivered candidate file.
        self.assertEqual(body['candidate']['validated_board_sha256'],
                         sha256(self.run_copy / 'candidate/route_demo/route_demo.kicad_pcb'))
        self.assertEqual(body['identity']['input']['source']['source_dir'], K91_FINAL_SOURCE)
        # The retained bytes alone re-derive the record after the run's output is gone.
        shutil.rmtree(self.run_copy)
        self.assertEqual(self.grade(attempt_dir, observed), record)

    def test_another_execution_is_refused(self):
        # H1: the same retained bytes graded as another execution's: another run directory,
        # another input directory (one that exists on this host), another observed raw output.
        attempt_dir, observed = self.retained()
        elsewhere = str(self.root / 'producer/fixtures/route_demo')
        Path(elsewhere).mkdir(parents=True)
        for grade, reason, detail in (
                ({'run_dir': K91_FINAL_RUN + '-other'}, 'run_mismatch', 'the sidecar requested output'),
                ({'source_dir': elsewhere}, 'run_mismatch', 'is not this attempt\'s input'),
                ({'source_dir': '/candidate/route_demo'}, 'run_mismatch', 'is not this attempt\'s input'),
                ({'source_dir': K91_FINAL_SOURCE + '/'}, 'run_mismatch', 'is not this attempt\'s input')):
            with self.subTest(grade=grade):
                self.assert_refused(self.grade(attempt_dir, observed, **grade), reason, detail, attempt_dir,
                                    observed, **grade)
        for raw, reason, detail in (('0' * 64, 'tampered', 'effective-configuration-output.kicad_pcb sha256 differs'),
                                    (None, 'missing', 'the adapter observed no such regular file')):
            with self.subTest(raw=raw):
                seen = {k: v for k, v in observed.items() if k != attempt.EXPERIMENT_RAW_OUTPUT}
                if raw is not None:
                    seen[attempt.EXPERIMENT_RAW_OUTPUT] = raw
                self.assert_refused(self.grade(attempt_dir, seen), reason, detail, attempt_dir, seen)

    def test_other_run_records_and_aliased_origins_are_refused_when_resealed(self):
        # H1 and H2 at this boundary: each case reseals the real sidecar over the edited
        # retained bytes, so every digest and the identity seal verify.
        cycle = "origin is a self-cycle: attempt.json source_dir is this run's own candidate"
        loop = self.root / 'loop'
        loop.symlink_to(loop)
        alias = self.root / 'alias'
        alias.symlink_to(self.run_copy / 'candidate/route_demo')

        def source(path):
            def edit(record, body):
                record['source']['source_dir'] = path
                body['identity']['input']['source']['source_dir'] = path
            return edit

        def other_input(record, body):
            record['input_fingerprint'] = body['identity']['input']['fingerprint'] = '1' * 64
            body['identity']['origin']['root_input_fingerprint'] = '1' * 64
        cases = (
            ({'edit_attempt': source(f'{K91_FINAL_RUN}/hop/../candidate/route_demo')}, 'unqualified', cycle),
            ({'edit_attempt': source(f'{K91_FINAL_RUN}/candidate/route_demo')}, 'unqualified', cycle),
            ({'edit_attempt': source(f'{K91_FINAL_RUN}//reference/./route_demo/')}, 'unqualified', cycle),
            ({'edit_attempt': source(str(alias))}, 'run_mismatch', "is not this attempt's input"),
            ({'edit_attempt': source(str(loop))}, 'run_mismatch', "is not this attempt's input"),
            ({'edit_attempt': source(f'{K91_FINAL_RUN}/loop')}, 'unqualified', cycle),
            # Another run's input, facts and candidate under the same policy.
            ({'edit_attempt': other_input}, 'run_mismatch', 'the sidecar input fingerprint'),
            ({'swap': {'facts/reference-facts.json': 'validate/candidate-facts.json'}}, 'run_mismatch',
             'the sidecar input board'),
            ({'swap': {'validate/candidate-facts.json': 'facts/reference-facts.json'}}, 'run_mismatch',
             'the sidecar graded refilled board'),
            ({'edit_body': lambda b: b['candidate'].update(validated_board_sha256='2' * 64)}, 'run_mismatch',
             'the sidecar graded refilled board'),
            ({'edit_body': lambda b: b['candidate'].update(raw_backend_output_board_sha256='2' * 64)}, 'run_mismatch',
             'the sidecar raw backend output board'),
            ({'edit_body': lambda b: b['identity']['input']['project'].update(name='other.kicad_pro')},
             'run_mismatch', 'the sidecar input project'),
            ({'edit_body': lambda b: b['identity']['input']['rules'].update(sha256='3' * 64)}, 'run_mismatch',
             'the sidecar input rules'))
        for scenario, reason, detail in cases:
            with self.subTest(detail=detail, scenario=sorted(scenario)):
                if not self.run_copy.exists():
                    disposable_copy(self.source, self.run_copy)
                attempt_dir, observed = self.retained()
                result = self.resealed(attempt_dir, **scenario)
                self.assert_refused(self.grade(attempt_dir, observed, result=result), reason, detail, attempt_dir,
                                    observed, result=result)

    def test_a_false_rules_inventory_or_prepared_input_is_refused(self):
        # agent-510 R1 on the real receipt, whose snapshot and effective input hold
        # route_demo.kicad_dru: the rules declared absent, or an invented not-loaded file,
        # in BOTH the attempt record and the resealed sidecar. And R2's observation: the
        # prepared input the adapter observed is not the one the manifests captured.
        absent = 'the adapter observed no regular prepared input board and project in work/input'

        def rules(custom=_KEEP, not_loaded=_KEEP, attempt_too=True):
            def edit(record, body):
                for target, name in ((record['rules_files'], 'custom_rules'), (body['identity']['input']['rules'], 'name')):
                    if target is record['rules_files'] and not attempt_too:
                        continue
                    if custom is not _KEEP:
                        target[name] = custom
                    if not_loaded is not _KEEP:
                        target['not_loaded'] = not_loaded
                if custom is None:
                    body['identity']['input']['rules']['sha256'] = None
            return edit

        def reference_dir(record, body):
            record['reference_dir'] = 'reference/..'
        for edit, detail in ((rules(custom=None), 'the sidecar input rules file'),
                             (rules(not_loaded=['invented.kicad_dru']), 'the sidecar input rules file'),
                             (rules(not_loaded=['invented.kicad_dru'], attempt_too=False), 'the sidecar input rules'),
                             (reference_dir, absent)):
            with self.subTest(detail=detail):
                if not self.run_copy.exists():
                    disposable_copy(self.source, self.run_copy)
                attempt_dir, observed = self.retained()
                result = self.resealed(attempt_dir, edit_attempt=edit)
                self.assert_refused(self.grade(attempt_dir, observed, result=result), 'run_mismatch', detail,
                                    attempt_dir, observed, result=result)
        if not self.run_copy.exists():
            disposable_copy(self.source, self.run_copy)
        attempt_dir, observed = self.retained()
        board = 'work/input/route_demo/route_demo.kicad_pcb'
        self.assertEqual(observed[board], K91_PREPARED_BOARD)
        for seen, detail in (({**observed, board: '4' * 64}, 'the sidecar effective input'),
                             ({**observed, 'work/input/route_demo/route_demo.kicad_dru': '4' * 64},
                              'the sidecar effective input'),
                             ({k: v for k, v in observed.items() if not k.startswith('work/input/')}, absent)):
            with self.subTest(detail=detail, observed=sorted(set(seen.items()) - set(observed.items()))):
                self.assert_refused(self.grade(attempt_dir, seen), 'run_mismatch', detail, attempt_dir, seen)

    def test_an_optional_prepared_rules_link_in_a_resealed_copy_is_refused(self):
        # agent-510 R4 on the real receipt: a private copy whose prepared
        # route_demo.kicad_dru is removed, or replaced by a link to the known original
        # rules file or by a dangling link, with both manifests, the effective identity
        # and the binding resealed to claim it null. These are resealed adversarial
        # copies graded by the consumer's helpers, not new native executions. Only the
        # actually absent file is that input; kicad-route a0ed434's input guard rejects
        # both links, and retain() records their path type without following them.
        name = 'work/input/route_demo/route_demo.kicad_dru'
        config_name, request_name = attempt.EXPERIMENT_MANIFESTS

        def null_rules(attempt_dir):
            for manifest in (request_name, config_name):
                doc = json.loads((attempt_dir / manifest).read_text())
                doc['input']['rules']['.kicad_dru'] = None
                if manifest == config_name:
                    doc['request_sha256'] = sha256(attempt_dir / request_name)
                (attempt_dir / manifest).write_text(json.dumps(doc, indent=2) + '\n')
            result = self.resealed(attempt_dir, edit_body=lambda b: b['identity']['effective']['input']['rules']
                                   .update({'.kicad_dru': None}))
            result['process']['effective_configuration'].update(
                sha256=sha256(attempt_dir / config_name), request_sha256=sha256(attempt_dir / request_name))
            return result
        for entry in (None, self.RUN + '/' + name, 'missing.kicad_dru'):
            with self.subTest(entry=entry):
                if not self.run_copy.exists():
                    disposable_copy(self.source, self.run_copy)
                (self.run_copy / name).unlink()
                if entry is not None:
                    (self.run_copy / name).symlink_to(entry)
                attempt_dir, observed = self.retained()
                result = null_rules(attempt_dir)
                record = self.grade(attempt_dir, observed, result=result)
                if entry is None:
                    self.assertNotIn(name, observed)
                    self.assertEqual((record['status'], record['qualified']), ('retained', False), record.get('detail'))
                    shutil.rmtree(self.run_copy)
                    self.assertEqual(self.grade(attempt_dir, observed, result=result), record)
                    continue
                self.assert_refused(record, 'run_mismatch', f'the prepared {name} is a link, neither absent nor a '
                                    'regular file', attempt_dir, observed, result=result)
                self.assertEqual(observed[name], 'link')

    def test_a_predecessor_candidate_input_is_refused_before_staging(self):
        # Review H2 (Sol, ee442d2): the real owning run's candidate, given as the design,
        # is a predecessor input (kicad-route cli.previous_step: its attempt.json
        # candidate_dir is this input). Staging would erase that ancestry and restage it
        # at depth 0, so step/run_step refuse it before anything runs or is staged; so
        # are a candidate whose owner is missing or names another candidate. The same
        # tree at a neutral path is an original and runs.
        boundary = ExperimentBoundaryTest('setUp')
        boundary.setUp()
        self.addCleanup(boundary.doCleanups)
        record = json.loads((self.run_copy / 'attempt.json').read_text())
        project = PurePosixPath(record['candidate_dir']).name
        self.assertEqual(PurePosixPath(record['candidate_dir']).parent.name, 'candidate')
        genuine = self.root / 'owning-run'
        disposable_copy(self.run_copy, genuine)
        self.assertEqual((genuine / record['candidate_dir']).resolve(), (genuine / 'candidate' / project).resolve())
        orphan = self.root / 'orphan/candidate'
        disposable_copy(genuine / 'candidate', orphan)
        other = self.root / 'other-owner'
        disposable_copy(self.run_copy, other)
        (other / 'attempt.json').write_text(json.dumps({**record, 'candidate_dir': 'candidate/elsewhere'}))
        neutral = self.root / 'neutral/input'
        disposable_copy(genuine / 'candidate', neutral)
        request = dict(project=project, board=record['board'], schematic=record['schematic'], name=project)
        for label, design, detail in (
                ('genuine owning run', genuine / 'candidate', 'is the candidate of the kicad-route run'),
                ('missing owner', orphan, 'without a readable owning run'),
                ('mismatched owner', other / 'candidate', 'without a readable owning run')):
            with self.subTest(label):
                root = self.root / f'consumer-{label.replace(" ", "-")}'
                spec = boundary.routing_step(root, {'name': 'complete'}, self.policy, **request)
                receipt_dir = root / 'receipts/1-kicad-route-builtin'
                with self.assertRaisesRegex(ValueError, detail):
                    stage_receipts.run_step(receipt_dir, design, spec, {'implementation': 1, 'receipt_index': 1})
                self.assertFalse(os.path.lexists(receipt_dir))
        # Without a declared experiment policy no origin evidence is consumed: a run's own
        # candidate or one beside a run record still refuses, a directory that is only
        # named candidate does not.
        for label, design, policy in (('genuine owning run, no policy', genuine / 'candidate', None),
                                      ('mismatched owner, no policy', other / 'candidate', None)):
            with self.subTest(label):
                root = self.root / f'consumer-{label.replace(" ", "-").replace(",", "")}'
                spec = boundary.routing_step(root, {'name': 'complete'}, policy, **request)
                with self.assertRaisesRegex(ValueError, 'predecessor'):
                    stage_receipts.run_step(root / 'receipts/1-kicad-route-builtin', design, spec,
                                            {'implementation': 1, 'receipt_index': 1})
        for label, design, policy in (('neutral original', neutral, self.policy),
                                      ('directory named candidate, no policy', orphan, None)):
            with self.subTest(label):
                root = self.root / f'consumer-{label.replace(" ", "-").replace(",", "")}'
                spec = boundary.routing_step(root, {'name': 'complete'}, policy, **request)
                receipt = stage_receipts.run_step(root / 'receipts/1-kicad-route-builtin', design, spec,
                                                  {'implementation': 1, 'receipt_index': 1})
                self.assertIsNotNone(receipt['execution'])

    def test_a_capture_run_inputs_layout_is_refused_and_its_neutral_copy_runs(self):
        # ee442 composition: an agent-ee capture run (runs/N holding result.json, candidate.json
        # and inputs/<project>) puts a run record two levels above inputs/<project>, the layout
        # kicad-route bd1e633 experiment._origin reads as a missing predecessor. The adapter
        # cannot tell whose run record it is, so routing straight from runs/N/inputs refuses,
        # with and without a declared policy, before any receipt exists; the same tree copied
        # to a neutral path is an original and runs.
        boundary = ExperimentBoundaryTest('setUp')
        boundary.setUp()
        self.addCleanup(boundary.doCleanups)
        record = json.loads((self.run_copy / 'attempt.json').read_text())
        project = PurePosixPath(record['candidate_dir']).name
        capture = self.root / 'campaign/runs/2'
        disposable_copy(self.run_copy / 'candidate' / project, capture / 'inputs' / project)
        (capture / 'result.json').write_text(json.dumps({'candidate_id': 'captured', 'findings': []}))
        (capture / 'candidate.json').write_text(json.dumps({'candidate_id': 'captured', 'files': {}}))
        neutral = self.root / 'neutral-capture'
        disposable_copy(capture / 'inputs', neutral)
        request = dict(project=project, board=record['board'], schematic=record['schematic'], name=project)
        for label, policy in (('capture run inputs', self.policy), ('capture run inputs, no policy', None)):
            with self.subTest(label):
                root = self.root / f'consumer-{label.replace(" ", "-").replace(",", "")}'
                spec = boundary.routing_step(root, {'name': 'complete'}, policy, **request)
                receipt_dir = root / 'receipts/1-kicad-route-builtin'
                with self.assertRaisesRegex(ValueError, 'without a readable owning run'):
                    stage_receipts.run_step(receipt_dir, capture / 'inputs', spec,
                                            {'implementation': 1, 'receipt_index': 1})
                self.assertFalse(os.path.lexists(receipt_dir))
        for label, policy in (('neutral copy', self.policy), ('neutral copy, no policy', None)):
            with self.subTest(label):
                root = self.root / f'consumer-{label.replace(" ", "-").replace(",", "")}'
                spec = boundary.routing_step(root, {'name': 'complete'}, policy, **request)
                receipt = stage_receipts.run_step(root / 'receipts/1-kicad-route-builtin', neutral, spec,
                                                  {'implementation': 1, 'receipt_index': 1})
                self.assertIsNotNone(receipt['execution'])

    def test_the_real_receipt_as_an_adapter_execution_is_refused_and_not_adopted(self):
        # The same receipt delivered by a router the adapter ran is not that execution: its
        # request, input directory and run are the producer's. Through step, run_step,
        # verify and adopt it is refused, also after OUT removal (RealReceiptReplayTest
        # replays its committed sidecar and manifests the same way).
        boundary = ExperimentBoundaryTest('setUp')
        boundary.setUp()
        self.addCleanup(boundary.doCleanups)
        spec = json.loads((REAL / 'receipts.json').read_text())['runs']['final-current']
        body = json.loads((self.run_copy / attempt.EXPERIMENT_SIDECAR).read_text())
        # Every file the sidecar seals, and its snapshot, as the router's run: every digest
        # binds, so what refuses is the execution the receipt describes.
        payload = {name: (self.run_copy / name).read_text() for name in [*body['artifacts'], 'snapshot.json']
                   if name != attempt.EXPERIMENT_POLICY_COPY}
        outcome = boundary.receipt({'name': 'complete', 'payload': payload,
                                    'effective_binding': spec['result_effective_configuration'],
                                    'replay': {'sidecar': (self.run_copy / attempt.EXPERIMENT_SIDECAR).read_text(),
                                               'copy': (self.run_copy / attempt.EXPERIMENT_POLICY_COPY).read_text()}},
                                   self.policy)
        record = RealReceiptReplayTest.refused(boundary, outcome, 'run_mismatch',
                                               f"attempt.json source_dir '{K91_FINAL_SOURCE}' is not this attempt's "
                                               'input /work/routing/agent-ee-original/project')
        self.assertEqual(record['identity_sha256'], K91_REVIEWED['native_receipt']['identity_sha256'])
        self.assertEqual(outcome[3]['omitted_design_files'][attempt.EXPERIMENT_RAW_OUTPUT],
                         K91_FINAL_PINNED[attempt.EXPERIMENT_RAW_OUTPUT])


class ProtectedNativeReceiptCase(NativeReceiptCase):
    """A protected native a0ed434 receipt (K91_PROTECTED) with its OWN run, source,
    ORIGINAL input (the snapshot) and PREPARED input (the protected work input), graded
    as FinalNativeReceiptTest grades the clean one. Helper evidence only, like it: the
    adapter's pinned router cannot issue this request. No selective-human claim."""
    KIND = None

    def setUp(self):
        spec = K91_PROTECTED[self.KIND]
        self.RUN, self.SOURCE, self.PINNED = spec['run'], spec['source'], spec['pinned']
        self.project = Path(self.SOURCE).name
        super().setUp()

    def locate(self):
        return external_receipt(f'AGENT_EE_K91_PROTECTED_{self.KIND.upper()}', f'k91-protected-{self.KIND}',
                                K91_PROTECTED[self.KIND]['run'])

    def test_the_prepared_protected_input_is_retained_distinct_from_the_original(self):
        snapshot = json.loads((self.run_copy / 'snapshot.json').read_text())
        self.assertEqual(snapshot['files']['route_demo.kicad_pcb'], K91_ORIGINAL_BOARD)
        attempt_dir, observed = self.retained()
        self.assertEqual(observed[f'work/input/{self.project}/route_demo.kicad_pcb'], K91_PREPARED_BOARD)
        record = self.grade(attempt_dir, observed)
        self.assertEqual(record['status'], 'retained', record.get('detail'))
        self.assertEqual((record['qualified'], record['comparison_eligible'], record['sha256'], record['identity_sha256']),
                         (False, True, self.PINNED[attempt.EXPERIMENT_SIDECAR],
                          K91_PROTECTED[self.KIND]['identity_sha256']))
        body = json.loads((attempt_dir / attempt.EXPERIMENT_SIDECAR).read_text())
        result = json.loads((attempt_dir / 'result.json').read_text())
        # Two distinct input stages, each bound to its own record.
        self.assertEqual(body['identity']['input']['board']['sha256'], K91_ORIGINAL_BOARD)
        self.assertEqual(body['identity']['effective']['input']['board_sha256'], K91_PREPARED_BOARD)
        self.assertEqual(result['process']['effective_configuration']['captured_input_sha256'], K91_PREPARED_BOARD)
        self.assertEqual(body['identity']['protection']['mode'], 'existing')
        self.assertEqual(body['candidate']['validated_board_sha256'],
                         sha256(self.run_copy / f'candidate/{self.project}/route_demo.kicad_pcb'))
        shutil.rmtree(self.run_copy)
        self.assertEqual(self.grade(attempt_dir, observed), record)

    def test_the_original_stage_claimed_for_the_prepared_input_is_refused(self):
        # The stage conflation as a self-consistent forgery: both manifests, the sealed
        # effective identity and the result.json binding claim the ORIGINAL input, every
        # digest and seal resealed, while the adapter observed the protected work input.
        attempt_dir, observed = self.retained()
        original = {'board_sha256': K91_ORIGINAL_BOARD,
                    'rules': json.loads((attempt_dir / attempt.EXPERIMENT_MANIFESTS[0]).read_text())['input']['rules']}
        config_name, request_name = attempt.EXPERIMENT_MANIFESTS
        request = json.loads((attempt_dir / request_name).read_text())
        request['input'] = original
        (attempt_dir / request_name).write_text(json.dumps(request, indent=2) + '\n')
        config = json.loads((attempt_dir / config_name).read_text())
        config.update(input=original, request_sha256=sha256(attempt_dir / request_name))
        (attempt_dir / config_name).write_text(json.dumps(config, indent=2) + '\n')
        result = self.resealed(attempt_dir, edit_body=lambda b: b['identity']['effective'].update(input=original))
        result['process']['effective_configuration'].update(
            sha256=sha256(attempt_dir / config_name), request_sha256=sha256(attempt_dir / request_name),
            captured_input_sha256=K91_ORIGINAL_BOARD)
        self.assert_refused(self.grade(attempt_dir, observed, result=result), 'run_mismatch',
                            'the sidecar effective input', attempt_dir, observed, result=result)
        # The unchanged receipt with an observed work input that is the original board.
        disposable_copy(self.source, self.run_copy)
        attempt_dir, observed = self.retained()
        seen = {**observed, f'work/input/{self.project}/route_demo.kicad_pcb': K91_ORIGINAL_BOARD}
        self.assert_refused(self.grade(attempt_dir, seen), 'run_mismatch', 'the sidecar effective input',
                            attempt_dir, seen)


class ProtectedExistingReceiptTest(ProtectedNativeReceiptCase, unittest.TestCase):
    KIND = 'existing'


class ProtectedHumanFallbackReceiptTest(ProtectedNativeReceiptCase, unittest.TestCase):
    KIND = 'human'


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
