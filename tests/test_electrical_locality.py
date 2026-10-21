"""Owned pad identities, coupled limits, pack guidance and captured native checks."""
import copy
import hashlib
import json
import random
import unittest

from agent_ee import electrical_locality as locality
from agent_ee.placer import pack, transform
from agent_ee.placement_check import parameters, predicate_names
from test_placement import capture, part, MM
from test_placer import request



FIXTURE_DECISION = 'Original fixture author: test parameters define EE322 numerical requirements.'
FIXTURE_SOURCES = {'fixture-decisions/EE322.txt': FIXTURE_DECISION}


def fixture_provenance():
    return {'class': 'owner', 'basis': 'Explicit original fixture requirement',
            'decision_reference': {'path': 'fixture-decisions/EE322.txt',
                'sha256': hashlib.sha256(FIXTURE_DECISION.encode()).hexdigest(),
                'locator': 'EE322 test parameter contract', 'quote': FIXTURE_DECISION,
                'owner': 'Original fixture author'}}


def endpoint(ref, pad, net):
    return dict(reference=ref, pad=pad, net=net)


def declaration(ic='U1', cap='C1', supply='VCC', ground='GND', limit=5*MM):
    def pair(a, b, net):
        return {'from': endpoint(ic, a, net), 'to': endpoint(cap, b, net),
                'provenance': fixture_provenance(), 'max_distance_nm': limit, 'citation': 'Owned fixture requirement EE322: declared test limit.'}
    return dict(schema=locality.SCHEMA, source_records=FIXTURE_SOURCES, obligations=[dict(id=cap, metric='pad-centre-to-centre',
                supply=pair('1', '1', supply), **{'return': pair('2', '2', ground)})])


def board():
    b = capture([part('U1', 6, 6), part('C1', 15, 15)])
    b['pcbnew_version'] = '10.0.7'
    for f in b['footprints']:
        x, y = f['position_nm']
        f['pads'] = [dict(uuid=f['ref']+str(i), number=str(i), net=net,
                          position_nm=[x+MM, y+(i-1)*MM], size_nm=[100000,100000],
                          copper_centroid_offset_nm=[[0,1],[0,1]], shape=1, attribute='smd', layers=['F.Cu'], rotation_deg=0)
                     for i, net in ((1, 'VCC'), (2, 'GND'))]
    return b


def output_loop():
    facts, doc = board(), declaration(limit=20*MM)
    ind = copy.deepcopy(facts['footprints'][0])
    ind['ref'] = ind['uuid'] = 'L1'
    for pad in ind['pads']:
        pad['uuid'] = 'L1'+pad['number']
    ind = transform(ind, [10*MM, 6*MM], 0)
    ind['pads'][0]['net'] = 'SW'
    ind['pads'][1]['net'] = 'VCC'
    facts['footprints'][0]['pads'][0]['net'] = 'SW'
    facts['footprints'].append(ind)
    row = doc['obligations'][0]
    row['supply']['from'] = endpoint('L1', '2', 'VCC')
    row['switch'] = dict(**{'from': endpoint('U1', '1', 'SW'),
                           'to': endpoint('L1', '1', 'SW')},
                         provenance=fixture_provenance(), max_distance_nm=20*MM, citation='Owned output-loop switch limit.')
    return facts, doc


def bound_output_loop():
    facts, doc = output_loop()
    doc['obligations'][0].update(kind='buck-output-loop', ic='U1', inductor='L1', return_net='GND')
    return facts, doc


class LocalityInvariantTests(unittest.TestCase):
    def test_untyped_distinct_sources_require_disjoint_net_sets(self):
        for supply_end in ('from', 'to'):
            for return_end in ('from', 'to'):
                facts, doc = output_loop()
                row = doc['obligations'][0]
                for role, prefix in (('supply', 'OUT'), ('return', 'GND')):
                    for end in ('from', 'to'):
                        row[role][end]['net'] = prefix + '_' + end
                row['supply'][supply_end]['net'] = row['return'][return_end]['net']
                with self.subTest(supply=supply_end, ground=return_end):
                    for evaluate in (lambda: locality.validate(doc),
                                     lambda: locality.grade(facts, doc),
                                     lambda: pack(facts, request(electrical_locality=doc))):
                        with self.assertRaisesRegex(ValueError, 'distinct-source supply and return nets must differ'):
                            evaluate()

    def test_bound_switch_net_is_disjoint_from_supply_and_return(self):
        for end in ('from', 'to', 'both'):
            for net in ('VCC', 'GND'):
                facts, doc = bound_output_loop()
                row = doc['obligations'][0]
                for key in (('from', 'to') if end == 'both' else (end,)):
                    row['switch'][key]['net'] = net
                with self.subTest(end=end, net=net):
                    for evaluate in (lambda: locality.validate(doc),
                                     lambda: locality.grade(facts, doc),
                                     lambda: pack(facts, request(electrical_locality=doc))):
                        with self.assertRaisesRegex(ValueError, 'loop switch nets must differ from supply and return'):
                            evaluate()

    def test_same_source_and_untyped_switch_keep_generic_net_semantics(self):
        facts, doc = board(), declaration(supply='GND', ground='GND')
        self.assertIs(locality.validate(doc), doc)
        self.assertEqual(locality.grade(facts, doc)['status'], 'defect')
        facts, doc = output_loop()
        for end in ('from', 'to'):
            doc['obligations'][0]['switch'][end]['net'] = 'VCC'
        self.assertIs(locality.validate(doc), doc)
        self.assertEqual(locality.grade(facts, doc)['status'], 'unknown')

    def test_bound_switch_on_supply_net_cannot_pass_with_matching_pads(self):
        facts, doc = bound_output_loop()
        extra = copy.deepcopy(facts['footprints'][0]['pads'][1])
        extra.update(number='3', uuid='U13', net='VCC')
        facts['footprints'][0]['pads'].append(extra)
        row = doc['obligations'][0]
        row['switch']['from'] = endpoint('U1', '3', 'VCC')
        row['switch']['to'] = endpoint('L1', '2', 'VCC')
        with self.assertRaisesRegex(ValueError, 'loop switch nets must differ from supply and return'):
            locality.grade(facts, doc)


class BoundOutputLoopTests(unittest.TestCase):
    def test_owned_evidence_and_optional_switch(self):
        facts, doc = bound_output_loop()
        original = copy.deepcopy(doc)
        result = locality.grade(facts, doc)
        self.assertEqual(result['status'], 'pass')
        self.assertEqual([r['distance_squared_nm'] for r in result['measured']],
                         [89*MM**2, 162*MM**2, 16*MM**2])
        self.assertEqual([r['citation'] for r in result['measured']],
                         [doc['obligations'][0][r]['citation'] for r in ('supply', 'return', 'switch')])
        self.assertEqual(result['declaration'], original)
        self.assertEqual(doc, original)
        doc['obligations'][0].pop('switch')
        self.assertEqual(locality.grade(facts, doc)['status'], 'pass')

    def test_bound_loop_guidance_and_defect_precedence(self):
        import itertools
        for priority in itertools.permutations(['U1', 'L1', 'C1']):
            facts, doc = bound_output_loop()
            for role in locality.roles(doc['obligations'][0]):
                doc['obligations'][0][role]['max_distance_nm'] = 4*MM
            plan = pack(facts, request(priority_refs=list(priority), electrical_locality=doc))
            self.assertEqual(plan['status'], 'planned', plan)
            self.assertEqual(plan['locality_grading']['status'], 'pass')
        facts, doc = bound_output_loop()
        doc['obligations'][0]['supply'].pop('citation')
        self.assertEqual(locality.grade(facts, doc)['status'], 'unknown')
        doc['obligations'][0]['return']['max_distance_nm'] = 0
        result = locality.grade(facts, doc)
        self.assertEqual(result['status'], 'defect')
        self.assertEqual([d['role'] for d in result['defects']], ['return'])

    def test_role_binding_refuses_unrelated_rail_member_probe_c(self):
        facts, doc = bound_output_loop()
        unrelated = copy.deepcopy(facts['footprints'][-1])
        unrelated['ref'] = unrelated['uuid'] = 'R9'
        for pad in unrelated['pads']:
            pad['uuid'] = 'R9'+pad['number']
        facts['footprints'].append(unrelated)
        row = doc['obligations'][0]
        row['supply']['from']['reference'] = 'R9'
        # R9 really has a nearby VCC pad: geometry and shared net alone pass.
        legacy = copy.deepcopy(doc)
        for key in ('kind', 'ic', 'inductor', 'return_net'):
            legacy['obligations'][0].pop(key)
        self.assertEqual(locality.grade(facts, legacy)['status'], 'pass')
        with self.assertRaisesRegex(ValueError, 'supply must start at the declared inductor'):
            locality.grade(facts, doc)
        with self.assertRaises(ValueError):
            pack(facts, request(electrical_locality=doc))

    def test_switch_rule_itself_with_three_distinct_references(self):
        for role, expected in (('from', 'declared IC'), ('to', 'declared inductor')):
            facts, doc = bound_output_loop()
            pair = doc['obligations'][0]['switch']
            pair[role]['reference'] = 'R9'
            self.assertNotEqual(pair['from']['reference'], pair['to']['reference'])
            with self.subTest(role=role), self.assertRaisesRegex(ValueError, expected):
                locality.validate(doc)

    def test_loop_schema_and_net_boundaries(self):
        for mutation in ('ic', 'inductor', 'return_net', 'kind', 'same_net', 'swapped',
                         'return_to', 'return_ic', 'same_sources'):
            facts, doc = bound_output_loop()
            row = doc['obligations'][0]
            if mutation in ('ic', 'inductor', 'return_net'):
                row.pop(mutation)
            elif mutation == 'kind': row['kind'] = 'unknown-loop'
            elif mutation == 'same_net':
                for end in ('from', 'to'): row['supply'][end]['net'] = 'GND'
            elif mutation == 'swapped':
                for end in ('from', 'to'):
                    row['supply'][end]['net'], row['return'][end]['net'] = 'GND', 'VCC'
            elif mutation == 'return_to': row['return']['to']['net'] = 'VCC'
            elif mutation == 'return_ic': row['return']['from']['reference'] = 'R9'
            else: row['inductor'] = 'U1'
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                locality.validate(doc)
        for key in ('ic', 'inductor', 'return_net'):
            for value in ('', ' ', None, 1, []):
                facts, doc = bound_output_loop()
                doc['obligations'][0][key] = value
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    locality.validate(doc)
        # Role metadata cannot silently be ignored on an untyped declaration.
        facts, doc = bound_output_loop()
        doc['obligations'][0].pop('kind')
        with self.assertRaises(ValueError): locality.validate(doc)


