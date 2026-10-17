"""Exercise native board export and the offline browser bundle together."""

import hashlib
import json
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.request import urlopen

from agent_ee.viewer import serve_viewer


ROOT = Path(__file__).resolve().parents[1]


class NativeViewerIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("kicad-cli"), "native KiCad CLI is not installed")
    def test_native_export_is_displayable_from_a_local_offline_bundle(self):
        capability = subprocess.run(["kicad-cli", "pcb", "export", "glb", "--help"],
                                    capture_output=True, text=True, timeout=20)
        if capability.returncode or "--include-soldermask" not in capability.stdout:
            self.skipTest("installed KiCad lacks native GLB surface export")
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            shutil.copytree(ROOT / "fixtures/rc/control", source)
            before = {p.name: p.read_bytes() for p in source.iterdir() if p.is_file()}
            out = Path(tmp) / "viewer"
            result = subprocess.run([sys.executable, str(ROOT / "bin/agent-ee"), "view3d",
                                     str(source / "rc_filter.kicad_pcb"), "--out", str(out),
                                     "--export-only"], capture_output=True, text=True, timeout=180)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(before, {p.name: p.read_bytes() for p in source.iterdir() if p.is_file()})
            metadata = json.loads((out / "viewer.json").read_text())
            self.assertIsNone(metadata["candidate_id"])
            self.assertEqual(metadata["board"]["sha256"],
                             hashlib.sha256(before["rc_filter.kicad_pcb"]).hexdigest())
            model = out / metadata["model"]
            model_bytes = model.read_bytes()
            magic, version, length, json_length, chunk_type = struct.unpack("<4sIIII", model_bytes[:20])
            self.assertEqual((magic, version, length, chunk_type),
                             (b"glTF", 2, len(model_bytes), 0x4E4F534A))
            geometry = json.loads(model_bytes[20:20 + json_length])
            self.assertGreater(len(geometry["meshes"]), 0)
            self.assertTrue(any(mesh["primitives"] for mesh in geometry["meshes"]))

            server = serve_viewer(out)
            self.assertEqual(server.server_address[0], "127.0.0.1")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_address[1]}/"
                with urlopen(base, timeout=10) as response:
                    html = response.read().decode()
                self.assertIn("3D PCB viewer", html)
                with urlopen(urljoin(base, metadata["model"]), timeout=10) as response:
                    self.assertEqual(response.read(), model_bytes)
                with urlopen(urljoin(base, "viewer.json"), timeout=10) as response:
                    self.assertEqual(json.load(response), metadata)
                with urlopen(urljoin(base, "viewer.css"), timeout=10) as response:
                    self.assertIn("text/css", response.headers["Content-Type"])
                self._assert_module_closure(base)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
            self.assertFalse(thread.is_alive())

    def _assert_module_closure(self, base):
        pending, visited = [urljoin(base, "viewer.js")], set()
        while pending:
            url = pending.pop()
            if url in visited:
                continue
            visited.add(url)
            with urlopen(url, timeout=10) as response:
                self.assertIn("javascript", response.headers["Content-Type"])
                module = response.read().decode()
            imports = re.findall(r"(?m)^import(?:[\w*{},$\s]+from)?\s*['\"]([^'\"]+)", module)
            imports += re.findall(r"\bimport\(\s*['\"]([^'\"]+)['\"]\s*\)", module)
            for specifier in imports:
                target = urljoin(base, "vendor/three.module.js") if specifier == "three" else urljoin(url, specifier)
                self.assertEqual(urlsplit(target).netloc, urlsplit(base).netloc,
                                 f"module depends on a network resource: {specifier}")
                pending.append(target)
        self.assertGreaterEqual(len(visited), 5, "viewer module dependency graph was not traversed")


if __name__ == "__main__":
    unittest.main()
