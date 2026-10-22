"""AGENTEE-348 G4 review H1-H5: the reviewer's probes as committed regressions.

Each case names the review finding it closes. A label, a file's presence or a citation
whose captured text does not state the governed value never grants hard authority.
"""
import hashlib
import tempfile
import unittest
from pathlib import Path

from agent_ee import electrical_locality as locality, locality_provenance, source_authority
from agent_ee.design_rule_provenance import (bind_project, classify_finding, finding_sources,
                                             geometry_defaults, import_constraints)
from agent_ee.storage import json_bytes, read_json, write_json
from owner_fixture import citation
from test_design_rule_provenance import document
from test_electrical_locality import board, declaration
from test_scaffold_producer import ProducerFixture
from toolchain import require_toolchain

SCHEMA = 'fome-mc.constraint-provenance/1'
RULE = b'(version 1)\n(rule "uncited template" (constraint clearance (min 4mm)))\n'
FINDING = {'type': 'clearance',
           'description': "Clearance violation (rule 'uncited template' min 4.0000 mm; actual 0.1000 mm)"}


def owner_rule_document(text):
    """A constraint document declaring the native rule as owner, cited over ``text``."""
    path = 'review/owner-rule.txt'
    spec = {'rule': 'uncited template', 'class': 'owner', 'basis': 'review owner rule',
            'citation': citation('owner', text, path)}
    return {'schema': SCHEMA, 'board_setup_rules': {}, 'source_records': {path: text},
            'custom_rules': {'candidate.kicad_dru': [spec]}}


class NativeOwnerRuleTest(unittest.TestCase):
    """H1: a native custom-rule file confers no owner authority by its path or hash."""

    def board(self, root, document=None):
        board = Path(root) / 'candidate.kicad_pcb'
        board.with_suffix('.kicad_dru').write_bytes(RULE)
        project = {}
        if document is not None:
            project, record = import_constraints({}, json_bytes(document), 'review/limits.json', 'probe')
            bind_project(project, record, RULE)
        write_json(board.with_suffix('.kicad_pro'), project)
        return board

    def test_standalone_native_rule_is_advisory_and_disclosed(self):
        with tempfile.TemporaryDirectory() as root:
            sources = finding_sources(self.board(root))
            rule = sources['rules']['uncited template']
            self.assertIsNone(rule['citation'])
            self.assertEqual(rule['capture']['sha256'], hashlib.sha256(RULE).hexdigest())
            self.assertEqual(rule['unsourced_reason'],
                             'captured native rule has no owner decision or provenance declaration')
            result = classify_finding(dict(FINDING), sources)['constraint_provenance']
            self.assertEqual((result['class'], result['enforcement']), ('default', 'advisory'))
        row, = geometry_defaults({}, {}, RULE)
        self.assertEqual((row['setting'], row['class'], row['citation'], row['unsourced_reason']),
                         ('native_rules.1.uncited template.0', 'default', None, rule['unsourced_reason']))

    def test_bound_native_rule_hard_only_with_a_value_bound_owner_decision(self):
        for text, hard in (('Review owner decision: clearance at least 4 mm', True),
                           ('Review owner decision: clearance at least 3 mm', False),
                           ('Review owner decision: no number here', False)):
            with self.subTest(text=text), tempfile.TemporaryDirectory() as root:
                board = self.board(root, owner_rule_document(text))
                sources = finding_sources(board)
                rule = sources['rules']['uncited template']
                result = classify_finding(dict(FINDING), sources)['constraint_provenance']
                record = read_json(board.with_suffix('.kicad_pro'))['agent_ee_constraint_provenance']
                rows = geometry_defaults({}, record, RULE)
                if hard:
                    self.assertEqual((result['class'], result['enforcement']), ('owner', 'hard'))
                    self.assertEqual(rule['citation']['sha256'], hashlib.sha256(RULE).hexdigest())
                    self.assertEqual(rows, [])
                else:
                    self.assertIsNone(rule['citation'])
                    self.assertEqual(rule['unsourced_reason'], 'cited quote does not state the declared value')
                    self.assertEqual(result['enforcement'], 'advisory')
                    self.assertEqual([r['unsourced_reason'] for r in rows], [rule['unsourced_reason']])
        # A bound project without any declaration of the rule stays advisory too.
        with tempfile.TemporaryDirectory() as root:
            board = self.board(root, {'schema': SCHEMA, 'board_setup_rules': {}})
            self.assertIsNone(finding_sources(board)['rules']['uncited template']['citation'])


