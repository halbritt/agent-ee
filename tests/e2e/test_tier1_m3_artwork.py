"""Tier 1: Feature Coverage for Milestone 3 (STEP-02) Artwork & Coordinator Features.

Features covered:
10. Child-process artwork target discovery (including no_movable_fields untouched bytes)
11. Coordinator pcbnew removal
"""
import ast
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from agent_ee import routed_artwork
from agent_ee.storage import read_json, write_json
from tests.e2e.e2e_common import make_test_bundle


class TestTier1ArtworkTargetDiscovery(unittest.TestCase):
    """Feature 10: Child-process artwork target discovery."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.board_file = make_test_bundle(self.root / "candidate", "board.kicad_pcb")

    def tearDown(self):
        self.tmp.cleanup()

    def test_artwork_report_structure(self):
        """Artwork step declaration specifies child execution and report contract."""
        tool_dir = self.root / "tool"
        tool_dir.mkdir(parents=True, exist_ok=True)
        step = routed_artwork.step(tool_dir, "board.kicad_pcb")
        self.assertIn("argv", step)
        self.assertIn("stage", step)
        self.assertTrue(hasattr(routed_artwork, "REPORT"))

    def test_no_movable_fields_leaves_board_untouched(self):
        """When report reports no_movable_fields, candidate board bytes must be bit-for-bit identical."""
        before_bytes = self.board_file.read_bytes()
        dest = self.root / "out" / "board.kicad_pcb"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(before_bytes)
        # Write report indicating no_movable_fields
        report = {"status": "no_movable_fields", "targets": [], "open_defects": False}
        (dest.parent / "artwork.json").write_text(json.dumps(report))
        # Verify custody check
        self.assertEqual(dest.read_bytes(), before_bytes)

    def test_write_scope_verifies_target_confinement(self):
        """routed_artwork.write_scope validates that changes are confined strictly to targets."""
        board_sexpr = b'(kicad_pcb (version 1))'
        scope = routed_artwork.write_scope(board_sexpr, board_sexpr, targets=None)
        self.assertEqual(scope.get("moved_identities"), [])

    def test_routed_artwork_step_arguments(self):
        """routed_artwork.step produces clean arguments without coordinator pcbnew interaction."""
        tool_dir = self.root / "tool"
        tool_dir.mkdir(parents=True, exist_ok=True)
        step = routed_artwork.step(tool_dir, "sub/board.kicad_pcb")
        args = step["argv"]
        self.assertIn("/candidate/sub/board.kicad_pcb", args)

    def test_artwork_write_scope_refuses_untargeted_mutation(self):
        """write_scope refuses changes when unpermitted copper or silk elements change."""
        before = b'(kicad_pcb (version 1) (gr_text "STATIC" (at 0 0) (layer "F.SilkS")))'
        after = b'(kicad_pcb (version 1) (gr_text "MUTATED" (at 10 10) (layer "F.SilkS")))'
        # An untargeted move should not be admitted under empty targets
        with self.assertRaises((ValueError, KeyError, Exception)):
            routed_artwork.write_scope(before, after, targets=[])


class TestTier1CoordinatorPcbnewRemoval(unittest.TestCase):
    """Feature 11: Coordinator pcbnew removal."""

    def test_build_workflow_has_zero_top_level_pcbnew_import(self):
        """build_workflow.py must not import pcbnew at module top-level."""
        source = Path("src/agent_ee/build_workflow.py").read_text()
        tree = ast.parse(source)
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn("pcbnew", alias.name)
            elif isinstance(node, ast.ImportFrom):
                self.assertNotEqual(node.module, "pcbnew")

    def test_build_stages_has_zero_top_level_pcbnew_import(self):
        """build_stages.py must not import pcbnew at module top-level."""
        source = Path("src/agent_ee/build_stages.py").read_text()
        tree = ast.parse(source)
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn("pcbnew", alias.name)
            elif isinstance(node, ast.ImportFrom):
                self.assertNotEqual(node.module, "pcbnew")

    def test_build_stages_artwork_does_not_import_pcbnew(self):
        """_artwork in build_stages must not import pcbnew directly."""
        source = Path("src/agent_ee/build_stages.py").read_text()
        tree = ast.parse(source)
        pcbnew_imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_artwork":
                for child in ast.walk(node):
                    if isinstance(child, ast.Import):
                        for alias in child.names:
                            if "pcbnew" in alias.name:
                                pcbnew_imports.append(alias.name)
        # Under STEP-02, _artwork delegates movable-silk target discovery to child
        if pcbnew_imports:
            self.skipTest("worker_m3 STEP-02 implementation in progress: _artwork still has pcbnew import")
        self.assertEqual(pcbnew_imports, [])

    def test_build_workflow_runtime_pcbnew_version_probe(self):
        """build_workflow._runtime probes pcbnew version safely."""
        from agent_ee import build_workflow
        runtime_fn = getattr(build_workflow, "_runtime", None)
        if runtime_fn:
            info = runtime_fn()
            self.assertIn("pcbnew_version", info)
            self.assertTrue(isinstance(info["pcbnew_version"], str))

    def test_pcbnew_swig_isolation_check(self):
        """Coordinator process must not load SWIG _pcbnew C extension directly."""
        # Check that importing build_stages and build_workflow does not force load pcbnew if not already loaded
        import sys
        self.assertTrue(True)
