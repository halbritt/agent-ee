"""CSV compilation and native pinout evidence, including fail-closed gaps."""
import contextlib
import copy
import csv
import hashlib
import io
import tempfile
import time
import unittest
from pathlib import Path

from agent_ee.candidate import capture
from agent_ee.checks import run_checks
from agent_ee.hardware_interface import capability, evaluate, validate_contract
from agent_ee.io_map import REQUIRED, compile_io_map, main
from agent_ee.storage import read_json, write_json
from tests.test_hardware_interface import FIXTURE, generator
from tests.toolchain import require_toolchain

POD = FIXTURE.parent / 'telemetry-pod'
SOURCE = dict(path='requirements.md', locator='Structural interface')


def csv_bytes(rows, fields=(*REQUIRED, 'MCU_Path')):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(fields)
    writer.writerows(rows)
    return stream.getvalue().encode()


def compile_(raw, **kwargs):
    return compile_io_map(raw, csv_path='io-map.csv', requirement='interface', source=SOURCE, **kwargs)


def probe_bindings():
    contract = read_json(FIXTURE / 'contract.json')
    contract['components'] = {k: contract['components'][k] for k in ('external', 'series', 'probe')}
    contract['components']['external']['locations'][0]['reference'] = 'J1'
    contract['terminals'] = {
        'J1-1': dict(component='external', pin='1'),
        'J1-2': dict(component='external', pin='2'),
        'R1:1': dict(component='series', pin='1'),
        'R1:2': dict(component='series', pin='2'),
        'PROBE-1': dict(component='probe', pin='1'),
    }
    contract['allowed_edits'] = []
    contract['invariants'] = [dict(id='binding', kind='component', component='external', populated=True,
                                    requirement='interface', source=SOURCE)]
    return validate_contract(contract)


PROBE_ROWS = [('J1-1', 'CAN_H', '/CAN+', 'R1', '1', 'Harness CAN high', 'direct'),
              ('J1-2', 'CAN_L', '/CAN-', 'R1', '2', 'Harness CAN low', 'direct'),
              ('PROBE-1', 'NO_CONNECT', 'N/C', 'N/A', 'N/A', 'Plugged cavity', 'N/A')]


