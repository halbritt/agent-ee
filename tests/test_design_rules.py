"""Design-rule cards (AGENTEE-62): one captured source for writer guidance and graded checks.

Pure tests use the strict-reader graph shape. Native tests export original
fixtures through the sandboxed kicad-cli netlist check and verify fictional
datasheet statements printed into an original PDF with pdftotext.
"""
import contextlib
import copy
import importlib.util
import io
import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))  # Also run as tests.test_design_rules.

from agent_ee.baseline import freeze_evidence, validate_witnesses, verify_evidence
from agent_ee.candidate import capture
from agent_ee.capabilities import compile_coverage, describe_check
from agent_ee.design_rules import (address, evaluate, resistance, validate_bindings, validate_pack,
                                   witnesses, writer_context)
from agent_ee.checks import run_checks
from agent_ee.storage import digest, read_json, write_json
from test_datasheet_facts import text_pdf
from toolchain import require_toolchain

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'fixtures/design-rules'
spec = importlib.util.spec_from_file_location('design_rules_fixture', FIXTURE / 'generate.py')
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)
GRADED = ['I2C_PULLUP_WINDOW', 'I2C_ADDRESS_UNIQUE', 'CRYSTAL_LOAD_CAPS', 'RESET_EN_RC', 'POWER_DECOUPLING']


def pack():
    return json.loads((FIXTURE / 'pack.json').read_text())


def graph(variant='control', parts=None):
    """Strict-reader shape from the fixture's own part list (no KiCad involved)."""
    out = {'components': {}, 'memberships': {}, 'nets': {}}
    codes = {}
    for ref, symbol, value, nets in (parts if parts is not None else generator.parts(variant)):
        pins = tuple(str(i + 1) for i in range(len(nets)))
        out['components'][ref] = {'lib_id': symbol, 'value': value, 'populated': True, 'pins': pins,
                                  'locator': f'components/comp[@ref={ref!r}]'}
        for pin, name in zip(pins, nets):
            code = codes.setdefault(name, str(len(codes) + 1))
            net = out['nets'].setdefault(code, {'name': '/' + name, 'terminals': (),
                                                'locator': f'nets/net[@code={code!r}]'})
            net['terminals'] += ((ref, pin),)
            out['memberships'][ref, pin] = code
    return out


def facts(*ids):
    lines = generator.FACT_LINES
    return {fid: {'id': fid, 'part_number': lines[fid][0], 'kind': lines[fid][1], 'value': lines[fid][2]}
            for fid in (ids or lines)}


def cards(report):
    return {c['id']: c for c in report['cards']}


def run(variant='control', *, selected=GRADED, verified=None, bindings=None, parts=None):
    return evaluate(validate_pack(pack()), validate_bindings(bindings or generator.bindings()), selected,
                    graph(variant, parts), facts() if verified is None else verified)


class ValueTests(unittest.TestCase):
    def test_written_resistance_and_address_forms(self):
        for value, ohms in [('4.7k', 4700), ('4k7', 4700), ('470', 470), ('470R', 470), ('4R7', 4.7),
                            ('10 kΩ', 10000), ('1M', 1000000), ('2.2K', 2200), ('10kohm', 10000)]:
            self.assertEqual(resistance(value), type(resistance('1'))(str(ohms)), value)
        for value in ('2mR', '4.7u', 'RC0201FR-0710KL', '', '1e3', '4k7k', None):
            self.assertIsNone(resistance(value), value)
        self.assertEqual((address('0x48'), address('0X7f')), (0x48, 0x7f))
        for value in ('0x80', '72', '0x', '48h', None):
            self.assertIsNone(address(value), value)


