"""Snapshot identity, host bytecode isolation and real receipt mutation boundaries."""

import os
import json
import stat
import time
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

    def test_snapshot_permissions_ignore_umask(self):
        (self.source / 'nested').mkdir()
        (self.source / 'nested' / 'data.txt').write_text('data')
        for mask in (0o000, 0o022, 0o077, 0o777):
            with self.subTest(umask=mask):
                cache = self.root / f'cache-{mask}' / 'nested'
                previous = os.umask(mask)
                try:
                    with mock.patch.dict(os.environ, AGENT_EE_TOOL_SOURCE_CACHE=str(cache)):
                        captured = snapshot(self.source)
                finally:
                    os.umask(previous)
                for directory in (cache.parent, cache, captured, captured / 'nested'):
                    self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE((captured / 'tool.py').stat().st_mode), 0o400)
                self.assertEqual(stat.S_IMODE((cache / '.lock').stat().st_mode), 0o600)
                self.assertEqual(stage_receipts.tree_identity(captured)['sha256'], captured.name)

    def test_cache_env_empty_uses_default_relative_refuses(self):
        with mock.patch('agent_ee.tool_source.Path.home', return_value=self.root), \
                mock.patch.dict(os.environ, AGENT_EE_TOOL_SOURCE_CACHE=''):
            captured = snapshot(self.source)
        self.assertEqual(captured.parent, self.root / '.cache/agent-ee/tool-source')
        with mock.patch.dict(os.environ, AGENT_EE_TOOL_SOURCE_CACHE='relative-cache'):
            with self.assertRaisesRegex(ValueError, 'absolute'):
                snapshot(self.source)
        self.assertFalse((Path.cwd() / 'relative-cache').exists())

    def test_cache_file_refuses_without_changing_its_permissions(self):
        cache_file = self.root / 'cache-file'
        cache_file.write_text('keep bytes and permissions')
        cache_file.chmod(0o600)
        with mock.patch.dict(os.environ, AGENT_EE_TOOL_SOURCE_CACHE=str(cache_file)):
            with self.assertRaises((ValueError, OSError)):
                snapshot(self.source)
        self.assertEqual(cache_file.read_text(), 'keep bytes and permissions')
        self.assertEqual(stat.S_IMODE(cache_file.stat().st_mode), 0o600)

    def test_prune_removes_old_snapshots_and_abandoned_captures_only(self):
        from agent_ee.tool_source import prune
        old = snapshot(self.source)
        stamp = time.time() - 3 * 86400
        os.utime(old, (stamp, stamp))
        (self.source / 'tool.py').write_text('new source')
        current = snapshot(self.source)
        cache = current.parent
        crashed = cache / '.capture-crashed'
        crashed.mkdir()
        (crashed / 'partial').write_text('unfinished')
        unrelated = cache / 'keep-me'
        unrelated.write_text('not a snapshot')
        removed = prune(older_than_days=1)
        self.assertIn(old, removed)
        self.assertIn(crashed, removed)
        self.assertFalse(old.exists())
        self.assertFalse(crashed.exists())
        self.assertTrue(current.exists())
        self.assertTrue(unrelated.exists())
        with self.assertRaisesRegex(ValueError, 'finite|nonnegative'):
            prune(older_than_days=-1)
        with self.assertRaisesRegex(ValueError, 'finite|nonnegative'):
            prune(older_than_days=float('nan'))

    def test_verified_reuse_refreshes_prune_age_but_corruption_does_not(self):
        from agent_ee.tool_source import prune
        mounted = snapshot(self.source)
        stamp = time.time() - 40 * 86400
        os.utime(mounted, (stamp, stamp))
        self.assertEqual(snapshot(self.source), mounted)
        self.assertGreater(mounted.stat().st_mtime, stamp + 86400)
        self.assertNotIn(mounted, prune(older_than_days=30))
        self.assertTrue(mounted.exists())
        (mounted / 'planted').write_text('tamper')
        os.utime(mounted, (stamp, stamp))
        with self.assertRaisesRegex(ValueError, 'snapshot changed'):
            snapshot(self.source)
        self.assertEqual(mounted.stat().st_mtime, stamp)

    def test_prune_waits_for_real_step_and_releases_after_receipt(self):
        import subprocess
        require_toolchain()
        mounted = snapshot(self.source)
        design = self.root / 'design'
        design.mkdir()
        spec = {'schema': 'agent-ee.stage-step/1', 'stage': 'fab', 'step': 'prune-race',
                'argv': ['/usr/bin/python3', '-B', '/tool/tool.py'],
                'version_argv': ['/usr/bin/python3', '--version'],
                'mounts': [{'source': str(mounted), 'at': '/tool'}], 'source': '/tool',
                'writes': {}, 'artifacts': ['result.txt']}
        execute = stage_receipts.execute
        processes = []
        def concurrent_prune(*args, **kwargs):
            if not processes:
                code = """import fcntl, os
from pathlib import Path
from agent_ee.tool_source import prune
with open(Path(os.environ['AGENT_EE_TOOL_SOURCE_CACHE']) / '.lock') as lock:
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print('step holds lock', flush=True)
    else:
        raise RuntimeError('step did not protect its snapshot')
prune(older_than_days=0)
"""
                proc = subprocess.Popen([os.sys.executable, '-c', code],
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                processes.append(proc)
                self.assertEqual(proc.stdout.readline().strip(), 'step holds lock')
                self.assertIsNone(proc.poll())
            self.assertTrue(mounted.exists())
            return execute(*args, **kwargs)
        try:
            with mock.patch.object(stage_receipts, 'execute', side_effect=concurrent_prune):
                receipt = stage_receipts.run_step(self.root / 'receipt', design, spec, {})
            self.assertEqual(receipt['status'], 'completed', receipt)
            self.assertEqual(receipt['violations'], [])
            stdout, stderr = processes[0].communicate(timeout=10)
            self.assertEqual(processes[0].returncode, 0, (stdout, stderr))
            self.assertFalse(mounted.exists())
            self.assertEqual(stage_receipts.validate_receipt(self.root / 'receipt'), receipt)
        finally:
            for proc in processes:
                if proc.poll() is None:
                    proc.kill()
                proc.communicate()

    def test_step_failure_releases_snapshot_lock(self):
        import fcntl
        mounted = snapshot(self.source)
        spec = {'schema': 'agent-ee.stage-step/1', 'stage': 'fab', 'step': 'invalid-design',
                'argv': ['/usr/bin/true'], 'mounts': [{'source': str(mounted), 'at': '/tool'}],
                'writes': {}}
        with self.assertRaises(ValueError):
            stage_receipts.run_step(self.root / 'receipt', self.root / 'missing', spec, {})
        with (mounted.parent / '.lock').open() as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_capture_cleans_crashed_temporary_directory_under_lock(self):
        cache = self.root / 'cache'
        crashed = cache / '.capture-crashed' / 'src'
        crashed.mkdir(parents=True)
        (crashed / 'partial.py').write_text('partial')
        crashed.chmod(0)
        crashed.parent.chmod(0)
        snapshot(self.source)
        self.assertEqual(list(cache.glob('.capture-*')), [])

    def test_pre_run_tampering_refuses_and_retained_name_mismatch_cannot_adopt(self):
        require_toolchain()
        mounted = snapshot(self.source)
        spec = {'schema': 'agent-ee.stage-step/1', 'stage': 'fab', 'step': 'pre-run-tamper',
                'argv': ['/usr/bin/python3', '-B', '/tool/tool.py'],
                'mounts': [{'source': str(mounted), 'at': '/tool'}], 'source': '/tool',
                'writes': {}, 'artifacts': ['result.txt']}
        design = self.root / 'design'
        design.mkdir()
        (mounted / 'planted.txt').write_text('tamper before run')
        receipt_dir = self.root / 'receipt'
        receipt = stage_receipts.run_step(receipt_dir, design, spec, {})
        self.assertEqual(receipt['exit_code'], 0)
        self.assertEqual(receipt['status'], 'refused')
        self.assertIn('snapshot_hash_mismatch:/tool', receipt['violations'])
        self.assertEqual(stage_receipts.validate_receipt(receipt_dir), receipt)
        with self.assertRaises(ValueError):
            stage_receipts.adopt(receipt_dir, receipt, design)
        # A legacy/forged completed receipt must fail retained validation too.
        forged = {**receipt, 'status': 'completed', 'violations': []}
        forged['identity'] = identity({k: v for k, v in forged.items() if k != 'identity'})
        from agent_ee.storage import write_json
        write_json(receipt_dir / 'receipt.json', forged)
        with self.assertRaisesRegex(ValueError, 'violations mismatch'):
            stage_receipts.adopt(receipt_dir, forged, design)

    def test_remaining_live_mount_builders_use_snapshots(self):
        from agent_ee import pod_driver, add_planes, binding_fill
        with mock.patch.object(pod_driver, 'SOURCE', self.source):
            specs = [pod_driver._scaffold_step(self.root, ['board.kicad_pcb']),
                     pod_driver._constraint_step(self.root, {'board': 'project/pod.kicad_pcb'})]
        specs += [add_planes.step(self.source, board='b.kicad_pcb', plan='planes.json'),
                  binding_fill.configuration(self.source, intent='intent.json', manifest='parts.json',
                      part_refs='refs.json', deviations='deviations.json', captures='.', dependencies=[])]
        expected = str(snapshot(self.source))
        for spec in specs:
            self.assertEqual(next(m['source'] for m in spec['mounts']
                                  if m['at'] == '/tool/agent-ee'), expected, spec['step'])

    def test_fixture_cleanup_retains_refused_receipts_on_exception(self):
        from tests.receipt_diagnostics import ReceiptTemporaryDirectory
        retained = self.root / 'retained'
        with mock.patch('tests.receipt_diagnostics.RETENTION_ROOT', retained):
            with self.assertRaisesRegex(RuntimeError, 'fixture failed'):
                with ReceiptTemporaryDirectory(dir=self.root) as tmp:
                    receipt = Path(tmp) / 'run' / 'receipt.json'
                    receipt.parent.mkdir()
                    receipt.write_text(json.dumps({'schema': stage_receipts.RECEIPT_SCHEMA,
                                                  'status': 'refused', 'violations': ['source_changed']}))
                    (receipt.parent / 'stderr.log').write_text('diagnostic')
                    raise RuntimeError('fixture failed')
        receipts = list(retained.glob('*/receipt.json'))
        self.assertEqual(len(receipts), 1)
        self.assertEqual(json.loads(receipts[0].read_text())['violations'], ['source_changed'])
        self.assertEqual((receipts[0].parent / 'stderr.log').read_text(), 'diagnostic')

    def test_prune_cli_and_unsafe_capture_link(self):
        import subprocess
        from agent_ee.tool_source import prune
        captured = snapshot(self.source)
        proc = subprocess.run([os.sys.executable, '-m', 'agent_ee.tool_source', 'prune',
                               '--older-than-days', '0'], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(str(captured), proc.stdout)
        self.assertFalse(captured.exists())
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'keep').write_text('keep')
        (captured.parent / '.capture-link').symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'unsafe abandoned'):
            snapshot(self.source)
        with self.assertRaisesRegex(ValueError, 'unsafe abandoned'):
            prune(older_than_days=0)
        self.assertEqual((outside / 'keep').read_text(), 'keep')

    def test_adoption_checks_recorded_source_hash_without_live_cache(self):
        require_toolchain()
        from agent_ee.storage import write_json
        mounted = snapshot(self.source)
        design = self.root / 'design'
        design.mkdir()
        spec = {'schema': 'agent-ee.stage-step/1', 'stage': 'fab', 'step': 'recorded-snapshot',
                'argv': ['/usr/bin/python3', '-B', '/tool/tool.py'],
                'mounts': [{'source': str(mounted), 'at': '/tool'}], 'source': '/tool',
                'writes': {}, 'artifacts': ['result.txt']}
        receipt_dir = self.root / 'receipt'
        receipt = stage_receipts.run_step(receipt_dir, design, spec, {})
        self.assertEqual(receipt['status'], 'completed')
        shutil.rmtree(mounted)
        stage_receipts.adopt(receipt_dir, receipt, design)
        for observation in (receipt['tool'], receipt['tool_after']):
            observation['source_tree']['sha256'] = '0' * 64
        receipt['identity'] = identity({k: v for k, v in receipt.items() if k != 'identity'})
        write_json(receipt_dir / 'receipt.json', receipt)
        with self.assertRaisesRegex(ValueError, 'violations mismatch'):
            stage_receipts.adopt(receipt_dir, receipt, design)

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

    def test_source_replaced_by_link_during_copy_never_chmods_link_target(self):
        victim = self.root / 'outside.txt'
        victim.write_text('outside bytes')
        victim.chmod(0o600)
        copy = shutil.copyfile
        def replace_with_link(source, destination, **kwargs):
            source = Path(source)
            source.unlink()
            source.symlink_to(victim)
            return copy(source, destination, **kwargs)
        with mock.patch('agent_ee.tool_source.shutil.copyfile', side_effect=replace_with_link):
            with self.assertRaisesRegex(ValueError, 'changed during snapshot'):
                snapshot(self.source)
        self.assertEqual(stat.S_IMODE(victim.stat().st_mode), 0o600)
        self.assertEqual(victim.read_text(), 'outside bytes')
        self.assertEqual([p for p in (self.root / 'cache').iterdir() if p.name != '.lock'], [])

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

    def test_repository_wide_step_builders_mount_only_snapshots(self):
        """Discover source-mount constructors; every discovered builder must be exercised."""
        import ast
        from agent_ee import (add_planes, binding_fill, pod_driver, rung1_stages,
                              scaffold, schematic_stage)
        route = require_kicad_route()
        repo = self.root / 'repo'
        repo.mkdir()
        shutil.copytree(self.source, repo / 'src')
        with mock.patch.object(pod_driver, 'SOURCE', self.source):
            cases = {
                'pod_driver._scaffold_step': [pod_driver._scaffold_step(self.root, ['b.kicad_pcb'])],
                'pod_driver._constraint_step': [pod_driver._constraint_step(self.root, {'board': 'b.kicad_pcb'})],
            }
        cases.update({
            'add_planes.step': [add_planes.step(self.source, board='b.kicad_pcb', plan='planes.json')],
            'binding_fill.configuration': [binding_fill.configuration(self.source, intent='intent.json',
                manifest='parts.json', part_refs='refs.json', deviations='deviations.json',
                captures='.', dependencies=[])],
            'fab_stage.step': [fab_stage.step(self.source, 'b.kicad_pcb', 'b.kicad_sch')],
            'rung1_campaign.scaffold_step': [rc.scaffold_step(self.source, rc.STEPS_DIR, rc.FIXTURE_PROJECT)],
            'rung1_campaign.step_specs': rc.step_specs(self.source, rc.STEPS_DIR, route),
            'rung1_qualified.schematic_step_specs': q.schematic_step_specs(source_root=self.source),
            'rung1_stages.requirements_step': [rung1_stages.requirements_step(self.source)],
            'scaffold.content_step': [scaffold.content_step(repo, 'envelope.json', 'parts.json', 'b.kicad_pcb')],
            'schematic_stage.configuration': schematic_stage.configuration(self.source,
                board='b.kicad_pcb', schematic='b.kicad_sch')['implementation_steps'],
        })
        discovered = set()
        for path in (Path(__file__).resolve().parents[1] / 'src/agent_ee').rglob('*.py'):
            tree = ast.parse(path.read_text())
            constants = {node.targets[0].id: node.value.value for node in tree.body
                         if isinstance(node, ast.Assign) and len(node.targets) == 1
                         and isinstance(node.targets[0], ast.Name)
                         and isinstance(node.value, ast.Constant)}
            # Tuple assignments declare the campaign MOUNT_SOURCE constants.
            for node in tree.body:
                if (isinstance(node, ast.Assign) and len(node.targets) == 1
                        and isinstance(node.targets[0], ast.Tuple)
                        and isinstance(node.value, ast.Tuple)):
                    constants.update({key.id: value.value for key, value in
                        zip(node.targets[0].elts, node.value.elts)
                        if isinstance(key, ast.Name) and isinstance(value, ast.Constant)})
            for function in ast.walk(tree):
                if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for node in ast.walk(function):
                    values = []
                    if (isinstance(node, ast.Dict) and any(isinstance(key, ast.Constant)
                            and key.value == 'source' for key in node.keys)):
                        values = [value for key, value in zip(node.keys, node.values)
                                  if isinstance(key, ast.Constant) and key.value == 'at']
                    elif isinstance(node, ast.Call) and any(kw.arg == 'source' for kw in node.keywords):
                        values = [kw.value for kw in node.keywords if kw.arg == 'at']
                    if any((value.value if isinstance(value, ast.Constant) else
                            constants.get(value.id) if isinstance(value, ast.Name) else None)
                           == '/tool/agent-ee' for value in values):
                        discovered.add(path.stem + '.' + function.name)
        self.assertEqual(discovered, set(cases), 'new source-mount builder needs executable coverage')
        expected = str(snapshot(self.source))
        for builder, specs in cases.items():
            mounts = [m for spec in specs for m in spec['mounts'] if m['at'] == '/tool/agent-ee']
            self.assertTrue(mounts, builder)
            for mount in mounts:
                self.assertEqual(mount['source'], expected, builder)
                self.assertNotEqual(Path(mount['source']), self.source, builder)

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
                            (mounted / 'tool.py').chmod(0o600)
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
                (mounted / 'tool.py').chmod(0o600)
                (mounted / 'tool.py').write_bytes(original)
                if planted.exists():
                    planted.unlink()
