"""Block provenance review input: arranged plan to sealed candidate to initial reviewers (AGENTEE-355).

Every fixture is original: the authored AGENTEE-339 sheets, parts, pads and nets, an
AGENTEE-342 library entry imported from an authored cluster, and authored text files
standing in for the reviewed board. No model is called: the reviewer transport is
replaced in the test only, to read the exact prompt each initial reviewer receives.
"""
import copy
import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_ee import block_arrangement as arrangement, block_library as bl, blocks
from agent_ee.candidate import capture
from agent_ee.cli import validate_scope
from agent_ee.reviewers import REVIEW_VERSION, build_packet, initial_reviews
from test_block_arrangement import plan_policy
from test_block_library import LibraryFixture
from test_blocks import PARTS, SHEETS, board, native_xml
from test_placement import rectangle

ROOT = Path(__file__).resolve().parents[1]
MAPPING = {'U1': 'U11', 'C1': 'C11', 'L1': 'L11', 'C3': 'C13'}
EXTRA = (('U11', '/s4/', 'BUCK', 'Owned:QFN4', 'owned-buck', {'1': 'VIN', '2': 'GND', '3': 'SW2', '4': 'V2'}),
         ('C11', '/s4/', 'C', 'Owned:C0805', '4.7uF', {'1': 'VIN', '2': 'GND'}),
         ('L11', '/s4/', 'L', 'Owned:L1210', '10uH', {'1': 'SW2', '2': 'V2'}),
         ('C13', '/s4/', 'C', 'Owned:C0805', '22uF', {'1': 'V2', '2': 'GND'}),
         ('R9', '/', 'R', 'Owned:R0603', '1k', {'1': 'V2', '2': 'GND'}))


class ProvenanceFixture(LibraryFixture):
    def setUp(self):
        super().setUp()
        # A library entry (search-derived, advisory) materialized on its source board ...
        self.entry = self.cluster()
        source = bl.materialize(self.entry, self.board, self.inv, self.names, self.sha, self.policy)
        self.assertEqual(source['status'], 'materialized', source['problems'])
        self.library_candidate = source['candidate']
        # ... and reused, mapped, as the second power stage of a target board.
        parts = PARTS + EXTRA
        self.target = board(parts)
        self.target['outline']['polygons'] = [rectangle(0, 0, 60, 40)]
        self.target_inv, names, sha = self.inventory(native_xml(parts, sheets={**SHEETS, '/s4/': '/Power2/'}))
        self.target_derivation = blocks.derive(self.target_inv, names, self.target, inventory_sha256=sha)
        self.own = {b: blocks.pack_block(self.target, self.target_inv, self.target_derivation, b,
                                         self.policy)['candidate'] for b in ('Power', 'Sensor')}
        entries = [{'candidate': c} for c in self.own.values()] + [
            {'candidate': self.library_candidate, 'mapping': MAPPING,
             'boundary_nets': {'n1': 'VIN', 'n2': 'GND', 'n4': 'V2'}}]
        self.plan = arrangement.arrange(self.target, self.target_inv, self.target_derivation, self.policy,
                                        plan_policy(), entries)
        self.assertEqual(self.plan['status'], 'planned', self.plan.get('reason'))

    def expected(self):
        """Each placed block's identity, class and enforcement, from its own admitted candidate."""
        candidates = {**self.own, 'Power2': self.library_candidate}
        refs = {b['id']: b['references'] for b in self.target_derivation['blocks']}
        return {bid: {'references': refs[bid], 'candidate_sha256': blocks.digest(c),
                      'class': c['provenance']['class'], 'enforcement': c['provenance']['enforcement'],
                      'citation': c['provenance']['citation'], 'method': c['provenance']['method']}
                for bid, c in candidates.items()}


