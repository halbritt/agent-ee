"""CLI lifecycle. Interrupted runs preserve records; resumes always make fresh runs."""

import argparse
import datetime
import json
import math
import os
import re
import shutil
import signal
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


def runtime_files(*, checkpoint=None):
    def fingerprint(path):
        if checkpoint is None:
            return digest(path)
        import hashlib
        value = hashlib.sha256()
        with Path(path).open('rb') as stream:
            while True:
                checkpoint()
                block = stream.read(1024 * 1024)
                if not block:
                    return value.hexdigest()
                value.update(block)

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
    return files


def pcbnew_extension(*, checkpoint=None):
    """Return (path, diagnostic) for KiCad's native _pcbnew extension module."""
    # pcbnew shares a large native extension rather than a standalone CLI.
    argv = ['/usr/bin/python3', '-c', 'import _pcbnew; print(_pcbnew.__file__)']
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


DOCTOR_PROBES = {"kicad-cli": ["kicad-cli", "version"], "ngspice": ["ngspice", "--version"],
    "bwrap": ["bwrap", "--version"], "python3": ["/usr/bin/python3", "--version"],
    "pcbnew": ["/usr/bin/python3", "-c", "import pcbnew; print(pcbnew.GetBuildVersion())"],
    "claude": ["claude", "--version"]}
HARNESS_PROBES = ("claude",)


def doctor(checks_only=False):
    """Probe versions and hash the probed binaries; real runs establish integration."""
    checks, skipped = {}, {}
    probes = dict(DOCTOR_PROBES)
    if shutil.which("pdftotext"):
        probes["pdftotext"] = ["pdftotext", "-v"]
    for name, command in probes.items():
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
    # Optional installed analyzers are tools, not core package dependencies.
    analyzer = os.environ.get("AGENT_EE_KICAD_HAPPY")
    if analyzer:
        from .kicad_happy import installation
        try:
            installed = installation(Path(analyzer).expanduser().resolve())
            checks["kicad-happy"] = {"path": analyzer, "exit_code": 0, **installed}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            checks["kicad-happy"] = {"path": analyzer, "error": str(exc)}
    report = {"prerequisites": checks,
        "note": "Presence/version probes do not establish engineering integration. Run a declared scope."}
    if skipped:
        report["skipped"] = skipped
    print(json.dumps(report, indent=2))
    return 0 if all(c.get("exit_code") == 0 and c.get("sha256") for c in checks.values()) else 1


def artifacts(run):
    found = {p.relative_to(run).as_posix(): digest(p) for parent in
            ("inputs", "work", "checks", "reviews", "review-work", "review-executions", "review-attempts")
            for p in sorted((run / parent).rglob("*")) if p.is_file()}
    for p in (run / "events").glob("*.json"):
        found[p.relative_to(run).as_posix()] = digest(p)
    if (run / "adjudicator-execution.json").is_file():
        found["adjudicator-execution.json"] = digest(run / "adjudicator-execution.json")
    return found


def verify(run, *, current_source=True):
    run = Path(run).resolve()
    from .locking import reader_lock
    from .bench_transactions import verify_publications
    with reader_lock(run):
        result = read_json(run / "result.json")
        verify_publications(run, result)
        return verify_records(run, result, current_source=current_source)


def verify_records(run, result, *, current_source=True):
    """Validate an explicit projection; callers separately bind publication intent."""
    manifest = verify_source(run) if current_source else verify_snapshot(run)
    if manifest["candidate_id"] != result.get("candidate_id"):
        raise ValueError("result candidate identity differs from snapshot")
    if result.get("policy_files") != policy_files():
        raise ValueError("review implementation changed; rerun under the current rules")
    if result.get("runtime_files") != runtime_files():
        raise ValueError("tool runtime changed; rerun under the current tools")
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
    verify_navigation(run, manifest, load_baseline(run), result["checks"], result["run_id"])
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


def run_review(scope_path, out, max_seconds, *, checks_only=False, previous=None, profile=None, expected_baseline=None, output_stream=None, max_schema_resubmissions=0):
    from .locking import writer_lock
    with writer_lock(out):
        return _run_review(scope_path, out, max_seconds, checks_only=checks_only,
                           previous=previous, profile=profile, expected_baseline=expected_baseline, output_stream=output_stream,
                           max_schema_resubmissions=max_schema_resubmissions)


