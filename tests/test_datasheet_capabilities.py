"""Capture-time coverage must not promote unverified citations to design checks."""
import copy
from pathlib import Path
import tempfile
import time
import unittest

from agent_ee.baseline import compile_baseline
from agent_ee.storage import digest, read_json, write_json
from agent_ee.candidate import capture
from agent_ee.baseline import freeze_evidence, verify_evidence
from agent_ee.checks import run_checks
from toolchain import require_toolchain

ROOT = Path(__file__).resolve().parents[1]


class DatasheetCapabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.inputs = Path(self.tmp.name)
        self.scope = read_json(ROOT / 'fixtures/datasheet-pins/control.scope.json')
        self.scope['schema'] = 'agent-ee.scope/3'
        self.scope['checks'] = self.scope['checks'][2:]
        req = self.scope['baseline']['requirements'][0]
        req.update(intent='acceptance', checks=['netlist', 'facts', 'pin-map'])
        self.facts = read_json(ROOT / 'fixtures/datasheet-pins/facts.json')
        write_json(self.inputs / 'facts.json', self.facts)
        (self.inputs / 'requirements.md').write_bytes(
            (ROOT / 'fixtures/datasheet-pins/requirements.md').read_bytes())
        self.files = {'facts.json': digest(self.inputs / 'facts.json'),
                      'TJA1051.pdf': 'a' * 64, 'requirements.md': digest(self.inputs / 'requirements.md'),
                      'project/pins.kicad_sch': 'c' * 64, 'project/pins.kicad_pcb': 'd' * 64}

    def compile(self):
        return compile_baseline(self.scope, self.files, self.inputs)

    def test_citation_is_prerequisite_not_defect_capability(self):
        baseline = self.compile()
        coverage = baseline['coverage']
        self.assertFalse(coverage['checks']['facts']['can_evaluate_bound'])
        pin = coverage['checks']['pin-map']
        self.assertEqual(pin['predicates'], ['pin-map:U1:6', 'pin-map:U1:7'])
        self.assertEqual(pin['prerequisites'], [
            {'check_id': 'facts', 'kind': 'datasheet_facts'},
            {'check_id': 'netlist', 'kind': 'netlist'}])
        self.assertEqual(coverage['requirements']['pins']['acceptance_checks'], ['pin-map'])
        self.assertEqual(pin['immutable_inputs'], ['TJA1051.pdf', 'facts.json'])
        self.assertIn('TJA1051.pdf', baseline['files'])
        self.scope['checks'].pop()
        self.scope['baseline']['requirements'][0]['checks'].remove('pin-map')
        with self.assertRaisesRegex(ValueError, 'no executable bound coverage'):
            self.compile()

    def test_inapplicable_missing_duplicate_and_forward_contracts_refuse(self):
        original = copy.deepcopy(self.scope)
        mutations = [
            lambda p: p.update(facts_check='absent'),
            lambda p: p.update(netlist_check='facts'),
            lambda p: p.update(symbols=[]),
            lambda p: p['symbols'][0].update(part_number='another part'),
            lambda p: p['symbols'][0].update(facts=['absent']),
            lambda p: p['symbols'][0].update(facts=['canl', 'canl']),
            lambda p: p['symbols'].append(copy.deepcopy(p['symbols'][0])),
        ]
        for mutation in mutations:
            self.scope = copy.deepcopy(original)
            mutation(self.scope['checks'][-1]['params'])
            with self.subTest(scope=self.scope), self.assertRaisesRegex(ValueError, 'unsupported capability'):
                self.compile()
        self.scope = copy.deepcopy(original)
        self.scope['checks'][1:] = reversed(self.scope['checks'][1:])
        with self.assertRaisesRegex(ValueError, 'forward'):
            self.compile()

    def test_missing_dependency_and_invalid_extractor_refuse_without_tools(self):
        del self.files['TJA1051.pdf']
        with self.assertRaisesRegex(ValueError, 'not captured'):
            self.compile()
        self.files['TJA1051.pdf'] = 'a' * 64
        self.scope['checks'][1]['params']['extractor']['sha256'] = 'invalid'
        with self.assertRaisesRegex(ValueError, 'extractor requires'):
            self.compile()

    def test_fact_dependencies_recompile_and_cannot_be_native_design(self):
        baseline = self.compile()
        self.facts['facts'][0]['source']['path'] = 'project/pins.kicad_sch'
        write_json(self.inputs / 'facts.json', self.facts)
        self.files['facts.json'] = digest(self.inputs / 'facts.json')
        with self.assertRaisesRegex(ValueError, 'native design cannot'):
            self.compile()
        self.assertEqual(baseline['schema'], 'agent-ee.baseline/2')