class ArtifactTests(ProvenanceFixture, unittest.TestCase):
    def test_every_placed_block_with_library_mapping(self):
        artifact = arrangement.provenance_artifact(self.plan)
        rows = {r['block']: r for r in artifact['blocks']}
        self.assertEqual(set(rows), {b['id'] for b in self.target_derivation['blocks']})
        for bid, expected in self.expected().items():
            self.assertEqual({k: rows[bid][k] for k in expected}, expected, bid)
        # The library block is mapped from the entry's references and keeps its library method.
        self.assertEqual(rows['Power2']['reuse'], 'mapped')
        self.assertEqual(rows['Power2']['mapping'], MAPPING)
        self.assertEqual((rows['Power2']['class'], rows['Power2']['enforcement'], rows['Power2']['method']),
                         ('search-derived', 'advisory', 'project-cluster/1'))
        self.assertEqual(rows['Power2']['citation'], self.entry['provenance']['citation'])
        self.assertEqual({rows[b]['reuse'] for b in ('Power', 'Sensor')}, {'own-board'})
        self.assertTrue(all('mapping' not in rows[b] for b in ('Power', 'Sensor')))
        arranged = {a['block']: a for a in self.plan['block_arrangement']['arranged']}
        for bid, row in rows.items():
            self.assertEqual((row['position_nm'], row['rotation_deg']),
                             (arranged[bid]['position_nm'], arranged[bid]['rotation_deg']))
        self.assertEqual(artifact['board_sha256'], self.plan['board_sha256'])
        self.assertEqual(artifact['authority'], arrangement.PROVENANCE_AUTHORITY)
        # The plan record itself keeps the mapping of the reused block only.
        record = {b['block']: b for b in self.plan['block_arrangement']['blocks']}
        self.assertEqual(record['Power2']['mapping'], MAPPING)
        self.assertNotIn('mapping', record['Power'])
        self.assertEqual(arrangement.provenance_artifact(copy.deepcopy(self.plan)), artifact)

    def test_refusals_fail_closed(self):
        artifact = arrangement.provenance_artifact(self.plan)
        failed = arrangement.arrange(self.target, self.target_inv, self.target_derivation, self.policy,
                                     plan_policy(), [{'candidate': c} for c in self.own.values()])
        self.assertEqual(failed['status'], 'arrangement_refused')
        with self.assertRaisesRegex(ValueError, 'complete arranged plan'):
            arrangement.provenance_artifact(failed)

        def edited(change):
            value = copy.deepcopy(artifact)
            change(value)
            return value
        power2 = next(i for i, r in enumerate(artifact['blocks']) if r['block'] == 'Power2')
        cases = {
            'relabelled hard': lambda v: v['blocks'][0].update(enforcement='hard'),
            'unknown class': lambda v: v['blocks'][0].update({'class': 'agent-approved'}),
            'hard class': lambda v: v['blocks'][0].update({'class': 'qualified-owner-board'}),
            'mapping dropped': lambda v: v['blocks'][power2].pop('mapping'),
            'own-board mapped': lambda v: v['blocks'][power2].update(reuse='own-board'),
            'mapping off target': lambda v: v['blocks'][power2]['mapping'].update(C3='R9'),
            'block missing': lambda v: v.update(blocks=[]),
            'shared reference': lambda v: v['blocks'][1]['references'].append(v['blocks'][0]['references'][0]),
            'authority edited': lambda v: v.update(authority='accepted'),
            'no citation': lambda v: v['blocks'][0].update(citation=' '),
            'extra key': lambda v: v['blocks'][0].update(accepted=True),
            'bad board hash': lambda v: v.update(board_sha256='0' * 63),
            'rule status': lambda v: v.update(rules=[{'rule': 'keepout', 'enforcement': 'hard', 'status': 'unknown'}]),
        }
        # A sourced hard class is valid only with its hard enforcement; relabelling it advisory refuses too.
        for name, change in cases.items():
            with self.subTest(name), self.assertRaises(ValueError):
                arrangement.validate_provenance(edited(change))
        hard = edited(lambda v: v['blocks'][0].update({'class': 'qualified-owner-board', 'enforcement': 'hard'}))
        self.assertEqual(arrangement.validate_provenance(hard)['blocks'][0]['enforcement'], 'hard')

    def test_cli_writes_the_artifact_beside_a_planned_result(self):
        root = self.dir / 'cli'
        root.mkdir()
        (root / 'probe.json').write_text(json.dumps(self.target))
        (root / 'design.xml').write_bytes(native_xml(PARTS + EXTRA, sheets={**SHEETS, '/s4/': '/Power2/'}))
        (root / 'policy.json').write_text(json.dumps(self.policy))
        (root / 'arrangement.json').write_text(json.dumps(plan_policy()))
        paths = []
        for name, candidate in self.own.items():
            paths += ['--candidate', str(root / f'{name}.json')]
            (root / f'{name}.json').write_text(json.dumps(candidate))
        args = ['--probe', str(root / 'probe.json'), '--netlist', str(root / 'design.xml'),
                '--policy', str(root / 'policy.json'), '--arrangement', str(root / 'arrangement.json'),
                '--out', str(root / 'plan.json'), '--provenance', str(root / 'provenance.json')]
        # Only own-board candidates are files: Power2 has none, so nothing is planned or written.
        self.assertEqual(arrangement.main(args + paths), 1)
        self.assertFalse((root / 'provenance.json').exists())
        (root / 'plan.json').unlink()
        two = native_xml(PARTS)
        (root / 'design.xml').write_bytes(two)
        target = board(PARTS)
        target['outline']['polygons'] = [rectangle(0, 0, 60, 40)]
        (root / 'probe.json').write_text(json.dumps(target))
        inv, names, sha = self.inventory(two)
        derivation = blocks.derive(inv, names, target, inventory_sha256=sha)
        for name in ('Power', 'Sensor'):
            (root / f'{name}.json').write_text(json.dumps(
                blocks.pack_block(target, inv, derivation, name, self.policy)['candidate']))
        self.assertEqual(arrangement.main(args + paths), 0)
        plan = json.loads((root / 'plan.json').read_text())
        written = arrangement.validate_provenance(json.loads((root / 'provenance.json').read_text()))
        self.assertEqual([r['block'] for r in written['blocks']], ['Power', 'Sensor'])
        self.assertEqual(written['board_sha256'], plan['board_sha256'])