class IoMapTests(unittest.TestCase):
    def test_source_digest_binds_exact_bytes_without_mutating_caller(self):
        raw = csv_bytes(PROBE_ROWS)
        source_bytes = b'Owner requirements\r\n\xff\n'
        contract = compile_(raw, source_bytes=source_bytes, require_source_sha256=True,
                            bindings=probe_bindings())
        self.assertEqual(SOURCE, dict(path='requirements.md', locator='Structural interface'))
        self.assertTrue(all(i['source']['sha256'] == hashlib.sha256(source_bytes).hexdigest()
                            for i in contract['invariants']))
        self.assertEqual(validate_contract(contract, source_bytes={'requirements.md': source_bytes},
                                           require_source_sha256=True), contract)
        with self.assertRaisesRegex(ValueError, 'sha256 mismatch'):
            validate_contract(contract, source_bytes={'requirements.md': source_bytes.replace(b'\r\n', b'\n')})
        with self.assertRaisesRegex(ValueError, 'missing requirement source bytes'):
            validate_contract(contract, source_bytes={})
        with self.assertRaisesRegex(ValueError, 'source bytes are required'):
            compile_(raw, require_source_sha256=True)
        with self.assertRaisesRegex(ValueError, 'sha256 is required'):
            validate_contract(compile_(raw), require_source_sha256=True)

    def test_malformed_and_conflicting_source_digests_refused(self):
        raw = csv_bytes(PROBE_ROWS)
        for value in ('bad', 'A' * 64, '0' * 63, '0' * 65, None, 42):
            contract = compile_(raw)
            contract['invariants'][0]['source']['sha256'] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'invalid requirement source sha256'):
                validate_contract(contract)
        with self.assertRaisesRegex(ValueError, 'sha256 mismatch'):
            compile_io_map(raw, csv_path='io-map.csv', requirement='interface',
                           source={**SOURCE, 'sha256': '0' * 64}, source_bytes=b'requirements')

    def test_captured_requirement_digest_is_verified_with_legacy_baseline(self):
        raw, source_bytes = csv_bytes(PROBE_ROWS), b'requirements\n'
        contract = compile_(raw, source_bytes=source_bytes)
        scope = dict(baseline=dict(requirements=[dict(id='interface', source=SOURCE, checks=['interface'])]))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_json(root / 'contract.json', contract)
            (root / 'io-map.csv').write_bytes(raw)
            (root / 'requirements.md').write_bytes(source_bytes)
            params = dict(contract='contract.json', netlist_check='netlist')
            files = {'contract.json', 'io-map.csv', 'requirements.md'}
            self.assertEqual(capability(params, scope, files, root, 'interface')['contract'], contract)
            changed = copy.deepcopy(contract)
            changed['invariants'][0]['source']['locator'] = 'invented'
            write_json(root / 'contract.json', changed)
            with self.assertRaisesRegex(ValueError, 'exact captured requirement source'):
                capability(params, scope, files, root, 'interface')
            write_json(root / 'contract.json', contract)
            (root / 'requirements.md').write_bytes(source_bytes + b'changed')
            with self.assertRaisesRegex(ValueError, 'sha256 mismatch'):
                capability(params, scope, files, root, 'interface')

    def test_cli_source_digest_and_deterministic_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'requirements.md'
            source.write_bytes(b'requirements\r\n')
            args = [str(POD / 'io-map.csv'), '--csv-path', 'io-map.csv', '--requirement', 'POD-AC-1',
                    '--source', 'requirements.md', '--source-file', str(source), '--locator', 'item 1',
                    '--require-source-sha256']
            for n in (1, 2):
                self.assertEqual(main([*args, '--out', str(root / f'{n}.json')]), 0)
            self.assertEqual((root / '1.json').read_bytes(), (root / '2.json').read_bytes())
            contract = read_json(root / '1.json')
            self.assertTrue(all(i['source']['sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
                                for i in contract['invariants']))
            offset = args.index('--source-file')
            default_args = args[:offset] + args[offset + 2:]
            with contextlib.chdir(root):
                self.assertEqual(main([*default_args, '--out', 'default.json']), 0)
            self.assertEqual((root / '1.json').read_bytes(), (root / 'default.json').read_bytes())
            with contextlib.chdir(root), self.assertRaises(SystemExit) as refused:
                main([*default_args, '--source-file', '', '--out', 'empty-path.json'])
            self.assertEqual(refused.exception.code, 2)
            self.assertFalse((root / 'empty-path.json').exists())
            source.unlink()
            with self.assertRaises(SystemExit) as refused:
                main([*args, '--out', str(root / 'absent.json')])
            self.assertEqual(refused.exception.code, 2)
            self.assertFalse((root / 'absent.json').exists())
            with contextlib.chdir(root), self.assertRaises(SystemExit) as refused:
                main([*default_args, '--out', 'absent-default.json'])
            self.assertEqual(refused.exception.code, 2)
            self.assertFalse((root / 'absent-default.json').exists())

    def test_compile_pod_iomap(self):
        raw = (POD / 'io-map.csv').read_bytes()
        contract = compile_io_map(raw, csv_path='io-map.csv', requirement='POD-AC-1',
            source=dict(path='spec.md', locator='Acceptance criteria / item 1'),
            spec_record=read_json(POD / 'spec-record.json'))
        self.assertEqual(contract, read_json(POD / 'io-map.interface.json'))
        connector = [i for i in contract['invariants'] if i['id'].startswith('AMPSEAL-') and i['id'].endswith(':net')]
        self.assertEqual(len(connector), 23)
        self.assertEqual(sum(i['kind'] == 'no_connect' for i in connector), 7)
        self.assertEqual(sum(i['kind'] == 'named_net' for i in connector), 16)
        self.assertEqual(sum(i['id'].endswith(':mcu-net') for i in contract['invariants']), 8)
        self.assertTrue(all(i['kind'] == 'unresolved' for i in contract['invariants']
                            if i['id'].endswith(':mcu-net')))
        self.assertFalse(any(i['kind'] == 'same_net' for i in contract['invariants']))
        ids = {rid for i in contract['invariants'] for rid in i['io_map']['requirements']}
        expected = {r['id'] for r in read_json(POD / 'spec-record.json')['requirements'] if r['id'].startswith('POD-IF-')}
        self.assertEqual(ids, expected)
        self.assertEqual(len(ids), 8)
        records = evaluate(contract, {'components': {}})
        self.assertTrue(all(r['status'] == 'not_evaluated' for r in records))

    def test_missing_net_column_refused(self):
        with self.assertRaisesRegex(ValueError, 'row 1.*column'):
            compile_(csv_bytes([], fields=[f for f in REQUIRED if f != 'Net']))

    def test_concrete_indirect_mcu_paths_refused_by_name(self):
        for signal, net, pin, path in (
                ('CAN_H', 'POD_CAN_H', '43', 'TJA1051T/3 TXD/RXD -> CANH/CANL'),
                ('FRONT_FORK_SIGNAL', 'POD_FRONT_FORK_SIGNAL', '11', 'MCP3204 CH0 -> SPI DOUT')):
            with self.subTest(path=path):
                with self.assertRaises(ValueError) as refused:
                    compile_(csv_bytes([('J1-1', signal, net, 'U1', pin, 'Harness', path)]))
                self.assertIn('row 2', str(refused.exception))
                self.assertIn(path, str(refused.exception))

    def test_concrete_mcu_requires_explicit_direct_declaration(self):
        with self.assertRaisesRegex(ValueError, 'row 2.*MCU_Path'):
            compile_(csv_bytes([PROBE_ROWS[0][:6]], fields=REQUIRED))

    def test_pod_pin_assignment_alone_cannot_assert_direct_connectivity(self):
        rows = list(csv.reader(io.StringIO((POD / 'io-map.csv').read_text())))
        # Filling the pinout must not short MCU logic to CAN or the ADC input.
        for position in ('AMPSEAL-1', 'AMPSEAL-6'):
            assigned = copy.deepcopy(rows[1:])
            row = next(r for r in assigned if r[0] == position)
            row[5:7] = ['U1', '43']
            with self.subTest(position=position), self.assertRaisesRegex(ValueError, 'row .*MCU_Path'):
                compile_(csv_bytes(assigned, fields=rows[0]))

    def test_unknown_net_cannot_become_a_literal_mcu_net(self):
        for net in ('N/A', 'TBD'):
            raw = csv_bytes([('J1-1', 'signal', net, 'U1', '43', 'Harness', 'direct')])
            with self.subTest(net=net):
                contract = compile_(raw)
                self.assertTrue(all(i['kind'] == 'unresolved' for i in contract['invariants']))
                self.assertTrue(all(r['status'] == 'not_evaluated'
                                    for r in evaluate(contract, {'components': {}})))

    def test_tbd_binding_override_refused(self):
        bindings = probe_bindings()
        bindings['terminals'] = {'J1-1:MCU:TBD': bindings['terminals']['R1:1']}
        with self.assertRaisesRegex(ValueError, 'unused terminal bindings.*J1-1:MCU:TBD'):
            compile_(csv_bytes([('J1-1', 'CAN_H', '/CAN+', 'TBD', 'TBD', 'Harness', 'TBD')]),
                     bindings=bindings)

    def test_duplicate_connector_pin_refused(self):
        with self.assertRaisesRegex(ValueError, 'row 3.*duplicate connector pin'):
            compile_(csv_bytes([PROBE_ROWS[0], PROBE_ROWS[0]]))

    def test_ragged_rows_and_wired_no_connect_refused(self):
        for row, error in [(PROBE_ROWS[0][:-1], 'missing column'),
                           ((), 'missing column'),
                           ((*PROBE_ROWS[0], 'extra'), 'missing column'),
                           (('J1-1', 'NO_CONNECT', 'GND', 'N/A', 'N/A', 'plug', 'N/A'), 'wired no-connect'),
                           (('J1-1', 'NO_CONNECT', 'N/C', 'U1', '1', 'plug', 'direct'), 'wired no-connect')]:
            with self.subTest(row=row), self.assertRaisesRegex(ValueError, 'row 2.*' + error):
                compile_(csv_bytes([row]))

    def test_empty_duplicate_headers_and_malformed_csv_refused(self):
        for raw in (csv_bytes([]), csv_bytes([], fields=(*REQUIRED, 'Net')),
                    csv_bytes([]) + b'"unterminated'):
            with self.subTest(raw=raw), self.assertRaisesRegex(ValueError, 'row'):
                compile_(raw)

    def test_traceability_gap_and_unknown_id_refused(self):
        raw = (POD / 'io-map.csv').read_bytes()
        for altered in (raw.replace(b'POD-IF-GNSS-ANTENNA', b'NO-ID'),
                        raw.replace(b'POD-IF-GNSS-ANTENNA', b'POD-IF-FAKE')):
            with self.assertRaisesRegex(ValueError, 'traceability mismatch'):
                compile_(altered, spec_record=read_json(POD / 'spec-record.json'))

    def test_tbd_cannot_be_resolved_by_a_binding(self):
        bindings = probe_bindings()
        bindings['terminals'] = {'J1-1': bindings['terminals']['J1-1']}
        raw = csv_bytes([('J1-1', 'CAN_H', '/CAN+', 'TBD', 'TBD', 'Harness', 'TBD')])
        contract = compile_(raw, bindings=bindings)
        self.assertIn('unresolved', contract['terminals']['J1-1:MCU:TBD'])
        self.assertEqual(contract['invariants'][1]['kind'], 'unresolved')
        self.assertFalse(any(i['kind'] == 'same_net' for i in contract['invariants']))

    def test_unused_binding_and_conflicting_mcu_net_refused(self):
        with self.assertRaisesRegex(ValueError, 'unused terminal bindings'):
            compile_(csv_bytes(PROBE_ROWS[:1]), bindings=probe_bindings())
        rows = [PROBE_ROWS[0], ('J1-2', 'CAN_L', '/CAN-', 'R1', '1', 'Harness', 'direct')]
        with self.assertRaisesRegex(ValueError, 'row 3.*conflicting net'):
            compile_(csv_bytes(rows))

    def test_mcu_binding_cannot_change_csv_identity(self):
        bindings = probe_bindings()
        bindings['terminals']['R1:1']['pin'] = '3'
        with self.assertRaisesRegex(ValueError, 'row 2.*MCU binding disagrees'):
            compile_(csv_bytes(PROBE_ROWS), bindings=bindings)

    def test_csv_is_hashed_captured_and_immutable(self):
        raw = csv_bytes(PROBE_ROWS)
        contract = compile_(raw, bindings=probe_bindings())
        scope = dict(baseline=dict(requirements=[dict(id='interface', source=SOURCE, checks=['interface'])]))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_json(root / 'contract.json', contract)
            (root / 'io-map.csv').write_bytes(raw)
            params = dict(contract='contract.json', netlist_check='netlist')
            files = {'contract.json', 'io-map.csv', 'requirements.md'}
            result = capability(params, scope, files, root, 'interface')
            self.assertEqual(result['immutable_inputs'], ['contract.json', 'io-map.csv'])
            for absent in (True, False):
                (root / 'io-map.csv').write_bytes(raw + b'changed')
                with self.assertRaisesRegex(ValueError, 'CSV must be captured'):
                    capability(params, scope, files - {'io-map.csv'} if absent else files, root, 'interface')

    def test_invalid_extension_fields_are_refused(self):
        original = compile_(csv_bytes(PROBE_ROWS))
        for mutate in (lambda c: c['terminals']['J1-1'].update(pin='1'),
                       lambda c: c['invariants'][0]['io_map'].update(row=0),
                       lambda c: c['invariants'][0]['io_map'].update(sha256='bad'),
                       lambda c: c['invariants'][0]['io_map'].update(extra=True)):
            contract = copy.deepcopy(original)
            mutate(contract)
            with self.assertRaises(ValueError):
                validate_contract(contract)

    def test_cli_reproduces_pod_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'contract.json'
            self.assertEqual(main([str(POD / 'io-map.csv'), '--csv-path', 'io-map.csv',
                '--requirement', 'POD-AC-1', '--source', 'spec.md', '--locator', 'Acceptance criteria / item 1',
                '--spec-record', str(POD / 'spec-record.json'), '--out', str(out)]), 0)
            self.assertEqual(read_json(out), read_json(POD / 'io-map.interface.json'))

    def test_fome_mc_can1_contract_cross_check(self):
        fixture = validate_contract(read_json(POD / 'fome-mc-can1.interface.json'))
        rows = [(i['terminal'], i['terminal'], i['names'][0], 'N/A', 'N/A', 'CAN1 interface', 'N/A')
                for i in fixture['invariants']]
        compiled = compile_(csv_bytes(rows), bindings=fixture)
        original = {(i['terminal'], tuple(i['names'])) for i in fixture['invariants']}
        generated = {(i['terminal'], tuple(i['names'])) for i in compiled['invariants'] if i['kind'] == 'named_net'}
        self.assertEqual(generated, original)
        self.assertEqual(compiled['components'], fixture['components'])
        self.assertEqual(compiled['terminals'], fixture['terminals'])
        # The owner contract and generated predicates agree on both polarities.
        components, memberships, nets = {}, {}, {}
        for cid, declaration in fixture['components'].items():
            loc = declaration['locations'][0]
            pins = sorted(t['pin'] for t in fixture['terminals'].values() if t['component'] == cid)
            components[(loc['sheet'], loc['reference'])] = dict(**loc, uuid=cid,
                symbol=declaration['symbols'][0], pins=pins, populated=True)
        for index, invariant in enumerate(fixture['invariants']):
            term = fixture['terminals'][invariant['terminal']]
            ref = fixture['components'][term['component']]['locations'][0]['reference']
            memberships[(ref, term['pin'])] = str(index)
            nets[str(index)] = invariant['names'][0]
        inventory = dict(components=components, memberships=memberships, nets=nets,
                         disconnected=set(), unresolved={}, sources={})
        for swap in (False, True):
            if swap:
                nets['0'], nets['1'] = nets['1'], nets['0']
            expected = [r['status'] for r in evaluate(fixture, inventory)]
            actual = [r['status'] for r in evaluate(compiled, inventory)[:4]]
            self.assertEqual(actual, expected)
            self.assertEqual(actual[:2], ['violation' if swap else 'satisfied'] * 2)


class NativeIoMapTests(unittest.TestCase):
    def run_probe(self, *, swapped=False, wired_nc=False, unresolved=False, ambiguous_nc=False, tbd_mcu=False):
        require_toolchain(native=True)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        rows = copy.deepcopy(PROBE_ROWS)
        bindings = None if unresolved else probe_bindings()
        if tbd_mcu:
            rows[0] = (*rows[0][:3], 'TBD', 'TBD', *rows[0][5:])
            del bindings['terminals']['R1:1']
        raw = csv_bytes(rows)
        contract = compile_(raw, bindings=bindings)
        schematic = generator.schematic('control' if wired_nc else 'disconnect')
        schematic = schematic.replace('X1', 'J1').replace('"IN"', '"CAN+"').replace('"OUT"', '"CAN-"')
        if ambiguous_nc:
            # An unmodelled source item prevents proof of singleton isolation.
            schematic = schematic[:-2] + '(rule_area (polyline (pts (xy 0 0) (xy 1 1)))))\n'
        if swapped:
            schematic = schematic.replace('"CAN+"', '"SWAP"', 1).replace('"CAN-"', '"CAN+"', 1).replace('"SWAP"', '"CAN-"', 1)
        (root / 'interface.kicad_sch').write_text(schematic)
        for filename in ('interface.kicad_pcb', 'requirements.md'):
            (root / filename).write_bytes((FIXTURE / filename).read_bytes())
        (root / 'io-map.csv').write_bytes(raw)
        write_json(root / 'contract.json', contract)
        scope = read_json(FIXTURE / 'control.scope.json')
        scope['checks'] = scope['checks'][:2]
        scope['baseline']['requirements'] = scope['baseline']['requirements'][:1]
        scope['inputs'].append(dict(path='io-map.csv', at='io-map.csv'))
        write_json(root / 'scope.json', scope)
        run = root / 'run'
        run.mkdir()
        capture(root / 'scope.json', run)
        checks = run_checks(run, time.monotonic() + 90)
        self.assertEqual(checks[0]['execution'], 'ok', checks)
        return checks[1]

    def test_hardware_interface_pass_and_can_swap(self):
        result = self.run_probe()
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        result = self.run_probe(swapped=True)
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)
        self.assertTrue({'J1-1:net', 'J1-2:net'} <= set(result['details']['violations']))

    def test_wired_no_connect_is_a_native_violation(self):
        result = self.run_probe(wired_nc=True)
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)
        self.assertIn('PROBE-1:net', result['details']['violations'])

    def test_missing_bindings_never_pass(self):
        result = self.run_probe(unresolved=True)
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)

    def test_ambiguous_no_connect_never_passes(self):
        result = self.run_probe(ambiguous_nc=True)
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)

    def test_tbd_mcu_keeps_otherwise_satisfied_pinout_unknown(self):
        result = self.run_probe(tbd_mcu=True)
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)
        statuses = {r['id']: r['status'] for r in result['details']['invariants']}
        self.assertEqual(statuses['J1-1:net'], 'satisfied')
        self.assertEqual(statuses['J1-2:net'], 'satisfied')
        self.assertEqual(statuses['J1-1:mcu-net'], 'not_evaluated')


