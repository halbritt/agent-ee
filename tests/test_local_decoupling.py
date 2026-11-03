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
        board.GetDesignSettings().m_MinClearance = 100_000
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
        from agent_ee.storage import write_json
        cls = self.board.GetAllNetClasses()['Default']
        write_json(self.path.with_suffix('.kicad_pro'),dict(
            board=dict(design_settings=dict(rules=dict(min_clearance=self.board.GetDesignSettings().m_MinClearance/1e6))),
            meta=dict(filename=self.path.with_suffix('.kicad_pro').name,version=1),
            net_settings=dict(classes=[dict(name='Default',clearance=cls.GetClearance()/1e6,
                track_width=cls.GetTrackWidth()/1e6,via_diameter=cls.GetViaDiameter()/1e6,
                via_drill=cls.GetViaDrill()/1e6)],meta=dict(version=3))))

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
        self.assertEqual(result['banks'][0]['relations'], ['cell'])
        self.assertFalse(result['banks'][0]['return']['pin_bypasses_bank'])
        self.assertTrue(result['banks'][0]['device_return_reaches_plane_only_through_bank_capacitors'])

    def two_capacitors(self):
        from copy import deepcopy
        doc = contract()
        second = deepcopy(doc['relations'][0])
        second['id'] = 'bulk'
        for leg in ('supply', 'return'):
            second[leg][1]['reference'] = 'OTHER'
        doc['relations'].append(second)
        return doc

    def test_two_capacitor_bank_passes_with_a_separate_entry_for_each_capacitor(self):
        from agent_ee.decoupling_loops import CopperGraph
        doc = self.two_capacitors()
        receipt = self.seed(doc)
        self.filled()
        result = measure(self.path, doc)
        self.assertEqual(result['outcome'], 'info', result)
        self.assertEqual(len(result['records']), 2)
        self.assertEqual([r['bank'] for r in result['records']], ['bank-1', 'bank-1'])
        for row in result['records']:
            self.assertGreater(row['supply']['path']['length_mm'], 0)
            self.assertGreater(row['return']['path']['length_mm'], 0)
            self.assertAlmostEqual(row['loop_length_mm'], sum(row[leg]['path']['length_mm'] for leg in ('supply', 'return')))
            self.assertNotIn('pin_bypasses_capacitor', row['return'])
        bank = result['banks'][0]
        self.assertEqual(bank['relations'], ['cell', 'bulk'])
        self.assertFalse(bank['return']['pin_bypasses_bank'], bank)
        self.assertTrue(bank['device_return_reaches_plane_only_through_bank_capacitors'], bank)
        self.assertEqual(local.verify(self.path, receipt)['geometry']['generated_vias'], 2)
        board = self.k.LoadBoard(str(self.path))
        board.BuildConnectivity()
        graph = CopperGraph(board, '/return')
        # Removing only one cap still leaves the other cap's legitimate entry.
        self.assertTrue(graph.bypass(local.pad(board, doc['relations'][0]['return'][0]),
                                     local.pad(board, doc['relations'][0]['return'][1])))
        checked = self.checked(doc)
        self.assertEqual((checked['execution'], checked['outcome']), ('ok', 'pass'), checked)
        self.assertFalse(checked['details']['banks'][0]['return']['pin_bypasses_bank'])

    def test_router_device_pin_via_skips_every_capacitor_in_the_bank(self):
        doc = self.two_capacitors()
        self.seed(doc)
        board = self.k.LoadBoard(str(self.path))
        device = local.pad(board, doc['relations'][0]['return'][0])
        via = self.k.PCB_VIA(board)
        via.SetPosition(device.GetPosition())
        via.SetWidth(600_000)
        via.SetDrill(300_000)
        via.SetViaType(self.k.VIATYPE_THROUGH)
        via.SetLayerPair(self.k.F_Cu, self.k.B_Cu)
        via.SetNetCode(device.GetNetCode())
        board.Add(via)
        uid = via.m_Uuid.AsString()
        self.k.SaveBoard(str(self.path), board)
        self.filled()
        result = self.checked(doc)
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        bank = result['details']['banks'][0]
        self.assertTrue(bank['return']['pin_bypasses_bank'], bank)
        self.assertFalse(bank['device_return_reaches_plane_only_through_bank_capacitors'], bank)
        self.assertIn(uid, [p['plane_entry'] for d in bank['return']['devices'] for p in d['bypass_paths']])

    def test_native_copper_save_preserves_authored_project_bytes(self):
        project = self.path.with_suffix('.kicad_pro')
        raw = b'{"board":{"design_settings":{"rules":{"min_clearance":0.1}}},"meta":{"version":1},"net_settings":{"meta":{"version":3}}}\n'
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
        self.save()
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
        # No length failure can mask the protected-copper check in this arm.
        receipt['contract'] = contract()
        native = self.k.LoadBoard(str(self.path))
        track = next(t for t in native.GetTracks() if t.GetClass() == 'PCB_TRACK')
        track.SetWidth(track.GetWidth() + 10_000)
        self.k.SaveBoard(str(self.path), native)
        routed = self.root / 'changed.kicad_pcb'
        import shutil
        shutil.copyfile(self.path, routed)
        shutil.copyfile(self.path.with_suffix('.kicad_pro'), routed.with_suffix('.kicad_pro'))
        with patch.object(job, 'drc', return_value=(dict(violations=[], unconnected_items=[]), {})), \
                patch.object(job, '_group_views', return_value={'status': 'rendered'}):
            final = job.final_checks(dict(status='routed', board=str(routed), local_decoupling=receipt),
                incumbent, job._owners(native), set(), self.root / 'changed-final', normalized)
        self.assertEqual(final['eligibility'], 'INELIGIBLE', final)
        self.assertTrue(any('protected copper missing/changed' in p for p in final['identity_problems']), final)

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
        self.save()
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

    def test_mirrored_cell_places_plane_entry_beyond_capacitor(self):
        for fp in self.board.GetFootprints():
            if fp.GetReference() in ('IC', 'BYP'):
                x = 33 if fp.GetReference() == 'IC' else 30
                for p in fp.Pads():
                    p.SetPosition(self.k.VECTOR2I(x * 1_000_000, p.GetPosition().y))
        self.save()
        self.seed()
        self.filled()
        row = measure(self.path, contract())['records'][0]
        self.assertFalse(row['return']['pin_bypasses_capacitor'], row)
        native = self.k.LoadBoard(str(self.path))
        via = next(t for t in native.GetTracks() if t.GetClass() == 'PCB_VIA')
        self.assertLess(via.GetPosition().x, 30_000_000)

    def test_same_net_pad_is_an_obstacle_for_tracks_and_vias(self):
        other = next(fp for fp in self.board.GetFootprints() if fp.GetReference() == 'OTHER')
        p = next(p for p in other.Pads() if p.GetNumber() == '2')
        p.SetPosition(self.k.VECTOR2I(34_400_000, 31_000_000))
        rail = next(p for p in other.Pads() if p.GetNumber() == '1')
        rail.SetPosition(self.k.VECTOR2I(31_500_000, 29_000_000))
        self.save()
        receipt = self.seed()
        self.assertEqual(local.verify(self.path, receipt)['geometry']['status'], 'pass')
        self.filled()
        self.assertGreater(measure(self.path, contract())['records'][0]['supply']['path']['length_mm'], 3)

    def test_foreign_pad_blocks_the_preferred_via_site(self):
        other = next(fp for fp in self.board.GetFootprints() if fp.GetReference() == 'OTHER')
        p = next(p for p in other.Pads() if p.GetNumber() == '2')
        p.SetNet(self.board.FindNet('/foreign'))
        p.SetPosition(self.k.VECTOR2I(34_000_000, 31_000_000))
        layers = self.k.LSET()
        layers.AddLayer(self.k.B_Cu)
        p.SetLayerSet(layers)
        self.save()
        receipt = self.seed()
        native = self.k.LoadBoard(str(self.path))
        via = next(t for t in native.GetTracks() if t.GetClass() == 'PCB_VIA')
        self.assertNotEqual(local.position(via), (34_000_000, 31_000_000))
        self.assertEqual(local.verify(self.path, receipt)['status'], 'pass')

    def test_via_site_keeps_the_board_edge_margin(self):
        for line in self.board.GetDrawings():
            for method in ('Start', 'End'):
                point = getattr(line, 'Get' + method)()
                if point.x == 45_000_000:
                    getattr(line, 'Set' + method)(self.k.VECTOR2I(34_500_000, point.y))
        self.save()
        self.seed()
        native = self.k.LoadBoard(str(self.path))
        via = next(t for t in native.GetTracks() if t.GetClass() == 'PCB_VIA')
        margin = local._rules(native, via)['clearance'] + via.GetWidth(self.k.PADSTACK.ALL_LAYERS) // 2 + 25_000
        self.assertLess(via.GetPosition().x, native.GetBoardEdgesBoundingBox().GetRight() - margin)

    def test_tracks_use_effective_class_width_and_are_not_duplicated(self):
        doc = contract()
        import copy
        second = copy.deepcopy(doc['relations'][0])
        second['id'] = 'second'
        # Sharing a supply segment is valid; both returns select the same capacitor.
        second['return'][0]['reference'] = 'OTHER'
        doc['relations'].append(second)
        other = next(fp for fp in self.board.GetFootprints() if fp.GetReference() == 'OTHER')
        next(p for p in other.Pads() if p.GetNumber() == '2').SetPosition(self.k.VECTOR2I(30_000_000,33_000_000))
        self.save()
        self.seed(doc)
        native = self.k.LoadBoard(str(self.path))
        identities = []
        for t in native.GetTracks():
            if t.GetClass() == 'PCB_TRACK':
                self.assertEqual(t.GetWidth(), 400_000)
                identities.append((t.GetNetCode(), t.GetLayer(), tuple(sorted((local.position_point(t.GetStart()), local.position_point(t.GetEnd()))))))
        self.assertEqual(len(identities), len(set(identities)))

    def test_geometry_rejects_via_pad_clearance_and_undeclared_pad_contacts(self):
        from copy import deepcopy
        receipt = self.seed()
        board = self.k.LoadBoard(str(self.path))
        via = next(t for t in board.GetTracks() if t.GetClass() == 'PCB_VIA')
        p = local.pad(board, contract()['relations'][0]['return'][1])
        via.SetPosition(p.GetPosition())
        changed = deepcopy(receipt)
        changed['protected'] = [local.copper_identity(t) for t in board.GetTracks()]
        self.k.SaveBoard(str(self.path), board)
        self.assertRaisesRegex(JobRefusal, 'pad clearance', local.verify, self.path, changed)
        # Restore the via and move an unrelated same-net land onto the supply leg.
        via.SetPosition(self.k.VECTOR2I(34_000_000, 31_000_000))
        other = next(fp for fp in board.GetFootprints() if fp.GetReference() == 'OTHER')
        p = next(p for p in other.Pads() if p.GetNumber() == '1')
        p.SetPosition(self.k.VECTOR2I(31_500_000, 29_000_000))
        changed['protected'] = [local.copper_identity(t) for t in board.GetTracks()]
        self.k.SaveBoard(str(self.path), board)
        self.assertRaisesRegex(JobRefusal, 'pad clearance', local.verify, self.path, changed)

    def test_changed_locked_item_geometry_is_rejected(self):
        receipt = self.seed()
        board = self.k.LoadBoard(str(self.path))
        track = next(t for t in board.GetTracks() if t.GetClass() == 'PCB_TRACK')
        track.SetWidth(track.GetWidth() + 10_000)
        self.k.SaveBoard(str(self.path), board)
        self.assertRaisesRegex(JobRefusal, 'protected copper missing/changed', local.verify, self.path, receipt)

    def test_plated_capacitor_pad_is_its_own_plane_entry(self):
        p = local.pad(self.board, contract()['relations'][0]['return'][1])
        p.SetAttribute(self.k.PAD_ATTRIB_PTH)
        p.SetDrillSize(self.k.VECTOR2I(300_000, 300_000))
        p.SetLayerSet(self.k.LSET.AllCuMask())
        self.save()
        self.seed()
        self.filled()
        native = self.k.LoadBoard(str(self.path))
        self.assertFalse(any(t.GetClass() == 'PCB_VIA' for t in native.GetTracks()))
        row = measure(self.path, contract())['records'][0]
        self.assertEqual(row['outcome'], 'info', row)
        self.assertFalse(row['return']['pin_bypasses_capacitor'], row)

    def test_plated_device_pad_and_back_capacitor_share_a_copper_layer(self):
        for p in next(fp for fp in self.board.GetFootprints() if fp.GetReference() == 'IC').Pads():
            p.SetAttribute(self.k.PAD_ATTRIB_PTH)
            p.SetDrillSize(self.k.VECTOR2I(300_000, 300_000))
            p.SetLayerSet(self.k.LSET.AllCuMask())
        for p in next(fp for fp in self.board.GetFootprints() if fp.GetReference() == 'BYP').Pads():
            layers = self.k.LSET()
            layers.AddLayer(self.k.B_Cu)
            p.SetLayerSet(layers)
        self.save()
        self.seed()
        self.filled()
        result = measure(self.path, contract())
        self.assertEqual(result['outcome'], 'info', result)
        native = self.k.LoadBoard(str(self.path))
        self.assertTrue(all(t.GetLayer() == self.k.B_Cu for t in native.GetTracks()))

    def checked(self, doc, *, wrong_hash=False):
        """Run the registered handler and its native CLI, with only mount paths adapted for the image test."""
        import json
        import shutil
        import subprocess
        import time
        from unittest.mock import patch
        from agent_ee.checks import Ctx
        from agent_ee.decoupling_loops import check
        from agent_ee.storage import digest, write_json
        run = self.root / ('check-' + str(len(list(self.root.glob('check-*')))))
        inputs = run / 'inputs'
        inputs.mkdir(parents=True)
        shutil.copyfile(self.path, inputs / 'board.kicad_pcb')
        write_json(inputs / 'local.json', doc)
        manifest = dict(candidate_id='native-fixture', scope=dict(board='board.kicad_pcb'),
                        files={'board.kicad_pcb': '0' * 64 if wrong_hash else digest(inputs / 'board.kicad_pcb')})
        ctx = Ctx(run, manifest, dict(id='loops', kind='decoupling_loops', params=dict(contract='local.json')),
                  time.monotonic() + 120)
        def execute(context, argv, timeout, **kwargs):
            argv = [arg.replace('/candidate/', str(inputs) + '/').replace('/work/', str(context.work) + '/') for arg in argv]
            subprocess.run(argv, check=True, timeout=timeout, capture_output=True)
        with patch.object(Ctx, 'execute', execute):
            return ctx.evaluate(check)

    def test_check_native_pass_info_defect_unknown_and_board_hash_binding(self):
        self.seed()
        # An unfilled zone must remain unknown, even if both legs are connected.
        unknown = self.checked(contract())
        self.assertEqual((unknown['execution'], unknown['outcome']), ('ok', 'unknown'), unknown)
        self.assertIn('unfilled zone', str(unknown['details']))
        self.filled()
        for limit, expected in ((None, 'info'), (7, 'pass'), (5, 'defect')):
            with self.subTest(limit=limit):
                result = self.checked(contract(limit))
                self.assertEqual(result['execution'], 'ok', result)
                self.assertEqual(result['outcome'], 'pass' if expected == 'info' else expected, result)
                self.assertEqual(result['details']['outcome'], expected, result)
                self.assertTrue(result['evidence'])
        mismatch = self.checked(contract(), wrong_hash=True)
        self.assertEqual(mismatch['execution'], 'malformed', mismatch)
        self.assertEqual(mismatch['outcome'], 'unknown', mismatch)

    def test_common_pad_does_not_hide_a_track_via_junction_outside_it(self):
        self.seed()
        board = self.k.LoadBoard(str(self.path))
        cap = local.pad(board, contract()['relations'][0]['return'][1])
        # This return track touches the cap AND the tie via outside the pad.
        via = next(t for t in board.GetTracks() if t.GetClass() == 'PCB_VIA')
        via.SetPosition(self.k.VECTOR2I(33_500_000, 31_000_000))
        local._track(board, [(30_000_000, 31_000_000), local.position(via)], cap.GetNetCode(), self.k.F_Cu, 400_000)
        self.k.SaveBoard(str(self.path), board)
        self.filled()
        row = measure(self.path, contract())['records'][0]
        self.assertTrue(row['return']['pin_bypasses_capacitor'], row)
        self.assertTrue(row['return']['bypass_paths'])

    def test_seed_drc_rejection_names_only_the_implicated_relation(self):
        from copy import deepcopy
        from unittest.mock import patch
        from agent_ee import placement_job as job
        self.save()
        doc = contract()
        other = deepcopy(doc['relations'][0])
        other['id'] = 'innocent'
        fp = self.k.FOOTPRINT(self.board)
        fp.SetReference('SECOND')
        self.board.Add(fp)
        for number, y, net in [('1', 29, '/rail'), ('2', 31, '/return')]:
            p = self.k.PAD(fp)
            p.SetNumber(number)
            p.SetAttribute(self.k.PAD_ATTRIB_SMD)
            p.SetShape(self.k.PAD_SHAPE_RECT)
            p.SetSize(self.k.VECTOR2I(700_000, 700_000))
            layers = self.k.LSET()
            layers.AddLayer(self.k.F_Cu)
            p.SetLayerSet(layers)
            p.SetPosition(self.k.VECTOR2I(41_000_000, y * 1_000_000))
            p.SetNet(self.board.FindNet(net))
            fp.Add(p)
        self.save()
        for leg in ('supply', 'return'):
            other[leg][0]['reference'] = 'OTHER'
            other[leg][1]['reference'] = 'SECOND'
        doc['relations'].append(other)
        fake = dict(violations=[], unconnected_items=[])
        def seeded_drc(path, directory, **kwargs):
            if Path(directory).name == 'before':
                return fake, {}
            native = self.k.LoadBoard(str(path))
            uid = next(t.m_Uuid.AsString() for t in native.GetTracks() if t.GetClass() == 'PCB_VIA' and t.GetPosition().x < 36_000_000)
            return dict(violations=[dict(type='clearance', severity='error', items=[dict(uuid=uid)])], unconnected_items=[]), {}
        with patch('agent_ee.routing_adapter.preflight', return_value={'problems': []}), \
                patch.object(job, 'drc', side_effect=seeded_drc), patch.object(job, '_route_attempt') as router:
            result = job.route(self.root, self.root / 'routing', self.root / 'unused-router',
                               budget_s=30, timeout_s=60, home=self.root / 'router-home', local_decoupling=doc)
        self.assertEqual(result['status'], 'local_copper_failed', result)
        self.assertIn('cell: native DRC rejects', result['reason'])
        self.assertNotIn('innocent', result['reason'])
        router.assert_not_called()


