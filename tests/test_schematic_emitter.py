"""Independent expectations for flat wire-free schematic emission (AGENTEE-40).

Transformed endpoints below are hand-derived from KiCad's sheet convention
(library y up, sheet y down, rotation counter-clockwise, mirror applied on the
sheet after rotation), not computed by the emitter. Native tests judge emitted
files only through kicad-cli 10.0.7 ERC, XML netlist export and upgrade.
"""
import hashlib
import json
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from agent_ee.executor import execute
from agent_ee.schematic_emitter import emit_schematic, main, publish, transformed_pin
from agent_ee.symbol_library import children, parse_sexpr
from toolchain import require_toolchain

ROOT = Path(__file__).resolve().parents[1] / 'fixtures/generation'
PROJECT = ROOT / 'project'
MM = 1000000


def load(name):
    return json.loads((ROOT / name).read_text())


def captures(intent):
    return {lib['path']: (PROJECT / lib['path']).read_bytes() for lib in intent['libraries']}


def tables():
    return dict(symbol_table={'OwnedGen': '${KIPRJMOD}/lib/Owned_Gen.kicad_sym',
                              'OwnedPower': '${KIPRJMOD}/lib/Owned_Power.kicad_sym'},
                footprint_table={'OwnedFP': '${KIPRJMOD}/lib/OwnedFP.pretty'})


def emit(intent_name='regulator', intent=None, layout=None, **kwargs):
    intent = intent or load(intent_name + '.intent.json')
    layout = layout or load(intent_name + '.layout.json')
    return emit_schematic(intent, captures(intent), layout, **kwargs)


def stacked(edit=None):
    """Regulator whose REG3 pin 4 sits on GND pin 2's connection point, as
    stock MCU symbols stack their VSS pins; ``edit`` adjusts the intent."""
    intent = load('regulator.intent.json')
    files = captures(intent)
    nc = '(pin no_connect line\n\t\t\t\t(at 0 7.62 270)'
    data = files['lib/Owned_Gen.kicad_sym'].decode()
    assert nc in data
    files['lib/Owned_Gen.kicad_sym'] = data.replace(nc, '(pin power_in line\n\t\t\t\t(at 0 -7.62 90)', 1).encode()
    intent['libraries'][0]['sha256'] = hashlib.sha256(files['lib/Owned_Gen.kicad_sym']).hexdigest()
    if edit:
        edit(intent)
    return intent, files


def on_ground(intent):
    intent['no_connects'] = []
    next(n for n in intent['nets'] if n['id'] == 'gnd')['terminals'].append(dict(component='reg', pin=dict(number='4')))


# ASYM library pins: (number, x, y, orientation), millimetres, library y up.
ASYM = [('1', -7.62, 2.54, 0), ('2', -7.62, -2.54, 0), ('3', 5.08, 7.62, 270), ('4', 10.16, -2.54, 180)]
# Hand-derived sheet offsets and outward label angles for each transform.
EXPECTED_ENDPOINTS = {
    (0, None): {'1': (-7.62, -2.54, 180), '2': (-7.62, 2.54, 180), '3': (5.08, -7.62, 90), '4': (10.16, 2.54, 0)},
    (90, None): {'1': (-2.54, 7.62, 270), '2': (2.54, 7.62, 270), '3': (-7.62, -5.08, 180), '4': (2.54, -10.16, 90)},
    (180, None): {'1': (7.62, 2.54, 0), '2': (7.62, -2.54, 0), '3': (-5.08, 7.62, 270), '4': (-10.16, -2.54, 180)},
    (270, None): {'1': (2.54, -7.62, 90), '2': (-2.54, -7.62, 90), '3': (7.62, 5.08, 0), '4': (-2.54, 10.16, 270)},
    (0, 'x'): {'1': (-7.62, 2.54, 180), '2': (-7.62, -2.54, 180), '3': (5.08, 7.62, 270), '4': (10.16, -2.54, 0)},
    (0, 'y'): {'1': (7.62, -2.54, 0), '2': (7.62, 2.54, 0), '3': (-5.08, -7.62, 90), '4': (-10.16, 2.54, 180)},
    (90, 'x'): {'1': (-2.54, -7.62, 90), '2': (2.54, -7.62, 90), '3': (-7.62, 5.08, 180), '4': (2.54, 10.16, 270)},
    (90, 'y'): {'1': (2.54, 7.62, 270), '2': (-2.54, 7.62, 270), '3': (7.62, -5.08, 0), '4': (-2.54, -10.16, 90)},
}


