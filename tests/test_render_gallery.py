import itertools
import json
import multiprocessing
import random
import re
import shutil
import struct
import tempfile
import threading
import unittest
import zlib
from pathlib import Path, PureWindowsPath
from urllib.error import HTTPError
from urllib.request import urlopen
from agent_ee.candidate import capture
from agent_ee.render3d import render_board
from agent_ee import render_gallery as gallery
from agent_ee.render_gallery import page, publish, serve
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


# Regression oracle (review rounds 433c-433i). Every host, user, share and directory
# in these texts carries "hidden" or "private-"; only plain local basenames may
# survive publication. The texts compose every operand kind found in review: file
# URIs in every casing with or without an authority (empty or root path, bracketed
# IP literals, user info), UNC hosts and paths, drives with either slash, POSIX
# paths, hosts interrupted by quotes, groups and whitespace, wrapped, punctuated and
# joined as first, second and later operands.
CASINGS = ('file', 'FILE', 'File', 'fIlE')
URI_FORMS = ('{}:///home/alice-hidden/dir-hidden/local.step', '{}:///C:/Users/alice-hidden/drive.step',
             '{}://host-hidden/share-hidden/auth.step', '{}:\\\\unc-host-hidden\\unc-share-hidden\\unc.step',
             '{}://root-host-hidden/', '{}://empty-host-hidden')
PLAIN_FORMS = ('/home/alice-hidden/dir hidden/plain.step', 'C:\\Users\\alice-hidden\\win.step', '\\\\bare-host-hidden')
SEPARATORS = ('', ',', ';', ', ', '; ', ' ', '\t', '"', "'", ')', ']', '>', '|', ' and ', '),(')
UNC_FORMS = ('\\\\later-host-hidden', '//later-host-hidden', '\\\\later-host-hidden\\share-hidden\\later.step',
             '//later-host-hidden/share-hidden/later.step')
LATER_SEPARATORS = (' (', ' [', '),(', ',[', ';(', '" ', "' ", '<', '{', ' {', '}(', ', (', ';[')
IP_URI_FORMS = ('{}://[fd00:1234::1]/share-hidden/v6.step', '{}://[fd00:1234::1]/', '{}://[fd00:1234::1]',
                '{}://user-hidden@[fd00:1234::1]:8/share-hidden/v6u.step',
                '{}://uri-host-hidden "user-hidden name"/share-hidden/us.step')
INTERRUPTED_FORMS = ('\\\\unc-host-hidden(1)\\share-hidden\\grp.step', '\\\\unc-host-hidden[1]\\share-hidden\\brk.step',
                     '\\\\unc-host-hidden "user-hidden"\\share-hidden\\q.step',
                     '\\\\unc-host-hidden (user-hidden)\\share-hidden\\s.step',
                     '\\\\unc-host-hidden "user-hidden name"\\share-hidden\\qs.step',
                     '\\\\unc-host-hidden "user-hidden" \\share-hidden\\qt.step',
                     '\\\\unc-host-hidden (user-hidden name) \\share-hidden\\gs.step')
# The exact texts each review round reported, and what the scrubbing redactor published
# for them (later rounds found host or user text left in those outputs).
REVIEW_EXAMPLES = (
    'C:/Users/Alice-hidden/private-project/board.kicad_pcb', '\\\\fileserver-hidden\\private-share\\c.step',
    '/home/Alice Smith-hidden/private-project/d.step', 'file:///home/alice-hidden/private-project/e.step',
    'FILE:///home/Alice-hidden/private-project/board.kicad_pcb', 'File:///C:/Users/Alice-hidden/private-project/b.step',
    'fIlE://fileserver-hidden/private-share/c.step', 'FILE:\\\\fileserver-hidden\\private-share\\e.step',
    'FILE://private-server/', 'fIlE://private-server', 'FiLe://localhost-hidden/', 'FILE:\\\\private-server\\',
    '\\\\private-server', 'file:////private-server/', 'FILE:///home/alice-hidden/m.step,FILE://private-server/',
    'FILE:///home/alice-hidden/m.step (\\\\private-server)', '/home/alice-hidden/m.step [\\\\private-server]',
    'm.step\\\\private-server', 'FILE://[fd00:1234::1]/private-share/m.step', 'fIlE://[fd00:1234::1]/',
    '\\\\private-host(1)\\private-share\\m.step', '\\\\private-host[1]\\private-share\\m.step',
    '\\\\private-host "private-user"\\private-share\\m.step',
    '\\\\private-host "private-user" \\private-share\\m.step',
    '\\\\private-host "private-user name"\\private-share\\m.step',
    '\\\\private-host (private-user name)\\private-share\\m.step',
    'file://private-host "private-user name"/private-share/m.step',
    '\\\\private-host ( private-user name )\t\\private-share\\m.step',
    "\\\\private-host 'private-user name' \\private-share\\m.step", '\\\\private-host private-user \\private-share\\m.step',
    '\\\\private-host "private-user name\\private-share\\m.step',
    '("\\\\private-host "private-user name" \\private-share\\m.step"), "x"', '\\\\private-host "see /home/a/m.step"',
    '\\\\private-host "a b"\n\\private-share\\m.step',
    # Published by the scrubbing redactor; each still carried host or user text.
    '<redacted>/private-server)', '<redacted>[fd00:1234::1]<redacted>/m.step', '<redacted>(1)\\private-share\\m.step',
    '<redacted> "private-user"\\private-share\\m.step', '<redacted> "private-user name"<redacted>/m.step',
    '<redacted> (private-user name)<redacted>/m.step', '<redacted>/private-server',
    # Review 433k H1: host, user and share words with no path syntax at all.
    'private-host private-user private-share', 'FILE: private-host', 'private-host; private-user')