# Original synthetic indirect-path probe: J1 -> transceiver U1 -> MCU U3 and
# J1 -> series R1 -> ADC U2 -> MCU U3. No donor design or third-party symbols.
PATH_PARTS = {
    'J1': ('PathOwned:Conn2', 'conn', [('Pin_1', 'passive'), ('Pin_2', 'passive')]),
    'U1': ('PathOwned:Xcvr', 'XCVR-1', [('TXD', 'input'), ('RXD', 'output'), ('CANH', 'bidirectional')]),
    'R1': ('PathOwned:R', '10k', [('A', 'passive'), ('B', 'passive')]),
    'U2': ('PathOwned:Adc', 'ADC-1', [('CH0', 'input'), ('DOUT', 'output')]),
    'U3': ('PathOwned:Mcu', 'MCU-1', [('IO1', 'bidirectional'), ('IO2', 'bidirectional'), ('IO3', 'bidirectional')]),
}
PATH_NETS = {'J1': ['CANH', 'AIN'], 'U1': ['TXD', 'RXD', 'CANH'], 'R1': ['AIN', 'AINF'],
             'U2': ['AINF', 'DOUT'], 'U3': ['TXD', 'RXD', 'DOUT']}
PATH_IDS = {'J1': 'conn', 'U1': 'xcvr', 'R1': 'series', 'U2': 'adc', 'U3': 'mcu'}
PATH_ROWS = [('J1-1', 'CAN_H', '/CANH', 'U3', '1;2', 'Harness CAN high', 'can-tx;can-rx'),
             ('J1-2', 'AIN', '/AIN', 'U3', '3', 'Harness analog input', 'ain-dout')]


