"""Owned synthetic passive rating sources; never real pod parts or vendor PDFs."""
import copy
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
import tempfile
import unittest

from agent_ee.storage import digest, identity, read_json, write_json
from test_approved_parts import named_fixture
from test_datasheet_facts import text_pdf
from test_parts_subset import captured_fixture

MPN = 'OWNED-R0603-10K'
POWER = {'attribute_name_en': 'Power(Watts)', 'attribute_value_name': '100mW'}
SUPPLY = {'attribute_name_en': 'Voltage-Supply(Max)', 'attribute_value_name': '75V'}


def extractor():
    version = subprocess.run(['/usr/bin/pdftotext', '-v'], capture_output=True, text=True,
                             check=True).stderr.splitlines()[0]
    return {'path': '/usr/bin/pdftotext', 'sha256': digest('/usr/bin/pdftotext'), 'version': version}


class PassiveRatingTests(unittest.TestCase):
    def setUp(self):
        from toolchain import require_toolchain
        require_toolchain(tools=('pdftotext', 'bwrap'))
        self.rebuild([POWER, SUPPLY])

    def rebuild(self, attributes):
        """Fresh owned capture whose purchased row carries exactly ``attributes`` (None: JSON null)."""
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.parts = captured_fixture(self, self.root)
        extra = {} if attributes is None else {'attributes': attributes}
        self.record = named_fixture(self.root, self.parts, mpn=MPN, package='0603', **extra)
        self.part = self.parts['parts'][0]
        self.intent = {'schema': 'agent-ee.connectivity-intent/1',
            'libraries': [{'name': 'Owned', 'path': 'Owned.kicad_sym', 'sha256': digest(self.root / 'Owned.kicad_sym')}],
            'components': [{'id': 'pod/R9', 'reference': 'R1', 'lib_id': 'Owned:R',
                            'footprint': 'Owned:R2', 'value': '10k', 'units': [1]}],
            'nets': [], 'no_connects': [{'component': 'pod/R9', 'pin': {'number': p}} for p in ('1', '2')]}
        self.refs = {'schema': 'agent-ee.part-refs/1', 'components': {'pod/R9': {'part_ref': 'resistor'}}}
        self.deviations = {'schema': 'agent-ee.part-deviations/1', 'deviations': []}
        self.ratings = {'schema': 'agent-ee.passive-rating-sources/1', 'catalog': True, 'facts': None,
                        'extractor': None}

    def fill(self, ratings=True):
        from agent_ee.binding_fill import fill
        result = fill(self.root, self.intent, self.parts, self.refs, self.deviations, captures='.',
                      ratings=self.ratings if ratings else None)
        self.last_report = result[1]
        return result

    def select_pdf(self, lines, *, rejected=True, data=None):
        """Pin an owned text PDF as the AGENTEE-237 selected source; the supplier PDF is rejected."""
        from agent_ee.part_record import prepare_record
        url = 'https://example.org/owned-resistor-family.pdf'
        name = hashlib.sha256(url.encode()).hexdigest() + '.pdf'
        (self.root / 'family').mkdir(exist_ok=True)
        text_pdf(self.root / 'family' / name, lines)
        if data is not None:
            (self.root / 'family' / name).write_bytes(data)
        self.selected = {'path': 'family/' + name, 'sha256': digest(self.root / 'family' / name)}
        write_json(self.root / 'family/manifest.json', dict(schema='agent-ee.datasheet-capture/1',
            bindings_sha256=identity([dict(id='family-alias', datasheet_url=url)]), outcome='captured',
            parts=[dict(id='family-alias', url=url, final_url=url, fetched_at='2026-10-18T00:00:00+00:00',
                        sha256=self.selected['sha256'], path=name, missing=False, reason=None)]))
        provenance = dict(path='family/manifest.json', sha256=digest(self.root / 'family/manifest.json'),
                          locator='parts[id=family-alias]')
        write_json(self.root / 'source-selection.json', {'schema': 'agent-ee.datasheet-source-selection/1',
            'parts': {'C38695': {'selected': [dict(id='general-family', sha256=self.selected['sha256'],
                                                   provenance=provenance)],
                                 'rejected': [dict(id='supplier-wrong-family', sha256=self.record['artifact']['sha256'],
                                                   reason='Owned wrong-family fixture')] if rejected else []}}})
        ref = dict(path='source-selection.json', sha256=digest(self.root / 'source-selection.json'),
                   part_key='C38695')
        record = prepare_record(self.root, 'C38695', self.record['catalog'], self.record['datasheets'],
                                self.record['datasheet_id'], source_selection=ref)
        selection = self.part['selection']
        write_json(self.root / selection['part_record'], record)
        selection['part_record_sha256'] = digest(self.root / selection['part_record'])
        self.part['datasheet'].update(artifact=record['artifact'], source_selection=ref, provenance={
            **record['datasheets'], 'locator': 'parts[id=' + record['datasheet_id'] + ']'})
        write_json(self.root / 'parts.json', self.parts)

    def facts(self, *facts):
        write_json(self.root / 'rating-facts.json', {'schema': 'agent-ee.datasheet-facts/1', 'facts': list(facts)})
        self.ratings.update(facts={'path': 'rating-facts.json', 'sha256': digest(self.root / 'rating-facts.json')},
                            extractor=extractor())

    def fact(self, value, unit, quote, *, kind='power_rating', source=None, page=1, fact_id='power'):
        return {'id': fact_id, 'part_number': MPN, 'kind': kind, 'value': value, 'unit': unit,
                'conditions': 'rated at 70 degC ambient', 'source': {**(source or self.selected), 'page': page,
                'locator': 'ratings table', 'quote': quote}}

    def provenance(self, fields, report=None, ref='R1'):
        """Full receipted record for ``ref``, after checking the native field pins it by hash."""
        native = json.loads(fields['AGENT_EE_Ratings'])
        self.assertLessEqual(len(fields['AGENT_EE_Ratings']), 256)
        self.assertEqual(native['ratings'], {k: fields[k] for k in ('Power', 'Voltage') if k in fields})
        entry = (report or self.last_report)['ratings']['derived'][ref]
        self.assertEqual(native['record_sha256'], identity(entry['record']))
        self.assertEqual(entry['record_sha256'], identity(entry['record']))
        return entry['record']

    def test_catalog_attribute_is_bound_with_exact_provenance(self):
        before = copy.deepcopy((self.intent, self.parts, self.ratings))
        intent, report = self.fill()
        fields = intent['components'][0]['part_binding']
        self.assertEqual(fields['Power'], '100mW')
        self.assertNotIn('Voltage', fields, 'Voltage-Supply(Max) is not a declared rating attribute')
        record = self.provenance(fields)
        source = record['ratings']['Power']['sources']
        response = 'named/C38695/responses/C38695.json'
        self.assertEqual(source, [{'kind': 'catalog', 'id': 'C38695', 'path': response,
                                   'sha256': digest(self.root / response), 'attribute': 'Power(Watts)'}])
        self.assertEqual(record['ratings']['Power']['si'], '0.1')
        self.assertEqual(record['part'], {'id': 'resistor', 'lcsc': 'C38695', 'mpn': MPN})
        self.assertIn('effective capacitance', record['limitations'][0])
        self.assertEqual(fields['LCSC'], 'C38695')
        self.assertEqual(fields['Partnumber'], MPN)
        self.assertEqual(report['ratings']['derived']['R1']['record']['ratings']['Power']['value'], '100mW')
        self.assertEqual(report['ratings']['missing'], {})
        self.assertEqual(before, (self.intent, self.parts, self.ratings))
        # Opt-in only: the unchanged producer writes no rating field.
        unrated, unrated_report = self.fill(ratings=False)
        self.assertNotIn('Power', unrated['components'][0]['part_binding'])
        self.assertNotIn('ratings', unrated_report)

    def test_writer_cannot_supply_a_different_rating(self):
        self.intent['components'][0]['part_binding'] = {'Power': '250mW'}
        with self.assertRaisesRegex(ValueError, 'writer binding mismatch'):
            self.fill()
        self.intent['components'][0]['part_binding'] = {'Power': '100mW'}
        self.fill()

    def test_contradictory_catalog_attributes_refuse(self):
        from agent_ee.passive_ratings import derive
        response = self.root / 'named/C38695/responses/C38695.json'
        document = read_json(response)
        document['data']['componentPageInfo']['list'][0]['attributes'][0]['attribute_value_name'] = '125mW'
        # A rewritten response no longer matches its pinned capture: the hash refuses before parsing.
        write_json(response, document)
        with self.assertRaisesRegex(ValueError, 'captured response differs from part record'):
            derive(self.root, self.part, 'R')
        self.rebuild([POWER, {'attribute_name_en': 'Power(Watts)', 'attribute_value_name': '125mW'}])
        with self.assertRaises(ValueError) as caught:
            self.fill()
        for value in ('resistor: contradictory Power', '100mW', '125mW', 'C38695.json'):
            self.assertIn(value, str(caught.exception))
        self.rebuild([{'attribute_name_en': 'Power(Watts)', 'attribute_value_name': 'about 0.1 W'}])
        with self.assertRaisesRegex(ValueError, "unparsed Power rating 'about 0.1 W'"):
            self.fill()

    def test_absent_rating_stays_missing(self):
        for attributes, reason in ((None, 'captured C38695 row has null attributes'),
                                   ([SUPPLY], 'captured C38695 row has no Power(Watts) attribute')):
            self.rebuild(attributes)
            intent, report = self.fill()
            fields = intent['components'][0]['part_binding']
            self.assertFalse({'Power', 'Voltage', 'AGENT_EE_Ratings'} & set(fields))
            self.assertEqual(report['ratings'], {'sources_sha256': identity(self.ratings), 'derived': {},
                'missing': {'R1': reason + '; no selected-source datasheet rating fact'}})
        # A selected-source datasheet fact can still supply the absent catalog value.
        self.select_pdf(['Rated power 1/10 W'])
        self.facts(self.fact('1/10', 'W', 'Rated power 1/10 W'))
        fields = self.fill()[0]['components'][0]['part_binding']
        self.assertEqual(fields['Power'], '1/10W')
        self.assertEqual(self.provenance(fields)['ratings']['Power']['si'], '0.1')

    def test_resolved_kind_limits_attributes(self):
        from agent_ee.passive_ratings import derive
        self.assertEqual(derive(self.root, self.part, 'C'),
                         {'missing': 'captured C38695 row has no Voltage Rating attribute; '
                                     'no selected-source datasheet rating fact'})

    def test_selected_datasheet_fact_agrees_with_catalog(self):
        self.select_pdf(['Owned resistor family', 'Rated power 100 mW at 70 C'])
        self.facts(self.fact('100', 'mW', 'Rated power 100 mW'))
        intent, report = self.fill()
        fields = intent['components'][0]['part_binding']
        self.assertEqual(fields['Power'], '100mW')
        sources = self.provenance(fields)['ratings']['Power']['sources']
        self.assertEqual([s['kind'] for s in sources], ['catalog', 'datasheet'])
        self.assertEqual(sources[1], {'kind': 'datasheet', 'id': 'general-family', 'fact': 'power',
                                      'path': self.selected['path'], 'sha256': self.selected['sha256'], 'page': 1})
        self.assertEqual(report['dependencies'][self.selected['path']], self.selected['sha256'])
        self.assertEqual(report['dependencies']['rating-facts.json'], digest(self.root / 'rating-facts.json'))
        # Datasheet evidence alone is sufficient when catalog ratings are not declared.
        self.ratings['catalog'] = False
        fields = self.fill()[0]['components'][0]['part_binding']
        self.assertEqual(fields['Power'], '100mW')
        self.assertEqual([s['kind'] for s in self.provenance(fields)['ratings']['Power']['sources']], ['datasheet'])

    def test_contradictory_datasheet_fact_refuses_with_sources(self):
        self.select_pdf(['Rated power 125 mW'])
        self.facts(self.fact('125', 'mW', 'Rated power 125 mW'))
        with self.assertRaises(ValueError) as caught:
            self.fill()
        for value in ('contradictory Power', '100mW', '125mW', 'Power(Watts)', self.selected['sha256']):
            self.assertIn(value, str(caught.exception))

    def test_rejected_or_unselected_source_refuses(self):
        self.select_pdf(['Rated power 100 mW'])
        self.facts(self.fact('100', 'mW', 'Rated power 100 mW', source=self.record['artifact']))
        with self.assertRaisesRegex(ValueError, 'cites rejected source ' + self.record['artifact']['sha256']):
            self.fill()
        self.select_pdf(['Rated power 100 mW'], rejected=False)
        text_pdf(self.root / 'other.pdf', ['Rated power 100 mW'])
        self.facts(self.fact('100', 'mW', 'Rated power 100 mW',
                             source={'path': 'other.pdf', 'sha256': digest(self.root / 'other.pdf')}))
        with self.assertRaisesRegex(ValueError, 'not selected source general-family'):
            self.fill()

    def test_fact_without_declared_source_selection_refuses(self):
        text_pdf(self.root / 'other.pdf', ['Rated power 100 mW'])
        self.facts(self.fact('100', 'mW', 'Rated power 100 mW',
                             source={'path': 'other.pdf', 'sha256': digest(self.root / 'other.pdf')}))
        with self.assertRaisesRegex(ValueError, 'require a captured datasheet with a declared source_selection'):
            self.fill()

    def test_unverified_citation_and_wrong_kind_refuse(self):
        self.select_pdf(['Rated power 100 mW'])
        self.facts(self.fact('100', 'mW', 'Rated power 100 mW', page=2))
        with self.assertRaisesRegex(ValueError, 'fact power citation rejected: page_out_of_range'):
            self.fill()
        self.facts(self.fact('100', 'mW', 'Rated power 1000 mW'))
        with self.assertRaisesRegex(ValueError, 'value_absent_from_quote'):
            self.fill()
        self.facts(self.fact('100', 'mW', 'Rated power 100 mW', kind='current_rating'))
        with self.assertRaisesRegex(ValueError, 'non-rating facts: power'):
            self.fill()
        self.facts(self.fact('50', 'V', 'Rated power 100 mW', kind='voltage_rating'))
        with self.assertRaisesRegex(ValueError, 'value_absent_from_quote'):
            self.fill()
        # A selected envelope pdftotext cannot read is unsupported, never support.
        self.select_pdf([], data=b'%PDF-1.4\nnot a page tree\n%%EOF\n')
        self.facts(self.fact('100', 'mW', 'Rated power 100 mW'))
        with self.assertRaisesRegex(ValueError, 'fact power citation unsupported: pdftotext'):
            self.fill()
        # Changed captured bytes refuse before any rating is derived.
        self.select_pdf(['Rated power 100 mW'])
        self.facts(self.fact('100', 'mW', 'Rated power 100 mW'))
        (self.root / self.selected['path']).write_bytes(b'%PDF-1.4\nchanged\n%%EOF\n')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            self.fill()

    def test_fact_naming_no_bound_part_refuses(self):
        self.select_pdf(['Rated power 100 mW'])
        fact = self.fact('100', 'mW', 'Rated power 100 mW')
        fact['part_number'] = 'OWNED-UNBOUND'
        self.facts(fact)
        with self.assertRaisesRegex(ValueError, 'rating facts name no bound R/C part: power'):
            self.fill()

    def test_quantity_spellings(self):
        from agent_ee.passive_ratings import quantity
        self.assertEqual(str(quantity('1/10 W', 'Power')), '1/10')
        self.assertEqual(quantity('100mW', 'Power'), quantity('0.1W', 'Power'))
        for text, field in (('75V', 'Power'), ('1/3W', 'Power'), ('0V', 'Voltage'), ('25 VDC', 'Voltage'),
                            ('', 'Voltage'), (None, 'Voltage')):
            self.assertIsNone(quantity(text, field), text)

    def test_checker_validates_present_provenance(self):
        from agent_ee.passive_ratings import validate_binding
        fields = self.fill()[0]['components'][0]['part_binding']
        self.assertEqual(validate_binding(fields, 'R'), [])
        self.assertEqual(validate_binding({'Power': '100mW'}, 'R'), [])
        self.assertIn("Power='250mW' differs", validate_binding({**fields, 'Power': '250mW'}, 'R')[0])
        self.assertIn("Power=None differs", validate_binding({k: v for k, v in fields.items() if k != 'Power'}, 'R')[0])
        self.assertIn('not a C rating', validate_binding(fields, 'C')[0])
        self.assertIn('malformed', validate_binding({**fields, 'AGENT_EE_Ratings': '{'}, 'R')[0])
        native = json.loads(fields['AGENT_EE_Ratings'])
        for change in ({'record_sha256': 'x'}, {'schema': 'other'}, {'ratings': {}}, {'extra': 1}):
            forged = {**fields, 'AGENT_EE_Ratings': json.dumps({**native, **change})}
            self.assertIn('malformed', validate_binding(forged, 'R')[0], change)
        unparsed = {**native, 'ratings': {'Power': 'about 0.1 W'}}
        self.assertIn('differs', validate_binding({**fields, 'Power': 'about 0.1 W',
                                                   'AGENT_EE_Ratings': json.dumps(unparsed)}, 'R')[0])


