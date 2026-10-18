"""Finding validation, non-voting collection, and evidence-grounded adjudication."""

import copy
import hashlib
import json
import re
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
FINDING_OPTIONAL_KEYS = frozenset(("downgraded_from", "validation_note", "witnesses"))
DECISIONS_TOP_KEYS = frozenset(("schema", "candidate_id", "run_id", "decisions"))
DECISION_REQUIRED_KEYS = frozenset(("finding_id", "disposition", "reason", "evidence"))
DECISION_OPTIONAL_KEYS = frozenset(("duplicate_of", "checks", "previous_run"))

_LOCATOR_STOPWORDS = frozenset({
    "a", "an", "and", "after", "all", "are", "as", "at", "be", "block", "blocks",
    "both", "bullet", "by", "canonical", "check", "col", "column", "confirms",
    "content", "criteria", "criterion", "deck", "detail", "details",
    "element", "elements", "empty", "entities", "entity", "entry", "entries",
    "equals", "export", "failed", "field", "fields", "file", "for", "from",
    "header", "identical", "in", "independent", "is", "island", "islands",
    "item", "items", "key", "keys", "line", "lines", "list", "mapping", "matches",
    "membership", "min", "max", "missing", "name", "naming", "net", "nets", "no",
    "none", "not", "observation", "observed", "of", "on", "one", "only", "or",
    "pad", "pads", "packet", "page", "pages", "part", "parts", "per", "present",
    "project", "quote", "re-export", "reason", "record", "records", "ref",
    "report", "result", "results", "row", "rows", "rule", "rules", "section",
    "sections", "sentence", "sentences", "setup", "sha256", "sheet", "source",
    "status", "summary", "symbol", "symbols", "table", "target",
    "targets", "template", "text", "that", "the", "to", "token", "tokens",
    "tool", "tracks", "unit", "units", "unverified", "value", "values", "via",
    "vias", "whole", "with", "within", "without",
})


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


def _resolve_json_steps(node, steps):
    cur = node
    for seg in steps:
        if isinstance(cur, dict):
            if seg not in cur:
                return False, None
            cur = cur[seg]
        elif isinstance(cur, list):
            if seg.isdigit() and 0 <= int(seg) < len(cur):
                cur = cur[int(seg)]
            else:
                matches = [
                    el for el in cur
                    if isinstance(el, dict)
                    and any(el.get(k) == seg for k in ("id", "name", "key", "ref", "code", "role", "metric"))
                ]
                if not matches:
                    return False, None
                cur = matches[0] if len(matches) == 1 else matches
        else:
            return False, None
    return True, cur


def _resolve_json_path(doc, steps, *, root_only=False):
    ok, val = _resolve_json_steps(doc, steps)
    if ok or root_only:
        return ok, val
    if isinstance(doc, dict):
        for sub_key in ("details", "coverage", "baseline", "scope", "report", "metrics"):
            if sub_key in doc and isinstance(doc[sub_key], (dict, list)):
                ok, val = _resolve_json_steps(doc[sub_key], steps)
                if ok:
                    return True, val
    return False, None


def _json_contains_scalar_or_key(node, token):
    t_low = token.lower()
    if isinstance(node, dict):
        for k, v in node.items():
            if str(k).lower() == t_low or _json_contains_scalar_or_key(v, token):
                return True
        return False
    if isinstance(node, list):
        if token.isdigit() and 0 <= int(token) < len(node):
            return True
        return any(_json_contains_scalar_or_key(el, token) for el in node)
    if node is None:
        return t_low == "null"
    if isinstance(node, bool):
        return t_low == ("true" if node else "false")
    return t_low in str(node).lower()


def _value_matches(resolved, expected_raw):
    exp = expected_raw.strip().strip("\"'")
    if isinstance(resolved, (int, float)) and not isinstance(resolved, bool):
        try:
            f_exp = float(exp)
            return abs(float(resolved) - f_exp) <= 1e-5 * max(1.0, abs(float(resolved)))
        except ValueError:
            return False
    if isinstance(resolved, (list, dict)):
        if exp == "[]" and isinstance(resolved, list) and [x for x in resolved if x != ""] == []:
            return True
        try:
            return resolved == json.loads(expected_raw)
        except Exception:
            return exp in json.dumps(resolved)
    return str(resolved).lower() == exp.lower()


