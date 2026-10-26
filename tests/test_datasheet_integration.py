"""Owned inputs for capture adapters and offline missing obligations."""
import copy
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from agent_ee.native_netlist import read_native_inventory

FIXTURE = Path(__file__).parent / 'fixtures/native-binding/control.xml'


class NativeDatasheetTests(unittest.TestCase):
    def read(self, mutate):
        with tempfile.TemporaryDirectory() as tmp:
            tree = ET.parse(FIXTURE)
            comp = tree.getroot().find('./components/comp')
            for node in comp.findall("./fields/field[@name='Datasheet']"):
                comp.find('fields').remove(node)
            mutate(comp)
            path = Path(tmp) / 'native.xml'
            tree.write(path)
            return next(iter(read_native_inventory(path)['components'].values()))

    def test_dedicated_and_matching_fields_preserve_exact_text(self):
        def mutate(comp):
            ET.SubElement(comp, 'datasheet').text = 'https://example.invalid/owned.pdf'
            ET.SubElement(comp.find('fields'), 'field', name='Datasheet').text = 'https://example.invalid/owned.pdf'
        self.assertEqual(self.read(mutate)['datasheet'], 'https://example.invalid/owned.pdf')

    def test_missing_and_empty_are_distinct(self):
        self.assertIsNone(self.read(lambda c: None)['datasheet'])
        self.assertEqual(self.read(lambda c: ET.SubElement(c, 'datasheet'))['datasheet'], '')

    def test_contradictions_duplicates_and_structured_forms_refuse(self):
        def mutate(comp, form):
            ET.SubElement(comp, 'datasheet').text = 'https://example.invalid/a.pdf'
            if form == 'property':
                ET.SubElement(comp, 'property', name='Datasheet', value='different')
            elif form == 'field':
                ET.SubElement(comp.find('fields'), 'field', name='Datasheet').text = ''
            elif form == 'nested':
                ET.SubElement(comp.find('datasheet'), 'url').text = 'different'
            else:
                ET.SubElement(comp, 'datasheet').text = 'https://example.invalid/a.pdf'
        for form in ('property', 'field', 'nested', 'duplicate'):
            with self.subTest(form=form), self.assertRaises(ValueError):
                self.read(lambda c: mutate(c, form))

class SelectionAdapterTests(unittest.TestCase):
    def test_experimental_all_null_keeps_unassembled_obligation(self):
        from agent_ee.datasheet_parts import bindings_from_rung1
        manifest, raw = rung1()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'owned.csv').write_bytes(raw)
            result = bindings_from_rung1(manifest, root)
        self.assertEqual(result['qualification'], 'experimental-unqualified')
        self.assertEqual([p['reference'] for p in result['parts']], ['J1', 'R1'])
        self.assertTrue(all(p['datasheet_url'] is None and p['part_number'] is None for p in result['parts']))


