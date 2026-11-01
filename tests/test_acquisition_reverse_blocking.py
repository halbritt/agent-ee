"""Pin/pad expectations for the finite ADP7104 development reference."""
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET

from agent_ee import acquisition_reverse_blocking as variant
from agent_ee import easyeda_footprint

CAPTURES = Path(os.environ.get('AGENT_EE_REVERSE_BLOCKING_SOURCES',
    Path(__file__).resolve().parents[1] / 'build/acquisition-reverse-blocking/sources'))
require_captures = unittest.skipUnless(
    all((CAPTURES / name).is_file() for name in ('C2903961.raweasy.json', 'C155367.raweasy.json')),
    'retained pinned supplier captures required')


class RecipeTests(unittest.TestCase):
    def setUp(self):
        self.original = (variant.DATA / 'base-circuit.json').read_bytes()

    def test_new_circuit_has_exact_pinout_and_preserves_other_topology(self):
        before = json.loads(self.original)
        after = json.loads(variant.circuit_bytes(self.original))
        actual = {t['pin']['number']: n['name'] for n in after['nets'] for t in n['terminals']
                  if t['component'] == 'U1'}
        self.assertEqual(actual, {'1': 'V3V3', '2': 'V3V3', '3': 'GND', '5': 'VIN_PROTECTED',
                                 '6': 'GND', '8': 'VIN_PROTECTED', '9': 'GND'})
        self.assertEqual([t['pin']['number'] for t in after['no_connects'] if t['component'] == 'U1'], ['4', '7'])
        for old, new in zip(before['nets'], after['nets']):
            self.assertEqual({**old, 'terminals': [t for t in old['terminals'] if t['component'] != 'U1']},
                             {**new, 'terminals': [t for t in new['terminals'] if t['component'] != 'U1']})
        self.assertEqual(sum(not c['reference'].startswith('#') for c in after['components']), 17)
        selected = {c['reference']: c for c in after['components']}
        self.assertEqual({ref: selected[ref]['value'] for ref in ('U1', 'D1', 'R5', 'R6')},
            {'U1': 'ADP7104ARDZ-3.3-R7', 'D1': 'B0520LW-7-F', 'R5': '1k', 'R6': '2.2k'})
        for old in before['components']:
            if old['reference'] not in ('U1', 'D1', 'R5', 'R6'):
                self.assertEqual(selected[old['reference']], old)
    @require_captures
    def test_generated_inputs_do_not_grant_production_acceptance(self):
        files, report = variant.inputs(self.original, (CAPTURES / 'C2903961.raweasy.json').read_bytes(),
                                       (CAPTURES / 'C155367.raweasy.json').read_bytes())
        self.assertFalse(report['accepted'])
        self.assertEqual(report['file_release'], 'not_assessed')
        self.assertEqual(report['files'], {name: sha256(raw).hexdigest() for name, raw in files.items()})

    def test_changed_base_cannot_be_silently_adopted(self):
        with self.assertRaisesRegex(ValueError, 'exact original'):
            variant.circuit_bytes(self.original + b'\n')
        for name, pin in json.loads((variant.DATA / 'sources.json').read_bytes())['files'].items():
            self.assertEqual(sha256((variant.DATA / name).read_bytes()).hexdigest(), pin)

    @require_captures
    def test_changed_supplier_cannot_be_silently_adopted(self):
        raw = (CAPTURES / 'C2903961.raweasy.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'exact C2903961'):
            variant.footprint_bytes(raw + b'\n')
        diode = (CAPTURES / 'C155367.raweasy.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'exact C155367'):
            variant.diode_footprint_bytes(diode + b'\n')
        for lcsc, source in json.loads((variant.DATA / 'sources.json').read_bytes())['supplier'].items():
            self.assertEqual(sha256((CAPTURES / (lcsc + '.raweasy.json')).read_bytes()).hexdigest(), source['raw_sha256'])

    def test_original_capacitor_locality_selects_correct_new_ground_pins(self):
        def endpoint(ref, pad, net):
            return dict(reference=ref, pad=pad, net=net)
        rows = []
        for ref, pad, net in [('C1', '1', '/VIN_PROTECTED'), ('C2', '5', '/V3V3')]:
            rows.append(dict(id=ref,
                supply=dict(**{'from': endpoint('U1', pad, net), 'to': endpoint(ref, '1', net)}, max_distance_nm=3000000),
                **{'return': dict(**{'from': endpoint('U1', '2', 'GND'), 'to': endpoint(ref, '2', 'GND')}, max_distance_nm=3000000)}))
        original = dict(obligations=rows)
        transformed = variant.locality(original)
        self.assertEqual([(r['supply']['from']['pad'], r['return']['from']['pad'])
                          for r in transformed['obligations']], [('8', '6'), ('1', '3')])
        self.assertEqual(original['obligations'][0]['return']['from']['pad'], '2')
        self.assertTrue(all(r[k]['max_distance_nm'] == 3000000 for r in transformed['obligations']
                            for k in ('supply', 'return')))
        original['obligations'][0]['return']['from']['pad'] = '9'
        with self.assertRaisesRegex(ValueError, 'unrecognized'):
            variant.locality(original)

    @require_captures
    def test_complete_supplier_represents_all_vias_with_explicit_ownership(self):
        parsed = easyeda_footprint.parse_package((CAPTURES / 'C2903961.raweasy.json').read_bytes(), 'C2903961', 200)
        self.assertEqual((parsed['inventory']['pads'], parsed['inventory']['holes'], parsed['inventory']['vias']),
                         (13, 4, 4))
        vias = [p for p in parsed['pad_locators'] if p['record'] == 'VIA']
        self.assertEqual(len(vias), 4)
        self.assertTrue(all(p['number'] == '9' and p['pin_assignment']['pad_index'] == 8 for p in vias))


@require_captures
class NativeFootprintTests(unittest.TestCase):
    def test_complete_supplier_matches_native_saved_copper_holes_and_pin_frame(self):
        from tests.toolchain import require_toolchain
        from tests.land_pattern_fixtures import manifest
        from agent_ee import cpl_rotation, executor, land_pattern
        from fractions import Fraction
        require_toolchain(native=True)
        raw = (CAPTURES / 'C2903961.raweasy.json').read_bytes()
        supplier = easyeda_footprint.parse_package(raw, 'C2903961', 200)
        criteria = dict(threshold='0.98', pin_tolerance_nm='100000', refinement_levels=[2, 4, 8, 16])
        item = dict(registration=dict(translation='declared_origin', rotation='270', mirror=False), pin_aliases=None)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = root / 'original.pretty'
            original.mkdir()
            filename = variant.FOOTPRINT + '.kicad_mod'
            (original / filename).write_bytes(variant.footprint_bytes(raw))
            # Keep native SWIG ownership inside the existing sandbox boundary;
            # pure comparison failures must not leave native objects in a traceback.
            (root / 'generate.py').write_text('''from pathlib import Path
import sys
import pcbnew as k
name = sys.argv[1]
io = k.PCB_IO_KICAD_SEXPR()
for case in ('control', 'omitted-via', 'wrong-drill', 'wrong-pin'):
    fp = io.FootprintLoad('/candidate/original.pretty', name)
    assert fp is not None
    drills = [p for p in fp.Pads() if p.GetAttribute() == k.PAD_ATTRIB_PTH]
    assert len(drills) == 4
    if case == 'omitted-via':
        fp.Remove(drills[0])
    elif case == 'wrong-drill':
        drills[0].SetDrillSize(k.VECTOR2I(254000, 254000))
    elif case == 'wrong-pin':
        drills[0].SetNumber('8')
    out = Path('/work') / (case + '.pretty')
    out.mkdir()
    io.FootprintSave(str(out), fp)
''')
            # Source and work must be disjoint for the normal executor.
            work = root.parent / (root.name + '-native')
            self.addCleanup(shutil.rmtree, work, True)
            saved = executor.execute(root, work, ['/usr/bin/python3', '/candidate/generate.py', variant.FOOTPRINT],
                                     timeout=30)
            self.assertEqual(saved['execution'], 'ok', saved)
            reports, pin1_offset = {}, None
            for case in ('control', 'omitted-via', 'wrong-drill', 'wrong-pin'):
                name = case + '.pretty/' + filename
                captures = {name: (work / name).read_bytes()}
                native = land_pattern.library_footprint(manifest(captures, library=variant.LIBRARY), captures,
                                                       variant.LIBRARY + ':' + variant.FOOTPRINT)
                with self.subTest(case=case):
                    reports[case] = land_pattern.compare(native, supplier, item, criteria)
                if case not in reports:
                    continue
                self.assertEqual(reports[case]['outcome'], 'pass' if case == 'control' else 'defect', reports[case])
                if case == 'control':
                    self.assertEqual(set(reports[case]['copper']), {'F.Cu', 'B.Cu'})
                    self.assertEqual(reports[case]['holes']['decision'], 'pass')
                    self.assertEqual(len(reports[case]['pins']['pairs']), 13)
                    def frame(pattern):
                        return cpl_rotation.classify_pin1([(p['number'], Fraction(p['primitive']['center'][0]),
                            -Fraction(p['primitive']['center'][1])) for p in pattern['pads']])
                    source_pin, source_frame, _ = frame(supplier['pattern'])
                    native_pin, native_frame, _ = frame(native['pattern'])
                    self.assertEqual((source_pin, native_pin), ('bottomside_left', 'leftside_top'))
                    self.assertEqual((source_frame, native_frame), ('sided', 'sided'))
                    self.assertEqual(cpl_rotation.pin1_offset(source_pin, native_pin), 270)
                    pin1_offset = 270
            artifact = os.environ.get('AGENT_EE_VIA_QUALIFICATION_ARTIFACTS')
            if artifact:
                (work / 'geometry-results.json').write_text(json.dumps(dict(
                    supplier_sha256=sha256(raw).hexdigest(), criteria=criteria, registration=item['registration'],
                    reports=reports, supplier_inventory=supplier['inventory'],
                    supplier_vias=[p for p in supplier['pad_locators'] if p['record'] == 'VIA'],
                    complete=len(reports) == 4, cpl_pin1_offset_degrees=pin1_offset,
                    qualification='Copper/drill geometry and pin frame only; no assembly acceptance'), indent=2)+'\n')
                shutil.copytree(work, Path(artifact))

    def test_generated_footprint_preserves_all_copper_and_drills(self):
        from tests.toolchain import require_toolchain
        require_toolchain(native=True)
        import pcbnew
        with tempfile.TemporaryDirectory() as tmp:
            library = Path(tmp) / 'AgentEE_Power.pretty'
            library.mkdir()
            (library / (variant.FOOTPRINT + '.kicad_mod')).write_bytes(
                variant.footprint_bytes((CAPTURES / 'C2903961.raweasy.json').read_bytes()))
            footprint = pcbnew.FootprintLoad(str(library), variant.FOOTPRINT)
            self.assertIsNotNone(footprint)
            pads = list(footprint.Pads())
            self.assertEqual(len(pads), 13)
            numbered = {str(p.GetNumber()): p for p in pads if p.GetAttribute() == pcbnew.PAD_ATTRIB_SMD}
            self.assertEqual(set(numbered), set('123456789'))
            # Independent expected standard top view: pin1 left/top, pin8 right/top.
            for number, expected in [('1', (-2720594, -1905000)), ('8', (2720594, -1905000))]:
                point = numbered[number].GetPosition()
                self.assertEqual((point.x, point.y), expected)
            drills = [p for p in pads if p.GetAttribute() == pcbnew.PAD_ATTRIB_PTH]
            self.assertEqual(len(drills), 4)
            for pad in drills:
                self.assertEqual(str(pad.GetNumber()), '9')
                self.assertEqual((pad.GetSize().x, pad.GetSize().y), (609600, 609600))
                self.assertEqual((pad.GetDrillSize().x, pad.GetDrillSize().y), (304800, 304800))

    def test_generated_diode_preserves_supplier_copper_and_polarity(self):
        from tests.toolchain import require_toolchain
        require_toolchain(native=True)
        import pcbnew
        with tempfile.TemporaryDirectory() as tmp:
            library = Path(tmp) / 'AgentEE_Power.pretty'
            library.mkdir()
            (library / (variant.DIODE_FOOTPRINT + '.kicad_mod')).write_bytes(
                variant.diode_footprint_bytes((CAPTURES / 'C155367.raweasy.json').read_bytes()))
            footprint = pcbnew.FootprintLoad(str(library), variant.DIODE_FOOTPRINT)
            self.assertIsNotNone(footprint)
            pads = list(footprint.Pads())
            self.assertEqual(len(pads), 2)
            self.assertEqual({str(p.GetNumber()) for p in pads}, {'1', '2'})
            for pad in pads:
                self.assertEqual(pad.GetAttribute(), pcbnew.PAD_ATTRIB_SMD)
                self.assertEqual(pad.GetShape(), pcbnew.PAD_SHAPE_RECT)
                self.assertEqual((pad.GetDrillSize().x, pad.GetDrillSize().y), (0, 0))
                expected_x = -1634998 if str(pad.GetNumber()) == '1' else 1634998
                self.assertEqual((pad.GetPosition().x, pad.GetPosition().y), (expected_x, 0))
                self.assertEqual((pad.GetSize().x, pad.GetSize().y), (1199998, 1199998))
                self.assertEqual(pad.GetOrientationDegrees(), 0)
                self.assertEqual(set(pad.GetLayerSet().Seq()), {pcbnew.F_Cu, pcbnew.F_Paste, pcbnew.F_Mask})


SOURCE = Path(os.environ.get('AGENT_EE_ACQUISITION_INPUTS',
              Path(__file__).resolve().parents[1] / 'build/acquisition-release/inputs'))


@unittest.skipUnless((SOURCE / 'Device.kicad_sym').is_file(), 'retained acquisition libraries required')
@require_captures
class NativeAcquisitionTests(unittest.TestCase):
    def test_generated_seventeen_part_schematic_has_native_pinout_and_clean_erc(self):
        from tests.toolchain import require_toolchain
        from agent_ee import executor, schematic_emitter
        require_toolchain(native=True)
        files, report = variant.inputs((variant.DATA / 'base-circuit.json').read_bytes(),
            (CAPTURES / 'C2903961.raweasy.json').read_bytes(), (CAPTURES / 'C155367.raweasy.json').read_bytes())
        intent = json.loads(files['circuit.json'])
        captures = {lib['path']: files.get(lib['path']) or (SOURCE / lib['path']).read_bytes()
                    for lib in intent['libraries']}
        layout = json.loads((SOURCE / 'layout.json').read_bytes())
        result, emitted = schematic_emitter.emit_schematic(intent, captures, layout,
            symbol_table={lib['name']: '${KIPRJMOD}/' + lib['path'] for lib in intent['libraries']},
            footprint_table={variant.LIBRARY: '${KIPRJMOD}/' + variant.LIBRARY + '.pretty',
                'Resistor_SMD': '${KIPRJMOD}/Resistor_SMD.pretty'})
        self.assertEqual(result['execution'], 'ok', result['findings'])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = root / 'candidate'
            candidate.mkdir()
            for name, raw in {**files, **captures, **emitted}.items():
                path = candidate / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            for name in ('Resistor_SMD.pretty/R_0603_1608Metric.kicad_mod',):
                path = candidate / name
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(Path('/usr/share/kicad/footprints') / name, path)
            erc = executor.execute(candidate, root / 'erc', ['kicad-cli', 'sch', 'erc', '--format', 'json',
                '--output', '/work/erc.json', '/candidate/acquisition.kicad_sch'], timeout=30)
            self.assertEqual(erc['execution'], 'ok', erc)
            violations = [v for sheet in json.loads((root / 'erc/erc.json').read_bytes())['sheets']
                          for v in sheet['violations']]
            self.assertEqual(violations, [])
            exported = executor.execute(candidate, root / 'netlist', ['kicad-cli', 'sch', 'export', 'netlist',
                '--format', 'kicadxml', '--output', '/work/native.xml', '/candidate/acquisition.kicad_sch'], timeout=30)
            self.assertEqual(exported['execution'], 'ok', exported)
            netlist = ET.fromstring((root / 'netlist/native.xml').read_bytes())
            components = {c.attrib['ref'] for c in netlist.findall('components/comp')}
            self.assertEqual(len(components), 17)
            pins = {node.attrib['pin']: net.attrib['name'].lstrip('/')
                    for net in netlist.findall('nets/net') for node in net.findall('node')
                    if node.attrib['ref'] == 'U1' and not net.attrib['name'].startswith('unconnected-')}
            self.assertEqual(pins, {'1': 'V3V3', '2': 'V3V3', '3': 'GND', '5': 'VIN_PROTECTED',
                                    '6': 'GND', '8': 'VIN_PROTECTED', '9': 'GND'})
            references = {c['id']: c['reference'] for c in intent['components']}
            expected = {net['name']: sorted((references[t['component']], t['pin']['number'])
                for t in net['terminals'] if not references[t['component']].startswith('#')) for net in intent['nets']}
            actual = {net.attrib['name'].lstrip('/'): sorted((node.attrib['ref'], node.attrib['pin'])
                for node in net.findall('node')) for net in netlist.findall('nets/net')
                if not net.attrib['name'].startswith('unconnected-')}
            self.assertEqual(actual, expected)
            artifact = os.environ.get('AGENT_EE_REVERSE_BLOCKING_ARTIFACTS')
            if artifact:
                (root / 'development-result.json').write_text(json.dumps(dict(report,
                    native_components=len(components), native_erc_violations=len(violations), native_u1_pins=pins,
                    native_complete_connectivity_matches=True,
                    emission=result, qualification='Development schematic only; no PCB or production build'), indent=2) + '\n')
                shutil.copytree(root, Path(artifact))
