"""Private, immutable render publication and a read-only gallery server."""
import argparse
import contextlib
import errno
import fcntl
import functools
import hashlib
import html
import ipaddress
import json
import os
import re
import secrets
import tempfile
from io import BytesIO
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .candidate import verify_snapshot
from .render3d import _VIEWS as _VIEW_COMMANDS, RenderError, _png_info
from .storage import digest
from .symbol_library import LibraryError, children, parse_sexpr
from .viewer import _ASSETS, _source_board, _stage_board, _validate_glb

_VIEWS = ("top", "bottom", "isometric")
# Host paths are found by a structural left-to-right scan, not a backtracking
# regex. Tokens are:
# - a file URI: a case-insensitive "file:" at ANY position followed by a slash or a
#   drive. "file:" plus two slashes is followed by an authority (host), which is
#   always removed and never kept as a basename; the path after it gives the
#   basename, and an authority-only, empty or root path leaves just <redacted>;
# - another URL (scheme://..., kept as is) and KiCad variables (${VAR}/..., kept);
# - an absolute path: a POSIX or single backslash root, a drive letter (either
#   slash) or a UNC prefix,
#   whose host (\\host or //host) is never a basename either.
# A host ends at a slash, whitespace, a stop or a component boundary (below) and
# otherwise runs through brackets and punctuation: [fd00::1] and host(1) are hosts
# whole. A host interrupted by whitespace, a quote or a group and continued by a
# backslash word (\\host "user"\share) is removed whole, without a basename.
# A path runs through spaces, commas and brackets (directory names contain them);
# a URL ends at whitespace.
# Both stop at a quote, < > | or a newline, at any "file:" URI, and at a component
# boundary: a cluster of whitespace, "," ";" and brackets ( ) [ ] { } followed by
# another URI, UNC host or drive path (when the cluster holds "," or ";", also any
# absolute path). So a later URI's authority or UNC host never becomes an earlier
# path's basename. A UNC "\\" starts a host wherever it is not part of a longer
# slash run, and a path with a doubled separator after its root is ambiguous and
# keeps no basename at all (fail closed: over-redact rather than leak a host).
# Already redacted text is left alone, so serving a redacted bundle is idempotent.
_STOP = frozenset("'\"<>|\r\n")
_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")
_DRIVE = re.compile(r"[A-Za-z]:[\\/]")
_MARK = "<redacted>"
_CLUSTER = re.compile(r"[\s,;()\[\]{}]*")
_BOUNDARY = ",;()[]{}"
_CLOSE = {")": "(", "]": "[", "}": "{"}
_SKIP = re.compile(r"[\s'\"<>|,;()\[\]{}]*")
_COUNTS = ("resolved", "missing", "unresolved", "disabled", "no_model")
_ID = r"[a-f0-9]{12}-[a-f0-9]{12}"
_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
# Supplement status vocabulary. Anything else is refused at publication and shown
# as unknown when served. "unrouted" is checked against the retained board.
_ROUTING = {"unrouted": "Unrouted (verified: the retained board has no tracks or vias)",
            "partially-routed": "Partially routed (publisher statement; tracks or vias present)",
            "routed": "Routed (publisher statement; completeness not verified)"}
_ADOPTION = {"not-adopted": "Proposed change NOT adopted (publisher statement)",
             "adopted": "Proposed change adopted (publisher statement, not verified)",
             "no-proposal": "No change proposed (publisher statement)"}


def _file_at(text, i):
    """True when a file URI starts at i: "file:" (any case) then a slash or a drive."""
    return (text[i:i + 5].lower() == "file:" and i + 5 < len(text)
            and (text[i + 5] in "/\\" or _DRIVE.match(text, i + 5) is not None))


def _double(text, i):
    return len(text) > i + 1 and text[i] in "/\\" and text[i + 1] in "/\\"


def _token_at(text, i, single):
    """True when another URI, UNC host or drive path (or, if single, any root) starts at i."""
    return i < len(text) and (_file_at(text, i) or _SCHEME.match(text, i) is not None or _double(text, i)
                              or _DRIVE.match(text, i) is not None or (single and text[i] in "/\\"))


def _scan(text, i, url=False):
    """Index where a path (or, with url, a URL) starting at i ends. Linear in the text."""
    while i < len(text):
        char = text[i]
        if char in _STOP or _file_at(text, i) or (url and char.isspace()):
            return i
        if char.isspace() or char in _BOUNDARY:
            j = _CLUSTER.match(text, i + 1).end()
            if _token_at(text, j, any(c in ",;" for c in text[i:j])):
                return i
            i = j
        else:
            i += 1
    return i


