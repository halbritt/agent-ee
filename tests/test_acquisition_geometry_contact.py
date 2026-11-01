"""Native copper contact must be proved without inventing a shorter path."""
import importlib.util
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import pcbnew as k

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/acquisition-film/geometry_contact.py'
spec = importlib.util.spec_from_file_location('film_geometry_contact', SOURCE)
contact = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contact)
NATIVE = Path(os.environ.get('AGENT_EE_FILM_NATIVE', '/nonexistent/film-native'))


class PadTrackContactTests(unittest.TestCase):
    def setUp(self):
        self.board = k.BOARD()
        self.board.SetCopperLayerCount(2)
        self.net = k.NETINFO_ITEM(self.board, '/VIN', 1)
        self.other_net = k.NETINFO_ITEM(self.board, '/OTHER', 2)
        self.board.Add(self.net)
        self.board.Add(self.other_net)
        fp = k.FOOTPRINT(self.board)
        fp.SetReference('C1')
        self.board.Add(fp)
        self.pad = k.PAD(fp)
        self.pad.SetNumber('1')
        self.pad.SetAttribute(k.PAD_ATTRIB_SMD)
        self.pad.SetShape(k.PAD_SHAPE_RECT)
        self.pad.SetSize(k.VECTOR2I(k.FromMM(1.4), k.FromMM(2.7)))
        self.pad.SetPosition(k.VECTOR2I(k.FromMM(10), k.FromMM(10)))
        layers = k.LSET()
        layers.AddLayer(k.F_Cu)
        self.pad.SetLayerSet(layers)
        self.pad.SetNet(self.net)
        fp.Add(self.pad)
        self.track = k.PCB_TRACK(self.board)
        self.track.SetLayer(k.F_Cu)
        self.track.SetWidth(k.FromMM(.25))
        self.track.SetNet(self.net)
        self.board.Add(self.track)
        self.move_track(9.2)

    def move_track(self, x):
        self.track.SetStart(k.VECTOR2I(k.FromMM(x), k.FromMM(8.5)))
        self.track.SetEnd(k.VECTOR2I(k.FromMM(x), k.FromMM(11.5)))

    def test_edge_contact_uses_a_native_copper_witness_and_conservative_length(self):
        self.assertFalse(self.pad.HitTest(k.VECTOR2I(k.FromMM(9.2), k.FromMM(10))))
        result = contact.pad_track_contact(self.pad, self.track, k.F_Cu)
        self.assertGreater(result['contact_area_mm2'], 0)
        self.assertGreaterEqual(result['path_length_mm'], .8)
        point = k.VECTOR2I(*(round(x * 1e6) for x in result['contact_mm']))
        self.assertTrue(self.pad.HitTest(point))
        self.assertTrue(self.track.HitTest(point))
        self.assertAlmostEqual(result['path_length_mm'],
            math.dist((10, 10), result['contact_mm']) +
            math.dist(result['contact_mm'], result['track_point_mm']))

    def test_touch_gap_foreign_net_and_wrong_layer_do_not_create_connections(self):
        for x in (9.175, 9.174):
            with self.subTest(x=x):
                self.move_track(x)
                self.assertIsNone(contact.pad_track_contact(self.pad, self.track, k.F_Cu))
        self.move_track(9.2)
        self.track.SetNet(self.other_net)
        self.assertIsNone(contact.pad_track_contact(self.pad, self.track, k.F_Cu))
        self.track.SetNet(self.net)
        self.track.SetLayer(k.B_Cu)
        self.assertIsNone(contact.pad_track_contact(self.pad, self.track, k.F_Cu))
        self.assertIsNone(contact.pad_track_contact(self.pad, self.track, k.B_Cu))

    def test_through_hole_contact_is_not_treated_as_a_convex_smd_path(self):
        self.pad.SetAttribute(k.PAD_ATTRIB_PTH)
        self.pad.SetDrillSize(k.VECTOR2I(k.FromMM(.3), k.FromMM(.3)))
        with self.assertRaisesRegex(ValueError, 'convex SMD'):
            contact.pad_track_contact(self.pad, self.track, k.F_Cu)


@unittest.skipUnless((NATIVE / 'project/acquisition.kicad_pcb').is_file(), 'captured failing film board required')
class SavedBoardGeometryTests(unittest.TestCase):
    def test_corrected_witnesses_meet_original_limits_without_changing_the_board(self):
        from agent_ee import typed_overlay
        original = (NATIVE / 'project/acquisition.kicad_pcb').read_bytes()
        self.assertEqual(sha256(original).hexdigest(),
            '2040f20bd2eda776d37eb39a296bbc2c7429f61ff268aa0cbfe9b7df0cac38b1')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(NATIVE / 'project', root / 'project')
            shutil.copyfile(NATIVE / 'groups.json', root / 'groups.json')
            (root / 'analysis').mkdir()
            for name in ('geometry_model.py', 'geometry_contact.py', 'geometry-model.json'):
                shutil.copyfile(SOURCE.parent / name, root / 'analysis' / name)
            output = root / 'geometry.json'
            result = subprocess.run([sys.executable, str(root / 'analysis/geometry_model.py'),
                str(root), str(output)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            observed = json.loads(output.read_text())
            limits = typed_overlay._check_pool(json.loads((NATIVE / 'requirement-map.json').read_text()))[
                'engineering-geometry']['params']['limits']
            for name, limit in limits.items():
                with self.subTest(metric=name):
                    if 'min' in limit:
                        self.assertGreaterEqual(observed['metrics'][name], limit['min'])
                    if 'max' in limit:
                        self.assertLessEqual(observed['metrics'][name], limit['max'])
            self.assertEqual((root / 'project/acquisition.kicad_pcb').read_bytes(), original)
            self.assertEqual(len(observed['parts']), 17)
            self.assertTrue(any(w['reference'] == 'C1' and w['pad'] == '1'
                                for w in observed['pad_track_contacts']))
        self.assertEqual((NATIVE / 'project/acquisition.kicad_pcb').read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
