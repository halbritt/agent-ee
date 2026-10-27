"""AGENTEE-489 controls, pending normal-job propagation after AGENTEE-479.

These authored dimensions qualify regression behavior, not a production design.
The opposite-side case establishes native legality only: the prepared job still
loses occupied layers. Its engine answer is retained for diagnosis, never treated
as a successful propagation test. Same-side and drilled controls must reject.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from agent_ee import mechanical_reservations, placement_job, placement_problem
from agent_ee.placement_probe import extract
from test_mechanical_reservations import bound, raw, reservation, write_native_board
from test_placement import MM, rectangle
from test_placement_job import build_board
from toolchain import require_toolchain


class SideMechanicalControls(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)
        retained = os.environ.get('AGENT_EE_489_PROPAGATION_OUTPUT')
        if retained:
            self.root = Path(retained) / self._testMethodName
            self.root.mkdir(parents=True, exist_ok=False)
        else:
            temporary = tempfile.TemporaryDirectory(prefix='agent-ee-489-controls-')
            self.addCleanup(temporary.cleanup)
            self.root = Path(temporary.name)

    def test_native_overlap_controls_preserve_faces_and_through_hole_conflicts(self):
        import pcbnew as k

        source = build_board(self.root / 'source.kicad_pcb', locked_hole=False)
        native = k.LoadBoard(str(source))
        for fp in list(native.GetFootprints()):
            if fp.GetReference() not in ('R1', 'C1'):
                native.Remove(fp)
        back = next(fp for fp in native.GetFootprints() if fp.GetReference() == 'C1')
        back.Flip(back.GetPosition(), k.FLIP_DIRECTION_LEFT_RIGHT)
        self.assertTrue(k.SaveBoard(str(source), native))
        self.check_collision_controls(source)

    def check_collision_controls(self, source):
        """Also accepts the retained exact Astra article for a bounded replay."""
        import pcbnew as k

        original = source.read_bytes()
        for name, flip, drilled, separated, illegal in (
                ('opposite-smt', False, False, False, False),
                ('same-smt', True, False, False, True),
                ('opposite-pth', False, True, False, True),
                ('separated-pth', False, True, True, False)):
            with self.subTest(control=name):
                native = k.LoadBoard(str(source))
                back = next(fp for fp in native.GetFootprints() if fp.GetReference() == 'C1')
                self.assertTrue(back.IsFlipped())
                if flip:
                    back.Flip(back.GetPosition(), k.FLIP_DIRECTION_LEFT_RIGHT)
                if drilled:
                    for pad in back.Pads():
                        pad.SetAttribute(k.PAD_ATTRIB_PTH)
                        pad.SetLayerSet(k.LSET.AllCuMask())
                        pad.SetDrillSize(k.VECTOR2I(300_000, 300_000))
                if separated:
                    back.SetPosition(k.VECTOR2I(15 * MM, 6 * MM))
                saved = self.root / f'{name}.kicad_pcb'
                self.assertTrue(k.SaveBoard(str(saved), native))
                saved.with_suffix('.kicad_pro').write_text('{}\n')
                facts = extract(saved)
                (self.root / f'{name}-facts.json').write_bytes(raw(facts))
                by_ref = {fp['ref']: fp for fp in facts['footprints']}
                self.assertEqual(by_ref['R1']['side'], 'F')
                self.assertEqual(by_ref['C1']['side'], 'F' if flip else 'B')
                drc, execution = placement_job.drc(saved, self.root, label=f'{name}-drc')
                (self.root / f'{name}-drc-execution.json').write_bytes(raw(execution))
                self.assertEqual(bool(drc['violations']), illegal, drc['violations'])
                self.assertEqual('shorting_items' in {v['type'] for v in drc['violations']}, illegal)
                evaluation = self.evaluate_saved(saved, name, drc)
                if illegal:
                    self.assertFalse(evaluation['feasible'], evaluation)
                    self.assertTrue(evaluation['violations'], evaluation)
                elif separated:
                    self.assertTrue(evaluation['feasible'], evaluation)
                # Opposite SMT solver acceptance is deliberately still pending.
        self.assertEqual(source.read_bytes(), original)

    def evaluate_saved(self, saved, name, drc):
        normalized = placement_job.normalize_intent(
            {'fixed_poses': [], 'edge_connectors': [], 'proximity': []}, {'groups': []})
        problem, capture = placement_job.capture(saved, normalized)
        payload = dict(problem, evaluate=[{}])
        (self.root / f'{name}-input.json').write_bytes(raw(payload))
        binary = placement_job.rust_executable()
        done = subprocess.run([str(binary)], input=raw(payload), capture_output=True, timeout=60)
        (self.root / f'{name}-engine.stdout').write_bytes(done.stdout)
        (self.root / f'{name}-engine.stderr').write_bytes(done.stderr)
        output = json.loads(done.stdout)
        placement_problem.check_output(output, payload, code=done.returncode)
        evaluation = output['evaluations'][0]
        (self.root / f'{name}-result.json').write_bytes(raw({
            'native_violations': len(drc['violations']),
            'native_unconnected': len(drc['unconnected_items']),
            'evaluation': evaluation, 'capture': capture,
            'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
            'input_sha256': hashlib.sha256(raw(payload)).hexdigest()}))
        return evaluation

    def test_attached_reservations_follow_native_pads_from_both_source_faces(self):
        import pcbnew as k

        for original_face in ('F', 'B'):
            directory = self.root / original_face
            directory.mkdir()
            source = write_native_board(directory)
            if original_face == 'B':
                native = k.LoadBoard(str(source))
                owner = next(iter(native.GetFootprints()))
                owner.Flip(owner.GetPosition(), k.FLIP_DIRECTION_LEFT_RIGHT)
                self.assertTrue(k.SaveBoard(str(source), native))
            original = source.read_bytes()
            facts = extract(source)
            owner = facts['footprints'][0]
            pad = next(p for p in owner['pads'] if p['number'] == '1')
            identity = {'ref': owner['ref'], 'uuid': owner['uuid']}
            # Native witness pads outline the authored reservation; expected
            # vertices come from save/reload, not a copy of the transform math.
            y = 1 if original_face == 'F' else -1
            footprint_polygon = [[[MM, 0], [3 * MM, 0], [3 * MM, y * MM], [MM, y * MM]]]
            pad_polygon = [[[0, 0], [2 * MM, 0], [2 * MM, y * MM], [0, y * MM]]]
            fixed = reservation('fixed', polygons=[rectangle(15, 15, 1, 1)])
            binding = bound(facts, [fixed,
                reservation('footprint', frame='footprint', owner=identity,
                            polygons=footprint_polygon, self_exclusion=True),
                reservation('pad', frame='pad', polygons=pad_polygon, self_exclusion=True,
                            owner={**identity, 'pad': {'number': '1', 'uuid': pad['uuid']}})])
            (directory / 'binding.json').write_bytes(raw(binding))
            for rotation in (0, 90, 180, 270):
                for flip in (False, True):
                    with self.subTest(original_face=original_face, rotation=rotation, flip=flip):
                        native = k.LoadBoard(str(source))
                        owned = next(iter(native.GetFootprints()))
                        owned.SetPosition(k.VECTOR2I(8 * MM, 6 * MM))
                        owned.SetOrientationDegrees(rotation)
                        if flip:
                            owned.Flip(owned.GetPosition(), k.FLIP_DIRECTION_LEFT_RIGHT)
                        saved = directory / f'rotation-{rotation}-flip-{int(flip)}.kicad_pcb'
                        self.assertTrue(k.SaveBoard(str(saved), native))
                        self.check_reservations_at_native_pads(saved, binding, fixed)
            self.assertEqual(source.read_bytes(), original)

    def check_reservations_at_native_pads(self, saved, binding, fixed):
        fresh = extract(saved)
        owner = fresh['footprints'][0]
        pads = {p['number']: p['position_nm'] for p in owner['pads']}
        expected = [[pads[str(number)] for number in range(1, 5)]]
        grade = mechanical_reservations.grade(fresh, binding)
        saved.with_suffix('.grade.json').write_bytes(raw(grade))
        self.assertEqual(grade['status'], 'pass', grade)
        rows = {r['id']: r for r in grade['reservations']}
        self.assertEqual(rows['fixed']['polygons_nm'], fixed['geometry']['polygons_nm'])
        self.assertEqual(rows['fixed']['faces'], ['F'])
        for name in ('footprint', 'pad'):
            self.assertEqual(rows[name]['polygons_nm'], expected)
            self.assertEqual(rows[name]['faces'], [owner['side']])


if __name__ == '__main__':
    unittest.main()
