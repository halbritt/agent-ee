"""Inventory exact board model references and explicitly cache stock KiCad models.

Inventory is read-only and never uses the network. A resolved reference means
the enabled, referenced file exists; native rendering logs establish whether
KiCad actually loaded it. Fetching is a separate, explicitly invoked operation.
"""

import hashlib
import json
import os
import re
import tempfile
import time
from http.client import HTTPException
from pathlib import Path, PureWindowsPath
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .storage import digest


STOCK_REPOSITORY = "https://gitlab.com/kicad/libraries/kicad-packages3D"
# Verified with git ls-remote on 2026-10-17. The second commit is 6.0.11^{}.
STOCK_REVISIONS = (
    "8975715167d1a76f9b44be155ac02326f704cdbb",
    "417c4ea884a0d1501061f511730604890bbb0a35",
)
_RECEIPT = ".agent-ee-models.json"
_TOKEN = re.compile(r'\s+|;[^\n]*|"(?:\\.|[^"\\])*"|[()]|[^()\s";]+')
_VARIABLE = re.compile(r"\$\{([^{}]+)\}|\$\(([^()]+)\)")
_STOCK_REFERENCE = re.compile(r"^\$\{(KICAD\d+_3DMODEL_DIR|KISYS3DMOD)\}/(.+)$")


class ModelFetchError(ValueError):
    """An explicit stock-model fetch failed without substituting another model."""


def _unquote(token):
    escapes = {'"': '"', "\\": "\\", "n": "\n", "r": "\r", "t": "\t"}
    return re.sub(r"\\(.)", lambda match: escapes.get(match[1], "\\" + match[1]), token[1:-1])


def _parse_board(text):
    """Parse lists iteratively so nested board geometry does not use Python's stack."""
    roots, stack = [], []
    position = 0
    while position < len(text):
        match = _TOKEN.match(text, position)
        if match is None:
            raise ValueError(f"invalid or unterminated KiCad string at character {position}")
        token = match[0]
        position = match.end()
        if token.isspace() or token.startswith(";"):
            continue
        if token == "(":
            node = []
            (stack[-1] if stack else roots).append(node)
            stack.append(node)
        elif token == ")":
            if not stack:
                raise ValueError("unexpected closing parenthesis in KiCad board")
            stack.pop()
        else:
            if not stack:
                raise ValueError("unexpected text outside KiCad board")
            stack[-1].append(_unquote(token) if token.startswith('"') else token)
    if stack:
        raise ValueError("unclosed parenthesis in KiCad board")
    if len(roots) != 1 or not roots[0] or roots[0][0] != "kicad_pcb":
        raise ValueError("model inventory requires one kicad_pcb document")
    return roots[0]


def _children(node, kind):
    return [child for child in node if isinstance(child, list) and child and child[0] == kind]


def _project_variables(board):
    project = board.with_suffix(".kicad_pro")
    if not project.exists():
        return {}, None
    raw = project.read_bytes()
    try:
        document = json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"invalid KiCad project JSON: {project}") from exc
    variables = document.get("text_variables", {}) if isinstance(document, dict) else None
    if not isinstance(variables, dict) or any(not isinstance(key, str) or not isinstance(value, str)
                                              for key, value in variables.items()):
        raise ValueError(f"KiCad project text_variables must be a string mapping: {project}")
    return variables, {"path": str(project), "sha256": hashlib.sha256(raw).hexdigest()}


def _expand_path(path, variables):
    seen = set()
    for _ in range(32):
        if path in seen:
            break
        seen.add(path)
        expanded = _VARIABLE.sub(lambda match: variables.get(match[1] or match[2], match[0]), path)
        if expanded == path:
            break
        path = expanded
    unresolved = sorted({match[1] or match[2] for match in _VARIABLE.finditer(path)})
    return path, unresolved


def _enabled(model):
    if "hide" in model:
        return False
    hidden = _children(model, "hide")
    return not hidden or (len(hidden[-1]) > 1 and hidden[-1][1] == "no")


