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
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import unquote, urlsplit

from .candidate import verify_snapshot
from .render3d import _VIEWS as _VIEW_COMMANDS, RenderError, _png_info
from .storage import digest
from .symbol_library import LibraryError, children, parse_sexpr
from .viewer import _ASSETS, _source_board, _stage_board, _validate_glb

_VIEWS = ("top", "bottom", "isometric")
# Publication surfaces (stored manifests and listings, both JSON endpoints and the
# HTML pages) carry only the allowlisted fields that _bundle, _viewer and _diagnostic
# rebuild, at publication and again when served. Free text is never scrubbed:
# - a path-bearing field is the final component of an unambiguous local path
#   (_basename), a source file name must already be a plain file name (_name),
#   and anything else is the fixed token <redacted>;
# - a warning is rebuilt from a recognised code and allowlisted parameters, else it
#   is <redacted> as a whole (a native log line keeps only its view);
# - a stage is a code from _STAGES and a label is a kind from _LABELS with a number,
#   rebuilt from those parts, else the whole field is <redacted>;
# - hashes, identifiers, times, counts and statuses must match their grammar;
# - every other field is dropped.
_MARK = "<redacted>"
_BUNDLE_SCHEMA = "agent-ee.gallery-bundle/3"
_VIEWER_PUBLICATION = "agent-ee.gallery-viewer/2"
_DIAGNOSTIC_SCHEMA = "agent-ee.gallery-diagnostic/4"
_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.+-]{0,127}")
_VARIABLE = re.compile(r"\$\{[A-Za-z0-9_]+\}(?=[\\/])")
_DRIVE = re.compile(r"[A-Za-z]:[\\/]")
_HASH = re.compile(r"[a-f0-9]{64}")
_TIME = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|[+-]\d\d:\d\d)?")
_REFERENCE = r"([A-Za-z0-9_.+*#-]{1,32})"
_NOTICES = frozenset((
    "File availability is not proof that KiCad loaded a model; inspect native render logs.",
    "The board hash binds the saved PCB; component models may come from current local KiCad libraries "
    "and are not bound by that hash.",
    "Geometry is for visual inspection, not electrical or fabrication acceptance."))
_LIMITATION = ("Publisher verifies the retained candidate and input bytes, not the analysis "
               "or the adoption of any proposed change.")
_SUMMARY = ("footprints", "models", "footprints_fully_resolved", "resolved", "missing", "unresolved",
            "disabled", "no_model")
_COUNTS = ("resolved", "missing", "unresolved", "disabled", "no_model")
_ID = r"[a-f0-9]{12}-[a-f0-9]{12}"
# Publication stages and the text shown for each, and the label kinds. A label is
# "KIND NUMBER"; no publisher-supplied free text is ever published.
_STAGES = {"placement": "Placement", "routing": "Routing in progress", "routed": "Routed",
           "review": "Design review", "diagnostic": "Diagnostic", "fixture": "Test fixture"}
_LABELS = ("Iteration", "Revision", "Candidate", "Milestone", "Diagnostic", "Fixture")
_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
# Supplement status vocabulary. Anything else is refused at publication and shown
# as unknown when served. "unrouted" is checked against the retained board.
_ROUTING = {"unrouted": "Unrouted (verified: the retained board has no tracks or vias)",
            "partially-routed": "Partially routed (publisher statement; tracks or vias present)",
            "routed": "Routed (publisher statement; completeness not verified)"}
_ADOPTION = {"not-adopted": "Proposed change NOT adopted (publisher statement)",
             "adopted": "Proposed change adopted (publisher statement, not verified)",
             "no-proposal": "No change proposed (publisher statement)"}


