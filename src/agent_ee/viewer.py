"""Export a native KiCad board and serve a self-contained local viewer."""

import functools
import json
import re
import shutil
import struct
import subprocess
import tempfile
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PureWindowsPath
from urllib.parse import unquote, urlsplit

from . import native_run
from .candidate import safe_relative, verify_snapshot
from .storage import digest, write_json


_ASSETS = Path(__file__).with_name("viewer_assets")
_FLAGS = ("--include-tracks", "--include-pads", "--include-zones",
          "--include-silkscreen", "--include-soldermask", "--subst-models")


class ViewerError(ValueError):
    """An actionable native export or viewer setup failure."""


def _stage_board(board, stage):
    # KiCad writes project-local .kicad_prl files even during GLB export.
    # Rewrite only model filename tokens so relative assets keep their meaning.
    text = board.read_text()
    tokens = list(re.finditer(r'"(?:\\.|[^"\\])*"|[()]|[^()\s"]+', text))
    replacements = []
    for i in range(len(tokens) - 2):
        opening, name, value = tokens[i:i + 3]
        if opening.group() != "(" or name.group() != "model" or not value.group().startswith('"'):
            continue
        filename = value.group()[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        if (not filename or "$" in filename or Path(filename).is_absolute()
                or PureWindowsPath(filename).is_absolute()
                or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://|^:[^:]+:", filename)):
            continue
        absolute = str((board.parent / filename).resolve())
        quoted = '"' + absolute.replace("\\", "\\\\").replace('"', '\\"') + '"'
        replacements.append((value.start(), value.end(), quoted))
    for start, end, value in reversed(replacements):
        text = text[:start] + value + text[end:]
    staged = stage / board.name
    staged.write_text(text)
    project = board.with_suffix(".kicad_pro")
    if project.is_file():
        shutil.copyfile(project, staged.with_suffix(".kicad_pro"))
    return staged, len(replacements)


def _source_board(source):
    if source.is_file():
        if source.suffix != ".kicad_pcb":
            raise ValueError("viewer source must be a .kicad_pcb file or retained run directory")
        return source, None, None
    if not source.is_dir() or not (source / "candidate.json").is_file():
        raise ValueError("viewer source must be a .kicad_pcb file or retained run directory")
    manifest = verify_snapshot(source)
    relative = str(safe_relative(manifest["scope"]["board"]))
    board = source / "inputs" / relative
    if relative not in manifest["files"] or board.suffix != ".kicad_pcb":
        raise ValueError("candidate scope.board must name a captured .kicad_pcb file")
    return board, manifest["candidate_id"], relative


def _probe(command):
    try:
        execution = native_run.run(
            native_run.Intent('probe', command, timeout=15, isolation='host'), work=None
        )
        result = native_run.completed(execution, text=True)
    except subprocess.TimeoutExpired as exc:
        raise ViewerError("KiCad CLI did not respond within 15 seconds") from exc
    if result.returncode:
        raise ViewerError("KiCad CLI with native GLB export is required (KiCad 10 or newer): "
                           + (result.stderr or result.stdout).strip())
    return result.stdout


def _validate_glb(path):
    if not path.is_file():
        raise ViewerError("KiCad reported success without producing board.glb")
    with path.open("rb") as stream:
        header = stream.read(20)
        if len(header) != 20:
            raise ViewerError("KiCad produced an incomplete GLB")
        magic, version, length, json_size, chunk = struct.unpack("<4sIIII", header)
        if magic != b"glTF" or version != 2 or length != path.stat().st_size or chunk != 0x4E4F534A:
            raise ViewerError("KiCad produced an invalid GLB")
        if json_size > length - 20:
            raise ViewerError("KiCad produced an incomplete GLB JSON chunk")
        try:
            document = json.loads(stream.read(json_size))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ViewerError("KiCad produced invalid GLB JSON") from exc
    if not document.get("meshes"):
        raise ViewerError("KiCad GLB contains no board geometry; check the saved board outline")


def build_viewer(source: Path, out: Path, *, timeout=300) -> Path:
    """Build into a new directory; retain export logs there on failure.

    Retained runs use their verified captured board, never manifest.sources.
    Component models resolve through the local KiCad model configuration.
    """
    source = Path(source).expanduser().resolve()
    out = Path(out).expanduser().absolute()
    if out.exists() or out.is_symlink():
        raise FileExistsError(f"viewer output must be a new directory: {out}")
    if timeout <= 0:
        raise ValueError("viewer export timeout must be positive")
    board, candidate_id, relative = _source_board(source)
    if source.is_dir() and out.resolve().is_relative_to(source):
        raise ValueError("viewer output must be outside the retained run directory")
    board_hash = digest(board)
    cli = shutil.which("kicad-cli")
    if cli is None:
        raise ViewerError("kicad-cli is missing; install KiCad 10 or newer with native GLB export")
    help_text = _probe([cli, "pcb", "export", "glb", "--help"])
    missing = [flag for flag in _FLAGS if flag not in help_text]
    if missing:
        raise ViewerError("KiCad native GLB export lacks required options: " + ", ".join(missing))
    version = _probe([cli, "version"]).strip()
    if not all((_ASSETS / filename).is_file() for filename in ("index.html", "viewer.js", "viewer.css")):
        raise ViewerError("viewer web assets are missing; reinstall agent-ee")
    out.mkdir(parents=True)
    export = {"kicad_version": version, "timeout_seconds": timeout,
              "stdout": "export.stdout.log", "stderr": "export.stderr.log"}
    with tempfile.TemporaryDirectory(prefix="agent-ee-viewer-") as temporary:
        staged, rewrites = _stage_board(board, Path(temporary))
        command = [cli, "pcb", "export", "glb", *_FLAGS,
                   "--define-var", f"KIPRJMOD={board.parent}",
                   "--output", str(out / "board.glb"), str(staged)]
        export.update(command=command, staged_board_sha256=digest(staged),
                      relative_model_paths_resolved=rewrites, project_directory=str(board.parent))
        write_json(out / "export.json", export)
        with (out / "export.stdout.log").open("wb") as stdout, (out / "export.stderr.log").open("wb") as stderr:
            try:
                execution = native_run.run(
                    native_run.Intent('stage', command, timeout=timeout, isolation='host', cwd=staged.parent),
                    work=None,
                    sink=stdout,
                    stderr_sink=stderr,
                )
                result = native_run.completed(execution)
            except subprocess.TimeoutExpired as exc:
                export["status"] = "timeout"
                write_json(out / "export.json", export)
                raise ViewerError(
                    f"KiCad GLB export timed out after {timeout}s; logs retained in {out}"
                ) from exc
    export["returncode"] = result.returncode
    write_json(out / "export.json", export)
    if digest(board) != board_hash:
        raise ViewerError(f"board changed during export; discard this viewer: {out}")
    if candidate_id:
        verify_snapshot(source)
    if result.returncode:
        raise ViewerError(f"KiCad GLB export failed (exit {result.returncode}); logs retained in {out}")
    _validate_glb(out / "board.glb")
    logs = [(out / name).read_text(errors="replace") for name in ("export.stdout.log", "export.stderr.log")]
    warnings = []
    for line in "\n".join(logs).splitlines():
        if re.search(r"warn|error|could not|cannot|unable|not found|missing|failed", line, re.I):
            warnings.append(line.strip())
    warnings.append("The board hash binds the saved PCB; component models may come from current local KiCad libraries and are not bound by that hash.")
    warnings.append("Geometry is for visual inspection, not electrical or fabrication acceptance.")
    metadata = {"schema": "agent-ee.viewer/1", "board": {"name": board.name,
                "path": str(board), "sha256": board_hash}, "candidate_id": candidate_id,
                "captured_board": relative, "model": "board.glb", "warnings": warnings,
                "coordinates": {"up": "Y", "units": "m"},
                "model_sha256": digest(out / "board.glb"), "export": export}
    shutil.copytree(_ASSETS, out, dirs_exist_ok=True)
    write_json(out / "viewer.json", metadata)
    return out / "index.html"


class _ViewerHandler(SimpleHTTPRequestHandler):
    """Serve only the exported bundle's regular files, without directory indexes."""

    def send_head(self):
        root = Path(self.directory).resolve()
        relative = unquote(urlsplit(self.path).path).lstrip("/") or "index.html"
        path = root / relative
        resolved = path.resolve()
        public = {"index.html", "viewer.js", "viewer.css", "viewer.json", "board.glb",
                  "export.json", "export.stdout.log", "export.stderr.log"}
        allowed = relative in public or relative.startswith("vendor/")
        if (not allowed or not resolved.is_relative_to(root) or not resolved.is_file()
                or any(p.is_symlink() for p in (path, *path.parents))):
            self.send_error(404, "File not found")
            return None
        return super().send_head()

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()


def serve_viewer(directory: Path, port=0) -> ThreadingHTTPServer:
    """Return an unstarted loopback HTTP server; caller owns serve_forever/close."""
    directory = Path(directory).expanduser().resolve()
    metadata = json.loads((directory / "viewer.json").read_text())
    if metadata.get("schema") != "agent-ee.viewer/1" or not (directory / "index.html").is_file():
        raise ValueError("not an agent-ee viewer directory")
    handler = functools.partial(_ViewerHandler, directory=str(directory))
    return ThreadingHTTPServer(("127.0.0.1", port), handler)