def validate_citation_locator(path_norm, locator, run_dir):
    loc = locator.strip()
    target = Path(run_dir) / path_norm
    data = target.read_bytes()

    m_check = re.fullmatch(r"check:([A-Za-z0-9_.-]+)", loc)
    if m_check:
        cid = m_check.group(1)
        if path_norm == f"checks/{cid}.json" or path_norm.startswith(f"work/{cid}/"):
            return
        if path_norm.endswith(".json") and data:
            try:
                doc = json.loads(data.decode("utf-8"))
                if isinstance(doc, dict) and doc.get("id") == cid:
                    return
            except ValueError:
                pass
        raise ValueError(f"citation locator {locator!r} does not resolve in {path_norm!r}")

    if not data:
        raise ValueError(f"citation locator {locator!r} resolves to nothing in empty {path_norm!r}")

    if data.startswith((b"\x89PNG\r\n\x1a\n", b"%" + b"PDF-")):
        if loc.startswith("/") or re.search(r"\b(?:line|row)s?\s+\d+\b", loc, flags=re.IGNORECASE):
            raise ValueError(f"citation locator {locator!r} does not resolve in binary {path_norm!r}")
        return

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"citation locator {locator!r} cannot resolve in binary {path_norm!r}") from exc
    if not text.strip():
        raise ValueError(f"citation locator {locator!r} resolves to nothing in blank {path_norm!r}")
    lines = text.splitlines()
    norm_text = " ".join(re.sub(r"\)\s*\(", ") (", text).split()).lower()
    file_sha = hashlib.sha256(data).hexdigest()

    is_json = path_norm.endswith(".json")
    doc = None
    if is_json:
        try:
            doc = json.loads(text)
        except ValueError as exc:
            raise ValueError(f"cited JSON artifact is malformed: {path_norm!r}") from exc

    m_line_prefix = re.match(r"^(?:line|row)s?\s+(\d+)(?:\s*[-–]\s*(\d+))?\s*[:;]\s*(.+)$", loc, flags=re.IGNORECASE)
    for m_lr in re.finditer(r"\b(?:line|row)s?\s+(\d+)(?:\s*[-–]\s*(\d+))?\b", loc, flags=re.IGNORECASE):
        start_n = int(m_lr.group(1))
        end_n = int(m_lr.group(2)) if m_lr.group(2) else start_n
        if start_n < 1 or end_n < start_n or end_n > len(lines):
            raise ValueError(f"citation locator {locator!r} line/row out of range in {path_norm!r}")

    quote_scan = re.sub(r"\[[^\[\]]*\]", " ", loc)
    for q1, q2 in re.findall(r"'([^']+)'|\"([^\"]+)\"", quote_scan):
        q = q1 or q2
        for part in re.split(r"\.\.\.|…", q):
            part_norm = " ".join(re.sub(r"\)\s*\(", ") (", part).split()).lower()
            if part_norm and part_norm not in norm_text:
                raise ValueError(f"citation locator {locator!r} quoted text {part!r} not found in {path_norm!r}")

    unquoted = re.sub(r"'[^']*'|\"[^\"]*\"", " ", loc)
    no_neg = re.sub(r"\bno\b[^,;()]*", " ", unquoted, flags=re.IGNORECASE)

    if loc.startswith("/"):
        if is_json:
            if loc == "/":
                if not doc:
                    raise ValueError(f"citation locator {locator!r} resolves to nothing in {path_norm!r}")
                return
            unparen = re.sub(r"\([^()]*\)", " ", loc)
            ptrs = re.findall(r"(?:^|(?<=[\s,;]))(/[A-Za-z0-9_.~/-]+)", unparen)
            if not ptrs:
                raise ValueError(f"citation locator {locator!r} does not resolve in {path_norm!r}")
            for ptr in ptrs:
                steps = [s.replace("~1", "/").replace("~0", "~") for s in ptr.lstrip("/").split("/") if s]
                ok, _ = _resolve_json_path(doc, steps, root_only=True)
                if not ok:
                    raise ValueError(f"citation locator {locator!r} pointer {ptr!r} does not resolve in {path_norm!r}")
            for utok in re.findall(r"[A-Za-z0-9]+(?:_[A-Za-z0-9]+)+", no_neg):
                if utok.lower() not in norm_text:
                    raise ValueError(
                        f"citation locator {locator!r} identifier {utok!r} does not resolve in {path_norm!r}"
                    )
            return
        lead = loc.split()[0].rstrip(",;:")
        if lead.lower() not in norm_text:
            raise ValueError(f"citation locator {locator!r} does not resolve in {path_norm!r}")
        return

    if is_json:
        m_slash = re.match(r"^([A-Za-z_][A-Za-z0-9_.-]*(?:/[A-Za-z0-9_.-]+)+)(?:\s|\(|$)", loc)
        if m_slash:
            raw_path = m_slash.group(1)
            steps = [s for s in raw_path.split("/") if s]
            ok, _ = _resolve_json_path(doc, steps)
            if not ok and raw_path.lower() not in norm_text:
                raise ValueError(f"citation locator {locator!r} path {raw_path!r} does not resolve in {path_norm!r}")
            if ok:
                return

        dotted_matches = list(re.finditer(
            r"(?<![A-Za-z0-9_./-])([A-Za-z_][A-Za-z0-9_-]*(?:\.[A-Za-z_][A-Za-z0-9_-]*|\[\d+\])+)(?:=([^\s,;)]+))?",
            unquoted
        ))
        checked_dotted = False
        for m_dot in dotted_matches:
            expr, eq_val = m_dot.group(1), m_dot.group(2)
            parts = [p for p in re.split(r"\.|\[(\d+)\]", expr) if p is not None and p != ""]
            if len(parts) == 2 and (
                parts[1] in ("json", "kicad_pcb", "kicad_sch", "kicad_pro", "kicad_prl", "xml", "txt", "md", "csv", "cir", "py", "h", "log", "Cu")
                or (re.fullmatch(r"[A-Z]+\d+", parts[0]) and parts[1].isdigit())
            ):
                continue
            ok, val = _resolve_json_path(doc, parts)
            if not ok:
                raise ValueError(f"citation locator {locator!r} path {expr!r} does not resolve in {path_norm!r}")
            if eq_val is not None and not _value_matches(val, eq_val):
                raise ValueError(f"citation locator {locator!r} value mismatch for {expr!r} in {path_norm!r}")
            checked_dotted = True

        kv_matches = list(re.finditer(
            r"(?<![A-Za-z0-9_./\[-])([A-Za-z_][A-Za-z0-9_-]*)=([^\s,;)]+)",
            unquoted
        ))
        checked_kv = False
        for m_kv in kv_matches:
            k_name, eq_val = m_kv.group(1), m_kv.group(2)
            ok, val = _resolve_json_path(doc, [k_name])
            if ok:
                if not _value_matches(val, eq_val):
                    raise ValueError(f"citation locator {locator!r} value mismatch for {k_name!r} in {path_norm!r}")
                checked_kv = True
        if checked_dotted or checked_kv:
            return

        if re.fullmatch(r"[A-Za-z0-9_.-]+", loc):
            if _json_contains_scalar_or_key(doc, loc):
                return
            raise ValueError(f"citation locator {locator!r} does not resolve in {path_norm!r}")

    if loc.lower() in ("all", "all rows", "whole file", "whole sheet"):
        return
    if re.fullmatch(r"(?:line|row)s?\s+\d+(?:\s*[-–]\s*\d+)?", loc, flags=re.IGNORECASE):
        return
    m_sec_range = re.fullmatch(r"(?:section|item)s?\s+(\d+)(?:\s*[-–]\s*(\d+))?", loc, flags=re.IGNORECASE)
    if m_sec_range:
        s_n = int(m_sec_range.group(1))
        e_n = int(m_sec_range.group(2)) if m_sec_range.group(2) else s_n
        if all(re.search(rf"\b{n}\b", text) for n in range(s_n, e_n + 1)):
            return
        raise ValueError(f"citation locator {locator!r} does not resolve in {path_norm!r}")

    target_norm = norm_text
    if m_line_prefix and not is_json:
        s_ln = int(m_line_prefix.group(1))
        e_ln = int(m_line_prefix.group(2)) if m_line_prefix.group(2) else s_ln
        slice_text = "\n".join(lines[s_ln - 1 : e_ln])
        target_norm = " ".join(re.sub(r"\)\s*\(", ") (", slice_text).split()).lower()

    loc_norm = " ".join(re.sub(r"\)\s*\(", ") (", loc).split()).lower()
    if loc_norm in target_norm:
        return

    m_absent_from = re.search(r"\b([A-Za-z0-9_.-]+)\s+absent\s+from\b", loc, flags=re.IGNORECASE)
    if m_absent_from and not is_json:
        missing_tok = m_absent_from.group(1).lower()
        if not re.search(rf"\b{re.escape(missing_tok)}\b", norm_text):
            return

    m_abs = re.match(r"^(?:whole\s+(?:file|sheet)\s*[:;]?\s*)?no\s+(.+)$", loc, flags=re.IGNORECASE)
    if m_abs and not is_json:
        absent_tokens = [
            t.strip("._-") for t in re.findall(r"[A-Za-z0-9_.-]+", m_abs.group(1).lower())
            if t.strip("._-") and t.strip("._-") not in _LOCATOR_STOPWORDS and t.strip("._-") not in ("nodes", "node", "anywhere")
        ]
        if absent_tokens and all(f"({t}" not in norm_text for t in absent_tokens):
            return

    cleaned = re.sub(r"\b(?:line|row|section|item)s?\s+\d+(?:\s*[-–]\s*\d+)?\b", " ", loc.lower())
    raw_tokens = [t.strip("._-") for t in re.findall(r"[A-Za-z0-9_.-]+", cleaned)]
    tokens = [t for t in raw_tokens if t and not all(c == "." for c in t)]
    substantive = [t for t in tokens if t not in _LOCATOR_STOPWORDS]
    check_tokens = substantive if substantive else tokens
    if not check_tokens:
        raise ValueError(f"citation locator {locator!r} does not resolve in {path_norm!r}")
    matched = [
        t for t in check_tokens
        if t in target_norm or (len(t) >= 8 and file_sha.startswith(t))
    ]
    if not matched:
        raise ValueError(f"citation locator {locator!r} does not resolve in {path_norm!r}")


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


