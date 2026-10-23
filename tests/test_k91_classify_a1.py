"""Amendment A1 to the K91 v2 classifier (scripts/k91_classify_a1.py): the five regression groups of the
"Control A remedy proposal" (reviews/ee442-native-positive-c-sol.md; agent-521 ruling f14c71ec).

The records are the sealed positive-c bd1 evidence (evidence/k91-native-positive-c/bd1), read only; every
mutation is made on a deep copy. Nothing here routes, runs a step, verifies, adopts or writes a record.
"""
import copy
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import k91_classify as v2  # noqa: E402
import k91_classify_a1 as a1  # noqa: E402

EVIDENCE = ROOT / 'evidence/k91-native-positive-c'
BD1 = EVIDENCE / 'bd1'
MANIFEST = BD1 / 'out/evidence-manifest.json'
RECEIPT = BD1 / 'out/positive/1/receipts/1-kicad-route-builtin/receipt.json'
A = 'a_mismatched_runtime'
# Identities of the sealed originals, as recorded in the positive-c result (sha256sum output).
CLASSIFIER_V2_SHA256 = '55563ea17c7e4ba725219717904a95beb0a5814676558f5690c0500f102dd706'
MANIFEST_SHA256 = '8acd9dff0c2ccfc3f860c8d28333b581fdbe6e357ab8a543355aac062d12ce53'
RECEIPT_SHA256 = 'e78941b30b2c1f305cfff7ae4fbc9cc526f1172f6a7461f0bfc5295970adccb0'


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load():
    manifest = json.loads(MANIFEST.read_text())
    captured = json.loads((BD1 / 'preflight.json').read_text())['mismatch_M']
    return manifest, a1.identities_of(manifest, captured)


def state(manifest, identities, record=None):
    controls = dict(manifest['controls'])
    if record is not None:
        controls[A] = record
    return a1.v2a1_verdict({**manifest, 'controls': controls}, identities)


def set_message(observation, message):
    observation['exception'][0]['message'] = message
    observation['error'] = f"{observation['exception'][0]['type']}: {message}"


class Base(unittest.TestCase):
    def setUp(self):
        self.manifest, self.ids = load()
        self.record = copy.deepcopy(self.manifest['controls'][A])

    def a_state(self, record, identities='default'):
        return state(self.manifest, self.ids if identities == 'default' else identities, record)['controls'][A]

    def incomplete(self, record, identities='default'):
        self.assertEqual(self.a_state(record, identities), v2.INCOMPLETE)


class Group1RetainedRecognized(Base):
    """1. The retained A observation and its host binding are refused under the NEW contract only."""

    def test_retained_a_refused_under_a1(self):
        verdict = state(self.manifest, self.ids)
        self.assertEqual(verdict['classifier'], a1.CLASSIFIER_V2A1)
        self.assertEqual(verdict['controls'][A], v2.REFUSED)
        self.assertEqual(a1.wrong_head_problems(self.record, self.ids), [])

    def test_retained_a_still_incomplete_under_verdict_2r4(self):
        self.assertEqual(v2.v2_verdict(self.manifest)['controls'][A], v2.INCOMPLETE)
        self.assertNotEqual(a1.CLASSIFIER_V2A1, v2.CLASSIFIER_V2)

    def test_observed_shape(self):
        root, host = self.record['consumer']['exception'], self.record['host_binding']['exception']
        for chain, path in ((root, a1.CONSUMER_PATH), (host, a1.HOST_PATH)):
            self.assertEqual(len(chain), 1)
            self.assertEqual((chain[0]['type'], chain[0]['message'], chain[0]['link']), ('ValueError', a1.WRONG_HEAD_MESSAGE, None))
            self.assertEqual((chain[0]['origin']['file'], chain[0]['origin']['function'], chain[0]['origin']['line']),
                             ('routing_adapter.py', 'validate_campaign_step', 228))
            self.assertTrue(v2._in_order(chain[0]['frames'], path))
        for layer in ('consumer_run_step', 'consumer_verify', 'consumer_adopt'):
            self.assertTrue(v2.skipped(self.record[layer]))
        self.assertEqual(self.record['design_before'], self.record['design_after'])
        self.assertNotEqual(self.record['consumer_design'], self.record['direct_design'])
        self.assertEqual(self.ids, {'proposed_commit': a1.PROPOSED_COMMIT, 'mismatch_commit': a1.MISMATCH_COMMIT})
        self.assertNotEqual(self.ids['proposed_commit'], self.ids['mismatch_commit'])


