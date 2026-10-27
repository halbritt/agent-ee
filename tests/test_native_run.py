import errno
import hashlib
import io
import json
import os
import signal
import tempfile
import time
import unittest
from pathlib import Path

from toolchain import prerequisite_problem

from agent_ee import benchmark_run, executor, native_run, stage_receipts
from agent_ee.native_run import Intent, Outcome


def _argv_variants(root):
    inputs, work = root / 'inputs', root / 'work'
    inputs.mkdir(exist_ok=True)
    work.mkdir(exist_ok=True)
    extra = root / 'extra'
    extra.mkdir(exist_ok=True)
    baseline = root / 'baseline.json'
    baseline.write_text('{}')
    yield 'plain', executor.sandbox_argv(inputs, work, ['/usr/bin/true'])
    yield 'network', executor.sandbox_argv(inputs, work, ['/usr/bin/true'], network=True)
    yield 'visuals', executor.sandbox_argv(inputs, work, ['kicad-cli', 'version'], skip_visuals=True)
    yield 'mounts', executor.sandbox_argv(inputs, work, ['/usr/bin/true'],
                                          extra_ro=[(baseline, '/evidence/baseline.json'), (extra, '/tool')])
    replay = executor.sandbox_argv(inputs, work, ['/usr/bin/python3', '-I'], extra_ro=((extra, '/bundle'),))
    boundary = replay.index('--')
    replay[boundary:boundary] = [arg for path in ('/work', '/tmp', '/dev', '/proc', '/')
                                 for arg in ('--remount-ro', path)]
    yield 'replay', replay


# Frozen copies of the readers that re-tokenised the argv before AGENTEE-540 batch d moved them onto
# parse_profile (stage_receipts.sandbox_profile, benchmark_run.sandbox_path_operands at 307a5da1).
# They are the independent reference for historical compatibility; do not route them through native_run.
def historical_sandbox_profile(sandbox_argv):
    if not isinstance(sandbox_argv, list) or '--' not in sandbox_argv:
        raise ValueError('malformed sandbox argv')
    options = sandbox_argv[:sandbox_argv.index('--')]
    profile, index = [], 0
    while index < len(options):
        token = options[index]
        if token in ('--bind', '--ro-bind', '--ro-bind-fd') and index + 2 < len(options):
            source, dest = options[index + 1], options[index + 2]
            profile += [token, source if source == dest else '<host>', dest]
            index += 3
        else:
            profile.append(token)
            index += 1
    return profile


HISTORICAL_FLAGS = {'--die-with-parent', '--new-session', '--unshare-pid', '--unshare-ipc', '--unshare-uts',
                    '--unshare-net', '--clearenv'}
HISTORICAL_ONE = {'--proc', '--dev', '--tmpfs', '--dir', '--chdir'}
HISTORICAL_TWO = {'--ro-bind', '--bind', '--symlink', '--setenv'}


def historical_path_operands(argv):
    if not argv or argv[0] != 'bwrap' or '--' not in argv:
        return None
    end = argv.index('--')
    found, i = [], 1
    while i < end:
        option = argv[i]
        if option in HISTORICAL_FLAGS:
            i += 1
        elif option in HISTORICAL_ONE and i + 1 < end:
            i += 2
        elif option in HISTORICAL_TWO and i + 2 < end:
            if option in ('--ro-bind', '--bind'):
                found.append(i + 1)
            i += 3
        else:
            return None
    return found if i == end else None


class ProfileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_normalized_profile_matches_the_stage_receipt_form(self):
        for label, argv in _argv_variants(self.root):
            with self.subTest(label):
                self.assertEqual(native_run.parse_profile(argv).normalized(), historical_sandbox_profile(argv))
                self.assertEqual(stage_receipts.sandbox_profile(argv), historical_sandbox_profile(argv))

    def test_path_sources_match_the_benchmark_operand_parser(self):
        for label, argv in _argv_variants(self.root):
            if label == 'replay':
                continue   # the benchmark parser never saw replay's --remount-ro options
            with self.subTest(label):
                self.assertEqual(native_run.parse_profile(argv).path_source_indexes(), historical_path_operands(argv))
                self.assertEqual(benchmark_run.sandbox_path_operands(argv), historical_path_operands(argv))

    def test_binds_network_and_tool_argv(self):
        argv = executor.sandbox_argv(self.root / 'inputs', self.root / 'work', ['kicad-cli', 'version'])
        parsed = native_run.parse_profile(argv)
        self.assertFalse(parsed.network)
        self.assertEqual(parsed.bound('/candidate'), [str((self.root / 'inputs').resolve())])
        self.assertEqual(parsed.bound('/work'), [str((self.root / 'work').resolve())])
        self.assertEqual([bind.dest for bind in parsed.binds if bind.writable], ['/work'])
        self.assertEqual(parsed.tool_argv, ('kicad-cli', 'version'))
        self.assertFalse(parsed.guarded)
        self.assertTrue(native_run.parse_profile(
            executor.sandbox_argv(self.root / 'inputs', self.root / 'work', ['true'], network=True)).network)

    def test_fd_mounts_expose_the_guard_identities(self):
        (self.root / 'inputs').mkdir()
        (self.root / 'work').mkdir()
        source = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, source)
        argv = executor.sandbox_argv(self.root / 'inputs', self.root / 'work', ['kicad-cli', 'version'],
                                     extra_ro=[(source, '/tool')])
        parsed = native_run.parse_profile(argv)
        observed = os.fstat(source)
        self.assertEqual(parsed.mount_identities, {'/tool': (observed.st_dev, observed.st_ino)})
        self.assertEqual(parsed.tool_argv, ('kicad-cli', 'version'))
        self.assertEqual([(b.option, b.source, b.dest) for b in parsed.binds if b.option == '--ro-bind-fd'],
                         [('--ro-bind-fd', str(source), '/tool')])
        self.assertEqual(parsed.normalized(), historical_sandbox_profile(argv))
        self.assertEqual(benchmark_run.sandbox_path_operands(argv), historical_path_operands(argv))
        self.assertEqual(executor.sandbox_invocation(argv), (list(parsed.tool_argv), parsed.mount_identities))

    def test_unknown_layouts_are_refused(self):
        argv = executor.sandbox_argv(self.root / 'inputs', self.root / 'work', ['true'])
        for label, bad in (('option', argv[:1] + ['--cap-add', 'ALL'] + argv[1:]),
                           ('operands', ['bwrap', '--bind', '/x', '--', 'true']),
                           ('not bwrap', ['docker', '--', 'true']),
                           ('no delimiter', argv[:argv.index('--')])):
            with self.subTest(label), self.assertRaises(ValueError):
                native_run.parse_profile(bad)
        with self.assertRaises(ValueError):
            native_run.profile({'argv': ['true']})


