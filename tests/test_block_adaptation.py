"""Owned native block fixtures; source evidence never substitutes for board checks."""
import copy
import importlib.util
import json
import tempfile
import shutil
import time
import unittest
from pathlib import Path
from xml.etree.ElementTree import ParseError
from unittest.mock import patch

from agent_ee.block_adaptation import instantiate, check_block_provenance
from agent_ee.baseline import freeze_evidence, load_baseline, verify_evidence, validate_witnesses
from agent_ee.candidate import capture
from agent_ee.checks import Ctx, CheckError, run_checks
from agent_ee.reference_blocks import block_contract
from agent_ee.schematic_emit import parse_quoted, format_sexpr
from tests.block_parts_fixture import parts_manifest
from agent_ee.storage import digest, read_json, write_json
from agent_ee.symbol_library import children, field
from tests.toolchain import require_toolchain

REPO = Path(__file__).resolve().parents[1]
ROOT_UUID = 'e32a2f24-2ee4-4987-b0c0-4f6ff7523be4'
CHILD_UUID = '62e7fa6a-3fb5-4e82-953c-0c76f667cf92'


def dump(tree):
    return ('\n'.join(format_sexpr(tree)) + '\n').encode()


def fixture(root):
    project = root / 'project'
    block = root / 'blocks' / 'rc'
    project.mkdir(parents=True)
    block.mkdir(parents=True)
    spec = importlib.util.spec_from_file_location('original_interface', REPO / 'fixtures/hardware-interface/generate.py')
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    source = parse_quoted(gen.schematic().encode())
    # Retain exactly the original synthetic RC and its three-pin connector.
    for node in list(source[1:]):
        if not isinstance(node, list):
            continue
        if node[0] == 'wire' or (node[0] in ('symbol', 'label') and float(field(node, 'at', 3)[0]) >= 100):
            source.remove(node)
    ports = [('VIN', 'input', '50'), ('VOUT', 'output', '52.54'), ('GND', 'passive', '55.08')]
    for index, (name, direction, y) in enumerate(ports):
        source.append(parse_quoted(f'''(hierarchical_label "{name}" (shape {direction}) (at 40 {y} 0)
          (effects (font (size 1 1))) (uuid "c5485795-b0e0-4ac5-a51d-0f903a7615a{index}"))'''.encode()))
    (block / 'rc.kicad_sch').write_bytes(dump(source))
    write_json(block / 'parts.json', parts_manifest())
    record = {'schema': 'agent-ee.block/1', 'id': 'owned-rc', 'version': '7', 'role': 'filter',
        'source': {'repository': 'owned synthetic', 'commit': 'a'*40,
                   'paths': {'rc.kicad_sch': 'rc.kicad_sch'}, 'license': 'MIT'},
        'sheet': 'rc.kicad_sch', 'parts_manifest': 'parts.json',
        'boundary_ports': [{'name': n, 'net_name': {'VIN': '/IN', 'VOUT': '/OUT', 'GND': '/GND'}[n], 'direction': d,
            'voltage_domain': 'signal', 'current_a': 1} for n, d, _ in ports],
        'requirements': [{'id': 'response', 'statement': 'RC step at 1 ms is between .62 and .65 V', 'checks': ['old-response']}],
        'files': {p: digest(block / p) for p in ('rc.kicad_sch', 'parts.json')}, 'runs': []}
    write_json(block / 'block-contract.json', block_contract(record))
    record['files']['block-contract.json'] = digest(block / 'block-contract.json')
    write_json(block / 'block.json', record)
    parent = f'''(kicad_sch (version 20250114) (generator "eeschema") (uuid "{ROOT_UUID}") (paper "A4") (lib_symbols)
      (sheet (at 20 20) (size 30 20) (uuid "{CHILD_UUID}")
        (property "Sheetname" "filter" (at 20 20 0) (effects (font (size 1 1))))
        (property "Sheetfile" "filter.kicad_sch" (at 20 40 0) (effects (font (size 1 1))))
        (pin "VIN" input (at 20 25 180) (effects (font (size 1 1))) (uuid "6075e5d5-f6e1-49f1-b2a2-450fd1507611"))
        (pin "VOUT" output (at 50 25 0) (effects (font (size 1 1))) (uuid "6075e5d5-f6e1-49f1-b2a2-450fd1507612"))
        (pin "GND" passive (at 20 35 180) (effects (font (size 1 1))) (uuid "6075e5d5-f6e1-49f1-b2a2-450fd1507613")))
      (label "IN" (at 20 25 0) (effects (font (size 1 1))) (uuid "d0a5239b-cbe3-44d0-b19e-cf771e1c8511"))
      (label "OUT" (at 50 25 0) (effects (font (size 1 1))) (uuid "d0a5239b-cbe3-44d0-b19e-cf771e1c8512"))
      (label "GND" (at 20 35 0) (effects (font (size 1 1))) (uuid "d0a5239b-cbe3-44d0-b19e-cf771e1c8513")))'''
    (project / 'board.kicad_sch').write_text(parent)
    (project / 'filter.kicad_sch').write_text('(kicad_sch (version 20250114) (generator "eeschema") (uuid "c4c6a261-f60e-4937-9c29-ac498a28d081") (lib_symbols))')
    (project / 'board.kicad_pcb').write_text('''(kicad_pcb (version 20250114) (generator "pcbnew")
      (general (thickness 1.6)) (paper "A4")
      (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user))
      (setup (pad_to_mask_clearance 0)))''')
    (root / 'requirements.md').write_text('Board requirements: retain native connectivity and fresh functional evidence.')
    scope = {'schema': 'agent-ee.scope/3', 'schematic': 'project/board.kicad_sch', 'board': 'project/board.kicad_pcb',
        'inputs': [{'path': 'project', 'at': 'project'}, {'path': 'blocks', 'at': 'blocks'},
                   {'path': 'requirements.md', 'at': 'requirements.md'}, {'path': 'instance.json', 'at': 'instance.json'}],
        'requirements': ['requirements.md'], 'mutable_inputs': ['project/board.kicad_sch', 'project/filter.kicad_sch'],
        'checks': [{'id': 'netlist', 'kind': 'netlist'}, {'id': 'response', 'kind': 'rc_transient',
            'params': {'netlist_check': 'netlist', 'input_net': '/IN', 'output_net': '/OUT', 'ground_net': '/GND',
                       'measurements': [{'name': 'vout_1ms', 'at_ms': 1, 'min': .62, 'max': .65}]}}],
        'baseline': {'requirements': [{'id': 'native', 'intent': 'informational', 'statement': 'Export native netlist',
            'source': {'path': 'requirements.md', 'locator': 'native'}, 'checks': ['netlist']}],
            'targets': ['requirements.md'], 'reference_architectures': ['requirements.md']}}
    request = {'id': 'filter', 'block': 'blocks/rc', 'sheet': 'project/filter.kicad_sch',
        'instance_path': '/' + CHILD_UUID + '/', 'bindings': {'VIN': '/IN', 'VOUT': '/OUT', 'GND': '/GND'},
        'requirements': {'response': ['response']}, 'record': 'instance.json',
        'provenance_check': 'provenance', 'netlist_check': 'netlist'}
    return scope, request


