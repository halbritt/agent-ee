"""Bounded single-symbol capture from over-limit libraries (AGENTEE-110).

Owned synthetic libraries below are original to these tests and redistributable.
The stock test reads the installed MCU_ST_STM32H7 library at run time; its
captured bytes are never written into the repository. Expected stock pins were
read independently from the raw library text, not from the capture or resolver.
"""
import contextlib
import hashlib
import io
import json
import os
import random
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from agent_ee.connectivity_ir import resolve_intent
from agent_ee.schematic_emitter import emit_schematic
from agent_ee.symbol_capture import (LIMITS, CaptureError, bind_resolved, capture_symbols, main,
                                     scan_library, verify_capture)
from agent_ee.symbol_library import (NODE_LIMIT, LibraryError, children, parse_library, parse_sexpr,
                                     resolve_symbol)
from test_schematic_emitter import PROJECT, ROOT, native_netlist, run_native
from toolchain import require_toolchain

STOCK = Path('/usr/share/kicad/symbols/MCU_ST_STM32H7.kicad_sym')
HEADER = '\t(version 20251024)\n\t(generator "agent_ee_test")\n\t(generator_version "10.0")\n'


def pin(number, name, y, kind='passive'):
    return (f'(pin {kind} line (at -5.08 {y} 0) (length 2.54) (name "{name}") (number "{number}"))')


def base(name, *pins):
    body = ' '.join(pin(n, label, y) for n, label, y in pins)
    return (f'\t(symbol "{name}" (property "Reference" "U" (at 0 0 0)) (property "Value" "{name}" (at 0 0 0))\n'
            f'\t\t(symbol "{name}_1_1" {body})\n\t)\n')


def derived(name, parent):
    return f'\t(symbol "{name}" (extends "{parent}") (property "Value" "{name}" (at 0 0 0)))\n'


def library(*symbols, header=HEADER, tail=')\n'):
    return ('(kicad_symbol_lib\n' + header + ''.join(symbols) + tail).encode('utf-8')


def capture(data, *names, **kwargs):
    return capture_symbols(data, list(names), library=kwargs.pop('library', 'Owned'), **kwargs)


def oversized(*symbols):
    """Owned library above the shared node limit: filler symbols after ``symbols``."""
    filler = ''.join(f'\t(symbol "FILL_{k:04d}" (fill{" x" * 2100}))\n' for k in range(1000))
    return library(*symbols, filler)


