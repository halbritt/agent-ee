"""AGENTEE-520: public placement must never discard declared intent."""
import copy
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from agent_ee import placement_intent, placement_job as job
from agent_ee.storage import digest, read_json, write_json
from tests.test_placement_job import Fixture, groups, intent


class PlacementIntentTests(unittest.TestCase):
    def test_locality_unknown_endpoint_is_reported_at_its_exact_path(self):
        locality = dict(schema='agent-ee.electrical-locality/1', obligations=[
            dict(id='supply', kind='single-leg', metric='pad-centre-to-centre', leg={
                'from': dict(reference='U1', pad='1', net='VCC', side='B'),
                'to': dict(reference='C1', pad='1', net='VCC')})])
        with self.assertRaisesRegex(job.JobRefusal,
                r'intent.electrical_locality.obligations\[0\].leg.from.side'):
            job.validate_intent({'electrical_locality': locality}, {'groups': []})

    def test_source_admission_preserves_unsupported_intent_message(self):
        with self.assertRaises(ValueError) as error:
            placement_intent.admit_source({'assembly': {'sides': 'B'}}, {'groups': []})
        self.assertEqual(str(error.exception), 'intent.assembly.sides: side selection is unsupported')

    def test_build_rejects_unsupported_intent_before_creating_a_run(self):
        from agent_ee import build_workflow as build
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'inputs'
            source.mkdir()
            write_json(source / 'intent.json', {'assembly': {'sides': 'B'}})
            write_json(source / 'groups.json', {'groups': []})
            plan = dict(schema=build.SCHEMA, source='inputs', envelope='envelope.json',
                requirements=dict(map='map.json', record='record.json', spec='spec.md'),
                parts=dict(manifest='parts.json', circuit='circuit.json', layout='layout.json'),
                placement=dict(intent='intent.json', groups='groups.json', budget_s=10,
                               search={}, router={}))
            write_json(root / 'build.json', plan)
            with self.assertRaisesRegex(ValueError, 'intent.assembly.sides'):
                build.run(root / 'build.json', root / 'run', through='requirements')
            self.assertFalse((root / 'run').exists())

    def test_unknown_nested_intent_and_malformed_declarations_refuse_with_paths(self):
        cases = [
            ({'assembly': {'sides': 'B'}}, {'groups': []}, 'intent.assembly.sides'),
            ({'blocks': [{'name': 'interface', 'refs': ['J1', 'J2'], 'side': 'B'}]},
             {'groups': []}, 'intent.blocks[0].side'),
            ({'edge_connectors': [{'ref': 'J1', 'edge': 'west', 'side': 'B'}]},
             {'groups': []}, 'intent.edge_connectors[0].side'),
            ({'edge_connectors': [{'ref': 'J1', 'edge': 'west', 'overhang_mm': {'mx': 2}}]},
             {'groups': []}, 'intent.edge_connectors[0].overhang_mm.mx'),
            ({'fixed_poses': [{'ref': 'R1', 'x': True, 'y': 2}]},
             {'groups': []}, 'intent.fixed_poses[0].x'),
            ({'fixed_poses': [{'ref': 'R1', 'x': 1, 'y': 2, 'rot': 45}]},
             {'groups': []}, 'intent.fixed_poses[0].rot'),
            ({'proximity': [{'ref': 'C1', 'near': 'U1', 'max_mm': 3, 'pads': {'C1': ['1']}}]},
             {'groups': []}, 'intent.proximity[0].pads.U1'),
            ({'proximity': [{'ref': 'C1', 'near': 'U1', 'max_mm': 3, 'basis': 'pad_edge',
                            'pads': {'C1': ['1'], 'U1': ['1']}}]},
             {'groups': []}, 'intent.proximity[0].basis'),
            ({'proximity': []}, {'groups': [{'name': 'a', 'anchor': 'R1', 'refs': ['R1'], 'near': ['b']}]},
             'groups.groups[0].near'),
            ({'fixed_poses': {}}, {'groups': []}, 'intent.fixed_poses'),
        ]
        for intent, groups, path in cases:
            with self.subTest(path=path), self.assertRaises(job.JobRefusal) as error:
                job.validate_intent(intent, groups)
            self.assertIn(path, str(error.exception))

    def test_public_ignored_intent_refuses_before_native_capture_and_preserves_resume(self):
        cases = [
            ({'keepouts': [{'name': 'reserved-over-J1', 'rect': [43, 48, 47, 52],
                           'sides': ['F'], 'allow': []}]}, 'intent.keepouts'),
            ({'assembly': {'sides': 'B', 'why': 'Bottom-only population'}}, 'intent.assembly.sides'),
            ({'blocks': [{'name': 'interface', 'refs': ['J1', 'J2'], 'side': 'B'}]}, 'intent.blocks[0].side'),
            ('{"fixed_poses": [], "fixed_poses": [{"ref": "R1"}]}', 'duplicate JSON field'),
        ]
        for declaration, path in cases:
            with self.subTest(path=path), tempfile.TemporaryDirectory() as tmp:
                self.assert_public_refusal_preserves_resume(Path(tmp), declaration, path)

    def assert_public_refusal_preserves_resume(self, root, declaration, path):
        files = dict(board=root / 'board.kicad_pcb', project=root / 'board.kicad_pro',
                     intent=root / 'intent.json', groups=root / 'groups.json')
        # Deliberately not native geometry: contract refusal must precede its load.
        files['board'].write_text('not a native board\n')
        write_json(files['project'], {})
        if isinstance(declaration, str):
            files['intent'].write_text(declaration)
        else:
            write_json(files['intent'], declaration)
        write_json(files['groups'], {'groups': []})
        config = dict(schema=job.CONFIG_SCHEMA, budget_s=10,
            inputs={k: dict(path=str(p), sha256=digest(p)) for k, p in files.items()},
            search={}, router=dict(checkout=str(root / 'missing-router')))
        write_json(root / 'job.json', config)
        out = root / 'result'
        done = subprocess.run([sys.executable, '-m', 'agent_ee.cli', 'place', 'run',
            str(root / 'job.json'), '--out', str(out)], capture_output=True, text=True, timeout=15)
        self.assertNotEqual(done.returncode, 0)
        self.assertTrue((out / 'job-report.json').exists(), done.stdout + done.stderr)
        report = read_json(out / 'job-report.json')
        self.assertEqual(report['status'], 'refused')
        self.assertIn(path, report['reason'])
        self.assertFalse((out / 'problem.json').exists())
        self.assertNotIn('engine', report)
        self.assertFalse(report['claims']['accepted'])
        files['intent'].write_text('{"unknown_changed_intent": true}')
        config['inputs']['intent']['sha256'] = digest(files['intent'])
        before = {p.relative_to(out): digest(p) for p in out.rglob('*') if p.is_file()}
        with self.assertRaisesRegex(job.JobRefusal, 'intent.unknown_changed_intent'):
            job.run_job(config, out, resume=True)
        self.assertEqual(before, {p.relative_to(out): digest(p) for p in out.rglob('*') if p.is_file()})


