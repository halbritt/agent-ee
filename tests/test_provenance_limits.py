"""Regression probes for AGENTEE-331 masked limits and structural invariants."""
import copy
from pathlib import Path
import tempfile
import unittest

from agent_ee.design_rule_provenance import (
    advisory_numeric, bind_project, classify_finding, finding_sources, import_constraints)
from agent_ee.placer_runner import _drc_comparison
from agent_ee.storage import json_bytes, write_json


def source(field, value, cls):
    return dict(setting=field, value=value, **{'class': cls}, citation={'path': 'limits.json'})


class ProvenanceLimitsTest(unittest.TestCase):
    def test_actual_value_checks_every_applicable_limit(self):
        sources = {'rules': {}, 'settings': [
            source('board.design_settings.rules.min_clearance', .09, 'manufacturer'),
            source('net_settings.classes.Default.clearance', .2, 'default')]}
        for actual, expected in ((.05, 'manufacturer'), (.09, 'default'), (.15, 'default')):
            with self.subTest(actual=actual):
                finding = dict(type='clearance', description=
                    f"Clearance violation (netclass 'Default' clearance 0.2000 mm; actual {actual:.4f} mm)",
                    items=[{'uuid': 'pad'}])
                delta = _drc_comparison({'ignored_checks': [], 'violations': []},
                    {'ignored_checks': [], 'violations': [finding]}, sources=sources)
                self.assertEqual(finding['constraint_provenance']['class'], expected)
                self.assertEqual(bool(delta['blocking_findings']), expected != 'default')
        sources['item_classes'] = {'pad': 'Default'}
        sources['settings'].append(source('net_settings.classes.Default.clearance', .12, 'owner'))
        finding['description'] = "Clearance violation (board setup constraints clearance 0.2000 mm; actual 0.0500 mm)"
        classify_finding(finding, sources)
        self.assertEqual(finding['constraint_provenance']['class'], 'owner')
        self.assertEqual(len(finding['constraint_provenance']['limits']), 2)

    def test_coincident_holes_are_structural_even_with_default_rule(self):
        finding = dict(type='holes_co_located', description='Drilled holes co-located', items=[{'uuid': 'h'}])
        classified = classify_finding(finding)
        self.assertEqual(classified['constraint_provenance']['class'], 'engineering')
        self.assertFalse(advisory_numeric(classified))
        delta = _drc_comparison({'ignored_checks': [], 'violations': []},
            {'ignored_checks': [], 'violations': [finding]})
        self.assertEqual(delta['blocking_findings'], [finding])

    def test_thermal_spoke_minimum_is_numeric_without_millimetres(self):
        sources = {'rules': {}, 'settings': [
            source('board.design_settings.rules.min_resolved_spokes', 2, 'manufacturer')]}
        for actual, expected in ((1, 'manufacturer'), (3, 'default')):
            finding = dict(type='starved_thermal', description=
                f'Thermal relief connection incomplete (layer In1.Cu; zone min spoke count 4; actual {actual})')
            classify_finding(finding, sources)
            self.assertEqual(finding['constraint_provenance']['class'], expected)
            self.assertEqual(advisory_numeric(finding), expected=='default')
        unknown = classify_finding(dict(type='starved_thermal', description=
            'Thermal relief connection incomplete (layer In1.Cu; zone min spoke count 2; actual 1)'))
        self.assertTrue(advisory_numeric(unknown))

    def test_layer_scoped_spoke_rule_is_not_invented_as_a_global_floor(self):
        with tempfile.TemporaryDirectory() as temporary:
            board = Path(temporary)/'b.kicad_pcb'
            board.write_text('(kicad_pcb)')
            board.with_suffix('.kicad_dru').write_text(
                '(version 1) (rule "scoped count" (layer "F.Cu") '
                '(constraint thermal_spoke_count (min 3)))')
            sources = finding_sources(board)
            self.assertEqual(sources['conditional_spoke_rules'], ['scoped count'])
            self.assertFalse(any(s['setting'].startswith('native_rules.') for s in sources['settings']))

    def test_malformed_spoke_measurements_never_become_advisory_passes(self):
        for description in ('', 'Thermal incomplete (zone min spoke count 2)',
                            'Thermal incomplete (zone min spoke count 2; actual -1)'):
            with self.subTest(description=description):
                with self.assertRaisesRegex(ValueError, 'thermal-spoke.*measurement'):
                    classify_finding(dict(type='starved_thermal', description=description))

    def test_unnamed_conditional_owned_spoke_limit_is_unavailable_coverage(self):
        from agent_ee.design_rule_provenance import require_evaluated_limits
        sources = {'rules': {}, 'settings': [], 'conditional_spoke_rules': ['owned count']}
        report = {'violations': [{'type': 'starved_thermal'}]}
        with self.assertRaisesRegex(ValueError, 'conditional.*spoke.*unavailable'):
            require_evaluated_limits(report, sources)
        require_evaluated_limits({'violations': []}, sources)
        require_evaluated_limits(report, {'rules': {}, 'settings': []})

    def test_ignored_coincident_drill_evaluation_is_never_a_pass(self):
        from agent_ee.design_rule_provenance import require_evaluated_limits
        for cls in ('default', 'manufacturer'):
            sources = {'settings': [source('board.design_settings.rules.min_hole_to_hole', .25, cls)],
                       'rules': {}}
            with self.subTest(cls=cls):
                with self.assertRaisesRegex(ValueError, 'explicitly ignored.*structural.*holes_co_located'):
                    require_evaluated_limits({'ignored_checks': [{'key': 'holes_co_located'}]}, sources)

    def test_copper_outside_outline_is_structural(self):
        for actual, expected in ((-.2, 'engineering'), (0, 'engineering'), (.2, 'default')):
            finding = dict(type='copper_edge_clearance', description=
                f'Board edge clearance violation (board setup constraints edge clearance 0.5000 mm; actual {actual:.4f} mm)',
                items=[{'uuid': 'pad'}])
            classify_finding(finding)
            self.assertEqual(finding['constraint_provenance']['class'], expected)
            self.assertEqual(advisory_numeric(finding), expected == 'default')

    def test_existing_advisory_identity_cannot_mask_new_hard_violation(self):
        for kind, before_actual, after_actual, setting, minimum in (
                ('clearance', .15, .05, 'min_clearance', .09),
                ('copper_edge_clearance', .2, -.2, 'min_copper_edge_clearance', .3)):
            with self.subTest(kind=kind):
                def finding(actual):
                    return dict(type=kind, description=
                        f'Violation (board setup constraints limit 0.5000 mm; actual {actual:.4f} mm)',
                        items=[{'uuid': 'pad'}])
                sources = {'settings': [source('board.design_settings.rules.'+setting, minimum, 'manufacturer')], 'rules': {}}
                before = {'ignored_checks': [], 'violations': [finding(before_actual)]}
                after = {'ignored_checks': [], 'violations': [finding(after_actual)]}
                delta = _drc_comparison(before, after, sources=sources)
                self.assertEqual(len(delta['blocking_findings']), 1, delta)
                # A byte-identical pre-existing finding is still baseline.
                same = _drc_comparison(copy.deepcopy(after), copy.deepcopy(after), sources=sources)
                self.assertEqual(same['added_findings'], [])

    def test_sourced_collision_identity_survives_rewording_but_not_a_changed_measurement(self):
        sources = {'settings': [source('board.design_settings.rules.min_clearance', .09, 'manufacturer'),
                                source('net_settings.classes.Default.clearance', .2, 'default')], 'rules': {}}

        def report(actual, wording='board setup constraints clearance 0.0900', pos=(1, 2)):
            return {'ignored_checks': [], 'violations': [dict(type='clearance', description=
                f'Clearance violation ({wording} mm; actual {actual:.4f} mm)',
                pos={'x': pos[0], 'y': pos[1]}, items=[{'uuid': 'a'}, {'uuid': 'b'}])]}
        before = report(.05)
        reworded = report(.05, "netclass 'Default' clearance 0.2000", pos=(7, 9))
        states = {'a': ('U1', 'pad', (0, 0, 0, 0)), 'b': ('U2', 'pad', (100, 0, 0, 0))}
        moved = {**states, 'b': ('U2', 'pad', (90, 0, 0, 0))}
        for after_states in (None, states):
            with self.subTest(states=after_states is not None):
                kept = _drc_comparison(before, copy.deepcopy(reworded), after_states, after_states, sources)
                self.assertEqual(kept['candidate_findings'][0]['constraint_provenance']['class'], 'manufacturer')
                self.assertEqual(kept['added_findings'], [])
        delta = _drc_comparison(before, copy.deepcopy(reworded), states, moved, sources)
        self.assertEqual(len(delta['blocking_findings']), 1, delta)
        # Without saved geometry a worsened or changed measurement is new, never baseline.
        for actual in (.03, .07):
            with self.subTest(actual=actual):
                delta = _drc_comparison(before, report(actual), sources=sources)
                self.assertEqual(delta['added_by_type'], {'clearance': 1})
                self.assertEqual(len(delta['blocking_findings']), 1, delta)

    def test_stateless_unmeasured_hard_collision_on_baseline_items_refuses(self):
        from agent_ee.placer_runner import CollisionMeasurementRefusal
        sources = {'settings': [source('board.design_settings.rules.min_clearance', .09, 'manufacturer')], 'rules': {}}

        def report(*findings):
            return {'ignored_checks': [], 'violations': [dict(type=kind, description=text,
                    items=[{'uuid': 'a'}, {'uuid': 'b'}]) for kind, text in findings]}
        short = ('shorting_items', 'Items shorting two nets (nets U1 and C1)')
        measured = ('clearance', 'Clearance violation (board setup constraints clearance 0.0900 mm; actual 0.0500 mm)')
        unmeasured = ('clearance', 'Clearance violation (board setup constraints clearance 0.0900 mm)')
        for label, before, after in (('short on baseline short', report(short), report(short)),
                                     ('measured then unmeasured', report(measured), report(unmeasured)),
                                     ('unmeasured on hard baseline', report(short, measured), report(short, measured))):
            with self.subTest(label):
                with self.assertRaisesRegex(CollisionMeasurementRefusal, 'native saved geometry required'):
                    _drc_comparison(before, copy.deepcopy(after), sources=sources)
        # An unmeasured hard collision without a baseline counterpart is determinately new.
        delta = _drc_comparison(report(), report(short), sources=sources)
        self.assertEqual([v['type'] for v in delta['blocking_findings']], ['shorting_items'])
        # An unmeasured advisory collision never blocks and keeps pair identity.
        self.assertEqual(_drc_comparison(report(unmeasured), report(unmeasured))['added_findings'], [])
        # Saved geometry or the write-scope proof of unchanged copper decides it.
        states = {'a': ('U1', 'pad', (0, 0, 0, 0)), 'b': ('C1', 'pad', (100, 0, 0, 0))}
        self.assertEqual(_drc_comparison(report(short), report(short), states, states)['added_findings'], [])
        proof = {'status': 'allowed', 'before_sha256': 'x', 'after_sha256': 'y', 'moved_identities': []}
        self.assertEqual(_drc_comparison(report(short), report(short), unchanged_copper=proof)['added_findings'], [])
        for bad in ({}, {**proof, 'status': 'refused'}, {**proof, 'after_sha256': None}):
            with self.subTest(proof=bad), self.assertRaisesRegex(ValueError, 'unchanged-copper proof'):
                _drc_comparison(report(short), report(short), unchanged_copper=bad)

    def test_owner_rule_cannot_be_reclassified_by_document(self):
        with tempfile.TemporaryDirectory() as temporary:
            board = Path(temporary) / 'b.kicad_pcb'
            board.write_text('(kicad_pcb)\n')
            board.with_suffix('.kicad_dru').write_text(
                '(version 1)\n(rule owner_gap (constraint clearance (min 0.3mm)))\n')
            for cls in ('default', 'manufacturer', 'datasheet', 'engineering', 'owner'):
                document = {'schema': 'fome-mc.constraint-provenance/1', 'board_setup_rules': {},
                    'custom_rules': {'b.kicad_dru': [{'rule': 'owner_gap', 'class': cls, 'basis': 'test claim'}]}}
                project, record = import_constraints({}, json_bytes(document), 'limits.json', 'test')
                bind_project(project, record)
                write_json(board.with_suffix('.kicad_pro'), project)
                if cls == 'owner':
                    sources = finding_sources(board)
                    self.assertEqual(sources['rules']['owner_gap']['class'], 'owner')
                    self.assertEqual(sources['rules']['owner_gap']['citation']['path'], str(board.with_suffix('.kicad_dru')))
                else:
                    with self.assertRaisesRegex(ValueError, 'owner.*reclassif'):
                        finding_sources(board)


