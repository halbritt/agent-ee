"""Two-level hierarchical emission from connectivity-intent/2 (AGENTEE-40).

``fixtures/generation/two_level.expected.json`` was written before any
hierarchical output existed. Endpoints below are computed here from the
captured library pins and the emitted instance positions (all at rotation 0),
not by the emitter. Native tests judge the emitted files only through
kicad-cli 10.0.7 ERC, XML netlist export and upgrade, run through the
production Bubblewrap executor with networking unshared.
"""
import json
import re
import shutil
import tempfile
import unittest
import uuid
from decimal import Decimal
from pathlib import Path

from agent_ee.connectivity_ir import resolve_intent
from agent_ee.native_netlist import read_native_inventory
from agent_ee.netlist_parity import compare, resolved_inventory, validate_policy
from agent_ee.schematic_emitter import emit_schematic
from agent_ee.symbol_library import children, parse_sexpr
from test_schematic_emitter import PROJECT, captures, load, native_netlist, native_project, run_native, tables
from toolchain import require_toolchain

ROOT_FILE, CHILD_FILE = 'two_level.kicad_sch', 'filter.kicad_sch'


def emit(intent=None, layout=None, **kwargs):
    intent = intent or load('two_level.intent.json')
    layout = layout or load('two_level.layout.json')
    return emit_schematic(intent, captures(intent), layout, **kwargs)


def identity(*parts):
    """The frozen identity contract: uuid5 of compact JSON parts under the layout namespace."""
    namespace = uuid.UUID(load('two_level.layout.json')['namespace'])
    return str(uuid.uuid5(namespace, json.dumps(list(parts), separators=(',', ':'))))


def mm(value):
    return Decimal(value)


def library_pins():
    """Pin connection offsets (mm, library y up) per symbol, from the captured library."""
    tree = parse_sexpr((PROJECT / 'lib/Owned_Gen.kicad_sym').read_bytes())
    pins = {}
    for symbol in children(tree, 'symbol'):
        for unit in children(symbol, 'symbol'):
            for pin in children(unit, 'pin'):
                at = children(pin, 'at')[0]
                number = children(pin, 'number')[0][1]
                pins.setdefault(symbol[1], {})[number] = (mm(at[1]), mm(at[2]))
    return pins


def structure(data):
    """Labels, markers, symbols and sheet symbols of one emitted file via the shared reader."""
    root = parse_sexpr(data)
    def point(node):
        at = children(node, 'at')[0]
        return (mm(at[1]), mm(at[2]))
    labels = {kind: [(n[1], point(n)) for n in children(root, kind)]
              for kind in ('label', 'global_label', 'hierarchical_label')}
    symbols = {}
    for node in children(root, 'symbol'):
        ref = next(p[2] for p in children(node, 'property') if p[1] == 'Reference')
        path = children(children(children(node, 'instances')[0], 'project')[0], 'path')[0]
        symbols[ref] = dict(lib_id=children(node, 'lib_id')[0][1], at=point(node), uuid=children(node, 'uuid')[0][1],
                            path=path[1], instance_ref=children(path, 'reference')[0][1])
    sheets = {}
    for node in children(root, 'sheet'):
        name = next(p[2] for p in children(node, 'property') if p[1] == 'Sheetname')
        sheets[name] = dict(uuid=children(node, 'uuid')[0][1], file=next(
            p[2] for p in children(node, 'property') if p[1] == 'Sheetfile'),
            pins=[(p[1], p[2], point(p)) for p in children(node, 'pin')])
    markers = [point(n) for n in children(root, 'no_connect')]
    return dict(uuid=children(root, 'uuid')[0][1], labels=labels, symbols=symbols, sheets=sheets, markers=markers)


