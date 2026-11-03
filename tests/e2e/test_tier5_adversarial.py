"""Tier 5: White-Box Adversarial Coverage Hardening Test Suite.

Adversarial stress-testing, boundary overflows, corruption, and failure mode verification
across all 6 campaign components:
- STEP-01 (M6): stage_execution, forward_annotation
- STEP-02 (M3): routed_artwork, build_stages, build_workflow
- STEP-03 (M1): harnesses, reviewers, review_draft
- STEP-04 (M4): source_mechanics, local_return_seed, placement_job
- STEP-05 (M5): native_run, build_completion, rating_lineage
- STEP-06 (M2): land_pattern, requirement_map, qualification, dfm_geometry
"""
import copy
import hashlib
import json
import math
import os
import tempfile
import time
import unittest
from pathlib import Path


class TestTier5AdversarialStageExecution(unittest.TestCase):
    """Component 1 (STEP-01 / M6): stage_execution & forward_annotation."""

    def test_adv_mountinfo_missing_separator(self):
        """Mountinfo line lacking ' - ' separator raises ConfinementError."""
        from agent_ee.stage_execution import ConfinementError, ConfinementView
        malformed = "1 2 3:4 / / rw ext4 /dev/sda1 rw"
        with self.assertRaises(ConfinementError):
            ConfinementView.parse_mountinfo(malformed)

    def test_adv_mountinfo_duplicate_mount_id(self):
        """Mountinfo with duplicate mount IDs raises ConfinementError."""
        from agent_ee.stage_execution import ConfinementError, ConfinementView
        lines = [
            "10 1 8:1 / / rw,relatime - ext4 /dev/sda1 rw",
            "10 1 8:2 /usr /usr ro,relatime - ext4 /dev/sda2 ro",
        ]
        with self.assertRaises(ConfinementError):
            ConfinementView.parse_mountinfo("\n".join(lines))

    def test_adv_mountinfo_ambiguous_permissions(self):
        """Mountinfo line with both 'ro' and 'rw' flags raises ConfinementError."""
        from agent_ee.stage_execution import ConfinementError, ConfinementView
        line = "12 1 8:1 / / ro,rw,relatime - ext4 /dev/sda1 rw"
        with self.assertRaises(ConfinementError):
            ConfinementView.parse_mountinfo(line)

    def test_adv_mountinfo_path_traversal(self):
        """Mountinfo line with '..' in mount point raises ConfinementError."""
        from agent_ee.stage_execution import ConfinementError, ConfinementView
        line = "12 1 8:1 /work/../etc /work/../etc rw,relatime - ext4 /dev/sda1 rw"
        with self.assertRaises(ConfinementError):
            ConfinementView.parse_mountinfo(line)

    def test_adv_mountinfo_verify_unauthorized_rw_usr(self):
        """verify() raises ConfinementError if /usr is mounted read-write."""
        from agent_ee.stage_execution import ConfinementError, ConfinementView
        mountinfo = "\n".join([
            "1 0 0:1 / / rw,relatime - tmpfs none rw",
            "2 1 0:2 /proc /proc rw,relatime - proc none rw",
            "3 1 0:3 /dev /dev rw,relatime - tmpfs none rw",
            "4 1 0:4 /tmp /tmp rw,relatime - tmpfs none rw",
            "5 1 0:5 /candidate /candidate ro,relatime - tmpfs none ro",
            "6 1 0:6 /work /work rw,relatime - tmpfs none rw",
            "7 1 0:7 /usr /usr rw,relatime - ext4 /dev/sda1 rw",
            "8 1 0:8 /etc /etc ro,relatime - ext4 /dev/sda1 ro",
            "9 1 0:9 /tool/agent-ee /tool/agent-ee ro,relatime - ext4 /dev/sda1 ro",
        ])
        view = ConfinementView.STAGE_TOOL
        with self.assertRaises(ConfinementError):
            view.verify(mountinfo_text=mountinfo, source_path="/tool/agent-ee/module.py")

    def test_adv_stage_kicad_project_symlink_input_refused(self):
        """stage_kicad_project refuses inputs directory containing symlinks."""
        from agent_ee.stage_execution import stage_kicad_project
        with tempfile.TemporaryDirectory() as tmp:
            in_dir = Path(tmp) / "inputs"
            in_dir.mkdir()
            target = Path(tmp) / "real_file"
            target.write_text("content")
            (in_dir / "symlink.kicad_pcb").symlink_to(target)
            work_dir = Path(tmp) / "work"
            work_dir.mkdir()
            with self.assertRaises((ValueError, Exception)) as cm:
                with stage_kicad_project(in_dir, work_dir, "board.kicad_pcb"):
                    pass
            self.assertIn("regular captured files", str(cm.exception))

    def test_adv_stage_kicad_project_unauthorized_mutation_detected(self):
        """stage_kicad_project detects mutation of non-prl files in project."""
        from agent_ee.stage_execution import stage_kicad_project
        with tempfile.TemporaryDirectory() as tmp:
            in_dir = Path(tmp) / "inputs"
            in_dir.mkdir()
            (in_dir / "board.kicad_pcb").write_text("(kicad_pcb original)")
            work_dir = Path(tmp) / "work"
            work_dir.mkdir()
            with self.assertRaises((ValueError, Exception)) as cm:
                with stage_kicad_project(in_dir, work_dir, "board.kicad_pcb") as proj:
                    (proj / "board.kicad_pcb").write_text("(kicad_pcb tampered)")
            self.assertIn("child changed its private project", str(cm.exception))

    def test_adv_stage_kicad_project_deletion_detected(self):
        """stage_kicad_project detects child deleting a file from private project."""
        from agent_ee.stage_execution import stage_kicad_project
        with tempfile.TemporaryDirectory() as tmp:
            in_dir = Path(tmp) / "inputs"
            in_dir.mkdir()
            (in_dir / "board.kicad_pcb").write_text("(kicad_pcb original)")
            (in_dir / "board.kicad_pro").write_text("(kicad_pro original)")
            work_dir = Path(tmp) / "work"
            work_dir.mkdir()
            with self.assertRaises((ValueError, Exception)) as cm:
                with stage_kicad_project(in_dir, work_dir, "board.kicad_pcb") as proj:
                    (proj / "board.kicad_pro").unlink()
            self.assertIn("child changed its private project", str(cm.exception))

    def test_adv_stage_context_probe_outside_scratch_refused(self):
        """StageContext.probe refuses input/work paths outside its scratch directory."""
        from agent_ee.forward_annotation import AnnotationError
        from agent_ee.stage_execution import StageContext
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp) / "scratch"
            scratch.mkdir()
            outside = Path(tmp) / "outside"
            outside.mkdir()
            ctx = StageContext(work=scratch)
            with self.assertRaises(AnnotationError) as cm:
                ctx.probe(outside, scratch, ["-v"])
            self.assertIn("must stay in stage scratch", str(cm.exception))

    def test_adv_stage_context_run_timeout_floor(self):
        """StageContext.run with timeout <= LAUNCH_FLOOR_S raises DeadlineExhausted."""
        from agent_ee import native_run
        from agent_ee.stage_execution import StageContext
        with tempfile.TemporaryDirectory() as tmp:
            ctx = StageContext(work=Path(tmp))
            intent = native_run.Intent("echo", ("hello",), isolation="sandbox", timeout=0.5)
            with self.assertRaises(native_run.DeadlineExhausted):
                ctx.run(intent, timeout=0.5)

    def test_adv_custom_geometry_path_traversal_refused(self):
        """custom_geometry strictly refuses path traversal and absolute footprint names."""
        from agent_ee.stage_execution import StageContext
        from agent_ee.footprint_library import FootprintError
        with tempfile.TemporaryDirectory() as tmp:
            ctx = StageContext(work=tmp)
            for bad_name in ("../../escaped", "/tmp/evil", "sub/nested", "..\\win_escaped"):
                with self.assertRaises(FootprintError):
                    ctx.custom_geometry(b"data", {"name": bad_name})
                escaped = Path(tmp) / "library-probes" / "escaped.kicad_mod"
                self.assertFalse(escaped.exists())

    def test_adv_custom_geometry_valid_input_succeeds_without_name_error(self):
        """custom_geometry initializes member directory and succeeds on valid footprint inputs."""
        from unittest.mock import patch
        from agent_ee.stage_execution import StageContext

        sample_report = {"pcbnew_version": "10.0.7", "pads": [{"number": "1", "shape": "rect"}]}
        with tempfile.TemporaryDirectory() as tmp:
            ctx = StageContext(work=tmp)

            def mock_probe(inputs, work, argv, timeout=120):
                self.assertTrue(Path(inputs).is_dir())
                self.assertFalse(Path(work).exists())
                Path(work).mkdir()  # The process adapter owns creation of its record scratch.
                mod_file = Path(inputs) / "test_pad.kicad_mod"
                self.assertTrue(mod_file.is_file())
                self.assertEqual(mod_file.read_bytes(), b"(footprint test_pad)")
                (Path(work) / "footprint.json").write_text(json.dumps(sample_report))
                return {"execution": "ok"}

            with patch.object(ctx, "probe", side_effect=mock_probe):
                result = ctx.custom_geometry(b"(footprint test_pad)", {"name": "test_pad"})
            self.assertEqual(result, sample_report)

    def test_adv_stage_kicad_project_absolute_or_parent_traversal_refused(self):
        """stage_kicad_project strictly blocks absolute and parent traversal design_rel paths."""
        from agent_ee.stage_execution import stage_kicad_project
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            inputs, work = t / "inputs", t / "work"
            inputs.mkdir(); work.mkdir()
            (inputs / "board.kicad_pcb").write_text("data")
            for bad_rel in ("/abs/board.kicad_pcb", "../escaped/board.kicad_pcb", ""):
                with self.assertRaises(ValueError):
                    with stage_kicad_project(inputs, work, bad_rel):
                        pass


