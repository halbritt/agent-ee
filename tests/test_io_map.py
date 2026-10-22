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
from agent_ee.hardware_interface import (capability, evaluate, requirement_authority, validate_contract,
                                        validate_contract_shape)
from agent_ee.capabilities import compile_coverage
from agent_ee.io_map import REQUIRED, build_record, compile_io_map, main, traceability
from agent_ee.storage import digest, json_bytes, read_json, write_json
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

    def test_one_digest_per_local_path_without_source_bytes(self):
        original = compile_(csv_bytes(PROBE_ROWS), source_bytes=b'requirements')
        self.assertEqual(validate_contract(original), original)
        legacy = compile_(csv_bytes(PROBE_ROWS))
        self.assertEqual(validate_contract(legacy), legacy)
        consistent = copy.deepcopy(original)
        consistent['invariants'][-1]['source'].update(path='./requirements.md', locator='another locator')
        self.assertEqual(validate_contract(consistent), consistent)
        for mixed in (False, True):
            for index in (0, -1):
                for alias in ('requirements.md', './requirements.md'):
                    contract = copy.deepcopy(original)
                    source = contract['invariants'][index]['source']
                    source['path'] = alias
                    source['locator'] = 'another locator'
                    if mixed:
                        del source['sha256']
                    else:
                        source['sha256'] = '0' * 64
                    with self.subTest(mixed=mixed, index=index, alias=alias):
                        with self.assertRaisesRegex(ValueError, 'one sha256 on all invariants or none'):
                            validate_contract(contract)
        for hashed in (False, True):
            contract = copy.deepcopy(original)
            source = contract['invariants'][-1]['source']
            source['path'] = 'other-requirements.md'
            if hashed:
                source['sha256'] = '0' * 64
            else:
                del source['sha256']
            self.assertEqual(validate_contract(contract), contract)

    def test_hashed_requirement_source_is_immutable(self):
        raw, source_bytes = csv_bytes(PROBE_ROWS), b'requirements\n'
        scope = dict(baseline=dict(requirements=[dict(id='interface', source=SOURCE, checks=['interface'])]))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'io-map.csv').write_bytes(raw)
            (root / 'requirements.md').write_bytes(source_bytes)
            params = dict(contract='contract.json', netlist_check='netlist')
            files = {'contract.json', 'io-map.csv', 'requirements.md'}
            for hashed in (False, True):
                contract = compile_(raw, source_bytes=source_bytes if hashed else None)
                write_json(root / 'contract.json', contract)
                expected = ['contract.json', 'io-map.csv'] + (['requirements.md'] if hashed else [])
                self.assertEqual(capability(params, scope, files, root, 'interface')['immutable_inputs'], expected)

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
            root = Path(tmp)
            out = root / 'contract.json'
            args = [str(POD / 'io-map.csv'), '--csv-path', 'io-map.csv',
                '--requirement', 'POD-AC-1', '--source', 'spec.md', '--locator', 'Acceptance criteria / item 1',
                '--spec-record', str(POD / 'spec-record.json'), '--out', str(out)]
            stderr, stdout = io.StringIO(), io.StringIO()
            with contextlib.chdir(root), contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(stdout):
                with self.assertRaises(SystemExit) as refused:
                    main(args)
            self.assertEqual(refused.exception.code, 2)
            self.assertIn('missing-source (FileNotFoundError)', stderr.getvalue())
            self.assertIn('spec.md', stderr.getvalue())
            self.assertEqual(stdout.getvalue(), '')
            self.assertFalse(out.exists())
            source_bytes = (POD / 'spec.md').read_bytes()
            (root / 'spec.md').write_bytes(source_bytes)
            with contextlib.chdir(root):
                self.assertEqual(main(args), 0)
            expected = read_json(POD / 'io-map.interface.json')
            for invariant in expected['invariants']:
                invariant['source']['sha256'] = hashlib.sha256(source_bytes).hexdigest()
            self.assertEqual(read_json(out), expected)

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
        self.assertEqual(result['details']['requirement_qualification'], {'sourced': [], 'unqualified-carryover': []})
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
            out = root / 'contract.json'
            args = [str(root / 'io-map.csv'), '--csv-path', 'io-map.csv', '--requirement', 'interface',
                '--source', SOURCE['path'], '--locator', SOURCE['locator'], '--bindings', str(root / 'bindings.json'),
                '--paths', str(root / 'paths.json'), '--out', str(out)]
            stderr, stdout = io.StringIO(), io.StringIO()
            with contextlib.chdir(root), contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(stdout):
                with self.assertRaises(SystemExit) as refused:
                    main(args)
            self.assertEqual(refused.exception.code, 2)
            self.assertIn('missing-source (FileNotFoundError)', stderr.getvalue())
            self.assertIn(SOURCE['path'], stderr.getvalue())
            self.assertEqual(stdout.getvalue(), '')
            self.assertFalse(out.exists())
            source_bytes = b'Original owner-declared indirect paths.\r\n'
            (root / SOURCE['path']).write_bytes(source_bytes)
            with contextlib.chdir(root):
                self.assertEqual(main(args), 0)
            expected = compile_paths()
            for invariant in expected['invariants']:
                invariant['source']['sha256'] = hashlib.sha256(source_bytes).hexdigest()
            self.assertEqual(read_json(out), expected)


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


