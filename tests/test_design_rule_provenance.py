"""Original constraint-source imports and immutable custom-rule inputs (AGENTEE-331)."""
import copy
import json
from pathlib import Path
import sys
import unittest

from agent_ee.candidate import capture
from agent_ee.campaign import check_proposal
from agent_ee.design_rule_provenance import declaration, import_constraints, inventory
from agent_ee.scaffold import scaffold
from agent_ee.storage import digest, json_bytes, read_json, write_json

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_scaffold import ScaffoldFixture
from test_scaffold_producer import ProducerFixture
from toolchain import require_toolchain


def document():
    return {'schema': 'fome-mc.constraint-provenance/1', 'board_setup_rules': {
        'min_clearance': {'value_mm': 0.15, 'class': 'engineering', 'basis': 'Original test margin, decision R1'},
        'min_track_width': {'value_mm': 0.1, 'class': 'default', 'basis': 'Synthetic tool default'}},
        'net_class_default': {'clearance_mm': 0.15, 'track_width_mm': 0.2,
                              'class': 'owner', 'basis': 'Original test owner statement R2'}}


class ConstraintImportTest(unittest.TestCase):
    def test_every_class_and_missing_source_classification(self):
        project = {'board': {'design_settings': {'rules': {'min_clearance': 0.2, 'min_track_width': 0.2}}},
                   'net_settings': {'classes': [{'name': 'Default', 'clearance': 0.2, 'via_drill': 0.3}]}}
        original = copy.deepcopy(project)
        for provenance in ('owner', 'manufacturer', 'datasheet', 'engineering', 'default'):
            declared = document()
            declared['board_setup_rules']['min_clearance']['class'] = provenance
            imported, record = import_constraints(project, json_bytes(declared), 'constraints.json', 'test-digest')
            expected = 0.2 if provenance == 'default' else 0.15
            self.assertEqual(imported['board']['design_settings']['rules']['min_clearance'], expected)
            by_setting = {r['setting']: r for r in record['settings']}
            result = by_setting['board.design_settings.rules.min_clearance']
            self.assertEqual(result['class'], provenance)
            self.assertEqual(result['enforcement'], 'advisory' if provenance == 'default' else 'hard')
            if provenance != 'default':
                self.assertEqual(result['citation']['path'], 'constraints.json')
            self.assertEqual(by_setting['net_settings.classes.Default.via_drill']['class'], 'default')
            self.assertEqual(project, original)
        self.assertTrue(all(r['class'] == 'default' and r['enforcement'] == 'advisory'
                            for r in inventory(project)))

    def test_optional_default_class_source_can_be_absent_or_null(self):
        project = {'net_settings': {'classes': [{'name': 'Default', 'clearance': 0.2}]}}
        for optional in ({}, {'net_class_default': None}):
            with self.subTest(optional=optional):
                declared = document()
                del declared['net_class_default']
                declared.update(optional)
                imported, record = import_constraints(project, json_bytes(declared),
                                                       'constraints.json', 'test-digest')
                self.assertEqual(imported['net_settings'], project['net_settings'])
                defaults = [r for r in record['settings'] if r['setting'].startswith('net_settings.')]
                self.assertEqual(len(defaults), 1)
                self.assertEqual((defaults[0]['class'], defaults[0]['enforcement']), ('default', 'advisory'))
                self.assertIsNone(defaults[0]['citation'])

    def test_invalid_sources_refuse(self):
        for mutate in (lambda d: d.update(schema='invented/1'),
                       lambda d: d['board_setup_rules']['min_clearance'].update({'class': 'assumed'}),
                       lambda d: d['board_setup_rules']['min_clearance'].update({'class': []}),
                       lambda d: d['board_setup_rules']['min_clearance'].update({'class': {}}),
                       lambda d: d['board_setup_rules']['min_clearance'].update(basis=''),
                       lambda d: d['board_setup_rules']['min_clearance'].update(value_mm=True),
                       lambda d: d['board_setup_rules']['min_clearance'].update(value_mm=-1),
                       lambda d: d['board_setup_rules'].update(invented={'value_mm': 1, 'class': 'owner', 'basis': 'x'})):
            declared = document()
            mutate(declared)
            with self.assertRaises(ValueError):
                declaration(json_bytes(declared))
        with self.assertRaises(ValueError):
            declaration(b'{"schema":"fome-mc.constraint-provenance/1","board_setup_rules":{"min_clearance":{"value_mm":NaN,"class":"owner","basis":"x"}}}')