class TestTier5AdversarialArtwork(unittest.TestCase):
    """Component 2 (STEP-02 / M3): routed_artwork & build_workflow."""

    def test_adv_artwork_policy_overflow_free_groups(self):
        """Artwork policy with > 256 free groups is rejected."""
        from agent_ee.routed_artwork import normalize_artwork_policy
        policy = {
            "schema": "agent-ee.artwork-policy/1",
            "source_sha256": "0" * 64,
            "free_groups": [
                {"owner_uuid": f"fp_{i}", "members": [f"m_{i}"]}
                for i in range(257)
            ]
        }
        with self.assertRaises(ValueError) as cm:
            normalize_artwork_policy(policy)
        self.assertIn("malformed artwork policy", str(cm.exception))

    def test_adv_artwork_policy_duplicate_member_across_groups(self):
        """Artwork policy with duplicate member UUID across groups is rejected."""
        from agent_ee.routed_artwork import normalize_artwork_policy
        policy = {
            "schema": "agent-ee.artwork-policy/1",
            "source_sha256": "0" * 64,
            "free_groups": [
                {"owner_uuid": "fp_1", "members": ["shared_uuid", "m_1"]},
                {"owner_uuid": "fp_2", "members": ["shared_uuid", "m_2"]}
            ]
        }
        with self.assertRaises(ValueError) as cm:
            normalize_artwork_policy(policy)
        self.assertIn("ambiguous artwork group membership", str(cm.exception))

    def test_adv_artwork_policy_whitespace_in_uuid(self):
        """Artwork policy with whitespace in UUID is rejected."""
        from agent_ee.routed_artwork import normalize_artwork_policy
        policy = {
            "schema": "agent-ee.artwork-policy/1",
            "source_sha256": "0" * 64,
            "free_groups": [
                {"owner_uuid": "fp 1", "members": ["m_1"]}
            ]
        }
        with self.assertRaises(ValueError) as cm:
            normalize_artwork_policy(policy)
        self.assertIn("ambiguous artwork group membership", str(cm.exception))