class OutputLoopTests(unittest.TestCase):
    def test_distinct_sources_with_equal_pad_numbers_and_coupled_constraints(self):
        facts, doc = output_loop()
        self.assertEqual(locality.grade(facts, doc)['status'], 'pass')
        for role in ('supply', 'return', 'switch'):
            with self.subTest(role=role):
                changed = copy.deepcopy(facts)
                pair = doc['obligations'][0][role]
                ref = pair['from']['reference']
                fp = next(f for f in changed['footprints'] if f['ref'] == ref)
                pad = next(p for p in fp['pads'] if p['number'] == pair['from']['pad'])
                pad['position_nm'][0] += 100*MM
                result = locality.grade(changed, doc)
                self.assertEqual([d['role'] for d in result['defects']], [role])
                # Every endpoint order blocks the same proven violation.
                for proposed in changed['footprints']:
                    if proposed['ref'] in {pair[k]['reference'] for k in ('from', 'to')}:
                        peers = [f for f in changed['footprints'] if f is not proposed]
                        blockers, cost = locality.constraint(peers, proposed, doc)
                        self.assertEqual(blockers, {pair[k]['reference'] for k in ('from', 'to')})
                        self.assertGreater(cost, 1)

    def test_packer_guides_all_three_footprints_in_each_order(self):
        import itertools
        facts, doc = output_loop()
        for role in locality.roles(doc['obligations'][0]):
            doc['obligations'][0][role]['max_distance_nm'] = 4*MM
        self.assertEqual(locality.grade(facts, doc)['status'], 'defect')
        for priority in itertools.permutations(['U1', 'L1', 'C1']):
            with self.subTest(priority=priority):
                policy = request(priority_refs=list(priority), electrical_locality=doc)
                plan = pack(facts, policy)
                self.assertEqual(plan['status'], 'planned', plan)
                self.assertEqual(plan['locality_grading']['status'], 'pass')
                self.assertEqual({m['role'] for m in plan['locality_grading']['measured']},
                                 {'supply', 'return', 'switch'})
                self.assertEqual(plan, pack(facts, policy))

    def test_uuid_selection_and_unknown_boundaries_preserve_known_defects(self):
        for mutation in ('duplicate', 'uuid', 'net', 'declared_net', 'limit', 'citation'):
            with self.subTest(mutation=mutation):
                facts, doc = output_loop()
                row = doc['obligations'][0]
                ground = facts['footprints'][0]['pads'][1]
                ground['uuid'] = '00000000-0000-4000-8000-000000000010'
                other = copy.deepcopy(ground)
                other['uuid'] = '00000000-0000-4000-8000-000000000011'
                facts['footprints'][0]['pads'].append(other)
                row['return']['from']['uuid'] = ground['uuid']
                self.assertEqual(locality.grade(facts, doc)['status'], 'pass')
                if mutation == 'duplicate': row['return']['from'].pop('uuid')
                elif mutation == 'uuid': row['return']['from']['uuid'] = other['uuid'][:-1]+'2'
                elif mutation == 'net': ground['net'] = 'OTHER'
                elif mutation == 'declared_net': row['return']['to']['net'] = 'OTHER'
                else: row['return'].pop('max_distance_nm' if mutation == 'limit' else 'citation')
                self.assertEqual(locality.grade(facts, doc)['status'], 'unknown')
                plan = pack(facts, request(electrical_locality=doc))
                self.assertEqual((plan['outcome'], plan['placements']), ('unknown', []))
                row['supply']['max_distance_nm'] = 0
                result = locality.grade(facts, doc)
                self.assertEqual(result['status'], 'defect')
                self.assertEqual([d['role'] for d in result['defects']], ['supply'])

    def test_capacitor_and_switch_identity_boundaries(self):
        for mutation in ('capacitor', 'cap_pad', 'switch_ic'):
            facts, doc = output_loop()
            row = doc['obligations'][0]
            if mutation == 'capacitor': row['return']['to']['reference'] = 'C2'
            elif mutation == 'cap_pad': row['return']['to']['pad'] = '1'
            else: row['switch']['from']['reference'] = 'L1'
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                locality.validate(doc)