class ReviewInputTests(ProvenanceFixture, unittest.TestCase):
    """The artifact is a sealed candidate input; build_packet binds it and every initial reviewer receives it."""

    def scope(self, artifact, board_text='(kicad_pcb (version 20260206) (generator "owned"))\n', **changes):
        root = Path(tempfile.mkdtemp(prefix='agent-ee-provenance-review-', dir=self.dir))
        source = root / 'source'
        source.mkdir()
        (source / 'pod.kicad_sch').write_text('(kicad_sch (version 20260206))\n')
        (source / 'pod.kicad_pcb').write_text(board_text)
        (source / 'block-provenance.json').write_text(json.dumps(artifact, sort_keys=True))
        scope = {'schema': 'agent-ee.scope/1', 'name': 'arranged-blocks', 'scope': 'Arranged block placement review',
                 'schematic': 'project/pod.kicad_sch', 'board': 'project/pod.kicad_pcb',
                 'block_provenance': 'project/block-provenance.json',
                 'checks': [{'id': 'drc', 'kind': 'drc'}], 'inputs': [{'path': 'source', 'at': 'project'}],
                 'review_profile': json.loads((ROOT / 'profiles/claude.json').read_text())}
        scope.update(changes)
        scope = {k: v for k, v in scope.items() if v is not None}
        validate_scope(scope)
        (root / 'scope.json').write_text(json.dumps(scope))
        run = root / 'run'
        run.mkdir()
        manifest = capture(root / 'scope.json', run)
        return run, manifest, source

    def bound(self, board_text='(kicad_pcb (version 20260206) (generator "owned"))\n'):
        return arrangement.provenance_artifact(
            self.plan, hashlib.sha256(board_text.encode()).hexdigest(),
            plan_sha256=blocks.digest(self.plan['block_arrangement']))

    def test_every_initial_reviewer_receives_every_block(self):
        artifact = self.bound()
        run, manifest, _ = self.scope(artifact)
        packet = build_packet(run, [], 'run-355')
        carried = packet['block_provenance']
        self.assertEqual(carried['path'], 'inputs/project/block-provenance.json')
        self.assertEqual(carried['sha256'], manifest['files']['project/block-provenance.json'])
        self.assertEqual(carried['record'], artifact)
        # The sealed input is part of candidate identity and of the reviewers' citation inventory.
        self.assertIn('project/block-provenance.json', manifest['files'])
        self.assertIn('inputs/project/block-provenance.json', [f['path'] for f in packet['citation_files']])
        prompts = {}

        def transport(run_dir, role, definition, prompt, schema, timeout, **kwargs):
            prompts[role] = prompt
            return {'execution': 'ok'}, {'schema': REVIEW_VERSION, 'candidate_id': packet['candidate_id'],
                                         'run_id': packet['run_id'], 'packet_sha256': packet['packet_sha256'],
                                         'role': role, 'coverage': ['block provenance read'],
                                         'limitations': [], 'findings': []}
        with patch('agent_ee.reviewers.reviewer_call', side_effect=transport):
            reviews, records = initial_reviews(run, packet, time.monotonic() + 30)
        roles = [r['role'] for r in manifest['scope']['review_profile']['reviewers']]
        self.assertEqual(sorted(prompts), sorted(roles))
        self.assertEqual(sorted(r['role'] for r in reviews), sorted(roles), records)
        expected = self.expected()
        for role, prompt in prompts.items():
            with self.subTest(role):
                received = json.loads(prompt.split('PACKET:\n', 1)[1])
                rows = {r['block']: r for r in received['block_provenance']['record']['blocks']}
                self.assertEqual(set(rows), set(expected))
                for bid, values in expected.items():
                    self.assertEqual({k: rows[bid][k] for k in values}, values)
                self.assertEqual(rows['Power2']['mapping'], MAPPING)
                self.assertEqual(rows['Power2']['method'], 'project-cluster/1')
                self.assertEqual({r['enforcement'] for r in rows.values()}, {'advisory'})
                # Independence: the same packet, and no peer's judgment or review.
                self.assertEqual(received, packet)
                for other in set(roles) - {role}:
                    self.assertNotIn(f'"role": "{other}"', prompt)

    def test_the_packet_is_not_built_without_a_bound_artifact(self):
        artifact = self.bound()
        run, _, _ = self.scope(artifact, board_text='(kicad_pcb (version 20260206) (generator "other"))\n')
        with self.assertRaisesRegex(ValueError, 'not the captured project/pod.kicad_pcb'):
            build_packet(run, [], 'run-355')
        self.assertFalse((run / 'packet.json').exists())
        run, _, _ = self.scope(artifact, block_provenance='project/missing.json')
        with self.assertRaisesRegex(ValueError, 'not a captured input'):
            build_packet(run, [], 'run-355')
        run, _, _ = self.scope(artifact)
        tampered = run / 'inputs/project/block-provenance.json'
        tampered.write_text(tampered.read_text().replace('"advisory"', '"hard"', 1))
        with self.assertRaisesRegex(ValueError, 'changed'):
            build_packet(run, [], 'run-355')
        relabelled = copy.deepcopy(artifact)
        relabelled['blocks'][0]['enforcement'] = 'hard'
        run, _, _ = self.scope(relabelled)
        with self.assertRaisesRegex(ValueError, 'does not carry enforcement'):
            build_packet(run, [], 'run-355')
        for bad in ('', ['project/block-provenance.json'], '../escape.json'):
            with self.subTest(bad), self.assertRaises(ValueError):
                validate_scope({'name': 'n', 'scope': 's', 'schematic': 'a.kicad_sch', 'board': 'a.kicad_pcb',
                                'block_provenance': bad, 'checks': [{'id': 'drc', 'kind': 'drc'}]})
        # Without the declaration the packet carries no block provenance at all.
        run, _, _ = self.scope(artifact, block_provenance=None)
        self.assertNotIn('block_provenance', build_packet(run, [], 'run-355'))


if __name__ == '__main__':
    unittest.main()
