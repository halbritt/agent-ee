"""Per-item envelope witnesses against real, saved KiCad geometry.

Boards and PDFs below are disposable test fixtures. The installed stock module
is loaded at runtime; no vendor design or PDF is distributed in these tests.
"""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

from agent_ee.board_envelope import derive_requirements, source_inventory, validate
from agent_ee.checks import run_checks
from agent_ee.envelope_check import keepout_verdict, parameters
from agent_ee.footprint_library import esp32_s3_wroom_1_datasheet_facts
from agent_ee.storage import digest, read_json, write_json
from tests.test_esp32_footprint_keepout import multipage_text_pdf
from tests.toolchain import require_toolchain

ROOT = Path(__file__).resolve().parents[1]

# All mutations use native APIs before SaveBoard, so saved footprint-owned zones
# follow KiCad's coordinate convention, including when the module moves/rotates.
BUILD = '''
import sys
import pcbnew
board = pcbnew.LoadBoard(sys.argv[1])
mode, angle, side = sys.argv[2], int(sys.argv[3]), sys.argv[4]
for i, (x, y) in enumerate(((4,4),(76,4),(76,46),(4,46)), 1):
    fp = pcbnew.FOOTPRINT(board)
    fp.SetReference('H'+str(i))
    pad = pcbnew.PAD(fp)
    pad.SetAttribute(pcbnew.PAD_ATTRIB_NPTH)
    pad.SetShape(pcbnew.PAD_SHAPE_CIRCLE)
    pad.SetSize(pcbnew.VECTOR2I(3200000,3200000))
    pad.SetDrillSize(pcbnew.VECTOR2I(3200000,3200000))
    pad.SetLayerSet(pcbnew.LSET.AllCuMask())
    fp.Add(pad)
    fp.SetPosition(pcbnew.VECTOR2I(x*1000000,y*1000000))
    board.Add(fp)
    if i == 1:
        if mode == 'moved': fp.Move(pcbnew.VECTOR2I(300000,0))
        if mode == 'boundary': fp.Move(pcbnew.VECTOR2I(100000,0))
        if mode == 'beyond': fp.Move(pcbnew.VECTOR2I(100001,0))
        if mode == 'outside': fp.Move(pcbnew.VECTOR2I(-3000000,0))
        if mode == 'drill': pad.SetDrillSize(pcbnew.VECTOR2I(2700000,2700000))
        if mode == 'plated': pad.SetAttribute(pcbnew.PAD_ATTRIB_PTH)
        if mode == 'offset': pad.SetOffset(pcbnew.VECTOR2I(100000,0))
        if mode == 'pad-moved': pad.SetPosition(pcbnew.VECTOR2I(x*1000000+300000,y*1000000))
        if mode == 'missing': board.Remove(fp)
        if mode == 'duplicate':
            other = pcbnew.FOOTPRINT(board)
            other.SetReference('H1')
            other.SetPosition(fp.GetPosition())
            board.Add(other)
fp = pcbnew.FootprintLoad('/usr/share/kicad/footprints/RF_Module.pretty','ESP32-S3-WROOM-1')
fp.SetFPID(pcbnew.LIB_ID('RF_Module','ESP32-S3-WROOM-1'))
fp.SetReference('U1')
board.Add(fp)
zone = list(fp.Zones())[0]
if mode == 'shrunk':
    for i, (x,y) in enumerate(((-23,-7.75),(23,-7.75),(23,-26.75),(-23,-26.75))):
        zone.Outline().SetVertex(i,pcbnew.VECTOR2I(int(x*1000000),int(y*1000000)))
if mode == 'tracks': zone.SetDoNotAllowTracks(False)
if mode == 'layers':
    layers = pcbnew.LSET()
    layers.AddLayer(pcbnew.F_Cu)
    zone.SetLayerSet(layers)
if mode == 'no-zone': fp.Remove(zone)
if side == 'B': fp.Flip(fp.GetPosition(), False)
fp.SetOrientationDegrees(angle)
fp.SetPosition(pcbnew.VECTOR2I(40000000,25000000 if angle % 180 else (30000000 if angle == 0 else 20000000)))
if mode == 'edge': fp.SetPosition(pcbnew.VECTOR2I(40000000,12750000))
if mode == 'board-area':
    copied = zone.Duplicate(False)
    fp.Remove(zone)
    board.Add(copied)
pcbnew.SaveBoard(sys.argv[1],board)
'''


