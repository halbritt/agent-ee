"""Original full-size fixtures exercise graph, hierarchy, XML and native syntax."""
import hashlib
import itertools
import json
from pathlib import Path
import tempfile
import unittest

from agent_ee.design_limits import MAX_COMPONENTS, MAX_NETS, MAX_TERMINALS
from agent_ee.connectivity_ir import resolve_intent, validate_resolved
from agent_ee.native_netlist import read_native_inventory
from agent_ee.netlist_parity import Unsupported, _assignments, compare, resolved_inventory
from agent_ee.schematic_decompile import decompile
from schematic_decompile_fixture import control, serialize
from agent_ee.symbol_library import parse_sexpr


def library(pins, *, spread=False):
    return ('(kicad_symbol_lib (version 20231120) (generator "owned") '
            '(symbol "Capacity" (symbol "Capacity_1_1"' + ''.join(
                f'(pin passive line (at {i if spread else 0} 0 0) (length 1) '
                f'(name "P{i}") (number "{i}"))' for i in range(pins)) + ')))').encode()


def intent(components, pins, nets, *, hierarchical=False):
    lib = library(pins)
    raw = dict(schema='agent-ee.connectivity-intent/1',
        libraries=[dict(name='Owned', path='Owned.kicad_sym', sha256=hashlib.sha256(lib).hexdigest())],
        components=[dict(id=str(i), reference=f'U{i+1}', lib_id='Owned:Capacity',
                         value=str(i), footprint='', units=[1]) for i in range(components)],
        nets=[], no_connects=[])
    terms = [dict(component=str(i), pin={'number': str(p)})
             for i in range(components) for p in range(pins)]
    if nets:
        raw['nets'] = [dict(id=str(i), name=f'N{i}', terminals=terms[i::nets]) for i in range(nets)]
    else:
        raw['no_connects'] = terms
    if hierarchical:
        raw.update(schema='agent-ee.connectivity-intent/2',
                   hierarchy=[dict(id='root', parent=None, local_id='root', file='main.kicad_sch')], ports=[])
        for kind in ('components', 'nets', 'no_connects'):
            for item in raw[kind]:
                item['sheet'] = 'root'
    return raw, {'Owned.kicad_sym': lib}


def native_xml(components, pins, nets):
    # Original minimal KiCad XML, preserving every selected physical terminal.
    pin_xml = ''.join(f'<pin num="{p}"/>' for p in range(pins))
    comps = ''.join(f'<comp ref="U{i+1}"><value>{i}</value><libsource lib="Owned" part="Capacity"/>'
        f'<sheetpath tstamps="/"/><tstamps>u{i}</tstamps><units><unit name="A"><pins>{pin_xml}'
        '</pins></unit></units></comp>' for i in range(components))
    terms = [f'<node ref="U{i+1}" pin="{p}"/>' for i in range(components) for p in range(pins)]
    net_xml = ''.join(f'<net code="{i+1}" name="N{i}">' + ''.join(terms[i::nets]) + '</net>'
                      for i in range(nets))
    part = ''.join(f'<pin num="{p}" name="P{p}" type="passive"/>' for p in range(pins))
    return ('<export version="E"><design><sheet tstamps="/"/></design><components>' + comps +
            '</components><libparts><libpart lib="Owned" part="Capacity"><pins>' + part +
            '</pins></libpart></libparts><nets>' + net_xml + '</nets></export>')


def schematic(components, pins, *, spread=False):
    lib = library(pins, spread=spread)
    root = parse_sexpr(control([(0, 0)] * components))
    definition = parse_sexpr(lib)[-1]
    definition[1] = 'Owned:Capacity'
    next(n for n in root if isinstance(n, list) and n[0] == 'lib_symbols')[1:] = [definition]
    for node in root:
        if isinstance(node, list) and node[0] == 'symbol':
            next(n for n in node if isinstance(n, list) and n[0] == 'lib_id')[1] = 'Owned:Capacity'
    manifest = [dict(name='Owned', path='Owned.kicad_sym', sha256=hashlib.sha256(lib).hexdigest())]
    return (serialize(root) + '\n').encode(), manifest, {'Owned.kicad_sym': lib}


