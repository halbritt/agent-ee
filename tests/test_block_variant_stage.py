"""Owned native fixture: receipt-produced variants through the production adapter."""
import copy
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest

from agent_ee import block_variant_stage as producer, placement_adapter as pa, pod_driver
from agent_ee import electrical_locality, stage_receipts
from agent_ee.native_netlist import read_native_inventory
from agent_ee.placement_probe import extract
from agent_ee.storage import digest, read_json, write_json
from test_placement_adapter import owned_design, BOARD, SCHEMATIC, POLICY, stage_receipts as require_receipts
from toolchain import require_toolchain


class StageOwnedVariantsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)
        require_receipts()

    def setUp(self):
        import pcbnew as k
        self.tmp = tempfile.TemporaryDirectory(prefix='stage-owned-blocks-')
        self.addCleanup(self.tmp.cleanup)
        retained = os.environ.get('AGENT_EE_STAGE_BLOCK_OUTPUT')
        self.root = (Path(retained) / self._testMethodName if retained else Path(self.tmp.name)).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.inputs = owned_design(self.root / 'inputs')
        inventory = read_native_inventory(self.root / 'reference-netlist.xml')
        board = k.LoadBoard(str(self.inputs / BOARD))
        for fp in board.GetFootprints():
            name = inventory['components'][fp.GetReference()]['footprint']
            nickname, _, item = name.partition(':')
            fp.SetFPID(k.LIB_ID(nickname if item else '', item or nickname))
        k.SaveBoard(str(self.inputs / BOARD), board)
        self.section = {'netlist': 'blocks/native.xml', 'arrangement': 'blocks/arrangement.json',
            'allocation': 'blocks/allocation.json', 'candidates': ['blocks/RC.json'],
            'side_eligibility': 'blocks/eligibility.json', 'contract': 'blocks/contract.json',
            'stage_owned': {'eligibility': 'blocks/eligibility-template.json',
                            'contract': 'blocks/contract-template.json', 'report': 'blocks/producer.json'}}
        self.declaration = {'schema': pa.ADAPTER_SCHEMA, 'helper': 'agent-ee.placer/1',
                            'policy': POLICY, 'fixed_footprints': [], 'timeout_s': 300, 'blocks': self.section}
        self.scope = {'board': BOARD, 'schematic': SCHEMATIC, 'stages': {'schema': 'agent-ee.stages/1'},
            'checks': [{'id': 'pins', 'kind': 'placement', 'params': {
                'criterion': electrical_locality.CRITERION, 'locality': 'blocks/locality.json'}}]}
        self.mutable = sorted([BOARD, SCHEMATIC, *producer.output_paths(self.section)])
        (self.inputs / 'blocks').mkdir()
        eligibility = {'schema': 'fome-mc.side-eligibility/1', 'sources': {},
                       'default': {'faces': ['F', 'B']}, 'blocks': {'RC': {'eligible_faces': ['F', 'B']}}}
        write_json(self.inputs / self.section['stage_owned']['eligibility'], eligibility)
        facts = extract(self.inputs / BOARD)
        pads = {(fp['ref'], p['number']): p for fp in facts['footprints'] for p in fp['pads']}
        text = 'Authored regression fixture: each capacitor pad pair maximum is 100 mm.'
        source = {'path': 'fixture-distance.txt', 'sha256': hashlib.sha256(text.encode()).hexdigest(),
                  'locator': 'authored fixture quantity', 'quote': text}
        pairs = []
        for number in ('1', '2'):
            net = pads['C1', number]['net']
            ref, pin = next((ref, pin) for ref, pin in sorted(pads, key=lambda p: (p[0] != 'R1', p)) if ref != 'C1' and pads[ref, pin]['net'] == net)
            pairs.append({'from': {'reference': ref, 'pad': pin, 'net': net},
                'to': {'reference': 'C1', 'pad': number, 'net': net}, 'max_distance_nm': 100000000,
                'citation': 'Authored regression fixture quantity', 'provenance': {
                    'class': 'datasheet', 'basis': 'Authored regression quantity, not production authority',
                    'derivation': {'kind': 'numeric', 'value_nm': 100000000, 'source': source}}})
        locality = {'schema': electrical_locality.SCHEMA, 'source_records': {source['path']: text},
            'obligations': [{'id': 'rc-pins', 'metric': 'pad-centre-to-centre', 'supply': pairs[0], 'return': pairs[1]}]}
        write_json(self.inputs / 'blocks/locality.json', locality)
        write_json(self.inputs / self.section['allocation'], {'schema': 'agent-ee.block-allocation/1',
            'blocks': [{'id': 'RC', 'references': ['C1', 'J1', 'R1'], 'anchor': 'R1'}]})
        write_json(self.inputs / self.section['stage_owned']['contract'], {'schema': 'agent-ee.block-contract/1',
            'allocation': {'schema': 'agent-ee.block-allocation/2',
                'blocks': [{'id': 'RC', 'references': ['C1', 'J1', 'R1'], 'anchor': {'member': 'R1'},
                            'interfaces_to_fixed': None}], 'fixed': [], 'shared': []},
            'eligibility': eligibility, 'relationships': locality, 'templates': [], 'thickness_nm': 1600000})
        # Authored geometric obstruction, not a face lock: both faces are
        # produced and admitted; the arrangement searches the available back.
        write_json(self.inputs / self.section['arrangement'], {'schema': 'agent-ee.block-arrangement/1',
            'step_budget': 10000, 'keepouts': [{'id': 'occupied-front', 'side': 'F',
                'polygons': [[[0, 0], [30000000, 0], [30000000, 30000000], [0, 30000000]]],
                'provenance': {'enforcement': 'hard', 'citation': 'Authored fixture front-space obstruction'}}]})
        for path in producer.output_paths(self.section):
            (self.inputs / path).write_bytes(b'')
        write_json(self.inputs / self.section['stage_owned']['report'], {'schema': producer.REPORT, 'status': 'pending'})
        self.refresh()

    def refresh(self):
        files, other = stage_receipts.file_tree(self.inputs)
        self.assertEqual(other, [])
        self.manifest = {'candidate_id': 'c' * 64, 'scope': self.scope, 'files': files}

    def produce(self):
        spec = producer.step(Path('src').resolve(), scope=self.scope, declaration=self.declaration,
                             mutable_inputs=self.mutable)
        producer.validate_step_contract(spec, self.scope, self.declaration, self.mutable)
        before = copy.deepcopy(self.manifest['files'])
        receipt_dir = self.root / 'producer-receipt'
        receipt = stage_receipts.run_step(receipt_dir, self.inputs, spec,
            {'implementation': 1, 'source_candidate_id': 'c' * 64, 'receipt_index': 1},
            deadline=time.monotonic() + 600)
        stage_receipts.adopt(receipt_dir, receipt, self.inputs)
        self.refresh()
        for path, sha in before.items():
            if path not in producer.output_paths(self.section):
                self.assertEqual(self.manifest['files'][path], sha)
        return receipt

    def test_produced_underside_is_consumed_and_stale_native_poses_refuse(self):
        import pcbnew as k
        initial = pa.binding(self.declaration, self.manifest, self.inputs, self.mutable)[0]
        with self.assertRaisesRegex(ValueError, 'not been produced'):
            pa.binding(self.declaration, self.manifest, self.inputs, self.mutable, require_produced=True)
        receipt = self.produce()
        binding = pa.binding(self.declaration, self.manifest, self.inputs, self.mutable, require_produced=True)[0]
        self.assertEqual(binding, initial, 'campaign recipe binding must survive stage generation')
        bundle = read_json(self.inputs / self.section['candidates'][0])
        self.assertEqual(bundle['schema'], 'agent-ee.block-candidate-set/1')
        self.assertEqual({k: v['status'] for k, v in bundle['variants'].items()}, {'F': 'packed', 'B': 'packed'})
        report = read_json(self.inputs / self.section['stage_owned']['report'])
        self.assertEqual(report['board']['sha256'], digest(self.inputs / BOARD))
        self.assertTrue(all(receipt['outputs'][path] == sha for path, sha in report['outputs'].items()))
        original = (self.inputs / BOARD).read_bytes()
        for mutation in ('move', 'rotate', 'flip'):
            board = k.LoadBoard(str(self.inputs / BOARD))
            fp = next(f for f in board.GetFootprints() if f.GetReference() == 'R1')
            if mutation == 'move': fp.SetPosition(fp.GetPosition() + k.VECTOR2I(1000000, 0))
            elif mutation == 'rotate': fp.SetOrientationDegrees(fp.GetOrientationDegrees() + 90)
            else: fp.Flip(fp.GetPosition(), k.FLIP_DIRECTION_LEFT_RIGHT)
            k.SaveBoard(str(self.inputs / BOARD), board)
            self.refresh()
            refused_work = self.root / 'refused' / str(('move', 'rotate', 'flip').index(mutation) + 1)
            refused_design = refused_work / 'review-work/implementation/design'
            shutil.copytree(self.inputs, refused_design)
            refused_state = {'history': [{'earliest_failing_stage': 'placement'}],
                             'mutable_inputs': self.mutable, 'stage_receipts': {}}
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError, 'stale.*board'):
                pa.run_placement_iteration(refused_work, refused_design, self.inputs, self.manifest,
                                           refused_state, self.declaration, time.monotonic() + 60)
            self.assertFalse((refused_work / 'receipts').exists(), 'stale input must refuse before helper launch')
            (self.inputs / BOARD).write_bytes(original)
        self.refresh()
        work = self.root / 'implementations/2'
        design = work / 'review-work/implementation/design'
        shutil.copytree(self.inputs, design)
        state = {'history': [{'earliest_failing_stage': 'placement'}], 'mutable_inputs': self.mutable, 'stage_receipts': {}}
        result = pa.run_placement_iteration(work, design, self.inputs, self.manifest, state, self.declaration,
                                            time.monotonic() + 600)
        self.assertEqual(result['status'], 'proposed', result)
        execution = read_json(work / result['helper']['receipt'] / 'run/placement/placed/planning-execution.json')
        self.assertEqual((execution['implementation'], execution['status'], execution['exit_code']), ('rust', 'ok', 0))
        self.assertEqual(pa.verify_record(work, self.inputs, design, self.manifest, self.declaration, self.mutable), result)
        before, after = extract(self.inputs / BOARD), extract(design / BOARD)
        self.assertEqual({f['side'] for f in after['footprints']}, {'B'})
        self.assertEqual({(f['ref'], f['uuid'], p['number'], p['net']) for f in before['footprints'] for p in f['pads']},
                         {(f['ref'], f['uuid'], p['number'], p['net']) for f in after['footprints'] for p in f['pads']})
        self.assertEqual(electrical_locality.grade(after, read_json(self.inputs / 'blocks/locality.json'))['status'], 'pass')
        self.assertEqual(dict(pod_driver.STEPS)[producer.STEP], 'schematic')
        names = [name for name, _ in pod_driver.STEPS]
        self.assertEqual(names[names.index(producer.STEP)-1:names.index(producer.STEP)+2],
                         ['forward-annotate', 'block-variants', 'place-board'])


    def test_output_hash_and_schema_tampering_cannot_be_consumed(self):
        self.produce()
        candidate = self.inputs / self.section['candidates'][0]
        original = candidate.read_bytes()
        candidate.write_bytes(original + b'\n')
        self.refresh()
        with self.assertRaisesRegex(ValueError, 'output digest changed'):
            pa.binding(self.declaration, self.manifest, self.inputs, self.mutable, require_produced=True)
        # Explicit forged-report control: even a resealed /2 inner record is
        # never accepted as the stage-owned /1 candidate set.
        inner = read_json(candidate)['variants']['B']['candidate']
        write_json(candidate, inner)
        report_path = self.inputs / self.section['stage_owned']['report']
        report = read_json(report_path)
        report['outputs'][self.section['candidates'][0]] = digest(candidate)
        write_json(report_path, report)
        self.refresh()
        with self.assertRaisesRegex(ValueError, 'native /1 candidate sets'):
            pa.binding(self.declaration, self.manifest, self.inputs, self.mutable, require_produced=True)

    def test_historical_bound_template_and_misdeclared_step_refuse(self):
        path = self.inputs / self.section['stage_owned']['eligibility']
        template = read_json(path)
        template['sources']['board_sha256'] = digest(self.inputs / BOARD)
        write_json(path, template)
        self.refresh()
        ctx = producer.context(self.scope, self.declaration, self.mutable)
        with self.assertRaisesRegex(ValueError, 'historical board bindings are not rewritten'):
            producer.produce_stage(self.inputs, self.root / 'refused-producer', ctx)
        spec = producer.step(Path('src').resolve(), scope=self.scope, declaration=self.declaration,
                             mutable_inputs=self.mutable)
        spec['writes'].pop(self.section['candidates'][0])
        with self.assertRaisesRegex(ValueError, 'owned producer contract'):
            producer.validate_step_contract(spec, self.scope, self.declaration, self.mutable)


if __name__ == '__main__':
    unittest.main()
