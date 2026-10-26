"""Owner-declared relations, never inferred from candidate naming."""
import copy
import importlib.util
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from agent_ee.hardware_interface import evaluate, validate_contract, read_inventory
from agent_ee.baseline import freeze_evidence, verify_evidence, validate_witnesses, load_baseline
from agent_ee.candidate import capture, verify_snapshot
from agent_ee.checks import run_checks
from agent_ee.storage import read_json, write_json
from tests.toolchain import require_toolchain

FIXTURE = Path(__file__).resolve().parents[1] / 'fixtures/hardware-interface'
spec = importlib.util.spec_from_file_location('interface_fixture', FIXTURE / 'generate.py')
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)

NC_PROBE = ['unconnected-(PROBE-Pad1)']


def global_label(name, x, y, tag):
    return (f'(global_label "{name}" (shape input) (at {x} {y} 0) (effects (font (size 1 1))) '
            f'(uuid "{generator.uid(tag)}"))')


def wire(a, b, tag):
    return (f'(wire (pts (xy {a[0]} {a[1]}) (xy {b[0]} {b[1]})) (stroke (width 0) (type default)) '
            f'(uuid "{generator.uid(tag)}"))')


def powered():
    """The disconnect fixture plus an owned power symbol named by its Value."""
    definition = ('(symbol "PowerOwned:Rail" (power) (pin_names (offset 0)) (in_bom yes) (on_board yes) '
                  + generator.property_('Reference', '#PWR') + generator.property_('Value', 'Rail')
                  + '(symbol "Rail_0_1" (pin power_in line (at 0 0 90) (length 0) hide '
                  '(name "Rail" (effects (font (size 1 1)))) (number "1" (effects (font (size 1 1)))))))')
    return generator.schematic('disconnect').replace('(lib_symbols ', '(lib_symbols ' + definition, 1)


def rail(x, y, value=NC_PROBE[0]):
    return (f'(symbol (lib_id "PowerOwned:Rail") (at {x} {y} 0) (unit 1) (in_bom yes) (on_board yes) '
            f'(dnp no) (uuid "{generator.uid("rail")}")' + generator.property_('Reference', '#PWR01')
            + generator.property_('Value', value) + f'(pin "1" (uuid "{generator.uid("rail-pin")}"))'
            + f'(instances (project "interface" (path "/{generator.uid("root")}" '
            '(reference "#PWR01") (unit 1)))))')


def placed(items, name):
    """Expand '{label}(x, y)' placeholders into one global label spelling."""
    out = []
    for item in items:
        if item.startswith('{label}'):
            x, y = (v.strip() for v in item[len('{label}('):-1].split(','))
            item = global_label(name, x, y, 'label')
        out.append(item)
    return out


def no_connect_marker(x, y, tag):
    return f'(no_connect (at {x} {y}) (uuid "{generator.uid(tag)}"))'


def no_connect_probe(hidden=False):
    """The disconnect fixture with the probe pin of library type no_connect."""
    pin = '(symbol "Probe_1_1" (pin passive line (at 0 -0.0 0)'
    schematic = generator.schematic('disconnect')
    assert pin in schematic
    return schematic.replace(pin, pin.replace('passive', 'no_connect') + (' hide' if hidden else ''))