def _run_review(scope_path, out, max_seconds, *, checks_only=False, previous=None, profile=None, expected_baseline=None, output_stream=None, max_schema_resubmissions=0):
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
        "policy_files": policy_files(), "runtime_files": runtime_files(),
        "checks": [], "reviews": [], "review_executions": [],
        "findings": [], "adjudicated": False, "limitations": [], "previous_run": previous,
        "max_schema_resubmissions": max_schema_resubmissions}
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
        from .preflight import capture_expectation
        selected = m['scope'].get('review_profile', {})
        definitions = [{**d, 'tool_mode': selected.get('tool_mode', 'investigate')}
                       for d in [*selected.get('reviewers', []), *([selected['adjudicator']] if selected.get('adjudicator') else [])]]
        result['launch_expectation'] = capture_expectation(result, m, definitions, inputs=run / 'inputs')
        launch_context = {'expected': result['launch_expectation'],
                          'max_schema_resubmissions': max_schema_resubmissions}
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
            result["reviews"], result["review_executions"] = initial_reviews(run, packet, deadline, launch_context=launch_context)
            result["findings"] = collect_findings(result["reviews"], result["checks"])
            save_result(run, result)
            from .preflight import verify_calls
            verify_calls(run, result, m)
            if len(result["reviews"]) == len(result["expected_review_roles"]) and time.monotonic() < deadline:
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
    print(f"{result['outcome']}: {run / 'REPORT.md'}", file=output_stream)
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


def view3d(source, out, *, port=0, no_browser=False, export_only=False):
    """Build a separate preview and keep its loopback server alive until Ctrl-C."""
    if not 0 <= port <= 65535:
        raise ValueError("viewer port must be between 0 and 65535")
    from .viewer import build_viewer, serve_viewer

    print("Exporting the board with KiCad…", flush=True)
    page = build_viewer(source, out)
    print(f"Viewer bundle: {page.parent}", flush=True)
    if export_only:
        return 0
    import webbrowser

    with serve_viewer(page.parent, port=port) as server:
        url = f"http://127.0.0.1:{server.server_address[1]}/"
        print(f"3D PCB viewer: {url}\nPress Ctrl-C to stop.", flush=True)
        if not no_browser:
            try:
                if not webbrowser.open(url):
                    print("Open the URL above in your browser.", file=sys.stderr)
            except (OSError, webbrowser.Error) as exc:
                print(f"Could not open a browser: {exc}. Open the URL above.", file=sys.stderr)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


