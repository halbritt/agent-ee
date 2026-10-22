"""AGENTEE-348: one citation model for owner/manufacturer/datasheet/engineering classes."""
import hashlib
import json
import unittest

from agent_ee import locality_provenance, source_authority
from agent_ee.design_rule_provenance import geometry_defaults, import_constraints
from agent_ee.storage import json_bytes
from owner_fixture import REPOSITORY, citation, committed, repository

TEXT = 'Original fixture decision: minimum clearance 0.15 mm'
PATH = 'fixture-sources/authority.txt'
RECORDS = {PATH: TEXT}


def owner(**changes):
    decision = {'kind': 'owner_decision', 'path': PATH, 'sha256': hashlib.sha256(TEXT.encode()).hexdigest(),
                'locator': 'clearance decision', 'quote': TEXT, 'owner': 'Original fixture author',
                **committed(PATH, TEXT)}
    decision.update(changes)
    return {key: value for key, value in decision.items() if value is not None}


def constraint(cls, cite=None):
    document = {'schema': 'fome-mc.constraint-provenance/1', 'source_records': dict(RECORDS),
                'board_setup_rules': {'min_clearance': {'value_mm': 0.15, 'class': cls,
                                                        'basis': 'Original authority probe'}}}
    if cite is not None:
        document['board_setup_rules']['min_clearance']['citation'] = cite
    project, record = import_constraints({}, json_bytes(document), 'limits.json', 'probe')
    setting, = record['settings']
    return setting, geometry_defaults(project, record)


def locality(decision):
    pair = {'max_distance_nm': 150000, 'provenance': {'class': 'owner', 'basis': 'Original authority probe',
                                                       'decision_reference': decision}}
    return locality_provenance.classify(pair, RECORDS)


