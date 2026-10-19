"""The actual pod's nominal J1 exclusion is an additive, source-pinned model."""
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RECIPE = ROOT / 'fixtures/telemetry-pod/electrical'
sys.path.insert(0, str(RECIPE))
from agent_ee.symbol_library import parse_sexpr, children, field


class J1CourtyardTests(unittest.TestCase):
    def setUp(self):
        self.producer = importlib.import_module('j1_courtyard')
        self.decision = (RECIPE / 'j1-placement-exclusion.json').read_bytes()
        self.root = Path(os.environ.get('AGENT_EE_POD_INPUTS',
            '/tmp/agent463-pod-preparation-20261018/native-v2/scaffold/inputs'))
        plan = json.loads(self.decision)
        self.inputs = {}
        for label, blob in plan['sources'].items():
            if label in ('geometry', 'registration'):
                path = Path('/tmp/agent463-pod-j1-geometry') / Path(blob['path']).name
            else:
                path = self.root / blob['path']
            if not path.is_file():
                if os.environ.get('AGENT_EE_REQUIRE_TOOLCHAIN') == '1':
                    self.fail('required exact retained J1 source absent: ' + str(path))
                self.skipTest('exact retained J1 source absent: ' + str(path))
            self.inputs[blob['path']] = path.read_bytes()
        self.source_path = plan['sources']['footprint']['path']

    def derive(self, inputs=None, decision=None):
        return self.producer.derive(self.inputs if inputs is None else inputs,
                                    self.decision if decision is None else decision)

    def test_adds_only_front_rectangle_and_is_deterministic(self):
        raw, receipt = self.derive()
        self.assertEqual((raw, receipt), self.derive())
        original = self.inputs[self.source_path]
        before, after = parse_sexpr(original), parse_sexpr(raw)
        self.assertEqual(after[:-1], before)
        rect = after[-1]
        self.assertEqual(rect[0], 'fp_rect')
        self.assertEqual(field(rect, 'start', 2), ['-30.4', '-16.05'])
        self.assertEqual(field(rect, 'end', 2), ['30.4', '16.05'])
        self.assertEqual(field(rect, 'layer', 1), ['F.CrtYd'])
        self.assertEqual(len(list(children(after, 'pad'))), 27)
        self.assertEqual(list(children(after, 'pad')), list(children(before, 'pad')))
        added = raw[len(original.rstrip())-1:-(len(original)-len(original.rstrip())+1)]
        self.assertEqual(raw.replace(added, b'', 1), original)
        self.assertEqual(receipt['derived']['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertFalse(receipt['qualification']['tolerance_qualified'])
        self.assertFalse(receipt['qualification']['assembly_clearance_qualified'])
        self.assertIsNone(receipt['qualification']['installed_height_mm'])
        self.assertFalse(receipt['done'])

    def test_refuses_each_changed_or_missing_source(self):
        for path in self.inputs:
            with self.subTest(path=path, mutation='changed'):
                mutated = dict(self.inputs, **{path:self.inputs[path]+b' '})
                with self.assertRaisesRegex(ValueError, 'source hash mismatch'):
                    self.derive(mutated)
            with self.subTest(path=path, mutation='missing'):
                mutated = dict(self.inputs); del mutated[path]
                with self.assertRaisesRegex(ValueError, 'missing source'):
                    self.derive(mutated)

    def test_refuses_unreviewed_padding_side_identity_or_acceptance(self):
        for key, value in [('added_margin_mm', '0.6'), ('layer', 'B.CrtYd'),
                           ('fpid', 'ampseal:other'), ('tolerance_qualified', True)]:
            changed = json.loads(self.decision)
            changed[key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'reviewed decision'):
                self.derive(decision=(json.dumps(changed)+'\n').encode())

    def test_refuses_pad_or_drawing_rebinding(self):
        for label in ('footprint', 'drawing'):
            changed = json.loads(self.decision)
            path = changed['sources'][label]['path']
            raw = self.inputs[path]
            raw = raw.replace(b'(at 14 4)', b'(at 14.1 4)') if label=='footprint' else raw+b'altered'
            self.assertNotEqual(raw, self.inputs[path])
            changed['sources'][label]['sha256'] = hashlib.sha256(raw).hexdigest()
            with self.subTest(label=label), self.assertRaisesRegex(ValueError, 'reviewed decision'):
                self.derive(dict(self.inputs, **{path:raw}), (json.dumps(changed)+'\n').encode())

    def test_duplicate_active_fpid_is_refused(self):
        from build_complete import add_footprint
        members = {}
        add_footprint(members, 'ampseal', 'ampseal-23', {'path':'one', 'sha256':'a'*64})
        add_footprint(members, 'ampseal', 'ampseal-23', {'path':'one', 'sha256':'a'*64})
        self.assertEqual(len(members['ampseal']), 1)
        with self.assertRaisesRegex(ValueError, 'duplicate footprint identity'):
            add_footprint(members, 'ampseal', 'ampseal-23', {'path':'two', 'sha256':'b'*64})

    def test_native_load_reload_keeps_pads_and_exact_front_rectangle(self):
        import pcbnew
        raw, _ = self.derive()
        with tempfile.TemporaryDirectory() as tmp:
            lib = Path(tmp)/'ampseal.pretty'; lib.mkdir()
            (lib/'ampseal-23.kicad_mod').write_bytes(raw)
            original_lib = Path(tmp)/'original.pretty'; original_lib.mkdir()
            (original_lib/'ampseal-23.kicad_mod').write_bytes(self.inputs[self.source_path])
            before = pcbnew.FootprintLoad(str(original_lib), 'ampseal-23')
            derived = pcbnew.FootprintLoad(str(lib), 'ampseal-23')
            def pads(fp):
                return sorted((p.GetNumber(), p.GetPosition().x, p.GetPosition().y,
                    p.GetSize().x, p.GetSize().y, p.GetDrillSize().x, p.GetDrillSize().y,
                    p.GetAttribute(), p.GetShape(), p.GetLayerSet().FmtHex()) for p in fp.Pads())
            self.assertEqual(pads(before), pads(derived))
            board = pcbnew.BOARD(); board.Add(derived)
            native = Path(tmp)/'footprint-roundtrip.kicad_pcb'
            pcbnew.SaveBoard(str(native), board)
            loaded_board = pcbnew.LoadBoard(str(native))
            reloaded = list(loaded_board.GetFootprints())[0]
            self.assertEqual(pads(before), pads(reloaded))
            rectangles = [g for g in reloaded.GraphicalItems() if g.GetLayer()==pcbnew.F_CrtYd]
            self.assertEqual(len(rectangles), 1)
            self.assertEqual((rectangles[0].GetStart().x, rectangles[0].GetStart().y,
                              rectangles[0].GetEnd().x, rectangles[0].GetEnd().y),
                             (-30400000,-16050000,30400000,16050000))
            self.assertFalse(any(g.GetLayer()==pcbnew.B_CrtYd for g in reloaded.GraphicalItems()))