class EnvelopeMechanicsTests(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True, tools=('pdftotext',))
        self.tmp = tempfile.TemporaryDirectory(prefix='ee162-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source'
        shutil.copytree(ROOT / 'fixtures/telemetry-pod', self.source)
        self.envelope = read_json(self.source / 'envelope.json')
        # This contract fixture supplies generator-selected mechanics; the shipped
        # pod's unresolved height/IMU/placement obligations are tested separately.
        self.envelope['obligations'] = []
        self.envelope['fixed_footprints'] = []
        self.envelope['mounting_holes'] = [dict(id=f'H{i}', at=[x,y], drill_mm=3.2,
            plated=False, tolerance_mm=.1) for i, (x,y) in enumerate(((4,4),(76,4),(76,46),(4,46)),1)]
        self.envelope['keepouts'] = [dict(id='antenna', layers=['F.Cu','In1.Cu','In2.Cu','B.Cu'],
            forbid=['footprints','tracks','vias','pads','copper_pours'],
            antenna=dict(reference='U1', facts_check='facts'))]
        multipage_text_pdf(self.source / 'owned.pdf', 45, 45, ['18','25.5','6','15'])
        self.facts = esp32_s3_wroom_1_datasheet_facts(pdf_path='owned.pdf', pdf_sha256=digest(self.source / 'owned.pdf'))
        write_json(self.source / 'facts.json', self.facts)
        self.version = subprocess.run(['/usr/bin/pdftotext','-v'],capture_output=True,text=True,check=True).stderr.splitlines()[0]
        for name in ('targets.md','reference.md'):
            (self.source / name).write_text('Owned envelope mechanics contract.\n')
        shutil.copyfile(ROOT / 'fixtures/rc/control/rc_filter.kicad_sch', self.source / 'board.kicad_sch')
        shutil.copyfile(ROOT / 'fixtures/rc/control/rc_filter.kicad_pro', self.source / 'board.kicad_pro')
        self.count = 0
        self.build()

    def save(self):
        write_json(self.source / 'envelope.json', self.envelope)

    def record(self):
        self.save()
        return validate((self.source / 'envelope.json').read_bytes(),
            profile=(self.source / 'fab-profile.json').read_bytes(), captured=source_inventory(self.source))

    def build(self, mode='control', angle=90, side='F'):
        from agent_ee.scaffold import board
        path = self.source / 'board.kicad_pcb'
        path.write_bytes(board(self.record(), 'owned-ee162'))
        built = subprocess.run(['/usr/bin/python3','-B','-c',BUILD,str(path),mode,str(angle),side],
                               capture_output=True,text=True,timeout=30)
        self.assertEqual(built.returncode,0,built.stdout+built.stderr)

    def scope(self):
        record = self.record()
        requirements = derive_requirements(record, 'envelope.json')['requirements']
        checks = [dict(id='facts', kind='datasheet_facts', params=dict(facts='facts.json', extractor=dict(
            path='/usr/bin/pdftotext', sha256=digest('/usr/bin/pdftotext'), version=self.version))),
            dict(id='fab',kind='fab_profile',params=dict(fab_profile='fab-profile.json'))]
        for r in requirements:
            field = r['source']['locator'].split('/')[1]
            cid = r['id'].removeprefix('R-envelope-telemetry-pod-').lower()
            if field == 'fab_profile':
                cid = 'fab'
            else:
                criterion = {'outline':'outline/1', 'layer_count':'layers/1', 'stackup':'stackup/1',
                             'mounting_holes':'mounting-holes/1','keepouts':'keepouts/1'}[field]
                params = dict(criterion=criterion,envelope='envelope.json')
                if field in ('mounting_holes','keepouts'):
                    params['id'] = record[field][int(r['source']['locator'].split('/')[-1])]['id']
                checks.append(dict(id=cid,kind='board_envelope',params=params))
            r['checks'] = [cid]
            del r['field_sha256'], r['check_kinds']
        requirements.append(dict(id='R-facts',statement='Verify the cited antenna dimensions.',intent='informational',
            source=dict(path='targets.md',locator='line 1'),checks=['facts']))
        return dict(schema='agent-ee.scope/3',name='owned-ee162',scope='All instantiated envelope geometry',
            inputs=[dict(path=str(p),at=p.relative_to(self.source).as_posix())
                    for p in sorted(self.source.rglob('*')) if p.is_file()],schematic='board.kicad_sch',board='board.kicad_pcb',
            envelope=dict(path='envelope.json',profile='fab-profile.json',dialog=None),checks=checks,
            baseline=dict(requirements=requirements,targets=['targets.md'],reference_architectures=['reference.md']))

    def capture(self, scope=None):
        from agent_ee.candidate import capture
        self.count += 1
        run = self.root / f'run-{self.count}'
        run.mkdir()
        write_json(self.root / 'scope.json',scope or self.scope())
        manifest = capture(self.root / 'scope.json',run)
        return run,manifest

    def results(self):
        run, manifest = self.capture()
        original = (run / 'inputs/board.kicad_pcb').read_bytes()
        results = {r['id']: r for r in run_checks(run,time.monotonic()+120)}
        self.assertEqual((run / 'inputs/board.kicad_pcb').read_bytes(),original)
        self.last_run, self.last_results = run, results
        return results

    def test_holes_bound(self):
        results = self.results()
        for i in range(1,5):
            result = results[f'hole-h{i}']
            self.assertEqual((result['execution'],result['outcome']),('ok','pass'),result)
            self.assertEqual(result['details']['id'],f'H{i}')
        self.build('boundary')
        self.assertEqual(self.results()['hole-h1']['outcome'],'pass')
        self.build('offset')
        self.assertEqual(self.results()['hole-h1']['outcome'],'pass')
        for mode in ('moved','beyond','outside','drill','plated','pad-moved','missing','duplicate'):
            with self.subTest(mode=mode):
                self.build(mode)
                result = self.results()['hole-h1']
                self.assertEqual((result['execution'],result['outcome']),('ok','defect'),result)
                self.assertEqual(result['details']['id'],'H1')
                if mode == 'outside':
                    self.assertIn('drilled circle is not inside the native outline',result['details']['defects'])
                if mode == 'pad-moved':
                    self.assertIn('drilled pad centre exceeds declared position tolerance',result['details']['defects'])
                self.assertEqual(self.last_results['hole-h2']['outcome'],'pass')
        self.envelope['mounting_holes'][0]['plated'] = True
        self.build('plated')
        self.assertEqual(self.results()['hole-h1']['outcome'],'pass')

    def test_antenna_source_hold_survives_native_pose_and_zone_changes(self):
        for mode, angle, side in [('control', 0, 'F'), ('control', 90, 'F'),
                                  ('control', 0, 'B'), ('edge', 0, 'F'),
                                  ('shrunk', 0, 'F'), ('no-zone', 0, 'F')]:
            with self.subTest(mode=mode, angle=angle, side=side):
                self.build(mode, angle=angle, side=side)
                result = self.results()['keepout-antenna']
                self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)
                self.assertIn('placement qualification unresolved', result['details']['unknown'][0])
                self.assertNotIn('declared_polygon_nm', result['details']['saved'])

    def test_explicit_polygon_fidelity_is_separate_from_antenna_qualification(self):
        self.build('board-area')
        self.assertEqual(self.results()['keepout-antenna']['outcome'], 'unknown')
        self.envelope['keepouts'][0].pop('antenna')
        self.envelope['keepouts'][0]['polygon'] = [[33.25,49],[33.25,1],[12.25,1],[12.25,49]]
        self.assertEqual(self.results()['keepout-antenna']['outcome'], 'pass')

    def test_duplicate_native_pad_uuid_cannot_overwrite_drill_evidence(self):
        from agent_ee.symbol_library import children, parse_sexpr
        path = self.source / 'board.kicad_pcb'
        raw = path.read_bytes()
        board = parse_sexpr(raw)
        ids = [children(pad,'uuid')[0][1] for fp in children(board,'footprint')
               for pad in children(fp,'pad')]
        self.assertEqual(len(set(ids)),len(ids))
        footprint_ids = [children(fp,'uuid')[0][1] for fp in children(board,'footprint')]
        for kind, identifiers in (('pad',ids),('footprint',footprint_ids)):
            with self.subTest(kind=kind):
                path.write_bytes(raw.replace(identifiers[0].encode(),identifiers[1].encode()))
                results = self.results()
                for cid in ('hole-h1','keepout-antenna'):
                    self.assertEqual((results[cid]['execution'],results[cid]['outcome']),('failed','unknown'),results[cid])

    def test_native_tool_failure_cannot_pass_mechanical_checks(self):
        from agent_ee.checks import Ctx, CheckError, check_board_envelope
        path = self.source / 'board.kicad_pcb'
        raw = path.read_bytes()
        path.write_bytes(raw.replace(b'(version 20260206)',b'(version 99999999)'))
        self.assertNotEqual(path.read_bytes(),raw)
        run, manifest = self.capture()
        for cid in ('hole-h1','keepout-antenna'):
            spec = next(c for c in manifest['scope']['checks'] if c['id'] == cid)
            ctx = Ctx(run,manifest,spec,time.monotonic()+60)
            with self.subTest(cid=cid),self.assertRaises(CheckError) as raised:
                check_board_envelope(ctx)
            self.assertEqual(raised.exception.execution,'failed')
            self.assertEqual(ctx.result['outcome'],'unknown')

    def test_keepout_forbid_subset(self):
        self.build('tracks')
        result = self.results()['keepout-antenna']
        self.assertEqual((result['execution'],result['outcome']),('ok','defect'),result)
        self.build('layers')
        self.assertEqual(self.results()['keepout-antenna']['outcome'],'defect')

    def test_full_scope_capture(self):
        results = self.results()
        baseline = read_json(self.last_run / 'baseline.json')
        coverage = baseline['envelope']['coverage']
        self.assertEqual(len(coverage),9)
        self.assertEqual({r['status'] for r in coverage},{'bound'})
        for cid in ('outline','layer-count','stackup','hole-h1','hole-h2','hole-h3','hole-h4'):
            self.assertEqual((results[cid]['execution'],results[cid]['outcome']),('ok','pass'),results[cid])
        antenna = results['keepout-antenna']
        self.assertEqual((antenna['execution'], antenna['outcome']), ('unsupported', 'unknown'), antenna)
        self.assertIn('placement qualification unresolved', antenna['details']['unknown'][0])
        self.assertIn('module dimensions do not define a base-board minimum', antenna['details']['unknown'][0])
        self.assertNotIn('declared_polygon_nm', antenna['details']['saved'])
        descriptor = baseline['coverage']['checks']['keepout-antenna']
        self.assertEqual(descriptor['predicates'],['envelope:keepouts/1:antenna'])
        self.assertEqual(descriptor['prerequisites'],[dict(check_id='facts',kind='datasheet_facts')])

    def test_fact_failures_remain_missing_coverage(self):
        for mutation in ('missing_pdf','wrong_page','wrong_part','missing_dimension'):
            with self.subTest(mutation=mutation):
                doc = copy.deepcopy(self.facts)
                if mutation == 'missing_pdf':
                    doc['facts'][0]['source']['path'] = 'absent.pdf'
                    # A valid captured path with mismatched bytes exercises runtime coverage.
                    (self.source / 'absent.pdf').write_bytes(b'missing PDF')
                if mutation == 'wrong_page': doc['facts'][0]['source']['page'] = 44
                if mutation == 'wrong_part': doc['facts'][0]['part_number'] = 'OTHER'
                if mutation == 'missing_dimension': doc['facts'].pop()
                write_json(self.source / 'facts.json',doc)
                result = self.results()['keepout-antenna']
                self.assertEqual(result['outcome'],'unknown',result)
                self.assertEqual(result['execution'],'unsupported',result)

    def test_capture_refuses_wrong_selectors_shared_witness_and_forward_facts(self):
        for mode in ('selector','shared','forward'):
            scope = self.scope()
            if mode == 'selector':
                next(c for c in scope['checks'] if c['id']=='hole-h1')['params']['id']='H2'
            if mode == 'shared':
                next(r for r in scope['baseline']['requirements'] if r['id'].endswith('hole-H2'))['checks']=['hole-h1']
            if mode == 'forward': scope['checks'].append(scope['checks'].pop(0))
            with self.subTest(mode=mode),self.assertRaises(ValueError): self.capture(scope)

    def test_unresolved_pod_generator_obligations_remain_named_refusals(self):
        scope = self.scope()
        self.envelope['obligations'] = read_json(ROOT / 'fixtures/telemetry-pod/envelope.json')['obligations']
        record = self.record()
        self.assertEqual({r['id'] for r in record['obligations']},
                         {'corner-holes','height','harness-edge','antenna','imu-separation'})
        from agent_ee.board_envelope import require_obligations
        requirements = derive_requirements(record,'envelope.json')
        for r in requirements['requirements']:
            if r['source']['locator'].startswith('/obligations/'):
                scope['baseline']['requirements'].append({k:v for k,v in r.items()
                    if k not in ('check_kinds','field_sha256')} | dict(checks=[]))
        with self.assertRaises(ValueError) as raised:
            require_obligations(scope,requirements,record,source_inventory(self.source)[0])
        for item in record['obligations']:
            self.assertIn('R-envelope-telemetry-pod-obligation-'+item['id'],str(raised.exception))