class ProducerProvenanceTest(unittest.TestCase):
    def test_planes_only_refuse_sourced_or_structural_zone_findings(self):
        from agent_ee.add_planes import _planned_zone_errors
        uid = '10000000-0000-4000-8000-000000000001'
        finding = dict(type='clearance', severity='error', description=
            "Clearance violation (netclass 'Default' clearance 0.2000 mm; actual 0.0500 mm)",
            items=[{'uuid': uid}])
        report = dict(included_severities=['error', 'warning', 'exclusion'], violations=[finding])
        self.assertEqual(_planned_zone_errors(report, [uid]), [])
        sources = {'rules': {}, 'settings': [source('board.design_settings.rules.min_clearance', .09, 'manufacturer')]}
        self.assertEqual(_planned_zone_errors(report, [uid], sources=sources), [finding])
        finding['severity'] = 'warning'
        self.assertEqual(_planned_zone_errors(report, [uid]), [])
        self.assertEqual(_planned_zone_errors(report, [uid], sources=sources), [finding])
        finding['severity'] = 'error'
        for kind, description in [('holes_co_located', 'Drilled holes co-located'),
                ('shorting_items', 'Short'), ('copper_edge_clearance',
                 'Board edge clearance (board setup constraints edge clearance 0.5000 mm; actual -0.2000 mm)')]:
            with self.subTest(kind=kind):
                finding.update(type=kind, description=description)
                self.assertEqual(_planned_zone_errors(report, [uid]), [finding])
        self.assertNotIn('constraint_provenance', finding)


class NativeLimitsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.toolchain import require_toolchain
        require_toolchain(native=True)

    def test_increasing_native_short_overlap_refuses_stateless_and_blocks_with_states(self):
        import pcbnew as k
        from tests.test_placement_native import NativePlacementTest
        from agent_ee.design_rule_provenance import measured_actual
        from agent_ee.placer_runner import CollisionMeasurementRefusal, _drc_item_states, _native_drc
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            maker = NativePlacementTest(); maker.root = root
            board = k.LoadBoard(str(maker.board()))
            for fp in board.GetFootprints():
                net = k.NETINFO_ITEM(board, fp.GetReference()); board.Add(net)
                for pad in fp.Pads():
                    pad.SetNet(net)
            peer = next(f for f in board.GetFootprints() if f.GetReference() == 'C1')
            reports, states = [], []
            # 0.5 mm pads at U1 (3, 3): C1 overlap grows from 0.1 to 0.3 mm on the same UUIDs.
            for name, x in (('before', 3.4), ('after', 3.2)):
                peer.SetPosition(k.VECTOR2I(k.FromMM(x), k.FromMM(3)))
                path = root/(name + '.kicad_pcb'); k.SaveBoard(str(path), board)
                (root/name).mkdir()
                reports.append(_native_drc(path, root/name)); states.append(_drc_item_states(path))
            shorts = [[v for v in r['violations'] if v['type'] == 'shorting_items'] for r in reports]
            self.assertEqual([len(s) for s in shorts], [1, 1], reports)
            self.assertEqual(*[sorted(i['uuid'] for i in s[0]['items']) for s in shorts])
            self.assertFalse(any(measured_actual(s[0]) for s in shorts), shorts)
            with self.assertRaisesRegex(CollisionMeasurementRefusal, 'native saved geometry required'):
                _drc_comparison(*copy.deepcopy(reports))
            delta = _drc_comparison(*copy.deepcopy(reports), *states)
            self.assertIn('shorting_items', [v['type'] for v in delta['blocking_findings']], delta)
            same = _drc_comparison(copy.deepcopy(reports[1]), copy.deepcopy(reports[1]), states[1], states[1])
            self.assertEqual(same['blocking_findings'], [])

    def test_masked_minimum_refuses_real_native_gap(self):
        import pcbnew as k
        from tests.test_placement_native import NativePlacementTest
        from agent_ee.placer_runner import _proposal_drc
        from agent_ee.storage import read_json
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            maker = NativePlacementTest(); maker.root = root
            path = maker.board()
            board = k.LoadBoard(str(path))
            for fp in board.GetFootprints():
                net = k.NETINFO_ITEM(board, fp.GetReference()); board.Add(net)
                for pad in fp.Pads():
                    pad.SetNet(net)
                if fp.GetReference() == 'C1':
                    fp.SetPosition(k.VECTOR2I(15000000, 3000000))
            k.SaveBoard(str(path), board)
            template = {'board': {'design_settings': {'rules': {'min_clearance': 0}}},
                'net_settings': {'meta': {'version': 5}, 'classes': [{'name': 'Default', 'clearance': .2}]}}
            doc = {'schema': 'fome-mc.constraint-provenance/1', 'board_setup_rules': {
                'min_clearance': {'value_mm': .09, 'class': 'manufacturer', 'basis': 'original probe minimum'}}}
            project, record = import_constraints(template, json_bytes(doc), 'limits.json', 'probe')
            bind_project(project, record); write_json(path.with_suffix('.kicad_pro'), project)
            for x, refuses in ((3.65, False), (3.55, True)):
                work = root/str(x); work.mkdir()
                output = work/'candidate.kicad_pcb'
                candidate = k.LoadBoard(str(path))
                next(f for f in candidate.GetFootprints() if f.GetReference() == 'C1').SetPosition(
                    k.VECTOR2I(k.FromMM(x), k.FromMM(3)))
                k.SaveBoard(str(output), candidate)
                if refuses:
                    with self.assertRaisesRegex(ValueError, 'introduced native DRC'):
                        _proposal_drc(path, output)
                else:
                    _proposal_drc(path, output)
                delta = read_json(work/'native-drc-comparison.json')
                clearances = [v for v in delta['added_findings'] if v['type']=='clearance']
                self.assertTrue(clearances, delta)
                self.assertTrue(all('0.2000 mm' in v['description'] for v in clearances), clearances)
                self.assertTrue(all(v['constraint_provenance']['class']==('manufacturer' if refuses else 'default')
                                    for v in clearances), clearances)
                self.assertTrue(any(('actual 0.0500 mm' if refuses else 'actual 0.1500 mm') in v['description']
                                    for v in clearances), clearances)
                if refuses:
                    import shutil
                    import time
                    from agent_ee.candidate import capture
                    from agent_ee.checks import run_checks
                    repo = Path(__file__).resolve().parents[1]
                    captured_source = root/'captured-source'
                    shutil.copytree(repo/'fixtures/rc/control', captured_source)
                    shutil.copyfile(output, captured_source/'rc_filter.kicad_pcb')
                    write_json(captured_source/'rc_filter.kicad_pro', project)
                    scope = read_json(repo/'fixtures/rc/control.scope.json')
                    for entry in scope['inputs']:
                        entry['path'] = str(captured_source if entry['at']=='project' else
                            repo/'fixtures/rc'/entry['path'])
                    scope['checks'] = [{'id': 'drc', 'kind': 'drc'}]
                    write_json(root/'scope.json', scope)
                    run = root/'captured'; run.mkdir()
                    capture(root/'scope.json', run)
                    result = run_checks(run, time.monotonic()+120)[0]
                    self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)
                    classified = read_json(run/'work/drc/drc-constraints.json')['violations']
                    native_gaps = [v for v in classified if v['type']=='clearance']
                    self.assertTrue(native_gaps)
                    self.assertTrue(all(v['constraint_provenance']['class']=='manufacturer' for v in native_gaps))

    def test_board_build_parity_retains_advisories_and_sourced_findings(self):
        import shutil
        from agent_ee.board_build import parity_drc
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            board = root/'project/rc_filter.kicad_pcb'
            shutil.copytree(Path(__file__).resolve().parents[1]/'fixtures/rc/control', board.parent)
            from agent_ee.storage import read_json
            original = read_json(board.with_suffix('.kicad_pro'))
            for cls in ('default', 'manufacturer'):
                doc = {'schema': 'fome-mc.constraint-provenance/1', 'board_setup_rules': {
                    'min_text_height': {'value_mm': 8, 'class': cls, 'basis': 'original text probe'}}}
                template = copy.deepcopy(original)
                template['board']['design_settings']['rules']['min_text_height'] = 8
                project, record = import_constraints(template, json_bytes(doc), 'limits.json', 'probe')
                bind_project(project, record); write_json(board.with_suffix('.kicad_pro'), project)
                parity = parity_drc(board, root/cls)
                self.assertEqual(parity['schematic_parity'], 0)
                self.assertGreater(parity['violations'], 0)
                self.assertEqual(parity['graded_violations'], 0 if cls=='default' else parity['violations'])
                self.assertTrue(all(v['constraint_provenance']['class']==cls for v in parity['classified_findings']))

    def test_row_standin_clean_flag_uses_graded_native_findings(self):
        from agent_ee import board_build
        from tests.test_board_build import NativeBoardBuildTest
        from agent_ee.storage import read_json
        fixture = NativeBoardBuildTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        built = fixture.build('base')
        self.assertEqual(built['status'], 'placed', built)
        unplaced = fixture.root/'base/unplaced/rc_filter.kicad_pcb'
        template_path = fixture.template/'rc_filter.kicad_pro'
        template = read_json(template_path)
        template['board']['design_settings']['rules']['min_text_height'] = 8
        name, files = board_build.project_files(fixture.project)
        envelope = board_build.read_envelope(read_json(fixture.envelope))
        request = board_build.read_request(read_json(fixture.request), envelope)
        for cls in ('default', 'manufacturer'):
            doc = {'schema': 'fome-mc.constraint-provenance/1', 'board_setup_rules': {
                'min_text_height': {'value_mm': 8, 'class': cls, 'basis': 'original row text probe'}}}
            project, record = import_constraints(template, json_bytes(doc), 'limits.json', 'probe')
            bind_project(project, record); write_json(template_path, project)
            out = fixture.root/('row-'+cls); out.mkdir()
            receipt = board_build._row_standin({}, out, name, files,
                {'template_project': template_path}, unplaced, envelope, request)
            self.assertEqual(receipt['status'], 'placed' if cls=='default' else 'placed_with_defects', receipt)
            parity = receipt['row_standin']['parity']
            self.assertGreater(parity['violations'], 0)
            self.assertEqual(parity['graded_violations'], 0 if cls=='default' else parity['violations'])

    def test_native_coincident_drills_remain_hard(self):
        import pcbnew as k
        from agent_ee.placer_runner import _native_drc
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); path = root/'holes.kicad_pcb'
            board = k.BOARD()
            outline = k.PCB_SHAPE(board); outline.SetShape(k.SHAPE_T_RECT); outline.SetLayer(k.Edge_Cuts)
            outline.SetStart(k.VECTOR2I(0, 0)); outline.SetEnd(k.VECTOR2I(20000000, 20000000)); board.Add(outline)
            for ref in ('H1', 'H2'):
                fp = k.FOOTPRINT(board); fp.SetReference(ref); board.Add(fp)
                pad = k.PAD(fp); pad.SetAttribute(k.PAD_ATTRIB_NPTH); pad.SetShape(k.PAD_SHAPE_CIRCLE)
                pad.SetSize(k.VECTOR2I(1000000, 1000000)); pad.SetDrillSize(k.VECTOR2I(1000000, 1000000))
                pad.SetLayerSet(k.PAD.UnplatedHoleMask()); fp.Add(pad)
                fp.SetPosition(k.VECTOR2I(10000000, 10000000))
            k.SaveBoard(str(path), board)
            write_json(path.with_suffix('.kicad_pro'), {'board': {'design_settings': {'rules': {'min_hole_to_hole': .25}}}})
            work = root/'drc'; work.mkdir()
            native = _native_drc(path, work)
            sources = finding_sources(path, native['violations'])
            holes = [classify_finding(dict(v), sources) for v in native['violations'] if v['type']=='holes_co_located']
            self.assertTrue(holes, native)
            self.assertTrue(all(v['constraint_provenance']['class']=='engineering' and not advisory_numeric(v) for v in holes))


