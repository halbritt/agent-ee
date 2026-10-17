"""Owned intent examples; these are not benchmark records."""
import hashlib
import json
import unittest

from agent_ee.design_intent import import_record


def record(**changes):
    value = dict(id='owned-can', task='Use 1x OWN_CAN and 2x R_Small.',
                 input_nodes='TXD, RXD', output_nodes='CANH, CANL',
                 input_voltage='3.3', output_voltage='3.3',
                 components='1x OWN_CAN, 2x R_Small', sub_module_name='OWN_CAN_INTERFACE')
    value.update(changes)
    return json.dumps(value).encode()


class DesignIntentTests(unittest.TestCase):
    def test_owned_import_preserves_bytes_and_incomplete_contract_boundary(self):
        raw = record()
        artifacts = import_record(raw)
        self.assertEqual(artifacts['requirements.md'], json.loads(raw)['task'].encode())
        report = json.loads(artifacts['intent.json'])
        self.assertEqual(report['source_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(report['outcome'], 'pass')
        stub = json.loads(artifacts['hardware-interface.stub.json'])
        self.assertEqual(stub['required_nets'], ['CANH', 'CANL', 'RXD', 'TXD'])
        self.assertFalse(stub['complete'])
        self.assertEqual(import_record(raw), artifacts)

    def test_component_encodings_and_comma_in_name(self):
        from agent_ee.design_intent import component_counts
        expected = {'OWN_A/N3,112': 2, 'R': 3}
        for value in ['2x OWN_A/N3,112, 3x R', expected,
                      ['OWN_A/N3,112', 'OWN_A/N3,112', 'R', 'R', 'R']]:
            self.assertEqual(component_counts(value), expected)
        for value in [{}, [], {'R': True}, {'R': 0}, {'R': -2}, {'R': 1.5}, 3]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                component_counts(value)

    def test_count_mismatch_and_duplicate_or_placeholder_nets_are_unknown(self):
        for changes, code in [({'task': 'Use 3x OWN_CAN.'}, 'component_count_mismatch'),
                              ({'output_nodes': 'OUT'}, 'placeholder_interface_net'),
                              ({'input_nodes': 'TXD, TXD'}, 'duplicate_interface_net')]:
            report = json.loads(import_record(record(**changes))['intent.json'])
            self.assertEqual(report['outcome'], 'unknown')
            self.assertIn(code, [f['code'] for f in report['findings']])

    def test_out_waiver_requires_recorded_nonempty_justification(self):
        report = json.loads(import_record(record(output_nodes='OUT'),
                            out_justification='Owner confirms OUT is the actual output pin.')['intent.json'])
        self.assertEqual(report['outcome'], 'pass')
        self.assertEqual(len(report['waivers']), 1)
        with self.assertRaises(ValueError):
            import_record(record(output_nodes='OUT'), out_justification='')

    def test_voltages_never_default_to_a_pass(self):
        for value in ['0', 'garbage', '3.3 (diff)', 'NaN', 'Infinity', True, None, '50A']:
            report = json.loads(import_record(record(input_voltage=value))['intent.json'])
            self.assertEqual(report['outcome'], 'unknown')
            self.assertIsNone(report['operating_targets']['input_voltage']['volts'])
        report = json.loads(import_record(record(output_voltage='15.0,-9.0'))['intent.json'])
        self.assertEqual(report['operating_targets']['output_voltage']['volts'], ['15', '-9'])

    def test_stub_cannot_validate_as_an_acceptance_contract(self):
        from agent_ee.hardware_interface import validate_contract
        stub = json.loads(import_record(record())['hardware-interface.stub.json'])
        with self.assertRaises(ValueError):
            validate_contract(stub)

    def test_invalid_json_and_shape_are_rejected(self):
        for raw in [b'{}', b'[]', b'{', record()[:-1] + b',"id":"again"}',
                    record(components={'R': 0}), record(input_nodes=[]), record(extra='field')]:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                import_record(raw)

    def test_cli_retains_source_and_refuses_existing_output(self):
        import os
        from pathlib import Path
        import subprocess
        import sys
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.json'
            source.write_bytes(record())
            argv = [sys.executable, '-m', 'agent_ee.design_intent', str(source), '--out', str(root / 'out')]
            first = subprocess.run(argv, capture_output=True, env=os.environ)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual((root / 'out/source-record.json').read_bytes(), source.read_bytes())
            second = subprocess.run(argv, capture_output=True, env=os.environ)
            self.assertEqual(second.returncode, 2)
            source.write_bytes(record(input_voltage='0'))
            third = subprocess.run(argv[:-1] + [str(root / 'unknown')], capture_output=True, env=os.environ)
            self.assertEqual(third.returncode, 1, third.stderr)