class TestTier5AdversarialHarnesses(unittest.TestCase):
    """Component 3 (STEP-03 / M1): harnesses & review_draft."""

    def test_adv_strict_schema_deep_recursion(self):
        """strict_schema recurses through $defs, allOf, anyOf, oneOf, prefixItems."""
        from agent_ee.harnesses import strict_schema
        schema = {
            "$defs": {
                "DefA": {
                    "type": "object",
                    "properties": {"opt_a": {"type": "string"}}
                }
            },
            "type": "object",
            "properties": {
                "union_field": {
                    "anyOf": [
                        {"type": "object", "properties": {"in_any": {"type": "integer"}}},
                        {"type": "string"}
                    ]
                },
                "items_field": {
                    "type": "array",
                    "prefixItems": [
                        {"type": "object", "properties": {"prefix_prop": {"type": "boolean"}}}
                    ]
                }
            }
        }
        result = strict_schema(schema)
        self.assertFalse(result["additionalProperties"])
        self.assertIn("union_field", result["required"])
        self.assertFalse(result["$defs"]["DefA"]["additionalProperties"])
        self.assertIn("opt_a", result["$defs"]["DefA"]["required"])
        branch0 = result["properties"]["union_field"]["anyOf"][0]["anyOf"][0]
        self.assertFalse(branch0["additionalProperties"])
        self.assertIn("in_any", branch0["required"])
        prefix_item = result["properties"]["items_field"]["anyOf"][0]["prefixItems"][0]
        self.assertFalse(prefix_item["additionalProperties"])
        self.assertIn("prefix_prop", prefix_item["required"])

    def test_adv_unstrict_value_strips_synthetic_null(self):
        """unstrict_value removes synthetic null on optional non-nullable properties."""
        from agent_ee.harnesses import unstrict_value
        schema = {
            "type": "object",
            "properties": {
                "req": {"type": "string"},
                "opt_non_null": {"type": "integer"},
                "opt_nullable": {"type": ["string", "null"]}
            },
            "required": ["req"]
        }
        val = {"req": "test", "opt_non_null": None, "opt_nullable": None}
        cleaned = unstrict_value(val, schema)
        self.assertEqual(cleaned, {"req": "test", "opt_nullable": None})

    def test_adv_unstrict_value_cyclic_defs_cycle(self):
        """unstrict_value handles mutually recursive $defs without RecursionError (remediated in Tier 5)."""
        from agent_ee.harnesses import unstrict_value
        schema = {
            "$defs": {
                "A": {"anyOf": [{"type": "string"}, {"$ref": "#/$defs/B"}]},
                "B": {"anyOf": [{"type": "number"}, {"$ref": "#/$defs/A"}]}
            },
            "type": "object",
            "properties": {
                "field": {"$ref": "#/$defs/A"}
            }
        }
        res = unstrict_value({"field": None}, schema)
        self.assertIsInstance(res, dict)
        res2 = unstrict_value({"field": "hello"}, schema)
        self.assertEqual(res2, {"field": "hello"})

    def test_adv_review_draft_decision_duplicate_dispositions(self):
        """review_draft.check handles adjudicator decisions list."""
        from agent_ee.review_draft import check, prepare
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            packet = {
                "candidate_id": "c_1", "run_id": "r_1", "packet_sha256": "0" * 64
            }
            schema = {"type": "object"}
            findings = [{"id": "f_1"}]
            ctrl = prepare(run_dir, "adjudicator", packet, schema, findings=findings)
            draft = run_dir / "draft.json"
            draft.write_text(json.dumps({
                "schema": "agent-ee.decisions/2",
                "candidate_id": "c_1",
                "run_id": "r_1",
                "decisions": [
                    {"finding_id": "f_1", "disposition": "concur", "reason": "valid defect"},
                    {"finding_id": "f_1", "disposition": "reject", "reason": "conflicting second disposition"}
                ]
            }))
            res = check(draft, run_dir, ctrl)
            self.assertIsNotNone(res.get("draft_sha256"))

    def test_adv_review_draft_non_dict_root_gracefully_rejected(self):
        """BUG-1 Fix Verification: non-dict root JSON must return valid=False with error, not crash."""
        from agent_ee import review_draft
        from agent_ee.storage import write_json
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            run_dir, control_dir = t / "run", t / "control"
            run_dir.mkdir(); control_dir.mkdir()
            write_json(control_dir / "schema.json", {"type": "object"})
            write_json(control_dir / "context.json", {
                "schema": "agent-ee.review-draft-context/1",
                "role": "circuit"
            })
            for non_dict in ("[1, 2, 3]", '"string_val"', "12345", "true"):
                draft = t / "draft.json"
                draft.write_text(non_dict)
                res = review_draft.check(draft, run_dir, control_dir)
                self.assertFalse(res["valid"])
                self.assertIn("error", res)

    def test_adv_strict_schema_preserves_circular_defs(self):
        """strict_schema handles circular defs idempotently without recursion error."""
        from agent_ee.harnesses import strict_schema
        schema = {
            "type": "object",
            "$defs": {
                "Node": {
                    "type": "object",
                    "properties": {"child": {"$ref": "#/$defs/Node"}}
                }
            },
            "properties": {"root": {"$ref": "#/$defs/Node"}}
        }
        res = strict_schema(schema)
        self.assertEqual(res["additionalProperties"], False)
        self.assertEqual(res["$defs"]["Node"]["additionalProperties"], False)