class LocalityOwnerValueTest(unittest.TestCase):
    """H2: one owner decision and value get one verdict in locality and the design-rule import."""

    def test_owner_bound_must_be_stated_by_the_decision(self):
        text = 'Review synthetic numeric requirement: 0.15 mm'
        path = 'review/numeric.txt'
        records = {path: text}
        reference = citation('owner', text, path)
        for nm, expected in ((999000000, 'advisory'), (150000, 'hard'), (150001, 'advisory')):
            with self.subTest(nm=nm):
                local = locality_provenance.classify({'max_distance_nm': nm, 'provenance': {
                    'class': 'owner', 'basis': 'review', 'decision_reference': reference}}, records)
                document = {'schema': SCHEMA, 'source_records': records, 'board_setup_rules': {'min_clearance': {
                    'value_mm': nm / 10 ** 6, 'class': 'owner', 'basis': 'review', 'citation': reference}}}
                setting, = import_constraints({}, json_bytes(document), 'review/limits.json', 'probe')[1]['settings']
                self.assertEqual(local['enforcement'], expected)
                self.assertEqual(setting['enforcement'], expected)
                if expected == 'advisory':
                    self.assertEqual(local['reason'], 'cited quote does not state the declared value')
                    self.assertEqual(setting['unsourced_reason'], local['reason'])
        # Exact unit conversion: a nanometre statement qualifies the same bound.
        text = 'Review synthetic numeric requirement: 150000 nm'
        self.assertEqual(locality_provenance.classify({'max_distance_nm': 150000, 'provenance': {
            'class': 'owner', 'basis': 'review', 'decision_reference': citation('owner', text, 'review/nm.txt')}},
            {'review/nm.txt': text})['enforcement'], 'hard')


class LocalityManufacturerTest(unittest.TestCase):
    """H4: an uncited manufacturer maximum below the captured floor is advisory, never infeasible."""

    def grade(self, provenance):
        facts, doc = board(), declaration(limit=149999)
        facts['footprints'][0]['pads'][0]['size_nm'] = [1000000, 1000000]
        facts['footprints'][1]['pads'][1]['size_nm'] = [1000000, 1000000]
        for role in ('supply', 'return'):
            doc['obligations'][0][role]['provenance'] = dict(provenance)
        doc['source_records'] = {**doc['source_records'], 'review/fab.txt': 'Review manufacturer limit 0.149999 mm'}
        return locality.grade(facts, doc)

    def test_uncited_manufacturer_is_advisory_with_the_captured_floor(self):
        result = self.grade({'class': 'manufacturer', 'basis': 'unsourced review label'})
        self.assertEqual(result['status'], 'info', result)
        self.assertFalse(result['defects'])
        self.assertTrue(all(a['below_floor'] and a['manufacturing_floor']['floor_nm'] == 150000
                            for a in result['advisories']))
        self.assertEqual({a['constraint_provenance']['reason'] for a in result['advisories']},
                         {'free-text manufacturer label without a resolvable citation'})
        cited = self.grade({'class': 'manufacturer', 'basis': 'review', 'citation': citation(
            'manufacturer', 'Review manufacturer limit 0.149999 mm', 'review/fab.txt')})
        self.assertEqual([d['kind'] for d in cited['defects']], ['locality_infeasible'] * 2)


