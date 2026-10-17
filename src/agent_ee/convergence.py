"""Stable criterion history and bounded escalation, independent of model prose."""

from .storage import identity


def advance(history, result, manifest, max_iterations):
    """Return a durable iteration record and a terminal/continuation diagnostic.

    Previously passing criteria and metric bounds are protected. Repairs must
    reduce failures or improve negative margins without worsening another one.
    Repeated designs and exhausted budgets escalate rather than oscillate.
    """
    failed = sorted(c["id"] for c in result["checks"] if c["outcome"] == "defect")
    passing = sorted(c["id"] for c in result["checks"]
                     if c["execution"] == "ok" and c["outcome"] == "pass")
    record = {"candidate_id": manifest["candidate_id"], "design_id": identity(manifest["files"]),
              "run_id": result["run_id"], "failed": failed, "passing": passing,
              "outcome": result["outcome"], "margins": {
                  c["id"]: c.get("details", {}).get("margins", {}) for c in result["checks"]}}
    reason = None
    if result["outcome"] == "incomplete":
        return record, {"state": "incomplete", "reason": "execution_incomplete"}
    elif history:
        protected = set().union(*(set(h["passing"]) for h in history))
        regressions = sorted(protected & set(failed))
        if regressions:
            reason = "regression"
        elif any(h["design_id"] == record["design_id"] for h in history):
            reason = "repeated_design"
        else:
            current_bounds = metric_bounds(record)
            prior_bounds = metric_bounds(history[-1])
            protected_bounds = set().union(*(set(k for k, v in metric_bounds(h).items() if v >= 0)
                                             for h in history))
            if any(current_bounds.get(k, float("-inf")) < 0 for k in protected_bounds):
                reason = "metric_regression"
            elif failed and not set(failed) < set(history[-1]["failed"]):
                # Compare each physical margin in its own units. A gain in one
                # dimension cannot buy a loss in another dimension.
                improving = (current_bounds.keys() == prior_bounds.keys() and
                    any(current_bounds[k] > v for k, v in prior_bounds.items() if v < 0) and
                    all(current_bounds[k] >= v for k, v in prior_bounds.items() if v < 0) and
                    set(failed) <= set(history[-1]["failed"]))
                if not improving:
                    reason = "no_monotonic_progress"
    if reason is None and result["outcome"] == "criteria_satisfied":
        return record, {"state": "criteria_satisfied"}
    if reason is None and not failed:
        reason = "unresolved_review_claims"
    if reason is None and len(history) + 1 >= max_iterations:
        reason = "iteration_limit"
    if reason:
        return record, {"schema": "agent-ee.tradeoff/1", "state": "escalated", "reason": reason,
            "failed_criteria": failed, "protected_criteria": sorted(set().union(
                *(set(h["passing"]) for h in history))) if history else [],
            "history": [*history, record],
            "required_action": "Resolve the declared constraints or model/coverage gap before starting a new campaign. "
                               "No requirement or margin was relaxed."}
    return record, {"state": "repair"}


def metric_bounds(record):
    return {(cid, metric, bound): value
            for cid, metrics in record.get("margins", {}).items()
            for metric, bounds in metrics.items() for bound, value in bounds.items()}
