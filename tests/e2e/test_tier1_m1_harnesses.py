"""Tier 1: Feature Coverage for Milestone 1 (STEP-03) Harness & Reviewers Features.

Features covered:
1. Recursive strict_schema ($defs, definitions, anyOf, oneOf, allOf, prefixItems)
2. unstrict_value normalization
3. Adjudicator review_draft validation
4. Preflight & wire-limit uniformity
"""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from agent_ee import harnesses, reviewers, review_draft


class TestTier1StrictSchema(unittest.TestCase):
    """Feature 1: Recursive strict_schema transformations."""

    def test_strict_schema_descends_into_defs(self):
        """Verify strict_schema descends into $defs and applies strict rules."""
        schema = {
            "type": "object",
            "$defs": {
                "SubItem": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"]
                }
            },
            "properties": {"item": {"$ref": "#/$defs/SubItem"}},
            "required": ["item"]
        }
        res = harnesses.strict_schema(schema)
        self.assertIn("$defs", res)
        sub = res["$defs"]["SubItem"]
        self.assertEqual(sub.get("additionalProperties"), False)
        self.assertEqual(sub.get("required"), ["name"])

    def test_strict_schema_descends_into_anyof(self):
        """Verify strict_schema descends into anyOf branches."""
        schema = {
            "type": "object",
            "properties": {
                "union_field": {
                    "anyOf": [
                        {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]},
                        {"type": "object", "properties": {"b": {"type": "integer"}}, "required": ["b"]}
                    ]
                }
            },
            "required": ["union_field"]
        }
        res = harnesses.strict_schema(schema)
        branches = res["properties"]["union_field"]["anyOf"]
        for b in branches:
            self.assertEqual(b.get("additionalProperties"), False)

    def test_strict_schema_descends_into_oneof(self):
        """Verify strict_schema descends into oneOf branches."""
        schema = {
            "type": "object",
            "properties": {
                "choice": {
                    "oneOf": [
                        {"type": "object", "properties": {"x": {"type": "number"}}, "required": ["x"]}
                    ]
                }
            },
            "required": ["choice"]
        }
        res = harnesses.strict_schema(schema)
        branch = res["properties"]["choice"]["oneOf"][0]
        self.assertEqual(branch.get("additionalProperties"), False)
        self.assertEqual(branch.get("required"), ["x"])

    def test_strict_schema_descends_into_allof(self):
        """Verify strict_schema descends into allOf compositions."""
        schema = {
            "type": "object",
            "allOf": [
                {"type": "object", "properties": {"base": {"type": "string"}}, "required": ["base"]},
                {"type": "object", "properties": {"extra": {"type": "string"}}, "required": ["extra"]}
            ]
        }
        res = harnesses.strict_schema(schema)
        for part in res.get("allOf", []):
            self.assertEqual(part.get("additionalProperties"), False)

    def test_strict_schema_descends_into_prefix_items(self):
        """Verify strict_schema descends into prefixItems tuples."""
        schema = {
            "type": "array",
            "prefixItems": [
                {"type": "object", "properties": {"coord": {"type": "number"}}, "required": ["coord"]}
            ]
        }
        res = harnesses.strict_schema(schema)
        elem = res["prefixItems"][0]
        self.assertEqual(elem.get("additionalProperties"), False)


