"""Original synthetic captures exercise the public design compiler offline."""

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_ee.design import DesignError
from agent_ee.design_compile import compile_design
from agent_ee.storage import digest, identity, write_json
from tests.test_jlc_stock_capture import row, search
from tests.test_native_binding import GEN
from tests.land_pattern_fixtures import (
    chip,
    chip_kicad,
    component,
    kicad_footprint,
    provenance,
)


def design_fixture(root):
    d = dict(
        schema="agent-ee.design/1",
        name="owned",
        requirement_prefix="TEST",
        intent="Original synthetic divider design; no real component approval.",
        board=dict(
            width_mm=30,
            height_mm=20,
            thickness_mm=1.2,
            copper_layers=2,
            max_component_height_mm=8,
        ),
        parts={
            r: dict(
                lcsc="C1",
                value="10k",
                lib_id="Owned:R",
                footprint="Owned:R2",
                group="divider",
            )
            for r in ("R1", "R2")
        },
        nets=dict(SIGNAL=["R1.1", "R2.1"], GND=["R1.2", "R2.2"]),
        ground="GND",
        power_flags=["GND"],
        no_connects=[],
        connectors=[],
        decoupling=[],
        groups=[dict(name="divider", anchor="R1", title="Owned divider")],
        plane=dict(net="GND", layer="B.Cu"),
        functional=[
            dict(
                text="R1 shall join SIGNAL to GND with 10 k resistance.",
                witnesses=[
                    dict(
                        id="resistance",
                        type="element_between",
                        **{"class": "resistor"},
                        lib_id="Owned:R",
                        nets=["SIGNAL", "GND"],
                        value="10k",
                    ),
                    dict(
                        id="signal-pin", type="pin_net", terminal="R1.1", net="SIGNAL"
                    ),
                ],
            )
        ],
        bench=["Measure resistance on assembled hardware."],
    )

    def put(rel, raw):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(raw, bytes):
            p.write_bytes(raw)
        else:
            write_json(p, raw)
        return digest(p)

    put("cad/Owned.kicad_sym", GEN.library())
    power = (
        '(kicad_symbol_lib (version 20241209) (generator "owned") '
        + GEN._definition("GND", [(1, [("1", 0, "power_in", "GND")])], power=True)
        + GEN._definition("PWR_FLAG", [(1, [("1", 0, "power_out", "pwr")])], power=True)
        + ")"
    )
    put("cad/power.kicad_sym", power.encode())
    put(
        "cad/Owned.pretty/R2.kicad_mod",
        kicad_footprint(
            "R2",
            chip_kicad(1, 1, 1),
            extra='(fp_rect (start -2 -1) (end 2 1) (stroke (width 0.05) (type default)) (layer "F.CrtYd"))',
        ),
    )
    raw = search(
        row(
            "C1",
            500,
            attributes=[
                dict(attribute_name_en="Resistance", attribute_value_name="10kΩ")
            ],
        )
    )
    sha = put("stock/responses/C1.json", raw)
    from agent_ee.jlc_stock_capture import (
        DEFAULT_ENDPOINT,
        PARSER,
        parse_search_response,
        request_body,
    )

    _, observed = parse_search_response(raw, "C1")
    stamp = "2026-11-01T00:00:00+00:00"
    put(
        "stock/catalog.json",
        dict(
            schema="agent-ee.jlc-catalog/1",
            as_of=stamp,
            completed_at=stamp,
            stale_before=None,
            source=dict(
                endpoint=DEFAULT_ENDPOINT,
                method="POST",
                request_body=request_body("{lcsc}").decode(),
                parser=PARSER,
                min_interval_seconds=5.0,
            ),
            requests=[
                dict(
                    lcsc="C1",
                    fetched_at=stamp,
                    status="found",
                    http_status=200,
                    response=dict(
                        path="responses/C1.json", sha256=sha, size_bytes=len(raw)
                    ),
                    reason=None,
                )
            ],
            parts={"C1": dict(observed, last_seen=stamp)},
        ),
    )
    url = "https://example.invalid/C1.pdf"
    pdf = hashlib.sha256(url.encode()).hexdigest() + ".pdf"
    sha = put("datasheets/" + pdf, b"%PDF-1.4\nOriginal synthetic envelope.\n%%EOF\n")
    put(
        "datasheets/manifest.json",
        dict(
            schema="agent-ee.datasheet-capture/1",
            bindings_sha256=identity([dict(id="C1", datasheet_url=url)]),
            outcome="captured",
            parts=[
                dict(
                    id="C1",
                    url=url,
                    final_url=url,
                    fetched_at=stamp,
                    sha256=sha,
                    path=pdf,
                    missing=False,
                    reason=None,
                )
            ],
        ),
    )
    raw = component("C1", chip(4, 2, 2))
    put("supplier/C1.raweasy.json", raw)
    put(
        "supplier/C1.provenance.json", provenance("C1", "supplier/C1.raweasy.json", raw)
    )
    from agent_ee.models3d import STOCK_REPOSITORY

    put(
        "lib3d/.agent-ee-models.json",
        dict(schema="agent-ee.stock-models/1", repository=STOCK_REPOSITORY, files={}),
    )
    return d


class DesignCompileTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.captures = self.root / "captures"
        self.captures.mkdir()
        self.design = design_fixture(self.captures)

    def compile(self, name="bundle"):
        return compile_design(self.design, self.captures, self.root / name)

    def test_complete_bundle_is_deterministic_relocatable_and_does_not_contact_network(
        self,
    ):
        with patch(
            "socket.socket", side_effect=AssertionError("compile contacted the network")
        ):
            self.compile("one")
            self.compile("two")

        def files(name):
            root = self.root / name
            return {
                str(p.relative_to(root)): p.read_bytes()
                for p in root.rglob("*")
                if p.is_file()
            }

        self.assertEqual(files("one"), files("two"))
        from agent_ee.build_plan import validate_plan

        plan = json.loads((self.root / "one/build-plan.json").read_text())
        validate_plan(plan)
        for rel in [
            *plan["requirements"].values(),
            *plan["parts"].values(),
            plan["envelope"],
            plan["placement"]["intent"],
        ]:
            self.assertTrue((self.root / "one/inputs" / rel).is_file(), rel)
        import shutil

        inputs = self.root / "one/inputs"
        (inputs / "requirements-map").mkdir()
        shutil.copyfile(inputs / "spec.md", inputs / "requirements-map/spec.md")
        from agent_ee.parts_manifest import qualify_manifest

        qualify_manifest(
            json.loads((self.root / "one/inputs/parts.json").read_text()),
            self.root / "one/inputs",
        )

    def test_generated_requirement_map_and_witness_rules_are_accepted_by_existing_compilers(
        self,
    ):
        self.compile()
        inputs = self.root / "bundle/inputs"
        from agent_ee.requirement_map import compile_map, input_inventory
        from agent_ee.scaffold import native_files
        from agent_ee.board_envelope import validate as validate_envelope

        envelope = validate_envelope((inputs / "envelope.json").read_bytes())
        outputs = native_files(
            envelope,
            "project/owned.kicad_sch",
            json.loads((inputs / "project/owned.kicad_pro").read_text()),
            "00000000-0000-4000-8000-000000000001",
            "owned-test",
        )
        for rel, raw in outputs.items():
            (inputs / rel).write_bytes(raw)
        import shutil

        (inputs / "requirements-map").mkdir()
        shutil.copyfile(inputs / "spec.md", inputs / "requirements-map/spec.md")
        from agent_ee.parts_stage import compile_parts

        _, bound = compile_parts(inputs, "parts.json", "circuit.json", "layout.json")
        (inputs / "build-generated").mkdir()
        write_json(inputs / "build-generated/connectivity-intent.json", bound)
        files = {
            p: h
            for p, h in input_inventory(inputs).items()
            if not p.startswith("requirements-map/")
        }
        compile_map(
            (inputs / "requirement-map.json").read_bytes(),
            (inputs / "spec-record.json").read_bytes(),
            (inputs / "spec.md").read_bytes(),
            files=files,
            inputs=inputs,
            obligations_raw=(inputs / "obligations.json").read_bytes(),
        )
        m = json.loads((inputs / "requirement-map.json").read_text())
        rows = m["requirements"]
        functional = next(r for r in rows if r["id"] == "TEST-FUNC-1")
        self.assertEqual({c["kind"] for c in functional["checks"]}, {"topology_rules"})
        self.assertEqual(rows[-1]["checks"][-1]["kind"], "fab_package")
        parity = [
            r
            for r in rows
            if any(c["kind"] == "netlist_parity" for c in r.get("checks", []))
        ]
        self.assertEqual(len(parity), 1)
        rules = json.loads((inputs / "topology-rules.json").read_text())["rules"]
        self.assertEqual(
            next(r for r in rules if r["id"] == "signal-pin")["net"], "/SIGNAL"
        )
        self.assertEqual({r["requirement"] for r in rules}, {"TEST-FUNC-1"})

    def test_refusals_report_independent_fields_and_publish_no_output(self):
        self.design["board"]["copper_layers"] = 4
        self.design["nets"]["SIGNAL"] = ["R1.99"]
        self.design["nets"]["GND"].append("U9.1")
        self.design["functional"][0]["witnesses"] = []
        self.design["connectors"] = [
            dict(
                reference="R1",
                edge="middle",
                position_mm=0,
                inset_mm=0,
                rotation_deg=0,
                insertion="from_top",
            )
        ]
        with self.assertRaises(DesignError) as caught:
            self.compile()
        message = str(caught.exception)
        for field in (
            "board.copper_layers",
            "nets.SIGNAL",
            "nets.GND",
            "connectors[0].edge",
            "functional[0].witnesses",
        ):
            self.assertIn(field, message)
        self.assertFalse((self.root / "bundle").exists())

    def test_missing_capture_and_symbol_footprint_mismatch_name_the_part(self):
        for kind in ("stock", "symbol", "footprint"):
            with self.subTest(kind=kind):
                d = copy.deepcopy(self.design)
                if kind == "stock":
                    d["parts"]["R1"]["lcsc"] = "C999"
                elif kind == "symbol":
                    d["parts"]["R1"]["lib_id"] = "Owned:Absent"
                else:
                    d["parts"]["R1"]["footprint"] = "Owned:Absent"
                with self.assertRaises(DesignError) as caught:
                    compile_design(d, self.captures, self.root / kind)
                self.assertIn("parts.R1", str(caught.exception))
                self.assertFalse((self.root / kind).exists())

    def test_witnesses_stay_independent_when_circuit_nets_are_swapped(self):
        self.design["nets"]["SIGNAL"][0] = "R1.2"
        self.design["nets"]["GND"][0] = "R1.1"
        self.compile()
        root = self.root / "bundle/inputs"
        rules = json.loads((root / "topology-rules.json").read_text())
        rule = next(r for r in rules["rules"] if r["id"] == "signal-pin")
        self.assertEqual(rule["terminal"], dict(reference="R1", pin="1"))
        self.assertEqual(rule["net"], "/SIGNAL")
        circuit = json.loads((root / "circuit.json").read_text())
        signal = next(n for n in circuit["nets"] if n["name"] == "SIGNAL")
        self.assertIn(dict(component="R1", pin=dict(number="2")), signal["terminals"])

    def test_optional_mechanics_use_local_courtyard_coordinates(self):
        self.design["mechanics"] = dict(access_owners=[])
        self.compile()
        root = self.root / "bundle/inputs"
        model = json.loads((root / "analysis/geometry-model.json").read_text())
        self.assertEqual(model["packages"]["R1"]["size_mm"], [4.0, 2.0, 8])
        self.assertEqual(model["packages"]["R1"]["offset_mm"], [0.0, 0.0])
        from agent_ee.acquisition_mechanics import load

        load(root / "placement-mechanics.json")

    def test_supplier_span_preserves_exact_bytes_with_unicode_and_decoy_strings(self):
        from agent_ee.design_capture import result_span

        raw = ' {"message":"a \\"result\\": decoy é", "result" : { "a" : "雪" }, "success":true} '.encode()
        span, (start, end) = result_span(raw)
        self.assertEqual(span, b'{ "a" : "\xe9\x9b\xaa" }')
        self.assertEqual(span, raw[start:end])

    def test_malformed_nested_shapes_report_fields_without_publishing(self):
        changes = [
            ("board", []),
            ("groups", [None]),
            ("parts", {"R1": []}),
            ("functional", [{"text": "Owned", "witnesses": [None]}]),
            ("mechanics", {"access_owners": [], "packages": []}),
        ]
        for field, value in changes:
            with self.subTest(field=field):
                d = copy.deepcopy(self.design)
                d[field] = value
                with self.assertRaises(DesignError) as caught:
                    compile_design(d, self.captures, self.root / "refused")
                self.assertIn(field, str(caught.exception))
                self.assertFalse((self.root / "refused").exists())

    def test_duplicate_assignments_bad_values_and_off_edge_positions_are_actionable(
        self,
    ):
        d = self.design
        d["nets"]["SIGNAL"].append("R1.2")
        d["functional"][0]["witnesses"][0]["value"] = "ten bananas"
        d["connectors"] = [
            dict(
                reference="R1",
                edge="left",
                position_mm=500,
                inset_mm=2,
                rotation_deg=0,
                insertion="from_top",
            )
        ]
        with self.assertRaises(DesignError) as caught:
            self.compile()
        for field in (
            "nets.GND",
            "functional[0].witnesses[0].value",
            "connectors[0].position_mm",
        ):
            self.assertIn(field, str(caught.exception))

    def test_repeated_decoupling_lands_are_refused_with_a_specific_remedy(self):
        path = self.captures / "cad/Owned.pretty/R2.kicad_mod"
        raw = path.read_text()
        raw = (
            raw[:-2]
            + '(pad "1" smd rect (at 3 0) (size 1 1) (layers "F.Cu" "F.Mask")))\n'
        )
        path.write_text(raw)
        self.design["decoupling"] = [
            dict(
                capacitor="R2",
                device="R1",
                supply=["R1.1", "R2.1"],
                **{"return": ["R1.2", "R2.2"]},
                max_distance_mm=5,
            )
        ]
        with self.assertRaises(DesignError) as caught:
            self.compile()
        self.assertIn("decoupling[0].supply", str(caught.exception))
        self.assertIn("native land UUID", str(caught.exception))

    def test_public_stock_fetch_reuses_pinned_models_and_rejects_unsafe_paths_offline(
        self,
    ):
        from agent_ee.models3d import (
            fetch_stock_references,
            STOCK_REPOSITORY,
            STOCK_REVISIONS,
        )

        relative = "Owned.3dshapes/R.step"
        root = self.root / "models"
        path = root / relative
        path.parent.mkdir(parents=True)
        path.write_bytes(b"ISO-10303-21;\nowned synthetic model envelope\n")
        commit = STOCK_REVISIONS[0]
        write_json(
            root / ".agent-ee-models.json",
            dict(
                schema="agent-ee.stock-models/1",
                repository=STOCK_REPOSITORY,
                files={
                    relative: dict(
                        commit=commit,
                        sha256=digest(path),
                        size_bytes=path.stat().st_size,
                        source_url=f"{STOCK_REPOSITORY}/-/raw/{commit}/{relative}",
                    )
                },
            ),
        )
        with patch("socket.socket", side_effect=AssertionError("network contacted")):
            result = fetch_stock_references([relative], root)
            self.assertEqual(result["missing"], 0)
            self.assertEqual(result["files"][0]["status"], "reused")
            with self.assertRaises(ValueError):
                fetch_stock_references(["../escape.step"], root)

    def test_routing_vias_respect_native_annular_width_without_weakening_fab_minima(
        self,
    ):
        # Real I2C run r1 routed all nets but hit annular_width at the fab's
        # minimum 0.25/0.15 pair. Preserve the gate and derive larger routing.
        self.compile()
        inputs = self.root / "bundle/inputs"
        project = json.loads((inputs / "project/owned.kicad_pro").read_text())
        rules = project["board"]["design_settings"]["rules"]
        routing = project["net_settings"]["classes"][0]
        from agent_ee.native_geometry_defaults import DEFAULTS

        native = DEFAULTS["board.design_settings.rules.min_via_annular_width"][0]
        self.assertEqual(rules["min_via_annular_width"], native)
        self.assertGreaterEqual(
            (routing["via_diameter"] - routing["via_drill"]) / 2, native
        )
        self.assertGreaterEqual(routing["via_diameter"], rules["min_via_diameter"])
        self.assertGreaterEqual(
            routing["via_drill"], rules["min_through_hole_diameter"]
        )
        derivation = json.loads((inputs / "project-rule-derivation.json").read_text())
        self.assertEqual(
            derivation["selected_routing"]["via_diameter"], routing["via_diameter"]
        )
