import unittest
from agent_ee.convergence import advance


def observation(failed, candidate, passing=(), outcome="defects_or_unresolved"):
    return ({"checks": [{"id": c, "execution": "ok", "outcome": "defect"} for c in failed] +
                       [{"id": c, "execution": "ok", "outcome": "pass"} for c in passing],
             "run_id": candidate, "outcome": outcome},
            {"candidate_id": candidate, "files": {"board": candidate}})


class ConvergenceTests(unittest.TestCase):
    def test_fix_a_reintroduces_b_escalates_without_third_repair(self):
        first, state = advance([], *observation(["A"], "one", ["B"]), 10)
        self.assertEqual(state["state"], "repair")
        second, state = advance([first], *observation(["B"], "two", ["A"]), 10)
        self.assertEqual(state["reason"], "regression")
        self.assertIn("B", state["protected_criteria"])
        self.assertEqual(len(state["history"]), 2)

    def test_progress_then_pass(self):
        first, _ = advance([], *observation(["A", "B"], "one"), 3)
        second, state = advance([first], *observation(["B"], "two", ["A"]), 3)
        self.assertEqual(state["state"], "repair")
        _, state = advance([first, second], *observation([], "three", ["A", "B"], "criteria_satisfied"), 3)
        self.assertEqual(state["state"], "criteria_satisfied")

    def test_stall_iteration_limit_and_incomplete_halt(self):
        first, _ = advance([], *observation(["A"], "one"), 2)
        _, state = advance([first], *observation(["A"], "two"), 2)
        self.assertEqual(state["reason"], "no_monotonic_progress")
        _, state = advance([], *observation(["A"], "one"), 1)
        self.assertEqual(state["reason"], "iteration_limit")
        _, state = advance([], *observation([], "one", outcome="incomplete"), 5)
        self.assertEqual(state["reason"], "execution_incomplete")
        self.assertEqual(state["state"], "incomplete")


class MetricConvergenceTests(unittest.TestCase):
    def test_partial_metric_progress_then_regression(self):
        result, manifest = observation(["joint"], "one")
        result["checks"][0]["details"] = {"margins": {"heat": {"max": -10}, "loop": {"max": -5}}}
        first, _ = advance([], result, manifest, 5)
        result, manifest = observation(["joint"], "two")
        result["checks"][0]["details"] = {"margins": {"heat": {"max": 15}, "loop": {"max": -1}}}
        second, state = advance([first], result, manifest, 5)
        self.assertEqual(state["state"], "repair")
        result, manifest = observation(["joint"], "three")
        result["checks"][0]["details"] = {"margins": {"heat": {"max": -1}, "loop": {"max": 2}}}
        _, state = advance([first, second], result, manifest, 5)
        self.assertEqual(state["reason"], "metric_regression")