class ScaffoldConstraintTest(ScaffoldFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)

    def declare(self):
        write_json(self.source / 'constraints.json', document())
        self.scope['inputs'].append({'path': 'source/constraints.json', 'at': 'constraints.json'})
        write_json(self.scope_path, self.scope)

    def test_import_receipt_native_values_and_immutable_sources(self):
        self.declare()
        receipt = scaffold(self.source / 'envelope.json', self.source / 'parts.json', self.root / 'out',
                           scope_path=self.scope_path, constraint_source=self.source / 'constraints.json')
        record = receipt['constraint_provenance']
        self.assertEqual(record['sha256'], digest(self.source / 'constraints.json'))
        self.assertEqual(record['document'], document())
        from agent_ee.executor import execute
        work = self.root / 'native'
        script = ('import json, pcbnew; b=pcbnew.LoadBoard("/candidate/project/design.kicad_pcb"); '
                  's=b.GetDesignSettings(); print(json.dumps([s.m_MinClearance, '
                  's.m_NetSettings.GetDefaultNetclass().GetClearance()]))')
        result = execute(self.root / 'out/inputs', work, ['/usr/bin/python3', '-c', script], timeout=60)
        self.assertEqual(result['execution'], 'ok', result)
        self.assertEqual(json.loads((work / 'stdout.log').read_text()), [150000, 150000])
        run = self.root / 'capture'
        run.mkdir()
        capture(self.root / 'out/scope.json', run)
        self.assertIn('constraints.json', read_json(run / 'baseline.json')['files'])

    def test_owner_custom_rule_is_carried_and_cannot_be_mutated(self):
        raw = '(version 1)\n(rule "original owned margin" (constraint clearance (min 0.15mm)))\n'
        rule = self.source / 'project/design.kicad_dru'
        rule.write_text(raw)
        self.scope['inputs'].append({'path': 'source/project/design.kicad_dru', 'at': 'project/design.kicad_dru'})
        write_json(self.scope_path, self.scope)
        self.build()
        self.assertEqual((self.root / 'scaffold/inputs/project/design.kicad_dru').read_text(), raw)
        run = self.root / 'capture'
        run.mkdir()
        manifest = capture(self.root / 'scaffold/scope.json', run)
        baseline = read_json(run / 'baseline.json')
        rel = 'project/design.kicad_dru'
        self.assertNotIn(rel, manifest['scope']['mutable_inputs'])
        (run / 'inputs' / rel).write_text(raw.replace('0.15', '0.10'))
        with self.assertRaisesRegex(ValueError, 'immutable verification input'):
            check_proposal(run / 'inputs', manifest, {'mutable_inputs': manifest['scope']['mutable_inputs'],
                                                     'baseline': baseline})

    def test_uncaptured_document_refuses_without_publishing(self):
        write_json(self.source / 'constraints.json', document())
        with self.assertRaises(ValueError):
            scaffold(self.source / 'envelope.json', self.source / 'parts.json', self.root / 'out',
                     scope_path=self.scope_path, constraint_source=self.source / 'constraints.json')
        self.assertFalse((self.root / 'out').exists())

    def test_legacy_template_settings_are_explicitly_advisory_in_receipt(self):
        receipt = self.build()
        self.assertTrue(receipt['constraint_provenance']['settings'])
        self.assertTrue(all(r['class'] == 'default' and r['citation'] is None
                            for r in receipt['constraint_provenance']['settings']))


