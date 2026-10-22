import copy
from decimal import Decimal
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_ee import datasheet_footprint as df
from agent_ee.datasheet_facts import load_facts
from agent_ee.harnesses import evidence_mounts
from agent_ee.storage import read_json, write_json
from test_datasheet_facts import text_pdf
from toolchain import enable_visuals_for_module, require_toolchain

PART = 'AEX-T3'
# Original fictitious land-pattern page; no vendor datasheet text.
LINES = ['AEX-T3 recommended land pattern, dimensions in mm',
         'Pad width A 0.60', 'Pad length B 1.00', 'Pad pitch e 0.95', 'Row spacing C 2.20',
         'Tab width D 2.00', 'Tab length E 0.80', 'Locating hole H 1.00',
         'Typical application: 1 uF at IN and at OUT to GND']
QUOTES = {'A': ('Pad width A 0.60', '0.60'), 'B': ('Pad length B 1.00', '1.00'),
          'e': ('Pad pitch e 0.95', '0.95'), 'C': ('Row spacing C 2.20', '2.20'),
          'D': ('Tab width D 2.00', '2.00'), 'E': ('Tab length E 0.80', '0.80'),
          'H': ('Locating hole H 1.00', '1.00')}
DEFINITION = {'harness': 'claude', 'model': 'claude-opus-5-5', 'effort': 'high', 'timeout_s': 600}


def cite(fact, scale='1'):
    return {'fact': fact, 'scale': scale}


def pad(number, name, x, y, w, h, cites, kind='smd', shape='rect', hole=None):
    full = dict.fromkeys(df.FIELDS)
    full.update(cites)
    return {'number': number, 'name': name, 'kind': kind, 'shape': shape, 'x': x, 'y': y, 'w': w, 'h': h,
            'hole': hole, 'cite': full}

setUpModule = enable_visuals_for_module


