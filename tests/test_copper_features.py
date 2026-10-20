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
        # Change only embedded CAD, preserving the native selected identity.
        helper.schematic = lambda variant: base.replace('(property "Value" "R"', '(property "Value" "Changed source value"', 1)
        _, changed = helper.run_case(document=self.document)
        self.assertEqual(changed['outcome'], 'unknown', changed)
        self.assertEqual(changed['details']['report']['findings'][0]['message'],
                         'R1: copper embedded symbol changed')
