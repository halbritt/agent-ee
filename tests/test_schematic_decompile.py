"""Owned native fixtures and independently specified terminal memberships."""
import hashlib
import unittest
from pathlib import Path

from agent_ee.schematic_decompile import decompile

FIXTURE = Path(__file__).parent / 'fixtures' / 'schematic-decompile'


class DecompileTests(unittest.TestCase):
    def test_original_imu_intent_and_spanning_edges(self):
        source = (FIXTURE / 'imu.kicad_sch').read_bytes()
        lib = (FIXTURE / 'Owned.kicad_sym').read_bytes()
        result = decompile(source, [{'name': 'Owned', 'path': 'Owned.kicad_sym',
            'sha256': hashlib.sha256(lib).hexdigest()}], {'Owned.kicad_sym': lib})
        self.assertEqual(result['report']['execution'], 'ok', result)
        self.assertEqual(result['resolved']['outcome'], 'pass', result)
        self.assertEqual(sorted(len(n['terminals']) for n in result['intent']['nets']), [2, 2, 3, 3])
        sensor = next(c['id'] for c in result['intent']['components'] if c['reference'] == 'U1')
        self.assertEqual([(p['pin'],p['position_nm']) for p in result['report']['source_pins']
            if p['component'] == sensor], [('1',[20000000,20000000]), ('2',[18000000,22000000]),
                                         ('3',[21000000,24000000]), ('4',[23000000,26000000])])
        for net in result['report']['nets']:
            self.assertEqual(len(net['connect_pins']), net['physical_terminal_count'] - 1)
        self.assertEqual(result, decompile(source, result['intent']['libraries'], {'Owned.kicad_sym': lib}))


class DecompileCase(unittest.TestCase):
    def extract(self, source):
        from schematic_decompile_fixture import MANIFEST, CAPTURES
        return decompile(source, MANIFEST, CAPTURES)

    def memberships(self, result):
        self.assertEqual(result['report']['execution'], 'ok', result)
        refs = {c['id']: c['reference'] for c in result['intent']['components']}
        return {frozenset((refs[t['component']], t['pin']['number']) for t in n['terminals'])
                for n in result['intent']['nets']}



class GraphTests(DecompileCase):
    def test_colocated_terminals_and_remote_global_label(self):
        from schematic_decompile_fixture import control
        source = control([(10, 10), (10, 10), (30, 30)],
                         labels=[('Exact', (10, 10)), ('Exact', (30, 30))])
        result = self.extract(source)
        self.assertEqual(self.memberships(result), {frozenset((('U1','1'), ('U2','1'), ('U3','1')))})
        edges = result['report']['nets'][0]['connect_pins']
        self.assertEqual(len(edges), 2)
        self.assertEqual(len({e['target']['component'] for e in edges}), 2)

    def test_crossing_and_junction(self):
        from schematic_decompile_fixture import control
        points = [(10, 20), (30, 20), (20, 10), (20, 30)]
        wires = [(points[0], points[1]), (points[2], points[3])]
        for junctions, sizes in [([], [2, 2]), ([(20, 20)], [4])]:
            with self.subTest(junctions=junctions):
                result = self.extract(control(points, wires, junctions=junctions))
                self.assertEqual(sorted(map(len, self.memberships(result))), sizes)

    def test_one_nanometre_gap_never_snaps(self):
        from schematic_decompile_fixture import control
        result = self.extract(control([(10, 10), ('10.000001', 10)]))
        self.assertIsNone(result['intent'])
        self.assertEqual(result['report']['execution'], 'unsupported')

    def test_refuses_each_electrical_construct_without_partial_ir(self):
        source = (FIXTURE / 'imu.kicad_sch').read_bytes()
        for construct in (b'(sheet)', b'(label "local" (at 20 20 0))', b'(hierarchical_label "h")',
                          b'(bus)', b'(bus_entry)', b'(bus_alias "b")', b'(no_connect)',
                          b'(future_electrical_node)'):
            with self.subTest(construct=construct):
                result = self.extract(source.rstrip()[:-1] + construct + b')')
                self.assertEqual(result['report']['execution'], 'unsupported')
                self.assertIsNone(result['intent'])
                self.assertIsNone(result['resolved'])

    def test_library_and_embedded_geometry_are_both_authoritative(self):
        from schematic_decompile_fixture import MANIFEST, LIB
        source = (FIXTURE / 'imu.kicad_sch').read_bytes()
        for source_bytes, captures in [(source, {}), (source, {'Owned.kicad_sym': LIB + b' '}),
            (source.replace(b'(at 3 -6 0)', b'(at 3 -7 0)'), {'Owned.kicad_sym': LIB})]:
            result = decompile(source_bytes, MANIFEST, captures)
            self.assertEqual(result['report']['execution'], 'unsupported', result)
            self.assertIsNone(result['intent'])


