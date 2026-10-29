"""Flat captured CAN composition through ordinary emission and native provenance."""
import copy
import hashlib
from pathlib import Path
import re
import shutil
import tempfile
import time
import unittest

from agent_ee import block_adaptation
from agent_ee.executor import execute
from agent_ee.native_netlist import read_native_inventory
from agent_ee.schematic_emitter import emit_schematic, publish
from agent_ee.storage import read_json, write_json
from tests.toolchain import require_toolchain

ROOT = Path(__file__).resolve().parents[1]
CAN = ROOT / 'fixtures/fome-mc/pod-can'
BINDINGS = {'CAN+': 'CANH', 'CAN-': 'CANL', 'TXD': 'TX', 'RXD': 'RX',
            'S': 'GND', '+5V': '+5V', '+3V3': '+3V3', 'GND': 'GND'}


def prepare(root, *, topology=False):
    if topology:
        from tests.block_topology_fixture import derive_topology_fixture
        derive_topology_fixture(CAN/'block',root/'block')
    else:
        shutil.copytree(CAN/'block',root/'block')
    shutil.copyfile(CAN / 'composition.json', root / 'composition.json')
    # Real seven-pin board interface, accounting for all external CAN/power nets.
    data = Path('/usr/share/kicad/symbols/Connector_Generic.kicad_sym').read_bytes()
    libraries = [{'name': 'Connector_Generic', 'path': 'libraries/Connector_Generic.kicad_sym',
                  'sha256': hashlib.sha256(data).hexdigest()}]
    intent = {'schema': 'agent-ee.connectivity-intent/1', 'libraries': libraries,
        'components': [{'id': 'interface', 'reference': 'J1', 'lib_id': 'Connector_Generic:Conn_01x07',
            'value': 'CAN interface', 'footprint': 'Connector_PinHeader_2.54mm:PinHeader_1x07_P2.54mm_Vertical', 'units': [1]}],
        'nets': [{'id': 'board-' + str(i), 'name': name,
            'terminals': [{'component': 'interface', 'pin': {'number': str(i)}}]}
            for i, name in enumerate(['CANH', 'CANL', 'TX', 'RX', '+5V', '+3V3', 'GND'], 1)],
        'no_connects': []}
    source = read_json(root / 'block/block.json')
    requests = [{'id': 'can', 'block': 'block', 'composition': 'composition.json',
        'reference_ranges': {'C': [100, 109], 'D': [100, 109], 'R': [100, 109], 'U': [100, 109]},
        'sheet': 'project/flat.kicad_sch',
        'requirements': {r['id']: r['checks'] for r in source['requirements']}}]
    return intent, requests, {'can': dict(BINDINGS)}, {libraries[0]['path']: data}