class BuilderTest(unittest.TestCase):
    """Each existing site's literal argv, reproduced by the one builder."""

    def test_drc_sites(self):
        build = native_run.drc_argv
        cases = {
            'add_planes': (build('/candidate/b.kicad_pcb', '/work/report.json', executable='/usr/bin/kicad-cli'),
                           ['/usr/bin/kicad-cli', 'pcb', 'drc', '--format', 'json', '--severity-all', '-o',
                            '/work/report.json', '/candidate/b.kicad_pcb']),
            'checks': (build('/candidate/b', '/work/drc.json', parity=True, units='mm'),
                       ['kicad-cli', 'pcb', 'drc', '--format', 'json', '--severity-all', '--schematic-parity',
                        '--units', 'mm', '-o', '/work/drc.json', '/candidate/b']),
            'placement_job': (build('b', 'r', refill=True, output_flag='--output'),
                              ['kicad-cli', 'pcb', 'drc', '--format', 'json', '--severity-all', '--refill-zones',
                               '--output', 'r', 'b']),
            'placement_defects': (build('/candidate/b', '/work/native-drc.json', output_flag='--output'),
                                  ['kicad-cli', 'pcb', 'drc', '--format', 'json', '--severity-all', '--output',
                                   '/work/native-drc.json', '/candidate/b']),
            'placement_access': (build('/candidate/b', '/work/report.json', refill=True,
                                       executable='/usr/bin/kicad-cli'),
                                 ['/usr/bin/kicad-cli', 'pcb', 'drc', '--format', 'json', '--severity-all',
                                  '--refill-zones', '-o', '/work/report.json', '/candidate/b']),
            'sublayout': (build('/candidate/s', '/work/drc.json', units='mm'),
                          ['kicad-cli', 'pcb', 'drc', '--format', 'json', '--severity-all', '--units', 'mm',
                           '-o', '/work/drc.json', '/candidate/s']),
        }
        for label, (built, literal) in cases.items():
            with self.subTest(label):
                self.assertEqual(built, literal)
        # board_build's retained build records carry this historical spelling (parity before --format).
        self.assertEqual(build('/s/copy.kicad_pcb', '/s/drc-parity.json', parity=True, parity_first=True, units='mm',
                               output_flag='--output'),
                         ['kicad-cli', 'pcb', 'drc', '--schematic-parity', '--format', 'json', '--severity-all',
                          '--units', 'mm', '--output', '/s/drc-parity.json', '/s/copy.kicad_pcb'])
        with self.assertRaises(ValueError):
            build('b', 'r', output_flag='--out')
        with self.assertRaises(ValueError):
            build('b', 'r', parity_first=True)

    def test_netlist_and_module_sites(self):
        self.assertEqual(native_run.netlist_argv('/candidate/a.kicad_sch', '/work/design.xml'),
                         ['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml', '-o', '/work/design.xml',
                          '/candidate/a.kicad_sch'])
        self.assertEqual(native_run.python_module_argv('agent_ee.placement_probe', 'b', 3),
                         ['/usr/bin/python3', '-B', '-m', 'agent_ee.placement_probe', 'b', '3'])
        self.assertEqual(native_run.python_module_argv('agent_ee.pin_contracts', no_bytecode=False),
                         ['/usr/bin/python3', '-m', 'agent_ee.pin_contracts'])
        intent = native_run.pcbnew_script('agent_ee.rf_geometry', 'b', timeout=60)
        self.assertEqual((intent.tool, intent.timeout, intent.isolation), ('pcbnew_script', 60, 'sandbox'))

    def test_intents_validate(self):
        for label, kwargs in (('untimed sandbox', dict(tool='x', argv=['ls'], timeout=None)),
                              ('sandbox grace', dict(tool='x', argv=['ls'], stop_grace_s=1)),
                              ('negative grace', dict(tool='x', argv=['ls'], isolation='host', stop_grace_s=-1)),
                              ('remount on host', dict(tool='x', argv=['ls'], isolation='host', remount_ro=['/'])),
                              ('zero log cap', dict(tool='x', argv=['ls'], log_bytes=0)),
                              ('float log cap', dict(tool='x', argv=['ls'], log_bytes=1.5)),
                              ('empty argv', dict(tool='x', argv=())), ('string argv', dict(tool='x', argv='ls')),
                              ('isolation', dict(tool='x', argv=['ls'], isolation='vm')),
                              ('sandbox env', dict(tool='x', argv=['ls'], env={'A': '1'}))):
            with self.subTest(label), self.assertRaises(ValueError):
                Intent(**kwargs)


class RunRulesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        (self.root / 'inputs').mkdir()

    def test_deadline_clamps_and_refuses(self):
        adapter = native_run.Recorded()
        intent = Intent('probe', ['true'], timeout=600)
        record = native_run.run(intent, inputs=self.root / 'inputs', work=self.root / 'w',
                                deadline=time.monotonic() + 30, adapter=adapter)
        self.assertLess(record['timeout_s'], 31)
        self.assertLess(adapter.calls[0].timeout, 31)
        with self.assertRaises(native_run.DeadlineExhausted):
            native_run.run(intent, inputs=self.root / 'inputs', work=self.root / 'w',
                           deadline=time.monotonic() + 0.5, adapter=adapter)
        self.assertEqual(len(adapter.calls), 1)
        unclamped = native_run.run(intent, inputs=self.root / 'inputs', work=self.root / 'w', adapter=adapter)
        self.assertEqual(unclamped['timeout_s'], 600)

    def test_cut_by_deadline(self):
        intent = Intent('probe', ['true'], timeout=600)
        self.assertTrue(native_run.cut_by_deadline({'execution': 'timeout', 'timeout_s': 30.0}, intent))
        self.assertFalse(native_run.cut_by_deadline({'execution': 'timeout', 'timeout_s': 600}, intent))
        self.assertFalse(native_run.cut_by_deadline({'execution': 'ok', 'timeout_s': 30.0}, intent))

    def test_baseline_is_mounted_first_only_when_present(self):
        adapter = native_run.Recorded()
        baseline = self.root / 'baseline.json'
        extra = (self.root / 'x', '/x')
        intent = Intent('drc', ['true'])
        native_run.run(intent, inputs=self.root / 'inputs', work=self.root / 'a', baseline=baseline,
                       mounts=[extra], adapter=adapter)
        baseline.write_text('{}')
        native_run.run(intent, inputs=self.root / 'inputs', work=self.root / 'b', baseline=baseline,
                       mounts=[extra], adapter=adapter)
        self.assertEqual(adapter.calls[0].mounts, (extra,))
        self.assertEqual(adapter.calls[1].mounts, ((baseline, '/evidence/baseline.json'), extra))

    def test_using_routes_and_restores(self):
        adapter = native_run.Recorded(lambda call: Outcome(stdout=b'hello\n'))
        with native_run.using(adapter):
            record = native_run.run(Intent('probe', ['anything']), inputs=self.root / 'inputs', work=self.root / 'w')
        self.assertEqual(record.stdout, b'hello\n')
        self.assertEqual(len(adapter.calls), 1)
        with self.assertRaises(ValueError):   # the default sandbox adapter is back and checks its inputs
            native_run.run(Intent('probe', ['true']), work=self.root / 'w')

    def test_docker_intent_needs_an_adapter(self):
        with self.assertRaises(ValueError):
            native_run.run(Intent('bench', ['x'], isolation='docker'), work=None)
        with self.assertRaises(TypeError):
            native_run.run(['true'], work=None)


class RecordedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        (self.root / 'inputs').mkdir()

    def test_writes_the_executor_record_shape(self):
        def respond(call):
            return Outcome(exit_code=2, stdout=b'out', stderr=b'err', files={'report.json': b'{}'})
        adapter = native_run.Recorded(respond)
        work = self.root / 'w'
        record = native_run.run(native_run.drc('/candidate/b', '/work/report.json', timeout=60),
                                inputs=self.root / 'inputs', work=work, adapter=adapter)
        self.assertEqual(record['execution'], 'failed')
        self.assertEqual(record['exit_code'], 2)
        self.assertEqual(json.loads((work / 'execution.json').read_text()), dict(record))
        self.assertEqual((work / 'console.log').read_bytes(), b'outerr')
        self.assertEqual((work / 'report.json').read_bytes(), b'{}')
        self.assertEqual(record['sandbox_argv'], executor.sandbox_argv(self.root / 'inputs', work,
                                                                       list(record['argv'])))
        self.assertEqual(native_run.profile(record).tool_argv, tuple(record['argv']))
        self.assertEqual(adapter.calls[0].intent.tool, 'drc')

    def test_canned_record_files_are_displaced(self):
        work = self.root / 'w'
        record = native_run.run(Intent('probe', ['true']), inputs=self.root / 'inputs', work=work,
                                adapter=native_run.Recorded(lambda call: Outcome(files={'console.log': b'x'})))
        moved = record['displaced_records']['console.log']
        self.assertEqual((work / moved['moved_to']).read_bytes(), b'x')
        self.assertEqual((work / 'console.log').read_bytes(), b'')

    def test_launch_observer_gets_the_canned_pid(self):
        pids = []
        native_run.run(Intent('probe', ['true'], isolation='host'), work=None, on_launch=pids.append,
                       adapter=native_run.Recorded(lambda call: Outcome(pid=4321)))
        self.assertEqual(pids, [4321])

    def test_exceptions_and_cancellation(self):
        adapter = native_run.Recorded(lambda call: OSError('boom'))
        with self.assertRaises(OSError):
            native_run.run(Intent('probe', ['true']), inputs=self.root / 'inputs', work=self.root / 'w',
                           adapter=adapter)
        cancel = executor.Cancellation()
        cancel.cancel()
        with self.assertRaises(KeyboardInterrupt):
            native_run.run(Intent('probe', ['true']), inputs=self.root / 'inputs', work=self.root / 'c',
                           cancel=cancel, adapter=native_run.Recorded())
        self.assertEqual(json.loads((self.root / 'c/execution.json').read_text())['execution'], 'interrupted')


class HostTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def host(self, code, **intent):
        return Intent('probe', ['/usr/bin/python3', '-c', code], isolation='host', **intent)

    def test_output_exit_stdin_and_records(self):
        lines = []
        record = native_run.run(self.host('import sys; d=sys.stdin.read(); print(d); print("e", file=sys.stderr);'
                                          ' sys.exit(3)', stdin='in'),
                                work=self.root / 'w', on_line=lines.append)
        self.assertEqual((record['execution'], record['exit_code'], record['isolation']), ('failed', 3, 'host'))
        self.assertEqual(record.stdout, b'in\n')
        self.assertEqual(record.stderr, b'e\n')
        self.assertEqual(lines, [b'in'])
        self.assertEqual(json.loads((self.root / 'w/execution.json').read_text()), dict(record))
        self.assertNotIn('sandbox_argv', record)

    def test_env_and_cwd(self):
        record = native_run.run(self.host('import os; print(os.environ["X"], os.getcwd())',
                                          env={'X': 'y', 'PATH': '/usr/bin'}, cwd=str(self.root)),
                                work=None)
        self.assertEqual(record.stdout.decode().split(), ['y', str(self.root.resolve())])

    def test_timeout_kills_the_whole_group(self):
        pidfile = self.root / 'child.pid'
        code = ('import subprocess, sys, time; '
                f'p = subprocess.Popen(["sleep", "60"]); open({str(pidfile)!r}, "w").write(str(p.pid)); '
                'time.sleep(60)')
        start = time.monotonic()
        record = native_run.run(self.host(code, timeout=2), work=None)
        self.assertEqual(record['execution'], 'timeout')
        self.assertLess(time.monotonic() - start, 10)
        child = int(pidfile.read_text())
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                break
            time.sleep(.05)
        else:
            os.kill(child, signal.SIGKILL)
            self.fail('descendant outlived the timed-out group')

    def test_cancel_before_launch_and_missing_tool(self):
        cancel = executor.Cancellation()
        cancel.cancel()
        record = native_run.run(self.host('print(1)'), work=None, cancel=cancel)
        self.assertEqual(record['execution'], 'interrupted')
        missing = native_run.run(Intent('probe', ['/nonexistent/tool'], isolation='host'), work=None)
        self.assertEqual(missing['execution'], 'unavailable')

    def test_cancel_keeps_output_already_written(self):
        # on_line holds the read loop while the child writes its stderr tail and the run is
        # cancelled, so the tail is still in the pipe when the cancel is observed.
        go, wrote = self.root / 'go', self.root / 'wrote'
        code = ('import os, sys, time; print("ready", flush=True); '
                f'exec("while not os.path.exists({str(go)!r}): time.sleep(.01)"); '
                f'sys.stderr.write("tail\\n"); sys.stderr.flush(); open({str(wrote)!r}, "w").close(); '
                'time.sleep(60)')
        cancel = executor.Cancellation()

        def on_line(line):
            go.touch()
            while not wrote.exists():
                time.sleep(.01)
            cancel.cancel()

        record = native_run.run(self.host(code, timeout=30), work=self.root / 'w', cancel=cancel, on_line=on_line)
        self.assertEqual(record['execution'], 'interrupted')
        self.assertEqual((record.stdout, record.stderr), (b'ready\n', b'tail\n'))
        self.assertEqual((self.root / 'w/console.log').read_bytes(), b'ready\ntail\n')

    def test_stop_cleanup_runs_on_every_abnormal_exit(self):
        code = 'import time; print("line", flush=True); time.sleep(60)'
        for label, failure, state in (('error', RuntimeError('on_line failed'), 'callback_failed'),
                                      ('interrupt', KeyboardInterrupt(), 'interrupted')):
            stops = []

            def on_line(line):
                raise failure

            with self.subTest(label), self.assertRaises(type(failure)) as caught:
                native_run.supervise(['/usr/bin/python3', '-c', code], timeout=30, on_line=on_line,
                                     on_stop=lambda: stops.append(1))
            self.assertEqual(stops, [1], label)
            self.assertEqual(caught.exception.native_execution[0]['execution'], state)
        stops = []
        fields, _, _ = native_run.supervise(['/usr/bin/python3', '-c', code], timeout=1,
                                            on_stop=lambda: stops.append(1))
        self.assertEqual((fields['execution'], stops), ('timeout', [1]))
        fields, _, _ = native_run.supervise(['/usr/bin/true'], timeout=30, on_stop=lambda: stops.append(2))
        self.assertEqual((fields['execution'], stops), ('ok', [1]))

        def broken():
            raise OSError('docker missing')

        fields, _, _ = native_run.supervise(['/usr/bin/python3', '-c', code], timeout=1, on_stop=broken)
        self.assertEqual(fields['cleanup_error'], 'stop cleanup failed: docker missing')

    def test_planted_records_are_displaced_not_overwritten(self):
        work = self.root / 'w'
        record = native_run.run(self.host('open("execution.json", "w").write("planted")', cwd=str(work)),
                                work=work)
        moved = record['displaced_records']['execution.json']
        self.assertEqual(moved['kind'], 'file')
        self.assertEqual((work / moved['moved_to']).read_text(), 'planted')
        self.assertEqual(json.loads((work / 'execution.json').read_text()), dict(record))

    def test_launch_observer_gets_the_child_pid(self):
        pids = []
        record = native_run.run(self.host('import os; print(os.getpid())'), work=None, on_launch=pids.append)
        self.assertEqual(pids, [int(record.stdout)])

    def test_untimed_host_runs_and_the_deadline_still_clamps(self):
        record = native_run.run(self.host('print(1)', timeout=None), work=self.root / 'w')
        self.assertEqual((record['execution'], record['timeout_s']), ('ok', None))
        self.assertIsNone(json.loads((self.root / 'w/execution.json').read_text())['timeout_s'])
        intent = self.host('import time; time.sleep(60)', timeout=None)
        record = native_run.run(intent, work=None, deadline=time.monotonic() + 2)
        self.assertEqual(record['execution'], 'timeout')
        self.assertTrue(native_run.cut_by_deadline(record, intent))

    def test_stop_grace_sends_sigterm_first(self):
        marker = self.root / 'term'
        code = ('import signal, sys, time\n'
                f'def term(*_): open({str(marker)!r}, "w").close(); sys.exit(7)\n'
                'signal.signal(signal.SIGTERM, term)\nprint("ready", flush=True)\ntime.sleep(60)')
        record = native_run.run(self.host(code, timeout=1.5, stop_grace_s=5), work=None)
        self.assertEqual(record['execution'], 'timeout')
        self.assertTrue(marker.exists())
        marker2 = self.root / 'term2'
        code2 = code.replace(str(marker), str(marker2))
        native_run.run(self.host(code2, timeout=1.5), work=None)
        self.assertFalse(marker2.exists())

    def test_callback_failure_keeps_the_stopped_run(self):
        code = ('import signal, sys, time\n'
                'signal.signal(signal.SIGTERM, lambda *_: sys.exit(5))\n'
                'sys.stderr.write("diagnostic\\n"); sys.stderr.flush()\n'
                'print("one", flush=True); print("two", flush=True); time.sleep(60)')

        def on_line(line):
            if line == b'two':
                raise ValueError('protocol')

        with self.assertRaises(ValueError) as caught:
            native_run.run(self.host(code, timeout=None, stop_grace_s=5), work=self.root / 'w', on_line=on_line)
        execution = caught.exception.native_execution
        self.assertEqual((execution['execution'], execution['exit_code']), ('callback_failed', 5))
        self.assertEqual(execution.stderr, b'diagnostic\n')
        self.assertEqual(execution.stdout, b'one\ntwo\n')
        self.assertEqual(json.loads((self.root / 'w/execution.json').read_text()), dict(execution))

        def bad_launch(pid):
            raise RuntimeError('observer')

        with self.assertRaises(RuntimeError) as caught:
            native_run.run(self.host('import time; time.sleep(60)'), work=None, on_launch=bad_launch)
        self.assertEqual(caught.exception.native_execution['execution'], 'callback_failed')

    def test_tick_checkpoint_cancels_while_a_descendant_holds_the_pipe(self):
        # The leader exits at once; its detached-in-group child keeps stdout open and stays silent.
        pidfile = self.root / 'child.pid'
        code = ('import subprocess, sys; '
                f'p = subprocess.Popen(["sleep", "60"], stdout=sys.stdout); open({str(pidfile)!r}, "w").write(str(p.pid))')
        ticks, threads = [], set()

        def on_tick():
            import threading
            threads.add(threading.get_ident())
            ticks.append(time.monotonic())
            if len(ticks) == 20:
                raise ValueError('operation cancelled')

        start = time.monotonic()
        with self.assertRaises(ValueError) as caught:
            native_run.run(self.host(code, timeout=30), work=None, on_tick=on_tick)
        self.assertEqual(str(caught.exception), 'operation cancelled')
        self.assertEqual(caught.exception.native_execution['execution'], 'callback_failed')
        self.assertLess(time.monotonic() - start, 10)
        import threading
        self.assertEqual(threads, {threading.get_ident()})
        child = int(pidfile.read_text())
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                break
            time.sleep(.05)
        else:
            os.kill(child, signal.SIGKILL)
            self.fail('the pipe-holding descendant survived the failed checkpoint')

    def test_reaped_returncode_survives_timeout_and_cancel(self):
        record = native_run.run(self.host('import time; time.sleep(60)', timeout=1), work=self.root / 'w')
        self.assertEqual((record['execution'], record['exit_code'], record.returncode), ('timeout', None, -signal.SIGKILL))
        self.assertNotIn('returncode', json.loads((self.root / 'w/execution.json').read_text()))
        code = 'import signal, sys, time; signal.signal(signal.SIGTERM, lambda *_: sys.exit(3)); time.sleep(60)'
        cancel = executor.Cancellation()
        import threading
        threading.Timer(1, cancel.cancel).start()
        record = native_run.run(self.host(code, timeout=30, stop_grace_s=5), work=None, cancel=cancel)
        self.assertEqual((record['execution'], record['exit_code'], record.returncode), ('interrupted', None, 3))
        done = native_run.run(self.host('import sys; sys.exit(4)'), work=None)
        self.assertEqual((done['exit_code'], done.returncode), (4, 4))
        missing = native_run.run(Intent('probe', ['/nonexistent/tool'], isolation='host'), work=None)
        self.assertIsNone(missing.returncode)
    def test_log_ceiling_stops_and_truncates(self):
        code = 'import sys; sys.stdout.write("x" * 6000); sys.stdout.flush(); sys.stderr.write("y" * 6000)'
        fields, out, err = native_run.supervise(['/usr/bin/python3', '-c', code], timeout=30, log_bytes=100)
        self.assertEqual((fields['execution'], fields['logs_truncated']), ('output_limit', True))
        self.assertEqual(len(out) + len(err), 100)
        self.assertNotIn('cleanup_error', fields)
        fields, out, err = native_run.supervise(['/usr/bin/python3', '-c', 'print("short")'], timeout=30,
                                                log_bytes=100)
        self.assertEqual((fields['execution'], fields['logs_truncated'], out), ('ok', False, b'short\n'))
        fields, _, _ = native_run.supervise(['/usr/bin/true'], timeout=30)
        self.assertNotIn('logs_truncated', fields)

    def test_merged_stream_and_live_sink(self):
        code = ('import sys, time; sys.stdout.write("a"); sys.stdout.flush(); sys.stderr.write("b"); sys.stderr.flush(); '
                'time.sleep(.1); sys.stdout.write("c\\n")')
        sink = self.root / 'console.log'
        with sink.open('wb') as stream:
            record = native_run.run(self.host(code, merge_stderr=True), work=None, sink=stream)
        self.assertEqual((record.stdout, record.stderr), (b'', b''))
        self.assertEqual(sink.read_bytes(), b'abc\n')
        self.assertEqual((record['stdout_sha256'], record['console_sha256']),
                         (hashlib.sha256(b'abc\n').hexdigest(),) * 2)
        with self.assertRaises(ValueError), io.BytesIO() as stream:
            native_run.run(self.host('1'), work=self.root / 'w', sink=stream)
        with self.assertRaises(ValueError):
            Intent('x', ['true'], merge_stderr=True)
        with self.assertRaises(ValueError):
            native_run.run(Intent('x', ['true']), inputs=self.root, work=self.root / 'w', sink=io.BytesIO(),
                           adapter=native_run.Sandbox())

    def test_sink_memory_stays_flat_and_digests_cover_the_stream(self):
        # 4 MiB through the sink: nothing is kept, the digest still covers every byte (review finding 2).
        code = 'import sys; [sys.stdout.buffer.write(bytes([i % 251]) * 65536) for i in range(64)]; sys.stderr.write("tail")'
        expected = hashlib.sha256(b''.join(bytes([i % 251]) * 65536 for i in range(64)))
        sink = self.root / 'big.log'
        with sink.open('wb') as stream:
            record = native_run.run(self.host(code), work=None, sink=stream)
        self.assertEqual(record.stdout, b'')
        self.assertEqual(sink.stat().st_size, 64 * 65536)
        self.assertEqual(record['stdout_sha256'], expected.hexdigest())
        console = expected.copy()
        console.update(b'tail')
        self.assertEqual((record.stderr, record['console_sha256']), (b'tail', console.hexdigest()))

    def test_log_ceiling_counts_streamed_bytes(self):
        # Review finding (e1ccf595): twenty 50-byte chunks to a sink with log_bytes=100 must stop at the ceiling.
        code = 'import sys, time\nfor _ in range(20):\n    sys.stdout.write("x" * 50); sys.stdout.flush(); time.sleep(.02)'
        with io.BytesIO() as sink:
            fields, out, err = native_run.supervise(['/usr/bin/python3', '-c', code], timeout=30, log_bytes=100,
                                                    sink=sink)
            self.assertEqual((fields['execution'], fields['logs_truncated']), ('output_limit', True))
            self.assertEqual(len(sink.getvalue()), 100)
        self.assertEqual(out, b'')

    def test_both_streams_live_in_separate_sinks(self):
        # render3d/viewer/candidate_visuals keep separate live stdout/stderr logs.
        out_log, err_log = self.root / 'stdout.log', self.root / 'stderr.log'
        code = ('import sys, time; sys.stdout.write("o1"); sys.stdout.flush(); sys.stderr.write("e1"); sys.stderr.flush(); '
                f'time.sleep(.3); seen = open({str(err_log)!r}).read(); sys.stdout.write("|" + seen)')
        with out_log.open('wb') as out, err_log.open('wb') as err:
            record = native_run.run(self.host(code), work=None, sink=out, stderr_sink=err)
        self.assertEqual(out_log.read_bytes(), b'o1|e1')   # stderr was on disk while the child ran
        self.assertEqual(err_log.read_bytes(), b'e1')
        self.assertEqual((record.stdout, record.stderr), (b'', b'e1'))
        self.assertEqual(record['console_sha256'], hashlib.sha256(b'o1|e1e1').hexdigest())
        with self.assertRaises(ValueError), io.BytesIO() as err:
            native_run.run(self.host('1'), work=self.root / 'w', stderr_sink=err)

    def test_completed_matches_subprocess_run(self):
        import subprocess
        code = 'import sys; sys.stdout.write("a\\r\\nb\\rc"); sys.stderr.write("e"); sys.exit(2)'
        argv = ['/usr/bin/python3', '-c', code]
        expected = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        got = native_run.completed(native_run.run(Intent('probe', argv, isolation='host', timeout=30), work=None),
                                   text=True)
        self.assertEqual((got.args, got.returncode, got.stdout, got.stderr),
                         (expected.args, expected.returncode, expected.stdout, expected.stderr))
        raw = native_run.completed(native_run.run(Intent('probe', argv, isolation='host', timeout=30), work=None))
        self.assertEqual(raw.stdout, b'a\r\nb\rc')
        slow = ['/usr/bin/python3', '-c', 'import sys, time; sys.stdout.write("x"); sys.stdout.flush(); time.sleep(30)']
        with self.assertRaises(subprocess.TimeoutExpired) as caught:
            native_run.completed(native_run.run(Intent('probe', slow, isolation='host', timeout=1), work=None),
                                 text=True)
        self.assertEqual((caught.exception.stdout, caught.exception.timeout), (b'x', 1))
        silent = ['/usr/bin/python3', '-c', 'import time; time.sleep(30)']
        with self.assertRaises(subprocess.TimeoutExpired) as quiet:
            native_run.completed(native_run.run(Intent('probe', silent, isolation='host', timeout=1), work=None))
        with self.assertRaises(subprocess.TimeoutExpired) as real_quiet:
            subprocess.run(silent, capture_output=True, timeout=1)
        self.assertEqual((quiet.exception.stdout, quiet.exception.stderr),
                         (real_quiet.exception.stdout, real_quiet.exception.stderr))
        self.assertEqual((quiet.exception.stdout, quiet.exception.stderr), (None, None))
        for label, argv, cwd in (('program', ['/nonexistent/tool'], None),
                                 ('cwd', ['/usr/bin/true'], str(self.root / 'absent'))):
            with self.subTest(missing=label):
                with self.assertRaises(FileNotFoundError) as missing:
                    native_run.completed(native_run.run(Intent('probe', argv, isolation='host', cwd=cwd), work=None))
                with self.assertRaises(FileNotFoundError) as real:
                    subprocess.run(argv, capture_output=True, cwd=cwd)
                self.assertEqual((type(missing.exception), missing.exception.errno, missing.exception.strerror,
                                  missing.exception.filename, str(missing.exception)),
                                 (type(real.exception), real.exception.errno, real.exception.strerror,
                                  real.exception.filename, str(real.exception)))
        recorded = native_run.Recorded(lambda call: native_run.Outcome(exit_code=None, execution='unavailable'))
        with self.assertRaises(FileNotFoundError) as fallback:
            native_run.completed(native_run.run(Intent('probe', ['tool'], isolation='host'), work=None,
                                                adapter=recorded))
        self.assertEqual((fallback.exception.errno, fallback.exception.filename), (errno.ENOENT, 'tool'))

    def test_host_refuses_mounts(self):
        with self.assertRaises(ValueError):
            native_run.run(self.host('1'), work=None, mounts=[('/x', '/y')])


