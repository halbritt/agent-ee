"""Pinned profile contract and independently generated native geometry."""
import unittest
from agent_ee.fab_profile import compile_profile


class FabProfileTest(unittest.TestCase):
    def test_missing_profile_never_chooses_defaults(self):
        with self.assertRaises(ValueError):
            compile_profile(None, {}, None)

    def test_each_missing_source_is_unsupported_not_defaulted(self):
        document = {'schema': 'agent-ee.fab-profile/1', 'name': 'owned_standard_2026-10',
                    'as_of': '2026-10-17', 'limits': [
                        {'id': 'trace', 'requirement': 'fab', 'key': 'min_trace_mm',
                         'value': 0.1, 'unit': 'mm', 'tier': 'minimum'}]}
        compiled = compile_profile(document, {}, None)
        self.assertEqual(compiled['constraints'][0]['execution'], 'unsupported')
        self.assertNotIn('rules', compiled['constraints'][0])


def owned_profile(inputs, specs):
    """Explicit test assumptions, not specifications for any real fabricator."""
    from agent_ee.storage import digest
    source = inputs / 'owned-capabilities.txt'
    source.write_text('Owned synthetic capability assumptions, 2026-10-17.\n' + '\n'.join(
        f'{key}: {value} {unit}; {tier}' for key, value, unit, tier in specs))
    limits = []
    for index, (key, value, unit, tier) in enumerate(specs):
        limits.append({'id': f'limit-{index}', 'requirement': 'fab', 'key': key,
                       'value': value, 'unit': unit, 'tier': tier,
                       'source': {'path': source.name, 'sha256': digest(source),
                                  'source': 'Owned synthetic assumptions (not a real fab)',
                                  'as_of': '2026-10-17', 'page': 1,
                                  'quote': f'{key}: {value} {unit}; {tier}'}})
    return {'schema': 'agent-ee.fab-profile/1', 'name': 'owned_standard_2026-10',
            'as_of': '2026-10-17', 'limits': limits}


class FabSourceBoundaryTest(unittest.TestCase):
    def test_capture_hash_date_page_and_quote_bind_every_limit(self):
        import copy
        import tempfile
        from pathlib import Path
        from agent_ee.storage import digest
        with tempfile.TemporaryDirectory() as tmp:
            inputs = Path(tmp)
            document = owned_profile(inputs, [('min_trace_mm', 0.1, 'mm', 'minimum')])
            files = {p.name: digest(p) for p in inputs.iterdir()}
            good = compile_profile(document, files, inputs)
            self.assertEqual(good['constraints'][0]['execution'], 'ready')
            for field, bad in (('sha256', '0'*64), ('as_of', '2026-01-01'),
                               ('page', 2), ('quote', 'missing')):
                changed = copy.deepcopy(document)
                changed['limits'][0]['source'][field] = bad
                compiled = compile_profile(changed, files, inputs)
                self.assertEqual(compiled['constraints'][0]['execution'], 'unsupported', field)
            (inputs / 'owned-capabilities.txt').write_text('changed')
            self.assertEqual(compile_profile(document, files, inputs)['constraints'][0]['execution'], 'unsupported')

    def test_raw_pdf_is_not_treated_as_qualified_paginated_text(self):
        import tempfile
        from pathlib import Path
        from agent_ee.storage import digest
        with tempfile.TemporaryDirectory() as tmp:
            inputs = Path(tmp)
            document = owned_profile(inputs, [('min_trace_mm', 0.1, 'mm', 'minimum')])
            source = inputs / 'owned-capabilities.txt'
            source.write_text('%PDF-1.7\n' + source.read_text())
            document['limits'][0]['source']['sha256'] = digest(source)
            files = {p.name: digest(p) for p in inputs.iterdir()}
            self.assertEqual(compile_profile(document, files, inputs)['constraints'][0]['execution'], 'unsupported')


def ring(*points):
    """Closed segment list with locator names e0..eN in drawing order."""
    return [(f'e{i}', a, b) for i, (a, b) in enumerate(zip(points, points[1:] + points[:1]))]