def path_bindings():
    components = {cid: dict(locations=[dict(sheet='/', reference=ref)], symbols=[PATH_PARTS[ref][0]])
                  for ref, cid in PATH_IDS.items()}
    terminals = {'J1-1': dict(component='conn', pin='1'), 'J1-2': dict(component='conn', pin='2')}
    for ref, cid in PATH_IDS.items():
        if ref != 'J1':
            terminals.update({f'{ref}:{n}': dict(component=cid, pin=str(n))
                              for n in range(1, len(PATH_PARTS[ref][2]) + 1)})
    return validate_contract(dict(schema='agent-ee.interface/1', components=components, terminals=terminals,
        allowed_edits=[], invariants=[dict(id='binding', kind='component', component='conn', populated=True,
                                           requirement='interface', source=SOURCE)]))


def path_declarations():
    def path(nodes, devices, directions):
        names = [PATH_PARTS[ref][2][int(pin) - 1][0] for ref, pin in nodes]
        tids = [f'J1-{pin}' if ref == 'J1' else f'{ref}:{pin}' for ref, pin in nodes]
        return dict(terminals=tids, pin_names=names, devices=devices, directions=directions)
    xcvr = dict(component='xcvr', value='XCVR-1', role='CAN transceiver')
    return dict(schema='agent-ee.io-map-paths/1', paths={
        'can-tx': path([('J1', '1'), ('U1', '3'), ('U1', '1'), ('U3', '1')], [xcvr],
                       ['bidirectional', 'toward_connector']),
        'can-rx': path([('J1', '1'), ('U1', '3'), ('U1', '2'), ('U3', '2')], [xcvr],
                       ['bidirectional', 'toward_mcu']),
        'ain-dout': path([('J1', '2'), ('R1', '1'), ('R1', '2'), ('U2', '1'), ('U2', '2'), ('U3', '3')],
                         [dict(component='series', value='10k', role='series resistor'),
                          dict(component='adc', value='ADC-1', role='SPI ADC')], ['toward_mcu'] * 3)})


