"""Member changes must improve guarded metrics without losing native block evidence."""
from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import unittest

from agent_ee import block_contract, block_refinement, placement, placer_runner
from agent_ee.placement_probe import extract
from test_blocks import footprint
from test_placement import capture, rectangle, MM
from test_placer import request
from toolchain import require_toolchain


def declarations(board):
    quote = 'Original fixture numeric requirement: 6 mm'
    source = {'path': 'fixture/refinement-distance.txt', 'sha256': hashlib.sha256(quote.encode()).hexdigest(),
              'locator': 'numeric requirement', 'quote': quote}
    def pair(number, net):
        return {'from': {'reference': 'U1', 'pad': number, 'net': net},
                'to': {'reference': 'C1', 'pad': number, 'net': net}, 'max_distance_nm': 6*MM,
                'citation': 'fixture/refinement-distance.txt numeric requirement',
                'provenance': {'class': 'datasheet', 'basis': 'authored native refinement fixture',
                    'derivation': {'kind': 'numeric', 'value_nm': 6*MM, 'source': source}}}
    document = {'schema': 'agent-ee.electrical-locality/1', 'source_records': {source['path']: quote},
                'obligations': [{'id': 'support', 'metric': 'pad-centre-to-centre',
                                 'supply': pair('1', 'VCC'), 'return': pair('2', 'GND')}]}
    policy = request(gap_nm=500000, electrical_locality=document, ground_nets=['GND'])
    contract = {'schema': block_contract.SCHEMA,
        'allocation': {'schema': 'agent-ee.block-allocation/2', 'board_sha256': board['board_sha256'],
                       'blocks': [{'id': 'device', 'references': ['U1', 'C1'], 'anchor': {'member': 'U1'},
                                   'interfaces_to_fixed': None}], 'fixed': [], 'shared': []},
        'eligibility': {'schema': 'fome-mc.side-eligibility/1', 'sources': {'board_sha256': board['board_sha256']},
                        'default': {'faces': ['F', 'B']}, 'blocks': {'device': {'eligible_faces': ['F', 'B']}}},
        'relationships': document, 'templates': [], 'thickness_nm': 1600000}
    record = {'blocks': [{'block': 'device', 'references': ['U1', 'C1'], 'anchor': 'U1',
                         'candidate_sha256': 'c'*64, 'provenance': {'class': 'search-derived'}}], 'arrangement': {}}
    return policy, contract, record


def facts():
    board = capture([footprint('U1', 2, 2, 'Owned:Square', {'1': 'VCC', '2': 'GND'}),
                     footprint('C1', 6, 2, 'Owned:Square', {'1': 'VCC', '2': 'GND'})],
                    rectangle(0, 0, 12, 8))
    board.update(pcbnew_version='10.0.7', max_error_nm=5000)
    return board


