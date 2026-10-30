"""Source-bound strip generation and native rule-area preservation."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from agent_ee import acquisition_local_routing as routing, acquisition_mechanics as mechanics
from agent_ee.placement_job import JobRefusal
from agent_ee.symbol_library import parse_sexpr


def recipe():
    raw=(Path(__file__).resolve().parents[1]/'fixtures/acquisition-qualification/geometry-model.json').read_bytes()
    sources={name:'0'*64 for name in mechanics.SOURCES}
    sources.update({'analysis/geometry-model.json':mechanics.MODEL,'analysis/geometry_model.py':mechanics.CHECKER})
    return dict(declaration=dict(schema=mechanics.SCHEMA,sources=sources),model=json.loads(raw),model_raw=raw.decode())


PLAN=json.dumps(dict(schema='agent-ee.plane-plan/1',zones=[dict(net='GND',layer='B.Cu',outline='board',
    clearance_mm=.2,min_width_mm=.2,thermal=dict(connection='thermal',gap_mm=.25,spoke_width_mm=.25),
    priority=0,island_removal='always')])).encode()


class GroundStripTests(unittest.TestCase):
    def test_reservation_includes_declared_plane_clearance_and_deduplicates_shared_edges(self):
        strips={'one':[[1,2],[1,4]],'two':[[1,4],[1,2]]}
        self.assertEqual(routing.rectangles(strips,.2),[(700000,1700000,1300000,4300000)])
        self.assertEqual(routing.rectangles(strips,.4),[(500000,1500000,1500000,4500000)])

    def test_nonorthogonal_or_oversized_strips_refuse(self):
        for strips in ({'one':[[1,2],[2,3]]},{'one':[[1,2],[1,2]]},
                       {'one':[[i,0] for i in range(33)]}):
            with self.subTest(strips=strips):
                with self.assertRaises(JobRefusal):routing.rectangles(strips,.2)

    def test_native_rule_areas_preserve_geometry_and_every_restriction(self):
        import pcbnew as k
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'board.kicad_pcb';board=k.BOARD();board.SetCopperLayerCount(2)
            k.SaveBoard(str(path),board);original=path.read_bytes()
            changed,reservations=routing.reservation_bytes(original,recipe(),PLAN)
            self.assertEqual(parse_sexpr(changed)[:-len(reservations['areas'])],parse_sexpr(original))
            path.write_bytes(changed)
            self.assertEqual(len(routing.verify_reservations(path,reservations)),len(reservations['areas']))
            board=k.LoadBoard(str(path));k.SaveBoard(str(path),board)
            routing.verify_reservations(path,reservations)
            for edit in ('tracks','vias','pour','layer','lost'):
                with self.subTest(edit=edit):
                    path.write_bytes(changed);board=k.LoadBoard(str(path));zone=next(z for z in board.Zones())
                    if edit=='tracks':zone.SetDoNotAllowTracks(False)
                    elif edit=='vias':zone.SetDoNotAllowVias(False)
                    elif edit=='pour':zone.SetDoNotAllowZoneFills(True)
                    elif edit=='layer':zone.SetLayer(k.F_Cu)
                    else:board.Remove(zone)
                    k.SaveBoard(str(path),board)
                    with self.assertRaises(JobRefusal):routing.verify_reservations(path,reservations)
            with self.assertRaisesRegex(JobRefusal,'already present'):
                routing.reservation_bytes(changed,recipe(),PLAN)

    def test_changed_source_recipe_is_rejected(self):
        changed=copy.deepcopy(recipe());changed['model']['ground_strips']={}
        with self.assertRaisesRegex(ValueError,'recipe changed'):
            routing.reservation_bytes(b'(kicad_pcb)',changed,PLAN)
