"""G1/G4 physical relationships, including native production-consumer evidence.

All extra numeric targets and routing clauses in these fixtures are authored
regression declarations. They are not claims about the telemetry pod.
"""
from copy import deepcopy
import hashlib
import json
import shutil
import time
import unittest

from agent_ee import block_contract, electrical_locality
from agent_ee.placement_probe import extract
from agent_ee.storage import read_json, write_json
from test_block_contract import fixture, moved, MM


def singles(document, *, advisory=True):
    result = deepcopy(document)
    result['obligations'] = []
    for obligation in document['obligations']:
        for role in electrical_locality.roles(obligation):
            pair = deepcopy(obligation[role])
            if advisory:
                pair['max_distance_nm'] = 1  # Deliberately exceeded; never a hard gate.
                pair['provenance'] = {'class': 'engineering', 'basis': 'authored test target'}
            result['obligations'].append({'id': obligation['id'] + '-' + role,
                'kind': 'single-leg', 'metric': obligation['metric'], 'leg': pair})
    return result


def single_fixture():
    board, contract = fixture()
    contract['relationships'] = singles(contract['relationships'])
    return board, contract


def routing(contract):
    source = fixture()[1]['relationships']['obligations'][0]['supply']['provenance']['derivation']['source']
    contract['routing_obligations'] = [{'id': kind, 'kind': kind, 'stage': 'routing',
        'pairs': [{k: deepcopy(contract['relationships']['obligations'][0]['leg'][k]) for k in ('from', 'to')}],
        'requirement': 'Authored routing test clause', 'source': deepcopy(source)}
        for kind in ('no-via', 'rf-corridor', 'controlled-impedance')]