def _basename(value):
    """The final component of an unambiguous local path, else <redacted> (None stays None).

    Local is a POSIX path (absolute or relative, without a backslash), a drive path
    with either slash, or either of those after a KiCad ${VARIABLE}. A URI or other
    "name:", a UNC or "//" host, a doubled separator, a backslash outside a drive
    path, a quote, < > | * ? or a control character anywhere, a trailing separator,
    or a final component that is not a plain file name, is <redacted>. So is a value an
    earlier redactor already scrubbed ("<redacted>/name"): what followed its marker is
    not known to have been a local file name.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        return _MARK
    if any(c in "\"'<>|*?" or not c.isprintable() for c in value) or value.endswith(("/", "\\")):
        return _MARK  # a trailing separator names a directory, not a file
    variable = _VARIABLE.match(value)
    path = value[variable.end():] if variable else value
    if _DRIVE.match(path):
        if re.search(r"[\\/]{2}", path):
            return _MARK
        name = PureWindowsPath(path).name
    elif ":" in path or "\\" in path or "//" in path:
        return _MARK
    else:
        name = PurePosixPath(path).name
    return name if _NAME.fullmatch(name) else _MARK


def _name(value):
    """A source file name when it is already a plain file name, else <redacted>.

    A file name is not parsed as a path: one that looks like a Windows or UNC path
    (a POSIX name may hold backslashes and colons) is <redacted> whole.
    """
    return value if isinstance(value, str) and _NAME.fullmatch(value) else _MARK


def _label(value):
    """A label rebuilt as "KIND NUMBER" (KIND from _LABELS, NUMBER 0 to 999999), else <redacted>."""
    if not isinstance(value, str):
        return _MARK
    kind, _, number = value.partition(" ")
    if kind in _LABELS and number.isascii() and number.isdecimal() and len(number) <= 6:
        return f"{kind} {int(number)}"
    return _MARK


def _stage(value):
    """A stage code from _STAGES, else <redacted>."""
    return value if isinstance(value, str) and value in _STAGES else _MARK


def _status(value, vocabulary):
    return value if isinstance(value, str) and value in vocabulary else None


def _warning(value):
    """Rebuild a render or viewer warning from its code and allowlisted parameters."""
    if not isinstance(value, str):
        return _MARK
    if value in _NOTICES:
        return value
    match = re.fullmatch(_REFERENCE + r": no 3D model is declared", value)
    if match:
        return f"{match[1]}: no 3D model is declared"
    match = re.fullmatch(_REFERENCE + r": (missing|unresolved) 3D model (.*)", value, re.S)
    if match:
        return f"{match[1]}: {match[2]} 3D model {_basename(match[3])}"
    match = re.fullmatch(r"(top|bottom|isometric): KiCad produced (\d{1,6})×(\d{1,6}) "
                         r"for requested (\d{1,6})×(\d{1,6})", value)
    if match:
        return f"{match[1]}: KiCad produced {match[2]}×{match[3]} for requested {match[4]}×{match[5]}"
    match = re.match(r"(top|bottom|isometric): ", value)
    if match:
        return f"{match[1]}: {_MARK}"  # a native log line: only its view is kept
    return _MARK


def _match(pattern, value):
    return value if isinstance(value, str) and pattern.fullmatch(value) else None


def _count(value):
    return value if type(value) is int and value >= 0 else None


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _items(value):
    return value if isinstance(value, list) else []


def _identified(item, record):
    """Prefix a listing entry's id (None when it is not a publication id)."""
    if "id" not in item:
        return record
    identifier = item["id"]
    return {"id": identifier if isinstance(identifier, str) and re.fullmatch(_ID, identifier) else None, **record}


def _bundle(item):
    """The allowlisted render bundle record, as stored, listed and served."""
    item = _mapping(item)
    summary = _mapping(_mapping(item.get("model_coverage")).get("summary"))
    return _identified(item, {
        "schema": _BUNDLE_SCHEMA, "label": _label(item.get("label")), "stage": _stage(item.get("stage")),
        "board_sha256": _match(_HASH, item.get("board_sha256")),
        "candidate_id": _match(_HASH, item.get("candidate_id")),
        "published_at": _match(_TIME, item.get("published_at")),
        "rendered_at": _match(_TIME, item.get("rendered_at")),
        "model_coverage": {"summary": {key: _count(summary.get(key)) for key in _SUMMARY}},
        "warnings": [_warning(w) for w in _items(item.get("warnings"))],
        "images": [{"view": i["view"], "path": i["view"] + ".png", "sha256": _match(_HASH, i.get("sha256"))}
                   for i in _items(item.get("images")) if isinstance(i, dict) and i.get("view") in _VIEWS],
        "viewer": item.get("viewer") is True})


