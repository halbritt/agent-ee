"""AGENTEE-479: a feasible underside must participate in residual legalization."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from agent_ee import block_arrangement, block_variants, blocks, electrical_locality, placement, placement_groups
from agent_ee import placer_runner
from agent_ee.native_netlist import read_native_inventory, read_sheet_names
from agent_ee.placement_probe import extract
from toolchain import require_toolchain
import underside_fixture as fixture


class CongestedUndersideTest(unittest.TestCase):
    def test_public_planner_moves_block_after_residual_conflict(self):
        require_toolchain(native=True)
        retained = os.environ.get('AGENT_EE_479_TEST_OUTPUT')
        if retained:
            root = Path(retained)
            root.mkdir(parents=True, exist_ok=False)
        else:
            temporary = tempfile.TemporaryDirectory(prefix='agent-ee-underside-')
            self.addCleanup(temporary.cleanup)
            root = Path(temporary.name)
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
        record = plan['block_arrangement']
        first = next(row for row in record['first_arranged'] if row['block'] == 'b')
        final = next(row for row in record['arranged'] if row['block'] == 'b')
        self.assertEqual((first['selected_face'], final['selected_face']), ('F', 'B'))
        self.assertNotEqual(first, final)
        self.assertTrue(any(row['reason'] == 'residual_packing_failed'
                            and first in row['before'] and final in row['after'] for row in record['adjustments']))
        self.assertEqual({row['ref'] for row in plan['placements']}, set(fixture.WITNESS))
        self.assertEqual(source.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