OPERANDS = ([t.format(c) for c in CASINGS for t in URI_FORMS + IP_URI_FORMS] + list(PLAIN_FORMS) + list(UNC_FORMS)
            + list(INTERRUPTED_FORMS) + ['C:/Users/alice-hidden/fwd.step', 'D:/', 'd:\\Users\\alice-hidden\\low.step',
                                         '/home/alice-hidden/posix.step'])
WRAPPERS = ('{}', '({})', '[{}]', '{{{}}}', '"{}"', "'{}'", '(({}))', '["{}"]', '("{}")')
OPERAND_SEPARATORS = (', ', '; ', ',', ';', ' ,\t', '; (', ',[')
LOOSE_SEPARATORS = ('', ' ', '\t', '\n', ' and ', '=', ':', ' | ', ' > ', ' -- ', ')(', '][', '} {')


def operand_triples(separators=OPERAND_SEPARATORS):
    """Yield (operands, wrappers, separators): every operand first and second, and a rotating third."""
    k = 0
    for first in OPERANDS:
        for second in OPERANDS:
            for separator in separators:
                wrappers = [WRAPPERS[(k + n) % len(WRAPPERS)] for n in (0, 1, 2)]
                k += 1
                yield [first, second, OPERANDS[(k - 1) % len(OPERANDS)]], wrappers, (separator, separators[k % len(separators)])


def compose(operands, wrappers, separators):
    return (wrappers[0].format(operands[0]) + separators[0] + wrappers[1].format(operands[1])
            + separators[1] + wrappers[2].format(operands[2]))


def corpus():
    """Every composed text: URI pairs, later UNC/URI operands, review examples and operand triples."""
    for n, casing in enumerate(CASINGS):
        firsts = [t.format(casing) for t in URI_FORMS] + list(PLAIN_FORMS)
        later = [t.format(CASINGS[(n + 1) % len(CASINGS)]) for t in URI_FORMS]
        yield from (first + separator + second for first in firsts for second in later for separator in SEPARATORS)
        operands = [t.format(c) for c in CASINGS for t in URI_FORMS] + list(UNC_FORMS)
        yield from (first + separator + second + tail + ')' for first in firsts for second in operands
                    for separator, tail in zip(LATER_SEPARATORS, LATER_SEPARATORS[n:] + LATER_SEPARATORS[:n]))
    yield from REVIEW_EXAMPLES
    for separators in (OPERAND_SEPARATORS, LOOSE_SEPARATORS):
        yield from (compose(*triple) for triple in operand_triples(separators))


def surface_texts():
    """A compact set with every review example and every operand first, second and third."""
    texts = list(REVIEW_EXAMPLES)
    for n, text in enumerate(OPERANDS):
        others = [OPERANDS[(n + 7) % len(OPERANDS)], OPERANDS[(n + 13) % len(OPERANDS)]]
        for position in range(3):
            parts = others[:position] + [text] + others[position:]
            separator, wrapper = OPERAND_SEPARATORS[n % len(OPERAND_SEPARATORS)], WRAPPERS[(n + position) % len(WRAPPERS)]
            texts.append(separator.join(wrapper.format(t) for t in parts))
    return texts


def random_texts(seed, rounds):
    rng, forms = random.Random(seed), URI_FORMS + IP_URI_FORMS
    plain = [t for t in OPERANDS if not t.lower().startswith('file')]
    separators = SEPARATORS + LATER_SEPARATORS + LOOSE_SEPARATORS + ('<', '(', '[', ' ; ')
    for _ in range(rounds):
        parts = []
        for _ in range(rng.randint(1, 5)):
            if rng.random() < .5:
                parts += [rng.choice(separators), rng.choice(forms).format(rng.choice(CASINGS))]
            else:
                parts += [rng.choice(separators), rng.choice(WRAPPERS).format(rng.choice(plain))]
        yield ''.join(parts + [rng.choice(separators)])


LEAKS = ('hidden', 'file:', 'C:', '\\', 'fd00', 'private-', 'Alice', 'localhost')


def assert_no_uri_leak(test, body, where):
    for leak in LEAKS:
        test.assertNotIn(leak, body.lower() if leak == 'file:' else body, where)


