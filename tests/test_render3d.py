import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import types
import unittest
import zlib
from pathlib import Path
from unittest.mock import Mock, patch

from agent_ee.candidate import capture, verify_snapshot
from agent_ee.render3d import RenderError, _png_info, render_board
from agent_ee.storage import digest
from toolchain import enable_visuals_for_module


setUpModule = enable_visuals_for_module


def png_bytes(width=2, height=2, scanlines=None):
    def chunk(kind, payload):
        return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload) & 0xffffffff)
    raw = scanlines if scanlines is not None else b'\0' * (height * (width * 3 + 1))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


class NativeRenderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.board = self.source / 'board.kicad_pcb'
        self.board.write_text('(kicad_pcb (model "model with spaces.step"))')
        self.board.with_suffix('.kicad_pro').write_text('{}')
        self.out = self.root / 'renders'
        self.cli = self.root / 'kicad-cli'
        self.cli.write_text('''#!/usr/bin/python3
import json, os, struct, sys, time, zlib
from pathlib import Path
if '--help' in sys.argv:
    print('--width --height --side --background --quality --floor --use-board-stackup-colors --rotate --define-var')
    sys.exit()
if sys.argv[1] == 'version':
    print('10.0.7')
    sys.exit()
board = Path(sys.argv[-1]); board.with_suffix('.kicad_prl').write_text('native side effect')
config = Path(os.environ['KICAD_CONFIG_HOME']); (config / 'config.json').write_text('{}')
print(json.dumps({'pid':os.getpid(),'board':str(board),'config':str(config),'text':board.read_text()}), flush=True)
mode = os.environ.get('RENDER_TEST_MODE')
if mode == 'timeout': time.sleep(30)
side = sys.argv[sys.argv.index('--side')+1]
if mode == 'failure' and side == 'bottom':
    print('native renderer failed', file=sys.stderr); sys.exit(3)
width = int(sys.argv[sys.argv.index('--width')+1])-32
height = int(sys.argv[sys.argv.index('--height')+1])-16
def chunk(kind, payload):
    return struct.pack('>I',len(payload))+kind+payload+struct.pack('>I',zlib.crc32(kind+payload)&0xffffffff)
image = b'\\x89PNG\\r\\n\\x1a\\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,0,0,0))
image += chunk(b'IDAT',zlib.compress(b'\\0'*(height*(width*3+1))))+chunk(b'IEND',b'')
if mode == 'corrupt' and side == 'bottom': image = image[:-12]
Path(sys.argv[sys.argv.index('--output')+1]).write_bytes(image)
''')
        self.cli.chmod(0o755)
        self.inspect = Mock(return_value={'schema': 'agent-ee.models3d/1', 'summary': {'missing': 1},
                                         'warnings': ['one component model is missing']})
        self.fetch = Mock(return_value={'status': 'complete', 'files': []})
        helper = types.ModuleType('agent_ee.models3d')
        helper.inspect_models, helper.fetch_stock_models = self.inspect, self.fetch
        self.addCleanup(patch.stopall)
        patch.dict(sys.modules, {'agent_ee.models3d': helper}).start()
        patch('agent_ee.render3d.shutil.which', return_value=str(self.cli)).start()

    def render(self, **kwargs):
        return render_board(self.board, self.out, width=96, height=80, timeout=5, **kwargs)

    def test_selected_top_bottom_views_use_the_same_renderer_contract(self):
        manifest = json.loads(self.render(views=('top', 'bottom'), jobs=2).read_text())
        self.assertEqual(manifest['status'], 'completed')
        self.assertEqual(set(manifest['views']), {'top', 'bottom'})
        self.assertEqual({i['view'] for i in manifest['images']}, {'top', 'bottom'})
        self.assertFalse((self.out / 'isometric.json').exists())

    def test_invalid_view_selection_refuses_before_output_creation(self):
        for views in ((), ('top', 'top'), ('sideways',), 'top'):
            with self.subTest(views=views), self.assertRaises(ValueError):
                self.render(views=views)
            self.assertFalse(self.out.exists())

    def test_three_native_views_preserve_inputs_and_record_actual_pngs(self):
        before = {p.name: digest(p) for p in self.source.iterdir()}
        progress = []
        manifest_path = self.render(progress=progress.append)
        manifest = json.loads(manifest_path.read_text())
        self.assertEqual(manifest['status'], 'completed')
        self.assertTrue(manifest['source_unchanged'])
        self.assertEqual(manifest['board']['sha256'], before[self.board.name])
        self.assertEqual(manifest['project']['sha256'], before[self.board.with_suffix('.kicad_pro').name])
        self.assertEqual(before, {p.name: digest(p) for p in self.source.iterdir()})
        self.assertEqual(len(manifest['images']), 3)
        configs, stages = set(), set()
        for image in manifest['images']:
            self.assertEqual((image['width'], image['height']), (64, 64))
            self.assertEqual(image['sha256'], digest(self.out / image['path']))
            record = manifest['views'][image['view']]
            command = record['command']
            self.assertIn('--floor', command)
            self.assertIn('--use-board-stackup-colors', command)
            self.assertNotIn('--perspective', command)
            self.assertEqual(command[command.index('--background') + 1], 'opaque')
            self.assertIn(f'KIPRJMOD={self.source}', command)
            configs.add(record['config_directory'])
            stages.add(command[-1])
            self.assertFalse(Path(record['config_directory']).exists())
            self.assertFalse(Path(command[-1]).exists())
        self.assertEqual(len(configs), 3)
        self.assertEqual(len(stages), 3)
        iso = manifest['views']['isometric']['command']
        self.assertEqual(iso[iso.index('--rotate') + 1], '315,0,45')
        bottom = manifest['views']['bottom']['command']
        self.assertEqual(bottom[bottom.index('--side') + 1], 'bottom')
        inventory_board, env = self.inspect.call_args.args
        self.assertNotEqual(inventory_board, self.board)
        self.assertEqual(env['KIPRJMOD'], str(self.source))
        self.assertEqual(inventory_board, self.out / 'snapshot/board.kicad_pcb')
        self.assertTrue(inventory_board.is_file())
        self.assertEqual(manifest['snapshot']['board']['sha256'], digest(inventory_board))
        self.fetch.assert_not_called()
        self.assertTrue(any('model coverage' in message for message in progress))
        self.assertTrue(any('several minutes' in message for message in progress))
        self.assertEqual(sum(': completed (' in message for message in progress), 3)

    def test_native_failure_retains_partial_images_and_per_view_diagnostics(self):
        with patch.dict(os.environ, {'RENDER_TEST_MODE': 'failure'}):
            with self.assertRaisesRegex(RenderError, 'failed'):
                self.render()
        manifest = json.loads((self.out / 'render-manifest.json').read_text())
        self.assertEqual(manifest['status'], 'failed')
        self.assertEqual({i['view'] for i in manifest['images']}, {'top', 'isometric'})
        self.assertEqual(manifest['views']['bottom']['returncode'], 3)
        self.assertIn('native renderer failed', (self.out / 'bottom.stderr.log').read_text())
        self.assertTrue(manifest['source_unchanged'])

    def test_truncated_native_png_cannot_be_marked_completed(self):
        with patch.dict(os.environ, {'RENDER_TEST_MODE': 'corrupt'}):
            with self.assertRaisesRegex(RenderError, 'IEND missing'):
                self.render()
        manifest = json.loads((self.out / 'render-manifest.json').read_text())
        self.assertEqual(manifest['status'], 'failed')
        self.assertEqual(manifest['views']['bottom']['status'], 'failed')
        self.assertEqual(len(manifest['images']), 2)

    def test_timeouts_kill_and_wait_for_all_native_processes(self):
        with patch.dict(os.environ, {'RENDER_TEST_MODE': 'timeout'}):
            with self.assertRaisesRegex(RenderError, 'timeout'):
                render_board(self.board, self.out, width=96, height=80, timeout=.15)
        manifest = json.loads((self.out / 'render-manifest.json').read_text())
        self.assertEqual(manifest['status'], 'timeout')
        self.assertFalse(manifest['images'])
        for record in manifest['views'].values():
            self.assertEqual(record['status'], 'timeout')
            self.assertLess(record['returncode'], 0)
            with self.assertRaises(ProcessLookupError):
                os.kill(record['pid'], 0)

    def test_interruption_kills_children_and_writes_manifest_before_reraising(self):
        def interrupted_iterator(futures):
            time.sleep(.1)
            raise KeyboardInterrupt
            yield  # Make this an iterator with an interruption at first iteration.

        with patch.dict(os.environ, {'RENDER_TEST_MODE': 'timeout'}):
            with patch('agent_ee.render3d.concurrent.futures.as_completed', interrupted_iterator):
                with self.assertRaises(KeyboardInterrupt):
                    self.render()
        manifest = json.loads((self.out / 'render-manifest.json').read_text())
        self.assertEqual(manifest['status'], 'interrupted')
        for record in manifest['views'].values():
            self.assertEqual(record['status'], 'interrupted')
            if record.get('command'):
                self.assertFalse(Path(record['command'][-1]).exists())
            diagnostic = (self.out / record['stdout']).read_text()
            if diagnostic:
                with self.assertRaises(ProcessLookupError):
                    os.kill(json.loads(diagnostic)['pid'], 0)

    def capture_run(self):
        scope = self.root / 'scope.json'
        scope.write_text(json.dumps({'schema': 'agent-ee.scope/1', 'board': 'project/board.kicad_pcb',
                                     'inputs': [{'path': 'source', 'at': 'project'}]}))
        run = self.root / 'run'
        run.mkdir()
        manifest = capture(scope, run)
        return run, manifest

    def test_retained_candidate_uses_captured_board_and_rejects_mutation(self):
        run, manifest = self.capture_run()
        self.board.write_text('live source changed')
        render_board(run, self.out, width=96, height=80)
        rendered = json.loads((self.out / 'render-manifest.json').read_text())
        self.assertEqual(rendered['candidate_id'], manifest['candidate_id'])
        self.assertEqual(rendered['board']['sha256'], manifest['files']['project/board.kicad_pcb'])
        verify_snapshot(run)
        with self.assertRaisesRegex(ValueError, 'outside the retained run'):
            render_board(run, run / 'new-render')
        (run / 'inputs/project/board.kicad_pcb').write_text('tampered snapshot')
        with self.assertRaisesRegex(ValueError, 'snapshot changed'):
            render_board(run, self.root / 'another')

    def test_explicit_model_fetch_and_aliases_are_recorded(self):
        models = self.root / 'models-cache'
        self.render(models=models, fetch_models=True)
        self.fetch.assert_called_once()
        self.assertEqual(self.fetch.call_args.args[1], models)
        self.assertEqual(self.fetch.call_args.kwargs['env']['KICAD6_3DMODEL_DIR'], str(models))
        self.assertEqual(self.inspect.call_args.args[1]['KISYS3DMOD'], str(models))
        manifest = json.loads((self.out / 'render-manifest.json').read_text())
        self.assertEqual(json.loads((self.out / manifest['models_fetch']).read_text()), self.fetch.return_value)

    def test_stock_cache_preserves_custom_model_directory_aliases(self):
        self.board.write_text('(kicad_pcb (model "${MY_CUSTOM_3DMODEL_DIR}/custom.step") '
                              '(model "${KICAD12_3DMODEL_DIR}/stock.step"))')
        models = self.root / 'stock-models'
        models.mkdir()
        with patch.dict(os.environ, {'MY_CUSTOM_3DMODEL_DIR': '/custom/local-models'}):
            self.render(models=models)
        environment = self.inspect.call_args.args[1]
        self.assertEqual(environment['MY_CUSTOM_3DMODEL_DIR'], '/custom/local-models')
        self.assertEqual(environment['KICAD12_3DMODEL_DIR'], str(models))

    def test_failed_explicit_model_fetch_preserves_error_receipt(self):
        self.fetch.side_effect = ValueError('model download transport failed')
        with self.assertRaisesRegex(RenderError, 'model download transport failed'):
            self.render(models=self.root / 'cache', fetch_models=True)
        manifest = json.loads((self.out / 'render-manifest.json').read_text())
        receipt = json.loads((self.out / manifest['models_fetch']).read_text())
        self.assertEqual(receipt['status'], 'failed')
        self.assertIn('transport failed', receipt['error'])
        self.assertEqual(manifest['status'], 'failed')
        self.assertEqual(manifest['images'], [])
        self.assertTrue(manifest['source_unchanged'])

    def test_project_change_prevents_completed_manifest_and_retains_original_snapshot(self):
        original_hash = digest(self.board.with_suffix('.kicad_pro'))

        def changed_project(message):
            if message.startswith('Queued top'):
                self.board.with_suffix('.kicad_pro').write_text('{"changed":true}')

        with self.assertRaisesRegex(RenderError, 'project changed'):
            self.render(progress=changed_project)
        manifest = json.loads((self.out / 'render-manifest.json').read_text())
        self.assertEqual(manifest['status'], 'failed')
        self.assertFalse(manifest['source_unchanged'])
        self.assertEqual(manifest['project']['sha256'], original_hash)
        self.assertEqual(digest(self.out / manifest['snapshot']['project']['path']), original_hash)

    def test_invalid_inputs_existing_output_and_missing_cli(self):
        for invalid in ({'width': 0}, {'height': -1}, {'jobs': 4}, {'timeout': float('nan')},
                        {'quality': 'user'}, {'fetch_models': True}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                render_board(self.board, self.out, **invalid)
            self.assertFalse(self.out.exists())
        self.out.mkdir()
        (self.out / 'keep').write_text('preserve')
        with self.assertRaises(FileExistsError):
            self.render()
        self.assertEqual((self.out / 'keep').read_text(), 'preserve')
        shutil.rmtree(self.out)
        with patch('agent_ee.render3d.shutil.which', return_value=None):
            with self.assertRaisesRegex(RenderError, 'kicad-cli is missing'):
                self.render()
        self.assertEqual(json.loads((self.out / 'render-manifest.json').read_text())['status'], 'failed')

    def test_png_checks_crc_scanline_integrity_and_complete_stream(self):
        image = self.root / 'image.png'
        image.write_bytes(png_bytes())
        self.assertEqual((_png_info(image)['width'], _png_info(image)['height']), (2, 2))
        bad_crc = bytearray(png_bytes())
        bad_crc[29] ^= 1
        for bad in (bytes(bad_crc), png_bytes()[:-1], png_bytes()[:-12],
                    png_bytes(scanlines=b'\x05' + b'\0' * 13),
                    png_bytes(scanlines=b'\0' * 13), png_bytes() + b'extra'):
            image.write_bytes(bad)
            with self.subTest(length=len(bad)), self.assertRaises(RenderError):
                _png_info(image)


class NativeRenderSmokeTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('kicad-cli'), 'native KiCad CLI is not installed')
    def test_real_small_fixture_renders_all_three_views_without_source_changes(self):
        capability = subprocess.run(['kicad-cli', 'pcb', 'render', '--help'],
                                    capture_output=True, text=True, timeout=20)
        if capability.returncode or '--use-board-stackup-colors' not in capability.stdout:
            self.skipTest('installed KiCad lacks native PCB rendering')
        fixture = Path(__file__).resolve().parents[1] / 'fixtures/rc/control'
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source'
            shutil.copytree(fixture, source)
            before = {p.name: p.read_bytes() for p in source.iterdir() if p.is_file()}
            manifest_path = render_board(source / 'rc_filter.kicad_pcb', root / 'renders',
                                         width=256, height=192, quality='basic', timeout=120)
            manifest = json.loads(manifest_path.read_text())
            self.assertEqual(manifest['status'], 'completed')
            self.assertEqual({image['view'] for image in manifest['images']}, {'top', 'bottom', 'isometric'})
            for image in manifest['images']:
                actual = _png_info(manifest_path.parent / image['path'])
                self.assertEqual(actual, {key: image[key] for key in ('width', 'height', 'sha256')})
                self.assertGreater(image['width'], 0)
                self.assertGreater(image['height'], 0)
            self.assertEqual(before, {p.name: p.read_bytes() for p in source.iterdir() if p.is_file()})
            self.assertTrue((manifest_path.parent / manifest['snapshot']['board']['path']).is_file())


if __name__ == '__main__':
    unittest.main()
