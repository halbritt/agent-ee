"""AST-enforced architectural boundaries and structural invariants."""
import ast
import unittest
from pathlib import Path


class TestArchitectureBoundaries(unittest.TestCase):
    """Enforce architectural layer boundaries, confinement, and module isolation via AST."""

    def setUp(self):
        self.src_root = Path("src/agent_ee")

    def test_invariant_1_zero_acquisition_imports_from_general_modules(self):
        """Invariant 1: Zero imports of agent_ee.acquisition from general modules.

        Benchmark drivers and the acquisition package itself are excluded.
        """
        violations = []
        for py_path in sorted(self.src_root.glob("*.py")):
            if py_path.name == "generation_benchmark.py":
                continue
            tree = ast.parse(py_path.read_text(), filename=str(py_path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "agent_ee.acquisition" or alias.name.startswith("agent_ee.acquisition."):
                            violations.append((py_path.name, node.lineno, alias.name))
                elif isinstance(node, ast.ImportFrom):
                    if node.level == 0 and node.module:
                        if node.module == "agent_ee.acquisition" or node.module.startswith("agent_ee.acquisition."):
                            violations.append((py_path.name, node.lineno, node.module))
                        elif node.module == "agent_ee":
                            for alias in node.names:
                                if alias.name == "acquisition":
                                    violations.append((py_path.name, node.lineno, f"agent_ee.{alias.name}"))
                    elif node.level == 1:
                        if node.module and (node.module == "acquisition" or node.module.startswith("acquisition.")):
                            violations.append((py_path.name, node.lineno, f".{node.module}"))
                        elif not node.module:
                            for alias in node.names:
                                if alias.name == "acquisition":
                                    violations.append((py_path.name, node.lineno, f".{alias.name}"))
        self.assertEqual(violations, [], f"General modules importing agent_ee.acquisition: {violations}")

    def test_invariant_2_zero_pcbnew_imports_in_build_stages_and_workflow(self):
        """Invariant 2: Zero 'import pcbnew' in build_workflow.py or build_stages.py."""
        violations = []
        for name in ("build_workflow.py", "build_stages.py"):
            path = self.src_root / name
            if not path.exists():
                continue
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "pcbnew" or alias.name.startswith("pcbnew."):
                            violations.append((name, node.lineno, alias.name))
                elif isinstance(node, ast.ImportFrom):
                    if node.module == "pcbnew" or (node.module and node.module.startswith("pcbnew.")):
                        violations.append((name, node.lineno, node.module))
        self.assertEqual(violations, [], f"Prohibited pcbnew imports: {violations}")

    def test_invariant_3_proc_self_mountinfo_reads_confined(self):
        """Invariant 3: Zero /proc/self/mountinfo reads outside authorized stage confinement modules."""
        # stage_execution.py is the canonical consolidated confinement module.
        # Until STEP-02 lands, footprint_probe and forward_annotation are allowed precursors.
        authorized = {"stage_execution.py", "stage_receipts.py", "forward_annotation.py", "footprint_probe.py"}
        violations = []
        for path in sorted(self.src_root.glob("**/*.py")):
            text = path.read_text()
            if "/proc/self/mountinfo" in text and path.name not in authorized:
                violations.append(path.name)
        self.assertEqual(violations, [], f"Unauthorized mountinfo reads in: {violations}")

    def test_invariant_4_zero_external_calls_to_reviewers_dispatch(self):
        """Invariant 4: Zero external calls to reviewers.dispatch outside reviewers.py and packet_ab.py."""
        violations = []
        for path in sorted(self.src_root.glob("*.py")):
            if path.name in ("reviewers.py", "packet_ab.py"):
                continue
            text = path.read_text()
            if "reviewers.dispatch" in text or ("dispatch(" in text and "from .reviewers import dispatch" in text):
                violations.append(path.name)
        self.assertEqual(violations, [], f"Unauthorized reviewers.dispatch calls in: {violations}")

    def test_invariant_5_zero_bidirectional_cycles_across_completion_and_benchmark(self):
        """Invariant 5: Zero bidirectional cycles across build_completion, judgment_seats, campaign, and generation_benchmark."""
        modules = ["build_completion", "judgment_seats", "campaign", "generation_benchmark"]
        graph = {m: set() for m in modules}

        for m in modules:
            p = self.src_root / f"{m}.py"
            if not p.exists():
                continue
            tree = ast.parse(p.read_text(), filename=str(p))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for a in node.names:
                        for target in modules:
                            if a.name == f"agent_ee.{target}" or a.name == target:
                                graph[m].add(target)
                elif isinstance(node, ast.ImportFrom):
                    for target in modules:
                        if (node.level == 1 and node.module == target) or (node.level == 0 and node.module == f"agent_ee.{target}"):
                            graph[m].add(target)

        cycles = []
        for m1 in modules:
            for m2 in graph[m1]:
                if m1 in graph[m2]:
                    cycles.append((m1, m2))
        self.assertEqual(cycles, [], f"Bidirectional import cycles found: {cycles}")
