"""fab-profile/2 conjunctive surcharge rules over owned synthetic via limits.

Every value here is an owned test assumption, not any real fabricator's table.
"""
import copy
import json
import unittest

import tests.test_fab_profile as base
from agent_ee.fab_profile import classify_preferred, compile_profile, evaluate_conjunctive, offender_ids

QUOTE = 'Small via: drill under 0.3 mm and diameter under 0.5 mm; preferred'


def conjunctive_profile(inputs, specs, terms=(('min_via_drill_mm', 0.3), ('min_via_pad_mm', 0.5))):
    """Owned /2 profile: scalar specs plus one sourced drill-AND-diameter surcharge rule."""
    from agent_ee.storage import digest
    document = base.owned_profile(inputs, specs)
    source = inputs / 'owned-capabilities.txt'
    source.write_text(source.read_text() + '\n' + QUOTE + '\n')
    for limit in document['limits']:
        limit['source']['sha256'] = digest(source)
    document['schema'] = 'agent-ee.fab-profile/2'
    document['limits'].append({
        'id': 'small-via', 'requirement': 'fab', 'tier': 'preferred',
        'all': [{'key': key, 'value': value, 'unit': 'mm'} for key, value in terms],
        'source': {'path': source.name, 'sha256': digest(source),
                   'source': 'Owned synthetic assumptions (not a real fab)',
                   'as_of': '2026-10-17', 'page': 1, 'quote': QUOTE}})
    return document


MINIMA = [('min_via_drill_mm', 0.2, 'mm', 'minimum'), ('min_via_pad_mm', 0.4, 'mm', 'minimum')]


class ConjunctiveContractTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.inputs = Path(tmp.name)
        self.document = conjunctive_profile(self.inputs, MINIMA)

    def compile(self, document):
        from agent_ee.storage import digest
        files = {p.name: digest(p) for p in self.inputs.iterdir()}
        return compile_profile(document, files, self.inputs)

    def rule(self, compiled):
        return next(c for c in compiled['constraints'] if c['id'] == 'small-via')

    def test_rule_compiles_one_isolated_native_run_per_term(self):
        rule = self.rule(self.compile(self.document))
        self.assertEqual(rule['execution'], 'ready', rule)
        self.assertNotIn('rules', rule)
        self.assertEqual([t['expected']['key'] for t in rule['terms']], ['min_via_drill_mm', 'min_via_pad_mm'])
        drill, pad = rule['terms']
        self.assertEqual((drill['id'], drill['marker']),
                         ('small-via+min_via_drill_mm', 'agent_ee_small-via+min_via_drill_mm'))
        self.assertIn('(condition "A.Type == \'Via\'")', drill['rules'])
        self.assertIn('(constraint hole_size (min 0.3mm))', drill['rules'])
        self.assertEqual((drill['native_types'], drill['project_rules']),
                         (['drill_out_of_range'], {'min_through_hole_diameter': 0.3}))
        self.assertIn('(constraint via_diameter (min 0.5mm))', pad['rules'])
        self.assertEqual(rule['expected']['source']['quote'], QUOTE)

    def test_v1_profiles_compile_unchanged_and_refuse_conjunctive_rules(self):
        v1 = copy.deepcopy(self.document)
        v1['schema'] = 'agent-ee.fab-profile/1'
        scalar = v1['limits'][:-1]
        self.assertEqual(self.compile({**v1, 'limits': scalar})['constraints'],
                         self.compile({**self.document, 'limits': scalar})['constraints'])
        with self.assertRaisesRegex(ValueError, 'unknown fabricator limit fields'):
            self.compile(v1)
        with self.assertRaisesRegex(ValueError, 'no defaults'):
            self.compile({**self.document, 'schema': 'agent-ee.fab-profile/3'})

    def test_invalid_rule_shapes_invalidate_the_profile(self):
        def changed(mutate):
            document = copy.deepcopy(self.document)
            mutate(document['limits'][-1])
            return document
        cases = (
            (lambda r: r.update(tier='minimum'), 'tier must be preferred'),
            (lambda r: r['all'].pop(), 'at least two terms'),
            (lambda r: r.update(all={'min_via_drill_mm': 0.3}), 'at least two terms'),
            (lambda r: r['all'][1].update(key='min_via_drill_mm'), 'duplicate conjunctive term'),
            (lambda r: r['all'][1].update(key='min_trace_mm'), 'support only'),
            (lambda r: r['all'][0].update(unit='mil'), 'unit mismatch'),
            (lambda r: r['all'][0].update(value=0), 'must be positive'),
            (lambda r: r['all'][0].update(value=10**400), 'finite'),
            (lambda r: r['all'][0].update(value=True), 'finite'),
            (lambda r: r['all'][0].update(tier='preferred'), 'exactly key/value/unit'),
            (lambda r: r.update(key='min_via_drill_mm'), 'unknown conjunctive rule fields'),
            (lambda r: r.update(id='Bad Id'), 'safe id'),
        )
        for mutate, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self.compile(changed(mutate))

    def test_unsourced_or_misquoted_rule_is_unsupported_not_defaulted(self):
        for field, value in (('source', None), ('quote', 'Small via: anything goes'), ('as_of', '2026-01-01')):
            document = copy.deepcopy(self.document)
            if field == 'source':
                del document['limits'][-1]['source']
            else:
                document['limits'][-1]['source'][field] = value
            with self.subTest(field=field):
                rule = self.rule(self.compile(document))
                self.assertEqual(rule['execution'], 'unsupported', rule)
                self.assertNotIn('terms', rule)

    def test_board_envelope_reads_v2_minima_and_ignores_the_surcharge_rule(self):
        from agent_ee.board_envelope import profile_support
        from agent_ee.storage import digest
        document = conjunctive_profile(self.inputs, [('max_layers', 2, 'count', 'minimum')])
        files = {p.name: digest(p) for p in self.inputs.iterdir()}
        inventory = {'copper_layers': 2, 'copper_layer_names': ['F.Cu', 'B.Cu'],
                     'outline_xy_mm': [10, 10], 'outline_ids': ['e']}
        compiled, support, unresolved = profile_support(document, (files, self.inputs), inventory)
        self.assertEqual([s['predicate'] for s in support], ['fab_profile_supports_layer_count'])
        self.assertEqual(unresolved, [])
        self.assertEqual(self.rule(compiled)['execution'], 'ready')

    def test_descriptor_freezes_rule_source_and_never_makes_a_surcharge_predicate(self):
        from agent_ee.capabilities import describe_check
        from agent_ee.storage import digest
        (self.inputs / 'board.kicad_pcb').write_text('Captured board bytes; pure validation never runs tools.')
        (self.inputs / 'requirements.txt').write_text('Declared fabrication bound.')
        (self.inputs / 'profile.json').write_text(json.dumps(self.document))
        files = {p.name: digest(p) for p in self.inputs.iterdir()}
        scope = {'board': 'board.kicad_pcb', 'baseline': {'requirements': [
            {'id': 'fab', 'checks': ['fab'], 'source': {'path': 'requirements.txt', 'locator': 'fab'}}]}}
        spec = {'id': 'fab', 'kind': 'fab_profile', 'params': {'fab_profile': 'profile.json'}}
        descriptor = describe_check(spec, check_specs=[spec], scope=scope, files=files, inputs=self.inputs)
        self.assertTrue(descriptor['can_evaluate_bound'], descriptor)
        self.assertEqual(descriptor['predicates'], ['fab:limit-0', 'fab:limit-1'])
        self.assertEqual(sorted(descriptor['immutable_inputs']), ['owned-capabilities.txt', 'profile.json'])