class HierarchyStructureTests(unittest.TestCase):
    def test_sheet_pins_hierarchical_and_local_labels_match_the_expectations(self):
        result, files = emit(**tables())
        self.assertEqual(result['execution'], 'ok', result['findings'])
        self.assertEqual(sorted(files), ['filter.kicad_sch', 'fp-lib-table', 'sym-lib-table', 'two_level.kicad_pro',
                                         'two_level.kicad_sch'])
        expected = load('two_level.expected.json')
        pins = library_pins()
        intent = load('two_level.intent.json')
        nc = {(n['sheet'], n['component'], n['pin']['number']) for n in intent['no_connects']}
        refs = {(c['sheet'], c['id']): c['reference'] for c in intent['components']}
        sheet_files = {s['id']: s['file'] for s in intent['hierarchy']}
        for name, want in expected['sheets'].items():
            with self.subTest(sheet=name):
                got = structure(files[name])
                self.assertEqual(sorted({n for n, _ in got['labels']['label']}), want['local_labels'])
                self.assertEqual(sorted({n for n, _ in got['labels']['hierarchical_label']}),
                                 want['hierarchical_labels'])
                self.assertEqual(got['labels']['global_label'], [])
                # One sheet pin per shared net, in name order.
                self.assertEqual({k: [p[0] for p in v['pins']] for k, v in got['sheets'].items()}, want['sheet_pins'])
                self.assertEqual(sorted(got['symbols']), expected['components_by_sheet'][name])
                # Every connected pin endpoint carries exactly one label, every NC pin one marker,
                # and every sheet pin one parent label: nothing is silently dropped.
                connected, markers = [], []
                for ref, symbol in got['symbols'].items():
                    x, y = symbol['at']
                    sheet = next(s for s, f in sheet_files.items() if f == name)
                    cid = next(c for (s, c), r in refs.items() if r == ref and s == sheet)
                    for number, (px, py) in pins[symbol['lib_id'].split(':')[1]].items():
                        endpoint = (x + px, y - py)
                        (markers if (sheet, cid, number) in nc else connected).append(endpoint)
                sheet_points = [p[2] for s in got['sheets'].values() for p in s['pins']]
                label_points = [p for kind in ('label', 'hierarchical_label') for _, p in got['labels'][kind]]
                self.assertEqual(sorted(label_points), sorted(connected + sheet_points))
                self.assertEqual(sorted(got['markers']), sorted(markers))
        root, child = structure(files[ROOT_FILE]), structure(files[CHILD_FILE])
        # Parent labels on the sheet pins name the parent segments, not the port names.
        names = {point: n for n, point in root['labels']['label']}
        self.assertEqual({p[0]: names[p[2]] for p in root['sheets']['filter']['pins']}, {'IN': 'SIG', 'GND': 'GND'})
        self.assertEqual({p[0]: p[1] for p in root['sheets']['filter']['pins']}, {'IN': 'passive', 'GND': 'passive'})
        # The child's hierarchical labels sit only on the pins of the port segments.
        hier = sorted(n for n, _ in child['labels']['hierarchical_label'])
        self.assertEqual(hier, ['GND', 'GND', 'IN'])

    def test_identity_contract_paths_and_stability(self):
        result, files = emit()
        self.assertEqual(result['execution'], 'ok', result['findings'])
        root_uuid = identity('document', ROOT_FILE)
        sheet_uuid = identity('sheet', ROOT_FILE, 'filter')
        root, child = structure(files[ROOT_FILE]), structure(files[CHILD_FILE])
        self.assertEqual((result['root_uuid'], root['uuid']), (root_uuid, root_uuid))
        self.assertEqual(root['sheets']['filter']['uuid'], sheet_uuid)
        self.assertEqual(root['sheets']['filter']['file'], CHILD_FILE)
        intent = load('two_level.intent.json')
        for comp in intent['components']:
            got = (root if comp['sheet'] == 'root' else child)['symbols'][comp['reference']]
            document = ROOT_FILE if comp['sheet'] == 'root' else CHILD_FILE
            path = '/' + root_uuid + ('' if comp['sheet'] == 'root' else '/' + sheet_uuid)
            self.assertEqual((got['uuid'], got['path'], got['instance_ref']),
                             (identity('symbol', document, comp['id'], 1), path, comp['reference']))
        project = json.loads(files['two_level.kicad_pro'])
        self.assertEqual(project['sheets'], [[root_uuid, 'Root'], [sheet_uuid, 'filter']])
        # Repeated and reordered input: identical bytes.
        shuffled = load('two_level.intent.json')
        for key in ('components', 'nets', 'ports', 'hierarchy'):
            shuffled[key].reverse()
        self.assertEqual(emit(intent=shuffled)[1], files)
        self.assertEqual(emit()[1], files)
        # Adding a child component keeps every existing UUID.
        grown = load('two_level.intent.json')
        grown['components'].append(dict(sheet='filter', id='c_more', reference='C5', lib_id='OwnedGen:CAP',
                                        value='1n', footprint='OwnedFP:CAP_2PAD', units=[1]))
        next(n for n in grown['nets'] if (n['sheet'], n['id']) == ('filter', 'local'))['terminals'].append(
            dict(component='c_more', pin=dict(number='1')))
        grown['no_connects'].append(dict(sheet='filter', component='c_more', pin=dict(number='2')))
        result, more = emit(intent=grown)
        self.assertEqual(result['execution'], 'ok', result['findings'])
        uuids = re.compile(rb'\(uuid "([0-9a-f-]{36})"\)')
        for name in (ROOT_FILE, CHILD_FILE):
            self.assertLessEqual(set(uuids.findall(files[name])), set(uuids.findall(more[name])), name)

    def assertRefused(self, code, intent=None, layout=None):
        result, files = emit(intent=intent, layout=layout)
        self.assertIsNone(files)
        self.assertIn(code, [f['code'] for f in result['findings']], result['findings'])
        self.assertEqual(result['files'], [])
        return result

    def mutate(self, edit):
        intent = load('two_level.intent.json')
        edit(intent)
        return intent

    def test_refusals_emit_nothing(self):
        # A port whose name is not its child segment label would need two names on one segment.
        self.assertRefused('port_label_alias', self.mutate(lambda i: i['ports'][0].update(name='INPUT')))
        self.assertRefused('layout_schema', self.mutate(lambda i: i['hierarchy'][0].update(file='main.kicad_sch')))
        self.assertRefused('hierarchy_file_unsupported', self.mutate(
            lambda i: i['hierarchy'][1].update(file='sheets/filter.kicad_sch')))
        # Resolver-owned boundaries stop emission too; there is no global-name fallback.
        result = self.assertRefused('resolution_singleton_net', self.mutate(lambda i: i['ports'].pop(0)))
        self.assertEqual(result['execution'], 'failed')
        result = self.assertRefused('resolution_hierarchy_schema', self.mutate(lambda i: i['hierarchy'].append(
            dict(id='again', parent='root', local_id='again', file=CHILD_FILE))))
        self.assertEqual(result['execution'], 'unsupported')
        self.assertRefused('resolution_hierarchy_schema', self.mutate(
            lambda i: i['ports'][0].update(name='LOCAL_SAME')))
        layout = load('two_level.layout.json')
        layout['transforms'] = {'c_in': {'rotation': 0, 'mirror': None}}
        self.assertRefused('layout_schema', layout=layout)

    def test_child_transforms_are_keyed_by_scoped_identity(self):
        layout = load('two_level.layout.json')
        layout['transforms'] = {'["filter","c_in"]': {'rotation': 90, 'mirror': None}}
        result, files = emit(layout=layout)
        self.assertEqual(result['execution'], 'ok', result['findings'])
        node = next(n for n in children(parse_sexpr(files[CHILD_FILE]), 'symbol')
                    if any(p[1:3] == ['Reference', 'C2'] for p in children(n, 'property')))
        self.assertEqual(children(node, 'at')[0][3], '90')


