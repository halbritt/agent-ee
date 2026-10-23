"""Original constraint-source imports and immutable custom-rule inputs (AGENTEE-331)."""
import copy
import json
from pathlib import Path
import sys
import unittest

from agent_ee.candidate import capture
from agent_ee.campaign import check_proposal
from agent_ee.design_rule_provenance import declaration, import_constraints, inventory, geometry_defaults
from agent_ee.scaffold import scaffold
from agent_ee.storage import digest, json_bytes, read_json, write_json

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_scaffold import ScaffoldFixture
from test_scaffold_producer import ProducerFixture
from toolchain import require_toolchain
from test_add_planes import PlaneFixture
from owner_fixture import cite, citation, owner_rules


def document():
    return {'schema': 'fome-mc.constraint-provenance/1', 'board_setup_rules': {
        'min_clearance': {'value_mm': 0.15, 'class': 'engineering', 'basis': 'Original test margin, decision R1'},
        'min_track_width': {'value_mm': 0.1, 'class': 'default', 'basis': 'Synthetic tool default'}},
        'net_class_default': {'clearance_mm': 0.15, 'track_width_mm': 0.2,
                              'class': 'owner', 'basis': 'Original test owner statement R2'}}


class ConstraintImportTest(unittest.TestCase):
    def test_geometry_inventory_is_exact_and_preserves_native_values(self):
        project = {'board': {'design_settings': {
            'rules': {'min_clearance': 0.27, 'min_track_width': 0.12, 'min_text_height': 1},
            'rule_severities': {'clearance': 'error'}, 'drc_exclusions': ['test'],
            'defaults': {'board_text_size_h': 1, 'zone_clearance': 0.38},
            'track_widths': [0, 0.31], 'via_dimensions': [{'diameter': 0.7, 'drill': 0.3}]}},
            'net_settings': {'classes': [{'name': 'Default', 'clearance': 0.27,
                'track_width': 0.33, 'via_drill': 0.3, 'wire_width': 6, 'priority': 1,
                'tuning_profile': ''}]}}
        before = copy.deepcopy(project)
        records = geometry_defaults(project, {})
        values = {r['setting']: r['value'] for r in records}
        self.assertEqual(values, {
            'board.design_settings.rules.min_clearance': 0.27,
            'board.design_settings.rules.min_track_width': 0.12,
            'board.design_settings.defaults.zone_clearance': 0.38,
            'board.design_settings.track_widths.0': 0,
            'board.design_settings.track_widths.1': 0.31,
            'board.design_settings.via_dimensions.0.diameter': 0.7,
            'board.design_settings.via_dimensions.0.drill': 0.3,
            'net_settings.classes.Default.clearance': 0.27,
            'net_settings.classes.Default.track_width': 0.33,
            'net_settings.classes.Default.via_drill': 0.3})
        self.assertTrue(all(r['enforcement'] == 'advisory' and r['native_enforcement'] == 'retained'
                            and r['consumers'] and r['citation'] is None for r in records))
        self.assertEqual(project, before)

    def test_geometry_sources_require_allowed_class_and_exact_value(self):
        """AGENTEE-348 ruling (b): only a cited owner/manufacturer/datasheet value leaves the inventory.

        A free-text label of those classes was previously excluded as sourced; it is now
        disclosed as unsourced geometry, labelled, uncited and with its reason, never dropped.
        """
        for source, cited in (('owner', True), ('manufacturer', True), ('datasheet', True),
                              ('owner', False), ('manufacturer', False), ('datasheet', False),
                              ('engineering', True), ('engineering', False), ('default', False)):
            with self.subTest(source=source, cited=cited):
                declared = {'schema': 'fome-mc.constraint-provenance/1', 'board_setup_rules': {
                    'min_clearance': {'class': source, 'basis': 'Original fixture limit', 'value_mm': 0.17}},
                    'net_class_default': {'class': source, 'basis': 'Original fixture target', 'track_width_mm': 0.29}}
                if cited:
                    cite(declared, declared['board_setup_rules']['min_clearance'], declared['net_class_default'])
                project, record = import_constraints({'net_settings': {'classes': [
                    {'name': 'Default', 'track_width': 0.4}]}}, json_bytes(declared), 'source.json', 'fixture')
                records = geometry_defaults(project, record)
                if not cited and source != 'default':
                    self.assertEqual(sorted((r['setting'], r['value'], r['class'], r['citation'], r['enforcement'])
                                            for r in records),
                                     [('board.design_settings.rules.min_clearance', 0.17, source, None, 'advisory'),
                                      ('net_settings.classes.Default.track_width', 0.29, source, None, 'advisory')])
                    self.assertTrue(all(r['unsourced_reason'] ==
                                        f'free-text {source} label without a resolvable citation' for r in records))
                elif source in ('owner', 'manufacturer', 'datasheet'):
                    self.assertEqual(records, [])
                    self.assertEqual(project['board']['design_settings']['rules']['min_clearance'], 0.17)
                    self.assertEqual(project['net_settings']['classes'][0]['track_width'], 0.29)
                    project['board']['design_settings']['rules']['min_clearance'] = 0.18
                    self.assertEqual([r['value'] for r in geometry_defaults(project, record)], [0.18])
                else:
                    self.assertTrue(records)
                    self.assertTrue(all(r['enforcement'] == 'advisory' for r in records))

    def test_geometry_generated_rules_retain_exact_constraints_and_consumers(self):
        raw = b'(version 1)\n(rule "generated" (condition "A.NetClass == \'Power\'") (constraint track_width (min 0.37mm)))\n'
        record = {'generated_rules': {'generated': {'class': 'default', 'citation': None}}}
        rows = geometry_defaults({}, record, raw)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['value'], ['constraint', 'track_width', ['min', '0.37mm']])
        self.assertEqual(rows[0]['consumers'], ['router'])
        for source in ('owner', 'manufacturer', 'datasheet'):
            record['generated_rules']['generated'] = {'class': source, 'citation': {'path': 'source.json'}}
            self.assertEqual(geometry_defaults({}, record, raw), [])
        # AGENTEE-348 H1 (was []): a captured native form is not sourced by the file itself;
        # without a resolving declaration it is disclosed with its reason.
        row, = geometry_defaults({}, {}, raw)
        self.assertEqual((row['class'], row['citation'], row['unsourced_reason']),
                         ('default', None, 'captured native rule has no owner decision or provenance declaration'))
        with self.assertRaises(ValueError):
            geometry_defaults({}, {}, b'(rule')

    def test_geometry_sourced_class_without_citation_stays_inventoried(self):
        raw = b'(version 1)\n(rule "generated" (constraint clearance (min 0.37mm)))\n'
        for source in ('owner', 'manufacturer', 'datasheet'):
            with self.subTest(source=source):
                project = {'board': {'design_settings': {'rules': {'min_clearance': 0.37}}}}
                record = {'derived_settings': [{'setting': 'board.design_settings.rules.min_clearance',
                    'value': 0.37, 'class': source, 'citation': None}],
                    'generated_rules': {'generated': {'class': source, 'citation': None}}}
                rows = geometry_defaults(project, record, raw)
                self.assertEqual(len(rows), 2)
                self.assertTrue(all(r['class'] == source and r['citation'] is None and
                                    r['enforcement'] == 'advisory' for r in rows))

    def test_new_geometry_setting_is_inventoried_without_a_known_field_allowlist(self):
        project = {'board': {'design_settings': {'rules': {'future_geometry_limit': 0.41}}}}
        self.assertEqual(geometry_defaults(project, {})[0]['setting'],
                         'board.design_settings.rules.future_geometry_limit')

    def test_shadowed_generated_sources_are_positional_and_fail_closed(self):
        raw = b'(version 1)\n(rule "same" (constraint clearance (min 0.2mm)))\n(rule "same" (constraint clearance (min 0.4mm)))\n'
        source = dict(index=1, name='same', **{'class': 'default', 'citation': None})
        rows = geometry_defaults({}, {'shadowed_generated_rules': [source]}, raw)
        # AGENTEE-348 H1 (was 1 row): the unshadowed captured native form is disclosed too.
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['setting'], 'native_rules.1.same.0')
        self.assertEqual(rows[0]['value'][-1], ['min', '0.2mm'])
        self.assertEqual((rows[1]['setting'], rows[1]['citation']), ('native_rules.2.same.0', None))
        for bad in (None, {}, [None], [{}], [dict(source, index=True)],
                    [dict(source, index=-1)], [dict(source, index=99)],
                    [dict(source, name='wrong')], [source, source]):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, 'geometry inventory'):
                geometry_defaults({}, {'shadowed_generated_rules': bad}, raw)

    def test_new_editor_default_is_not_silently_omitted(self):
        project = {'board': {'design_settings': {'defaults': {'future_geometry_limit': 0.41}}}}
        row, = geometry_defaults(project, {})
        self.assertEqual((row['setting'], row['value'], row['consumers']),
                         ('board.design_settings.defaults.future_geometry_limit', 0.41, ['router', 'zone_filler']))

    def test_every_class_and_missing_source_classification(self):
        """AGENTEE-348 ruling (b): a free-text class label is advisory; its cited form is hard.

        Previously every non-default label with prose was hard. The label stays visible,
        with no citation and the recorded reason, and the value is still emitted.
        """
        project = {'board': {'design_settings': {'rules': {'min_clearance': 0.2, 'min_track_width': 0.2}}},
                   'net_settings': {'classes': [{'name': 'Default', 'clearance': 0.2, 'via_drill': 0.3}]}}
        original = copy.deepcopy(project)
        for provenance, cited in (('owner', False), ('manufacturer', False), ('datasheet', False),
                                  ('engineering', False), ('default', False), ('owner', True),
                                  ('manufacturer', True), ('datasheet', True), ('engineering', True)):
            declared = document()
            declared['board_setup_rules']['min_clearance']['class'] = provenance
            if cited:
                cite(declared, declared['board_setup_rules']['min_clearance'])
            imported, record = import_constraints(project, json_bytes(declared), 'constraints.json', 'test-digest')
            expected = 0.2 if provenance == 'default' else 0.15
            self.assertEqual(imported['board']['design_settings']['rules']['min_clearance'], expected)
            by_setting = {r['setting']: r for r in record['settings']}
            result = by_setting['board.design_settings.rules.min_clearance']
            self.assertEqual(result['class'], provenance)
            self.assertEqual(result['enforcement'], 'hard' if cited else 'advisory')
            if cited:
                self.assertEqual(result['citation']['path'], 'constraints.json')
                self.assertEqual(result['citation']['source'],
                                 declared['board_setup_rules']['min_clearance']['citation'])
            elif provenance != 'default':
                self.assertIsNone(result['citation'])
                self.assertEqual(result['unsourced_reason'],
                                 f'free-text {provenance} label without a resolvable citation')
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

    def test_owner_only_rules_are_sealed_without_changing_native_settings(self):
        from agent_ee.design_rule_provenance import PROJECT_KEY, finding_sources

        self.build('without-rules')
        control = read_json(self.root / 'without-rules/inputs/project/design.kicad_pro')
        source_project = (self.source / 'project/design.kicad_pro').read_bytes()
        raw = (b'(version 1)\n# Captured owner bytes, with no generated rules.\n'
               b'(rule "captured margin" (constraint clearance (min 0.15mm)))\n')
        rule = self.source / 'project/design.kicad_dru'
        rule.write_bytes(raw)
        self.scope['inputs'].append({'path': 'source/project/design.kicad_dru',
                                     'at': 'project/design.kicad_dru'})
        write_json(self.scope_path, self.scope)

        receipt = self.build('with-rules')
        board = self.root / 'with-rules/inputs/project/design.kicad_pcb'
        project = read_json(board.with_suffix('.kicad_pro'))
        record = project[PROJECT_KEY]
        self.assertNotIn('generated_rules', record)
        self.assertEqual(board.with_suffix('.kicad_dru').read_bytes(), raw)
        self.assertEqual(rule.read_bytes(), raw)
        self.assertEqual((self.source / 'project/design.kicad_pro').read_bytes(), source_project)
        self.assertEqual({k: v for k, v in project.items() if k != PROJECT_KEY},
                         {k: v for k, v in control.items() if k != PROJECT_KEY})
        native_rows = [row for row in record['unsourced_geometry']
                       if row['setting'].startswith('native_rules.')]
        self.assertEqual([row['value'] for row in native_rows],
                         [['constraint', 'clearance', ['min', '0.15mm']]])
        self.assertEqual(record['unsourced_geometry'],
                         receipt['constraint_provenance']['unsourced_geometry'])
        # Exercise the unchanged downstream verifier against actual saved files.
        sources = finding_sources(board)
        self.assertEqual(sources['rules']['captured margin']['class'], 'default')
        self.assertIsNone(sources['rules']['captured margin']['citation'])

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

    def test_template_geometry_changes_are_disclosed_in_bound_inventory(self):
        first = self.build('first')['constraint_provenance']['unsourced_geometry']
        self.project['net_settings']['classes'][0]['clearance'] = 0.43
        write_json(self.source / 'project/design.kicad_pro', self.project)
        second = self.build('second')['constraint_provenance']['unsourced_geometry']
        setting = 'net_settings.classes.Default.clearance'
        self.assertEqual([r['value'] for r in first if r['setting'] == setting], [0.25])
        self.assertEqual([r['value'] for r in second if r['setting'] == setting], [0.43])
        project = read_json(self.root / 'second/inputs/project/design.kicad_pro')
        self.assertEqual(project['agent_ee_constraint_provenance']['unsourced_geometry'], second)
        self.assertEqual(project['net_settings'], self.project['net_settings'])


