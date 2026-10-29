"""AGENTEE-538: one builder for the placement problem, shared by capture and final-board evaluation.

Everything here is data: an in-memory board description, a normalized intent
bundle and the two documents built from them. No pcbnew, Rust binary or
external fixture is needed.
"""
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_ee import placement_job as job, placement_problem as problem_builder

MM = 1_000_000


def footprint(n, ref, x, y, *, pads=(), silk_only=False, mark_eligible=False, locked=False, occupancy=True,
              refusal=None, seats=None):
    uuid = f'00000000-0000-0000-0000-{n:012x}'
    return {
        'uuid': uuid, 'ref': ref, 'side': 'F', 'locked': locked, 'position_nm': [x, y], 'rotation_deg': 0.0,
        'quarter_turns': 0, 'silk_only': silk_only, 'mark_eligible': mark_eligible,
        'occupancy': ({'status': 'derived', 'label': 'derived_occupancy', 'rung': 'fab+pads',
                       'box': [x - MM, y - MM // 2, x + MM, y + MM // 2], 'sources': ['pads'],
                       'note': 'derived envelope; separation is not proof of package body separation'}
                      if occupancy and not silk_only else None),
        'occupancy_local_nm': [-MM, -MM // 2, MM, MM // 2] if occupancy and not silk_only else None,
        'occupancy_refusal': refusal,
        'occupancy_diagnostics': ['courtyard missing (metadata gap): derived_occupancy rung fab+pads']
        if occupancy and not silk_only else [],
        'classified_layers': {},
        'copper_local_nm': [{'rect_nm': [px - 300_000, -300_000, px + 300_000, 300_000], 'clearance_nm': 0}
                            for px, _ in pads],
        'mask_local_nm': [], 'holes_local_nm': [],
        'pads': [{'uuid': f'{uuid}-{i}', 'number': str(i + 1), 'net': net, 'local_nm': [px, 0],
                  'half_nm': [300_000, 300_000]} for i, (px, net) in enumerate(pads)],
        'seats_local_nm': seats or {'body:fab': None, 'body:silk': None}}


def board():
    return {'outline_rect_nm': [0, 0, 30 * MM, 20 * MM],
            'rules': {'copper_clearance_nm': 200_000, 'hole_to_hole_clearance_nm': 250_000,
                      'copper_edge_clearance_nm': 300_000, 'mask_min_web_nm': 100_000},
            'pair': {'width_nm': None, 'gap_nm': None, 'clearance_nm': 200_000},
            'footprints': [
                footprint(1, 'R1', 10 * MM, 6 * MM, pads=[(-750_000, 'A'), (750_000, 'GND')]),
                footprint(2, 'C1', 14 * MM, 6 * MM, pads=[(-750_000, 'A'), (750_000, 'B')]),
                footprint(3, 'FID', 3 * MM, 3 * MM, pads=[(0, '')], mark_eligible=True, occupancy=False,
                          refusal='FID: no courtyard, fab, silk or pad geometry for derived occupancy'),
                footprint(4, 'H1', 17 * MM, 9 * MM, pads=[(0, '')], mark_eligible=True, locked=True),
                footprint(5, '', 10 * MM, 6 * MM, silk_only=True, occupancy=False),
                footprint(6, 'J1', 2 * MM, 10 * MM, pads=[(0, 'A')],
                          seats={'body:fab': [-MM, -2 * MM, MM, 2 * MM], 'body:silk': None})]}


def bundle(**intent):
    declared = {'fixed_poses': [{'ref': 'FID', 'x': 3.0, 'y': 3.0, 'rot': 0.0, 'side': 'F', 'basis': 'mechanical'}],
                'edge_connectors': [{'ref': 'J1', 'edge': 'west', 'class': 'edge_receptacle', 'max_setback_mm': 0.5,
                                     'overhang_mm': {'min': 0, 'max': 1}}]}
    declared.update(intent)
    return job.normalize_intent(declared, {'groups': [{'name': 'g', 'anchor': 'R1', 'refs': ['R1', 'C1']}]})


def evaluated(normalized, description=None):
    intent = normalized['intent']
    return problem_builder.evaluate(description or board(), fixed_poses=intent.get('fixed_poses', []),
                                    edges=intent.get('edge_connectors', []), groups=normalized['groups']['groups'],
                                    rails=normalized['rails'], proximity=intent.get('proximity'))


class BuilderTests(unittest.TestCase):
    def test_capture_and_evaluate_documents_agree_as_data(self):
        normalized = bundle()
        captured, records = problem_builder.capture(board(), normalized)
        measured = evaluated(normalized)
        self.assertEqual(measured.pop('evaluate'), [{}])
        # With no proximity declared the two documents are the same problem, identity included.
        self.assertEqual(measured, captured)
        self.assertEqual(captured['weights'], problem_builder.WEIGHTS)
        roles = {r['native_ref']: r['role'] for r in captured['footprints']}
        self.assertEqual(roles, {'R1': 'movable', 'C1': 'movable', 'FID': 'fixed_copper_mark', 'H1': 'fixed_body',
                                 '': 'silk_only', 'J1': 'movable'})
        edge = next(r for r in captured['footprints'] if r['native_ref'] == 'J1')['edge']
        self.assertEqual(edge, {'edge': 'west', 'max_setback_nm': 500_000, 'overhang_nm': {'min': 0, 'max': MM},
                                'seat_local_nm': [-MM, -2 * MM, MM, 2 * MM], 'seat_basis': 'body:fab', 'order': 0})
        self.assertEqual([r['native_ref'] for r in records], ['R1', 'C1', 'FID', 'H1', '', 'J1'])
        self.assertEqual(next(r for r in records if r['native_ref'] == 'FID')['fixed_pose']['basis'], 'mechanical')

    def test_proximity_is_resolved_for_the_solver_and_declared_for_the_measurement(self):
        row = {'ref': 'R1', 'near': 'C1', 'max_mm': 2.0, 'basis': 'pad_edge', 'pads': {'R1': ['1']},
               'context': {'limit': 'derived_default'}}
        normalized = bundle(proximity=[row])
        captured, _ = problem_builder.capture(board(), normalized)
        measured = evaluated(normalized)
        self.assertEqual(captured['proximity'], [{
            'ref': 'R1', 'near': 'C1', 'max_nm': 2 * MM, 'ref_pads': ['00000000-0000-0000-0000-000000000001-0'],
            'near_pads': ['00000000-0000-0000-0000-000000000002-0', '00000000-0000-0000-0000-000000000002-1'],
            'subject_declared': True, 'partner_declared': False, 'basis': 'pad_edge', 'limit': 'derived_default'}])
        self.assertEqual(measured['proximity'], [row])
        self.assertEqual({k: v for k, v in measured.items() if k not in ('proximity', 'evaluate', 'problem_sha256')},
                         {k: v for k, v in captured.items() if k not in ('proximity', 'problem_sha256')})

    def test_one_role_rule(self):
        cases = {(False, False, False, False): 'movable', (False, False, True, False): 'fixed_body',
                 (False, True, False, False): 'movable', (False, True, True, False): 'fixed_body',
                 (False, True, False, True): 'fixed_copper_mark', (False, False, False, True): 'fixed_body',
                 (True, True, True, True): 'silk_only'}
        for (silk, mark, locked, declared), expected in cases.items():
            with self.subTest(silk=silk, mark=mark, locked=locked, declared=declared):
                fp = {'silk_only': silk, 'mark_eligible': mark, 'locked': locked}
                self.assertEqual(problem_builder.role(fp, declared), expected)

    def test_occupancy_refusal_applies_only_when_the_role_needs_a_body(self):
        normalized = bundle()
        problem_builder.capture(board(), normalized)  # FID is a declared mark: no body, no refusal.
        undeclared = bundle(fixed_poses=[])
        for build in (lambda: problem_builder.capture(board(), undeclared), lambda: evaluated(undeclared)):
            with self.assertRaisesRegex(job.JobRefusal, 'FID: no courtyard'):
                build()

    def test_evaluation_identity_ignores_the_evaluation_rows_only(self):
        measured = evaluated(bundle())
        self.assertEqual(measured['problem_sha256'], problem_builder.identity(dict(measured, evaluate=[{'x': 1}])))
        changed = copy.deepcopy(measured)
        changed['weights']['edge'] += 1
        self.assertNotEqual(problem_builder.identity(changed), measured['problem_sha256'])

    def test_edges_are_checked_on_authored_values_before_conversion(self):
        """One authored-value check (the intent validator's), also on the direct board_metrics edges."""
        description = board()
        fixed = bundle()['intent']['fixed_poses']
        for declared, message in (
                ({'ref': 'J9', 'edge': 'west'}, 'J9: edge connector must identify one'),
                ({'ref': 'J1', 'edge': 'up'}, r'edges\[0\]\.edge: expected west'),
                ({'ref': 'J1', 'edge': 'west', 'overhang_mm': {'lo': 0}}, r'edges\[0\]\.overhang_mm\.lo'),
                ({'ref': 'J1', 'edge': 'west', 'max_setback_mm': 'x'}, r'edges\[0\]\.max_setback_mm'),
                ({'ref': 'J1', 'edge': 'west', 'max_setback_mm': -1e-7}, r'edges\[0\]\.max_setback_mm'),
                ({'ref': 'J1', 'edge': 'west', 'overhang_mm': {'min': 2, 'max': 1}}, 'positive minimum'),
                # Review finding: 1.0000002 > 1.0000001 mm, but both round to 1000000 nm.
                ({'ref': 'J1', 'edge': 'west', 'overhang_mm': {'min': 1.0000002, 'max': 1.0000001}}, 'positive minimum'),
                ({'ref': 'J1', 'edge': 'west', 'overhang_mm': {'min': 1e-7}}, 'positive minimum')):
            with self.subTest(declared=declared), self.assertRaisesRegex(job.JobRefusal, message):
                problem_builder.evaluate(description, fixed_poses=fixed, edges=[declared])
        accepted = problem_builder.evaluate(description, fixed_poses=fixed, edges=[
            {'ref': 'J1', 'edge': 'west', 'overhang_mm': {'min': 0}}])
        self.assertEqual(next(r for r in accepted['footprints'] if r['native_ref'] == 'J1')['edge']['overhang_nm'],
                         {'min': 0, 'max': None})


class OutputCheckTests(unittest.TestCase):
    def test_check_output_requires_the_schema_and_this_problem(self):
        problem = {'problem_sha256': 'a' * 64}
        good = {'schema': problem_builder.OUTPUT_SCHEMA, 'problem_sha256': 'a' * 64}
        self.assertIs(problem_builder.check_output(good, problem), good)
        for output, code, message in ((dict(good, problem_sha256='b' * 64), 0, 'answered a different problem'),
                                      (dict(good, schema='other'), 0, 'placement engine failed'),
                                      ({'error': 'refused'}, 1, r'placement engine failed \(exit 1\): refused'),
                                      (None, 0, 'placement engine failed')):
            with self.subTest(output=output), self.assertRaisesRegex(job.JobRefusal, message):
                problem_builder.check_output(output, problem, code=code)

    def test_evaluator_applies_the_solver_output_check(self):
        description = board()
        path = Path(__file__)  # read-only stand-in; nothing native is loaded

        def engine(stdout):
            return subprocess.CompletedProcess([], 0, json.dumps(stdout).encode(), b'')

        def answer(payload, **changes):
            return dict({'schema': problem_builder.OUTPUT_SCHEMA, 'problem_sha256': payload['problem_sha256'],
                         'status': 'evaluated', 'evaluations': [{'edge_intent': {}}]}, **changes)

        expected = evaluated(bundle(), description)
        for stdout, message in ((answer(expected, problem_sha256='0' * 64), 'answered a different problem'),
                                (answer(expected, schema='agent-ee.rust-planning-output/1'), 'placement engine failed'),
                                (answer(expected, evaluations=[]), 'no evaluation of the saved board'),
                                (answer(expected), None)):
            with self.subTest(message=message), \
                    patch('agent_ee.placement_probe.extract', return_value={}), \
                    patch.object(job, '_describe_board', return_value=description), \
                    patch.object(job, 'rust_executable', return_value='engine'), \
                    patch.object(job, '_run_child', return_value=engine(stdout)) as child:
                call = lambda: job._evaluate_board_native(
                    path, fixed_poses=bundle()['intent']['fixed_poses'], edges=bundle()['intent']['edge_connectors'],
                    groups=bundle()['groups']['groups'], rails=bundle()['rails'], board=object(),
                    before=path.read_bytes())
                if message:
                    with self.assertRaisesRegex(job.JobRefusal, message):
                        call()
                else:
                    self.assertEqual(call(), {'edge_intent': {}})
                self.assertEqual(json.loads(child.call_args.kwargs['input_bytes']), expected)


class DirectMetricsEdgeTests(unittest.TestCase):
    """Review regression: board_metrics(edges=...) reaches the builder without intent normalization."""

    def test_rounding_cannot_hide_a_reversed_overhang_on_the_direct_metrics_path(self):
        import shutil
        import tempfile
        from toolchain import require_toolchain
        from test_placement_job import build_board
        require_toolchain(native=True)
        directory = Path(tempfile.mkdtemp(prefix='ee538-edges-'))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        path = build_board(directory / 'board.kicad_pcb')
        reversed_ = [{'ref': 'R1', 'edge': 'west', 'overhang_mm': {'min': 1.0000002, 'max': 1.0000001}}]
        with patch.object(job, '_run_child', side_effect=AssertionError('engine reached')):
            with self.assertRaisesRegex(job.JobRefusal, r'R1: edges\[0\]\.overhang_mm: positive minimum'):
                job.board_metrics(path, edges=reversed_)


class EngineValidatesItsInputTests(unittest.TestCase):
    """The engine reads the built document and validates the converted edge values at its own input boundary."""

    def run_engine(self, document):
        try:
            executable = job.rust_executable()
        except Exception as exc:  # noqa: BLE001 - any unavailable planner is a skip, never a pass
            self.skipTest(f'placement engine unavailable: {exc}')
        done = subprocess.run([str(executable)], input=json.dumps(document).encode(), capture_output=True,
                              timeout=60)
        return done.returncode, json.loads(done.stdout)

    def test_engine_accepts_the_built_document_and_refuses_a_reversed_interval(self):
        code, output = self.run_engine(evaluated(bundle()))
        self.assertEqual((code, output['status']), (0, 'evaluated'), output)
        problem_builder.check_output(output, evaluated(bundle()))
        # The engine checks the converted values it receives, independently of the authored check.
        reversed_ = copy.deepcopy(evaluated(bundle()))
        row = next(r for r in reversed_['footprints'] if r['native_ref'] == 'J1')
        row['edge']['overhang_nm'] = {'min': 2 * MM, 'max': MM}
        reversed_['problem_sha256'] = problem_builder.identity(reversed_)
        code, output = self.run_engine(reversed_)
        self.assertEqual(code, 1)
        self.assertIn('malformed overhang interval', output['error'])


if __name__ == '__main__':
    unittest.main()


class AcquisitionMechanicsTests(unittest.TestCase):
    """Original unit captures, not native/campaign placement acceptance."""
    def setUp(self):
        import tempfile
        from agent_ee import acquisition_mechanics as mechanics
        self.mechanics = mechanics
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        fixture = Path(__file__).resolve().parents[1] / 'fixtures/acquisition-qualification'
        (self.root / 'analysis').mkdir()
        for name in ('geometry-model.json', 'geometry_model.py'):
            (self.root / 'analysis' / name).write_bytes((fixture / name).read_bytes())
        (self.root / 'spec.md').write_text('Original authored CPU mechanics specification; no native board.')
        self.requirements = {'prose':[], 'requirements':[{'id':'ACQ-MECH-2','checks':[{'id':'engineering-geometry','params':{
            'limits': {'body_gap':{'min':.2},'body_overlap':{'max':0},'body_outside':{'max':0},
                       'access_obstruction':{'max':1e-8}}}}]}]}
        for key, limit in self.requirements['requirements'][0]['checks'][0]['params']['limits'].items():
            limit.update(unit='mm' if key=='body_gap' else 'mm2', condition='nominal25C-reviewed-acquisition')
        (self.root / 'requirement-map.json').write_text(json.dumps(self.requirements))
        self.path = self.root / 'placement-mechanics.json'
        self.path.write_text(json.dumps(mechanics.declaration(self.root)))
        self.recipe = mechanics.load(self.path)
        check = self.requirements['requirements'][0].pop('checks')
        self.requirements['requirements'][0]['spans'] = [{'mapping':{'checks':check}}]
        (self.root / 'requirement-map.json').write_text(json.dumps(self.requirements))
        self.path.write_text(json.dumps(mechanics.declaration(self.root)))
        self.recipe = mechanics.load(self.path)
        self.facts = {'board_sha256':'a'*64,'footprints':[]}
        self.description = board(); self.description['footprints'] = []
        for i, ref in enumerate(self.recipe['model']['packages'], 1):
            x,y = (i%6+1)*4*MM, (i//6+1)*5*MM
            row = footprint(i,ref,x,y,pads=[(0,'GND')],locked=ref.startswith('J'))
            self.description['footprints'].append(row)
            self.facts['footprints'].append({'uuid':row['uuid'],'ref':ref,'value':ref,'fpid':'Owned:'+ref,
                'side':'F','locked':row['locked'],'position_nm':[x,y],'rotation_deg':0,
                'pads':[{'uuid':row['pads'][0]['uuid'],'number':'1','net':'GND','position_nm':[x,y],
                         'rotation_deg':0,'shape':0,'size_nm':[100000,100000]}]})

    def test_exact_source_bodies_access_and_native_fixed_roles_reach_both_builders(self):
        binding = self.mechanics.bind(self.recipe,self.facts)
        desc = self.mechanics.attach(copy.deepcopy(self.description),binding,self.facts)
        normalized = job.normalize_intent({'fixed_poses':[]}, {'groups':[]})
        captured, records = problem_builder.capture(desc,normalized)
        measured = problem_builder.evaluate(desc)
        self.assertEqual(captured, {k:v for k,v in measured.items() if k!='evaluate'})
        j1 = next(r for r in captured['footprints'] if r['native_ref']=='J1')
        self.assertEqual(j1['source_body_local_nm'],[-1950000,-2000000,3950000,2500000])
        self.assertEqual(j1['access_local_nm'],[-2450000,-2500000,4450000,3000000])
        self.assertEqual({r['native_ref'] for r in records if r['role']=='fixed_body'},{'J1','J2','J3'})
        moved = copy.deepcopy(self.facts); moved['board_sha256']='b'*64
        c2 = next(f for f in moved['footprints'] if f['ref']=='C2')
        c2['position_nm'][0]+=MM; c2['pads'][0]['position_nm'][0]+=MM
        saved = copy.deepcopy(self.description)
        next(f for f in saved['footprints'] if f['ref']=='C2')['position_nm'][0] += MM
        self.mechanics.attach(saved,binding,moved)
        self.assertEqual(binding['original_board_sha256'],'a'*64)

    def test_missing_duplicate_stale_owner_pad_fixed_anchor_and_model_refuse(self):
        binding = self.mechanics.bind(self.recipe,self.facts)
        for change in ('missing','duplicate','pad','fixed','side','model'):
            facts=copy.deepcopy(self.facts); bound=copy.deepcopy(binding)
            if change=='missing': facts['footprints'].pop()
            elif change=='duplicate': facts['footprints'].append(copy.deepcopy(facts['footprints'][0]))
            elif change=='pad': facts['footprints'][0]['pads'][0]['number']='wrong'
            elif change=='fixed':
                fp=next(f for f in facts['footprints'] if f['ref']=='J1')
                fp['position_nm'][0]+=MM; fp['pads'][0]['position_nm'][0]+=MM
            elif change=='side': facts['footprints'][0]['side']='B'
            else: bound['recipe']['model']['packages']['C2']['size_mm'][0]=.1
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.mechanics.attach(copy.deepcopy(self.description),bound,facts)
        unlocked=copy.deepcopy(self.facts)
        next(f for f in unlocked['footprints'] if f['ref']=='J2')['locked']=False
        with self.assertRaisesRegex(ValueError,'natively locked'):
            self.mechanics.bind(self.recipe,unlocked)

    def test_model_and_requirement_source_drift_cannot_be_self_qualified(self):
        target=self.root/'analysis/geometry-model.json'; original=target.read_bytes()
        target.write_bytes(original+b' ')
        with self.assertRaisesRegex(ValueError,'source changed'):
            self.mechanics.load(self.path)
        self.path.write_text(json.dumps(self.mechanics.declaration(self.root)))
        with self.assertRaisesRegex(ValueError,'unsupported acquisition model'):
            self.mechanics.load(self.path)
        target.write_bytes(original)
        self.requirements['requirements'][0]['spans'][0]['mapping']['checks'][0]['params']['limits']['body_gap']['min']=.01
        (self.root/'requirement-map.json').write_text(json.dumps(self.requirements))
        self.path.write_text(json.dumps(self.mechanics.declaration(self.root)))
        with self.assertRaisesRegex(ValueError,'requirements differ'):
            self.mechanics.load(self.path)

    def test_declared_mechanics_cannot_disappear_from_native_or_promotion_gate(self):
        binding=self.mechanics.bind(self.recipe,self.facts)
        normalized={'mechanics':self.recipe,'mechanics_binding':binding}
        path=self.root/'saved.kicad_pcb';path.write_bytes(b'late saved move')
        report={'status':'defect','defects':[{'kind':'connector_access','ref':'J1','with':'C2'}],
                'binding_sha256':binding['binding_sha256'],'board_sha256':job.digest(path)}
        with patch.object(job,'_native_read',return_value=report) as native:
            result=job.check_mechanics(path,normalized)
            self.assertEqual(native.call_args.args[1],'_check_mechanics')
            self.assertEqual(native.call_args.args[2]['binding']['original_board_sha256'],'a'*64)
        entry={'integrity':'preserved','placement_introduced_errors':0,'status':'checked',
               'engine_feasible':True,'edge_intent':{'status':'pass'},'engine_violations':[],'mechanics':result}
        self.assertFalse(job._placement_eligible(entry))
        with self.assertRaisesRegex(job.JobRefusal,'no original matching binding'):
            job.check_mechanics(path,{'mechanics':self.recipe})
        with patch.object(job,'_native_read',return_value={'status':'not_declared'}):
            with self.assertRaises(job.JobRefusal):job.check_mechanics(path,normalized)

    def test_saved_pose_mismatch_and_inconsistent_native_verdict_refuse(self):
        binding=self.mechanics.bind(self.recipe,self.facts)
        stale=copy.deepcopy(self.description)
        stale['footprints'][0]['position_nm'][0]+=1
        with self.assertRaisesRegex(ValueError,'description pose/owner'):
            self.mechanics.attach(stale,binding,self.facts)
        path=self.root/'saved.kicad_pcb';path.write_bytes(b'late board')
        report={'status':'pass','defects':[{'kind':'source_body_gap'}],
                'binding_sha256':binding['binding_sha256'],'board_sha256':job.digest(path)}
        with patch.object(job,'_native_read',return_value=report), self.assertRaisesRegex(job.JobRefusal,'inconsistent'):
            job.check_mechanics(path,{'mechanics':self.recipe,'mechanics_binding':binding})
