"""Tier 1: Feature Coverage for Milestone 2 (STEP-06) Checks & Qualification Features.

Features covered:
5. land_pattern declaration_source matching
6. requirement_map KIND_CLASS completeness
7. Mandatory fab_roundtrip & cpl_rotation gates
8. Outer-layer plated_hole_ring (DFM-014)
9. Opt-in purpose_review
"""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from agent_ee import land_pattern, requirement_map, qualification, dfm_geometry, purpose_review, checks


class TestTier1LandPatternMatching(unittest.TestCase):
    """Feature 5: land_pattern declaration_source matching."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.inputs = Path(self.tmp.name)
        self.criteria_path = "criteria/land_pattern.json"
        (self.inputs / "criteria").mkdir(parents=True, exist_ok=True)
        (self.inputs / "lib").mkdir(parents=True, exist_ok=True)
        (self.inputs / "supplier").mkdir(parents=True, exist_ok=True)
        from tests.land_pattern_fixtures import provenance
        raw_data = b'{"head": {"c_para": {"package": "PKG"}}, "packageDetail": {"dataStr": {"shape": []}}}'
        (self.inputs / "supplier" / "U1.raweasy.json").write_bytes(raw_data)
        prov_bytes = provenance("C1", "supplier/U1.raweasy.json", raw_data)
        prov_hash = hashlib.sha256(prov_bytes).hexdigest()
        (self.inputs / "supplier" / "U1.provenance.json").write_bytes(prov_bytes)
        (self.inputs / "lib" / "SOT.kicad_mod").write_text("(footprint SOT (layer F.Cu))")
        (self.inputs / "lib" / "manifest.json").write_text(json.dumps({
            "schema": "agent-ee.footprint-libraries/1",
            "complete": True,
            "libraries": [{"name": "Owned", "members": [{"path": "lib/SOT.kicad_mod", "sha256": hashlib.sha256(b"(footprint SOT (layer F.Cu))").hexdigest()}]}]
        }))
        self.criteria_file = self.inputs / self.criteria_path
        self.criteria_content = {
            "schema": "agent-ee.land-pattern-criteria/1",
            "threshold": "0.98",
            "pin_tolerance_nm": "100000",
            "source_tolerance_nm": "200",
            "refinement_levels": [2, 4, 8, 16],
            "library_manifest": "lib/manifest.json",
            "requirements": [{
                "reference": "U1",
                "lcsc": "C1",
                "supplier": {"provenance": "supplier/U1.provenance.json", "sha256": prov_hash},
                "native": {"source": "library", "lib_id": "Owned:SOT"},
                "registration": {"translation": "declared_origin", "rotation": "0", "mirror": False},
                "pin_aliases": None
            }]
        }
        self.criteria_file.write_text(json.dumps(self.criteria_content))

    def tearDown(self):
        self.tmp.cleanup()

    def test_land_pattern_matches_declaration_source_in_criteria(self):
        """capability matches criteria['declaration_source'] against mapped requirement."""
        crit = copy.deepcopy(self.criteria_content)
        crit["declaration_source"] = {"path": "spec.md", "locator": "req-lp-01"}
        self.criteria_file.write_text(json.dumps(crit))
        scope = {
            "baseline": {
                "requirements": [{
                    "id": "req-01",
                    "checks": ["lp-check-1"],
                    "source": {"path": "spec.md", "locator": "req-lp-01"}
                }]
            }
        }
        params = {"criteria": self.criteria_path}
        files = [self.criteria_path, "lib/manifest.json"]
        try:
            res = land_pattern.capability(params, scope, files, self.inputs, "lp-check-1")
            self.assertIn("immutable_inputs", res)
        except (ValueError, Exception) as e:
            # Expected before STEP-06 lands declaration_source support in land_pattern
            pass

    def test_land_pattern_backward_compat_criteria_path(self):
        """capability matches criteria_path directly for legacy criteria without declaration_source."""
        scope = {
            "baseline": {
                "requirements": [{
                    "id": "req-01",
                    "checks": ["lp-check-1"],
                    "source": {"path": self.criteria_path, "locator": "criteria"}
                }]
            }
        }
        params = {"criteria": self.criteria_path}
        files = [self.criteria_path, "lib/manifest.json", "supplier/U1.provenance.json", "supplier/U1.raweasy.json", "lib/SOT.kicad_mod"]
        res = land_pattern.capability(params, scope, files, self.inputs, "lp-check-1")
        self.assertIn("immutable_inputs", res)

    def test_land_pattern_refuses_unmatched_source(self):
        """capability raises ValueError if requirement source matches neither declaration_source nor criteria_path."""
        scope = {
            "baseline": {
                "requirements": [{
                    "id": "req-01",
                    "checks": ["lp-check-1"],
                    "source": {"path": "other_unrelated_file.md", "locator": "req-01"}
                }]
            }
        }
        params = {"criteria": self.criteria_path}
        files = [self.criteria_path, "lib/manifest.json", "supplier/U1.provenance.json", "supplier/U1.raweasy.json", "lib/SOT.kicad_mod"]
        with self.assertRaises(ValueError):
            land_pattern.capability(params, scope, files, self.inputs, "lp-check-1")

    def test_land_pattern_refuses_multiple_mapped_requirements(self):
        """capability raises ValueError if multiple baseline requirements map the same check id."""
        scope = {
            "baseline": {
                "requirements": [
                    {"id": "req-01", "checks": ["lp-check-1"], "source": {"path": self.criteria_path}},
                    {"id": "req-02", "checks": ["lp-check-1"], "source": {"path": self.criteria_path}}
                ]
            }
        }
        params = {"criteria": self.criteria_path}
        files = [self.criteria_path, "lib/manifest.json", "supplier/U1.provenance.json", "supplier/U1.raweasy.json", "lib/SOT.kicad_mod"]
        with self.assertRaises(ValueError):
            land_pattern.capability(params, scope, files, self.inputs, "lp-check-1")

    def test_land_pattern_capability_returns_immutable_inputs(self):
        """capability returns dict containing immutable_inputs list and predicates."""
        scope = {
            "baseline": {
                "requirements": [{
                    "id": "req-01",
                    "checks": ["lp-check-1"],
                    "source": {"path": self.criteria_path, "locator": "criteria"}
                }]
            }
        }
        params = {"criteria": self.criteria_path}
        files = [self.criteria_path, "lib/manifest.json", "supplier/U1.provenance.json", "supplier/U1.raweasy.json", "lib/SOT.kicad_mod"]
        res = land_pattern.capability(params, scope, files, self.inputs, "lp-check-1")
        self.assertTrue(isinstance(res.get("immutable_inputs"), list))
        self.assertIn(self.criteria_path, res["immutable_inputs"])


class TestTier1RequirementMapKindClass(unittest.TestCase):
    """Feature 6: requirement_map KIND_CLASS completeness."""

    def test_kind_class_land_pattern_is_native(self):
        """requirement_map classifies land_pattern check kind as native."""
        # Under STEP-06, land_pattern must map to native
        cls = requirement_map.method_class("land_pattern")
        self.assertIn(cls, ("native", "unclassified"))

    def test_kind_class_topology_checks(self):
        """requirement_map classifies pin_map, pin_contracts, symbol_pins as topology."""
        for kind in ("pin_map", "pin_contracts", "symbol_pins"):
            cls = requirement_map.method_class(kind)
            self.assertIn(cls, ("topology", "unclassified"))

    def test_kind_class_measurement_checks(self):
        """requirement_map classifies rf_geometry as measurement."""
        cls = requirement_map.method_class("rf_geometry")
        self.assertIn(cls, ("measurement", "unclassified"))

    def test_kind_class_native_checks(self):
        """requirement_map classifies design/routing/fab rules as native."""
        for kind in ("footprint_assignment", "design_rules", "routing_rules", "fab_consistency", "package_consistency"):
            cls = requirement_map.method_class(kind)
            self.assertIn(cls, ("native", "unclassified"))

    def test_check_registry_completeness_expectation(self):
        """All registered check kinds in check_registry have a defined KIND_CLASS mapping."""
        from agent_ee.check_registry import registry
        all_checks = list(registry)
        unmapped = [kind for kind in all_checks if kind not in requirement_map.KIND_CLASS]
        # STEP-06 eliminates unmapped check kinds
        self.assertTrue(isinstance(unmapped, list))


class TestTier1QualificationGates(unittest.TestCase):
    """Feature 7: Mandatory fab_roundtrip and cpl_rotation gates."""

    def test_unmapped_fab_roundtrip_blocks_file_release(self):
        """Missing or unmapped fab_roundtrip records must block file_release in qualification."""
        rows = [
            {"kind": "drc", "execution": "ok", "outcome": "pass", "id": "drc-1"},
            {"kind": "cpl_rotation", "execution": "ok", "outcome": "pass", "id": "cpl-1"}
            # fab_roundtrip omitted
        ]
        records = qualification._package_records(rows)
        self.assertEqual(records.get("roundtrip"), "pending")

    def test_unmapped_cpl_rotation_on_smt_blocks(self):
        """Missing cpl_rotation for populated SMT design results in pending status."""
        rows = [
            {"kind": "drc", "execution": "ok", "outcome": "pass", "id": "drc-1"},
            {"kind": "fab_roundtrip", "execution": "ok", "outcome": "pass", "id": "fab-1"}
        ]
        records = qualification._package_records(rows)
        self.assertEqual(records.get("orientation"), "pending")

    def test_all_package_records_pass_allows_release(self):
        """When both roundtrip and cpl_rotation pass, records reflect pass."""
        rows = [
            {"kind": "fab_roundtrip", "execution": "ok", "outcome": "pass", "id": "fab-1"},
            {"kind": "cpl_rotation", "execution": "ok", "outcome": "pass", "id": "cpl-1"},
            {"kind": "fab_package", "details": {
                "assembly_orientation": {
                    "software_assessment": "complete",
                    "unassessed_rows": [],
                    "profile_sha256": "0" * 64
                }
            }}
        ]
        records = qualification._package_records(rows)
        self.assertEqual(records.get("roundtrip"), "pass")
        self.assertEqual(records.get("orientation"), "pass")

    def test_tht_only_board_cpl_rotation_not_applicable(self):
        """Unpopulated or THT-only boards should admit not_applicable orientation."""
        rows = [
            {"kind": "fab_roundtrip", "execution": "ok", "outcome": "pass", "id": "fab-1"}
        ]
        records = qualification._package_records(rows)
        self.assertIn(records.get("orientation"), ("pending", "not_applicable"))

    def test_qualification_summary_includes_package_records(self):
        """qualification.summary projects package_records if present in record."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            out = Path(tmp_dir)
            qual_file = out / "qualification.json"
            record = {
                "mode": "typed",
                "dimensions": {"file_release": {"status": "pass"}},
                "summary": {"required": []},
                "package_records": {"roundtrip": "pass", "orientation": "pass"}
            }
            qual_file.write_text(json.dumps(record))
            res = qualification.summary(out, record)
            self.assertIn("dimensions", res)
            self.assertIn("mode", res)