def _path(text, root):
    """Return (end, replacement) for the path whose root (a slash or drive) ends at index root.

    A path whose first component would start with whitespace, a delimiter or a
    stop is just its root (file://host/ and FILE://host/, both stay <redacted>).
    """
    end = root
    if end < len(text) and not text[end].isspace() and text[end] not in _BOUNDARY:
        end = _scan(text, end)
        while text[end - 1].isspace():
            end -= 1  # whitespace before a quote or stop is not part of the path
    if re.search(r"[\\/]{2}", text[root - 1:end].rstrip("/\\ \t")):
        return end, _MARK  # a/b//c or a\\host: ambiguous, so no basename
    parts = [p for p in re.split(r"[\\/]", text[:end].rstrip("/\\ \t")[root - 1:]) if p]
    if parts and _DRIVE.match(parts[0] + "/") and len(parts[0]) == 2:
        parts = parts[1:]
    return end, _MARK + "/" + parts[-1] if parts else _MARK


def _trim(text, start, end):
    """End of the pathless host text[start:end] without trailing whitespace, , ; : =, openers or unmatched closers.

    (\\\\host) and (file://host) keep their ")"; \\\\host(1) and file://[::1] are hosts whole.
    """
    opened, unmatched = {}, set()
    for k in range(start, end):
        if text[k] in "([{":
            opened[text[k]] = opened.get(text[k], 0) + 1
        elif text[k] in _CLOSE:
            if opened.get(_CLOSE[text[k]]):
                opened[_CLOSE[text[k]]] -= 1
            else:
                unmatched.add(k)
    while end > start and (text[end - 1] in ",;:=([{" or text[end - 1].isspace() or end - 1 in unmatched):
        end -= 1
    return end


def _continuation(text, i):
    """End of the rest of a host operand interrupted at i, or None when none follows.

    A quote, whitespace or grouping run, then a word holding a backslash (or a slash
    right after a quote or closing bracket) continues the same UNC/authority path:
    \\\\host "user"\\share\\m.step. It is removed whole, with no basename.
    """
    p = _SKIP.match(text, i).end()
    if p >= len(text) or _token_at(text, p, True):
        return None  # nothing follows, or a new path or URI of its own
    for q in range(p, len(text)):
        if text[q].isspace() or _token_at(text, q, False):
            return None  # the word ends, or a further URI or UNC host starts in it
        if text[q] == "\\" or (text[q] == "/" and text[q - 1] in "\"')]}"):
            return _trim(text, i, _scan(text, q + 1))
    return None


def _host(text, i):
    """Return (end, replacement) for a host (file URI authority or UNC host) starting at i.

    The host is removed and never becomes a basename; only a path after it can.
    It runs through brackets, "," and ";" (an IP literal [fd00::1], host(1)) up to a
    slash, whitespace, a stop or a component boundary. Another double slash right
    after a host starts a further host.
    """
    while True:
        start, opened = i, []
        while i < len(text) and text[i] not in "/\\" and text[i] not in _STOP \
                and not text[i].isspace() and not _file_at(text, i):
            if text[i] in _BOUNDARY and not (opened and _CLOSE.get(text[i]) == opened[-1]):
                j = _CLUSTER.match(text, i + 1).end()
                if _token_at(text, j, any(c in ",;" for c in text[i:j])):
                    break
                while i < j and not text[i].isspace():  # the cluster is part of the host
                    if opened and _CLOSE.get(text[i]) == opened[-1]:
                        opened.pop()
                    elif text[i] in "([{":
                        opened.append(text[i])
                    i += 1
                continue
            if opened and _CLOSE.get(text[i]) == opened[-1]:
                opened.pop()  # closes a bracket of the host itself: [fd00::1]
            i += 1
        if not _double(text, i):
            break
        i += 2
    if i < len(text) and text[i] in "/\\":
        return _path(text, i + 1)
    if i < len(text) and (text[i].isspace() or text[i] in _STOP):
        end = _continuation(text, i)
        if end is not None:
            return end, _MARK
    return _trim(text, start, i), _MARK


def _token(text, i):
    """Return (end, replacement) for a file URI or absolute path at i, or None."""
    if _file_at(text, i):
        i += 5
        if _double(text, i):
            return _host(text, i + 2)
        return _path(text, i + (3 if _DRIVE.match(text, i) else 1))
    if text.endswith(_MARK, 0, i) or i and text[i - 1] in "/\\":
        return None  # already redacted text; the rest of a slash run
    if text.startswith("\\\\", i):
        return _host(text, i + 2)  # a UNC host even when glued: m.step\\host
    if i and (text[i - 1].isalnum() or text[i - 1] in "_.}"):
        return None  # ${VAR}/x, and/or, v1.2/3: not host paths
    if _double(text, i):
        return _host(text, i + 2)
    if text[i] == "/" or (text[i] == "\\" and i + 1 < len(text) and not text[i + 1].isspace()
                          and text[i + 1] not in _STOP):
        return _path(text, i + 1)  # a POSIX root, or a Windows root-relative \share\m.step
    if _DRIVE.match(text, i):
        return _path(text, i + 3)
    return None


