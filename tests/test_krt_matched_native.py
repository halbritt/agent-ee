"""Host benchmark paths survive collection, and failures remain failures."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('matched_bench', ROOT / 'benchmarks/krt-matched/bench.py')
bench = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bench)


class NativeBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(bench, 'verify_engine', return_value={'verified': True}))
        self.enterContext(patch.object(bench, 'host_snapshot', return_value={}))

    def test_mismatched_engine_refuses_before_job_and_has_no_scored_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            board = root / 'source.kicad_pcb'
            board.write_text('input')
            board.with_suffix('.kicad_pro').write_text('{}')
            with patch.object(bench, 'router_checkout', return_value='/router'), \
                 patch.object(bench, 'intent_for', return_value={}), \
                 patch.object(bench, 'verify_engine', return_value={'verified': False, 'error': 'source differs'}), \
                 patch.object(bench, 'run') as run:
                result = bench.agent_ee_job(board, board, {}, 'tag', root / 'out')
            run.assert_not_called()
            self.assertEqual(result['job_status'], 'refused')
            self.assertIsNone(result['output'])
            self.assertIn('source differs', Path(result['log']).read_text())

    def test_reuse_refuses_rule_revision_or_project_mismatch_before_copying(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / 'old/boards/board'
            bench.write_json(old / 'prep.json', {'stated_fab_rules': {
                'revision': 2, 'values': {'hole_clearance': 0.20}}})
            bench.write_json(old / 'norm/board.kicad_pro', {
                'board': {'design_settings': {'rules': {'min_hole_clearance': 0.25}}}})
            with patch.object(bench, 'RUNS', root / 'new'):
                for revision, message in ((1, 'requested fab-rule revision'), (2, 'project differs')):
                    with self.subTest(revision=revision), self.assertRaisesRegex(SystemExit, message):
                        bench.reuse(SimpleNamespace(name='board', source=str(root / 'old'), fab_rules_revision=revision))
            self.assertFalse((root / 'new').exists())

    def test_direct_router_uses_adapter_environment_and_preserves_rule_bytes(self):
        spec = importlib.util.spec_from_file_location('direct_route', ROOT / 'benchmarks/krt-matched/direct_route.py')
        direct = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(direct)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            board = root / 'input.kicad_pcb'
            board.write_text('board')
            board.with_suffix('.kicad_pro').write_text('{"rules": "original"}')
            out = root / 'out'
            with patch.object(direct.sys, 'argv', ['direct_route.py', str(board), str(out), '30', '/router']), \
                 patch.object(direct.subprocess, 'run', return_value=SimpleNamespace(returncode=2, stdout='', stderr='failed')) as run:
                direct.main()
            env = run.call_args.kwargs['env']
            self.assertEqual(env, direct.routing_adapter.board_route_env('/router', out / 'home'))
            self.assertNotIn('PYTHONPATH', env)
            self.assertEqual((out / 'candidate/input/board.kicad_pro').read_bytes(), board.with_suffix('.kicad_pro').read_bytes())
            self.assertEqual(json.loads((out / 'direct-route.json').read_text())['exit_code'], 2)

    def test_job_retains_native_outputs_and_original_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            board = root / 'source.kicad_pcb'
            board.write_text('input board')
            board.with_suffix('.kicad_pro').write_text('{"original": true}')
            board.with_suffix('.kicad_dru').write_text('(version 1)')
            out = root / 'cell with spaces'

            def execute(argv, log, **options):
                config = json.loads(Path(argv[3]).read_text())
                self.assertEqual(options['env']['HOME'], os.environ['HOME'])
                self.assertEqual(config['router']['checkout'], '/explicit/router')
                for row in config['inputs'].values():
                    self.assertEqual(bench.sha256(row['path']), row['sha256'])
                job = Path(argv[5])
                job.mkdir()
                candidate = job / 'candidate.kicad_pcb'
                candidate.write_text('saved candidate')
                bench.write_json(job / 'job-report.json', {'status': 'completed', 'final': {'board': str(candidate)}})
                bench.write_json(job / 'incumbent.json', {'board': str(candidate)})
                Path(log).write_text('native job')
                return {'exit_code': 0, 'wall_s': 1}

            with patch.object(bench, 'router_checkout', return_value='/explicit/router'), \
                 patch.object(bench, 'intent_for', return_value={}), patch.object(bench, 'run', side_effect=execute):
                record = bench.agent_ee_job(board, board, {}, 'tag', out)
            self.assertEqual(Path(record['output']).read_text(), 'saved candidate')
            self.assertEqual(record['placement'], record['output'])
            self.assertEqual((out / 'inputs/source.kicad_pro').read_bytes(), board.with_suffix('.kicad_pro').read_bytes())
            self.assertEqual((out / 'inputs/source.kicad_dru').read_bytes(), board.with_suffix('.kicad_dru').read_bytes())
            self.assertEqual(record['execution_environment'], 'host')

    def test_failed_native_job_has_no_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            board = root / 'source.kicad_pcb'
            board.write_text('input')
            board.with_suffix('.kicad_pro').write_text('{}')
            with patch.object(bench, 'router_checkout', return_value='/explicit/router'), \
                 patch.object(bench, 'intent_for', return_value={}), \
                 patch.object(bench, 'run', return_value={'exit_code': 2, 'wall_s': 1}):
                record = bench.agent_ee_job(board, board, {}, 'tag', root / 'out')
            self.assertEqual(record['exit_code'], 2)
            self.assertIsNone(record['output'])
            self.assertIsNone(record['placement'])

    def test_native_environment_keeps_host_context(self):
        with patch.dict(os.environ, {'PYTHONPATH': '/existing', 'CODEX_HOME': '/configured'}):
            env = bench.native_env()
        self.assertEqual(env['CODEX_HOME'], '/configured')
        self.assertEqual(env['PYTHONPATH'], str(ROOT / 'src') + os.pathsep + '/existing')


if __name__ == '__main__':
    unittest.main()