class PackTests(unittest.TestCase):
    def test_shipped_pack_classifies_graded_and_advisory_cards(self):
        report = run()
        by_id = cards(report)
        self.assertEqual((report['execution'], report['outcome']), ('ok', 'pass'), report)
        self.assertEqual(by_id['I2C_BUS_CAPACITANCE']['status'], 'advisory')
        self.assertEqual(by_id['USB_SERIES_RESISTORS']['status'], 'not_enforced')
        self.assertEqual({by_id[c]['status'] for c in GRADED}, {'pass'})
        self.assertEqual(by_id['I2C_PULLUP_WINDOW']['description'], pack()['cards'][0]['description'])

    def test_invalid_packs_and_bindings_are_refused(self):
        def broken(change):
            data = pack()
            change(data)
            return data
        for label, data in [
                ('priority', broken(lambda p: p['cards'][0].update(priority='urgent'))),
                ('advisory parameters', broken(lambda p: p['cards'][6].update(parameters={'min_ohm': 1}))),
                ('reversed window', broken(lambda p: p['cards'][0]['parameters'].update(min_ohm=20000))),
                ('duplicate id', broken(lambda p: p['cards'][1].update(id='I2C_PULLUP_WINDOW'))),
                ('lower-case id', broken(lambda p: p['cards'][1].update(id='i2c'))),
                ('two enforcements', broken(lambda p: p['cards'][0].update(
                    enforcement={'adapter': 'reset-rc/1', 'check_kind': 'erc'}))),
                ('no provenance', broken(lambda p: p.pop('provenance')))]:
            with self.subTest(label), self.assertRaises(ValueError):
                validate_pack(data)
        for label, change in [('empty group', lambda b: b['roles'].update(resets=[])),
                              ('unknown group', lambda b: b['roles'].update(spi_buses=[{}])),
                              ('unknown class', lambda b: b['classes'].update({'Owned:X': {'class': 'crystal', 'pins': ['1', '2']}})),
                              ('no devices', lambda b: b['roles']['i2c_buses'][0].update(devices=[])),
                              ('loose requires', lambda b: b['roles']['resets'][0]['applicability'].update(requires='yes'))]:
            data = generator.bindings()
            change(data)
            with self.subTest(label), self.assertRaises(ValueError):
                validate_bindings(data)


class I2CTests(unittest.TestCase):
    def test_no_pullups_is_defect_citing_each_line_net(self):
        report = run('no-pullups', selected=['I2C_PULLUP_WINDOW'])
        card = cards(report)['I2C_PULLUP_WINDOW']
        self.assertEqual((report['outcome'], card['status']), ('defect', 'defect'))
        self.assertEqual([(i['net_name'], i['reason']) for i in card['items']],
                         [('/SDA', 'pullup_absent'), ('/SCL', 'pullup_absent')])
        self.assertTrue(all(i['locator'].startswith('nets/net') for i in card['items']))
        self.assertIn('I2C_PULLUP_WINDOW: pullup_absent at /SDA', '; '.join(witnesses(report)))

    def test_parallel_equivalent_window(self):
        card = cards(run('control', selected=['I2C_PULLUP_WINDOW']))['I2C_PULLUP_WINDOW']
        self.assertEqual(card['status'], 'pass')
        self.assertEqual([i['equivalent_ohm'] for i in card['items']], [2350.0, 2350.0])
        self.assertEqual([[p['reference'] for p in i['pullups']] for i in card['items']], [['R1', 'R3'], ['R2', 'R4']])
        strong = cards(run('strong', selected=['I2C_PULLUP_WINDOW']))['I2C_PULLUP_WINDOW']
        self.assertEqual(strong['status'], 'defect')
        self.assertEqual({(i['equivalent_ohm'], i['reason']) for i in strong['items']},
                         {(470.0, 'equivalent_outside_window')})

    def test_unreadable_unfitted_or_undeclared_pullups_never_pass(self):
        parts = [p if p[0] != 'R3' else (p[0], p[1], 'RC0201FR-0710KL', p[3]) for p in generator.parts()]
        card = cards(run(parts=parts, selected=['I2C_PULLUP_WINDOW']))['I2C_PULLUP_WINDOW']
        self.assertEqual((card['status'], card['items'][0]['reason']), ('unknown', 'value_unparsed'))
        report = run('undeclared-pullup', selected=['I2C_PULLUP_WINDOW'])
        card = cards(report)['I2C_PULLUP_WINDOW']
        self.assertEqual((report['execution'], report['outcome']), ('unsupported', 'unknown'))
        self.assertEqual({i['reason'] for i in card['items']}, {'unclassified_two_pin_part'})
        unfitted = graph('strong')
        for ref in ('R1', 'R2'):
            unfitted['components'][ref]['populated'] = False
        report = evaluate(validate_pack(pack()), validate_bindings(generator.bindings()), ['I2C_PULLUP_WINDOW'],
                          unfitted, facts())
        items = cards(report)['I2C_PULLUP_WINDOW']['items']
        self.assertEqual({(i['reason'], i['unpopulated'][0]['reference']) for i in items},
                         {('pullup_absent', 'R1'), ('pullup_absent', 'R2')})

    def test_duplicate_resolved_addresses_are_defects(self):
        card = cards(run('same-address', selected=['I2C_ADDRESS_UNIQUE']))['I2C_ADDRESS_UNIQUE']
        self.assertEqual(card['status'], 'defect')
        self.assertEqual([(i['reference'], i['address'], i['duplicates']) for i in card['items']],
                         [('U2', '0x48', ['U3']), ('U3', '0x48', ['U2'])])
        self.assertEqual({i['strap']['net_name'] for i in card['items']}, {'/GND'})
        control = cards(run('control', selected=['I2C_ADDRESS_UNIQUE']))['I2C_ADDRESS_UNIQUE']
        self.assertEqual([(i['address'], i['status']) for i in control['items']], [('0x48', 'satisfied'), ('0x49', 'satisfied')])

    def test_device_without_address_fact_is_unknown_never_pass(self):
        bindings = generator.bindings()
        del bindings['roles']['i2c_buses'][0]['devices'][1]['address']
        report = run('control', selected=['I2C_ADDRESS_UNIQUE'], bindings=bindings)
        card = cards(report)['I2C_ADDRESS_UNIQUE']
        self.assertEqual((report['outcome'], card['status']), ('unknown', 'unknown'))
        self.assertEqual(card['items'][1]['reason'], 'address_fact_missing')
        # The strap's fact exists but was not verified by the facts check.
        card = cards(run('control', selected=['I2C_ADDRESS_UNIQUE'], verified=facts('ts1-addr-gnd')))['I2C_ADDRESS_UNIQUE']
        self.assertEqual([i['status'] for i in card['items']], ['satisfied', 'unknown'])
        self.assertEqual(card['status'], 'unknown')
        # A known duplicate stays a defect even beside an unresolved device.
        bindings = generator.bindings()
        bindings['roles']['i2c_buses'][0]['devices'].append({'reference': 'U1', 'part_number': 'OWN-MCU1'})
        card = cards(run('same-address', selected=['I2C_ADDRESS_UNIQUE'], bindings=bindings))['I2C_ADDRESS_UNIQUE']
        self.assertEqual((card['status'], card['items'][2]['reason']), ('defect', 'address_fact_missing'))


