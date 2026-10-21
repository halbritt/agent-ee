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
        run = self.root / 'capture'
        run.mkdir()
        capture(self.root / 'produced/scope.json', run)
        self.assertIn('constraints.json', read_json(run / 'baseline.json')['files'])
        self.assertEqual((self.root / 'produced/inputs/project/design.kicad_dru').read_bytes(), rule.read_bytes())
        self.assertIn('project/design.kicad_dru', read_json(run / 'baseline.json')['files'])
        self.assertNotIn('project/design.kicad_dru', read_json(self.root / 'produced/scope.json')['mutable_inputs'])
