"""CPU-only regression for the nested-bwrap rating failure (e7c9e4ad).

Host records and document text below are authored test facts, not native evidence.
"""
import copy
from dataclasses import replace
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_ee import binding_fill, datasheet_facts, forward_annotation, native_run, passive_ratings
from agent_ee.storage import digest, read_json, write_json
import test_passive_ratings as fixtures


def mounts(extra=()):
    entries = [('/', 'rw', 'tmpfs'), ('/usr', 'ro', 'ext4'), ('/etc', 'ro', 'ext4'),
               ('/candidate', 'ro', 'ext4'), ('/app', 'ro', 'ext4'), ('/tool/agent-ee', 'ro', 'ext4'),
               ('/work', 'rw', 'ext4'), ('/proc', 'rw', 'proc'), ('/dev', 'rw', 'tmpfs'), ('/tmp', 'rw', 'tmpfs')]
    return '\n'.join(f'{i} 0 0:1 / {path} {flags} - {fs} fixture rw'
                     for i, (path, flags, fs) in enumerate([*entries, *extra], 1))


class RatingStageTests(unittest.TestCase):
    def setUp(self):
        # Reuse checked-in owned capture construction without toolchain preflight.
        fixtures.PassiveRatingTests.rebuild(self, [fixtures.POWER, fixtures.SUPPLY])
        fixtures.PassiveRatingTests.select_pdf(self, ['Rated power 100 mW at 70 C'])
        self.fact = fixtures.PassiveRatingTests.fact(self, '100', 'mW', 'Rated power 100 mW')
        self.tool = {'path': '/usr/bin/pdftotext', 'sha256': digest('/usr/bin/pdftotext'),
                     'version': 'pdftotext version CPU_MOCK_ONLY'}
        write_json(self.root / 'rating-facts.json', {'schema': 'agent-ee.datasheet-facts/1', 'facts': [self.fact]})
        self.ratings.update(facts={'path': 'rating-facts.json', 'sha256': digest(self.root / 'rating-facts.json')}, extractor=self.tool)
        for name, value in [('intent.json', self.intent), ('refs.json', self.refs),
                            ('deviations.json', self.deviations), ('ratings.json', self.ratings)]:
            write_json(self.root / name, value)
        self.work = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.args = ['--intent', 'intent.json', '--manifest', 'parts.json', '--part-refs', 'refs.json',
                     '--deviations', 'deviations.json', '--captures', '.', '--ratings', 'ratings.json']
        self.calls = []
        real_path, read_text = Path, Path.read_text
        def paths(value):
            value = str(value)
            for virtual, actual in (('/candidate', self.root), ('/work', self.work)):
                if value == virtual or value.startswith(virtual + '/'):
                    return actual / value.removeprefix(virtual).lstrip('/')
            return real_path(value)
        self.enterContext(patch.object(binding_fill, 'Path', side_effect=paths))
        self.enterContext(patch.object(forward_annotation, '__file__', '/tool/agent-ee/agent_ee/forward_annotation.py'))
        self.view = mounts()
        def text(path, *args, **kwargs):
            return self.view if str(path) == '/proc/self/mountinfo' else read_text(path, *args, **kwargs)
        self.enterContext(patch.object(Path, 'read_text', text))

    def host(self, intent, *, inputs, work, timeout, mounts, cancel):
        self.calls.append((intent, timeout, cancel))
        self.assertEqual(intent.isolation, 'host')
        self.assertIsNone(inputs)
        self.assertEqual(mounts, ())
        self.assertEqual(intent.env['HOME'], str(work / 'home'))
        self.assertEqual(intent.env['KICAD_CONFIG_HOME'], str(work / 'kicad-config'))
        self.assertEqual(intent.cwd, str(work))
        work.mkdir(parents=True)
        version = intent.argv == ('/usr/bin/pdftotext', '-v')
        if not version:
            self.assertEqual(intent.argv, ('/usr/bin/pdftotext', '-layout',
                str(self.root / self.selected['path']), str(work / 'document.txt')))
            (work / 'document.txt').write_text('Rated power 100 mW at 70 C\f')
        (work / 'console.log').write_text(self.tool['version'] + '\n' if version else 'CPU_MOCK_ONLY extraction\n')
        (work / 'stdout.log').write_text('')
        (work / 'stderr.log').write_text('CPU_MOCK_ONLY\n')
        write_json(work / 'execution.json', {'execution': 'ok', 'exit_code': 0, 'CPU_MOCK_ONLY': True})
        return {'execution': 'ok', 'exit_code': 0}

    def stage(self, args=None):
        with patch.object(native_run.Host, 'run', side_effect=self.host):
            return binding_fill._stage_main(self.args if args is None else args)

    def test_stage_propagates_real_owned_binding_and_retains_both_probe_records(self):
        before = digest(self.root / self.selected['path'])
        self.assertEqual(self.stage(), 0)
        intent = read_json(self.work / 'out/intent.json')
        fields = intent['components'][0]['part_binding']
        self.assertEqual(fields['Power'], '100mW')
        report = read_json(self.work / 'out/binding-fill.json')
        self.assertEqual(report['ratings']['derived']['R1']['record']['ratings']['Power']['sources'][1]['kind'], 'datasheet')
        self.assertEqual(digest(self.root / self.selected['path']), before)
        for child in ('version', 'pdf-0'):
            for artifact in ('console.log', 'stdout.log', 'stderr.log', 'execution.json'):
                self.assertTrue((self.work / 'out/rating-citations' / child / artifact).is_file())
        self.assertEqual(len(self.calls), 2)

    def test_version_failure_retains_raw_denial_and_does_not_claim_mismatch(self):
        original = self.host
        def failed(intent, **kwargs):
            original(intent, **kwargs)
            (kwargs['work'] / 'console.log').write_text('bwrap: namespace denial CPU_MOCK_ONLY\n')
            write_json(kwargs['work'] / 'execution.json', {'execution': 'failed', 'exit_code': 1})
            return {'execution': 'failed', 'exit_code': 1}
        with patch.object(native_run.Host, 'run', side_effect=failed):
            self.assertEqual(binding_fill._stage_main(self.args), 1)
        report = read_json(self.work / 'out/binding-fill.json')
        self.assertIn('probe_failed', report['reason'])
        self.assertNotIn('; version_mismatch;', report['reason'])
        self.assertEqual(read_json(self.work / 'out/rating-citations/version/execution.json')['exit_code'], 1)
        self.assertFalse((self.work / 'out/intent.json').exists())

    def test_extraction_failure_remains_unsupported_with_raw_status(self):
        original = self.host
        def failed(intent, **kwargs):
            result = original(intent, **kwargs)
            if '-layout' in intent.argv:
                write_json(kwargs['work'] / 'execution.json', {'execution': 'timeout', 'exit_code': None})
                return {'execution': 'timeout', 'exit_code': None}
            return result
        with patch.object(native_run.Host, 'run', side_effect=failed):
            self.assertEqual(binding_fill._stage_main(self.args), 1)
        self.assertIn('citation unsupported: pdftotext timeout', read_json(self.work / 'out/binding-fill.json')['reason'])
        self.assertEqual(read_json(self.work / 'out/rating-citations/pdf-0/execution.json')['execution'], 'timeout')

    def test_existing_guard_refuses_unknown_or_writable_source_view_before_any_child(self):
        for view in (mounts([('/run/host', 'ro', 'ext4')]), mounts().replace('/candidate ro', '/candidate rw')):
            self.view = view
            with patch.object(native_run.Host, 'run', side_effect=AssertionError('must not launch')), self.assertRaises(ValueError):
                binding_fill._stage_main(self.args)
        self.assertFalse((self.work / 'out').exists())

    def test_adapter_rejects_other_commands_inputs_paths_and_mounts(self):
        adapter = binding_fill._RatingStageExecution()
        base = native_run.Intent('probe', ['/usr/bin/pdftotext', '-v'], timeout=60)
        options = dict(inputs='/candidate', work='/work/out/rating-citations/version', timeout=60, mounts=(), cancel=None)
        cases = [(replace(base, argv=('/usr/bin/true',)), {}), (replace(base, network=True), {}),
                 (replace(base, tool='other'), {}), (base, {'inputs': '/tmp'}),
                 (base, {'work': '/work/out/other/version'}), (base, {'mounts': (('/tmp', '/extra'),)}),
                 (replace(base, argv=('/usr/bin/pdftotext', '-layout', '/candidate/../secret', '/work/document.txt')),
                  {'work': '/work/out/rating-citations/pdf-0'}),
                 (replace(base, argv=('/usr/bin/pdftotext', '-layout', '/candidate/a.pdf', '/tmp/out')),
                  {'work': '/work/out/rating-citations/pdf-0'})]
        with patch.object(native_run.Host, 'run', side_effect=AssertionError('must not launch')):
            for intent, updates in cases:
                with self.subTest(intent=intent, updates=updates), self.assertRaises((ValueError, OSError)):
                    adapter.run(intent, **{**options, **updates})

    def test_setup_time_and_cancellation_are_forwarded_without_reset(self):
        adapter = binding_fill._RatingStageExecution()
        intent = native_run.Intent('probe', ['/usr/bin/pdftotext', '-v'], timeout=60)
        cancel = native_run.Cancellation()
        kwargs = dict(inputs='/candidate', work='/work/out/rating-citations/version', timeout=9, mounts=(), cancel=cancel)
        with patch.object(binding_fill.time, 'monotonic', side_effect=[100, 103]), \
                patch.object(native_run.Host, 'run', return_value={'execution': 'interrupted'}) as host:
            self.assertEqual(adapter.run(intent, **kwargs)['execution'], 'interrupted')
            self.assertEqual(host.call_args.kwargs['timeout'], 6)
            self.assertIs(host.call_args.kwargs['cancel'], cancel)
        with patch.object(binding_fill.time, 'monotonic', side_effect=[100, 110]), \
                patch.object(native_run.Host, 'run', side_effect=AssertionError('must not launch')), \
                self.assertRaises(native_run.DeadlineExhausted):
            adapter.run(intent, **kwargs)

    def test_adapter_context_resets_on_success_failure_and_cancellation(self):
        sentinel = object()
        with native_run.using(sentinel):
            with patch.object(binding_fill, 'main', side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
                binding_fill._stage_main(self.args)
            self.assertIs(native_run._active.get(), sentinel)
            with patch.object(binding_fill, 'main', return_value=1):
                self.assertEqual(binding_fill._stage_main(self.args), 1)
            self.assertIs(native_run._active.get(), sentinel)
        self.assertIsNone(native_run._active.get())

    def test_standalone_citations_keep_sandbox_and_temporary_lifetime(self):
        roots = []
        def sandbox(intent, *, inputs, work, **kwargs):
            self.assertEqual(intent.isolation, 'sandbox')
            roots.append(work.parent)
            if '-layout' in intent.argv:
                intent = replace(intent, argv=(intent.argv[0], intent.argv[1], str(self.root / self.selected['path']), str(work / 'document.txt')),
                                 isolation='host', env={'HOME': str(work / 'home'), 'KICAD_CONFIG_HOME': str(work / 'kicad-config')}, cwd=str(work))
            else:
                intent = replace(intent, isolation='host', env={'HOME': str(work / 'home'), 'KICAD_CONFIG_HOME': str(work / 'kicad-config')}, cwd=str(work))
            return self.host(intent, inputs=None, work=work, timeout=60, mounts=(), cancel=None)
        class SandboxOnly:
            run = staticmethod(sandbox)
        with native_run.using(SandboxOnly()):
            status, deps = passive_ratings.verify_citations(self.root, [self.fact], self.tool)
        self.assertEqual(status['power'][0], 'verified')
        self.assertEqual(deps, {self.selected['path']: self.selected['sha256']})
        self.assertTrue(roots)
        self.assertTrue(all(not root.exists() for root in roots))

    def test_catalog_only_and_no_rating_stage_artifacts_remain_appropriate(self):
        self.ratings.update(facts=None, extractor=None)
        write_json(self.root / 'ratings.json', self.ratings)
        self.assertEqual(self.stage(), 0)
        self.assertTrue((self.work / 'out/rating-citations').is_dir())
        request = read_json(self.work / 'out/rating-citations/request.json')
        self.assertEqual(request, {'declaration': 'ratings.json', 'sha256': digest(self.root / 'ratings.json')})
        from agent_ee.stage_receipts import _artifact_observation
        artifacts, tree = _artifact_observation({'artifacts': ['out/rating-citations']}, self.work)
        self.assertTrue(artifacts)
        self.assertNotIn(None, artifacts.values())
        self.assertEqual(tree['out/rating-citations']['kind'], 'directory')
        self.assertEqual(self.calls, [])
        # Another fresh output root: ordinary non-rating source migration needs no artifact.
        self.work = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.assertEqual(self.stage(self.args[:-2] + ['--source-migration']), 0)
        self.assertFalse((self.work / 'out/rating-citations').exists())
        self.assertEqual(read_json(self.work / 'out/binding-fill.json')['schema'], 'agent-ee.binding-fill/2')

    def test_source_pdf_hash_value_quote_and_page_refusals_stay_enforced(self):
        for change, reason in [('sha', 'pdf_hash_mismatch'), ('value', 'value_absent_from_quote'),
                               ('page', 'page_out_of_range'), ('quote', 'quote_absent_from_page')]:
            fact = copy.deepcopy(self.fact)
            if change == 'sha': fact['source']['sha256'] = '0' * 64
            elif change == 'value': fact['value'] = '50'
            elif change == 'page': fact['source']['page'] = 2
            else: fact['source']['quote'] = 'Other power 100 mW'
            scratch = self.work / change
            def mock(intent, *, inputs, work, **kwargs):
                child = replace(intent, isolation='host', env={'HOME': str(work / 'home'), 'KICAD_CONFIG_HOME': str(work / 'kicad-config')}, cwd=str(work))
                if '-layout' in intent.argv:
                    child = replace(child, argv=(intent.argv[0], intent.argv[1], str(self.root / self.selected['path']), str(work / 'document.txt')))
                return self.host(child, inputs=None, work=work, timeout=60, mounts=(), cancel=None)
            class Mock:
                run = staticmethod(mock)
            with self.subTest(change=change), native_run.using(Mock()):
                found, _ = passive_ratings.verify_citations(self.root, [fact], self.tool, _work=scratch)
                self.assertEqual(found['power'][1], reason)

    def test_pdf_mutation_and_post_extraction_extractor_change_refuse(self):
        original = self.host
        def changed(intent, **kwargs):
            result = original(intent, **kwargs)
            if '-layout' in intent.argv:
                (self.root / self.selected['path']).write_bytes(b'changed CPU_MOCK_ONLY')
            return result
        with patch.object(native_run.Host, 'run', side_effect=changed):
            self.assertEqual(binding_fill._stage_main(self.args), 1)
        self.assertIn('changed its captured PDF', read_json(self.work / 'out/binding-fill.json')['reason'])
        self.assertTrue((self.work / 'out/rating-citations/pdf-0/execution.json').is_file())
        # The real verifier still observes extractor identity again after citations.
        self.work = Path(self.enterContext(tempfile.TemporaryDirectory()))
        with patch.object(datasheet_facts, 'digest', side_effect=[self.tool['sha256'], '0' * 64]), \
                patch.object(native_run.Host, 'run', side_effect=original):
            with native_run.using(binding_fill._RatingStageExecution()), self.assertRaisesRegex(ValueError, 'extractor|pdftotext hash changed'):
                passive_ratings.verify_citations(self.root, [self.fact], self.tool, _work=self.work / 'out/rating-citations')
        self.assertTrue((self.work / 'out/rating-citations/version/execution.json').is_file())

    def test_configuration_declares_only_rating_enabled_artifact_and_private_entry(self):
        source = self.work / 'owned-tool-source'
        source.mkdir()
        (source / 'fixture.py').write_text('# CPU_MOCK_ONLY source declaration fixture\n')
        with patch.object(binding_fill, 'snapshot', return_value=source):
            for ratings in (None, 'ratings.json'):
                for migration in (False, True):
                    spec = binding_fill.configuration(source, intent='intent.json', manifest='parts.json',
                        part_refs='refs.json', deviations='deviations.json', captures='.', dependencies=[],
                        ratings=ratings, source_migration=migration)
                    self.assertIn('from agent_ee.binding_fill import _stage_main; raise SystemExit(_stage_main())', spec['argv'])
                    self.assertEqual(spec['artifacts'], ['out/binding-fill.json'] +
                        (['out/rating-citations'] if ratings else []))
                    self.assertEqual(spec['writes'], {'intent.json': 'out/intent.json'})

    def test_child_cancellation_keeps_partial_record_and_resets_adapter(self):
        original = self.host
        def cancelled(intent, **kwargs):
            original(intent, **kwargs)
            write_json(kwargs['work'] / 'execution.json', {'execution': 'interrupted', 'exit_code': None})
            raise KeyboardInterrupt('CPU_MOCK_ONLY cancellation')
        with patch.object(native_run.Host, 'run', side_effect=cancelled), self.assertRaises(KeyboardInterrupt):
            binding_fill._stage_main(self.args)
        self.assertIsNone(native_run._active.get())
        self.assertEqual(read_json(self.work / 'out/rating-citations/version/execution.json')['execution'], 'interrupted')

    def test_extractor_identity_refuses_before_child_and_exact_version_mismatch_is_retained(self):
        bad = {**self.tool, 'sha256': '0' * 64}
        with self.assertRaisesRegex(ValueError, 'pdftotext hash changed'):
            passive_ratings.verify_citations(self.root, [self.fact], bad, _work=self.work / 'initial-refusal')
        self.assertFalse((self.work / 'initial-refusal').exists())
        original = self.host
        def mismatch(intent, **kwargs):
            result = original(intent, **kwargs)
            (kwargs['work'] / 'console.log').write_text('pdftotext version OTHER_CPU_MOCK_ONLY\n')
            return result
        with patch.object(native_run.Host, 'run', side_effect=mismatch):
            self.assertEqual(binding_fill._stage_main(self.args), 1)
        self.assertIn('; version_mismatch;', read_json(self.work / 'out/binding-fill.json')['reason'])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(read_json(self.work / 'out/rating-citations/version/execution.json')['exit_code'], 0)

    def test_adapter_refuses_symlink_scratch_and_captured_member(self):
        adapter = binding_fill._RatingStageExecution()
        intent = native_run.Intent('probe', ['/usr/bin/pdftotext', '-v'])
        (self.work / 'out').mkdir()
        (self.work / 'out/rating-citations').symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'fixed candidate'):
            adapter.run(intent, inputs='/candidate', work='/work/out/rating-citations/version',
                        timeout=60, mounts=(), cancel=None)
        (self.work / 'out/rating-citations').unlink()
        (self.root / 'link.pdf').symlink_to(self.root / self.selected['path'])
        intent = replace(intent, argv=('/usr/bin/pdftotext', '-layout', '/candidate/link.pdf', '/work/document.txt'))
        with self.assertRaisesRegex(ValueError, 'symlink'):
            adapter.run(intent, inputs='/candidate', work='/work/out/rating-citations/pdf-0',
                        timeout=60, mounts=(), cancel=None)
