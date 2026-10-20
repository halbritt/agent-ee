"""AGENTEE-266: every envelope constraint is owner-stated, derived or assumed; assumed limits are soft.

The owned fixture fixtures/board-envelope/provenance/ states one constraint of each
class against a synthetic owner statement. An assumed or owner-weakened limit is
reported for owner confirmation and never fails placement as a design defect; a
record without provenance keeps every verdict and reports unknown provenance.
"""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent_ee.board_envelope import (derive, derive_requirements, obligation_coverage, require_obligations,
                                     source_inventory, validate)
from agent_ee.placement_adapter import combined_provenance, envelope_fixed, envelope_provenance
from agent_ee.placer import pack
from agent_ee.storage import digest
from test_placement import part, rectangle
from test_placer_capacity import columns_case
from test_placer_residuals import box
from test_placer_search import board

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'fixtures/board-envelope/provenance'
RC_ENVELOPE = ROOT / 'fixtures/board-envelope/rc.envelope.json'
STATEMENT = FIXTURE / 'owner-statement.md'


def fixture():
    return json.loads((FIXTURE / 'envelope.json').read_text())


def raw(value):
    return json.dumps(value).encode()


def captured():
    return source_inventory(FIXTURE)


def by_locator(record):
    return {e['locator']: e for e in record['constraint_provenance']['constraints']}


def stated(quote, locator='/outline'):
    value = fixture()
    value['constraint_provenance'] = {}
    value['constraint_provenance'][locator] = {
        'class': 'owner_stated', 'quote': quote,
        'source': {'path': 'owner-statement.md', 'sha256': hashlib.sha256(quote.encode()).hexdigest()}}
    return value


def with_statement(quote, locator='/outline', envelope=None):
    """(envelope bytes, captured inventory) with the quote as the whole owner statement."""
    root = Path(tempfile.mkdtemp(prefix='agent-ee-ee266-'))
    (root / 'owner-statement.md').write_text(quote)
    value = stated(quote, locator)
    if envelope is not None:
        envelope(value)
    return raw(value), source_inventory(root)


def enforcement(quote, locator='/outline', envelope=None):
    data, inventory = with_statement(quote, locator, envelope)
    return by_locator(validate(data, captured=inventory))[locator]['enforcement']


def square(value):
    """The fixture outline made a 20 mm square."""
    for edge in value['outline']['edges']:
        edge['start'][1], edge['end'][1] = (116 if c == 108 else c for c in (edge['start'][1], edge['end'][1]))


