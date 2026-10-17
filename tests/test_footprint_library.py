"""Independent footprint examples; no installed library files are authoritative."""
import hashlib
import unittest

from agent_ee.footprint_library import index_footprints


def capture(text, name='SOT3'):
    data = text.encode()
    path = f'owned.pretty/{name}.kicad_mod'
    manifest = {'schema': 'agent-ee.footprint-libraries/1', 'complete': True,
                'libraries': [{'name': 'Owned', 'members': [
                    {'path': path, 'sha256': hashlib.sha256(data).hexdigest()}]}]}
    return manifest, {path: data}


SOT3 = '''(footprint "SOT3" (version 20240108) (generator "owned")
(layer "F.Cu") (descr "Owned three-pad fixture") (tags "owned") (attr smd)
(pad "1" smd rect (at -1 0) (size 1 1) (layers "F.Cu" "F.Paste" "F.Mask"))
(pad "2" smd rect (at 1 0) (size 1 1) (layers "F.Cu" "F.Paste" "F.Mask"))
(pad "3" smd rect (at 0 2) (size 1 1) (layers "F.Cu" "F.Paste" "F.Mask")))'''


class FootprintTests(unittest.TestCase):
    def test_complete_index_preserves_native_inventory_and_hash(self):
        manifest, files = capture(SOT3)
        result = index_footprints(manifest, files)
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'info'))
        fp = result['footprints'][0]
        self.assertEqual(fp['lib_id'], 'Owned:SOT3')
        self.assertEqual(fp['electrical_numbers'], ['1', '2', '3'])
        self.assertEqual(len(fp['pads']), 3)
        self.assertEqual(fp['source'], manifest['libraries'][0]['members'][0])

    def test_incomplete_or_drifted_member_is_unknown(self):
        manifest, files = capture(SOT3)
        for bad in ({}, {next(iter(files)): b'changed'}):
            result = index_footprints(manifest, bad)
            self.assertEqual((result['execution'], result['outcome'], result['coverage']),
                             ('failed', 'unknown', 'missing'))
        manifest['complete'] = False
        self.assertEqual(index_footprints(manifest, files)['outcome'], 'unknown')

    def test_duplicate_thermal_pad_and_qualified_exclusions(self):
        extra = '''(pad "3" smd rect (at 0 3) (size 1 1) (layers "F.Cu"))
(pad "" smd rect (at 0 4) (size 1 1) (layers "F.Paste"))
(pad "" np_thru_hole circle (at 0 5) (size 1 1) (drill 0.5) (layers "*.Cu" "*.Mask"))'''
        manifest, files = capture(SOT3[:-1] + extra + ')')
        result = index_footprints(manifest, files)
        self.assertEqual(result['execution'], 'ok')
        fp = result['footprints'][0]
        self.assertEqual(fp['electrical_numbers'], ['1', '2', '3'])
        self.assertEqual([p['number'] for p in fp['pads']].count('3'), 2)
        self.assertEqual({p['role'] for p in fp['pads']}, {'electrical', 'paste', 'mechanical'})

    def test_custom_or_malformed_pad_refuses_whole_index(self):
        for data in (SOT3.replace('smd rect', 'smd custom', 1), SOT3[:-1],
                     SOT3.replace('(pad "1"', '(pad ""', 1)):
            manifest, files = capture(data)
            self.assertEqual(index_footprints(manifest, files)['outcome'], 'unknown')


def symbol_capture(numbers=('1', '2', '3', '4'), power=False):
    pins = ''.join(f'(pin passive line (at {i} 0 0) (length 1) (name "~") (number "{n}"))'
                   for i, n in enumerate(numbers))
    data = ('(kicad_symbol_lib (version 20231120) (generator "owned") '
            '(symbol "Four" ' + ('(power)' if power else '') +
            '(symbol "Four_1_1" ' + pins + ')))').encode()
    return {'name': 'Owned', 'path': 'owned.kicad_sym', 'sha256': hashlib.sha256(data).hexdigest()}, data


def declarations(lib, **changes):
    component = dict(reference='U1', lib_id='Owned:Four', footprint='Owned:SOT3',
                     assembly='smd', connector=None)
    component.update(changes)
    return dict(schema='agent-ee.footprint-assignments-input/1', libraries=[lib], components=[component])