class ScanBoundaryTests(unittest.TestCase):
    def test_inherited_closure_keeps_exact_source_bytes_and_order(self):
        parts = [derived('LEAF', 'MID'), base('OTHER', ('1', 'A', 0)),
                 base('ROOT', ('1', 'IN', 2.54), ('2', 'OUT', -2.54)), derived('MID', 'ROOT')]
        data = library(*parts)
        output, receipt = capture(data, 'LEAF')
        # Independent oracle: header plus the three source spans in source order.
        expected = ('(kicad_symbol_lib\n' + HEADER + parts[0] + parts[2] + parts[3] + ')\n').encode()
        self.assertEqual(output, expected)
        self.assertEqual([(s['name'], s['role'], s['extends']) for s in receipt['symbols']],
                         [('LEAF', 'selected', 'MID'), ('ROOT', 'ancestor', None), ('MID', 'ancestor', 'ROOT')])
        for entry, part in zip(receipt['symbols'], (parts[0], parts[2], parts[3])):
            self.assertEqual(entry['sha256'], hashlib.sha256(part[1:-1].encode()).hexdigest())
        self.assertEqual(receipt['source']['sha256'], hashlib.sha256(data).hexdigest())
        self.assertEqual(receipt['output'], dict(path='Owned.kicad_sym', bytes=len(output),
                                                 sha256=hashlib.sha256(output).hexdigest()))
        pins, sources, _ = resolve_symbol(parse_library(output), 'LEAF', 'Owned.kicad_sym', 'h')
        self.assertEqual([s['symbol'] for s in sources], ['ROOT', 'MID', 'LEAF'])
        self.assertEqual(sorted((p['number'], p['name']) for p in pins), [('1', 'IN'), ('2', 'OUT')])

    def test_request_order_and_repetition_are_deterministic(self):
        data = library(base('B', ('1', 'A', 0)), base('A', ('1', 'A', 0)), base('C', ('1', 'A', 0)))
        first = capture(data, 'A', 'B')
        self.assertEqual(capture(data, 'B', 'A'), first)
        self.assertEqual(capture(data, 'A', 'B'), first)
        self.assertEqual([s['name'] for s in first[1]['symbols']], ['B', 'A'])
        self.assertEqual(first[1]['selected'], ['A', 'B'])
        self.assertEqual(sorted(parse_library(first[0])), ['A', 'B'])

    def test_quoted_text_escapes_and_comments_are_not_structure(self):
        tricky = ('\t(symbol "Q\\"1" ; comment ) ( "\n'
                  '\t\t(property "Reference" "U" (at 0 0 0))\n'
                  '\t\t(property "Value" "a \\"b\\" ) (symbol \\"FAKE\\" ;" (at 0 0 0))\n'
                  '\t\t(symbol "Q\\"1_1_1" ' + pin('1', 'P(1', 0) + ')\n\t)\n')
        data = library('; (symbol "GHOST" top-level comment\n', tricky, base('NEXT', ('1', 'A', 0)))
        _, symbols = scan_library(data)[1:]
        self.assertEqual(sorted(symbols), ['NEXT', 'Q"1'])
        output, receipt = capture(data, 'Q"1')
        self.assertIn(tricky[1:-1].encode(), output)
        self.assertNotIn(b'GHOST', output)
        pins = resolve_symbol(parse_library(output), 'Q"1', 'p', 'h')[0]
        self.assertEqual([(p['number'], p['name']) for p in pins], [('1', 'P(1')])
        value = [p for p in children(parse_library(output)['Q"1'], 'property') if p[1] == 'Value'][0][2]
        self.assertEqual(value, 'a "b" ) (symbol "FAKE" ;')

    def test_malformed_suffix_refuses_after_a_complete_selected_symbol(self):
        good = base('A', ('1', 'X', 0))
        for tail in (')\n(extra)\n', ')\natom\n', ')\n)\n', '', '\t(symbol "Z" "open)\n)\n',
                     '\t(symbol "Z" "esc\\', '\t(symbol "Z"\n', ')\n\x00\n', ')\n﻿'):
            data = library(good, tail=tail)
            with self.subTest(tail=tail), self.assertRaises(CaptureError):
                capture(data, 'A')
        with self.assertRaises(CaptureError):
            capture(library(good) + b'\n(\xff)', 'A')

    def test_cycles_missing_duplicate_and_ambiguous_identities_refuse(self):
        good = base('A', ('1', 'X', 0))
        cases = (
            ('extends cycle', library(derived('A', 'B'), derived('B', 'A')), 'A'),
            ('extends cycle: S -> S', library(derived('S', 'S')), 'S'),
            ('missing parent symbol: GONE', library(derived('A', 'GONE')), 'A'),
            ('unknown symbol: A', library(base('B', ('1', 'X', 0))), 'A'),
            ('duplicate symbol identity: P', library(derived('A', 'P'), base('P', ('1', 'X', 0)),
                                                     base('P', ('1', 'Y', 0))), 'A'),
            ('duplicate symbol identity: A', library(good, derived('A', 'A')), 'A'),
            ('ambiguous extends', library('\t(symbol "A" (extends "B") (extends "C"))\n',
                                          base('B', ('1', 'X', 0)), base('C', ('1', 'X', 0))), 'A'),
            ('invalid symbol identity', library('\t(symbol (A))\n', good), 'A'),
            ('unsupported library-level node: lib_symbols', library('\t(lib_symbols)\n', good), 'A'),
            ('unsupported library-level atom', library('\tstray\n', good), 'A'),
            ('unsupported library-level node: version', library(good, header=HEADER + '\t(version 1)\n'), 'A'),
            ('exactly one complete library root', library(good) + b'(kicad_symbol_lib)', 'A'),
            ('must start with kicad_symbol_lib', library(good).replace(b'kicad_symbol_lib', b'footprint', 1), 'A'),
        )
        for message, data, selected in cases:
            with self.subTest(message=message), self.assertRaisesRegex(CaptureError, re.escape(message)):
                capture(data, selected)
        for selected in ([], ['A', 'A'], [''], 'A', [None]):
            with self.subTest(selected=selected), self.assertRaises(CaptureError):
                capture_symbols(library(base('A', ('1', 'X', 0))), selected, library='Owned')
        for name in ('', 'a/b', '../x', 'x' * 65, None):
            with self.subTest(library=name), self.assertRaises(CaptureError):
                capture_symbols(library(base('A', ('1', 'X', 0))), ['A'], library=name)

    def test_unsupported_symbol_semantics_refuse_through_shared_resolver(self):
        replacement = ('\t(symbol "D" (extends "B")\n\t\t(symbol "D_1_1" ' + pin('2', 'Y', 0) + ')\n\t)\n')
        with self.assertRaisesRegex(LibraryError, 'replacement graphics'):
            capture(library(base('B', ('1', 'X', 0)), replacement), 'D')

    def test_each_resource_bound_is_separate_and_cannot_be_raised(self):
        data = library(base('ROOT', ('1', 'A', 0)), derived('MID', 'ROOT'), derived('LEAF', 'MID'))
        output, receipt = capture(data, 'LEAF')
        sizes = [s['bytes'] for s in receipt['symbols']]
        nodes = [s['nodes'] for s in receipt['symbols']]
        for limits, message in ((dict(closure_symbols=2), '2 symbols'),
                                (dict(closure_bytes=sum(sizes) - 1), 'bytes'),
                                (dict(closure_nodes=sum(nodes) - 1), 'nodes'),
                                (dict(scan_nodes=10), 'scan node limit'),
                                (dict(source_bytes=len(data) - 1), 'at most')):
            with self.subTest(limits=limits), self.assertRaisesRegex(CaptureError, message):
                capture(data, 'LEAF', limits=limits)
        self.assertEqual(capture(data, 'LEAF', limits=dict(closure_symbols=3, closure_bytes=sum(sizes),
                                                           closure_nodes=sum(nodes)))[0], output)
        for key, value in LIMITS.items():
            for bad in (value + 1, 0, True, 1.0):
                with self.subTest(key=key, bad=bad), self.assertRaises(ValueError):
                    capture(data, 'LEAF', limits={key: bad})
        with self.assertRaises(ValueError):
            capture(data, 'LEAF', limits=dict(unknown=1))
        deep = library('\t(symbol "A" ' + '(x ' * 127 + ')' * 127 + ')\n')
        with self.assertRaisesRegex(CaptureError, 'nesting limit exceeded'):
            capture(deep, 'A')
        long_chain = [base('S0', ('1', 'A', 0))] + [derived(f'S{k}', f'S{k - 1}') for k in range(1, 70)]
        with self.assertRaisesRegex(CaptureError, 'closure exceeds 64 symbols'):
            capture(library(*long_chain), 'S69')

    def test_scanner_agrees_with_shared_reader_on_generated_inputs(self):
        """Wherever the scan accepts, the shared reader gives the same symbols and nodes."""
        fragments = ['(', ')', ' ', '\t', '\n', '\x1c', '\xa0', '"', '\\', 'x', 'symbol', '"s\\"(;"',
                     '; c ) (\n', ';', '"a\nb"', ' ', 'extends']
        rng = random.Random(110)
        accepted = 0
        for _ in range(3000):
            body = ''.join(rng.choice(fragments) for _ in range(rng.randrange(1, 14)))
            data = ('(kicad_symbol_lib (version 1) (symbol "K") (symbol ' + body + '))').encode()
            try:
                text, _, symbols = scan_library(data)
            except CaptureError:
                continue
            accepted += 1
            shared = parse_library(data)
            self.assertEqual(sorted(shared), sorted(symbols), data)
            for name, item in symbols.items():
                span = text[item['start']:item['end']].encode()
                self.assertEqual(parse_sexpr(span), shared[name], data)
        self.assertGreater(accepted, 100)


class OversizedLibraryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = oversized(base('ROOT', ('1', 'IN', 2.54), ('2', 'OUT', -2.54)), derived('PART', 'ROOT'))

    def intent(self, data, path='symbols/Big.kicad_sym'):
        return dict(schema='agent-ee.connectivity-intent/1',
                    libraries=[dict(name='Big', path=path, sha256=hashlib.sha256(data).hexdigest())],
                    components=[dict(id='u', reference='U1', lib_id='Big:PART', value='PART', footprint='',
                                     units=[1])],
                    nets=[], no_connects=[dict(component='u', pin=dict(number='1')),
                                          dict(component='u', pin=dict(number='2'))])

    def test_whole_library_fails_closed_naming_limit_and_extraction_route(self):
        with self.assertRaises(LibraryError) as caught:
            parse_library(self.data)
        self.assertIn(f'({NODE_LIMIT} nodes)', str(caught.exception))
        self.assertIn('python -m agent_ee.symbol_capture', str(caught.exception))
        record = resolve_intent(self.intent(self.data), {'symbols/Big.kicad_sym': self.data})
        self.assertEqual((record['execution'], record['outcome'], record['coverage']), ('failed', 'unknown', 'missing'))
        # The refused library leaves its component, and so both no-connects, unavailable.
        self.assertEqual([f['code'] for f in record['findings']],
                         ['library_coverage', 'unknown_component', 'unknown_component'])
        self.assertIn('2000000 nodes', record['findings'][0]['message'])
        self.assertIn('agent_ee.symbol_capture', record['findings'][0]['message'])
        self.assertEqual(record['components'], [])

    def test_captured_subset_resolves_and_binds_provenance(self):
        output, receipt = capture_symbols(self.data, ['PART'], library='Big', source_file='Big.kicad_sym')
        self.assertEqual(sorted(parse_library(output)), ['PART', 'ROOT'])
        record = resolve_intent(self.intent(output), {'symbols/Big.kicad_sym': output})
        self.assertEqual((record['execution'], record['outcome'], record['coverage']), ('ok', 'pass', 'complete'),
                         record['findings'])
        self.assertEqual([s['symbol'] for s in record['components'][0]['sources']], ['ROOT', 'PART'])
        binding = bind_resolved(receipt, record)
        self.assertEqual(binding['source_sha256'], hashlib.sha256(self.data).hexdigest())
        self.assertEqual(binding['library'], dict(name='Big', path='symbols/Big.kicad_sym',
                                                  sha256=hashlib.sha256(output).hexdigest()))
        self.assertEqual([s['name'] for s in binding['symbols']], ['ROOT', 'PART'])
        self.assertEqual(binding['components'], ['u'])
        self.assertEqual(verify_capture(receipt, output, self.data), receipt)

    def test_tampered_capture_receipt_or_binding_refuses(self):
        output, receipt = capture_symbols(self.data, ['PART'], library='Big', source_file='Big.kicad_sym')
        with self.assertRaisesRegex(CaptureError, 're-derive'):
            verify_capture(receipt, output.replace(b'"IN"', b'"XN"'), self.data)
        with self.assertRaisesRegex(CaptureError, 're-derive'):
            verify_capture(receipt, output, self.data.replace(b'FILL_0000', b'FILL_A000'))
        forged = json.loads(json.dumps(receipt))
        forged['source']['sha256'] = '0' * 64
        with self.assertRaisesRegex(CaptureError, 'identity mismatch'):
            verify_capture(forged, output, self.data)
        with self.assertRaisesRegex(CaptureError, 'identity mismatch'):
            bind_resolved(forged, resolve_intent(self.intent(output), {'symbols/Big.kicad_sym': output}))
        other, _ = capture_symbols(self.data, ['ROOT'], library='Big')
        intent = self.intent(other)
        intent['components'][0]['lib_id'] = 'Big:ROOT'
        with self.assertRaisesRegex(CaptureError, 'exactly once'):
            bind_resolved(receipt, resolve_intent(intent, {'symbols/Big.kicad_sym': other}))
        renamed = self.intent(output)
        renamed['libraries'][0]['name'] = 'Other'
        renamed['components'][0]['lib_id'] = 'Other:PART'
        with self.assertRaisesRegex(CaptureError, 'exactly once'):
            bind_resolved(receipt, resolve_intent(renamed, {'symbols/Big.kicad_sym': output}))
        incomplete = self.intent(output)
        incomplete['no_connects'].pop()
        with self.assertRaises(ValueError):
            bind_resolved(receipt, resolve_intent(incomplete, {'symbols/Big.kicad_sym': output}))

    def test_cli_publishes_exclusively_and_never_touches_the_source(self):
        with tempfile.TemporaryDirectory(prefix='agent-ee-capture-cli-') as tmp:
            source = Path(tmp) / 'Big.kicad_sym'
            source.write_bytes(self.data)
            out = Path(tmp) / 'capture'
            with contextlib.redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(main([str(source), '--symbol', 'PART', '--out', str(out)]), 0)
            self.assertEqual(json.loads(stdout.getvalue())['source_sha256'], hashlib.sha256(self.data).hexdigest())
            self.assertEqual(sorted(p.name for p in out.iterdir()), ['Big.kicad_sym', 'capture.json'])
            receipt = json.loads((out / 'capture.json').read_text())
            self.assertEqual(receipt['source']['file'], 'Big.kicad_sym')
            self.assertEqual(verify_capture(receipt, (out / 'Big.kicad_sym').read_bytes(), self.data), receipt)
            self.assertEqual(source.read_bytes(), self.data)
            before = sorted(p.name for p in Path(tmp).iterdir())
            with contextlib.redirect_stderr(io.StringIO()) as stderr:
                self.assertEqual(main([str(source), '--symbol', 'PART', '--out', str(out)]), 1)
                self.assertEqual(main([str(source), '--symbol', 'MISSING', '--out', str(Path(tmp) / 'refused')]), 1)
            self.assertIn('unknown symbol: MISSING', stderr.getvalue())
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), before)

    def test_emitted_sheet_from_capture_equals_emission_from_owned_library(self):
        intent, layout, lib = owned_capture_inputs()
        original = json.loads((ROOT / 'regulator.intent.json').read_text())
        captures = {x['path']: (PROJECT / x['path']).read_bytes() for x in original['libraries']}
        reference = emit_schematic(original, captures, layout)[1]
        result, files = emit_schematic(intent, lib, layout)
        self.assertEqual(result['execution'], 'ok', result['findings'])
        self.assertEqual(files['regulator.kicad_sch'], reference['regulator.kicad_sch'])


