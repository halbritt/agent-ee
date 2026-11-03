"""Native saved-board contracts; run under native-admit in the production image."""
from pathlib import Path
import tempfile
import unittest

from agent_ee import local_decoupling as local
from agent_ee.decoupling_loops import measure
from agent_ee.placement_job import Clock, JobRefusal


def contract(limit=None):
    row = dict(id='cell', supply=[dict(reference=r, pad='1', net='/rail') for r in ('IC', 'BYP')],
               **{'return': [dict(reference=r, pad='2', net='/return') for r in ('IC', 'BYP')]})
    if limit is not None:
        row.update(max_loop_mm=limit, basis=local.BASIS)
    return dict(schema=local.SCHEMA, relations=[row], planes=[dict(net='/return', layer='B.Cu')], tie_other_pads=False)


class LocalCopperTests(unittest.TestCase):
    def setUp(self):
        import pcbnew as k
        self.k = k
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = self.root / 'board.kicad_pcb'
        self.board = board = k.BOARD()
        board.SetCopperLayerCount(2)
        board.GetDesignSettings().SetBoardThickness(1_200_000)
        # Explicit test routing rules, with ample space for a plane tie.
        cls = board.GetAllNetClasses()['Default']
        cls.SetTrackWidth(400_000)
        cls.SetClearance(200_000)
        cls.SetViaDiameter(600_000)
        cls.SetViaDrill(300_000)
        nets = {}
        for name in ('/rail', '/return', '/foreign'):
            net = k.NETINFO_ITEM(board, name)
            board.Add(net)
            nets[name] = net
        for ref, x in [('IC', 30), ('BYP', 33), ('OTHER', 38)]:
            fp = k.FOOTPRINT(board)
            fp.SetReference(ref)
            fp.SetPosition(k.VECTOR2I(x * 1_000_000, 30_000_000))
            board.Add(fp)
            for number, y, net in [('1', 29, '/rail'), ('2', 31, '/return')]:
                pad = k.PAD(fp)
                pad.SetNumber(number)
                pad.SetAttribute(k.PAD_ATTRIB_SMD)
                pad.SetShape(k.PAD_SHAPE_RECT)
                pad.SetSize(k.VECTOR2I(700_000, 700_000))
                layers = k.LSET()
                layers.AddLayer(k.F_Cu)
                pad.SetLayerSet(layers)
                pad.SetPosition(k.VECTOR2I(x * 1_000_000, y * 1_000_000))
                pad.SetNet(nets[net])
                fp.Add(pad)
        for a, b in [((20, 20), (45, 20)), ((45, 20), (45, 40)), ((45, 40), (20, 40)), ((20, 40), (20, 20))]:
            line = k.PCB_SHAPE(board)
            line.SetShape(k.SHAPE_T_SEGMENT)
            line.SetStart(k.VECTOR2I(*(v * 1_000_000 for v in a)))
            line.SetEnd(k.VECTOR2I(*(v * 1_000_000 for v in b)))
            line.SetLayer(k.Edge_Cuts)
            line.SetWidth(50_000)
            board.Add(line)
        zone = k.ZONE(board)
        zone.SetLayer(k.B_Cu)
        zone.SetNet(nets['/return'])
        zone.SetPadConnection(k.ZONE_CONNECTION_FULL)
        polygon = zone.Outline()
        polygon.NewOutline()
        for x, y in [(20, 20), (45, 20), (45, 40), (20, 40)]:
            polygon.Append(x * 1_000_000, y * 1_000_000)
        board.Add(zone)
        self.save()

    def save(self):
        self.k.SaveBoard(str(self.path), self.board)

    def seed(self, doc=None):
        return local.seed(self.path, doc or contract(), clock=Clock(60))

    def filled(self):
        board = self.k.LoadBoard(str(self.path))
        self.k.ZONE_FILLER(board).Fill(board.Zones())
        self.k.SaveBoard(str(self.path), board)

    def test_declared_endpoints_generate_locked_legs_and_capacitor_plane_tie(self):
        receipt = self.seed()
        native = self.k.LoadBoard(str(self.path))
        tracks = list(native.GetTracks())
        self.assertEqual(sum(t.GetClass() == 'PCB_VIA' for t in tracks), 1)
        self.assertTrue(all(t.IsLocked() for t in tracks))
        self.assertEqual({t.GetNetname() for t in tracks}, {'/rail', '/return'})
        self.assertEqual(local.verify(self.path, receipt)['status'], 'pass')
        self.filled()
        result = measure(self.path, contract())
        self.assertEqual(result['outcome'], 'info', result)
        self.assertAlmostEqual(result['records'][0]['loop_length_mm'], 6)
        self.assertFalse(result['records'][0]['return']['pin_bypasses_capacitor'])

    def test_native_copper_save_preserves_authored_project_bytes(self):
        project = self.path.with_suffix('.kicad_pro')
        raw = b'{"meta":{"version":1},"net_settings":{"meta":{"version":3}}}\n'
        project.write_bytes(raw)
        self.seed()
        self.assertEqual(project.read_bytes(), raw)

    def test_broken_loop_fails_even_with_identical_pad_distances_and_nets(self):
        self.seed()
        board = self.k.LoadBoard(str(self.path))
        for track in list(board.GetTracks()):
            if track.GetNetname() == '/rail':
                board.Remove(track)
        self.k.SaveBoard(str(self.path), board)
        self.filled()
        result = measure(self.path, contract())
        self.assertEqual(result['outcome'], 'defect', result)
        self.assertIn('supply: no native-connected copper path', result['records'][0]['reasons'])

    def test_declared_limit_is_hard_and_unbounded_measurements_are_info(self):
        self.seed()
        self.filled()
        self.assertEqual(measure(self.path, contract())['outcome'], 'info')
        self.assertEqual(measure(self.path, contract(7))['outcome'], 'pass')
        result = measure(self.path, contract(5))
        self.assertEqual(result['outcome'], 'defect', result)
        self.assertEqual(result['records'][0]['basis'], local.BASIS)

    def test_final_candidate_validation_rejects_a_preserved_loop_over_its_limit(self):
        from unittest.mock import patch
        from agent_ee import placement_job as job
        from agent_ee.storage import digest, write_json
        self.path.with_suffix('.kicad_pro').write_text('{}')
        doc = contract(5)
        receipt = self.seed(doc)
        self.filled()
        native = self.k.LoadBoard(str(self.path))
        incumbent = dict(board=str(self.path), directory=str(self.root), board_sha256=digest(self.path))
        write_json(self.root / 'native-drc.json', dict(violations=[], unconnected_items=[]))
        normalized = job.normalize_intent(dict(fixed_poses=[], edge_connectors=[]),
            dict(groups=[dict(name='cell', anchor='IC', refs=['IC', 'BYP', 'OTHER'])]))
        with patch.object(job, 'drc', return_value=(dict(violations=[], unconnected_items=[]), {})), \
                patch.object(job, '_group_views', return_value={'status': 'rendered'}):
            final = job.final_checks(dict(status='routed', board=str(self.path), local_decoupling=receipt),
                incumbent, job._owners(native), set(), self.root / 'final', normalized)
        self.assertEqual(final['integrity'], 'preserved', final)
        self.assertEqual(final['eligibility'], 'INELIGIBLE', final)
        self.assertEqual(final['decoupling_loops']['outcome'], 'defect')

    def test_removed_locked_item_is_rejected(self):
        receipt = self.seed()
        board = self.k.LoadBoard(str(self.path))
        board.Remove(list(board.GetTracks())[0])
        self.k.SaveBoard(str(self.path), board)
        self.assertRaisesRegex(JobRefusal, 'protected copper missing', local.verify, self.path, receipt)

    def test_missing_capacitor_plane_tie_fails_even_when_both_surface_legs_remain(self):
        self.seed()
        board = self.k.LoadBoard(str(self.path))
        for item in list(board.GetTracks()):
            if item.GetClass() == 'PCB_VIA':
                board.Remove(item)
        self.k.SaveBoard(str(self.path), board)
        self.filled()
        result = measure(self.path, contract())
        self.assertEqual(result['outcome'], 'defect', result)
        self.assertIn('return: capacitor has no native plane connection without the device pin',
                      result['records'][0]['reasons'])

    def test_failed_local_copper_returns_an_ineligible_candidate_reason_before_routing(self):
        from unittest.mock import patch
        from agent_ee import placement_job as job
        (self.root / 'board.kicad_pro').write_text('{}')
        doc = contract()
        doc['relations'][0]['supply'][0]['reference'] = 'missing'
        fake = dict(violations=[], unconnected_items=[])
        with patch('agent_ee.routing_adapter.preflight', return_value={'problems': []}), \
                patch.object(job, 'drc', return_value=(fake, {})), \
                patch.object(job, '_route_attempt') as router:
            result = job.route(self.root, self.root / 'routing', self.root / 'unused-router',
                               budget_s=30, timeout_s=60, home=self.root / 'router-home', local_decoupling=doc)
        self.assertEqual(result['status'], 'local_copper_failed')
        self.assertIn('local decoupling cell: missing/ambiguous pad missing.1', result['reason'])
        router.assert_not_called()

    def test_other_plane_pads_get_their_own_optional_ties(self):
        doc = contract()
        doc['tie_other_pads'] = True
        receipt = self.seed(doc)
        self.assertEqual([r['status'] for r in receipt['other_pads']], ['seeded'])
        native = self.k.LoadBoard(str(self.path))
        self.assertEqual(sum(t.GetClass() == 'PCB_VIA' for t in native.GetTracks()), 2)

    def test_no_local_escape_names_the_relation(self):
        from unittest.mock import patch
        with patch('agent_ee.acquisition.local_return._obstacle_path', return_value=None):
            self.assertRaisesRegex(JobRefusal, 'local decoupling cell:', self.seed)

    def test_foreign_pad_obstacle_forces_a_detour(self):
        fp = next(fp for fp in self.board.GetFootprints() if fp.GetReference() == 'OTHER')
        obstacle = next(p for p in fp.Pads() if p.GetNumber() == '1')
        obstacle.SetNet(self.board.FindNet('/foreign'))
        obstacle.SetPosition(self.k.VECTOR2I(31_500_000, 29_000_000))
        self.save()
        self.seed()
        self.filled()
        result = measure(self.path, contract())
        self.assertEqual(result['outcome'], 'info', result)
        self.assertGreater(result['records'][0]['supply']['path']['length_mm'], 3)

    def test_copper_graphic_is_an_obstacle_to_the_local_search(self):
        line = self.k.PCB_SHAPE(self.board)
        line.SetShape(self.k.SHAPE_T_SEGMENT)
        line.SetLayer(self.k.F_Cu)
        line.SetStart(self.k.VECTOR2I(31_500_000, 28_500_000))
        line.SetEnd(self.k.VECTOR2I(31_500_000, 29_500_000))
        line.SetWidth(400_000)
        self.board.Add(line)
        self.save()
        self.seed()
        self.filled()
        result = measure(self.path, contract())
        self.assertEqual(result['outcome'], 'info', result)
        self.assertGreater(result['records'][0]['supply']['path']['length_mm'], 3)

    def test_plane_hop_pays_two_barrels_and_lateral_copper(self):
        # Replace the direct return with separate native ties and a real plane.
        doc = contract()
        self.seed(doc)
        board = self.k.LoadBoard(str(self.path))
        for t in list(board.GetTracks()):
            if t.GetNetname() == '/return':
                board.Remove(t)
        for endpoint in doc['relations'][0]['return']:
            local._tie(board, local.pad(board, endpoint), self.k.B_Cu, clock=Clock(60), cancel_dir=None)
        self.k.SaveBoard(str(self.path), board)
        self.filled()
        result = measure(self.path, doc)
        row = result['records'][0]
        self.assertEqual(result['outcome'], 'info', result)
        self.assertIsNone(row['return']['surface_path'])
        self.assertGreater(row['return']['path']['length_mm'], 3 + 2 * 1.2)
        self.assertGreater(row['return']['path']['plane_hop_mm'], 0)
        self.assertTrue(row['return']['pin_bypasses_capacitor'])

    def test_saved_back_copper_is_measured_with_the_plane(self):
        self.seed()
        board = self.k.LoadBoard(str(self.path))
        via = next(t for t in board.GetTracks() if t.GetClass() == 'PCB_VIA')
        x, y = local.position(via)
        local._track(board, [(x, y), (x + 2_000_000, y)], via.GetNetCode(), self.k.B_Cu, 400_000)
        self.k.SaveBoard(str(self.path), board)
        self.filled()
        result = measure(self.path, contract())
        self.assertEqual(result['outcome'], 'info', result)

    def test_same_net_disconnected_plane_islands_do_not_complete_a_loop(self):
        self.seed()
        board = self.k.LoadBoard(str(self.path))
        for track in list(board.GetTracks()):
            if track.GetNetname() == '/return':
                board.Remove(track)
        for zone in list(board.Zones()):
            board.Remove(zone)
        for endpoint in contract()['relations'][0]['return']:
            local._tie(board, local.pad(board, endpoint), self.k.B_Cu, clock=Clock(60), cancel_dir=None)
        for left, right in [(20, 31.7), (32.3, 45)]:
            zone = self.k.ZONE(board)
            zone.SetLayer(self.k.B_Cu)
            zone.SetNet(board.FindNet('/return'))
            zone.SetPadConnection(self.k.ZONE_CONNECTION_FULL)
            polygon = zone.Outline()
            polygon.NewOutline()
            for x, y in [(left, 20), (right, 20), (right, 40), (left, 40)]:
                polygon.Append(round(x * 1e6), round(y * 1e6))
            board.Add(zone)
        self.k.SaveBoard(str(self.path), board)
        self.filled()
        result = measure(self.path, contract())
        self.assertEqual(result['outcome'], 'defect', result)
        self.assertIn('return: no native-connected copper path', result['records'][0]['reasons'])


class ContractTests(unittest.TestCase):
    def test_malformed_limits_and_unknown_fields_refuse(self):
        for limit in (True, -1, 0, float('inf'), float('nan')):
            with self.subTest(limit=limit):
                self.assertRaises(ValueError, local.validate, contract(limit))
        doc = contract(5)
        doc['relations'][0]['basis'] = 'owner ruling'
        self.assertRaises(ValueError, local.validate, doc)


class PlaneGeometryTests(unittest.TestCase):
    def test_a_hole_cannot_be_a_zero_cost_plane_hop(self):
        from agent_ee.local_loop_geometry import plane_path, inside_segment
        poly = dict(outline=[(0, 0), (10, 0), (10, 10), (0, 10)],
                    holes=[[(4, 2), (6, 2), (6, 8), (4, 8)]])
        self.assertFalse(inside_segment((2, 5), (8, 5), poly))
        path = plane_path((2, 5), (8, 5), poly)
        self.assertGreater(sum(__import__('math').dist(a, b) for a, b in zip(path, path[1:])), 6)
