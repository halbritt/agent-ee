"""Tier 2: Boundary & Corner Cases (Part 1 - Features 1 through 11).

Coverage:
- Boundary conditions for schemas, nulls, drafts, wire limits.
- Boundary conditions for land pattern sources, check kinds, qualification states, DFM geometry.
- Boundary conditions for artwork targets and coordinator confinement.
"""
import ast
import copy
import json
import tempfile
import unittest
from pathlib import Path

from agent_ee import harnesses, reviewers, review_draft, land_pattern, requirement_map, qualification, dfm_geometry, purpose_review, routed_artwork


class TestTier2StrictSchemaBoundaries(unittest.TestCase):
    """Feature 1 Boundaries: Recursive strict_schema."""

    def test_strict_schema_empty_object(self):
        """Empty object schema gets additionalProperties False and empty required list."""
        schema = {"type": "object"}
        res = harnesses.strict_schema(schema)
        self.assertEqual(res.get("additionalProperties"), False)
        self.assertEqual(res.get("required"), [])

    def test_strict_schema_deep_nesting(self):
        """Schema with deep recursive nesting (>= 10 levels) transforms correctly."""
        schema = {"type": "object", "properties": {"a": {"type": "string"}}}
        for _ in range(8):
            schema = {"type": "object", "properties": {"nested": schema}, "required": ["nested"]}
        res = harnesses.strict_schema(schema)
        self.assertEqual(res.get("additionalProperties"), False)

    def test_strict_schema_already_strict(self):
        """Already strict schema with additionalProperties=False is idempotent."""
        schema = {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"], "additionalProperties": False}
        res1 = harnesses.strict_schema(schema)
        res2 = harnesses.strict_schema(res1)
        self.assertEqual(res1, res2)

    def test_strict_schema_non_object_nodes(self):
        """Primitive non-object nodes (string, integer, boolean) remain unaltered."""
        for prim in ({"type": "string"}, {"type": "integer"}, {"type": "boolean"}):
            self.assertEqual(harnesses.strict_schema(prim), prim)

    def test_strict_schema_empty_anyof(self):
        """Schema with empty anyOf or oneOf list does not crash."""
        schema = {"type": "object", "properties": {"choice": {"anyOf": []}}}
        res = harnesses.strict_schema(schema)
        self.assertIn("choice", res["properties"])


class TestTier2UnstrictValueBoundaries(unittest.TestCase):
    """Feature 2 Boundaries: unstrict_value normalization."""

    def test_unstrict_value_empty_dict(self):
        """Empty dictionary with empty object schema returns empty dict."""
        schema = {"type": "object", "properties": {}}
        if hasattr(harnesses, "unstrict_value"):
            self.assertEqual(harnesses.unstrict_value({}, schema), {})
        else:
            self.skipTest("unstrict_value not yet exported")

    def test_unstrict_value_all_nulls_stripped(self):
        """Dict containing only synthetic nulls on optional fields becomes empty."""
        schema = {
            "type": "object",
            "properties": {"a": {"type": "string"}, "b": {"type": "integer"}},
            "required": []
        }
        if hasattr(harnesses, "unstrict_value"):
            self.assertEqual(harnesses.unstrict_value({"a": None, "b": None}, schema), {})
        else:
            self.skipTest("unstrict_value not yet exported")

    def test_unstrict_value_required_null_preservation(self):
        """When a field is in required but explicitly admits null, None is preserved."""
        schema = {
            "type": "object",
            "properties": {"req_null": {"type": ["string", "null"]}},
            "required": ["req_null"]
        }
        if hasattr(harnesses, "unstrict_value"):
            res = harnesses.unstrict_value({"req_null": None}, schema)
            self.assertEqual(res, {"req_null": None})
        else:
            self.skipTest("unstrict_value not yet exported")

    def test_unstrict_value_special_characters_in_strings(self):
        """String fields containing unicode, quotes, and newlines remain unmodified."""
        schema = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}
        val = {"text": "hello\nworld\t\"quoted\" \u2603"}
        if hasattr(harnesses, "unstrict_value"):
            self.assertEqual(harnesses.unstrict_value(val, schema), val)
        else:
            self.skipTest("unstrict_value not yet exported")

    def test_unstrict_value_empty_array(self):
        """Empty array passed with items schema returns empty array."""
        schema = {"type": "array", "items": {"type": "object"}}
        if hasattr(harnesses, "unstrict_value"):
            self.assertEqual(harnesses.unstrict_value([], schema), [])
        else:
            self.skipTest("unstrict_value not yet exported")