class InterfaceTests(unittest.TestCase):
    def setUp(self):
        self.contract = read_json(FIXTURE / 'contract.json')

    def test_contract_rejects_empty_predicates(self):
        self.contract['invariants'] = []
        with self.assertRaisesRegex(ValueError, 'invariants'):
            validate_contract(self.contract)

    def test_component_free_contract_requires_cited_unknown_obligations(self):
        contract = dict(schema='agent-ee.interface/1', components={}, terminals={}, allowed_edits=[],
                        invariants=[dict(id='pending', kind='unresolved', reason='Not yet bound',
                                         requirement='interface', source=self.contract['invariants'][0]['source'])])
        with self.assertRaisesRegex(ValueError, 'component-free contract'):
            validate_contract(contract)

    def test_external_block_citations_cannot_supply_board_requirement_authority(self):
        from agent_ee.hardware_interface import capability
        source = dict(kind='pinned-external', repository='owned', commit='a' * 40,
                      path='requirements.md', blob='b' * 40, sha256='c' * 64)
        for invariant in self.contract['invariants']:
            invariant['source'] = source
        rid = self.contract['invariants'][0]['requirement']
        scope = dict(baseline=dict(requirements=[dict(id=rid, source=source, checks=['interface'])]))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_json(root / 'contract.json', self.contract)
            params = dict(contract='contract.json', netlist_check='native')
            with self.assertRaisesRegex(ValueError, 'exact captured requirement source'):
                capability(params, scope, {'contract.json'}, root, 'interface')
            self.assertEqual(capability(params, scope, {'contract.json'}, root,
                                        'interface', block_sources=True)['contract'], self.contract)

    def test_contract_refuses_ambiguous_or_wildcard_authority(self):
        for mutation in (
            lambda c: c['components']['load']['locations'].append({'sheet':'/','reference':'X1'}),
            lambda c: c['invariants'].append(copy.deepcopy(c['invariants'][0])),
            lambda c: c['allowed_edits'][0].update(property='*'),
            lambda c: c['invariants'][0].update(kind='transparent_resistor'),
            lambda c: c['invariants'][2].update(names=[]),
            lambda c: c['terminals']['in'].update(component='absent'),
            lambda c: c['terminals'].update(another_in=copy.deepcopy(c['terminals']['in'])),
        ):
            contract = copy.deepcopy(self.contract)
            mutation(contract)
            with self.subTest(contract=contract), self.assertRaises(ValueError):
                validate_contract(contract)

    def test_duplicate_json_identity_is_not_silently_overwritten(self):
        from agent_ee.hardware_interface import load_contract_unverified as load_contract
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'contract.json'
            original=(FIXTURE/'contract.json').read_text()
            path.write_text(original.replace('"schema":', '"schema":"ignored", "schema":'))
            with self.assertRaisesRegex(ValueError,'duplicate JSON'):
                load_contract(Path(tmp),'contract.json')

    def test_source_label_connection_refuses_unmodelled_geometry(self):
        from agent_ee.hardware_interface import label_connection
        from agent_ee.symbol_library import parse_sexpr
        probe = {'sheet': '/', 'reference': 'PROBE', 'uuid': generator.uid('PROBE'),
                 'symbol': 'ConnectorOwned:Probe'}
        base = generator.schematic('disconnect')
        place = '(symbol (lib_id "ConnectorOwned:Probe") (at 120 50 0)'
        def classify(extra='', schematic=base, component=probe):
            source = parse_sexpr((schematic[:-2] + extra + global_label(NC_PROBE[0], 140, 80, 'g') + ')\n').encode())
            return label_connection({'interface.kicad_sch': source}, generator.uid('root'), component, '1', NC_PROBE[0])
        self.assertEqual(classify()[0], 'disconnected')
        self.assertEqual(classify(global_label(NC_PROBE[0], 120, 50, 'on-pin'))[0], 'connected')
        start, depth = base.index(place), 0
        for end, char in enumerate(base[start:], start):
            depth += {'(': 1, ')': -1}.get(char, 0)
            if not depth:
                break
        symbol = base[start:end + 1]
        cases = {
            'unknown item': (classify('(rule_area (polyline (pts (xy 0 0) (xy 1 1))))'), 'unsupported native schematic item'),
            'oblique rotation': (classify(schematic=base.replace(place, place[:-2] + '45)')), 'placement'),
            'duplicate source UUID': (classify(symbol), 'one captured source symbol'),
            'instance mismatch': (classify(component={**probe, 'reference': 'PROBE2'}), 'does not match'),
            'library mismatch': (classify(component={**probe, 'symbol': 'Device:R'}), 'does not match'),
            'hidden pin': (classify(schematic=base.replace('(symbol "Probe_1_1" (pin passive line (at 0 -0.0 0)',
                '(symbol "Probe_1_1" (pin passive line (at 0 -0.0 0) hide')), 'no unique visible'),
            'bus at pin': (classify('(bus (pts (xy 120 50) (xy 120 60)) (stroke (width 0) (type default)) '
                                    f'(uuid "{generator.uid("bus")}"))'), 'bus may touch'),
            'directive label': (classify('(netclass_flag "" (length 2.54) (shape round) (at 120 50 0) '
                                         f'(effects (font (size 1 1))) (uuid "{generator.uid("flag")}"))'),
                                'netclass_flag may touch'),
        }
        for name, ((status, reason), expected) in cases.items():
            with self.subTest(case=name):
                self.assertEqual(status, 'unknown')
                self.assertIn(expected, reason)
        # Unmodelled items elsewhere cannot break an exact native route.
        self.assertEqual(classify('(rule_area (polyline (pts (xy 0 0) (xy 1 1))))'
                                  + global_label(NC_PROBE[0], 120, 50, 'on-pin'))[0], 'connected')

    def test_library_no_connect_pin_is_isolated_only_by_its_own_explicit_marker(self):
        from agent_ee.hardware_interface import label_connection
        from agent_ee.symbol_library import parse_sexpr
        probe = {'sheet': '/', 'reference': 'PROBE', 'uuid': generator.uid('PROBE'),
                 'symbol': 'ConnectorOwned:Probe'}
        pin = '(symbol "Probe_1_1" (pin passive line (at 0 -0.0 0)'
        marker = no_connect_marker(120, 50, 'nc-marker')
        def classify(extra, hidden=False, name=NC_PROBE[0]):
            schematic = no_connect_probe(hidden)
            source = parse_sexpr((schematic[:-2] + extra + ')\n').encode())
            return label_connection({'interface.kicad_sch': source}, generator.uid('root'), probe, '1', name)
        for hidden in (False, True):
            with self.subTest(hidden=hidden):
                status, locators = classify(marker + global_label(NC_PROBE[0], 140, 80, 'far'), hidden)
                self.assertEqual(status, 'disconnected')
                self.assertEqual(locators, [f'interface.kicad_sch: no_connect at (120, 50) mm, UUID '
                                            f'{generator.uid("nc-marker")} alone isolates library '
                                            'no_connect PROBE pin 1'])
        cases = {
            'no marker': ('', 'no unique explicit no_connect marker'),
            'offset marker': (no_connect_marker(120.0001, 50, 'nc-off'), 'no unique explicit no_connect marker'),
            'two markers': (marker + no_connect_marker(120, 50, 'nc-twin'), 'no unique explicit no_connect marker'),
            'unmodelled item': (marker + '(rule_area (polyline (pts (xy 0 0) (xy 1 1))))',
                                'unsupported native schematic item'),
            'wire in margin': (marker + wire((120.01, 50), (130, 50), 'near'), 'wire may touch PROBE pin 1'),
            'junction on pin': (marker + f'(junction (at 120 50) (diameter 0) (color 0 0 0 0) '
                                f'(uuid "{generator.uid("j")}"))', 'junction may touch'),
            'second marker in margin': (marker + no_connect_marker(120.01, 50, 'nc-near'), 'no_connect may touch'),
            'marker chain': (marker + no_connect_marker(120.01, 50, 'nc-a') + global_label('X', 120.02, 50, 'chain'),
                             'global_label, no_connect may touch'),
            # Never 'connected' by a global label spelling, even exactly on the pin.
            'exact global label on pin': (marker + global_label(NC_PROBE[0], 120, 50, 'on-pin'),
                                          'global_label may touch'),
            'local label': (marker + '(label "L" (at 120 50 0) (effects (font (size 1 1))) '
                            f'(uuid "{generator.uid("local")}"))', 'label may touch'),
            'bus entry': (marker + f'(bus_entry (at 120 50) (size 2.54 2.54) (stroke (width 0) (type default)) '
                          f'(uuid "{generator.uid("entry")}"))', 'bus_entry may touch'),
        }
        for hidden in (False, True):
            for case, (extra, expected) in cases.items():
                with self.subTest(case=case, hidden=hidden):
                    status, reason = classify(extra, hidden)
                    self.assertEqual(status, 'unknown')
                    self.assertIn(expected, reason)
        # Hidden pins of any other type still lack a visible source point.
        hidden_passive = generator.schematic('disconnect').replace(pin, pin + ' hide')
        source = parse_sexpr((hidden_passive[:-2] + marker + ')\n').encode())
        status, reason = label_connection({'interface.kicad_sch': source}, generator.uid('root'),
                                          probe, '1', NC_PROBE[0])
        self.assertEqual(status, 'unknown')
        self.assertIn('no unique visible source point', reason)

    def test_generated_singleton_name_is_judged_by_geometry_not_source_text(self):
        from agent_ee.hardware_interface import qualify_global_names
        from agent_ee.symbol_library import parse_sexpr
        probe = {'sheet': '/', 'reference': 'PROBE', 'uuid': generator.uid('PROBE'),
                 'symbol': 'ConnectorOwned:Probe'}
        def qualify(items, pins=(('PROBE', '1'),)):
            inventory = {'components': {('/', 'PROBE'): probe}, 'memberships': {('PROBE', '1'): '9'},
                         'nets': {'9': NC_PROBE[0]}, 'disconnected': {('PROBE', '1')},
                         'unresolved': {}, 'sources': {}}
            source = parse_sexpr((powered()[:-2] + ''.join(items) + ')\n').encode())
            return qualify_global_names(inventory, {'interface.kicad_sch': source}, generator.uid('root'), set(pins))
        # No literal source spelling equals the exported name; the label still touches the pin.
        dynamic = 'unconnected-(${PROBE:REFERENCE}-Pad1)'
        for items in ([global_label(dynamic, 120, 50, 'dyn')], [rail(120, 50, dynamic)]):
            inventory = qualify(items)
            self.assertNotIn(('PROBE', '1'), inventory['disconnected'])
            self.assertIn('may touch PROBE pin 1', inventory['unresolved'][('PROBE', '1')])
            self.assertEqual(inventory['sources'][('PROBE', '1')]['status'], 'unknown')
        # Isolation is proved from geometry whatever the far item's spelling.
        inventory = qualify([global_label(dynamic, 140, 80, 'dyn')])
        self.assertEqual((inventory['disconnected'], inventory['unresolved'], inventory['sources']),
                         ({('PROBE', '1')}, {}, {}))
        # An unmodelled item anywhere in the owning file leaves isolation unproven.
        inventory = qualify(['(rule_area (polyline (pts (xy 0 0) (xy 1 1))))'])
        self.assertIn('unsupported native schematic item', inventory['unresolved'][('PROBE', '1')])
        # Only declared terminals are judged; others are neither poisoned nor reported.
        inventory = qualify([global_label(dynamic, 120, 50, 'dyn')], pins=())
        self.assertEqual((inventory['unresolved'], inventory['sources']), ({}, {}))


