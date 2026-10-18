import json
import unittest
from pathlib import Path

from agent_ee.netlist_parity import Unsupported, resolved_inventory

FIXTURE = Path(__file__).parent / 'fixtures/netlist-parity'


class ResolvedTests(unittest.TestCase):
    def test_identical_parts_remain_separate_instances(self):
        graph = resolved_inventory(json.loads((FIXTURE / 'resolved.json').read_text()))
        self.assertEqual(len(graph['components']), 3)
        self.assertEqual(len(graph['nets']), 3)
        self.assertEqual(graph['components']['series']['pins'], ('1', '2'))
        self.assertEqual(graph['components']['shunt']['pins'], ('1', '2'))

    def test_power_projection_refuses_unqualified_or_unobservable_markers(self):
        from agent_ee.connectivity_ir import resolve_intent
        root = Path(__file__).parents[1] / 'fixtures/generation'
        def nc(intent, cid, net):
            conductor = next(n for n in intent['nets'] if n['id'] == net)
            terminal = next(t for t in conductor['terminals'] if t['component'] == cid)
            conductor['terminals'].remove(terminal)
            intent['no_connects'].append(terminal)
        def virtual_only(intent):
            terminals = []
            for cid, nid in (('flag_vin', 'vin'), ('flag_gnd', 'gnd')):
                conductor = next(n for n in intent['nets'] if n['id'] == nid)
                terminal = next(t for t in conductor['terminals'] if t['component'] == cid)
                conductor['terminals'].remove(terminal)
                terminals.append(terminal)
            intent['nets'].append(dict(id='virtual', name='VIRTUAL', terminals=terminals))
        edits = {
            'ordinary_reference_on_power': lambda i: next(c for c in i['components'] if c['id'] == 'gnd').update(reference='P1'),
            'virtual_reference_on_physical_part': lambda i: next(c for c in i['components'] if c['id'] == 'c_out').update(reference='#C1'),
            'power_with_footprint': lambda i: next(c for c in i['components'] if c['id'] == 'gnd').update(footprint='OwnedFP:CAP_2PAD'),
            'unconnected_power': lambda i: nc(i, 'gnd', 'gnd'),
            'one_remaining_physical_pin': lambda i: nc(i, 'j_in', 'vin'),
            'no_remaining_physical_pins': virtual_only,
        }
        for case, edit in edits.items():
            with self.subTest(case=case):
                intent = json.loads((root / 'regulator.intent.json').read_text())
                edit(intent)
                captures = {lib['path']: (root / 'project' / lib['path']).read_bytes() for lib in intent['libraries']}
                resolved = resolve_intent(intent, captures)
                self.assertEqual((resolved['execution'], resolved['outcome']), ('ok', 'pass'), resolved)
                with self.assertRaises(Unsupported):
                    resolved_inventory(resolved)

    def test_power_projection_rejects_multiple_or_nonpower_pins(self):
        import hashlib
        from agent_ee.connectivity_ir import resolve_intent
        root = Path(__file__).parents[1] / 'fixtures/generation'
        for case in ('passive_pin', 'multiple_pins'):
            with self.subTest(case=case):
                intent = json.loads((root / 'regulator.intent.json').read_text())
                captures = {lib['path']: (root / 'project' / lib['path']).read_bytes() for lib in intent['libraries']}
                power = next(lib for lib in intent['libraries'] if lib['name'] == 'OwnedPower')
                data = captures[power['path']]
                if case == 'passive_pin':
                    data = data.replace(b'(pin power_in line', b'(pin passive line', 1)
                else:
                    data = data.replace(b'(symbol "GND_1_1"', b'(symbol "GND_1_1"\n'
                        b'(pin power_in line (at 2.54 0 180) (length 2.54) (name "P2") (number "2"))', 1)
                    next(n for n in intent['nets'] if n['id'] == 'gnd')['terminals'].append(
                        {'component': 'gnd', 'pin': {'number': '2'}})
                captures[power['path']] = data
                power['sha256'] = hashlib.sha256(data).hexdigest()
                resolved = resolve_intent(intent, captures)
                self.assertEqual((resolved['execution'], resolved['outcome']), ('ok', 'pass'), resolved)
                with self.assertRaises(Unsupported):
                    resolved_inventory(resolved)


class NativeReaderTests(unittest.TestCase):
    def test_full_native_inventory_has_pin_metadata_and_provenance(self):
        from agent_ee.native_netlist import read_native_inventory
        graph = read_native_inventory(Path(__file__).parents[1] /
            'evidence/rc-final/control/work/netlist/design.xml')
        self.assertEqual(len(graph['components']), 3)
        self.assertEqual(graph['components']['J1']['pins'], ('1', '2', '3'))
        self.assertEqual(graph['components']['J1']['lib_id'], 'Connector_Generic:Conn_01x03')
        self.assertEqual(graph['components']['J1']['sheet'], '/')


