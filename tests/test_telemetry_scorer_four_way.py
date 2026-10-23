"""Offline correctness and stop conditions for the matched benchmark harness."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import telemetry_scorer_four_way as four


class MatchedHarnessTest(unittest.TestCase):
    def record(self, backend, elapsed=100):
        metadata = {'status': 'placed', 'runner_wall_ns': elapsed, 'scorer_events': [],
                    'matched_identity': {'before_sha256': 'pin', 'after_sha256': 'pin', 'unchanged': True},
                    'first_native_invocation': {'returned': True},
                    'pypy_planning_calls': [{'status': 'ok', 'exit_code': 0, 'error': None,
                                            'runtime': {'implementation': 'PyPy'},
                                            'first_wire_cost': {'returned': True}}]}
        return {'backend': backend, 'exit_code': 0, 'worker': metadata}

    def test_order_balances_positions_and_every_directed_carryover(self):
        four.validate_orders()
        with mock.patch.object(four, 'ORDERS', (four.ORDERS[0],) * 4):
            with self.assertRaisesRegex(ValueError, 'each position'):
                four.validate_orders()

    def test_success_requires_placement_stable_identity_no_fallback_and_actual_backend(self):
        for backend in four.BACKENDS:
            four.require_success(self.record(backend), 'pin')
        mutations = [('exit_code', 124), ('status', 'refused'), ('scorer_events', ['fallback']),
                     ('matched_identity', {'before_sha256': 'pin', 'after_sha256': 'changed', 'unchanged': False})]
        for key, value in mutations:
            record = self.record('rust')
            (record if key == 'exit_code' else record['worker'])[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                four.require_success(record, 'pin')
        for backend in ('c', 'rust'):
            record = self.record(backend)
            record['worker']['first_native_invocation'] = {'returned': False}
            with self.subTest(backend=backend), self.assertRaisesRegex(ValueError, 'native scorer'):
                four.require_success(record, 'pin')
        for mutation in ({'runtime': {'implementation': 'CPython'}}, {'first_wire_cost': None}):
            record = self.record('pypy')
            record['worker']['pypy_planning_calls'][0].update(mutation)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                four.require_success(record, 'pin')

    def test_comparison_rejects_cross_round_artifact_or_normalization_changes(self):
        reference = ({'candidate.kicad_pcb': b'board', 'plan.json': b'plan'}, {'date': 'normalized'}, ['raw-hash'])
        candidate = copy.deepcopy(reference)
        with mock.patch.object(four.pair, 'comparison_artifacts', side_effect=[reference, candidate]):
            self.assertEqual(four.compare_to_reference(Path('round-1/python'), Path('round-4/c'))['status'], 'equal')
        for actual in [({'candidate.kicad_pcb': b'board', 'plan.json': b'changed'}, reference[1], reference[2]),
                       (reference[0], {}, reference[2]),
                       ({'candidate.kicad_pcb': b'changed', 'plan.json': b'plan'}, reference[1], reference[2])]:
            with mock.patch.object(four.pair, 'comparison_artifacts', side_effect=[reference, actual]):
                self.assertEqual(four.compare_to_reference(Path('round-1/python'), Path('round-4/c'))['status'], 'different')

    def test_complete_rust_planner_requires_its_process_witness(self):
        record = self.record('rust')
        record['worker']['first_native_invocation'] = None
        call = {'status': 'ok', 'exit_code': 0, 'input_sha256': 'input', 'executable_sha256': 'binary'}
        record['worker']['rust_planning_calls'] = [call]
        four.require_success(record, 'pin')
        for key, value in [('status', 'error'), ('exit_code', 1), ('input_sha256', ''), ('executable_sha256', '')]:
            broken = copy.deepcopy(record)
            broken['worker']['rust_planning_calls'][0][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'whole-planning'):
                four.require_success(broken, 'pin')
        record['worker']['rust_planning_calls'] = []
        with self.assertRaisesRegex(ValueError, 'native scorer'):
            four.require_success(record, 'pin')

    def test_summary_requires_all_sixteen_parity_records(self):
        rows = [{'arms': [self.record(backend, (index + 1) * 100) for backend in order],
                 'comparisons': {backend: {'status': 'equal'} for backend in order}}
                for index, order in enumerate(four.ORDERS)]
        summary = four.summarize(rows)
        self.assertEqual(summary['runner_wall_ns']['c'], [100, 200, 300, 400])
        self.assertEqual(summary['mean_runner_ns']['python'], 250)
        self.assertEqual(summary['per_round_speedup_vs_cpython']['pypy'], [1, 1, 1, 1])
        rows[3]['comparisons'].pop('c')
        with self.assertRaisesRegex(ValueError, 'parity'):
            four.summarize(rows)
        with self.assertRaisesRegex(ValueError, 'four complete rounds'):
            four.summarize(rows[:3])

    def test_tree_identity_rejects_external_and_directory_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'runtime'
            root.mkdir()
            (root / 'binary').write_bytes(b'pinned')
            (root / 'alias').symlink_to('binary')
            self.assertEqual(four.tree_identity(root)['file_count'], 2)
            (root / 'outside').symlink_to(Path(directory))
            with self.assertRaisesRegex(ValueError, 'symlink'):
                four.tree_identity(root)

    def test_archive_manifest_must_cover_benchmark_helpers(self):
        with mock.patch.object(four.pair, 'source_identity', return_value={
                'provenance': 'archive-manifest-verified-files', 'files_sha256': {}}):
            with self.assertRaisesRegex(ValueError, 'does not seal benchmark file'):
                four.source_identity()

    def test_mismatch_retains_evidence_and_stops_without_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for filename in ('board', 'netlist', 'request', 'c-library', 'rust-library', 'kicad-cli'):
                (root / filename).write_text('frozen')
            for folder in ('models', 'pypy-root'):
                (root / folder).mkdir()
            executable = root / 'pypy-root' / 'pypy'
            executable.write_text('frozen')
            argv = ['driver']
            for name in ('board', 'netlist', 'request', 'models', 'c-library', 'rust-library', 'pypy-root'):
                argv.extend(['--' + name, str(root / name)])
            output = root / 'results'
            argv.extend(['--pypy', str(executable), '--out', str(output)])
            identity = {'fixed': 'identity'}
            expected = four.pair.sha(four.pair.canonical(identity))
            def run_arm(config, backend, arm_root, **kwargs):
                record = self.record(backend)
                record['worker']['matched_identity'].update(before_sha256=expected, after_sha256=expected)
                return record
            with mock.patch.object(sys, 'argv', argv), mock.patch.dict(os.environ), \
                    mock.patch.object(four.shutil, 'which', return_value=str(root / 'kicad-cli')), \
                    mock.patch.object(four, 'common_identity', return_value=identity), \
                    mock.patch.object(four.pair, 'environment', return_value={}), \
                    mock.patch.object(four.pair, 'run_arm', side_effect=run_arm) as launch, \
                    mock.patch.object(four, 'compare_to_reference', side_effect=[{'status': 'equal'}, {'status': 'different'}]):
                self.assertEqual(four.main(), 1)
            self.assertEqual([call.args[1] for call in launch.call_args_list], ['python', 'c'])
            manifest = json.loads((output / 'manifest.json').read_text())
            self.assertEqual(manifest['status'], 'failed')
            self.assertEqual(len(manifest['rounds'][0]['arms']), 2)
            self.assertEqual(manifest['rounds'][0]['comparisons']['c']['status'], 'different')
            self.assertNotIn('summary', manifest)
            self.assertTrue((output / 'driver-exception.txt').is_file())


if __name__ == '__main__':
    unittest.main()