def render3d(args):
    """Render a complete native three-view bundle without starting a browser."""
    from .render3d import render_board

    try:
        manifest = render_board(args.source, args.out, width=args.width,
            height=args.height, quality=args.quality, jobs=args.jobs,
            timeout=args.timeout, models=args.models, fetch_models=args.fetch_models,
            progress=lambda message: print(message, flush=True))
    except KeyboardInterrupt:
        print("agent-ee: rendering interrupted; inspect the retained output logs", file=sys.stderr)
        return 130
    print(f"3D render manifest: {manifest}", flush=True)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    scaffold_parser = sub.add_parser('scaffold', help='create an empty native candidate from captured planning inputs')
    scaffold_parser.add_argument('envelope')
    scaffold_parser.add_argument('parts')
    scaffold_parser.add_argument('--scope', required=True, help='stage-one scope/2 or scope/3 with captured project rules')
    scaffold_parser.add_argument('--out', required=True)
    catalog = sub.add_parser('capture-catalog', help='prepare a pinned local catalog; no upstream authentication')
    catalog.add_argument('manifest')
    catalog.add_argument('--source-root', required=True)
    catalog.add_argument('--out', required=True)
    viewer = sub.add_parser("view3d", help="open a local 3D preview of a KiCad board or captured review")
    viewer.add_argument("source", help=".kicad_pcb file or retained review directory")
    viewer.add_argument("--out", required=True, help="new directory for the exported viewer bundle")
    viewer.add_argument("--port", type=int, default=0, help="loopback HTTP port (default: choose a free port)")
    viewer.add_argument("--no-browser", action="store_true", help="print the URL without opening a browser")
    viewer.add_argument("--export-only", action="store_true", help="build the bundle without starting a server")
    renders = sub.add_parser("render3d", help="ray trace top, bottom and isometric PCB images with native KiCad")
    renders.add_argument("source", help=".kicad_pcb file or retained review directory")
    renders.add_argument("--out", required=True, help="new directory for images, snapshots, logs and manifest")
    renders.add_argument("--width", type=int, default=4096, help="requested pixel width (default: 4096)")
    renders.add_argument("--height", type=int, default=3072, help="requested pixel height (default: 3072)")
    renders.add_argument("--quality", choices=("basic", "high"), default="high", help="native ray-tracing quality (default: high)")
    renders.add_argument("--jobs", type=int, choices=(1, 2, 3), default=3, help="concurrent views (default: 3)")
    renders.add_argument("--timeout", type=float, default=1200, help="maximum seconds per view (default: 1200)")
    renders.add_argument("--models", help="local stock-model directory; maps KiCad model-library variables")
    renders.add_argument("--fetch-models", action="store_true", help="fetch referenced stock models into --models from pinned official KiCad sources")
    request = sub.add_parser("evidence-request", help="export an inert request from a retained concern")
    request.add_argument("run")
    request.add_argument("--finding", required=True)
    request.add_argument("--spec", required=True)
    request.add_argument("--script")
    request.add_argument("--out", required=True)
    replay = sub.add_parser("evidence-replay", help="networkless nonauthoritative Python experiment")
    replay.add_argument("request")
    replay.add_argument("--out", required=True)
    graduation = sub.add_parser("evidence-graduation", help="export an inert unaccepted check proposal")
    graduation.add_argument("request")
    graduation.add_argument("--proposal", required=True)
    graduation.add_argument("--replay")
    graduation.add_argument("--out", required=True)
    operation = sub.add_parser("verify-evidence-operation", help="verify standalone operation bytes, not acceptance")
    operation.add_argument("path")
    doctor_parser = sub.add_parser("doctor", help="inspect installed prerequisites; real runs establish integration")
    doctor_parser.add_argument("--checks-only", action="store_true",
                               help="probe only the native check toolchain, not reviewer harnesses")
    bench = sub.add_parser("bench-review", help="capture bench evidence and rerun calibrated checks/reviews")
    bench.add_argument("run")
    bench.add_argument("report")
    bench.add_argument("--out", required=True)
    bench.add_argument("--max-seconds", type=float, default=1200)
    bench.add_argument("--operation-id", help="opt into durable bench-transaction/1")
    for command in ('bench-status', 'bench-recover', 'bench-launch'):
        transaction = sub.add_parser(command, help='inspect, reconcile, or explicitly launch a prepared bench transaction')
        transaction.add_argument('path')
    feedback = sub.add_parser("campaign-feedback", help="ingest bench evidence and continue a campaign in a new model epoch")
    feedback.add_argument("campaign")
    feedback.add_argument("report")
    feedback.add_argument("--out", required=True)
    feedback.add_argument("--max-seconds", type=float, default=1200)
    feedback.add_argument("--operation-id", help="bounded durable feedback; no automatic repair continuation")
    attempt = sub.add_parser('bench-new-attempt', help='explicitly authorize a fresh review after uncertain execution')
    attempt.add_argument('path')
    attempt.add_argument('--attempt-id', required=True)
    attempt.add_argument('--out', required=True)
    diagnosis = sub.add_parser("diagnose", help="read-only advisory run or campaign diagnosis")
    diagnosis.add_argument("path")
    diagnosis.add_argument("--json", action="store_true", dest="machine")
    diagnosis.add_argument("--snapshot-only", action="store_true")
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
        if args.command == 'scaffold':
            from .scaffold import scaffold
            receipt = scaffold(args.envelope, args.parts, args.out, scope_path=args.scope)
            print(receipt['candidate_id'])
            return 0
        if args.command == 'capture-catalog':
            from .catalog import capture_catalog, strict_json
            capture_catalog(strict_json(Path(args.manifest).read_text(encoding='utf-8')), args.source_root, args.out)
            print(digest(Path(args.out) / 'manifest.json'))
            return 0
        if args.command == "render3d":
            return render3d(args)
        if args.command == "view3d":
            return view3d(args.source, args.out, port=args.port,
                          no_browser=args.no_browser, export_only=args.export_only)
        if args.command == "evidence-request":
            from .evidence_requests import export_request
            record = export_request(args.run, args.finding, read_json(args.spec), args.out, script=args.script)
            print(record["request_id"])
            return 0
        if args.command == "evidence-replay":
            from .replay import replay
            receipt = replay(args.request, args.out)
            print(json.dumps(receipt, indent=2))
            return 0 if receipt["status"] == "completed" else 130 if receipt["execution"] == "interrupted" else 1
        if args.command == "evidence-graduation":
            from .evidence_requests import export_graduation
            export_graduation(args.request, read_json(args.proposal), args.out, replay_dir=args.replay)
            return 0
        if args.command == "verify-evidence-operation":
            from .evidence_requests import verify_operation
            print(verify_operation(args.path)["operation_id"])
            return 0
        if args.command == "diagnose":
            from .diagnostics import diagnose, render_diagnosis
            report = diagnose(args.path, current_source=not args.snapshot_only)
            print(json.dumps(report, indent=2) if args.machine else render_diagnosis(report))
            return 0
        if args.command == "doctor":
            return doctor(checks_only=args.checks_only)
        if args.command in ('bench-status', 'bench-recover', 'bench-launch'):
            from .bench_transactions import recover, status
            value = (status(args.path) if args.command == 'bench-status' else
                     recover(args.path, launch_review=args.command == 'bench-launch'))
            print(json.dumps(value, indent=2))
            return 0 if value['disposition'] in ('completed', 'ready_for_explicit_review') else 1
        if args.command == "bench-review":
            if args.operation_id:
                from .bench_transactions import transact
                value = transact(args.run, args.report, args.out, args.max_seconds, operation_id=args.operation_id)
                print(json.dumps(value, indent=2))
                return 0 if (value.get('current_review') or {}).get('outcome') == 'criteria_satisfied' else 1
            from .empirical import bench_review
            return bench_review(args.run, args.report, args.out, args.max_seconds)
        if args.command == 'bench-new-attempt':
            from .bench_transactions import new_attempt
            value = new_attempt(args.path, args.attempt_id, args.out)
            print(json.dumps(value, indent=2))
            return 0 if value['disposition'] == 'completed' else 1
        if args.command == "campaign-feedback":
            if args.operation_id:
                from .bench_transactions import campaign_transaction
                value = campaign_transaction(args.campaign, args.report, args.out, args.max_seconds,
                                             operation_id=args.operation_id)
                print(json.dumps(value, indent=2))
                return 0 if (value.get('current_review') or {}).get('outcome') == 'criteria_satisfied' else 1
            from .campaign import campaign_feedback
            return campaign_feedback(args.campaign, args.report, args.out, args.max_seconds)
        if args.command == "verify-campaign":
            from .campaign import verify_campaign
            state = verify_campaign(args.campaign)
            print(state["state"])
            artifacts = state.get("fab_artifacts")
            if artifacts:
                from .fab_package import rotation_blocker
                run = state["history"][-1]["run"]
                rotation = artifacts.get("rotation") or {}
                print(f"fab_rotation {rotation.get('status')} policy {rotation.get('policy_sha256')}")
                blocker = rotation_blocker(rotation.get("status"))
                if blocker:
                    print(blocker)
                print(f"fab_artifacts {artifacts['artifacts_id']} candidate {artifacts['candidate_id']}")
                for rel, sha in artifacts["files"].items():
                    print(f"{sha}  {run}/{rel}")
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
    except (ValueError, OSError, KeyError, TypeError) as exc:
        from .bench_transactions import TransactionError
        if isinstance(exc, TransactionError):
            print(json.dumps(exc.diagnostic, indent=2), file=sys.stderr)
        elif args.command in ('bench-status', 'bench-recover', 'bench-launch', 'bench-new-attempt') or (
                args.command in ('bench-review', 'campaign-feedback') and args.operation_id):
            print(json.dumps({'schema': 'agent-ee.bench-recovery/1', 'disposition': 'unverified',
                              'reason': f'{type(exc).__name__}: {exc}',
                              'operator_actions': ['preserve retained evidence; diagnose before recovery']}, indent=2), file=sys.stderr)
        else:
            print(f"agent-ee: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
