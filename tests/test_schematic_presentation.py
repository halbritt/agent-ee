"""Functional schematic presentation (AGENTEE-222): grouping, local wires, parity.

The owned ``presentation`` fixture is a connector, a regulator with two input
capacitors, an output capacitor, a series capacitor feeding a filter capacitor
and a second connector, plus the ground symbol and power flags its intent
declares. Expectations are structural (read back with the shared reader) and
native (kicad-cli 10.0.7 ERC, XML netlist, forced re-save, AGENTEE-41 parity).
The default ``rows`` presentation must stay byte-identical to the emitter before
AGENTEE-222; those hashes were taken from main 9cb4f12.
"""
import hashlib
import json
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest import mock
from pathlib import Path

from agent_ee import schematic_presentation as presentation
from agent_ee.connectivity_ir import resolve_intent
from agent_ee.schematic_emitter import EmissionError, _geometry, _plan, transformed_pin
from agent_ee.symbol_library import ParsedLibraries, children, nanometres, parse_sexpr
from test_schematic_emitter import PROJECT, captures, emit, load, native_netlist, native_project, run_native, tables
from toolchain import require_toolchain

REPO = Path(__file__).resolve().parents[1]
# Rows output of main 9cb4f12 (before AGENTEE-222) for the owned fixtures.
ROWS_SHA256 = {
    'regulator': '7c8abe45e77d3359368b6b0a298a93e84fb947ef4195e5b53777fd46333f689b',
    'transforms': '367a5a0a0a5b8fa8820012fef2bbc7df8b572ebedbc95ed75894dafe679f68af',
    'dual': '3a5097152d92167bb839a1c0cf2e0923783f22a72462cb510b2d3caa83f3a84a',
}
TITLES = {'Input connector', 'Regulator and output filter', 'J2 CONN3'}


def functional(intent=None, layout=None, **kwargs):
    result, files = emit('presentation', intent=intent, layout=layout, **kwargs)
    assert result['execution'] == 'ok', result['findings']
    return files


def rows_layout():
    layout = load('presentation.layout.json')
    del layout['presentation'], layout['groups']
    return layout


def at(node):
    found = children(node, 'at')[0]
    return nanometres(found[1]), nanometres(found[2])


def sheet(data):
    root = parse_sexpr(data)
    symbols = {}
    for node in children(root, 'symbol'):
        ref = next(p[2] for p in children(node, 'property') if p[1] == 'Reference')
        a = children(node, 'at')[0]
        symbols[ref] = dict(uuid=children(node, 'uuid')[0][1], at=at(node), rotation=int(a[3]),
                            pins={p[1]: children(p, 'uuid')[0][1] for p in children(node, 'pin')})
    labels = [(n[0], n[1], at(n)) for n in children(root, 'label') + children(root, 'global_label')]
    wires = [tuple((nanometres(xy[1]), nanometres(xy[2])) for xy in children(children(n, 'pts')[0], 'xy'))
             for n in children(root, 'wire')]
    junctions = [at(n) for n in children(root, 'junction')]
    frames = [(tuple(map(nanometres, children(n, 'start')[0][1:])), tuple(map(nanometres, children(n, 'end')[0][1:])))
              for n in children(root, 'rectangle')]
    titles = {n[1] for n in children(root, 'text')}
    return root, symbols, labels, wires, junctions, frames, titles


def endpoints(intent, data):
    """Every connected pin endpoint, from the fresh resolution and the emitted anchors."""
    resolved = resolve_intent(intent, captures(intent))
    _, symbols, *_ = sheet(data)
    points = {}
    for comp in resolved['components']:
        placed = symbols[comp['reference']]
        root = parse_sexpr(data)
        node = next(n for n in children(root, 'symbol')
                    if next(p[2] for p in children(n, 'property') if p[1] == 'Reference') == comp['reference'])
        mirror = children(node, 'mirror')
        for pin in comp['pins']:
            x, y, _ = transformed_pin(pin, placed['at'], placed['rotation'], mirror[0][1] if mirror else None)
            points[comp['reference'], pin['number']] = (x, y)
    return points


