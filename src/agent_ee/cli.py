"""CLI lifecycle. Interrupted runs preserve records; resumes always make fresh runs."""

import argparse
import datetime
import json
import math
import re
import shutil
import subprocess
import sys
import time
import traceback
import uuid
from pathlib import Path

from .candidate import capture, safe_relative, verify_snapshot, verify_source
from .storage import digest, identity, read_json, write_json


def policy_files():
    root = Path(__file__).resolve().parent
    return {p.name: digest(p) for p in sorted(root.glob("*.py"))}


LOADED_POLICY_FILES = policy_files()


def runtime_files():
    files = {}
    for name in ("kicad-cli", "ngspice", "bwrap", "/usr/bin/python3", "claude"):
        path = shutil.which(name)
        files[name] = {"path": path, "sha256": digest(path) if path else None}
    from .harnesses import executable_for
    for name in ("claude", "codex", "agy"):
        path = executable_for(name)
        files[name] = {"path": path, "sha256": digest(path) if path and Path(path).is_file() else None}
    native_codex = executable_for("codex")
    host = Path(native_codex).parent / "codex-code-mode-host" if native_codex else None
    files["codex_tool_host"] = {"path": str(host) if host and host.is_file() else None,
        "sha256": digest(host) if host and host.is_file() else None}
    path, diagnostic = pcbnew_extension()
    if path:
        files["pcbnew"] = {"path": path, "sha256": digest(path)}
    else:
        files["pcbnew"] = {"path": None, "sha256": None, "diagnostic": diagnostic}
    return files


def pcbnew_extension():
    """Return (path, diagnostic) for KiCad's native _pcbnew extension module."""
    # pcbnew shares a large native extension rather than a standalone CLI.
    probe = subprocess.run(["/usr/bin/python3", "-c", "import _pcbnew; print(_pcbnew.__file__)"],
                           capture_output=True, text=True, timeout=15)
    return (probe.stdout.strip(), "") if probe.returncode == 0 else (None, probe.stderr)


DOCTOR_PROBES = {"kicad-cli": ["kicad-cli", "version"], "ngspice": ["ngspice", "--version"],
    "bwrap": ["bwrap", "--version"], "python3": ["/usr/bin/python3", "--version"],
    "pcbnew": ["/usr/bin/python3", "-c", "import pcbnew; print(pcbnew.GetBuildVersion())"],
    "claude": ["claude", "--version"]}
HARNESS_PROBES = ("claude",)


def doctor(checks_only=False):
    """Probe versions and hash the probed binaries; real runs establish integration."""
    checks, skipped = {}, {}
    for name, command in DOCTOR_PROBES.items():
        if checks_only and name in HARNESS_PROBES:
            skipped[name] = "not probed with --checks-only; reviews and adjudication still require it"
            continue
        try:
            p = subprocess.run(command, capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired) as exc:
            checks[name] = {"error": str(exc)}
            continue
        record = {"path": shutil.which(command[0]), "exit_code": p.returncode,
                  "version_output": p.stdout.strip(), "diagnostics": p.stderr.strip()}
        # The hashed file is the one runtime_files() fingerprints in each result.
        try:
            record["binary"] = pcbnew_extension()[0] if name == "pcbnew" else record["path"]
            record["sha256"] = digest(record["binary"]) if record["binary"] else None
        except (OSError, subprocess.TimeoutExpired) as exc:
            record.update(sha256=None, hash_error=str(exc))
        record.setdefault("binary", None)
        checks[name] = record
    report = {"prerequisites": checks,
        "note": "Presence/version probes do not establish engineering integration. Run a declared scope."}
    if skipped:
        report["skipped"] = skipped
    print(json.dumps(report, indent=2))
    return 0 if all(c.get("exit_code") == 0 and c.get("sha256") for c in checks.values()) else 1