class BlockRefinementTest(unittest.TestCase):
    def test_one_member_moves_inside_window_while_anchor_and_original_stay_exact(self):
        board = facts()
        policy, contract, record = declarations(board)
        before = deepcopy(board)
        final, witness = block_refinement.refine(board, policy, contract, record, step_budget=64)
        self.assertEqual(board, before)
        self.assertEqual(witness['status'], 'refined', witness)
        self.assertEqual(final['footprints'][0], board['footprints'][0])
        self.assertEqual(final['footprints'][1]['position_nm'], [4500000, 2000000])
        self.assertTrue(all(m['after']['internal_hpwl_nm'] < m['before']['internal_hpwl_nm'] for m in witness['moves']))
        self.assertEqual(block_contract.judge(final, contract, original_capture=board)['status'], 'pass')
        self.assertEqual(placement.judge(final)['verdict'], 'pass')
        again, repeat = block_refinement.refine(final, policy, contract, record, step_budget=64)
        self.assertEqual(again, final)
        self.assertEqual(repeat['status'], 'unchanged')

    def test_zero_budget_and_reference_layout_remain_unchanged(self):
        board = facts()
        policy, contract, record = declarations(board)
        final, witness = block_refinement.refine(board, policy, contract, record, step_budget=0)
        self.assertEqual(final, board)
        self.assertEqual((witness['termination'], witness['steps_used']), ('budget_exhausted', 0))
        record['blocks'][0]['provenance']['class'] = 'manufacturer-reference-layout'
        final, witness = block_refinement.refine(board, policy, contract, record, step_budget=64)
        self.assertEqual(final, board)
        self.assertEqual(witness['skipped'][0]['reason'], 'reference_layout_immutable')

    def test_locked_member_and_whole_board_tradeoff_are_not_moved(self):
        board = facts()
        policy, contract, record = declarations(board)
        board['footprints'][1]['locked'] = True
        final, witness = block_refinement.refine(board, policy, contract, record, step_budget=64)
        self.assertEqual(final, board)
        self.assertEqual(witness['moves'], [])
        # A local improvement cannot compensate for any declared pair regression.
        baseline = {'internal_hpwl_nm': 10, 'whole_board_hpwl_nm': 10,
                    'declared_pair_distance_squared_nm2': [9, 16], 'thermal_minimum_distance_squared_nm2': 25}
        for mutation in ({'whole_board_hpwl_nm': 11}, {'declared_pair_distance_squared_nm2': [9, 17]},
                         {'thermal_minimum_distance_squared_nm2': 24}):
            self.assertFalse(block_refinement._improves(baseline, {**baseline, 'internal_hpwl_nm': 9, **mutation}))

    def test_moved_owned_pad_keepout_cannot_cover_stationary_peer_land(self):
        board = facts()
        board['rule_areas'] = [{'uuid': 'owned-pad-zone', 'owner': 'C1', 'layers': ['F.Cu'],
            'forbid': {'footprints': False, 'pads': True},
            'outline': {'status': 'proven', 'polygons': [rectangle(4.75, 2.25, .5, .5)]}}]
        policy, contract, record = declarations(board)
        final, witness = block_refinement.refine(board, policy, contract, record, step_budget=64)
        self.assertEqual(final, board)
        self.assertGreater(witness['rejected'].get('constraint_rejected', 0), 0)
        proposed = block_refinement._move(board, 'C1', [4500000, 2000000])
        self.assertEqual(proposed['rule_areas'][0]['outline']['polygons'], [rectangle(3.25, 2.25, .5, .5)])
        self.assertFalse(block_refinement._admitted(proposed, 'C1', policy, contract, record))

    def test_native_save_reload_preserves_actual_member_improvement(self):
        require_toolchain(native=True)
        import pcbnew as k
        import underside_fixture
        with tempfile.TemporaryDirectory(prefix='agent-ee-member-refinement-') as directory:
            root = Path(directory)
            source, _ = underside_fixture.write_board(root / 'inputs', witness=True)
            board = k.LoadBoard(str(source))
            for fp in list(board.GetFootprints()):
                if fp.GetReference() not in ('U1', 'C1'):
                    board.Remove(fp)
                else:
                    fp.SetPosition(k.VECTOR2I((2 if fp.GetReference() == 'U1' else 7)*MM, 1500000))
            self.assertTrue(k.SaveBoard(str(source), board))
            initial = extract(source)
            policy, contract, record = declarations(initial)
            final, witness = block_refinement.refine(initial, policy, contract, record, step_budget=64)
            self.assertEqual(witness['status'], 'refined', witness)
            initial_by_ref = {fp['ref']: fp for fp in initial['footprints']}
            final_by_ref = {fp['ref']: fp for fp in final['footprints']}
            self.assertEqual(final_by_ref['U1'], initial_by_ref['U1'])
            self.assertNotEqual(final_by_ref['C1']['position_nm'], initial_by_ref['C1']['position_nm'])
            output = root / 'candidate.kicad_pcb'
            plan = {'placements': [{'ref': fp['ref'], 'uuid': fp['uuid'], 'position_nm': fp['position_nm'],
                                    'rotation_deg': fp['rotation_deg'], 'fixed': fp['ref'] == 'U1'}
                                   for fp in final['footprints']]}
            placer_runner._apply_plan(board, plan, output)
            saved = extract(output)
            report = block_contract.judge(saved, contract, original_capture=initial, plan=plan)
            self.assertEqual(report['status'], 'pass', report)
            self.assertEqual({fp['ref']: fp['position_nm'] for fp in saved['footprints']},
                             {fp['ref']: fp['position_nm'] for fp in final['footprints']})
            drc = placer_runner._native_drc(output, root)
            self.assertEqual([r for r in drc['violations'] if r['type'] in placer_runner.DRC_COURTYARDS], [])