class ProducerConstraintTest(ProducerFixture, unittest.TestCase):
    def test_producer_inventory_excludes_sourced_fab_limits_and_binds_to_project(self):
        receipt = self.build()
        project = read_json(self.root / 'produced/inputs/project/design.kicad_pro')
        record = receipt['constraint_provenance']
        rows = record['unsourced_geometry']
        self.assertEqual(project['agent_ee_constraint_provenance']['unsourced_geometry'], rows)
        hard = {r['setting'] for r in record['derived_settings']}
        self.assertFalse(hard & {r['setting'] for r in rows})
        self.assertTrue(rows)
        self.assertEqual(rows, geometry_defaults(project, record))

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
    def test_geometry_binding_is_rederived_before_drc_grading(self):
        import tempfile
        from agent_ee.design_rule_provenance import bind_project, finding_sources, PROJECT_KEY
        from agent_ee.checks import drc_admissible
        with tempfile.TemporaryDirectory() as temporary:
            board = Path(temporary) / 'design.kicad_pcb'
            raw = b'(version 1)\n(rule "generated" (constraint track_width (min 0.37mm)))\n'
            project = {'board': {'design_settings': {'track_widths': [0.37]}}}
            record = {'settings': inventory(project),
                      'generated_rules': {'generated': {'class': 'default', 'citation': None}}}
            bind_project(project, record, raw)
            board.with_suffix('.kicad_dru').write_bytes(raw)
            write_json(board.with_suffix('.kicad_pro'), project)
            finding_sources(board)
            report = dict(violations=[], unconnected_items=[], schematic_parity=[])
            drc_admissible(board.parent, board.name, report, '')
            for mutation in ('empty', 'value', 'consumer', 'missing', 'preset', 'rule', 'rule_missing'):
                with self.subTest(mutation=mutation):
                    changed = copy.deepcopy(project)
                    rows = changed[PROJECT_KEY]['unsourced_geometry']
                    if mutation == 'empty':
                        rows.clear()
                    elif mutation == 'value':
                        rows[0]['value'] = 99
                    elif mutation == 'consumer':
                        rows[0]['consumers'] = []
                    elif mutation == 'missing':
                        del changed[PROJECT_KEY]['unsourced_geometry']
                    elif mutation == 'preset':
                        changed['board']['design_settings']['track_widths'][0] = 0.99
                    board.with_suffix('.kicad_dru').write_bytes(
                        raw.replace(b'0.37mm', b'0.99mm') if mutation == 'rule' else raw)
                    if mutation == 'rule_missing':
                        board.with_suffix('.kicad_dru').unlink()
                    write_json(board.with_suffix('.kicad_pro'), changed)
                    with self.assertRaisesRegex(ValueError, 'geometry inventory'):
                        finding_sources(board)
                    with self.assertRaisesRegex(ValueError, 'DRC constraint provenance:.*geometry inventory'):
                        drc_admissible(board.parent, board.name, report, '')

    def test_emission_witness_and_materialization_boundaries(self):
        import tempfile
        import hashlib
        from agent_ee.design_rule_provenance import bind_project, finding_sources, PROJECT_KEY
        from agent_ee.native_geometry_defaults import DEFAULTS
        with tempfile.TemporaryDirectory() as temporary:
            board = Path(temporary) / 'design.kicad_pcb'
            project = {'board': {'design_settings': {'track_widths': [0.37]}}}
            record = {'settings': inventory(project)}
            bind_project(project, record)
            emission = record['geometry_emission']
            self.assertEqual(hashlib.sha256(emission['project_bytes'].encode()).hexdigest(),
                             emission['sha256'])
            self.assertEqual(json.loads(emission['project_bytes'])[PROJECT_KEY],
                             {k: v for k, v in record.items() if k != 'geometry_emission'})
            for mutation in ('missing witness', 'hash', 'bytes', 'version', 'metadata',
                             'removed', 'changed to native default', 'unknown addition',
                             'nondefault addition', 'bool impersonation'):
                with self.subTest(mutation=mutation):
                    changed = copy.deepcopy(project)
                    witness = changed[PROJECT_KEY]['geometry_emission']
                    design = changed['board']['design_settings']
                    if mutation == 'missing witness':
                        del changed[PROJECT_KEY]['geometry_emission']
                    elif mutation == 'hash':
                        witness['sha256'] = '0' * 64
                    elif mutation == 'bytes':
                        witness['project_bytes'] += ' '
                    elif mutation == 'version':
                        witness['native_defaults_version'] = 'future'
                    elif mutation == 'metadata':
                        changed[PROJECT_KEY]['generated_rules'] = {}
                    elif mutation == 'removed':
                        del design['track_widths']
                    elif mutation == 'changed to native default':
                        design['track_widths'] = [0.0]
                    elif mutation == 'unknown addition':
                        design['future_geometry'] = False
                    elif mutation == 'nondefault addition':
                        design['zones_allow_external_fillets'] = True
                    else:
                        design['zones_allow_external_fillets'] = 0
                    write_json(board.with_suffix('.kicad_pro'), changed)
                    with self.assertRaisesRegex(ValueError, 'geometry inventory'):
                        finding_sources(board)
            # Every allowed addition is closed over a cited native setting/value;
            # adding it must not refresh or enlarge the original binding.
            for name, (value, citation) in DEFAULTS.items():
                with self.subTest(native_default=name):
                    changed = copy.deepcopy(project)
                    parts = name.split('.')
                    node = changed
                    for part in parts[:-1]:
                        if isinstance(node, list):
                            index = 0 if part == 'Default' else int(part)
                            while len(node) <= index:
                                node.append({})
                            node = node[index]
                        else:
                            node = node.setdefault(part, [] if part in {
                                'track_widths', 'via_dimensions', 'diff_pair_dimensions',
                                'teardrop_options', 'teardrop_parameters', 'classes'} else {})
                    if isinstance(node, list):
                        while len(node) <= int(parts[-1]):
                            node.append(0.0)
                        node[int(parts[-1])] = value
                    else:
                        node[parts[-1]] = value
                    if name.startswith('net_settings.classes.'):
                        changed['net_settings']['classes'][0]['name'] = 'Default'
                    # Track preset index zero was explicitly emitted as 0.37:
                    # materialization cannot excuse overwriting it.
                    if name == 'board.design_settings.track_widths.0':
                        continue
                    self.assertIn('10.0.7', citation)
                    write_json(board.with_suffix('.kicad_pro'), changed)
                    finding_sources(board)
                    self.assertEqual(changed[PROJECT_KEY], record)
                    if type(value) is float and value.is_integer():
                        node[int(parts[-1]) if isinstance(node, list) else parts[-1]] = int(value)
                        write_json(board.with_suffix('.kicad_pro'), changed)
                        finding_sources(board)

    def test_empty_binding_refuses_while_unbound_legacy_stays_supported(self):
        import tempfile
        from agent_ee.design_rule_provenance import finding_sources, PROJECT_KEY
        with tempfile.TemporaryDirectory() as temporary:
            board = Path(temporary) / 'legacy.kicad_pcb'
            write_json(board.with_suffix('.kicad_pro'), {})
            finding_sources(board)
            write_json(board.with_suffix('.kicad_pro'), {PROJECT_KEY: {}})
            with self.assertRaisesRegex(ValueError, 'no valid settings inventory'):
                finding_sources(board)

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
            self.assertEqual(classify_finding(finding, sources)['constraint_provenance']['class'], cls)
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


