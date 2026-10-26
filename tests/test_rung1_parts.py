"""Rung-1 bindings over original synthetic supplier claims; no real part approval."""
import copy
import functools
import json
from pathlib import Path
import tempfile
import unittest

from agent_ee import generate as g, parts_manifest, rung1_parts as parts, rung1_qualified as q
from agent_ee.part_selection import selection_check
from agent_ee.storage import digest, identity, read_json, write_json
try:
    import test_part_selection as selection_helpers
    from toolchain import require_toolchain
except ImportError:
    from tests import test_part_selection as selection_helpers
    from tests.toolchain import require_toolchain


ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / 'fixtures/rung1/spec-assembled.md'


@functools.lru_cache(maxsize=None)
def cad_symbols(name):
    """Capture only selected stock members; fixture bytes stay in private scratch."""
    from agent_ee.schematic_emitter import _dump, _scan
    tree = _scan((g.STOCK / 'symbols' / (name + '.kicad_sym')).read_bytes())
    selected = {'Device': {'R', 'C'}, 'Connector_Generic': {'Conn_01x03'}}[name]
    tree = [node for node in tree if not isinstance(node, list) or node[0] != 'symbol' or node[1] in selected]
    return ('\n'.join(_dump(tree)) + '\n').encode('utf-8')


def bundle(test, root):
    """Use the existing capture, selection and CAD schemas, with stock CAD locally."""
    root.mkdir()
    helper = selection_helpers.SelectionTests()
    helper.root = root
    rows = [helper.row(package='0805'),
            helper.row('C2', mpn='C-C2', category='capacitor', package='0805',
                       attributes=json.dumps({'capacitance': '100 nF +/-10%', 'voltage': '25 V'})),
            helper.row('C3', mpn='J-C3', category='connector', package='TH',
                       attributes=json.dumps({'voltage': '50 V'}))]
    snapshot, pin = helper.snapshot(rows)
    requirement = helper.requirements(package='0805')
    requirement['parts'] += [dict(id='C1', category='capacitor', package='0805', quantity_per_board=1,
        constraints={'capacitance': dict(kind='within', nominal='100 nF', tolerance_percent='10'),
                     'voltage': dict(kind='at_least', min='25 V')}),
        dict(id='J1', category='connector', package='TH', quantity_per_board=1,
             constraints={'voltage': dict(kind='at_least', min='50 V')})]

    def put(path, payload):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(payload, bytes):
            target.write_bytes(payload)
        else:
            write_json(target, payload)
        return {'path': path, 'sha256': digest(target)}

    requirements = put('selection-requirements.json', requirement)
    result = selection_check(snapshot, pin, requirement)
    test.assertEqual(result['outcome'], 'pass', result)
    selected = put('selection.json', result)
    libraries = [dict(name=name, **put('cad/' + name + '.kicad_sym', cad_symbols(name)))
                 for name in ('Device', 'Connector_Generic')]
    specifications = [
        ('resistor', 'R1', 'Device:R', 'Resistor_SMD:R_0805_2012Metric', 'smd', None),
        ('capacitor', 'C1', 'Device:C', 'Capacitor_SMD:C_0805_2012Metric', 'smd', None),
        ('header', 'J1', 'Connector_Generic:Conn_01x03',
         'Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical', 'through_hole', {'rows': 1, 'columns': 3})]
    cad_libraries, components, logical, members = [], [], [], []
    choices = {p['id']: p['chosen'] for p in result['parts']}
    for role, ref, symbol, footprint, assembly, connector in specifications:
        library, name = footprint.split(':')
        relative = f'{library}.pretty/{name}.kicad_mod'
        blob = put('cad/' + relative, (g.STOCK / 'footprints' / relative).read_bytes())
        members.append(blob)
        cad_libraries.append({'name': library, 'members': [blob]})
        components.append(dict(reference=role, lib_id=symbol, footprint=footprint,
                               assembly=assembly, connector=connector))
        row = choices[ref]
        logical.append(dict(id=role, role=role, required=True, instances=[{'sheet': '/', 'reference': ref}],
                            selection_id=ref, lib_id=symbol, footprint=footprint, lcsc=row['lcsc'],
                            mpn=row['mpn'], manufacturer=row['mfr'], stock=row['stock'],
                            datasheet=dict(status='unavailable', required=False, reason='Synthetic fixture.')))
    cad = put('cad.json', dict(schema='agent-ee.footprint-libraries/1', complete=True, libraries=cad_libraries))
    assignments = put('assignments.json', dict(schema='agent-ee.footprint-assignments-input/1',
                                               libraries=libraries, components=components))
    declaration = {**put('parts-declaration.md', b'Original synthetic R/C/header allowlist; not owner approval.\n'),
                   'locator': 'line 1'}
    document = dict(schema='agent-ee.parts/1', declaration_source=declaration,
                    catalog=dict(snapshot='snapshot', manifest_sha256=pin,
                                 database_sha256=digest(snapshot / 'catalog.sqlite3'), snapshot_date='2026-10-17'),
                    selection=dict(requirements=requirements, result=selected),
                    cad=dict(manifest=cad, assignments=assignments,
                             members_sha256=identity(sorted(members + [{k: lib[k] for k in ('path', 'sha256')}
                                                                       for lib in libraries], key=lambda x: x['path']))),
                    property_fields=dict(lcsc='LCSC', mpn='Partnumber', manufacturer='Manufacturer'),
                    population='native-dnp-optional-only/1', parts=logical)
    put('parts.json', document)
    parts_manifest.qualify_manifest(document, root)
    return document


