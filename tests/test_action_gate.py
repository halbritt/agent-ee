import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "action_gate.py"
spec = importlib.util.spec_from_file_location("action_gate", SCRIPT)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

from agent_ee.findings import collect_findings


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
        # Like the writer: each executed check is retained as checks/<id>.json and its defect becomes a finding.
        shutil.rmtree(self.run_dir / "checks", ignore_errors=True)
        (self.run_dir / "checks").mkdir()
        for record in self.result["checks"]:
            (self.run_dir / "checks" / f"{record['id']}.json").write_text(json.dumps(record))
        self.result["findings"] = collect_findings([], self.result["checks"])
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
            self.write()
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

    def test_projection_contradicting_retained_check_evidence_is_invalid(self):
        self.result["checks"][1] = check("rc-step", outcome="defect", summary="vout_1ms=0.095 V outside 0.62..0.65")
        self.write()
        retained = json.loads((self.run_dir / "result.json").read_text())
        mutations = {
            "outcome": lambda r: r["checks"][1].update(outcome="pass"),
            "summary": lambda r: r["checks"][1].update(summary="within limits"),
            "finding": lambda r: r["findings"][0].update(title="within limits"),
            "dropped finding": lambda r: r.update(findings=[]),
            "unretained check": lambda r: r["checks"].append(check("drc")) or r["expected_check_ids"].append("drc"),
            "unprojected record": lambda r: r["checks"].pop(1) and r["expected_check_ids"].pop(1),
        }
        for name, mutate in mutations.items():
            value = json.loads(json.dumps(retained))
            mutate(value)
            (self.run_dir / "result.json").write_text(json.dumps(value))
            judgment = gate.judge(self.run_dir, verify_exit=0)
            self.assertEqual(judgment["verdict"], "result_invalid", name)
            self.assertIn("retained check evidence", judgment["reasons"][0], name)
            self.assertEqual(self.gate("--verify-exit", "0")[0], 1, name)
        # An unexecuted placeholder for a check the run never reached needs no retained record.
        (self.run_dir / "checks" / "erc.json").unlink()
        retained["checks"][0] = check("erc", execution="not_run", outcome="unknown")
        (self.run_dir / "result.json").write_text(json.dumps(retained))
        self.assertEqual(gate.judge(self.run_dir)["verdict"], "coverage_incomplete")


class CheckProjectionBindingTests(unittest.TestCase):
    """Real checks-only runs: verify and the gate bind result.json to the retained checks/<id>.json."""

    @classmethod
    def setUpClass(cls):
        from tests.toolchain import require_toolchain
        require_toolchain(native=True)
        from agent_ee.cli import run_review
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        fixtures = SCRIPT.parents[1] / "fixtures" / "rc"
        for name in ("seeded", "control"):
            with contextlib.redirect_stdout(io.StringIO()):
                run_review(fixtures / f"{name}.scope.json", cls.root / name / "run", 1200, checks_only=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def copy(self, name):
        target = Path(tempfile.mkdtemp(dir=self.root)) / "run"
        shutil.copytree(self.root / name / "run", target, symlinks=True)
        return target

    def judged(self, run):
        from agent_ee.cli import verify
        try:
            verify(run)
        except ValueError as exc:
            return gate.judge(run, verify_exit=2)["verdict"], str(exc)
        return gate.judge(run, verify_exit=0)["verdict"], None

    def test_intact_seeded_and_control_runs_keep_their_verdicts(self):
        self.assertEqual(self.judged(self.root / "seeded" / "run"), ("check_failed", None))
        self.assertEqual(self.judged(self.root / "control" / "run"), ("checks_passed", None))

    def test_mutated_check_or_finding_projection_fails_closed(self):
        mutations = {
            "rc-step outcome": (lambda r: next(c for c in r["checks"] if c["id"] == "rc-step").update(outcome="pass"),
                                "check rc-step projection differs from retained native-check result"),
            "rc-step summary": (lambda r: next(c for c in r["checks"] if c["id"] == "rc-step").update(summary="ok"),
                                "check rc-step projection differs from retained native-check result"),
            "finding title": (lambda r: r["findings"][0].update(title="within limits"),
                              "finding check/rc-step differs from its retained native-check result"),
            "dropped finding": (lambda r: r.update(findings=[]),
                                "check findings differ from the defects in the retained native-check results"),
            "check inventory": (lambda r: r["checks"].pop() and r["expected_check_ids"].pop(),
                                "result check inventory differs from the captured scope's declared checks"),
        }
        for name, (mutate, reason) in mutations.items():
            run = self.copy("seeded")
            result = json.loads((run / "result.json").read_text())
            self.assertEqual(result["state"], "incomplete")
            mutate(result)
            (run / "result.json").write_text(json.dumps(result))
            verdict, error = self.judged(run)
            self.assertEqual(verdict, "result_invalid", name)
            self.assertIn(reason, error or "", name)
            # The gate binds the evidence itself, even when told verification succeeded.
            self.assertEqual(gate.judge(run, verify_exit=0)["verdict"], "result_invalid", name)

    def test_missing_retained_check_record_fails_closed(self):
        run = self.copy("control")
        (run / "checks" / "rc-step.json").unlink()
        verdict, error = self.judged(run)
        self.assertEqual(verdict, "result_invalid")
        self.assertIn("evidence changed", error)
        self.assertEqual(gate.judge(run, verify_exit=0)["verdict"], "result_invalid")


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


if __name__ == "__main__":
    unittest.main()
