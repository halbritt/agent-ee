# Argon Finding Validation & Adjudication Contract (`src/agent_ee/findings.py`)

> Historical design, review or evaluation record. Status and proposed actions
> describe the recorded work, not the current implementation. Use the
> [current manual](README.md) for supported contracts and commands.

Implemented in `src/agent_ee/findings.py` with tests in `tests/test_findings.py`.

## Public API

1. **`validate_review(value, candidate_id, run_id, packet_sha256, run_dir) -> dict`**
   - Enforces `schema == "agent-ee.review/1"`, exact `candidate_id`, `run_id`, and `packet_sha256` match, and safe `role` (rejecting `"check"`, `"adjudicator"`, or unsafe IDs).
   - Verifies `run_dir / "candidate.json"` snapshot integrity (`verify_snapshot`) and `run_dir / "packet.json"` identity when present.
   - Validates each finding (`id`, `title`, `location`, `condition`, `mechanism`, `severity`, `assessment`, `evidence`, `reasoning`) and rejects duplicate local finding IDs within the review.
   - Validates every citation via `validate_citation(item, run_dir)`:
     - Path must be a safe relative POSIX path under `inputs/<...>`, `checks/<...>`, or `work/<check-id>/<...>` (rejects `reviews/`, `review-work/`, `events/`, traversal `..`, symlinks, and missing files).
   - **Empty evidence handling**: If a reviewer finding claims `assessment == "defect"` with `evidence == []`, it is visibly downgraded to `assessment = "concern"` (`downgraded_from = "defect"`, `validation_note` added, and review `limitations` appended) rather than dropped or accepted as a defect.

2. **`collect_findings(reviews: list[dict], checks: list[dict]) -> list[dict]`**
   - Accepts empty `reviews` (`[]`) and/or empty `checks` (`[]`); does not require `candidate_id` or `run_id` on empty lists or check dicts.
   - Validates each check result with `validate_check_result(check)` (enforcing execution/outcome honesty: any `execution != "ok"` MUST have `outcome == "unknown"`, so failed/timed-out/interrupted/not_run checks can never masquerade as `"pass"` or as an established `"defect"`).
   - Preserves every check with `execution == "ok"` and `outcome == "defect"` as a finding with namespaced ID `check/<id>`, `source="check"`, `role="check"`, `assessment="defect"`, `status="unresolved"`, `disposition="unresolved"`, `events=[]`.
   - Preserves every reviewer finding with namespaced ID `<role>/<id>`, `source="review"`, `role=review["role"]`, `status="unresolved"`, `disposition="unresolved"`, `events=[]`.
   - **No voting / no majority elimination**: A singleton finding in one review survives unchanged even when all other reviews are clean (`findings: []`).

3. **`adjudicate(findings: list[dict], decisions: dict, run_dir: Path) -> list[dict]`**
   - Enforces `schema == "agent-ee.decisions/1"`, exact `candidate_id` and `run_id` match against `run_dir` (`candidate.json`, `result.json`, `packet.json`), and snapshot integrity (`verify_snapshot`).
   - Validates every decision (`finding_id`, `disposition` in `accepted|noted|dismissed|deferred|duplicate|closed`, non-empty `reason`, valid `evidence` citations in `run_dir`).
   - **Disposition rules**:
     - `accepted`: requires `assessment == "defect"` and `len(evidence) >= 1` with valid citations in `run_dir`. Non-defect concerns (`assessment == "concern"`, including unsupported defects downgraded to `concern`) cannot be `accepted` as established defects.
     - `noted`: records verified, evidence-supported non-defect observations (`assessment == "concern"` and `severity` in `{"minor", "info"}` with non-empty original `evidence` and not `downgraded_from == "defect"`). Requires non-empty `reason` and `len(evidence) >= 1` valid citations in `run_dir`, and preserves all original finding fields and citations. Forbidden on `source == "check"`, `assessment == "defect"`, `severity` in `{"blocking", "major"}`, or unsupported/downgraded claims.
     - `dismissed`: forbidden on authoritative native check defects (`source == "check"`). For reviewer findings, requires non-empty `reason`; dismissing a supported reviewer finding (`assessment == "defect"` or non-empty finding `evidence`) requires `len(evidence) >= 1` counter-evidence, whereas an unsupported concern (`evidence == []`) may be dismissed with a non-empty `reason`.
     - `deferred`: forbidden on authoritative native check defects (`source == "check"`); preserves explicit uncertainty (`status = "deferred"`, `disposition = "deferred"`) on reviewer findings (including unrefuted `major`/`blocking` concerns).
     - `duplicate`: requires `duplicate_of` referencing a distinct existing finding (`duplicate_of != finding_id`); rejects `duplicate_of` on non-duplicate dispositions; detects and rejects any cycle (`A -> B -> A`); verifies the canonical target has status in `{"accepted", "noted", "unresolved", "deferred"}` (never `"dismissed"` or `"closed"`); forbids duplicating a `source == "check"` or `"accepted"` finding into a non-`"accepted"` target; and forbids duplicating a `defect`, `major`/`blocking` concern, or unsupported claim into a `"noted"` target. Records `duplicates` on the target and keeps both records in `findings`.
     - `closed`: unified with `validate_closure(previous_run, run_dir, ...)`. Requires explicit `previous_run` linkage (on the decision item or `run_dir/result.json["previous_run"]`), distinct saved candidate IDs (`old_candidate_id != new_candidate_id`), unchanged `scope.checks` and `scope.requirements` + requirement file hashes, prior finding status `"accepted"`, `new_result["outcome"] == "criteria_satisfied"`, and fresh passing affected checks (`execution == "ok"` and `outcome == "pass"`).
   - **Consistent status & report fields**: Updates `finding["status"] = disposition`, `finding["disposition"] = disposition`, `finding["reason"] = reason`, `finding["disposition_reason"] = reason`, `finding["decision"] = event`, and appends `event` to `finding["events"]` without deleting any finding or overwriting original `finding["evidence"]`. Unmentioned findings remain `"unresolved"`.

4. **`validate_closure(previous_run, current_run, finding_id, check_ids, reason, *, evidence=None) -> dict`**
   - Bounded-closure verifier invoked both directly and by `adjudicate(..., disposition="closed")`. Enforces distinct saved candidates, unchanged `scope.checks` and `scope.requirements` list + file hashes, prior `"accepted"` finding status, `"criteria_satisfied"` on the new run, and fresh `execution == "ok"` + `outcome == "pass"` on all `check_ids`.