class ConjunctiveClassificationTest(unittest.TestCase):
    @staticmethod
    def term(key, status, *uuids, execution='ok'):
        return {'id': 'small-via+' + key, 'requirement': 'fab', 'expected': {'key': key},
                'execution': execution, 'status': status,
                'native_findings': [{'items': [{'uuid': u}]} for u in uuids]}

    def rule(self, *terms):
        record = {'id': 'small-via', 'requirement': 'fab', 'status': 'not_evaluated', 'execution': 'unsupported',
                  'expected': {'tier': 'preferred', 'all': [{'key': 'min_via_drill_mm'}, {'key': 'min_via_pad_mm'}]}}
        return evaluate_conjunctive(record, list(terms))

    def test_joint_offenders_are_the_intersection_of_term_findings(self):
        rule = self.rule(self.term('min_via_drill_mm', 'violation', 'joint', 'drill-only', 'illegal'),
                         self.term('min_via_pad_mm', 'violation', 'joint', 'pad-only', 'illegal'))
        self.assertEqual((rule['execution'], rule['status'], rule['offender_ids']),
                         ('ok', 'violation', ['illegal', 'joint']))
        disjoint = self.rule(self.term('min_via_drill_mm', 'violation', 'drill-only'),
                             self.term('min_via_pad_mm', 'violation', 'pad-only'))
        self.assertEqual((disjoint['status'], disjoint['offender_ids']), ('satisfied', []))

    def test_unproved_term_leaves_the_rule_unknown(self):
        failed = self.rule(self.term('min_via_drill_mm', 'violation', 'joint'),
                           {**self.term('min_via_pad_mm', 'not_evaluated', execution='failed'),
                            'reason': 'native DRC failed'})
        self.assertEqual((failed['execution'], failed['status']), ('failed', 'not_evaluated'))
        self.assertIn('min_via_pad_mm not evaluated: native DRC failed', failed['reason'])
        unsupported = self.rule(self.term('min_via_drill_mm', 'violation', 'joint'),
                                self.term('min_via_pad_mm', 'not_evaluated'))
        self.assertEqual((unsupported['execution'], unsupported['status']), ('unsupported', 'not_evaluated'))

    def minimum(self, key, status, *uuids):
        return {'id': key, 'requirement': 'fab', 'expected': {'key': key, 'tier': 'minimum'},
                'execution': 'ok', 'status': status,
                'native_findings': [{'items': [{'uuid': u}]} for u in uuids]}

    def test_extra_charge_excludes_vias_illegal_under_any_term_minimum(self):
        terms = (self.term('min_via_drill_mm', 'violation', 'joint', 'thin-drill', 'thin-pad'),
                 self.term('min_via_pad_mm', 'violation', 'joint', 'thin-drill', 'thin-pad'))
        records = [self.minimum('min_via_drill_mm', 'violation', 'thin-drill'),
                   self.minimum('min_via_pad_mm', 'violation', 'thin-pad'), self.rule(*terms)]
        rule = classify_preferred(records, {'outline_xy_mm': [10, 10]})[-1]
        self.assertEqual((rule['status'], rule['offender_ids'], rule['minimum_violation_ids']),
                         ('extra_charge', ['joint'], ['thin-drill', 'thin-pad']))
        records = [self.minimum('min_via_drill_mm', 'violation', 'joint'),
                   self.minimum('min_via_pad_mm', 'satisfied'),
                   self.rule(self.term('min_via_drill_mm', 'violation', 'joint'),
                             self.term('min_via_pad_mm', 'violation', 'joint'))]
        rule = classify_preferred(records, {'outline_xy_mm': None})[-1]
        self.assertEqual((rule['status'], rule['offender_ids'], rule['minimum_violation_ids']),
                         ('satisfied', [], ['joint']))

    def test_every_term_needs_an_evaluated_minimum(self):
        terms = (self.term('min_via_drill_mm', 'violation', 'joint'), self.term('min_via_pad_mm', 'violation', 'joint'))
        for minima in ([self.minimum('min_via_drill_mm', 'satisfied')],
                       [self.minimum('min_via_drill_mm', 'satisfied'),
                        {**self.minimum('min_via_pad_mm', 'not_evaluated'), 'execution': 'unsupported'}]):
            with self.subTest(minima=len(minima)):
                rule = classify_preferred([*minima, self.rule(*terms)], {'outline_xy_mm': [10, 10]})[-1]
                self.assertEqual((rule['execution'], rule['status']), ('unsupported', 'not_evaluated'))


VIAS = {  # name: (drill mm, diameter mm); drill bound 0.3, diameter bound 0.5
    'joint': (0.25, 0.45), 'drill_only': (0.25, 0.6), 'pad_only': (0.35, 0.45),
    'drill_at_bound': (0.3, 0.45), 'pad_at_bound': (0.25, 0.5), 'control': (0.4, 0.7)}


