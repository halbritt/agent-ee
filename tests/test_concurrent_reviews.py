"""Scheduling evidence from real, networkless scripted harness subprocesses."""
import contextlib
import copy
import io
import json
import math
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from toolchain import require_toolchain, toolchain_test

from agent_ee.cli import outcome, run_review
from agent_ee.candidate import capture
from agent_ee.executor import execute
from agent_ee.preflight import capture_expectation
from agent_ee.reviewers import build_packet, initial_reviews
from agent_ee.storage import digest, read_json, write_json


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
    _coordinator_timings = None

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
            "adjudicator": dict(definition)}
        self.scope = {"schema": "agent-ee.scope/1", "name": "scheduling", "scope": "scheduling protocol",
            "inputs": [{"path": "source", "at": "design"}], "review_profile": self.profile}
        self.settings = {"circuit": {"sleep": 1.2, "fail": False},
                         "physical": {"sleep": 1.2, "fail": False}}
        self.calls = []
        self.dispatch_intervals = {}
        runtime = {name: {'path': '/usr/bin/true', 'sha256': digest('/usr/bin/true')}
                   for name in ('codex', 'claude', 'codex_tool_host', 'bwrap', '/usr/bin/python3', 'kicad-cli')}
        runtime_patch = patch('agent_ee.cli.runtime_files', return_value=runtime)
        runtime_patch.start()
        self.addCleanup(runtime_patch.stop)

    def prepare(self):
        write_json(self.root / "scope.json", self.scope)
        run = self.root / f"run-{len(list(self.root.glob('run-*')))}"
        run.mkdir()
        manifest = capture(self.root / "scope.json", run)
        packet = {"candidate_id": manifest["candidate_id"], "run_id": "scripted-run", "packet_sha256": "packet"}
        write_json(run / "packet.json", packet)
        from agent_ee.cli import policy_files, runtime_files
        selected = manifest['scope']['review_profile']
        definitions = [{**d, 'tool_mode': selected.get('tool_mode', 'investigate')} for d in
                       [*selected['reviewers'], selected['adjudicator']]]
        self.context = {'expected': capture_expectation({'run_id': packet['run_id'],
            'policy_files': policy_files(), 'runtime_files': runtime_files()}, manifest, definitions)}
        return run, packet

    def configure_coordinator_scope(self):
        fixture = Path(__file__).resolve().parents[1] / "fixtures/rc/control.scope.json"
        self.scope = read_json(fixture)
        for entry in self.scope["inputs"]:
            entry["path"] = str((fixture.parent / entry["path"]).resolve())
        self.scope["inputs"].append({"path": str(self.root / "source"), "at": "design"})
        self.scope["review_profile"] = self.profile
        # A real native netlist export suffices for this scheduling test.
        self.scope["checks"] = [{"id": "netlist", "kind": "netlist"}]

    def measure_coordinator_timings(self):
        if ConcurrentReviewTests._coordinator_timings is not None:
            return dict(ConcurrentReviewTests._coordinator_timings)
        from agent_ee.cli import policy_files, runtime_files
        saved_scope = copy.deepcopy(self.scope)
        saved_profile = copy.deepcopy(self.profile)
        saved_settings = copy.deepcopy(self.settings)
        saved_calls = list(self.calls)
        saved_intervals = dict(self.dispatch_intervals)
        try:
            probe_root = self.root / "timing-probe"
            probe_root.mkdir(exist_ok=True)
            probe_definition = {"harness": "codex", "model": "scripted-test", "effort": "high", "timeout_s": 30}
            self.profile = {
                "schema": "agent-ee.profile/1",
                "name": "scripted-probe",
                "reviewers": [{**probe_definition, "role": "circuit"}, {**probe_definition, "role": "physical"}],
                "adjudicator": dict(probe_definition),
            }
            self.configure_coordinator_scope()
            probe_scope = probe_root / "scope.json"
            probe_run = probe_root / "run"
            write_json(probe_scope, self.scope)
            export_start = time.monotonic()
            with contextlib.redirect_stdout(io.StringIO()):
                run_review(probe_scope, probe_run, 180, checks_only=True)
            export_s = time.monotonic() - export_start
            probe_result = read_json(probe_run / "result.json")
            checks = probe_result.get("checks", [])
            if (
                probe_result.get("error")
                or not checks
                or checks[0].get("execution") != "ok"
                or checks[0].get("outcome") not in ("pass", "info")
            ):
                load_1m = os.getloadavg()[0]
                self.fail(
                    f"budget exceeded under load {load_1m:.2f} "
                    f"(timing probe checks_only failed after {export_s:.3f}s: {probe_result.get('error') or checks})"
                )
            netlist_exec = probe_run / "work/netlist/execution.json"
            netlist_s = float(read_json(netlist_exec).get("duration_s", export_s)) if netlist_exec.is_file() else export_s
            manifest = read_json(probe_run / "candidate.json")
            packet = build_packet(probe_run, checks, probe_result["run_id"])
            definitions = [
                {**d, "tool_mode": self.profile.get("tool_mode", "investigate")}
                for d in [*self.profile["reviewers"], self.profile["adjudicator"]]
            ]
            context = {
                "expected": capture_expectation(
                    {"run_id": packet["run_id"], "policy_files": policy_files(), "runtime_files": runtime_files()},
                    manifest,
                    definitions,
                )
            }
            self.settings = {"circuit": {"sleep": 0.0, "fail": False}, "physical": {"sleep": 0.0, "fail": False}}
            harness_start = time.monotonic()
            with patch("agent_ee.reviewers.dispatch", side_effect=self.harness):
                _, records = initial_reviews(probe_run, packet, harness_start + 60, launch_context=context)
            harness_s = time.monotonic() - harness_start
            if len(records) != 2 or any(r.get("execution") != "ok" for r in records):
                load_1m = os.getloadavg()[0]
                self.fail(
                    f"budget exceeded under load {load_1m:.2f} "
                    f"(timing probe harness failed after {harness_s:.3f}s: {records})"
                )
            ConcurrentReviewTests._coordinator_timings = {
                "export_s": max(export_s, 0.05),
                "netlist_s": max(netlist_s, 0.01),
                "harness_s": max(harness_s, 0.05),
            }
            return dict(ConcurrentReviewTests._coordinator_timings)
        finally:
            self.scope = saved_scope
            self.profile = saved_profile
            self.settings = saved_settings
            self.calls = saved_calls
            self.dispatch_intervals = saved_intervals

    def coordinator_budget(self, waves=1):
        timings = self.measure_coordinator_timings()
        scale = max(1.0, timings["harness_s"])
        for role_settings in self.settings.values():
            role_settings["sleep"] = round(role_settings["sleep"] * scale, 3)
        max_sleep = max((s["sleep"] for s in self.settings.values()), default=0.0)
        total_sleep = sum(s["sleep"] for s in self.settings.values())
        role_timeout = max(5, math.ceil(4.0 * timings["harness_s"] + max_sleep + 2.0))
        for definition in self.profile["reviewers"]:
            definition["timeout_s"] = role_timeout
        self.profile["adjudicator"]["timeout_s"] = role_timeout
        budget = 3.0 * timings["export_s"] + (waves + 4.0) * timings["harness_s"] + total_sleep
        return budget, timings

    def assert_within_budget(self, elapsed, budget, timings, result=None, context="coordinator run"):
        budget_exhausted = elapsed >= budget
        if result is not None:
            records = result.get("review_executions", [])
            for record in records:
                if record.get("execution") == "timeout":
                    budget_exhausted = True
                if "global review budget expired" in record.get("error", ""):
                    budget_exhausted = True
            for limitation in result.get("limitations", []):
                if "Wall-clock work budget exhausted" in limitation:
                    budget_exhausted = True
            if (
                records
                and all(r.get("execution") == "ok" for r in records)
                and not result.get("adjudicated")
                and not result.get("error")
            ):
                budget_exhausted = True
        if budget_exhausted:
            load_1m = os.getloadavg()[0]
            self.fail(
                f"budget exceeded under load {load_1m:.2f} "
                f"({context}: elapsed={elapsed:.3f}s, budget={budget:.3f}s, "
                f"export_s={timings['export_s']:.3f}s, netlist_s={timings['netlist_s']:.3f}s, "
                f"harness_s={timings['harness_s']:.3f}s)"
            )

    def assert_concurrent_initial_reviews(self, run, roles=("circuit", "physical")):
        run = Path(run)
        intervals = []
        durations = []
        for role in roles:
            work = run / "review-work" / role
            start_time = read_json(work / "experiments/start.json")["time"]
            finish_path = work / "experiments/finish.json"
            if finish_path.is_file():
                finish_time = read_json(finish_path)["time"]
            else:
                finish_time = self.dispatch_intervals[role][1]
            intervals.append((start_time, finish_time))
            durations.append(float(read_json(run / f"review-executions/{role}.json")["duration_s"]))
        wave_span = max(e for _, e in intervals) - min(s for s, _ in intervals)
        serial_budget = sum(durations)
        self.assertLess(
            wave_span,
            serial_budget,
            f"initial roles {roles} exceeded derived concurrent budget: "
            f"span={wave_span:.3f}s >= serial_budget={serial_budget:.3f}s",
        )
        self.assertLess(
            max(s for s, _ in intervals),
            min(e for _, e in intervals),
            f"initial roles {roles} did not overlap in time: {intervals}",
        )

    def harness(self, run, role, definition, prompt, schema, timeout, *, cancellation=None, executable=None):
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
        dispatch_start = time.monotonic()
        record = execute(Path(run) / "inputs", work,
            ["/usr/bin/python3", "/candidate/design/harness.py"], timeout=timeout,
            stdin=json.dumps({"role": role, "identity": identity, **self.settings[role]}),
            **({"cancellation": cancellation} if cancellation is not None else {}))
        self.dispatch_intervals[role] = (dispatch_start, time.monotonic())
        value = json.loads((work / "stdout.log").read_text().splitlines()[-1]) if record["execution"] == "ok" else None
        return record, value

    @toolchain_test
    def test_two_sleeping_roles_overlap_and_keep_role_order(self):
        run, packet = self.prepare()
        start = time.monotonic()
        with patch("agent_ee.reviewers.dispatch", side_effect=self.harness):
            reviews, records = initial_reviews(run, packet, start + 10, launch_context=self.context)
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

    @toolchain_test
    def test_failed_role_retains_inflight_peer_in_either_order(self):
        for failed in ("circuit", "physical"):
            with self.subTest(failed=failed):
                other = "physical" if failed == "circuit" else "circuit"
                self.settings[failed] = {"sleep": .25, "fail": True}
                self.settings[other] = {"sleep": .7, "fail": False}
                run, packet = self.prepare()
                with patch("agent_ee.reviewers.dispatch", side_effect=self.harness):
                    reviews, records = initial_reviews(run, packet, time.monotonic() + 5, launch_context=self.context)
                self.assertEqual([r["role"] for r in reviews], [other])
                self.assertEqual({r["role"]: r["execution"] for r in records}, {failed: "failed", other: "ok"})
                self.assert_retained(run, records)
                self.assertEqual(outcome({"state": "complete", "checks": [], "reviews": reviews,
                    "review_executions": records, "findings": [], "adjudicated": False}), "incomplete")

    @toolchain_test
    def test_per_call_timeout_retains_completed_peer_in_either_order(self):
        for slow in ("circuit", "physical"):
            with self.subTest(slow=slow):
                other = "physical" if slow == "circuit" else "circuit"
                self.settings[slow] = {"sleep": 3, "fail": False}
                self.settings[other] = {"sleep": .2, "fail": False}
                for definition in self.profile["reviewers"]:
                    definition["timeout_s"] = 1 if definition["role"] == slow else 5
                run, packet = self.prepare()
                with patch("agent_ee.reviewers.dispatch", side_effect=self.harness):
                    reviews, records = initial_reviews(run, packet, time.monotonic() + 5, launch_context=self.context)
                self.assertEqual([r["role"] for r in reviews], [other])
                self.assertEqual({r["role"]: r["execution"] for r in records}, {slow: "timeout", other: "ok"})
                self.assertTrue(0 < next(r for r in records if r["role"] == slow)["timeout_s"] <= 1)
                self.assert_retained(run, records)

    @toolchain_test
    def test_global_deadline_bounds_both_calls_and_retains_partial_artifacts(self):
        self.settings["circuit"]["sleep"] = .2
        self.settings["physical"]["sleep"] = 3
        run, packet = self.prepare()
        start = time.monotonic()
        with patch("agent_ee.reviewers.dispatch", side_effect=self.harness):
            reviews, records = initial_reviews(run, packet, start + .8, launch_context=self.context)
        self.assertLess(time.monotonic() - start, 1.5)
        self.assertEqual([r["role"] for r in reviews], ["circuit"])
        self.assertEqual([r["execution"] for r in records], ["ok", "timeout"])
        self.assertTrue(all(0 < r["timeout_s"] <= .8 for r in records))
        self.assert_retained(run, records)

    @toolchain_test
    def test_known_harness_failure_suppresses_queued_calls_without_affecting_other_harness(self):
        definition = self.profile["reviewers"][0]
        self.profile["reviewers"] += [{**definition, "role": "queued"},
            {**definition, "role": "independent", "harness": "claude"}]
        self.settings.update(circuit={"sleep": .25, "fail": True}, physical={"sleep": .7, "fail": False},
            independent={"sleep": .1, "fail": False})
        run, packet = self.prepare()
        with patch("agent_ee.reviewers.dispatch", side_effect=self.harness):
            reviews, records = initial_reviews(run, packet, time.monotonic() + 5, launch_context=self.context)
        self.assertEqual([r["role"] for r in reviews], ["physical", "independent"])
        self.assertEqual([r["execution"] for r in records], ["failed", "ok", "not_run", "ok"])
        self.assertCountEqual(self.calls, ["circuit", "physical", "independent"])
        self.assertIn("affected harness stopped", records[2]["error"])
        self.assertFalse((run / "review-work/queued").exists())
        self.assert_retained(run, records)

    def test_expired_budget_launches_nothing_and_records_every_role(self):
        run, packet = self.prepare()
        with patch("agent_ee.reviewers.dispatch", side_effect=self.harness):
            reviews, records = initial_reviews(run, packet, time.monotonic() - 1, launch_context=self.context)
        self.assertEqual(reviews, [])
        self.assertEqual(self.calls, [])
        self.assertEqual([r["execution"] for r in records], ["not_run", "not_run"])
        self.assert_retained(run, records)

    def test_coordinator_adjudicates_only_after_both_initial_roles_settle(self):
        require_toolchain(native=True)
        self.configure_coordinator_scope()
        self.settings["circuit"]["sleep"] = .6
        self.settings["physical"]["sleep"] = .2
        budget, timings = self.coordinator_budget(waves=1)
        write_json(self.root / "scope.json", self.scope)
        run = self.root / "coordinator"
        start = time.monotonic()
        with patch("agent_ee.reviewers.dispatch", side_effect=self.harness), contextlib.redirect_stdout(io.StringIO()):
            status = run_review(self.root / "scope.json", run, budget)
        elapsed = time.monotonic() - start
        result = read_json(run / "result.json")
        self.assert_within_budget(elapsed, budget, timings, result=result)
        self.assertEqual(status, 0, result)
        self.assertEqual(result["outcome"], "criteria_satisfied")
        self.assertEqual(self.calls[-1], "adjudicator")
        self.assertCountEqual(self.calls[:-1], ["circuit", "physical"])
        self.assertEqual([r["role"] for r in result["reviews"]], ["circuit", "physical"])
        self.assert_concurrent_initial_reviews(run)

    def test_coordinator_reports_failure_incomplete_and_does_not_adjudicate(self):
        require_toolchain(native=True)
        self.configure_coordinator_scope()
        # Two successful peers must not allow a new adjudicator call on the
        # failed harness when a profile declares more than two initial roles.
        self.profile["reviewers"][1]["harness"] = "claude"
        self.profile["reviewers"].append({**self.profile["reviewers"][1], "role": "independent"})
        self.settings["circuit"] = {"sleep": .25, "fail": True}
        self.settings["physical"]["sleep"] = .6
        self.settings["independent"] = {"sleep": .1, "fail": False}
        budget, timings = self.coordinator_budget(waves=2)
        write_json(self.root / "scope.json", self.scope)
        run = self.root / "coordinator"
        start = time.monotonic()
        with patch("agent_ee.reviewers.dispatch", side_effect=self.harness), contextlib.redirect_stdout(io.StringIO()):
            status = run_review(self.root / "scope.json", run, budget)
        elapsed = time.monotonic() - start
        result = read_json(run / "result.json")
        self.assert_within_budget(elapsed, budget, timings, result=result)
        self.assertEqual(status, 1, result)
        self.assertEqual(result["outcome"], "incomplete")
        self.assertFalse(result["adjudicated"])
        self.assertNotIn("adjudicator", self.calls)
        self.assertEqual([r["role"] for r in result["reviews"]], ["physical", "independent"])
        self.assert_retained(run, result["review_executions"])
        self.assert_concurrent_initial_reviews(run)

    def test_derived_budget_catches_serialised_reviewers(self):
        require_toolchain(native=True)
        self.configure_coordinator_scope()
        self.settings["circuit"]["sleep"] = .6
        self.settings["physical"]["sleep"] = .2
        budget, timings = self.coordinator_budget(waves=1)
        write_json(self.root / "scope.json", self.scope)
        run = self.root / "coordinator-serial"
        real_executor = ThreadPoolExecutor
        with patch(
            "agent_ee.reviewers.ThreadPoolExecutor",
            side_effect=lambda max_workers=None, *a, **kw: real_executor(max_workers=1, *a, **kw),
        ), patch("agent_ee.reviewers.dispatch", side_effect=self.harness), contextlib.redirect_stdout(io.StringIO()):
            status = run_review(self.root / "scope.json", run, budget)
        result = read_json(run / "result.json")
        self.assertEqual(status, 0, result)
        with self.assertRaisesRegex(AssertionError, r"exceeded derived concurrent budget"):
            self.assert_concurrent_initial_reviews(run)

    def test_exhausted_coordinator_budget_reports_named_load_failure(self):
        timings = {"export_s": 12.5, "netlist_s": 0.45, "harness_s": 0.4}
        with self.assertRaisesRegex(AssertionError, r"^budget exceeded under load [0-9]+\.[0-9]{2} "):
            self.assert_within_budget(31.0, 30.0, timings, result={"state": "complete", "review_executions": []})
        with self.assertRaisesRegex(AssertionError, r"^budget exceeded under load [0-9]+\.[0-9]{2} "):
            self.assert_within_budget(
                10.0,
                30.0,
                timings,
                result={
                    "state": "complete",
                    "adjudicated": False,
                    "review_executions": [{"role": "circuit", "execution": "ok"}, {"role": "physical", "execution": "ok"}],
                },
            )


    def test_owner_sigint_cancels_process_groups_and_queued_roles(self):
        require_toolchain(native=True)
        self.assert_owner_cancellation("waiting")

    def test_owner_sigint_during_preflight_cancels_probes_before_provider(self):
        require_toolchain(native=True)
        self.assert_owner_cancellation('preflight')

    def test_owner_sigint_during_launch_cancels_new_process_groups(self):
        require_toolchain(native=True)
        self.assert_owner_cancellation("launch")

    def assert_owner_cancellation(self, phase):
        budget, timings = self.coordinator_budget(waves=1)
        driver_budget = max(120, math.ceil(budget * 2))
        driver = r'''import json, os, subprocess, sys, time
from pathlib import Path
from unittest.mock import patch
from test_concurrent_reviews import ConcurrentReviewTests
from agent_ee.cli import run_review
from agent_ee.storage import write_json
case = ConcurrentReviewTests()
case.setUp()
root = Path(sys.argv[1])
case.root = root
source = root / "source"
source.mkdir(exist_ok=True)
(source / "harness.py").write_text("import os, subprocess, time\nfrom pathlib import Path\nchild = subprocess.Popen(['/usr/bin/sleep', '60'])\nPath('experiments/pids').write_text(str(os.getpid()) + ' ' + str(child.pid))\nprint('partial output', flush=True)\ntime.sleep(60)\n")
case.profile['reviewers'].append({**case.profile['reviewers'][0], 'role': 'queued'})
for role in case.profile['reviewers']:
    role['timeout_s'] = 60
fixture = Path('fixtures/rc/control.scope.json').resolve()
case.scope = json.loads(fixture.read_text())
for entry in case.scope['inputs']:
    entry['path'] = str((fixture.parent / entry['path']).resolve())
case.scope['inputs'].append({'path': str(source), 'at': 'design'})
case.scope['review_profile'] = case.profile
case.scope['checks'] = [{'id': 'netlist', 'kind': 'netlist'}]
write_json(root / 'scope.json', case.scope)
# Keep the real reviewer adapters' scheduling boundary and real executor;
# only substitute a networkless scripted model call.
def harness(run, role, definition, prompt, schema, timeout, **kwargs):
    if role == 'adjudicator':
        raise AssertionError('adjudicator launched after cancellation')
    work = Path(run) / 'review-work' / role
    (work / 'experiments').mkdir(parents=True)
    from agent_ee.executor import execute
    kwargs.pop('executable', None)
    return execute(Path(run) / 'inputs', work,
        ['/usr/bin/python3', '/candidate/design/harness.py'], timeout=timeout, **kwargs), None
from contextlib import ExitStack
real_popen = subprocess.Popen
from agent_ee.executor import sandbox_argv as real_sandbox
def slow_probe(inputs, work, argv, **kwargs):
    if argv == ['/usr/bin/true']:
        code = "import subprocess,time; from pathlib import Path; subprocess.Popen(['/usr/bin/sleep','60']); Path('probe-start').write_text('started'); print('partial probe',flush=True); time.sleep(60)"
        argv = ['/usr/bin/python3', '-c', code]
    return real_sandbox(inputs, work, argv, **kwargs)
def launching(*args, **kwargs):
    proc = real_popen(*args, **kwargs)
    if '/candidate/design/harness.py' in args[0]:
        Path(kwargs.get('cwd', root), 'launch-' + str(proc.pid)).touch()
        time.sleep(.8)
    return proc
with ExitStack() as stack:
    stack.enter_context(patch('agent_ee.reviewers.dispatch', side_effect=harness))
    if sys.argv[2] == 'preflight':
        stack.enter_context(patch('agent_ee.executor.sandbox_argv', side_effect=slow_probe))
    if sys.argv[2] == 'launch':
        stack.enter_context(patch('agent_ee.executor.subprocess.Popen', side_effect=launching))
    status = run_review(root / 'scope.json', root / 'run', int(sys.argv[3]))
sys.exit(0 if status == 1 else 2)
'''
        process = subprocess.Popen([sys.executable, '-c', driver, str(self.root), phase, str(driver_budget)],
            env={**os.environ, "PYTHONPATH": "src:tests"}, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        run = self.root / "run"
        wait_start = time.monotonic()
        deadline = wait_start + budget
        marker = 'review-work/*/preflight/probe/probe-start' if phase == 'preflight' else 'review-work/*/experiments/pids'
        while len(list(run.glob(marker))) < 2:
            if process.poll() is not None:
                self.fail(str(process.communicate()) + str(read_json(run / "result.json").get("error")))
            if time.monotonic() >= deadline:
                self.assert_within_budget(
                    time.monotonic() - wait_start,
                    budget,
                    timings,
                    context=f"owner cancellation ({phase}) reviewer startup",
                )
            time.sleep(.02)
        # Namespace PIDs are not host PIDs: inspect host descendants through /proc.
        def descendants(pid):
            children = [child for task in Path(f"/proc/{pid}/task").glob("*/children")
                        for child in task.read_text().split()]
            return [int(child) for child in children] + [nested for child in children for nested in descendants(child)]
        if phase == "launch":
            self.assertEqual(len(list(self.root.glob("launch-*"))), 2)
        children = descendants(process.pid)
        self.assertGreaterEqual(len(children), 4)
        start = time.monotonic()
        process.send_signal(signal.SIGINT)
        try:
            stdout, stderr = process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            # Clean up the red regression without leaving scripted reviewers.
            for pid in reversed(children):
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.kill()
            process.communicate()
            self.fail("owner interruption did not return within 3 seconds")
        self.assertEqual(process.returncode, 0, (stdout, stderr))
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 3)
        print(f"SIGINT during {phase}: coordinator returned in {elapsed:.3f}s", flush=True)
        for pid in children:
            path = Path(f"/proc/{pid}/stat")
            self.assertTrue(not path.exists() or path.read_text().split()[2] == "Z", f"child {pid} survived")
        result = read_json(run / "result.json")
        self.assertEqual(result["state"], "interrupted")
        self.assertEqual(result["outcome"], "incomplete")
        self.assertFalse(result["adjudicated"])
        expected_execution = 'not_run' if phase == 'preflight' else 'interrupted'
        self.assertEqual({r["role"]: r["execution"] for r in result["review_executions"]},
            {"circuit": expected_execution, "physical": expected_execution, "queued": "not_run"})
        self.assertFalse((run / "review-work/queued").exists())
        for role in ("circuit", "physical"):
            work = run / "review-work" / role
            if phase == 'preflight':
                self.assertEqual(read_json(work / 'preflight/receipt.json')['result'], 'refused')
                self.assertFalse((work / 'experiments').exists())
                work = work / 'preflight/probe'
                self.assertIn('partial probe', (work / 'stdout.log').read_text())
                self.assertEqual(read_json(work / 'execution.json')['execution'], 'interrupted')
                continue
            self.assertEqual(read_json(work / "execution.json")["execution"], "interrupted")
            self.assertIn("partial output", (work / "stdout.log").read_text())
            self.assertTrue((work / "experiments/pids").exists())

    @toolchain_test
    def test_global_drift_preserves_admitted_peer_and_refuses_other_provider(self):
        from threading import Event, Lock
        from agent_ee.cli import runtime_files
        run, packet = self.prepare()
        runtime = runtime_files()
        provider_started = Event()
        observed = Lock()
        count = 0
        def observe(**kwargs):
            nonlocal count
            with observed:
                count += 1
                first = count == 1
            if first:
                return runtime
            if not provider_started.wait(3):
                raise AssertionError('first role did not reach provider')
            return {**runtime, 'changed': {}}
        def provider(*args, **kwargs):
            provider_started.set()
            return self.harness(*args, **kwargs)
        with patch('agent_ee.cli.runtime_files', side_effect=observe), \
             patch('agent_ee.reviewers.dispatch', side_effect=provider):
            reviews, records = initial_reviews(run, packet, time.monotonic() + 5, launch_context=self.context)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(reviews), 1)
        self.assertCountEqual([record['execution'] for record in records], ['ok', 'not_run'])
        self.assertTrue(any('runtime_drift' in record.get('error', '') for record in records))
