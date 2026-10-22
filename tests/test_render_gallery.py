import json
import multiprocessing
import shutil
import struct
import tempfile
import threading
import unittest
import zlib
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen
from agent_ee.candidate import capture
from agent_ee.render3d import render_board
from agent_ee.render_gallery import page, publish, redact, serve
from agent_ee.storage import digest
from agent_ee.viewer import _ASSETS, build_viewer
from toolchain import require_toolchain

HOST = '/home/someone/secret-project'
SIDES = {'top': ['--side', 'top'], 'bottom': ['--side', 'bottom'], 'isometric': ['--side', 'top', '--rotate', '315,0,45']}


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
            self.write_receipt(view, views[view])
        self.manifest = dict(
            schema='agent-ee.render3d/1', status='completed', errors=[], source_unchanged=True,
            board={'name': 'board.kicad_pcb', 'path': HOST + '/board.kicad_pcb', 'sha256': self.board_hash},
            candidate_id=None, captured_board=None, images=images, views=views, settings={'width': 2, 'height': 2},
            staged_board_sha256=digest(staged),
            snapshot={'board': {'path': 'snapshot/board.kicad_pcb', 'sha256': digest(staged)}},
            warnings=['R1: missing 3D model ' + HOST + '/models/r.step'],
            model_coverage={'board': {'path': HOST + '/board.kicad_pcb'},
                            'summary': {'resolved': 3, 'missing': 1, 'unresolved': 2, 'disabled': 4, 'no_model': 5}})
        self.save()

    def write_receipt(self, view, record):
        command = ['kicad-cli', 'pcb', 'render', '--width', '2', '--height', '2', *SIDES[view],
                   '--output', HOST + '/renders/' + record['path'], HOST + '/work/' + view + '/board.kicad_pcb']
        (self.renders / f'{view}.json').write_text(json.dumps(dict(record, command=command)))

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
        staged = self.renders / 'snapshot' / 'board.kicad_pcb'
        shutil.copyfile(run / 'inputs' / 'project' / 'board.kicad_pcb', staged)
        self.manifest['staged_board_sha256'] = self.manifest['snapshot']['board']['sha256'] = digest(staged)
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

    # --- HOLD follow-up: snapshot, receipts, path forms, malformed counts ----
    def test_unrelated_or_rehashed_snapshot_with_verified_candidate_is_refused(self):
        run, candidate = self.make_candidate()
        self.bind(run, candidate)
        staged = self.renders / 'snapshot' / 'board.kicad_pcb'
        for text in ('unrelated staged board', 'board text (thickness 2.0)'):
            staged.write_text(text)
            self.manifest['staged_board_sha256'] = self.manifest['snapshot']['board']['sha256'] = digest(staged)
            self.save()
            self.refuses('not the retained candidate board', candidate=run)
        self.assertEqual((run / 'inputs' / 'project' / 'board.kicad_pcb').read_text(), 'board text')
        self.bind(run, candidate)
        publish(self.root, self.renders, label='x', stage='y', candidate=run)
        self.manifest['snapshot']['project'] = {'path': 'snapshot/board.kicad_pro', 'sha256': digest(staged)}
        shutil.copyfile(staged, self.renders / 'snapshot' / 'board.kicad_pro')
        self.save()
        self.refuses('snapshot project is not the retained', candidate=run)

    def test_snapshot_path_must_be_a_contained_regular_file(self):
        staged = self.renders / 'snapshot' / 'board.kicad_pcb'
        outside = Path(self.tmp.name) / 'board.kicad_pcb'
        shutil.copyfile(staged, outside)
        for path in ('../board.kicad_pcb', HOST + '/board.kicad_pcb', 'snapshot/other.kicad_pcb', None):
            self.manifest['snapshot']['board']['path'] = path
            self.save()
            self.refuses('snapshot board')
        self.manifest['snapshot']['board']['path'] = 'snapshot/board.kicad_pcb'
        staged.unlink()
        staged.symlink_to(outside)
        self.save()
        self.refuses('snapshot board')

    def test_full_permutation_of_distinct_images_is_refused_by_native_receipts(self):
        top, bottom = (self.renders / f'board-3d-{v}.png' for v in ('top', 'bottom'))
        a, b = top.read_bytes(), bottom.read_bytes()
        top.write_bytes(b)
        bottom.write_bytes(a)
        for item in self.manifest['images'][:2]:
            item['sha256'] = self.manifest['views'][item['view']]['sha256'] = digest(self.renders / item['path'])
        self.save()
        self.refuses('native render receipt')

    def test_receipt_for_another_view_or_missing_receipt_is_refused(self):
        receipt = json.loads((self.renders / 'top.json').read_text())
        receipt['command'][receipt['command'].index('--side') + 1] = 'bottom'
        (self.renders / 'top.json').write_text(json.dumps(receipt))
        self.refuses('native render receipt')
        (self.renders / 'top.json').unlink()
        self.refuses('receipt is missing')

    def test_windows_unc_and_spaced_paths_are_redacted_in_both_served_json_endpoints(self):
        paths = ('C:/Users/Alice/private-project/board.kicad_pcb', 'C:\\Users\\Alice\\private-project\\b.step',
                 '\\\\fileserver\\private-share\\c.step', '/home/Alice Smith/private-project/d.step',
                 'file:///home/alice/private-project/e.step')
        self.manifest['model_coverage']['board']['path'] = paths[0]
        self.manifest['model_coverage']['extra'] = ['path:' + p for p in paths]
        self.manifest['warnings'] = ['R9: missing 3D model ' + p for p in paths]
        self.save()
        viewer = self.make_viewer()
        metadata = json.loads((viewer / 'viewer.json').read_text())
        metadata['board']['path'] = paths[0]
        metadata['warnings'] = list(paths)
        (viewer / 'viewer.json').write_text(json.dumps(metadata))
        bundle = publish(self.root, self.renders, label='x', stage='y', viewer=viewer)
        with self.serving() as url:
            bodies = [urlopen(url + '/bundles/' + bundle + '/' + suffix).read().decode()
                      for suffix in ('manifest.json', 'viewer/viewer.json')]
            bodies.append(urlopen(url).read().decode())
        for body in bodies:
            for leak in ('Alice', 'private-project', 'fileserver', 'private-share', 'C:'):
                self.assertNotIn(leak, body)
        record = json.loads(bodies[0])
        self.assertEqual(record['model_coverage']['board']['path'], '<redacted>/board.kicad_pcb')
        self.assertIn('R9: missing 3D model <redacted>/b.step', record['warnings'])
        self.assertIn('<redacted>/e.step', bodies[1])

    # File URI schemes are case-insensitive (RFC 3986); the authority (host or UNC
    # share) of a file URI is part of the private location and is removed too.
    FILE_URIS = ('FILE:///home/Alice/private-project/board.kicad_pcb',
                 'File:///C:/Users/Alice/private-project/b.step',
                 'fIlE://fileserver/private-share/c.step',
                 'file://fileserver/private-share/d.step',
                 'FILE:\\\\fileserver\\private-share\\e.step')

    def test_case_insensitive_and_authority_file_uris_are_redacted_at_publication_and_when_served(self):
        uris = self.FILE_URIS
        self.manifest['model_coverage']['board']['path'] = uris[0]
        self.manifest['model_coverage']['extra'] = ['path:' + u for u in uris]
        self.manifest['warnings'] = ['R9: missing 3D model ' + u for u in uris]
        self.save()
        viewer = self.make_viewer()
        metadata = json.loads((viewer / 'viewer.json').read_text())
        metadata['board']['path'] = uris[0]
        metadata['warnings'] = list(uris)
        (viewer / 'viewer.json').write_text(json.dumps(metadata))
        bundle = publish(self.root, self.renders, label='x', stage='y', viewer=viewer)
        stored = [(self.root / 'bundles' / bundle / name).read_text()
                  for name in ('manifest.json', 'viewer/viewer.json')]
        with self.serving() as url:
            served = [urlopen(url + '/bundles/' + bundle + '/' + suffix).read().decode()
                      for suffix in ('manifest.json', 'viewer/viewer.json')]
            served.append(urlopen(url).read().decode())
        for body in stored + served:
            for leak in ('Alice', 'private-project', 'fileserver', 'private-share', 'C:', 'FILE', 'File', 'fIlE'):
                self.assertNotIn(leak, body)
        record = json.loads(served[0])
        self.assertEqual(record['model_coverage']['board']['path'], '<redacted>/board.kicad_pcb')
        self.assertEqual(record['warnings'], ['R9: missing 3D model <redacted>/' + n
                                              for n in ('board.kicad_pcb', 'b.step', 'c.step', 'd.step', 'e.step')])
        self.assertEqual(json.loads(served[1])['warnings'],
                         ['<redacted>/' + n for n in ('board.kicad_pcb', 'b.step', 'c.step', 'd.step', 'e.step')])
        self.assertIn('R9: missing 3D model &lt;redacted&gt;/c.step', served[2])

    def test_legacy_bundle_with_uppercase_file_uri_is_redacted_when_served(self):
        bundle = self.add()
        path = self.root / 'bundles' / bundle / 'manifest.json'
        record = json.loads(path.read_text())
        record['warnings'] = ['R1: ' + u for u in self.FILE_URIS]
        path.write_text(json.dumps(record))
        with self.serving() as url:
            body = urlopen(url + '/bundles/' + bundle + '/manifest.json').read().decode()
            page_body = urlopen(url).read().decode()
        for text in (body, page_body):
            for leak in ('Alice', 'private-project', 'fileserver', 'private-share', 'C:'):
                self.assertNotIn(leak, text)
        self.assertIn('R1: <redacted>/e.step', json.loads(body)['warnings'])

    def test_redact_path_forms(self):
        cases = {'C:/Users/A/m.step': '<redacted>/m.step', 'C:\\Users\\A B\\m.step': '<redacted>/m.step',
                 '\\\\srv\\share\\m.step': '<redacted>/m.step', '//srv/share/m.step': '<redacted>/m.step',
                 'path:/home/a/m.step': 'path:<redacted>/m.step', '/home/A B/p q/m.step': '<redacted>/m.step',
                 'x="/home/a/b" y': 'x="<redacted>/b" y', 'and/or a/b 1/2': 'and/or a/b 1/2',
                 'see https://x.org/a/b and ${KIPRJMOD}/m.step': 'see https://x.org/a/b and ${KIPRJMOD}/m.step'}
        for text, expected in cases.items():
            self.assertEqual(redact(text), expected, text)
        uri_cases = {'FILE:///home/a/m.step': '<redacted>/m.step', 'File:///C:/Users/A/m.step': '<redacted>/m.step',
                     'fIlE://srv/share/m.step': '<redacted>/m.step', 'file://srv/share/m.step': '<redacted>/m.step',
                     'FILE://localhost/C:/Users/A/m.step': '<redacted>/m.step', 'file:/home/a/m.step': '<redacted>/m.step',
                     'FILE:\\\\srv\\share\\m.step': '<redacted>/m.step', 'x="File:///home/a b/m.step" y': 'x="<redacted>/m.step" y',
                     'HTTPS://X.org/a/b http://x.org/c x-file:///a/b': 'HTTPS://X.org/a/b http://x.org/c x-file:///a/b',
                     'see https://x.org/a ${KICAD8_3DMODEL_DIR}/m.step FILE:///home/a/m.step':
                         'see https://x.org/a ${KICAD8_3DMODEL_DIR}/m.step <redacted>/m.step'}
        for text, expected in uri_cases.items():
            self.assertEqual(redact(text), expected, text)
            self.assertEqual(redact(expected), expected, expected)
        self.assertEqual(redact({'/home/a/key': 1}), {'<redacted>/key': 1})

    def test_malformed_counts_or_coverage_are_never_complete(self):
        good = dict(resolved=3, missing=0, unresolved=0, disabled=0, no_model=0)
        malformed = [{'summary': dict(good, resolved=bad)} for bad in (-1, True, None, 3.0, '3')]
        malformed += [{'summary': dict(good, disabled=-2)}, {'summary': []}, [], ['bad'], 'bad', 7]
        for coverage in malformed:
            visible, _ = self.visible_html(coverage)
            self.assertIn('Model coverage unknown', visible, coverage)
            self.assertNotIn('All declared 3D models resolved', visible, coverage)

    def test_native_render_binding_refuses_snapshot_and_permutation_tampering(self):
        require_toolchain(native=True)
        source = Path(self.tmp.name) / 'native-source'
        shutil.copytree(Path(__file__).resolve().parents[1] / 'fixtures/rc/control', source)
        scope = Path(self.tmp.name) / 'native-scope.json'
        scope.write_text(json.dumps({'schema': 'agent-ee.scope/1', 'board': 'project/rc_filter.kicad_pcb',
                                     'inputs': [{'path': 'native-source', 'at': 'project'}]}))
        run = Path(self.tmp.name) / 'native-run'
        run.mkdir()
        capture(scope, run)
        out = Path(self.tmp.name) / 'native-renders'
        manifest_path = render_board(run, out, width=128, height=128, quality='basic', jobs=1, timeout=120)
        viewer = Path(self.tmp.name) / 'native-viewer'
        build_viewer(run, viewer)
        bundle = publish(self.root, out, label='native', stage='y', viewer=viewer, candidate=run)
        record = json.loads((self.root / 'bundles' / bundle / 'manifest.json').read_text())
        self.assertEqual(set(record['model_coverage']['summary']) >= {'resolved', 'disabled', 'no_model'}, True)
        published = ''.join(p.read_text() for p in (self.root / 'bundles' / bundle).rglob('*.json') if 'vendor' not in p.parts)
        self.assertNotIn(self.tmp.name, published)
        pristine = manifest_path.read_text()
        manifest = json.loads(pristine)
        staged = out / manifest['snapshot']['board']['path']
        original = staged.read_bytes()
        changed = original.replace(b'(thickness 1.6)', b'(thickness 2.0)', 1)
        self.assertNotEqual(changed, original)
        staged.write_bytes(changed)
        manifest['snapshot']['board']['sha256'] = manifest['staged_board_sha256'] = digest(staged)
        manifest_path.write_text(json.dumps(manifest))
        self.renders = out
        self.refuses('not the retained candidate board', candidate=run)
        staged.write_bytes(original)
        manifest = json.loads(pristine)
        images = {item['view']: item for item in manifest['images']}
        top, bottom = out / images['top']['path'], out / images['bottom']['path']
        a, b = top.read_bytes(), bottom.read_bytes()
        self.assertNotEqual(a, b)
        top.write_bytes(b)
        bottom.write_bytes(a)
        for view in ('top', 'bottom'):
            images[view]['sha256'] = manifest['views'][view]['sha256'] = digest(out / images[view]['path'])
        manifest_path.write_text(json.dumps(manifest))
        self.refuses('native render receipt', candidate=run)