class CrystalResetTests(unittest.TestCase):
    def test_crystal_load_cap_presence_per_pin(self):
        card = cards(run(selected=['CRYSTAL_LOAD_CAPS']))['CRYSTAL_LOAD_CAPS']
        self.assertEqual([(i['pin'], i['net_name'], [c['reference'] for c in i['load_caps']]) for i in card['items']],
                         [('1', '/XIN', ['C1']), ('3', '/XOUT', ['C2'])])
        card = cards(run('no-load-cap', selected=['CRYSTAL_LOAD_CAPS']))['CRYSTAL_LOAD_CAPS']
        self.assertEqual([(i['pin'], i['status'], i['reason']) for i in card['items']],
                         [('1', 'satisfied', 'load_cap_present'), ('3', 'violation', 'load_cap_absent')])

    def test_reset_rc_defect_only_when_a_fact_requires_it(self):
        card = cards(run('bare-en', selected=['RESET_EN_RC']))['RESET_EN_RC']
        self.assertEqual((card['status'], card['items'][0]['missing']), ('defect', ['pullup', 'capacitor']))
        self.assertEqual(card['items'][0]['applicability']['fact_id'], 'mcu1-en-rc')
        report = run('bare-en', selected=['RESET_EN_RC'], verified=facts('ts1-addr-gnd'))
        self.assertEqual(cards(report)['RESET_EN_RC']['items'][0]['reason'], 'applicability_fact_unverified')
        self.assertEqual(report['outcome'], 'unknown')
        bindings = generator.bindings()
        del bindings['roles']['resets'][0]['applicability']
        card = cards(run('bare-en', selected=['RESET_EN_RC'], bindings=bindings))['RESET_EN_RC']
        self.assertEqual((card['status'], card['items'][0]['reason']), ('unknown', 'applicability_fact_missing'))
        bindings['roles']['resets'][0]['part_number'] = 'OWN-MCU2'
        bindings['roles']['resets'][0]['applicability'] = {'fact': 'mcu1-en-rc', 'requires': True}
        card = cards(run('bare-en', selected=['RESET_EN_RC'], bindings=bindings))['RESET_EN_RC']
        self.assertEqual(card['items'][0]['reason'], 'applicability_fact_inapplicable')
        bindings['roles']['resets'][0].update(part_number='OWN-MCU1', applicability={'fact': 'mcu1-en-rc', 'requires': False})
        report = run('bare-en', selected=['RESET_EN_RC'], bindings=bindings)
        self.assertEqual((report['outcome'], cards(report)['RESET_EN_RC']['status']), ('pass', 'not_applicable'))

    def test_missing_role_binding_is_unknown(self):
        bindings = generator.bindings()
        del bindings['roles']['crystals']
        report = run(selected=['CRYSTAL_LOAD_CAPS', 'I2C_PULLUP_WINDOW'], bindings=bindings)
        self.assertEqual(cards(report)['CRYSTAL_LOAD_CAPS']['reason'], 'role_binding_missing')
        self.assertEqual((report['execution'], report['outcome']), ('unsupported', 'unknown'))
        bindings = generator.bindings()
        bindings['roles']['crystals'][0]['pins'] = ['1', '9']
        card = cards(run(selected=['CRYSTAL_LOAD_CAPS'], bindings=bindings))['CRYSTAL_LOAD_CAPS']
        self.assertEqual(card['items'][1]['reason'], 'terminal_unresolved')

    def test_usb_series_resistor_is_applicability_gated(self):
        parts = generator.parts() + [('J2', 'Owned:HEADER3', 'USB', ['DP_CONN', '+3V3', 'GND']),
                                     ('R9', 'Owned:R', '22', ['DP_CONN', 'DP_MCU']),
                                     ('U4', 'Owned:HEADER3', 'PHY', ['DP_MCU', '+3V3', 'GND'])]
        bindings = generator.bindings()
        line = {'id': 'DP', 'device': {'reference': 'U4', 'pin': '1'}, 'connector': {'reference': 'J2', 'pin': '1'},
                'part_number': 'OWN-PHY1'}
        bindings['roles']['usb_lines'] = [line]
        card = cards(run(parts=parts, selected=['USB_SERIES_RESISTORS'], bindings=bindings))['USB_SERIES_RESISTORS']
        self.assertEqual(card['items'][0]['reason'], 'applicability_fact_missing')
        verified = {'phy': {'id': 'phy', 'part_number': 'OWN-PHY1', 'kind': 'usb_external_series_resistor', 'value': 'required'}}
        line['applicability'] = {'fact': 'phy', 'requires': True}
        card = cards(run(parts=parts, selected=['USB_SERIES_RESISTORS'], bindings=bindings, verified=verified))['USB_SERIES_RESISTORS']
        self.assertEqual((card['status'], card['items'][0]['series'][0]['reference']), ('pass', 'R9'))
        direct = [p for p in parts if p[0] != 'R9'] + [('U5', 'Owned:HEADER3', 'PHY', ['DP_CONN', '+3V3', 'GND'])]
        line['device'] = {'reference': 'U5', 'pin': '1'}
        card = cards(run(parts=direct, selected=['USB_SERIES_RESISTORS'], bindings=bindings, verified=verified))['USB_SERIES_RESISTORS']
        self.assertEqual((card['status'], card['items'][0]['reason']), ('defect', 'series_resistor_absent'))