def artifacts(run):
    found = {p.relative_to(run).as_posix(): digest(p) for parent in
            ("inputs", "work", "checks", "reviews", "review-work", "review-executions")
            for p in sorted((run / parent).rglob("*")) if p.is_file()}
    for p in (run / "events").glob("*.json"):
        found[p.relative_to(run).as_posix()] = digest(p)
    if (run / "adjudicator-execution.json").is_file():
        found["adjudicator-execution.json"] = digest(run / "adjudicator-execution.json")
    return found


def verify(run, *, current_source=True):
    run = Path(run).resolve()
    result = read_json(run / "result.json")
    manifest = verify_source(run) if current_source else verify_snapshot(run)
    if manifest["candidate_id"] != result.get("candidate_id"):
        raise ValueError("result candidate identity differs from snapshot")
    if result.get("policy_files") != policy_files():
        raise ValueError("review implementation changed; rerun under the current rules")
    if result.get("runtime_files") != runtime_files():
        raise ValueError("tool runtime changed; rerun under the current tools")
    if artifacts(run) != result.get("artifact_hashes"):
        raise ValueError("execution/review evidence changed; prior acceptance is stale")
    if digest(run / "candidate.json") != result.get("manifest_sha256"):
        raise ValueError("candidate manifest changed")
    if (run / "packet.json").exists() and digest(run / "packet.json") != result.get("packet_file_sha256"):
        raise ValueError("review evidence packet changed")
    from .baseline import verify_evidence
    verify_evidence(run, result["checks"], required=result["state"] == "complete")
    for spec in manifest["scope"].get("checks", []):
        tool = spec.get("params", {}).get("tool")
        if spec.get("kind") == "measurements":
            if not isinstance(tool, dict) or not tool.get("argv") or not tool.get("sha256"):
                raise ValueError("measurement check lacks a pinned tool declaration")
            if digest(tool["argv"][0]) != tool["sha256"]:
                raise ValueError("declared measurement tool changed since review")
    if result.get("outcome") != outcome(result):
        raise ValueError("stored result contradicts its execution and finding records")
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
    # A disposition cannot silently change a declared executable criterion.
    if any(c["outcome"] == "defect" for c in result.get("checks", [])):
        return "defects_or_unresolved"
    statuses = [finding_status(f) for f in result.get("findings", [])]
    if any(s in ("accepted", "unresolved", "deferred") for s in statuses):
        return "defects_or_unresolved"
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
    lines += ["", "## Initial reviews", ""]
    for r in result.get("review_executions", []):
        lines.append(f"- {r['role']}: {r['execution']}; requested model {r.get('model_requested', 'not invoked')}.")
    for review in result.get("reviews", []):
        lines += ["", f"{review['role']} coverage: " + "; ".join(review["coverage"])]
        for limitation in review["limitations"]:
            lines.append(f"- {limitation}")
    lines += ["", "## Findings and dispositions", ""]
    for f in result.get("findings", []):
        lines += [f"### {f['id']}: {f['title']}", "", f"Disposition: **{finding_status(f)}**.", "",
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


def recover_records(run, result):
    """Project completed durable work after an exception or operator interrupt."""
    from .findings import collect_findings

    previous = {f["id"]: f for f in result.get("findings", [])}
    result["checks"] = [read_json(p) for p in sorted((run / "checks").glob("*.json"))]
    result["reviews"] = [read_json(p) for p in sorted((run / "reviews").glob("*.json"))]
    result["review_executions"] = [read_json(p) for p in sorted((run / "review-executions").glob("*.json"))]
    if result.get("candidate_id"):
        scope = read_json(run / "candidate.json")["scope"]
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
        for key in ("status", "disposition", "reason", "disposition_reason", "decision", "events", "duplicate_of", "duplicates"):
            if key in old:
                finding[key] = old[key]


def validate_scope(scope):
    for field in ("name", "scope", "schematic", "board"):
        if not isinstance(scope.get(field), str) or not scope[field]:
            raise ValueError(f"scope requires nonempty {field}")
    for field in ("schematic", "board"):
        safe_relative(scope[field])
    for field in ("requirements", "packet_inputs", "packet_artifacts"):
        for rel in scope.get(field, []):
            safe_relative(rel)
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
        seen.add(cid)
    seen = set()
    for reviewer in scope.get("review_profile", {}).get("reviewers", [{"role": "circuit"}, {"role": "physical"}]):
        role = reviewer.get("role", "")
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", role) or role == "adjudicator" or role in seen:
            raise ValueError(f"invalid or duplicate reviewer role: {role!r}")
        seen.add(role)


def run_review(scope_path, out, max_seconds, *, checks_only=False, previous=None, profile=None, expected_baseline=None):
    from .locking import writer_lock
    with writer_lock(out):
        return _run_review(scope_path, out, max_seconds, checks_only=checks_only,
                           previous=previous, profile=profile, expected_baseline=expected_baseline)


def _run_review(scope_path, out, max_seconds, *, checks_only=False, previous=None, profile=None, expected_baseline=None):
    from .checks import run_checks
    from .findings import adjudicate, collect_findings
    from .reviewers import adjudicator_call, build_packet, initial_reviews

    run = Path(out).resolve()
    if not math.isfinite(max_seconds) or not 0 <= max_seconds <= 86400:
        raise ValueError("max-seconds must be finite and between 0 and 86400")
    run.mkdir(parents=True, exist_ok=False)
    result = {"schema": "agent-ee.result/1", "run_id": str(uuid.uuid4()), "state": "running",
        "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "policy_files": policy_files(), "runtime_files": runtime_files(),
        "checks": [], "reviews": [], "review_executions": [],
        "findings": [], "adjudicated": False, "limitations": [], "previous_run": previous}
    deadline = time.monotonic() + max_seconds
    save_result(run, result)
    try:
        if result["policy_files"] != LOADED_POLICY_FILES:
            raise ValueError("implementation files changed after this process loaded; start a fresh process")
        m = capture(scope_path, run, profile)
        validate_scope(m["scope"])
        if expected_baseline is not None:
            from .baseline import load_baseline
            if load_baseline(run) != expected_baseline:
                raise ValueError("candidate changed the campaign baseline before verification")
        if m["scope"].get("review_profile"):
            from .profiles import validate_profile
            validate_profile(m["scope"]["review_profile"])
        for rel in [m["scope"]["schematic"], m["scope"]["board"], *m["scope"].get("requirements", [])]:
            if rel not in m["files"]:
                raise ValueError(f"declared native/requirement input was not captured: {rel}")
        result.update(candidate_id=m["candidate_id"], name=m["scope"]["name"], scope=m["scope"]["scope"],
                      limitations=m["scope"].get("limitations", []),
                      expected_check_ids=[c["id"] for c in m["scope"]["checks"]],
                      expected_review_roles=[r["role"] for r in m["scope"].get("review_profile", {}).get("reviewers",
                          [{"role": "circuit"}, {"role": "physical"}])])
        save_result(run, result)
        result["checks"] = run_checks(run, deadline)
        from .baseline import freeze_evidence
        freeze_evidence(run, result["checks"])
        result["findings"] = collect_findings([], result["checks"])
        save_result(run, result)
        verify_snapshot(run)
        if not checks_only and time.monotonic() < deadline:
            from .profiles import validate_profile
            validate_profile(m["scope"].get("review_profile"))
            packet = build_packet(run, result["checks"], result["run_id"])
            result["limitations"] += packet["packet_limits"]
            result["reviews"], result["review_executions"] = initial_reviews(run, packet, deadline)
            result["findings"] = collect_findings(result["reviews"], result["checks"])
            save_result(run, result)
            if len(result["reviews"]) >= 2 and time.monotonic() < deadline:
                execution, decisions = adjudicator_call(run, packet, result["findings"], result["reviews"], deadline)
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
        if checks_only:
            result["limitations"].append("Checks-only run: independent agent reviews and adjudication not performed.")
        if time.monotonic() >= deadline:
            result["limitations"].append("Wall-clock work budget exhausted; uncompleted work remains unknown.")
        verify_snapshot(run)
        verify_source(run)
        if result["policy_files"] != policy_files() or result["runtime_files"] != runtime_files():
            raise ValueError("implementation or tool runtime changed during review; rerun required")
        result["state"] = "complete" if not checks_only and result["adjudicated"] else "incomplete"
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
    print(f"{result['outcome']}: {run / 'REPORT.md'}")
    return 0 if result["outcome"] == "criteria_satisfied" else 1


def apply_decisions(run, decisions_path):
    from .locking import writer_lock
    with writer_lock(run):
        return _apply_decisions(run, decisions_path)


def _apply_decisions(run, decisions_path):
    from .findings import adjudicate
    run = Path(run).resolve()
    result = verify(run)
    decisions = read_json(decisions_path)
    updated = adjudicate(result["findings"], decisions, run)
    event = run / "events" / (str(uuid.uuid4()) + "-adjudication.json")
    write_json(event, {"at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "decisions": decisions})
    result.update(findings=updated, adjudicated=True)
    if (len(result["reviews"]) >= 2 and all(c["execution"] == "ok" and c["outcome"] != "unknown"
            for c in result["checks"]) and all(r["execution"] == "ok" for r in result["review_executions"])
            and all(finding_status(f) != "unresolved" for f in updated)):
        result["state"] = "complete"
        if result.get("error"):
            result.setdefault("prior_errors", []).append(result.pop("error"))
    save_result(run, result)


def link_correction(previous, current, finding_id, check_ids, reason):
    from .locking import writer_lock
    with writer_lock(previous, current):
        return _link_correction(previous, current, finding_id, check_ids, reason)


def _link_correction(previous, current, finding_id, check_ids, reason):
    previous, current = Path(previous).resolve(), Path(current).resolve()
    old, new = verify(previous, current_source=False), verify(current)
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
    checks = {c["id"]: c for c in new["checks"]}
    if not check_ids or any(cid not in checks or checks[cid]["execution"] != "ok" or
                            checks[cid]["outcome"] != "pass" for cid in check_ids):
        raise ValueError("affected closure checks must all execute and pass freshly")
    event = {"schema": "agent-ee.correction/1", "finding_id": finding_id, "reason": reason,
        "old_candidate_id": old["candidate_id"], "new_candidate_id": new["candidate_id"],
        "old_run_id": old["run_id"], "new_run_id": new["run_id"], "new_run": str(current),
        "checks": check_ids, "new_result_sha256": digest(current / "result.json")}
    event["changed_inputs"] = {p: {"before": om["files"].get(p), "after": nm["files"].get(p)}
        for p in sorted(om["files"].keys() | nm["files"].keys()) if om["files"].get(p) != nm["files"].get(p)}
    write_json(previous / "events" / (str(uuid.uuid4()) + "-correction.json"), event)
    save_result(previous, old)
    print(json.dumps(event, indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    doctor_parser = sub.add_parser("doctor", help="inspect installed prerequisites; real runs establish integration")
    doctor_parser.add_argument("--checks-only", action="store_true",
                               help="probe only the native check toolchain, not reviewer harnesses")
    bench = sub.add_parser("bench-review", help="capture bench evidence and rerun calibrated checks/reviews")
    bench.add_argument("run")
    bench.add_argument("report")
    bench.add_argument("--out", required=True)
    bench.add_argument("--max-seconds", type=float, default=1200)
    feedback = sub.add_parser("campaign-feedback", help="ingest bench evidence and continue a campaign in a new model epoch")
    feedback.add_argument("campaign")
    feedback.add_argument("report")
    feedback.add_argument("--out", required=True)
    feedback.add_argument("--max-seconds", type=float, default=1200)
    campaign_verify = sub.add_parser("verify-campaign", help="verify all candidate history links and convergence state")
    campaign_verify.add_argument("campaign")
    campaign = sub.add_parser("campaign", help="run a bounded single-writer implementation/review campaign")
    campaign.add_argument("config")
    campaign.add_argument("--out", required=True)
    review = sub.add_parser("review", help="capture and review a fresh candidate")
    review.add_argument("scope")
    review.add_argument("--out", required=True)
    review.add_argument("--max-seconds", type=float, default=1200)
    review.add_argument("--checks-only", action="store_true")
    review.add_argument("--profile", help="explicit agent-ee.profile/1 JSON file")
    for cmd in ("inspect", "verify"):
        p = sub.add_parser(cmd)
        p.add_argument("run")
        if cmd == "verify":
            p.add_argument("--snapshot-only", action="store_true")
    resume = sub.add_parser("resume", help="preserve old run; recapture/rerun all evidence into new run")
    resume.add_argument("run")
    resume.add_argument("--out", required=True)
    resume.add_argument("--max-seconds", type=float, default=1200)
    decision = sub.add_parser("adjudicate")
    decision.add_argument("run")
    decision.add_argument("decisions")
    closure = sub.add_parser("close-correction")
    closure.add_argument("previous")
    closure.add_argument("current")
    closure.add_argument("--finding", required=True)
    closure.add_argument("--checks", required=True)
    closure.add_argument("--reason", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            return doctor(checks_only=args.checks_only)
        if args.command == "bench-review":
            from .empirical import bench_review
            return bench_review(args.run, args.report, args.out, args.max_seconds)
        if args.command == "campaign-feedback":
            from .campaign import campaign_feedback
            return campaign_feedback(args.campaign, args.report, args.out, args.max_seconds)
        if args.command == "verify-campaign":
            from .campaign import verify_campaign
            state = verify_campaign(args.campaign)
            print(state["state"])
            return 0
        if args.command == "campaign":
            from .campaign import run_campaign
            return run_campaign(args.config, args.out)
        if args.command == "review":
            return run_review(args.scope, args.out, args.max_seconds, checks_only=args.checks_only, profile=args.profile)
        if args.command == "inspect":
            run = Path(args.run).resolve()
            result = read_json(run / "result.json")
            if result["state"] == "running":
                recover_records(run, result)
            try:
                verify(run)
                freshness = "current"
            except (ValueError, OSError) as exc:
                freshness = str(exc)
            corrections = []
            for p in sorted((run / "events").glob("*-correction.json")):
                event = read_json(p)
                target = Path(event["new_run"])
                try:
                    verify(target)
                    event["linked_evidence_freshness"] = ("current" if digest(target / "result.json") ==
                        event["new_result_sha256"] else "linked result changed since closure")
                except (ValueError, OSError) as exc:
                    event["linked_evidence_freshness"] = str(exc)
                corrections.append(event)
            print(json.dumps({"outcome": result["outcome"], "state": result["state"],
                "candidate_id": result.get("candidate_id"), "freshness": freshness,
                "checks": result.get("checks", []), "findings": result.get("findings", []),
                "corrections": corrections}, indent=2))
        elif args.command == "verify":
            result = verify(args.run, current_source=not args.snapshot_only)
            print(f"fresh {result['outcome']}: {result['candidate_id']}")
        elif args.command == "resume":
            old = read_json(Path(args.run) / "candidate.json")
            return run_review(old["scope_path"], args.out, args.max_seconds, previous=str(Path(args.run).resolve()), profile=old.get("profile_path"))
        elif args.command == "adjudicate":
            apply_decisions(args.run, args.decisions)
        elif args.command == "close-correction":
            link_correction(args.previous, args.current, args.finding, args.checks.split(","), args.reason)
        return 0
    except (ValueError, OSError, KeyError) as exc:
        print(f"agent-ee: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