class DatasheetFootprintTests(unittest.TestCase):
    def setUp(self):
        require_toolchain(tools=('pdftotext', 'pdftoppm', 'kicad-cli'))
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.pdf = self.root / 'source.pdf'
        text_pdf(self.pdf, LINES)
        self.sha = hashlib.sha256(self.pdf.read_bytes()).hexdigest()
        version = subprocess.run(['/usr/bin/pdftotext', '-v'], capture_output=True, text=True,
                                 check=True).stderr.splitlines()[0]
        kicad = subprocess.run(['kicad-cli', 'version'], capture_output=True, text=True, check=True).stdout.strip()
        self.request = {
            'schema': df.REQUEST, 'part_number': PART, 'library': 'AgentEE_Datasheet', 'footprint': 'AEX-T3',
            'pdf': {'path': 'datasheets/aex-t3.pdf', 'sha256': self.sha},
            'extractor': {'path': '/usr/bin/pdftotext', 'sha256': hashlib.sha256(
                Path('/usr/bin/pdftotext').read_bytes()).hexdigest(), 'version': version},
            'kicad_version': kicad, 'extractors': {'extraction-a': DEFINITION, 'extraction-b': dict(DEFINITION)}}

    def fact(self, key, prefix=''):
        quote, value = QUOTES[key]
        return {'id': prefix + key, 'part_number': PART, 'kind': 'dimension', 'value': value, 'unit': 'mm',
                'conditions': 'recommended land pattern',
                'source': {'path': self.request['pdf']['path'], 'sha256': self.sha, 'page': 1,
                           'locator': 'land pattern table', 'quote': quote}}

    def extraction(self, prefix='', refs=('U1', 'C1', 'C2')):
        f = lambda key: prefix + key
        u, cin, cout = refs
        pads = [pad(str(n), name, x, '1.1', '0.60', '1.00',
                    {'x': cite(f('e'), s) if s else None, 'y': cite(f('C'), '1/2'), 'w': cite(f('A')), 'h': cite(f('B'))})
                for n, name, x, s in ((1, 'IN', '-0.95', '-1'), (2, 'GND', '0', None), (3, 'OUT', '0.95', '1'))]
        pads.append(pad('4', 'GND', '0', '-1.1', '2.00', '0.80',
                        {'y': cite(f('C'), '-1/2'), 'w': cite(f('D')), 'h': cite(f('E'))}))
        pads.append(pad('', '', '0', '0', '1.00', '1.00', {'w': cite(f('H')), 'h': cite(f('H')),
                        'hole_w': cite(f('H')), 'hole_h': cite(f('H'))}, kind='np_thru_hole', shape='circle',
                        hole={'shape': 'circle', 'w': '1.00', 'h': '1.00'}))
        circuit = {'components': [{'ref': u, 'type': 'device', 'value': PART},
                                  {'ref': cin, 'type': 'capacitor', 'value': '1u'},
                                  {'ref': cout, 'type': 'capacitor', 'value': '1u'}],
                   'nets': [{'name': 'VIN', 'pins': [f'{u}.1', f'{cin}.1']},
                            {'name': 'VOUT', 'pins': [f'{u}.3', f'{cout}.1']},
                            {'name': 'GND', 'pins': [f'{u}.2', f'{u}.4', f'{cin}.2', f'{cout}.2']}]}
        return {'schema': df.EXTRACTION, 'part_number': PART, 'pdf_sha256': self.sha, 'pads': pads,
                'circuit': circuit, 'facts': [self.fact(k, prefix) for k in QUOTES], 'limitations': []}

    def run_pair(self, a, b, run='run', failures=None):
        values = {'extraction-a': a, 'extraction-b': b}
        failures = failures or {}
        calls = []

        def dispatch(run_dir, label, definition, prompt, schema, timeout, **kwargs):
            calls.append(label)
            run_dir = Path(run_dir)
            # Isolation: only the captured PDF is mounted; no other extraction is visible.
            self.assertEqual([p.relative_to(run_dir / 'inputs').as_posix()
                              for p in (run_dir / 'inputs').rglob('*') if p.is_file()], [self.request['pdf']['path']])
            self.assertEqual(evidence_mounts(run_dir, label), [])
            self.assertIn('/candidate/' + self.request['pdf']['path'], prompt)
            self.assertIs(schema, df.EXTRACTION_SCHEMA)
            if label in failures:
                failure = failures[label]
                if isinstance(failure, Exception):
                    raise failure
                if failure == 'sandbox-timeout':
                    # The real executor's timeout vocabulary, from a process that outlives its limit.
                    from agent_ee.executor import execute
                    return execute(run_dir / 'inputs', run_dir / 'review-work' / label, ['sleep', '5'], timeout=0.5), None
                return {'execution': failure, 'error': 'recorded failure'}, None
            return {'execution': 'ok', 'backend': 'claude'}, copy.deepcopy(values[label])

        with patch('agent_ee.reviewers.dispatch', side_effect=dispatch):
            df.run_extractions(self.request, self.pdf, self.root / run)
        self.assertEqual(calls, list(df.LABELS))
        return self.root / run

    def accept(self, run, out='out'):
        def forbidden(*args, **kwargs):
            raise AssertionError('acceptance must not call a model')
        with patch('agent_ee.reviewers.dispatch', side_effect=forbidden), \
                patch('agent_ee.reviewers.claude_call', side_effect=forbidden):
            return df.accept(run, self.root / out)

    def assert_unpublished(self, report, out='out'):
        self.assertFalse(report['published'])
        self.assertFalse((self.root / out / 'library').exists())
        self.assertFalse((self.root / out / '.library-staging').exists())
        self.assertEqual(read_json(self.root / out / 'agreement.json'), report)

    def test_agreeing_extractions_publish_loadable_cited_footprint_and_symbol(self):
        run = self.run_pair(self.extraction(), self.extraction('b-', refs=('U7', 'C9', 'C5')))
        report = self.accept(run)
        self.assertEqual((report['execution'], report['decision'], report['published']), ('ok', 'accepted', True),
                         report['reason'])
        self.assertEqual(report['differences'], [])
        self.assertEqual(report['coverage_gaps'], [])
        self.assertTrue(report['facts'] and all(f['status'] == 'verified' for f in report['facts']))
        self.assertEqual(report['kicad']['version'], self.request['kicad_version'])
        self.assertEqual(report['kicad']['reextracted_pads']['pads'], 4)
        self.assertEqual(report['assignment']['execution'], 'ok')
        self.assertEqual(report['assignment']['outcome'], 'pass')
        self.assertEqual(report['assignment']['pin_numbers'], [['1', '2', '3', '4']])
        self.assertEqual(report['circuit']['reference_mapping'], {'C1': 'C9', 'C2': 'C5', 'U1': 'U7'})
        library = self.root / 'out/library'
        for rel, sha in report['library']['files'].items():
            self.assertEqual(hashlib.sha256((library / rel).read_bytes()).hexdigest(), sha)
        self.assertEqual(sorted(report['library']['files']), [
            'AgentEE_Datasheet.kicad_sym', 'AgentEE_Datasheet.pretty/AEX-T3.kicad_mod', 'facts.json',
            'footprint-libraries.json', 'pad-table.json', 'reference-circuit.json'])
        footprint = (library / 'AgentEE_Datasheet.pretty/AEX-T3.kicad_mod').read_text()
        self.assertIn('(pad "1" smd rect (at -0.95 1.1) (size 0.6 1) (layers "F.Cu" "F.Paste" "F.Mask") (uuid ', footprint)
        self.assertIn('(pad "" np_thru_hole circle (at 0 0) (size 1 1) (drill 1) (layers "*.Cu" "*.Mask") (uuid ', footprint)
        # Pads span x -1.25..1.25, y -1.5..1.6; 0.25 mm clearance on the 0.01 mm grid.
        self.assertIn('(fp_rect (start -1.5 -1.75) (end 1.5 1.85)', footprint)
        # Every published pad value cites both extractions' verified facts; the facts file is shared-format.
        table = read_json(library / 'pad-table.json')
        self.assertEqual(table['pads'][0]['cite']['extraction-b']['w'], {'fact': 'b-A', 'scale': '1'})
        (library / self.request['pdf']['path']).parent.mkdir(parents=True)
        (library / self.request['pdf']['path']).write_bytes(self.pdf.read_bytes())
        self.assertEqual(len(load_facts(library, 'facts.json')), 14)
        pages = report['pages']
        self.assertEqual(pages['renderer']['dpi'], 200)
        self.assertEqual([p['page'] for p in pages['pages']], [1])
        self.assertTrue((self.root / 'out' / pages['pages'][0]['path']).read_bytes().startswith(b'\x89PNG'))

    def test_replay_is_deterministic_and_makes_no_model_calls(self):
        run = self.run_pair(self.extraction(), self.extraction('b-'))
        first, second = self.accept(run, 'one'), self.accept(run, 'two')
        self.assertTrue(first['published'])
        self.assertEqual(first, second)
        for rel in first['library']['files']:
            self.assertEqual((self.root / 'one/library' / rel).read_bytes(), (self.root / 'two/library' / rel).read_bytes())

    def test_disagreement_beyond_tolerance_publishes_nothing_and_lists_every_difference(self):
        b = self.extraction('b-')
        b['pads'][0]['w'] = '0.62'
        b['pads'][3]['y'] = '-1.15'
        b['pads'][1]['name'] = 'VSS'
        b['circuit']['nets'][0]['pins'].append('C2.1')
        b['circuit']['nets'][1]['pins'].remove('C2.1')
        report = self.accept(self.run_pair(self.extraction(), b))
        self.assertEqual((report['execution'], report['decision']), ('ok', 'rejected'))
        self.assert_unpublished(report)
        found = sorted((d['code'], d.get('number'), d.get('field')) for d in report['differences'])
        self.assertEqual(found, [('circuit_nets', None, None), ('mismatch', '2', 'name'),
                                 ('out_of_tolerance', '1', 'w'), ('out_of_tolerance', '4', 'y')])
        width = next(d for d in report['differences'] if d.get('field') == 'w')
        self.assertEqual((width['a'], width['b'], width['delta_mm']), ('0.6', '0.62', '0.02'))
        self.assertFalse((self.root / 'out/work').exists())

    def test_tolerance_boundary_is_inclusive_at_one_hundredth_millimetre(self):
        a = df.validate_extraction(self.extraction(), self.request)
        for delta, agreed in (('0.01', True), ('0.0101', False)):
            b = self.extraction()
            b['pads'][0]['x'] = format(-(Decimal('0.95') + Decimal(delta)), 'f')
            differences, _ = df.compare(a, df.validate_extraction(b, self.request))
            self.assertEqual(differences == [], agreed, differences)

    def test_structural_differences_are_diagnosed(self):
        a = df.validate_extraction(self.extraction(), self.request)
        b = self.extraction()
        b['pads'][0]['kind'] = 'thru_hole'
        b['pads'][0]['shape'] = 'circle'
        b['pads'][0]['w'] = b['pads'][0]['h'] = '1.00'
        b['pads'][0]['hole'] = {'shape': 'circle', 'w': '0.50', 'h': '0.50'}
        del b['pads'][3]
        b['circuit'] = None
        diffs, _ = df.compare(a, df.validate_extraction(b, self.request))
        codes = sorted((d['code'], d.get('field')) for d in diffs)
        self.assertIn(('pad_count', None), codes)
        self.assertIn(('mismatch', 'kind'), codes)
        self.assertIn(('mismatch', 'hole'), codes)
        self.assertIn(('circuit_presence', None), codes)
        c = self.extraction()
        c['circuit']['components'][1]['value'] = '10u'
        diffs, _ = df.compare(a, df.validate_extraction(c, self.request))
        self.assertEqual([d['code'] for d in diffs], ['circuit_components'])

    def test_missing_or_unverified_fact_is_missing_coverage(self):
        a = self.extraction()
        a['pads'][3]['cite']['h'] = None
        b = self.extraction('b-')
        b['facts'][0]['source']['quote'] = 'Pad width A 0.60 typical'
        # A quote that cuts a printed token is no support: 0.9 is not printed, 0.95 is.
        b['facts'][2]['value'] = '0.9'
        b['facts'][2]['source']['quote'] = 'Pad pitch e 0.9'
        b['pads'][0]['cite']['x'] = cite('b-e', '-19/18')
        b['pads'][2]['cite']['x'] = cite('b-e', '19/18')
        b['pads'][1]['cite']['x'] = cite('b-e', '1')
        report = self.accept(self.run_pair(a, b))
        self.assertEqual((report['execution'], report['decision']), ('unsupported', 'unknown'))
        self.assert_unpublished(report)
        gaps = sorted((g['extraction'], g['number'], g['field'], g['reason']) for g in report['coverage_gaps'])
        self.assertIn(('extraction-a', '4', 'h', 'missing_fact'), gaps)
        self.assertIn(('extraction-b', '1', 'w', 'fact_unverified'), gaps)
        self.assertIn(('extraction-b', '2', 'x', 'value_differs_from_fact'), gaps)
        self.assertIn(('extraction-b', '1', 'x', 'fact_unverified'), gaps)
        statuses = {f['id']: f['reason'] for f in report['facts'] if f['extraction'] == 'extraction-b'}
        self.assertEqual(statuses['b-A'], 'quote_absent_from_page')
        self.assertEqual(statuses['b-e'], 'quote_truncates_a_page_token')

    def test_dimension_must_equal_fact_not_a_scaled_fact(self):
        a = self.extraction()
        a['pads'][3]['w'] = '1.90'
        a['pads'][3]['cite']['w'] = cite('e', '2')
        b = copy.deepcopy(a)
        report = self.accept(self.run_pair(a, b))
        self.assertEqual(report['execution'], 'unsupported')
        self.assertEqual({(g['number'], g['field'], g['reason']) for g in report['coverage_gaps']},
                         {('4', 'w', 'dimension_must_equal_fact')})

    def test_contrived_rational_coordinate_is_uncited_despite_verified_fact(self):
        a = self.extraction()
        a['pads'][0]['x'] = '-0.913'
        a['pads'][0]['cite']['x'] = cite('e', '-913/950')
        report = self.accept(self.run_pair(a, copy.deepcopy(a)))
        self.assertEqual((report['execution'], report['decision']), ('unsupported', 'unknown'))
        self.assert_unpublished(report)
        self.assertTrue(all(f['status'] == 'verified' for f in report['facts']))
        self.assertEqual({(g['number'], g['field'], g['reason']) for g in report['coverage_gaps']},
                         {('1', 'x', 'unsupported_coordinate_scale'), ('3', 'x', 'unsupported_coordinate_scale')})

    def test_coordinate_scales_require_the_fact_role_and_centered_pad_index(self):
        for fact, scale, x in (('A', '-1', '-0.60'), ('C', '-1/2', '-1.10'),
                               ('e', '-2', '-1.90'), ('e', '-1/2', '-0.475')):
            with self.subTest(fact=fact, scale=scale):
                a = self.extraction()
                a['pads'][0]['x'] = x
                a['pads'][0]['cite']['x'] = cite(fact, scale)
                tag = fact + scale.replace('/', '_')
                report = self.accept(self.run_pair(a, copy.deepcopy(a), run='run-' + tag), 'out-' + tag)
                self.assertEqual((report['execution'], report['decision']), ('unsupported', 'unknown'))
                self.assert_unpublished(report, 'out-' + tag)
                self.assertTrue(all(f['status'] == 'verified' for f in report['facts']))
                self.assertIn(('1', 'x', 'unsupported_coordinate_scale'),
                              {(g['number'], g['field'], g['reason']) for g in report['coverage_gaps']})

    def test_span_only_allows_signed_half_not_arbitrary_or_whole_multiples(self):
        for scale, y in (('3/2', '3.30'), ('1', '2.20'), ('7/11', '1.40')):
            with self.subTest(scale=scale):
                a = self.extraction()
                for p in a['pads'][:3]:
                    p['y'] = y
                    p['cite']['y'] = cite('C', scale)
                tag = scale.replace('/', '_')
                report = self.accept(self.run_pair(a, copy.deepcopy(a), run='run-' + tag), 'out-' + tag)
                self.assertEqual((report['execution'], report['decision']), ('unsupported', 'unknown'))
                self.assert_unpublished(report, 'out-' + tag)
                self.assertEqual({(g['number'], g['field'], g['reason']) for g in report['coverage_gaps']},
                                 {(str(n), 'y', 'unsupported_coordinate_scale') for n in (1, 2, 3)})

    def test_integer_and_half_integer_pitch_indices_publish_independent_of_pad_order(self):
        for count in (4, 5):
            with self.subTest(count=count):
                a = self.extraction()
                a['circuit'] = None
                a['pads'] = []
                for i in range(count):
                    numerator = 2 * i - (count - 1)
                    scale = str(numerator // 2) if numerator % 2 == 0 else f'{numerator}/2'
                    x = format(Decimal('0.95') * Decimal(numerator) / 2, 'f')
                    a['pads'].append(pad(str(10 + i), 'P' + str(i), x, '1.10', '0.60', '1.00',
                                         {'x': cite('e', scale) if numerator else None,
                                          'y': cite('C', '1/2'), 'w': cite('A'), 'h': cite('B')}))
                b = copy.deepcopy(a)
                b['pads'].reverse()
                tag = str(count)
                report = self.accept(self.run_pair(a, b, run='run-' + tag), 'out-' + tag)
                self.assertEqual((report['execution'], report['decision'], report['published']),
                                 ('ok', 'accepted', True), report['reason'])
                self.assertEqual(report['coverage_gaps'], [])
                self.assertTrue(all(f['status'] == 'verified' for f in report['facts']))

    def test_vertical_pitch_and_column_span_publish(self):
        text_pdf(self.pdf, [line.replace('Row spacing', 'Column span') for line in LINES])
        self.sha = hashlib.sha256(self.pdf.read_bytes()).hexdigest()
        self.request['pdf']['sha256'] = self.sha
        a = self.extraction()
        for fact in a['facts']:
            fact['source']['quote'] = fact['source']['quote'].replace('Row spacing', 'Column span')
        for p in a['pads']:
            p['x'], p['y'] = p['y'], p['x']
            p['cite']['x'], p['cite']['y'] = p['cite']['y'], p['cite']['x']
        report = self.accept(self.run_pair(a, copy.deepcopy(a)))
        self.assertEqual((report['execution'], report['decision'], report['published']),
                         ('ok', 'accepted', True), report['reason'])
        self.assertEqual(report['coverage_gaps'], [])

    def test_multidimension_quote_and_metadata_cannot_supply_another_facts_role(self):
        a = self.extraction()
        a['facts'][0]['source']['quote'] = '\n'.join(LINES[1:5])
        a['facts'][0]['source']['locator'] = 'Pad pitch'
        a['facts'][0]['conditions'] = 'pitch'
        a['pads'][0]['x'] = '-0.60'
        a['pads'][0]['cite']['x'] = cite('A', '-1')
        report = self.accept(self.run_pair(a, copy.deepcopy(a)))
        self.assertEqual((report['execution'], report['decision']), ('unsupported', 'unknown'))
        self.assert_unpublished(report)
        self.assertTrue(all(f['status'] == 'verified' for f in report['facts']))
        self.assertIn(('1', 'x', 'unsupported_coordinate_scale'),
                      {(g['number'], g['field'], g['reason']) for g in report['coverage_gaps']})

    def test_agent_failure_or_timeout_is_failed_and_never_accepted_alone(self):
        for failure in ('sandbox-timeout', 'timeout', 'failed', 'unavailable', OSError('harness vanished')):
            with self.subTest(failure=failure):
                tag = type(failure).__name__ if isinstance(failure, Exception) else failure
                run = self.run_pair(self.extraction(), self.extraction('b-'), run='run-' + tag,
                                    failures={'extraction-b': failure})
                retained = read_json(run / 'extractions/extraction-b.json')
                self.assertIsNone(retained['value'])
                if failure == 'sandbox-timeout':
                    self.assertEqual(retained['call']['execution'], 'timeout')
                report = self.accept(run, 'out-' + tag)
                self.assertEqual((report['execution'], report['decision']), ('failed', 'unknown'))
                self.assertIn('never accepted', report['reason'])
                self.assert_unpublished(report, 'out-' + tag)
                self.assertEqual(report['extractions']['extraction-a']['execution'], 'ok')

    def test_missing_or_substituted_second_extraction_is_refused(self):
        run = self.run_pair(self.extraction(), self.extraction('b-'))
        (run / 'extractions/extraction-b.json').unlink()
        report = self.accept(run, 'missing')
        self.assertEqual(report['execution'], 'failed')
        self.assert_unpublished(report, 'missing')
        copied = read_json(run / 'extractions/extraction-a.json')
        write_json(run / 'extractions/extraction-b.json', copied)
        report = self.accept(run, 'copied')
        self.assertEqual(report['execution'], 'malformed')
        self.assert_unpublished(report, 'copied')
        copied['label'] = 'extraction-b'
        copied['call'] = {'execution': 'ok'}
        copied['value'] = None
        write_json(run / 'extractions/extraction-b.json', copied)
        report = self.accept(run, 'empty')
        self.assertEqual(report['execution'], 'failed')

    def test_changed_pdf_or_request_is_refused_on_replay(self):
        run = self.run_pair(self.extraction(), self.extraction('b-'))
        pdf = run / 'inputs' / self.request['pdf']['path']
        pdf.write_bytes(pdf.read_bytes() + b'\n')
        report = self.accept(run, 'pdf')
        self.assertEqual(report['execution'], 'failed')
        self.assert_unpublished(report, 'pdf')
        pdf.write_bytes(self.pdf.read_bytes())
        request = read_json(run / 'request.json')
        request['footprint'] = 'OTHER'
        write_json(run / 'request.json', request)
        report = self.accept(run, 'request')
        self.assertEqual(report['execution'], 'malformed')
        self.assert_unpublished(report, 'request')

    def test_malformed_extraction_value_is_malformed(self):
        bad = self.extraction('b-')
        bad['pads'][0]['w'] = 0.6
        report = self.accept(self.run_pair(self.extraction(), bad))
        self.assertEqual((report['execution'], report['extractions']['extraction-b']['execution']),
                         ('malformed', 'malformed'))
        self.assert_unpublished(report)
        for mutate, message in (
                (lambda e: e['pads'][0].update(number=''), 'unnumbered'),
                (lambda e: e['pads'][3].update(number='2', name='VSS'), 'different names'),
                (lambda e: e['pads'][0].update(hole={'shape': 'circle', 'w': '0.3', 'h': '0.3'}), 'hole'),
                (lambda e: e['circuit']['nets'][0]['pins'].append('U1.9'), 'electrical pad'),
                (lambda e: e['circuit']['nets'][0]['pins'].append('U1.3'), 'more than one net'),
                (lambda e: e.update(pdf_sha256='0' * 64), 'different part')):
            value = self.extraction()
            mutate(value)
            with self.subTest(message=message), self.assertRaisesRegex(df.ExtractionError, message):
                df.validate_extraction(value, self.request)

    def test_request_requires_two_qualified_extractors(self):
        for mutate in (lambda r: r['extractors'].pop('extraction-b'),
                       lambda r: r['extractors']['extraction-a'].update(tool_mode='fixed_packet'),
                       lambda r: r['extractors']['extraction-a'].update(model=''),
                       lambda r: r['extractors']['extraction-b'].update(timeout_s=0),
                       lambda r: r.update(library='bad name'),
                       lambda r: r['pdf'].update(path='../x.pdf')):
            request = copy.deepcopy(self.request)
            mutate(request)
            with self.assertRaises(df.ExtractionError):
                df.validate_request(request)
        other = self.root / 'other.pdf'
        text_pdf(other, ['different bytes'])
        with self.assertRaisesRegex(df.ExtractionError, 'differ'):
            df.run_extractions(self.request, other, self.root / 'x')
        self.assertFalse((self.root / 'x').exists())

    def test_symbol_pin_parity_is_checked_by_footprint_assignment(self):
        pads = df.validate_extraction(self.extraction(), self.request)['pads']
        staging = self.root / 'staging'
        (staging / 'AgentEE_Datasheet.pretty').mkdir(parents=True)
        footprint = df.emit_footprint(self.request, pads)
        symbol = df.emit_symbol(self.request, [p for p in pads if p['number'] != '4'])
        (staging / 'AgentEE_Datasheet.pretty/AEX-T3.kicad_mod').write_bytes(footprint)
        (staging / 'AgentEE_Datasheet.kicad_sym').write_bytes(symbol)
        _, assignment = df.load_and_parity(self.request, pads, footprint, symbol, staging, self.root / 'work')
        self.assertEqual((assignment['execution'], assignment['outcome']), ('ok', 'defect'))
        self.assertEqual(assignment['findings'][0]['code'], 'pad_pin_parity')
        self.assertEqual(assignment['findings'][0]['extra_pads'], ['4'])

    def test_kicad_rejection_of_generated_footprint_fails_closed(self):
        pads = df.validate_extraction(self.extraction(), self.request)['pads']
        staging = self.root / 'staging'
        (staging / 'AgentEE_Datasheet.pretty').mkdir(parents=True)
        footprint = df.emit_footprint(self.request, pads).replace(b'(pad "1" smd', b'(pad "1" (smd')
        (staging / 'AgentEE_Datasheet.pretty/AEX-T3.kicad_mod').write_bytes(footprint)
        symbol = df.emit_symbol(self.request, pads)
        (staging / 'AgentEE_Datasheet.kicad_sym').write_bytes(symbol)
        with self.assertRaises(df.ExtractionError):
            df.load_and_parity(self.request, pads, footprint, symbol, staging, self.root / 'work')

    def test_through_hole_part_round_trips_through_kicad(self):
        lines = ['AEX-D2 through-hole lead pattern, dimensions in mm', 'Lead pitch F 2.54',
                 'Lead pad P 1.60', 'Lead hole d 0.80']
        text_pdf(self.pdf, lines)
        self.sha = hashlib.sha256(self.pdf.read_bytes()).hexdigest()
        self.request.update(part_number='AEX-D2', footprint='AEX-D2')
        self.request['pdf']['sha256'] = self.sha

        def fact(fid, quote, value):
            return {'id': fid, 'part_number': 'AEX-D2', 'kind': 'dimension', 'value': value, 'unit': 'mm',
                    'conditions': 'lead pattern', 'source': {'path': self.request['pdf']['path'], 'sha256': self.sha,
                                                             'page': 1, 'locator': 'table', 'quote': quote}}
        facts = [fact('F', 'Lead pitch F 2.54', '2.54'), fact('P', 'Lead pad P 1.60', '1.60'),
                 fact('d', 'Lead hole d 0.80', '0.80')]
        size = {'w': cite('P'), 'h': cite('P'), 'hole_w': cite('d'), 'hole_h': cite('d')}
        pads = [pad('1', 'A', '-1.27', '0', '1.60', '1.60', {**size, 'x': cite('F', '-1/2')}, kind='thru_hole',
                    shape='circle', hole={'shape': 'circle', 'w': '0.80', 'h': '0.80'}),
                pad('2', 'K', '1.27', '0', '1.60', '1.60', {**size, 'x': cite('F', '1/2')}, kind='thru_hole',
                    shape='circle', hole={'shape': 'circle', 'w': '0.80', 'h': '0.80'})]
        value = {'schema': df.EXTRACTION, 'part_number': 'AEX-D2', 'pdf_sha256': self.sha, 'pads': pads,
                 'circuit': None, 'facts': facts, 'limitations': []}
        report = self.accept(self.run_pair(value, copy.deepcopy(value)))
        self.assertTrue(report['published'], report['reason'])
        self.assertIsNone(report['circuit'])
        footprint = (self.root / 'out/library/AgentEE_Datasheet.pretty/AEX-D2.kicad_mod').read_text()
        self.assertIn('(attr through_hole)', footprint)
        self.assertIn('(pad "1" thru_hole circle (at -1.27 0) (size 1.6 1.6) (drill 0.8) (layers "*.Cu" "*.Mask") (uuid ',
                      footprint)

    def test_cli_accept_exit_status(self):
        run = self.run_pair(self.extraction(), self.extraction('b-'))
        with patch('sys.stdout'):
            self.assertEqual(df.main(['accept', str(run), '--out', str(self.root / 'cli')]), 0)
        b = read_json(run / 'extractions/extraction-b.json')
        b['value']['pads'][0]['w'] = '0.70'
        write_json(run / 'extractions/extraction-b.json', b)
        with patch('sys.stdout'):
            self.assertEqual(df.main(['accept', str(run), '--out', str(self.root / 'cli-2')]), 1)
        self.assertEqual(read_json(self.root / 'cli-2/agreement.json')['decision'], 'rejected')
        self.assertEqual(json.loads(json.dumps(df.EXTRACTION_SCHEMA))['properties']['schema']['enum'], [df.EXTRACTION])


if __name__ == '__main__':
    unittest.main()