class LocalityTests(unittest.TestCase):
    def test_grade_exact_boundary_and_one_nanometre(self):
        b, doc = board(), declaration()
        b['footprints'][1] = transform(b['footprints'][1], [11*MM, 6*MM], 0)
        self.assertEqual(locality.grade(b, doc)['status'], 'pass')
        b['footprints'][1]['pads'][1]['position_nm'][0] += 1
        result = locality.grade(b, doc)
        self.assertEqual(result['status'], 'defect')
        self.assertEqual(result['defects'][0]['role'], 'return')
        self.assertGreater(result['defects'][0]['distance_squared_nm'], (5*MM)**2)

    def test_unknown_boundaries_and_defect_precedence(self):
        for mutation in ('missing_ref', 'duplicate_ref', 'missing_pad', 'duplicate_pad', 'net',
                         'limit', 'citation', 'metric', 'centre', 'declared_net'):
            with self.subTest(mutation=mutation):
                b, doc = board(), declaration(limit=100*MM)
                cap = b['footprints'][1]
                if mutation == 'missing_ref': b['footprints'].pop()
                elif mutation == 'duplicate_ref': b['footprints'].append(copy.deepcopy(cap))
                elif mutation == 'missing_pad': cap['pads'].pop()
                elif mutation == 'duplicate_pad': cap['pads'].append(copy.deepcopy(cap['pads'][0]))
                elif mutation == 'net': cap['pads'][0]['net'] = 'OTHER'
                elif mutation == 'centre': cap['pads'][0].pop('position_nm')
                elif mutation == 'declared_net': doc['obligations'][0]['supply']['to']['net'] = 'OTHER'
                elif mutation == 'metric': doc['obligations'][0]['metric'] = 'copper-edge-to-edge'
                else: doc['obligations'][0]['supply'].pop('max_distance_nm' if mutation == 'limit' else 'citation')
                self.assertEqual(locality.grade(b, doc)['status'], 'unknown')
                if mutation == 'duplicate_ref':
                    with self.assertRaises(ValueError): pack(b, request(electrical_locality=doc))
                else:
                    plan = pack(b, request(electrical_locality=doc))
                    self.assertEqual((plan['outcome'], plan['placements']), ('unknown', []))
        b, doc = board(), declaration()
        doc['obligations'][0]['supply'].pop('max_distance_nm')
        self.assertEqual(locality.grade(b, doc)['status'], 'defect')

    def test_guidance_is_deterministic_symmetric_and_preserves_input(self):
        for fixed, priority in ((['U1'], []), ([], ['C1']), ([], ['U1'])):
            b, doc = board(), declaration()
            original = copy.deepcopy(b)
            policy = request(fixed_refs=fixed, priority_refs=priority, electrical_locality=doc)
            plan = pack(b, policy)
            self.assertEqual(plan['status'], 'planned', plan)
            self.assertEqual(plan['locality_grading']['status'], 'pass')
            self.assertEqual(plan, pack(b, policy))
            self.assertEqual(b, original)
            self.assertEqual({r['role'] for r in plan['locality_grading']['measured']}, {'supply', 'return'})
        # Without the declaration, ordinary net cost has no electrical acceptance.
        unguided = pack(board(), request())
        self.assertNotIn('locality_grading', unguided)

    def test_supply_improvement_cannot_hide_return_or_switch_regression(self):
        b, doc = board(), declaration()
        cap = b['footprints'][1]
        cap['pads'][0]['position_nm'] = [7*MM, 6*MM]
        cap['pads'][1]['position_nm'] = [7*MM, 16*MM]
        result = locality.grade(b, doc)
        self.assertEqual([d['role'] for d in result['defects']], ['return'])
        blockers, _ = locality.constraint([b['footprints'][0]], cap, doc)
        self.assertEqual(blockers, {'U1', 'C1'})
        switch = copy.deepcopy(doc['obligations'][0]['return'])
        doc['obligations'][0]['switch'] = switch
        self.assertEqual([d['role'] for d in locality.grade(b, doc)['defects']], ['return', 'switch'])

    def test_fixed_defect_and_finite_failure_never_publish(self):
        b, doc = board(), declaration()
        plan = pack(b, request(fixed_refs=['U1', 'C1'], electrical_locality=doc))
        self.assertEqual((plan['status'], plan['outcome'], plan['placements']), ('fixed_conflict', 'unknown', []))
        for p in doc['obligations'][0].values():
            if isinstance(p, dict): p['max_distance_nm'] = 0
        plan = pack(b, request(electrical_locality=doc))
        self.assertEqual((plan['outcome'], plan['placements']), ('unknown', []))

    def test_return_guidance_changes_the_actual_packed_transform(self):
        b, doc = board(), declaration(limit=4*MM)
        b['footprints'][0]['pads'][1]['position_nm'] = [5*MM, 6*MM]
        b['footprints'][1]['pads'][1]['position_nm'] = [16*MM, 19*MM]
        unguided = pack(b, request())
        placements = {p['ref']: p for p in unguided['placements']}
        saved = {**b, 'footprints': [transform(f, placements[f['ref']]['position_nm'],
                    placements[f['ref']]['rotation_deg']) for f in b['footprints']]}
        before = locality.grade(saved, doc)
        self.assertEqual([d['role'] for d in before['defects']], ['return'])
        guided = pack(b, request(electrical_locality=doc))
        self.assertEqual(guided['status'], 'planned', guided)
        self.assertEqual(guided['locality_grading']['status'], 'pass')
        self.assertNotEqual(guided['placements'], unguided['placements'])
        self.assertLess(max(m['distance_squared_nm'] for m in guided['locality_grading']['measured']),
                        max(m['distance_squared_nm'] for m in before['measured']))

    def test_targeted_origin_improvement_cannot_bypass_pad_constraints(self):
        from agent_ee.placement_plans import plan
        from test_placement_plans import scoring, row
        b, doc = board(), declaration(limit=0)
        policy = scoring()
        policy['capacitors'].pop('C2')
        spec = row(capacitor_refs=['C1'], judge_policy=policy)
        original = plan(b, request(), spec)
        self.assertEqual(original['status'], 'planned', original)
        constrained = plan(b, request(electrical_locality=doc), spec)
        self.assertEqual((constrained['status'], constrained['outcome']), ('blocked', 'unknown'))
        self.assertEqual(constrained['locality_grading']['status'], 'defect')
        self.assertIn('declared electrical locality not satisfied', constrained['reasons'])

    def test_pod_distance_counterexamples_on_owned_standins(self):
        # Original 1D point fixtures reproduce reported scalar distances only;
        # they contain no pod design geometry. Limits are test requirements.
        for cap, before, after in (
                ('C97', (5589331, 3641844), (2510383, 8276450)),
                ('C98', (6791381, 2069118), (1963173, 8578406))):
            doc = declaration(ic='U26', cap=cap)
            for distances, failed_role in ((before, 'supply'), (after, 'return')):
                b = board()
                b['footprints'][0]['ref'], b['footprints'][1]['ref'] = 'U26', cap
                for index, distance in enumerate(distances):
                    b['footprints'][0]['pads'][index]['position_nm'] = [0, index*MM]
                    b['footprints'][1]['pads'][index]['position_nm'] = [distance, index*MM]
                result = locality.grade(b, doc)
                self.assertEqual([d['role'] for d in result['defects']], [failed_role])
        doc = declaration(ic='U7', cap='C11')
        b = board()
        b['footprints'][0]['ref'], b['footprints'][1]['ref'] = 'U7', 'C11'
        for distance, expected in ((1982278, 'pass'), (7580872, 'defect')):
            b['footprints'][0]['pads'][0]['position_nm'] = [0, 0]
            b['footprints'][1]['pads'][0]['position_nm'] = [distance, 0]
            b['footprints'][0]['pads'][1]['position_nm'] = [0, MM]
            b['footprints'][1]['pads'][1]['position_nm'] = [MM, MM]
            self.assertEqual(locality.grade(b, doc)['status'], expected)
        for ic, cap, ind, before, after in (('U5', 'C4', 'L1', 12023220, 6859360),
                                           ('U7', 'C11', 'L2', 15754277, 5315473)):
            doc = declaration(ic=ic, cap=cap, limit=7*MM)
            b = board()
            b['footprints'][0]['ref'], b['footprints'][1]['ref'] = ic, cap
            for f in b['footprints']:
                for i,pad in enumerate(f['pads']): pad['position_nm'] = [0,i*MM]
            b['footprints'][0]['pads'].append(dict(number='8', net='SW', position_nm=[0,0], copper_centroid_offset_nm=[[0,1],[0,1]]))
            inductor = dict(ref=ind,pads=[dict(number='1', net='SW', position_nm=[before,0], copper_centroid_offset_nm=[[0,1],[0,1]])])
            b['footprints'].append(inductor)
            doc['obligations'][0]['switch'] = dict(
                **{'from': endpoint(ic,'8','SW'), 'to':endpoint(ind,'1','SW')},
                provenance=fixture_provenance(), max_distance_nm=7*MM, citation='Owned counterexample fixture: switch centres within 7 mm.')
            self.assertEqual([d['role'] for d in locality.grade(b,doc)['defects']], ['switch'])
            inductor['pads'][0]['position_nm'] = [after,0]
            self.assertEqual(locality.grade(b,doc)['status'], 'pass')

    def test_schema_and_registration(self):
        params = dict(criterion=locality.CRITERION, drc_check='drc', locality='project/locality.json')
        self.assertEqual(parameters(params), ['project/locality.json'])
        self.assertEqual(predicate_names(params), ['placement:electrical-locality/1'])
        for mutation in ('extra', 'return', 'endpoint', 'negative', 'boolean', 'duplicate', 'same_ref'):
            doc = declaration()
            row = doc['obligations'][0]
            if mutation == 'extra': row['bogus'] = 1
            elif mutation == 'return': row.pop('return')
            elif mutation == 'endpoint': row['supply']['from'].pop('pad')
            elif mutation == 'negative': row['supply']['max_distance_nm'] = -1
            elif mutation == 'boolean': row['supply']['max_distance_nm'] = True
            elif mutation == 'same_ref': row['supply']['to']['reference'] = 'U1'
            else: doc['obligations'].append(copy.deepcopy(row))
            with self.assertRaises(ValueError): locality.validate(doc)