def owned_capture_inputs():
    """Regulator intent whose OwnedGen library is captured from an over-limit owned library."""
    owned = (PROJECT / 'lib/Owned_Gen.kicad_sym').read_bytes()
    filler = ''.join(f'\t(symbol "FILL_{k:04d}" (fill{" x" * 2100}))\n' for k in range(1000)).encode()
    big = owned[:owned.rindex(b')')] + filler + b')\n'
    try:
        parse_library(big)
    except LibraryError:
        pass
    else:
        raise AssertionError('owned filler library must exceed the shared node limit')
    output, receipt = capture_symbols(big, ['CAP', 'CONN3', 'REG3'], library='OwnedGen')
    intent = json.loads((ROOT / 'regulator.intent.json').read_text())
    for lib in intent['libraries']:
        if lib['name'] == 'OwnedGen':
            lib.update(path='lib/OwnedGen.kicad_sym', sha256=receipt['output']['sha256'])
    layout = json.loads((ROOT / 'regulator.layout.json').read_text())
    return intent, layout, {'lib/OwnedGen.kicad_sym': output,
                            'lib/Owned_Power.kicad_sym': (PROJECT / 'lib/Owned_Power.kicad_sym').read_bytes()}


def stock_library(test):
    if not STOCK.is_file():
        if os.environ.get('AGENT_EE_REQUIRE_TOOLCHAIN') == '1':
            raise AssertionError('AGENT_EE_REQUIRE_TOOLCHAIN=1: stock library missing: ' + str(STOCK))
        test.skipTest('stock symbol library missing: ' + str(STOCK))
    return STOCK.read_bytes()


