"""Versioned verdict classifiers of the AGENTEE-442 native K91 harness (review ee442-native-positive-b, H2).

The frozen harness (scripts/k91_native_positive.py, 553e4151) and its one native
record (evidence/k91-native-positive) stay byte-identical; CLASSIFIER_V1 restates
its verdict and must reproduce the recorded booleans. CLASSIFIER_V2
(scripts/k91_classify.py, used by scripts/k91_native_positive_v2.py) counts the
actual consumer's coherent not_run return of the mismatched-runtime control as a
refusal, keeps the candidate-board and sidecar tamper controls apart, and leaves
an absent candidate-board control incomplete. No router runs here: the controls
are exercised through step/run_step/verify/adopt on the synthetic fixture router.
"""
import copy
import hashlib
import importlib.util
import json
import shutil
import tempfile
import types
import unittest
from pathlib import Path

from agent_ee import stage_receipts
from tests import test_routing_experiment_boundary as boundary
from tests.toolchain import require_toolchain

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'evidence/k91-native-positive'
# The frozen harness as it ran (evidence-manifest.json harness_sha256).
V1_HARNESS_SHA256 = '97d6055d148ef40ae110a09ab6c906f28d0ceea65afc4949f44ca531242e600f'


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f'scripts/{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


classify = load('k91_classify')


def historical():
    return json.loads((EVIDENCE / 'evidence-manifest.json').read_text())


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class HistoricalRecordTest(unittest.TestCase):
    def test_the_frozen_harness_and_its_record_are_unchanged(self):
        manifest = historical()
        self.assertEqual(sha256(ROOT / 'scripts/k91_native_positive.py'), V1_HARNESS_SHA256)
        self.assertEqual(manifest['harness_sha256'], V1_HARNESS_SHA256)
        for line in (EVIDENCE / 'SHA256SUMS').read_text().splitlines():
            digest, name = line.split('  ', 1)
            with self.subTest(name):
                self.assertEqual(sha256(EVIDENCE / name), digest)

    def test_v1_reproduces_the_recorded_verdict(self):
        manifest = historical()
        self.assertEqual(classify.v1_verdict(manifest), manifest['verdict'])
        self.assertEqual(manifest['verdict'], {'positive_retained_and_adopted': False, 'controls_refused': False})

    def test_the_reviewers_mismatch_return_is_a_refusal_under_v2_only(self):
        # The actual consumer's return for the mismatched-runtime control: the reviewer's replay.py
        # re-ran verify() on the retained receipt and asserted it equals this recorded value.
        record = historical()['controls']['a_mismatched_runtime']
        value = record['verify']['value']
        self.assertTrue(record['verify']['accepted'])
        self.assertEqual((value['classification'], value['proposal'], value['router_exit'], value['candidate_sha256']),
                         ('not_run', 'none', None, None))
        self.assertFalse(record['adopt']['accepted'])
        # v1 counts the non-raising verify as accepted, so its controls stay false.
        self.assertFalse(classify.v1_verdict(historical())['controls_refused'])
        # v2: a coherent verified not_run with failed adoption, host refusal and runtime reasons is a refusal.
        self.assertTrue(classify.verify_refused(record['verify']))
        self.assertEqual(classify.runtime_state(record), classify.REFUSED)
        self.assertIn('owned engine source digest differs', record['host_step']['error'])
        self.assertTrue(any('owned engine source digest differs' in r for r in record['sandbox']['reasons']))

    def test_v2_on_the_historical_records(self):
        verdict = classify.v2_verdict(classify.v1_controls_as_v2(historical()))
        self.assertEqual(verdict['controls'], {'a_mismatched_runtime': classify.REFUSED,
                                               'b_tampered_candidate': classify.INCOMPLETE,
                                               'b_tampered_sidecar': classify.REFUSED,
                                               'c_stale_input': classify.REFUSED})
        # No candidate was delivered: the candidate-board control is incomplete, so not all refused.
        self.assertFalse(verdict['controls_refused'])
        self.assertFalse(verdict['positive_accepted'])


