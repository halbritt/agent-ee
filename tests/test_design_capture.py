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
        # Datasheet URL fields of a part's JLC row by LCSC id, and datasheet routes
        # (path -> status, body) that take precedence over the fixed ones below.
        self.row_urls, self.pdf_routes = {}, {}
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = json.loads(
                    self.rfile.read(int(self.headers["Content-Length"]))
                )
                owner.requests.append((self.command, self.path, payload))
                lcsc = payload["keyword"]
                urls = owner.row_urls.get(lcsc, dict(
                    dataManualFileAccessIdUrl=owner.url + "/signed.pdf",
                    dataManualUrl=owner.url + "/html-must-not-be-used",
                    dataManualOfficialLink=owner.url + "/official.pdf",
                ))
                self.reply(
                    owner.stock_status,
                    search(
                        row(
                            lcsc,
                            500,
                            attributes=[
                                dict(
                                    attribute_name_en="Resistance",
                                    attribute_value_name="10kΩ",
                                )
                            ],
                            **urls,
                        )
                    ),
                )

            def do_GET(self):
                owner.requests.append((self.command, self.path, None))
                if self.path in owner.pdf_routes:
                    self.reply(*owner.pdf_routes[self.path])
                elif self.path == "/supplier/C1":
                    self.reply(owner.supplier_status, owner.supplier_raw)
                elif self.path.startswith("/supplier/"):
                    lcsc = self.path.rsplit("/", 1)[1]
                    self.reply(200, b'{"success":true,"result":' + component(lcsc, chip(4, 2, 2)) + b"}")
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
                self.send_header("Content-Length", str(len(raw)))
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

    def test_truncated_jlc_file_falls_back_per_part_and_every_source_is_recorded(self):
        # Benchmark r4 load-switch attempt-001: JLC's file for C20917 stopped before its
        # end-of-file marker on every download, and its row had no manufacturer link. Capture
        # tried one URL and fell back only when every failed part had a manufacturer link,
        # so neither part below was captured.
        import hashlib
        from types import SimpleNamespace
        from unittest.mock import patch
        from agent_ee import jlc_stock_capture
        from agent_ee.datasheet_cascade import validate_cascade

        truncated = b"%PDF-1.4\n% owned synthetic file cut before its end marker\n" + b"0" * 4000
        lcsc_pdf = b"%PDF-1.4\n% owned synthetic LCSC copy\n%%EOF\n"
        maker_pdf = b"%PDF-1.4\n% owned synthetic manufacturer copy\n%%EOF\n"
        self.design["parts"]["R2"]["lcsc"] = "C2"
        self.row_urls = {
            "C1": dict(dataManualFileAccessIdUrl=self.url + "/c1-file.pdf", dataManualOfficialLink="",
                       dataManualUrl=self.url + "/c1-lcsc.pdf"),
            "C2": dict(dataManualFileAccessIdUrl=self.url + "/c2-file.pdf",
                       dataManualOfficialLink=self.url + "/c2-maker.pdf", dataManualUrl=self.url + "/c2-lcsc.pdf"),
        }
        self.pdf_routes = {"/c1-file.pdf": (200, truncated), "/c1-lcsc.pdf": (200, lcsc_pdf),
                           "/c2-file.pdf": (403, b"refused"), "/c2-maker.pdf": (200, maker_pdf)}
        # The loopback stub needs no politeness pause between its two stock requests.
        clock = SimpleNamespace(sleep=lambda seconds: None, monotonic=jlc_stock_capture.time.monotonic)
        with patch.object(jlc_stock_capture, "time", clock):
            captured = self.capture()
        validate_capture_manifest(captured)
        final = json.loads((captured / "datasheets/manifest.json").read_bytes())
        self.assertEqual([(r["id"], r["url"], r["sha256"]) for r in final["parts"]], [
            ("C1", self.url + "/c1-lcsc.pdf", hashlib.sha256(lcsc_pdf).hexdigest()),
            ("C2", self.url + "/c2-maker.pdf", hashlib.sha256(maker_pdf).hexdigest())])
        sources = json.loads((captured / "datasheet-sources.json").read_bytes())["parts"]

        def tried(lcsc):
            return [(a["source"], a["outcome"], a["failure"], a["transient"]) for a in sources[lcsc]["attempts"]]
        self.assertEqual(tried("C1"), [("jlc_file", "failed", "truncated", False),
                                       ("manufacturer", "unavailable", "no_url", False),
                                       ("cascade", "selected", None, None)])
        self.assertEqual(tried("C2"), [("jlc_file", "failed", "http_status", False),
                                       ("manufacturer", "selected", None, None)])
        cut = sources["C1"]["attempts"][0]
        self.assertEqual((cut["size_bytes"], cut["content_length"]), (len(truncated), len(truncated)))
        # The cascade ran for C1 only, and its retained evidence replays offline.
        cascade = json.loads((captured / "datasheet-attempts/cascade-1/cascade.json").read_bytes())
        self.assertEqual(sorted(cascade["parts"]), ["C1"])
        validate_cascade(captured, "datasheet-attempts/cascade-1/cascade.json")
        self.assertNotIn(("GET", "/c2-lcsc.pdf"), [r[:2] for r in self.requests])

    def test_declared_source_is_used_alone_and_a_changed_document_is_refused(self):
        import hashlib
        from agent_ee.design import validate

        maker_pdf = b"%PDF-1.4\n% owned synthetic manufacturer datasheet\n%%EOF\n"
        self.pdf_routes = {"/maker.pdf": (200, maker_pdf)}
        source = dict(url=self.url + "/maker.pdf", sha256=hashlib.sha256(maker_pdf).hexdigest())
        # One declaration covers every part with the same LCSC id (R2 is also C1).
        self.design["parts"]["R1"]["datasheet"] = source
        captured = self.capture()
        final = json.loads((captured / "datasheets/manifest.json").read_bytes())
        self.assertEqual([(r["id"], r["url"], r["sha256"]) for r in final["parts"]],
                         [("C1", source["url"], source["sha256"])])
        self.assertNotIn(("GET", "/signed.pdf"), [r[:2] for r in self.requests])
        report = json.loads((captured / "datasheet-sources.json").read_bytes())["parts"]["C1"]
        self.assertEqual((report["declared"], [(a["source"], a["outcome"]) for a in report["attempts"]]),
                         (source, [("declared", "selected")]))

        # The URL now serves another document: refused without advice to retry, its copy kept.
        self.pdf_routes["/maker.pdf"] = (200, maker_pdf.replace(b"manufacturer", b"revised"))
        with self.assertRaises(DesignError) as caught:
            self.capture("changed")
        message = str(caught.exception)
        self.assertIn("parts.R1.datasheet: no complete datasheet PDF for C1 (R1, R2)", message)
        self.assertIn("not the declared " + source["sha256"], message)
        self.assertNotIn("retry", message)
        self.assertEqual(len(list((self.root / "changed/datasheet-attempts/declared").glob("*.pdf"))), 1)
        self.assertFalse((self.root / "changed/capture.json").exists())

        # A declared source that is not the captured datasheet cannot use that capture.
        self.design["parts"]["R1"]["datasheet"] = dict(url=self.url + "/other.pdf")
        with self.assertRaisesRegex(DesignError, r"parts\.R1\.datasheet: the captured datasheet .* is not this"):
            validate(self.design, captured)

    def test_invalid_or_conflicting_declared_sources_refuse_before_any_download(self):
        import copy

        for declarations, problem in (
                (dict(R1=dict(url="ftp://example.invalid/sheet.pdf")), "parts.R1.datasheet.url: supply"),
                (dict(R1=dict(url=self.url + "/a.pdf", sha256="ABC")), "parts.R1.datasheet.sha256: supply"),
                (dict(R1=dict(url=self.url + "/a.pdf", size=1)), "parts.R1.datasheet.size: unknown field"),
                (dict(R1=dict(url=self.url + "/a.pdf"), R2=dict(url=self.url + "/b.pdf")),
                 "parts.R2.datasheet: parts with lcsc C1 share one datasheet")):
            with self.subTest(problem=problem):
                design = copy.deepcopy(self.design)
                for ref, source in declarations.items():
                    design["parts"][ref]["datasheet"] = source
                with self.assertRaises(DesignError) as caught:
                    capture_design(design, self.root / "refused", kicad_root=self.kicad,
                                   stock_endpoint=self.url + "/stock")
                self.assertIn(problem, str(caught.exception))
                self.assertEqual(self.requests, [])

    def test_refusal_names_the_part_and_each_source_and_says_retry_only_for_temporary_failures(self):
        from agent_ee.design_generate import refusal_classes

        signed = "/file.pdf?signature=owned"
        self.row_urls = {"C1": dict(dataManualFileAccessIdUrl=self.url + signed,
                                    dataManualOfficialLink=self.url + "/maker", dataManualUrl=self.url + "/lcsc.pdf")}
        page = (200, b"<html>owned synthetic product page</html>")
        for name, routes, expected in (
                ("repeating", {signed: (200, b"%PDF-1.4\n% owned synthetic file cut short\n"), "/maker": page,
                               "/lcsc.pdf": (404, b"not found")},
                 ("the file is truncated", "it has no PDF header", "HTTP Error 404",
                  "declare a complete copy of its datasheet in parts.R1.datasheet")),
                ("temporary", {signed: (503, b"busy"), "/maker": page, "/lcsc.pdf": (503, b"busy")},
                 ("HTTP Error 503", "[can be temporary]", "retry design capture into a new directory"))):
            with self.subTest(name=name):
                self.pdf_routes = routes
                with self.assertRaises(DesignError) as caught:
                    self.capture(name)
                message = str(caught.exception)
                self.assertEqual(refusal_classes(message), ["parts.R1.datasheet"])
                for text in ("no complete datasheet PDF for C1 (R1, R2)", self.url + "/file.pdf?...",
                             self.url + "/maker", self.url + "/lcsc.pdf", *expected):
                    self.assertIn(text, message)
                self.assertNotIn("signature=owned", message)
                self.assertEqual("retry" in message, name == "temporary")
                report = json.loads((self.root / name / "datasheet-sources.json").read_bytes())
                self.assertEqual(report["parts"]["C1"]["outcome"], "unresolved")
                self.assertFalse((self.root / name / "datasheets").exists())

    def test_stock_or_supplier_http_failure_is_an_actionable_design_refusal(self):
        for boundary in ("stock", "supplier"):
            with self.subTest(boundary=boundary):
                self.stock_status = 503 if boundary == "stock" else 200
                self.supplier_status = 503 if boundary == "supplier" else 200
                with self.assertRaisesRegex(DesignError, "capture|captures"):
                    self.capture(boundary)
                self.assertTrue((self.root / boundary / "cad/Owned.kicad_sym").exists())
                self.assertFalse((self.root / boundary / "capture.json").exists())

    def test_missing_stock_model_refusal_names_each_part_footprint_and_model_file(self):
        # Benchmark r4 led-flasher attempt-001: the refusal named neither the part nor the file.
        from unittest.mock import patch
        from agent_ee import models3d

        footprint = self.kicad / "footprints/Owned.pretty/R2.kicad_mod"
        footprint.write_bytes(footprint.read_bytes().rstrip()[:-1]
                              + b' (model "${KICAD10_3DMODEL_DIR}/Owned.3dshapes/R2.step"))\n')
        # Every download is a 404, as for a model missing from the repository at each revision.
        with patch.object(models3d, "_download_model", return_value=None), \
                self.assertRaises(DesignError) as caught:
            self.capture()
        message = str(caught.exception)
        for ref in ("R1", "R2"):
            self.assertIn(f"parts.{ref}.footprint: Owned:R2 uses the 3D model Owned.3dshapes/R2.step", message)
        for revision in models3d.STOCK_REVISIONS:
            self.assertIn(revision, message)
        report = json.loads((self.root / "captured/models-report.json").read_bytes())
        self.assertEqual([(r["path"], r["status"]) for r in report["files"]],
                         [("Owned.3dshapes/R2.step", "not-found")])
        self.assertFalse((self.root / "captured/capture.json").exists())

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