class NativeHierarchyTests(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)
        self.tmp = Path(tempfile.mkdtemp(prefix='agent-ee-emitter-hierarchy-'))
        self.addCleanup(shutil.rmtree, self.tmp)

    def native(self, files, label):
        project = native_project(files, self.tmp / label)
        work = self.tmp / label / 'work'
        run_native(project, work / 'erc', 'kicad-cli', 'sch', 'erc', '--format', 'json', '--severity-all',
                   '--units', 'mm', '-o', '/work/erc.json', '/candidate/' + ROOT_FILE)
        run_native(project, work / 'netlist', 'kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml',
                   '-o', '/work/design.xml', '/candidate/' + ROOT_FILE)
        erc = json.loads((work / 'erc' / 'erc.json').read_text())
        self.assertEqual(erc['kicad_version'], '10.0.7')
        return project, work, erc

    def test_two_level_erc_netlist_and_current_format(self):
        result, files = emit(**tables())
        self.assertEqual(result['execution'], 'ok', result['findings'])
        project, work, erc = self.native(files, 'two-level')
        self.assertEqual({s['path']: s['violations'] for s in erc['sheets']}, {'/': [], '/filter/': []})
        components, nets, singles = native_netlist(work / 'netlist' / 'design.xml')
        expected = load('two_level.expected.json')
        self.assertEqual(sorted(sorted(n) for n in nets.values()), sorted(sorted(n) for n in expected['physical_nets']))
        self.assertEqual([[r, p] for (r, p), _ in singles.values()], expected['explicit_nc'])
        self.assertTrue(all('no_connect' in kind for _, kind in singles.values()))
        # The two LOCAL_SAME nets stay distinct natively; internal names are sheet-scoped.
        self.assertEqual(sorted(n for n in nets if n.endswith('LOCAL_SAME')), ['/LOCAL_SAME', '/filter/LOCAL_SAME'])
        sheet_path = '/' + identity('sheet', ROOT_FILE, 'filter') + '/'
        intent = load('two_level.intent.json')
        for comp in intent['components']:
            native = components[comp['reference']]
            self.assertEqual((native['sheet'], native['uuid']),
                             ('/' if comp['sheet'] == 'root' else sheet_path,
                              identity('symbol', ROOT_FILE if comp['sheet'] == 'root' else CHILD_FILE, comp['id'], 1)))
            self.assertEqual((native['value'], native['footprint']), (comp['value'], comp['footprint']))
        # Both files are current: the upgrader leaves them alone, and a forced
        # native re-save reproduces them apart from the generator name.
        for name in (ROOT_FILE, CHILD_FILE):
            with self.subTest(file=name):
                copy_dir = work / ('upgrade-' + name) / 'copy'
                shutil.copytree(project, copy_dir)
                run_native(project, copy_dir.parent, 'kicad-cli', 'sch', 'upgrade', '/work/copy/' + name)
                self.assertIn('Schematic file was not updated', (copy_dir.parent / 'console.log').read_text())
                self.assertEqual((copy_dir / name).read_bytes(), files[name])
                resave = work / ('resave-' + name)
                shutil.copytree(project, resave / 'copy')
                run_native(project, resave, 'kicad-cli', 'sch', 'upgrade', '--force', '/work/copy/' + name)
                native = (resave / 'copy' / name).read_bytes()
                self.assertNotEqual(native, files[name])
                self.assertEqual(native.replace(b'(generator "eeschema")',
                                                b'(generator "agent-ee-schematic-emitter")', 1), files[name])

    def test_three_level_pass_through_and_sibling(self):
        """A middle sheet with no parts carries its segment on the grandchild's sheet pin."""
        intent, layout = load('three_level.intent.json'), load('three_level.layout.json')
        result, files = emit_schematic(intent, captures(intent), layout, **tables())
        self.assertEqual(result['execution'], 'ok', result['findings'])
        middle = structure(files['mid.kicad_sch'])
        self.assertEqual((middle['symbols'], middle['labels']['label']), ({}, []))
        leaf_pins = middle['sheets']['leaf']['pins']
        self.assertEqual([(name, point) for name, _, point in leaf_pins],
                         middle['labels']['hierarchical_label'])
        self.assertEqual({k: [p[0] for p in v['pins']] for k, v in structure(files['three_level.kicad_sch'])['sheets']
                          .items()}, {'mid': ['THRU'], 'side': ['Y']})
        project = native_project(files, self.tmp / 'three')
        work = self.tmp / 'three' / 'work'
        run_native(project, work / 'erc', 'kicad-cli', 'sch', 'erc', '--format', 'json', '--severity-all',
                   '--units', 'mm', '-o', '/work/erc.json', '/candidate/three_level.kicad_sch')
        run_native(project, work / 'netlist', 'kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml',
                   '-o', '/work/design.xml', '/candidate/three_level.kicad_sch')
        erc = json.loads((work / 'erc' / 'erc.json').read_text())
        self.assertEqual({s['path']: s['violations'] for s in erc['sheets']},
                         {'/': [], '/mid/': [], '/mid/leaf/': [], '/side/': []})
        expected = resolved_inventory(resolve_intent(intent, captures(intent)))
        actual = read_native_inventory(work / 'netlist' / 'design.xml')
        for predicate in ('topology', 'components'):
            policy = validate_policy(dict(source=dict(kind='intent', path='three_level.intent.json'),
                                          netlist_check='netlist', predicate=predicate, multiset='exactly'))
            self.assertEqual(compare(expected, actual, policy)['outcome'], 'pass', predicate)

    def test_native_erc_detects_missing_and_extra_sheet_pins(self):
        result, files = emit(**tables())
        self.assertEqual(result['execution'], 'ok', result['findings'])
        text = files[ROOT_FILE].decode()
        block = re.search(r'\t\t\(pin "IN" passive\n.*?\n\t\t\)\n', text, re.S).group(0)
        missing = dict(files, **{ROOT_FILE: text.replace(block, '', 1).encode()})
        # An undeclared pin elsewhere on the sheet edge, with its own UUID.
        at = re.search(r'\(at ([0-9.]+) ([0-9.]+) 180\)', block)
        extra_block = block.replace('"IN"', '"EXTRA"').replace(
            at.group(0), f'(at {at.group(1)} {Decimal(at.group(2)) + Decimal("5.08")} 180)')
        extra_block = re.sub(r'\(uuid "[^"]+"\)', '(uuid "0b6c3f0e-58a4-4a64-9f5e-1c1d2c3b4a59")', extra_block)
        extra = dict(files, **{ROOT_FILE: text.replace(block, block + extra_block, 1).encode()})
        for label, variant in (('missing', missing), ('extra', extra)):
            with self.subTest(sheet_pin=label):
                self.assertNotEqual(variant[ROOT_FILE], files[ROOT_FILE])
                _, _, erc = self.native(variant, label)
                kinds = [v['type'] for s in erc['sheets'] for v in s['violations']]
                self.assertIn('hier_label_mismatch', kinds)


if __name__ == '__main__':
    unittest.main()