def stm32_intent(output):
    return dict(schema='agent-ee.connectivity-intent/1',
                libraries=[dict(name='MCU_ST_STM32H7', path='symbols/MCU_ST_STM32H7.kicad_sym',
                                sha256=hashlib.sha256(output).hexdigest())],
                components=[dict(id='mcu', reference='U26', lib_id='MCU_ST_STM32H7:STM32H723ZGTx',
                                 value='STM32H723ZGTx', footprint='Package_QFP:LQFP-144_20x20mm_P0.5mm',
                                 units=[1])],
                nets=[dict(id='vdd', name='+3V3', terminals=[dict(component='mcu', pin=dict(name='VDD'))]),
                      dict(id='gnd', name='GND', terminals=[dict(component='mcu', pin=dict(name='VSS'))]),
                      dict(id='vcap', name='VCAP', terminals=[dict(component='mcu', pin=dict(name='VCAP'))])],
                no_connects=[dict(component='mcu', pin=dict(number=str(n))) for n in range(1, 145)
                             if n not in (VDD | VSS | {71, 106})])


# Read from the raw installed text (pin blocks), independent of capture and resolver.
VDD = {17, 30, 39, 52, 62, 72, 84, 108, 121, 131, 144}
VSS = {16, 38, 51, 61, 83, 94, 107, 120, 130}
KEY_PINS = {'25': ('NRST', 'input'), '138': ('BOOT0', 'input'), '143': ('PDR_ON', 'input'),
            '6': ('VBAT', 'power_in'), '33': ('VDDA', 'power_in'), '31': ('VSSA', 'power_in'),
            '32': ('VREF+', 'input'), '95': ('VDD33USB', 'power_in'), '71': ('VCAP', 'power_out'),
            '106': ('VCAP', 'power_out'), '16': ('VSS', 'power_in'), '38': ('VSS', 'passive'),
            '105': ('PA13(JTMS', 'bidirectional'), '1': ('PE2', 'bidirectional')}