class EnvelopeMechanicsGeometryTests(unittest.TestCase):
    def test_board_area_and_concave_coverage_use_edges_not_only_vertices(self):
        area = dict(uuid='board-area',owner='board',layers=['F.Cu'],placement_area=False,
                    forbid=dict(tracks=True),outline=dict(status='proven',polygons=[
                        [[0,0],[10,0],[10,10],[6,10],[6,4],[4,4],[4,10],[0,10]]]))
        native = dict(rule_areas=[area],outline=dict(status='proven',polygons=[[[0,0],[10,0],[10,10],[0,10]]]))
        keepout = dict(id='K',polygon_mm=[['0.000001','0.000001'],['0.000009','0.000001'],
            ['0.000009','0.000003'],['0.000001','0.000003']],layers=['F.Cu'],forbid=['tracks'])
        self.assertEqual(keepout_verdict(keepout,native)['status'],'pass')
        area['placement_area']=True
        self.assertEqual(keepout_verdict(keepout,native)['status'],'unknown')
        area['placement_area']=False
        keepout['polygon_mm'][2][1] = keepout['polygon_mm'][3][1] = '0.000009'
        self.assertEqual(keepout_verdict(keepout,native)['status'],'defect')
        area['outline']['status']='unsupported'
        self.assertEqual(keepout_verdict(keepout,native)['status'],'unknown')

    def test_parameters_and_tolerance_fail_closed(self):
        for params in (dict(criterion='mounting-holes/1',envelope='e.json'),
            dict(criterion='keepouts/1',envelope='e.json',id='../K'),
            dict(criterion='layers/1',envelope='e.json',id='H1')):
            with self.subTest(params=params),self.assertRaises(ValueError): parameters(params)
        raw = read_json(ROOT / 'fixtures/telemetry-pod/envelope.json')
        raw['mounting_holes'] = [dict(id='H1',at=[4,4],drill_mm=3.2,plated=False,tolerance_mm=-.1)]
        with self.assertRaisesRegex(ValueError,'tolerance_mm'):
            validate(json.dumps(raw).encode())


if __name__ == '__main__':
    unittest.main()