class NativeRatingPropagationTests(unittest.TestCase):
    def test_ratings_with_separate_output_receipt_fills_only_the_bound_intent(self):
        # The pod campaign composes AGENTEE-197's separate output with AGENTEE-233's ratings.
        from toolchain import require_toolchain
        from agent_ee import binding_fill
        from agent_ee.parts_manifest import qualify_manifest
        from agent_ee.stage_receipts import adopt, run_step, validate_receipt
        require_toolchain(native=True)
        helper = PassiveRatingTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        root = helper.root
        helper.select_pdf(['Owned resistor family', 'Rated power 100 mW at 70 C'])
        helper.facts(helper.fact('100', 'mW', 'Rated power 100 mW'))
        intent = helper.intent
        intent['components'] = [{**intent['components'][0], 'id': 'pod/R1', 'reference': 'R1'}]
        intent['no_connects'] = [{'component': 'pod/R1', 'pin': {'number': p}} for p in ('1', '2')]
        helper.part['instances'] = [{'sheet': '/', 'reference': 'R1'}]
        refs = {'schema': 'agent-ee.part-refs/1', 'components': {'pod/R1': {'part_ref': 'resistor'}}}
        for name, value in [('intent.json', intent), ('intent.bound.json', {}), ('parts.json', helper.parts),
                            ('refs.json', refs), ('deviations.json', helper.deviations),
                            ('ratings.json', helper.ratings)]:
            write_json(root / name, value)
        shutil.copyfile(root / 'Owned.kicad_sym', root / 'captures.kicad_sym')
        source = Path(__file__).resolve().parents[1] / 'src'
        kwargs = dict(intent='intent.json', manifest='parts.json', part_refs='refs.json',
                      deviations='deviations.json', captures='.',
                      dependencies=sorted({*qualify_manifest(helper.parts, root)['dependencies'],
                                           'rating-facts.json', helper.selected['path']}))
        spec = binding_fill.configuration(source, output='intent.bound.json', ratings='ratings.json', **kwargs)
        self.assertEqual(spec['writes'], {'intent.bound.json': 'out/intent.json'})
        self.assertIn('intent.json', spec['inputs'])
        self.assertIn('ratings.json', spec['inputs'])
        self.assertNotIn('intent.bound.json', spec['inputs'])
        self.assertEqual(spec['argv'][spec['argv'].index('--ratings') + 1], 'ratings.json')
        for clash, message in (({'output': 'ratings.json'}, 'output intent'),
                               ({'intent': 'ratings.json'}, 'read intent')):
            with self.subTest(clash=clash), self.assertRaisesRegex(ValueError, message):
                binding_fill.configuration(source, **{**kwargs, 'output': 'intent.bound.json',
                                                      'ratings': 'ratings.json', **clash})
        before = digest(root / 'intent.json')
        step = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'bind'
        receipt = run_step(step, root, spec, {'implementation': 1, 'source_candidate_id': 'pod-compose',
                                               'receipt_index': 1})
        self.assertEqual(receipt['status'], 'completed', (step / 'run/console.log').read_text())
        self.assertEqual(validate_receipt(step), receipt)
        adopt(step, receipt, root)
        self.assertEqual(digest(root / 'intent.json'), before)
        report = read_json(step / 'run/out/binding-fill.json')
        self.assertEqual(set(report['ratings']['derived']), {'R1'})
        self.assertEqual(report['ratings']['missing'], {})
        bound = read_json(root / 'intent.bound.json')['components'][0]['part_binding']
        self.assertEqual(bound['Power'], '100mW')
        self.assertEqual(json.loads(bound['AGENT_EE_Ratings'])['record_sha256'],
                         identity(report['ratings']['derived']['R1']['record']))

    def test_receipted_fields_reach_native_symbol_board_and_assembly_check(self):
        import xml.etree.ElementTree as ET
        import pcbnew as k
        from toolchain import require_toolchain
        from agent_ee import binding_fill, schematic_stage
        from agent_ee.assembly_readiness import evaluate_assembly_readiness
        from agent_ee.part_binding import validate_part_bindings
        from agent_ee.parts_manifest import qualify_manifest
        from agent_ee.stage_receipts import adopt, run_step, validate_receipt
        from test_assembly_readiness import _write_sqlite_catalog
        require_toolchain(native=True)
        helper = PassiveRatingTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        root = helper.root
        # A cited fact on the selected source exercises pdftotext inside the receipted step's sandbox.
        helper.select_pdf(['Owned resistor family', 'Rated power 100 mW at 70 C'])
        helper.facts(helper.fact('100', 'mW', 'Rated power 100 mW'))
        intent = helper.intent
        intent['components'] = [{**intent['components'][0], 'id': f'pod/R{i}', 'reference': f'R{i}'} for i in (1, 2)]
        intent['no_connects'] = [{'component': c['id'], 'pin': {'number': p}}
                                 for c in intent['components'] for p in ('1', '2')]
        helper.part['instances'] = [{'sheet': '/', 'reference': 'R1'}, {'sheet': '/', 'reference': 'R2'}]
        refs = {'schema': 'agent-ee.part-refs/1',
                'components': {c['id']: {'part_ref': 'resistor'} for c in intent['components']}}
        for name, value in [('intent.json', intent), ('parts.json', helper.parts), ('refs.json', refs),
                            ('deviations.json', helper.deviations), ('ratings.json', helper.ratings)]:
            write_json(root / name, value)
        captures = root / 'captures'
        captures.mkdir()
        shutil.copyfile(root / 'Owned.kicad_sym', captures / 'Owned.kicad_sym')
        shutil.copytree(root / 'Owned.pretty', captures / 'Owned.pretty')
        write_json(root / 'layout.json', {'schema': 'agent-ee.schematic-layout/1', 'project': 'rated',
                                          'namespace': '6f0f7f0e-5c43-5b8e-9a51-2a3f2c1d0e41', 'transforms': {}})
        k.SaveBoard(str(root / 'rated.kicad_pcb'), k.BOARD())
        (root / 'rated.kicad_sch').write_bytes(b'')
        source = Path(__file__).resolve().parents[1] / 'src'
        dependencies = sorted({*qualify_manifest(helper.parts, root)['dependencies'], 'rating-facts.json',
                               helper.selected['path']})
        steps = [binding_fill.configuration(source, intent='intent.json', manifest='parts.json',
            part_refs='refs.json', deviations='deviations.json', captures='captures',
            dependencies=dependencies, ratings='ratings.json')]
        self.assertIn('--ratings', steps[0]['argv'])
        steps += schematic_stage.configuration(source, board='rated.kicad_pcb', schematic='rated.kicad_sch',
            intent='intent.json', layout='layout.json', captures='captures',
            footprints='cad.json')['implementation_steps']
        receipts = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for i, spec in enumerate(steps, 1):
            path = receipts / str(i)
            receipt = run_step(path, root, spec, {'implementation': 1, 'source_candidate_id': 'rated',
                                                  'receipt_index': i})
            if receipt['status'] != 'completed' and os.environ.get('AGENT_EE_TEST_EVIDENCE'):
                shutil.copytree(path, tempfile.mkdtemp(prefix=f'failed-step-{i}-', dir=os.environ['AGENT_EE_TEST_EVIDENCE']),
                                dirs_exist_ok=True)
            self.assertEqual(receipt['status'], 'completed', {'step': i, 'violations': receipt['violations'],
                'console': (path / 'run/console.log').read_text()})
            self.assertEqual(validate_receipt(path), receipt)
            adopt(path, receipt, root)
        fill_report = read_json(receipts / '1/run/out/binding-fill.json')
        self.assertEqual(set(fill_report['ratings']['derived']), {'R1', 'R2'})
        filled = {c['reference']: c['part_binding'] for c in read_json(root / 'intent.json')['components']}
        self.assertEqual(filled['R1']['Power'], '100mW')
        record = fill_report['ratings']['derived']['R1']
        self.assertEqual(json.loads(filled['R1']['AGENT_EE_Ratings']),
                         {'schema': 'agent-ee.passive-rating-binding/1', 'ratings': {'Power': '100mW'},
                          'record_sha256': identity(record['record'])})
        self.assertEqual([s['kind'] for s in record['record']['ratings']['Power']['sources']], ['catalog', 'datasheet'])
        schematic, board = (root / 'rated.kicad_sch').read_bytes(), (root / 'rated.kicad_pcb').read_bytes()
        report = validate_part_bindings(schematic, board, require_datasheet=True)
        self.assertEqual((report['execution'], report['outcome']), ('ok', 'pass'), report)
        # A fresh pcbnew process reads the emitted footprint fields, not this process's objects.
        probe = subprocess.run([shutil.which('python3') or '/usr/bin/python3', '-c',
            'import json, sys, pcbnew\n'
            'b = pcbnew.LoadBoard(sys.argv[1])\n'
            'print(json.dumps({f.GetReference(): {x.GetName(): x.GetText() for x in f.GetFields()} '
            'for f in b.GetFootprints()}))', str(root / 'rated.kicad_pcb')],
            capture_output=True, text=True, timeout=120, env={**os.environ, 'PYTHONPATH': ''})
        self.assertEqual(probe.returncode, 0, probe.stderr)
        native = json.loads(probe.stdout.strip().splitlines()[-1])
        for ref, fields in filled.items():
            for name in ('Power', 'AGENT_EE_Ratings'):
                self.assertEqual(native[ref][name], fields[name], (ref, name))
        catalog = root / 'assembly-catalog'
        catalog.mkdir()
        _write_sqlite_catalog(catalog, [{'lcsc': 'C38695', 'mpn': MPN, 'stock': 1000}])
        xml = receipts / '3/run/out/netlist/design.xml'
        params = {'catalog': 'assembly-catalog/catalog-manifest.json'}
        assembly = evaluate_assembly_readiness(root, xml, params)
        self.assertEqual(assembly['outcome'], 'pass', assembly)
        self.assertIs(assembly['policy']['require_passive_ratings'], True)
        self.assertEqual({c['rating_provenance'] for c in assembly['components']}, {'source_backed'})
        # The checker refuses a rating that drifted from its provenance, and absent ratings stay defects.
        def edited(name, change):
            tree = ET.parse(xml)
            for comp in tree.getroot().find('components').findall('comp'):
                for node in comp.findall('./fields/field') + comp.findall('property'):
                    change(comp, node)
            tree.write(root / name)
            return root / name

        def drift(comp, node):
            if comp.get('ref') == 'R1' and node.get('name') == 'Power':
                node.text, value = ('250mW', None) if node.tag == 'field' else (node.text, node.set('value', '250mW'))

        def strip(comp, node):
            if node.get('name') in ('Power', 'AGENT_EE_Ratings'):
                (comp.find('fields') if node.tag == 'field' else comp).remove(node)

        drifted = evaluate_assembly_readiness(root, edited('drifted.xml', drift), params)
        self.assertEqual([(f['code'], f['reference']) for f in drifted['findings'] if f['severity'] == 'defect'],
                         [('invalid_rating_provenance', 'R1')])
        result = evaluate_assembly_readiness(root, edited('bare.xml', strip), params)
        self.assertEqual(result['outcome'], 'defect')
        self.assertEqual(result['missing_rating_refs'], ['R1', 'R2'])