def rung1():
    from agent_ee.storage import identity
    import hashlib
    raw = b'Comment,Designator,Footprint,LCSC Part #,Quantity\n10k,R9,Resistor_SMD:R_0805_2012Metric,C123,2\n'
    sha = hashlib.sha256(raw).hexdigest()
    source = dict(path='/not/read/owned.csv', repository_path='owned.csv', read_from='git blob owned',
                  commit='a' * 40, checkout_head='b' * 40, remote='owned', sha256=sha, bytes=len(raw),
                  working_tree_differs=False, working_tree_sha256=sha, note='owned test data')
    sels = {'R1': dict(lib_id='Device:R', value='10k', default_package='0805',
            chosen=dict(catalog_line=2, footprint='Resistor_SMD:R_0805_2012Metric', lcsc='C123',
                        quantity='2', package='0805', rank_key=[0, -4, -2, 123]), rejected=[],
            footprint_file=dict(path='/not/read/R.kicad_mod', sha256='c' * 64, pads=['1', '2']),
            package_note='fixture default package'),
            'J1': dict(lib_id='Connector_Generic:Conn_01x03', value='Conn_01x03', chosen=None,
                       rejected=[], catalog_rows=[], reason='hand soldered',
                       footprint_file=dict(path='/not/read/J.kicad_mod', sha256='d' * 64, pads=['1','2','3']))}
    for ref, sel in sels.items():
        sel['selection_id'] = identity(dict(catalog_sha256=sha,
            catalog_line=sel['chosen']['catalog_line'] if sel['chosen'] else None,
            lib_id=sel['lib_id'], value=sel['value'], reference=ref))
    parts = {'R1': dict(Footprint='Resistor_SMD:R_0805_2012Metric', LCSC='C123'),
             'J1': dict(Footprint='Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical', JLCPCB_Assembly='not_assembled')}
    for part in parts.values():
        part.update(Datasheet=None, datasheet_obligation='missing:catalog_has_no_datasheet_column')
    return dict(schema='agent-ee.rung1-parts-manifest/0-experimental', qualification='experimental-unqualified',
                catalog=dict(path='owned.csv', sha256=sha, source=source,
                             columns=['Comment','Designator','Footprint','LCSC Part #','Quantity']),
                policy=dict(value_match='catalog Comment equals the intent value exactly',
                            families={'Device:R': r'Resistor_SMD:R_(\d{4,5})_\d{4,5}Metric',
                                      'Device:C': r'Capacitor_SMD:C_(\d{4,5})_\d{4,5}Metric'},
                            rank=['distance from the fixture default chip size','larger chip first on equal distance',
                                  'catalog quantity descending (most used in fome-mc)','LCSC number ascending'],
                            not_assembled='parts the spec names hand-soldered; they have no catalog row and are excluded from the JLCPCB BOM and CPL by rule'),
                parts=parts, selections=sels,
                assembly_exclusions=[dict(ref='J1', rule='JLCPCB_Assembly=not_assembled', reason='hand soldered')],
                unknown_fields={'MPN':'catalog has no MPN column; omitted, never invented',
                                'Manufacturer':'catalog has no manufacturer column; omitted, never invented',
                                'LCSC':'omitted for not_assembled parts, which have no catalog row'}), raw

class MissingCoverageTests(unittest.TestCase):
    def test_preparation_preserves_every_part_without_fake_pdf(self):
        from agent_ee.datasheet_parts import bindings_from_rung1
        from agent_ee.datasheet_capture import prepare_datasheets
        from agent_ee.datasheet_facts import load_facts, verification_inputs, load_obligations
        manifest, raw = rung1()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'owned.csv').write_bytes(raw)
            bindings = bindings_from_rung1(manifest, root)
            result = prepare_datasheets(bindings, {'J1': ['pins'], 'R1': ['rating']}, root / 'prepared')
            self.assertEqual(result['outcome'], 'missing')
            self.assertEqual(load_facts(root / 'prepared', 'facts.json'), [])
            doc = load_obligations(root / 'prepared', 'facts.json')
            self.assertEqual([r['reference'] for r in doc['required_parts']], ['J1', 'R1'])
            self.assertEqual(list((root / 'prepared').rglob('*.pdf')), [])
            deps = verification_inputs({'checks': [{'kind': 'datasheet_facts', 'params': {'facts': 'facts.json'}}]}, root / 'prepared')
            self.assertEqual(deps, {'facts.json', 'bindings.json', 'acquisition/manifest.json'})