def validate_citation(item, run_dir):
    if not isinstance(item, dict) or set(item.keys()) != {"path", "locator"}:
        raise ValueError(f"malformed evidence citation: {item!r}")
    path_norm = validate_citation_path(item["path"], run_dir)
    locator = _nonempty_str(item["locator"], "evidence locator")
    validate_citation_locator(path_norm, locator, run_dir)
    return {"path": path_norm, "locator": locator}


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
            path_norm = validate_citation_path(p, run_dir)
            if isinstance(entry, dict) and "locator" in entry:
                loc = _nonempty_str(entry["locator"], "evidence locator")
                validate_citation_locator(path_norm, loc, run_dir)
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
    unknown_top = set(value.keys()) - REVIEW_TOP_KEYS
    missing_top = REVIEW_TOP_KEYS - set(value.keys())
    if missing_top or unknown_top:
        raise ValueError(f"review keys mismatch: missing={sorted(missing_top)}, extra={sorted(unknown_top)}")
    if value.get("schema") != "agent-ee.review/1":
        raise ValueError(f"expected schema 'agent-ee.review/1', got {value.get('schema')!r}")
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
        evidence = [validate_citation(c, run_dir) for c in raw_ev]

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

    return {
        "schema": "agent-ee.review/1",
        "candidate_id": candidate_id,
        "run_id": run_id,
        "packet_sha256": packet_sha256,
        "role": role,
        "coverage": coverage,
        "limitations": limitations,
        "findings": validated_findings,
    }


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
                ev_list.append({"path": entry["path"], "locator": entry.get("locator", f"check:{cid}")})
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
    if decisions.get("schema") != "agent-ee.decisions/1":
        raise ValueError(f"expected schema 'agent-ee.decisions/1', got {decisions.get('schema')!r}")

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
        validated_ev = [validate_citation(c, run_dir) for c in raw_ev]

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
            from .baseline import load_baseline, validate_witnesses
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
