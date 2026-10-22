"""Component-side through-hole board-thickness fit (AGENTEE-448) on original fixtures."""
import copy
from decimal import Decimal
import inspect
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

from agent_ee import tht_thickness_fit as fit
from agent_ee.board_envelope import validate
from agent_ee.storage import read_json, write_json

ROOT = Path(__file__).resolve().parents[1]
J1_FOOTPRINT = 'PodConnector:AMPSEAL_35_1-776163-1'
# TE's recommended board thickness for 1-776163-1 is a fact (AGENTEE-448); the drawing is not copied.
TE = {'class': 'manufacturer', 'citation': 'TE Connectivity 1-776163-1 customer drawing: '
                                           'recommended PCB thickness 1.57 mm'}


def j1(**extra):
    return {'reference': 'J1', 'part_number': '1-776163-1', 'footprint_id': J1_FOOTPRINT,
            'recommended_mm': 1.57, 'recommended_source': dict(TE), **extra}


def raw(*parts):
    return json.dumps({'schema': fit.SCHEMA, 'parts': list(parts)}).encode()


def envelope_bytes(thickness):
    """The original RC envelope with nominal stackup thickness ``thickness`` mm."""
    text = (ROOT / 'fixtures/board-envelope/rc.envelope.json').read_text()
    core = str(Decimal(thickness) - Decimal('0.07'))
    return (text.replace('"thickness_mm": 1.6', f'"thickness_mm": {thickness}')
            .replace('"thickness_mm": 1.53', f'"thickness_mm": {core}').encode())


def record(thickness):
    result = validate(envelope_bytes(thickness))
    assert Decimal(result['stackup']['thickness_mm']) == Decimal(thickness), result['stackup']
    return result


def nm(thickness):
    return int(Decimal(thickness) * 10 ** 6)


def native(thickness, *, ref='J1', fpid=J1_FOOTPRINT, attribute='PTH'):
    return {'thickness_nm': nm(thickness),
            'footprints': [{'ref': ref, 'uuid': 'u-j1', 'fpid': fpid, 'value': 'AMPSEAL',
                            'pads': [{'attribute': attribute}]}]}


def bindings(partnumber='1-776163-1', ref='J1'):
    return {ref: {'duplicate_disagreements': [], 'uuid': 'u-j1', 'lib_id': J1_FOOTPRINT, 'value': 'AMPSEAL',
                  'partnumber': partnumber, 'manufacturer': 'TE Connectivity'}}


def grade(thickness, part=None, **native_overrides):
    parts = fit.declarations(raw(part or j1()))
    return fit.evaluate(parts, record(thickness), native(thickness, **native_overrides), bindings())


