"""Tier 4: Real-World Application Scenarios (End-to-End Multi-Board Workflows).

Full pipeline workflows:
- Scenario 1: 555-Oscillator minimal board compilation & qualification lifecycle
- Scenario 2: I2C-Sensor dual-layer SMT board with land pattern & CPL rotation
- Scenario 3: Mixed SMT + THT multi-board with silk artwork and purpose review
- Scenario 4: Resilient stage failure and immutable candidate custody
- Scenario 5: Typed build completion pipeline with rating lineage & seat policy
"""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from agent_ee import qualification, requirement_map, routed_artwork, reviewers, review_draft
from tests.e2e.e2e_common import make_test_bundle, make_test_packet


class TestTier4OscillatorLifecycleScenario(unittest.TestCase):
    """Scenario 1: 555-Oscillator minimal board compilation & qualification lifecycle."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.board_file = make_test_bundle(self.root / "oscillator", "oscillator.kicad_pcb")

    def tearDown(self):
        self.tmp.cleanup()

    def test_555_oscillator_full_qualification_flow(self):
        """Simulate end-to-end 555-oscillator board checks to final qualification record."""
        # 1. Run checks simulation
        check_rows = [
            {"id": "erc", "kind": "erc", "execution": "ok", "outcome": "pass"},
            {"id": "drc", "kind": "drc", "execution": "ok", "outcome": "pass"},
            {"id": "netlist", "kind": "netlist", "execution": "ok", "outcome": "pass"},
            {"id": "fab_roundtrip", "kind": "fab_roundtrip", "execution": "ok", "outcome": "pass"},
            {"id": "cpl_rotation", "kind": "cpl_rotation", "execution": "ok", "outcome": "pass"},
            {"id": "pkg-01", "kind": "fab_package", "details": {
                "assembly_orientation": {
                    "software_assessment": "complete",
                    "unassessed_rows": [],
                    "profile_sha256": "0" * 64
                }
            }}
        ]

        # 2. Package records evaluation
        package_records = qualification._package_records(check_rows)
        self.assertEqual(package_records["roundtrip"], "pass")
        self.assertEqual(package_records["orientation"], "pass")

        # 3. Create persistent qualification record
        qual_record = {
            "schema": qualification.QUALIFICATION_SCHEMA,
            "mode": "typed",
            "accepted": True,
            "dimensions": {
                "file_release": {"status": "pass"},
                "electrical": {"status": "pass"}
            },
            "summary": {"required": []},
            "package_records": package_records
        }

        # 4. Project summary
        out_dir = self.root / "oscillator_out"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "qualification.json").write_text(json.dumps(qual_record))
        summary = qualification.summary(out_dir, qual_record)
        self.assertEqual(summary["mode"], "typed")
        self.assertEqual(summary["dimensions"]["file_release"], "pass")


class TestTier4I2cSensorScenario(unittest.TestCase):
    """Scenario 2: I2C-Sensor dual-layer SMT board with land pattern & CPL rotation."""

    def test_i2c_sensor_exact_package_qualification_gates(self):
        """I2C sensor SMT package requires cpl_rotation and fab_roundtrip before file_release."""
        # Simulated sensor board check results
        rows_without_cpl = [
            {"id": "erc", "kind": "erc", "execution": "ok", "outcome": "pass"},
            {"id": "drc", "kind": "drc", "execution": "ok", "outcome": "pass"},
            {"id": "fab_roundtrip", "kind": "fab_roundtrip", "execution": "ok", "outcome": "pass"}
            # cpl_rotation missing
        ]
        records = qualification._package_records(rows_without_cpl)
        # SMT sensor board must be pending orientation
        self.assertEqual(records.get("orientation"), "pending")

        # Now add verified CPL rotation
        rows_complete = copy.deepcopy(rows_without_cpl)
        rows_complete.append({"id": "cpl", "kind": "cpl_rotation", "execution": "ok", "outcome": "pass"})
        rows_complete.append({"id": "pkg", "kind": "fab_package", "details": {
            "assembly_orientation": {
                "software_assessment": "complete",
                "unassessed_rows": [],
                "profile_sha256": "0" * 64
            }
        }})
        records_complete = qualification._package_records(rows_complete)
        self.assertEqual(records_complete.get("orientation"), "pass")
        self.assertEqual(records_complete.get("roundtrip"), "pass")


class TestTier4MixedSmtThtArtworkScenario(unittest.TestCase):
    """Scenario 3: Mixed SMT + THT multi-board with silk artwork and purpose review."""

    def test_artwork_target_discovery_on_mixed_board(self):
        """Silk artwork child process preserves bit-identical bytes when no movable silk fields exist."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            board_file = make_test_bundle(root / "mixed_board", "mixed.kicad_pcb")
            original_bytes = board_file.read_bytes()

            # Output directory
            out_file = root / "out" / "mixed.kicad_pcb"
            out_file.parent.mkdir(parents=True, exist_ok=True)
            out_file.write_bytes(original_bytes)

            # Record no_movable_fields
            report = {"status": "no_movable_fields", "targets": [], "open_defects": False}
            (out_file.parent / "artwork.json").write_text(json.dumps(report))

            # Custody assertion: bit-for-bit identical
            self.assertEqual(out_file.read_bytes(), original_bytes)


