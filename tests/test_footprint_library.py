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


def fixture(name, pads, attr='(attr smd)'):
    return (f'(footprint "{name}" (version 20240108) (generator "owned") (layer "F.Cu") {attr}'
            + ''.join(pads) + ')')


def smd(number, x, y=0, layers='"F.Cu" "F.Paste" "F.Mask"', shape='rect', extra=''):
    return f'(pad "{number}" smd {shape} (at {x} {y}) (size 1 1) {extra}(layers {layers}))'


def tht(number, x, y=0, kind='thru_hole', drill=0.3, layers='"*.Cu" "*.Mask"'):
    return f'(pad "{number}" {kind} circle (at {x} {y}) (size 0.6 0.6) (drill {drill}) (layers {layers}))'


# Owned land patterns for each declared case; none is copied from a stock library.
CONN_MP = fixture('ConnMP', [smd(1, 0), smd(2, 1), smd('MP', -2, 2), smd('MP', 3, 2)])
QFN_TV = fixture('QfnTV', [smd(n, n) for n in (1, 2, 3, 4)] + [smd(5, 2, 3, layers='"F.Cu" "F.Mask"'),
                 tht(5, 1.5, 3), tht(5, 2.5, 3), smd('', 2, 3, layers='"F.Paste"')])
MOUNTING_HOLE = fixture('MountingHole', [tht('', 0, kind='np_thru_hole', drill=3)],
                        '(attr exclude_from_pos_files exclude_from_bom)')
MOUNTING_PAD = fixture('MountingPad', [tht(1, 0, drill=3)], '(attr exclude_from_pos_files exclude_from_bom)')
FIDUCIAL = fixture('Fiducial', [smd('', 0, layers='"F.Cu" "F.Mask"', shape='circle')], '(attr smd exclude_from_bom)')
NO_ATTR = SOT3.replace(' (attr smd)', '').replace('"SOT3"', '"NoAttr"', 1)
CUSTOM = fixture('CustomPad', [smd(1, 0, shape='custom', extra='(options (clearance outline) (anchor rect)) '
                               '(primitives (gr_poly (pts (xy -0.5 -0.5) (xy 1 -0.5) (xy 1 0.5)) (width 0) (fill yes))) '),
                               smd(2, 1), smd(3, 0, 2)])
FIXTURES = dict(ConnMP=CONN_MP, QfnTV=QFN_TV, MountingHole=MOUNTING_HOLE, MountingPad=MOUNTING_PAD,
                Fiducial=FIDUCIAL, NoAttr=NO_ATTR, CustomPad=CUSTOM)


def library(*names):
    manifest = {'schema': 'agent-ee.footprint-libraries/1', 'complete': True,
                'libraries': [{'name': 'Owned', 'members': []}]}
    files = {}
    for name in names:
        data = FIXTURES[name].encode()
        path = f'owned.pretty/{name}.kicad_mod'
        manifest['libraries'][0]['members'].append({'path': path, 'sha256': hashlib.sha256(data).hexdigest()})
        files[path] = data
    return manifest, files


def owner_declarations(footprints=(), pinless=()):
    entries = []
    for footprint, changes in footprints:
        entry = dict(footprint=footprint, mechanical_pads=[], thermal_via_pads=[], custom_pads=[],
                     assembly=None, rationale='owner reviewed land pattern')
        entry.update(changes)
        entries.append(entry)
    return dict(schema='agent-ee.footprint-declarations/1', footprints=entries,
                pinless_symbols=[dict(lib_id=x, rationale='owner reviewed non-electrical part') for x in pinless])