class PolicyTests(unittest.TestCase):
    def test_j1_recommended_157_on_16_board_is_screened(self):
        result = grade('1.6')
        self.assertEqual(result['status'], 'pass', result)
        item, = result['measured']
        self.assertEqual(item['reason'], 'screened (default +/-10% component fit)')
        self.assertEqual(item['band_mm'], ['1.413', '1.727'])
        self.assertEqual(item['board_nominal_mm'], '1.6')
        self.assertEqual(item['recommended_source'], TE)

    def test_screening_band_is_inclusive_and_exact(self):
        for thickness, status in (('1.413', 'pass'), ('1.727', 'pass'),
                                  ('1.412999', 'unknown'), ('1.727001', 'unknown')):
            with self.subTest(thickness=thickness):
                self.assertEqual(grade(thickness)['status'], status)

    def test_outside_band_is_advisory_never_reject(self):
        result = grade('1.4')
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['defects'], [])
        self.assertEqual(result['unknown'], [
            'J1: board nominal 1.4 mm is below the default +/-10% component fit 1.413 mm; '
            'needs feature geometry or evidence'])
        self.assertEqual(grade('2.0')['status'], 'unknown')
        self.assertEqual(grade('2.0')['defects'], [])

    def test_explicit_hard_limits_govern(self):
        hard = {'min_mm': 1.5, 'max_mm': 1.65, 'source': {'class': 'datasheet', 'citation': 'Owned drawing note 4'}}
        passed = grade('1.6', j1(hard_limits=hard))
        self.assertEqual(passed['status'], 'pass')
        self.assertEqual(passed['measured'][0]['reason'], 'within manufacturer hard limit')
        self.assertEqual(grade('1.65', j1(hard_limits=hard))['status'], 'pass')
        failed = grade('1.7', j1(hard_limits=hard))
        self.assertEqual(failed['status'], 'defect')
        self.assertEqual(failed['defects'], ['J1: board nominal 1.7 mm is above the manufacturer hard limit 1.65 mm'])
        # Hard limits narrower than the default band reject inside it; wider ones pass outside it.
        self.assertEqual(grade('1.45', j1(hard_limits=hard))['status'], 'defect')
        wide = {**hard, 'min_mm': 1.2, 'max_mm': 2.4}
        self.assertEqual(grade('2.0', j1(hard_limits=wide))['status'], 'pass')

    def test_one_sided_hard_limit_keeps_the_screening_band_on_the_other_side(self):
        upper = {'max_mm': 1.65, 'source': dict(TE)}
        self.assertEqual(grade('1.7', j1(hard_limits=upper))['status'], 'defect')
        lower = grade('1.4', j1(hard_limits=upper))
        self.assertEqual((lower['status'], lower['defects']), ('unknown', []))
        self.assertEqual(lower['measured'][0]['basis'], ['default +/-10% component fit', 'manufacturer hard limit'])

    def test_wider_documented_fit_range_overrides_default(self):
        documented = {'min_mm': 1.2, 'max_mm': 2.4, 'source': {'class': 'manufacturer', 'citation': 'Owned app note'}}
        result = grade('2.0', j1(fit_range=documented))
        self.assertEqual(result['status'], 'pass')
        self.assertEqual(result['measured'][0]['reason'], 'within documented fit range')
        self.assertEqual(result['measured'][0]['band_mm'], ['1.2', '2.4'])
        outside = grade('2.5', j1(fit_range=documented))
        self.assertEqual((outside['status'], outside['defects']), ('unknown', []))
        self.assertIn('documented fit range 2.4 mm; needs feature geometry or evidence', outside['unknown'][0])
        # A hard limit still governs over a documented range.
        hard = {'max_mm': 1.9, 'source': dict(TE)}
        self.assertEqual(grade('2.0', j1(fit_range=documented, hard_limits=hard))['status'], 'defect')

    def test_missing_or_blank_citation_is_refused(self):
        cases = [j1(recommended_source={'class': 'manufacturer'}),
                 j1(recommended_source={'class': 'manufacturer', 'citation': '  '}),
                 j1(recommended_source={'class': 'owner', 'citation': 'said so'}),
                 {k: v for k, v in j1().items() if k != 'recommended_source'},
                 j1(hard_limits={'max_mm': 1.65}),
                 j1(hard_limits={'max_mm': 1.65, 'source': {'class': 'datasheet', 'citation': ''}}),
                 j1(fit_range={'min_mm': 1.2, 'max_mm': 2.4, 'source': {'class': 'datasheet'}})]
        for part in cases:
            with self.subTest(part=part), self.assertRaisesRegex(ValueError, 'source|citation|class'):
                fit.declarations(raw(part))

    def test_malformed_declarations_are_refused(self):
        cases = [raw(), b'{"schema": "agent-ee.tht-thickness-fit/2", "parts": []}', raw(j1(), j1()),
                 raw(j1(recommended_mm=0)), raw(j1(recommended_mm='1.57')), raw(j1(recommended_mm=1.5700001)),
                 raw(j1(reference=' J1')), raw(j1(tolerance_percent=10)),
                 raw(j1(hard_limits={'min_mm': 1.6, 'max_mm': 1.5, 'source': dict(TE)})),
                 raw(j1(hard_limits={'min_mm': 1.6, 'source': dict(TE)})),
                 raw(j1(hard_limits={'source': dict(TE)})),
                 raw(j1(fit_range={'min_mm': 1.2, 'source': dict(TE)})),
                 raw(j1(fit_range={'min_mm': 1.6, 'max_mm': 2.4, 'source': dict(TE)}))]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                fit.declarations(case)

    def test_fab_finished_thickness_tolerance_is_never_component_fit(self):
        # An envelope/2 record whose JLC +/-10% finished-thickness tolerance and physical total
        # would admit 1.6 mm cannot move the component verdict: only the nominal is read.
        base = record('1.4')
        tolerant = copy.deepcopy(base)
        tolerant['stackup'].update(nominal_thickness_mm='1.4', physical_thickness_mm='1.54',
                                   finished_thickness_tolerance={'percent': 10, 'min_nominal_thickness_mm': 0.4})
        tolerant['fab_profile'] = {'finished_thickness_tolerance': {'execution': 'ready',
                                                                    'expected': {'percent': 10}}}
        parts = fit.declarations(raw(j1()))
        verdicts = [fit.evaluate(parts, r, native('1.4'), bindings()) for r in (base, tolerant)]
        self.assertEqual(verdicts[0], verdicts[1])
        self.assertEqual(verdicts[1]['status'], 'unknown')
        self.assertEqual(verdicts[1]['measured'][0]['band_mm'], ['1.413', '1.727'])
        # A board nominal inside the component band passes whatever the fab tolerance says.
        narrow = copy.deepcopy(record('1.6'))
        narrow['stackup']['finished_thickness_tolerance'] = {'percent': 0}
        self.assertEqual(fit.evaluate(parts, narrow, native('1.6'), bindings())['status'], 'pass')
        source = inspect.getsource(fit).split('"""', 2)[2]
        self.assertNotIn('finished_thickness_tolerance', source)
        self.assertNotIn('physical_thickness', source)

    def test_declaration_must_bind_one_installed_plated_through_hole_part(self):
        for overrides, message in (({'ref': 'J2'}, 'exactly one native footprint'),
                                   ({'fpid': 'Owned:Other'}, 'differs from declared'),
                                   ({'attribute': 'SMD'}, 'no plated through-hole pad')):
            with self.subTest(overrides=overrides):
                result = grade('1.6', **overrides)
                self.assertEqual((result['status'], result['defects']), ('unknown', []))
                self.assertIn(message, result['unknown'][0])
        parts = fit.declarations(raw(j1()))
        wrong = fit.evaluate(parts, record('1.6'), native('1.6'), bindings('776228-1'))
        self.assertEqual(wrong['status'], 'unknown')
        self.assertIn("saved part number '776228-1' differs", wrong['unknown'][0])
        drift = fit.evaluate(parts, record('1.6'), native('1.5'), bindings())
        self.assertEqual(drift['status'], 'unknown')
        self.assertIn('native board thickness differs', drift['unknown'][0])

    def test_parameters_are_closed(self):
        from agent_ee.envelope_check import parameters, predicate_names
        params = {'criterion': 'tht-thickness-fit/1', 'envelope': 'envelope.json', 'thickness_fit': 'fit.json'}
        self.assertEqual(parameters(params), ['envelope.json', 'fit.json'])
        self.assertEqual(predicate_names(params), ['envelope:tht-thickness-fit/1'])
        for bad in ({k: v for k, v in params.items() if k != 'thickness_fit'}, {**params, 'id': 'J1'},
                    {**params, 'thickness_fit': '../fit.json'}):
            with self.subTest(params=bad), self.assertRaises(ValueError):
                parameters(bad)

    def test_criterion_never_covers_an_envelope_obligation(self):
        from agent_ee.envelope_check import CRITERIA
        envelope = read_json(ROOT / 'fixtures/board-envelope/rc.envelope.json')
        self.assertNotIn(CRITERIA['tht-thickness-fit/1'], envelope)
        from agent_ee.board_envelope import bind_native_envelope
        spec = {'id': 'fit', 'kind': 'board_envelope', 'params': {
            'criterion': 'tht-thickness-fit/1', 'envelope': 'envelope.json', 'thickness_fit': 'fit.json'}}
        for locator in ('/stackup', '/max_component_height_mm'):
            with self.subTest(locator=locator), self.assertRaisesRegex(ValueError, 'exact criterion'):
                bind_native_envelope({'id': 'R', 'source': {'locator': locator}}, spec, record('1.6'), {}, [])


