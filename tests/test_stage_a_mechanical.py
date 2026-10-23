"""Actual native Stage-A packing must reserve attached intra-block space.

All board geometry, attachment extents and electrical bounds are authored test
inputs. Native reproduction and saved-board qualification remain explicit.
"""
import copy
import os
from pathlib import Path
import tempfile
import unittest

from agent_ee import block_contract, block_variants, blocks, electrical_locality
from agent_ee import mechanical_reservations, placement, placement_groups, placement_rust_runtime, placer_runner
from agent_ee.native_netlist import read_native_inventory, read_sheet_names
from agent_ee.placement_probe import extract
from scripts.placement_engine_parity import first_difference, normalized
from test_mechanical_reservations import bound, raw, reservation, sha
from test_placement import MM, rectangle
from test_placement_engine_parity import EngineFixture
from toolchain import require_toolchain
import underside_fixture as fixture


def candidate_capture(capture, candidate):
    """Reconstruct the admitted frame for independent geometry grading only."""
    originals = {fp['ref']: fp for fp in capture['footprints']}
    framed = [blocks.frame_member(originals[row['reference']], row['pose']['position_nm'],
                                  row['pose']['rotation_deg']) for row in candidate['members']]
    return {**copy.deepcopy(capture), 'footprints': framed,
            'outline': {'status': 'proven', 'polygons': [rectangle(-10, -10, 20, 20)]}}


