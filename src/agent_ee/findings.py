"""Finding validation, non-voting collection, and evidence-grounded adjudication."""

import copy
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath

from .candidate import verify_snapshot
from .storage import read_json

SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
SEVERITIES = ("blocking", "major", "minor", "info")
ASSESSMENTS = ("defect", "concern")
DISPOSITIONS = ("accepted", "noted", "dismissed", "deferred", "duplicate", "closed")
CITATION_PATH_RULE = (
    "baseline.json (verified immutable baseline), inputs/, checks/, work/<check-id>/, "
    "or review-work/<role>/experiments/"
)
CHECK_EXECUTIONS = (
    "ok",
    "failed",
    "unavailable",
    "timeout",
    "malformed",
    "not_run",
    "interrupted",
    "unsupported",
)
CHECK_OUTCOMES = ("pass", "defect", "unknown", "info")

REVIEW_TOP_KEYS = frozenset(
    ("schema", "candidate_id", "run_id", "packet_sha256", "role", "coverage", "limitations", "findings")
)
FINDING_REQUIRED_KEYS = frozenset(
    ("id", "title", "location", "condition", "mechanism", "severity", "assessment", "evidence", "reasoning")
)
FINDING_OPTIONAL_KEYS = frozenset(("downgraded_from", "validation_note", "witnesses", "dimension"))
DECISIONS_TOP_KEYS = frozenset(("schema", "candidate_id", "run_id", "decisions"))
DECISION_REQUIRED_KEYS = frozenset(("finding_id", "disposition", "reason", "evidence"))
DECISION_OPTIONAL_KEYS = frozenset(("duplicate_of", "checks", "previous_run"))

STRUCTURED_CITATION_FIELDS = frozenset(("pointer", "lines", "xpath"))
REVIEW_VERSION = "agent-ee.review/2"
DECISIONS_VERSION = "agent-ee.decisions/2"


class CitationValidationError(ValueError):
    """An unresolved structured citation, eligible for output resubmission."""


def citation_format(data):
    """Describe locator choices from the same bytes the resolvers inspect."""
    try:
        text = data.decode("utf-8")
        is_text = "\x00" not in text and not data.startswith((b"%" + b"PDF-", b"\x89PNG"))
    except UnicodeDecodeError:
        is_text = False
    text_kinds = ["lines"] if is_text else []
    try:
        json.loads(data)
    except (ValueError, UnicodeDecodeError, RecursionError):
        pass
    else:
        return {"format": "JSON", "locator_kinds": ["pointer", *text_kinds]}
    try:
        ET.fromstring(data)
    except (ET.ParseError, ValueError, LookupError, RecursionError):
        pass
    else:
        return {"format": "XML", "locator_kinds": ["xpath", *text_kinds]}
    return {"format": "UTF-8 text" if is_text else "binary", "locator_kinds": text_kinds}


def safe_id(value, label="id"):
    if not isinstance(value, str) or not SAFE_ID_RE.fullmatch(value) or ".." in value:
        raise ValueError(f"invalid {label}: {value!r}")
    return value


