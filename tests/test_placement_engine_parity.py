"""Complete Rust plans against the current, independent Python reference.

These authored cases exercise decisions, evidence and refusals, not throughput.
Only the arrangement's elapsed clock is normalized; ordering, numeric types,
placements, budgets, proofs and diagnostics must agree exactly.
"""
import copy
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from agent_ee import blocks, electrical_locality, locality_provenance, placer_runner
from agent_ee import placement_rust_runtime, placement_runtime
from agent_ee.native_netlist import read_native_inventory, read_sheet_names
from scripts.placement_engine_parity import first_difference, normalized
from test_placement import MM, capture, part, rectangle
from test_placer import request


def _source(text, path='fixture-sources/rust-planner-distance.txt'):
    return {'path': path, 'sha256': hashlib.sha256(text.encode()).hexdigest(),
            'locator': 'authored regression distance', 'quote': text}


def _locality(text='Original synthetic maximum: 1 mm.', limit=MM, provenance=None):
    """A captured numeric fixture, without process-local owner authority pins."""
    source = _source(text)
    provenance = provenance or {'class': 'datasheet', 'basis': 'Authored regression quantity',
        'derivation': {'kind': 'numeric', 'value_nm': limit, 'source': source}}
    pairs = {}
    for role, pad, net in [('supply', '1', 'VCC'), ('return', '2', 'GND')]:
        pairs[role] = {'from': {'reference': 'U1', 'pad': pad, 'net': net},
                       'to': {'reference': 'C1', 'pad': pad, 'net': net},
                       'max_distance_nm': limit, 'citation': 'Authored differential fixture',
                       'provenance': copy.deepcopy(provenance)}
    return {'schema': electrical_locality.SCHEMA, 'source_records': {source['path']: text},
            'obligations': [{'id': 'distance', 'metric': 'pad-centre-to-centre', **pairs}]}


def _captured_block_limits(document):
    """Keep the authored block limits; cite captured quotes in both processes.

    owner_fixture registers test-only Python pins. They are not production
    authority and must not be transmitted as authority to the Rust process.
    """
    document = copy.deepcopy(document)
    document['source_records'] = {}
    for obligation in document['obligations']:
        for role in electrical_locality.roles(obligation):
            pair = obligation[role]
            limit = pair['max_distance_nm']
            text = f'Original synthetic block maximum: {limit} nm.'
            source = _source(text, f'fixture-sources/block-{limit}.txt')
            document['source_records'][source['path']] = text
            pair['provenance'] = {'class': 'datasheet', 'basis': 'Authored block regression limit',
                'derivation': {'kind': 'numeric', 'value_nm': limit, 'source': source}}
    return document


class EngineFixture:
    @classmethod
    def setUpClass(cls):
        try:
            cls.binary = placement_rust_runtime.resolve_executable()
        except (placement_runtime.PlanningProcessError, OSError) as error:
            # A configured path is an assertion, not an optional prerequisite.
            if os.environ.get('AGENT_EE_REQUIRE_TOOLCHAIN') == '1' or os.environ.get('AGENT_EE_RUST_PLANNER'):
                raise AssertionError(f'Rust planner prerequisite: {error}') from error
            raise unittest.SkipTest(str(error)) from error

    def compare(self, facts, request_, *, policy=None, netlist_xml=None):
        original = copy.deepcopy([facts, request_, policy])
        with tempfile.TemporaryDirectory(prefix='agent-ee-planner-parity-') as directory:
            netlist = None
            if netlist_xml is not None:
                netlist = Path(directory) / 'native.xml'
                netlist.write_bytes(netlist_xml.encode('utf-8'))
            try:
                expected = {'plan': normalized(placer_runner._plan_local(
                    facts, request_, policy, netlist, heartbeat=lambda _: None))}
            except ValueError as error:
                expected = {'error': str(error)}
            self.assertIsNone(first_difference(original, [facts, request_, policy]))
            if netlist is not None:
                self.assertEqual(netlist.read_bytes(), netlist_xml.encode('utf-8'))
        payload = {'schema': 'agent-ee.rust-planning-input/1', 'facts': facts,
                   'request': request_, 'policy': policy, 'netlist_xml': netlist_xml}
        process = subprocess.run([str(self.binary)], input=json.dumps(payload, allow_nan=False).encode(),
                                 capture_output=True, timeout=120)
        self.assertEqual(len(process.stdout.splitlines()), 1, process.stderr.decode(errors='replace'))
        response = json.loads(process.stdout)
        self.assertEqual(response.get('schema'), 'agent-ee.rust-planning-output/1')
        if 'error' in response:
            self.assertEqual(response.get('error_type'), 'ValueError')
            actual = {'error': response['error']}
        else:
            actual = {'plan': normalized(response['plan'])}
        self.assertEqual(process.returncode, 1 if 'error' in expected else 0,
                         process.stderr.decode(errors='replace'))
        self.assertIsNone(first_difference(expected, actual))
        return actual.get('plan', actual)