class InstantiationTests(unittest.TestCase):
    def test_invalid_block_parts_refused_before_writing_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scope, request = fixture(root)
            child = root / request['sheet']
            original = child.read_bytes()
            source = root / request['block']
            document = read_json(source / 'parts.json')
            document['parts'][0]['lcsc'] = 'NOT-AN-LCSC'
            write_json(source / 'parts.json', document)
            record = read_json(source / 'block.json')
            record['files']['parts.json'] = digest(source / 'parts.json')
            write_json(source / 'block.json', record)
            with self.assertRaisesRegex(ValueError, 'LCSC'):
                instantiate(root, scope, request)
            self.assertEqual(child.read_bytes(), original)
            self.assertFalse((root / 'instance.json').exists())

    def test_byte_exact_copy_mapped_requirements_and_immutable_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scope, request = fixture(root)
            original = copy.deepcopy(scope)
            result = instantiate(root, scope, request)
            self.assertEqual(scope, original)
            self.assertEqual((root / 'project/filter.kicad_sch').read_bytes(), (root / 'blocks/rc/rc.kicad_sch').read_bytes())
            write_json(root / 'scope.json', result)
            run = root / 'run'
            run.mkdir()
            manifest = capture(root / 'scope.json', run)
            baseline = load_baseline(run)
            # AGENTEE-556: the 2026-10-18 regression dropped block predicates
            # while leaving the kind registered. Assert the published baseline.
            provenance = baseline['coverage']['checks']['provenance']
            self.assertTrue(provenance['can_evaluate_bound'])
            self.assertEqual(provenance['predicates'],
                             ['block-boundary:VIN', 'block-boundary:VOUT', 'block-boundary:GND'])
            self.assertIn('instance.json', baseline['files'])
            self.assertTrue(all('blocks/rc/' + p in baseline['files'] for p in read_json(root / 'blocks/rc/block.json')['files']))
            from agent_ee.campaign import mutable_inputs
            self.assertEqual(mutable_inputs(manifest, baseline), scope['mutable_inputs'])
            self.assertEqual(baseline['requirements'][-2]['checks'], ['response'])
            # Neither the source check name nor its old run state enters criteria.
            self.assertNotIn('old-response', json.dumps(baseline['criteria']))
            from agent_ee.campaign import check_proposal
            design = root / 'writer'
            shutil.copytree(run / 'inputs', design)
            child = design / request['sheet']
            child.write_bytes(child.read_bytes().replace(b'"10k"', b'"100k"'))
            state = {'baseline': baseline, 'mutable_inputs': scope['mutable_inputs']}
            check_proposal(design, manifest, state)
            record = design / 'instance.json'
            record.write_text(record.read_text().replace('/IN', '/HIDDEN'))
            with self.assertRaisesRegex(ValueError, 'immutable verification input'):
                check_proposal(design, manifest, state)


    def test_scope2_freezes_source_and_instance_without_scope3_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scope, request = fixture(root)
            scope['schema'] = 'agent-ee.scope/2'
            for req in scope['baseline']['requirements']:
                req.pop('intent', None)
            result = instantiate(root, scope, request)
            write_json(root / 'scope.json', result)
            run = root / 'run'
            run.mkdir()
            capture(root / 'scope.json', run)
            baseline = load_baseline(run)
            self.assertEqual(baseline['schema'], 'agent-ee.baseline/1')
            self.assertIn('instance.json', baseline['files'])
            self.assertIn('blocks/rc/rc.kicad_sch', baseline['files'])
            changed = read_json(run / 'inputs/instance.json')
            changed['bindings']['VIN'] = '/OTHER'
            write_json(run / 'inputs/instance.json', changed)
            with self.assertRaisesRegex(ValueError, '(captured file missing or hash changed|block dependency not captured or changed): instance.json'):
                load_baseline(run)

    def test_refused_scaffolds_leave_precreated_sheet_unchanged(self):
        cases = {
            'missing_binding': lambda r, s, p: r['bindings'].pop('VIN'),
            'wrong_instance': lambda r, s, p: r.update(instance_path='/' + ROOT_UUID + '/'),
            'no_fresh_check': lambda r, s, p: r['requirements'].update(response=['provenance']),
            'missing_mapping': lambda r, s, p: r.update(requirements={}),
            'mutable_source': lambda r, s, p: s['mutable_inputs'].append('blocks/rc/rc.kicad_sch'),
            'unsafe_record': lambda r, s, p: r.update(record='../escape.json'),
            'nonempty_sheet': lambda r, s, p: p.write_bytes((p.parent.parent / 'blocks/rc/rc.kicad_sch').read_bytes()),
            'linked_sheet': lambda r, s, p: (p.unlink(), p.symlink_to('../blocks/rc/rc.kicad_sch')),
        }
        for name, mutate in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                scope, request = fixture(root)
                path = root / request['sheet']
                mutate(request, scope, path)
                before = None if path.is_symlink() else path.read_bytes()
                with self.assertRaises((ValueError, FileNotFoundError)):
                    instantiate(root, scope, request)
                if before is not None:
                    self.assertEqual(path.read_bytes(), before)
                self.assertFalse((root / 'instance.json').exists())


