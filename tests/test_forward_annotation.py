"""Forward annotation onto an owned placed and routed board (AGENTEE-52).

Inputs are actual kicad-cli XML exports of emitted schematics and the owned
routed fixture board (fixtures/generation/regulator.kicad_pcb, native DRC with
schematic parity clean against the emitted regulator). Preservation is judged
by the existing independent ``agent_ee.geometry_probe`` and by raw copper
S-expressions read through the shared reader, never by the helper's own
facts. The native reader is AGENTEE-41's ``native_netlist``; it is an internal
dependency, so its absence is an import error here, never a skip.
"""
import contextlib
import copy
import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path

from agent_ee import forward_annotation as fa
from agent_ee.native_netlist import read_native_inventory
from agent_ee.schematic_emitter import emit_schematic
from agent_ee.symbol_library import children, parse_sexpr
try:
    from test_schematic_emitter import PROJECT, ROOT, captures, load, run_native, tables
    from toolchain import require_toolchain
except ImportError:  # module-qualified invocation from the repository root
    from tests.test_schematic_emitter import PROJECT, ROOT, captures, load, run_native, tables
    from tests.toolchain import require_toolchain

BOARD = ROOT / 'regulator.kicad_pcb'
MANIFEST = ROOT / 'footprints.manifest.json'


def by_uuid(items):
    """Inventory comparison independent of native save order."""
    return sorted(items, key=lambda x: x['uuid'])


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def protected():
    """Hashes of every source fixture the helper must never change."""
    manifest = load('footprints.manifest.json')
    files = [BOARD, MANIFEST] + [PROJECT / m['path'] for lib in manifest['libraries'] for m in lib['members']]
    return {str(f): sha(f) for f in files}


def copper(board_bytes):
    """Raw segment, via and zone nodes with net bindings separated from geometry."""
    root = parse_sexpr(board_bytes)
    geometry, nets = [], []
    for node in root[1:]:
        if isinstance(node, list) and node[0] in ('segment', 'via', 'zone', 'arc'):
            uid = children(node, 'uuid')[0][1]
            geometry.append((uid, [x for x in node if not (isinstance(x, list) and x[0] in ('net', 'net_name'))]))
            nets.append((uid, [x for x in node if isinstance(x, list) and x[0] == 'net']))
    return sorted(geometry), sorted(nets)


def read_native_inventory_bytes(data, tmp):
    """Components of native XML bytes through the shared AGENTEE-41 reader."""
    with tempfile.NamedTemporaryFile(dir=tmp, suffix='.xml') as stream:
        stream.write(data)
        stream.flush()
        return read_native_inventory(stream.name)['components']


def footprint_poses(board_bytes):
    root = parse_sexpr(board_bytes)
    poses = {}
    for node in children(root, 'footprint'):
        path = children(node, 'path')
        poses[path[0][1] if path else None] = (node[1], children(node, 'uuid')[0][1], children(node, 'layer')[0],
                                               children(node, 'at')[0])
    return poses