def records(text):
    """The published records of a bundle, viewer and supplement with text in every field and key."""
    warnings = [text, 'R1: missing 3D model ' + text, 'R2: unresolved 3D model ' + text, 'top: ' + text,
                text + ': no 3D model is declared', 'bottom: KiCad produced 1×1 for requested ' + text]
    bundle = gallery._bundle({
        'id': '0' * 12 + '-' + '1' * 12, 'label': text, 'stage': text, 'board_sha256': text, 'candidate_id': text,
        'published_at': text, 'rendered_at': text, 'warnings': warnings, 'viewer': text, text: text,
        'model_coverage': {'board': {'path': text}, 'footprints': [{'reference': text}], text: text,
                           'summary': {'resolved': text, text: 1}},
        'images': [{'view': 'top', 'path': text, 'sha256': text}, {'view': text, 'path': 'x'}]})
    viewer = gallery._viewer({
        'schema': 'agent-ee.viewer/1', 'board': {'name': text, 'path': text, 'sha256': text, text: text},
        'candidate_id': text, 'captured_board': text, 'model': text, 'warnings': warnings, 'model_sha256': text,
        'coordinates': {'up': text, 'units': text}, 'export': {'command': [text], 'project_directory': text}, text: text})
    diagnostic = gallery._diagnostic({
        'id': '2' * 12 + '-' + '3' * 12, 'schema': 'agent-ee.gallery-diagnostic/2', 'kind': text, 'label': text,
        'stage': text, 'candidate_id': text, 'captured_board': text, 'board_sha256': text, 'published_at': text,
        'routing': {'status': text, 'copper_items': text}, 'adoption': {'status': text, 'verified': text},
        'source_packet': {'name': text, 'sha256': text, text: text}, 'source_image': {'name': text},
        'image': {'path': text, 'width': text}, 'limitation': text, text: text})
    return bundle, viewer, diagnostic


def surfaces(text):
    """Everything publication or the server can emit for text: stored and served JSON and both pages."""
    bundle, viewer, diagnostic = records(text)
    return (json.dumps([bundle, viewer, diagnostic], ensure_ascii=False) + page([bundle], [diagnostic]).decode()
            + page([], [diagnostic], diagnostic_only=True, latest=bundle).decode())


def assert_leak_free(test, published, seed=433, rounds=20000, stride=1):
    """The oracle: no composed or seeded random text leaves a host marker in what is published."""
    for text in itertools.islice(itertools.chain(corpus(), random_texts(seed, rounds)), 0, None, stride):
        assert_no_uri_leak(test, published(text), text)


