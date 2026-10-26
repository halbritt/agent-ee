"""Owned synthetic pin contracts; named vendor cases are mechanism replays, not vendor data."""
import copy
import contextlib
import io
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # Also run as tests.test_pin_contracts.

from agent_ee.pin_contracts import evaluate, interval, validate_contract, capability
from agent_ee.storage import digest, read_json, write_json
from tests.test_datasheet_facts import text_pdf
from tests.test_parts_subset import captured_fixture
from tests.test_native_binding import GEN
from tests.toolchain import require_toolchain


PARAMS = dict(manifest='parts.json', facts_check='facts', netlist_check='netlist',
              ground_nets=['GND'], power_nets=['VDD', 'UP'], rails={},
              classes={'Owned:IC': dict(**{'class': 'other'}, pins=['1', '2', '3', '4']),
                       'Owned:C': dict(**{'class': 'capacitor'}, pins=['1', '2']),
                       'Owned:R': dict(**{'class': 'resistor'}, pins=['1', '2'])})


def diagnostic_fixture():
    """Explicit physical-pin net oracle, independent of the production evaluator."""
    components = {}
    part_specs = [('U1', 'AP2112K-1.8', 'Owned:IC'), ('U2', 'F1C100S', 'Owned:IC'),
                  ('U3', 'TPS54302', 'Owned:IC'), ('C1', 'C-owned', 'Owned:C'),
                  ('R1', 'R-owned', 'Owned:R')]
    parts = []
    for ref, mpn, symbol in part_specs:
        pins = ('1', '2') if symbol in ('Owned:C', 'Owned:R') else ('1', '2', '3', '4')
        components[ref] = dict(reference=ref, sheet='/', uuid=ref, lib_id=symbol, pins=pins,
            populated=True, properties=dict(MPN=mpn, Manufacturer='Owned fixture'), locator='native ' + ref)
        parts.append(dict(id=mpn, mpn=mpn, manufacturer='Owned fixture', lib_id=symbol, required=True,
            instances=[dict(reference=ref, sheet='/')], datasheet=dict(status='captured',
                artifact=dict(path='owned.pdf', sha256='a' * 64))))
    nets = dict(vdd=dict(name='VDD', terminals=[('U1', '1'), ('U2', '1'), ('C1', '1')]),
                gnd=dict(name='GND', terminals=[('U1', '2'), ('U2', '2'), ('U3', '2'), ('C1', '2'), ('R1', '2')]),
                boot=dict(name='BOOT', terminals=[('U3', '1')]),
                sw=dict(name='SW', terminals=[('U3', '3')]),
                up=dict(name='UP', terminals=[('U2', '3'), ('R1', '1')]))
    for ref in ('U1', 'U2', 'U3'):
        for pin in components[ref]['pins']:
            if not any((ref, pin) in n['terminals'] for n in nets.values()):
                nets[ref + pin] = dict(name=ref + pin, terminals=[(ref, pin)])
    graph = dict(components=components, nets=nets,
        memberships={t: nid for nid, net in nets.items() for t in net['terminals']})
    document = dict(parts=parts, property_fields=dict(mpn='MPN', manufacturer='Manufacturer'))
    contracts, facts = {}, []
    for part in parts:
        contracts[part['id']] = dict(schema='agent-ee.pin-contract/1', part_number=part['mpn'],
                                    manufacturer='Owned fixture', pins=[])
    def obligation(ref, pin, kind, value='true', other_pin=None):
        part = next(p for p in parts if any(s['reference'] == ref for s in p['instances']))
        record = next((p for p in contracts[part['id']]['pins'] if p['pin'] == pin), None)
        if record is None:
            record = dict(pin=pin, obligations=[])
            contracts[part['id']]['pins'].append(record)
        values = value if isinstance(value, list) else [value]
        ids = []
        for index, val in enumerate(values):
            fid = f'{ref}-{pin}-{kind}-{index}'
            ids.append(fid)
            quote = f'Owned test {ref} pin {pin} {kind} {val}'
            facts.append(dict(id=fid, part_number=part['mpn'], kind=kind, pin=pin, value=val,
                unit='V' if 'voltage' in kind else '', conditions='Owned synthetic test conditions',
                source=dict(path='owned.pdf', sha256='a' * 64, page=1, locator=fid, quote=quote)))
        entry = dict(kind=kind, facts=ids)
        if other_pin:
            entry['other_pin'] = other_pin
        record['obligations'].append(entry)
    obligation('U1', '1', 'provides_voltage', '1.8')
    obligation('U2', '1', 'requires_voltage', '2.5')
    obligation('U3', '1', 'capacitor_to_pin', other_pin='3')
    for ref, comp in components.items():
        for pin in comp['pins']:
            obligation(ref, pin, 'must_be_connected' if len(nets[graph['memberships'][ref, pin]]['terminals']) > 1
                       else 'do_not_connect')
    return document, contracts, facts, graph, obligation