class PresentationRulesTests(unittest.TestCase):
    def test_rows_presentation_bytes_are_unchanged(self):
        for name, kwargs in (('regulator', tables()), ('transforms', {}), ('dual', {})):
            with self.subTest(name=name):
                result, files = emit(name, **kwargs)
                self.assertEqual(hashlib.sha256(files[name + '.kicad_sch']).hexdigest(), ROWS_SHA256[name])
                layout = dict(load(name + '.layout.json'), presentation='rows')
                self.assertEqual(emit(name, layout=layout, **kwargs)[1], files)

    def test_deterministic_and_independent_of_input_order(self):
        first = functional()
        self.assertEqual(functional(), first)
        intent = load('presentation.intent.json')
        shuffle = random.Random(222)
        shuffle.shuffle(intent['components'])
        shuffle.shuffle(intent['nets'])
        for net in intent['nets']:
            shuffle.shuffle(net['terminals'])
        self.assertEqual(functional(intent=intent), first)

    def test_identities_match_the_rows_presentation(self):
        rows = emit('presentation', layout=rows_layout())[1]['presentation.kicad_sch']
        wired = functional()['presentation.kicad_sch']
        _, before, *_ = sheet(rows)
        _, after, *_ = sheet(wired)
        self.assertEqual({r: (s['uuid'], s['pins']) for r, s in before.items()},
                         {r: (s['uuid'], s['pins']) for r, s in after.items()})
        self.assertEqual(children(parse_sexpr(rows), 'lib_symbols'), children(parse_sexpr(wired), 'lib_symbols'))
        self.assertEqual(children(parse_sexpr(rows), 'no_connect')[0][2], children(parse_sexpr(wired), 'no_connect')[0][2])

    def test_local_parts_are_wired_and_every_net_keeps_its_name(self):
        intent = load('presentation.intent.json')
        data = functional()['presentation.kicad_sch']
        _, symbols, labels, wires, junctions, _, _ = sheet(data)
        points = endpoints(intent, data)
        names = {}
        for kind, name, point in labels:
            names.setdefault(name, []).append(point)
        # One label per net name survives at least; wired attach pins drop theirs.
        self.assertEqual({k: len(v) for k, v in names.items()},
                         {'VIN': 3, 'VOUT': 1, 'OUT_F': 2, 'SENSE': 2, 'GND': 9})
        self.assertEqual(len(wires), 5)
        for a, b in wires:
            self.assertTrue(a[0] == b[0] or a[1] == b[1], 'wires are axis-aligned')
        ends = [p for w in wires for p in w]
        pins = set(points.values())
        for point in ends:
            self.assertTrue(point in pins or point in junctions or ends.count(point) > 1)
        for point in junctions:  # A junction marks a comb point: two wires and a pin.
            self.assertEqual(ends.count(point), 2)
            self.assertIn(point, pins)
        # C1 and C2 comb on one VIN pin; C3 hangs from VOUT; C4 sits in line; C5 hangs from OUT_F.
        for ref in ('C1', 'C2', 'C3', 'C4', 'C5'):
            attach = [n for n in '12' if points[ref, n] in ends]
            self.assertEqual(len(attach), 1, ref)
            self.assertNotIn(points[ref, attach[0]], [p for _, _, p in labels], ref)
        self.assertIn(symbols['C4']['rotation'], (90, 270))
        self.assertEqual(points['C4', '1'][1], points['U1', '3'][1])
        self.assertEqual(points['C4', '2'][1], points['U1', '3'][1])
        self.assertEqual(points['C3', '1'][1], points['U1', '3'][1])
        self.assertIn(symbols['C5']['rotation'], (0, 180))

    def test_groups_are_framed_titled_and_contain_their_parts(self):
        data = functional()['presentation.kicad_sch']
        _, symbols, _, _, _, frames, titles = sheet(data)
        self.assertEqual(titles, TITLES)
        self.assertEqual(len(frames), 3)

        def frame_of(ref):
            x, y = symbols[ref]['at']
            inside = [f for f in frames if f[0][0] < x < f[1][0] and f[0][1] < y < f[1][1]]
            self.assertEqual(len(inside), 1, ref)
            return inside[0]
        self.assertEqual({frame_of(r) for r in ('J1', '#FLG01', '#FLG02', '#PWR01')}, {frame_of('J1')})
        self.assertEqual({frame_of(r) for r in ('U1', 'C3', 'C4')}, {frame_of('U1')})
        self.assertEqual(frame_of('C5'), frame_of('J2'))
        self.assertEqual(len({frame_of('J1'), frame_of('U1'), frame_of('J2')}), 3)
        for a in frames:
            for b in frames:
                if a != b:
                    self.assertFalse(a[0][0] < b[1][0] and b[0][0] < a[1][0] and
                                     a[0][1] < b[1][1] and b[0][1] < a[1][1], 'frames overlap')

    def test_part_with_no_anchor_net_is_unassigned_not_guessed(self):
        intent = load('presentation.intent.json')
        for cid, ref in (('c_island', 'C9'), ('c_island2', 'C10')):
            intent['components'].append(dict(id=cid, reference=ref, lib_id='OwnedGen:CAP', value='1n',
                                             footprint='OwnedFP:CAP_2PAD', units=[1]))
        intent['nets'] += [dict(id='iso_' + n, name='ISO_' + n.upper(),
                                terminals=[dict(component=c, pin=dict(number=p)) for c in ('c_island', 'c_island2')])
                           for n, p in (('a', '1'), ('b', '2'))]
        _, symbols, labels, *_, titles = sheet(functional(intent=intent)['presentation.kicad_sch'])
        self.assertEqual(titles, TITLES | {'Unassigned parts'})
        self.assertEqual(sorted(n for k, n, _ in labels if n.startswith('ISO_')), ['ISO_A'] * 2 + ['ISO_B'] * 2)

    def test_explicit_transform_is_kept_and_the_part_stays_labelled(self):
        intent, layout = load('presentation.intent.json'), load('presentation.layout.json')
        layout['transforms'] = {'c_out': {'rotation': 90, 'mirror': None}}
        data = functional(layout=layout)['presentation.kicad_sch']
        _, symbols, labels, wires, _, frames, titles = sheet(data)
        points = endpoints(intent, data)
        self.assertEqual(symbols['C3']['rotation'], 90)
        self.assertEqual((len(wires), len(frames), titles), (4, 3, TITLES))
        ends = {p for w in wires for p in w}
        for pin in '12':
            self.assertNotIn(points['C3', pin], ends)
            self.assertIn(points['C3', pin], [p for _, _, p in labels])

    def test_listed_divider_leg_is_wired_after_its_series_part(self):
        # AGENTEE-241: listing the leg (C5, OUT_F to GND) before the series
        # part (C4) whose far pin it hangs from must not leave it loose.
        intent, layout = load('presentation.intent.json'), load('presentation.layout.json')
        layout['groups'][1]['components'] = ['reg', 'c_filter', 'c_series']
        data = functional(layout=layout)['presentation.kicad_sch']
        _, symbols, labels, wires, *_ = sheet(data)
        points = endpoints(intent, data)
        ends = {p for w in wires for p in w}
        self.assertIn(points['C4', '1'], ends)
        self.assertIn(points['C5', '1'], ends)
        self.assertNotIn(points['C5', '1'], [p for _, _, p in labels])
        self.assertEqual(functional(layout=layout)['presentation.kicad_sch'], data)

    def fanned(self):
        layout = load('presentation.layout.json')
        layout['transforms'] = {'j_in': {'rotation': 90, 'mirror': None}}  # J1's three pins on one edge
        return layout

    def test_dense_edge_pins_fan_out_to_staggered_horizontal_labels(self):
        intent = load('presentation.intent.json')
        data = functional(layout=self.fanned())['presentation.kicad_sch']
        root, symbols, labels, wires, *_ = sheet(data)
        self.assertEqual(symbols['J1']['rotation'], 90)
        points = endpoints(intent, data)
        angles = {(n[1], at(n)): int(children(n, 'at')[0][3])
                  for n in children(root, 'label') + children(root, 'global_label')}
        # J1 pin 1 carries the C1/C2 comb: it keeps its wire and its label at
        # the pin, and its free neighbours fan away from it to the right.
        self.assertIn(('VIN', points['J1', '1']), angles)
        self.assertTrue(any(points['J1', '1'] in w for w in wires))
        fan = []
        for pin, name in (('2', 'GND'), ('3', 'SENSE')):
            start = points['J1', pin]
            wire = [w for w in wires if start in w]
            self.assertEqual(len(wire), 1, pin)
            end = wire[0][1] if wire[0][0] == start else wire[0][0]
            self.assertEqual(end[0], start[0], 'fan wires leave the edge straight')
            self.assertNotIn((name, start), angles, 'the label moved off the pin')
            fan.append((start[0], angles[name, end], end[1] - start[1]))
        # Horizontal labels, staggered one grid apart, the outermost pin nearest the edge.
        self.assertEqual([f[1:] for f in sorted(fan)], [(0, 5080000), (0, 2540000)])
        self.assertEqual(functional(layout=self.fanned())['presentation.kicad_sch'], data)

    def test_shunt_part_continues_an_anchor_pin_in_line_only_as_the_fallback(self):
        _, _, units = self.units()
        reg, cap = (next(u for u in units if u['comp']['reference'] == r) for r in ('U1', 'C3'))
        for last, along in ((False, False), (True, True)):
            cluster = presentation._Cluster(reg, 0, lambda net: False)
            target = next(t for t in cluster.targets if t['key'][2] == '3')
            placed = cluster.attach(cap, target, '1', '2', False, last)
            self.assertIsNotNone(placed)
            (_, anchor), = [p for p in cluster.parts if p[0] is placed]
            ends = [(anchor[0] + x, anchor[1] + y) for p, _, x, y, _ in placed['labels']]
            # VOUT leaves U1 to the right: in line keeps both C3 pins on the pin's row.
            self.assertEqual(all(y == target['point'][1] for _, y in ends), along, last)
            self.assertEqual(target['closed'], along)

    def test_stacked_fields_sit_on_one_side_reference_first(self):
        _, _, units = self.units()
        cap = next(u for u in units if u['comp']['reference'] == 'C3')
        geo = _geometry(cap, 90, None)
        top, bottom = geo['shapes'][0][1], geo['shapes'][0][3]
        for side in ('above', 'below'):
            fields = presentation._autoplace(cap, geo, True, side)['fields']
            self.assertLess(fields['Reference'][1], fields['Value'][1], side)
            self.assertEqual(fields['Reference'][0], fields['Value'][0])
            for _, y in fields.values():
                self.assertTrue(y < top if side == 'above' else y > bottom, side)

    def test_frames_take_their_tightest_aspect(self):
        def area(data):
            _, *_, frames, _ = sheet(data)
            return sum((b[0] - a[0]) * (b[1] - a[1]) for a, b in frames)
        tight = area(functional()['presentation.kicad_sch'])
        for aspect in presentation.ASPECTS:
            with mock.patch.object(presentation, 'ASPECTS', (aspect,)):
                self.assertLessEqual(tight, area(functional()['presentation.kicad_sch']), aspect)

    def test_explicit_and_automatic_groups_have_distinct_drawing_identities(self):
        # Authored IDs may match an unlisted anchor ID or the fallback name.
        layout = load('presentation.layout.json')
        layout['groups'][0]['id'] = 'j_out'
        layout['groups'][1]['id'] = 'unassigned'
        intent = load('presentation.intent.json')
        for cid, ref in (('island_a', 'C9'), ('island_b', 'C10')):
            intent['components'].append(dict(id=cid, reference=ref, lib_id='OwnedGen:CAP', value='1n',
                                             footprint='OwnedFP:CAP_2PAD', units=[1]))
        intent['nets'] += [dict(id='island_' + p, name='ISLAND_' + p,
                                terminals=[dict(component=c, pin=dict(number=p))
                                           for c in ('island_a', 'island_b')]) for p in ('1', '2')]
        root, *_, frames, titles = sheet(functional(intent=intent, layout=layout)['presentation.kicad_sch'])
        self.assertEqual(len(frames), 4)
        self.assertEqual(titles, TITLES | {'Unassigned parts'})
        drawing_ids = [children(n, 'uuid')[0][1] for kind in ('rectangle', 'text') for n in children(root, kind)]
        self.assertEqual(len(drawing_ids), len(set(drawing_ids)))

    def test_layout_contract_for_presentation(self):
        def refused(layout, name='presentation'):
            result, files = emit(name, layout=layout)
            self.assertIsNone(files)
            self.assertEqual(result['findings'][-1]['code'], 'layout_schema', result['findings'])
        base = load('presentation.layout.json')
        for edit in (lambda l: l.update(presentation='pretty'),
                     lambda l: l.update(presentation='rows'),  # groups require functional
                     lambda l: l['groups'].append(dict(id='input', title='Again', components=['j_out'])),
                     lambda l: l['groups'].append(dict(id='more', title='More', components=['reg'])),
                     lambda l: l['groups'].append(dict(id='more', title='More', components=['missing'])),
                     lambda l: l['groups'].append(dict(id='more', title='', components=['j_out'])),
                     lambda l: l['groups'].append(dict(id='more', title='${X}', components=['j_out'])),
                     lambda l: l['groups'].append(dict(id='more', title='More', components=[])),
                     lambda l: l['groups'].append(dict(id='bad id', title='More', components=['j_out'])),
                     lambda l: l['groups'].append(dict(id='more', title='More', components=['j_out'], extra=1))):
            layout = json.loads(json.dumps(base))
            edit(layout)
            with self.subTest(layout=layout):
                refused(layout)
        hierarchy = dict(load('two_level.layout.json'), presentation='functional')
        refused(hierarchy, 'two_level')

    def test_stable_regeneration_refuses_the_functional_presentation(self):
        from agent_ee.schematic_regenerate import regenerate_schematic
        intent, layout = load('presentation.intent.json'), load('presentation.layout.json')
        previous = functional()['presentation.kicad_sch']
        result, files = regenerate_schematic(previous, intent, intent, captures(intent), layout)
        self.assertIsNone(files)
        self.assertIn('rows presentation only', json.dumps(result['findings']))

    def units(self):
        intent = load('presentation.intent.json')
        cache, captured = ParsedLibraries(), captures(intent)
        resolved = resolve_intent(intent, captured, _library_cache=cache)
        return resolved, *_plan(resolved, captured, intent['libraries'], {}, cache, {})[::-1]

    def plan(self):
        resolved, global_nets, units = self.units()
        layout = load('presentation.layout.json')
        paper, placed, omit, drawn = presentation.arrange(units, resolved, global_nets, layout['groups'],
                                                          uuid.UUID(layout['namespace']))
        wires = [tuple((nanometres(xy[1]), nanometres(xy[2])) for xy in n[1][1:]) for n in drawn if n[0] == 'wire']
        return resolved, placed, omit, wires

    def test_self_check_refuses_drawn_shorts_splits_and_crossings(self):
        resolved, placed, omit, wires = self.plan()
        presentation.verify(placed, omit, wires, resolved)
        pin = {}
        for item, anchor in placed:
            for p, net, x, y, _ in item['labels']:
                pin[item['comp']['reference'], p['number']] = (anchor[0] + x, anchor[1] + y)
        cases = {
            'merges': wires + [(pin['C3', '1'], (pin['C3', '1'][0], pin['C3', '2'][1]))],  # VOUT onto GND
            'splits': [w for w in wires if pin['C3', '1'] not in w],  # C3's attach pin loses its only path
            'passes through': wires + [((pin['U1', '1'][0] - 2540000, pin['U1', '1'][1]),
                                        (pin['U1', '1'][0] + 2540000, pin['U1', '1'][1]))],
            'no-connect': wires + [(pin['U1', '4'], (pin['U1', '4'][0], pin['U1', '4'][1] - 2540000))],
        }
        for message, drawn in cases.items():
            with self.subTest(case=message):
                with self.assertRaises(EmissionError) as caught:
                    presentation.verify(placed, omit, drawn, resolved)
                self.assertEqual(caught.exception.code, 'presentation_conflict')
                self.assertIn(message, str(caught.exception))

    def test_cli_module_run_writes_the_api_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            out, receipt = Path(tmp) / 'out', Path(tmp) / 'receipt.json'
            result = subprocess.run(
                [sys.executable, '-m', 'agent_ee.schematic_emitter',
                 str(REPO / 'fixtures/generation/presentation.intent.json'),
                 '--layout', str(REPO / 'fixtures/generation/presentation.layout.json'),
                 '--captures', str(PROJECT), '--out', str(out), '--receipt', str(receipt)],
                capture_output=True, text=True, env={'PYTHONPATH': str(REPO / 'src'), 'PATH': '/usr/bin:/bin'})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(receipt.read_text())['execution'], 'ok')
            files = functional()
            self.assertEqual({p.name: p.read_bytes() for p in out.iterdir()}, files)


