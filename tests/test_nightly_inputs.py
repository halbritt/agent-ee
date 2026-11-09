"""Nightly input receipt binding and fresh-stock preparation consumer contract (AGENTEE-619/621)."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import nightly_inputs


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class ReceiptBindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='nightly-inputs-')
        self.base = Path(self.tmp.name)
        self.manifest = self.base / 'manifest.json'
        self.manifest.write_text('{"schema": "agent-ee.native-inputs/1", "inputs": []}\n')
        self.evidence = self.base / 'native'
        (self.evidence / 'run').mkdir(parents=True)
        self.receipt = self.evidence / 'run/native-inputs.json'
        self.record = {
            'schema': 'agent-ee.native-inputs/1', 'status': 'prepared', 'manifest': str(self.manifest),
            'manifest_sha256': sha(self.manifest.read_bytes()), 'image': 'sha256:' + 'a' * 64,
            'agent_ee': {'commit': 'c' * 40, 'tree': 't' * 40, 'clean': True},
            'runner': {'sha256': 'r' * 64}, 'preparer': {'sha256': 'p' * 64}, 'policy_sha256': 'q' * 64,
            'module_env_sha256': 'm' * 64, 'output_directory': str(self.evidence / 'run/output'),
            'inputs': [{'name': 'k91-final', 'env': 'AGENT_EE_K91_FINAL_RECEIPT', 'read_only': True,
                        'files': {'RESULT.md': 'f' * 64}}]}
        self.receipt.write_text(json.dumps(self.record))
        self.log = f'ae-ci-exec: image sha256:{"a" * 64}\nae-ci-exec: native input receipt {self.receipt}\n'.encode()

    def tearDown(self):
        self.tmp.cleanup()

    def bind(self, log=None, **overrides):
        options = dict(manifest=self.manifest, evidence_dir=self.evidence, commit='c' * 40, tree='t' * 40,
                       runner_sha256='r' * 64, image_ids=['sha256:' + 'a' * 64])
        options.update(overrides)
        return nightly_inputs.bind_receipt(self.log if log is None else log, **options)

    def test_bound_receipt_records_identities_and_inputs(self):
        bound = self.bind()
        self.assertEqual(bound['sha256'], sha(self.receipt.read_bytes()))
        self.assertEqual(bound['manifest_sha256'], sha(self.manifest.read_bytes()))
        self.assertEqual(bound['image'], 'sha256:' + 'a' * 64)
        self.assertEqual(bound['inputs'], [{'name': 'k91-final', 'env': 'AGENT_EE_K91_FINAL_RECEIPT',
                                            'identity': {'RESULT.md': 'f' * 64}}])
        self.assertEqual(bound['preparer_sha256'], 'p' * 64)

    def test_every_binding_mismatch_refuses(self):
        cases = {
            'no announcement': dict(log=b'ae-ci-exec: image sha256:' + b'a' * 64 + b'\n'),
            'two announcements': dict(log=self.log + self.log),
            'outside evidence': dict(evidence_dir=self.base / 'elsewhere'),
            'other commit': dict(commit='0' * 40), 'other tree': dict(tree='0' * 40),
            'other runner': dict(runner_sha256='0' * 64), 'other image': dict(image_ids=['sha256:' + 'b' * 64]),
        }
        for label, overrides in cases.items():
            with self.subTest(label=label), self.assertRaises(ValueError):
                self.bind(**overrides)
        for label, change in {'status': {'status': 'prepared_with_unavailable_inputs'},
                              'manifest': {'manifest_sha256': '0' * 64}, 'schema': {'schema': 'other/1'},
                              'no inputs': {'inputs': []}, 'policy': {'policy_sha256': ''},
                              'writable input': {'inputs': [{**self.record['inputs'][0], 'read_only': False}]}}.items():
            with self.subTest(label=label):
                self.receipt.write_text(json.dumps({**self.record, **change}))
                with self.assertRaises(ValueError):
                    self.bind()
        with self.subTest(label='duplicate field'):
            self.receipt.write_text(json.dumps(self.record)[:-1] + ', "status": "prepared"}')
            with self.assertRaises(ValueError):
                self.bind()

    def test_retained_binding_verification_detects_change_and_other_manifest(self):
        bound = self.bind()
        nightly_inputs.verify_bound_receipt(bound, manifest_sha256=bound['manifest_sha256'])
        with self.assertRaises(ValueError):
            nightly_inputs.verify_bound_receipt(bound, manifest_sha256='0' * 64)
        self.receipt.write_bytes(self.receipt.read_bytes() + b'\n')
        with self.assertRaises(ValueError):
            nightly_inputs.verify_bound_receipt(bound, manifest_sha256=bound['manifest_sha256'])


class StockPreparationContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='nightly-stock-')
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def config(self, **overrides):
        document = {'schema': 'agent-ee.nightly-stock-config/1', 'context_path': str(self.base / 'context.json'),
                    'source_root': str(self.base / 'source'), 'per_request_timeout_seconds': 10,
                    'invocation_timeout_seconds': 190, 'min_interval_seconds': 5, 'max_bytes': 2097152}
        document.update(overrides)
        path = self.base / 'stock-config.json'
        path.write_text(json.dumps(document))
        return path

    def test_config_is_exact_bounded_and_https_only(self):
        nightly_inputs.load_stock_config(self.config())
        nightly_inputs.load_stock_config(self.config(endpoint='http://127.0.0.1:8/search'))
        for label, overrides in {'schema': {'schema': 'x'}, 'relative': {'context_path': 'rel.json'},
                                 'zero timeout': {'per_request_timeout_seconds': 0}, 'bool': {'max_bytes': True},
                                 'plain http': {'endpoint': 'http://example.invalid/'},
                                 'extra field': {'retries': 3}}.items():
            with self.subTest(label=label), self.assertRaises(ValueError):
                nightly_inputs.load_stock_config(self.config(**overrides))

    def test_missing_context_refuses_before_any_capture(self):
        with self.assertRaises((OSError, ValueError)):
            nightly_inputs.prepare_fresh_stock(self.config(), ROOT, self.base / 'stock')
        self.assertFalse((self.base / 'stock/captures').exists() and any((self.base / 'stock/captures').iterdir()))

    def test_manifest_rebinding_replaces_exactly_the_stock_entries(self):
        old_source, old_context = self.base / 'old-source', self.base / 'old-context.json'
        new_source, new_context = self.base / 'new-source', self.base / 'new-context.json'
        for root in (old_source, new_source):
            root.mkdir()
            (root / 'catalog.json').write_text(root.name + '\n')
        old_context.write_text('old\n')
        new_context.write_text('new\n')
        manifest = self.base / 'manifest.json'
        manifest.write_text(json.dumps({'schema': 'agent-ee.native-inputs/1', 'inputs': [
            {'name': 'addr-source', 'env': 'AGENT_EE_ADDR_SOURCE', 'path': str(old_source), 'files': {'catalog.json': '0' * 64}},
            {'name': 'addr-context', 'env': 'AGENT_EE_ADDR_CONTEXT', 'path': str(old_context), 'files': {'.': '0' * 64}},
            {'name': 'power-control', 'env': 'AGENT_EE_ACQUISITION_INPUTS', 'path': '/retained/power', 'files': {'a': '1' * 64}}]}))
        prepared = {'source_root': new_source, 'context_path': new_context,
                    'replaced': {'source_root': old_source, 'context_path': old_context}}
        written = nightly_inputs.rebind_manifest(manifest, self.base / 'attempt-manifest.json', prepared)
        document = json.loads(written.read_text())
        by_name = {item['name']: item for item in document['inputs']}
        self.assertEqual(by_name['addr-source']['path'], str(new_source))
        self.assertEqual(by_name['addr-source']['files'], {'catalog.json': sha(b'new-source\n')})
        self.assertEqual(by_name['addr-context'], {'name': 'addr-context', 'env': 'AGENT_EE_ADDR_CONTEXT',
                                                   'path': str(new_context), 'files': {'.': sha(b'new\n')}})
        self.assertEqual(by_name['power-control']['path'], '/retained/power')
        with self.assertRaises(ValueError):
            nightly_inputs.rebind_manifest(manifest, self.base / 'attempt-manifest.json', prepared)
        prepared['replaced']['context_path'] = self.base / 'absent.json'
        with self.assertRaises(ValueError):
            nightly_inputs.rebind_manifest(manifest, self.base / 'another.json', prepared)


if __name__ == '__main__':
    unittest.main()
