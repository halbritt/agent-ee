"""Independent pin-number and failure expectations for captured intent."""
import hashlib
import unittest
from agent_ee.connectivity_ir import resolve_intent, canonical_resolved

LIB = b'''(kicad_symbol_lib (version 20231120) (generator "owned")
(symbol "R" (symbol "R_1_1"
(pin passive line (at 0 3.81 270) (length 1) (name "~") (number "1"))
(pin passive line (at 0 -3.81 90) (length 1) (name "~") (number "2")))))'''


def intent():
    return dict(schema='agent-ee.connectivity-intent/1',
                libraries=[dict(name='Owned', path='Owned.kicad_sym', sha256=hashlib.sha256(LIB).hexdigest())],
                components=[dict(id='r', reference='R1', lib_id='Owned:R', value='1k', footprint='', units=[1])],
                nets=[], no_connects=[dict(component='r', pin={'number': n}) for n in ('1', '2')])


class ConnectivityTests(unittest.TestCase):
    def test_captured_pin_geometry(self):
        result = resolve_intent(intent(), {'Owned.kicad_sym': LIB})
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'))
        self.assertEqual(result['components'][0]['pins'][0]['y_nm'], 3810000)
        self.assertEqual(result['components'][0]['pins'][0]['electrical_type'], 'passive')
        self.assertEqual(canonical_resolved(result), canonical_resolved(result))

    def test_unknown_pin_never_reassigned(self):
        raw = intent()
        raw['no_connects'][0]['pin'] = {'number': '99'}
        result = resolve_intent(raw, {'Owned.kicad_sym': LIB})
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'))
        finding = next(x for x in result['findings'] if x['code'] == 'unknown_pin')
        self.assertIn('R1', finding['message'])
        self.assertEqual([p['number'] for p in finding['available_pins']], ['1', '2'])

    def test_missing_corrupt_and_hash_mismatch_are_missing_coverage(self):
        for captures in ({}, {'Owned.kicad_sym': b'bad'}, {'Owned.kicad_sym': b'('}):
            raw = intent()
            if captures:
                raw['libraries'][0]['sha256'] = hashlib.sha256(captures['Owned.kicad_sym']).hexdigest()
            with self.subTest(captures=captures):
                result = resolve_intent(raw, captures)
                self.assertEqual((result['execution'], result['outcome'], result['coverage']),
                                 ('failed', 'unknown', 'missing'))

    def test_roundtrip_and_declaration_order(self):
        import json
        from agent_ee.connectivity_ir import validate_resolved
        raw = intent()
        result = resolve_intent(raw, {'Owned.kicad_sym': LIB})
        validate_resolved(result)
        self.assertEqual(canonical_resolved(result), canonical_resolved(json.loads(canonical_resolved(result))))
        raw['no_connects'].reverse()
        self.assertEqual(result, resolve_intent(raw, {'Owned.kicad_sym': LIB}))
        result['components'][0]['value'] = '2k'
        with self.assertRaises(ValueError):
            validate_resolved(result)

    def test_generation_malformed_schema_and_duplicate_keys(self):
        for raw in ('not JSON', '{"schema":1,"schema":2}', '{}', '{"a":NaN}', dict(intent(), extra=True)):
            with self.subTest(raw=raw):
                result = resolve_intent(raw, {})
                self.assertEqual((result['execution'], result['outcome']), ('malformed', 'unknown'))

    def test_topology_defects(self):
        cases = []
        raw = intent()
        raw['components'].append(dict(raw['components'][0], id='other'))
        cases.append((raw, 'duplicate_component'))
        raw = intent()
        raw['nets'] = [dict(id='n', name='N', terminals=[raw['no_connects'].pop()])]
        cases.append((raw, 'singleton_net'))
        raw = intent()
        raw['no_connects'].append(raw['no_connects'][0])
        cases.append((raw, 'duplicate_terminal'))
        raw = intent()
        raw['no_connects'] = []
        cases.append((raw, 'unaccounted_pin'))
        raw = intent()
        raw['components'][0]['lib_id'] = 'Owned:Unknown'
        cases.append((raw, 'unknown_symbol'))
        for raw, code in cases:
            with self.subTest(code=code):
                result = resolve_intent(raw, {'Owned.kicad_sym': LIB})
                self.assertEqual(result['outcome'], 'defect')
                self.assertIn(code, [x['code'] for x in result['findings']])

    def test_same_name_group_and_inherited_pins(self):
        library = LIB.replace(b'(name "~")', b'(name "GND")')[:-1] + b'(symbol "Child" (extends "R")))'
        raw = intent()
        raw['libraries'][0]['sha256'] = hashlib.sha256(library).hexdigest()
        raw['components'][0]['lib_id'] = 'Owned:Child'
        raw['no_connects'] = []
        raw['nets'] = [dict(id='g', name='GND', terminals=[dict(component='r', pin={'name': 'GND'})])]
        result = resolve_intent(raw, {'Owned.kicad_sym': library})
        self.assertEqual(result['outcome'], 'pass', result)
        self.assertEqual(result['nets'][0]['terminals'], [dict(component='r', pin='1'), dict(component='r', pin='2')])
        self.assertEqual([s['symbol'] for s in result['components'][0]['sources']], ['R', 'Child'])

    def test_cycles_empty_duplicate_pins_and_trailing_garbage(self):
        variants = [
            (b'(kicad_symbol_lib (symbol "R" (extends "C")) (symbol "C" (extends "R")))', 'unsupported'),
            (b'(kicad_symbol_lib (symbol "R" (extends "Missing")))', 'unsupported'),
            (b'(kicad_symbol_lib (symbol "R"))', 'ok'),
            (LIB.replace(b'(number "2")', b'(number "1")'), 'unsupported'),
            (LIB + b' garbage', 'failed'),
            (b'(' * 129, 'failed'),
        ]
        for library, execution in variants:
            raw = intent()
            raw['libraries'][0]['sha256'] = hashlib.sha256(library).hexdigest()
            result = resolve_intent(raw, {'Owned.kicad_sym': library})
            self.assertEqual(result['execution'], execution, result)
            self.assertNotEqual(result['outcome'], 'pass')

    def test_hierarchy_is_retained_and_unsupported(self):
        raw = intent()
        raw['hierarchy'] = [dict(id='child', parent='root')]
        result = resolve_intent(raw, {'Owned.kicad_sym': LIB})
        self.assertEqual(result['execution'], 'unsupported')
        self.assertEqual(result['hierarchy'], raw['hierarchy'])

    def test_cli_offline_manifest_and_missing_capture(self):
        import json
        import tempfile
        from pathlib import Path
        from agent_ee.connectivity_ir import main
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'intent.json').write_text(json.dumps(intent()))
            (root / 'Owned.kicad_sym').write_bytes(LIB)
            self.assertEqual(main([str(root / 'intent.json'), '--captures', tmp, '--out', str(root / 'out')]), 0)
            manifest = json.loads((root / 'out/manifest.json').read_text())
            self.assertEqual(manifest['artifacts'][0]['sha256'], hashlib.sha256((root / 'out/resolved.json').read_bytes()).hexdigest())
            (root / 'Owned.kicad_sym').unlink()
            self.assertEqual(main([str(root / 'intent.json'), '--captures', tmp, '--out', str(root / 'missing')]), 1)
            self.assertEqual(json.loads((root / 'missing/manifest.json').read_text())['execution'], 'failed')