def _redact_text(text):
    out, kept, i = [], 0, 0
    while i < len(text):
        if not (i and (text[i - 1].isalnum() or text[i - 1] in "_.+-")) \
                and _SCHEME.match(text, i) and not _file_at(text, i):
            # Kept as is up to whitespace or a stop; a "file:" inside the scheme
            # (x-file:///...) still starts a file URI.
            i = _scan(text, i + 1, url=True)
            continue
        token = _token(text, i)
        if token is None:
            i += 1
            continue
        out += [text[kept:i], token[1]]
        kept = i = token[0]
    return "".join(out) + text[kept:]


def redact(value):
    """Replace absolute host paths in any JSON value (keys included) with <redacted>/basename."""
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, dict):
        return {redact(k): redact(v) for k, v in value.items()}
    return value


def coverage_counts(coverage):
    """Return the model counts, with None for any that are absent or not nonnegative integers."""
    summary = coverage.get("summary") if isinstance(coverage, dict) else None
    summary = summary if isinstance(summary, dict) else {}
    return {key: value if type(value) is int and value >= 0 else None
            for key, value in ((key, summary.get(key)) for key in _COUNTS)}


def _json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _encode(data):
    return (json.dumps(data, indent=2) + "\n").encode()


def _artifact(source, relative):
    if not isinstance(relative, str) or not relative:
        raise ValueError("unsafe artifact path")
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("unsafe artifact path")
    file = source / relative
    if any(p.is_symlink() for p in (file, *file.parents)) or not file.is_file():
        raise ValueError("artifact must be a regular file without symlinks")
    return file


def _read(source, relative, expected=None):
    """Read a source artifact once; the returned bytes are the ones hashed and published."""
    file = _artifact(source, relative)
    try:
        descriptor = os.open(file, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as error:
        raise ValueError(f"artifact must be a regular file without symlinks: {error.strerror}") from None
    with os.fdopen(descriptor, "rb") as stream:
        data = stream.read()
    if expected is not None and hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("artifact hash mismatch")
    return data


def _directory(parent, name):
    """Open (creating if absent) a directory entry without following a symlink."""
    for _ in range(2):
        try:
            return os.open(name, _DIRECTORY, dir_fd=parent)
        except FileNotFoundError:
            with contextlib.suppress(FileExistsError):
                os.mkdir(name, 0o755, dir_fd=parent)
        except OSError as error:
            if error.errno in (errno.ELOOP, errno.ENOTDIR):
                raise ValueError(f"publication destination {name!r} must be a directory, not a symlink") from None
            raise
    raise ValueError(f"publication destination {name!r} could not be opened")


def _put(parent, name, data):
    """Create a new file; never follows a symlink or replaces an existing entry."""
    try:
        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                             0o644, dir_fd=parent)
    except FileExistsError:
        raise ValueError(f"publication file {name!r} already exists") from None
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)


def _put_tree(parent, source):
    for item in sorted(source.iterdir()):
        if item.is_dir():
            os.mkdir(item.name, 0o755, dir_fd=parent)
            child = _directory(parent, item.name)
            try:
                _put_tree(child, item)
            finally:
                os.close(child)
        else:
            _put(parent, item.name, item.read_bytes())


def _remove(parent, name):
    child = os.open(name, _DIRECTORY, dir_fd=parent)
    try:
        for entry in list(os.scandir(child)):
            if entry.is_dir(follow_symlinks=False):
                _remove(child, entry.name)
            else:
                os.unlink(entry.name, dir_fd=child)
    finally:
        os.close(child)
    os.rmdir(name, dir_fd=parent)


@contextlib.contextmanager
def _publication(root, area, prefix):
    """Lock the gallery and stage a new publication beside its area, all opened without symlinks.

    Every component of root, root/area and the staging directory are opened
    with O_NOFOLLOW and used through directory descriptors, so a link planted at
    any level refuses publication instead of redirecting writes outside root.
    """
    root = Path(os.path.abspath(root))
    descriptors = []
    try:
        descriptors.append(os.open("/", _DIRECTORY))
        for part in root.parts[1:]:
            descriptors.append(_directory(descriptors[-1], part))
            os.close(descriptors.pop(-2))
        root_fd = descriptors[0]
        try:
            lock = os.open(".publish.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o644,
                           dir_fd=root_fd)
        except OSError as error:
            raise ValueError(f"publication lock must be a regular file, not a symlink: {error.strerror}") from None
        descriptors.append(lock)
        fcntl.flock(lock, fcntl.LOCK_EX)
        area_fd = _directory(root_fd, area)
        descriptors.append(area_fd)
        staged = prefix + secrets.token_hex(8)
        os.mkdir(staged, 0o755, dir_fd=root_fd)
        try:
            staged_fd = _directory(root_fd, staged)
            descriptors.append(staged_fd)
            yield _Staged(root_fd, area_fd, staged, staged_fd)
        finally:
            with contextlib.suppress(FileNotFoundError):
                _remove(root_fd, staged)
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


