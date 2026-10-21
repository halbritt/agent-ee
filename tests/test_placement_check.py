"""Mechanical placement criteria with real saved-board probes, DRC and PDF verification."""
import copy
from fractions import Fraction
import subprocess
import time
import unittest

from agent_ee.candidate import capture
from agent_ee.checks import run_checks
from agent_ee.imu_axes import parameters, evaluate, envelope_declaration
from agent_ee.board_envelope import validate
from agent_ee.storage import digest, read_json, write_json
from agent_ee.symbol_library import parse_sexpr
from agent_ee.symbol_library import children
from tests.test_scaffold import EnvelopeScaffoldFixture
from tests.test_esp32_footprint_keepout import multipage_text_pdf


class KeepApartTests(unittest.TestCase):
    def policy(self):
        return dict(schema='agent-ee.placement-policy/1', keep_apart=[
            dict(ref='IMU', others=['SMA'], min_distance_mm=15)])

    def native_apart(self, center_distance_nm=7000000, circle=False, minimum_mm=15):
        from tests.test_placement_gates_native import NativePlacementGates, placement
        helper = NativePlacementGates()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        source = helper.source()
        board = source / 'project/rc_filter.kicad_pcb'
        edit = '''import pcbnew as k,sys
b=k.LoadBoard(sys.argv[1])
for fp in b.GetFootprints():
    if fp.GetReference() not in ('J1','R1'): continue
    fp.SetReference('IMU' if fp.GetReference()=='R1' else 'SMA')
    for s in fp.GraphicalItems():
        if s.GetLayer() in (k.F_CrtYd,k.B_CrtYd): s.SetLayer(k.Dwgs_User)
    fp.SetOrientationDegrees(0)
    fp.SetPosition(k.VECTOR2I(0,0))
    shape=k.PCB_SHAPE(fp)
    shape.SetShape(k.SHAPE_T_CIRCLE if sys.argv[3]=='circle' else k.SHAPE_T_RECT)
    shape.SetLayer(k.F_CrtYd)
    shape.SetStart(k.VECTOR2I(0,0) if sys.argv[3]=='circle' else k.VECTOR2I(-1000000,-1000000))
    shape.SetEnd(k.VECTOR2I(1000000,0) if sys.argv[3]=='circle' else k.VECTOR2I(1000000,1000000))
    shape.SetWidth(50000)
    fp.Add(shape)
    fp.SetPosition(k.VECTOR2I(100000000 if fp.GetReference()=='IMU' else 100000000+int(sys.argv[2]),100000000))
k.SaveBoard(sys.argv[1],b)
'''
        process = subprocess.run(['/usr/bin/python3', '-B', '-c', edit, str(board),
                                  str(center_distance_nm), 'circle' if circle else 'rect'],
                                 capture_output=True, text=True, timeout=60)
        self.assertEqual(process.returncode, 0, process.stderr)
        schematic = source / 'project/rc_filter.kicad_sch'
        schematic.write_text(schematic.read_text().replace('"J1"', '"SMA"').replace('"R1"', '"IMU"'))
        policy = self.policy()
        policy['keep_apart'][0]['min_distance_mm'] = minimum_mm
        return helper.run_scope(source, [dict(id='drc', kind='drc'),
            placement('apart', 'keep-apart/1', drc='drc', policy=policy)])

    def test_keep_apart(self):
        """Actual saved courtyards separated by 5 mm fail the declared 15 mm minimum."""
        run, results = self.native_apart()
        self.assertEqual(results['drc']['execution'], 'ok', results['drc'])
        result = results['apart']
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)
        self.assertEqual(result['details']['measured'][0]['distance_mm'], 5)
        self.assertEqual(result['details']['defects'][0]['refs'], ['IMU', 'SMA'])
        self.assertIn('work/apart/placement-judgment.json', result['evidence'])
        baseline = read_json(run / 'baseline.json')
        self.assertEqual(baseline['coverage']['checks']['apart']['predicates'], ['placement:keep-apart/1'])

    def test_keep_apart_native_circle_bounds(self):
        for distance, status, execution in ((12000000, 'unknown', 'unsupported'),
                                             (12020000, 'pass', 'ok'), (11990000, 'defect', 'ok')):
            with self.subTest(center_distance_nm=distance):
                run, results = self.native_apart(distance, circle=True, minimum_mm=10)
                result = results['apart']
                self.assertEqual(results['drc']['execution'], 'ok', results['drc'])
                self.assertEqual((result['execution'], result['outcome']), (execution, status), result)
                facts = read_json(run / 'work/apart/placement-probe.json')
                selected = [fp for fp in facts['footprints'] if fp['ref'] in ('IMU', 'SMA')]
                self.assertEqual(len(selected), 2)
                for fp in selected:
                    courtyard = fp['courtyards']['F']
                    self.assertEqual(courtyard['status'], 'proven')
                    self.assertIn('inner_polygons', courtyard)
                    self.assertEqual([p['kind'] for p in courtyard['primitives']], ['circle'])
                self.assert_curve_bounds(result['details'], status)
                self.assertIn('work/apart/placement-judgment.json', result['evidence'])
                if status == 'unknown':
                    self.assertNotIn('gate_findings', result['details'])

    def assert_curve_bounds(self, result, status):
        self.assertEqual(result['status'], status, result)
        measured, = result['measured']
        self.assertEqual(measured['status'], status)
        outer = Fraction(**measured['distance_squared_nm'])
        inner = Fraction(**measured['inner_distance_squared_nm'])
        self.assertLess(outer, inner)
        if status == 'pass':
            self.assertGreaterEqual(outer, 10000000**2)
            self.assertEqual(result['defects'], [])
            self.assertEqual(result['unknown'], [])
        elif status == 'defect':
            self.assertLess(inner, 10000000**2)
            self.assertEqual(result['defects'][0]['refs'], ['IMU', 'SMA'])
            self.assertEqual(result['unknown'], [])
        else:
            self.assertLess(outer, 10000000**2)
            self.assertGreater(inner, 10000000**2)
            self.assertEqual(result['defects'], [])
            entry, = result['unknown']
            self.assertEqual(entry['kind'], 'courtyard_approximation')
            self.assertEqual(entry['refs'], ['IMU', 'SMA'])

    def test_keep_apart_curve_bounds(self):
        from agent_ee.placement_predicates import keep_apart
        from agent_ee.placement_probe import prove_contours
        from tests.test_placement_predicates import probe, part
        policy = self.policy()
        policy['keep_apart'][0]['min_distance_mm'] = 10
        for distance, status in ((12000000, 'unknown'), (12020000, 'pass'), (11990000, 'defect')):
            for side in ('F', 'B'):
                with self.subTest(center_distance_nm=distance, side=side):
                    parts = [part('IMU', 0, 0), part('SMA', distance / 1000000, 0, side=side)]
                    for fp, x in zip(parts, (0, distance)):
                        fp['courtyards'][fp['side']] = prove_contours([
                            dict(kind='circle', center=[x, 0], end=[x+1000000, 0])],
                            'courtyard_missing', 'courtyard_unproven', curves_allowed=True)
                    self.assert_curve_bounds(keep_apart(probe(parts), policy), status)

    def test_keep_apart_unproven_curve_is_unknown(self):
        from agent_ee.placement_predicates import keep_apart
        from tests.test_placement_predicates import probe, part
        for record in (dict(status='courtyard_unproven', reason='unsupported primitive'),
                       dict(status='proven', polygons=[[[0, 0], [1, 0], [0, 1]]],
                            inner_polygons=[[[0, 0], [1, 0], [0, 1]]])):
            with self.subTest(record=record):
                parts = [part('IMU', 0, 0), part('SMA', 1, 0)]
                parts[1]['courtyards']['F'] = record
                result = keep_apart(probe(parts), self.policy())
                self.assertEqual(result['status'], 'unknown')
                self.assertEqual(result['defects'], [])
                self.assertEqual(result['unknown'][0]['reference'], 'SMA')

    def test_keep_apart_boundaries_and_unknowns(self):
        from agent_ee.placement_predicates import keep_apart
        from tests.test_placement_predicates import probe, part
        for x, expected in ((22, 'pass'), (21.999999, 'defect'), (6, 'defect'), (5, 'defect')):
            with self.subTest(x=x):
                facts = probe([part('IMU', 5, 5), part('SMA', x, 5, side='B')])
                self.assertEqual(keep_apart(facts, self.policy())['status'], expected)
        for parts in ([part('IMU', 5, 5)], [part('IMU', 5, 5), part('SMA', 22, 5, courtyard=False)]):
            self.assertEqual(keep_apart(probe(parts), self.policy())['status'], 'unknown')
        # Containment has zero separation even when boundaries do not cross.
        contained = probe([part('IMU', 5, 5, w=10, h=10), part('SMA', 5, 5)])
        self.assertEqual(keep_apart(contained, self.policy())['measured'][0]['distance_mm'], 0)

    def test_keep_apart_declared_contract(self):
        from agent_ee.placement_check import parameters
        from agent_ee.placement import keep_apart_rules
        params = dict(criterion='keep-apart/1', drc_check='drc', policy=self.policy())
        self.assertEqual(parameters(params), [])
        for bad in (None, {}, dict(schema='agent-ee.placement-policy/1', keep_apart=[])):
            with self.subTest(policy=bad), self.assertRaises(ValueError):
                parameters({**params, 'policy': bad})
        for rule in (dict(ref='IMU', others=['IMU'], min_distance_mm=15),
                     dict(ref='IMU', others=['SMA', 'SMA'], min_distance_mm=15),
                     dict(ref='IMU', others=['SMA'], min_distance_mm=0),
                     dict(ref='IMU', others=['SMA'], min_distance_mm=True)):
            with self.subTest(rule=rule), self.assertRaises(ValueError):
                keep_apart_rules(dict(schema='agent-ee.placement-policy/1', keep_apart=[rule]))


