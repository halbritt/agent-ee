"""Pure custody controls for the authored AGENTEE-623 placement fixture."""
import json
import os
import unittest
import subprocess
import sys
from unittest.mock import patch

from agent_ee import placement_intent, placement_job as job
from portable_placement import command, contract, portable_fixture, verified


class PortablePlacementSourceTests(unittest.TestCase):
    def test_cli_negative_declares_an_edge_other_than_geometry_nearest(self):
        with portable_fixture(self) as fixture:
            geometry = json.loads(verified(fixture, 'geometry')[1])
            intent = json.loads(verified(fixture, 'intent')[1])
            x0, y0, x1, y1 = geometry['outline_mm']
            x, y = geometry['poses_mm']['R1']
            distances = {'west': x - x0, 'north': y - y0, 'east': x1 - x, 'south': y1 - y}
            nearest = min(distances, key=distances.get)
            self.assertEqual(nearest, 'west')
            self.assertLess(distances['west'], min(value for edge, value in distances.items() if edge != 'west'))
            connectors = intent['edge_connectors']
            self.assertEqual([row['ref'] for row in connectors], ['R1'])
            declared = connectors[0]['edge']
            (fixture['root'] / 'edge-identity-blueprint.json').write_text(json.dumps({
                'basis': 'authored outline and R1 centre; not native acceptance',
                'centre_distances_mm': distances, 'nearest': nearest, 'declared': declared}, indent=2) + '\n')
            self.assertNotEqual(declared, nearest, 'CLI negative must exercise the covered edge_identity mismatch')
            self.assertEqual(declared, 'east')

    def test_historical_clearance_gate_imports_and_refuses_missing_strict_sources(self):
        from tests import placement_clearance_agreement as gate
        with portable_fixture(self) as fixture:
            with patch.dict(os.environ, {'AGENT_EE_REQUIRE_TOOLCHAIN': '1'}, clear=True), \
                    patch.object(gate, 'require_toolchain', side_effect=AssertionError('native boundary reached')):
                with self.assertRaisesRegex(AssertionError,
                        '^AGENT_EE_ESP_PROG_PREPARED required for strict native agreement$') as refused:
                    gate.EspProgClearanceTests.setUpClass()
            (fixture['root'] / 'clearance-import-refusal.json').write_text(json.dumps({
                'module': gate.__name__, 'historical_source_identity': gate.ESP_PROG,
                'strict_refusal': str(refused.exception), 'native_operation': 'not reached'}, indent=2) + '\n')

    def test_malformed_text_keeps_original_diagnostic_bytes_and_refuses_decode(self):
        with portable_fixture(self) as fixture:
            with self.assertRaises(UnicodeDecodeError):
                command(fixture, [sys.executable, '-c', "import sys; sys.stdout.buffer.write(b'\\xff'); sys.exit(1)"], 'bad-text')
            self.assertEqual((fixture['root'] / 'bad-text.stdout').read_bytes(), b'\xff')
            self.assertEqual(json.loads((fixture['root'] / 'bad-text.execution.json').read_text())['exit_code'], 1)

    def test_failed_command_keeps_stdout_stderr_and_exit(self):
        with portable_fixture(self) as fixture:
            result = command(fixture, [sys.executable, '-c',
                "import sys; print('owned stdout'); print('owned stderr', file=sys.stderr); sys.exit(7)"], 'refused')
            self.assertEqual(result.returncode, 7)
            self.assertEqual((fixture['root'] / 'refused.stdout').read_text(), 'owned stdout\n')
            self.assertEqual((fixture['root'] / 'refused.stderr').read_text(), 'owned stderr\n')
            self.assertEqual(json.loads((fixture['root'] / 'refused.execution.json').read_text())['exit_code'], 7)

    def test_timeout_retains_its_failure_before_cleanup(self):
        with portable_fixture(self) as fixture:
            with self.assertRaises(subprocess.TimeoutExpired):
                command(fixture, [sys.executable, '-c',
                    "import time; print('started', flush=True); time.sleep(30)"], 'timed-out', timeout=0.3)
            self.assertEqual(json.loads((fixture['root'] / 'timed-out.execution.json').read_text())['status'], 'timeout')
            self.assertTrue((fixture['root'] / 'timed-out.stdout').is_file())

    def test_authored_contract_has_explicit_provenance_and_four_measured_legs(self):
        source = contract()
        geometry = json.loads(source['geometry.json'])
        self.assertEqual(geometry['provenance'], 'synthetic-authored; not a historical capture or board acceptance')
        self.assertEqual(len(geometry['pad_edge_oracles']), 4)
        self.assertEqual(geometry['roles'], {'movable': 3, 'fixed_copper_mark': 2, 'silk_only': 2})

    def test_changed_and_missing_sources_refuse_without_a_replacement(self):
        for fault in ('changed', 'missing'):
            with self.subTest(fault=fault), portable_fixture(self) as fixture:
                path = fixture['root'] / 'intent.json'
                if fault == 'changed':
                    path.write_bytes(path.read_bytes() + b'\n')
                    with self.assertRaisesRegex(job.JobRefusal, 'declared sha256'):
                        verified(fixture, 'intent')
                else:
                    path.unlink()
                    with self.assertRaises(FileNotFoundError):
                        verified(fixture, 'intent')

    def test_missing_problem_is_not_substituted_for_an_evaluation(self):
        with portable_fixture(self) as fixture:
            fixture['inputs']['problem'] = {'path': str(fixture['root'] / 'missing-problem.json'), 'sha256': 'a' * 64}
            with self.assertRaises(FileNotFoundError):
                verified(fixture, 'problem')

    def test_changed_problem_refuses_its_original_capture_identity(self):
        with portable_fixture(self) as fixture:
            path = fixture['root'] / 'problem.json'
            path.write_text('{"schema":"owned-pure-custody-control"}\n')
            import hashlib
            fixture['inputs']['problem'] = {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
            path.write_text('{}\n')
            with self.assertRaisesRegex(job.JobRefusal, 'declared sha256'):
                verified(fixture, 'problem')

    def test_original_dialect_keeps_unresolved_claims_and_refuses_direct_validation(self):
        source = contract()
        adapted = placement_intent.adapt_bytes(source['intent.json'], source['groups.json'])
        self.assertFalse(adapted['runnable'])
        self.assertEqual([row['id'] for row in adapted['unresolved_claims']], ['interfaces[R1].cable_envelope_mm', 'interfaces[R1].insertion_path_mm', 'interfaces[R1].mating_clearance_mm'])
        self.assertTrue(all(row['status'] == 'unresolved_unmeasured' for row in adapted['unresolved_claims']))
        with self.assertRaises(ValueError):
            placement_intent.validate(json.loads(source['intent.json']), json.loads(source['groups.json']))


if __name__ == '__main__':
    unittest.main()