class OutlinePolygonTest(unittest.TestCase):
    def assertRefused(self, segments, reason, locators):
        from agent_ee.fab_profile import simple_polygon
        outline, problem = simple_polygon(segments)
        self.assertIsNone(outline)
        self.assertIn(reason, problem[0])
        self.assertEqual(problem[1], sorted(locators))

    def test_simple_straight_polygons_are_measured(self):
        from agent_ee.fab_profile import simple_polygon
        square = ring((0, 0), (10_000_000, 0), (10_000_000, 4_000_000), (0, 4_000_000))
        reversed_side = square[:1] + [('e1', square[1][2], square[1][1])] + square[2:]
        split_side = ring((0, 0), (5_000_000, 0), (10_000_000, 0), (10_000_000, 4_000_000), (0, 4_000_000))
        concave = ring((0, 0), (8_000_000, 0), (8_000_000, 2_000_000), (2_000_000, 2_000_000),
                       (2_000_000, 6_000_000), (0, 6_000_000))
        triangle = ring((0, 0), (3_000_000, 1_000_000), (1_000_000, 5_000_000))
        for segments, extents in ((square, [10, 4]), (square[::-1], [10, 4]), (reversed_side, [10, 4]),
                                  (split_side, [10, 4]), (concave, [8, 6]), (triangle, [3, 5])):
            self.assertEqual(simple_polygon(segments), (extents, None), segments)

    def test_degenerate_rings_and_duplicates_are_refused_with_locators(self):
        a, b, c = (0, 0), (10, 0), (10, 10)
        self.assertRefused([('only', a, a)], 'zero-length', ['only'])
        self.assertRefused([('x', a, b), ('y', a, b)], 'duplicate', ['x', 'y'])
        self.assertRefused([('x', a, b), ('y', b, a)], 'duplicate', ['x', 'y'])
        self.assertRefused(ring(a, b, c) + [('e9', c, a)], 'duplicate', ['e2', 'e9'])
        self.assertRefused(ring(a, (5, 0), b), 'overlapping', ['e0', 'e2'])

    def test_crossing_touching_and_overlapping_edges_are_refused(self):
        self.assertRefused(ring((0, 0), (10, 10), (0, 10), (10, 0)), 'self-intersecting', ['e0', 'e2'])
        # Vertex (5,0) lies inside non-adjacent edge e0 without sharing an endpoint.
        self.assertRefused(ring((0, 0), (10, 0), (10, 10), (5, 0), (0, 10)), 'self-intersecting', ['e0', 'e2'])
        # Non-adjacent collinear overlap: e4 runs back along part of e0.
        notch = ring((0, 0), (10, 0), (10, -5), (8, -5), (8, 0), (3, 0), (3, 5), (0, 5))
        self.assertRefused(notch[4:] + notch[:4], 'overlapping', ['e0', 'e4'])
        # Adjacent fold-back spike: e2 retraces part of e1.
        self.assertRefused(ring((0, 0), (10, 0), (10, 10), (10, 5), (0, 5)), 'overlapping', ['e1', 'e2'])

    def test_open_branched_multiple_and_vertex_touching_outlines_are_refused(self):
        square = ring((0, 0), (10, 0), (10, 10), (0, 10))
        hole = [(f'h{i}', a, b) for i, (_, a, b) in enumerate(ring((2, 2), (4, 2), (4, 4), (2, 4)))]
        self.assertRefused(square[:-1], 'open', ['e0'])
        self.assertRefused(square + hole, 'multiple closed', ['h0', 'h1', 'h2', 'h3'])
        figure_eight = ring((0, 0), (10, 0), (10, 10)) + [
            ('f0', (10, 10), (20, 10)), ('f1', (20, 10), (20, 20)), ('f2', (20, 20), (10, 10))]
        self.assertRefused(figure_eight, 'vertex-touching', ['e1', 'e2', 'f0', 'f2'])

    def test_result_and_locators_do_not_depend_on_drawing_order(self):
        import itertools
        from agent_ee.fab_profile import simple_polygon
        bowtie = ring((0, 0), (10, 10), (0, 10), (10, 0))
        results = {repr(simple_polygon(list(order))) for order in itertools.permutations(bowtie)}
        self.assertEqual(len(results), 1, results)


