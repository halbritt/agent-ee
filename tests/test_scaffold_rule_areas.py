"""Captured CAD retention is generation custody, never antenna qualification."""
import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_ee import footprint_library as fl
from agent_ee.storage import write_json


CAD = b'''(footprint "ESP32-S3-WROOM-1" (version 20241229) (generator "pcbnew")
(layer "F.Cu") (attr smd)
(pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu" "F.Paste" "F.Mask"))
(zone (layers "F.Cu" "In1.Cu" "In2.Cu" "B.Cu")
(uuid "18bbecf2-2977-41e2-b949-929efe1bdbf7") (hatch full 0.5)
(keepout (tracks not_allowed) (vias not_allowed) (pads not_allowed)
(copperpour not_allowed) (footprints not_allowed))
(polygon (pts (xy -24 -6.75) (xy 24 -6.75) (xy 24 -27.75) (xy -24 -27.75)))))'''


def setup_record(root):
    """Owned synthetic source bytes; patch only the trusted source pin in tests."""
    source = 'cad/ESP32-S3-WROOM-1.kicad_mod'
    (root / 'cad').mkdir(exist_ok=True)
    (root / source).write_bytes(CAD)
    (root / 'guideline.pdf').write_bytes(b'%PDF-1.4\nOwned synthetic policy source.\n')
    member = dict(path=source, sha256=hashlib.sha256(CAD).hexdigest())
    cad = dict(schema='agent-ee.footprint-libraries/1', complete=True,
               libraries=[dict(name='RF_Module', members=[member])])
    write_json(root / 'cad.json', cad)
    pose = dict(anchor_mm=['62.5', '6.5'], rotation_deg='0', side='front')
    fixed = dict(reference='U1', **pose)
    area = dict(id='radio', antenna=dict(reference='U1', facts_check='facts'),
        layers=['F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu'],
        forbid=['tracks', 'vias', 'pads', 'copper_pours', 'footprints'])
    envelope = dict(fixed_footprints=[fixed], keepouts=[area], copper_layers=area['layers'][:])
    document = dict(parts=[dict(footprint=fl.ESP32_FOOTPRINT_ID,
                               instances=[dict(reference='U1', sheet='/')])],
                    cad=dict(manifest=dict(path='cad.json', sha256=sha(root / 'cad.json'))))
    write_json(root / 'envelope.json', envelope)
    write_json(root / 'parts.json', document)
    policy = fl.esp32_s3_wroom_1_placement_policy('outside_board', pdf_path='guideline.pdf')
    context = fl.antenna_placement_context(policy, {'guideline.pdf': (root / 'guideline.pdf').read_bytes()})
    entry = dict(keepout_id='radio', reference='U1', footprint=fl.ESP32_FOOTPRINT_ID,
        pose=pose, source=member, rule_areas=fl.probe_rule_areas(CAD, path=source),
        policy=policy, context=context)
    record = dict(schema='agent-ee.captured-rule-areas/1', purpose='retain-captured-cad-only',
        envelope=dict(path='envelope.json', sha256=sha(root / 'envelope.json')),
        parts=dict(path='parts.json', sha256=sha(root / 'parts.json')), items=[entry])
    write_json(root / 'retained.json', record)
    return record, envelope, document


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CapturedRuleAreaContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        policy_bytes = b'%PDF-1.4\nOwned synthetic policy source.\n'
        patcher = patch.object(fl, 'ESP32_GUIDELINES_SHA256', hashlib.sha256(policy_bytes).hexdigest())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.record, self.envelope, self.document = setup_record(self.root)

    def load(self):
        from agent_ee.scaffold_rule_areas import load_retention
        return load_retention(self.root, 'retained.json', 'envelope.json', 'parts.json',
                              self.envelope, self.document)

    def test_retention_binds_geometry_and_keeps_qualification_unknown(self):
        result = self.load()
        self.assertEqual(set(result['items']), {'radio'})
        self.assertEqual(result['items']['radio']['context']['outcome'], 'unknown')
        self.assertIsNone(result['items']['radio']['context']['source_derived_minimum'])
        self.assertEqual(result['items']['radio']['rule_areas']['rule_areas'][0]['polygon_nm'],
            [[-24000000,-6750000],[24000000,-6750000],[24000000,-27750000],[-24000000,-27750000]])
        self.assertEqual(set(result['dependencies']),
                         {'retained.json','envelope.json','parts.json','cad.json',
                          'cad/ESP32-S3-WROOM-1.kicad_mod','guideline.pdf'})
        from agent_ee.envelope_check import keepout_verdict
        self.assertEqual(keepout_verdict(self.envelope['keepouts'][0], {}, [])['status'], 'unknown')

    def test_declared_claims_ambiguity_and_binding_changes_refuse(self):
        mutations = {
            'minimum': lambda r: r.update(source_derived_minimum={'mm':15}),
            'accepted': lambda r: r['items'][0]['context'].update(outcome='pass'),
            'purpose': lambda r: r.update(purpose='qualified-antenna'),
            'duplicate': lambda r: r['items'].append(copy.deepcopy(r['items'][0])),
            'missing': lambda r: r.update(items=[]),
            'identity': lambda r: r['items'][0].update(footprint='Other:ESP32-S3-WROOM-1'),
            'ref': lambda r: r['items'][0].update(reference='U2'),
            'pose': lambda r: r['items'][0]['pose'].update(anchor_mm=['0','0']),
            'polygon': lambda r: r['items'][0]['rule_areas']['rule_areas'][0]['polygon_nm'][0].__setitem__(0,0),
            'case': lambda r: r['items'][0]['policy'].update(placement_case='board_cutout'),
            'source-page': lambda r: r['items'][0]['policy']['source'].update(page=45),
            'source-sha': lambda r: r['items'][0]['source'].update(sha256='0'*64),
            'envelope-sha': lambda r: r['envelope'].update(sha256='0'*64),
            'parts-sha': lambda r: r['parts'].update(sha256='0'*64),
            'extra-item-field': lambda r: r['items'][0].update(minimum_mm=15),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                record = copy.deepcopy(self.record)
                mutate(record)
                write_json(self.root / 'retained.json', record)
                with self.assertRaises(ValueError):
                    self.load()

    def test_changed_source_bytes_missing_member_and_weak_rules_refuse(self):
        for path in ('guideline.pdf','cad/ESP32-S3-WROOM-1.kicad_mod','cad.json'):
            with self.subTest(path=path):
                original = (self.root / path).read_bytes()
                (self.root / path).write_bytes(original+b'\n')
                with self.assertRaises(ValueError):
                    self.load()
                (self.root / path).write_bytes(original)
        for key, value in [('layers',['F.Cu']), ('forbid',['tracks'])]:
            with self.subTest(key=key):
                self.envelope['keepouts'][0][key] = value
                # Rebind the envelope deliberately; changed envelope flags cannot silently
                # authorize different CAD restrictions or leave the scoped source unexamined.
                write_json(self.root / 'envelope.json', self.envelope)
                record = copy.deepcopy(self.record)
                record['envelope']['sha256'] = sha(self.root / 'envelope.json')
                write_json(self.root / 'retained.json', record)
                with self.assertRaises(ValueError):
                    self.load()
                self.envelope = copy.deepcopy(setup_record(self.root)[1])

    def test_content_step_exposes_input_and_retains_only_board_write(self):
        from agent_ee.scaffold import content_step
        step = content_step('.', 'envelope.json', 'parts.json', 'design/board.kicad_pcb',
                            captured_rule_areas='retained.json')
        self.assertEqual(step['inputs'], ['retained.json'])
        self.assertEqual(step['argv'][-2:], ['--captured-rule-areas', 'retained.json'])
        self.assertEqual(step['writes'], {'design/board.kicad_pcb':'design/design/board.kicad_pcb'})
        with self.assertRaises(ValueError):
            content_step('.', 'envelope.json','parts.json','board.kicad_pcb',
                         captured_rule_areas='../retained.json')

    def test_missing_symlink_ambiguous_member_and_zone_refuse(self):
        path = self.root / 'guideline.pdf'
        original = path.read_bytes()
        path.unlink()
        with self.assertRaises(OSError):
            self.load()
        path.write_bytes(original)
        target = self.root / 'other.pdf'
        target.write_bytes(original)
        path.unlink()
        path.symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.load()
        path.unlink()
        path.write_bytes(original)
        for raw in (CAD.replace(b'(zone (layers', b'(zone (layers', 1).split(b'(zone ')[0]+b')',
                    CAD[:-1]+CAD[CAD.index(b'(zone '):-1]+b')'):
            with self.subTest(raw=hashlib.sha256(raw).hexdigest()):
                member = self.record['items'][0]['source']
                (self.root / member['path']).write_bytes(raw)
                member['sha256'] = sha(self.root / member['path'])
                import json
                cad = json.loads((self.root / 'cad.json').read_text())
                cad['libraries'][0]['members'] = [member]
                write_json(self.root / 'cad.json', cad)
                self.document['cad']['manifest']['sha256'] = sha(self.root / 'cad.json')
                write_json(self.root / 'parts.json', self.document)
                self.record['parts']['sha256'] = sha(self.root / 'parts.json')
                self.record['items'][0]['rule_areas'] = fl.probe_rule_areas(raw,path=member['path'])
                write_json(self.root / 'retained.json',self.record)
                with self.assertRaisesRegex(ValueError, 'missing/ambiguous'):
                    self.load()
        self.record,self.envelope,self.document = setup_record(self.root)
        import json
        cad = json.loads((self.root / 'cad.json').read_text())
        extra = dict(path='other/ESP32-S3-WROOM-1.kicad_mod',sha256=hashlib.sha256(CAD).hexdigest())
        cad['libraries'][0]['members'].append(extra)
        write_json(self.root / 'cad.json',cad)
        self.document['cad']['manifest']['sha256'] = sha(self.root / 'cad.json')
        write_json(self.root / 'parts.json',self.document)
        self.record['parts']['sha256'] = sha(self.root / 'parts.json')
        write_json(self.root / 'retained.json',self.record)
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            self.load()

    def test_native_saved_zone_fidelity_and_tamper_refusal(self):
        # No scaffold mounting-hole code is invoked by this isolated native control.
        from tests.toolchain import require_toolchain
        require_toolchain(native=True)
        import pcbnew
        from agent_ee.scaffold_rule_areas import verify_native_retention
        retention = self.load()
        board = pcbnew.BOARD()
        board.SetCopperLayerCount(4)
        fp = pcbnew.FootprintLoad(str(self.root/'cad'), 'ESP32-S3-WROOM-1')
        fp.SetFPID(pcbnew.LIB_ID('RF_Module','ESP32-S3-WROOM-1'))
        fp.SetReference('U1')
        board.Add(fp)
        fp.SetPosition(pcbnew.VECTOR2I(62500000,6500000))
        path = self.root / 'native.kicad_pcb'
        self.assertTrue(pcbnew.SaveBoard(str(path),board))
        saved = pcbnew.LoadBoard(str(path))
        verify_native_retention(saved,retention['items'],self.root)
        from agent_ee.placement_probe import rule_area
        zone = list(next(iter(saved.GetFootprints())).Zones())[0]
        measured = rule_area(zone,'U1')
        self.assertEqual(measured['outline']['polygons'],
                         [[[38500000,-250000],[86500000,-250000],[86500000,-21250000],[38500000,-21250000]]])
        self.assertEqual((self.root/'cad/ESP32-S3-WROOM-1.kicad_mod').read_bytes(), CAD)
        for mutate in (lambda f,z: f.SetPosition(pcbnew.VECTOR2I(62000000,6500000)),
                       lambda f,z: z.SetDoNotAllowPads(False),
                       lambda f,z: z.Move(pcbnew.VECTOR2I(0,1000000)),
                       lambda f,z: z.SetPlacementAreaEnabled(True),
                       lambda f,z: z.SetLayer(pcbnew.F_Cu),
                       lambda f,z: f.Remove(z)):
            changed = pcbnew.LoadBoard(str(path))
            f = next(iter(changed.GetFootprints()))
            z = list(f.Zones())[0]
            mutate(f,z)
            with self.assertRaises(ValueError):
                verify_native_retention(changed,retention['items'],self.root)


# Production writer integration over the existing original mechanics fixture.
# The policy bytes/pin are synthetic; native geometry and refusal behavior are real.
from tests.test_scaffold import EnvelopeScaffoldFixture


class CapturedRuleAreaContentTests(EnvelopeScaffoldFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        from agent_ee.board_envelope import validate
        from agent_ee.storage import read_json, identity
        self.envelope['keepouts'][0] = dict(id='antenna',
            antenna=dict(reference='U2',facts_check='facts'),
            layers=['F.Cu','In1.Cu','In2.Cu','B.Cu'],
            forbid=['tracks','vias','pads','copper_pours','footprints'])
        self.envelope['fixed_footprints'][-1].update(side='front',position_mm=62.5,
            inset_mm=6.5,rotation_deg=0,insertion='from_top')
        source = 'RF_Module.pretty/ESP32-S3-WROOM-1.kicad_mod'
        raw = (self.source/source).read_bytes().rstrip()
        (self.source/source).write_bytes(raw[:-1]+b'\n'+CAD[CAD.index(b'(zone '):-1]+b'\n)')
        cad = read_json(self.source/'cad.json')
        for lib in cad['libraries']:
            for member in lib['members']:
                member['sha256'] = sha(self.source/member['path'])
        write_json(self.source/'cad.json',cad)
        self.parts['cad']['manifest']['sha256'] = sha(self.source/'cad.json')
        members = [m for lib in cad['libraries'] for m in lib['members']]
        self.parts['cad']['members_sha256'] = identity(sorted(
            [*members,dict(path='Owned.kicad_sym',sha256=sha(self.source/'Owned.kicad_sym'))],
            key=lambda m:m['path']))
        write_json(self.source/'parts.json',self.parts)
        self.save()
        native_envelope = validate((self.source/'envelope.json').read_bytes())
        policy_bytes = b'%PDF-1.4\nOwned synthetic policy source.\n'
        (self.source/'guideline.pdf').write_bytes(policy_bytes)
        patcher = patch.object(fl,'ESP32_GUIDELINES_SHA256',hashlib.sha256(policy_bytes).hexdigest())
        patcher.start()
        self.addCleanup(patcher.stop)
        policy = fl.esp32_s3_wroom_1_placement_policy('outside_board',pdf_path='guideline.pdf')
        item = dict(keepout_id='antenna',reference='U2',footprint=fl.ESP32_FOOTPRINT_ID,
            pose={k:native_envelope['fixed_footprints'][-1][k] for k in ('anchor_mm','rotation_deg','side')},
            source=dict(path=source,sha256=sha(self.source/source)),
            rule_areas=fl.probe_rule_areas((self.source/source).read_bytes(),path=source),
            policy=policy,context=fl.antenna_placement_context(policy,{'guideline.pdf':policy_bytes}))
        self.retention = dict(schema='agent-ee.captured-rule-areas/1',purpose='retain-captured-cad-only',
            envelope=dict(path='envelope.json',sha256=sha(self.source/'envelope.json')),
            parts=dict(path='parts.json',sha256=sha(self.source/'parts.json')),items=[item])
        write_json(self.source/'retained.json',self.retention)
        self.save()

    def produce(self, name):
        from agent_ee.scaffold import write_envelope_content
        self.build(name)
        inputs = self.root/name/'inputs'
        write_envelope_content(inputs,'envelope.json','parts.json','project/design.kicad_pcb',
            intent_rel='intent.json',layout_rel='layout.json',captured_rule_areas_rel='retained.json')
        return inputs

    def test_full_native_writer_preserves_outside_zone_and_unknown(self):
        from agent_ee import envelope_probe
        from agent_ee.envelope_check import keepout_verdict
        from agent_ee.board_envelope import validate
        first = self.produce('first')
        second = self.produce('second')
        board = first/'project/design.kicad_pcb'
        self.assertEqual(board.read_bytes(),(second/'project/design.kicad_pcb').read_bytes())
        native = envelope_probe.extract(board)
        self.assertEqual(len(native['footprints']),8)
        fp = next(f for f in native['footprints'] if f['ref']=='U2')
        self.assertEqual(fp['position_nm'],[62500000,6500000])
        zones = [z for z in native['rule_areas'] if z['owner']==fp['uuid']]
        self.assertEqual(len(zones),1)
        self.assertEqual(zones[0]['outline']['polygons'],
            [[[38500000,-250000],[86500000,-250000],[86500000,-21250000],[38500000,-21250000]]])
        self.assertEqual(len([z for z in native['rule_areas'] if z['owner']=='board']),1)
        area = validate((first/'envelope.json').read_bytes())['keepouts'][0]
        verdict = keepout_verdict(area,native,self.facts)
        self.assertEqual(verdict['status'],'unknown')
        self.assertEqual((first/'envelope.json').read_bytes(),(self.source/'envelope.json').read_bytes())
        from agent_ee.forward_annotation import board_facts
        before = board_facts(board,self.root/'native-paths')
        self.assertEqual(len([f for f in before['footprints'] if f['path']]),4)
        from agent_ee.schematic_emitter import emit_schematic
        from agent_ee.executor import execute
        from agent_ee.storage import read_json
        from agent_ee import forward_annotation as fa
        result, files = emit_schematic(self.intent,
            {'Owned.kicad_sym':(first/'Owned.kicad_sym').read_bytes()}, read_json(first/'layout.json'))
        self.assertEqual(result['execution'],'ok')
        (first/'project/design.kicad_sch').write_bytes(files['design.kicad_sch'])
        result = execute(first,self.root/'xml',['kicad-cli','sch','export','netlist','--format','kicadxml',
            '-o','/work/native.xml','/candidate/project/design.kicad_sch'])
        self.assertEqual(result['execution'],'ok',result)
        xml = self.root/'xml/native.xml'
        manifest = read_json(first/'cad.json')
        plan = fa.plan(board,xml,manifest,first,self.root/'annotation-plan')
        self.assertEqual(plan['execution'],'ok',plan)
        self.assertEqual(plan['counts']['add'],0)
        annotated = self.root/'annotated.kicad_pcb'
        applied = fa.apply(plan,board,xml,manifest,first,annotated,self.root/'annotation-apply',
                           backup_dir=self.root/'backups')
        self.assertEqual(applied['execution'],'ok',applied)
        self.assertEqual(envelope_probe.extract(annotated)['rule_areas'],native['rule_areas'])

    def test_failed_retention_never_publishes_a_changed_board(self):
        from agent_ee.scaffold import write_envelope_content
        self.build('refusals')
        inputs = self.root/'refusals/inputs'
        board = inputs/'project/design.kicad_pcb'
        before = board.read_bytes()
        for mutation in ('pose','policy','zone'):
            record = copy.deepcopy(self.retention)
            if mutation=='pose':
                record['items'][0]['pose']['anchor_mm'] = ['0','0']
            elif mutation=='policy':
                record['items'][0]['context']['outcome'] = 'pass'
            else:
                record['items'][0]['rule_areas']['rule_areas'][0]['polygon_nm'][0][0] = 0
            write_json(inputs/'retained.json',record)
            with self.assertRaises(ValueError):
                write_envelope_content(inputs,'envelope.json','parts.json','project/design.kicad_pcb',
                    intent_rel='intent.json',layout_rel='layout.json',captured_rule_areas_rel='retained.json')
            self.assertEqual(board.read_bytes(),before)
        write_json(inputs/'retained.json',self.retention)
        with patch('agent_ee.scaffold_rule_areas.verify_native_retention',
                   side_effect=ValueError('native saved zone changed')):
            with self.assertRaisesRegex(ValueError,'native saved zone changed'):
                write_envelope_content(inputs,'envelope.json','parts.json','project/design.kicad_pcb',
                    intent_rel='intent.json',layout_rel='layout.json',captured_rule_areas_rel='retained.json')
        self.assertEqual(board.read_bytes(),before)