class Group2MalformedStaysIncomplete(Base):
    """2. Wrong type/origin, frames, chains, outer exceptions, fields and embedded reasons stay incomplete."""

    def each(self, key):
        return self.record[key]['exception'][0]

    def test_wrong_type(self):
        for key in ('consumer', 'host_binding'):
            r = copy.deepcopy(self.record)
            r[key]['exception'][0]['type'] = 'RuntimeError'
            r[key]['error'] = f'RuntimeError: {a1.WRONG_HEAD_MESSAGE}'
            self.incomplete(r)

    def test_wrong_origin(self):
        for key in ('consumer', 'host_binding'):
            for field, value in (('file', 'campaign.py'), ('function', 'preflight')):
                r = copy.deepcopy(self.record)
                r[key]['exception'][0]['origin'][field] = value
                self.incomplete(r)

    def test_missing_or_reordered_frames(self):
        for key, path in (('consumer', a1.CONSUMER_PATH), ('host_binding', a1.HOST_PATH)):
            for frame in path[:-1]:
                r = copy.deepcopy(self.record)
                r[key]['exception'][0]['frames'].remove(frame)
                self.incomplete(r)
            r = copy.deepcopy(self.record)
            frames = r[key]['exception'][0]['frames']
            i, j = frames.index(path[0]), frames.index(path[-1])
            frames[i], frames[j] = frames[j], frames[i]
            self.incomplete(r)

    def test_truncated_dangling_cyclic_chains(self):
        for key in ('consumer', 'host_binding'):
            truncated = copy.deepcopy(self.record)
            truncated[key]['exception'][0]['link'] = 'context'
            self.incomplete(truncated)
            dangling = copy.deepcopy(self.record)
            dangling[key]['exception'].append({**copy.deepcopy(dangling[key]['exception'][0]), 'link': 'cause'})
            self.incomplete(dangling)
            cyclic = copy.deepcopy(self.record)
            link = copy.deepcopy(cyclic[key]['exception'][0])
            cyclic[key]['exception'] = [{**link, 'link': 'context'}, {**link, 'link': None}]
            self.incomplete(cyclic)

    def test_unrelated_outer_exception(self):
        for key in ('consumer', 'host_binding'):
            r = copy.deepcopy(self.record)
            inner = r[key]['exception'][0]
            outer = {'type': 'OSError', 'message': 'unrelated', 'link': 'context', 'frames': list(inner['frames']),
                     'origin': {'file': 'campaign.py', 'function': 'run_implementation_steps', 'line': 1}}
            r[key]['exception'] = [outer, inner]
            r[key]['error'] = 'OSError: unrelated'
            self.incomplete(r)

    def test_absent_or_malformed_fields(self):
        for key in ('consumer', 'host_binding'):
            for mutate in (lambda o: o.pop('exception'), lambda o: o['exception'][0].pop('frames'),
                           lambda o: o['exception'][0].update(frames='routing_adapter.py:validate_campaign_step'),
                           lambda o: o['exception'][0]['origin'].pop('line'), lambda o: o.update(error='')):
                r = copy.deepcopy(self.record)
                mutate(r[key])
                self.incomplete(r)
        for key in ('consumer', 'host_binding'):
            r = copy.deepcopy(self.record)
            r.pop(key)
            self.incomplete(r)

    def test_reason_embedded_in_another_message(self):
        for message in ('refused: ' + a1.WRONG_HEAD_MESSAGE, a1.WRONG_HEAD_MESSAGE + '; extra',
                        a1.WRONG_HEAD_MESSAGE + ' ', a1.WRONG_HEAD_MESSAGE[:-1],
                        a1.WRONG_HEAD_MESSAGE.replace('pour_guard.py', 'other.py'),
                        a1.WRONG_HEAD_MESSAGE.split('; tool file differs')[0]):
            for keys in (('consumer',), ('host_binding',), ('consumer', 'host_binding')):
                r = copy.deepcopy(self.record)
                for key in keys:
                    set_message(r[key], message)
                self.incomplete(r)