def _viewer(metadata):
    """The allowlisted viewer.json: the agent-ee.viewer/1 fields the viewer reads, without export logs."""
    metadata = _mapping(metadata)
    board, coordinates = _mapping(metadata.get("board")), _mapping(metadata.get("coordinates"))
    return {"schema": "agent-ee.viewer/1" if metadata.get("schema") == "agent-ee.viewer/1" else None,
            "publication": _VIEWER_PUBLICATION,
            "board": {"name": _basename(board.get("name")), "path": _basename(board.get("path")),
                      "sha256": _match(_HASH, board.get("sha256"))},
            "candidate_id": _match(_HASH, metadata.get("candidate_id")),
            "captured_board": _basename(metadata.get("captured_board")), "model": "board.glb",
            "warnings": [_warning(w) for w in _items(metadata.get("warnings"))],
            "coordinates": {"up": coordinates.get("up") if coordinates.get("up") in ("Y", "Z") else None,
                            "units": "m" if coordinates.get("units") == "m" else None},
            "model_sha256": _match(_HASH, metadata.get("model_sha256"))}


def _diagnostic(item):
    """The allowlisted diagnostic supplement record; earlier schemas without candidate binding are unbound."""
    item = _mapping(item)
    routing, adoption = _mapping(item.get("routing")), _mapping(item.get("adoption"))
    packet, source, image = (_mapping(item.get(key)) for key in ("source_packet", "source_image", "image"))
    bound = item.get("schema") in ("agent-ee.gallery-diagnostic/2", "agent-ee.gallery-diagnostic/3",
                                   _DIAGNOSTIC_SCHEMA)
    return _identified(item, {
        "schema": _DIAGNOSTIC_SCHEMA if bound else None, "kind": "annotated-2d-diagnostic",
        "label": _label(item.get("label")), "stage": _stage(item.get("stage")),
        "candidate_id": _match(_HASH, item.get("candidate_id")),
        "captured_board": _basename(item.get("captured_board")),
        "board_sha256": _match(_HASH, item.get("board_sha256")),
        "routing": {"status": _status(routing.get("status"), _ROUTING),
                    "copper_items": _count(routing.get("copper_items"))},
        "adoption": {"status": _status(adoption.get("status"), _ADOPTION),
                     "verified": False},
        "published_at": _match(_TIME, item.get("published_at")),
        "source_packet": {"name": _name(packet.get("name")), "sha256": _match(_HASH, packet.get("sha256"))},
        "source_image": {"name": _name(source.get("name")), "sha256": _match(_HASH, source.get("sha256"))},
        "image": {"path": "image.png", "width": _count(image.get("width")), "height": _count(image.get("height")),
                  "sha256": _match(_HASH, image.get("sha256"))},
        "limitation": _LIMITATION})


