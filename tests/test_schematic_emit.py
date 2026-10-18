"""Wire-free schematic emission: native ERC/netlist agreement, determinism and refusals."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from agent_ee.connectivity_ir import resolve_intent
from agent_ee.schematic_emit import (PARTS_SCHEMA, RECEIPT_SCHEMA, EmitError, emit, format_sexpr, main,
                                     parse_quoted)
sys.path.insert(0, str(Path(__file__).resolve().parent))  # tests.<module> invocation
from toolchain import require_toolchain  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'fixtures/rc/control'
TEMPLATE = (FIXTURE / 'rc_filter.kicad_pro').read_bytes()
STOCK = Path('/usr/share/kicad/symbols')

# Owned minimal library (not derived from KiCad's library files).
OWNED = b'''(kicad_symbol_lib (version 20251024) (generator "owned")
(symbol "R" (property "Reference" "R" (at 2.032 0 90)) (property "Value" "R" (at 0 0 90))
 (symbol "R_1_1"
  (pin passive line (at 0 3.81 270) (length 1.27) (name "~") (number "1"))
  (pin passive line (at 0 -3.81 90) (length 1.27) (name "~") (number "2"))))
(symbol "GND" (power) (property "Reference" "#PWR" (at 0 -6.35 0) (hide yes))
 (property "Value" "GND" (at 0 -3.81 0))
 (symbol "GND_1_1" (pin power_in line (at 0 0 270) (length 0) (name "~") (number "1"))))
(symbol "PWR_FLAG" (power) (property "Reference" "#FLG" (at 0 1.905 0) (hide yes))
 (property "Value" "PWR_FLAG" (at 0 3.81 0))
 (symbol "PWR_FLAG_0_0" (pin power_out line (at 0 0 90) (length 0) (name "~") (number "1")))))
'''


def term(component, pin):
    return dict(component=component, pin={'number': pin})


def owned_intent(**changes):
    intent = dict(schema='agent-ee.connectivity-intent/1',
                  libraries=[dict(name='Owned', path='Owned.kicad_sym', sha256=hashlib.sha256(OWNED).hexdigest())],
                  components=[dict(id=r, reference=r, lib_id='Owned:R', value='1k', footprint='', units=[1])
                              for r in ('R1', 'R2')],
                  nets=[dict(id='a', name='/A', terminals=[term('R1', '1'), term('R2', '1')]),
                        dict(id='gnd', name='GND', terminals=[term('R1', '2'), term('R2', '2')])],
                  no_connects=[])
    intent.update(changes)
    return intent


def manifest(references, footprint='Resistor_SMD:R_0805_2012Metric'):
    return json.dumps(dict(schema=PARTS_SCHEMA, parts={r: dict(Footprint=footprint) for r in references})).encode()


def resolved_bytes(intent, captures):
    return json.dumps(resolve_intent(intent, captures)).encode()


class OwnedEmissionTests(unittest.TestCase):
    def test_libraries_are_parsed_once_and_never_retained_between_emissions(self):
        from unittest import mock
        from agent_ee import symbol_library
        from agent_ee.symbol_capture import capture_symbols
        extra, _ = capture_symbols(OWNED, ['R'], library='Extra')
        intent = owned_intent()
        intent['libraries'].append(dict(name='Extra', path='Extra.kicad_sym', sha256=hashlib.sha256(extra).hexdigest()))
        captures = {'Owned.kicad_sym': OWNED, 'Extra.kicad_sym': extra}
        record = resolved_bytes(intent, captures)
        for _ in range(2):
            with mock.patch.object(symbol_library, 'parse_sexpr', wraps=symbol_library.parse_sexpr) as parse:
                emit(record, captures, manifest(['R1', 'R2']), TEMPLATE, 'owned')
            self.assertCountEqual([call.args[0] for call in parse.call_args_list], list(captures.values()))

    def emit_owned(self, intent=None, parts=None, captures=None):
        record = resolved_bytes(intent or owned_intent(), {'Owned.kicad_sym': OWNED})
        return emit(record, captures or {'Owned.kicad_sym': OWNED}, parts or manifest(['R1', 'R2']),
                    TEMPLATE, 'owned')

    def test_quote_preserving_round_trip(self):
        tree = parse_quoted(OWNED)
        text = '\n'.join(format_sexpr(tree)).encode()
        self.assertEqual(parse_quoted(text), tree)
        self.assertIn(b'(pin passive line', text)
        self.assertIn(b'(hide yes)', text)

    def test_fixture_schematic_reformats_byte_identically(self):
        data = (FIXTURE / 'rc_filter.kicad_sch').read_bytes()
        self.assertEqual(('\n'.join(format_sexpr(parse_quoted(data))) + '\n').encode(), data)

    def test_wire_free_deterministic_emission(self):
        first, second = self.emit_owned(), self.emit_owned()
        self.assertEqual(first, second)
        sch = first['owned.kicad_sch'].decode()
        self.assertNotIn('(wire', sch)
        self.assertEqual(sch.count('(label "A"'), 2)
        self.assertEqual(sch.count('(lib_id "Owned:GND")'), 3)  # Two pins plus the flag pair.
        self.assertEqual(sch.count('(lib_id "Owned:PWR_FLAG")'), 1)
        self.assertIn('(property "Footprint" "Resistor_SMD:R_0805_2012Metric"', sch)
        receipt = json.loads(first['emit-receipt.json'])
        self.assertEqual((receipt['schema'], receipt['qualification']),
                         (RECEIPT_SCHEMA, 'experimental-unqualified'))
        self.assertEqual({o['path'] for o in receipt['outputs']},
                         {'owned.kicad_sch', 'owned.kicad_pro', 'sym-lib-table', 'fp-lib-table'})
        for item in receipt['outputs']:
            self.assertEqual(item['sha256'], hashlib.sha256(first[item['path']]).hexdigest())
        self.assertEqual(receipt['power_flags'][0]['net'], 'GND')
        project = json.loads(first['owned.kicad_pro'])
        self.assertEqual(project['schematic']['top_level_sheets'][0]['filename'], 'owned.kicad_sch')

    def test_unknown_pin_refused_with_real_pins(self):
        intent = owned_intent()
        intent['nets'][0]['terminals'][1] = term('R2', '3')
        intent['no_connects'] = [term('R2', '1')]
        with self.assertRaisesRegex(EmitError, r'R2: unknown pin 3; available pins: 1, 2'):
            self.emit_owned(intent)

    def test_record_pins_disagreeing_with_library_refused(self):
        record = resolve_intent(owned_intent(), {'Owned.kicad_sym': OWNED})
        record['components'][0]['pins'][0]['x_nm'] = 2540000
        from agent_ee.connectivity_ir import _json_bytes
        record.pop('canonical_sha256')
        record['canonical_sha256'] = hashlib.sha256(_json_bytes(record)).hexdigest()
        with self.assertRaisesRegex(EmitError, 'real pins: 1, 2'):
            emit(json.dumps(record).encode(), {'Owned.kicad_sym': OWNED}, manifest(['R1', 'R2']),
                 TEMPLATE, 'owned')

    def test_datasheet_travels_from_manifest_and_null_keeps_library_default(self):
        url = 'https://example.invalid/owned-r.pdf'
        fp = 'Resistor_SMD:R_0805_2012Metric'
        parts = json.dumps(dict(schema=PARTS_SCHEMA, parts={
            'R1': dict(Footprint=fp, Datasheet=url),
            'R2': dict(Footprint=fp, Datasheet=None, datasheet_obligation='missing:catalog_has_no_datasheet_column')}))
        outputs = self.emit_owned(parts=parts.encode())
        tree = parse_quoted(outputs['owned.kicad_sch'])
        fields = {}
        for node in tree[1:]:
            if isinstance(node, list) and node[0] == 'symbol':
                props = {p[1]: p[2] for p in node[1:] if isinstance(p, list) and p[0] == 'property'}
                fields[props['Reference']] = props['Datasheet']
        self.assertEqual(fields['R1'], url)
        self.assertEqual(fields['R2'], '')  # Owned library default; nothing invented.
        receipt = json.loads(outputs['emit-receipt.json'])
        self.assertEqual(receipt['ignored_manifest_keys'], ['R2.datasheet_obligation'])
        for bad in ('', 'a\nb', 7):
            broken = json.dumps(dict(schema=PARTS_SCHEMA, parts={'R1': dict(Footprint=fp, Datasheet=bad),
                                                                 'R2': dict(Footprint=fp)})).encode()
            with self.subTest(bad=bad), self.assertRaisesRegex(EmitError, 'R1.Datasheet must be'):
                self.emit_owned(parts=broken)

    def test_component_missing_from_manifest_refused(self):
        with self.assertRaisesRegex(EmitError, 'missing from the parts manifest: R2'):
            self.emit_owned(parts=manifest(['R1']))

    def test_library_sha_mismatch_refused(self):
        tampered = OWNED.replace(b'"owned"', b'"other"')
        with self.assertRaisesRegex(EmitError, 'sha256 mismatch'):
            self.emit_owned(captures={'Owned.kicad_sym': tampered})

    def test_inexpressible_nets_and_parts_refused(self):
        bare = owned_intent()
        bare['nets'][0]['name'] = 'A'  # Neither /-prefixed nor a captured power symbol.
        bad_footprint = manifest(['R1', 'R2'], footprint='R_0805')
        unknown = json.dumps(dict(schema=PARTS_SCHEMA, parts={r: dict(Footprint='L:F') for r in
                                                              ('R1', 'R2', 'R9')})).encode()
        wrong_schema = json.dumps(dict(schema='agent-ee.parts-manifest/1', parts={})).encode()
        for intent, parts, message in ((bare, None, 'no captured power symbol'),
                                       (None, bad_footprint, 'LIBRARY:NAME'),
                                       (None, unknown, 'absent from the record: R9'),
                                       (None, wrong_schema, 'rung1-parts-manifest')):
            with self.subTest(message=message), self.assertRaisesRegex(EmitError, message):
                self.emit_owned(intent, parts)


def stock_prerequisite():
    require_toolchain(tools=('kicad-cli',))
    missing = [name for name in ('Device', 'power', 'Connector_Generic')
               if not (STOCK / f'{name}.kicad_sym').is_file()]
    if missing:
        reason = 'toolchain prerequisite: KiCad stock symbol libraries missing: ' + ', '.join(missing)
        if os.environ.get('AGENT_EE_REQUIRE_TOOLCHAIN') == '1':
            raise AssertionError('AGENT_EE_REQUIRE_TOOLCHAIN=1: ' + reason)
        raise unittest.SkipTest(reason)


class NativeRcTests(unittest.TestCase):
    """The shared rung-1 RC intent, emitted from captured stock libraries and read by KiCad."""

    NETS = {'/IN': {('J1', '1'), ('R1', '1')},
            '/OUT': {('R1', '2'), ('C1', '1'), ('J1', '2')},
            'GND': {('C1', '2'), ('J1', '3')}}

    @classmethod
    def setUpClass(cls):
        stock_prerequisite()
        cls.tmp = Path(tempfile.mkdtemp(prefix='agent-ee-schematic-emit-'))
        libs = cls.tmp / 'libs/symbols'
        libs.mkdir(parents=True)
        libraries = []
        for name in ('Device', 'power', 'Connector_Generic'):
            data = (STOCK / f'{name}.kicad_sym').read_bytes()
            (libs / f'{name}.kicad_sym').write_bytes(data)
            libraries.append(dict(name=name, path=f'symbols/{name}.kicad_sym',
                                  sha256=hashlib.sha256(data).hexdigest()))
        fixture_footprints = {'R1': 'Resistor_SMD:R_0805_2012Metric', 'C1': 'Capacitor_SMD:C_0805_2012Metric',
                              'J1': 'Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical'}
        components = [dict(id='R1', reference='R1', lib_id='Device:R', value='10k'),
                      dict(id='C1', reference='C1', lib_id='Device:C', value='100n'),
                      dict(id='J1', reference='J1', lib_id='Connector_Generic:Conn_01x03', value='Conn_01x03')]
        for comp in components:
            comp.update(footprint=fixture_footprints[comp['id']], units=[1])
        intent = dict(schema='agent-ee.connectivity-intent/1', libraries=libraries, components=components,
                      nets=[dict(id=name.strip('/').lower(), name=name,
                                 terminals=[term(c, p) for c, p in sorted(members)])
                            for name, members in cls.NETS.items()],
                      no_connects=[])
        captures = {lib['path']: (libs / f"{lib['name']}.kicad_sym").read_bytes() for lib in libraries}
        record = resolve_intent(intent, captures)
        assert record['outcome'] == 'pass', record['findings']
        (cls.tmp / 'resolved.json').write_text(json.dumps(record))
        parts = {ref: dict(Footprint=fp) for ref, fp in fixture_footprints.items()}
        parts['J1']['JLCPCB_Assembly'] = 'not_assembled'
        (cls.tmp / 'parts.json').write_text(json.dumps(dict(schema=PARTS_SCHEMA, parts=parts)))
        cls.outputs = [cls.run_cli(f'out{index}') for index in (1, 2)]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def run_cli(cls, name):
        out = cls.tmp / name
        code = main([str(cls.tmp / 'resolved.json'), '--libraries', str(cls.tmp / 'libs'),
                     '--parts', str(cls.tmp / 'parts.json'), '--project-template',
                     str(FIXTURE / 'rc_filter.kicad_pro'), '--name', 'rc_gen', '--out', str(out)])
        assert code == 0, code
        return out

    def kicad(self, *argv):
        work = self.tmp / 'native'
        if not work.exists():
            shutil.copytree(self.outputs[0], work)
        env = dict(os.environ, KICAD_CONFIG_HOME=str(self.tmp / 'kicad-config'), HOME=str(self.tmp))
        result = subprocess.run(['kicad-cli', *argv], cwd=work, env=env, capture_output=True,
                                text=True, timeout=180)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return work

    def test_native_erc_has_zero_violations(self):
        work = self.kicad('sch', 'erc', '--format', 'json', '--severity-all', '-o', 'erc.json',
                          'rc_gen.kicad_sch')
        report = json.loads((work / 'erc.json').read_text())
        violations = [v for sheet in report['sheets'] for v in sheet['violations']]
        self.assertEqual(violations, [])

    def test_native_netlist_reproduces_intent_nets(self):
        work = self.kicad('sch', 'export', 'netlist', '--format', 'kicadxml', '-o', 'net.xml',
                          'rc_gen.kicad_sch')
        root = ET.parse(work / 'net.xml').getroot()
        nets = {net.get('name'): {(n.get('ref'), n.get('pin')) for n in net.iter('node')}
                for net in root.iter('net')}
        self.assertEqual(nets, self.NETS)
        fields = {comp.get('ref'): comp.findtext('footprint') for comp in root.iter('comp')}
        self.assertEqual(fields['J1'], 'Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical')

    def test_second_emission_is_byte_identical(self):
        first, second = self.outputs
        names = sorted(p.name for p in first.iterdir())
        self.assertEqual(names, sorted(p.name for p in second.iterdir()))
        for name in names:
            self.assertEqual((first / name).read_bytes(), (second / name).read_bytes(), name)
        for table in ('sym-lib-table', 'fp-lib-table'):
            self.assertEqual((first / table).read_bytes(), (FIXTURE / table).read_bytes())


if __name__ == '__main__':
    unittest.main()
