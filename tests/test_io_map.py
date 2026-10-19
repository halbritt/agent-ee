"""CSV compilation and native pinout evidence, including fail-closed gaps."""
import copy
import csv
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