class NativeDatasheetCoverageTests(unittest.TestCase):
    def test_scope3_control_and_seed_retain_executable_pin_coverage(self):
        require_toolchain(native=True, tools=('pdftotext',))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = ROOT / 'fixtures/datasheet-pins'
            for name, outcome in [('control', 'pass'), ('swapped', 'defect')]:
                with self.subTest(name=name):
                    scope = read_json(fixture / f'{name}.scope.json')
                    scope['schema'] = 'agent-ee.scope/3'
                    scope['baseline']['requirements'][0]['intent'] = 'acceptance'
                    for item in scope['inputs']:
                        item['path'] = str((fixture / item['path']).resolve())
                    write_json(root / 'scope.json', scope)
                    run = root / name
                    run.mkdir()
                    capture(root / 'scope.json', run)
                    checks = run_checks(run, time.monotonic() + 90)
                    by_id = {c['id']: c for c in checks}
                    self.assertEqual(by_id['pin-map']['outcome'], outcome, by_id)
                    self.assertTrue(all(c['execution'] == 'ok' for c in checks), checks)
                    for cid in ('erc', 'drc', 'facts'):
                        self.assertEqual(by_id[cid]['outcome'], 'pass', by_id[cid])
                    freeze_evidence(run, checks)
                    verify_evidence(run, checks, required=True)
                    from agent_ee.cli import run_review, verify
                    reviewed = root / (name + '-review')
                    self.assertEqual(run_review(root / 'scope.json', reviewed, 90, checks_only=True), 1)
                    result = verify(reviewed)
                    self.assertEqual(len(result['checks']), len(scope['checks']), result)
                    self.assertEqual(result['checks'][-1]['outcome'], outcome, result)