TYPE_COUNTS = {'bidirectional': 114, 'input': 4, 'passive': 8, 'power_in': 16, 'power_out': 2}


class StockStm32Tests(unittest.TestCase):
    """Private actual-fixture qualification against the installed KiCad library."""

    @classmethod
    def setUpClass(cls):
        cls.output = cls.receipt = None

    def setUp(self):
        self.data = stock_library(self)
        if self.output is None:
            type(self).output, type(self).receipt = capture_symbols(
                self.data, ['STM32H723ZGTx'], library='MCU_ST_STM32H7', source_file=STOCK.name)

    def test_installed_library_exceeds_the_shared_limit_and_fails_closed(self):
        with self.assertRaisesRegex(LibraryError, r'node limit exceeded \(2000000 nodes\).*agent_ee\.symbol_capture'):
            parse_library(self.data)

    def test_exact_symbol_bytes_and_repeatable_capture(self):
        start = self.data.index(b'\n\t(symbol "STM32H723ZGTx"\n') + 2
        end = self.data.index(b'\n\t(symbol "', start)
        raw = self.data[start:end]
        self.assertEqual(self.receipt['source']['sha256'], hashlib.sha256(self.data).hexdigest())
        self.assertEqual(self.receipt['symbols'], [dict(name='STM32H723ZGTx', role='selected', extends=None,
                                                        sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw),
                                                        nodes=self.receipt['symbols'][0]['nodes'])])
        header = self.data[:self.data.index(b'\n\t(symbol "')]
        self.assertEqual(self.output, header + b'\n\t' + raw + b'\n)\n')
        self.assertEqual(capture_symbols(self.data, ['STM32H723ZGTx'], library='MCU_ST_STM32H7',
                                         source_file=STOCK.name), (self.output, self.receipt))
        self.assertEqual(verify_capture(self.receipt, self.output, self.data), self.receipt)

    def test_intent_resolves_all_physical_pins_against_capture(self):
        intent = stm32_intent(self.output)
        record = resolve_intent(intent, {'symbols/MCU_ST_STM32H7.kicad_sym': self.output})
        self.assertEqual((record['execution'], record['outcome'], record['coverage']), ('ok', 'pass', 'complete'),
                         record['findings'])
        pins = {p['number']: p for p in record['components'][0]['pins']}
        self.assertEqual(sorted(pins, key=int), [str(n) for n in range(1, 145)])
        types = {}
        for p in pins.values():
            types[p['electrical_type']] = types.get(p['electrical_type'], 0) + 1
        self.assertEqual(types, TYPE_COUNTS)
        self.assertEqual({n: (pins[n]['name'], pins[n]['electrical_type']) for n in KEY_PINS}, KEY_PINS)
        nets = {n['name']: sorted(int(t['pin']) for t in n['terminals']) for n in record['nets']}
        self.assertEqual(nets, {'+3V3': sorted(VDD), 'GND': sorted(VSS), 'VCAP': [71, 106]})
        self.assertTrue(all(p['source']['sha256'] == hashlib.sha256(self.output).hexdigest() for p in pins.values()))
        binding = bind_resolved(self.receipt, record)
        self.assertEqual(binding['source_sha256'], hashlib.sha256(self.data).hexdigest())
        self.assertEqual(binding['symbols'], [dict(name='STM32H723ZGTx', sha256=self.receipt['symbols'][0]['sha256'])])
        self.assertEqual(binding['resolved_sha256'], record['canonical_sha256'])


