import contextlib
import io
from unittest.mock import patch
import copy
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from agent_ee.baseline import freeze_evidence, load_baseline, verify_evidence
from agent_ee.candidate import capture
from agent_ee.cli import verify, run_review
from agent_ee.checks import run_checks
from agent_ee.storage import digest, read_json, write_json
from toolchain import require_toolchain

ROOT = Path(__file__).resolve().parents[1]
PDF = ROOT / 'references/TJA1051.pdf'


class DatasheetFactsTests(unittest.TestCase):
    def setUp(self):
        require_toolchain(tools=('pdftotext',))
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.pdf = PDF
        self.version = subprocess.run(['/usr/bin/pdftotext', '-v'], capture_output=True,
                                      text=True, check=True).stderr.splitlines()[0]
        self.fact = {'id': 'canl', 'part_number': 'TJA1051T', 'kind': 'pin_function',
                     'pin': '6', 'value': 'CANL', 'unit': '', 'conditions': 'Table 3, TJA1051T',
                     'source': {'path': 'TJA1051.pdf', 'sha256': digest(PDF), 'page': 4,
                                'locator': 'Table 3, CANL row',
                                'quote': 'CANL          6          LOW-level CAN bus line'}}

    def run_fact(self, fixture=None):
        facts = [self.fact]
        if fixture:
            canh = copy.deepcopy(self.fact)
            canh.update(id='canh', pin='7', value='CANH')
            canh['source'].update(quote='CANH          7          HIGH-level CAN bus line', locator='Table 3, CANH row')
            facts.append(canh)
        write_json(self.root / 'facts.json', {'schema': 'agent-ee.datasheet-facts/1', 'facts': facts})
        scope = {'schema': 'agent-ee.scope/1', 'name': 'datasheet test', 'scope': 'pin-function citation test', 'inputs': [
            {'path': str(self.pdf), 'at': 'TJA1051.pdf'},
            {'path': str(self.root / 'facts.json'), 'at': 'facts.json'}],
            'checks': [{'id': 'facts', 'kind': 'datasheet_facts', 'params': {
                'facts': 'facts.json', 'extractor': {'path': '/usr/bin/pdftotext',
                    'sha256': digest('/usr/bin/pdftotext'), 'version': self.version}}}]}
        if fixture:
            scope['inputs'].extend([
                {'path': str(ROOT / 'fixtures/datasheet-pins' / fixture), 'at': 'project'},
                {'path': '/usr/share/kicad/template', 'at': 'stock/template',
                 'include': ['sym-lib-table', 'fp-lib-table']}])
            scope.update(schematic='project/pins.kicad_sch', board='project/pins.kicad_pcb')
            scope['checks'] = [{'id': 'erc', 'kind': 'erc'}, {'id': 'drc', 'kind': 'drc'},
                               {'id': 'netlist', 'kind': 'netlist'}, *scope['checks'],
                {'id': 'pin-map', 'kind': 'pin_map', 'params': {
                    'facts_check': 'facts', 'netlist_check': 'netlist', 'symbols': [
                        {'reference': 'U1', 'part_number': 'TJA1051T', 'facts': ['canl', 'canh']}]}}]
        self.scope = scope
        write_json(self.root / 'scope.json', scope)
        run = self.root / ('run' + str(len(list(self.root.glob('run*')))))
        run.mkdir()
        capture(self.root / 'scope.json', run)
        self.run = run
        self.results = run_checks(run, time.monotonic() + 60)
        return next(r for r in self.results if r['id'] == 'facts')

    def test_captured_tja1051_pin_fact_verifies(self):
        result = self.run_fact()
        self.assertEqual(result['execution'], 'ok', result)
        self.assertEqual(result['details']['facts'][0]['status'], 'verified')

    def test_citation_rejections_have_specific_reasons(self):
        cases = [('page', 3, 'quote_absent_from_page'),
                 ('page', 99, 'page_out_of_range'),
                 ('quote', 'CANL 6 invented text', 'quote_absent_from_page'),
                 ('quote', '6 LOW-level CAN bus line', 'value_absent_from_quote'),
                 ('sha256', '0' * 64, 'pdf_hash_mismatch')]
        for key, value, reason in cases:
            with self.subTest(reason=reason):
                old = self.fact['source'][key]
                self.fact['source'][key] = value
                result = self.run_fact()
                self.assertEqual(result['execution'], 'unsupported', result)
                self.assertEqual(result['outcome'], 'unknown')
                self.assertEqual(result['details']['facts'][0]['reason'], reason)
                self.fact['source'][key] = old

    def test_value_must_be_a_complete_token_in_the_quote(self):
        # Exact quotes on the real pages; only a fragment of another token matches each value.
        cases = [('CAN', '6', 4, 'CANL          6'),
                 ('5', '', 2, '4.5    -             5.5')]
        for value, pin, page, quote in cases:
            with self.subTest(value=value):
                self.fact.update(value=value, pin=pin or '6',
                                 kind='pin_function' if pin else 'supply_voltage', unit='' if pin else 'V')
                self.fact['source'].update(page=page, quote=quote)
                result = self.run_fact()
                self.assertEqual(result['execution'], 'unsupported', result)
                self.assertEqual(result['details']['facts'][0]['reason'], 'value_absent_from_quote')
        self.fact.update(value='5.5', kind='supply_voltage', unit='V')
        self.assertEqual(self.run_fact()['execution'], 'ok')

    def test_value_token_boundaries(self):
        from agent_ee.datasheet_facts import value_in_quote
        for value, unit, quote, expected in [
                ('5', 'V', 'max 5 V', True), ('5', 'V', 'max 5V', True), ('5', 'V', 'max 5.', True),
                ('5', 'V', '+15 V', False), ('5', 'V', '4.5 V', False), ('5', 'V', '5.5 V', False),
                ('1', 'A', '1,000 A', False), ('5', 'V', '5k', False), ('CAN', '', 'CANL 6', False),
                ('CAN', '', 'CAN bus', True), ('-0.3', 'V', '-0.3 to +6 V', True)]:
            with self.subTest(value=value, quote=quote):
                self.assertIs(value_in_quote(value, unit, quote), expected)

    def test_native_swapped_pin_defect_with_clean_erc_drc(self):
        require_toolchain(native=True)
        for fixture, expected in [('control', 'pass'), ('swapped', 'defect')]:
            with self.subTest(fixture=fixture):
                self.run_fact(fixture)
                results = {r['id']: r for r in self.results}
                for cid in ('erc', 'drc'):
                    self.assertEqual((results[cid]['execution'], results[cid]['outcome']),
                                     ('ok', 'pass'), results[cid])
                self.assertEqual(results['pin-map']['execution'], 'ok', results['pin-map'])
                self.assertEqual(results['pin-map']['outcome'], expected)
                if fixture == 'swapped':
                    mismatch = results['pin-map']['details']['mismatches'][0]
                    self.assertEqual((mismatch['reference'], mismatch['pin'], mismatch['native_name']),
                                     ('U1', '6', 'CANH'))
                    from agent_ee.findings import collect_findings
                    finding = collect_findings([], [results['pin-map']])[0]
                    self.assertIn('U1, pin 6', finding['location'])
                    self.assertTrue(any('U1, pin 6' in e['locator'] for e in finding['evidence']))
                    from agent_ee.reviewers import build_packet
                    packet = build_packet(self.run, self.results, 'native-pin-mismatch')
                    artifact = next(a for a in packet['artifacts'] if a['path'] == 'work/pin-map/pin-map.json')
                    self.assertIn('CANH', artifact['content'])
                    self.assertEqual(packet['checks'][-1]['evidence'], results['pin-map']['evidence'])
                    self.assertEqual(sum(a['path'] == artifact['path'] for a in packet['artifacts']), 1)


    def test_consumer_of_unverified_fact_is_unsupported(self):
        require_toolchain(native=True)
        self.fact['source']['sha256'] = '0' * 64
        self.run_fact('swapped')
        result = self.results[-1]
        self.assertEqual(result['execution'], 'unsupported', result)
        self.assertEqual(result['outcome'], 'unknown')
        self.assertIn('unverified', result['summary'])

    def test_actual_blank_pdf_page_is_unsupported(self):
        # Original one-page PDF, no text objects. Exercise installed Poppler, no fake extraction.
        objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
                   b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
                   b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Resources << >> >>']
        data = b'%PDF-1.4\n'
        offsets = [0]
        for index, obj in enumerate(objects, 1):
            offsets.append(len(data))
            data += f'{index} 0 obj\n'.encode() + obj + b'\nendobj\n'
        start = len(data)
        data += b'xref\n0 4\n0000000000 65535 f \n'
        for offset in offsets[1:]:
            data += f'{offset:010d} 00000 n \n'.encode()
        data += f'trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n'.encode()
        self.pdf = self.root / 'blank.pdf'
        self.pdf.write_bytes(data)
        self.fact['source'].update(sha256=digest(self.pdf), page=1)
        result = self.run_fact()
        self.assertEqual(result['execution'], 'unsupported', result)
        self.assertEqual(result['details']['facts'][0]['reason'], 'page_has_no_text')

    def test_baseline_and_seal_protect_fact_and_pdf_dependencies(self):
        from agent_ee.campaign import mutable_inputs
        require_toolchain(native=True)
        self.run_fact('swapped')
        scope = self.scope
        scope['schema'] = 'agent-ee.scope/2'
        scope['baseline'] = {
            'requirements': [{'id': 'pins', 'statement': 'Declared pins match applicable data',
                              'source': {'path': 'facts.json', 'locator': 'fixture applicability'},
                              'checks': [s['id'] for s in scope['checks']]}],
            'targets': ['project/pins.kicad_pro'], 'reference_architectures': ['project/pins.kicad_sym']}
        write_json(self.root / 'scope.json', scope)
        run = self.root / 'baseline-run'
        run.mkdir()
        manifest = capture(self.root / 'scope.json', run)
        baseline = load_baseline(run)
        self.assertTrue({'facts.json', 'TJA1051.pdf'} <= baseline['files'].keys())
        for rel in ('facts.json', 'TJA1051.pdf'):
            manifest['scope']['mutable_inputs'] = [rel]
            with self.assertRaisesRegex(ValueError, 'immutable baseline'):
                mutable_inputs(manifest, baseline)
        checks = run_checks(run, time.monotonic() + 60)
        freeze_evidence(run, checks)
        verify_evidence(run, checks, required=True)
        (run / 'work/facts/page-0/page.txt').write_text('tampered extraction')
        with self.assertRaisesRegex(ValueError, 'evidence seal'):
            verify_evidence(run, checks, required=True)

    def test_verify_detects_extractor_identity_change(self):
        require_toolchain(native=True)
        self.run_fact('control')
        out = self.root / 'cli-run'
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_review(self.root / 'scope.json', out, 60, checks_only=True), 1)
        result = verify(out)
        self.assertEqual(result['checks'][-1]['outcome'], 'pass')
        # Simulated installed binary replacement; extraction above used real Poppler.
        with patch('agent_ee.datasheet_facts.digest', return_value='0' * 64):
            with self.assertRaisesRegex(ValueError, 'pdftotext hash changed'):
                verify(out)

    def test_extractor_hash_and_version_mismatch_are_not_verified(self):
        self.run_fact()
        for key, value, message in [('sha256', '0' * 64, 'hash changed'),
                                    ('version', 'pdftotext version 0', 'version differs')]:
            with self.subTest(key=key):
                scope = copy.deepcopy(self.scope)
                scope['checks'][0]['params']['extractor'][key] = value
                write_json(self.root / 'scope.json', scope)
                run = self.root / ('bad-' + key)
                run.mkdir()
                capture(self.root / 'scope.json', run)
                result = run_checks(run, time.monotonic() + 60)[0]
                self.assertNotEqual(result['execution'], 'ok', result)
                self.assertIn(message, result['summary'])

    def test_consumer_requires_engineer_applicability_and_native_metadata(self):
        import xml.etree.ElementTree as ET
        from agent_ee.checks import Ctx, CheckError
        from agent_ee.datasheet_facts import check_pin_map
        require_toolchain(native=True)
        self.run_fact('control')
        manifest = read_json(self.run / 'candidate.json')
        spec = manifest['scope']['checks'][-1]
        params = copy.deepcopy(spec['params'])
        cases = [('symbols', [], 'malformed'),
                 ('facts_check', 'missing', 'unsupported'),
                 ('netlist_check', 'missing', 'unsupported'),
                 ('symbols', [{'reference': 'U1', 'part_number': 'OTHER', 'facts': ['canl']}], 'unsupported'),
                 ('symbols', [{'reference': 'U1', 'part_number': 'TJA1051T', 'facts': ['missing']}], 'unsupported')]
        for key, value, execution in cases:
            with self.subTest(key=key, value=value):
                spec['params'] = {**params, key: value}
                with self.assertRaises(CheckError) as raised:
                    check_pin_map(Ctx(self.run, manifest, spec, time.monotonic() + 60))
                self.assertEqual(raised.exception.execution, execution)
        spec['params'] = params
        # Damage an actual native output for a parser-boundary test, not native evidence.
        xml_path = self.run / 'work/netlist/design.xml'
        xml = ET.parse(xml_path)
        pin = xml.getroot().find('./libparts/libpart/pins/pin[@num="6"]')
        del pin.attrib['name']
        xml.write(xml_path)
        with self.assertRaisesRegex(CheckError, 'metadata missing') as raised:
            check_pin_map(Ctx(self.run, manifest, spec, time.monotonic() + 60))
        self.assertEqual(raised.exception.execution, 'unsupported')