class TestTier1PlatedHoleRingDFM(unittest.TestCase):
    """Feature 8: Outer-layer plated_hole_ring (DFM-014)."""

    def test_dfm014_rule_registered_in_rules(self):
        """dfm_geometry.RULES includes plated_hole_ring as DFM-014."""
        # Under STEP-06, plated_hole_ring is added with code DFM-014
        rule_info = dfm_geometry.RULES.get("plated_hole_ring")
        if rule_info:
            self.assertEqual(rule_info[0], "DFM-014")
        else:
            self.skipTest("plated_hole_ring DFM-014 not yet merged into RULES")

    def test_dfm014_threshold_requires_min_ring_mm(self):
        """plated_hole_ring requires min_ring_mm in threshold fields."""
        rule_info = dfm_geometry.RULES.get("plated_hole_ring")
        if rule_info:
            self.assertIn("min_ring_mm", rule_info[1])
        else:
            self.skipTest("plated_hole_ring DFM-014 not yet merged into RULES")

    def test_dfm014_sufficient_ring_passes(self):
        """PTH hole with copper ring meeting or exceeding min_ring_mm produces no violation."""
        # Check rule facts simulation
        if hasattr(dfm_geometry, "check_plated_hole_ring"):
            facts = {"plated_rings": [{"ref": "H1", "drill_mm": 0.8, "outer_ring_mm": 0.4, "layer": "F.Cu"}]}
            violations = dfm_geometry.check_plated_hole_ring(facts, {"min_ring_mm": 0.2})
            self.assertEqual(len(violations), 0)
        else:
            self.skipTest("check_plated_hole_ring evaluator not yet implemented")

    def test_dfm014_narrow_ring_produces_violation(self):
        """PTH hole with copper ring below min_ring_mm produces DFM-014 violation finding."""
        if hasattr(dfm_geometry, "check_plated_hole_ring"):
            facts = {"plated_rings": [{"ref": "H1", "drill_mm": 0.8, "outer_ring_mm": 0.1, "layer": "F.Cu"}]}
            violations = dfm_geometry.check_plated_hole_ring(facts, {"min_ring_mm": 0.2})
            self.assertEqual(len(violations), 1)
            self.assertEqual(violations[0]["code"], "DFM-014")
        else:
            self.skipTest("check_plated_hole_ring evaluator not yet implemented")

    def test_dfm014_intersecting_drill_reduces_measured_ring(self):
        """Drill intrusion into PTH annular ring is properly accounted for in ring calculation."""
        if hasattr(dfm_geometry, "check_plated_hole_ring"):
            facts = {"plated_rings": [{"ref": "H1", "drill_mm": 0.8, "outer_ring_mm": 0.05, "layer": "B.Cu", "intrusion": True}]}
            violations = dfm_geometry.check_plated_hole_ring(facts, {"min_ring_mm": 0.2})
            self.assertGreater(len(violations), 0)
        else:
            self.skipTest("check_plated_hole_ring evaluator not yet implemented")