class ProducerConstraintTest(ProducerFixture, unittest.TestCase):
    def test_producer_plan_declares_captured_source(self):
        write_json(self.source / 'constraints.json', document())
        rule = self.source / 'project/design.kicad_dru'
        rule.parent.mkdir(exist_ok=True)
        rule.write_text('(version 1)\n(rule "original owner clearance" (constraint clearance (min 0.15mm)))\n')
        self.plan['constraint_source'] = 'constraints.json'
        self.write()
        receipt = self.build()
        self.assertEqual(receipt['constraint_provenance']['path'], 'constraints.json')
        project = read_json(self.root / 'produced/inputs/project/design.kicad_pro')
        self.assertEqual(project['board']['design_settings']['rules']['min_clearance'], 0.15)
        settings = {r['setting']: r for r in receipt['constraint_provenance']['settings']}
        self.assertEqual(settings['board.design_settings.rules.min_track_width']['class'], 'manufacturer')
        self.assertEqual(settings['net_settings.classes.Default.via_drill']['class'], 'manufacturer')
        run = self.root / 'capture'
        run.mkdir()
        capture(self.root / 'produced/scope.json', run)
        self.assertIn('constraints.json', read_json(run / 'baseline.json')['files'])
        self.assertEqual((self.root / 'produced/inputs/project/design.kicad_dru').read_bytes(), rule.read_bytes())
        self.assertIn('project/design.kicad_dru', read_json(run / 'baseline.json')['files'])
        self.assertNotIn('project/design.kicad_dru', read_json(self.root / 'produced/scope.json')['mutable_inputs'])


