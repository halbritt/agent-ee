import hashlib
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from agent_ee.datasheet_capture import capture_datasheets
from test_datasheet_facts import text_pdf


class DatasheetCaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        text_pdf(self.root / 'original.pdf', ['CANL 6', 'CANH 7'])
        self.pdf = (self.root / 'original.pdf').read_bytes()
        self.requests = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                owner.requests.append(self.path)
                if self.path == '/identified':
                    if self.headers.get('User-Agent') != 'agent-ee/1 (public datasheet acquisition)':
                        self.send_error(403, 'Identify the acquisition client')
                        return
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(owner.pdf)
                    return
                if self.path == '/slow':
                    import time
                    time.sleep(0.6)
                    return
                if self.path in ('/redirect', '/bad-redirect'):
                    self.send_response(302)
                    self.send_header('Location', 'file:///etc/passwd' if self.path == '/bad-redirect' else '/pdf')
                    self.end_headers()
                    return
                self.send_response(200)
                self.end_headers()
                self.wfile.write(owner.pdf if self.path == '/pdf' else
                                 b'%PDF-1.4\ntruncated' if self.path == '/truncated' else b'<html>error</html>')

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def stop_server(self):
        if self.thread.is_alive():
            self.server.shutdown()
            self.server.server_close()
            self.thread.join()

    def test_identified_client_downloads_pdf_from_user_agent_gated_source(self):
        import urllib.error
        import urllib.request
        with self.assertRaises(urllib.error.HTTPError) as refused:
            urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                self.url + '/identified', timeout=3)
        self.assertEqual(refused.exception.code, 403)
        result = capture_datasheets(
            [{'id': 'U1', 'datasheet_url': self.url + '/identified'}],
            self.root / 'identified')
        self.assertEqual(result['outcome'], 'captured')
        record = result['parts'][0]
        self.assertEqual((self.root / 'identified' / record['path']).read_bytes(), self.pdf)
        self.assertEqual(record['sha256'], hashlib.sha256(self.pdf).hexdigest())

    def test_cancellation_reaps_owned_child_and_allows_retry(self):
        import os
        import subprocess
        import sys
        from unittest.mock import patch
        from agent_ee import datasheet_capture as dc

        real_popen = subprocess.Popen
        children = []
        cancellation = KeyboardInterrupt('owned capture cancellation')

        def launch(*args, **kwargs):
            child = real_popen([sys.executable, '-c', 'import time; time.sleep(30)'],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            children.append(child)
            def cancel(**kwargs):
                raise cancellation

            child.communicate = cancel
            return child

        try:
            with patch.object(dc.subprocess, 'Popen', launch):
                with self.assertRaises(KeyboardInterrupt) as raised:
                    capture_datasheets([{'id': 'U1', 'datasheet_url': self.url + '/pdf'}],
                                       self.root / 'cancelled')
            self.assertIs(raised.exception, cancellation)
            self.assertIsNotNone(children[0].returncode)
            with self.assertRaises(ChildProcessError):
                os.waitpid(children[0].pid, os.WNOHANG)
            self.assertFalse((self.root / 'cancelled').exists())
            self.assertEqual(list(self.root.glob('.datasheet-*')), [])
        finally:
            # Only our direct, unreaped child can retain this PID.
            for child in children:
                if child.poll() is None:
                    self.assertEqual(dc._process_identity(child.pid)[1], str(Path.cwd()))
                    child.kill()
                child.wait()
        result = capture_datasheets([{'id': 'U1', 'datasheet_url': self.url + '/pdf'}],
                                    self.root / 'cancelled')
        self.assertEqual(result['outcome'], 'captured')

    def test_exceptional_fetch_lifecycle_reaps_real_children(self):
        import os
        import subprocess
        import sys
        from unittest.mock import patch
        from agent_ee import datasheet_capture as dc

        real_popen, real_identity = subprocess.Popen, dc._process_identity
        real_stop = dc._stop_fetch
        for boundary in ('identity', 'communicate', 'exited', 'cleanup'):
            with self.subTest(boundary=boundary):
                children = []
                failure = SystemExit('identity cancellation') if boundary == 'identity' else OSError('read failure')

                def launch(*args, **kwargs):
                    child = real_popen([sys.executable, '-c', 'import time; time.sleep(30)'],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    children.append(child)

                    def fail(**kwargs):
                        if boundary == 'exited':
                            owner = real_identity(child.pid)
                            self.assertEqual(real_identity(child.pid), owner)
                            child.kill()
                            child.wait()
                        raise failure

                    child.communicate = fail
                    return child

                def identify(pid):
                    if boundary == 'identity' and not getattr(identify, 'failed', False):
                        identify.failed = True
                        raise failure
                    return real_identity(pid)

                def stop(child, owner):
                    real_stop(child, owner)
                    if boundary == 'cleanup':
                        raise OSError('secondary cleanup failure')

                try:
                    with patch.object(dc.subprocess, 'Popen', launch), patch.object(dc, '_process_identity', identify), patch.object(dc, '_stop_fetch', stop):
                        with self.assertRaises(type(failure)) as raised:
                            capture_datasheets([{'id': 'U1', 'datasheet_url': self.url + '/pdf'}],
                                               self.root / boundary)
                    self.assertIs(raised.exception, failure)
                    if boundary == 'cleanup':
                        self.assertIn('secondary cleanup failure', raised.exception.__notes__[0])
                    self.assertIsNotNone(children[0].returncode)
                    with self.assertRaises(ChildProcessError):
                        os.waitpid(children[0].pid, os.WNOHANG)
                    self.assertFalse((self.root / boundary).exists())
                    self.assertEqual(list(self.root.glob('.datasheet-*')), [])
                finally:
                    for child in children:
                        if child.poll() is None:
                            self.assertEqual(real_identity(child.pid)[1], str(Path.cwd()))
                            child.kill()
                        child.wait()

    def test_real_child_success_and_timeout_are_reaped(self):
        import os
        import subprocess
        from unittest.mock import patch
        from agent_ee import datasheet_capture as dc

        real_popen = subprocess.Popen
        children = []

        def launch(*args, **kwargs):
            child = real_popen(*args, **kwargs)
            children.append(child)
            return child

        with patch.object(dc.subprocess, 'Popen', launch):
            success = capture_datasheets([{'id': 'U1', 'datasheet_url': self.url + '/pdf'}],
                                        self.root / 'success')
            timeout = capture_datasheets([{'id': 'U1', 'datasheet_url': self.url + '/slow'}],
                                        self.root / 'timeout', timeout=0.05)
        self.assertEqual(success['outcome'], 'captured')
        self.assertEqual(timeout['parts'][0]['reason'], 'fetch deadline exceeded')
        for child in children:
            self.assertIsNotNone(child.returncode)
            with self.assertRaises(ChildProcessError):
                os.waitpid(child.pid, os.WNOHANG)

    def test_deduplicates_and_retains_each_part_provenance(self):
        parts = [{'id': 'U1', 'datasheet_url': self.url + '/redirect'},
                 {'id': 'U2', 'datasheet_url': self.url + '/redirect'}]
        result = capture_datasheets(parts, self.root / 'capture')
        self.assertEqual(self.requests, ['/redirect', '/pdf'])
        self.assertEqual(result['outcome'], 'captured')
        first, second = result['parts']
        self.assertEqual(first['path'], second['path'])
        self.assertEqual(first['sha256'], hashlib.sha256(self.pdf).hexdigest())
        self.assertEqual(first['final_url'], self.url + '/pdf')
        self.assertTrue(first['fetched_at'].endswith('+00:00'))
        self.assertFalse(first['missing'])
        self.assertEqual((self.root / 'capture' / first['path']).read_bytes(), self.pdf)

    def test_missing_html_bad_scheme_and_size_are_missing_per_part(self):
        parts = [{'id': 'none', 'datasheet_url': None},
                 {'id': 'html', 'datasheet_url': self.url + '/html'},
                 {'id': 'file', 'datasheet_url': 'file:///etc/passwd'},
                 {'id': 'large', 'datasheet_url': self.url + '/pdf'}]
        result = capture_datasheets(parts, self.root / 'missing', max_bytes=32)
        self.assertEqual(result['outcome'], 'missing')
        self.assertTrue(all(p['missing'] and p['path'] is None and p['sha256'] is None for p in result['parts']))
        self.assertEqual(list((self.root / 'missing').glob('*.pdf')), [])
        self.assertEqual(self.requests, ['/html', '/pdf'])

    def test_existing_directory_traversal_and_symlink_are_refused_before_fetch(self):
        (self.root / 'existing').mkdir()
        (self.root / 'link').symlink_to(self.root, target_is_directory=True)
        parts = [{'id': '../U1', 'datasheet_url': self.url + '/pdf'}]
        for out in [self.root / 'existing', self.root / 'link/new', self.root / '../escaped']:
            with self.subTest(out=out), self.assertRaises((ValueError, FileExistsError)):
                capture_datasheets(parts, out)
        self.assertEqual(self.requests, [])

    def test_limits_and_duplicate_ids_refused_without_output(self):
        for parts, kwargs in [([{'id': 'U1', 'datasheet_url': None}] * 2, {}),
                              ([{'id': 'U1', 'datasheet_url': None}], {'timeout': float('nan')}),
                              ([{'id': 'U1', 'datasheet_url': None}], {'max_bytes': 0})]:
            with self.assertRaises(ValueError):
                capture_datasheets(parts, self.root / 'out', **kwargs)
            self.assertFalse((self.root / 'out').exists())

    def test_output_write_failure_does_not_publish_partial_capture(self):
        from unittest.mock import patch
        real_open = Path.open

        def fail_manifest(path, *args, **kwargs):
            if path.name == 'manifest.json':
                raise OSError('injected disk failure')
            return real_open(path, *args, **kwargs)

        with patch.object(Path, 'open', fail_manifest):
            with self.assertRaisesRegex(OSError, 'disk failure'):
                capture_datasheets([{'id': 'U1', 'datasheet_url': self.url + '/pdf'}], self.root / 'out')
        self.assertFalse((self.root / 'out').exists())
        self.assertEqual(list(self.root.glob('.datasheet-*')), [])

    def test_deterministic_filename_does_not_use_part_or_remote_path(self):
        parts = [{'id': '../../unsafe', 'datasheet_url': self.url + '/pdf'}]
        first = capture_datasheets(parts, self.root / 'first')['parts'][0]
        second = capture_datasheets(parts, self.root / 'second')['parts'][0]
        self.assertEqual(first['path'], second['path'])
        self.assertEqual(first['path'], hashlib.sha256((self.url + '/pdf').encode()).hexdigest() + '.pdf')

    def test_offline_native_pin_map_consumes_captured_original_pdf_and_seal(self):
        import contextlib
        import io
        import subprocess
        from agent_ee.run_record import run_review, verify
        from agent_ee.storage import digest, write_json
        from toolchain import require_toolchain
        require_toolchain(native=True, tools=('pdftotext',))
        captured = self.root / 'captured'
        part = capture_datasheets([{'id': 'U1', 'datasheet_url': self.url + '/pdf'}], captured)['parts'][0]
        self.stop_server()
        facts = [{'id': name.lower(), 'part_number': 'Original', 'kind': 'pin_function',
                  'pin': pin, 'value': name, 'unit': '', 'conditions': 'original fixture',
                  'source': {'path': 'sheets/' + part['path'], 'sha256': part['sha256'],
                             'page': 1, 'locator': 'original row', 'quote': name + ' ' + pin}}
                 for name, pin in [('CANL', '6'), ('CANH', '7')]]
        write_json(self.root / 'facts.json', {'schema': 'agent-ee.datasheet-facts/1', 'facts': facts})
        version = subprocess.run(['/usr/bin/pdftotext', '-v'], capture_output=True, text=True, check=True).stderr.splitlines()[0]
        repo = Path(__file__).resolve().parents[1]
        scope = {'schema': 'agent-ee.scope/1', 'name': 'captured original PDF', 'scope': 'declared pin names',
                 'inputs': [{'path': str(captured), 'at': 'sheets'},
                            {'path': str(self.root / 'facts.json'), 'at': 'facts.json'},
                            {'path': str(repo / 'fixtures/datasheet-pins/control'), 'at': 'project'},
                            {'path': '/usr/share/kicad/template', 'at': 'stock/template',
                             'include': ['sym-lib-table', 'fp-lib-table']}],
                 'schematic': 'project/pins.kicad_sch', 'board': 'project/pins.kicad_pcb',
                 'checks': [{'id': 'netlist', 'kind': 'netlist'},
                            {'id': 'facts', 'kind': 'datasheet_facts', 'params': {'facts': 'facts.json',
                             'extractor': {'path': '/usr/bin/pdftotext', 'sha256': digest('/usr/bin/pdftotext'), 'version': version}}},
                            {'id': 'pins', 'kind': 'pin_map', 'params': {'facts_check': 'facts',
                             'netlist_check': 'netlist', 'symbols': [{'reference': 'U1',
                             'part_number': 'Original', 'facts': ['canl', 'canh']}]}}]}
        write_json(self.root / 'scope.json', scope)
        for missing in (False, True):
            if missing:
                (captured / part['path']).unlink()
            run = self.root / ('missing-run' if missing else 'run')
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(run_review(self.root / 'scope.json', run, 90, checks_only=True), 1)
            result = verify(run)
            checks = {c['id']: c for c in result['checks']}
            self.assertEqual((checks['netlist']['execution'], checks['netlist']['outcome']), ('ok', 'info'), checks['netlist'])
            self.assertEqual(checks['pins']['outcome'], 'unknown' if missing else 'pass', checks['pins'])
            self.assertEqual(checks['facts']['execution'], 'unsupported' if missing else 'ok', checks['facts'])
            from agent_ee.datasheet_capture import bindings_from_native
            bindings = bindings_from_native(run / 'work/netlist/design.xml', {'U1': {'Datasheet': self.url + '/pdf'}})
            self.assertEqual(bindings, [{'id': 'U1', 'datasheet_url': self.url + '/pdf'}])
            self.assertEqual(bindings_from_native(run / 'work/netlist/design.xml', {}),
                             [{'id': 'U1', 'datasheet_url': None}])
            with self.assertRaises(ValueError):
                bindings_from_native(run / 'work/netlist/design.xml', {'U99': {'Datasheet': 'x'}})
            from agent_ee.storage import read_json
            invocations = list((run / 'work').rglob('execution.json'))
            self.assertTrue(invocations)
            for invocation in invocations:
                self.assertIn('--unshare-net', read_json(invocation)['sandbox_argv'])
            # Public verify above validates normal coordinator evidence seals.

    def test_deadline_invalid_redirect_and_truncated_pdf_never_publish_pdf(self):
        for route in ('/slow', '/bad-redirect', '/truncated'):
            with self.subTest(route=route):
                result = capture_datasheets([{'id': 'U1', 'datasheet_url': self.url + route}],
                                            self.root / route[1:], timeout=0.2 if route == '/slow' else 3)
                part = result['parts'][0]
                self.assertTrue(part['missing'], part)
                self.assertIsNone(part['sha256'])
                self.assertFalse(list((self.root / route[1:]).glob('*.pdf')))

    def test_cli_exit_and_manifest_for_missing_url(self):
        import subprocess
        from agent_ee.storage import write_json, read_json
        write_json(self.root / 'bindings.json', [{'id': 'U1', 'datasheet_url': None}])
        command = ['python3', '-m', 'agent_ee.datasheet_capture', str(self.root / 'bindings.json'),
                   '--out', str(self.root / 'cli')]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=10)
        self.assertEqual(completed.returncode, 2, completed.stderr)
        self.assertEqual(read_json(self.root / 'cli/manifest.json')['outcome'], 'missing')
        repeated = subprocess.run(command, capture_output=True, text=True, timeout=10)
        self.assertEqual(repeated.returncode, 2)

    def test_fetch_worker_cli_cannot_bypass_size_or_time_limits(self):
        import subprocess
        for seconds, size in [('inf', '1024'), ('1', '-1'), ('61', '1024')]:
            completed = subprocess.run(['python3', '-m', 'agent_ee.datasheet_capture', '--fetch',
                                        self.url + '/pdf', seconds, size],
                                       capture_output=True, text=True, timeout=10)
            self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(self.requests, [])
