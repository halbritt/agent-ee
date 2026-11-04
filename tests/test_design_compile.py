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


def seal_capture(root):
    write_json(
        root / "capture.json",
        dict(
            schema="agent-ee.design-capture/1",
            files={
                str(p.relative_to(root)): digest(p)
                for p in sorted(root.rglob("*"))
                if p.is_file() and p.name != "capture.json"
            },
        ),
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
    from agent_ee.design_capture import result_span

    full = b'{"success":true,"result":' + raw + b"}"
    span, (start, end) = result_span(full)
    put("supplier/C1.full.json", full)
    put(
        "supplier/C1.capture.json",
        dict(
            path="supplier/C1.full.json",
            sha256=hashlib.sha256(full).hexdigest(),
            byte_start=start,
            byte_end=end,
        ),
    )
    seal_capture(root)
    return d


class CompilePackagePolarityTests(unittest.TestCase):
    """The same captured pin facts must select the same gate at both boundaries."""

    def test_non_diode_references_agree_on_selection_and_polarity_failures(self):
        from agent_ee.design_compile import cpl_policy
        from agent_ee.cpl_rotation import evaluate, load_policy
        from tests.test_cpl_rotation import diode_supplier, supplier, probe, footprint, board, DIODE_PADS
        cases = (
            # Unnamed board pins stay ungraded, even with a polarized family name.
            ("C1", "C_Polarized", "", "", "+", "-", None),
            ("C1", "C_Polarized", "", "", "1", "2", None),
            ("F1", "Fuse_Polarized", "", "", "+", "-", None),
            ("BT1", "Battery_Cell", "+", "-", "1", "2", "polarity_unknown"),
            ("BT1", "Battery_Cell", "+", "-", "-", "-", "polarity_conflict"),
            ("BT1", "Battery_Cell", "+", "-", "+", "-", None),
            ("BT1", "Battery_Cell", "+", "-", "-", "+", None),
            ("C1", "NamedCap", "+", "-", "1", "2", "polarity_unknown"),
            ("F1", "NamedFuse", "+", "-", "1", "2", "polarity_unknown"),
            ("Q1", "Q_Photo_NPN", "C", "E", "1", "2", None),
        )
        for ref, symbol, one, two, supplied_one, supplied_two, failure in cases:
            with self.subTest(ref=ref, symbol=symbol, pins=(supplied_one, supplied_two)), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                for directory in ("supplier", "named", "Owned.pretty"):
                    (root / directory).mkdir()
                pins = [("1", 0, "passive", one), ("2", 0, "passive", two)]
                definition = GEN._definition(symbol, [(1, pins)])
                definition = definition.replace('(name "P1"', '(name ' + json.dumps(one) ).replace(
                    '(name "P2"', '(name ' + json.dumps(two))
                (root / "Device.kicad_sym").write_text(
                    '(kicad_symbol_lib (version 20241209) (generator "owned") ' + definition + ')')
                pads = DIODE_PADS.format(k=one, a=two)
                fp = root / "Owned.pretty/Chip.kicad_mod"
                fp.write_bytes(kicad_footprint("Chip", pads))
                write_json(root / "cad.json", dict(schema="agent-ee.footprint-libraries/1", complete=True,
                    libraries=[dict(name="Owned", members=[dict(path="Owned.pretty/Chip.kicad_mod", sha256=digest(fp))])]))
                raw = diode_supplier("C2", supplied_one, pin2_name=supplied_two)
                (root / "supplier/C2.raweasy.json").write_bytes(raw)
                (root / "supplier/C2.provenance.json").write_bytes(provenance("C2", "supplier/C2.raweasy.json", raw))
                write_json(root / f"named/{ref}.json", dict(package="0603"))
                design = dict(parts={ref: dict(footprint="Owned:Chip", lcsc="C2", lib_id="Device:" + symbol)})
                if failure:
                    with self.assertRaisesRegex(DesignError, rf"parts\.{ref}\.lcsc: {failure}"):
                        cpl_policy(design, root)
                    policy = dict(schema_version=1, policy_id="owned", provenance="owned fixture",
                                  footprint_rules=[], component_overrides=[])
                else:
                    cpl_policy(design, root)
                    policy = json.loads((root / "cpl/jlc-cpl-policy.json").read_bytes())
                rows = [[ref, "70.000000", "-40.000000", "Top", "0.000000"]]
                geometry = [probe(ref, "Owned:Chip", 70000000, 40000000,
                    [[67950000, 39400000, 68850000, 40600000], [71150000, 39400000, 72050000, 40600000]])]
                records, _, _ = evaluate(rows, geometry, board(footprint("Owned:Chip", ref, 70, 40, 0, pads)),
                    {ref: "C2"}, load_policy(json.dumps(policy).encode()), {"C2": supplier(raw)}, 200)
                record = records[0]
                self.assertEqual([m["mode"] for m in record["modes"]], [failure] if failure else [])
                if not failure:
                    self.assertEqual(record["outcome"], "pass")
                    diagnostic = json.loads((root / "design-compile-report.json").read_bytes())["land_patterns"][0]
                    if not one or one == "C":
                        self.assertEqual(record["polarity"]["assessment"], "ungraded")
                        self.assertEqual(diagnostic["polarity"]["assessment"], "ungraded")
                    else:
                        self.assertEqual(record["polarity"]["board"], "anode")
                        self.assertEqual(record["polarity"]["supplier"], "anode")
                        self.assertEqual(diagnostic["polarity"]["supplier"], "anode")


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
        scope, _ = compile_map(
            (inputs / "requirement-map.json").read_bytes(),
            (inputs / "spec-record.json").read_bytes(),
            (inputs / "spec.md").read_bytes(),
            files=files,
            inputs=inputs,
            obligations_raw=(inputs / "obligations.json").read_bytes(),
        )
        from agent_ee.qualification import manufacturing_coverage
        plan = json.loads((self.root / 'bundle/build-plan.json').read_text())
        manufacturing_coverage(plan, scope, [])
        plan['placement']['local_decoupling'] = 'local-decoupling.json'
        with self.assertRaisesRegex(ValueError, 'loop check must bind'):
            manufacturing_coverage(plan, scope, [])
        scope['checks'].insert(0, dict(kind='decoupling_loops', params=dict(contract='local-decoupling.json')))
        manufacturing_coverage(plan, scope, [])
        scope['checks'].pop(0)
        m = json.loads((inputs / "requirement-map.json").read_text())
        rows = m["requirements"]
        functional = next(r for r in rows if r["id"] == "TEST-FUNC-1")
        self.assertEqual({c["kind"] for c in functional["checks"]}, {"topology_rules"})
        delivery = rows[-1]["checks"]
        self.assertEqual([c["kind"] for c in delivery[-2:]], ["fab_package", "fab_roundtrip"])
        self.assertEqual(delivery[-1]["params"], {"package_check": delivery[-2]["id"]})
        from agent_ee.qualification import TYPED_MANDATORY
        self.assertFalse(TYPED_MANDATORY - {c["kind"] for row in rows for c in row.get("checks", [])})
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
        from agent_ee.acquisition.mechanics import load

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
        seal_capture(self.captures)
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
        )

        relative = "Owned.3dshapes/R.step"
        root = self.root / "models"
        path = root / relative
        path.parent.mkdir(parents=True)
        path.write_bytes(b"ISO-10303-21;\nowned synthetic model envelope\n")
        commit = "8975715167d1a76f9b44be155ac02326f704cdbb"  # kicad-packages3D 10.0.8^{}
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

    def test_hole_clearances_come_from_the_published_limits_not_the_kicad_default(self):
        # Tool defect #32 review: KiCad's 0.25 mm min_hole_clearance default had no source.
        # JLCPCB publishes NPTH to track 0.2 mm and PTH to track 0.28 mm.
        self.compile()
        inputs = self.root / "bundle/inputs"
        project = json.loads((inputs / "project/owned.kicad_pro").read_text())
        self.assertEqual(project["board"]["design_settings"]["rules"]["min_hole_clearance"], 0.2)
        derivation = json.loads((inputs / "project-rule-derivation.json").read_text())
        hole = next(item for item in derivation["enforced"]
                    if item["setting"] == "board.design_settings.rules.min_hole_clearance")
        self.assertEqual((hole["limit_id"], hole["key"], hole["value"]),
                         ("npth-copper", "min_npth_hole_clearance_mm", 0.2))
        (plated,) = derivation["custom_rules"]
        self.assertEqual((plated["rule"], plated["limit_id"], plated["value"]), ("fab_pth-copper", "pth-copper", 0.28))
        self.assertEqual(
            (inputs / "project/owned.kicad_dru").read_text(),
            '(version 1)\n(rule "fab_pth-copper"\n'
            "  (condition \"A.Pad_Type == 'Through-hole' && B.Type != 'Via' && "
            "(B.Type != 'Pad' || B.Pad_Type != 'NPTH, mechanical')\")\n"
            "  (constraint hole_clearance (min 0.28mm)))\n")
        profile = json.loads((inputs / "fab-profile.json").read_text())
        facts = (inputs / "fab-sources/selected-facts.txt").read_text(encoding="utf-8")
        quotes = {limit["id"]: limit["source"]["quote"] for limit in profile["limits"]}
        self.assertEqual((quotes["npth-copper"], quotes["pth-copper"]),
                         ("NPTH hole / track: 0.2 mm", "PTH hole / track: minimum 0.28 mm"))
        self.assertIn("PCB capabilities: fetched 2026-11-04T05:08:38Z; SHA-256 "
                      "ee6b34a71cb17a0adebd0be27491a52585570ff7a3c81a5e9d23dd28cf929c1e", facts)

    def test_supplier_response_span_is_replayed_and_remains_bound_after_relocation(
        self,
    ):
        from agent_ee.design_capture import result_span

        raw = (self.captures / "supplier/C1.raweasy.json").read_bytes()
        full = b'{"success":true,"result":' + raw + b"}"
        span, (start, end) = result_span(full)
        (self.captures / "supplier/C1.full.json").write_bytes(full)
        write_json(
            self.captures / "supplier/C1.capture.json",
            dict(
                path="supplier/C1.full.json",
                sha256=hashlib.sha256(full).hexdigest(),
                byte_start=start,
                byte_end=end,
            ),
        )
        self.compile()
        root = self.root / "bundle/inputs"
        receipt = json.loads((root / "cpl/supplier/C1.capture.json").read_text())
        self.assertTrue((root / receipt["path"]).is_file())
        self.assertEqual(
            (root / receipt["path"]).read_bytes()[
                receipt["byte_start"] : receipt["byte_end"]
            ],
            span,
        )
        paths = ["supplier/C1.full.json", "supplier/C1.raweasy.json",
                 "supplier/C1.capture.json", "supplier/C1.provenance.json"]
        saved = {p: (self.captures / p).read_bytes() for p in paths}
        for change, message in (("span", "span"), ("offset", "span"),
                                ("response_hash", "full response"), ("provenance_hash", "provenance")):
            with self.subTest(change=change):
                for p, data in saved.items():
                    (self.captures / p).write_bytes(data)
                receipt_path = self.captures / "supplier/C1.capture.json"
                receipt = json.loads(receipt_path.read_bytes())
                provenance_path = self.captures / "supplier/C1.provenance.json"
                provenance = json.loads(provenance_path.read_bytes())
                if change == "span":
                    changed = raw + b" "
                    (self.captures / "supplier/C1.raweasy.json").write_bytes(changed)
                    provenance.update(sha256=hashlib.sha256(changed).hexdigest(), size_bytes=len(changed))
                    write_json(provenance_path, provenance)
                elif change == "offset":
                    receipt["byte_start"] += 1
                    write_json(receipt_path, receipt)
                elif change == "response_hash":
                    (self.captures / "supplier/C1.full.json").write_bytes(full + b" ")
                else:
                    provenance["sha256"] = "0" * 64
                    write_json(provenance_path, provenance)
                seal_capture(self.captures)
                from agent_ee.design_capture import validate_supplier_capture
                with self.assertRaisesRegex(ValueError, message):
                    validate_supplier_capture(self.captures, "C1")
                with self.assertRaisesRegex(DesignError, message):
                    self.compile("refused")
                self.assertFalse((self.root / "refused").exists())

    def test_linked_capture_and_invalid_requirement_prefix_are_refused_before_output(
        self,
    ):
        path = self.captures / "cad/Owned.kicad_sym"
        raw = path.read_bytes()
        target = self.root / "external-symbol"
        target.write_bytes(raw)
        path.unlink()
        path.symlink_to(target)
        with self.assertRaises(DesignError) as caught:
            self.compile()
        self.assertIn("captures:", str(caught.exception))
        path.unlink()
        path.write_bytes(raw)
        self.design["requirement_prefix"] = "lower_case"
        with self.assertRaises(DesignError) as caught:
            self.compile()
        self.assertIn("requirement_prefix", str(caught.exception))
        self.assertFalse((self.root / "bundle").exists())

    def test_decoupling_distance_is_bound_to_an_enforceable_numeric_spec_citation(self):
        # Timer run r1 had correct geometry but only an advisory generic citation.
        self.design["decoupling"] = [
            dict(
                capacitor="R2",
                device="R1",
                supply=["R1.1", "R2.1"],
                **{"return": ["R1.2", "R2.2"]},
                max_distance_mm=6,
            )
        ]
        self.compile()
        root = self.root / "bundle/inputs"
        locality = json.loads((root / "electrical-locality.json").read_text())
        from agent_ee.locality_provenance import classify

        for leg in ("supply", "return"):
            pair = locality["obligations"][0][leg]
            self.assertEqual(pair["max_distance_nm"], 6000000)
            self.assertEqual(
                classify(pair, locality["source_records"])["enforcement"], "hard"
            )
            self.assertIn("6 mm", pair["provenance"]["citation"]["quote"])

    def test_local_copper_and_optional_loop_limit_reach_the_plan_and_requirement_map(self):
        self.design['decoupling'] = [dict(capacitor='R2', device='R1',
            supply=['R1.1', 'R2.1'], **{'return': ['R1.2', 'R2.2']}, max_distance_mm=6, max_loop_mm=9)]
        self.compile()
        root = self.root / 'bundle/inputs'
        plan = json.loads((self.root / 'bundle/build-plan.json').read_text())
        self.assertEqual(plan['placement']['local_decoupling'], 'local-decoupling.json')
        doc = json.loads((root / 'local-decoupling.json').read_text())
        self.assertFalse(doc['tie_other_pads'])
        self.assertEqual(doc['relations'][0]['supply'][0], dict(reference='R1', pad='1', net='/SIGNAL'))
        self.assertEqual(doc['relations'][0]['max_loop_mm'], 9)
        self.assertEqual(doc['relations'][0]['basis'], 'Agent-selected measurement requirement; not an owner or datasheet ruling')
        requirement = next(r for r in json.loads((root / 'requirement-map.json').read_text())['requirements']
                           if r['id'] == 'TEST-MECH-3')
        self.assertIn('decoupling_loops', [c['kind'] for c in requirement['checks']])
        self.assertIn('9 mm', (root / 'spec.md').read_text())

    def test_compiled_delivery_includes_the_required_sealed_fabrication_roundtrip(self):
        self.compile()
        root = self.root / 'bundle/inputs'
        document = json.loads((root / 'requirement-map.json').read_text())
        delivery = next(r for r in document['requirements'] if r['id'] == 'TEST-DELIV-1')
        checks = delivery['checks']
        package = next(c for c in checks if c['kind'] == 'fab_package')
        roundtrip = next(c for c in checks if c['kind'] == 'fab_roundtrip')
        self.assertEqual(roundtrip['params'], dict(package_check=package['id']))
        self.assertLess(checks.index(package), checks.index(roundtrip))

    def test_local_copper_does_not_add_a_routing_power_policy(self):
        self.design['decoupling'] = [dict(capacitor='R2', device='R1',
            supply=['R1.1','R2.1'], **{'return':['R1.2','R2.2']}, max_distance_mm=6)]
        self.compile()
        root = self.root / 'bundle/inputs'
        project = json.loads((root / 'project/owned.kicad_pro').read_text())
        self.assertEqual([c['name'] for c in project['net_settings']['classes']], ['Default'])
        self.assertNotIn('netclass_patterns', project['net_settings'])
        self.assertFalse((root / 'power-routing-default.json').exists())

    def test_compile_refuses_intrinsic_local_pad_failures_with_relation_and_pad(self):
        self.design['decoupling'] = [dict(capacitor='R2', device='R1',
            supply=['R1.1', 'R2.1'], **{'return': ['R1.2', 'R2.2']}, max_distance_mm=6)]
        path = self.captures / 'cad/Owned.pretty/R2.kicad_mod'
        path.write_text(path.read_text().replace('smd rect', 'thru_hole rect').replace(
            '(layers "F.Cu" "F.Mask")', '(drill 0.3) (layers "B.Cu" "B.Mask")'))
        seal_capture(self.captures)
        with self.assertRaisesRegex(DesignError, r'decoupling-1: R1.1.*plated hole must carry both'):
            self.compile()
        self.assertFalse((self.root / 'bundle').exists())

    def test_shared_plane_returns_compile_to_one_automatic_bank(self):
        self.design['parts']['R3'] = copy.deepcopy(self.design['parts']['R2'])
        self.design['nets']['SIGNAL'].append('R3.1')
        self.design['nets']['GND'].append('R3.2')
        self.design['decoupling'] = [dict(capacitor=cap, device='R1',
            supply=['R1.1', cap + '.1'], **{'return': ['R1.2', cap + '.2']}, max_distance_mm=6)
            for cap in ('R2', 'R3')]
        self.compile()
        doc = json.loads((self.root / 'bundle/inputs/local-decoupling.json').read_text())
        self.assertEqual(doc['banks'], [dict(id='bank-1', relations=['decoupling-1', 'decoupling-2'])])
        self.assertEqual(len(doc['relations']), 2)

    def test_compiler_accepts_clean_plated_local_pads(self):
        from agent_ee.design_compile import placement
        self.design['decoupling'] = [dict(capacitor='R2', device='R1',
            supply=['R1.1', 'R2.1'], **{'return': ['R1.2', 'R2.2']}, max_distance_mm=6)]
        self.compile()
        root = self.root / 'bundle/inputs'
        # Validate the compiler's local-copper emission independently of supplier land matching.
        path = root / 'Owned.pretty/R2.kicad_mod'
        path.write_text(path.read_text().replace('smd rect', 'thru_hole rect').replace(
            '(layers "F.Cu" "F.Mask")', '(drill 0.3) (layers "*.Cu" "*.Mask")'))
        placement(self.design, root)
        self.assertEqual(len(json.loads((root / 'local-decoupling.json').read_text())['relations']), 1)

    def test_clearance_uses_fab_floor_track_width_uses_native_routing_default(
        self,
    ):
        self.compile()
        root = self.root / "bundle/inputs"
        pro = json.loads((root / "project/owned.kicad_pro").read_bytes())
        default = pro["net_settings"]["classes"][0]
        self.assertEqual(default["clearance"], 0.1)
        self.assertEqual(default["track_width"], 0.2)
        derivation = json.loads((root / "project-rule-derivation.json").read_bytes())
        self.assertEqual(derivation["routing"]["clearance"]["selected"], 0.1)
        self.assertEqual(derivation["routing"]["track_width"]["basis"],
                         "routing default (at least pinned native default)")
        self.assertEqual(derivation["routing"]["clearance"]["basis"],
                         "fabrication clearance floor")
        self.assertEqual(derivation["routing"]["track_width"]["native_default"], 0.2)
        facts = (root / "fab-sources/selected-facts.txt").read_text(encoding="utf-8")
        self.assertIn("https://jlcpcb.com/help/article/jlcpcb-copper-weight", facts)
        self.assertNotIn("https://jlcpcb.com/blog/pcb-copper-weight", facts)
        source = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "fixtures/board-envelope/jlc-2layer/source-capture.json"
            ).read_bytes()
        )
        from agent_ee.design_compile import DATA

        template = json.loads((DATA / "jlc-2layer-fab-profile.json").read_bytes())
        for item in [
            template["stackup"],
            template["finished_thickness_tolerance"],
            *template["limits"],
        ]:
            self.assertEqual(item["source"]["sha256"], "0" * 64)
        for capture in source["raw_captures"]:
            if capture["file"] in ("jlc-capabilities.html", "jlc-copper-weight.html"):
                self.assertIn(capture["sha256"], facts)
                self.assertIn(capture["captured_at"], facts)

    def test_gate_policy_pins_stock_age_connector_tolerance_values_and_native_ground(
        self,
    ):
        self.design["connectors"] = [
            dict(
                reference="R1",
                edge="left",
                position_mm=10,
                inset_mm=2,
                rotation_deg=0,
                insertion="from_top",
            )
        ]
        self.compile()
        root = self.root / "bundle/inputs"
        m = json.loads((root / "requirement-map.json").read_bytes())
        checks = {c["id"]: c for r in m["requirements"] for c in r.get("checks", [])}
        self.assertEqual(checks["assembly"]["params"]["max_age_hours"], 24)
        env = json.loads((root / "envelope.json").read_bytes())
        self.assertEqual(env["fixed_footprints"][0]["tolerance_mm"], 0)
        rules = json.loads((root / "topology-rules.json").read_bytes())["rules"]
        resistance = next(r for r in rules if r["id"] == "resistance")
        self.assertEqual(resistance["value"], "10k")
        self.assertEqual(resistance["nets"], ["/SIGNAL", "GND"])
        plane = json.loads((root / "planes.json").read_bytes())
        self.assertEqual(plane["zones"][0]["net"], "GND")

    def test_pin_only_requirements_compile_and_bench_claims_stay_physical(self):
        self.design["functional"][0]["witnesses"] = [
            self.design["functional"][0]["witnesses"][1]
        ]
        self.design["bench"] = [
            "Measure 1 kHz at 0.1 percent accuracy and survival of 100 V surges."
        ]
        self.compile()
        root = self.root / "bundle/inputs"
        rules = json.loads((root / "topology-rules.json").read_bytes())
        self.assertEqual(rules["classes"], {})
        self.assertEqual(len(rules["rules"]), 1)
        from agent_ee.topology_rules import validate_rules

        classified = dict(
            schema="agent-ee.topology-rules/1",
            classes={},
            rules=[
                dict(
                    id="missing-class",
                    requirement="TEST-FUNC-1",
                    type="element_between",
                    **{"class": "resistor"},
                    nets=["/SIGNAL", "GND"],
                    value="10k",
                )
            ],
        )
        with self.assertRaisesRegex(ValueError, "no declared symbol"):
            validate_rules(classified)
        spec = (root / "spec.md").read_text(encoding="utf-8")
        self.assertIn("not established by these witnesses", spec)
        record = json.loads((root / "spec-record.json").read_bytes())
        func = next(r for r in record["requirements"] if r["id"] == "TEST-FUNC-1")
        span = func["locator"]
        clause = (
            (root / "spec.md")
            .read_bytes()[span["byte_start"] : span["byte_end"]]
            .decode()
        )
        self.assertNotIn(self.design["functional"][0]["text"], clause)
        self.assertTrue(
            any(r["section"] == "Functional purpose" for r in record["prose"])
        )
        obligations = json.loads((root / "obligations.json").read_bytes())
        row = next(r for r in obligations["clauses"] if r["id"] == "TEST-BENCH-1")
        self.assertEqual({s["obligation"] for s in row["spans"]}, {"physical"})
        m = json.loads((root / "requirement-map.json").read_bytes())
        bench = next(r for r in m["requirements"] if r["id"] == "TEST-BENCH-1")
        self.assertIn("bench", bench)
        self.assertNotIn("checks", bench)

    def test_polarized_elements_and_standard_symbol_class_lies_are_refused(self):
        for cls, lib in (
            ("diode", "Device:D"),
            ("led", "Device:LED"),
            ("polarized_capacitor", "Device:C_Polarized"),
            ("capacitor", "Device:R"),
        ):
            with self.subTest(cls=cls):
                d = copy.deepcopy(self.design)
                d["functional"][0]["witnesses"][0].update({"class": cls, "lib_id": lib})
                with self.assertRaisesRegex(
                    DesignError, "undirected|is class resistor"
                ):
                    compile_design(d, self.captures, self.root / "refused")

    def test_manifest_custody_detects_missing_changed_and_unlisted_files(self):
        target = self.captures / "cad/Owned.kicad_sym"
        original = target.read_bytes()
        manifest = self.captures / "capture.json"
        saved = manifest.read_bytes()
        for change in (
            "symbol pin name",
            "deleted manifest",
            "malformed manifest",
            "deleted receipt",
            "unlisted file",
        ):
            with self.subTest(change=change):
                target.write_bytes(original)
                manifest.write_bytes(saved)
                receipt = self.captures / "supplier/C1.capture.json"
                receipt_raw = receipt.read_bytes()
                if change == "symbol pin name":
                    target.write_bytes(original.replace(b'"~"', b'"changed"'))
                    if target.read_bytes() == original:
                        target.write_bytes(original + b" ")
                elif change == "deleted manifest":
                    manifest.unlink()
                elif change == "malformed manifest":
                    manifest.write_bytes(b"[]")
                elif change == "deleted receipt":
                    receipt.unlink()
                else:
                    (self.captures / "extra").write_bytes(b"accidental file")
                with self.assertRaisesRegex(DesignError, "captures.capture.json"):
                    self.compile("refused")
                receipt.write_bytes(receipt_raw)
                (self.captures / "extra").unlink(missing_ok=True)
        target.write_bytes(original)
        manifest.write_bytes(saved)

    def test_even_resealed_supplier_without_response_receipt_is_refused(self):
        (self.captures / "supplier/C1.capture.json").unlink()
        seal_capture(self.captures)
        with self.assertRaisesRegex(
            DesignError, "supplier response capture receipt is missing"
        ):
            self.compile()

    def test_future_stock_refuses_and_stale_stock_warns_with_time_left(self):
        path = self.captures / "stock/catalog.json"
        original = json.loads(path.read_bytes())
        for stamp in ("2030-01-01T00:00:00+00:00", "2020-01-01T00:00:00+00:00"):
            with self.subTest(stamp=stamp):
                catalog = copy.deepcopy(original)
                catalog["as_of"] = catalog["completed_at"] = stamp
                catalog["requests"][0]["fetched_at"] = stamp
                catalog["parts"]["C1"]["last_seen"] = stamp
                write_json(path, catalog)
                seal_capture(self.captures)
                if stamp.startswith("2030"):
                    with self.assertRaisesRegex(DesignError, "future"):
                        self.compile("future")
                else:
                    with self.assertWarnsRegex(UserWarning, "hours left.*24-hour"):
                        self.compile("stale")

    def test_unsupported_dimensions_markdown_intent_and_vacuous_locality_are_actionable(
        self,
    ):
        pair = dict(
            capacitor="R2",
            device="R1",
            supply=["R1.1", "R2.1"],
            **{"return": ["R1.2", "R2.2"]},
            max_distance_mm=5,
        )
        for field, value in (
            ("width_mm", 1000),
            ("width_mm", 10**400),
            ("max_component_height_mm", 100000),
        ):
            with self.subTest(field=field, value=str(value)[:12]):
                d = copy.deepcopy(self.design)
                d["board"][field] = value
                with self.assertRaisesRegex(DesignError, "board." + field):
                    compile_design(d, self.captures, self.root / "refused")
        for text in ("# Intent", "- Intent", "1. Intent", "| Intent"):
            d = copy.deepcopy(self.design)
            d["intent"] = text
            with self.subTest(text=text), self.assertRaisesRegex(DesignError, "intent"):
                compile_design(d, self.captures, self.root / "refused")
        for change in (
            dict(capacitor="R1"),
            {"return": pair["supply"]},
            dict(max_distance_mm=100000),
        ):
            d = copy.deepcopy(self.design)
            d["decoupling"] = [dict(pair, **change)]
            with (
                self.subTest(change=change),
                self.assertRaisesRegex(DesignError, r"decoupling\[0\]"),
            ):
                compile_design(d, self.captures, self.root / "refused")

    def test_decimal_outlines_and_equivalent_quarter_turns_compile(self):
        for width, angle in ((66.04, 360), (8.04, 450), (30, -90)):
            with self.subTest(width=width, angle=angle):
                d = copy.deepcopy(self.design)
                d["board"]["width_mm"] = width
                d["connectors"] = [
                    dict(
                        reference="R1",
                        edge="left",
                        position_mm=10,
                        inset_mm=1,
                        rotation_deg=angle,
                        insertion="from_top",
                    )
                ]
                compile_design(d, self.captures, self.root / str(angle))
                env = json.loads(
                    (self.root / str(angle) / "inputs/envelope.json").read_bytes()
                )
                self.assertEqual(
                    env["fixed_footprints"][0]["rotation_deg"], angle % 360
                )

    def test_connector_courtyard_must_reach_edge_after_rotation(self):
        # Asymmetric courtyard: local +Y extent is 4, -Y extent is 1, +X is 2, -X is 3.
        path = self.captures / "cad/Owned.pretty/R2.kicad_mod"
        path.write_bytes(path.read_bytes().replace(b"(end 2 1)", b"(end 2 4)").replace(b"(start -2 -1)", b"(start -3 -1)"))
        seal_capture(self.captures)
        for edge, angle, inset, accepted in (("right", 90, 5, True), ("right", 270, 5, False),
                                             ("right", 0, 3, True), ("right", 0, 3.1, False),
                                             ("top", 270, 4, True), ("top", 90, 4, False),
                                             ("right", 180, 4, True), ("right", 180, 4.1, False)):
            d = copy.deepcopy(self.design)
            d["connectors"] = [dict(reference="R1", edge=edge, position_mm=10,
                                    inset_mm=inset, rotation_deg=angle, insertion="from_top")]
            with self.subTest(edge=edge, angle=angle, inset=inset):
                out = self.root / f"gap-{edge}-{angle}-{inset}"
                if accepted:
                    compile_design(d, self.captures, out)
                else:
                    with self.assertRaisesRegex(DesignError, r"connectors\[0\].inset_mm"):
                        compile_design(d, self.captures, out)

    def test_connector_courtyard_past_an_edge_corner_is_refused_naming_the_start_corner(self):
        # Benchmark r4 led-flasher attempt-003: a 16.5 mm courtyard at position 10.4 on the bottom
        # edge of a 28 mm board. The bottom edge starts at the bottom-right corner and runs west, so
        # the fixed connector overhung the east edge by 1.45 mm and no placement could succeed.
        path = self.captures / "cad/Owned.pretty/R2.kicad_mod"
        path.write_bytes(path.read_bytes().replace(b"(start -2 -1)", b"(start -4.65 -4.6)")
                         .replace(b"(end 2 1)", b"(end 11.85 4.6)"))
        seal_capture(self.captures)
        self.design["board"].update(width_mm=28, height_mm=24)
        for position, refusal in ((10.4, "runs 1.45 mm past the bottom-right corner"), (11.85, None),
                                  (23.35, None), (23.4, "runs 0.05 mm past the bottom-left corner")):
            d = copy.deepcopy(self.design)
            d["connectors"] = [dict(reference="R1", edge="bottom", position_mm=position, inset_mm=5.3,
                                    rotation_deg=0, insertion="from_top")]
            with self.subTest(position=position):
                out = self.root / f"edge-{position}"
                if refusal is None:
                    compile_design(d, self.captures, out)
                    continue
                with self.assertRaises(DesignError) as caught:
                    compile_design(d, self.captures, out)
                message = str(caught.exception)
                self.assertIn(f"connectors[0].position_mm: R1: rotated courtyard {refusal}", message)
                self.assertIn("The bottom edge starts at the bottom-right corner and runs west", message)
                self.assertIn("use position_mm from 11.85 to 23.35", message)

    def test_edge_start_corners_in_messages_and_doc_match_the_generated_outline(self):
        # The author reads design-compile.md; the outline edges are what the placer uses.
        from agent_ee.design_geometry import EDGE_RUNS
        self.compile()
        edges = json.loads((self.root / "bundle/inputs/envelope.json").read_text())["outline"]["edges"]
        xs = [e["start"][0] for e in edges]
        ys = [e["start"][1] for e in edges]
        compass = {(1, 0): "east", (0, 1): "south", (-1, 0): "west", (0, -1): "north"}
        doc = " ".join((Path(__file__).resolve().parents[1] / "docs/design-compile.md").read_text().split())
        for edge in edges:
            start, unit = edge["start"], tuple((b > a) - (b < a) for a, b in zip(edge["start"], edge["end"]))
            corner = ("top" if start[1] == min(ys) else "bottom") + "-" + ("left" if start[0] == min(xs) else "right")
            with self.subTest(edge=edge["name"]):
                self.assertEqual(EDGE_RUNS[edge["name"]][0], corner)
                self.assertEqual(EDGE_RUNS[edge["name"]][2:], (compass[unit], unit))
                self.assertIn(f"`{edge['name']}` starts at the {corner} corner and runs {compass[unit]}", doc)

    def test_through_edge_requires_local_axis_and_refuses_reversed_pose(self):
        d = copy.deepcopy(self.design)
        d["connectors"] = [dict(reference="R1", edge="right", position_mm=10,
                                inset_mm=1, rotation_deg=90, insertion="through_edge")]
        with self.assertRaisesRegex(DesignError, "R1.*footprint-local"):
            compile_design(d, self.captures, self.root / "missing")
        d["connectors"][0]["local_mating_axis"] = "y+"
        compile_design(d, self.captures, self.root / "facing")
        root = self.root / "facing/inputs"
        from agent_ee.board_envelope import validate
        from agent_ee.placement_predicates import envelope_declarations
        env = validate((root / "envelope.json").read_bytes())
        self.assertEqual(envelope_declarations(env)[0]["local_mating_axis"], "y+")
        d["connectors"][0]["rotation_deg"] = 270
        with self.assertRaisesRegex(DesignError, "R1: mating axis faces x-"):
            compile_design(d, self.captures, self.root / "reversed")

    def test_generated_claims_keep_scope_and_measurement_basis(self):
        self.design["connectors"] = [dict(reference="R1", edge="left", position_mm=10,
                                        inset_mm=1, rotation_deg=0, insertion="from_top")]
        self.design["decoupling"] = [dict(capacitor="R2", device="R1", supply=["R1.1", "R2.1"],
                                          **{"return": ["R1.2", "R2.2"]}, max_distance_mm=5)]
        self.compile()
        root = self.root / "bundle/inputs"
        spec = (root / "spec.md").read_text()
        self.assertIn("Mouth direction and physical mating access are not established by this check.", spec)
        self.assertIn("This checks only declared pin assignments and undirected class/value relationships.", spec)
        self.assertIn("not established by these witnesses", spec)
        self.assertIn("The native component inventory shall match the declared parts membership and multiplicity.", spec)
        self.assertNotIn("size codes shall agree", spec)
        placement = json.loads((root / "placement.json").read_bytes())
        self.assertIn("Agent-selected measurement requirement; not an owner or datasheet ruling; final acceptance required",
                      json.dumps(placement))

    def test_plain_numeric_intent_compiles_markdown_structure_names_field(self):
        from agent_ee.design import validate
        for index, text in enumerate(("1.5 V board", "-5 V input", "#hashtag", "Board\u00a0", "Board\f",
                                      "\u2003Board\u2003", " Board\t")):
            d = copy.deepcopy(self.design)
            d["intent"] = text
            compile_design(d, self.captures, self.root / ("plain-" + str(index)))
        for text in ("* x", "```code", "~~~code", "> quote", "1) item"):
            d = copy.deepcopy(self.design)
            d["intent"] = text
            with self.subTest(text=text), self.assertRaisesRegex(DesignError, "intent:") as caught:
                validate(d)
            self.assertNotRegex(str(caught.exception), r"byte \d+")

    def test_overlap_warning_boundary_is_strictly_below_one_half(self):
        from agent_ee.land_pattern import compare
        for value, warned in (("0.499999", True), ("0.5", False), ("0.500001", False)):
            def measured(*args, **kwargs):
                result = compare(*args, **kwargs)
                for metric in result["copper"].values():
                    metric["presented"]["lower"] = value
                return result
            with self.subTest(value=value), patch("agent_ee.land_pattern.compare", side_effect=measured):
                import warnings
                with warnings.catch_warnings(record=True) as emitted:
                    warnings.simplefilter("always")
                    self.compile("overlap-" + value)
                report = json.loads((self.root / ("overlap-" + value) / "inputs/design-compile-report.json").read_bytes())
                overlap = [w for w in report["warnings"] if "measured copper overlap" in w]
                self.assertEqual(bool(overlap), warned)
                self.assertEqual([str(w.message) for w in emitted if "measured copper overlap" in str(w.message)], overlap)
                if warned:
                    self.assertTrue(all("parts.R" in w and value in w for w in overlap))

    def test_same_net_decoupling_and_diagonal_limit_are_refused(self):
        from agent_ee.design import validate
        d = copy.deepcopy(self.design)
        d["board"].update(width_mm=30, height_mm=40)
        pair = dict(capacitor="R2", device="R1", supply=["R1.1", "R2.1"],
                    **{"return": ["R1.2", "R2.2"]}, max_distance_mm=50)
        d["decoupling"] = [pair]
        with self.assertRaisesRegex(DesignError, "diagonal"):
            validate(d)
        pair["max_distance_mm"] = 5
        d["nets"] = dict(GND=["R1.1", "R2.1", "R1.2", "R2.2"])
        d["functional"] = [dict(text="All terminals grounded.", witnesses=[dict(id="ground", type="pin_net", terminal="R1.1", net="GND")])]
        with self.assertRaisesRegex(DesignError, "different nets"):
            validate(d)

    def put_footprint(self, name, pads, *courtyard):
        """An original two-pad footprint: (number, x, y) land centres and F.CrtYd graphics, each
        given without its closing stroke and layer."""
        lands = "".join(f'(pad "{n}" smd rect (at {x} {y}) (size 0.2 0.2) (layers "F.Cu" "F.Mask"))'
                        for n, x, y in pads)
        (self.captures / f"cad/Owned.pretty/{name}.kicad_mod").write_bytes(kicad_footprint(
            name, lands, extra="".join(graphic + ' (stroke (width 0.05) (type default)) (layer "F.CrtYd"))'
                                       for graphic in courtyard)))

    def test_decoupling_limit_no_courtyard_arrangement_meets_is_refused_with_the_bound(self):
        # Benchmark r4 led-flasher attempt-004: the supply and return pads of a 3.9 x 4.9 mm SOIC-8
        # sit on diagonal corners and the capacitor is an 0603, so with the courtyards apart one leg
        # is at least 5.335 mm in every rotation. Its 5 mm limit sent the placer into a search that
        # could not succeed. These footprints have the same land centres and courtyard boxes.
        from agent_ee.design import validate
        self.put_footprint("DEV", [("1", 2.475, 1.905), ("2", -2.475, -1.905)],
                           "(fp_rect (start -3.7 -2.7) (end 3.7 2.7)")
        self.put_footprint("CAP", [("1", -0.775, 0), ("2", 0.775, 0)],
                           "(fp_rect (start -1.48 -0.73) (end 1.48 0.73)")
        seal_capture(self.captures)
        self.design["parts"]["R1"]["footprint"] = "Owned:DEV"
        self.design["parts"]["R2"]["footprint"] = "Owned:CAP"
        relation = dict(capacitor="R2", device="R1", supply=["R1.1", "R2.1"], **{"return": ["R1.2", "R2.2"]})
        for limit, refused in ((5, True), (5.334, True), (5.335, False), (5.34, False)):
            self.design["decoupling"] = [dict(relation, max_distance_mm=limit)]
            with self.subTest(limit=limit):
                if refused:
                    with self.assertRaisesRegex(DesignError, r"decoupling\[0\]\.max_distance_mm: R1\.1-R2\.1 and "
                                                r"R1\.2-R2\.2 .* at least 5\.335 mm\. Raise max_distance_mm to 5\.34 mm"):
                        validate(self.design, self.captures)
                else:
                    validate(self.design, self.captures)
        # A curved courtyard has no box this check trusts, so the limit is not judged.
        self.put_footprint("CAP", [("1", -0.775, 0), ("2", 0.775, 0)],
                           "(fp_circle (center 0 0) (end 1.5 0)")
        seal_capture(self.captures)
        self.design["decoupling"] = [dict(relation, max_distance_mm=5)]
        validate(self.design, self.captures)

    def test_decoupling_bound_needs_a_movable_part(self):
        # Review of 36cf4842 (agent-1006): two fixed connectors with straight triangular courtyards
        # 0.565685 mm apart have both legs 1.979899 mm, and the placement engine reports that pose
        # feasible. Their courtyard boxes overlap, so the box bound refused 2 mm and asked for 9 mm.
        from agent_ee.design import validate
        triangles = dict(DEV=[(-5, -5), (5, -5), (-5, 5)], CAP=[(-5, 5), (5, -5), (5, 5)])
        lands = dict(DEV=[("1", -1, 0), ("2", 0, -1)], CAP=[("1", 0, 1), ("2", 1, 0)])
        for name, corners in triangles.items():
            self.put_footprint(name, lands[name], *(f"(fp_line (start {a[0]} {a[1]}) (end {b[0]} {b[1]})"
                                                    for a, b in zip(corners, corners[1:] + corners[:1])))
        seal_capture(self.captures)
        self.design["parts"]["R1"]["footprint"] = "Owned:DEV"
        self.design["parts"]["R2"]["footprint"] = "Owned:CAP"
        self.design["decoupling"] = [dict(capacitor="R2", device="R1", supply=["R1.1", "R2.1"],
                                          **{"return": ["R1.2", "R2.2"]}, max_distance_mm=2)]
        connectors = [dict(reference=ref, edge="top", position_mm=10 + offset, inset_mm=5 + offset,
                           rotation_deg=0, insertion="from_top") for ref, offset in (("R1", 0), ("R2", 0.4))]
        self.design["connectors"] = connectors
        validate(self.design, self.captures)
        # With the capacitor movable, the placer keeps the two boxes apart: 2 mm is out of reach.
        self.design["connectors"] = connectors[:1]
        with self.assertRaisesRegex(DesignError, r"decoupling\[0\]\.max_distance_mm: .* at least 9\.000 mm"):
            validate(self.design, self.captures)

    def test_malformed_decoupling_terminal_keeps_its_refusal_when_captures_are_given(self):
        # Review of 36cf4842 (agent-1006): the bound read the missing pin of "R1", and the
        # refusal became "list index out of range".
        self.design["decoupling"] = [dict(capacitor="R2", device="R1", supply=["R1", "R2"],
                                          **{"return": ["R1.2", "R2.2"]}, max_distance_mm=5)]
        with self.assertRaises(DesignError) as caught:
            self.compile()
        self.assertIn("decoupling[0].supply: use REF.PIN", str(caught.exception))
        self.assertNotIn("index out of range", str(caught.exception))

    def test_device_symbol_families_cannot_lie_about_class(self):
        from agent_ee.design import validate, device_class
        for family, kind in (("R", "resistor"), ("C", "capacitor"), ("C_Polarized", "polarized_capacitor"),
                             ("L", "inductor"), ("D", "diode"), ("LED", "led"),
                             ("Fuse", "fuse"), ("Polyfuse", "fuse"), ("FerriteBead", "ferrite")):
            for suffix in ("", "_US", "_Small", "_Schottky"):
                symbol = "Device:" + family + suffix
                self.assertEqual(device_class(symbol), kind)
                d = copy.deepcopy(self.design)
                w = d["functional"][0]["witnesses"][0]
                w.update(lib_id=symbol, **{"class": "capacitor" if kind == "resistor" else "resistor"})
                with self.subTest(symbol=symbol), self.assertRaisesRegex(DesignError, "is class"):
                    validate(d)
        self.assertIsNone(device_class("Device:Rheostat"))

    def test_polarized_fuse_family_requires_directed_pin_witnesses(self):
        from agent_ee.design import validate
        d = copy.deepcopy(self.design)
        d["functional"][0]["witnesses"][0].update(lib_id="Device:Fuse_Polarized_Small", **{"class": "fuse"})
        with self.assertRaisesRegex(DesignError, "is polarized"):
            validate(d)

    def test_fuse_current_value_is_executable(self):
        from agent_ee.design import validate
        from agent_ee.topology_rules import quantity, same_quantity
        d = copy.deepcopy(self.design)
        d["functional"][0]["witnesses"][0].update(value="500mA", **{"class": "fuse"})
        validate(d)
        self.assertTrue(same_quantity(quantity("500mA"), quantity("0.5 A")))

    def test_utf8_locality_provenance_and_missing_model_report(self):
        self.design["intent"] = "Résistance and 雪: synthetic UTF-8 design."
        self.design["decoupling"] = [
            dict(
                capacitor="R2",
                device="R1",
                supply=["R1.1", "R2.1"],
                **{"return": ["R1.2", "R2.2"]},
                max_distance_mm=5,
            )
        ]
        self.compile()
        root = self.root / "bundle/inputs"
        locality = json.loads((root / "electrical-locality.json").read_bytes())
        for leg in ("supply", "return"):
            basis = locality["obligations"][0][leg]["provenance"]["basis"]
            self.assertIn("Agent-selected measurement requirement", basis)
            self.assertIn("not an owner or datasheet ruling", basis)
        report = json.loads((root / "design-compile-report.json").read_bytes())
        self.assertEqual(len([w for w in report["warnings"] if "no 3D model" in w]), 2)
        self.assertEqual(len(report["land_patterns"]), 2)
        self.assertIn("F.Cu", report["land_patterns"][0]["copper"])
        import os
        import subprocess
        import sys

        design_path = self.root / "utf8.json"
        write_json(design_path, self.design)
        env = dict(os.environ, LC_ALL="C", PYTHONUTF8="0", PYTHONCOERCECLOCALE="0")
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "agent_ee.cli",
                "design",
                "compile",
                str(design_path),
                "--captures",
                str(self.captures),
                "--out",
                str(self.root / "locale"),
            ],
            env=env,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (root / "electrical-locality.json").read_bytes(),
            (self.root / "locale/inputs/electrical-locality.json").read_bytes(),
        )

    def test_ferrite_default_names_the_installed_standard_symbol(self):
        from agent_ee.design_compile import topology_rules

        self.design["functional"][0]["witnesses"][0].update({"class": "ferrite"})
        self.design["functional"][0]["witnesses"][0].pop("lib_id")
        target = self.root / "rules"
        target.mkdir()
        topology_rules(self.design, target)
        rules = json.loads((target / "topology-rules.json").read_bytes())
        self.assertIn("Device:FerriteBead", rules["classes"])

    def test_cli_compile_dispatch_and_refusal_exit_codes(self):
        import subprocess
        import sys

        source = self.root / "design.json"
        write_json(source, self.design)
        command = [
            sys.executable,
            "-m",
            "agent_ee.cli",
            "design",
            "compile",
            str(source),
            "--captures",
            str(self.captures),
            "--out",
            str(self.root / "cli"),
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / "cli/build-plan.json").exists())
        self.design["board"]["width_mm"] = 10**400
        write_json(source, self.design)
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("board.width_mm", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "agent_ee.cli",
                "design",
                "capture",
                str(source),
                "--out",
                str(self.root / "cli-capture"),
            ],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("board.width_mm", result.stderr)
        self.assertFalse((self.root / "cli-capture").exists())

    def test_asymmetric_mechanics_courtyard_has_exact_native_body_bounds(self):
        from decimal import Decimal

        path = self.captures / "cad/Owned.pretty/R2.kicad_mod"
        raw = path.read_bytes().replace(
            b"(start -2 -1) (end 2 1)", b"(start -2 -2.85) (end 2 3.9)"
        )
        path.write_bytes(raw)
        seal_capture(self.captures)
        self.design["mechanics"] = dict(access_owners=[])
        self.compile()
        model = json.loads(
            (self.root / "bundle/inputs/analysis/geometry-model.json").read_bytes()
        )
        for body in model["packages"].values():
            self.assertEqual(body["offset_mm"], [0.0, 0.525])
            self.assertEqual(body["size_mm"], [4.0, 6.75, 8])
            for size, offset in zip(body["size_mm"], body["offset_mm"]):
                for sign in (-1, 1):
                    bound_nm = (
                        Decimal(str(offset)) + sign * Decimal(str(size)) / 2
                    ) * 1000000
                    self.assertEqual(bound_nm, bound_nm.to_integral_value())