class NativeDefaultTableTest(unittest.TestCase):
    def test_pinned_native_defaults(self):
        import tempfile
        import subprocess
        from agent_ee.native_geometry_defaults import DEFAULTS, VERSION
        from agent_ee.design_rule_provenance import bind_project, finding_sources, PROJECT_KEY
        require_toolchain(native=True)
        with tempfile.TemporaryDirectory() as temporary:
            board = Path(temporary) / 'native.kicad_pcb'
            # Original minimal board and empty project: no third-party fixtures.
            board.write_text('(kicad_pcb (version 20260101) (generator "pcbnew") '
                '(general (thickness 1.6)) (paper "A4") '
                '(layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user)))')
            project = {}
            record = {'settings': []}
            bind_project(project, record)
            write_json(board.with_suffix('.kicad_pro'), project)
            script = ('import pcbnew,sys; '
                'assert pcbnew.GetBuildVersion().startswith(sys.argv[2]); '
                'b=pcbnew.LoadBoard(sys.argv[1]); pcbnew.SaveBoard(sys.argv[1],b)')
            result = subprocess.run(['/usr/bin/python3', '-c', script, str(board), VERSION],
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            saved = read_json(board.with_suffix('.kicad_pro'))
            rows = geometry_defaults(saved, {})
            self.assertEqual({r['setting']: r['value'] for r in rows},
                             {name: value for name, (value, _) in DEFAULTS.items()})
            self.assertEqual(saved[PROJECT_KEY], project[PROJECT_KEY])
            finding_sources(board)
            saved['board']['design_settings']['zones_allow_external_fillets'] = True
            write_json(board.with_suffix('.kicad_pro'), saved)
            with self.assertRaisesRegex(ValueError, 'geometry inventory'):
                finding_sources(board)


class NativeGeometryInventoryTest(PlaneFixture, unittest.TestCase):
    def test_zone_filler_template_clearance_is_disclosed_and_remains_advisory(self):
        from agent_ee.design_rule_provenance import bind_project, PROJECT_KEY
        template = read_json(Path(__file__).resolve().parents[1] /
                             'fixtures/netclass-completeness/native.kicad_pro')
        areas = []
        for clearance in (0.25, 0.85):
            project = copy.deepcopy(template)
            project['net_settings']['classes'][0]['clearance'] = clearance
            project['board']['design_settings'] = {'rules': {'min_clearance': clearance}}
            record = {'settings': inventory(project)}
            bind_project(project, record)
            path = (self.source / self.board).with_suffix('.kicad_pro')
            write_json(path, project)
            rows = record['unsourced_geometry']
            row, = [r for r in rows if r['setting'] == 'net_settings.classes.Default.clearance']
            self.assertEqual((row['value'], row['enforcement']), (clearance, 'advisory'))
            self.assertIn('zone_filler', row['consumers'])
            report = self.run_plan(str(clearance))
            self.assertEqual(report['execution'], 'ok', report)
            restored = self.root / str(clearance) / 'native/native-project/pod.kicad_pro'
            self.assertEqual(read_json(restored)[PROJECT_KEY]['unsourced_geometry'], rows)
            areas.append(sum(z['filled_area_mm2'] for z in report['zones']))
        self.assertGreater(areas[0], areas[1])


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
            # AGENTEE-348 ruling (b): a declared class gates only in its cited form; the same
            # free-text labels ('uncited-*', formerly blocking) are now advisory.
            for case in ('default', 'owner', 'manufacturer', 'datasheet', 'engineering', 'legacy-null',
                         'unrelated-class', 'uncited-owner', 'uncited-manufacturer', 'uncited-datasheet',
                         'uncited-engineering'):
                cls = case.removeprefix('uncited-')
                source = root / (case + '.kicad_pcb')
                source.write_bytes(original.read_bytes())
                declared = {'schema': 'fome-mc.constraint-provenance/1', 'board_setup_rules': {
                    'min_clearance': {'value_mm': 4, 'class': cls, 'basis': 'original test limit'}}}
                if case in ('owner', 'manufacturer', 'datasheet', 'engineering'):
                    cite(declared, declared['board_setup_rules']['min_clearance'])
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
                    self.assertEqual(geometry['clearance_nm'], 0 if cls == 'default' or cls != case else 4000000)
                self.assertEqual(source.with_suffix('.kicad_pro').read_bytes(), original_project)
                work = root / case
                work.mkdir()
                output = work / 'candidate.kicad_pcb'
                candidate = k.LoadBoard(str(source))
                next(f for f in candidate.GetFootprints() if f.GetReference() == 'C1').SetPosition(k.VECTOR2I(9000000, 3000000))
                k.SaveBoard(str(output), candidate)
                advisory = cls in ('default', 'legacy-null', 'unrelated-class') or cls != case
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
    def test_shadowed_generated_geometry_is_inventoried_without_owner_promotion(self):
        from agent_ee.design_rule_provenance import finding_sources
        self.use_net_constraints()
        write_json(self.source / 'rule-sources.json', document())
        self.plan['constraint_source'] = 'rule-sources.json'
        rule = self.source / 'project/design.kicad_dru'
        rule.parent.mkdir(exist_ok=True)
        raw = b'(version 1)\n(rule "agent_ee_netclass_RF_50" (constraint track_width (min 0.35mm)))\n'
        rule.write_bytes(raw)
        self.write()
        receipt = self.build()
        board = self.root / 'produced/inputs/project/design.kicad_pcb'
        project = read_json(board.with_suffix('.kicad_pro'))
        record = receipt['constraint_provenance']
        rows = record['unsourced_geometry']
        native = [r for r in rows if r['setting'].startswith('native_rules.')]
        # AGENTEE-348 H1 (was 3 rows and an 'owner' RF_50 rule): the captured owner file confers
        # no authority without a declaration, so its RF_50 form is disclosed and not owner.
        self.assertEqual(len(native), 4)
        shadowed, owner_form = sorted((r for r in native if '.agent_ee_netclass_RF_50.' in r['setting']),
                                      key=lambda r: int(r['setting'].split('.')[1]))
        self.assertEqual((shadowed['class'], shadowed['citation']), ('default', None))
        self.assertEqual((owner_form['class'], owner_form['citation'], owner_form['unsourced_reason']),
                         ('default', None, 'captured native rule has no owner decision or provenance declaration'))
        self.assertEqual(rows, geometry_defaults(project, record, board.with_suffix('.kicad_dru').read_bytes()))
        self.assertEqual(finding_sources(board)['rules']['agent_ee_netclass_RF_50']['class'], 'default')
        self.assertEqual((board.parent / 'design.owner.kicad_dru').read_bytes(), raw)

    def test_generated_geometry_is_disclosed_in_receipt_and_project(self):
        self.use_net_constraints()
        receipt = self.build()
        inputs = self.root / 'produced/inputs'
        project = read_json(inputs / 'project/design.kicad_pro')
        record = receipt['constraint_provenance']
        raw = (inputs / 'project/design.kicad_dru').read_bytes()
        rows = record['unsourced_geometry']
        self.assertEqual(rows, geometry_defaults(project, record, raw))
        self.assertEqual(rows, project['agent_ee_constraint_provenance']['unsourced_geometry'])
        native = [r for r in rows if r['setting'].startswith('native_rules.')]
        self.assertEqual(len(native), 3)
        self.assertTrue(all(r['class'] == 'default' and r['enforcement'] == 'advisory' for r in native))
        self.assertEqual({r['value'][1] for r in native}, {'track_width'})

    def test_declared_generated_rule_is_hard_only_in_its_cited_form(self):
        """AGENTEE-348: a labelled custom_rules declaration resolves through the same citation model."""
        from agent_ee.design_rule_provenance import finding_sources
        self.use_net_constraints()
        name = 'agent_ee_netclass_PWR'
        for cited in (False, True):
            with self.subTest(cited=cited):
                declared = document()
                spec = {'rule': name, 'class': 'datasheet', 'basis': 'Original fixture power width'}
                declared['custom_rules'] = {'design.kicad_dru': [spec]}
                if cited:  # H3: the captured text states the emitted rule's 1.012 mm payload
                    text = 'Original fixture power width: 1.012 mm'
                    declared['source_records'] = {'fixture-sources/pwr.txt': text}
                    spec['citation'] = citation('datasheet', text, 'fixture-sources/pwr.txt')
                write_json(self.source / 'rule-sources.json', declared)
                self.plan['constraint_source'] = 'rule-sources.json'
                self.write()
                receipt = self.build('cited' if cited else 'uncited')
                board = self.root / ('cited' if cited else 'uncited') / 'inputs/project/design.kicad_pcb'
                source = finding_sources(board)['rules'][name]
                rows = [r for r in receipt['constraint_provenance']['unsourced_geometry']
                        if '.' + name + '.' in r['setting']]
                self.assertEqual(source['class'], 'datasheet')
                if cited:
                    self.assertEqual(source['citation']['source'], spec['citation'])
                    self.assertEqual(rows, [])
                else:
                    self.assertIsNone(source['citation'])
                    self.assertEqual(source['unsourced_reason'], 'free-text datasheet label without a resolvable citation')
                    self.assertTrue(rows)
                    self.assertTrue(all(r['class'] == 'datasheet' and r['citation'] is None and
                                        r['unsourced_reason'] == source['unsourced_reason'] for r in rows))

    def test_declared_sources_compose_net_rules_and_preserve_owner_bytes(self):
        from agent_ee.design_rule_provenance import finding_sources
        self.use_net_constraints()
        rule = self.source / 'project/design.kicad_dru'
        rule.parent.mkdir(exist_ok=True)
        raw = b'(version 1)\n# Original owner comment with a decoy (version 1)\n(rule "agent_ee_netclass_RF_50" (constraint track_width (min 0.35mm)))\n(rule "original owner gap" (constraint clearance (min 0.15mm)))\n'
        rule.write_bytes(raw)
        # AGENTEE-348 H1 migration: the owner rules carry committed, value-bound owner decisions.
        write_json(self.source / 'rule-sources.json', owner_rules(document(), raw, 'design.kicad_dru'))
        self.plan['constraint_source'] = 'rule-sources.json'
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

    def test_native_clean_report_with_edited_geometry_inventory_is_unknown(self):
        import shutil
        import tempfile
        import time
        from agent_ee.checks import run_checks
        from agent_ee.design_rule_provenance import bind_project, PROJECT_KEY
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source'
            shutil.copytree(repo / 'fixtures/rc/control', source)
            project_path = next(source.glob('*.kicad_pro'))
            project = read_json(project_path)
            project['board']['design_settings']['track_widths'] = [0.37]
            record = {'settings': inventory(project)}
            bind_project(project, record)
            self.assertTrue(record['unsourced_geometry'])
            scope = read_json(repo / 'fixtures/rc/control.scope.json')
            for entry in scope['inputs']:
                entry['path'] = (str(source) if entry['at'] == 'project' else
                                 str((repo / 'fixtures/rc' / entry['path']).resolve()))
            scope['checks'] = [{'id': 'drc', 'kind': 'drc'}]
            write_json(root / 'scope.json', scope)
            for edited in (False, True):
                if edited:
                    project[PROJECT_KEY]['unsourced_geometry'] = []
                write_json(project_path, project)
                run = root / ('edited' if edited else 'valid')
                run.mkdir()
                capture(root / 'scope.json', run)
                result = run_checks(run, time.monotonic() + 120)[0]
                self.assertEqual(read_json(run / 'work/drc/drc.json')['violations'], [])
                if edited:
                    self.assertEqual((result['execution'], result['outcome']), ('malformed', 'unknown'), result)
                    self.assertIn('geometry inventory', result['summary'])
                else:
                    self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)

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
                if cls != 'default':  # AGENTEE-348 cited form keeps the sourced limit hard.
                    cite(declared, declared['board_setup_rules']['min_text_height'])
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
