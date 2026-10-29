"""Pure contracts for native children in the fixed receipted fab boundary."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_ee import fab_stage, forward_annotation as fa, native_run
from agent_ee.checks import CheckError
from tests.test_schematic_stage_execution import mount_view


class FabStageExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.inputs = self.root / 'inputs'
        self.inputs.mkdir()
        (self.inputs / 'board.kicad_pcb').write_bytes(b'captured board')
        self.baseline = self.root / 'baseline.json'
        self.baseline.write_text('{}')

    def execution(self, view=None):
        original = Path.read_text
        view = view or (mount_view() + '\n99 0 0:1 / /tool/fixtures/fab-export ro - ext4 none ro')
        def read(path, *args, **kwargs):
            return view if str(path) == '/proc/self/mountinfo' else original(path, *args, **kwargs)
        with patch.object(Path, 'read_text', read), patch.object(
                fa, '__file__', '/tool/agent-ee/agent_ee/forward_annotation.py'):
            execution = fab_stage._FabStageExecution()
        execution.stage.scratch = self.root
        return execution

    def test_fixed_entry_routes_children_and_retains_failure_without_nested_sandbox(self):
        execution = self.execution()
        work = self.root / 'work'
        work.mkdir()
        (work / 'kicad-config').mkdir()
        def child(intent, **kwargs):
            self.assertEqual(intent.isolation, 'host')
            self.assertEqual(intent.argv[-3:], ('-o', str(work / 'out/report.json'),
                                               str(work / 'project/board.kicad_pcb')))
            self.assertEqual(intent.cwd, str(work))
            self.assertEqual(intent.env['KICAD_CONFIG_HOME'], str(work / 'kicad-config'))
            self.assertEqual(intent.env['PYTHONPATH'], '/tool/agent-ee')
            self.assertGreater(kwargs['timeout'], 0)
            self.assertLessEqual(kwargs['timeout'], 7)
            self.assertEqual(kwargs['mounts'], ())
            (work / 'stderr.log').write_text('native failure retained')
            return {'execution': 'failed', 'exit_code': 42}
        intent = native_run.Intent('kicad-cli', ['kicad-cli', 'pcb', 'export', 'stats',
            '-o', '/work/out/report.json', '/candidate/board.kicad_pcb'], timeout=7)
        def producer(argv):
            return native_run.run(intent, inputs=self.inputs, work=work,
                                  baseline=self.baseline, deadline=time.monotonic() + 30)
        with patch.object(fab_stage, '_FabStageExecution', return_value=execution), \
                patch.object(fab_stage, 'main', producer), \
                patch.object(native_run.Host, 'run', side_effect=child), \
                patch.object(native_run.Sandbox, 'run', side_effect=AssertionError('nested sandbox')):
            result = fab_stage._stage_main([])
        self.assertEqual(result, {'execution': 'failed', 'exit_code': 42})
        self.assertEqual((work / 'stderr.log').read_text(), 'native failure retained')
        self.assertEqual((self.inputs / 'board.kicad_pcb').read_bytes(), b'captured board')

    def test_standalone_entry_keeps_normal_sandbox(self):
        def producer(*args):
            return native_run.run(native_run.Intent('check', ['kicad-cli', 'version']),
                                  inputs=self.inputs, work=self.root / 'standalone')
        with patch.object(fab_stage, 'export', side_effect=producer), \
                patch.object(native_run.Sandbox, 'run', return_value={}) as sandbox, \
                patch.object(native_run.Host, 'run', side_effect=AssertionError('host escape')):
            self.assertEqual(fab_stage.main(['--board', 'b', '--schematic', 's']), 0)
        sandbox.assert_called_once()

    def test_baseline_argument_resolves_only_to_the_captured_file(self):
        intent = native_run.Intent('read-baseline', ['/usr/bin/cat', native_run.BASELINE_AT])
        with native_run.using(self.execution()), patch.object(native_run.Host, 'run', return_value={}) as host:
            native_run.run(intent, inputs=self.inputs, work=self.root / 'work', baseline=self.baseline)
            self.assertEqual(host.call_args.args[0].argv, ('/usr/bin/cat', str(self.baseline)))
            host.reset_mock()
            with self.assertRaisesRegex(ValueError, 'baseline was not captured'):
                native_run.run(intent, inputs=self.inputs, work=self.root / 'work')
            host.assert_not_called()

    def test_mount_guard_requires_readonly_templates_and_rejects_extra_host_mount(self):
        from agent_ee.forward_annotation import AnnotationError
        for view in (mount_view(), mount_view() + '\n99 0 0:1 / /tool/fixtures/fab-export rw - ext4 none rw',
                     mount_view() + '\n99 0 0:1 / /tool/fixtures/fab-export ro - ext4 none ro\n'
                     '100 0 0:1 / /host ro - ext4 none ro'):
            with self.subTest(view=view), self.assertRaises(AnnotationError):
                self.execution(view)

    def test_input_or_baseline_mutation_is_refused_even_when_child_returns_success(self):
        execution = self.execution()
        for target in (self.inputs / 'board.kicad_pcb', self.baseline):
            original = target.read_bytes()
            def mutate(*args, **kwargs):
                target.write_bytes(b'changed')
                return {'execution': 'ok', 'exit_code': 0}
            with self.subTest(target=target), patch.object(native_run.Host, 'run', side_effect=mutate), \
                    native_run.using(execution), self.assertRaisesRegex(CheckError, 'changed its captured inputs'):
                native_run.run(native_run.Intent('check', ['kicad-cli', 'version']),
                               inputs=self.inputs, work=self.root / 'work', baseline=self.baseline)
            target.write_bytes(original)

    def test_escape_and_undeclared_mount_are_refused_before_native_launch(self):
        execution = self.execution()
        with patch.object(native_run.Host, 'run', side_effect=AssertionError('must refuse before native')):
            for argv, mounts in ((['/bin/true', '/work/../escape'], ()),
                                 (['/bin/true'], ((self.baseline, '/undeclared'),))):
                with self.subTest(argv=argv, mounts=mounts), native_run.using(execution), \
                        self.assertRaises(ValueError):
                    native_run.run(native_run.Intent('check', argv), inputs=self.inputs,
                                   work=self.root / 'work', mounts=mounts)

    def test_deadline_refuses_before_launch_and_stage_context_does_not_leak(self):
        with native_run.using(self.execution()), patch.object(native_run.Host, 'run') as host:
            with self.assertRaises(native_run.DeadlineExhausted):
                native_run.run(native_run.Intent('check', ['/bin/true']), inputs=self.inputs,
                               work=self.root / 'work', deadline=time.monotonic())
            host.assert_not_called()
        with patch.object(native_run.Sandbox, 'run', return_value={}) as sandbox:
            native_run.run(native_run.Intent('check', ['/bin/true']), inputs=self.inputs,
                           work=self.root / 'normal')
            sandbox.assert_called_once()

    def test_pcb_exports_use_fresh_exact_projects_and_only_disposable_preferences_can_change(self):
        from agent_ee.stage_receipts import file_tree
        for suffix in ('.kicad_pro', '.kicad_dru', '.kicad_prl', '.kicad_sch'):
            (self.inputs / ('board' + suffix)).write_text('captured ' + suffix)
        (self.inputs / 'collateral').mkdir()
        (self.inputs / 'collateral/child.kicad_sch').write_text('captured hierarchy')
        before = file_tree(self.inputs)
        execution = self.execution()
        # Rewriting the exact preferences file is allowed only in each copy.
        # Every design/collateral mutation and unrelated addition still refuses.
        for i, changed in enumerate(('.kicad_prl', '.kicad_prl', '.kicad_pcb', '.kicad_pro',
                '.kicad_dru', '.kicad_sch', 'extra', 'collateral/child.kicad_sch',
                'remove-board', 'remove-preferences', 'linked-preferences')):
            work = self.root / ('export-' + str(i))
            def child(intent, **kwargs):
                board = Path(intent.argv[-1]); project = work / 'project'
                self.assertEqual(board, project / 'board.kicad_pcb')
                self.assertEqual(file_tree(project), before)
                self.assertEqual(intent.env['KICAD10_SYMBOL_DIR'], str(self.inputs / 'stock/symbols'))
                self.assertEqual(intent.argv[-2], str(work / 'out/stats.json'))
                if changed == 'remove-board':
                    board.unlink()
                elif changed == 'remove-preferences':
                    board.with_suffix('.kicad_prl').unlink()
                elif changed == 'linked-preferences':
                    board.with_suffix('.kicad_prl').unlink()
                    board.with_suffix('.kicad_prl').symlink_to(self.baseline)
                else:
                    target = board.with_suffix(changed) if changed.startswith('.') else project / changed
                    target.write_text('authored native side effect')
                (work / 'stderr.log').write_text('retained child diagnostic')
                return {'execution': 'ok', 'exit_code': 0}
            with self.subTest(changed=changed), native_run.using(execution), \
                    patch.object(native_run.Host, 'run', side_effect=child):
                intent = native_run.Intent('kicad-cli', ['kicad-cli', 'pcb', 'export', 'stats',
                    '-o', '/work/out/stats.json', '/candidate/board.kicad_pcb'])
                if changed == '.kicad_prl':
                    self.assertEqual(native_run.run(intent, inputs=self.inputs, work=work)['exit_code'], 0)
                else:
                    with self.assertRaisesRegex(CheckError, 'changed its private project'):
                        native_run.run(intent, inputs=self.inputs, work=work)
                self.assertEqual(file_tree(self.inputs), before)
                self.assertEqual((work / 'stderr.log').read_text(), 'retained child diagnostic')

    def test_new_preferences_stay_in_copy_and_captured_additions_still_refuse(self):
        from agent_ee.stage_receipts import file_tree
        before = file_tree(self.inputs)
        for captured in (False, True):
            work = self.root / ('preferences-' + str(captured))
            def child(intent, **kwargs):
                board = Path(intent.argv[-1])
                (self.inputs / 'board.kicad_prl' if captured else board.with_suffix('.kicad_prl')).write_text(
                    'authored preferences')
                return {'execution': 'ok', 'exit_code': 0}
            with self.subTest(captured=captured), native_run.using(self.execution()), \
                    patch.object(native_run.Host, 'run', side_effect=child):
                intent = native_run.Intent('kicad-cli', ['kicad-cli', 'pcb', 'export', 'pos',
                    '-o', '/work/out/pos.csv', '/candidate/board.kicad_pcb'])
                if captured:
                    with self.assertRaisesRegex(CheckError, 'changed its captured inputs'):
                        native_run.run(intent, inputs=self.inputs, work=work)
                else:
                    self.assertEqual(native_run.run(intent, inputs=self.inputs, work=work)['exit_code'], 0)
                    self.assertEqual(file_tree(self.inputs), before)
                    self.assertTrue((work / 'project/board.kicad_prl').is_file())

    def test_schematic_exports_keep_variant_project_exact_and_preferences_disposable(self):
        from agent_ee.stage_receipts import file_tree
        (self.inputs / 'board.kicad_sch').write_text('captured schematic')
        (self.inputs / 'board.kicad_pro').write_text('captured variant project')
        before = file_tree(self.inputs)
        for command in ('netlist', 'bom'):
            for changed in ('.kicad_prl', '.kicad_pro'):
                work = self.root / (command + changed)
                def child(intent, **kwargs):
                    design = Path(intent.argv[-1])
                    self.assertEqual(design, work / 'project/board.kicad_sch')
                    self.assertEqual(file_tree(work / 'project'), before)
                    design.with_suffix(changed).write_text('native side effect')
                    return {'execution': 'ok', 'exit_code': 0}
                with self.subTest(command=command, changed=changed), native_run.using(self.execution()), \
                        patch.object(native_run.Host, 'run', side_effect=child):
                    intent = native_run.Intent('kicad-cli', ['kicad-cli', 'sch', 'export', command,
                        '-o', '/work/out/result', '/candidate/board.kicad_sch'])
                    if changed == '.kicad_prl':
                        self.assertEqual(native_run.run(intent, inputs=self.inputs, work=work)['exit_code'], 0)
                    else:
                        with self.assertRaisesRegex(CheckError, 'changed its private project'):
                            native_run.run(intent, inputs=self.inputs, work=work)
                    self.assertEqual(file_tree(self.inputs), before)

    def test_project_setup_consumes_child_budget_before_launch(self):
        intent = native_run.Intent('kicad-cli', ['kicad-cli', 'pcb', 'export', 'stats',
                                  '/candidate/board.kicad_pcb'], timeout=7)
        with native_run.using(self.execution()), patch.object(native_run.Host, 'run') as host, \
                patch.object(fab_stage.time, 'monotonic', side_effect=[100, 108]):
            with self.assertRaises(native_run.DeadlineExhausted):
                native_run.run(intent, inputs=self.inputs, work=self.root / 'spent')
            host.assert_not_called()

    def test_changed_copy_is_refused_before_native_launch(self):
        copytree = fab_stage.shutil.copytree
        def changed(source, target, **kwargs):
            result = copytree(source, target, **kwargs)
            (Path(target) / 'board.kicad_pcb').write_bytes(b'changed during copy')
            return result
        with native_run.using(self.execution()), patch.object(fab_stage.shutil, 'copytree', changed), \
                patch.object(native_run.Host, 'run') as host:
            with self.assertRaisesRegex(CheckError, 'private project copy changed'):
                native_run.run(native_run.Intent('kicad-cli', ['kicad-cli', 'pcb', 'export', 'stats',
                    '/candidate/board.kicad_pcb']), inputs=self.inputs, work=self.root / 'bad-copy')
            host.assert_not_called()
        self.assertEqual((self.inputs / 'board.kicad_pcb').read_bytes(), b'captured board')