def scope_for(checks, requirements, paths, schematic='rules.kicad_sch'):
    return {'schema': 'agent-ee.scope/3', 'name': 'design rules', 'scope': 'design-rule card test',
            'schematic': schematic, 'inputs': [{'path': p, 'at': p} for p in paths],
            'requirements': ['requirements.md'], 'checks': checks,
            'baseline': {'requirements': requirements, 'targets': ['requirements.md'],
                         'reference_architectures': ['requirements.md']}}


def rules_check(cards_selected, **extra):
    return {'id': 'rules', 'kind': 'design_rules', 'params': {
        'pack': 'pack.json', 'bindings': 'bindings.json', 'netlist_check': 'netlist', 'cards': cards_selected, **extra}}


def requirement(rid, checks):
    return {'id': rid, 'statement': 'Design-rule cards hold', 'source': {'path': 'requirements.md', 'locator': rid},
            'checks': checks, 'intent': 'acceptance'}


class CapabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        write_json(self.root / 'pack.json', pack())
        write_json(self.root / 'bindings.json', generator.bindings())
        (self.root / 'rules.kicad_sch').write_text(generator.schematic())
        (self.root / 'requirements.md').write_text('R1: design rules')
        self.files = {p: digest(self.root / p) for p in ('pack.json', 'bindings.json', 'rules.kicad_sch', 'requirements.md')}

    def compile(self, rules, extra_checks=()):
        checks = [{'id': 'netlist', 'kind': 'netlist'}, *extra_checks, rules]
        scope = scope_for(checks, [requirement('R1', [c['id'] for c in checks])], self.files)
        return scope, compile_coverage(scope, self.files, self.root)

    def describe(self, rules, extra_checks=()):
        checks = [{'id': 'netlist', 'kind': 'netlist'}, *extra_checks, rules]
        scope = scope_for(checks, [requirement('R1', [c['id'] for c in checks])], self.files)
        return describe_check(rules, check_specs=checks, files=self.files, scope=scope, inputs=self.root)

    def test_qualified_cards_publish_one_predicate_each(self):
        _, coverage = self.compile(rules_check(['I2C_PULLUP_WINDOW', 'RESET_EN_RC']))
        descriptor = coverage['checks']['rules']
        self.assertEqual(descriptor['predicates'], ['design-rule:I2C_PULLUP_WINDOW:i2c-pullup-window/1',
                                                    'design-rule:RESET_EN_RC:reset-rc/1'])
        self.assertEqual(descriptor['immutable_inputs'], ['bindings.json', 'pack.json'])
        self.assertEqual(descriptor['prerequisites'], [{'check_id': 'netlist', 'kind': 'netlist'}])
        self.assertEqual(coverage['requirements']['R1']['acceptance_checks'], ['rules'])

    def test_advisory_or_unqualified_card_cannot_enter_an_acceptance_mapping(self):
        descriptor = self.describe(rules_check(['I2C_BUS_CAPACITANCE']))
        self.assertEqual(descriptor['status'], 'unsupported')
        self.assertIn('advisory', descriptor['unsupported_reasons'][0]['message'])
        with self.assertRaisesRegex(ValueError, 'unsupported capability rules'):
            self.compile(rules_check(['I2C_BUS_CAPACITANCE']))
        data = pack()
        data['cards'][0]['enforcement'] = {'adapter': 'i2c-pullup-window/2'}
        data['cards'][0]['parameters'] = {}
        data['cards'][1]['enforcement'] = {'check_kind': 'not_a_kind'}
        write_json(self.root / 'pack.json', data)
        self.files['pack.json'] = digest(self.root / 'pack.json')
        for card, reason in [('I2C_PULLUP_WINDOW', 'is not qualified'), ('I2C_ADDRESS_UNIQUE', 'not a registered')]:
            with self.subTest(card):
                with self.assertRaisesRegex(ValueError, reason):
                    self.compile(rules_check([card]))
        # Mixed selection: one advisory card refuses the whole check, not just that card.
        with self.assertRaisesRegex(ValueError, 'cannot be enforced or mapped'):
            self.compile(rules_check(['RESET_EN_RC', 'I2C_PULLUP_WINDOW']))

    def test_unknown_card_uncaptured_inputs_and_bad_prerequisites_are_refused(self):
        for label, rules, message in [
                ('unknown card', rules_check(['NOT_A_CARD']), 'not in rule pack'),
                ('no cards', rules_check([]), 'nonempty'),
                ('same file', {**rules_check(['RESET_EN_RC']), 'params': {**rules_check(['RESET_EN_RC'])['params'],
                                                                         'bindings': 'pack.json'}}, 'distinct'),
                ('extra key', rules_check(['RESET_EN_RC'], threshold=1), 'expected fields'),
                ('facts check', rules_check(['RESET_EN_RC'], facts_check='netlist'), 'wrong-kind prerequisite')]:
            with self.subTest(label):
                descriptor = self.describe(rules)
                self.assertEqual(descriptor['status'], 'unsupported', descriptor)
                self.assertIn(message, descriptor['unsupported_reasons'][0]['message'])

    def test_delegated_card_binds_one_single_predicate_check(self):
        data = pack()
        data['cards'][4].update(enforcement={'check_kind': 'erc'})
        write_json(self.root / 'pack.json', data)
        self.files['pack.json'] = digest(self.root / 'pack.json')
        erc = {'id': 'erc', 'kind': 'erc'}
        rules = rules_check(['POWER_DECOUPLING'], delegated={'POWER_DECOUPLING': {'check': 'erc', 'predicate': 'native:erc:violations'}})
        _, coverage = self.compile(rules, [erc])
        self.assertEqual(coverage['checks']['rules']['predicates'], ['design-rule:POWER_DECOUPLING:delegated:erc'])
        for label, delegated in [('missing', {}), ('wrong predicate', {'POWER_DECOUPLING': {'check': 'erc', 'predicate': 'x'}}),
                                 ('later check', {'POWER_DECOUPLING': {'check': 'netlist', 'predicate': 'native:erc:violations'}})]:
            with self.subTest(label):
                descriptor = self.describe(rules_check(['POWER_DECOUPLING'], delegated=delegated), [erc])
                self.assertEqual(descriptor['status'], 'unsupported', descriptor)