class _Staged:
    def __init__(self, root, area, name, directory):
        self.root, self.area, self.name, self.directory = root, area, name, directory

    def commit(self, identifier, listing, entry):
        """Move the staged publication into place and prepend it to the listing.

        An existing publication is never replaced, and the listing is replaced
        atomically through an exclusive, unpredictable staging file.
        """
        try:
            descriptor = os.open(listing, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=self.root)
        except FileNotFoundError:
            entries = []
        except OSError as error:
            raise ValueError(f"{listing} must be a regular file, not a symlink: {error.strerror}") from None
        else:
            with os.fdopen(descriptor, "rb") as stream:
                entries = json.loads(stream.read().decode("utf-8"))
        try:
            os.stat(identifier, dir_fd=self.area, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ValueError(f"publication {identifier} already exists; immutable publications are never replaced")
        temporary = f".{listing}-{secrets.token_hex(8)}.next"
        _put(self.root, temporary, _encode([entry, *entries]))
        try:
            os.rename(self.name, identifier, src_dir_fd=self.root, dst_dir_fd=self.area)
        except BaseException:
            os.unlink(temporary, dir_fd=self.root)
            raise
        os.replace(temporary, listing, src_dir_fd=self.root, dst_dir_fd=self.root)


def _option(command, flag):
    return command[command.index(flag) + 1] if flag in command[:-1] else None


def _verify_receipt(renders, manifest, item, snapshot):
    """Check an image against the per-view receipt written by its own native process."""
    view = item["view"]
    try:
        receipt = _json(_artifact(renders, f"{view}.json"))
    except (OSError, ValueError) as error:
        raise ValueError(f"{view} native render receipt is missing or unreadable: {error}") from None
    command = receipt.get("command") if isinstance(receipt, dict) else None
    settings = manifest.get("settings") or {}
    side, rotation = _VIEW_COMMANDS[view]
    if (not isinstance(command, list) or not all(isinstance(c, str) for c in command) or len(command) < 4
            or command[1:3] != ["pcb", "render"] or receipt.get("view") != view
            or receipt.get("status") != "completed"
            or any(receipt.get(key) != item.get(key) for key in ("path", "sha256", "width", "height"))
            or _option(command, "--side") != side or _option(command, "--rotate") != rotation
            or Path(_option(command, "--output") or "").name != item["path"]
            or Path(command[-1]).name != snapshot.name
            or _option(command, "--width") != str(settings.get("width"))
            or _option(command, "--height") != str(settings.get("height"))):
        raise ValueError(f"{view} image does not match its native render receipt")


def _verify_snapshot_against(candidate, snapshot, project):
    """Re-stage the verified retained board and require the render snapshot to be those bytes."""
    board, _, _ = _source_board(Path(candidate).expanduser().resolve())
    with tempfile.TemporaryDirectory(prefix="agent-ee-gallery-stage-") as temporary:
        expected, _ = _stage_board(board, Path(temporary))
        expected_project = expected.with_suffix(".kicad_pro")
        if snapshot.read_bytes() != expected.read_bytes():
            raise ValueError("render snapshot board is not the retained candidate board")
        if (expected_project.read_bytes() if expected_project.is_file() else None) != (
                project.read_bytes() if project is not None else None):
            raise ValueError("render snapshot project is not the retained candidate project")


def _verify_provenance(manifest, renders, candidate):
    """Bind the render snapshot, images and receipts to each other and to the retained candidate."""
    if manifest.get("errors") or manifest.get("source_unchanged") is not True:
        raise ValueError("render manifest records errors or a changed source")
    candidate_id, captured = manifest.get("candidate_id"), manifest.get("captured_board")
    board = manifest["board"]
    retained = None
    if candidate is None:
        if candidate_id is not None or captured is not None:
            raise ValueError("render manifest names a retained candidate; pass --candidate to verify it")
    else:
        retained = verify_snapshot(candidate)
        relative = retained["scope"]["board"]
        if candidate_id != retained["candidate_id"] or captured != relative:
            raise ValueError("render manifest is bound to a different candidate")
        if board["sha256"] != retained["files"].get(relative):
            raise ValueError("render board hash is not the retained candidate board")
    records = manifest.get("snapshot") if isinstance(manifest.get("snapshot"), dict) else {}
    record = records.get("board") or {}
    if not isinstance(record, dict) or record.get("path") != f"snapshot/{board['name']}":
        raise ValueError("render snapshot board is missing or does not match the manifest")
    try:
        snapshot = _artifact(renders, record["path"])
        project = _artifact(renders, records["project"]["path"]) if records.get("project") else None
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"render snapshot board is missing or does not match the manifest: {error}") from None
    if manifest.get("staged_board_sha256") != record.get("sha256") or digest(snapshot) != record["sha256"]:
        raise ValueError("render snapshot board is missing or does not match the manifest")
    if project is not None and digest(project) != records["project"].get("sha256"):
        raise ValueError("render snapshot project does not match the manifest")
    if retained is not None:
        _verify_snapshot_against(candidate, snapshot, project)
    views, images, seen = manifest.get("views") or {}, manifest["images"], set()
    stem = Path(board["name"]).stem
    for item in images:
        view, record = item["view"], views.get(item["view"]) or {}
        if (item["path"] != f"{stem}-3d-{view}.png" or record.get("status") != "completed"
                or record.get("path") != item["path"] or record.get("sha256") != item["sha256"]):
            raise ValueError(f"{view} image is not bound to its native view record")
        seen.add(item["sha256"])
        try:
            if digest(renders / item["path"]) != item["sha256"]:
                raise ValueError("artifact hash mismatch")
            info = _png_info(renders / item["path"])
        except OSError as error:
            raise ValueError(f"{view} image is unreadable: {error}") from None
        except RenderError as error:
            raise ValueError(f"{view} image is not a valid PNG: {error}") from None
        if (info["width"], info["height"]) != (item.get("width"), item.get("height")) or info["sha256"] != item["sha256"]:
            raise ValueError(f"{view} image does not match its recorded dimensions")
    if len(seen) != 3:
        raise ValueError("view images must be distinct")
    for item in images:
        _verify_receipt(renders, manifest, item, snapshot)


