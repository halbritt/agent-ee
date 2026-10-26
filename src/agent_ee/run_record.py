"""Run record: result.json shape, review work lifecycle, freshness verification and outcome projection.

Interrupted runs preserve records; resumes always make fresh runs. Host fingerprints (policy and runtime
files) are observed here; callers that already hold an observation pass it as ``host`` (AGENTEE-545)."""

import copy
import datetime
import math
import os
import re
import shutil
import signal
import subprocess
import time
import traceback
import uuid
from pathlib import Path

from .candidate import capture, safe_relative, verify_snapshot, verify_source
from .storage import digest, digest_scope, fingerprint as runtime_digest, read_json, write_json


def policy_files():
    # Package data is policy too: the snapshot a helper step runs from needs it (AGENTEE-439).
    root = Path(__file__).resolve().parent
    files = {p.name: digest(p) for p in sorted(root.glob("*.py"))}
    files.update({p.relative_to(root).as_posix(): digest(p) for p in sorted((root / "data").rglob("*"))
                  if p.is_file() and "__pycache__" not in p.parts})
    return files


LOADED_POLICY_FILES = policy_files()


def runtime_files(*, checkpoint=None):
    # One host observation: a binary reached under two names (claude) is hashed once.
    with digest_scope():
        return _runtime_files(checkpoint)


def _runtime_files(checkpoint):
    def fingerprint(path):
        return runtime_digest(path, checkpoint=checkpoint)

    files = {}
    for name in ("kicad-cli", "ngspice", "/usr/bin/ngspice", "bwrap", "/usr/bin/python3", "claude"):
        path = shutil.which(name)
        files[name] = {"path": path, "sha256": fingerprint(path) if path else None}
    from .harnesses import executable_for
    for name in ("claude", "codex", "agy"):
        path = executable_for(name)
        files[name] = {"path": path, "sha256": fingerprint(path) if path and Path(path).is_file() else None}
    native_codex = executable_for("codex")
    host = Path(native_codex).parent / "codex-code-mode-host" if native_codex else None
    files["codex_tool_host"] = {"path": str(host) if host and host.is_file() else None,
        "sha256": fingerprint(host) if host and host.is_file() else None}
    path, diagnostic = pcbnew_extension(checkpoint=checkpoint)
    if path:
        files["pcbnew"] = {"path": path, "sha256": fingerprint(path)}
    else:
        files["pcbnew"] = {"path": None, "sha256": None, "diagnostic": diagnostic}
    from .placement_rust_runtime import resolve_executable
    from .placement_runtime import PlanningProcessError
    files["agent-ee-placement-build"] = {"path": None, "sha256": None}
    try:
        planner = resolve_executable()
    except (OSError, PlanningProcessError) as exc:
        files["agent-ee-placement"] = {"path": None, "sha256": None, "diagnostic": str(exc)}
    else:
        files["agent-ee-placement"] = {"path": str(planner), "sha256": fingerprint(str(planner))}
        receipt = planner.with_name(planner.name + '.json')
        if receipt.is_file():
            files["agent-ee-placement-build"] = {"path": str(receipt), "sha256": fingerprint(str(receipt))}
    return files


