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
        if (self.source / 'parts.json').is_file():
            # The scaffold's native membership check over the captured parts manifest (AGENTEE-86).
            checks += [dict(id='parts-netlist',kind='netlist'),dict(id='parts',kind='parts_subset',params=dict(
                manifest='parts.json',netlist_check='parts-netlist',requirement_id='R-parts'))]
            requirements.append(dict(id='R-parts',statement='Every populated native instance matches parts.json.',
                intent='acceptance',source=dict(path='requirements.md',locator='line 1'),
                checks=['parts-netlist','parts']))
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

    def parts_capture(self, change=None):
        """An owned captured agent-ee.parts/1 closure whose J1 carries the contracted identity.

        The catalog, symbols and footprints are the parts-subset test fixture; J1 is a
        synthetic named part record (the approved-parts fixture) under the contracted
        identity, assigned an owned footprint under the contracted library name, so the
        closure passes the authoritative qualify_manifest and every byte is original test
        data, not the vendor record or footprint.
        """
        name = 'AMPSEAL_35_1-776163-1'
        member = f'PodConnector.pretty/{name}.kicad_mod'
        if not hasattr(self, 'parts_pristine'):
            from agent_ee.parts_manifest import qualify_manifest
            from agent_ee.storage import identity
            from tests.test_approved_parts import named_fixture
            from tests.test_native_binding import GEN
            from tests.test_parts_subset import captured_fixture
            document = captured_fixture(self, self.source, trio=True)
            index = next(i for i, p in enumerate(document['parts']) if p['instances'] == [dict(sheet='/', reference='J1')])
            # A synthetic captured record carrying the contracted identity (LCSC/MPN/manufacturer as
            # facts only); the canonical replay, not the declared strings, establishes J1.
            named_fixture(self.source, document, index, lcsc='C596198', mpn='1-776163-1',
                          package='Push-Pull,P=4mm', manufacturer='TE Connectivity')
            self.member_bytes = GEN.footprint(name, [GEN.tht(n, 4 * i) for i, n in enumerate('123')], 'through_hole')
            (self.source / 'PodConnector.pretty').mkdir()
            (self.source / member).write_bytes(self.member_bytes)
            cad = read_json(self.source / 'cad.json')
            cad['libraries'].append(dict(name='PodConnector', members=[dict(path=member,
                                                                            sha256=digest(self.source / member))]))
            write_json(self.source / 'cad.json', cad)
            assignments = read_json(self.source / 'assignments.json')
            next(c for c in assignments['components'] if c['reference'] == document['parts'][index]['id'])[
                'footprint'] = 'PodConnector:' + name
            write_json(self.source / 'assignments.json', assignments)
            blobs = [m for lib in cad['libraries'] for m in lib['members']] + [{k: lib[k] for k in ('path', 'sha256')}
                     for lib in assignments['libraries']]
            document['cad'].update(manifest=dict(path='cad.json', sha256=digest(self.source / 'cad.json')),
                                   assignments=dict(path='assignments.json',
                                                    sha256=digest(self.source / 'assignments.json')),
                                   members_sha256=identity(sorted(blobs, key=lambda m: m['path'])))
            document['parts'][index]['footprint'] = 'PodConnector:' + name
            write_json(self.source / 'parts.json', document)
            qualify_manifest(document, self.source)
            self.parts_pristine = document
        (self.source / member).write_bytes(self.member_bytes)
        document = copy.deepcopy(self.parts_pristine)
        j1 = next(p for p in document['parts'] if p['instances'] == [dict(sheet='/', reference='J1')])
        if change is not None:
            change(document, j1)
        write_json(self.source / 'parts.json', document)
        return document

    def stage(self):
        """The five generic pod obligations staged under the reviewed source contract (AGENTEE-197).

        Statements come verbatim from the contract; witnesses are concrete envelope
        locators and every open item is text only, so the record introduces no numeric
        limit. The contract fixes the minimum; the coverage lists below equal it.
        """
        from agent_ee.board_envelope import CONTAINMENT, OBLIGATION_CONTRACTS, PART_IDENTITY
        contract = OBLIGATION_CONTRACTS['telemetry-pod-expanded/1']['obligations']
        self.envelope = copy.deepcopy(self.pristine)
        self.envelope['fixed_footprints'] = [
            dict(reference='J1', side='front', edge='bottom', position_mm=40, inset_mm=8, rotation_deg=0,
                 insertion='from_top', tolerance_mm=0, courtyard_overhangs=[dict(edge='bottom', max_mm=20)]),
            dict(reference='U1', side='front', edge='top', position_mm=60, inset_mm=6, rotation_deg=0,
                 insertion='from_top', tolerance_mm=0, courtyard_overhangs=[dict(edge='top', max_mm=18)])]
        generation = {
            'corner-holes': [dict(locator=f'/mounting_holes/{i}') for i in range(4)],
            'height': [],
            'harness-edge': [dict(locator='/fixed_footprints/0'),
                             dict(locator='/fixed_footprints/0', criterion=CONTAINMENT),
                             dict(locator='/fixed_footprints/0', criterion=PART_IDENTITY)],
            'antenna': [dict(locator='/fixed_footprints/1'),
                        dict(locator='/fixed_footprints/1', criterion=CONTAINMENT), dict(locator='/keepouts/0')],
            'imu-separation': []}
        coverage = {oid: dict(generation=generation[oid], open=[
            dict(id=i, stage=stage, statement=f'{i} evidence is not yet captured.')
            for i, stage in item['open'].items()]) for oid, item in contract.items()}
        self.envelope['obligations'] = [dict(id=oid, **{k: copy.deepcopy(item[k]) for k in
                                        ('requirement_ids', 'statement', 'owner_items')}, coverage=coverage[oid])
                                        for oid, item in contract.items()]
        if not (self.source / 'parts.json').is_file():
            self.parts_capture()
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
                         [['fixed-j1'], ['containment'], ['parts']])
        self.assertEqual(staged['antenna']['witnesses'][2]['checks'], ['keepout-antenna'])
        # Admission is not qualification: every staged obligation and open item is named.
        with self.assertRaises(ValueError) as raised:
            require_qualified(report)
        for oid, item in coverage.items():
            self.assertIn(f'obligation-{oid} (' + ', '.join(e['id'] for e in item['open']) + ')',
                          str(raised.exception))
        files = source_inventory(self.source)[0]
        self.assertEqual(require_obligations(self.scope(), requirements, record, files, self.source), report)

    def refused(self, message, scope=None):
        with self.assertRaisesRegex(ValueError, message):
            self.capture(scope)
        self.assertFalse((self.root / f'run-{self.count}' / 'baseline.json').exists())

    def test_source_contract_floor_refuses_caller_coverage(self):
        """Review H1: the reviewed source contract, not the coverage list, fixes the generation minimum."""
        def by_id(oid):
            return next(o for o in self.envelope['obligations'] if o['id'] == oid)
        def drop_open(oid, item):
            by_id(oid)['coverage']['open'] = [e for e in by_id(oid)['coverage']['open'] if e['id'] != item]
        omits = 'coverage omits source contract telemetry-pod-expanded/1 generation witnesses: '
        holes = ("obligation corner-holes source contract binds exactly the source mounting-hole witnesses "
                 "\\['H1', 'H2', 'H3', 'H4'\\]")
        def extra_hole(at):
            self.envelope['mounting_holes'].append(dict(id='H5', at=at, drill_mm=3.2, plated=False, tolerance_mm=.1))
            by_id('corner-holes')['coverage']['generation'].append(dict(locator='/mounting_holes/4'))
        cases = {
            'H4 witness only': (lambda: by_id('corner-holes')['coverage']['generation'].pop(),
                                'obligation corner-holes ' + omits + '/mounting_holes/3\\Z'),
            'empty corner witnesses': (lambda: by_id('corner-holes')['coverage'].update(generation=[]),
                                       'obligation corner-holes ' + omits + '/mounting_holes/0'),
            'every witness list empty': (lambda: [o['coverage'].update(generation=[])
                                                  for o in self.envelope['obligations']],
                                         'obligation corner-holes ' + omits),
            'antenna keepout witness only': (lambda: by_id('antenna')['coverage']['generation'].pop(),
                                             'obligation antenna ' + omits + '/keepouts/0'),
            'identity witness only': (lambda: by_id('harness-edge')['coverage']['generation'].pop(),
                                      'obligation harness-edge ' + omits + '/fixed_footprints/0 \\(part-identity/1\\)'),
            'containment witness only': (lambda: by_id('antenna')['coverage']['generation'].pop(1),
                                         'obligation antenna ' + omits + '/fixed_footprints/1 \\(outline-containment/1\\)'),
            'cowl-fit open item': (lambda: drop_open('height', 'cowl-fit'), "dropped \\['cowl-fit'\\]"),
            'connector-fit open item': (lambda: drop_open('height', 'connector-fit'), "dropped \\['connector-fit'\\]"),
            'feed-point open item': (lambda: drop_open('antenna', 'feed-point-side'), "dropped \\['feed-point-side'\\]"),
            'imu assessment item': (lambda: drop_open('imu-separation', 'placement-assessment'),
                                    "dropped \\['placement-assessment'\\]"),
            'physical item restaged': (lambda: by_id('height')['coverage']['open'][1].update(stage='candidate_assessment'),
                                       "restaged \\['cowl-fit'\\]"),
            'H4 declaration and witness': (lambda: (self.envelope['mounting_holes'].pop(),
                                                    by_id('corner-holes')['coverage']['generation'].pop()),
                                           holes + "; missing \\['H4'\\]; another hole role"),
            'every hole and witness': (lambda: (self.envelope['mounting_holes'].clear(),
                                                by_id('corner-holes')['coverage'].update(generation=[])),
                                       holes + "; missing \\['H1', 'H2', 'H3', 'H4'\\]"),
            'two holes at one corner': (lambda: self.envelope['mounting_holes'][3].update(at=[8, 8]),
                                        "witnesses .* each uniquely nearest a distinct outline corner; nearest "
                                        "corners are \\{'H1': 0, 'H2': 1, 'H3': 2, 'H4': 0\\}"),
            # Combined review H2: the source witnesses are H1-H4 themselves, not a count of holes.
            'H4 renamed H5': (lambda: self.envelope['mounting_holes'][3].update(id='H5'),
                              holes + "; missing \\['H4'\\]; undeclared \\['H5'\\]"),
            'extra fifth hole with witness': (lambda: extra_hole([60, 4]), holes + "; undeclared \\['H5'\\]"),
            'coordinate-duplicate extra hole': (lambda: extra_hole([4, 4]), holes + "; undeclared \\['H5'\\]"),
            'tied extra hole': (lambda: extra_hole([40, 4]), holes + "; undeclared \\['H5'\\]"),
            'H4 renamed H1': (lambda: self.envelope['mounting_holes'][3].update(id='H1'),
                              'duplicate reference or identifier: H1'),
            'H1 witness in place of H4': (lambda: by_id('corner-holes')['coverage']['generation'][3].update(
                locator='/mounting_holes/0'), 'repeats witness /mounting_holes/0'),
            'keepout declaration and witness': (lambda: (self.envelope['keepouts'].clear(),
                                                         by_id('antenna')['coverage']['generation'].pop()),
                                                'requires the captured antenna keepout of U1'),
            'J1 declaration': (lambda: (self.envelope['fixed_footprints'].pop(0),
                                        [w.update(locator='/fixed_footprints/0')
                                         for w in by_id('antenna')['coverage']['generation'][:2]],
                                        by_id('harness-edge')['coverage'].update(generation=[])),
                               'obligation harness-edge source contract requires fixed footprint J1'),
            'obligation dropped': (lambda: self.envelope['obligations'].pop(1),
                                   'obligation height is missing; it cannot be dropped'),
            'statement altered': (lambda: by_id('height').update(statement='Component height is at most 12 mm.'),
                                  "obligation height differs from reviewed source contract .* \\['statement'\\]"),
            'requirement IDs altered': (lambda: by_id('antenna').update(requirement_ids=['POD-AC-7']),
                                        "differs from reviewed source contract .* \\['requirement_ids'\\]"),
            'uncontracted obligation': (lambda: self.envelope['obligations'].append(dict(
                id='extra', requirement_ids=['POD-X'], owner_items=['AGENTEE-177'], statement='Owned extra.',
                coverage=dict(generation=[], open=[dict(id='x', stage='physical_qualification', statement='x')]))),
                                        "staged obligations outside source contract .*\\['extra'\\]")}
        for mode, (change, message) in cases.items():
            with self.subTest(mode=mode):
                self.stage()
                change()
                with self.assertRaisesRegex(ValueError, message):
                    self.record()
                self.refused(message)
        # Only contracted obligations stage: the same coverage outside a contract refuses.
        self.stage()
        for item in self.envelope['obligations']:
            item['id'] = 'owned-' + item['id']
        self.refused('staged obligations match no reviewed source contract')
        # A coverage list may add witnesses and open items, and order is free.
        self.stage()
        by_id('height')['coverage']['open'].append(dict(id='owned-extra', stage='physical_qualification',
                                                       statement='Owned extra qualification item.'))
        self.envelope['obligations'].reverse()
        # The H1-H4 binding is by identity, not by declaration order.
        self.envelope['mounting_holes'].reverse()
        run, _ = self.capture()
        report = read_json(run / 'baseline.json')['envelope']['coverage']
        height = next(r for r in report if r['id'].endswith('obligation-height'))
        self.assertEqual((height['status'], height['open'][-1]['id']), ('staged', 'owned-extra'))
        corners = next(r for r in report if r['id'].endswith('obligation-corner-holes'))
        self.assertEqual((corners['status'], sorted(w['requirement'] for w in corners['witnesses'])),
                         ('staged', [f'R-envelope-telemetry-pod-hole-H{i}' for i in range(1, 5)]))

    def test_harness_identity_binds_the_contracted_part(self):
        """Review H2: J1 identity comes from the captured parts closure, never from geometry or deferral."""
        self.stage()
        run, _ = self.capture()
        report = read_json(run / 'baseline.json')['envelope']['coverage']
        harness = next(r for r in report if r['id'].endswith('obligation-harness-edge'))
        identity = next(w for w in harness['witnesses'] if w.get('criterion') == 'part-identity/1')
        self.assertEqual((harness['status'], identity['status'], identity['checks']), ('staged', 'bound', ['parts']))
        self.assertNotIn('connector-identity', [e['id'] for e in harness['open']])
        cases = {
            'missing part': (lambda d, j1: j1.update(instances=[dict(sheet='/', reference='J9')]),
                             'declares no root-sheet part for J1'),
            'wrong part': (lambda d, j1: j1.update(mpn='WRONG_PART'),
                           "declares J1 as \\{'mpn': 'WRONG_PART'\\}, not the contracted"),
            'wrong manufacturer': (lambda d, j1: j1.update(manufacturer='Owned Other'),
                                   "declares J1 as \\{'manufacturer': 'Owned Other'\\}"),
            'wrong footprint': (lambda d, j1: j1.update(footprint='Owned:PinHeader_1x03'),
                                "declares J1 as \\{'footprint': 'Owned:PinHeader_1x03'\\}"),
            'optional part': (lambda d, j1: j1.update(required=False), 'J1 is not a required purchased part')}
        for mode, (change, message) in cases.items():
            with self.subTest(mode=mode):
                self.stage()
                self.parts_capture(change)
                self.refused('obligation-harness-edge .*J1 part identity unestablished: parts: parts.json ' + message)
        with self.subTest(mode='missing capture'):
            # No parts_subset check: a geometry-only scope, as in the review's WRONG_PART probe.
            self.stage()
            self.parts_capture()
            scope = self.scope()
            scope['checks'] = [c for c in scope['checks'] if c['id'] not in ('parts', 'parts-netlist')]
            scope['baseline']['requirements'] = [r for r in scope['baseline']['requirements'] if r['id'] != 'R-parts']
            self.refused('obligation-harness-edge .*J1 needs a parts_subset check over the captured parts manifest', scope)
        with self.subTest(mode='uncaptured footprint member'):
            self.stage()
            self.parts_capture()
            scope = self.scope()
            member = 'PodConnector.pretty/AMPSEAL_35_1-776163-1.kicad_mod'
            scope['inputs'] = [i for i in scope['inputs'] if i['at'] != member]
            self.refused('J1 part identity unestablished: parts: footprint PodConnector:AMPSEAL_35_1-776163-1 '
                         'is not a captured member of cad.json', scope)
        with self.subTest(mode='footprint member hash'):
            # The contracted footprint name over substituted bytes is a different capture.
            self.stage()
            self.parts_capture()
            path = self.source / 'PodConnector.pretty/AMPSEAL_35_1-776163-1.kicad_mod'
            path.write_bytes(path.read_bytes().replace(b'Owned AMPSEAL', b'Owned substitute'))
            self.refused('hash mismatch|not a captured member')
        with self.subTest(mode='swapped canonical record'):
            # Combined review H1: the contracted strings stay, but the selection, LCSC and datasheet
            # move to another valid captured record; only the authoritative replay sees it.
            from tests.test_approved_parts import named_fixture
            def swap(document, j1):
                other = dict(parts=[copy.deepcopy(j1)])
                named_fixture(self.source, other, 0)
                j1.update({k: other['parts'][0][k] for k in ('lcsc', 'selection', 'datasheet')})
            self.stage()
            self.parts_capture(swap)
            self.refused('J1 part identity unestablished: parts: named identity/stock differs from captured part '
                         'record: header')
        with self.subTest(mode='self-consistent footprint substitution'):
            # Pad 1 becomes pad 101 under the contracted name, with every digest re-sealed.
            from agent_ee.storage import identity
            cad_path = self.source / 'cad.json'
            pristine = cad_path.read_bytes()
            def resealed(document, j1):
                path = self.source / 'PodConnector.pretty/AMPSEAL_35_1-776163-1.kicad_mod'
                path.write_bytes(path.read_bytes().replace(b'(pad "1" ', b'(pad "101" ', 1))
                cad = read_json(cad_path)
                member = next(m for lib in cad['libraries'] for m in lib['members'] if m['path'] == path.relative_to(
                    self.source).as_posix())
                member['sha256'] = digest(path)
                write_json(cad_path, cad)
                blobs = [m for lib in cad['libraries'] for m in lib['members']] + [
                    {k: lib[k] for k in ('path', 'sha256')} for lib in read_json(self.source / 'assignments.json')['libraries']]
                document['cad'].update(manifest=dict(path='cad.json', sha256=digest(cad_path)),
                                       members_sha256=identity(sorted(blobs, key=lambda m: m['path'])))
            self.stage()
            try:
                self.parts_capture(resealed)
                self.refused('J1 part identity unestablished: parts: captured CAD assignments unavailable or '
                             'inconsistent')
            finally:
                cad_path.write_bytes(pristine)
        with self.subTest(mode='real control'):
            self.stage()
            self.parts_capture()
            run, _ = self.capture()
            report = read_json(run / 'baseline.json')['envelope']['coverage']
            self.assertEqual(next(r for r in report if r['id'].endswith('obligation-harness-edge'))['status'], 'staged')

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


    def staged_blocking(self, coverage):
        prefix = 'R-envelope-telemetry-pod-obligation-'
        return [dict(id=prefix + oid, status='staged', open=[e['id'] for e in item['open']])
                for oid, item in coverage.items()]

    def review_profile(self):
        definition = dict(harness='codex', model='test', effort='high', timeout_s=10)
        return dict(schema='agent-ee.profile/1', name='final-guard-test', adjudicator=definition,
                    reviewers=[dict(definition, role='circuit'), dict(definition, role='physical')])

    def test_final_guard_holds_the_review_outcome(self):
        """cli: the captured staged baseline yields incomplete with the open IDs, never a defect or a pass."""
        import io
        from agent_ee import cli
        coverage = self.stage()
        self.build()
        write_json(self.root / 'scope.json', self.scope())
        out = self.root / 'review'
        cli.run_review(self.root / 'scope.json', out, 600, checks_only=True, output_stream=io.StringIO())
        result = read_json(out / 'result.json')
        baseline = read_json(out / 'baseline.json')
        self.assertEqual(result['final_qualification'], dict(schema='agent-ee.final-qualification/1',
                         baseline_id=baseline['baseline_id'], blocking=self.staged_blocking(coverage)))
        held = [t for t in result['limitations'] if t.startswith('Final qualification open')]
        self.assertEqual(len(held), 1)
        for oid, item in coverage.items():
            self.assertIn(f'obligation-{oid} staged (' + ', '.join(e['id'] for e in item['open']) + ')', held[0])
        self.assertEqual(cli.verify(out)['final_qualification'], result['final_qualification'])
        # Only a would-be pass changes: checks and reviews alone no longer satisfy the criteria.
        satisfied = dict(state='complete', checks=[dict(id='c', execution='ok', outcome='pass')],
                         reviews=[dict(role='circuit'), dict(role='physical')], adjudicated=True,
                         review_executions=[dict(role='circuit', execution='ok')], findings=[])
        self.assertEqual(cli.outcome(satisfied), 'criteria_satisfied')
        guarded = dict(satisfied, final_qualification=result['final_qualification'])
        self.assertEqual(cli.outcome(guarded), 'incomplete')
        self.assertEqual(cli.outcome(dict(guarded, checks=[dict(id='c', execution='ok', outcome='defect')])),
                         'defects_or_unresolved')
        self.assertEqual(cli.outcome(dict(satisfied, final_qualification=dict(result['final_qualification'],
                                                                             blocking=[]))), 'criteria_satisfied')
        # A removed, emptied or stale guard record in a retained result is refused on verify.
        original = (out / 'result.json').read_bytes()
        for mode in ('removed', 'emptied', 'stale'):
            with self.subTest(mode=mode):
                forged = json.loads(original)
                if mode == 'removed':
                    del forged['final_qualification']
                if mode == 'emptied':
                    forged['final_qualification']['blocking'] = []
                if mode == 'stale':
                    forged['final_qualification']['baseline_id'] = '0' * 64
                write_json(out / 'result.json', forged)
                with self.assertRaisesRegex(ValueError, 'stored final qualification differs'):
                    cli.verify(out)
        (out / 'result.json').write_bytes(original)
        cli.verify(out)
        # The guard reads the immutable baseline, not the run's mutable envelope bytes.
        envelope = out / 'inputs/envelope.json'
        changed = json.loads(envelope.read_bytes())
        for item in changed['obligations']:
            del item['coverage']
        envelope.write_bytes(json.dumps(changed).encode())
        with self.assertRaises(ValueError):
            cli.verify(out)

    def test_final_guard_holds_campaign_acceptance_and_replay(self):
        """campaign: an acceptance computed from checks and reviews alone cannot publish or replay as done."""
        import contextlib
        import io
        from unittest import mock
        from agent_ee import campaign
        coverage = self.stage()
        self.build()
        scope = self.scope()
        profile = self.review_profile()
        scope.update(review_profile=profile, mutable_inputs=['board.kicad_pcb', 'board.kicad_sch'])
        write_json(self.root / 'scope.json', scope)
        write_json(self.root / 'campaign.json', dict(schema='agent-ee.campaign/1', scope='scope.json',
                   review_profile=profile, implementation=profile['adjudicator'], max_iterations=1,
                   max_seconds=600))
        advance = campaign.advance
        # Simulates the pre-guard acceptance rule: done from checks and reviewer judgements alone.
        accepted = lambda *a, **k: (advance(*a, **k)[0], {'state': 'criteria_satisfied'})
        message = 'final acceptance refused: captured envelope qualification open: R-envelope-telemetry-pod-'
        root = self.root / 'campaign'
        with mock.patch.object(campaign, 'advance', side_effect=accepted), \
                contextlib.redirect_stdout(io.StringIO()):
            status = campaign.run_campaign(self.root / 'campaign.json', root)
            state = read_json(root / 'state.json')
            self.assertEqual((status, state['state']), (1, 'incomplete'))
            self.assertIn(message, state['error'])
            for oid, item in coverage.items():
                self.assertIn(f'obligation-{oid} staged (' + ', '.join(e['id'] for e in item['open']) + ')',
                              state['error'])
            self.assertNotIn('fab_artifacts', state)
            self.assertNotIn('stage_done', state)
            campaign.verify_campaign(root)
            # Replay re-applies the guard: a manually accepted state is refused, not trusted.
            forged = {k: v for k, v in state.items() if k != 'error'}
            write_json(root / 'state.json', dict(forged, state='criteria_satisfied'))
            with self.assertRaisesRegex(ValueError, message):
                campaign.verify_campaign(root)

    def fab_scope(self):
        """The owned fab-export board as a generation scope over the captured envelope declaration."""
        for name in ('fab.kicad_pcb', 'fab.kicad_sch'):
            shutil.copyfile(ROOT / 'fixtures/fab-export' / name, self.source / name)
        scope = self.scope()
        scope.update(board='fab.kicad_pcb', schematic='fab.kicad_sch', generation=dict(
            schema='agent-ee.generation/1', done_check='package'), inputs=[
            dict(path=str(p), at=p.relative_to(self.source).as_posix())
            for p in sorted(self.source.rglob('*')) if p.is_file()])
        scope['checks'] += [dict(id='netlist', kind='netlist'), dict(id='geometry', kind='geometry'),
                            dict(id='package', kind='fab_package', params=dict(netlist_check='netlist'))]
        scope['baseline']['requirements'] += [
            dict(id='R-package', statement='The captured board yields a complete fab package.', intent='acceptance',
                 source=dict(path='targets.md', locator='line 1'), checks=['package']),
            dict(id='I-native', statement='Retain native board and schematic observations.',
                 intent='informational', source=dict(path='targets.md', locator='line 1'),
                 checks=['netlist', 'geometry'])]
        return scope

    def test_final_guard_holds_the_fab_done_gate_and_release(self):
        """fab_package: exports stay as diagnostics; the done gate and sealed release wait for qualification."""
        from agent_ee.fab_package import artifact_record
        results = {}
        for mode in ('qualified', 'staged'):
            with self.subTest(mode=mode):
                self.envelope = copy.deepcopy(self.pristine)
                coverage = self.stage() if mode == 'staged' else None
                run, _ = self.capture(self.fab_scope())
                results[mode] = check = run_checks(run, time.monotonic() + 300)[-1]
                self.assertEqual(check['id'], 'package')
                self.assertTrue((run / 'work/package/fab/gerbers.zip').stat().st_size > 0)
                if mode == 'qualified':
                    self.assertEqual((check['execution'], check['outcome']), ('ok', 'pass'), check['summary'])
                    self.assertEqual(artifact_record(run, check), check['details']['artifacts'])
                    continue
                self.assertEqual((check['execution'], check['outcome']), ('unsupported', 'unknown'))
                self.assertIn('fab package done gate held: final acceptance refused', check['summary'])
                for oid, item in coverage.items():
                    self.assertIn(f'obligation-{oid} staged (' + ', '.join(e['id'] for e in item['open']) + ')',
                                  check['summary'])
                self.assertNotIn('artifacts', check['details'])
                # A result claiming the gate passed still cannot be sealed for release.
                passed = dict(results['qualified'], candidate_id=check['candidate_id'])
                with self.assertRaisesRegex(ValueError, 'captured envelope qualification open'):
                    artifact_record(run, passed)

    def test_final_guard_refuses_retained_metadata_tampering(self):
        """Combined review H3: a removed or edited envelope declaration is refused, never read as no envelope.

        A genuine native clean-board witness (prepare_release) accompanies a would-be passing
        package result, so only the envelope guard can hold the sealed release.
        """
        from agent_ee.board_envelope import require_final
        from agent_ee.checks import Ctx
        from agent_ee.fab_package import artifact_record
        from agent_ee.placement_defects import prepare_release
        from agent_ee.storage import identity
        self.stage()
        run, _ = self.capture(self.fab_scope())
        check = run_checks(run, time.monotonic() + 300)[-1]
        self.assertEqual((check['id'], check['execution'], check['outcome']), ('package', 'unsupported', 'unknown'))
        manifest = read_json(run / 'candidate.json')
        ctx = Ctx(run, manifest, next(c for c in manifest['scope']['checks'] if c['id'] == check['id']),
                  time.monotonic() + 300)
        prepare_release(ctx)
        passed = dict(check, execution='ok', outcome='pass',
                      evidence=sorted(set(check['evidence'] + ctx.result['evidence'])))
        with self.assertRaisesRegex(ValueError, 'captured envelope qualification open: .*obligation-corner-holes '
                                                'staged'):
            artifact_record(run, passed)
        path = run / 'candidate.json'
        original = path.read_bytes()
        def removed(candidate):
            candidate['scope'].pop('envelope')
        def removed_reidentified(candidate):
            removed(candidate)
            candidate['candidate_id'] = identity({'scope': candidate['scope'], 'files': candidate['files']})
        def changed_reidentified(candidate):
            candidate['scope']['envelope']['profile'] = 'targets.md'
            candidate['candidate_id'] = identity({'scope': candidate['scope'], 'files': candidate['files']})
        cases = {'removed declaration': (removed, 'candidate metadata differs from its captured identity'),
                 # Recompiling the captured scope refuses the envelope it still consumes.
                 'removed declaration, re-identified': (removed_reidentified,
                                                        'consumed envelope .*without a scope envelope declaration'),
                 'changed declaration, re-identified': (changed_reidentified, 'immutable baseline changed|fab profile')}
        for mode, (change, message) in cases.items():
            with self.subTest(mode=mode):
                candidate = read_json(path)
                change(candidate)
                write_json(path, candidate)
                try:
                    with self.assertRaisesRegex(ValueError, message):
                        require_final(run)
                    with self.assertRaisesRegex(ValueError, message):
                        artifact_record(run, passed)
                finally:
                    path.write_bytes(original)
        # The captured baseline still stages every obligation.
        self.assertTrue(read_json(run / 'baseline.json')['envelope']['coverage'])
        with self.assertRaisesRegex(ValueError, 'captured envelope qualification open'):
            require_final(run)


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

    def test_contracted_source_envelope_keeps_every_obligation_before_staging(self):
        """A source envelope named by the contract must carry its obligations verbatim, staged or not."""
        from agent_ee.board_envelope import OBLIGATION_CONTRACTS
        contract = OBLIGATION_CONTRACTS['telemetry-pod-expanded/1']
        raw = read_json(ROOT / 'fixtures/telemetry-pod/envelope.json')
        raw['id'] = contract['envelope_ids'][0]
        raw['obligations'] = [dict(id=oid, **{k: item[k] for k in ('requirement_ids', 'statement', 'owner_items')})
                              for oid, item in contract['obligations'].items()]
        record = validate(json.dumps(raw).encode())
        self.assertNotIn('obligation_contract', record)
        self.assertEqual([u['predicate'] for u in record['unresolved'] if u['predicate'].startswith('mechanical_')],
                         ['mechanical_' + oid for oid in contract['obligations']])
        raw['obligations'].pop(1)
        with self.assertRaisesRegex(ValueError, 'obligation height is missing; it cannot be dropped'):
            validate(json.dumps(raw).encode())
        # Another envelope id without staging is outside the contract: unchanged, unresolved.
        raw['id'] = 'owned-other'
        self.assertNotIn('obligation_contract', validate(json.dumps(raw).encode()))

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