class SingleLegContractTest(unittest.TestCase):
    def test_explicit_nonempty_leg_and_legacy_pairs(self):
        board, contract = single_fixture()
        document = contract['relationships']
        self.assertEqual(electrical_locality.grade(board, document)['status'], 'info')
        report = block_contract.check_complete(board, contract)
        self.assertEqual(report['status'], 'pass', report)
        self.assertEqual(len(report['relationships']), 2)
        self.assertEqual({r['role'] for r in report['relationships']}, {'leg'})
        self.assertTrue(all(r['exceeded'] for r in report['advisories']))
        self.assertEqual(block_contract.judge(board, contract, original_capture=board)['status'], 'info')
        for mutation in ('missing', 'empty', 'mixed', 'implicit', 'paired-return'):
            changed = deepcopy(document)
            row = changed['obligations'][0]
            if mutation == 'missing': del row['leg']
            elif mutation == 'empty': row['leg'] = {}
            elif mutation == 'mixed': row['return'] = deepcopy(row['leg'])
            elif mutation == 'implicit': del row['kind']
            else:
                changed = fixture()[1]['relationships']
                del changed['obligations'][0]['return']
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                electrical_locality.validate(changed)

    def test_single_leg_scores_advisory_overrun_and_blocks_governed_overrun(self):
        board, contract = fixture()
        proposed = moved(board, 'C1', 10*MM)['footprints'][1]
        for advisory in (False, True):
            document = singles(contract['relationships'], advisory=advisory)
            blockers, cost = electrical_locality.constraint(board['footprints'], proposed, document)
            self.assertEqual(blockers, set() if advisory else {'U1', 'C1'})
            self.assertEqual(cost, 288*MM**2 if advisory else 32)
            index = electrical_locality.PlacementIndex(document)
            index.push(board['footprints'][0])
            self.assertEqual(index.prepare(proposed).constraint([0, 0]), (blockers, cost))
            graded = electrical_locality.grade(moved(board, 'C1', 10*MM), document)
            self.assertEqual(graded['status'], 'info' if advisory else 'defect')

    def test_wrong_pin_stale_policy_and_illegal_side_refuse(self):
        board, contract = single_fixture()
        section = {'contract': contract, 'side_eligibility': contract['eligibility'], 'candidates': [],
            'allocation_text': json.dumps({'schema': 'agent-ee.block-allocation/1', 'blocks': [
                {'id': 'device', 'references': ['U1', 'C1'], 'anchor': 'U1'}]})}
        policy = {'electrical_locality': deepcopy(contract['relationships'])}
        block_contract.validate(board, section, policy)
        policy['electrical_locality']['obligations'][0]['leg']['max_distance_nm'] += 1
        with self.assertRaisesRegex(ValueError, 'differ from effective policy'):
            block_contract.validate(board, section, policy)
        bad = deepcopy(contract)
        bad['relationships']['obligations'][0]['leg']['to']['pad'] = '99'
        self.assertEqual(block_contract.check_complete(board, bad)['status'], 'unknown')
        contract['eligibility']['members'] = {'C1': {'eligible_faces': ['F']}}
        report = block_contract.check_complete(moved(board, 'C1', side='B'), contract)
        self.assertEqual(report['status'], 'defect')
        self.assertTrue(any(d['kind'] == 'forbidden_side' for d in report['defects']))

    def test_advisory_template_is_qualitative_and_numeric_relaxation_visible(self):
        board, contract = single_fixture()
        contract['templates'] = [{'relationship': o['id'], 'role': 'leg', 'template': 'decoupler-under-device'}
                                 for o in contract['relationships']['obligations']]
        inside = moved(board, 'C1', -2*MM, side='B')
        report = block_contract.judge(inside, contract, original_capture=board)
        self.assertEqual(report['status'], 'info', report)
        self.assertTrue(all(r['exceeded'] for r in report['relationships']))
        self.assertEqual({r['classification'] for r in report['relationships']}, {'deliberate_cross_face'})
        outside = block_contract.check_complete(moved(board, 'C1', MM, side='B'), contract)
        self.assertEqual(outside['status'], 'defect')
        self.assertTrue(any(d['kind'] == 'cross_face_template_outside' for d in outside['defects']))
        for row in contract['templates']: row['template'] = 'same-face-only'
        self.assertEqual(block_contract.check_complete(inside, contract)['status'], 'defect')

    def test_routing_pending_keeps_provenance_and_does_not_waive_unknowns(self):
        board, contract = single_fixture()
        routing(contract)
        for report in (block_contract.check_complete(board, contract),
                       block_contract.judge(board, contract, original_capture=board)):
            self.assertIn(report['status'], ('pass', 'info'))
            self.assertEqual([r['status'] for r in report['routing_obligations']], ['pending']*3)
            self.assertIn('impedance-qualification', report['routing_obligations'][2]['evidence_required'])
        for mutation in ('hash', 'stage', 'reference', 'duplicate', 'asserted-pass'):
            bad = deepcopy(contract)
            row = bad['routing_obligations'][0]
            if mutation == 'hash': row['source']['sha256'] = '0'*64
            elif mutation == 'stage': row['stage'] = 'placement'
            elif mutation == 'reference': row['pairs'][0]['to']['pad'] = '999'
            elif mutation == 'duplicate': bad['routing_obligations'].append(deepcopy(row))
            else: row['status'] = 'pass'
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                block_contract.check_complete(board, bad)
        contract['eligibility']['blocks']['device']['material_unknowns'] = ['untyped unresolved requirement']
        self.assertEqual(block_contract.check_complete(board, contract)['status'], 'unknown')


from test_placement_engine_parity import EngineFixture