class NativeDecompileTests(DecompileCase):
    @classmethod
    def setUpClass(cls):
        from toolchain import require_toolchain
        require_toolchain(native=True)

    def native(self, source):
        import subprocess
        import tempfile
        from agent_ee.native_netlist import read_native_inventory
        with tempfile.TemporaryDirectory(prefix='agent-ee-42-native-') as directory:
            path = Path(directory) / 'control.kicad_sch'
            path.write_bytes(source)
            output = Path(directory) / 'native.xml'
            run = subprocess.run(['kicad-cli', 'sch', 'export', 'netlist', '--format',
                'kicadxml', '-o', str(output), str(path)], capture_output=True, timeout=30)
            self.assertEqual(run.returncode, 0, (run.stdout, run.stderr))
            self.assertTrue(output.is_file())
            return read_native_inventory(output)

    def assert_native(self, source, expected):
        from agent_ee.netlist_parity import compare, resolved_inventory
        actual = self.native(source)
        native_sets = {frozenset(net['terminals']) for net in actual['nets'].values()}
        self.assertEqual(native_sets, {frozenset(net) for net in expected})
        result = self.extract(source)
        self.assertEqual(self.memberships(result), native_sets)
        report = compare(resolved_inventory(result['resolved']), actual, dict(
            predicate='instance', multiset='exactly', anchors=result['report']['source_anchors'],
            instance_components=[c['id'] for c in result['intent']['components']]))
        self.assertEqual((report['execution'], report['outcome']), ('ok', 'pass'), report)
        self.assertEqual(report['scores']['jaccard'], 1.0)
        self.assertTrue(report['correspondence_evaluated'])
        return result

    def test_native_encoded_slash_collision(self):
        from schematic_decompile_fixture import control
        points = [(10,10), (20,10), (30,10), (40,10)]
        labels = [('A/B', points[0]), ('A/B', points[1]),
                  ('A{slash}B', points[2]), ('A{slash}B', points[3])]
        source = control(points, labels=labels)
        result = self.assert_native(source, [[('U1','1'), ('U2','1'), ('U3','1'), ('U4','1')]])
        self.assertEqual(result['intent']['nets'][0]['name'], 'A/B')

    def test_native_aliases_on_same_conductor(self):
        from schematic_decompile_fixture import control
        source = control([(10,10), (20,10)], wires=[((10,10),(20,10))],
                         labels=[('A/B',(10,10)), ('A{slash}B',(20,10))])
        result = self.assert_native(source, [[('U1','1'), ('U2','1')]])
        self.assertEqual(result['intent']['nets'][0]['name'], 'A/B')
        conflict = control([(10,10), (20,10)], wires=[((10,10),(20,10))],
                           labels=[('A/B',(10,10)), ('A_B',(20,10))])
        refused = self.extract(conflict)
        self.assertEqual(refused['report']['execution'], 'unsupported')
        self.assertIsNone(refused['intent'])
        self.assertIsNone(refused['resolved'])

    def test_native_qualified_label_names_and_distinct_controls(self):
        from schematic_decompile_fixture import control
        points = [(10,10), (20,10), (30,10), (40,10)]
        joined = [[('U1','1'), ('U2','1'), ('U3','1'), ('U4','1')]]
        separate = [[('U1','1'), ('U2','1')], [('U3','1'), ('U4','1')]]
        cases = [
            ('A{slash}B', 'A{slash}B', ['A/B']),
            ('A'+chr(92)+'B', 'A{backslash}B', ['A'+chr(92)+'B']),
            ('A:B', 'A{colon}B', ['A:B']),
            ('A B', 'A{space}B', ['A B']),
            ('A{backslash}{slash}{colon}{space}{quote}µ',
             'A'+chr(92)+"/: 'µ", ['A'+chr(92)+"/: 'µ"]),
            ("A'B", 'A{quote}B', ["A'B"]),
            ('电源/µ', '电源{slash}µ', ['电源/µ']),
            ('A B', 'A_B', ['A B', 'A_B']),
            ('A', 'a', ['A', 'a']),
            ('~{RESET}', 'RESET', ['RESET', '~{RESET}']),
            ('A[0..1]', 'A0', ['A0', 'A[0..1]']),
            ('é', 'e\u0301', ['e\u0301', 'é']),
            ('A"B', "A'B", ['A"B', "A'B"])]
        for a, b, names in cases:
            with self.subTest(a=a, b=b):
                source = control(points, labels=[(a, points[0]), (a, points[1]),
                                                (b, points[2]), (b, points[3])])
                result = self.assert_native(source, joined if len(names) == 1 else separate)
                self.assertEqual(sorted(n['name'] for n in self.native(source)['nets'].values()), names)
                self.assertEqual(sorted(n['name'] for n in result['intent']['nets']), names)
                self.assertEqual(sorted(l['name'] for n in result['report']['nets']
                                        for l in n['global_labels']), sorted([a,a,b,b]))
                self.assertEqual(result, self.extract(source))

    def test_native_unqualified_braces_and_dynamic_labels_refuse_whole_source(self):
        from schematic_decompile_fixture import control
        # Native literal/encoded collisions are observed, never inferred from
        # two consumers of the shared resolver. This grammar refuses them all.
        cases = [('A{B', 'A{brace}B', ['A{B']),
                 ('A{slash}B', 'A{brace}slash}B', ['A/B']),
                 ('A{unknown}B', 'A{unknown}B', ['A{unknown}B']),
                 ('A}B', 'A{rbrace}B', ['A{rbrace}B', 'A}B']),
                 ('~{slash}', '~/', ['~{slash}']),
                 ('~{backslash}', '~'+chr(92), ['~'+chr(92), '~{backslash}'])]
        points = [(10,10), (20,10), (30,10), (40,10)]
        for a, b, names in cases:
            with self.subTest(a=a, b=b):
                source = control(points, labels=[(a,points[0]), (a,points[1]),
                                                (b,points[2]), (b,points[3])])
                native = self.native(source)
                self.assertEqual(sorted(n['name'] for n in native['nets'].values()), names)
                self.assertEqual(sorted(len(n['terminals']) for n in native['nets'].values()),
                                 [4] if len(names) == 1 else [2,2])
                result = self.extract(source)
                self.assertEqual(result['report']['execution'], 'unsupported', result)
                self.assertIsNone(result['intent'])
                self.assertIsNone(result['resolved'])
                self.assertEqual(result['report']['nets'], [])

    def test_original_native_matches_decompiled_imu(self):
        self.assert_native((FIXTURE / 'imu.kicad_sch').read_bytes(), [
            [('U1','1'), ('J1','1'), ('C1','1')],
            [('U1','2'), ('J1','2'), ('C1','2')],
            [('U1','3'), ('J1','3')], [('U1','4'), ('J1','4')]])

    def test_native_crossing_endpoint_and_overlap_controls(self):
        from schematic_decompile_fixture import control
        points = [(10,20), (30,20), (20,10), (20,30)]
        cross = [(points[0], points[1]), (points[2], points[3])]
        separate = [[('U1','1'), ('U2','1')], [('U3','1'), ('U4','1')]]
        joined = [[('U1','1'), ('U2','1'), ('U3','1'), ('U4','1')]]
        self.assert_native(control(points, cross), separate)
        self.assert_native(control(points, cross, junctions=[(20,20)]), joined)
        self.assert_native(control(points, cross + [cross[0]]), separate)
        self.assert_native(control([(10,10),(30,10),(20,10),(40,10)],
            [((10,10),(30,10)),((20,10),(40,10))]), separate)
        self.assert_native(control(points,
            [((10,20),(20,20)),((20,20),(30,20)), cross[1]]), separate)
        # Native drops pinless conductors and unrelated junctions; extraction
        # refuses them because intent/1 cannot account for them faithfully.
        for source in [control(points, cross, junctions=[(100,100)]),
                       control(points, cross + [((20,20),(25,25))]),
                       control(points, cross + [((15,20),(25,20))])]:
            self.assertEqual({frozenset(n['terminals']) for n in self.native(source)['nets'].values()},
                             {frozenset(n) for n in separate})
            self.assertIsNone(self.extract(source)['intent'])

    def test_native_interior_pin_label_and_zero_length(self):
        from schematic_decompile_fixture import control
        source = control([(10,10),(30,10),(20,10)], [((10,10),(30,10))])
        native_sets = {frozenset(n['terminals']) for n in self.native(source)['nets'].values()}
        self.assertEqual(native_sets, {frozenset([('U1','1'),('U2','1')]), frozenset([('U3','1')])})
        self.assertIsNone(self.extract(source)['intent'])
        self.assert_native(control([(10,10),(30,10),(20,10)], [((10,10),(30,10))],
            junctions=[(20,10)]), [[('U1','1'),('U2','1'),('U3','1')]])
        self.assert_native(control([(10,10),(30,10),(50,10)], [((10,10),(30,10))],
            labels=[('G', (20,10)), ('G', (50,10))]), [[('U1','1'), ('U2','1'), ('U3','1')]])
        self.assert_native(control([(10,10),(10,10)], [((10,10),(10,10))]), [[('U1','1'), ('U2','1')]])

    def test_native_diagonal_and_colocated_controls(self):
        from schematic_decompile_fixture import control
        self.assert_native(control([(10,10),(30,30),(20,20)], [((10,10),(30,30))], junctions=[(20,20)]),
                           [[('U1','1'), ('U2','1'), ('U3','1')]])
        self.assert_native(control([(10,10),(10,10)]), [[('U1','1'), ('U2','1')]])

    def test_native_disconnected_and_unsplit_endpoint_controls(self):
        from schematic_decompile_fixture import control
        cases = [
            (control([(10,10),('10.0001',10)]), [[('U1','1')],[('U2','1')]]),
            (control([(10,20),(30,20),(20,10)],
                     [((10,20),(30,20)),((20,10),(20,20))]),
             [[('U1','1'),('U2','1')],[('U3','1')]]),
            (control([(10,10),(30,30),(20,20)], [((10,10),(30,30))]),
             [[('U1','1'),('U2','1')],[('U3','1')]])]
        for source, expected in cases:
            self.assertEqual({frozenset(n['terminals']) for n in self.native(source)['nets'].values()},
                             {frozenset(n) for n in expected})
            result = self.extract(source)
            self.assertEqual(result['report']['execution'], 'unsupported', result)
            self.assertIsNone(result['intent'])
        self.assert_native(control([(10,20),(30,20),(20,10)],
            [((10,20),(30,20)),((20,10),(20,20))], junctions=[(20,20)]),
            [[('U1','1'),('U2','1'),('U3','1')]])

    def test_native_rotations_and_mirrors_asymmetric_pins(self):
        from schematic_decompile_fixture import control
        # Asymmetric source pin offsets, independently worked out for each angle.
        offsets = {0: [(0,0),(-2,2),(1,4),(3,6)], 90: [(0,0),(2,2),(4,-1),(6,-3)],
                   180: [(0,0),(2,-2),(-1,-4),(-3,-6)], 270: [(0,0),(-2,-2),(-4,1),(-6,3)]}
        for rotation in (0,90,180,270):
            for mirror in ('', 'x', 'y'):
                # KiCad mirror axes are sheet coordinates, after rotation.
                sx = -1 if mirror == 'y' else 1
                sy = -1 if mirror == 'x' else 1
                labels = [(f'N{n}', (20+sx*x,20+sy*y))
                          for n,(x,y) in enumerate(offsets[rotation],1)]
                labels += [(f'N{n}', (60+x,20+y)) for n,(x,y) in enumerate(offsets[0],1)]
                with self.subTest(rotation=rotation, mirror=mirror):
                    self.assert_native(control([(20,20),(60,20)], labels=labels, symbol='Sensor',
                        rotations={0:rotation}, mirrors={0:mirror} if mirror else {}),
                        [[('U1',str(n)),('U2',str(n))] for n in range(1,5)])


    def test_native_mutations_fail_original_parity(self):
        from agent_ee.netlist_parity import compare, resolved_inventory
        from agent_ee.symbol_library import parse_sexpr, children
        from schematic_decompile_fixture import serialize, control
        original = (FIXTURE / 'imu.kicad_sch').read_bytes()
        result = self.extract(original)
        wanted = resolved_inventory(result['resolved'])
        tree = parse_sexpr(original)
        sensor = children(children(tree, 'lib_symbols')[0], 'symbol')[0]
        pins = children(children(sensor, 'symbol')[0], 'pin')
        children(pins[2], 'number')[0][1] = '4'
        children(pins[3], 'number')[0][1] = '3'
        swapped = serialize(tree).encode()
        changed_label = original.replace(b'(global_label "SDA"', b'(global_label "WRONG"', 1)
        extra_tree = parse_sexpr(original)
        extra = parse_sexpr(control([(100,100)]))
        instance = children(extra, 'symbol')[0]
        children(instance, 'property')[0][2] = 'U99'
        path = children(children(children(instance, 'instances')[0], 'project')[0], 'path')[0]
        path[1] = '/' + children(tree, 'uuid')[0][1]
        children(path, 'reference')[0][1] = 'U99'
        extra_tree.append(instance)
        for source in (swapped, changed_label, serialize(extra_tree).encode()):
            actual = self.native(source)
            report = compare(wanted, actual, dict(predicate='instance', multiset='exactly',
                anchors=result['report']['source_anchors'], instance_components=list(wanted['components'])))
            self.assertEqual((report['execution'], report['outcome']), ('ok', 'defect'), report)
        points = [(10,20),(30,20),(20,10),(20,30)]
        wires = [(points[0],points[1]),(points[2],points[3])]
        result = self.extract(control(points, wires, junctions=[(20,20)]))
        for source in (control(points, wires), control(points, wires[:1], junctions=[(20,20)])):
            report = compare(resolved_inventory(result['resolved']), self.native(source), dict(
                predicate='instance', multiset='exactly', anchors=result['report']['source_anchors'],
                instance_components=[c['id'] for c in result['intent']['components']]))
            self.assertEqual((report['execution'], report['outcome']), ('ok', 'defect'), report)


