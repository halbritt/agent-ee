"""Native package binding and real networkless DFM parsing on original fixtures."""
import copy
import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from agent_ee import checks, jlc_dfm
from agent_ee.baseline import compile_baseline, freeze_evidence, load_baseline, validate_witnesses, verify_evidence
from agent_ee.candidate import capture
from agent_ee.capabilities import describe_check
from agent_ee.findings import collect_findings
from agent_ee.preflight import capture_expectation
from agent_ee.storage import digest, read_json, write_json
from tests.test_fab_package import generation_scope
from tests.toolchain import require_toolchain

FIXTURE = Path(__file__).resolve().parents[1] / 'fixtures/jlc-dfm'
SPEC = {'id': 'dfm', 'kind': 'jlc_dfm', 'params': {'fab_check': 'fab', 'report': 'dfm/report.json'}}
FINDING = {'id': 'synthetic-check-1', 'severity': 'error', 'message': 'Synthetic manufacturing fault',
           'page': 1, 'locator': 'synthetic-check-1'}


class ReportFormatTests(unittest.TestCase):
    def test_original_fixture_and_located_findings_parse(self):
        doc = read_json(FIXTURE / 'report.json')
        doc['findings'] = [dict(FINDING)]
        self.assertEqual(jlc_dfm.parse_report(json.dumps(doc).encode()), doc)

    def test_malformed_and_incomplete_reports_are_refused(self):
        doc = read_json(FIXTURE / 'report.json')
        cases = [[], {**doc, 'extra': 1}, {**doc, 'schema': 'unknown'},
                 {**doc, 'supplier': 'other'}, {**doc, 'status': 'pending'},
                 {**doc, 'gerber_zip_sha256': ''}, {**doc, 'gerber_zip_sha256': 'A' * 64},
                 {**doc, 'source': {'path': '../secret', 'sha256': '0' * 64}},
                 {**doc, 'source': {'path': '/tmp/secret', 'sha256': '0' * 64}},
                 {**doc, 'findings': {}}, {**doc, 'findings': [FINDING, FINDING]},
                 *({**doc, 'findings': [{**FINDING, key: value}]} for key, value in
                   [('severity', 'danger'), ('severity', []), ('page', True), ('page', 0),
                    ('page', 1.5), ('message', ''), ('locator', ' '), ('id', None)])]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                jlc_dfm.parse_report(json.dumps(case).encode())
        for data in (b'', b'\xff', b'{', b'{"schema":1,"schema":2}',
                     b'{"page":NaN}', b'[' * 1100):
            with self.subTest(data=data[:50]), self.assertRaises(ValueError):
                jlc_dfm.parse_report(data)

    def test_stage_driver_preserves_package_with_dfm_tail(self):
        from agent_ee.rung1_stages import done_gate, stage_checks, StageRefusal
        scope = generation_scope('agent-ee.scope/3')
        scope['checks'].append(copy.deepcopy(SPEC))
        self.assertEqual(done_gate(scope), scope)
        self.assertEqual(stage_checks(scope)['fab'], ['fab', 'dfm'])
        scope['checks'].append({'id': 'late', 'kind': 'geometry'})
        with self.assertRaises(StageRefusal):
            done_gate(scope)

    def test_strict_params_paths_and_no_other_tail_checks(self):
        for params in ({}, {**SPEC['params'], 'url': 'https://example.invalid'},
                       {**SPEC['params'], 'report': '../report'},
                       {**SPEC['params'], 'fab_check': '../fab'},
                       {**SPEC['params'], 'report': 'dfm//report.json'}):
            with self.subTest(params=params), self.assertRaises(ValueError):
                jlc_dfm.validate_params(params)
        from agent_ee.fab_package import require_last
        gate = {'id': 'fab', 'kind': 'fab_package'}
        require_last([gate, SPEC], gate)
        for tail in ({**SPEC, 'kind': 'geometry'},
                     {**SPEC, 'params': {**SPEC['params'], 'fab_check': 'other'}}):
            with self.assertRaises(ValueError):
                require_last([gate, tail], gate)


class NativeBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)
        cls.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-jlc-dfm-')
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.root = Path(cls.tmp.name)
        seed = cls.root / 'seed'
        seed.mkdir()
        write_json(seed / 'scope.json', generation_scope('agent-ee.scope/3'))
        (seed / 'run').mkdir()
        capture(seed / 'scope.json', seed / 'run')
        results = checks.run_checks(seed / 'run', time.monotonic() + 300)
        if (results[-1]['execution'], results[-1]['outcome']) != ('ok', 'pass'):
            raise AssertionError(results)
        freeze_evidence(seed / 'run', results)
        verify_evidence(seed / 'run', results, required=True)
        cls.zip_sha = digest(seed / 'run/work/fab/fab/gerbers.zip')

    def setUp(self):
        self.case = self.root / self._testMethodName
        self.case.mkdir()
        self.source = self.case / 'source'
        shutil.copytree(FIXTURE, self.source)
        self.doc = read_json(self.source / 'report.json')
        self.doc['gerber_zip_sha256'] = self.zip_sha
        self.scope = generation_scope('agent-ee.scope/3')
        self.scope['checks'].append(copy.deepcopy(SPEC))
        self.scope['inputs'].append({'path': str(self.source), 'at': 'dfm',
                                     'include': ['report.json', 'saved-report.txt']})
        req = self.scope['baseline']['requirements'][0]
        self.scope['baseline']['requirements'].append({**req, 'id': 'R-dfm',
            'statement': 'The uploaded package has no error-level DFM findings', 'checks': ['dfm']})

    def capture_case(self):
        write_json(self.source / 'report.json', self.doc)
        write_json(self.case / 'scope.json', self.scope)
        self.run = self.case / 'run'
        self.run.mkdir()
        return capture(self.case / 'scope.json', self.run)

    def run_case(self, fault=None, *, seal=True):
        self.capture_case()
        function = checks.CHECK_FUNCS['jlc_dfm']
        def inject(ctx):
            if fault:
                fault(ctx)
            return function(ctx)
        with mock.patch.dict(checks.CHECK_FUNCS, {'jlc_dfm': inject}):
            results = checks.run_checks(self.run, time.monotonic() + 300)
        self.assertEqual((results[-2]['execution'], results[-2]['outcome']), ('ok', 'pass'), results)
        if seal:
            freeze_evidence(self.run, results)
            verify_evidence(self.run, results, required=True)
        self.results = results
        return results[-1]

    def test_clean_report_bound_to_real_sealed_zip_and_sandbox(self):
        result = self.run_case()
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        self.assertEqual(result['details']['binding'], 'matched')
        self.assertEqual(result['details']['gerber_zip_sha256'], self.zip_sha)
        self.assertEqual(result['details']['report_sha256'], digest(self.source / 'saved-report.txt'))
        self.assertEqual(result['details']['normalization_sha256'], digest(self.source / 'report.json'))
        execution = read_json(self.run / 'work/dfm/execution.json')
        self.assertIn('--unshare-net', execution['sandbox_argv'])
        self.assertEqual(execution['exit_code'], 0)
        self.assertEqual((self.run / 'work/dfm/saved-report').read_bytes(),
                         (self.source / 'saved-report.txt').read_bytes())
        baseline = read_json(self.run / 'baseline.json')
        self.assertIn('dfm/report.json', baseline['files'])
        self.assertIn('dfm/saved-report.txt', baseline['files'])
        expected = capture_expectation({'run_id': 'test', 'policy_files': {}, 'runtime_files': {}},
                                       read_json(self.run / 'candidate.json'), [], inputs=self.run / 'inputs')
        self.assertEqual(expected['required_runtime'], ['/usr/bin/python3', 'bwrap', 'kicad-cli', 'pcbnew'])
        (self.run / 'work/dfm/saved-report').write_bytes(b'tampered retained report')
        with self.assertRaisesRegex(ValueError, 'evidence seal'):
            verify_evidence(self.run, self.results, required=True)

    def test_error_is_a_sealed_coordinator_defect_with_witness(self):
        self.doc['findings'] = [dict(FINDING)]
        result = self.run_case()
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)
        finding = result['details']['findings'][0]
        self.assertEqual(finding['report_sha256'], digest(self.source / 'saved-report.txt'))
        self.assertIn('page 1 synthetic-check-1', result['location'])
        validate_witnesses(self.run, [{'requirement_id': 'R-dfm', 'check_id': 'dfm'}],
                           [{'path': 'checks/dfm.json', 'locator': result['location']}])
        findings = collect_findings([], self.results)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]['source'], 'check')

    def test_warning_and_info_are_advisory_with_limitations(self):
        self.doc['findings'] = [{**FINDING, 'severity': 'warning'},
                                {**FINDING, 'id': 'note', 'severity': 'info'}]
        result = self.run_case()
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        self.assertEqual(result['details']['warnings'], 1)
        self.assertTrue(any('advisory' in text for text in result['limitations']))
        self.assertEqual(collect_findings([], self.results), [])

    def test_different_zip_is_unknown_even_when_report_has_errors(self):
        self.doc['gerber_zip_sha256'] = 'a' * 64
        self.doc['findings'] = [dict(FINDING)]
        result = self.run_case()
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)
        self.assertEqual(result['details']['binding'], 'mismatch')
        self.assertEqual(collect_findings([], self.results), [])

    def test_missing_normalization_is_unknown(self):
        result = self.run_case(lambda ctx: (ctx.inputs / 'dfm/report.json').unlink(), seal=False)
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)
        with self.assertRaisesRegex(ValueError, 'unsupported capability dfm'):
            load_baseline(self.run)

    def test_missing_source_is_unknown(self):
        result = self.run_case(lambda ctx: (ctx.inputs / 'dfm/saved-report.txt').unlink())
        self.assertEqual((result['execution'], result['outcome']), ('malformed', 'unknown'), result)

    def test_replaced_source_is_unknown(self):
        result = self.run_case(lambda ctx: (ctx.inputs / 'dfm/saved-report.txt').write_bytes(b'changed'))
        self.assertEqual((result['execution'], result['outcome']), ('malformed', 'unknown'), result)

    def test_source_digest_mismatch_is_unknown(self):
        self.doc['source']['sha256'] = 'b' * 64
        result = self.run_case()
        self.assertEqual((result['execution'], result['outcome']), ('malformed', 'unknown'), result)

    def test_pending_report_is_unknown(self):
        self.doc['status'] = 'pending'
        result = self.run_case()
        self.assertEqual((result['execution'], result['outcome']), ('malformed', 'unknown'), result)

    def test_unknown_severity_is_unknown(self):
        self.doc['findings'] = [{**FINDING, 'severity': 'danger'}]
        result = self.run_case()
        self.assertEqual((result['execution'], result['outcome']), ('malformed', 'unknown'), result)

    def test_changed_normalization_is_unknown(self):
        def fault(ctx):
            doc = read_json(ctx.inputs / 'dfm/report.json')
            doc['gerber_zip_sha256'] = 'a' * 64
            write_json(ctx.inputs / 'dfm/report.json', doc)
        result = self.run_case(fault)
        self.assertEqual((result['execution'], result['outcome']), ('malformed', 'unknown'), result)

    def test_wrong_candidate_package_is_unknown(self):
        def fault(ctx):
            ctx.manifest = {**ctx.manifest, 'candidate_id': 'c' * 64}
        result = self.run_case(fault)
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)

    def test_changed_zip_is_unknown(self):
        result = self.run_case(lambda ctx: (ctx.run_dir / 'work/fab/fab/gerbers.zip').write_bytes(b'broken'))
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)

    def test_changed_artifact_record_is_unknown_and_seal_refuses_tampering(self):
        def fault(ctx):
            path = ctx.run_dir / 'checks/fab.json'
            record = read_json(path)
            record['details']['artifacts']['artifacts_id'] = 'e' * 64
            write_json(path, record)
        result = self.run_case(fault, seal=False)
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)
        with self.assertRaisesRegex(ValueError, 'check differs from retained result'):
            freeze_evidence(self.run, self.results)

    def copy_fault(self, name, contents):
        execute = checks.Ctx.execute
        def damage_copy(ctx, *args, **kwargs):
            result = execute(ctx, *args, **kwargs)
            if ctx.cid == 'dfm':
                if contents is None:
                    (ctx.work / name).unlink()
                else:
                    (ctx.work / name).write_bytes(contents)
            return result
        with mock.patch.object(checks.Ctx, 'execute', damage_copy):
            result = self.run_case()
        self.assertEqual((result['execution'], result['outcome']), ('malformed', 'unknown'), result)
        self.assertIn('retained DFM copy missing or changed', result['summary'])

    def test_missing_retained_source_copy_is_unknown(self):
        self.copy_fault('saved-report', None)

    def test_missing_retained_normalization_copy_is_unknown(self):
        self.copy_fault('report.json', None)

    def test_changed_retained_source_copy_is_unknown(self):
        self.copy_fault('saved-report', b'changed report copy')

    def test_changed_retained_normalization_copy_is_unknown(self):
        self.copy_fault('report.json', b'changed normalization copy')

    def test_missing_parser_output_is_unknown(self):
        execute = checks.Ctx.execute
        def remove_output(ctx, *args, **kwargs):
            result = execute(ctx, *args, **kwargs)
            if ctx.cid == 'dfm':
                (ctx.work / 'parsed.json').unlink()
            return result
        with mock.patch.object(checks.Ctx, 'execute', remove_output):
            result = self.run_case()
        self.assertEqual((result['execution'], result['outcome']), ('malformed', 'unknown'), result)

    def test_malformed_parser_output_is_unknown(self):
        execute = checks.Ctx.execute
        def corrupt_output(ctx, *args, **kwargs):
            result = execute(ctx, *args, **kwargs)
            if ctx.cid == 'dfm':
                write_json(ctx.work / 'parsed.json', {'execution': 'ok', 'facts': {}})
            return result
        with mock.patch.object(checks.Ctx, 'execute', corrupt_output):
            result = self.run_case()
        self.assertEqual((result['execution'], result['outcome']), ('malformed', 'unknown'), result)

    def test_parser_crash_is_unknown(self):
        execute = checks.Ctx.execute
        def crash(ctx, argv, *args, **kwargs):
            if ctx.cid == 'dfm':
                argv = ['/usr/bin/python3', '-c', 'raise RuntimeError("injected parser crash")']
            return execute(ctx, argv, *args, **kwargs)
        with mock.patch.object(checks.Ctx, 'execute', crash):
            result = self.run_case()
        self.assertEqual(result['outcome'], 'unknown', result)
        self.assertNotEqual(result['execution'], 'ok', result)

    def test_scope3_capability_freezes_both_files_and_orders_prerequisite(self):
        self.capture_case()
        manifest = read_json(self.run / 'candidate.json')
        specs = self.scope['checks']
        def describe(spec=specs[-1], order=specs):
            return describe_check(spec, check_specs=order, files=manifest['files'],
                                  scope={**self.scope, 'checks': order}, inputs=self.run / 'inputs')
        good = describe()
        self.assertEqual(good['status'], 'supported', good)
        self.assertTrue(good['can_evaluate_bound'])
        self.assertEqual(good['immutable_inputs'], ['dfm/report.json', 'dfm/saved-report.txt'])
        self.assertEqual(good['prerequisites'], [{'check_id': 'fab', 'kind': 'fab_package'}])
        self.assertEqual(describe(order=[specs[-1], *specs[:-1]])['status'], 'unsupported')
        scope2 = copy.deepcopy(self.scope)
        scope2['schema'] = 'agent-ee.scope/2'
        for req in scope2['baseline']['requirements']:
            del req['intent']
        baseline = compile_baseline(scope2, manifest['files'], self.run / 'inputs')
        self.assertIn('dfm/report.json', baseline['files'])
        self.assertIn('dfm/saved-report.txt', baseline['files'])
        from agent_ee.campaign import mutable_inputs
        manifest['scope']['mutable_inputs'] = ['dfm/report.json']
        with self.assertRaisesRegex(ValueError, 'immutable baseline'):
            mutable_inputs(manifest, load_baseline(self.run))


if __name__ == '__main__':
    unittest.main()
