"""Exercise the real coordinator and sandbox tools with scripted agent responses.

These tests establish orchestration contracts, not live-model review quality or
qualification of the synthetic measurement fixture as a physical simulator.
"""
import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_ee.campaign import run_campaign, verify_campaign, campaign_feedback, check_proposal
from agent_ee.cli import run_review, verify
from agent_ee.empirical import bench_review
from agent_ee.storage import digest, read_json, write_json

try:
    from toolchain import require_toolchain, toolchain_test
except ImportError:  # module-qualified invocation from the repository root
    from tests.toolchain import require_toolchain, toolchain_test


class ImmutableInputGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-saveboard-guard-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source'
        project = self.source / 'project'
        project.mkdir(parents=True)
        # Original minimal project: no external symbols, footprints or libraries.
        (project / 'owned.kicad_pcb').write_text('''(kicad_pcb
 (version 20260206) (generator "pcbnew") (generator_version "10.0")
 (general (thickness 1.6))
 (layers (0 "F.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user))
 (setup (pad_to_mask_clearance 0))
)
''')
        for suffix in ('.kicad_pro', '.kicad_prl'):
            (project / ('owned' + suffix)).write_text('{}\n')
        (project / 'other.kicad_pro').write_text('{}\n')
        self.manifest = {'files': {p.relative_to(self.source).as_posix(): digest(p)
                                   for p in sorted(self.source.rglob('*')) if p.is_file()},
                         'scope': {'board': 'project/owned.kicad_pcb', 'checks': []}}
        self.state = {'mutable_inputs': ['project/owned.kicad_pcb'], 'baseline': {'files': {}}}
        self.design = self.root / 'design'
        shutil.copytree(self.source, self.design)

    def test_unchanged_project_and_board_only_edit_are_allowed(self):
        check_proposal(self.design, self.manifest, self.state)
        board = self.design / 'project/owned.kicad_pcb'
        board.write_text(board.read_text().replace('1.6', '1.7'))
        check_proposal(self.design, self.manifest, self.state)

    def test_sibling_hint_requires_same_stem_directory_and_changed_board(self):
        cases = [('project/owned.kicad_pro', False), ('project/owned.kicad_prl', False),
                 ('project/other.kicad_pro', True), ('elsewhere/owned.kicad_pro', True),
                 ('project/owned.kicad_sch', True)]
        for index, (rel, board_changed) in enumerate(cases):
            with self.subTest(rel=rel, board_changed=board_changed):
                manifest = {**self.manifest, 'files': dict(self.manifest['files'])}
                design = self.root / f'case-{index}'
                shutil.copytree(self.source, design)
                path = design / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                if not path.exists():
                    path.write_text('original')
                    manifest['files'][rel] = digest(path)
                path.write_text('changed')
                if board_changed:
                    (design / 'project/owned.kicad_pcb').write_text('changed board')
                with self.assertRaises(ValueError) as caught:
                    check_proposal(design, manifest, self.state)
                self.assertEqual(str(caught.exception),
                                 f'implementation changed immutable verification input: {rel}')

    def test_new_siblings_and_links_still_refuse(self):
        added = self.design / 'project/new.kicad_prl'
        added.write_text('{}\n')
        with self.assertRaisesRegex(ValueError, 'captured file inventory'):
            check_proposal(self.design, self.manifest, self.state)
        added.unlink()
        sibling = self.design / 'project/owned.kicad_pro'
        sibling.unlink()
        sibling.symlink_to(self.source / 'project/owned.kicad_pro')
        with self.assertRaisesRegex(ValueError, 'live link'):
            check_proposal(self.design, self.manifest, self.state)

    def test_native_saveboard_sibling_rewrites_are_refused_with_writer_rule(self):
        require_toolchain(native=True)
        from agent_ee.executor import execute
        work = self.root / 'saveboard'
        program = '''import shutil
import pcbnew
shutil.copytree('/candidate', '/work/design')
path = '/work/design/project/owned.kicad_pcb'
board = pcbnew.LoadBoard(path)
board.GetDesignSettings().SetBoardThickness(1700000)
assert pcbnew.SaveBoard(path, board)
'''
        record = execute(self.source, work, ['/usr/bin/python3', '-c', program], timeout=60)
        self.assertEqual(record['execution'], 'ok', (work / 'console.log').read_text())
        self.assertEqual(record['exit_code'], 0)
        self.assertIn('--unshare-net', record['sandbox_argv'])
        design = work / 'design'
        self.assertNotEqual(digest(design / 'project/owned.kicad_pcb'),
                            self.manifest['files']['project/owned.kicad_pcb'])
        with self.assertRaisesRegex(ValueError, 'likely pcbnew.SaveBoard side effect'):
            check_proposal(design, self.manifest, self.state)
        for suffix in ('.kicad_pro', '.kicad_prl'):
            rel = 'project/owned' + suffix
            with self.subTest(suffix=suffix):
                self.assertNotEqual(digest(design / rel), self.manifest['files'][rel])
                # Isolate each real native rewrite so both refusal paths are exercised.
                isolated = self.root / suffix[1:]
                shutil.copytree(self.source, isolated)
                for changed in ('project/owned.kicad_pcb', rel):
                    shutil.copyfile(design / changed, isolated / changed)
                with self.assertRaises(ValueError) as caught:
                    check_proposal(isolated, self.manifest, self.state)
                message = str(caught.exception)
                self.assertIn(f'implementation changed immutable verification input: {rel}', message)
                self.assertIn('pcbnew.SaveBoard', message)
                self.assertIn('likely', message)
                self.assertIn('sandboxed step runner', message)
                self.assertIn('staging copy', message)
                self.assertIn('immutable', message)
        # Adopt only the saved board from staging; the delivered project's siblings stay exact.
        shutil.copyfile(design / 'project/owned.kicad_pcb', self.design / 'project/owned.kicad_pcb')
        check_proposal(self.design, self.manifest, self.state)
        self.assertEqual({p.relative_to(self.source).as_posix(): digest(p)
                          for p in self.source.rglob('*') if p.is_file()}, self.manifest['files'])


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
                "evidence": [{"path": "checks/" + c["id"] + ".json", "locator": "outcome", "pointer": "/outcome"}]}
                for c in checks if c["outcome"] == "defect"]
            return {"execution": "ok"}, {"schema": "agent-ee.decisions/2", **identity, "decisions": decisions}
        self.assertNotIn('"initial_reviews"', prompt)
        self.assertIn('baseline', prompt)
        return {"execution": "ok"}, {"schema": "agent-ee.review/2", **identity,
            "packet_sha256": packet["packet_sha256"], "role": label,
            "coverage": ["protocol"], "limitations": ["scripted test response"], "findings": []}

    @toolchain_test
    def test_adjudicator_can_cite_verified_baseline(self):
        def answer(*args, **kwargs):
            record, value = self.agent(*args, **kwargs)
            if args[1] == 'adjudicator':
                value['decisions'][0]['evidence'].append({'path': 'baseline.json', 'locator': 'requirements/R1', 'pointer': '/requirements/0'})
            return record, value
        with patch('agent_ee.reviewers.dispatch', side_effect=answer), contextlib.redirect_stdout(io.StringIO()):
            run_review(self.root / 'scope.json', self.root / 'run', 30)
        result = verify(self.root / 'run')
        self.assertTrue(result['adjudicated'])
        self.assertEqual(result['findings'][0]['status'], 'accepted')
        execution = read_json(self.root / 'run/adjudicator-execution.json')
        self.assertEqual(execution['resubmissions_used'], 0)

    @toolchain_test
    def test_single_writer_repair_then_fresh_independent_reviews(self):
        with contextlib.redirect_stdout(io.StringIO()):
            status = run_campaign(self.root / "campaign.json", self.root / "campaign")
        state = read_json(self.root / "campaign/state.json")
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
    def test_campaign_verification_hashes_each_file_once_per_call(self):
        # AGENTEE-250: one verification call hashes each file once; no reuse crosses calls.
        import mmap
        import os
        import time
        from agent_ee import storage
        from agent_ee.cli import main
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_campaign(self.root / "campaign.json", self.root / "campaign"), 0)
        time.sleep(2.2)
        counts = {}
        original = storage._hash_stream

        def counted(stream, checkpoint):
            info = os.fstat(stream.fileno())
            key = (info.st_dev, info.st_ino)
            counts[key] = counts.get(key, 0) + 1
            if info.st_uid == 0:
                rooted.add(key)
            return original(stream, checkpoint)
        rooted = set()

        def hashes(action):
            counts.clear()
            with patch('agent_ee.storage._hash_stream', side_effect=counted):
                action()
            return dict(counts)
        root = self.root / "campaign"
        inputs = {(i.st_dev, i.st_ino) for i in (os.stat(p) for p in root.glob('runs/*/inputs/**/*') if p.is_file())}
        once = hashes(lambda: verify_campaign(root))
        self.assertTrue(inputs and inputs <= once.keys())
        self.assertEqual(max(once.values()), 1, once)
        # Every call re-hashes every file, root-owned runtime files (the measurement tool) included.
        repeated = hashes(lambda: [verify_campaign(root) for _ in range(3)])
        self.assertEqual({repeated[k] for k in inputs}, {3}, repeated)
        self.assertEqual(repeated, {k: 3 * v for k, v in once.items()})
        self.assertTrue(rooted and all(repeated[k] == 3 for k in rooted), rooted)
        with contextlib.redirect_stdout(io.StringIO()):
            command = hashes(lambda: self.assertEqual(main(['verify-campaign', str(root)]), 0))
        self.assertEqual(max(command.values()), 1, command)
        # A candidate byte changed between calls without any status change still fails closed.
        target = root / "runs/1/inputs/design/board"
        with target.open('r+b') as stream, mmap.mmap(stream.fileno(), 0) as mapped:
            mapped[0] = mapped[0]
            time.sleep(2.2)
            verify_campaign(root)
            before = os.stat(target)
            mapped[0] = ord('B')
            self.assertEqual(storage._status(os.stat(target)), storage._status(before))
            with self.assertRaisesRegex(ValueError, "candidate snapshot changed"):
                verify_campaign(root)
            mapped[0] = ord('b')
        # The same holds for a rewrite whose timestamps are restored.
        before = os.stat(target)
        verify_campaign(root)
        target.write_text("BOARD")
        os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
        with self.assertRaisesRegex(ValueError, "candidate snapshot changed"):
            verify_campaign(root)

    def test_finding_retry_limit_below_two_refused_before_campaign_creation(self):
        self.scope['checks'][0]['stage'] = 'schematic'
        self.scope['stages'] = {'schema': 'agent-ee.stages/1', 'order': ['schematic'],
            'subjects': {'schematic': {'path': 'design/schematic', 'kind': 'symbols',
                                      'references': ['R1']}}, 'finding_retry_limit': 1}
        for schema in ('agent-ee.scope/2', 'agent-ee.scope/3'):
            for limit in (-1, 0, 1):
                with self.subTest(schema=schema, limit=limit):
                    self.scope['schema'] = schema
                    self.scope['stages']['finding_retry_limit'] = limit
                    write_json(self.root / 'scope.json', self.scope)
                    out = self.root / f'refused-{schema[-1]}-{limit}'
                    with self.assertRaisesRegex(ValueError, 'finding_retry_limit'):
                        run_campaign(self.root / 'campaign.json', out)
                    self.assertFalse(out.exists())
                    self.assertEqual(self.calls, [])

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
        self.assertEqual(fresh['max_schema_resubmissions'], 1)
        self.assertEqual(verify(self.root / 'campaign/runs/3')['max_schema_resubmissions'], 1)
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

    def harness_runtime(self, name):
        """Pinned runtime with every harness present, and the same runtime after a host update of name."""
        base = {entry: {'path': '/usr/bin/true', 'sha256': digest('/usr/bin/true')}
                for entry in ('codex', 'codex_tool_host', 'claude', 'agy', 'bwrap', '/usr/bin/python3')}
        return base, {**base, name: {'path': '/usr/bin/true', 'sha256': 'replaced-by-host-update'}}

    def review_with_update_after_checks(self, name):
        from agent_ee.checks import run_checks as real_checks
        base, updated = self.harness_runtime(name)
        current = {'runtime': base}
        patch('agent_ee.cli.runtime_files', side_effect=lambda **kwargs: current['runtime']).start()
        def checks(*args, **kwargs):
            result = real_checks(*args, **kwargs)
            current['runtime'] = updated
            return result
        with patch('agent_ee.checks.run_checks', side_effect=checks), contextlib.redirect_stdout(io.StringIO()):
            run_review(self.root / 'scope.json', self.root / 'run', 30)
        return base, updated, read_json(self.root / 'run/result.json')

    @toolchain_test
    def test_unused_harness_update_after_checks_is_observed_and_review_completes(self):
        from agent_ee.cli import main
        base, updated, result = self.review_with_update_after_checks('agy')
        self.assertCountEqual(self.calls, ['circuit', 'physical', 'adjudicator'])
        self.assertEqual(result['state'], 'complete', result.get('error'))
        self.assertTrue(result['adjudicated'])
        self.assertEqual(result['runtime_observations'],
                         [{'name': 'agy', 'expected': base['agy'], 'observed': updated['agy']}])
        for role in ('circuit', 'physical', 'adjudicator'):
            receipt = read_json(self.root / f'run/review-work/{role}/preflight/receipt.json')
            self.assertEqual(receipt['result'], 'ok')
            self.assertEqual(receipt['runtime_classification'],
                             {'agy': 'unused', 'claude': 'unused', 'codex': 'used', 'codex_tool_host': 'used'})
            self.assertEqual([c['name'] for c in receipt['unused_runtime_changes']], ['agy'])
        self.assertIn('unused harness `agy`', (self.root / 'run/REPORT.md').read_text())
        observations = []
        self.assertEqual(verify(self.root / 'run', observations=observations), result)
        self.assertEqual([c['name'] for c in observations], ['agy'])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(['verify', str(self.root / 'run')]), 0)
        self.assertIn('observation: unused harness runtime agy changed', out.getvalue())

    @toolchain_test
    def test_used_harness_update_after_checks_still_refuses_review(self):
        _, _, result = self.review_with_update_after_checks('codex')
        self.assertEqual(self.calls, [])
        self.assertEqual(result['outcome'], 'incomplete')
        self.assertIn('implementation or tool runtime changed during review; rerun required', result['error'])
        self.assertTrue(all('runtime_drift' in r.get('error', '') for r in result['review_executions']))
        with self.assertRaisesRegex(ValueError, 'tool runtime changed'):
            verify(self.root / 'run')

    @toolchain_test
    def test_retained_campaign_observes_unused_harness_update_and_refuses_used(self):
        from agent_ee.campaign import implement as real_implement
        from agent_ee.cli import main
        base, updated = self.harness_runtime('agy')
        current = {'runtime': base}
        patch('agent_ee.cli.runtime_files', side_effect=lambda **kwargs: current['runtime']).start()
        def update(*args, **kwargs):
            # The writer's pinned environment is the campaign start, before this update.
            current['runtime'] = updated
            return real_implement(*args, **kwargs)
        root = self.root / 'campaign'
        with patch('agent_ee.campaign.implement', side_effect=update), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_campaign(self.root / 'campaign.json', root), 0)
        self.assertIn('implementation', self.calls)
        receipt = read_json(root / 'implementations/1/review-work/implementation/preflight/receipt.json')
        self.assertEqual(receipt['result'], 'ok')
        self.assertEqual([c['name'] for c in receipt['unused_runtime_changes']], ['agy'])
        observations = []
        self.assertEqual(verify_campaign(root, observations=observations)['state'], 'criteria_satisfied')
        self.assertEqual([c['name'] for c in observations], ['agy'])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(['verify-campaign', str(root)]), 0)
        self.assertIn('observation: unused harness runtime agy changed', out.getvalue())
        current['runtime'] = {**updated, 'codex': {'path': '/usr/bin/true', 'sha256': 'replaced-by-host-update'}}
        with self.assertRaisesRegex(ValueError, 'tool runtime changed'):
            verify_campaign(root)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(['verify-campaign', str(root)]), 2)

    @toolchain_test
    def test_retained_mixed_harness_campaign_gates_on_writer_harness(self):
        from agent_ee.cli import main
        config = read_json(self.root / 'campaign.json')
        write_json(self.root / 'campaign.json', {**config, 'implementation': {**config['implementation'], 'harness': 'claude'}})
        base, writer_updated = self.harness_runtime('claude')
        current = {'runtime': base}
        patch('agent_ee.cli.runtime_files', side_effect=lambda **kwargs: current['runtime']).start()
        root = self.root / 'campaign'
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_campaign(self.root / 'campaign.json', root), 0)
        self.assertIn('implementation', self.calls)
        receipt = read_json(root / 'implementations/1/review-work/implementation/preflight/receipt.json')
        self.assertEqual(receipt['runtime_classification']['claude'], 'used')
        state = read_json(root / 'state.json')
        self.assertEqual(state['state'], 'criteria_satisfied')
        self.assertGreater(len(state['history']), 1)
        # The writer produced the candidate, so its binary changing is integrity drift.
        current['runtime'] = writer_updated
        with self.assertRaisesRegex(ValueError, 'tool runtime changed'):
            verify_campaign(root)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(['verify-campaign', str(root)]), 2)
        # A single review run never invoked the writer; its own verify only observes it.
        observations = []
        verify(root / 'runs/1', current_source=False, observations=observations)
        self.assertEqual([c['name'] for c in observations], ['claude'])
        # A harness neither the profile nor the writer invoked is one observation for the campaign.
        _, unused_updated = self.harness_runtime('agy')
        current['runtime'] = unused_updated
        observations = []
        self.assertEqual(verify_campaign(root, observations=observations)['state'], 'criteria_satisfied')
        self.assertEqual([c['name'] for c in observations], ['agy'])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(['verify-campaign', str(root)]), 0)
        self.assertEqual(out.getvalue().count('observation: unused harness runtime agy changed'), 1)

    @toolchain_test
    def test_capture_refusal_surfaces_the_run_error_not_a_missing_candidate(self):
        # An undeclared envelope obligation is refused inside capture, before any candidate exists.
        self.scope["baseline"]["requirements"][0]["id"] = "R-envelope-undeclared"
        write_json(self.root / "scope.json", self.scope)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_campaign(self.root / "campaign.json", self.root / "campaign"), 1)
        root = self.root / "campaign"
        state, result = read_json(root / "state.json"), read_json(root / "runs/1/result.json")
        self.assertFalse((root / "runs/1/candidate.json").exists())
        self.assertEqual(result["outcome"], "incomplete")
        self.assertIn("without a scope envelope declaration: R-envelope-undeclared", result["error"])
        self.assertEqual((state["state"], state["error"]),
                         ("incomplete", f"ValueError: run 1 captured no candidate: {result['error']}"))
        self.assertNotIn("candidate.json", state["error"])
        self.assertEqual((state["history"], self.calls), ([], []))
        self.assertEqual(verify_campaign(root)["state"], "incomplete")

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

    def witness_agent(self, *, twice_invalid=False, drop_finding=False):
        """Script one structural refusal while retaining the same engineering claim."""
        counts = {}
        (self.root / 'source/resubmission_harness.py').write_text(
            'import json, sys\nrequest = json.load(sys.stdin)\nprint(json.dumps(request["response"]))\n')
        def agent(run, label, definition, prompt, schema, timeout, **kwargs):
            record, response = self.agent(run, label, definition, prompt, schema, timeout, **kwargs)
            key = (str(run), label)
            counts[key] = counts.get(key, 0) + 1
            if label == 'physical' and read_json(Path(run) / 'checks/response.json')['outcome'] == 'defect':
                valid = counts[key] > 1 and not twice_invalid
                response['findings'] = [{
                    'id': 'output', 'title': 'Output exceeds R1', 'location': 'design/design.json',
                    'condition': 'load', 'mechanism': 'Declared measurement exceeds maximum',
                    'severity': 'major', 'assessment': 'defect', 'reasoning': '15 V exceeds 10 V',
                    'witnesses': [{'requirement_id': 'R1', 'check_id': 'response'}],
                    'evidence': [{'path': 'checks/response.json' if valid else 'inputs/design/board',
                                  'locator': 'outcome' if valid else 'board',
                                  **({'pointer': '/outcome'} if valid else {'lines': [1, 1]})}]}]
                if counts[key] > 1 and drop_finding:
                    response['findings'] = []
            if label == 'adjudicator':
                reviews = [read_json(p) for p in (Path(run) / 'reviews').glob('*.json')]
                for review in reviews:
                    for finding in review['findings']:
                        response['decisions'].append({
                            'finding_id': review['role'] + '/' + finding['id'], 'disposition': 'duplicate',
                            'duplicate_of': 'check/response', 'reason': 'Same executable R1 violation',
                            'evidence': [{'path': 'checks/response.json', 'locator': 'outcome', 'pointer': '/outcome'}]})
            if label == 'physical':
                from agent_ee.executor import execute
                work = Path(run) / 'review-work' / label
                (work / 'experiments').mkdir(exist_ok=True)
                execution = execute(Path(run) / 'inputs', work,
                    ['/usr/bin/python3', '/candidate/design/resubmission_harness.py'],
                    stdin=json.dumps({'response': response}), timeout=timeout, network=False,
                    cancellation=kwargs.get('cancellation'))
                record.update(execution)
                response = json.loads((work / 'stdout.log').read_text()) if execution['execution'] == 'ok' else None
            return record, response
        return agent

    @toolchain_test
    def test_witness_resubmission_completes_campaign_with_retained_refusal(self):
        """AGENTEE-115: the rung-1 witness refusal must not discard the campaign."""
        with patch('agent_ee.reviewers.dispatch', side_effect=self.witness_agent()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_campaign(self.root / 'campaign.json', self.root / 'campaign'), 0)
        root = self.root / 'campaign'
        state = verify_campaign(root)
        self.assertEqual(state['state'], 'criteria_satisfied')
        self.assertEqual(read_json(root / 'config.json')['max_schema_resubmissions'], 1)
        run = root / 'runs/1'
        result = verify(run)
        record = next(r for r in result['review_executions'] if r['role'] == 'physical')
        self.assertEqual(record['execution'], 'ok')
        self.assertEqual(record['resubmissions_used'], 1)
        self.assertEqual([a['execution'] for a in record['attempts']], ['malformed', 'ok'])
        first, second = [run / a['path'] for a in record['attempts']]
        for archive in (first, second):
            raw = read_json(archive / 'execution.json')
            self.assertEqual(raw['exit_code'], 0)
            self.assertIn('--unshare-net', raw['sandbox_argv'])
            console = archive / 'review-work/physical/stdout.log'
            self.assertEqual(json.loads(console.read_text()), read_json(archive / 'response.json'))
        error = read_json(first / 'execution.json')['error']
        self.assertEqual(error, 'witness needs an unchanged cited check result')
        self.assertTrue((second / 'prompt.txt').read_text().startswith((first / 'prompt.txt').read_text()))
        self.assertEqual((second / 'prompt.txt').read_text(),
                         (first / 'prompt.txt').read_text() + '\nOUTPUT VALIDATION ERROR:\n' + error)
        self.assertEqual(read_json(first / 'schema.json'), read_json(second / 'schema.json'))
        receipts = [read_json(p / 'review-work/physical/preflight/receipt.json') for p in (first, second)]
        self.assertNotEqual(receipts[0]['invocation_id'], receipts[1]['invocation_id'])
        for field in ('definition', 'expectation_id', 'candidate_id', 'baseline_id', 'observed'):
            self.assertEqual(receipts[0][field], receipts[1][field])
        from agent_ee.findings import validate_review
        packet = read_json(run / 'packet.json')
        with self.assertRaisesRegex(ValueError, error):
            validate_review(read_json(first / 'response.json'), packet['candidate_id'], packet['run_id'],
                            packet['packet_sha256'], run)
        self.assertEqual(validate_review(read_json(second / 'response.json'), packet['candidate_id'],
                                        packet['run_id'], packet['packet_sha256'], run)['role'], 'physical')
        self.assertEqual(self.calls.count('physical'), 3)  # two calls in iteration one, one fresh in two
        self.assertEqual(len(state['history']), 2)
        (first / 'response.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'evidence changed'):
            verify(run)

    @toolchain_test
    def test_second_invalid_witness_is_terminal_and_unrepaired(self):
        with patch('agent_ee.reviewers.dispatch', side_effect=self.witness_agent(twice_invalid=True)), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_campaign(self.root / 'campaign.json', self.root / 'campaign'), 1)
        root = self.root / 'campaign'
        self.assertEqual(verify_campaign(root)['state'], 'incomplete')
        run = root / 'runs/1'
        result = verify(run)
        self.assertEqual(result['state'], 'incomplete')
        self.assertNotIn('adjudicator', self.calls)
        self.assertNotIn('implementation', self.calls)
        record = read_json(run / 'review-executions/physical.json')
        self.assertEqual(record['execution'], 'malformed')
        self.assertEqual(record['resubmissions_used'], 1)
        self.assertEqual(len(record['attempts']), 2)
        self.assertEqual(self.calls.count('physical'), 2)
        self.assertEqual(read_json(run / record['attempts'][0]['path'] / 'response.json'),
                         read_json(run / record['attempts'][1]['path'] / 'response.json'))
        self.assertFalse((run / 'reviews/physical.json').exists())

    @toolchain_test
    def test_zero_resubmission_configuration_preserves_single_shot_refusal(self):
        config = read_json(self.root / 'campaign.json')
        config['max_schema_resubmissions'] = 0
        write_json(self.root / 'campaign.json', config)
        with patch('agent_ee.reviewers.dispatch', side_effect=self.witness_agent()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_campaign(self.root / 'campaign.json', self.root / 'campaign'), 1)
        record = read_json(self.root / 'campaign/runs/1/review-executions/physical.json')
        self.assertEqual(record['execution'], 'malformed')
        self.assertEqual(record['resubmissions_used'], 0)
        self.assertEqual(self.calls.count('physical'), 1)
        verify_campaign(self.root / 'campaign')

    def test_invalid_resubmission_limits_refuse_before_campaign_creation(self):
        config = read_json(self.root / 'campaign.json')
        for value in (True, -1, 2, 1.5, '1', None):
            with self.subTest(value=value):
                config['max_schema_resubmissions'] = value
                write_json(self.root / 'campaign.json', config)
                with self.assertRaisesRegex(ValueError, 'max_schema_resubmissions'):
                    run_campaign(self.root / 'campaign.json', self.root / 'invalid')
                self.assertFalse((self.root / 'invalid').exists())

    @toolchain_test
    def test_resubmission_cannot_drop_the_invalid_finding(self):
        with patch('agent_ee.reviewers.dispatch', side_effect=self.witness_agent(drop_finding=True)), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_campaign(self.root / 'campaign.json', self.root / 'campaign'), 1)
        record = read_json(self.root / 'campaign/runs/1/review-executions/physical.json')
        self.assertEqual(record['execution'], 'refused')
        self.assertEqual(record['error'], 'resubmission changed finding set')
        self.assertNotIn('adjudicator', self.calls)
        self.assertEqual(verify_campaign(self.root / 'campaign')['state'], 'incomplete')

    @toolchain_test
    def test_adjudicator_resubmission_reuses_exact_finding_set(self):
        def agent(*args, **kwargs):
            record, response = self.agent(*args, **kwargs)
            if args[1] == 'adjudicator' and self.calls.count('adjudicator') == 1:
                response['decisions'][0]['evidence'][0]['path'] = 'outside.json'
            return record, response
        with patch('agent_ee.reviewers.dispatch', side_effect=agent), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_campaign(self.root / 'campaign.json', self.root / 'campaign'), 0)
        run = self.root / 'campaign/runs/1'
        record = read_json(run / 'adjudicator-execution.json')
        self.assertEqual(record['resubmissions_used'], 1)
        self.assertEqual([a['execution'] for a in record['attempts']], ['malformed', 'ok'])
        first, second = [run / a['path'] for a in record['attempts']]
        self.assertIn('citation path must name', read_json(first / 'execution.json')['error'])
        self.assertTrue((second / 'prompt.txt').read_text().startswith((first / 'prompt.txt').read_text()))
        self.assertEqual(read_json(first / 'response.json')['decisions'][0]['evidence'][0]['path'], 'outside.json')
        verify_campaign(self.root / 'campaign')

    @toolchain_test
    def test_writer_resubmission_reissues_summary_without_another_design(self):
        def agent(*args, **kwargs):
            record, response = self.agent(*args, **kwargs)
            if args[1] == 'implementation' and self.calls.count('implementation') == 1:
                response = {'summary': []}
            return record, response
        with patch('agent_ee.reviewers.dispatch', side_effect=agent), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_campaign(self.root / 'campaign.json', self.root / 'campaign'), 0)
        root = self.root / 'campaign'
        work = root / 'implementations/1'
        record = read_json(work / 'implementation-execution.json')
        self.assertEqual(record['resubmissions_used'], 1)
        self.assertEqual([a['execution'] for a in record['attempts']], ['malformed', 'ok'])
        self.assertEqual(self.calls.count('implementation'), 2)
        verify_campaign(root)
        altered = {k: v for k, v in record.items() if k != 'attempts'}
        write_json(work / 'implementation-execution.json', altered)
        with self.assertRaisesRegex(ValueError, 'implementation execution record changed'):
            verify_campaign(root)
        write_json(work / 'implementation-execution.json', record)
        archive = work / record['attempts'][0]['path']
        (archive / 'prompt.txt').write_text('changed refused-call provenance')
        with self.assertRaisesRegex(ValueError, 'resubmission record changed'):
            verify_campaign(root)

    @toolchain_test
    def test_writer_resubmission_cannot_change_delivered_design(self):
        def agent(*args, **kwargs):
            record, response = self.agent(*args, **kwargs)
            if args[1] == 'implementation':
                if self.calls.count('implementation') == 1:
                    response = {'summary': []}
                else:
                    write_json(Path(args[0]) / 'review-work/implementation/design/design/design.json', {'value': 6})
            return record, response
        with patch('agent_ee.reviewers.dispatch', side_effect=agent), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_campaign(self.root / 'campaign.json', self.root / 'campaign'), 1)
        work = self.root / 'campaign/implementations/1'
        record = read_json(work / 'implementation-execution.json')
        self.assertEqual(record['execution'], 'refused')
        self.assertEqual(record['error'], 'resubmission changed implementation design')
        self.assertFalse((work / 'proposal.scope.json').exists())
        self.assertEqual(verify_campaign(self.root / 'campaign')['state'], 'incomplete')

    @toolchain_test
    def test_runtime_drift_between_refusal_and_resubmission_never_dispatches_again(self):
        from agent_ee.cli import runtime_files
        observed = runtime_files()
        scripted = self.witness_agent()
        def call(*args, **kwargs):
            record, response = scripted(*args, **kwargs)
            if args[1] == 'physical' and response is not None:
                drift = patch('agent_ee.cli.runtime_files', return_value={
                    **observed, 'codex': {**observed['codex'], 'sha256': '0' * 64}})
                drift.start()
                self.addCleanup(drift.stop)
            return record, response
        with patch('agent_ee.reviewers.dispatch', side_effect=call), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_campaign(self.root / 'campaign.json', self.root / 'campaign'), 1)
        run = self.root / 'campaign/runs/1'
        record = read_json(run / 'review-executions/physical.json')
        self.assertEqual(self.calls.count('physical'), 1)
        self.assertEqual([a['execution'] for a in record['attempts']], ['malformed', 'not_run'])
        self.assertIn('runtime_drift', record['error'])
        self.assertEqual(read_json(run / 'result.json')['state'], 'incomplete')
        self.assertNotIn('adjudicator', self.calls)

    @toolchain_test
    def test_initial_call_interruption_keeps_verifiable_attempt_provenance(self):
        def agent(*args, **kwargs):
            record, response = self.agent(*args, **kwargs)
            if args[1] == 'physical':
                raise KeyboardInterrupt
            return record, response
        with patch('agent_ee.reviewers.dispatch', side_effect=agent), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_review(self.root / 'scope.json', self.root / 'run', 30), 1)
        run = self.root / 'run'
        result = verify(run)
        self.assertEqual(result['state'], 'interrupted')
        record = next(r for r in result['review_executions'] if r['role'] == 'physical')
        self.assertEqual(record['execution'], 'interrupted')
        self.assertEqual(len(record['attempts']), 1)

    @toolchain_test
    def test_malformed_check_output_is_executed_once_without_resubmission(self):
        (self.root / 'source/adapter.py').write_text('raise RuntimeError("scripted parser failure")\n')
        solver = self.root / 'source/solver.py'
        solver.write_text(solver.read_text() +
            '\nwith Path("check-invocations.log").open("a") as marker:\n    marker.write("executed\\n")\n')
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_campaign(self.root / 'campaign.json', self.root / 'campaign'), 1)
        run = self.root / 'campaign/runs/1'
        self.assertEqual((run / 'work/response/tool/check-invocations.log').read_text().splitlines(), ['executed'])
        result = verify(run)
        self.assertEqual(result['checks'][0]['outcome'], 'unknown')
        self.assertNotEqual(result['checks'][0]['execution'], 'ok')
        self.assertEqual(len(result['checks']), 1)
        self.assertFalse((run / 'review-attempts/response').exists())
        self.assertNotIn('implementation', self.calls)

    @toolchain_test
    def test_missing_failed_writer_workspace_refuses_campaign_verification(self):
        import shutil
        def agent(*args, **kwargs):
            record, response = self.agent(*args, **kwargs)
            if args[1] == 'implementation':
                response = {'summary': []}
            return record, response
        with patch('agent_ee.reviewers.dispatch', side_effect=agent), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_campaign(self.root / 'campaign.json', self.root / 'campaign'), 1)
        root = self.root / 'campaign'
        self.assertEqual(verify_campaign(root)['state'], 'incomplete')
        shutil.rmtree(root / 'implementations/1')
        with self.assertRaisesRegex(ValueError, 'implementation execution record changed'):
            verify_campaign(root)


