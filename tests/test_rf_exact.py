"""Exact-CAD CPW controls, including native refill and independent DRC."""
import copy
from decimal import Decimal
from fractions import Fraction
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from toolchain import require_toolchain
from agent_ee.rf_exact import distance2, geometry_digest, point_distance2
from agent_ee.rf_geometry import evaluate, extract, parse_declaration, probe_request
from rf_exact_fixture import emit


def declaration(**changes):
    doc = {'schema': 'agent-ee.rf-geometry/1', 'net': '/RF', 'ground_net': 'GND',
           'signal_layer': 'F.Cu', 'reference_layer': 'In1.Cu',
           'cpw': {'width_mm': .293, 'gap_mm': .2032, 'width_tolerance_mm': 0, 'gap_tolerance_mm': 0}}
    doc['cpw'].update(changes)
    return doc


def resolved(doc=None):
    return parse_declaration(json.dumps(doc or declaration()).encode())[1]


class ExactArithmeticTests(unittest.TestCase):
    def test_zero_null_and_invalid_tolerances(self):
        self.assertEqual(resolved()['cpw']['width_nm'],293000)
        self.assertEqual(resolved()['cpw']['gap_nm'],203200)
        self.assertIsNone(resolved(declaration(width_tolerance_mm=None))['cpw']['width_tolerance_mm'])
        for value in (-1, True, float('nan'), float('inf')):
            for key in ('width_tolerance_mm','gap_tolerance_mm'):
                with self.subTest(value=value,key=key),self.assertRaises(ValueError):
                    resolved(declaration(**{key:value}))

    def test_decimal_tokens_are_not_rounded_into_equality(self):
        for key in ('width_mm','gap_mm'):
            raw = json.dumps(declaration()).replace('"'+key+'": '+str(declaration()['cpw'][key]),
                                                    '"'+key+'": 0.29300000000000000000001')
            with self.assertRaisesRegex(ValueError,'integer number of nm'):
                parse_declaration(raw)
        with self.assertRaises(ValueError):
            resolved(declaration(width_mm=.29286522460874))

    def test_exact_source_requires_decimal_capture(self):
        doc = declaration(); del doc['cpw']['width_mm']; del doc['cpw']['gap_mm']
        doc['source'] = {'path':'project/calc.json','sha256':'0'*64,'width_pointer':'/w','gap_pointer':'/g'}
        capture = {'document':{'w':.293,'g':.2032}, 'sha256':'0'*64}
        with self.assertRaisesRegex(ValueError,'decimal_document'):
            parse_declaration(json.dumps(doc),lambda _:capture)
        capture['decimal_document'] = {'w':Decimal('.293'),'g':Decimal('.2032')}
        self.assertEqual(parse_declaration(json.dumps(doc),lambda _:capture)[1]['cpw']['width_nm'],293000)

    def test_rational_distance_and_disjoint_collinear_segments(self):
        self.assertEqual(point_distance2((0,1),(0,0),(1,1)),Fraction(1,2))
        self.assertEqual(distance2((0,0),(1,0),(2,0),(3,0)),1)
        self.assertEqual(distance2((0,0),(2,2),(0,2),(2,0)),0)
        self.assertEqual(distance2((0,0),(10,0),(0,203200),(10,203200)),203200**2)


class ExactNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.cache = {}

    def case(self,name='nominal',**kwargs):
        if name not in self.cache:
            path = Path(self.tmp.name)/name/'board.kicad_pcb'
            ids = emit(path,**kwargs)
            native = extract(path,probe_request(resolved()))
            self.cache[name] = (path,ids,native)
        return self.cache[name]

    def cpw(self,native):
        return evaluate(resolved(),native)['sections']['cpw']['items']

    def test_nominal_ordinary_certificates_and_refill_connectivity(self):
        import pcbnew as k
        path,ids,before = self.case()
        items = self.cpw(before)
        self.assertEqual(items['width']['status'],'pass')
        self.assertEqual(items['reference']['status'],'pass')
        rows = items['gap']['intervals']
        ordinary = [r for r in rows if r['status']=='pass']
        self.assertTrue(ordinary,items['gap'])
        self.assertTrue(any(r.get('reason')=='transition' for r in rows))
        self.assertEqual(items['gap']['status'],'unknown') # launches are not certified
        for row in ordinary:
            self.assertEqual(len(row['certificates']),2)
            for c in row['certificates']:
                self.assertEqual(c['gap_nm'],'203200')
                self.assertEqual(c['centre_distance_squared_nm2'],'721990090000')
        board = k.LoadBoard(str(path)); k.ZONE_FILLER(board).Fill(board.Zones()); k.SaveBoard(str(path),board)
        board = k.LoadBoard(str(path)); board.BuildConnectivity()
        anchor = next(p for f in board.GetFootprints() for p in f.Pads() if p.m_Uuid.AsString()==ids['anchor'])
        members = {i.m_Uuid.AsString() for i in board.GetConnectivity().GetConnectedItems(anchor)}
        self.assertTrue(set(ids['flanks']+ids['vias']+ids['zones']) <= members)
        self.assertEqual(len(ids['vias']),4)
        after = extract(path,probe_request(resolved()))
        self.assertEqual(before['exact_cpw']['geometry_sha256'],after['exact_cpw']['geometry_sha256'])
        self.assertEqual(self.cpw(after)['gap'],items['gap'])

    def test_native_drc_and_dimension_controls(self):
        for name,kwargs,category in [('nominal',{},None),('below',{'gap':202200},'clearance'),
                                     ('above',{'gap':204200},None),('narrow',{'width':283000},'track_width'),
                                     ('wide',{'width':303000},'track_width')]:
            path,_,native = self.case(name,**kwargs)
            output = path.parent/'drc.json'
            proc = subprocess.run(['kicad-cli','pcb','drc','--format','json','--output',str(output),str(path)],
                                  capture_output=True,text=True,timeout=60)
            self.assertEqual(proc.returncode,0,proc.stderr)
            drc = json.loads(output.read_text())
            with self.subTest(name=name):
                if category:
                    self.assertIn(category,{v['type'] for v in drc['violations']})
                else:
                    self.assertEqual(drc['violations'],[])
                    self.assertEqual(drc['unconnected_items'],[])
                items = self.cpw(native)
                if name in ('below','above'):
                    self.assertEqual(items['gap']['status'],'defect')
                elif name in ('narrow','wide'):
                    self.assertEqual(items['width']['status'],'defect')

    def test_one_nanometre_width_mismatch_is_not_rounded_away(self):
        for width in (292999,293001):
            _,_,native = self.case(f'width-{width}',width=width)
            item = self.cpw(native)['width']
            self.assertEqual(item['status'],'defect')
            self.assertEqual(len(item['violations']),1)

    def test_missing_flank_never_borrows_zone_boundary(self):
        _,_,native = self.case('missing',missing_flank=True)
        gap = self.cpw(native)['gap']
        self.assertEqual(gap['status'],'defect')
        self.assertTrue(any(f['reason']=='explicit_flank_missing' for r in gap['intervals'] for f in r.get('failures',[])))

    def test_floating_reference_is_unknown_for_cpw_only(self):
        _,_,native = self.case('floating',floating=True)
        reference = self.cpw(native)['reference']
        self.assertEqual((reference['status'],reference['reason']),('unknown','reference_unanchored'))

    def test_composed_floating_reference_stays_a_stitching_defect(self):
        import rf_geometry_fixture as fx
        path = Path(self.tmp.name)/'composed.kicad_pcb'
        fx.make_board(path,vias=False)
        spec = parse_declaration(json.dumps(fx.declaration()))[1]
        result = evaluate(spec,extract(path,probe_request(spec)))
        self.assertEqual(result['sections']['cpw']['items']['reference']['reason'],'reference_unanchored')
        self.assertEqual(result['sections']['stitching']['status'],'defect')
        self.assertEqual(result['status'],'defect')

    def test_remote_reference_zone_does_not_poison_supplied_strip(self):
        from agent_ee.rf_exact import reference_binding
        _,_,native = self.case()
        facts = copy.deepcopy(native['exact_cpw'])
        zone = {'uuid':'remote','kind':'zone','net':'GND','layers':['In1.Cu'],
                'bbox_nm':[50000000,50000000,60000000,60000000], 'filled':True,
                'polygons_nm':[{'outline':[[50000000,50000000],[60000000,50000000],
                                          [60000000,60000000],[50000000,60000000]],'holes':[]}]}
        facts['primitives'].append(zone); facts['reference_zones'].append('remote')
        self.assertEqual(reference_binding(facts,resolved())['status'],'pass')
        zone['polygons_nm'][0]['outline'] = [[15000000,19000000],[25000000,19000000],
                                           [25000000,21000000],[15000000,21000000]]
        zone['bbox_nm'] = [15000000,19000000,25000000,21000000]
        self.assertEqual(reference_binding(facts,resolved())['status'],'unknown')

    def test_report_cannot_be_relabelled(self):
        _,_,native = self.case()
        doc = declaration(); doc['net'] = '/OTHER'
        with self.assertRaisesRegex(ValueError,'request does not match'):
            evaluate(resolved(doc),native)

    def test_non_axis_and_curves_are_located_unknown(self):
        for name,kwargs in [('diagonal',{'diagonal':True}),('arc',{'arc':True})]:
            _,_,native = self.case(name,**kwargs)
            gap = self.cpw(native)['gap']
            self.assertEqual(gap['status'],'unknown')
            self.assertTrue(all(r['reason']=='non_axis_or_curve' for r in gap['intervals']))
            if name=='diagonal':
                self.assertTrue(gap['intervals'][0]['grid_residuals'])
                self.assertTrue(any(abs(r['grid_residual_nm']) > 0 for r in gap['intervals'][0]['grid_residuals']))

    def test_geometry_digest_ignores_identity_but_detects_copper_changes(self):
        _,_,native = self.case()
        facts = copy.deepcopy(native['exact_cpw'])
        original = geometry_digest(facts)
        for p in facts['primitives']:
            p['uuid'] = 'fresh-id'
        facts['primitives'].reverse()
        self.assertEqual(geometry_digest(facts),original)
        next(p for p in facts['primitives'] if p['kind']=='segment')['width_nm'] += 1
        self.assertNotEqual(geometry_digest(facts),original)
        with self.assertRaisesRegex(ValueError,'geometry digest mismatch'):
            self.cpw({**native,'exact_cpw':facts})