class GraphTests(unittest.TestCase):
    @staticmethod
    def four_channels():
        """Owned repeated RC ladders; native sort order scrambles each position."""
        from agent_ee.netlist_parity import terminal_inventory
        graphs = []
        for native in (False, True):
            components, nets = {}, {}
            for channel in range(4):
                ids = []
                for stage in range(12):
                    cid = (f'R{stage:02}-{(channel + stage) % 4}' if native
                           else f'ch{channel}-r{stage:02}')
                    ids.append(cid)
                    components[cid] = {'lib_id': 'Owned:C' if stage == 11 else 'Owned:R',
                        'value': '100n' if stage == 11 else '10k', 'populated': True,
                        'pins': ('1', '2'), 'reference': cid if native else f'R{channel * 12 + stage + 1}', 'locator': cid}
                for stage in range(11):
                    nid = f'{channel}-{stage}'
                    nets[nid] = {'name': f'/CH{channel}_IN' if stage == 0 else f'generated-{nid}',
                        'locator': nid, 'terminals': ((ids[stage], '2'), (ids[stage + 1], '1'))}
            graphs.append(terminal_inventory(components, nets))
        names = {'schema': 'agent-ee.net-name-policy/1',
            'requirement': {'id': 'names', 'source': {'path': 'requirements.md', 'locator': 'Names'}},
            'required': [{'name': f'CH{channel}_IN',
                          'anchors': [{'reference': f'R00-{channel}', 'pin': '2'}]}
                         for channel in range(4)]}
        return *graphs, names

    def test_four_channel_symmetry_anchored(self):
        from agent_ee.netlist_parity import compare
        expected, actual, names = self.four_channels()
        policy = {'predicate': 'topology', 'multiset': 'exactly', 'anchors': {}}
        unanchored = compare(expected, actual, policy)
        self.assertEqual((unanchored['execution'], unanchored['outcome'], unanchored['search_steps']),
                         ('unsupported', 'unknown', 100000))
        report = compare(expected, actual, policy, name_policy=names)
        self.assertEqual((report['execution'], report['outcome']), ('ok', 'pass'), report)
        self.assertLessEqual(report['search_steps'], 100000)
        self.assertEqual(len(report['anchors']['nets']), 4)
        self.assertEqual(len(report['matched_nets']), 44)
        self.assertEqual(report['scores']['jaccard'], 1.0)

    def test_fixed_references_seed_refinement_and_instance_correspondence(self):
        from agent_ee.netlist_parity import compare
        expected, actual, _ = self.four_channels()
        anchors = {f'ch{channel}-r00': {'reference': f'R00-{channel}'} for channel in range(4)}
        report = compare(expected, actual, {'predicate': 'instance', 'multiset': 'exactly',
            'anchors': anchors, 'instance_components': ['ch0-r11']})
        self.assertEqual((report['execution'], report['outcome']), ('ok', 'pass'), report)
        self.assertEqual(len(report['anchors']['components']), 4)
        self.assertEqual(report['component_matches']['ch0-r11'], ['R11-3'])
        self.assertLessEqual(report['search_steps'], 100000)

    def test_declared_component_anchor_mismatches_are_defects(self):
        for anchor in ({'reference': 'absent'}, {'reference': 'X9'}, {'reference': 'R90'}):
            with self.subTest(anchor=anchor):
                report = self.compare(anchors={'series': anchor})
                self.assertEqual((report['execution'], report['outcome']), ('ok', 'defect'), report)
                self.assertTrue(report['anchor_mismatches'])
                self.assertEqual(report['search_steps'], 0)

    def test_mixed_anchor_syntax_cannot_bind_two_parts_to_one_instance(self):
        report = self.compare(anchors={'series': {'reference': 'R80'},
            'shunt': {'sheet': '/', 'uuid': 'series-uuid'}})
        self.assertEqual((report['execution'], report['outcome']), ('ok', 'defect'), report)
        self.assertTrue(any(m['kind'] == 'component_anchor_conflict' for m in report['anchor_mismatches']))

    def test_required_net_anchors_match_exhaustive_small_graph_oracle(self):
        from itertools import permutations
        from agent_ee.netlist_parity import compare, terminal_inventory
        terminals = [(f'R{i}', pin) for i in range(3) for pin in ('1', '2')]
        components = {f'R{i}': {'lib_id': 'Owned:R', 'value': '10k', 'populated': True,
            'pins': ('1', '2'), 'reference': f'R{i}', 'locator': f'R{i}'} for i in range(3)}
        def partitions(pins):
            if not pins:
                yield []
            else:
                for index in range(1, len(pins)):
                    for rest in partitions(pins[1:index] + pins[index + 1:]):
                        yield [(pins[0], pins[index])] + rest
        def graph(pairs):
            return terminal_inventory(components, {str(i): {
                'name': 'IN' if ('R0', '1') in pair else f'generated{i}',
                'locator': str(i), 'terminals': tuple(pair)} for i, pair in enumerate(pairs)})
        expected = graph([(('R0', '1'), ('R1', '1')), (('R0', '2'), ('R2', '1')),
                          (('R1', '2'), ('R2', '2'))])
        names = {'schema': 'agent-ee.net-name-policy/1', 'requirement': {
            'id': 'names', 'source': {'path': 'requirements.md', 'locator': 'Names'}},
            'required': [{'name': 'IN', 'anchors': [{'reference': 'R0', 'pin': '1'}]}]}
        for pairs in partitions(terminals):
            actual = graph(pairs)
            observed = {frozenset(pair) for pair in pairs}
            anchored = next(frozenset(pair) for pair in pairs if ('R0', '1') in pair)
            equivalent = False
            for order in permutations(components):
                mapping = dict(zip(components, order))
                mapped = {nid: frozenset((mapping[cid], pin) for cid, pin in net['terminals'])
                          for nid, net in expected['nets'].items()}
                equivalent |= set(mapped.values()) == observed and mapped['0'] == anchored
            with self.subTest(pairs=pairs):
                report = compare(expected, actual, {'predicate': 'topology', 'multiset': 'exactly',
                    'anchors': {}}, name_policy=names)
                self.assertEqual((report['execution'], report['outcome']),
                                 ('ok', 'pass' if equivalent else 'defect'), report)

    def test_net_anchor_failure_boundaries(self):
        import copy
        from agent_ee.netlist_parity import compare
        expected, actual, names = self.four_channels()
        def run(graph, policy=names):
            return compare(expected, graph, {'predicate': 'topology', 'multiset': 'exactly',
                'anchors': {}}, name_policy=policy)
        for case in ('absent', 'duplicate', 'off_pin', 'wrong_incidence', 'unresolved'):
            with self.subTest(case=case):
                graph = copy.deepcopy(actual)
                if case == 'absent':
                    graph['nets']['0-0']['name'] = 'suggested'
                elif case == 'duplicate':
                    graph['nets']['1-0']['name'] = '/child/CH0_IN'
                elif case == 'off_pin':
                    # Name exists, but its declared reference/pin is on another net.
                    graph['nets']['0-0']['name'], graph['nets']['0-1']['name'] = (
                        graph['nets']['0-1']['name'], graph['nets']['0-0']['name'])
                elif case == 'wrong_incidence':
                    from agent_ee.netlist_parity import terminal_inventory
                    a, b = graph['nets']['0-0'], graph['nets']['0-1']
                    a['terminals'], b['terminals'] = (
                        (a['terminals'][0], b['terminals'][1]), (b['terminals'][0], a['terminals'][1]))
                    graph.update(terminal_inventory(graph['components'], graph['nets']))
                else:
                    from agent_ee.netlist_parity import terminal_inventory
                    graph['nets']['0-0']['terminals'] = (graph['nets']['0-0']['terminals'][1],)
                    graph.update(terminal_inventory(graph['components'], graph['nets']))
                report = run(graph)
                self.assertEqual(report['outcome'], 'unknown' if case == 'unresolved' else 'defect', report)
        source = copy.deepcopy(expected)
        source['nets']['0-0']['name'] = 'absent'
        report = compare(source, actual, {'predicate': 'topology', 'multiset': 'exactly',
            'anchors': {}}, name_policy=names)
        self.assertEqual((report['execution'], report['outcome']), ('unsupported', 'unknown'))
        report = compare(expected, actual, {'predicate': 'topology', 'multiset': 'exactly',
            'anchors': {}, 'max_search': 1}, name_policy=names)
        self.assertEqual((report['execution'], report['outcome'], report['search_steps']),
                         ('unsupported', 'unknown', 1))

    def test_anchor_mismatch_defect(self):
        # Exchanging only the names is topologically invisible until declared required.
        from agent_ee.netlist_parity import compare
        expected, actual, names = self.four_channels()
        actual['nets']['0-0']['name'], actual['nets']['1-0']['name'] = (
            actual['nets']['1-0']['name'], actual['nets']['0-0']['name'])
        report = compare(expected, actual, {'predicate': 'topology', 'multiset': 'exactly',
            'anchors': {}}, name_policy=names)
        self.assertEqual((report['execution'], report['outcome']), ('ok', 'defect'), report)
        self.assertTrue(report['anchor_mismatches'])
        self.assertEqual(report['search_steps'], 0)

    def test_net_anchors_allow_exchange_of_indistinguishable_channel_tails(self):
        from agent_ee.netlist_parity import compare, terminal_inventory
        expected, actual, names = self.four_channels()
        a, b = actual['nets']['0-0'], actual['nets']['1-0']
        a['terminals'], b['terminals'] = (
            (a['terminals'][0], b['terminals'][1]), (b['terminals'][0], a['terminals'][1]))
        actual.update(terminal_inventory(actual['components'], actual['nets']))
        report = compare(expected, actual, {'predicate': 'topology', 'multiset': 'exactly',
            'anchors': {}}, name_policy=names)
        self.assertEqual((report['execution'], report['outcome']), ('ok', 'pass'), report)
        self.assertEqual(report['scores']['jaccard'], 1.0)

    def setUp(self):
        from agent_ee.native_netlist import read_native_inventory
        self.expected = resolved_inventory(json.loads((FIXTURE / 'resolved.json').read_text()))
        self.actual = read_native_inventory(FIXTURE / 'native.xml')

    def compare(self, **policy):
        from agent_ee.netlist_parity import compare
        return compare(self.expected, self.actual, {'predicate': 'topology',
                       'multiset': 'exactly', 'anchors': {}, **policy})

    def test_all_reference_and_net_names_can_change(self):
        report = self.compare()
        self.assertEqual(report['outcome'], 'pass', report)
        self.assertEqual(report['scores']['jaccard'], 1.0)
        self.assertEqual(report['component_matches']['series'], ['R80'])

    def change_membership(self, terminal, target):
        nets = self.actual['nets']
        for net in nets.values():
            net['terminals'] = tuple(t for t in net['terminals'] if t != terminal)
        if target is not None:
            nets[target]['terminals'] += (terminal,)
        from agent_ee.netlist_parity import terminal_inventory
        self.actual = terminal_inventory(self.actual['components'], nets)

    def test_ic_pin_swap_reports_both_affected_nets(self):
        self.change_membership(('X9', '1'), '82')
        self.change_membership(('X9', '2'), '81')
        report = self.compare()
        self.assertEqual(report['outcome'], 'defect', report)
        self.assertEqual(set(report['missing_nets']), {'input', 'output'})
        self.assertEqual(report['expected_net_names']['input'], 'VBUS')
        self.assertEqual(report['expected_net_names']['output'], 'VCC')

    def test_merge_and_off_pin_binding_are_not_hidden_by_name(self):
        self.actual['nets']['82']['terminals'] += self.actual['nets'].pop('81')['terminals']
        from agent_ee.netlist_parity import terminal_inventory
        self.actual.update(terminal_inventory(self.actual['components'], self.actual['nets']))
        report = self.compare()
        self.assertEqual(report['outcome'], 'defect', report)
        self.assertTrue(any(set(m['expected']) == {'input', 'output'} for m in report['merged_nets']))
        self.setUp()
        self.change_membership(('R80', '1'), None)
        self.actual['nets']['81']['name'] = 'VBUS'
        report = self.compare()
        self.assertEqual(report['outcome'], 'defect', report)
        self.assertTrue(any(b['component'] == 'series' and b['pin'] == '1' and
            b['kind'] == 'missing_binding' for b in report['bindings']))

    def test_identical_resistor_incidence_mutation_is_not_collapsed(self):
        self.change_membership(('R90', '1'), '81')
        report = self.compare()
        self.assertEqual(report['outcome'], 'defect', report)
        self.assertEqual(len(report['native_components']), 3)

    def test_exact_and_at_least_component_multiplicity(self):
        import copy
        self.actual['components']['R100'] = copy.deepcopy(self.actual['components']['R90'])
        report = self.compare(predicate='components')
        self.assertEqual(report['outcome'], 'defect', report)
        self.assertEqual(report['extra_components'][0]['count'], 1)
        self.assertEqual(self.compare(predicate='components', multiset='at_least')['outcome'], 'pass')
        del self.actual['components']['R100']
        del self.actual['components']['R90']
        self.assertEqual(self.compare(predicate='components', multiset='at_least')['outcome'], 'defect')

    def test_unresolvable_keys_and_exhausted_search_never_pass(self):
        exhausted = self.compare(max_search=1)
        self.assertEqual(exhausted['outcome'], 'unknown')
        self.assertEqual(exhausted['search_steps'], 1)
        self.actual['components']['R80']['pins'] = ('1', '2', '9')
        self.assertEqual(self.compare()['outcome'], 'unknown')

    def test_symmetric_instances_need_anchors_for_instance_predicate(self):
        # Two parallel resistors are indistinguishable under every graph predicate.
        from agent_ee.netlist_parity import terminal_inventory
        for graph, a, b in [(self.expected, 'series', 'shunt'), (self.actual, 'R80', 'R90')]:
            graph['nets'] = {str(i): {'name': str(i), 'locator': str(i),
                'terminals': ((a, str(i)), (b, str(i)), ('port' if graph is self.expected else 'X9', str(i)))}
                for i in (1, 2)}
            # Port pin3 is explicitly unconnected on both sides.
            graph.update(terminal_inventory(graph['components'], graph['nets']))
        self.assertEqual(self.compare()['outcome'], 'pass')
        report = self.compare(predicate='instance', instance_components=['series'])
        self.assertEqual((report['execution'], report['outcome']), ('unsupported', 'unknown'), report)
        self.assertEqual(report['component_matches']['series'], ['R80', 'R90'])
        anchored = self.compare(predicate='instance', instance_components=['series'],
                                anchors={'series': {'sheet': '/', 'uuid': 'series-uuid'}})
        self.assertEqual(anchored['outcome'], 'pass', anchored)

    def test_no_connect_normalization_cannot_hide_a_required_identical_part_binding(self):
        from agent_ee.netlist_parity import terminal_inventory
        self.expected['nets']['output']['terminals'] = tuple(t for t in self.expected['nets']['output']['terminals']
            if t != ('shunt', '1'))
        self.expected.update(terminal_inventory(self.expected['components'], self.expected['nets']))
        self.expected['no_connects'] = (('shunt', '1'),)
        self.change_membership(('R80', '1'), None)
        self.actual['nets']['nc'] = {'name': 'unconnected-(R80-Pad1)', 'locator': 'native unconnected pin',
            'terminals': (('R80', '1'),), 'auto_unconnected_singleton': True, 'no_connect_marker': False}
        self.actual.update(terminal_inventory(self.actual['components'], self.actual['nets']))
        report = self.compare()
        self.assertEqual(report['outcome'], 'defect', report)
        self.assertIn('input', report['missing_nets'])

    def test_maximum_terminal_fanout_retains_exact_multiplicity(self):
        from agent_ee.netlist_parity import compare, terminal_inventory
        graphs = []
        for prefix in ['logical', 'native']:
            components = {f'{prefix}-{i}': {'lib_id': 'Owned:Many', 'value': 'same', 'populated': True,
                'pins': tuple(str(p) for p in range(64)), 'locator': str(i)} for i in range(128)}
            nets = {'one-conductor': {'name': prefix, 'locator': prefix,
                'terminals': tuple((cid, pin) for cid, comp in components.items() for pin in comp['pins'])}}
            graphs.append(terminal_inventory(components, nets))
        report = compare(*graphs, {'predicate': 'topology', 'multiset': 'exactly', 'anchors': {}})
        self.assertEqual(report['outcome'], 'pass', report)
        self.assertEqual(len(report['component_matches']), 128)
        self.assertLessEqual(report['search_steps'], 100000)
        self.assertEqual(report['scores']['jaccard'], 1.0)


class NativeParityTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from toolchain import require_toolchain
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-netlist-parity-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.counter = 0

    def run_variant(self, variant='control', params=None, golden_fixture='native.xml', name_policy=None):
        import importlib.util
        import time
        from agent_ee.candidate import capture
        from agent_ee.checks import run_checks
        from agent_ee.storage import write_json
        spec = importlib.util.spec_from_file_location('parity_fixture_generator', FIXTURE / 'generate.py')
        generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generator)
        self.counter += 1
        case = self.root / str(self.counter)
        case.mkdir()
        (case / 'parity.kicad_sch').write_text(generator.schematic(variant))
        (case / 'golden.xml').write_bytes((FIXTURE / golden_fixture).read_bytes())
        if policy_source := (params or {}).get('source'):
            if policy_source['kind'] in ('intent', 'resolved'):
                for name in [policy_source['path'], policy_source.get('intent'), 'Owned.kicad_sym']:
                    if name:
                        (case / name).write_bytes((FIXTURE / name).read_bytes())
        (case / 'requirements.md').write_text('Parity shall preserve the declared topology.\n')
        policy = {'source': {'kind': 'golden_xml', 'path': 'golden.xml'}, 'predicate': 'topology',
                  'multiset': 'exactly', 'netlist_check': 'netlist', **(params or {})}
        scope = {'schema': 'agent-ee.scope/3', 'name': 'parity', 'scope': 'Owned native fixture.',
            'limitations': [], 'schematic': 'parity.kicad_sch',
            'inputs': [{'path': n, 'at': n} for n in ['parity.kicad_sch', 'golden.xml', 'requirements.md']],
            'requirements': ['requirements.md'],
            'checks': [{'id': 'netlist', 'kind': 'netlist'},
                       {'id': 'parity', 'kind': 'netlist_parity', 'params': policy}],
            'baseline': {'requirements': [{'id': 'parity', 'statement': 'Preserve topology.',
                'source': {'path': 'requirements.md', 'locator': 'line 1'},
                'checks': ['netlist', 'parity'], 'intent': 'acceptance'}],
                'targets': ['requirements.md'], 'reference_architectures': ['golden.xml']}}
        if policy['source']['kind'] in ('intent', 'resolved'):
            scope['inputs'] += [{'path': n, 'at': n} for n in [policy['source']['path'], 'Owned.kicad_sym']]
        if 'intent' in policy['source']:
            scope['inputs'].append({'path': policy['source']['intent'], 'at': policy['source']['intent']})
        if name_policy is not None:
            write_json(case / 'names.json', name_policy)
            policy['net_name_policy'] = 'names.json'
            scope['inputs'].append({'path': 'names.json', 'at': 'names.json'})
        write_json(case / 'scope.json', scope)
        run = case / 'run'
        run.mkdir()
        capture(case / 'scope.json', run)
        checks = run_checks(run, time.monotonic() + 90)
        self.assertEqual(checks[0]['execution'], 'ok', checks)
        return run, checks

    def test_native_required_name_anchors_are_captured_sealed_and_immutable(self):
        from agent_ee.baseline import freeze_evidence, verify_evidence, validate_witnesses
        from agent_ee.candidate import verify_snapshot
        from agent_ee.storage import digest, read_json
        names = {'schema': 'agent-ee.net-name-policy/1', 'requirement': {
            'id': 'parity', 'source': {'path': 'requirements.md', 'locator': 'line 1'}},
            'required': [{'name': 'VBUS', 'anchors': [{'reference': 'X9', 'pin': '1'}]}]}
        for variant, outcome in [('control', 'pass'), ('renamed', 'defect'), ('ic-swap', 'defect')]:
            with self.subTest(variant=variant):
                run, checks = self.run_variant(variant, params={
                    'source': {'kind': 'intent', 'path': 'intent.json'}}, name_policy=names)
                self.assertEqual((checks[1]['execution'], checks[1]['outcome']), ('ok', outcome), checks)
                report = checks[1]['details']
                self.assertEqual(len(report['anchors']['nets']), 1)
                self.assertEqual(report['net_name_policy_sha256'], digest(run / 'inputs/names.json'))
                baseline = read_json(run / 'baseline.json')
                self.assertIn('names.json', baseline['files'])
                freeze_evidence(run, checks)
                verify_evidence(run, checks, required=True)
                if outcome == 'defect':
                    validate_witnesses(run, [{'check_id': 'parity', 'requirement_id': 'parity'}],
                        [{'path': 'checks/parity.json', 'locator': 'anchor_mismatches'}])
                path = run / 'work/parity/parity.json'
                path.write_bytes(path.read_bytes() + b' ')
                with self.assertRaises(ValueError):
                    verify_evidence(run, checks, required=True)
                (run / 'inputs/names.json').write_text('{}')
                with self.assertRaises(ValueError):
                    verify_snapshot(run)

    def test_native_fixed_reference_anchor_detects_reannotation(self):
        for variant, outcome in [('control', 'pass'), ('exchange-refs', 'defect')]:
            with self.subTest(variant=variant):
                _, checks = self.run_variant(variant, params={
                    'source': {'kind': 'intent', 'path': 'intent.json'},
                    'anchors': {'series': {'reference': 'R80'}}})
                self.assertEqual((checks[1]['execution'], checks[1]['outcome']), ('ok', outcome), checks)

    def test_actual_kicad_exports_match_precommitted_expectations(self):
        expected = json.loads((FIXTURE / 'native-expectations.json').read_text())
        for variant, outcome in expected.items():
            with self.subTest(variant=variant):
                _, checks = self.run_variant(variant)
                self.assertEqual(checks[1]['execution'], 'ok', checks)
                self.assertEqual(checks[1]['outcome'], outcome, checks)
                report = checks[1]['details']
                if outcome == 'pass':
                    self.assertEqual(report['scores']['jaccard'], 1.0)
                    self.assertEqual(report['scores']['f1'], 1.0)
                if variant == 'off-pin':
                    self.assertTrue(any(binding['kind'] == 'missing_binding' and
                        binding['component'] == 'R80' and binding['pin'] == '1'
                        for binding in report['bindings']), report)
                    self.assertIn('/VBUS', report['native_net_names'].values())
                if variant in ('missing', 'extra'):
                    entries = report['missing_components' if variant == 'missing' else 'extra_components']
                    self.assertEqual(entries, [{'key': ['Owned:R', '10k', True], 'count': 1}])

    def test_native_binding_defects_name_original_intent_nets(self):
        for variant in ('ic-swap', 'merge'):
            with self.subTest(variant=variant):
                _, checks = self.run_variant(variant, params={
                    'source': {'kind': 'intent', 'path': 'intent.json'}})
                self.assertEqual((checks[1]['execution'], checks[1]['outcome']), ('ok', 'defect'), checks)
                report = checks[1]['details']
                affected = {report['expected_net_names'][nid] for nid in report['missing_nets']}
                self.assertTrue({'VBUS', 'VCC'} <= affected, report)
                if variant == 'merge':
                    self.assertTrue(any({'VBUS', 'VCC'} <= {
                        report['expected_net_names'][nid] for nid in merge['expected']}
                        for merge in report['merged_nets']), report)

    def test_native_report_and_xml_are_sealed_and_intent_immutable(self):
        from agent_ee.baseline import freeze_evidence, verify_evidence, validate_witnesses
        from agent_ee.candidate import verify_snapshot
        from agent_ee.storage import digest
        run, checks = self.run_variant('ic-swap')
        self.assertEqual(checks[1]['outcome'], 'defect', checks)
        self.assertEqual(checks[1]['details']['connectivity_sha256'],
                         digest(run / 'work/netlist/connectivity.json'))
        freeze_evidence(run, checks)
        witness = [{'requirement_id': 'parity', 'check_id': 'parity'}]
        citation = [{'path': 'checks/parity.json', 'locator': 'missing_nets'}]
        validate_witnesses(run, witness, citation)
        for rel in ['work/parity/parity.json', 'work/netlist/design.xml', 'work/netlist/connectivity.json']:
            path = run / rel
            original = path.read_bytes()
            path.write_bytes(original + b' ')
            with self.assertRaises(ValueError):
                verify_evidence(run, checks, required=True)
            with self.assertRaises(ValueError):
                validate_witnesses(run, witness, citation)
            path.write_bytes(original)
        (run / 'inputs/golden.xml').write_text('<export/>')
        with self.assertRaises(ValueError):
            verify_snapshot(run)

    def test_runtime_missing_malformed_and_failed_producer_are_unknown(self):
        import time
        from agent_ee.checks import Ctx, CheckError
        from agent_ee.netlist_parity import check_netlist_parity
        from agent_ee.storage import read_json, write_json
        run, _ = self.run_variant()
        manifest = read_json(run / 'candidate.json')
        source, xml = run / 'inputs/golden.xml', run / 'work/netlist/design.xml'
        original_source, original_xml = source.read_bytes(), xml.read_bytes()
        for path, content, execution in [(source, None, 'failed'), (source, b'<broken', 'malformed'),
                                          (xml, b'<broken', 'malformed')]:
            with self.subTest(path=path, content=content):
                if content is None:
                    path.unlink()
                else:
                    path.write_bytes(content)
                ctx = Ctx(run, manifest, manifest['scope']['checks'][1], time.monotonic() + 30)
                with self.assertRaises(CheckError) as raised:
                    check_netlist_parity(ctx)
                self.assertEqual(raised.exception.execution, execution)
                self.assertEqual(ctx.result['outcome'], 'unknown')
                source.write_bytes(original_source)
                xml.write_bytes(original_xml)
        producer_path = run / 'checks/netlist.json'
        producer = read_json(producer_path)
        producer.update(execution='failed', outcome='unknown')
        write_json(producer_path, producer)
        ctx = Ctx(run, manifest, manifest['scope']['checks'][1], time.monotonic() + 30)
        with self.assertRaises(CheckError) as raised:
            check_netlist_parity(ctx)
        self.assertEqual(raised.exception.execution, 'failed')
        self.assertEqual(ctx.result['outcome'], 'unknown')

    def test_connectivity_projection_failures_cannot_establish_parity(self):
        import time
        from agent_ee.checks import Ctx, CheckError
        from agent_ee.netlist_parity import check_netlist_parity
        from agent_ee.storage import read_json
        run, _ = self.run_variant()
        manifest = read_json(run / 'candidate.json')
        paths = [run / 'inputs/golden.xml', run / 'work/netlist/design.xml',
                 run / 'work/netlist/connectivity.json']
        for path in paths:
            original = path.read_bytes()
            corruptions = [('missing', None, 'failed'), ('unreadable_directory', None, 'failed'),
                           ('unreadable_file', original, 'failed'),
                           ('unparseable', b'{broken' if path.suffix == '.json' else b'<broken', 'malformed')]
            if path.suffix == '.json':
                changed = json.loads(original)
                changed['nets'].pop(next(iter(changed['nets'])))
                corruptions += [('wrong_shape', b'{}', 'malformed'),
                                ('contradictory', json.dumps(changed).encode(), 'malformed'),
                                ('duplicate_fields', b'{"components":{},"components":{},"nets":{}}', 'malformed'),
                                ('excessive_nesting', b'[' * 10000 + b'0' + b']' * 10000, 'malformed')]
            for name, content, execution in corruptions:
                with self.subTest(path=path.name, corruption=name):
                    path.unlink()
                    if name == 'unreadable_directory':
                        path.mkdir()
                    elif content is not None:
                        path.write_bytes(content)
                        if name == 'unreadable_file':
                            path.chmod(0)
                    try:
                        ctx = Ctx(run, manifest, manifest['scope']['checks'][1], time.monotonic() + 30)
                        with self.assertRaises(CheckError) as raised:
                            check_netlist_parity(ctx)
                        self.assertEqual(raised.exception.execution, execution)
                        self.assertEqual(ctx.result['outcome'], 'unknown')
                        self.assertEqual(ctx.result['details'].get('evaluated_predicates', []), [])
                    finally:
                        if path.is_dir():
                            path.rmdir()
                        elif path.exists():
                            path.chmod(0o600)
                        path.write_bytes(original)

    def test_actual_native_export_failure_is_failed_unknown(self):
        import time
        from agent_ee.baseline import freeze_evidence, validate_witnesses
        from agent_ee.candidate import capture
        from agent_ee.checks import run_checks
        run, _ = self.run_variant()
        (run.parent / 'parity.kicad_sch').write_text('(kicad_sch (version 20250114) broken')
        failed = run.parent / 'failed-export'
        failed.mkdir()
        capture(run.parent / 'scope.json', failed)
        checks = run_checks(failed, time.monotonic() + 90)
        self.assertEqual((checks[0]['execution'], checks[0]['outcome']), ('failed', 'unknown'), checks)
        self.assertNotEqual(checks[0]['details']['tool']['exit_code'], 0)
        self.assertEqual((checks[1]['execution'], checks[1]['outcome']), ('failed', 'unknown'), checks)
        self.assertEqual(checks[1]['details'].get('evaluated_predicates', []), [])
        freeze_evidence(failed, checks)
        with self.assertRaises(ValueError):
            validate_witnesses(failed, [{'requirement_id': 'parity', 'check_id': 'parity'}],
                               [{'path': 'checks/parity.json', 'locator': 'outcome'}])

    def test_capability_baselines_and_whole_check_witness_scope(self):
        import copy
        from agent_ee.baseline import compile_baseline
        from agent_ee.storage import read_json
        run, _ = self.run_variant()
        manifest = read_json(run / 'candidate.json')
        scope = manifest['scope']
        baseline = compile_baseline(scope, manifest['files'], run / 'inputs')
        self.assertIn('golden.xml', baseline['files'])
        descriptor = baseline['coverage']['checks']['parity']
        self.assertEqual(descriptor['predicates'], ['netlist-parity:topology'])
        self.assertEqual(descriptor['status'], 'supported')
        legacy = copy.deepcopy(scope)
        legacy['schema'] = 'agent-ee.scope/2'
        del legacy['baseline']['requirements'][0]['intent']
        self.assertIn('golden.xml', compile_baseline(legacy, manifest['files'], run / 'inputs')['files'])
        scope['baseline']['requirements'].append({**scope['baseline']['requirements'][0], 'id': 'unrelated'})
        with self.assertRaisesRegex(ValueError, 'one requirement'):
            compile_baseline(scope, manifest['files'], run / 'inputs')
        scope['baseline']['requirements'].pop()
        scope['checks'].reverse()
        with self.assertRaisesRegex(ValueError, 'forward'):
            compile_baseline(scope, manifest['files'], run / 'inputs')

    def test_actual39_intent_and_resolved_adapters_pass_native_exports(self):
        from agent_ee.baseline import freeze_evidence, verify_evidence, load_baseline
        from agent_ee.storage import read_json
        for source in [{'kind': 'intent', 'path': 'intent.json'},
                       {'kind': 'resolved', 'path': 'resolved.json', 'intent': 'intent.json'}]:
            with self.subTest(kind=source['kind']):
                run, checks = self.run_variant(params={'source': source})
                self.assertEqual((checks[1]['execution'], checks[1]['outcome']), ('ok', 'pass'), checks)
                self.assertEqual(checks[1]['details']['scores']['jaccard'], 1.0)
                resolved = read_json(run / 'work/parity/resolved-intent.json')
                from agent_ee.connectivity_ir import validate_resolved
                validate_resolved(resolved)
                self.assertEqual(checks[1]['details']['intent_sha256'], resolved['intent_sha256'])
                self.assertIn('Owned.kicad_sym', load_baseline(run)['files'])
                self.assertIn('intent.json', load_baseline(run)['files'])
                self.assertEqual(checks[1]['details']['source_provenance'],
                                 'resolved_from_captured_intent' if source['kind'] == 'intent'
                                 else 'captured_resolution_rederived_from_captured_intent')
                freeze_evidence(run, checks)
                verify_evidence(run, checks, required=True)

    def test_authored_resigned_resolution_cannot_supply_graph_authority(self):
        # Independent of the original provenance probe: a structurally valid,
        # self-hash-consistent record that the captured intent does not derive.
        import hashlib
        import time
        from agent_ee.baseline import freeze_evidence, validate_witnesses
        from agent_ee.candidate import capture
        from agent_ee.checks import run_checks
        from agent_ee.connectivity_ir import canonical_resolved, validate_resolved
        from agent_ee.storage import read_json, write_json
        source = {'kind': 'resolved', 'path': 'resolved.json', 'intent': 'intent.json'}
        run, checks = self.run_variant(params={'source': source})
        self.assertEqual(checks[1]['outcome'], 'pass', checks)
        def resign(record):
            payload = {k: v for k, v in record.items() if k != 'canonical_sha256'}
            record['canonical_sha256'] = hashlib.sha256(canonical_resolved(payload)).hexdigest()
            return validate_resolved(record)
        def value(record):
            record['components'][0]['value'] = 'AUTHORED-WRONG-VALUE'
        def pin_type(record):
            record['components'][0]['pins'][0]['electrical_type'] = 'power_in'
        def net_order(record):
            record['nets'].reverse()
        def pin_source(record):
            record['components'][0]['pins'][0]['source']['unit_symbol'] = 'Forged_1_1'
        def intent_identity(record):
            record['intent_sha256'] = hashlib.sha256(b'another intent').hexdigest()
        for mutate in [value, pin_type, net_order, pin_source, intent_identity]:
            with self.subTest(mutation=mutate.__name__):
                record = read_json(FIXTURE / 'resolved.json')
                mutate(record)
                write_json(run.parent / 'resolved.json', resign(record))
                forged = run.parent / f'forged-{mutate.__name__}'
                forged.mkdir()
                capture(run.parent / 'scope.json', forged)
                checks = run_checks(forged, time.monotonic() + 90)
                self.assertEqual((checks[1]['execution'], checks[1]['outcome']), ('malformed', 'unknown'), checks)
                self.assertIn('rederivation', checks[1]['summary'])
                self.assertEqual(checks[1]['details'].get('evaluated_predicates', []), [])
                freeze_evidence(forged, checks)
                with self.assertRaises(ValueError):
                    validate_witnesses(forged, [{'requirement_id': 'parity', 'check_id': 'parity'}],
                                       [{'path': 'checks/parity.json', 'locator': 'missing_components'}])

    def test_resolved_source_without_captured_intent_is_not_supported(self):
        from agent_ee.baseline import compile_baseline
        from agent_ee.netlist_parity import validate_policy
        good = {'predicate': 'topology', 'netlist_check': 'netlist', 'multiset': 'exactly'}
        with self.assertRaisesRegex(ValueError, 'captured intent'):
            validate_policy({**good, 'source': {'kind': 'resolved', 'path': 'resolved.json'}})
        for kind in ('golden_xml', 'intent'):
            with self.assertRaises(ValueError):
                validate_policy({**good, 'source': {'kind': kind, 'path': 'x', 'intent': 'intent.json'}})
        source = {'kind': 'resolved', 'path': 'resolved.json', 'intent': 'intent.json'}
        run, _ = self.run_variant(params={'source': source})
        from agent_ee.storage import read_json
        manifest = read_json(run / 'candidate.json')
        files = dict(manifest['files'])
        del files['intent.json']
        with self.assertRaisesRegex(ValueError, 'captured'):
            compile_baseline(manifest['scope'], files, run / 'inputs')

    def test_captured_intent_or_library_drift_is_rejected_by_snapshot_and_rederivation(self):
        import time
        from agent_ee.candidate import verify_snapshot
        from agent_ee.checks import Ctx, CheckError
        from agent_ee.netlist_parity import check_netlist_parity
        from agent_ee.storage import read_json, write_json
        source = {'kind': 'resolved', 'path': 'resolved.json', 'intent': 'intent.json'}
        run, checks = self.run_variant(params={'source': source})
        self.assertEqual(checks[1]['outcome'], 'pass', checks)
        intent = read_json(run / 'inputs/intent.json')
        intent['components'][0]['value'] = 'changed'
        write_json(run / 'inputs/intent.json', intent)
        with self.assertRaises(ValueError):
            verify_snapshot(run)
        manifest = read_json(run / 'candidate.json')
        ctx = Ctx(run, manifest, manifest['scope']['checks'][1], time.monotonic() + 30)
        with self.assertRaises(CheckError) as raised:
            check_netlist_parity(ctx)
        self.assertEqual(raised.exception.execution, 'malformed')
        self.assertEqual(ctx.result['outcome'], 'unknown')

    def test_actual39_corrupt_library_is_failed_unknown_with_resolution_evidence(self):
        import time
        from agent_ee.checks import Ctx, CheckError
        from agent_ee.netlist_parity import check_netlist_parity
        from agent_ee.storage import read_json
        run, checks = self.run_variant(params={'source': {'kind': 'intent', 'path': 'intent.json'}})
        self.assertEqual(checks[1]['outcome'], 'pass', checks)
        (run / 'inputs/Owned.kicad_sym').write_text('corrupt captured library')
        manifest = read_json(run / 'candidate.json')
        ctx = Ctx(run, manifest, manifest['scope']['checks'][1], time.monotonic() + 30)
        with self.assertRaises(CheckError) as raised:
            check_netlist_parity(ctx)
        self.assertEqual(raised.exception.execution, 'failed')
        self.assertEqual(ctx.result['outcome'], 'unknown')
        self.assertIn('work/parity/resolved-intent.json', ctx.result['evidence'])
        self.assertEqual(read_json(run / 'work/parity/resolved-intent.json')['execution'], 'failed')

    def test_native_extra_part_allowed_only_by_at_least_multiset(self):
        _, checks = self.run_variant('extra', params={'predicate': 'components', 'multiset': 'at_least'})
        self.assertEqual((checks[1]['execution'], checks[1]['outcome']), ('ok', 'pass'), checks)
        self.assertEqual(checks[1]['details']['extra_components'][0]['count'], 1)

    def test_independent_initial_reviews_then_central_adjudication(self):
        import contextlib
        import io
        from unittest.mock import patch
        from agent_ee.cli import runtime_files, run_review, verify
        from agent_ee.storage import digest, read_json, write_json
        from test_capability_pipeline import NativeCapabilityPipelineTests, PROFILE_CALL
        self.calls = []
        runtime = runtime_files()
        for key in ('codex', 'codex_tool_host'):
            runtime[key] = {'path': '/usr/bin/true', 'sha256': digest('/usr/bin/true')}
        for variant, params, expected in [('control', {}, 'criteria_satisfied'),
                ('ic-swap', {}, 'defects_or_unresolved'),
                ('control', {'anchors': {'R80': {'sheet': '/', 'uuid': 'unresolved'}}}, 'incomplete')]:
            with self.subTest(variant=variant, expected=expected):
                first, _ = self.run_variant(variant, params=params)
                scope_path = first.parent / 'scope.json'
                scope = read_json(scope_path)
                (first.parent / 'parity.kicad_pcb').write_bytes((Path(__file__).parents[1] / 'fixtures/hardware-interface/interface.kicad_pcb').read_bytes())
                scope['board'] = 'parity.kicad_pcb'
                scope['inputs'].append({'path': 'parity.kicad_pcb', 'at': 'parity.kicad_pcb'})
                scope['review_profile'] = {'schema': 'agent-ee.profile/1', 'name': 'scripted-parity',
                    'reviewers': [{**PROFILE_CALL, 'role': 'circuit'}, {**PROFILE_CALL, 'role': 'physical'}],
                    'adjudicator': PROFILE_CALL}
                write_json(scope_path, scope)
                def agent(*args, **kwargs):
                    return NativeCapabilityPipelineTests.agent(self, *args, **kwargs)
                out = first.parent / 'review'
                with patch('agent_ee.cli.runtime_files', return_value=runtime), \
                     patch('agent_ee.reviewers.dispatch', side_effect=agent), \
                     contextlib.redirect_stdout(io.StringIO()):
                    status = run_review(scope_path, out, 120)
                self.assertNotIn('error', read_json(out / 'result.json'))
                with patch('agent_ee.cli.runtime_files', return_value=runtime):
                    result = verify(out)
                self.assertEqual(result['outcome'], expected, result)
                self.assertEqual(status, 0 if expected == 'criteria_satisfied' else 1, result)
                self.assertCountEqual([role for _, role in self.calls[-3:-1]], ['circuit', 'physical'])
                self.assertEqual(self.calls[-1][1], 'adjudicator')
                accepted = [f for f in result['findings'] if f.get('status') == 'accepted']
                self.assertEqual([f['local_id'] for f in accepted], ['parity'] if variant == 'ic-swap' else [])
                self.assertTrue(all(f['source'] == 'check' for f in accepted))

    def test_native_symmetric_instances_unknown_until_declared_uuid_anchor(self):
        from uuid import NAMESPACE_URL, uuid5
        params = {'predicate': 'instance', 'instance_components': ['R80']}
        _, checks = self.run_variant('symmetric', params=params, golden_fixture='symmetric.xml')
        report = checks[1]
        self.assertEqual((report['execution'], report['outcome']), ('unsupported', 'unknown'), report)
        self.assertEqual(report['details']['component_matches']['R80'], ['R80', 'R90'])
        self.assertTrue(report['details']['topology_equivalent'])
        # This immutable UUID is declared before export from the original owned fixture identity.
        params['anchors'] = {'R80': {'sheet': '/',
            'uuid': str(uuid5(NAMESPACE_URL, 'agent-ee-owned-netlist-parity/R80'))}}
        _, checks = self.run_variant('symmetric', params=params, golden_fixture='symmetric.xml')
        self.assertEqual((checks[1]['execution'], checks[1]['outcome']), ('ok', 'pass'), checks)

    def test_declared_unconnected_pins_accept_only_compatible_auto_singletons(self):
        for variant in ('no-connects', 'unconnected'):
            with self.subTest(variant=variant):
                _, checks = self.run_variant(variant, params={
                    'source': {'kind': 'intent', 'path': 'nc-intent.json'}})
                report = checks[1]
                self.assertEqual((report['execution'], report['outcome']), ('ok', 'pass'), report)
                normalized = report['details']['normalized_no_connects']
                self.assertEqual(len(normalized), 2)
                self.assertTrue(all(n['native_no_connect_marker'] == (variant == 'no-connects') for n in normalized))
                self.assertEqual(report['details']['scores']['jaccard'], 1.0)

    def test_native_identical_part_swap_defeats_collapsed_token_oracle(self):
        from agent_ee.native_netlist import read_native_inventory
        run, checks = self.run_variant('identical-pin-swap', params={
            'source': {'kind': 'intent', 'path': 'intent.json'}})
        graph = read_native_inventory(run / 'work/netlist/design.xml')
        # Independently worked bags: a lossy per-net SET oracle sees no change.
        expected_bags = {
            'VBUS': {('Owned:Port', 'port', '1'), ('Owned:R', '10k', '1')},
            'VCC': {('Owned:Port', 'port', '2'), ('Owned:R', '10k', '1'), ('Owned:R', '10k', '2')},
            'GND': {('Owned:Port', 'port', '3'), ('Owned:R', '10k', '2')}}
        collapsed = {net['name'].removeprefix('/'): {
            (graph['components'][cid]['lib_id'], graph['components'][cid]['value'], pin)
            for cid, pin in net['terminals']} for net in graph['nets'].values()}
        self.assertEqual(collapsed, expected_bags)
        self.assertEqual((checks[1]['execution'], checks[1]['outcome']), ('ok', 'defect'), checks)
        self.assertEqual(set(checks[1]['details']['missing_nets']), {'output', 'ground'})


