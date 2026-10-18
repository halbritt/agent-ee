"""Exercise the real coordinator and sandbox tools with scripted agent responses.

These tests establish orchestration contracts, not live-model review quality or
qualification of the synthetic measurement fixture as a physical simulator.
"""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_ee.campaign import run_campaign, verify_campaign, campaign_feedback
from agent_ee.cli import run_review, verify
from agent_ee.empirical import bench_review
from agent_ee.storage import digest, read_json, write_json

from toolchain import toolchain_test


class CampaignIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        source = self.root / "source"
        source.mkdir()
        for name in ("board", "schematic", "requirements", "targets", "reference", "model"):
            (source / name).write_text(name)
        write_json(source / "design.json", {"value": 15})
        (source / "solver.py").write_text('from pathlib import Path\nimport json\nvalue=json.loads(Path("/candidate/design/design.json").read_text())["value"]\nPath("raw.json").write_text(json.dumps({"value":value}))\n')
        (source / "adapter.py").write_text('''import json, sys
from pathlib import Path
request = json.loads(Path(sys.argv[1]).read_text())
value = json.loads(Path("/work/tool/raw.json").read_text())["value"]
report = {"schema": "agent-ee.measurements/1", "candidate_id": request["candidate_id"],
 "files": request["files"], "tool": "test-protocol", "version": "1", "domain": "electrical",
 "artifacts": ["tool/raw.json"], "metrics": {"output": {"value": value, "unit": "V", "condition": "load"}}}
Path(sys.argv[2]).write_text(json.dumps(report))
''')
        definition = {"harness": "codex", "model": "test", "effort": "high", "timeout_s": 10}
        self.profile = {"schema": "agent-ee.profile/1", "name": "protocol-test",
            "reviewers": [{**definition, "role": "circuit"}, {**definition, "role": "physical"}],
            "adjudicator": definition}
        self.scope = {"schema": "agent-ee.scope/2", "name": "protocol", "scope": "protocol integration test",
            "board": "design/board", "schematic": "design/schematic", "mutable_inputs": ["design/design.json"],
            "inputs": [{"path": str(source), "at": "design"}], "review_profile": self.profile,
            "checks": [{"id": "response", "kind": "measurements", "params": {
                "adapter": "design/adapter.py", "tool": {"argv": ["/usr/bin/python3", "/candidate/design/solver.py"],
                "sha256": digest("/usr/bin/python3"), "domain": "electrical", "inputs": ["design/solver.py"]}, "calibration_inputs": ["design/model"],
                "limits": {"output": {"unit": "V", "condition": "load", "min": 0, "max": 10}}}}],
            "baseline": {"requirements": [{"id": "R1", "statement": "Output in [0,10] V at load",
                "source": {"path": "design/requirements", "locator": "R1"}, "checks": ["response"]}],
                "targets": ["design/targets"], "reference_architectures": ["design/reference"]}}
        write_json(self.root / "scope.json", self.scope)
        write_json(self.root / "campaign.json", {"schema": "agent-ee.campaign/1", "scope": "scope.json",
            "review_profile": self.profile, "implementation": definition, "max_iterations": 3, "max_seconds": 60})
        self.calls = []
        self.addCleanup(patch.stopall)
        patch('agent_ee.reviewers.dispatch', side_effect=self.agent).start()
        # Avoid platform fingerprint probes; sandbox tool execution is real.
        patch('agent_ee.cli.runtime_files', return_value={name: {'path': '/usr/bin/true', 'sha256': digest('/usr/bin/true')}
            for name in ('codex', 'codex_tool_host', 'bwrap', '/usr/bin/python3')}).start()

    def agent(self, run, label, definition, prompt, schema, timeout, *, cancellation=None, executable=None):
        run = Path(run)
        self.calls.append(label)
        if label == "implementation":
            write_json(run / "review-work/implementation/design/design/design.json", {"value": 5})
            return {"execution": "ok"}, {"summary": "Changed synthetic fixture value to 5"}
        packet = read_json(run / "packet.json")
        identity = {k: packet[k] for k in ("candidate_id", "run_id")}
        if label == "adjudicator":
            checks = [read_json(p) for p in (run / "checks").glob('*.json')]
            decisions = [{"finding_id": "check/" + c["id"], "disposition": "accepted", "reason": "R1 failed",
                "evidence": [{"path": "checks/" + c["id"] + ".json", "locator": "outcome"}]}
                for c in checks if c["outcome"] == "defect"]
            return {"execution": "ok"}, {"schema": "agent-ee.decisions/1", **identity, "decisions": decisions}
        self.assertNotIn('"initial_reviews"', prompt)
        self.assertIn('baseline', prompt)
        return {"execution": "ok"}, {"schema": "agent-ee.review/1", **identity,
            "packet_sha256": packet["packet_sha256"], "role": label,
            "coverage": ["protocol"], "limitations": ["scripted test response"], "findings": []}

    @toolchain_test
    def test_adjudicator_can_cite_verified_baseline(self):
        def answer(*args, **kwargs):
            record, value = self.agent(*args, **kwargs)
            if args[1] == 'adjudicator':
                value['decisions'][0]['evidence'].append({'path': 'baseline.json', 'locator': 'requirements/R1'})
            return record, value
        with patch('agent_ee.reviewers.dispatch', side_effect=answer), contextlib.redirect_stdout(io.StringIO()):
            run_review(self.root / 'scope.json', self.root / 'run', 30)
        result = verify(self.root / 'run')
        self.assertTrue(result['adjudicated'])
        self.assertEqual(result['findings'][0]['status'], 'accepted')
        execution = read_json(self.root / 'run/adjudicator-execution.json')
        self.assertEqual(execution['resubmissions']['used'], 0)

    def malformed_adjudication(self, *, recover=False, limit=1, drift=False, failed=False, changed_packet=False, policy=False, cancel_second=False):
        prompts, schemas = [], []
        from agent_ee.cli import runtime_files
        original_runtime = runtime_files()
        runtime_patch = patch('agent_ee.cli.runtime_files', return_value={**original_runtime, 'drift': {}})
        self.addCleanup(runtime_patch.stop)

        def answer(*args, **kwargs):
            record, value = self.agent(*args, **kwargs)
            if args[1] == 'adjudicator':
                prompts.append(args[3])
                schemas.append(args[4])
                if cancel_second and len(prompts) == 2:
                    raise KeyboardInterrupt
                if failed:
                    return {'execution': 'failed', 'error': 'subscription authentication/quota failure'}, None
                if not recover or len(prompts) == 1:
                    # An existing run file outside the citation allowlist is still refused.
                    write_json(Path(args[0]) / 'outside.json', {'unsealed': True})
                    value['decisions'][0]['evidence'] = [{'path': 'outside.json', 'locator': 'unsealed'}]
                    if drift:
                        runtime_patch.start()
                    if changed_packet:
                        packet = read_json(Path(args[0]) / 'packet.json')
                        packet['unexpected'] = 'changed evidence'
                        write_json(Path(args[0]) / 'packet.json', packet)
                if policy:
                    value['decisions'][0].update(disposition='dismissed',
                        evidence=[{'path': 'checks/response.json', 'locator': 'outcome'}])
                # The scripted provider has real networkless execution artifacts.
                from agent_ee.executor import execute
                raw = execute(Path(args[0]) / 'inputs', Path(args[0]) / 'review-work/adjudicator',
                    ['/usr/bin/python3', '-c', 'import sys; print(sys.stdin.read())'],
                    stdin=json.dumps(value), timeout=5)
                self.assertEqual(raw['execution'], 'ok')
                record.update(raw)
            return record, value
        with patch('agent_ee.reviewers.dispatch', side_effect=answer), contextlib.redirect_stdout(io.StringIO()):
            run_review(self.root / 'scope.json', self.root / 'run', 30, output_resubmissions=limit)
        runtime_patch.stop()
        return read_json(self.root / 'run/result.json'), prompts, schemas

    @toolchain_test
    def test_malformed_adjudication_resubmits_exact_error_and_retains_refusal(self):
        result, prompts, schemas = self.malformed_adjudication(recover=True)
        self.assertTrue(result['adjudicated'])
        run = self.root / 'run'
        execution = read_json(run / 'adjudicator-execution.json')
        self.assertEqual(execution['resubmissions']['limit'], 1)
        self.assertEqual(execution['resubmissions']['used'], 1)
        first = read_json(run / 'review-executions/adjudicator-calls/0.json')
        second = read_json(run / 'review-executions/adjudicator-calls/1.json')
        self.assertEqual(first['execution_record']['execution'], 'malformed')
        self.assertEqual(first['execution_record']['refusal_class'], 'output_format')
        self.assertEqual(first['response']['decisions'][0]['evidence'][0]['path'], 'outside.json')
        self.assertIn('baseline.json', first['execution_record']['error'])
        self.assertEqual(prompts[1], prompts[0] + '\nVALIDATION ERROR:\n' + first['execution_record']['error'])
        self.assertEqual(schemas[0], schemas[1])
        receipts = [read_json(run / c['execution_record']['preflight']['path']) for c in (first, second)]
        self.assertNotEqual(receipts[0]['invocation_id'], receipts[1]['invocation_id'])
        self.assertEqual(receipts[0]['expectation_id'], receipts[1]['expectation_id'])
        self.assertEqual(receipts[0]['observed']['runtime_files'], receipts[1]['observed']['runtime_files'])
        self.assertEqual(read_json(run / 'review-work/.refused/adjudicator/0/stdout.log'), first['response'])
        self.assertEqual(read_json(run / 'review-work/adjudicator/stdout.log'), second['response'])
        from agent_ee.harnesses import evidence_mounts
        self.assertFalse(any('.refused' in str(source) for source, _ in evidence_mounts(run, 'adjudicator')))
        from agent_ee.findings import validate_citation_path
        with self.assertRaises(ValueError):
            validate_citation_path('review-work/.refused/adjudicator/0/stdout.log', run)
        self.assertEqual(result, verify(run))
        from agent_ee.review_run import recorded_calls
        execution['resubmissions']['used'] = 0
        with self.assertRaisesRegex(ValueError, 'inventory'):
            recorded_calls(run, execution, 'adjudicator')
        (run / second['prompt']).write_text(prompts[1] + '\nchanged input')
        execution['resubmissions']['used'] = 1
        with self.assertRaisesRegex(ValueError, 'changed prompt'):
            recorded_calls(run, execution, 'adjudicator')
        with self.assertRaisesRegex(ValueError, 'evidence changed'):
            verify(run)

    @toolchain_test
    def test_outside_citations_stay_incomplete_after_second_refusal(self):
        result, prompts, _ = self.malformed_adjudication()
        self.assertEqual(len(prompts), 2)
        self.assertEqual(result['outcome'], 'incomplete')
        self.assertFalse(result['adjudicated'])
        self.assertFalse((self.root / 'run/events/0001-adjudication.json').exists())
        execution = read_json(self.root / 'run/adjudicator-execution.json')
        self.assertEqual(execution['execution'], 'malformed')
        self.assertEqual(execution['resubmissions']['used'], 1)
        self.assertEqual(result, verify(self.root / 'run'))

    @toolchain_test
    def test_disabled_resubmissions_and_provider_failure_do_not_retry(self):
        result, prompts, _ = self.malformed_adjudication(limit=0)
        self.assertEqual(len(prompts), 1)
        self.assertEqual(result['outcome'], 'incomplete')
        self.assertEqual(read_json(self.root / 'run/adjudicator-execution.json')['resubmissions']['used'], 0)
        # A separate run tests a provider failure with the default bound.
        (self.root / 'run').rename(self.root / 'disabled-run')
        result, prompts, _ = self.malformed_adjudication(failed=True)
        self.assertEqual(len(prompts), 1)
        self.assertEqual(result['outcome'], 'incomplete')
        self.assertEqual(read_json(self.root / 'run/adjudicator-execution.json')['execution'], 'failed')

    @toolchain_test
    def test_runtime_drift_blocks_resubmission_dispatch(self):
        result, prompts, _ = self.malformed_adjudication(recover=True, drift=True)
        self.assertEqual(len(prompts), 1)
        self.assertEqual(result['outcome'], 'incomplete')
        self.assertFalse(result['adjudicated'])
        execution = read_json(self.root / 'run/adjudicator-execution.json')
        self.assertEqual(execution['execution'], 'not_run')
        self.assertIn('runtime_drift', execution['error'])
        self.assertEqual(execution['resubmissions']['used'], 1)

    @toolchain_test
    def test_changed_packet_blocks_resubmission_and_retains_original_refusal(self):
        result, prompts, _ = self.malformed_adjudication(recover=True, changed_packet=True)
        self.assertEqual(len(prompts), 1)
        self.assertEqual(result['outcome'], 'incomplete')
        execution = read_json(self.root / 'run/adjudicator-execution.json')
        self.assertEqual(execution['resubmissions']['used'], 0)
        self.assertEqual(execution['resubmission_refusal'], 'resubmission packet changed')
        self.assertIn('outside.json', execution['error'])

    @toolchain_test
    def test_unsupported_judgment_is_refused_without_resubmission(self):
        result, prompts, _ = self.malformed_adjudication(policy=True)
        self.assertEqual(len(prompts), 1)
        self.assertEqual(result['outcome'], 'incomplete')
        execution = read_json(self.root / 'run/adjudicator-execution.json')
        self.assertEqual(execution['resubmissions']['used'], 0)
        self.assertEqual(execution['refusal_class'], 'judgment_or_evidence')
        self.assertIn('cannot be dismissed', execution['error'])

    @toolchain_test
    def test_interrupted_resubmission_retains_both_calls_and_count(self):
        result, prompts, _ = self.malformed_adjudication(recover=True, cancel_second=True)
        self.assertEqual(len(prompts), 2)
        self.assertEqual(result['state'], 'interrupted')
        self.assertFalse(result['adjudicated'])
        run = self.root / 'run'
        execution = read_json(run / 'adjudicator-execution.json')
        self.assertEqual(execution['execution'], 'interrupted')
        self.assertEqual(execution['resubmissions']['used'], 1)
        self.assertEqual(read_json(run / 'review-executions/adjudicator-calls/0.json')['execution_record']['execution'], 'malformed')
        self.assertEqual(result, verify(run))

    def test_campaign_refuses_invalid_resubmission_bound_before_execution(self):
        for limit in (True, -1, 2, '1', None):
            with self.subTest(limit=limit):
                config = read_json(self.root / 'campaign.json')
                config['output_resubmissions'] = limit
                write_json(self.root / 'campaign.json', config)
                with self.assertRaisesRegex(ValueError, 'output_resubmissions'):
                    run_campaign(self.root / 'campaign.json', self.root / 'invalid-campaign')
        self.assertEqual(self.calls, [])

    @toolchain_test
    def test_single_writer_repair_then_fresh_independent_reviews(self):
        with contextlib.redirect_stdout(io.StringIO()):
            status = run_campaign(self.root / "campaign.json", self.root / "campaign")
        state = read_json(self.root / "campaign/state.json")
        self.assertEqual(read_json(self.root / 'campaign/config.json')['output_resubmissions'], 1)
        self.assertEqual(status, 0, state)
        self.assertEqual(state["state"], "criteria_satisfied")
        self.assertCountEqual(self.calls[:2], ["circuit", "physical"])
        self.assertEqual(self.calls[2:4], ["adjudicator", "implementation"])
        self.assertCountEqual(self.calls[4:6], ["circuit", "physical"])
        self.assertEqual(self.calls[6:], ["adjudicator"])
        self.assertEqual(len(state["history"]), 2)
        self.assertNotEqual(state["history"][0]["candidate_id"], state["history"][1]["candidate_id"])
        self.assertEqual(read_json(self.root / "source/design.json")["value"], 15)
        self.assertEqual(verify(self.root / "campaign/runs/2")["outcome"], "criteria_satisfied")
        self.assertEqual(verify_campaign(self.root / "campaign")["state"], "criteria_satisfied")
        path = self.root / "campaign/runs/1/result.json"
        altered = read_json(path)
        altered["annotation"] = "changed after campaign consumed this run"
        write_json(path, altered)
        with self.assertRaisesRegex(ValueError, "history result link"):
            verify_campaign(self.root / "campaign")

    @toolchain_test
    def test_campaign_bench_epoch_repairs_without_erasing_original_history(self):
        self.scope['checks'][0]['params']['limits']['aux'] = {'unit': 'V', 'condition': 'load', 'min': 0, 'max': 10}
        adapter = self.root / 'source/adapter.py'
        adapter.write_text(adapter.read_text().replace('"metrics": {"output":',
            '"metrics": {"aux": {"value": value, "unit": "V", "condition": "load"}, "output":'))
        write_json(self.root / 'scope.json', self.scope)
        write_json(self.root / "source/design.json", {"value": 5})
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_campaign(self.root / "campaign.json", self.root / "campaign"), 0)
        first = verify_campaign(self.root / "campaign")
        sample = {"check_id": "response", "metric": "output", "value": 12,
                  "uncertainty": 1, "unit": "V", "condition": "load"}
        write_json(self.root / "instrument.json", {"samples": [sample, {**sample, "metric": "aux"}]})
        write_json(self.root / "bench.json", {"schema": "agent-ee.bench/1",
            "candidate_id": first["history"][-1]["candidate_id"], "prototype": "fixture",
            "instrument": "test", "instrument_calibration": "test", "procedure": "test",
            "observed_utc": "2026-10-17T00:00:00Z", "artifacts": [{"path": "instrument.json",
                "sha256": digest(self.root / "instrument.json")}],
            "observations": [{**sample, "artifact": "instrument.json", "locator": "0"},
                             {**sample, "metric": "aux", "artifact": "instrument.json", "locator": "1"}]})
        def calibrated_repair(run, label, definition, prompt, schema, timeout, *, cancellation=None, executable=None):
            reply = self.agent(run, label, definition, prompt, schema, timeout)
            if label == "implementation":
                write_json(Path(run) / "review-work/implementation/design/design/design.json", {"value": 1})
            return reply
        with patch('agent_ee.reviewers.dispatch', side_effect=calibrated_repair):
            with contextlib.redirect_stdout(io.StringIO()):
                status = campaign_feedback(self.root / "campaign", self.root / "bench.json",
                                           self.root / "feedback", 60)
        state = verify_campaign(self.root / "campaign")
        self.assertEqual(status, 1, state)
        self.assertEqual(state['state'], 'incomplete')
        stale = verify(self.root / 'campaign/runs/3')
        self.assertEqual(stale['checks'][0]['execution'], 'unavailable')
        self.assertEqual(stale['checks'][0]['outcome'], 'unknown')
        self.assertEqual(stale['checks'][0]['details']['metrics']['output']['value'], 1)
        self.assertEqual(stale['findings'], [])
        sample.update(value=2, uncertainty=0)
        write_json(self.root / 'instrument.json', {'samples': [sample]})
        report = read_json(self.root / 'bench.json')
        report.update(candidate_id=stale['candidate_id'],
            artifacts=[{'path': 'instrument.json', 'sha256': digest(self.root / 'instrument.json')}],
            observations=[{**sample, 'artifact': 'instrument.json', 'locator': '0'}])
        write_json(self.root / 'bench.json', report)
        from agent_ee.empirical import ingest
        with self.assertRaisesRegex(ValueError, 'omits required correction obligations'):
            ingest(self.root / 'campaign/runs/3', self.root / 'bench.json', self.root / 'omitted-epoch')
        self.assertNotIn('empirical_feedback', verify(self.root / 'campaign/runs/3'))
        write_json(self.root / 'instrument.json', {'samples': [sample, {**sample, 'metric': 'aux'}]})
        report['artifacts'][0]['sha256'] = digest(self.root / 'instrument.json')
        report['observations'].append({**sample, 'metric': 'aux', 'artifact': 'instrument.json', 'locator': '1'})
        write_json(self.root / 'bench.json', report)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(campaign_feedback(self.root / 'campaign', self.root / 'bench.json',
                                               self.root / 'fresh-feedback', 60), 0)
        state = verify_campaign(self.root / 'campaign')
        self.assertEqual(state['state'], 'criteria_satisfied')
        self.assertEqual([h['epoch'] for h in state['history']], [0, 1, 1, 2])
        self.assertEqual(state['history'][0], first['history'][0])
        self.assertEqual(state['history'][1]['failed'], ['response'])
        fresh = verify(self.root / 'campaign/runs/4')
        metric = fresh['checks'][0]['details']['metrics']['output']
        self.assertEqual((metric['nominal'], metric['lower'], metric['upper']), (1, 2, 2))
        self.assertEqual(len(read_json(self.root / 'fresh-feedback/empirical/calibration.json')['observations']), 4)
        self.assertEqual(verify(self.root / 'campaign/runs/1')['outcome'], 'incomplete')
        self.assertEqual(verify(self.root / 'campaign/runs/3')['outcome'], 'incomplete')
        state["history"][2]["epoch"] = 2
        write_json(self.root / "campaign/state.json", state)
        with self.assertRaisesRegex(ValueError, "epoch lacks"):
            verify_campaign(self.root / "campaign")

    @toolchain_test
    def test_implementation_cannot_change_verification_inputs_or_add_files(self):
        for attack in ("modify", "plant"):
            with self.subTest(attack=attack):
                def attacking_agent(run, label, definition, prompt, schema, timeout, *, cancellation=None, executable=None):
                    reply = self.agent(run, label, definition, prompt, schema, timeout)
                    if label == "implementation":
                        design = Path(run) / "review-work/implementation/design/design"
                        target = design / ("model" if attack == "modify" else "planted-history.txt")
                        target.write_text("untrusted change")
                    return reply
                with patch('agent_ee.reviewers.dispatch', side_effect=attacking_agent):
                    with contextlib.redirect_stdout(io.StringIO()):
                        status = run_campaign(self.root / "campaign.json", self.root / attack)
                state = read_json(self.root / attack / "state.json")
                self.assertEqual(status, 1)
                self.assertEqual(state["state"], "incomplete")
                self.assertEqual(len(state["history"]), 1)
                self.assertIn("implementation changed", state["error"])

    @toolchain_test
    def test_retained_write_authority_cannot_be_broadened(self):
        write_json(self.root / "source/design.json", {"value": 5})
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_campaign(self.root / "campaign.json", self.root / "campaign"), 0)
        path = self.root / "campaign/state.json"
        state = read_json(path)
        state["mutable_inputs"].append("design/model")
        write_json(path, state)
        with self.assertRaisesRegex(ValueError, "captured write authority"):
            verify_campaign(self.root / "campaign")

    def test_legacy_missing_measurement_tool_has_handled_verification_refusal(self):
        from agent_ee.cli import main
        self.scope["schema"] = "agent-ee.scope/1"
        del self.scope["checks"][0]["params"]["tool"]
        write_json(self.root / "scope.json", self.scope)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_review(self.root / "scope.json", self.root / "legacy", 60, checks_only=True), 1)
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            self.assertEqual(main(["verify", str(self.root / "legacy")]), 2)
        self.assertIn("lacks a pinned tool declaration", error.getvalue())
        self.assertNotIn("Traceback", error.getvalue())

    @toolchain_test
    def test_bench_discrepancy_reenters_checks_and_adjudication(self):
        write_json(self.root / "source/design.json", {"value": 5})
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_review(self.root / "scope.json", self.root / "initial", 60), 0)
        original = read_json(self.root / "initial/result.json")
        sample = {"check_id": "response", "metric": "output", "value": 12,
                  "uncertainty": 1, "unit": "V", "condition": "load"}
        write_json(self.root / "instrument.json", {"samples": [sample]})
        report = {"schema": "agent-ee.bench/1", "candidate_id": original["candidate_id"],
            "prototype": "test-fixture", "instrument": "protocol-fixture", "instrument_calibration": "test-only",
            "procedure": "synthetic integration test", "observed_utc": "2026-10-17T00:00:00Z",
            "artifacts": [{"path": "instrument.json", "sha256": digest(self.root / "instrument.json")}],
            "observations": [{**sample, "artifact": "instrument.json", "locator": "0"}]}
        write_json(self.root / "bench.json", report)
        with contextlib.redirect_stdout(io.StringIO()):
            status = bench_review(self.root / "initial", self.root / "bench.json", self.root / "feedback", 60)
        self.assertEqual(status, 1)
        result = verify(self.root / "feedback/review")
        self.assertEqual(result["outcome"], "defects_or_unresolved")
        self.assertEqual(result["checks"][0]["details"]["margins"]["output"]["max"], -3)
        self.assertEqual(result["findings"][0]["status"], "accepted")
        bundle = read_json(self.root / "feedback/empirical/calibration.json")
        for observation in bundle["observations"]:
            facts = read_json(self.root / "feedback/review/inputs" / observation["source_result"])
            self.assertNotIn("reviews", facts)
            self.assertNotIn("findings", facts)
        self.assertNotEqual(result["candidate_id"], original["candidate_id"])
        self.assertEqual(verify(self.root / "initial")["outcome"], "incomplete")
        self.assertTrue(read_json(self.root / "initial/result.json")["empirical_feedback"])

        # A later near-nominal sample cannot erase the earlier larger bias.
        sample.update(value=6, uncertainty=0)
        write_json(self.root / "instrument.json", {"samples": [sample]})
        report.update(candidate_id=result["candidate_id"],
            artifacts=[{"path": "instrument.json", "sha256": digest(self.root / "instrument.json")}],
            observations=[{**sample, "artifact": "instrument.json", "locator": "0"}])
        write_json(self.root / "bench.json", report)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(bench_review(self.root / "feedback/review", self.root / "bench.json",
                                         self.root / "feedback2", 60), 1)
        second = verify(self.root / "feedback2/review")
        metric = second["checks"][0]["details"]["metrics"]["output"]
        self.assertEqual((metric["nominal"], metric["lower"], metric["upper"]), (5, 6, 13))
        self.assertEqual(len(second["checks"][0]["details"]["empirical_cases"]), 2)

    @toolchain_test
    def test_arbitrary_incomplete_campaign_cannot_use_bench_recovery(self):
        (self.root / 'source/adapter.py').write_text("raise RuntimeError('parser failure')")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_campaign(self.root / 'campaign.json', self.root / 'campaign'), 1)
        self.assertEqual(verify_campaign(self.root / 'campaign')['state'], 'incomplete')
        with self.assertRaisesRegex(ValueError, 'bench continuation requires'):
            campaign_feedback(self.root / 'campaign', self.root / 'absent-report.json',
                              self.root / 'refused-feedback', 60)
        self.assertFalse((self.root / 'refused-feedback').exists())

    @toolchain_test
    def test_reentry_preserves_unclassified_empirical_files_and_supersession_on_interrupt(self):
        write_json(self.root / 'source/design.json', {'value': 5})
        (self.root / 'unclassified').mkdir()
        (self.root / 'unclassified/stackup.txt').write_text('captured material dependency')
        self.scope['inputs'].append({'path': str(self.root / 'unclassified'), 'at': 'empirical'})
        write_json(self.root / 'scope.json', self.scope)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_review(self.root / 'scope.json', self.root / 'initial', 60), 0)
        original = verify(self.root / 'initial')
        sample = {'check_id': 'response', 'metric': 'output', 'value': 5,
                  'uncertainty': 0, 'unit': 'V', 'condition': 'load'}
        write_json(self.root / 'raw.json', {'samples': [sample]})
        report = {'schema': 'agent-ee.bench/1', 'candidate_id': original['candidate_id'],
            'prototype': 'fixture', 'instrument': 'fixture', 'instrument_calibration': 'fixture',
            'procedure': 'synthetic coordinator test', 'observed_utc': '2026-10-17',
            'artifacts': [{'path': 'raw.json', 'sha256': digest(self.root / 'raw.json')}],
            'observations': [{**sample, 'artifact': 'raw.json', 'locator': '0'}]}
        write_json(self.root / 'bench.json', report)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(bench_review(self.root / 'initial', self.root / 'bench.json',
                                         self.root / 'feedback', 60), 0)
        captured = self.root / 'feedback/review/inputs/empirical/stackup.txt'
        self.assertEqual(captured.read_text(), 'captured material dependency')
        fresh = verify(self.root / 'feedback/review')
        report['candidate_id'] = fresh['candidate_id']
        write_json(self.root / 'bench.json', report)
        with patch('agent_ee.cli.run_review', side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            bench_review(self.root / 'feedback/review', self.root / 'bench.json', self.root / 'interrupted', 60)
        self.assertEqual(verify(self.root / 'feedback/review')['outcome'], 'incomplete')
        self.assertTrue((self.root / 'interrupted/empirical/calibration.json').is_file())
        self.assertEqual(len(read_json(self.root / 'interrupted/empirical/calibration.json')['observations']), 2)

    @toolchain_test
    def test_interrupted_supersession_retry_refuses_before_new_epoch(self):
        self.test_reentry_preserves_unclassified_empirical_files_and_supersession_on_interrupt()
        run = self.root / 'feedback/review'
        before = read_json(run / 'result.json')
        events = {p.name: digest(p) for p in (run / 'events').iterdir()}
        retained = digest(self.root / 'interrupted/empirical/calibration.json')
        for attempt in ('retry', 'retry-again'):
            with self.assertRaisesRegex(ValueError, 'superseded.*recovery'):
                bench_review(run, self.root / 'bench.json', self.root / attempt, 60)
            self.assertFalse((self.root / attempt / 'empirical/calibration.json').exists())
            self.assertFalse((self.root / attempt / 'review').exists())
            self.assertEqual(read_json(run / 'result.json'), before)
            self.assertEqual({p.name: digest(p) for p in (run / 'events').iterdir()}, events)
            self.assertEqual(digest(self.root / 'interrupted/empirical/calibration.json'), retained)

    @toolchain_test
    def test_ingest_rejects_duplicate_corrections_before_publication(self):
        from agent_ee.empirical import ingest
        self.test_reentry_preserves_unclassified_empirical_files_and_supersession_on_interrupt()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_review(self.root / 'scope.json', self.root / 'new-source', 60), 0)
        run = self.root / 'new-source'
        before = read_json(run / 'result.json')
        report = read_json(self.root / 'bench.json')
        report['candidate_id'] = before['candidate_id']
        report['observations'] *= 2
        write_json(self.root / 'duplicate-bench.json', report)
        with self.assertRaisesRegex(ValueError, 'duplicate correction identity'):
            ingest(run, self.root / 'duplicate-bench.json', self.root / 'invalid-epoch')
        self.assertFalse((self.root / 'invalid-epoch/calibration.json').exists())
        self.assertEqual(read_json(run / 'result.json'), before)

    @toolchain_test
    def test_scope3_changed_design_requires_and_recovers_with_fresh_bench_epoch(self):
        from agent_ee.capabilities import convert_scope
        self.scope = convert_scope(self.scope)
        write_json(self.root / 'scope.json', self.scope)
        self.test_campaign_bench_epoch_repairs_without_erasing_original_history()

    @toolchain_test
    def test_scope3_campaign_preserves_audited_baseline_and_single_writer(self):
        from agent_ee.capabilities import convert_scope
        self.scope = convert_scope(self.scope)
        write_json(self.root / 'scope.json', self.scope)
        self.test_single_writer_repair_then_fresh_independent_reviews()
        first = read_json(self.root / 'campaign/runs/1/baseline.json')
        second = read_json(self.root / 'campaign/runs/2/baseline.json')
        self.assertEqual(first['schema'], 'agent-ee.baseline/2')
        self.assertEqual(first, second)
        self.assertEqual(first['coverage']['checks']['response']['immutable_inputs'],
                         ['design/adapter.py', 'design/model', 'design/solver.py'])

    @toolchain_test
    def test_scope3_implementation_cannot_change_captured_verification_dependencies(self):
        from agent_ee.capabilities import convert_scope
        self.scope = convert_scope(self.scope)
        write_json(self.root / 'scope.json', self.scope)
        self.test_implementation_cannot_change_verification_inputs_or_add_files()

    @toolchain_test
    def test_implementation_drift_refuses_writer_with_original_scratch_context(self):
        from agent_ee.campaign import implement as real_implement
        from agent_ee.cli import runtime_files
        original_runtime = runtime_files()
        def drift(root, previous, definition, state, deadline):
            with patch('agent_ee.cli.runtime_files', return_value={**original_runtime, 'drift': {}}):
                return real_implement(root, previous, definition, state, deadline)
        with patch('agent_ee.campaign.implement', side_effect=drift), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_campaign(self.root / 'campaign.json', self.root / 'campaign'), 1)
        self.assertNotIn('implementation', self.calls)
        work = self.root / 'campaign/implementations/1'
        record = read_json(work / 'implementation-execution.json')
        self.assertEqual(record['execution'], 'not_run')
        self.assertIn('runtime_drift', record['error'])
        self.assertFalse((work / 'proposal.scope.json').exists())
        self.assertFalse((work / 'candidate.json').exists())
        self.assertFalse((work / 'result.json').exists())
        self.assertEqual(read_json(work / 'review-work/implementation/design/design/design.json'), {'value': 15})
        self.assertEqual(read_json(self.root / 'campaign/state.json')['state'], 'incomplete')
        verify_campaign(self.root / 'campaign')

    @toolchain_test
    def test_adjudication_drift_has_zero_adjudicator_dispatches(self):
        from agent_ee.reviewers import adjudicator_call as real_adjudicator
        from agent_ee.cli import runtime_files
        original_runtime = runtime_files()
        def drift(*args, **kwargs):
            with patch('agent_ee.cli.runtime_files', return_value={**original_runtime, 'drift': {}}):
                return real_adjudicator(*args, **kwargs)
        with patch('agent_ee.reviewers.adjudicator_call', side_effect=drift), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_review(self.root / 'scope.json', self.root / 'run', 30), 1)
        self.assertCountEqual(self.calls, ['circuit', 'physical'])
        result = verify(self.root / 'run')
        self.assertEqual(result['outcome'], 'incomplete')
        self.assertFalse(result['adjudicated'])
        record = read_json(self.root / 'run/adjudicator-execution.json')
        self.assertIn('runtime_drift', record['error'])

    @toolchain_test
    def test_drift_after_checks_prevents_all_initial_dispatches(self):
        from agent_ee.checks import run_checks as real_checks
        from agent_ee.cli import runtime_files
        original_runtime = runtime_files()
        # The deterministic check completes normally; subsequent observation sees drift.
        runtime_patch = patch('agent_ee.cli.runtime_files', return_value={**original_runtime, 'drift': {}})
        def checks(*args, **kwargs):
            result = real_checks(*args, **kwargs)
            runtime_patch.start()
            self.addCleanup(runtime_patch.stop)
            return result
        with patch('agent_ee.checks.run_checks', side_effect=checks), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_review(self.root / 'scope.json', self.root / 'run', 30), 1)
        self.assertEqual(self.calls, [])
        result = read_json(self.root / 'run/result.json')
        self.assertEqual(result['checks'][0]['execution'], 'ok')
        self.assertEqual(result['outcome'], 'incomplete')
        self.assertTrue(any('runtime_drift' in record.get('error', '') for record in result['review_executions']))

    @toolchain_test
    def test_campaign_verification_requires_separate_implementation_receipt(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_campaign(self.root / 'campaign.json', self.root / 'campaign'), 0)
        root = self.root / 'campaign'
        verify_campaign(root)
        receipt = root / 'implementations/1/review-work/implementation/preflight/receipt.json'
        receipt.unlink()
        with self.assertRaises((ValueError, OSError)):
            verify_campaign(root)

    @toolchain_test
    def test_tampered_initial_receipt_prevents_adjudication_and_completion(self):
        def tamper(*args, **kwargs):
            record, response = self.agent(*args, **kwargs)
            run, label = Path(args[0]), args[1]
            if label == 'circuit':
                path = run / 'review-work/circuit/preflight/receipt.json'
                receipt = read_json(path)
                receipt['baseline_id'] = 'foreign'
                write_json(path, receipt)
            return record, response
        with patch('agent_ee.reviewers.dispatch', side_effect=tamper), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_review(self.root / 'scope.json', self.root / 'run', 30), 1)
        self.assertNotIn('adjudicator', self.calls)
        result = read_json(self.root / 'run/result.json')
        self.assertEqual(result['outcome'], 'incomplete')
        self.assertIn('preflight receipt changed', result['error'])