class WholePlannerParityTest(EngineFixture, unittest.TestCase):
    def test_backjump_anchor_and_candidate_budget(self):
        from test_placer_search import anchor_case, promotion_case
        for name, factory, budget in [('promotion', promotion_case, None),
                                      ('anchor', anchor_case, None), ('budget', promotion_case, 1)]:
            with self.subTest(case=name):
                facts, policy = factory()
                if budget is not None:
                    policy['step_budget'] = budget
                plan = self.compare(facts, {'policy': policy})
                self.assertEqual(plan['status'], 'packing_budget_exhausted' if budget else 'planned')
                self.assertGreater(plan['steps_used'], 0)
                if budget is None:
                    self.assertGreater(plan['search']['backjumps'], 0)
                    self.assertEqual(len(plan['placements']), len(facts['footprints']))

    def test_targeted_success_refusal_and_selection_tie(self):
        from test_placement_plans import board, row
        variants = [('row', {'targeted': row()}, 'planned'),
                    ('blocked', {'targeted': row(offset_nm=[10*MM, 0])}, 'blocked'),
                    ('selection', {'targeted_plans': [row(step_nm=[7*MM, 0]),
                        row(offset_nm=[10*MM, 0]), row(), row()]}, 'planned')]
        for name, section, status in variants:
            with self.subTest(case=name):
                plan = self.compare(board(), {'policy': request(), **section})
                self.assertEqual(plan['status'], status)
                if name == 'selection':
                    self.assertEqual(plan['selection']['selected_index'], 2)

    def test_geometry_admission_and_exact_refusal(self):
        from scripts.placement_engine_geometry_cases import cases
        selected = {'geometry-concave-notch', 'geometry-custom-copper-F', 'geometry-custom-copper-B',
                    'geometry-pad-keepout-lane', 'geometry-access-sourced', 'geometry-access-relaxing',
                    'geometry-missing-pad-flag'}
        seen = set()
        for name, facts, request_, _ in cases():
            if name not in selected:
                continue
            seen.add(name)
            with self.subTest(case=name):
                plan = self.compare(facts, request_)
                self.assertEqual(plan['status'], 'unsupported_geometry' if name.endswith('missing-pad-flag') else 'planned')
                if name.endswith('access-relaxing'):
                    self.assertEqual(plan['capacity']['excluded_access_overhang_refs'], ['J3'])
        self.assertEqual(seen, selected)

    def test_cross_side_hole_reservation_and_missing_native_target_evidence(self):
        from test_placer_geometry_cost import IncompleteTargetInventoryTests
        facts = IncompleteTargetInventoryTests().board()
        peer = facts['footprints'][1]
        peer['side'] = 'B'
        peer['courtyards'] = {'B': peer['courtyards']['F']}
        for complete in (False, True):
            with self.subTest(complete=complete):
                if complete:
                    peer['packing_pad_geometry'] = {'status': 'proven', 'copper': [], 'holes': []}
                plan = self.compare(facts, {'policy': request(fixed_refs=['U1', 'C1'], gap_nm=0)})
                self.assertEqual(plan['status'], 'fixed_conflict' if complete else 'unsupported_geometry')
                self.assertEqual((plan['placements'], plan['steps_used']), ([], 0))

    def test_capacity_default_is_after_search_true_is_before_and_false_opts_out(self):
        from test_placer_capacity import columns_case
        facts, policy = columns_case(7, capacity_proof=True)
        policy['step_budget'] = 40
        results = {}
        for mode in ('absent', False, True):
            with self.subTest(capacity_proof=mode):
                variant = copy.deepcopy(policy)
                if mode == 'absent':
                    del variant['capacity_proof']
                else:
                    variant['capacity_proof'] = mode
                results[mode] = self.compare(facts, {'policy': variant})
        default, opted_out, early = results['absent'], results[False], results[True]
        self.assertEqual((default['status'], default['outcome']), ('packing_infeasible', 'defect'))
        self.assertEqual(default['search_failure'], {key: opted_out[key] for key in ('status', 'outcome', 'reason')})
        self.assertEqual(default['steps_used'], opted_out['steps_used'])
        self.assertGreater(default['steps_used'], 0)
        self.assertEqual(early['steps_used'], 0)
        self.assertEqual(default['proof'], early['proof'])
        self.assertNotIn('proof', opted_out)
        self.assertNotIn('search_failure', opted_out)
        unsourced = copy.deepcopy(policy)
        del unsourced['constraint_provenance']['gap_nm']
        report = self.compare(facts, {'policy': unsourced})
        self.assertEqual((report['status'], report['outcome'], report['owner_confirmation']),
                         ('packing_infeasible', 'unknown', ['gap_nm']))

    def test_advisory_region_never_becomes_a_hard_obstacle(self):
        from test_advisory_regions import region
        facts = capture([part('U1', 0, 0), part('C1', 3, 0)], rectangle(0, 0, 6, 2))
        polygons = [rectangle(2, 0, 4, 2)]
        soft = self.compare(facts, {'policy': request(gap_nm=0, advisory_regions=[
            region('right-half', side='F', polygons=polygons)])})
        hard = self.compare(facts, {'policy': request(gap_nm=0, obstacles=[{'side': 'F', 'polygons': polygons}])})
        self.assertEqual(soft['status'], 'planned')
        self.assertEqual(soft['advisory_regions']['search'], 'never-pruned')
        self.assertEqual(hard['status'], 'packing_infeasible')
        self.assertEqual(hard['search_failure']['status'], 'packing_search_exhausted')

    def test_fractional_centroid_guides_real_search(self):
        from test_electrical_locality import board
        facts = board()
        facts['footprints'][1]['pads'][0]['copper_centroid_offset_nm'] = [[1, 3], [-2, 7]]
        document = _locality('Original synthetic maximum: 5 mm.', 5*MM)
        plan = self.compare(facts, {'policy': request(electrical_locality=document)})
        self.assertEqual((plan['status'], plan['locality_grading']['status']), ('planned', 'pass'))
        self.assertGreater(plan['steps_used'], 0)

    def test_owner_commit_and_closed_quantity_grammar_preserve_authority(self):
        from test_electrical_locality import board
        for label, text, status, reason in [
                ('length', 'Original synthetic maximum: 1 mm.', 'fixed_conflict', None),
                ('rate', 'Original synthetic maximum: 1 mm/s.', 'planned', 'does not state the declared value'),
                ('stray-power', 'Original synthetic maximum: 1 mm ⁻¹.', 'planned', 'outside the closed grammar'),
                ('range', 'Original synthetic maximum: 1 to 2 mm.', 'planned', 'outside the closed grammar')]:
            with self.subTest(case=label):
                document = _locality(text)
                plan = self.compare(board(), {'policy': request(fixed_refs=['U1', 'C1'], electrical_locality=document)})
                self.assertEqual(plan['status'], status)
                if reason:
                    rows = plan['locality_grading']['advisories']
                    self.assertEqual(len(rows), 2)
                    self.assertTrue(all(reason in row['constraint_provenance']['reason'] for row in rows))
        text = 'Original synthetic maximum: 1 mm.'
        for committed in (False, True):
            with self.subTest(owner_commit=committed):
                decision = {**_source(text), 'owner': 'Original synthetic fixture author'}
                if committed:
                    decision.update(commit='0'*40, repository='fixture/unpinned-owner')
                document = _locality(text, provenance={'class': 'owner', 'basis': 'Authored owner declaration',
                                                      'decision_reference': decision})
                plan = self.compare(board(), {'policy': request(fixed_refs=['U1', 'C1'], electrical_locality=document)})
                self.assertEqual((plan['status'], plan['locality_grading']['status']), ('planned', 'info'))
                expected = 'pinned owner commit' if committed else 'uncommitted'
                self.assertTrue(all(expected in row['constraint_provenance']['reason']
                                    for row in plan['locality_grading']['advisories']))

    def test_manufacturer_capture_binds_hash_locator_and_numeric_fact(self):
        from test_electrical_locality import board
        record = locality_provenance.manufacturing_record()
        citation = {'kind': 'manufacturer_capture', 'capture': 'pcb',
                    'sha256': record['sources']['pcb']['sha256'], 'locator': record['locators']['pcb'],
                    'fact': 'different_net_clearance_nm'}
        for label, changes, limit, enforcement in [
                ('valid', {}, 150000, 'feasibility'), ('stale', {'sha256': '0'*64}, 150000, 'advisory'),
                ('locator', {'locator': 'wrong row'}, 150000, 'advisory'), ('value', {}, MM, 'advisory')]:
            with self.subTest(case=label):
                document = _locality(limit=limit, provenance={'class': 'manufacturer', 'basis': 'Captured floor',
                                                             'citation': {**citation, **changes}})
                plan = self.compare(board(), {'policy': request(fixed_refs=['U1', 'C1'], electrical_locality=document)})
                measured = plan['locality_grading']['measured']
                self.assertEqual(len(measured), 2)
                self.assertTrue(all(row['constraint_provenance']['enforcement'] == enforcement for row in measured))

    def test_validation_error_is_typed_and_equal(self):
        plan = self.compare(capture([part('U1', 2, 2)]), {'policy': request(gap_nm=-1)})
        self.assertIn('gap', plan['error'])

    def test_block_admission_arrangement_residual_and_bound_source_refusal(self):
        from test_blocks import board, native_xml, obligations
        from test_block_arrangement import plan_policy, CITE
        facts, raw = board(), native_xml()
        policy = request(fixed_refs=['H1'], step_budget=20000, gap_nm=250000,
                         electrical_locality=_captured_block_limits(obligations()))
        with tempfile.TemporaryDirectory(prefix='agent-ee-block-parity-') as directory:
            path = Path(directory) / 'native.xml'
            path.write_bytes(raw)
            inventory = read_native_inventory(path)
            derived = blocks.derive(inventory, read_sheet_names(path), facts,
                                    inventory_sha256=hashlib.sha256(raw).hexdigest())
            entries = [{'candidate': blocks.pack_block(facts, inventory, derived, block['id'], policy)['candidate']}
                       for block in derived['blocks']]
        keepout = {'id': 'isolation', 'side': 'F', 'polygons': [rectangle(-1, -1, 14, 14)],
                   'provenance': {'enforcement': 'hard', 'citation': CITE}}
        for name in ('complete', 'hard-rule', 'changed-land', 'changed-policy'):
            with self.subTest(case=name):
                candidates = copy.deepcopy(entries)
                rules = plan_policy(keepouts=[keepout]) if name == 'hard-rule' else plan_policy()
                if name == 'changed-land':
                    candidates[0]['candidate']['members'][0]['pad_geometry_sha256'] = 'f'*64
                if name == 'changed-policy':
                    candidates[0]['candidate']['sources']['policy_sha256'] = 'f'*64
                plan = self.compare(facts, {'policy': policy, 'blocks': {'arrangement': rules,
                                    'candidates': candidates}}, netlist_xml=raw.decode('utf-8'))
                if name.startswith('changed-'):
                    self.assertEqual((plan['status'], plan['placements']), ('arrangement_refused', []))
                    self.assertTrue(plan['block_arrangement']['problems'])
                else:
                    self.assertEqual(plan['status'], 'planned')
                    self.assertEqual(len(plan['placements']), len(facts['footprints']))
                    self.assertEqual(plan['block_arrangement']['residual']['references'], ['J1'])
                    self.assertEqual(plan['steps_used'], plan['block_arrangement']['search']['steps_used']
                                     + plan['block_arrangement']['residual']['steps_used'])