class DiffPairIntentTests(unittest.TestCase):
    """Differential pairs are declared intent, never guessed from names (tool defect 31)."""

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.captures = self.root / "captures"
        self.captures.mkdir()
        self.design = design_fixture(self.captures)

    def nets(self, first, second=None, pairs=None):
        """R1/R2 on ``first``, R3/R4 on ``second`` (when given), every other pin on GND."""
        d = self.design
        d["parts"].update({r: dict(d["parts"]["R1"]) for r in ("R3", "R4")})
        d["nets"] = {first: ["R1.1", "R2.1"], "GND": ["R1.2", "R2.2", "R3.2", "R4.2"]}
        if second is None:
            d["nets"]["GND"] += ["R3.1", "R4.1"]
        else:
            d["nets"][second] = ["R3.1", "R4.1"]
        witnesses = d["functional"][0]["witnesses"]
        witnesses[0]["nets"] = [first, "GND"]
        witnesses[1]["net"] = first
        if pairs is not None:
            d["diff_pairs"] = pairs
        return d

    def plan(self, name="bundle"):
        compile_design(self.design, self.captures, self.root / name)
        return json.loads((self.root / name / "build-plan.json").read_text())

    def refused(self, name):
        with self.assertRaises(DesignError) as caught:
            compile_design(self.design, self.captures, self.root / name)
        self.assertFalse((self.root / name).exists())
        return caught.exception.problems

    def test_undeclared_design_keeps_pair_like_names_and_routes_without_pair_handling(self):
        from agent_ee import routing_adapter

        # The benchmark r5b sense net, beside names the router would pair under --diff-pairs auto.
        self.nets("LOAD_P", "RXP")
        self.design["parts"].update({r: dict(self.design["parts"]["R1"]) for r in ("R5", "R6")})
        self.design["nets"].update(RXN=["R5.1", "R6.1"], **{"V+": ["R5.2", "R6.2"]})
        router = self.plan()["placement"]["router"]
        self.assertEqual(router, {"checkout": "/opt/kicad-route"})
        project = json.loads((self.root / "bundle/inputs/project/owned.kicad_pro").read_text())
        self.assertFalse([k for k in project["net_settings"]["classes"][0] if k.startswith("diff_pair")])
        options = routing_adapter.validate_router_options(router)
        argv = routing_adapter.board_route_argv(
            router["checkout"], "/staged", "/out", budget_s=60, diff_pairs=options["diff_pairs"])
        self.assertNotIn("--diff-pairs", argv)

    def test_declared_pair_and_its_intent_reach_the_router_command(self):
        from agent_ee import routing_adapter

        match = {"tolerance_mm": 0.1, "amplitude_mm": 1}
        impedance = {"target_ohm": 90, "tolerance_percent": 10, "max_uncoupled_mm": 3}
        self.nets("USB_P", "USB_N", [{"positive": "USB_P", "negative": "USB_N",
                                      "intra_pair_match": match, "impedance": impedance}])
        router = self.plan()["placement"]["router"]
        self.assertEqual(router, {
            "checkout": "/opt/kicad-route", "diff_pairs": "auto", "intra_pair_match": match,
            "pair_layers": ["F.Cu"],  # the outer layer opposite the declared B.Cu plane
            "pair_impedance": dict(impedance, reference_net="GND")})
        recorded = json.loads((self.root / "bundle/inputs/design.json").read_text())
        self.assertEqual(recorded["diff_pairs"], self.design["diff_pairs"])
        # The router pairs only nets whose class has a diff-pair width and gap (native check below).
        default = json.loads((self.root / "bundle/inputs/project/owned.kicad_pro").read_text())[
            "net_settings"]["classes"][0]
        self.assertEqual({k: default[k] for k in ("diff_pair_width", "diff_pair_gap", "diff_pair_via_gap")},
                         {"diff_pair_width": 0.2, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25})
        derivation = json.loads((self.root / "bundle/inputs/project-rule-derivation.json").read_text())
        self.assertEqual(derivation["routing"]["diff_pair_gap"]["selected"], 0.25)
        options = routing_adapter.validate_router_options(router)
        argv = routing_adapter.board_route_argv(
            router["checkout"], "/staged", "/out", budget_s=60, diff_pairs=options["diff_pairs"],
            pair_layers=options["pair_layers"], pair_impedance=options["pair_impedance"],
            intra_pair_match=options["intra_pair_match"])
        self.assertEqual(argv[argv.index("--diff-pairs") + 1], "auto")
        self.assertEqual(argv[argv.index("--pair-impedance") + 1], "90")
        self.assertEqual(argv[argv.index("--impedance-reference") + 1], "GND")
        self.assertIn("--diff-pair-intra-match", argv)
        self.nets("CLK_P", "CLK_N", [{"positive": "CLK_P", "negative": "CLK_N"}])
        self.assertEqual(self.plan("plain")["placement"]["router"],
                         {"checkout": "/opt/kicad-route", "diff_pairs": "auto"})

    def test_pair_naming_refusals_say_how_to_name(self):
        pair = {"positive": "USB_P", "negative": "USB_N"}
        cases = [
            ("convention", ("USB_DP", "USB_DM", [{"positive": "USB_DP", "negative": "USB_DM"}]),
             "diff_pairs[0]: the router pairs nets only by name, as STEM_P and STEM_N: name this pair "
             "USB_D_P (positive) and USB_D_N (negative)"),
            ("swapped", ("USB_P", "USB_N", [{"positive": "USB_N", "negative": "USB_P"}]),
             "diff_pairs[0]: the router pairs nets only by name"),
            ("stems", ("A_P", "B_N", [{"positive": "A_P", "negative": "B_N"}]),
             "name this pair A_P (positive) and A_N (negative)"),
            ("undeclared-suffix", ("USB_P", "LOAD_P", [pair]), "nets.LOAD_P: with diff_pairs declared, "
             "the router treats LOAD_P as a differential-pair member (LOAD_P with LOAD_N) by its name; "
             "declare that pair in diff_pairs, or rename LOAD_P"),
            ("undeclared-sign", ("USB_P", "VOUT+", [pair]), "nets.VOUT+: with diff_pairs declared"),
            ("bare-letters", ("TXP", "TXN", [{"positive": "TXP", "negative": "TXN"}]),
             "name this pair TX_P (positive) and TX_N (negative)"),
            ("unknown-net", ("USB_P", "USB_N", [{"positive": "USB_P", "negative": "OTHER_N"}]),
             "diff_pairs[0].negative: select a declared net"),
            ("one-pair-per-net", ("USB_P", "USB_N", [pair, pair]),
             "diff_pairs[1].positive: USB_P is already in diff_pairs[0]"),
        ]
        for name, (first, second, pairs), message in cases:
            with self.subTest(name=name):
                self.setUp()
                self.nets(first, second, pairs)
                problems = self.refused(name)
                self.assertTrue(any(message in p for p in problems), problems)

    def test_undeclared_mates_beside_a_declared_pair_are_refused(self):
        self.nets("USB_P", "USB_N", [{"positive": "USB_P", "negative": "USB_N"}])
        self.design["parts"].update({r: dict(self.design["parts"]["R1"]) for r in ("R5", "R6")})
        self.design["nets"].update(TXP=["R5.1", "R6.1"], TXN=["R5.2", "R6.2"])
        problems = self.refused("mates")
        self.assertEqual([p.split(":")[0] for p in problems], ["nets.TXN", "nets.TXP"])

    def test_pair_intent_must_agree_and_be_routable(self):
        match = {"tolerance_mm": 0.1, "amplitude_mm": 1}
        self.nets("A_P", "A_N")
        self.design["parts"].update({r: dict(self.design["parts"]["R1"]) for r in ("R5", "R6")})
        self.design["nets"].update(B_P=["R5.1", "R6.1"], B_N=["R5.2", "R6.2"])
        for name, pairs, message in (
            ("disagree", [{"positive": "A_P", "negative": "A_N", "intra_pair_match": match},
                          {"positive": "B_P", "negative": "B_N"}],
             "diff_pairs[1].intra_pair_match: the router applies one intra_pair_match setting to every pair"),
            ("value", [{"positive": "A_P", "negative": "A_N", "intra_pair_match": dict(match, tolerance_mm=0)},
                       {"positive": "B_P", "negative": "B_N", "intra_pair_match": dict(match, tolerance_mm=0)}],
             "diff_pairs[0].intra_pair_match: intra-pair matching values must be finite"),
            ("impedance", [{"positive": "A_P", "negative": "A_N", "impedance": {
                "target_ohm": 90, "tolerance_percent": 100, "max_uncoupled_mm": 3}}],
             "diff_pairs[0].impedance: pair impedance needs positive finite numbers"),
            ("shape", [{"positive": "A_P", "negative": "A_N", "layer": "F.Cu"}],
             "diff_pairs[0].layer: unknown field"),
        ):
            with self.subTest(name=name):
                self.design["diff_pairs"] = pairs
                if name == "impedance":
                    self.design["nets"]["GND"] += self.design["nets"].pop("B_P") + self.design["nets"].pop("B_N")
                problems = self.refused(name)
                self.assertTrue(any(message in p for p in problems), problems)

    def test_compiled_project_gives_declared_pairs_native_width_and_gap(self):
        """kicad-route pairs a net only when its effective net class has a positive diff-pair width and
        a gap no smaller than its clearance (router diff_pairs.detect, read as in its kicad/pairs.py)."""
        from tests.toolchain import require_toolchain

        require_toolchain(native=True)
        import pcbnew
        import shutil

        self.nets("USB_P", "USB_N", [{"positive": "USB_P", "negative": "USB_N"}])
        self.plan()
        scratch, routed = self.root / "scratch", self.root / "routed"
        scratch.mkdir()
        routed.mkdir()
        board = pcbnew.BOARD()
        for name in ("/USB_P", "/USB_N"):
            board.Add(pcbnew.NETINFO_ITEM(board, name))
        pcbnew.SaveBoard(str(scratch / "owned.kicad_pcb"), board)
        # The board beside the compiled project, as the router loads it; SaveBoard's own project stays apart.
        shutil.copyfile(scratch / "owned.kicad_pcb", routed / "owned.kicad_pcb")
        shutil.copyfile(self.root / "bundle/inputs/project/owned.kicad_pro", routed / "owned.kicad_pro")
        settings = pcbnew.LoadBoard(str(routed / "owned.kicad_pcb")).GetDesignSettings().m_NetSettings
        for name in ("/USB_P", "/USB_N"):
            with self.subTest(net=name):
                netclass = settings.GetEffectiveNetClass(name)
                self.assertTrue(netclass.HasDiffPairWidth() and netclass.HasDiffPairGap())
                self.assertGreater(netclass.GetDiffPairWidth(), 0)
                self.assertGreaterEqual(netclass.GetDiffPairGap(), netclass.GetClearance())


