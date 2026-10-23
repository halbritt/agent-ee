"""AGENTEE-500: real stage production/adoption and post-routing plane binding."""
import copy
from pathlib import Path
import shutil
from types import SimpleNamespace
import unittest

from agent_ee import add_planes, final_planes, stage_receipts, routing_adapter, rf_review
from agent_ee.executor import execute
from agent_ee.storage import digest, read_json, write_json
from agent_ee.scaffold import schematic as empty_schematic
from tests.test_add_planes import PlaneFixture
from tests.toolchain import require_kicad_route


class LatePlaneBindingTests(PlaneFixture, unittest.TestCase):
    def test_produced_report_adopted_and_final_consumer_accepts_with_refusal_controls(self):
        report_path = add_planes.report_path(self.plan_path)
        # A coordinator output slot is captured empty; no report digest is invented.
        (self.source/report_path).write_bytes(b'')
        schematic = self.source/'project/pod.kicad_sch'
        schematic.write_bytes(empty_schematic('d59616d7-b33b-41fe-bc77-f86de4d10183', '/', {},
                                             'Late plane binding fixture'))
        plan_link = {'path': self.plan_path, 'sha256': digest(self.source/self.plan_path)}
        write_json(self.source/'composition.json', {'schema': 'pod-capture.scope-composition/4', 'planes': [plan_link]})
        params = {'plane_obligations': [dict(plan=plan_link,
            producer={'path': report_path, 'stage': 'add-planes'},
            composition={'path': 'composition.json', 'sha256': digest(self.source/'composition.json')})]}
        self.assertNotIn(report_path, final_planes.binding_inputs(params))
        receipts = self.root/'receipts'
        plane_dir = receipts/'1-add-planes'
        spec = add_planes.step(Path(add_planes.__file__).resolve().parents[1], board=self.board, plan=self.plan_path)
        binding = {'implementation': 1, 'source_candidate_id': 'a'*64, 'receipt_index': 1}
        plane = stage_receipts.run_step(plane_dir, self.source, spec, binding)
        self.assertEqual(plane['status'], 'completed', plane)
        stage_receipts.adopt(plane_dir, plane, self.source)
        self.assertEqual(digest(self.source/report_path), plane['outputs'][report_path])
        anchor = {report_path: {'path': str(plane_dir/'receipt.json'), 'sha256': digest(plane_dir/'receipt.json')}}
        files = stage_receipts.file_tree(self.source)[0]
        complete, evidence = final_planes.complete_bindings(self.source, files, params, anchor, board=self.board)
        self.assertEqual(complete['plane_obligations'][0]['producer']['sha256'], plane['outputs'][report_path])
        self.assertTrue(evidence)
        self.assertEqual(final_planes.bound_plans(self.source, files, params,
                         producer_receipts=anchor, board=self.board), [self.plan_path])
        original = (self.source/report_path).read_bytes()
        refusals = []
        for case in ('no-receipt', 'missing', 'stale', 'wrong-report-plan', 'wrong-report-board', 'wrong-plan', 'wrong-board'):
            with self.subTest(case=case):
                selected = copy.deepcopy(params)
                board = self.board
                selected_anchor = anchor
                if case == 'no-receipt':
                    selected_anchor = None
                elif case == 'missing':
                    (self.source/report_path).unlink()
                elif case == 'stale':
                    (self.source/report_path).write_bytes(original+b'\n')
                elif case in ('wrong-report-plan', 'wrong-report-board'):
                    wrong = read_json(self.source/report_path)
                    wrong['plan_sha256' if case == 'wrong-report-plan' else 'board_after'] = '0'*64
                    write_json(self.source/report_path, wrong)
                elif case == 'wrong-plan':
                    selected['plane_obligations'][0]['plan']['sha256'] = '0'*64
                elif case == 'wrong-board':
                    board = 'project/other.kicad_pcb'
                current = stage_receipts.file_tree(self.source)[0]
                with self.assertRaises(ValueError) as refusal:
                    final_planes.bound_plans(self.source, current, selected,
                                             producer_receipts=selected_anchor, board=board)
                refusals.append({'case': case, 'error': str(refusal.exception)})
                (self.source/report_path).write_bytes(original)
        write_json(self.root/'binding-refusals.json', refusals)
        route = routing_adapter.step(project='project', board='pod.kicad_pcb', schematic='pod.kicad_sch',
            name='late-plane-probe', label='late-planes-native', checkout=require_kicad_route(), budget=120)
        route_dir = receipts/f"2-{route['step']}"
        routed = stage_receipts.run_step(route_dir, self.source, route, {**binding, 'receipt_index': 2})
        self.assertEqual(routed['status'], 'completed', routed)
        stage_receipts.adopt(route_dir, routed, self.source)
        self.assertEqual(routed['inputs'], stage_receipts.after_tree(plane))
        run = self.root/'final-run'
        shutil.copytree(self.source, run/'inputs')
        files = stage_receipts.file_tree(run/'inputs')[0]
        manifest = {'files': files, 'scope': {'board': self.board, 'checks': []}}
        retained = rf_review.capture_routing(run, manifest,
            {'receipt': {'path': str(route_dir/'receipt.json'), 'sha256': digest(route_dir/'receipt.json')}})
        self.assertEqual(retained['status'], 'verified', retained)
        drc = execute(run/'inputs', self.root/'final-drc', ['/usr/bin/kicad-cli', 'pcb', 'drc',
            '--format', 'json', '--severity-all', '-o', '/work/report.json', '/candidate/'+self.board], timeout=120)
        self.assertEqual(drc['exit_code'], 0, drc)
        work = run/'work/final'; work.mkdir(parents=True)
        ctx = SimpleNamespace(run_dir=run, manifest=manifest, inputs=run/'inputs', params=params,
            scope=manifest['scope'], work=work, result={'details': {}}, remaining=lambda: 180,
            evidence=lambda *paths: None)
        final_planes.check_captured(ctx, read_json(self.root/'final-drc/report.json'))
        self.assertEqual(ctx.result['details']['final_planes'][0]['status'], 'PASS')
        self.assertEqual(ctx.result['details']['plane_bindings'][0]['producer']['sha256'], plane['outputs'][report_path])
        write_json(run/'final-check.json', ctx.result)
        from agent_ee.checks import CheckError
        saved = (ctx.inputs/report_path).read_bytes()
        (ctx.inputs/report_path).write_bytes(saved+b'\n')
        ctx.manifest['files'][report_path] = digest(ctx.inputs/report_path)
        with self.assertRaisesRegex(CheckError, 'stale adopted plane producer report') as refusal:
            final_planes.check_captured(ctx, read_json(self.root/'final-drc/report.json'))
        write_json(run/'stale-check-refusal.json', {'execution': refusal.exception.execution,
                   'error': str(refusal.exception)})
        (ctx.inputs/report_path).write_bytes(saved)
        ctx.manifest['files'][report_path] = digest(ctx.inputs/report_path)
        import os
        if os.environ.get('LATE_PLANE_EVIDENCE'):
            shutil.copytree(self.root, Path(os.environ['LATE_PLANE_EVIDENCE']), dirs_exist_ok=True)