class FabClassificationTest(unittest.TestCase):
    @staticmethod
    def record(rid, key, tier, status, **fields):
        return {'id': rid, 'requirement': 'fab', 'expected': {'key': key, 'tier': tier},
                'status': status, 'execution': 'ok', **fields}

    def test_preferred_residual_keeps_minimum_offenders_visible(self):
        from agent_ee.fab_profile import classify_preferred
        finding = lambda *uuids: {'items': [{'uuid': u} for u in uuids]}
        for preferred_items, expected in ((('a', 'b'), ('extra_charge', ['b'], ['a'])),
                                          (('a',), ('satisfied', [], ['a']))):
            records = [self.record('min', 'min_trace_mm', 'minimum', 'violation', native_findings=[finding('a')]),
                       self.record('pref', 'min_trace_mm', 'preferred', 'violation',
                                   native_findings=[finding(u) for u in preferred_items])]
            preferred = classify_preferred(records)[1]
            self.assertEqual((preferred['status'], preferred['offender_ids'],
                              preferred['minimum_violation_ids']), expected)

    def test_edge_clearance_needs_qualified_outline_when_inventory_is_known(self):
        from agent_ee.fab_profile import classify_preferred
        def records():
            return [self.record('edge', 'min_edge_clearance_mm', 'minimum', 'satisfied'),
                    self.record('edge-pref', 'min_edge_clearance_mm', 'preferred', 'violation',
                                native_findings=[{'items': [{'uuid': 't'}]}]),
                    self.record('trace', 'min_trace_mm', 'minimum', 'satisfied')]
        missing = {'outline_xy_mm': None, 'outline_problem': {'reason': 'no Edge.Cuts outline',
                                                              'locator_ids': []}}
        checked = classify_preferred(records(), missing)
        self.assertEqual([r['status'] for r in checked], ['not_evaluated', 'not_evaluated', 'satisfied'])
        self.assertIn('no Edge.Cuts outline', checked[0]['reason'])
        qualified = classify_preferred(records(), {'outline_xy_mm': [10, 10]})
        self.assertEqual([r['status'] for r in qualified], ['satisfied', 'extra_charge', 'satisfied'])
        violated = records()
        violated[0].update(status='violation', native_findings=[{'items': [{'uuid': 't'}]}])
        self.assertEqual(classify_preferred(violated, missing)[0]['status'], 'violation')

    def test_board_size_allows_quarter_turn_but_not_larger_extents(self):
        from agent_ee.fab_profile import evaluate_geometry
        constraint = {'id': 'size', 'requirement': 'fab', 'expected': {'key': 'max_board_mm', 'value': [15, 20]}}
        inventory = {'outline_ids': ['e'], 'outline_xy_mm': [20, 15]}
        self.assertEqual(evaluate_geometry(constraint, inventory)['status'], 'satisfied')
        inventory['outline_xy_mm'] = [20.5, 14]
        result = evaluate_geometry(constraint, inventory)
        self.assertEqual((result['status'], result['offender_ids']), ('violation', ['e']))
        inventory.update(outline_xy_mm=None, outline_problem={'reason': 'zero-area', 'locator_ids': ['e']})
        result = evaluate_geometry(constraint, inventory)
        self.assertEqual((result['status'], result['execution'], result['locator_ids']),
                         ('not_evaluated', 'unsupported', ['e']))


DUPLICATE = '00000000-0000-0000-0000-000000000007'