UNROUTED_BOARD = '(kicad_pcb (version 20240108) (generator "fixture") (gr_line (start 0 0) (end 1 0)))\n'


class DiagnosticSupplementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base/'public'
        self.root.mkdir()
        self.image = self.base/'annotated.png'
        self.image.write_bytes(png_bytes(100))
        self.packet = self.base/'INDEX.md'
        self.packet.write_text('Historical diagnostic. Proposed ADC move NOT adopted.')
        self.run, self.candidate = self.capture('unrouted', UNROUTED_BOARD)
        self.board_hash = self.candidate['files']['project/board.kicad_pcb']

    def capture(self, name, text):
        source = self.base/('source-' + name)
        source.mkdir()
        (source/'board.kicad_pcb').write_text(text)
        scope = self.base/('scope-' + name + '.json')
        scope.write_text(json.dumps({'schema': 'agent-ee.scope/1', 'board': 'project/board.kicad_pcb',
                                     'inputs': [{'path': source.name, 'at': 'project'}]}))
        run = self.base/('run-' + name)
        run.mkdir()
        return run, capture(scope, run)

    def add(self, **overrides):
        from agent_ee.render_gallery import publish_supplement
        arguments = dict(candidate=self.run, board_sha256=self.board_hash, label='Historic <analysis>',
                         stage='UNROUTED; proposed ADC move NOT adopted', routing='unrouted', adoption='not-adopted')
        arguments.update(overrides)
        return publish_supplement(self.root, overrides.pop('image', self.image), self.packet,
                                  **{k: v for k, v in arguments.items() if k != 'image'})

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes()
                for p in self.root.rglob('*') if p.is_file() and not p.is_symlink() and p.name != '.publish.lock'}

    def refuses(self, pattern, **overrides):
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, pattern):
            self.add(**overrides)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(list(self.root.glob('.diagnostic-*')) + list(self.root.glob('.diagnostics.json-*')), [])

    def get(self, url):
        with urlopen(url) as response:
            return response.read().decode()

    def serving(self):
        server = serve(self.root, '127.0.0.1', 0)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        return f'http://127.0.0.1:{server.server_port}'

    def test_independent_immutable_history_preserves_latest_bytes(self):
        latest = b'[{"id":"unchanged current milestone"}]\n'
        (self.root/'index.json').write_bytes(latest)
        first = self.add()
        old = {p.name:p.read_bytes() for p in (self.root/'diagnostics'/first).iterdir()}
        self.image.write_bytes(png_bytes(150))
        second = self.add()
        self.assertNotEqual(first, second)
        self.assertEqual(latest, (self.root/'index.json').read_bytes())
        self.assertEqual(old, {p.name:p.read_bytes() for p in (self.root/'diagnostics'/first).iterdir()})
        manifest = json.loads(old['manifest.json'])
        self.assertEqual(manifest['board_sha256'], self.board_hash)
        self.assertEqual(manifest['candidate_id'], self.candidate['candidate_id'])
        self.assertEqual(manifest['captured_board'], 'project/board.kicad_pcb')
        self.assertEqual(manifest['routing'], {'status': 'unrouted', 'copper_items': 0})
        self.assertEqual(manifest['adoption'], {'status': 'not-adopted', 'verified': False})
        self.assertEqual(manifest['source_packet']['sha256'], digest(self.packet))
        self.assertEqual(manifest['source_image']['sha256'], manifest['image']['sha256'])
        self.assertEqual(set(old), {'image.png','manifest.json'})

    def test_wrong_board_hash_symlink_and_invalid_png_leave_listing_unchanged(self):
        self.add()
        self.refuses('hash mismatch', board_sha256=digest(self.packet))
        self.refuses('invalid board hash', board_sha256='A' * 64)
        self.image.unlink()
        self.image.symlink_to(self.packet)
        self.refuses('symlink')
        self.image.unlink()
        for data in (b'not a PNG', b'<svg xmlns="http://www.w3.org/2000/svg"/>', png_bytes(100) + b'trailing',
                     png_bytes(100)[:-6]):
            self.image.write_bytes(data)
            self.refuses('PNG')

    def test_http_only_serves_allowlisted_diagnostic_artifacts(self):
        identifier = self.add()
        base = self.serving()
        prefix = '/diagnostics/'+identifier+'/'
        body = self.get(base+prefix+'index.html')
        self.assertIn('Historic &lt;analysis&gt;', body)
        self.assertIn('NOT adopted', body)
        self.assertNotIn('Latest:', body)
        with urlopen(base+prefix+'image.png') as result:
            self.assertEqual(result.read(), self.image.read_bytes())
        for path in ('diagnostics.json', prefix+'source-board', prefix+'source-packet', prefix+'../image.png', prefix):
            with self.assertRaises(HTTPError) as error: urlopen(base+'/'+path.lstrip('/'))
            self.assertEqual(error.exception.code, 404)
        image = self.root/'diagnostics'/identifier/'image.png'
        image.unlink()
        image.symlink_to(self.image)
        with self.assertRaises(HTTPError): urlopen(base+prefix+'image.png')

    # --- supplement review H1: destination symlinks ---------------------------
    def test_destination_symlinks_refuse_without_writing_outside_root(self):
        outside = self.base/'outside'
        outside.mkdir()
        (self.root/'diagnostics').symlink_to(outside, target_is_directory=True)
        self.refuses('must be a directory, not a symlink')
        self.assertEqual(list(outside.iterdir()), [])
        (self.root/'diagnostics').unlink()
        linked = self.base/'linked-root'
        linked.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'not a symlink'):
            from agent_ee.render_gallery import publish_supplement
            publish_supplement(linked/'nested', self.image, self.packet, candidate=self.run,
                               board_sha256=self.board_hash, label='x', stage='y', routing='unrouted',
                               adoption='not-adopted')
        self.assertFalse((self.root/'nested').exists())

    def test_planted_listing_and_staging_links_never_overwrite_a_publication(self):
        first = self.add()
        manifest = self.root/'diagnostics'/first/'manifest.json'
        old = manifest.read_bytes()
        # The pre-fix staging name, and every guessable alternative, is now irrelevant.
        (self.root/'.diagnostics.next').symlink_to(manifest)
        self.image.write_bytes(png_bytes(150))
        second = self.add()
        self.assertEqual(old, manifest.read_bytes())
        self.assertTrue((self.root/'.diagnostics.next').is_symlink())
        listing = json.loads((self.root/'diagnostics.json').read_text())
        self.assertEqual([item['id'] for item in listing], [second, first])
        # A listing replaced by a link is refused rather than followed.
        saved = self.root/'saved.json'
        (self.root/'diagnostics.json').rename(saved)
        (self.root/'diagnostics.json').symlink_to(manifest)
        self.image.write_bytes(png_bytes(200))
        self.refuses('must be a regular file, not a symlink')
        self.assertEqual(old, manifest.read_bytes())
        lock = self.root/'.publish.lock'
        (self.root/'diagnostics.json').unlink()
        lock.unlink()
        lock.symlink_to(manifest)
        self.refuses('publication lock')
        self.assertEqual(old, manifest.read_bytes())

    def test_identifier_collision_never_replaces_an_existing_supplement(self):
        from unittest import mock
        import agent_ee.render_gallery as gallery
        fixed = gallery.datetime(2026, 10, 22, tzinfo=gallery.timezone.utc)
        with mock.patch.object(gallery, 'datetime', wraps=gallery.datetime) as clock:
            clock.now.return_value = fixed
            first = self.add()
            target = self.root/'diagnostics'/first
            old = {p.name: p.read_bytes() for p in target.iterdir()}
            for entry in target.iterdir():
                entry.unlink()
            self.refuses('already exists; immutable publications are never replaced')
            for name, data in old.items():
                (target/name).write_bytes(data)
            self.refuses('already exists; immutable publications are never replaced')
        self.assertEqual(old, {p.name: p.read_bytes() for p in target.iterdir()})

    def test_bundle_destination_symlinks_refuse(self):
        outside = self.base/'outside-bundles'
        outside.mkdir()
        (self.root/'bundles').symlink_to(outside, target_is_directory=True)
        renders = self.base/'renders'
        renders.mkdir()
        (renders/'render-manifest.json').write_text(json.dumps({
            'schema': 'agent-ee.render3d/1', 'status': 'completed', 'board': {'sha256': 'a' * 64},
            'images': [{'view': v} for v in ('top', 'bottom', 'isometric')]}))
        from agent_ee import render_gallery as gallery
        from unittest import mock
        with mock.patch.object(gallery, '_verify_provenance'):
            with self.assertRaisesRegex(ValueError, 'must be a directory, not a symlink'):
                publish(self.root, renders, label='x', stage='y')
        self.assertEqual(list(outside.iterdir()), [])

    # --- supplement review H2: bound to a verified retained candidate ---------
    def test_supplement_requires_a_verified_retained_candidate(self):
        self.refuses('retained candidate run directory is required', candidate=self.base/'missing')
        self.refuses('retained candidate run directory is required', candidate=self.base)
        junk, record = self.capture('junk', 'not a KiCad board or retained candidate')
        self.refuses('not a parseable KiCad board', candidate=junk, board_sha256=record['files']['project/board.kicad_pcb'])
        other, record = self.capture('other', UNROUTED_BOARD.replace('fixture', 'other'))
        self.refuses('hash mismatch', candidate=other)
        (self.run/'inputs'/'project'/'board.kicad_pcb').write_text(UNROUTED_BOARD + ' ')
        self.refuses('stale')
        self.assertFalse((self.root/'diagnostics.json').exists())

    def test_status_is_explicit_and_unrouted_is_checked_against_the_board(self):
        for routing in ('', 'unknown', 'UNROUTED', None):
            self.refuses('routing status must be one of', routing=routing)
        for adoption in ('', 'unknown', 'maybe', None):
            self.refuses('adoption status must be one of', adoption=adoption)
        self.refuses('contradicts the retained board', routing='routed')
        routed = UNROUTED_BOARD.replace('(gr_line', '(segment (start 0 0) (end 1 0) (width 0.2) (layer "F.Cu") (net 0)) (gr_line')
        run, record = self.capture('routed', routed)
        digest_ = record['files']['project/board.kicad_pcb']
        self.refuses('contradicts the retained board', candidate=run, board_sha256=digest_)
        identifier = self.add(candidate=run, board_sha256=digest_, routing='partially-routed', adoption='adopted')
        manifest = json.loads((self.root/'diagnostics'/identifier/'manifest.json').read_text())
        self.assertEqual(manifest['routing'], {'status': 'partially-routed', 'copper_items': 1})
        self.assertEqual(manifest['candidate_id'], record['candidate_id'])

    def test_served_status_labels_historical_and_fails_closed_on_unknown(self):
        identifier = self.add()
        base = self.serving()
        page_ = self.get(base)
        self.assertIn('No Latest render to compare', page_)
        self.assertIn('Retained candidate ' + self.candidate['candidate_id'], page_)
        self.assertIn('Unrouted (verified: the retained board has no tracks or vias)', page_)
        self.assertIn('Proposed change NOT adopted (publisher statement)', page_)
        (self.root/'index.json').write_text(json.dumps([{'id': '0' * 12 + '-' + '1' * 12, 'label': 'L', 'stage': 'S',
                                                          'board_sha256': 'b' * 64, 'rendered_at': 't', 'images': [],
                                                          'warnings': [], 'viewer': False}]))
        for body in (self.get(base), self.get(base + '/diagnostics/' + identifier + '/index.html')):
            self.assertIn('Historical: not the board in the Latest render', body)
        latest = json.loads((self.root/'index.json').read_text())
        (self.root/'index.json').write_text(json.dumps([dict(latest[0], board_sha256=self.board_hash)]))
        for body in (self.get(base), self.get(base + '/diagnostics/' + identifier + '/index.html')):
            self.assertIn('Same board as the Latest render', body)
            self.assertNotIn('Historical:', body)
        # A supplement from before candidate binding, or a tampered status, is unknown.
        listing = json.loads((self.root/'diagnostics.json').read_text())
        legacy = {k: v for k, v in listing[0].items() if k not in ('candidate_id', 'routing', 'adoption')}
        legacy.update(schema='agent-ee.gallery-diagnostic/1', id='2' * 12 + '-' + '3' * 12)
        tampered = dict(listing[0], id='4' * 12 + '-' + '5' * 12, routing={'status': 'mostly'})
        (self.root/'diagnostics.json').write_text(json.dumps([legacy, tampered, listing[0]]))
        body = self.get(base)
        self.assertEqual(body.count('Status unknown: not bound to a verified retained candidate'), 2)
        self.assertEqual(body.count('Unrouted (verified'), 1)

    # --- supplement review H3: host paths in supplement surfaces --------------
    def test_windows_and_posix_host_paths_are_redacted_on_every_supplement_surface(self):
        leaks = ('C:/Users/secret/project/board.kicad_pcb', 'C:\\Users\\secret\\project\\b.kicad_pcb',
                 '\\\\fileserver\\private-share\\c.png', '/home/secret/project/d.md')
        packet = self.base/'\\\\fileserver\\private-share\\INDEX.md'
        packet.write_text('packet')
        self.packet = packet
        identifier = self.add(label='see ' + leaks[0], stage=' '.join(leaks))
        stored = (self.root/'diagnostics'/identifier/'manifest.json').read_text()
        stored += (self.root/'diagnostics.json').read_text()
        base = self.serving()
        # Legacy records stored before publication redaction are redacted when served.
        listing = json.loads((self.root/'diagnostics.json').read_text())
        legacy_id = '6' * 12 + '-' + '7' * 12
        legacy = dict(listing[0], id=legacy_id, label=leaks[0], stage=leaks[2],
                      source_packet={'name': leaks[2], 'sha256': 'c' * 64})
        (self.root/'diagnostics'/legacy_id).mkdir()
        (self.root/'diagnostics'/legacy_id/'manifest.json').write_text(json.dumps(legacy))
        (self.root/'diagnostics.json').write_text(json.dumps([legacy, *listing]))
        bodies = [stored]
        for ident in (identifier, legacy_id):
            bodies += [self.get(base + '/diagnostics/' + ident + suffix) for suffix in ('/index.html', '/manifest.json')]
        bodies.append(self.get(base))
        for body in bodies:
            for leak in ('secret', 'fileserver', 'private-share', 'C:', '/home/'):
                self.assertNotIn(leak, body)
        record = json.loads(bodies[2])
        self.assertEqual(record['source_packet']['name'], '<redacted>/INDEX.md')
        self.assertEqual(record['label'], 'see <redacted>/board.kicad_pcb')

    def test_case_insensitive_and_authority_file_uris_are_redacted_on_every_supplement_surface(self):
        uris = GalleryTests.FILE_URIS
        identifier = self.add(label='see ' + uris[0], stage=' '.join(uris))
        stored = (self.root/'diagnostics'/identifier/'manifest.json').read_text()
        stored += (self.root/'diagnostics.json').read_text()
        base = self.serving()
        listing = json.loads((self.root/'diagnostics.json').read_text())
        legacy_id = '8' * 12 + '-' + '9' * 12
        legacy = dict(listing[0], id=legacy_id, label=uris[1], stage=uris[2],
                      source_packet={'name': uris[3], 'sha256': 'c' * 64})
        (self.root/'diagnostics'/legacy_id).mkdir()
        (self.root/'diagnostics'/legacy_id/'manifest.json').write_text(json.dumps(legacy))
        (self.root/'diagnostics.json').write_text(json.dumps([legacy, *listing]))
        bodies = [stored]
        for ident in (identifier, legacy_id):
            bodies += [self.get(base + '/diagnostics/' + ident + suffix) for suffix in ('/index.html', '/manifest.json')]
        bodies.append(self.get(base))
        for body in bodies:
            for leak in ('Alice', 'private-project', 'fileserver', 'private-share', 'C:', 'FILE', 'File', 'fIlE'):
                self.assertNotIn(leak, body)
        record = json.loads(bodies[2])
        self.assertEqual(record['label'], 'see <redacted>/board.kicad_pcb')
        # A host path runs to the end of the text (spaced directory names), so the
        # space-joined stage collapses to the last basename.
        self.assertEqual(record['stage'], '<redacted>/e.step')
        record = json.loads(bodies[4])
        self.assertEqual(record['source_packet']['name'], '<redacted>/d.step')
        self.assertEqual(record['label'], '<redacted>/b.step')