class NativeSingleLegConsumerTest(EngineFixture, unittest.TestCase):
    # Reuse setup/receipt helpers only, not unrelated test cases.
    from test_block_variant_stage import StageOwnedVariantsTest as _Fixture
    @classmethod
    def setUpClass(cls):
        cls._Fixture.setUpClass.__func__(cls)
        EngineFixture.setUpClass.__func__(cls)
    setUp = _Fixture.setUp
    refresh = _Fixture.refresh
    produce = _Fixture.produce

    def test_native_producer_default_rust_consumer_and_saved_report(self):
        from agent_ee import placement_adapter as pa
        from test_placement_adapter import BOARD
        locality = singles(read_json(self.inputs / 'blocks/locality.json'))
        template_path = self.inputs / self.section['stage_owned']['contract']
        template = read_json(template_path)
        route_text = 'Authored regression requirement: no vias between the declared RC fixture terminals.'
        locality['source_records']['fixture-routing.txt'] = route_text
        template['relationships'] = locality
        template['routing_obligations'] = [{'id': 'fixture-no-via', 'stage': 'routing', 'kind': 'no-via',
            'pairs': [{k: deepcopy(locality['obligations'][0]['leg'][k]) for k in ('from', 'to')}],
            'requirement': route_text, 'source': {'path': 'fixture-routing.txt',
                'sha256': hashlib.sha256(route_text.encode()).hexdigest(),
                'locator': 'authored no-via requirement', 'quote': route_text}}]
        template['templates'] = [{'relationship': o['id'], 'role': 'leg', 'template': 'same-face-only'}
                                 for o in locality['obligations']]
        write_json(template_path, template)
        write_json(self.inputs / 'blocks/locality.json', locality)
        self.refresh()
        self.produce()
        work = self.root / 'implementations/1'
        design = work / 'review-work/implementation/design'
        shutil.copytree(self.inputs, design)
        state = {'history': [{'earliest_failing_stage': 'placement'}], 'mutable_inputs': self.mutable, 'stage_receipts': {}}
        result = pa.run_placement_iteration(work, design, self.inputs, self.manifest, state, self.declaration,
                                           time.monotonic() + 600)
        self.assertEqual(result['status'], 'proposed', result)
        execution = read_json(work / result['helper']['receipt'] / 'run/placement/placed/planning-execution.json')
        self.assertEqual((execution['implementation'], execution['status'], execution['exit_code']), ('rust', 'ok', 0))
        self.assertEqual(pa.verify_record(work, self.inputs, design, self.manifest, self.declaration, self.mutable), result)
        original, saved = extract(self.inputs / BOARD), extract(design / BOARD)
        contract = read_json(self.inputs / self.section['contract'])
        report = block_contract.judge(saved, contract, original_capture=original)
        self.assertEqual(report['status'], 'info', report)
        self.assertEqual({fp['side'] for fp in saved['footprints']}, {'B'})
        self.assertEqual(len(report['relationships']), 2)
        self.assertEqual(report['routing_obligations'][0]['status'], 'pending')
        self.assertEqual(report['routing_obligations'][0]['pairs'], template['routing_obligations'][0]['pairs'])
        self.assertTrue(all(r['exceeded'] for r in report['relationships']))
        self.assertEqual({r['role'] for r in report['relationships']}, {'leg'})
        self.assertTrue(all(r['constraint_provenance']['enforcement'] == 'advisory' for r in report['relationships']))
        write_json(self.root / 'single-leg-final-report.json', report)
        from test_placer import request
        policy = request(fixed_refs=sorted(f['ref'] for f in saved['footprints']), electrical_locality=locality)
        plan = self.compare(saved, {'policy': policy})
        self.assertEqual(plan['status'], 'planned', plan)
        for mutation in ('omitted-leg', 'wrong-pin'):
            bad = deepcopy(policy)
            leg = bad['electrical_locality']['obligations'][0]
            if mutation == 'omitted-leg': del leg['leg']
            else: leg['leg']['to']['pad'] = '999'
            with self.subTest(mutation=mutation):
                refusal = self.compare(saved, {'policy': bad})
                self.assertNotEqual(refusal.get('status'), 'planned', refusal)
        # These are actual native saved-board mutations, not edited capture JSON.
        import pcbnew as k
        changed_path = self.root / 'illegal-side.kicad_pcb'
        native = k.LoadBoard(str(design / BOARD))
        fp = next(f for f in native.GetFootprints() if f.GetReference() == 'C1')
        fp.Flip(fp.GetPosition(), k.FLIP_DIRECTION_LEFT_RIGHT)
        k.SaveBoard(str(changed_path), native)
        illegal = block_contract.judge(extract(changed_path), contract, original_capture=original)
        self.assertEqual(illegal['status'], 'defect', illegal)
        self.assertTrue(any(d['kind'] == 'same_face_required' for d in illegal['defects']))
        stale = deepcopy(locality)
        stale['obligations'][0]['leg']['max_distance_nm'] += 1
        write_json(self.inputs / 'blocks/locality.json', stale)
        self.refresh()
        with self.assertRaises(ValueError):
            pa.binding(self.declaration, self.manifest, self.inputs, self.mutable, require_produced=True)