class NativeCaptureTests(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)
        self.tmp = Path(tempfile.mkdtemp(prefix='agent-ee-capture-native-'))
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_native_resave_of_captured_stm32_is_byte_identical(self):
        data = stock_library(self)
        output, _ = capture_symbols(data, ['STM32H723ZGTx'], library='MCU_ST_STM32H7')
        project = self.tmp / 'project'
        project.mkdir()
        (project / 'MCU_ST_STM32H7.kicad_sym').write_bytes(output)
        work = self.tmp / 'work'
        (work / 'copy').mkdir(parents=True)
        shutil.copy(project / 'MCU_ST_STM32H7.kicad_sym', work / 'copy')
        run_native(project, work, 'kicad-cli', 'sym', 'upgrade', '--force', '/work/copy/MCU_ST_STM32H7.kicad_sym')
        self.assertIn('Saving symbol library in updated format', (work / 'console.log').read_text())
        self.assertEqual((work / 'copy' / 'MCU_ST_STM32H7.kicad_sym').read_bytes(), output)

    def erc(self, project, label):
        work = self.tmp / label
        run_native(project, work, 'kicad-cli', 'sch', 'erc', '--format', 'json', '--severity-all',
                   '--units', 'mm', '-o', '/work/erc.json', '/candidate/regulator.kicad_sch')
        erc = json.loads((work / 'erc.json').read_text())
        self.assertEqual(erc['kicad_version'], '10.0.7')
        return [v for sheet in erc['sheets'] for v in sheet['violations']], work

    def test_emitted_sheet_embedding_capture_passes_native_erc_and_netlist(self):
        intent, layout, lib = owned_capture_inputs()
        result, files = emit_schematic(intent, lib, layout,
                                       symbol_table={'OwnedGen': '${KIPRJMOD}/lib/OwnedGen.kicad_sym',
                                                     'OwnedPower': '${KIPRJMOD}/lib/Owned_Power.kicad_sym'},
                                       footprint_table={'OwnedFP': '${KIPRJMOD}/lib/OwnedFP.pretty'})
        self.assertEqual(result['execution'], 'ok', result['findings'])
        project = self.tmp / 'project'
        shutil.copytree(PROJECT / 'lib/OwnedFP.pretty', project / 'lib/OwnedFP.pretty')
        for path, data in {**lib, **files}.items():
            (project / path).write_bytes(data)
        violations, _ = self.erc(project, 'erc')
        self.assertEqual(violations, [])
        work = self.tmp / 'netlist'
        run_native(project, work, 'kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml',
                   '-o', '/work/design.xml', '/candidate/regulator.kicad_sch')
        components, nets, singles = native_netlist(work / 'design.xml')
        expected = json.loads((ROOT / 'regulator.expected.json').read_text())
        self.assertEqual(nets, expected['nets'])
        self.assertEqual([[n, p] for (n, p), _ in singles.values()], expected['no_connects'])
        self.assertEqual({ref: c['lib'] for ref, c in components.items()},
                         {'J1': ('OwnedGen', 'CONN3'), 'U1': ('OwnedGen', 'REG3'), 'C1': ('OwnedGen', 'CAP')})
        # Control: ERC does compare embedded symbols with the captured project library.
        # Moving CAP pin 1 in the library must surface as a native mismatch.
        moved = lib['lib/OwnedGen.kicad_sym'].replace(b'(at 0 2.54 270)', b'(at 0 3.81 270)', 1)
        self.assertNotEqual(moved, lib['lib/OwnedGen.kicad_sym'])
        (project / 'lib/OwnedGen.kicad_sym').write_bytes(moved)
        violations, _ = self.erc(project, 'control')
        self.assertEqual([(v['type'], v['description']) for v in violations],
                         [('lib_symbol_mismatch', "Symbol 'CAP' doesn't match copy in library 'OwnedGen'")])


if __name__ == '__main__':
    unittest.main()
