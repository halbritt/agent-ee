"""Coupled group tuning survives the public job and native build producer."""
import copy
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from build_stage_fixture import requirements as retained_requirements

from agent_ee import placement_job as job
from agent_ee.storage import digest, read_json, write_json
from tests.test_placement_job_rules import public_job
from tests.toolchain import require_kicad_route, require_toolchain

SPEC = dict(groups=[['A_P','A_N','B_P','B_N']], tolerance_mm=.1, amplitude_mm=8)


def prepare(root, checkout, *, heterogeneous=False):
    shutil.copytree(Path(checkout)/'fixtures/inter_pair_matching',root)
    board=root/'pairs.kicad_pcb'
    if heterogeneous:
        project=read_json(board.with_suffix('.kicad_pro'))
        other={**project['net_settings']['classes'][1], 'name':'WidePair',
               'diff_pair_width':.3, 'diff_pair_gap':.3, 'track_width':.3}
        project['net_settings']['classes'].append(other)
        for pattern in project['net_settings']['netclass_patterns']:
            if pattern['pattern'].startswith('B_'):pattern['netclass']='WidePair'
        write_json(board.with_suffix('.kicad_pro'),project)
        rules=board.with_suffix('.kicad_dru')
        rules.write_text(rules.read_text()+'''
(rule "wide pair width" (condition "A.NetClass == 'WidePair'") (constraint track_width (min 0.3mm) (max 0.3mm)))
(rule "wide pair gap" (condition "A.NetClass == 'WidePair'") (constraint diff_pair_gap (min 0.29mm) (max 0.31mm)))
''')
    write_json(root/'intent.json',dict(fixed_poses=[],edge_connectors=[],proximity=[]))
    write_json(root/'groups.json',dict(groups=[dict(name='pair-a',anchor='J1',refs=['J1','J2']),
                                              dict(name='pair-b',anchor='J3',refs=['J3','J4'])]))
    files=dict(board=board,project=board.with_suffix('.kicad_pro'),rules=board.with_suffix('.kicad_dru'),
               intent=root/'intent.json',groups=root/'groups.json')
    config=dict(schema=job.CONFIG_SCHEMA,budget_s=240,
        inputs={name:dict(path=str(path),sha256=digest(path)) for name,path in files.items()},
        search=dict(seeds=[dict(seed=0,start='scattered')],steps_per_seed=10,shortlist=1),
        router=dict(checkout=str(checkout),grid_step_mm=.025,pair_layers=['F.Cu'],
                    length_match=copy.deepcopy(SPEC)))
    write_json(root/'job.json',config)
    return config


class InterPairJobTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)
        cls.checkout=require_kicad_route()
        cls.temp=tempfile.TemporaryDirectory(prefix='ee-inter-pair-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root=Path(os.environ.get('AGENT_EE_INTER_PAIR_EVIDENCE',cls.temp.name))
        cls.root.mkdir(parents=True,exist_ok=True)
        cls.config=prepare(cls.root/'inputs',cls.checkout)
        cls.done=public_job(cls.root/'inputs/job.json',cls.root/'positive')
        cls.report=read_json(cls.root/'positive/job-report.json')

    def assert_matched(self,report):
        self.assertEqual(report['status'],'completed',report)
        final=report['final']
        self.assertEqual(final['opens'],0)
        self.assertEqual(final['identity_problems'],[])
        measured=final['length_matching']
        self.assertEqual(measured['status'],'PASS',measured)
        self.assertEqual(measured['board_sha256'],digest(final['board']))
        self.assertEqual(measured['requirement'],dict(spacing_widths=2,**SPEC))
        group,=measured['groups']
        self.assertEqual(len(group['measurements']),4)
        self.assertEqual(group['skew_violations'],[])

    def test_public_job_checks_all_coupled_paths(self):
        self.assertEqual(self.done.returncode,0,self.done.stdout+self.done.stderr)
        self.assert_matched(self.report)
        self.assertTrue(all(digest(e['path'])==e['sha256'] for e in self.config['inputs'].values()))

    def test_connected_unmatched_pairs_reject(self):
        config=copy.deepcopy(self.config);config['router']['length_match']['amplitude_mm']=.001
        path,out=self.root/'insufficient.json',self.root/'insufficient'
        write_json(path,config);done=public_job(path,out)
        self.assertNotEqual(done.returncode,0)
        report=read_json(out/'job-report.json')
        self.assertEqual(report['status'],'verification_failed',report)
        self.assertEqual(report['final']['opens'],0)
        self.assertEqual(report['final']['length_matching']['status'],'FAIL')
        self.assertFalse((out/'routed-incumbent.json').exists())

    def test_authored_coupling_requirement_cannot_be_waived(self):
        config=prepare(self.root/'strict-inputs',self.checkout)
        path=Path(config['inputs']['rules']['path']);path.write_text(path.read_text().replace('(max 3mm)','(max 1mm)'))
        config['inputs']['rules']['sha256']=digest(path)
        request,out=self.root/'strict.json',self.root/'strict'
        write_json(request,config);done=public_job(request,out)
        self.assertNotEqual(done.returncode,0)
        report=read_json(out/'job-report.json')
        self.assertEqual(report['status'],'verification_failed',report)
        self.assertEqual(report['final']['opens'],0)
        self.assertEqual(report['final']['length_matching']['status'],'PASS')
        native=read_json(Path(report['final']['board']).parent/'native-drc.json')
        self.assertIn('diff_pair_uncoupled_length_too_long',{v['type'] for v in native['violations']})
        self.assertFalse((out/'routed-incumbent.json').exists())

    def test_changed_group_requirement_cannot_resume(self):
        config=copy.deepcopy(self.config);config['router']['length_match']['tolerance_mm']=.05
        path=self.root/'changed.json';write_json(path,config)
        before=digest(self.root/'positive/checkpoint.json')
        done=public_job(path,self.root/'positive','--resume')
        self.assertNotEqual(done.returncode,0)
        self.assertIn('different job identity',done.stdout+done.stderr)
        self.assertEqual(digest(self.root/'positive/checkpoint.json'),before)

    def test_build_producer_preserves_distinct_native_pair_classes(self):
        out=self.root/'build-stage';inputs=out/'inputs'
        config=prepare(inputs,self.checkout,heterogeneous=True)
        attempt = retained_requirements(out, inputs, dict(board='pairs.kicad_pcb',checks=[]), dict(requirements=[]))
        stage = attempt.directory
        plan=dict(placement=dict(budget_s=config['budget_s'],search=config['search'],
            router=config['router'],intent='intent.json',groups='groups.json'))
        candidate=attempt.execute(plan, inputs, diagnostic=True)
        report=read_json(stage/'placement/job-report.json');self.assert_matched(report)
        native,_=job.drc(candidate/'pairs.kicad_pcb',stage,refill=True,label='native-final')
        self.assertEqual(native['violations'],[])
        self.assertEqual(native['unconnected_items'],[])
        for path in inputs.rglob('*'):
            if path.is_file() and (path.suffix in ('.kicad_mod','.kicad_pro','.kicad_dru') or path.name=='fp-lib-table'):
                self.assertEqual(digest(path),digest(candidate/path.relative_to(inputs)))


if __name__=='__main__':
    unittest.main()