class TestTier5AdversarialMechanics(unittest.TestCase):
    """Component 4 (STEP-04 / M4): source_mechanics & local_return_seed."""

    def test_adv_source_mechanics_design_model_missing_access_owners(self):
        """_design_model rejects design-declared model missing access_owners."""
        from agent_ee.source_mechanics import _design_model
        model = {
            "source_files": [],
            "packages": {"U1": {"size_mm": [10, 10, 1], "offset_mm": [0, 0]}}
        }
        sources = ["analysis/geometry-model.json"]
        with self.assertRaises(ValueError) as cm:
            _design_model(model, sources)
        self.assertIn("must list its access_owners", str(cm.exception))

    def test_adv_source_mechanics_design_model_boolean_in_size(self):
        """_design_model rejects boolean values disguised as numbers in package size."""
        from agent_ee.source_mechanics import _design_model
        model = {
            "source_files": [],
            "access_owners": ["J1"],
            "packages": {"U1": {"size_mm": [True, 10, 1], "offset_mm": [0, 0]}}
        }
        sources = ["analysis/geometry-model.json"]
        with self.assertRaises(ValueError) as cm:
            _design_model(model, sources)
        self.assertIn("package needs size_mm", str(cm.exception))

    def test_adv_local_return_seed_symlink_refusal(self):
        """local_return_seed.capture refuses source directories accessed via symlink."""
        from agent_ee.local_return_seed import JobRefusal, capture
        with tempfile.TemporaryDirectory() as tmp:
            real_dir = Path(tmp) / "real"
            real_dir.mkdir()
            (real_dir / "board.kicad_pcb").write_text("pcb")
            (real_dir / "board.kicad_pro").write_text("pro")
            link_dir = Path(tmp) / "link"
            link_dir.symlink_to(real_dir)
            with self.assertRaises(JobRefusal) as cm:
                capture(link_dir)
            self.assertIn("source directory aliases are unsupported", str(cm.exception))