def pcbnew_extension(*, checkpoint=None, python='/usr/bin/python3'):
    """Return (path, diagnostic) for KiCad's native _pcbnew extension module."""
    # pcbnew shares a large native extension rather than a standalone CLI.
    argv = [python, '-c', 'import _pcbnew; print(_pcbnew.__file__)']
    if checkpoint is None:
        probe = subprocess.run(argv, capture_output=True, text=True, timeout=15)
        return (probe.stdout.strip(), '') if probe.returncode == 0 else (None, probe.stderr)
    with subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, start_new_session=True) as probe:
        end = time.monotonic() + 15
        try:
            while True:
                checkpoint()
                if time.monotonic() >= end:
                    raise ValueError('runtime_probe_timeout')
                try:
                    stdout, stderr = probe.communicate(timeout=.05)
                    return (stdout.strip(), '') if probe.returncode == 0 else (None, stderr)
                except subprocess.TimeoutExpired:
                    continue
        finally:
            # The leader may have exited while a descendant still holds a pipe.
            try:
                os.killpg(probe.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                probe.communicate(timeout=1)
            except subprocess.TimeoutExpired as exc:
                raise ValueError('runtime_probe_cleanup_timeout') from exc


def artifacts(run):
    found = {p.relative_to(run).as_posix(): digest(p) for parent in
            ("inputs", "work", "checks", "reviews", "review-work", "review-executions", "review-attempts")
            for p in sorted((run / parent).rglob("*")) if p.is_file()}
    for p in (run / "events").glob("*.json"):
        found[p.relative_to(run).as_posix()] = digest(p)
    if (run / "adjudicator-execution.json").is_file():
        found["adjudicator-execution.json"] = digest(run / "adjudicator-execution.json")
    return found


def verify(run, *, current_source=True, observations=None, invoked=(), host=None):
    """Raise on integrity failure; unused-harness changes are appended to observations.

    invoked names further definitions that ran against this run (a campaign
    writer); their harnesses gate drift like the profile's. host, when given, holds
    the caller's one observation of this host's policy and runtime files (see observed)."""
    run = Path(run).resolve()
    from .locking import reader_lock
    from .bench_transactions import verify_publications
    with reader_lock(run):
        result = read_json(run / "result.json")
        verify_publications(run, result)
        return verify_records(run, result, current_source=current_source, observations=observations,
                              invoked=invoked, host=host)


def observed(host, name, observe):
    """observe(), taken once per host dict: one verification call that checks several runs
    shares one observation of this host's files instead of re-hashing them per run (AGENTEE-250).

    host maps "policy_files" and/or "runtime_files" to fingerprints; a caller that already holds
    them (a test, a verifier with its own observation) passes them in instead of re-observing."""
    if host is None:
        return observe()
    if name not in host:
        host[name] = observe()
    return host[name]


def pinned(host, name, observe):
    """The fingerprint host gives for name, else a fresh observe() that is not kept.

    A run compares its start observation with a later one, so a lifecycle never caches into
    host. The loaded-code fingerprint (LOADED_POLICY_FILES) is never supplied."""
    return copy.deepcopy(host[name]) if host is not None and name in host else observe()


def verify_records(run, result, *, current_source=True, observations=None, invoked=(), host=None):
    """Validate an explicit projection; callers separately bind publication intent."""
    manifest = verify_source(run) if current_source else verify_snapshot(run)
    if manifest["candidate_id"] != result.get("candidate_id"):
        raise ValueError("result candidate identity differs from snapshot")
    declared_limits = manifest["scope"].get("limitations", [])
    retained_limits = result.get("limitations", [])
    if (not isinstance(retained_limits, list) or
            retained_limits[:len(declared_limits)] != declared_limits):
        raise ValueError("result omits or changes captured scope limitations")
    if result.get("policy_files") != observed(host, "policy_files", policy_files):
        raise ValueError("review implementation changed; rerun under the current rules")
    from .preflight import profile_definitions, runtime_drift
    # Without a captured launch expectation every harness counts as used.
    drift = runtime_drift(result.get("runtime_files") or {}, observed(host, "runtime_files", runtime_files),
                          [*profile_definitions(manifest["scope"]), *invoked]
                          if "launch_expectation" in result else None)
    if not isinstance(result.get("runtime_files"), dict) or drift["gated"]:
        raise ValueError("tool runtime changed; rerun under the current tools")
    if observations is not None:
        observations.extend(drift["unused_changes"])
    from .kicad_happy import verify_installations
    verify_installations(result["checks"])
    if artifacts(run) != result.get("artifact_hashes"):
        raise ValueError("execution/review evidence changed; prior acceptance is stale")
    if digest(run / "candidate.json") != result.get("manifest_sha256"):
        raise ValueError("candidate manifest changed")
    if (run / "packet.json").exists() and digest(run / "packet.json") != result.get("packet_file_sha256"):
        raise ValueError("review evidence packet changed")
    from .packet_index import verify_navigation
    from .baseline import load_baseline
    baseline = load_baseline(run)
    verify_navigation(run, manifest, baseline, result["checks"], result["run_id"])
    from .preflight import verify_calls
    verify_calls(run, result, manifest)
    from .baseline import verify_evidence
    verify_evidence(run, result["checks"], required=result["state"] == "complete")
    for spec in manifest["scope"].get("checks", []):
        tool = spec.get("params", {}).get("tool")
        if spec.get("kind") == "datasheet_facts":
            from .datasheet_facts import verify_extractor
            verify_extractor(spec.get("params", {}).get("extractor"))
        if spec.get("kind") == "measurements":
            if not isinstance(tool, dict) or not tool.get("argv") or not tool.get("sha256"):
                raise ValueError("measurement check lacks a pinned tool declaration")
            if digest(tool["argv"][0]) != tool["sha256"]:
                raise ValueError("declared measurement tool changed since review")
    if result.get("candidate_id"):
        from .rf_review import decide_run
        # A record without the decision is accepted only when no RF review is required.
        expected, stored = decide_run(run, manifest), result.get("rf_review")
        if (stored is None and expected["required"]) or (stored is not None and stored != expected):
            raise ValueError("stored RF review decision differs from the captured candidate's RF sources")
    from .purpose_review import verify_records as verify_purpose
    verify_purpose(run, result)
    from .board_envelope import final_qualification
    # Recomputed from the immutable baseline verified above: a removed, stale or edited guard record is refused.
    expected = final_qualification(run, result.get("candidate_id"), baseline=baseline)
    stored = result.get("final_qualification")
    if stored != expected:
        raise ValueError("stored final qualification differs from the captured baseline envelope coverage")
    if result.get("outcome") != outcome(result):
        raise ValueError("stored result contradicts its execution and finding records")
    # Every run state, checks-only and interrupted runs included, projects the retained checks.
    if ("expected_check_ids" in result and
            result["expected_check_ids"] != [c.get("id") for c in manifest["scope"].get("checks", [])]):
        raise ValueError("result check inventory differs from the captured scope's declared checks")
    bind_checks(run, result["checks"])
    bind_check_findings(result)
    if result["state"] == "complete":
        expected = {c["id"] for c in manifest["scope"]["checks"]}
        if expected != {c["id"] for c in result["checks"]}:
            raise ValueError("completed result omits declared checks")
        for check in result["checks"]:
            if check != read_json(run / "checks" / (check["id"] + ".json")):
                raise ValueError("check projection differs from retained native-check result")
        for review in result["reviews"]:
            if review != read_json(run / "reviews" / (review["role"] + ".json")):
                raise ValueError("review projection differs from retained initial review")
    return result


def bind_checks(run, checks):
    """Raise unless the projected checks are exactly the retained checks/*.json records.

    A projected check without a retained record is accepted only as an unexecuted unknown, the
    placeholder recover_records writes for a check the run never finished (AGENTEE-16)."""
    retained = {p.name: read_json(p) for p in sorted((Path(run) / "checks").glob("*.json"))}
    if not isinstance(checks, list) or not all(isinstance(c, dict) for c in checks):
        raise ValueError("result checks are not a list of records")
    for check in checks:
        cid = check.get("id")
        name = f"{cid}.json" if isinstance(cid, str) else None
        if name in retained:
            if check != retained.pop(name):
                raise ValueError(f"check {cid} projection differs from retained native-check result checks/{name}")
            continue
        # A malformed declaration is retained as checks/invalid-<index>.json.
        malformed = next((n for n, r in retained.items() if n.startswith("invalid-") and r == check), None)
        if malformed is not None:
            del retained[malformed]
        elif check.get("execution") == "ok" or check.get("outcome") != "unknown":
            raise ValueError(f"check {cid!r} projects an executed or known result without a retained "
                             "native-check record")
    if retained:
        raise ValueError("retained native-check results are not projected: " + ", ".join(sorted(retained)))


# Fields adjudication owns; every other collected field derives from the check record.
ADJUDICATED_FIELDS = ("status", "disposition", "events")


def bind_check_findings(result):
    """Raise unless the check findings are those the (bound) projected checks derive."""
    from .findings import collect_findings
    expected = {f["id"]: f for f in collect_findings([], result["checks"])}
    stored = [f for f in result.get("findings", []) if not isinstance(f, dict) or f.get("source") == "check"]
    ids = [f.get("id") if isinstance(f, dict) else None for f in stored]
    if len(ids) != len(set(ids)) or set(ids) != set(expected):
        raise ValueError("check findings differ from the defects in the retained native-check results")
    for finding in stored:
        derived = expected[finding["id"]]
        if any(finding.get(key) != value for key, value in derived.items() if key not in ADJUDICATED_FIELDS):
            raise ValueError(f"finding {finding['id']} differs from its retained native-check result")


def finding_status(finding):
    return finding.get("disposition", finding.get("status", "unresolved"))


def outcome(result):
    if result.get("empirical_feedback"):
        return "incomplete"
    if result.get("state") != "complete":
        return "incomplete"
    if (result.get("expected_check_ids") is not None and
            set(result["expected_check_ids"]) != {c["id"] for c in result.get("checks", [])}):
        return "incomplete"
    roles = result.get("expected_review_roles")
    if roles is not None and (set(roles) != {r["role"] for r in result.get("reviews", [])} or
            set(roles) != {r["role"] for r in result.get("review_executions", [])}):
        return "incomplete"
    failures = [c for c in result.get("checks", []) if c["execution"] != "ok" or c["outcome"] == "unknown"]
    if failures or any(r["execution"] != "ok" for r in result.get("review_executions", [])):
        return "incomplete"
    if len(result.get("reviews", [])) < 2 or not result.get("adjudicated"):
        return "incomplete"
    from .rf_review import gate as rf_gate
    if result.get("targeted_review_roles") or rf_gate(result):
        return "incomplete"
    # A disposition cannot silently change a declared executable criterion.
    if any(c["outcome"] == "defect" for c in result.get("checks", [])):
        return "defects_or_unresolved"
    # An adjudicated purpose deferral is advisory (AGENTEE-385): savings cannot block acceptance.
    from .purpose_review import advisory_deferral
    statuses = [finding_status(f) for f in result.get("findings", []) if not advisory_deferral(f)]
    if any(s in ("accepted", "unresolved", "deferred") for s in statuses):
        return "defects_or_unresolved"
    # Open captured envelope qualification is missing evidence, not a design defect (AGENTEE-197).
    if (result.get("final_qualification") or {}).get("blocking"):
        return "incomplete"
    return "criteria_satisfied"


def render(run, result):
    lines = [f"# {result.get('name', 'agent-ee')} review", "",
        f"Outcome: **{result['outcome']}**; process state: `{result['state']}`.", "",
        f"Candidate: `{result.get('candidate_id', 'capture incomplete')}`",
        f"Run: `{result['run_id']}`", "", result.get("scope", ""), "",
        "## Checks", "", "| Check | Execution | Design result | Summary |",
        "| --- | --- | --- | --- |"]
    if result.get("previous_run"):
        lines[8:8] = [f"Previous run and its retained findings: [{result['previous_run']}](<{result['previous_run']}/REPORT.md>).", ""]
    for c in result.get("checks", []):
        lines.append(f"| {c['id']} | {c['execution']} | {c['outcome']} | {c['summary'].replace('|', '/')} |")
    rf = result.get("rf_review")
    if isinstance(rf, dict):
        from .rf_review import gate as rf_gate
        lines += ["", "## RF review applicability", "",
                  f"RF review {'required' if rf['required'] else 'not applicable'}: " + "; ".join(rf["basis"]) + ".",
                  f"Binding: `{rf['binding_sha256']}`; gate: {rf_gate(result) or ('satisfied' if rf['required'] else 'not required')}."]
        lines += ["- Missing RF evidence: " + m for m in rf.get("missing", [])]
    lines += ["", "## Initial reviews", ""]
    for r in result.get("review_executions", []):
        lines.append(f"- {r['role']}: {r['execution']}; requested model {r.get('model_requested', 'not invoked')}.")
    for review in result.get("reviews", []):
        lines += ["", f"{review['role']} coverage: " + "; ".join(review["coverage"])]
        for limitation in review["limitations"]:
            lines.append(f"- {limitation}")
    for record in result.get("purpose_coverage", []):
        lines.append(f"- {record['role']} purpose coverage: {record['status']}.")
    lines += ["", "## Findings and dispositions", ""]
    for f in result.get("findings", []):
        lines += [f"### {f['id']}: {f['title']}", "", f"Disposition: **{finding_status(f)}**.", "",
                  *(["Authority: advisory check; the defect is confirmed but witnesses no requirement.", ""]
                    if f.get("authority") == "advisory" else []),
                  *(["Purpose status: advisory; an estimated saving that does not hold the run outcome.", ""]
                    if f.get("purpose_status") == "advisory" else []),
                  f"Location: {f.get('location', '')}. Condition: {f.get('condition', '')}.", "",
                  f.get("mechanism", f.get("summary", "")), "", f.get("reasoning", "")]
        decision = f.get("decision", {})
        reason = f.get("reason", decision.get("reason", f.get("disposition_reason", "")))
        if reason:
            lines += ["", "Disposition reason: " + reason]
        for e in f.get("evidence", []):
            if isinstance(e, dict):
                lines.append(f"- [{e['path']}](<{e['path']}>): {e.get('locator', '')}")
        lines.append("")
    if not result.get("findings"):
        lines += ["No findings recorded. Acceptance still requires complete applicable execution and review.", ""]
    lines += ["## Limitations", ""]
    for limitation in result.get("limitations", []):
        lines.append("- " + limitation)
    for change in result.get("runtime_observations", []):
        lines.append(f"- Runtime observation: unused harness `{change['name']}` changed during the run; "
                     "it was not invoked, so the review was not refused.")
    if result.get("error"):
        lines += ["", "Run error: " + result["error"]]
    for path in sorted((run / "events").glob("*-correction.json")):
        event = read_json(path)
        lines += ["", f"Correction of `{event['finding_id']}` verified in candidate `{event['new_candidate_id']}`:",
                  f"{event['reason']} Fresh evidence: [{event['new_run']}](<{event['new_run']}/REPORT.md>).",
                  "The original finding remains a defect in this captured candidate."]
    lines += ["", "This result applies only to the captured candidate and declared criteria. Prototype and bench",
              "validation, including operating conditions omitted from this review, remain necessary.", ""]
    (run / "REPORT.md").write_text("\n".join(lines))


def save_result(run, result):
    result["outcome"] = outcome(result)
    result["artifact_hashes"] = artifacts(run)
    if (run / "candidate.json").exists():
        result["manifest_sha256"] = digest(run / "candidate.json")
    if (run / "packet.json").exists():
        result["packet_file_sha256"] = digest(run / "packet.json")
    write_json(run / "result.json", result)
    render(run, result)


def _recovered_execution(path, expected):
    """Read a review-execution record; a record cut off before it named its role or status is kept, not dropped.

    review-executions/<role>.json is written per role, so a record without ``role`` takes it from the file
    name, but only when that name is one of the scope's expected reviewer roles (``expected`` is None without a
    captured scope). A record whose file name or role is not expected is returned as ``(None, unexpected)`` so
    it is reported beside the executions and never satisfies a role. A record without ``execution`` is
    cancelled evidence, not a pass. The reconstruction mark is returned beside the record, not inside it, so
    the archived execution projection stays exactly what the executor wrote.
    """
    record = read_json(path)
    if "role" in record:
        recognized = record["role"] == path.stem if expected is None else record["role"] == path.stem in expected
    else:
        recognized = expected is not None and path.stem in expected
    if not recognized:
        return None, {"file": path.name, "role": record.get("role"),
                      "error": "review execution record does not belong to an expected reviewer role"}
    reconstructed = "role" not in record
    if reconstructed:
        record["role"] = path.stem
    if "execution" not in record:
        record["execution"] = "interrupted"
        record.setdefault("error", "run stopped before this review execution record was completed")
    return record, path.stem if reconstructed else None


def recover_records(run, result):
    """Project completed durable work after an exception or operator interrupt."""
    from .findings import collect_findings

    previous = {f["id"]: f for f in result.get("findings", [])}
    result["checks"] = [read_json(p) for p in sorted((run / "checks").glob("*.json"))]
    result["reviews"] = [read_json(p) for p in sorted((run / "reviews").glob("*.json"))]
    scope = read_json(run / "candidate.json")["scope"] if result.get("candidate_id") else None
    expected = ({r["role"] for r in scope.get("review_profile", {}).get("reviewers", [{"role": "circuit"}, {"role": "physical"}])}
                if scope else None)
    result["review_executions"] = []
    result["recovered_review_roles"] = []
    result["unexpected_review_executions"] = []
    for p in sorted((run / "review-executions").glob("*.json")):
        record, note = _recovered_execution(p, expected)
        if record is None:
            result["unexpected_review_executions"].append(note)
            continue
        result["review_executions"].append(record)
        if note:
            result["recovered_review_roles"].append(note)
    if scope:
        done = {c["id"] for c in result["checks"]}
        for spec in scope["checks"]:
            if spec["id"] in done:
                continue
            raw = run / "work" / spec["id"] / "execution.json"
            execution = read_json(raw).get("execution", "interrupted") if raw.exists() else "not_run"
            check = {"id": spec["id"], "kind": spec["kind"], "candidate_id": result["candidate_id"],
                "execution": execution if execution != "ok" else "interrupted", "outcome": "unknown",
                "summary": "run stopped before this check produced a validated result", "details": {},
                "evidence": [raw.relative_to(run).as_posix()] if raw.exists() else [], "limitations": []}
            result["checks"].append(check)
        done = {r["role"] for r in result["review_executions"]}
        for spec in scope.get("review_profile", {}).get("reviewers", [{"role": "circuit"}, {"role": "physical"}]):
            if spec["role"] not in done:
                raw = run / "review-work" / spec["role"] / "execution.json"
                execution = read_json(raw).get("execution", "interrupted") if raw.exists() else "not_run"
                result["review_executions"].append({"role": spec["role"],
                    "execution": execution if execution != "ok" else "interrupted",
                    "error": "run stopped before a validated review was saved"})
    result["findings"] = collect_findings(result["reviews"], result["checks"])
    for finding in result["findings"]:
        old = previous.get(finding["id"], {})
        for key in ("status", "disposition", "reason", "disposition_reason", "decision", "events", "duplicate_of", "duplicates",
                    "purpose_status"):
            if key in old:
                finding[key] = old[key]
    from .purpose_review import record_coverage
    record_coverage(result)


def validate_scope(scope):
    for field in ("name", "scope", "schematic", "board"):
        if not isinstance(scope.get(field), str) or not scope[field]:
            raise ValueError(f"scope requires nonempty {field}")
    for field in ("schematic", "board"):
        safe_relative(scope[field])
    for field in ("requirements", "packet_inputs", "packet_artifacts"):
        for rel in scope.get(field, []):
            safe_relative(rel)
    if ("block_provenance" in scope) != ("block_plan" in scope):
        raise ValueError("block_provenance and its block_plan are declared together")
    for field in ("block_provenance", "block_plan", "reserved_area"):
        if field in scope:
            if not isinstance(scope[field], str) or not scope[field]:
                raise ValueError(f"{field} must be a captured input-relative path")
            safe_relative(scope[field])
    limit = scope.get("packet_max_bytes", 240_000)
    if not isinstance(limit, int) or not 1024 <= limit <= 1_000_000:
        raise ValueError("packet_max_bytes must be an integer between 1024 and 1000000")
    if not scope.get("checks"):
        raise ValueError("scope declares no checks")
    seen = set()
    for check in scope["checks"]:
        cid = check.get("id", "")
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", cid) or cid in seen:
            raise ValueError(f"invalid or duplicate check ID: {cid!r}")
        if check.get("kind") == "rc_transient" and check.get("params", {}).get("netlist_check") not in seen:
            raise ValueError("rc_transient must name a preceding declared netlist check")
        if check.get("kind") == "active_spice":
            from .active_spice import validate_params
            try:
                params = validate_params(check.get("params", {}))
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"invalid active_spice declaration: {exc}") from exc
            if not any(c["id"] == params["netlist_check"] and c["kind"] == "netlist"
                       for c in scope["checks"] if c["id"] in seen):
                raise ValueError("active_spice must name a preceding native netlist check")
        seen.add(cid)
    seen = set()
    for reviewer in scope.get("review_profile", {}).get("reviewers", [{"role": "circuit"}, {"role": "physical"}]):
        role = reviewer.get("role", "")
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", role) or role == "adjudicator" or role in seen:
            raise ValueError(f"invalid or duplicate reviewer role: {role!r}")
        seen.add(role)


