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


def numeric_record(token, field='input_voltage', **changes):
    """Raw JSON number bytes; json.dumps cannot express arbitrary decimal tokens."""
    raw = record(**{field: 'PLACEHOLDER'}, **changes)
    marker = f'"{field}": "PLACEHOLDER"'.encode()
    assert raw.count(marker) == 1
    return raw.replace(marker, f'"{field}": '.encode() + token.encode())


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

    def test_voltage_targets_preserve_precision_without_decimal_context_overflow(self):
        from agent_ee.design_intent import voltage_targets
        value = '1.23456789012345678901234567890123456789'
        self.assertEqual(voltage_targets(value), [value])
        self.assertEqual(voltage_targets('1e999999999999999999'), ['1E+999999999999999999'])

    def test_list_net_whitespace_cannot_hide_placeholders_or_duplicates(self):
        for changes, code in [({'output_nodes': [' OUT ']}, 'placeholder_interface_net'),
                              ({'input_nodes': ['TXD', ' TXD ']}, 'duplicate_interface_net')]:
            report = json.loads(import_record(record(**changes))['intent.json'])
            self.assertEqual(report['outcome'], 'unknown')
            self.assertIn(code, [finding['code'] for finding in report['findings']])

    def test_requirement_id_tracks_record_identity_and_preserves_original_text(self):
        first = json.loads(import_record(record())['requirements.json'])['requirements'][0]
        second = json.loads(import_record(record(task='Revised wording.'))['requirements.json'])['requirements'][0]
        third = json.loads(import_record(record(id='other'))['requirements.json'])['requirements'][0]
        self.assertEqual(first['text'], 'Use 1x OWN_CAN and 2x R_Small.')
        self.assertEqual(first['id'], second['id'])
        self.assertNotEqual(first['id'], third['id'])

    def test_numeric_voltage_tokens_keep_exact_decimal_value_and_raw_token(self):
        from decimal import Decimal
        for token, volts in [('1.0000000000000001', '1.0000000000000001'),
                             ('9007199254740993.0', '9007199254740993'),
                             ('9007199254740993', '9007199254740993'),
                             ('1.23456789012345678901234567890123456789',
                              '1.23456789012345678901234567890123456789'),
                             ('3.30', '3.3'), ('-1.5E-3', '-0.0015'), ('2.5e+1', '25'),
                             ('1e-400', '1E-400'), ('-1.0e400', '-1.0E+400')]:
            with self.subTest(token=token):
                raw = numeric_record(token)
                artifacts = import_record(raw)
                self.assertEqual(artifacts['source-record.json'], raw)
                report = json.loads(artifacts['intent.json'], parse_float=Decimal)
                self.assertEqual(report['outcome'], 'pass')
                target = report['operating_targets']['input_voltage']
                self.assertEqual(target['volts'], [volts])
                self.assertEqual(Decimal(target['volts'][0]), Decimal(token))
                self.assertIn(b'"raw":' + token.encode() + b',', artifacts['intent.json'])
                self.assertEqual(import_record(raw), artifacts)

    def test_numeric_and_string_voltage_tokens_are_equivalent(self):
        for token in ['1.0000000000000001', '9007199254740993.0', '-1.5E-3', '1e-400', '1e400',
                      '0', '0.0', '-0.0', '0e5', '1e9999999999999999999', '-1e-9999999999999999999']:
            with self.subTest(token=token):
                numeric = json.loads(import_record(numeric_record(token))['intent.json'],
                                     parse_float=str)
                string = json.loads(import_record(record(input_voltage=token))['intent.json'])
                self.assertEqual(numeric['outcome'], string['outcome'])
                self.assertEqual(numeric['findings'], string['findings'])
                self.assertEqual(numeric['operating_targets']['input_voltage']['volts'],
                                 string['operating_targets']['input_voltage']['volts'])
                self.assertEqual(str(numeric['operating_targets']['input_voltage']['raw']), token)

    def test_decimal_context_cannot_change_targets_or_artifact_bytes(self):
        from decimal import Decimal, localcontext
        for token, expected in [('1.0e400', '1.0E+400'), ('1.0e-400', '1.0E-400'),
                                ('-1.0e400', '-1.0E+400'), ('1e-400', '1E-400'),
                                ('3.30', '3.3')]:
            for raw in (numeric_record(token), record(input_voltage=token)):
                with localcontext() as context:
                    context.capitals = 1
                    baseline = import_record(raw)
                for capitals in (0, 1):
                    with self.subTest(token=token, raw=raw, capitals=capitals), localcontext() as context:
                        context.capitals = capitals
                        artifacts = import_record(raw)
                        report = json.loads(artifacts['intent.json'], parse_float=Decimal)
                        self.assertEqual(report['outcome'], 'pass')
                        volts = report['operating_targets']['input_voltage']['volts']
                        self.assertEqual(volts, [expected])
                        self.assertEqual(Decimal(volts[0]), Decimal(token))
                        self.assertEqual(artifacts, baseline)

    def test_numeric_zero_and_out_of_range_exponents_never_pass(self):
        for token in ['0', '-0.0', '0E-7', '1e9999999999999999999', '-1e-9999999999999999999']:
            with self.subTest(token=token):
                artifacts = import_record(numeric_record(token, field='output_voltage'))
                report = json.loads(artifacts['intent.json'], parse_float=str)
                self.assertEqual(report['outcome'], 'unknown')
                self.assertEqual([f['code'] for f in report['findings']], ['unknown_voltage'])
                self.assertIsNone(report['operating_targets']['output_voltage']['volts'])

    def test_unrepresentable_integer_tokens_and_inexact_counts_are_refused(self):
        with self.assertRaises(ValueError):
            import_record(numeric_record('1' + '0' * 5000))
        for raw in [record()[:-1] + b', "n_gt_components": 3.0}']:
            report = json.loads(import_record(raw)['intent.json'])
            self.assertEqual(report['outcome'], 'unknown')
            self.assertEqual(report['findings'][0]['code'], 'component_metadata_mismatch')
        for raw in [record(id='X').replace(b'"id": "X"', b'"id": 37.0'),
                    record(components={'R_Small': 'X'}).replace(b'"X"', b'2.0')]:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                import_record(raw)

    def test_intent_bytes_remain_storage_canonical(self):
        from agent_ee.storage import canonical
        for raw in [record(), numeric_record('3.3'), numeric_record('12')]:
            intent = import_record(raw)['intent.json']
            self.assertEqual(intent, canonical(json.loads(intent)) + b'\n')

    def test_cli_writes_exact_numeric_voltage_token(self):
        from pathlib import Path
        import subprocess
        import sys
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.json'
            source.write_bytes(numeric_record('9007199254740993.0'))
            result = subprocess.run([sys.executable, '-m', 'agent_ee.design_intent', str(source),
                                     '--out', str(root / 'out')], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((root / 'out/intent.json').read_bytes(), parse_float=str)
            self.assertEqual(report['operating_targets']['input_voltage'],
                             {'raw': '9007199254740993.0', 'volts': ['9007199254740993']})

    def test_structured_numeric_voltage_stays_unknown_with_exact_raw_tokens(self):
        for token in ['[1.0000000000000001]', '{"V": 1e400}', '[3.3, -0.0]']:
            with self.subTest(token=token):
                artifacts = import_record(numeric_record(token))
                report = json.loads(artifacts['intent.json'], parse_float=str)
                self.assertEqual(report['outcome'], 'unknown')
                self.assertIsNone(report['operating_targets']['input_voltage']['volts'])
                self.assertIn(b'"raw":' + token.replace(', ', ',').replace(': ', ':').encode(),
                              artifacts['intent.json'])