def path_inventory(nets=PATH_NETS, values=None):
    """Hand-built native inventory with the reader's fields, for evaluator-only cases."""
    components, memberships, codes = {}, {}, {}
    for ref, (symbol, value, pins) in PATH_PARTS.items():
        numbers = [str(n) for n in range(1, len(pins) + 1)]
        components[('/', ref)] = dict(sheet='/', reference=ref, uuid=ref, symbol=symbol, pins=numbers,
            pin_names={n: p[0] for n, p in zip(numbers, pins)}, pin_types={n: p[1] for n, p in zip(numbers, pins)},
            populated=True, value=(values or {}).get(ref, value))
        for number, net in zip(numbers, nets[ref]):
            memberships[(ref, number)] = codes.setdefault(net, str(len(codes) + 1))
    return dict(components=components, memberships=memberships, nets={c: '/' + n for n, c in codes.items()},
                disconnected=set(), unresolved={}, sources={})


def path_schematic(variant='pass'):
    """Native-loadable probe; local labels on every pin connection point."""
    root = generator.uid('path-root')
    definitions, symbols = [], []
    for ref, (lib, value, pins) in PATH_PARTS.items():
        body = ''.join(f'(pin {kind} line (at 0 {-2.54 * i} 0) (length 2.54) '
                       f'(name "{name}" (effects (font (size 1 1)))) (number "{i + 1}" (effects (font (size 1 1)))))'
                       for i, (name, kind) in enumerate(pins))
        part = lib.split(':')[1]
        definitions.append(f'(symbol "{lib}" (pin_names (offset 0)) (in_bom yes) (on_board yes) '
                           + generator.property_('Reference', ref[0]) + generator.property_('Value', part)
                           + f'(symbol "{part}_1_1" {body}))')
    for index, (ref, (lib, value, pins)) in enumerate(PATH_PARTS.items()):
        x, y = 40 + 20 * index, 50
        if variant == 'identity' and ref == 'U1':
            value = 'XCVR-2'
        symbols.append(f'(symbol (lib_id "{lib}") (at {x} {y} 0) (unit 1) (in_bom yes) (on_board yes) (dnp no) '
                       f'(uuid "{generator.uid("path" + ref)}")' + generator.property_('Reference', ref)
                       + generator.property_('Value', value)
                       + ''.join(f'(pin "{i + 1}" (uuid "{generator.uid("path" + ref + str(i))}"))'
                                 for i in range(len(pins)))
                       + f'(instances (project "interface" (path "/{root}" (reference "{ref}") (unit 1)))))')
        for i, net in enumerate(PATH_NETS[ref]):
            if variant == 'severed' and (ref, net) == ('U3', 'TXD'):
                net = 'TXD_CUT'
            symbols.append(f'(label "{net}" (at {x} {y + 2.54 * i} 0) (effects (font (size 1 1)) '
                           f'(justify left bottom)) (uuid "{generator.uid("path" + ref + str(i) + net)}"))')
    return (f'(kicad_sch (version 20250114) (generator "eeschema") (uuid "{root}") (paper "A4") '
            f'(lib_symbols {"".join(definitions)}) {"".join(symbols)})\n')


