"""Structural audit of capabilities.describe_check.

Every ``elif kind == '...'`` branch that builds a descriptor through a
``capability(...)`` call must publish ``result['predicates']`` inside that same
branch. A merge on 2026-10-18 (b3a6eb5) inserted a new branch between another
branch's ``capability`` call and its closing statements, which silently left the
``block_provenance`` kind without predicates; this test fails on that shape.
"""
import ast
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / 'src' / 'agent_ee' / 'capabilities.py'


def _branches(node):
    """Yield (kind, body) for each `kind == '<name>'` test in an if/elif chain."""
    while isinstance(node, ast.If):
        test = node.test
        if (isinstance(test, ast.Compare) and isinstance(test.left, ast.Name) and test.left.id == 'kind'
                and len(test.comparators) == 1 and isinstance(test.comparators[0], ast.Constant)):
            yield test.comparators[0].value, node.body
        node = node.orelse[0] if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If) else None


def _calls_capability(body):
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'capability'
               for stmt in body for n in ast.walk(stmt))


def _assigns_predicates(body):
    for stmt in body:
        for n in ast.walk(stmt):
            if isinstance(n, ast.Assign):
                for target in n.targets:
                    if (isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name)
                            and target.value.id == 'result' and isinstance(target.slice, ast.Constant)
                            and target.slice.value == 'predicates'):
                        return True
    return False


class DescribeCheckStructureTest(unittest.TestCase):
    def test_every_capability_branch_publishes_predicates(self):
        tree = ast.parse(SOURCE.read_text())
        describe = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'describe_check')
        chains = [n for n in ast.walk(describe) if isinstance(n, ast.If)]
        seen, missing = set(), []
        for chain in chains:
            for kind, body in _branches(chain):
                if kind in seen:
                    continue
                seen.add(kind)
                if _calls_capability(body) and not _assigns_predicates(body):
                    missing.append(kind)
        self.assertTrue(seen, 'describe_check has no kind branches')
        self.assertEqual(missing, [], 'kind branches that call capability() without publishing predicates')


if __name__ == '__main__':
    unittest.main()
