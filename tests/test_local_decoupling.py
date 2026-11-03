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
        # Supply pin and capacitor are shared; return uses the other land.
        second['return'][1]['reference'] = 'OTHER'
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
        for leg in ('supply', 'return'):
            other[leg][1]['reference'] = 'OTHER'
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