class StockLibraryTests(unittest.TestCase):
    def test_captured_native_stock_pins(self):
        import os
        from pathlib import Path
        from agent_ee.connectivity_ir import validate_resolved
        captures = {}
        libraries = []
        for name in ('Device', 'Regulator_Linear'):
            path = Path('/usr/share/kicad/symbols') / (name + '.kicad_sym')
            if not path.is_file():
                if os.environ.get('AGENT_EE_REQUIRE_TOOLCHAIN') == '1':
                    self.fail('required stock symbol library missing: ' + str(path))
                self.skipTest('stock symbol library missing: ' + str(path))
            data = path.read_bytes()
            captures[path.name] = data
            libraries.append(dict(name=name, path=path.name, sha256=hashlib.sha256(data).hexdigest()))
        raw = dict(schema='agent-ee.connectivity-intent/1', libraries=libraries,
                   components=[dict(id='r', reference='R1', lib_id='Device:R', value='1k', footprint='', units=[1]),
                               dict(id='u', reference='U1', lib_id='Regulator_Linear:AMS1117-3.3', value='AMS1117-3.3', footprint='', units=[1])],
                   nets=[dict(id='vin', name='VIN', terminals=[dict(component='r', pin={'number': '1'}), dict(component='u', pin={'name': 'VI'})])],
                   no_connects=[dict(component='r', pin={'number': '2'}), dict(component='u', pin={'number': '1'}), dict(component='u', pin={'number': '2'})])
        result = resolve_intent(raw, captures)
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        validate_resolved(result)
        self.assertEqual(result['nets'][0]['terminals'], [dict(component='r', pin='1'), dict(component='u', pin='3')])
        pins = {c['id']: {p['number']: p for p in c['pins']} for c in result['components']}
        self.assertEqual(pins['r']['1']['electrical_type'], 'passive')
        self.assertEqual((pins['u']['3']['name'], pins['u']['3']['electrical_type']), ('VI', 'power_in'))
        self.assertEqual([s['symbol'] for s in result['components'][1]['sources']], ['AMS1117-1.5', 'AMS1117-3.3'])