def sheet_items(data):
    """Instances, labels and markers from emitted bytes via the shared reader."""
    root = parse_sexpr(data)
    symbols = {}
    for node in children(root, 'symbol'):
        ref = next(p[2] for p in children(node, 'property') if p[1] == 'Reference')
        unit = int(children(node, 'unit')[0][1])
        at = children(node, 'at')[0]
        mirror = children(node, 'mirror')
        symbols[ref, unit] = dict(uuid=children(node, 'uuid')[0][1], at=(float(at[1]), float(at[2]), int(at[3])),
                                  mirror=mirror[0][1] if mirror else None,
                                  pins={p[1]: children(p, 'uuid')[0][1] for p in children(node, 'pin')})
    labels = [(n[1], float(children(n, 'at')[0][1]), float(children(n, 'at')[0][2]), int(children(n, 'at')[0][3]))
              for n in children(root, 'label')]
    markers = [(float(children(n, 'at')[0][1]), float(children(n, 'at')[0][2])) for n in children(root, 'no_connect')]
    return root, symbols, labels, markers


class TransformTests(unittest.TestCase):
    def test_every_transform_matches_hand_derived_endpoints(self):
        for (rotation, mirror), expected in EXPECTED_ENDPOINTS.items():
            for number, x, y, orientation in ASYM:
                pin = dict(number=number, x_nm=round(x * MM), y_nm=round(y * MM), orientation=orientation)
                with self.subTest(rotation=rotation, mirror=mirror, pin=number):
                    ex, ey, angle = expected[number]
                    self.assertEqual(transformed_pin(pin, (0, 0), rotation, mirror),
                                     (round(ex * MM), round(ey * MM), angle))

    def test_equivalent_spellings_share_one_transform(self):
        pin = dict(number='1', x_nm=-7620000, y_nm=2540000, orientation=0)
        self.assertEqual(transformed_pin(pin, (0, 0), 180, 'x'), transformed_pin(pin, (0, 0), 0, 'y'))
        self.assertEqual(transformed_pin(pin, (0, 0), 270, 'x'), transformed_pin(pin, (0, 0), 90, 'y'))

    def test_emitted_labels_sit_on_independently_transformed_pins(self):
        result, files = emit('transforms')
        self.assertEqual(result['execution'], 'ok', result['findings'])
        _, symbols, labels, markers = sheet_items(files['transforms.kicad_sch'])
        layout = load('transforms.layout.json')['transforms']
        intent = load('transforms.intent.json')
        nets = {}
        for net in intent['nets']:
            for t in net['terminals']:
                numbers = [t['pin']['number']] if 'number' in t['pin'] else \
                    [n for n, *_ in ASYM if t['pin']['name'] == {'1': 'A', '2': 'B', '3': 'A', '4': 'C'}[n]]
                for n in numbers:
                    nets[t['component'], n] = net['name']
        ncs = {(t['component'], t['pin']['number']) for t in intent['no_connects']}
        expected_labels, expected_markers = [], []
        for comp in intent['components']:
            if comp['lib_id'] != 'OwnedGen:ASYM':
                continue
            instance = symbols[comp['reference'], 1]
            spec = layout.get(comp['id'], dict(rotation=0, mirror=None))
            key = (spec['rotation'], spec['mirror'])
            # Emitter may spell an equivalent transform canonically; compare geometry.
            ax, ay, _ = instance['at']
            for number, offsets in EXPECTED_ENDPOINTS[key].items():
                point = (round(ax + offsets[0], 4), round(ay + offsets[1], 4))
                if (comp['id'], number) in ncs:
                    expected_markers.append(point)
                else:
                    expected_labels.append((nets[comp['id'], number], *point, offsets[2]))
        asym_labels = [x for x in labels if x[0].startswith('T')]
        self.assertEqual(sorted((n, round(x, 4), round(y, 4), a) for n, x, y, a in asym_labels),
                         sorted(expected_labels))
        self.assertEqual(sorted((round(x, 4), round(y, 4)) for x, y in markers), sorted(expected_markers))
        for (ref, unit), instance in symbols.items():
            self.assertEqual([round(v / 2.54, 6) % 1 for v in instance['at'][:2]], [0, 0], ref)


