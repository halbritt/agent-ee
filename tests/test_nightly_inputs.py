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
            'mount_plan_sha256': 'n' * 64, 'launch': ['docker', 'run'],
            'launch_sha256': sha(json.dumps(['docker', 'run']).encode()), 'validated_at': '2026-11-09T00:00:00+00:00',
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
        self.assertEqual((bound['mount_plan_sha256'], bound['launch_sha256'], bound['validated_at']),
                         ('n' * 64, sha(json.dumps(['docker', 'run']).encode()), '2026-11-09T00:00:00+00:00'))

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
                              'unvalidated launch': {'validated_at': None}, 'no mount plan': {'mount_plan_sha256': ''},
                              'launch digest': {'launch_sha256': '0' * 64},
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

    def test_config_is_exact_finite_bounded_and_uncoerced(self):
        loaded = nightly_inputs.load_stock_config(self.config())
        self.assertEqual((loaded['per_request_timeout_seconds'], loaded['max_bytes']), (10, 2097152))
        nightly_inputs.load_stock_config(self.config(endpoint='http://127.0.0.1:8/search'))
        limits = {'max_seconds': 60, 'min_interval': 1.0, 'max_bytes': 2 * 1024 * 1024}
        nightly_inputs.load_stock_config(self.config(), limits)
        refusals = {'schema': {'schema': 'x'}, 'relative': {'context_path': 'rel.json'},
                    'zero timeout': {'per_request_timeout_seconds': 0}, 'bool bytes': {'max_bytes': True},
                    'float bytes': {'max_bytes': 2097152.0}, 'plain http': {'endpoint': 'http://example.invalid/'},
                    'extra field': {'retries': 3}, 'invocation above 420': {'invocation_timeout_seconds': 421},
                    'request not below invocation': {'per_request_timeout_seconds': 190},
                    'negative interval': {'min_interval_seconds': -1}}
        for label, overrides in refusals.items():
            with self.subTest(label=label), self.assertRaises(ValueError):
                nightly_inputs.load_stock_config(self.config(**overrides))
        for label, text in {'NaN': 'NaN', 'Infinity': 'Infinity', '-Infinity': '-Infinity'}.items():
            with self.subTest(label=label):
                path = self.base / 'nonfinite.json'
                path.write_text(json.dumps(json.loads(self.config().read_text())).replace('10,', text + ',', 1))
                with self.assertRaises(ValueError):
                    nightly_inputs.load_stock_config(path)
        for label, overrides in {'above producer seconds': {'per_request_timeout_seconds': 61},
                                 'below producer interval': {'min_interval_seconds': 0.5},
                                 'above producer bytes': {'max_bytes': 2 * 1024 * 1024 + 1}}.items():
            with self.subTest(label=label), self.assertRaises(ValueError):
                nightly_inputs.load_stock_config(self.config(**overrides), limits)
        with self.subTest(label='duplicate field'):
            path = self.base / 'dup.json'
            path.write_text(self.config().read_text()[:-1] + ', "max_bytes": 1}')
            with self.assertRaises(ValueError):
                nightly_inputs.load_stock_config(path)

    def test_missing_context_refuses_before_any_capture(self):
        with self.assertRaises(RuntimeError) as error:
            nightly_inputs.prepare_fresh_stock(self.config(), ROOT, self.base / 'stock')
        self.assertIn('stock preparation failed', str(error.exception))
        self.assertFalse((self.base / 'stock/captures').exists())

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


try:
    from test_jlc_stock_capture import JlcStub, row, search
except ImportError:
    from tests.test_jlc_stock_capture import JlcStub, row, search

sys.path.insert(0, str(ROOT / 'src'))
from agent_ee import jlc_stock_capture as stock  # noqa: E402  (authoring the original closure; the child under test imports its own)
from agent_ee.acquisition import alternative_context  # noqa: E402
from agent_ee.storage import json_bytes  # noqa: E402