def _plain(label, stage):
    """Refuse a label or stage that the allowlist would not publish as given."""
    if _label(label) != label or label == _MARK:
        raise ValueError(f"label must be KIND NUMBER with KIND one of {', '.join(_LABELS)} and NUMBER "
                         "0 to 999999 without leading zeros")
    if _stage(stage) != stage or stage == _MARK:
        raise ValueError(f"stage must be one of {sorted(_STAGES)}")


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
    _plain(label, stage)
    _verify_provenance(manifest, renders, candidate)
    with _publication(root, "bundles", ".publish-") as staged:
        record = _bundle({"label": label, "stage": stage, "board_sha256": board_hash,
                          "candidate_id": manifest.get("candidate_id"),
                          "published_at": datetime.now(timezone.utc).isoformat(),
                          "rendered_at": manifest.get("finished_at"),
                          "model_coverage": manifest.get("model_coverage"),
                          "warnings": manifest.get("warnings", []), "images": images, "viewer": bool(viewer)})
        for item in images:
            data = _read(renders, item["path"], item["sha256"])
            if data[:8] != b"\x89PNG\r\n\x1a\n":
                raise ValueError("image is not PNG")
            _put(staged.directory, item["view"] + ".png", data)
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
                _put(directory, "viewer.json", _encode(_viewer(metadata)))
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
    _plain(label, stage)
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
        record = _diagnostic({"schema": _DIAGNOSTIC_SCHEMA, "label": label, "stage": stage,
                              "candidate_id": candidate_id, "captured_board": captured,
                              "board_sha256": board_sha256, "routing": {"status": routing, "copper_items": tracks},
                              "adoption": {"status": adoption},
                              "published_at": datetime.now(timezone.utc).isoformat(),
                              "source_packet": {"name": packet.name, "sha256": hashlib.sha256(packet_data).hexdigest()},
                              "source_image": {"name": image.name, "sha256": info["sha256"]}, "image": info})
        _put(staged.directory, "image.png", image_data)
        manifest_bytes = _encode(record)
        _put(staged.directory, "manifest.json", manifest_bytes)
        identifier = board_sha256[:12] + "-" + hashlib.sha256(manifest_bytes).hexdigest()[:12]
        staged.commit(identifier, "diagnostics.json", {"id": identifier, **record})
    return identifier


def _supplement_status(item, latest):
    """Status lines for a served supplement; anything not bound to a verified candidate is unknown."""
    escape = html.escape
    routing, adoption = item["routing"], item["adoption"]
    if (item["schema"] != _DIAGNOSTIC_SCHEMA or item["candidate_id"] is None
            or routing["status"] not in _ROUTING or adoption["status"] not in _ADOPTION):
        return ('<p class="badge unknown" role="status">Status unknown: not bound to a verified retained '
                'candidate, or routing and adoption status are not recorded</p>')
    if latest is None:
        currency = "No Latest render to compare: treat as historical"
    elif latest["board_sha256"] is not None and latest["board_sha256"] == item["board_sha256"]:
        currency = "Same board as the Latest render"
    else:
        currency = "Historical: not the board in the Latest render"
    return (f'<p class="badge incomplete" role="status">{escape(currency)}</p>'
            f'<p>Retained candidate {escape(item["candidate_id"])}</p>'
            f'<p>Routing: {escape(_ROUTING[routing["status"]])}</p>'
            f'<p>Adoption: {escape(_ADOPTION[adoption["status"]])}</p>')


def diagnostic_cards(supplements, latest=None):
    escape = html.escape
    latest = _bundle(latest) if latest is not None else None
    cards = []
    for item in map(_diagnostic, supplements):
        identifier = item.get("id")
        if identifier is None:
            continue
        base = "/diagnostics/" + identifier + "/"
        cards.append(f'<section id="diagnostic-{identifier}"><h2>{escape(item["label"])}</h2>'
                     f'<p><strong>Annotated 2D diagnostic supplement</strong></p><p>Stage: {escape(_STAGES.get(item["stage"], _MARK))}</p>'
                     f'{_supplement_status(item, latest)}'
                     f'<p class="hash">Board SHA-256: {escape(str(item["board_sha256"] or "unknown"))}</p>'
                     f'<p>Published {escape(str(item["published_at"] or "unknown"))}</p>'
                     f'<a href="{base}image.png"><img src="{base}image.png" alt="Annotated 2D diagnostic; open full-size image"></a>'
                     f'<p>{escape(item["limitation"])}</p><a href="{base}index.html">Open diagnostic</a> · <a href="{base}manifest.json">Diagnostic provenance</a></section>')
    return ''.join(cards)


