"""Source-bound native qualification on the unchanged owned IMU."""
import hashlib
import tempfile
import unittest
from pathlib import Path

from schematic_decompile_fixture import FIXTURE, MANIFEST, CAPTURES
from agent_ee.schematic_decompile import decompile
from agent_ee.schematic_emitter import emit_schematic

LAYOUT = dict(schema='agent-ee.schematic-layout/1', namespace='3d5e7a52-9c1b-5f0e-8d2a-7b6c4e1f0a93',
              project='imu', transforms={})


class SeamTests(unittest.TestCase):
    def test_original_imu_emits_without_snapping(self):
        source = (FIXTURE / 'imu.kicad_sch').read_bytes()
        extracted = decompile(source, MANIFEST, CAPTURES)
        result, files = emit_schematic(extracted['intent'], CAPTURES, LAYOUT,
                                       expected_resolved=extracted['resolved'])
        self.assertEqual(result['execution'], 'ok', result['findings'])
        self.assertIn('imu.kicad_sch', files)
        self.assertEqual((result, files), emit_schematic(extracted['intent'], CAPTURES, LAYOUT,
                         expected_resolved=extracted['resolved']))


class NativeRoundtripTests(unittest.TestCase):
    def setUp(self):
        from toolchain import require_toolchain
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-42-roundtrip-')
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / 'qualification'

    def test_original_imu_complete_source_bound_roundtrip(self):
        from agent_ee.schematic_roundtrip import roundtrip
        source = (FIXTURE / 'imu.kicad_sch').read_bytes()
        report = roundtrip(source, MANIFEST, CAPTURES, LAYOUT, self.out)
        self.assertEqual((report['execution'], report['outcome']), ('ok', 'pass'), report)
        self.assertEqual(hashlib.sha256(source).hexdigest(),
                         '33d4d3967e4b556a93b193775e7e094a3e70cde3a12771a2db83a72923935aa4')
        self.assertEqual(hashlib.sha256(CAPTURES['Owned.kicad_sym']).hexdigest(),
                         '0320bcc9abd66d5935c27382719d33e4a3bff76b6da6a72bb76e2eefa18f3df0')
        self.assertEqual(report['source_sha256'], hashlib.sha256(source).hexdigest())
        self.assertEqual(report['comparisons']['native_topology']['scores']['jaccard'], 1.0)
        self.assertEqual(report['inventory'], dict(components=3, pins=10, nets=4))
        self.assertEqual(len(report['identity_mapping']), 3)
        self.assertEqual(decompile(source, MANIFEST, CAPTURES)['report']['roundtrip'], 'not_run')
        self.assertTrue((self.out / 'report.json').is_file())
        again = roundtrip(source, MANIFEST, CAPTURES, LAYOUT, self.out.parent / 'again')
        for key in ('source_sha256', 'layout_sha256', 'libraries', 'inventory', 'identity_mapping', 'comparisons'):
            self.assertEqual(report[key], again[key], key)
        self.assertEqual((self.out/'emitted/imu.kicad_sch').read_bytes(),
                         (self.out.parent/'again/emitted/imu.kicad_sch').read_bytes())
        with self.assertRaises(FileExistsError):
            roundtrip(source, MANIFEST, CAPTURES, LAYOUT, self.out)

    def test_native_mutants_preserve_topology_and_identity_boundaries(self):
        import re
        from agent_ee.schematic_roundtrip import roundtrip, qualify
        from agent_ee.executor import execute
        from agent_ee.native_netlist import read_native_inventory
        from agent_ee.netlist_parity import resolved_inventory
        from agent_ee.schematic_emitter import publish
        source = (FIXTURE / 'imu.kicad_sch').read_bytes()
        baseline = roundtrip(source, MANIFEST, CAPTURES, LAYOUT, self.out)
        self.assertEqual(baseline['outcome'], 'pass', baseline)
        extracted = decompile(source, MANIFEST, CAPTURES)
        expected = resolved_inventory(extracted['resolved'])
        src = read_native_inventory(self.out / 'source-export/design.xml')
        anchors = baseline['identity_mapping']
        source_anchors = {cid: a['source'] for cid, a in anchors.items()}
        emitted_anchors = {cid: a['emitted'] for cid, a in anchors.items()}
        _, files = emit_schematic(extracted['intent'], CAPTURES, LAYOUT)
        text = files['imu.kicad_sch'].decode()
        labels = list(re.finditer(r'\t\(label "([^"]+)"\n.*?\n\t\)\n', text, re.S))
        self.assertEqual(len(labels), 10)
        first = labels[0]
        other = next(m for m in labels if m.group(1) != first.group(1))
        swap = text[:other.start()] + other.group().replace(other.group(1), first.group(1), 1) + text[other.end():]
        swap = swap[:first.start()] + first.group().replace(first.group(1), other.group(1), 1) + swap[first.end():]
        uuid = next(iter(emitted_anchors.values()))['uuid']
        variants = dict(
            omitted=text[:first.start()] + text[first.end():],
            short=text[:other.start()] + other.group().replace(other.group(1), first.group(1), 1) + text[other.end():],
            swapped=swap,
            uuid=text.replace(uuid, '11111111-2222-5333-8444-555555555555'))
        import copy
        extra = copy.deepcopy(extracted['intent'])
        cap = next(c for c in extra['components'] if c['reference'] == 'C1')
        extra['components'].append(dict(cap, id='extra_cap', reference='C9'))
        for net in extra['nets']:
            net['terminals'] += [dict(t, component='extra_cap') for t in list(net['terminals'])
                                 if t['component'] == cap['id']]
        extra_receipt, extra_files = emit_schematic(extra, CAPTURES, LAYOUT)
        self.assertEqual(extra_receipt['execution'], 'ok', extra_receipt)
        variants['extra_component'] = extra_files['imu.kicad_sch'].decode()
        for label, mutated in variants.items():
            with self.subTest(label=label):
                candidate = self.out.parent / label
                publish({**files, 'imu.kicad_sch': mutated.encode()}, candidate)
                work = self.out.parent / (label + '-export')
                execution = execute(candidate, work, ['kicad-cli', 'sch', 'export', 'netlist', '--format',
                    'kicadxml', '-o', '/work/design.xml', '/candidate/imu.kicad_sch'])
                self.assertEqual((execution['execution'], execution['exit_code']), ('ok', 0), execution)
                actual = read_native_inventory(work / 'design.xml')
                reports = qualify(expected, src, actual, source_anchors, emitted_anchors)
                if label == 'uuid':
                    self.assertEqual(reports['emitted_topology']['outcome'], 'pass')
                    self.assertNotEqual(reports['emitted_instance']['outcome'], 'pass')
                elif label == 'extra_component':
                    self.assertEqual(reports['emitted_components']['outcome'], 'defect')
                else:
                    self.assertEqual(reports['emitted_topology']['outcome'], 'defect')

    def test_off_editor_grid_transforms_have_exact_native_membership(self):
        from agent_ee.schematic_roundtrip import roundtrip
        from agent_ee.symbol_library import children, parse_sexpr
        from decimal import Decimal
        extracted = decompile((FIXTURE / 'imu.kicad_sch').read_bytes(), MANIFEST, CAPTURES)
        sensor = next(c['id'] for c in extracted['intent']['components'] if c['reference'] == 'U1')
        offsets = {0: [(0,0),(-2,2),(1,4),(3,6)], 90: [(0,0),(2,2),(4,-1),(6,-3)],
                   180: [(0,0),(2,-2),(-1,-4),(-3,-6)], 270: [(0,0),(-2,-2),(-4,1),(-6,3)]}
        for rotation in (0, 90, 180, 270):
            for mirror in (None, 'x', 'y'):
                with self.subTest(rotation=rotation, mirror=mirror):
                    layout = dict(LAYOUT, transforms={sensor: dict(rotation=rotation, mirror=mirror)})
                    out = self.out.parent / f'transform-{rotation}-{mirror}'
                    report = roundtrip((FIXTURE / 'imu.kicad_sch').read_bytes(), MANIFEST, CAPTURES, layout, out)
                    self.assertEqual(report['outcome'], 'pass', report)
                    root = parse_sexpr((out / 'emitted/imu.kicad_sch').read_bytes())
                    symbol = next(s for s in children(root, 'symbol') if children(s, 'property')[0][2] == 'U1')
                    anchor = children(symbol, 'at')[0][1:3]
                    x, y = map(Decimal, anchor)
                    sx, sy = (-1 if mirror == 'y' else 1), (-1 if mirror == 'x' else 1)
                    points = {(x + sx*dx, y + sy*dy) for dx,dy in offsets[rotation]}
                    label_points = {tuple(map(Decimal, children(n, 'at')[0][1:3])) for n in children(root, 'label')}
                    self.assertTrue(points <= label_points, (points, label_points))
                    self.assertTrue(report['erc']['violations'])

    def test_failed_missing_and_malformed_native_exports_retain_execution(self):
        import json
        from dataclasses import replace
        from agent_ee import native_run
        from agent_ee.schematic_roundtrip import roundtrip
        # Only the tool boundary is fault-injected; each failure is actually
        # executed and recorded by the production networkless executor.
        commands = dict(failed=['/bin/false'], missing=['/does-not-exist'],
                        malformed=['/usr/bin/python3', '-c', "open('/work/design.xml','w').write('<bad>')"],
                        absent=['/bin/true'])
        for label, argv in commands.items():
            def execute_fault(call):
                result = native_run.run(
                    call.intent,
                    inputs=call.inputs,
                    work=call.work,
                    mounts=call.mounts,
                    adapter=native_run.Sandbox(),
                )
                return native_run.Outcome(execution=result['execution'], exit_code=result['exit_code'],
                                          stdout=result.stdout, stderr=result.stderr)

            class FaultedRun(native_run.Recorded):
                def run(self, intent, **kwargs):
                    if 'netlist' in intent.argv:
                        intent = replace(intent, argv=argv)
                    return super().run(intent, **kwargs)

            with self.subTest(label=label), native_run.using(FaultedRun(execute_fault)):
                out = self.out.parent / label
                report = roundtrip((FIXTURE / 'imu.kicad_sch').read_bytes(), MANIFEST, CAPTURES, LAYOUT, out)
                self.assertEqual((report['execution'], report['outcome']), ('failed', 'unknown'), report)
                self.assertEqual(report['comparisons'], {})
                receipt = json.loads((out / 'source-export/execution.json').read_text())
                self.assertEqual(receipt['argv'], argv)
                self.assertEqual(report['coverage'], 'missing')

    def test_cancelled_export_propagates_with_unknown_report(self):
        import json
        from agent_ee.executor import Cancellation
        from agent_ee.schematic_roundtrip import roundtrip
        cancellation = Cancellation()
        cancellation.cancel()
        with self.assertRaises(KeyboardInterrupt):
            roundtrip((FIXTURE / 'imu.kicad_sch').read_bytes(), MANIFEST, CAPTURES, LAYOUT,
                      self.out, cancellation=cancellation)
        report = json.loads((self.out / 'report.json').read_text())
        self.assertEqual(report['outcome'], 'unknown')
        receipt = json.loads((self.out / 'version/execution.json').read_text())
        self.assertEqual(receipt['execution'], 'not_run')

    def test_native_100nm_offset_is_exact_under_every_transform(self):
        from decimal import Decimal
        from agent_ee.connectivity_ir import resolve_intent
        from agent_ee.executor import execute
        from agent_ee.native_netlist import read_native_inventory
        from agent_ee.netlist_parity import compare, resolved_inventory, validate_policy
        from agent_ee.schematic_emitter import publish
        from agent_ee.symbol_library import children, parse_sexpr
        import copy
        extracted = decompile((FIXTURE / 'imu.kicad_sch').read_bytes(), MANIFEST, CAPTURES)
        intent = copy.deepcopy(extracted['intent'])
        sensor = next(c['id'] for c in intent['components'] if c['reference'] == 'U1')
        lib = CAPTURES['Owned.kicad_sym'].replace(b'(at -2 -2 0)', b'(at -2.0001 -2 0)', 1)
        self.assertNotEqual(lib, CAPTURES['Owned.kicad_sym'])
        captures = {'Owned.kicad_sym': lib}
        intent['libraries'][0]['sha256'] = hashlib.sha256(lib).hexdigest()
        expected = resolved_inventory(resolve_intent(intent, captures))
        offsets = {0: ('-2.0001','2'), 90: ('2','2.0001'),
                   180: ('2.0001','-2'), 270: ('-2','-2.0001')}
        for rotation in (0,90,180,270):
            for mirror in (None,'x','y'):
                with self.subTest(rotation=rotation, mirror=mirror):
                    layout = dict(LAYOUT, transforms={sensor: dict(rotation=rotation, mirror=mirror)})
                    receipt, files = emit_schematic(intent, captures, layout)
                    self.assertEqual(receipt['execution'], 'ok', receipt)
                    root = parse_sexpr(files['imu.kicad_sch'])
                    node = next(n for n in children(root, 'symbol') if children(n, 'property')[0][2] == 'U1')
                    x,y = map(Decimal, children(node, 'at')[0][1:3])
                    dx,dy = map(Decimal, offsets[rotation])
                    endpoint = (x + (-dx if mirror == 'y' else dx), y + (-dy if mirror == 'x' else dy))
                    self.assertIn(endpoint, {tuple(map(Decimal,children(n,'at')[0][1:3])) for n in children(root,'label')})
                    candidate = self.out.parent / f'lattice-{rotation}-{mirror}'
                    publish(files, candidate)
                    work = candidate.with_name(candidate.name + '-export')
                    execution = execute(candidate, work, ['kicad-cli','sch','export','netlist','--format','kicadxml',
                        '-o','/work/design.xml','/candidate/imu.kicad_sch'])
                    self.assertEqual(execution['execution'], 'ok', execution)
                    actual = read_native_inventory(work / 'design.xml')
                    policy = validate_policy(dict(source=dict(kind='intent',path='intent.json'), netlist_check='native',
                                                  predicate='topology', multiset='exactly'))
                    self.assertEqual(compare(expected,actual,policy)['outcome'], 'pass')