def _inspect_model(model, board, variables):
    path = model[1] if len(model) > 1 and isinstance(model[1], str) else None
    enabled = _enabled(model)
    record = {"path": path, "enabled": enabled}
    if not enabled:
        return {**record, "status": "disabled"}
    if not path:
        return {**record, "status": "unresolved", "reason": "model has no external filename"}
    expanded, unresolved = _expand_path(path, variables)
    if unresolved:
        return {**record, "status": "unresolved", "unresolved_variables": unresolved,
                "reason": "unresolved or cyclic model path variables"}
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://|^:[^:]+:", expanded):
        return {**record, "status": "unresolved", "reason": "embedded, URI, or KiCad alias path requires native resolution"}
    if PureWindowsPath(expanded).is_absolute() and not Path(expanded).is_absolute():
        return {**record, "status": "unresolved", "reason": "Windows absolute path is not local on this host"}
    resolved = Path(expanded).expanduser()
    if not resolved.is_absolute():
        resolved = board.parent / resolved
    resolved = resolved.resolve()
    record["resolved_path"] = str(resolved)
    if not resolved.is_file():
        return {**record, "status": "missing", "reason": "referenced model file does not exist"}
    return {**record, "status": "resolved", "sha256": digest(resolved), "size_bytes": resolved.stat().st_size}


def _reference(footprint):
    for prop in _children(footprint, "property"):
        if len(prop) > 2 and prop[1] == "Reference":
            return prop[2]
    for text in _children(footprint, "fp_text"):
        if len(text) > 2 and text[1] == "reference":
            return text[2]
    return None


def _footprint_status(models):
    if not models:
        return "no_model"
    active = {model["status"] for model in models if model["enabled"]}
    if not active:
        return "disabled"
    if active == {"resolved"}:
        return "resolved"
    if "resolved" in active:
        return "partial"
    return "unresolved" if "unresolved" in active else "missing"


def inspect_models(board: Path, env: dict) -> dict:
    """Inventory every footprint, using supplied environment and project text vars.

    Supplied variables override project text variables. KIPRJMOD defaults to the
    board directory. Relative filenames are relative to that board directory.
    Embedded models and custom KiCad aliases remain explicitly unresolved.
    Malformed input raises ValueError; filesystem failures propagate as OSError.
    """
    board = Path(board).expanduser().resolve()
    raw = board.read_bytes()
    document = _parse_board(raw.decode("utf-8"))
    project_variables, project = _project_variables(board)
    variables = {**project_variables, **env}
    variables.setdefault("KIPRJMOD", str(board.parent))
    footprints = []
    for node in document:
        if not isinstance(node, list) or not node or node[0] not in ("footprint", "module"):
            continue
        models = [_inspect_model(model, board, variables) for model in _children(node, "model")]
        footprints.append({"reference": _reference(node),
                           "footprint": node[1] if len(node) > 1 and isinstance(node[1], str) else None,
                           "status": _footprint_status(models), "models": models})
    models = [model for footprint in footprints for model in footprint["models"]]
    summary = {"footprints": len(footprints), "models": len(models),
               "no_model": sum(footprint["status"] == "no_model" for footprint in footprints),
               "footprints_fully_resolved": sum(footprint["status"] == "resolved" for footprint in footprints)}
    summary.update({status: sum(model["status"] == status for model in models)
                    for status in ("resolved", "missing", "unresolved", "disabled")})
    return {"schema": "agent-ee.models3d/1", "board": {"path": str(board), "sha256": hashlib.sha256(raw).hexdigest()},
            "project": project, "footprints": footprints, "summary": summary,
            "warnings": ["File availability is not proof that KiCad loaded a model; inspect native render logs."]}


def _stock_relative(path):
    parts = path.split("/")
    if (len(parts) != 2 or not re.fullmatch(r"[A-Za-z0-9_.+-]+\.3dshapes", parts[0])
            or not re.fullmatch(r"[A-Za-z0-9_.+(), =-]+\.(?:step|stp|wrl)", parts[1], re.IGNORECASE)
            or any(part in (".", "..") for part in parts)):
        raise ModelFetchError(f"unsafe or unsupported stock model path: {path!r}")
    return Path(*parts)