class NativeDesignRuleTests(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True, tools=('pdftotext',))
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.counter = 0
        self.version = subprocess.run(['/usr/bin/pdftotext', '-v'], capture_output=True,
                                      text=True, check=True).stderr.splitlines()[0]

    def write_facts(self, root, ids):
        lines = [generator.FACT_LINES[fid][3] for fid in ids]
        text_pdf(root / 'facts.pdf', lines)
        records = [{'id': fid, 'part_number': part, 'kind': kind, 'value': value, 'unit': '',
                    'conditions': 'owned fixture statement', 'source': {
                        'path': 'facts.pdf', 'sha256': digest(root / 'facts.pdf'), 'page': 1,
                        'locator': f'line {i + 1}', 'quote': quote}}
                   for i, (fid, (part, kind, value, quote)) in enumerate((f, generator.FACT_LINES[f]) for f in ids)]
        write_json(root / 'facts.json', {'schema': 'agent-ee.datasheet-facts/1', 'facts': records})

    def run_variant(self, variant='control', *, selected=GRADED, with_facts=True, bindings=None):
        self.counter += 1
        root = self.root / str(self.counter)
        root.mkdir()
        (root / 'rules.kicad_sch').write_text(generator.schematic(variant))
        (root / 'requirements.md').write_text('R1: design-rule cards from the captured pack hold.')
        write_json(root / 'pack.json', pack())
        write_json(root / 'bindings.json', bindings or generator.bindings())
        paths = ['rules.kicad_sch', 'requirements.md', 'pack.json', 'bindings.json']
        checks = [{'id': 'netlist', 'kind': 'netlist'}]
        extra = {}
        if with_facts:
            self.write_facts(root, list(generator.FACT_LINES))
            paths += ['facts.pdf', 'facts.json']
            checks.append({'id': 'facts', 'kind': 'datasheet_facts', 'params': {
                'facts': 'facts.json', 'extractor': {'path': '/usr/bin/pdftotext',
                    'sha256': digest('/usr/bin/pdftotext'), 'version': self.version}}})
            extra['facts_check'] = 'facts'
        checks.append(rules_check(selected, **extra))
        scope = scope_for(checks, [requirement('R1', [c['id'] for c in checks])], paths)
        write_json(root / 'scope.json', scope)
        run = root / 'run'
        run.mkdir()
        capture(root / 'scope.json', run)
        results = {r['id']: r for r in run_checks(run, time.monotonic() + 120)}
        self.assertEqual(results['netlist']['execution'], 'ok', results['netlist'])
        if with_facts:
            self.assertEqual((results['facts']['execution'], results['facts']['outcome']), ('ok', 'pass'), results['facts'])
        return run, results

    def test_native_variants_grade_each_card(self):
        expected = {'control': ('pass', {}),
                    'no-pullups': ('defect', {'I2C_PULLUP_WINDOW': 'defect'}),
                    'strong': ('defect', {'I2C_PULLUP_WINDOW': 'defect'}),
                    'same-address': ('defect', {'I2C_ADDRESS_UNIQUE': 'defect'}),
                    'no-load-cap': ('defect', {'CRYSTAL_LOAD_CAPS': 'defect'}),
                    'bare-en': ('defect', {'RESET_EN_RC': 'defect'})}
        for variant, (outcome, changed) in expected.items():
            with self.subTest(variant=variant):
                run, results = self.run_variant(variant)
                result = results['rules']
                self.assertEqual((result['execution'], result['outcome']), ('ok', outcome), result)
                statuses = {c['id']: c['status'] for c in result['details']['cards'] if c['enforced']}
                self.assertEqual(statuses, {c: changed.get(c, 'pass') for c in GRADED})
                self.assertEqual(result['details']['verified_facts'], sorted(generator.FACT_LINES))
                self.assertIn('work/rules/design-rules.json', result['evidence'])
                if variant == 'no-pullups':
                    items = cards(result['details'])['I2C_PULLUP_WINDOW']['items']
                    self.assertEqual([i['net_name'] for i in items], ['/SDA', '/SCL'])
                    self.assertIn("pullup_absent at /SDA (nets/net[@code=", result['location'])
                if variant == 'same-address':
                    items = cards(result['details'])['I2C_ADDRESS_UNIQUE']['items']
                    self.assertEqual({i['address'] for i in items}, {'0x48'})
                if outcome == 'defect':
                    freeze_evidence(run, list(results.values()))
                    verify_evidence(run, list(results.values()), required=True)
                    validate_witnesses(run, [{'requirement_id': 'R1', 'check_id': 'rules'}],
                                       [{'path': 'checks/rules.json', 'locator': 'outcome'}])

    def test_native_without_facts_check_fact_gated_cards_are_unknown(self):
        run, results = self.run_variant('bare-en', selected=['RESET_EN_RC', 'I2C_ADDRESS_UNIQUE'], with_facts=False)
        result = results['rules']
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)
        by_id = cards(result['details'])
        self.assertEqual(by_id['RESET_EN_RC']['items'][0]['reason'], 'applicability_fact_unverified')
        self.assertEqual({i['reason'] for i in by_id['I2C_ADDRESS_UNIQUE']['items']},
                         {'address_fact_unverified_or_inapplicable'})