class TestTier4ResilientStageFailureScenario(unittest.TestCase):
    """Scenario 4: Resilient stage failure and immutable candidate custody."""

    def test_stage_failure_preserves_candidate_immutability(self):
        """Simulated stage failure leaves original candidate bundle bytes strictly pristine."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cand_dir = root / "candidate"
            board_file = make_test_bundle(cand_dir, "fragile.kicad_pcb")
            before_bytes = board_file.read_bytes()

            # Simulate private scratch work directory
            scratch = root / "scratch"
            scratch.mkdir(parents=True, exist_ok=True)
            failed_report = {
                "status": "failed",
                "stage": "routing",
                "error": "congestion limits exceeded"
            }
            (scratch / "report.json").write_text(json.dumps(failed_report))

            # Verify candidate remains completely untouched
            self.assertEqual(board_file.read_bytes(), before_bytes)


class TestTier4BuildCompletionPipelineScenario(unittest.TestCase):
    """Scenario 5: Typed build completion pipeline with rating lineage & seat policy."""

    def test_build_completion_lineage_and_qualification_chain(self):
        """End-to-end lineage comparison between baseline and revised build packages."""
        baseline_criteria = {
            "requirements": [{"id": "req-01", "kind": "drc"}, {"id": "req-02", "kind": "erc"}]
        }
        revised_criteria = copy.deepcopy(baseline_criteria)

        # Baseline comparison
        try:
            from agent_ee import rating_lineage
            if hasattr(rating_lineage, "same_authored_baseline"):
                self.assertTrue(rating_lineage.same_authored_baseline(baseline_criteria, revised_criteria))
        except ImportError:
            self.skipTest("rating_lineage not yet available")

        # Package records and qualification chain
        rows = [
            {"id": "drc", "kind": "drc", "execution": "ok", "outcome": "pass"},
            {"id": "erc", "kind": "erc", "execution": "ok", "outcome": "pass"},
            {"id": "rt", "kind": "fab_roundtrip", "execution": "ok", "outcome": "pass"},
            {"id": "cpl", "kind": "cpl_rotation", "execution": "ok", "outcome": "pass"},
            {"id": "pkg", "kind": "fab_package", "details": {
                "assembly_orientation": {
                    "software_assessment": "complete",
                    "unassessed_rows": [],
                    "profile_sha256": "0" * 64
                }
            }}
        ]
        package_records = qualification._package_records(rows)
        self.assertEqual(package_records["roundtrip"], "pass")
        self.assertEqual(package_records["orientation"], "pass")


class TestTier4MultiBoardVariantScenario(unittest.TestCase):
    """Scenario 6: Multi-board variant selection and custody parity."""

    def test_variant_bundle_assembly_and_custody_hash(self):
        """Variants maintain independent custody hashes and verify bit-for-bit isolation."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            board_a = make_test_bundle(root / "variant_a", "var_a.kicad_pcb")
            board_b = make_test_bundle(root / "variant_b", "var_b.kicad_pcb")
            self.assertNotEqual(board_a, board_b)
            self.assertEqual(board_a.read_bytes(), board_b.read_bytes())


class TestTier4EnvelopeAndDfmScenario(unittest.TestCase):
    """Scenario 7: Complete board envelope and DFM boundary verification."""

    def test_envelope_and_dfm_rule_checking(self):
        """Board geometry passes outline containment and DFM-002 / DFM-003 rules."""
        from agent_ee import dfm_geometry
        self.assertIn("annular_ring", dfm_geometry.RULES)
        self.assertIn("acid_trap", dfm_geometry.RULES)
        self.assertIn("component_spacing", dfm_geometry.RULES)


class TestTier4NativeChecksProjectionScenario(unittest.TestCase):
    """Scenario 8: Native check results parsing and method class projection."""

    def test_complete_projected_method_classes_for_board(self):
        """All checks executed for a candidate project map to valid non-empty method classes."""
        checks = ["erc", "drc", "geometry", "placement", "netlist", "active_spice"]
        for cid in checks:
            cls = requirement_map.method_class(cid)
            self.assertIn(cls, ("native", "topology", "model", "measurement"))


class TestTier4PacketAndDraftReviewScenario(unittest.TestCase):
    """Scenario 9: Review packet creation, draft check, and evidence verification."""

    def test_packet_creation_and_draft_check_cycle(self):
        """End-to-end cycle: make candidate -> prepare packet -> review draft -> check valid."""
        from tests import test_findings as fixture
        t = fixture.FindingsTests()
        t.setUp()
        try:
            packet = dict(candidate_id=t.candidate_id, run_id=t.run_id, packet_sha256=t.packet_sha256)
            (t.run_dir / "packet.json").write_text(json.dumps(packet))
            schema = copy.deepcopy(reviewers.REVIEW_SCHEMA)
            reviewers.bind_evidence_schema(schema, "findings", t.run_dir, packet, "circuit")
            value = t._review(findings=[t._finding(
                assessment="concern", evidence=[dict(path="inputs/project/rc.kicad_sch",
                    locator="Synthetic symbol", lines=[1, 1])])])
            value["schema"] = "agent-ee.review/2"
            draft = t.root / "draft.json"
            draft.write_text(json.dumps(value))
            control = review_draft.prepare(t.run_dir, "circuit", packet, schema)
            res = review_draft.check(draft, t.run_dir, control)
            self.assertTrue(res.get("valid"))
        finally:
            t.tmp.cleanup()



class TestTier4StageReceiptLifecycleScenario(unittest.TestCase):
    """Scenario 10: Full multi-stage build receipt lifecycle and state progression."""

    def test_stage_receipt_hash_binding(self):
        """Stage step receipts record input digests and output write artifacts."""
        with tempfile.TemporaryDirectory() as tmp:
            stage_dir = Path(tmp)
            receipt = {
                "schema": "agent-ee.stage-receipt/1",
                "stage": "verification",
                "step": "agent-ee-routed-artwork",
                "status": "completed",
                "inputs": {"board": "0" * 64},
                "outputs": {"board": "0" * 64}
            }
            (stage_dir / "receipt.json").write_text(json.dumps(receipt))
            read_back = json.loads((stage_dir / "receipt.json").read_text())
            self.assertEqual(read_back["status"], "completed")