class AssignmentTests(unittest.TestCase):
    def check(self, numbers=('1', '2', '3', '4'), power=False, **changes):
        from agent_ee.footprint_library import check_assignments
        manifest, files = capture(SOT3)
        lib, data = symbol_capture(numbers, power)
        files[lib['path']] = data
        return check_assignments(manifest, files, declarations(lib, **changes))

    def test_pin4_is_defect_named_for_each_reference(self):
        from agent_ee.footprint_library import check_assignments
        manifest, files = capture(SOT3)
        lib, data = symbol_capture()
        files[lib['path']] = data
        declaration = declarations(lib)
        declaration['components'].append(dict(declaration['components'][0], reference='U2'))
        result = check_assignments(manifest, files, declaration)
        self.assertEqual(result['outcome'], 'defect')
        self.assertEqual([x['reference'] for x in result['findings']], ['U1', 'U2'])
        for finding in result['findings']:
            self.assertEqual(finding['missing_pads'], ['4'])
            self.assertEqual(finding['footprint'], 'Owned:SOT3')

    def test_three_pins_pass_but_connector_count_and_assembly_fail(self):
        self.assertEqual(self.check(('1', '2', '3'))['outcome'], 'pass')
        result = self.check(('1', '2', '3'), connector={'rows': 1, 'columns': 4}, assembly='through_hole')
        self.assertEqual({x['code'] for x in result['findings']}, {'connector_count', 'assembly_mismatch'})

    def test_empty_and_absent_are_defects_after_complete_index(self):
        for fp, code in (('', 'empty_footprint'), ('Owned:Absent', 'absent_footprint')):
            result = self.check(footprint=fp)
            self.assertEqual(result['outcome'], 'defect')
            self.assertEqual(result['findings'][0]['code'], code)

    def test_power_exclusion_uses_native_metadata_not_reference(self):
        self.assertEqual(self.check(power=True, footprint='')['assignments'][0]['status'], 'excluded')
        self.assertEqual(self.check(reference='#PWR1', footprint='')['outcome'], 'defect')

    def test_alias_resolution_uses_exact_captured_table_and_hash(self):
        from agent_ee.footprint_library import resolve_package
        name = 'R_0805_2012Metric'
        manifest, files = capture(SOT3.replace('"SOT3"', '"' + name + '"', 1), name)
        manifest['libraries'][0]['name'] = 'Resistor_SMD'
        table = dict(schema='agent-ee.package-aliases/1', convention='explicit-imperial-to-metric',
                     rows=[dict(family='resistor', package='0805', footprint='Resistor_SMD:' + name)])
        result = resolve_package(table, 'resistor', '0805', manifest, files)
        self.assertEqual(result['footprint'], 'Resistor_SMD:R_0805_2012Metric')
        self.assertEqual(result['source']['sha256'], hashlib.sha256(next(iter(files.values()))).hexdigest())
        self.assertEqual(len(result['table_sha256']), 64)
        table['rows'].append(dict(table['rows'][0]))
        result = resolve_package(table, 'resistor', '0805', manifest, files)
        self.assertEqual((result['outcome'], result['footprint']), ('unknown', None))

    def test_whole_multiunit_device_not_connected_pin_subset(self):
        from agent_ee.footprint_library import check_assignments
        manifest, files = capture(SOT3)
        lib, data = symbol_capture(('1', '2', '3'))
        data = data[:-2] + b'(symbol "Four_2_1" (pin passive line (at 0 0 0) (length 1) (name "hidden") (number "4")))))'
        lib['sha256'] = hashlib.sha256(data).hexdigest()
        files[lib['path']] = data
        result = check_assignments(manifest, files, declarations(lib))
        self.assertEqual(result['outcome'], 'defect')
        self.assertEqual(result['assignments'][0]['pin_numbers'], ['1', '2', '3', '4'])

    def test_pin_or_library_drift_cannot_be_treated_as_selection_absence(self):
        from agent_ee.footprint_library import check_assignments
        manifest, files = capture(SOT3)
        lib, data = symbol_capture()
        result = check_assignments(manifest, files, declarations(lib, footprint='Owned:Absent'))
        self.assertEqual((result['execution'], result['outcome']), ('failed', 'unknown'))
        self.assertFalse(any(x['code'] == 'absent_footprint' for x in result['findings']))

    def test_index_order_is_deterministic(self):
        from agent_ee.footprint_library import canonical
        manifest, files = capture(SOT3)
        first = canonical(index_footprints(manifest, files))
        self.assertEqual(first, canonical(index_footprints(manifest, dict(reversed(list(files.items()))))))

    def test_duplicate_thermal_pads_do_not_create_extra_electrical_pins(self):
        from agent_ee.footprint_library import check_assignments
        text = SOT3[:-1] + '(pad "3" smd rect (at 1 3) (size 1 1) (layers "F.Cu")))'
        manifest, files = capture(text)
        lib, data = symbol_capture(('1', '2', '3'))
        files[lib['path']] = data
        result = check_assignments(manifest, files, declarations(lib))
        self.assertEqual(result['outcome'], 'pass')
        self.assertEqual(len(result['index']['footprints'][0]['pads']), 4)

    def test_one_by_four_connector_assigned_five_numbered_pads(self):
        from agent_ee.footprint_library import check_assignments
        extra = ''.join(f'(pad "{n}" smd rect (at {n} 0) (size 1 1) (layers "F.Cu"))' for n in (4, 5))
        manifest, files = capture(SOT3[:-1] + extra + ')')
        lib, data = symbol_capture(('1', '2', '3', '4'))
        files[lib['path']] = data
        result = check_assignments(manifest, files, declarations(lib, connector={'rows': 1, 'columns': 4}))
        self.assertEqual(result['outcome'], 'defect')
        findings = result['assignments'][0]['findings']
        self.assertEqual(findings[0]['extra_pads'], ['5'])
        self.assertEqual(findings[1], dict(code='connector_count', expected=4, actual=5))

    def test_unreadable_unselected_member_prevents_selected_footprint_pass(self):
        from agent_ee.footprint_library import check_assignments
        manifest, files = capture(SOT3)
        manifest['libraries'][0]['members'].append(dict(path='owned.pretty/Unreadable.kicad_mod', sha256='0'*64))
        lib, data = symbol_capture(('1', '2', '3'))
        files[lib['path']] = data
        result = check_assignments(manifest, files, declarations(lib))
        self.assertEqual((result['execution'], result['outcome']), ('failed', 'unknown'))
        self.assertEqual(result['assignments'][0]['status'], 'unknown')

    def test_native_virtual_board_exclusion_is_retained(self):
        from agent_ee.footprint_library import check_assignments
        manifest, files = capture(SOT3)
        lib, data = symbol_capture()
        data = data.replace(b'(symbol "Four" ', b'(symbol "Four" (on_board no) ')
        lib['sha256'] = hashlib.sha256(data).hexdigest()
        files[lib['path']] = data
        result = check_assignments(manifest, files, declarations(lib, footprint=''))
        self.assertEqual(result['assignments'][0]['exclusion'], 'pinned native on_board=no')
        self.assertEqual(result['outcome'], 'info')


