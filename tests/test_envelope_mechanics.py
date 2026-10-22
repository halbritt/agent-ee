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
if mode in ('tracks', 'board-area-tracks'): zone.SetDoNotAllowTracks(False)
if mode in ('layers', 'board-area-layers'):
    layers = pcbnew.LSET()
    layers.AddLayer(pcbnew.F_Cu)
    zone.SetLayerSet(layers)
if mode == 'no-zone': fp.Remove(zone)
if side == 'B': fp.Flip(fp.GetPosition(), False)
fp.SetOrientationDegrees(angle)
fp.SetPosition(pcbnew.VECTOR2I(40000000,25000000 if angle % 180 else (30000000 if angle == 0 else 20000000)))
if mode == 'edge': fp.SetPosition(pcbnew.VECTOR2I(40000000,12750000))
if mode in ('board-area', 'board-area-tracks', 'board-area-layers'):
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
        self.pristine = copy.deepcopy(self.envelope)
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
        if record['fixed_footprints']:
            checks += [dict(id='drc',kind='drc'),dict(id='containment',kind='placement',
                params=dict(criterion='outline-containment/1',drc_check='drc',envelope='envelope.json'))]
        # Generic obligations are never mapped to a check; staged coverage carries them.
        requirements = [r for r in requirements if not r['source']['locator'].startswith('/obligations/')]
        for r in requirements:
            field = r['source']['locator'].split('/')[1]
            cid = r['id'].removeprefix('R-envelope-telemetry-pod-').lower()
            if field == 'fab_profile':
                cid = 'fab'
            elif field == 'fixed_footprints':
                reference = record[field][int(r['source']['locator'].split('/')[-1])]['reference']
                checks.append(dict(id=cid,kind='placement',params=dict(criterion='connector-edge/1',
                    drc_check='drc',envelope='envelope.json',reference=reference)))
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
        if record['fixed_footprints']:
            requirements.append(dict(id='R-drc',statement='DRC passes; fixed courtyards stay inside their '
                'declared overhangs.',intent='acceptance',source=dict(path='targets.md',locator='line 1'),
                checks=['drc','containment']))
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
        # Explicit generator geometry tests native coverage independently of the
        # dynamic antenna declaration's unresolved manufacturer qualification.
        self.envelope['keepouts'][0].pop('antenna')
        self.envelope['keepouts'][0]['polygon'] = [[33.25,49],[33.25,1],[12.25,1],[12.25,49]]
        self.build('board-area')
        control = self.results()['keepout-antenna']
        self.assertEqual((control['execution'],control['outcome']),('ok','pass'),control)
        areas = read_json(self.last_run / 'work/keepout-antenna/envelope-probe.json')['rule_areas']
        self.assertEqual(len(areas),1)
        original = areas[0]
        self.assertEqual(original['owner'],'board')
        self.assertTrue(original['forbid']['tracks'])
        self.assertTrue(set(self.envelope['keepouts'][0]['layers']) <= set(original['layers']))
        for mode in ('board-area-tracks','board-area-layers'):
            with self.subTest(mode=mode):
                self.build(mode)
                result = self.results()['keepout-antenna']
                self.assertEqual((result['execution'],result['outcome']),('ok','defect'),result)
                self.assertEqual(result['details']['unknown'],[])
                self.assertEqual(result['details']['defects'],[
                    'no saved rule area covers the declared polygon, layers and forbid set'])
                areas = read_json(self.last_run / 'work/keepout-antenna/envelope-probe.json')['rule_areas']
                self.assertEqual(len(areas),1)  # Missing zone cannot satisfy this negative.
                area = areas[0]
                self.assertEqual(area['owner'],'board')
                self.assertEqual(area['outline'],original['outline'])
                if mode == 'board-area-tracks':
                    self.assertEqual(area['layers'],original['layers'])
                    self.assertEqual(area['forbid'],dict(original['forbid'],tracks=False))
                else:
                    self.assertEqual(area['layers'],['F.Cu'])
                    self.assertEqual(area['forbid'],original['forbid'])

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

    def stage(self):
        """The five generic pod obligations with explicit stage coverage (AGENTEE-197, agent-509 decision).

        Owned test text: witnesses are concrete envelope locators and every open
        item is text only, so the record introduces no numeric limit.
        """
        self.envelope = copy.deepcopy(self.pristine)
        self.envelope['fixed_footprints'] = [
            dict(reference='J1', side='front', edge='bottom', position_mm=40, inset_mm=8, rotation_deg=0,
                 insertion='from_top', tolerance_mm=0, courtyard_overhangs=[dict(edge='bottom', max_mm=20)]),
            dict(reference='U1', side='front', edge='top', position_mm=60, inset_mm=6, rotation_deg=0,
                 insertion='from_top', tolerance_mm=0, courtyard_overhangs=[dict(edge='top', max_mm=18)])]
        opened = lambda stage, *ids: [dict(id=i, stage=stage, statement=f'{i} evidence is not yet captured.')
                                      for i in ids]
        physical, candidate = 'physical_qualification', 'candidate_assessment'
        coverage = {
            'corner-holes': dict(generation=[dict(locator=f'/mounting_holes/{i}') for i in range(4)],
                                 open=opened(physical, 'hole-authority', 'fabricated-holes')),
            'height': dict(generation=[], open=opened(candidate, 'candidate-heights') +
                           opened(physical, 'cowl-fit', 'underside-fit', 'mating-plug-fit', 'harness-fit')),
            'harness-edge': dict(generation=[dict(locator='/fixed_footprints/0'),
                                             dict(locator='/fixed_footprints/0', criterion='outline-containment/1')],
                                 open=opened(candidate, 'connector-identity') + opened(
                                     physical, 'panel-cutout', 'mating-plug', 'retention', 'thickness-tolerance',
                                     'harness-clearance')),
            'antenna': dict(generation=[dict(locator='/fixed_footprints/1'),
                                        dict(locator='/fixed_footprints/1', criterion='outline-containment/1'),
                                        dict(locator='/keepouts/0')],
                            open=opened(physical, 'housing-pocket', 'nearby-metal', 'support', 'rf-performance')),
            'imu-separation': dict(generation=[], open=opened(candidate, 'placement-assessment') +
                                   opened(physical, 'vibration-coupling'))}
        self.envelope['obligations'] = [dict(item, coverage=coverage[item['id']]) for item in
                                        read_json(ROOT / 'fixtures/telemetry-pod/envelope.json')['obligations']]
        return coverage

    def test_staged_obligations_admit_generation_and_keep_qualification_open(self):
        from agent_ee.board_envelope import require_obligations, require_qualified
        coverage = self.stage()
        record = self.record()
        self.assertEqual((record['outcome'], record['unresolved']), ('unknown', []))
        self.assertEqual(sorted((u['obligation'], u['stage']) for u in record['qualification_open']),
                         sorted((o, e['stage']) for o, c in coverage.items() for e in c['open']))
        requirements = derive_requirements(record, 'envelope.json')
        self.assertTrue(requirements['complete'])
        self.assertEqual(requirements['qualification_open'], record['qualification_open'])
        self.assertFalse(any(r['source']['locator'] == '/max_component_height_mm'
                             for r in requirements['requirements']))
        run, _ = self.capture()
        report = read_json(run / 'baseline.json')['envelope']['coverage']
        staged = {r['id'].removeprefix('R-envelope-telemetry-pod-obligation-'): r
                  for r in report if r['status'] == 'staged'}
        self.assertEqual(set(staged), set(coverage))
        self.assertEqual({r['status'] for r in report if r['id'].split('-obligation-')[0] == r['id']}, {'bound'})
        for oid, entry in staged.items():
            self.assertEqual(entry['open'], coverage[oid]['open'])
            self.assertEqual(entry['checks'], [])
            self.assertEqual([(w['locator'], w.get('criterion'), w['status']) for w in entry['witnesses']],
                             [(w['locator'], w.get('criterion'), 'bound') for w in coverage[oid]['generation']])
        self.assertEqual([w['requirement'] for w in staged['corner-holes']['witnesses']],
                         [f'R-envelope-telemetry-pod-hole-H{i}' for i in range(1, 5)])
        self.assertEqual([w['checks'] for w in staged['harness-edge']['witnesses']],
                         [['fixed-j1'], ['containment']])
        self.assertEqual(staged['antenna']['witnesses'][2]['checks'], ['keepout-antenna'])
        # Admission is not qualification: every staged obligation and open item is named.
        with self.assertRaises(ValueError) as raised:
            require_qualified(report)
        for oid, item in coverage.items():
            self.assertIn(f'obligation-{oid} (' + ', '.join(e['id'] for e in item['open']) + ')',
                          str(raised.exception))
        files = source_inventory(self.source)[0]
        self.assertEqual(require_obligations(self.scope(), requirements, record, files), report)

    def test_removing_a_witness_refuses_generation(self):
        for mode in ('hole-absent', 'hole-unmapped', 'containment-absent', 'keepout-soft', 'coverage-absent'):
            with self.subTest(mode=mode):
                self.stage()
                if mode == 'hole-absent':
                    self.envelope['mounting_holes'].pop()
                    with self.assertRaisesRegex(ValueError, 'witness /mounting_holes/3 is not declared'):
                        self.record()
                    continue
                if mode == 'keepout-soft':
                    # A soft keepout is informational, not acceptance, so it cannot witness.
                    self.envelope['constraint_provenance'] = {'/keepouts/0': {
                        'class': 'assumed', 'rationale': 'Owned test assumption awaiting owner confirmation.'}}
                scope = self.scope()
                if mode == 'hole-unmapped':
                    scope['baseline']['requirements'] = [r for r in scope['baseline']['requirements']
                                                         if not r['id'].endswith('hole-H4')]
                    scope['checks'] = [c for c in scope['checks'] if c['id'] != 'hole-h4']
                if mode == 'containment-absent':
                    scope['checks'] = [c for c in scope['checks'] if c['id'] != 'containment']
                    next(r for r in scope['baseline']['requirements'] if r['id'] == 'R-drc')['checks'] = ['drc']
                if mode == 'coverage-absent':
                    del self.envelope['obligations'][0]['coverage']
                    scope = self.scope()
                expected = {'hole-unmapped': 'obligation-corner-holes .*/mounting_holes/3 is unmapped',
                            'containment-absent': 'obligation-harness-edge .*outline-containment/1 check.*'
                                                  'obligation-antenna',
                            'keepout-soft': 'obligation-antenna .*/keepouts/0 is soft',
                            'coverage-absent': 'refused, not dropped: R-envelope-telemetry-pod-obligation-corner-holes \\(unmapped'}[mode]
                with self.assertRaisesRegex(ValueError, expected):
                    self.capture(scope)
                self.assertFalse((self.root / f'run-{self.count}' / 'baseline.json').exists())

    def test_final_qualification_cannot_be_claimed(self):
        from agent_ee.board_envelope import require_qualified
        self.stage()
        # Soft provenance on the generic obligation itself still leaves it staged and open.
        self.envelope['constraint_provenance'] = {'/obligations/1': {
            'class': 'assumed', 'rationale': 'Owned test assumption awaiting owner confirmation.'}}
        run, _ = self.capture()
        report = read_json(run / 'baseline.json')['envelope']['coverage']
        height = next(r for r in report if r['id'].endswith('obligation-height'))
        self.assertEqual(height['status'], 'staged')
        with self.assertRaisesRegex(ValueError, 'obligation-height \\(candidate-heights'):
            require_qualified(report)
        for mode in ('no-open', 'status', 'mapped', 'statement'):
            with self.subTest(mode=mode):
                self.stage()
                height = self.envelope['obligations'][1]
                if mode == 'no-open':
                    height['coverage']['open'] = []
                if mode == 'status':
                    height['coverage']['open'][0]['status'] = 'qualified'
                if mode in ('no-open', 'status'):
                    with self.assertRaisesRegex(ValueError, 'open'):
                        self.record()
                    continue
                scope = self.scope()
                record = self.record()
                derived = next(r for r in derive_requirements(record, 'envelope.json')['requirements']
                               if r['id'].endswith('obligation-height'))
                claim = {k: derived[k] for k in ('id', 'statement', 'source', 'intent')} | dict(checks=['drc'])
                if mode == 'statement':
                    claim['statement'] = 'Height qualified.'
                scope['baseline']['requirements'].append(claim)
                with self.assertRaisesRegex(ValueError, 'obligation-height is staged'):
                    self.capture(scope)

    def test_staged_coverage_carries_no_numeric_limit(self):
        for mode, change, message in (
                ('cap field', lambda c: c.update(max_component_height_mm=12), 'unknown'),
                ('height witness', lambda c: c['generation'].append(dict(locator='/max_component_height_mm')),
                 'mounting hole, keepout or fixed footprint'),
                ('height criterion', lambda c: c['generation'].append(
                    dict(locator='/fixed_footprints/0', criterion='component-height/1')), 'courtyard_overhangs'),
                ('numeric item', lambda c: c['open'][0].update(max_mm=4), 'unknown'),
                ('numeric statement', lambda c: c['open'][0].update(statement=4), 'statement required'),
                ('stage', lambda c: c['open'][0].update(stage='qualified'), 'stage must be one of'),
                ('duplicate witness', lambda c: c['generation'].append(dict(locator='/fixed_footprints/0')),
                 'repeats witness')):
            with self.subTest(mode=mode):
                self.stage()
                change(self.envelope['obligations'][2]['coverage'])
                with self.assertRaisesRegex(ValueError, message):
                    self.record()


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
