"""Strict prepared-job contracts over authored data; no native execution claims."""
from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import unittest

from agent_ee import placement_job, placement_contract

MM = 1_000_000


def fixture():
    footprints = []
    for index, (ref, x) in enumerate((('U1', 2), ('C1', 4), ('TP1', 8))):
        footprints.append(dict(ref=ref, uuid=f'fp-{index}', position_nm=[x * MM, 2 * MM],
            rotation_deg=0, side='F', locked=False,
            courtyards={'F': {'status': 'proven', 'polygons': [[[x*MM-MM//2, MM],
                [x*MM+MM//2, MM], [x*MM+MM//2, 3*MM], [x*MM-MM//2, 3*MM]]]}},
            pads=[dict(number=str(i+1), uuid=f'pad-{index}-{i}', net=net,
                       position_nm=[x*MM, (2+i)*MM], copper_centroid_offset_nm=[[0, 1], [0, 1]])
                  for i, net in enumerate(('VCC', 'GND'))] if ref != 'TP1' else []))
    capture = dict(schema='agent-ee.placement-probe/1', board_sha256='a'*64, footprints=footprints)
    quote = 'Authored engineering maximum: 3 mm'
    citation = dict(kind='captured_source', path='limits.txt', locator='line 1', quote=quote,
                    sha256=hashlib.sha256(quote.encode()).hexdigest())
    def pair(pad, net):
        return {'from': dict(reference='U1', pad=pad, net=net),
                'to': dict(reference='C1', pad=pad, net=net), 'max_distance_nm': 3*MM,
                'citation': quote}
    supply, returned = pair('1', 'VCC'), pair('2', 'GND')
    for p in (supply, returned):
        p['provenance'] = {'class': 'engineering', 'basis': 'authored fixture', 'citation': citation}
    relationships = dict(schema='agent-ee.electrical-locality/1', source_records={'limits.txt': quote},
                         obligations=[dict(id='support', metric='pad-centre-to-centre',
                                           supply=supply, **{'return': returned})])
    contract = dict(schema='agent-ee.block-contract/1',
        allocation=dict(schema='agent-ee.block-allocation/2', board_sha256='a'*64,
            blocks=[dict(id='device', references=['U1', 'C1'], anchor={'member': 'U1'}, interfaces_to_fixed=None)],
            fixed=[], shared=[dict(reference='TP1', role='test point')]),
        eligibility=dict(schema='fome-mc.side-eligibility/1', sources={'board_sha256':'a'*64},
            default={'faces':['F']}, blocks={'device': {'eligible_faces':['F']}},
            shared={'TP1': {'eligible_faces':['F']}}),
        relationships=relationships, templates=[], thickness_nm=1600000)
    normalized = placement_job.normalize_intent({'electrical_locality': relationships},
        {'groups':[dict(name='device', anchor='U1', refs=['U1', 'C1'])]})
    records = [dict(uuid=fp['uuid'], native_ref=fp['ref'], role='movable') for fp in footprints]
    return capture, contract, normalized, records


class PreparedBlockContractTests(unittest.TestCase):
    def test_complete_allocation_is_bound_and_omitted_shared_member_refuses(self):
        capture, contract, normalized, records = fixture()
        binding = placement_contract.bind(capture, contract, normalized, records)
        self.assertEqual(placement_contract.grade(capture, binding)['status'], 'pass')
        missing = deepcopy(contract)
        missing['allocation']['shared'] = []
        with self.assertRaisesRegex(placement_job.JobRefusal, 'unallocated native footprints: TP1'):
            placement_contract.bind(capture, missing, normalized, records)

    def test_stale_source_or_divergent_effective_intent_refuses_binding(self):
        capture, contract, normalized, records = fixture()
        for change, message in (('source', 'original board'), ('policy', 'relationships differ'),
                                ('group', 'bridge membership differs'), ('anchor', 'anchor differs')):
            altered, intent = deepcopy(contract), deepcopy(normalized)
            if change == 'source':
                altered['allocation']['board_sha256'] = 'b'*64
            elif change == 'policy':
                intent['intent']['electrical_locality']['obligations'][0]['return']['max_distance_nm'] += 1
            elif change == 'group':
                intent['groups']['groups'][0]['refs'].remove('C1')
            else:
                intent['groups']['groups'][0]['anchor'] = 'C1'
            with self.subTest(change=change), self.assertRaisesRegex(placement_job.JobRefusal, message):
                placement_contract.bind(capture, altered, intent, records)

    def test_saved_contract_result_controls_incumbent_without_claiming_native_acceptance(self):
        # Authored state records exercise promotion only; no DRC result is fabricated.
        capture, contract, normalized, records = fixture()
        binding = placement_contract.bind(capture, contract, normalized, records)
        before = deepcopy((capture, contract, normalized, records))
        legal = placement_contract.grade(capture, binding)
        moved = deepcopy(capture)
        moved['board_sha256'] = 'b'*64
        support = moved['footprints'][1]
        support['position_nm'][0] += 10*MM
        for pad in support['pads']:
            pad['position_nm'][0] += 10*MM
        for polygon in support['courtyards']['F']['polygons']:
            for point in polygon:
                point[0] += 10*MM
        defect = placement_contract.grade(moved, binding)
        self.assertEqual(defect['board_sha256'], 'b'*64)
        self.assertEqual(defect['original_board_sha256'], 'a'*64)
        self.assertEqual([(r['role'], r['distance_squared_nm2']) for r in defect['relationships']],
                         [('supply', 144*MM**2), ('return', 144*MM**2)])
        self.assertEqual(defect['status'], 'defect')
        qualitative = deepcopy(contract)
        qualitative['relationships'] = {'schema': 'agent-ee.electrical-locality/1', 'obligations': []}
        uncovered = deepcopy(normalized)
        del uncovered['intent']['electrical_locality']
        pending = placement_contract.grade(capture, placement_contract.bind(capture, qualitative, uncovered, records))
        self.assertEqual(pending['status'], 'unknown')
        self.assertTrue(any(r['kind'] == 'block_pin_relationships_missing' for r in pending['unknown']))
        entry = dict(status='checked', integrity='preserved', placement_introduced_errors=0,
                     engine_feasible=True, engine_violations=[], edge_intent={'status':'pass'},
                     engine_cost='10', block_contract=legal)
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(placement_job.promote(tmp, entry))
            pointer = Path(tmp, 'incumbent.json').read_bytes()
            for failed in (defect, pending):
                self.assertFalse(placement_job.promote(tmp, dict(entry, engine_cost='0', block_contract=failed)))
                self.assertEqual(Path(tmp, 'incumbent.json').read_bytes(), pointer)
        self.assertEqual((capture, contract, normalized, records), before)

    def test_changed_binding_or_request_refuses_before_a_native_read(self):
        capture, contract, normalized, records = fixture()
        binding = placement_contract.bind(capture, contract, normalized, records)
        binding['contract']['thickness_nm'] += 1
        with self.assertRaisesRegex(placement_job.JobRefusal, 'binding changed after capture'):
            placement_contract.grade(capture, binding)
        requested = dict(normalized, block_contract=contract)
        for binding_value in (None, binding):
            with self.subTest(bound=binding_value is not None):
                if binding_value is not None:
                    requested['block_contract_binding'] = binding_value
                with self.assertRaisesRegex(placement_job.JobRefusal, 'matching original capture binding'):
                    placement_job.check_block_contract('/not-a-board', requested)
        self.assertEqual(placement_job.check_block_contract('/not-a-board', normalized), {'status':'not_declared'})

    def test_explicit_uuid_roles_preserve_repeated_marks_and_empty_graphic_labels(self):
        capture, contract, normalized, records = fixture()
        for i, (label, role) in enumerate((('FID', 'fixed_copper_mark'), ('FID', 'fixed_copper_mark'),
                                         ('', 'silk_only'))):
            fp = deepcopy(capture['footprints'][2])
            fp.update(uuid=f'extra-{i}', ref=label, position_nm=[(10+i)*MM, 2*MM])
            capture['footprints'].append(fp)
            records.append(dict(uuid=fp['uuid'], native_ref=label, role=role))
            ref = 'uuid:' + fp['uuid']
            contract['allocation']['fixed'].append({'reference':ref})
            contract['eligibility'].setdefault('fixed', {})[ref] = {'eligible_faces':['F']}
        before = deepcopy(capture)
        binding = placement_contract.bind(capture, contract, normalized, records)
        report = placement_contract.grade(capture, binding)
        self.assertEqual(report['status'], 'pass')
        self.assertEqual(report['reference_aliases'], {'extra-0':'uuid:extra-0', 'extra-1':'uuid:extra-1',
                                                     'extra-2':'uuid:extra-2'})
        self.assertEqual(capture, before)
        for kind in ('pose', 'reference'):
            changed = deepcopy(capture)
            if kind == 'pose':
                changed['footprints'][3]['position_nm'][0] += MM
            else:
                changed['footprints'][3]['ref'] = 'other-mark'
            result = placement_contract.grade(changed, binding)
            self.assertEqual(result['status'], 'defect')
            self.assertTrue(any(d['kind'] == ('fixed_pose_changed' if kind == 'pose'
                                            else 'saved_native_reference_changed') for d in result['defects']))

    def test_duplicate_circuit_references_are_not_promoted_to_mark_aliases(self):
        capture, contract, normalized, records = fixture()
        capture['footprints'][2]['ref'] = 'U1'
        with self.assertRaisesRegex(placement_job.JobRefusal, 'ambiguous footprint identity'):
            placement_contract.bind(capture, contract, normalized, records)

    def test_build_admits_a_captured_contract_and_refuses_unimplemented_package_bridge(self):
        from agent_ee import build_plan, build_stages
        plan = dict(schema=build_plan.SCHEMA, source='inputs', envelope='envelope.json',
            requirements=dict(map='map.json', record='record.json', spec='spec.md'),
            parts=dict(manifest='parts.json', circuit='circuit.json', layout='layout.json'),
            placement=dict(intent='intent.json', groups='groups.json', budget_s=10, search={}, router={},
                           block_contract='strict-contract.json'))
        self.assertEqual(build_plan.validate_plan(plan)['placement']['block_contract'], 'strict-contract.json')
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'requires final artwork revalidation'):
                build_stages._package(plan, Path(tmp), Path(tmp), None, None)
            self.assertFalse(Path(tmp, 'accepted-package.json').exists())
        plan['placement']['block_contract'] = '../not-captured.json'
        with self.assertRaises(ValueError):
            build_plan.validate_plan(plan)
