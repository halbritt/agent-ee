import json
import multiprocessing
import struct
import tempfile
import threading
import unittest
import zlib
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen
from agent_ee.candidate import capture
from agent_ee.render_gallery import page, publish, redact, serve
from agent_ee.storage import digest
from agent_ee.viewer import _ASSETS

HOST = '/home/someone/secret-project'


def png_bytes(shade, width=2, height=2):
    def chunk(kind, payload):
        return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload) & 0xffffffff)
    raw = (b'\0' + bytes([shade]) * (width * 3)) * height
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


def glb_bytes():
    body = json.dumps({'asset': {'version': '2.0'}, 'meshes': [{}]}).encode()
    body += b' ' * (-len(body) % 4)
    return struct.pack('<4sIIII', b'glTF', 2, 20 + len(body), len(body), 0x4E4F534A) + body


def publish_in_process(root, renders, label):
    publish(Path(root), Path(renders), label=label, stage='concurrent')


class GalleryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'gallery'
        self.renders = Path(self.tmp.name) / 'renders'
        self.renders.mkdir()
        self.board_hash = 'a' * 64
        (self.renders / 'snapshot').mkdir()
        staged = self.renders / 'snapshot' / 'board.kicad_pcb'
        staged.write_text('staged board')
        images, views = [], {}
        for shade, view in enumerate(('top', 'bottom', 'isometric'), 1):
            p = self.renders / f'board-3d-{view}.png'
            p.write_bytes(png_bytes(shade * 40))
            images.append(dict(view=view, path=p.name, width=2, height=2, sha256=digest(p)))
            views[view] = dict(images[-1], status='completed')
        self.manifest = dict(
            schema='agent-ee.render3d/1', status='completed', errors=[], source_unchanged=True,
            board={'name': 'board.kicad_pcb', 'path': HOST + '/board.kicad_pcb', 'sha256': self.board_hash},
            candidate_id=None, captured_board=None, images=images, views=views,
            staged_board_sha256=digest(staged),
            snapshot={'board': {'path': 'snapshot/board.kicad_pcb', 'sha256': digest(staged)}},
            warnings=['R1: missing 3D model ' + HOST + '/models/r.step'],
            model_coverage={'board': {'path': HOST + '/board.kicad_pcb'},
                            'summary': {'resolved': 3, 'missing': 1, 'unresolved': 2, 'disabled': 4, 'no_model': 5}})
        self.save()

    def save(self):
        (self.renders / 'render-manifest.json').write_text(json.dumps(self.manifest))

    def add(self):
        return publish(self.root, self.renders, label='Candidate <one>', stage='Unrouted diagnostic')

    def test_history_and_failed_import_preserves_latest(self):
        first = self.add()
        second = self.add()
        self.assertNotEqual(first, second)
        before = (self.root/'index.json').read_bytes()
        (self.renders/'board-3d-top.png').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            self.add()
        self.assertEqual(before, (self.root/'index.json').read_bytes())
        self.assertTrue((self.root/'bundles'/first/'top.png').is_file())
        self.assertEqual(list(self.root.glob('.publish-*')), [])

    def test_refuses_incomplete_and_traversal_and_symlinks(self):
        self.manifest['status'] = 'running'
        self.save()
        with self.assertRaises(ValueError): self.add()
        self.manifest['status'] = 'completed'
        self.manifest['images'][0]['path'] = '../secret'
        self.save()
        with self.assertRaises(ValueError): self.add()
        self.manifest['images'][0]['path'] = 'board-3d-top.png'
        self.save()
        (self.renders/'board-3d-top.png').unlink()
        (self.renders/'board-3d-top.png').symlink_to(self.renders/'board-3d-bottom.png')
        with self.assertRaises(ValueError): self.add()

    def test_mismatched_viewer_does_not_publish(self):
        viewer = Path(self.tmp.name)/'viewer'
        viewer.mkdir()
        (viewer/'viewer.json').write_text(json.dumps({'schema':'agent-ee.viewer/1', 'board':{'sha256':'b'*64}}))
        with self.assertRaisesRegex(ValueError, 'different board'):
            publish(self.root, self.renders, label='Bad', stage='test', viewer=viewer)
        self.assertFalse((self.root/'index.json').exists())
        self.assertEqual(list((self.root/'bundles').iterdir()), [])

    def test_http_scope_and_escaped_labels(self):
        bundle = self.add()
        httpd = serve(self.root, '127.0.0.1', 0)
        thread = threading.Thread(target=httpd.serve_forever)
        thread.start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(httpd.shutdown)
        url = f'http://127.0.0.1:{httpd.server_port}'
        with urlopen(url) as response:
            self.assertIn(b'Candidate &lt;one&gt;', response.read())
        with urlopen(url+'/bundles/'+bundle+'/top.png') as response:
            self.assertEqual(response.status, 200)
        for path in ('/.publish.lock','/index.json','/bundles/','/../etc/passwd', '/bundles/'+bundle+'/secret.txt'):
            with self.assertRaises(HTTPError) as error: urlopen(url+path)
            self.assertEqual(error.exception.code, 404)
        with self.assertRaises(ValueError): serve(self.root, '0.0.0.0', 0)

    # --- G1: coverage visible outside the collapsed details -----------------
    def visible_html(self, coverage):
        item = dict(id='0'*12+'-'+'1'*12, label='L', stage='S', board_sha256='a'*64, rendered_at='t',
                    model_coverage=coverage, warnings=['W1'], images=[], viewer=False)
        text = page([item]).decode()
        return text.split('<details>')[0], text

    def test_incomplete_coverage_badge_is_outside_details(self):
        visible, text = self.visible_html({'summary': {'resolved': 3, 'missing': 1, 'unresolved': 2, 'disabled': 4, 'no_model': 5}})
        self.assertIn('Incomplete models', visible)
        for expected in ('3 resolved', '1 missing', '2 unresolved', '4 disabled', '5 footprints without a model'):
            self.assertIn(expected, visible)
        self.assertNotIn('W1', visible)
        self.assertIn('W1', text)

    def test_unknown_or_absent_coverage_is_never_shown_complete(self):
        for coverage in (None, {}, {'summary': {'resolved': 3, 'missing': 0, 'unresolved': 0, 'no_model': 0}},
                         {'summary': {'resolved': 3, 'missing': 'x', 'unresolved': 0, 'disabled': 0, 'no_model': 0}}):
            visible, _ = self.visible_html(coverage)
            self.assertIn('Model coverage unknown', visible)
            self.assertNotIn('All declared 3D models resolved', visible)
            self.assertIn('unknown', visible)

    def test_fully_resolved_coverage_badge(self):
        visible, _ = self.visible_html({'summary': {'resolved': 3, 'missing': 0, 'unresolved': 0, 'disabled': 0, 'no_model': 0}})
        self.assertIn('All declared 3D models resolved', visible)

    # --- G2: manifest provenance and candidate binding ----------------------
    def refuses(self, pattern, **kwargs):
        before = (self.root / 'index.json').read_bytes() if (self.root / 'index.json').exists() else None
        with self.assertRaisesRegex(ValueError, pattern):
            publish(self.root, self.renders, label='x', stage='y', **kwargs)
        after = (self.root / 'index.json').read_bytes() if (self.root / 'index.json').exists() else None
        self.assertEqual(before, after)
        self.assertEqual(list(self.root.glob('.publish-*')), [])

    def test_self_consistent_edited_manifest_pointing_top_at_bottom_is_refused(self):
        bottom = self.renders / 'board-3d-bottom.png'
        self.manifest['images'][0] = dict(self.manifest['images'][1], view='top')
        self.manifest['views']['top'] = dict(self.manifest['images'][0], status='completed')
        self.save()
        self.refuses('not bound to its native view record')
        # Same bytes under the expected filename is also refused (not distinct).
        (self.renders / 'board-3d-top.png').write_bytes(bottom.read_bytes())
        self.manifest['images'][0]['path'] = 'board-3d-top.png'
        self.manifest['views']['top'] = dict(self.manifest['images'][0], status='completed')
        self.save()
        self.refuses('distinct')

    def test_manifest_recording_errors_changed_source_or_bad_view_is_refused(self):
        self.manifest['errors'] = ['boom']; self.save()
        self.refuses('errors or a changed source')
        self.manifest['errors'] = []; self.manifest['source_unchanged'] = False; self.save()
        self.refuses('errors or a changed source')
        self.manifest['source_unchanged'] = True; self.manifest['views']['top']['status'] = 'failed'; self.save()
        self.refuses('native view record')

    def test_dimension_and_png_forgery_is_refused(self):
        self.manifest['images'][0]['width'] = 99; self.save()
        self.refuses('recorded dimensions')
        self.manifest['images'][0]['width'] = 2
        garbage = self.renders / 'board-3d-top.png'
        garbage.write_bytes(b'\x89PNG\r\n\x1a\nnot really')
        self.manifest['images'][0]['sha256'] = self.manifest['views']['top']['sha256'] = digest(garbage)
        self.save()
        self.refuses('not a valid PNG')

    def test_missing_or_altered_snapshot_is_refused(self):
        (self.renders / 'snapshot' / 'board.kicad_pcb').write_text('altered')
        self.refuses('snapshot board')

    def make_candidate(self):
        source = Path(self.tmp.name) / 'source'
        source.mkdir()
        (source / 'board.kicad_pcb').write_text('board text')
        scope = Path(self.tmp.name) / 'scope.json'
        scope.write_text(json.dumps({'schema': 'agent-ee.scope/1', 'board': 'project/board.kicad_pcb',
                                     'inputs': [{'path': 'source', 'at': 'project'}]}))
        run = Path(self.tmp.name) / 'run'
        run.mkdir()
        return run, capture(scope, run)

    def bind(self, run, candidate):
        self.manifest['candidate_id'] = candidate['candidate_id']
        self.manifest['captured_board'] = 'project/board.kicad_pcb'
        self.manifest['board']['sha256'] = candidate['files']['project/board.kicad_pcb']
        self.save()

    def test_candidate_binding(self):
        run, candidate = self.make_candidate()
        # A manifest that is unbound cannot be passed off as the retained candidate.
        self.refuses('different candidate', candidate=run)
        self.bind(run, candidate)
        # A bound manifest cannot be published without verifying the candidate.
        self.refuses('pass --candidate')
        bundle = publish(self.root, self.renders, label='x', stage='y', candidate=run)
        record = json.loads((self.root / 'bundles' / bundle / 'manifest.json').read_text())
        self.assertEqual(record['candidate_id'], candidate['candidate_id'])
        self.manifest['candidate_id'] = '0' * 64
        self.save()
        self.refuses('different candidate', candidate=run)
        self.bind(run, candidate)
        self.manifest['board']['sha256'] = 'c' * 64
        self.save()
        self.refuses('not the retained candidate board', candidate=run)
        self.bind(run, candidate)
        (run / 'inputs' / 'project' / 'board.kicad_pcb').write_text('mutated')
        self.refuses('stale', candidate=run)

    # --- G3: no absolute host paths are published ---------------------------
    def make_viewer(self, **extra):
        viewer = Path(self.tmp.name) / 'viewer'
        viewer.mkdir(exist_ok=True)
        (viewer / 'board.glb').write_bytes(glb_bytes())
        metadata = {'schema': 'agent-ee.viewer/1', 'board': {'name': 'board.kicad_pcb', 'path': HOST + '/board.kicad_pcb',
                    'sha256': self.board_hash}, 'candidate_id': None, 'captured_board': None, 'model': 'board.glb',
                    'model_sha256': digest(viewer / 'board.glb'), 'warnings': ['see ' + HOST + '/x.log'],
                    'export': {'command': ['kicad-cli', '--define-var', 'KIPRJMOD=' + HOST, '--output', HOST + '/o.glb'],
                               'project_directory': HOST}, **extra}
        (viewer / 'viewer.json').write_text(json.dumps(metadata))
        return viewer

    def test_published_files_contain_no_absolute_host_paths(self):
        bundle = publish(self.root, self.renders, label='x', stage='y', viewer=self.make_viewer())
        for path in (self.root / 'index.json', *(self.root / 'bundles' / bundle).rglob('*.json')):
            if 'vendor' in path.parts:
                continue
            text = path.read_text()
            self.assertNotIn('/home/', text, path)
            self.assertNotIn(HOST, text, path)
        record = json.loads((self.root / 'bundles' / bundle / 'manifest.json').read_text())
        self.assertEqual(record['model_coverage']['summary']['missing'], 1)
        self.assertIn('<redacted>/r.step', record['warnings'][0])

    def test_redact_keeps_kicad_variables_and_urls(self):
        self.assertEqual(redact('${KICAD8_3DMODEL_DIR}/a/b.step https://x.org/y KIPRJMOD=/h/p/q'),
                         '${KICAD8_3DMODEL_DIR}/a/b.step https://x.org/y KIPRJMOD=<redacted>/q')
        self.assertEqual(redact({'a': ['/etc/passwd', 3]}), {'a': ['<redacted>/passwd', 3]})

    def test_legacy_bundle_is_redacted_when_served(self):
        bundle = self.add()
        legacy = self.root / 'bundles' / bundle / 'manifest.json'
        record = json.loads(legacy.read_text())
        record['warnings'] = ['leak ' + HOST + '/m.step']
        legacy.write_text(json.dumps(record))
        with self.serving() as url:
            with urlopen(url + '/bundles/' + bundle + '/manifest.json') as response:
                body = response.read().decode()
        self.assertNotIn(HOST, body)
        self.assertIn('<redacted>/m.step', body)

    # --- G4: viewer happy path, vendor assets, concurrency ------------------
    def serving(self):
        httpd = serve(self.root, '127.0.0.1', 0)
        thread = threading.Thread(target=httpd.serve_forever)
        thread.start()

        class Context:
            def __enter__(inner):
                return f'http://127.0.0.1:{httpd.server_port}'

            def __exit__(inner, *exc):
                httpd.shutdown()
                thread.join()
                httpd.server_close()
        return Context()

    def test_viewer_happy_path_and_vendor_assets_are_served(self):
        bundle = publish(self.root, self.renders, label='x', stage='y', viewer=self.make_viewer())
        with self.serving() as url:
            base = url + '/bundles/' + bundle + '/viewer/'
            for name in ('index.html', 'viewer.js', 'viewer.css', 'board.glb', 'viewer.json',
                         'vendor/three.module.js', 'vendor/GLTFLoader.js'):
                with urlopen(base + name) as response:
                    self.assertEqual(response.status, 200, name)
            with urlopen(base + 'board.glb') as response:
                self.assertEqual(response.read(4), b'glTF')
            with urlopen(base + 'vendor/three.module.js') as response:
                self.assertEqual(response.read(), (_ASSETS / 'vendor' / 'three.module.js').read_bytes())
            with urlopen(url) as response:
                self.assertIn(b'Open interactive 3D viewer', response.read())
            for name in ('vendor/nonexistent.js', 'vendor/../../index.json', 'export.json'):
                with self.assertRaises(HTTPError) as error:
                    urlopen(base + name)
                self.assertEqual(error.exception.code, 404)

    def test_viewer_for_other_candidate_is_refused(self):
        viewer = self.make_viewer(candidate_id='f' * 64)
        with self.assertRaisesRegex(ValueError, 'different candidate'):
            publish(self.root, self.renders, label='x', stage='y', viewer=viewer)

    def test_concurrent_publishers_keep_every_bundle_and_a_valid_index(self):
        context = multiprocessing.get_context('fork')
        workers = [context.Process(target=publish_in_process, args=(str(self.root), str(self.renders), f'c{n}'))
                   for n in range(6)]
        for w in workers:
            w.start()
        for w in workers:
            w.join(120)
            self.assertEqual(w.exitcode, 0)
        index = json.loads((self.root / 'index.json').read_text())
        self.assertEqual(len(index), 6)
        self.assertEqual(len({item['id'] for item in index}), 6)
        self.assertEqual(sorted(item['label'] for item in index), [f'c{n}' for n in range(6)])
        for item in index:
            self.assertTrue((self.root / 'bundles' / item['id'] / 'top.png').is_file())
        self.assertEqual(len(list((self.root / 'bundles').iterdir())), 6)
        self.assertEqual(list(self.root.glob('.publish-*')), [])