class ClassifierRulesTest(unittest.TestCase):
    def setUp(self):
        self.runtime = historical()['controls']['a_mismatched_runtime']

    def test_any_departure_from_a_coherent_not_run_is_not_a_refusal(self):
        cases = {'classification': 'complete', 'proposal': 'eligible', 'router_exit': 0, 'candidate_sha256': 'a' * 64}
        for key, value in cases.items():
            with self.subTest(key):
                record = copy.deepcopy(self.runtime)
                record['verify']['value'][key] = value
                self.assertFalse(classify.verify_refused(record['verify']))
                self.assertEqual(classify.runtime_state(record), classify.NOT_REFUSED)

    def test_a_refusal_needs_failed_adoption_host_refusal_and_sandbox_reasons(self):
        edits = {'adopted': lambda r: r.update(adopt={'accepted': True, 'value': None}),
                 'host accepted': lambda r: r.update(host_step={'accepted': True}),
                 'sandbox ran': lambda r: r['sandbox'].update(classification='complete'),
                 'no reasons': lambda r: r['sandbox'].update(reasons=[]),
                 'sandbox candidate': lambda r: r['sandbox'].update(candidate={'sha256': 'a' * 64}),
                 'bare adoption': lambda r: r.update(direct_adopt={'accepted': True, 'value': None})}
        for label, edit in edits.items():
            with self.subTest(label):
                record = copy.deepcopy(self.runtime)
                edit(record)
                self.assertEqual(classify.runtime_state(record), classify.NOT_REFUSED)

    def test_raising_verify_is_a_refusal_and_absent_controls_are_incomplete(self):
        raised = {'verify': {'accepted': False, 'error': 'ValueError: x'}, 'adopt': {'accepted': False, 'error': 'y'}}
        self.assertEqual(classify.control_state(raised), classify.REFUSED)
        self.assertEqual(classify.control_state({**raised, 'adopt': {'accepted': True, 'value': None}}),
                         classify.NOT_REFUSED)
        self.assertEqual(classify.control_state({'skipped': 'absent'}), classify.INCOMPLETE)
        self.assertEqual(classify.control_state(None), classify.INCOMPLETE)
        self.assertEqual(classify.runtime_state(None), classify.INCOMPLETE)

    def test_the_positive_needs_a_retained_record_and_an_adopted_candidate(self):
        value = {'classification': 'complete', 'proposal': 'eligible', 'candidate_sha256': 'b' * 64,
                 'experiment': {'status': 'retained', 'qualified': False}}
        good = {'verify': {'accepted': True, 'value': value}, 'adopt': {'accepted': True, 'value': None},
                'design_changed': ['project/rc_filter.kicad_pcb']}
        self.assertTrue(classify.positive_accepted(good))
        edits = {'not adopted': lambda p: p.update(adopt={'accepted': False, 'error': 'x'}),
                 'nothing changed': lambda p: p.update(design_changed=[]),
                 'no candidate': lambda p: p['verify']['value'].update(candidate_sha256=None),
                 'refused record': lambda p: p['verify']['value']['experiment'].update(status='refused'),
                 'qualified': lambda p: p['verify']['value']['experiment'].update(qualified=True),
                 'verify raised': lambda p: p.update(verify={'accepted': False, 'error': 'x'})}
        for label, edit in edits.items():
            with self.subTest(label):
                positive = copy.deepcopy(good)
                edit(positive)
                self.assertFalse(classify.positive_accepted(positive))
        self.assertFalse(classify.positive_accepted({}))


