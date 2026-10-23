"""AGENTEE-348 G4 review H1-H5: the reviewer's probes as committed regressions.

Each case names the review finding it closes. A label, a file's presence or a citation
whose captured text does not state the governed value never grants hard authority.
"""
import copy
import hashlib
import tempfile
from fractions import Fraction
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
OUTSIDE = 'quote has a quantity outside the closed grammar: '
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



class TypedLiteralTest(unittest.TestCase):
    """Round-2 H1: a quoted literal is read whole; a suffix or a dropped sign never qualifies."""

    def setting(self, quote, value_mm=3):
        path = 'review/signed.txt'
        document = {'schema': SCHEMA, 'source_records': {path: quote}, 'board_setup_rules': {'min_clearance': {
            'value_mm': value_mm, 'class': 'datasheet', 'basis': 'review numeric token',
            'citation': citation('datasheet', quote, path)}}}
        project, record = import_constraints({}, json_bytes(document), 'review/limits.json', 'probe')
        return record['settings'][0], geometry_defaults(project, record)

    def locality(self, quote, nm):
        path = 'review/signed.txt'
        pair = {'max_distance_nm': nm, 'provenance': {'class': 'datasheet', 'basis': 'review numeric token',
                'derivation': {'kind': 'numeric', 'value_nm': nm, 'source': citation('datasheet', quote, path)}}}
        return locality_provenance.classify(pair, {path: quote})

    def test_exponent_and_signed_literals_never_qualify_their_suffix(self):
        for quote in ('Captured length: 1e-3 mm', 'Captured length: -3 mm', 'Captured length: \u22123 mm',
                      'Captured length: x-3 mm', 'Captured length: 1-3 mm', 'Captured length: 13 mm',
                      'Captured length: 3.3 mm', 'Captured length: 1/3 mm', 'Captured length: 1\u20193 mm',
                      "Captured length: 1'3 mm", 'Captured length: 1 3 mm', 'Captured length: 1\u20093 mm',
                      'Captured length: 1,3 mm'):
            with self.subTest(quote=quote):
                setting, rows = self.setting(quote)
                # Third-HOLD grammar: an in-grammar mismatch or an out-of-grammar quote, never hard.
                self.assertEqual(setting['enforcement'], 'advisory')
                self.assertTrue(setting['unsourced_reason'] == 'cited quote does not state the declared value'
                                or setting['unsourced_reason'].startswith(OUTSIDE), setting)
                self.assertEqual(len(rows), 1)
                self.assertEqual(self.locality(quote, 3000000)['enforcement'], 'advisory')
        for quote in ('Captured length: 3 mm', 'Captured length: 3mm', 'Captured length: +3 mm'):
            with self.subTest(control=quote):
                setting, rows = self.setting(quote)
                self.assertEqual((setting['enforcement'], rows), ('hard', []))
                self.assertEqual(self.locality(quote, 3000000)['enforcement'], 'hard')
        # The complete exponent literal is read exactly: 1e-3 mm is 0.001 mm.
        setting, rows = self.setting('Captured length: 1e-3 mm', 0.001)
        self.assertEqual((setting['enforcement'], rows), ('hard', []))