class NativeInterfaceTests(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.counter = 0

    def run_variant(self, variant='control', *, resistor='10k', contract=None, mutate_xml=None, schematic=None,
                    interface_only=False):
        self.counter += 1
        case = self.root / str(self.counter)
        case.mkdir()
        (case / 'interface.kicad_sch').write_text(schematic if schematic is not None else generator.schematic(variant, resistor))
        (case / 'interface.kicad_pcb').write_bytes((FIXTURE / 'interface.kicad_pcb').read_bytes())
        (case / 'requirements.md').write_bytes((FIXTURE / 'requirements.md').read_bytes())
        write_json(case / 'contract.json', contract or read_json(FIXTURE / 'contract.json'))
        scope = read_json(FIXTURE / 'control.scope.json')
        if interface_only:
            scope['checks'] = scope['checks'][:2]
            scope['baseline']['requirements'] = scope['baseline']['requirements'][:1]
        write_json(case / 'scope.json', scope)
        run = case / 'run'
        run.mkdir()
        capture(case / 'scope.json', run)
        if mutate_xml:
            # Export once with the native producer, then test malformed reader inputs.
            netlist = run_checks(run, time.monotonic()+60)[0]
            self.assertEqual(netlist['execution'], 'ok', netlist)
            xml = run / 'work/netlist/design.xml'
            tree = ET.parse(xml)
            mutate_xml(tree.getroot())
            tree.write(xml)
            from agent_ee.checks import Ctx, check_hardware_interface, CheckError
            manifest = read_json(run / 'candidate.json')
            ctx = Ctx(run, manifest, scope['checks'][1], time.monotonic()+60)
            try:
                check_hardware_interface(ctx)
            except CheckError as exc:
                ctx.result.update(execution=exc.execution, outcome='unknown', summary=str(exc))
            return run, {'interface':ctx.result}
        checks = run_checks(run, time.monotonic()+90)
        return run, {c['id']:c for c in checks}

    def test_owned_control_and_defects_match_predeclared_oracle(self):
        expectations = read_json(FIXTURE / 'expectations.json')
        baseline = None
        for variant, expected in expectations.items():
            with self.subTest(variant=variant):
                run, checks = self.run_variant(variant, resistor='100k' if variant=='wrong-value' else '10k')
                result = checks['interface']
                self.assertEqual(checks['netlist']['execution'], 'ok', checks)
                self.assertEqual(result['execution'], 'ok', result)
                self.assertEqual(result['details']['violations'], expected, result)
                self.assertEqual(result['outcome'], 'defect' if expected else 'pass', result)
                current = load_baseline(run)['baseline_id']
                if baseline is None:
                    baseline = current
                self.assertEqual(current, baseline)
                if variant in ('control', 'wrong-value'):
                    self.assertEqual(checks['response']['execution'], 'ok', checks['response'])
                    self.assertEqual(checks['response']['outcome'], 'pass' if variant=='control' else 'defect', checks['response'])
                    import math
                    r=10000 if variant=='control' else 100000
                    expected_voltage=1e6/(1e6+r)*(1-math.exp(-.001/((r*1e6/(r+1e6))*100e-9)))
                    measured=checks['response']['details']['measurements']['vout_1ms']['measured']
                    self.assertAlmostEqual(measured,expected_voltage,delta=.001)
                freeze_evidence(run, list(checks.values()))
                verify_evidence(run, list(checks.values()), required=True)

    def test_disconnected_terminal_cannot_satisfy_any_conductor_predicate(self):
        contract = read_json(FIXTURE / 'contract.json')
        contract['invariants'].append({
            **copy.deepcopy(contract['invariants'][0]), 'id': 'probe-isolated',
            'kind': 'different_net', 'terminals': ['in', 'probe-tip']})
        del contract['invariants'][-1]['component']
        del contract['invariants'][-1]['populated']
        run, checks = self.run_variant('disconnect', contract=contract)
        records = {r['id']: r for r in checks['interface']['details']['invariants']}
        self.assertEqual(records['probe-isolated']['status'], 'violation')
        self.assertEqual(records['probe-wire']['status'], 'violation')
        inventory = read_inventory(run / 'work/netlist/design.xml')
        code = inventory['memberships'][('PROBE', '1')]
        self.assertEqual(inventory['nets'][code], 'unconnected-(PROBE-Pad1)')
        self.assertEqual(records['probe-isolated']['actual']['terminals'][1]['net_code'], code)

    def test_native_named_nc_lookalike_and_explicit_no_connect(self):
        contract = read_json(FIXTURE / 'contract.json')
        named = copy.deepcopy(contract['invariants'][3])
        named.update(id='probe-name', terminal='probe-tip', names=['unconnected-(PROBE-Pad1)'])
        contract['invariants'].append(named)
        schematic = generator.schematic().replace('(label "OUT"', '(label "unconnected-(PROBE-Pad1)"')
        # KiCad qualifies intentional local labels with the sheet path.
        for invariant in contract['invariants']:
            if invariant['kind'] == 'named_net' and '/OUT' in invariant['names']:
                invariant['names'] = ['/unconnected-(PROBE-Pad1)']
        contract['invariants'][-1]['names'] = ['/unconnected-(PROBE-Pad1)']
        run, checks = self.run_variant(contract=contract, schematic=schematic)
        self.assertEqual(checks['interface']['outcome'], 'pass', checks)
        inventory = read_inventory(run / 'work/netlist/design.xml')
        self.assertNotIn(('PROBE', '1'), inventory['disconnected'])
        self.assertEqual(next(r for r in checks['interface']['details']['invariants']
            if r['id'] == 'anonymous-wire')['status'], 'satisfied')
        schematic = generator.schematic('disconnect')
        schematic = schematic[:-2] + (
            f'(no_connect (at 120 50) (uuid "{generator.uid("explicit-nc")}")))\n')
        contract = read_json(FIXTURE / 'contract.json')
        contract['invariants'].append({**named, 'names': ['unconnected-(PROBE-Pad1)']})
        _, checks = self.run_variant(contract=contract, schematic=schematic)
        records = {r['id']: r for r in checks['interface']['details']['invariants']}
        self.assertEqual(records['probe-name']['status'], 'violation')
        self.assertEqual(records['probe-wire']['status'], 'violation')

    def test_native_library_no_connect_pin_with_explicit_marker_is_satisfied(self):
        contract = read_json(FIXTURE / 'contract.json')
        isolated = {**copy.deepcopy(contract['invariants'][1]), 'id': 'probe-nc', 'kind': 'no_connect',
                    'terminal': 'probe-tip'}
        del isolated['component'], isolated['populated']
        contract['invariants'] = [isolated]
        contract['allowed_edits'] = []
        marker = no_connect_marker(120, 50, 'nc-marker')
        for hidden in (False, True):
            with self.subTest(hidden=hidden, marker=True):
                schematic = no_connect_probe(hidden)
                run, checks = self.run_variant(contract=contract, interface_only=True,
                                               schematic=schematic[:-2] + marker + ')\n')
                inventory = read_inventory(run / 'work/netlist/design.xml')
                self.assertEqual(inventory['nets'][inventory['memberships'][('PROBE', '1')]], NC_PROBE[0])
                self.assertEqual((checks['interface']['execution'], checks['interface']['outcome']),
                                 ('ok', 'pass'), checks)
                record = checks['interface']['details']['invariants'][0]
                self.assertEqual(record['status'], 'satisfied', record)
                source = record['actual']['global_label_sources'][0]
                self.assertEqual(source['status'], 'disconnected', source)
                self.assertIn(generator.uid('nc-marker'), source['locators'][0])
            # Without its own marker, or with anything else in the margin, isolation stays unproven.
            for extra, reason in (('', 'no unique explicit no_connect marker'),
                                  (marker + wire((120.01, 50), (130, 50), 'near'), 'wire may touch')):
                with self.subTest(hidden=hidden, extra=reason):
                    _, checks = self.run_variant(contract=contract, interface_only=True,
                                                 schematic=no_connect_probe(hidden)[:-2] + extra + ')\n')
                    self.assertEqual((checks['interface']['execution'], checks['interface']['outcome']),
                                     ('unsupported', 'unknown'), checks)
                    record = checks['interface']['details']['invariants'][0]
                    self.assertEqual(record['status'], 'not_evaluated', record)
                    self.assertIn(reason, record['actual']['reason'])

    def test_native_global_label_exact_nc_name_is_a_conductor(self):
        contract = read_json(FIXTURE / 'contract.json')
        named = copy.deepcopy(next(i for i in contract['invariants'] if i['kind'] == 'named_net'))
        named.update(id='probe-global', terminal='probe-tip', names=['unconnected-(PROBE-Pad1)'])
        contract['invariants'] = [named]
        contract['allowed_edits'] = []
        schematic = generator.schematic('disconnect')
        label = ('(global_label "unconnected-(PROBE-Pad1)" (shape input) (at 120 50 0) '
                 f'(effects (font (size 1 1))) (uuid "{generator.uid("global-nc")}"))')
        schematic = schematic[:-2] + label + ')\n'
        _, checks = self.run_variant(contract=contract, schematic=schematic)
        self.assertEqual(checks['interface']['outcome'], 'pass', checks)
        record = checks['interface']['details']['invariants'][0]
        self.assertEqual(record['actual']['global_label_sources'][0]['status'], 'connected', record)
        self.assertIn('interface.kicad_sch: global_label unconnected-(PROBE-Pad1) at (120, 50) mm, '
                      f'UUID {generator.uid("global-nc")} joins PROBE pin 1', record['locators'])

    def global_probe(self, items, *, names=NC_PROBE, schematic=None, relation=False):
        """One owner named_net on the probe pin, plus optional unaffected relations."""
        contract = read_json(FIXTURE / 'contract.json')
        named = copy.deepcopy(next(i for i in contract['invariants'] if i['kind'] == 'named_net'))
        named.update(id='probe-name', terminal='probe-tip', names=names)
        if relation:
            isolated = copy.deepcopy(next(i for i in contract['invariants'] if i['id'] == 'series-isolation'))
            isolated.update(id='probe-isolated', terminals=['in', 'probe-tip'])
            contract['invariants'] = [named, isolated] + [
                i for i in contract['invariants'] if i['id'] in ('probe-wire', 'load-present')]
        else:
            contract['invariants'] = [named]
        contract['allowed_edits'] = []
        schematic = schematic or generator.schematic('disconnect')
        run, checks = self.run_variant(contract=contract, schematic=schematic[:-2] + ''.join(items) + ')\n',
                                       interface_only=True)
        self.assertEqual(checks['netlist']['execution'], 'ok', checks)
        inventory = read_inventory(run / 'work/netlist/design.xml')
        net = inventory['nets'][inventory['memberships'][('PROBE', '1')]]
        return net, checks['interface'], {r['id']: r for r in checks['interface']['details']['invariants']}

    def test_offpin_global_label_spelling_is_not_connection_evidence(self):
        # Exact independent-review regression: label (140,80), PROBE pin 1 at (120,50).
        net, check, records = self.global_probe([global_label(NC_PROBE[0], 140, 80, 'global-offpin')],
                                                relation=True)
        self.assertEqual(net, NC_PROBE[0])
        self.assertEqual((check['execution'], check['outcome']), ('ok', 'defect'), check)
        self.assertCountEqual(check['details']['violations'], ['probe-name', 'probe-isolated', 'probe-wire'])
        self.assertEqual(records['load-present']['status'], 'satisfied')
        source = records['probe-name']['actual']['global_label_sources'][0]
        self.assertEqual(source['status'], 'disconnected', source)
        self.assertIn('at (140, 80) mm', source['locators'][0])
        self.assertFalse(any('global_label' in l for l in records['probe-name']['locators']), records)
        # An explicit marker makes native de-duplicate the generated name; still disconnected.
        marker = f'(no_connect (at 120 50) (uuid "{generator.uid("explicit-nc")}"))'
        net, check, records = self.global_probe([marker, global_label(NC_PROBE[0], 140, 80, 'global-offpin')],
                                                relation=True)
        self.assertEqual(net, NC_PROBE[0] + '_1')
        self.assertEqual((check['execution'], check['outcome']), ('ok', 'defect'), check)
        self.assertEqual(records['probe-isolated']['status'], 'violation', records)
        self.assertNotIn('global_label_sources', records['probe-name']['actual'])
        # Attached to another conductor, native emits two nets with one name: refused, not chosen.
        contract = read_json(FIXTURE / 'contract.json')
        schematic = generator.schematic('disconnect')
        run, checks = self.run_variant(contract=contract, interface_only=True, schematic=schematic[:-2]
                                       + global_label(NC_PROBE[0], 140, 52.54, 'global-other') + ')\n')
        names = [n.get('name') for n in ET.parse(run / 'work/netlist/design.xml').getroot().findall('./nets/net')]
        self.assertEqual(names.count(NC_PROBE[0]), 2, names)
        self.assertEqual((checks['interface']['execution'], checks['interface']['outcome']),
                         ('unsupported', 'unknown'), checks)
        self.assertTrue(all(r['status'] == 'not_evaluated' for r in checks['interface']['details']['invariants']))
        # The conservative 10 um touch margin is inclusive; beyond it, disconnection is proved.
        for x, outcome in ((120.01, 'unknown'), (120.0101, 'defect')):
            net, check, _ = self.global_probe([global_label(NC_PROBE[0], x, 50, 'global-near')])
            self.assertEqual((net, check['outcome']), (NC_PROBE[0], outcome), check)

    def test_wired_global_label_qualifies_singleton_conductor(self):
        wire_down = wire((120, 50), (120, 70), 'down')
        cases = {
            'endpoint-chain': [wire_down, wire((120, 70), (130, 70), 'across'), '{label}(130, 70)'],
            'wire-interior': [wire_down, '{label}(120, 60)'],
            'junction-tee': [wire_down, wire((120, 60), (130, 60), 'tee'),
                             f'(junction (at 120 60) (diameter 0) (color 0 0 0 0) (uuid "{generator.uid("j")}"))',
                             '{label}(130, 60)'],
        }
        for name, items in cases.items():
            with self.subTest(case=name):
                # Native oracle: an ordinary global label at the same point names the pin.
                net, check, _ = self.global_probe(placed(items, 'TIPNET'), names=['TIPNET'])
                self.assertEqual((net, check['outcome']), ('TIPNET', 'pass'), check)
                net, check, records = self.global_probe(placed(items, NC_PROBE[0]))
                self.assertEqual((net, check['outcome']), (NC_PROBE[0], 'pass'), check)
                source = records['probe-name']['actual']['global_label_sources'][0]
                self.assertEqual(source['status'], 'connected', source)
                self.assertIn(generator.uid('down'), source['locators'][0])
                self.assertEqual(records['probe-name']['locators'][1], source['locators'][0])

    def test_unproven_global_label_geometry_is_unknown_not_pass_or_violation(self):
        wire_down = wire((120, 50), (120, 70), 'down')
        cases = {
            # kicad-cli leaves these unconnected: no junction, pin on a wire interior.
            'tee-without-junction': ([wire_down, wire((120, 60), (130, 60), 'tee'), '{label}(130, 60)'], False),
            'pin-on-wire-interior': ([wire((120, 40), (120, 60), 'through'), '{label}(120, 60)'], False),
            'micrometre-off-pin': (['{label}(120.001, 50)'], False),
            # kicad-cli attaches labels about one unit from a wire; exact proof only.
            'unit-off-wire': ([wire_down, '{label}(120.0001, 60)'], True),
        }
        for name, (items, native) in cases.items():
            with self.subTest(case=name):
                net, check, _ = self.global_probe(placed(items, 'TIPNET'), names=['TIPNET'])
                self.assertEqual(net, 'TIPNET' if native else NC_PROBE[0], check)
                net, check, records = self.global_probe(placed(items, NC_PROBE[0]), relation=True)
                self.assertEqual(net, NC_PROBE[0])
                self.assertEqual((check['execution'], check['outcome']), ('unsupported', 'unknown'), check)
                self.assertEqual(records['probe-name']['status'], 'not_evaluated')
                self.assertEqual(records['probe-isolated']['status'], 'not_evaluated')
                self.assertIn('unproven', records['probe-name']['actual']['reason'])
                # A singleton cannot share a conductor whether or not the label joins it.
                self.assertEqual(records['probe-wire']['status'], 'violation')
                self.assertEqual(records['load-present']['status'], 'satisfied')
                self.assertEqual(records['probe-name']['actual']['global_label_sources'][0]['status'], 'unknown')
        # Unproven geometry never rescues a name the owner did not declare.
        _, check, records = self.global_probe(placed(cases['micrometre-off-pin'][0], NC_PROBE[0]),
                                              names=['/OUT'])
        self.assertEqual((check['outcome'], records['probe-name']['status']), ('defect', 'violation'), check)
        # A power symbol also names a net without a sheet prefix; only labels are modelled.
        net, check, records = self.global_probe([rail(120, 50)], schematic=powered())
        self.assertEqual((net, check['outcome']), (NC_PROBE[0], 'unknown'), check)
        self.assertIn('pin may touch PROBE pin 1', records['probe-name']['actual']['reason'])
        net, check, _ = self.global_probe([rail(140, 80)], schematic=powered())
        self.assertEqual((net, check['outcome']), (NC_PROBE[0], 'defect'), check)
        # Coordinates finer than the native unit are not modelled.
        _, check, records = self.global_probe([global_label(NC_PROBE[0], 140.00005, 80, 'fine')])
        self.assertEqual(check['outcome'], 'unknown', check)
        self.assertIn('whole native schematic unit', records['probe-name']['actual']['reason'])

    def test_text_variable_names_require_isolating_source_geometry(self):
        # Exact independent-review counterexample: native expands ${REF:FIELD} and
        # ${UUID:FIELD} before naming the net, so raw source text never matches.
        net, check, _ = self.global_probe([global_label('TIP_${PROBE:REFERENCE}', 120, 50, 'variable-control')],
                                          names=['TIP_PROBE'])
        self.assertEqual((net, check['outcome']), ('TIP_PROBE', 'pass'), check)
        dynamic = {'reference': 'unconnected-(${PROBE:REFERENCE}-Pad1)',
                   'uuid': 'unconnected-(${' + generator.uid('PROBE') + ':REFERENCE}-Pad1)'}
        def unknown(items, reason, schematic=None):
            net, check, records = self.global_probe(items, relation=True, schematic=schematic)
            self.assertEqual(net, NC_PROBE[0])
            self.assertEqual((check['execution'], check['outcome']), ('unsupported', 'unknown'), check)
            self.assertEqual(records['probe-name']['status'], 'not_evaluated', records)
            self.assertEqual(records['probe-isolated']['status'], 'not_evaluated', records)
            self.assertIn(reason, records['probe-name']['actual']['reason'])
            self.assertEqual(records['probe-name']['actual']['global_label_sources'][0]['status'], 'unknown')
            self.assertFalse(any('global_label' in l for l in records['probe-name']['locators']), records)
            # A singleton shares no conductor under either hypothesis.
            self.assertEqual(records['probe-wire']['status'], 'violation')
            self.assertEqual(records['load-present']['status'], 'satisfied')
        for kind, name in dynamic.items():
            with self.subTest(source=kind, at='pin'):
                unknown([global_label(name, 120, 50, 'variable-nc')], 'global_label may touch PROBE pin 1')
        name = dynamic['reference']
        with self.subTest(source='wired'):
            unknown([wire((120, 50), (120, 70), 'down'), global_label(name, 120, 70, 'variable-wired')],
                    'global_label may touch PROBE pin 1')
        with self.subTest(source='near'):
            unknown([global_label(name, 120.01, 50, 'variable-near')], 'global_label may touch PROBE pin 1')
        with self.subTest(source='far'):
            net, check, records = self.global_probe([global_label(name, 140, 80, 'variable-far')], relation=True)
            self.assertEqual(net, NC_PROBE[0])
            self.assertEqual((check['execution'], check['outcome']), ('ok', 'defect'), check)
            self.assertCountEqual(check['details']['violations'], ['probe-name', 'probe-isolated', 'probe-wire'])
            self.assertNotIn('global_label_sources', records['probe-name']['actual'])
        # Power symbols name nets through their Value, which native also expands.
        net, check, _ = self.global_probe([rail(120, 50, 'RAIL_${PROBE:REFERENCE}')], names=['RAIL_PROBE'],
                                          schematic=powered())
        self.assertEqual((net, check['outcome']), ('RAIL_PROBE', 'pass'), check)
        with self.subTest(source='power'):
            unknown([rail(120, 50, name)], 'pin may touch PROBE pin 1', schematic=powered())
        net, check, _ = self.global_probe([rail(140, 80, name)], schematic=powered())
        self.assertEqual((net, check['outcome']), (NC_PROBE[0], 'defect'), check)

    def test_global_label_source_pin_transforms_match_native(self):
        # Native offsets for lib pin (1.27, 3.81) observed with kicad-cli 10.0.7.
        offsets = {('0', ''): (1.27, -3.81), ('0', 'x'): (1.27, 3.81), ('0', 'y'): (-1.27, -3.81),
                   ('90', ''): (-3.81, -1.27), ('90', 'x'): (-3.81, 1.27), ('90', 'y'): (3.81, -1.27),
                   ('180', ''): (-1.27, 3.81), ('180', 'x'): (-1.27, -3.81), ('180', 'y'): (1.27, 3.81),
                   ('270', ''): (3.81, 1.27), ('270', 'x'): (3.81, -1.27), ('270', 'y'): (-3.81, 1.27)}
        base = generator.schematic('disconnect')
        before = '(symbol "Probe_1_1" (pin passive line (at 0 -0.0 0)'
        place = '(symbol (lib_id "ConnectorOwned:Probe") (at 120 50 0)'
        self.assertEqual((base.count(before), base.count(place)), (1, 1))
        base = base.replace(before, '(symbol "Probe_1_1" (pin passive line (at 1.27 3.81 270)')
        for (angle, mirror), (dx, dy) in offsets.items():
            with self.subTest(angle=angle, mirror=mirror):
                schematic = base.replace(place, f'(symbol (lib_id "ConnectorOwned:Probe") (at 120 50 {angle})'
                                         + (f' (mirror {mirror})' if mirror else ''))
                point = (round(120 + dx, 2), round(50 + dy, 2))
                net, check, _ = self.global_probe([global_label('TIPNET', *point, 'tip')], names=['TIPNET'],
                                                  schematic=schematic)
                self.assertEqual((net, check['outcome']), ('TIPNET', 'pass'), check)
                net, check, _ = self.global_probe([global_label(NC_PROBE[0], *point, 'tip')], schematic=schematic)
                self.assertEqual((net, check['outcome']), (NC_PROBE[0], 'pass'), check)
                if angle in ('90', '270') and mirror:
                    # Mirroring before rotating lands on a different point.
                    wrong = (round(120 - dx, 2), round(50 - dy, 2))
                    net, check, _ = self.global_probe([global_label(NC_PROBE[0], *wrong, 'tip')],
                                                      schematic=schematic)
                    self.assertEqual((net, check['outcome']), (NC_PROBE[0], 'defect'), check)

    def test_global_label_qualification_uses_the_component_sheet_file(self):
        main_uuid, sheet = generator.uid('hierarchy-root'), generator.uid('sheet-probe')
        def run(child_items, root_items='', sheet_pins=''):
            case = self.root / f'sheet-{self.counter}'
            self.counter += 1
            case.mkdir()
            child = generator.schematic('disconnect')
            for ref in ['X1', 'R1', 'C1', 'RLOAD', 'PROBE', 'R2', 'R3']:
                child = child.replace(f'(path "/{generator.uid("root")}" (reference "{ref}")',
                                      f'(path "/{main_uuid}/{sheet}" (reference "{ref}")')
            (case / 'child.kicad_sch').write_text(child[:-2] + ''.join(child_items) + ')\n')
            (case / 'interface.kicad_sch').write_text(
                f'(kicad_sch (version 20250114) (generator "eeschema") (uuid "{main_uuid}") (paper "A4") '
                f'(lib_symbols) (sheet (at 40 30) (size 20 20) (stroke (width 0) (type default)) '
                f'(fill (color 0 0 0 0)) (uuid "{sheet}") '
                '(property "Sheetname" "Probe" (at 40 30 0) (effects (font (size 1 1)))) '
                '(property "Sheetfile" "child.kicad_sch" (at 40 50 0) (effects (font (size 1 1))))'
                f'{sheet_pins}) {root_items})\n')
            for name in ['interface.kicad_pcb', 'requirements.md']:
                (case / name).write_bytes((FIXTURE / name).read_bytes())
            contract = read_json(FIXTURE / 'contract.json')
            named = copy.deepcopy(next(i for i in contract['invariants'] if i['kind'] == 'named_net'))
            named.update(id='probe-name', terminal='probe-tip', names=NC_PROBE)
            contract.update(components={'probe': contract['components']['probe']},
                            terminals={'probe-tip': contract['terminals']['probe-tip']},
                            allowed_edits=[], invariants=[named])
            contract['components']['probe']['locations'] = [{'sheet': f'/{sheet}/', 'reference': 'PROBE'}]
            write_json(case / 'contract.json', contract)
            scope = read_json(FIXTURE / 'control.scope.json')
            scope['checks'] = scope['checks'][:2]
            scope['baseline']['requirements'] = scope['baseline']['requirements'][:1]
            scope['inputs'].append({'path': 'child.kicad_sch', 'at': 'child.kicad_sch'})
            write_json(case / 'scope.json', scope)
            (case / 'run').mkdir()
            capture(case / 'scope.json', case / 'run')
            checks = run_checks(case / 'run', time.monotonic() + 90)
            self.assertEqual((checks[0]['execution'], checks[0]['details']['sheet_instances']), ('ok', 2), checks)
            inventory = read_inventory(case / 'run/work/netlist/design.xml')
            self.assertEqual(inventory['nets'][inventory['memberships'][('PROBE', '1')]], NC_PROBE[0])
            return checks[1]
        check = run([global_label(NC_PROBE[0], 120, 50, 'child-label')])
        self.assertEqual(check['outcome'], 'pass', check)
        self.assertIn('child.kicad_sch: global_label', check['details']['invariants'][0]['locators'][1])
        # The same coordinates in the root file are a different coordinate space.
        check = run([], global_label(NC_PROBE[0], 120, 50, 'root-label'))
        self.assertEqual(check['outcome'], 'defect', check)
        # A hierarchical port can carry the global name; that route is not modelled.
        port = ('(hierarchical_label "TIP" (shape input) (at 120 50 0) (effects (font (size 1 1))) '
                f'(uuid "{generator.uid("hier-tip")}"))')
        pin = (f'(pin "TIP" input (at 60 40 0) (effects (font (size 1 1))) (uuid "{generator.uid("sheet-tip")}"))')
        check = run([port], wire((60, 40), (80, 40), 'root-wire')
                    + global_label(NC_PROBE[0], 80, 40, 'root-global'), pin)
        self.assertEqual(check['outcome'], 'unknown', check)
        self.assertIn('hierarchical_label may touch PROBE pin 1',
                      check['details']['invariants'][0]['actual']['reason'])

    def test_connected_anonymous_nc_lookalike_retains_membership(self):
        def rename(root):
            net = next(n for n in root.findall('./nets/net') if n.get('name').startswith('Net-'))
            self.assertEqual(len(net.findall('node')), 2)
            net.set('name', 'unconnected-(R2-Pad2)')
        _, checks = self.run_variant(mutate_xml=rename)
        self.assertEqual(checks['interface']['outcome'], 'pass', checks)
        record = next(r for r in checks['interface']['details']['invariants'] if r['id'] == 'anonymous-wire')
        self.assertEqual(record['status'], 'satisfied')
        self.assertEqual(record['actual']['terminals'][0]['net_name'], 'unconnected-(R2-Pad2)')

    def test_named_pin_disconnection_uses_native_symbol_pin_name(self):
        contract = read_json(FIXTURE / 'contract.json')
        relation = copy.deepcopy(next(i for i in contract['invariants'] if i['id'] == 'series-isolation'))
        relation.update(id='probe-isolated', terminals=['in', 'probe-tip'])
        contract['invariants'].append(relation)
        schematic = generator.schematic('disconnect').replace('(name "1"', '(name "TIP"')
        _, checks = self.run_variant(contract=contract, schematic=schematic)
        record = next(r for r in checks['interface']['details']['invariants'] if r['id'] == 'probe-isolated')
        self.assertEqual(record['status'], 'violation')
        self.assertEqual(record['actual']['terminals'][1]['net_name'], 'unconnected-(PROBE-TIP-Pad1)')

    def test_nc_normalization_never_hides_duplicate_or_unknown_nodes(self):
        def duplicate(root):
            net = next(n for n in root.findall('./nets/net') if n.get('name').startswith('unconnected-'))
            root.findall('./nets/net')[0].append(copy.deepcopy(net.find('node')))
        def unknown(root):
            net = next(n for n in root.findall('./nets/net') if n.get('name').startswith('unconnected-'))
            net.find('node').set('pin', '99')
        for mutation in (duplicate, unknown):
            _, checks = self.run_variant('disconnect', mutate_xml=mutation)
            self.assertEqual(checks['interface']['outcome'], 'unknown', checks)

    def test_value_inventory_distinguishes_missing_duplicate_and_empty(self):
        def missing(root):
            comp = root.find("./components/comp[@ref='R1']")
            comp.remove(comp.find('value'))
        def duplicate(root):
            ET.SubElement(root.find("./components/comp[@ref='R1']"), 'value').text = '10k'
        for mutation in (missing, duplicate):
            with self.subTest(mutation=mutation.__name__):
                _, checks = self.run_variant(mutate_xml=mutation)
                self.assertEqual(checks['interface']['execution'], 'unsupported')
                self.assertEqual(checks['interface']['outcome'], 'unknown')
                self.assertTrue(all(r['status'] == 'not_evaluated'
                    for r in checks['interface']['details']['invariants']))
        def empty(root):
            root.find("./components/comp[@ref='R1']/value").text = None
        _, checks = self.run_variant(mutate_xml=empty)
        self.assertEqual(checks['interface']['execution'], 'ok')
        self.assertEqual(checks['interface']['details']['violations'], ['series-value'])

    def test_duplicate_net_names_and_invalid_unused_sheet_paths_are_unknown(self):
        def duplicate(root):
            nets = root.findall('./nets/net')
            nets[-1].set('name', nets[0].get('name'))
        for mutation in [duplicate] + [
                lambda root, path=path: ET.SubElement(root.find('design'), 'sheet', tstamps=path)
                for path in ('unused', '/unused', 'unused/', '//', '/a//b/')]:
            with self.subTest(mutation=mutation):
                run, checks = self.run_variant(mutate_xml=mutation)
                with self.assertRaises(ValueError):
                    read_inventory(run / 'work/netlist/design.xml')
                self.assertEqual(checks['interface']['outcome'], 'unknown')
                self.assertTrue(all(r['status'] == 'not_evaluated'
                    for r in checks['interface']['details']['invariants']))

    def test_sealed_failed_witness_and_tamper(self):
        run, checks = self.run_variant('delete-load')
        freeze_evidence(run, list(checks.values()))
        witnesses = [{'requirement_id':'interface','check_id':'interface'}]
        citation = [{'path':'checks/interface.json','locator':'load-present'}]
        validate_witnesses(run, witnesses, citation)
        for path in ('work/interface/interface.json','work/netlist/design.xml'):
            target = run / path
            before = target.read_bytes()
            target.write_bytes(before+b' ')
            with self.assertRaises(ValueError):
                verify_evidence(run, list(checks.values()), required=True)
            with self.assertRaises(ValueError):
                validate_witnesses(run, witnesses, citation)
            target.write_bytes(before)
        (run/'inputs/contract.json').write_text('{}')
        with self.assertRaises(ValueError):
            verify_snapshot(run)

    def test_unknown_mapping_is_incomplete_even_with_known_violation(self):
        contract = read_json(FIXTURE/'contract.json')
        contract['components']['external']['symbols'] = ['Unknown:Port']
        run, checks = self.run_variant('delete-load', contract=contract)
        check = checks['interface']
        self.assertEqual((check['execution'],check['outcome']), ('unsupported','unknown'), check)
        self.assertIn('load-present', check['details']['violations'])
        freeze_evidence(run, list(checks.values()))
        with self.assertRaisesRegex(ValueError, 'freshly failed'):
            validate_witnesses(run, [{'requirement_id':'interface','check_id':'interface'}],
                               [{'path':'checks/interface.json','locator':'load-present'}])

    def test_native_zero_ohm_value_never_merges_direct_conductors(self):
        contract=read_json(FIXTURE/'contract.json')
        contract['allowed_edits'][0]['values'].append('0')
        _,checks=self.run_variant(resistor='0',contract=contract)
        result=checks['interface']
        self.assertEqual(result['outcome'],'pass',result)
        relation=next(r for r in result['details']['invariants'] if r['id']=='series-isolation')
        self.assertEqual(relation['status'],'satisfied',relation)
        self.assertNotEqual(*[t['net_code'] for t in relation['actual']['terminals']])

    def test_missing_pin_metadata_is_unknown_but_known_unconnected_pin_is_defect(self):
        contract = read_json(FIXTURE/'contract.json')
        contract['terminals']['probe-tip']['pin'] = '99'
        _, checks = self.run_variant(contract=contract)
        self.assertEqual(checks['interface']['outcome'], 'unknown', checks)
        _, checks = self.run_variant('disconnect')
        self.assertEqual(checks['interface']['details']['violations'], ['probe-wire'], checks)

    def test_native_inventory_rejects_ambiguity_instead_of_overwriting(self):
        def duplicate_ref(root):
            comp=copy.deepcopy(root.find('./components/comp'))
            comp.find('sheetpath').set('tstamps','/other/')
            root.find('components').append(comp)
        def duplicate_pin(root):
            pins=root.find('./libparts/libpart/pins')
            pins.append(copy.deepcopy(pins[0]))
        def missing_part(root):
            root.find('libparts').remove(root.find('./libparts/libpart'))
        def multiple_nets(root):
            root.findall('./nets/net')[1].append(copy.deepcopy(root.find('./nets/net/node')))
        def unknown_sheet(root):
            root.find('./components/comp/sheetpath').set('tstamps','/unknown/')
        def duplicate_uuid(root):
            comps=root.findall('./components/comp')
            comps[1].find('tstamps').text=comps[0].findtext('tstamps')
        for mutation in (duplicate_ref, duplicate_pin, missing_part, multiple_nets, unknown_sheet, duplicate_uuid):
            with self.subTest(mutation=mutation.__name__):
                _, checks = self.run_variant(mutate_xml=mutation)
                self.assertEqual((checks['interface']['execution'], checks['interface']['outcome']),
                                 ('unsupported','unknown'), checks)

    def test_direct_conductors_do_not_union_passives_and_names_are_exact(self):
        run, _ = self.run_variant()
        contract=read_json(FIXTURE/'contract.json')
        inventory=read_inventory(run/'work/netlist/design.xml')
        for cls, value in [('Device:R','0'),('Device:C','100n'),('Device:L','1m')]:
            inventory['components'][('/','R1')].update(symbol=cls,value=value)
            contract['components']['series']['symbols']=[cls]
            relation=next(i for i in contract['invariants'] if i['id']=='series-isolation')
            relation['kind']='same_net'
            records=evaluate(contract,inventory)
            self.assertEqual(next(r for r in records if r['id']=='series-isolation')['status'],'violation')
        inventory['nets']={code:('arbitrary anonymous spelling' if name.startswith('Net-') else name)
                           for code,name in inventory['nets'].items()}
        self.assertEqual(next(r for r in evaluate(contract,inventory) if r['id']=='anonymous-wire')['status'],'satisfied')
        contract=read_json(FIXTURE/'contract.json')
        contract['invariants'][3]['names'].append('/RENAMED')
        _, checks=self.run_variant('renamed',contract=contract)
        self.assertEqual(checks['interface']['outcome'],'pass',checks)

    def test_contract_changes_create_new_baselines_and_requirement_sources_are_exact(self):
        run,_=self.run_variant()
        baseline=load_baseline(run)['baseline_id']
        for mutation in (lambda c:c['allowed_edits'][0]['values'].append('1k'),
                         lambda c:c['invariants'][3]['names'].append('/RENAMED')):
            contract=read_json(FIXTURE/'contract.json');mutation(contract)
            run,_=self.run_variant(contract=contract)
            self.assertNotEqual(load_baseline(run)['baseline_id'],baseline)
        contract=read_json(FIXTURE/'contract.json')
        contract['invariants'][0]['source']['locator']='invented'
        with self.assertRaisesRegex(ValueError,'requirement source'):
            self.run_variant(contract=contract)

    def test_campaign_write_authority_excludes_contract_and_scope2_also_freezes_it(self):
        from agent_ee.campaign import mutable_inputs
        from agent_ee.baseline import compile_baseline
        run,_=self.run_variant()
        manifest=read_json(run/'candidate.json')
        baseline=load_baseline(run)
        # Existing campaign helper expects a board field even with explicit mutable_inputs.
        manifest['scope'].update(board='interface.kicad_sch',mutable_inputs=['interface.kicad_sch'])
        self.assertEqual(mutable_inputs(manifest,baseline),['interface.kicad_sch'])
        manifest['scope']['mutable_inputs'].append('contract.json')
        with self.assertRaisesRegex(ValueError,'immutable baseline'):
            mutable_inputs(manifest,baseline)
        scope=copy.deepcopy(manifest['scope'])
        scope['schema']='agent-ee.scope/2'
        for req in scope['baseline']['requirements']:
            del req['intent']
        legacy=compile_baseline(scope,manifest['files'],run/'inputs')
        self.assertIn('contract.json',legacy['files'])

    def test_capability_refuses_uncovered_requirement_and_forward_prerequisite(self):
        from agent_ee.baseline import compile_baseline
        run,_=self.run_variant()
        manifest=read_json(run/'candidate.json')
        scope=manifest['scope']
        scope['baseline']['requirements'][1]['checks'].append('interface')
        with self.assertRaisesRegex(ValueError,'requirement mappings'):
            compile_baseline(scope,manifest['files'],run/'inputs')
        scope['baseline']['requirements'][1]['checks'].remove('interface')
        scope['checks'][:2]=reversed(scope['checks'][:2])
        with self.assertRaisesRegex(ValueError,'forward'):
            compile_baseline(scope,manifest['files'],run/'inputs')

    def test_one_failed_check_cannot_witness_an_unrelated_satisfied_requirement(self):
        from agent_ee.baseline import compile_baseline
        run,_=self.run_variant()
        manifest=read_json(run/'candidate.json')
        contract=read_json(run/'inputs/contract.json')
        contract['invariants'][0]['requirement']='another'
        scope=manifest['scope']
        scope['baseline']['requirements'].append({
            'id':'another','statement':'A separate load obligation.',
            'source':contract['invariants'][0]['source'],
            'checks':['interface'],'intent':'acceptance'})
        write_json(run/'inputs/contract.json',contract)
        from agent_ee.storage import digest
        manifest['files']['contract.json']=digest(run/'inputs/contract.json')
        with self.assertRaisesRegex(ValueError,'one requirement'):
            compile_baseline(scope,manifest['files'],run/'inputs')

    def test_native_repeated_sheet_instances_use_scoped_symbol_uuids(self):
        case=self.root/'hierarchy'
        case.mkdir()
        main_uuid=generator.uid('hierarchy-root')
        sheets=[generator.uid('sheet-a'),generator.uid('sheet-b')]
        child=generator.schematic()
        references=['X1','R1','C1','RLOAD','PROBE','R2','R3']
        for ref in references:
            before=f'(project "interface" (path "/{generator.uid("root")}" (reference "{ref}") (unit 1)))'
            after='(project "interface" '+''.join(
                f'(path "/{main_uuid}/{sheet}" (reference "{ref}{suffix}") (unit 1))'
                for sheet,suffix in zip(sheets,['A','B']))+')'
            self.assertIn(before,child)
            child=child.replace(before,after)
        (case/'child.kicad_sch').write_text(child)
        sheet_nodes=''.join(f'(sheet (at {40+30*i} 30) (size 20 20) '
            f'(stroke (width 0) (type default)) (fill (color 0 0 0 0)) (uuid "{sheet}") '
            f'(property "Sheetname" "{name}" (at 40 30 0) (effects (font (size 1 1)))) '
            '(property "Sheetfile" "child.kicad_sch" (at 40 50 0) (effects (font (size 1 1)))))'
            for i,(sheet,name) in enumerate(zip(sheets,['First','Second'])))
        (case/'interface.kicad_sch').write_text(f'(kicad_sch (version 20250114) '
            f'(generator "eeschema") (uuid "{main_uuid}") (paper "A4") (lib_symbols) {sheet_nodes})')
        for name in ['interface.kicad_pcb','requirements.md']:
            (case/name).write_bytes((FIXTURE/name).read_bytes())
        contract=read_json(FIXTURE/'contract.json')
        for component in contract['components'].values():
            for location in component['locations']:
                location.update(sheet=f'/{sheets[0]}/',reference=location['reference']+'A')
        for invariant in contract['invariants']:
            if invariant['kind']=='named_net':
                invariant['names']=['/First'+name for name in invariant['names']]
        write_json(case/'contract.json',contract)
        scope=read_json(FIXTURE/'control.scope.json')
        scope['checks']=scope['checks'][:2]
        scope['baseline']['requirements']=scope['baseline']['requirements'][:1]
        scope['inputs'].append({'path':'child.kicad_sch','at':'child.kicad_sch'})
        write_json(case/'scope.json',scope)
        run=case/'run';run.mkdir();capture(case/'scope.json',run)
        checks=run_checks(run,time.monotonic()+90)
        self.assertEqual(checks[0]['details']['sheet_instances'],3,checks)
        self.assertEqual(checks[0]['execution'],'ok',checks)
        self.assertEqual(checks[1]['outcome'],'pass',checks)


class InterfacePipelineTests(unittest.TestCase):
    def test_native_defect_and_unknown_centralized_adjudication(self):
        import contextlib
        import io
        from unittest.mock import patch
        from agent_ee.run_record import run_review, verify
        from test_capability_pipeline import NativeCapabilityPipelineTests, PROFILE_CALL
        require_toolchain(native=True)
        self.calls=[]
        from agent_ee.run_record import runtime_files
        from agent_ee.storage import digest
        runtime = runtime_files()
        for name in ('codex', 'codex_tool_host'):
            runtime[name] = {'path': '/usr/bin/true', 'sha256': digest('/usr/bin/true')}
        self.host = {'runtime_files': runtime}
        with tempfile.TemporaryDirectory() as tmp:
            for name in ('control','delete-load','unknown'):
                case=Path(tmp)/name
                case.mkdir()
                variant='delete-load' if name=='delete-load' else 'control'
                (case/'interface.kicad_sch').write_text(generator.schematic(variant))
                (case/'interface.kicad_pcb').write_bytes((FIXTURE/'interface.kicad_pcb').read_bytes())
                (case/'requirements.md').write_bytes((FIXTURE/'requirements.md').read_bytes())
                contract=read_json(FIXTURE/'contract.json')
                if name=='unknown':
                    contract['components']['probe']['symbols']=['Unknown:Probe']
                write_json(case/'contract.json',contract)
                scope=read_json(FIXTURE/'control.scope.json')
                scope['review_profile']={'schema':'agent-ee.profile/1','name':'scripted-interface',
                    'reviewers':[{**PROFILE_CALL,'role':'circuit'},{**PROFILE_CALL,'role':'physical'}],
                    'adjudicator':PROFILE_CALL}
                write_json(case/'scope.json',scope)
                def agent(*args,**kwargs):
                    return NativeCapabilityPipelineTests.agent(self,*args,**kwargs)
                with patch('agent_ee.reviewers.dispatch',side_effect=agent), contextlib.redirect_stdout(io.StringIO()):
                    status=run_review(case/'scope.json',case/'run',120, host=self.host)
                self.assertNotIn('error',read_json(case/'run/result.json'))
                result=verify(case/'run', host=self.host)
                self.assertEqual(status,0 if name=='control' else 1,result)
                self.assertEqual(result['outcome'],{'control':'criteria_satisfied',
                    'delete-load':'defects_or_unresolved','unknown':'incomplete'}[name],result)
                accepted=[f for f in result['findings'] if f.get('status')=='accepted']
                self.assertEqual([f['local_id'] for f in accepted],['interface'] if name=='delete-load' else [],result)
                self.assertTrue(all(f['source']=='check' for f in accepted))
                self.assertCountEqual([label for _,label in self.calls[-3:-1]],['circuit','physical'])
                self.assertEqual(self.calls[-1][1],'adjudicator')
