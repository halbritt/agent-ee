"""Compiler CPL derivation uses the same pad and polarity semantics as the gate."""

import json
from pathlib import Path
import tempfile
import unittest

from agent_ee.design import DesignError
from agent_ee.design_compile import cpl_policy
from agent_ee.storage import digest, write_json
from tests.land_pattern_fixtures import (
    chip,
    chip_kicad,
    component,
    kicad_footprint,
    provenance,
)
from tests.test_cpl_rotation import SOIC_PADS, DIODE_PADS, soic_supplier, diode_supplier
from tests.test_native_binding import GEN


class CompileCplTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.root / "supplier").mkdir()
        (self.root / "named").mkdir()
        (self.root / "Owned.pretty").mkdir()

    def policy(
        self,
        pads,
        supplier,
        *,
        ref="R1",
        name="Chip",
        package="0603",
        second_supplier=None,
    ):
        footprint = f"Owned:{name}"
        native_path = self.root / f"Owned.pretty/{name}.kicad_mod"
        native_path.write_bytes(kicad_footprint(name, pads))
        write_json(
            self.root / "cad.json",
            dict(
                schema="agent-ee.footprint-libraries/1",
                complete=True,
                libraries=[
                    dict(
                        name="Owned",
                        members=[
                            dict(
                                path=str(native_path.relative_to(self.root)),
                                sha256=digest(native_path),
                            )
                        ],
                    )
                ],
            ),
        )
        refs = [(ref, "C1", supplier)]
        if second_supplier is not None:
            refs.append(("R2", "C2", second_supplier))
        design = dict(parts={})
        for reference, lcsc, raw in refs:
            (self.root / f"supplier/{lcsc}.raweasy.json").write_bytes(raw)
            (self.root / f"supplier/{lcsc}.provenance.json").write_bytes(
                provenance(lcsc, f"supplier/{lcsc}.raweasy.json", raw)
            )
            write_json(self.root / f"named/{reference}.json", dict(package=package))
            design["parts"][reference] = dict(
                footprint=footprint, lcsc=lcsc, lib_id="Owned:R"
            )
        cpl_policy(design, self.root)
        from agent_ee.cpl_rotation import load_policy

        return load_policy((self.root / "cpl/jlc-cpl-policy.json").read_bytes())

    def test_reflection_and_supplier_pad_set_mismatch_name_the_design_part(self):
        cases = [
            (SOIC_PADS, soic_supplier(flip=True), "reflected"),
            (SOIC_PADS, component("C1", chip(4, 2, 2)), "pad sets disagree"),
        ]
        for index, (pads, raw, message) in enumerate(cases):
            with self.subTest(message=message):
                original = self.root
                self.root = original / str(index)
                self.root.mkdir()
                for directory in ("supplier", "named", "Owned.pretty"):
                    (self.root / directory).mkdir()
                with self.assertRaisesRegex(DesignError, "parts.U1.*" + message):
                    self.policy(pads, raw, ref="U1")
                self.root = original

    def test_opposite_supplier_diode_numbering_matches_gate_polarity(self):
        power = (
            '(kicad_symbol_lib (version 20241209) (generator "owned") '
            + GEN._definition(
                "R", [(1, [("1", 0, "passive", "K"), ("2", 180, "passive", "A")])]
            )
            + ")"
        )
        (self.root / "Owned.kicad_sym").write_text(power)
        policy = self.policy(
            DIODE_PADS.format(k="K", a="A"),
            diode_supplier("C1", "A", pin2_name="K", pin1_on_right=True),
            ref="D1",
        )
        self.assertEqual(policy["footprint_rules"][0]["rotation_degrees"], 0)

    def test_shared_footprint_uses_per_component_overrides_for_conflicting_suppliers(
        self,
    ):
        shapes = []
        for shape in chip(4, 2, 2):
            fields = shape.split("~")
            if fields[0] == "PAD":
                fields[8] = {"1": "2", "2": "1"}[fields[8]]
            shapes.append("~".join(fields))
        policy = self.policy(
            chip_kicad(1, 1, 1),
            component("C1", chip(4, 2, 2)),
            second_supplier=component("C2", shapes),
        )
        from agent_ee.cpl_rotation import match_policy

        one = match_policy(policy, "R1", "Owned:Chip")[1]
        two = match_policy(policy, "R2", "Owned:Chip")[1]
        self.assertEqual((one["rotation_degrees"], two["rotation_degrees"]), (0, 180))

    def test_wrong_standard_passive_size_refuses_and_correct_size_records_overlap(self):
        with self.assertRaisesRegex(
            DesignError, "native passive size 0805 differs from catalog 0603"
        ):
            self.policy(
                chip_kicad(1, 1, 1),
                component("C1", chip(4, 2, 2)),
                name="R_0805_2012Metric",
            )
        # A separate root avoids overwriting the failed evidence.
        self.root = self.root / "correct"
        self.root.mkdir()
        for directory in ("supplier", "named", "Owned.pretty"):
            (self.root / directory).mkdir()
        self.policy(
            chip_kicad(1, 1, 1),
            component("C1", chip(4, 2, 2)),
            name="R_0603_1608Metric",
        )
        report = json.loads((self.root / "design-compile-report.json").read_bytes())
        metric = report["land_patterns"][0]["copper"]["F.Cu"]
        self.assertIsNotNone(metric["presented"]["lower"])
        self.assertIn(
            "no suitability threshold", report["land_patterns"][0]["limitation"]
        )