class DeterminismTests(unittest.TestCase):
    def test_part_binding_fields_are_hidden_and_override_library_defaults(self):
        intent = load('regulator.intent.json')
        binding = {'LCSC': 'C900001', 'Manufacturer': 'Owned fixture', 'Partnumber': 'Original-1',
                   'Datasheet': 'captured/owned.pdf', 'PARAM_voltage': '{"min":"25","max":"25","unit":"V"}'}
        intent['components'][0]['part_binding'] = binding
        result, files = emit(intent=intent)
        self.assertEqual(result['execution'], 'ok', result['findings'])
        symbols = children(parse_sexpr(files['regulator.kicad_sch']), 'symbol')
        symbol = next(s for s in symbols if any(p[1:3] == ['Reference', 'J1'] for p in children(s, 'property')))
        props = {p[1]: p for p in children(symbol, 'property')}
        for key, value in binding.items():
            self.assertEqual(props[key][2], value)
        self.assertEqual(len([p for p in children(symbol, 'property') if p[1] == 'Datasheet']), 1)
        self.assertIn(['hide', 'yes'], props['LCSC'])
        self.assertEqual(files, emit(intent=intent)[1])

    def test_repeat_and_input_order_give_identical_bytes(self):
        first = emit(**tables())[1]
        self.assertEqual(first, emit(**tables())[1])
        intent = load('regulator.intent.json')
        for key in ('components', 'nets', 'libraries', 'no_connects'):
            intent[key].reverse()
        for net in intent['nets']:
            net['terminals'].reverse()
        self.assertEqual(first, emit(intent=intent, **tables())[1])

    def test_adding_one_component_preserves_existing_uuid_bindings(self):
        before = sheet_items(emit()[1]['regulator.kicad_sch'])[1]
        intent = load('regulator.intent.json')
        intent['components'].append(dict(id='c_extra', reference='C2', lib_id='OwnedGen:CAP', value='100n',
                                         footprint='OwnedFP:CAP_2PAD', units=[1]))
        vout = next(n for n in intent['nets'] if n['id'] == 'vout')
        gnd = next(n for n in intent['nets'] if n['id'] == 'gnd')
        vout['terminals'].append({'component': 'c_extra', 'pin': {'number': '1'}})
        gnd['terminals'].append({'component': 'c_extra', 'pin': {'number': '2'}})
        result, files = emit(intent=intent)
        self.assertEqual(result['execution'], 'ok', result['findings'])
        root, after, *_ = sheet_items(files['regulator.kicad_sch'])
        self.assertEqual(set(after) - set(before), {('C2', 1)})
        for key, instance in before.items():
            self.assertEqual((after[key]['uuid'], after[key]['pins']), (instance['uuid'], instance['pins']), key)
        self.assertEqual(children(root, 'uuid')[0][1],
                         children(parse_sexpr(emit()[1]['regulator.kicad_sch']), 'uuid')[0][1])

    def test_embedded_symbols_equal_captured_definitions(self):
        intent = load('regulator.intent.json')
        files = emit()[1]
        embedded = {n[1]: n for n in children(children(parse_sexpr(files['regulator.kicad_sch']), 'lib_symbols')[0],
                                              'symbol')}
        self.assertEqual(sorted(embedded), sorted({c['lib_id'] for c in intent['components']}))
        for lib in intent['libraries']:
            library = {n[1]: n for n in children(parse_sexpr((PROJECT / lib['path']).read_bytes()), 'symbol')}
            for lib_id, node in embedded.items():
                name, symbol = lib_id.split(':')
                if name == lib['name']:
                    self.assertEqual(node[2:], library[symbol][2:], lib_id)

    def test_power_symbols_and_flags_are_virtual_instances(self):
        # Eeschema's own placement (fixtures/rc/control) writes power symbols and
        # PWR_FLAG as in_bom no / on_board no although the library says yes.
        from agent_ee.stages import native_references
        files = emit()[1]
        root = parse_sexpr(files['regulator.kicad_sch'])
        flags = {}
        for node in children(root, 'symbol'):
            ref = next(p[2] for p in children(node, 'property') if p[1] == 'Reference')
            flags[ref] = {k: children(node, k)[0][1] for k in ('exclude_from_sim', 'in_bom', 'on_board', 'in_pos_files')}
        physical = {'exclude_from_sim': 'no', 'in_bom': 'yes', 'on_board': 'yes', 'in_pos_files': 'yes'}
        virtual = dict(physical, in_bom='no', on_board='no')
        self.assertEqual(flags, {'J1': physical, 'U1': physical, 'C1': physical,
                                 '#PWR01': virtual, '#FLG01': virtual, '#FLG02': virtual})
        embedded = {n[1]: n for n in children(children(root, 'lib_symbols')[0], 'symbol')}
        for lib_id in ('OwnedPower:GND', 'OwnedPower:PWR_FLAG'):
            self.assertEqual((children(embedded[lib_id], 'in_bom'), children(embedded[lib_id], 'on_board')),
                             ([['in_bom', 'yes']], [['on_board', 'yes']]), lib_id)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'regulator.kicad_sch'
            path.write_bytes(files['regulator.kicad_sch'])
            self.assertEqual(native_references(path, 'symbols'), ['C1', 'J1', 'U1'])