class CountFactTest(unittest.TestCase):
    """Round-2 H2: a dimensionless count qualifies only against an explicit count of its kind."""

    RAW = b'(version 1) (rule "spoke rule" (constraint thermal_spoke_count (min 3)))'

    def sources(self, root, quote=None):
        board = Path(root) / 'candidate.kicad_pcb'
        board.with_suffix('.kicad_dru').write_bytes(self.RAW)
        project, record = {}, {}
        if quote is not None:
            path = 'review/count.txt'
            spec = {'rule': 'spoke rule', 'class': 'owner', 'basis': 'count probe',
                    'citation': citation('owner', quote, path)}
            document = {'schema': SCHEMA, 'source_records': {path: quote}, 'board_setup_rules': {},
                        'custom_rules': {'candidate.kicad_dru': [spec]}}
            project, record = import_constraints({}, json_bytes(document), 'review/limits.json', 'probe')
            bind_project(project, record, self.RAW)
        write_json(board.with_suffix('.kicad_pro'), project)
        sources = finding_sources(board)
        hard = [s for s in sources['settings'] if s['setting'].startswith('native_rules.')
                and s['enforcement'] == 'hard']
        return sources['rules']['spoke rule'], hard, geometry_defaults(project, record, self.RAW)

    def test_bound_count_rule_needs_an_explicit_spoke_count(self):
        for quote in ('Captured board thickness: 3 mm. This record states no thermal spoke count.',
                      'Captured board thickness: 3mm.', 'Captured value: 3', 'Captured: 3 vias',
                      'Captured: 4 thermal spokes', 'Captured: 3.0 thermal spokes'):
            with self.subTest(quote=quote), tempfile.TemporaryDirectory() as root:
                rule, hard, rows = self.sources(root, quote)
                self.assertIsNone(rule['citation'])
                # Third-HOLD grammar: a non-integer count literal is outside the closed grammar.
                self.assertTrue(rule['unsourced_reason'] == 'cited quote does not state the declared value'
                                or rule['unsourced_reason'].startswith(OUTSIDE), rule)
                self.assertEqual(hard, [])
                self.assertEqual([r['unsourced_reason'] for r in rows], [rule['unsourced_reason']])
        for quote in ('Captured: 3 thermal spokes', 'Captured: spoke count: 3', 'Captured: 3 spokes'):
            with self.subTest(control=quote), tempfile.TemporaryDirectory() as root:
                rule, hard, rows = self.sources(root, quote)
                self.assertIsNotNone(rule['citation'])
                self.assertEqual([(s['value'], s['class']) for s in hard], [(3, 'owner')])
                self.assertEqual(rows, [])
        self.assertFalse(source_authority.states('3 mm', [], [('thermal_spoke_count', 3)]))
        self.assertFalse(source_authority.states('3mm', [], [('thermal_spoke_count', 3)]))
        self.assertFalse(source_authority.states('3 spokes', [3], []))

    def test_standalone_count_rule_is_advisory_and_disclosed(self):
        with tempfile.TemporaryDirectory() as root:
            rule, hard, rows = self.sources(root)
            self.assertEqual((rule['class'], rule['citation']), ('default', None))
            self.assertEqual(rule['unsourced_reason'],
                             'captured native rule has no owner decision or provenance declaration')
            self.assertEqual(hard, [])
            self.assertEqual([r['unsourced_reason'] for r in rows], [rule['unsourced_reason']])


class LocalityDatasheetModelTest(unittest.TestCase):
    """Round-2 H3: locality datasheet authority goes through the common exact-unit model."""

    def pair(self, quote, nm, layout=None):
        path = 'review/source.txt'
        derivation = ({'kind': 'numeric', 'value_nm': nm} if layout is None else
                      {'kind': 'layout', **layout, 'scale_reference': 'agent selected scale'})
        derivation['source'] = citation('datasheet', quote, path)
        return {'max_distance_nm': nm, 'provenance': {'class': 'datasheet', 'basis': 'review', 'derivation': derivation}}, {path: quote}

    def test_layout_needs_its_distance_stated_by_the_captured_source(self):
        quote = 'Captured diagram: illustrative only; no numeric distance or scale requirement.'
        layout = {'distance_units': 10, 'scale_nm_per_unit': 100000}
        pair, records = self.pair(quote, 1000000, layout)
        self.assertEqual(locality_provenance.classify(pair, records),
                         {'class': 'datasheet', 'enforcement': 'advisory',
                          'reason': 'layout derivation scale is not validated by the captured source'})
        self.assertEqual(source_authority.cite('datasheet', pair['provenance']['derivation']['source'],
                                               records, [1])['enforcement'], 'advisory')
        facts, doc = board(), declaration(limit=1000000)
        doc['source_records'] = records
        for role in ('supply', 'return'):
            doc['obligations'][0][role]['provenance'] = copy.deepcopy(pair['provenance'])
        graded = locality.grade(facts, doc)
        self.assertEqual((graded['status'], graded['defects']), ('info', []))
        # A captured source that states the derived distance qualifies the same layout.
        pair, records = self.pair('Captured reference layout: 1 mm', 1000000, layout)
        self.assertEqual(locality_provenance.classify(pair, records)['enforcement'], 'hard')

    def test_every_supported_unit_agrees_across_consumers(self):
        for quote, nm, mm in (('Captured: 1 mm', 1000000, 1), ('Captured: 1000 um', 1000000, 1),
                              ('Captured: 1000 \u00b5m', 1000000, 1), ('Captured: 1000000 nm', 1000000, 1),
                              ('Captured: 10 mil', 254000, Fraction(254, 1000)), ('Captured: 1e3 um', 1000000, 1)):
            for declared_nm, expected in ((nm, 'hard'), (nm + 1, 'advisory')):
                with self.subTest(quote=quote, declared_nm=declared_nm):
                    pair, records = self.pair(quote, declared_nm)
                    local = locality_provenance.classify(pair, records)
                    common = source_authority.cite('datasheet', pair['provenance']['derivation']['source'],
                                                   records, [Fraction(declared_nm, 10 ** 6)])
                    self.assertEqual((local['enforcement'], common['enforcement']), (expected, expected))


if __name__ == '__main__':
    unittest.main()