class PinContractTests(unittest.TestCase):
    def setUp(self):
        self.document, self.contracts, self.facts, self.graph, self.add = diagnostic_fixture()
        self.params = copy.deepcopy(PARAMS)

    def run_contracts(self, rejected=()):
        verified = {f['id']: f for f in self.facts if f['id'] not in rejected}
        before = copy.deepcopy((self.document, self.contracts, self.facts, self.graph, self.params))
        report = evaluate(self.document, self.contracts, self.facts, verified, self.graph, self.params)
        self.assertEqual(before, (self.document, self.contracts, self.facts, self.graph, self.params))
        return report

    def record(self, kind, ref='U2', pin='1', report=None):
        report = report or self.run_contracts()
        return next(r for r in report['records'] if (r['reference'], r['pin'], r['kind']) == (ref, pin, kind))

    def test_f1c100s_wrong_variant_names_both_physical_pins_and_pages(self):
        report = self.run_contracts()
        self.assertEqual((report['execution'], report['outcome']), ('ok', 'defect'))
        row = self.record('requires_voltage', report=report)
        self.assertEqual(row['status'], 'defect')
        self.assertEqual((row['reference'], row['pin']), ('U2', '1'))
        self.assertEqual([(p['reference'], p['pin']) for p in row['providers']], [('U1', '1')])
        self.assertEqual(row['voltage_v'], ['2.5', '2.5'])
        self.assertEqual(row['providers'][0]['voltage_v'], ['1.8', '1.8'])
        for source in row['sources'] + row['providers'][0]['sources']:
            self.assertEqual(source['page'], 1)
            self.assertTrue(source['locator'] and source['quote'])

    def test_matching_3v3_scalar_and_tolerance_containment(self):
        for fact in self.facts:
            if 'voltage' in fact['kind']:
                fact['value'] = '3.3'
        self.assertEqual(self.record('requires_voltage')['status'], 'pass')
        # Full interval containment, including exact boundary equality, is required.
        for required, provided, expected in [(['3.0', '3.6'], ['3.1', '3.5'], 'pass'),
                (['3.0', '3.6'], ['3.0', '3.6'], 'pass'),
                (['3.0', '3.6'], ['2.9', '3.3'], 'defect'),
                (['3.0', '3.6'], ['3.3', '3.7'], 'defect')]:
            with self.subTest(required=required, provided=provided):
                self.setUp()
                for ref, kind, vals in [('U1', 'provides_voltage', provided), ('U2', 'requires_voltage', required)]:
                    part = self.document['parts'][0 if ref == 'U1' else 1]
                    pin = self.contracts[part['id']]['pins'][0]
                    pin['obligations'] = [o for o in pin['obligations'] if o['kind'] != kind]
                    self.add(ref, '1', kind, vals)
                self.assertEqual(self.record('requires_voltage')['status'], expected)

    def test_prose_wrong_units_and_unverified_provider_are_unknown(self):
        for value, unit in [('nominal supply', 'V'), ('3.3', 'mV'), ('NaN', 'V'), ('3.3 V', 'V')]:
            with self.subTest(value=value):
                self.setUp()
                fact = next(f for f in self.facts if f['kind'] == 'requires_voltage')
                fact.update(value=value, unit=unit)
                self.assertEqual(self.record('requires_voltage')['status'], 'unknown')
                self.assertEqual(self.run_contracts()['outcome'], 'unknown')
        self.setUp()
        provider = next(f for f in self.facts if f['kind'] == 'provides_voltage')
        report = self.run_contracts([provider['id']])
        self.assertEqual(self.record('requires_voltage', report=report)['status'], 'unknown')
        self.assertEqual(report['outcome'], 'unknown')

    def test_singleton_must_connect_and_connected_no_connect_are_separate_defects(self):
        self.add('U1', '3', 'must_be_connected')
        self.add('U1', '1', 'do_not_connect')
        self.assertEqual(self.record('must_be_connected', 'U1', '3')['status'], 'defect')
        self.assertEqual(self.record('do_not_connect', 'U1', '1')['status'], 'defect')
        self.assertEqual(self.record('do_not_connect', 'U1', '4')['status'], 'pass')

    def move(self, ref, pin, nid):
        old = self.graph['memberships'][ref, pin]
        self.graph['nets'][old]['terminals'].remove((ref, pin))
        self.graph['memberships'][ref, pin] = nid
        self.graph['nets'][nid]['terminals'].append((ref, pin))

    def test_boot_sw_and_decoupling_require_direct_capacitor_endpoints(self):
        row = self.record('capacitor_to_pin', 'U3', '1')
        self.assertEqual(row['status'], 'defect')
        self.assertEqual(row['sources'][0]['page'], 1)
        self.assertIn('capacitor_to_pin', row['sources'][0]['quote'])
        self.add('U2', '1', 'should_have_decoupling_capacitor')
        self.assertEqual(self.record('should_have_decoupling_capacitor')['status'], 'pass')
        self.move('C1', '2', 'up')
        self.assertEqual(self.record('should_have_decoupling_capacitor')['status'], 'defect')
        self.move('C1', '1', 'boot')
        self.assertEqual(self.record('capacitor_to_pin', 'U3', '1')['status'], 'defect')
        self.move('C1', '2', 'sw')
        self.assertEqual(self.record('capacitor_to_pin', 'U3', '1')['status'], 'pass')
        self.graph['components']['C1']['populated'] = False
        self.assertEqual(self.record('capacitor_to_pin', 'U3', '1')['status'], 'defect')

    def test_pullup_needs_resistor_to_power_and_no_conduction_inference(self):
        self.add('U2', '3', 'needs_external_pullup')
        self.assertEqual(self.record('needs_external_pullup', pin='3')['status'], 'defect')
        self.move('R1', '2', 'vdd')
        self.assertEqual(self.record('needs_external_pullup', pin='3')['status'], 'pass')
        # A resistor from UP to VDD does not supply UP with a voltage.
        self.add('U2', '3', 'requires_voltage', '1.8')
        self.assertEqual(self.record('requires_voltage', pin='3')['status'], 'unknown')
        self.params['rails']['UP'] = ['1.7', '1.9']
        self.assertEqual(self.record('requires_voltage', pin='3')['status'], 'defect')
        self.params['rails']['UP'] = '1.8'
        self.assertEqual(self.record('requires_voltage', pin='3')['status'], 'pass')

    def test_unknown_empty_mismatched_ambiguous_and_unmatched_coverage(self):
        for mutation, reason in [
                (lambda: self.contracts.pop('F1C100S'), 'no contract file'),
                (lambda: self.contracts['F1C100S']['pins'].clear(), 'empty contract'),
                (lambda: self.contracts['F1C100S'].update(manufacturer='Wrong manufacturer'), 'manufacturer'),
                (lambda: self.contracts['F1C100S']['pins'].append(copy.deepcopy(self.contracts['F1C100S']['pins'][0])), 'ambiguous'),
                (lambda: self.contracts['F1C100S']['pins'][0].update(pin='99'), 'unmatched'),
                (lambda: self.graph['components']['U2']['properties'].update(MPN='Different variant'), 'mismatch')]:
            with self.subTest(reason=reason):
                self.setUp()
                mutation()
                report = self.run_contracts()
                self.assertEqual((report['execution'], report['outcome']), ('unsupported', 'unknown'))
                reasons = [r['reason'] or '' for r in report['records']] + [c['reason'] for c in report['coverage']]
                self.assertTrue(any(reason in r for r in reasons), reasons)
                if reason == 'ambiguous':
                    self.assertTrue(all(not r['bound'] for r in report['records']
                                        if r['reference'] == 'U2' and r['pin'] == '1'))

    def test_feedback_requires_distinct_resistors_to_both_rail_classes(self):
        self.add('U2', '3', 'feedback_divider')
        self.assertEqual(self.record('feedback_divider', pin='3')['status'], 'defect')
        part = next(p for p in self.document['parts'] if p['id'] == 'R-owned')
        part['instances'].append(dict(reference='R2', sheet='/'))
        self.graph['components']['R2'] = dict(copy.deepcopy(self.graph['components']['R1']), reference='R2', uuid='R2')
        for pin, nid in (('1', 'up'), ('2', 'vdd')):
            self.graph['memberships']['R2', pin] = nid
            self.graph['nets'][nid]['terminals'].append(('R2', pin))
        self.assertEqual(self.record('feedback_divider', pin='3')['status'], 'pass')
        self.move('R2', '2', 'gnd')
        self.assertEqual(self.record('feedback_divider', pin='3')['status'], 'defect')

    def test_repeated_mpn_instances_are_evaluated_independently(self):
        part = self.document['parts'][1]
        part['instances'].append(dict(reference='U4', sheet='/'))
        self.graph['components']['U4'] = copy.deepcopy(self.graph['components']['U2'])
        self.graph['components']['U4'].update(reference='U4', uuid='U4')
        for pin in ('1', '2', '3', '4'):
            self.graph['nets']['four' + pin] = dict(name='four' + pin, terminals=[('U4', pin)])
            self.graph['memberships']['U4', pin] = 'four' + pin
        self.params['power_nets'].append('four1')
        self.params['rails']['four1'] = '2.5'
        report = self.run_contracts()
        self.assertEqual(self.record('requires_voltage', 'U2', report=report)['status'], 'defect')
        self.assertEqual(self.record('requires_voltage', 'U4', report=report)['status'], 'pass')

    def test_unclassified_elements_cannot_prove_absence_and_missing_pin_net_is_unknown(self):
        self.add('U2', '1', 'should_have_decoupling_capacitor')
        del self.params['classes']['Owned:C']
        self.assertEqual(self.record('should_have_decoupling_capacitor')['status'], 'unknown')
        del self.graph['memberships']['U2', '1']
        self.assertEqual(self.record('requires_voltage')['status'], 'unknown')

    def test_contract_and_interval_reject_malformed_boundaries(self):
        for v in (True, False, [], [1], [2, 1], [1, 2, 3], 'NaN', float('inf'), '3V', {}, None):
            with self.subTest(value=v), self.assertRaises(ValueError):
                interval(v)
        for contract in self.contracts.values():
            self.assertIs(validate_contract(contract), contract)
        contract = copy.deepcopy(self.contracts['F1C100S'])
        contract['pins'][0]['obligations'][0]['kind'] = 'future_predicate'
        with self.assertRaises(ValueError):
            validate_contract(contract)