class RefusalTests(unittest.TestCase):
    def test_changed_library_unsupported_source_and_output_links(self):
        from agent_ee.schematic_roundtrip import roundtrip
        source = (FIXTURE / 'imu.kicad_sch').read_bytes()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report = roundtrip(source, MANIFEST, {'Owned.kicad_sym': CAPTURES['Owned.kicad_sym'] + b' '}, LAYOUT, root/'hash')
            self.assertEqual(report['execution'], 'unsupported')
            self.assertEqual(report['comparisons'], {})
            report = roundtrip(source.replace(b'(kicad_sch ', b'(kicad_sch (bus) ', 1), MANIFEST, CAPTURES, LAYOUT, root/'bus')
            self.assertEqual(report['execution'], 'unsupported')
            self.assertFalse((root/'bus/emitted').exists())
            link = root/'link'
            link.symlink_to(root, target_is_directory=True)
            with self.assertRaises(ValueError):
                roundtrip(source, MANIFEST, CAPTURES, LAYOUT, link/'write')
            with self.assertRaises(ValueError):
                roundtrip(source, MANIFEST, CAPTURES, LAYOUT, root/'timeout', timeout=float('inf'))

    def test_emitter_name_refusal_never_renames_for_roundtrip(self):
        from schematic_decompile_fixture import control
        from agent_ee.schematic_roundtrip import roundtrip
        source = control([(10,10),(30,10)], labels=[('A/B',(10,10)),('A{slash}B',(30,10))])
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)/'qualification'
            report = roundtrip(source, MANIFEST, CAPTURES, LAYOUT, out)
            self.assertEqual((report['execution'], report['outcome']), ('failed', 'unknown'))
            self.assertEqual(report['comparisons'], {})
            self.assertTrue((out/'emission.json').is_file())
            self.assertFalse((out/'emitted/imu.kicad_sch').exists())