PARENT_IDS = (['C17024', 'C25804'], ['C468683'])  # producer order: numeric ascending within a parent
ROWS = {'C25804': dict(componentModelEn='MPN-A', componentBrandEn='Mfr A'),
        'C17024': dict(componentModelEn='MPN-B', componentBrandEn='Mfr B', library='expand'),
        'C468683': dict(componentModelEn='MPN-C', componentBrandEn='Mfr C', library='expand')}


def routes(stock_by_id, **changes):
    result = {}
    for lcsc, options in ROWS.items():
        if lcsc in changes:
            result[lcsc] = changes[lcsc]
        else:
            result[lcsc] = (200, search(row(lcsc, stock_by_id[lcsc], **options)))
    return result


def author_closure(root: Path, server: JlcStub) -> Path:
    """An explicitly authored two-parent context whose parents the REAL producer captured from the stub."""
    def put(rel, raw):
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        return {'path': rel, 'sha256': sha(raw)}
    files, parents, union = {}, [], None
    catalog_dir = 'preparation/catalog'
    for index, ids in enumerate(PARENT_IDS, 1):
        out = root.parent / f'authored-parent-{index}'
        stock.capture_jlc_stock(ids, out, allow_network=True, endpoint=server.url, min_interval=1.0, timeout=5)
        raw = (out / 'catalog.json').read_bytes()
        catalog = json.loads(raw)
        blob = put(f'preparation/parents/parent-{index}.json', raw)
        files[blob['path']] = blob['sha256']
        parents.append(blob)
        for request in catalog['requests']:
            rel = request['response']['path']
            copied = put(f'{catalog_dir}/{rel}', (out / rel).read_bytes())
            files[copied['path']] = copied['sha256']
        if union is None:
            union = dict(schema=stock.SCHEMA, as_of=catalog['as_of'], completed_at=catalog['completed_at'],
                         stale_before=None, source=catalog['source'], requests=[], parts={})
        union['as_of'] = min(union['as_of'], catalog['as_of'], key=lambda t: stock._utc(t, 'as_of'))
        union['completed_at'] = max(union['completed_at'], catalog['completed_at'], key=lambda t: stock._utc(t, 'completed_at'))
        union['requests'] += catalog['requests']
        union['parts'].update(catalog['parts'])
    catalog_blob = put(f'{catalog_dir}/catalog.json', json_bytes(union))
    derivation_blob = put('preparation/derivation.json', json_bytes({
        'kind': 'Authored conservative offline union; NOT a fresh capture', 'parents': [{'sha256': b['sha256']} for b in parents]}))
    carrier = put('carrier.html', b'<html>authored carrier page</html>\n')
    ordering = put('ordering.txt', b'authored ordering text\n')
    for blob in (catalog_blob, derivation_blob, carrier, ordering):
        files[blob['path']] = blob['sha256']
    context = {'schema': alternative_context.SCHEMA, 'reference': 'acquisition-control', 'files': files,
               'catalog': catalog_blob, 'carrier_page': carrier, 'ordering_text': ordering,
               'parents': parents, 'derivation': derivation_blob}
    context_path = root.parent / 'authored-context.json'
    context_path.write_bytes(json_bytes(context))
    alternative_context.read(root, context_path.read_bytes())  # the authored closure must itself be admissible
    return context_path