class DeclarationTests(unittest.TestCase):
    def check(self, names, numbers, components, declarations=None):
        from agent_ee.footprint_library import check_assignments
        manifest, files = library(*names)
        lib, data = symbol_capture(numbers)
        files[lib['path']] = data
        declaration = declarations_for(lib, components)
        return check_assignments(manifest, files, declaration, declarations=declarations)

    def codes(self, result):
        return sorted(f['code'] for f in result['findings'])

    def test_undeclared_mp_pads_stay_extra_and_declared_ones_are_mechanical(self):
        component = [dict(footprint='Owned:ConnMP', connector={'rows': 1, 'columns': 2})]
        result = self.check(['ConnMP'], ('1', '2'), component)
        self.assertEqual(result['outcome'], 'defect')
        self.assertEqual(result['findings'][0]['extra_pads'], ['MP'])
        self.assertEqual(self.codes(result), ['connector_count', 'pad_pin_parity'])
        declared = owner_declarations([('Owned:ConnMP', dict(mechanical_pads=['MP']))])
        result = self.check(['ConnMP'], ('1', '2'), component, declared)
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result['findings'])
        assignment = result['assignments'][0]
        self.assertEqual(assignment['mechanical_pads'], ['MP'])
        self.assertEqual(assignment['footprint_declaration'], declared['footprints'][0])
        self.assertEqual(result['declarations_sha256'], result['index']['declarations_sha256'])
        pads = [p for p in result['index']['footprints'][0]['pads'] if p['number'] == 'MP']
        self.assertEqual([(p['role'], p['declared']) for p in pads], [('mechanical', 'mechanical')] * 2)

    def test_declared_mechanical_pads_do_not_hide_bad_designs(self):
        declared = owner_declarations([('Owned:ConnMP', dict(mechanical_pads=['MP']))])
        component = [dict(footprint='Owned:ConnMP', connector={'rows': 1, 'columns': 3})]
        result = self.check(['ConnMP'], ('1', '2', '3'), component, declared)
        self.assertEqual(result['outcome'], 'defect')
        self.assertEqual(result['findings'][0]['missing_pads'], ['3'])
        self.assertEqual(result['findings'][1], dict(code='connector_count', expected=3, actual=2,
                         reference='X1', footprint='Owned:ConnMP', lib_id='Owned:Four'))
        result = self.check(['ConnMP'], ('1', '2', 'MP'), [dict(footprint='Owned:ConnMP')], declared)
        self.assertEqual((result['outcome'], result['findings'][0]['missing_pads']), ('defect', ['MP']))

    def test_stale_or_unknown_declarations_are_unknown_not_pass(self):
        component = [dict(footprint='Owned:ConnMP')]
        for declared in (owner_declarations([('Owned:ConnMP', dict(mechanical_pads=['MP', 'X']))]),
                         owner_declarations([('Owned:ConnMP', dict(mechanical_pads=['MP'])),
                                             ('Owned:Absent', dict(mechanical_pads=['MP']))]),
                         owner_declarations([('Owned:ConnMP', dict(mechanical_pads=['MP'], custom_pads=['1']))]),
                         owner_declarations([('Owned:ConnMP', dict(mechanical_pads=['MP'], thermal_via_pads=['1']))])):
            with self.subTest(declared=declared):
                result = self.check(['ConnMP'], ('1', '2'), component, declared)
                self.assertEqual((result['execution'], result['outcome'], result['coverage']),
                                 ('failed', 'unknown', 'missing'))
                self.assertEqual(result['assignments'][0]['status'], 'unknown')

    def test_malformed_declarations_are_malformed(self):
        good = owner_declarations([('Owned:ConnMP', dict(mechanical_pads=['MP']))])
        bad = [dict(good, schema='agent-ee.footprint-declarations/0'), dict(good, extra=1),
               owner_declarations([('Owned:ConnMP', {})]),
               owner_declarations([('Owned:ConnMP', dict(mechanical_pads=['MP', 'MP']))]),
               owner_declarations([('Owned:ConnMP', dict(mechanical_pads=['MP'], thermal_via_pads=['MP']))]),
               owner_declarations([('Owned:ConnMP', dict(mechanical_pads=['MP'], rationale=''))]),
               owner_declarations([('ConnMP', dict(mechanical_pads=['MP']))]),
               owner_declarations([('Owned:ConnMP', dict(assembly='mixed'))]),
               owner_declarations(pinless=['Owned:Four', 'Owned:Four'])]
        for declared in bad:
            with self.subTest(declared=declared):
                result = self.check(['ConnMP'], ('1', '2'), [dict(footprint='Owned:ConnMP')], declared)
                self.assertEqual((result['execution'], result['outcome']), ('malformed', 'unknown'))
                self.assertEqual({f['code'] for f in result['findings']}, {'declaration'})

    def test_declared_pinless_mounting_hole_and_fiducial(self):
        components = [dict(footprint='Owned:MountingHole', assembly='through_hole')]
        self.assertEqual(self.check(['MountingHole'], (), components)['outcome'], 'unknown')
        pinless = owner_declarations(pinless=['Owned:Four'])
        # No native smd/through_hole attribute: assembly stays unknown until declared.
        self.assertEqual(self.check(['MountingHole'], (), components, pinless)['outcome'], 'unknown')
        declared = owner_declarations([('Owned:MountingHole', dict(assembly='through_hole'))], ['Owned:Four'])
        result = self.check(['MountingHole'], (), components, declared)
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result['findings'])
        self.assertEqual(result['assignments'][0]['pinless'], declared['pinless_symbols'][0])
        mismatch = self.check(['MountingHole'], (), [dict(components[0], assembly='smd')], declared)
        self.assertEqual(self.codes(mismatch), ['assembly_mismatch'])
        undeclared = self.check(['Fiducial'], (), [dict(footprint='Owned:Fiducial')], pinless)
        self.assertEqual((undeclared['execution'], undeclared['outcome']), ('failed', 'unknown'))
        declared = owner_declarations([('Owned:Fiducial', dict(mechanical_pads=['']))], ['Owned:Four'])
        result = self.check(['Fiducial'], (), [dict(footprint='Owned:Fiducial')], declared)
        self.assertEqual(result['outcome'], 'pass', result['findings'])
        self.assertEqual(result['assignments'][0]['mechanical_pads'], [''])

    def test_pinless_declaration_keeps_bad_designs_failing(self):
        declared = owner_declarations([('Owned:MountingPad', dict(assembly='through_hole'))], ['Owned:Four'])
        result = self.check(['MountingPad'], (), [dict(footprint='Owned:MountingPad', assembly='through_hole')], declared)
        self.assertEqual((result['outcome'], result['findings'][0]['extra_pads']), ('defect', ['1']))
        result = self.check(['MountingPad'], (), [dict(footprint='', assembly='through_hole')], declared)
        self.assertEqual(self.codes(result), ['empty_footprint'])
        declared = owner_declarations([('Owned:ConnMP', dict(mechanical_pads=['MP']))], ['Owned:Four'])
        result = self.check(['ConnMP'], ('1', '2'), [dict(footprint='Owned:ConnMP')], declared)
        self.assertEqual((result['execution'], result['outcome']), ('failed', 'unknown'))
        self.assertIn('contradicts', result['assignments'][0]['findings'][0]['message'])

    def test_thermal_via_qfn_requires_declaration_and_keeps_parity(self):
        component = [dict(footprint='Owned:QfnTV')]
        pins = ('1', '2', '3', '4', '5')
        undeclared = self.check(['QfnTV'], pins, component)
        self.assertEqual((undeclared['execution'], undeclared['outcome']), ('failed', 'unknown'))
        self.assertIn('mixed', undeclared['assignments'][0]['findings'][0]['message'])
        declared = owner_declarations([('Owned:QfnTV', dict(thermal_via_pads=['5']))])
        result = self.check(['QfnTV'], pins, component, declared)
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result['findings'])
        vias = [p for p in result['index']['footprints'][0]['pads'] if p.get('thermal_via')]
        self.assertEqual([(p['number'], p['type'], p['role']) for p in vias], [('5', 'thru_hole', 'electrical')] * 2)
        missing = self.check(['QfnTV'], pins[:4], component, declared)
        self.assertEqual((missing['outcome'], missing['findings'][0]['extra_pads']), ('defect', ['5']))
        wrong = self.check(['QfnTV'], pins, [dict(footprint='Owned:QfnTV', assembly='through_hole')], declared)
        self.assertEqual(self.codes(wrong), ['assembly_mismatch'])
        stale = owner_declarations([('Owned:QfnTV', dict(thermal_via_pads=['5', '1']))])
        self.assertEqual(self.check(['QfnTV'], pins, component, stale)['outcome'], 'unknown')

    def test_attr_less_footprint_uses_declared_assembly_only(self):
        component = [dict(footprint='Owned:NoAttr')]
        pins = ('1', '2', '3')
        self.assertEqual(self.check(['NoAttr'], pins, component)['outcome'], 'unknown')
        declared = owner_declarations([('Owned:NoAttr', dict(assembly='smd'))])
        self.assertEqual(self.check(['NoAttr'], pins, component, declared)['outcome'], 'pass')
        wrong = self.check(['NoAttr'], pins, [dict(footprint='Owned:NoAttr', assembly='through_hole')], declared)
        self.assertEqual(self.codes(wrong), ['assembly_mismatch'])
        contradiction = owner_declarations([('Owned:NoAttr', dict(assembly='through_hole'))])
        result = self.check(['NoAttr'], pins, [dict(footprint='Owned:NoAttr', assembly='through_hole')], contradiction)
        self.assertEqual((result['execution'], result['outcome']), ('failed', 'unknown'))
        redundant = owner_declarations([('Owned:ConnMP', dict(mechanical_pads=['MP'], assembly='smd'))])
        result = self.check(['ConnMP'], ('1', '2'), [dict(footprint='Owned:ConnMP')], redundant)
        self.assertEqual((result['execution'], result['outcome']), ('failed', 'unknown'))

    def test_declared_custom_pad_participates_in_numbering_only(self):
        component = [dict(footprint='Owned:CustomPad')]
        pins = ('1', '2', '3')
        self.assertEqual(index_footprints(*library('CustomPad'))['outcome'], 'unknown')
        declared = owner_declarations([('Owned:CustomPad', dict(custom_pads=['1']))])
        result = self.check(['CustomPad'], pins, component, declared)
        self.assertEqual(result['outcome'], 'pass', result['findings'])
        pad = next(p for p in result['index']['footprints'][0]['pads'] if p['number'] == '1')
        self.assertEqual((pad['shape'], pad['geometry']), ('custom', 'declared_custom'))
        self.assertEqual(self.check(['CustomPad'], pins[:2], component, declared)['outcome'], 'defect')
        FIXTURES['Padstack'] = CUSTOM.replace('"CustomPad"', '"Padstack"', 1).replace(
            '(options', '(padstack (mode front_inner_back)) (options', 1)
        self.addCleanup(FIXTURES.pop, 'Padstack')
        manifest, files = library('Padstack')
        result = index_footprints(manifest, files, owner_declarations([('Owned:Padstack', dict(custom_pads=['1']))]))
        self.assertEqual((result['execution'], result['outcome']), ('failed', 'unknown'))

    def test_declarations_are_deterministic_and_change_identity(self):
        manifest, files = library('ConnMP')
        declared = owner_declarations([('Owned:ConnMP', dict(mechanical_pads=['MP']))])
        first = index_footprints(manifest, files, declared)
        self.assertEqual(first, index_footprints(manifest, dict(files), declared))
        self.assertNotEqual(first['canonical_sha256'], index_footprints(manifest, files)['canonical_sha256'])


