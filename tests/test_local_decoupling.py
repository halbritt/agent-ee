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
        self.path.with_suffix('.kicad_pro').write_text('{}')
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