class Group3NeverRefusal(Base):
    """3. Host binding, identities, layers, adoption and design facts never stand in for a refusal."""

    def test_missing_or_accepted_host_binding(self):
        for host in (None, {'attempted': True, 'accepted': True, 'value': None},
                     {'attempted': False, 'reason': 'not made'}):
            r = copy.deepcopy(self.record)
            if host is None:
                r.pop('host_binding')
            else:
                r['host_binding'] = host
            self.incomplete(r)

    def test_identities(self):
        for ids in (None, {}, {'proposed_commit': a1.PROPOSED_COMMIT, 'mismatch_commit': a1.PROPOSED_COMMIT},
                    {'proposed_commit': a1.MISMATCH_COMMIT, 'mismatch_commit': a1.MISMATCH_COMMIT},
                    {'proposed_commit': a1.MISMATCH_COMMIT, 'mismatch_commit': a1.PROPOSED_COMMIT},
                    {'proposed_commit': a1.PROPOSED_COMMIT, 'mismatch_commit': '0' * 40},
                    {'proposed_commit': '0' * 40, 'mismatch_commit': a1.MISMATCH_COMMIT}):
            self.incomplete(copy.deepcopy(self.record), ids)

    def test_identities_of_requires_consistent_capture(self):
        manifest = copy.deepcopy(self.manifest)
        self.assertIsNone(a1.identities_of(manifest, None))
        self.assertIsNone(a1.identities_of(manifest, {}))
        manifest['pin']['commit'] = a1.MISMATCH_COMMIT
        self.assertIsNone(a1.identities_of(manifest, {'commit': a1.MISMATCH_COMMIT}))
        same = copy.deepcopy(self.manifest)
        ids = a1.identities_of(same, {'commit': a1.PROPOSED_COMMIT})
        self.incomplete(copy.deepcopy(self.record), ids)

    def test_layers_absent_or_called(self):
        for layer in ('consumer_run_step', 'consumer_verify', 'consumer_adopt'):
            r = copy.deepcopy(self.record)
            r.pop(layer)
            self.incomplete(r)
            r = copy.deepcopy(self.record)
            r[layer] = {'attempted': True, 'accepted': False, 'error': 'ValueError: x'}
            self.assertNotEqual(self.a_state(r), v2.REFUSED)

    def test_successful_consumer_adoption_or_changed_design(self):
        r = copy.deepcopy(self.record)
        r['consumer_adopt'] = {'attempted': True, 'accepted': True, 'value': None}
        self.assertEqual(self.a_state(r), v2.NOT_REFUSED)
        r = copy.deepcopy(self.record)
        r['design_after'] = 'f' * 64
        self.assertEqual(self.a_state(r), v2.NOT_REFUSED)
        r = copy.deepcopy(self.record)
        r['consumer'] = {'attempted': True, 'accepted': True, 'value': None}
        self.assertEqual(self.a_state(r), v2.NOT_REFUSED)

    def test_same_design_copy_or_replayed(self):
        r = copy.deepcopy(self.record)
        r['direct_design'] = r['consumer_design']
        self.incomplete(r)
        r = copy.deepcopy(self.record)
        r['run_step'] = 'replayed'
        self.incomplete(r)


