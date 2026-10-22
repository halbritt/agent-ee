import contextlib
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "action_gate.py"
spec = importlib.util.spec_from_file_location("action_gate", SCRIPT)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def check(cid, execution="ok", outcome="pass", summary="native check summary"):
    return {"id": cid, "kind": "erc" if cid == "erc" else "rc_transient", "candidate_id": "c" * 64,
            "execution": execution, "outcome": outcome, "summary": summary,
            "evidence": [], "details": {}, "limitations": []}


class ActionGateTests(unittest.TestCase):
    """The consumer action fails only on a check defect or incomplete coverage in result.json."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.run_dir = Path(tmp.name) / "run"
        self.run_dir.mkdir()
        # Shape of an actual checks-only result: never complete, outcome incomplete, exit 1.
        self.result = {"schema": "agent-ee.result/1", "run_id": "r", "candidate_id": "c" * 64,
            "state": "incomplete", "outcome": "incomplete", "adjudicated": False,
            "started_utc": "2026-10-17T00:00:00+00:00", "finished_utc": "2026-10-17T00:01:00+00:00",
            "expected_check_ids": ["erc", "rc-step"], "checks": [check("erc"), check("rc-step")],
            "reviews": [], "review_executions": [], "findings": [],
            "limitations": ["Checks-only run: independent agent reviews and adjudication not performed."]}

    def write(self):
        (self.run_dir / "result.json").write_text(json.dumps(self.result))

    def gate(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = gate.main([str(self.run_dir), *args])
        return code, out.getvalue()

    def test_all_checks_passing_succeeds_despite_checks_only_exit_one(self):
        self.write()
        (self.run_dir / "REPORT.md").write_text("# RC review\n\nOutcome: **incomplete**\n")
        code, text = self.gate("--review-exit", "1", "--verify-exit", "0")
        self.assertEqual(code, 0)
        self.assertEqual(gate.judge(self.run_dir)["verdict"], "checks_passed")
        self.assertIn("checks_passed", text)
        self.assertIn("exited 1", text)
        self.assertIn("# RC review", text)

    def test_check_defect_fails_and_appears_in_summary(self):
        self.result["checks"][1] = check("rc-step", outcome="defect", summary="vout_1ms=0.095 V outside 0.62..0.65")
        self.write()
        code, text = self.gate()
        self.assertEqual(code, 1)
        self.assertEqual(gate.judge(self.run_dir)["verdict"], "check_failed")
        self.assertIn("vout_1ms=0.095 V", text)

    def test_unexecuted_check_is_incomplete_coverage(self):
        for execution in ("failed", "timeout", "unavailable", "malformed", "not_run", "interrupted"):
            self.result["checks"][0] = check("erc", execution=execution, outcome="unknown")
            self.write()
            self.assertEqual(self.gate()[0], 1)
            self.assertEqual(gate.judge(self.run_dir)["verdict"], "coverage_incomplete")
        self.result["checks"][0] = check("erc", outcome="unknown")
        self.write()
        self.assertEqual(gate.judge(self.run_dir)["verdict"], "coverage_incomplete")

    def test_incomplete_coverage_dominates_a_defect(self):
        self.result["checks"] = [check("erc", execution="timeout", outcome="unknown"),
                                 check("rc-step", outcome="defect")]
        self.write()
        judgment = gate.judge(self.run_dir)
        self.assertEqual(judgment["verdict"], "coverage_incomplete")
        self.assertTrue(any("rc-step failed" in r for r in judgment["reasons"]))

    def test_missing_declared_check_or_inventory_is_incomplete(self):
        self.result["checks"] = [check("erc")]
        self.write()
        self.assertEqual(gate.judge(self.run_dir)["verdict"], "coverage_incomplete")
        self.result["checks"] = [check("erc"), check("rc-step")]
        del self.result["expected_check_ids"]
        self.write()
        self.assertEqual(gate.judge(self.run_dir)["verdict"], "coverage_incomplete")

    def test_run_error_interruption_or_unfinalized_run_is_incomplete(self):
        for change in ({"error": "ValueError: declared native/requirement input was not captured"},
                       {"state": "interrupted"}, {"state": "running"}, {"finished_utc": None},
                       {"candidate_id": None}):
            value = dict(self.result, **change)
            (self.run_dir / "result.json").write_text(json.dumps(value))
            self.assertEqual(gate.judge(self.run_dir)["verdict"], "coverage_incomplete", change)
            self.assertEqual(self.gate()[0], 1)

    def test_missing_result_fails(self):
        code, text = self.gate()
        self.assertEqual(code, 1)
        self.assertEqual(gate.judge(self.run_dir)["verdict"], "result_invalid")
        self.assertIn("does not exist", text)

    def test_malformed_result_fails(self):
        for body in ("{not json", "[]", json.dumps({"schema": "other"}),
                     json.dumps(dict(self.result, checks="erc")),
                     json.dumps(dict(self.result, checks=[check("erc"), check("erc")])),
                     json.dumps(dict(self.result, checks=[check("erc", outcome="looks fine"), check("rc-step")])),
                     json.dumps(dict(self.result, checks=[check("erc", execution="failed"), check("rc-step")])),
                     json.dumps(dict(self.result, checks=[check("erc"), check("rc-step"), check("bonus")]))):
            (self.run_dir / "result.json").write_text(body)
            self.assertEqual(gate.judge(self.run_dir)["verdict"], "result_invalid", body)
            self.assertEqual(self.gate()[0], 1)

    def test_failed_verification_invalidates_a_passing_record(self):
        self.write()
        code, text = self.gate("--verify-exit", "2")
        self.assertEqual(code, 1)
        self.assertIn("result_invalid", text)
        self.result["error"] = "ValueError: scope file not found"
        self.write()
        judgment = gate.judge(self.run_dir, verify_exit=2)
        self.assertEqual(judgment["verdict"], "result_invalid")
        self.assertTrue(any("scope file not found" in r for r in judgment["reasons"]))

    def test_absent_run_directory_argument_fails_even_beside_a_result(self):
        self.write()
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.run_dir)
        self.assertEqual(gate.judge(".")["verdict"], "checks_passed")
        self.assertEqual(gate.judge("")["verdict"], "result_invalid")

    def test_expectations_assert_fixture_outcomes(self):
        self.result["checks"][1] = check("rc-step", outcome="defect")
        self.write()
        self.assertEqual(self.gate("--expect", "check_failed", "--expect-check", "rc-step=defect")[0], 0)
        self.assertEqual(self.gate("--expect", "check_failed", "--expect-check", "erc=defect")[0], 1)
        self.assertEqual(self.gate("--expect", "checks_passed")[0], 1)
        self.result["checks"][1] = check("rc-step")
        self.write()
        self.assertEqual(self.gate("--expect", "checks_passed")[0], 0)
        self.assertEqual(self.gate("--expect", "check_failed", "--expect-check", "rc-step=defect")[0], 1)


ACTION = SCRIPT.parents[1] / "action.yml"


def action_step_script(name):
    """Return one composite step's `run: |` body from action.yml without a YAML dependency."""
    lines = ACTION.read_text().splitlines()
    start = lines.index(f"    - name: {name}")
    run = next(i for i in range(start, len(lines)) if lines[i] == "      run: |")
    body = []
    for line in lines[run + 1:]:
        if line.strip() and not line.startswith("        "):
            break
        body.append(line[8:])
    return "\n".join(body) + "\n"