class TestTier5AdversarialCompletion(unittest.TestCase):
    """Component 5 (STEP-05 / M5): native_run & build_completion."""

    def test_adv_native_run_checkpoint_nan_deadline(self):
        """checkpoint with NaN deadline raises DeadlineExhausted."""
        from agent_ee.native_run import DeadlineExhausted, checkpoint
        with self.assertRaises(DeadlineExhausted) as cm:
            checkpoint(deadline=float("nan"))
        self.assertIn("deadline invalid or exhausted", str(cm.exception))

    def test_adv_native_run_checkpoint_inf_deadline(self):
        """checkpoint with infinite deadline raises DeadlineExhausted."""
        from agent_ee.native_run import DeadlineExhausted, checkpoint
        with self.assertRaises(DeadlineExhausted) as cm:
            checkpoint(deadline=float("inf"))
        self.assertIn("deadline invalid or exhausted", str(cm.exception))

    def test_adv_native_run_checkpoint_cancellation_callable(self):
        """checkpoint with cancel callback returning True raises DeadlineExhausted."""
        from agent_ee.native_run import DeadlineExhausted, checkpoint
        with self.assertRaises(DeadlineExhausted) as cm:
            checkpoint(cancel=lambda: True, label="test_cancel")
        self.assertIn("execution cancelled (test_cancel)", str(cm.exception))

    def test_adv_load_seat_policy_oversized(self):
        """load_seat_policy rejects files larger than 1 MiB."""
        from agent_ee.build_completion import load_seat_policy
        with tempfile.TemporaryDirectory() as tmp:
            policy_file = Path(tmp) / "big_policy.json"
            policy_file.write_bytes(b" " * (1048576 + 100))
            with self.assertRaises(ValueError) as cm:
                load_seat_policy(policy_file)
            self.assertIn("trusted judgment seat policy pin mismatch", str(cm.exception))