def _cache_path(cache, relative):
    path = cache / relative
    if path.is_symlink() or not path.resolve().is_relative_to(cache):
        raise ModelFetchError(f"model cache path escapes the cache or is a symlink: {relative}")
    return path


def _atomic_json(path, document):
    if path.is_symlink():
        raise ModelFetchError(f"model cache receipt must not be a symlink: {path}")
    descriptor, temporary = tempfile.mkstemp(prefix=".models-receipt-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(document, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _read_receipt(cache):
    path = cache / _RECEIPT
    if path.is_symlink():
        raise ModelFetchError(f"model cache receipt must not be a symlink: {path}")
    if not path.exists():
        return {"schema": "agent-ee.stock-models/1", "repository": STOCK_REPOSITORY, "files": {}}
    try:
        receipt = json.loads(path.read_text())
    except ValueError as exc:
        raise ModelFetchError(f"invalid model cache receipt: {path}") from exc
    if (not isinstance(receipt, dict) or receipt.get("schema") != "agent-ee.stock-models/1"
            or receipt.get("repository") != STOCK_REPOSITORY or not isinstance(receipt.get("files"), dict)):
        raise ModelFetchError(f"unsupported model cache receipt: {path}")
    for relative, entry in receipt["files"].items():
        _validate_receipt_entry(relative, entry)
    return receipt


def _validate_receipt_entry(relative, entry):
    _stock_relative(relative)
    if (not isinstance(entry, dict) or not isinstance(entry.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
            or type(entry.get("size_bytes")) is not int or entry["size_bytes"] < 0):
        raise ModelFetchError(f"invalid model cache receipt entry: {relative}")
    if entry.get("source") == "existing-local":
        if "commit" not in entry and "source_url" not in entry:
            return
    elif (entry.get("commit") in STOCK_REVISIONS
          and entry.get("source_url") == _model_url(entry["commit"], relative)):
        return
    raise ModelFetchError(f"invalid model cache provenance: {relative}")


def _model_url(revision, relative):
    return f"{STOCK_REPOSITORY}/-/raw/{revision}/{quote(relative, safe='/')}"


def _download_model(path, url, *, timeout, max_file_bytes):
    request = Request(url, headers={"User-Agent": "agent-ee-stock-models/1"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return _save_download(response, path, url, timeout=timeout, max_file_bytes=max_file_bytes)
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise ModelFetchError(f"stock model request failed (HTTP {exc.code}): {url}") from exc
    except (URLError, TimeoutError, OSError, HTTPException) as exc:
        raise ModelFetchError(f"stock model request failed: {url}: {exc}") from exc


def _save_download(response, path, url, *, timeout, max_file_bytes):
    declared = response.headers.get("Content-Length")
    try:
        expected = int(declared) if declared is not None else None
    except ValueError as exc:
        raise ModelFetchError(f"invalid stock model Content-Length: {url}") from exc
    if expected is not None and (expected < 0 or expected > max_file_bytes):
        raise ModelFetchError(f"stock model exceeds {max_file_bytes} bytes or has invalid length: {url}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".model-download-", dir=path.parent)
    size, checksum, prefix = 0, hashlib.sha256(), bytearray()
    started = time.monotonic()
    try:
        with os.fdopen(descriptor, "wb") as stream:
            for chunk in iter(lambda: response.read(64 * 1024), b""):
                size += len(chunk)
                if size > max_file_bytes:
                    raise ModelFetchError(f"stock model exceeds {max_file_bytes} bytes: {url}")
                if time.monotonic() - started > timeout:
                    raise ModelFetchError(f"stock model download exceeded {timeout} seconds: {url}")
                if len(prefix) < 64:
                    prefix.extend(chunk[:64 - len(prefix)])
                checksum.update(chunk)
                stream.write(chunk)
            if expected is not None and size != expected:
                raise ModelFetchError(f"stock model download was truncated: expected {expected}, received {size}: {url}")
            header = bytes(prefix).lstrip()
            signature = b"#VRML V2.0" if path.suffix.lower() == ".wrl" else b"ISO-10303-21;"
            if not header.startswith(signature):
                raise ModelFetchError(f"download is not the referenced {path.suffix} model: {url}")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {"source_url": url, "sha256": checksum.hexdigest(), "size_bytes": size}


def _required_stock_models(inventory):
    required, skipped = {}, []
    for footprint in inventory["footprints"]:
        for model in footprint["models"]:
            if not model["enabled"]:
                continue
            match = _STOCK_REFERENCE.fullmatch(model["path"] or "")
            if match is None:
                skipped.append({"reference": footprint["reference"], "path": model["path"],
                                "reason": "not an explicit KiCad stock model variable reference"})
                continue
            relative = _stock_relative(match[2]).as_posix()
            required.setdefault(relative, []).append(footprint["reference"])
    return required, skipped


def _fetch_one(cache, relative, receipt, *, timeout, max_file_bytes):
    path = _cache_path(cache, relative)
    if path.exists():
        if not path.is_file():
            raise ModelFetchError(f"stock model cache entry is not a file: {path}")
        checksum = digest(path)
        known = receipt["files"].get(relative)
        if known and known.get("sha256") != checksum:
            raise ModelFetchError(f"cached model changed since its receipt was written: {path}")
        provenance = known or {"sha256": checksum, "size_bytes": path.stat().st_size, "source": "existing-local"}
        receipt["files"][relative] = provenance
        return {**provenance, "status": "reused" if known else "existing-local"}
    attempts = []
    for revision in STOCK_REVISIONS:
        url = _model_url(revision, relative)
        downloaded = _download_model(path, url, timeout=timeout, max_file_bytes=max_file_bytes)
        if downloaded is not None:
            provenance = {**downloaded, "commit": revision}
            receipt["files"][relative] = provenance
            return {**provenance, "status": "downloaded"}
        attempts.append({"commit": revision, "source_url": url, "status": 404})
    return {"status": "not-found", "attempts": attempts}


def fetch_stock_models(board: Path, cache: Path, *, env=None, timeout=30,
                       max_file_bytes=32 * 1024 * 1024) -> dict:
    """Explicitly fetch exact enabled stock references into a persistent cache.

    Only two-component .3dshapes paths beneath KISYS3DMOD or
    KICAD<number>_3DMODEL_DIR are eligible. No project files are uploaded.
    A 404 tries the same filename at the pinned legacy revision; missing files
    remain missing. Other network, path, and size failures raise ModelFetchError.
    Existing files are preserved and identified as local unless receipts exist.
    """
    if timeout <= 0 or max_file_bytes <= 0:
        raise ValueError("model download timeout and size limit must be positive")
    inventory = inspect_models(board, {} if env is None else env)
    required, skipped = _required_stock_models(inventory)
    cache = Path(cache).expanduser().resolve()
    cache.mkdir(parents=True, exist_ok=True)
    receipt = _read_receipt(cache)
    files = []
    # Validate every target before the first network request or model write.
    for relative in required:
        _cache_path(cache, relative)
    for relative, references in required.items():
        record = _fetch_one(cache, relative, receipt, timeout=timeout, max_file_bytes=max_file_bytes)
        files.append({"path": relative, "references": references, **record})
        _atomic_json(cache / _RECEIPT, receipt)
    missing = sum(record["status"] == "not-found" for record in files)
    return {"schema": "agent-ee.models-fetch/1", "board": inventory["board"], "cache": str(cache),
            "repository": STOCK_REPOSITORY, "revisions": list(STOCK_REVISIONS),
            "status": "partial" if missing else "complete", "files": files, "skipped": skipped,
            "downloaded": sum(record["status"] == "downloaded" for record in files),
            "reused": sum(record["status"] in ("reused", "existing-local") for record in files),
            "missing": missing}