class ManufacturerCaptureValueTest(unittest.TestCase):
    """H5: a manufacturer capture qualifies only the numeric fact it records."""

    def import_value(self, value_mm, **change):
        record = locality_provenance.manufacturing_record()
        cite = {'kind': 'manufacturer_capture', 'capture': 'pcb', 'sha256': record['sources']['pcb']['sha256'],
                'locator': record['locators']['pcb'], 'fact': 'different_net_clearance_nm', **change}
        document = {'schema': SCHEMA, 'board_setup_rules': {'min_clearance': {
            'value_mm': value_mm, 'class': 'manufacturer', 'basis': 'review', 'citation': cite}}}
        project, provenance = import_constraints({}, json_bytes(document), 'review/limits.json', 'probe')
        return provenance['settings'][0], geometry_defaults(project, provenance)

    def test_capture_binds_the_declared_value_to_its_recorded_fact(self):
        setting, rows = self.import_value(999)
        self.assertEqual(setting['enforcement'], 'advisory')
        self.assertEqual(setting['unsourced_reason'],
                         'declared value differs from the captured manufacturer fact different_net_clearance_nm')
        self.assertEqual(len(rows), 1)
        setting, rows = self.import_value(0.15)
        self.assertEqual((setting['enforcement'], rows), ('hard', []))
        for change, reason in (({'fact': 'invented_nm'}, 'manufacturer capture pcb records no fact invented_nm'),
                               ({'locator': 'review elsewhere'},
                                'manufacturer citation locator differs from the captured record locator')):
            with self.subTest(change=change):
                setting, rows = self.import_value(0.15, **change)
                self.assertEqual((setting['enforcement'], setting['unsourced_reason']), ('advisory', reason))
                self.assertEqual(len(rows), 1)
        empty = {'kind': 'manufacturer_capture', 'capture': 'pcb', 'locator': 'x', 'sha256': 'y', 'fact': 'z'}
        self.assertEqual(source_authority.cite('manufacturer', empty, {}, []),
                         {'enforcement': 'advisory', 'reason': 'no declared value to bind to the citation'})


class GeneratedRulePayloadTest(ProducerFixture, unittest.TestCase):
    """H3: a generated rule qualifies against its emitted numeric constraints."""

    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)

    def build_with(self, name, text):
        self.use_net_constraints()
        declared = document()
        path = 'review/' + name + '.txt'
        spec = {'rule': 'agent_ee_netclass_PWR', 'class': 'datasheet', 'basis': 'review source',
                'citation': citation('datasheet', text, path)}
        declared['source_records'] = {path: text}
        declared['custom_rules'] = {'design.kicad_dru': [spec]}
        write_json(self.source / 'rule-sources.json', declared)
        self.plan['constraint_source'] = 'rule-sources.json'
        self.write()
        receipt = self.build(name)
        board = self.root / name / 'inputs/project/design.kicad_pcb'
        rows = [r for r in receipt['constraint_provenance']['unsourced_geometry']
                if '.agent_ee_netclass_PWR.' in r['setting']]
        return finding_sources(board)['rules']['agent_ee_netclass_PWR'], rows

    def test_nonnumeric_mismatched_and_bound_sources(self):
        for name, text, hard in (
                ('nonnumeric', 'Review synthetic source: there is no numeric design requirement here.', False),
                ('mismatched', 'Review synthetic source: power width at least 0.5 mm', False),
                ('bound', 'Review synthetic source: power width at least 1.012 mm', True)):
            with self.subTest(name=name):
                source, rows = self.build_with(name, text)
                self.assertEqual(source['class'], 'datasheet')
                if hard:
                    self.assertIsNotNone(source['citation'])
                    self.assertEqual(rows, [])
                else:
                    self.assertIsNone(source['citation'])
                    self.assertEqual(source['unsourced_reason'], 'cited quote does not state the declared value')
                    self.assertTrue(rows)
                    self.assertTrue(all(r['unsourced_reason'] == source['unsourced_reason'] for r in rows))


if __name__ == '__main__':
    unittest.main()