class NativeLocalityTests(unittest.TestCase):
    def test_registered_captured_native_check(self):
        from test_placement_gates_native import NativePlacementGates, DRC_STAGE, DRC_FULL, placement
        from agent_ee.storage import read_json
        helper = NativePlacementGates()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        # Native RC: select actual exact pad nets from the owned saved board.
        from agent_ee.placement_probe import extract
        for expected, limit in (('pass', 100*MM), ('defect', 0), ('unknown', None), ('info', 0)):
            source = helper.source()
            facts = extract(source / 'project/rc_filter.kicad_pcb')
            fps = {f['ref']: f for f in facts['footprints']}
            shared = [(a, b) for a in fps['J1']['pads'] for b in fps['C1']['pads'] if a['net'] and a['net']==b['net']]
            a, b = shared[0]
            pair = {'from': endpoint('J1', a['number'], a['net']), 'to': endpoint('C1', b['number'], b['net']),
                    'provenance': fixture_provenance(), 'citation': 'Owned RC diagnostic distance requirement.'}
            if limit is not None: pair['max_distance_nm'] = limit
            if expected == 'info': pair.pop('provenance')
            cap_ground = next(p for p in fps['C1']['pads'] if p['net'] != a['net'])
            header_ground = next(p for p in fps['J1']['pads'] if p['net'] == cap_ground['net'])
            ground = {**pair, 'from': endpoint('J1', header_ground['number'], header_ground['net']),
                      'to': endpoint('C1', cap_ground['number'], cap_ground['net'])}
            doc = dict(schema=locality.SCHEMA, source_records=FIXTURE_SOURCES, obligations=[dict(id='RC', metric='pad-centre-to-centre',
                       supply=pair, **{'return': ground})])
            (source / 'project/locality.json').write_text(json.dumps(doc))
            spec = placement('locality', locality.CRITERION, locality='project/locality.json')
            run, results = helper.run_scope(source, [DRC_STAGE, spec, DRC_FULL])
            result = results['locality']
            self.assertEqual(result['outcome'], expected, result)
            self.assertEqual(result['execution'], 'unsupported' if expected=='unknown' else 'ok', result)
            if expected == 'info':
                self.assertIn('2 advisory locality bound(s), 2 exceeded', result['summary'])
            self.assertIn('work/locality/placement-judgment.json', result['evidence'])
            baseline = read_json(run / 'baseline.json')
            self.assertEqual(baseline['coverage']['checks']['locality']['predicates'], ['placement:electrical-locality/1'])
            self.assertIn('project/locality.json', baseline['coverage']['checks']['locality']['immutable_inputs'])
            self.assertIn('project/locality.json', baseline['files'])
            self.assertEqual(result['details']['locality'], {'path': 'project/locality.json',
                             'sha256': baseline['files']['project/locality.json']})

    def test_registered_captured_native_bound_output_loop(self):
        self.test_registered_captured_native_distinct_source_loop(bound=True)

    def test_registered_captured_native_distinct_source_loop(self, bound=False):
        from test_placement_gates_native import NativePlacementGates, DRC_STAGE, DRC_FULL, placement
        from agent_ee.placement_probe import extract
        helper = NativePlacementGates()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        source = helper.source()
        facts = extract(source / 'project/rc_filter.kicad_pcb')
        fps = {f['ref']: f for f in facts['footprints']}
        # Owned RC topology exercises the buck loop's three-reference contract.
        cap = fps['C1']
        supply = next(p for p in cap['pads'] if 'GND' not in p['net'])
        ground = next(p for p in cap['pads'] if 'GND' in p['net'])
        def pair(ref, target):
            pad = next(p for p in fps[ref]['pads'] if p['net'] == target['net'])
            return dict(**{'from': {**endpoint(ref, pad['number'], pad['net']), 'uuid': pad['uuid']},
                           'to': {**endpoint('C1', target['number'], target['net']), 'uuid': target['uuid']}},
                        provenance=fixture_provenance(), max_distance_nm=100*MM, citation='Owned native three-reference loop requirement.')
        doc = dict(schema=locality.SCHEMA, source_records=FIXTURE_SOURCES, obligations=[dict(id='output-loop', metric='pad-centre-to-centre',
                   supply=pair('R1', supply), **{'return': pair('J1', ground)})])
        if bound:
            doc['obligations'][0].update(kind='buck-output-loop', ic='J1', inductor='R1',
                                         return_net=ground['net'])
        (source / 'project/locality.json').write_text(json.dumps(doc))
        spec = placement('loop', locality.CRITERION, locality='project/locality.json')
        run, results = helper.run_scope(source, [DRC_STAGE, spec, DRC_FULL])
        self.assertEqual((results['loop']['execution'], results['loop']['outcome']), ('ok', 'pass'), results['loop'])
        self.assertIn('work/loop/placement-judgment.json', results['loop']['evidence'])

    def test_native_guided_save_reload(self):
        from test_placer_native import NativePlacerTest
        from agent_ee.placer_runner import place_candidate
        from agent_ee.placement_probe import extract
        helper = NativePlacerTest()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        import pcbnew as k
        from agent_ee.storage import digest
        native = k.LoadBoard(str(helper.source))
        nets = {}
        for name in ('VCC', 'GND'):
            nets[name] = k.NETINFO_ITEM(native, name)
            native.Add(nets[name])
        for fp in native.GetFootprints():
            first = list(fp.Pads())[0]
            first.SetNet(nets['VCC'])
            second = k.PAD(fp)
            second.SetShape(first.GetShape())
            second.SetAttribute(first.GetAttribute())
            second.SetSize(first.GetSize())
            second.SetLayerSet(first.GetLayerSet())
            second.SetNumber('2')
            second.SetNet(nets['GND'])
            second.SetPosition(k.VECTOR2I(first.GetPosition().x, first.GetPosition().y+700000))
            fp.Add(second)
        k.SaveBoard(str(helper.source), native)
        helper.config['authorization']['source_sha256'] = digest(helper.source)
        helper.netlist.write_text('<export><components><comp ref="U1"/><comp ref="C1"/></components><nets>'
            '<net code="1" name="VCC"><node ref="U1" pin="1"/><node ref="C1" pin="1"/></net>'
            '<net code="2" name="GND"><node ref="U1" pin="2"/><node ref="C1" pin="2"/></net></nets></export>')
        facts = extract(helper.source)
        fps = {f['ref']: f for f in facts['footprints']}
        # The owned source has native electrical pads; explicitly select their existing identities.
        pairs = [(a,b) for a in fps['U1']['pads'] for b in fps['C1']['pads'] if a['net'] and a['net']==b['net']]
        self.assertTrue(pairs)
        a,b = pairs[0]
        pair = {'from': endpoint('U1', a['number'], a['net']), 'to': endpoint('C1', b['number'], b['net']),
                'provenance': fixture_provenance(), 'max_distance_nm': 5*MM, 'citation': 'Owned native geometry fixture: 5 mm diagnostic limit.'}
        doc = dict(schema=locality.SCHEMA, source_records=FIXTURE_SOURCES, obligations=[dict(id='native', metric='pad-centre-to-centre',
                   supply=pair, **{'return': {**pair, 'from': endpoint('U1', '2', 'GND'),
                                                      'to': endpoint('C1', '2', 'GND')}})])
        helper.config['policy']['electrical_locality'] = doc
        result = place_candidate(helper.source, helper.netlist, helper.scratch / 'guided', helper.config)
        self.assertEqual(result['status'], 'placed', result)
        self.assertEqual(result['judgment']['locality_grading']['status'], 'pass')
        self.assertEqual(locality.grade(extract(helper.scratch/'guided/candidate.kicad_pcb'), doc)['status'], 'pass')
        # The same native writer must publish exceeded, unsourced guidance.
        for role in ('supply', 'return'):
            doc['obligations'][0][role].pop('provenance')
            doc['obligations'][0][role]['max_distance_nm'] = 0
        advisory = place_candidate(helper.source, helper.netlist, helper.scratch / 'advisory', helper.config)
        self.assertEqual(advisory['status'], 'placed', advisory)
        self.assertEqual(advisory['judgment']['locality_grading']['status'], 'info')
        self.assertTrue(all(a['exceeded'] for a in advisory['judgment']['locality_grading']['advisories']))


    def test_runner_timeout_writes_partial_result_and_heartbeat_without_proposal(self):
        import contextlib
        import io
        from agent_ee.placer_runner import main
        from test_placer_native import NativePlacerTest
        helper = NativePlacerTest()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        helper.config['policy']['search_timeout_s'] = 1e-12
        request_path = helper.root / 'timeout-request.json'
        request_path.write_text(json.dumps(helper.config))
        result_path = helper.scratch / 'result.json'
        destination = helper.scratch / 'proposal'
        output, diagnostics = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(diagnostics):
            code = main([str(helper.source), str(helper.netlist), '--request', str(request_path),
                         '--destination', str(destination), '--result', str(result_path)])
        self.assertEqual(code, 1)
        result = json.loads(result_path.read_text())
        self.assertEqual((result['status'], result['outcome'], result['placements']),
                         ('packing_budget_exhausted', 'unknown', []))
        self.assertTrue(result['search']['partial']['placements'])
        self.assertFalse(destination.exists())
        self.assertEqual(output.getvalue(), '')
        events = [json.loads(line) for line in diagnostics.getvalue().splitlines()]
        self.assertTrue(events)
        self.assertTrue(all(e['event'] == 'placement_heartbeat' for e in events))
        self.assertEqual(helper.source.read_bytes(), helper.saved)