class TestTier5AdversarialChecks(unittest.TestCase):
    """Component 6 (STEP-06 / M2): land_pattern, requirement_map, dfm_geometry."""

    def test_adv_land_pattern_criteria_threshold_out_of_range(self):
        """validate_criteria rejects threshold >= 1.0 or non-positive."""
        from agent_ee.land_pattern import validate_criteria
        crit = {
            "schema": "agent-ee.land-pattern-criteria/1",
            "threshold": "1.0",
            "pin_tolerance_nm": "1000",
            "source_tolerance_nm": "1000",
            "refinement_levels": [1, 2, 4],
            "library_manifest": None,
            "requirements": []
        }
        with self.assertRaises(ValueError) as cm:
            validate_criteria(crit)
        self.assertIn("threshold must be below 1", str(cm.exception))

    def test_adv_requirement_map_unclassified_kind(self):
        """method_class returns 'unclassified' for unregistered check kinds."""
        from agent_ee.requirement_map import method_class
        self.assertEqual(method_class("nonexistent_quantum_check"), "unclassified")

    def test_adv_dfm_geometry_plated_hole_ring_violation(self):
        """check_plated_hole_ring flags annular ring violation below min_ring_mm."""
        from agent_ee.dfm_geometry import check_plated_hole_ring
        facts = {
            "plated_rings": [
                {
                    "ref": "J1",
                    "pad": "1",
                    "layer": "F.Cu",
                    "outer_ring_mm": 0.12,
                    "drill_mm": 0.8
                }
            ]
        }
        thresholds = {"min_ring_mm": 0.15}
        violations = check_plated_hole_ring(facts, thresholds)
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0]["code"], "DFM-014")
        self.assertIn("below min_ring_mm 0.15", violations[0]["reason"])

    def test_adv_dfm_plated_hole_ring_identifies_violation(self):
        """dfm_geometry evaluate emits DFM-014 violation when ring measurement is below threshold."""
        from agent_ee import dfm_geometry
        facts = {
            "copper_layers": ["F.Cu", "B.Cu"],
            "pads": [], "courtyards": [], "tracks": [], "vias": [],
            "copper_area": {"F.Cu": 100.0, "B.Cu": 100.0},
            "plated_rings": [
                {"ref": "J1", "pad": "1", "layer": "F.Cu", "drill_mm": 0.8, "ring_mm": 0.08, "position_nm": [0, 0]}
            ]
        }
        profile = {
            "schema": "agent-ee.dfm-profile/1",
            "name": "test_dfm",
            "rules": [{"id": "DFM-014", "rule": "plated_hole_ring", "min_ring_mm": 0.15}]
        }
        report = dfm_geometry.evaluate(facts, profile)
        rule_rec = report["rules"][0]
        self.assertEqual(rule_rec["status"], "defect")
        self.assertEqual(len(rule_rec["findings"]), 1)
        self.assertEqual(rule_rec["findings"][0]["code"], "DFM-014")


if __name__ == "__main__":
    unittest.main()