class NativeIntentTests(Fixture):
    def test_fixed_native_guard_still_refuses_before_materializing(self):
        problem, _ = self.problem()
        poses = [dict(uuid=row['uuid'], **row['pose'], moved=False) for row in problem['footprints']]
        fixed = next(row['uuid'] for row in problem['footprints'] if row['native_ref'] == 'FID')
        next(row for row in poses if row['uuid'] == fixed)['moved'] = True
        out = self.dir / 'rejected'
        with self.assertRaisesRegex(job.JobRefusal, 'protected footprint'):
            job.materialize(self.board, poses, out, self.project.read_bytes(),
                            normalized=job.normalize_intent(intent(), {'groups': []}))
        self.assertFalse(out.exists())

    def test_versioned_and_legacy_supported_intent_capture_identical_constraints(self):
        legacy = intent()
        versioned = dict(legacy, schema='agent-ee.placement-intent/1')
        before = copy.deepcopy(versioned)
        old_problem, old_record = job.capture(self.board, job.normalize_intent(legacy, groups()))
        new_problem, new_record = job.capture(self.board, job.normalize_intent(versioned, groups()))
        self.assertEqual(new_problem, old_problem)
        self.assertEqual(new_record, old_record)
        self.assertEqual(versioned, before)

    def test_changed_fixed_rotation_and_missing_proximity_pad_refuse(self):
        for field, value in [('rot', 90), ('side', 'B')]:
            changed = intent()
            changed['fixed_poses'][0][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(job.JobRefusal,
                    r'intent.fixed_poses\[0\].' + field):
                job.capture(self.board, job.normalize_intent(changed, groups()))
        changed = intent()
        changed['proximity'] = [dict(ref='R1', near='C1', max_mm=3,
                                   pads={'R1': ['99'], 'C1': ['1']})]
        with self.assertRaisesRegex(job.JobRefusal, r'intent.proximity\[0\].pads.R1'):
            job.capture(self.board, job.normalize_intent(changed, groups()))
