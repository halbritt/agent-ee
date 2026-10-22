"""AGENTEE-337: numeric authority, soft legacy guidance and manufacturing floors."""
import copy
import hashlib
import unittest

from agent_ee import electrical_locality as locality, locality_provenance
from agent_ee.placer import pack, transform
from test_electrical_locality import board, declaration, MM
from test_placer import request
from owner_fixture import committed


def source(text='Original numeric requirement: 1 mm'):
    return {'path': 'requirements/owned-numeric.txt',
            'sha256': hashlib.sha256(text.encode()).hexdigest(),
            'locator': 'numeric distance row', 'quote': text}


def numeric(pair):
    pair['provenance'] = {'class': 'datasheet', 'basis': 'Original synthetic numeric source',
        'derivation': {'kind': 'numeric', 'value_nm': pair['max_distance_nm'], 'source': source()}}


class LocalityProvenanceTests(unittest.TestCase):
    def test_legacy_and_agent_owner_label_are_advisory_even_when_exceeded(self):
        for provenance in (None, {'class': 'owner', 'basis': 'agent selected design-owner D-PL-1'},
                           {'class': 'owner', 'basis': 'agent self-assertion',
                            'decision_reference': {**source(), 'owner': 'agent-499'}},
                           {'class': 'owner', 'basis': 'design-owner self-assertion',
                            'decision_reference': {**source(), 'owner': 'design-owner'}},
                           {'class': 'default', 'basis': 'default'},
                           {'class': 'engineering', 'basis': 'agent selected'}):
            facts, doc = board(), declaration(limit=MM)
            doc['source_records'] = {**doc['source_records'], 'requirements/owned-numeric.txt': 'Original numeric requirement: 1 mm'}
            for role in ('supply', 'return'):
                pair = doc['obligations'][0][role]
                pair.pop('provenance', None)
                if provenance: pair['provenance'] = provenance
            result = locality.grade(facts, doc)
            self.assertEqual(result['status'], 'info', result)
            self.assertFalse(result['defects'])
            self.assertEqual(len(result['advisories']), 2)
            self.assertTrue(all(a['kind'] == 'unsourced' and a['severity'] == 'defect'
                                for a in result['advisories']))
            self.assertFalse(locality.constraint(facts['footprints'], facts['footprints'][1], doc)[0])
            index = locality.PlacementIndex(doc)
            index.push(facts['footprints'][0])
            candidate = index.prepare(transform(facts['footprints'][1], [0, 0], 0))
            self.assertFalse(candidate.constraint([100*MM, 100*MM])[0])
            self.assertEqual(candidate.targets(), [])
            plan = pack(facts, request(fixed_refs=['U1', 'C1'], electrical_locality=doc))
            self.assertEqual(plan['status'], 'planned', plan)
            self.assertEqual(plan['locality_grading']['status'], 'info')

    def test_owner_label_variants_stay_advisory(self):
        facts, doc = board(), declaration(limit=MM)
        doc['source_records'] = {**doc['source_records'], source()['path']: source()['quote']}
        for owner in ('Design Owner', 'DESIGN_OWNER', 'designowner', 'design-owner'):
            with self.subTest(owner=owner):
                for role in ('supply', 'return'):
                    doc['obligations'][0][role]['provenance'] = {
                        'class': 'owner', 'basis': 'self assertion',
                        'decision_reference': {**source(), 'owner': owner}}
                self.assertEqual(locality.grade(facts, doc)['status'], 'info')

    def test_uncited_advisory_plans_but_missing_maximum_stays_unknown(self):
        facts, doc = board(), declaration(limit=0)
        for role in ('supply', 'return'):
            pair = doc['obligations'][0][role]
            pair.pop('provenance')
            pair.pop('citation')
        result = locality.grade(facts, doc)
        self.assertEqual(result['status'], 'info')
        self.assertTrue(all(a['exceeded'] for a in result['advisories']))
        self.assertEqual(pack(facts, request(electrical_locality=doc))['status'], 'planned')
        doc['obligations'][0]['supply'].pop('max_distance_nm')
        result = locality.grade(facts, doc)
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(len(result['unknown']), 1)

    def test_geometry_unknown_manufacturer_is_advisory(self):
        facts, doc = board(), declaration(limit=0)
        for fp in facts['footprints']:
            for pad in fp['pads']:
                pad['shape'] = 3  # Trapezoid centroid known; copper floor unsupported.
        for role in ('supply', 'return'):
            doc['obligations'][0][role]['provenance'] = {
                'class': 'manufacturer', 'basis': 'agent manufacturing label'}
        result = locality.grade(facts, doc)
        self.assertEqual(result['status'], 'info')
        self.assertTrue(all(a['manufacturing_floor']['status'] == 'unknown'
                            and a['exceeded'] and not a['below_floor'] for a in result['advisories']))
        self.assertFalse(locality.constraint(facts['footprints'], facts['footprints'][1], doc)[0])

    def test_sourced_infeasibility_and_advisory_below_floor(self):
        """AGENTEE-348 ruling (b): an uncommitted owner decision is advisory like an unsourced bound.

        Only a decision cited at a resolving commit proves infeasibility, so the hard owner case
        now carries a committed citation and the former uncommitted owner case is advisory.
        """
        for kind in ('owner', 'uncommitted owner', 'datasheet', 'default'):
            with self.subTest(kind=kind):
                facts, doc = board(), declaration(limit=1000)
                facts['footprints'][0]['pads'][0]['size_nm'] = [1500000, 1500000]
                facts['footprints'][1]['pads'][1]['size_nm'] = [1500000, 1500000]
                text = 'Original numeric requirement: 1000 nm'
                doc['source_records'] = {**doc['source_records'], source(text)['path']: text}
                for role in ('supply', 'return'):
                    pair = doc['obligations'][0][role]
                    if kind in ('owner', 'uncommitted owner'):
                        decision = {**source(text), 'owner': 'fixture author'}
                        if kind == 'owner':
                            decision.update(committed(decision['path'], text))
                        pair['provenance'] = {'class': 'owner', 'basis': 'fixture requirement',
                                              'decision_reference': decision}
                    elif kind == 'datasheet':
                        pair['provenance'] = {'class': 'datasheet', 'basis': 'fixture number',
                            'derivation': {'kind': 'numeric', 'value_nm': 1000, 'source': source(text)}}
                    else:
                        pair.pop('provenance')
                result = locality.grade(facts, doc)
                self.assertTrue(all(m['manufacturing_floor']['floor_nm'] == 650000
                                    for m in result['measured']))
                if kind in ('default', 'uncommitted owner'):
                    self.assertEqual(result['status'], 'info')
                    self.assertTrue(all(a['below_floor'] for a in result['advisories']))
                else:
                    self.assertEqual([d['kind'] for d in result['defects']], ['locality_infeasible'] * 2)
                    refusal = pack(facts, request(electrical_locality=doc))
                    self.assertEqual((refusal['status'], refusal['outcome']), ('locality_infeasible', 'defect'))

    def test_numeric_datasheet_and_owner_decision_block(self):
        """AGENTEE-348 ruling (b): an owner decision blocks only when cited at a resolving commit.

        The former uncommitted fixture-author decision is now advisory with its reason recorded;
        the committed citation of the same captured text keeps the two hard defects.
        """
        for kind in ('datasheet', 'owner', 'uncommitted owner'):
            facts, doc = board(), declaration(limit=MM)
            doc['source_records'] = {**doc['source_records'], 'requirements/owned-numeric.txt': 'Original numeric requirement: 1 mm'}
            for role in ('supply', 'return'):
                pair = doc['obligations'][0][role]
                numeric(pair)
                if kind != 'datasheet':
                    decision = {**source(), 'owner': 'fixture author'}
                    if kind == 'owner':
                        decision.update(committed(decision['path'], decision['quote']))
                    pair['provenance'] = {'class': 'owner', 'basis': 'Owned fixture requirement',
                                          'decision_reference': decision}
            result = locality.grade(facts, doc)
            if kind == 'uncommitted owner':
                self.assertEqual(result['status'], 'info', result)
                self.assertFalse(result['defects'])
                self.assertEqual([a['constraint_provenance']['reason'] for a in result['advisories']],
                                 ['owner decision is not cited at a commit (uncommitted)'] * 2)
                self.assertFalse(locality.constraint(facts['footprints'], facts['footprints'][1], doc)[0])
                continue
            self.assertEqual(result['status'], 'defect')
            self.assertEqual(len(result['defects']), 2)
            self.assertTrue(locality.constraint(facts['footprints'], facts['footprints'][1], doc)[0])

    def test_numeric_mismatch_prose_only_and_bad_references_stay_unsourced(self):
        facts, doc = board(), declaration(limit=MM)
        doc['source_records'] = {**doc['source_records'], 'requirements/owned-numeric.txt': 'Original numeric requirement: 1 mm'}
        for mutation in ('value', 'locator', 'hash', 'kind', 'class', 'bytes', 'missing_source', 'quote', 'malformed_class'):
            changed = copy.deepcopy(doc)
            for role in ('supply', 'return'):
                pair = changed['obligations'][0][role]
                numeric(pair)
                p = pair['provenance']
                if mutation == 'value': p['derivation']['value_nm'] += 1
                if mutation == 'locator': p['derivation']['source']['locator'] = ''
                if mutation == 'hash': p['derivation']['source']['sha256'] = 'not-a-hash'
                if mutation == 'kind': p['derivation'] = {'kind': 'prose', 'text': 'as close as possible'}
                if mutation == 'class': p['class'] = 'design-owner'
                if mutation == 'malformed_class': p['class'] = ['owner']
                if mutation == 'bytes': changed['source_records']['requirements/owned-numeric.txt'] = 'changed'
                if mutation == 'missing_source': changed['source_records'].pop('requirements/owned-numeric.txt', None)
                if mutation == 'quote': p['derivation']['source']['quote'] = 'unsupported 1 mm quote'
            result = locality.grade(facts, changed)
            if mutation == 'bytes':  # Coordinator ruling 402a: fail closed on present corrupt evidence.
                self.assertEqual(result['status'], 'unknown', (mutation, result))
                self.assertEqual([u['kind'] for u in result['unknown']], ['source_record_integrity'] * 2)
                self.assertEqual(result['advisories'], [])
            else:
                self.assertEqual(result['status'], 'info', (mutation, result))
                self.assertTrue(all(a['kind'] == 'unsourced' for a in result['advisories']))

    def test_integrity_is_typed_and_never_a_hard_constraint(self):
        for kind in ('datasheet', 'owner'):
            for limit in (MM, 100 * MM):
                with self.subTest(kind=kind, limit=limit):
                    facts, doc = board(), declaration(limit=limit)
                    # Leave return clean: its determinate defect must survive supply unknown.
                    pair = doc['obligations'][0]['supply']
                    numeric(pair)
                    if kind == 'owner':
                        pair['provenance'] = {'class': 'owner', 'basis': 'original fixture decision',
                            'decision_reference': {**source(), 'owner': 'fixture author'}}
                    doc['source_records'] = {**doc['source_records'], source()['path']: 'changed'}
                    expected = {'enforcement': 'integrity', 'reason': 'source record sha256 mismatch'}
                    self.assertEqual(locality_provenance.verified_reference(source(), doc['source_records']), expected)
                    self.assertEqual(locality_provenance.classify(pair, doc['source_records']),
                                     {'class': kind, **expected})
                    result = locality.grade(facts, doc)
                    self.assertEqual(result['status'], 'defect' if limit == MM else 'unknown')
                    self.assertEqual(len(result['unknown']), 1)
                    self.assertEqual(result['unknown'][0]['kind'], 'source_record_integrity')
                    self.assertEqual(result['unknown'][0]['reason'], expected['reason'])
                    self.assertEqual(result['advisories'], [])
                    supply_only = copy.deepcopy(doc)
                    supply_only['obligations'][0]['return']['provenance'] = copy.deepcopy(pair['provenance'])
                    self.assertFalse(locality.constraint(facts['footprints'], facts['footprints'][1], supply_only)[0])
                    self.assertEqual(locality.targets(facts['footprints'][:1], facts['footprints'][1], supply_only), [])
                    index = locality.PlacementIndex(supply_only)
                    index.push(facts['footprints'][0])
                    candidate = index.prepare(transform(facts['footprints'][1], [0, 0], 0))
                    self.assertFalse(candidate.constraint([100 * MM, 100 * MM])[0])
                    self.assertEqual(candidate.targets(), [])
                    refusal = pack(facts, request(electrical_locality=supply_only))
                    self.assertEqual((refusal['status'], refusal['outcome']),
                                     ('electrical_locality_unavailable', 'unknown'))

    def test_scaled_layout_derivation_recomputes_distance(self):
        facts, doc = board(), declaration(limit=MM)
        doc['source_records'] = {**doc['source_records'], 'requirements/owned-numeric.txt': 'Original numeric requirement: 1 mm'}
        for role in ('supply', 'return'):
            pair = doc['obligations'][0][role]
            pair['provenance'] = {'class': 'datasheet', 'basis': 'Original reference-layout measurement',
                'derivation': {'kind': 'layout', 'distance_units': 10, 'scale_nm_per_unit': 100000,
                               'scale_reference': '1 mm scale bar measures 10 units', 'source': source()}}
        self.assertEqual(locality.grade(facts, doc)['status'], 'defect')
        doc['obligations'][0]['supply']['provenance']['derivation']['scale_nm_per_unit'] = 1
        result = locality.grade(facts, doc)
        self.assertEqual(result['status'], 'defect')  # Return defect survives advisory supply.
        self.assertEqual(len(result['advisories']), 1)

    def test_manufacturing_floor_is_computed_and_names_infeasible_pair(self):
        facts, doc = board(), declaration(limit=149999)
        facts['footprints'][0]['pads'][0]['size_nm'] = [1000000, 1000000]
        facts['footprints'][1]['pads'][1]['size_nm'] = [1000000, 1000000]
        facts['footprints'][0]['fpid'] = 'Owned:SOIC-8_Original'
        facts['footprints'][1]['fpid'] = 'Owned:C_0603_Original'
        for role in ('supply', 'return'):
            doc['obligations'][0][role]['provenance'] = {'class': 'manufacturer', 'basis': 'JLCPCB spacing'}
        result = locality.grade(facts, doc)
        self.assertEqual(result['status'], 'defect', result)
        self.assertEqual([d['kind'] for d in result['defects']], ['locality_infeasible']*2)
        self.assertEqual(result['defects'][0]['manufacturing_floor']['floor_nm'], 150000)
        self.assertEqual(result['defects'][0]['refs'], ['C1', 'U1'])
        refusal = pack(facts, request(electrical_locality=doc))
        self.assertEqual((refusal['status'], refusal['outcome'], refusal['placements']),
                         ('locality_infeasible', 'defect', []))
        estimate = result['measured'][0]['manufacturing_floor']['assembly']
        self.assertEqual(estimate['status'], 'unknown')
        self.assertIn('pad_envelope_estimate_nm', estimate)
        # Geometry, never a supplied floor, changes the computed result.
        facts['footprints'][1]['pads'][1]['size_nm'] = [1200000, 1200000]
        self.assertEqual(locality.grade(facts, doc)['measured'][0]['manufacturing_floor']['floor_nm'], 250000)

    def test_missing_or_tampered_captured_manufacturer_rules_remain_unknown(self):
        from pathlib import Path
        import tempfile
        from unittest.mock import patch
        from agent_ee import locality_provenance
        facts, doc = board(), declaration(limit=MM)
        for role in ('supply', 'return'):
            doc['obligations'][0][role]['provenance'] = {'class': 'manufacturer', 'basis': 'Captured PCB clearance'}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for content in (None, b'{}'):
                if content is not None:
                    (root/'data').mkdir()
                    (root/'data/jlcpcb-locality.json').write_bytes(content)
                # Package resource I/O boundary, using actual absent/tampered files.
                with patch.object(locality_provenance, 'files', return_value=root):
                    result = locality.grade(facts, doc)
                self.assertEqual(result['status'], 'unknown', result)
                self.assertEqual(len(result['unknown']), 2)
                self.assertFalse(result['defects'])

    def test_copper_floor_is_rotation_invariant_and_ignores_mask_only_overlap(self):
        facts, doc = board(), declaration(limit=149999)
        facts['footprints'][0]['pads'][0]['size_nm'] = [1000000, 1000000]
        facts['footprints'][1]['pads'][1]['size_nm'] = [1000000, 1000000]
        pair = doc['obligations'][0]['supply']
        pair['provenance'] = {'class': 'manufacturer', 'basis': 'Captured PCB clearance'}
        for rotation in (0, 90, 180, 270):
            changed = copy.deepcopy(facts)
            changed['footprints'][1] = transform(changed['footprints'][1], [10*MM, 10*MM], rotation)
            result = locality.grade(changed, doc)
            self.assertEqual(result['measured'][0]['manufacturing_floor']['floor_nm'], 150000)
        for fp, layer in zip(facts['footprints'], ('F.Cu', 'B.Cu')):
            for pad in fp['pads']:
                pad['layers'] = [layer, 'F.Mask']
        result = locality.grade(facts, doc)
        self.assertEqual(result['measured'][0]['manufacturing_floor']['floor_nm'], 0)
        self.assertFalse(any(d['kind'] == 'locality_infeasible' for d in result['defects']))

    def test_missing_package_geometry_and_other_side_are_unknown_floors(self):
        facts, doc = board(), declaration(limit=MM)
        doc['source_records'] = {**doc['source_records'], 'requirements/owned-numeric.txt': 'Original numeric requirement: 1 mm'}
        for role in ('supply', 'return'): doc['obligations'][0][role].pop('provenance')
        for fp in facts['footprints']: fp['fpid'] = 'Owned:Unsupported'
        result = locality.grade(facts, doc)
        self.assertTrue(all(m['manufacturing_floor']['assembly']['status'] == 'unknown' for m in result['measured']))
        opposite = copy.deepcopy(facts)
        opposite['footprints'][1]['side'] = 'B'
        self.assertEqual(locality.grade(opposite, doc)['measured'][0]['manufacturing_floor']['status'], 'unknown')
        # Geometry identity failures retain existing unknown coverage, even for advisories.
        facts['footprints'][1]['pads'][0]['net'] = 'OTHER'
        self.assertEqual(locality.grade(facts, doc)['status'], 'unknown')


class AdvisoryPublicationTests(unittest.TestCase):
    def test_targeted_plans_admit_exceeded_unsourced_bounds(self):
        from agent_ee.placement_plans import plan
        from test_placement_plans import scoring, row
        facts, doc = board(), declaration(limit=0)
        for role in ('supply', 'return'):
            doc['obligations'][0][role].pop('provenance')
        policy = scoring()
        policy['capacitors'].pop('C2')
        result = plan(facts, request(electrical_locality=doc),
                      row(capacitor_refs=['C1'], judge_policy=policy))
        self.assertEqual(result['status'], 'planned', result)
        self.assertEqual(result['locality_grading']['status'], 'info')