class FailureBoundaryTests(unittest.TestCase):
    def test_native_duplicate_id_pin_membership_and_entities_are_malformed(self):
        import copy
        import tempfile
        import xml.etree.ElementTree as ET
        from agent_ee.native_netlist import read_native_inventory
        def dup_ref(root):
            root.find('components').append(copy.deepcopy(root.find('./components/comp')))
        def dup_uuid(root):
            comps = root.findall('./components/comp')
            comps[1].find('tstamps').text = comps[0].findtext('tstamps')
        def dup_pin(root):
            pins = root.find('./libparts/libpart/pins')
            pins.append(copy.deepcopy(pins[0]))
        def dup_membership(root):
            root.findall('./nets/net')[1].append(copy.deepcopy(root.find('./nets/net/node')))
        def dup_net(root):
            root.find('nets').append(copy.deepcopy(root.find('./nets/net')))
        def undeclared_unit_pin(root):
            root.find('./components/comp/units/unit/pins/pin').set('num', '999')
        for mutate in [dup_ref, dup_uuid, dup_pin, dup_membership, dup_net, undeclared_unit_pin]:
            with self.subTest(mutation=mutate.__name__), tempfile.TemporaryDirectory() as tmp:
                tree = ET.parse(FIXTURE / 'native.xml')
                mutate(tree.getroot())
                path = Path(tmp) / 'native.xml'
                tree.write(path)
                with self.assertRaises(ValueError):
                    read_native_inventory(path)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'native.xml'
            path.write_text('<!DOCTYPE export [<!ENTITY e "expanded">]><export version="E">&e;</export>')
            with self.assertRaisesRegex(ValueError, 'entities'):
                read_native_inventory(path)

    def test_resolved_inventory_rejects_duplicate_and_unaccounted_pins(self):
        import copy
        record = json.loads((FIXTURE / 'resolved.json').read_text())
        for mutation in [lambda r: r['components'].append(copy.deepcopy(r['components'][0])),
                         lambda r: r['nets'][0]['terminals'].append(copy.deepcopy(r['nets'][0]['terminals'][0])),
                         lambda r: r['nets'][0]['terminals'].pop(),
                         lambda r: r['no_connects'].append({'component': 'series', 'pin': '1'})]:
            with self.subTest(mutation=mutation):
                changed = copy.deepcopy(record)
                mutation(changed)
                import hashlib
                payload = {k: v for k, v in changed.items() if k != 'canonical_sha256'}
                changed['canonical_sha256'] = hashlib.sha256(json.dumps(payload, sort_keys=True,
                    separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
                with self.assertRaisesRegex(ValueError, 'inventory|terminal|pin|component|net'):
                    resolved_inventory(changed)

    def test_input_bounds_fail_unknown_before_unbounded_reads_or_resolution(self):
        import tempfile
        from unittest.mock import patch
        from agent_ee.netlist_parity import _source_inventory, Unsupported, read_json_source
        from agent_ee.native_netlist import read_native_inventory
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = json.loads((FIXTURE / 'intent.json').read_text())
            (root / 'intent.json').write_text(json.dumps(raw))
            (root / 'Owned.kicad_sym').write_bytes(b'x' * 100)
            with patch('agent_ee.netlist_parity.MAX_LIBRARY_BYTES', 64), self.assertRaisesRegex(Unsupported, 'library byte'):
                _source_inventory(root, {'source': {'kind': 'intent', 'path': 'intent.json'}})
            raw['components'] *= 129
            (root / 'intent.json').write_text(json.dumps(raw))
            with self.assertRaisesRegex(Unsupported, 'component inventory'):
                _source_inventory(root, {'source': {'kind': 'intent', 'path': 'intent.json'}})
            with patch('agent_ee.netlist_parity.MAX_BYTES', 8), self.assertRaisesRegex(Unsupported, 'byte limit'):
                read_json_source(root / 'intent.json')
            with patch('agent_ee.native_netlist.MAX_BYTES', 8), self.assertRaisesRegex(Unsupported, 'byte limit'):
                read_native_inventory(FIXTURE / 'native.xml')

    def test_policy_rejects_mixed_identities_and_unbounded_search(self):
        from agent_ee.netlist_parity import validate_policy
        good = {'source': {'kind': 'golden_xml', 'path': 'golden.xml'}, 'predicate': 'topology',
                'netlist_check': 'netlist', 'multiset': 'exactly'}
        for patch in [{'anchors': {'series': {'reference': 'R80', 'uuid': 'ambiguous'}}}, {'max_search': 0},
                      {'max_search': True}, {'max_search': 100001}, {'multiset': 'at_least'},
                      {'source': {'kind': 'guessed', 'path': 'golden.xml'}}, {'predicate': 'instance'},
                      {'net_name_policy': ''}, {'anchors': {'series': {'reference': 'R80'},
                                                            'shunt': {'reference': 'R80'}}},
                      {'predicate': 'components', 'net_name_policy': 'names.json'}]:
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                validate_policy({**good, **patch})

    def test_net_name_policy_requires_capture_and_baseline_provenance(self):
        import copy
        import tempfile
        from agent_ee.netlist_parity import capability
        from agent_ee.storage import write_json
        names = {'schema': 'agent-ee.net-name-policy/1', 'requirement': {
            'id': 'parity', 'source': {'path': 'requirements.md', 'locator': 'Names'}},
            'required': [{'name': 'VBUS', 'anchors': [{'reference': 'X9', 'pin': '1'}]}]}
        params = {'source': {'kind': 'golden_xml', 'path': 'golden.xml'}, 'predicate': 'topology',
                  'netlist_check': 'netlist', 'multiset': 'exactly', 'net_name_policy': 'names.json'}
        scope = {'checks': [{'id': 'netlist', 'kind': 'netlist'},
                            {'id': 'parity', 'kind': 'netlist_parity'}],
                 'baseline': {'requirements': [{**names['requirement'], 'checks': ['parity']}]}}
        files = dict.fromkeys(['golden.xml', 'names.json', 'requirements.md'], 'hash')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_json(root / 'names.json', names)
            descriptor = capability(params, scope, files, root, 'parity')
            self.assertEqual(descriptor['name_policy'], names)
            self.assertEqual(set(descriptor['immutable_inputs']), set(files))
            for missing in ('names.json', 'requirements.md'):
                with self.subTest(missing=missing), self.assertRaises(ValueError):
                    capability(params, scope, {k: v for k, v in files.items() if k != missing}, root, 'parity')
            changed = copy.deepcopy(names)
            changed['requirement']['source']['locator'] = 'unbound source'
            write_json(root / 'names.json', changed)
            with self.assertRaisesRegex(ValueError, 'baseline requirement source'):
                capability(params, scope, files, root, 'parity')
            changed = copy.deepcopy(names)
            changed['required'].append(copy.deepcopy(changed['required'][0]))
            write_json(root / 'names.json', changed)
            with self.assertRaisesRegex(ValueError, 'duplicate required name'):
                capability(params, scope, files, root, 'parity')

    def test_json_duplicate_fields_fail_closed(self):
        import tempfile
        from agent_ee.netlist_parity import read_json_source
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'intent.json'
            path.write_text('{"schema":"one","schema":"two"}')
            with self.assertRaisesRegex(ValueError, 'duplicate JSON'):
                read_json_source(path)

    def test_excessively_nested_json_is_a_malformed_input(self):
        import tempfile
        from agent_ee.netlist_parity import read_json_source
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'connectivity.json'
            path.write_bytes(b'[' * 10000 + b'0' + b']' * 10000)
            with self.assertRaisesRegex(ValueError, 'nesting'):
                read_json_source(path)