class ReviewRegressionTests(unittest.TestCase):
    setUp = LocalCopperTests.setUp
    save = LocalCopperTests.save
    seed = LocalCopperTests.seed
    filled = LocalCopperTests.filled
    checked = LocalCopperTests.checked

    def add_fp(self, ref, pads):
        fp = self.k.FOOTPRINT(self.board)
        fp.SetReference(ref)
        self.board.Add(fp)
        for number, x, y, net in pads:
            self.add_pad(fp, number, x, y, net)
        return fp

    def add_pad(self, fp, number, x, y, net, size=700_000):
        p = self.k.PAD(fp)
        p.SetNumber(str(number))
        p.SetAttribute(self.k.PAD_ATTRIB_SMD)
        p.SetShape(self.k.PAD_SHAPE_RECT)
        p.SetSize(self.k.VECTOR2I(size, size))
        layers = self.k.LSET()
        layers.AddLayer(self.k.F_Cu)
        p.SetLayerSet(layers)
        p.SetPosition(self.k.VECTOR2I(round(x*1e6), round(y*1e6)))
        if net:
            p.SetNet(self.board.FindNet(net))
        fp.Add(p)
        return p

    def add_far_pads(self, count, net):
        fp = self.add_fp('FAR', [])
        for n in range(count):
            i, j = divmod(n, 73)
            self.add_pad(fp, n+1, 21+i*.25, 21+j*.25, net, 100_000)

    def test_junction_opening_keeps_the_floor_beyond_one_floor_of_the_pad(self):
        # Reviewer C1Narrow: a shallow in-pad junction cannot exempt its whole pair.
        import math
        from copy import deepcopy
        for role in ('escape', 'other-bank'):
            for land, angle, accepted in ((1_200_000,20,False),(200_000,90,True),
                                         (250_000,120,True),(300_000,150,True)):
                with self.subTest(role=role,land=land,angle=angle):
                    self.setUp();k=self.k;doc=contract();net=self.board.FindNet('/return').GetNetCode()
                    cap=local.pad(self.board,doc['relations'][0]['return'][1])
                    cap.SetPosition(k.VECTOR2I(36_000_000,25_000_000));cap.SetSize(k.VECTOR2I(land,land))
                    device=local.pad(self.board,doc['relations'][0]['return'][0]);device.SetPosition(k.VECTOR2I(39_000_000,25_000_000))
                    c=local.position(cap);a=local._track(self.board,[local.position(device),c],net,k.F_Cu,200_000)[0]
                    length=1_500_000 if role=='escape' else 3_000_000
                    end=(c[0]+round(length*math.cos(math.radians(angle))),c[1]+round(length*math.sin(math.radians(angle))))
                    b=local._track(self.board,[c,end],net,k.F_Cu,200_000)[0]
                    ids=lambda items:[t.m_Uuid.AsString() for t in items]
                    first=dict(id='cell',copper=ids([a]),legs=[dict(kind='return',tracks=ids([a]),pads=ids([device,cap]))])
                    rows=[first]
                    if role=='escape':
                        first['copper']+=ids([b]);first['legs'].append(dict(kind='plane_tie',tracks=ids([b]),pads=ids([cap])))
                    else:
                        fp=self.add_fp('DEV2',[]);other=self.add_pad(fp,'2',end[0]/1e6,end[1]/1e6,'/return',500_000)
                        self.add_pad(fp,'1',26,35,'/rail',500_000)
                        relation=deepcopy(doc['relations'][0]);relation['id']='other-bank'
                        for kind in ('supply','return'):relation[kind][0]['reference']='DEV2'
                        doc['relations'].append(relation)
                        rows.append(dict(id='other-bank',copper=ids([b]),legs=[dict(kind='return',tracks=ids([b]),pads=ids([other,cap]))]))
                        self.assertEqual(len(local.banks(doc)),2)
                    receipt=dict(contract=doc,relations=rows,protected=[local.copper_identity(t) for t in (a,b)],other_pads=[])
                    self.assertTrue(local.junction_in_pad(a,b,k.F_Cu,[cap]))
                    if accepted:self.assertEqual(local.verify_geometry(self.board,receipt)['status'],'pass')
                    else:self.assertRaisesRegex(JobRefusal,'outside a declared shared pad',local.verify_geometry,self.board,receipt)
                    self.board.Remove(b)
                    clear=local._clear_tie_path(self.board,[c,end],cap,k.F_Cu,200_000)
                    self.board.Add(b)
                    self.assertEqual(clear,accepted)
                    self.save();report=local.separation_report(self.path,receipt)
                    row=next(r for r in report['pairs'] if set(r['items'])==set(ids([a,b])))
                    self.assertIsNotNone(row['minimum_gap_mm_lower_bound'])
                    self.assertEqual(row['status'],'pass' if accepted else 'defect')
                    self.assertEqual(report['outcome'],'pass' if accepted else 'defect')
                    if accepted:self.assertGreaterEqual(row['minimum_gap_mm_lower_bound'],.1)
                    else:self.assertLess(row['minimum_gap_mm_lower_bound'],.1)
                    self.doCleanups()

    def test_geometry_rejection_retries_before_saving_the_nearest_board(self):
        from unittest.mock import patch
        real=local._seed_copper;invalid=[]
        def attempt(board,doc,**kwargs):
            rows,protected,other=real(board,doc,**kwargs)
            if kwargs['order']=='nearest-first':
                # Native copper through undeclared OTHER.1 is a real guard defect.
                extra=local._track(board,[(37_000_000,29_000_000),(39_000_000,29_000_000)],
                                   board.FindNet('/rail').GetNetCode(),self.k.F_Cu,200_000)
                invalid.extend(t.m_Uuid.AsString() for t in extra)
                rows[0]['copper']+=invalid;rows[0]['legs'][0]['tracks']+=invalid
                protected.extend(local.copper_identity(t) for t in extra)
            return rows,protected,other
        with patch.object(local,'_seed_copper',side_effect=attempt):receipt=self.seed()
        self.assertEqual(receipt['generation_order']['selected'],'document')
        self.assertIn('pad clearance OTHER.1',receipt['generation_order']['attempts'][0]['reason'])
        board=self.k.LoadBoard(str(self.path));actual={t.m_Uuid.AsString() for t in board.GetTracks()}
        self.assertFalse(actual.intersection(invalid))
        self.assertEqual(local.verify(self.path,receipt)['status'],'pass')

    def test_cross_bank_device_legs_search_around_each_other(self):
        from copy import deepcopy
        self.add_fp('DEV2',[('1',26,35,'/rail'),('2',31.5,29.5,'/return')])
        self.add_fp('CAP2',[('1',27,35,'/rail'),('2',31.5,32.5,'/return')])
        doc=contract();doc['planes']=[]
        relation=deepcopy(doc['relations'][0]);relation['id']='other-bank'
        for kind in ('supply','return'):
            relation[kind][0]['reference']='DEV2';relation[kind][1]['reference']='CAP2'
        doc['relations'].append(relation);self.save();receipt=self.seed(doc)
        self.assertEqual(len(local.banks(doc)),2)
        self.assertEqual(local.verify(self.path,receipt)['status'],'pass')
        self.filled();result=measure(self.path,doc)
        self.assertEqual(result['outcome'],'info',result)
        self.assertTrue(all(row['return']['surface_path'] for row in result['records']))

    def test_tiny_lands_allow_the_two_arms_of_an_in_pad_junction(self):
        import math
        k=self.k;net=self.board.FindNet('/return').GetNetCode()
        for land in (200_000,250_000,300_000,500_000):
            fp=self.add_fp('LAND'+str(land),[]);p=self.add_pad(fp,'1',36,25,'/return',land)
            for angle in (180,150,120,90):
                with self.subTest(land=land,angle=angle):
                    c=(36_000_000,25_000_000)
                    end=(c[0]+round(1_000_000*math.cos(math.radians(angle))),c[1]-round(1_000_000*math.sin(math.radians(angle))))
                    a=local._track(self.board,[c,(37_000_000,25_000_000)],net,k.F_Cu,200_000)[0]
                    b=local._track(self.board,[c,end],net,k.F_Cu,200_000)[0]
                    for gap in (1,100_000,125_000):self.assertFalse(local._too_close(a,b,k.F_Cu,[p],gap))
                    self.board.Remove(a);self.board.Remove(b)
            self.board.Remove(fp)

    def test_a_shared_junction_does_not_excuse_overlap_outside_its_pad(self):
        import math
        fp=self.add_fp('LAND',[]);p=self.add_pad(fp,'1',36,25,'/return',200_000)
        c=(36_000_000,25_000_000);net=p.GetNetCode()
        a=local._track(self.board,[c,(37_000_000,25_000_000)],net,self.k.F_Cu,200_000)[0]
        b=local._track(self.board,[c,(c[0]+round(1_000_000*math.cos(math.radians(80))),c[1]-round(1_000_000*math.sin(math.radians(80))))],net,self.k.F_Cu,200_000)[0]
        self.assertFalse(local.junction_in_pad(a,b,self.k.F_Cu,[p]))
        self.assertTrue(local._too_close(a,b,self.k.F_Cu,[p],100_000))

    def test_polygon_junction_agrees_with_typed_bank_on_reviewer_seed2_tracks(self):
        from agent_ee import local_loop_geometry as geo
        k=self.k;net=self.board.FindNet('/return').GetNetCode()
        cap=local.pad(self.board,contract()['relations'][0]['return'][1])
        cap.SetPosition(k.VECTOR2I(28_640_000,29_290_000));cap.SetSize(k.VECTOR2I(600_000,600_000))
        returned=local._track(self.board,[(30_000_000,31_000_000),(28_739_999,29_625_001),(28_640_000,29_290_000)],net,k.F_Cu,400_000)
        escaped=local._track(self.board,[(28_640_000,29_290_000),(28_014_999,29_190_001),(27_200_000,28_500_000)],net,k.F_Cu,400_000)
        via=k.PCB_VIA(self.board);via.SetPosition(k.VECTOR2I(27_200_000,28_500_000));via.SetWidth(600_000);via.SetDrill(300_000)
        via.SetLayerPair(k.F_Cu,k.B_Cu);via.SetNetCode(net);via.SetLocked(True);self.board.Add(via);escaped.append(via)
        supply=local._track(self.board,[(30_000_000,29_000_000),(33_000_000,29_000_000)],self.board.FindNet('/rail').GetNetCode(),k.F_Cu,400_000)
        a,b=geo.closest((local.position_point(returned[0].GetStart()),local.position_point(returned[0].GetEnd())),
                        (local.position_point(escaped[0].GetStart()),local.position_point(escaped[0].GetEnd())))
        self.assertFalse(all(cap.GetEffectiveShape(k.F_Cu).Collide(k.VECTOR2I(round(x),round(y)),0) for x,y in (a,b)))
        self.assertTrue(local.junction_in_pad(returned[0],escaped[0],k.F_Cu,[cap]))
        doc=contract();legs=[]
        for kind,items,endpoints in [('supply',supply,doc['relations'][0]['supply']),('return',returned,doc['relations'][0]['return']),('plane_tie',escaped,doc['relations'][0]['return'][1:])]:
            legs.append(dict(kind=kind,tracks=[t.m_Uuid.AsString() for t in items],pads=[local.pad(self.board,p).m_Uuid.AsString() for p in endpoints]))
        items=supply+returned+escaped;receipt=dict(contract=doc,relations=[dict(id='cell',copper=[t.m_Uuid.AsString() for t in items],legs=legs)],protected=[local.copper_identity(t) for t in items],other_pads=[])
        self.assertEqual(local.verify_geometry(self.board,receipt)['status'],'pass')
        self.save();self.filled();result=measure(self.path,doc)
        self.assertEqual(result['outcome'],'info',result)
        self.assertFalse(result['banks'][0]['return']['pin_bypasses_bank'],result)

    def test_undeclared_same_net_pad_keeps_the_native_floor_in_guard_and_report(self):
        fp=self.add_fp('NEAR',[]);p=self.add_pad(fp,'1',31.5,31.4,'/return',300_000)
        items=local._track(self.board,[(30_000_000,31_000_000),(33_000_000,31_000_000)],p.GetNetCode(),self.k.F_Cu,400_000)
        doc=contract();ids=[t.m_Uuid.AsString() for t in items]
        receipt=dict(contract=doc,relations=[dict(id='cell',copper=ids,legs=[dict(kind='return',tracks=ids,pads=[local.pad(self.board,e).m_Uuid.AsString() for e in doc['relations'][0]['return']])])],protected=[local.copper_identity(t) for t in items],other_pads=[])
        self.assertRaisesRegex(JobRefusal,'pad clearance NEAR.1',local.verify_geometry,self.board,receipt)
        self.save();report=local.separation_report(self.path,receipt)
        row=next(r for r in report['pad_pairs'] if r['pad']=='NEAR.1')
        self.assertEqual(row['status'],'defect');self.assertAlmostEqual(row['minimum_gap_mm_lower_bound'],.05,places=5)
        self.assertEqual(report['outcome'],'defect')

    def test_missing_or_zero_clearance_without_a_bound_profile_refuses_local_copper(self):
        self.board.GetDesignSettings().m_MinClearance=0
        self.path.with_suffix('.kicad_pro').unlink(missing_ok=True)
        self.assertRaisesRegex(JobRefusal,'nonzero authored minimum',local._separation_floor,self.board)
        self.board.GetDesignSettings().m_MinClearance=100_000
        self.assertEqual(local._separation_floor(self.board),100_000)

    def test_job_refuses_an_unknown_floor_before_placement_search(self):
        import json
        from unittest.mock import patch
        from agent_ee import placement_job as job
        self.board.GetDesignSettings().m_MinClearance=0;self.save()
        self.path.with_suffix('.kicad_pro').write_text('{}')
        doc=contract();doc['planes']=[]
        intent=dict(fixed_poses=[],edge_connectors=[])
        groups=dict(groups=[dict(name='cell',anchor='IC',refs=['IC','BYP','OTHER'])])
        inputs=dict(board=(self.path,self.path.read_bytes()),project=(self.path.with_suffix('.kicad_pro'),b'{}'),
                    intent=(self.path,json.dumps(intent).encode()),groups=(self.path,json.dumps(groups).encode()),
                    local_decoupling=(self.path,json.dumps(doc).encode()))
        config=dict(budget_s=60,inputs={key:{} for key in inputs},search=dict(shortlist=1),router=dict(checkout='unused'))
        with patch.object(job,'verified_input',side_effect=lambda entry,label:inputs[label]), \
             patch.object(job,'capture') as capture,patch.object(job,'solve') as solve:
            report=job.run_job(config,self.root/'unknown-floor-job')
        self.assertEqual(report['status'],'refused',report)
        self.assertIn('nonzero authored minimum',report['reason'])
        capture.assert_not_called();solve.assert_not_called()

    def test_tie_path_rejects_a_subfloor_near_miss_to_another_leg(self):
        net=self.board.FindNet('/return').GetNetCode()
        local._track(self.board,[(34_000_000,33_000_000),(36_000_000,33_000_000)],net,self.k.F_Cu,200_000)
        terminal=local.pad(self.board,contract()['relations'][0]['return'][1])
        self.assertFalse(local._clear_tie_path(self.board,[(34_000_000,33_250_000),(36_000_000,33_250_000)],terminal,self.k.F_Cu,200_000))

    def test_via_collection_includes_an_obstacle_reached_only_by_its_ring(self):
        terminal=local.pad(self.board,contract()['relations'][0]['return'][1]);rules=local._rules(self.board,terminal)
        via=self.k.PCB_VIA(self.board);via.SetPosition(self.k.VECTOR2I(35_000_000,31_000_000));via.SetWidth(rules['diameter']);via.SetDrill(rules['drill'])
        via.SetLayerPair(self.k.F_Cu,self.k.B_Cu);via.SetNetCode(terminal.GetNetCode())
        fp=self.add_fp('RING',[]);obstacle=self.add_pad(fp,'1',35.55,31,'/foreign',300_000)
        gathered=local._via_obstacles(self.board,terminal,rules)
        self.assertIn(obstacle.m_Uuid.AsString(),{p.m_Uuid.AsString() for p in gathered[0]})
        self.assertTrue(local._via_site_blocked(self.board,via,terminal,rules,gathered))

    def test_reviewer_seed1_four_capacitor_bank_records_the_order_that_succeeds(self):
        from copy import deepcopy
        caps=[('C1',(27.41,32.86),(27.41,34.06)),('C2',(31.2,29.87),(31.2,31.67)),('C3',(28.2,30.16),(28.2,31.66))]
        for ref,a,b in caps:
            fp=self.add_fp(ref,[])
            self.add_pad(fp,'1',*a,'/rail',600_000);self.add_pad(fp,'2',*b,'/return',600_000)
        doc=contract();relations=[]
        for ref,_,_ in reversed(caps):
            relation=deepcopy(doc['relations'][0]);relation['id']='far-'+ref
            for kind in ('supply','return'):relation[kind][1]['reference']=ref
            relations.append(relation)
        doc['relations']=relations+doc['relations'];self.save();receipt=self.seed(doc)
        self.assertIn(receipt['generation_order']['selected'],('nearest-first','document'))
        self.assertEqual(receipt['generation_order']['attempts'][-1]['status'],'seeded')
        self.assertEqual(local.verify(self.path,receipt)['status'],'pass')
        self.filled();self.assertFalse(measure(self.path,doc)['banks'][0]['return']['pin_bypasses_bank'])

    def test_failed_nearest_order_leaves_no_copper_in_the_document_retry(self):
        from unittest.mock import patch
        real=local._seed_copper
        def attempt(board,doc,**kwargs):
            result=real(board,doc,**kwargs)
            if kwargs['order']=='nearest-first':raise JobRefusal('forced refusal after constructing copper')
            return result
        with patch.object(local,'_seed_copper',side_effect=attempt):receipt=self.seed()
        self.assertEqual(receipt['generation_order']['selected'],'document')
        self.assertEqual([r['status'] for r in receipt['generation_order']['attempts']],['refused','seeded'])
        native=self.k.LoadBoard(str(self.path))
        self.assertEqual({t.m_Uuid.AsString() for t in native.GetTracks()},{r['uuid'] for r in receipt['protected']})

    def test_nearest_order_is_applied_within_stable_bank_order(self):
        from copy import deepcopy
        self.add_fp('DEVICE',[('1',25,35,'/rail'),('2',25,37,'/return')])
        self.add_fp('CAP',[('1',26,35,'/rail'),('2',26,37,'/return')])
        doc=contract();second=deepcopy(doc['relations'][0]);second['id']='second'
        for kind in ('supply','return'):
            second[kind][0]['reference']='DEVICE';second[kind][1]['reference']='CAP'
        bulk=deepcopy(doc['relations'][0]);bulk['id']='bulk'
        for kind in ('supply','return'):bulk[kind][1]['reference']='OTHER'
        doc['relations']=[bulk,doc['relations'][0],second];self.save();receipt=self.seed(doc)
        self.assertEqual([r['id'] for r in receipt['relations']],['cell','bulk','second'])
        self.assertEqual(receipt['generation_order']['selected'],'nearest-first')

    def test_three_capacitor_bank_reaches_nearest_capacitor_before_enclosing_it(self):
        from copy import deepcopy
        from agent_ee.storage import write_json
        doc = contract()
        for name, x, ys, size in [('CAP2',34.6,(29,31),600_000),('CAP3',31.6,(29,32.4),500_000)]:
            fp = self.add_fp(name,[])
            for number,y,net in [('1',ys[0],'/rail'),('2',ys[1],'/return')]:
                self.add_pad(fp,number,x,y,net,size)
            relation = deepcopy(doc['relations'][0]);relation['id'] = name
            for kind in ('supply','return'):relation[kind][1]['reference'] = name
            doc['relations'].append(relation)
        # A fresh project name lets native settings load the authored fabrication floor.
        self.path = self.root/'three-cap-bank.kicad_pcb';self.save()
        write_json(self.path.with_suffix('.kicad_pro'),dict(
            board=dict(design_settings=dict(rules=dict(min_clearance=.1))),
            meta=dict(filename=self.path.with_suffix('.kicad_pro').name,version=1),
            net_settings=dict(classes=[dict(name='Default',clearance=.2,track_width=.4,
                via_diameter=.6,via_drill=.3)],meta=dict(version=3))))
        import subprocess,sys,json
        request=self.root/'bank-contract.json';result=self.root/'bank-result.json';write_json(request,doc)
        done=subprocess.run([sys.executable,'-B','-c',
            'import json,sys,pcbnew as k;from agent_ee import local_decoupling as l;'
            'from agent_ee.decoupling_loops import measure;from agent_ee.placement_job import Clock;'
            'doc=json.load(open(sys.argv[2]));r=l.seed(sys.argv[1],doc,clock=Clock(60));'
            'b=k.LoadBoard(sys.argv[1]);k.ZONE_FILLER(b).Fill(b.Zones());k.SaveBoard(sys.argv[1],b);'
            'json.dump(dict(floor=l._separation_floor(b),geometry=l.verify(sys.argv[1],r),'
            'loops=measure(sys.argv[1],doc)),open(sys.argv[3],"w"))',
            str(self.path),str(request),str(result)],capture_output=True,text=True,timeout=90)
        self.assertEqual(done.returncode,0,done.stderr)
        report=json.loads(result.read_text())
        self.assertEqual(report['floor'],100_000)
        self.assertEqual(report['geometry']['geometry']['status'],'pass')
        self.assertEqual(len(report['loops']['records']),3)
        self.assertTrue(report['loops']['banks'][0]['device_return_reaches_plane_only_through_bank_capacitors'])
        self.assertFalse(report['loops']['banks'][0]['return']['pin_bypasses_bank'])

    def test_via_site_work_ignores_8000_distant_obstacles(self):
        from unittest.mock import patch
        terminal = local.pad(self.board,contract()['relations'][0]['return'][1])
        rules = local._rules(self.board,terminal)
        baseline = local._via_obstacles(self.board,terminal,rules)
        fp = self.add_fp('FAR',[])
        for n in range(8000):self.add_pad(fp,n+1,80+(n%100)*.1,80+(n//100)*.1,None,50_000)
        distant = local._track(self.board,[(80_000_000,80_000_000),(90_000_000,80_000_000)],
                               terminal.GetNetCode(),self.k.F_Cu,400_000)[0]
        drawing = self.k.PCB_SHAPE(self.board);drawing.SetShape(self.k.SHAPE_T_SEGMENT)
        drawing.SetStart(self.k.VECTOR2I(80_000_000,82_000_000));drawing.SetEnd(self.k.VECTOR2I(90_000_000,82_000_000))
        drawing.SetWidth(400_000);drawing.SetLayer(self.k.B_Cu);self.board.Add(drawing)
        obstacles = local._via_obstacles(self.board,terminal,rules)
        self.assertEqual([len(group) for group in obstacles],[len(group) for group in baseline])
        via = self.k.PCB_VIA(self.board);via.SetWidth(rules['diameter']);via.SetDrill(rules['drill'])
        via.SetLayerPair(self.k.F_Cu,self.k.B_Cu);via.SetNetCode(terminal.GetNetCode())
        with patch.object(local,'_via_pad_collision',wraps=local._via_pad_collision) as examined:
            for point in local._via_centres(local.position(terminal),range(0,360,10)):
                via.SetPosition(self.k.VECTOR2I(*point))
                local._via_site_blocked(self.board,via,terminal,rules,obstacles)
            self.assertLessEqual(examined.call_count,1496*len(baseline[0]))
            print(dict(far_pads=8000,local_obstacles=[len(group) for group in obstacles],
                       pad_shape_tests=examined.call_count,max_pad_shape_tests=1496*len(baseline[0])))
        # Collection happens once per tie, independent of the number of trial sites.
        with patch.object(local,'_via_obstacles',wraps=local._via_obstacles) as collect:
            local._tie(self.board,terminal,self.k.B_Cu,clock=Clock(60),cancel_dir=None,
                       away_from=(30_000_000,31_000_000))
            self.assertEqual(collect.call_count,1)

    def test_shared_pad_openings_do_not_erase_nearby_track_obstacles(self):
        self.board.GetDesignSettings().m_MinClearance = 100_000
        terminal = local.pad(self.board,contract()['relations'][0]['return'][0]);net=terminal.GetNetCode()
        local._track(self.board,[(30_600_000,31_600_000),(31_500_000,31_600_000)],net,self.k.F_Cu,400_000)
        local._track(self.board,[(30_000_000,31_000_000),(33_000_000,31_000_000)],net,self.k.F_Cu,400_000)
        boxes = local._obstacles(self.board,net,self.k.F_Cu,425_000,(terminal,))
        for point in [(30_610_000,31_610_000),(30_900_000,31_000_000)]:
            self.assertTrue(any(x<=point[0]<=u and y<=point[1]<=v for x,y,u,v in boxes),point)

    def test_geometry_rejects_plane_via_touching_a_different_generated_leg(self):
        receipt,items = self.close_pair()
        self.board.Remove(items[1])
        via = self.k.PCB_VIA(self.board);via.SetPosition(self.k.VECTOR2I(35_000_000,33_200_000))
        via.SetWidth(600_000);via.SetDrill(300_000);via.SetLayerPair(self.k.F_Cu,self.k.B_Cu)
        via.SetNetCode(items[0].GetNetCode());via.SetLocked(True);self.board.Add(via)
        row = receipt['relations'][1];row['copper']=[via.m_Uuid.AsString()]
        row['legs'][0].update(kind='plane_tie',tracks=row['copper'])
        receipt['protected']=[local.copper_identity(items[0]),local.copper_identity(via)]
        self.assertRaisesRegex(JobRefusal,'plane via touches another generated leg',local.verify_geometry,self.board,receipt)

    def test_stub_preparation_preserves_locked_protected_one_ended_and_wider_tracks(self):
        from agent_ee.storage import digest
        for kind in ('locked','protected','one-ended','wider'):
            with self.subTest(kind=kind):
                board = self.k.BOARD();board.SetCopperLayerCount(2)
                net=self.k.NETINFO_ITEM(board,'/return');board.Add(net)
                receiver=local._track(board,[(30_000_000,30_000_000),(33_000_000,30_000_000)],net.GetNetCode(),self.k.F_Cu,400_000)[0]
                end=(32_000_000,31_000_000) if kind=='one-ended' else (32_000_000,30_000_000)
                item=local._track(board,[(31_000_000,30_000_000),end],net.GetNetCode(),self.k.F_Cu,
                                  600_000 if kind=='wider' else 200_000)[0]
                item.SetLocked(kind=='locked')
                path=self.root/(kind+'.kicad_pcb');self.k.SaveBoard(str(path),board)
                protected=[local.copper_identity(receiver)]
                if kind=='protected':protected.append(local.copper_identity(item))
                before=digest(path);output=self.root/(kind+'-trial.kicad_pcb')
                result=local.prepare_stub_trial(dict(board=str(path),board_sha256=before,output=str(output),
                    protected=protected,warnings=[item.m_Uuid.AsString()]))
                self.assertEqual(result['status'],'not_applicable',result)
                self.assertFalse(output.exists());self.assertEqual(digest(path),before)

    def test_stub_trial_rejects_opens_dangling_failed_verify_and_changed_native_nodes(self):
        from unittest.mock import patch
        from agent_ee import placement_job as job
        from agent_ee.storage import digest,read_json,write_json
        import subprocess
        receipt=self.seed();native=self.k.LoadBoard(str(self.path))
        stub=local._track(native,[(31_000_000,31_000_000),(32_000_000,31_000_000)],
                          native.FindNet('/return').GetNetCode(),self.k.F_Cu,200_000)[0]
        stub.SetLocked(False);self.k.SaveBoard(str(self.path),native)
        warning=dict(type='track_dangling',severity='warning',items=[dict(uuid=stub.m_Uuid.AsString())])
        report=dict(violations=[warning],unconnected_items=[])
        original=self.path.read_bytes()
        # Refill owns these zones, as in the controller's real cleanup request.
        owned={z.m_Uuid.AsString() for z in native.Zones()}
        for fault in ('opens','dangling','verify','native-nodes','clean'):
            with self.subTest(fault=fault):
                self.path.write_bytes(original)
                before=digest(self.path)
                def prepare(argv,**kwargs):
                    request=read_json(argv[-1]);result=local.prepare_stub_trial(request)
                    self.assertEqual(result['status'],'prepared')
                    if fault=='native-nodes':
                        trial=self.k.LoadBoard(request['output'])
                        outline=next(d for d in trial.GetDrawings() if d.GetLayer()==self.k.Edge_Cuts)
                        outline.SetWidth(outline.GetWidth()+100_000)
                        self.k.SaveBoard(request['output'],trial)
                        result['board_sha256']=digest(Path(request['output']))
                    write_json(request['result'],result)
                    return subprocess.CompletedProcess(argv,0,b'',b'')
                after=dict(violations=[warning] if fault=='dangling' else [],
                           unconnected_items=[dict(unconnected=True)] if fault=='opens' else [])
                with patch.object(job,'_run_child',side_effect=prepare), patch.object(job,'drc',return_value=(after,{})):
                    if fault=='verify':
                        with patch.object(local,'verify',side_effect=JobRefusal('deliberate protected verification failure')):
                            result=job._cleanup_local_router_stubs(self.path,report,receipt,self.root/('cleanup-'+fault),owned_zone_uuids=owned,clock=Clock(60),cancel_dir=None)
                    else:
                        result=job._cleanup_local_router_stubs(self.path,report,receipt,self.root/('cleanup-'+fault),owned_zone_uuids=owned,clock=Clock(60),cancel_dir=None)
                self.assertEqual(result['status'],'accepted' if fault=='clean' else 'rejected',result)
                if fault=='clean':self.assertNotEqual(digest(self.path),before)
                else:self.assertEqual(digest(self.path),before)

    def test_search_keeps_native_floor_from_an_earlier_escape(self):
        self.board.GetDesignSettings().m_MinClearance = 100_000
        net = self.board.FindNet('/return').GetNetCode()
        escape = local._track(self.board,[(31_000_000,31_325_000),(32_000_000,31_325_000)],
                              net,self.k.F_Cu,100_000)[0]
        endpoints = [local.pad(self.board,p) for p in contract()['relations'][0]['return']]
        added = local._leg(self.board,*endpoints,clock=Clock(60),cancel_dir=None)
        self.assertTrue(added)
        for item in added:
            self.assertFalse(item.GetEffectiveShape(self.k.F_Cu).Collide(
                escape.GetEffectiveShape(self.k.F_Cu),100_000),local.copper_identity(item))
        via = self.k.PCB_VIA(self.board);via.SetPosition(self.k.VECTOR2I(31_500_000,31_750_000))
        via.SetWidth(600_000);via.SetDrill(300_000);via.SetLayerPair(self.k.F_Cu,self.k.B_Cu)
        via.SetNetCode(net)
        self.assertTrue(local._via_site_blocked(self.board,via,endpoints[0],local._rules(self.board,endpoints[0])))

    def test_leg_can_use_a_channel_above_floor_below_class_preference(self):
        self.board.GetDesignSettings().m_MinClearance = 100_000
        endpoints = [local.pad(self.board,p) for p in contract()['relations'][0]['return']]
        for terminal in endpoints:terminal.SetSize(self.k.VECTOR2I(200_000,200_000))
        obstacles = [local._track(self.board,[(28_000_000,y),(35_000_000,y)],
                     endpoints[0].GetNetCode(),self.k.F_Cu,100_000)[0]
                     for y in (30_700_000,31_300_000)]
        # A 0.2 mm leg has 0.15 mm gaps: above the 0.1 mm native floor,
        # below the 0.2 mm class preference. Neither obstacle touches a pad.
        added = local._leg(self.board,*endpoints,clock=Clock(60),cancel_dir=None)
        self.assertTrue(added)
        self.assertAlmostEqual(sum(t.GetLength()/1e6 for t in added),3)
        for item in added:
            for obstacle in obstacles:
                self.assertFalse(item.GetEffectiveShape(self.k.F_Cu).Collide(
                    obstacle.GetEffectiveShape(self.k.F_Cu),100_000))

    def test_search_keeps_native_floor_from_an_existing_plane_via(self):
        self.board.GetDesignSettings().m_MinClearance = 100_000
        endpoints = [local.pad(self.board,p) for p in contract()['relations'][0]['return']]
        via = self.k.PCB_VIA(self.board);via.SetPosition(self.k.VECTOR2I(31_500_000,31_575_000))
        via.SetWidth(600_000);via.SetDrill(300_000);via.SetLayerPair(self.k.F_Cu,self.k.B_Cu)
        via.SetNetCode(endpoints[0].GetNetCode());self.board.Add(via)
        added = local._leg(self.board,*endpoints,clock=Clock(60),cancel_dir=None)
        self.assertTrue(added)
        for item in added:
            self.assertFalse(item.GetEffectiveShape(self.k.F_Cu).Collide(via.GetEffectiveShape(self.k.F_Cu),100_000))

    def close_pair(self, same_bank=False, escape=False, gap=50_000):
        from copy import deepcopy
        self.board.GetDesignSettings().m_MinClearance = 100_000
        doc = contract();second = deepcopy(doc['relations'][0]);second['id'] = 'second'
        if same_bank:
            for kind in ('supply','return'):second[kind][1]['reference'] = 'OTHER'
        else:
            for kind in ('supply','return'):second[kind][0]['reference'] = 'OTHER'
        doc['relations'].append(second)
        net = self.board.FindNet('/return').GetNetCode()
        items = [local._track(self.board,[(34_000_000,y),(36_000_000,y)],net,self.k.F_Cu,200_000)[0]
                 for y in (33_000_000,33_200_000+gap)]
        rows=[]
        for index,(relation,item) in enumerate(zip(doc['relations'],items)):
            kind = 'plane_tie' if escape and index==0 else 'return'
            endpoints = relation['return'][1:] if kind=='plane_tie' else relation['return']
            uid=item.m_Uuid.AsString()
            rows.append(dict(id=relation['id'],copper=[uid],legs=[dict(kind=kind,tracks=[uid],
                pads=[local.pad(self.board,p).m_Uuid.AsString() for p in endpoints])]))
        receipt=dict(contract=doc,relations=rows,protected=[local.copper_identity(t) for t in items],other_pads=[])
        return receipt,items

    def test_geometry_refuses_subfloor_noncontacting_pairs(self):
        for same_bank,escape in ((False,False),(True,True)):
            with self.subTest(same_bank=same_bank,escape=escape):
                receipt,items=self.close_pair(same_bank,escape)
                self.assertFalse(items[0].GetEffectiveShape(self.k.F_Cu).Collide(items[1].GetEffectiveShape(self.k.F_Cu),1))
                self.assertRaisesRegex(JobRefusal,'native clearance floor',local.verify_geometry,self.board,receipt)
                for item in items:self.board.Remove(item)
        receipt,items=self.close_pair(gap=150_000)
        self.assertEqual(local.verify_geometry(self.board,receipt)['status'],'pass')
        # 0.15 mm is above the native 0.1 mm floor, below the 0.2 mm class preference.
        via=self.k.PCB_VIA(self.board);via.SetPosition(self.k.VECTOR2I(35_000_000,33_475_000))
        via.SetWidth(600_000);via.SetDrill(300_000);via.SetLayerPair(self.k.F_Cu,self.k.B_Cu)
        via.SetNetCode(items[0].GetNetCode());via.SetLocked(True);self.board.Add(via)
        row=receipt['relations'][1];row['copper']=[via.m_Uuid.AsString()];row['legs'][0]['tracks']=row['copper'];row['legs'][0]['kind']='plane_tie'
        receipt['protected']=[local.copper_identity(items[0]),local.copper_identity(via)]
        self.assertFalse(via.GetEffectiveShape(self.k.F_Cu).Collide(items[0].GetEffectiveShape(self.k.F_Cu),1))
        self.assertRaisesRegex(JobRefusal,'native clearance floor',local.verify_geometry,self.board,receipt)

    def test_same_bank_device_legs_remain_exempt_from_floor(self):
        receipt,items=self.close_pair(same_bank=True)
        self.assertTrue(items[0].GetEffectiveShape(self.k.F_Cu).Collide(items[1].GetEffectiveShape(self.k.F_Cu),100_000))
        self.assertEqual(local.verify_geometry(self.board,receipt)['status'],'pass')

    def test_saved_separation_report_uses_floor_and_records_exemptions(self):
        import subprocess,sys
        from agent_ee.storage import write_json,read_json
        def saved_report(receipt):
            self.save()
            # Native design-rule floors belong to the authored project, not the PCB alone.
            write_json(self.path.with_suffix('.kicad_pro'),dict(
                board=dict(design_settings=dict(rules=dict(min_clearance=.1))),
                meta=dict(filename='board.kicad_pro',version=1),
                net_settings=dict(classes=[dict(name='Default',clearance=.2,track_width=.4,
                    via_diameter=.6,via_drill=.3)],meta=dict(version=3))))
            record=self.root/'receipt.json';result=self.root/'separation.json';write_json(record,receipt)
            # A new native process reads the authored project, avoiding KiCad's cached project.
            done=subprocess.run([sys.executable,'-B','-c',
                'import json,sys;from agent_ee.local_decoupling import separation_report;'
                'json.dump(separation_report(sys.argv[1],json.load(open(sys.argv[2]))),open(sys.argv[3],"w"))',
                str(self.path),str(record),str(result)],capture_output=True,text=True,timeout=60)
            self.assertEqual(done.returncode,0,done.stderr)
            return read_json(result)
        receipt,items=self.close_pair(gap=150_000)
        result=saved_report(receipt)
        self.assertEqual(result['floor_mm'],.1)
        self.assertEqual(result['outcome'],'pass')
        self.assertEqual(len(result['pairs']),1)
        self.assertAlmostEqual(result['pairs'][0]['minimum_gap_mm_lower_bound'],.15,places=5)
        for item in items:self.board.Remove(item)
        receipt,items=self.close_pair(same_bank=True)
        result=saved_report(receipt)
        self.assertFalse(result['pairs'])
        self.assertEqual(len(result['exempt_pairs']),1)

    def test_shared_return_can_reach_a_small_second_capacitor_without_class_clearance(self):
        from copy import deepcopy
        self.add_fp('CAP2', [('1', 32, 28, '/rail'), ('2', 32, 30.55, '/return')])
        for fp in self.board.GetFootprints():
            if fp.GetReference() == 'CAP2':
                for p in fp.Pads():p.SetSize(self.k.VECTOR2I(200_000,200_000))
        doc = contract();second = deepcopy(doc['relations'][0]);second['id'] = 'small-cap'
        for leg in ('supply','return'):second[leg][1]['reference'] = 'CAP2'
        doc['relations'].append(second)
        self.save();receipt = self.seed(doc)
        self.assertEqual(local.verify(self.path,receipt)['status'],'pass')
        self.filled();result = measure(self.path,doc)
        self.assertEqual(result['outcome'],'info',result)
        self.assertFalse(result['banks'][0]['return']['pin_bypasses_bank'])

    def test_plane_escape_finds_a_window_between_radial_samples(self):
        net = self.board.FindNet('/foreign')
        for ref, x, y, w, h in [('LEFT',26.925,30,13.85,20),('RIGHT',39.975,30,10.05,20),
                                  ('TOP',34.4,25.575,1.1,11.15),('BOTTOM',34.4,36.125,1.1,7.75)]:
            fp = self.k.FOOTPRINT(self.board);fp.SetReference(ref);self.board.Add(fp)
            p = self.k.PAD(fp);p.SetNumber('1');p.SetAttribute(self.k.PAD_ATTRIB_SMD)
            p.SetShape(self.k.PAD_SHAPE_RECT);p.SetSize(self.k.VECTOR2I(round(w*1e6),round(h*1e6)))
            layers = self.k.LSET();layers.AddLayer(self.k.B_Cu);p.SetLayerSet(layers)
            p.SetPosition(self.k.VECTOR2I(round(x*1e6),round(y*1e6)));p.SetNet(net);fp.Add(p)
        self.save();receipt = self.seed()
        board = self.k.LoadBoard(str(self.path))
        via = next(t for t in board.GetTracks() if t.GetClass()=='PCB_VIA')
        self.assertEqual(local.position(via),(34_400_000,31_700_000))
        self.assertEqual(local.verify(self.path,receipt)['geometry']['status'],'pass')

    def test_1100_distant_pads_do_not_exhaust_the_local_search(self):
        self.add_far_pads(1100, None)
        self.save()
        receipt = self.seed()
        self.assertEqual(local.verify(self.path, receipt)['status'], 'pass')
        self.filled()
        self.assertEqual(measure(self.path, contract())['outcome'], 'info')

    def test_2100_distant_net_items_do_not_make_the_typed_check_unknown(self):
        self.add_far_pads(2100, '/return')
        self.save()
        self.seed()
        self.filled()
        result = self.checked(contract())
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        self.assertEqual(result['details']['outcome'], 'info')

    def escape_case(self, same_bank):
        from copy import deepcopy
        g = self.k.PCB_SHAPE(self.board)
        g.SetShape(self.k.SHAPE_T_SEGMENT)
        g.SetLayer(self.k.B_Cu)
        g.SetStart(self.k.VECTOR2I(33_800_000, 30_000_000))
        g.SetEnd(self.k.VECTOR2I(33_800_000, 32_000_000))
        g.SetWidth(1_200_000)
        self.board.Add(g)
        doc = contract()
        second = deepcopy(doc['relations'][0])
        second['id'] = 'bulk' if same_bank else 'other-device'
        if same_bank:
            self.add_fp('BLK', [('1', 33, 30, '/foreign')])
            fp = next(fp for fp in self.board.GetFootprints() if fp.GetReference() == 'BLK')
            for p in fp.Pads():p.SetSize(self.k.VECTOR2I(200_000, 200_000))
            self.add_fp('CAP2', [('1', 34.05, 27.6, '/rail'), ('2', 34.05, 29, '/return')])
        else:
            self.add_fp('DEV2', [('1', 36.5, 34, '/rail'), ('2', 34.1, 34, '/return')])
            self.add_fp('CAP2', [('1', 36.5, 26.5, '/rail'), ('2', 34.1, 26.5, '/return')])
            for leg in ('supply', 'return'):second[leg][0]['reference'] = 'DEV2'
        for leg in ('supply', 'return'):second[leg][1]['reference'] = 'CAP2'
        doc['relations'].append(second)
        self.save()
        return doc

    def test_cross_bank_escape_search_finds_the_clear_detour(self):
        doc = self.escape_case(False)
        receipt = self.seed(doc)
        self.assertEqual(local.verify(self.path, receipt)['status'], 'pass')
        self.filled()
        self.assertTrue(all(not b['return']['pin_bypasses_bank'] for b in measure(self.path, doc)['banks']))

    def test_same_bank_escape_search_finds_the_clear_detour(self):
        doc = self.escape_case(True)
        receipt = self.seed(doc)
        self.assertEqual(local.verify(self.path, receipt)['status'], 'pass')
        self.filled()
        self.assertFalse(measure(self.path, doc)['banks'][0]['return']['pin_bypasses_bank'])

    def poison_return(self, receipt, points):
        # Fault injection into a saved candidate proves the final guard remains
        # independent of the search that normally avoids this geometry.
        board = self.k.LoadBoard(str(self.path))
        row = receipt['relations'][1]
        leg = next(leg for leg in row['legs'] if leg['kind'] == 'return')
        old = set(leg['tracks'])
        for item in list(board.GetTracks()):
            if item.m_Uuid.AsString() in old:board.Remove(item)
        code = board.FindNet('/return').GetNetCode()
        added = local._track(board, points, code, self.k.F_Cu, 400_000)
        ids = [t.m_Uuid.AsString() for t in added]
        leg['tracks'] = ids
        row['copper'] = [uid for uid in row['copper'] if uid not in old] + ids
        receipt['protected'] = [p for p in receipt['protected'] if p['uuid'] not in old] + [local.copper_identity(t) for t in added]
        self.k.SaveBoard(str(self.path), board)
        return receipt

    def test_same_bank_escape_guard_rejects_a_planner_crossing(self):
        doc = self.escape_case(True)
        receipt = self.seed(doc)
        receipt = self.poison_return(receipt, [(30_000_000,31_000_000), (32_225_000,31_775_000),
            (33_775_000,31_775_000), (34_050_000,29_000_000)])
        self.assertRaisesRegex(JobRefusal, 'generated legs meet outside', local.verify, self.path, receipt)

    def test_cross_bank_guard_rejects_a_planner_crossing(self):
        doc = self.escape_case(False)
        receipt = self.seed(doc)
        receipt = self.poison_return(receipt, [(34_100_000,34_000_000), (32_000_000,34_000_000),
            (32_000_000,30_000_000), (34_100_000,30_000_000), (34_100_000,26_500_000)])
        self.assertRaisesRegex(JobRefusal, 'generated legs meet outside', local.verify, self.path, receipt)

    def two_devices(self, second_net='/return'):
        from copy import deepcopy
        if second_net != '/return':self.board.Add(self.k.NETINFO_ITEM(self.board, second_net))
        fp = next(fp for fp in self.board.GetFootprints() if fp.GetReference() == 'IC')
        self.add_pad(fp, '3', 30, 34, second_net)
        fp = next(fp for fp in self.board.GetFootprints() if fp.GetReference() == 'OTHER')
        next(p for p in fp.Pads() if p.GetNumber() == '2').SetNet(self.board.FindNet(second_net))
        doc = contract()
        second = deepcopy(doc['relations'][0]);second['id'] = 'second'
        for leg in ('supply', 'return'):second[leg][1]['reference'] = 'OTHER'
        second['return'][0]['pad'] = '3'
        for endpoint in second['return']:endpoint['net'] = second_net
        doc['relations'].append(second)
        self.save()
        return doc

    def device_via(self, board, endpoint):
        terminal = local.pad(board, endpoint)
        via = self.k.PCB_VIA(board)
        via.SetPosition(terminal.GetPosition());via.SetWidth(600_000);via.SetDrill(300_000)
        via.SetViaType(self.k.VIATYPE_THROUGH);via.SetLayerPair(self.k.F_Cu, self.k.B_Cu)
        via.SetNetCode(terminal.GetNetCode());board.Add(via)
        return via

    def test_reachable_graph_keeps_a_distant_router_bypass(self):
        doc = contract();self.seed(doc)
        board = self.k.LoadBoard(str(self.path))
        via = self.device_via(board, doc['relations'][0]['return'][0])
        via.SetPosition(self.k.VECTOR2I(40_000_000,37_000_000))
        uid = via.m_Uuid.AsString()
        local._track(board,[(30_000_000,31_000_000),(40_000_000,37_000_000)],via.GetNetCode(),self.k.F_Cu,400_000)
        self.k.SaveBoard(str(self.path),board);self.filled()
        bank = measure(self.path,doc)['banks'][0]
        self.assertTrue(bank['return']['pin_bypasses_bank'])
        self.assertIn(uid,[p['plane_entry'] for d in bank['return']['devices'] for p in d['bypass_paths']])

    def test_bank_without_a_plane_is_not_applicable_not_a_bypass(self):
        doc = self.two_devices('/return2')
        self.seed(doc);self.filled()
        result = self.checked(doc)
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        bank = result['details']['banks'][0]
        self.assertFalse(bank['return']['pin_bypasses_bank'])
        self.assertIsNone(bank['device_return_reaches_plane_only_through_bank_capacitors'])
        self.assertEqual(bank['positive_statement_status'], 'not_applicable')
        self.assertEqual(bank['device_returns_without_plane'], [doc['relations'][1]['return'][0]])

    def test_bank_grades_the_second_device_pin_too(self):
        doc = self.two_devices()
        self.seed(doc)
        board = self.k.LoadBoard(str(self.path));self.device_via(board, doc['relations'][1]['return'][0])
        self.k.SaveBoard(str(self.path), board);self.filled()
        bank = measure(self.path, doc)['banks'][0]
        self.assertEqual(len(bank['return']['devices']), 2)
        self.assertFalse(bank['return']['devices'][0]['pin_bypasses_bank'])
        self.assertTrue(bank['return']['devices'][1]['pin_bypasses_bank'])
        self.assertTrue(bank['return']['pin_bypasses_bank'])

    def test_capacitor_plane_access_blocks_every_bank_device_pin(self):
        doc = self.two_devices()
        receipt = self.seed(doc)
        ties = {uid for row in receipt['relations'] for leg in row['legs'] if leg['kind'] == 'plane_tie' for uid in leg['tracks']}
        board = self.k.LoadBoard(str(self.path))
        for item in list(board.GetTracks()):
            if item.m_Uuid.AsString() in ties:board.Remove(item)
        firstcap = local.pad(board, doc['relations'][0]['return'][1])
        otherdev = local.pad(board, doc['relations'][1]['return'][0])
        local._track(board, [local.position(firstcap), local.position(otherdev)], firstcap.GetNetCode(), self.k.F_Cu, 400_000)
        self.device_via(board, doc['relations'][1]['return'][0])
        self.k.SaveBoard(str(self.path), board);self.filled()
        row = measure(self.path, doc)['records'][0]
        self.assertIsNone(row['return']['capacitor_to_plane_mm'])
        self.assertIn('return: capacitor has no native plane connection without the device pin', row['reasons'])

    def test_redundant_router_stub_cleanup_keeps_native_connectivity_and_generated_items(self):
        from agent_ee import placement_job as job
        fp = next(fp for fp in self.board.GetFootprints() if fp.GetReference() == 'OTHER')
        next(p for p in fp.Pads() if p.GetNumber() == '2').SetPosition(self.k.VECTOR2I(32_600_000,34_000_000))
        self.save();receipt = self.seed()
        board = self.k.LoadBoard(str(self.path))
        ret = board.FindNet('/return').GetNetCode();rail = board.FindNet('/rail').GetNetCode()
        stub = local._track(board,[(32_000_000,31_000_000),(32_050_000,31_050_000)],ret,self.k.F_Cu,400_000)
        tracks = stub + local._track(board,[(32_050_000,31_050_000),(32_600_000,31_600_000),(32_600_000,34_000_000)],ret,self.k.F_Cu,400_000)
        tracks += local._track(board,[(33_000_000,29_000_000),(38_000_000,29_000_000)],rail,self.k.F_Cu,400_000)
        for t in tracks:t.SetLocked(False)
        uid = stub[0].m_Uuid.AsString()
        self.k.SaveBoard(str(self.path),board);self.filled()
        out = self.root/'stub-drc';out.mkdir()
        report,_ = job.drc(self.path,out,refill=True,clock=Clock(120))
        self.assertFalse(report['unconnected_items'])
        self.assertIn(uid,{i['uuid'] for v in report['violations'] if v['type']=='track_dangling' for i in v['items']})
        result = job._cleanup_local_router_stubs(self.path,report,receipt,self.root/'cleanup',
            owned_zone_uuids={z.m_Uuid.AsString() for z in self.board.Zones()},clock=Clock(120),cancel_dir=None)
        self.assertEqual(result['status'],'accepted',result)
        self.assertEqual(result['removed'],[uid])
        self.assertEqual(local.verify(self.path,receipt)['status'],'pass')
        final,_ = job.drc(self.path,out,label='after',clock=Clock(120))
        self.assertFalse(final['unconnected_items'])
        self.assertFalse([v for v in final['violations'] if v.get('severity')=='error' or v['type']=='track_dangling'])

    def test_identical_segment_reuse_is_explicitly_bank_scoped(self):
        board = self.board
        points = [(34_000_000,33_000_000),(36_000_000,33_000_000)]
        code = board.FindNet('/return').GetNetCode()
        first = local._track(board, points, code, self.k.F_Cu, 400_000)
        ids = {t.m_Uuid.AsString() for t in first}
        same = local._track(board, points, code, self.k.F_Cu, 400_000, reusable=ids)
        other = local._track(board, points, code, self.k.F_Cu, 400_000, reusable=set())
        self.assertEqual(same[0].m_Uuid.AsString(), first[0].m_Uuid.AsString())
        self.assertNotEqual(other[0].m_Uuid.AsString(), first[0].m_Uuid.AsString())
        from copy import deepcopy
        doc = contract();second = deepcopy(doc['relations'][0]);second['id'] = 'other'
        for leg in ('supply','return'):second[leg][0]['reference'] = 'OTHER'
        doc['relations'].append(second)
        rows = []
        for relation, items in zip(doc['relations'], (first,other)):
            ids = [t.m_Uuid.AsString() for t in items]
            rows.append(dict(id=relation['id'], copper=ids, legs=[dict(kind='return', tracks=ids,
                pads=[local.pad(board,p).m_Uuid.AsString() for p in relation['return']])]))
        receipt = dict(contract=doc, relations=rows, protected=[local.copper_identity(t) for t in first+other], other_pads=[])
        self.assertRaisesRegex(JobRefusal, 'generated legs meet outside', local.verify_geometry, board, receipt)



class ContractTests(unittest.TestCase):
    def test_shared_returns_and_shared_supplies_form_transitive_banks(self):
        from copy import deepcopy
        doc = contract()
        second = deepcopy(doc['relations'][0])
        second['id'] = 'other-cell'
        second['supply'][0]['reference'] = 'ALT'
        for leg in ('supply', 'return'):
            second[leg][1]['reference'] = 'OTHER'
        third = deepcopy(second)
        third['id'] = 'third-cell'
        third['return'][0]['reference'] = 'ALT'
        for leg in ('supply', 'return'):
            third[leg][1]['reference'] = 'THIRD'
        doc['relations'] += [second, third]
        self.assertEqual(local.validate_topology(doc), doc)
        expected = [dict(id='bank-1', relations=['cell', 'other-cell', 'third-cell'])]
        self.assertEqual(local.banks(doc), expected)
        doc['banks'] = expected
        self.assertEqual(local.validate(doc), doc)
        doc['banks'] = [dict(id='bank-1', relations=['cell'])]
        self.assertRaisesRegex(ValueError, 'automatically derived', local.validate, doc)

    def test_bridge_relation_merges_two_existing_banks(self):
        from copy import deepcopy
        doc = contract()
        second = deepcopy(doc['relations'][0]);second['id'] = 'second'
        second['supply'][0]['pad'] = '3';second['return'][0]['pad'] = '4'
        for leg in ('supply', 'return'):second[leg][1]['reference'] = 'OTHER'
        bridge = deepcopy(second);bridge['id'] = 'bridge'
        bridge['supply'][0]['pad'] = '1'
        for leg in ('supply', 'return'):bridge[leg][1]['reference'] = 'THIRD'
        doc['relations'] += [second,bridge]
        self.assertEqual(local.banks(doc), [dict(id='bank-1', relations=['cell','second','bridge'])])

    def test_device_pad_cannot_also_require_its_own_capacitor_plane_entry(self):
        from copy import deepcopy
        doc = contract()
        second = deepcopy(doc['relations'][0])
        second['id'] = 'other-cell'
        second['return'][0]['reference'] = 'OTHER'
        second['return'][1]['reference'] = 'IC'
        doc['relations'].append(second)
        with self.assertRaisesRegex(ValueError, r'cell and other-cell: device plane pad IC.2'):
            local.validate_topology(doc)

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