class RequirementQualificationTests(unittest.TestCase):
    """Requirement citations carry an enforced qualification; unqualified ones are never authority."""
    FIELDS = (*REQUIRED, 'MCU_Path', 'Notes')
    ROWS = [('J1-1', 'CAN_H', '/CAN+', 'R1', '1', 'Harness CAN high', 'direct', 'POD-IF-A; POD-IF-B'),
            ('J1-2', 'CAN_L', '/CAN-', 'R1', '2', 'Harness CAN low', 'direct', 'POD-IF-A'),
            ('PROBE-1', 'NO_CONNECT', 'N/C', 'N/A', 'N/A', 'Plugged cavity', 'N/A', 'plain')]
    REQ = b'requirements\n'
    SRC = dict(path='item4.md', sha256=hashlib.sha256(REQ).hexdigest(), locator='item 4')

    def build(self, **kwargs):
        return compile_(csv_bytes(self.ROWS, fields=self.FIELDS), bindings=probe_bindings(), **kwargs)

    def cited(self, contract):
        return [i['io_map']['requirements'] for i in contract['invariants'] if 'io_map' in i]

    def test_legacy_bare_ids_are_unqualified_and_unchanged(self):
        contract = self.build()
        self.assertIn(['POD-IF-A', 'POD-IF-B'], self.cited(contract))
        self.assertEqual(requirement_authority(contract),
                         {'sourced': [], 'unqualified-carryover': ['POD-IF-A', 'POD-IF-B']})
        self.assertEqual(validate_contract(contract), contract)

    def test_explicit_qualification_emits_status_per_citation(self):
        contract = self.build(qualifications={'POD-IF-A': self.SRC})
        self.assertIn([dict(id='POD-IF-A', qualification='sourced', source=self.SRC),
                       dict(id='POD-IF-B', qualification='unqualified-carryover')], self.cited(contract))
        self.assertEqual(requirement_authority(contract),
                         {'sourced': ['POD-IF-A'], 'unqualified-carryover': ['POD-IF-B']})
        self.assertEqual(requirement_authority(self.build(qualifications={}))['sourced'], [])

    def test_sourced_without_source_or_with_bad_source_is_refused(self):
        for bad in (None, {}, dict(path='requirements.md', locator='x'), {**self.SRC, 'sha256': 'abc'},
                    {**self.SRC, 'path': '../x'}, {**self.SRC, 'extra': 1}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.build(qualifications={'POD-IF-A': bad})
        with self.assertRaisesRegex(ValueError, 'uncited'):
            self.build(qualifications={'POD-IF-Z': self.SRC})

    def test_stripping_or_upgrading_status_is_refused(self):
        original = self.build(qualifications={'POD-IF-A': self.SRC})

        def entries(contract, rid):
            found = {id(r): r for i in contract['invariants'] if 'io_map' in i
                     for r in i['io_map']['requirements'] if isinstance(r, dict) and r['id'] == rid}
            return list(found.values())
        mutations = {
            'strip status': lambda c: [e.pop('qualification') for e in entries(c, 'POD-IF-A')],
            'upgrade without source': lambda c: [e.update(qualification='sourced') for e in entries(c, 'POD-IF-B')],
            'sourced loses source': lambda c: [e.pop('source') for e in entries(c, 'POD-IF-A')],
            'unqualified gains source': lambda c: [e.update(source=self.SRC) for e in entries(c, 'POD-IF-B')],
            'unknown status': lambda c: [e.update(qualification='verified') for e in entries(c, 'POD-IF-B')],
        }
        for name, mutate in mutations.items():
            contract = copy.deepcopy(original)
            mutate(contract)
            with self.subTest(name), self.assertRaises(ValueError):
                validate_contract(contract)

    def test_downgrade_is_visible_and_one_unqualified_citation_taints_the_id(self):
        contract = self.build(qualifications={'POD-IF-A': self.SRC})
        for i in contract['invariants']:
            for k, r in enumerate(i.get('io_map', {}).get('requirements', [])):
                if r == dict(id='POD-IF-A', qualification='sourced', source=self.SRC) and i['id'] == 'J1-2:net':
                    i['io_map']['requirements'][k] = 'POD-IF-A'
        self.assertEqual(requirement_authority(validate_contract(contract))['sourced'], [])

    def test_capability_requires_captured_matching_source_and_reports_authority(self):
        contract = self.build(qualifications={'POD-IF-A': self.SRC})
        raw = csv_bytes(self.ROWS, fields=self.FIELDS)
        scope = dict(baseline=dict(requirements=[dict(id='interface', source=SOURCE, checks=['interface'])]))
        params = dict(contract='contract.json', netlist_check='netlist')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_json(root / 'contract.json', contract)
            (root / 'io-map.csv').write_bytes(raw)
            (root / 'item4.md').write_bytes(self.REQ)
            (root / 'requirements.md').write_bytes(b'x')
            files = {'contract.json', 'io-map.csv', 'requirements.md', 'item4.md'}
            result = capability(params, scope, files, root, 'interface')
            self.assertEqual(result['requirement_authority'],
                             {'sourced': ['POD-IF-A'], 'unqualified-carryover': ['POD-IF-B']})
            with self.assertRaisesRegex(ValueError, 'sourced requirement'):
                capability(params, scope, files - {'item4.md'}, root, 'interface')
            (root / 'item4.md').write_bytes(self.REQ + b'x')
            with self.assertRaisesRegex(ValueError, 'sourced requirement'):
                capability(params, scope, files, root, 'interface')

    def test_cli_qualifications_and_unqualified_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'in.csv').write_bytes(csv_bytes(self.ROWS, fields=self.FIELDS))
            (root / 'requirements.md').write_bytes(self.REQ)
            write_json(root / 'q.json', {'POD-IF-A': self.SRC})
            write_json(root / 'b.json', probe_bindings())
            stdout = io.StringIO()
            with contextlib.chdir(root), contextlib.redirect_stdout(stdout):
                main(['in.csv', '--csv-path', 'io-map.csv', '--requirement', 'interface', '--source',
                      'requirements.md', '--locator', 'item 4', '--bindings', 'b.json',
                      '--qualifications', 'q.json', '--out', 'out.json'])
            self.assertIn('unqualified carry-over requirements (reported, never authority): POD-IF-B',
                          stdout.getvalue())
            self.assertEqual(requirement_authority(read_json(root / 'out.json'))['sourced'], ['POD-IF-A'])