def ipc2152_oracle_mm(current_a, rise_c, oz):
    """The published IPC-2152 generic-chart fit restated independently, rounded up to 1 um."""
    import math

    area = (117.555 * rise_c ** -0.913 + 1.15) * current_a ** (0.84 * rise_c ** -0.108 + 1.159)
    return math.ceil(area / (1.37 * oz) * 0.0254 * 1000 - 1e-9) / 1000


class DeclaredCurrentTests(unittest.TestCase):
    """AGENTEE-582: a declared net current sets that net's routed width through the net-constraint path."""

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.captures = self.root / "captures"
        self.captures.mkdir()
        self.design = design_fixture(self.captures)

    def compile(self, name, currents=None):
        design = copy.deepcopy(self.design)
        if currents is not None:
            design["currents"] = currents
        compile_design(design, self.captures, self.root / name)
        return self.root / name / "inputs"

    def test_one_amp_gets_the_ipc2152_width_in_class_rule_record_and_final_check(self):
        inputs = self.compile("one-amp", {"SIGNAL": {"current_ma": 1000}})
        width = ipc2152_oracle_mm(1.0, 10, 1)
        self.assertEqual(width, 0.288)
        project = json.loads((inputs / "project/owned.kicad_pro").read_text())
        classes = {c["name"]: c for c in project["net_settings"]["classes"]}
        self.assertEqual(classes["CURRENT_SIGNAL"]["track_width"], width)
        # The router reads this pattern; '/' stays literal for its glob matching.
        self.assertEqual(project["net_settings"]["netclass_patterns"],
                         [{"netclass": "CURRENT_SIGNAL", "pattern": "/SIGNAL"}])
        self.assertEqual({k: v for k, v in classes["CURRENT_SIGNAL"].items() if k not in ("name", "priority", "track_width")},
                         {k: v for k, v in classes["Default"].items() if k not in ("name", "track_width")})
        self.assertIn("(rule \"agent_ee_netclass_CURRENT_SIGNAL\"\n  (condition \"A.NetClass == 'CURRENT_SIGNAL'\")\n"
                      "  (constraint track_width (min 0.288mm)))", (inputs / "project/owned.kicad_dru").read_text())
        record = json.loads((inputs / "net-constraints.record.json").read_text())
        (spec,) = record["classes"]
        self.assertEqual((record["outcome"], spec["nets"], spec["min_track_width_mm"]), ("pass", ["/SIGNAL"], width))
        basis = spec["basis"]
        self.assertEqual((basis["current_ma"], basis["temperature_rise_c"], basis["width_mm"]), (1000, 10, width))
        self.assertEqual(basis["current_source"], dict(path="design.json", locator="/currents/SIGNAL/current_ma"))
        self.assertEqual(basis["temperature_rise_source"]["selection"], "agent-selected")
        self.assertIn("Not an owner", basis["temperature_rise_source"]["authority"])
        self.assertEqual((basis["copper"]["copper_weight_oz"], basis["copper"]["effective_thickness_mm"]), (1, "0.034798"))
        self.assertEqual(basis["copper"]["source"], dict(path="fab-profile.json", locator="/stackup/layers",
                                                         sha256=digest(inputs / "fab-profile.json")))
        self.assertNotIn("GND", record["nets"])
        rows = {r["id"]: r for r in json.loads((inputs / "requirement-map.json").read_text())["requirements"]}
        for rid in ("TEST-DELIV-1", "TEST-DELIV-2"):
            geometry = next(c for c in rows[rid]["checks"] if c["id"] == "geometry")
            self.assertEqual(geometry["params"]["net_constraints"], "net-constraints.record.json")
        self.assertEqual(spec["requirement"], "TEST-DELIV-2")
        self.assertIn("SIGNAL 1000 mA at least 0.288 mm", (inputs / "spec.md").read_text())
        from agent_ee.net_constraints import net_minimums

        board = inputs / "project/owned.kicad_pcb"
        board.write_bytes(b'(kicad_pcb (net 1 "/SIGNAL") (net 2 "GND"))')
        self.assertEqual(net_minimums(inputs / "net-constraints.record.json", inputs / "project/owned.kicad_pro"),
                         {"/SIGNAL": width})

    def test_other_currents_follow_the_fit_and_small_ones_keep_the_default_width(self):
        for current, expected in ((2000, ipc2152_oracle_mm(2.0, 10, 1)), (500, ipc2152_oracle_mm(0.5, 10, 1))):
            with self.subTest(current=current):
                inputs = self.compile(f"amps-{current}", {"SIGNAL": {"current_ma": current}})
                (spec,) = json.loads((inputs / "net-constraints.record.json").read_text())["classes"]
                self.assertEqual(spec["min_track_width_mm"], expected)
                project = json.loads((inputs / "project/owned.kicad_pro").read_text())
                routed = next(c for c in project["net_settings"]["classes"] if c["name"] == "CURRENT_SIGNAL")
                # A small current never narrows a net below the Default routing width.
                self.assertEqual(routed["track_width"], max(expected, 0.2))
        self.assertEqual((ipc2152_oracle_mm(2.0, 10, 1), ipc2152_oracle_mm(0.5, 10, 1)), (1.012, 0.082))

    def test_nets_without_declared_current_keep_todays_bundle(self):
        plain = self.compile("plain")
        self.assertFalse((plain / "net-constraints.json").exists())
        self.assertFalse((plain / "net-constraints.record.json").exists())
        # The fab profile's plated-hole rule is a plain bundle's only custom rule.
        self.assertNotIn("agent_ee_netclass_", (plain / "project/owned.kicad_dru").read_text())
        settings = json.loads((plain / "project/owned.kicad_pro").read_text())["net_settings"]
        self.assertEqual([c["name"] for c in settings["classes"]], ["Default"])
        self.assertNotIn("netclass_patterns", settings)
        rows = json.loads((plain / "requirement-map.json").read_text())["requirements"]
        geometry = next(c for r in rows for c in r.get("checks", []) if c["id"] == "geometry")
        self.assertEqual(sorted(geometry["params"]), ["covered_primitives", "criterion", "exclude_nets", "min_track_width_mm"])
        self.assertNotIn("TEST-DELIV-2", {r["id"] for r in rows})

        current = self.compile("current", {"SIGNAL": {"current_ma": 1000}})

        def files(root):
            return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}

        before, after = files(plain), files(current)
        changed = {name for name in before.keys() & after.keys() if before[name] != after[name]}
        # The spec gains the width requirement; parts.json changes only in its spec-record hash binding.
        self.assertEqual(changed, {"design.json", "spec.md", "spec-record.json", "obligations.json",
                                   "requirement-map.json", "parts.json", "project/owned.kicad_pro",
                                   "project/owned.kicad_dru"})
        # The width rule is added after the fab profile's custom rule, which stays unchanged.
        self.assertTrue(after["project/owned.kicad_dru"].startswith(before["project/owned.kicad_dru"]))
        parts = [json.loads(side["parts.json"]) for side in (before, after)]
        for side in parts:
            side["declaration_source"].pop("sha256")
        self.assertEqual(parts[0], parts[1])
        self.assertEqual(after.keys() - before.keys(), {"net-constraints.json", "net-constraints.record.json"})
        record = json.loads(after["net-constraints.record.json"])
        self.assertEqual((record["template_rules_sha256"], record["rules_sha256"]),
                         tuple(hashlib.sha256(side["project/owned.kicad_dru"]).hexdigest() for side in (before, after)))
        self.assertEqual(json.loads(before["project/owned.kicad_pro"])["net_settings"]["classes"][0],
                         json.loads(after["project/owned.kicad_pro"])["net_settings"]["classes"][0])

    def test_router_resolves_the_declared_width_and_leaves_other_nets_on_default(self):
        import subprocess
        from toolchain import require_kicad_route
        from agent_ee import kicad_route_attempt, routing_adapter

        checkout = require_kicad_route()
        state = Path(routing_adapter.preflight(checkout)["state"])
        generation, _ = kicad_route_attempt.state_generation(state)
        venv = state / generation / "venv" if generation else state / ".venv-krt"
        inputs = self.compile("router", {"SIGNAL": {"current_ma": 1000}})
        board = inputs / "project/owned.kicad_pcb"  # absent: the router reads the sibling project
        script = ("import json, sys; sys.path.insert(0, sys.argv[1]); "
                  "from list_nets import read_design_rules, net_track_width_map_by_id; "
                  "rules = read_design_rules(sys.argv[2]); "
                  "print(json.dumps(net_track_width_map_by_id(sys.argv[2], {1: '/SIGNAL', 2: 'GND'}, rules)))")
        done = subprocess.run(routing_adapter.router_python(
            "-c", script, str(checkout / "src/kicad_route/router/py_router"), str(board),
            python=str(venv / "bin/python")), capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout.splitlines()[-1]), {"1": 0.288})

    def test_declared_currents_are_refused_with_actionable_fields(self):
        cases = {
            "currents.NOPE: declare a current only for a net in nets": {"NOPE": {"current_ma": 10}},
            "currents.SIGNAL.current_ma: supply the continuous current in mA": {"SIGNAL": {"current_ma": 0}},
            "currents.SIGNAL.current_ma: supply": {"SIGNAL": {"current_ma": 30001}},
            "currents.GND.current_ma: supply": {"GND": {"current_ma": True}},
            "currents.SIGNAL.amps: unknown field": {"SIGNAL": {"current_ma": 1, "amps": 1}},
            "currents.SIGNAL.current_ma: supply this required field": {"SIGNAL": {}},
            "currents: supply a dict": [],
        }
        for message, currents in cases.items():
            with self.subTest(message=message), self.assertRaises(DesignError) as caught:
                self.compile("refused", currents)
            self.assertIn(message, str(caught.exception))
            self.assertFalse((self.root / "refused").exists())
        paired = DiffPairIntentTests.nets(self, "D_P", "D_N", [{"positive": "D_P", "negative": "D_N"}])
        paired["currents"] = {"D_P": {"current_ma": 100}}
        with self.assertRaisesRegex(DesignError, r"currents\.D_P: D_P is in diff_pairs\[0\], which the router "
                                                 r"draws at the pair's diff-pair width.*\[tool_gap\]"):
            compile_design(paired, self.captures, self.root / "paired")
        self.design = design_fixture(self.captures)
        renamed = json.loads(json.dumps(self.design).replace('"SIGNAL"', '"SIG.A"'))
        renamed["currents"] = {"SIG.A": {"current_ma": 100}}
        with self.assertRaisesRegex(DesignError, r"currents\.SIG\.A: name a net .* \[tool_gap\]"):
            compile_design(renamed, self.captures, self.root / "renamed")

    def test_local_copper_narrower_than_the_derived_width_is_refused_before_placement(self):
        from agent_ee.design import validate

        self.design["decoupling"] = [dict(capacitor="R2", device="R1", supply=["R1.1", "R2.1"],
                                          **{"return": ["R1.2", "R2.2"]}, max_distance_mm=6)]
        self.design["currents"] = {"SIGNAL": {"current_ma": 2000}}
        with self.assertRaises(DesignError) as caught:
            validate(self.design, self.captures)
        self.assertIn("decoupling[0].supply: generated local copper on SIGNAL is at most 1 mm wide (the narrower "
                      "land of R1.1 and R2.1), below the 1.012 mm its declared 2000 mA needs", str(caught.exception))
        self.assertNotIn("decoupling[0].return", str(caught.exception))
        self.design["currents"] = {"SIGNAL": {"current_ma": 1000}}
        validate(self.design, self.captures)

    def test_lands_only_bent_routes_reach_compile_with_an_advisory_not_a_refusal(self):
        """Sol review of 91688717, P1: eight blocked straight rays refused a design the router completes.

        The reviewer's reproducer: 0.6 mm lands, eight 0.6 mm holes at radius 3 mm, SIGNAL at 1 A. kicad-route
        routed that geometry at 0.288 mm and native validation passed, so no reach claim may refuse it."""
        import math
        import warnings
        from agent_ee.design import validate

        def ring(cx):
            return "".join(f'(pad "" np_thru_hole circle (at {cx + 3 * math.cos(k * math.pi / 4):.6f} '
                           f'{3 * math.sin(k * math.pi / 4):.6f}) (size 0.6 0.6) (drill 0.6) (layers "*.Cu" "*.Mask"))'
                           for k in range(8))

        path = self.captures / "cad/Owned.pretty/R2.kicad_mod"
        self.design["board"].update(width_mm=50, height_mm=30)
        self.design["currents"] = {"SIGNAL": {"current_ma": 1000}}
        path.write_bytes(kicad_footprint(
            "R2", '(pad "1" smd rect (at 0 0) (size 0.6 0.6) (layers "F.Cu" "F.Mask")) '
                  '(pad "2" smd rect (at 5 3) (size 0.6 0.6) (layers "F.Cu" "F.Mask")) ' + ring(0),
            extra='(fp_rect (start -4 -4) (end 6 4) (stroke (width 0.05) (type default)) (layer "F.CrtYd"))'))
        seal_capture(self.captures)
        validate(self.design, self.captures)
        # The same ring around the fixture's registrable chip land: the full compile completes.
        path.write_bytes(kicad_footprint("R2", chip_kicad(1, 1, 1) + " " + ring(-1),
            extra='(fp_rect (start -4.5 -3.5) (end 2.5 3.5) (stroke (width 0.05) (type default)) (layer "F.CrtYd"))'))
        seal_capture(self.captures)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            inputs = self.compile("ring", self.design["currents"])
        advisories = [w for w in json.loads((inputs / "design-compile-report.json").read_text())["warnings"]
                      if w.startswith("currents.")]
        self.assertEqual(len(advisories), 2)
        self.assertIn("currents.SIGNAL: advisory: no straight 0.288 mm track", advisories[0])
        self.assertIn("reaches R1 pad 1 (Owned:R2)", advisories[0])
        self.assertIn("widest straight approach 0 mm); a route with bends may still fit, and the final "
                      "copper-width/1 check decides", advisories[0])
        self.assertEqual(advisories, [str(w.message) for w in caught if str(w.message).startswith("currents.")])
        (spec,) = json.loads((inputs / "net-constraints.record.json").read_text())["classes"]
        self.assertEqual(spec["min_track_width_mm"], 0.288)


