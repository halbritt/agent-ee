"""Prepared and local grades preserve exact outputs, including rejected trials."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from agent_ee import mechanical_reservations as mr, placer
from agent_ee.placement_probe import extract
from test_mechanical_custom_pads import FIXTURE, frame_witness, inputs
from test_mechanical_reservations import bound, reservation, simple_capture
from test_placement import MM, capture, part, rectangle
from test_placement_engine_parity import EngineFixture
from test_placer import request
from toolchain import require_toolchain


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


class PreparedMechanicalTests(unittest.TestCase):
    def problem(self):
        facts = capture([part('U1', 2, 2), part('C1', 12, 2), part('R1', 16, 2)])
        row = reservation('service', frame='footprint', owner={'ref': 'U1', 'uuid': 'U1'},
                          polygons=[rectangle(3, 0, 3, 3)], self_exclusion=True)
        binding = bound(facts, [row])
        return facts, binding, mr.prepare(binding, facts)

    def test_raw_bytes_and_output_mutation_do_not_alias_snapshot(self):
        facts, binding, prepared = self.problem()
        for phase in (None, 'assembled', 'undeclared'):
            for partial in (False, True):
                with self.subTest(phase=phase, partial=partial):
                    self.assertEqual(encoded(mr.grade(facts, binding, phase, partial)),
                                     encoded(mr.grade(facts, prepared, phase, partial)))
                    self.assertEqual(encoded(mr.materialize(binding, facts, phase, partial)),
                                     encoded(mr.materialize(prepared, facts, phase, partial)))
        before = encoded(mr.grade(facts, prepared))
        report = mr.grade(facts, prepared)
        report['reservations'][0]['source']['author'] = 'changed output'
        report['reservations'][0]['height_nm'][1] += 1
        self.assertEqual(encoded(mr.grade(facts, prepared)), before)
        with self.assertRaises(TypeError):
            prepared._document['reservations'][0]['faces'].append('B')
        with self.assertRaises(TypeError):
            prepared._owners['U1']['side'] = 'B'

    def test_full_identity_and_mutable_input_changes_require_preparation(self):
        facts, binding, prepared = self.problem()
        identity = prepared.identity
        for changed in ('binding', 'owners', 'side', 'capture', 'board'):
            new_binding, new_capture = copy.deepcopy(binding), copy.deepcopy(facts)
            if changed == 'binding':
                new_binding['document_sha256'] = 'd' * 64
            elif changed == 'owners':
                new_binding['owners'][0]['value'] = 'new owner value'
            elif changed == 'side':
                new_capture['footprints'][0]['side'] = 'B'
            elif changed == 'capture':
                new_capture['pcbnew_version'] = 'other capture identity'
            else:
                new_capture['board_sha256'] = 'e' * 64
            with self.subTest(changed=changed):
                with self.assertRaisesRegex(ValueError, 'identity changed'):
                    prepared.require_problem(new_binding, new_capture)
                self.assertNotEqual(mr.prepare(new_binding, new_capture).identity, identity)
        binding['document']['reservations'][0]['height_nm'][1] += 1
        with self.assertRaisesRegex(ValueError, 'document identity changed'):
            mr.prepare(binding, facts)
        with self.assertRaisesRegex(ValueError, 'identity changed'):
            prepared.require_problem(binding, facts)
        self.assertEqual(prepared.identity, identity)

    def test_every_trial_new_neighbour_commit_rollback_and_stale_generation(self):
        facts, binding, prepared = self.problem()
        local = mr.LocalMechanical(prepared, assert_reference=True)
        initial = local.commit(facts, 1)
        self.assertEqual(initial['status'], 'pass')
        first_count = local.evaluations
        unchanged = local.trial(facts, 1)
        self.assertEqual(unchanged, initial)
        self.assertEqual(local.evaluations, first_count)
        self.assertGreater(local.reused, 0)
        collided = copy.deepcopy(facts)
        collided['footprints'][1] = placer.transform(collided['footprints'][1], [6*MM, 3*MM], 0)
        rejected = local.trial(collided, 1)
        self.assertEqual(rejected, mr.grade(collided, binding, partial=True))
        self.assertEqual(rejected['status'], 'defect')
        self.assertEqual({r['ref'] for r in rejected['defects']}, {'C1'})
        # The previously unrelated R1 edge is reused; the new C1 collision is evaluated.
        self.assertEqual(local.evaluations - first_count, 1)
        self.assertEqual(local.trial(facts, 1)['status'], 'pass')
        self.assertEqual(local.commit(facts, 2), initial)
        with self.assertRaisesRegex(ValueError, 'stale'):
            local.trial(collided, 1)
        self.assertEqual(local.restore(facts, 1), initial)
        self.assertEqual(local.trial(collided, 1)['status'], 'defect')

    def test_owner_move_side_change_unknowns_and_pair_dependency_closure(self):
        facts, binding, prepared = self.problem()
        second = reservation('solid', polygons=[rectangle(10, 2, 2, 2)])
        second['kind'] = 'solid'
        binding = bound(facts, [*binding['document']['reservations'], second])
        local = mr.LocalMechanical(mr.prepare(binding, facts), assert_reference=True)
        local.commit(facts, 0)
        moved = copy.deepcopy(facts)
        moved['footprints'][0] = placer.transform(moved['footprints'][0], [7*MM, 2*MM], 0)
        report = local.trial(moved, 0)
        self.assertTrue(any(r['kind'] == 'mechanical_reservation_pair_overlap' for r in report['defects']))
        moved['footprints'][0]['side'] = 'B'
        moved['footprints'][0]['courtyards'] = {'B': moved['footprints'][0]['courtyards']['F']}
        self.assertEqual(local.trial(moved, 0), mr.grade(moved, binding, partial=True))
        missing = {**facts, 'footprints': facts['footprints'][1:]}
        self.assertEqual(local.trial(missing, 0), mr.grade(missing, binding, partial=True))
        unresolved = copy.deepcopy(binding)
        unresolved['document']['reservations'][0]['coverage']['height'] = 'unknown'
        unresolved['canonical_document_sha256'] = mr._digest(unresolved['document'])
        unknown = mr.LocalMechanical(mr.prepare(unresolved, facts), assert_reference=True).trial(facts, 0)
        self.assertEqual(unknown['status'], 'unknown')

    def test_missing_native_pad_and_tampered_binding_preserve_refusals(self):
        facts = simple_capture()
        fp = facts['footprints'][0]
        row = reservation(frame='pad', owner={'ref': fp['ref'], 'uuid': fp['uuid'],
            'pad': {'number': '1', 'uuid': fp['pads'][0]['uuid']}},
            polygons=[rectangle(0, 0, 1, 1)], self_exclusion=True)
        binding = bound(facts, [row])
        local = mr.LocalMechanical(mr.prepare(binding, facts), assert_reference=True)
        changed = copy.deepcopy(facts)
        changed['footprints'][0]['pads'] = []
        self.assertEqual(local.trial(changed, 0)['status'], 'unknown')
        self.assertEqual(local.trial(facts, 0)['status'], 'pass')
        binding['document']['reservations'][0]['geometry']['polygons_nm'][0][0][0] += 1
        with self.assertRaises(ValueError):
            mr.prepare(binding, facts)

    def test_empty_selection_is_still_validated_and_capture_unknown_is_not_pass(self):
        facts, binding, _ = self.problem()
        selected = mr.for_block(binding, ['C1', 'R1'])
        prepared = mr.prepare(selected, facts)
        self.assertEqual(mr.grade(facts, prepared)['reservations'], [])
        broken = copy.deepcopy(selected)
        broken['document']['reservations'][0]['height_nm'][1] += 1
        with self.assertRaises(ValueError):
            mr.prepare(broken, facts)
        invalid = copy.deepcopy(facts)
        invalid['schema'] = 'wrong'
        self.assertEqual(mr.LocalMechanical(prepared, assert_reference=True).trial(invalid, 0)['status'], 'unknown')


class RealPodPreparedMechanicalTests(EngineFixture, unittest.TestCase):
    def test_actual_custom_pad_access_rows_native_move_new_collision_and_parity(self):
        require_toolchain(native=True)
        import pcbnew as k
        _, declaration = inputs()
        with tempfile.TemporaryDirectory(prefix='agent-ee-prepared-jp-') as temporary:
            target = Path(temporary) / 'jumpers.kicad_pcb'
            board = k.LoadBoard(str(FIXTURE / 'jumpers.kicad_pcb'))
            for fp in board.GetFootprints():
                fp.SetPosition(k.VECTOR2I((10 + 15 * (int(fp.GetReference()[2:]) - 1)) * MM, 20 * MM))
            k.SaveBoard(str(target), board)
            facts = extract(target)
            declaration['original_board_sha256'] = facts['board_sha256']
            binding = mr.bind(frame_witness(declaration), facts)
            prepared = mr.prepare(binding, facts)
            local = mr.LocalMechanical(prepared, assert_reference=True)
            initial = local.commit(facts, 1)
            self.assertEqual(initial['status'], 'pass', initial)
            self.assertEqual(len(initial['reservations']), 10)
            self.assertTrue(all(row['kind'] == 'access' for row in initial['reservations']))
            policy = request(fixed_refs=[fp['ref'] for fp in facts['footprints']],
                             mechanical_reservations=binding, step_budget=2000)
            self.assertEqual(self.compare(facts, {'policy': policy})['status'], 'planned')
            jp1 = next(fp for fp in board.GetFootprints() if fp.GetReference() == 'JP1')
            jp2 = next(fp for fp in board.GetFootprints() if fp.GetReference() == 'JP2')
            jp2.SetPosition(jp1.GetPosition())
            k.SaveBoard(str(target), board)
            collided = extract(target)
            rejected = local.trial(collided, 1)
            self.assertEqual(rejected['status'], 'defect')
            self.assertEqual(encoded(rejected), encoded(mr.grade(collided, binding, partial=True)))
            self.assertTrue(any(row['ref'] == 'JP2' for row in rejected['defects']))
            self.assertNotEqual(self.compare(collided, {'policy': policy})['status'], 'planned')
            self.assertEqual(local.restore(facts, 1), initial)
            jp2.SetPosition(k.VECTOR2I(25 * MM, 20 * MM))
            jp2.Flip(jp2.GetPosition(), k.FLIP_DIRECTION_LEFT_RIGHT)
            k.SaveBoard(str(target), board)
            flipped = extract(target)
            self.assertEqual(local.trial(flipped, 1), mr.grade(flipped, binding, partial=True))
            self.assertEqual(self.compare(flipped, {'policy': policy})['status'], 'planned')
            changed = copy.deepcopy(binding)
            changed['document']['reservations'][0]['height_nm'][1] += 1
            with self.assertRaises(ValueError):
                mr.prepare(changed, facts)