BUILD = '''import pcbnew as k,sys
b=k.BOARD()
for a,z in (((0,0),(30,0)),((30,0),(30,20)),((30,20),(0,20)),((0,20),(0,0))):
    s=k.PCB_SHAPE(b)
    s.SetShape(k.SHAPE_T_SEGMENT)
    s.SetLayer(k.Edge_Cuts)
    s.SetStart(k.VECTOR2I(a[0]*1000000,a[1]*1000000))
    s.SetEnd(k.VECTOR2I(z[0]*1000000,z[1]*1000000))
    s.SetWidth(50000)
    b.Add(s)
b.GetDesignSettings().SetBoardThickness(int(sys.argv[2]))
fp=k.FOOTPRINT(b)
fp.SetFPID(k.LIB_ID('PodConnector','AMPSEAL_35_1-776163-1'))
fp.SetReference('J1')
fp.SetValue('AMPSEAL')
fp.SetField('Partnumber','1-776163-1')
pad=k.PAD(fp)
pad.SetNumber('1')
pad.SetShape(k.PAD_SHAPE_CIRCLE)
pad.SetAttribute(k.PAD_ATTRIB_PTH if sys.argv[3]=='pth' else k.PAD_ATTRIB_SMD)
pad.SetSize(k.VECTOR2I(2000000,2000000))
if sys.argv[3]=='pth':
    pad.SetDrillSize(k.VECTOR2I(1000000,1000000))
    pad.SetLayerSet(k.LSET.AllCuMask())
else:
    layers=k.LSET()
    layers.AddLayer(k.F_Cu)
    pad.SetLayerSet(layers)
fp.Add(pad)
fp.SetPosition(k.VECTOR2I(15000000,10000000))
b.Add(fp)
k.SaveBoard(sys.argv[1],b)
'''