class RungPartsTests(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix='agent-ee-101-')))
        self.source = self.root / 'source'
        self.document = bundle(self, self.source)
        self.params = g.parse_spec(SPEC.read_text())

    def stage(self, name='prep'):
        stage = g.Stage(self.root / name, 2, 'parts')
        stage.start()
        state = {'params': self.params}
        g.stage_parts(stage, state, {'parts_manifest': self.source / 'parts.json'})
        stage.finish('ok')
        return stage, read_json(state['parts'])

    def test_capture_replays_selection_and_freezes_every_lcsc_and_j1_decision(self):
        stage, wrapper = self.stage()
        self.assertEqual({ref: f['LCSC'] for ref, f in wrapper['parts'].items()},
                         {'R1': 'C1', 'C1': 'C2', 'J1': 'C3'})
        self.assertEqual(wrapper['assembly_exclusions'], [])
        self.assertIn('including J1', wrapper['assembly_decision'])
        captured = stage.dir / 'approved'
        self.assertEqual((captured / parts.MANIFEST).read_bytes(), (self.source / 'parts.json').read_bytes())
        self.assertEqual(parts_manifest.qualify_manifest(self.document, captured)['selection'],
                         read_json(self.source / 'selection.json'))
        self.assertEqual(read_json(captured / 'selection.json')['snapshot_manifest_sha256'],
                         self.document['catalog']['manifest_sha256'])

    def test_assembly_reader_accepts_canonical_decimal_prices_and_null_cutoff(self):
        from agent_ee.assembly_readiness import load_catalog_snapshot
        catalog = load_catalog_snapshot(self.source, 'snapshot/manifest.json',
                                       expected_sha256=self.document['catalog']['manifest_sha256'])
        self.assertIsNone(catalog['stale_before'])
        self.assertEqual(catalog['parts']['C1']['price1'], 0.01)
        self.assertEqual(catalog['parts']['C1']['stock'], 1000)

    def test_changed_snapshot_or_selection_is_refused_without_modifying_prior_capture(self):
        stage, before = self.stage()
        original = (stage.dir / 'approved/selection.json').read_bytes()
        selection = read_json(self.source / 'selection.json')
        selection['snapshot_manifest_sha256'] = '0' * 64
        write_json(self.source / 'selection.json', selection)
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            self.stage('changed-selection')
        self.assertEqual((stage.dir / 'approved/selection.json').read_bytes(), original)
        self.assertEqual(read_json(stage.dir / 'parts-manifest.json'), before)

    def test_new_snapshot_requires_a_new_selection_receipt_and_preserves_old_capture(self):
        try:
            from catalog_repin import repin
        except ImportError:
            from tests.catalog_repin import repin
        old_stage, old = self.stage()
        original = (old_stage.dir / 'approved/selection.json').read_bytes()
        # Explicitly change the source declaration, then re-pin through the shared test helper.
        manifest = read_json(self.source / 'snapshot/manifest.json')
        manifest['source_url'] = 'https://example.invalid/owned-revised-source'
        write_json(self.source / 'snapshot/manifest.json', manifest)
        pin = repin(self.source / 'snapshot')
        with self.assertRaises(ValueError):
            self.stage('old-pins')
        self.document['catalog']['manifest_sha256'] = pin
        selection = selection_check(self.source / 'snapshot', pin,
                                    read_json(self.source / 'selection-requirements.json'))
        write_json(self.source / 'selection.json', selection)
        self.document['selection']['result']['sha256'] = digest(self.source / 'selection.json')
        write_json(self.source / 'parts.json', self.document)
        new_stage, revised = self.stage('new-pins')
        self.assertNotEqual(old['approved_bundle']['sha256'], revised['approved_bundle']['sha256'])
        self.assertNotEqual(original, (new_stage.dir / 'approved/selection.json').read_bytes())
        self.assertEqual((old_stage.dir / 'approved/selection.json').read_bytes(), original)
        self.assertEqual(old['parts'], revised['parts'])

    def test_wrong_nominal_missing_role_optional_and_manual_header_refused(self):
        qualified = parts_manifest.qualify_manifest(self.document, self.source)
        for mutation in ('nominal', 'package', 'missing', 'optional', 'manual'):
            document, params, replay = copy.deepcopy(self.document), copy.deepcopy(self.params), copy.deepcopy(qualified)
            if mutation == 'nominal':
                params['resistor']['value'] = '11k'
            elif mutation == 'missing':
                document['parts'].pop()
            elif mutation == 'optional':
                document['parts'][0]['required'] = False
            elif mutation == 'package':
                next(p for p in replay['selection']['parts'] if p['id'] == 'R1')['chosen']['package'] = '0603'
            else:
                params['header']['assembly'] = 'not_assembled'
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                parts.fields_for_rung(document, replay, params)

    def test_cad_names_cannot_substitute_changed_symbols_or_footprint_bytes(self):
        qualified = parts_manifest.qualify_manifest(self.document, self.source)
        symbols = {name + '.kicad_sym': cad_symbols(name) for name in ('Device', 'Connector_Generic')}
        footprints = read_json(self.source / 'cad.json')
        parts.check_cad_inputs(qualified, self.source, symbols, footprints)
        altered = dict(symbols)
        altered['Device.kicad_sym'] = altered['Device.kicad_sym'].replace(
            b'(property "Value" "R"', b'(property "Value" "different"')
        self.assertNotEqual(altered, symbols)
        with self.assertRaisesRegex(ValueError, 'emitted symbol differs'):
            parts.check_cad_inputs(qualified, self.source, altered, footprints)
        footprints['libraries'][0]['members'][0]['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'emitted footprint differs'):
            parts.check_cad_inputs(qualified, self.source, symbols, footprints)

    def test_owned_scaffold_captures_bundle_and_registers_existing_assembly_package_checks(self):
        from agent_ee import rung1_campaign as rc
        from agent_ee.candidate import capture
        from agent_ee.stage_receipts import file_tree, import_pre_receipts, validate_receipt, verify_pre_receipts
        run = self.root / 'run'
        run.mkdir()
        prep = q.prepare(SPEC, run, parts_manifest=self.source / 'parts.json')
        scaffold, record = q.write_scaffold(run, prep)
        before = file_tree(scaffold)
        scope = q.compile_staged_scope(prep['params'], run, scaffold)
        self.assertEqual(file_tree(scaffold), before)
        self.assertEqual(q.compile_staged_scope(prep['params'], run, scaffold), scope)
        mapping = 'rung1-package-map.json'
        receipt = validate_receipt(run / 'scaffold-receipt', binding={'pre_receipt': 1})
        self.assertEqual(receipt['inputs'][mapping], record['files'][mapping])
        self.assertEqual(record['files'][mapping], digest(scaffold / mapping))
        write_json(run / 'scope.json', scope)
        campaign = run / 'campaign'
        links = import_pre_receipts(campaign, [rc.scaffold_pre_receipt(record)], run)
        first = campaign / 'runs/1'
        first.mkdir(parents=True)
        capture(run / 'scope.json', first)
        state = {'history': [{'run': str(first)}],
                 'pre_receipts': {links[0]['path']: links[0]['sha256']}}
        verify_pre_receipts(campaign, state, links)
        captured = read_json(first / 'candidate.json')
        captured['files'][mapping] = '0' * 64
        write_json(first / 'candidate.json', captured)
        with self.assertRaisesRegex(ValueError, 'pre_receipt output differs from first capture: ' + mapping):
            verify_pre_receipts(campaign, state, links)
        validation = q.validate_scope_file(run / 'scope.json', run / 'validation')
        self.assertTrue({'parts_subset', 'assembly_readiness', 'package_consistency'} <=
                        {c['kind'] for c in scope['checks']})
        self.assertTrue({'approved-parts.json', 'selection.json', 'snapshot/manifest.json',
                         'snapshot/catalog.sqlite3'} <= set(validation['baseline_files']))
        self.assertEqual(prep['parts']['parts']['J1']['LCSC'], 'C3')
        self.native_fab_evidence(run, prep, scaffold, scope)

    def test_parts_bundle_cannot_supply_the_reserved_package_map(self):
        run = self.root / 'collision'
        run.mkdir()
        prep = q.prepare(SPEC, run, parts_manifest=self.source / 'parts.json')
        (prep['parts_path'].parent / 'approved/rung1-package-map.json').write_text('{}')
        with self.assertRaisesRegex(q.PrepareFailure, 'parts bundle uses reserved package-map path'):
            q.write_scaffold(run, prep)
        self.assertFalse((run / 'scaffold-receipt').exists())

    def native_fab_evidence(self, run, prep, scaffold, scope):
        """Real emitter → native XML → annotation → sealed checks; no campaign claim."""
        from agent_ee import executor, forward_annotation
        from agent_ee.run_record import run_review, verify
        from agent_ee.schematic_emitter import emit_schematic
        intent = q.qualified_intent(prep)
        emission, files = emit_schematic(intent, q.intent_captures(intent), q.layout())
        self.assertEqual(emission['execution'], 'ok', emission)
        schematic = scaffold / f'project/{q.NAME}.kicad_sch'
        schematic.write_bytes(files[q.NAME + '.kicad_sch'])
        candidate = g.native_candidate(run / 'native', scaffold / 'project', q.FOOTPRINT_LIBRARIES)
        export = run / 'export'
        g.seed_kicad_config(export, candidate)
        execution = executor.execute(candidate, export, ['kicad-cli', 'sch', 'export', 'netlist',
            '--format', 'kicadxml', '-o', '/work/design.xml', '/candidate/project/' + schematic.name], timeout=300)
        self.assertEqual(execution['execution'], 'ok', execution)
        board = scaffold / f'project/{q.NAME}.kicad_pcb'
        footprints = read_json(scaffold / 'generation/footprints.json')
        plan = forward_annotation.plan(board, export / 'design.xml', footprints,
                                       g.STOCK / 'footprints', run / 'annotation-plan')
        self.assertEqual(plan['execution'], 'ok', plan)
        annotation = forward_annotation.apply(plan, board, export / 'design.xml', footprints,
            g.STOCK / 'footprints', run / 'annotated.kicad_pcb', run / 'annotation-apply')
        self.assertEqual(annotation['execution'], 'ok', annotation)
        board.write_bytes((run / 'annotated.kicad_pcb').read_bytes())
        # Isolate parts/fab criteria from the not-yet-placed/routed campaign stages.
        wanted = {'netlist', 'binding', 'fab', 'parts-membership', 'assembly', 'packages'}
        scope.pop('stages')
        scope['checks'] = [c for c in scope['checks'] if c['id'] in wanted]
        for check in scope['checks']:
            check.pop('stage')
        next(c for c in scope['checks'] if c['id'] == 'assembly')['params']['max_stock_age_days'] = 0
        requirements = []
        for requirement in scope['baseline']['requirements']:
            requirement['checks'] = [c for c in requirement['checks'] if c in wanted]
            if requirement['checks']:
                requirements.append(requirement)
        scope['baseline']['requirements'] = requirements
        write_json(run / 'fab-scope.json', scope)
        checked = run / 'fab-review'
        run_review(run / 'fab-scope.json', checked, 600, checks_only=True)
        for name in ('binding', 'fab', 'parts-membership', 'assembly'):
            self.assertTrue((checked / f'checks/{name}.json').is_file(), read_json(checked / 'result.json'))
            check = read_json(checked / f'checks/{name}.json')
            self.assertEqual((check['execution'], check['outcome']), ('ok', 'pass'), check)
        package = read_json(checked / 'checks/packages.json')
        self.assertEqual(package['outcome'], 'unknown', package)  # TH is deliberately unmapped.
        fab = read_json(checked / 'work/fab/fab/manifest.json')
        self.assertEqual(set(fab['designators']['board_non_dnp']), {'R1', 'C1', 'J1'})
        parity = read_json(checked / 'checks/fab.json')['details']['designators']
        self.assertEqual(parity['bom'], parity['cpl'])
        self.assertEqual(parity['bom'], fab['designators']['board_non_dnp'])
        import csv
        with (checked / 'work/fab/fab/bom.csv').open() as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual({r['Designator']: r['LCSC Part #'] for r in rows},
                         {'R1': 'C1', 'C1': 'C2', 'J1': 'C3'})
        verify(checked)
        from agent_ee.assembly_readiness import evaluate_assembly_readiness
        without_age = dict(next(c for c in scope['checks'] if c['id'] == 'assembly')['params'])
        without_age.pop('max_stock_age_days')
        report = evaluate_assembly_readiness(checked / 'inputs', checked / 'work/assembly/design.xml',
            without_age, expected_sheets=1, hierarchy_files=['project/' + schematic.name])
        self.assertEqual(report['outcome'], 'unknown', report)
        self.assertTrue(any(f['code'] == 'stale_stock_telemetry' for f in report['findings']))


if __name__ == '__main__':
    unittest.main()