from tests import test_add_planes as plane_fixture


class NativePlaneLimitsTest(plane_fixture.PlaneFixture, unittest.TestCase):
    def test_generated_plane_clearance_classification_uses_actual_gap(self):
        from unittest.mock import patch
        from agent_ee import add_planes, benchmark
        from agent_ee.storage import read_json
        self.plan['zones'] = [dict(self.plan['zones'][0], island_removal='never',
            thermal=dict(connection='solid', gap_mm=.3, spoke_width_mm=.3))]
        self.save()
        original = add_planes.splice_zones

        def near_foreign_pad(*args):
            text = original(*args)
            zone = next(z for z in benchmark.children(benchmark.parse(text), 'zone')
                        if benchmark.uid(z)==add_planes.zone_ids(self.plan)[0])
            fill = benchmark.children(zone, 'filled_polygon')[0]
            polygon = '(filled_polygon (layer "In1.Cu") (pts (xy 11 11) (xy 18.95 11) (xy 18.95 49) (xy 11 49)))'
            return benchmark.replace_spans(text, [(fill.start, fill.end, polygon)])

        for cls in ('default', 'manufacturer'):
            with self.subTest(cls=cls):
                template = {'board': {'design_settings': {'rules': {'min_clearance': 0}}},
                    'net_settings': {'meta': {'version': 5}, 'classes': [{'name': 'Default', 'clearance': .2}]}}
                doc = {'schema': 'fome-mc.constraint-provenance/1', 'board_setup_rules': {
                    'min_clearance': {'value_mm': .09, 'class': cls, 'basis': 'original plane probe'}}}
                project, record = import_constraints(template, json_bytes(doc), 'limits.json', 'probe')
                bind_project(project, record)
                write_json((self.source/self.board).with_suffix('.kicad_pro'), project)
                with patch.object(add_planes, 'splice_zones', side_effect=near_foreign_pad):
                    report = self.run_plan(cls)
                self.assertEqual(report['execution'], 'ok' if cls=='default' else 'failed', report)
                native = read_json(self.root/cls/'drc/report.json')
                gaps = [v for v in report['drc']['classified_findings'] if v['type']=='clearance']
                self.assertTrue(gaps, native)
                self.assertTrue(all('actual 0.0500 mm' in v['description'] for v in gaps), gaps)
                self.assertTrue(all(v['constraint_provenance']['class']==cls for v in gaps), gaps)
                self.assertEqual(bool(report['drc']['planned_zone_errors']), cls!='default')

    def test_unsourced_spoke_count_is_advisory_but_owned_count_refuses(self):
        from unittest.mock import patch
        from agent_ee import add_planes, benchmark
        self.plan['zones'] = [dict(self.plan['zones'][0], island_removal='never')]
        self.save()
        original = add_planes.splice_zones

        def one_spoke(*args):
            text = original(*args)
            zone = next(z for z in benchmark.children(benchmark.parse(text), 'zone')
                        if benchmark.uid(z)==add_planes.zone_ids(self.plan)[0])
            fill = benchmark.children(zone, 'filled_polygon')[0]
            polygon = '(filled_polygon (layer "In1.Cu") (pts (xy 11 11) (xy 18.95 11) (xy 18.95 49) (xy 11 49)))'
            return benchmark.replace_spans(text, [(fill.start, fill.end, polygon)])

        for owned in (False, True):
            if owned:
                (self.source/self.board).with_suffix('.kicad_dru').write_text(
                    '(version 1)\n(rule "owned spoke count" (constraint thermal_spoke_count (min 3)))\n')
            with patch.object(add_planes, 'splice_zones', side_effect=one_spoke):
                report = self.run_plan('owned-spokes' if owned else 'default-spokes')
            self.assertEqual(report['execution'], 'failed' if owned else 'ok', report)
            spokes = [v for v in report['drc']['classified_findings'] if v['type']=='starved_thermal']
            self.assertTrue(spokes, report)
            self.assertTrue(all(v['constraint_provenance']['class']==('owner' if owned else 'default') for v in spokes))
            if owned:
                self.assertTrue(all('min spoke count 2' in v['description'] for v in spokes), spokes)
                self.assertTrue(all(v['constraint_provenance']['citation']['rule']=='owned spoke count' for v in spokes))
                self.assertTrue(all(v['constraint_provenance']['limits'][0]['value']==3 for v in spokes))
                self.assertTrue(any(v['type']=='starved_thermal' for v in report['drc']['planned_zone_errors']))
        (self.source/self.board).with_suffix('.kicad_dru').write_text(
            '(version 1)\n(rule "conditional owned count" (condition "A.Type == \'Pad\'") '
            '(constraint thermal_spoke_count (min 2)))\n')
        with patch.object(add_planes, 'splice_zones', side_effect=one_spoke):
            conditional = self.run_plan('conditional-spokes')
        self.assertEqual((conditional['execution'], conditional['outcome']), ('failed', 'unknown'), conditional)
        self.assertIn('conditional sourced thermal-spoke provenance unavailable', str(conditional['findings']))
        self.assertIn('conditional owned count', str(conditional['findings']))
        self.assertIn("'class': 'owner'", str(conditional['findings']))


from tests import test_scaffold_producer as producer_fixture


class OwnerDeclarationProducerTest(producer_fixture.ProducerFixture, unittest.TestCase):
    def test_scaffold_refuses_owner_demotion_before_publication(self):
        from agent_ee.design_rule_provenance import OwnerRuleReclassification
        for compose in (False, True):
            with self.subTest(compose=compose):
                if compose:
                    self.use_net_constraints()
                name = 'owner_composed' if compose else 'owner_plain'
                rule = self.source/'project/design.kicad_dru'
                rule.parent.mkdir(exist_ok=True)
                rule.write_text(f'(version 1)\n(rule {name} (constraint clearance (min 0.3mm)))\n')
                doc = {'schema': 'fome-mc.constraint-provenance/1', 'board_setup_rules': {},
                    'custom_rules': {'design.kicad_dru': [{'rule': name, 'class': 'default', 'basis': 'demotion probe'}]}}
                write_json(self.source/'limits.json', doc)
                self.plan['constraint_source'] = 'limits.json'
                self.write()
                with self.assertRaises(OwnerRuleReclassification):
                    self.build()
                self.assertFalse((self.root/'produced').exists())