class NativeThicknessFitTests(unittest.TestCase):
    """Registered dispatch over a captured original board, envelope and declaration."""
    def setUp(self):
        from tests.toolchain import require_toolchain
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='ee448-fit-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.count = 0

    def run_fit(self, thickness, declaration, pads='pth'):
        from agent_ee.candidate import capture
        from agent_ee.capabilities import describe_check
        from agent_ee.checks import run_checks
        self.count += 1
        source = self.root / f'source-{self.count}'
        source.mkdir()
        subprocess.run(['/usr/bin/python3', '-B', '-c', BUILD, str(source / 'board.kicad_pcb'),
                        str(nm(thickness)), pads], check=True, capture_output=True, timeout=60)
        (source / 'envelope.json').write_bytes(envelope_bytes(thickness))
        (source / 'fit.json').write_bytes(declaration)
        spec = {'id': 'fit', 'kind': 'board_envelope', 'params': {
            'criterion': 'tht-thickness-fit/1', 'envelope': 'envelope.json', 'thickness_fit': 'fit.json'}}
        scope = {'schema': 'agent-ee.scope/1', 'name': 'owned-tht-fit', 'scope': 'Through-hole board thickness fit',
                 'board': 'board.kicad_pcb', 'checks': [spec],
                 'inputs': [{'path': str(p), 'at': p.name} for p in source.iterdir()]}
        write_json(self.root / 'scope.json', scope)
        run = self.root / f'run-{self.count}'
        run.mkdir()
        manifest = capture(self.root / 'scope.json', run)
        descriptor = describe_check(spec, scope=scope, check_specs=[spec], files=manifest['files'],
                                    inputs=run / 'inputs')
        self.descriptor = descriptor
        if descriptor['status'] != 'supported':
            return None
        self.assertEqual(descriptor['predicates'], ['envelope:tht-thickness-fit/1'])
        self.assertIn('fit.json', descriptor['immutable_inputs'])
        result, = run_checks(run, time.monotonic() + 120)
        self.assertIn('work/fit/envelope-judgment.json', result['evidence'])
        return result

    def test_native_j1_pass_advisory_and_hard_defect(self):
        result = self.run_fit('1.6', raw(j1()))
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        self.assertEqual(result['details']['measured'][0]['reason'], 'screened (default +/-10% component fit)')
        result = self.run_fit('1.4', raw(j1()))
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)
        self.assertIn('needs feature geometry or evidence', result['summary'])
        self.assertEqual(result['details']['defects'], [])
        hard = {'max_mm': 1.58, 'source': dict(TE)}
        result = self.run_fit('1.6', raw(j1(hard_limits=hard)))
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)
        self.assertEqual(result['details']['defects'],
                         ['J1: board nominal 1.6 mm is above the manufacturer hard limit 1.58 mm'])

    def test_native_smd_footprint_and_uncited_declaration_do_not_pass(self):
        result = self.run_fit('1.6', raw(j1()), pads='smd')
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)
        self.assertIn('no plated through-hole pad', result['summary'])
        self.assertIsNone(self.run_fit('1.6', raw(j1(recommended_source={'class': 'manufacturer'}))))
        self.assertEqual(self.descriptor['status'], 'unsupported')
        self.assertIn('citation', str(self.descriptor['unsupported_reasons']))


if __name__ == '__main__':
    unittest.main()