class TelemetryCompatibilityParityTest(EngineFixture, unittest.TestCase):
    """Real iteration-7 geometry and constraints, using current candidate identities.

    This is a new compatibility regression, not the historical iter8n benchmark.
    The older candidates retain their exact frame poses and policies. Re-emission
    computes canonical geometry identities and evidence without running search.
    Design bytes remain in the existing external fome-mc object store.
    """
    COMMIT = '19522e37de01103a93fe171a09fe624d8aba6bad'
    NATIVE_COMMIT = '6530e8b8dd05baa0e5e39ac295172b35026c4f56'
    ITERATION = 'telemetry-pod/expansion/placement-candidate/iteration-7/'
    INPUTS = {
        'seed.kicad_pcb': (COMMIT, ITERATION + 'inputs/seed.kicad_pcb.gz',
                          '21bbf8efdfaf848f1a9494846a6fb1bdf31d19581daaf2c5fb601a26ffba8ecc'),
        'seed.kicad_pro': (COMMIT, ITERATION + 'candidate.kicad_pro',
                          'a91ee4d1a8ecfa583df33e707defdb3174e505d91853fc1e4959600d770aa57f'),
        'request.json': (COMMIT, ITERATION + 'inputs/request.json',
                         'f701c80ceae42616caead2c5b01f2ad93d00ac2819957e530c8026c07bedec05'),
        'native.xml': (NATIVE_COMMIT, 'telemetry-pod/expansion/storage-microsd/native.xml',
                       '7791d44380d40bb46b0dbe1cd7495ce59408e8691afe2bf360eaf70b3eab7556'),
    }

    def test_tracked_309_part_board_arranges_and_packs_with_exact_reference_parity(self):
        from agent_ee.placement_probe import extract
        from toolchain import fome_mc_store, require_toolchain
        require_toolchain(native=True)
        store = fome_mc_store(os.environ.get('AGENT_EE_FOME_MC_CHECKOUT', Path.home() / 'git/fome-mc'),
                              self.COMMIT, self.NATIVE_COMMIT)
        with tempfile.TemporaryDirectory(prefix='agent-ee-telemetry-parity-') as directory:
            root = Path(directory)
            original_bytes = {}
            for name, (commit, path, expected_sha) in self.INPUTS.items():
                raw = subprocess.run(['git', '-C', str(store), 'cat-file', 'blob', f'{commit}:{path}'],
                                     capture_output=True, check=True, timeout=30).stdout
                raw = gzip.decompress(raw) if path.endswith('.gz') else raw
                self.assertEqual(hashlib.sha256(raw).hexdigest(), expected_sha, name)
                original_bytes[name] = raw
                (root / name).write_bytes(raw)
            facts = extract(root / 'seed.kicad_pcb')
            self.assertEqual(facts['board_sha256'], self.INPUTS['seed.kicad_pcb'][2])
            self.assertEqual(len(facts['footprints']), 309)
            request_ = json.loads(original_bytes['request.json'])
            original_request = copy.deepcopy(request_)
            inventory = read_native_inventory(root / 'native.xml')
            derived = blocks.derive(inventory, read_sheet_names(root / 'native.xml'), facts,
                inventory_sha256=self.INPUTS['native.xml'][2],
                allocation_raw=request_['blocks']['allocation_text'].encode('utf-8'))
            definitions = {block['id']: block for block in derived['blocks']}
            self.assertEqual(len(request_['blocks']['candidates']), 17)
            for entry in request_['blocks']['candidates']:
                old = entry['candidate']
                anchor = old['frame']['anchor']
                poses = {row['reference']: {'ref': row['reference'], **row['pose']} for row in old['members']}
                self.assertEqual(poses[anchor]['position_nm'], [0, 0])
                self.assertEqual(poses[anchor]['rotation_deg'], 0)
                # These are retained search records, not a new search or an invented witness.
                old_plan = {'search': old['search'], 'judgment': old['placer_judgment'],
                            'steps_used': old['search']['steps_used'], 'step_budget': old['search']['step_budget']}
                current = blocks._plain(blocks.candidate(facts, inventory, derived, definitions[old['block_id']],
                    anchor, poses, request_['policy'], old['block_policy'], old['deferred'], old_plan))
                self.assertEqual(current['status'], 'packed', old['block_id'])
                for before, after in zip(old['members'], current['members'], strict=True):
                    for key in ('reference', 'fpid', 'side', 'value', 'pose', 'pad_geometry_sha256', 'pins'):
                        self.assertIsNone(first_difference(before[key], after[key]), (old['block_id'], key))
                    self.assertEqual(set(before['courtyard']), set(after['courtyard']))
                    for side, record in before['courtyard'].items():
                        canonical = copy.deepcopy(record)
                        for key in ('polygons', 'inner_polygons'):
                            if key in canonical:
                                canonical[key] = sorted(blocks.canonical_ring(ring) for ring in canonical[key])
                        self.assertIsNone(first_difference(canonical, after['courtyard'][side]))
                for side, outline in old['outline'].items():
                    self.assertEqual(outline['box_nm'], current['outline'][side]['box_nm'])
                    self.assertEqual(sorted(blocks.canonical_ring(r) for r in outline['courtyards']),
                                     sorted(blocks.canonical_ring(r) for r in current['outline'][side]['courtyards']))
                entry['candidate'] = current
            self.assertIsNone(first_difference(request_['policy'], original_request['policy']))
            self.assertIsNone(first_difference(request_['blocks']['arrangement'],
                                               original_request['blocks']['arrangement']))
            self.assertEqual(request_['blocks']['allocation_text'], original_request['blocks']['allocation_text'])
            plan = self.compare(facts, request_, netlist_xml=original_bytes['native.xml'].decode('utf-8'))
            self.assertEqual(plan['status'], 'planned')
            self.assertEqual(len(plan['placements']), 309)
            self.assertEqual({p['ref'] for p in plan['placements']}, {fp['ref'] for fp in facts['footprints']})
            self.assertGreater(plan['block_arrangement']['search']['steps_used'], 0)
            self.assertGreater(plan['block_arrangement']['residual']['steps_used'], 0)
            for name, raw in original_bytes.items():
                self.assertEqual((root / name).read_bytes(), raw, name)


if __name__ == '__main__':
    unittest.main()
