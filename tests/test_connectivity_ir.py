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
        self.assertEqual([s['symbol'] for s in result['components'][1]['sources']], ['AP1117-15', 'AMS1117-3.3'])


class ResolvedBoundaryTests(unittest.TestCase):
    def test_rehashed_invalid_pin_metadata_is_rejected(self):
        import copy
        from agent_ee.connectivity_ir import validate_resolved
        valid = resolve_intent(intent(), {'Owned.kicad_sym': LIB})
        for field, value in [('electrical_type', 'imaginary'), ('x_nm', 1.5), ('orientation', 45), ('unit', -1)]:
            result = copy.deepcopy(valid)
            result['components'][0]['pins'][0][field] = value
            payload = {k: v for k, v in result.items() if k != 'canonical_sha256'}
            result['canonical_sha256'] = hashlib.sha256(canonical_resolved(payload)).hexdigest()
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_resolved(result)

    def test_quoted_comments_power_and_common_unit_pins(self):
        library = b'''(kicad_symbol_lib ; comment "unbalanced quote ignored
        (symbol "Supply" (power)
          (symbol "Supply_0_1" (pin power_in line (at 0 0 0)
             (name "GND\\\"return" (effects (font (size 1 1)))) (number "1")))
          (symbol "Supply_1_1" (pin power_in line (at 2.54 0 180)
             (name "GND\\\"return") (number "2")))))'''
        raw = intent()
        raw['libraries'][0]['sha256'] = hashlib.sha256(library).hexdigest()
        raw['components'][0]['lib_id'] = 'Owned:Supply'
        result = resolve_intent(raw, {'Owned.kicad_sym': library})
        self.assertEqual(result['outcome'], 'pass', result)
        self.assertTrue(result['components'][0]['power'])
        self.assertEqual([p['name'] for p in result['components'][0]['pins']], ['GND"return', 'GND"return'])
        self.assertEqual([p['unit'] for p in result['components'][0]['pins']], [0, 1])

    def test_common_only_units_do_not_license_arbitrary_selections(self):
        library = LIB.replace(b'R_1_1', b'R_0_1')
        for units, status in (([1], ('ok', 'pass', 'complete')),
                              ([99], ('unsupported', 'unknown', 'missing')),
                              ([1, 99], ('unsupported', 'unknown', 'missing'))):
            raw = intent()
            raw['libraries'][0]['sha256'] = hashlib.sha256(library).hexdigest()
            raw['components'][0]['units'] = units
            with self.subTest(units=units):
                result = resolve_intent(raw, {'Owned.kicad_sym': library})
                self.assertEqual(tuple(result[k] for k in ('execution', 'outcome', 'coverage')), status)
                if status[1] == 'pass':
                    from agent_ee.connectivity_ir import validate_resolved
                    validate_resolved(result)
                    self.assertEqual([p['unit'] for p in result['components'][0]['pins']], [0, 0])
                else:
                    self.assertIn('symbol_unsupported', [f['code'] for f in result['findings']])

    def test_declared_multiunits_include_pinless_units_and_inheritance(self):
        library = b'''(kicad_symbol_lib
          (symbol "R"
            (symbol "R_0_1" (pin power_in line (at 0 0 0) (name "GND") (number "1")))
            (symbol "R_1_1" (pin passive line (at 1 0 0) (name "A") (number "2")))
            (symbol "R_2_1" (pin passive line (at 2 0 0) (name "B") (number "3")))
            (symbol "R_3_1" (rectangle (start 0 0) (end 1 1))))
          (symbol "Child" (extends "R")))'''
        for symbol in ('R', 'Child'):
            for units, numbers in (([1], ['1', '2']), ([2], ['1', '3']),
                                   ([1, 2], ['1', '2', '3']), ([3], ['1']),
                                   ([4], None), ([1, 4], None)):
                raw = intent()
                raw['libraries'][0]['sha256'] = hashlib.sha256(library).hexdigest()
                raw['components'][0].update(lib_id='Owned:' + symbol, units=units)
                raw['no_connects'] = [dict(component='r', pin={'number': n}) for n in numbers or []]
                with self.subTest(symbol=symbol, units=units):
                    result = resolve_intent(raw, {'Owned.kicad_sym': library})
                    if numbers is None:
                        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'))
                    else:
                        from agent_ee.connectivity_ir import validate_resolved
                        validate_resolved(result)
                        self.assertEqual([p['number'] for p in result['components'][0]['pins']], numbers)
                        self.assertEqual(result['components'][0]['sources'][0]['symbol'], 'R')

    def test_failed_capture_dominates_unsupported_in_all_orders(self):
        import copy
        import itertools
        for capture in (None, b'not a library', LIB + b' '):
            for hierarchy in ([], [dict(id='child')]):
                raw = intent()
                raw['libraries'].append(dict(name='Missing', path='Missing.kicad_sym',
                    sha256=hashlib.sha256(capture or b'').hexdigest() if capture != LIB + b' ' else '0' * 64))
                raw['components'].append(dict(raw['components'][0], id='s', reference='R2', units=[99]))
                raw['hierarchy'] = hierarchy
                captures = {'Owned.kicad_sym': LIB}
                if capture is not None:
                    captures['Missing.kicad_sym'] = capture
                expected = None
                for libraries in itertools.permutations(raw['libraries']):
                    for components in itertools.permutations(raw['components']):
                        case = copy.deepcopy(raw)
                        case['libraries'], case['components'] = list(libraries), list(components)
                        with self.subTest(capture=capture, hierarchy=hierarchy, components=[c['id'] for c in components]):
                            result = resolve_intent(case, captures)
                            self.assertEqual(tuple(result[k] for k in ('execution', 'outcome', 'coverage')),
                                             ('failed', 'unknown', 'missing'))
                            codes = {f['code'] for f in result['findings']}
                            self.assertTrue({'library_coverage', 'symbol_unsupported'} <= codes)
                            if hierarchy:
                                self.assertIn('hierarchy_unsupported', codes)
                            if expected is not None:
                                self.assertEqual(result, expected)
                            expected = result

    def test_pin_binding_to_net_and_no_connect_rejected(self):
        raw = intent()
        raw['nets'] = [dict(id='n', name='N', terminals=raw['no_connects'][:])]
        result = resolve_intent(raw, {'Owned.kicad_sym': LIB})
        self.assertEqual(result['outcome'], 'defect')
        self.assertEqual(sum(f['code'] == 'duplicate_terminal' for f in result['findings']), 2)

    def test_invalid_library_pin_nodes_return_status(self):
        for fragment in (b'(symbol (unexpected))', b'(symbol "R_1_1" (pin (bad) line))'):
            library = b'(kicad_symbol_lib (symbol "R" ' + fragment + b'))'
            raw = intent()
            raw['libraries'][0]['sha256'] = hashlib.sha256(library).hexdigest()
            self.assertNotEqual(resolve_intent(raw, {'Owned.kicad_sym': library})['outcome'], 'pass')

    def test_coordinates_never_round_subnanometre_values(self):
        raw = intent()
        for value in (b'0.00000099999999999999999999999999999999999999999', b'NaN', b'1e1000000', b'0.0000001'):
            library = LIB.replace(b'3.81 270', value + b' 270')
            raw['libraries'][0]['sha256'] = hashlib.sha256(library).hexdigest()
            with self.subTest(value=value):
                result = resolve_intent(raw, {'Owned.kicad_sym': library})
                self.assertNotEqual(result['outcome'], 'pass')

    def test_shuffled_components_nets_and_terminals_are_canonical(self):
        import copy
        raw = intent()
        raw['components'].append(dict(raw['components'][0], id='s', reference='R2'))
        raw['no_connects'] = []
        raw['nets'] = [dict(id=number, name=number, terminals=[dict(component=c, pin={'number': number}) for c in ('r', 's')]) for number in ('1', '2')]
        shuffled = copy.deepcopy(raw)
        shuffled['components'].reverse()
        shuffled['nets'].reverse()
        for net in shuffled['nets']:
            net['terminals'].reverse()
        first = resolve_intent(raw, {'Owned.kicad_sym': LIB})
        self.assertEqual(first['outcome'], 'pass', first)
        self.assertEqual(first, resolve_intent(shuffled, {'Owned.kicad_sym': LIB}))

    def test_hash_mismatch_and_duplicate_ids_fail_closed(self):
        mismatch = resolve_intent(intent(), {'Owned.kicad_sym': LIB + b' '})
        self.assertEqual((mismatch['execution'], mismatch['outcome']), ('failed', 'unknown'))
        raw = intent()
        raw['components'].append(dict(raw['components'][0], reference='R2'))
        result = resolve_intent(raw, {'Owned.kicad_sym': LIB})
        self.assertEqual(result['outcome'], 'defect')
        self.assertIn('duplicate_component', [f['code'] for f in result['findings']])

    def test_alternate_and_contradictory_common_pins_are_unsupported(self):
        for library in (LIB.replace(b'R_1_1', b'R_1_2'),
                        LIB.replace(b'(number "2")', b'(number "1")')):
            raw = intent()
            raw['libraries'][0]['sha256'] = hashlib.sha256(library).hexdigest()
            result = resolve_intent(raw, {'Owned.kicad_sym': library})
            self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'))

    def test_cli_symlink_capture_is_missing_coverage(self):
        import json
        import tempfile
        from pathlib import Path
        from agent_ee.connectivity_ir import main
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'intent.json').write_text(json.dumps(intent()))
            (root / 'real.kicad_sym').write_bytes(LIB)
            (root / 'Owned.kicad_sym').symlink_to(root / 'real.kicad_sym')
            status = main([str(root / 'intent.json'), '--captures', tmp, '--out', str(root / 'out')])
            self.assertEqual(status, 1)
            self.assertEqual(json.loads((root / 'out/manifest.json').read_text())['execution'], 'failed')

    def test_rehashed_nonlist_findings_are_malformed(self):
        from agent_ee.connectivity_ir import validate_resolved
        for findings in (None, {}, '', False, 0):
            result = resolve_intent(intent(), {'Owned.kicad_sym': LIB})
            result['findings'] = findings
            result['canonical_sha256'] = hashlib.sha256(canonical_resolved({k: v for k, v in result.items() if k != 'canonical_sha256'})).hexdigest()
            with self.subTest(findings=findings), self.assertRaises(ValueError):
                validate_resolved(result)

    def test_nul_pin_metadata_cannot_produce_usable_resolution(self):
        for library in (LIB.replace(b'(name "~")', b'(name "bad\x00name")'),
                        LIB.replace(b'R_1_1', b'bad\x00R_1_1')):
            raw = intent()
            raw['libraries'][0]['sha256'] = hashlib.sha256(library).hexdigest()
            with self.subTest(library=library):
                result = resolve_intent(raw, {'Owned.kicad_sym': library})
                self.assertNotEqual(result['outcome'], 'pass')