class BoundaryTests(DecompileCase):
    def test_metadata_and_numeric_refusals_are_all_or_nothing(self):
        from schematic_decompile_fixture import control
        source = (FIXTURE / 'imu.kicad_sch').read_bytes()
        cases = [source.replace(b'(dnp no)', b'(dnp yes)', 1),
                 source.replace(b'(on_board yes)', b'(on_board no)'),
                 source.replace(b'(unit 1)', b'(unit 2)', 1),
                 source.replace(b'(at 20 20 0)', b'(at 20 20 45)', 1),
                 source.replace(b'(at 20 20 0)', b'(at NaN 20 0)', 1),
                 source.replace(b'(at 20 20 0)', b'(at 20.0000001 20 0)', 1),
                 source.replace(b'(property "Reference" "U1"', b'(property "Reference" "J1"', 1),
                 source.replace(b'(property "Value" "Invented-IMU"', b'(property "Value" "Invented-IMU") (property "Value" "other"', 1),
                 source.replace(b'(pin passive line', b'(pin passive inverted', 1),
                 source.replace(b'(pin passive line', b'(pin passive line hide', 1),
                 source.replace(b'(pin_names (offset 0))', b'(power) (pin_names (offset 0))', 1),
                 source.replace(b'(symbol "Sensor_1_1"', b'(symbol "Sensor_2_1"', 1),
                 source.replace(b'(symbol "Sensor_1_1"', b'(symbol "Sensor_1_2"', 1),
                 control([(10,10),('10.000001',10)], [((10,10),('10.000001',10))]),
                 b'(' * 130 + b')' * 130, b'\xff', source + b'garbage']
        for data in cases:
            with self.subTest(data=data[:100]):
                result = self.extract(data)
                self.assertEqual(result['report']['execution'], 'unsupported', result)
                self.assertIsNone(result['intent'])
                self.assertIsNone(result['resolved'])
                self.assertTrue(result['report']['findings'])

    def test_case_sensitive_labels_conflicts_and_label_only_conductors(self):
        from schematic_decompile_fixture import control
        for labels in [[('A',(10,10)), ('a',(20,10))],
                       [('A',(10,10)), ('B',(10,10))],
                       [('A',(90,90))]]:
            source = control([(10,10),(20,10)], labels=labels)
            self.assertIsNone(self.extract(source)['intent'])

    def test_label_encoding_refusal_is_whole_source(self):
        from schematic_decompile_fixture import control
        points = [(10,10), (20,10), (30,10), (40,10)]
        for name in ('${VALUE}', '{brace}', '{slash', 'A}', '~{A{slash}B}',
                     '{unknown}', 'A'+chr(9)+'B', '{tab}', '{backslash}${VALUE}',
                     '~{slash}', '~/', '~{RESET}suffix'):
            with self.subTest(name=name):
                result = self.extract(control(points, labels=[('Good', points[0]),
                    ('Good', points[1]), (name, points[2]), (name, points[3])]))
                self.assertEqual(result['report']['execution'], 'unsupported', result)
                self.assertIsNone(result['intent'])
                self.assertIsNone(result['resolved'])
                self.assertEqual(result['report']['nets'], [])

    def test_graph_resource_limits_refuse_before_usable_output(self):
        from schematic_decompile_fixture import control
        from unittest.mock import patch
        source = control([(10,10),(20,10)], [((10,10),(20,10))])
        for limit in ('MAX_SYMBOLS', 'MAX_PINS', 'MAX_WIRES', 'MAX_WORK', 'MAX_POINTS', 'MAX_LIBRARY_BYTES'):
            with self.subTest(limit=limit), patch('agent_ee.schematic_decompile.' + limit, 0):
                result = self.extract(source)
                self.assertEqual(result['report']['execution'], 'unsupported', result)
                self.assertIsNone(result['intent'])

    def test_cli_byte_identical_artifacts_and_no_partial_unsupported_output(self):
        import json
        import tempfile
        from agent_ee.schematic_decompile import main
        from schematic_decompile_fixture import MANIFEST
        with tempfile.TemporaryDirectory(prefix='agent-ee-42-cli-') as directory:
            root = Path(directory)
            (root/'libraries.json').write_text(json.dumps(MANIFEST))
            for index in (1,2):
                code = main([str(FIXTURE/'imu.kicad_sch'), '--libraries', str(root/'libraries.json'),
                    '--captures', str(FIXTURE), '--out', str(root/str(index))])
                self.assertEqual(code, 0)
            for name in ('intent.json','resolved.json','report.json'):
                self.assertEqual((root/'1'/name).read_bytes(), (root/'2'/name).read_bytes())
            bad = root/'bad.kicad_sch'
            bad.write_bytes((FIXTURE/'imu.kicad_sch').read_bytes().rstrip()[:-1] + b'(bus))')
            self.assertEqual(main([str(bad), '--libraries', str(root/'libraries.json'),
                '--captures', str(FIXTURE), '--out', str(root/'bad')]), 1)
            self.assertEqual([p.name for p in (root/'bad').iterdir()], ['report.json'])