class FindingSourceTest(unittest.TestCase):
    def test_native_materialized_defaults_are_advisory_without_changing_bound_settings(self):
        import tempfile
        from agent_ee.design_rule_provenance import bind_project, finding_sources
        with tempfile.TemporaryDirectory() as temporary:
            board = Path(temporary) / 'original.kicad_pcb'
            declared = document()
            del declared['net_class_default']
            project, record = import_constraints({}, json_bytes(declared), 'constraints.json', 'test-source')
            bind_project(project, record)
            rules = project['board']['design_settings']['rules']
            rules['min_text_height'] = 0.8
            write_json(board.with_suffix('.kicad_pro'), project)
            settings = {s['setting']: s for s in finding_sources(board)['settings']}
            self.assertEqual(settings['board.design_settings.rules.min_text_height']['class'], 'default')
            self.assertIsNone(settings['board.design_settings.rules.min_text_height']['citation'])
            self.assertEqual(settings['board.design_settings.rules.min_clearance']['class'], 'engineering')
            # An addition cannot excuse changes to the originally bound inventory.
            rules['min_clearance'] = 9
            write_json(board.with_suffix('.kicad_pro'), project)
            with self.assertRaisesRegex(ValueError, 'differs from saved project'):
                finding_sources(board)

    def test_numeric_classes_value_agreement_and_unresolved_authority(self):
        from agent_ee.design_rule_provenance import classify_finding
        for cls in ('owner', 'manufacturer', 'datasheet', 'engineering', 'default'):
            sources = {'rules': {}, 'settings': [{
                'setting': 'board.design_settings.rules.min_text_height', 'value': 1.0,
                'class': cls, 'citation': {'path': 'constraints.json', 'basis': 'original test'}}]}
            finding = {'type': 'text_height', 'description':
                       'Text height out of range (board setup constraints silk text height min height 1.0000 mm; actual 0.8000 mm)'}
            result = classify_finding(finding, sources)['constraint_provenance']
            self.assertEqual(result['class'], cls)
            self.assertEqual(result['enforcement'], 'advisory' if cls == 'default' else 'hard')
            self.assertEqual(result['citation']['path'], 'constraints.json')
            sources['settings'][0]['value'] = 1.1
            self.assertEqual(classify_finding(finding, sources)['constraint_provenance']['class'], 'default')
        for kind in ('silk_overlap', 'silk_over_copper', 'text_thickness', 'new_native_category'):
            result = classify_finding({'type': kind})['constraint_provenance']
            self.assertEqual((result['class'], result['enforcement'], result['citation']),
                             ('default', 'advisory', None))

    def test_every_finding_including_baseline_silk_is_retained_and_classified(self):
        from agent_ee.placer_runner import _drc_comparison
        baseline = {'ignored_checks': [], 'violations': [
            {'type': 'silk_overlap', 'items': [{'uuid': 's'}]}]}
        after = copy.deepcopy(baseline)
        after['violations'] += [{'type': 'clearance', 'items': [{'uuid': 'c'}]},
                                {'type': 'shorting_items', 'items': [{'uuid': 'a'}, {'uuid': 'b'}]}]
        delta = _drc_comparison(baseline, after)
        self.assertEqual(len(delta['baseline_findings']), 1)
        self.assertEqual(len(delta['candidate_findings']), 3)
        self.assertEqual(len(delta['advisory_findings']), 2)
        self.assertEqual([v['type'] for v in delta['blocking_findings']], ['shorting_items'])
        self.assertTrue(delta['blocking_findings'][0]['constraint_provenance']['citation'])
        self.assertTrue(all('constraint_provenance' in f for f in delta['candidate_findings']))

    def test_stale_binding_and_uncited_authority_cannot_be_hard(self):
        import tempfile
        from agent_ee.design_rule_provenance import bind_project, classify_finding, finding_sources
        with tempfile.TemporaryDirectory() as temporary:
            board = Path(temporary) / 'original.kicad_pcb'
            # A project without net classes can carry just the board declaration.
            declared = document()
            del declared['net_class_default']
            project, record = import_constraints({}, json_bytes(declared), 'constraints.json', 'test-source')
            bind_project(project, record)
            write_json(board.with_suffix('.kicad_pro'), project)
            finding_sources(board)
            project['board']['design_settings']['rules']['min_clearance'] = 9
            write_json(board.with_suffix('.kicad_pro'), project)
            with self.assertRaisesRegex(ValueError, 'differs from saved project'):
                finding_sources(board)
        for cls, citation in [('owner', None), ('invented', {'path': 'missing'})]:
            finding = {'type': 'clearance', 'description': "Clearance violation (rule 'original gap' clearance 4.0000 mm; actual 1.0000 mm)"}
            source = {'settings': [], 'rules': {'original gap': {'class': cls, 'citation': citation}}}
            self.assertEqual(classify_finding(finding, source)['constraint_provenance']['enforcement'], 'advisory')

    def test_native_rule_comments_cannot_invent_owned_rules(self):
        from agent_ee.net_constraints import rule_forms, template_rules
        raw = b'(version 1)\n# (rule \"invented owner\" (constraint clearance (min 9mm)))\n(rule \"actual owner\" (constraint clearance (min 0.15mm)))\n'
        template_rules(raw, allow_generated=True)
        self.assertEqual([n[1] for n in rule_forms(raw, native=True)[1:]], ['actual owner'])
        with self.assertRaises(ValueError):
            template_rules(b'; Not a native rule comment\n' + raw, allow_generated=True)

    def test_native_item_net_reader_keeps_ambiguous_identities_unresolved(self):
        from agent_ee.net_binding import board_item_nets
        first = '10000000-0000-4000-8000-000000000001'
        second = '10000000-0000-4000-8000-000000000002'
        third = '10000000-0000-4000-8000-000000000003'
        raw = ('(kicad_pcb (net 1 \"N\") (footprint \"Original\" '
               f'(pad \"1\" smd rect (uuid \"{first}\") (net 1 \"N\")) '
               f'(pad \"2\" smd rect (uuid \"{first}\") (net 1 \"N\"))) '
               f'(segment (uuid \"{second}\") (net 1)) '
               f'(via (uuid \"{third}\")))').encode()
        self.assertEqual(board_item_nets(raw), {first: None, second: None, third: ''})

    def test_sourced_custom_numeric_evaluation_cannot_be_ignored(self):
        from agent_ee.design_rule_provenance import require_evaluated_limits
        sources = {'settings': [], 'rules': {'width': {
            'class': 'owner', 'citation': {'path': 'owned.kicad_dru', 'rule': 'width'}}},
            'rule_constraints': {'width': ['track_width']}, 'ignored_rules': []}
        with self.assertRaisesRegex(ValueError, 'explicitly ignored'):
            require_evaluated_limits({'ignored_checks': [{'key': 'track_width'}]}, sources)
        sources['ignored_rules'] = ['width']
        with self.assertRaisesRegex(ValueError, 'explicitly ignored'):
            require_evaluated_limits({'ignored_checks': []}, sources)

    def test_ambiguous_equal_values_do_not_invent_a_source(self):
        from agent_ee.design_rule_provenance import classify_finding
        sources = {'rules': {}, 'settings': [
            dict(setting='net_settings.classes.' + name + '.clearance', value=0.2,
                 **{'class': cls}, citation={'path': name})
            for name, cls in [('Default', 'default'), ('Owned', 'owner')]]}
        finding = {'type': 'clearance', 'description': 'Clearance violation ( clearance 0.2000 mm; actual 0.1500 mm)'}
        self.assertEqual(classify_finding(finding, sources)['constraint_provenance']['class'], 'default')


class NativeFindingSourceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)

    def test_native_packing_uses_sourced_margins_without_cached_or_local_defaults(self):
        import tempfile
        import pcbnew as k
        from agent_ee.design_rule_provenance import packing_clearances
        from agent_ee.placement_probe import _packing_pad_geometry
        from test_placer_through_holes import ThroughHolePlacementTest
        with tempfile.TemporaryDirectory() as temporary:
            maker = ThroughHolePlacementTest()
            maker.root = Path(temporary)
            path = maker.board()
            original = path.read_bytes()
            board = k.LoadBoard(str(path))
            fp = next(iter(board.GetFootprints()))
            project = {'net_settings': {'classes': [{'name': 'Default', 'clearance': 0.125}]}}
            sources = {'settings': [{'setting': 'net_settings.classes.Default.clearance',
                'value': 0.125, 'class': 'engineering', 'citation': {'path': 'original-test'}}]}
            margins = packing_clearances(board, sources, project)
            self.assertEqual(set(margins.values()), {125000})
            for pad in fp.Pads():
                pad.SetLocalClearance(900000)
            expected = _packing_pad_geometry(board, fp, margins)
            for pad in fp.Pads():
                pad.SetLocalClearance(1000000)
            self.assertEqual(_packing_pad_geometry(board, fp, margins), expected)
            advisory = packing_clearances(board, {'settings': []}, project)
            self.assertEqual(set(advisory.values()), {0})
            self.assertNotEqual(_packing_pad_geometry(board, fp, advisory), expected)
            self.assertEqual(path.read_bytes(), original)

    def test_numeric_native_gate_uses_every_declared_class(self):
        import tempfile
        import pcbnew as k
        from agent_ee.design_rule_provenance import bind_project
        from agent_ee.placer_runner import _proposal_drc
        from test_placer_through_holes import ThroughHolePlacementTest
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            maker = ThroughHolePlacementTest()
            maker.root = root
            original = maker.board()
            board = k.LoadBoard(str(original))
            for fp in board.GetFootprints():
                net = k.NETINFO_ITEM(board, fp.GetReference())
                board.Add(net)
                for pad in fp.Pads():
                    pad.SetNet(net)
                if fp.GetReference() == 'C1':
                    fp.SetPosition(k.VECTOR2I(15000000, 3000000))
            k.SaveBoard(str(original), board)
            for cls in ('default', 'owner', 'manufacturer', 'datasheet', 'engineering', 'legacy-null', 'unrelated-class'):
                source = root / (cls + '.kicad_pcb')
                source.write_bytes(original.read_bytes())
                declared = {'schema': 'fome-mc.constraint-provenance/1', 'board_setup_rules': {
                    'min_clearance': {'value_mm': 4, 'class': cls, 'basis': 'original test limit'}}}
                if cls in ('legacy-null', 'unrelated-class'):
                    project = {'board': {'design_settings': {'rules': {'min_clearance': None}}},
                               'net_settings': {'meta': {'version': 5},
                                                'classes': [{'name': 'Default', 'clearance': 4}]}}
                else:
                    project, record = import_constraints({'board': {'design_settings': {'rules': {
                        'min_clearance': 4}}}}, json_bytes(declared), 'constraints.json', 'original-test-source')
                    bind_project(project, record)
                if cls == 'unrelated-class':
                    project['net_settings']['classes'].append({'name': 'Wide', 'clearance': 0.2})
                    project['net_settings']['netclass_patterns'] = [
                        {'netclass': 'Wide', 'pattern': name} for name in ('U1', 'C1')]
                    declared = {'schema': 'fome-mc.constraint-provenance/1', 'board_setup_rules': {},
                                'net_class_default': {'clearance_mm': 4, 'class': 'owner',
                                                      'basis': 'original Default-only owner limit'}}
                    project, record = import_constraints(project, json_bytes(declared), 'limits.json', 'test-source')
                    native = k.LoadBoard(str(source))
                    for fp in native.GetFootprints():
                        for pad in fp.Pads():
                            pad.SetLocalClearance(4000000)
                    k.SaveBoard(str(source), native)
                    bind_project(project, record)
                write_json(source.with_suffix('.kicad_pro'), project)
                from agent_ee.placement_probe import extract
                original_project = source.with_suffix('.kicad_pro').read_bytes()
                geometry = extract(source)['footprints'][0]['packing_pad_geometry']
                self.assertEqual(geometry['status'], 'proven', geometry)
                if cls not in ('legacy-null', 'unrelated-class'):
                    self.assertEqual(geometry['clearance_nm'], 0 if cls == 'default' else 4000000)
                self.assertEqual(source.with_suffix('.kicad_pro').read_bytes(), original_project)
                work = root / cls
                work.mkdir()
                output = work / 'candidate.kicad_pcb'
                candidate = k.LoadBoard(str(source))
                next(f for f in candidate.GetFootprints() if f.GetReference() == 'C1').SetPosition(k.VECTOR2I(9000000, 3000000))
                k.SaveBoard(str(output), candidate)
                advisory = cls in ('default', 'legacy-null', 'unrelated-class')
                expected_class = 'default' if advisory else cls
                if advisory:
                    _proposal_drc(source, output)
                else:
                    with self.assertRaisesRegex(ValueError, 'introduced native DRC'):
                        _proposal_drc(source, output)
                delta = read_json(work / 'native-drc-comparison.json')
                clearances = [v for v in delta['added_findings'] if v['type'] == 'clearance']
                self.assertTrue(clearances, delta)
                self.assertTrue(all(v['constraint_provenance']['class'] == expected_class for v in clearances), delta)
                self.assertEqual(bool(delta['blocking_findings']), not advisory)
                self.assertEqual(read_json(work / 'native-drc-execution.json')['execution'], 'ok')


