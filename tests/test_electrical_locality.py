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
                          shape=1, attribute='smd', layers=['F.Cu'], rotation_deg=0)
                     for i, net in ((1, 'VCC'), (2, 'GND'))]
    return b


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
            b['footprints'][0]['pads'].append(dict(number='8', net='SW', position_nm=[0,0]))
            inductor = dict(ref=ind,pads=[dict(number='1', net='SW', position_nm=[before,0])])
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