# Net and terminal fixtures stay below all other inventory ceilings.
BOUNDARIES = (
    ('components', (MAX_COMPONENTS, 2, 1), (MAX_COMPONENTS + 1, 2, 1), 'component inventory'),
    ('nets', (2, MAX_NETS, MAX_NETS), (2, MAX_NETS + 1, MAX_NETS + 1), 'net inventory'),
    ('terminals', (2, MAX_TERMINALS // 2, 1), (1, MAX_TERMINALS + 1, 1), 'terminal inventory'),
)


class DesignLimitsTests(unittest.TestCase):
    def test_flat_resolved_graph_boundaries(self):
        for label, at, above, message in BOUNDARIES:
            for dims, accepted in ((at, True), (above, False)):
                with self.subTest(limit=label, dims=dims):
                    raw, captures = intent(*dims)
                    record = resolve_intent(raw, captures)
                    self.assertEqual(record['outcome'], 'pass', record['findings'])
                    if accepted:
                        graph = resolved_inventory(record)
                        self.assertEqual(len(graph['components']), dims[0])
                        self.assertEqual(len(graph['nets']), dims[2])
                    else:
                        with self.assertRaisesRegex(Unsupported, message):
                            resolved_inventory(record)

    def test_hierarchical_boundaries(self):
        for label, at, above, _ in BOUNDARIES:
            for dims, accepted in ((at, True), (above, False)):
                with self.subTest(limit=label, dims=dims):
                    raw, captures = intent(*dims, hierarchical=True)
                    record = resolve_intent(raw, captures)
                    self.assertEqual((record['execution'], record['outcome']),
                                     ('ok', 'pass') if accepted else ('unsupported', 'unknown'), record['findings'])
                    if accepted:
                        validate_resolved(record)
                        graph = resolved_inventory(record)
                        self.assertEqual(len(graph['components']), dims[0])
                        self.assertEqual(len(graph['nets']), dims[2])
                    else:
                        self.assertTrue({'hierarchy_schema', 'symbol_unsupported'} & {f['code'] for f in record['findings']}, record['findings'])
                        self.assertEqual(record['coverage'], 'missing')

    def test_native_xml_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'native.xml'
            for label, at, above, message in BOUNDARIES:
                for dims, accepted in ((at, True), (above, False)):
                    with self.subTest(limit=label, dims=dims):
                        path.write_text(native_xml(*dims))
                        if accepted:
                            graph = read_native_inventory(path)
                            self.assertEqual(len(graph['components']), dims[0])
                            self.assertEqual(len(graph['nets']), dims[2])
                        else:
                            with self.assertRaisesRegex(Unsupported, message):
                                read_native_inventory(path)

    def test_decompiled_boundaries(self):
        cases = (
            ('components', (MAX_COMPONENTS, 1), (MAX_COMPONENTS + 1, 1), False, 'symbol count limit'),
            ('nets', (2, MAX_NETS), (2, MAX_NETS + 1), True, 'net count limit'),
            ('terminals', (2, MAX_TERMINALS // 2), (1, MAX_TERMINALS + 1), False, 'physical pin count limit'),
        )
        for label, at, above, spread, message in cases:
            for dims, accepted in ((at, True), (above, False)):
                with self.subTest(limit=label, dims=dims):
                    result = decompile(*schematic(*dims, spread=spread))
                    report = result['report']
                    self.assertEqual((report['execution'], report['outcome']),
                                     ('ok', 'pass') if accepted else ('unsupported', 'unknown'), report['findings'])
                    if accepted:
                        graph = resolved_inventory(result['resolved'])
                        self.assertEqual(len(graph['components']), dims[0])
                        self.assertEqual(sum(len(c['pins']) for c in graph['components'].values()), dims[0] * dims[1])
                    else:
                        self.assertIsNone(result['intent'])
                        self.assertIsNone(result['resolved'])
                        self.assertIn(message, str(report['findings']))

    def test_net_overflow_refuses_before_missing_library_read(self):
        from agent_ee.netlist_parity import _resolve_captured_intent
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw, _ = intent(2, MAX_NETS + 1, MAX_NETS + 1)
            (root / 'intent.json').write_text(json.dumps(raw))
            with self.assertRaisesRegex(Unsupported, 'net inventory limit exceeded before resolution'):
                _resolve_captured_intent(root, 'intent.json')

    def test_combined_ceiling_exact_search_does_not_recurse(self):
        raw, captures = intent(MAX_COMPONENTS, 8, MAX_NETS)
        graph = resolved_inventory(resolve_intent(raw, captures))
        report = compare(graph, graph, {'predicate': 'topology', 'multiset': 'exactly'})
        self.assertEqual((report['execution'], report['outcome']), ('ok', 'pass'))
        self.assertEqual(report['search_steps'], MAX_COMPONENTS)
        self.assertEqual(len(report['matched_nets']), MAX_NETS)

    def test_expanded_capacity_still_evaluates_defects_and_search_exhaustion(self):
        raw, captures = intent(327, 2, 1)
        expected = resolved_inventory(resolve_intent(raw, captures))
        raw['components'][0]['value'] = 'changed'
        actual = resolved_inventory(resolve_intent(raw, captures))
        report = compare(expected, actual, {'predicate': 'topology', 'multiset': 'exactly'})
        self.assertEqual((report['execution'], report['outcome']), ('ok', 'defect'))
        report = compare(expected, expected,
                         {'predicate': 'topology', 'multiset': 'exactly', 'max_search': 326})
        self.assertEqual((report['execution'], report['outcome']), ('unsupported', 'unknown'))
        self.assertEqual(report['reason'], 'exact graph search limit exhausted')
        self.assertEqual(report['search_steps'], 326)

    def test_assignment_order_and_exact_budget(self):
        candidates = {'b': ['1', '2'], 'a': ['1', '2']}
        budget = dict(steps=0, limit=6)
        expected = [dict(zip(('a', 'b'), p)) for p in itertools.permutations(('1', '2'))]
        self.assertEqual(list(_assignments(candidates, budget)), expected)
        self.assertEqual(budget['steps'], 6)
        budget = dict(steps=0, limit=5)
        iterator = _assignments(candidates, budget)
        self.assertEqual(next(iterator), expected[0])
        with self.assertRaisesRegex(Unsupported, 'exact graph search limit exhausted'):
            list(iterator)
        self.assertEqual(budget['steps'], 5)
        budget = dict(steps=0, limit=0)
        self.assertEqual(list(_assignments({}, budget)), [{}])
        self.assertEqual(budget['steps'], 0)
        self.assertEqual(list(_assignments({'empty': []}, budget)), [])