class HistoricalQualificationTests(unittest.TestCase):
    """Original provenance, complete citation set and statuses are bound to a pinned qualification record."""
    FIELDS = RequirementQualificationTests.FIELDS
    ROWS = RequirementQualificationTests.ROWS
    RAW = csv_bytes(ROWS, fields=FIELDS)
    CAPTURED = {'io-map.csv': RAW}
    HISTORY = dict(schema='agent-ee.io-map-history/1', rows={'J1-2': 'v9 table 2 / J1-2'}, sourced={},
                   origin=dict(path='v9/io-map.csv', revision='v9', sha256='a' * 64, locator='v9 table 2'))
    SCOPE = dict(schema='agent-ee.scope/3', checks=[dict(id='interface', kind='hardware_interface',
        params=dict(contract='contract.json', netlist_check='netlist'))],
        baseline=dict(requirements=[dict(id='interface', statement='Preserve declared interface invariants.',
        intent='acceptance', source=SOURCE, checks=['interface'])], targets=['requirements.md'],
        reference_architectures=['requirements.md']))

    def compile_bound(self, history=None, captured=None):
        return compile_(self.RAW, bindings=probe_bindings(), history=history or self.HISTORY,
                        record_path='qualification.json', captured=captured)

    def record(self, history=None, captured=None):
        return build_record(self.compile_bound(history, captured), history or self.HISTORY)

    def capture(self, contract, record, *, pin=True, extra=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / 'io-map.csv').write_bytes(self.RAW)
        (root / 'requirements.md').write_bytes(b'Structural interface\n')
        write_json(root / 'contract.json', contract)
        scope = copy.deepcopy(self.SCOPE)
        if record is not None:
            write_json(root / 'qualification.json', record)
            if pin:
                scope['checks'][0]['params']['qualification_record'] = dict(
                    path='qualification.json', sha256=digest(root / 'qualification.json'))
        for name, data in (extra or {}).items():
            (root / name).write_bytes(data)
        files = {p.name: digest(p) for p in root.iterdir()}
        return scope, files, root

    def run_capability(self, contract, record, **kwargs):
        scope, files, root = self.capture(contract, record, **kwargs)
        return capability(scope['checks'][0]['params'], scope, files, root, 'interface')

    def mutate(self, contract, mode):
        csv_sha = hashlib.sha256(self.RAW).hexdigest()
        for invariant in contract['invariants']:
            if mode == 'strip-io-map':
                invariant.pop('io_map')
                continue
            cite = invariant['io_map']
            if mode == 'bare-ids':
                cite['requirements'] = [r['id'] if isinstance(r, dict) else r for r in cite['requirements']]
            elif mode == 'empty-citations':
                cite['requirements'] = []
            elif mode == 'upgrade-from-csv':
                cite['requirements'] = [dict(id=r['id'] if isinstance(r, dict) else r, qualification='sourced',
                    source=dict(path='io-map.csv', sha256=csv_sha, locator='inherited Notes token'))
                    for r in cite['requirements']]
            elif mode == 'changed-description':
                cite['external_destination'] = 'accidental current-authority description'
            elif mode == 'changed-signal':
                cite['signal'] = 'CHANGED'
        return contract

    MODES = ('bare-ids', 'empty-citations', 'strip-io-map', 'upgrade-from-csv', 'changed-description',
             'changed-signal')

    def test_correct_retention_is_accepted_with_original_provenance(self):
        contract, record = self.compile_bound(), self.record()
        self.assertEqual(contract['qualification_record']['sha256'], hashlib.sha256(json_bytes(record)).hexdigest())
        origins = {r['connector_pin']: r['origin'] for r in record['rows']}
        self.assertEqual(origins['J1-1'], self.HISTORY['origin'])
        self.assertEqual(origins['J1-2'], dict(self.HISTORY['origin'], locator='v9 table 2 / J1-2'))
        self.assertEqual(validate_contract(copy.deepcopy(contract), qualification=json_bytes(record), captured=self.CAPTURED), contract)
        descriptor = self.run_capability(contract, record)
        self.assertEqual(descriptor['requirement_authority'],
                         {'sourced': [], 'unqualified-carryover': ['POD-IF-A', 'POD-IF-B']})
        self.assertTrue(descriptor['requirement_history']['qualification_bound'])
        self.assertEqual(descriptor['requirement_history']['origins']['POD-IF-A'],
                         [self.HISTORY['origin'], dict(self.HISTORY['origin'], locator='v9 table 2 / J1-2')])
        self.assertIn('qualification.json', descriptor['immutable_inputs'])

    def test_loss_upgrade_and_description_changes_refuse_in_validator_and_capability(self):
        original, record = self.compile_bound(), self.record()
        for mode in self.MODES:
            contract = self.mutate(copy.deepcopy(original), mode)
            with self.subTest(mode, where='validator'), self.assertRaises(ValueError):
                validate_contract(contract, qualification=json_bytes(record), captured=self.CAPTURED)
            with self.subTest(mode, where='capability'), self.assertRaises(ValueError):
                self.run_capability(contract, record)

    def test_declaration_cannot_be_stripped_or_left_unpinned(self):
        original, record = self.compile_bound(), self.record()
        stripped = copy.deepcopy(original)
        del stripped['qualification_record']
        with self.assertRaisesRegex(ValueError, 'pinned by the scope'):
            self.run_capability(stripped, record)
        with self.assertRaisesRegex(ValueError, 'must be pinned'):
            self.run_capability(original, record, pin=False)
        with self.assertRaisesRegex(ValueError, 'declares none'):
            validate_contract(stripped, qualification=json_bytes(record), captured=self.CAPTURED)

    def test_record_tampering_and_missing_provenance_refuse(self):
        original, record = self.compile_bound(), self.record()
        scope, files, root = self.capture(original, record)
        write_json(root / 'qualification.json', record)
        tampered = copy.deepcopy(record)
        tampered['rows'][0]['requirements'][0] = dict(id='POD-IF-A', qualification='sourced', source=dict(
            path='x.md', sha256='b' * 64, locator='forged'))
        write_json(root / 'qualification.json', tampered)
        with self.assertRaisesRegex(ValueError, 'matching sha256'):
            capability(scope['checks'][0]['params'], scope, files, root, 'interface')
        for mutate in (lambda r: r['rows'][0].pop('origin'),
                       lambda r: r['rows'][0]['origin'].pop('revision'),
                       lambda r: r['rows'][0]['origin'].update(sha256='bad'),
                       lambda r: r['rows'].pop(),
                       lambda r: r['rows'][0].update(requirements=['POD-IF-A'])):
            broken = copy.deepcopy(record)
            mutate(broken)
            # re-pin the declaration so the shape and binding checks, not the digest, must refuse
            pinned = copy.deepcopy(original)
            pinned['qualification_record']['sha256'] = hashlib.sha256(json_bytes(broken)).hexdigest()
            with self.assertRaises(ValueError):
                validate_contract(pinned, qualification=json_bytes(broken), captured=self.CAPTURED)

    def test_legacy_inputs_stay_accepted_but_cannot_upgrade_from_the_inherited_csv(self):
        legacy = compile_(self.RAW, bindings=probe_bindings())
        descriptor = self.run_capability(legacy, None)
        self.assertEqual(descriptor['requirement_authority']['sourced'], [])
        self.assertFalse(descriptor['requirement_history']['qualification_bound'])
        explicit = compile_(self.RAW, bindings=probe_bindings(), qualifications={})
        self.assertEqual(self.run_capability(explicit, None)['requirement_authority']['unqualified-carryover'],
                         ['POD-IF-A', 'POD-IF-B'])
        for contract in (legacy, explicit):
            upgraded = self.mutate(copy.deepcopy(contract), 'upgrade-from-csv')
            with self.assertRaisesRegex(ValueError, 'inherited io_map CSV'):
                validate_contract(upgraded)
            with self.assertRaises(ValueError):
                self.run_capability(upgraded, None)
        entry = explicit['invariants'][0]['io_map']['requirements'][0]
        entry['source'] = dict(path='io-map.csv', sha256='a' * 64, locator='original v9 row')
        with self.assertRaises(ValueError):
            validate_contract(explicit)

    def test_history_compile_refusals(self):
        for mutate in (lambda h: h.pop('origin'), lambda h: h['origin'].update(sha256='x'),
                       lambda h: h.update(schema='other'), lambda h: h.update(extra=1),
                       lambda h: h['sourced'].update({'POD-IF-A': dict(path='n.md', locator='x')}),
                       lambda h: h['sourced'].update({'POD-IF-Z': dict(path='n.md', sha256='c' * 64, locator='x')})):
            history = copy.deepcopy(self.HISTORY)
            mutate(history)
            with self.subTest(), self.assertRaises(ValueError):
                self.compile_bound(history)
        with self.assertRaisesRegex(ValueError, 'requires record_path'):
            compile_(self.RAW, bindings=probe_bindings(), history=self.HISTORY)

    def test_mixed_statuses_in_record_are_reported_and_origin_retained(self):
        body = b'owner item 4\n'
        history = copy.deepcopy(self.HISTORY)
        history['sourced'] = {'POD-IF-A': dict(path='item4.md', sha256=hashlib.sha256(body).hexdigest(),
                                               locator='item 4')}
        # the compiler verifies through the same loader: a sourced source must be supplied as matching bytes
        for captured in (None, {'item4.md': b'x'}):
            with self.subTest(captured=captured), self.assertRaisesRegex(
                    ValueError, 'sourced requirement must be captured'):
                self.compile_bound(history, captured)
        item = history['sourced']['POD-IF-A']
        contract, record = self.compile_bound(history, {'item4.md': body}), self.record(history, {'item4.md': body})
        self.assertEqual(requirement_authority(contract),
                         {'sourced': ['POD-IF-A'], 'unqualified-carryover': ['POD-IF-B']})
        scope, files, root = self.capture(contract, record, extra={'item4.md': b'x'})
        with self.assertRaisesRegex(ValueError, 'sourced requirement must be captured'):
            capability(scope['checks'][0]['params'], scope, files, root, 'interface')
        descriptor = self.run_capability(contract, record, extra={'item4.md': body})
        self.assertEqual(descriptor['requirement_authority']['sourced'], ['POD-IF-A'])
        upgraded = copy.deepcopy(contract)
        for invariant in upgraded['invariants']:
            invariant['io_map']['requirements'] = [
                dict(id=r['id'] if isinstance(r, dict) else r, qualification='sourced', source=dict(item))
                for r in invariant['io_map']['requirements']]
        with self.assertRaisesRegex(ValueError, 'differs from qualification record'):
            self.run_capability(upgraded, record, extra={'item4.md': body})

    def test_declared_pin_is_enforced_by_the_validator(self):
        """Sol round-2 H1 reproductions: a record not matching the declared pin, or none at all, never validates."""
        contract, record = self.compile_bound(), self.record()
        pin = contract['qualification_record']['sha256']
        # (a) only the supplied record's original revision changes
        altered = copy.deepcopy(record)
        altered['rows'][0]['origin']['revision'] = 'accidentally wrong revision'
        self.assertNotEqual(hashlib.sha256(json_bytes(altered)).hexdigest(), pin)
        with self.assertRaisesRegex(ValueError, 'differ from the declared sha256 pin'):
            validate_contract(copy.deepcopy(contract), qualification=json_bytes(altered), captured=self.CAPTURED)
        # (b) record and matching citations upgraded together, pin unchanged
        source = dict(path='current.md', sha256=hashlib.sha256(b'current\n').hexdigest(), locator='item 4')
        upgraded, forged = copy.deepcopy(record), copy.deepcopy(contract)
        for row in upgraded['rows']:
            row['requirements'] = [dict(id=e['id'], qualification='sourced', source=source) for e in row['requirements']]
        for invariant in forged['invariants']:
            invariant['io_map']['requirements'] = [
                dict(id=e['id'], qualification='sourced', source=source) for e in invariant['io_map']['requirements']]
        with self.assertRaisesRegex(ValueError, 'differ from the declared sha256 pin'):
            validate_contract(forged, qualification=json_bytes(upgraded), captured=self.CAPTURED)
        # (c) record omitted: emptied lists, and the untouched contract, are both refused
        emptied = copy.deepcopy(contract)
        for invariant in emptied['invariants']:
            invariant['io_map']['requirements'] = []
        for bad in (emptied, copy.deepcopy(contract)):
            with self.assertRaisesRegex(ValueError, 'requires its qualification record bytes'):
                validate_contract(bad)
        # non-bytes and mixed shape-only supply are refused; the correct record still validates
        with self.assertRaisesRegex(ValueError, 'exact bytes'):
            validate_contract(copy.deepcopy(contract), qualification=record)
        with self.assertRaisesRegex(ValueError, 'captured io_map CSV'):
            validate_contract(copy.deepcopy(contract), qualification=json_bytes(record))
        self.assertEqual(validate_contract(copy.deepcopy(contract), qualification=json_bytes(record), captured=self.CAPTURED), contract)
        # the explicit shape-only mode is available to loaders and is not a verification
        self.assertEqual(validate_contract_shape(copy.deepcopy(emptied)), emptied)

    def test_historical_origin_is_verified_only_against_captured_bytes(self):
        body = b'v9 io-map bytes\n'
        history = copy.deepcopy(self.HISTORY)
        history['origin'].update(path='v9-io-map.csv')
        history['origin']['sha256'] = hashlib.sha256(body).hexdigest()
        report = {}
        compile_(self.RAW, bindings=probe_bindings(), history=history, record_path='qualification.json',
                 report=report)
        self.assertEqual(report['origin_verification'], 'declared-unverified')
        with self.assertRaisesRegex(ValueError, 'origin bytes differ'):
            compile_(self.RAW, bindings=probe_bindings(), history=history, record_path='qualification.json',
                     origin_bytes=b'other')
        compile_(self.RAW, bindings=probe_bindings(), history=history, record_path='qualification.json',
                 origin_bytes=body, report=report)
        self.assertEqual(report['origin_verification'], 'verified')
        contract, record = self.compile_bound(history), self.record(history)
        unverified = self.run_capability(contract, record)['requirement_history']
        self.assertEqual(unverified['origin_verification'], {'v9-io-map.csv': 'declared-unverified'})
        self.assertEqual({r['requirements']['POD-IF-A'] for r in unverified['rows']
                          if 'POD-IF-A' in r['requirements']}, {'unqualified historical assertion'})
        verified = self.run_capability(contract, record, extra={'v9-io-map.csv': body})['requirement_history']
        self.assertEqual(verified['origin_verification'], {'v9-io-map.csv': 'verified'})
        with self.assertRaisesRegex(ValueError, 'captured historical source .* differs'):
            self.run_capability(contract, record, extra={'v9-io-map.csv': b'tampered'})

    def coverage_scope(self, contract, record):
        scope, files, root = self.capture(contract, record)
        scope['checks'].insert(0, dict(id='netlist', kind='netlist'))
        scope['baseline']['requirements'][0]['checks'].insert(0, 'netlist')
        scope.update(schematic='interface.kicad_sch', board='interface.kicad_pcb')
        for name in ('interface.kicad_sch', 'interface.kicad_pcb'):
            (root / name).write_bytes(b'synthetic inventory placeholder')
        return scope, {p.name: digest(p) for p in root.iterdir()}, root

    def test_coverage_reports_unqualified_ids_without_counting_them(self):
        contract, record = self.compile_bound(), self.record()
        scope, files, root = self.coverage_scope(contract, record)
        coverage = compile_coverage(scope, files, root)
        cited = coverage['requirement_citations']
        self.assertEqual(cited['unqualified-carryover'], {'interface': ['POD-IF-A', 'POD-IF-B']})
        self.assertEqual(cited['qualified'], [])
        self.assertIs(cited['counted_as_coverage'], False)
        detail = coverage['checks']['interface']['requirement_qualification']
        self.assertTrue(detail['qualification_bound'])
        self.assertEqual(detail['origins']['POD-IF-B'], [self.HISTORY['origin']])
        # structural predicates stay graded by their independently bound requirement source
        self.assertTrue(coverage['checks']['interface']['can_evaluate_bound'])
        self.assertEqual(coverage['requirements']['interface']['acceptance_checks'], ['interface'])

    def test_coverage_refuses_loss_and_upgrade_and_reports_mixed(self):
        contract, record = self.compile_bound(), self.record()
        for mode in ('bare-ids', 'empty-citations', 'strip-io-map', 'upgrade-from-csv', 'changed-description'):
            bad = self.mutate(copy.deepcopy(contract), mode)
            scope, files, root = self.coverage_scope(bad, record)
            with self.subTest(mode), self.assertRaises(ValueError):
                compile_coverage(scope, files, root)
        body = b'owner item 4\n'
        history = copy.deepcopy(self.HISTORY)
        history['sourced'] = {'POD-IF-A': dict(path='item4.md', sha256=hashlib.sha256(body).hexdigest(), locator='i4')}
        contract, record = self.compile_bound(history, {'item4.md': body}), self.record(history, {'item4.md': body})
        scope, files, root = self.capture(contract, record, extra={'item4.md': body})
        scope['checks'].insert(0, dict(id='netlist', kind='netlist'))
        scope['baseline']['requirements'][0]['checks'].insert(0, 'netlist')
        scope.update(schematic='interface.kicad_sch', board='interface.kicad_pcb')
        for name in ('interface.kicad_sch', 'interface.kicad_pcb'):
            (root / name).write_bytes(b'synthetic inventory placeholder')
        files = {p.name: digest(p) for p in root.iterdir()}
        cited = compile_coverage(scope, files, root)['requirement_citations']
        self.assertEqual((cited['qualified'], cited['unqualified-carryover']),
                         (['POD-IF-A'], {'interface': ['POD-IF-B']}))

    def test_legacy_coverage_without_citations_is_unchanged(self):
        contract = copy.deepcopy(compile_(self.RAW, bindings=probe_bindings()))
        for invariant in contract['invariants']:
            invariant['io_map']['requirements'] = []
        scope, files, root = self.coverage_scope(contract, None)
        coverage = compile_coverage(scope, files, root)
        self.assertNotIn('requirement_citations', coverage)
        self.assertNotIn('requirement_qualification', coverage['checks']['interface'])

    def test_spec_traceability_separates_qualified_from_token_presence(self):
        spec = read_json(POD / 'spec-record.json')
        raw = (POD / 'io-map.csv').read_bytes()
        kwargs = dict(csv_path='io-map.csv', requirement='POD-AC-1', spec_record=spec,
                      source=dict(path='spec.md', locator='Acceptance criteria / item 1'))
        for extra in ({}, dict(qualifications={})):
            report = {}
            contract = compile_io_map(raw, report=report, **extra, **kwargs)
            self.assertEqual(report['traceability']['qualified'], [])
            self.assertEqual(len(report['traceability']['unqualified-carryover']), 8)
            self.assertEqual(report['traceability']['missing'], [])
        sourced = dict(path='cap/cite.md', sha256='d' * 64, locator='item 1')
        report = {}
        compile_io_map(raw, report=report, qualifications={'POD-IF-CAN1': sourced}, **kwargs)
        self.assertEqual(report['traceability']['qualified'], ['POD-IF-CAN1'])
        self.assertNotIn('POD-IF-CAN1', report['traceability']['unqualified-carryover'])
        self.assertEqual(traceability(contract, spec)['missing'], [])

    def test_cli_history_writes_record_and_reports_split(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'in.csv').write_bytes(self.RAW)
            (root / 'requirements.md').write_bytes(b'r\n')
            write_json(root / 'h.json', self.HISTORY)
            write_json(root / 'b.json', probe_bindings())
            argv = ['in.csv', '--csv-path', 'io-map.csv', '--requirement', 'interface', '--source',
                    'requirements.md', '--locator', 'x', '--bindings', 'b.json', '--out', 'out.json']
            with contextlib.chdir(root):
                with self.assertRaises(SystemExit) as refused:
                    main([*argv, '--history', 'h.json'])
                self.assertEqual(refused.exception.code, 2)
                with contextlib.redirect_stdout(io.StringIO()):
                    main([*argv, '--history', 'h.json', '--record-path', 'qualification.json',
                          '--record-out', 'record.json'])
            contract, record = read_json(root / 'out.json'), read_json(root / 'record.json')
            self.assertEqual(contract['qualification_record'],
                             dict(path='qualification.json', sha256=digest(root / 'record.json')))
            self.assertEqual(validate_contract(contract, qualification=(root / 'record.json').read_bytes(),
                                               captured=self.CAPTURED), contract)


    def test_cli_refuses_null_inputs_and_requires_captured_sourced_bytes(self):
        """A supplied JSON file whose value is null is refused, never read as absent; sourced bytes are captured."""
        body = b'owner item 4\n'
        history = copy.deepcopy(self.HISTORY)
        history['sourced'] = {'POD-IF-A': dict(path='item4.md', sha256=hashlib.sha256(body).hexdigest(),
                                               locator='item 4')}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'in.csv').write_bytes(self.RAW)
            (root / 'requirements.md').write_bytes(b'r\n')
            (root / 'item4.md').write_bytes(body)
            (root / 'null.json').write_bytes(b'null')
            write_json(root / 'h.json', history)
            write_json(root / 'b.json', probe_bindings())
            argv = ['in.csv', '--csv-path', 'io-map.csv', '--requirement', 'interface', '--source',
                    'requirements.md', '--locator', 'x', '--bindings', 'b.json', '--out', 'out.json']
            bound = ['--record-path', 'qualification.json', '--record-out', 'record.json']
            with contextlib.chdir(root):
                for extra in (['--qualifications', 'null.json'], ['--history', 'null.json', *bound],
                              ['--history', 'h.json', *bound]):
                    with self.subTest(extra), self.assertRaises(SystemExit) as refused, \
                            contextlib.redirect_stderr(io.StringIO()):
                        main([*argv, *extra])
                    self.assertEqual(refused.exception.code, 2)
                with contextlib.redirect_stdout(io.StringIO()):
                    main([*argv, '--history', 'h.json', *bound, '--captured', 'item4.md'])
            contract = read_json(root / 'out.json')
            self.assertEqual(requirement_authority(contract)['sourced'], ['POD-IF-A'])
            self.assertEqual(contract['qualification_record']['sha256'], digest(root / 'record.json'))
            self.assertEqual(validate_contract(contract, qualification=(root / 'record.json').read_bytes(),
                                               captured={'io-map.csv': self.RAW, 'item4.md': body}), contract)
            with self.assertRaisesRegex(ValueError, 'require history'):
                compile_(self.RAW, bindings=probe_bindings(), record_path='qualification.json')

