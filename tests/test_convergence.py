import unittest
from agent_ee.convergence import advance


def observation(failed, candidate, passing=(), outcome="defects_or_unresolved"):
    return ({"checks": [{"id": c, "execution": "ok", "outcome": "defect"} for c in failed] +
                       [{"id": c, "execution": "ok", "outcome": "pass"} for c in passing],
             "run_id": candidate, "outcome": outcome},
            {"candidate_id": candidate, "files": {"board": candidate}})


class ConvergenceTests(unittest.TestCase):
    def test_info_check_stays_protected_across_repairs(self):
        history = []
        for failed, number, passing in [(['A', 'B'], 'one', ['C']),
                                         (['B'], 'two', ['A', 'C'])]:
            result, manifest = observation(failed, number, passing)
            result['checks'].append({'id': 'inventory', 'execution': 'ok', 'outcome': 'info'})
            record, state = advance(history, result, manifest, 10)
            self.assertEqual(state['state'], 'repair')
            history.append(record)
        _, state = advance(history, *observation(['inventory'], 'three', ['A', 'B', 'C']), 10)
        self.assertEqual(state['reason'], 'regression')
        self.assertIn('inventory', state['protected_criteria'])
        self.assertEqual(len(state['history']), 3)

    def test_info_check_success_and_missing_coverage(self):
        result, manifest = observation(['A'], 'one', ['inventory'])
        result['checks'][-1]['outcome'] = 'info'
        first, _ = advance([], result, manifest, 10)
        self.assertIn('inventory', first['passing'])
        for execution, outcome in [('ok', 'info'), ('failed', 'unknown')]:
            with self.subTest(execution=execution, outcome=outcome):
                result, manifest = observation([], 'two', ['A', 'inventory'],
                    'criteria_satisfied' if execution == 'ok' else 'incomplete')
                result['checks'][-1].update(execution=execution, outcome=outcome)
                record, state = advance([first], result, manifest, 10)
                self.assertEqual(state['state'], result['outcome'])
                if execution != 'ok':
                    self.assertEqual(state['reason'], 'execution_incomplete')
                    self.assertNotIn('inventory', record['passing'])

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


class PendingFabTests(unittest.TestCase):
    def test_unknown_artifacts_schedule_only_after_every_other_obligation_is_satisfied(self):
        from agent_ee.fab_stage import pending_checks
        pending = {'id': 'fab', 'kind': 'fab_package', 'execution': 'not_run', 'outcome': 'unknown',
                   'details': {'reason': 'fab_step_pending', 'fab_step': {'receipt': None}}}
        native = {'id': 'native', 'kind': 'netlist', 'execution': 'ok', 'outcome': 'info'}
        result = {'state': 'complete', 'checks': [native, pending], 'expected_check_ids': ['native', 'fab'],
                  'expected_review_roles': ['circuit', 'physical'],
                  'reviews': [{'role': role} for role in ('circuit', 'physical')],
                  'review_executions': [{'role': role, 'execution': 'ok'} for role in ('circuit', 'physical')],
                  'adjudicated': True, 'findings': []}
        self.assertEqual(pending_checks(result), ['fab'])
        for change in ({'adjudicated': False}, {'reviews': []}, {'empirical_feedback': ['pending']},
                       {'findings': [{'status': 'accepted'}]},
                       {'checks': [{**native, 'execution': 'failed', 'outcome': 'unknown'}, pending]},
                       {'checks': [native, {**pending, 'execution': 'failed'}]},
                       {'checks': [native, {**pending, 'kind': 'measurement_adapter'}]},
                       {'checks': [native, {**pending, 'details': {'reason': 'fab_step_pending'}}]}):
            with self.subTest(change=change):
                self.assertEqual(pending_checks({**result, **change}), [])


if __name__ == '__main__':
    unittest.main()