class FlatComposeTests(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-flat-can-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.intent, self.requests, self.bindings, self.libraries = prepare(self.root,topology=True)

    def compose(self):
        return block_adaptation.compose_flat(self.root, self.intent, self.requests, self.bindings,
                                             captured_libraries=self.libraries)

    def emit(self, result):
        report, files = emit_schematic(result['intent'], result['captures'],
            {'schema': 'agent-ee.schematic-layout/1', 'namespace': '76c559cf-c0b2-412c-b59f-5f08dd59a919',
             'project': 'flat', 'transforms': {}})
        self.assertEqual(report['execution'], 'ok', report)
        publish(files, self.root / 'project')
        native = execute(self.root, self.root / 'native', ['kicad-cli', 'sch', 'export', 'netlist',
            '--format', 'kicadxml', '-o', '/work/design.xml', '/candidate/project/flat.kicad_sch'], timeout=120)
        self.assertEqual((native['execution'], native['exit_code']), ('ok', 0), native)
        return read_native_inventory(self.root / 'native/design.xml')

    def test_can_block_flat(self):
        before = copy.deepcopy(self.intent)
        result = self.compose()
        self.assertEqual(self.intent, before)
        graph = self.emit(result)
        self.assertEqual(set(graph['components']), {'J1', 'C100', 'D100', 'R100', 'U100'})
        expected = {'CANH': {('J1','1'),('U100','7'),('D100','2'),('R100','1')},
                    'CANL': {('J1','2'),('U100','6'),('D100','1'),('R100','2')},
                    'TX': {('J1','3'),('U100','1')}, 'RX': {('J1','4'),('U100','4')},
                    '+5V': {('J1','5'),('U100','3'),('C100','1')},
                    '+3V3': {('J1','6'),('U100','5')},
                    'GND': {('J1','7'),('U100','2'),('U100','8'),('C100','2'),('D100','3')}}
        self.assertEqual({n['name']: set(n['terminals']) for n in graph['nets'].values()},
                         {'/' + name: pins for name, pins in expected.items()})
        from agent_ee.connectivity_ir import resolve_intent
        from agent_ee.netlist_parity import compare, resolved_inventory
        parity = compare(resolved_inventory(resolve_intent(result['intent'], result['captures'])), graph,
                         {'predicate': 'topology', 'multiset': 'exactly',
                          'anchors': {c['id']: {'reference': c['reference']} for c in result['intent']['components']}})
        self.assertEqual((parity['execution'], parity['outcome']), ('ok', 'pass'), parity)
        self.assertEqual(graph['components']['R100']['properties']['LCSC'], 'C22787')
        self.assertEqual(result['block_instances'][0]['references'], {'C308':'C100','D4':'D100','R9':'R100','U307':'U100'})
        self.assertEqual(len(result['block_instances'][0]['source_parts']), 4)

    def assert_library_erc_matches(self, result):
        report, files = emit_schematic(result['intent'], result['captures'],
            {'schema': 'agent-ee.schematic-layout/1', 'namespace': '76c559cf-c0b2-412c-b59f-5f08dd59a919',
             'project': 'flat', 'transforms': {}},
            symbol_table={lib['name']: '${KIPRJMOD}/' + lib['path'] for lib in result['intent']['libraries']})
        self.assertEqual(report['execution'], 'ok', report)
        publish(files, self.root / 'project')
        for path, data in result['captures'].items():
            target = self.root / 'project' / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        native = execute(self.root, self.root / 'erc', ['kicad-cli', 'sch', 'erc',
            '--format', 'json', '--severity-all', '-o', '/work/erc.json',
            '/candidate/project/flat.kicad_sch'], timeout=120)
        self.assertEqual((native['execution'], native['exit_code']), ('ok', 0), native)
        violations = [v for sheet in read_json(self.root / 'erc/erc.json')['sheets']
                      for v in sheet['violations']]
        self.assertFalse([v for v in violations if v['type'] == 'lib_symbol_mismatch'], violations)

    def test_legacy_symbols_match_derived_libraries_in_native_erc(self):
        result = self.compose()
        self.assert_library_erc_matches(result)
        from agent_ee.symbol_library import children, parse_sexpr
        library = parse_sexpr(result['captures']['libraries/Block_63616e_446576696365.kicad_sym'])
        self.assertEqual(children(library, 'version')[0][1], '20241209')
        for definition in children(library, 'symbol'):
            if definition[1] in ('C', 'R_Small'):
                self.assertEqual(next(p[2] for p in children(definition, 'property') if p[1] == 'Datasheet'), '')

    def test_modern_literal_tilde_survives_composition_and_native_erc(self):
        # Synthetic version variant of the captured fixture: unlike its legacy
        # source, modern KiCad treats these same '~' property values literally.
        block = read_json(self.root / 'block/block.json')
        plan = read_json(self.root / 'composition.json')
        for source in plan['sources']:
            if source['sheet'] != 'sheets/psu.kicad_sch':
                continue  # One derived library deliberately mixes both formats.
            path = self.root / 'block' / source['sheet']
            path.write_bytes(re.sub(rb'\(version [0-9]+\)', b'(version 20260306)', path.read_bytes(), count=1))
            source['sha256'] = block['files'][source['sheet']] = hashlib.sha256(path.read_bytes()).hexdigest()
        write_json(self.root / 'block/block.json', block)
        write_json(self.root / 'composition.json', plan)
        result = self.compose()
        self.assert_library_erc_matches(result)
        from agent_ee.symbol_library import children, parse_sexpr
        library = parse_sexpr(result['captures']['libraries/Block_63616e_446576696365.kicad_sym'])
        self.assertEqual(children(library, 'version')[0][1], '20250318')
        for definition in children(library, 'symbol'):
            if definition[1] in ('C', 'R_Small'):
                self.assertEqual(next(p[2] for p in children(definition, 'property') if p[1] == 'Datasheet'),
                                 '~' if definition[1] == 'R_Small' else '')

    def prepare_checks(self, result):
        from agent_ee.block_adaptation import _requirement_intent, _source
        record = result['block_instances'][0]
        write_json(self.root / 'instance.json', record)
        contract = read_json(CAN / 'block/interface.json')
        components = {c['reference']: c for c in result['intent']['components']}
        for ref, component in contract['components'].items():
            delivered = record['references'][ref]
            component['locations'] = [{'reference': delivered, 'sheet': '/'}]
            component['symbols'] = [components[delivered]['lib_id']]
        aliases = {'/CAN+': '/CANH', '/CAN-': '/CANL', '/wbo1/CAN_TX': '/TX',
                   '/wbo1/CAN_RX': '/RX', '+3V3_WBO1': '/+3V3', '+5V': '/+5V', 'GND': '/GND'}
        source_ref = {'path': 'block/block.json', 'locator': 'requirements/pod-can-interface'}
        for invariant in contract['invariants']:
            invariant.update(source=source_ref, requirement='can-pod-can-interface')
            if 'names' in invariant:
                invariant['names'] = [aliases[n] for n in invariant['names']]
        write_json(self.root / 'interface.json', contract)
        source = _source(self.root, 'block', 'composition.json')
        scope = {'schema': 'agent-ee.scope/3', 'name': 'Flat CAN composition',
            'scope': 'Source provenance and fresh interface, no source qualification.',
            'schematic': 'project/flat.kicad_sch',
            'inputs': [{'path': p, 'at': p} for p in ('project', 'block', 'composition.json', 'instance.json', 'interface.json')],
            'mutable_inputs': ['project/flat.kicad_sch'], 'requirements': ['block/requirements.md'],
            'checks': [*source['check_set'], {'id': 'provenance', 'kind': 'block_provenance',
                'params': {'instance': 'instance.json', 'netlist_check': 'netlist'}}],
            'baseline': {'requirements': [], 'targets': ['block/requirements.md'],
                         'reference_architectures': ['block/block.json']},
            'limitations': ['Source is unverified; provenance does not approve adaptations.']}
        for req in source['requirements']:
            scope['baseline']['requirements'].append({'id': 'can-' + req['id'], 'statement': req['statement'],
                'source': {'path': 'block/block.json', 'locator': 'requirements/' + req['id']},
                'checks': req['checks'], 'intent': _requirement_intent(source, req)})
        scope['baseline']['requirements'].append({'id': 'can-provenance',
            'statement': 'Record source differences; preserve declared boundary bindings.',
            'source': {'path': 'instance.json', 'locator': 'bindings'}, 'checks': ['provenance'], 'intent': 'acceptance'})
        from tests.block_topology_fixture import assert_topology_acceptance
        assert_topology_acceptance(self.root,scope)
        write_json(self.root / 'scope.json', scope)

    def check(self, name='run'):
        from agent_ee.candidate import capture
        from agent_ee.checks import run_checks
        from agent_ee.baseline import freeze_evidence, verify_evidence
        run = self.root / name
        run.mkdir()
        capture(self.root / 'scope.json', run)
        results = run_checks(run, time.monotonic() + 180)
        freeze_evidence(run, results)
        verify_evidence(run, results, required=True)
        return {r['id']: r for r in results}

    def test_adaptation_reported(self):
        from agent_ee.schematic_stage import configuration
        from agent_ee.stage_receipts import run_step, adopt, validate_receipt
        result = self.compose()
        self.emit(result)
        next(c for c in result['intent']['components'] if c['reference'] == 'R100')['value'] = '60'
        write_json(self.root / 'intent.json', result['intent'])
        write_json(self.root / 'layout.json', {'schema': 'agent-ee.schematic-layout/1',
            'namespace': '76c559cf-c0b2-412c-b59f-5f08dd59a919', 'project': 'flat', 'transforms': {}})
        write_json(self.root / 'footprints.json', {})  # Emit step does not consume footprint bodies.
        for rel, data in result['captures'].items():
            path = self.root / rel; path.parent.mkdir(exist_ok=True); path.write_bytes(data)
        config = configuration(ROOT / 'src', schematic='project/flat.kicad_sch', board='project/flat.kicad_pcb',
            intent='intent.json', layout='layout.json', footprints='footprints.json', captures='.')
        with tempfile.TemporaryDirectory(prefix='agent-ee-flat-receipt-') as tmp:
            target = Path(tmp) / 'receipt'
            receipt = run_step(target, self.root, config['implementation_steps'][0],
                {'implementation': 1, 'source_candidate_id': 'flat-test', 'receipt_index': 1})
            self.assertEqual(receipt['status'], 'completed', (target / 'run/console.log').read_text())
            self.assertEqual(validate_receipt(target), receipt)
            adopt(target, receipt, self.root)
        self.prepare_checks(result)
        results = self.check()
        provenance = results['provenance']
        self.assertEqual((provenance['execution'], provenance['outcome']), ('ok', 'info'), provenance)
        change = next(c for c in provenance['details']['component_changes'] if c['reference'] == 'R100')
        self.assertEqual((change['before']['value'], change['after']['value']), ('120', '60'))
        self.assertEqual(provenance['details']['source_evidence'], 'provenance_only')
        self.assertEqual(results['interface']['outcome'], 'defect', results['interface'])
        self.assertIn('R9-value', results['interface']['details']['violations'])

    def test_port_unbound_refused(self):
        del self.bindings['can']['TXD']
        with self.assertRaisesRegex(ValueError, 'every boundary port'):
            self.compose()
        self.assertFalse((self.root / 'project').exists())

    def test_reference_collision_refused(self):
        self.intent['components'][0]['reference'] = 'R105'
        with self.assertRaisesRegex(ValueError, 'reference collision'):
            self.compose()
        self.assertFalse((self.root / 'project').exists())

    def test_alias_and_duplicate_range_refused(self):
        self.bindings['can']['S'] = 'INDEPENDENT_S'
        with self.assertRaisesRegex(ValueError, 'alias binding'):
            self.compose()
        self.bindings['can']['S'] = 'GND'
        other = dict(self.requests[0], id='can2')
        self.requests.append(other)
        self.bindings['can2'] = dict(self.bindings['can'])
        with self.assertRaisesRegex(ValueError, 'ranges collide'):
            self.compose()

    def test_singleton_boundary_not_filled_by_fake_component(self):
        self.intent['nets'] = [n for n in self.intent['nets'] if n['name'] != 'TX']
        self.intent['no_connects'] = [{'component': 'interface', 'pin': {'number': '3'}}]
        with self.assertRaisesRegex(ValueError, 'singleton_net'):
            self.compose()
        self.assertFalse((self.root / 'project').exists())

    def test_deterministic_order_and_native_provenance(self):
        result = self.compose()
        self.intent['nets'].reverse()
        self.intent['components'].reverse()
        repeated = self.compose()
        self.assertEqual(result, repeated)
        self.emit(result)
        self.prepare_checks(result)
        results = self.check()
        for cid in ('interface', 'provenance'):
            self.assertEqual((results[cid]['execution'], results[cid]['outcome']), ('ok', 'pass'), results[cid])
        self.assertEqual(results['provenance']['details']['component_changes'], [])
        self.assertEqual(results['provenance']['details']['net_changes'], [])
        for index in (0, 1):
            self.assertTrue((self.root / f'run/work/provenance/source-{index}/design.xml').is_file())
        from agent_ee.reference_blocks import validate_block
        self.assertEqual(validate_block(self.root / 'block')['status'], 'unverified')

    def test_source_and_receipt_tampers_refused(self):
        from agent_ee.block_adaptation import _instance
        from agent_ee.candidate import capture
        result = self.compose()
        self.emit(result)
        self.prepare_checks(result)
        for sheet in ('wbo1', 'psu'):
            path = self.root / f'block/sheets/{sheet}.kicad_sch'
            before = path.read_bytes()
            path.write_bytes(before + b'\n')
            with self.subTest(sheet=sheet):
                with self.assertRaisesRegex(ValueError, 'inventory/hash differs'):
                    _instance(self.root, 'instance.json')
                run = self.root / ('tamper-' + sheet); run.mkdir()
                with self.assertRaises(ValueError):
                    capture(self.root / 'scope.json', run)
            path.write_bytes(before)
        record = read_json(self.root / 'instance.json')
        for field, edit in (('references', {'R9':'R999'}), ('source_parts', {}), ('native_bindings', {})):
            broken = copy.deepcopy(record); broken[field] = edit
            write_json(self.root / 'instance.json', broken)
            with self.subTest(field=field), self.assertRaises(ValueError):
                _instance(self.root, 'instance.json')
        write_json(self.root / 'instance.json', record)

    def test_native_pin_adaptation_is_boundary_defect(self):
        result = self.compose()
        canh = next(n for n in result['intent']['nets'] if n['name'] == 'CANH')
        canl = next(n for n in result['intent']['nets'] if n['name'] == 'CANL')
        first = next(t for t in canh['terminals'] if t['component'] == 'can/R9')
        second = next(t for t in canl['terminals'] if t['component'] == 'can/R9')
        first['pin'], second['pin'] = second['pin'], first['pin']
        self.emit(result)
        self.prepare_checks(result)
        results = self.check()
        self.assertEqual(results['provenance']['outcome'], 'defect', results['provenance'])
        self.assertEqual({tuple(c['terminal']) for c in results['provenance']['details']['net_changes']},
                         {('R100', '1'), ('R100', '2')})
        self.assertEqual(results['interface']['outcome'], 'defect', results['interface'])


class DeferredFlatContractTests(unittest.TestCase):
    """Authored XML replaces only export; no native/candidate qualification."""
    def setUp(self):
        from tests.test_block_no_connects import source_fixture
        from agent_ee.reference_blocks import block_contract
        from agent_ee.storage import digest
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.sheet,self.xml=source_fixture();root=self.root;directory=root/'block';directory.mkdir()
        (directory/'source.kicad_sch').write_bytes(self.sheet)
        (directory/'requirements.md').write_text('Preserve abstract fixture topology; evaluate new board response at25C.')
        parts=dict(schema='agent-ee.block-parts/1',source='Authored CPU fixture',catalog=None,
            parts=[dict(instance='/',reference=ref,lib_id='Owned:Package'+str(n),value='Package'+str(n),footprint='Owned:Package',lcsc=None,mpn=None,manufacturer=None,populated=True,catalog=None) for ref,n in [('U1',5),('U2',10)]])
        write_json(directory/'parts.json',parts)
        record=dict(schema='agent-ee.block/1',id='owned',version='fresh-contract',role='test',source=dict(repository='authored',commit='a'*40,paths={'source.kicad_sch':'source.kicad_sch'},license='Owned',upstream_origin='authored',upstream_license='Owned'),sheet='source.kicad_sch',parts_manifest='parts.json',boundary_ports=[],
            instances=[dict(path='/',uuid_path='/',sheet='source.kicad_sch',references=['U1','U2'])],runs=[],
            requirements=[dict(id='native',statement='Export the source',checks=['old-netlist']),
                dict(id='later',statement='Evaluate the response',checks=['old-response'],verification_phase='adaptation',source=dict(path='requirements.md',locator='line1'),conditions=['25C'])],
            check_set=[dict(id='old-netlist',kind='netlist',params={}),dict(id='old-response',kind='rc_transient',params=dict(netlist_check='old-netlist',input_net='SIGNAL',output_net='SIGNAL',ground_net='GND',measurements=[dict(name='vout',at_ms=1,min=.6,max=.7)]))],
            files={p:digest(directory/p) for p in ('source.kicad_sch','parts.json','requirements.md')})
        write_json(directory/'block-contract.json',block_contract(record));record['files']['block-contract.json']=digest(directory/'block-contract.json');write_json(directory/'block.json',record)
        self.source=record
        plan=dict(schema='agent-ee.block-composition/1',sources=[dict(sheet='source.kicad_sch',uuid_path='/',sha256=digest(directory/'source.kicad_sch'),references={'U1':'U1','U2':'U2'},nets={'/COMMON':'SIGNAL'},reason='authored')],
            ports=[dict(name='SIGNAL',net_name='SIGNAL',direction='passive',voltage_domain='test',current_a=None,alias_of=None,reason='authored')],reason='authored')
        write_json(root/'composition.json',plan)
        self.intent=dict(schema='agent-ee.connectivity-intent/1',libraries=[],components=[],nets=[],no_connects=[])
        self.request=dict(id='pair',block='block',composition='composition.json',reference_ranges={'U':[11,12]},sheet='project/flat.kicad_sch',requirements={'native':['netlist'],'later':['response']})

    def compose(self,request=None):
        from unittest.mock import patch
        def export(intent,*,inputs,work,**kwargs):
            (work/'design.xml').write_bytes(self.xml)
            return dict(execution='ok',exit_code=0)
        with patch('agent_ee.native_run.run',side_effect=export):
            return block_adaptation.compose_flat(self.root,self.intent,[request or self.request],{'pair':{'SIGNAL':'signal'}},captured_libraries={})

    def test_flat_producer_reader_and_capability_keep_deferred_acceptance_and_bound_criteria(self):
        from agent_ee.storage import digest
        from agent_ee.block_flat import read_instance
        result=self.compose();record=result['block_instances'][0]
        self.assertEqual(record['references'],{'U1':'U11','U2':'U12'})
        write_json(self.root/'instance.json',record);read_instance(self.root,record)
        (self.root/'project').mkdir();(self.root/record['sheet']).write_bytes(self.sheet)
        source=block_adaptation._source(self.root,'block','composition.json')
        requirements=[dict(id='pair-'+r['id'],intent=block_adaptation._requirement_intent(source,r),statement=r['statement'],source=dict(path='block/block.json',locator='requirements/'+r['id']),checks=record['requirements'][r['id']]) for r in source['requirements']]
        params=copy.deepcopy(source['check_set'][1]['params']);params.update(netlist_check='netlist',input_net='/signal',output_net='/signal')
        scope=dict(schema='agent-ee.scope/3',schematic=record['sheet'],checks=[dict(id='netlist',kind='netlist'),dict(id='response',kind='rc_transient',params=params),dict(id='provenance',kind='block_provenance',params=dict(instance='instance.json',netlist_check='netlist'))],baseline=dict(requirements=requirements))
        files={str(p.relative_to(self.root)):digest(p) for p in self.root.rglob('*') if p.is_file()}
        block_adaptation.capability(dict(instance='instance.json',netlist_check='netlist'),scope,files,self.root,'provenance')
        self.assertEqual(requirements[0]['intent'],'informational');self.assertEqual(requirements[1]['intent'],'acceptance')
        for change in ('missing-baseline','family','criteria','informational'):
            changed=copy.deepcopy(scope)
            if change=='missing-baseline':changed['baseline']['requirements'].pop()
            elif change=='family':changed['checks'][1]=dict(id='response',kind='erc',params={})
            elif change=='criteria':changed['checks'][1]['params']['measurements'][0]['max']=9
            else:changed['baseline']['requirements'][1]['intent']='informational'
            with self.subTest(change=change),self.assertRaises(ValueError):block_adaptation.capability(dict(instance='instance.json',netlist_check='netlist'),changed,files,self.root,'provenance')
        changed=copy.deepcopy(record);changed['requirements'].pop('later')
        with self.assertRaisesRegex(ValueError,'every source and adaptation'):read_instance(self.root,changed)

    def test_flat_missing_unknown_duplicate_and_legacy_pending_never_publish(self):
        from agent_ee.reference_blocks import block_contract
        from agent_ee.storage import digest
        for change in ('missing','extra','duplicate'):
            request=copy.deepcopy(self.request)
            if change=='missing':request['requirements'].pop('later')
            elif change=='extra':request['requirements']['unknown']=['response']
            else:request['requirements']['later']=['response','response']
            with self.subTest(change=change),self.assertRaises(ValueError):self.compose(request)
        record=self.source;record['pending_requirements']=[dict(id='unresolved',statement='Unresolved',source=dict(path='requirements.md',locator='line1'),conditions=['25C'],blocked_by='Missing')]
        write_json(self.root/'block/block-contract.json',block_contract(record));record['files']['block-contract.json']=digest(self.root/'block/block-contract.json');write_json(self.root/'block/block.json',record)
        with self.assertRaisesRegex(ValueError,'pending cannot be dropped'):self.compose()
        self.assertFalse((self.root/'project').exists())

class TopologyFixtureTransitionTests(unittest.TestCase):
    """CPU source-contract controls; root separately runs the native regressions."""
    def test_archived_pending_refuses_flat_and_hierarchical_without_publishing(self):
        from tests.test_block_adaptation import fixture
        for mode in ('flat','hierarchical'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)
                if mode=='flat':
                    intent,requests,bindings,libraries=prepare(root)
                    before=copy.deepcopy(intent)
                    with self.assertRaisesRegex(ValueError,'pending cannot be dropped'):
                        block_adaptation.compose_flat(root,intent,requests,bindings,captured_libraries=libraries)
                    self.assertEqual(intent,before)
                    self.assertFalse((root/'project/flat.kicad_sch').exists())
                else:
                    scope,request=fixture(root)
                    shutil.copytree(CAN/'block',root/'block')
                    shutil.copyfile(CAN/'composition.json',root/'composition.json')
                    request.update(block='block',composition='composition.json')
                    source=read_json(root/'block/block.json')
                    request['requirements']={r['id']:r['checks'] for r in source['requirements']}
                    before=(root/request['sheet']).read_bytes()
                    with self.assertRaisesRegex(ValueError,'pending cannot be dropped'):
                        block_adaptation.instantiate(root,scope,request)
                    self.assertEqual((root/request['sheet']).read_bytes(),before)
                self.assertFalse((root/'instance.json').exists())

    def test_versioned_lineage_and_joint_contract_without_rewriting_archive(self):
        from tests.block_topology_fixture import derive_topology_fixture,assert_topology_acceptance,LINEAGE,VERSION
        from agent_ee.storage import digest
        before={str(p.relative_to(CAN)):digest(p) for p in CAN.rglob('*') if p.is_file()}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);record=derive_topology_fixture(CAN/'block',root/'block')
            original=read_json(CAN/'block/block.json');lineage=read_json(root/'block'/LINEAGE)
            self.assertEqual(record['version'],VERSION)
            self.assertEqual(lineage['original_pending'],original['pending_requirements'][0])
            self.assertEqual(record['runs'],original['runs'])
            self.assertEqual(record['check_set'],original['check_set'])
            self.assertEqual(record['boundary_ports'],original['boundary_ports'])
            scope=dict(checks=[dict(id='interface',kind='hardware_interface'),dict(id='provenance',kind='block_provenance')],
                baseline=dict(requirements=[dict(intent='acceptance',checks=['interface']),dict(intent='acceptance',checks=['provenance'])]))
            assert_topology_acceptance(root,scope)
            for check in ('interface','provenance'):
                bad=copy.deepcopy(scope);bad['baseline']['requirements']=[r for r in bad['baseline']['requirements'] if check not in r['checks']]
                with self.assertRaisesRegex(ValueError,'both fresh acceptance'):assert_topology_acceptance(root,bad)
        self.assertEqual(before,{str(p.relative_to(CAN)):digest(p) for p in CAN.rglob('*') if p.is_file()})

    def test_flat_and_hierarchical_fresh_contracts_are_executable_source_contracts(self):
        from tests.test_block_composition import compose
        from tests.block_topology_fixture import derive_topology_fixture,assert_topology_acceptance
        from unittest.mock import patch
        from agent_ee.storage import digest
        for mode in ('flat','hierarchical'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                base=Path(tmp)
                if mode=='flat':
                    obj=FlatComposeTests('test_can_block_flat');obj.root=base
                    obj.intent,obj.requests,obj.bindings,obj.libraries=prepare(base,topology=True)
                    result=obj.compose();obj.prepare_checks(result);root=base
                else:
                    source=base/'source';source.mkdir();derive_topology_fixture(CAN/'block',source/'block')
                    shutil.copyfile(CAN/'composition.json',source/'composition.json')
                    root=base/'prepared'
                    with patch.object(compose,'ROOT',source):compose.prepare(root)
                scope=read_json(root/'scope.json');assert_topology_acceptance(root,scope)
                files={str(p.relative_to(root)):digest(p) for p in root.rglob('*') if p.is_file()}
                result=block_adaptation.capability(dict(instance='instance.json',netlist_check='netlist'),scope,files,root,'provenance')
                self.assertIn('block-boundary:CAN+',result['predicates'])