class ComposedProducerSourceTest(ProducerFixture, unittest.TestCase):
    def test_declared_sources_compose_net_rules_and_preserve_owner_bytes(self):
        from agent_ee.design_rule_provenance import finding_sources
        self.use_net_constraints()
        write_json(self.source / 'rule-sources.json', document())
        self.plan['constraint_source'] = 'rule-sources.json'
        rule = self.source / 'project/design.kicad_dru'
        rule.parent.mkdir(exist_ok=True)
        raw = b'(version 1)\n# Original owner comment with a decoy (version 1)\n(rule "agent_ee_netclass_RF_50" (constraint track_width (min 0.35mm)))\n(rule "original owner gap" (constraint clearance (min 0.15mm)))\n'
        rule.write_bytes(raw)
        self.write()
        receipt = self.build()
        inputs = self.root / 'produced/inputs'
        preserved = receipt['constraint_provenance']['owner_rules']['path']
        self.assertEqual((inputs / preserved).read_bytes(), raw)
        sources = finding_sources(inputs / 'project/design.kicad_pcb')
        self.assertEqual(sources['rules']['original owner gap']['class'], 'owner')
        self.assertEqual(sources['rules']['agent_ee_netclass_PWR']['class'], 'default')
        self.assertEqual(sources['rules']['agent_ee_netclass_RF_50']['class'], 'owner')
        self.assertEqual(sources['rules']['original owner gap']['citation']['sha256'], digest(rule))
        run = self.root / 'captured'
        run.mkdir()
        capture(self.root / 'produced/scope.json', run)
        baseline = read_json(run / 'baseline.json')
        self.assertIn(preserved, baseline['files'])
        self.assertIn('project/design.kicad_dru', baseline['files'])
        from agent_ee.net_constraints import rule_forms
        forms = rule_forms((inputs / 'project/design.kicad_dru').read_bytes(), native=True)
        self.assertEqual(forms[0], ['version', '1'])
        self.assertEqual(forms[-1][1], 'original owner gap')
        self.assertEqual(sum(n[0] == 'version' for n in forms), 1)
        from agent_ee.placer_runner import _native_drc
        native_work = self.root / 'composed-native'
        native_work.mkdir()
        import pcbnew as k
        native = k.LoadBoard(str(inputs / 'project/design.kicad_pcb'))
        net = k.NETINFO_ITEM(native, 'GNSS_RF')
        native.Add(net)
        track = k.PCB_TRACK(native)
        track.SetStart(k.VECTOR2I(5000000, 5000000))
        track.SetEnd(k.VECTOR2I(8000000, 5000000))
        track.SetWidth(320000)
        track.SetLayer(k.F_Cu)
        track.SetNet(net)
        native.Add(track)
        probe = native_work / 'design.kicad_pcb'
        k.SaveBoard(str(probe), native)
        for suffix in ('.kicad_pro', '.kicad_dru'):
            probe.with_suffix(suffix).write_bytes((inputs / 'project/design.kicad_pcb').with_suffix(suffix).read_bytes())
        # The captured template's ignored courtyard category still refuses.
        # Its raw native report independently proves owner-last width priority.
        with self.assertRaisesRegex(ValueError, 'explicitly ignored'):
            _native_drc(probe, native_work)
        self.assertEqual(read_json(native_work / 'native-drc-execution.json')['execution'], 'ok')
        widths = [v for v in read_json(native_work / 'native-drc.json')['violations'] if v['type'] == 'track_width']
        self.assertTrue(widths, {'report': read_json(native_work / 'native-drc.json'),
                                'stdout': (native_work / 'native-drc.stdout.txt').read_text(),
                                'stderr': (native_work / 'native-drc.stderr.txt').read_text(),
                                'rules': probe.with_suffix('.kicad_dru').read_text()})
        self.assertTrue(all('0.3500 mm' in v['description'] for v in widths), widths)
        from agent_ee.design_rule_provenance import classify_finding
        self.assertTrue(all(classify_finding(v, sources)['constraint_provenance']['class'] == 'owner' for v in widths))


class NativeCapturedDrcSourceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)

    def test_captured_check_and_review_packet_keep_native_advisories(self):
        import shutil
        import tempfile
        import time
        from agent_ee.checks import run_checks
        from agent_ee.design_rule_provenance import bind_project
        from agent_ee.reviewers import build_packet
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source'
            shutil.copytree(repo / 'fixtures/rc/control', source)
            # The owned RC control has a clean native parity baseline. Raising
            # only its text minimum creates a numeric-only DRC violation.
            project_path = next(source.glob('*.kicad_pro'))
            original = read_json(project_path)
            for cls in ('default', 'manufacturer'):
                declared = {'schema': 'fome-mc.constraint-provenance/1', 'board_setup_rules': {
                    'min_text_height': {'value_mm': 8, 'class': cls, 'basis': 'original native text test'}}}
                project = copy.deepcopy(original)
                project.setdefault('board', {}).setdefault('design_settings', {}).setdefault('rules', {})['min_text_height'] = 8
                project, record = import_constraints(project, json_bytes(declared), 'limits.json', 'test-source')
                bind_project(project, record)
                write_json(project_path, project)
                scope = read_json(repo / 'fixtures/rc/control.scope.json')
                for entry in scope['inputs']:
                    if entry['at'] == 'project':
                        entry['path'] = str(source)
                    elif not Path(entry['path']).is_absolute():
                        entry['path'] = str(repo / 'fixtures/rc' / entry['path'])
                scope['checks'] = [{'id': 'drc', 'kind': 'drc'}]
                write_json(root / 'scope.json', scope)
                run = root / cls
                run.mkdir()
                capture(root / 'scope.json', run)
                result = run_checks(run, time.monotonic() + 120)[0]
                self.assertEqual(result['execution'], 'ok', result)
                self.assertEqual(result['outcome'], 'pass' if cls == 'default' else 'defect', result)
                native = read_json(run / 'work/drc/drc.json')
                self.assertTrue(native['violations'])
                self.assertTrue(all('constraint_provenance' not in v for v in native['violations']))
                self.assertTrue(all(v['constraint_provenance']['class'] == cls
                                    for v in read_json(run / 'work/drc/drc-constraints.json')['violations']), result)
                packet = build_packet(run, [result], 'original-test-run')
                self.assertTrue(packet['design_rule_sources'])
                self.assertTrue(any(a['path'] == 'work/drc/drc-constraints.json' for a in packet['artifacts']))
                self.assertEqual(result['details']['constraint_classes'], {cls: len(native['violations'])})

                if cls == 'manufacturer':
                    project['board']['design_settings'].setdefault('rule_severities', {})['text_height'] = 'ignore'
                    write_json(project_path, project)
                    ignored_run = root / 'ignored'
                    ignored_run.mkdir()
                    capture(root / 'scope.json', ignored_run)
                    ignored = run_checks(ignored_run, time.monotonic() + 120)[0]
                    self.assertEqual((ignored['execution'], ignored['outcome']), ('malformed', 'unknown'), ignored)
                    self.assertIn('explicitly ignored', ignored['summary'])
