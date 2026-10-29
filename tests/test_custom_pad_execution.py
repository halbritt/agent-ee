"""CPU-only controls for nested custom-pad supervision; no native facts asserted."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_ee import footprint_probe, native_run
from agent_ee.footprint_library import index_footprints
from tests.test_footprint_library import capture, fixture, smd, symbol_capture, declarations


def mount_view(extra=()):
    entries = [('/', 'rw', 'tmpfs'), ('/usr', 'ro', 'ext4'), ('/etc', 'ro', 'ext4'),
               ('/candidate', 'ro', 'ext4'), ('/app', 'ro', 'ext4'), ('/work', 'rw', 'ext4'),
               ('/proc', 'rw', 'proc'), ('/dev', 'rw', 'tmpfs'), ('/tmp', 'rw', 'tmpfs')]
    return '\n'.join(f'{i} 0 0:1 / {path} {flags} - {fs} fixture rw'
                     for i, (path, flags, fs) in enumerate([*entries, *extra], 1))


TEXT = fixture('OwnedCustom', [smd('1', 0, shape='custom', extra='(zone_connect 2) '
    '(options (clearance outline) (anchor rect)) '
    '(primitives (gr_rect (start -1 -1) (end 2 1) (width 0) (fill yes))) '), smd('2', 4)])
# Authored controller facts, never native evidence. Production validates these as usual.
REPORT = dict(pcbnew_version='CPU_MOCK_ONLY', pads=[
    dict(number='1', type='smd', shape='custom', x_nm=0, y_nm=0,
         orientation_microdegrees=0, size_nm=[1000000, 1000000], anchor='rect',
         layer_outline_counts=[1], outline_count=1, bbox_nm=[-1000000, -1000000, 2000000, 1000000]),
    dict(number='2', type='smd', shape='rect', x_nm=4000000, y_nm=0,
         orientation_microdegrees=0, size_nm=[1000000, 1000000], bbox_nm=[3500000, -500000, 4500000, 500000])])


class EnclosingViewTests(unittest.TestCase):
    def verify(self, view):
        with patch.object(footprint_probe, '__file__', '/app/agent_ee/footprint_probe.py'), \
                patch.object(Path, 'read_text', return_value=view):
            return footprint_probe._enclosing_sandbox()

    def test_fixed_view_and_readonly_native_evidence_allowed(self):
        self.assertTrue(self.verify(mount_view()))
        self.assertTrue(self.verify(mount_view([('/evidence/netlist/design.xml', 'ro', 'ext4')])))

    def test_ordinary_import_never_selects_supervised_child(self):
        with patch.object(Path, 'read_text', side_effect=AssertionError('must not inspect host mounts')):
            self.assertFalse(footprint_probe._enclosing_sandbox())

    def test_unknown_malformed_and_writable_views_refuse(self):
        for view in [mount_view().replace('/candidate ro', '/candidate rw'),
                     mount_view().replace('/work rw', '/work ro'),
                     mount_view([('/app/agent_ee', 'rw', 'ext4')]),
                     mount_view([('/evidence/netlist', 'rw', 'ext4')]),
                     mount_view([('/run/host', 'ro', 'ext4')]),
                     mount_view([('/tmp/host', 'rw', 'ext4')]),
                     mount_view().replace('/proc rw - proc', '/proc rw - ext4'),
                     mount_view() + '\n1 0 0:1 / /extra ro - ext4 x ro',
                     mount_view().replace('1 0 0:1', '01 0 0:1'),
                     mount_view().replace('/candidate ro', '/candidate ro,rw'),
                     mount_view().replace('/candidate ro', '/candidate/../candidate ro'), '', 'bad']:
            with self.subTest(view=view), self.assertRaisesRegex(ValueError, 'fixed executor sandbox'):
                self.verify(view)

    def test_private_placement_branch_requires_exact_readonly_mounts_and_source(self):
        valid = mount_view([('/placement-tool','ro','ext4'),('/placement-request','ro','ext4')])
        with patch.object(footprint_probe, '__file__', '/placement-tool/agent_ee/footprint_probe.py'), \
                patch.object(Path, 'read_text', return_value=valid):
            self.assertFalse(footprint_probe._enclosing_sandbox())
            self.assertTrue(footprint_probe._enclosing_sandbox(_placement=True))
        invalid = [mount_view(), mount_view([('/placement-tool','ro','ext4')]),
                   mount_view([('/placement-request','ro','ext4')]),
                   valid.replace('/placement-tool ro','/placement-tool rw'),
                   valid.replace('/placement-request ro','/placement-request rw'),
                   valid.replace('/app ro','/app rw'),
                   valid.replace('/candidate ro','/candidate rw'),
                   valid + '\n99 0 0:1 / /unknown ro - ext4 fixture ro']
        for view in invalid:
            with self.subTest(view=view), patch.object(footprint_probe, '__file__', '/placement-tool/agent_ee/footprint_probe.py'), \
                    patch.object(Path, 'read_text', return_value=view), self.assertRaisesRegex(ValueError, 'fixed executor sandbox'):
                footprint_probe._enclosing_sandbox(_placement=True)
        for source in ('/placement-request/agent_ee/footprint_probe.py', '/elsewhere/agent_ee/footprint_probe.py'):
            with self.subTest(source=source), patch.object(footprint_probe, '__file__', source), \
                    patch.object(Path, 'read_text', side_effect=AssertionError('ordinary import mount read')):
                self.assertFalse(footprint_probe._enclosing_sandbox(_placement=True))


class CustomPadExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.parent = Path(self.tmp.name) / 'library-probes'
        real_path = Path
        def paths(value):
            return self.parent if value == '/work/library-probes' else real_path(value)
        self.enterContext(patch.object(footprint_probe, 'Path', side_effect=paths))
        self.enterContext(patch.object(footprint_probe, '_enclosing_sandbox', return_value=True))
        self.calls = []

    def child(self, intent, *, work):
        self.calls.append(intent)
        self.assertEqual((intent.isolation, intent.timeout, intent.tool), ('host', 60, 'pcbnew_script'))
        self.assertEqual(intent.argv[:4], ('/usr/bin/python3', '-B', '-P', '-c'))
        self.assertEqual(intent.argv[4], 'from agent_ee.footprint_probe import _stage_main; import sys; _stage_main(*sys.argv[1:])')
        inputs = Path(intent.argv[5])
        self.assertEqual((inputs / 'OwnedCustom.kicad_mod').read_bytes(), TEXT.encode())
        self.assertEqual(intent.argv[6:], ('OwnedCustom', str(work / 'footprint.json')))
        self.assertEqual(intent.env['PYTHONPATH'], '/app')
        self.assertEqual(intent.env['HOME'], str(work / 'home'))
        self.assertEqual(intent.env['KICAD_CONFIG_HOME'], str(work / 'kicad-config'))
        self.assertEqual(intent.env['KICAD10_FOOTPRINT_DIR'], '/candidate/stock/footprints')
        work.mkdir()
        (work / 'console.log').write_text('CPU_MOCK_ONLY')
        (work / 'execution.json').write_text('{"execution":"ok","CPU_MOCK_ONLY":true}')
        (work / 'footprint.json').write_text(json.dumps(REPORT))
        return {'execution': 'ok'}

    def indexed(self):
        return index_footprints(*capture(TEXT, 'OwnedCustom'))

    def test_existing_index_selects_private_child_and_retains_exact_inputs_and_records(self):
        with patch.object(native_run, 'run', side_effect=self.child):
            result = self.indexed()
            second = self.indexed()
        self.assertEqual(result['coverage'], 'complete', result['findings'])
        self.assertEqual(result, second)
        self.assertEqual(result['footprints'][0]['electrical_numbers'], ['1', '2'])
        roots = list(self.parent.iterdir())
        self.assertEqual(len(roots), 2)
        for root in roots:
            self.assertEqual((root / 'inputs/OwnedCustom.kicad_mod').read_bytes(), TEXT.encode())
            self.assertEqual((root / 'inputs/OwnedCustom.kicad_mod').stat().st_mode & 0o777, 0o400)
            for name in ('execution.json', 'console.log', 'footprint.json'):
                self.assertTrue((root / 'work' / name).is_file())

    def test_failed_timeout_unavailable_missing_and_invalid_native_output_remain_unknown(self):
        for status in ('failed', 'timeout', 'unavailable', 'missing', 'invalid', 'null', 'list'):
            def child(intent, *, work):
                self.child(intent, work=work)
                if status == 'missing':
                    (work / 'footprint.json').unlink()
                elif status == 'invalid':
                    (work / 'footprint.json').write_text('bad JSON')
                elif status in ('null', 'list'):
                    (work / 'footprint.json').write_text('null' if status == 'null' else '[]')
                return {'execution': 'ok' if status in ('missing', 'invalid', 'null', 'list') else status}
            with self.subTest(status=status), patch.object(native_run, 'run', side_effect=child):
                result = self.indexed()
                self.assertEqual((result['execution'], result['outcome'], result['coverage']),
                                 ('failed', 'unknown', 'missing'))
                self.assertTrue(list(self.parent.glob('*/work/execution.json')))

    def test_changed_inputs_or_native_identity_and_disconnected_island_refuse(self):
        for change in ('input', 'number', 'outline'):
            def child(intent, *, work):
                result = self.child(intent, work=work)
                if change == 'input':
                    member = Path(intent.argv[5]) / 'OwnedCustom.kicad_mod'
                    member.chmod(0o600)
                    member.write_bytes(b'changed')
                else:
                    report = copy.deepcopy(REPORT)
                    report['pads'][0]['number' if change == 'number' else 'outline_count'] = '9' if change == 'number' else 2
                    (work / 'footprint.json').write_text(json.dumps(report))
                return result
            with self.subTest(change=change), patch.object(native_run, 'run', side_effect=child):
                result = self.indexed()
                self.assertEqual((result['execution'], result['outcome'], result['coverage']),
                                 ('failed', 'unknown', 'missing'))

    def test_unrecognized_outer_view_does_not_fall_back_or_launch(self):
        with patch.object(footprint_probe, '_enclosing_sandbox', side_effect=ValueError('unknown view')), \
                patch.object(native_run, 'run', side_effect=AssertionError('must not launch')):
            result = self.indexed()
        self.assertEqual((result['execution'], result['outcome']), ('failed', 'unknown'))

    def test_standalone_keeps_existing_sandbox_intent(self):
        def sandbox(intent, *, inputs, work):
            self.assertEqual(intent.isolation, 'sandbox')
            self.assertIsNone(intent.env)
            self.assertEqual(intent.argv, ('/usr/bin/python3', '-B', '-m', 'agent_ee.footprint_probe', 'OwnedCustom'))
            self.assertEqual((inputs / 'OwnedCustom.kicad_mod').read_bytes(), TEXT.encode())
            work.mkdir()
            (work / 'footprint.json').write_text(json.dumps(REPORT))
            return {'execution': 'ok'}
        with patch.object(footprint_probe, '_enclosing_sandbox', return_value=False), \
                patch.object(native_run, 'run', side_effect=sandbox):
            self.assertEqual(self.indexed()['coverage'], 'complete')
        self.assertFalse(self.parent.exists())

    def test_capture_hash_drift_refuses_before_probe(self):
        manifest, files = capture(TEXT, 'OwnedCustom')
        files[next(iter(files))] += b' drift'
        with patch.object(native_run, 'run', side_effect=AssertionError('must not launch')):
            result = index_footprints(manifest, files)
        self.assertEqual((result['execution'], result['outcome']), ('failed', 'unknown'))

    def test_assignment_cli_reaches_same_probe_without_new_trust_arguments(self):
        from agent_ee.footprint_library import main
        manifest, files = capture(TEXT, 'OwnedCustom')
        lib, data = symbol_capture(('1', '2'))
        files[lib['path']] = data
        root = Path(self.tmp.name)
        for name, data in files.items():
            path = root / name
            path.parent.mkdir(exist_ok=True, parents=True)
            path.write_bytes(data)
        (root / 'manifest.json').write_text(json.dumps(manifest))
        (root / 'assignments.json').write_text(json.dumps(declarations(lib, footprint='Owned:OwnedCustom')))
        with patch.object(native_run, 'run', side_effect=self.child):
            status = main([str(root / 'manifest.json'), '--captures', str(root),
                           '--assignments', str(root / 'assignments.json'), '--out', str(root / 'result.json')])
        self.assertEqual(status, 0)
        result = json.loads((root / 'result.json').read_text())
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result['findings'])
        self.assertEqual(len(self.calls), 1)

    def test_symlink_scratch_refuses_before_child(self):
        target = Path(self.tmp.name) / 'unrelated'
        target.mkdir()
        self.parent.symlink_to(target, target_is_directory=True)
        with patch.object(native_run, 'run', side_effect=AssertionError('must not launch')):
            result = self.indexed()
        self.assertEqual((result['execution'], result['outcome']), ('failed', 'unknown'))
        self.assertEqual(list(target.iterdir()), [])
