"""Native ground seed custody; clearance and plane performance are separate checks."""
import copy
import re
from pathlib import Path
import tempfile
import unittest

from agent_ee import acquisition_ground_ties as ties
from agent_ee.placement_job import JobRefusal
from agent_ee.symbol_library import children, parse_sexpr


class GroundTieTests(unittest.TestCase):
    def setUp(self):
        import pcbnew as k
        self.k = k
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = self.directory/'board.kicad_pcb'
        board = k.BOARD()
        board.SetCopperLayerCount(2)
        board.GetDesignSettings().SetBoardThickness(1_200_000)
        gnd = k.NETINFO_ITEM(board, 'GND')
        signal = k.NETINFO_ITEM(board, 'SIGNAL')
        board.Add(gnd)
        board.Add(signal)
        for ref, x in [('U2', 30), ('C4', 40), ('C5', 50)]:
            fp = k.FOOTPRINT(board)
            fp.SetReference(ref)
            fp.SetPosition(k.VECTOR2I(x*1_000_000, 40_000_000))
            board.Add(fp)
            numbers = ['3'] if ref == 'U2' else ['1', '2']
            for number in numbers:
                pad = k.PAD(fp)
                pad.SetNumber(number)
                pad.SetAttribute(k.PAD_ATTRIB_SMD)
                pad.SetShape(k.PAD_SHAPE_RECT)
                pad.SetSize(k.VECTOR2I(900_000, 900_000))
                layers=k.LSET()
                layers.AddLayer(k.F_Cu)
                pad.SetLayerSet(layers)
                pad.SetPosition(k.VECTOR2I(x*1_000_000 + (-1_000_000 if number in ('2','3') else 1_000_000),40_000_000))
                pad.SetNet(gnd if number in ('2','3') else signal)
                fp.Add(pad)
        k.SaveBoard(str(self.path), board)
        self.original = self.path.read_bytes()

    def seeded(self):
        data, receipt = ties.seed(self.path, self.original, ties.POLICY)
        output = self.directory/'seed.kicad_pcb'
        output.write_bytes(data)
        return output, receipt

    def test_generated_paths_preserve_native_source_and_replay_deterministically(self):
        output, receipt = self.seeded()
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(parse_sexpr(output.read_bytes())[:-6], parse_sexpr(self.original))
        repeated, again = ties.seed(self.path, self.original, ties.POLICY)
        self.assertEqual((repeated, again), (output.read_bytes(), receipt))
        result = ties.verify(output, receipt)
        self.assertEqual(result['status'], 'pass')
        self.assertEqual(len(result['connections']), 3)
        self.assertTrue(all(r['front_length_mm']+r['barrel_length_mm']<=3 for r in result['connections']))

    def test_translated_and_rotated_pad_frames_drive_the_seed(self):
        k = self.k
        board = k.LoadBoard(str(self.path))
        for fp in board.GetFootprints():
            fp.Rotate(fp.GetPosition(), k.EDA_ANGLE(90,k.DEGREES_T))
            fp.Move(k.VECTOR2I(7_000_000,-9_000_000))
        k.SaveBoard(str(self.path),board)
        changed = self.path.read_bytes()
        data, receipt = ties.seed(self.path,changed,ties.POLICY)
        output = self.directory/'rotated.kicad_pcb'
        output.write_bytes(data)
        ties.verify(output,receipt)
        for row in receipt['connections']:
            self.assertEqual(row['pad_nm'][0],row['via_nm'][0])
            self.assertNotEqual(row['pad_nm'][1],row['via_nm'][1])

    def test_changed_copper_or_endpoint_cannot_borrow_the_receipt(self):
        k = self.k
        output, receipt = self.seeded()
        good = output.read_bytes()
        for fault in ('missing','net','diameter','drill','position','stub','pad'):
            with self.subTest(fault=fault):
                output.write_bytes(good)
                board = k.LoadBoard(str(output))
                via = next(t for t in board.GetTracks() if t.GetClass()=='PCB_VIA')
                track = next(t for t in board.GetTracks() if t.GetClass()=='PCB_TRACK')
                if fault=='missing': board.Remove(via)
                elif fault=='net': pass  # Persist the mutation below; native save infers GND.
                elif fault=='diameter': via.SetWidth(700_000)
                elif fault=='drill': via.SetDrill(400_000)
                elif fault=='position': via.SetPosition(via.GetPosition()+k.VECTOR2I(1,0))
                elif fault=='stub': track.SetWidth(200_000)
                else:
                    fp=next(f for f in board.GetFootprints() if f.GetReference()=='U2')
                    pad=next(iter(fp.Pads()))
                    pad.SetPosition(pad.GetPosition()+k.VECTOR2I(1,0))
                k.SaveBoard(str(output),board)
                if fault=='net':
                    raw=output.read_bytes();uid=via.m_Uuid.AsString().encode()
                    match=re.search(rb'\(via\b(?:(?!\(via\b).)*?\(uuid "'+uid+rb'"\)\s*\)',raw,re.S)
                    self.assertIsNotNone(match)
                    self.assertEqual(match.group().count(b'(net "GND")'),1)
                    output.write_bytes(raw[:match.start()]+match.group().replace(
                        b'(net "GND")',b'(net "SIGNAL")')+raw[match.end():])
                with self.assertRaises(JobRefusal): ties.verify(output,receipt)

    def test_unknown_policy_source_or_helper_refuses(self):
        with self.assertRaisesRegex(JobRefusal,'captured bytes differ'):
            ties.seed(self.path,self.original+b'\n',ties.POLICY)
        policy=copy.deepcopy(ties.POLICY)
        policy['maximum_path_mm']=30
        with self.assertRaisesRegex(JobRefusal,'unsupported policy'):
            ties.seed(self.path,self.original,policy)
        output,receipt=self.seeded()
        receipt['helper_sha256']='0'*64
        with self.assertRaisesRegex(JobRefusal,'custody differs'):
            ties.verify(output,receipt)

    def test_repeated_endpoint_reference_refuses(self):
        k=self.k
        board=k.LoadBoard(str(self.path))
        next(fp for fp in board.GetFootprints() if fp.GetReference()=='C5').SetReference('C4')
        k.SaveBoard(str(self.path),board)
        with self.assertRaisesRegex(JobRefusal,'duplicate footprint'):
            ties.seed(self.path,self.path.read_bytes(),ties.POLICY)