def _nonempty_str(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _str_list(value, label):
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{label} must be a list of non-empty strings")
    return list(value)


def _validate_pointer(data, pointer):
    if not isinstance(pointer, str) or (pointer and not pointer.startswith("/")) or re.search(r"~(?![01])", pointer):
        raise ValueError("citation pointer must be an RFC 6901 JSON pointer")
    try:
        node = json.loads(data)
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise ValueError("citation pointer requires a valid JSON document") from exc
    for token in pointer.split("/")[1:] if pointer else []:
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and token in node:
            node = node[token]
        elif isinstance(node, list) and re.fullmatch(r"0|[1-9][0-9]*", token) and int(token) < len(node):
            node = node[int(token)]
        else:
            raise ValueError(f"citation pointer {pointer!r} does not resolve")


def _validate_lines(data, lines):
    if (not isinstance(lines, list) or len(lines) != 2 or
            any(type(n) is not int for n in lines) or not 1 <= lines[0] <= lines[1]):
        raise ValueError("citation lines must be [start, end] with 1-based inclusive integers")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("citation lines require UTF-8 text") from exc
    if "\x00" in text or data.startswith((b"%" + b"PDF-", b"\x89PNG")):
        raise ValueError("citation lines require UTF-8 text")
    if lines[1] > len(text.splitlines()):
        raise ValueError("citation lines out of range")


def _validate_xpath(data, xpath):
    _nonempty_str(xpath, "citation xpath")
    try:
        root = ET.fromstring(data)
        # ElementTree paths are root-relative; accept an explicit /root prefix too.
        path = xpath
        if xpath.startswith("/"):
            head, sep, tail = xpath[1:].partition("/")
            if head != root.tag:
                raise ValueError(f"citation xpath {xpath!r} does not resolve")
            path = "./" + tail if sep else "."
        if not root.findall(path):
            raise ValueError(f"citation xpath {xpath!r} does not resolve")
    except (ET.ParseError, SyntaxError, LookupError, TypeError, RecursionError) as exc:
        raise ValueError("citation xpath requires valid XML and an ElementTree-supported path") from exc


def validate_citation_path(path_value, run_dir):
    if not isinstance(path_value, str) or not path_value.strip() or "\\" in path_value:
        raise ValueError(f"invalid citation path: {path_value!r}")
    rel = PurePosixPath(path_value)
    if rel.is_absolute() or not rel.parts or any(part in ("", ".", "..") for part in rel.parts):
        raise ValueError(f"unsafe citation path: {path_value!r}")
    head = rel.parts[0]
    if path_value == "baseline.json":
        pass  # Verify the coordinator baseline after the common file/link guards.
    elif head in ("inputs", "checks"):
        if len(rel.parts) < 2:
            raise ValueError(f"citation path must reference a file inside {head}/: {path_value!r}")
    elif head == "review-work":
        if len(rel.parts) < 4 or rel.parts[2] != "experiments":
            raise ValueError("reviewer citations must name review-work/<role>/experiments/<file>")
        safe_id(rel.parts[1], "review role")
    elif head == "work":
        if len(rel.parts) < 3:
            raise ValueError(f"citation path under work/ must be work/<check-id>/<file>: {path_value!r}")
        safe_id(rel.parts[1], "work check id")
    else:
        raise ValueError(
            f"citation path must name {CITATION_PATH_RULE}: {path_value!r}"
        )

    root = Path(run_dir)
    if not root.is_dir():
        raise ValueError(f"run directory does not exist: {root}")
    cur = root
    for part in rel.parts:
        cur = cur / part
        if cur.is_symlink():
            raise ValueError(f"citation path traverses symlink: {path_value!r}")
    resolved_root = root.resolve()
    resolved_target = cur.resolve()
    if not resolved_target.is_relative_to(resolved_root) or not cur.is_file():
        raise ValueError(f"cited artifact does not exist in run: {path_value!r}")
    if path_value == "baseline.json":
        from .baseline import load_baseline
        if load_baseline(root) is None:
            raise ValueError("baseline.json citation requires an immutable scope/2 or scope/3 baseline")
    return rel.as_posix()


def validate_citation(item, run_dir, *, require_structured=False):
    if (not isinstance(item, dict) or not {"path", "locator"} <= item.keys() or
            item.keys() - {"path", "locator"} - STRUCTURED_CITATION_FIELDS):
        raise ValueError(f"malformed evidence citation: {item!r}")
    path_norm = validate_citation_path(item["path"], run_dir)
    locator = _nonempty_str(item["locator"], "evidence locator")
    fields = item.keys() & STRUCTURED_CITATION_FIELDS
    if require_structured and not fields:
        info = citation_format((Path(run_dir) / path_norm).read_bytes())
        raise CitationValidationError(
            f"citation {path_norm!r}: evidence citation requires pointer, lines or xpath; "
            f"locator kind used: none; {info['format']} requires locator kind(s) {info['locator_kinds']}")
    # Descriptive locators in retained /1 records are deliberately never parsed.
    if fields:
        data = (Path(run_dir) / path_norm).read_bytes()
        errors = []
        for key, validate in (("pointer", _validate_pointer), ("lines", _validate_lines), ("xpath", _validate_xpath)):
            if key in fields:
                try:
                    validate(data, item[key])
                except ValueError as exc:
                    errors.append(f"{key}: {exc}")
        if errors:
            info = citation_format(data)
            required = ", ".join(info["locator_kinds"]) or "none; cite a captured text extraction instead"
            raise CitationValidationError(
                f"citation {path_norm!r}: locator kind(s) used {', '.join(sorted(fields))}; "
                f"{info['format']} requires locator kind(s) {required}. " + "; ".join(errors))
    return {"path": path_norm, "locator": locator,
            **{key: copy.deepcopy(item[key]) for key in STRUCTURED_CITATION_FIELDS if key in item}}


def validate_check_result(check, run_dir=None):
    if not isinstance(check, dict):
        raise ValueError("check result must be a JSON object")
    cid = safe_id(check.get("id"), "check id")
    _nonempty_str(check.get("kind"), "check kind")
    execution = check.get("execution")
    if execution not in CHECK_EXECUTIONS:
        raise ValueError(f"check {cid}: invalid execution status {execution!r}")
    outcome = check.get("outcome")
    if outcome not in CHECK_OUTCOMES:
        raise ValueError(f"check {cid}: invalid outcome {outcome!r}")
    if execution != "ok" and outcome != "unknown":
        raise ValueError(
            f"check {cid}: dishonest status (execution={execution!r} requires outcome='unknown', got {outcome!r})"
        )
    _nonempty_str(check.get("summary"), f"check {cid} summary")
    from .stages import validate_gate_findings
    validate_gate_findings(check)
    ev = check.get("evidence")
    if not isinstance(ev, list):
        raise ValueError(f"check {cid}: evidence must be a list")
    for entry in ev:
        p = entry.get("path") if isinstance(entry, dict) else entry
        if not isinstance(p, str) or not p.strip():
            raise ValueError(f"check {cid}: invalid evidence entry {entry!r}")
        if run_dir is not None:
            validate_citation_path(p, run_dir)
            if isinstance(entry, dict) and ("locator" in entry or STRUCTURED_CITATION_FIELDS & entry.keys()):
                validate_citation(entry, run_dir)
    return check


def validate_review(value, candidate_id, run_id, packet_sha256, run_dir):
    run_dir = Path(run_dir)
    _nonempty_str(candidate_id, "expected candidate_id")
    _nonempty_str(run_id, "expected run_id")
    _nonempty_str(packet_sha256, "expected packet_sha256")

    if (run_dir / "candidate.json").is_file():
        manifest = verify_snapshot(run_dir)
        if manifest.get("candidate_id") != candidate_id:
            raise ValueError("expected candidate_id does not match run candidate.json")
    if (run_dir / "packet.json").is_file():
        packet = read_json(run_dir / "packet.json")
        if (
            packet.get("candidate_id") != candidate_id
            or packet.get("run_id") != run_id
            or packet.get("packet_sha256") != packet_sha256
        ):
            raise ValueError("expected review identity does not match run packet.json")

    if not isinstance(value, dict):
        raise ValueError("review must be a JSON object")
    from .rf_review import ASSESSMENT_KEY, ROLE as RF_ROLE
    # The dedicated RF role alone carries, and must carry, its structured assessment.
    expected_top = REVIEW_TOP_KEYS | ({ASSESSMENT_KEY} if value.get("role") == RF_ROLE else set())
    unknown_top = set(value.keys()) - expected_top
    missing_top = expected_top - set(value.keys())
    if missing_top or unknown_top:
        raise ValueError(f"review keys mismatch: missing={sorted(missing_top)}, extra={sorted(unknown_top)}")
    if value.get("schema") not in ("agent-ee.review/1", REVIEW_VERSION):
        raise ValueError(f"unsupported review schema: {value.get('schema')!r}")
    if value.get("candidate_id") != candidate_id:
        raise ValueError("stale or mismatched review candidate_id")
    if value.get("run_id") != run_id:
        raise ValueError("stale or mismatched review run_id")
    if value.get("packet_sha256") != packet_sha256:
        raise ValueError("stale or mismatched review packet_sha256")

    role = safe_id(value.get("role"), "reviewer role")
    if role in ("check", "adjudicator"):
        raise ValueError(f"reserved role name not allowed for reviewer: {role!r}")
    coverage = _str_list(value.get("coverage"), "coverage")
    limitations = _str_list(value.get("limitations"), "limitations")

    raw_findings = value.get("findings")
    if not isinstance(raw_findings, list):
        raise ValueError("review findings must be a list")

    validated_findings = []
    seen_ids = set()
    for item in raw_findings:
        if not isinstance(item, dict):
            raise ValueError("finding entry must be a JSON object")
        keys = set(item.keys())
        missing = FINDING_REQUIRED_KEYS - keys
        extra = keys - (FINDING_REQUIRED_KEYS | FINDING_OPTIONAL_KEYS)
        if missing or extra:
            raise ValueError(f"finding keys mismatch: missing={sorted(missing)}, extra={sorted(extra)}")
        fid = safe_id(item.get("id"), "finding id")
        if fid in seen_ids:
            raise ValueError(f"duplicate finding id {fid!r} in review {role!r}")
        seen_ids.add(fid)

        title = _nonempty_str(item.get("title"), f"finding {fid} title")
        location = _nonempty_str(item.get("location"), f"finding {fid} location")
        condition = _nonempty_str(item.get("condition"), f"finding {fid} condition")
        mechanism = _nonempty_str(item.get("mechanism"), f"finding {fid} mechanism")
        reasoning = _nonempty_str(item.get("reasoning"), f"finding {fid} reasoning")
        severity = item.get("severity")
        if severity not in SEVERITIES:
            raise ValueError(f"finding {fid}: invalid severity {severity!r}")
        assessment = item.get("assessment")
        if assessment not in ASSESSMENTS:
            raise ValueError(f"finding {fid}: invalid assessment {assessment!r}")

        raw_ev = item.get("evidence")
        if not isinstance(raw_ev, list):
            raise ValueError(f"finding {fid}: evidence must be a list")
        evidence = [validate_citation(c, run_dir, require_structured=value["schema"] == REVIEW_VERSION) for c in raw_ev]

        normalized = {
            "id": fid,
            "title": title,
            "location": location,
            "condition": condition,
            "mechanism": mechanism,
            "severity": severity,
            "assessment": assessment,
            "evidence": evidence,
            "reasoning": reasoning,
        }
        from .purpose_review import DIMENSION, check_finding
        if check_finding(fid, item):
            normalized["dimension"] = DIMENSION
        if "witnesses" in item:
            from .baseline import validate_witness_shape
            validate_witness_shape(item["witnesses"])
            normalized["witnesses"] = copy.deepcopy(item["witnesses"])
        if assessment == "defect" and (run_dir / "baseline.json").exists():
            from .baseline import validate_witnesses
            validate_witnesses(run_dir, item.get("witnesses"), evidence)
        if not evidence and assessment == "defect":
            normalized["assessment"] = "concern"
            normalized["downgraded_from"] = "defect"
            note = "Downgraded from defect to concern: empty evidence cannot establish a defect."
            normalized["validation_note"] = note
            if note not in normalized["reasoning"]:
                normalized["reasoning"] = f"{normalized['reasoning']} [{note}]"
            limitations.append(f"{role}/{fid}: downgraded from defect to concern because evidence was empty.")
        elif item.get("downgraded_from"):
            normalized["downgraded_from"] = item["downgraded_from"]
        validated_findings.append(normalized)

    validated = {
        "schema": value["schema"],
        "candidate_id": candidate_id,
        "run_id": run_id,
        "packet_sha256": packet_sha256,
        "role": role,
        "coverage": coverage,
        "limitations": limitations,
        "findings": validated_findings,
    }
    if role == RF_ROLE:
        from .rf_review import validate_assessment
        validated[ASSESSMENT_KEY] = validate_assessment(value[ASSESSMENT_KEY], validated_findings, run_dir,
                                                        require_structured=value["schema"] == REVIEW_VERSION)
    return validated


def collect_findings(reviews, checks):
    if not isinstance(reviews, list) or not isinstance(checks, list):
        raise ValueError("reviews and checks must be lists")

    collected = []
    seen_ids = set()

    for check in checks:
        validate_check_result(check)
        if check.get("execution") != "ok" or check.get("outcome") != "defect":
            continue
        cid = safe_id(check["id"], "check id")
        fid = f"check/{cid}"
        if fid in seen_ids:
            raise ValueError(f"duplicate collected finding id: {fid}")
        seen_ids.add(fid)

        ev_list = []
        for entry in check.get("evidence", []):
            if isinstance(entry, dict):
                ev_list.append({"path": entry["path"], "locator": entry.get("locator", f"check:{cid}"),
                                **{key: copy.deepcopy(entry[key]) for key in STRUCTURED_CITATION_FIELDS if key in entry}})
            else:
                ev_list.append({"path": str(entry), "locator": f"check:{cid}"})

        finding = {
            "id": fid,
            "source": "check",
            "role": "check",
            "local_id": cid,
            "check_kind": check.get("kind", ""),
            "title": check.get("summary") or f"Check {cid} reported a design defect",
            "location": check.get("location", cid),
            "condition": check.get("condition", f"check {cid} ({check.get('kind', cid)})"),
            "mechanism": check.get("mechanism", check.get("summary", "")),
            "severity": check.get("severity", "blocking"),
            "assessment": "defect",
            "evidence": ev_list,
            "reasoning": check.get("reasoning", check.get("summary", "")),
            "status": "unresolved",
            "disposition": "unresolved",
            "events": [],
        }
        if 'gate_findings' in check.get('details', {}):
            finding['gate_findings'] = copy.deepcopy(check['details']['gate_findings'])
        if check.get("candidate_id"):
            finding["candidate_id"] = check["candidate_id"]
        if check.get("run_id"):
            finding["run_id"] = check["run_id"]
        collected.append(finding)

    for review in reviews:
        if not isinstance(review, dict):
            raise ValueError("each review in reviews must be a JSON object")
        role = safe_id(review.get("role"), "reviewer role")
        if role in ("check", "adjudicator"):
            raise ValueError(f"reserved role name not allowed for reviewer: {role!r}")
        rcand = review.get("candidate_id")
        rrun = review.get("run_id")
        for item in review.get("findings", []):
            if not isinstance(item, dict):
                raise ValueError("finding in review must be a JSON object")
            local_id = safe_id(item.get("id"), "finding id")
            fid = f"{role}/{local_id}"
            if fid in seen_ids:
                raise ValueError(f"duplicate collected finding id: {fid}")
            seen_ids.add(fid)

            ev = copy.deepcopy(item.get("evidence", []))
            if not isinstance(ev, list):
                raise ValueError(f"finding {fid}: evidence must be a list")
            assessment = item.get("assessment", "concern")
            if assessment not in ASSESSMENTS:
                raise ValueError(f"finding {fid}: invalid assessment {assessment!r}")
            severity = item.get("severity", "major")
            if severity not in SEVERITIES:
                raise ValueError(f"finding {fid}: invalid severity {severity!r}")

            downgraded_from = item.get("downgraded_from")
            reasoning = _nonempty_str(item.get("reasoning"), f"finding {fid} reasoning")
            if not ev and assessment == "defect":
                assessment = "concern"
                downgraded_from = "defect"
                note = "Downgraded from defect to concern: empty evidence cannot establish a defect."
                if note not in reasoning:
                    reasoning = f"{reasoning} [{note}]"

            finding = {
                "id": fid,
                "source": "review",
                "role": role,
                "local_id": local_id,
                "title": _nonempty_str(item.get("title"), f"finding {fid} title"),
                "location": _nonempty_str(item.get("location"), f"finding {fid} location"),
                "condition": _nonempty_str(item.get("condition"), f"finding {fid} condition"),
                "mechanism": _nonempty_str(item.get("mechanism"), f"finding {fid} mechanism"),
                "severity": severity,
                "assessment": assessment,
                "evidence": ev,
                "reasoning": reasoning,
                "status": "unresolved",
                "disposition": "unresolved",
                "events": [],
            }
            if downgraded_from:
                finding["downgraded_from"] = downgraded_from
            from .purpose_review import DIMENSION, check_finding
            if check_finding(fid, item):
                finding["dimension"] = DIMENSION
            if rcand:
                finding["candidate_id"] = rcand
            if rrun:
                finding["run_id"] = rrun
            if "witnesses" in item:
                finding["witnesses"] = copy.deepcopy(item["witnesses"])
            collected.append(finding)

    return collected


def _expected_run_identity(run_dir):
    cand_id, run_id = None, None
    if (run_dir / "candidate.json").is_file():
        manifest = verify_snapshot(run_dir)
        cand_id = manifest.get("candidate_id")
    if (run_dir / "result.json").is_file():
        res = read_json(run_dir / "result.json")
        if res.get("candidate_id"):
            if cand_id and res["candidate_id"] != cand_id:
                raise ValueError("result.json candidate_id disagrees with candidate.json")
            cand_id = res["candidate_id"]
        if res.get("run_id"):
            run_id = res["run_id"]
    if (run_dir / "packet.json").is_file():
        pkt = read_json(run_dir / "packet.json")
        if pkt.get("candidate_id"):
            if cand_id and pkt["candidate_id"] != cand_id:
                raise ValueError("packet.json candidate_id disagrees with candidate.json")
            cand_id = pkt["candidate_id"]
        if pkt.get("run_id"):
            if run_id and pkt["run_id"] != run_id:
                raise ValueError("packet.json run_id disagrees with result.json")
            run_id = pkt["run_id"]
    return cand_id, run_id


def _verify_unchanged_requirements(old_manifest, new_manifest):
    old_reqs = old_manifest["scope"].get("requirements", [])
    new_reqs = new_manifest["scope"].get("requirements", [])
    if old_reqs != new_reqs:
        raise ValueError("requirements list changed; closure under original criteria refused")
    old_files = old_manifest.get("files", {})
    new_files = new_manifest.get("files", {})
    for req in old_reqs:
        if req not in old_files or req not in new_files:
            raise ValueError(f"requirement file {req!r} missing from captured candidate manifest")
        if old_files[req] != new_files[req]:
            raise ValueError(f"requirements changed ({req}); closure under original criteria refused")


def validate_closure(previous_run, current_run, finding_id, check_ids, reason, *, evidence=None):
    """Validate bounded repair closure across two distinct candidate runs."""
    if not previous_run:
        raise ValueError("closure requires an explicit previous_run directory")
    prev_dir = Path(previous_run).resolve()
    curr_dir = Path(current_run).resolve()
    _nonempty_str(finding_id, "finding_id")
    _nonempty_str(reason, "closure reason")
    if not isinstance(check_ids, list) or not check_ids:
        raise ValueError("closure requires a non-empty list of affected check IDs")

    old_manifest = verify_snapshot(prev_dir)
    new_manifest = verify_snapshot(curr_dir)
    if old_manifest["candidate_id"] == new_manifest["candidate_id"]:
        raise ValueError("closure requires a changed saved candidate")

    if old_manifest["scope"].get("checks") != new_manifest["scope"].get("checks"):
        raise ValueError("verification criteria changed; cannot close under different rules")
    _verify_unchanged_requirements(old_manifest, new_manifest)

    old_result = read_json(prev_dir / "result.json")
    if old_result.get("candidate_id") != old_manifest["candidate_id"]:
        raise ValueError("previous_run result.json candidate_id does not match its candidate.json")
    source_finding = next((f for f in old_result.get("findings", []) if f.get("id") == finding_id), None)
    if source_finding is None:
        raise ValueError(f"finding {finding_id!r} not found in previous run")
    status = source_finding.get("disposition", source_finding.get("status", "unresolved"))
    if status != "accepted":
        raise ValueError(f"closure requires an accepted finding (got {status!r})")

    new_result = read_json(curr_dir / "result.json")
    if new_result.get("candidate_id") != new_manifest["candidate_id"]:
        raise ValueError("current_run result.json candidate_id does not match its candidate.json")
    if new_result.get("outcome") != "criteria_satisfied":
        raise ValueError(
            f"new review has not satisfied its declared criteria (outcome={new_result.get('outcome')!r})"
        )

    checks_by_id = {c["id"]: c for c in new_result.get("checks", [])}
    for cid in check_ids:
        safe_id(cid, "check id")
        if cid not in checks_by_id:
            raise ValueError(f"affected closure check {cid!r} not present in new run")
        chk = checks_by_id[cid]
        validate_check_result(chk, curr_dir)
        if chk.get("execution") != "ok" or chk.get("outcome") != "pass":
            raise ValueError(
                f"affected closure check {cid!r} did not freshly pass "
                f"(execution={chk.get('execution')!r}, outcome={chk.get('outcome')!r})"
            )

    from .rf_review import closure_gate
    closure_gate(source_finding, old_result, new_result)
    validated_ev = [validate_citation(c, curr_dir) for c in (evidence or [])]
    return {
        "schema": "agent-ee.correction/1",
        "finding_id": finding_id,
        "reason": reason,
        "old_candidate_id": old_manifest["candidate_id"],
        "new_candidate_id": new_manifest["candidate_id"],
        "old_run_id": old_result.get("run_id"),
        "new_run_id": new_result.get("run_id"),
        "new_run": str(curr_dir),
        "checks": list(check_ids),
        "evidence": validated_ev,
    }


def _verify_closure_via_adjudicate(finding, decision, validated_evidence, current_candidate_id, current_run_id, run_dir):
    origin_cand = (
        finding.get("candidate_id")
        or (finding["events"][0].get("candidate_id") if finding.get("events") else None)
        or current_candidate_id
    )
    if origin_cand == current_candidate_id:
        raise ValueError(
            f"finding {finding['id']}: cannot close in the same candidate ({current_candidate_id}); "
            "repair closure requires a new candidate and fresh affected checks"
        )
    if not validated_evidence:
        raise ValueError(f"finding {finding['id']}: closed disposition requires fresh evidence citations")

    prev_run = decision.get("previous_run")
    if not prev_run and (run_dir / "result.json").is_file():
        prev_run = read_json(run_dir / "result.json").get("previous_run")
    if not prev_run:
        raise ValueError(
            f"finding {finding['id']}: closing via adjudicate requires previous_run linkage to validate "
            "unchanged criteria, requirements, and prior accepted finding state"
        )

    check_ids = list(decision.get("checks") or [])
    for cite in validated_evidence:
        p = PurePosixPath(cite["path"])
        if len(p.parts) == 2 and p.parts[0] == "checks" and p.parts[1].endswith(".json"):
            cid = p.parts[1][:-5]
            if cid not in check_ids:
                check_ids.append(cid)
    if finding.get("source") == "check" and finding.get("local_id"):
        if finding["local_id"] not in check_ids:
            check_ids.append(finding["local_id"])

    closure = validate_closure(
        prev_run,
        run_dir,
        finding["id"],
        check_ids,
        decision.get("reason", ""),
        evidence=validated_evidence,
    )
    if closure["old_candidate_id"] != origin_cand:
        raise ValueError(
            f"finding {finding['id']}: previous_run candidate_id {closure['old_candidate_id']} "
            f"does not match finding candidate_id {origin_cand}"
        )
    if closure["new_candidate_id"] != current_candidate_id or closure["new_run_id"] != current_run_id:
        raise ValueError(f"finding {finding['id']}: closure run identity mismatch")
    return closure["checks"]


def adjudicate(findings, decisions, run_dir):
    run_dir = Path(run_dir)
    if not isinstance(findings, list):
        raise ValueError("findings must be a list")
    if not isinstance(decisions, dict):
        raise ValueError("decisions must be a JSON object")

    unknown_top = set(decisions.keys()) - DECISIONS_TOP_KEYS
    missing_top = DECISIONS_TOP_KEYS - set(decisions.keys())
    if missing_top or unknown_top:
        raise ValueError(f"decisions keys mismatch: missing={sorted(missing_top)}, extra={sorted(unknown_top)}")
    if decisions.get("schema") not in ("agent-ee.decisions/1", DECISIONS_VERSION):
        raise ValueError(f"unsupported decisions schema: {decisions.get('schema')!r}")

    cand_id = _nonempty_str(decisions.get("candidate_id"), "decisions candidate_id")
    run_id = _nonempty_str(decisions.get("run_id"), "decisions run_id")
    expected_cand, expected_run = _expected_run_identity(run_dir)
    if expected_cand is not None and cand_id != expected_cand:
        raise ValueError("stale or mismatched decisions candidate_id")
    if expected_run is not None and run_id != expected_run:
        raise ValueError("stale or mismatched decisions run_id")

    raw_list = decisions.get("decisions")
    if not isinstance(raw_list, list):
        raise ValueError("decisions.decisions must be a list")

    updated = copy.deepcopy(findings)
    by_id = {}
    for f in updated:
        if not isinstance(f, dict) or "id" not in f:
            raise ValueError("invalid finding object in findings list")
        fid = f["id"]
        if fid in by_id:
            raise ValueError(f"duplicate finding id in findings list: {fid}")
        f.setdefault("status", f.get("disposition", "unresolved"))
        f.setdefault("disposition", f["status"])
        f.setdefault("events", [])
        from .purpose_review import check_finding
        # A purpose tag on a downgraded defect or correctness claim is refused, never granted (AGENTEE-385).
        check_finding(fid, f)
        by_id[fid] = f

    seen_decision_ids = set()
    staged = []

    for item in raw_list:
        if not isinstance(item, dict):
            raise ValueError("decision item must be a JSON object")
        keys = set(item.keys())
        missing = DECISION_REQUIRED_KEYS - keys
        extra = keys - (DECISION_REQUIRED_KEYS | DECISION_OPTIONAL_KEYS)
        if missing or extra:
            raise ValueError(f"decision item keys mismatch: missing={sorted(missing)}, extra={sorted(extra)}")

        fid = _nonempty_str(item.get("finding_id"), "decision finding_id")
        if fid not in by_id:
            raise ValueError(f"decision references unknown finding_id: {fid!r}")
        if fid in seen_decision_ids:
            raise ValueError(f"duplicate decision for finding_id: {fid!r}")
        seen_decision_ids.add(fid)

        target_finding = by_id[fid]
        fcand = target_finding.get("candidate_id")
        frun = target_finding.get("run_id")
        disp = item.get("disposition")
        if disp not in DISPOSITIONS:
            raise ValueError(f"finding {fid}: invalid disposition {disp!r}")

        if disp != "closed":
            if fcand and fcand != cand_id:
                raise ValueError(f"finding {fid} belongs to candidate {fcand}, not {cand_id}")
            if frun and frun != run_id:
                raise ValueError(f"finding {fid} belongs to run {frun}, not {run_id}")

        reason = _nonempty_str(item.get("reason"), f"decision {fid} reason")
        raw_ev = item.get("evidence")
        if not isinstance(raw_ev, list):
            raise ValueError(f"decision {fid}: evidence must be a list")
        validated_ev = [validate_citation(c, run_dir, require_structured=decisions["schema"] == DECISIONS_VERSION) for c in raw_ev]

        dup_of = item.get("duplicate_of")
        if disp == "duplicate":
            dup_of = _nonempty_str(dup_of, f"decision {fid} duplicate_of")
            if dup_of == fid:
                raise ValueError(f"finding {fid} cannot be a duplicate of itself")
            if dup_of not in by_id:
                raise ValueError(f"decision {fid} duplicate_of references unknown finding: {dup_of!r}")
        elif dup_of is not None:
            raise ValueError(f"decision {fid}: duplicate_of is only allowed when disposition is 'duplicate'")

        if disp == "accepted":
            from .baseline import advisory_check, load_baseline, validate_advisory_defect, validate_witnesses
            baseline = load_baseline(run_dir) if (run_dir / "candidate.json").exists() else None
            if baseline is not None:
                if target_finding.get("source") != "check":
                    raise ValueError("only executable check findings may be accepted; reviewer claims require reproduction or duplication")
                witnesses = target_finding.get("witnesses")
                if target_finding.get("source") == "check":
                    witnesses = [{"requirement_id": r["id"], "check_id": target_finding["local_id"]}
                        for r in baseline["requirements"] if target_finding["local_id"] in r["checks"]
                        and (baseline["schema"] == "agent-ee.baseline/1" or
                             target_finding["local_id"] in baseline["coverage"]["requirements"][r["id"]]["acceptance_checks"])]
                if not witnesses and advisory_check(baseline, target_finding["local_id"]):
                    # AGENTEE-217: a proved advisory defect is confirmed from its sealed result
                    # but witnesses no requirement; the check's defect outcome keeps the review failed.
                    validate_advisory_defect(run_dir, target_finding["local_id"], validated_ev)
                    item = {**item, "authority": "advisory"}
                else:
                    validate_witnesses(run_dir, witnesses, validated_ev)
            if target_finding.get("assessment") != "defect":
                raise ValueError(
                    f"decision {fid}: 'accepted' requires assessment='defect' "
                    f"(got {target_finding.get('assessment')!r}); use 'noted' for verified non-defect minor/info concerns "
                    "or 'deferred' for unresolved uncertainty"
                )
            if not validated_ev:
                raise ValueError(f"decision {fid}: accepted disposition requires non-empty evidence")
        elif disp == "noted":
            if target_finding.get("source") == "check" or target_finding.get("assessment") == "defect":
                raise ValueError(f"decision {fid}: established defect or check failure cannot be 'noted'")
            if target_finding.get("downgraded_from") == "defect" or not target_finding.get("evidence"):
                raise ValueError(f"decision {fid}: unsupported or downgraded claim cannot be 'noted'")
            if target_finding.get("severity") in ("blocking", "major"):
                raise ValueError(
                    f"decision {fid}: {target_finding.get('severity')!r} concern cannot be 'noted'; "
                    "refute with counter-evidence ('dismissed') or keep as open uncertainty ('deferred')"
                )
            if not validated_ev:
                raise ValueError(f"decision {fid}: noted disposition requires non-empty evidence")
        elif disp == "dismissed":
            if target_finding.get("source") == "check":
                raise ValueError(
                    f"decision {fid}: authoritative check defect cannot be dismissed; "
                    "accept it, mark it duplicate of an accepted finding, or close it in a repaired candidate"
                )
            is_supported = target_finding.get("assessment") == "defect" or bool(target_finding.get("evidence"))
            if is_supported and not validated_ev:
                raise ValueError(f"decision {fid}: dismissing a supported finding requires counter-evidence")
        elif disp == "deferred":
            if target_finding.get("source") == "check":
                raise ValueError(f"decision {fid}: authoritative check defect cannot be deferred")
        elif disp == "closed":
            verified_checks = _verify_closure_via_adjudicate(
                target_finding, item, validated_ev, cand_id, run_id, run_dir
            )
            item = {**item, "checks": verified_checks}

        staged.append((target_finding, disp, reason, validated_ev, dup_of, item))

    # Apply staged statuses tentatively to validate duplicate chains/cycles before committing.
    prior_status = {fid: f["status"] for fid, f in by_id.items()}
    tentative_status = dict(prior_status)
    tentative_dup = {
        fid: f.get("duplicate_of")
        for fid, f in by_id.items()
        if f.get("status") == "duplicate" and f.get("duplicate_of")
    }
    for target_finding, disp, _reason, _ev, dup_of, _raw in staged:
        fid = target_finding["id"]
        tentative_status[fid] = disp
        if disp == "duplicate":
            tentative_dup[fid] = dup_of
        else:
            tentative_dup.pop(fid, None)

    advisory = {fid for fid, f in by_id.items() if f.get("authority") == "advisory"}
    advisory.update(t["id"] for t, _disp, _r, _e, _d, raw in staged if raw.get("authority") == "advisory")
    for fid in list(tentative_dup.keys()):
        visited = [fid]
        cur = tentative_dup.get(fid)
        while cur is not None:
            if cur in visited:
                cycle_path = " -> ".join(visited + [cur])
                raise ValueError(f"duplicate cycle detected: {cycle_path}")
            visited.append(cur)
            if tentative_status.get(cur) != "duplicate":
                break
            cur = tentative_dup.get(cur)
        root_id = visited[-1]
        root_status = tentative_status.get(root_id, "unresolved")
        if root_status == "dismissed":
            raise ValueError(
                f"duplicate {fid} -> {root_id}: duplicate target has status 'dismissed' "
                "and would hide an unresolved or accepted finding"
            )
        if root_status == "closed" and prior_status.get(fid) != "duplicate":
            raise ValueError(
                f"duplicate {fid} -> {root_id}: cannot newly mark a finding as duplicate of a closed target"
            )
        if (prior_status.get(fid) == "accepted" or by_id[fid].get("source") == "check") and root_status not in (
            "accepted",
            "closed",
        ):
            raise ValueError(
                f"duplicate {fid} -> {root_id}: accepted or check defect cannot be duplicated into {root_status!r} target"
            )
        if root_id in advisory and by_id[fid].get("source") == "check" and fid not in advisory:
            from .baseline import advisory_check, load_baseline
            if not advisory_check(load_baseline(run_dir), by_id[fid].get("local_id")):
                raise ValueError(
                    f"duplicate {fid} -> {root_id}: an acceptance check defect cannot be represented by an advisory finding"
                )
        if by_id[root_id].get("dimension") == "purpose_review" and by_id[fid].get("dimension") != "purpose_review":
            raise ValueError(
                f"duplicate {fid} -> {root_id}: a correctness finding cannot be represented by an advisory purpose finding"
            )
        if root_status == "noted" and (
            by_id[fid].get("source") == "check"
            or by_id[fid].get("assessment") == "defect"
            or by_id[fid].get("downgraded_from") == "defect"
            or by_id[fid].get("severity") in ("blocking", "major")
            or not by_id[fid].get("evidence")
        ):
            raise ValueError(
                f"duplicate {fid} -> {root_id}: defect, major/blocking, or unsupported finding "
                "cannot be duplicated into 'noted' target"
            )

    for target_finding, disp, reason, validated_ev, dup_of, raw_item in staged:
        if not target_finding.get("candidate_id"):
            target_finding["candidate_id"] = cand_id
        if not target_finding.get("run_id"):
            target_finding["run_id"] = run_id

        event = {
            "seq": len(target_finding["events"]) + 1,
            "candidate_id": cand_id,
            "run_id": run_id,
            "disposition": disp,
            "reason": reason,
            "evidence": validated_ev,
        }
        if dup_of is not None:
            event["duplicate_of"] = dup_of
            target_finding["duplicate_of"] = dup_of
            root_finding = by_id[dup_of]
            dups = root_finding.setdefault("duplicates", [])
            if target_finding["id"] not in dups:
                dups.append(target_finding["id"])
        else:
            target_finding.pop("duplicate_of", None)

        if raw_item.get("authority") == "advisory":
            event["authority"] = "advisory"
            target_finding["authority"] = "advisory"
        if "dimension" in target_finding:
            # AGENTEE-385: purpose savings are advisory; recorded so a deferral stays visible
            # without holding the run outcome.
            event["purpose_status"] = "advisory"
            target_finding["purpose_status"] = "advisory"
        if disp == "closed":
            event["closed_in_candidate_id"] = cand_id
            event["closed_in_run_id"] = run_id
            event["checks"] = raw_item.get("checks", [])

        target_finding["events"].append(event)
        target_finding["status"] = disp
        target_finding["disposition"] = disp
        target_finding["reason"] = reason
        target_finding["disposition_reason"] = reason
        target_finding["decision"] = event
        target_finding["decision_evidence"] = validated_ev

    return updated
