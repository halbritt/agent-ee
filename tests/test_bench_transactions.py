"""Transaction harness evidence, not live inference or physical validation."""
import contextlib
import io
from pathlib import Path

import unittest
import test_campaign as fixture
from toolchain import toolchain_test
from agent_ee.cli import run_review, verify
from agent_ee.storage import digest, read_json, write_json


class BenchTransactionTests(unittest.TestCase):
    setUp = fixture.CampaignIntegrationTests.setUp
    agent = fixture.CampaignIntegrationTests.agent
    # Reuse the actual producer/parser fixture and independent review protocol.
    def initial(self):
        write_json(self.root / 'source/design.json', {'value': 5})
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_review(self.root / 'scope.json', self.root / 'initial', 60), 0)
        sample = {'check_id': 'response', 'metric': 'output', 'value': 12,
                  'uncertainty': 1, 'unit': 'V', 'condition': 'load'}
        write_json(self.root / 'raw.json', {'samples': [sample]})
        write_json(self.root / 'bench.json', {'schema': 'agent-ee.bench/1',
            'candidate_id': read_json(self.root / 'initial/candidate.json')['candidate_id'],
            'prototype': 'synthetic', 'instrument': 'synthetic', 'instrument_calibration': 'synthetic',
            'procedure': 'coordinator harness', 'observed_utc': '2026-10-17',
            'artifacts': [{'path': 'raw.json', 'sha256': digest(self.root / 'raw.json')}],
            'observations': [{**sample, 'artifact': 'raw.json', 'locator': '0'}]})
        self.calls.clear()

    @toolchain_test
    def test_same_request_returns_completed_transaction_without_repeating_review(self):
        from agent_ee.bench_transactions import transact, recover
        self.initial()
        with contextlib.redirect_stdout(io.StringIO()):
            first = transact(self.root / 'initial', self.root / 'bench.json', self.root / 'feedback',
                             60, operation_id='bench-1')
            again = transact(self.root / 'initial', self.root / 'bench.json', self.root / 'feedback',
                             60, operation_id='bench-1')
        self.assertEqual(first, again)
        self.assertEqual(first['phase'], 'completed')
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(recover(self.root / 'feedback')['phase'], 'completed')
        source = verify(self.root / 'initial', current_source=False)
        self.assertEqual(source['outcome'], 'incomplete')
        self.assertEqual(len(source['empirical_feedback']), 1)

    @toolchain_test
    def test_committed_intent_refuses_stale_acceptance_then_recovers_without_inference(self):
        from unittest.mock import patch
        from agent_ee import bench_transactions as tx
        self.initial()
        real_put = tx.put
        def interrupt(path, value):
            real_put(path, value)
            if Path(path).parent.name == 'bench-publications':
                raise KeyboardInterrupt()
        with patch.object(tx, 'put', side_effect=interrupt), self.assertRaises(KeyboardInterrupt):
            tx.transact(self.root / 'initial', self.root / 'bench.json', self.root / 'feedback',
                        60, operation_id='interrupted')
        with self.assertRaisesRegex(ValueError, 'explicit recovery'):
            verify(self.root / 'initial', current_source=False)
        recovered = tx.recover(self.root / 'feedback')
        self.assertEqual(recovered['disposition'], 'ready_for_explicit_review')
        self.assertEqual(self.calls, [])
        self.assertEqual(verify(self.root / 'initial', current_source=False)['outcome'], 'incomplete')
        with contextlib.redirect_stdout(io.StringIO()):
            complete = tx.recover(self.root / 'feedback', launch_review=True)
        self.assertEqual(complete['phase'], 'completed')
        self.assertEqual(len(self.calls), 3)

    @toolchain_test
    def test_uncertain_start_is_never_replayed_and_new_attempt_is_explicit(self):
        from unittest.mock import patch
        from agent_ee import bench_transactions as tx
        self.initial()
        with patch('agent_ee.cli.run_review', side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            tx.transact(self.root / 'initial', self.root / 'bench.json', self.root / 'feedback',
                        60, operation_id='uncertain')
        before = tx.inventory(self.root / 'feedback')
        diagnosis = tx.status(self.root / 'feedback')
        self.assertEqual(diagnosis['disposition'], 'uncertain_execution')
        self.assertEqual(before, tx.inventory(self.root / 'feedback'))
        for _ in range(2):
            self.assertEqual(tx.recover(self.root / 'feedback', launch_review=True)['disposition'], 'uncertain_execution')
        self.assertEqual(self.calls, [])
        with contextlib.redirect_stdout(io.StringIO()):
            value = tx.new_attempt(self.root / 'feedback', 'operator-2', self.root / 'fresh-review')
        self.assertEqual(value['phase'], 'completed')
        self.assertEqual(value['review']['attempt_id'], 'operator-2')
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(len(verify(self.root / 'initial', current_source=False)['empirical_feedback']), 1)

    @toolchain_test
    def test_replacement_attempts_preserve_all_retained_trees_on_overlap_refusal(self):
        from unittest.mock import patch
        from agent_ee import bench_transactions as tx
        self.initial()
        out = self.root / 'feedback'
        older = self.root / 'older-parent/review'
        middle = self.root / 'middle-parent/review'
        latest = self.root / 'latest-parent/review'

        def interrupted_review(scope, target, *args, **kwargs):
            (target / 'checks/empty').mkdir(parents=True)
            (target / 'checks/raw.bin').write_bytes(b'\x00retained evidence\xff')
            raise KeyboardInterrupt()

        with patch('agent_ee.cli.run_review', side_effect=interrupted_review):
            with self.assertRaises(KeyboardInterrupt):
                tx.transact(self.root / 'initial', self.root / 'bench.json', out,
                            60, operation_id='overlap', review_out=older)
            with self.assertRaises(KeyboardInterrupt):
                tx.new_attempt(out, 'second', middle)
            with self.assertRaises(KeyboardInterrupt):
                tx.new_attempt(out, 'third', latest)

        def snapshot():
            return {p.relative_to(self.root).as_posix(): p.read_bytes() if p.is_file() else None
                    for p in self.root.rglob('*')}

        foreign = self.root / 'foreign-review'
        foreign.mkdir()
        (foreign / 'owner-data').write_bytes(b'foreign output')
        before = snapshot()
        with patch('agent_ee.cli.run_review', side_effect=AssertionError('refusal launched inference')):
            for retained in (older, middle, latest, self.root / 'initial', out):
                for target in (retained, retained.parent, retained / 'checks/new-review',
                               retained / 'absent-parent/new-review'):
                    with self.subTest(retained=retained, target=target):
                        with self.assertRaisesRegex(tx.TransactionError, 'overlaps retained evidence'):
                            tx.new_attempt(out, 'forbidden', target)
                        self.assertEqual(snapshot(), before)
            with self.assertRaisesRegex(tx.TransactionError, 'fresh run directory'):
                tx.new_attempt(out, 'foreign', foreign)
            self.assertEqual(snapshot(), before)
        with contextlib.redirect_stdout(io.StringIO()):
            value = tx.new_attempt(out, 'fresh-sibling', self.root / 'fresh-review')
        self.assertEqual(value['phase'], 'completed')
        self.assertEqual(value['review']['attempt_id'], 'fresh-sibling')
        self.assertEqual(len(self.calls), 3)
        for retained in (older, middle, latest):
            prefix = retained.relative_to(self.root).as_posix() + '/'
            self.assertEqual({k: v for k, v in snapshot().items() if k.startswith(prefix)},
                             {k: v for k, v in before.items() if k.startswith(prefix)})
        self.assertEqual(len(verify(self.root / 'initial', current_source=False)['empirical_feedback']), 1)

    @toolchain_test
    def test_busy_transaction_refuses_new_attempt_before_creating_target_lease(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        from unittest.mock import patch
        from agent_ee import bench_transactions as tx
        from agent_ee.locking import writer_lock
        self.initial()
        out = self.root / 'feedback'
        with patch('agent_ee.cli.run_review', side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            tx.transact(self.root / 'initial', self.root / 'bench.json', out,
                        60, operation_id='busy-attempt', review_out=self.root / 'external-review')
        entered, release = threading.Event(), threading.Event()

        def competing_writer():
            with writer_lock(out):
                entered.set()
                if not release.wait(15):
                    raise RuntimeError('test barrier timed out')

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(competing_writer)
            try:
                self.assertTrue(entered.wait(15))
                # This sorts before the journal, exposing premature lease creation.
                target = self.root / 'aaa-new-parent/review'
                before = {p.relative_to(self.root).as_posix(): p.read_bytes() if p.is_file() else None
                          for p in self.root.rglob('*')}
                with self.assertRaisesRegex(ValueError, 'another writer'):
                    tx.new_attempt(out, 'contender', target)
                after = {p.relative_to(self.root).as_posix(): p.read_bytes() if p.is_file() else None
                         for p in self.root.rglob('*')}
                self.assertEqual(after, before)
            finally:
                release.set()
            future.result(timeout=15)
        self.assertEqual(tx.recover(out)['disposition'], 'uncertain_execution')
        self.assertEqual(self.calls, [])

    @toolchain_test
    def test_changed_request_conflicts_and_equivalent_operation_returns_existing_identity(self):
        from unittest.mock import patch
        from agent_ee import bench_transactions as tx
        self.initial()
        with patch('agent_ee.cli.run_review', side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            tx.transact(self.root / 'initial', self.root / 'bench.json', self.root / 'feedback',
                        60, operation_id='same')
        same = tx.transact(self.root / 'initial', self.root / 'bench.json', self.root / 'other',
                           60, operation_id='equivalent')
        self.assertEqual(same['disposition'], 'existing_operation')
        self.assertEqual(same['operation_id'], 'same')
        before = tx.inventory(self.root / 'initial'), tx.inventory(self.root / 'feedback')
        for field in ('max_seconds', 'out', 'report'):
            args = {'run': self.root / 'initial', 'report': self.root / 'bench.json',
                    'out': self.root / 'feedback', 'max_seconds': 60, 'operation_id': 'same'}
            if field == 'max_seconds':
                args[field] = 61
            elif field == 'out':
                args[field] = self.root / 'other'
            else:
                report = read_json(args[field]); report['procedure'] = 'different'
                write_json(self.root / 'changed.json', report); args[field] = self.root / 'changed.json'
            with self.subTest(field=field), self.assertRaises(tx.TransactionError):
                tx.transact(**args)
        self.assertEqual(before, (tx.inventory(self.root / 'initial'), tx.inventory(self.root / 'feedback')))
        self.assertEqual(self.calls, [])

    @toolchain_test
    def test_completed_review_adopted_after_missing_completion_receipt(self):
        from unittest.mock import patch
        from agent_ee import bench_transactions as tx
        self.initial()
        real_receipt = tx.receipt
        def interrupt(out, phase, payload):
            if phase == 'completed':
                raise KeyboardInterrupt()
            return real_receipt(out, phase, payload)
        with patch.object(tx, 'receipt', side_effect=interrupt), self.assertRaises(KeyboardInterrupt):
            with contextlib.redirect_stdout(io.StringIO()):
                tx.transact(self.root / 'initial', self.root / 'bench.json', self.root / 'feedback',
                            60, operation_id='adopt')
        self.assertEqual(len(self.calls), 3)
        value = tx.recover(self.root / 'feedback')
        self.assertEqual(value['phase'], 'completed')
        self.assertEqual(len(self.calls), 3)

    @toolchain_test
    def test_campaign_completion_reconciles_one_epoch_and_retains_original_snapshot(self):
        from unittest.mock import patch
        from agent_ee import bench_transactions as tx
        from agent_ee.campaign import run_campaign, verify_campaign
        self.initial()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_campaign(self.root / 'campaign.json', self.root / 'campaign'), 0)
        source = self.root / 'campaign/runs/1'
        report = read_json(self.root / 'bench.json')
        report['candidate_id'] = read_json(source / 'candidate.json')['candidate_id']
        write_json(self.root / 'bench.json', report)
        original_snapshot = digest(self.root / 'campaign/history/1.json')
        real_receipt = tx.receipt
        def interrupt(out, phase, payload):
            if phase == 'completed':
                raise KeyboardInterrupt()
            return real_receipt(out, phase, payload)
        with patch.object(tx, 'receipt', side_effect=interrupt), self.assertRaises(KeyboardInterrupt):
            with contextlib.redirect_stdout(io.StringIO()):
                tx.campaign_transaction(self.root / 'campaign', self.root / 'bench.json',
                                        self.root / 'feedback', 60, operation_id='campaign-feedback')
        count = len(self.calls)
        value = tx.recover(self.root / 'feedback')
        self.assertEqual(value['phase'], 'completed')
        state = verify_campaign(self.root / 'campaign')
        self.assertEqual(state['epoch'], 1)
        self.assertEqual(verify(Path(state['history'][-1]['run']), current_source=False)['max_schema_resubmissions'], 1)
        self.assertEqual(len(state['history']), 2)
        self.assertEqual(len(self.calls), count)
        self.assertEqual(digest(self.root / 'campaign/history/1.json'), original_snapshot)
        self.assertEqual(tx.recover(self.root / 'feedback')['phase'], 'completed')

    @toolchain_test
    def test_prepared_corruption_and_foreign_event_fail_closed(self):
        from unittest.mock import patch
        from agent_ee import bench_transactions as tx
        self.initial()
        with patch.object(tx, 'publish', side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            tx.transact(self.root / 'initial', self.root / 'bench.json', self.root / 'feedback',
                        60, operation_id='prepared')
        self.assertEqual(verify(self.root / 'initial', current_source=False)['outcome'], 'criteria_satisfied')
        bundle = self.root / 'feedback/prepared/empirical/calibration.json'
        bundle.write_text('{}')
        with self.assertRaisesRegex(tx.TransactionError, 'prepared evidence changed'):
            tx.recover(self.root / 'feedback')
        self.assertEqual(self.calls, [])
        self.assertFalse((self.root / 'initial/bench-publications').exists())

    @toolchain_test
    def test_supported_concurrent_writers_and_readers_use_the_complete_lease(self):
        import multiprocessing
        from unittest.mock import patch
        from agent_ee import bench_transactions as tx
        self.initial()
        context = multiprocessing.get_context('fork')
        entered, release = context.Event(), context.Event()
        result = context.Queue()
        real_prepare = tx.prepare
        def paused(*args):
            entered.set()
            if not release.wait(15):
                raise RuntimeError('test barrier timed out')
            return real_prepare(*args)
        def worker():
            try:
                with patch.object(tx, 'prepare', side_effect=paused), patch('agent_ee.cli.run_review', side_effect=KeyboardInterrupt):
                    tx.transact(self.root / 'initial', self.root / 'bench.json', self.root / 'feedback',
                                60, operation_id='writer', review_out=self.root / 'external-review')
            except KeyboardInterrupt:
                result.put('started')
            except Exception as exc:
                result.put(repr(exc))
        process = context.Process(target=worker)
        process.start()
        try:
            self.assertTrue(entered.wait(15))
            with self.assertRaisesRegex(ValueError, 'another writer'):
                tx.transact(self.root / 'initial', self.root / 'bench.json', self.root / 'other',
                            60, operation_id='writer-2')
            with self.assertRaisesRegex(ValueError, 'coherent read unavailable'):
                verify(self.root / 'initial', current_source=False)
            from agent_ee.locking import writer_lock
            with self.assertRaisesRegex(ValueError, 'another writer'):
                with writer_lock(self.root / 'external-review'):
                    self.fail('external target not leased')
        finally:
            release.set()
            process.join(20)
            if process.is_alive():
                process.kill(); process.join()
        self.assertEqual(process.exitcode, 0)
        self.assertEqual(result.get(timeout=2), 'started')
        self.assertEqual(tx.recover(self.root / 'feedback')['disposition'], 'uncertain_execution')
        self.assertEqual(len(verify(self.root / 'initial', current_source=False)['empirical_feedback']), 1)

    @toolchain_test
    def test_process_death_before_and_after_each_prelaunch_fsync_and_rename(self):
        import multiprocessing
        import os
        import shutil
        from unittest.mock import patch
        from agent_ee import bench_transactions as tx
        self.initial()
        source, out = self.root / 'initial', self.root / 'feedback'
        template = self.root / 'original-run'
        shutil.copytree(source, template)
        trace = []
        real_fsync, real_replace = os.fsync, Path.replace
        def observed_fsync(fd):
            trace.append(('fsync', os.readlink(f'/proc/self/fd/{fd}')))
            return real_fsync(fd)
        def observed_replace(path, target):
            trace.append(('rename', str(target)))
            return real_replace(path, target)
        with patch('os.fsync', side_effect=observed_fsync), patch.object(Path, 'replace', observed_replace), \
                patch('agent_ee.cli.run_review', side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            tx.transact(source, self.root / 'bench.json', out, 60, operation_id='fault-matrix')
        self.assertGreater(len(trace), 20)
        print(f'Prelaunch crash boundaries: {len(trace)}; process-death cases: {2 * len(trace)}', flush=True)
        context = multiprocessing.get_context('fork')
        for index, point in enumerate(trace):
            for edge in ('before', 'after'):
                with self.subTest(index=index, point=point, edge=edge):
                    shutil.rmtree(source); shutil.copytree(template, source)
                    shutil.rmtree(out)
                    def crash_worker():
                        count = 0
                        def checkpoint(call, *args):
                            nonlocal count
                            here = count
                            count += 1
                            if here == index and edge == 'before':
                                os._exit(77)
                            result = call(*args)
                            if here == index and edge == 'after':
                                os._exit(77)
                            return result
                        def fsync(fd):
                            return checkpoint(real_fsync, fd)
                        def replace(path, target):
                            return checkpoint(real_replace, path, target)
                        with patch('os.fsync', side_effect=fsync), patch.object(Path, 'replace', replace), \
                                patch('agent_ee.cli.run_review', side_effect=AssertionError('unexpected provider boundary')):
                            tx.transact(source, self.root / 'bench.json', out, 60, operation_id='fault-matrix')
                        os._exit(99)
                    worker = context.Process(target=crash_worker)
                    worker.start(); worker.join(15)
                    if worker.is_alive():
                        worker.kill(); worker.join()
                    self.assertEqual(worker.exitcode, 77)
                    parent, child = context.Pipe(False)
                    def recovery_worker():
                        with patch('agent_ee.cli.run_review', side_effect=AssertionError('recovery repeated inference')):
                            try:
                                result = tx.recover(out)
                            except (ValueError, OSError, KeyError) as exc:
                                result = {'disposition': 'refused', 'reason': str(exc)}
                            try:
                                original = verify(source, current_source=False)
                                result['source_outcome'] = original['outcome']
                            except (ValueError, OSError, KeyError) as exc:
                                result['source_outcome'] = 'unverified'
                                result['source_error'] = str(exc)
                            child.send(result)
                    recovery = context.Process(target=recovery_worker)
                    recovery.start(); recovery.join(15)
                    if recovery.is_alive():
                        recovery.kill(); recovery.join()
                    self.assertEqual(recovery.exitcode, 0)
                    result = parent.recv()
                    if (source / 'bench-publications').exists():
                        self.assertNotEqual(result['source_outcome'], 'criteria_satisfied', result)
                    if result['disposition'] not in ('refused', 'uncertain_execution'):
                        self.assertEqual(result['disposition'], 'ready_for_explicit_review', result)
                        self.assertEqual(result['source_outcome'], 'incomplete')
                    parent.close(); child.close()
        self.assertEqual(self.calls, [])

    @toolchain_test
    def test_captured_parent_closure_survives_interrupted_second_epoch(self):
        from unittest.mock import patch
        from agent_ee import bench_transactions as tx
        self.initial()
        with contextlib.redirect_stdout(io.StringIO()):
            first = tx.transact(self.root / 'initial', self.root / 'bench.json', self.root / 'first',
                                60, operation_id='first-epoch')
        source = Path(first['review']['out'])
        report = read_json(self.root / 'bench.json')
        report['candidate_id'] = first['review']['candidate_id']
        report['observed_utc'] = '2026-10-18'
        write_json(self.root / 'bench2.json', report)
        real_put = tx.put
        def interrupt(path, value):
            real_put(path, value)
            if Path(path).parent.name == 'bench-publications':
                raise KeyboardInterrupt()
        with patch.object(tx, 'put', side_effect=interrupt), self.assertRaises(KeyboardInterrupt):
            tx.transact(source, self.root / 'bench2.json', self.root / 'second', 60, operation_id='second-epoch')
        bundle_path = self.root / 'second/prepared/empirical/calibration.json'
        bundle_hash = digest(bundle_path)
        count = len(self.calls)
        for _ in range(2):
            tx.recover(self.root / 'second')
        self.assertEqual(digest(bundle_path), bundle_hash)
        self.assertEqual(len(self.calls), count)
        bundle = read_json(bundle_path)
        self.assertIsNotNone(bundle['parent'])
        self.assertEqual(len(bundle['observations']), 2)
        self.assertEqual(len(verify(source, current_source=False)['empirical_feedback']), 1)
        self.assertEqual(tx.recover(self.root / 'first')['phase'], 'completed')
        with self.assertRaisesRegex(ValueError, 'superseded'):
            from agent_ee.empirical import ingest
            ingest(source, self.root / 'bench2.json', self.root / 'unsafe-retry')
        self.assertFalse((self.root / 'unsafe-retry').exists())

    @toolchain_test
    def test_explicit_campaign_attempt_preserves_partial_run_and_history_verifies(self):
        from unittest.mock import patch
        from agent_ee import bench_transactions as tx
        from agent_ee.campaign import run_campaign, verify_campaign
        self.initial()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_campaign(self.root / 'campaign.json', self.root / 'campaign'), 0)
        source = self.root / 'campaign/runs/1'
        report = read_json(self.root / 'bench.json')
        report['candidate_id'] = read_json(source / 'candidate.json')['candidate_id']
        write_json(self.root / 'bench.json', report)
        def uncertain(scope, target, *args, **kwargs):
            Path(target).mkdir()
            (Path(target) / 'partial-provider-output').write_text('retained uncertain evidence')
            raise KeyboardInterrupt()
        with patch('agent_ee.cli.run_review', side_effect=uncertain), self.assertRaises(KeyboardInterrupt):
            tx.campaign_transaction(self.root / 'campaign', self.root / 'bench.json', self.root / 'feedback',
                                    60, operation_id='uncertain-campaign')
        with contextlib.redirect_stdout(io.StringIO()):
            value = tx.new_attempt(self.root / 'feedback', 'replacement', self.root / 'campaign/attempts/replacement')
        self.assertEqual(value['phase'], 'completed')
        self.assertTrue((self.root / 'campaign/runs/2/partial-provider-output').is_file())
        state = verify_campaign(self.root / 'campaign')
        self.assertEqual(state['epoch'], 1)
        self.assertEqual(verify(Path(state['history'][-1]['run']), current_source=False)['max_schema_resubmissions'], 1)
        self.assertEqual(len(state['history']), 2)
        self.assertEqual(state['history'][-1]['bench_attempt_id'], 'replacement')

    @toolchain_test
    def test_campaign_commitpoint_death_recovers_exactly_one_history_entry(self):
        import multiprocessing
        import os
        import shutil
        from unittest.mock import patch
        from agent_ee import bench_transactions as tx
        from agent_ee.campaign import run_campaign, verify_campaign
        self.initial()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_campaign(self.root / 'campaign.json', self.root / 'campaign'), 0)
        source = self.root / 'campaign/runs/1'
        report = read_json(self.root / 'bench.json')
        report['candidate_id'] = read_json(source / 'candidate.json')['candidate_id']
        write_json(self.root / 'bench.json', report)
        with patch.object(tx, 'finish_campaign', side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            tx.campaign_transaction(self.root / 'campaign', self.root / 'bench.json', self.root / 'feedback',
                                    60, operation_id='history-crash')
        saved_campaign, saved_feedback = self.root / 'saved-campaign', self.root / 'saved-feedback'
        shutil.copytree(self.root / 'campaign', saved_campaign)
        shutil.copytree(self.root / 'feedback', saved_feedback)
        original_hash = digest(self.root / 'campaign/history/1.json')
        trace = []
        real_fsync, real_replace = os.fsync, Path.replace
        selected = [str(self.root / 'campaign/history/2.json'), str(self.root / 'campaign/state.json'),
                    str(self.root / 'feedback/campaign-intent.json'), str(self.root / 'feedback/completed.json')]
        selected_directories = {str(self.root / 'campaign'), str(self.root / 'campaign/history'),
                                str(self.root / 'feedback')}
        def selected_file(path):
            return str(path).removesuffix('.tmp') in selected or str(path) in selected_directories
        def observed_fsync(fd):
            if selected_file(os.readlink(f'/proc/self/fd/{fd}')):
                trace.append('fsync')
            return real_fsync(fd)
        def observed_replace(path, target):
            if selected_file(target):
                trace.append('rename')
            return real_replace(path, target)
        with patch('os.fsync', side_effect=observed_fsync), patch.object(Path, 'replace', observed_replace):
            self.assertEqual(tx.recover(self.root / 'feedback')['phase'], 'completed')
        self.assertGreaterEqual(len(trace), 8)
        self.assertEqual(trace.count('rename'), 4)
        print(f'Completion/history crash boundaries: {len(trace)}; process-death cases: {2 * len(trace)}', flush=True)
        context = multiprocessing.get_context('fork')
        count = len(self.calls)
        for index in range(len(trace)):
            for edge in ('before', 'after'):
                with self.subTest(index=index, edge=edge):
                    for current, saved in ((self.root / 'campaign', saved_campaign), (self.root / 'feedback', saved_feedback)):
                        shutil.rmtree(current); shutil.copytree(saved, current)
                    def crash_worker():
                        counter = 0
                        def checkpoint(call, selected, *args):
                            nonlocal counter
                            here = counter
                            if selected:
                                counter += 1
                                if here == index and edge == 'before':
                                    os._exit(77)
                            value = call(*args)
                            if selected and here == index and edge == 'after':
                                os._exit(77)
                            return value
                        def fsync(fd):
                            return checkpoint(real_fsync, selected_file(os.readlink(f'/proc/self/fd/{fd}')), fd)
                        def replace(path, target):
                            return checkpoint(real_replace, selected_file(target), path, target)
                        with patch('os.fsync', side_effect=fsync), patch.object(Path, 'replace', replace), \
                                patch('agent_ee.cli.run_review', side_effect=AssertionError('recovery spent inference')):
                            tx.recover(self.root / 'feedback')
                        os._exit(99)
                    worker = context.Process(target=crash_worker)
                    worker.start(); worker.join(15)
                    if worker.is_alive():
                        worker.kill(); worker.join()
                    self.assertEqual(worker.exitcode, 77)
                    with patch('agent_ee.cli.run_review', side_effect=AssertionError('recovery spent inference')):
                        self.assertEqual(tx.recover(self.root / 'feedback')['phase'], 'completed')
                    state = verify_campaign(self.root / 'campaign')
                    self.assertEqual(len(state['history']), 2)
                    self.assertEqual(state['epoch'], 1)
                    self.assertEqual(verify(Path(state['history'][-1]['run']), current_source=False)['max_schema_resubmissions'], 1)
                    self.assertEqual(digest(self.root / 'campaign/history/1.json'), original_hash)
        self.assertEqual(len(self.calls), count)

    @toolchain_test
    def test_successful_calibrated_review_requires_fresh_checks_and_both_initial_reviews(self):
        from agent_ee import bench_transactions as tx
        self.initial()
        report = read_json(self.root / 'bench.json')
        sample = {k: v for k, v in report['observations'][0].items() if k not in ('artifact', 'locator')}
        sample.update(value=6, uncertainty=.1)
        write_json(self.root / 'raw.json', {'samples': [sample]})
        report['observations'] = [{**sample, 'artifact': 'raw.json', 'locator': '0'}]
        report['artifacts'][0]['sha256'] = digest(self.root / 'raw.json')
        write_json(self.root / 'bench.json', report)
        value = tx.transact(self.root / 'initial', self.root / 'bench.json', self.root / 'feedback',
                            60, operation_id='passing-review')
        self.assertEqual(value['phase'], 'completed')
        self.assertEqual(value['review']['outcome'], 'criteria_satisfied')
        reviewed = verify(value['review']['out'])
        self.assertEqual(len(reviewed['reviews']), 2)
        self.assertTrue(reviewed['adjudicated'])
        self.assertEqual(reviewed['checks'][0]['execution'], 'ok')
        self.assertEqual(reviewed['checks'][0]['outcome'], 'pass')
        self.assertEqual(verify(self.root / 'initial', current_source=False)['outcome'], 'incomplete')

    @toolchain_test
    def test_failed_provider_settles_incomplete_and_recovery_cannot_retry_it(self):
        from unittest.mock import patch
        from agent_ee import bench_transactions as tx
        self.initial()
        with patch('agent_ee.reviewers.dispatch', side_effect=lambda *args, **kwargs: ({'execution': 'error', 'error': 'scripted subscription failure'}, None)) as dispatch:
            value = tx.transact(self.root / 'initial', self.root / 'bench.json', self.root / 'feedback',
                                60, operation_id='provider-failure')
            self.assertEqual(value['phase'], 'completed')
            self.assertEqual(value['review']['outcome'], 'incomplete')
            count = dispatch.call_count
            self.assertEqual(count, 2)
            self.assertEqual(tx.recover(self.root / 'feedback')['phase'], 'completed')
            self.assertEqual(dispatch.call_count, count)
        self.assertEqual(verify(self.root / 'initial', current_source=False)['outcome'], 'incomplete')

    @toolchain_test
    def test_actual_sigint_during_scripted_provider_preserves_supersession_and_cleans_children(self):
        import multiprocessing
        import os
        import signal
        import time
        from unittest.mock import patch
        from agent_ee import bench_transactions as tx
        self.initial()
        context = multiprocessing.get_context('fork')
        queue = context.Queue()
        def worker():
            def sleeping(run, role, definition, prompt, schema, timeout, **kwargs):
                self.assertNotEqual(role, 'adjudicator')
                from agent_ee.executor import execute
                kwargs.pop('executable', None)
                work = Path(run) / 'review-work' / role
                script = ("import subprocess,time; from pathlib import Path; "
                          "subprocess.Popen(['/usr/bin/sleep','60']); Path('started').write_text('yes'); "
                          "print('partial provider output',flush=True); time.sleep(60)")
                return execute(Path(run) / 'inputs', work, ['/usr/bin/python3', '-c', script],
                               timeout=timeout, **kwargs), None
            with patch('agent_ee.reviewers.dispatch', side_effect=sleeping):
                value = tx.transact(self.root / 'initial', self.root / 'bench.json', self.root / 'feedback',
                                    60, operation_id='signal')
            queue.put(value)
        process = context.Process(target=worker)
        process.start()
        target = self.root / 'feedback/review'
        deadline = time.monotonic() + 20
        while len(list(target.glob('review-work/*/started'))) < 2:
            self.assertTrue(process.is_alive(), 'transaction exited before dispatch')
            if time.monotonic() > deadline:
                process.kill(); process.join()
                self.fail('scripted providers did not reach barrier')
            time.sleep(.02)
        def descendants(pid):
            children = [int(child) for task in Path(f'/proc/{pid}/task').glob('*/children')
                        for child in task.read_text().split()]
            return children + [nested for child in children for nested in descendants(child)]
        children = descendants(process.pid)
        self.assertGreaterEqual(len(children), 4)
        os.kill(process.pid, signal.SIGINT)
        process.join(5)
        if process.is_alive():
            for pid in reversed(children):
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.kill(); process.join()
            self.fail('transaction cancellation exceeded cleanup bound')
        self.assertEqual(process.exitcode, 0)
        value = queue.get(timeout=2)
        self.assertEqual(value['phase'], 'completed')
        self.assertEqual(value['review']['state'], 'interrupted')
        self.assertEqual(value['review']['outcome'], 'incomplete')
        for pid in children:
            stat = Path(f'/proc/{pid}/stat')
            self.assertTrue(not stat.exists() or stat.read_text().split()[2] == 'Z', f'child {pid} survived')
        self.assertEqual(verify(self.root / 'initial', current_source=False)['outcome'], 'incomplete')
        self.assertEqual(tx.recover(self.root / 'feedback')['phase'], 'completed')
        self.assertEqual(self.calls, [])

    @toolchain_test
    def test_semantically_equal_foreign_event_bytes_are_a_conflict_before_publication(self):
        import json
        from unittest.mock import patch
        from agent_ee import bench_transactions as tx
        from agent_ee.storage import identity
        self.initial()
        with patch.object(tx, 'publish', side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            tx.transact(self.root / 'initial', self.root / 'bench.json', self.root / 'feedback',
                        60, operation_id='foreign-event')
        request = read_json(self.root / 'feedback/request.json')
        event = tx.event_for(self.root / 'feedback', request)
        path = self.root / 'initial/events' / ('bench-' + identity({'operation_id': 'foreign-event'}) + '.json')
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(event) + '\n')
        original = digest(self.root / 'initial/result.json')
        with self.assertRaisesRegex(tx.TransactionError, 'conflict|foreign'):
            tx.recover(self.root / 'feedback')
        self.assertEqual(digest(self.root / 'initial/result.json'), original)
        self.assertFalse((self.root / 'initial/bench-publications').exists())
        self.assertEqual(self.calls, [])

    @toolchain_test
    def test_terminal_receipt_cannot_promote_a_failed_review_to_acceptance(self):
        from agent_ee import bench_transactions as tx
        self.initial()
        value = tx.transact(self.root / 'initial', self.root / 'bench.json', self.root / 'feedback',
                            60, operation_id='receipt-validation')
        self.assertEqual(value['review']['outcome'], 'defects_or_unresolved')
        path = self.root / 'feedback/completed.json'
        receipt = read_json(path)
        receipt['review']['outcome'] = 'criteria_satisfied'
        write_json(path, receipt)
        with self.assertRaisesRegex(tx.TransactionError, 'contradicts retained review'):
            tx.recover(self.root / 'feedback')
        self.assertEqual(tx.status(self.root / 'feedback')['disposition'], 'conflict')
        self.assertEqual(verify(self.root / 'feedback/review')['outcome'], 'defects_or_unresolved')

    def test_legacy_partial_diagnosis_is_read_only_and_cannot_convert_itself(self):
        from agent_ee import bench_transactions as tx
        out = self.root / 'legacy'
        out.mkdir()
        (out / 'partial-output').write_text('legacy evidence')
        before = tx.inventory(out)
        self.assertEqual(tx.status(out)['disposition'], 'unverified')
        with self.assertRaisesRegex(tx.TransactionError, 'no valid opt-in transaction'):
            tx.recover(out)
        self.assertEqual(tx.inventory(out), before)

    @toolchain_test
    def test_settled_transaction_distinguishes_historical_acceptance_from_current_supersession(self):
        from unittest.mock import patch
        from agent_ee import bench_transactions as tx
        self.initial()
        report = read_json(self.root / 'bench.json')
        sample = {k: v for k, v in report['observations'][0].items() if k not in ('artifact', 'locator')}
        sample.update(value=6, uncertainty=.1)
        write_json(self.root / 'raw.json', {'samples': [sample]})
        report['observations'] = [{**sample, 'artifact': 'raw.json', 'locator': '0'}]
        report['artifacts'][0]['sha256'] = digest(self.root / 'raw.json')
        write_json(self.root / 'bench.json', report)
        first = tx.transact(self.root / 'initial', self.root / 'bench.json', self.root / 'first',
                            60, operation_id='historical-pass')
        self.assertEqual(first['review']['outcome'], 'criteria_satisfied')
        self.assertEqual(first['current_review']['outcome'], 'criteria_satisfied')
        report['candidate_id'] = first['review']['candidate_id']
        report['observed_utc'] = '2026-10-18'
        write_json(self.root / 'second-report.json', report)
        with patch('agent_ee.cli.run_review', side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            tx.transact(first['review']['out'], self.root / 'second-report.json', self.root / 'second',
                        60, operation_id='later-feedback')
        current = tx.recover(self.root / 'first')
        self.assertEqual(current['phase'], 'completed')
        self.assertEqual(current['review']['outcome'], 'criteria_satisfied')
        self.assertEqual(current['current_review']['outcome'], 'incomplete')
        self.assertTrue(current['current_review']['empirical_superseded'])
        self.assertEqual(tx.status(self.root / 'first')['current_review']['outcome'], 'incomplete')

    def test_legacy_empty_output_is_refused_before_creating_internal_target_lease(self):
        from agent_ee import bench_transactions as tx
        from agent_ee.empirical import bench_review
        out = self.root / 'legacy-empty'
        out.mkdir()
        before = tx.inventory(out)
        with self.assertRaisesRegex(ValueError, 'legacy partial directories require diagnosis'):
            bench_review(self.root / 'absent-source', self.root / 'absent-report', out, 60)
        self.assertEqual(tx.inventory(out), before)

    @toolchain_test
    def test_concurrent_review_progress_is_per_call_and_does_not_replace_global_stdout(self):
        import sys
        from concurrent.futures import ThreadPoolExecutor
        streams = [io.StringIO(), io.StringIO()]
        targets = [self.root / 'parallel-a', self.root / 'parallel-b']
        original_stdout = sys.stdout
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(run_review, self.root / 'scope.json', target, 60,
                                   output_stream=stream) for target, stream in zip(targets, streams)]
            self.assertEqual([future.result() for future in futures], [1, 1])
        self.assertIs(sys.stdout, original_stdout)
        for index, stream in enumerate(streams):
            self.assertIn(str(targets[index] / 'REPORT.md'), stream.getvalue())
            self.assertNotIn(str(targets[1-index] / 'REPORT.md'), stream.getvalue())
            self.assertEqual(verify(targets[index])['outcome'], 'defects_or_unresolved')

    @toolchain_test
    def test_transaction_cli_returns_one_json_document_and_routes_progress_to_stderr(self):
        import json
        from agent_ee.cli import main
        self.initial()
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(['bench-review', str(self.root / 'initial'), str(self.root / 'bench.json'),
                         '--out', str(self.root / 'feedback'), '--operation-id', 'json-command',
                         '--max-seconds', '60'])
        self.assertEqual(code, 1)
        value = json.loads(stdout.getvalue())
        self.assertEqual(value['phase'], 'completed')
        self.assertEqual(value['current_review']['outcome'], 'defects_or_unresolved')
        self.assertIn('defects_or_unresolved:', stderr.getvalue())
