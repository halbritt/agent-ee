"""Public catalog preparation, immutable input and sandbox boundaries."""
import copy
import tempfile
import unittest
from pathlib import Path

from agent_ee.storage import digest, write_json
import test_part_selection


class CatalogIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        fixture = test_part_selection.SelectionTests()
        fixture.root = self.root
        fixture.snapshot([fixture.row()])
        write_json(self.root / 'parts.json', fixture.requirements())
        self.params = dict(snapshot='snapshot', manifest_sha256=digest(self.root / 'snapshot/manifest.json'),
                           requirements='parts.json', requirement_id='parts')
        self.files = {str(p.relative_to(self.root)): digest(p) for p in self.root.rglob('*') if p.is_file()}
        self.scope = dict(baseline=dict(requirements=[dict(id='parts', checks=['select'],
                         source=dict(path='parts.json', locator='parts'))]))

    def test_selection_closure_and_exact_requirement_mapping(self):
        from agent_ee.catalog_checks import capability
        result = capability('part_selection', self.params, self.scope, self.files, self.root, 'select')
        self.assertEqual(result['immutable_inputs'], ['parts.json', 'snapshot/catalog.sqlite3', 'snapshot/manifest.json'])
        for change in ('id', 'source', 'extra'):
            scope = copy.deepcopy(self.scope)
            if change == 'id':
                scope['baseline']['requirements'][0]['id'] = 'other'
            elif change == 'source':
                scope['baseline']['requirements'][0]['source']['path'] = 'owned.csv'
            else:
                scope['baseline']['requirements'].append(dict(id='other', checks=['select'], source=dict(path='parts.json', locator='parts')))
            with self.subTest(change=change), self.assertRaises(ValueError):
                capability('part_selection', self.params, scope, self.files, self.root, 'select')

    def scope_document(self):
        for name in ('board', 'schematic', 'target', 'reference'):
            (self.root / name).write_text('Owned fixture ' + name)
        params = {**self.params, 'snapshot': 'data/snapshot', 'requirements': 'data/parts.json'}
        return dict(schema='agent-ee.scope/3', name='catalog-control', scope='Owned catalog constraints',
            board='data/board', schematic='data/schematic',
            inputs=[dict(path='.', at='data', include=['board', 'schematic', 'target', 'reference',
                                                     'parts.json', 'snapshot/*'])],
            checks=[dict(id='select', kind='part_selection', params=params)],
            baseline=dict(requirements=[dict(id='parts', intent='acceptance', statement='Declared parts match snapshot constraints',
                source=dict(path='data/parts.json', locator='parts'), checks=['select'])],
                targets=['data/target'], reference_architectures=['data/reference']))

    def test_public_checks_only_verify_seal_and_witness_authority(self):
        from toolchain import require_toolchain
        require_toolchain()
        from agent_ee.cli import run_review, verify
        from agent_ee.baseline import validate_witnesses
        from agent_ee.storage import read_json
        scope = self.scope_document()
        write_json(self.root / 'scope.json', scope)
        run = self.root / 'run'
        run_review(self.root / 'scope.json', run, 60, checks_only=True)
        check = read_json(run / 'checks/select.json')
        self.assertEqual((check['execution'], check['outcome']), ('ok', 'pass'), check)
        self.assertEqual(check['details']['report']['parts'][0]['chosen']['lcsc'], 'C1')
        baseline = read_json(run / 'baseline.json')
        self.assertTrue({'data/parts.json', 'data/snapshot/manifest.json', 'data/snapshot/catalog.sqlite3'} <= baseline['files'].keys())
        verify(run)
        with self.assertRaisesRegex(ValueError, 'failed criterion'):
            validate_witnesses(run, [dict(requirement_id='parts', check_id='select')],
                [dict(path='checks/select.json', locator='outcome')])
        from unittest.mock import patch
        from agent_ee.cli import policy_files
        changed = policy_files()
        normalizer = next(path for path in changed if path == 'catalog.py')
        changed[normalizer] = '0' * 64
        with patch('agent_ee.cli.policy_files', return_value=changed), self.assertRaises(ValueError):
            verify(run)
        report = run / 'work/select/catalog.json'
        report.write_text('{}')
        with self.assertRaises(ValueError):
            verify(run)

    def test_sandbox_invalid_repinned_and_missing_inputs_fail_unknown(self):
        from toolchain import require_toolchain
        require_toolchain()
        from agent_ee import executor
        from agent_ee.storage import read_json
        from catalog_repin import repin, set_derived
        work = self.root / 'work'
        work.mkdir()
        snapshot = self.root / 'snapshot'
        cases = [('bad-pin', '0' * 64), ('valid', self.params['manifest_sha256'])]
        for name, pin in cases:
            rec = executor.execute(self.root, work, ['/usr/bin/python3', '-m', 'agent_ee.catalog_checks',
                'part_selection', '/candidate/snapshot', pin, '--requirements', '/candidate/parts.json',
                '--out', '/work/report.json'], timeout=30)
            self.assertEqual(rec['execution'], 'ok', rec)
            report = read_json(work / 'report.json')
            self.assertEqual((report['execution'], report['outcome']),
                             ('ok', 'pass') if name == 'valid' else ('failed', 'unknown'), report)
        pin = repin(snapshot, lambda connection: set_derived(connection, 'C1',
                    {'resistance': {'min': '1', 'max': '2', 'unit': 'ohm'}}))
        requirement_bytes = (self.root / 'parts.json').read_bytes()
        for name in ('contradiction', 'missing-requirements', 'missing-db'):
            if name == 'missing-requirements':
                (self.root / 'parts.json').unlink()
            if name == 'missing-db':
                (self.root / 'parts.json').write_bytes(requirement_bytes)
                (snapshot / 'catalog.sqlite3').unlink()
            rec = executor.execute(self.root, work, ['/usr/bin/python3', '-m', 'agent_ee.catalog_checks',
                'part_selection', '/candidate/snapshot', pin, '--requirements', '/candidate/parts.json',
                '--out', '/work/report.json'], timeout=30)
            self.assertEqual(rec['execution'], 'ok', rec)
            report = read_json(work / 'report.json')
            self.assertEqual((report['execution'], report['outcome']), ('failed', 'unknown'), report)

    def test_catalog_is_informational_and_missing_closure_is_unsupported(self):
        from agent_ee.capabilities import describe_check
        spec = dict(id='integrity', kind='catalog', params={k: self.params[k] for k in ('snapshot', 'manifest_sha256')})
        result = describe_check(spec, check_specs=[spec], files=self.files, scope={}, inputs=self.root)
        self.assertEqual(result['status'], 'supported')
        self.assertFalse(result['can_evaluate_bound'])
        files = dict(self.files)
        del files['snapshot/catalog.sqlite3']
        result = describe_check(spec, check_specs=[spec], files=files, scope={}, inputs=self.root)
        self.assertEqual(result['status'], 'unsupported')

    def test_unreadable_requirements_keep_verified_snapshot_identity_in_sandbox(self):
        from toolchain import require_toolchain
        require_toolchain()
        from agent_ee import executor
        from agent_ee.storage import read_json
        snapshot = read_json(self.root / 'snapshot/manifest.json')
        work = self.root / 'work'
        work.mkdir()
        requirements = self.root / 'broken.json'
        for name, payload in (('missing', None), ('invalid-json', b'{'),
                              ('duplicate-json', b'{"parts":[],"parts":[]}'),
                              ('invalid-utf8', b'\xff')):
            with self.subTest(name=name):
                if payload is not None:
                    requirements.write_bytes(payload)
                rec = executor.execute(self.root, work, ['/usr/bin/python3', '-m', 'agent_ee.catalog_checks',
                    'part_selection', '/candidate/snapshot', self.params['manifest_sha256'],
                    '--requirements', '/candidate/broken.json', '--out', '/work/report.json'], timeout=30)
                self.assertEqual(rec['execution'], 'ok', rec)
                report = read_json(work / 'report.json')
                self.assertEqual((report['execution'], report['outcome'], report['parts']),
                                 ('failed', 'unknown', []), report)
                self.assertEqual(report['snapshot_date'], snapshot['snapshot_date'])
                self.assertEqual(report['snapshot_sha256'], snapshot['database']['sha256'])
                self.assertEqual(report['snapshot_manifest_sha256'], self.params['manifest_sha256'])
                self.assertTrue(report['details']['error'])

    def test_preparation_cli_is_local_and_bad_pin_publishes_nothing(self):
        import contextlib
        import io
        from agent_ee.cli import main
        from agent_ee.catalog import load_catalog
        from agent_ee.storage import read_json
        manifest = read_json(self.root / 'snapshot/manifest.json')
        source = {key: manifest[key] for key in ('snapshot_date', 'source_url', 'source_commit', 'currency', 'stale_before', 'volumes')}
        source['schema'] = 'agent-ee.catalog-source/1'
        write_json(self.root / 'source.json', source)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['capture-catalog', str(self.root / 'source.json'), '--source-root', str(self.root),
                                   '--out', str(self.root / 'second')]), 0)
        self.assertEqual(output.getvalue().strip(), digest(self.root / 'second/manifest.json'))
        self.assertEqual(load_catalog(self.root / 'second', output.getvalue().strip())['rows'][0]['lcsc'], 'C1')
        source['volumes'][0]['sha256'] = '0' * 64
        write_json(self.root / 'source.json', source)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertNotEqual(main(['capture-catalog', str(self.root / 'source.json'), '--source-root', str(self.root),
                                     '--out', str(self.root / 'bad')]), 0)
        self.assertFalse((self.root / 'bad').exists())

    def test_snapshot_requirements_and_normalization_policy_are_identity_bound(self):
        from agent_ee.candidate import capture, verify_snapshot, verify_source
        from agent_ee.baseline import load_baseline
        from agent_ee.cli import policy_files
        from agent_ee.storage import read_json
        scope = self.scope_document()
        write_json(self.root / 'scope.json', scope)
        run = self.root / 'captured'
        run.mkdir()
        capture(self.root / 'scope.json', run)
        before = load_baseline(run)['baseline_id']
        hashes = policy_files()
        self.assertTrue(any(path.endswith('catalog.py') for path in hashes))
        self.assertTrue(any(path.endswith('part_selection.py') for path in hashes))
        requirements = read_json(self.root / 'parts.json')
        requirements['build_quantity'] = 11
        write_json(self.root / 'parts.json', requirements)
        with self.assertRaises(ValueError):
            verify_source(run)
        other = self.root / 'recaptured'
        other.mkdir()
        capture(self.root / 'scope.json', other)
        self.assertNotEqual(load_baseline(other)['baseline_id'], before)
        (run / 'inputs/data/snapshot/catalog.sqlite3').write_bytes(b'corrupt')
        with self.assertRaises(ValueError):
            verify_snapshot(run)

    def test_source_declarations_cannot_be_changed_under_existing_pin(self):
        from agent_ee.catalog import catalog_check
        from agent_ee.storage import read_json
        from catalog_repin import repin
        snapshot = self.root / 'snapshot'
        path = snapshot / 'manifest.json'
        manifest = read_json(path)
        manifest['source_url'] = 'https://example.invalid/unattested-claim'
        write_json(path, manifest)
        report = catalog_check(snapshot, self.params['manifest_sha256'])
        self.assertEqual((report['execution'], report['outcome']), ('failed', 'unknown'))
        # A newly approved declaration is consistent but still unauthenticated.
        pin = repin(snapshot)
        report = catalog_check(snapshot, pin)
        self.assertEqual((report['execution'], report['outcome']), ('ok', 'pass'))

    def test_no_stock_is_unknown_and_cannot_witness_a_defect(self):
        from toolchain import require_toolchain
        require_toolchain()
        from agent_ee.cli import run_review, verify
        from agent_ee.storage import read_json
        from agent_ee.baseline import validate_witnesses
        requirements = read_json(self.root / 'parts.json')
        requirements['stock_floor'] = 1001
        write_json(self.root / 'parts.json', requirements)
        write_json(self.root / 'scope.json', self.scope_document())
        run = self.root / 'empty-run'
        run_review(self.root / 'scope.json', run, 60, checks_only=True)
        check = read_json(run / 'checks/select.json')
        self.assertEqual((check['execution'], check['outcome']), ('ok', 'unknown'), check)
        self.assertIsNone(check['details']['report']['parts'][0]['chosen'])
        verify(run)
        with self.assertRaisesRegex(ValueError, 'failed criterion'):
            validate_witnesses(run, [dict(requirement_id='parts', check_id='select')],
                [dict(path='checks/select.json', locator='outcome')])