class NativeAdaptationTests(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)
        self.temp = tempfile.TemporaryDirectory(prefix='agent-ee-block-adaptation-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.scope, self.request = fixture(self.root)
        self.scope = instantiate(self.root, self.scope, self.request)
        self.index = 0

    def run_candidate(self, mutation=None):
        if mutation:
            mutation(self.root / 'project/filter.kicad_sch')
        write_json(self.root / 'scope.json', self.scope)
        self.index += 1
        run = self.root / ('run' + str(self.index))
        run.mkdir()
        capture(self.root / 'scope.json', run)
        checks = run_checks(run, time.monotonic() + 120)
        return run, {c['id']: c for c in checks}

    def test_unchanged_instance_and_fresh_sealed_board_response(self):
        run, checks = self.run_candidate()
        self.assertEqual((checks['provenance']['execution'], checks['provenance']['outcome']), ('ok', 'pass'), checks)
        self.assertIn('same as block v7, sha256 ' + digest(self.root / 'blocks/rc/rc.kicad_sch'), checks['provenance']['summary'])
        self.assertEqual(checks['provenance']['details']['component_changes'], [])
        self.assertEqual(checks['provenance']['details']['net_changes'], [])
        self.assertEqual((checks['response']['execution'], checks['response']['outcome']), ('ok', 'pass'), checks)
        self.assertAlmostEqual(checks['response']['details']['measurements']['vout_1ms']['measured'], .63212, places=3)
        self.assertTrue((run / 'work/response/execution.json').is_file())
        freeze_evidence(run, list(checks.values()))
        verify_evidence(run, list(checks.values()), required=True)
        with self.assertRaisesRegex(ValueError, 'freshly failed'):
            validate_witnesses(run, [{'requirement_id': 'filter-boundary', 'check_id': 'provenance'}],
                               [{'path': 'checks/provenance.json', 'locator': 'boundary'}])

    def test_changed_resistor_lists_before_after_and_board_simulation_fails_fresh(self):
        run, checks = self.run_candidate(lambda p: p.write_text(p.read_text().replace('"10k"', '"100k"')))
        provenance = checks['provenance']
        self.assertEqual((provenance['execution'], provenance['outcome']), ('ok', 'pass'), checks)
        changed, = provenance['details']['component_changes']
        self.assertEqual((changed['before']['value'], changed['after']['value']), ('10k', '100k'))
        self.assertEqual(checks['response']['outcome'], 'defect', checks)
        self.assertAlmostEqual(checks['response']['details']['measurements']['vout_1ms']['measured'], .09516, places=3)
        freeze_evidence(run, list(checks.values()))
        verify_evidence(run, list(checks.values()), required=True)

    def test_missing_rebound_disconnected_and_moved_boundary_are_defects(self):
        original = (self.root / 'project/filter.kicad_sch').read_bytes()
        def remove_child_port(path):
            tree = parse_quoted(path.read_bytes())
            tree.remove(next(n for n in children(tree, 'hierarchical_label') if n[1] == 'VIN'))
            path.write_bytes(dump(tree))
        def move_child_port(path):
            tree = parse_quoted(path.read_bytes())
            label = next(n for n in children(tree, 'hierarchical_label') if n[1] == 'VIN')
            children(label, 'at')[0][1] = '5'
            path.write_bytes(dump(tree))
        parent = self.root / 'project/board.kicad_sch'
        before_parent = parent.read_text()
        for label in ('removed', 'rebound', 'missing_parent_pin', 'moved'):
            with self.subTest(case=label):
                (self.root / 'project/filter.kicad_sch').write_bytes(original)
                parent.write_text(before_parent)
                if label == 'removed':
                    remove_child_port(self.root / 'project/filter.kicad_sch')
                elif label == 'moved':
                    move_child_port(self.root / 'project/filter.kicad_sch')
                elif label == 'rebound':
                    parent.write_text(before_parent.replace('(label "IN"', '(label "WRONG"'))
                else:
                    ast = parse_quoted(parent.read_bytes())
                    sheet = children(ast, 'sheet')[0]
                    sheet.remove(next(p for p in children(sheet, 'pin') if p[1] == 'VIN'))
                    parent.write_bytes(dump(ast))
                run, checks = self.run_candidate()
                result = checks['provenance']
                self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), checks)
                self.assertTrue(any(p['port'] == 'VIN' and p['status'] == 'violation' for p in result['details']['boundary']))
                freeze_evidence(run, list(checks.values()))
                verify_evidence(run, list(checks.values()), required=True)
                validate_witnesses(run, [{'requirement_id': 'filter-boundary', 'check_id': 'provenance'}],
                                   [{'path': 'checks/provenance.json', 'locator': 'boundary/VIN'}])



    def test_root_components_and_wire_connected_ports_preserve_native_graph(self):
        from agent_ee.schematic_emit import Quoted
        parent_path = self.root / 'project/board.kicad_sch'
        child_path = self.root / 'project/filter.kicad_sch'
        parent = parse_quoted(parent_path.read_bytes())
        child = parse_quoted(child_path.read_bytes())
        definition = next(n for n in children(children(child, 'lib_symbols')[0], 'symbol') if n[1] == 'Device:R')
        children(parent, 'lib_symbols')[0].append(copy.deepcopy(definition))
        resistor = copy.deepcopy(next(n for n in children(child, 'symbol') if field(n, 'lib_id', 1) == ['Device:R']))
        children(resistor, 'uuid')[0][1] = Quoted('59149514-76bf-4cff-894e-25ff3bcb8041')
        children(resistor, 'at')[0][1:3] = ['100', '100']
        for prop in children(resistor, 'property'):
            if prop[1] == 'Reference':
                prop[2] = Quoted('REXT')
        for pins in children(resistor, 'pin'):
            children(pins, 'uuid')[0][1] = Quoted('59149514-76bf-4cff-894e-25ff3bcb805' + pins[1])
        resistor.remove(children(resistor, 'instances')[0])
        parent.append(resistor)
        for index, (name, y) in enumerate((('IN', '100'), ('GND', '102.54'))):
            parent.append(parse_quoted(f'(label "{name}" (at 100 {y} 0) (effects (font (size 1 1))) (uuid "fdb7c87d-d9f6-487d-a890-6830a765ce3{index}"))'.encode()))
        parent_path.write_bytes(dump(parent))
        # Move only the hierarchical label and reconnect it with an original
        # conductor. Probes must work at a wire endpoint, not just on a pin.
        port = next(n for n in children(child, 'hierarchical_label') if n[1] == 'VIN')
        children(port, 'at')[0][1] = '30'
        child.append(parse_quoted(b'(wire (pts (xy 30 50) (xy 40 50)) (stroke (width 0) (type default)) (uuid "8cdd1c26-3dc9-45c8-a480-7c86b6157ba4"))'))
        child_path.write_bytes(dump(child))
        run, checks = self.run_candidate()
        self.assertEqual((checks['provenance']['execution'], checks['provenance']['outcome']), ('ok', 'pass'), checks)
        freeze_evidence(run, list(checks.values()))
        verify_evidence(run, list(checks.values()), required=True)

    def test_source_run_references_supply_no_board_check_results(self):
        # Retain an actual earlier sealed check set, then reference its identities
        # from block metadata. Board execution must not open that earlier run.
        old_run, old_checks = self.run_candidate()
        freeze_evidence(old_run, list(old_checks.values()))
        baseline = load_baseline(old_run)
        block_path = self.root / 'blocks/rc/block.json'
        block = read_json(block_path)
        block['runs'] = [{'run_id': 'earlier-sealed-board',
            'candidate_id': old_checks['response']['candidate_id'],
            'baseline_id': baseline['baseline_id'],
            'files': {name: 'blocks/rc/' + name for name in block['files']}}]
        write_json(block_path, block)
        with patch('agent_ee.reference_blocks.verify', side_effect=AssertionError('source evidence must not be read')):
            run, checks = self.run_candidate(lambda p: p.write_text(p.read_text().replace('"10k"', '"100k"')))
        self.assertEqual(old_checks['response']['outcome'], 'pass')
        self.assertEqual(checks['response']['outcome'], 'defect', checks)
        self.assertEqual(checks['provenance']['outcome'], 'pass', checks)
        self.assertNotEqual(checks['response']['candidate_id'], old_checks['response']['candidate_id'])
        self.assertNotEqual(digest(run / 'work/response/execution.json'), digest(old_run / 'work/response/execution.json'))
        freeze_evidence(run, list(checks.values()))
        verify_evidence(run, list(checks.values()), required=True)

    def test_source_export_crash_missing_and_malformed_output_are_unknown(self):
        import dataclasses
        from agent_ee import native_run
        from native_hooks import Hooked
        # Inject only the executable command. The production sandbox adapter still launches
        # and records the failing process inside its networkless sandbox.
        commands = {
            'crash': ['/bin/false'],
            'missing': ['/bin/true'],
            'malformed': ['/usr/bin/python3', '-c', "open('/work/design.xml','w').write('<bad>')"],
        }
        for label, argv in commands.items():
            def fault(call, run, argv=argv):
                return run(intent=dataclasses.replace(call.intent, argv=tuple(argv)))
            with self.subTest(case=label), native_run.using(Hooked(fault)):
                # Exercise the provenance export itself, independently of the
                # earlier native board producer.
                write_json(self.root / 'scope.json', self.scope)
                self.index += 1
                run = self.root / ('fault' + str(self.index))
                run.mkdir()
                manifest = capture(self.root / 'scope.json', run)
                from agent_ee.block_adaptation import _export
                ctx = Ctx(run, manifest, manifest['scope']['checks'][-1], time.monotonic() + 30)
                with self.assertRaises((CheckError, OSError, ParseError, ValueError)):
                    _export(ctx, 'source', '/candidate/blocks/rc/rc.kicad_sch')
                self.assertNotEqual(ctx.result['outcome'], 'pass')
                record = read_json(ctx.work / 'source/execution.json')
                self.assertEqual(record['argv'], argv)
                self.assertNotIn('--share-net', record['sandbox_argv'])
                self.assertIn('--unshare-net', record['sandbox_argv'])

    def test_missing_malformed_and_failed_exports_never_inherit_a_pass(self):
        run, checks = self.run_candidate()
        manifest = read_json(run / 'candidate.json')
        spec = manifest['scope']['checks'][-1]
        for label in ('missing', 'malformed', 'failed'):
            with self.subTest(case=label):
                ctx = Ctx(run, manifest, spec, time.monotonic() + 60)
                xml = run / 'work/netlist/design.xml'
                original = xml.read_bytes()
                if label == 'missing':
                    xml.unlink()
                elif label == 'malformed':
                    xml.write_text('<broken>')
                else:
                    producer = read_json(run / 'checks/netlist.json')
                    producer['execution'] = 'failed'
                    write_json(run / 'checks/netlist.json', producer)
                with self.assertRaises(CheckError):
                    check_block_provenance(ctx)
                self.assertNotEqual(ctx.result['outcome'], 'pass')
                xml.write_bytes(original)


