"""Scheduling evidence from real, networkless scripted harness subprocesses."""
import contextlib
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_ee.cli import outcome, run_review
from agent_ee.candidate import capture
from agent_ee.executor import execute
from agent_ee.reviewers import initial_reviews
from agent_ee.storage import read_json, write_json


HARNESS = '''import json, sys, time
from pathlib import Path
request = json.loads(sys.stdin.read())
role = request["role"]
assert not Path("/experiments").exists()
assert not Path("/work/../review-work").exists()
assert not Path("/candidate/peer-marker").exists()
try:
    Path("/candidate/peer-marker").write_text(role)
except OSError:
    pass
else:
    raise AssertionError("candidate writable")
Path("experiments").mkdir(exist_ok=True)
Path("experiments/start.json").write_text(json.dumps({"time": time.monotonic(), "request": request}))
print("started " + role, flush=True)
time.sleep(request["sleep"])
if request["fail"]:
    print("subscription authentication/quota failure", file=sys.stderr, flush=True)
    sys.exit(1)
response = {"schema": "agent-ee.review/1", **request["identity"], "role": role,
    "coverage": ["scripted scheduling only"], "limitations": [], "findings": []}
Path("experiments/finish.json").write_text(json.dumps({"time": time.monotonic()}))
print(json.dumps(response), flush=True)
'''


class ConcurrentReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        source = self.root / "source"
        source.mkdir()
        (source / "harness.py").write_text(HARNESS)
        definition = {"harness": "codex", "model": "scripted-test", "effort": "high", "timeout_s": 5}
        self.profile = {"schema": "agent-ee.profile/1", "name": "scripted",
            "reviewers": [{**definition, "role": "circuit"}, {**definition, "role": "physical"}],
            "adjudicator": definition}
        self.scope = {"schema": "agent-ee.scope/1", "name": "scheduling", "scope": "scheduling protocol",
            "inputs": [{"path": "source", "at": "design"}], "review_profile": self.profile}
        self.settings = {"circuit": {"sleep": 1.2, "fail": False},
                         "physical": {"sleep": 1.2, "fail": False}}
        self.calls = []

    def prepare(self):
        write_json(self.root / "scope.json", self.scope)
        run = self.root / f"run-{len(list(self.root.glob('run-*')))}"
        run.mkdir()
        manifest = capture(self.root / "scope.json", run)
        packet = {"candidate_id": manifest["candidate_id"], "run_id": "scripted-run", "packet_sha256": "packet"}
        write_json(run / "packet.json", packet)
        return run, packet

    def harness(self, run, role, definition, prompt, schema, timeout):
        self.calls.append(role)
        if role == "adjudicator":
            now = time.monotonic()
            for initial in ("circuit", "physical"):
                self.assertEqual(read_json(Path(run) / f"review-executions/{initial}.json")["execution"], "ok")
                self.assertLess(read_json(Path(run) / f"review-work/{initial}/experiments/finish.json")["time"], now)
                self.assertTrue((Path(run) / f"reviews/{initial}.json").is_file())
            packet = read_json(Path(run) / "packet.json")
            return {"execution": "ok"}, {"schema": "agent-ee.decisions/1",
                "candidate_id": packet["candidate_id"], "run_id": packet["run_id"], "decisions": []}
        packet = read_json(Path(run) / "packet.json")
        identity = {key: packet[key] for key in ("candidate_id", "run_id", "packet_sha256")}
        for key, value in {**identity, "role": role}.items():
            self.assertEqual(schema["properties"][key]["enum"], [value])
        self.assertNotIn('"initial_reviews"', prompt)
        self.assertIn(f"Your role is {role}.", prompt)
        work = Path(run) / "review-work" / role
        record = execute(Path(run) / "inputs", work,
            ["/usr/bin/python3", "/candidate/design/harness.py"], timeout=timeout,
            stdin=json.dumps({"role": role, "identity": identity, **self.settings[role]}))
        value = json.loads((work / "stdout.log").read_text().splitlines()[-1]) if record["execution"] == "ok" else None
        return record, value

    def test_two_sleeping_roles_overlap_and_keep_role_order(self):
        run, packet = self.prepare()
        start = time.monotonic()
        with patch("agent_ee.reviewers.reviewer_call", side_effect=self.harness):
            reviews, records = initial_reviews(run, packet, start + 10)
        elapsed = time.monotonic() - start
        self.assertEqual([r["role"] for r in reviews], ["circuit", "physical"])
        self.assertEqual([r["execution"] for r in records], ["ok", "ok"])
        intervals = [(
            read_json(run / f"review-work/{role}/experiments/start.json")["time"],
            read_json(run / f"review-work/{role}/experiments/finish.json")["time"])
            for role in ("circuit", "physical")]
        self.assertLess(max(s for s, _ in intervals), min(e for _, e in intervals))
        self.assertLess(elapsed, 2.1, f"two 1.2s harnesses took {elapsed:.3f}s")
        for record in records:
            self.assertEqual(read_json(run / f"review-executions/{record['role']}.json"), record)

    def assert_retained(self, run, records):
        for record in records:
            role = record["role"]
            self.assertEqual(read_json(run / f"review-executions/{role}.json"), record)
            if record["execution"] != "not_run":
                work = run / "review-work" / role
                self.assertTrue((work / "experiments/start.json").is_file())
                self.assertIn("started " + role, (work / "stdout.log").read_text())
                self.assertEqual(read_json(work / "execution.json")["execution"], record["execution"])

    def test_failed_role_retains_inflight_peer_in_either_order(self):
        for failed in ("circuit", "physical"):
            with self.subTest(failed=failed):
                other = "physical" if failed == "circuit" else "circuit"
                self.settings[failed] = {"sleep": .25, "fail": True}
                self.settings[other] = {"sleep": .7, "fail": False}
                run, packet = self.prepare()
                with patch("agent_ee.reviewers.reviewer_call", side_effect=self.harness):
                    reviews, records = initial_reviews(run, packet, time.monotonic() + 5)
                self.assertEqual([r["role"] for r in reviews], [other])
                self.assertEqual({r["role"]: r["execution"] for r in records}, {failed: "failed", other: "ok"})
                self.assert_retained(run, records)
                self.assertEqual(outcome({"state": "complete", "checks": [], "reviews": reviews,
                    "review_executions": records, "findings": [], "adjudicated": False}), "incomplete")

    def test_per_call_timeout_retains_completed_peer_in_either_order(self):
        for slow in ("circuit", "physical"):
            with self.subTest(slow=slow):
                other = "physical" if slow == "circuit" else "circuit"
                self.settings[slow] = {"sleep": 3, "fail": False}
                self.settings[other] = {"sleep": .2, "fail": False}
                for definition in self.profile["reviewers"]:
                    definition["timeout_s"] = 1 if definition["role"] == slow else 5
                run, packet = self.prepare()
                with patch("agent_ee.reviewers.reviewer_call", side_effect=self.harness):
                    reviews, records = initial_reviews(run, packet, time.monotonic() + 5)
                self.assertEqual([r["role"] for r in reviews], [other])
                self.assertEqual({r["role"]: r["execution"] for r in records}, {slow: "timeout", other: "ok"})
                self.assertEqual(next(r for r in records if r["role"] == slow)["timeout_s"], 1)
                self.assert_retained(run, records)

    def test_global_deadline_bounds_both_calls_and_retains_partial_artifacts(self):
        self.settings["circuit"]["sleep"] = .2
        self.settings["physical"]["sleep"] = 3
        run, packet = self.prepare()
        start = time.monotonic()
        with patch("agent_ee.reviewers.reviewer_call", side_effect=self.harness):
            reviews, records = initial_reviews(run, packet, start + .8)
        self.assertLess(time.monotonic() - start, 1.5)
        self.assertEqual([r["role"] for r in reviews], ["circuit"])
        self.assertEqual([r["execution"] for r in records], ["ok", "timeout"])
        self.assertTrue(all(0 < r["timeout_s"] <= .8 for r in records))
        self.assert_retained(run, records)

    def test_known_harness_failure_suppresses_queued_calls_without_affecting_other_harness(self):
        definition = self.profile["reviewers"][0]
        self.profile["reviewers"] += [{**definition, "role": "queued"},
            {**definition, "role": "independent", "harness": "claude"}]
        self.settings.update(circuit={"sleep": .25, "fail": True}, physical={"sleep": .7, "fail": False},
            independent={"sleep": .1, "fail": False})
        run, packet = self.prepare()
        with patch("agent_ee.reviewers.reviewer_call", side_effect=self.harness):
            reviews, records = initial_reviews(run, packet, time.monotonic() + 5)
        self.assertEqual([r["role"] for r in reviews], ["physical", "independent"])
        self.assertEqual([r["execution"] for r in records], ["failed", "ok", "not_run", "ok"])
        self.assertCountEqual(self.calls, ["circuit", "physical", "independent"])
        self.assertIn("affected harness stopped", records[2]["error"])
        self.assertFalse((run / "review-work/queued").exists())
        self.assert_retained(run, records)

    def test_expired_budget_launches_nothing_and_records_every_role(self):
        run, packet = self.prepare()
        with patch("agent_ee.reviewers.reviewer_call", side_effect=self.harness):
            reviews, records = initial_reviews(run, packet, time.monotonic() - 1)
        self.assertEqual(reviews, [])
        self.assertEqual(self.calls, [])
        self.assertEqual([r["execution"] for r in records], ["not_run", "not_run"])
        self.assert_retained(run, records)

    def test_coordinator_adjudicates_only_after_both_initial_roles_settle(self):
        fixture = Path(__file__).resolve().parents[1] / "fixtures/rc/control.scope.json"
        self.scope = read_json(fixture)
        for entry in self.scope["inputs"]:
            entry["path"] = str((fixture.parent / entry["path"]).resolve())
        self.scope["inputs"].append({"path": str(self.root / "source"), "at": "design"})
        self.scope["review_profile"] = self.profile
        # A real native netlist export suffices for this scheduling test.
        self.scope["checks"] = [{"id": "netlist", "kind": "netlist"}]
        self.settings["circuit"]["sleep"] = .6
        self.settings["physical"]["sleep"] = .2
        write_json(self.root / "scope.json", self.scope)
        run = self.root / "coordinator"
        with patch("agent_ee.reviewers.reviewer_call", side_effect=self.harness), contextlib.redirect_stdout(io.StringIO()):
            status = run_review(self.root / "scope.json", run, 30)
        result = read_json(run / "result.json")
        self.assertEqual(status, 0, result)
        self.assertEqual(result["outcome"], "criteria_satisfied")
        self.assertEqual(self.calls[-1], "adjudicator")
        self.assertCountEqual(self.calls[:-1], ["circuit", "physical"])
        self.assertEqual([r["role"] for r in result["reviews"]], ["circuit", "physical"])

    def test_coordinator_reports_failure_incomplete_and_does_not_adjudicate(self):
        fixture = Path(__file__).resolve().parents[1] / "fixtures/rc/control.scope.json"
        self.scope = read_json(fixture)
        for entry in self.scope["inputs"]:
            entry["path"] = str((fixture.parent / entry["path"]).resolve())
        self.scope["inputs"].append({"path": str(self.root / "source"), "at": "design"})
        self.scope["review_profile"] = self.profile
        self.scope["checks"] = [{"id": "netlist", "kind": "netlist"}]
        # Two successful peers must not allow a new adjudicator call on the
        # failed harness when a profile declares more than two initial roles.
        self.profile["reviewers"][1]["harness"] = "claude"
        self.profile["reviewers"].append({**self.profile["reviewers"][1], "role": "independent"})
        self.settings["circuit"] = {"sleep": .25, "fail": True}
        self.settings["physical"]["sleep"] = .6
        self.settings["independent"] = {"sleep": .1, "fail": False}
        write_json(self.root / "scope.json", self.scope)
        run = self.root / "coordinator"
        with patch("agent_ee.reviewers.reviewer_call", side_effect=self.harness), contextlib.redirect_stdout(io.StringIO()):
            status = run_review(self.root / "scope.json", run, 30)
        result = read_json(run / "result.json")
        self.assertEqual(status, 1, result)
        self.assertEqual(result["outcome"], "incomplete")
        self.assertFalse(result["adjudicated"])
        self.assertNotIn("adjudicator", self.calls)
        self.assertEqual([r["role"] for r in result["reviews"]], ["physical", "independent"])
        self.assert_retained(run, result["review_executions"])
