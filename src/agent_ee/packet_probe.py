"""Measure real reviewer packet assembly from captured runs, without inference."""

import argparse
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sys
import time

from .candidate import safe_relative, verify_snapshot
from .reviewers import build_packet
from .storage import digest, read_json, write_json

STAGES = ("empty", "schematic", "placement", "routing")
_ACTIVE_GUARDS = {}
_GUARD_INSTALLED = False


def _audit_guard(event, args):
    if _ACTIVE_GUARDS and (event.startswith(("subprocess.", "socket.", "os.exec", "os.spawn"))
                          or event in ("os.system", "os.posix_spawn", "os.fork", "os.forkpty")):
        for attempts in _ACTIVE_GUARDS.values():
            attempts.append(event)
        raise RuntimeError(f"packet probe forbids process/network activity: {event}")


@contextmanager
def no_inference():
    """Refuse process and network activity while assembling local evidence."""
    global _GUARD_INSTALLED
    if not _GUARD_INSTALLED:
        sys.addaudithook(_audit_guard)
        _GUARD_INSTALLED = True
    attempts = []
    _ACTIVE_GUARDS[id(attempts)] = attempts
    try:
        yield attempts
        if attempts:
            raise RuntimeError("packet probe attempted process/network activity")
    finally:
        del _ACTIVE_GUARDS[id(attempts)]


def local_file(root, relative):
    relative = safe_relative(relative)
    path = root / relative
    for part in (path, *path.parents):
        if part == root:
            break
        if part.is_symlink():
            raise ValueError(f"linked evidence is unsupported: {relative}")
    if not path.is_file():
        raise ValueError(f"missing captured evidence: {relative}")
    return path


def copy_capture(source, destination):
    """Copy only packet inputs and declared check evidence, never reviewer output."""
    if not source.is_dir():
        raise ValueError("captured run directory is unavailable")
    candidate_path = local_file(source, "candidate.json")
    result_path = local_file(source, "result.json")
    candidate_hash, result_hash = digest(candidate_path), digest(result_path)
    manifest = verify_snapshot(source)
    result = read_json(result_path)
    if (result.get("candidate_id") != manifest["candidate_id"]
            or result.get("manifest_sha256") != candidate_hash
            or not isinstance(result.get("run_id"), str)):
        raise ValueError("run result is not bound to the captured candidate")
    checks = result.get("checks")
    if not isinstance(checks, list):
        raise ValueError("run lacks captured check records")
    expected = {item["id"] for item in manifest["scope"].get("checks", [])}
    if len({c["id"] for c in checks}) != len(checks) or any(c["id"] not in expected for c in checks):
        raise ValueError("run has duplicate or undeclared check records")
    files = {"candidate.json": candidate_hash, "result.json": result_hash}
    files.update({"inputs/" + rel: sha for rel, sha in manifest["files"].items()})
    if "baseline" in manifest["scope"]:
        files["baseline.json"] = digest(local_file(source, "baseline.json"))
    seals = result.get("artifact_hashes", {})
    for check in checks:
        if check.get("candidate_id") != manifest["candidate_id"]:
            raise ValueError("check belongs to another candidate")
        record = "checks/" + str(safe_relative(check["id"])) + ".json"
        if read_json(local_file(source, record)) != check:
            raise ValueError("check differs from retained record")
        paths = [record, *[e["path"] if isinstance(e, dict) else e for e in check.get("evidence", [])]]
        for rel in paths:
            if not rel.startswith(("inputs/", "work/", "checks/")):
                raise ValueError(f"non-coordinator packet evidence: {rel}")
            actual = digest(local_file(source, rel))
            if seals.get(rel) != actual:
                raise ValueError(f"unsealed or changed check evidence: {rel}")
            files[rel] = actual
    for rel, expected_hash in files.items():
        data = local_file(source, rel).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected_hash:
            raise ValueError(f"captured evidence changed: {rel}")
        target = destination / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    verify_snapshot(destination)
    if any(digest(local_file(source, rel)) != sha for rel, sha in files.items()):
        raise ValueError("captured run changed while copying")
    return manifest, result, files


def encoded_size(value):
    # initial_reviews supplies json.dumps(packet), not canonical or pretty JSON.
    return len(json.dumps(value).encode("utf-8"))


