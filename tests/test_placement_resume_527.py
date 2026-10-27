"""AGENTEE-527 controls for resume custody and an exhausted job budget."""
import copy
import json
import shutil

from agent_ee import placement_job as job
import test_placement_job as fixture
from test_placement_job_refusal import snapshot
from toolchain import require_kicad_route


class ResumeIdentityTests(fixture.Fixture):
    def setUp(self):
        super().setUp()
        require_kicad_route()

    def test_changed_required_intent_or_engine_preserves_the_retained_job(self):
        config = fixture.EntryPointTests._config(self)
        executable = self.dir / 'engine'
        shutil.copy2(job.rust_executable(), executable)
        out = self.dir / 'placement'
        out.mkdir()
        (out / 'CANCEL').touch()
        first = job.run_job(config, out, executable=executable)
        self.assertEqual(first['status'], 'cancelled')
        self.assertEqual(first['attempt'], 1)
        (out / 'CANCEL').unlink()
        retained = snapshot(out)

        intent_path = self.dir / 'intent.json'
        for changed in ('intent', 'engine'):
            with self.subTest(changed=changed):
                resumed_config = copy.deepcopy(config)
                path = intent_path if changed == 'intent' else executable
                original = path.read_bytes()
                self.addCleanup(path.write_bytes, original)
                path.write_bytes(original + b'\n')
                if changed == 'intent':
                    resumed_config['inputs']['intent']['sha256'] = job.digest(path)
                try:
                    with self.assertRaisesRegex(job.JobRefusal, 'different job identity'):
                        job.run_job(resumed_config, out, resume=True, executable=executable)
                    self.assertEqual(snapshot(out), retained)
                finally:
                    path.write_bytes(original)

    def test_exhausted_budget_keeps_acceptance_false_without_grading_evidence(self):
        config = fixture.EntryPointTests._config(self)
        config['budget_s'] = 1e-9
        out = self.dir / 'no-grade-budget'
        report = job.run_job(config, out)
        self.assertEqual(report['status'], 'deadline')
        self.assertEqual(report['claims'], {'accepted': False, 'clean': False, 'superiority': None})
        self.assertNotIn('final', report)
        self.assertNotIn('eligibility', report)
        self.assertFalse((out / 'incumbent.json').exists())
        self.assertEqual(json.loads((out / 'job-report.json').read_text()), report)