class ForwardAnnotationTests(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)
        self.tmp = Path(tempfile.mkdtemp(prefix='agent-ee-annotation-'))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.before = protected()
        self.manifest = load('footprints.manifest.json')

    def tearDown(self):
        self.assertEqual(protected(), self.before)

    def project(self, intent, label, name='regulator', edit=None):
        """Emitted project with a copy of the routed fixture board and its native XML.

        ``edit`` changes the emitted schematic bytes before the native export, so
        that KiCad itself produces the XML for a schematic the emitter never writes.
        """
        layout = load(name + '.layout.json')
        result, files = emit_schematic(intent, captures(intent), layout, **tables())
        self.assertEqual(result['execution'], 'ok', result['findings'])
        project = self.tmp / label / 'project'
        shutil.copytree(PROJECT / 'lib', project / 'lib')
        for file, data in files.items():
            if edit and file.endswith('.kicad_sch'):
                data = edit(data)
            (project / file).write_bytes(data)
        if name == 'regulator':
            shutil.copyfile(BOARD, project / 'regulator.kicad_pcb')
        work = self.tmp / label / 'xml'
        run_native(project, work, 'kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml',
                   '-o', '/work/design.xml', f'/candidate/{layout["project"]}.kicad_sch')
        xml = self.tmp / label / 'design.xml'
        shutil.copyfile(work / 'design.xml', xml)
        return project, xml

    def geometry(self, project, label):
        work = self.tmp / 'geometry' / label
        run_native(project, work, '/usr/bin/python3', '-B', '-m', 'agent_ee.geometry_probe',
                   '/candidate/regulator.kicad_pcb', '-o', '/work/geometry.json')
        return json.loads((work / 'geometry.json').read_text())

    def drc(self, project, label):
        work = self.tmp / 'drc' / label
        run_native(project, work, 'kicad-cli', 'pcb', 'drc', '--format', 'json', '--severity-all',
                   '--schematic-parity', '--units', 'mm', '-o', '/work/drc.json', '/candidate/regulator.kicad_pcb')
        self.assertNotIn('parity', (work / 'console.log').read_text().lower().replace('schematic parity issues', ''))
        return json.loads((work / 'drc.json').read_text())

    def annotate(self, project, xml, label):
        board = project / 'regulator.kicad_pcb'
        plan = fa.plan(board, xml, self.manifest, PROJECT, self.tmp / label / 'plan')
        self.assertEqual(plan['execution'], 'ok', plan['findings'])
        result = fa.apply(plan, board, xml, self.manifest, PROJECT, board, self.tmp / label / 'apply',
                          backup_dir=self.tmp / label / 'backup')
        self.assertEqual(result['execution'], 'ok', result['findings'])
        self.assertEqual(sha(result['backup']), sha(BOARD))
        return plan, result

    def intent(self, edit=None):
        intent = load('regulator.intent.json')
        if edit:
            edit(intent)
        return intent

    def test_fixture_board_is_clean_against_emitted_schematic(self):
        project, _ = self.project(self.intent(), 'baseline')
        report = self.drc(project, 'baseline')
        self.assertEqual((report['violations'], report['unconnected_items'], report['schematic_parity']), ([], [], []))

    def test_native_net_name_escaping_matches_native_export_and_parity(self):
        """Original fixture: pin-name slash and label slash, braces and spaces."""
        self.maxDiff = None
        def edit(data):
            return (data.replace(b'(name "NC"', b'(name "SDA/~{SPI_CS} {X}:Q"')
                    .replace(b'(label "VOUT"', b'(global_label "A/B {C} D:E" (shape input)'))
        project, xml = self.project(self.intent(), 'escaping', edit=edit)
        work = self.tmp / 'escaping-native-netlist'
        run_native(project, work, 'kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadsexpr',
                   '-o', '/work/design.net', '/candidate/regulator.kicad_sch')
        native = parse_sexpr((work / 'design.net').read_bytes())
        expected = {children(n, 'name')[0][1] for n in children(children(native, 'nets')[0], 'net')}
        raw = {n['name'] for n in read_native_inventory(xml)['nets'].values()}
        self.assertIn('A/B {C} D:E', raw)
        self.assertIn('unconnected-(U1-SDA/~{SPI_CS}_{X}:Q-Pad4)', raw)
        self.assertIn('A{slash}B {C} D:E', expected)
        self.assertIn('unconnected-(U1-SDA{slash}~{SPI_CS}_{X}:Q-Pad4)', expected)
        before = self.geometry(project, 'escape-before')
        plan, _ = self.annotate(project, xml, 'escaping')
        after = self.geometry(project, 'escape-after')
        # A separate process reloads the saved board and extracts pad copper layers.
        for key in ('footprints', 'pads'):
            def layers(items):
                return sorted((p['uuid'], p.get('layer'), p.get('flash_layers')) for p in items)
            self.assertEqual(layers(before[key]), layers(after[key]))
        self.assertEqual(self.drc(project, 'escaping')['schematic_parity'], [])
        self.assertEqual({p['net'] for p in plan['pads']}, expected)
        again = fa.plan(project / 'regulator.kicad_pcb', xml, self.manifest, PROJECT, self.tmp / 'escape-again')
        self.assertEqual(again['execution'], 'ok', again['findings'])
        self.assertEqual((again['renames'], again['new_nets'], again['counts']['pad_changes']), ([], [], 0))
        # Both XML-to-board producers use the same conversion; parity retains raw XML names.
        from agent_ee.board_build import read_netlist
        self.assertEqual({n['name'] for n in read_netlist(xml.read_bytes())['nets']}, expected)
        self.assertEqual({n['name'] for n in read_native_inventory(xml)['nets'].values()}, raw)
        from agent_ee.board_build import read_envelope
        envelope = read_envelope(dict(schema='agent-ee.rung1-envelope/0-experimental',
            outline_mm=dict(x=100, y=100, w=30, h=20), copper_layers=2,
            stackup='template-2-layer', fixed_refs=[]))
        spec = dict(netlist=read_netlist(xml.read_bytes()), envelope=envelope,
            template='/candidate/regulator.kicad_pcb', output='/work/regulator.kicad_pcb',
            seed_hashes=['0' * 64], footprints={
                lib['name'] + ':' + Path(m['path']).stem: '/candidate/' + m['path']
                for lib in self.manifest['libraries'] for m in lib['members']})
        (project / 'build-spec.json').write_text(json.dumps(spec))
        work = self.tmp / 'escaping-board-build'
        run_native(project, work, '/usr/bin/python3', '-B', '-m', 'agent_ee.board_build',
                   '--construct', '/candidate/build-spec.json')
        shutil.copyfile(work / 'regulator.kicad_pcb', project / 'regulator.kicad_pcb')
        rebuilt = self.geometry(project, 'escape-built-reload')
        self.assertEqual({p['net'] for p in rebuilt['pads']}, expected)
        # Rebuilt UUIDs differ; compare saved copper-layer inventories by reference/pad.
        def pad_layers(report):
            return sorted((f['ref'], p['pad'], p['flash_layers'])
                          for f in report['footprints'] for p in f['pads'])
        self.assertEqual(pad_layers(before), pad_layers(rebuilt))
        self.assertEqual(self.drc(project, 'escape-built')['schematic_parity'], [])

    def test_xml_board_encoding_character_table_against_native_export(self):
        from agent_ee.board_build import read_netlist
        for index, name in enumerate(('A/B', 'A{B', 'A}B', 'A B', "A:B'C", 'A{slash}B', '~{RESET}/X')):
            with self.subTest(name=name):
                def edit(data):
                    return data.replace(b'(label "VOUT"',
                        ('(global_label ' + json.dumps(name) + ' (shape input)').encode())
                project, xml = self.project(self.intent(), 'characters-' + str(index), edit=edit)
                work = self.tmp / ('characters-native-' + str(index))
                run_native(project, work, 'kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadsexpr',
                           '-o', '/work/design.net', '/candidate/regulator.kicad_sch')
                native = parse_sexpr((work / 'design.net').read_bytes())
                self.assertEqual({n['name'] for n in read_netlist(xml.read_bytes())['nets']},
                    {children(n, 'name')[0][1] for n in children(children(native, 'nets')[0], 'net')})

    def test_net_encoding_refuses_collisions_and_ambiguous_local_slashes(self):
        from agent_ee.board_build import BoardBuildError, read_netlist
        project, xml = self.project(self.intent(), 'encoding-refusals')
        empty = dict(footprints=[], tracks=[], zones=[], edge_bbox_nm=[0, 0, 10000000, 10000000])
        captured = {m['path']: (PROJECT / m['path']).read_bytes()
                    for lib in self.manifest['libraries'] for m in lib['members']}
        original = xml.read_bytes()
        for old, new, message in [
                (b'name="/VOUT"', b'name="/A/B"', 'ambiguous local net slash'),
                (b'name="/VIN"', b'name="A/B"', None)]:
            data = original.replace(old, new)
            if message is None:
                data = data.replace(b'name="/VOUT"', b'name="A{slash}B"')
                message = 'collide after board encoding'
            self.assertNotEqual(data, original)
            plan = fa.build_plan(data, '0' * 64, empty, self.manifest, captured)
            self.assertEqual(plan['execution'], 'failed', plan)
            self.assertIn(message, plan['findings'][0]['message'])
            with self.assertRaisesRegex(BoardBuildError, message):
                read_netlist(data)
        self.assertEqual((project / 'regulator.kicad_pcb').read_bytes(), BOARD.read_bytes())

    def test_adding_one_component_preserves_layout_and_reapply_is_a_noop(self):
        def add(i):
            i['components'].append(dict(id='c_extra', reference='C2', lib_id='OwnedGen:CAP', value='100n',
                                        footprint='OwnedFP:CAP_2PAD', units=[1]))
            next(n for n in i['nets'] if n['id'] == 'vout')['terminals'].append(
                {'component': 'c_extra', 'pin': {'number': '1'}})
            next(n for n in i['nets'] if n['id'] == 'gnd')['terminals'].append(
                {'component': 'c_extra', 'pin': {'number': '2'}})
        project, xml = self.project(self.intent(add), 'add')
        xml_sha = sha(xml)
        before = self.geometry(project, 'add-before')
        source = BOARD.read_bytes()
        plan, _ = self.annotate(project, xml, 'add')
        self.assertEqual((plan['counts']['add'], plan['counts']['remove'], plan['counts']['renames'],
                          plan['counts']['new_nets'], plan['counts']['retain']), (1, 0, 0, 0, 3))
        after = self.geometry(project, 'add-after')
        output = (project / 'regulator.kicad_pcb').read_bytes()
        old = {f['ref']: f for f in before['footprints']}
        new = {f['ref']: f for f in after['footprints']}
        self.assertEqual(sorted(new), ['C1', 'C2', 'J1', 'U1'])
        for ref in old:
            self.assertEqual({k: new[ref][k] for k in ('uuid', 'position_mm', 'layer', 'pads')},
                             {k: old[ref][k] for k in ('uuid', 'position_mm', 'layer', 'pads')}, ref)
        old_poses, new_poses = footprint_poses(source), footprint_poses(output)
        for path, pose in old_poses.items():
            self.assertEqual(new_poses[path], pose)
        self.assertEqual(by_uuid(before['tracks']), by_uuid(after['tracks']))
        self.assertEqual(by_uuid(before['zones']), by_uuid(after['zones']))
        self.assertEqual(copper(source), copper(output))
        # The new footprint: staged right of the 100..130 mm outline on the 1.27 mm grid.
        c2 = new['C2']
        self.assertEqual({p['pad']: p['net'] for p in c2['pads']}, {'1': '/VOUT', '2': 'GND'})
        x, y = c2['position_mm']
        self.assertGreater(x, 130 + 5)
        self.assertTrue(100 <= y <= 120)
        self.assertEqual([round(v * 1000) % 1270 for v in (x, y)], [0, 0])
        namespace = uuid.UUID(load('regulator.layout.json')['namespace'])
        expected_path = '/' + str(uuid.uuid5(namespace, json.dumps(['symbol', 'c_extra', 1], separators=(',', ':'))))
        self.assertIn(expected_path, new_poses)
        report = self.drc(project, 'add')
        self.assertEqual(report['schematic_parity'], [])
        unrouted = {item['description'] for v in report['unconnected_items'] for item in v['items']}
        self.assertTrue(unrouted and all('C2' in d or 'Track' in d or 'Via' in d or 'Zone' in d or 'Pad' in d
                                         for d in unrouted), unrouted)
        self.assertTrue(any('C2' in d for d in unrouted))
        # Same inputs again: no change at all, byte for byte.
        again = fa.plan(project / 'regulator.kicad_pcb', xml, self.manifest, PROJECT, self.tmp / 'again-plan')
        self.assertEqual(again['execution'], 'ok', again['findings'])
        self.assertEqual({k: v for k, v in again['counts'].items() if k not in ('retain',)},
                         dict(add=0, remove=0, update=0, board_only=0, renames=0, new_nets=0, stale_nets=0,
                              pad_changes=0))
        out = self.tmp / 'again-out.kicad_pcb'
        result = fa.apply(again, project / 'regulator.kicad_pcb', xml, self.manifest, PROJECT, out,
                          self.tmp / 'again-apply')
        self.assertEqual(result['execution'], 'ok', result['findings'])
        self.assertEqual(out.read_bytes(), output)
        self.assertEqual(sha(xml), xml_sha)

    def test_renamed_net_keeps_pad_track_via_and_zone_bindings(self):
        def rename(i):
            next(n for n in i['nets'] if n['id'] == 'gnd')['name'] = 'AGND'
            next(c for c in i['components'] if c['id'] == 'gnd')['value'] = 'AGND'
        project, xml = self.project(self.intent(rename), 'rename')
        before = self.geometry(project, 'rename-before')
        plan, _ = self.annotate(project, xml, 'rename')
        self.assertEqual(plan['renames'], [{'from': 'GND', 'to': 'AGND'}])
        self.assertEqual((plan['new_nets'], plan['counts']['pad_changes'], plan['counts']['add']), ([], 0, 0))
        after = self.geometry(project, 'rename-after')
        swap = lambda net: 'AGND' if net == 'GND' else net
        for key in ('tracks', 'zones'):
            self.assertEqual(by_uuid(dict(x, net=swap(x['net'])) for x in before[key]), by_uuid(after[key]))
        vias = [t for t in after['tracks'] if t['type'] == 'via']
        self.assertEqual({v['net'] for v in vias}, {'AGND'})
        self.assertEqual([z['net'] for z in after['zones']], ['AGND'])
        self.assertEqual(by_uuid(dict(p, net=swap(p['net'])) for p in before['pads']), by_uuid(after['pads']))
        geometry, nets = copper((project / 'regulator.kicad_pcb').read_bytes())
        source_geometry, source_nets = copper(BOARD.read_bytes())
        self.assertEqual(geometry, source_geometry)
        self.assertNotEqual(nets, source_nets)
        report = self.drc(project, 'rename')
        self.assertEqual((report['violations'], report['unconnected_items'], report['schematic_parity']), ([], [], []))

    def test_removed_component_leaves_its_copper_for_drc(self):
        def remove(i):
            i['components'] = [c for c in i['components'] if c['id'] != 'c_out']
            for net in i['nets']:
                net['terminals'] = [t for t in net['terminals'] if t['component'] != 'c_out']
        project, xml = self.project(self.intent(remove), 'remove')
        before = self.geometry(project, 'remove-before')
        plan, _ = self.annotate(project, xml, 'remove')
        self.assertEqual([r['reference'] for r in plan['remove']], ['C1'])
        after = self.geometry(project, 'remove-after')
        self.assertEqual(sorted(f['ref'] for f in after['footprints']), ['J1', 'U1'])
        self.assertEqual(by_uuid(before['tracks']), by_uuid(after['tracks']))
        self.assertEqual(by_uuid(before['zones']), by_uuid(after['zones']))
        self.assertEqual(copper((project / 'regulator.kicad_pcb').read_bytes()), copper(BOARD.read_bytes()))
        report = self.drc(project, 'remove')
        self.assertEqual(report['schematic_parity'], [])
        reported = [v for v in report['violations'] + report['unconnected_items']
                    if any('Track' in i['description'] for i in v['items'])]
        self.assertTrue(reported, report['violations'] + report['unconnected_items'])

    def test_refusals_happen_before_any_mutation(self):
        cases = []
        missing = self.intent(lambda i: next(c for c in i['components'] if c['id'] == 'c_out').update(
            footprint='OwnedFP:MISSING'))
        cases.append(('missing_footprint', missing, self.manifest, None))
        unreadable = copy.deepcopy(self.manifest)
        unreadable['libraries'][0]['members'].append(dict(path='lib/OwnedFP.pretty/ABSENT.kicad_mod', sha256='0' * 64))
        cases.append(('footprint_library_coverage', self.intent(), unreadable, None))
        duplicate = BOARD.read_text()
        paths = [line for line in duplicate.splitlines() if line.strip().startswith('(path "/')]
        duplicate = duplicate.replace(paths[1], paths[0])
        cases.append(('duplicate_native_path', self.intent(), self.manifest, duplicate))
        for code, intent, manifest, board_text in cases:
            with self.subTest(code=code):
                project, xml = self.project(intent, code)
                board = project / 'regulator.kicad_pcb'
                if board_text is not None:
                    board.write_text(board_text)
                original = board.read_bytes()
                plan = fa.plan(board, xml, manifest, PROJECT, self.tmp / code / 'plan')
                self.assertEqual(plan['execution'], 'failed')
                self.assertEqual([f['code'] for f in plan['findings']], [code])
                result = fa.apply(plan, board, xml, manifest, PROJECT, board, self.tmp / code / 'apply',
                                  backup_dir=self.tmp / code / 'backup')
                self.assertEqual((result['execution'], result['findings'][0]['code']), ('failed', 'plan_rejected'))
                self.assertEqual(board.read_bytes(), original)
                self.assertFalse((self.tmp / code / 'backup').exists())

    def test_stale_or_tampered_plans_and_unsafe_backups_are_refused(self):
        def add(i):
            i['components'].append(dict(id='c_extra', reference='C2', lib_id='OwnedGen:CAP', value='100n',
                                        footprint='OwnedFP:CAP_2PAD', units=[1]))
            next(n for n in i['nets'] if n['id'] == 'vout')['terminals'].append(
                {'component': 'c_extra', 'pin': {'number': '1'}})
            next(n for n in i['nets'] if n['id'] == 'gnd')['terminals'].append(
                {'component': 'c_extra', 'pin': {'number': '2'}})
        project, xml = self.project(self.intent(add), 'stale')
        board = project / 'regulator.kicad_pcb'
        plan = fa.plan(board, xml, self.manifest, PROJECT, self.tmp / 'stale-plan')
        self.assertEqual(plan['execution'], 'ok', plan['findings'])
        tampered = copy.deepcopy(plan)
        tampered['add'][0]['position_nm'][0] += 1270000
        variants = [('stale_plan', tampered, board, xml), ('backup_location', plan, board, xml)]
        changed_xml = self.tmp / 'changed.xml'
        changed_xml.write_bytes(xml.read_bytes().replace(b'100n', b'220n'))
        variants.append(('stale_plan', plan, board, changed_xml))
        for index, (code, record, target, netlist) in enumerate(variants):
            with self.subTest(code=code, index=index):
                original = target.read_bytes()
                backup = project / 'backup' if code == 'backup_location' else self.tmp / f'backup-{index}'
                result = fa.apply(record, target, netlist, self.manifest, PROJECT, target,
                                  self.tmp / f'stale-apply-{index}', backup_dir=backup)
                self.assertEqual((result['execution'], [f['code'] for f in result['findings']]), ('failed', [code]))
                self.assertEqual(target.read_bytes(), original)
                self.assertFalse(backup.exists())
        board.write_bytes(board.read_bytes() + b'\n')
        result = fa.apply(plan, board, xml, self.manifest, PROJECT, self.tmp / 'new.kicad_pcb',
                          self.tmp / 'stale-board')
        self.assertEqual([f['code'] for f in result['findings']], ['stale_plan'])
        self.assertFalse((self.tmp / 'new.kicad_pcb').exists())

    def test_binding_uses_native_unit_identity_never_references(self):
        project, xml = self.project(load('dual.intent.json'), 'dual', name='dual')
        empty = dict(footprints=[], tracks=[], zones=[], edge_bbox_nm=[0, 0, 10000000, 10000000])
        captured = {m['path']: (PROJECT / m['path']).read_bytes()
                    for lib in self.manifest['libraries'] for m in lib['members']}
        plan = fa.build_plan(xml.read_bytes(), '0' * 64, empty, self.manifest, captured)
        self.assertEqual(plan['execution'], 'ok', plan['findings'])
        added = plan['add'][0]
        namespace = uuid.UUID(load('dual.layout.json')['namespace'])
        units = ['/' + str(uuid.uuid5(namespace, json.dumps(['symbol', 'dual', u], separators=(',', ':'))))
                 for u in (1, 2)]
        self.assertIn(added['path'], units)
        def footprint(path, ref, fp_uuid):
            return dict(uuid=fp_uuid, path=path, reference=ref, value='DUAL', fpid='OwnedFP:DUAL_6PAD',
                        position_nm=[0, 0], orientation_deg=0.0, layer='F.Cu', fields={},
                        pads=[dict(number=str(n), net='', position_nm=[0, n]) for n in range(1, 7)])
        # Either unit UUID binds; the stale reference does not matter.
        for path in units:
            facts = dict(empty, footprints=[footprint(path, 'X99', 'f-1')])
            result = fa.build_plan(xml.read_bytes(), '0' * 64, facts, self.manifest, captured)
            self.assertEqual((result['counts']['add'], result['counts']['retain'], result['counts']['update']),
                             (0, 1, 1))
        both = dict(empty, footprints=[footprint(units[0], 'U1', 'f-1'), footprint(units[1], 'U1', 'f-2')])
        result = fa.build_plan(xml.read_bytes(), '0' * 64, both, self.manifest, captured)
        self.assertEqual([f['code'] for f in result['findings']], ['multiunit_binding_ambiguous'])
        # A footprint carrying the right reference but no native path is never adopted.
        orphan = dict(empty, footprints=[footprint('', 'U1', 'f-1')])
        result = fa.build_plan(xml.read_bytes(), '0' * 64, orphan, self.manifest, captured)
        self.assertEqual((result['counts']['add'], result['counts']['board_only']), (1, 1))

    def test_annotated_board_passes_public_review_and_verify_rejects_tampering(self):
        from agent_ee.cli import run_review, verify
        from agent_ee.storage import write_json
        def rename(i):
            next(n for n in i['nets'] if n['id'] == 'gnd')['name'] = 'AGND'
            next(c for c in i['components'] if c['id'] == 'gnd')['value'] = 'AGND'
        project, xml = self.project(self.intent(rename), 'review')
        self.annotate(project, xml, 'review')
        template = self.tmp / 'review' / 'template'
        template.mkdir()
        (template / 'sym-lib-table').write_text('(sym_lib_table\n  (version 7)\n)\n')
        (template / 'fp-lib-table').write_text('(fp_lib_table\n  (version 7)\n)\n')
        scope = {'schema': 'agent-ee.scope/1', 'name': 'Forward-annotated regulator',
                 'scope': 'Native checks of an owned forward-annotated regulator board.',
                 'limitations': ['Owned fixture; no electrical performance is reviewed.'],
                 'inputs': [{'path': 'project', 'at': 'project'}, {'path': 'template', 'at': 'stock/template'}],
                 'schematic': 'project/regulator.kicad_sch', 'board': 'project/regulator.kicad_pcb',
                 'requirements': [], 'checks': [{'id': 'erc', 'kind': 'erc'}, {'id': 'drc', 'kind': 'drc'},
                                                {'id': 'netlist', 'kind': 'netlist'}]}
        write_json(self.tmp / 'review' / 'scope.json', scope)
        run = self.tmp / 'review' / 'run'
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_review(self.tmp / 'review' / 'scope.json', run, 600, checks_only=True), 1)
        result = verify(run)
        outcomes = {c['id']: (c['execution'], c['outcome']) for c in result['checks']}
        self.assertEqual(outcomes, {'erc': ('ok', 'pass'), 'drc': ('ok', 'pass'), 'netlist': ('ok', 'info')})
        report = run / 'work' / 'drc' / 'drc.json'
        original = report.read_bytes()
        report.write_bytes(original + b' ')
        with self.assertRaisesRegex(ValueError, 'evidence changed'):
            verify(run)
        report.write_bytes(original)
        stored_bytes = (run / 'result.json').read_bytes()
        stored = json.loads(stored_bytes)
        self.assertIn('kicad-cli', stored['runtime_files'])
        tampered = dict(stored, runtime_files=dict(stored['runtime_files'], **{'kicad-cli': '0' * 64}))
        write_json(run / 'result.json', tampered)
        with self.assertRaisesRegex(ValueError, 'tool runtime changed'):
            verify(run)
        (run / 'result.json').write_bytes(stored_bytes)
        verify(run)
        board = project / 'regulator.kicad_pcb'
        board.write_bytes(board.read_bytes() + b'\n')
        with self.assertRaises(ValueError):
            verify(run)


    def test_board_exclusion_is_judged_on_parsed_native_flags(self):
        """Exclusion is a parsed XML property, whatever its byte spelling."""
        import xml.etree.ElementTree as ET
        empty = dict(footprints=[], tracks=[], zones=[], edge_bbox_nm=[0, 0, 10000000, 10000000])
        captured = {m['path']: (PROJECT / m['path']).read_bytes()
                    for lib in self.manifest['libraries'] for m in lib['members']}

        def flagged(*flags):
            def edit(data):
                # Only the C1 instance (the one OwnedGen:CAP) changes; library symbols precede it.
                ix = data.index(b'(lib_id "OwnedGen:CAP")')
                tail = data[ix:]
                for old, new in flags:
                    self.assertIn(old, tail)
                    tail = tail.replace(old, new, 1)
                return data[:ix] + tail
            return edit

        def plan(data):
            return fa.build_plan(data, '0' * 64, empty, self.manifest, captured)

        def flags(data, ref='C1'):
            comp = next(c for c in ET.fromstring(data).iter('comp') if c.get('ref') == ref)
            return sorted(p.get('name') for p in comp.iter('property'))

        _, clean = self.project(self.intent(), 'flags-clean')
        clean = clean.read_bytes()
        self.assertEqual(plan(clean)['execution'], 'ok', plan(clean)['findings'])
        _, excluded = self.project(self.intent(), 'flags-board', edit=flagged((b'(on_board yes)', b'(on_board no)')))
        excluded = excluded.read_bytes()
        # Native control: KiCad 10.0.7 writes exclusion as a valueless C1 property.
        self.assertIn(b'<property name="exclude_from_board"/>', excluded)
        self.assertEqual(flags(excluded), ['Sheetfile', 'Sheetname', 'exclude_from_board'])
        self.assertEqual(flags(clean), ['Sheetfile', 'Sheetname'])
        canonical = b'<property name="exclude_from_board"/>'
        spellings = {
            'native': excluded,
            'single_quotes': excluded.replace(canonical, b"<property name='exclude_from_board'/>"),
            'newline_before_name': excluded.replace(canonical, b'<property\n name="exclude_from_board"/>'),
            'spaces_around_equals': excluded.replace(canonical, b'<property name = "exclude_from_board" />'),
            'character_reference': excluded.replace(canonical, b'<property name="exclude&#95;from_board"/>'),
            'explicit_end_tag': excluded.replace(canonical, b'<property name="exclude_from_board"></property>'),
        }
        for label, data in spellings.items():
            with self.subTest(spelling=label):
                if label != 'native':
                    self.assertNotIn(canonical, data)
                # The shared reader accepts each spelling as the same document.
                self.assertEqual(flags(data), flags(excluded))
                with tempfile.NamedTemporaryFile(dir=self.tmp, suffix='.xml') as stream:
                    stream.write(data)
                    stream.flush()
                    self.assertEqual(read_native_inventory(stream.name)['components'].keys(),
                                     read_native_inventory_bytes(clean, self.tmp).keys())
                result = plan(data)
                self.assertEqual((result['execution'], result['findings']),
                                 ('failed', [dict(code='board_exclusion_unsupported', location='/components/C1',
                                                  message='native export excludes this symbol from the board')]))
        # A field merely mentioning the flag is not a flag.
        mention = clean.replace(b'<field name="Description">', b'<field name="Note">'
                                b'&lt;property name="exclude_from_board"/&gt;</field><field name="Description">', 1)
        self.assertNotEqual(mention, clean)
        self.assertEqual(plan(mention)['execution'], 'ok', plan(mention)['findings'])
        # Do-not-populate, judged by the shared reader, stays a separate refusal.
        _, dnp = self.project(self.intent(), 'flags-dnp', edit=flagged((b'(dnp no)', b'(dnp yes)')))
        self.assertEqual([f['code'] for f in plan(dnp.read_bytes())['findings']], ['dnp_unsupported'])

    def test_hierarchical_native_paths_bind_by_sheet_and_unit(self):
        """A child-sheet symbol binds only under its full native sheet path."""
        project, xml = self.project(load('two_level.intent.json'), 'hierarchy', name='two_level')
        captured = {m['path']: (PROJECT / m['path']).read_bytes()
                    for lib in self.manifest['libraries'] for m in lib['members']}
        empty = dict(footprints=[], tracks=[], zones=[], edge_bbox_nm=[0, 0, 10000000, 10000000])
        namespace = uuid.UUID(load('two_level.layout.json')['namespace'])
        ident = lambda *parts: str(uuid.uuid5(namespace, json.dumps(list(parts), separators=(',', ':'))))
        sheet = '/' + ident('sheet', 'two_level.kicad_sch', 'filter') + '/'
        local = ident('symbol', 'filter.kicad_sch', 'c_in', 1)
        plan = fa.build_plan(xml.read_bytes(), '0' * 64, empty, self.manifest, captured)
        self.assertEqual(plan['execution'], 'ok', plan['findings'])
        native_work = self.tmp / 'hierarchy-native-netlist'
        run_native(project, native_work, 'kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadsexpr',
                   '-o', '/work/design.net', '/candidate/two_level.kicad_sch')
        native = parse_sexpr((native_work / 'design.net').read_bytes())
        self.assertEqual({p['net'] for p in plan['pads']},
                         {children(n, 'name')[0][1] for n in children(children(native, 'nets')[0], 'net')})
        self.assertEqual({a['reference']: a['path'] for a in plan['add']}, {
            'J1': '/' + ident('symbol', 'two_level.kicad_sch', 'j_in', 1),
            'C1': '/' + ident('symbol', 'two_level.kicad_sch', 'c_bulk', 1),
            'C2': sheet + local, 'C3': sheet + ident('symbol', 'filter.kicad_sch', 'c_gnd', 1),
            'C4': sheet + ident('symbol', 'filter.kicad_sch', 'c_local', 1)})
        def footprint(path):
            return dict(uuid='f-1', path=path, reference='C2', value='100n', fpid='OwnedFP:CAP_2PAD',
                        position_nm=[0, 0], orientation_deg=0.0, layer='F.Cu', fields={},
                        pads=[dict(number=str(n), net='', position_nm=[0, n]) for n in (1, 2)])
        bound = fa.build_plan(xml.read_bytes(), '0' * 64, dict(empty, footprints=[footprint(sheet + local)]),
                              self.manifest, captured)
        self.assertEqual((bound['counts']['retain'], bound['counts']['add'], bound['counts']['remove']), (1, 4, 0))
        # The same local UUID under the root path is a different occurrence: removed, never adopted.
        stray = fa.build_plan(xml.read_bytes(), '0' * 64, dict(empty, footprints=[footprint('/' + local)]),
                              self.manifest, captured)
        self.assertEqual((stray['counts']['retain'], stray['counts']['add'], stray['counts']['remove']), (0, 5, 1))

    def test_publication_never_follows_planted_paths(self):
        """Every output, backup and staging path is created privately; links are never followed."""
        project, xml = self.project(self.intent(), 'publish')
        board = project / 'regulator.kicad_pcb'
        source = board.read_bytes()
        record = fa.plan(board, xml, self.manifest, PROJECT, self.tmp / 'publish-plan')
        self.assertEqual(record['execution'], 'ok', record['findings'])
        counter = iter(range(100))

        def apply(out, backup_dir=None):
            return fa.apply(record, board, xml, self.manifest, PROJECT, out, self.tmp / f'publish-{next(counter)}',
                            backup_dir=backup_dir)

        def listing(directory):
            """Every entry without following links: name, link flag, target, bytes or subtree."""
            def entry(p):
                if p.is_symlink():
                    return os.readlink(p)
                return tuple(listing(p)) if p.is_dir() else p.read_bytes()
            return sorted((p.name, p.is_symlink(), entry(p)) for p in Path(directory).iterdir())

        def codes(result):
            return result['execution'], [f['code'] for f in result['findings']]

        sentinel = self.tmp / 'owned-sentinel.txt'
        sentinel.write_bytes(b'owned review sentinel')
        # The former fixed staging name, as a link and as a regular file: both untouched.
        for kind in ('symlink', 'regular'):
            with self.subTest(planted_staging=kind):
                directory = self.tmp / ('staging-' + kind)
                directory.mkdir()
                out = directory / 'published.kicad_pcb'
                planted = directory / '.published.kicad_pcb.annotation'
                if kind == 'symlink':
                    planted.symlink_to(sentinel)
                else:
                    planted.write_bytes(b'owned planted file')
                before = listing(directory)
                result = apply(out)
                self.assertEqual(codes(result), ('ok', []))
                self.assertEqual(sentinel.read_bytes(), b'owned review sentinel')
                self.assertFalse(out.is_symlink())
                self.assertEqual(out.read_bytes(), source)
                self.assertEqual(result['output'], dict(path=str(out), sha256=sha(board)))
                self.assertEqual(listing(directory), sorted(before + [(out.name, False, source)]))
        # An existing output entry is never replaced, whether a live or a dangling link.
        for kind, target in (('live', sentinel), ('dangling', self.tmp / 'absent-target')):
            with self.subTest(output_link=kind):
                directory = self.tmp / ('output-' + kind)
                directory.mkdir()
                out = directory / 'published.kicad_pcb'
                out.symlink_to(target)
                before = listing(directory)
                self.assertEqual(codes(apply(out)), ('failed', ['output_exists']))
                self.assertEqual(listing(directory), before)
                self.assertFalse((self.tmp / 'absent-target').exists())
                self.assertEqual(sentinel.read_bytes(), b'owned review sentinel')
        # No path component may be a link: output and backup ancestors are walked without following.
        real = self.tmp / 'real-directory'
        real.mkdir()
        (self.tmp / 'linked-directory').symlink_to(real)
        with self.subTest(ancestor='output'):
            self.assertEqual(codes(apply(self.tmp / 'linked-directory' / 'out.kicad_pcb')),
                             ('failed', ['unsafe_path']))
        with self.subTest(ancestor='backup'):
            self.assertEqual(codes(apply(board, self.tmp / 'linked-directory' / 'nested' / 'backup')),
                             ('failed', ['unsafe_path']))
        with self.subTest(parent_reference='output'):
            self.assertEqual(codes(apply(self.tmp / 'linked-directory' / '..' / 'via-parent.kicad_pcb')),
                             ('failed', ['unsafe_path']))
            self.assertFalse((self.tmp / 'via-parent.kicad_pcb').exists())
        self.assertEqual(listing(real), [])
        # An in-place target reached through a link is refused, not replaced by a file.
        with self.subTest(in_place='linked board'):
            linked = project / 'linked.kicad_pcb'
            linked.symlink_to(board)
            result = fa.apply(record, linked, xml, self.manifest, PROJECT, linked, self.tmp / 'publish-linked',
                              backup_dir=self.tmp / 'linked-backup')
            self.assertEqual(codes(result), ('failed', ['unsafe_path']))
            self.assertTrue(linked.is_symlink())
            self.assertFalse((self.tmp / 'linked-backup').exists())
            linked.unlink()
        # The content-addressed backup name: a planted link (even to identical bytes) or wrong bytes refuse.
        name = record['inputs']['board_sha256'] + '.kicad_pcb'
        identical = self.tmp / 'identical-copy.kicad_pcb'
        identical.write_bytes(source)
        for kind in ('dangling', 'identical-link', 'wrong-bytes'):
            with self.subTest(backup=kind):
                backup_dir = self.tmp / ('backup-' + kind)
                backup_dir.mkdir()
                if kind == 'dangling':
                    (backup_dir / name).symlink_to(self.tmp / 'absent-backup')
                elif kind == 'identical-link':
                    (backup_dir / name).symlink_to(identical)
                else:
                    (backup_dir / name).write_bytes(b'not the source')
                before = listing(backup_dir)
                self.assertEqual(codes(apply(board, backup_dir)), ('failed', ['backup_location']))
                self.assertEqual(listing(backup_dir), before)
                self.assertFalse((self.tmp / 'absent-backup').exists())
                self.assertEqual(board.read_bytes(), source)
        # Success in place: private backup with the source bytes, board replaced, nothing else written.
        backup_dir = self.tmp / 'backup-new' / 'nested'
        before = listing(project)
        result = apply(board, backup_dir)
        self.assertEqual(codes(result), ('ok', []))
        self.assertEqual(listing(backup_dir), [(name, False, source)])
        self.assertEqual(listing(project), before)
        self.assertFalse(board.is_symlink())
        # An existing regular backup with the source bytes is reused, not rewritten.
        self.assertEqual(codes(apply(board, backup_dir)), ('ok', []))
        self.assertEqual(listing(backup_dir), [(name, False, source)])

if __name__ == '__main__':
    unittest.main()