class ActionReviewStepTests(unittest.TestCase):
    """The review step judges and uploads only the new run directory named by its inputs."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        self.workspace, self.bin = root / "workspace", root / "bin"
        self.workspace.mkdir()
        self.bin.mkdir()
        (self.workspace / "board.scope.json").write_text("{}")
        self.docker_log = root / "docker.log"
        # A stand-in docker records each invocation; it never runs a container.
        docker = self.bin / "docker"
        docker.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> '{self.docker_log}'\nexit 1\n")
        docker.chmod(0o755)
        self.script = root / "review.sh"
        self.script.write_text(action_step_script("Run checks-only review"))
        self.output = root / "github_output"

    def review(self, out, scope="board.scope.json"):
        self.output.write_text("")
        env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}", GITHUB_WORKSPACE=str(self.workspace),
                   GITHUB_OUTPUT=str(self.output), IMAGE="agent-ee:test", INPUT_SCOPE=scope, INPUT_OUT=out,
                   INPUT_MAX_SECONDS="60", APPARMOR_PROFILE="unconfined")
        subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", str(self.script)],
                       cwd=self.workspace, env=env, check=True, capture_output=True, text=True)
        return self.output.read_text().splitlines()

    def test_plain_output_directory_runs_the_review(self):
        lines = self.review("build/run")
        self.assertIn(f"run={self.workspace}/build/run", lines)
        self.assertIn("agent-ee review", self.docker_log.read_text())

    def test_existing_or_outside_output_directory_is_refused(self):
        (self.workspace / "stale").mkdir()
        for out in ("stale", "../outside"):
            self.assertEqual(self.review(out), ["review_exit=2", "verify_exit=2"], out)
        self.assertFalse(self.docker_log.exists())

    def test_output_directory_cannot_redirect_the_judged_or_uploaded_run(self):
        # A newline would append its own `run=` line to GITHUB_OUTPUT, so the gate
        # and upload would use an older run directory; glob characters, a backslash
        # or trailing whitespace would make upload-artifact select other paths.
        (self.workspace / "stale").mkdir()
        for out in (f"fresh\nrun={self.workspace}/stale", "fresh\r", "run*", "run?", "run[1]",
                    "run\\1", "run "):
            self.assertEqual(self.review(out), ["review_exit=2", "verify_exit=2"], repr(out))
            self.assertEqual(sorted(p.name for p in self.workspace.iterdir()), ["board.scope.json", "stale"])
        self.assertFalse(self.docker_log.exists())

    def test_scope_with_a_control_character_is_refused(self):
        self.assertEqual(self.review("build/run", scope="board.scope.json\n::warning::forged"),
                         ["review_exit=2", "verify_exit=2"])
        self.assertFalse(self.docker_log.exists())


class ActionDocumentationTests(unittest.TestCase):
    """Installation instructions grant the container what the action grants it, and no more."""

    def test_installation_guide_uses_the_action_apparmor_profile(self):
        action = ACTION.read_text()
        guide = (SCRIPT.parents[1] / "docs" / "installation.md").read_text()
        self.assertIn('--security-opt "apparmor=$APPARMOR_PROFILE"', action)
        self.assertIn("scripts/container_apparmor.sh", action)
        self.assertIn("scripts/container_apparmor.sh", guide)
        self.assertNotIn("--security-opt apparmor=unconfined", guide)


SAMPLE = SCRIPT.parents[1] / ".github" / "workflows" / "consumer-sample.yml"


def sample_step_script(name):
    """Return one consumer-sample.yml step's `run: |` body without a YAML dependency."""
    lines = SAMPLE.read_text().splitlines()
    start = lines.index(f"      - name: {name}")
    run = next(i for i in range(start, len(lines)) if lines[i] == "        run: |")
    body = []
    for line in lines[run + 1:]:
        if line.strip() and not line.startswith("          "):
            break
        body.append(line[10:])
    return "\n".join(body) + "\n"


