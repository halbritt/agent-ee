"""Full capture orchestration over scripted loopback HTTP, without vendor access."""

import json
import shutil
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from agent_ee.design import DesignError
from agent_ee.design_capture import capture_design, validate_capture_manifest
from agent_ee.design_compile import compile_design
from tests.test_design_compile import design_fixture
from tests.test_jlc_stock_capture import row, search
from tests.land_pattern_fixtures import chip, component


class CaptureHttpTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        original = self.root / "original"
        original.mkdir()
        self.design = design_fixture(original)
        self.kicad = self.root / "kicad"
        shutil.copytree(original / "cad", self.kicad / "symbols")
        shutil.copytree(
            original / "cad/Owned.pretty", self.kicad / "footprints/Owned.pretty"
        )
        self.requests = []
        self.stock_status = self.supplier_status = 200
        self.pdf_status = 200
        self.supplier_raw = (
            b'{"success":true,"result":' + component("C1", chip(4, 2, 2)) + b"}"
        )
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = json.loads(
                    self.rfile.read(int(self.headers["Content-Length"]))
                )
                owner.requests.append((self.command, self.path, payload))
                self.reply(
                    owner.stock_status,
                    search(
                        row(
                            "C1",
                            500,
                            attributes=[
                                dict(
                                    attribute_name_en="Resistance",
                                    attribute_value_name="10kΩ",
                                )
                            ],
                            dataManualFileAccessIdUrl=owner.url + "/signed.pdf",
                            dataManualUrl=owner.url + "/html-must-not-be-used",
                            dataManualOfficialLink=owner.url + "/official.pdf",
                        )
                    ),
                )

            def do_GET(self):
                owner.requests.append((self.command, self.path, None))
                if self.path == "/supplier/C1":
                    self.reply(owner.supplier_status, owner.supplier_raw)
                elif self.path == "/signed.pdf":
                    self.reply(
                        owner.pdf_status, b"%PDF-1.4\nsynthetic signed PDF\n%%EOF\n"
                    )
                elif self.path == "/official.pdf":
                    self.reply(200, b"%PDF-1.4\nsynthetic official PDF\n%%EOF\n")
                else:
                    self.reply(500, b"unexpected URL")

            def reply(self, status, raw):
                self.send_response(status)
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        thread = threading.Thread(target=self.server.serve_forever)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def capture(self, name="captured"):
        out = self.root / name
        capture_design(
            self.design,
            out,
            kicad_root=self.kicad,
            stock_endpoint=self.url + "/stock",
            supplier_endpoint=self.url + "/supplier/{lcsc}",
        )
        return out

    def test_capture_retains_http_bytes_and_compiles_with_signed_pdf_precedence(self):
        captured = self.capture()
        validate_capture_manifest(captured)
        self.assertEqual(
            (captured / "supplier/C1.full.json").read_bytes(), self.supplier_raw
        )
        self.assertEqual(
            [r[:2] for r in self.requests],
            [("POST", "/stock"), ("GET", "/signed.pdf"), ("GET", "/supplier/C1")],
        )
        compile_design(self.design, captured, self.root / "bundle")
        with self.assertRaisesRegex(DesignError, "immutable"):
            self.capture()

    def test_failed_signed_pdf_falls_back_and_retains_the_failed_attempt(self):
        self.pdf_status = 403
        captured = self.capture()
        manifest = json.loads(
            (captured / "datasheet-attempts/signed/manifest.json").read_bytes()
        )
        self.assertTrue(manifest["parts"][0]["missing"])
        final = json.loads((captured / "datasheets/manifest.json").read_bytes())
        self.assertEqual(final["parts"][0]["url"], self.url + "/official.pdf")
        self.assertFalse(final["parts"][0]["missing"])
        validate_capture_manifest(captured)

    def test_stock_or_supplier_http_failure_is_an_actionable_design_refusal(self):
        for boundary in ("stock", "supplier"):
            with self.subTest(boundary=boundary):
                self.stock_status = 503 if boundary == "stock" else 200
                self.supplier_status = 503 if boundary == "supplier" else 200
                with self.assertRaisesRegex(DesignError, "capture|captures"):
                    self.capture(boundary)
                self.assertTrue((self.root / boundary / "cad/Owned.kicad_sym").exists())
                self.assertFalse((self.root / boundary / "capture.json").exists())

    def test_malformed_supplier_body_stays_retained_and_cannot_be_compiled(self):
        self.supplier_raw = b'{"result":null}'
        with self.assertRaisesRegex(DesignError, "captures"):
            self.capture()
        self.assertEqual(
            (self.root / "captured/supplier/C1.full.json").read_bytes(),
            self.supplier_raw,
        )
        with self.assertRaisesRegex(DesignError, "capture.json"):
            compile_design(self.design, self.root / "captured", self.root / "bundle")
