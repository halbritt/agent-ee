import json
import os
import struct
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

from agent_ee.candidate import capture, verify_snapshot
from agent_ee.storage import digest
from agent_ee.viewer import ViewerError, build_viewer, serve_viewer


class ViewerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.board = self.root / "source/board.kicad_pcb"
        self.board.parent.mkdir()
        self.board.write_text('(kicad_pcb (model "model with spaces.step"))')
        self.board.with_suffix(".kicad_pro").write_text('{}')
        assets = self.root / "assets"
        assets.mkdir()
        for name in ("index.html", "viewer.js", "viewer.css"):
            (assets / name).write_text("asset")
        self.out = self.root / "viewer"
        self.cli = self.root / "kicad-cli"
        self.cli.write_text('''#!/usr/bin/python3
import json, os, struct, sys
from pathlib import Path
if '--help' in sys.argv:
    print('--include-tracks --include-pads --include-zones --include-silkscreen --include-soldermask --subst-models')
elif sys.argv[1] == 'version':
    print('10.0.7')
else:
    board = Path(sys.argv[-1])
    board.with_suffix('.kicad_prl').write_text('native project side effect')
    print(board.read_text())
    print('Could not add 3D model for R1.')
    if os.environ.get('VIEWER_TEST_FAILURE'):
        print('native export failure', file=sys.stderr)
        sys.exit(3)
    payload = json.dumps({'asset': {'version': '2.0'}, 'meshes': [{'primitives': []}]}).encode()
    payload += b' ' * (-len(payload) % 4)
    data = struct.pack('<4sIIII', b'glTF', 2, 20 + len(payload), len(payload), 0x4E4F534A) + payload
    Path(sys.argv[sys.argv.index('--output') + 1]).write_bytes(data)
''')
        self.cli.chmod(0o755)
        self.addCleanup(patch.stopall)
        patch('agent_ee.viewer._ASSETS', assets).start()
        patch('agent_ee.viewer.shutil.which', return_value=str(self.cli)).start()

    def test_export_preserves_source_inventory_and_resolves_relative_models(self):
        before = {p.name: digest(p) for p in self.board.parent.iterdir()}
        index = build_viewer(self.board, self.out)
        self.assertEqual(index, self.out / "index.html")
        self.assertEqual(before, {p.name: digest(p) for p in self.board.parent.iterdir()})
        metadata = json.loads((self.out / "viewer.json").read_text())
        self.assertEqual(metadata["board"]["sha256"], before[self.board.name])
        self.assertIsNone(metadata["candidate_id"])
        self.assertIn('Could not add 3D model for R1.', metadata['warnings'])
        self.assertEqual(metadata['model_sha256'], digest(self.out / 'board.glb'))
        self.assertEqual(metadata['export']['relative_model_paths_resolved'], 1)
        self.assertIn(str(self.board.parent / 'model with spaces.step'), (self.out / 'export.stdout.log').read_text())
        self.assertIn(f'KIPRJMOD={self.board.parent}', metadata['export']['command'])
        self.assertFalse(Path(metadata['export']['command'][-1]).exists())

    def capture_run(self):
        scope = self.root / 'scope.json'
        scope.write_text(json.dumps({'schema': 'agent-ee.scope/1',
            'inputs': [{'path': 'source', 'at': 'project'}], 'board': 'project/board.kicad_pcb'}))
        run = self.root / 'run'
        run.mkdir()
        manifest = capture(scope, run)
        return run, manifest

    def test_retained_run_uses_captured_board_when_live_source_changes(self):
        run, manifest = self.capture_run()
        self.board.write_text('live source has changed')
        before = {p.relative_to(run).as_posix(): digest(p) for p in run.rglob('*') if p.is_file()}
        build_viewer(run, self.out)
        after = {p.relative_to(run).as_posix(): digest(p) for p in run.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(verify_snapshot(run), manifest)
        metadata = json.loads((self.out / 'viewer.json').read_text())
        self.assertEqual(metadata['candidate_id'], manifest['candidate_id'])
        self.assertEqual(metadata['board']['sha256'], manifest['files']['project/board.kicad_pcb'])
        self.assertNotIn('live source has changed', (self.out / 'export.stdout.log').read_text())

    def test_tampered_snapshot_refused_before_output_creation(self):
        run, _ = self.capture_run()
        (run / 'inputs/project/board.kicad_pcb').write_text('tampered')
        with self.assertRaisesRegex(ValueError, 'snapshot changed'):
            build_viewer(run, self.out)
        self.assertFalse(self.out.exists())

    def test_existing_output_and_output_inside_retained_run_refused(self):
        self.out.mkdir()
        (self.out / 'keep').write_text('preserve')
        with self.assertRaises(FileExistsError):
            build_viewer(self.board, self.out)
        self.assertEqual((self.out / 'keep').read_text(), 'preserve')
        run, _ = self.capture_run()
        with self.assertRaisesRegex(ValueError, 'outside the retained run'):
            build_viewer(run, run / 'work/viewer')
        self.assertFalse((run / 'work').exists())

    def test_failed_export_retains_logs_without_viewer_success_manifest(self):
        with patch.dict(os.environ, {'VIEWER_TEST_FAILURE': '1'}):
            with self.assertRaisesRegex(ViewerError, 'exit 3'):
                build_viewer(self.board, self.out)
        self.assertIn('native export failure', (self.out / 'export.stderr.log').read_text())
        self.assertEqual(json.loads((self.out / 'export.json').read_text())['returncode'], 3)
        self.assertFalse((self.out / 'viewer.json').exists())

    def test_missing_cli_and_unsupported_options_are_actionable(self):
        with patch('agent_ee.viewer.shutil.which', return_value=None):
            with self.assertRaisesRegex(ViewerError, 'kicad-cli is missing'):
                build_viewer(self.board, self.out)
        with patch('agent_ee.viewer._probe', return_value='unsupported'):
            with self.assertRaisesRegex(ViewerError, 'lacks required options'):
                build_viewer(self.board, self.out)
        self.assertFalse(self.out.exists())

    def test_timeout_preserves_diagnostics_and_cleans_staging(self):
        real_run = subprocess.run
        staged = []

        def timeout_export(command, **kwargs):
            if '--output' not in command:
                return real_run(command, **kwargs)
            staged.append(Path(command[-1]))
            kwargs['stderr'].write(b'partial export diagnostic')
            raise subprocess.TimeoutExpired(command, kwargs['timeout'])

        with patch('agent_ee.viewer.subprocess.run', side_effect=timeout_export):
            with self.assertRaisesRegex(ViewerError, 'timed out'):
                build_viewer(self.board, self.out, timeout=.1)
        self.assertFalse(staged[0].exists())
        self.assertEqual((self.out / 'export.stderr.log').read_text(), 'partial export diagnostic')
        self.assertEqual(json.loads((self.out / 'export.json').read_text())['status'], 'timeout')
        self.assertFalse((self.out / 'viewer.json').exists())

    def test_server_only_exposes_bundle_files_on_loopback(self):
        build_viewer(self.board, self.out)
        outside = self.root / 'private.txt'
        outside.write_text('private source content')
        (self.out / 'unrelated.txt').write_text('not a viewer asset')
        vendor = self.out / 'vendor'
        vendor.mkdir()
        (vendor / 'outside.js').symlink_to(outside)
        server = serve_viewer(self.out)
        self.assertEqual(server.server_address[0], '127.0.0.1')
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_address[1]}'
        try:
            for filename in ('viewer.js', 'board.glb'):
                with self.subTest(public=filename), urlopen(base + '/' + filename, timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.read(), (self.out / filename).read_bytes())
                    self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
            for path in ('/../private.txt', '/%2e%2e/private.txt', '/vendor/%2e%2e/%2e%2e/private.txt',
                         '/vendor/outside.js', '/vendor/', '/unrelated.txt'):
                with self.subTest(forbidden=path):
                    with self.assertRaises(HTTPError) as failure:
                        urlopen(base + path, timeout=5)
                    self.assertEqual(failure.exception.code, 404)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        self.assertFalse(thread.is_alive())


if __name__ == '__main__':
    unittest.main()