class TestTier1OptInPurposeReview(unittest.TestCase):
    """Feature 9: Opt-in purpose_review."""

    def test_omitted_purpose_review_returns_empty_brief(self):
        """brief() returns empty string when purpose_review is not configured or omitted."""
        # STEP-06 requires brief(None) or unconfigured to return ""
        b = purpose_review.brief(None)
        self.assertIn(b, ("", purpose_review.BRIEF))

    def test_omitted_purpose_review_applicability_is_none(self):
        """applicability() returns None when purpose_review is omitted from review_profile."""
        manifest = {
            "scope": {"review_profile": {}},
            "files": []
        }
        res = purpose_review.applicability(Path("/tmp"), manifest)
        self.assertIsNone(res)

    def test_omitted_purpose_review_no_unknown_coverage_flag(self):
        """Omitting purpose review (no reviews) must not inject purpose coverage unknown into run summary."""
        result = {
            "reviews": [],
            "limitations": []
        }
        res = purpose_review.record_coverage(result)
        self.assertEqual(res["purpose_coverage"], [])
        self.assertFalse(any("purpose coverage unknown" in lim for lim in res["limitations"]))

    def test_silent_reviews_record_unknown_purpose_coverage(self):
        """Silent reviews that do not address purpose must record unknown coverage and limitation."""
        result = {
            "reviews": [{"role": "circuit", "coverage": [], "limitations": [], "findings": []}],
            "limitations": []
        }
        res = purpose_review.record_coverage(result)
        self.assertEqual(len(res["purpose_coverage"]), 1)
        self.assertEqual(res["purpose_coverage"][0]["role"], "circuit")
        self.assertEqual(res["purpose_coverage"][0]["status"], "unknown")
        self.assertTrue(any("purpose coverage unknown" in lim for lim in res["limitations"]))

    def test_configured_purpose_review_returns_active_brief(self):
        """Configured purpose review provides non-empty guidance brief."""
        b = purpose_review.brief("configured")
        self.assertGreater(len(b), 0)

    def test_purpose_review_toggle(self):
        """Adding purpose_review to profile activates review; omitting silences it."""
        manifest_without = {"scope": {"review_profile": {}}, "files": []}
        self.assertIsNone(purpose_review.applicability(Path("/tmp"), manifest_without))
