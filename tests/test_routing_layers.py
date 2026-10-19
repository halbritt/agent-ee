"""Native layer/zone connectivity is distinct from a measurable routed path."""
import tempfile
import unittest
import os
from pathlib import Path

from agent_ee.routing_native import probe_board, route_coverage
from tests.routing_fixture import make_board
from tests.toolchain import require_toolchain


def plane_board(path, *, split=False, rule_area=False):
    import pcbnew as k
    point = lambda x, y: k.VECTOR2I(round(x * 1e6), round(y * 1e6))
    board = k.BOARD()
    board.SetCopperLayerCount(4)
    corners = [(40, 40), (60, 40), (60, 60), (40, 60)]
    for a, b in zip(corners, corners[1:] + corners[:1]):
        edge = k.PCB_SHAPE(board); edge.SetShape(k.SHAPE_T_SEGMENT)
        edge.SetLayer(k.Edge_Cuts); edge.SetStart(point(*a)); edge.SetEnd(point(*b))
        edge.SetWidth(50000); board.Add(edge)
    net = k.NETINFO_ITEM(board, 'GND'); board.Add(net)
    for i, x in enumerate((45, 55)):
        fp = k.FOOTPRINT(board); board.Add(fp); fp.SetReference('J' + str(i))
        fp.SetPosition(point(x, 50))
        pad = k.PAD(fp); fp.Add(pad); pad.SetNumber('1'); pad.SetNet(net)
        pad.SetAttribute(k.PAD_ATTRIB_SMD); pad.SetShape(k.PAD_SHAPE_RECT)
        layers = k.LSET(); layers.AddLayer(k.F_Cu); pad.SetLayerSet(layers)
        pad.SetSize(point(1, 1)); pad.SetPosition(point(x, 50))
    zone = k.ZONE(board); zone.SetNet(net); zone.SetLayer(k.F_Cu)
    zone.SetLocalClearance(200000); zone.SetMinThickness(200000)
    zone.SetPadConnection(k.ZONE_CONNECTION_THERMAL)
    zone.SetThermalReliefGap(300000); zone.SetThermalReliefSpokeWidth(300000)
    zone.SetIslandRemovalMode(k.ISLAND_REMOVAL_MODE_NEVER)
    for x1, x2 in [(42, 58)]:
        zone.Outline().NewOutline()
        for x, y in [(x1, 45), (x2, 45), (x2, 55), (x1, 55)]:
            zone.Outline().Append(round(x * 1e6), round(y * 1e6))
    board.Add(zone)
    if rule_area or split:
        keepout = k.ZONE(board); keepout.SetLayer(k.F_Cu); keepout.SetIsRuleArea(True)
        keepout.SetDoNotAllowZoneFills(True); keepout.Outline().NewOutline()
        for x, y in ([(49, 44), (51, 44), (51, 56), (49, 56)] if split else
                     [(49, 53), (51, 53), (51, 54), (49, 54)]):
            keepout.Outline().Append(round(x * 1e6), round(y * 1e6))
        board.Add(keepout)
    board.BuildConnectivity()
    assert k.ZONE_FILLER(board).Fill(board.Zones())
    assert k.SaveBoard(str(path), board)


