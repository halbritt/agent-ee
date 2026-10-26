import tempfile
import unittest
from pathlib import Path

from agent_ee.storage import write_json
from agent_ee.packet_index import build_index, page


XML = '''<export version="E"><components><comp ref="R1"><value>1k</value>
<libsource lib="Device" part="R"/><sheetpath names="/" tstamps="/sheet/"/>
<tstamps>r-uuid</tstamps></comp></components><libparts><libpart lib="Device" part="R">
<pins><pin num="1" name="~"/><pin num="2" name="~"/></pins></libpart></libparts>
<nets><net code="1" name="IN"><node ref="R1" pin="1"/></net></nets></export>'''


class PacketIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run = Path(self.tmp.name)
        self.manifest = {'candidate_id': 'candidate-a', 'files': {}, 'scope': {'checks': [
            {'id': 'native', 'kind': 'netlist'},
            {'id': 'pins', 'kind': 'pin_map', 'params': {'netlist_check': 'native',
                'symbols': [{'reference': 'R1', 'facts': ['pin1']}]}}]}}
        write_json(self.run / 'candidate.json', self.manifest)
        self.xml = self.run / 'work/native/design.xml'
        self.xml.parent.mkdir(parents=True)
        self.xml.write_text(XML)
        self.checks = [{'id': 'native', 'execution': 'ok', 'outcome': 'pass',
            'evidence': ['work/native/design.xml'], 'details': {'sheets_checked': 1}},
            {'id': 'pins', 'execution': 'ok', 'outcome': 'defect', 'evidence': []}]

    def test_native_inventory_and_stable_structural_identity(self):
        first = build_index(self.run, self.manifest, None, self.checks, 'run-a')
        second = build_index(self.run, self.manifest, None, self.checks, 'run-b')
        self.assertEqual(first['index'], second['index'])
        self.assertNotEqual(first['binding'], second['binding'])
        entities = [e for e in first['index']['entries'] if e['type'] == 'entity']
        self.assertEqual(sorted(e['kind'] for e in entities), ['component', 'net', 'pin', 'pin'])
        resistor = next(e for e in entities if e['kind'] == 'component')
        self.assertEqual(resistor['checks'], ['native', 'pins'])
        self.assertIn('r-uuid', str(resistor['native_identity']))
        self.assertTrue(all(e['source']['semantic_sha256'] for e in entities))
        self.assertTrue(any(e.get('connection') == 'unconnected' for e in entities))
        ownership = [e for e in first['index']['entries'] if e.get('relation') == 'contains_pin']
        self.assertEqual(len(ownership), 2)
        view = page(first['index'], byte_budget=1200)
        self.assertLessEqual(len(__import__('json').dumps(view).encode()), 1200)
        recovered = []
        while True:
            recovered.extend(view['entries'])
            if 'oversized_entry' in view:
                pointer = view['oversized_entry']['index_pointer']
                recovered.append(first['index']['entries'][int(pointer.rsplit('/', 1)[1])])
            if view['continuation'] is None:
                break
            view = page(first['index'], byte_budget=1200, continuation=view['continuation'])
        self.assertEqual(recovered, first['index']['entries'])
        self.xml.write_text(XML.replace('IN', 'RENAMED'))
        changed = build_index(self.run, self.manifest, None, self.checks, 'run-c')
        self.assertNotEqual(first['index']['index_id'], changed['index']['index_id'])
        with self.assertRaisesRegex(ValueError, 'stale'):
            page(changed['index'], continuation=page(first['index'], byte_budget=1200)['continuation'])

    def test_partial_sources_ambiguity_and_limits_do_not_erase_access(self):
        self.xml.write_text(XML.replace('</components>', XML.split('<components>')[1].split('</components>')[0] + '</components>'))
        result = build_index(self.run, self.manifest, None, self.checks, 'a')
        self.assertGreater(result['index']['coverage']['unresolved_entities'], 0)
        self.assertTrue(any('unresolved native node' in d['reason'] for d in result['index']['coverage']['diagnostics']))
        self.assertFalse(any('pins' in e['checks'] for e in result['index']['entries'] if e['type'] == 'entity'))
        self.xml.write_text('<broken')
        broken = build_index(self.run, self.manifest, None, self.checks, 'a')
        self.assertEqual(broken['index']['coverage']['status'], 'unavailable')
        self.assertTrue(self.xml.exists())
        self.assertEqual(len([e for e in broken['index']['entries'] if e['type'] == 'check']), 2)
        self.xml.write_text(XML)
        from unittest.mock import patch
        with patch('agent_ee.packet_index.MAX_ENTITIES', 1):
            bounded = build_index(self.run, self.manifest, None, self.checks, 'a')
        self.assertIn('entity limit', str(bounded['index']['coverage']['diagnostics']))
        with patch('agent_ee.packet_index.MAX_SOURCE_BYTES', 2):
            limited = build_index(self.run, self.manifest, None, self.checks, 'a')
        self.assertIn('byte limit', str(limited['index']['coverage']['diagnostics']))
        self.checks[0]['execution'] = 'failed'
        failed = build_index(self.run, self.manifest, None, self.checks, 'a')
        self.assertIn('native inventory unavailable', str(failed['index']['coverage']['diagnostics']))

    def test_common_index_excludes_peer_judgments_and_preserves_coupled_identity(self):
        from agent_ee.harnesses import evidence_mounts
        for rel in ('reviews/circuit.json', 'review-work/physical/experiments/opinion.json',
                    'work/native/extra-opinion.json', 'campaign-history.json', 'preflight/receipt.json'):
            write_json(self.run / rel, {'marker': 'PEER-SECRET'})
        self.manifest['scope']['checks'] += [
            {'id': 'response', 'kind': 'rc_transient', 'params': {'netlist_check': 'native', 'input_net': 'IN'}},
            {'id': 'thermal', 'kind': 'measurements'},
            {'id': 'coupled', 'kind': 'coupled_margins', 'params': {'condition': 'load=2A',
                'members': [{'check': 'response', 'metric': 'v', 'name': 'signal'},
                            {'check': 'thermal', 'metric': 't', 'name': 'temperature'}],
                'limits': {'signal': {'min': 0.5}, 'temperature': {'max': 80}}}}]
        self.checks += [
            {'id': 'response', 'execution': 'ok', 'outcome': 'pass', 'details': {
                'elements': {'R1': {'parsed': 1000}}, 'metrics': {'v': {'value': .7, 'unit': 'V', 'condition': 'load=2A'}},
                'producer': {'domain': 'electrical'}}},
            {'id': 'thermal', 'execution': 'ok', 'outcome': 'defect', 'details': {
                'metrics': {'t': {'value': 90, 'unit': 'degC', 'condition': 'load=2A'}},
                'producer': {'domain': 'thermal'}}}]
        result = build_index(self.run, self.manifest, None, self.checks, 'a')['index']
        self.assertNotIn('PEER-SECRET', str(result))
        couplings = [e for e in result['entries'] if e['type'] == 'coupling']
        self.assertEqual(len(couplings), 2)
        signal = next(e for e in couplings if e['member']['name'] == 'signal')
        thermal = next(e for e in couplings if e['member']['name'] == 'temperature')
        self.assertEqual(signal['condition'], 'load=2A')
        self.assertEqual(signal['metric']['unit'], 'V')
        self.assertEqual(thermal['entity_scope'], 'unresolved')
        self.assertEqual(thermal['metric']['unit'], 'degC')
        linked = [e for e in result['entries'] if e['type'] == 'entity' and e['id'] in signal['entities']]
        self.assertTrue(any(e['kind'] == 'component' for e in linked))
        self.assertTrue(any(e['kind'] == 'net' for e in linked))
        self.assertTrue(all(e['couplings'] == ['coupled'] for e in linked))
        for role in ('circuit', 'physical'):
            self.assertFalse(any('/experiments/' in target for _, target in evidence_mounts(self.run, role)))
        self.assertTrue(any('/experiments/' in target for _, target in evidence_mounts(self.run, 'adjudicator')))

    def test_run_receipt_hashes_are_volatile_structural_facts_are_not(self):
        first = build_index(self.run, self.manifest, None, self.checks, 'a')
        self.xml.write_text(XML.replace('<export version="E">', '<export version="E"><design><date>tomorrow</date></design>'))
        second = build_index(self.run, self.manifest, None, self.checks, 'b')
        self.assertEqual(first['index'], second['index'])
        self.assertNotEqual(first['binding']['sources'], second['binding']['sources'])
        from agent_ee.packet_index import retain, verify_navigation
        retain(self.run, self.manifest, None, self.checks, 'b')
        verify_navigation(self.run, self.manifest, None, self.checks, 'b')
        write_json(self.run / 'work/.navigation/binding.json', {'candidate_id': 'fake'})
        with self.assertRaisesRegex(ValueError, 'binding changed'):
            verify_navigation(self.run, self.manifest, None, self.checks, 'b')

    def test_unsafe_sources_fail_instead_of_becoming_optional_navigation_failure(self):
        self.xml.unlink()
        self.xml.symlink_to('/etc/hostname')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            build_index(self.run, self.manifest, None, self.checks, 'a')

    def test_oversized_entry_has_explicit_direct_pointer_and_forward_progress(self):
        self.manifest['scope']['checks'][0]['params'] = {'text': 'é' * 4000}
        index = build_index(self.run, self.manifest, None, self.checks, 'a')['index']
        view = page(index, byte_budget=1024)
        self.assertEqual(view['entries'], [])
        self.assertEqual(view['oversized_entry']['index_pointer'], '/entries/0')
        self.assertEqual(view['continuation']['offset'], 1)
        from agent_ee.storage import canonical
        self.assertLessEqual(len(canonical(view)), 1024)
        with self.assertRaisesRegex(ValueError, 'at least'):
            page(index, byte_budget=4)

    def test_geometry_arc_unknown_class_and_native_net_locators(self):
        self.manifest['scope']['checks'].append({'id': 'board', 'kind': 'geometry'})
        geometry = {'pcbnew_version': 'test-envelope', 'board': '/candidate/design.kicad_pcb',
            'footprints': [], 'pads': [], 'zones': [], 'nets': {'/OUT': 0},
            'tracks': [{'uuid': 'arc-uuid', 'native_class': 'PCB_ARC', 'layer': 'F.Cu', 'net': '/OUT'},
                       {'uuid': 'unknown-uuid', 'native_class': 'PCB_FUTURE', 'layer': 'B.Cu', 'net': '/OUT'}]}
        write_json(self.run / 'work/board/geometry.json', geometry)
        self.checks.append({'id': 'board', 'execution': 'ok', 'outcome': 'defect',
                            'evidence': ['work/board/geometry.json']})
        result = build_index(self.run, self.manifest, None, self.checks, 'a')['index']
        arcs = [e for e in result['entries'] if e.get('native_class') == 'PCB_ARC']
        self.assertEqual(len(arcs), 1)
        self.assertEqual(arcs[0]['source']['locator'], '/tracks/0')
        self.assertTrue(any(e['type'] == 'connection' and e['from'] == arcs[0]['id'] for e in result['entries']))
        self.assertIn('unsupported primitive class', str(result['coverage']['diagnostics']))
        geometry['pcbnew_version'] = 'another-runtime'
        geometry['board'] = '/different/mount/path'
        write_json(self.run / 'work/board/geometry.json', geometry)
        self.assertEqual(result, build_index(self.run, self.manifest, None, self.checks, 'b')['index'])

    def test_pin_in_multiple_nets_has_no_qualified_links(self):
        self.xml.write_text(XML.replace('</nets>',
            '<net code="2" name="OUT"><node ref="R1" pin="1"/></net></nets>'))
        self.checks[1]['details'] = {'comparisons': [
            {'reference': 'R1', 'pin': '1', 'fact_id': 'pin1', 'matches': False}]}
        baseline = {'requirements': [{'id': 'pin-req', 'checks': ['pins', 'native']}]}
        index = build_index(self.run, self.manifest, baseline, self.checks, 'a')['index']
        pin = next(e for e in index['entries'] if e.get('kind') == 'pin' and e['pin'] == '1')
        self.assertEqual(pin['identity_status'], 'unresolved')
        self.assertEqual(pin['connection'], 'unresolved')
        self.assertEqual(pin['checks'], ['native'])
        self.assertEqual(pin['requirements'], [])
        self.assertFalse(any(e.get('relation') == 'net_membership' and e['from'] == pin['id']
                             for e in index['entries']))
        self.assertIn('ambiguous native pin membership', str(index['coverage']['diagnostics']))
        self.assertEqual(self.xml.read_text().count('<node'), 2)

    def test_ambiguous_native_inventory_retains_raw_access_without_consumer_links(self):
        import copy
        import xml.etree.ElementTree as ET
        self.checks[1]['details'] = {'comparisons': [
            {'reference': 'R1', 'pin': '1', 'fact_id': 'pin1', 'matches': True}]}
        baseline = {'requirements': [{'id': 'req', 'checks': ['pins', 'native']}]}
        self.manifest['scope']['checks'].append({'id': 'coupled', 'kind': 'coupled_margins',
            'params': {'members': [{'name': 'inventory', 'check': 'native', 'metric': 'count'}]}})
        for case in ('scoped_uuid', 'net_code', 'pin_number', 'same_net_node', 'reference', 'sheet'):
            with self.subTest(case=case):
                root = ET.fromstring(XML)
                design = ET.SubElement(root, 'design')
                for path in ('/', '/sheet/', '/other/'):
                    ET.SubElement(design, 'sheet', {'tstamps': path})
                if case == 'scoped_uuid':
                    comp = copy.deepcopy(root.find('./components/comp'))
                    comp.set('ref', 'R2')
                    root.find('components').append(comp)
                elif case == 'net_code':
                    net = ET.SubElement(root.find('nets'), 'net', {'code': '1', 'name': 'OTHER'})
                    ET.SubElement(net, 'node', {'ref': 'R1', 'pin': '2'})
                elif case == 'pin_number':
                    del root.find('./libparts/libpart/pins/pin').attrib['num']
                    del root.find('./nets/net/node').attrib['pin']
                elif case == 'same_net_node':
                    root.find('./nets/net').append(copy.deepcopy(root.find('./nets/net/node')))
                elif case == 'reference':
                    del root.find('./components/comp').attrib['ref']
                    del root.find('./nets/net/node').attrib['ref']
                else:
                    root.find('./components/comp/sheetpath').set('tstamps', '/absent/')
                raw = ET.tostring(root)
                self.xml.write_bytes(raw)
                built = build_index(self.run, self.manifest, baseline, self.checks, 'a')
                index = built['index']
                unresolved = [e for e in index['entries'] if e.get('identity_status') == 'unresolved']
                self.assertTrue(unresolved)
                self.assertTrue(index['coverage']['diagnostics'])
                self.assertEqual(index['coverage']['unresolved_entities'], len(unresolved))
                unresolved_ids = {e['id'] for e in unresolved}
                self.assertFalse(any(unresolved_ids.intersection(e['entities']) for e in index['entries']
                                     if e['type'] == 'coupling'))
                self.assertEqual(index['coverage']['status'], 'partial')
                self.assertEqual(built['binding']['access']['evidence'], '/evidence/<path>')
                self.assertEqual(self.xml.read_bytes(), raw)
                for entity in unresolved:
                    self.assertEqual(entity['checks'], ['native'])
                    self.assertEqual(entity['requirements'], [])
                    self.assertEqual(entity['couplings'], [])
                    self.assertTrue(all(m['relation'] == 'native_inventory' for m in entity['mappings']))
                self.assertFalse(any(e.get('relation') == 'net_membership'
                                     for e in index['entries']))
                if case in ('net_code', 'same_net_node'):
                    self.assertTrue(any(e.get('kind') == 'component' and e['identity_status'] == 'native'
                                        for e in index['entries']))

    def test_symbol_uuid_reuse_is_valid_only_across_declared_sheet_instances(self):
        import copy
        import xml.etree.ElementTree as ET
        root = ET.fromstring(XML)
        design = ET.SubElement(root, 'design')
        for path in ('/sheet/', '/other/'):
            ET.SubElement(design, 'sheet', {'tstamps': path})
        comp = copy.deepcopy(root.find('./components/comp'))
        comp.set('ref', 'R2')
        comp.find('sheetpath').set('tstamps', '/other/')
        root.find('components').append(comp)
        self.xml.write_bytes(ET.tostring(root))
        index = build_index(self.run, self.manifest, None, self.checks, 'a')['index']
        components = [e for e in index['entries'] if e.get('kind') == 'component']
        self.assertEqual(len(components), 2)
        self.assertTrue(all(e['identity_status'] == 'native' for e in components))
        self.assertEqual(index['coverage']['diagnostics'], [])
        self.assertNotEqual(components[0]['id'], components[1]['id'])
        # Sheet declarations are structural facts; volatile design timestamps are not.
        old_id = index['index_id']
        design.remove(design.find("sheet[@tstamps='/other/']"))
        self.xml.write_bytes(ET.tostring(root))
        changed = build_index(self.run, self.manifest, None, self.checks, 'a')['index']
        self.assertNotEqual(old_id, changed['index_id'])
        self.assertTrue(any(e.get('label') == 'R2' and e['identity_status'] == 'unresolved'
                            for e in changed['entries']))

    def test_pin_consumer_links_only_compared_pins(self):
        self.checks[1]['details'] = {'comparisons': [{'reference': 'R1', 'pin': '1',
            'fact_id': 'pin1', 'matches': False}]}
        index = build_index(self.run, self.manifest, None, self.checks, 'a')['index']
        pins = {e['pin']: e for e in index['entries'] if e.get('kind') == 'pin'}
        self.assertIn('pins', pins['1']['checks'])
        self.assertNotIn('pins', pins['2']['checks'])
        self.assertEqual(pins['1']['mappings'][-1]['source']['locator'], '/details/comparisons/0')

    def test_malformed_geometry_envelope_is_explicitly_unavailable(self):
        self.manifest['scope']['checks'].append({'id': 'board', 'kind': 'geometry'})
        write_json(self.run / 'work/board/geometry.json', [])
        self.checks.append({'id': 'board', 'execution': 'ok', 'outcome': 'pass',
                            'evidence': ['work/board/geometry.json']})
        result = build_index(self.run, self.manifest, None, self.checks, 'a')['index']
        self.assertIn('navigation extraction unavailable', str(result['coverage']['diagnostics']))