class IMUAxesTests(EnvelopeScaffoldFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        # This tests an original R2 stand-in, not an approved production IMU.
        multipage_text_pdf(self.source / 'imu.pdf', 1, 1, ['Owned axis frame X Y Z'])
        self.axis_fact = dict(id='imu-frame', part_number=self.parts['parts'][0]['mpn'],
            kind='axis_orientation', value='X', unit='', conditions='Owned R2 stand-in, front pin-1 frame.',
            footprint_id=self.parts['parts'][0]['footprint'], axes={'X':'x-', 'Y':'y+', 'Z':'z+'},
            source=dict(path='imu.pdf', sha256=digest(self.source / 'imu.pdf'), page=1,
                        locator='Original fixture axis declaration', quote='Owned axis frame X Y Z'))
        self.facts['facts'].append(self.axis_fact)
        write_json(self.source / 'facts.json', self.facts)
        self.save()

    def run_imu(self, edit='', overrides=None, mutate_fact=None):
        inputs, receipt_path, receipt = self.content()
        self.assert_completed(receipt_path, receipt)
        board = inputs / 'project/design.kicad_pcb'
        text, = children(parse_sexpr(board.read_bytes()), 'gr_text')
        graphic_id = children(text, 'uuid')[0][1]
        params = dict(criterion='imu-axes/1', drc_check='drc', envelope='envelope.json', imu_ref='U1',
                      graphic_id=graphic_id, forward_axis='x+', side='front', max_distance_mm=4,
                      marker_rotation_deg=0, facts_check='facts', axis_fact='imu-frame')
        params.update(overrides or {})
        if overrides and 'marker_ref' in overrides:
            params.pop('graphic_id')
        if edit:
            script = 'import pcbnew as k,sys\nb=k.LoadBoard(sys.argv[1])\n' + edit + '\nk.SaveBoard(sys.argv[1],b)\n'
            subprocess.run(['/usr/bin/python3', '-B', '-c', script, str(board)], check=True,
                           capture_output=True, text=True, timeout=60)
        if mutate_fact:
            facts = read_json(inputs / 'facts.json')
            mutate_fact(facts['facts'][-1])
            write_json(inputs / 'facts.json', facts)
        scope = self.acceptance_scope(inputs)
        scope['checks'].append(dict(id='imu',kind='placement',params=params))
        scope['baseline']['requirements'].append(dict(id='R-imu', statement='IMU marking and axes align.',
            intent='acceptance', source=dict(path='targets.md',locator='line 1'), checks=['imu']))
        write_json(self.root / 'content/scope.json',scope)
        run = self.root / 'checked'
        run.mkdir()
        capture(self.root / 'content/scope.json',run)
        results = {r['id']:r for r in run_checks(run,time.monotonic()+300)}
        return run, results['imu']

    def assert_status(self, result, execution, outcome):
        self.assertEqual((result['execution'],result['outcome']),(execution,outcome),result)
        if 'selection' in result['details']:
            self.assertEqual(result['details']['selection']['ref'],'U1')
            self.assertTrue(result['details']['selection']['marker'])

    def test_imu_axes_pass(self):
        run, result = self.run_imu()
        self.assert_status(result,'ok','pass')
        self.assertEqual(result['details']['measured']['board_axes'],{'X':'x+','Y':'y-','Z':'z+'})
        self.assertEqual(result['details']['measured']['marker']['layer'],'F.SilkS')
        self.assertEqual(result['details']['axis_fact'],self.axis_fact)
        coverage = read_json(run / 'baseline.json')['coverage']['checks']['imu']
        self.assertEqual(coverage['predicates'],['placement:imu-axes/1:U1'])
        self.assertIn('envelope.json',coverage['immutable_inputs'])
        self.assertIn(dict(check_id='facts',kind='datasheet_facts'),coverage['prerequisites'])
        self.assertIn('work/imu/placement-judgment.json',result['evidence'])
        from agent_ee.capabilities import describe_check
        manifest = read_json(run / 'candidate.json')
        specs = manifest['scope']['checks']
        for label, edit in (
            ('missing facts check', dict(facts_check='absent')),
            ('late facts check', dict(facts_check='imu')),
            ('undeclared reference', dict(imu_ref='U99')),
            ('contradictory side', dict(side='back'))):
            with self.subTest(label=label):
                changed_specs = copy.deepcopy(specs)
                changed_specs[-1]['params'].update(edit)
                capability = describe_check(changed_specs[-1],check_specs=changed_specs,files=manifest['files'],
                    scope=manifest['scope'],inputs=run / 'inputs')
                self.assertEqual(capability['status'],'unsupported',capability)


    def test_imu_rotated_defect(self):
        run, result = self.run_imu("next(f for f in b.GetFootprints() if f.GetReference()=='U1').SetOrientationDegrees(90)")
        self.assert_status(result,'ok','defect')
        self.assertIn('imu_rotation',[d['kind'] for d in result['details']['defects']])
        from agent_ee.baseline import freeze_evidence, validate_witnesses
        results = [read_json(p) for p in sorted((run / 'checks').glob('*.json'))]
        freeze_evidence(run,results)
        witness = [dict(requirement_id='R-imu',check_id='imu')]
        citations = [dict(path='checks/imu.json',locator='details.defects')]
        validate_witnesses(run,witness,citations)
        with self.assertRaises(ValueError):
            validate_witnesses(run,[dict(requirement_id='R-facts',check_id='imu')],citations)
        (run / 'work/imu/placement-probe.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'tool evidence changed'):
            validate_witnesses(run,witness,citations)

    def test_marking_missing_defect(self):
        _, result = self.run_imu("for t in list(b.GetDrawings()):\n if t.GetClass()=='PCB_TEXT': b.Remove(t)")
        self.assert_status(result,'ok','defect')
        self.assertEqual([d['kind'] for d in result['details']['defects']],['marking_missing'])

    def test_marking_unreadable_unknown(self):
        _, result = self.run_imu("next(t for t in b.GetDrawings() if t.GetClass()=='PCB_TEXT').SetTextSize(k.VECTOR2I(100000,100000))")
        self.assert_status(result,'unsupported','unknown')
        self.assertEqual([u['kind'] for u in result['details']['unknown']],['marking_unreadable'])

    def test_custom_font_marking_unknown(self):
        _, result = self.run_imu("next(t for t in b.GetDrawings() if t.GetClass()=='PCB_TEXT').SetFontProp('DejaVu Sans')")
        self.assert_status(result,'unsupported','unknown')
        self.assertEqual([u['kind'] for u in result['details']['unknown']],['marking_unreadable'])

    def test_mirrored_marker_unknown(self):
        _, result = self.run_imu("next(t for t in b.GetDrawings() if t.GetClass()=='PCB_TEXT').SetMirrored(True)")
        self.assert_status(result,'unsupported','unknown')
        self.assertEqual([u['kind'] for u in result['details']['unknown']],['marking_unreadable'])

    def test_marker_footprint_pass(self):
        _, result = self.run_imu("""t=next(t for t in b.GetDrawings() if t.GetClass()=='PCB_TEXT')
f=k.FOOTPRINT(b)
f.SetReference('AXES1')
b.Add(f)
g=k.PCB_TEXT(f)
g.SetText(t.GetText())
g.SetLayer(k.F_SilkS)
g.SetPosition(t.GetPosition())
g.SetTextSize(t.GetTextSize())
g.SetTextThickness(t.GetTextThickness())
f.Add(g)
b.Remove(t)""", overrides=dict(marker_ref='AXES1'))
        self.assert_status(result,'ok','pass')
        self.assertEqual(result['details']['measured']['marker']['owner_ref'],'AXES1')

    def test_wrong_marker_layer_defect(self):
        _, result = self.run_imu("next(t for t in b.GetDrawings() if t.GetClass()=='PCB_TEXT').SetLayer(k.B_SilkS)")
        self.assert_status(result,'ok','defect')
        self.assertEqual([d['kind'] for d in result['details']['defects']],['marking_layer'])

    def test_rotated_marker_defect(self):
        _, result = self.run_imu("next(t for t in b.GetDrawings() if t.GetClass()=='PCB_TEXT').SetTextAngle(k.EDA_ANGLE(90,k.DEGREES_T))")
        self.assert_status(result,'ok','defect')
        self.assertEqual([d['kind'] for d in result['details']['defects']],['marking_rotation'])

    def test_far_marker_defect_one_nm_over_boundary(self):
        _, result = self.run_imu("next(t for t in b.GetDrawings() if t.GetClass()=='PCB_TEXT').SetPosition(k.VECTOR2I(50000000,34000001))")
        self.assert_status(result,'ok','defect')
        self.assertEqual([d['kind'] for d in result['details']['defects']],['marking_distance'])

    def test_wrong_side_defect(self):
        _, result = self.run_imu("f=next(f for f in b.GetFootprints() if f.GetReference()=='U1')\nf.Flip(f.GetPosition(),k.FLIP_DIRECTION_TOP_BOTTOM)")
        self.assert_status(result,'ok','defect')
        self.assertIn('imu_side',[d['kind'] for d in result['details']['defects']])

    def test_inapplicable_fact_unknown(self):
        _, result = self.run_imu(mutate_fact=lambda f:f.update(part_number='another-part'))
        self.assert_status(result,'unsupported','unknown')
        self.assertIn('inapplicable',result['details']['unknown'][0]['reason'])

    def test_unverified_fact_unknown(self):
        _, result = self.run_imu(mutate_fact=lambda f:f['source'].update(quote='not in PDF'))
        self.assert_status(result,'unsupported','unknown')
        self.assertIn('unverified',result['details']['unknown'][0]['reason'])

    def test_marker_ref_and_fail_closed_boundaries(self):
        run, result = self.run_imu()
        self.assert_status(result,'ok','pass')
        probe = read_json(run / 'work/imu/placement-probe.json')
        params = read_json(run / 'candidate.json')['scope']['checks'][-1]['params']
        fixed = envelope_declaration(validate((run / 'inputs/envelope.json').read_bytes()),params)
        declaration = dict(fixed=fixed,fact=self.axis_fact)
        for label, edit, status, kind in (
            ('inventory absent',lambda p:p.pop('markings'),'unknown','marking_inventory_unavailable'),
            ('duplicate marker',lambda p:p['markings'].append(copy.deepcopy(result['details']['measured']['marker'])),
             'unknown','marking_ambiguous'),
            ('unsupported graphic',lambda p:p['markings'].__setitem__(next(i for i,m in enumerate(p['markings']) if m['uuid']==params['graphic_id']),
                dict(uuid=params['graphic_id'],owner_ref=None,native_class='PCB_TEXTBOX',layer='F.SilkS')),
             'unknown','marking_unreadable')):
            with self.subTest(label=label):
                changed=copy.deepcopy(probe)
                edit(changed)
                verdict=evaluate(changed,params,declaration)
                self.assertEqual(verdict['status'],status,verdict)
                self.assertEqual(verdict['unknown'][0]['kind'],kind)
        # Empty native/cited IDs cannot qualify a footprint datum by equality.
        unnamed = copy.deepcopy(probe)
        next(fp for fp in unnamed['footprints'] if fp['ref']=='U1')['fpid']=''
        unnamed_declaration = copy.deepcopy(declaration)
        unnamed_declaration['fact']['footprint_id']=''
        self.assertEqual(evaluate(unnamed,params,unnamed_declaration)['status'],'unknown')
        # The alternate selector is a dedicated footprint with one text graphic.
        marker = next(m for m in probe['markings'] if m['uuid']==params['graphic_id'])
        marker['owner_ref']='AXES1'
        params.pop('graphic_id')
        params['marker_ref']='AXES1'
        parameters(params)
        self.assertEqual(evaluate(probe,params,declaration)['status'],'pass')

    def test_parameters_refuse_ambiguous_and_unqualified_contracts(self):
        params=dict(envelope='e.json',imu_ref='U1',graphic_id='00000000-0000-0000-0000-000000000001',
            forward_axis='x+',side='front',max_distance_mm=4,marker_rotation_deg=0,facts_check='facts',axis_fact='axis')
        parameters(params)
        for changes in (dict(marker_ref='AXES1'),dict(graphic_id='bad'),dict(forward_axis='y+'),
                        dict(side='sideways'),dict(max_distance_mm=0),dict(marker_rotation_deg=45)):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                parameters(dict(params,**changes))


class LocalityIngressTests(unittest.TestCase):
    def test_deeply_nested_captured_locality_is_malformed_not_recursion_error(self):
        """AGENTEE-363b: decoder recursion at the captured-locality ingress is a typed refusal."""
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import patch
        from agent_ee import placement_check
        from agent_ee.checks import CheckError
        from agent_ee.electrical_locality import DEPTH_ERROR

        with tempfile.TemporaryDirectory() as tmp:
            inputs = Path(tmp)
            (inputs / 'locality.json').write_text('[' * 10_000 + '0' + ']' * 10_000)

            def native(*args, **kwargs):
                raise AssertionError('refusal must precede native board probing')
            ctx = SimpleNamespace(
                params=dict(criterion='electrical-locality/1', drc_check='drc', locality='locality.json'),
                inputs=inputs, manifest=dict(files={'locality.json': digest(inputs / 'locality.json')}),
                execute=native, candidate_path=native)
            # Only the prerequisite native DRC result is substituted; parsing runs for real.
            with patch.object(placement_check, 'prerequisite_drc', return_value=({}, {})), \
                    self.assertRaises(CheckError) as raised:
                placement_check.check_placement(ctx)
        self.assertEqual(raised.exception.execution, 'malformed')
        self.assertEqual(str(raised.exception), f'electrical locality refused: {DEPTH_ERROR}')


if __name__=='__main__':
    unittest.main()