class NativePinContractsTests(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True, tools=('pdftotext',))
        self.temp = tempfile.TemporaryDirectory(prefix='agent-ee-pin-contracts-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.document = captured_fixture(self, self.root)
        self.params = copy.deepcopy(PARAMS)
        self.params.update(classes={'Owned:R': dict(**{'class': 'other'}, pins=['1', '2'])},
                           power_nets=['/VDD'], rails={'/VDD': '3.3'})
        part = self.document['parts'][0]
        part['instances'].append(dict(reference='R2', sheet='/'))
        self.contract = dict(schema='agent-ee.pin-contract/1', part_number=part['mpn'],
            manufacturer=part['manufacturer'], pins=[
                dict(pin='1', obligations=[dict(kind='requires_voltage', facts=['vdd']),
                                           dict(kind='must_be_connected', facts=['connected'])]),
                dict(pin='2', obligations=[dict(kind='do_not_connect', facts=['nc'])])])
        lines = ['Owned test pin 1 requires_voltage 3.3 V', 'Owned test pin 1 must_be_connected true',
                 'Owned test pin 2 do_not_connect true']
        text_pdf(self.root / 'owned.pdf', lines)
        self.facts = [dict(id=fid, part_number=part['mpn'], kind=kind, pin=pin,
                          value=value, unit=unit, conditions='Owned synthetic demonstration',
                          source=dict(path='owned.pdf', sha256=digest(self.root / 'owned.pdf'), page=1,
                                      locator=f'line {index + 1}', quote=lines[index]))
                      for index, (fid, kind, pin, value, unit) in enumerate([
                          ('vdd', 'requires_voltage', '1', '3.3', 'V'),
                          ('connected', 'must_be_connected', '1', 'true', ''),
                          ('nc', 'do_not_connect', '2', 'true', '')])]
        part['datasheet']['artifact'] = dict(path='owned.pdf', sha256=digest(self.root / 'owned.pdf'))
        nodes = ''
        for index, ref in enumerate(('R1', 'R2')):
            x = 40 + index * 40
            node = GEN._symbol(ref, 'R', 'Owned:R2', ('yes', 'yes', 'no'), 1, x, GEN._root_entries(ref, 1, 'control'))
            node = node[:-1] + GEN.prop('MPN', part['mpn']) + GEN.prop('Manufacturer', part['manufacturer']) + ')'
            # Pin 1 coincides with this label. Pin 2 remains a native singleton.
            node += (f'(label "VDD" (at {x} 50 0) (effects (font (size 1 1))) '
                     f'(uuid "{GEN.uid(ref + "-vdd")}"))')
            nodes += node
        (self.root / 'binding.kicad_sch').write_text(GEN._sheet_file(GEN.uid('root'), nodes))
        (self.root / 'binding.kicad_pcb').write_text('(kicad_pcb (version 20241229) (generator "owned"))\n')
        self.tool = dict(path='/usr/bin/pdftotext', sha256=digest('/usr/bin/pdftotext'),
            version=subprocess.run(['/usr/bin/pdftotext', '-v'], text=True, capture_output=True, check=True).stderr.splitlines()[0])
        self.index = 0

    def prepare(self, schema='agent-ee.scope/3'):
        if self.contract is None:
            self.document['parts'][0].pop('pin_contract', None)
        else:
            write_json(self.root / 'contract.json', self.contract)
            self.document['parts'][0]['pin_contract'] = dict(path='contract.json', sha256=digest(self.root / 'contract.json'))
        write_json(self.root / 'parts.json', self.document)
        write_json(self.root / 'facts.json', dict(schema='agent-ee.datasheet-facts/1', facts=self.facts))
        checks = [dict(id='netlist', kind='netlist'), dict(id='facts', kind='datasheet_facts',
            params=dict(facts='facts.json', extractor=self.tool)), dict(id='pins', kind='pin_contracts', params=self.params)]
        paths = [p.relative_to(self.root).as_posix() for p in self.root.rglob('*')
                 if p.is_file() and not p.relative_to(self.root).parts[0].startswith('run') and p.name != 'scope.json']
        requirements = [dict(id='inputs', intent='informational', statement='Capture native connectivity and citations',
                             source=dict(path='requirements.md', locator='line 1'), checks=['netlist', 'facts']),
                        dict(id='electrical', intent='acceptance', statement='Obey page-cited pin contracts',
                             source=dict(path='requirements.md', locator='line 1'), checks=['pins'])]
        if schema.endswith('/2'):
            for req in requirements:
                del req['intent']
        scope = dict(schema=schema, name='Owned pin contract test', scope='Synthetic pin electrical obligations',
            schematic='binding.kicad_sch', board='binding.kicad_pcb', inputs=[dict(path=p, at=p) for p in paths],
            requirements=['requirements.md'], checks=checks,
            baseline=dict(requirements=requirements, targets=['requirements.md'], reference_architectures=['requirements.md']))
        write_json(self.root / 'scope.json', scope)
        return scope

    def run_case(self, schema='agent-ee.scope/3'):
        from agent_ee.run_record import run_review
        self.prepare(schema)
        self.index += 1
        run = self.root / f'run{self.index}'
        with contextlib.redirect_stdout(io.StringIO()):
            run_review(self.root / 'scope.json', run, max_seconds=120, checks_only=True)
        self.assertTrue((run / 'checks/pins.json').is_file(), read_json(run / 'result.json').get('error'))
        return run, read_json(run / 'checks/pins.json')

    def prepare_named_replay(self, capacitor_to=None, decoupling=False):
        """Export the explicit oracle as original KiCad symbols with exact labelled nets."""
        document, contracts, facts, graph, add = diagnostic_fixture()
        if decoupling:
            add('U2', '1', 'should_have_decoupling_capacitor')
        if capacitor_to == 'absent':
            document['parts'] = [p for p in document['parts'] if p['id'] != 'C-owned']
            del graph['components']['C1']
        elif capacitor_to:
            for pin, nid in zip(('1', '2'), capacitor_to):
                graph['memberships']['C1', pin] = nid
        template = self.document['parts'][0]
        self.document['parts'] = []
        for part in document['parts']:
            complete = dict(copy.deepcopy(template), **part)
            # The manifest retains its ordinary dependency schema; this test does
            # not claim catalog/CAD qualification of these synthetic allocations.
            complete['datasheet'] = copy.deepcopy(template['datasheet'])
            complete['selection_id'] = part['id']
            complete['lcsc'] = 'C' + str(len(self.document['parts']) + 1)
            self.document['parts'].append(complete)
        self.facts = facts
        self.params = copy.deepcopy(PARAMS)
        for key in ('ground_nets', 'power_nets'):
            self.params[key] = ['/' + n for n in self.params[key]]
        for fact in facts:
            fact['source']['quote'] = ('voltage ' + fact['value'] + ' V' if 'voltage' in fact['kind']
                                       else 'pin ' + fact['pin'] + ' true')
        text_pdf(self.root / 'owned.pdf', sorted({f['source']['quote'] for f in facts}))
        pdf_hash = digest(self.root / 'owned.pdf')
        for fact in facts:
            fact['source']['sha256'] = pdf_hash
        for part in self.document['parts']:
            part['datasheet']['artifact'] = dict(path='owned.pdf', sha256=pdf_hash)
            path = 'contract-' + part['id'] + '.json'
            write_json(self.root / path, contracts[part['id']])
            part['pin_contract'] = dict(path=path, sha256=digest(self.root / path))
        self.contract = contracts['AP2112K-1.8']
        body = ''
        for index, (ref, comp) in enumerate(graph['components'].items()):
            x = 30 + index * 40
            source_symbol = 'Quad' if comp['lib_id'] == 'Owned:IC' else 'R'
            node = GEN._symbol(ref, source_symbol, template['footprint'], ('yes', 'yes', 'no'), 1, x,
                               GEN._root_entries(ref, 1, 'control'))
            node = node.replace('Owned:' + source_symbol + '"', comp['lib_id'] + '"')
            node = node[:-1] + ''.join(GEN.prop(k, v) for k, v in comp['properties'].items()) + ')'
            for pin in comp['pins']:
                net = graph['nets'][graph['memberships'][ref, pin]]['name']
                node += (f'(label "{net}" (at {x} {50 + (int(pin) - 1) * 2.54} 0) '
                         f'(effects (font (size 1 1))) (uuid "{GEN.uid(ref + pin + net)}"))')
            body += node
        extra = (GEN._definition('IC', GEN.SYMBOLS['Quad'], prefix='Owned:') +
                 GEN._definition('C', GEN.SYMBOLS['R'], prefix='Owned:'))
        schematic = GEN._sheet_file(GEN.uid('root'), body).replace('(lib_symbols ', '(lib_symbols ' + extra)
        (self.root / 'binding.kicad_sch').write_text(schematic)

    def test_acquired_pdf_labels_survive_native_contract_receipt(self):
        from tests.test_datasheet_integration import CanonicalAdapterTests
        from tests.test_datasheet_capture import DatasheetCaptureTests
        from agent_ee.datasheet_capture import capture_datasheets, import_historical_pdf
        from agent_ee.datasheet_parts import bindings_from_selection
        from agent_ee.candidate import capture
        from agent_ee.checks import run_checks
        server = DatasheetCaptureTests()
        server.setUp()
        self.addCleanup(server.doCleanups)
        server.pdf = (self.root / 'owned.pdf').read_bytes()
        url = server.url + '/pdf'
        helper = CanonicalAdapterTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        snap, sha, req, selection = helper.selected(url=url, quantity=1)
        bindings = bindings_from_selection(snap, sha, req, selection, {'resistors': ['R1']})
        self.assertEqual(bindings['parts'][0]['part_number'], self.document['parts'][0]['mpn'])
        write_json(self.root / 'bindings.json', bindings)
        for historical in (False, True):
            directory = 'historical' if historical else 'live'
            if historical:
                write_json(self.root / 'sources.json', [dict(file='owned.pdf', sha256=digest(self.root / 'owned.pdf'),
                    url=url, captured_at_utc='2026-01-01T00:00:00Z')])
                manifest = import_historical_pdf(self.root / 'owned.pdf', self.root,
                    dict(path='sources.json', sha256=digest(self.root / 'sources.json'), entry=0),
                    'R1', self.root / directory)
            else:
                manifest = capture_datasheets([dict(id='R1', datasheet_url=url)], self.root / directory)
            pdf = manifest['parts'][0]
            self.document['parts'][0]['datasheet']['artifact'] = dict(
                path=directory + '/' + pdf['path'], sha256=pdf['sha256'])
            scope = self.prepare()
            facts = copy.deepcopy(self.facts)
            for fact in facts:
                fact['source']['path'] = directory + '/' + pdf['path']
            write_json(self.root / 'facts.json', dict(schema='agent-ee.datasheet-facts/2',
                bindings=dict(path='bindings.json', sha256=digest(self.root / 'bindings.json')),
                acquisition=dict(path=directory + '/manifest.json', sha256=digest(self.root / directory / 'manifest.json')),
                required_parts=[dict(reference='R1', selection_id=bindings['parts'][0]['selection_id'],
                    part_number=bindings['parts'][0]['part_number'], required_facts=[f['id'] for f in facts])], facts=facts))
            write_json(self.root / 'scope.json', scope)
            run = self.root / ('run-label-' + directory)
            run.mkdir()
            capture(self.root / 'scope.json', run)
            checks = {c['id']: c for c in run_checks(run, time.monotonic() + 120)}
            method, redirect = ('historical_import', 'unknown') if historical else ('live', 'observed')
            with self.subTest(historical=historical):
                self.assertEqual((checks['pins']['execution'], checks['pins']['outcome']), ('ok', 'pass'), checks)
                for receipt in (checks['pins']['details'], read_json(run / 'work/pins/pin-contracts.json')):
                    self.assertEqual(receipt['acquisitions']['R1']['acquisition_method'], method)
                    self.assertEqual(receipt['acquisitions']['R1']['redirect_provenance'], redirect)

    def test_native_named_wrong_ldo_variant_and_missing_boot_capacitor(self):
        self.prepare_named_replay()
        run, check = self.run_case()
        self.assertEqual((check['execution'], check['outcome']), ('ok', 'defect'), check)
        voltage = next(r for r in check['details']['records'] if r['kind'] == 'requires_voltage')
        self.assertEqual((voltage['reference'], voltage['pin'], voltage['status']), ('U2', '1', 'defect'))
        self.assertEqual([(p['reference'], p['pin'], p['voltage_v']) for p in voltage['providers']],
                         [('U1', '1', ['1.8', '1.8'])])
        boot = next(r for r in check['details']['records'] if r['kind'] == 'capacitor_to_pin')
        self.assertEqual((boot['reference'], boot['pin'], boot['status']), ('U3', '1', 'defect'))
        self.assertEqual((boot['sources'][0]['page'], boot['sources'][0]['quote']), (1, 'pin 1 true'))
        from agent_ee.run_record import verify
        verify(run)

    def test_native_decoupling_missing_wrong_ground_and_boot_cap_present(self):
        for endpoints, expected in [('absent', 'defect'), (('vdd', 'up'), 'defect'), (None, 'pass')]:
            with self.subTest(endpoints=endpoints):
                self.prepare_named_replay(capacitor_to=endpoints, decoupling=True)
                _, check = self.run_case()
                self.assertEqual(check['execution'], 'ok', check)
                row = next(r for r in check['details']['records'] if r['kind'] == 'should_have_decoupling_capacitor')
                self.assertEqual(row['status'], expected)
        self.prepare_named_replay(capacitor_to=('boot', 'sw'))
        _, check = self.run_case()
        self.assertEqual(check['execution'], 'ok', check)
        row = next(r for r in check['details']['records'] if r['kind'] == 'capacitor_to_pin')
        self.assertEqual((row['status'], row['components']), ('pass', ['C1']))

    def test_native_singleton_must_connect_and_multi_terminal_no_connect(self):
        self.contract['pins'][0]['obligations'][1]['kind'] = 'do_not_connect'
        self.contract['pins'][1]['obligations'][0]['kind'] = 'must_be_connected'
        self.facts[1]['kind'] = 'do_not_connect'
        self.facts[2]['kind'] = 'must_be_connected'
        # The authored applicability changes; the source still cites exact true text.
        _, check = self.run_case()
        self.assertEqual((check['execution'], check['outcome']), ('ok', 'defect'), check)
        rows = [r for r in check['details']['records'] if r['kind'] != 'requires_voltage']
        self.assertEqual([r['status'] for r in rows], ['defect'] * 4)

    def test_native_positive_scope3_registration_preflight_baseline_and_public_verify(self):
        from agent_ee.run_record import verify
        run, check = self.run_case()
        self.assertEqual((check['execution'], check['outcome']), ('ok', 'pass'), check)
        self.assertEqual([(r['reference'], r['status']) for r in check['details']['records']
                          if r['kind'] == 'requires_voltage'], [('R1', 'pass'), ('R2', 'pass')])
        self.assertTrue(all(r['sources'][0]['page'] == 1 for r in check['details']['records']))
        invocation = read_json(run / 'work/pins/execution.json')
        self.assertIn('--unshare-net', invocation['sandbox_argv'])
        baseline = read_json(run / 'baseline.json')
        descriptor = baseline['coverage']['checks']['pins']
        self.assertTrue(descriptor['can_evaluate_bound'])
        self.assertEqual(descriptor['prerequisites'], [dict(check_id='netlist', kind='netlist'),
                                                     dict(check_id='facts', kind='datasheet_facts')])
        self.assertEqual(set(descriptor['predicates']), {'pin-contracts:requires_voltage',
            'pin-contracts:must_be_connected', 'pin-contracts:do_not_connect'})
        self.assertIn('contract.json', baseline['files'])
        self.assertIn('owned.pdf', baseline['files'])
        result = read_json(run / 'result.json')
        self.assertIn('kicad-cli', result['launch_expectation']['required_runtime'])
        self.assertEqual(result['launch_expectation']['declared_tools']['/usr/bin/pdftotext'], self.tool['sha256'])
        verify(run)

    def test_native_voltage_defect_sealed_witness_and_tamper_refusal(self):
        from agent_ee.run_record import verify
        from agent_ee.baseline import validate_witnesses
        self.params['rails']['/VDD'] = '1.8'
        run, check = self.run_case()
        self.assertEqual((check['execution'], check['outcome']), ('ok', 'defect'), check)
        validate_witnesses(run, [dict(requirement_id='electrical', check_id='pins')],
                           [dict(path='checks/pins.json', locator='details.records')])
        verify(run)
        for rel in ('inputs/contract.json', 'inputs/owned.pdf', 'work/pins/pin-contracts.json',
                    'work/facts/facts.json', 'work/netlist/design.xml'):
            path = run / rel
            original = path.read_bytes()
            path.write_bytes(original + b' ')
            with self.subTest(rel=rel), self.assertRaises(ValueError):
                verify(run)
            path.write_bytes(original)

    def test_actual_pdftotext_rejection_leaves_affected_obligations_unknown(self):
        from agent_ee.run_record import verify
        from agent_ee.baseline import validate_witnesses
        self.facts[0]['source']['quote'] = 'Invented requires_voltage 3.3 V'
        run, check = self.run_case()
        producer = read_json(run / 'checks/facts.json')
        self.assertEqual((producer['execution'], producer['outcome']), ('unsupported', 'unknown'))
        self.assertEqual(producer['details']['facts'][0]['reason'], 'quote_absent_from_page')
        self.assertEqual((check['execution'], check['outcome']), ('unsupported', 'unknown'), check)
        self.assertEqual([r['status'] for r in check['details']['records'] if r['kind'] == 'requires_voltage'], ['unknown'] * 2)
        with self.assertRaises(ValueError):
            validate_witnesses(run, [dict(requirement_id='electrical', check_id='pins')],
                               [dict(path='checks/pins.json', locator='details.records')])
        verify(run)

    def test_native_authored_presence_obligations_accept_verified_source_text(self):
        lines = ['Supply is 3.3 V', 'Connect pin 1 to the supply.', 'Leave pin 2 open.']
        text_pdf(self.root / 'owned.pdf', lines)
        pdf_hash = digest(self.root / 'owned.pdf')
        self.document['parts'][0]['datasheet']['artifact']['sha256'] = pdf_hash
        for fact, quote in zip(self.facts, lines):
            fact['source'].update(quote=quote, sha256=pdf_hash)
        self.facts[1]['value'] = 'Connect pin 1'
        self.facts[2]['value'] = 'Leave pin 2 open'
        run, check = self.run_case()
        self.assertEqual((check['execution'], check['outcome']), ('ok', 'pass'), check)
        self.assertEqual([r['status'] for r in check['details']['records']], ['pass'] * 6)
        self.assertEqual([r['sources'][0]['quote'] for r in check['details']['records'][:3]], lines)
        self.assertEqual(read_json(run / 'checks/facts.json')['execution'], 'ok')

    def test_native_missing_contract_empty_contract_manufacturer_mismatch_and_ambiguity(self):
        initial = copy.deepcopy(self.contract)
        for case in ('missing', 'empty', 'manufacturer', 'ambiguous'):
            with self.subTest(case=case):
                self.contract = copy.deepcopy(initial)
                if case == 'missing':
                    self.contract = None
                elif case == 'empty':
                    self.contract['pins'] = []
                elif case == 'manufacturer':
                    self.contract['manufacturer'] = 'Wrong manufacturer'
                else:
                    self.contract['pins'].append(copy.deepcopy(initial['pins'][0]))
                _, check = self.run_case('agent-ee.scope/2')
                self.assertEqual((check['execution'], check['outcome']), ('unsupported', 'unknown'), check)
                if case in ('missing', 'empty'):
                    self.assertEqual(len(check['details']['coverage']), 2)
                elif case == 'ambiguous':
                    self.assertGreater(check['details']['counts']['ambiguous'], 0)
                else:
                    self.assertTrue(all(r['status'] == 'unknown' for r in check['details']['records']))

    def test_scope2_freezes_closure_and_capability_refuses_forward_or_mutated_inputs(self):
        from agent_ee.baseline import compile_baseline
        from agent_ee.capabilities import describe_check
        scope = self.prepare('agent-ee.scope/2')
        files = {p.relative_to(self.root).as_posix(): digest(p) for p in self.root.rglob('*') if p.is_file()}
        baseline = compile_baseline(scope, files, self.root)
        self.assertIn('contract.json', baseline['files'])
        self.assertIn('snapshot/catalog.sqlite3', baseline['files'])
        scope['checks'].reverse()
        check = scope['checks'][0]
        descriptor = describe_check(check, check_specs=scope['checks'], scope=scope, files=files, inputs=self.root)
        self.assertEqual(descriptor['status'], 'unsupported')
        scope['checks'].reverse()
        (self.root / 'contract.json').write_text('{}')
        with self.assertRaises(ValueError):
            capability(self.params, scope, files, self.root, 'pins')

    def test_failed_missing_and_mismatched_native_prerequisites_never_pass(self):
        from agent_ee.checks import Ctx, CHECK_FUNCS, CheckError
        run, _ = self.run_case()
        manifest = read_json(run / 'candidate.json')
        spec = manifest['scope']['checks'][-1]
        path = run / 'checks/netlist.json'
        original = path.read_bytes()
        for change in ({'execution': 'failed'}, {'candidate_id': 'stale-candidate'}):
            prior = read_json(path)
            prior.update(change)
            write_json(path, prior)
            ctx = Ctx(run, manifest, spec, time.monotonic() + 60)
            with self.subTest(change=change), self.assertRaises(CheckError):
                CHECK_FUNCS['pin_contracts'](ctx)
            self.assertEqual(ctx.result['outcome'], 'unknown')
            path.write_bytes(original)
        path.unlink()
        with self.assertRaises(CheckError):
            CHECK_FUNCS['pin_contracts'](Ctx(run, manifest, spec, time.monotonic() + 60))


if __name__ == '__main__':
    unittest.main()