class ReceiptBoundRatingProvenanceTests(unittest.TestCase):
    """AGENTEE-242: the checker resolves record_sha256 against the receipted binding-fill report."""

    def setUp(self):
        from toolchain import require_toolchain
        from agent_ee import binding_fill
        from agent_ee.parts_manifest import qualify_manifest
        from agent_ee.stage_receipts import adopt, run_step
        from test_assembly_readiness import _write_sqlite_catalog
        require_toolchain(native=True)
        helper = PassiveRatingTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        self.root = root = helper.root
        for name, value in [('intent.json', helper.intent), ('parts.json', helper.parts), ('refs.json', helper.refs),
                            ('deviations.json', helper.deviations), ('ratings.json', helper.ratings)]:
            write_json(root / name, value)
        spec = binding_fill.configuration(Path(__file__).resolve().parents[1] / 'src', intent='intent.json',
            manifest='parts.json', part_refs='refs.json', deviations='deviations.json', captures='.',
            dependencies=sorted(qualify_manifest(helper.parts, root)['dependencies']), ratings='ratings.json')
        step = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'bind'
        receipt = run_step(step, root, spec, {'implementation': 1, 'source_candidate_id': 'rated', 'receipt_index': 1})
        self.assertEqual(receipt['status'], 'completed', (step / 'run/console.log').read_text())
        adopt(step, receipt, root)
        (root / 'provenance').mkdir()
        shutil.copyfile(step / 'receipt.json', root / 'provenance/binding-fill.receipt.json')
        shutil.copyfile(step / 'run/out/binding-fill.json', root / 'provenance/binding-fill.json')
        self.report = read_json(root / 'provenance/binding-fill.json')
        self.fields = read_json(root / 'intent.json')['components'][0]['part_binding']
        self.assertIn('AGENT_EE_Ratings', self.fields)
        (root / 'assembly-catalog').mkdir()
        _write_sqlite_catalog(root / 'assembly-catalog', [{'lcsc': 'C38695', 'mpn': MPN, 'stock': 1000}])
        self.provenance = {'receipt': 'provenance/binding-fill.receipt.json',
                           'receipt_sha256': digest(root / 'provenance/binding-fill.receipt.json'),
                           'report': 'provenance/binding-fill.json'}
        self.params = {'catalog': 'assembly-catalog/catalog-manifest.json', 'rating_provenance': self.provenance}

    def netlist(self, fields, name='design.xml'):
        """An owned KiCad XML netlist carrying exactly ``fields`` on R1."""
        import xml.etree.ElementTree as ET
        export = ET.Element('export', version='E')
        ET.SubElement(ET.SubElement(export, 'design'), 'sheet', number='1', name='/', tstamps='/')
        comp = ET.SubElement(ET.SubElement(export, 'components'), 'comp', ref='R1')
        ET.SubElement(comp, 'value').text = '10k'
        ET.SubElement(comp, 'footprint').text = 'Owned:R2'
        node = ET.SubElement(comp, 'fields')
        for key, value in fields.items():
            ET.SubElement(node, 'field', name=key).text = value
        ET.SubElement(comp, 'libsource', lib='Owned', part='R')
        ET.SubElement(comp, 'sheetpath', names='/', tstamps='/')
        ET.SubElement(comp, 'tstamps').text = '6f0f7f0e-5c43-5b8e-9a51-2a3f2c1d0e41'
        ET.ElementTree(export).write(self.root / name)
        return self.root / name

    def evaluate(self, fields, params=None):
        from agent_ee.assembly_readiness import evaluate_assembly_readiness
        return evaluate_assembly_readiness(self.root, self.netlist(fields), params or self.params)

    def defects(self, result):
        return [(f['code'], f['reference']) for f in result['findings'] if f['severity'] == 'defect']

    def forged(self, **change):
        native = json.loads(self.fields['AGENT_EE_Ratings'])
        return {**self.fields, 'AGENT_EE_Ratings': json.dumps({**native, **change})}

    def test_genuine_receipted_record_is_accepted(self):
        result = self.evaluate(self.fields)
        self.assertEqual(result['outcome'], 'pass', result['findings'])
        self.assertEqual([(c['rating_provenance'], c['rating_record']) for c in result['components']],
                         [('source_backed', 'receipt_bound')])
        self.assertEqual(result['rating_provenance']['report_sha256'],
                         digest(self.root / 'provenance/binding-fill.json'))
        self.assertEqual((result['rating_provenance']['records'], result['rating_provenance']['error']), (1, None))

    def test_forged_64_hex_hash_is_refused(self):
        result = self.evaluate(self.forged(record_sha256='a' * 64))
        self.assertEqual(result['outcome'], 'defect')
        self.assertEqual(self.defects(result), [('invalid_rating_provenance', 'R1')])
        self.assertIn('not the receipted record', ' '.join(result['findings'][0]['problems']))
        self.assertEqual(result['components'][0]['rating_record'], 'unresolved')

    def test_consistent_native_fields_with_a_forged_record_are_refused(self):
        from agent_ee.storage import canonical
        entry = self.report['ratings']['derived']['R1']
        record = copy.deepcopy(entry['record'])
        record['ratings']['Power'].update(value='250mW', si='0.25')
        # A self-consistent forgery: native Power, field ratings and record identity all agree.
        fields = {**self.fields, 'Power': '250mW', 'AGENT_EE_Ratings': canonical({
            'schema': 'agent-ee.passive-rating-binding/1', 'ratings': {'Power': '250mW'},
            'record_sha256': identity(record)}).decode()}
        self.assertEqual(self.evaluate(fields, {'catalog': self.params['catalog']})['outcome'], 'pass')
        result = self.evaluate(fields)
        self.assertEqual(self.defects(result), [('invalid_rating_provenance', 'R1')])
        problems = ' '.join(result['findings'][0]['problems'])
        self.assertIn('not the receipted record', problems)
        self.assertIn('differ from the receipted record', problems)
        # A genuine hash with a respelled rating or another part identity is still refused.
        respelled = {**self.fields, 'Power': '0.1W', 'AGENT_EE_Ratings': canonical({
            'schema': 'agent-ee.passive-rating-binding/1', 'ratings': {'Power': '0.1W'},
            'record_sha256': entry['record_sha256']}).decode()}
        self.assertEqual(self.defects(self.evaluate(respelled)), [('invalid_rating_provenance', 'R1')])
        other = self.evaluate({**self.fields, 'Partnumber': 'OTHER-MPN'})
        self.assertIn('part number differs', ' '.join(f for x in other['findings'] if x['severity'] == 'defect'
                                                        for f in x.get('problems', [])))
        # A rating field stripped from a component the receipt derived is refused, not silently unsourced.
        bare = {k: v for k, v in self.fields.items() if k != 'AGENT_EE_Ratings'}
        self.assertIn(('invalid_rating_provenance', 'R1'), self.defects(self.evaluate(bare)))

    def test_missing_or_unbound_provenance_is_unknown(self):
        def unknown(result, text):
            self.assertEqual(result['outcome'], 'unknown', result['findings'])
            self.assertEqual([f['code'] for f in result['findings'] if f['severity'] == 'unknown'],
                             ['rating_provenance_unavailable'])
            self.assertIn(text, result['rating_provenance']['error'])
            self.assertEqual(result['components'][0]['rating_record'], 'unavailable')

        report = self.root / 'provenance/binding-fill.json'
        original = report.read_bytes()
        report.unlink()
        unknown(self.evaluate(self.fields), 'missing')
        # A report that is not the receipted artifact, even a well-formed one, is not provenance.
        forged = copy.deepcopy(self.report)
        forged['ratings']['derived']['R1']['record']['limitations'] = []
        forged['ratings']['derived']['R1']['record_sha256'] = identity(forged['ratings']['derived']['R1']['record'])
        write_json(report, forged)
        unknown(self.evaluate(self.fields), 'not the receipted artifact')
        report.write_bytes(original)
        unknown(self.evaluate(self.fields, {**self.params, 'rating_provenance': {
            **self.provenance, 'receipt_sha256': 'b' * 64}}), 'pinned sha256')
        receipt = read_json(self.root / self.provenance['receipt'])
        receipt['status'] = 'failed'
        write_json(self.root / 'provenance/failed.receipt.json', receipt)
        unknown(self.evaluate(self.fields, {**self.params, 'rating_provenance': {
            **self.provenance, 'receipt': 'provenance/failed.receipt.json',
            'receipt_sha256': digest(self.root / 'provenance/failed.receipt.json')}}), 'malformed')
        # A determinate defect elsewhere stays a defect while the provenance is unknown.
        report.unlink()
        drifted = self.evaluate({**self.fields, 'Power': '250mW'})
        self.assertEqual(drifted['outcome'], 'defect')
        self.assertEqual(self.defects(drifted), [('invalid_rating_provenance', 'R1')])

    def test_undeclared_provenance_keeps_the_format_check_and_says_so(self):
        params = {'catalog': self.params['catalog']}
        result = self.evaluate(self.forged(record_sha256='a' * 64), params)
        self.assertEqual(result['outcome'], 'pass')
        self.assertEqual(result['components'][0]['rating_record'], 'undeclared')
        self.assertEqual([(f['code'], f['references']) for f in result['findings'] if f['severity'] == 'info'],
                         [('undeclared_rating_provenance', ['R1'])])
        self.assertIsNone(result['rating_provenance'])

    def test_capability_pins_the_provenance_as_captured_inputs(self):
        from agent_ee.assembly_readiness import capability, validate_params
        files = {'assembly-catalog/catalog-manifest.json', self.provenance['receipt'], self.provenance['report']}
        descriptor = capability(self.params, files)
        self.assertIn('assembly:rating-provenance', descriptor['predicates'])
        self.assertTrue({self.provenance['receipt'], self.provenance['report']} <= set(descriptor['immutable_inputs']))
        self.assertNotIn('assembly:rating-provenance', capability({'catalog': self.params['catalog']}, files)['predicates'])
        with self.assertRaisesRegex(ValueError, 'captured input'):
            capability(self.params, files - {self.provenance['report']})
        for change, message in (({'receipt_sha256': 'x'}, 'hex digest'), ({'report': '../x.json'}, 'normalized|unsafe|escape'),
                                ({'report': self.provenance['receipt']}, 'distinct'), ({'extra': 1}, '')):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, message):
                validate_params({**self.params, 'rating_provenance': {**self.provenance, **change}})