def publish_in_process(root, renders, label):
    publish(Path(root), Path(renders), label=label, stage='placement')


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
        return publish(self.root, self.renders, label='Candidate 1', stage='diagnostic')

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
            publish(self.root, self.renders, label='Iteration 2', stage='placement', viewer=viewer)
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
            body = response.read()
        self.assertIn(b'Latest: Candidate 1</h2><p>Stage: Diagnostic', body)
        listing = json.loads((self.root / 'index.json').read_text())
        (self.root / 'index.json').write_text(json.dumps([dict(listing[0], label='<b>x</b>', stage='<i>')]))
        with urlopen(url) as response:
            body = response.read()
        self.assertIn(b'Latest: &lt;redacted&gt;</h2><p>Stage: &lt;redacted&gt;', body)
        self.assertNotIn(b'<b>', body)
        with urlopen(url+'/bundles/'+bundle+'/top.png') as response:
            self.assertEqual(response.status, 200)
        for path in ('/.publish.lock','/index.json','/bundles/','/../etc/passwd', '/bundles/'+bundle+'/secret.txt'):
            with self.assertRaises(HTTPError) as error: urlopen(url+path)
            self.assertEqual(error.exception.code, 404)
        with self.assertRaises(ValueError): serve(self.root, '0.0.0.0', 0)

    # --- G1: coverage visible outside the collapsed details -----------------
    def visible_html(self, coverage):
        item = dict(id='0'*12+'-'+'1'*12, label='L', stage='S', board_sha256='a'*64, rendered_at='t',
                    model_coverage=coverage, warnings=['W1: no 3D model is declared'], images=[], viewer=False)
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
            publish(self.root, self.renders, label='Iteration 1', stage='placement', **kwargs)
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
        bundle = publish(self.root, self.renders, label='Iteration 1', stage='placement', candidate=run)
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
        bundle = publish(self.root, self.renders, label='Iteration 1', stage='placement', viewer=self.make_viewer())
        for path in (self.root / 'index.json', *(self.root / 'bundles' / bundle).rglob('*.json')):
            if 'vendor' in path.parts:
                continue
            text = path.read_text()
            self.assertNotIn('/home/', text, path)
            self.assertNotIn(HOST, text, path)
        record = json.loads((self.root / 'bundles' / bundle / 'manifest.json').read_text())
        self.assertEqual(record['schema'], 'agent-ee.gallery-bundle/3')
        self.assertEqual(record['model_coverage'], {'summary': dict(
            resolved=3, missing=1, unresolved=2, disabled=4, no_model=5, footprints=None, models=None,
            footprints_fully_resolved=None)})
        self.assertEqual(record['warnings'], ['R1: missing 3D model r.step'])
        metadata = json.loads((self.root / 'bundles' / bundle / 'viewer' / 'viewer.json').read_text())
        self.assertEqual(metadata, {
            'schema': 'agent-ee.viewer/1', 'publication': 'agent-ee.gallery-viewer/2',
            'board': {'name': 'board.kicad_pcb', 'path': 'board.kicad_pcb', 'sha256': self.board_hash},
            'candidate_id': None, 'captured_board': None, 'model': 'board.glb', 'warnings': ['<redacted>'],
            'coordinates': {'up': None, 'units': None}, 'model_sha256': digest(self.root / 'bundles' / bundle / 'viewer' / 'board.glb')})

    def test_basename_is_kept_only_for_an_unambiguous_local_path(self):
        kept = {'/home/a/m.step': 'm.step', '/home/A B/p q/m.step': 'm.step', '/home/Smith, Alice/m.step': 'm.step',
                'C:/Users/A/m.step': 'm.step', 'C:\\Users\\A B\\m.step': 'm.step', 'd:\\x\\R_0603.wrl': 'R_0603.wrl',
                '${KICAD8_3DMODEL_DIR}/Resistor_SMD.3dshapes/R_0603.step': 'R_0603.step',
                'models/r.step': 'r.step', 'r.step': 'r.step', None: None}
        redacted = ('\\\\srv\\share\\m.step', '//srv/share/m.step', 'file:///home/a/m.step', 'FILE:///home/a/m.step',
                    'file:C:/Users/A/m.step', 'https://x.org/a/m.step', ':alias:m.step', 'C:m.step', '\\share\\m.step', '${KIPRJMOD}\\models\\r.step',
                    '/home/a//host/m.step', '/a/m.step\\\\host', 'C:\\a\\\\host\\m.step', '/home/a/', 'C:\\', 'D:/', '',
                    '/home/a/"m.step"', "/home/a/m'step", '/home/a/<m>.step', '/home/a/m.step|x', '/home/a/m\tb.step',
                    '/home/a/m\n.step', '/home/a/m step', '/home/a/.hidden', '/home/a/..', '/home/a/m.step)',
                    '<redacted>', '<redacted>/', '<redacted>/a/b', 'x<redacted>/m.step', 3, ['m.step'], {'a': 1},
                    # Review 433k H2: an earlier redactor's output is not known to end in a file name.
                    '<redacted>/m.step', '<redacted>/private-server', '<redacted>\\private-server')
        for value, expected in kept.items():
            self.assertEqual(gallery._basename(value), expected, value)
        for value in redacted:
            self.assertEqual(gallery._basename(value), '<redacted>', value)
        for value in REVIEW_EXAMPLES:  # only the local drive and POSIX examples keep a basename
            self.assertRegex(gallery._basename(value), r'^(<redacted>|board\.kicad_pcb|[a-e]\.step)$', value)
            self.assertEqual(gallery._name(value), '<redacted>', value)
        for value in (*kept, *redacted, *REVIEW_EXAMPLES):
            once = gallery._basename(value)
            self.assertEqual(gallery._basename(once), once, value)

    def test_warnings_are_rebuilt_from_codes_and_allowlisted_parameters(self):
        notice = 'Geometry is for visual inspection, not electrical or fabrication acceptance.'
        cases = {'R1: missing 3D model ' + HOST + '/r.step': 'R1: missing 3D model r.step',
                 'U$1: unresolved 3D model ${X}/u.step': '<redacted>',
                 'U1: unresolved 3D model \\\\srv\\share\\u.step': 'U1: unresolved 3D model <redacted>',
                 'U1: unresolved 3D model None': 'U1: unresolved 3D model None',
                 'C3: no 3D model is declared': 'C3: no 3D model is declared',
                 'C3: no 3D model is declared ' + HOST: '<redacted>',
                 'top: KiCad produced 10×20 for requested 30×40': 'top: KiCad produced 10×20 for requested 30×40',
                 'isometric: Warning: could not load ' + HOST + '/r.step': 'isometric: <redacted>',
                 'see ' + HOST + '/x.log': '<redacted>', notice: notice, notice + ' ' + HOST: '<redacted>',
                 'R1: missing 3D model <redacted>/r.step': 'R1: missing 3D model <redacted>',
                 'R1: missing 3D model <redacted>/private-server': 'R1: missing 3D model <redacted>', 7: '<redacted>'}
        for text, expected in cases.items():
            self.assertEqual(gallery._warning(text), expected, text)
            self.assertEqual(gallery._warning(expected), expected, expected)

    def test_labels_and_stages_are_rebuilt_from_their_vocabulary_or_redacted_whole(self):
        """Review 433k H1: no publisher free text is published, with or without path syntax."""
        for value in ('Iteration 8', 'Revision 0', 'Candidate 12', 'Milestone 999999', 'Diagnostic 1', 'Fixture 2'):
            self.assertEqual(gallery._label(value), value)
        self.assertEqual(gallery._label('Iteration 08'), 'Iteration 8')
        for value in ('placement', 'routing', 'routed', 'review', 'diagnostic', 'fixture'):
            self.assertEqual(gallery._stage(value), value)
        exact = ('private-host private-user private-share', 'FILE: private-host', 'private-host; private-user')
        refused = (*exact, 'Telemetry pod iteration 8', 'Iteration', 'Iteration ', 'Iteration 8 ', 'iteration 8',
                   'Iteration 1000000', 'Iteration -1', 'Iteration 8.0', 'Iteration \uff18', 'Iteration private-host',
                   'Candidate <one>', 'Placement', 'see ' + HOST, 'C:\\x', 'user@host', '', '<redacted>',
                   '<redacted>/private-server', None, 3, ['placement'], {'a': 1}, *REVIEW_EXAMPLES)
        for value in refused:
            self.assertEqual(gallery._label(value), '<redacted>', value)
            self.assertEqual(gallery._stage(value), '<redacted>', value)
            if isinstance(value, str):
                with self.assertRaisesRegex(ValueError, 'label must be KIND NUMBER'):
                    publish(self.root, self.renders, label=value, stage='placement')
                with self.assertRaisesRegex(ValueError, 'stage must be one of'):
                    publish(self.root, self.renders, label='Iteration 1', stage=value)
        with self.assertRaisesRegex(ValueError, 'label must be KIND NUMBER'):
            publish(self.root, self.renders, label='Iteration 08', stage='placement')
        self.assertFalse((self.root / 'index.json').exists())

    def test_reviewed_host_text_in_labels_stages_and_marker_paths_is_never_published(self):
        """Review 433k H1/H2 exact inputs, through real publication, legacy records and HTTP."""
        exact = ('private-host private-user private-share', 'FILE: private-host', 'private-host; private-user')
        marker = '<redacted>/private-server'
        self.manifest['warnings'] = ['R1: missing 3D model ' + marker, 'R2: unresolved 3D model ' + marker]
        self.save()
        viewer = self.make_viewer(warnings=['R1: missing 3D model ' + marker])
        metadata = json.loads((viewer / 'viewer.json').read_text())
        metadata['board'].update(name=marker, path=marker)
        (viewer / 'viewer.json').write_text(json.dumps(metadata))
        bundle = publish(self.root, self.renders, label='Iteration 1', stage='placement', viewer=viewer)
        names = ('manifest.json', 'viewer/viewer.json')
        stored = [(self.root / 'bundles' / bundle / n).read_text() for n in names] + [(self.root / 'index.json').read_text()]
        with self.serving() as url:
            served = [urlopen(url + '/bundles/' + bundle + '/' + n).read().decode() for n in names]
            served.append(urlopen(url).read().decode())
            bodies = []
            for label, stage in zip(exact, exact[1:] + exact[:1]):
                for n in names:  # an earlier publisher stored the text as given
                    path = self.root / 'bundles' / bundle / n
                    record = json.loads(path.read_text())
                    record.update(label=label, stage=stage, captured_board=marker,
                                  board=dict(record.get('board', {}), name=marker, path=marker),
                                  warnings=['R1: missing 3D model ' + marker, label, stage])
                    if n == 'manifest.json':
                        record['schema'] = 'agent-ee.gallery-bundle/2'
                    path.write_text(json.dumps(record))
                listing = json.loads((self.root / 'index.json').read_text())
                listing[0].update(label=label, stage=stage, warnings=['R1: missing 3D model ' + marker])
                (self.root / 'index.json').write_text(json.dumps(listing))
                bodies += [urlopen(url + '/bundles/' + bundle + '/' + n).read().decode() for n in names]
                bodies.append(urlopen(url).read().decode())
        for n, body in enumerate(stored + served + bodies):
            assert_no_uri_leak(self, body, n)
            self.assertNotIn('private', body, n)
        record, viewer_record = json.loads(served[0]), json.loads(served[1])
        self.assertEqual((record['label'], record['stage']), ('Iteration 1', 'placement'))
        self.assertEqual(record['warnings'], ['R1: missing 3D model <redacted>', 'R2: unresolved 3D model <redacted>'])
        self.assertEqual((viewer_record['board']['name'], viewer_record['board']['path']), ('<redacted>', '<redacted>'))
        self.assertEqual(viewer_record['warnings'], ['R1: missing 3D model <redacted>'])
        legacy = json.loads(bodies[-3])
        self.assertEqual((legacy['label'], legacy['stage'], legacy['schema']),
                         ('<redacted>', '<redacted>', 'agent-ee.gallery-bundle/3'))

    def test_legacy_bundle_is_projected_when_served(self):
        bundle = self.add()
        legacy = self.root / 'bundles' / bundle / 'manifest.json'
        record = json.loads(legacy.read_text())
        record.update(warnings=['leak ' + HOST + '/m.step', 'R1: missing 3D model ' + HOST + '/m.step'],
                      label='see ' + HOST, private=HOST)
        del record['schema']
        legacy.write_text(json.dumps(record))
        with self.serving() as url:
            with urlopen(url + '/bundles/' + bundle + '/manifest.json') as response:
                body = response.read().decode()
        self.assertNotIn(HOST, body)
        served = json.loads(body)
        self.assertEqual(served['warnings'], ['<redacted>', 'R1: missing 3D model m.step'])
        self.assertEqual((served['label'], served['schema']), ('<redacted>', 'agent-ee.gallery-bundle/3'))
        self.assertNotIn('private', served)
        self.assertEqual(json.loads(legacy.read_text()), record)  # immutable: served, not rewritten

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
        bundle = publish(self.root, self.renders, label='Iteration 1', stage='placement', viewer=self.make_viewer())
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
            publish(self.root, self.renders, label='Iteration 1', stage='placement', viewer=viewer)

    def test_concurrent_publishers_keep_every_bundle_and_a_valid_index(self):
        context = multiprocessing.get_context('fork')
        workers = [context.Process(target=publish_in_process, args=(str(self.root), str(self.renders), f'Iteration {n}'))
                   for n in range(6)]
        for w in workers:
            w.start()
        for w in workers:
            w.join(120)
            self.assertEqual(w.exitcode, 0)
        index = json.loads((self.root / 'index.json').read_text())
        self.assertEqual(len(index), 6)
        self.assertEqual(len({item['id'] for item in index}), 6)
        self.assertEqual(sorted(item['label'] for item in index), [f'Iteration {n}' for n in range(6)])
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
        publish(self.root, self.renders, label='Iteration 1', stage='placement', candidate=run)
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

    def test_host_paths_in_warnings_coverage_and_viewer_are_published_as_basenames_or_redacted(self):
        paths = ('C:/Users/Alice/private-project/board.kicad_pcb', 'C:\\Users\\Alice\\private-project\\b.step',
                 '\\\\fileserver\\private-share\\c.step', '/home/Alice Smith/private-project/d.step',
                 'file:///home/alice/private-project/e.step', 'FILE://fileserver/private-share/f.step')
        self.manifest['model_coverage']['board']['path'] = paths[0]
        self.manifest['model_coverage']['extra'] = {p: p for p in paths}
        self.manifest['warnings'] = ['R9: missing 3D model ' + p for p in paths]
        self.save()
        viewer = self.make_viewer()
        metadata = json.loads((viewer / 'viewer.json').read_text())
        metadata['board']['path'] = paths[2]
        metadata['warnings'] = list(paths)
        (viewer / 'viewer.json').write_text(json.dumps(metadata))
        bundle = publish(self.root, self.renders, label='Iteration 1', stage='placement', viewer=viewer)
        names = ('manifest.json', 'viewer/viewer.json')
        bodies = [(self.root / 'bundles' / bundle / n).read_text() for n in names] + [(self.root / 'index.json').read_text()]
        with self.serving() as url:
            bodies += [urlopen(url + '/bundles/' + bundle + '/' + n).read().decode() for n in names]
            bodies.append(urlopen(url).read().decode())
        for body in bodies:
            for leak in ('Alice', 'private-project', 'fileserver', 'private-share', 'C:', 'file:', 'FILE'):
                self.assertNotIn(leak, body)
        record = json.loads(bodies[3])
        self.assertNotIn('extra', record['model_coverage'])
        self.assertEqual(record['warnings'], ['R9: missing 3D model ' + n for n in
                                              ('board.kicad_pcb', 'b.step', '<redacted>', 'd.step', '<redacted>', '<redacted>')])
        metadata = json.loads(bodies[4])
        self.assertEqual(metadata['warnings'], ['<redacted>'] * len(paths))
        self.assertEqual(metadata['board']['path'], '<redacted>')
        self.assertNotIn('export', metadata)
        self.assertIn('R9: missing 3D model d.step', bodies[5])

    def test_projection_is_idempotent_and_drops_unlisted_fields(self):
        for text in itertools.islice(corpus(), 0, None, 37):
            for project, record in zip((gallery._bundle, gallery._viewer, gallery._diagnostic), records(text)):
                self.assertEqual(project(record), record, text)
                self.assertNotIn(text, record)
        bundle, viewer, diagnostic = records('x')
        self.assertEqual(set(bundle), {'id', 'schema', 'label', 'stage', 'board_sha256', 'candidate_id', 'published_at',
                                       'rendered_at', 'model_coverage', 'warnings', 'images', 'viewer'})
        self.assertEqual(set(viewer), {'schema', 'publication', 'board', 'candidate_id', 'captured_board', 'model',
                                       'warnings', 'coordinates', 'model_sha256'})
        self.assertEqual(set(diagnostic), {'id', 'schema', 'kind', 'label', 'stage', 'candidate_id', 'captured_board',
                                           'board_sha256', 'routing', 'adoption', 'published_at', 'source_packet',
                                           'source_image', 'image', 'limitation'})
        self.assertEqual(bundle['images'], [{'view': 'top', 'path': 'top.png', 'sha256': None}])
        self.assertEqual((bundle['viewer'], diagnostic['adoption']['verified']), (False, False))

    def test_property_generator_covers_every_operand_position(self):
        """Review 433h H2: bare drives (both separators), POSIX and UNC operands at every position."""
        triples = [t for t, *_ in operand_triples()]
        kinds = {'forward-drive': r'(?<![/\\])C:/Users', 'back-drive': r'(?<![/\\])[Cd]:\\Users',
                 'posix': r'(?<![/\\:])/home/', 'unc': r'(?<![:\\])\\\\[a-z]',
                 'ipv6': r'(?i)file://(user-hidden@)?\[', 'interrupted-unc': r'hidden[( ]',
                 'interrupted-space': r'user-hidden name|hidden[")] \\'}
        for kind, pattern in kinds.items():
            for position in range(3):
                found = [t for t in triples if re.search(pattern, t[position])]
                self.assertTrue(found, (kind, position))
        surface = surface_texts()
        for kind, pattern in kinds.items():
            self.assertTrue([t for t in surface if re.search(pattern, t)], kind)

    def test_oracle_holds_for_every_composed_text_on_every_surface(self):
        assert_leak_free(self, surfaces)

    def test_oracle_fails_on_deliberately_leaky_publication(self):
        """Negative control: the oracle itself fails when publication leaks host text."""
        def scrubbed(text):  # the rejected design: a regex scrubber on free text (433c-433i)
            return surfaces(text) + re.sub(r'(?:[A-Za-z]:)?[\\/][^\s"\'()]*[\\/]', '<redacted>/', text)

        def unlisted(text):  # an unknown field passed through
            return surfaces(text) + json.dumps({'export': {'command': [text]}})

        def loose_basename(text):  # a basename without the local-path checks
            return surfaces(text) + PureWindowsPath(text).name

        def legacy_marker(text):  # text already starting with the marker trusted as redacted
            return surfaces(text) + (text if text.startswith('<redacted>') else '')

        def slash_only(text):  # a label allowlist that only refuses "/"
            return surfaces(text) + (text if '/' not in text else '')

        character_filter = re.compile(r"(?:[A-Za-z0-9 _.,;!?'\"()\[\]{}<>&=*#+|·–—’“”×-]|:(?= |$)){1,200}")

        def plain_text(text):  # review 433k H1: a label kept when it is plain text without path syntax
            return surfaces(text) + (text if character_filter.fullmatch(text) else '')

        def marker_name(text):  # review 433k H2: "<redacted>/NAME" trusted as an earlier basename
            name = text[len('<redacted>/'):] if text.startswith('<redacted>/') else ''
            return surfaces(text) + (name if re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.+-]*', name) else '')

        exact = (legacy_marker, slash_only, plain_text, marker_name)
        for leaky in (lambda text: text, scrubbed, unlisted, loose_basename, *exact):
            with self.assertRaisesRegex(AssertionError, 'unexpectedly found in'):
                assert_leak_free(self, leaky, rounds=200, stride=1 if leaky in exact else 7)
        assert_leak_free(self, surfaces, seed=1, rounds=200, stride=50)

    def test_composed_texts_are_never_published_on_any_core_surface(self):
        texts = surface_texts() + list(itertools.islice(corpus(), 0, None, 97))
        self.manifest['model_coverage']['board']['path'] = texts[0]
        self.manifest['model_coverage']['extra'] = {t: t for t in texts}
        self.manifest['warnings'] = texts + ['R1: missing 3D model ' + t for t in texts]
        self.save()
        viewer = self.make_viewer(private_location=texts[1], warnings=texts)
        metadata = json.loads((viewer / 'viewer.json').read_text())
        metadata['board']['path'] = texts[2]
        (viewer / 'viewer.json').write_text(json.dumps(metadata))
        bundle = publish(self.root, self.renders, label='Iteration 1', stage='placement', viewer=viewer)
        names = ('manifest.json', 'viewer/viewer.json')
        stored = [(self.root / 'bundles' / bundle / n).read_text() for n in names] + [(self.root / 'index.json').read_text()]
        with self.serving() as url:
            served = [urlopen(url + '/bundles/' + bundle + '/' + n).read().decode() for n in names]
            served.append(urlopen(url).read().decode())
            # Records written before the allowlist are projected when served.
            for n in names:
                path = self.root / 'bundles' / bundle / n
                record = json.loads(path.read_text())
                record.update(warnings=texts, label=texts[3], stage=texts[4], board={'path': texts[5], 'name': texts[6]},
                              captured_board=texts[7], **{t: t for t in texts})
                path.write_text(json.dumps(record))
            listing = json.loads((self.root / 'index.json').read_text())
            listing[0].update(warnings=texts, label=texts[3], stage=texts[4], **{t: t for t in texts})
            (self.root / 'index.json').write_text(json.dumps(listing))
            legacy = [urlopen(url + '/bundles/' + bundle + '/' + n).read().decode() for n in names]
            legacy.append(urlopen(url).read().decode())
        for where, body in zip(('stored manifest', 'stored viewer', 'stored index', 'served manifest',
                                'served viewer', 'served html', 'legacy manifest', 'legacy viewer', 'legacy html'),
                               stored + served + legacy):
            assert_no_uri_leak(self, body, where)
        record = json.loads(served[0])
        self.assertEqual(record['warnings'][:len(texts)], ['<redacted>'] * len(texts))
        for warning in record['warnings'][len(texts):]:  # a basename only when the whole text is a local path
            self.assertRegex(warning, r'^R1: missing 3D model (<redacted>|[A-Za-z0-9_][A-Za-z0-9_.+-]*)$')
        self.assertEqual(json.loads(served[1])['warnings'], ['<redacted>'] * len(texts))
        self.assertEqual(json.loads(legacy[0])['label'], '<redacted>')

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
        bundle = publish(self.root, out, label='Iteration 1', stage='placement', viewer=viewer, candidate=run)
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
        arguments = dict(candidate=self.run, board_sha256=self.board_hash, label='Diagnostic 1',
                         stage='diagnostic', routing='unrouted', adoption='not-adopted')
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
        self.assertEqual(manifest['captured_board'], 'board.kicad_pcb')  # a basename, like every published path
        self.assertEqual(manifest['schema'], 'agent-ee.gallery-diagnostic/4')
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
        self.assertIn('<h2>Diagnostic 1</h2>', body)
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
                               board_sha256=self.board_hash, label='Iteration 1', stage='placement', routing='unrouted',
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
                publish(self.root, renders, label='Iteration 1', stage='placement')
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

    # --- supplement review H3 and 433d-433i: host text in supplement surfaces --
    def legacy(self, identifier, **fields):
        """Store a supplement as an earlier publisher might have, beside the real one."""
        listing = json.loads((self.root/'diagnostics.json').read_text())
        record = dict(listing[0], id=identifier, **fields)
        (self.root/'diagnostics'/identifier).mkdir()
        (self.root/'diagnostics'/identifier/'manifest.json').write_text(json.dumps(record))
        (self.root/'diagnostics.json').write_text(json.dumps([record, *listing]))

    def surfaces(self, *identifiers, stored=True):
        """Stored manifest and listing (when publication wrote them), then everything served."""
        stored = [(self.root/'diagnostics'/identifiers[0]/'manifest.json').read_text(),
                  (self.root/'diagnostics.json').read_text()] if stored else []
        base = self.serving()
        served = [self.get(base + '/diagnostics/' + i + suffix) for i in identifiers
                  for suffix in ('/manifest.json', '/index.html')]
        return stored + served + [self.get(base)]

    def test_labels_and_stages_outside_the_vocabulary_are_refused(self):
        for value in ('see C:/Users/secret/project/board.kicad_pcb', '\\\\fileserver\\private-share\\c.png',
                      'FILE://private-server/', 'Historic analysis', 'Diagnostic', *REVIEW_EXAMPLES):
            self.refuses('label must be KIND NUMBER', label=value)
            self.refuses('stage must be one of', stage=value)
        self.assertFalse((self.root/'diagnostics.json').exists())

    def test_reviewed_host_text_and_marker_board_in_earlier_supplements_is_never_served(self):
        """Review 433k H1/H2 exact inputs in a legacy supplement's label, stage and captured_board."""
        identifier = self.add()
        exact = ('private-host private-user private-share', 'FILE: private-host', 'private-host; private-user')
        ids = []
        for n, (label, stage) in enumerate(zip(exact, exact[1:] + exact[:1])):
            ids.append(f'{n + 6}' * 12 + '-' + '7' * 12)
            self.legacy(ids[-1], label=label, stage=stage, captured_board='<redacted>/private-server',
                        schema='agent-ee.gallery-diagnostic/3')
        bodies = self.surfaces(*ids, identifier, stored=False)
        for n, body in enumerate(bodies):
            assert_no_uri_leak(self, body, n)
            self.assertNotIn('private', body, n)
        for body in bodies[0:6:2]:
            record = json.loads(body)
            self.assertEqual((record['label'], record['stage'], record['captured_board'], record['schema']),
                             ('<redacted>', '<redacted>', '<redacted>', 'agent-ee.gallery-diagnostic/4'))
        record = json.loads(bodies[6])
        self.assertEqual((record['label'], record['stage'], record['captured_board']),
                         ('Diagnostic 1', 'diagnostic', 'board.kicad_pcb'))
        self.assertIn('<h2>Diagnostic 1</h2>', bodies[-1])
        self.assertIn('Stage: Diagnostic', bodies[-1])

    def test_malformed_earlier_statuses_are_served_as_unknown(self):
        """Review 433k gap: list or mapping statuses render unknown instead of failing the request."""
        identifier = self.add()
        self.legacy('8' * 12 + '-' + '9' * 12, routing={'status': ['unrouted']}, adoption={'status': {'a': 1}})
        bodies = self.surfaces('8' * 12 + '-' + '9' * 12, identifier, stored=False)
        record = json.loads(bodies[0])
        self.assertEqual((record['routing']['status'], record['adoption']['status']), (None, None))
        self.assertIn('Status unknown: not bound to a verified retained candidate', bodies[1])
        self.assertEqual(bodies[-1].count('Status unknown: not bound'), 1)

    def test_host_text_in_earlier_supplement_records_is_never_served(self):
        identifier = self.add()
        texts = surface_texts()
        legacy_id = '6' * 12 + '-' + '7' * 12
        self.legacy(legacy_id, label=texts[0], stage=texts[1], captured_board=texts[2], limitation=texts[3],
                    source_packet={'name': texts[4], 'sha256': 'c' * 64, 'path': texts[5]}, notes=texts,
                    source_image={'name': texts[6]}, **{t: t for t in texts})
        bodies = self.surfaces(legacy_id, identifier, stored=False)
        for where, body in zip(('legacy manifest', 'legacy html', 'manifest', 'html', 'listing'), bodies):
            assert_no_uri_leak(self, body, where)
        record = json.loads(bodies[0])
        self.assertEqual((record['label'], record['stage'], record['source_packet'], record['source_image']['name']),
                         ('<redacted>', '<redacted>', {'name': '<redacted>', 'sha256': 'c' * 64}, '<redacted>'))
        self.assertEqual(record['captured_board'], gallery._basename(texts[2]))
        self.assertNotIn('notes', record)
        self.assertEqual(record['limitation'], json.loads(bodies[2])['limitation'])
        self.assertEqual(json.loads(bodies[2])['source_packet']['name'], 'INDEX.md')

    def test_real_packet_and_image_names_are_published_as_plain_basenames_or_redacted(self):
        # A file name cannot hold "/", so URIs use the backslash spelling. Every review
        # example and composed operand that fits a file name is a real packet file.
        names = {n.replace('/', '\\') for n in (*REVIEW_EXAMPLES, *surface_texts()[:90])}
        names = sorted(n for n in names if '\n' not in n and '\0' not in n and len(n.encode()) < 255)
        self.assertGreater(len(names), 60)
        for n, name in enumerate(names):
            directory = self.base / ('packet-' + str(n))
            directory.mkdir()
            self.packet = directory / name
            self.packet.write_text('diagnostic ' + str(n))
            identifier = self.add()
            bodies = self.surfaces(identifier)
            for where, body in zip(('stored manifest', 'stored listing', 'served manifest', 'served html', 'listing'),
                                   bodies):
                assert_no_uri_leak(self, body, where + ' ' + name)
            for body in bodies[:1] + bodies[2:3]:
                self.assertEqual(json.loads(body)['source_packet']['name'], '<redacted>', name)
        directory = self.base / 'plain'
        directory.mkdir()
        self.packet, image = directory / 'Free-space_analysis.v2.md', directory / 'annotated rev B.png'
        self.packet.write_text('plain')
        image.write_bytes(png_bytes(120))
        record = json.loads(self.surfaces(self.add(image=image))[0])
        self.assertEqual((record['source_packet']['name'], record['source_image']['name'], record['captured_board']),
                         ('Free-space_analysis.v2.md', '<redacted>', 'board.kicad_pcb'))