def run_review(scope_path, out, max_seconds, *, checks_only=False, previous=None, profile=None, expected_baseline=None, output_stream=None, max_schema_resubmissions=1, fab_context=None, stock_context=None, review_roles=None, routing_context=None, baseline_view=None, host=None):
    """Capture and review a fresh candidate into out. host, when given, holds the policy and runtime
    fingerprints the caller supplies for this run's observations and reviewer launches (see pinned)."""
    from .locking import writer_lock
    with writer_lock(out):
        return _run_review(scope_path, out, max_seconds, checks_only=checks_only,
                           previous=previous, profile=profile, expected_baseline=expected_baseline,
                           baseline_view=baseline_view, output_stream=output_stream,
                           max_schema_resubmissions=max_schema_resubmissions, fab_context=fab_context, stock_context=stock_context,
                           review_roles=review_roles, routing_context=routing_context, host=host)


def _run_review(scope_path, out, max_seconds, *, checks_only=False, previous=None, profile=None, expected_baseline=None, output_stream=None, max_schema_resubmissions=1, fab_context=None, stock_context=None, review_roles=None, routing_context=None, baseline_view=None, host=None):
    from .checks import run_checks
    from .findings import adjudicate, collect_findings
    from .reviewers import adjudicator_call, build_packet, initial_reviews

    from .review_run import resubmission_limit
    max_schema_resubmissions = resubmission_limit(max_schema_resubmissions)
    run = Path(out).resolve()
    if not math.isfinite(max_seconds) or not 0 <= max_seconds <= 86400:
        raise ValueError("max-seconds must be finite and between 0 and 86400")
    run.mkdir(parents=True, exist_ok=False)
    result = {"schema": "agent-ee.result/1", "run_id": str(uuid.uuid4()), "state": "running",
        "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "policy_files": pinned(host, "policy_files", policy_files),
        "runtime_files": pinned(host, "runtime_files", runtime_files),
        "checks": [], "reviews": [], "review_executions": [],
        "findings": [], "adjudicated": False, "limitations": [], "previous_run": previous,
        "max_schema_resubmissions": max_schema_resubmissions}
    deadline = time.monotonic() + max_seconds
    save_result(run, result)
    try:
        if result["policy_files"] != LOADED_POLICY_FILES:
            raise ValueError("implementation files changed after this process loaded; start a fresh process")
        m = capture(scope_path, run, profile)
        from .board_envelope import final_qualification
        # Final acceptance is held by the captured baseline's open envelope qualification (AGENTEE-197).
        if (qualification := final_qualification(run, m["candidate_id"])) is not None:
            result["final_qualification"] = qualification
        validate_scope(m["scope"])
        if expected_baseline is not None:
            from .baseline import load_baseline
            # A campaign may compare the candidate's sealed view (coordinator rating provenance removed).
            if (baseline_view or load_baseline)(run) != expected_baseline:
                raise ValueError("candidate changed the campaign baseline before verification")
        if m["scope"].get("review_profile"):
            from .profiles import validate_profile
            validate_profile(m["scope"]["review_profile"])
        for rel in [m["scope"]["schematic"], m["scope"]["board"], *m["scope"].get("requirements", [])]:
            if rel not in m["files"]:
                raise ValueError(f"declared native/requirement input was not captured: {rel}")
        from .candidate_visuals import capture_visuals
        imaging_started = time.monotonic()
        m = capture_visuals(run)
        # Advisory imaging has its own executor guard, outside the review budget.
        deadline += time.monotonic() - imaging_started
        result.update(candidate_id=m["candidate_id"], name=m["scope"]["name"], scope=m["scope"]["scope"],
                      limitations=m["scope"].get("limitations", []),
                      expected_check_ids=[c["id"] for c in m["scope"]["checks"]],
                      expected_review_roles=[r["role"] for r in m["scope"].get("review_profile", {}).get("reviewers",
                          [{"role": "circuit"}, {"role": "physical"}])])
        qualification = result.get("final_qualification")
        if qualification is not None and qualification["blocking"]:
            from .board_envelope import describe_blockers
            result["limitations"].append("Final qualification open in the captured envelope baseline; the "
                                         "outcome stays incomplete: " + describe_blockers(qualification["blocking"]))
        from . import rf_review
        # Candidate-bound routing adapter evidence for the RF review; absent or unverifiable is recorded so.
        rf_review.capture_routing(run, m, routing_context)
        result["rf_review"] = rf_review.decide_run(run, m)
        if previous and (Path(previous) / "result.json").is_file():
            result["rf_invalidation"] = rf_review.invalidation(read_json(Path(previous) / "result.json"),
                                                               result["rf_review"])
        if review_roles is not None:
            unknown = sorted(set(review_roles) - set(result["expected_review_roles"]))
            if not review_roles or unknown:
                raise ValueError(f"targeted review roles must name profile reviewers: {unknown or review_roles}")
            result["targeted_review_roles"] = list(review_roles)
            result["limitations"].append("Targeted review of roles " + ", ".join(review_roles) +
                                         " only; other roles and adjudication not run, so the result is incomplete.")
        from .preflight import capture_expectation, profile_definitions
        definitions = profile_definitions(m['scope'])
        result['launch_expectation'] = capture_expectation(result, m, definitions, inputs=run / 'inputs')
        launch_context = {'expected': result['launch_expectation'],
                          'max_schema_resubmissions': max_schema_resubmissions}
        if host is not None:
            launch_context['host'] = host
        save_result(run, result)
        if fab_context is None and stock_context is None:
            result["checks"] = run_checks(run, deadline)
        else:
            result["checks"] = run_checks(run, deadline, fab_context=fab_context, stock_context=stock_context)
        from .baseline import freeze_evidence
        freeze_evidence(run, result["checks"])
        result["findings"] = collect_findings([], result["checks"])
        save_result(run, result)
        verify_snapshot(run)
        rf_missing = (result["rf_review"]["required"] and
                      rf_review.ROLE not in result["expected_review_roles"])
        if rf_missing:
            result["limitations"].append("RF review required (" + "; ".join(result["rf_review"]["basis"]) +
                                         ") but the review profile declares no rf reviewer; reviews not dispatched.")
        if not checks_only and not rf_missing and time.monotonic() < deadline:
            from .profiles import validate_profile
            validate_profile(m["scope"].get("review_profile"))
            packet = build_packet(run, result["checks"], result["run_id"])
            result["limitations"] += packet["packet_limits"]
            result["reviews"], result["review_executions"] = initial_reviews(run, packet, deadline, launch_context=launch_context,
                                                                             roles=review_roles)
            result["findings"] = collect_findings(result["reviews"], result["checks"])
            from .purpose_review import record_coverage
            record_coverage(result)
            save_result(run, result)
            from .preflight import verify_calls
            verify_calls(run, result, m)
            if (review_roles is None and len(result["reviews"]) == len(result["expected_review_roles"]) and
                    time.monotonic() < deadline):
                execution, decisions = adjudicator_call(run, packet, result["findings"], result["reviews"], deadline, launch_context=launch_context)
                if decisions is not None:
                    try:
                        if {d["finding_id"] for d in decisions.get("decisions", [])} != {f["id"] for f in result["findings"]}:
                            raise ValueError("adjudicator did not disposition every finding")
                        result["findings"] = adjudicate(result["findings"], decisions, run)
                        event = {"at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "decisions": decisions,
                                 "method": "fresh subscription agent adjudication"}
                        write_json(run / "events/0001-adjudication.json", event)
                        result["adjudicated"] = True
                    except (ValueError, KeyError, TypeError) as exc:
                        execution.update(execution="malformed", error=str(exc))
                write_json(run / "adjudicator-execution.json", execution)
        from .preflight import verify_calls
        verify_calls(run, result, m)
        if checks_only:
            result["limitations"].append("Checks-only run: independent agent reviews and adjudication not performed.")
        if time.monotonic() >= deadline:
            result["limitations"].append("Wall-clock work budget exhausted; uncompleted work remains unknown.")
        verify_snapshot(run)
        verify_source(run)
        from .kicad_happy import verify_installations
        verify_installations(result["checks"])
        from .preflight import runtime_drift
        drift = runtime_drift(result["runtime_files"], pinned(host, "runtime_files", runtime_files), definitions)
        # A harness this profile never invokes is recorded, not a reason to discard the review.
        result["runtime_observations"] = drift["unused_changes"]
        if result["policy_files"] != pinned(host, "policy_files", policy_files) or drift["gated"]:
            raise ValueError("implementation or tool runtime changed during review; rerun required")
        reason = rf_review.gate(result)
        if reason and not checks_only:
            result["limitations"].append("RF review gate: " + reason)
        result["state"] = ("complete" if not checks_only and result["adjudicated"] and reason is None
                           and review_roles is None else "incomplete")
    except KeyboardInterrupt:
        result["state"] = "interrupted"
        result["error"] = "operator interrupted the run; useful work is preserved"
        recover_records(run, result)
    except Exception as exc:
        # This is the CLI crash boundary: preserve diagnostics and never turn a
        # parser, provider or implementation failure into design acceptance.
        result["state"] = "incomplete"
        result["error"] = f"{type(exc).__name__}: {exc}"
        (run / "error.log").write_text(traceback.format_exc())
        recover_records(run, result)
    result["finished_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    save_result(run, result)
    print(f"{result['outcome']}: {run / 'REPORT.md'}", file=output_stream)
    return 0 if result["outcome"] == "criteria_satisfied" else 1


def apply_decisions(run, decisions_path, *, host=None):
    from .locking import writer_lock
    with writer_lock(run):
        return _apply_decisions(run, decisions_path, host=host)


def _apply_decisions(run, decisions_path, *, host=None):
    from .findings import adjudicate
    run = Path(run).resolve()
    result = verify(run, host=host)
    decisions = read_json(decisions_path)
    updated = adjudicate(result["findings"], decisions, run)
    from .rf_review import gate as rf_gate
    event = run / "events" / (str(uuid.uuid4()) + "-adjudication.json")
    write_json(event, {"at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "decisions": decisions})
    result.update(findings=updated, adjudicated=True)
    if result["reviews"]:
        from .purpose_review import record_coverage
        record_coverage(result)
    if (len(result["reviews"]) >= 2 and all(c["execution"] == "ok" and c["outcome"] != "unknown"
            for c in result["checks"]) and all(r["execution"] == "ok" for r in result["review_executions"])
            and all(finding_status(f) != "unresolved" for f in updated)
            and not result.get("targeted_review_roles") and rf_gate(result) is None):
        result["state"] = "complete"
        if result.get("error"):
            result.setdefault("prior_errors", []).append(result.pop("error"))
    save_result(run, result)


def link_correction(previous, current, finding_id, check_ids, reason, *, host=None):
    """Record a verified correction of finding_id in previous; return the correction event."""
    from .locking import writer_lock
    with writer_lock(previous, current):
        return _link_correction(previous, current, finding_id, check_ids, reason, host=host)


def _link_correction(previous, current, finding_id, check_ids, reason, *, host=None):
    previous, current = Path(previous).resolve(), Path(current).resolve()
    old, new = verify(previous, current_source=False, host=host), verify(current, host=host)
    if old["candidate_id"] == new["candidate_id"]:
        raise ValueError("closure requires a changed saved candidate")
    source = next((f for f in old["findings"] if f["id"] == finding_id), None)
    if source is None or finding_status(source) != "accepted":
        raise ValueError("closure requires an accepted finding")
    om, nm = read_json(previous / "candidate.json"), read_json(current / "candidate.json")
    if om["files"] == nm["files"]:
        raise ValueError("closure requires changed captured input bytes, not only a reviewer profile or scope identity")
    if om["scope"]["checks"] != nm["scope"]["checks"]:
        raise ValueError("verification criteria changed; cannot silently close under different rules")
    if om["scope"].get("requirements", []) != nm["scope"].get("requirements", []):
        raise ValueError("requirement declaration changed; closure under original criteria refused")
    for req in om["scope"].get("requirements", []):
        if req not in om["files"] or om["files"][req] != nm["files"].get(req):
            raise ValueError("requirements changed; closure under original criteria refused")
    if new["outcome"] != "criteria_satisfied":
        raise ValueError("new review has not satisfied its declared criteria")
    from .rf_review import changed_categories, closure_gate
    closure_gate(source, old, new)
    checks = {c["id"]: c for c in new["checks"]}
    if not check_ids or any(cid not in checks or checks[cid]["execution"] != "ok" or
                            checks[cid]["outcome"] != "pass" for cid in check_ids):
        raise ValueError("affected closure checks must all execute and pass freshly")
    event = {"schema": "agent-ee.correction/1", "finding_id": finding_id, "reason": reason,
        "old_candidate_id": old["candidate_id"], "new_candidate_id": new["candidate_id"],
        "old_run_id": old["run_id"], "new_run_id": new["run_id"], "new_run": str(current),
        "checks": check_ids, "new_result_sha256": digest(current / "result.json")}
    event["rf_changed_categories"] = changed_categories(old.get("rf_review"), new.get("rf_review"))
    event["changed_inputs"] = {p: {"before": om["files"].get(p), "after": nm["files"].get(p)}
        for p in sorted(om["files"].keys() | nm["files"].keys()) if om["files"].get(p) != nm["files"].get(p)}
    write_json(previous / "events" / (str(uuid.uuid4()) + "-correction.json"), event)
    save_result(previous, old)
    return event


def inspect(run, *, host=None):
    """Return the inspect projection: the stored outcome, its freshness and each linked correction's freshness."""
    run = Path(run).resolve()
    result = read_json(run / "result.json")
    if result["state"] == "running":
        recover_records(run, result)
    try:
        verify(run, host=host)
        freshness = "current"
    except (ValueError, OSError) as exc:
        freshness = str(exc)
    corrections = []
    for p in sorted((run / "events").glob("*-correction.json")):
        event = read_json(p)
        target = Path(event["new_run"])
        try:
            verify(target, host=host)
            event["linked_evidence_freshness"] = ("current" if digest(target / "result.json") ==
                event["new_result_sha256"] else "linked result changed since closure")
        except (ValueError, OSError) as exc:
            event["linked_evidence_freshness"] = str(exc)
        corrections.append(event)
    return {"outcome": result["outcome"], "state": result["state"],
        "candidate_id": result.get("candidate_id"), "freshness": freshness,
        "checks": result.get("checks", []), "findings": result.get("findings", []),
        "corrections": corrections}