class DeferredAdaptationTests(unittest.TestCase):
    def prepared(self,root):
        scope,request=fixture(root);directory=root/'blocks/rc';record=read_json(directory/'block.json')
        (directory/'requirements.md').write_text('Source-only RC response contract; deferred check must run at25C on every new board.')
        record['files']['requirements.md']=digest(directory/'requirements.md')
        record['requirements'].append(dict(id='later',statement='The board response at2ms shall be within the declared bounds',
            source=dict(path='requirements.md',locator='line1'),conditions=['25C'],verification_phase='adaptation',checks=['old-later']))
        later=copy.deepcopy(scope['checks'][1]);later['id']='later'
        later['params']['measurements']=[dict(name='vout_2ms',at_ms=2,min=.8,max=.9)]
        scope['checks'].append(later)
        record['check_set']=[dict(id='old-response',kind='rc_transient',params=copy.deepcopy(scope['checks'][1]['params'])),
                             dict(id='old-later',kind='rc_transient',params=copy.deepcopy(later['params']))]
        request['requirements']['later']=['later']
        write_json(directory/'block-contract.json',block_contract(record));record['files']['block-contract.json']=digest(directory/'block-contract.json');write_json(directory/'block.json',record)
        return scope,request,record

    def test_source_and_deferred_both_enter_fresh_acceptance_with_exact_criteria(self):
        from agent_ee.block_adaptation import capability,_instance
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);scope,request,record=self.prepared(root)
            result=instantiate(root,scope,request)
            self.assertEqual({r['id'] for r in result['baseline']['requirements']},{'native','filter-response','filter-later','filter-boundary'})
            self.assertEqual(next(r for r in result['baseline']['requirements'] if r['id']=='filter-later')['intent'],'acceptance')
            files={str(p.relative_to(root)):digest(p) for p in root.rglob('*') if p.is_file()}
            capability(dict(instance='instance.json',netlist_check='netlist'),result,files,root,'provenance')
            for mutate in ('family','limit','informational','mapping'):
                changed=copy.deepcopy(result)
                if mutate=='family':changed['checks'][2]=dict(id='later',kind='netlist',params={})
                elif mutate=='limit':changed['checks'][2]['params']['measurements'][0]['max']=5
                elif mutate=='informational':next(r for r in changed['baseline']['requirements'] if r['id']=='filter-later')['intent']='informational'
                else:next(r for r in changed['baseline']['requirements'] if r['id']=='filter-later')['checks']=['netlist']
                with self.subTest(mutate=mutate),self.assertRaises(ValueError):capability(dict(instance='instance.json',netlist_check='netlist'),changed,files,root,'provenance')
            instance=read_json(root/'instance.json');instance['requirements'].pop('later');write_json(root/'instance.json',instance)
            with self.assertRaisesRegex(ValueError,'every source and adaptation'):_instance(root,'instance.json')

    def test_bad_union_and_legacy_pending_refuse_before_any_child_write(self):
        for change in ('missing','extra','duplicate','pending'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);scope,request,record=self.prepared(root);before=(root/request['sheet']).read_bytes()
                if change=='missing':request['requirements'].pop('later')
                elif change=='extra':request['requirements']['invented']=['later']
                elif change=='duplicate':request['requirements']['later']=['later','later']
                else:
                    record['pending_requirements']=[dict(id='unresolved',statement='Unknown constraint',source=dict(path='requirements.md',locator='line1'),conditions=['25C'],blocked_by='missing source')]
                    directory=root/'blocks/rc';write_json(directory/'block-contract.json',block_contract(record));record['files']['block-contract.json']=digest(directory/'block-contract.json');write_json(directory/'block.json',record)
                with self.assertRaises(ValueError):instantiate(root,scope,request)
                self.assertEqual((root/request['sheet']).read_bytes(),before)
                self.assertFalse((root/'instance.json').exists())

    def test_check_and_requirement_ids_can_overlap_without_losing_fresh_source_binding(self):
        from agent_ee.block_adaptation import _rebound
        value=dict(requirement='thermal',requirement_id='thermal',netlist_check='thermal',requirement_sources={'thermal':dict(path='original.md',locator='line1')})
        result=_rebound(value,{'original.md':'block/original.md'},{'thermal':'instance-thermal'},{'thermal':'fresh-thermal-check'})
        self.assertEqual(result['requirement'],'instance-thermal')
        self.assertEqual(result['requirement_id'],'instance-thermal')
        self.assertEqual(result['netlist_check'],'fresh-thermal-check')
        self.assertEqual(set(result['requirement_sources']),{'instance-thermal'})

    def test_declared_interval_asset_algebra_and_current_requirement_citation_both_survive(self):
        from agent_ee.storage import identity
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);scope,request,record=self.prepared(root);directory=root/'blocks/rc'
            source=dict(schema='agent-ee.interval-assertions/1',correlation='shared-symbol/independent-parameters',
                parameters={'v':dict(source='declared',requirement='later',unit='V',min='3.2 V',max='3.4 V'), 'limit':dict(source='declared',requirement='later',unit='V',nominal='3.5 V')},
                assertions=[dict(id='rail',requirement='later',lhs='v',op='<=',rhs='limit',unit='V')],
                requirement_sources={'later':dict(path='requirements.md',locator='line1',sha256=digest(directory/'requirements.md'))})
            write_json(directory/'assertions.json',source);record['files']['assertions.json']=digest(directory/'assertions.json')
            record['check_set'][1]=dict(id='old-later',kind='interval_assertions',params=dict(assertions='assertions.json'))
            write_json(directory/'block-contract.json',block_contract(record));record['files']['block-contract.json']=digest(directory/'block-contract.json');write_json(directory/'block.json',record)
            current=copy.deepcopy(source)
            for parameter in current['parameters'].values():
                parameter['requirement']='filter-later'
            current['assertions'][0]['requirement']='filter-later'
            current['requirement_sources']={'filter-later':dict(path='blocks/rc/block.json',locator='requirements/later',sha256=digest(directory/'block.json'))}
            write_json(root/'fresh-assertions.json',current)
            scope['checks'][2]=dict(id='later',kind='interval_assertions',params=dict(assertions='fresh-assertions.json'))
            result=instantiate(root,scope,request)
            self.assertEqual(next(r for r in result['baseline']['requirements'] if r['id']=='filter-later')['intent'],'acceptance')
            from agent_ee.block_adaptation import capability
            files={str(p.relative_to(root)):digest(p) for p in root.rglob('*') if p.is_file()}
            current['assertions'][0]['rhs']='9 V';write_json(root/'fresh-assertions.json',current);files['fresh-assertions.json']=digest(root/'fresh-assertions.json')
            with self.assertRaisesRegex(ValueError,'asset changes'):capability(dict(instance='instance.json',netlist_check='netlist'),result,files,root,'provenance')

    def test_fresh_membership_contract_replays_scoped_and_legacy_whole_inventory(self):
        from tests.test_parts_subset import captured_fixture
        from tests.test_native_binding import native_graph,GEN
        from agent_ee.block_adaptation import _fresh_contract
        for scoped in (True,False):
            with self.subTest(scoped=scoped),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);document=captured_fixture(self,root);(root/'block').mkdir()
                tree=parse_quoted(GEN.schematic().encode())
                for node in list(children(tree,'symbol')):
                    if next(p[2] for p in children(node,'property') if p[1]=='Reference') != 'R1':
                        tree.remove(node)
                (root/'binding.kicad_sch').write_bytes(dump(tree))
                write_json(root/'block/parts.json',document)
                original_params=dict(manifest='parts.json',netlist_check='old-netlist',requirement_id='members')
                params=dict(manifest='fresh-parts.json',netlist_check='netlist',requirement_id='filter-members')
                if scoped:
                    selected=[dict(sheet=c['sheet'],reference=r,uuids=sorted(c['uuid'].split())) for r,c in sorted(native_graph()['components'].items()) if r=='R1']
                    original_params['reference_set']=selected;params['reference_set']=copy.deepcopy(selected)
                source=dict(files={'parts.json':digest(root/'block/parts.json')},requirements=[dict(id='members',statement='Exact allocated parts',checks=['old-parts'])],boundary_ports=[],
                    check_set=[dict(id='old-parts',kind='parts_subset',params=original_params)])
                write_json(root/'block/block.json',source)
                document['declaration_source']=dict(path='block/block.json',locator='requirements/members',sha256=digest(root/'block/block.json'))
                write_json(root/'fresh-parts.json',document)
                record=dict(id='filter',block='block',sheet='binding.kicad_sch',instance_path='/',bindings={},requirements={'members':['new-parts']})
                scope=dict(schema='agent-ee.scope/3',checks=[dict(id='netlist',kind='netlist'),dict(id='new-parts',kind='parts_subset',params=params)],
                    baseline=dict(requirements=[dict(id='filter-members',intent='acceptance',statement='Exact allocated parts',source=dict(path='block/block.json',locator='requirements/members'),checks=['new-parts'])]))
                files={str(p.relative_to(root)):digest(p) for p in root.rglob('*') if p.is_file()}
                _fresh_contract(root,source,record,scope,files,'netlist')
                self.assertEqual('reference_set' in params,scoped)
                document['parts'][0]['mpn']='FORGED';write_json(root/'fresh-parts.json',document);files['fresh-parts.json']=digest(root/'fresh-parts.json')
                with self.assertRaisesRegex(ValueError,'allocation/identity'):_fresh_contract(root,source,record,scope,files,'netlist')

    def test_differing_provided_package_is_not_a_legacy_omission(self):
        from tests.test_acquisition_alternative import authored_fixture
        from agent_ee.block_adaptation import _fresh_contract
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);authored_fixture(root);(root/'block').mkdir()
            document=read_json(root/'parts.json')
            self.assertTrue(all('package' in p for p in document['parts']))
            write_json(root/'block/parts.json',document)
            source=dict(files={'parts.json':digest(root/'block/parts.json')},boundary_ports=[],
                requirements=[dict(id='members',statement='Preserve exact purchasing identity',checks=['old-parts'])],
                check_set=[dict(id='old-parts',kind='parts_subset',params=dict(manifest='parts.json',netlist_check='old-netlist',requirement_id='members',reference_set=[]))])
            write_json(root/'block/block.json',source)
            document['parts'][0]['package']='DIFFERENT-PROVIDED-PACKAGE'
            write_json(root/'fresh-parts.json',document)
            record=dict(id='filter',block='block',sheet='not-evaluated.kicad_sch',instance_path='/',bindings={},requirements={'members':['new-parts']})
            scope=dict(checks=[dict(id='netlist',kind='netlist'),dict(id='new-parts',kind='parts_subset',params=dict(manifest='fresh-parts.json',netlist_check='netlist',requirement_id='filter-members',reference_set=[]))])
            files={str(p.relative_to(root)):digest(p) for p in root.rglob('*') if p.is_file()}
            with self.assertRaisesRegex(ValueError,'allocation/identity'):_fresh_contract(root,source,record,scope,files,'netlist')


    def test_reference_renumbering_cannot_change_purchasing_or_carrier_identity(self):
        from agent_ee.block_adaptation import _part_allocations,_rebound
        part=dict(id='U1',role='U1',required=True,lib_id='Owned:Device',footprint='Owned:Device',lcsc='C1',
            mpn='U1',manufacturer='U1',package='U1',instances=[dict(sheet='/',reference='U1')])
        names={'U1':'U11','/':'/00000000-0000-4000-8000-000000000001/','Owned:Device':'Block_61:Device'}
        row=_part_allocations(dict(parts=[part]),names)[0]
        self.assertEqual({k:row[k] for k in ('id','role','mpn','manufacturer','package','footprint')},
                         {k:part[k] for k in ('id','role','mpn','manufacturer','package','footprint')})
        self.assertEqual(row['instances'],[dict(sheet=names['/'],reference='U11')])
        self.assertEqual(row['lib_id'],'Block_61:Device')
        literals=dict(value='U1',equals='U1',subcircuit='U1',pin_map={'1':'U1'})
        self.assertEqual(_rebound(literals,names,{},{}),literals)

    def test_deferred_active_spice_keeps_literal_contract_and_captured_models(self):
        from agent_ee.block_adaptation import capability
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);scope,request,record=self.prepared(root);directory=root/'blocks/rc'
            (directory/'owned.lib').write_text('.subckt r_two P N\nR_effective P N 10k\n.ends r_two\n')
            record['files']['owned.lib']=digest(directory/'owned.lib')
            base=read_json(REPO/'fixtures/active-spice/control.scope.json')['checks'][1]['params']
            params=copy.deepcopy(base)
            params.update(netlist_check='netlist',model_files=['owned.lib'],ground_net='/GND',
                active_parts={'R1':dict(model_file='owned.lib',subcircuit='r_two',pin_map={'1':'P','2':'N'})})
            params['sources']=[copy.deepcopy(params['sources'][0])]
            params['analyses']=params['analyses'][:1]
            for measurement in params['analyses'][0]['measurements']:
                measurement['positive_net']='/OUT';measurement['negative_net']='/GND'
            record['check_set'][1]=dict(id='old-later',kind='active_spice',params=params)
            write_json(directory/'block-contract.json',block_contract(record));record['files']['block-contract.json']=digest(directory/'block-contract.json');write_json(directory/'block.json',record)
            current=copy.deepcopy(params);current['model_files']=['blocks/rc/owned.lib'];current['active_parts']['R1']['model_file']='blocks/rc/owned.lib'
            scope['checks'][2]=dict(id='later',kind='active_spice',params=current)
            result=instantiate(root,scope,request)
            self.assertEqual(result['checks'][2]['params']['contract'],'active-spice/1')
            files={str(p.relative_to(root)):digest(p) for p in root.rglob('*') if p.is_file()}
            for mutation in ('token','model','bounds'):
                changed=copy.deepcopy(result);actual=changed['checks'][2]['params']
                if mutation=='token':actual['contract']='active-spice/2'
                elif mutation=='model':actual['model_files']=['other.lib']
                else:actual['analyses'][0]['measurements'][0]['max']=999
                with self.subTest(mutation=mutation),self.assertRaises(ValueError):capability(dict(instance='instance.json',netlist_check='netlist'),changed,files,root,'provenance')
            (directory/'owned.lib').write_text('.subckt r_two P N\nR_effective P N 100\n.ends r_two\n')
            with self.assertRaisesRegex(ValueError,'inventory/hash'):capability(dict(instance='instance.json',netlist_check='netlist'),result,files,root,'provenance')


