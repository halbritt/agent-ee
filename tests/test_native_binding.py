"""Native-bound footprint assignment over owned KiCad exports (AGENTEE-36).

Expected outcomes are predeclared in fixtures/native-binding/EXPECTATIONS.md.
"""
import copy
import hashlib
import importlib.util
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

FIXTURE = Path(__file__).parent / 'fixtures/native-binding'


def generator():
    spec = importlib.util.spec_from_file_location('native_binding_fixture', FIXTURE / 'generate.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GEN = generator()
LIBRARY = {'name': 'Owned', 'path': 'Owned.kicad_sym',
           'sha256': hashlib.sha256(GEN.library()).hexdigest()}


def declaration(variant='control'):
    components = []
    for ref, symbol, fp, (_, on_board, _), _ in GEN.parts(variant):
        if ref.startswith('#') or on_board == 'no':
            continue
        components.append({'reference': ref, 'lib_id': 'Owned:' + symbol, 'footprint': fp,
                           'assembly': 'through_hole' if ref == 'J1' else 'smd',
                           'connector': {'rows': 1, 'columns': 4} if ref == 'J1' else None})
    return {'schema': 'agent-ee.footprint-assignments-input/1', 'libraries': [dict(LIBRARY)],
            'components': components}


def component(decl, ref):
    return next(c for c in decl['components'] if c['reference'] == ref)


def captures():
    files = GEN.footprints()
    files['Owned.kicad_sym'] = GEN.library()
    return GEN.manifest(GEN.footprints()), files


def native_graph(xml=FIXTURE / 'control.xml'):
    from agent_ee.native_netlist import read_native_inventory
    return read_native_inventory(xml)


class AdapterTests(unittest.TestCase):
    """The one strict XML reader exposes the facts footprint binding needs."""

    def test_board_bom_and_population_are_distinct_native_facts(self):
        graph = native_graph()
        c = graph['components']
        self.assertEqual((c['R1']['populated'], c['R1']['on_board'], c['R1']['in_bom']), (True, True, True))
        self.assertEqual((c['R2']['populated'], c['R2']['on_board']), (False, True))
        self.assertEqual((c['R3']['on_board'], c['R3']['footprint']), (False, ''))
        self.assertEqual((c['R4']['in_bom'], c['R4']['on_board']), (False, True))
        self.assertIn('dnp', c['R2']['properties'])
        self.assertIsNone(c['R2']['properties']['dnp'])
        self.assertNotIn('#PWR01', c)
        self.assertTrue(graph['exporter'].startswith('Eeschema 10.0.7'), graph['exporter'])

    def test_complete_library_part_inventory_includes_unplaced_units(self):
        graph = native_graph()
        dual = graph['library_parts']['Owned:Dual']
        self.assertEqual(sorted(dual), ['1', '2', '3', '4', '5'])
        self.assertEqual(dual['4'], {'name': '', 'electrical_type': 'no_connect'})
        self.assertEqual(dual['5'], {'name': 'V', 'electrical_type': 'power_in'})
        self.assertEqual(graph['components']['U2']['library_pins'], ('1', '2', '3', '4', '5'))
        self.assertEqual(graph['library_parts']['Owned:Child'], graph['library_parts']['Owned:Tri'])

    def test_existing_consumer_fields_are_unchanged(self):
        comp = native_graph()['components']['U1']
        for key in ('reference', 'sheet', 'uuid', 'lib_id', 'value', 'footprint', 'populated',
                    'pins', 'units', 'pin_metadata', 'locator'):
            self.assertIn(key, comp)
        self.assertEqual(comp['pins'], ('1', '2', '3'))
        self.assertEqual(comp['lib_id'], 'Owned:Tri')

    def mutated(self, mutate):
        tree = ET.parse(FIXTURE / 'control.xml')
        mutate(tree.getroot())
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / 'design.xml'
        tree.write(path, encoding='utf-8')
        return path

    def test_duplicate_structured_or_contradictory_footprints_are_malformed(self):
        from agent_ee.native_netlist import read_native_inventory

        def comp(root, ref='U1'):
            return next(c for c in root.findall('./components/comp') if c.get('ref') == ref)

        def extra(text, first):
            def mutate(root):
                node = comp(root)
                element = ET.Element('footprint')
                element.text = text
                index = list(node).index(node.find('footprint'))
                node.insert(index if first else index + 1, element)
            return mutate

        def nested(root):
            ET.SubElement(comp(root).find('footprint'), 'footprint').text = 'Owned:SOT4'

        def contradicted_field(root):
            comp(root).find("./fields/field[@name='Footprint']").text = 'Owned:SOT4'

        def flag_value(root):
            comp(root, 'R2').find("./property[@name='dnp']").set('value', 'no')

        def empty_with_field(root):
            comp(root, 'R3').find("./fields/field[@name='Footprint']").text = 'Owned:R2'

        for mutate in [extra('Owned:SOT4', False), extra('Owned:SOT4', True), extra('Owned:SOT3', False),
                       nested, contradicted_field, flag_value, empty_with_field]:
            with self.subTest(mutation=getattr(mutate, '__name__', 'extra')):
                with self.assertRaises(ValueError):
                    read_native_inventory(self.mutated(mutate))


class VirtualSymbolTests(unittest.TestCase):
    def test_exporter_omitted_references_are_audited_by_embedded_definition(self):
        from agent_ee.native_binding import virtual_symbols
        control = virtual_symbols({'binding.kicad_sch': GEN.schematic().encode()})
        self.assertEqual([(r['references'], r['embedded_power']) for r in control], [(['#PWR01'], True)])
        seeded = virtual_symbols({'binding.kicad_sch': GEN.schematic('virtual-nonpower').encode()})
        self.assertEqual({r['references'][0]: r['embedded_power'] for r in seeded},
                         {'#PWR01': True, '#X1': False})


class BindingTests(unittest.TestCase):
    """Pure binding over the committed native export, with in-memory seeds."""

    def bind(self, decl=None, graph=None, schematic='control', files=None):
        from agent_ee.native_binding import bind_native_assignments, virtual_symbols
        manifest, captured = captures()
        captured.update(files or {})
        virtual = virtual_symbols({'binding.kicad_sch': GEN.schematic(schematic).encode()})
        return bind_native_assignments(manifest, captured, decl or declaration(),
                                       graph or native_graph(), virtual)

    def statuses(self, report):
        return {b['reference']: b['status'] for b in report['bindings']}

    def codes(self, report, ref):
        binding = next(b for b in report['bindings'] if b['reference'] == ref)
        return {f['code'] for f in binding['findings']}

    def test_control_binds_every_physical_component(self):
        report = self.bind()
        self.assertEqual((report['execution'], report['outcome']), ('ok', 'pass'), report['findings'])
        self.assertEqual(self.statuses(report), {'J1': 'pass', 'R1': 'pass', 'R2': 'pass', 'R3': 'excluded',
                                                 'R4': 'pass', 'U1': 'pass', 'U2': 'pass', 'U3': 'pass'})
        bindings = {b['reference']: b for b in report['bindings']}
        self.assertFalse(bindings['R2']['populated'])
        self.assertFalse(bindings['R4']['in_bom'])
        self.assertEqual(bindings['R3']['exclusion'], 'native exclude_from_board instance property')
        u2 = next(a for a in report['assignments']['assignments'] if a['reference'] == 'U2')
        self.assertEqual(u2['pin_numbers'], ['1', '2', '3', '4', '5'])
        self.assertEqual([e['references'] for e in report['exclusions'] if e['kind'] == 'exporter_virtual'],
                         [['#PWR01']])
        self.assertEqual(report['predicate'], 'footprint:native-assignment-parity/1')
        self.assertEqual(len(report['native_inventory_sha256']), 64)

    def test_omitted_declaration_is_missing_coverage_not_pass(self):
        decl = declaration()
        decl['components'] = [c for c in decl['components'] if c['reference'] != 'U1']
        report = self.bind(decl)
        self.assertEqual((report['execution'], report['outcome']), ('unsupported', 'unknown'))
        self.assertIn('undeclared_native_component', self.codes(report, 'U1'))

    def test_ghost_and_swapped_selection_are_binding_defects(self):
        decl = declaration()
        decl['components'].append(dict(component(decl, 'R1'), reference='R9'))
        report = self.bind(decl)
        self.assertEqual(report['outcome'], 'defect')
        self.assertEqual(self.codes(report, 'R9'), {'ghost_declaration'})
        decl = declaration()
        component(decl, 'U1')['lib_id'] = 'Owned:Quad'
        report = self.bind(decl)
        self.assertEqual(report['outcome'], 'defect')
        self.assertIn('lib_id_mismatch', self.codes(report, 'U1'))
        decl = declaration()
        component(decl, 'U1')['footprint'] = 'Owned:SOT4'
        report = self.bind(decl)
        self.assertEqual(report['outcome'], 'defect')
        self.assertIn('footprint_mismatch', self.codes(report, 'U1'))

    def test_shared_unusable_footprint_reports_dnp_and_populated_references(self):
        graph = native_graph()
        decl = declaration()
        for ref in ('R1', 'R2'):
            graph['components'][ref]['footprint'] = 'Owned:SOT3'
            component(decl, ref)['footprint'] = 'Owned:SOT3'
        report = self.bind(decl, graph)
        self.assertEqual(report['outcome'], 'defect')
        defects = sorted(f['reference'] for f in report['findings'] if f['code'] == 'pad_pin_parity')
        self.assertEqual(defects, ['R1', 'R2'])

    def test_embedded_library_metadata_disagreement_is_unknown(self):
        for mutate in [lambda pins: pins['3'].update(electrical_type='power_in'),
                       lambda pins: pins.update({'4': {'name': 'P4', 'electrical_type': 'passive'}}),
                       lambda pins: pins['1'].update(name='renamed')]:
            graph = native_graph()
            mutate(graph['library_parts']['Owned:Tri'])
            report = self.bind(graph=graph)
            self.assertEqual((report['execution'], report['outcome']), ('unsupported', 'unknown'))
            self.assertIn('library_pin_mismatch', self.codes(report, 'U1'))

    def test_nonpower_exporter_omission_is_unknown(self):
        report = self.bind(schematic='virtual-nonpower')
        self.assertEqual((report['execution'], report['outcome']), ('unsupported', 'unknown'))
        self.assertTrue(any(f['code'] == 'virtual_nonpower_symbol' for f in report['findings']))

    def test_library_drift_or_unreadable_member_fails(self):
        report = self.bind(files={'Owned.kicad_sym': GEN.library() + b' '})
        self.assertEqual((report['execution'], report['outcome']), ('failed', 'unknown'))
        report = self.bind(files={'Owned.pretty/SOT4.kicad_mod': b'unreadable'})
        self.assertEqual((report['execution'], report['outcome']), ('failed', 'unknown'))

    def test_captured_power_flag_does_not_exclude_an_exported_board_instance(self):
        graph = native_graph()
        graph['components']['R1']['lib_id'] = 'Owned:GND'
        graph['components']['R1']['footprint'] = ''
        graph['library_parts']['Owned:GND'] = {'1': {'name': 'GND', 'electrical_type': 'power_in'}}
        decl = declaration()
        component(decl, 'R1').update(lib_id='Owned:GND', footprint='')
        report = self.bind(decl, graph)
        self.assertEqual(report['outcome'], 'defect')
        self.assertIn('empty_footprint', self.codes(report, 'R1'))

    def test_declared_exporter_omitted_power_is_excluded_only_with_its_identity(self):
        decl = declaration()
        decl['components'].append({'reference': '#PWR01', 'lib_id': 'Owned:GND', 'footprint': '',
                                   'assembly': 'smd', 'connector': None})
        report = self.bind(decl)
        self.assertEqual(report['outcome'], 'pass')
        self.assertEqual(self.statuses(report)['#PWR01'], 'excluded')
        component(decl, '#PWR01')['lib_id'] = 'Owned:R'
        report = self.bind(decl)
        self.assertEqual(report['outcome'], 'defect')
        self.assertEqual(self.codes(report, '#PWR01'), {'lib_id_mismatch'})

    def test_malformed_declaration_is_malformed(self):
        decl = declaration()
        decl['components'].append(dict(decl['components'][0]))
        self.assertEqual(self.bind(decl)['execution'], 'malformed')


class NativeBindingPipelineTests(unittest.TestCase):
    """Actual kicad-cli exports through the ordinary capture/check/seal path."""

    def setUp(self):
        from toolchain import require_toolchain
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-native-binding-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.counter = 0

    def scope(self, case, decl, checks=None):
        from agent_ee.storage import write_json
        names = ['binding.kicad_sch', 'Owned.kicad_sym', 'manifest.json', 'assignment.json',
                 'requirements.md', *GEN.footprints()]
        params = {'manifest': 'manifest.json', 'declaration': 'assignment.json', 'netlist_check': 'netlist',
                  'assembly_intent': [{k: c[k] for k in ('reference', 'assembly', 'connector')}
                                      for c in decl['components']]}
        return {'schema': 'agent-ee.scope/3', 'name': 'binding', 'scope': 'Owned native binding fixture.',
                'limitations': [], 'schematic': 'binding.kicad_sch',
                'inputs': [{'path': n, 'at': n} for n in names], 'requirements': ['requirements.md'],
                'checks': checks or [{'id': 'netlist', 'kind': 'netlist'},
                                     {'id': 'fp', 'kind': 'footprint_assignment', 'params': params}],
                'baseline': {'requirements': [{'id': 'fp', 'statement': 'Every board symbol has a valid footprint.',
                    'source': {'path': 'requirements.md', 'locator': 'line 1'}, 'checks': ['netlist', 'fp'],
                    'intent': 'acceptance'}], 'targets': ['requirements.md'],
                    'reference_architectures': ['requirements.md']}}

    def run_variant(self, variant='control', decl=None, case=None, schematic=None):
        import time
        from agent_ee.candidate import capture
        from agent_ee.checks import run_checks
        from agent_ee.storage import write_json
        decl = decl or declaration(variant)
        if case is None:
            self.counter += 1
            case = self.root / str(self.counter)
            case.mkdir()
            manifest, files = captures()
            for path, data in files.items():
                (case / path).parent.mkdir(parents=True, exist_ok=True)
                (case / path).write_bytes(data)
            write_json(case / 'manifest.json', manifest)
            (case / 'requirements.md').write_text('Every board symbol shall have a valid footprint.\n')
        (case / 'binding.kicad_sch').write_text(schematic or GEN.schematic(variant))
        write_json(case / 'assignment.json', decl)
        write_json(case / 'scope.json', self.scope(case, decl))
        run = case / f'run-{variant}-{len(list(case.glob("run-*")))}'
        run.mkdir()
        capture(case / 'scope.json', run)
        return run, run_checks(run, time.monotonic() + 120)

    def test_committed_control_export_matches_fresh_native_export(self):
        run, checks = self.run_variant()
        self.assertEqual(checks[0]['execution'], 'ok', checks)
        fresh, committed = (ET.parse(p).getroot() for p in (run / 'work/netlist/design.xml', FIXTURE / 'control.xml'))
        for root in (fresh, committed):
            # The exporter writes Sheetfile relative to its own working directory.
            for prop in root.findall("./components/comp/property[@name='Sheetfile']"):
                prop.set('value', Path(prop.get('value')).name)
        for tag in ('components', 'libparts', 'nets'):
            self.assertEqual(ET.tostring(fresh.find(tag)), ET.tostring(committed.find(tag)), tag)
        self.assertEqual(fresh.findtext('./design/tool'), committed.findtext('./design/tool'))

    def test_actual_native_variants_match_predeclared_expectations(self):
        def tweak(variant, change):
            decl = declaration(variant)
            change(decl)
            return decl

        def drop_u1(decl):
            decl['components'] = [c for c in decl['components'] if c['reference'] != 'U1']

        cases = [
            ('control', None, ('ok', 'pass'), None),
            ('pin4', None, ('ok', 'defect'), ('U1', 'pad_pin_parity')),
            ('pin4', tweak('pin4', drop_u1), ('unsupported', 'unknown'), ('U1', 'undeclared_native_component')),
            ('pin4-corrected', None, ('ok', 'pass'), None),
            ('extra-native', tweak('control', lambda d: None), ('unsupported', 'unknown'),
             ('R6', 'undeclared_native_component')),
            ('control', tweak('control', lambda d: d['components'].append(
                dict(component(d, 'R1'), reference='R9'))), ('ok', 'defect'), ('R9', 'ghost_declaration')),
            ('control', tweak('control', lambda d: component(d, 'U1').update(lib_id='Owned:Quad')),
             ('ok', 'defect'), ('U1', 'lib_id_mismatch')),
            ('footprint-edit', tweak('control', lambda d: None), ('ok', 'defect'), ('U1', 'footprint_mismatch')),
            ('shared-bad', None, ('ok', 'defect'), ('R2', 'pad_pin_parity')),
            ('empty-footprint', None, ('ok', 'defect'), ('R1', 'empty_footprint')),
            ('absent-footprint', None, ('ok', 'defect'), ('R1', 'absent_footprint')),
            ('embedded-retyped', declaration(), ('unsupported', 'unknown'), ('U1', 'library_pin_mismatch')),
            ('embedded-extra-pin', declaration(), ('unsupported', 'unknown'), ('U1', 'library_pin_mismatch')),
            ('virtual-nonpower', declaration(), ('unsupported', 'unknown'), None),
            ('power-exported', None, ('ok', 'defect'), ('PW1', 'empty_footprint')),
            ('connector-1x5', None, ('ok', 'defect'), ('J1', 'connector_count')),
        ]
        for variant, decl, expected, finding in cases:
            with self.subTest(variant=variant, finding=finding):
                _, checks = self.run_variant(variant, decl)
                self.assertEqual(checks[0]['execution'], 'ok', checks)
                fp = checks[1]
                self.assertEqual((fp['execution'], fp['outcome']), expected, fp)
                report = fp['details']['report']
                if finding:
                    binding = next(b for b in report['bindings'] if b['reference'] == finding[0])
                    self.assertIn(finding[1], {f['code'] for f in binding['findings']}, binding)
                self.assertEqual(fp['details']['evaluated_predicates'],
                                 ['footprint:native-assignment-parity/1'] if expected[0] == 'ok' else [])
                if variant == 'pin4' and expected[1] == 'defect':
                    parity = next(f for f in report['findings'] if f['code'] == 'pad_pin_parity')
                    self.assertEqual((parity['reference'], parity['footprint'], parity['missing_pads']),
                                     ('U1', 'Owned:SOT3', ['4']))
                if variant == 'shared-bad':
                    self.assertEqual(sorted(f['reference'] for f in report['findings']
                                            if f['code'] == 'pad_pin_parity'), ['R1', 'R2'])

    def test_scope3_seal_witness_drift_and_fresh_corrected_candidate(self):
        from agent_ee.baseline import freeze_evidence, validate_witnesses, verify_evidence
        from agent_ee.candidate import verify_snapshot
        from agent_ee.storage import read_json, write_json
        run, checks = self.run_variant('pin4')
        self.assertEqual(checks[1]['outcome'], 'defect', checks)
        self.assertEqual(checks[1]['details']['native_xml_sha256'],
                         hashlib.sha256((run / 'work/netlist/design.xml').read_bytes()).hexdigest())
        baseline = read_json(run / 'baseline.json')
        self.assertIn('Owned.kicad_sym', baseline['files'])
        self.assertIn('manifest.json', baseline['files'])
        self.assertNotIn('assignment.json', baseline['files'])
        self.assertNotIn('binding.kicad_sch', baseline['files'])
        self.assertEqual(baseline['coverage']['checks']['fp']['predicates'],
                         ['footprint:native-assignment-parity/1'])
        from agent_ee.baseline import compile_baseline
        manifest = read_json(run / 'candidate.json')
        legacy = copy.deepcopy(manifest['scope'])
        legacy['schema'] = 'agent-ee.scope/2'
        del legacy['baseline']['requirements'][0]['intent']
        legacy_files = compile_baseline(legacy, manifest['files'], run / 'inputs')['files']
        self.assertIn('Owned.kicad_sym', legacy_files)
        self.assertIn('Owned.pretty/SOT3.kicad_mod', legacy_files)
        freeze_evidence(run, checks)
        witness = [{'requirement_id': 'fp', 'check_id': 'fp'}]
        citation = [{'path': 'checks/fp.json', 'locator': 'outcome'}]
        validate_witnesses(run, witness, citation)
        for rel in ['work/fp/native-assignments.json', 'work/netlist/design.xml']:
            path = run / rel
            original = path.read_bytes()
            path.write_bytes(original + b' ')
            with self.assertRaises(ValueError):
                verify_evidence(run, checks, required=True)
            with self.assertRaises(ValueError):
                validate_witnesses(run, witness, citation)
            path.write_bytes(original)
        (run / 'inputs/Owned.kicad_sym').write_bytes(GEN.library() + b' ')
        with self.assertRaises(ValueError):
            verify_snapshot(run)
        corrected = declaration('pin4-corrected')
        fresh, fixed = self.run_variant('pin4-corrected', corrected, case=run.parent)
        self.assertEqual((fixed[1]['execution'], fixed[1]['outcome']), ('ok', 'pass'), fixed)
        self.assertEqual(read_json(fresh / 'baseline.json')['baseline_id'], baseline['baseline_id'])
        self.assertNotEqual(read_json(fresh / 'candidate.json')['candidate_id'],
                            read_json(run / 'candidate.json')['candidate_id'])

    def test_failed_export_missing_producer_and_report_mismatch_refuse_acceptance(self):
        import time
        from agent_ee.baseline import compile_baseline
        from agent_ee.checks import Ctx, CheckError
        from agent_ee.library_checks import check_footprint_assignment
        from agent_ee.storage import read_json, write_json
        run, checks = self.run_variant('control', schematic='(kicad_sch (version 20250114) broken')
        self.assertNotEqual(checks[0]['execution'], 'ok', checks)
        self.assertEqual(checks[1]['outcome'], 'unknown', checks)
        self.assertEqual(checks[1]['details'].get('evaluated_predicates', []), [])
        run, checks = self.run_variant('control')
        self.assertEqual(checks[1]['outcome'], 'pass', checks)
        manifest = read_json(run / 'candidate.json')
        scope = copy.deepcopy(manifest['scope'])
        scope['checks'] = scope['checks'][1:]
        scope['baseline']['requirements'][0]['checks'] = ['fp']
        with self.assertRaisesRegex(ValueError, 'prerequisite'):
            compile_baseline(scope, manifest['files'], run / 'inputs')
        scope = copy.deepcopy(manifest['scope'])
        scope['checks'].reverse()
        with self.assertRaisesRegex(ValueError, 'prerequisite'):
            compile_baseline(scope, manifest['files'], run / 'inputs')
        producer_path = run / 'checks/netlist.json'
        producer = read_json(producer_path)
        failed = dict(producer, execution='failed', outcome='unknown')
        write_json(producer_path, failed)
        ctx = Ctx(run, manifest, manifest['scope']['checks'][1], time.monotonic() + 60)
        with self.assertRaises(CheckError) as raised:
            check_footprint_assignment(ctx)
        self.assertEqual(raised.exception.execution, 'failed')
        write_json(producer_path, dict(producer, details=dict(producer['details'], sheet_instances=2)))
        ctx = Ctx(run, manifest, manifest['scope']['checks'][1], time.monotonic() + 60)
        with self.assertRaises(CheckError) as raised:
            check_footprint_assignment(ctx)
        self.assertEqual(raised.exception.execution, 'malformed')
        write_json(producer_path, producer)
        xml = run / 'work/netlist/design.xml'
        tree = ET.parse(xml)
        u1 = next(c for c in tree.getroot().findall('./components/comp') if c.get('ref') == 'U1')
        u1.append(copy.deepcopy(u1.find('footprint')))
        tree.write(xml, encoding='utf-8')
        # Report outputs are write-once; rerun as a fresh check would.
        (run / 'work/fp/native-assignments.json').unlink()
        ctx = Ctx(run, manifest, manifest['scope']['checks'][1], time.monotonic() + 60)
        with self.assertRaises(CheckError) as raised:
            check_footprint_assignment(ctx)
        self.assertEqual(raised.exception.execution, 'malformed')
        report = read_json(run / 'work/fp/native-assignments.json')
        self.assertEqual(report['findings'][0]['code'], 'native_inventory')
        self.assertIn('footprint', report['findings'][0]['message'])

    def test_declared_only_mode_keeps_its_historical_predicate(self):
        from agent_ee.library_checks import capability
        run, _ = self.run_variant('control')
        from agent_ee.storage import read_json
        manifest = read_json(run / 'candidate.json')
        scope = manifest['scope']
        params = dict(scope['checks'][1]['params'])
        self.assertEqual(capability(params, manifest['files'], run / 'inputs', scope, 'fp')['predicates'],
                         ['footprint:native-assignment-parity/1'])
        with self.assertRaisesRegex(ValueError, 'prerequisite'):
            capability(params, manifest['files'], run / 'inputs')
        del params['netlist_check']
        self.assertEqual(capability(params, manifest['files'], run / 'inputs')['predicates'],
                         ['footprint:assignment-parity/1'])
