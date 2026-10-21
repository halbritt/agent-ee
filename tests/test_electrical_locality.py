"""Owned pad identities, coupled limits, pack guidance and captured native checks."""
import copy
import json
import unittest

from agent_ee import electrical_locality as locality
from agent_ee.placer import pack, transform
from agent_ee.placement_check import parameters, predicate_names
from test_placement import capture, part, MM
from test_placer import request


def endpoint(ref, pad, net):
    return dict(reference=ref, pad=pad, net=net)


def declaration(ic='U1', cap='C1', supply='VCC', ground='GND', limit=5*MM):
    def pair(a, b, net):
        return {'from': endpoint(ic, a, net), 'to': endpoint(cap, b, net),
                'max_distance_nm': limit, 'citation': 'Owned fixture requirement EE322: declared test limit.'}
    return dict(schema=locality.SCHEMA, obligations=[dict(id=cap, metric='pad-centre-to-centre',
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
                         max_distance_nm=20*MM, citation='Owned output-loop switch limit.')
    return facts, doc


def bound_output_loop():
    facts, doc = output_loop()
    doc['obligations'][0].update(kind='buck-output-loop', ic='U1', inductor='L1', return_net='GND')
    return facts, doc


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
                max_distance_nm=7*MM, citation='Owned counterexample fixture: switch centres within 7 mm.')
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
        for expected, limit in (('pass', 100*MM), ('defect', 0), ('unknown', None)):
            source = helper.source()
            facts = extract(source / 'project/rc_filter.kicad_pcb')
            fps = {f['ref']: f for f in facts['footprints']}
            shared = [(a, b) for a in fps['J1']['pads'] for b in fps['C1']['pads'] if a['net'] and a['net']==b['net']]
            a, b = shared[0]
            pair = {'from': endpoint('J1', a['number'], a['net']), 'to': endpoint('C1', b['number'], b['net']),
                    'citation': 'Owned RC diagnostic distance requirement.'}
            if limit is not None: pair['max_distance_nm'] = limit
            cap_ground = next(p for p in fps['C1']['pads'] if p['net'] != a['net'])
            header_ground = next(p for p in fps['J1']['pads'] if p['net'] == cap_ground['net'])
            ground = {**pair, 'from': endpoint('J1', header_ground['number'], header_ground['net']),
                      'to': endpoint('C1', cap_ground['number'], cap_ground['net'])}
            doc = dict(schema=locality.SCHEMA, obligations=[dict(id='RC', metric='pad-centre-to-centre',
                       supply=pair, **{'return': ground})])
            (source / 'project/locality.json').write_text(json.dumps(doc))
            spec = placement('locality', locality.CRITERION, locality='project/locality.json')
            run, results = helper.run_scope(source, [DRC_STAGE, spec, DRC_FULL])
            result = results['locality']
            self.assertEqual(result['outcome'], expected, result)
            self.assertEqual(result['execution'], 'unsupported' if expected=='unknown' else 'ok', result)
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
                        max_distance_nm=100*MM, citation='Owned native three-reference loop requirement.')
        doc = dict(schema=locality.SCHEMA, obligations=[dict(id='output-loop', metric='pad-centre-to-centre',
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
                'max_distance_nm': 5*MM, 'citation': 'Owned native geometry fixture: 5 mm diagnostic limit.'}
        doc = dict(schema=locality.SCHEMA, obligations=[dict(id='native', metric='pad-centre-to-centre',
                   supply=pair, **{'return': {**pair, 'from': endpoint('U1', '2', 'GND'),
                                                      'to': endpoint('C1', '2', 'GND')}})])
        helper.config['policy']['electrical_locality'] = doc
        result = place_candidate(helper.source, helper.netlist, helper.scratch / 'guided', helper.config)
        self.assertEqual(result['status'], 'placed', result)
        self.assertEqual(result['judgment']['locality_grading']['status'], 'pass')
        self.assertEqual(locality.grade(extract(helper.scratch/'guided/candidate.kicad_pcb'), doc)['status'], 'pass')