class NativePadTests(unittest.TestCase):
    def test_owned_native_inventory_matches_pcbnew(self):
        from toolchain import require_toolchain
        require_toolchain(native=True)
        import tempfile
        import pcbnew
        from pathlib import Path
        variants = [SOT3,
            SOT3[:-1] + '(pad "3" smd rect (at 1 3) (size 1 1) (layers "F.Cu")))',
            SOT3[:-1] + '(pad "" smd rect (at 0 4) (size 1 1) (layers "F.Paste"))'
            '(pad "" np_thru_hole circle (at 0 5) (size 1 1) (drill 0.5) (layers "*.Cu" "*.Mask")))']
        for text in variants:
            with self.subTest(text=text), tempfile.TemporaryDirectory() as tmp:
                manifest, files = capture(text)
                path = Path(tmp) / 'SOT3.kicad_mod'
                path.write_text(text)
                native = pcbnew.FootprintLoad(tmp, 'SOT3')
                self.assertIsNotNone(native)
                actual = sorted(p.GetNumber() for p in native.Pads())
                indexed = index_footprints(manifest, files)
                self.assertEqual(indexed['execution'], 'ok')
                self.assertEqual(sorted(p['number'] for p in indexed['footprints'][0]['pads']), actual)
                self.assertEqual(indexed['footprints'][0]['electrical_numbers'], ['1', '2', '3'])