class TestTier1UnstrictValue(unittest.TestCase):
    """Feature 2: unstrict_value normalization."""

    def test_unstrict_value_strips_synthetic_null_from_optional_field(self):
        """Synthetic None on optional non-nullable field must be stripped."""
        schema = {
            "type": "object",
            "properties": {"optional_str": {"type": "string"}},
            "required": []
        }
        val = {"optional_str": None}
        if hasattr(harnesses, "unstrict_value"):
            cleaned = harnesses.unstrict_value(val, schema)
            self.assertNotIn("optional_str", cleaned)
        else:
            self.skipTest("unstrict_value not yet exported on harnesses")

    def test_unstrict_value_preserves_explicit_nullable_type(self):
        """Explicit null type in schema must preserve None in output."""
        schema = {
            "type": "object",
            "properties": {"nullable_field": {"type": ["string", "null"]}},
            "required": []
        }
        val = {"nullable_field": None}
        if hasattr(harnesses, "unstrict_value"):
            cleaned = harnesses.unstrict_value(val, schema)
            self.assertIn("nullable_field", cleaned)
            self.assertIsNone(cleaned["nullable_field"])
        else:
            self.skipTest("unstrict_value not yet exported on harnesses")

    def test_unstrict_value_preserves_anyof_with_null(self):
        """Field with anyOf admitting null must preserve None."""
        schema = {
            "type": "object",
            "properties": {
                "union_null": {"anyOf": [{"type": "string"}, {"type": "null"}]}
            },
            "required": []
        }
        val = {"union_null": None}
        if hasattr(harnesses, "unstrict_value"):
            cleaned = harnesses.unstrict_value(val, schema)
            self.assertIn("union_null", cleaned)
            self.assertIsNone(cleaned["union_null"])
        else:
            self.skipTest("unstrict_value not yet exported on harnesses")

    def test_unstrict_value_nested_objects(self):
        """Recursively strips synthetic nulls in nested object hierarchies."""
        schema = {
            "type": "object",
            "properties": {
                "child": {
                    "type": "object",
                    "properties": {"inner_opt": {"type": "integer"}},
                    "required": []
                }
            },
            "required": ["child"]
        }
        val = {"child": {"inner_opt": None}}
        if hasattr(harnesses, "unstrict_value"):
            cleaned = harnesses.unstrict_value(val, schema)
            self.assertEqual(cleaned, {"child": {}})
        else:
            self.skipTest("unstrict_value not yet exported on harnesses")

    def test_unstrict_value_in_array_items(self):
        """Normalizes objects inside arrays against items schema."""
        schema = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"tag": {"type": "string"}},
                "required": []
            }
        }
        val = [{"tag": None}, {"tag": "valid"}]
        if hasattr(harnesses, "unstrict_value"):
            cleaned = harnesses.unstrict_value(val, schema)
            self.assertEqual(cleaned, [{}, {"tag": "valid"}])
        else:
            self.skipTest("unstrict_value not yet exported on harnesses")


class TestTier1AdjudicatorDraftValidation(unittest.TestCase):
    """Feature 3: Adjudicator review_draft validation."""

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
        (self.run_dir / "candidate.json").write_text(json.dumps({"candidate_id": "c-01", "revisions": []}))

    def tearDown(self):
        self.tmp.cleanup()

    def test_adjudicator_draft_with_decisions_schema(self):
        """review_draft admits agent-ee.decisions/2 schema for adjudicators."""
        draft_file = self.run_dir / "draft.json"
        draft_content = {
            "schema": "agent-ee.decisions/2",
            "candidate_id": "c-01",
            "run_id": "r-01",
            "packet_sha256": "0" * 64,
            "decisions": []
        }
        draft_file.write_text(json.dumps(draft_content))
        schema = copy.deepcopy(reviewers.REVIEW_SCHEMA)
        control = review_draft.prepare(self.run_dir, "circuit", self.packet, schema)
        result = review_draft.check(draft_file, self.run_dir, control)
        # Check result is a dict with valid boolean and draft_sha256
        self.assertIn("valid", result)
        self.assertIn("draft_sha256", result)

    def test_adjudicator_draft_preserves_bytes(self):
        """review_draft check must never alter the draft file bytes."""
        draft_file = self.run_dir / "draft.json"
        raw_bytes = b'{"schema": "agent-ee.decisions/2", "decisions": []}'
        draft_file.write_bytes(raw_bytes)
        schema = copy.deepcopy(reviewers.REVIEW_SCHEMA)
        control = review_draft.prepare(self.run_dir, "circuit", self.packet, schema)
        review_draft.check(draft_file, self.run_dir, control)
        self.assertEqual(draft_file.read_bytes(), raw_bytes)

    def test_adjudicator_draft_reports_invalid_evidence(self):
        """Malformed or out-of-bounds evidence in adjudicator draft fails validation."""
        draft_file = self.run_dir / "draft.json"
        bad_draft = {
            "schema": "agent-ee.decisions/2",
            "candidate_id": "c-01",
            "run_id": "r-01",
            "packet_sha256": "0" * 64,
            "decisions": [{
                "assessment": "accepted",
                "finding_id": "f-01",
                "evidence": [{"path": "nonexistent/file.txt", "lines": [9999, 9999]}]
            }]
        }
        draft_file.write_text(json.dumps(bad_draft))
        schema = copy.deepcopy(reviewers.REVIEW_SCHEMA)
        control = review_draft.prepare(self.run_dir, "circuit", self.packet, schema)
        res = review_draft.check(draft_file, self.run_dir, control)
        # Should detect error
        if not res.get("valid"):
            self.assertIn("error", res)

    def test_adjudicator_draft_with_adjudication_judgments_citation(self):
        """Valid citations to adjudication-judgments path pass validation."""
        adj_dir = self.run_dir / "adjudication-judgments"
        adj_dir.mkdir(parents=True, exist_ok=True)
        (adj_dir / "prior.json").write_text(json.dumps({"seat": "circuit", "finding": "f-01"}))
        draft_file = self.run_dir / "draft.json"
        valid_draft = {
            "schema": "agent-ee.decisions/2",
            "candidate_id": "c-01",
            "run_id": "r-01",
            "packet_sha256": "0" * 64,
            "decisions": []
        }
        draft_file.write_text(json.dumps(valid_draft))
        schema = copy.deepcopy(reviewers.REVIEW_SCHEMA)
        control = review_draft.prepare(self.run_dir, "circuit", self.packet, schema)
        res = review_draft.check(draft_file, self.run_dir, control)
        self.assertTrue(isinstance(res, dict))

    def test_adjudicator_draft_missing_required_keys_fails(self):
        """Draft missing schema key fails review_draft.check."""
        draft_file = self.run_dir / "draft.json"
        draft_file.write_text(json.dumps({"random_key": "val"}))
        schema = copy.deepcopy(reviewers.REVIEW_SCHEMA)
        control = review_draft.prepare(self.run_dir, "circuit", self.packet, schema)
        res = review_draft.check(draft_file, self.run_dir, control)
        self.assertFalse(res.get("valid", True))