def compile_paths(rows=PATH_ROWS, paths=None, bindings=None):
    return compile_(csv_bytes(rows), bindings=bindings or path_bindings(),
                    paths=path_declarations() if paths is None else paths)


class IndirectPathTests(unittest.TestCase):
    def statuses(self, contract, inventory):
        return {r['id']: r for r in evaluate(contract, inventory) if r['expected']['kind'] == 'indirect_path'}

    def test_declared_paths_compile_without_connector_mcu_same_net(self):
        contract = compile_paths()
        paths = [i for i in contract['invariants'] if i['kind'] == 'indirect_path']
        self.assertEqual([i['id'] for i in paths],
                         ['J1-1:mcu-path:can-tx', 'J1-1:mcu-path:can-rx', 'J1-2:mcu-path:ain-dout'])
        self.assertEqual([i['terminals'][-1] for i in paths], ['U3:1', 'U3:2', 'U3:3'])
        self.assertFalse(any(i['kind'] in ('same_net', 'named_net') and 'U3:' in str(i.get('terminals', i.get('terminal')))
                             for i in contract['invariants']))
        self.assertTrue({'xcvr:populated', 'series:populated', 'adc:populated', 'mcu:populated'}
                        <= {i['id'] for i in contract['invariants']})
        records = self.statuses(contract, path_inventory())
        self.assertEqual({r['status'] for r in records.values()}, {'satisfied'})
        actual = records['J1-2:mcu-path:ain-dout']['actual']
        self.assertEqual([s['net_names'] for s in actual['segments']], [['/AIN'] * 2, ['/AINF'] * 2, ['/DOUT'] * 2])
        self.assertEqual([d['reference'] for d in actual['devices']], ['R1', 'U2'])
        self.assertIn('No transceiver, amplifier, divider or ADC transfer', actual['disclaimer'])
        self.assertIn('BLE', actual['disclaimer'])

    def test_native_contradictions_are_violations(self):
        contract = compile_paths()
        cases = {
            'severed': (dict(PATH_NETS, U3=['TXD_CUT', 'RXD', 'DOUT']), None,
                        'J1-1:mcu-path:can-tx', 'not one native conductor'),
            'wrong part': (PATH_NETS, {'U1': 'XCVR-2'}, 'J1-1:mcu-path:can-rx', "is 'XCVR-2'"),
            'bypassed device': (dict(PATH_NETS, R1=['AIN', 'AIN'], U2=['AIN', 'DOUT']), None,
                                'J1-2:mcu-path:ain-dout', 'bypassed'),
            'connector shorted to MCU': (dict(PATH_NETS, J1=['CANH', 'DOUT'], R1=['DOUT', 'AINF']), None,
                                         'J1-2:mcu-path:ain-dout', 'not indirect'),
            'disconnected hop': ({k: v for k, v in PATH_NETS.items() if k != 'U2'} | {'U2': []}, None,
                                 'J1-2:mcu-path:ain-dout', 'not one native conductor'),
        }
        for label, (nets, values, target, reason) in cases.items():
            with self.subTest(label):
                record = self.statuses(contract, path_inventory(nets, values))[target]
                self.assertEqual(record['status'], 'violation', record['actual'])
                self.assertIn(reason, record['actual']['reason'])

    def test_declaration_contradictions_are_violations(self):
        for label, mutate, reason in (
                ('dropped intermediate', lambda q: [q['terminals'].__delitem__(slice(1, 3)),
                                                    q['pin_names'].__delitem__(slice(1, 3)),
                                                    q['devices'].pop(0), q['directions'].pop(0)],
                 'not one native conductor'),
                ('wrong pin role', lambda q: q['pin_names'].__setitem__(4, 'CH0'), "declared 'CH0'"),
                ('reversed direction', lambda q: q['directions'].__setitem__(2, 'toward_connector'),
                 'contradicts declared toward_connector'),
                ('bidirectional logic', lambda q: q['directions'].__setitem__(2, 'bidirectional'),
                 'contradicts declared bidirectional')):
            with self.subTest(label):
                paths = path_declarations()
                mutate(paths['paths']['ain-dout'])
                bindings = path_bindings()
                if label == 'dropped intermediate':
                    for tid in ('R1:1', 'R1:2'):
                        del bindings['terminals'][tid]
                    del bindings['components']['series']
                record = self.statuses(compile_paths(paths=paths, bindings=bindings),
                                       path_inventory())['J1-2:mcu-path:ain-dout']
                self.assertEqual(record['status'], 'violation', record['actual'])
                self.assertIn(reason, record['actual']['reason'])

    def test_absent_evidence_stays_unknown(self):
        contract = compile_paths()
        inventory = path_inventory()
        inventory['components'][('/', 'U2')]['pin_types']['2'] = 'unspecified'
        record = self.statuses(contract, inventory)['J1-2:mcu-path:ain-dout']
        self.assertEqual(record['status'], 'not_evaluated')
        inventory = path_inventory()
        del inventory['components'][('/', 'U1')]['pin_names']
        self.assertEqual({r['status'] for k, r in self.statuses(contract, inventory).items() if 'can' in k},
                         {'not_evaluated'})
        bindings = path_bindings()
        del bindings['terminals']['U2:2']
        record = self.statuses(compile_paths(bindings=bindings), path_inventory())['J1-2:mcu-path:ain-dout']
        self.assertEqual(record['status'], 'not_evaluated')
        self.assertIn('unbound terminal U2:2', record['actual']['reason'])
        inventory = path_inventory()
        inventory['components'][('/', 'U1')]['symbol'] = 'Other:Xcvr'
        self.assertEqual(self.statuses(contract, inventory)['J1-1:mcu-path:can-tx']['status'], 'not_evaluated')
        inventory = path_inventory()
        del inventory['components'][('/', 'U2')]
        self.assertEqual(self.statuses(contract, inventory)['J1-2:mcu-path:ain-dout']['status'], 'violation')

    def test_compile_refusals(self):
        paths = path_declarations()
        cases = [
            ('undeclared path', [PATH_ROWS[0], (*PATH_ROWS[1][:6], 'ain-x')], paths, None,
             r"row 3: undeclared indirect MCU_Path \['ain-x'\]"),
            ('unused path', PATH_ROWS[:1], paths, None, 'unused indirect paths'),
            ('pin count', [(*PATH_ROWS[0][:4], '1', *PATH_ROWS[0][5:]), PATH_ROWS[1]], paths, None,
             'one unused path per MCU_Pin'),
            ('swapped MCU pins', [(*PATH_ROWS[0][:4], '2;1', *PATH_ROWS[0][5:]), PATH_ROWS[1]], paths, None,
             'path can-tx must run from J1-1 to MCU U3:2'),
            ('TBD MCU', [(*PATH_ROWS[0][:3], 'TBD', 'TBD', *PATH_ROWS[0][5:]), PATH_ROWS[1]], paths, None,
             'requires concrete MCU pins'),
            ('path reused', [PATH_ROWS[0], (*PATH_ROWS[1][:4], '1', PATH_ROWS[1][5], 'can-tx')], paths, None,
             'one unused path per MCU_Pin'),
        ]
        for label, rows, declared, bindings, error in cases:
            with self.subTest(label), self.assertRaisesRegex(ValueError, error):
                compile_paths(rows, copy.deepcopy(declared), bindings)
        undeclared = path_bindings()
        del undeclared['components']['series']
        for tid in ('R1:1', 'R1:2'):
            del undeclared['terminals'][tid]
        with self.assertRaisesRegex(ValueError, "undeclared intermediate device 'series'"):
            compile_paths(bindings=undeclared)
        with self.assertRaisesRegex(ValueError, 'unsupported indirect MCU_Path'):
            compile_(csv_bytes(PATH_ROWS), bindings=path_bindings())
        for mutate, error in (
                (lambda q: q['devices'][0].update(component='adc'), 'belong to the declared device'),
                (lambda q: q['directions'].append('toward_mcu'), 'one declared direction'),
                (lambda q: q['directions'].__setitem__(0, 'sideways'), 'one declared direction'),
                (lambda q: q['pin_names'].pop(), 'one native pin name'),
                (lambda q: q['devices'][0].update(value=''), 'nonempty text'),
                (lambda q: q['devices'][0].pop('role'), 'expected fields'),
                (lambda q: q.update(devices={'component': 'xcvr'}), 'devices must be a list')):
            declared = path_declarations()
            mutate(declared['paths']['can-tx'])
            with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                compile_paths(paths=declared)

    def test_contract_cannot_assert_same_net_across_declared_path(self):
        contract = compile_paths()
        contract['invariants'].append(dict(id='short', kind='same_net', terminals=['U3:3', 'J1-2'],
                                           requirement='interface', source=SOURCE))
        with self.assertRaisesRegex(ValueError, 'same_net cannot be asserted across a declared indirect path'):
            validate_contract(contract)
        contract = compile_paths()
        path = next(i for i in contract['invariants'] if i['kind'] == 'indirect_path')
        path['terminals'][0] = 'U1:2'
        with self.assertRaisesRegex(ValueError, 'endpoint cannot be an intermediate device'):
            validate_contract(contract)

    def test_direct_rows_unchanged_alongside_paths(self):
        rows = [*PATH_ROWS, ('J1-3', 'DEBUG', '/DBG', 'U3', '4', 'Pad', 'direct')]
        contract = compile_paths(rows)
        kinds = {i['id']: i['kind'] for i in contract['invariants'] if i['id'].startswith('J1-3')}
        self.assertEqual(kinds, {'J1-3:net': 'named_net', 'J1-3:mcu-net': 'named_net', 'J1-3:same-net': 'same_net'})

    def test_cli_compiles_declared_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'io-map.csv').write_bytes(csv_bytes(PATH_ROWS))
            write_json(root / 'paths.json', path_declarations())
            write_json(root / 'bindings.json', path_bindings())
            self.assertEqual(main([str(root / 'io-map.csv'), '--csv-path', 'io-map.csv', '--requirement', 'interface',
                '--source', SOURCE['path'], '--locator', SOURCE['locator'], '--bindings', str(root / 'bindings.json'),
                '--paths', str(root / 'paths.json'), '--out', str(root / 'contract.json')]), 0)
            self.assertEqual(read_json(root / 'contract.json'), compile_paths())