def publish(root, renders, *, label, stage, viewer=None, candidate=None):
    """Publish only completed manifest-bound native images and optional viewer."""
    manifest = _json(renders / "render-manifest.json")
    if manifest.get("schema") != "agent-ee.render3d/1" or manifest.get("status") != "completed":
        raise ValueError("a completed native render3d bundle is required")
    board_hash = manifest["board"]["sha256"]
    if not re.fullmatch(r"[a-f0-9]{64}", board_hash):
        raise ValueError("invalid board hash")
    images = manifest["images"]
    if {i["view"] for i in images} != set(_VIEWS) or len(images) != 3:
        raise ValueError("top, bottom and isometric images are required")
    _verify_provenance(manifest, renders, candidate)
    with _publication(root, "bundles", ".publish-") as staged:
        record = redact({"label": label, "stage": stage, "board_sha256": board_hash,
                         "candidate_id": manifest.get("candidate_id"),
                         "published_at": datetime.now(timezone.utc).isoformat(),
                         "rendered_at": manifest.get("finished_at"),
                         "model_coverage": manifest.get("model_coverage"),
                         "warnings": manifest.get("warnings", []), "images": [], "viewer": bool(viewer)})
        for item in images:
            name = item["view"] + ".png"
            data = _read(renders, item["path"], item["sha256"])
            if data[:8] != b"\x89PNG\r\n\x1a\n":
                raise ValueError("image is not PNG")
            _put(staged.directory, name, data)
            record["images"].append({"view": item["view"], "path": name, "sha256": item["sha256"]})
        if viewer:
            metadata = _json(viewer / "viewer.json")
            if metadata.get("schema") != "agent-ee.viewer/1" or metadata["board"]["sha256"] != board_hash:
                raise ValueError("viewer belongs to a different board")
            if (metadata.get("candidate_id"), metadata.get("captured_board")) != (
                    manifest.get("candidate_id"), manifest.get("captured_board")):
                raise ValueError("viewer belongs to a different candidate")
            _validate_glb(_artifact(viewer, "board.glb"))
            model = _read(viewer, "board.glb", metadata["model_sha256"])
            os.mkdir("viewer", 0o755, dir_fd=staged.directory)
            directory = _directory(staged.directory, "viewer")
            try:
                _put(directory, "board.glb", model)
                _put_tree(directory, _ASSETS)
                _put(directory, "viewer.json", _encode(redact(metadata)))
            finally:
                os.close(directory)
        manifest_bytes = _encode(record)
        _put(staged.directory, "manifest.json", manifest_bytes)
        # Include image identity so a new export of the same board retains history.
        bundle = board_hash[:12] + "-" + hashlib.sha256(manifest_bytes).hexdigest()[:12]
        staged.commit(bundle, "index.json", {"id": bundle, **record})
    return bundle