class SuccessorControlsTest(unittest.TestCase):
    """The v2 tamper and stale controls through the actual consumer, on a synthetic delivered candidate."""

    receipt = boundary.ExperimentBoundaryTest.receipt

    def setUp(self):
        require_toolchain()
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-k91-classifier-')
        self.addCleanup(self.tmp.cleanup)
        self.count = 0
        self.v2 = load('k91_native_positive_v2')

    def test_candidate_and_sidecar_tampers_are_separate_refusals(self):
        receipt, receipt_dir, design, _ = self.receipt({'name': 'complete'}, boundary.CURRENT)
        self.assertEqual(receipt['status'], 'completed')
        args = types.SimpleNamespace(project_dir=str(design / 'project'), requirements_dir=str(design / 'requirements'))
        root = Path(self.tmp.name)
        target = next(iter(receipt['spec']['writes'].values()))
        records = {}
        for label, rel in (('candidate', target), ('sidecar', 'routing/attempt/routability-experiment.json')):
            records[label] = self.v2.tamper(receipt_dir, rel, receipt, self.pin, root / f'tamper-{label}', args)
            with self.subTest(label):
                self.assertNotEqual(records[label]['sha256_before'], records[label]['sha256_after'])
                self.assertEqual(classify.control_state(records[label]), classify.REFUSED, records[label])
                self.assertEqual(records[label]['adopt'], {'accepted': False, 'error': 'not attempted: verify refused'})
        # The bare stage adoption checks the declared outputs only: it refuses the tampered
        # candidate board and accepts the tampered sidecar, which only verify() refuses.
        self.assertFalse(records['candidate']['direct_adopt']['accepted'])
        self.assertTrue(records['sidecar']['direct_adopt']['accepted'])
        self.assertIn('candidate', records['candidate']['file'])
        # The untouched receipt still adopts: the refusals are the tampered bytes, not the receipt.
        stage_receipts.adopt(receipt_dir, receipt, design)

    def test_an_absent_candidate_board_is_incomplete(self):
        receipt, receipt_dir, design, _ = self.receipt({'name': 'complete'}, boundary.CURRENT)
        args = types.SimpleNamespace(project_dir=str(design / 'project'), requirements_dir=str(design / 'requirements'))
        record = self.v2.tamper(receipt_dir, 'routing/candidate/project/absent.kicad_pcb', receipt, self.pin,
                                Path(self.tmp.name) / 'absent', args)
        self.assertIn('skipped', record)
        self.assertEqual(classify.control_state(record), classify.INCOMPLETE)


class SuccessorCommandLineTest(unittest.TestCase):
    def setUp(self):
        self.v2 = load('k91_native_positive_v2')
        self.argv = ['--checkout', 'L', '--frozen-source', 'F', '--mismatch', 'M', '--out', 'O', '--commit', 'c',
                     '--source-sha256', 's', '--extension-sha256', 'e', '--packages-sha256', 'p',
                     '--python-version', '3.12.3', '--input-tree-sha256', 't', '--board-sha256', 'b',
                     '--rule-areas-sha256', 'r']

    def test_limits_default_to_the_authorized_bounds_and_are_ordered(self):
        args = self.v2.parse(self.argv)
        self.assertEqual((args.router_limit_s, args.total_limit_s, args.budget), (900, 2400, 300))
        for extra in (['--budget', '900'], ['--router-limit-s', '2500'], ['--budget', '0']):
            with self.subTest(extra), self.assertRaises(SystemExit):
                self.v2.parse(self.argv + extra)
        for required in ('--input-tree-sha256', '--board-sha256', '--packages-sha256', '--python-version'):
            with self.subTest(required), self.assertRaises(SystemExit):
                i = self.argv.index(required)
                self.v2.parse(self.argv[:i] + self.argv[i + 2:])

    def test_runtime_and_input_identities_are_exact(self):
        args = self.v2.parse(self.argv)
        found = {'packages_sha256': 'p', 'python_version': '3.12.3'}
        self.assertEqual(self.v2.runtime_mismatch(args, found, {}), {})
        self.assertIn('python_version', self.v2.runtime_mismatch(args, {**found, 'python_version': '3.12.4'}, {}))
        record = {'sha256': 't', 'files': {'project/rc_filter.kicad_pcb': 'b'}}
        self.assertEqual(self.v2.input_mismatch(args, record, 'b'), {})
        self.assertEqual(set(self.v2.input_mismatch(args, {**record, 'sha256': 'u'}, 'b')), {'input_tree_sha256'})
        self.assertEqual(set(self.v2.input_mismatch(args, record, 'x')), {'board_sha256'})


if __name__ == '__main__':
    unittest.main()
