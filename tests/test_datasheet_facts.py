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


def text_pdf(path, lines, to_unicode=None):
    """Original one-page Helvetica PDF; to_unicode maps single byte codes to extracted text."""
    content = ''.join(f'BT /F1 10 Tf 20 {180 - 14 * i} Td ({line}) Tj ET\n' for i, line in enumerate(lines))
    content = content.encode('latin-1')
    font = b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica'
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] '
               b'/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
               font + (b' /ToUnicode 6 0 R >>' if to_unicode else b' >>'),
               b'<< /Length %d >>\nstream\n' % len(content) + content + b'\nendstream']
    if to_unicode:
        pairs = ''.join(f'<{ord(code):02X}> <{text.encode("utf-16-be").hex().upper()}>\n'
                        for code, text in to_unicode.items())
        cmap = ('/CIDInit /ProcSet findresource begin 12 dict begin begincmap\n'
                '/CMapName /AgentEE def /CMapType 2 def\n'
                '1 begincodespacerange <00> <FF> endcodespacerange\n'
                f'{len(to_unicode)} beginbfchar\n{pairs}endbfchar\n'
                'endcmap CMapName currentdict /CMap defineresource pop end end\n').encode()
        objects.append(b'<< /Length %d >>\nstream\n' % len(cmap) + cmap + b'\nendstream')
    data = b'%PDF-1.4\n'
    offsets = []
    for index, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += f'{index} 0 obj\n'.encode() + obj + b'\nendobj\n'
    start = len(data)
    data += f'xref\n0 {len(objects) + 1}\n0000000000 65535 f \n'.encode()
    data += b''.join(f'{offset:010d} 00000 n \n'.encode() for offset in offsets)
    data += (f'trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n'
             f'startxref\n{start}\n%%EOF\n').encode()
    path.write_bytes(data)


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

    def run_fact(self, fixture=None, facts=None):
        if facts is None:
            facts = [self.fact]
        if fixture and len(facts) == 1:
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

    def test_quote_must_start_and_end_at_page_token_boundaries(self):
        from agent_ee.datasheet_facts import page_support
        cases = [
            ('Pad pitch e 0.9', 'Pad pitch e 0.95', False),
            ('95 mm', '0.95 mm', False), ('5 V', '-5 V', False),
            ('CAN 6', 'XCAN 6', False), ('CAN', 'CANL', False),
            ('CAN', 'CAN\u0332', False), ('CAN', '\u0332CAN', False),
            ('Pitch 5', 'Pitch 5E+3', False), ('Pitch 5', 'Pitch 5.5', False),
            ('Pitch 5', 'Pitch 5\u00b5A', False),
            ('Pad pitch e 0.95', 'Pad pitch e 0.95', True),
            ('0.95', 'Pitch (0.95) mm', True), ('CAN', 'CAN bus', True),
            ('5', 'Limit 5. V', True), ('-5', 'Limit -5 V', True),
            # A truncated occurrence must not hide a later complete occurrence.
            ('CAN', 'CANL CAN bus', True), ('CAN', 'CANL XCAN', False),
            ('CAN', 'CANL\nCAN', True),
            (' CAN ', 'XCAN CAN CANL', True), ('\nCAN\n', 'XCAN\nCAN\nCANL', True),
            (' 0.9', 'Pitch 0.95', False), ('95 ', '0.95 mm', False),
        ]
        for quote, page, expected in cases:
            with self.subTest(quote=quote, page=page):
                self.assertEqual(page_support(dict(page=1, quote=quote), [page]),
                    ('verified', 'exact_quote_and_value_supported') if expected else
                    ('rejected', 'quote_truncates_a_page_token'))

    def test_actual_pdf_truncated_quote_is_not_verified(self):
        self.pdf = self.root / 'pitch.pdf'
        text_pdf(self.pdf, ['Pad pitch e 0.95', 'XCAN 6'])
        facts = [self.cited('truncated-last', '0.9', 'mm', 'Pad pitch e 0.9'),
                 self.cited('truncated-first', 'CAN', '', 'CAN 6', pin='6'),
                 self.cited('complete', '0.95', 'mm', 'Pad pitch e 0.95'),
                 self.cited('edge-space', '0.95', 'mm', ' e 0.95')]
        result = self.run_fact(facts=facts)
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)
        self.assertEqual(self.outcomes(result), {
            'truncated-last': ('rejected', 'quote_truncates_a_page_token'),
            'truncated-first': ('rejected', 'quote_truncates_a_page_token'),
            'complete': ('verified', 'exact_quote_and_value_supported'),
            'edge-space': ('verified', 'exact_quote_and_value_supported')})
        page = (self.run / 'work/facts/page-0/page.txt').read_text(encoding='utf-8')
        self.assertIn('Pad pitch e 0.95', page)
        freeze_evidence(self.run, self.results)
        verify_evidence(self.run, self.results, required=True)
        self.assertEqual(self.run_fact(facts=[facts[-1]])['execution'], 'ok')

    def test_complete_numeric_and_symbol_lexemes(self):
        from agent_ee.datasheet_facts import value_in_quote
        for value, unit, quote, expected in [
                # Leading decimals, exponents and signs belong to the written literal.
                ('5', 'V', '.5 V', False), ('5', 'V', '1e-5 V', False), ('3', 'V', '5E+3 V', False),
                ('5', 'V', '1e5 V', False), ('5', 'V', '5.e3 V', False), ('5', 'E', '5E+3', False),
                ('5', 'V', '-5 V', False), ('5', 'V', '\u22125 V', False), ('5', 'V', '\u00b15 V', False),
                ('6', 'V', '-0.3 to +6 V', False), ('5', 'V', '\uf02d5 V', False),
                ('5', 'V', '\u20135 V', False), ('10', '', '10\u207b\u2075', False),
                ('5', '', '5\u00d710\u00b3', False), ('10', '', '1\u00d710-5', False),
                # A sign glued between digits is ambiguous (range, difference or flattened exponent).
                ('5.5', 'V', '4.5-5.5 V', False), ('4.5', 'V', '4.5-5.5 V', False),
                ('5', '', '10-5', False), ('10', '', '10-5', False),
                # Exact complete spellings, attached declared units and dash-separated ranges.
                ('.5', 'V', '.5 V', True), ('1e-5', 'V', '1e-5 V', True), ('1e-5', 'V', '1e-5V', True),
                ('5E+3', 'V', '5E+3 V', True), ('+6', 'V', '-0.3 to +6 V', True),
                ('\u22125', 'V', '\u22125 V', True), ('\uf02d0.3', 'V', '\uf02d0.3   +7   V', True),
                ('5.5', 'V', '4.5\u20135.5 V', True), ('4.5', 'V', '4.5\u20135.5 V', True),
                ('4.5-5.5', 'V', '4.5-5.5 V', True), ('5', 'V', '5. V', True), ('5', '\u00b5A', '5\u00b5A', True),
                ('5', 'V', '5\u00b5A', False), ('0.5', 'V', '0.5 V', True), ('5', 'V', '(5 V)', True),
                # Combining marks, format characters and unmapped glyphs continue a token.
                ('CAN', '', 'CAN\u0301', False), ('CAN', '', 'CAN\u0332 bus', False),
                ('CAN', '', '\u0301CAN', False), ('CAN', '', 'CAN\u00adL', False),
                ('CAN', '', 'CAN\uf02d', False), ('CAN', '', 'CAN\ufffd', False),
                ('CAN', '', 'CAN\u00b5', False), ('CAN\u0332', '', 'CAN\u0332 bus', True),
                ('\u0332', '', 'CAN\u0332 bus', False), ('CAN', '', 'CAN-FD', True),
                # Documented lexical limit: slash and space still delimit tokens.
                ('3', '', 'TJA1051T/3', True), ('5', '', '5 \u00d7 10\u00b3', True),
                # Whitespace is not part of a token; an edge-padded value is never a complete token.
                (' 5', 'V', 'max 5 V', False), ('5 ', 'V', 'max 5 V', False)]:
            with self.subTest(value=value, unit=unit, quote=quote):
                self.assertIs(value_in_quote(value, unit, quote), expected)

    def cited(self, fid, value, unit, quote, *, pin=None, page=1):
        fact = copy.deepcopy(self.fact)
        fact.update(id=fid, value=value, unit=unit, kind='pin_function' if pin else 'supply_voltage')
        fact['pin'] = pin or fact['pin']
        fact['source'].update(sha256=digest(self.pdf), page=page, quote=quote, locator=fid)
        return fact

    def outcomes(self, result):
        return {r['id']: (r['status'], r['reason']) for r in result['details']['facts']}

    def test_actual_pdf_numeric_fragments_are_not_verified(self):
        # Original PDF extracted by the installed Poppler inside the sandbox; no fake page text.
        lines = ['.5 V', '1e-5 V', '5E+3 V', '~5 V', 'CANL 1e-5', 'CANL 5E+3', '5.5V']
        self.pdf = self.root / 'numeric.pdf'
        text_pdf(self.pdf, lines, {'~': '\u2212'})
        minus = '\u22125 V'
        absent = [self.cited('leading', '5', 'V', '.5 V'), self.cited('exponent', '5', 'V', '1e-5 V'),
                  self.cited('signed-exponent', '3', 'V', '5E+3 V'), self.cited('minus', '5', 'V', minus)]
        pins = [self.cited('pin-exponent', 'CANL', '', 'CANL 1e-5', pin='5'),
                self.cited('pin-signed-exponent', 'CANL', '', 'CANL 5E+3', pin='3')]
        complete = [self.cited('leading-ok', '.5', 'V', '.5 V'), self.cited('exponent-ok', '1e-5', 'V', '1e-5 V'),
                    self.cited('signed-exponent-ok', '5E+3', 'V', '5E+3 V'),
                    self.cited('minus-ok', '\u22125', 'V', minus),
                    self.cited('attached-ok', '5.5', 'V', '5.5V')]
        result = self.run_fact(facts=absent + pins + complete)
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)
        expected = {f['id']: ('rejected', 'value_absent_from_quote') for f in absent}
        expected.update({f['id']: ('rejected', 'pin_absent_from_quote') for f in pins})
        expected.update({f['id']: ('verified', 'exact_quote_and_value_supported') for f in complete})
        self.assertEqual(self.outcomes(result), expected)
        # Rejected tokens never reach extraction; the minus control's extracted page is retained.
        index = len(absent + pins) + 3
        page = (self.run / f'work/facts/page-{index}/page.txt').read_text(encoding='utf-8')
        self.assertIn(minus, page)
        # Positive controls alone pass; the rejections above are the token rule, not extraction.
        self.assertEqual(self.run_fact(facts=complete)['execution'], 'ok')

    def test_actual_pdf_combining_mark_continues_symbol_token(self):
        # U+0332 has no precomposed form, so Poppler keeps it after N in the extracted page.
        self.pdf = self.root / 'mark.pdf'
        text_pdf(self.pdf, ['CAN~ 6'], {'~': '\u0332'})
        result = self.run_fact(facts=[self.cited('fragment', 'CAN', '', 'CAN\u0332 6', pin='6'),
                                      self.cited('complete', 'CAN\u0332', '', 'CAN\u0332 6', pin='6')])
        self.assertEqual(self.outcomes(result), {
            'fragment': ('rejected', 'value_absent_from_quote'),
            'complete': ('verified', 'exact_quote_and_value_supported')})

    def test_real_tja1051_signed_limits_keep_their_signs(self):
        # Table 5 prints minus with a private-use Symbol glyph; Poppler extracts it as U+F02D.
        quote = 'on any other pin' + ' ' * 63 + '\uf02d0.3          +7             V'
        facts = [self.cited('unsigned-min', '0.3', 'V', quote, page=7),
                 self.cited('unsigned-max', '7', 'V', quote, page=7),
                 self.cited('signed-min', '\uf02d0.3', 'V', quote, page=7),
                 self.cited('signed-max', '+7', 'V', quote, page=7)]
        result = self.run_fact(facts=facts)
        self.assertEqual(self.outcomes(result), {
            'unsigned-min': ('rejected', 'value_absent_from_quote'),
            'unsigned-max': ('rejected', 'value_absent_from_quote'),
            'signed-min': ('verified', 'exact_quote_and_value_supported'),
            'signed-max': ('verified', 'exact_quote_and_value_supported')})

    def test_exponent_fragment_pins_cannot_bless_swapped_symbol(self):
        require_toolchain(native=True)
        # An untrusted author cites an original PDF whose pins occur only inside exponents.
        self.pdf = self.root / 'forged.pdf'
        text_pdf(self.pdf, ['CANH 1e-6', 'CANL 1e-7', 'CANH 6', 'CANL 7'])
        for suffix, expected in [(' 1e-6', 'unsupported'), (' 6', 'ok')]:
            with self.subTest(quote=suffix):
                seven = suffix.replace('6', '7')
                facts = [self.cited('canl', 'CANH', '', 'CANH' + suffix, pin='6'),
                         self.cited('canh', 'CANL', '', 'CANL' + seven, pin='7')]
                self.run_fact('swapped', facts)
                results = {r['id']: r for r in self.results}
                for cid in ('erc', 'drc'):
                    self.assertEqual((results[cid]['execution'], results[cid]['outcome']), ('ok', 'pass'))
                self.assertEqual(results['facts']['execution'], expected, results['facts'])
                freeze_evidence(self.run, self.results)
                verify_evidence(self.run, self.results, required=True)
                if expected == 'unsupported':
                    sealed = read_json(self.run / 'work/facts/facts.json')['facts']
                    self.assertEqual({r['reason'] for r in sealed}, {'pin_absent_from_quote'})
                    self.assertEqual((results['pin-map']['execution'], results['pin-map']['outcome']),
                                     ('unsupported', 'unknown'), results['pin-map'])
                    self.assertNotIn('comparisons', results['pin-map']['details'])
                else:
                    # Complete tokens in the author's own PDF: textual support only, as documented.
                    self.assertEqual(results['pin-map']['outcome'], 'pass', results['pin-map'])

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