def _copper_items(data):
    """Count track segments, arcs and vias in retained board bytes; unparseable boards refuse."""
    try:
        board = parse_sexpr(data)
    except LibraryError as error:
        raise ValueError(f"retained candidate board is not a parseable KiCad board: {error}") from None
    if board[0] != "kicad_pcb":
        raise ValueError("retained candidate board is not a parseable KiCad board")
    return sum(len(children(board, kind)) for kind in ("segment", "arc", "via"))


def publish_supplement(root, image, packet, *, candidate, board_sha256, label, stage, routing, adoption):
    """Publish an annotated PNG bound to a verified retained candidate, separately from render milestones."""
    if not isinstance(board_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", board_sha256):
        raise ValueError("invalid board hash")
    if routing not in _ROUTING:
        raise ValueError(f"routing status must be one of {sorted(_ROUTING)}; unknown status is refused")
    if adoption not in _ADOPTION:
        raise ValueError(f"adoption status must be one of {sorted(_ADOPTION)}; unknown status is refused")
    candidate = Path(candidate).expanduser().resolve()
    if not (candidate / "candidate.json").is_file():
        raise ValueError("a retained candidate run directory is required")
    # verify_snapshot recomputes every captured file hash; the board is then read once more
    # and the bytes used for the status checks are the ones bound to the publication.
    board, candidate_id, captured = _source_board(candidate)
    data = _read(board.parent, board.name)
    retained = verify_snapshot(candidate)
    if hashlib.sha256(data).hexdigest() != retained["files"][captured] or retained["candidate_id"] != candidate_id:
        raise ValueError("candidate snapshot changed; evidence is stale")
    if board_sha256 != retained["files"][captured]:
        raise ValueError("board hash mismatch: the supplied hash is not the retained candidate board")
    tracks = _copper_items(data)
    if (routing == "unrouted") != (tracks == 0):
        raise ValueError(f"routing status {routing!r} contradicts the retained board ({tracks} tracks or vias)")
    try:
        info = _png_info(_artifact(image.parent, image.name))
    except RenderError as error:
        raise ValueError(f"supplement image is not a valid PNG: {error}") from None
    image_data, packet_data = _read(image.parent, image.name, info["sha256"]), _read(packet.parent, packet.name)
    with _publication(root, "diagnostics", ".diagnostic-") as staged:
        # Retain identities, not source files or host paths, in the HTTP publication.
        record = redact({"schema": "agent-ee.gallery-diagnostic/2", "kind": "annotated-2d-diagnostic",
                         "label": label, "stage": stage, "candidate_id": candidate_id,
                         "captured_board": captured, "board_sha256": board_sha256,
                         "routing": {"status": routing, "copper_items": tracks},
                         "adoption": {"status": adoption, "verified": False},
                         "published_at": datetime.now(timezone.utc).isoformat(),
                         "source_packet": {"name": packet.name, "sha256": hashlib.sha256(packet_data).hexdigest()},
                         "source_image": {"name": image.name, "sha256": info["sha256"]},
                         "image": {"path": "image.png", **info},
                         "limitation": "Publisher verifies the retained candidate and input bytes, not the analysis "
                                       "or the adoption of any proposed change."})
        _put(staged.directory, "image.png", image_data)
        manifest_bytes = _encode(record)
        _put(staged.directory, "manifest.json", manifest_bytes)
        identifier = board_sha256[:12] + "-" + hashlib.sha256(manifest_bytes).hexdigest()[:12]
        staged.commit(identifier, "diagnostics.json", {"id": identifier, **record})
    return identifier


def _supplement_status(item, latest):
    """Status lines for a served supplement; anything not bound to a verified candidate is unknown."""
    escape = html.escape
    routing = item.get("routing") if isinstance(item.get("routing"), dict) else {}
    adoption = item.get("adoption") if isinstance(item.get("adoption"), dict) else {}
    if (item.get("schema") != "agent-ee.gallery-diagnostic/2" or not isinstance(item.get("candidate_id"), str)
            or routing.get("status") not in _ROUTING or adoption.get("status") not in _ADOPTION):
        return ('<p class="badge unknown" role="status">Status unknown: not bound to a verified retained '
                'candidate, or routing and adoption status are not recorded</p>')
    if latest is None:
        currency = "No Latest render to compare: treat as historical"
    elif latest.get("board_sha256") == item.get("board_sha256"):
        currency = "Same board as the Latest render"
    else:
        currency = "Historical: not the board in the Latest render"
    return (f'<p class="badge incomplete" role="status">{escape(currency)}</p>'
            f'<p>Retained candidate {escape(item["candidate_id"])}</p>'
            f'<p>Routing: {escape(_ROUTING[routing["status"]])}</p>'
            f'<p>Adoption: {escape(_ADOPTION[adoption["status"]])}</p>')


def diagnostic_cards(supplements, latest=None):
    escape = html.escape
    cards = []
    for item in supplements:
        identifier = str(item.get("id"))
        if not re.fullmatch(_ID, identifier):
            continue
        base = "/diagnostics/" + identifier + "/"
        cards.append(f'<section id="diagnostic-{identifier}"><h2>{escape(str(item.get("label")))}</h2>'
                     f'<p><strong>Annotated 2D diagnostic supplement</strong></p><p>{escape(str(item.get("stage")))}</p>'
                     f'{_supplement_status(item, latest)}'
                     f'<p class="hash">Board SHA-256: {escape(str(item.get("board_sha256")))}</p>'
                     f'<p>Published {escape(str(item.get("published_at")))}</p>'
                     f'<a href="{base}image.png"><img src="{base}image.png" alt="Annotated 2D diagnostic; open full-size image"></a>'
                     f'<p>{escape(str(item.get("limitation")))}</p><a href="{base}index.html">Open diagnostic</a> · <a href="{base}manifest.json">Diagnostic provenance</a></section>')
    return ''.join(cards)


def page(index, supplements=(), *, diagnostic_only=False, latest=None):
    escape = html.escape
    latest = latest if latest is not None else (index[0] if index else None)
    cards = []
    for position, item in enumerate(index):
        base = "/bundles/" + item["id"] + "/"
        pictures = "".join(f'<a href="{base}{i["path"]}"><figure><img src="{base}{i["path"]}" alt="{i["view"]} native 3D view"><figcaption>{i["view"].title()}</figcaption></figure></a>' for i in item["images"])
        viewer = f'<a class="button" href="{base}viewer/index.html">Open interactive 3D viewer</a>' if item["viewer"] else '<p>Interactive viewer not published for this candidate.</p>'
        summary = coverage_counts(item.get("model_coverage"))
        counts = list(summary.values())
        if None in counts:
            badge = '<p class="badge unknown" role="status">Model coverage unknown: do not treat this geometry as complete</p>'
        elif any(counts[1:]):
            badge = '<p class="badge incomplete" role="status">Incomplete models: geometry is missing components</p>'
        else:
            badge = '<p class="badge complete">All declared 3D models resolved</p>'
        summary = {key: "unknown" if value is None else value for key, value in summary.items()}
        coverage_text = (f'Models: {summary["resolved"]} resolved; {summary["missing"]} missing; {summary["unresolved"]} unresolved; {summary["disabled"]} disabled; {summary["no_model"]} footprints without a model.')
        warnings = "".join('<li>' + escape(redact(str(w))) + '</li>' for w in item["warnings"])
        cards.append(f'<section><h2>{"Latest: " if position == 0 else ""}{escape(item["label"])}</h2><p>{escape(item["stage"])} · Rendered {escape(str(item["rendered_at"]))}</p><p class="hash">Board SHA-256: {escape(str(item["board_sha256"]))}</p>{badge}<p class="coverage">{escape(coverage_text)}</p>{viewer}<div class="images">{pictures}</div><details><summary>Model warnings ({len(item["warnings"])})</summary><ul>{warnings}</ul></details><a href="{base}manifest.json">Publication manifest</a></section>')
    return ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>PCB render gallery</title><style>body{font:16px system-ui;background:#111923;color:#e7eef7;margin:auto;padding:24px;max-width:1400px}a{color:#8fc9ff}li{overflow-wrap:anywhere}h1{margin-bottom:8px}section{background:#1b2735;padding:24px;margin:24px 0;border-radius:12px}.images{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;margin:20px 0}figure{margin:0}img{width:100%;border-radius:8px}figcaption{padding:8px}.hash,pre{overflow-wrap:anywhere;white-space:pre-wrap;font-size:13px}.badge{display:inline-block;padding:6px 10px;border-radius:6px;font-weight:600}.incomplete{background:#7a4b00;color:#fff}.unknown{background:#8a1c1c;color:#fff}.complete{background:#1e5a3a;color:#fff}.button{display:inline-block;padding:12px;background:#294968;border-radius:6px}@media(max-width:700px){body{padding:12px}section{padding:16px}.images{grid-template-columns:1fr}}</style>' + ('<h1>PCB diagnostic supplement</h1><p><a href="/">Back to render gallery</a></p>' if diagnostic_only else '<h1>PCB render gallery</h1><p>Native KiCad images · Latest candidate and retained history</p>') + '<p>Visual inspection only. Missing component models can make geometry incomplete; these images do not approve electrical or fabrication readiness.</p>' + ('<p><a href="#diagnostics">Diagnostic supplements</a></p>' if supplements else '') + (''.join(cards) or ('' if diagnostic_only else '<p>No completed render bundles have been published yet.</p>')) + ('<div id="diagnostics"><h2>Diagnostic supplements</h2>' + diagnostic_cards(supplements, latest) + '</div>' if supplements else '') + '</html>').encode()


class GalleryHandler(SimpleHTTPRequestHandler):
    def send_head(self):
        root = Path(self.directory)
        relative = unquote(urlsplit(self.path).path).lstrip("/")
        def listing(name):
            return redact(_json(root / name)) if (root / name).is_file() else []
        if not relative:
            data = page(listing("index.json"), listing("diagnostics.json"))
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return BytesIO(data)
        parts = Path(relative).parts
        allowed = False
        if len(parts) >= 3 and parts[0] == "bundles" and re.fullmatch(_ID, parts[1]):
            suffix = "/".join(parts[2:])
            allowed = suffix in {"manifest.json", "top.png", "bottom.png", "isometric.png", "viewer/index.html", "viewer/viewer.json", "viewer/board.glb", "viewer/viewer.css", "viewer/viewer.js"} or (suffix.startswith("viewer/vendor/") and ( _ASSETS / suffix.removeprefix("viewer/")).is_file())
        diagnostic_page = (len(parts) == 3 and parts[0] == "diagnostics"
                           and re.fullmatch(_ID, parts[1])
                           and parts[2] == "index.html")
        if len(parts) == 3 and parts[0] == "diagnostics" and re.fullmatch(_ID, parts[1]):
            allowed = parts[2] in {"manifest.json", "image.png", "index.html"}
        path = root / relative
        if diagnostic_page:
            path = path.with_name("manifest.json")
        if (not allowed or ".." in parts or not path.resolve().is_relative_to(root.resolve()) or not path.is_file() or any(p.is_symlink() for p in (path, *path.parents))):
            self.send_error(404)
            return None
        if diagnostic_page:
            data = page([], [{"id": parts[1], **redact(_json(path))}], diagnostic_only=True,
                        latest=next(iter(listing("index.json")), None))
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return BytesIO(data)
        if parts[-1] in ("manifest.json", "viewer.json"):
            # Bundles published before path redaction are immutable; redact on the way out.
            data = (json.dumps(redact(_json(path)), indent=2) + "\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return BytesIO(data)
        return super().send_head()

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def serve(root, bind, port):
    address = ipaddress.ip_address(bind)
    if not (address.is_loopback or address in ipaddress.ip_network("100.64.0.0/10")):
        raise ValueError("bind must be loopback or a Tailscale IPv4 address")
    return ThreadingHTTPServer((bind, port), functools.partial(GalleryHandler, directory=str(root.resolve())))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("publish")
    add.add_argument("--renders", type=Path, required=True)
    add.add_argument("--viewer", type=Path)
    add.add_argument("--candidate", type=Path, help="retained run directory the render was taken from")
    add.add_argument("--label", required=True)
    add.add_argument("--stage", required=True)
    supplement = commands.add_parser("supplement", help="publish annotated PNG without changing Latest")
    supplement.add_argument("--image", type=Path, required=True)
    supplement.add_argument("--packet", type=Path, required=True)
    supplement.add_argument("--candidate", type=Path, required=True,
                            help="retained run directory whose captured board the image analyses")
    supplement.add_argument("--board-sha256", required=True, help="expected hash of the retained board")
    supplement.add_argument("--routing", required=True, choices=sorted(_ROUTING))
    supplement.add_argument("--adoption", required=True, choices=sorted(_ADOPTION))
    supplement.add_argument("--label", required=True)
    supplement.add_argument("--stage", required=True)
    server = commands.add_parser("serve")
    server.add_argument("--bind", default="127.0.0.1")
    server.add_argument("--port", type=int, default=18510)
    args = parser.parse_args()
    if args.command == "publish":
        print(publish(args.root, args.renders, label=args.label, stage=args.stage, viewer=args.viewer,
                      candidate=args.candidate))
    elif args.command == "supplement":
        print(publish_supplement(args.root, args.image, args.packet, candidate=args.candidate,
                                 board_sha256=args.board_sha256, label=args.label, stage=args.stage,
                                 routing=args.routing, adoption=args.adoption))
    else:
        with serve(args.root, args.bind, args.port) as httpd:
            httpd.serve_forever()


if __name__ == "__main__":
    main()