class SingleSourceOccurrenceTests(unittest.TestCase):
    """Authored A-to-B source contracts, not native exports or role qualification."""
    def test_nonroot_source_occurrence_rebinds_interface_and_scoped_parts(self):
        from tests.test_parts_subset import captured_fixture
        from tests.test_native_binding import GEN
        from agent_ee.block_adaptation import _source, _fresh_contract
        from agent_ee.reference_blocks import sheet_symbols
        from agent_ee.schematic_emit import Quoted
        source_path = '/11111111-1111-4111-8111-111111111111/'
        target_path = '/22222222-2222-4222-8222-222222222222/'
        foreign_path = '/33333333-3333-4333-8333-333333333333/'
        target_unit = '44444444-4444-4444-8444-444444444444'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parts = captured_fixture(self, root)
            directory = root / 'block'; directory.mkdir()
            tree = parse_quoted(GEN.schematic().encode())
            for node in list(children(tree, 'symbol')):
                if next(p[2] for p in children(node, 'property') if p[1] == 'Reference') != 'R1':
                    tree.remove(node)
            resistor, = children(tree, 'symbol')
            def occurrence(node, path):
                for instances in children(node, 'instances'):
                    for project in children(instances, 'project'):
                        for entry in children(project, 'path'):
                            entry[1] = Quoted('/' + GEN.uid('root') + path)
            occurrence(resistor, source_path)
            for name in ('P', 'N'):
                tree.append(parse_quoted(('(hierarchical_label "' + name + '" (shape passive) '
                    '(at 20 20 0) (effects (font (size 1 1))) (uuid "' + GEN.uid(name) + '"))').encode()))
            (directory / 'source.kicad_sch').write_bytes(dump(tree))
            parts['parts'][0]['instances'] = [dict(sheet=source_path, reference='R1')]
            write_json(directory / 'parts.json', parts)
            source_unit = sheet_symbols(dump(tree), source_path)['R1']['uuids']
            interface = dict(schema='agent-ee.interface/1', allowed_edits=[],
                components={'resistor': dict(locations=[dict(sheet=source_path, reference='R1')], symbols=['Owned:R'])},
                terminals={'p': dict(component='resistor', pin='1')},
                invariants=[dict(id='value', kind='value', component='resistor', equals='10k', requirement='interface',
                    source=dict(path='requirements.md', locator='line 1')),
                    dict(id='net', kind='named_net', terminal='p', names=['/P'], requirement='interface',
                    source=dict(path='requirements.md', locator='line 1'))])
            write_json(directory / 'interface.json', interface)
            (directory / 'requirements.md').write_text('Owned source: retain R1 identity/value and bind P explicitly.\n')
            source = dict(schema='agent-ee.block/1', id='owned-single', version='nonroot-1', role='test',
                source=dict(repository='owned', commit='a' * 40, paths={'source.kicad_sch': 'source.kicad_sch'},
                    license='Owned', upstream_origin='authored', upstream_license='Owned'),
                sheet='source.kicad_sch', parts_manifest='parts.json', interface='interface.json',
                instances=[dict(path='/source/', uuid_path=source_path, sheet='source.kicad_sch', references=['R1'])],
                boundary_ports=[dict(name=n, net_name='/' + n, direction='passive', voltage_domain='test', current_a=None) for n in ('P', 'N')],
                requirements=[dict(id=n, statement='Retain declared ' + n, checks=['old-' + n],
                    source=dict(path='requirements.md', locator='line 1')) for n in ('interface', 'members')],
                check_set=[dict(id='old-interface', kind='hardware_interface', params=dict(contract='interface.json', netlist_check='old-netlist')),
                    dict(id='old-members', kind='parts_subset', params=dict(manifest='parts.json', netlist_check='old-netlist',
                        requirement_id='members', reference_set=[dict(sheet=source_path, reference='R1', uuids=source_unit)]))],
                files={p: digest(directory / p) for p in ('source.kicad_sch', 'parts.json', 'interface.json', 'requirements.md')}, runs=[])
            write_json(directory / 'block-contract.json', block_contract(source))
            source['files']['block-contract.json'] = digest(directory / 'block-contract.json')
            write_json(directory / 'block.json', source)
            admitted = _source(root, 'block')  # A is an admitted explicit single occurrence, not a composition.
            self.assertEqual(admitted['instances'][0]['uuid_path'], source_path)
            occurrence(resistor, target_path)
            children(resistor, 'uuid')[0][1] = Quoted(target_unit)
            (root / 'target.kicad_sch').write_bytes(dump(tree))
            fresh = copy.deepcopy(interface)
            fresh['components']['resistor']['locations'][0]['sheet'] = target_path
            for invariant in fresh['invariants']:
                invariant['requirement'] = 'leaf-interface'
                invariant['source'] = dict(path='block/block.json', locator='requirements/interface')
            fresh['invariants'][1]['names'] = ['/BOARD_P']
            write_json(root / 'fresh-interface.json', fresh)
            allocated = copy.deepcopy(parts)
            allocated['parts'][0]['instances'][0]['sheet'] = target_path
            allocated['declaration_source'] = dict(path='block/block.json', locator='requirements/members', sha256=digest(directory / 'block.json'))
            write_json(root / 'fresh-parts.json', allocated)
            selected = [dict(sheet=target_path, reference='R1', uuids=[target_unit])]
            checks = [dict(id='netlist', kind='netlist'),
                dict(id='interface', kind='hardware_interface', params=dict(contract='fresh-interface.json', netlist_check='netlist')),
                dict(id='members', kind='parts_subset', params=dict(manifest='fresh-parts.json', netlist_check='netlist', requirement_id='leaf-members', reference_set=selected))]
            scope = dict(schema='agent-ee.scope/3', schematic='target.kicad_sch', checks=checks,
                baseline=dict(requirements=[dict(id='leaf-' + n, intent='acceptance', checks=[n],
                    source=dict(path='block/block.json', locator='requirements/' + n)) for n in ('interface', 'members')]))
            record = dict(id='leaf', block='block', sheet='target.kicad_sch', instance_path=target_path,
                bindings={'P': '/BOARD_P', 'N': '/BOARD_N'}, requirements={n: [n] for n in ('interface', 'members')})
            files = {str(p.relative_to(root)): digest(p) for p in root.rglob('*') if p.is_file()}
            _fresh_contract(root, admitted, record, scope, files, 'netlist')
            for change in ('interface-retained-A', 'interface-foreign', 'allocation-retained-A', 'allocation-foreign',
                           'selector-occurrence', 'selector-unit', 'selector-reference', 'mpn', 'criteria'):
                altered_interface, altered_parts, altered_scope = copy.deepcopy((fresh, allocated, scope))
                if change.startswith('interface-'):
                    altered_interface['components']['resistor']['locations'][0]['sheet'] = source_path if change.endswith('A') else foreign_path
                elif change.startswith('allocation-'):
                    altered_parts['parts'][0]['instances'][0]['sheet'] = source_path if change.endswith('A') else foreign_path
                elif change.startswith('selector-'):
                    selector = altered_scope['checks'][2]['params']['reference_set'][0]
                    if change == 'selector-occurrence': selector['sheet'] = foreign_path
                    elif change == 'selector-unit': selector['uuids'] = source_unit
                    else: selector['reference'] = 'R9'
                elif change == 'mpn': altered_parts['parts'][0]['mpn'] = 'FORGED'
                else: altered_interface['invariants'][0]['equals'] = '60'
                write_json(root / 'fresh-interface.json', altered_interface)
                write_json(root / 'fresh-parts.json', altered_parts)
                altered_files = {**files, 'fresh-interface.json': digest(root / 'fresh-interface.json'), 'fresh-parts.json': digest(root / 'fresh-parts.json')}
                with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'asset changes|allocation/identity|parameters differ'):
                    _fresh_contract(root, admitted, record, altered_scope, altered_files, 'netlist')