class NativePacketIndexTests(unittest.TestCase):
    def test_native_seeded_packet_source_refresh_and_public_tamper_verification(self):
        import contextlib
        import io
        import shutil
        import xml.etree.ElementTree as ET
        from agent_ee.run_record import run_review, verify, save_result
        from agent_ee.storage import read_json, digest
        from agent_ee.reviewers import build_packet
        from toolchain import require_toolchain
        require_toolchain(native=True)
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / 'project'
            shutil.copytree(repo / 'fixtures/rc/seeded', project)
            template = repo / 'fixtures/rc/seeded.scope.json'
            scope = read_json(template)
            for selection in scope['inputs']:
                selection['path'] = str((template.parent / selection['path']).resolve())
            scope['inputs'][0]['path'] = str(project)
            scope['packet_max_bytes'] = 1024
            write_json(root / 'scope.json', scope)
            run = root / 'run'
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(run_review(root / 'scope.json', run, 180, checks_only=True), 1)
            result = verify(run)
            self.assertNotIn('error', result)
            self.assertTrue(all(c['execution'] == 'ok' for c in result['checks']))
            self.assertEqual(next(c for c in result['checks'] if c['id'] == 'rc-step')['outcome'], 'defect')
            packet = build_packet(run, result['checks'], result['run_id'])
            save_result(run, result)
            self.assertEqual(verify(run)['outcome'], 'incomplete')
            index_path = run / 'work/.navigation/index.json'
            original = read_json(index_path)
            entities = [e for e in original['entries'] if e['type'] == 'entity']
            resistor = next(e for e in entities if e['kind'] == 'component' and e['label'] == 'R1')
            native = ET.parse(run / resistor['source']['path'])
            self.assertEqual(native.find(resistor['source']['locator']).get('ref'), 'R1')
            self.assertIn('rc-step', resistor['checks'])
            self.assertTrue(any(e['kind'] == 'primitive' and e['identity_status'] == 'native' for e in entities))
            self.assertEqual(sum(a['bytes_included'] for a in packet['artifacts']), 1024)
            self.assertIn('rc-step', str(packet['criteria']))
            self.assertIn('navigation', packet)
            self.assertEqual(result['artifact_hashes']['work/.navigation/index.json'], digest(index_path))
            original_bytes = index_path.read_bytes()
            altered = dict(original, candidate_id='wrong-candidate')
            write_json(index_path, altered)
            with self.assertRaisesRegex(ValueError, 'evidence changed'):
                verify(run)
            # Even updating the outer inventory cannot bless an inconsistent structural binding.
            save_result(run, result)
            with self.assertRaisesRegex(ValueError, 'navigation identity/binding changed'):
                verify(run)
            index_path.write_bytes(original_bytes)
            save_result(run, result)
            self.assertEqual(verify(run)['outcome'], 'incomplete')
            # Corrupt a retained native inventory, then refresh only its outer hashes.
            # Public verify must reconstruct ambiguity coverage instead of trusting the old index.
            native_path = run / resistor['source']['path']
            native_bytes = native_path.read_bytes()
            corrupted = ET.fromstring(native_bytes)
            net = corrupted.find('./nets/net[node]')
            self.assertIsNotNone(net)
            import copy
            net.append(copy.deepcopy(net.find('node')))
            native_path.write_bytes(ET.tostring(corrupted))
            save_result(run, result)
            with self.assertRaisesRegex(ValueError, 'navigation identity/binding changed'):
                verify(run)
            from agent_ee.packet_index import build_index
            from agent_ee.baseline import load_baseline
            manifest = read_json(run / 'candidate.json')
            diagnosed = build_index(run, manifest, load_baseline(run), result['checks'], result['run_id'])['index']
            self.assertGreater(diagnosed['coverage']['unresolved_entities'], 0)
            self.assertIn('ambiguous native pin membership', str(diagnosed['coverage']['diagnostics']))
            ambiguous = [e for e in diagnosed['entries'] if e.get('identity_status') == 'unresolved']
            self.assertTrue(all(not e['requirements'] and len(e['checks']) == 1 for e in ambiguous))
            self.assertEqual(next(c for c in result['checks'] if c['id'] == 'rc-step')['outcome'], 'defect')
            native_path.write_bytes(native_bytes)
            save_result(run, result)
            self.assertEqual(verify(run)['outcome'], 'incomplete')
            packet_file = run / 'packet.json'
            packet_bytes = packet_file.read_bytes()
            corrupted_packet = read_json(packet_file)
            corrupted_packet['navigation']['index_id'] = 'wrong-index'
            write_json(packet_file, corrupted_packet)
            save_result(run, result)
            with self.assertRaisesRegex(ValueError, 'navigation display differs'):
                verify(run)
            packet_file.write_bytes(packet_bytes)
            binding_bytes = (run / 'work/.navigation/binding.json').read_bytes()
            shutil.rmtree(run / 'work/.navigation')
            save_result(run, result)
            with self.assertRaisesRegex(ValueError, 'navigation unavailable'):
                verify(run)
            index_path.parent.mkdir()
            index_path.write_bytes(original_bytes)
            (index_path.parent / 'binding.json').write_bytes(binding_bytes)
            save_result(run, result)
            self.assertEqual(verify(run)['outcome'], 'incomplete')
            for filename in ('rc_filter.kicad_sch', 'rc_filter.kicad_pcb'):
                path = project / filename
                path.write_text(path.read_text().replace('"R1"', '"R9"'))
            scope['checks'] = [c for c in scope['checks'] if c['kind'] == 'netlist']
            write_json(root / 'scope.json', scope)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(run_review(root / 'scope.json', root / 'changed', 180, checks_only=True), 1)
            changed = verify(root / 'changed')
            packet2 = build_packet(root / 'changed', changed['checks'], changed['run_id'])
            new_index = read_json(root / 'changed/work/.navigation/index.json')
            self.assertNotEqual(original['candidate_id'], new_index['candidate_id'])
            self.assertTrue(any(e.get('kind') == 'component' and e['label'] == 'R9' for e in new_index['entries']))
            self.assertNotEqual(packet['navigation']['index_id'], packet2['navigation']['index_id'])
