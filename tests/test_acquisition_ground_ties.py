"""Native ground seed custody; clearance and plane performance are separate checks."""
import copy
import json
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
        regulator = k.FOOTPRINT(board)
        regulator.SetReference('U1')
        regulator.SetPosition(k.VECTOR2I(60_000_000, 40_000_000))
        board.Add(regulator)
        for dx, dy in ((-500_000, -500_000), (500_000, -500_000), (-500_000, 500_000), (500_000, 500_000)):
            hole = k.PAD(regulator)
            hole.SetNumber('9')
            hole.SetAttribute(k.PAD_ATTRIB_PTH)
            hole.SetShape(k.PAD_SHAPE_CIRCLE)
            hole.SetSize(k.VECTOR2I(609_600, 609_600))
            hole.SetDrillSize(k.VECTOR2I(304_800, 304_800))
            hole.SetLayerSet(k.LSET.AllCuMask())
            hole.SetPosition(k.VECTOR2I(60_000_000 + dx, 40_000_000 + dy))
            hole.SetNet(gnd)
            regulator.Add(hole)
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
        self.assertEqual(parse_sexpr(output.read_bytes())[:-8], parse_sexpr(self.original))
        repeated, again = ties.seed(self.path, self.original, ties.POLICY)
        self.assertEqual((repeated, again), (output.read_bytes(), receipt))
        result = ties.verify(output, receipt)
        self.assertEqual(result['status'], 'pass')
        # KiCad rewrites numbers without trailing zeros; that is not a change.
        board = self.k.LoadBoard(str(output))
        self.k.SaveBoard(str(output), board)
        self.assertEqual(ties.verify(output, receipt)['status'], 'pass')
        self.assertEqual(len(result['connections']), 3)
        self.assertTrue(all(r['front_length_mm']+r['barrel_length_mm']<=3 for r in result['connections']))
        self.assertEqual(len(result['bars']), 2)

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
        for bar in receipt['bars']:
            self.assertEqual(sum(a!=b for a,b in zip(bar['start_nm'],bar['end_nm'])),1)

    def test_changed_copper_or_endpoint_cannot_borrow_the_receipt(self):
        k = self.k
        output, receipt = self.seeded()
        good = output.read_bytes()
        for fault in ('missing','net','diameter','bottom land','backdrill','drill','position','stub','pad','bar','no bar','hole','hole drill'):
            with self.subTest(fault=fault):
                output.write_bytes(good)
                board = k.LoadBoard(str(output))
                via = next(t for t in board.GetTracks() if t.GetClass()=='PCB_VIA')
                track = next(t for t in board.GetTracks() if t.GetClass()=='PCB_TRACK')
                if fault=='missing': board.Remove(via)
                elif fault in ('net','backdrill'): pass  # Persist the mutation in the saved bytes below.
                elif fault=='diameter': via.SetWidth(700_000)
                elif fault=='bottom land':
                    # Independent review 2026-11-01: the receipt checked F.Cu only.
                    via.Padstack().SetMode(k.PADSTACK.MODE_FRONT_INNER_BACK)
                    via.Padstack().SetSize(k.VECTOR2I(600_000,600_000),k.F_Cu)
                    via.Padstack().SetSize(k.VECTOR2I(1_200_000,1_200_000),k.B_Cu)
                elif fault=='drill': via.SetDrill(400_000)
                elif fault=='position': via.SetPosition(via.GetPosition()+k.VECTOR2I(1,0))
                elif fault=='stub': track.SetWidth(200_000)
                elif fault in ('bar','no bar'):
                    identity=receipt['bars'][0]['segment_uuid']
                    bar=next(t for t in board.GetTracks() if t.m_Uuid.AsString()==identity)
                    if fault=='bar': bar.SetWidth(600_000)
                    else: board.Remove(bar)
                elif fault=='hole drill':
                    # Independent review 2026-11-01: the receipt bound hole positions only.
                    fp=next(f for f in board.GetFootprints() if f.GetReference()=='U1')
                    for hole in fp.Pads():
                        hole.SetDrillSize(k.VECTOR2I(500_000,500_000))
                else:
                    fp=next(f for f in board.GetFootprints() if f.GetReference()==('U2' if fault=='pad' else 'U1'))
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
                if fault=='backdrill':
                    # Independent review 2026-11-01: only named fields were compared.
                    raw=output.read_bytes();marker=b'(uuid "'+via.m_Uuid.AsString().encode()+b'")'
                    self.assertEqual(raw.count(marker),1)
                    output.write_bytes(raw.replace(marker,b'(backdrill (size 0.8) (layers "B.Cu" "F.Cu"))\n'+marker))
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

    def test_pending_plane_allowance_covers_only_generated_vias_before_the_plane_exists(self):
        # Regression for the first native build on 2026-11-01: the reservation
        # DRC runs before the bottom plane exists, so every generated via dangled.
        from agent_ee.acquisition_local_return import _legal
        from agent_ee.placement_job import drc
        output,receipt=self.seeded()
        report,_=drc(output,self.directory)
        dangling=[v for v in report['violations'] if v['type']=='via_dangling']
        self.assertEqual(len(dangling),3)
        # This synthetic board has no outline, so keep only the findings under test.
        report={**report,'violations':dangling,'schematic_parity':[]}
        self.assertFalse(_legal(report))
        self.assertTrue(_legal(ties.without_pending_plane_warnings(report,receipt)))
        unrelated=copy.deepcopy(receipt)
        unrelated['connections'][0]['via_uuid']='00000000-0000-0000-0000-000000000000'
        self.assertFalse(_legal(ties.without_pending_plane_warnings(report,unrelated)))

    def test_retained_ground_tie_policy_still_generates_its_original_copper(self):
        # Independent review 2026-11-01: model d451823e loaded but its policy refused to seed.
        policy=json.loads((Path(ties.__file__).parents[2]/'fixtures/acquisition-film/mechanics-test-case-r3.json').read_text())
        policy=json.loads(policy['model_raw'])['local_plane_returns']
        self.assertEqual(policy['schema'],'agent-ee.acquisition-ground-ties/1')
        data,receipt=ties.seed(self.path,self.original,policy)
        output=self.directory/'retained.kicad_pcb'
        output.write_bytes(data)
        self.assertEqual(parse_sexpr(data)[:-6],parse_sexpr(self.original))
        self.assertEqual(receipt['bars'],[])
        self.assertEqual(ties.verify(output,receipt)['status'],'pass')

    def test_exposed_pad_needs_four_plated_ground_holes_in_two_rows(self):
        k=self.k
        for change in ('three holes','foreign net','skewed row'):
            with self.subTest(change=change):
                self.path.write_bytes(self.original)
                board=k.LoadBoard(str(self.path))
                fp=next(f for f in board.GetFootprints() if f.GetReference()=='U1')
                holes=list(fp.Pads())
                if change=='three holes': fp.Remove(holes[0])
                elif change=='foreign net': holes[0].SetNet(board.FindNet('SIGNAL'))
                else: holes[0].SetPosition(holes[0].GetPosition()+k.VECTOR2I(1,1))
                k.SaveBoard(str(self.path),board)
                with self.assertRaisesRegex(JobRefusal,'exposed pad|exposed-pad'):
                    ties.seed(self.path,self.path.read_bytes(),ties.POLICY)

    def test_repeated_endpoint_reference_refuses(self):
        k=self.k
        board=k.LoadBoard(str(self.path))
        next(fp for fp in board.GetFootprints() if fp.GetReference()=='C5').SetReference('C4')
        k.SaveBoard(str(self.path),board)
        with self.assertRaisesRegex(JobRefusal,'duplicate footprint'):
            ties.seed(self.path,self.path.read_bytes(),ties.POLICY)