class MutableIntentSchematicCampaignTests(unittest.TestCase):
    """Scripted subscription responses; real emitter, annotation and receipt executor."""

    def setUp(self):
        try:
            from tests.test_stage_receipts import CampaignStageReceiptTests
            from tests.test_schematic_stage import prepare, INTENT, SCH, BOARD
        except ImportError:
            from test_stage_receipts import CampaignStageReceiptTests
            from test_schematic_stage import prepare, INTENT, SCH, BOARD
        self.fixture = CampaignStageReceiptTests
        self.fixture.setUp(self)
        require_toolchain(native=True)
        from agent_ee.cli import runtime_files
        runtime = runtime_files()
        runtime['kicad-cli'] = {'path': '/usr/bin/kicad-cli', 'sha256': digest('/usr/bin/kicad-cli')}
        patch('agent_ee.cli.runtime_files', return_value=runtime).start()
        self.intent, self.sch, self.board = INTENT, SCH, BOARD
        source = self.root / 'source'
        self.stage = prepare(source)
        scope = read_json(self.root / 'scope.json')
        scope.update(board=BOARD, schematic=SCH, mutable_inputs=self.stage['mutable_inputs'])
        scope['checks'][0]['stage'] = 'schematic'
        scope['checks'].insert(0, {'id': 'erc', 'kind': 'erc', 'stage': 'schematic'})
        scope['baseline']['requirements'].append({'id': 'R-native',
            'statement': 'Native schematic ERC has no violations',
            'source': {'path': 'design/requirements', 'locator': 'R-native'}, 'checks': ['erc']})
        scope['stages'] = {'schema': 'agent-ee.stages/1', 'order': ['schematic'],
            'subjects': {'schematic': {'path': SCH, 'kind': 'symbols', 'references': ['U1', 'C1', 'J1']}},
            'finding_retry_limit': 3}
        # A protocol-only requirement forces a writer turn, not simulator evidence.
        # Native evidence comes from the production steps.
        (source / 'solver.py').write_text('''from pathlib import Path
import json
intent=json.loads(Path('/candidate/design/generation/connectivity-intent.json').read_text())
name=next(n['name'] for n in intent['nets'] if n['id']=='vout')
Path('raw.json').write_text(json.dumps({'value': 5 if name=='VOUT_FIXED' else 15}))
''')
        write_json(self.root / 'scope.json', scope)
        self.edit = 'intent'
        self.config['max_seconds'] = 120

    def agent(self, run, label, *args, **kwargs):
        if label == 'implementation':
            design = Path(run) / 'review-work/implementation/design'
            if self.edit == 'intent':
                intent = read_json(design / self.intent)
                next(n for n in intent['nets'] if n['id'] == 'vout')['name'] = 'VOUT_FIXED'
                write_json(design / self.intent, intent)
            else:
                path = design / (self.sch if self.edit == 'schematic' else self.board)
                path.write_bytes(path.read_bytes() + b'\n')
        return self.fixture.agent(self, run, label, *args, **kwargs)

    def campaign(self, steps):
        return self.fixture.campaign(self, steps)

    @toolchain_test
    def test_intent_mutable_schematic_receipted(self):
        status, state = self.campaign(self.stage['implementation_steps'])
        self.assertEqual((status, state['state']), (0, 'criteria_satisfied'), state.get('error'))
        root = self.root / 'campaign'
        index = read_json(root / 'implementations/1/stage-receipts.json')
        receipts = [read_json(root / 'implementations/1' / link['path']) for link in index['receipts']]
        initial = read_json(root / 'runs/1/candidate.json')
        final = read_json(root / 'runs/2/candidate.json')
        self.assertNotEqual(initial['files'][self.intent], receipts[0]['inputs'][self.intent])
        self.assertEqual(receipts[0]['inputs'][self.intent], final['files'][self.intent])
        self.assertEqual(receipts[1]['inputs'][self.intent], final['files'][self.intent])
        self.assertEqual(receipts[0]['outputs'][self.sch], receipts[1]['inputs'][self.sch])
        self.assertEqual(receipts[1]['outputs'][self.board], final['files'][self.board])
        prompt = (root / 'implementations/1/implementation-prompt.txt').read_text()
        declared = json.loads(prompt.split('\nDeclared implementation steps:\n', 1)[1])
        self.assertIn(self.intent, declared['implementation_steps'][0]['inputs'])
        self.assertIn('VOUT_FIXED', (root / 'runs/2/inputs' / self.board).read_text())
        self.assertEqual(verify_campaign(root)['state'], 'criteria_satisfied')

    @toolchain_test
    def test_writer_schematic_edit_refused(self):
        self.edit = 'schematic'
        status, state = self.campaign(self.stage['implementation_steps'])
        self.assertEqual((status, state['state']), (1, 'incomplete'))
        self.assertIn('writer_step_output_edit', state['error'])
        self.assertFalse((self.root / 'campaign/implementations/1/receipts').exists())

    @toolchain_test
    def test_writer_board_edit_refused(self):
        self.edit = 'board'
        status, state = self.campaign(self.stage['implementation_steps'])
        self.assertEqual((status, state['state']), (1, 'incomplete'))
        self.assertIn('writer_step_output_edit', state['error'])

    @toolchain_test
    def test_done_requires_emit_receipt(self):
        # Checks pass before campaign start, but no declared producer ran.
        path = self.root / 'source/generation/connectivity-intent.json'
        intent = read_json(path)
        next(n for n in intent['nets'] if n['id'] == 'vout')['name'] = 'VOUT_FIXED'
        write_json(path, intent)
        status, state = self.campaign(self.stage['implementation_steps'])
        self.assertEqual((status, state['state']), (1, 'incomplete'))
        self.assertIn('stage_step_unreceipted: schematic/emit-schematic', state['error'])
        self.assertEqual(state['history'][-1]['outcome'], 'criteria_satisfied')
        self.assertEqual(verify_campaign(self.root / 'campaign')['state'], 'incomplete')