def measure_packet(packet, scope):
    sections = dict.fromkeys(("metadata", "inputs", "netlist", "drc", "probe_output", "receipts"), 0)
    kinds = {check["id"]: check["kind"] for check in scope.get("checks", [])}
    owners = {}
    for check in packet["checks"]:
        kind = kinds.get(check["id"])
        section = kind if kind in ("netlist", "drc") else "probe_output"
        for entry in check.get("evidence", []):
            owners.setdefault(entry["path"] if isinstance(entry, dict) else entry, section)
    artifacts = []
    for artifact in packet["artifacts"]:
        path = artifact["path"]
        section = "inputs" if path.startswith("inputs/") else owners.get(path, "probe_output")
        size = encoded_size(artifact)
        sections[section] += size
        artifacts.append({key: artifact[key] for key in ("path", "sha256", "bytes_total", "bytes_included", "extent")}
                         | {"section": section, "packet_bytes": size})
    receipts = [{"id": check["id"], "kind": kinds.get(check["id"]),
                 "packet_bytes": encoded_size(check)} for check in packet["checks"]]
    sections["receipts"] = sum(row["packet_bytes"] for row in receipts)
    total = encoded_size(packet)
    sections["metadata"] = total - sum(sections.values())
    cap = scope.get("packet_max_bytes", 240_000)
    if type(cap) is not int or not 1024 <= cap <= 1_000_000:
        raise ValueError("invalid captured packet_max_bytes")
    cumulative, rows = 0, []
    for section, size in sections.items():
        before = cumulative
        cumulative += size
        rows.append({"section": section, "packet_bytes": size,
                     "bytes_beyond_cap": max(0, cumulative - max(before, cap))})
    return {"packet_bytes": total,
            "packet_payload_sha256": hashlib.sha256(json.dumps(packet).encode("utf-8")).hexdigest(),
            "packet_max_bytes": cap, "check_receipts": receipts,
            "packet_exceeds_cap": total > cap, "packet_overflow_bytes": max(0, total - cap),
            "sections": rows, "section_accounting_order": list(sections),
            "overflow_sections": [row for row in rows if row["bytes_beyond_cap"]],
            "artifact_text_bytes": sum(a["bytes_included"] for a in artifacts),
            "artifacts": artifacts,
            "truncated_artifacts": [a for a in artifacts if a["bytes_included"] < a["bytes_total"]],
            "packet_limits": packet["packet_limits"]}


def probe_candidates(candidates, out):
    """Build each explicitly supplied stage packet in a fresh output directory."""
    if not candidates or any(stage not in STAGES for stage in candidates):
        raise ValueError("supply one or more empty/schematic/placement/routing captured runs")
    out = Path(out).resolve()
    sources = {stage: Path(path).resolve() for stage, path in candidates.items()}
    if any(out.is_relative_to(source) or source.is_relative_to(out) for source in sources.values()):
        raise ValueError("probe output must be separate from captured source runs")
    out.mkdir(parents=True, exist_ok=False)
    records = []
    with no_inference() as attempts:
        for stage in STAGES:
            if stage not in sources:
                records.append({"stage": stage, "status": "unavailable", "reason": "no captured run supplied"})
                continue
            source = sources[stage]
            destination = out / stage
            destination.mkdir()
            try:
                manifest, result, hashes = copy_capture(source, destination)
                start = time.perf_counter()
                packet = build_packet(destination, result["checks"], result["run_id"])
                seconds = time.perf_counter() - start
                record = {"stage": stage, "status": "measured", "source_run": str(source),
                          "candidate_id": manifest["candidate_id"], "run_id": result["run_id"],
                          "source_files": hashes, "packet_sha256": packet["packet_sha256"],
                          "packet_file_sha256": digest(destination / "packet.json"),
                          "assembly_seconds": seconds, "source_state": result.get("state"),
                          "profile_timeouts_s": {
                              definition.get("role", "adjudicator"): definition.get("timeout_s")
                              for definition in [*manifest["scope"].get("review_profile", {}).get("reviewers", []),
                                                 manifest["scope"].get("review_profile", {}).get("adjudicator", {})]
                              if definition},
                          "checks": [{"id": c["id"], "execution": c.get("execution"),
                                      "outcome": c.get("outcome")} for c in result["checks"]],
                          "missing_check_ids": sorted({c["id"] for c in manifest["scope"].get("checks", [])}
                                                      - {c["id"] for c in result["checks"]}),
                          **measure_packet(packet, manifest["scope"])}
            except (OSError, ValueError, KeyError, TypeError) as exc:
                record = {"stage": stage, "status": "unavailable", "source_run": str(source), "reason": str(exc)}
            records.append(record)
        report = {"schema": "agent-ee.packet-budget/1", "candidates": records,
                  "inference": {"invoked": False, "process_network_guard": True, "blocked_attempts": attempts},
                  "model_call_seconds": None,
                  "limitations": ["Stage labels are supplied by the operator; they do not prove stage acceptance or a telemetry-pod identity.",
                      "Measured bytes use the actual reviewer packet JSON encoding; role prompts and tool-mounted files are additional context.",
                      "packet_max_bytes bounds artifact text in the assembler, not total serialized JSON. Each text artifact is also capped at 100000 bytes.",
                      "Overflow attribution uses the recorded accounting order; all section contributions and truncated artifacts remain visible.",
                      "Assembly timing is local only; inference latency, tokens and provider cost are not measured. No cap or timeout changed.",
                      "Captured candidate and selected check hashes are verified; this probe does not requalify historical tools or establish design acceptance."]}
        if attempts:
            raise RuntimeError("packet probe attempted process/network activity")
        write_json(out / "packet-budget.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", action="append", required=True, metavar="STAGE=RUN",
                        help="captured run with candidate.json, result.json and sealed check evidence; repeat by stage")
    parser.add_argument("--out", type=Path, required=True, help="fresh output directory, normally under build/")
    args = parser.parse_args(argv)
    candidates = {}
    for value in args.candidate:
        stage, separator, run = value.partition("=")
        if not separator or not run or stage not in STAGES or stage in candidates:
            parser.error("each candidate must have a unique known STAGE and a RUN path")
        candidates[stage] = run
    try:
        report = probe_candidates(candidates, args.out)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(2, f"packet-probe: {exc}\n")
    print(json.dumps(report, indent=2))
    return 0 if all(c["status"] == "measured" for c in report["candidates"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