class StockPreparationEndToEndTests(unittest.TestCase):
    """The REAL producer and context algebra run from the selected checkout against a loopback stub."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='nightly-stock-e2e-')
        self.base = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.source = self.base / 'closure/source'
        self.source.mkdir(parents=True)
        self.original_server = JlcStub(routes({'C25804': 10, 'C17024': 20, 'C468683': 30}))
        self.addCleanup(self.original_server.close)
        self.context_path = author_closure(self.source, self.original_server)
        self.original_bytes = {path: path.read_bytes() for path in self.source.rglob('*') if path.is_file()}
        self.original_bytes[self.context_path] = self.context_path.read_bytes()

    def fresh_server(self, **changes):
        server = JlcStub(routes({'C25804': 111, 'C17024': 222, 'C468683': 333}, **changes))
        self.addCleanup(server.close)
        return server

    def config(self, server):
        path = self.base / 'stock-config.json'
        path.write_text(json.dumps({'schema': 'agent-ee.nightly-stock-config/1', 'context_path': str(self.context_path),
                                    'source_root': str(self.source), 'endpoint': server.url,
                                    'per_request_timeout_seconds': 5, 'invocation_timeout_seconds': 60,
                                    'min_interval_seconds': 1, 'max_bytes': 2097152}))
        return path

    def assert_originals_unchanged(self):
        for path, raw in self.original_bytes.items():
            self.assertEqual(path.read_bytes(), raw, path)

    def test_fresh_success_builds_an_admissible_union_from_real_captures(self):
        server = self.fresh_server()
        stock_dir = self.base / 'stock'
        prepared = nightly_inputs.prepare_fresh_stock(self.config(server), ROOT, stock_dir)
        record = prepared['record']
        self.assertEqual(record['status'], 'prepared')
        self.assertEqual([inv['requested'] for inv in record['invocations']], [list(ids) for ids in PARENT_IDS])
        self.assertTrue(all(inv['exit_code'] == 0 for inv in record['invocations']))
        self.assertEqual(len(server.requests), 3)  # one real request per id, none retried
        context, captures = alternative_context.read(prepared['source_root'], prepared['context_path'].read_bytes())
        catalog = json.loads(captures[context['catalog']['path']])
        self.assertEqual({lcsc: part['stock'] for lcsc, part in catalog['parts'].items()},
                         {'C25804': 111, 'C17024': 222, 'C468683': 333})
        self.assertEqual([r['lcsc'] for r in catalog['requests']], ['C17024', 'C25804', 'C468683'])
        self.assertEqual(record['producer_sha256'], sha((ROOT / 'src/agent_ee/jlc_stock_capture.py').read_bytes()))
        self.assertTrue((stock_dir / 'captures/parent-1/catalog.json').is_file())
        self.assert_originals_unchanged()
        manifest = self.base / 'manifest.json'
        manifest.write_text(json.dumps({'schema': 'agent-ee.native-inputs/1', 'inputs': [
            {'name': 'addr-source', 'env': 'AGENT_EE_ADDR_SOURCE', 'path': str(self.source), 'files': {}},
            {'name': 'addr-context', 'env': 'AGENT_EE_ADDR_CONTEXT', 'path': str(self.context_path), 'files': {}}]}))
        rebound = json.loads(nightly_inputs.rebind_manifest(manifest, self.base / 'attempt.json', prepared).read_text())
        self.assertEqual({item['path'] for item in rebound['inputs']},
                         {str(prepared['source_root']), str(prepared['context_path'])})

    def test_failed_missing_or_changed_responses_are_retained_as_failure_without_fallback(self):
        cases = {
            'failed': ({'C17024': (503, b'{"code":503}')}, 'exit 2'),
            'missing': ({'C468683': (200, search())}, 'incomplete'),
            'changed': ({'C25804': (200, search(row('C25804', 5, componentModelEn='OTHER-MPN', componentBrandEn='Mfr A')))},
                        'changed part identity'),
        }
        for label, (changes, expected) in cases.items():
            with self.subTest(label=label):
                server = self.fresh_server(**changes)
                stock_dir = self.base / f'stock-{label}'
                with self.assertRaises(RuntimeError) as error:
                    nightly_inputs.prepare_fresh_stock(self.config(server), ROOT, stock_dir)
                self.assertIn(expected, str(error.exception))
                record = json.loads((stock_dir / 'record.json').read_text())
                self.assertEqual(record['status'], 'failed')
                self.assertIn(expected, record['failure'])
                self.assertFalse((stock_dir / 'addr-current-context.json').exists())
                self.assertTrue(record['invocations'], 'the real producer ran before the refusal')
                self.assert_originals_unchanged()


if __name__ == '__main__':
    unittest.main()