class CanonicalAdapterTests(unittest.TestCase):
    def setUp(self):
        from test_part_selection import SelectionTests
        self.fixture = SelectionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def selected(self, url='https://example.invalid/owned.pdf', quantity=2):
        import json
        from agent_ee.part_selection import select_parts
        attrs = {'resistance': '10 kohm +/-1%', 'voltage': '50 V'}
        if url is not None:
            attrs['Datasheet'] = url
        snapshot, sha = self.fixture.snapshot([self.fixture.row(attributes=json.dumps(attrs))])
        req = self.fixture.requirements(id='resistors', quantity_per_board=quantity)
        return snapshot, sha, req, select_parts(snapshot, sha, req)

    def test_actual_catalog_rederivation_and_explicit_multiplicity(self):
        from agent_ee.datasheet_parts import bindings_from_selection
        snap, sha, req, selection = self.selected()
        result = bindings_from_selection(snap, sha, req, selection, {'resistors': ['R1', 'R2']})
        self.assertEqual([p['reference'] for p in result['parts']], ['R1', 'R2'])
        self.assertEqual([p['part_number'] for p in result['parts']], ['R-C1', 'R-C1'])
        self.assertTrue(all(p['datasheet_url'] == 'https://example.invalid/owned.pdf' for p in result['parts']))
        self.assertEqual(result['qualification'], 'selection-only')
        self.assertNotEqual(result['parts'][0]['selection_id'], result['parts'][1]['selection_id'])
        self.assertEqual(result['parts'][0]['source']['chosen'], selection['parts'][0]['chosen'])

    def test_tampered_chosen_row_or_allocation_refuses(self):
        from agent_ee.datasheet_parts import bindings_from_selection
        snap, sha, req, selection = self.selected()
        bad = copy.deepcopy(selection)
        bad['parts'][0]['chosen']['mpn'] = 'invented'
        with self.assertRaisesRegex(ValueError, 'differs'):
            bindings_from_selection(snap, sha, req, bad, {'resistors': ['R1', 'R2']})
        for allocation in ({}, {'resistors': ['R1']}, {'resistors': ['R1', 'R1']}, {'R1': ['R1','R2']}):
            with self.subTest(allocation=allocation), self.assertRaises(ValueError):
                bindings_from_selection(snap, sha, req, selection, allocation)

    def test_no_winner_keeps_all_allocated_obligations(self):
        from agent_ee.datasheet_parts import bindings_from_selection
        from agent_ee.part_selection import select_parts
        snap, sha, req, _ = self.selected()
        req['parts'][0]['package'] = 'unknown'
        selection = select_parts(snap, sha, req)
        result = bindings_from_selection(snap, sha, req, selection, {'resistors': ['R1', 'R2']})
        self.assertEqual(len(result['parts']), 2)
        self.assertTrue(all(p['datasheet_url'] is None and p['part_number'] is None for p in result['parts']))


class ExperimentalBoundaryTests(unittest.TestCase):
    def test_manifest_identity_and_full_rows_not_just_selection_id(self):
        from agent_ee.datasheet_parts import bindings_from_rung1
        mutations = [
            lambda m: m.update(schema='agent-ee.rung1-parts-manifest/1'),
            lambda m: m.update(qualification='approved'),
            lambda m: m.update(unknown=True),
            lambda m: m['catalog'].update(sha256='0' * 64),
            lambda m: m['catalog'].update(path='../owned.csv'),
            lambda m: m['catalog']['source'].update(sha256='0' * 64),
            lambda m: m['catalog']['columns'].append('Datasheet'),
            lambda m: m['parts'].pop('J1'),
            lambda m: m['selections']['R1'].update(selection_id='0' * 64),
            lambda m: m['selections']['R1']['chosen'].update(catalog_line=3),
            lambda m: m['selections']['R1']['chosen'].update(lcsc='C999'),
            lambda m: m['parts']['R1'].update(LCSC='C999'),
            lambda m: m['selections']['R1']['chosen'].update(footprint='Resistor_SMD:R_0603_1608Metric'),
            lambda m: m['selections']['R1']['chosen'].update(quantity='999'),
            lambda m: m['parts']['R1'].update(Datasheet='http://example.invalid/fake.pdf'),
            lambda m: m.update(assembly_exclusions=[]),
            lambda m: m['selections']['J1'].update(reason='different'),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index, mutation in enumerate(mutations):
                manifest, raw = rung1()
                (root / 'owned.csv').write_bytes(raw)
                mutation(manifest)
                with self.subTest(index=index), self.assertRaises(ValueError):
                    bindings_from_rung1(manifest, root)

    def test_changed_bytes_and_symlink_refuse(self):
        from agent_ee.datasheet_parts import bindings_from_rung1
        manifest, raw = rung1()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'owned.csv').write_bytes(raw + b'\n')
            with self.assertRaisesRegex(ValueError, 'hash'):
                bindings_from_rung1(manifest, root)
            (root / 'owned.csv').unlink()
            (root / 'target.csv').write_bytes(raw)
            (root / 'owned.csv').symlink_to('target.csv')
            with self.assertRaisesRegex(ValueError, 'symlink'):
                bindings_from_rung1(manifest, root)