class Group4OtherContractsUnchanged(Base):
    """4. Same-commit mismatch reasons and every candidate/sidecar/stale contract keep their prior results."""

    def test_retained_bcd_and_low_level_and_positive_unchanged(self):
        old, new = v2.v2_verdict(self.manifest), state(self.manifest, self.ids)
        for name in ('b_tampered_candidate', 'b_tampered_sidecar', 'c_stale_input'):
            self.assertEqual(new['controls'][name], old['controls'][name])
        self.assertEqual(new['low_level'], old['low_level'])
        self.assertEqual(new['positive_accepted'], old['positive_accepted'])
        self.assertEqual(new['low_level']['b_tampered_sidecar'], v2.INCOMPLETE)
        sidecar = self.manifest['controls']['b_tampered_sidecar']['direct_adopt']
        self.assertEqual((sidecar['attempted'], sidecar['accepted']), (True, True))  # bare-sidecar acceptance visible

    def test_same_commit_reason_unchanged(self):
        message = 'kicad-route does not qualify: tool file differs from pin: src/kicad_route/__init__.py'
        r = copy.deepcopy(self.record)
        for key in ('consumer', 'host_binding'):
            set_message(r[key], message)
        for ids in (self.ids, None):
            self.assertEqual(self.a_state(r, ids), v2.REFUSED)
        self.assertEqual(state(self.manifest, None, r)['controls'][A],
                         v2.v2_verdict({**self.manifest, 'controls': {**self.manifest['controls'], A: r}})['controls'][A])

    def test_missing_candidate_and_sidecar_stay_incomplete(self):
        for name in ('b_tampered_candidate', 'b_tampered_sidecar'):
            controls = {**self.manifest['controls'], name: {'skipped': 'absent in the positive receipt', 'file': 'x'}}
            m = {**self.manifest, 'controls': controls}
            self.assertEqual(a1.v2a1_verdict(m, self.ids)['controls'][name], v2.INCOMPLETE)
            self.assertEqual(v2.v2_verdict(m)['controls'][name], v2.INCOMPLETE)

    def test_control_matrix_rows_unchanged_under_a1(self):
        """Every existing control-matrix row (actual consumer on the fixture router) keeps its expected
        outcome when judged by A1 instead of verdict-2r4. Without identities A1 adds nothing."""
        spec = importlib.util.spec_from_file_location('k91_control_matrix', ROOT / 'scripts/k91_control_matrix.py')
        matrix = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(matrix)
        k, c = matrix.load(ROOT / 'scripts')

        class Amended:
            def __getattr__(self, name):
                return getattr(c, name)

            def v2_verdict(self, manifest):
                return a1.v2a1_verdict(manifest, None)

        with tempfile.TemporaryDirectory(prefix='agent-ee-k91-a1-matrix-') as temp:
            m = matrix.Matrix(k, Amended(), temp)
            try:
                rows = m.run()
            finally:
                m.close()
        differ = [row['id'] for row in rows if not matrix.matches(row)]
        self.assertGreater(len(rows), 0)
        self.assertEqual(differ, [])


class Group5OriginalsImmutable(unittest.TestCase):
    """5. Original classifier, manifest, receipt, stdout/exit and classification are immutable; A1 is separate."""

    def test_original_classifier_bytes(self):
        self.assertEqual(sha256(ROOT / 'scripts/k91_classify.py'), CLASSIFIER_V2_SHA256)
        preserved = EVIDENCE / 'amend-a1/original-classifier/k91_classify.py'
        self.assertEqual(sha256(preserved), CLASSIFIER_V2_SHA256)
        self.assertEqual(json.loads(MANIFEST.read_text())['classifier_sha256'], CLASSIFIER_V2_SHA256)

    def test_sealed_manifest_receipt_stdout_exit(self):
        self.assertEqual(sha256(MANIFEST), MANIFEST_SHA256)
        self.assertEqual(sha256(RECEIPT), RECEIPT_SHA256)
        manifest = json.loads(MANIFEST.read_text())
        self.assertEqual(manifest['classifier'], v2.CLASSIFIER_V2)
        self.assertIs(manifest['qualified'], False)
        self.assertEqual(manifest['verdict']['controls'][A], v2.INCOMPLETE)
        self.assertIs(manifest['verdict']['controls_refused'], False)
        self.assertEqual(json.loads((BD1 / 'attempt.stdout').read_text()), manifest['verdict'])
        self.assertIn('harness-exit=1', (BD1 / 'attempt-admission.log').read_text().splitlines())
        self.assertEqual(v2.v2_verdict(manifest), manifest['verdict'])

    def test_a1_is_separate_and_writes_nothing(self):
        before = {p: sha256(p) for p in sorted(EVIDENCE.rglob('*')) if p.is_file()}
        manifest, ids = load()
        verdict = a1.v2a1_verdict(manifest, ids)
        self.assertEqual((verdict['classifier'], verdict['amends']), (a1.CLASSIFIER_V2A1, v2.CLASSIFIER_V2))
        self.assertNotIn('verdict-2r4-a1', json.dumps(json.loads(MANIFEST.read_text())))
        self.assertEqual({p: sha256(p) for p in sorted(EVIDENCE.rglob('*')) if p.is_file()}, before)
        self.assertFalse((EVIDENCE / 'amend-a1/evaluation').exists())


if __name__ == '__main__':
    unittest.main()