class DockerTest(unittest.TestCase):
    def test_argv_is_the_documented_container_command(self):
        source, prepared, output = '/s/impl', '/c/prepared', '/o/run'
        args = ['_execute', prepared, output + '/runs']
        adapter = native_run.Docker(
            image='sha256:' + 'a' * 64, name='agent-ee-21-run', env=('HOME=/tmp', 'AGENT_EE_REQUIRE_TOOLCHAIN=1'),
            volumes=((source, '/opt/agent-ee', True), (prepared, prepared, True), (output, output, False)),
            workdir='/opt/agent-ee', entrypoint='/usr/bin/python3')
        intent = Intent('benchmark', ['/opt/agent-ee/scripts/seeded_benchmark.py', *args], isolation='docker')
        # The literal argv benchmark_run.container_command built before it moved onto this adapter.
        self.assertEqual(adapter.argv(intent),
                         ['docker', 'run', '--rm', '--init', '--name', 'agent-ee-21-run', '--network', 'none',
                          '--user', f'{os.getuid()}:{os.getgid()}', '-e', 'HOME=/tmp', '-e', 'AGENT_EE_REQUIRE_TOOLCHAIN=1',
                          '--security-opt', 'seccomp=unconfined', '--security-opt', 'apparmor=unconfined',
                          '--security-opt', 'systempaths=unconfined', '-v', f'{source}:/opt/agent-ee:ro',
                          '-v', f'{prepared}:{prepared}:ro', '-v', f'{output}:{output}', '-w', '/opt/agent-ee',
                          '--entrypoint', '/usr/bin/python3', 'sha256:' + 'a' * 64,
                          '/opt/agent-ee/scripts/seeded_benchmark.py', *args])
        self.assertEqual(benchmark_run.container_command('agent-ee-21-run', 'sha256:' + 'a' * 64, source,
                                                         [(prepared, True), (output, False)], args),
                         adapter.argv(intent))

    def test_docker_refuses_other_intents(self):
        adapter = native_run.Docker(image='i', name='n')
        with self.assertRaises(ValueError):
            native_run.run(Intent('x', ['true']), inputs='/tmp', work=None, adapter=adapter)


