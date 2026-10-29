"""Parts membership contract: original fixture data is not real part approval."""
import hashlib
import unittest


def subset_generator():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location('owned_parts_subset',
        Path(__file__).parent / 'fixtures/parts-subset/generate.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SchemaTests(unittest.TestCase):
    def test_experimental_manifest_is_not_the_versioned_contract(self):
        from agent_ee.parts_manifest import validate_manifest
        with self.assertRaises(ValueError):
            validate_manifest({'schema': 'agent-ee.rung1-parts-manifest/0-experimental'})

class MembershipTests(unittest.TestCase):
    def test_required_dnp_is_a_defect(self):
        from agent_ee.parts_subset import compare_inventory
        # Comparator consumes validated inputs; fixture construction is shared below.
        document, graph = comparison_fixture()
        graph['components']['R1']['populated'] = False
        report = compare_inventory(document, graph, [])
        self.assertEqual(report['outcome'], 'defect', report)
        self.assertEqual(report['matches'][0]['findings'][0]['code'], 'required_unpopulated')


def comparison_fixture():
    from tests.test_native_binding import native_graph
    graph = native_graph()
    graph['components'] = {'R1': graph['components']['R1']}
    comp = graph['components']['R1']
    comp['properties'].update(LCSC='C1', MPN='R-C1', Manufacturer='Owned fixture')
    part = dict(id='resistor', role='filter', required=True, instances=[dict(sheet='/', reference='R1')],
                selection_id='R1', lib_id='Owned:R', footprint='Owned:R2', lcsc='C1', mpn='R-C1',
                manufacturer='Owned fixture', stock=1000,
                datasheet=dict(status='unavailable', required=False, reason='fixture'))
    return dict(parts=[part], property_fields=dict(lcsc='LCSC', mpn='MPN', manufacturer='Manufacturer')), graph

class ComparisonBoundaryTests(unittest.TestCase):
    def compare(self, mutate):
        import copy
        from agent_ee.parts_subset import compare_inventory
        document, graph = comparison_fixture()
        mutate(document, graph)
        inputs = copy.deepcopy((document, graph))
        result = compare_inventory(document, graph, [])
        self.assertEqual((document, graph), inputs)
        return result

    def test_exact_identities_and_informative_footprint_failure(self):
        self.assertEqual(self.compare(lambda d, g: None)['outcome'], 'pass')
        for field in ('lib_id', 'footprint', 'LCSC', 'MPN', 'Manufacturer'):
            with self.subTest(field=field):
                def change(d, g):
                    target = g['components']['R1']
                    (target if field in ('lib_id', 'footprint') else target['properties'])[field] = 'wrong'
                report = self.compare(change)
                self.assertEqual(report['outcome'], 'defect', report)
                finding = report['matches'][0]['findings'][0]
                self.assertEqual(finding['native'], 'wrong')
                if field == 'footprint':
                    self.assertEqual(finding['declared'], 'Owned:R2')
                    self.assertEqual(report['matches'][0]['reference'], 'R1')

    def test_missing_required_optional_and_capacity(self):
        self.assertEqual(self.compare(lambda d, g: g['components'].clear())['outcome'], 'defect')
        def optional(d, g):
            d['parts'][0]['required'] = False
            g['components'].clear()
        self.assertEqual(self.compare(optional)['outcome'], 'unknown')
        def extra(d, g):
            import copy
            g['components']['R9'] = copy.deepcopy(g['components']['R1'])
        report = self.compare(extra)
        self.assertEqual(report['outcome'], 'defect')
        self.assertEqual(report['matches'][1]['findings'][0]['code'], 'unexpected_populated_instance')

    def test_optional_absence_does_not_waive_other_required_parts(self):
        import copy
        from agent_ee.parts_subset import compare_inventory
        document, graph = comparison_fixture()
        optional = copy.deepcopy(document['parts'][0])
        optional.update(id='optional', selection_id='optional', required=False,
                        instances=[dict(sheet='/', reference='R2')])
        document['parts'].append(optional)
        report = compare_inventory(document, graph, [])
        self.assertEqual(report['outcome'], 'pass', report)
        self.assertEqual(report['matches'][1]['status'], 'optional_absent')
        graph['components']['R2'] = copy.deepcopy(graph['components']['R1'])
        graph['components']['R2']['footprint'] = 'wrong'
        self.assertEqual(compare_inventory(document, graph, [])['outcome'], 'defect')
        optional['required'] = True
        del graph['components']['R2']
        self.assertEqual(compare_inventory(document, graph, [])['outcome'], 'defect')

    def test_bom_assembly_dnp_property_and_off_board_do_not_waive_population(self):
        def excluded(d, g):
            comp = g['components']['R1']
            comp['in_bom'] = False
            comp['properties'].update(JLCPCB_Assembly='not_assembled', DNP='true')
            comp['footprint'] = 'Owned:Wrong'
        self.assertEqual(self.compare(excluded)['outcome'], 'defect')
        self.assertEqual(self.compare(lambda d, g: g['components']['R1'].update(on_board=False))['outcome'], 'defect')

    def test_missing_native_property_and_mixed_coverage_remain_unknown(self):
        def change(d, g):
            del g['components']['R1']['properties']['MPN']
            g['components']['R1']['footprint'] = 'wrong'
        report = self.compare(change)
        self.assertEqual((report['execution'], report['outcome']), ('unsupported', 'unknown'))
        self.assertIn('footprint_mismatch', {f['code'] for f in report['matches'][0]['findings']})

    def test_child_sheet_identity_cannot_satisfy_root_selector(self):
        report = self.compare(lambda d, g: g['components']['R1'].update(sheet='/child/'))
        self.assertEqual(report['outcome'], 'defect')
        self.assertEqual(len(report['matches']), 2)


def captured_fixture(test, root, trio=False):
    """Original synthetic declarations and catalog; never real owner part approval."""
    import copy
    from tests.test_part_selection import SelectionTests
    from tests.test_native_binding import GEN, LIBRARY
    from agent_ee.part_selection import selection_check
    from agent_ee.storage import write_json, digest, identity
    helper = SelectionTests()
    helper.setUp()
    test.addCleanup(helper.doCleanups)
    rows = [helper.row()]
    if trio:
        import json
        rows += [helper.row('C2', mpn='C-C2', category='capacitor',
                    attributes=json.dumps({'capacitance': '100 nF +/-10%', 'voltage': '25 V'})),
                 helper.row('C3', mpn='J-C3', category='connector', package='TH',
                    attributes=json.dumps({'voltage': '50 V'}))]
    snapshot, pin = helper.snapshot(rows)
    import shutil
    shutil.copytree(snapshot, root / 'snapshot')
    def put(path, data):
        dest = root / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, bytes):
            dest.write_bytes(data)
        else:
            write_json(dest, data)
        return dict(path=path, sha256=digest(dest))
    requirement = helper.requirements()
    if trio:
        requirement['parts'] += [dict(id='C1', category='capacitor', package='0402', quantity_per_board=1,
            constraints={'capacitance': dict(kind='within', nominal='100 nF', tolerance_percent='10'),
                         'voltage': dict(kind='at_least', min='25 V')}),
            dict(id='J1', category='connector', package='TH', quantity_per_board=1,
                 constraints={'voltage': dict(kind='at_least', min='50 V')})]
    selection = selection_check(root / 'snapshot', pin, requirement)
    test.assertEqual(selection['outcome'], 'pass', selection)
    requirements = put('selection-requirements.json', requirement)
    selected = put('selection.json', selection)
    fp = {'Owned.pretty/R2.kicad_mod': GEN.footprints()['Owned.pretty/R2.kicad_mod']}
    if trio:
        fp = subset_generator().trio_footprints()
    library_data = subset_generator().trio_library() if trio else GEN.library()
    library = dict(LIBRARY, sha256=hashlib.sha256(library_data).hexdigest())
    for path, data in fp.items():
        put(path, data)
    put('Owned.kicad_sym', library_data)
    cad = put('cad.json', GEN.manifest(fp))
    assignment_doc = dict(schema='agent-ee.footprint-assignments-input/1',
        libraries=[copy.deepcopy(library)], components=[dict(reference='resistor', lib_id='Owned:R',
        footprint='Owned:R2', assembly='smd', connector=None)])
    if trio:
        assignment_doc['components'] += [dict(reference='capacitor', lib_id='Owned:C', footprint='Owned:R2',
            assembly='smd', connector=None), dict(reference='header', lib_id='Owned:Conn3',
            footprint='Owned:PinHeader_1x03', assembly='through_hole', connector=dict(rows=1, columns=3))]
    assignments = put('assignments.json', assignment_doc)
    declaration = put('requirements.md', b'Owned fixture: R1 is the one required resistor.\n')
    declaration['locator'] = 'line 1'
    part = comparison_fixture()[0]['parts'][0]
    sheet = put('datasheet.txt', b'Owned synthetic resistor datasheet, no vendor claim.\n')
    provenance = put('datasheet-source.txt', b'Original test fixture, not acquired vendor evidence.\n')
    provenance['locator'] = 'line 1'
    part['datasheet'] = dict(status='captured', required=True, artifact=sheet, provenance=provenance)
    document = dict(schema='agent-ee.parts/1', declaration_source=declaration,
        catalog=dict(snapshot='snapshot', manifest_sha256=pin,
                     database_sha256=digest(root / 'snapshot/catalog.sqlite3'), snapshot_date='2026-10-17'),
        selection=dict(requirements=requirements, result=selected),
        cad=dict(manifest=cad, assignments=assignments,
                 members_sha256=identity(sorted([
                    dict(path='Owned.kicad_sym', sha256=library['sha256']),
                    *[dict(path=p, sha256=digest(root / p)) for p in fp]],
                    key=lambda m: m['path']))),
        property_fields=dict(lcsc='LCSC', mpn='MPN', manufacturer='Manufacturer'),
        population='native-dnp-optional-only/1', parts=[part])
    if trio:
        for name, ref, symbol, footprint, lcsc, mpn in (
            ('capacitor', 'C1', 'Owned:C', 'Owned:R2', 'C2', 'C-C2'),
            ('header', 'J1', 'Owned:Conn3', 'Owned:PinHeader_1x03', 'C3', 'J-C3')):
            added = copy.deepcopy(part)
            added.update(id=name, role=name, selection_id=ref, lib_id=symbol, footprint=footprint,
                         lcsc=lcsc, mpn=mpn, instances=[dict(sheet='/', reference=ref)])
            document['parts'].append(added)
    put('parts.json', document)
    return document


class CapturedManifestTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        temp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(temp)
        self.document = captured_fixture(self, self.root)

    def test_complete_closure_selection_replay_and_preparation_do_not_mutate(self):
        import copy
        from agent_ee.parts_manifest import qualify_manifest, prepare_manifest
        before = copy.deepcopy(self.document)
        result = qualify_manifest(self.document, self.root)
        self.assertEqual(result['missing_provenance'], [])
        self.assertEqual(result['selection']['parts'][0]['chosen']['lcsc'], 'C1')
        self.assertIn('Owned.kicad_sym', result['dependencies'])
        self.assertIn('datasheet-source.txt', result['dependencies'])
        self.assertEqual(prepare_manifest(self.document, self.root), before)
        self.assertEqual(self.document, before)

    def test_rehashed_forged_selection_does_not_replace_canonical_rederivation(self):
        from agent_ee.storage import read_json, write_json, digest
        from agent_ee.parts_manifest import qualify_manifest
        path = self.root / 'selection.json'
        data = read_json(path)
        data['parts'][0]['chosen']['mpn'] = 'FORGED'
        write_json(path, data)
        self.document['selection']['result']['sha256'] = digest(path)
        self.document['parts'][0]['mpn'] = 'FORGED'
        with self.assertRaisesRegex(ValueError, 'rederivation'):
            qualify_manifest(self.document, self.root)

    def test_required_missing_provenance_is_explicit(self):
        from agent_ee.parts_manifest import qualify_manifest
        self.document['parts'][0]['datasheet'] = dict(status='unavailable', required=True, reason='not supplied')
        result = qualify_manifest(self.document, self.root)
        self.assertEqual(result['missing_provenance'], [dict(part_id='resistor', reason='not supplied')])

    def test_capacity_and_duplicate_selectors_fail(self):
        import copy
        from agent_ee.parts_manifest import qualify_manifest, validate_manifest
        self.document['parts'][0]['instances'].append(dict(sheet='/child/', reference='R2'))
        with self.assertRaisesRegex(ValueError, 'capacity'):
            qualify_manifest(self.document, self.root)
        self.document['parts'][0]['instances'][1] = copy.deepcopy(self.document['parts'][0]['instances'][0])
        with self.assertRaisesRegex(ValueError, 'selector'):
            validate_manifest(self.document)

    def test_cad_datasheet_declaration_and_link_mutations_fail_closed(self):
        from agent_ee.parts_manifest import dependency_closure
        for name in ('Owned.kicad_sym', 'Owned.pretty/R2.kicad_mod', 'datasheet.txt', 'requirements.md'):
            path = self.root / name
            original = path.read_bytes()
            path.write_bytes(original + b' ')
            with self.subTest(path=name), self.assertRaisesRegex(ValueError, 'hash mismatch'):
                dependency_closure(self.document, self.root)
            path.write_bytes(original)
        path = self.root / 'datasheet.txt'
        path.unlink()
        path.symlink_to(self.root / 'requirements.md')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            dependency_closure(self.document, self.root)
    def test_repinned_catalog_cannot_contradict_its_captured_raw_attributes(self):
        from tests.catalog_repin import repin, set_derived
        from agent_ee.parts_manifest import qualify_manifest
        from agent_ee.storage import digest
        snapshot = self.root / 'snapshot'
        pin = repin(snapshot, lambda connection: set_derived(connection, 'C1',
            {'resistance': {'min': '9999', 'max': '10001', 'unit': 'ohm'},
             'voltage': {'min': '50', 'max': '50', 'unit': 'V'}}))
        self.document['catalog'].update(manifest_sha256=pin,
                                       database_sha256=digest(snapshot / 'catalog.sqlite3'))
        with self.assertRaisesRegex(ValueError, 'canonical captured selection'):
            qualify_manifest(self.document, self.root)

    def test_malformed_or_duplicate_native_evidence_is_unknown_not_partial_pass(self):
        import xml.etree.ElementTree as ET
        from tests.test_native_binding import FIXTURE
        from agent_ee.parts_subset import evaluate
        path = self.root / 'native.xml'
        for mutation in ('duplicate_component', 'duplicate_footprint', 'malformed'):
            tree = ET.parse(FIXTURE / 'control.xml')
            if mutation == 'duplicate_component':
                import copy
                parent = tree.getroot().find('components')
                parent.append(copy.deepcopy(parent[0]))
            elif mutation == 'duplicate_footprint':
                import copy
                comp = tree.getroot().find('./components/comp')
                comp.append(copy.deepcopy(comp.find('footprint')))
            tree.write(path, encoding='utf-8')
            if mutation == 'malformed':
                path.write_text('<export>')
            report = evaluate(self.root, 'parts.json', path, ['binding.kicad_sch'])
            with self.subTest(mutation=mutation):
                self.assertEqual((report['execution'], report['outcome']), ('malformed', 'unknown'), report)
                self.assertEqual(report['matches'], [])
                self.assertEqual(report['findings'][0]['code'], 'unavailable_captured_evidence')


class NativePartsPipelineTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        from tests.toolchain import require_toolchain
        require_toolchain(native=True)
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix='agent-ee-86-')))
        self.document = captured_fixture(self, self.root)
        self.count = 0

    def schematic(self, variant='control'):
        return subset_generator().trio() if variant == 'trio' else subset_generator().schematic(variant)

    def run_case(self, variant='control', document=None, schema='agent-ee.scope/3'):
        from agent_ee.run_record import run_review
        from agent_ee.storage import write_json, read_json
        self.count += 1
        write_json(self.root / 'parts.json', document or self.document)
        sheets = subset_generator().hierarchy() if variant == 'hierarchy' else {
            'binding.kicad_sch': self.schematic(variant)}
        for name, content in sheets.items():
            (self.root / name).write_text(content)
        (self.root / 'binding.kicad_pcb').write_text('(kicad_pcb (version 20241229) (generator "owned"))\n')
        scope = dict(schema=schema, name='parts-fixture', scope='Owned synthetic parts membership.',
            limitations=[], schematic='binding.kicad_sch', board='binding.kicad_pcb', mutable_inputs=['binding.kicad_sch'],
            inputs=[dict(path=p.relative_to(self.root).as_posix(), at=p.relative_to(self.root).as_posix())
                    for p in self.root.rglob('*') if p.is_file() and not p.name.startswith('scope')
                    and not any(c.startswith('run-') for c in p.relative_to(self.root).parts)],
            requirements=['requirements.md'], checks=[dict(id='netlist', kind='netlist'),
                dict(id='parts', kind='parts_subset', params=dict(manifest='parts.json', netlist_check='netlist',
                                                                 requirement_id='membership'))],
            baseline=dict(requirements=[dict(id='membership', statement='R1 matches declared membership.',
                source=dict(path='requirements.md', locator='line 1'), checks=['netlist', 'parts'], intent='acceptance')],
                targets=['requirements.md'], reference_architectures=['requirements.md']))
        if schema == 'agent-ee.scope/2':
            del scope['baseline']['requirements'][0]['intent']
        write_json(self.root / 'scope.json', scope)
        run = self.root / f'run-{self.count}'
        run_review(self.root / 'scope.json', run, 120, checks_only=True)
        self.assertTrue((run / 'checks/parts.json').is_file(), read_json(run / 'result.json'))
        return run, read_json(run / 'checks/parts.json')

    def test_native_positive_and_footprint_defect_public_verify_and_witness(self):
        from agent_ee.run_record import verify
        from agent_ee.storage import read_json
        from agent_ee.baseline import validate_witnesses
        run, check = self.run_case()
        self.assertEqual((check['execution'], check['outcome']), ('ok', 'pass'), check)
        verify(run)
        coverage = read_json(run / 'baseline.json')['coverage']
        self.assertEqual(coverage['checks']['parts']['prerequisites'],
                         [dict(check_id='netlist', kind='netlist')])
        self.assertTrue(coverage['checks']['parts']['can_evaluate_bound'])
        run, check = self.run_case('footprint')
        self.assertEqual((check['execution'], check['outcome']), ('ok', 'defect'), check)
        self.assertEqual(check['details']['evaluated_predicates'], ['parts:declared-membership-multiplicity/1'])
        finding = check['details']['report']['matches'][0]
        self.assertEqual(finding['reference'], 'R1')
        self.assertIn(dict(code='footprint_mismatch', field='footprint',
                           declared='Owned:R2', native='Owned:SOT3'), finding['findings'])
        verify(run)
        validate_witnesses(run, [dict(requirement_id='membership', check_id='parts')],
                           [dict(path='checks/parts.json', locator='details.report.matches[0]')])
        with self.assertRaises(ValueError):
            validate_witnesses(run, [dict(requirement_id='stock', check_id='parts')],
                               [dict(path='checks/parts.json', locator='outcome')])
        for relative in ('work/parts/parts-subset.json', 'work/netlist/design.xml'):
            path = run / relative
            original = path.read_bytes()
            path.write_bytes(original + b' ')
            with self.subTest(tamper=relative), self.assertRaises(ValueError):
                verify(run)
            path.write_bytes(original)

    def test_native_absent_required_instance_is_a_sealed_defect(self):
        from agent_ee.run_record import verify
        self.document['parts'][0]['instances'][0]['reference'] = 'R99'
        run, check = self.run_case()
        self.assertEqual((check['execution'], check['outcome']), ('ok', 'defect'), check)
        rows = {row['reference']: row for row in check['details']['report']['matches']}
        self.assertEqual(rows['R99']['findings'], [dict(code='required_absent')])
        verify(run)

    def test_native_required_dnp_and_missing_required_datasheet(self):
        run, check = self.run_case('dnp')
        self.assertEqual(check['outcome'], 'defect', check)
        self.assertEqual(check['details']['report']['matches'][0]['findings'][0]['code'], 'required_unpopulated')
        self.document['parts'][0]['datasheet'] = dict(status='unavailable', required=True, reason='owner data missing')
        run, check = self.run_case()
        self.assertEqual(check['outcome'], 'unknown', check)
        self.assertEqual(check['details']['evaluated_predicates'], [])

    def test_failed_stale_missing_producer_and_mutable_closure(self):
        import copy
        import time
        from agent_ee.parts_subset import capability, check_parts_subset
        from agent_ee.checks import Ctx, CheckError
        from agent_ee.storage import read_json, write_json
        run, check = self.run_case()
        self.assertEqual(check['outcome'], 'pass', check)
        manifest = read_json(run / 'candidate.json')
        scope = manifest['scope']
        params = scope['checks'][1]['params']
        for mutation in ('failed', 'stale', 'missing', 'hierarchy'):
            with self.subTest(mutation=mutation):
                path = run / 'checks/netlist.json'
                producer = read_json(path)
                changed = copy.deepcopy(producer)
                if mutation == 'failed':
                    changed.update(execution='failed', outcome='unknown')
                elif mutation == 'stale':
                    changed['candidate_id'] = 'wrong'
                elif mutation == 'hierarchy':
                    changed['details']['sheet_instances'] = 2
                write_json(path, changed)
                if mutation == 'missing':
                    path.unlink()
                ctx = Ctx(run, manifest, scope['checks'][1], time.monotonic() + 60)
                with self.assertRaises(CheckError):
                    check_parts_subset(ctx)
                self.assertEqual(ctx.result['outcome'], 'unknown')
                self.assertEqual(ctx.result['details']['evaluated_predicates'], [])
                write_json(path, producer)
        for mutable in ('parts.json', 'Owned.kicad_sym', 'datasheet.txt', 'selection.json'):
            changed = copy.deepcopy(scope)
            changed['mutable_inputs'].append(mutable)
            with self.subTest(mutable=mutable), self.assertRaisesRegex(ValueError, 'mutable'):
                capability(params, manifest['files'], run / 'inputs', changed, 'parts')
        changed = copy.deepcopy(scope)
        changed['checks'].reverse()
        with self.assertRaisesRegex(ValueError, 'prerequisite'):
            capability(params, manifest['files'], run / 'inputs', changed, 'parts')

    def test_manifest_change_changes_baseline_and_mutation_breaks_verification(self):
        from agent_ee.run_record import verify
        from agent_ee.storage import read_json
        run, check = self.run_case()
        self.assertEqual(check['outcome'], 'pass', check)
        baseline = read_json(run / 'baseline.json')
        self.assertIn('parts.json', baseline['files'])
        self.document['parts'][0]['role'] = 'another declared role'
        fresh, check = self.run_case()
        self.assertEqual(check['outcome'], 'pass', check)
        self.assertNotEqual(read_json(fresh / 'baseline.json')['baseline_id'], baseline['baseline_id'])
        path = fresh / 'inputs/parts.json'
        path.write_bytes(path.read_bytes() + b' ')
        with self.assertRaises(ValueError):
            verify(fresh)

    def test_scope2_freezes_complete_closure_and_rejects_campaign_manifest_replacement(self):
        import shutil
        from agent_ee.baseline import compile_baseline
        from agent_ee.campaign import check_proposal, mutable_inputs
        from agent_ee.run_record import run_review, verify
        from agent_ee.parts_manifest import dependency_closure
        from agent_ee.storage import read_json, digest
        run, check = self.run_case(schema='agent-ee.scope/2')
        self.assertEqual((check['execution'], check['outcome']), ('ok', 'pass'), check)
        verify(run)
        baseline = read_json(run / 'baseline.json')
        self.assertEqual(baseline['files']['parts.json'], digest(run / 'inputs/parts.json'))
        for path, sha in dependency_closure(self.document, run / 'inputs').items():
            self.assertEqual(baseline['files'][path], sha)
        manifest = read_json(run / 'candidate.json')
        proposal = self.root / 'proposal'
        shutil.copytree(run / 'inputs', proposal)
        state = dict(baseline=baseline, mutable_inputs=mutable_inputs(manifest, baseline))
        check_proposal(proposal, manifest, state)
        # Even a writer granted manifest mutability cannot override the baseline.
        state['mutable_inputs'].append('parts.json')
        with (proposal / 'parts.json').open('ab') as stream:
            stream.write(b' ')
        with self.assertRaisesRegex(ValueError, 'immutable baseline source: parts.json'):
            check_proposal(proposal, manifest, state)
        scope = manifest['scope']
        scope['mutable_inputs'].append('parts.json')
        with self.assertRaisesRegex(ValueError, 'mutable'):
            compile_baseline(scope, manifest['files'], run / 'inputs')
        scope['mutable_inputs'].remove('parts.json')
        # The normal review entry point also refuses a fresh capture against an
        # existing campaign baseline before running any checks or inference.
        with (self.root / 'parts.json').open('ab') as stream:
            stream.write(b' ')
        refused = self.root / 'run-refused'
        run_review(self.root / 'scope.json', refused, 120, checks_only=True,
                   expected_baseline=baseline)
        result = read_json(refused / 'result.json')
        self.assertIn('candidate changed the campaign baseline', result['error'])
        self.assertEqual(result['checks'], [])
        self.assertEqual(result['outcome'], 'incomplete')

    def test_owned_rc_and_three_pin_header_complete_canonical_native_positive(self):
        # Fresh capture root: this is synthetic data, not a real component approval.
        import shutil
        for member in list(self.root.iterdir()):
            if member.is_dir():
                shutil.rmtree(member)
            else:
                member.unlink()
        self.document = captured_fixture(self, self.root, trio=True)
        run, check = self.run_case('trio')
        self.assertEqual((check['execution'], check['outcome']), ('ok', 'pass'), check)
        self.assertEqual([m['reference'] for m in check['details']['report']['matches']], ['C1', 'J1', 'R1'])
        from agent_ee.run_record import verify
        verify(run)

    def test_actual_child_instances_are_separate_capacity_obligations(self):
        from tests.test_native_binding import GEN
        from agent_ee.storage import read_json, write_json, digest
        requirements = read_json(self.root / 'selection-requirements.json')
        requirements['parts'][0]['quantity_per_board'] = 3
        write_json(self.root / 'selection-requirements.json', requirements)
        self.document['selection']['requirements']['sha256'] = digest(self.root / 'selection-requirements.json')
        from agent_ee.part_selection import selection_check
        selection = selection_check(self.root / 'snapshot', self.document['catalog']['manifest_sha256'], requirements)
        self.assertEqual(selection['outcome'], 'pass')
        write_json(self.root / 'selection.json', selection)
        self.document['selection']['result']['sha256'] = digest(self.root / 'selection.json')
        self.document['parts'][0]['instances'] += [dict(sheet='/' + sheet + '/', reference=ref)
            for (_, sheet), ref in zip(GEN.SHEETS, ('R11', 'R12'))]
        run, check = self.run_case('hierarchy')
        self.assertEqual((check['execution'], check['outcome']), ('ok', 'pass'), check)
        matches = check['details']['report']['matches']
        self.assertEqual(len(matches), 3)
        self.assertEqual(len({(m['sheet'], m['uuid']) for m in matches}), 3)
        self.document['parts'][0]['instances'][2]['reference'] = 'R99'
        run, check = self.run_case('hierarchy')
        self.assertEqual(check['outcome'], 'defect', check)
        codes = [f['code'] for m in check['details']['report']['matches'] for f in m['findings']]
        self.assertIn('required_absent', codes)
        self.assertIn('unexpected_populated_instance', codes)

    def test_actual_failed_export_and_missing_identity_are_unknown(self):
        run, check = self.run_case('broken')
        from agent_ee.storage import read_json
        self.assertNotEqual(read_json(run / 'checks/netlist.json')['execution'], 'ok')
        self.assertEqual(check['outcome'], 'unknown', check)
        run, check = self.run_case('missing-property')
        self.assertEqual(check['outcome'], 'unknown', check)
        self.assertEqual(check['details']['evaluated_predicates'], [])