class RefusalTests(unittest.TestCase):
    def test_opaque_binding_cannot_override_identity_flags_or_emit_nontext_values(self):
        for binding in ({'Footprint': 'Other:Part'}, {'Reference': 'J9'}, {'dnp': 'yes'},
                        {'in_bom': 'no'}, {'LCSC': None}, {'LCSC': 123}, {'ki_description': 'hidden'}):
            intent = load('regulator.intent.json')
            intent['components'][0]['part_binding'] = binding
            with self.subTest(binding=binding):
                self.assertRefused('malformed_part_binding', intent)

    def assertRefused(self, code, intent=None, layout=None, name='regulator', **kwargs):
        result, files = emit(name, intent=intent, layout=layout, **kwargs)
        self.assertIsNone(files and sorted(files))
        self.assertNotEqual(result['execution'], 'ok')
        self.assertEqual(result['files'], [])
        self.assertIn(code, [f['code'] for f in result['findings']], result['findings'])
        return result

    def mutate(self, edit, name='regulator'):
        intent = load(name + '.intent.json')
        edit(intent)
        return intent

    def test_unknown_symbol_and_duplicate_reference(self):
        for code, edit in (
                ('resolution_unknown_symbol', lambda i: i['components'][2].update(lib_id='OwnedGen:MISSING')),
                ('resolution_duplicate_component', lambda i: i['components'][2].update(reference='J1'))):
            with self.subTest(code=code):
                self.assertEqual(self.assertRefused(code, self.mutate(edit))['execution'], 'failed')

    def test_changed_captured_library_hash(self):
        intent = load('regulator.intent.json')
        files = captures(intent)
        files['lib/Owned_Gen.kicad_sym'] += b'\n'
        result, out = emit_schematic(intent, files, load('regulator.layout.json'))
        self.assertIsNone(out and sorted(out))
        self.assertEqual(result['execution'], 'failed')
        self.assertIn('resolution_library_coverage', [f['code'] for f in result['findings']])

    def test_forged_resolved_geometry_is_not_authority(self):
        from agent_ee.connectivity_ir import resolve_intent
        intent = load('regulator.intent.json')
        forged = resolve_intent(intent, captures(intent))
        forged['components'][0]['pins'][0]['x_nm'] += 1270000
        forged['canonical_sha256'] = hashlib.sha256(json.dumps(
            {k: v for k, v in forged.items() if k != 'canonical_sha256'}, sort_keys=True, separators=(',', ':'),
            ensure_ascii=False).encode()).hexdigest()
        self.assertRefused('resolved_mismatch', expected_resolved=forged)

    def test_unlocatable_or_impossible_pins(self):
        # Rewrite captured library bytes, then re-declare the matching hash.
        def variant(old, new, code):
            intent = load('regulator.intent.json')
            files = captures(intent)
            data = files['lib/Owned_Gen.kicad_sym'].decode()
            self.assertIn(old, data)
            files['lib/Owned_Gen.kicad_sym'] = data.replace(old, new, 1).encode()
            intent['libraries'][0]['sha256'] = hashlib.sha256(files['lib/Owned_Gen.kicad_sym']).hexdigest()
            result, out = emit_schematic(intent, files, load('regulator.layout.json'))
            self.assertIsNone(out and sorted(out))
            self.assertEqual(result['execution'], 'failed')
            self.assertIn(code, [f['code'] for f in result['findings']], result['findings'])
        # VO moved onto VI's connection point: two pins would be shorted.
        vo = '(pin power_out line\n\t\t\t\t(at 7.62 0 180)'
        variant(vo, vo.replace('7.62 0', '-7.62 0'), 'impossible_pin_endpoint')
        variant(vo, vo.replace('7.62 0', '7.600001 0'), 'unlocatable_pin')
        body = '(symbol "REG3_0_1"\n'
        variant(body, body + '\t\t\t(arc\n\t\t\t\t(start -5.08 5.08)\n\t\t\t\t(mid 0 6)\n'
                '\t\t\t\t(end 5.08 5.08)\n\t\t\t)\n', 'unsupported_graphic')

    def test_stacked_pins_share_one_label_only_on_one_net(self):
        layout = load('regulator.layout.json')
        result, files = emit_schematic(*stacked(on_ground), layout)
        self.assertEqual(result['execution'], 'ok', result['findings'])
        root = parse_sexpr(files['regulator.kicad_sch'])
        reg = next(n for n in children(root, 'symbol')
                   if any(p[1] == 'Reference' and p[2] == 'U1' for p in children(n, 'property')))
        self.assertEqual([p[1] for p in children(reg, 'pin')], ['1', '2', '3', '4'])
        x, y = (float(v) for v in children(reg, 'at')[0][1:3])
        stack = (x, round(y + 7.62, 4))  # Sheet y runs down from the library's (0, -7.62).
        at = [(n[1], float(children(n, 'at')[0][1]), float(children(n, 'at')[0][2]))
              for n in children(root, 'global_label')]
        self.assertEqual([a for a in at if a[1:] == stack], [('GND', *stack)])
        self.assertEqual(len(set(at)), len(at))
        self.assertEqual(children(root, 'no_connect'), [])
        # A stack on two nets, or a no-connect at a stack, would change connectivity.
        refusals = (
            lambda i: None,
            lambda i: (on_ground(i), i['nets'][1]['terminals'].pop(), i['nets'][2]['terminals'].append(
                dict(component='reg', pin=dict(number='4')))),
            lambda i: (i['nets'][1]['terminals'].pop(1), i['no_connects'].append(
                dict(component='reg', pin=dict(name='GND')))),
        )
        for edit in refusals:
            result, out = emit_schematic(*stacked(edit), layout)
            self.assertIsNone(out and sorted(out))
            self.assertEqual(result['execution'], 'failed')
            self.assertEqual([(f['code'], f['location']) for f in result['findings']],
                             [('impossible_pin_endpoint', '/components/reg/pins/4')])

    def test_malformed_names_and_escaping(self):
        cases = [
            ('malformed_net_name', lambda i: i['nets'][2].update(name='VOUT[0..3]')),
            ('malformed_net_name', lambda i: i['nets'][2].update(name='${VOUT}')),
            ('malformed_net_name', lambda i: i['nets'][2].update(name='')),
            ('malformed_value', lambda i: i['components'][2].update(value='10u\\"')),
            ('malformed_value', lambda i: i['components'][2].update(value='${X}')),
            ('malformed_value', lambda i: i['components'][2].update(value='a\nb')),
            ('malformed_reference', lambda i: i['components'][2].update(reference='C 1')),
            ('malformed_reference', lambda i: i['components'][2].update(reference='#C1')),
            ('malformed_footprint', lambda i: i['components'][2].update(footprint='Lib:Name"x')),
            ('duplicate_net_name', lambda i: i['nets'][2].update(name='VIN')),
            ('power_net_mismatch', lambda i: i['nets'][1].update(name='AGND')),
        ]
        for code, edit in cases:
            with self.subTest(code=code):
                self.assertRefused(code, self.mutate(edit))

    def test_hierarchy_is_refused_not_flattened(self):
        result = self.assertRefused('resolution_hierarchy_unsupported', self.mutate(
            lambda i: i.update(hierarchy=[{'sheet': 'child'}])))
        self.assertEqual(result['execution'], 'unsupported')

    def test_derived_symbols_are_refused_until_qualified(self):
        intent = load('regulator.intent.json')
        files = captures(intent)
        data = files['lib/Owned_Gen.kicad_sym'].decode().rstrip()[:-1]
        data += ('\t(symbol "CAP_DERIVED"\n\t\t(extends "CAP")\n\t\t(property "Reference" "C"\n\t\t\t(at 0 0 0)\n\t\t)\n'
                 '\t\t(property "Value" "CAP_DERIVED"\n\t\t\t(at 0 0 0)\n\t\t)\n\t)\n)\n')
        files['lib/Owned_Gen.kicad_sym'] = data.encode()
        intent['libraries'][0]['sha256'] = hashlib.sha256(data.encode()).hexdigest()
        intent['components'][2]['lib_id'] = 'OwnedGen:CAP_DERIVED'
        result, out = emit_schematic(intent, files, load('regulator.layout.json'))
        self.assertIsNone(out and sorted(out))
        self.assertIn('derived_symbol_unsupported', [f['code'] for f in result['findings']])

    def test_layout_contract(self):
        for edit in (lambda l: l.update(namespace='not-a-uuid'), lambda l: l.update(project='../x'),
                     lambda l: l.update(transforms={'missing': {'rotation': 0, 'mirror': None}}),
                     lambda l: l.update(transforms={'reg': {'rotation': 45, 'mirror': None}}),
                     lambda l: l.update(extra=1)):
            layout = load('regulator.layout.json')
            edit(layout)
            self.assertRefused('layout_schema', layout=layout)

    def test_cli_refusal_publishes_nothing_and_success_is_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            intent = load('regulator.intent.json')
            intent['components'][2]['lib_id'] = 'OwnedGen:MISSING'
            (tmp / 'bad.json').write_text(json.dumps(intent))
            argv = ['--layout', str(ROOT / 'regulator.layout.json'), '--captures', str(PROJECT)]
            self.assertEqual(main([str(tmp / 'bad.json'), *argv, '--out', str(tmp / 'bad'),
                                   '--receipt', str(tmp / 'bad-receipt.json')]), 1)
            self.assertFalse((tmp / 'bad').exists())
            self.assertEqual(sorted(p.name for p in tmp.iterdir()), ['bad-receipt.json', 'bad.json'])
            self.assertEqual(main([str(ROOT / 'regulator.intent.json'), *argv, '--out', str(tmp / 'good'),
                                   '--receipt', str(tmp / 'receipt.json')]), 0)
            receipt = json.loads((tmp / 'receipt.json').read_text())
            self.assertEqual(sorted(p.name for p in (tmp / 'good').iterdir()),
                             sorted(f['path'] for f in receipt['files']))
            for item in receipt['files']:
                self.assertEqual(hashlib.sha256((tmp / 'good' / item['path']).read_bytes()).hexdigest(),
                                 item['sha256'])
            with self.assertRaises(FileExistsError):
                publish({'x.kicad_sch': b''}, tmp / 'good')
            self.assertEqual(sorted(p.name for p in (tmp / 'good').iterdir()),
                             sorted(f['path'] for f in receipt['files']))


