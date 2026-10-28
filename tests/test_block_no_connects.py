"""CPU composition regression; authored XML substitutes for export, never proves it."""
import copy
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

from agent_ee.block_adaptation import compose_flat
from agent_ee.block_composition import compare, emit, models, validate
from agent_ee.connectivity_ir import resolve_intent
from agent_ee.hardware_interface import marker_isolation, sheet_items
from agent_ee.native_netlist import read_native_inventory
from agent_ee.reference_blocks import capture_block
from agent_ee.schematic_emitter import emit_schematic
from agent_ee.schematic_emit import Quoted, format_sexpr, parse_quoted
from agent_ee.storage import read_json, write_json
from agent_ee.symbol_library import children, field


ROOT_UUID = 'b2e692b5-8926-44d7-ad8b-372ea736bc29'
UUIDS = {'U1': 'abbe5472-857b-4cd3-8f03-ec83242d44a1',
         'U2': 'bf79788e-85bc-4dc0-9223-c9f4b9b2ca35'}


def source_fixture():
    # Abstract packages retain the two acquisition NC pin numbers. All other
    # passive pins share one synthetic signal; no electrical behavior is modeled.
    libraries, symbols, components, libparts, common, opens = [], [], [], [], [], []
    for index, (ref, count, nc) in enumerate((('U1', 5, 4), ('U2', 10, 2))):
        name = 'Package' + str(count)
        pins = ''.join(f'(pin {"no_connect" if ref == "U1" and pin == nc else "passive"} line '
            f'(at 0 {pin * 2.54} 0) (length 1) (name "P{pin}") (number "{pin}"))'
            for pin in range(1, count + 1))
        libraries.append(f'(symbol "Owned:{name}" (property "Reference" "U") '
                         f'(property "Value" "{name}") (symbol "{name}_1_1" {pins}))')
        x = 40 + 40 * index
        symbols.append(f'(symbol (lib_id "Owned:{name}") (at {x} 50 0) (unit 1) '
            f'(in_bom yes) (on_board yes) (dnp no) (uuid "{UUIDS[ref]}") '
            f'(property "Reference" "{ref}") (property "Value" "{name}") '
            '(property "Footprint" "Owned:Package") '
            f'(instances (project "source" (path "/{ROOT_UUID}" (reference "{ref}") (unit 1)))))')
        symbols.append(f'(no_connect (at {x} {50 - nc * 2.54:g}) '
                       f'(uuid "e0000000-0000-4000-8000-00000000000{index}"))')
        component = ET.Element('comp', ref=ref)
        for key, value in (('value', name), ('footprint', 'Owned:Package'),
                           ('datasheet', ''), ('tstamps', UUIDS[ref])):
            ET.SubElement(component, key).text = value
        ET.SubElement(component, 'libsource', lib='Owned', part=name)
        ET.SubElement(component, 'sheetpath', names='/', tstamps='/')
        unitpins = ET.SubElement(ET.SubElement(ET.SubElement(component, 'units'), 'unit', name='A'), 'pins')
        libpart = ET.Element('libpart', lib='Owned', part=name)
        libpins = ET.SubElement(libpart, 'pins')
        for pin in range(1, count + 1):
            electrical = 'no_connect' if ref == 'U1' and pin == nc else 'passive'
            ET.SubElement(unitpins, 'pin', num=str(pin))
            ET.SubElement(libpins, 'pin', num=str(pin), name='P' + str(pin), type=electrical)
            node = ET.Element('node', ref=ref, pin=str(pin), pintype=electrical)
            if pin == nc:
                node.set('pintype', electrical + '+no_connect')
                net = ET.Element('net', code=ref, name=f'unconnected-({ref}-P{pin}-Pad{pin})')
                net.append(node); opens.append(net)
            else:
                common.append(node)
        components.append(component); libparts.append(libpart)
    sheet = ('(kicad_sch (version 20260306) (generator "owned") '
             f'(uuid "{ROOT_UUID}") (lib_symbols {"".join(libraries)}) {"".join(symbols)})').encode()
    export = ET.Element('export', version='E')
    design = ET.SubElement(export, 'design')
    ET.SubElement(design, 'source').text = '/candidate/source.kicad_sch'
    ET.SubElement(design, 'sheet', name='/', tstamps='/')
    ET.SubElement(export, 'components').extend(components)
    ET.SubElement(export, 'libparts').extend(libparts)
    nets = ET.SubElement(export, 'nets')
    ET.SubElement(nets, 'net', code='1', name='/COMMON').extend(common)
    nets.extend(opens)
    return sheet, ET.tostring(export)


class NoConnectCompositionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-cpu-nc-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.sheet, self.xml = source_fixture()
        repo = self.root / 'source'; repo.mkdir(); self.repo = repo
        (repo / 'source.kicad_sch').write_bytes(self.sheet)
        parts = {'schema': 'agent-ee.block-parts/1', 'source': 'owned', 'catalog': None,
            'parts': [dict(instance='/', reference=ref, lib_id='Owned:Package' + str(count),
                value='Package' + str(count), footprint='Owned:Package', lcsc=None,
                mpn=None, manufacturer=None, populated=True, catalog=None)
                for ref, count in (('U1', 5), ('U2', 10))]}
        write_json(repo / 'parts.json', parts)
        for args in (('init', '-q'), ('add', '.'),
                     ('-c', 'user.name=CPU fixture', '-c', 'user.email=fixture@invalid', 'commit', '-qm', 'Owned source')):
            subprocess.run(['git', *args], cwd=repo, check=True, capture_output=True)
        commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()
        request = dict(schema='agent-ee.block/1', id='owned-nc', version='1', role='fixture',
            source=dict(repository='', commit='', paths={'source.kicad_sch': 'source.kicad_sch',
                'parts.json': 'parts.json'}, license='Owned synthetic test data',
                upstream_origin='Authored synthetic fixture', upstream_license='Owned synthetic test data'),
            sheet='source.kicad_sch', parts_manifest='parts.json', boundary_ports=[],
            requirements=[dict(id='topology', statement='Preserve all physical pins and explicit no-connects',
                               checks=['interface'])], files={}, runs=[],
            instances=[dict(path='/', uuid_path='/', sheet='source.kicad_sch', references=['U1', 'U2'])])
        self.block_request = request
        self.assertEqual(capture_block(request, repo, commit, self.root / 'block')['status'], 'unverified')
        self.plan = dict(schema='agent-ee.block-composition/1',
            sources=[dict(sheet='source.kicad_sch', uuid_path='/',
                sha256=hashlib.sha256(self.sheet).hexdigest(), references={'U1': 'U1', 'U2': 'U2'},
                nets={'/COMMON': 'SIGNAL'}, reason='Owned connected terminals')],
            ports=[dict(name='SIGNAL', net_name='SIGNAL', direction='passive', voltage_domain='fixture',
                        current_a=None, alias_of=None, reason='Synthetic signal')], reason='NC regression')
        write_json(self.root / 'composition.json', self.plan)
        self.intent = dict(schema='agent-ee.connectivity-intent/1', libraries=[], components=[], nets=[], no_connects=[])
        self.bindings = {'SIGNAL': 'SIGNAL'}
        self.request = dict(id='pair', block='block', composition='composition.json',
            reference_ranges={'U': [1, 2]}, sheet='project/flat.kicad_sch', requirements={'topology': ['interface']})

    def revise_source(self, sheet, name):
        (self.repo / 'source.kicad_sch').write_bytes(sheet)
        subprocess.run(['git', 'add', '.'], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(['git', '-c', 'user.name=CPU fixture', '-c', 'user.email=fixture@invalid',
                        'commit', '-qm', name], cwd=self.repo, check=True, capture_output=True)
        commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=self.repo, text=True).strip()
        capture_block(self.block_request, self.repo, commit, self.root / name)
        self.request['block'] = name
        self.plan['sources'][0]['sha256'] = hashlib.sha256(sheet).hexdigest()
        write_json(self.root / 'composition.json', self.plan)

    def compose(self):
        # The only stub is the native process boundary. Source capture, XML
        # reading, source selection, library derivation and resolution are real.
        def export(intent, *, inputs, work, **kwargs):
            (work / 'design.xml').write_bytes(self.xml)
            return {'execution': 'ok', 'exit_code': 0}
        with patch('agent_ee.native_run.run', side_effect=export):
            return compose_flat(self.root, self.intent, [self.request], {'pair': self.bindings},
                                captured_libraries={})

    def test_explicit_nc_pins_survive_composition_and_schematic_emission(self):
        result = self.compose()
        self.assertEqual(result['intent']['no_connects'], [
            {'component': 'pair/U1', 'pin': {'number': '4'}},
            {'component': 'pair/U2', 'pin': {'number': '2'}}])
        self.assertEqual({c['reference'] for c in result['intent']['components']}, {'U1', 'U2'})
        resolved = resolve_intent(result['intent'], result['captures'])
        self.assertEqual((resolved['outcome'], resolved['coverage']), ('pass', 'complete'))
        self.assertEqual(sum(len(n['terminals']) for n in resolved['nets']), 13)
        report, emitted = emit_schematic(result['intent'], result['captures'],
            dict(schema='agent-ee.schematic-layout/1', namespace=ROOT_UUID, project='flat', transforms={}))
        self.assertEqual(report['execution'], 'ok', report)
        tree = parse_quoted(emitted['flat.kicad_sch'])
        geometry, problems = sheet_items(tree)
        self.assertFalse(problems)
        isolated = set()
        for terminal in (i for i in geometry if i['kind'] == 'pin'):
            status, _ = marker_isolation('flat', geometry, problems, terminal, terminal['number'])
            if status == 'disconnected':
                ref = next(p[2] for p in children(terminal['node'], 'property') if p[1] == 'Reference')
                isolated.add((ref, terminal['number']))
        self.assertEqual(isolated, {('U1', '4'), ('U2', '2')})

    def test_source_nc_requires_isolated_marker_and_matching_export(self):
        tree = parse_quoted(self.sheet)
        markers = children(tree, 'no_connect')
        variants = []
        for marker in markers:
            missing = copy.deepcopy(tree)
            missing.remove(marker)
            variants.append(missing)
        touched = copy.deepcopy(tree)
        touched.append(['label', 'driven', ['at', '80', '44.92', '0'],
                        ['uuid', 'c18e06c2-2dd4-4a10-9b43-0e9654a0578b']])
        variants.append(touched)
        for index, variant in enumerate(variants):
            with self.subTest(variant=index):
                self.revise_source(('\n'.join(format_sexpr(variant)) + '\n').encode(), 'variant-' + str(index))
                with self.assertRaisesRegex(ValueError, 'no-connect|explicit native net mapping'):
                    self.compose()
        self.assertFalse((self.root / 'project').exists())

    def test_connected_or_missing_native_nc_pin_cannot_be_composed(self):
        original = self.xml
        for mutation in ('connected', 'missing', 'no_membership', 'no_marker_witness', 'ordinary_singleton'):
            export = ET.fromstring(original)
            if mutation == 'connected':
                nets = export.find('nets')
                nc = nets.find("net[@code='U2']")
                nets.remove(nc)
                nets.find("net[@code='1']").append(nc.find('node'))
            elif mutation == 'missing':
                pins = export.find("./components/comp[@ref='U2']/units/unit/pins")
                pins.remove(pins.find("pin[@num='2']"))
            elif mutation == 'no_membership':
                nets = export.find('nets')
                nets.remove(nets.find("net[@code='U2']"))
            elif mutation == 'no_marker_witness':
                export.find("./nets/net[@code='U2']/node").set('pintype', 'passive')
            else:
                export.find("./nets/net[@code='U2']").set('name', 'ordinary-singleton')
            self.xml = ET.tostring(export)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.compose()
        self.assertEqual(self.intent['components'], [])
        self.assertEqual(self.intent['nets'], [])

    def test_nc_cannot_be_exposed_as_port_or_given_current_rating(self):
        block = read_json(self.root / 'block/block.json')
        invalid_current = copy.deepcopy(self.plan)
        invalid_current['ports'][0]['current_a'] = 0
        with self.assertRaisesRegex(ValueError, 'does not establish port current limits'):
            validate(invalid_current, block)
        self.plan['sources'][0]['nets']['unconnected-(U2-P2-Pad2)'] = 'FALSE_PORT'
        self.plan['ports'].append(dict(self.plan['ports'][0], name='FALSE_PORT', net_name='FALSE_PORT'))
        self.bindings['FALSE_PORT'] = 'FALSE_PORT'
        write_json(self.root / 'composition.json', self.plan)
        with self.assertRaisesRegex(ValueError, 'unused source net mappings'):
            self.compose()

    def hierarchical_fixture(self):
        path = self.root / 'synthetic.xml'; path.write_bytes(self.xml)
        graph = read_native_inventory(path)
        plan = copy.deepcopy(self.plan)
        references = {'U1': 'U11', 'U2': 'U12'}
        plan['sources'][0]['references'] = references
        selected = models(self.root / 'block', plan, lambda i, sheet: graph)
        child_path = '/cd825fc6-1bc7-416a-afd6-96369c047344/'
        occurrence = dict(path=child_path, instance_path='/' + ROOT_UUID + child_path.rstrip('/'), file='child.kicad_sch',
                          sha256=hashlib.sha256(self.sheet).hexdigest(), tree=parse_quoted(self.sheet))
        emitted = emit(selected, plan, occurrence, 'child')
        occurrence.update(tree=parse_quoted(emitted), sha256=hashlib.sha256(emitted).hexdigest())
        # Authored CPU substitution: references/sheet paths/UUIDs match the child.
        # This rebinds fixture XML; no exporter produced or verified these bytes.
        xml = ET.fromstring(self.xml)
        ET.SubElement(xml.find('design'), 'sheet', name='/child/', tstamps=child_path)
        symbols = {next(p[2] for p in children(n, 'property') if p[1] == 'Reference'): n
                   for n in children(occurrence['tree'], 'symbol')}
        for comp in xml.findall('./components/comp'):
            ref = references[comp.get('ref')]
            comp.set('ref', ref)
            comp.find('tstamps').text = field(symbols[ref], 'uuid', 1)[0]
            comp.find('sheetpath').set('tstamps', child_path)
            comp.find('sheetpath').set('names', '/child/')
        for net in xml.findall('./nets/net'):
            if net.get('code') == '1':
                net.set('name', '/child/SIGNAL')
            else:
                ref = references[net.get('code')]
                pin = net.find('node').get('pin')
                net.set('name', f'unconnected-({ref}-P{pin}-Pad{pin})')
            for node in net.findall('node'):
                node.set('ref', references[node.get('ref')])
        return selected, occurrence, xml

    def test_hierarchical_emitted_child_and_rebound_graph_preserve_nc_disposition(self):
        selected, occurrence, xml = self.hierarchical_fixture()
        path = self.root / 'authored-child.xml'; path.write_bytes(ET.tostring(xml))
        graph = read_native_inventory(path)
        self.assertEqual(set(graph['components']), {'U11', 'U12'})
        self.assertEqual(len(children(occurrence['tree'], 'no_connect')), 2)
        self.assertEqual(compare(selected, graph, occurrence, {'SIGNAL': '/child/SIGNAL'}), ([], []))
        for mutation in ('removed_marker', 'connected_with_marker'):
            delivered = copy.deepcopy(occurrence)
            changed = copy.deepcopy(xml)
            marker = children(delivered['tree'], 'no_connect')[1]
            if mutation == 'removed_marker':
                delivered['tree'].remove(marker)
            else:
                at = copy.deepcopy(children(marker, 'at')[0]); at.append('0')
                delivered['tree'].append(['label', Quoted('SIGNAL'), at,
                    ['uuid', Quoted('a5681c13-732b-4b35-bfca-0c2fc77894f9')]])
                nets = changed.find('nets'); nc = nets.find("net[@code='U2']")
                nets.remove(nc)
                nets.find("net[@code='1']").append(nc.find('node'))
                # An unrelated provenance defect must survive the NC finding.
                changed.find("./components/comp[@ref='U11']/value").text = 'changed'
            path.write_bytes(ET.tostring(changed))
            with self.subTest(mutation=mutation):
                components, changes = compare(selected, read_native_inventory(path), delivered,
                                              {'SIGNAL': '/child/SIGNAL'})
                self.assertEqual([(c['terminal'], c['severity']) for c in changes], [(['U12', '2'], 'defect')])
                self.assertEqual([c['reference'] for c in components], ['U11'] if mutation == 'connected_with_marker' else [])

    def test_source_without_no_connects_retains_all_connected_pins(self):
        tree = parse_quoted(self.sheet.replace(b'(pin no_connect line', b'(pin passive line'))
        for marker in children(tree, 'no_connect'):
            tree.remove(marker)
        xml = ET.fromstring(self.xml)
        xml.find("./libparts/libpart[@part='Package5']/pins/pin[@num='4']").set('type', 'passive')
        nets = xml.find('nets')
        for code in ('U1', 'U2'):
            net = nets.find(f"net[@code='{code}']"); node = net.find('node')
            node.set('pintype', 'passive')
            nets.remove(net); nets.find("net[@code='1']").append(node)
        self.xml = ET.tostring(xml)
        self.revise_source(('\n'.join(format_sexpr(tree)) + '\n').encode(), 'all-connected')
        result = self.compose()
        self.assertEqual(result['intent']['no_connects'], [])
        self.assertEqual(sum(len(n['terminals']) for n in result['intent']['nets']), 15)


if __name__ == '__main__':
    unittest.main()
