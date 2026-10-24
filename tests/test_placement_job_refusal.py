"""Terminal refusal evidence begins only after job-directory ownership."""
import json
import os
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
        self.assertEqual(set(report['phases']), {'setup'})

    def test_setup_refusal_after_ownership_has_null_attempt(self):
        config = self.config()
        config['inputs']['board']['sha256'] = '0' * 64
        out = self.dir / 'job'
        report = job.run_job(config, out)
        self.assert_report(out, report, None, 'does not match its declared sha256')

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


if __name__ == '__main__':
    unittest.main()
