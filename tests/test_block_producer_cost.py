"""Bounded independent workers preserve native sets and atomic campaign output."""
import copy
import hashlib
from pathlib import Path
import tempfile
import unittest

from agent_ee import block_variant_stage as stage, block_variants, blocks
from agent_ee.native_netlist import read_native_inventory, read_sheet_names
from agent_ee.placement_probe import extract
from agent_ee.storage import read_json
from toolchain import require_toolchain
import underside_fixture


class ParallelProducerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)

    def test_native_parallel_sets_equal_serial_and_preserve_refusals(self):
        with tempfile.TemporaryDirectory(prefix='parallel-blocks-') as temporary:
            root = Path(temporary)
            source, netlist = underside_fixture.write_board(root / 'source')
            capture = extract(source)
            inventory = read_native_inventory(netlist)
            derivation = blocks.derive(inventory, read_sheet_names(netlist), capture,
                inventory_sha256=hashlib.sha256(netlist.read_bytes()).hexdigest(),
                allocation_raw=underside_fixture.allocation_text().encode())
            policy = underside_fixture.policy()
            eligibility = underside_fixture.eligibility(capture['board_sha256'])
            result = stage._produce_blocks(source, capture, inventory, derivation, policy, eligibility, root / 'parallel')
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(result['completed'], list(range(len(derivation['blocks']))))
            self.assertLessEqual(result['workers'], stage.MAX_BLOCK_WORKERS)
            for index, block in enumerate(derivation['blocks']):
                serial = block_variants.produce(source, capture, inventory, derivation, block['id'], policy,
                                                eligibility, root / 'serial' / str(index))
                parallel = read_json(root / 'parallel' / str(index) / 'candidate-set.json')
                self.assertEqual(parallel, serial)
                block_variants.verify_native(source, parallel, capture)
                timing = read_json(root / 'parallel' / str(index) / 'worker.json')
                self.assertEqual(timing['status'], 'completed')
                self.assertGreater(timing['elapsed_s'], 0)
            stale = copy.deepcopy(capture)
            stale['board_sha256'] = '0'*64
            with self.assertRaisesRegex(ValueError, 'exact native source'):
                stage._produce_blocks(source, stale, inventory, derivation, policy, eligibility, root / 'stale')
            self.assertNotEqual(read_json(root / 'stale/production.json')['status'], 'completed')

    def test_passing_status_preserves_exceeded_advisory_rows(self):
        from test_single_leg_contract import single_fixture
        from agent_ee.block_contract import check_complete
        capture, contract = single_fixture()
        report = check_complete(capture, contract)
        summary = stage.advisory_summary(report)
        self.assertEqual(summary['status'], 'pass')
        self.assertEqual(summary['advisory_count'], 2)
        self.assertEqual(summary['exceeded_advisory_count'], 2)
        self.assertEqual(summary['advisories'], report['advisories'])
