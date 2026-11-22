"""Terminal refusal evidence begins only after job-directory ownership."""
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from agent_ee import placement_job as job
import test_placement_job as fixture
from toolchain import require_kicad_route


def snapshot(directory):
    return {p.relative_to(directory).as_posix(): p.read_bytes()
            for p in directory.rglob('*') if p.is_file()}


class NativeLengthRefusalTests(unittest.TestCase):
    def test_only_native_length_certification_failure_is_a_rule_refusal(self):
        record = dict(native_operation_schema='kicad-route/native-operation/1', status='no_candidate',
                      router_exit=3, router_status='unroutable',
                      reason='length rule "timing": cannot certify net "/A[0]" within 123 nm '
                             '(constructed upper bound 456 nm)')
        self.assertTrue(job._native_length_refusal(record))
        for difference in [dict(reason='no legal path'), dict(reason='length rule "timing" is unsupported'),
                           dict(router_status='budget_exhausted', router_exit=4),
                           dict(status='backend_failure'), dict(native_operation_schema=None)]:
            with self.subTest(difference=difference):
                self.assertFalse(job._native_length_refusal({**record, **difference}))


class RefusalReportTests(fixture.Fixture):
    def setUp(self):
        super().setUp()
        require_kicad_route()

    def config(self):
        return fixture.EntryPointTests._config(self)

    def refusing_engine(self):
        path = self.dir / 'refusing-engine'
        path.write_text('#!/usr/bin/python3\nimport json,sys\njson.load(sys.stdin)\n'
                        'print(json.dumps({"error":"forced engine refusal"}))\nsys.exit(1)\n')
        path.chmod(0o755)
        return path

    def assert_report(self, out, report, attempt, reason):
        self.assertEqual(report['status'], 'refused')
        self.assertIn(reason, report['reason'])
        self.assertEqual(report['attempt'], attempt)
        self.assertEqual(report['claims'], {'accepted': False, 'clean': False, 'superiority': None})
        self.assertNotIn('eligibility', report)
        self.assertNotIn('accepted', report)
        self.assertNotIn('clean', report)
        self.assertGreaterEqual(report['elapsed_s'], 0)
        self.assertEqual(json.loads((out / 'job-report.json').read_text()), report)
        if attempt is not None:
            self.assertEqual(json.loads((out / f'job-report-a{attempt}.json').read_text()), report)
            self.assertIn('identity', report)
        else:
            self.assertEqual(list(out.glob('job-report-a*.json')), [])
        if 'preflight' in report:
            self.assertTrue({'engine', 'engine_sha256', 'router', 'kicad_cli', 'pcbnew_version'} <= report['preflight'].keys())

    def test_engine_refusal_has_attempt_identity_phases_and_false_claims(self):
        out = self.dir / 'job'
        report = job.run_job(self.config(), out, executable=self.refusing_engine())
        self.assert_report(out, report, 1, 'forced engine refusal')
        self.assertEqual(set(report['phases']), {'setup', 'import', 'search'})
        self.assertFalse((out / 'incumbent.json').exists())
        self.assertFalse((out / 'best-diagnostic.json').exists())

    def test_capture_refusal_writes_report_without_inventing_attempt(self):
        fixture.build_board(self.board, malformed_courtyard=True)
        out = self.dir / 'job'
        report = job.run_job(self.config(), out)
        self.assert_report(out, report, None, 'malformed courtyard')
        self.assertEqual(set(report['phases']), {'setup', 'import'})

    def test_child_design_refusal_has_no_environment_class_and_returns_author_feedback(self):
        from agent_ee import design_generate
        config = self.config()
        declaration = fixture.intent()
        declaration['proximity'] = [dict(ref='R1', near='C1', max_mm=3,
                                        pads={'R1': ['99'], 'C1': ['1']})]
        path = Path(config['inputs']['intent']['path'])
        path.write_text(json.dumps(declaration))
        config['inputs']['intent']['sha256'] = job.digest(path)
        build = self.dir / 'build'
        out = build / 'stages/placement-routing/placement'
        report = job.run_job(config, out)
        message = 'intent.proximity[0].pads.R1: selected pad is missing'
        self.assert_report(out, report, None, message)
        self.assertEqual(report['reason'], message)
        self.assertNotIn('failure_kind', report)
        self.assertNotIn('failing_stage', report)
        findings = design_generate.placement_findings(build)
        self.assertIsNone(design_generate.environment_failure(findings))
        feedback = design_generate.phase_feedback('placement-routing', 'placement/routing did not pass: refused',
                                                  dict(placement_findings=findings))
        self.assertIn(message, feedback)

    def test_cli_refusal_summary_and_nonzero_exit_for_capture_and_engine(self):
        for phase in ('engine', 'capture'):
            with self.subTest(phase=phase):
                env = dict(os.environ)
                if phase == 'engine':
                    env['AGENT_EE_RUST_PLANNER'] = str(self.refusing_engine())
                else:
                    fixture.build_board(self.board, malformed_courtyard=True)
                config = self.dir / 'config.json'
                config.write_text(json.dumps(self.config()))
                out = self.dir / phase
                done = subprocess.run([sys.executable, '-m', 'agent_ee.placement_job', 'run', str(config),
                                       '--out', str(out)], env=env, capture_output=True, text=True, timeout=30)
                self.assertEqual(done.returncode, 1, done.stderr)
                self.assertEqual(json.loads(done.stdout)['status'], 'refused')
                report = json.loads((out / 'job-report.json').read_text())
                self.assert_report(out, report, 1 if phase == 'engine' else None,
                                   'forced engine refusal' if phase == 'engine' else 'malformed courtyard')

    def test_preflight_refusal_keeps_available_runtime_identities(self):
        config = self.config()
        config['router']['checkout'] = str(self.dir / 'missing-router')
        out = self.dir / 'job'
        report = job.run_job(config, out)
        self.assert_report(out, report, None, 'runtime preflight failed')
        self.assertEqual((report['failure_kind'], report['failing_stage']), ('environment', 'runtime-preflight'))
        self.assertEqual(set(report['phases']), {'setup'})

    def test_job_only_external_native_checkout_refuses_before_placement(self):
        from agent_ee import routing_adapter
        checkout = Path(require_kicad_route())
        if not routing_adapter.is_rust_capable_checkout(checkout):
            self.skipTest('requires a Rust-capable checkout for host executable ownership')
        # Use the same clean source and actual native executable at a different
        # location. Only the operator's checkout selection differs.
        alternate = self.dir / 'external-checkout'
        subprocess.run(['git', 'clone', '--quiet', '--local', '--no-hardlinks', str(checkout), str(alternate)],
                       check=True, timeout=60)
        head = subprocess.check_output(['git', '-C', str(checkout), 'rev-parse', 'HEAD'], text=True).strip()
        subprocess.run(['git', '-C', str(alternate), 'checkout', '--quiet', '--detach', head],
                       check=True, timeout=60)
        executable = alternate / 'rust-engine/target/release/kicad-route-engine'
        executable.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(routing_adapter.resolve_kicad_route_engine(checkout), executable)
        config = self.config()
        config['router']['checkout'] = str(alternate)
        config_path = self.dir / 'external-job.json'
        config_path.write_text(json.dumps(config))
        out = self.dir / 'external-job'
        env = os.environ.copy()
        for variable in ('AGENT_EE_KICAD_ROUTE_CHECKOUT', 'AGENT_EE_KICAD_ROUTE_ENGINE', 'KICAD_ROUTE_ENGINE_BIN'):
            env.pop(variable, None)
        result = subprocess.run([sys.executable, '-m', 'agent_ee.cli', 'place', 'run', str(config_path),
                                 '--out', str(out)], env=env, capture_output=True, text=True, timeout=90)
        self.assertNotEqual(result.returncode, 0)
        report = json.loads((out / 'job-report.json').read_text())
        self.assert_report(out, report, None, 'untrusted kicad-route-engine path')
        self.assertEqual((report['failure_kind'], report['failing_stage']), ('environment', 'runtime-preflight'))
        self.assertEqual(report['preflight']['router']['commit'], head)
        self.assertTrue(report['preflight']['router']['clean'])
        self.assertEqual(set(report['phases']), {'setup'})
        self.assertFalse(list(out.glob('route-a*')))

    def test_missing_planner_is_a_runtime_failure_with_a_retained_report(self):
        out = self.dir / 'job'
        report = job.run_job(self.config(), out, executable=self.dir / 'missing-planner')
        self.assertEqual((report['status'], report['failure_kind'], report['failing_stage']),
                         ('refused', 'environment', 'runtime-preflight'))
        self.assertIn('missing-planner', report['reason'])
        self.assertEqual(json.loads((out / 'job-report.json').read_text()), report)

    def test_native_planner_crash_is_an_environment_failure_without_more_search(self):
        executable = self.dir / 'crashing-planner'
        executable.write_text('#!/usr/bin/python3\nimport os,signal\nos.kill(os.getpid(), signal.SIGKILL)\n')
        executable.chmod(0o755)
        out = self.dir / 'job'
        report = job.run_job(self.config(), out, executable=executable)
        self.assertEqual((report['status'], report['failure_kind'], report['failing_stage']),
                         ('refused', 'environment', 'native-tool'))
        self.assertIn('crashed (exit -9)', report['reason'])
        self.assertEqual(json.loads((out / 'job-report.json').read_text()), report)
        self.assertFalse((out / 'candidates').exists())

    def test_drc_execution_and_report_errors_are_tool_failures(self):
        binary_dir = self.dir / 'bin'
        binary_dir.mkdir()
        executable = binary_dir / 'kicad-cli'
        for name, code in (
                ('failed', 'import sys; sys.exit(1)'),
                ('missing', ''),
                ('invalid', 'from pathlib import Path; import sys; Path(sys.argv[-2]).write_text("invalid")'),
                ('uncounted', 'from pathlib import Path; import sys; Path(sys.argv[-2]).write_text("{}")')):
            with self.subTest(name=name):
                executable.write_text('#!/usr/bin/python3\n' + code + '\n')
                executable.chmod(0o755)
                out = self.dir / name
                out.mkdir()
                with patch.dict(os.environ, PATH=str(binary_dir) + os.pathsep + os.environ['PATH']):
                    with self.assertRaises(job.EnvironmentFailure) as caught:
                        job.drc(self.board, out)
                self.assertEqual(caught.exception.stage, 'native-tool')
                self.assertIn('native DRC', str(caught.exception))
                self.assertTrue((out / 'drc.log').is_file())

    def test_setup_refusal_after_ownership_has_null_attempt(self):
        config = self.config()
        config['inputs']['board']['sha256'] = '0' * 64
        out = self.dir / 'job'
        report = job.run_job(config, out)
        self.assert_report(out, report, None, 'does not match its declared sha256')

    def test_preownership_environment_failure_is_reported_without_touching_foreign_files(self):
        out = self.dir / 'foreign-runtime'
        out.mkdir()
        (out / 'sentinel').write_bytes(b'foreign input')
        before = snapshot(out)
        config = self.config()
        config['router']['checkout'] = str(self.dir / 'missing-router')
        report = job.run_job(config, out, resume=True)
        self.assertEqual((report['status'], report['failure_kind'], report['failing_stage']),
                         ('refused', 'environment', 'runtime-preflight'))
        self.assertEqual(snapshot(out), before)

    def test_nonempty_directory_is_byte_identical_without_resume(self):
        out = self.dir / 'foreign'
        out.mkdir()
        (out / 'capture.json').write_bytes(b'foreign capture\x00')
        (out / 'job-report-a1.json').write_text('foreign report')
        before = snapshot(out)
        with self.assertRaisesRegex(job.JobRefusal, 'not empty'):
            job.run_job(self.config(), out)
        self.assertEqual(snapshot(out), before)

    def test_foreign_resume_and_missing_checkpoint_preserve_every_file(self):
        for checkpoint in (False, True):
            with self.subTest(checkpoint=checkpoint):
                out = self.dir / f'foreign-{checkpoint}'
                (out / 'candidates').mkdir(parents=True)
                for name in ('capture.json', 'problem.json', 'incumbent.json', 'best-diagnostic.json',
                             'job-report.json', 'job-report-a1.json', 'candidates/saved.kicad_pcb'):
                    (out / name).write_bytes(b'foreign content\x00')
                if checkpoint:
                    (out / 'checkpoint.json').write_text(json.dumps(
                        {'schema': job.CHECKPOINT_SCHEMA, 'identity': 'foreign', 'attempts': 1}))
                before = snapshot(out)
                with self.assertRaisesRegex(job.JobRefusal, 'different job identity|requires an identity-bound checkpoint'):
                    job.run_job(self.config(), out, resume=True)
                self.assertEqual(snapshot(out), before)

    def test_interrupted_resume_before_identity_preserves_foreign_files(self):
        out = self.dir / 'foreign-interrupted'
        out.mkdir()
        for name in ('capture.json', 'problem.json', 'job-report.json', 'job-report-a1.json'):
            (out / name).write_bytes(b'foreign bytes')
        before = snapshot(out)
        for status in ('deadline', 'cancelled'):
            with patch.object(job, 'preflight', side_effect=job.JobStopped(status)):
                report = job.run_job(self.config(), out, resume=True)
            self.assertEqual(report['status'], status)
            self.assertEqual(snapshot(out), before)

    def test_changed_router_identity_cannot_reuse_placement_checkpoint(self):
        out = self.dir / 'runtime-change'
        out.mkdir()
        (out / 'CANCEL').touch()
        config = self.config()
        first = job.run_job(config, out)
        self.assertEqual(first['status'], 'cancelled')
        (out / 'CANCEL').unlink()
        before = snapshot(out)
        preflight = job.preflight
        def changed(*args, **kwargs):
            record = preflight(*args, **kwargs)
            record['router']['commit'] = 'different-router-build'
            return record
        with patch.object(job, 'preflight', side_effect=changed):
            with self.assertRaisesRegex(job.JobRefusal, 'different job identity'):
                job.run_job(config, out, resume=True)
        self.assertEqual(snapshot(out), before)

    def test_refusal_on_owned_resume_preserves_incumbent_diagnostic_and_old_reports(self):
        out = self.dir / 'job'
        out.mkdir()
        (out / 'CANCEL').touch()
        config, executable = self.config(), self.refusing_engine()
        first = job.run_job(config, out, executable=executable)
        self.assertEqual(first['status'], 'cancelled')
        (out / 'CANCEL').unlink()
        for name in ('incumbent.json', 'best-diagnostic.json'):
            (out / name).write_text(json.dumps({'preserved': name}))
        (out / 'job-report-a99.json').write_text('old attempt')
        protected = {name: (out / name).read_bytes() for name in (
            'incumbent.json', 'best-diagnostic.json', 'job-report-a1.json', 'job-report-a99.json')}
        report = job.run_job(config, out, resume=True, executable=executable)
        self.assert_report(out, report, 2, 'forced engine refusal')
        self.assertEqual({name: (out / name).read_bytes() for name in protected}, protected)

    def test_invalid_grid_step_is_refused_before_routing(self):
        for index, value in enumerate((0, -.025, True, '0.025')):
            with self.subTest(value=value):
                config = self.config()
                config['router']['grid_step_mm'] = value
                report = job.run_job(config, self.dir / f'job-grid-refusal-{index}')
                self.assertEqual(report['status'], 'refused')
                self.assertIn('grid_step_mm', report['reason'])
                self.assertNotIn('route', report)

    def test_bind_native_board_attempt_classifies_exit2_exit6_and_exit8(self):
        from agent_ee import kicad_route_attempt as attempt, routing_adapter
        src = self.dir / 'src.kicad_pcb'
        src.write_text('(kicad_pcb)\n')
        src_sha = job.digest(src)
        out_board = self.dir / 'out.kicad_pcb'
        req_file = self.dir / 'operation-request.json'
        req_file.write_text('{"schema":"kicad-route/native-request/1"}\n')
        req_sha = job.digest(req_file)
        for label, exit_code, status, exp_status, exp_env in (
            ('exit2_invalid', 2, 'invalid', 'router_refused', False),
            ('exit2_unsupported', 2, 'unsupported', 'router_refused', False),
            ('exit8_usage', 8, 'usage_refused', 'router_refused', True),
            ('exit6_timeout', 6, 'operation_timeout', 'no_candidate', False),
        ):
            with self.subTest(label=label):
                op_path = self.dir / f'{label}-op-result.json'
                op_path.write_text(json.dumps({
                    'schema': attempt.NATIVE_OPERATION_SCHEMA,
                    'version': 1,
                    'exit_code': exit_code,
                    'status': status,
                    'reason': f'{status} detail',
                    'request': {'request_sha256': req_sha},
                    'source': {'source_sha256': src_sha, 'sidecar_check': {'passed': True}},
                    'publication': {'published': False, 'ambiguous_termination': False, 'output_sha256': None},
                }))
                argv = ['kicad-route-engine', 'route-native', str(src), str(out_board),
                        '--operation-result', str(op_path), '--save-request', str(req_file)]
                bound = routing_adapter.bind_board_attempt(self.dir, argv, exit_code)
                self.assertEqual(bound['status'], exp_status, bound)
                if exp_env:
                    self.assertEqual((bound.get('failure_kind'), bound.get('failing_stage')),
                                     ('environment', 'routing'))
                else:
                    self.assertNotIn('failure_kind', bound)
                    self.assertNotIn('failing_stage', bound)


if __name__ == '__main__':
    unittest.main()
