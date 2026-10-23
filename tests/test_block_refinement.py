"""Member changes must improve guarded metrics without losing native block evidence."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_ee import block_contract, block_refinement, placement, placer, placer_runner
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


def verification_entries(board, record):
    """Minimal authored candidate pose records; production admits complete /2 upstream."""
    by_ref = {fp['ref']: fp for fp in board['footprints']}
    block = record['blocks'][0]
    anchor = by_ref[block['anchor']]
    rotation = int(anchor['rotation_deg']) % 360
    members = []
    for ref in block['references']:
        fp = by_ref[ref]
        offset = [p-a for p, a in zip(fp['position_nm'], anchor['position_nm'])]
        members.append({'reference': ref, 'side': fp['side'], 'pose': {
            'position_nm': list(placer.rotate(offset, -rotation//90)),
            'rotation_deg': (int(fp['rotation_deg'])-rotation) % 360}})
    candidate = {'schema': 'agent-ee.block-candidate/2', 'block_id': block['block'],
                 'frame': {'anchor': block['anchor']}, 'members': members,
                 'provenance': deepcopy(block['provenance'])}
    block['candidate_sha256'] = block_refinement.digest(candidate)
    record['arranged'] = [{'block': block['block'], 'position_nm': anchor['position_nm'],
                          'rotation_deg': rotation}]
    return [{'candidate': candidate}]


class BlockRefinementTest(unittest.TestCase):
    def test_capture_digest_preserves_public_facts_without_private_cache_or_angle_spelling(self):
        board = facts()
        expected = block_refinement.capture_digest(board)
        cached = deepcopy(board)
        cached['footprints'][0].update(_packing_targets=['private cache'],
                                       _packing_copper_setback=5050, rotation_deg=0.0)
        cached['footprints'][0]['pads'][0]['rotation_deg'] = 0.0
        untouched = deepcopy(cached)
        self.assertEqual(block_refinement.capture_digest(cached), expected)
        self.assertEqual(cached, untouched)
        for change in ('nanometre', 'nonintegral', 'public', 'missing_angle'):
            changed = deepcopy(board)
            if change == 'nanometre': changed['footprints'][0]['position_nm'][0] += 1
            elif change == 'nonintegral': changed['footprints'][0]['rotation_deg'] = 0.5
            elif change == 'public': changed['footprints'][0]['packing_keepouts'] = []
            else: del changed['footprints'][0]['rotation_deg']
            with self.subTest(change=change):
                self.assertNotEqual(block_refinement.capture_digest(changed), expected)

    def test_saved_metric_replay_binds_actual_context_and_rejects_forged_improvement(self):
        board = facts()
        policy, contract, record = declarations(board)
        entries = verification_entries(board, record)
        final, witness = block_refinement.refine(board, policy, contract, record, step_budget=64)
        record['member_refinement'] = witness
        saved = deepcopy(final)
        saved['board_sha256'] = 'f'*64  # Saved bytes have a distinct identity from planned capture records.
        verification = block_refinement.verify_saved(saved, policy, contract, record, entries)
        self.assertEqual((verification['status'], verification['moves_verified']), ('pass', len(witness['moves'])))
        self.assertEqual(verification['capture_digest_basis'], block_refinement.CAPTURE_DIGEST_BASIS)
        self.assertEqual(verification['saved_public_capture_sha256'], block_refinement.capture_digest(saved))
        self.assertNotEqual(verification['saved_capture_sha256'], witness['final_capture_sha256'])
        with patch('agent_ee.placer_runner.extract', return_value=saved):
            with self.assertRaisesRegex(ValueError, 'returned plan policy'):
                placer_runner._saved_block_contract(Path('not-written.kicad_pcb'), contract, board,
                    {'policy': {**policy, 'gap_nm': 1}}, policy, {})
        with self.assertRaisesRegex(ValueError, 'actual effective policy'):
            block_refinement.verify_saved(saved, {**policy, 'gap_nm': 1}, contract, record, entries)
        with self.assertRaisesRegex(ValueError, 'requested block contract'):
            block_refinement.verify_saved(saved, policy, {**contract, 'thickness_nm': 2}, record, entries)
        for field in ('before', 'after'):
            forged = deepcopy(record)
            forged['member_refinement']['moves'][0][field]['whole_board_hpwl_nm'] += 1
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'metrics differ'):
                block_refinement.verify_saved(saved, policy, contract, forged, entries)
        forged = deepcopy(record)
        forged['member_refinement']['moves'][0]['before_position_nm'][0] += 1
        with self.assertRaisesRegex(ValueError, 'selected starting candidate geometry'):
            block_refinement.verify_saved(saved, policy, contract, forged, entries)
        forged = deepcopy(record)
        forged['blocks'][0]['references'] = ['C1']
        with self.assertRaisesRegex(ValueError, 'membership differs'):
            block_refinement.verify_saved(saved, policy, contract, forged, entries)
        altered = block_refinement._move(saved, 'C1', [4500001, 2000000])
        with self.assertRaisesRegex(ValueError, 'final member pose differs'):
            block_refinement.verify_saved(altered, policy, contract, record, entries)

    def test_one_member_moves_inside_window_while_anchor_and_original_stay_exact(self):
        board = facts()
        policy, contract, record = declarations(board)
        before = deepcopy(board)
        final, witness = block_refinement.refine(board, policy, contract, record, step_budget=64)
        self.assertEqual(board, before)
        self.assertEqual(witness['status'], 'refined', witness)
        self.assertEqual(witness['capture_digest_basis'], block_refinement.CAPTURE_DIGEST_BASIS)
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
        retained = os.environ.get('AGENT_EE_479_TEST_OUTPUT')
        if retained:
            root = Path(retained).resolve() / self._testMethodName
            root.mkdir(parents=True, exist_ok=False)
        else:
            temporary = tempfile.TemporaryDirectory(prefix='agent-ee-member-refinement-')
            self.addCleanup(temporary.cleanup)
            root = Path(temporary.name)
        def retain(name, value):
            (root / name).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
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
        entries = verification_entries(initial, record)
        retain('before-capture.json', initial)
        retain('policy.json', policy)
        retain('contract.json', contract)
        retain('starting-block-records.json', record)
        final, witness = block_refinement.refine(initial, policy, contract, record, step_budget=64)
        retain('after-capture.json', final)
        retain('member-refinement.json', witness)
        self.assertEqual(witness['status'], 'refined', witness)
        initial_by_ref = {fp['ref']: fp for fp in initial['footprints']}
        final_by_ref = {fp['ref']: fp for fp in final['footprints']}
        self.assertEqual(final_by_ref['U1'], initial_by_ref['U1'])
        self.assertNotEqual(final_by_ref['C1']['position_nm'], initial_by_ref['C1']['position_nm'])
        output = root / 'candidate.kicad_pcb'
        plan = {'policy': policy, 'placements': [{'ref': fp['ref'], 'uuid': fp['uuid'], 'position_nm': fp['position_nm'],
                                'rotation_deg': fp['rotation_deg'], 'fixed': fp['ref'] == 'U1'}
                               for fp in final['footprints']]}
        retain('plan.json', plan)
        placer_runner._apply_plan(board, plan, output)
        saved = extract(output)
        record['member_refinement'] = witness
        verification = block_refinement.verify_saved(saved, policy, contract, record, entries)
        retain('saved-refinement-verification.json', verification)
        self.assertEqual(verification['status'], 'pass', verification)
        report = block_contract.judge(saved, contract, original_capture=initial, plan=plan)
        retain('saved-capture.json', saved)
        retain('saved-block-contract.json', report)
        self.assertEqual(report['status'], 'pass', report)
        self.assertEqual({fp['ref']: fp['position_nm'] for fp in saved['footprints']},
                         {fp['ref']: fp['position_nm'] for fp in final['footprints']})
        drc = placer_runner._native_drc(output, root)
        retain('native-drc.json', drc)
        retain('evidence-scope.json', {'scope': 'standalone native member-refinement vertical test',
            'native_save_reload': True, 'native_courtyard_drc': True, 'full_placer_runner': False,
            'netlist_gate': False, 'unused_fixture_netlist': 'inputs/native.xml'})
        self.assertEqual([r for r in drc['violations'] if r['type'] in placer_runner.DRC_COURTYARDS], [])