if __name__ == '__main__':
    unittest.main()


class ExactReferenceSetTests(unittest.TestCase):
    def fixture(self):
        import copy, uuid
        document,graph=comparison_fixture()
        original=graph['components']['R1'];graph['components']={}
        parts=[]
        for i in range(1,18):
            ref='R'+str(i);component=copy.deepcopy(original)
            component.update(reference=ref,uuid=str(uuid.UUID(int=i)))
            graph['components'][ref]=component
            part=copy.deepcopy(document['parts'][0]);part.update(id=ref,instances=[dict(sheet='/',reference=ref)])
            parts.append(part)
        document['parts']=parts
        return document,graph

    def selector(self,graph,refs):
        return [dict(sheet=graph['components'][r]['sheet'],reference=r,uuids=sorted(graph['components'][r]['uuid'].split())) for r in sorted(refs)]

    def test_three_exact_roles_keep_full_seventeen_component_inventory(self):
        import copy
        from agent_ee.parts_subset import compare_inventory
        full,graph=self.fixture()
        for a,b in ((1,8),(8,15),(15,18)):
            refs={'R'+str(i) for i in range(a,b)}
            document=copy.deepcopy(full);document['parts']=[p for p in full['parts'] if p['id'] in refs]
            scope=self.selector(graph,refs)
            report=compare_inventory(document,graph,[],reference_scope=scope)
            self.assertEqual(report['outcome'],'pass')
            self.assertEqual(report['counts']['native'],17)
            self.assertEqual(report['counts']['selected_native'],b-a)
            self.assertEqual(len(report['native_inventory']['components']),17)
            self.assertEqual(len(report['outside_reference_set']),17-(b-a))
            self.assertEqual(compare_inventory(document,graph,[])['outcome'],'defect')

    def test_selected_absence_uuid_identity_dnp_and_provenance_still_refuse(self):
        import copy,uuid
        from agent_ee.parts_subset import compare_inventory
        full,graph=self.fixture();document=copy.deepcopy(full);document['parts']=document['parts'][:1]
        scope=self.selector(graph,['R1'])
        for kind in ('missing','uuid','footprint','identity','dnp','occurrence','multiunit'):
            changed=copy.deepcopy(graph)
            if kind=='missing':changed['components'].pop('R1')
            elif kind=='uuid':changed['components']['R1']['uuid']=str(uuid.UUID(int=90))
            elif kind=='footprint':changed['components']['R1']['footprint']='Wrong:Other'
            elif kind=='identity':changed['components']['R1']['properties']['MPN']='wrong'
            elif kind=='dnp':changed['components']['R1']['populated']=False
            elif kind=='occurrence':changed['components']['R1']['sheet']='/'+str(uuid.UUID(int=90))+'/'
            else:changed['components']['R1']['uuid']+=' '+str(uuid.UUID(int=91))
            with self.subTest(kind=kind):self.assertEqual(compare_inventory(document,changed,[],reference_scope=scope)['outcome'],'defect')
        report=compare_inventory(document,graph,[],[dict(part_id='R1',reason='missing')],reference_scope=scope)
        self.assertEqual(report['outcome'],'unknown')
        graph['components']['R1']['uuid']+=' '+str(uuid.UUID(int=91))
        self.assertEqual(compare_inventory(document,graph,[],reference_scope=self.selector(graph,['R1']))['outcome'],'pass')

    def test_selector_is_exact_not_inferred_from_manifest_or_observed_rows(self):
        import copy
        from agent_ee.parts_subset import compare_inventory,reference_set,report_scope_matches
        full,graph=self.fixture();scope=self.selector(graph,['R1']);document=copy.deepcopy(full);document['parts']=document['parts'][:1]
        for selected in ([],scope+scope,[dict(scope[0],sheet='/alias/')],[dict(scope[0],uuids=[])], [dict(scope[0],uuids=scope[0]['uuids']*2)]):
            with self.subTest(selected=selected),self.assertRaises(ValueError):reference_set(selected)
        with self.assertRaisesRegex(ValueError,'exactly'):compare_inventory(full,graph,[],reference_scope=scope)
        report=compare_inventory(document,graph,[],reference_scope=scope)
        other=self.selector(graph,['R2']);report['reference_set']=other
        from agent_ee.storage import identity
        report['reference_set_sha256']=identity(other)
        self.assertFalse(report_scope_matches(report,other))
