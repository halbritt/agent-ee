"""Snapshot identity, host bytecode isolation and real receipt mutation boundaries."""

import os
import py_compile
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_ee import fab_stage, rung1_campaign as rc, rung1_qualified as q, stage_receipts
from agent_ee.storage import identity
from agent_ee.tool_source import snapshot
from tests.toolchain import require_kicad_route, require_toolchain


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'src'
        self.source.mkdir()
        (self.source / 'tool.py').write_text("from pathlib import Path; Path('result.txt').write_text('ok')\n")
        self.patch = mock.patch.dict(os.environ, AGENT_EE_TOOL_SOURCE_CACHE=str(self.root / 'cache'))
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_same_bytes_same_path_and_spec_identity_despite_host_bytecode(self):
        first = fab_stage.step(self.source, 'project/b.kicad_pcb', 'project/b.kicad_sch')
        py_compile.compile(str(self.source / 'tool.py'), doraise=True)
        second = fab_stage.step(self.source, 'project/b.kicad_pcb', 'project/b.kicad_sch')
        self.assertEqual(first, second)
        self.assertEqual(identity(first), identity(second))
        mounted = Path(first['mounts'][0]['source'])
        self.assertEqual(list(mounted.rglob('__pycache__')), [])
        self.assertEqual(mounted.name, stage_receipts.tree_identity(mounted)['sha256'])
        other = self.root / 'same-bytes'
        shutil.copytree(self.source, other)
        self.assertEqual(snapshot(other), mounted)

    def test_concurrent_publish_reuses_the_same_verified_tree(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=4) as workers:
            targets = list(workers.map(snapshot, [self.source] * 8))
        self.assertEqual(len(set(targets)), 1)
        self.assertEqual([p.name for p in (self.root / 'cache').iterdir()
                          if p.name != '.lock'], [targets[0].name])

    def test_source_edit_changes_path_and_spec_identity(self):
        first = fab_stage.step(self.source, 'b', 's')
        (self.source / 'tool.py').write_text('changed = True\n')
        second = fab_stage.step(self.source, 'b', 's')
        self.assertNotEqual(identity(first), identity(second))
        self.assertNotEqual(first['mounts'][0]['source'], second['mounts'][0]['source'])

    def test_non_cache_bytecode_and_other_files_are_captured(self):
        (self.source / 'tool.pyc').write_bytes(b'bytecode')
        (self.source / 'note.txt').write_bytes(b'note')
        captured = snapshot(self.source)
        self.assertEqual((captured / 'tool.pyc').read_bytes(), b'bytecode')
        self.assertEqual((captured / 'note.txt').read_bytes(), b'note')

    def test_links_empty_and_missing_source_refuse(self):
        (self.source / 'link').symlink_to(self.source / 'tool.py')
        with self.assertRaisesRegex(ValueError, 'link or special'):
            snapshot(self.source)
        with self.assertRaises(FileNotFoundError):
            snapshot(self.root / 'missing')
        empty = self.root / 'empty'
        empty.mkdir()
        with self.assertRaisesRegex(ValueError, 'empty'):
            snapshot(empty)

    def test_corrupted_cache_refuses_instead_of_repairing(self):
        for name in ('new.py', 'new.pyc', 'note.txt'):
            with self.subTest(name=name):
                captured = snapshot(self.source)
                planted = captured / name
                planted.write_bytes(b'planted')
                with self.assertRaisesRegex(ValueError, 'snapshot changed'):
                    snapshot(self.source)
                self.assertEqual(planted.read_bytes(), b'planted')
                planted.unlink()

    def test_unreadable_subdirectory_refuses_instead_of_partial_capture(self):
        restricted = self.source / 'restricted'
        restricted.mkdir()
        (restricted / 'hidden.py').write_text('hidden = True')
        restricted.chmod(0)
        try:
            with self.assertRaises(PermissionError):
                snapshot(self.source)
        finally:
            restricted.chmod(0o700)
        self.assertFalse((self.root / 'cache').exists())

    def test_special_files_and_links_in_cache_refuse(self):
        captured = snapshot(self.source)
        fifo = captured / 'fifo'
        os.mkfifo(fifo)
        with self.assertRaisesRegex(ValueError, 'snapshot changed'):
            snapshot(self.source)
        fifo.unlink()
        link = captured / 'link'
        link.symlink_to(self.source / 'tool.py')
        with self.assertRaisesRegex(ValueError, 'snapshot changed'):
            snapshot(self.source)

    def test_generator_refusal_reports_violations_and_keeps_receipt(self):
        from agent_ee import generate as g
        from tests.test_fab_stage import candidate
        require_toolchain(native=True)
        inputs = candidate(self.root / 'inputs')
        stage = g.Stage(self.root / 'generator', 7, 'fab')
        stage.start()
        execute = stage_receipts.execute
        mounted = snapshot(g.PACKAGE_ROOT)
        planted = mounted / 'receipt-probe.txt'
        def execute_then_mutate(*args, **kwargs):
            result = execute(*args, **kwargs)
            if Path(args[1]).name == 'version':
                planted.write_text('mutation')
            return result
        with mock.patch.object(stage_receipts, 'execute', side_effect=execute_then_mutate):
            with self.assertRaisesRegex(g.StageFailure, 'violations.*mount_changed.*receipt kept at'):
                g.stage_fab(stage, {'name': 'cpl', 'reviewed_inputs': inputs}, {})
        receipt_dir = stage.dir / 'export-fab'
        receipt = stage_receipts.validate_receipt(receipt_dir)
        self.assertEqual((receipt['exit_code'], receipt['status']), (0, 'refused'))
        self.assertEqual(receipt['violations'], ['mount_changed:/tool/agent-ee', 'source_changed'])
        self.assertTrue((receipt_dir / 'receipt.json').is_file())

    def test_changed_source_during_copy_refuses_without_publishing(self):
        copy = shutil.copyfile
        def changing_copy(*args, **kwargs):
            result = copy(*args, **kwargs)
            (self.source / 'tool.py').write_text('changed = True\n')
            return result
        with mock.patch('agent_ee.tool_source.shutil.copyfile', side_effect=changing_copy):
            with self.assertRaisesRegex(ValueError, 'changed during snapshot'):
                snapshot(self.source)
        self.assertEqual([p for p in (self.root / 'cache').iterdir() if p.name != '.lock'], [])

    def test_every_driver_source_mount_is_snapshotted(self):
        route = require_kicad_route()
        specs = rc.step_specs(self.source, rc.STEPS_DIR, route)
        specs += q.step_specs(route, source_root=self.source, fab=True)
        specs += [rc.scaffold_step(self.source, rc.STEPS_DIR, rc.FIXTURE_PROJECT)]
        expected = str(snapshot(self.source))
        mounts = [m for s in specs for m in s['mounts'] if m['at'] == '/tool/agent-ee']
        self.assertGreater(len(mounts), 5)
        self.assertTrue(all(m['source'] == expected for m in mounts))

    def test_real_receipts_isolate_host_writes_but_refuse_mounted_mutations(self):
        require_toolchain()
        mounted = snapshot(self.source)
        design = self.root / 'design'
        design.mkdir()
        spec = {'schema': 'agent-ee.stage-step/1', 'stage': 'fab', 'step': 'snapshot-boundary',
                'argv': ['/usr/bin/python3', '-B', '/tool/tool.py'],
                'version_argv': ['/usr/bin/python3', '--version'],
                'mounts': [{'source': str(mounted), 'at': '/tool'}], 'source': '/tool',
                'writes': {}, 'artifacts': ['result.txt']}
        execute = stage_receipts.execute
        cases = ['host-pyc', 'new.py', 'new.pyc', 'note.txt', 'edit.py']
        for case in cases:
            with self.subTest(case=case):
                original = (mounted / 'tool.py').read_bytes()
                planted = mounted / case
                def execute_then_mutate(*args, **kwargs):
                    result = execute(*args, **kwargs)
                    if Path(args[1]).name == 'version':
                        if case == 'host-pyc':
                            py_compile.compile(str(self.source / 'tool.py'), doraise=True)
                        elif case == 'edit.py':
                            (mounted / 'tool.py').write_bytes(original + b'\n# edit\n')
                        else:
                            planted.write_bytes(b'planted')
                    return result
                receipt_dir = self.root / case
                with mock.patch.object(stage_receipts, 'execute', side_effect=execute_then_mutate):
                    receipt = stage_receipts.run_step(receipt_dir, design, spec, {'case': case})
                self.assertEqual(stage_receipts.validate_receipt(receipt_dir), receipt)
                self.assertEqual(receipt['exit_code'], 0)
                if case == 'host-pyc':
                    self.assertEqual(receipt['status'], 'completed', receipt)
                    stage_receipts.adopt(receipt_dir, receipt, design)
                else:
                    self.assertEqual(receipt['status'], 'refused', receipt)
                    from tests.receipt_diagnostics import retain_refusal
                    with self.assertRaisesRegex(AssertionError, 'violations.*mount_changed.*receipt kept at') as failure:
                        retain_refusal(receipt_dir, receipt)
                    retained = Path(str(failure.exception).split('receipt kept at ')[1])
                    self.assertEqual(stage_receipts.validate_receipt(retained.parent), receipt)
                    shutil.rmtree(retained.parent)
                    self.assertEqual(receipt['violations'], ['mount_changed:/tool', 'source_changed'])
                    with self.assertRaises(ValueError):
                        stage_receipts.adopt(receipt_dir, receipt, design)
                (mounted / 'tool.py').write_bytes(original)
                if planted.exists():
                    planted.unlink()
