"""Owned native custom-pad placement and mutation controls (AGENTEE-307)."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_ee.placement_probe import extract, custom_pad_geometry
from agent_ee.placer import pack, transform
from agent_ee.placer_runner import place_candidate, _apply_plan
from tests.test_custom_pad_index import PRIMITIVES
from tests.test_placer import request
from tests import test_placer_native as native_fixture
from tests.toolchain import require_toolchain


class CustomPadPlacementTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-custom-placement-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.scratch = self.root / 'scratch'
        self.scratch.mkdir()
        self.netlist = self.root / 'netlist.xml'
        self.netlist.write_text('<export><components><comp ref="U1"/><comp ref="C1"/></components>'
                                '<nets><net code="1" name="SIGNAL"><node ref="C1" pin="1"/></net></nets></export>')

    def source(self, primitive=None, rotation=0, back=False):
        import pcbnew as k
        fixture = native_fixture.StockPadShapeNativeTest()
        fixture.root = self.root
        path = fixture.shaped_board('custom', [(k.PAD_SHAPE_CUSTOM, 0)])
        text = path.read_text()
        # Replace the native empty primitive set with original geometry. Use all
        # supported kinds together, with an asymmetric extent and nontrivial net.
        text = text.replace('(primitives)', '(primitives ' + (primitive or ' '.join(PRIMITIVES.values())) + ')')
        text = text.replace('(options', '(zone_connect 2) (options')
        path.write_text(text)
        board = k.LoadBoard(str(path))
        net = k.NETINFO_ITEM(board, 'SIGNAL')
        board.Add(net)
        fp = next(f for f in board.GetFootprints() if f.GetReference() == 'C1')
        for pad in fp.Pads():
            layers = k.LSET()
            for layer in (k.F_Cu, k.F_Paste, k.F_Mask):
                layers.AddLayer(layer)
            pad.SetLayerSet(layers)
            pad.SetNet(net)
        if back:
            fp.Flip(fp.GetPosition(), k.FLIP_DIRECTION_LEFT_RIGHT)
        fp.SetOrientationDegrees(rotation)
        k.SaveBoard(str(path), board)
        return path

    def config(self, source):
        return {'policy': request(fixed_refs=[]), 'authorization': {
            'schema': 'agent-ee.placement-write/1', 'single_design_writer': True,
            'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'writable_root': str(self.scratch)}}

    def test_full_primitives_extent_identity_net_layers_all_turns_and_both_sides(self):
        import pcbnew as k
        for back in (False, True):
            for rotation in (0, 90, 180, 270):
                with self.subTest(back=back, rotation=rotation):
                    source = self.source(rotation=rotation, back=back)
                    before = extract(source)
                    fp = next(f for f in before['footprints'] if f['ref'] == 'C1')
                    pad = fp['pads'][0]
                    self.assertEqual(pad['custom_geometry']['status'], 'proven', pad)
                    self.assertEqual(len(pad['custom_geometry']['primitives']), 4)
                    extent = pad['custom_geometry']['bbox_nm']
                    self.assertEqual(sorted((extent[2]-extent[0], extent[3]-extent[1])), [4_000_000, 4_400_000])
                    self.assertEqual(pad['net'], 'SIGNAL')
                    side = 'B' if back else 'F'
                    self.assertEqual(pad['layers'], [side+'.Cu', side+'.Mask', side+'.Paste'])
                    # Independently check a flip reflects the actual primitive set.
                    native = next(f for f in k.LoadBoard(str(source)).GetFootprints() if f.GetReference() == 'C1')
                    snapshot = custom_pad_geometry(next(iter(native.Pads())))
                    for target in (0, 90, 180, 270):
                        board = k.LoadBoard(str(source))
                        facts = transform(fp, [12_000_000, 13_000_000], target)
                        native_plan = {'placements': [{'uuid': fp['uuid'], 'fixed': False,
                                                      'position_nm': facts['position_nm'], 'rotation_deg': target}]}
                        output = self.root / 'rigid.kicad_pcb'
                        _apply_plan(board, native_plan, output)
                        after = next(f for f in extract(output)['footprints'] if f['ref'] == 'C1')
                        self.assertEqual(after['pads'], facts['pads'])
                        reloaded = k.LoadBoard(str(output))
                        moved = next(f for f in reloaded.GetFootprints() if f.GetReference() == 'C1')
                        self.assertEqual(custom_pad_geometry(next(iter(moved.Pads()))), snapshot)
                    destination = self.scratch / f'placed-{back}-{rotation}'
                    report = place_candidate(source, self.netlist, destination, self.config(source))
                    self.assertEqual(report['status'], 'placed', report)
                    self.assertEqual(report['judgment']['verdict'], 'pass')
                    self.assertEqual(report['plan']['qualified_custom_pads'],
                                     [{'reference': 'C1', 'uuid': pad['uuid'], 'number': '1'}])
                    self.assertEqual(report['native_courtyard_overlaps'], [])
                    self.assertEqual(extract(source), before)

    def test_custom_writer_runs_in_networkless_executor(self):
        import json
        from agent_ee.executor import execute
        source = self.source(back=True, rotation=90)
        inputs = self.root / 'inputs'
        inputs.mkdir()
        (inputs / 'board.kicad_pcb').write_bytes(source.read_bytes())
        (inputs / 'netlist.xml').write_bytes(self.netlist.read_bytes())
        config = self.config(source)
        config['authorization']['writable_root'] = '/work'
        (inputs / 'request.json').write_text(json.dumps(config))
        work = self.root / 'sandbox-work'
        execution = execute(inputs, work, ['/usr/bin/python3', '-B', '-m', 'agent_ee.placer_runner',
            '/candidate/board.kicad_pcb', '/candidate/netlist.xml', '--request', '/candidate/request.json',
            '--destination', '/work/proposal', '--result', '/work/result.json'], timeout=60)
        self.assertEqual(execution['execution'], 'ok', (work / 'console.log').read_text())
        report = json.loads((work / 'result.json').read_text())
        self.assertEqual((report['status'], report['write_scope']['status']), ('placed', 'allowed'))
        self.assertEqual(len(report['plan']['qualified_custom_pads']), 1)
        self.assertEqual((inputs / 'board.kicad_pcb').read_bytes(), source.read_bytes())

    def test_native_side_flip_reflects_full_copper_and_round_trips(self):
        import pcbnew as k
        for direction in (k.FLIP_DIRECTION_LEFT_RIGHT, k.FLIP_DIRECTION_TOP_BOTTOM):
            for rotation in (0, 90, 180, 270):
                with self.subTest(direction=direction, rotation=rotation):
                    source = self.source(rotation=rotation)
                    initial = next(f for f in extract(source)['footprints'] if f['ref'] == 'C1')
                    board = k.LoadBoard(str(source))
                    fp = next(f for f in board.GetFootprints() if f.GetReference() == 'C1')
                    fp.Flip(fp.GetPosition(), direction)
                    output = self.root / 'flipped.kicad_pcb'
                    k.SaveBoard(str(output), board)
                    flipped = next(f for f in extract(output)['footprints'] if f['ref'] == 'C1')
                    before, after = initial['pads'][0], flipped['pads'][0]
                    self.assertEqual((after['uuid'], after['number'], after['net']),
                                     (before['uuid'], before['number'], before['net']))
                    self.assertEqual(after['custom_geometry']['status'], 'proven')
                    self.assertEqual(len(after['custom_geometry']['primitives']), 4)
                    from decimal import Decimal
                    def mirrored(node):
                        if not isinstance(node, list):
                            return node
                        if node[0] in ('start', 'end', 'center', 'xy'):
                            return [node[0], node[1], str((-Decimal(node[2])).normalize())]
                        return [mirrored(n) for n in node]
                    expected_primitives = mirrored(before['custom_geometry']['primitives'])
                    for primitive in expected_primitives:
                        if primitive[0] == 'gr_rect':
                            start = next(n for n in primitive if n[0] == 'start')
                            end = next(n for n in primitive if n[0] == 'end')
                            for axis in (1, 2):
                                start[axis], end[axis] = sorted((start[axis], end[axis]), key=Decimal)
                    self.assertEqual(after['custom_geometry']['primitives'], expected_primitives)
                    self.assertEqual(after['layers'], ['B.Cu', 'B.Mask', 'B.Paste'])
                    points = lambda p: sorted(tuple(x) for poly in p['custom_geometry']['merged_polygons_nm'] for x in poly)
                    self.assertEqual(points(after), sorted((x, -y) for x, y in points(before)))
                    box = before['custom_geometry']['bbox_nm']
                    x, y = fp.GetPosition().x, fp.GetPosition().y
                    expected = ([2*x-box[2], box[1], 2*x-box[0], box[3]]
                                if direction == k.FLIP_DIRECTION_LEFT_RIGHT else
                                [box[0], 2*y-box[3], box[2], 2*y-box[1]])
                    self.assertEqual(after['custom_geometry']['bbox_nm'], expected)
                    fp.Flip(fp.GetPosition(), direction)
                    k.SaveBoard(str(output), board)
                    restored = next(f for f in extract(output)['footprints'] if f['ref'] == 'C1')
                    self.assertEqual(restored['pads'], initial['pads'])

    def flipped_with_arc(self, rotation=0, back=False, direction=None):
        import pcbnew as k
        source = self.source(rotation=rotation, back=back)
        board = k.LoadBoard(str(source))
        fp = next(f for f in board.GetFootprints() if f.GetReference() == 'C1')
        arc = k.PCB_SHAPE(fp)
        arc.SetShape(k.SHAPE_T_ARC)
        arc.SetLayer(k.B_SilkS if back else k.F_SilkS)
        arc.SetWidth(120000)
        arc.SetArcGeometry(k.VECTOR2I(200000, -1000000), k.VECTOR2I(800000, -600000),
                           k.VECTOR2I(1200000, 300000))
        fp.Add(arc)
        k.SaveBoard(str(source), board)
        before = source.read_bytes()
        board = k.LoadBoard(str(source))
        fp = next(f for f in board.GetFootprints() if f.GetReference() == 'C1')
        fp.Flip(fp.GetPosition(), k.FLIP_DIRECTION_TOP_BOTTOM if direction is None else direction)
        fp.SetPosition(k.VECTOR2I(12000000, 13000000))
        fp.SetOrientationDegrees(fp.GetOrientationDegrees() + 90)
        output = self.root / 'scope-flipped.kicad_pcb'
        k.SaveBoard(str(output), board)
        return before, output.read_bytes()

    def test_writer_scope_custom_flip_arcs_all_turns_sides_and_directions(self):
        import pcbnew as k
        from agent_ee.placement_adapter import write_scope
        for rotation in (0, 90, 180, 270):
            for back in (False, True):
                for direction in (k.FLIP_DIRECTION_TOP_BOTTOM, k.FLIP_DIRECTION_LEFT_RIGHT):
                    with self.subTest(rotation=rotation, back=back, direction=direction):
                        before, after = self.flipped_with_arc(rotation, back, direction)
                        report = write_scope(before, after)
                        self.assertEqual(report['status'], 'allowed', report)
                        self.assertTrue(report['transforms'][0]['flipped'])
                        saved = self.root / 'scope-flipped.kicad_pcb'
                        board = k.LoadBoard(str(saved))
                        fp = next(f for f in board.GetFootprints() if f.GetReference() == 'C1')
                        fp.Flip(fp.GetPosition(), direction)
                        k.SaveBoard(str(saved), board)
                        reverse = write_scope(after, saved.read_bytes())
                        self.assertEqual(reverse['status'], 'allowed', reverse)

    def test_writer_scope_refuses_every_custom_flip_mutation(self):
        import copy
        from agent_ee import placement_adapter as pa
        from agent_ee.scaffold import sexpr
        before, after = self.flipped_with_arc()
        from agent_ee.schematic_emit import parse_quoted
        root = parse_quoted(after)
        control = pa.write_scope(before, sexpr(root))
        self.assertEqual(control['status'], 'allowed', control)
        for mutation in ('arc-radius', 'arc-angle', 'arc-width', 'arc-layer', 'arc-drop', 'arc-add',
                         'primitive-drop', 'primitive-add', 'primitive-move', 'primitive-width',
                         'pad-number', 'pad-net', 'pad-shape', 'pad-anchor', 'pad-layer'):
            altered = copy.deepcopy(root)
            fp = next(f for f in pa.footprints(altered).values() if pa._reference(f) == 'C1')
            arc = pa.children(fp, 'fp_arc')[0]
            pad = pa.children(fp, 'pad')[0]
            primitives = pa.children(pad, 'primitives')[0]
            if mutation in ('arc-radius', 'arc-angle'):
                point = pa.children(arc, 'mid' if mutation == 'arc-radius' else 'end')[0]
                point[1] = str(float(point[1]) + 0.01)
            elif mutation == 'arc-width':
                pa.children(pa.children(arc, 'stroke')[0], 'width')[0][1] = '0.13'
            elif mutation == 'arc-layer':
                pa.children(arc, 'layer')[0][1] = 'B.Fab'
            elif mutation == 'arc-drop':
                fp.remove(arc)
            elif mutation == 'arc-add':
                extra = copy.deepcopy(arc)
                pa.children(extra, 'uuid')[0][1] = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
                fp.append(extra)
            elif mutation == 'primitive-drop':
                primitives.pop()
            elif mutation == 'primitive-add':
                primitives.append(copy.deepcopy(primitives[-1]))
            elif mutation == 'primitive-move':
                line = pa.children(primitives, 'gr_line')[0]
                pa.children(line, 'start')[0][1] = '-1.9'
            elif mutation == 'primitive-width':
                pa.children(pa.children(primitives, 'gr_line')[0], 'width')[0][1] = '0.5'
            elif mutation == 'pad-number':
                pad[1] = '99'
            elif mutation == 'pad-net':
                pa.children(pad, 'net')[0][1] = 'CHANGED'
            elif mutation == 'pad-shape':
                pad[3] = 'rect'
            elif mutation == 'pad-anchor':
                pa.children(pa.children(pad, 'options')[0], 'anchor')[0][1] = 'circle'
            else:
                pa.children(pad, 'layers')[0][1] = 'F.Cu'
            with self.subTest(mutation=mutation):
                report = pa.write_scope(before, sexpr(altered))
                self.assertEqual(report['status'], 'refused', report)
                self.assertTrue(report['violations'])

    def test_writer_scope_native_failure_and_missing_output_refuse(self):
        from agent_ee import placement_adapter as pa
        before, after = self.flipped_with_arc()
        for record in ({'execution': 'failed'}, {'execution': 'ok'}):
            with self.subTest(record=record):
                def failed(inputs, work, argv, **kwargs):
                    work.mkdir()
                    (work / 'console.log').write_text('deliberate test failure')
                    return record
                with patch('agent_ee.executor.execute', failed):
                    report = pa.write_scope(before, after)
                self.assertEqual(report['status'], 'refused', report)
                self.assertTrue(any('probe' in v for v in report['violations']))

    def test_unsupported_arc_and_thermal_connection_stay_unknown(self):
        for n, primitive in enumerate(('(gr_arc (start 0 0) (mid 1 1) (end 2 0) (width 0.2))',
                                       PRIMITIVES['rect'])):
            source = self.source(primitive)
            if n:
                source.write_text(source.read_text().replace('(zone_connect 2)', '(zone_connect 1)'))
            result = pack(extract(source), request())
            self.assertEqual((result['status'], result['outcome']), ('unsupported_geometry', 'unknown'))
            self.assertEqual(result['placements'], [])

    def test_malformed_custom_evidence_remains_typed_unknown(self):
        import copy
        facts = extract(self.source())
        for evidence in (None, [], {}, {'status': 'proven', 'primitives': [1],
                                       'merged_polygons_nm': [1], 'bbox_nm': None}):
            altered = copy.deepcopy(facts)
            pad = next(f for f in altered['footprints'] if f['ref'] == 'C1')['pads'][0]
            pad['custom_geometry'] = evidence
            result = pack(altered, request())
            self.assertEqual((result['status'], result['outcome']), ('unsupported_geometry', 'unknown'))
            self.assertEqual(result['placements'], [])

    def test_changed_dropped_moved_primitive_and_anchor_refuse_publication(self):
        import pcbnew as k
        import agent_ee.placer_runner as runner
        for mutation in ('change', 'drop', 'move', 'anchor'):
            with self.subTest(mutation=mutation):
                source = self.source()
                def corrupt(board, plan, output):
                    _apply_plan(board, plan, output)
                    text = output.read_text()
                    if mutation == 'drop':
                        # Drop the rectangle duplicated by the polygon: merged
                        # copper stays identical, so compare every primitive.
                        import re
                        before = next(f for f in extract(output)['footprints'] if f['ref'] == 'C1')['pads'][0]
                        text = re.sub(r'\(gr_rect\s+\(start[^)]*\)\s+\(end[^)]*\)\s+\(width[^)]*\)\s+\(fill[^)]*\)\s*\)', '', text)
                    elif mutation == 'anchor':
                        text = text.replace('(anchor rect)', '(anchor circle)')
                    elif mutation == 'change':
                        text = text.replace('(width 0.4)', '(width 0.5)')
                    else:
                        text = text.replace('(start -2 0)', '(start -1.9 0)')
                    self.assertNotEqual(text, output.read_text())
                    output.write_text(text)
                    if mutation == 'drop':
                        after = next(f for f in extract(output)['footprints'] if f['ref'] == 'C1')['pads'][0]
                        self.assertEqual(after['custom_geometry']['merged_polygons_nm'],
                                         before['custom_geometry']['merged_polygons_nm'])
                        self.assertEqual(after['custom_geometry']['bbox_nm'], before['custom_geometry']['bbox_nm'])
                destination = self.scratch / mutation
                with patch.object(runner, '_apply_plan', corrupt):
                    with self.assertRaisesRegex(ValueError, 'changed footprint pad geometry|copper extent differs|merged copper missing'):
                        place_candidate(source, self.netlist, destination, self.config(source))
                self.assertFalse(destination.exists())
                self.assertEqual(len(list(self.scratch.glob(mutation + '-failure-*'))), 1)