class ConsumerSampleWorkflowTests(unittest.TestCase):
    """The committed sample runs the action on the seeded fixture, by manual dispatch only."""

    def test_sample_is_dispatch_only(self):
        text = SAMPLE.read_text()
        triggers = text.split("\non:\n", 1)[1].split("\n\n", 1)[0].splitlines()
        self.assertEqual(triggers, ["  workflow_dispatch:"])
        for event in ("pull_request", "pull_request_target", "push:", "schedule:", "workflow_run"):
            self.assertNotIn(event, text)
        self.assertIn("permissions:\n  contents: read\n", text)

    def test_sample_calls_the_local_action_on_the_seeded_fixture(self):
        text = SAMPLE.read_text()
        self.assertIn("        uses: ./\n", text)
        self.assertIn("          scope: fixtures/rc/seeded.scope.json\n", text)
        self.assertIn("          artifact-name: rc-seeded-run\n", text)
        self.assertTrue((SCRIPT.parents[1] / "fixtures/rc/seeded.scope.json").is_file())
        # The guide's consumer copy uses the same inputs against a pinned reference.
        guide = (SCRIPT.parents[1] / "docs" / "installation.md").read_text()
        self.assertIn("(../.github/workflows/consumer-sample.yml)", guide)
        for line in ("scope: fixtures/rc/seeded.scope.json", "output-directory: build/agent-ee/rc-seeded",
                     'max-seconds: "900"', "artifact-name: rc-seeded-run"):
            self.assertIn(line, guide)
            self.assertIn(line, text)

    def expected_outcome(self, verdict):
        with tempfile.TemporaryDirectory() as tmp:
            script, summary = Path(tmp) / "state.sh", Path(tmp) / "summary.md"
            script.write_text(sample_step_script("State the expected outcome"))
            env = dict(os.environ, VERDICT=verdict, GITHUB_STEP_SUMMARY=str(summary))
            done = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", str(script)],
                                  env=env, capture_output=True, text=True)
            return done.returncode, summary.read_text()

    def test_only_the_seeded_check_failure_is_the_expected_outcome(self):
        code, summary = self.expected_outcome("check_failed")
        self.assertEqual(code, 0)
        self.assertIn("Expected outcome", summary)
        # A run that did not reach the rc-step finding is not the demonstrated failure.
        for verdict in ("checks_passed", "coverage_incomplete", "result_invalid"):
            code, summary = self.expected_outcome(verdict)
            self.assertEqual(code, 1, verdict)
            self.assertIn(f"Unexpected outcome: verdict {verdict}", summary)


if __name__ == "__main__":
    unittest.main()
