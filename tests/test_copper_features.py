"""Original two-pad copper membership fixtures and fail-closed boundaries."""
import copy
import tempfile
import unittest
from pathlib import Path

from agent_ee import copper_features as cf
from agent_ee.parts_manifest import qualify_manifest, validate_manifest
from agent_ee.parts_subset import compare_inventory
from agent_ee.storage import digest, identity, write_json
from agent_ee.symbol_library import parse_library, resolve_symbol
from tests.test_parts_subset import captured_fixture, comparison_fixture


def fixture(test, root):
    document = captured_fixture(test, root)
    fp = root / 'Owned.pretty/R2.kicad_mod'
    fp.write_text(fp.read_text().replace('(attr smd)', '(attr smd exclude_from_bom exclude_from_pos_files)'))
    import json
    cad = json.loads((root / 'cad.json').read_text())
    # Keep the existing CAD manifest contract and recompute its pinned member.
    def repin(node):
        if isinstance(node, dict):
            if node.get('path') == 'Owned.pretty/R2.kicad_mod':
                node['sha256'] = digest(fp)
            for v in node.values():
                repin(v)
        elif isinstance(node, list):
            for v in node:
                repin(v)
    repin(cad)
    write_json(root / 'cad.json', cad)
    document['cad']['manifest']['sha256'] = digest(root / 'cad.json')
    document['cad']['members_sha256'] = identity(sorted([
        dict(path='Owned.kicad_sym', sha256=digest(root / 'Owned.kicad_sym')),
        dict(path='Owned.pretty/R2.kicad_mod', sha256=digest(fp))], key=lambda m: m['path']))
    symbols = parse_library((root / 'Owned.kicad_sym').read_bytes())
    pins, _, _ = resolve_symbol(symbols, 'R', '', '')
    pins = [{k: p[k] for k in ('number', 'name', 'electrical_type')} for p in pins]
    document.update(catalog=None, selection=None)
    document['parts'] = [dict(kind='copper_feature', id='resistor', role='copper jumper', required=True,
        lib_id='Owned:R', footprint='Owned:R2', instances=[dict(sheet='/', reference='R1',
        nets={'1': 'VIN', '2': 'VOUT'})], copper=dict(
        symbol=dict(path='Owned.kicad_sym', sha256=digest(root / 'Owned.kicad_sym')),
        footprint=dict(path='Owned.pretty/R2.kicad_mod', sha256=digest(fp)), pins=pins,
        native_flags=dict(in_bom=False, dnp=False, exclude_from_board=False)))]
    return document


class CopperTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.document = fixture(self, self.root)

    def test_complete_captured_qualification_and_preparation(self):
        from agent_ee.parts_manifest import prepare_manifest
        qualified = qualify_manifest(self.document, self.root)
        self.assertEqual(qualified['missing_provenance'], [])
        self.assertIsNone(qualified['selection'])
        self.assertEqual(prepare_manifest(self.document, self.root), self.document)
        self.assertIn('Owned.pretty/R2.kicad_mod', qualified['dependencies'])

    def compare(self, mutate=lambda d, g: None):
        document = copy.deepcopy(self.document)
        _, graph = comparison_fixture()
        c = graph['components']['R1']
        c.update(in_bom=False, properties={'exclude_from_bom': None})
        record = cf.declarations(document, self.root)['R1']
        record['nets'] = {pin: next(n['name'] for n in graph['nets'].values()
            if ('R1', pin) in n['terminals']) for pin in c['pins']}
        mutate(document, graph)
        return compare_inventory(document, graph, [], copper={'R1': record})

    def test_consumers_refuse_purchased_identity_for_copper(self):
        from agent_ee.datasheet_selection import datasheet
        from agent_ee.passive_ratings import derive
        from agent_ee.rung1_parts import fields_for_rung
        part = self.document['parts'][0]
        params = dict(header=dict(assembly='assembled', ref='J1'),
                      resistor=dict(ref='R1'), capacitor=dict(ref='C1'))
        for consume in (lambda: datasheet(self.root, part),
                        lambda: derive(self.root, part, 'R'),
                        lambda: fields_for_rung(self.document, qualify_manifest(self.document, self.root), params)):
            with self.subTest(consumer=consume), self.assertRaisesRegex(ValueError, 'copper_feature'):
                consume()

    def test_scaffold_fixed_copper_refuses_before_generation(self):
        from agent_ee.scaffold import write_envelope_content
        from agent_ee.storage import read_json
        envelope = read_json(Path(__file__).resolve().parents[1] / 'fixtures/board-envelope/rc.envelope.json')
        envelope['fixed_footprints'] = [dict(reference='R1', side='front', edge='left', position_mm=10, inset_mm=2,
            rotation_deg=0, insertion='from_top', tolerance_mm=0)]
        write_json(self.root / 'envelope.json', envelope)
        write_json(self.root / 'parts.json', self.document)
        from agent_ee.scaffold import board
        from agent_ee.board_envelope import validate
        pcb = board(validate((self.root / 'envelope.json').read_bytes()), 'owned-copper-scaffold')
        (self.root / 'design.kicad_pcb').write_bytes(pcb)
        write_json(self.root / 'intent.json', dict(schema='agent-ee.connectivity-intent/1',
            libraries=[dict(name='Owned', path='Owned.kicad_sym', sha256=digest(self.root / 'Owned.kicad_sym'))],
            components=[dict(id='resistor', reference='R1', lib_id='Owned:R', footprint='Owned:R2',
                             value='copper jumper', units=[1])], nets=[],
            no_connects=[dict(component='resistor', pin=dict(number=p)) for p in ('1', '2')]))
        write_json(self.root / 'layout.json', dict(schema='agent-ee.schematic-layout/1',
            namespace='6506234f-1932-45c7-a667-380bc1f26ea8', project='design', transforms={}))
        with self.assertRaisesRegex(ValueError, 'fixed-footprint scaffold.*copper_feature'):
            write_envelope_content(self.root, 'envelope.json', 'parts.json', 'design.kicad_pcb',
                                   intent_rel='intent.json', layout_rel='layout.json')
        self.assertEqual((self.root / 'design.kicad_pcb').read_bytes(), pcb)

    def test_fab_parts_declarations_must_agree_in_both_directions(self):
        write_json(self.root / 'parts.json', self.document)
        files = {'parts.json': digest(self.root / 'parts.json')}
        scope = dict(checks=[dict(id='parts', kind='parts_subset',
            params=dict(manifest='parts.json', netlist_check='netlist'))], mutable_inputs=[])
        params = dict(netlist_check='netlist', copper_features=['R1'])
        rows = cf.fab_parts_manifests(params, scope, files, self.root)
        self.assertEqual(rows[0]['references'], ['R1'])
        for refs in ([], ['JP2'], ['R1', 'JP2']):
            changed = dict(netlist_check='netlist')
            if refs:
                changed['copper_features'] = refs
            with self.subTest(refs=refs), self.assertRaisesRegex(ValueError, 'fab/parts copper declarations differ'):
                cf.fab_parts_manifests(changed, scope, files, self.root)
        other = copy.deepcopy(scope)
        other['checks'][0]['params']['netlist_check'] = 'different-netlist'
        self.assertEqual(cf.fab_parts_manifests(params, other, files, self.root), [])
        purchased_root = self.root / 'purchased'
        purchased_root.mkdir()
        purchased = captured_fixture(self, purchased_root)
        write_json(purchased_root / 'parts.json', purchased)
        purchased_files = {'parts.json': digest(purchased_root / 'parts.json')}
        self.assertEqual(cf.fab_parts_manifests(dict(netlist_check='netlist'), scope,
                                              purchased_files, purchased_root), [])
        with self.assertRaisesRegex(ValueError, 'fab/parts copper declarations differ'):
            cf.fab_parts_manifests(params, scope, purchased_files, purchased_root)
        scope['mutable_inputs'] = ['parts.json']
        with self.assertRaisesRegex(ValueError, 'captured and immutable'):
            cf.fab_parts_manifests(params, scope, files, self.root)
        scope['mutable_inputs'] = []
        (self.root / 'parts.json').write_bytes((self.root / 'parts.json').read_bytes() + b'\n')
        with self.assertRaisesRegex(ValueError, 'changed'):
            cf.fab_parts_manifests(params, scope, files, self.root)

    def test_fab_capabilities_bind_copper_membership_for_export_and_package(self):
        from agent_ee.capabilities import describe_check
        write_json(self.root / 'parts.json', self.document)
        for rel in ('design.kicad_pcb', 'design.kicad_sch'):
            (self.root / rel).write_text('original fixture capability input')
        files = {p.relative_to(self.root).as_posix(): digest(p)
                 for p in self.root.rglob('*') if p.is_file()}
        for kind in ('fab_export', 'fab_package'):
            netlist = dict(id='netlist', kind='netlist')
            parts = dict(id='parts', kind='parts_subset', params=dict(manifest='parts.json', netlist_check='netlist'))
            fab = dict(id='fab', kind=kind, params=dict(netlist_check='netlist', copper_features=['R1']))
            scope = dict(checks=[netlist, parts, dict(id='geometry', kind='geometry'), fab],
                         board='design.kicad_pcb', schematic='design.kicad_sch', mutable_inputs=[])
            def describe():
                return describe_check(fab, check_specs=scope['checks'], files=files, scope=scope, inputs=self.root)
            descriptor = describe()
            self.assertEqual(descriptor['status'], 'supported', descriptor)
            self.assertIn(dict(check_id='parts', kind='parts_subset'), descriptor['prerequisites'])
            self.assertIn('parts.json', descriptor['immutable_inputs'])
            fab['params'].pop('copper_features')
            descriptor = describe()
            self.assertEqual(descriptor['status'], 'unsupported', descriptor)
            self.assertIn('fab/parts copper declarations differ', descriptor['unsupported_reasons'][0]['message'])
            fab['params']['copper_features'] = ['R1']
            scope['checks'].remove(parts)
            scope['checks'].append(parts)
            descriptor = describe()
            self.assertEqual(descriptor['status'], 'unsupported', descriptor)

    def test_pin_contracts_copper_identity_is_explicitly_unsupported(self):
        from agent_ee.pin_contracts import _bindings
        _, graph = comparison_fixture()
        rows, coverage = _bindings(self.document, {}, graph, {}, {})
        self.assertEqual(rows, [])
        self.assertEqual(coverage[0]['status'], 'unsupported')
        self.assertEqual(coverage[0]['reason'], 'copper feature is not purchased-part electrical evidence')

    def test_native_membership_and_refusals(self):
        self.assertEqual(self.compare()['outcome'], 'pass')
        for key, value in [('in_bom', True), ('on_board', False), ('populated', False),
                           ('footprint', 'Owned:Wrong'), ('pins', ('1',))]:
            with self.subTest(key=key):
                self.assertEqual(self.compare(lambda d, g: g['components']['R1'].update({key: value}))['outcome'], 'defect')
        self.assertEqual(self.compare(lambda d, g: d['parts'].clear())['outcome'], 'defect')
        self.assertEqual(self.compare(lambda d, g: g['components']['R1']['properties'].update(LCSC='C1'))['outcome'], 'defect')
        self.assertEqual(self.compare(lambda d, g: g['nets'].clear())['outcome'], 'defect')

    def test_schema_refuses_purchased_metadata_dnp_and_net_gaps(self):
        for mutate in [lambda p: p.update(lcsc='C1'), lambda p: p.update(datasheet={}),
                       lambda p: p.update(required=False),
                       lambda p: p['copper']['native_flags'].update(dnp=True),
                       lambda p: p['instances'][0]['nets'].pop('2'),
                       lambda p: p['instances'][0].update(reference='#JP1')]:
            d = copy.deepcopy(self.document)
            mutate(d['parts'][0])
            with self.assertRaises(ValueError):
                validate_manifest(d)

    def test_source_pad_count_supplier_and_hash_refusals(self):
        fp = self.root / 'Owned.pretty/R2.kicad_mod'
        original = fp.read_bytes()
        for data in [original.replace(b'(pad "2" smd rect (at 1 0) (size 1 1) (layers "F.Cu" "F.Paste" "F.Mask"))', b''),
                     original.replace(b'(pad "2"', b'(pad "3"'),
                     original.rstrip()[:-1] + b'(pad "3" smd rect (at 0 0) (size 1 1) (layers "F.Cu" "F.Mask")))',
                     original.replace(b'(pad "2"', b'(pad "1"'),
                     original.replace(b'(attr smd', b'(property "LCSC" "C1") (attr smd'),
                     original.replace(b'exclude_from_pos_files', b'')]:
            with self.subTest(data=data):
                fp.write_bytes(data)
                d = copy.deepcopy(self.document)
                d['parts'][0]['copper']['footprint']['sha256'] = digest(fp)
                with self.assertRaises(ValueError):
                    cf.qualify(d['parts'][0], self.root)
        fp.write_bytes(original + b'\n')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            cf.qualify(self.document['parts'][0], self.root)

    def test_native_sandboxed_pipeline_and_embedded_source_tamper(self):
        import re
        from agent_ee.cli import verify
        from agent_ee.storage import read_json
        from tests.test_parts_subset import NativePartsPipelineTests, subset_generator
        from tests.toolchain import require_toolchain
        require_toolchain(native=True)
        helper = NativePartsPipelineTests()
        helper.root, helper.document, helper.count = self.root, self.document, 0
        base = subset_generator().schematic()
        # Authored fixture instances use the same BOM flag as real copper jumpers.
        for name in ('LCSC', 'MPN', 'Manufacturer', 'JLCPCB_Assembly', 'DNP'):
            base = re.sub(r'\(property "' + name + r'".*?\(hide yes\)\)\)', '', base)
        # Source libraries retain their exact original bytes; only instance flags differ.
        helper.schematic = lambda variant: base
        self.document['parts'][0]['instances'][0]['nets'] = {
            '1': 'unconnected-(R1-P1-Pad1)', '2': 'unconnected-(R1-P2-Pad2)'}
        run, result = helper.run_case(document=self.document)
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        verify(run)
        import time
        from agent_ee.checks import Ctx, CheckError
        manifest = read_json(run / 'candidate.json')
        params = dict(netlist_check='netlist', copper_features=['R1'])
        spec = dict(id='fab', kind='fab_export', params=params)
        ctx = Ctx(run, manifest, spec, time.monotonic() + 120)
        rows = cf.fab_membership(ctx, params)
        self.assertEqual(rows[0]['references'], ['R1'])
        self.assertIn('work/parts/parts-subset.json', ctx.result['evidence'])
        self.assertIn('work/netlist/design.xml', ctx.result['evidence'])
        membership = run / 'checks/parts.json'
        original = membership.read_bytes()
        changed_result = read_json(membership)
        changed_result['outcome'] = 'defect'
        write_json(membership, changed_result)
        with self.assertRaisesRegex(ValueError, 'passing, source-bound'):
            cf.fab_membership(ctx, params)
        membership.write_bytes(original)
        report_path = run / 'work/parts/parts-subset.json'
        original_report = report_path.read_bytes()
        changed_report = read_json(report_path)
        changed_report['native_xml_sha256'] = '0' * 64
        write_json(report_path, changed_report)
        with self.assertRaisesRegex(ValueError, 'passing, source-bound'):
            cf.fab_membership(ctx, params)
        report_path.write_bytes(original_report)
        membership.unlink()
        with self.assertRaises(CheckError):
            cf.fab_membership(ctx, params)
        membership.write_bytes(original)
        from agent_ee.cli import run_review
        scope = read_json(self.root / 'scope.json')
        scope['schema'] = 'agent-ee.scope/2'
        for requirement in scope['baseline']['requirements']:
            requirement.pop('intent', None)
        scope['checks'].append(dict(id='fab', kind='fab_export',
            params=dict(netlist_check='netlist', copper_features=['JP2'])))
        scope['baseline']['requirements'][0]['checks'].append('fab')
        scope_path = self.root / 'scope-fab-mismatch.json'
        write_json(scope_path, scope)
        mismatch_run = self.root / 'run-fab-mismatch'
        run_review(scope_path, mismatch_run, 120, checks_only=True)
        self.assertTrue((mismatch_run / 'checks/fab.json').exists(), read_json(mismatch_run / 'result.json').get('error'))
        refused = read_json(mismatch_run / 'checks/fab.json')
        self.assertEqual((refused['execution'], refused['outcome']), ('malformed', 'unknown'))
        self.assertIn('fab/parts copper declarations differ', refused['summary'])
        self.assertFalse((mismatch_run / 'work/fab/fab').exists())
        # Change only embedded CAD, preserving the native selected identity.
        helper.schematic = lambda variant: base.replace('(property "Value" "R"', '(property "Value" "Changed source value"', 1)
        _, changed = helper.run_case(document=self.document)
        self.assertEqual(changed['outcome'], 'unknown', changed)
        self.assertEqual(changed['details']['report']['findings'][0]['message'],
                         'R1: copper embedded symbol changed')