def native_project(files, tmp):
    project = Path(tmp) / 'project'
    shutil.copytree(PROJECT / 'lib', project / 'lib')
    for name, data in files.items():
        (project / name).write_bytes(data)
    return project


def run_native(project, work, *argv):
    """kicad-cli inside the production Bubblewrap executor, network unshared."""
    config = Path(work) / 'kicad-config' / '10.0'
    config.mkdir(parents=True, exist_ok=True)
    (config / 'sym-lib-table').write_text('(sym_lib_table\n  (version 7)\n)\n')
    (config / 'fp-lib-table').write_text('(fp_lib_table\n  (version 7)\n)\n')
    record = execute(project, work, list(argv), timeout=300)
    if record['execution'] != 'ok':
        raise AssertionError(f'{argv} {record["execution"]}: ' + (Path(work) / 'console.log').read_text())
    return record


def native_netlist(path):
    """Independent minimal reader of the native XML: nets, singletons, components."""
    root = ET.parse(path).getroot()
    components = {c.get('ref'): dict(uuid=c.findtext('tstamps').strip(), sheet=c.find('sheetpath').get('tstamps'),
                                     lib=(c.find('libsource').get('lib'), c.find('libsource').get('part')),
                                     value=c.findtext('value'), footprint=c.findtext('footprint') or '')
                  for c in root.iter('comp')}
    nets, singles = {}, {}
    for net in root.iter('net'):
        nodes = sorted([n.get('ref'), n.get('pin')] for n in net.findall('node'))
        if len(nodes) > 1:
            nets[net.get('name')] = nodes
        else:
            singles[net.get('name')] = (nodes[0], net.find('node').get('pintype'))
    return components, nets, singles