class NativeLayerCoverageTests(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        if os.environ.get('AGENT_EE_LAYER_EVIDENCE'):
            self.root = Path(os.environ['AGENT_EE_LAYER_EVIDENCE']) / self._testMethodName
            self.root.mkdir(parents=True)
        self.path = self.root / 'board.kicad_pcb'

    def probe(self, net='GND', label='probe'):
        before = self.path.read_bytes()
        result = probe_board(self.path, [{'nets': [net]}], work=self.root / label)
        from agent_ee.storage import write_json
        write_json(self.root / (label + '.json'), result)
        self.assertEqual(self.path.read_bytes(), before)
        return result

    def test_four_layer_coverage(self):
        import pcbnew as k
        make_board(self.path, lengths=(10,), vias=2, layers=4, net_names=('CAN+',))
        board = k.LoadBoard(str(self.path))
        for track in board.GetTracks():
            if track.GetClass() == 'PCB_TRACK' and track.GetLayer() == k.B_Cu:
                track.SetLayer(k.In1_Cu)
        k.SaveBoard(str(self.path), board)
        result = self.probe('CAN+')['routes']['CAN+']
        self.assertTrue(result['complete'], result)
        self.assertTrue(result['measurement_qualified'], result)
        removed = next(t for t in board.GetTracks() if t.GetClass() == 'PCB_VIA')
        board.Remove(removed); k.SaveBoard(str(self.path), board)
        result = self.probe('CAN+', 'missing-via')['routes']['CAN+']
        self.assertFalse(result['complete'], result)
        self.assertEqual(result['net'], 'CAN+')

    def test_plane_connected_net_and_rule_area(self):
        import pcbnew as k
        plane_board(self.path, rule_area=True)
        result = self.probe()
        route = result['routes']['GND']
        self.assertTrue(route['complete'], route)
        self.assertFalse(route['measurement_qualified'], route)
        self.assertEqual(result['zone_freshness']['status'], 'fresh')
        board = k.LoadBoard(str(self.path))
        zone = next(z for z in board.Zones() if not z.GetIsRuleArea())
        zone.UnFill(); k.SaveBoard(str(self.path), board)
        route = self.probe(label='unfilled')['routes']['GND']
        self.assertFalse(route['complete'], route)
        self.assertIn('unfilled', route['reason'])

    def test_disconnected_islands_are_not_one_zone_connection(self):
        plane_board(self.path, split=True)
        route = self.probe()['routes']['GND']
        self.assertFalse(route['complete'], route)
        self.assertFalse(route['measurement_qualified'], route)
        self.assertIn('disconnected', route['reason'])

    def test_stale_fill_refused_after_save_load(self):
        import pcbnew as k
        for change in ('outline', 'pad'):
            with self.subTest(change=change):
                plane_board(self.path)
                board = k.LoadBoard(str(self.path)); zone = next(iter(board.Zones()))
                if change == 'outline':
                    zone.Outline().SetVertex(0, k.VECTOR2I(47000000, 45000000))
                else:
                    pad = next(p for fp in board.GetFootprints() for p in fp.Pads())
                    pad.SetPosition(k.VECTOR2I(pad.GetPosition().x, 51000000))
                zone.SetNeedRefill(True); k.SaveBoard(str(self.path), board)
                reloaded = k.LoadBoard(str(self.path))
                self.assertFalse(next(iter(reloaded.Zones())).NeedRefill())
                result = self.probe(label=change)
                self.assertEqual(result['zone_freshness']['status'], 'stale')
                self.assertFalse(result['routes']['GND']['complete'])

    def test_non_through_vias_remain_named_refusals(self):
        import pcbnew as k
        make_board(self.path, lengths=(10,), vias=1, layers=4, net_names=('CAN+',))
        board = k.LoadBoard(str(self.path)); via = next(t for t in board.GetTracks() if t.GetClass() == 'PCB_VIA')
        for kind, name in [(k.VIATYPE_BLIND, 'blind/buried'), (k.VIATYPE_BURIED, 'blind/buried'), (k.VIATYPE_MICROVIA, 'micro')]:
            via.SetViaType(kind)
            result = route_coverage(board, 'CAN+')
            self.assertFalse(result['complete'])
            self.assertIn(name, result['reason'])

    def test_duplicate_native_identity_cannot_collapse_connections(self):
        import pcbnew as k
        make_board(self.path, lengths=(10,), net_names=('CAN+',))
        board = k.LoadBoard(str(self.path))
        pads = [p for fp in board.GetFootprints() for p in fp.Pads()]
        pads[1].SetUuidDirect(k.KIID(pads[0].m_Uuid.AsString()))
        self.assertIn('duplicate', route_coverage(board, 'CAN+')['reason'])
        plane_board(self.path)
        from agent_ee import benchmark
        raw = self.path.read_text(); parsed = benchmark.parse(raw)
        zone = benchmark.children(parsed, 'zone')[0]
        self.path.write_text(raw[:parsed.end - 1] + raw[zone.start:zone.end] + raw[parsed.end - 1:])
        with self.assertRaisesRegex(ValueError, 'duplicate native copper zone'):
            self.probe(label='duplicate-zone')

    def test_current_custom_rules_change_native_fill(self):
        import pcbnew as k
        plane_board(self.path)
        board = k.LoadBoard(str(self.path))
        net = k.NETINFO_ITEM(board, 'VCC'); board.Add(net)
        fp = k.FOOTPRINT(board); board.Add(fp); fp.SetReference('U1')
        pad = k.PAD(fp); fp.Add(pad); pad.SetNumber('1'); pad.SetNet(net)
        pad.SetAttribute(k.PAD_ATTRIB_SMD); pad.SetShape(k.PAD_SHAPE_RECT)
        layers = k.LSET(); layers.AddLayer(k.F_Cu); pad.SetLayerSet(layers)
        pad.SetSize(k.VECTOR2I(1000000, 1000000)); pad.SetPosition(k.VECTOR2I(50000000, 50000000))
        board.BuildConnectivity(); self.assertTrue(k.ZONE_FILLER(board).Fill(board.Zones()))
        self.assertTrue(k.SaveBoard(str(self.path), board))
        rule = self.path.with_suffix('.kicad_dru')
        raw = '(version 1)\n(rule "strong-clearance" (constraint clearance (min 2mm)))\n'
        rule.write_text(raw)
        result = self.probe(label='new-rules')
        self.assertEqual(result['zone_freshness']['status'], 'stale')
        self.assertEqual(rule.read_text(), raw)
        from agent_ee.storage import digest
        self.assertEqual(result['zone_freshness']['source_hashes'][rule.name], digest(rule))


LAYERED_CAN = r'''
import sys, pcbnew as k
b=k.LoadBoard('/candidate/project/can.kicad_pcb');b.SetCopperLayerCount(4)
net=k.NETINFO_ITEM(b,'GND');b.Add(net)
for index,x in enumerate((45,55)):
    fp=k.FOOTPRINT(b);b.Add(fp);fp.SetReference('G'+str(index));fp.SetPosition(k.VECTOR2I(x*1000000,53000000))
    p=k.PAD(fp);fp.Add(p);p.SetNumber('1');p.SetNet(net);p.SetAttribute(k.PAD_ATTRIB_SMD)
    p.SetShape(k.PAD_SHAPE_RECT);p.SetSize(k.VECTOR2I(1000000,1000000));p.SetPosition(fp.GetPosition())
    ls=k.LSET();ls.AddLayer(k.F_Cu);p.SetLayerSet(ls)
    v=k.PCB_VIA(b);v.SetNet(net);v.SetPosition(p.GetPosition());v.SetWidth(600000);v.SetDrill(300000)
    v.SetViaType(k.VIATYPE_THROUGH);v.SetLayerPair(k.F_Cu,k.B_Cu);b.Add(v)
assert k.SaveBoard('/work/can.kicad_pcb',b)
'''


class NativeFourLayerCanTests(unittest.TestCase):
    def test_four_layer_can_and_plane_numeric_refusal(self):
        import importlib.util
        import shutil
        from agent_ee import add_planes, stage_receipts
        from agent_ee.executor import execute
        from agent_ee.storage import digest, read_json, write_json
        from tests.toolchain import require_kicad_route
        require_toolchain(native=True)
        checkout = require_kicad_route()
        repo = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location('can_probe', repo / 'fixtures/telemetry-pod/can-pair/probe.py')
        probe = importlib.util.module_from_spec(spec); spec.loader.exec_module(probe)
        out = Path(self.enterContext(tempfile.TemporaryDirectory()))
        if os.environ.get('AGENT_EE_LAYER_EVIDENCE'):
            out = Path(os.environ['AGENT_EE_LAYER_EVIDENCE']) / 'four-layer-can'
            out.mkdir(parents=True)
        root = out / 'candidate'; probe.prepare(root)
        made = execute(root, out / 'four-layer', ['/usr/bin/python3', '-I', '-B', '-c', LAYERED_CAN], timeout=90)
        self.assertEqual(made['exit_code'], 0, made)
        shutil.copyfile(out / 'four-layer/can.kicad_pcb', root / 'project/can.kicad_pcb')
        plan = {'schema': 'agent-ee.plane-plan/1', 'zones': []}
        for layer in ('In1.Cu', 'In2.Cu'):
            plan['zones'].append({'net': 'GND', 'layer': layer,
                'outline': {'polygon_mm': [[42, 44], [58, 44], [58, 56], [42, 56]]},
                'clearance_mm': .2, 'min_width_mm': .2,
                'thermal': {'connection': 'solid', 'gap_mm': .3, 'spoke_width_mm': .3},
                'priority': 0, 'island_removal': 'never'})
        write_json(root / 'plane-plan.json', plan)
        step = add_planes.step(repo / 'src', board='project/can.kicad_pcb', plan='plane-plan.json')
        receipt = stage_receipts.run_step(out / 'planes', root, step,
            {'implementation': 1, 'source_candidate_id': 'four-layer-can', 'receipt_index': 1})
        self.assertEqual(receipt['status'], 'completed', receipt)
        stage_receipts.validate_receipt(out / 'planes'); stage_receipts.adopt(out / 'planes', receipt, root)
        summary = probe.route(root, out / 'route', checkout)
        self.assertEqual(summary['classification'], 'complete', summary)
        self.assertEqual(summary['pair_coverage']['status'], 'reported_coupled')
        positive = probe.evaluate(root, out / 'positive')
        self.assertEqual((positive['execution'], positive['outcome']), ('ok', 'pass'), positive)
        self.assertEqual(digest(out / 'route/run/routing/candidate/project/can.kicad_pcb'),
                         digest(out / 'positive/inputs/project/can.kicad_pcb'))
        original = (root / 'project/can.kicad_pcb').read_bytes()
        mutation = execute(root, out / 'lengthen', ['/usr/bin/python3', '-I', '-B', '-c', probe.LENGTHEN,
            '/candidate/project/can.kicad_pcb', '/work/can.kicad_pcb'], timeout=90)
        self.assertEqual(mutation['exit_code'], 0, mutation)
        shutil.copyfile(out / 'lengthen/can.kicad_pcb', root / 'project/can.kicad_pcb')
        negative = probe.evaluate(root, out / 'negative')
        self.assertEqual((negative['execution'], negative['outcome']), ('ok', 'defect'), negative)
        self.assertEqual(negative['details']['violations'], ['can-skew'])
        (root / 'project/can.kicad_pcb').write_bytes(original)
        rules = {'schema': 'agent-ee.routing-rules/1', 'constraints': [
            {'id': 'ground-length', 'requirement': 'can-probe-routing', 'kind': 'length',
             'nets': ['GND'], 'max_mm': 1000}]}
        write_json(root / 'routing-rules.json', rules)
        ground = probe.evaluate(root, out / 'ground-numeric')
        self.assertEqual(ground['outcome'], 'unknown', ground)
        coverage = ground['details']['constraints'][0]['route_coverage']['GND']
        self.assertTrue(coverage['complete'], coverage)
        self.assertFalse(coverage['measurement_qualified'], coverage)
        write_json(out / 'summary.json', {'routing': summary, 'positive': positive['outcome'],
            'negative': negative['outcome'], 'negative_violations': negative['details']['violations'],
            'ground_numeric': ground['outcome'], 'ground_coverage': coverage})


if __name__ == '__main__':
    unittest.main()