class NativeQualificationTests(unittest.TestCase):
    """Native pass keeps independent structural authority while carry-over is reported, never graded."""

    def run_native(self, bound):
        require_toolchain(native=True)
        base = RequirementQualificationTests()
        raw = csv_bytes(base.ROWS, fields=base.FIELDS)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        history = HistoricalQualificationTests.HISTORY
        if bound:
            contract = compile_(raw, bindings=probe_bindings(), history=history, record_path='qualification.json')
            write_json(root / 'qualification.json', build_record(contract, history))
        else:
            contract = base.build(qualifications={})
        schematic = generator.schematic('disconnect').replace('X1', 'J1').replace('"IN"', '"CAN+"').replace('"OUT"', '"CAN-"')
        (root / 'interface.kicad_sch').write_text(schematic)
        for filename in ('interface.kicad_pcb', 'requirements.md'):
            (root / filename).write_bytes((FIXTURE / filename).read_bytes())
        (root / 'io-map.csv').write_bytes(raw)
        write_json(root / 'contract.json', contract)
        scope = read_json(FIXTURE / 'control.scope.json')
        scope['checks'] = scope['checks'][:2]
        scope['baseline']['requirements'] = scope['baseline']['requirements'][:1]
        scope['inputs'].append(dict(path='io-map.csv', at='io-map.csv'))
        if bound:
            scope['inputs'].append(dict(path='qualification.json', at='qualification.json'))
            scope['checks'][1]['params']['qualification_record'] = dict(
                path='qualification.json', sha256=digest(root / 'qualification.json'))
        write_json(root / 'scope.json', scope)
        run = root / 'run'
        run.mkdir()
        capture(root / 'scope.json', run)
        return run_checks(run, time.monotonic() + 90)[1]

    def test_unqualified_citations_are_reported_never_graded(self):
        for bound in (False, True):
            with self.subTest(bound=bound):
                result = self.run_native(bound)
                self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
                self.assertEqual(result['details']['requirement_qualification'],
                                 {'sourced': [], 'unqualified-carryover': ['POD-IF-A', 'POD-IF-B']})
                self.assertTrue(any('never graded' in x for x in result['limitations']), result)