class EnvelopeBoundaryTests(unittest.TestCase):
    def test_omitted_or_vacuous_obligations_and_manifest_tampering_refuse(self):
        from agent_ee.datasheet_parts import bindings_from_rung1
        from agent_ee.datasheet_capture import prepare_datasheets
        from agent_ee.datasheet_facts import load_facts
        from agent_ee.storage import read_json, write_json
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, raw = rung1()
            (root / 'owned.csv').write_bytes(raw)
            bindings = bindings_from_rung1(manifest, root)
            prepare_datasheets(bindings, {'J1': ['pins'], 'R1': ['rating']}, root / 'prepared')
            prepared = root / 'prepared'
            original = read_json(prepared / 'facts.json')
            mutations = [lambda d: d['required_parts'].pop(),
                         lambda d: d['required_parts'][0].update(required_facts=[]),
                         lambda d: d['required_parts'][0].update(selection_id='0' * 64),
                         lambda d: d['required_parts'][0].update(part_number='invented'),
                         lambda d: d['required_parts'].append(copy.deepcopy(d['required_parts'][0])),
                         lambda d: d['bindings'].update(sha256='0' * 64),
                         lambda d: d['acquisition'].update(path='../manifest.json')]
            for index, mutate in enumerate(mutations):
                data = copy.deepcopy(original)
                mutate(data)
                write_json(prepared / 'facts.json', data)
                with self.subTest(index=index), self.assertRaises(ValueError):
                    load_facts(prepared, 'facts.json')
            write_json(prepared / 'facts.json', original)
            (prepared / 'acquisition/manifest.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'acquisition hash'):
                load_facts(prepared, 'facts.json')

class OfflineNativeIntegrationTests(unittest.TestCase):
    def setUp(self):
        from toolchain import require_toolchain
        require_toolchain(native=True, tools=('pdftotext',))
        from test_datasheet_capture import DatasheetCaptureTests
        self.capture_fixture = DatasheetCaptureTests()
        self.capture_fixture.setUp()
        self.addCleanup(self.capture_fixture.doCleanups)
        self.root = self.capture_fixture.root

    def scope(self, prepared, *, native=False):
        import subprocess
        from agent_ee.storage import digest
        version = subprocess.run(['/usr/bin/pdftotext', '-v'], capture_output=True, text=True, check=True).stderr.splitlines()[0]
        scope = dict(schema='agent-ee.scope/2', name='Owned datasheet obligations', scope='Offline coverage',
                     inputs=prepared['inputs'] + [{'path': str(self.root / 'requirements.md'), 'at': 'requirements.md'}],
                     requirements=['requirements.md'],
                     checks=[dict(id='facts', kind='datasheet_facts', params=dict(facts='facts.json',
                         extractor=dict(path='/usr/bin/pdftotext', sha256=digest('/usr/bin/pdftotext'), version=version)))],
                     baseline=dict(requirements=[dict(id='coverage', statement='All declared per-part citations supported',
                          source=dict(path='requirements.md', locator='Owned obligations'), checks=['facts'])],
                          targets=['requirements.md'], reference_architectures=['requirements.md']))
        (self.root / 'requirements.md').write_text('Owned obligations: every declared part and required citation remains in scope.\n')
        if native:
            scope['inputs'] += [{'path': str(self.root / 'project'), 'at': 'project'},
                                {'path': '/usr/share/kicad/template', 'at': 'stock/template', 'include': ['sym-lib-table','fp-lib-table']}]
            scope.update(schematic='project/pins.kicad_sch', board='project/pins.kicad_pcb')
            scope['checks'].insert(0, dict(id='netlist', kind='netlist'))
            scope['checks'].append(dict(id='pins', kind='pin_map', params=dict(facts_check='facts', netlist_check='netlist',
                symbols=[dict(reference='U1', part_number='R-C1', facts=['canl','canh'])])))
            scope['baseline']['requirements'][0]['checks'] = ['netlist', 'facts', 'pins']
        if not native:
            original = Path(__file__).resolve().parents[1] / 'fixtures/datasheet-pins/control'
            scope['inputs'].append({'path': str(original), 'at': 'project'})
            scope.update(schematic='project/pins.kicad_sch', board='project/pins.kicad_pcb')
        return scope

    def review(self, scope, name):
        import contextlib
        import io
        from agent_ee.run_record import run_review, verify
        from agent_ee.storage import read_json, write_json
        write_json(self.root / 'scope.json', scope)
        run = self.root / name
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_review(self.root / 'scope.json', run, 90, checks_only=True), 1)
        recorded = read_json(run / 'result.json')
        self.assertFalse(recorded.get('error'), recorded)
        result = verify(run)
        for path in (run / 'work').rglob('execution.json'):
            self.assertIn('--unshare-net', read_json(path)['sandbox_argv'])
        return run, {c['id']: c for c in result['checks']}

    def canonical_bindings(self, url, quantity=1):
        from agent_ee.datasheet_parts import bindings_from_selection
        helper = CanonicalAdapterTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        snap, sha, req, selected = helper.selected(url, quantity)
        refs = ['U1'] if quantity == 1 else ['U1','U2']
        return bindings_from_selection(snap, sha, req, selected, {'resistors': refs})

    def test_owned_loopback_pdf_native_url_and_offline_facts_pinmap_public_verify(self):
        import shutil
        from agent_ee.datasheet_capture import prepare_datasheets, bindings_from_native
        from agent_ee.datasheet_parts import validate_native_bindings
        from agent_ee.storage import read_json, write_json
        fixture = self.capture_fixture
        bindings = self.canonical_bindings(fixture.url + '/pdf')
        prepared = prepare_datasheets(bindings, {'U1': ['canl','canh']}, self.root / 'prepared')
        fixture.stop_server()
        self.assertEqual(fixture.requests, ['/pdf'])
        self.assertEqual(prepared['outcome'], 'captured')
        # Explicit software fixture applicability; no claim of physical resistor/IC equivalence.
        original = Path(__file__).resolve().parents[1] / 'fixtures/datasheet-pins/control'
        shutil.copytree(original, self.root / 'project')
        schematic = self.root / 'project/pins.kicad_sch'
        text = schematic.read_text()
        marker = '(property "Reference" "U1"'
        self.assertEqual(text.count(marker), 1)
        text = text.replace(marker, '(property "Datasheet" "' + fixture.url + '/pdf" (at 0 0 0) (effects (font (size 1 1)) hide))\n' + marker)
        schematic.write_text(text)
        scope = self.scope(prepared, native=True)
        _, unknown = self.review(scope, 'no-citations')
        self.assertEqual((unknown['facts']['execution'], unknown['facts']['outcome']), ('unsupported','unknown'))
        self.assertEqual(unknown['facts']['details']['required_parts'][0]['missing_facts'], ['canl','canh'])
        self.assertEqual(unknown['pins']['outcome'], 'unknown')
        doc = read_json(self.root / 'prepared/facts.json')
        acquired = read_json(self.root / 'prepared/acquisition/manifest.json')['parts'][0]
        doc['facts'] = [dict(id=name.lower(), part_number='R-C1', kind='pin_function', pin=pin,
                             value=name, unit='', conditions='Owned synthetic fixture declaration',
                             source=dict(path='acquisition/' + acquired['path'], sha256=acquired['sha256'], page=1,
                                         locator='Owned row', quote=name + ' ' + pin))
                        for name, pin in [('CANL','6'), ('CANH','7')]]
        write_json(self.root / 'prepared/facts.json', doc)
        run, checks = self.review(scope, 'cited')
        self.assertEqual((checks['facts']['execution'], checks['facts']['outcome']), ('ok','pass'), checks)
        self.assertEqual(checks['pins']['outcome'], 'pass', checks)
        native = run / 'work/netlist/design.xml'
        self.assertEqual(bindings_from_native(native), [{'id':'U1','datasheet_url':fixture.url + '/pdf'}])
        self.assertEqual(validate_native_bindings(native, bindings)['U1']['datasheet'], fixture.url + '/pdf')
        bad = copy.deepcopy(bindings)
        bad['parts'][0]['datasheet_url'] = 'https://example.invalid/changed.pdf'
        with self.assertRaisesRegex(ValueError, 'differs'):
            validate_native_bindings(native, bad)
        # Successful acquisition files are mandatory immutable inputs, not a missing-record exception.
        pdf = self.root / 'prepared/acquisition' / acquired['path']
        saved = pdf.read_bytes()
        pdf.unlink()
        from agent_ee.candidate import capture
        (self.root / 'missing-success').mkdir()
        with self.assertRaises((FileNotFoundError, ValueError)):
            capture(self.root / 'scope.json', self.root / 'missing-success')
        pdf.write_bytes(saved)
        from agent_ee.run_record import verify
        sealed = run / 'inputs/acquisition' / acquired['path']
        self.assertTrue(sealed.is_file())
        sealed.write_bytes(saved + b'changed')
        with self.assertRaises(ValueError):
            verify(run)

    def test_no_url_html_and_timeout_each_retain_per_part_unknown(self):
        from agent_ee.datasheet_capture import prepare_datasheets
        fixture = self.capture_fixture
        preparations = []
        for name, url in [('absent',None), ('html',fixture.url + '/html'), ('timeout',fixture.url + '/slow')]:
            bindings = self.canonical_bindings(url, quantity=2)
            prepared = prepare_datasheets(bindings, {'U1':['pin'], 'U2':['pin']}, self.root / name,
                                           timeout=0.2 if name == 'timeout' else 3)
            self.assertEqual(prepared['outcome'], 'missing')
            preparations.append((name,prepared))
        fixture.stop_server()
        for name, prepared in preparations:
            run, checks = self.review(self.scope(prepared), name + '-run')
            facts = checks['facts']
            self.assertEqual((facts['execution'], facts['outcome']), ('unsupported','unknown'), facts)
            self.assertEqual([(r['reference'],r['reason']) for r in facts['details']['required_parts']],
                             [('U1','datasheet_missing'),('U2','datasheet_missing')])
            from agent_ee.baseline import load_baseline
            baseline = load_baseline(run)
            self.assertIsNotNone(baseline)

    def test_experimental_allnull_remains_unqualified_offline_unknown(self):
        from agent_ee.datasheet_parts import bindings_from_rung1
        from agent_ee.datasheet_capture import prepare_datasheets
        manifest, raw = rung1()
        (self.root / 'owned.csv').write_bytes(raw)
        bindings = bindings_from_rung1(manifest, self.root)
        prepared = prepare_datasheets(bindings, {'J1':['pins'], 'R1':['rating']}, self.root / 'experimental')
        self.capture_fixture.stop_server()
        self.assertEqual(self.capture_fixture.requests, [])
        _, checks = self.review(self.scope(prepared), 'experimental-run')
        self.assertEqual((checks['facts']['execution'], checks['facts']['outcome']), ('unsupported','unknown'))
        self.assertEqual(checks['facts']['details']['qualification'], 'experimental-unqualified')
        self.assertEqual([r['reference'] for r in checks['facts']['details']['required_parts']], ['J1','R1'])

    def test_selected_url_travels_through_rung1_emitter_and_native_netlist_into_capture(self):
        import hashlib
        import json
        from agent_ee import generate as g
        from agent_ee.connectivity_ir import resolve_intent
        from agent_ee.datasheet_capture import bindings_from_native, capture_datasheets
        from agent_ee.executor import execute
        from agent_ee.schematic_emit import PARTS_SCHEMA, emit
        from agent_ee.storage import read_json
        fixture = self.capture_fixture
        url = fixture.url + '/pdf'
        params = g.parse_spec((Path(__file__).resolve().parents[1] / 'fixtures/rung1/spec.md').read_text())
        # Stage-2 part records as a URL-bearing catalog would produce them; one URL shared by two picks.
        parts = {'R1': {'Footprint': 'Resistor_SMD:R_0805_2012Metric', 'LCSC': 'C1', 'Datasheet': url},
                 'C1': {'Footprint': 'Capacitor_SMD:C_0805_2012Metric', 'LCSC': 'C2', 'Datasheet': url},
                 'J1': {'Footprint': 'Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical',
                        'JLCPCB_Assembly': 'not_assembled', 'Datasheet': None,
                        'datasheet_obligation': 'missing:catalog_has_no_datasheet_column'}}
        libraries, captures = [], {}
        for name in g.SYMBOL_LIBRARIES:
            raw = (g.STOCK / 'symbols' / f'{name}.kicad_sym').read_bytes()
            libraries.append({'name': name, 'path': f'{name}.kicad_sym', 'sha256': hashlib.sha256(raw).hexdigest()})
            captures[f'{name}.kicad_sym'] = raw
        resolved = resolve_intent(g.connectivity_intent(params, parts, libraries), captures)
        self.assertEqual(resolved['outcome'], 'pass', resolved['findings'])
        outputs = emit(json.dumps(resolved).encode(), captures,
                       json.dumps({'schema': PARTS_SCHEMA, 'parts': parts}).encode(),
                       (g.FIXTURE_PROJECT / 'rc_filter.kicad_pro').read_bytes(), 'rung1_rc')
        project = self.root / 'emitted'
        project.mkdir()
        for name, data in outputs.items():
            (project / name).write_bytes(data)
        work = self.root / 'emitted-export'
        result = execute(project, work, ['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml',
                                         '--output', '/work/design.xml', '/candidate/rung1_rc.kicad_sch'], timeout=120)
        self.assertEqual(result['execution'], 'ok', result)
        self.assertIn('--unshare-net', read_json(work / 'execution.json')['sandbox_argv'])
        native = read_native_inventory(work / 'design.xml')['components']
        # KiCad exports the stock library's '~' placeholder as empty text.
        self.assertEqual({ref: c['datasheet'] for ref, c in native.items()}, {'R1': url, 'C1': url, 'J1': ''})
        manifest = capture_datasheets(bindings_from_native(work / 'design.xml'), self.root / 'captured')
        fixture.stop_server()
        self.assertEqual(fixture.requests, ['/pdf'])  # One fetch per unique URL.
        records = {p['id']: p for p in manifest['parts']}
        digest = hashlib.sha256(fixture.pdf).hexdigest()
        for ref in ('R1', 'C1'):
            self.assertEqual((records[ref]['url'], records[ref]['sha256'], records[ref]['missing']), (url, digest, False))
            self.assertIsNotNone(records[ref]['fetched_at'])
        self.assertEqual((records['J1']['missing'], records['J1']['fetched_at'], records['J1']['reason']),
                         (True, None, 'no datasheet URL'))
        self.assertEqual((self.root / 'captured' / records['R1']['path']).read_bytes(), fixture.pdf)
        self.assertEqual(manifest['outcome'], 'missing')

    def test_real_native_datasheet_and_population_flags(self):
        from test_native_binding import GEN
        from agent_ee.executor import execute
        from agent_ee.storage import read_json
        root = self.root / 'native-source'
        root.mkdir()
        url = 'https://example.invalid/original.pdf'
        text = GEN.schematic().replace('(property "Reference"', GEN.prop('Datasheet', url) + '(property "Reference"')
        self.assertIn(url, text)
        (root / 'binding.kicad_sch').write_text(text)
        (root / 'Owned.kicad_sym').write_bytes(GEN.library())
        (root / 'sym-lib-table').write_text('(sym_lib_table (lib (name "Owned")(type "KiCad")(uri "${KIPRJMOD}/Owned.kicad_sym")(options "")(descr "")))')
        work = self.root / 'native-export'
        result = execute(root, work, ['kicad-cli','sch','export','netlist','--format','kicadxml','--output','/work/design.xml','/candidate/binding.kicad_sch'], timeout=60)
        self.assertEqual(result['execution'], 'ok', result)
        self.assertIn('--unshare-net', read_json(work / 'execution.json')['sandbox_argv'])
        components = read_native_inventory(work / 'design.xml')['components']
        self.assertTrue(all(c['datasheet'] == url for c in components.values()))
        self.assertEqual((components['R2']['populated'],components['R2']['on_board']), (False, True))
        self.assertEqual(components['R3']['on_board'], False)
        self.assertEqual((components['R4']['in_bom'],components['R4']['on_board']), (False, True))