def via_board(assignments, saved):
    """Owned board whose six saved vias take the named (drill, diameter) pairs.

    `saved` receives name -> saved via UUID, read back from the saved board.
    """
    def build(path, **options):
        import pcbnew as k
        from tests.routing_fixture import make_board
        make_board(path, vias=3, **options)
        board = k.LoadBoard(str(path))
        vias = sorted((t for t in board.GetTracks() if t.GetClass() == 'PCB_VIA'),
                      key=lambda v: (v.GetPosition().y, v.GetPosition().x))
        assert len(vias) == len(assignments), len(vias)
        positions = {}
        for via, (name, (drill, diameter)) in zip(vias, assignments):
            via.SetDrill(round(drill*1e6)); via.SetWidth(round(diameter*1e6))
            positions[(int(via.GetPosition().x), int(via.GetPosition().y))] = name
        k.SaveBoard(str(path), board)
        sizes = dict(assignments)
        for via in k.LoadBoard(str(path)).GetTracks():
            if via.GetClass() == 'PCB_VIA':
                name = positions[(int(via.GetPosition().x), int(via.GetPosition().y))]
                drill, diameter = sizes[name]
                assert (via.GetDrillValue(), via.GetWidth(k.F_Cu)) == (round(drill*1e6), round(diameter*1e6))
                saved[name] = via.m_Uuid.AsString()
    return build


class NativeConjunctiveTest(unittest.TestCase):
    """Real sandboxed KiCad DRC per term; joint offenders are saved via UUIDs."""
    @classmethod
    def setUpClass(cls):
        from tests.toolchain import require_toolchain
        require_toolchain(native=True)

    def run_vias(self, assignments, specs=MINIMA, **profile_options):
        saved = {}
        result = base.NativeFabTest.run_profile(
            self, None, builder=via_board(assignments, saved),
            profile=lambda inputs: conjunctive_profile(inputs, specs, **profile_options))
        rule = next(c for c in result['details']['constraints'] if c['id'] == 'small-via')
        return result, rule, saved

    def test_only_vias_under_both_bounds_are_extra_charges(self):
        assignments = list(VIAS.items())
        result, rule, saved = self.run_vias(assignments)
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        self.assertEqual((rule['status'], rule['execution']), ('extra_charge', 'ok'), rule)
        self.assertEqual(rule['offender_ids'], [saved['joint']], rule)
        self.assertEqual(rule['minimum_violation_ids'], [], rule)
        self.assertEqual(result['details']['extra_charges'], [rule])
        # Each term's native evidence cites exactly the vias strictly under its own bound.
        for term, kind, names in zip(rule['terms'], ('drill_out_of_range', 'via_diameter'),
                                     (('joint', 'drill_only', 'pad_at_bound'), ('joint', 'pad_only', 'drill_at_bound'))):
            self.assertEqual((term['execution'], term['status']), ('ok', 'violation'), term)
            self.assertEqual(sorted(offender_ids(term)), sorted(saved[n] for n in names), term)
            self.assertEqual({f['type'] for f in term['native_findings']}, {kind}, term)
            self.assertTrue(term['native_report'].startswith('native-rules/small-via/'), term)

    def test_joint_via_illegal_under_a_minimum_is_a_defect_not_a_surcharge(self):
        assignments = [*VIAS.items()][:5] + [('thin_drill', (0.15, 0.45))]
        result, rule, saved = self.run_vias(assignments)
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)
        self.assertEqual(result['details']['violations'], ['limit-0'], result)
        self.assertEqual((rule['status'], rule['offender_ids'], rule['minimum_violation_ids']),
                         ('extra_charge', [saved['joint']], [saved['thin_drill']]), rule)

    def test_no_joint_via_means_no_surcharge(self):
        assignments = [(n, VIAS[n]) for n in ('drill_only', 'pad_only', 'drill_at_bound', 'pad_at_bound',
                                              'control')] + [('control2', (0.4, 0.7))]
        result, rule, _ = self.run_vias(assignments)
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        self.assertEqual((rule['status'], rule['offender_ids']), ('satisfied', []), rule)
        self.assertEqual(result['details']['extra_charges'], [])

    def test_rule_without_term_minima_is_unknown(self):
        result, rule, _ = self.run_vias(list(VIAS.items()), specs=MINIMA[:1])
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)
        self.assertEqual((rule['status'], rule['execution']), ('not_evaluated', 'unsupported'), rule)


if __name__ == '__main__':
    unittest.main()