class WriterContextTests(unittest.TestCase):
    def test_writer_context_carries_card_text_and_grading_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_json(Path(tmp) / 'pack.json', pack())
            manifest = {'scope': {'checks': [{'id': 'netlist', 'kind': 'netlist'}, rules_check(['RESET_EN_RC'])]}}
            context = writer_context(manifest, tmp)
        self.assertEqual(len(context), 1)
        self.assertEqual((context[0]['check_id'], context[0]['pack'], context[0]['version']),
                         ('rules', 'mcu-peripheral-basics', '1'))
        by_id = {c['id']: c for c in context[0]['cards']}
        self.assertEqual(by_id['RESET_EN_RC']['graded_by'], 'rules')
        self.assertEqual((by_id['I2C_BUS_CAPACITANCE']['graded_by'], by_id['I2C_BUS_CAPACITANCE']['advisory']), (None, True))
        self.assertEqual((by_id['I2C_PULLUP_WINDOW']['graded_by'], by_id['I2C_PULLUP_WINDOW']['advisory']), (None, False))
        self.assertEqual(by_id['RESET_EN_RC']['description'], pack()['cards'][3]['description'])
        self.assertNotIn('parameters', by_id['RESET_EN_RC'])
        self.assertEqual(writer_context({'scope': {'checks': [{'id': 'netlist', 'kind': 'netlist'}]}}, '/nonexistent'), [])


