"""AGENTEE-618: capture current PDF tools while retaining each run's identity."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'fixtures/acquisition-typed/capture_manufacturer_text.py'
spec = importlib.util.spec_from_file_location('manufacturer_text_capture', SCRIPT)
capture_text = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture_text)


class CaptureToolIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / 'base'
        self.base.mkdir()
        pdf = self.base / 'part.pdf'
        pdf.write_bytes(b'synthetic captured PDF')
        source_manifest = self.base / 'source.json'
        source_manifest.write_bytes(capture_text.json_bytes({'parts': [
            {'id': 'part', 'path': pdf.name, 'sha256': capture_text.digest(pdf.read_bytes())}]}))
        self.tools = {}
        for name in ('pdfinfo', 'pdftotext'):
            path = self.root / name
            path.write_bytes((name + ' build one').encode())
            self.tools[name] = {'path': str(path)}
        declaration = {
            'base_inventory_sha256': capture_text.digest(capture_text.json_bytes({
                p.name: capture_text.digest(p.read_bytes()) for p in self.base.iterdir()})),
            'tools': self.tools,
            'limitations': ['Synthetic test input; no PDF tool executes.'],
            'documents': [{'id': 'part', 'physical_pages': 1, 'source': {
                'manifest_path': 'source.json',
                'manifest_sha256': capture_text.digest(source_manifest.read_bytes()),
                'part_id': 'part', 'path': 'part.pdf', 'sha256': capture_text.digest(pdf.read_bytes())}}]}
        (self.root / 'manufacturer-text-sources.json').write_bytes(capture_text.json_bytes(declaration))
        self.procedure = self.root / 'capture_manufacturer_text.py'
        self.procedure.write_bytes(SCRIPT.read_bytes())
        self.version = '24.02.0'
        self.version_failure = None
        self.drift = None
        self.addCleanup(patch.stopall)
        patch.object(capture_text, '__file__', str(self.procedure)).start()
        # The subprocess boundary supplies deterministic PDF text and version responses.
        patch.object(capture_text.subprocess, 'run', side_effect=self.process).start()

    def process(self, argv, **kwargs):
        name = Path(argv[0]).name
        code, stdout, stderr = 0, b'', b''
        if argv[1] == '-v':
            stderr = f'{name} version {self.version}\n'.encode()
            if self.version_failure == 'empty':
                stderr = b''
            elif self.version_failure == 'nonzero':
                code = 1
            elif self.version_failure == 'wrong-tool':
                stderr = b'unrelated version 1\n'
            elif self.version_failure == 'changed-binary':
                Path(argv[0]).write_bytes(b'replaced during version probe')
        elif name == 'pdfinfo':
            stdout = b'Pages: 1\n'
        else:
            stdout = b'Captured fact text.\f'
            if self.drift == 'binary':
                Path(argv[0]).write_bytes(b'replaced during extraction')
            elif self.drift == 'version':
                self.version = '26.01.0'
        return subprocess.CompletedProcess(argv, code, stdout=stdout, stderr=stderr)

    def test_new_tool_build_is_recorded_without_rewriting_prior_capture(self):
        first = capture_text.capture(self.base, self.root / 'first')
        first_bytes = (self.root / 'first/manifest.json').read_bytes()
        self.version = '26.01.0'
        for selector in self.tools.values():
            Path(selector['path']).write_bytes(b'updated installed tool')
        second = capture_text.capture(self.base, self.root / 'second')
        for name, selector in self.tools.items():
            self.assertNotEqual(first['tools'][name]['sha256'], second['tools'][name]['sha256'])
            self.assertEqual(second['tools'][name], {
                'path': selector['path'], 'sha256': capture_text.digest(b'updated installed tool'),
                'version': name + ' version 26.01.0'})
        self.assertEqual(second['status'], 'complete')
        self.assertEqual((self.root / 'first/manifest.json').read_bytes(), first_bytes)
        self.assertEqual((self.root / 'second/part.txt').read_bytes(), b'Captured fact text.\f')

    def test_tool_drift_during_extraction_retains_failed_manifest(self):
        for drift in ('binary', 'version'):
            with self.subTest(drift=drift):
                self.drift = drift
                self.version = '24.02.0'
                output = self.root / drift
                with self.assertRaisesRegex(ValueError, 'extraction binary changed'):
                    capture_text.capture(self.base, output)
                manifest = json.loads((output / 'manifest.json').read_bytes())
                self.assertEqual(manifest['status'], 'failed')
                self.assertTrue(manifest['failures'])
                self.assertEqual(manifest['tools']['pdftotext']['version'], 'pdftotext version 24.02.0')
                self.assertTrue((output / 'part.txt').is_file())

    def test_unusable_version_or_probe_drift_refuses_before_output(self):
        for failure in ('empty', 'nonzero', 'wrong-tool', 'changed-binary'):
            with self.subTest(failure=failure):
                self.version_failure = failure
                output = self.root / failure
                with self.assertRaisesRegex(ValueError, 'extraction binary'):
                    capture_text.capture(self.base, output)
                self.assertFalse(output.exists())

    def test_descriptor_selects_tools_without_installed_build_constraints(self):
        descriptor = json.loads((ROOT / 'fixtures/acquisition-typed/manufacturer-text-sources.json').read_bytes())
        self.assertEqual(descriptor['tools'], {
            'pdfinfo': {'path': '/usr/bin/pdfinfo'}, 'pdftotext': {'path': '/usr/bin/pdftotext'}})