class NativeEmissionTests(unittest.TestCase):
    def test_bindings_survive_real_native_export_and_forced_resave(self):
        intent = load('regulator.intent.json')
        binding = {'LCSC': 'C900001', 'Manufacturer': 'Owned fixture', 'Partnumber': 'Original-1',
                   'Datasheet': 'captured/owned.pdf'}
        intent['components'][0]['part_binding'] = binding
        result, files = emit(intent=intent, **tables())
        self.assertEqual(result['execution'], 'ok', result['findings'])
        project, work, erc, graph = self.native_run('regulator', files, 'binding')
        native = ET.parse(work / 'netlist/design.xml').getroot()
        comp = native.find('./components/comp[@ref="J1"]')
        fields = {n.get('name'): n.text for n in comp.findall('./fields/field')}
        for name, value in binding.items():
            if name == 'Datasheet':
                self.assertEqual(comp.findtext('datasheet'), value)
            else:
                self.assertEqual(fields[name], value)
        self.assertNativeResave(project, work, 'regulator', files)

    def setUp(self):
        require_toolchain(native=True)
        self.tmp = tempfile.mkdtemp(prefix='agent-ee-emitter-native-')
        self.addCleanup(shutil.rmtree, self.tmp)

    def native_run(self, name, files, label):
        project = native_project(files, Path(self.tmp) / label)
        work = Path(self.tmp) / label / 'work'
        sch = f'/candidate/{name}.kicad_sch'
        run_native(project, work / 'erc', 'kicad-cli', 'sch', 'erc', '--format', 'json', '--severity-all',
                   '--units', 'mm', '-o', '/work/erc.json', sch)
        run_native(project, work / 'netlist', 'kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml',
                   '-o', '/work/design.xml', sch)
        erc = json.loads((work / 'erc' / 'erc.json').read_text())
        self.assertEqual(erc['kicad_version'], '10.0.7')
        return project, work, erc, native_netlist(work / 'netlist' / 'design.xml')

    def test_regulator_erc_netlist_and_current_format(self):
        result, files = emit(**tables())
        self.assertEqual(result['execution'], 'ok', result['findings'])
        project, work, erc, (components, nets, singles) = self.native_run('regulator', files, 'regulator')
        violations = [v for sheet in erc['sheets'] for v in sheet['violations']]
        self.assertEqual(len(erc['sheets']), 1)
        self.assertEqual(violations, [])
        expected = load('regulator.expected.json')
        self.assertEqual({ref: c['lib'] for ref, c in components.items()},
                         {'J1': ('OwnedGen', 'CONN3'), 'U1': ('OwnedGen', 'REG3'), 'C1': ('OwnedGen', 'CAP')})
        self.assertEqual(nets, expected['nets'])
        self.assertEqual([[n, p] for (n, p), _ in singles.values()], expected['no_connects'])
        self.assertTrue(all('no_connect' in kind for _, kind in singles.values()))
        intent = load('regulator.intent.json')
        for comp in intent['components']:
            if comp['reference'] in components:
                self.assertEqual((components[comp['reference']]['value'], components[comp['reference']]['footprint']),
                                 (comp['value'], comp['footprint']))
        # A current-format file is left untouched by the native upgrader.
        copy_dir = work / 'upgrade' / 'copy'
        shutil.copytree(project, copy_dir)
        run_native(project, work / 'upgrade', 'kicad-cli', 'sch', 'upgrade', '/work/copy/regulator.kicad_sch')
        self.assertIn('Schematic file was not updated', (work / 'upgrade' / 'console.log').read_text())
        self.assertEqual((copy_dir / 'regulator.kicad_sch').read_bytes(), files['regulator.kicad_sch'])
        self.assertNativeResave(project, work, 'regulator', files)

    def assertNativeResave(self, project, work, name, files):
        """KiCad's own forced re-save reproduces the bytes, apart from the generator."""
        shutil.copytree(project, work / 'resave' / 'copy')
        run_native(project, work / 'resave', 'kicad-cli', 'sch', 'upgrade', '--force', f'/work/copy/{name}.kicad_sch')
        native = (work / 'resave' / 'copy' / f'{name}.kicad_sch').read_bytes()
        self.assertNotEqual(native, files[f'{name}.kicad_sch'])
        self.assertEqual(native.replace(b'(generator "eeschema")', b'(generator "agent-ee-schematic-emitter")', 1),
                         files[f'{name}.kicad_sch'])

    def test_all_transforms_multiunit_and_repeated_names_connect_natively(self):
        result, files = emit('transforms', symbol_table={'OwnedGen': '${KIPRJMOD}/lib/Owned_Gen.kicad_sym'})
        self.assertEqual(result['execution'], 'ok', result['findings'])
        project, work, erc, (components, nets, singles) = self.native_run('transforms', files, 'transforms')
        self.assertNativeResave(project, work, 'transforms', files)
        expected = {}
        for k in range(1, 9):
            for n in '1234':
                if (k, n) == (7, '4') or (k == 8 and n in '13'):
                    continue
                expected[f'/T{k}_{n}'] = [[f'R{k}', n], [f'X{k}', n]]
        expected['/T8_A'] = [['R8', '1'], ['R8', '3'], ['X8', '1'], ['X8', '3']]
        expected['/D_A'] = [['U1', '1'], ['U1', '4']]
        expected['/D_B'] = [['U1', '2'], ['U1', '3']]
        expected['/D_P'] = [['U1', '5'], ['U1', '6']]
        self.assertEqual(nets, expected)
        self.assertEqual(sorted(ref for (ref, pin), _ in singles.values()), ['R7', 'X7'])
        self.assertEqual(len(components['U1']['uuid'].split()), 2)
        errors = [v for s in erc['sheets'] for v in s['violations'] if v['severity'] == 'error']
        self.assertEqual(errors, [])

    def test_stacked_same_net_pins_connect_natively(self):
        intent, captured = stacked(on_ground)
        result, files = emit_schematic(intent, captured, load('regulator.layout.json'), **tables())
        self.assertEqual(result['execution'], 'ok', result['findings'])
        # The project library is the stacked capture, so ERC also compares the embedded copy with it.
        _, _, erc, (_, nets, singles) = self.native_run('regulator', {**files, **captured}, 'stacked')
        self.assertEqual([v for sheet in erc['sheets'] for v in sheet['violations']], [])
        expected = load('regulator.expected.json')['nets']
        expected['GND'] = sorted(expected['GND'] + [['U1', '4']])
        self.assertEqual(nets, expected)
        self.assertEqual(singles, {})

    def test_native_oracle_detects_swapped_and_shorted_terminals(self):
        intent = load('regulator.intent.json')
        nets = {n['id']: n for n in intent['nets']}
        # Swap: the cap's terminals change nets; short: VOUT merges into VIN.
        nets['vout']['terminals'][2]['pin']['number'] = '2'
        nets['gnd']['terminals'][2]['pin']['number'] = '1'
        result, files = emit(intent=intent, **tables())
        self.assertEqual(result['execution'], 'ok', result['findings'])
        _, _, _, (_, swapped, _) = self.native_run('regulator', files, 'swap')
        self.assertNotEqual(swapped, load('regulator.expected.json')['nets'])
        intent = load('regulator.intent.json')
        vin, vout = intent['nets'][0], intent['nets'][2]
        vin['terminals'] += vout['terminals']
        intent['nets'].remove(vout)
        result, files = emit(intent=intent, **tables())
        self.assertEqual(result['execution'], 'ok', result['findings'])
        _, _, _, (_, shorted, _) = self.native_run('regulator', files, 'short')
        self.assertEqual(shorted['/VIN'], [['C1', '1'], ['J1', '1'], ['J1', '3'], ['U1', '1'], ['U1', '3']])


if __name__ == '__main__':
    unittest.main()