class NativeGridRegressionTests(unittest.TestCase):
    def test_7point6mm_is_native_representable_but_sub100nm_is_refused(self):
        from toolchain import require_toolchain
        require_toolchain(native=True)
        from test_schematic_emitter import captures, load, native_project, run_native
        from agent_ee.connectivity_ir import resolve_intent
        from agent_ee.native_netlist import read_native_inventory
        from agent_ee.netlist_parity import compare, resolved_inventory, validate_policy
        intent = load('regulator-core.intent.json')
        captured = captures(intent)
        path = 'lib/Owned_Gen.kicad_sym'
        old = b'(pin power_out line\n\t\t\t\t(at 7.62 0 180)'
        self.assertEqual(captured[path].count(old), 1)
        original = captured[path]
        for offset, expected_nm in ((b'7.6', 7600000), (b'7.600001', 7600001)):
            captured[path] = original.replace(old, old.replace(b'7.62', offset), 1)
            intent['libraries'][0]['sha256'] = hashlib.sha256(captured[path]).hexdigest()
            resolved = resolve_intent(intent, captured)
            regulator = next(c for c in resolved['components'] if c['id'] == 'reg')
            self.assertEqual(next(p['x_nm'] for p in regulator['pins'] if p['number'] == '3'), expected_nm)
            result, files = emit_schematic(intent, captured, load('regulator-core.layout.json'))
            if offset == b'7.600001':
                self.assertIsNone(files)
                self.assertIn('unlocatable_pin', [f['code'] for f in result['findings']])
                continue
            self.assertEqual(result['execution'], 'ok', result)
            with tempfile.TemporaryDirectory() as temp:
                project = native_project(files, Path(temp)/'project')
                work = Path(temp)/'export'
                run_native(project, work, 'kicad-cli','sch','export','netlist','--format','kicadxml',
                           '-o','/work/design.xml','/candidate/regulator_core.kicad_sch')
                expected = resolved_inventory(resolved)
                actual = read_native_inventory(work/'design.xml')
                policy = validate_policy(dict(source=dict(kind='intent',path='intent.json'), netlist_check='native',
                                              predicate='topology',multiset='exactly'))
                report = compare(expected, actual, policy)
                self.assertEqual(report['outcome'], 'pass', report)