class LocalityIndexTests(unittest.TestCase):
    def test_lying_dict_snapshot_is_refused_with_and_without_deepcopy_hook(self):
        class Liar(dict):
            def __getitem__(self, key):
                return 'SW' if key == 'net' else dict.__getitem__(self, key)

            def values(self):
                return [self[key] for key in self]

        class Wrapper(dict):
            def __deepcopy__(self, memo):
                endpoint = copy.deepcopy(dict(self), memo)
                endpoint['net'] = 'GND'
                return Liar(endpoint)

        for wrapped in (False, True):  # Reviewer P7b and P7.
            facts, doc = bound_output_loop()
            row = doc['obligations'][0]
            raw = dict(row['switch']['from'], net=row['return_net'])
            row['switch']['from'] = Wrapper(raw) if wrapped else Liar(raw)
            for operation in (locality.PlacementIndex, locality.validate,
                              lambda d: locality.grade(facts, d)):
                with self.subTest(wrapped=wrapped, operation=operation), self.assertRaisesRegex(
                        ValueError, 'locality document must contain only JSON values'):
                    operation(doc)

    def test_index_snapshot_is_unreachable_by_a_copy_hook_after_validation(self):
        import sys
        kept = []

        class Keep(dict):
            def __deepcopy__(self, memo):
                endpoint = copy.deepcopy(dict(self), memo)
                kept.append(endpoint)
                return endpoint

        _, doc = bound_output_loop()
        row = doc['obligations'][0]
        row['switch']['from'] = Keep(row['switch']['from'])

        def after_validate(frame, event, arg):  # Stands in for a thread switch.
            if event == 'return' and frame.f_code is locality.validate.__code__ and kept:
                kept[-1]['net'] = row['return_net']

        previous_profile = sys.getprofile()
        sys.setprofile(after_validate)
        try:
            state = locality.PlacementIndex(doc)
        finally:
            sys.setprofile(previous_profile)
        self.assertEqual(kept[-1]['net'], row['return_net'])
        self.assertEqual({pair[0]['net'] for pairs in state.pairs.values() for pair in pairs
                          if pair[0]['reference'] == row['switch']['from']['reference']
                          and pair[0]['pad'] == row['switch']['from']['pad']}, {'SW'})

    def test_grade_never_rereads_caller_document_after_validation(self):
        import sys
        facts, doc = bound_output_loop()
        expected = locality.grade(facts, copy.deepcopy(doc))
        self.assertEqual(expected['status'], 'pass')

        def tighten(d):
            for row in d['obligations']:
                for role in locality.roles(row):
                    row[role]['max_distance_nm'] = 0

        def unbind(d):
            d['obligations'][0]['supply']['from']['net'] = 'NOT_A_NET'

        mutations = {'tighten': tighten, 'unbind': unbind, 'clear': lambda d: d['obligations'].clear(),
                     'records': lambda d: d.update(source_records={'x': 1}),
                     'metric': lambda d: d['obligations'][0].update(metric='other')}
        for name, mutate in mutations.items():  # AGENTEE-363 A: mutation after validate() returns.
            facts, doc = bound_output_loop()
            fired = []

            def after_validate(frame, event, arg):  # Stands in for a thread switch.
                if event == 'return' and frame.f_code is locality.validate.__code__ and not fired:
                    fired.append(name)
                    mutate(doc)

            previous_profile = sys.getprofile()
            sys.setprofile(after_validate)
            try:
                result = locality.grade(facts, doc)
            finally:
                sys.setprofile(previous_profile)
            with self.subTest(mutation=name):
                self.assertEqual(fired, [name])
                self.assertEqual(result, expected)
                self.assertIsNot(result['declaration'], doc)
                self.assertNotEqual(result['declaration'], doc)

    def test_validate_returns_fresh_snapshot_and_grade_reports_it(self):
        facts, doc = bound_output_loop()
        snapshot = locality.validate(doc)
        self.assertEqual(snapshot, doc)
        self.assertIsNot(snapshot, doc)
        self.assertIsNot(snapshot['obligations'][0], doc['obligations'][0])
        declaration = locality.grade(facts, doc)['declaration']
        doc['obligations'][0]['supply']['max_distance_nm'] = 0
        self.assertEqual(declaration, snapshot)

    def test_deep_nesting_raises_typed_error_at_every_entry_point(self):
        def chain(kind, depth, leaf='x'):
            value = leaf
            for _ in range(depth):
                value = [value] if kind == 'list' else {'k': value}
            return value

        # Provenance values sit at depth 5 (document, obligations, obligation, pair, provenance).
        allowed = locality.MAX_DOCUMENT_DEPTH - 4
        operations = (('index', locality.PlacementIndex), ('validate', locality.validate))
        for kind in ('list', 'dict'):
            for depth in (allowed + 1, 100_000):  # Far beyond the interpreter recursion limit.
                for where in ('provenance', 'citation', 'top'):
                    facts, doc = bound_output_loop()
                    nested = chain(kind, depth)
                    if where == 'provenance':
                        doc['obligations'][0]['supply']['provenance']['extra'] = nested
                    elif where == 'citation':  # One level shallower than provenance values.
                        doc['obligations'][0]['supply']['citation'] = [nested]
                    else:
                        doc = chain(kind, depth, doc)
                    for name, operation in operations + (('grade', lambda d: locality.grade(facts, d)),):
                        with self.subTest(kind=kind, depth=depth, where=where, operation=name), \
                                self.assertRaisesRegex(ValueError, f'^{locality.DEPTH_ERROR}$'):
                            operation(doc)
        for kind in ('list', 'dict'):  # The boundary itself stays acceptable.
            facts, doc = bound_output_loop()
            expected = locality.grade(facts, copy.deepcopy(doc))
            doc['obligations'][0]['supply']['provenance']['extra'] = chain(kind, allowed)
            locality.validate(doc)
            locality.PlacementIndex(doc)
            graded = locality.grade(facts, doc)
            with self.subTest(boundary=kind):
                self.assertEqual(graded['status'], expected['status'])
                self.assertEqual(graded['declaration'], doc)

    def test_deep_nesting_under_subclass_or_copy_hook_never_raises_recursion_error(self):
        class Wrapper(dict):
            def __deepcopy__(self, memo):
                return copy.deepcopy(dict(self), memo)

        deep = 'x'
        for _ in range(100_000):
            deep = [deep]
        for container in (Wrapper, type('Listing', (list,), {})):
            facts, doc = bound_output_loop()
            supply = doc['obligations'][0]['supply']
            supply['provenance'] = (Wrapper(supply['provenance'], extra=deep) if container is Wrapper
                                    else supply['provenance'])
            if container is not Wrapper:
                supply['provenance']['extra'] = container([deep])
            # The index walks subclass storage before copying: depth refuses first.
            with self.subTest(container=container, operation='index'), \
                    self.assertRaisesRegex(ValueError, f'^{locality.DEPTH_ERROR}$'):
                locality.PlacementIndex(doc)
            # validate() and grade() refuse the subclass before descending into it.
            for name, operation in (('validate', locality.validate),
                                    ('grade', lambda d: locality.grade(facts, d))):
                with self.subTest(container=container, operation=name), self.assertRaisesRegex(
                        ValueError, '^locality document must contain only JSON values$'):
                    operation(doc)

    def test_cyclic_documents_raise_typed_json_error_at_every_entry_point(self):
        for cycle in ('dict', 'list', 'mixed'):
            facts, doc = bound_output_loop()
            if cycle == 'dict':
                doc['cycle'] = doc
            elif cycle == 'list':
                doc['obligations'].append(doc['obligations'])
            else:
                doc['obligations'][0]['supply']['citation'] = [doc]
            for operation in (locality.PlacementIndex, locality.validate,
                              lambda d: locality.grade(facts, d)):
                with self.subTest(cycle=cycle, operation=operation), self.assertRaisesRegex(
                        ValueError, '^locality document must contain only JSON values$'):
                    operation(doc)

    def test_shared_acyclic_json_values_are_copied_without_aliases(self):
        shared = {'items': [None, True, 1, 1.5, 'text']}
        original = [shared, shared]
        snapshot = locality._plain_json(original)
        self.assertEqual(snapshot, original)
        self.assertIsNot(snapshot, original)
        self.assertIsNot(snapshot[0], snapshot[1])
        self.assertIsNot(snapshot[0]['items'], snapshot[1]['items'])
        shared['items'].clear()
        self.assertEqual(snapshot[0]['items'], [None, True, 1, 1.5, 'text'])

    def test_hostile_string_cannot_bypass_net_disjointness(self):
        class Net(str):
            __hash__ = object.__hash__

            def __eq__(self, other):
                return False

            def __ne__(self, other):
                return True

        facts, doc = bound_output_loop()
        row = doc['obligations'][0]
        row['switch']['from']['net'] = Net(row['return_net'])  # Reviewer P8.
        for operation in (locality.PlacementIndex, locality.validate,
                          lambda d: locality.grade(facts, d)):
            with self.subTest(operation=operation), self.assertRaisesRegex(
                    ValueError, 'locality document must contain only JSON values'):
                operation(doc)

    def test_non_json_types_and_subclasses_are_refused_before_schema_reads(self):
        class PretendJSON(type):
            __hash__ = type.__hash__

            def __eq__(cls, other):
                return True

        class Text(str, metaclass=PretendJSON):
            pass

        values = (type('Dict', (dict,), {})(), type('List', (list,), {})([]),
                  type('Text', (str,), {})('text'), type('Integer', (int,), {})(1),
                  type('Float', (float,), {})(1.0), Text('citation'),
                  (), set(), object(), {1: 'text'})
        for value in values:
            facts, doc = bound_output_loop()
            doc['obligations'][0]['supply']['citation'] = value
            for operation in (locality.PlacementIndex, locality.validate,
                              lambda d: locality.grade(facts, d)):
                with self.subTest(type=type(value), operation=operation), self.assertRaisesRegex(
                        ValueError, 'locality document must contain only JSON values'):
                    operation(doc)

    def test_index_validates_deepcopy_output_including_hostile_endpoint(self):
        from unittest.mock import patch

        class Sly(dict):
            def __deepcopy__(self, memo):
                endpoint = copy.deepcopy(dict(self), memo)
                endpoint['net'] += '_SWAPPED'
                return endpoint

        _, doc = bound_output_loop()
        row = doc['obligations'][0]
        row['switch']['from'] = Sly(row['switch']['from'])
        # Reviewer P5: the copied endpoint differs from the caller's endpoint.
        with patch.object(locality, 'validate', wraps=locality.validate) as validate:
            state = locality.PlacementIndex(doc)
        snapshot = validate.call_args.args[0]
        self.assertIsNot(snapshot, doc)
        self.assertEqual(snapshot['obligations'][0]['switch']['from']['net'], 'SW_SWAPPED')
        self.assertTrue(any(pair[0]['net'] == 'SW_SWAPPED'
                            for pairs in state.pairs.values() for pair in pairs))

        class InvalidCopy(dict):
            def __deepcopy__(self, memo):
                endpoint = copy.deepcopy(dict(self), memo)
                endpoint['net'] = row['return']['from']['net']
                return endpoint

        row['switch']['from'] = InvalidCopy(row['switch']['from'])
        with self.assertRaisesRegex(ValueError, 'switch nets must differ'):
            locality.PlacementIndex(doc)

    def test_index_validates_snapshot_when_caller_changes_during_copy(self):
        from unittest.mock import patch

        facts, doc = bound_output_loop()
        snapshot = copy.deepcopy(doc)

        def copy_then_mutate(document):
            self.assertIs(document, doc)
            document['obligations'].clear()
            return snapshot

        with patch.object(locality, 'deepcopy', side_effect=copy_then_mutate), \
                patch.object(locality, 'validate', wraps=locality.validate) as validate:
            state = locality.PlacementIndex(doc)
        validate.assert_called_once_with(snapshot)
        self.assertIsNot(validate.call_args.args[0], snapshot)  # fresh plain copy, unreachable by the copier
        movable, *fixed = facts['footprints']
        for fp in fixed:
            state.push(fp)
        self.assertEqual(state.prepare(transform(movable, [0, 0], 0)).constraint([0, 0]),
                         locality.constraint(fixed, transform(movable, [0, 0], 0), snapshot))

    def test_index_pairs_and_endpoints_are_read_only_at_every_level(self):
        _, doc = bound_output_loop()
        state = locality.PlacementIndex(doc)
        ref = next(iter(state.pairs))
        own, peer = state.pairs[ref][0][:2]
        endpoint_map = state.endpoints[ref]
        key = next(iter(endpoint_map))
        mutations = [(state.pairs, ref, ()), (state.endpoints, ref, {}),
                     (state.pairs[ref], 0, (peer, own, 0)),
                     (state.pairs[ref][0], 2, 0),
                     (endpoint_map, key, {}), (own, 'net', 'CHANGED'),
                     (peer, 'reference', 'R9'), (endpoint_map[key], 'pad', '99')]
        for mapping, key, value in mutations:
            with self.subTest(key=key), self.assertRaises(TypeError):
                mapping[key] = value
        for attribute in ('pairs', 'endpoints'):
            with self.subTest(attribute=attribute), self.assertRaises(AttributeError):
                setattr(state, attribute, {})

    def test_index_snapshots_caller_document_before_push_and_prepare(self):
        mutations = [(role, end, field, value)
                     for role in ('supply', 'return', 'switch')
                     for end in ('from', 'to')
                     for field, value in (('net', 'CHANGED'), ('reference', 'R9'),
                                          ('pad', '99'), ('uuid', 'invalid'))]
        mutations += [(role, None, 'max_distance_nm', 0)
                      for role in ('supply', 'return', 'switch')]
        mutations.append((None, None, 'obligations', []))
        for role, end, field, value in mutations:
            with self.subTest(role=role, end=end, field=field):
                facts, doc = bound_output_loop()
                original = copy.deepcopy(doc)
                state = locality.PlacementIndex(doc)
                if role is None:
                    doc[field].clear()
                else:
                    target = doc['obligations'][0][role]
                    if end is not None:
                        target = target[end]
                    target[field] = value
                for movable in facts['footprints']:
                    fixed = [fp for fp in facts['footprints'] if fp is not movable]
                    for fp in fixed:
                        state.push(fp)
                    origin = transform(movable, [0, 0], 0)
                    candidate = state.prepare(origin)
                    self.assertEqual(candidate.targets(), sorted(set(map(tuple,
                        locality.targets(fixed, origin, original)))))
                    for position in ([0, 0], movable['position_nm'], [99*MM, 99*MM]):
                        self.assertEqual(candidate.constraint(position), locality.constraint(
                            fixed, transform(movable, position, 0), original))
                    for fp in fixed:
                        state.pop(fp['ref'])

    def test_index_matches_original_cost_targets_and_backtracking(self):
        b, doc = board(), declaration()
        # Fractional, rotated copper centroids must keep exact candidate ranks.
        b['footprints'][1]['pads'][0]['copper_centroid_offset_nm'] = [[1, 3], [-2, 7]]
        state = locality.PlacementIndex(doc)
        fixed, movable = b['footprints']
        state.push(fixed)
        for rotation in (0, 90, 180, 270):
            candidate = state.prepare(transform(movable, [0, 0], rotation))
            self.assertEqual(candidate.targets(), sorted(set(map(tuple, locality.targets(
                [fixed], transform(movable, [0, 0], rotation), doc)))))
            for position in ([0, 0], [7*MM, 6*MM], [19*MM, MM]):
                self.assertEqual(candidate.constraint(position), locality.constraint(
                    [fixed], transform(movable, position, rotation), doc))
        state.pop('U1')
        self.assertEqual(state.prepare(transform(movable, [0, 0], 0)).pairs, [])
        moved = transform(fixed, [12*MM, 8*MM], 90)
        state.push(moved)
        candidate = state.prepare(transform(movable, [0, 0], 0))
        self.assertEqual(candidate.constraint([MM, 2*MM]), locality.constraint(
            [moved], transform(movable, [MM, 2*MM], 0), doc))

    def test_seeded_index_differential_over_random_push_pop_and_output_loops(self):
        from test_placement import rectangle
        rng = random.Random(9332)
        comparisons = pushes = pops = loops = 0
        rotations = set()
        # Adapted from the AGENTEE-332 review differential, with original
        # generated geometry and independent legacy constraint/target oracles.
        for case in range(300):
            footprints = {}
            for i in range(rng.randint(3, 8)):
                ref = 'P' + str(i)
                x, y = rng.randint(1, 14), rng.randint(1, 14)
                w, h = rng.choice(((2, 2), (3, 2), (2, 4), (1, 1)))
                fp = part(ref, x, y, shape=rectangle(x, y, w, h))
                fp['pads'] = [dict(
                    uuid=f'{ref}-{k}', number=str(k), net=net,
                    position_nm=[x*MM + rng.randint(0, w*MM), y*MM + rng.randint(0, h*MM)],
                    size_nm=[100000, 100000], shape=1, attribute='smd', layers=['F.Cu'], rotation_deg=0,
                    copper_centroid_offset_nm=[[rng.randint(-50, 50), rng.choice((1, 3, 7))],
                                               [rng.randint(-50, 50), rng.choice((1, 2, 9))]])
                    for k, net in ((1, 'VCC'), (2, 'GND'), (3, 'SW'))]
                footprints[ref] = fp
            refs = list(footprints)
            doc = {'schema': locality.SCHEMA, 'source_records': FIXTURE_SOURCES, 'obligations': []}
            for j in range(rng.randint(1, 5)):
                ic, cap, inductor = rng.sample(refs, 3)
                obligation = declaration(ic=ic, cap=cap, limit=rng.choice((2, 4, 6, 9, 15))*MM)['obligations'][0]
                obligation['id'] = f'o{j}'
                if rng.random() < .5:
                    loops += 1
                    obligation['supply']['from'] = endpoint(inductor, '1', 'VCC')
                    obligation['switch'] = dict(
                        **{'from': endpoint(ic, '3', 'SW'), 'to': endpoint(inductor, '3', 'SW')},
                        max_distance_nm=6*MM, citation='Owned seeded output-loop limit.')
                doc['obligations'].append(obligation)
            index, placed = locality.PlacementIndex(doc), []
            for step in range(25):
                free = [ref for ref in refs if ref not in {fp['ref'] for fp in placed}]
                if placed and (not free or rng.random() < .4):
                    index.pop(placed.pop()['ref'])
                    pops += 1
                else:
                    fp = transform(footprints[rng.choice(free)],
                                   [rng.randint(0, 20*MM), rng.randint(0, 20*MM)],
                                   rng.choice((0, 90, 180, 270)))
                    placed.append(fp)
                    index.push(fp)
                    pushes += 1
                for ref in refs:
                    if ref in {fp['ref'] for fp in placed}:
                        continue
                    rotation = rng.choice((0, 90, 180, 270))
                    rotations.add(rotation)
                    origin = transform(footprints[ref], [0, 0], rotation)
                    evaluator = index.prepare(origin)
                    with self.subTest(case=case, step=step, ref=ref, rotation=rotation):
                        for _ in range(3):
                            position = [rng.randint(-5*MM, 25*MM), rng.randint(-5*MM, 25*MM)]
                            self.assertEqual(evaluator.constraint(position), locality.constraint(
                                placed, transform(footprints[ref], position, rotation), doc))
                            comparisons += 1
                        expected = sorted(set(map(tuple, locality.targets(placed, origin, doc))))
                        self.assertEqual(evaluator.targets(), expected)
            while placed:
                index.pop(placed.pop()['ref'])
            self.assertEqual(index.positions, {})
        self.assertGreater(comparisons, 30_000)
        self.assertGreater(pushes, 3000)
        self.assertGreater(pops, 2000)
        self.assertGreater(loops, 300)
        self.assertEqual(rotations, {0, 90, 180, 270})

    def test_direct_constructor_revalidates_role_and_net_bindings(self):
        for mutation, message in (
                ('unrelated', 'supply must start at the declared inductor'),
                ('swapped', 'return must use the declared return_net'),
                ('switch_source', 'switch pair must start at the declared IC'),
                ('switch_target', 'switch must end at the declared inductor'),
                ('switch_net', 'switch nets must differ from supply and return'),
                ('overlap', 'distinct-source supply and return nets must differ')):
            _, doc = bound_output_loop()
            row = doc['obligations'][0]
            if mutation == 'unrelated':
                row['supply']['from']['reference'] = 'R9'
            elif mutation == 'swapped':
                for end in ('from', 'to'):
                    row['supply'][end]['net'], row['return'][end]['net'] = 'GND', 'VCC'
            elif mutation == 'switch_source':
                row['switch']['from']['reference'] = 'R9'
            elif mutation == 'switch_target':
                row['switch']['to']['reference'] = 'R9'
            elif mutation == 'switch_net':
                row['switch']['from']['net'] = 'VCC'
            else:
                for key in ('kind', 'ic', 'inductor', 'return_net'):
                    row.pop(key)
                row['supply']['to']['net'] = 'GND'
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError, message):
                locality.PlacementIndex(doc)

    def test_uuid_selected_repeated_number_is_indexed_without_aliasing(self):
        b, doc = board(), declaration()
        cap = b['footprints'][1]
        extra = copy.deepcopy(cap['pads'][0])
        cap['pads'][0]['uuid'] = '00000000-0000-0000-0000-000000000001'
        extra.update(uuid='00000000-0000-0000-0000-000000000002', position_nm=[999*MM, 0])
        cap['pads'].append(extra)
        doc['obligations'][0]['supply']['to']['uuid'] = cap['pads'][0]['uuid']
        state = locality.PlacementIndex(doc)
        state.push(cap)
        candidate = state.prepare(transform(b['footprints'][0], [0,0], 0))
        self.assertEqual(candidate.constraint([MM,MM]), locality.constraint(
            [cap], transform(b['footprints'][0], [MM,MM], 0), doc))
        for position in ([15*MM, 15*MM], [999*MM, 0]):
            with self.subTest(position=position):
                self.assertEqual(candidate.constraint(position), locality.constraint(
                    [cap], transform(b['footprints'][0], position, 0), doc))
                self.assertEqual(bool(candidate.constraint(position)[0]), position == [999*MM, 0])

    def test_target_budget_refuses_instead_of_truncating_constraints(self):
        pairs = [([0,0], [i*1000, i*1000], 1, {'U1','C1'}) for i in range(100)]
        candidate = locality.CandidateLocality(pairs)
        with self.assertRaises(locality.TargetBudgetExhausted):
            candidate.targets()
        # Every role is still included in cost/constraint evaluation.
        self.assertEqual(candidate.constraint([0,0])[0], {'U1','C1'})
        doc = declaration()
        doc['obligations'] = [{**copy.deepcopy(doc['obligations'][0]), 'id': str(i)} for i in range(100)]
        for i, obligation in enumerate(doc['obligations']):
            for role in ('supply', 'return'):
                obligation[role]['max_distance_nm'] = (i+1)*MM
        result = pack(board(), request(electrical_locality=doc))
        self.assertEqual((result['status'], result['outcome'], result['placements']),
                         ('packing_budget_exhausted', 'unknown', []))
        self.assertEqual(result['reason'], 'locality target budget exhausted')

    def test_timeout_and_step_budget_keep_diagnostic_partial_separate(self):
        events = []
        for changes in ({'search_timeout_s': 1e-12}, {'step_budget': 1}):
            result = pack(board(), request(electrical_locality=declaration(), **changes),
                          heartbeat=events.append)
            self.assertEqual((result['status'], result['outcome'], result['placements']),
                             ('packing_budget_exhausted', 'unknown', []))
            self.assertEqual(result['search']['termination'], 'budget_exhausted')
            self.assertEqual(result['search']['partial']['placements'][0]['ref'], 'U1')
            self.assertEqual(result['search']['partial']['authority'],
                             'diagnostic-only-unvalidated-partial-placement')
        exhausted = pack(board(), request(electrical_locality=declaration(limit=0)))
        self.assertEqual(exhausted['status'], 'packing_search_exhausted')
        self.assertEqual(exhausted['search']['termination'], 'search_exhausted')
        self.assertEqual(exhausted['placements'], [])
        self.assertTrue(events)
        self.assertTrue(all(e['steps_used'] >= 0 for e in events))
        for invalid in (None, 0, -1, True, float('inf'), float('nan'), '1', 86401, 10**1000):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                pack(board(), request(search_timeout_s=invalid))

    def test_pod_scale_index_benchmark_331_footprints_15_obligations(self):
        import time
        from unittest.mock import patch
        b = board()
        for i in range(329):
            fp = copy.deepcopy(b['footprints'][1])
            fp['ref'] = 'R'+str(i)
            for pad in fp['pads']:
                pad['uuid'] = fp['ref'] + pad['number']
            b['footprints'].append(fp)
        doc = declaration()
        doc['obligations'] += [declaration(cap='R'+str(i))['obligations'][0] for i in range(14)]
        self.assertEqual(len(b['footprints']), 331)
        self.assertEqual(len(doc['obligations']), 15)
        started = time.monotonic()
        with patch.object(locality, 'resolve', wraps=locality.resolve) as resolver:
            state = locality.PlacementIndex(doc)
            for fp in b['footprints']:
                state.push(fp)
            candidate = state.prepare(transform(b['footprints'][1], [0,0], 0))
        self.assertLessEqual(resolver.call_count, 34)
        self.assertEqual(len(candidate.pairs), 2)
        expected = locality.constraint(b['footprints'], b['footprints'][1], doc)
        with patch.object(locality, 'resolve', side_effect=AssertionError('per-candidate pad scan')):
            for _ in range(5000):
                self.assertEqual(candidate.constraint(b['footprints'][1]['position_nm']), expected)
            for fp in b['footprints'][16:]:
                self.assertEqual(state.prepare(fp).pairs, [])
        self.assertLess(time.monotonic()-started, 120.0, '331-footprint incremental locality safety ceiling')

    def test_pod_scale_guided_pack_331_footprints_with_wall_time_bound(self):
        import time
        from unittest.mock import patch
        from test_placement import rectangle
        templates = board()['footprints']
        footprints = []
        for i in range(331):
            fp = copy.deepcopy(templates[i % 2])
            fp.update(ref='P'+str(i), uuid='P'+str(i))
            for pad in fp['pads']:
                pad['uuid'] = fp['ref'] + pad['number']
            footprints.append(transform(fp, [(i % 20)*4*MM, (i // 20)*4*MM], 0))
        b = capture(footprints, rectangle(0, 0, 84, 72))
        b['pcbnew_version'] = '10.0.7'
        doc = {'schema': locality.SCHEMA, 'source_records': FIXTURE_SOURCES, 'obligations': []}
        for i in range(15):
            doc['obligations'] += declaration(ic='P'+str(2*i), cap='P'+str(2*i+1),
                                              limit=8*MM)['obligations']
        # One constrained movable peer among 330 placed footprints isolates the
        # reported per-state/per-candidate growth without benchmarking backjumps.
        policy = request(fixed_refs=[f['ref'] for f in footprints if f['ref'] != 'P29'],
                         electrical_locality=doc)
        started = time.monotonic()
        with patch.object(locality, 'resolve', wraps=locality.resolve) as resolver, \
             patch.object(locality.CandidateLocality, 'constraint', autospec=True,
                          side_effect=locality.CandidateLocality.constraint) as evaluator:
            result = pack(b, policy)
        elapsed = time.monotonic()-started
        self.assertEqual(result['status'], 'planned', result)
        self.assertEqual(len(result['placements']), 331)
        self.assertEqual(result['locality_grading']['status'], 'pass')
        self.assertLess(resolver.call_count, 400, 'resolution must scale with state updates, not candidates')
        self.assertLessEqual(evaluator.call_count, 3000, 'bounded candidate locality work')
        self.assertLessEqual(result['steps_used'], 16, 'bounded exact candidate tests')
        self.assertLess(elapsed, 180.0, '331-footprint guided packing safety ceiling')

    def test_exact_limit_passes_and_one_nanometre_over_blocks(self):
        # Every admissible slot beside the fixed U1 lies exactly 2.5 mm from its
        # pads, so a plan exists at the limit and none one nanometre tighter.
        for limit, planned in ((5*MM//2, True), (5*MM//2 - 1, False)):
            for caps in (1, 3):
                with self.subTest(limit=limit, caps=caps):
                    b, doc = board(), declaration(limit=limit)
                    for i in range(2, caps + 1):
                        c = copy.deepcopy(b['footprints'][1])
                        c['ref'] = c['uuid'] = 'C' + str(i)
                        for pad in c['pads']:
                            pad['uuid'] = c['ref'] + '-' + pad['number']
                        b['footprints'].append(c)
                        doc['obligations'] += declaration(cap=c['ref'], limit=limit)['obligations']
                    result = pack(b, request(electrical_locality=doc))
                    self.assertEqual(result['status'] == 'planned', planned, result['status'])
                    if planned:
                        self.assertEqual(result['locality_grading']['status'], 'pass')
                        self.assertIn(limit**2, {m['distance_squared_nm']
                                                 for m in result['locality_grading']['measured']})
                    else:
                        self.assertEqual((result['outcome'], result['placements']), ('unknown', []))
                    if caps == 3:
                        from agent_ee.placer import _pack
                        joint = _pack(b, request(electrical_locality=doc), heartbeat=None, joint=True)
                        self.assertEqual(joint['status'] == 'planned', planned, joint['status'])
                        if planned:
                            self.assertEqual(joint['locality_grading']['status'], 'pass')
                            self.assertIn(limit**2, {m['distance_squared_nm']
                                for m in joint['locality_grading']['measured']})
                        else:
                            self.assertEqual((joint['outcome'], joint['placements']), ('unknown', []))
        b, doc = board(), declaration(limit=5*MM//2)
        index = locality.PlacementIndex(doc)
        index.push(b['footprints'][0])
        evaluator = index.prepare(transform(b['footprints'][1], [0, 0], 0))
        for x, blocked in ((int(8.5*MM), False), (int(8.5*MM) + 1, True)):
            moved = transform(b['footprints'][1], [x, 6*MM], 0)
            expected = locality.constraint([b['footprints'][0]], moved, doc)
            self.assertEqual(evaluator.constraint([x, 6*MM]), expected)
            self.assertEqual(bool(expected[0]), blocked)
            graded = locality.grade({**b, 'footprints': [b['footprints'][0], moved]}, doc)['status']
            self.assertEqual(graded, 'defect' if blocked else 'pass')

    def test_guided_pack_does_not_scan_placed_footprints_per_candidate(self):
        from unittest.mock import patch
        from test_placement import rectangle
        import agent_ee.placer as placer

        class Counting(list):
            iterations = 0

            def __iter__(self):
                Counting.iterations += 1
                return super().__iter__()

        original = placer._Search.__init__

        def counted(search, *args, **kwargs):
            original(search, *args, **kwargs)
            search.placed = Counting(search.placed)

        templates = board()['footprints']
        footprints = []
        for i in range(331):
            fp = copy.deepcopy(templates[i % 2])
            fp.update(ref='P'+str(i), uuid='P'+str(i))
            for pad in fp['pads']:
                pad['uuid'] = fp['ref'] + pad['number']
            footprints.append(transform(fp, [(i % 20)*4*MM, (i // 20)*4*MM], 0))
        b = capture(footprints, rectangle(0, 0, 84, 72))
        b['pcbnew_version'] = '10.0.7'
        for movable, clustered in (({'P29'}, False), ({'P29', 'P31'}, True)):
            doc = {'schema': locality.SCHEMA, 'source_records': FIXTURE_SOURCES, 'obligations': []}
            for i in range(15):
                doc['obligations'] += declaration(ic='P'+str(2*i), cap='P'+str(2*i+1),
                                                  limit=8*MM)['obligations']
            if clustered:  # P28 also feeds P31: one joint cluster with two movable members
                doc['obligations'] = [o for o in doc['obligations'] if o['id'] != 'P31']
                doc['obligations'].append(dict(declaration(ic='P28', cap='P31', limit=8*MM)['obligations'][0],
                                               id='P31-from-P28'))
            policy = request(fixed_refs=[f['ref'] for f in footprints if f['ref'] not in movable],
                             electrical_locality=doc)
            Counting.iterations = 0
            with self.subTest(clustered=clustered), patch.object(placer._Search, '__init__', counted), \
                    patch.object(locality, 'resolve', wraps=locality.resolve) as resolver:
                result = (placer._pack(b, policy, heartbeat=None, joint=True) if clustered else pack(b, policy))
                self.assertEqual(result['status'], 'planned', result)
                self.assertEqual(bool(result['search'].get('locality_clusters')), clustered)
                self.assertEqual(result['locality_grading']['status'], 'pass')
                self.assertLess(resolver.call_count, 400)
                # Placed-state scans scale with exact tests and scans, never with ranked candidates.
                self.assertLessEqual(Counting.iterations, 4*result['steps_used'] + 64)