class CitationModelTest(unittest.TestCase):
    def test_free_text_labels_are_advisory_and_disclosed_in_both_consumers(self):
        for cls in ('owner', 'manufacturer', 'datasheet', 'engineering'):
            with self.subTest(cls=cls):
                setting, rows = constraint(cls)
                self.assertEqual((setting['class'], setting['enforcement'], setting['citation']),
                                 (cls, 'advisory', None))
                self.assertEqual(setting['unsourced_reason'], f'free-text {cls} label without a resolvable citation')
                row, = rows
                self.assertEqual((row['class'], row['citation'], row['unsourced_reason']),
                                 (cls, None, setting['unsourced_reason']))
        self.assertEqual(locality({k: v for k, v in owner().items() if k not in ('commit', 'repository')}),
                         {'class': 'owner', 'enforcement': 'advisory',
                          'reason': 'owner decision is not cited at a commit (uncommitted)'})

    def test_cited_forms_are_hard_and_leave_the_geometry_inventory(self):
        for cls in ('owner', 'manufacturer', 'datasheet'):
            with self.subTest(cls=cls):
                cite = citation(cls, TEXT, PATH)
                setting, rows = constraint(cls, cite)
                self.assertEqual((setting['enforcement'], setting['citation']['source']), ('hard', cite))
                self.assertEqual(rows, [])
        # An engineering margin may cite any captured source and stays disclosed as a margin.
        setting, rows = constraint('engineering', citation('datasheet', TEXT, PATH))
        self.assertEqual(setting['enforcement'], 'hard')
        self.assertEqual(len(rows), 1)
        self.assertEqual(locality(owner())['enforcement'], 'hard')

    def test_owner_commit_must_resolve_to_a_pinned_owner_commit(self):
        cases = {
            'uncommitted': (owner(commit=None), 'owner decision is not cited at a commit (uncommitted)'),
            'stale string': (owner(commit='stale'),
                             'owner decision commit is not a full commit id in a named repository'),
            'no repository': (owner(repository=None),
                              'owner decision commit is not a full commit id in a named repository'),
            'unpinned commit': (owner(commit='0' * 40),
                                'owner decision commit does not resolve to a pinned owner commit'),
            'other repository': (owner(repository='github.com/elsewhere/repo'),
                                 'owner decision commit does not resolve to a pinned owner commit'),
            'agent owner': (owner(owner='agent-348'),
                            'owner decision names no owner, or names an agent or the design-owner role'),
            'design owner': (owner(owner='Design Owner'),
                             'owner decision names no owner, or names an agent or the design-owner role'),
        }
        for name, (decision, reason) in cases.items():
            with self.subTest(case=name):
                self.assertEqual(source_authority.owner_decision(decision, RECORDS),
                                 {'enforcement': 'advisory', 'reason': reason})
                self.assertEqual(locality(decision), {'class': 'owner', 'enforcement': 'advisory', 'reason': reason})
                setting, rows = constraint('owner', decision)
                self.assertEqual((setting['enforcement'], setting['unsourced_reason']), ('advisory', reason))
                self.assertEqual(len(rows), 1)

    def test_stale_pin_and_integrity_failures(self):
        # The commit resolves, but the pinned bytes are another file's: a stale citation.
        changed = TEXT + ' (revised)'
        newer = committed(PATH, changed)
        decision = owner(**newer)
        self.assertEqual(source_authority.owner_decision(decision, RECORDS)['reason'],
                         'owner decision bytes differ from the pinned owner commit (stale)')
        # Present captured bytes that differ from the cited hash fail closed in both consumers.
        corrupt = {PATH: 'changed'}
        self.assertEqual(source_authority.owner_decision(owner(), corrupt)['enforcement'], 'integrity')
        with self.assertRaisesRegex(ValueError, 'constraint source record integrity'):
            document = {'schema': 'fome-mc.constraint-provenance/1', 'source_records': corrupt,
                        'board_setup_rules': {'min_clearance': {'value_mm': 0.15, 'class': 'owner',
                            'basis': 'Original authority probe', 'citation': owner()}}}
            import_constraints({}, json_bytes(document), 'limits.json', 'probe')
        with self.assertRaisesRegex(ValueError, 'source_records must map'):
            import_constraints({}, json_bytes({'schema': 'fome-mc.constraint-provenance/1',
                                               'source_records': {PATH: 1}, 'board_setup_rules': {}}),
                               'limits.json', 'probe')

    def test_value_kind_and_manufacturer_capture_boundaries(self):
        other = 'Original fixture decision: minimum clearance 0.2 mm'
        self.assertEqual(source_authority.cite('datasheet', citation('datasheet', other, 'other.txt'),
                                               {'other.txt': other}, [0.15])['reason'],
                         'cited quote does not state the declared value')
        record = locality_provenance.manufacturing_record()
        capture = {'kind': 'manufacturer_capture', 'capture': 'pcb', 'sha256': record['sources']['pcb']['sha256'],
                   'locator': record['locators']['pcb'], 'fact': 'different_net_clearance_nm'}
        for kind, cite in (('manufacturer_capture', capture),
                           ('manufacturer_source', citation('manufacturer', TEXT, PATH))):
            self.assertEqual(source_authority.cite('datasheet', cite, RECORDS, [0.15]),
                             {'enforcement': 'advisory',
                              'reason': f"datasheet cannot resolve through citation kind '{kind}'"})
        self.assertEqual(source_authority.cite('manufacturer', citation('datasheet', TEXT, PATH), RECORDS, [0.15]),
                         {'enforcement': 'advisory',
                          'reason': "manufacturer cannot resolve through citation kind 'captured_source'"})
        self.assertEqual(source_authority.cite('datasheet', citation('datasheet', TEXT, 'absent.txt'), RECORDS,
                                               [0.15]),
                         {'enforcement': 'advisory', 'reason': 'captured source record, locator or quote not verified'})
        self.assertEqual(source_authority.cite('default', citation('datasheet', TEXT, PATH), RECORDS, [0.15]),
                         {'enforcement': 'advisory', 'reason': 'default class is never hard'})
        # AGENTEE-348 H5: the builtin capture qualifies only its recorded 0.15 mm fact.
        self.assertEqual(source_authority.manufacturer_capture(capture, [0.15])['enforcement'], 'hard')
        for change, reason in (({'sha256': '0' * 64},
                                'manufacturer capture sha256 differs from the pinned capture (stale)'),
                               ({'capture': 'invented'}, 'no captured manufacturer record named invented'),
                               ({'locator': ''},
                                'manufacturer citation needs a capture name, its sha256 and a locator')):
            with self.subTest(change=change):
                self.assertEqual(source_authority.manufacturer_capture({**capture, **change}),
                                 {'enforcement': 'advisory', 'reason': reason})

    def test_verify_committed_resolves_commit_and_bytes(self):
        pin = committed(PATH, TEXT)
        sha = hashlib.sha256(TEXT.encode()).hexdigest()
        root = repository()
        self.assertEqual(source_authority.verify_committed(root, pin['commit'], PATH, sha), [])
        self.assertEqual(source_authority.verify_committed(root, '0' * 40, PATH, sha),
                         ['commit absent from the repository'])
        self.assertEqual(source_authority.verify_committed(root, pin['commit'], 'absent.txt', sha),
                         ['path absent at the commit'])
        self.assertEqual(source_authority.verify_committed(root, pin['commit'], PATH, '0' * 64),
                         ['committed bytes differ from the cited sha256'])
        self.assertEqual(source_authority.OWNER_DECISIONS[(REPOSITORY, pin['commit'], PATH)], sha)

    def test_production_owner_table_pins_no_fixture(self):
        # Fixture pins live only in this test process; the shipped table is reviewed source.
        import ast
        from pathlib import Path
        tree = ast.parse(Path(source_authority.__file__).read_text())
        table, = [node.value for node in tree.body if isinstance(node, ast.Assign)
                  and [t.id for t in node.targets] == ['OWNER_DECISIONS']]
        self.assertEqual(json.dumps(ast.literal_eval(table)), '{}')


if __name__ == '__main__':
    unittest.main()
