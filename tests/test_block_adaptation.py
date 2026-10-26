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
        from agent_ee.executor import execute
        # Inject only the executable command. Production execute still launches
        # and records the failing process inside its networkless sandbox.
        commands = {
            'crash': ['/bin/false'],
            'missing': ['/bin/true'],
            'malformed': ['/usr/bin/python3', '-c', "open('/work/design.xml','w').write('<bad>')"],
        }
        for label, argv in commands.items():
            def fault(inputs, work, command, **kwargs):
                return execute(inputs, work, argv, **kwargs)
            with self.subTest(case=label), patch('agent_ee.executor.execute', side_effect=fault):
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