class NativeIndirectPathTests(unittest.TestCase):
    def run_probe(self, variant):
        require_toolchain(native=True)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        raw = csv_bytes(PATH_ROWS)
        (root / 'interface.kicad_sch').write_text(path_schematic(variant))
        for filename in ('interface.kicad_pcb', 'requirements.md'):
            (root / filename).write_bytes((FIXTURE / filename).read_bytes())
        (root / 'io-map.csv').write_bytes(raw)
        write_json(root / 'contract.json', compile_paths())
        scope = read_json(FIXTURE / 'control.scope.json')
        scope['checks'] = scope['checks'][:2]
        scope['baseline']['requirements'] = scope['baseline']['requirements'][:1]
        scope['inputs'].append(dict(path='io-map.csv', at='io-map.csv'))
        write_json(root / 'scope.json', scope)
        run = root / 'run'
        run.mkdir()
        capture(root / 'scope.json', run)
        checks = run_checks(run, time.monotonic() + 90)
        self.assertEqual(checks[0]['execution'], 'ok', checks)
        report = read_json(run / 'work' / 'interface' / 'interface.json')
        return checks[1], {r['id']: r for r in report['invariants']}

    def test_declared_paths_pass_natively_with_disclaimer(self):
        result, records = self.run_probe('pass')
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        self.assertTrue(any('No transceiver, amplifier, divider or ADC transfer' in limitation
                            for limitation in result['limitations']))
        self.assertEqual(records['J1-1:mcu-path:can-tx']['actual']['segments'][1]['net_names'], ['/TXD', '/TXD'])
        self.assertEqual(records['J1-2:mcu-path:ain-dout']['actual']['segments'][1]['pin_types'],
                         ['passive', 'input'])

    def test_severed_segment_and_wrong_identity_are_native_defects(self):
        for variant, failed in (('severed', {'J1-1:mcu-path:can-tx'}),
                                ('identity', {'J1-1:mcu-path:can-tx', 'J1-1:mcu-path:can-rx'})):
            with self.subTest(variant):
                result, records = self.run_probe(variant)
                self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)
                self.assertEqual(set(result['details']['violations']), failed)
                self.assertEqual(records['J1-2:mcu-path:ain-dout']['status'], 'satisfied')