class ProvenanceRecordTests(unittest.TestCase):
    def test_owned_fixture_reports_all_three_classes_and_their_enforcement(self):
        record = validate((FIXTURE / 'envelope.json').read_bytes(), captured=captured())
        entries = by_locator(record)
        self.assertEqual({k: (v['class'], v['enforcement']) for k, v in entries.items()}, {
            '/outline': ('assumed', 'soft'), '/layer_count': ('owner_stated', 'hard'),
            '/stackup': ('derived', 'hard'), '/fab_profile': ('assumed', 'soft'),
            '/max_component_height_mm': ('owner_stated', 'hard'),
            '/fixed_footprints/0': ('owner_stated', 'soft')})
        self.assertEqual(record['constraint_provenance']['status'], 'declared')
        self.assertEqual(record['constraint_provenance']['owner_confirmation'],
                         ['/outline', '/fab_profile', '/fixed_footprints/0'])
        self.assertEqual(record['constraint_provenance']['source_paths'], ['owner-statement.md'])
        self.assertEqual(entries['/layer_count']['verification'], 'quote_in_source')
        self.assertEqual(entries['/layer_count']['source']['sha256'],
                         hashlib.sha256(STATEMENT.read_bytes()).hexdigest())
        self.assertEqual(entries['/stackup']['from'], ['/layer_count'])
        self.assertIn("'ideally'", entries['/fixed_footprints/0']['soft_reason'])
        # Provenance never changes the structural verdict.
        self.assertEqual((record['structural'], record['outcome']), ('pass', 'unknown'))

    def test_uncaptured_sources_stay_unverified_and_keep_enforcement(self):
        entries = by_locator(validate((FIXTURE / 'envelope.json').read_bytes()))
        self.assertEqual(entries['/layer_count']['verification'], 'not_captured')
        self.assertEqual(entries['/layer_count']['enforcement'], 'hard')

    def test_record_without_provenance_is_unknown_and_enforced_as_before(self):
        record = validate(RC_ENVELOPE.read_bytes())
        provenance = record['constraint_provenance']
        self.assertEqual(provenance['status'], 'unknown')
        self.assertEqual(provenance['owner_confirmation'], [])
        self.assertTrue(all((e['class'], e['enforcement']) == ('unknown', 'hard') for e in provenance['constraints']))
        requirements = derive_requirements(record, 'requirements/envelope.json')
        self.assertTrue(all(r['intent'] == 'acceptance' and not r['statement'].startswith(('ASSUMED', 'SOFT'))
                            for r in requirements['requirements']))
        self.assertTrue(all(p == {'class': 'unknown', 'enforcement': 'hard'}
                            for p in requirements['provenance'].values()))

    def test_softening_words_never_compile_into_a_hard_maximum(self):
        # The pod case: the number is written, but the same text says the envelope is not tight.
        envelope, inventory = with_statement('Board envelope 20 mm by 12 mm or smaller. The rear seat cowl is '
                                             'large, so the envelope is not tight.')
        outline = by_locator(validate(envelope, captured=inventory))['/outline']
        self.assertEqual((outline['class'], outline['enforcement']), ('owner_stated', 'soft'))
        self.assertIn('not tight', outline['soft_reason'])
        for quote in ('The board should be small, 20 mm wide or smaller.', 'Keep the board about 20 x 12 mm.',
                      "There's room in the cowl for a 20 x 12 mm board."):
            envelope, inventory = with_statement(quote)
            self.assertEqual(by_locator(validate(envelope, captured=inventory))['/outline']['enforcement'], 'soft',
                             quote)
        # "Or smaller" with both stated numbers is the owner's own maximum, and "no room" is not weakening.
        for quote in ('The board is 20 mm by 12 mm or smaller.',
                      'There is no room for more than 20 mm by 12 mm.'):
            envelope, inventory = with_statement(quote)
            self.assertEqual(by_locator(validate(envelope, captured=inventory))['/outline']['enforcement'], 'hard',
                             quote)

    def test_wider_weakening_wording_never_compiles_into_a_hard_maximum(self):
        # AGENTEE-271: the AGENTEE-266 review named target, preliminary and plenty of room.
        for quote in ('Target board size 20 x 12 mm.', 'Preliminary outline: 20 x 12 mm.',
                      'The cowl has plenty of room; the board is 20 x 12 mm.',
                      'The board is at least 20 x 12 mm.', 'Tentatively 20 x 12 mm.', 'The board is 20ish x 12 mm.',
                      'Board 20 x 12 mm for now.', 'Aim for 20 x 12 mm.', 'Board TBD, 20 x 12 mm placeholder.',
                      'Roughly 20 x 12 mm.', 'The board is \u2248 20 x 12 mm.'):
            self.assertEqual(enforcement(quote), 'soft', quote)
        data, inventory = with_statement('Target board size 20 x 12 mm.')
        self.assertIn("'Target'", by_locator(validate(data, captured=inventory))['/outline']['soft_reason'])
        # The owner's own fixed limits stay hard.
        for quote in ('The board is 20 x 12 mm.', 'The board is 20mm x 12mm.', 'The board is 12 mm by 20 mm.',
                      'The board must fit 20 x 12 mm.'):
            self.assertEqual(enforcement(quote), 'hard', quote)

    def test_stray_matching_numbers_do_not_state_a_limit(self):
        # Each quote holds the field's numbers, but not as that field's limit.
        for locator, quote in (('/outline', 'The board carries 20 parts and sits 12 mm from the wall.'),
                               ('/outline', 'The enclosure is 40 x 30 mm and the board is 20 x 12 mm.'),
                               ('/outline', 'The board is 20 mm wide and 12 mm long.'),
                               ('/layer_count', 'The board uses 4 copper layers and 2 connectors.'),
                               ('/layer_count', 'Use 2 or 4 layers.'),
                               ('/max_component_height_mm', 'J1 sits 12.5 mm from the corner.'),
                               ('/max_component_height_mm', 'Parts 12.5 mm tall, the lid 20 mm high.')):
            data, inventory = with_statement(quote, locator)
            entry = by_locator(validate(data, captured=inventory))[locator]
            self.assertEqual(entry['enforcement'], 'soft', quote)
            self.assertIn('does not fix the limit', entry['soft_reason'])
        for locator, quote in (('/layer_count', 'A 2-layer board.'), ('/layer_count', 'The board uses 2 copper layers.'),
                               ('/max_component_height_mm', 'Parts may be up to 12.5 mm tall.')):
            self.assertEqual(enforcement(quote, locator), 'hard', quote)

    def test_square_outline_needs_both_dimensions_stated(self):
        self.assertEqual(enforcement('The board is 20 mm wide.', envelope=square), 'soft')
        self.assertEqual(enforcement('The board is 20 mm by 12 mm.', envelope=square), 'soft')
        for quote in ('The board is 20 x 20 mm.', 'The board is a 20 mm square.'):
            self.assertEqual(enforcement(quote, envelope=square), 'hard', quote)

    def test_derived_limits_check_their_derivation_wording_and_numbers(self):
        statement = 'The cowl cavity is 24 x 16 mm with 2 mm clearance each side.'
        root = Path(tempfile.mkdtemp(prefix='agent-ee-ee271-'))
        (root / 'owner-statement.md').write_text(statement)
        fact = {'quote': statement, 'source': {'path': 'owner-statement.md',
                                               'sha256': hashlib.sha256(statement.encode()).hexdigest()}}

        def outline(derivation):
            value = fixture()
            value['constraint_provenance'] = {'/outline': {'class': 'derived', 'derivation': derivation,
                                                           'facts': [fact]}}
            return by_locator(validate(raw(value), captured=source_inventory(root)))['/outline']
        # A derivation naming its inputs and its result is hard.
        self.assertEqual(outline('24 x 16 mm cavity less 2 mm each side gives 20 x 12 mm.')['enforcement'], 'hard')
        weak = outline('24 x 16 mm cavity less clearance gives roughly 20 x 12 mm.')
        self.assertEqual(weak['enforcement'], 'soft')
        self.assertIn("'roughly'", weak['soft_reason'])
        bare = outline('The board fits the cavity with clearance.')
        self.assertEqual(bare['enforcement'], 'soft')
        self.assertIn('derivation does not fix the limit', bare['soft_reason'])
        self.assertEqual(outline('24 x 16 mm cavity less 2 mm each side gives 22 x 14 mm.')['enforcement'], 'soft')

    def test_owner_quote_outside_the_captured_inventory_is_refused(self):
        root = Path(tempfile.mkdtemp(prefix='agent-ee-ee271-'))
        (root / 'other.md').write_text('unrelated')
        with self.assertRaisesRegex(ValueError, 'owner-statement.md is not in the captured inventory'):
            validate((FIXTURE / 'envelope.json').read_bytes(), captured=source_inventory(root))
        value = fixture()
        value['constraint_provenance']['/stackup'] = {
            'class': 'derived', 'derivation': 'x', 'facts': [{'quote': 'x', 'source': {
                'path': 'absent.md', 'sha256': '0' * 64}}]}
        with self.assertRaisesRegex(ValueError, 'fact 0 source absent.md is not in the captured inventory'):
            validate(raw(value), captured=captured())

    def test_owner_quotes_must_match_their_captured_source(self):
        value = fixture()
        value['constraint_provenance']['/layer_count']['quote'] = 'The board uses 4 copper layers.'
        with self.assertRaisesRegex(ValueError, 'quote does not appear'):
            validate(raw(value), captured=captured())
        value = fixture()
        value['constraint_provenance']['/layer_count']['source']['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'not the cited sha256'):
            validate(raw(value), captured=captured())

    def test_malformed_provenance_is_refused(self):
        cases = {
            'names no constrained field': ('/keepouts/0', {'class': 'assumed', 'rationale': 'x'}),
            'class must be one of': ('/outline', {'class': 'guessed', 'rationale': 'x'}),
            'fields: missing': ('/outline', {'class': 'owner_stated', 'quote': 'x'}),
            'must cite owner-stated': ('/stackup', {'class': 'derived', 'derivation': 'x'}),
            'derives from assumed': ('/stackup', {'class': 'derived', 'derivation': 'x', 'from': ['/outline']}),
            'without provenance or twice': ('/stackup', {'class': 'derived', 'derivation': 'x',
                                                         'from': ['/mounting_holes/0']}),
            'nonempty text': ('/outline', {'class': 'assumed', 'rationale': ' '}),
        }
        for message, (locator, entry) in cases.items():
            value = fixture()
            value['constraint_provenance'][locator] = entry
            with self.assertRaisesRegex(ValueError, message):
                validate(raw(value))
        value = fixture()
        value['constraint_provenance']['/layer_count'] = {'class': 'derived', 'derivation': 'x', 'from': ['/stackup']}
        with self.assertRaisesRegex(ValueError, 'cycle'):
            validate(raw(value))
        value = fixture()
        value['constraint_provenance'] = {}
        with self.assertRaisesRegex(ValueError, 'nonempty object'):
            validate(raw(value))


class ProvenanceRequirementTests(unittest.TestCase):
    def setUp(self):
        artifacts = derive((FIXTURE / 'envelope.json').read_bytes(), source_path='requirements/envelope.json',
                           captured=captured())
        self.record = json.loads(artifacts['envelope-validation.json'])
        self.requirements = json.loads(artifacts['envelope-requirements.json'])
        self.markdown = artifacts['requirements.md'].decode()
        self.dialog = json.loads(artifacts['dialog-requirements.json'])

    def test_soft_limits_derive_informational_obligations_for_owner_confirmation(self):
        items = {r['source']['locator']: r for r in self.requirements['requirements']}
        # fab_profile is null, so it derives no obligation.
        self.assertEqual({k: r['intent'] for k, r in items.items()}, {
            '/outline': 'informational', '/layer_count': 'acceptance', '/stackup': 'acceptance',
            '/max_component_height_mm': 'acceptance', '/fixed_footprints/0': 'informational'})
        self.assertTrue(items['/outline']['statement'].startswith('ASSUMED, not a hard limit'))
        self.assertTrue(items['/fixed_footprints/0']['statement'].startswith('SOFT owner statement'))
        self.assertIn('Owner confirmation needed: /outline', self.markdown)
        self.assertIn('Provenance: assumed, soft limit', self.markdown)
        dialog = {r['id']: r['text'] for r in self.dialog['requirements']}
        self.assertTrue(dialog[items['/outline']['id']].startswith('ASSUMED'))

    def test_soft_obligations_need_no_check_and_are_reported_not_refused(self):
        hard = [r for r in self.requirements['requirements'] if r['intent'] == 'acceptance']
        soft = [r for r in self.requirements['requirements'] if r['intent'] == 'informational']
        scope = {'schema': 'agent-ee.scope/3', 'checks': [], 'baseline': {'requirements': []}}
        report = {r['id']: r for r in obligation_coverage(scope, self.requirements, self.record, {})}
        for item in soft:
            self.assertEqual(report[item['id']]['status'], 'soft')
            self.assertEqual(report[item['id']]['provenance']['enforcement'], 'soft')
        for item in hard:
            self.assertEqual(report[item['id']]['status'], 'unmapped')
        with self.assertRaises(ValueError) as raised:
            require_obligations(scope, self.requirements, self.record, {})
        self.assertTrue(all(item['id'] in str(raised.exception) for item in hard))
        self.assertTrue(all(item['id'] not in str(raised.exception) for item in soft))
        # A soft obligation mapped as acceptance is a changed intent, refused.
        outline = next(r for r in soft if r['source']['locator'] == '/outline')
        scope['baseline']['requirements'].append({'id': outline['id'], 'statement': outline['statement'],
                                                  'source': outline['source'], 'intent': 'acceptance',
                                                  'checks': []})
        with self.assertRaisesRegex(ValueError, 'informational intent differs'):
            obligation_coverage(scope, self.requirements, self.record, {})


ASSUMED = {'class': 'assumed', 'enforcement': 'soft', 'soft_reason': 'agent-proposed default awaiting owner confirmation'}
STATED = {'class': 'owner_stated', 'enforcement': 'hard'}


class ProvenancePlacementTests(unittest.TestCase):
    def test_assumed_outline_does_not_hard_fail_a_capacity_proof(self):
        source, policy = columns_case(7, capacity_proof=True)
        before = pack(source, policy)
        self.assertEqual((before['status'], before['outcome']), ('packing_infeasible', 'defect'))
        self.assertEqual(before['binding_constraints'][0]['class'], 'unknown')
        report = pack(source, {**policy, 'constraint_provenance': {'outline': ASSUMED}})
        self.assertEqual((report['status'], report['outcome']), ('packing_infeasible', 'unknown'))
        self.assertEqual(report['proof'], before['proof'])
        self.assertEqual(report['owner_confirmation'], ['outline'])
        self.assertEqual(report['binding_constraints'][0], {'constraint': 'outline', **ASSUMED})
        self.assertIn('binds soft limit(s) outline (assumed', report['reason'])
        # Owner-stated limits enforce as before.
        stated = pack(source, {**policy, 'constraint_provenance': {'outline': STATED}})
        self.assertEqual((stated['status'], stated['outcome']), ('packing_infeasible', 'defect'))

    def test_capacity_report_names_the_assumed_binding_constraint(self):
        source, policy = columns_case(7)
        policy['constraint_provenance'] = {'outline': ASSUMED, 'fixed': {'U1': STATED}}
        report = pack(source, policy)
        self.assertEqual(report['outcome'], 'unknown')
        binding = {b['constraint']: b for b in report['capacity']['sides']['F']['binding_constraints']}
        self.assertEqual(binding['outline'], {'constraint': 'outline', **ASSUMED})
        self.assertEqual(binding['fixed U1']['class'], 'owner_stated')
        self.assertEqual({binding['min_courtyard_edge_mm']['class'], binding['gap_nm']['class']}, {'unknown'})
        # Planned results carry the same diagnostic.
        source, policy = columns_case(6)
        plan = pack(source, {**policy, 'constraint_provenance': {'outline': ASSUMED}})
        self.assertEqual(plan['status'], 'planned')
        self.assertEqual(plan['capacity']['sides']['F']['binding_constraints'][0]['class'], 'assumed')

    def test_part_larger_than_an_assumed_outline_is_unknown_not_defect(self):
        source = board([part('U1', 1, 1, shape=box(1, 1, 3, 3)), part('C1', 4, 4, shape=box(4, 4, 30, 6))],
                       rectangle(0, 0, 20, 12))
        policy = columns_case(1)[1]
        hard = pack(source, policy)
        self.assertEqual((hard['status'], hard['outcome']), ('packing_infeasible', 'defect'))
        soft = pack(source, {**policy, 'constraint_provenance': {'outline': ASSUMED}})
        self.assertEqual((soft['status'], soft['outcome'], soft['proof']), ('packing_infeasible', 'unknown',
                                                                             hard['proof']))

    def test_malformed_policy_provenance_is_refused(self):
        source, policy = columns_case(6)
        for value in ({'outline': {'class': 'assumed', 'enforcement': 'hard'}},
                      {'outline': {'class': 'unknown', 'enforcement': 'soft', 'soft_reason': 'x'}},
                      {'outline': {'class': 'owner_stated', 'enforcement': 'soft'}},
                      {'board': ASSUMED}):
            with self.assertRaisesRegex(ValueError, 'constraint_provenance'):
                pack(source, {**policy, 'constraint_provenance': value})

    def test_adapter_reads_provenance_only_from_the_captured_envelope(self):
        record = validate((FIXTURE / 'envelope.json').read_bytes(), captured=captured())
        value = envelope_provenance(record)
        self.assertEqual(value['outline']['class'], 'assumed')
        self.assertEqual(value['fixed']['J1']['enforcement'], 'soft')
        self.assertIsNone(envelope_provenance(validate(RC_ENVELOPE.read_bytes())))
        one = {'provenance': value}
        self.assertEqual(combined_provenance([one]), value)
        # A second envelope without provenance leaves the board unknown, enforced as before.
        self.assertIsNone(combined_provenance([one, {}]))
        # Any hard statement of the same limit wins over an assumption.
        other = copy.deepcopy(value)
        other['outline'] = STATED
        self.assertEqual(combined_provenance([one, {'provenance': other}])['outline'], STATED)



class ProvenanceEnvelopeFixedTests(unittest.TestCase):
    """AGENTEE-271: the placement adapter validates envelope provenance against the capture."""

    def setUp(self):
        self.inputs = Path(tempfile.mkdtemp(prefix='agent-ee-ee271-'))
        for name in ('envelope.json', 'owner-statement.md'):
            (self.inputs / name).write_bytes((FIXTURE / name).read_bytes())
        self.manifest = {'scope': {'checks': [{'id': 'edge-j1', 'kind': 'placement', 'params': {
            'criterion': 'connector-edge/1', 'envelope': 'envelope.json', 'reference': 'J1', 'drc_check': 'drc'}}]},
            'files': {name: digest(self.inputs / name) for name in ('envelope.json', 'owner-statement.md')}}

    def test_captured_owner_quotes_are_verified_and_bound(self):
        found = envelope_fixed(self.manifest, self.inputs, [])
        self.assertEqual(found[0]['references'], ['J1'])
        self.assertEqual(found[0]['provenance']['outline']['class'], 'assumed')
        self.assertEqual(found[0]['provenance']['fixed']['J1']['enforcement'], 'soft')

    def test_uncaptured_changed_or_mutable_owner_source_is_refused(self):
        del self.manifest['files']['owner-statement.md']
        with self.assertRaisesRegex(ValueError, 'not in the captured inventory'):
            envelope_fixed(self.manifest, self.inputs, [])
        self.manifest['files']['owner-statement.md'] = digest(self.inputs / 'owner-statement.md')
        with self.assertRaisesRegex(ValueError, 'cites owner quotes in mutable inputs'):
            envelope_fixed(self.manifest, self.inputs, ['owner-statement.md'])
        # The statement changed after capture.
        (self.inputs / 'owner-statement.md').write_text('The board uses 4 copper layers.')
        with self.assertRaisesRegex(ValueError, 'hash changed: owner-statement.md'):
            envelope_fixed(self.manifest, self.inputs, [])
        # Captured, but it no longer says what the envelope quotes.
        self.manifest['files']['owner-statement.md'] = digest(self.inputs / 'owner-statement.md')
        with self.assertRaisesRegex(ValueError, 'not the cited sha256'):
            envelope_fixed(self.manifest, self.inputs, [])

if __name__ == '__main__':
    unittest.main()
