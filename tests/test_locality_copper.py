"""AGENTEE-328: copper offsets, captured UUID selection and policy binding."""
import copy
from fractions import Fraction
from pathlib import Path
import tempfile
import unittest

from agent_ee import electrical_locality as locality, placement_adapter as adapter
from agent_ee.placement_probe import extract
from agent_ee.placer import pack, transform
from agent_ee.storage import digest, write_json
from tests.test_electrical_locality import board, declaration, MM
from tests.test_placer import request
from tests.toolchain import require_toolchain


class CopperLocalityTests(unittest.TestCase):
    def test_distinct_uuid_near_far_requires_selector_and_validates_all_fields(self):
        facts, doc = board(), declaration(limit=100*MM)
        cap = facts['footprints'][1]
        near = cap['pads'][0]
        near['uuid'] = '00000000-0000-4000-8000-000000000001'
        far = copy.deepcopy(near)
        far.update(uuid='00000000-0000-4000-8000-000000000002', position_nm=[1000*MM, 0])
        cap['pads'].append(far)
        pair = doc['obligations'][0]['supply']
        self.assertEqual(locality.grade(facts, doc)['status'], 'unknown')
        self.assertEqual(pack(facts, request(electrical_locality=doc))['placements'], [])
        pair['to']['uuid'] = near['uuid']
        self.assertEqual(locality.grade(facts, doc)['status'], 'pass')
        pair['to']['uuid'] = far['uuid']
        self.assertEqual(locality.grade(facts, doc)['status'], 'defect')
        for key, value in (('uuid', '00000000-0000-4000-8000-000000000003'),
                           ('pad', '2'), ('net', 'OTHER'), ('reference', 'U1')):
            broken = {**pair['to'], key: value}
            with self.subTest(key=key), self.assertRaises(ValueError):
                locality.resolve(facts['footprints'], broken)
        cap['pads'].append(copy.deepcopy(far))
        self.assertEqual(locality.grade(facts, doc)['status'], 'unknown')

    def test_native_copper_evidence_required_and_rotates_with_pad(self):
        facts, doc = board(), declaration(limit=5*MM)
        cap = facts['footprints'][1]
        cap['pads'][0]['copper_centroid_offset_nm'] = [[20*MM, 1], [0, 1]]
        selected = transform(cap, [6*MM, 6*MM], 90)
        self.assertEqual(locality.resolve([selected], doc['obligations'][0]['supply']['to']),
                         [6*MM, -15*MM])
        facts['footprints'][1] = selected
        self.assertEqual(locality.grade(facts, doc)['status'], 'defect')
        for pad in selected['pads']:
            pad.pop('copper_centroid_offset_nm', None)
        self.assertEqual(locality.grade(facts, doc)['status'], 'unknown')


class LocalityBindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.rel = 'project/locality.json'
        self.policy_path = 'project/policy.json'
        self.doc = declaration()
        write_json(self.root/self.rel, self.doc)
        self.policy = request(electrical_locality=self.doc)
        self.check = dict(kind='placement', params=dict(criterion=locality.CRITERION, locality=self.rel, drc_check='drc'))
        self.manifest = dict(files={self.rel: digest(self.root/self.rel)}, scope=dict(checks=[self.check]))

    def bind(self, mutable=()):
        write_json(self.root/self.policy_path, self.policy)
        self.manifest['files'][self.policy_path] = digest(self.root/self.policy_path)
        return adapter.bind_locality(self.policy, self.manifest, self.root, mutable)

    def test_matching_file_binds_raw_hash_and_derives_guidance_if_omitted(self):
        expected = dict(path=self.rel, sha256=digest(self.root/self.rel))
        self.assertEqual(self.bind(), expected)
        self.assertEqual(self.policy['electrical_locality_binding'], expected)
        self.policy.pop('electrical_locality')
        self.policy.pop('electrical_locality_binding')
        self.assertEqual(self.bind(), expected)
        self.assertEqual(self.policy['electrical_locality'], self.doc)

    def test_coordinator_binding_and_helper_request_retain_source(self):
        from tests.test_placement_adapter import TEXT, BOARD, SCHEMATIC, POLICY
        self.policy['fixed_refs'] = []
        write_json(self.root/POLICY, self.policy)
        (self.root/BOARD).write_text(TEXT)
        (self.root/SCHEMATIC).write_text('owned schematic placeholder; binding only')
        self.manifest['scope'].update(board=BOARD, schematic=SCHEMATIC)
        for rel in (POLICY, BOARD, SCHEMATIC):
            self.manifest['files'][rel] = digest(self.root/rel)
        declaration = dict(schema=adapter.ADAPTER_SCHEMA, helper='agent-ee.placer/1', policy=POLICY,
                           fixed_footprints=[], timeout_s=600)
        record, _, policy, _, _ = adapter.binding(declaration, self.manifest, self.root, [BOARD])
        request = adapter.helper_request((self.root/BOARD).read_bytes(), policy, [])
        self.assertEqual(record['electrical_locality'], request['policy']['electrical_locality_binding'])
        self.assertEqual(record['electrical_locality']['sha256'], digest(self.root/self.rel))
        self.assertEqual(request['policy']['electrical_locality'], self.doc)


    def test_mismatched_mutable_missing_changed_linked_and_ungraded_refuse(self):
        for mode in ('mismatch', 'mutable', 'missing', 'changed', 'symlink', 'ungraded', 'spoofed', 'two_files', 'float_limit'):
            with self.subTest(mode=mode):
                if (self.root/self.rel).is_symlink(): (self.root/self.rel).unlink()
                self.policy = request(electrical_locality=declaration())
                write_json(self.root/self.rel, declaration())
                self.manifest = dict(files={self.rel: digest(self.root/self.rel)}, scope=dict(checks=[self.check]))
                if mode == 'mismatch': self.policy['electrical_locality']['obligations'][0]['supply']['max_distance_nm'] += 1
                if mode == 'float_limit': self.policy['electrical_locality']['obligations'][0]['supply']['max_distance_nm'] = float(5*MM)
                if mode == 'missing': self.manifest['files'].pop(self.rel)
                if mode == 'changed': (self.root/self.rel).write_text('{}')
                if mode == 'symlink':
                    (self.root/'target.json').write_bytes((self.root/self.rel).read_bytes())
                    (self.root/self.rel).unlink()
                    (self.root/self.rel).symlink_to(self.root/'target.json')
                if mode == 'ungraded': self.manifest['scope']['checks'] = []
                if mode == 'spoofed': self.policy['electrical_locality_binding'] = dict(path=self.rel, sha256='0'*64)
                if mode == 'two_files':
                    write_json(self.root/'other.json', self.doc)
                    self.manifest['files']['other.json'] = digest(self.root/'other.json')
                    self.manifest['scope']['checks'].append(dict(kind='placement', params={**self.check['params'], 'locality':'other.json'}))
                with self.assertRaises(ValueError): self.bind([self.rel] if mode=='mutable' else [])


class NativeCopperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)

    def test_native_custom_copper_centroid_is_not_its_anchor(self):
        from tests.test_placer_custom_pads import CustomPadPlacementTest
        from tests.test_custom_pad_index import PRIMITIVES
        helper = CustomPadPlacementTest()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        for back in (False, True):
            for angle in (0,90,180,270):
                source = helper.source(PRIMITIVES['rect'], rotation=angle, back=back)
                facts = extract(source)
                cap = next(f for f in facts['footprints'] if f['ref']=='C1')
                pad = cap['pads'][0]
                self.assertIn('copper_centroid_offset_nm', pad, pad)
                center = locality.resolve([cap], dict(reference='C1', pad=pad['number'], net=pad['net']))
                self.assertEqual(sum((x-y)**2 for x,y in zip(center,pad['position_nm'])), (MM//2)**2)
                # A transformed captured centroid must equal a native rotation of that copper.
                moved = transform(cap, cap['position_nm'], (angle+90)%360)
                from agent_ee.placer import rotate
                expected_offset = rotate([Fraction(*v) for v in pad['copper_centroid_offset_nm']], 1)
                self.assertEqual(moved['pads'][0]['copper_centroid_offset_nm'],
                                 [[v.numerator,v.denominator] for v in expected_offset])
        source = helper.source(PRIMITIVES['circle'])
        pad = next(f for f in extract(source)['footprints'] if f['ref']=='C1')['pads'][0]
        self.assertNotIn('copper_centroid_offset_nm', pad)
        self.assertIn('curved', pad['copper_centroid_reason'])

    def test_native_empty_non_copper_and_per_layer_centroids_refuse(self):
        import pcbnew as k
        from agent_ee.placement_probe import copper_centroid
        native = k.BOARD()
        fp = k.FOOTPRINT(native)
        native.Add(fp)
        pad = k.PAD(fp)
        fp.Add(pad)
        pad.SetShape(k.PAD_SHAPE_RECT)
        pad.SetLayerSet(pad.SMDMask())
        pad.SetSize(k.VECTOR2I(0,0))
        with self.assertRaisesRegex(ValueError, 'nonempty'):
            copper_centroid(pad,{})
        pad.SetSize(k.VECTOR2I(MM,MM))
        layers = k.LSET()
        layers.AddLayer(k.F_Mask)
        pad.SetLayerSet(layers)
        with self.assertRaisesRegex(ValueError, 'no copper'):
            copper_centroid(pad,{})
        pad.SetLayerSet(pad.SMDMask())
        pad.Padstack().SetMode(k.PADSTACK.MODE_FRONT_INNER_BACK)
        with self.assertRaisesRegex(ValueError, 'per-layer'):
            copper_centroid(pad,{})

    def test_native_triangle_keeps_exact_fractional_centroid(self):
        from tests.test_placer_custom_pads import CustomPadPlacementTest
        helper = CustomPadPlacementTest()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        triangle = '(gr_poly (pts (xy -1 -1) (xy 3 -1) (xy -1 2)) (width 0) (fill yes))'
        source = helper.source(triangle)
        cap = next(f for f in extract(source)['footprints'] if f['ref']=='C1')
        pad = cap['pads'][0]
        self.assertEqual(pad['copper_centroid_offset_nm'], [[MM,3],[0,1]])
        self.assertEqual(locality.resolve([cap], dict(reference='C1', pad='1', net=pad['net'])),
                         [pad['position_nm'][0]+Fraction(MM,3), pad['position_nm'][1]])

    def test_exact_fractional_squared_distance_is_serializable(self):
        import json
        facts, doc = board(), declaration(limit=100*MM)
        facts['footprints'][1]['pads'][0]['copper_centroid_offset_nm'] = [[1,3],[0,1]]
        measured = locality.grade(facts,doc)['measured'][0]
        exact = Fraction(**measured['distance_squared_nm'])
        a = facts['footprints'][0]['pads'][0]['position_nm']
        b = facts['footprints'][1]['pads'][0]['position_nm']
        self.assertEqual(exact, (b[0]-a[0]+Fraction(1,3))**2 + (b[1]-a[1])**2)
        json.dumps(measured)

    def test_offset_rect_measures_copper_not_anchor_all_turns_both_sides(self):
        import pcbnew as k
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'offset.kicad_pcb'
            native = k.BOARD()
            nets = {}
            for name in ('VCC', 'GND'):
                nets[name] = k.NETINFO_ITEM(native, name)
                native.Add(nets[name])
            for ref, x in (('U1', 10), ('C1', 11)):
                fp = k.FOOTPRINT(native)
                fp.SetReference(ref)
                native.Add(fp)
                fp.SetPosition(k.VECTOR2I(x*MM, 10*MM))
                for number, net in (('1','VCC'), ('2','GND')):
                    pad = k.PAD(fp)
                    pad.SetNumber(number)
                    pad.SetAttribute(k.PAD_ATTRIB_SMD)
                    pad.SetShape(k.PAD_SHAPE_RECT)
                    pad.SetLayerSet(pad.SMDMask())
                    pad.SetSize(k.VECTOR2I(MM//2, MM//2))
                    pad.SetPosition(fp.GetPosition())
                    pad.SetNet(nets[net])
                    if ref == 'C1' and number == '1':
                        pad.SetOffset(k.PADSTACK.ALL_LAYERS, k.VECTOR2I(10*MM, 0))
                    fp.Add(pad)
            doc = declaration(limit=2*MM)
            for back in (False, True):
                for angle in (0,90,180,270):
                    cap = next(f for f in native.GetFootprints() if f.GetReference()=='C1')
                    if cap.IsFlipped() != back: cap.Flip(cap.GetPosition(), k.FLIP_DIRECTION_LEFT_RIGHT)
                    cap.SetOrientationDegrees(angle)
                    k.SaveBoard(str(path), native)
                    facts = extract(path)
                    result = locality.grade(facts, doc)
                    with self.subTest(back=back, angle=angle):
                        self.assertEqual(result['status'], 'defect', result)
                        self.assertGreater(result['measured'][0]['distance_mm'], 8)