def declarations_for(lib, components):
    declaration = declarations(lib)
    declaration['components'] = [dict(declaration['components'][0], reference=f'X{i}', **c)
                                 for i, c in enumerate(components, 1)]
    return declaration


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

    def test_declared_fixtures_match_pcbnew_pad_inventory(self):
        from toolchain import require_toolchain
        require_toolchain(native=True)
        import tempfile
        import pcbnew
        from pathlib import Path
        declared = owner_declarations([('Owned:ConnMP', dict(mechanical_pads=['MP'])),
                                       ('Owned:QfnTV', dict(thermal_via_pads=['5'])),
                                       ('Owned:MountingHole', dict(assembly='through_hole')),
                                       ('Owned:MountingPad', dict(assembly='through_hole')),
                                       ('Owned:Fiducial', dict(mechanical_pads=[''])),
                                       ('Owned:NoAttr', dict(assembly='smd')),
                                       ('Owned:CustomPad', dict(custom_pads=['1']))])
        manifest, files = library(*FIXTURES)
        indexed = index_footprints(manifest, files, declared)
        self.assertEqual(indexed['execution'], 'ok', indexed['findings'])
        kinds = {pcbnew.PAD_ATTRIB_SMD: 'smd', pcbnew.PAD_ATTRIB_PTH: 'thru_hole', pcbnew.PAD_ATTRIB_NPTH: 'np_thru_hole'}
        with tempfile.TemporaryDirectory() as tmp:
            for name, text in FIXTURES.items():
                with self.subTest(name=name):
                    (Path(tmp) / f'{name}.kicad_mod').write_text(text)
                    native = pcbnew.FootprintLoad(tmp, name)
                    self.assertIsNotNone(native)
                    actual = sorted((p.GetNumber(), kinds[p.GetAttribute()]) for p in native.Pads())
                    fp = next(x for x in indexed['footprints'] if x['name'] == name)
                    self.assertEqual(sorted((p['number'], p['type']) for p in fp['pads']), actual)
                    native_smd = bool(native.GetAttributes() & pcbnew.FP_SMD)
                    native_tht = bool(native.GetAttributes() & pcbnew.FP_THROUGH_HOLE)
                    self.assertEqual(native_smd, 'smd' in fp['attributes'])
                    self.assertEqual(native_tht, 'through_hole' in fp['attributes'])