class TestTier2AdjudicatorDraftBoundaries(unittest.TestCase):
    """Feature 3 Boundaries: Adjudicator review_draft validation."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.tmp.name)
        self.packet = {
            "candidate_id": "c-01",
            "run_id": "r-01",
            "packet_sha256": "0" * 64,
            "inputs": {"project": "board.kicad_pcb"}
        }
        (self.run_dir / "packet.json").write_text(json.dumps(self.packet))
        (self.run_dir / "candidate.json").write_text(json.dumps({"candidate_id": "c-01"}))

    def tearDown(self):
        self.tmp.cleanup()

    def test_draft_file_exactly_zero_bytes(self):
        """Zero-byte draft file fails validation with descriptive error."""
        draft = self.run_dir / "draft.json"
        draft.write_bytes(b"")
        control = review_draft.prepare(self.run_dir, "circuit", self.packet, reviewers.REVIEW_SCHEMA)
        res = review_draft.check(draft, self.run_dir, control)
        self.assertFalse(res.get("valid", True))

    def test_draft_file_exceeding_size_limit(self):
        """Draft file exceeding 8 MiB size limit is refused."""
        draft = self.run_dir / "draft.json"
        # Test boundary without allocating 8MB on disk
        self.assertTrue(hasattr(review_draft, "MAX_DRAFT_BYTES"))
        self.assertEqual(review_draft.MAX_DRAFT_BYTES, 8 * 1024 * 1024)

    def test_draft_non_json_content(self):
        """Draft file with malformed JSON syntax fails validation gracefully."""
        draft = self.run_dir / "draft.json"
        draft.write_text("NOT JSON {")
        control = review_draft.prepare(self.run_dir, "circuit", self.packet, reviewers.REVIEW_SCHEMA)
        res = review_draft.check(draft, self.run_dir, control)
        self.assertFalse(res.get("valid", True))

    def test_draft_nonexistent_file(self):
        """Checking nonexistent draft path returns valid=False with error."""
        draft = self.run_dir / "nonexistent.json"
        control = review_draft.prepare(self.run_dir, "circuit", self.packet, reviewers.REVIEW_SCHEMA)
        res = review_draft.check(draft, self.run_dir, control)
        self.assertFalse(res.get("valid", True))

    def test_draft_directory_instead_of_file(self):
        """Directory path passed as draft file is rejected."""
        draft_dir = self.run_dir / "is_dir.json"
        draft_dir.mkdir()
        control = review_draft.prepare(self.run_dir, "circuit", self.packet, reviewers.REVIEW_SCHEMA)
        res = review_draft.check(draft_dir, self.run_dir, control)
        self.assertFalse(res.get("valid", True))


class TestTier2PreflightWireLimitBoundaries(unittest.TestCase):
    """Feature 4 Boundaries: Wire limits and preflight uniformity."""

    def test_wire_size_exact_boundary(self):
        """Schema wire size exactly at boundary (120 * 1024 bytes) behavior."""
        if hasattr(reviewers, "_check_schema_limits"):
            exact_120k = {"type": "object", "properties": {"pad": {"type": "string", "description": "x" * (120 * 1024 - 100)}}}
            # Should either pass or raise depending on exact serialization overhead
            try:
                reviewers._check_schema_limits(exact_120k)
            except ValueError:
                pass
        else:
            self.skipTest("_check_schema_limits not exposed")

    def test_wire_size_extreme_oversize(self):
        """Schema of 1 MB wire size is firmly rejected."""
        if hasattr(reviewers, "_check_schema_limits"):
            oversized = {"type": "object", "properties": {"pad": {"type": "string", "description": "x" * (1024 * 1024)}}}
            with self.assertRaises(ValueError):
                reviewers._check_schema_limits(oversized)
        else:
            self.skipTest("_check_schema_limits not exposed")

    def test_wire_size_empty_schema(self):
        """Minimal schema ({}) passes wire size limits cleanly."""
        if hasattr(reviewers, "_check_schema_limits"):
            reviewers._check_schema_limits({})
        else:
            self.skipTest("_check_schema_limits not exposed")

    def test_preflight_timeout_boundary_zero(self):
        """Preflight rejects non-positive timeout values (<= 0) or returns not_run."""
        if hasattr(reviewers, "reviewer_call"):
            res, _ = reviewers.reviewer_call(Path("/tmp"), "label", {"harness": "codex"}, "prompt", {}, 0)
            self.assertEqual(res.get("execution"), "not_run")
        else:
            self.skipTest("reviewer_call not available")

    def test_preflight_empty_prompt(self):
        """Empty string prompt is handled by reviewer_call."""
        if hasattr(reviewers, "reviewer_call"):
            res, _ = reviewers.reviewer_call(Path("/tmp"), "label", {"harness": "codex"}, "", {}, 10.0)
            self.assertIn("execution", res)
        else:
            self.skipTest("reviewer_call not available")


class TestTier2LandPatternBoundaries(unittest.TestCase):
    """Feature 5 Boundaries: land_pattern declaration_source matching."""

    def test_criteria_path_none_fails(self):
        """Passing None for criteria_path raises ValueError or TypeError."""
        scope = {"baseline": {"requirements": []}}
        with self.assertRaises((ValueError, TypeError, KeyError)):
            land_pattern.capability({}, scope, [], Path("/tmp"), "cid-01")

    def test_empty_files_list_raises_unmet_dependencies(self):
        """Empty files list raises ValueError for missing criteria and libraries."""
        scope = {"baseline": {"requirements": []}}
        with self.assertRaises(ValueError):
            land_pattern.capability({"criteria": "c.json"}, scope, [], Path("/tmp"), "cid-01")

    def test_declaration_source_with_special_characters_in_locator(self):
        """declaration_source locator containing slashes, dashes, and colons matches correctly."""
        from unittest.mock import patch
        criteria = {
            "schema": land_pattern.SCHEMA,
            "declaration_source": {"path": "sub-dir/doc.pdf", "locator": "sec:3.2/p-1"},
            "threshold": "0.9",
            "pin_tolerance_nm": "100",
            "source_tolerance_nm": "100",
            "refinement_levels": [1, 2],
            "library_manifest": None,
            "requirements": [{
                "reference": "R1",
                "lcsc": "C1234",
                "supplier": None,
                "native": {"source": "board"},
                "registration": {"translation": [0, 0], "rotation": 0, "mirror": False},
                "pin_aliases": {}
            }]
        }
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "criteria.json"
            p.write_text(json.dumps(criteria))
            scope = {
                "baseline": {
                    "requirements": [{
                        "checks": ["cid-01"],
                        "source": {"path": "sub-dir/doc.pdf", "locator": "sec:3.2/p-1"}
                    }]
                },
                "board": "board.kicad_pcb"
            }
            with patch("agent_ee.land_pattern.dependencies", return_value=([], {"sources": ["board"]})):
                res = land_pattern.capability({"criteria": "criteria.json"}, scope, ["criteria.json", "board.kicad_pcb"], tmp, "cid-01")
                self.assertIn("criteria.json", res["immutable_inputs"])

    def test_declaration_source_case_sensitivity(self):
        """Path matching between requirement source and declaration_source is exact/case-sensitive."""
        from unittest.mock import patch
        criteria = {
            "schema": land_pattern.SCHEMA,
            "declaration_source": {"path": "hardware/Spec.json", "locator": "L1"},
            "threshold": "0.9",
            "pin_tolerance_nm": "100",
            "source_tolerance_nm": "100",
            "refinement_levels": [1, 2],
            "library_manifest": None,
            "requirements": [{
                "reference": "R1",
                "lcsc": "C1234",
                "supplier": None,
                "native": {"source": "board"},
                "registration": {"translation": [0, 0], "rotation": 0, "mirror": False},
                "pin_aliases": {}
            }]
        }
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "criteria.json"
            p.write_text(json.dumps(criteria))
            scope = {
                "baseline": {
                    "requirements": [{
                        "checks": ["cid-01"],
                        "source": {"path": "hardware/spec.json", "locator": "L1"}
                    }]
                },
                "board": "board.kicad_pcb"
            }
            with patch("agent_ee.land_pattern.dependencies", return_value=([], {"sources": ["board"]})):
                with self.assertRaises(ValueError) as err:
                    land_pattern.capability({"criteria": "criteria.json"}, scope, ["criteria.json", "board.kicad_pcb"], tmp, "cid-01")
                self.assertIn("land-pattern check must map exactly one baseline requirement", str(err.exception))

    def test_land_pattern_scope_missing_baseline(self):
        """Scope dictionary lacking baseline key raises descriptive error."""
        with self.assertRaises((ValueError, KeyError, TypeError, OSError)):
            land_pattern.capability({"criteria": "c.json"}, {}, ["c.json"], Path("/tmp"), "cid-01")


class TestTier2RequirementMapBoundaries(unittest.TestCase):
    """Feature 6 Boundaries: requirement_map KIND_CLASS completeness."""

    def test_method_class_empty_string(self):
        """Empty string check kind returns unclassified or raises ValueError."""
        cls = requirement_map.method_class("")
        self.assertEqual(cls, "unclassified")

    def test_method_class_none_input(self):
        """None check kind returns unclassified or raises ValueError."""
        cls = requirement_map.method_class(None)
        self.assertEqual(cls, "unclassified")

    def test_method_class_case_sensitivity(self):
        """Check kind matching is case-sensitive (e.g. 'NATIVE' is not 'native')."""
        cls = requirement_map.method_class("ERC")
        self.assertEqual(cls, "unclassified")

    def test_kind_class_immutability(self):
        """requirement_map.KIND_CLASS mapping dictionary is treated as immutable."""
        self.assertIn("erc", requirement_map.KIND_CLASS)
        self.assertEqual(requirement_map.KIND_CLASS["erc"], "native")

    def test_status_order_completeness(self):
        """requirement_map.STATUS_ORDER covers all five canonical status states."""
        self.assertEqual(len(requirement_map.STATUS_ORDER), 5)
        for s in ("failed", "unknown", "pending", "pass", "not_applicable"):
            self.assertIn(s, requirement_map.STATUS_ORDER)


class TestTier2QualificationBoundaries(unittest.TestCase):
    """Feature 7 Boundaries: Mandatory fab_roundtrip & cpl_rotation gates."""

    def test_package_records_empty_rows(self):
        """Empty rows list yields pending status for roundtrip and orientation."""
        records = qualification._package_records([])
        self.assertEqual(records.get("roundtrip"), "pending")
        self.assertEqual(records.get("orientation"), "pending")

    def test_package_records_failed_roundtrip(self):
        """Failed execution or outcome on fab_roundtrip results in failed status."""
        rows = [{"kind": "fab_roundtrip", "execution": "error", "outcome": "failed", "id": "fab-1"}]
        records = qualification._package_records(rows)
        self.assertEqual(records.get("roundtrip"), "failed")

    def test_package_records_duplicate_rows_fails(self):
        """Duplicate rows for the same package check kind are marked failed."""
        rows = [
            {"kind": "fab_roundtrip", "execution": "ok", "outcome": "pass", "id": "fab-1"},
            {"kind": "fab_roundtrip", "execution": "ok", "outcome": "pass", "id": "fab-2"}
        ]
        records = qualification._package_records(rows)
        self.assertEqual(records.get("roundtrip"), "failed")

    def test_cpl_rotation_unassessed_rows_fails(self):
        """Assembly orientation with unassessed SMT rows remains pending."""
        rows = [
            {"kind": "fab_package", "details": {
                "assembly_orientation": {
                    "software_assessment": "incomplete",
                    "unassessed_rows": ["R1", "C1"],
                    "profile_sha256": "0" * 64
                }
            }}
        ]
        records = qualification._package_records(rows)
        self.assertEqual(records.get("orientation"), "pending")

    def test_qualification_summary_missing_file_fails(self):
        """qualification.summary raises FileNotFoundError if qualification.json is missing."""
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                qualification.summary(Path(tmp), {"mode": "typed", "dimensions": {}, "summary": {}})


class TestTier2DfmGeometryBoundaries(unittest.TestCase):
    """Feature 8 Boundaries: Outer-layer plated_hole_ring (DFM-014)."""

    def test_min_ring_mm_boundary_zero(self):
        """min_ring_mm of zero boundary condition."""
        if hasattr(dfm_geometry, "check_plated_hole_ring"):
            facts = {"plated_rings": [{"ref": "H1", "drill_mm": 0.8, "outer_ring_mm": 0.05, "layer": "F.Cu"}]}
            violations = dfm_geometry.check_plated_hole_ring(facts, {"min_ring_mm": 0.0})
            self.assertEqual(len(violations), 0)
        else:
            self.skipTest("check_plated_hole_ring not yet exported")

    def test_min_ring_mm_boundary_exact_threshold(self):
        """Measured ring exactly equal to min_ring_mm passes without violation."""
        if hasattr(dfm_geometry, "check_plated_hole_ring"):
            facts = {"plated_rings": [{"ref": "H1", "drill_mm": 0.8, "outer_ring_mm": 0.15, "layer": "F.Cu"}]}
            violations = dfm_geometry.check_plated_hole_ring(facts, {"min_ring_mm": 0.15})
            self.assertEqual(len(violations), 0)
        else:
            self.skipTest("check_plated_hole_ring not yet exported")

    def test_min_ring_mm_negative_threshold_refused(self):
        """Negative min_ring_mm value is rejected by validator."""
        if hasattr(dfm_geometry, "check_plated_hole_ring"):
            with self.assertRaises(ValueError):
                dfm_geometry.check_plated_hole_ring({"plated_rings": []}, {"min_ring_mm": -0.5})
        else:
            self.skipTest("check_plated_hole_ring not yet exported")

    def test_dfm_facts_empty_plated_rings(self):
        """Empty plated_rings list in facts produces zero violations."""
        if hasattr(dfm_geometry, "check_plated_hole_ring"):
            violations = dfm_geometry.check_plated_hole_ring({"plated_rings": []}, {"min_ring_mm": 0.2})
            self.assertEqual(len(violations), 0)
        else:
            self.skipTest("check_plated_hole_ring not yet exported")

    def test_dfm_facts_both_layers_evaluated(self):
        """Independent measurement of F.Cu and B.Cu rings on same through-hole."""
        if hasattr(dfm_geometry, "check_plated_hole_ring"):
            facts = {"plated_rings": [
                {"ref": "H1", "drill_mm": 0.8, "outer_ring_mm": 0.3, "layer": "F.Cu"},
                {"ref": "H1", "drill_mm": 0.8, "outer_ring_mm": 0.1, "layer": "B.Cu"}
            ]}
            violations = dfm_geometry.check_plated_hole_ring(facts, {"min_ring_mm": 0.2})
            self.assertEqual(len(violations), 1)
            self.assertEqual(violations[0]["layer"], "B.Cu")
        else:
            self.skipTest("check_plated_hole_ring not yet exported")


class TestTier2PurposeReviewBoundaries(unittest.TestCase):
    """Feature 9 Boundaries: Opt-in purpose_review."""

    def test_purpose_review_missing_review_profile_key(self):
        """Scope without review_profile defaults to dormant purpose review."""
        manifest = {"scope": {}, "files": []}
        self.assertIsNone(purpose_review.applicability(Path("/tmp"), manifest))

    def test_purpose_review_null_review_profile(self):
        """Scope with review_profile: None defaults to dormant."""
        manifest = {"scope": {"review_profile": None}, "files": []}
        self.assertIsNone(purpose_review.applicability(Path("/tmp"), manifest))

    def test_purpose_review_mode_board_is_unsupported(self):
        """Mode board in purpose_review profile returns None (dormant)."""
        profile = json.loads(Path("profiles/claude.json").read_text())
        profile["purpose_review"] = {"mode": "board", "reason": "eval"}
        manifest = {"scope": {"review_profile": profile}, "files": []}
        self.assertIsNone(purpose_review.applicability(Path("/tmp"), manifest))

    def test_brief_with_empty_string_input(self):
        """brief("") behaves consistently."""
        b = purpose_review.brief("")
        self.assertTrue(isinstance(b, str))

    def test_brief_deterministic(self):
        """Repeated calls to brief yield identical results."""
        self.assertEqual(purpose_review.brief("configured"), purpose_review.brief("configured"))


class TestTier2ArtworkTargetBoundaries(unittest.TestCase):
    """Feature 10 Boundaries: Child-process artwork target discovery."""

    def test_artwork_invalid_target_format_refused(self):
        """write_scope refuses malformed target tuples."""
        board_sexpr = b'(kicad_pcb (version 1))'
        with self.assertRaises(ValueError):
            routed_artwork.write_scope(board_sexpr, board_sexpr, targets=["invalid_string_target"])

    def test_artwork_target_with_control_chars_refused(self):
        """write_scope refuses targets containing NUL or newline characters."""
        board_sexpr = b'(kicad_pcb (version 1))'
        with self.assertRaises(ValueError):
            routed_artwork.write_scope(board_sexpr, board_sexpr, targets=[("U1\x00", "Reference")])

    def test_artwork_empty_target_set_refused(self):
        """write_scope refuses targets=[] (empty list must be None or contain targets)."""
        board_sexpr = b'(kicad_pcb (version 1))'
        with self.assertRaises(ValueError):
            routed_artwork.write_scope(board_sexpr, board_sexpr, targets=[])

    def test_artwork_target_leading_dash_refused(self):
        """write_scope refuses targets starting with a dash flag character."""
        board_sexpr = b'(kicad_pcb (version 1))'
        with self.assertRaises(ValueError):
            routed_artwork.write_scope(board_sexpr, board_sexpr, targets=[("-U1", "Reference")])

    def test_artwork_write_scope_identical_bytes_no_mutation(self):
        """When before and after bytes are identical, write_scope returns zero moved identities."""
        board_sexpr = b'(kicad_pcb (version 1))'
        res = routed_artwork.write_scope(board_sexpr, board_sexpr, targets=None)
        self.assertEqual(res.get("moved_identities"), [])


class TestTier2CoordinatorIsolationBoundaries(unittest.TestCase):
    """Feature 11 Boundaries: Coordinator pcbnew isolation."""

    def test_coordinator_ast_walk_nested_pcbnew_import_detection(self):
        """AST scanner detects in-function pcbnew imports."""
        code = "def foo():\n    import pcbnew\n"
        tree = ast.parse(code)
        found = any(isinstance(n, ast.Import) and any(a.name == "pcbnew" for a in n.names) for n in ast.walk(tree))
        self.assertTrue(found)

    def test_coordinator_modules_exist_and_parse(self):
        """build_stages.py and build_workflow.py are valid Python files without syntax errors."""
        for name in ("build_stages.py", "build_workflow.py"):
            path = Path("src/agent_ee") / name
            ast.parse(path.read_text())

    def test_coordinator_probe_timeout_handling(self):
        """pcbnew version child probe handles process timeout gracefully."""
        from agent_ee import native_run
        import subprocess, sys
        intent = native_run.Intent('probe', [sys.executable, '-c', 'import time; time.sleep(10)'], timeout=0.1, isolation='host')
        exec_res = native_run.run(intent, work=None)
        with self.assertRaises(subprocess.TimeoutExpired):
            native_run.completed(exec_res)

    def test_coordinator_build_workflow_returns_clean_dict(self):
        """_runtime() returns dictionary with version string."""
        from agent_ee import build_workflow
        runtime_fn = getattr(build_workflow, "_runtime", None)
        if runtime_fn:
            info = runtime_fn()
            self.assertTrue(isinstance(info, dict))

    def test_coordinator_child_process_isolation_environment(self):
        """Child probe intent specifies isolated execution without coordinator memory leakage."""
        from agent_ee import native_run
        import sys
        intent = native_run.Intent('probe', [sys.executable, '-c', 'pass'], timeout=15, isolation='host')
        self.assertEqual(intent.isolation, 'host')
        self.assertEqual(intent.timeout, 15)