class TrackAccessWidthTests(unittest.TestCase):
    """The widest track that reaches a land, against hand-computed land geometry."""

    def width(self, pads, number="1", same=("1",), clearance=0.1):
        from agent_ee.design_geometry import track_access_width
        from agent_ee.symbol_library import parse_sexpr

        return track_access_width(parse_sexpr(kicad_footprint("X", pads)), number, set(same), clearance)

    def test_soic_row_allows_the_track_along_the_land(self):
        row = "".join(f'(pad "{n}" smd roundrect (at -2.475 {y}) (size 1.95 0.6) (layers "F.Cu" "F.Mask") '
                      f'(roundrect_rratio 0.25))' for n, y in (("1", -1.905), ("2", -0.635), ("3", 0.635)))
        # Along the land: the neighbour's straight side is 1.27 - 0.3 mm away.
        self.assertAlmostEqual(self.width(row, "2", ("2",)), 2 * (1.27 - 0.3 - 0.1), places=9)

    def test_circular_obstacles_are_modelled_exactly(self):
        circle = '(pad "1" smd rect (at 0 0) (size 0.2 0.2) (layers "F.Cu")) ' + "".join(
            f'(pad "{n}" smd circle (at {x} {y}) (size 1 1) (layers "F.Cu"))'
            for n, (x, y) in zip("2345678", ((2, 1), (2, -1), (-2, 1), (-2, -1), (0, 2), (0, -2), (3, 0))))
        # Best ray is -x: the circles at (-2, +-1) are 1 mm from it, less their 0.5 mm radius.
        self.assertAlmostEqual(self.width(circle), 2 * (1 - 0.5 - 0.1), places=9)

    def test_other_layer_and_same_net_lands_are_not_obstacles(self):
        walls = "".join(f'(pad "{n}" smd rect (at {x} {y}) (size 0.5 0.5) (layers "{layer}"))'
                        for n, x, y, layer in (("2", 1, 0, "B.Cu"), ("3", -1, 0, "F.Cu"), ("4", 0, 1, "F.Cu"),
                                               ("5", 0, -1, "F.Cu"), ("6", 0.7, 0.7, "F.Cu"), ("7", -0.7, 0.7, "F.Cu"),
                                               ("8", 0.7, -0.7, "F.Cu"), ("9", -0.7, -0.7, "F.Cu")))
        target = '(pad "1" smd rect (at 0 0) (size 0.4 0.4) (layers "F.Cu")) '
        front = walls.replace('"B.Cu"', '"F.Cu"')
        # Only +x is open, past the diagonal lands whose near sides are 0.7 - 0.25 mm away.
        self.assertAlmostEqual(self.width(target + walls), 2 * (0.45 - 0.1), places=9)
        self.assertAlmostEqual(self.width(target + front, same=("1", "2")), 2 * (0.45 - 0.1), places=9)
        self.assertEqual(self.width(target + front), 0.0)

    def test_footprint_with_a_custom_land_gets_no_claim(self):
        target = '(pad "1" smd rect (at 0 0) (size 0.4 0.4) (layers "F.Cu")) '
        self.assertIsNone(self.width(target + '(pad "2" smd custom (at 3 0) (size 1 1) (layers "F.Cu"))'))