def page(index, supplements=(), *, diagnostic_only=False, latest=None):
    escape = html.escape
    index = [item for item in map(_bundle, index) if item.get("id") is not None]
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
        warnings = "".join('<li>' + escape(w) + '</li>' for w in item["warnings"])
        cards.append(f'<section><h2>{"Latest: " if position == 0 else ""}{escape(item["label"])}</h2><p>Stage: {escape(_STAGES.get(item["stage"], _MARK))} · Rendered {escape(item["rendered_at"] or "unknown")}</p><p class="hash">Board SHA-256: {escape(item["board_sha256"] or "unknown")}</p>{badge}<p class="coverage">{escape(coverage_text)}</p>{viewer}<div class="images">{pictures}</div><details><summary>Model warnings ({len(item["warnings"])})</summary><ul>{warnings}</ul></details><a href="{base}manifest.json">Publication manifest</a></section>')
    return ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>PCB render gallery</title><style>body{font:16px system-ui;background:#111923;color:#e7eef7;margin:auto;padding:24px;max-width:1400px}a{color:#8fc9ff}li{overflow-wrap:anywhere}h1{margin-bottom:8px}section{background:#1b2735;padding:24px;margin:24px 0;border-radius:12px}.images{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;margin:20px 0}figure{margin:0}img{width:100%;border-radius:8px}figcaption{padding:8px}.hash,pre{overflow-wrap:anywhere;white-space:pre-wrap;font-size:13px}.badge{display:inline-block;padding:6px 10px;border-radius:6px;font-weight:600}.incomplete{background:#7a4b00;color:#fff}.unknown{background:#8a1c1c;color:#fff}.complete{background:#1e5a3a;color:#fff}.button{display:inline-block;padding:12px;background:#294968;border-radius:6px}@media(max-width:700px){body{padding:12px}section{padding:16px}.images{grid-template-columns:1fr}}</style>' + ('<h1>PCB diagnostic supplement</h1><p><a href="/">Back to render gallery</a></p>' if diagnostic_only else '<h1>PCB render gallery</h1><p>Native KiCad images · Latest candidate and retained history</p>') + '<p>Visual inspection only. Missing component models can make geometry incomplete; these images do not approve electrical or fabrication readiness.</p>' + ('<p><a href="#diagnostics">Diagnostic supplements</a></p>' if supplements else '') + (''.join(cards) or ('' if diagnostic_only else '<p>No completed render bundles have been published yet.</p>')) + ('<div id="diagnostics"><h2>Diagnostic supplements</h2>' + diagnostic_cards(supplements, latest) + '</div>' if supplements else '') + '</html>').encode()


class GalleryHandler(SimpleHTTPRequestHandler):
    def send_head(self):
        root = Path(self.directory)
        relative = unquote(urlsplit(self.path).path).lstrip("/")
        def listing(name):
            entries = _json(root / name) if (root / name).is_file() else []
            return entries if isinstance(entries, list) else []
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
            data = page([], [{**_mapping(_json(path)), "id": parts[1]}], diagnostic_only=True,
                        latest=next(iter(listing("index.json")), None))
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return BytesIO(data)
        if parts[-1] in ("manifest.json", "viewer.json"):
            # Publications are immutable and may predate the allowlist; project on the way out.
            project = _viewer if parts[-1] == "viewer.json" else _diagnostic if parts[0] == "diagnostics" else _bundle
            data = _encode(project(_json(path)))
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
    labels = f"KIND NUMBER, KIND one of {', '.join(_LABELS)}"
    add.add_argument("--label", required=True, help=labels)
    add.add_argument("--stage", required=True, choices=sorted(_STAGES))
    supplement = commands.add_parser("supplement", help="publish annotated PNG without changing Latest")
    supplement.add_argument("--image", type=Path, required=True)
    supplement.add_argument("--packet", type=Path, required=True)
    supplement.add_argument("--candidate", type=Path, required=True,
                            help="retained run directory whose captured board the image analyses")
    supplement.add_argument("--board-sha256", required=True, help="expected hash of the retained board")
    supplement.add_argument("--routing", required=True, choices=sorted(_ROUTING))
    supplement.add_argument("--adoption", required=True, choices=sorted(_ADOPTION))
    supplement.add_argument("--label", required=True, help=labels)
    supplement.add_argument("--stage", required=True, choices=sorted(_STAGES))
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
