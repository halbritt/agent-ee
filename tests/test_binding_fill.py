"""Original synthetic captured-part pipeline; never an actual telemetry pod."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from agent_ee.storage import digest, read_json, write_json
from test_parts_subset import captured_fixture
from test_approved_parts import named_fixture


class BindingFillTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.parts = captured_fixture(self, self.root)
        self.record = named_fixture(self.root, self.parts)
        self.intent = {'schema': 'agent-ee.connectivity-intent/1',
            'libraries': [{'name': 'Owned', 'path': 'Owned.kicad_sym', 'sha256': digest(self.root / 'Owned.kicad_sym')}],
            'components': [{'id': 'can/R9', 'reference': 'R1', 'lib_id': 'Owned:R',
                            'footprint': 'Owned:R2', 'value': '10k', 'units': [1]}],
            'nets': [], 'no_connects': [{'component': 'can/R9', 'pin': {'number': p}} for p in ('1', '2')]}
        self.refs = {'schema': 'agent-ee.part-refs/1', 'components': {'can/R9': {'part_ref': 'resistor'}}}
        self.deviations = {'schema': 'agent-ee.part-deviations/1', 'deviations': []}

    def fill(self):
        from agent_ee.binding_fill import fill
        return fill(self.root, self.intent, self.parts, self.refs, self.deviations,
                    captures='.')

    def test_manifest_to_intent(self):
        before = copy.deepcopy((self.intent, self.parts, self.refs, self.deviations))
        intent, report = self.fill()
        fields = intent['components'][0]['part_binding']
        self.assertEqual(fields['LCSC'], 'C38695')
        self.assertEqual(fields['Manufacturer'], 'Synthetic manufacturer')
        self.assertEqual(fields['Partnumber'], 'TJA1051T/3')
        self.assertEqual(fields['Datasheet'], 'https://example.org/synthetic-C38695.pdf')
        self.assertEqual(json.loads(fields['PARAM_Datasheet']), {
            'schema': 'agent-ee.datasheet-binding/1', 'url': fields['Datasheet'],
            'sha256': self.record['artifact']['sha256']})
        self.assertNotIn('JLCPCB_Assembly', fields)
        self.assertEqual(before, (self.intent, self.parts, self.refs, self.deviations))
        self.assertEqual(report['components'][0]['part_ref'], 'resistor')
        self.refs['components'] = {}
        with self.assertRaisesRegex(ValueError, 'part_ref coverage'):
            self.fill()

    def test_writer_binding_mismatch_refused(self):
        fields = self.fill()[0]['components'][0]['part_binding']
        for name, value in fields.items():
            with self.subTest(name=name):
                self.intent['components'][0]['part_binding'] = {name: value}
                self.fill()
                self.intent['components'][0]['part_binding'] = {name: ''}
                with self.assertRaisesRegex(ValueError, 'writer binding mismatch'):
                    self.fill()
        self.intent['components'][0]['part_binding'] = {'Invented': 'data'}
        with self.assertRaisesRegex(ValueError, 'writer binding mismatch'):
            self.fill()

    def test_datasheet_param_is_typed_and_numeric_params_unchanged(self):
        from agent_ee.part_binding import parse_param_value, PartBindingMalformedError
        value = json.loads(self.fill()[0]['components'][0]['part_binding']['PARAM_Datasheet'])
        self.assertEqual(parse_param_value(json.dumps(value), 'PARAM_Datasheet'), value)
        for update in ({'sha256': 'x'}, {'url': 'file:///private'}, {'schema': 'wrong'}, {'extra': True}):
            with self.subTest(update=update), self.assertRaises(PartBindingMalformedError):
                parse_param_value(json.dumps({**value, **update}), 'PARAM_Datasheet')
        with self.assertRaises(PartBindingMalformedError):
            parse_param_value(json.dumps(value), 'PARAM_random')
        self.assertEqual(parse_param_value('{"min":"1","max":"2","unit":"V"}', 'PARAM_voltage'),
                         {'min': '1', 'max': '2', 'unit': 'V'})

    def test_missing_unknown_ref_and_cad_or_capture_mismatch(self):
        for refs in ({}, {'unknown': {'part_ref': 'resistor'}},
                     {'can/R9': {'part_ref': 'missing'}}, {'can/R9': {'part_ref': 'resistor', 'extra': 1}}):
            old = self.refs['components']
            self.refs['components'] = refs
            with self.subTest(refs=refs), self.assertRaises(ValueError):
                self.fill()
            self.refs['components'] = old
        for field in ('reference', 'footprint', 'lib_id'):
            old = self.intent['components'][0][field]
            self.intent['components'][0][field] = 'Owned:Tri' if field == 'lib_id' else 'wrong'
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.fill()
            self.intent['components'][0][field] = old
        original = self.root / 'Owned.kicad_sym'
        (self.root / 'changed.kicad_sym').write_bytes(original.read_bytes() + b'\n')
        self.intent['libraries'][0].update(path='changed.kicad_sym', sha256=digest(self.root / 'changed.kicad_sym'))
        with self.assertRaisesRegex(ValueError, 'symbol capture differs'):
            self.fill()

    def test_power_exemption_is_resolved_and_pin_defects_remain_emitter_owned(self):
        self.intent['components'].append({'id': 'gnd', 'reference': '#PWR01', 'lib_id': 'Owned:GND',
            'value': 'GND', 'footprint': '', 'units': [1]})
        self.intent['no_connects'].append({'component': 'gnd', 'pin': {'number': '1'}})
        self.intent['no_connects'][0]['pin']['number'] = '999'
        output, _ = self.fill()
        self.assertNotIn('part_binding', output['components'][1])
        self.refs['components']['gnd'] = {'part_ref': 'resistor'}
        with self.assertRaisesRegex(ValueError, 'part_ref coverage'):
            self.fill()

    def test_capture_hash_and_datasheet_acquisition_mismatch_refuse(self):
        from agent_ee.parts_manifest import qualify_manifest
        sheet = self.root / self.record['artifact']['path']
        raw = sheet.read_bytes()
        sheet.write_bytes(raw + b'changed')
        with self.assertRaises(ValueError):
            self.fill()
        sheet.write_bytes(raw)
        self.parts['parts'][0]['datasheet']['provenance']['locator'] = 'parts[id=wrong]'
        with self.assertRaises(ValueError):
            qualify_manifest(self.parts, self.root)

    def test_failed_receipt_cannot_adopt_conflicting_binding(self):
        from agent_ee.binding_fill import configuration
        from agent_ee.parts_manifest import qualify_manifest
        from agent_ee.stage_receipts import run_step, validate_receipt, adopt
        from toolchain import require_toolchain
        import shutil
        require_toolchain(native=True)
        (self.root / 'captures').mkdir()
        shutil.copyfile(self.root / 'Owned.kicad_sym', self.root / 'captures/Owned.kicad_sym')
        self.intent['components'][0]['part_binding'] = {'LCSC': 'C999'}
        for path, value in [('intent.json', self.intent), ('refs.json', self.refs), ('deviations-current.json', self.deviations)]:
            write_json(self.root / path, value)
        before = digest(self.root / 'intent.json')
        spec = configuration(Path(__file__).resolve().parents[1] / 'src', intent='intent.json',
            manifest='parts.json', part_refs='refs.json', deviations='deviations-current.json', captures='captures',
            dependencies=qualify_manifest(self.parts, self.root)['dependencies'])
        step = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'failure'
        receipt = run_step(step, self.root, spec, {'implementation': 1, 'source_candidate_id': 'synthetic', 'receipt_index': 1})
        self.assertEqual(validate_receipt(step), receipt)
        self.assertEqual(receipt['exit_code'], 1)
        self.assertIsNone(receipt['outputs']['intent.json'])
        self.assertIn('writer binding mismatch', read_json(step / 'run/out/binding-fill.json')['reason'])
        with self.assertRaisesRegex(ValueError, 'no output adopted'):
            adopt(step, receipt, self.root)
        self.assertEqual(digest(self.root / 'intent.json'), before)

    def test_separate_output_keeps_read_intent_unwritten(self):
        from agent_ee.binding_fill import configuration
        from agent_ee.parts_manifest import qualify_manifest
        from agent_ee.stage_receipts import run_step, validate_receipt, adopt
        from toolchain import require_toolchain
        import shutil
        require_toolchain(native=True)
        (self.root / 'captures').mkdir()
        shutil.copyfile(self.root / 'Owned.kicad_sym', self.root / 'captures/Owned.kicad_sym')
        for path, value in [('intent.json', self.intent), ('intent.bound.json', {}), ('refs.json', self.refs),
                            ('deviations-current.json', self.deviations)]:
            write_json(self.root / path, value)
        before = digest(self.root / 'intent.json')
        kwargs = dict(intent='intent.json', manifest='parts.json', part_refs='refs.json',
                      deviations='deviations-current.json', captures='captures',
                      dependencies=qualify_manifest(self.parts, self.root)['dependencies'])
        source = Path(__file__).resolve().parents[1] / 'src'
        spec = configuration(source, output='intent.bound.json', **kwargs)
        self.assertEqual(spec['writes'], {'intent.bound.json': 'out/intent.json'})
        self.assertIn('intent.json', spec['inputs'])
        self.assertEqual(configuration(source, **kwargs)['writes'], {'intent.json': 'out/intent.json'})
        with self.assertRaisesRegex(ValueError, 'output intent'):
            configuration(source, **{**kwargs, 'output': 'refs.json'})
        self.assertIn('.', configuration(source, **{**kwargs, 'captures': '.'})['argv'])
        for unsafe in ('..', '/captures', './captures'):
            with self.subTest(captures=unsafe), self.assertRaises(ValueError):
                configuration(source, **{**kwargs, 'captures': unsafe})
        # Libraries captured at the candidate root, as in the actual pod intent.
        spec = configuration(source, output='intent.bound.json', **{**kwargs, 'captures': '.'})
        step = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'separate'
        receipt = run_step(step, self.root, spec, {'implementation': 1, 'source_candidate_id': 'synthetic', 'receipt_index': 1})
        self.assertEqual(validate_receipt(step), receipt)
        self.assertEqual(receipt['status'], 'completed', (step / 'run/console.log').read_text())
        adopt(step, receipt, self.root)
        self.assertEqual(digest(self.root / 'intent.json'), before)
        self.assertEqual(read_json(self.root / 'intent.bound.json'), self.fill()[0])


class ExternalBindingTests(BindingFillTests):
    def setUp(self):
        super().setUp()
        from agent_ee.part_record import prepare_external_record
        self.named_part = copy.deepcopy(self.parts['parts'][0])
        self.deviations['deviations'] = [{'requirement_id': 'POD-CD-HEADER',
            'original': {'mpn': '776228-1', 'lcsc': None}, 'assembly': 'consigned',
            'reason': 'Owned test hand-fit decision.', 'purchase_source': 'Synthetic purchase-list entry',
            'stock_deviation': {'reason': 'No JLC assembly supply declared.'},
            'evidence_refs': [{'path': 'identity-source.txt', 'locator': 'line 1'}]}]
        (self.root / 'identity-source.txt').write_text('Owned synthetic header identity; no vendor claim.\n')
        write_json(self.root / 'external-identity.json', {'schema': 'agent-ee.external-part-identity/1',
            'mpn': '776228-1', 'manufacturer': 'Synthetic manufacturer', 'package': 'Synthetic header',
            'evidence_refs': [{'path': 'identity-source.txt', 'sha256': digest(self.root / 'identity-source.txt'),
                               'locator': 'line 1'}]})
        write_json(self.root / 'deviations.json', self.deviations)
        self.record = prepare_external_record(self.root,
            {'path': 'external-identity.json', 'sha256': digest(self.root / 'external-identity.json'), 'locator': '/'},
            self.record['datasheets'], self.record['datasheet_id'],
            {'path': 'deviations.json', 'sha256': digest(self.root / 'deviations.json'), 'index': 0})
        write_json(self.root / 'external-part.json', self.record)
        part = self.parts['parts'][0]
        part.update(lcsc='NO_LCSC', mpn='776228-1', package='Synthetic header', stock=None)
        part['selection'].update(kind='external', lcsc='NO_LCSC', part_record='external-part.json',
                                 part_record_sha256=digest(self.root / 'external-part.json'))
        write_json(self.root / 'parts.json', self.parts)

    def test_manifest_to_intent(self):
        from agent_ee.parts_manifest import qualify_manifest
        qualified = qualify_manifest(self.parts, self.root)
        self.assertIsNone(qualified['selection'])
        self.assertIn('identity-source.txt', qualified['dependencies'])
        self.assertIn('deviations.json', qualified['dependencies'])
        fields = self.fill()[0]['components'][0]['part_binding']
        self.assertEqual(fields['LCSC'], 'NO_LCSC')
        self.assertEqual(fields['Partnumber'], '776228-1')
        self.assertEqual(fields['JLCPCB_Assembly'], 'not_assembled')
        self.assertIsNone(self.parts['parts'][0]['stock'])

    def test_external_mutations_refuse(self):
        from agent_ee.parts_manifest import qualify_manifest
        for field, bad in [('mpn', 'wrong'), ('stock', 0), ('stock', 1), ('lcsc', 'C1')]:
            original = self.parts['parts'][0][field]
            self.parts['parts'][0][field] = bad
            with self.subTest(field=field, value=bad), self.assertRaises(ValueError):
                qualify_manifest(self.parts, self.root)
            self.parts['parts'][0][field] = original
        for path in ('deviations.json', 'identity-source.txt', 'external-identity.json'):
            target = self.root / path
            original = target.read_bytes()
            target.write_bytes(original + b' ')
            with self.subTest(path=path), self.assertRaises(ValueError):
                qualify_manifest(self.parts, self.root)
            target.write_bytes(original)
        self.deviations['deviations'] = []
        with self.assertRaisesRegex(ValueError, 'assembly deviation'):
            self.fill()

    def test_record_rederivation_and_deviation_requalification(self):
        from agent_ee.part_record import qualify_external_record, prepare_external_record
        for field, bad in [('manufacturer', 'forged'), ('package', 'forged'), ('stock', 0)]:
            record = {**self.record, field: bad}
            with self.subTest(field=field), self.assertRaises(ValueError):
                qualify_external_record(record, self.root)
        changed = copy.deepcopy(self.deviations)
        changed['deviations'][0]['original']['mpn'] = 'wrong'
        write_json(self.root / 'deviations.json', changed)
        pin = {**self.record['deviation_ref'], 'sha256': digest(self.root / 'deviations.json')}
        with self.assertRaisesRegex(ValueError, 'differs from hand-fit'):
            prepare_external_record(self.root, self.record['identity_source'], self.record['datasheets'],
                                    self.record['datasheet_id'], pin)
        changed['deviations'] = []
        write_json(self.root / 'deviations.json', changed)
        pin['sha256'] = digest(self.root / 'deviations.json')
        with self.assertRaisesRegex(ValueError, 'pinned assembly deviation'):
            prepare_external_record(self.root, self.record['identity_source'], self.record['datasheets'],
                                    self.record['datasheet_id'], pin)


class BindingScaleBoundaryTests(unittest.TestCase):
    def test_component_boundary_and_pre_resolution_refusal(self):
        from agent_ee.netlist_parity import terminal_inventory, _resolve_captured_intent, Unsupported
        components = {str(i): {'pins': ['1']} for i in range(256)}
        self.assertEqual(len(terminal_inventory(components, {})['components']), 256)
        components['overflow'] = {'pins': ['1']}
        with self.assertRaisesRegex(Unsupported, 'component inventory limit'):
            terminal_inventory(components, {})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # Refused before any unavailable symbol library is opened or resolved.
            write_json(root / 'intent.json', {'components': list(components),
                                              'libraries': [{'path': 'missing.kicad_sym'}]})
            with self.assertRaisesRegex(Unsupported, 'before resolution'):
                _resolve_captured_intent(root, 'intent.json')
        with self.assertRaisesRegex(Unsupported, 'terminal inventory limit'):
            terminal_inventory({'one': {'pins': [str(i) for i in range(8193)]}}, {})

    def test_resolved_record_overflow_stays_unsupported(self):
        from agent_ee.connectivity_ir import resolve_intent
        from agent_ee.netlist_parity import resolved_inventory, Unsupported
        from test_native_binding import GEN, LIBRARY
        document = {'schema': 'agent-ee.connectivity-intent/1', 'libraries': [LIBRARY],
            'components': [{'id': str(i), 'reference': f'R{i+1}', 'lib_id': 'Owned:R',
                            'value': '10k', 'footprint': 'Owned:R2', 'units': [1]} for i in range(257)],
            'nets': [], 'no_connects': [{'component': str(i), 'pin': {'number': p}}
                                       for i in range(257) for p in ('1', '2')]}
        record = resolve_intent(document, {'Owned.kicad_sym': GEN.library()})
        self.assertEqual(record['outcome'], 'pass', record['findings'])
        with self.assertRaisesRegex(Unsupported, 'component inventory limit'):
            resolved_inventory(record)


class NativeBindingFillTests(unittest.TestCase):
    def test_130_component_receipted_fields_parity_and_placement(self):
        import os
        import shutil
        import pcbnew as k
        from toolchain import require_toolchain
        from agent_ee import binding_fill, schematic_stage, placement_adapter
        from agent_ee.part_binding import validate_part_bindings
        from agent_ee.parts_manifest import qualify_manifest
        from agent_ee.stage_receipts import run_step, validate_receipt, adopt
        from agent_ee.assembly_readiness import evaluate_assembly_readiness
        from test_assembly_readiness import _write_sqlite_catalog
        require_toolchain(native=True)
        helper = ExternalBindingTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        root = helper.root
        intent, parts = helper.intent, helper.parts
        external = parts['parts'][0]
        external.update(id='external', selection_id='external', instances=[{'sheet': '/', 'reference': 'R130'}])
        named = copy.deepcopy(helper.named_part)
        named['instances'] = [{'sheet': '/', 'reference': 'R' + str(i)} for i in range(1, 130)]
        parts['parts'].append(named)
        assignments = read_json(root / 'assignments.json')
        other = copy.deepcopy(assignments['components'][0])
        other['reference'] = 'external'
        assignments['components'].append(other)
        write_json(root / 'assignments.json', assignments)
        parts['cad']['assignments']['sha256'] = digest(root / 'assignments.json')
        template = intent['components'][0]
        intent['components'] = [{**template, 'id': f'flat/R{i}', 'reference': f'R{i}'} for i in range(1, 131)]
        intent['no_connects'] = [{'component': c['id'], 'pin': {'number': p}}
                                  for c in intent['components'] for p in ('1', '2')]
        refs = {'schema': 'agent-ee.part-refs/1', 'components': {
            c['id']: {'part_ref': 'external' if c['reference'] == 'R130' else 'resistor'}
            for c in intent['components']}}
        for name, value in [('intent.json', intent), ('parts.json', parts), ('refs.json', refs)]:
            write_json(root / name, value)
        captures = root / 'captures'
        captures.mkdir()
        shutil.copyfile(root / 'Owned.kicad_sym', captures / 'Owned.kicad_sym')
        shutil.copytree(root / 'Owned.pretty', captures / 'Owned.pretty')
        layout = {'schema': 'agent-ee.schematic-layout/1', 'project': 'benchmark',
                  'namespace': '6f0f7f0e-5c43-5b8e-9a51-2a3f2c1d0e40', 'transforms': {}}
        write_json(root / 'layout.json', layout)
        k.SaveBoard(str(root / 'benchmark.kicad_pcb'), k.BOARD())
        (root / 'benchmark.kicad_sch').write_bytes(b'')
        source = Path(__file__).resolve().parents[1] / 'src'
        qualified = qualify_manifest(parts, root)
        steps = [binding_fill.configuration(source, intent='intent.json', manifest='parts.json',
            part_refs='refs.json', deviations='deviations.json', captures='captures',
            dependencies=qualified['dependencies'])]
        steps += schematic_stage.configuration(source, board='benchmark.kicad_pcb', schematic='benchmark.kicad_sch',
            intent='intent.json', layout='layout.json', captures='captures', footprints='cad.json')['implementation_steps']
        receipts = Path(self.enterContext(tempfile.TemporaryDirectory()))
        previous = None
        for i, spec in enumerate(steps, 1):
            path = receipts / str(i)
            receipt = run_step(path, root, spec, {'implementation': 1, 'source_candidate_id': 'synthetic-130', 'receipt_index': i})
            if receipt['status'] != 'completed' and os.environ.get('AGENT_EE_TEST_EVIDENCE'):
                failed = Path(tempfile.mkdtemp(prefix=f'failed-step-{i}-', dir=os.environ['AGENT_EE_TEST_EVIDENCE']))
                shutil.copytree(path, failed / 'receipt')
            self.assertEqual(receipt['status'], 'completed', {'step': i, 'violations': receipt['violations'],
                'console': (path / 'run/console.log').read_text()})
            self.assertEqual(validate_receipt(path), receipt)
            if previous:
                for target, value in previous['outputs'].items():
                    self.assertEqual(receipt['inputs'][target], value)
            adopt(path, receipt, root)
            previous = receipt
        schematic, board = (root / 'benchmark.kicad_sch').read_bytes(), (root / 'benchmark.kicad_pcb').read_bytes()
        report = validate_part_bindings(schematic, board, require_datasheet=True)
        self.assertEqual((report['execution'], report['outcome']), ('ok', 'pass'), report)
        self.assertEqual(len(report['evaluated_references']), 130)
        self.assertEqual(report['missing_bindings'], [])
        filled = read_json(root / 'intent.json')
        parsed = k.LoadBoard(str(root / 'benchmark.kicad_pcb'))
        actual = {fp.GetReference(): {field.GetName(): field.GetText() for field in fp.GetFields()} for fp in parsed.GetFootprints()}
        for comp in filled['components']:
            for field, value in comp['part_binding'].items():
                self.assertEqual(actual[comp['reference']][field], value, (comp['reference'], field))
        for fp in parsed.GetFootprints():
            fp.SetPosition(k.VECTOR2I(fp.GetPosition().x + 1_000_000, fp.GetPosition().y + 2_000_000))
        k.SaveBoard(str(root / 'moved.kicad_pcb'), parsed)
        placement = placement_adapter.write_scope(board, (root / 'moved.kicad_pcb').read_bytes())
        self.assertEqual(placement['status'], 'allowed', placement)
        self.assertEqual(len(placement['transforms']), 130)
        # External identity stays out of JLC stock aggregation; the ordinary167 check remains decisive.
        catalog = root / 'assembly-catalog'
        catalog.mkdir()
        _write_sqlite_catalog(catalog, [{'lcsc': 'C38695', 'mpn': 'TJA1051T/3', 'stock': 1000}])
        xml = receipts / '3/run/out/netlist/design.xml'
        params = {'catalog': 'assembly-catalog/catalog-manifest.json', 'deviations': 'deviations.json',
                  'require_passive_ratings': False}
        assembly = evaluate_assembly_readiness(root, xml, params)
        self.assertEqual(assembly['outcome'], 'pass', assembly)
        self.assertEqual(assembly['counts']['unique_lcsc'], 1)
        self.assertEqual(assembly['counts']['declared_deviation'], 1)
        self.assertEqual(set(assembly['parts']), {'C38695'})
        # Native parity detects a changed hash even when the string remains valid typed provenance.
        forged = board.replace(helper.record['artifact']['sha256'].encode(), b'0' * 64, 1)
        self.assertEqual(validate_part_bindings(schematic, forged)['outcome'], 'defect')
        if os.environ.get('AGENT_EE_TEST_EVIDENCE'):
            dest = Path(os.environ['AGENT_EE_TEST_EVIDENCE']) / 'synthetic-130'
            shutil.copytree(root, dest / 'candidate')
            shutil.copytree(receipts, dest / 'receipts')
            write_json(dest / 'part-binding.json', report)
            write_json(dest / 'placement.json', placement)
            write_json(dest / 'assembly.json', assembly)


if __name__ == '__main__':
    unittest.main()