class EnvelopeCapabilityTests(unittest.TestCase):
    """Scope/3 retains the complete facts/2 source closure without new authority."""

    def setUp(self):
        from test_datasheet_integration import OfflineNativeIntegrationTests
        self.fixture = OfflineNativeIntegrationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root

    def prepare(self, *, quantity=2, cite_second=True, distinct_second=False):
        from agent_ee.datasheet_capture import prepare_datasheets
        url = self.fixture.capture_fixture.url + '/pdf'
        if distinct_second:
            import json
            from test_part_selection import SelectionTests
            from agent_ee.datasheet_parts import bindings_from_selection
            from agent_ee.part_selection import select_parts
            catalog = SelectionTests()
            catalog.setUp()
            self.addCleanup(catalog.doCleanups)
            rows = [catalog.row(lcsc, package=package, attributes=json.dumps({
                'resistance': '10 kohm +/-1%', 'voltage': '50 V', 'Datasheet': address}))
                for lcsc, package, address in [('C1', '0402', url),
                    ('C2', '0603', self.fixture.capture_fixture.url + '/redirect')]]
            snapshot, sha = catalog.snapshot(rows)
            requirements = catalog.requirements(id='first')
            requirements['parts'].append(dict(requirements['parts'][0], id='second', package='0603'))
            selected = select_parts(snapshot, sha, requirements)
            bindings = bindings_from_selection(snapshot, sha, requirements, selected,
                                               {'first': ['U1'], 'second': ['U2']})
        else:
            bindings = self.fixture.canonical_bindings(url, quantity=quantity)
        required = {'U1': ['canl', 'canh']}
        if quantity == 2:
            required['U2'] = ['second']
        prepared = prepare_datasheets(bindings, required, self.root / 'prepared')
        self.fixture.capture_fixture.stop_server()
        doc = read_json(self.root / 'prepared/facts.json')
        acquired = read_json(self.root / 'prepared/acquisition/manifest.json')['parts'][0]
        source = dict(path='acquisition/' + acquired['path'], sha256=acquired['sha256'], page=1,
                      locator='Owned synthetic row', quote='CANL 6')
        doc['facts'] = [dict(id=fid, part_number='R-C1', kind='pin_function', pin=pin,
                             value=name, unit='', conditions='Synthetic software applicability only',
                             source=dict(source, quote=name + ' ' + pin))
                        for fid, name, pin in [('canl', 'CANL', '6'), ('canh', 'CANH', '7')]]
        if quantity == 2 and cite_second:
            doc['facts'].append(dict(doc['facts'][0], id='second'))
        write_json(self.root / 'prepared/facts.json', doc)
        scope = self.fixture.scope(prepared)
        scope['schema'] = 'agent-ee.scope/3'
        scope['checks'].insert(0, dict(id='netlist', kind='netlist'))
        scope['checks'].append(dict(id='pins', kind='pin_map', params=dict(
            facts_check='facts', netlist_check='netlist',
            symbols=[dict(reference='U1', part_number='R-C1', facts=['canl', 'canh'])])))
        scope['baseline']['requirements'][0].update(intent='acceptance', checks=['netlist', 'facts', 'pins'])
        pdfs = {'acquisition/' + r['path'] for r in
                read_json(self.root / 'prepared/acquisition/manifest.json')['parts'] if not r['missing']}
        return scope, {'facts.json', 'bindings.json', 'acquisition/manifest.json'} | pdfs

    def compile(self, scope):
        inputs = self.root / 'prepared'
        (inputs / 'requirements.md').write_bytes((self.root / 'requirements.md').read_bytes())
        files = {p.relative_to(inputs).as_posix(): digest(p) for p in inputs.rglob('*') if p.is_file()}
        files.update({'requirements.md': digest(inputs / 'requirements.md'),
                      'project/pins.kicad_sch': 'a' * 64, 'project/pins.kicad_pcb': 'b' * 64})
        return compile_baseline(scope, files, inputs), files

    def test_live_and_historical_acquisition_labels_in_capabilities_and_pin_map(self):
        import shutil
        from agent_ee.datasheet_capture import import_historical_pdf
        scope, _ = self.prepare(quantity=1)
        # Compare the same native control before and after the offline import.
        inputs = self.root / 'prepared'
        acq_path = inputs / 'acquisition/manifest.json'
        live = read_json(acq_path)['parts'][0]
        for historical in (False, True):
            if historical:
                note = inputs / 'sources.json'
                write_json(note, [dict(file=live['path'], sha256=live['sha256'], url=live['url'],
                                      captured_at_utc='2026-01-01T00:00:00Z')])
                output = inputs / 'import'
                import_historical_pdf(inputs / 'acquisition' / live['path'], inputs,
                    dict(path='sources.json', sha256=digest(note), entry=0), 'U1', output)
                shutil.rmtree(inputs / 'acquisition')
                output.rename(inputs / 'acquisition')
                doc = read_json(inputs / 'facts.json')
                doc['acquisition']['sha256'] = digest(acq_path)
                # The importer uses the content hash as its PDF filename.
                pdf = read_json(acq_path)['parts'][0]
                for fact in doc['facts']:
                    fact['source']['path'] = 'acquisition/' + pdf['path']
                write_json(inputs / 'facts.json', doc)
            method, redirect = ('historical_import', 'unknown') if historical else ('live', 'observed')
            baseline, _ = self.compile(scope)
            with self.subTest(historical=historical):
                for cid in ('facts', 'pins'):
                    receipt = baseline['coverage']['checks'][cid]['acquisitions']['U1']
                    self.assertEqual((receipt['acquisition_method'], receipt['redirect_provenance']), (method, redirect))
                run, checks = self.fixture.review(scope, 'label-' + str(historical))
                self.assertEqual(checks['facts']['outcome'], 'pass', checks)
                self.assertEqual(checks['pins']['outcome'], 'pass', checks)
                for receipt in (checks['pins']['details'], read_json(run / 'work/pins/pin-map.json')):
                    self.assertEqual(receipt['acquisitions']['U1']['acquisition_method'], method)
                    self.assertEqual(receipt['acquisitions']['U1']['redirect_provenance'], redirect)

    def test_complete_closure_in_each_descriptor_and_baseline(self):
        scope, closure = self.prepare()
        baseline, _ = self.compile(scope)
        for cid in ('facts', 'pins'):
            descriptor = baseline['coverage']['checks'][cid]
            self.assertEqual(set(descriptor['immutable_inputs']), closure)
            self.assertEqual(descriptor['status'], 'supported')
        facts = baseline['coverage']['checks']['facts']
        self.assertEqual(facts['predicates'], [])
        self.assertFalse(facts['can_evaluate_bound'])
        self.assertEqual(baseline['coverage']['checks']['pins']['predicates'], ['pin-map:U1:6', 'pin-map:U1:7'])
        self.assertEqual(set(baseline['files']), closure | {'requirements.md'})

    def test_inventory_membership_and_disk_bytes_are_both_required(self):
        from agent_ee.capabilities import describe_check
        scope, closure = self.prepare()
        _, files = self.compile(scope)
        inputs = self.root / 'prepared'
        for path in sorted(closure):
            for cid in ('facts', 'pins'):
                spec = next(c for c in scope['checks'] if c['id'] == cid)
                with self.subTest(path=path, cid=cid, boundary='inventory'):
                    descriptor = describe_check(spec, check_specs=scope['checks'],
                        files={p: sha for p, sha in files.items() if p != path}, scope=scope, inputs=inputs)
                    self.assertEqual(descriptor['status'], 'unsupported', descriptor)
                    self.assertIn('not captured', str(descriptor['unsupported_reasons']))
                raw = (inputs / path).read_bytes()
                (inputs / path).unlink()
                try:
                    with self.subTest(path=path, cid=cid, boundary='disk'):
                        descriptor = describe_check(spec, check_specs=scope['checks'], files=files,
                                                    scope=scope, inputs=inputs)
                        self.assertEqual(descriptor['status'], 'unsupported', descriptor)
                finally:
                    (inputs / path).write_bytes(raw)

    def test_rehashed_incomplete_or_inapplicable_documents_refuse(self):
        scope, _ = self.prepare()
        inputs = self.root / 'prepared'
        original = {p: read_json(inputs / p) for p in
                    ('facts.json', 'bindings.json', 'acquisition/manifest.json')}
        cases = [
            ('facts.json', lambda d: d['required_parts'].pop()),
            ('facts.json', lambda d: d['required_parts'][0].update(part_number='wrong')),
            ('facts.json', lambda d: d['facts'][0].update(part_number='wrong')),
            ('bindings.json', lambda d: d['parts'].pop()),
            ('bindings.json', lambda d: d['parts'][0].update(datasheet_url='https://example.invalid/changed')),
            ('acquisition/manifest.json', lambda d: d['parts'].pop()),
            ('acquisition/manifest.json', lambda d: d['parts'][0].update(url='https://example.invalid/changed')),
            ('acquisition/manifest.json', lambda d: d['parts'][0].update(path='absent.pdf')),
            ('acquisition/manifest.json', lambda d: d['parts'][0].update(sha256='0' * 64)),
            ('facts.json', lambda d: d['bindings'].update(path='../bindings.json')),
        ]
        for path, mutate in cases:
            for rel, doc in original.items():
                write_json(inputs / rel, doc)
            data = copy.deepcopy(original[path])
            mutate(data)
            write_json(inputs / path, data)
            if path != 'facts.json':
                envelope = read_json(inputs / 'facts.json')
                envelope['bindings' if path == 'bindings.json' else 'acquisition']['sha256'] = digest(inputs / path)
                write_json(inputs / 'facts.json', envelope)
            with self.subTest(path=path, data=data), self.assertRaisesRegex(ValueError, 'unsupported capability'):
                self.compile(scope)

    def test_dependency_cannot_alias_native_or_staged_mutable_subject(self):
        scope, closure = self.prepare()
        for path in sorted(closure):
            for native in ('schematic', 'board'):
                aliased = copy.deepcopy(scope)
                aliased[native] = path
                with self.subTest(path=path, native=native), self.assertRaisesRegex(ValueError, 'native design cannot'):
                    self.compile(aliased)
            staged = copy.deepcopy(scope)
            staged['schematic'] = path
            staged['mutable_inputs'] = [path]
            staged['stages'] = dict(schema='agent-ee.stages/1', order=['schematic'], finding_retry_limit=2,
                subjects={'schematic': dict(path=path, kind='symbols', references=['U1'])})
            for check in staged['checks']:
                check['stage'] = 'schematic'
            with self.subTest(path=path, boundary='stage'), self.assertRaisesRegex(ValueError, 'stage subjects cannot'):
                self.compile(staged)

    def independent_gate(self, scope):
        scope['checks'] = [dict(id='drc', kind='drc'), next(c for c in scope['checks'] if c['id'] == 'facts')]
        req = scope['baseline']['requirements'][0]
        req.update(intent='informational', checks=['facts'])
        scope['baseline']['requirements'].append(dict(req, id='native', intent='acceptance',
            statement='Owned board has no native rule violations', checks=['drc']))
        scope['inputs'].append(dict(path='/usr/share/kicad/template', at='stock/template',
                                    include=['sym-lib-table', 'fp-lib-table']))
        return scope

    def test_uncited_successful_pdf_stays_immutable_and_unknown(self):
        scope, closure = self.prepare(quantity=1)
        path = self.root / 'prepared/facts.json'
        doc = read_json(path)
        doc['facts'] = []
        write_json(path, doc)
        scope = self.independent_gate(scope)
        baseline, _ = self.compile(scope)
        self.assertEqual(set(baseline['coverage']['checks']['facts']['immutable_inputs']), closure)
        run, checks = self.fixture.review(scope, 'uncited-run')
        self.assertEqual(checks['drc']['outcome'], 'pass', checks)
        self.assertEqual((checks['facts']['execution'], checks['facts']['outcome']), ('unsupported', 'unknown'))
        self.assertEqual(checks['facts']['details']['required_parts'][0]['missing_facts'], ['canl', 'canh'])
        self.assert_no_witness(run, 'coverage', 'facts')

    def assert_no_witness(self, run, rid, cid):
        from agent_ee.baseline import validate_witnesses
        with self.assertRaises(ValueError):
            validate_witnesses(run, [dict(requirement_id=rid, check_id=cid)],
                               [dict(path=f'checks/{cid}.json', locator='outcome')])

    def test_all_missing_informational_parts_with_real_independent_acceptance_gate(self):
        from test_datasheet_integration import rung1
        from agent_ee.datasheet_parts import bindings_from_rung1
        from agent_ee.datasheet_capture import prepare_datasheets
        manifest, raw = rung1()
        (self.root / 'owned.csv').write_bytes(raw)
        bindings = bindings_from_rung1(manifest, self.root)
        prepared = prepare_datasheets(bindings, {'J1': ['pins'], 'R1': ['rating']}, self.root / 'prepared')
        self.fixture.capture_fixture.stop_server()
        self.assertEqual(self.fixture.capture_fixture.requests, [])
        scope = self.fixture.scope(prepared)
        scope['schema'] = 'agent-ee.scope/3'
        scope = self.independent_gate(scope)
        baseline, _ = self.compile(scope)
        descriptor = baseline['coverage']['checks']['facts']
        self.assertEqual(descriptor['immutable_inputs'], ['acquisition/manifest.json', 'bindings.json', 'facts.json'])
        self.assertEqual(descriptor['predicates'], [])
        self.assertFalse(descriptor['can_evaluate_bound'])
        run, checks = self.fixture.review(scope, 'all-missing-run')
        self.assertEqual((checks['drc']['execution'], checks['drc']['outcome']), ('ok', 'pass'), checks)
        self.assertEqual((checks['facts']['execution'], checks['facts']['outcome']), ('unsupported', 'unknown'), checks)
        self.assertEqual([(r['reference'], r['reason']) for r in checks['facts']['details']['required_parts']],
                         [('J1', 'datasheet_missing'), ('R1', 'datasheet_missing')])
        self.assertEqual(checks['facts']['details']['qualification'], 'experimental-unqualified')
        self.assert_no_witness(run, 'coverage', 'facts')
        bad = copy.deepcopy(scope)
        bad['baseline']['requirements'][0]['intent'] = 'acceptance'
        with self.assertRaisesRegex(ValueError, 'no executable bound coverage'):
            self.compile(bad)
        bad = copy.deepcopy(scope)
        bad['checks'].insert(0, dict(id='netlist', kind='netlist'))
        bad['checks'].append(dict(id='pins', kind='pin_map', params=dict(facts_check='facts', netlist_check='netlist',
            symbols=[dict(reference='R1', part_number='unknown', facts=['absent'])])))
        bad['baseline']['requirements'][0]['checks'] += ['netlist', 'pins']
        with self.assertRaisesRegex(ValueError, 'unsupported capability pins'):
            self.compile(bad)

    def test_partial_second_part_missing_propagates_unknown_to_pin_map(self):
        scope, closure = self.prepare(cite_second=False, distinct_second=True)
        baseline, _ = self.compile(scope)
        for cid in ('facts', 'pins'):
            self.assertEqual(set(baseline['coverage']['checks'][cid]['immutable_inputs']), closure)
        pdfs = {p for p in closure if p.endswith('.pdf')}
        self.assertEqual(len(pdfs), 2)
        cited = {f['source']['path'] for f in read_json(self.root / 'prepared/facts.json')['facts']}
        self.assertEqual(len(pdfs - cited), 1)
        run, checks = self.fixture.review(scope, 'partial-run')
        self.assertEqual(checks['netlist']['execution'], 'ok', checks)
        self.assertEqual((checks['facts']['execution'], checks['facts']['outcome']), ('unsupported', 'unknown'))
        self.assertEqual([(r['reference'], r['status'], r['missing_facts'])
                          for r in checks['facts']['details']['required_parts']],
                         [('U1', 'verified', []), ('U2', 'unsupported', ['second'])])
        self.assertEqual((checks['pins']['execution'], checks['pins']['outcome']), ('unsupported', 'unknown'))
        self.assert_no_witness(run, 'coverage', 'pins')

    def test_positive_seed_witness_candidate_seal_and_mutation_boundaries(self):
        import shutil
        from agent_ee.baseline import load_baseline, validate_witnesses
        from agent_ee.cli import verify
        scope, closure = self.prepare()
        project = self.root / 'project'
        shutil.copytree(ROOT / 'fixtures/datasheet-pins/control', project)
        next(i for i in scope['inputs'] if i['at'] == 'project')['path'] = str(project)
        control, checks = self.fixture.review(scope, 'positive-run')
        self.assertTrue(all(c['execution'] == 'ok' for c in checks.values()), checks)
        self.assertEqual(checks['facts']['outcome'], 'pass')
        self.assertEqual(checks['pins']['outcome'], 'pass')
        self.assertEqual([r['status'] for r in checks['facts']['details']['required_parts']], ['verified', 'verified'])
        baseline = load_baseline(control)
        self.assertTrue(closure <= baseline['files'].keys())
        self.assert_no_witness(control, 'coverage', 'facts')
        self.assert_no_witness(control, 'coverage', 'pins')

        # Change the native cached and library pin names together; facts/PDF stay fixed.
        for path in (project / 'pins.kicad_sch', project / 'pins.kicad_sym'):
            raw = path.read_text()
            self.assertIn('(name "CANL"', raw)
            path.write_text(raw.replace('(name "CANL"', '(name "CANX"'))
        seed, seeded = self.fixture.review(scope, 'seed-run')
        self.assertEqual(seeded['facts']['outcome'], 'pass', seeded)
        self.assertEqual((seeded['pins']['execution'], seeded['pins']['outcome']), ('ok', 'defect'), seeded)
        self.assertEqual(load_baseline(seed)['baseline_id'], baseline['baseline_id'])
        self.assertNotEqual(read_json(seed / 'candidate.json')['candidate_id'], read_json(control / 'candidate.json')['candidate_id'])
        evidence = [dict(path='checks/pins.json', locator='outcome')]
        witness = [dict(requirement_id='coverage', check_id='pins')]
        validate_witnesses(seed, witness, evidence)
        report = read_json(seed / 'work/pins/pin-map.json')
        self.assertEqual([(c['reference'], c['pin'], c['expected'], c['native_name'])
                          for c in report['comparisons'] if not c['matches']],
                         [('U1', '6', 'CANL', 'CANX')])
        self.assertIn('pin-map:U1:6', load_baseline(seed)['coverage']['checks']['pins']['predicates'])
        self.assert_no_witness(seed, 'coverage', 'facts')
        with self.assertRaisesRegex(ValueError, 'witness requires'):
            validate_witnesses(seed, [dict(witness[0], predicate='pin-map:U1:7')], evidence)

        # Original source drift has no effect on the retained candidate.
        source = self.root / 'prepared/bindings.json'
        source.write_bytes(source.read_bytes() + b'\n')
        with self.assertRaisesRegex(ValueError, 'source inputs changed'):
            verify(control)
        self.assertEqual(verify(control, current_source=False)['checks'][-1]['outcome'], 'pass')
        for rel in sorted(closure):
            path = control / 'inputs' / rel
            raw = path.read_bytes()
            path.write_bytes(raw + b'changed')
            try:
                with self.subTest(path=rel), self.assertRaises(ValueError):
                    verify(control, current_source=False)
            finally:
                path.write_bytes(raw)
        self.assertEqual(verify(control, current_source=False)['checks'][-1]['outcome'], 'pass')

        seal_path = seed / 'work/verification/evidence.json'
        seal = read_json(seal_path)
        for field in ('candidate_id', 'baseline_id'):
            changed = copy.deepcopy(seal)
            changed[field] = '0' * 64
            write_json(seal_path, changed)
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_witnesses(seed, witness, evidence)
            with self.assertRaises(ValueError):
                verify(seed, current_source=False)
        write_json(seal_path, seal)
        artifact = seed / 'work/pins/pin-map.json'
        raw = artifact.read_bytes()
        artifact.write_bytes(raw + b'\n')
        with self.assertRaisesRegex(ValueError, 'evidence changed'):
            validate_witnesses(seed, witness, evidence)
        with self.assertRaises(ValueError):
            verify(seed, current_source=False)
        artifact.write_bytes(raw)
        validate_witnesses(seed, witness, evidence)
        # A control's passing check result cannot be transplanted into a failed candidate.
        result_path = seed / 'checks/pins.json'
        result = result_path.read_bytes()
        result_path.write_bytes((control / 'checks/pins.json').read_bytes())
        with self.assertRaises(ValueError):
            verify(seed, current_source=False)
        with self.assertRaises(ValueError):
            validate_witnesses(seed, witness, evidence)
        result_path.write_bytes(result)
        self.assertEqual(verify(seed, current_source=False)['checks'][-1]['outcome'], 'defect')
