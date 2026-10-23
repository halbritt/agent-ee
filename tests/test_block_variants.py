"""Pure candidate-set integrity and writer authority tests on owned block fixtures.

Native face production and congested-board integration use the separate AGENTEE-479
native fixture. These tests do not claim native qualification for their hash stubs.
"""
import copy
import hashlib
import tempfile
from pathlib import Path
import unittest

from agent_ee import blocks, block_variants
from agent_ee.footprint_library import digest
from test_blocks import Fixture, policy


class CandidateSetIntegrityTest(Fixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.source = self.dir / 'source.kicad_pcb'
        self.source.write_bytes(b'owned pure-integrity fixture; not a native board')
        self.board['board_sha256'] = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.inventory_data, _, _ = self.inventory()
        self.derivation = self.derive()
        self.policy = policy(electrical_locality=None)
        self.eligibility = {'schema': 'fome-mc.side-eligibility/1',
            'sources': {'board_sha256': self.board['board_sha256']},
            'default': {'faces': ['F', 'B']},
            'blocks': {'Power': {'eligible_faces': ['F', 'B']}}}
        packed = blocks._plain(blocks.pack_block(self.board, self.inventory_data, self.derivation,
                                                'Power', self.policy, compact=False))
        self.assertEqual(packed['status'], 'packed')
        self.candidate = packed['candidate']
        members = [fp for fp in self.board['footprints']
                   if fp['ref'] in {row['reference'] for row in self.candidate['members']}]
        members.sort(key=lambda fp: blocks._natural(fp['ref']))
        original = [{'ref': fp['ref'], 'uuid': fp['uuid'], 'side': fp['side']} for fp in members]
        def rows(face, native_hash):
            return [{'ref': fp['ref'], 'uuid': fp['uuid'], 'source_side': fp['side'], 'side': face,
                     'flipped': fp['side'] != face, 'pad_inventory_sha256': native_hash} for fp in members]
        self.document = {'schema': block_variants.SCHEMA, 'block_id': 'Power',
            'sources': self.candidate['sources'],
            'eligibility': {'record': self.eligibility, 'record_sha256': digest(self.eligibility),
                            'eligible_faces': ['F', 'B']},
            'captured_members': original,
            'variants': {
                'F': {'status': 'packed', 'capture': self.board, 'capture_sha256': digest(self.board),
                      'board_sha256': self.board['board_sha256'], 'derivation': self.derivation,
                      'members': rows('F', 'a' * 64), 'candidate': self.candidate, 'failure': None},
                'B': {'status': 'native_flip_refused', 'capture': None, 'capture_sha256': None,
                      'board_sha256': None, 'derivation': None, 'members': rows('B', None), 'candidate': None,
                      'failure': {'status': 'native_flip_refused', 'outcome': 'unknown',
                                  'reason': 'owned explicit failed-face fixture'}}}}

    def validate(self, document):
        block_variants.validate(document, self.board, self.inventory_data, self.derivation,
                                self.policy, self.eligibility)

    def test_unchanged_inner_candidate_and_failed_eligible_face_are_retained(self):
        self.validate(self.document)
        self.assertEqual(self.document['variants']['F']['candidate'], self.candidate)
        self.assertEqual(self.document['variants']['B']['failure']['status'], 'native_flip_refused')
        mutated = copy.deepcopy(self.document)
        del mutated['variants']['B']
        with self.assertRaisesRegex(ValueError, 'every eligible face'):
            self.validate(mutated)

    def test_source_and_identity_mutations_refuse(self):
        changes = [
            lambda d: d['sources'].update(policy_sha256='0' * 64),
            lambda d: d['eligibility']['record'].update(count_balance='invented quota'),
            lambda d: d['captured_members'][0].update(uuid='other-footprint'),
            lambda d: d['variants']['F']['members'][0].update(flipped=True),
            lambda d: d['variants']['F']['capture']['footprints'][0].update(value='forged'),
            lambda d: d['variants']['F']['derivation'].update(unexpected='field'),
            lambda d: d['variants']['B'].update(candidate=d['variants']['F']['candidate']),
        ]
        for mutate in changes:
            document = copy.deepcopy(self.document)
            mutate(document)
            with self.subTest(mutation=changes.index(mutate)), self.assertRaises(ValueError):
                self.validate(document)

    def test_eligibility_requires_original_board_binding_and_explicit_restriction(self):
        document = copy.deepcopy(self.document)
        document['eligibility']['record']['sources']['board_sha256'] = '0' * 64
        document['eligibility']['record_sha256'] = digest(document['eligibility']['record'])
        with self.assertRaisesRegex(ValueError, 'original board'):
            block_variants._structure(document, self.board)
        restricted = copy.deepcopy(self.eligibility)
        restricted['blocks']['Power']['eligible_faces'] = ['F']
        with self.assertRaisesRegex(ValueError, 'explicit face restrictions'):
            block_variants.eligible_faces(restricted, 'Power')

    def test_side_writer_requires_exact_selected_variant_and_all_original_uuids(self):
        plan = {'placements': [{key: copy.deepcopy(fp[key]) for key in ('ref', 'uuid', 'side')}
                               | {'fixed': False} for fp in self.board['footprints']],
                'block_arrangement': {
                    'arranged': [{'block': 'Power', 'selected_face': 'F'}],
                    'blocks': [{'block': 'Power', 'selected_face': 'F',
                                'candidate_sha256': digest(self.candidate),
                                'candidate_set_sha256': digest(self.document)}]}}
        self.assertIsNone(block_variants.authorization(self.source, self.board, [self.document], plan,
                                                      self.eligibility))
        cases = []
        changed = copy.deepcopy(plan)
        changed['block_arrangement']['arranged'][0]['selected_face'] = 'B'
        cases.append(changed)
        changed = copy.deepcopy(plan)
        changed['block_arrangement']['blocks'][0]['candidate_set_sha256'] = '0' * 64
        cases.append(changed)
        changed = copy.deepcopy(plan)
        changed['placements'][0]['side'] = 'B'
        cases.append(changed)
        changed = copy.deepcopy(plan)
        changed['placements'].pop()
        cases.append(changed)
        for changed in cases:
            with self.assertRaises(ValueError):
                block_variants.authorization(self.source, self.board, [self.document], changed, self.eligibility)


class NativeVariantVerificationTest(unittest.TestCase):
    def test_native_recapture_rejects_forged_geometry_even_with_updated_hash(self):
        from toolchain import require_toolchain
        require_toolchain(native=True)
        import underside_fixture
        from agent_ee.native_netlist import read_native_inventory, read_sheet_names
        from agent_ee.placement_probe import extract
        from agent_ee.placer_runner import _side_changes
        with tempfile.TemporaryDirectory(prefix='agent-ee-variant-proof-') as directory:
            root = Path(directory)
            source, netlist = underside_fixture.write_board(root / 'input')
            facts = extract(source)
            original_bytes = source.read_bytes()
            inventory = read_native_inventory(netlist)
            derivation = blocks.derive(inventory, read_sheet_names(netlist), facts,
                                       inventory_sha256=hashlib.sha256(netlist.read_bytes()).hexdigest())
            eligibility = underside_fixture.eligibility(facts['board_sha256'])
            document = block_variants.produce(source, facts, inventory, derivation, 'b',
                underside_fixture.policy(), eligibility, root / 'variants', compact=False)
            self.assertEqual({face: row['status'] for face, row in document['variants'].items()},
                             {'F': 'packed', 'B': 'packed'})
            block_variants.verify_native(source, document, facts)
            forged = copy.deepcopy(document)
            forged['variants']['B']['capture']['footprints'][0]['position_nm'][0] += 1
            forged['variants']['B']['capture_sha256'] = digest(forged['variants']['B']['capture'])
            with self.assertRaisesRegex(ValueError, 'reproduced native capture'):
                block_variants.verify_native(source, forged, facts)
            forged = copy.deepcopy(document)
            forged['variants']['B']['members'][0]['pad_inventory_sha256'] = '0' * 64
            with self.assertRaisesRegex(ValueError, 'reproduced native capture'):
                block_variants.verify_native(source, forged, facts)
            variant = document['variants']['B']
            selected_sides = {row['uuid']: row['side'] for row in variant['members']}
            plan = {'placements': [{'ref': fp['ref'], 'uuid': fp['uuid'],
                                    'side': selected_sides.get(fp['uuid'], fp['side']), 'fixed': False}
                                   for fp in facts['footprints']],
                    'block_arrangement': {
                        'arranged': [{'block': 'b', 'selected_face': 'B'}],
                        'blocks': [{'block': 'b', 'selected_face': 'B',
                                    'candidate_sha256': digest(variant['candidate']),
                                    'candidate_set_sha256': digest(document)}]}}
            authorized = block_variants.authorization(source, facts, [document], plan, eligibility)
            self.assertEqual({row['ref'] for row in authorized['changes']}, {'U1', 'C1'})
            self.assertEqual(_side_changes(source, authorized, plan), selected_sides)
            self.assertEqual(source.read_bytes(), original_bytes)


if __name__ == '__main__':
    unittest.main()