class CampaignPromptTests(unittest.TestCase):
    """The single writer receives card text through the campaign prompt; fresh checks grade the repair."""

    def setUp(self):
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        source = self.root / 'source'
        source.mkdir()
        (source / 'rules.kicad_sch').write_text(generator.schematic('strong'))
        (source / 'requirements.md').write_text('R1: design-rule cards from the captured pack hold.')
        (source / 'board').write_text('board')  # Review runs name a board; no check reads it.
        write_json(source / 'pack.json', pack())
        write_json(source / 'bindings.json', generator.bindings())
        definition = {'harness': 'codex', 'model': 'test', 'effort': 'high', 'timeout_s': 10}
        self.profile = {'schema': 'agent-ee.profile/1', 'name': 'rules-test',
                        'reviewers': [{**definition, 'role': 'circuit'}, {**definition, 'role': 'physical'}],
                        'adjudicator': definition}
        checks = [{'id': 'netlist', 'kind': 'netlist'},
                  {'id': 'rules', 'kind': 'design_rules', 'params': {
                      'pack': 'design/pack.json', 'bindings': 'design/bindings.json', 'netlist_check': 'netlist',
                      'cards': ['I2C_PULLUP_WINDOW', 'CRYSTAL_LOAD_CAPS']}}]
        scope = {'schema': 'agent-ee.scope/3', 'name': 'rules', 'scope': 'design-rule campaign test',
                 'board': 'design/board', 'schematic': 'design/rules.kicad_sch', 'mutable_inputs': ['design/rules.kicad_sch'],
                 'inputs': [{'path': str(source), 'at': 'design'}], 'review_profile': self.profile,
                 'requirements': ['design/requirements.md'], 'checks': checks,
                 'baseline': {'requirements': [{'id': 'R1', 'statement': 'Design-rule cards hold',
                     'source': {'path': 'design/requirements.md', 'locator': 'R1'},
                     'checks': ['netlist', 'rules'], 'intent': 'acceptance'}],
                     'targets': ['design/requirements.md'], 'reference_architectures': ['design/requirements.md']}}
        write_json(self.root / 'scope.json', scope)
        write_json(self.root / 'campaign.json', {'schema': 'agent-ee.campaign/1', 'scope': 'scope.json',
            'review_profile': self.profile, 'implementation': definition, 'max_iterations': 3, 'max_seconds': 120})
        self.writer_prompts = []
        self.addCleanup(patch.stopall)
        patch('agent_ee.reviewers.dispatch', side_effect=self.agent).start()
        kicad = shutil.which('kicad-cli')
        self.host = {'runtime_files': {**{name: {'path': '/usr/bin/true', 'sha256': digest('/usr/bin/true')}
            for name in ('codex', 'codex_tool_host', 'bwrap', '/usr/bin/python3')},
            'kicad-cli': {'path': kicad, 'sha256': digest(kicad)}}}

    def agent(self, run, label, definition, prompt, schema, timeout, *, cancellation=None, executable=None):
        run = Path(run)
        if label == 'implementation':
            self.writer_prompts.append(prompt)
            # Scripted repair: restore the fitted 4.7k pull-ups. Fresh checks, not this statement, grade it.
            (run / 'review-work/implementation/design/design/rules.kicad_sch').write_text(generator.schematic('control'))
            return {'execution': 'ok'}, {'summary': 'Fitted two 4.7k pull-ups per line'}
        packet = read_json(run / 'packet.json')
        ident = {k: packet[k] for k in ('candidate_id', 'run_id')}
        if label == 'adjudicator':
            checks = [read_json(p) for p in (run / 'checks').glob('*.json')]
            decisions = [{'finding_id': 'check/' + c['id'], 'disposition': 'accepted', 'reason': 'R1 failed',
                          'evidence': [{'path': 'checks/' + c['id'] + '.json', 'locator': 'outcome', 'pointer': '/outcome'}]}
                         for c in checks if c['outcome'] == 'defect']
            return {'execution': 'ok'}, {'schema': 'agent-ee.decisions/2', **ident, 'decisions': decisions}
        return {'execution': 'ok'}, {'schema': 'agent-ee.review/2', **ident,
            'packet_sha256': packet['packet_sha256'], 'role': label,
            'coverage': ['design rules'], 'limitations': ['scripted test response'], 'findings': []}

    def test_writer_prompt_carries_cards_and_repair_is_graded_by_fresh_checks(self):
        from agent_ee.campaign import run_campaign
        from agent_ee.review_run import RESUBMISSION_NOTICE
        with contextlib.redirect_stdout(io.StringIO()):
            status = run_campaign(self.root / 'campaign.json', self.root / 'campaign', host=self.host)
        state = read_json(self.root / 'campaign/state.json')
        self.assertEqual(status, 0, state)
        self.assertEqual(len(self.writer_prompts), 1)
        retained = (self.root / 'campaign/implementations/1/implementation-prompt.txt').read_text()
        self.assertEqual(retained + RESUBMISSION_NOTICE, self.writer_prompts[0])
        marker = 'Do not edit rule packs or their role bindings.\n'
        self.assertIn(marker, retained)
        context = json.loads(retained.split(marker, 1)[1])['design_rule_cards']
        by_id = {c['id']: c for c in context[0]['cards']}
        self.assertEqual(by_id['I2C_PULLUP_WINDOW']['graded_by'], 'rules')
        self.assertIsNone(by_id['I2C_BUS_CAPACITANCE']['graded_by'])
        self.assertEqual(by_id['I2C_PULLUP_WINDOW']['description'], pack()['cards'][0]['description'])
        first = read_json(self.root / 'campaign/runs/1/checks/rules.json')
        final = read_json(self.root / 'campaign/runs/2/checks/rules.json')
        self.assertEqual((first['execution'], first['outcome']), ('ok', 'defect'))
        self.assertEqual((final['execution'], final['outcome']), ('ok', 'pass'))


if __name__ == '__main__':
    unittest.main()