class PresentationNativeTests(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)
        self.tmp = Path(tempfile.mkdtemp(prefix='agent-ee-presentation-native-'))
        self.addCleanup(shutil.rmtree, self.tmp)

    def native(self, files, label):
        project = native_project(files, self.tmp / label)
        work = self.tmp / label / 'work'
        sch = '/candidate/presentation.kicad_sch'
        run_native(project, work / 'erc', 'kicad-cli', 'sch', 'erc', '--format', 'json', '--severity-all',
                   '--units', 'mm', '-o', '/work/erc.json', sch)
        run_native(project, work / 'netlist', 'kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml',
                   '-o', '/work/design.xml', sch)
        erc = json.loads((work / 'erc/erc.json').read_text())
        self.assertEqual(erc['kicad_version'], '10.0.7')
        return project, work, erc

    def test_erc_netlist_parity_and_native_resave(self):
        from agent_ee.native_netlist import read_native_inventory
        from agent_ee.netlist_parity import compare, resolved_inventory, validate_policy
        files = functional(**tables())
        project, work, erc = self.native(files, 'functional')
        self.assertEqual([v for s in erc['sheets'] for v in s['violations']], [])
        rows_files = emit('presentation', layout=dict(rows_layout()), **tables())[1]
        _, rows_work, rows_erc = self.native(rows_files, 'rows')
        self.assertEqual([v for s in rows_erc['sheets'] for v in s['violations']], [])
        components, nets, singles = native_netlist(work / 'netlist/design.xml')
        self.assertEqual((components, nets, singles), native_netlist(rows_work / 'netlist/design.xml'))
        self.assertEqual(nets, {'/VIN': [['C1', '1'], ['C2', '1'], ['J1', '1'], ['U1', '1']],
                                '/VOUT': [['C3', '1'], ['C4', '1'], ['U1', '3']],
                                '/OUT_F': [['C4', '2'], ['C5', '1'], ['J2', '1']],
                                '/SENSE': [['J1', '3'], ['J2', '3']],
                                'GND': [['C1', '2'], ['C2', '2'], ['C3', '2'], ['C5', '2'], ['J1', '2'], ['J2', '2'],
                                        ['U1', '2']]})
        self.assertEqual(list(singles.values()), [(['U1', '4'], 'no_connect')])
        intent, layout = load('presentation.intent.json'), load('presentation.layout.json')
        expected = resolved_inventory(resolve_intent(intent, captures(intent)))
        actual = read_native_inventory(work / 'netlist/design.xml')
        namespace = uuid.UUID(layout['namespace'])
        physical = [c['id'] for c in intent['components'] if not c['reference'].startswith('#')]
        anchors = {cid: {'sheet': '/', 'uuid': str(uuid.uuid5(namespace, json.dumps(['symbol', cid, 1],
                                                                                    separators=(',', ':'))))}
                   for cid in physical}
        for predicate in ('topology', 'components', 'instance'):
            extra = dict(anchors=anchors, instance_components=physical) if predicate == 'instance' else {}
            policy = validate_policy(dict(source=dict(kind='intent', path='presentation.intent.json'),
                                          netlist_check='netlist', predicate=predicate, multiset='exactly', **extra))
            report = compare(expected, actual, policy)
            with self.subTest(predicate=predicate):
                self.assertEqual((report['execution'], report['outcome']), ('ok', 'pass'), report)
        # KiCad's own forced re-save reproduces wires, junctions, frames and titles.
        shutil.copytree(project, work / 'resave' / 'copy')
        run_native(project, work / 'resave', 'kicad-cli', 'sch', 'upgrade', '--force',
                   '/work/copy/presentation.kicad_sch')
        native = (work / 'resave/copy/presentation.kicad_sch').read_bytes()
        self.assertEqual(native.replace(b'(generator "eeschema")', b'(generator "agent-ee-schematic-emitter")', 1),
                         files['presentation.kicad_sch'])

    def test_fanned_labels_keep_native_connectivity_and_resave(self):
        """AGENTEE-241: fan wires and moved labels change nothing native but their drawing."""
        transforms = {'j_in': {'rotation': 90, 'mirror': None}}
        files = functional(layout=dict(load('presentation.layout.json'), transforms=transforms), **tables())
        self.assertEqual(len(children(parse_sexpr(files['presentation.kicad_sch']), 'wire')), 7)
        project, work, erc = self.native(files, 'fanned')
        self.assertEqual([v for s in erc['sheets'] for v in s['violations']], [])
        rows_files = emit('presentation', layout=dict(rows_layout(), transforms=transforms), **tables())[1]
        _, rows_work, rows_erc = self.native(rows_files, 'rows')
        self.assertEqual([v for s in rows_erc['sheets'] for v in s['violations']], [])
        self.assertEqual(native_netlist(work / 'netlist/design.xml'), native_netlist(rows_work / 'netlist/design.xml'))
        shutil.copytree(project, work / 'resave' / 'copy')
        run_native(project, work / 'resave', 'kicad-cli', 'sch', 'upgrade', '--force',
                   '/work/copy/presentation.kicad_sch')
        native = (work / 'resave/copy/presentation.kicad_sch').read_bytes()
        self.assertEqual(native.replace(b'(generator "eeschema")', b'(generator "agent-ee-schematic-emitter")', 1),
                         files['presentation.kicad_sch'])

    def test_wrong_drawn_connection_changes_the_native_netlist(self):
        """The native oracle sees a drawn short: moving one wire end onto GND merges the nets."""
        files = functional(**tables())
        data = files['presentation.kicad_sch'].decode()
        root = parse_sexpr(files['presentation.kicad_sch'])
        wire = children(root, 'wire')[0]
        (x0, y0), (x1, y1) = [(xy[1], xy[2]) for xy in children(children(wire, 'pts')[0], 'xy')]
        old = f'(xy {x0} {y0}) (xy {x1} {y1})'
        self.assertEqual(data.count(old), 1)
        intent = load('presentation.intent.json')
        points = endpoints(intent, files['presentation.kicad_sch'])
        gnd = points['U1', '2']
        broken = data.replace(old, f'(xy {x0} {y0}) (xy {gnd[0] / 1e6:g} {gnd[1] / 1e6:g})')
        self.native({**files, 'presentation.kicad_sch': broken.encode()}, 'broken')
        _, nets, _ = native_netlist(self.tmp / 'broken/work/netlist/design.xml')
        clean = self.native(files, 'clean')
        self.assertNotEqual(nets, native_netlist(self.tmp / 'clean/work/netlist/design.xml')[1])


if __name__ == '__main__':
    unittest.main()
