"""AGENTEE-479: a feasible underside must participate in residual legalization."""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from agent_ee import block_arrangement, block_variants, blocks, electrical_locality, placement, placement_groups
from agent_ee import block_contract, placement_rust_runtime, placer_runner
from agent_ee.native_netlist import read_native_inventory, read_sheet_names
from agent_ee.placement_probe import extract
from toolchain import require_toolchain
from scripts.placement_engine_parity import first_difference, normalized
from test_placement_engine_parity import EngineFixture
import underside_fixture as fixture


class CongestedUndersideTest(EngineFixture, unittest.TestCase):
    def artifact_root(self):
        retained = os.environ.get('AGENT_EE_479_TEST_OUTPUT')
        if retained:
            root = Path(retained).resolve() / self._testMethodName
            root.mkdir(parents=True, exist_ok=False)
            return root
        temporary = tempfile.TemporaryDirectory(prefix='agent-ee-underside-')
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name)

    def test_public_planner_moves_block_after_residual_conflict(self):
        require_toolchain(native=True)
        root = self.artifact_root()
        source, netlist = fixture.write_board(root / 'inputs')
        witness, _ = fixture.write_board(root / 'witness', witness=True)
        facts, witness_facts = extract(source), extract(witness)
        original = source.read_bytes()
        policy, rules = fixture.policy(), fixture.arrangement_policy()

        # Proof by literal interval inequalities, not a search-result inference:
        # R1 has height 2 in a height-2 usable strip, hence y=1.5. Its front
        # neighbours end at x=2.5 and start at x=8, with gap 0.5, so x=5.25.
        # The two other quarter-turns have height 4.5 and cannot enter the strip.
        residual_width, residual_height, gap, margin = 4500000, 2000000, 500000, 500000
        usable_height = 3000000 - 2 * margin
        self.assertEqual(residual_height, usable_height)
        self.assertGreater(residual_width, usable_height)
        left = 2500000 + gap + residual_width // 2
        right = 8000000 - gap - residual_width // 2
        self.assertEqual((left, right, margin + residual_height // 2), (5250000, 5250000, 1500000))
        actual = {fp['ref']: fp for fp in witness_facts['footprints']}
        self.assertEqual({ref: (fp['position_nm'], fp['side']) for ref, fp in actual.items()}, fixture.WITNESS)
        for ref, fp in actual.items():
            self.assertEqual(set(fp['courtyards']), {fp['side']}, ref)
            self.assertEqual([blocks.canonical_ring(ring) for ring in fp['courtyards'][fp['side']]['polygons']],
                             [blocks.canonical_ring(fixture.WITNESS_COURTYARDS[ref])], ref)
        self.assertEqual([blocks.canonical_ring(ring) for ring in witness_facts['outline']['polygons']],
                         [blocks.canonical_ring([[0, 0], [10500000, 0], [10500000, 3000000], [0, 3000000]])])
        self.assertEqual(placement.judge(witness_facts)['verdict'], 'pass')
        self.assertTrue(placement_groups.satisfied(placement_groups.grade(witness_facts, policy['groups'])))
        locality = electrical_locality.grade(witness_facts, policy['electrical_locality'])
        self.assertEqual((locality['status'], len(locality['measured'])), ('pass', 2))
        graded = block_arrangement.grade_rules(witness_facts, rules,
                                               {'J1': 'a', 'U1': 'b', 'C1': 'b'}, {'H1', 'H2'})
        self.assertTrue(all(row['status'] == 'pass' for row in graded))
        native = placer_runner._native_drc(witness, witness.parent)
        self.assertEqual([row for row in native['violations'] if row['type'] in placer_runner.DRC_COURTYARDS], [])

        inventory = read_native_inventory(netlist)
        allocation = fixture.allocation_text()
        derivation = blocks.derive(inventory, read_sheet_names(netlist), facts,
            inventory_sha256=hashlib.sha256(netlist.read_bytes()).hexdigest(), allocation_raw=allocation.encode())
        eligibility = fixture.eligibility(facts['board_sha256'])
        candidates = [block_variants.produce(source, facts, inventory, derivation, block['id'], policy,
                      eligibility, root / 'variants' / block['id'], compact=False)
                      for block in derivation['blocks']]
        self.assertEqual({bundle['block_id']: {face: row['status'] for face, row in bundle['variants'].items()}
                          for bundle in candidates}, {'a': {'F': 'packed'}, 'b': {'F': 'packed', 'B': 'packed'}})
        request = {'policy': policy, 'blocks': {'arrangement': rules, 'side_eligibility': eligibility,
                                               'allocation_text': allocation,
                                               'contract': fixture.contract(facts['board_sha256'], eligibility, policy),
                                               'candidates': [{'candidate': bundle} for bundle in candidates]}}
        for name, document in (('facts', facts), ('request', request), ('witness-facts', witness_facts)):
            (root / f'{name}.json').write_text(json.dumps(blocks._plain(document), indent=2) + '\n')
        try:
            plan = placer_runner._plan_local(facts, request, netlist=netlist, heartbeat=lambda _: None)
        except ValueError as error:
            (root / 'planner-error.json').write_text(json.dumps({'type': 'ValueError', 'message': str(error)}) + '\n')
            raise
        (root / 'plan.json').write_text(json.dumps(blocks._plain(plan), indent=2) + '\n')
        self.assertEqual(plan['status'], 'planned', plan)
        self.assert_plan_parity(facts, request, netlist, plan)
        record = plan['block_arrangement']
        first = next(row for row in record['first_arranged'] if row['block'] == 'b')
        final = next(row for row in record['arranged'] if row['block'] == 'b')
        self.assertEqual((first['selected_face'], final['selected_face']), ('F', 'B'))
        self.assertNotEqual(first, final)
        self.assertTrue(any(row['reason'] == 'residual_packing_failed'
                            and first in row['before'] and final in row['after'] for row in record['adjustments']))
        self.assertEqual({row['ref'] for row in plan['placements']}, set(fixture.WITNESS))
        self.assertEqual(record['contract_check']['status'], 'pass')
        self.assertEqual(plan['steps_used'], record['search']['steps_used'] + record['residual_budget']['steps_used']
                         + record.get('member_refinement', {}).get('steps_used', 0))
        self.saved_board_checks(root, source, netlist, facts, request, candidates, plan)
        self.assertEqual(source.read_bytes(), original)


    def assert_plan_parity(self, facts, request, netlist, expected):
        untouched = copy.deepcopy([facts, request])
        session = placement_rust_runtime.Session(self.binary)
        actual = session.plan(facts, request, None, netlist, heartbeat=lambda _: None)
        root = Path(netlist).parent.parent
        for name, document in (('facts', facts), ('request', request), ('python-plan', expected),
                               ('rust-plan', actual), ('rust-execution', session.calls)):
            (root / f'{name}.json').write_text(json.dumps(blocks._plain(document), indent=2) + '\n')
        self.assertEqual(session.calls[-1]['status'], 'ok')
        self.assertIsNone(first_difference(normalized(expected), normalized(actual)))
        self.assertIsNone(first_difference(untouched, [facts, request]))
        return actual

    def inputs(self, root, *, capacity_proof=False, spare_front=False, underside_reserve=True, mixed=False, chain=False):
        source, netlist = fixture.write_board(root / 'inputs', spare_front=spare_front, chain=chain)
        facts = extract(source)
        policy, rules = (fixture.chain_policy() if chain else fixture.policy()), fixture.arrangement_policy()
        policy['capacity_proof'] = capacity_proof
        if spare_front and underside_reserve:
            # The wider front has two slots; this literal underside reservation
            # leaves B's closest legal slot 5 mm farther from front connector A.
            rules['keepouts'] = [{'id': 'spare-front-underside-reserve', 'side': 'B',
                'polygons': [fixture.rectangle(3, 0.5, 4.5, 2)],
                'provenance': {'enforcement': 'hard', 'citation': fixture.CITATION}}]
        inventory = read_native_inventory(netlist)
        allocation = fixture.allocation_text(chain=chain)
        derivation = blocks.derive(inventory, read_sheet_names(netlist), facts,
            inventory_sha256=hashlib.sha256(netlist.read_bytes()).hexdigest(), allocation_raw=allocation.encode())
        eligibility = fixture.eligibility(facts['board_sha256'])
        mixed_sides = {'F': {'U1': 'F', 'C1': 'B'}, 'B': {'U1': 'B', 'C1': 'F'}}
        if chain:
            for face in mixed_sides:
                mixed_sides[face]['C2'] = face
        candidates = [block_variants.produce(source, facts, inventory, derivation, block['id'], policy,
                      eligibility, root / 'variants' / block['id'], compact=False,
                      member_sides=(mixed_sides if mixed and block['id'] == 'b' else None))
                      for block in derivation['blocks']]
        if chain:
            by_block = {bundle['block_id']: bundle for bundle in candidates}
            self.assertEqual(by_block['a']['variants']['F']['status'], 'packed')
            self.assertEqual(by_block['b']['variants']['F']['status'], 'packed')
            # Both eligible faces were attempted with the same finite Stage-A
            # budget. Preserve the B search limit as a typed retained result;
            # this fixture's full-pipeline refinement uses its packed F layout.
            failed = by_block['b']['variants']['B']
            self.assertEqual(failed['status'], 'packing_budget_exhausted')
            self.assertIsNone(failed['candidate'])
            self.assertEqual(failed['failure']['status'], failed['status'])
            self.assertEqual(failed['failure']['plan']['steps_used'], policy['step_budget'])
        else:
            self.assertTrue(all(row['status'] == 'packed' for bundle in candidates for row in bundle['variants'].values()))
        request = {'policy': policy, 'blocks': {'arrangement': rules, 'side_eligibility': eligibility,
            'allocation_text': allocation, 'contract': fixture.contract(facts['board_sha256'], eligibility, policy, chain=chain),
            'candidates': [{'candidate': bundle} for bundle in candidates]}}
        if mixed:
            request['blocks']['contract']['templates'] = [
                {'relationship': 'chain-support' if chain else 'device-support', 'role': role, 'template': 'decoupler-under-device'}
                for role in ('supply', 'return')]
        return source, netlist, facts, request

    def test_same_input_without_side_choice_exhausts_without_claiming_infeasible(self):
        require_toolchain(native=True)
        root = self.artifact_root()
        _, netlist, facts, request = self.inputs(root)
        original = copy.deepcopy(request)
        request['blocks']['arrangement']['side_choice'] = False
        plan = placer_runner._plan_local(facts, request, netlist=netlist, heartbeat=lambda _: None)
        self.assert_plan_parity(facts, request, netlist, plan)
        self.assertIn(plan['status'], ('arrangement_search_exhausted', 'arrangement_budget_exhausted'))
        self.assertEqual(plan['outcome'], 'unknown')
        record = plan['block_arrangement']
        self.assertTrue(record['residual_failures'])
        self.assertTrue(all(row['selected_face'] == 'F' for failure in record['residual_failures']
                            for row in failure['arranged']))
        self.assertLessEqual(record['search']['steps_used'], fixture.STEP_BUDGET)
        self.assertLessEqual(record['residual_budget']['steps_used'], fixture.STEP_BUDGET)
        self.assertEqual(plan['steps_used'], record['search']['steps_used'] + record['residual_budget']['steps_used']
                         + record.get('member_refinement', {}).get('steps_used', 0))
        del request['blocks']['arrangement']['side_choice']
        self.assertIsNone(first_difference(original, request))

    def test_capacity_proof_of_one_arrangement_does_not_end_global_search(self):
        require_toolchain(native=True)
        root = self.artifact_root()
        _, netlist, facts, request = self.inputs(root, capacity_proof=True)
        plan = placer_runner._plan_local(facts, request, netlist=netlist, heartbeat=lambda _: None)
        self.assert_plan_parity(facts, request, netlist, plan)
        self.assertEqual(plan['status'], 'planned', plan)
        record = plan['block_arrangement']
        self.assertTrue(any(row['status'] == 'packing_infeasible' and row['steps_used'] == 0
                            and 'proof' in row for row in record['residual_failures']))
        self.assertEqual(next(row['selected_face'] for row in record['arranged'] if row['block'] == 'b'), 'B')
        self.assertEqual(record['contract_check']['status'], 'pass')
        charged = sum(max(1, row['steps_used']) for row in record['residual_failures']) + record['residual']['steps_used']
        self.assertEqual(record['residual_budget']['steps_used'], charged)
        self.assertEqual(plan['steps_used'], record['search']['steps_used'] + charged + record['member_refinement']['steps_used'])

    def test_spare_front_prefers_shorter_inter_block_wire(self):
        require_toolchain(native=True)
        root = self.artifact_root()
        _, netlist, facts, request = self.inputs(root, spare_front=True)
        plan = placer_runner._plan_local(facts, request, netlist=netlist, heartbeat=lambda _: None)
        self.assert_plan_parity(facts, request, netlist, plan)
        self.assertEqual(plan['status'], 'planned', plan)
        record = plan['block_arrangement']
        self.assertEqual(next(row['selected_face'] for row in record['arranged'] if row['block'] == 'b'), 'F')
        self.assertEqual(record['adjustments'], [])
        self.assertEqual(record['objective']['terms'], ['pad-locality', 'inter-block-wire', 'thermal-spread'])
        placed = {row['ref']: row for row in plan['placements']}
        self.assertTrue(all(3000000 <= placed[ref]['position_nm'][0] <= 7500000 for ref in ('U1', 'C1')))
        actual = {fp['ref']: placer_runner.transform(fp, placed[fp['ref']]['position_nm'],
                                                    placed[fp['ref']]['rotation_deg'])
                  for fp in facts['footprints'] if fp['ref'] in ('J1', 'U1', 'C1')}
        target = next(p['position_nm'] for p in actual['J1']['pads'] if p['net'] == 'VCC')
        pads = [p['position_nm'] for ref in ('U1', 'C1') for p in actual[ref]['pads'] if p['net'] == 'VCC']
        centroid = [sum(p[axis] for p in pads) // len(pads) for axis in (0, 1)]
        actual_front_wire = sum(abs(a-b) for a, b in zip(centroid, target))
        # H2 and the authored reserve exclude the entire left underside strip;
        # beyond x=7.5, gap .5 and a 1-mm half-courtyard put every member origin
        # at x>=9. Any quarter-turn puts its VCC pad at x>=8.6. This lower bound
        # applies to their centroid too, regardless of y or candidate ordering.
        pad_offset = max(abs(p['position_nm'][axis]-fp['position_nm'][axis])
                         for fp in facts['footprints'] if fp['ref'] in ('U1', 'C1')
                         for p in fp['pads'] for axis in (0, 1))
        self.assertEqual(pad_offset, 400000)
        for fp in facts['footprints']:
            if fp['ref'] in ('U1', 'C1'):
                bounds = placement.box(fp['courtyards']['F']['polygons'])
                self.assertEqual((bounds[2]-bounds[0], bounds[3]-bounds[1]), (2000000, 2000000))
        lower_back_wire = 7500000 + request['policy']['gap_nm'] + 1000000 - pad_offset - target[0]
        self.assertLess(actual_front_wire, lower_back_wire)

    def test_native_cross_face_members_keep_template_cohesion(self):
        require_toolchain(native=True)
        root = self.artifact_root()
        source, netlist, facts, request = self.inputs(root, spare_front=True, underside_reserve=False, mixed=True)
        original = source.read_bytes()
        plan = placer_runner._plan_local(facts, request, netlist=netlist, heartbeat=lambda _: None)
        self.assert_plan_parity(facts, request, netlist, plan)
        self.assertEqual(plan['status'], 'planned', plan)
        placements = {row['ref']: row for row in plan['placements']}
        self.assertEqual((placements['U1']['side'], placements['C1']['side']), ('F', 'B'))
        selected = next(row for row in plan['block_arrangement']['arranged'] if row['block'] == 'b')
        self.assertEqual(selected['selected_face'], 'F')
        check = plan['block_arrangement']['contract_check']
        self.assertEqual(check['status'], 'pass')
        self.assertEqual({row['classification'] for row in check['relationships']}, {'deliberate_cross_face'})
        self.assertTrue(all(row['dz_nm'] == 1600000 and not row['exceeded'] for row in check['relationships']))
        candidates = [entry['candidate'] for entry in request['blocks']['candidates']]
        self.saved_board_checks(root, source, netlist, facts, request, candidates, plan,
                                classification='deliberate_cross_face')
        self.assertEqual(source.read_bytes(), original)

    def test_native_chain_refines_member_after_global_arrangement(self):
        require_toolchain(native=True)
        root = self.artifact_root()
        source, netlist, facts, request = self.inputs(root, spare_front=True,
            underside_reserve=False, mixed=True, chain=True)
        original = source.read_bytes()
        candidates = [entry['candidate'] for entry in request['blocks']['candidates']]
        immutable_candidates = copy.deepcopy(candidates)
        # These are real Stage-A search products, including the initially
        # unconstrained C1-before-C2 order. No retained layout is transplanted.
        self.assertEqual(request['policy']['priority_refs'], ['C1', 'C2'])
        for bundle in candidates:
            for variant in bundle['variants'].values():
                if variant['status'] == 'packed':
                    self.assertEqual(variant['candidate']['provenance']['class'], 'search-derived')
        plan = placer_runner._plan_local(facts, request, netlist=netlist, heartbeat=lambda _: None)
        self.assert_plan_parity(facts, request, netlist, plan)
        self.assertEqual(plan['status'], 'planned', plan)
        record = plan['block_arrangement']
        witness = record['member_refinement']
        self.assertEqual(witness['status'], 'refined', witness)
        self.assertTrue(witness['moves'])
        self.assertEqual(record['contract_check']['status'], 'pass')
        self.assertLessEqual(witness['steps_used'], witness['step_budget'])
        self.assertEqual(plan['steps_used'], record['search']['steps_used']
                         + record['residual_budget']['steps_used'] + witness['steps_used'])
        self.assertEqual(record['routing_feedback']['status'], 'unavailable')
        for move in witness['moves']:
            self.assertEqual(move['block'], 'b')
            self.assertIn(move['reference'], {'C1', 'C2'})
            self.assertNotEqual(move['before_position_nm'], move['after_position_nm'])
            self.assertLess(move['after']['internal_hpwl_nm'], move['before']['internal_hpwl_nm'])
            self.assertLessEqual(move['after']['whole_board_hpwl_nm'], move['before']['whole_board_hpwl_nm'])
            self.assertTrue(all(after <= before for before, after in zip(
                move['before']['declared_pair_distance_squared_nm2'],
                move['after']['declared_pair_distance_squared_nm2'])))
        self.assertEqual(candidates, immutable_candidates)
        self.saved_board_checks(root, source, netlist, facts, request, candidates, plan,
            classification={'same_face', 'deliberate_cross_face'}, member_refs={'U1', 'C1', 'C2'},
            internal_nets={'VCC', 'GND', 'OUT'})
        saved_facts = extract(root / 'published' / 'candidate' / 'candidate.kicad_pcb')
        before_r1 = next(fp for fp in facts['footprints'] if fp['ref'] == 'R1')
        after_r1 = next(fp for fp in saved_facts['footprints'] if fp['ref'] == 'R1')
        before_area = next(area for area in facts['rule_areas'] if area['owner'] == before_r1['uuid'])
        after_area = next(area for area in saved_facts['rule_areas'] if area['owner'] == after_r1['uuid'])
        transformed = placer_runner.transform({'position_nm': before_r1['position_nm'],
            'rotation_deg': before_r1['rotation_deg'], 'pads': [], 'courtyards': {'zone': before_area['outline']}},
            after_r1['position_nm'], after_r1['rotation_deg'])['courtyards']['zone']
        self.assertEqual(after_area['outline']['status'], transformed['status'])
        self.assertEqual([blocks.canonical_ring(p) for p in after_area['outline']['polygons']],
                         [blocks.canonical_ring(p) for p in transformed['polygons']])
        self.assertNotEqual(before_r1['position_nm'], after_r1['position_nm'])
        self.assertEqual(source.read_bytes(), original)

    def saved_board_checks(self, root, source, netlist, facts, request, candidates, plan, *,
                           classification='same_face', member_refs=None, internal_nets=None):
        import pcbnew as k
        writable = (root / 'published').resolve()
        writable.mkdir()
        bound = copy.deepcopy(request)
        bound.update(skip_visuals=True, authorization={'schema': 'agent-ee.placement-write/1',
            'single_design_writer': True, 'writable_root': str(writable), 'source_sha256': facts['board_sha256']})
        destination = writable / 'candidate'
        report = placer_runner.place_candidate(source, netlist, destination, bound)
        self.assertEqual(report['status'], 'placed', report)
        self.assertEqual(report['block_contract']['status'], 'pass')
        saved_plan = json.loads((destination / 'placement-plan.json').read_text())
        self.assertIsNone(first_difference(normalized(plan), normalized(saved_plan)))
        saved = destination / 'candidate.kicad_pcb'
        saved_facts = extract(saved)
        checked = block_contract.judge(saved_facts, request['blocks']['contract'], original_capture=facts, plan=saved_plan)
        self.assertEqual((checked['status'], checked['unknown'], checked['defects']), ('pass', [], []))
        block = next(row for row in checked['blocks'] if row['id'] == 'b')
        self.assertEqual({row['reference'] for row in block['members']}, member_refs or {'U1', 'C1'})
        self.assertGreaterEqual(block['anchor_spread_squared_nm2'], 0)
        if classification == 'same_face':
            self.assertGreater(block['anchor_spread_squared_nm2'], 0)
        self.assertEqual({row['net'] for row in block['internal_connections']['nets']}, internal_nets or {'VCC', 'GND'})
        self.assertEqual({row['classification'] for row in checked['relationships']},
                         {classification} if isinstance(classification, str) else classification)
        self.assertTrue(all(not row['exceeded'] for row in checked['relationships']))
        for label, reference in (('pin-bound', 'C1'), ('forbidden-side', 'J1')):
            mutated = root / label
            mutated.mkdir()
            output = mutated / 'candidate.kicad_pcb'
            board = k.LoadBoard(str(saved))
            fp = next(fp for fp in board.GetFootprints() if fp.GetReference() == reference)
            if label == 'pin-bound':
                fp.SetPosition(fp.GetPosition() + k.VECTOR2I(6000000, 0))
            else:
                fp.Flip(fp.GetPosition(), k.FLIP_DIRECTION_LEFT_RIGHT)
            k.SaveBoard(str(output), board)
            with self.assertRaisesRegex(ValueError, 'saved-board block contract'):
                placer_runner._saved_block_contract(output, request['blocks']['contract'], facts, saved_plan,
                                                    request['policy'], request['blocks'])
            rejection = json.loads((mutated / 'block-contract.json').read_text())
            self.assertEqual(rejection['status'], 'defect')
            self.assertIn('pin_distance_exceeded' if label == 'pin-bound' else 'forbidden_side',
                          {row['kind'] for row in rejection['defects']})
        forged = copy.deepcopy(plan)
        next(row for row in forged['placements'] if row['ref'] == 'J1')['side'] = 'B'
        with self.assertRaisesRegex(ValueError, 'planned member side differs'):
            block_variants.authorization(source, facts, candidates, forged, request['blocks']['side_eligibility'])


if __name__ == '__main__':
    unittest.main()
