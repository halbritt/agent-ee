"""The routing adapter's step declaration under AGENTEE-95 coordinator stage receipts.

These tests run once agent_ee.stage_receipts is integrated. The router is the
policy fixture from kicad_route_fixture.py, reached through a test driver that
supplies the fixture's pin; the step's mounts, writes, artifacts and adoptable
exits are the ones routing_adapter.step() declares.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_ee import kicad_route_attempt as attempt
from agent_ee import routing_adapter
from kicad_route_fixture import FAKE_PIN, make_candidate, make_checkout
from toolchain import require_toolchain

try:
    from agent_ee import stage_receipts
except ImportError:  # AGENTEE-95 lands separately; root integrates both.
    stage_receipts = None

DRIVER = ('import json, sys\nsys.path.insert(0, "/opt/agent-ee-routing")\n'
          'import kicad_route_attempt as a\n'
          'cfg = json.load(open("/opt/driver/layout.json"))\n'
          'layout = a.default_layout(a.parse_request(sys.argv[1:]))\n'
          'layout.update(pin=cfg["pin"], code=cfg["code"])\n'
          'sys.exit(a.main(sys.argv[1:], layout))\n')


@unittest.skipIf(stage_receipts is None, 'AGENTEE-95 agent_ee.stage_receipts is not integrated on this branch')
class StageReceiptRoutingTest(unittest.TestCase):
    def setUp(self):
        require_toolchain()
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-route-receipt-')
        self.addCleanup(self.tmp.cleanup)

    def receipt(self, scenario):
        root = Path(self.tmp.name) / scenario
        checkout, code = make_checkout(root / 'checkout', scenario)
        design = make_candidate(root / 'design')
        driver = root / 'driver'
        driver.mkdir()
        (driver / 'layout.json').write_text(json.dumps({'pin': FAKE_PIN, 'code': code}))
        (driver / 'run.py').write_text(DRIVER)
        with patch.object(attempt, 'PIN', FAKE_PIN), patch.object(attempt, 'PINNED_CODE', code):
            spec = routing_adapter.step(project='project', board='rc.kicad_pcb', schematic='rc.kicad_sch',
                                        name='rc', label='step-1', checkout=checkout, budget=5, timeout=30)
        spec['argv'][3] = '/opt/driver/run.py'
        spec['mounts'].append({'source': str(driver), 'at': '/opt/driver'})
        receipt_dir = root / 'receipts/1-kicad-route-krt'
        receipt = stage_receipts.run_step(receipt_dir, design, spec, {'implementation': 1, 'receipt_index': 1})
        self.assertEqual(stage_receipts.validate_receipt(receipt_dir), receipt)
        return receipt, receipt_dir, design

    def test_complete_attempt_is_adopted_and_a_later_writer_edit_is_not_applicable(self):
        receipt, receipt_dir, design = self.receipt('complete')
        self.assertEqual((receipt['exit_code'], receipt['status'], receipt['violations']), (0, 'completed', []))
        self.assertEqual(receipt['changed'], ['project/rc.kicad_pcb'])
        self.assertEqual(receipt['tool']['reported_version']['status'], 'unknown')
        self.assertEqual(receipt['tool']['backend']['value']['name'], 'krt')
        summary = routing_adapter.verify(receipt_dir / 'run', receipt['inputs'])
        self.assertEqual(summary['candidate_sha256'], receipt['outputs']['project/rc.kicad_pcb'])
        stage_receipts.adopt(receipt_dir, receipt, design)
        delivered = attempt.files_of(attempt.tree(design))
        self.assertEqual(stage_receipts.applicability([receipt], delivered)[0]['applicability'], 'applicable')
        (design / 'project/rc.kicad_pcb').write_text('writer edit after routing\n')
        edited = attempt.files_of(attempt.tree(design))
        relation = stage_receipts.applicability([receipt], edited)[0]
        self.assertEqual(relation, {'applicability': 'not_applicable', 'differences': ['project/rc.kicad_pcb']})

    def test_partial_attempt_is_adoptable_as_partial(self):
        receipt, receipt_dir, design = self.receipt('incomplete')
        self.assertEqual((receipt['exit_code'], receipt['status']), (2, 'partial'))
        stage_receipts.adopt(receipt_dir, receipt, design)

    def test_non_adoptable_attempts_keep_receipts_and_deliver_nothing(self):
        for scenario, code in (('validation_failed', 3), ('backend_failure', 1), ('refused', 4),
                               ('argparse', attempt.EXIT_CONTRADICTION)):
            with self.subTest(scenario):
                receipt, receipt_dir, design = self.receipt(scenario)
                self.assertEqual((receipt['exit_code'], receipt['status']), (code, 'failed'))
                self.assertIn('missing_output:project/rc.kicad_pcb', receipt['violations'])
                self.assertIsNotNone(receipt['artifacts']['routing/adapter-report.json'])
                before = attempt.files_of(attempt.tree(design))
                with self.assertRaises(ValueError):
                    stage_receipts.adopt(receipt_dir, receipt, design)
                self.assertEqual(attempt.files_of(attempt.tree(design)), before)
                routing_adapter.verify(receipt_dir / 'run', receipt['inputs'])


if __name__ == '__main__':
    unittest.main()