class RetainedProvenanceTests(unittest.TestCase):
    def test_rewritten_normalized_part_and_omitted_canonical_reference_refuse(self):
        from agent_ee.datasheet_parts import bindings_from_selection, validate_bindings
        helper = CanonicalAdapterTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        snap, sha, req, selection = helper.selected()
        original = bindings_from_selection(snap, sha, req, selection, {'resistors': ['R1','R2']})
        for mutation in (lambda d: d['parts'].pop(),
                         lambda d: d['parts'][0].update(datasheet_url='https://example.invalid/changed'),
                         lambda d: d['parts'][0].update(part_number='invented'),
                         lambda d: d['source']['selection']['parts'][0]['chosen'].update(lcsc='C99')):
            data = copy.deepcopy(original)
            mutation(data)
            with self.subTest(data=data), self.assertRaises(ValueError):
                validate_bindings(data)

    def test_acquisition_content_validation_after_rehash(self):
        from agent_ee.datasheet_parts import bindings_from_rung1
        from agent_ee.datasheet_capture import prepare_datasheets
        from agent_ee.datasheet_facts import load_facts
        from agent_ee.storage import digest, read_json, write_json
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, raw = rung1()
            (root / 'owned.csv').write_bytes(raw)
            bindings = bindings_from_rung1(manifest, root)
            prepare_datasheets(bindings, {'J1': ['pins'], 'R1': ['rating']}, root / 'prepared')
            prepared = root / 'prepared'
            envelope = read_json(prepared / 'facts.json')
            acquisition = read_json(prepared / 'acquisition/manifest.json')
            for index, mutate in enumerate((lambda d: d['parts'].pop(),
                    lambda d: d['parts'][0].update(id='unknown'),
                    lambda d: d['parts'][0].update(url='https://example.invalid/changed'),
                    lambda d: d['parts'][0].update(path='fake.pdf'),
                    lambda d: d['parts'][0].update(final_url={'url':'invented'}),
                    lambda d: d['parts'][0].update(fetched_at=123),
                    lambda d: d.update(outcome='captured'),
                    lambda d: d.update(bindings_sha256='0' * 64))):
                data = copy.deepcopy(acquisition)
                mutate(data)
                write_json(prepared / 'acquisition/manifest.json', data)
                envelope['acquisition']['sha256'] = digest(prepared / 'acquisition/manifest.json')
                write_json(prepared / 'facts.json', envelope)
                with self.subTest(index=index), self.assertRaises(ValueError):
                    load_facts(prepared, 'facts.json')