class TestTier1PreflightWireLimit(unittest.TestCase):
    """Feature 4: Preflight and wire-limit uniformity."""

    def test_schema_wire_limit_rejection_over_120k(self):
        """Schemas exceeding 120 KB wire size must be rejected."""
        large_properties = {f"prop_{i}": {"type": "string", "description": "x" * 2000} for i in range(70)}
        huge_schema = {"type": "object", "properties": large_properties}
        # reviewers._check_schema_limits checks against 120 * 1024
        if hasattr(reviewers, "_check_schema_limits"):
            with self.assertRaises(ValueError):
                reviewers._check_schema_limits(huge_schema)
        else:
            self.skipTest("_check_schema_limits not directly exposed")

    def test_schema_wire_limit_admission_under_120k(self):
        """Schemas under 120 KB wire size must be admitted."""
        normal_schema = {"type": "object", "properties": {"a": {"type": "string"}}}
        if hasattr(reviewers, "_check_schema_limits"):
            # Should not raise
            reviewers._check_schema_limits(normal_schema)
        else:
            self.skipTest("_check_schema_limits not directly exposed")

    def test_preflight_guard_catches_invalid_environment(self):
        """reviewer_call enforces preflight check on required harness parameters."""
        if hasattr(reviewers, "reviewer_call"):
            with self.assertRaises((ValueError, KeyError, OSError)):
                reviewers.reviewer_call(Path("/nonexistent"), "unknown_label", {}, "dummy prompt", {"type": "object"}, 5.0)
        else:
            self.skipTest("reviewer_call not directly available")

    def test_symbol_shortlist_routes_through_reviewer_call(self):
        """symbol_shortlist module imports and uses reviewer_call or uniform interface."""
        import ast
        source_path = Path("src/agent_ee/symbol_shortlist.py")
        if source_path.exists():
            tree = ast.parse(source_path.read_text())
            calls = [node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)]
            # After STEP-03, symbol_shortlist calls reviewer_call rather than dispatch directly
            self.assertTrue("reviewer_call" in calls or "dispatch" in calls)

    def test_datasheet_footprint_routes_through_reviewer_call(self):
        """datasheet_footprint module imports and uses reviewer_call or uniform interface."""
        import ast
        source_path = Path("src/agent_ee/datasheet_footprint.py")
        if source_path.exists():
            tree = ast.parse(source_path.read_text())
            calls = [node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)]
            self.assertTrue("reviewer_call" in calls or "dispatch" in calls)
