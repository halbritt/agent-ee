"""scripts/refresh_viewer_vendor.py: a newer three.js release is written and recorded only when the
viewer's imports and the loader and controls API it uses are unchanged. Upstream is a local stand-in."""
import hashlib
import http.server
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('refresh_viewer_vendor', ROOT / 'scripts/refresh_viewer_vendor.py')
refresh_viewer_vendor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(refresh_viewer_vendor)
VENDOR = ROOT / 'src/agent_ee/viewer_assets/vendor'
VIEWER = ROOT / 'src/agent_ee/viewer_assets/viewer.js'
LOCAL_IMPORT = "from './BufferGeometryUtils.js'"
UPSTREAM_IMPORT = "from '../utils/BufferGeometryUtils.js'"


def sha256(payload):
    return hashlib.sha256(payload).hexdigest()


class RefreshTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.vendor = Path(scratch.name) / 'vendor'
        shutil.copytree(VENDOR, self.vendor)
        self.manifest = json.loads((self.vendor / 'manifest.json').read_text())
        self.before = {p.name: p.read_bytes() for p in self.vendor.iterdir()}
        # A later release whose files differ from the vendored ones but keep the viewer's API.
        self.release = {}
        for name, entry in self.manifest['files'].items():
            payload = (VENDOR / name).read_bytes().replace(LOCAL_IMPORT.encode(), UPSTREAM_IMPORT.encode())
            self.release[entry['source'].replace('/r180/', '/r999/')] = payload + b'\n// r999\n'
        self.requested = []

    def get(self, url):
        self.requested.append(url)
        return self.release[url]

    def refresh(self):
        return refresh_viewer_vendor.refresh(self.vendor, VIEWER, get=self.get, tags=lambda repository: 'r999')

    def edit(self, name, old, new):
        url = self.manifest['files'][name]['source'].replace('/r180/', '/r999/')
        self.assertIn(old.encode(), self.release[url])
        self.release[url] = self.release[url].replace(old.encode(), new.encode())

    def test_release_with_the_same_viewer_api_is_written_and_recorded(self):
        report, status = self.refresh()
        self.assertEqual((status, report['problems']), ('written', []))
        manifest = json.loads((self.vendor / 'manifest.json').read_text())
        self.assertEqual((manifest['version'], manifest['upstream']),
                         ('0.999.0', 'https://github.com/mrdoob/three.js/tree/r999'))
        self.assertEqual(sorted(self.requested), sorted(self.release))
        for name, entry in manifest['files'].items():
            with self.subTest(name):
                upstream = self.release[entry['source']]
                written = (self.vendor / name).read_bytes()
                self.assertEqual((entry['upstream_sha256'], entry['sha256']), (sha256(upstream), sha256(written)))
                self.assertEqual(entry['transforms'], self.manifest['files'][name]['transforms'])
        # The recorded transform is applied again; the license travels with the files.
        loader = (self.vendor / 'GLTFLoader.js').read_text()
        self.assertIn(LOCAL_IMPORT, loader)
        self.assertNotIn(UPSTREAM_IMPORT, loader)
        self.assertTrue((self.vendor / 'LICENSE').read_bytes().endswith(b'// r999\n'))
        # Already current: nothing is fetched again.
        self.requested.clear()
        self.assertEqual(self.refresh()[1], 'current')
        self.assertEqual(self.requested, [])

    def test_release_that_changes_what_the_viewer_uses_is_refused_and_nothing_written(self):
        for case, (name, old, new, problem) in {
            'controls member renamed': ('OrbitControls.js', 'enableDamping', 'enableSmoothing',
                                        'controls_members enableDamping'),
            # Review of ec8976ca: renamed in code, still named in two comments.
            'controls member renamed in code, named in comments': (
                'OrbitControls.js', 'this.enableDamping', 'this.enableSmoothing', 'controls_members enableDamping'),
            'controls member named only in a string': (
                'OrbitControls.js', 'this.enableDamping =', "console.warn( 'this.enableDamping = gone' ); this.enableSmoothing =",
                'controls_members enableDamping'),
            'controls base class changed': ('OrbitControls.js', 'class OrbitControls extends Controls',
                                            'class OrbitControls extends EventDispatcher', 'bases OrbitControls'),
            'controls constructor changed': ('OrbitControls.js', 'constructor( object, domElement = null )',
                                             'constructor( object, domElement, options )',
                                             'constructors OrbitControls'),
            'controls event renamed': ('OrbitControls.js', "type: 'start'", "type: 'begin'", 'controls_events start'),
            'loader imports a module that is not vendored': (
                'GLTFLoader.js', "} from 'three';", "} from 'three';\nimport { clone } from '../utils/SkeletonUtils.js';",
                'imports ../utils/SkeletonUtils.js, which is not vendored'),
            'core export the viewer uses renamed': ('three.module.js', ' as WebGLRenderer,', ' as WebGPURenderer,',
                                                    'three_exports WebGLRenderer'),
        }.items():
            with self.subTest(case):
                self.setUp()
                self.edit(name, old, new)
                report, status = self.refresh()
                self.assertEqual(status, 'refused')
                self.assertTrue(any(problem in line for line in report['problems']), report['problems'])
                self.assertEqual({p.name: p.read_bytes() for p in self.vendor.iterdir()}, self.before)

    def test_regular_expression_after_return_is_not_a_definition(self):
        # Review of 5704964d: executable this.enableDamping renamed, the old text left in a regex.
        self.edit('OrbitControls.js', 'this.enableDamping', 'this.enableSmoothing')
        url = self.manifest['files']['OrbitControls.js']['source'].replace('/r180/', '/r999/')
        self.release[url] += b'\nfunction removedFeature() { return /this.enableDamping = gone/; }\n'
        report, status = self.refresh()
        self.assertEqual(status, 'refused')
        self.assertTrue(any('controls_members enableDamping' in line for line in report['problems']), report['problems'])
        self.assertEqual({p.name: p.read_bytes() for p in self.vendor.iterdir()}, self.before)

    def test_code_drops_regular_expressions_after_expression_keywords_but_keeps_division(self):
        for keyword in sorted(refresh_viewer_vendor.KEYWORDS_BEFORE_REGEX):
            with self.subTest(keyword):
                text = f'function f() {{ {keyword} /this.enableDamping = gone/; }}'
                self.assertNotIn('enableDamping', refresh_viewer_vendor.code(text, strings=False))
        division = 'a = b / c / d; e = (f) / g; h = i[0] / 2; j = x.return / 2; k = returned / 3;'
        self.assertEqual(refresh_viewer_vendor.code(division, strings=False), division)

    def test_missing_source_is_refused_with_its_url(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root)
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), http.server.SimpleHTTPRequestHandler)
        server.RequestHandlerClass = lambda *args, **kwargs: http.server.SimpleHTTPRequestHandler(
            *args, directory=str(root), **kwargs)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f'http://127.0.0.1:{server.server_port}/r999/build/three.module.min.js'
        with self.assertRaisesRegex(refresh_viewer_vendor.CheckFailed, 'r999/build/three.module.min.js does not exist'):
            refresh_viewer_vendor.fetch(url)

    def test_newest_release_is_the_highest_numbered_tag(self):
        repository = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, repository)
        git = lambda *args: subprocess.run(['git', '-C', str(repository), '-c', 'user.name=fixture',
                                            '-c', 'user.email=fixture@invalid', '-c', 'commit.gpgsign=false',
                                            *args], check=True, capture_output=True)
        git('init', '-q')
        git('commit', '-q', '--allow-empty', '-m', 'Owned tag fixture')
        for tag in ('r9', 'r100', 'r99', 'v1.0', 'r100-rc'):
            git('tag', tag)
        self.assertEqual(refresh_viewer_vendor.latest_release(str(repository)), 'r100')


if __name__ == '__main__':
    unittest.main()