class SandboxTest(unittest.TestCase):
    """The sandbox adapter's record is the executor's record, byte for byte."""

    @classmethod
    def setUpClass(cls):
        # Probed here, not at import, so the adapter tests that need no bwrap still run without it.
        problem = prerequisite_problem()
        if problem:
            raise unittest.SkipTest(problem)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        (self.root / 'inputs').mkdir()
        (self.root / 'inputs/data.txt').write_text('candidate\n')

    @staticmethod
    def stable(path):
        record = json.loads((path / 'execution.json').read_text())
        record.pop('duration_s')
        return record

    def test_records_are_byte_identical_to_executor(self):
        argv = ['/bin/sh', '-c', 'cat /candidate/data.txt; read line; echo "$line" >&2; exit 4']
        direct = executor.execute(self.root / 'inputs', self.root / 'direct', argv, timeout=30, stdin='x\n')
        intent = Intent('probe', argv, timeout=30, stdin='x\n')
        seam = native_run.run(intent, inputs=self.root / 'inputs', work=self.root / 'direct')
        self.assertEqual({k: v for k, v in seam.items() if k != 'duration_s'},
                         {k: v for k, v in direct.items() if k != 'duration_s'})
        self.assertEqual((seam['execution'], seam['exit_code']), ('failed', 4))
        self.assertEqual(seam.stdout, b'candidate\n')
        self.assertEqual(seam.stderr, b'x\n')
        self.assertEqual((self.root / 'direct/console.log').read_bytes(), b'candidate\nx\n')

    def test_baseline_mount_reaches_the_tool(self):
        baseline = self.root / 'baseline.json'
        baseline.write_text('{"baseline": 1}')
        record = native_run.run(Intent('probe', ['cat', '/evidence/baseline.json'], timeout=30),
                                inputs=self.root / 'inputs', work=self.root / 'w', baseline=baseline)
        self.assertEqual(record['execution'], 'ok')
        self.assertEqual(record.stdout, b'{"baseline": 1}')
        self.assertEqual(native_run.profile(record).bound('/evidence/baseline.json'), [str(baseline.resolve())])

    def test_bounded_run_matches_the_replay_view(self):
        bundle = self.root / 'bundle'
        bundle.mkdir()
        argv = ['/bin/sh', '-c', 'echo start; touch /work/x 2>/dev/null && echo writable; head -c 5000 /dev/zero']
        intent = Intent('replay', argv, timeout=30, remount_ro=('/work', '/tmp', '/dev', '/proc', '/'), log_bytes=200)
        record = native_run.run(intent, inputs=self.root / 'inputs', work=self.root / 'w',
                                mounts=((bundle, '/bundle'),))
        expected = executor.sandbox_argv(self.root / 'inputs', self.root / 'w', argv, extra_ro=((bundle, '/bundle'),))
        boundary = expected.index('--')
        expected[boundary:boundary] = [arg for path in ('/work', '/tmp', '/dev', '/proc', '/')
                                       for arg in ('--remount-ro', path)]
        self.assertEqual(record['sandbox_argv'], expected)
        self.assertEqual((record['execution'], record['logs_truncated']), ('output_limit', True))
        self.assertTrue(record.stdout.startswith(b'start\n'))
        self.assertNotIn(b'writable', record.stdout)
        self.assertEqual(len(record.stdout) + len(record.stderr), 200)
        parsed = native_run.profile(record)
        self.assertEqual(parsed.normalized(), historical_sandbox_profile(record['sandbox_argv']))
        self.assertEqual(json.loads((self.root / 'w/execution.json').read_text()), dict(record))

    def test_bounded_run_refuses_descriptor_mounts(self):
        source = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, source)
        with self.assertRaises(ValueError):
            native_run.run(Intent('replay', ['true'], log_bytes=10), inputs=self.root / 'inputs',
                           work=self.root / 'w', mounts=((source, '/tool'),))

    def test_sandbox_refuses_without_inputs_or_with_host_intents(self):
        with self.assertRaises(ValueError):
            native_run.run(Intent('probe', ['true']), work=self.root / 'w')
        with self.assertRaises(ValueError):
            native_run.run(Intent('probe', ['true'], isolation='host'), inputs=self.root / 'inputs',
                           work=self.root / 'w', adapter=native_run.Sandbox())


if __name__ == '__main__':
    unittest.main()
