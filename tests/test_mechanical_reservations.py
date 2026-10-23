"""Source-bound mechanical geometry, including fresh native move/flip replay.

All dimensions and engineering statements here are authored regression inputs.
They establish no manufacturer requirement or production enclosure clearance.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
import uuid

from agent_ee import board_envelope, placement, placement_adapter, placer
from agent_ee.placement_probe import extract
from test_placement import MM, capture, part, rectangle
from test_placer import request
from test_placement_engine_parity import EngineFixture
from toolchain import require_toolchain


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = 'sources/authored-mechanics.txt'
SOURCE_TEXT = 'Agent-authored fixture: the declared solid and access projections must remain unobstructed.'
NAMESPACE = uuid.UUID('d1598a3e-70a1-4e68-a584-52478717d848')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def raw(document):
    return (json.dumps(document, sort_keys=True, indent=2) + '\n').encode()


def mechanics():
    # Kept local so the no-extra-restrictions regression also loads on the base.
    from agent_ee import mechanical_reservations
    return mechanical_reservations


def reservation(identifier='fixed-access', *, frame='board', owner=None, polygons=None,
                faces=None, self_exclusion=False):
    row = {'id': identifier, 'required': True, 'kind': 'access', 'frame': frame,
           'geometry': {'polygons_nm': polygons if polygons is not None else [rectangle(8, .5, 4.5, 2)]},
           'faces': faces or (['F'] if frame == 'board' else ['same']),
           'height_nm': [0, 5 * MM], 'phases': ['assembled'], 'self_exclusion': self_exclusion,
           'coverage': {'projection': 'conservative-per-face', 'height': 'covered', 'access': 'covered',
                        'basis': 'Authored fixture reserves this full face projection for the stated interval.'},
           'uncertainty': []}
    if owner is not None:
        row['owner'] = owner
    return row


def document(facts, rows):
    return {'schema': 'agent-ee.mechanical-reservations/1',
            'original_board_sha256': facts['board_sha256'], 'original_board_path': 'source.kicad_pcb',
            'source': {'path': SOURCE_PATH, 'sha256': sha(SOURCE_TEXT.encode()),
                       'locator': 'authored complete projection', 'classification': 'engineering',
                       'author': 'agent-ee regression fixture author'},
            'reservations': rows}


def bound(facts, rows):
    declared = document(facts, rows)
    return mechanics().bind(declared, facts, source_sha256=sha(raw(declared)))


def simple_capture():
    facts = capture([part('U1', 2, 2)])
    owner = facts['footprints'][0]
    owner['uuid'] = str(uuid.uuid5(NAMESPACE, 'U1'))
    owner['pads'] = [{'uuid': str(uuid.uuid5(NAMESPACE, 'U1-pad-1')), 'number': '1', 'net': 'VCC',
                      'position_nm': [3 * MM, 2 * MM], 'rotation_deg': 0,
                      'copper_centroid_offset_nm': [[0, 1], [0, 1]]}]
    return facts


class MechanicalReservationTests(unittest.TestCase):
    def test_captured_envelope_accepts_engineering_reservations_without_promoting_authority(self):
        with tempfile.TemporaryDirectory(prefix='agent-ee-mechanical-source-') as temporary:
            root = Path(temporary)
            (root / SOURCE_PATH).parent.mkdir()
            (root / SOURCE_PATH).write_text(SOURCE_TEXT)
            board_bytes = b'Authored immutable board binding fixture; not a native geometry probe.\n'
            (root / 'source.kicad_pcb').write_bytes(board_bytes)
            facts = simple_capture()
            facts['board_sha256'] = sha(board_bytes)
            declared = document(facts, [reservation()])
            (root / 'mechanical.json').write_bytes(raw(declared))
            envelope = json.loads((ROOT / 'fixtures/board-envelope/rc.envelope.json').read_text())
            envelope['mechanical_reservations'] = {'path': 'mechanical.json', 'sha256': sha(raw(declared))}
            (root / 'envelope.json').write_bytes(raw(envelope))
            files = {p.relative_to(root).as_posix(): sha(p.read_bytes()) for p in root.rglob('*') if p.is_file()}
            record = board_envelope.validate(raw(envelope), captured=(files, root))
            actual = record['mechanical_reservations']
            self.assertEqual(actual['document'], declared)
            self.assertEqual(actual['source']['classification'], 'engineering')
            self.assertEqual(actual['source']['author'], declared['source']['author'])
            manifest = {'files': files, 'scope': {'checks': [{'kind': 'placement', 'params': {
                'criterion': 'mechanical-access/1', 'drc_check': 'drc', 'envelope': 'envelope.json'}}]}}
            self.assertTrue(placement_adapter.envelope_fixed(manifest, root, []))
            for mutable in ('mechanical.json', SOURCE_PATH, 'source.kicad_pcb'):
                with self.subTest(mutable=mutable), self.assertRaises(ValueError):
                    placement_adapter.envelope_fixed(manifest, root, [mutable])
            for changed in ('mechanical.json', SOURCE_PATH, 'source.kicad_pcb'):
                path = root / changed
                original = path.read_bytes()
                path.write_bytes(original + b'\n')
                with self.subTest(stale=changed), self.assertRaises(ValueError):
                    board_envelope.validate(raw(envelope), captured=(files, root))
                path.unlink()
                with self.subTest(missing=changed), self.assertRaises(ValueError):
                    board_envelope.validate(raw(envelope), captured=(files, root))
                path.write_bytes(original)
            with self.assertRaises(ValueError):
                placement_adapter.refuse_policy_sources({'mechanical_reservations': declared})

    def test_required_unknown_geometry_height_access_and_phase_never_pass(self):
        facts = simple_capture()
        for missing in ('geometry', 'height', 'height-coverage', 'access-coverage'):
            row = reservation()
            if missing == 'geometry':
                row['geometry']['polygons_nm'] = None
            elif missing == 'height':
                row['height_nm'] = None
                row['uncertainty'] = ['Estimated cowl height 90 mm; this is not an established maximum.']
            else:
                row['coverage'][missing.split('-')[0]] = 'unknown'
            with self.subTest(missing=missing):
                binding = bound(facts, [row])
                result = mechanics().grade(facts, binding)
                self.assertEqual(result['status'], 'unknown', result)
                self.assertTrue(result['unknown'])
                self.assertEqual(binding['document']['reservations'][0]['uncertainty'], row['uncertainty'])
        binding = bound(facts, [reservation()])
        self.assertEqual(mechanics().grade(facts, binding, phase='undeclared-installation')['status'], 'unknown')

    def test_missing_or_stale_native_owner_and_pad_binding_never_pass(self):
        facts = simple_capture()
        fp = facts['footprints'][0]
        owner = {'ref': fp['ref'], 'uuid': fp['uuid'],
                 'pad': {'number': '1', 'uuid': fp['pads'][0]['uuid']}}
        binding = bound(facts, [reservation(frame='pad', owner=owner,
            polygons=[rectangle(0, 0, 1, 1)], self_exclusion=True)])
        partial = {**facts, 'footprints': []}
        self.assertEqual(mechanics().materialize(binding, partial, partial=True)['status'], 'pass')
        self.assertEqual(mechanics().materialize(binding, partial, partial=False)['status'], 'unknown')
        for mutation in ('missing-owner', 'stale-owner', 'missing-pad', 'stale-pad', 'wrong-pad-number'):
            changed = copy.deepcopy(facts)
            fp = changed['footprints'][0]
            if mutation == 'missing-owner':
                changed['footprints'] = []
            elif mutation == 'stale-owner':
                fp['uuid'] = str(uuid.uuid5(NAMESPACE, 'replacement-owner'))
            elif mutation == 'missing-pad':
                fp['pads'] = []
            elif mutation == 'stale-pad':
                fp['pads'][0]['uuid'] = str(uuid.uuid5(NAMESPACE, 'replacement-pad'))
            else:
                fp['pads'][0]['number'] = '2'
            with self.subTest(mutation=mutation):
                result = mechanics().grade(changed, binding)
                self.assertEqual(result['status'], 'unknown', result)
                self.assertTrue(result['unknown'])
        tampered = copy.deepcopy(binding)
        tampered['document']['reservations'][0]['height_nm'][1] += 1
        with self.assertRaises(ValueError):
            mechanics().grade(facts, tampered)
        with self.assertRaises(ValueError):
            mechanics().grade(facts, None)
        changed_source = copy.deepcopy(facts)
        changed_source['board_sha256'] = 'b' * 64
        with self.assertRaises(ValueError):
            mechanics().bind(binding['document'], changed_source)

    def test_custom_pads_refuse_and_exact_copper_offsets_follow_native_transform(self):
        facts = simple_capture()
        source = facts['footprints'][0]
        source['pads'][0]['copper_centroid_offset_nm'] = [[1, 2], [3, 4]]
        source['pads'][0]['copper_land_offset_nm'] = [2, 3]
        row = reservation(frame='footprint', owner={'ref': source['ref'], 'uuid': source['uuid']},
                          polygons=[rectangle(0, 0, 1, 1)], self_exclusion=True)
        binding = bound(facts, [row])
        moved = copy.deepcopy(facts)
        owner = moved['footprints'][0]
        owner.update(side='B', position_nm=[10 * MM, 10 * MM], rotation_deg=90,
                     courtyards={'B': {'status': 'proven', 'polygons': [rectangle(8, 8, 2, 2)]}})
        owner['pads'][0].update(position_nm=[10 * MM, 9 * MM], rotation_deg=90,
            copper_centroid_offset_nm=[[-3, 4], [-1, 2]], copper_land_offset_nm=[-3, -2])
        result = mechanics().grade(moved, binding)
        self.assertEqual(result['status'], 'pass', result)
        self.assertEqual(result['reservations'][0]['faces'], ['B'])
        self.assertEqual(result['reservations'][0]['polygons_nm'],
                         [[[10 * MM, 10 * MM], [10 * MM, 9 * MM], [9 * MM, 9 * MM], [9 * MM, 10 * MM]]])
        for key, value in (('copper_centroid_offset_nm', [[3, 4], [-1, 2]]),
                           ('copper_land_offset_nm', [3, -2])):
            changed = copy.deepcopy(moved)
            changed['footprints'][0]['pads'][0][key] = value
            with self.subTest(offset=key):
                rejected = mechanics().grade(changed, binding)
                self.assertEqual(rejected['status'], 'unknown', rejected)
                self.assertEqual(rejected['unknown'][0]['reason'],
                                 'mechanical native pad copper offset changed independently of owner')
        for key, value in (('shape', 6), ('custom_geometry', {'status': 'proven'})):
            changed = copy.deepcopy(moved)
            changed['footprints'][0]['pads'][0][key] = value
            with self.subTest(custom=key):
                rejected = mechanics().grade(changed, binding)
                self.assertEqual(rejected['status'], 'unknown', rejected)
                self.assertEqual(rejected['unknown'][0]['reason'],
                                 'mechanical attachments do not support custom-pad geometry')
                unsupported = copy.deepcopy(facts)
                unsupported['footprints'][0]['pads'][0][key] = value
                with self.assertRaisesRegex(ValueError, 'custom-pad geometry'):
                    bound(unsupported, [row])

    def test_self_exclusion_is_explicit_and_excludes_only_native_owner(self):
        facts = simple_capture()
        fp = facts['footprints'][0]
        row = reservation(frame='footprint', owner={'ref': fp['ref'], 'uuid': fp['uuid']},
                          polygons=[rectangle(0, 0, 1, 1)])
        rejected = mechanics().grade(facts, bound(facts, [row]))
        self.assertEqual(rejected['status'], 'defect')
        self.assertEqual({r['ref'] for r in rejected['defects']}, {'U1'})
        row['self_exclusion'] = True
        binding = bound(facts, [row])
        self.assertEqual(mechanics().grade(facts, binding)['status'], 'pass')
        changed = copy.deepcopy(facts)
        changed['footprints'].append(part('C1', 2.25, 2.25))
        rejected = mechanics().grade(changed, binding)
        self.assertEqual(rejected['status'], 'defect')
        self.assertEqual({r['ref'] for r in rejected['defects']}, {'C1'})
        final = placement.judge(changed, mechanical=binding)
        self.assertEqual(final['verdict'], 'hard_fail')
        self.assertIn('mechanical_reservation_overlap', {r['kind'] for r in final['hard_failures']})

    def test_plain_packing_without_extra_restrictions_remains_available(self):
        facts = capture([part('U1', 2, 2), part('C1', 10, 10)])
        plan = placer.pack(facts, request())
        self.assertEqual(plan['status'], 'planned', plan)
        self.assertEqual(plan['judgment']['verdict'], 'pass')
        self.assertNotIn('mechanical_reservations', plan['policy'])
        self.assertNotIn('mechanical_reservations', plan['judgment'])

    def test_attached_solid_blocks_another_owners_access_outside_both_courtyards(self):
        facts = simple_capture()
        other = part('C1', 10, 2)
        other['uuid'] = str(uuid.uuid5(NAMESPACE, 'C1'))
        facts['footprints'].append(other)
        left = reservation('solid-extension', frame='footprint',
            owner={'ref': 'U1', 'uuid': facts['footprints'][0]['uuid']},
            polygons=[rectangle(4, 0, 1, 1)], self_exclusion=True)
        left['kind'] = 'solid'
        right = reservation('other-access', frame='footprint',
            owner={'ref': 'C1', 'uuid': other['uuid']},
            polygons=[rectangle(-4, 0, 1, 1)], self_exclusion=True)
        self.assertEqual(placement.judge(facts)['verdict'], 'pass')
        result = mechanics().grade(facts, bound(facts, [left, right]))
        self.assertEqual(result['status'], 'defect', result)
        self.assertEqual({row['kind'] for row in result['defects']}, {'mechanical_reservation_pair_overlap'})
        self.assertEqual({result['defects'][0]['reservation'], result['defects'][0]['other_reservation']},
                         {'solid-extension', 'other-access'})
        # Distinct phases do not manufacture a simultaneous-service requirement.
        right['phases'] = ['maintenance-with-solid-removed']
        self.assertEqual(mechanics().grade(facts, bound(facts, [left, right]))['status'], 'pass')



class MechanicalPlanningParityTests(EngineFixture, unittest.TestCase):
    def test_global_policy_cannot_suppress_required_rows_with_block_selection(self):
        facts = simple_capture()
        row = reservation('required-world-space')
        row['geometry']['polygons_nm'] = None
        binding = mechanics().for_block(bound(facts, [row]), ['U1'])
        self.assertEqual(binding['selection']['reservation_ids'], [])
        policy = request(fixed_refs=['U1'], mechanical_reservations=binding)
        for override in (None, request(fixed_refs=['U1'])):
            with self.subTest(effective_override=override is not None):
                result = self.compare(facts, {'policy': policy}, policy=override)
                self.assertEqual(result, {'error': 'global policy cannot use a block mechanical selection'})

    def test_required_unknown_geometry_refuses_before_search_in_python_and_rust(self):
        facts = capture([part('U1', 2, 2), part('C1', 10, 10)])
        row = reservation('unresolved-service-lane')
        row['geometry']['polygons_nm'] = None
        policy = request(fixed_refs=['U1'], mechanical_reservations=bound(facts, [row]))
        plan = self.compare(facts, {'policy': policy})
        self.assertEqual(plan['status'], 'mechanical_geometry_unavailable')
        self.assertEqual(plan['outcome'], 'unknown')
        self.assertEqual(plan['steps_used'], 0)
        self.assertEqual(plan['mechanical_reservations']['status'], 'unknown')
        self.assertEqual({item['reservation'] for item in plan['mechanical_reservations']['unknown']},
                         {'unresolved-service-lane'})
        self.assertFalse(plan['mechanical_reservations']['defects'])

def write_native_board(directory):
    """Asymmetric native pads independently witness the attachment transform."""
    import pcbnew as k
    directory.mkdir(parents=True, exist_ok=True)
    board = k.BOARD()
    vector = lambda x, y: k.VECTOR2I(round(x * MM), round(y * MM))

    def identity(item, name):
        item.SetUuidDirect(k.KIID(str(uuid.uuid5(NAMESPACE, name))))

    def ring(parent, layer, points, name):
        for index, start in enumerate(points):
            edge = k.PCB_SHAPE(parent)
            identity(edge, f'{name}-{index}')
            edge.SetShape(k.SHAPE_T_SEGMENT)
            edge.SetLayer(layer)
            edge.SetStart(vector(*start))
            edge.SetEnd(vector(*points[(index + 1) % len(points)]))
            edge.SetWidth(50000)
            parent.Add(edge)

    ring(board, k.Edge_Cuts, [(0, 0), (20, 0), (20, 20), (0, 20)], 'outline')
    fp = k.FOOTPRINT(board)
    identity(fp, 'U1')
    fp.SetReference('U1')
    fp.SetValue('authored transform witness')
    fp.SetAttributes(k.FP_SMD)
    board.Add(fp)
    ring(fp, k.F_CrtYd, [(-.5, -.5), (3.5, -.5), (3.5, 1.5), (-.5, 1.5)], 'courtyard')
    net = k.NETINFO_ITEM(board, 'VCC')
    board.Add(net)
    for number, position in enumerate(((1, 0), (3, 0), (3, 1), (1, 1)), 1):
        pad = k.PAD(fp)
        identity(pad, f'U1-pad-{number}')
        pad.SetNumber(str(number))
        pad.SetAttribute(k.PAD_ATTRIB_SMD)
        pad.SetShape(k.PAD_SHAPE_RECT)
        pad.SetSize(vector(.1, .1))
        pad.SetPosition(vector(*position))
        layers = k.LSET()
        for layer in (k.F_Cu, k.F_Mask, k.F_Paste):
            layers.AddLayer(layer)
        pad.SetLayerSet(layers)
        pad.SetNet(net)
        fp.Add(pad)
    fp.SetPosition(vector(3, 3))
    path = directory / 'source.kicad_pcb'
    if not k.SaveBoard(str(path), board):
        raise RuntimeError('could not save authored native mechanics fixture')
    return path


class NativeMechanicalReservationTests(unittest.TestCase):
    def root(self):
        retained = os.environ.get('AGENT_EE_489_TEST_OUTPUT')
        if retained:
            directory = Path(retained).resolve() / self._testMethodName
            directory.mkdir(parents=True, exist_ok=False)
            return directory
        temporary = tempfile.TemporaryDirectory(prefix='agent-ee-mechanical-native-')
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name)

    def test_saved_native_move_rotation_and_flip_transform_attached_but_not_fixed(self):
        require_toolchain(native=True)
        import pcbnew as k
        root = self.root()
        source = write_native_board(root)
        source_bytes = source.read_bytes()
        facts = extract(source)
        fp = facts['footprints'][0]
        owner = {'ref': 'U1', 'uuid': fp['uuid']}
        pad = next(p for p in fp['pads'] if p['number'] == '1')
        fixed = reservation('fixed', polygons=[rectangle(15, 15, 1, 1)])
        attached = reservation('attached', frame='footprint', owner=owner,
                               polygons=[rectangle(1, 0, 2, 1)], self_exclusion=True)
        pad_attached = reservation('pad-attached', frame='pad',
            owner={**owner, 'pad': {'number': '1', 'uuid': pad['uuid']}},
            polygons=[rectangle(0, 0, 2, 1)], self_exclusion=True)
        binding = bound(facts, [fixed, attached, pad_attached])
        (root / 'binding.json').write_bytes(raw(binding))
        previous_hash = facts['board_sha256']
        for name, position, rotation, flip in (
                ('moved', (8, 6), 0, False), ('rotated', (8, 6), 90, False),
                ('flipped', (8, 6), 90, True)):
            native = k.LoadBoard(str(source))
            owned = next(f for f in native.GetFootprints() if f.GetReference() == 'U1')
            owned.SetPosition(k.VECTOR2I(position[0] * MM, position[1] * MM))
            owned.SetOrientationDegrees(rotation)
            if flip:
                owned.Flip(owned.GetPosition(), k.FLIP_DIRECTION_LEFT_RIGHT)
            saved = root / f'{name}.kicad_pcb'
            self.assertTrue(k.SaveBoard(str(saved), native))
            fresh = extract(saved)
            self.assertNotEqual(fresh['board_sha256'], previous_hash)
            previous_hash = fresh['board_sha256']
            current = fresh['footprints'][0]
            native_pad_points = {p['number']: p['position_nm'] for p in current['pads']}
            expected = [[native_pad_points[str(number)] for number in range(1, 5)]]
            materialized = mechanics().materialize(binding, fresh)
            (root / f'{name}-materialized.json').write_bytes(raw(materialized))
            rows = {r['id']: r for r in materialized['reservations']}
            self.assertEqual(materialized['status'], 'pass', materialized)
            self.assertEqual(rows['fixed']['polygons_nm'], fixed['geometry']['polygons_nm'])
            self.assertEqual(rows['fixed']['faces'], ['F'])
            for identifier in ('attached', 'pad-attached'):
                self.assertEqual(rows[identifier]['polygons_nm'], expected, (name, identifier, current))
                self.assertEqual(rows[identifier]['faces'], [current['side']])
            self.assertEqual(mechanics().grade(fresh, binding)['status'], 'pass')
        self.assertEqual(source.read_bytes(), source_bytes)

    def test_saved_native_missing_or_replaced_pad_cannot_pass_original_binding(self):
        require_toolchain(native=True)
        import pcbnew as k
        root = self.root()
        source = write_native_board(root)
        original = source.read_bytes()
        facts = extract(source)
        fp = facts['footprints'][0]
        pad = next(p for p in fp['pads'] if p['number'] == '1')
        binding = bound(facts, [reservation(frame='pad', self_exclusion=True,
            owner={'ref': 'U1', 'uuid': fp['uuid'], 'pad': {'number': '1', 'uuid': pad['uuid']}},
            polygons=[rectangle(0, 0, 2, 1)])])
        for mutation in ('removed', 'replaced-uuid'):
            native = k.LoadBoard(str(source))
            owner = next(f for f in native.GetFootprints() if f.GetReference() == 'U1')
            target = next(p for p in owner.Pads() if p.GetNumber() == '1')
            if mutation == 'removed':
                owner.Remove(target)
            else:
                target.SetUuidDirect(k.KIID(str(uuid.uuid5(NAMESPACE, 'replacement-pad'))))
            saved = root / f'{mutation}.kicad_pcb'
            self.assertTrue(k.SaveBoard(str(saved), native))
            fresh = extract(saved)
            result = mechanics().grade(fresh, binding)
            (root / f'{mutation}-grade.json').write_bytes(raw(result))
            self.assertEqual(result['status'], 'unknown', result)
            self.assertTrue(result['unknown'])
        self.assertEqual(source.read_bytes(), original)

    def test_existing_saved_judgment_rejects_mechanically_illegal_front_pose(self):
        require_toolchain(native=True)
        import pcbnew as k
        from agent_ee import placer_runner
        import underside_fixture as fixture
        root = self.root()
        source, _ = fixture.write_board(root / 'inputs', witness=True, spare_front=True)
        facts = extract(source)
        mechanical = bound(facts, [reservation()])
        self.assertEqual(mechanics().grade(facts, mechanical)['status'], 'pass')
        native = k.LoadBoard(str(source))
        for ref, x in (('U1', 9), ('C1', 11.5)):
            fp = next(f for f in native.GetFootprints() if f.GetReference() == ref)
            fp.Flip(fp.GetPosition(), k.FLIP_DIRECTION_LEFT_RIGHT)
            fp.SetOrientationDegrees(0)
            fp.SetPosition(k.VECTOR2I(round(x * MM), 1500000))
        saved = root / 'illegal-front.kicad_pcb'
        self.assertTrue(k.SaveBoard(str(saved), native))
        fresh = extract(saved)
        self.assertEqual(placement.judge(fresh)['verdict'], 'pass')
        plan = {'policy': request(mechanical_reservations=mechanical), 'placements': [
            {key: fp[key] for key in ('ref', 'uuid', 'position_nm', 'rotation_deg')}
            | {'fixed': True} for fp in fresh['footprints']]}
        (root / 'saved-plan.json').write_bytes(raw(plan))
        actual_facts, judgment = placer_runner._saved_judgment(saved, plan)
        (root / 'saved-judgment.json').write_bytes(raw(judgment))
        self.assertEqual(actual_facts['board_sha256'], fresh['board_sha256'])
        self.assertEqual(judgment['verdict'], 'hard_fail', judgment)
        self.assertEqual({row['ref'] for row in judgment['hard_failures']
                          if row['kind'] == 'mechanical_reservation_overlap'}, {'U1', 'C1'})

    def test_public_saved_check_binds_immutable_original_and_probes_current_candidate(self):
        require_toolchain(native=True)
        import pcbnew as k
        from agent_ee import candidate, checks
        from test_placement_gates_native import NativePlacementGates, DRC_STAGE, DRC_FULL, STOCK, placement as check
        from test_board_envelope import jlc_capture
        helper = NativePlacementGates()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        root = self.root()
        helper.root = root
        source = helper.source()
        authored = write_native_board(root / 'native-input')
        board = source / 'project/rc_filter.kicad_pcb'
        board.write_bytes(authored.read_bytes())
        original = source / 'project/mechanical-original.kicad_pcb'
        original.write_bytes(board.read_bytes())
        facts = extract(original)
        declared = document(facts, [reservation('fixed-service-lane', polygons=[rectangle(6, 6, 5, 2)])])
        declared['original_board_path'] = 'project/mechanical-original.kicad_pcb'
        declared['source']['path'] = 'requirements/authored-mechanics.txt'
        (source / declared['source']['path']).write_text(SOURCE_TEXT)
        (source / 'requirements/mechanical.json').write_bytes(raw(declared))
        # Reuse the captured, source-qualified profile for envelope coverage;
        # these mechanical tests make no manufacturing qualification claim.
        envelope_bytes, profile_bytes, _ = jlc_capture(source)
        envelope = json.loads(envelope_bytes)
        envelope['fixed_footprints'] = []
        points = [(0, 0), (20, 0), (20, 20), (0, 20)]
        envelope['outline']['edges'] = [{'name': name, 'start': points[i], 'end': points[(i + 1) % 4]}
                                      for i, name in enumerate(('top', 'right', 'bottom', 'left'))]
        envelope['mechanical_reservations'] = {'path': 'requirements/mechanical.json', 'sha256': sha(raw(declared))}
        (source / 'requirements/envelope.json').write_bytes(raw(envelope))
        specs = [copy.deepcopy(DRC_STAGE), check('mechanical', 'mechanical-access/1',
                                                envelope='requirements/envelope.json')]
        for field, criterion in (('outline', 'outline/1'), ('layer_count', 'layers/1'), ('stackup', 'stackup/1')):
            specs.append({'id': 'envelope-' + field, 'kind': 'board_envelope',
                          'params': {'criterion': criterion, 'envelope': 'requirements/envelope.json'}})
        specs.append({'id': 'envelope-fab_profile', 'kind': 'fab_profile',
                      'params': {'fab_profile': 'fab-profile.json'}})
        specs.append(copy.deepcopy(DRC_FULL))
        original_bytes = original.read_bytes()
        original_document = copy.deepcopy(declared)
        previous_binding = None
        declaration = {'schema': placement_adapter.ADAPTER_SCHEMA, 'helper': None, 'policy': None,
                       'fixed_footprints': [], 'timeout_s': 60}
        for state, expected in (('original', 'pass'), ('moved', 'defect'), ('stale-owner', 'unknown'),
                                ('current-as-original', 'unknown')):
            if state == 'moved':
                native = k.LoadBoard(str(board))
                owner = next(fp for fp in native.GetFootprints() if fp.GetReference() == 'U1')
                owner.SetPosition(k.VECTOR2I(8 * MM, 6 * MM))
                self.assertTrue(k.SaveBoard(str(board), native))
                self.assertNotEqual(sha(board.read_bytes()), sha(original_bytes))
            elif state == 'stale-owner':
                declared['reservations'] = [reservation('stale-attached-service-lane', frame='footprint',
                    owner={'ref': 'U1', 'uuid': str(uuid.uuid5(NAMESPACE, 'not-the-native-owner'))},
                    polygons=[rectangle(0, 0, 1, 1)], self_exclusion=True)]
                (source / 'requirements/mechanical.json').write_bytes(raw(declared))
                envelope['mechanical_reservations']['sha256'] = sha(raw(declared))
                (source / 'requirements/envelope.json').write_bytes(raw(envelope))
            elif state == 'current-as-original':
                # Every declared digest is valid. The invalid part is using the
                # mutable candidate itself as its supposedly immutable reference.
                board.write_bytes(original_bytes)
                declared = copy.deepcopy(original_document)
                declared['original_board_path'] = 'project/rc_filter.kicad_pcb'
                declared['original_board_sha256'] = sha(board.read_bytes())
                (source / 'requirements/mechanical.json').write_bytes(raw(declared))
                envelope['mechanical_reservations']['sha256'] = sha(raw(declared))
                (source / 'requirements/envelope.json').write_bytes(raw(envelope))
            files = {p.relative_to(source).as_posix(): sha(p.read_bytes())
                     for p in source.rglob('*') if p.is_file()}
            manifest = {'candidate_id': sha(board.read_bytes()), 'files': files, 'scope': {
                'board': 'project/rc_filter.kicad_pcb', 'schematic': 'project/rc_filter.kicad_sch',
                'checks': specs}}
            if state == 'stale-owner':
                with self.assertRaisesRegex(ValueError, 'owner ref/UUID differs'):
                    placement_adapter.binding(declaration, manifest, source, ['project/rc_filter.kicad_pcb'])
            elif state == 'current-as-original':
                self.assertEqual(files[declared['original_board_path']], declared['original_board_sha256'])
                with self.assertRaisesRegex(ValueError, 'sources in mutable inputs'):
                    placement_adapter.binding(declaration, manifest, source, ['project/rc_filter.kicad_pcb'])
            else:
                _, _, policy, _, _ = placement_adapter.binding(
                    declaration, manifest, source, ['project/rc_filter.kicad_pcb'])
                actual_binding = policy['mechanical_reservations']
                self.assertEqual(actual_binding['source_board_sha256'], sha(original_bytes))
                if previous_binding is not None:
                    self.assertEqual(actual_binding, previous_binding)
                previous_binding = copy.deepcopy(actual_binding)
            validation = board_envelope.validate(raw(envelope), profile=profile_bytes, captured=(files, source))
            requirements = board_envelope.derive_requirements(validation, 'requirements/envelope.json')['requirements']
            mapped = []
            for requirement in requirements:
                field = requirement['source']['locator'].split('/')[1]
                cid = 'mechanical' if field == 'mechanical_reservations' else 'envelope-' + field
                mapped.append({key: requirement[key] for key in ('id', 'statement', 'source')}
                              | {'intent': requirement.get('intent', 'acceptance'), 'checks': [cid]})
            mapped.append({'id': 'R-drc-placement', 'statement': 'Retain actual native placement DRC.',
                'source': {'path': 'requirements/placement.md', 'locator': 'authored native prerequisite'},
                'checks': ['drc-placement'], 'intent': 'acceptance'})
            mapped.append({'id': 'R-drc-final', 'statement': 'Retain subsequent full native DRC with connectivity.',
                'source': {'path': 'requirements/placement.md', 'locator': 'authored full native prerequisite'},
                'checks': ['drc-final'], 'intent': 'acceptance'})
            scope = {'schema': 'agent-ee.scope/3',
                'inputs': [{'path': str(source / 'project'), 'at': 'project'},
                           {'path': str(source / 'requirements'), 'at': 'requirements'},
                           {'path': str(source / 'sources'), 'at': 'sources'},
                           {'path': str(source / 'fab-profile.json'), 'at': 'fab-profile.json'}, *STOCK],
                'schematic': 'project/rc_filter.kicad_sch', 'board': 'project/rc_filter.kicad_pcb',
                'envelope': {'path': 'requirements/envelope.json', 'profile': 'fab-profile.json', 'dialog': None},
                'checks': specs, 'baseline': {'requirements': mapped,
                    'targets': ['requirements/placement.md'], 'reference_architectures': ['requirements/placement.md']}}
            scope_path = root / f'{state}-scope.json'
            scope_path.write_bytes(raw(scope))
            run = root / f'{state}-run'
            run.mkdir()
            if state == 'current-as-original':
                # Normal baseline capture already rejects this dependency.
                # Exercise the public check's independent guard too, with a
                # deliberately constructed, byte-consistent captured manifest.
                with self.assertRaisesRegex(ValueError, 'native design cannot be an immutable verification dependency'):
                    candidate.capture(scope_path, run)
                from agent_ee.storage import identity
                captured_files, sources, revisions = candidate.source_manifest(scope, scope_path.parent)
                self.assertEqual(captured_files, {p.relative_to(run / 'inputs').as_posix(): sha(p.read_bytes())
                    for p in (run / 'inputs').rglob('*') if p.is_file()})
                manual_manifest = {'schema': 'agent-ee.candidate/1', 'scope': scope, 'files': captured_files,
                    'sources': sources, 'source_revisions': revisions, 'scope_path': str(scope_path),
                    'scope_sha256': sha(scope_path.read_bytes()),
                    'candidate_id': identity({'scope': scope, 'files': captured_files})}
                (run / 'candidate.json').write_bytes(raw(manual_manifest))
            else:
                candidate.capture(scope_path, run)
            results = {row['id']: row for row in checks.run_checks(run, time.monotonic() + 120)}
            self.assertEqual(results['drc-placement']['execution'], 'ok', results['drc-placement'])
            self.assertEqual(results['drc-final']['execution'], 'ok', results['drc-final'])
            result = results['mechanical']
            expected_execution = 'unsupported' if state in ('stale-owner', 'current-as-original') else 'ok'
            self.assertEqual((result['execution'], result['outcome']), (expected_execution, expected), result)
            if state == 'current-as-original':
                self.assertIn('original_board_path must be distinct from the current candidate board', result['summary'])
                for artifact in ('mechanical-source-probe.json', 'placement-probe.json'):
                    self.assertNotIn('work/mechanical/' + artifact, result['evidence'])
                    self.assertFalse((run / 'work/mechanical' / artifact).exists())
                self.assertEqual(original.read_bytes(), original_bytes)
                continue
            self.assertIn('work/mechanical/mechanical-source-probe.json', result['evidence'])
            initial_probe = json.loads((run / 'work/mechanical/mechanical-source-probe.json').read_text())
            self.assertEqual(initial_probe['board_sha256'], sha(original_bytes))
            if state == 'stale-owner':
                self.assertIn('mechanical source binding unavailable', result['summary'])
                self.assertNotIn('work/mechanical/placement-probe.json', result['evidence'])
                self.assertFalse((run / 'work/mechanical/placement-probe.json').exists())
            else:
                self.assertIn('work/mechanical/placement-probe.json', result['evidence'])
                current_probe = json.loads((run / 'work/mechanical/placement-probe.json').read_text())
                self.assertEqual(current_probe['board_sha256'], sha(board.read_bytes()))
                self.assertEqual(result['details']['reservations'][0]['source']['classification'], 'engineering')
            self.assertEqual(original.read_bytes(), original_bytes)
            if state == 'moved':
                original.write_bytes(original_bytes + b'\n')
                try:
                    with self.assertRaises(ValueError):
                        placement_adapter.binding(declaration, manifest, source, ['project/rc_filter.kicad_pcb'])
                finally:
                    original.write_bytes(original_bytes)

    def test_search_selects_legal_underside_and_saved_illegal_flip_is_rejected(self):
        require_toolchain(native=True)
        import pcbnew as k
        from agent_ee import block_contract, block_variants, blocks, placement_groups, placer_runner
        from agent_ee import placement_rust_runtime
        from agent_ee.native_netlist import read_native_inventory, read_sheet_names
        from scripts.placement_engine_parity import first_difference, normalized
        import underside_fixture as fixture

        root = self.root()
        source, netlist = fixture.write_board(root / 'inputs', spare_front=True)
        facts = extract(source)
        original = source.read_bytes()
        mechanical = bound(facts, [reservation()])
        policy = fixture.policy()
        policy['mechanical_reservations'] = mechanical
        allocation = fixture.allocation_text()
        inventory = read_native_inventory(netlist)
        derivation = blocks.derive(inventory, read_sheet_names(netlist), facts,
            inventory_sha256=sha(netlist.read_bytes()), allocation_raw=allocation.encode())
        eligibility = fixture.eligibility(facts['board_sha256'])
        candidates = [block_variants.produce(source, facts, inventory, derivation, block['id'], policy,
            eligibility, root / 'variants' / block['id'], compact=False) for block in derivation['blocks']]
        self.assertTrue(all(row['status'] == 'packed'
                            for bundle in candidates for row in bundle['variants'].values()))
        proposed = {'policy': policy, 'blocks': {'arrangement': fixture.arrangement_policy(),
            'allocation_text': allocation, 'side_eligibility': eligibility,
            'contract': fixture.contract(facts['board_sha256'], eligibility, policy),
            'candidates': [{'candidate': bundle} for bundle in candidates]}}
        for name, value in (('facts', facts), ('request', proposed), ('mechanical-binding', mechanical)):
            (root / f'{name}.json').write_bytes(raw(value))
        plan = placer_runner._plan_local(facts, proposed, netlist=netlist, heartbeat=lambda _: None)
        (root / 'python-plan.json').write_bytes(raw(blocks._plain(plan)))
        self.assertEqual(plan['status'], 'planned', plan)
        # The ordinary spare-front case selects F. The added mechanical
        # projection closes x=8..12.5 on F; R1 must use the other front slot.
        # A full legal result must retain the block on the unobstructed B face.
        block = next(row for row in plan['block_arrangement']['arranged'] if row['block'] == 'b')
        self.assertEqual(block['selected_face'], 'B', plan)
        self.assertEqual(plan['block_arrangement']['contract_check']['status'], 'pass')
        self.assertTrue(placement_groups.satisfied(plan['group_grading']))
        session = placement_rust_runtime.Session(placement_rust_runtime.resolve_executable())
        rust_plan = session.plan(facts, proposed, None, netlist, heartbeat=lambda _: None)
        (root / 'rust-plan.json').write_bytes(raw(blocks._plain(rust_plan)))
        (root / 'rust-execution.json').write_bytes(raw(session.calls))
        self.assertIsNone(first_difference(normalized(plan), normalized(rust_plan)))
        self.assertEqual(session.calls[-1]['status'], 'ok')

        writable = root / 'published'
        writable.mkdir()
        approved = {**copy.deepcopy(proposed), 'skip_visuals': True, 'authorization': {
            'schema': 'agent-ee.placement-write/1', 'single_design_writer': True,
            'writable_root': str(writable.resolve()), 'source_sha256': facts['board_sha256']}}
        output = writable / 'candidate'
        result = placer_runner.place_candidate(source, netlist, output, approved)
        self.assertEqual(result['status'], 'placed', result)
        saved = output / 'candidate.kicad_pcb'
        fresh = extract(saved)
        self.assertEqual(mechanics().grade(fresh, mechanical)['status'], 'pass')
        contract = block_contract.judge(fresh, proposed['blocks']['contract'], original_capture=facts)
        self.assertEqual(contract['status'], 'pass', contract)
        self.assertEqual({row['classification'] for row in contract['relationships']}, {'same_face'})
        self.assertTrue(all(not row['exceeded'] for row in contract['relationships']))
        self.assertEqual({fp['ref']: fp['side'] for fp in fresh['footprints'] if fp['ref'] in ('U1', 'C1')},
                         {'U1': 'B', 'C1': 'B'})

        # Preserve the block and electrical bounds while moving it into the
        # mechanically forbidden spare slot on F. Native courtyard clearance
        # alone cannot reject this saved mutation.
        native = k.LoadBoard(str(saved))
        for ref, x in (('U1', 9), ('C1', 11.5)):
            fp = next(fp for fp in native.GetFootprints() if fp.GetReference() == ref)
            self.assertTrue(fp.IsFlipped())
            fp.Flip(fp.GetPosition(), k.FLIP_DIRECTION_LEFT_RIGHT)
            fp.SetOrientationDegrees(0)
            fp.SetPosition(k.VECTOR2I(round(x * MM), 1500000))
        illegal = root / 'illegal-front.kicad_pcb'
        self.assertTrue(k.SaveBoard(str(illegal), native))
        changed = extract(illegal)
        self.assertEqual(block_contract.judge(changed, proposed['blocks']['contract'], original_capture=facts)['status'], 'pass')
        self.assertEqual(placement.judge(changed)['verdict'], 'pass')
        rejected = mechanics().grade(changed, mechanical)
        (root / 'illegal-mechanical-grade.json').write_bytes(raw(rejected))
        self.assertEqual(rejected['status'], 'defect')
        self.assertEqual({row['ref'] for row in rejected['defects']}, {'U1', 'C1'})
        final = placement.judge(changed, mechanical=mechanical)
        self.assertEqual(final['verdict'], 'hard_fail')
        self.assertIn('mechanical_reservation_overlap', {row['kind'] for row in final['hard_failures']})
        self.assertEqual(source.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