def outline_board(path, segments, *, arc=None, footprint_edge=False, duplicate_line=False, **options):
    """Owned fixture board whose Edge.Cuts are replaced by exact saved shapes (mm).

    pcbnew drops an identical same-direction segment on save, so a coincident
    duplicate is written as a second saved gr_line, as a hand edit would be.
    """
    import pcbnew as k
    from routing_fixture import make_board
    make_board(path, **options)
    board = k.LoadBoard(str(path))
    for item in list(board.GetDrawings()):
        if item.GetLayer() == k.Edge_Cuts:
            board.Remove(item)
    point = lambda xy: k.VECTOR2I(round(xy[0]*1e6), round(xy[1]*1e6))
    def shape(parent, kind):
        item = k.PCB_SHAPE(parent); item.SetShape(kind); item.SetLayer(k.Edge_Cuts); item.SetWidth(50000)
        return item
    for start, end in segments:
        edge = shape(board, k.SHAPE_T_SEGMENT); edge.SetStart(point(start)); edge.SetEnd(point(end)); board.Add(edge)
    if arc:
        edge = shape(board, k.SHAPE_T_ARC); edge.SetArcGeometry(*map(point, arc)); board.Add(edge)
    if footprint_edge:
        footprint = k.FOOTPRINT(board); footprint.SetReference('H1'); board.Add(footprint)
        edge = shape(footprint, k.SHAPE_T_SEGMENT); edge.SetStart(point((14, 8))); edge.SetEnd(point((15, 8)))
        footprint.Add(edge)
    k.SaveBoard(str(path), board)
    if duplicate_line:
        import re
        text = path.read_text(encoding='utf-8')
        line = re.search(r'\t\(gr_line\n.*?\n\t\)\n', text, re.S).group(0)
        copy = re.sub(r'\(uuid "[^"]+"\)', f'(uuid "{DUPLICATE}")', line)
        path.write_text(text.replace(line, line + copy, 1), encoding='utf-8')


def closed(*points):
    return list(zip(points, points[1:] + points[:1]))


class NativeOutlineTest(unittest.TestCase):
    """Actual saved+loaded pcbnew outlines; locators are the saved Edge.Cuts UUIDs."""
    @classmethod
    def setUpClass(cls):
        from toolchain import require_toolchain
        require_toolchain(native=True)

    def inventory(self, segments, **options):
        import tempfile
        from pathlib import Path
        from agent_ee.fab_profile import native_inventory
        import pcbnew as k
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'test.kicad_pcb'
            outline_board(path, segments, **options)
            inventory = native_inventory(path)
            self.assertEqual(native_inventory(path), inventory)
            text = path.read_text(encoding='utf-8')
            for locator in (*inventory['outline_ids'], *inventory.get('outline_problem', {}).get('locator_ids', [])):
                self.assertIn(f'(uuid "{locator}")', text)
            saved = {e.m_Uuid.AsString(): ((e.GetStart().x, e.GetStart().y), (e.GetEnd().x, e.GetEnd().y))
                     for e in k.LoadBoard(str(path)).GetDrawings() if e.GetLayer() == k.Edge_Cuts}
        self.assertEqual(inventory['outline_ids'], sorted(saved))
        return inventory, saved

    def refused(self, segments, reason, **options):
        inventory, saved = self.inventory(segments, **options)
        self.assertIsNone(inventory['outline_xy_mm'], inventory)
        self.assertIn(reason, inventory['outline_problem']['reason'])
        return inventory['outline_problem']['locator_ids'], saved

    def test_crossing_zero_area_and_duplicate_rings_are_unqualified(self):
        mm = lambda *xy: tuple(round(v*1e6) for v in xy)
        locators, saved = self.refused(closed((0, 0), (10, 10), (0, 10), (10, 0)), 'self-intersecting')
        self.assertEqual({saved[i] for i in locators}, {(mm(0, 0), mm(10, 10)), (mm(0, 10), mm(10, 0))})
        locators, saved = self.refused(closed((0, 0), (5, 0), (10, 0)), 'overlapping')
        self.assertIn((mm(10, 0), mm(0, 0)), {saved[i] for i in locators})
        self.assertEqual(len(locators), 2)
        locators, saved = self.refused([((0, 0), (10, 0)), ((10, 0), (0, 0))], 'duplicate')
        self.assertEqual(locators, sorted(saved))
        locators, saved = self.refused(closed((0, 0), (10, 0), (10, 10), (0, 10)), 'duplicate', duplicate_line=True)
        self.assertIn(DUPLICATE, locators)
        self.assertEqual(len(set(saved[i] for i in locators)), 1)
        locators, saved = self.refused(closed((0, 0), (10, 0), (10, 10)) + [((0, 0), (10, 10))], 'duplicate')
        self.assertEqual({saved[i] for i in locators}, {(mm(10, 10), mm(0, 0)), (mm(0, 0), mm(10, 10))})

    def test_curves_cutouts_and_footprint_edges_are_explicitly_unsupported(self):
        square = closed((-2, -2), (18, -2), (18, 13), (-2, 13))
        locators, saved = self.refused(square[:-1], 'straight', arc=((-2, 13), (-4, 5.5), (-2, -2)))
        self.assertEqual(len(locators), 1)
        self.refused(square + closed((14, 7), (16, 7), (16, 9), (14, 9)), 'multiple closed')
        locators, saved = self.refused(square, 'footprint', footprint_edge=True)
        self.assertEqual(len(locators), 1)
        self.assertNotIn(locators[0], saved)

    def test_layer_locators_are_saved_canonical_copper_names(self):
        import tempfile
        from pathlib import Path
        from routing_fixture import make_board
        from agent_ee.fab_profile import native_inventory
        import pcbnew as k
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'test.kicad_pcb'
            make_board(path, layers=4)
            board = k.LoadBoard(str(path)); board.SetLayerName(k.F_Cu, 'Top'); k.SaveBoard(str(path), board)
            inventory = native_inventory(path)
            self.assertEqual(native_inventory(path), inventory)
            self.assertEqual((inventory['copper_layers'], sorted(inventory['copper_layer_names'])),
                             (4, ['B.Cu', 'F.Cu', 'In1.Cu', 'In2.Cu']))
            self.assertIn('"F.Cu" signal "Top"', path.read_text(encoding='utf-8'))

    def test_simple_concave_outline_is_measured(self):
        inventory, _ = self.inventory(closed((-2, -2), (18, -2), (18, 6), (8, 6), (8, 13), (-2, 13)))
        self.assertEqual((inventory['outline_xy_mm'], inventory.get('outline_problem')), ([20.0, 15.0], None))


class NativeFabTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from toolchain import require_toolchain
        require_toolchain(native=True)

    def run_profile(self, specs, *, unsupported=False, builder=None, **board_options):
        import json
        import tempfile
        import time
        from pathlib import Path
        from routing_fixture import make_board, manifest_for_rules, assert_sealed_native
        from agent_ee.checks import Ctx
        from agent_ee.fab_profile import check_fab_profile
        from agent_ee.storage import digest
        root = Path(tempfile.mkdtemp(prefix='agent-ee-fab-native-'))
        self.addCleanup(__import__('shutil').rmtree, root)
        inputs = root / 'inputs'; inputs.mkdir()
        (builder or make_board)(inputs / 'test.kicad_pcb', **board_options)
        document = owned_profile(inputs, specs)
        if unsupported:
            document['limits'][0].pop('source')
        (inputs / 'profile.json').write_text(json.dumps(document))
        (inputs / 'requirements.txt').write_text('Owned fabricator requirement.')
        files = {p.name: digest(p) for p in inputs.iterdir()}
        reqs = [{'id': 'fab', 'checks': ['fab'],
                 'source': {'path': 'requirements.txt', 'locator': 'Owned'}}]
        spec = {'id': 'fab', 'kind': 'fab_profile', 'params': {'fab_profile': 'profile.json'}}
        manifest = manifest_for_rules(inputs, files, spec, reqs)
        ctx = Ctx(root, manifest, spec, time.monotonic()+900)
        check_fab_profile(ctx)
        assert_sealed_native(self, ctx)
        self.assertEqual(files, {p.name: digest(p) for p in inputs.iterdir()})
        self.assertEqual(ctx.result['details']['source']['sha256'], files['profile.json'])
        return ctx.result

    def test_native_trace_minimum_and_preferred(self):
        specs = [('min_trace_mm', 0.1, 'mm', 'minimum'), ('min_trace_mm', 0.2, 'mm', 'preferred')]
        illegal = self.run_profile(specs, width=0.09)
        self.assertEqual((illegal['execution'], illegal['outcome']), ('ok', 'defect'), illegal)
        self.assertEqual(illegal['details']['extra_charges'], [], illegal)
        preferred = self.run_profile(specs, width=0.15)
        self.assertEqual((preferred['execution'], preferred['outcome']), ('ok', 'pass'), preferred)
        self.assertTrue(preferred['details']['extra_charges'][0]['offender_ids'], preferred)

    def test_native_drill_minimum_and_extra_charge(self):
        specs = [('min_via_drill_mm', 0.2, 'mm', 'minimum'), ('min_via_drill_mm', 0.3, 'mm', 'preferred')]
        for drill, expected in ((0.15, 'defect'), (0.25, 'pass')):
            result = self.run_profile(specs, vias=1, drill=drill)
            self.assertEqual((result['execution'], result['outcome']), ('ok', expected), result)
            if expected == 'pass':
                self.assertEqual(len(result['details']['extra_charges'][0]['offender_ids']), 2, result)

    def test_native_layer_and_outline_offenders(self):
        specs = [('max_layers', 2, 'count', 'minimum'), ('max_board_mm', [15, 10], 'mm', 'minimum')]
        result = self.run_profile(specs, layers=4, outline=(20, 15))
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)
        for c in result['details']['constraints']:
            self.assertEqual(c['status'], 'violation', c)
            self.assertTrue(c['offender_ids'], c)
        layers = next(c for c in result['details']['constraints'] if c['expected']['key'] == 'max_layers')
        self.assertEqual(sorted(layers['offender_ids']), ['B.Cu', 'F.Cu', 'In1.Cu', 'In2.Cu'], layers)
        good = self.run_profile(specs, outline=(14, 9))
        self.assertEqual((good['execution'], good['outcome']), ('ok', 'pass'), good)

    def test_native_unsourced_limit_is_unknown(self):
        result = self.run_profile([('min_trace_mm', 0.1, 'mm', 'minimum')], unsupported=True)
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)

    def test_native_other_compiled_limits(self):
        for key, bound, options in (
            ('min_via_pad_mm', 0.8, {'vias': 1}),
            ('min_hole_to_hole_mm', 3.5, {'vias': 1}),
            ('min_edge_clearance_mm', 3, {}),
            ('min_clearance_mm', 4, {}),
            ('supports_smt', False, {}),
        ):
            with self.subTest(key=key):
                unit = 'boolean' if key == 'supports_smt' else 'mm'
                result = self.run_profile([(key, bound, unit, 'minimum')], **options)
                self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)

    def test_native_open_outline_cannot_pass_size_check(self):
        result = self.run_profile([('max_board_mm', [100, 100], 'mm', 'minimum')], open_outline=True)
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)

    def test_native_preferred_requires_proven_minimum(self):
        result = self.run_profile([('min_trace_mm', 0.2, 'mm', 'preferred')], width=0.15)
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)

    def test_geometry_surcharge_preserves_fact_and_drc_distinction(self):
        result = self.run_profile([('max_layers', 4, 'count', 'minimum'),
                                   ('max_layers', 2, 'count', 'preferred')], layers=4)
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        self.assertTrue(result['details']['extra_charges'][0]['offender_ids'], result)
        for record in result['details']['constraints']:
            self.assertNotIn('native_findings', record)

    def test_native_malformed_outline_cannot_pass_size_check(self):
        bowtie = closed((-2, -2), (18, 13), (-2, 13), (18, -2))
        result = self.run_profile([('max_board_mm', [100, 100], 'mm', 'minimum')],
                                  builder=outline_board, segments=bowtie)
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)
        record = result['details']['constraints'][0]
        self.assertIn('self-intersecting', record['reason'])
        self.assertEqual(len(record['locator_ids']), 2, record)

    def test_native_concave_outline_and_quarter_turn_size(self):
        concave = closed((-2, -2), (18, -2), (18, 6), (8, 6), (8, 13), (-2, 13))
        for limit, outcome in (([15, 20], 'pass'), ([19, 16], 'defect')):
            result = self.run_profile([('max_board_mm', limit, 'mm', 'minimum')],
                                      builder=outline_board, segments=concave)
            self.assertEqual((result['execution'], result['outcome']), ('ok', outcome), result)