class StageAMechanicalNativeTest(EngineFixture, unittest.TestCase):
    def root(self):
        retained = os.environ.get('AGENT_EE_493_TEST_OUTPUT')
        if retained:
            root = Path(retained).resolve() / self._testMethodName
            root.mkdir(parents=True, exist_ok=False)
            return root
        temporary = tempfile.TemporaryDirectory(prefix='agent-ee-stage-a-mechanical-')
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name)

    def inputs(self, root, *, impossible=False):
        source, netlist = fixture.write_board(root / 'inputs')
        facts = extract(source)
        inventory = read_native_inventory(netlist)
        allocation = fixture.allocation_text()
        derivation = blocks.derive(inventory, read_sheet_names(netlist), facts,
            inventory_sha256=sha(netlist.read_bytes()), allocation_raw=allocation.encode())
        policy = fixture.policy()
        baseline = blocks._plain(blocks.pack_block(facts, inventory, derivation, 'b', policy, compact=False))
        self.assertEqual(baseline['status'], 'packed', baseline)
        owner = next(fp for fp in facts['footprints'] if fp['ref'] == 'U1')
        # Positive: reserve the left approach while right/above remain available.
        # Negative: both pad bounds imply the average pad (footprint centre)
        # separation is <=3 mm. A centred 8x8-mm reservation therefore intersects
        # every same-face 2x2-mm C1 courtyard satisfying those unchanged bounds.
        polygon = rectangle(-4, -4, 8, 8) if impossible else rectangle(-4, -1.5, 3, 3)
        row = reservation('device-service-space', frame='footprint',
            owner={'ref': owner['ref'], 'uuid': owner['uuid']}, polygons=[polygon], self_exclusion=True)
        policy['mechanical_reservations'] = bound(facts, [row])
        eligibility = fixture.eligibility(facts['board_sha256'])
        for name, value in (('facts', facts), ('policy', policy), ('baseline-block', baseline)):
            (root / f'{name}.json').write_bytes(raw(value))
        return source, netlist, facts, inventory, derivation, allocation, policy, eligibility, baseline['candidate']

    def request(self, facts, allocation, policy, eligibility, candidates):
        return {'policy': policy, 'blocks': {'arrangement': fixture.arrangement_policy(),
            'allocation_text': allocation, 'side_eligibility': eligibility,
            'contract': fixture.contract(facts['board_sha256'], eligibility, policy),
            'candidates': [{'candidate': bundle} for bundle in candidates]}}

    def test_attached_space_changes_stage_a_then_global_and_native_saved_plan_pass(self):
        require_toolchain(native=True)
        root = self.root()
        source, netlist, facts, inventory, derivation, allocation, policy, eligibility, baseline = self.inputs(root)
        original = source.read_bytes()
        ordinary = candidate_capture(facts, baseline)
        self.assertEqual(placement.judge(ordinary)['verdict'], 'pass')
        self.assertEqual(electrical_locality.grade(ordinary, policy['electrical_locality'])['status'], 'pass')
        rejected = mechanical_reservations.grade(ordinary, policy['mechanical_reservations'])
        (root / 'ordinary-block-mechanical.json').write_bytes(raw(rejected))
        self.assertEqual(rejected['status'], 'defect', rejected)
        self.assertEqual({row['ref'] for row in rejected['defects']}, {'C1'})

        candidates = [block_variants.produce(source, facts, inventory, derivation, block['id'], policy,
            eligibility, root / 'variants' / block['id'], compact=True) for block in derivation['blocks']]
        device = next(bundle for bundle in candidates if bundle['block_id'] == 'b')
        self.assertEqual({face: row['status'] for face, row in device['variants'].items()},
                         {'F': 'packed', 'B': 'packed'})
        baseline_poses = {row['reference']: row['pose'] for row in baseline['members']}
        for face, variant in device['variants'].items():
            with self.subTest(face=face):
                candidate = variant['candidate']
                compaction = candidate['search']['compaction']
                self.assertLessEqual(compaction['box_area_nm2'], compaction['first_box_area_nm2'])
                self.assertTrue(compaction['trials'], compaction)
                framed = candidate_capture(variant['capture'], candidate)
                report = mechanical_reservations.grade(framed, policy['mechanical_reservations'])
                (root / f'{face}-candidate-mechanical.json').write_bytes(raw(report))
                self.assertEqual(report['status'], 'pass', report)
                self.assertEqual(placement.judge(framed)['verdict'], 'pass')
                self.assertEqual(electrical_locality.grade(framed, policy['electrical_locality'])['status'], 'pass')
                if face == 'F':
                    self.assertNotEqual({row['reference']: row['pose'] for row in candidate['members']}, baseline_poses)
        proposed = self.request(facts, allocation, policy, eligibility, candidates)
        (root / 'request.json').write_bytes(raw(proposed))
        expected = placer_runner._plan_local(facts, proposed, netlist=netlist, heartbeat=lambda _: None)
        (root / 'python-plan.json').write_bytes(raw(blocks._plain(expected)))
        self.assertEqual(expected['status'], 'planned', expected)
        self.assertEqual(expected['block_arrangement']['contract_check']['status'], 'pass')
        self.assertTrue(placement_groups.satisfied(expected['group_grading']))
        session = placement_rust_runtime.Session(self.binary)
        actual = session.plan(facts, proposed, None, netlist, heartbeat=lambda _: None)
        (root / 'rust-plan.json').write_bytes(raw(blocks._plain(actual)))
        (root / 'rust-execution.json').write_bytes(raw(session.calls))
        self.assertEqual(session.calls[-1]['status'], 'ok')
        self.assertIsNone(first_difference(normalized(expected), normalized(actual)))
        self.assertEqual(next(row for row in actual['block_arrangement']['arranged'] if row['block'] == 'b')['selected_face'], 'B')

        published = root / 'published'
        published.mkdir()
        approved = {**copy.deepcopy(proposed), 'skip_visuals': True, 'authorization': {
            'schema': 'agent-ee.placement-write/1', 'single_design_writer': True,
            'writable_root': str(published.resolve()), 'source_sha256': facts['board_sha256']}}
        destination = published / 'candidate'
        result = placer_runner.place_candidate(source, netlist, destination, approved)
        (root / 'writer-result.json').write_bytes(raw(result))
        self.assertEqual(result['status'], 'placed', result)
        saved = extract(destination / 'candidate.kicad_pcb')
        (root / 'saved-facts.json').write_bytes(raw(saved))
        self.assertEqual(placement.judge(saved)['verdict'], 'pass')
        mechanical = mechanical_reservations.grade(saved, policy['mechanical_reservations'])
        contract = block_contract.judge(saved, proposed['blocks']['contract'], original_capture=facts)
        (root / 'saved-mechanical.json').write_bytes(raw(mechanical))
        (root / 'saved-contract.json').write_bytes(raw(contract))
        self.assertEqual(mechanical['status'], 'pass', mechanical)
        self.assertEqual(contract['status'], 'pass', contract)
        self.assertTrue(all(not row['exceeded'] for row in contract['relationships']))
        self.assertEqual(source.read_bytes(), original)


    def test_impossible_same_face_attachment_is_retained_and_never_published(self):
        require_toolchain(native=True)
        root = self.root()
        source, netlist, facts, inventory, derivation, allocation, policy, eligibility, baseline = self.inputs(
            root, impossible=True)
        original = source.read_bytes()
        ordinary = candidate_capture(facts, baseline)
        self.assertEqual(placement.judge(ordinary)['verdict'], 'pass')
        self.assertEqual(electrical_locality.grade(ordinary, policy['electrical_locality'])['status'], 'pass')
        self.assertEqual(mechanical_reservations.grade(ordinary, policy['mechanical_reservations'])['status'], 'defect')
        candidates = [block_variants.produce(source, facts, inventory, derivation, block['id'], policy,
            eligibility, root / 'variants' / block['id'], compact=False) for block in derivation['blocks']]
        device = next(bundle for bundle in candidates if bundle['block_id'] == 'b')
        self.assertEqual(set(device['variants']), {'F', 'B'})
        for face, variant in device['variants'].items():
            with self.subTest(face=face):
                self.assertIn(variant['status'], ('packing_search_exhausted', 'packing_budget_exhausted',
                                                'locality_cluster_exhausted'))
                self.assertIsNone(variant['candidate'])
                self.assertIsNotNone(variant['capture'])
                failure = variant['failure']
                self.assertEqual(failure['status'], variant['status'])
                self.assertEqual(failure['outcome'], 'unknown')
                self.assertEqual(failure['plan']['placements'], [])
                evidence = failure['mechanical_reservations']
                self.assertEqual(evidence['status'], 'unresolved')
                self.assertEqual(evidence['reservation_ids'], ['device-service-space'])
                self.assertEqual(evidence['initial_grade']['status'], 'defect')
                self.assertIn('device-service-space', failure['reason'])
        proposed = self.request(facts, allocation, policy, eligibility, candidates)
        (root / 'request.json').write_bytes(raw(proposed))
        expected = placer_runner._plan_local(facts, proposed, netlist=netlist, heartbeat=lambda _: None)
        (root / 'python-plan.json').write_bytes(raw(blocks._plain(expected)))
        self.assertEqual(expected['status'], 'arrangement_refused', expected)
        self.assertEqual(expected['placements'], [])
        session = placement_rust_runtime.Session(self.binary)
        actual = session.plan(facts, proposed, None, netlist, heartbeat=lambda _: None)
        (root / 'rust-plan.json').write_bytes(raw(blocks._plain(actual)))
        (root / 'rust-execution.json').write_bytes(raw(session.calls))
        self.assertEqual(session.calls[-1]['status'], 'ok')
        self.assertIsNone(first_difference(normalized(expected), normalized(actual)))
        published = root / 'published'
        published.mkdir()
        destination = published / 'candidate'
        approved = {**copy.deepcopy(proposed), 'skip_visuals': True, 'authorization': {
            'schema': 'agent-ee.placement-write/1', 'single_design_writer': True,
            'writable_root': str(published.resolve()), 'source_sha256': facts['board_sha256']}}
        result = placer_runner.place_candidate(source, netlist, destination, approved)
        (root / 'writer-result.json').write_bytes(raw(result))
        self.assertEqual(result['status'], 'arrangement_refused', result)
        self.assertFalse(destination.exists())
        self.assertFalse(list(published.rglob('candidate.kicad_pcb')))
        self.assertEqual(source.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