class PreparationLifecycleTests(unittest.TestCase):
    def test_cancel_or_write_failure_removes_owned_stage_then_retry(self):
        from unittest.mock import patch
        from agent_ee import datasheet_capture as dc
        from agent_ee.datasheet_parts import bindings_from_rung1
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, raw = rung1()
            (root / 'owned.csv').write_bytes(raw)
            bindings = bindings_from_rung1(manifest, root)
            required = {'J1':['pins'], 'R1':['rating']}
            real_capture = dc.capture_datasheets
            def cancelled(*args, **kwargs):
                real_capture(*args, **kwargs)
                raise KeyboardInterrupt('owned preparation cancellation')
            for boundary in ('cancel', 'write'):
                with self.subTest(boundary=boundary):
                    target = root / boundary
                    if boundary == 'cancel':
                        manager = patch.object(dc, 'capture_datasheets', cancelled)
                        error = KeyboardInterrupt
                    else:
                        manager = patch('agent_ee.storage.write_json', side_effect=OSError('owned write failure'))
                        error = OSError
                    with manager, self.assertRaises(error):
                        dc.prepare_datasheets(bindings, required, target)
                    self.assertFalse(target.exists())
                    self.assertEqual(list(root.glob('.datasheet-*')), [])
                    self.assertEqual(dc.prepare_datasheets(bindings, required, target)['outcome'], 'missing')
                    with self.assertRaises(FileExistsError):
                        dc.prepare_datasheets(bindings, required, target)

    def test_native_property_only_and_agreeing_representations(self):
        from test_native_binding import AdapterTests
        helper = AdapterTests()
        self.addCleanup(helper.doCleanups)
        url = 'https://example.invalid/owned.pdf'
        for dedicated in (False, True):
            def mutate(root):
                comp = root.find('./components/comp')
                field = comp.find("./fields/field[@name='Datasheet']")
                comp.find('fields').remove(field)
                ET.SubElement(comp, 'property', name='Datasheet', value=url)
                if dedicated:
                    ET.SubElement(comp, 'datasheet').text = url
                    ET.SubElement(comp.find('fields'), 'field', name='Datasheet').text = url
            with self.subTest(dedicated=dedicated):
                native = read_native_inventory(helper.mutated(mutate))
                self.assertEqual(native['components']['J1']['datasheet'], url)
