"""Full capture orchestration over scripted loopback HTTP, without vendor access."""

import io
import json
import os
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from agent_ee import native_run
from agent_ee.design import DesignError
from agent_ee.design_capture import capture_design, validate_capture_manifest
from agent_ee.design_compile import compile_design
from agent_ee.design_generate import refusal_classes
from agent_ee.kicad_install import RECORD_ENV
from tests.test_design_compile import design_fixture
from tests.test_footprint_rules import NPTH_LIMIT, hole_finding, owned_footprint, recorded
from tests.test_kicad_install import record as install_record
from tests.test_jlc_stock_capture import row, search
from tests.land_pattern_fixtures import chip, component, pad
from toolchain import require_toolchain


class CaptureHttpTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        # The footprint rule check's scratch-board and DRC runs answer from canned reports:
        # footprint_findings[ref] lists that part's DRC violations. One test runs it natively.
        self.footprint_findings = {}
        self.enterContext(native_run.using(recorded(self.footprint_findings)))
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
        # (path -> status, body[, False for no Content-Length]) that take precedence over the
        # fixed ones below; a list of routes answers successive requests in turn.
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
                    route = owner.pdf_routes[self.path]
                    if isinstance(route, list):
                        hits = sum(r[1] == self.path for r in owner.requests)
                        route = route[min(hits, len(route)) - 1]
                    self.reply(*route)
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

            def reply(self, status, raw, framed=True):
                self.send_response(status)
                if framed:
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

    def test_source_cut_without_a_declared_length_is_downloaded_again_and_both_downloads_recorded(self):
        # Review of ffea1c80: the connection closed after 20 bytes with no Content-Length; capture
        # called the file truncated for good, and the next capture of the same URL succeeded.
        whole = b"%PDF-1.4\n% owned synthetic manufacturer datasheet\n%%EOF\n"
        self.pdf_routes = {"/maker.pdf": [(200, whole[:20], False), (200, whole, False)]}
        self.design["parts"]["R1"]["datasheet"] = dict(url=self.url + "/maker.pdf")
        captured = self.capture()
        attempts = json.loads((captured / "datasheet-sources.json").read_bytes())["parts"]["C1"]["attempts"]
        self.assertEqual([(a["source"], a["outcome"]) for a in attempts], [("declared", "selected")])
        self.assertEqual([(f["failure"], f["size_bytes"], f["content_length"]) for f in attempts[0]["fetches"]],
                         [("truncated", 20, None), (None, len(whole), None)])
        final = json.loads((captured / "datasheets/manifest.json").read_bytes())["parts"][0]
        self.assertEqual((captured / "datasheets" / final["path"]).read_bytes(), whole)

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
        # The image install record names the installed release (10.0.8) and the mocked GitLab tag
        # lookup returns its packages3D commit; no kicad-cli runs.
        current = {"tag": "10.0.8", "commit": "8975715167d1a76f9b44be155ac02326f704cdbb"}
        record = self.root / "kicad.install.json"
        record.write_text(json.dumps(install_record("10.0.8")))
        tag = io.BytesIO(json.dumps({"name": "10.0.8", "commit": {"id": current["commit"]}}).encode())
        with patch.dict(os.environ, {RECORD_ENV: str(record)}), \
                patch.object(models3d, "urlopen", return_value=tag) as lookup, \
                patch.object(models3d, "_download_model", return_value=None), \
                self.assertRaises(DesignError) as caught:
            self.capture()
        self.assertEqual(lookup.call_args.args[0].full_url, models3d.STOCK_TAG_API + "10.0.8")
        message = str(caught.exception)
        for ref in ("R1", "R2"):
            self.assertIn(f"parts.{ref}.footprint: Owned:R2 uses the 3D model Owned.3dshapes/R2.step", message)
        for revision in (current, models3d.LEGACY_REVISION):
            self.assertIn(revision["commit"], message)
        self.assertIn('[tool_gap]', message)
        report = json.loads((self.root / "captured/models-report.json").read_bytes())
        self.assertEqual([(r["path"], r["status"]) for r in report["files"]],
                         [("Owned.3dshapes/R2.step", "not-found")])
        self.assertFalse((self.root / "captured/capture.json").exists())

    def test_missing_declared_model_names_part_path_and_source_without_claiming_a_stock_revision(self):
        from urllib.error import HTTPError
        from agent_ee import models3d
        footprint = self.kicad / 'footprints/Owned.pretty/R2.kicad_mod'
        footprint.write_bytes(footprint.read_bytes().rstrip()[:-1]
            + b' (model "${KICAD10_3DMODEL_DIR}/Owned.3dshapes/Missing.step"))\n')
        url = 'https://example.invalid/declared-model.step'
        source = {'Owned.3dshapes/Missing.step': dict(source_url=url, sha256='a' * 64)}
        with patch.object(models3d, 'urlopen', side_effect=HTTPError(url, 404, 'missing', {}, None)), \
                self.assertRaises(DesignError) as caught:
            capture_design(self.design, self.root / 'declared-missing', kicad_root=self.kicad,
                stock_endpoint=self.url + '/stock', supplier_endpoint=self.url + '/supplier/{lcsc}', model_sources=source)
        message = str(caught.exception)
        for ref in ('R1', 'R2'):
            self.assertIn('parts.' + ref + '.footprint:', message)
        for value in ('[tool_gap]', url, 'Owned.3dshapes/Missing.step'):
            self.assertIn(value, message)
        self.assertNotIn('does not have at the revisions', message)

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

    def test_deterministic_supplier_refusal_names_every_part_id_mode_and_tool_gap(self):
        self.supplier_raw = b'{"success":true,"result":' + component('C1', [
            pad('ELLIPSE', 4000, 3000, 2, 3, '1')]) + b'}'
        with self.assertRaises(DesignError) as caught:
            self.capture()
        message = str(caught.exception)
        for ref in ('R1', 'R2'):
            self.assertIn(f'parts.{ref}.lcsc: C1: deterministic supplier unsupported_shape [tool_gap]', message)
        self.assertNotIn('retry', message)
        self.assertIn('extend the footprint reader', message)
        self.assertEqual((self.root / 'captured/supplier/C1.full.json').read_bytes(), self.supplier_raw)
        self.assertFalse((self.root / 'captured/capture.json').exists())

    def test_explicit_model_source_is_captured_and_usable_by_compile(self):
        from tests.test_models3d import ModelResponse
        fp = self.kicad / 'footprints/Owned.pretty/R2.kicad_mod'
        raw = fp.read_text()
        fp.write_text(raw.rstrip()[:-1] + '\n(model "${KICAD10_3DMODEL_DIR}/Owned.3dshapes/Connector.step"))\n')
        payload = b'ISO-10303-21;\nHEADER;\nENDSEC;\nEND-ISO-10303-21;\n'
        sources = {'Owned.3dshapes/Connector.step': dict(source_url='https://example.invalid/owned.step',
                                                     sha256=hashlib.sha256(payload).hexdigest())}
        out = self.root / 'explicit-model'
        with patch('agent_ee.models3d.urlopen', return_value=ModelResponse(payload)):
            capture_design(self.design, out, kicad_root=self.kicad, stock_endpoint=self.url + '/stock',
                           supplier_endpoint=self.url + '/supplier/{lcsc}', model_sources=sources)
        self.assertEqual(json.loads((out / 'model-sources.json').read_bytes()), sources)
        validate_capture_manifest(out)
        compile_design(self.design, out, self.root / 'explicit-bundle')
        receipt = json.loads((self.root / 'explicit-bundle/inputs/lib3d/.agent-ee-models.json').read_bytes())
        self.assertEqual(receipt['files']['Owned.3dshapes/Connector.step']['sha256'], sources['Owned.3dshapes/Connector.step']['sha256'])

    def test_a_footprint_breaking_the_board_rules_is_refused_at_capture_before_any_download(self):
        self.footprint_findings['R1'] = [hole_finding('R1')]
        with self.assertRaises(DesignError) as caught:
            self.capture()
        message = str(caught.exception)
        # A design field and no tool gap: the generate loop returns it to the author as a design issue.
        self.assertEqual(refusal_classes(message), ['parts.R1.footprint'])
        self.assertNotIn('[tool_gap]', message)
        (line,) = caught.exception.problems
        for text in ("parts.R1.footprint: Owned:R2 breaks this board's fabrication rules on its own, before "
                     'placement or routing: 1 native DRC error inside the footprint, which final verification '
                     'fails as pre-existing whatever the placement.',
                     'hole_clearance: Pad 1 [/SIGNAL] of R1 on F.Cu and NPTH pad of R1 measure 0.1944 mm '
                     + NPTH_LIMIT,
                     'Choose another footprint for R1 whose holes and pads meet these rules'):
            self.assertIn(text, line)
        self.assertEqual(self.requests, [])  # before the stock, datasheet and supplier downloads
        report = json.loads((self.root / 'captured/footprint-rules/report.json').read_bytes())
        self.assertEqual([(p['reference'], p['status']) for p in report['parts']], [('R1', 'refused'), ('R2', 'pass')])

    def test_footprints_are_checked_under_the_rule_file_that_compile_emits(self):
        captured = self.capture()
        compile_design(self.design, captured, self.root / 'bundle')
        for name in ('owned.kicad_pro', 'owned.kicad_dru'):
            self.assertEqual((captured / 'footprint-rules/inputs' / name).read_bytes(),
                             (self.root / 'bundle/inputs/project' / name).read_bytes())
        self.assertEqual(json.loads((captured / 'footprint-rules/report.json').read_bytes())['status'], 'pass')

    def test_native_capture_refuses_a_footprint_whose_hole_is_too_close_to_its_pad(self):
        require_toolchain(native=True)
        (self.kicad / 'footprints/Owned.pretty/R2.kicad_mod').write_bytes(owned_footprint('R2', 0.15))
        with native_run.using(None), self.assertRaises(DesignError) as caught:
            self.capture()
        message = str(caught.exception)
        self.assertEqual(refusal_classes(message), ['parts.R1.footprint', 'parts.R2.footprint'])
        self.assertIn('measure 0.1500 mm ' + NPTH_LIMIT, message)
        self.assertEqual(self.requests, [])

    def test_native_capture_of_clean_footprints_passes_and_its_files_compile(self):
        # The scratch boards, KiCad's own home and config files and the DRC records stay in the
        # capture; compile's inventory check must accept them.
        require_toolchain(native=True)
        with native_run.using(None):
            captured = self.capture()
        report = json.loads((captured / 'footprint-rules/report.json').read_bytes())
        self.assertEqual([(p['reference'], p['status'], p['errors']) for p in report['parts']],
                         [('R1', 'pass', []), ('R2', 'pass', [])])
        compile_design(self.design, captured, self.root / 'bundle')
