"""Private, immutable render publication and a read-only gallery server."""
import argparse
import fcntl
import functools
import html
import ipaddress
import json
import os
import re
import shutil
import tempfile
from io import BytesIO
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .candidate import verify_snapshot
from .render3d import _VIEWS as _VIEW_COMMANDS, RenderError, _png_info
from .storage import digest
from .viewer import _ASSETS, _source_board, _stage_board, _validate_glb

_VIEWS = ("top", "bottom", "isometric")
# URLs other than file:// are not host paths; everything else that starts at a
# POSIX root, a drive letter (either slash) or a UNC prefix is. A path runs to the
# end of the text or a quote, so directory names containing spaces are removed too.
# Already redacted text is left alone, so serving a redacted bundle is idempotent.
_URL = re.compile(r"(?<![\w.+-])(?!file:)[A-Za-z][A-Za-z0-9+.-]*://[^\s'\"<>]*")
_ABSOLUTE = re.compile(r"(?<![\w.}\])/\\])(?<!<redacted>)(?:file://)?(?:/|\\\\|[A-Za-z]:[\\/])(?=[^\s'\"<>])[^'\"<>\r\n]*")
_COUNTS = ("resolved", "missing", "unresolved", "disabled", "no_model")


def _redact_text(text):
    return _ABSOLUTE.sub(lambda m: "<redacted>/" + re.split(r"[\\/]", m.group().rstrip("/\\ \t"))[-1], text)


def redact(value):
    """Replace absolute host paths in any JSON value (keys included) with <redacted>/basename."""
    if isinstance(value, str):
        kept, parts = 0, []
        for url in _URL.finditer(value):
            parts += [_redact_text(value[kept:url.start()]), url.group()]
            kept = url.end()
        return "".join(parts) + _redact_text(value[kept:])
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


def _write(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


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


def _copy(source, relative, target, expected):
    file = _artifact(source, relative)
    if digest(file) != expected:
        raise ValueError("artifact hash mismatch")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(file, target)
    if digest(target) != expected:
        raise ValueError("artifact changed while copying")


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
    root.mkdir(parents=True, exist_ok=True)
    (root / "bundles").mkdir(exist_ok=True)
    with (root / ".publish.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with tempfile.TemporaryDirectory(prefix=".publish-", dir=root) as temporary:
            pending = Path(temporary)
            record = {"label": label, "stage": stage, "board_sha256": board_hash,
                      "candidate_id": manifest.get("candidate_id"),
                      "published_at": datetime.now(timezone.utc).isoformat(),
                      "rendered_at": manifest.get("finished_at"),
                      "model_coverage": redact(manifest.get("model_coverage")),
                      "warnings": redact(manifest.get("warnings", [])), "images": [], "viewer": bool(viewer)}
            for item in images:
                name = item["view"] + ".png"
                _copy(renders, item["path"], pending / name, item["sha256"])
                if (pending / name).read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
                    raise ValueError("image is not PNG")
                record["images"].append({"view": item["view"], "path": name, "sha256": item["sha256"]})
            if viewer:
                metadata = _json(viewer / "viewer.json")
                if metadata.get("schema") != "agent-ee.viewer/1" or metadata["board"]["sha256"] != board_hash:
                    raise ValueError("viewer belongs to a different board")
                if (metadata.get("candidate_id"), metadata.get("captured_board")) != (
                        manifest.get("candidate_id"), manifest.get("captured_board")):
                    raise ValueError("viewer belongs to a different candidate")
                _copy(viewer, "board.glb", pending / "viewer/board.glb", metadata["model_sha256"])
                _validate_glb(pending / "viewer/board.glb")
                shutil.copytree(_ASSETS, pending / "viewer", dirs_exist_ok=True)
                _write(pending / "viewer/viewer.json", redact(metadata))
            _write(pending / "manifest.json", record)
            # Include image identity so a new export of the same board retains history.
            bundle = board_hash[:12] + "-" + digest(pending / "manifest.json")[:12]
            os.rename(pending, root / "bundles" / bundle)
        index_path = root / "index.json"
        index = _json(index_path) if index_path.exists() else []
        index.insert(0, {"id": bundle, **record})
        temporary_index = root / ".index.next"
        _write(temporary_index, index)
        os.replace(temporary_index, index_path)
    return bundle



def publish_supplement(root, image, packet, board, *, board_sha256, label, stage):
    """Publish a verified annotated PNG separately from native render milestones."""
    if not re.fullmatch(r"[a-f0-9]{64}", board_sha256):
        raise ValueError("invalid board hash")
    image_hash, packet_hash = digest(image), digest(packet)
    root.mkdir(parents=True, exist_ok=True)
    (root / "diagnostics").mkdir(exist_ok=True)
    with (root / ".publish.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with tempfile.TemporaryDirectory(prefix=".diagnostic-", dir=root) as temporary:
            pending = Path(temporary)
            _copy(board.parent, board.name, pending / "source-board", board_sha256)
            _copy(packet.parent, packet.name, pending / "source-packet", packet_hash)
            _copy(image.parent, image.name, pending / "image.png", image_hash)
            info = _png_info(pending / "image.png")
            record = {"schema": "agent-ee.gallery-diagnostic/1", "kind": "annotated-2d-diagnostic",
                      "label": redact(label), "stage": redact(stage), "board_sha256": board_sha256,
                      "published_at": datetime.now(timezone.utc).isoformat(),
                      "source_packet": {"name": packet.name, "sha256": packet_hash},
                      "source_image": {"name": image.name, "sha256": image_hash},
                      "image": {"path": "image.png", **info},
                      "limitation": "Publisher verifies input bytes, not the analysis or adoption of any proposed change."}
            # Retain identities, not source files or host paths, in the HTTP publication.
            (pending / "source-board").unlink()
            (pending / "source-packet").unlink()
            _write(pending / "manifest.json", record)
            identifier = board_sha256[:12] + "-" + digest(pending / "manifest.json")[:12]
            os.rename(pending, root / "diagnostics" / identifier)
        index_path = root / "diagnostics.json"
        index = _json(index_path) if index_path.exists() else []
        index.insert(0, {"id": identifier, **record})
        temporary_index = root / ".diagnostics.next"
        _write(temporary_index, index)
        os.replace(temporary_index, index_path)
    return identifier


def diagnostic_cards(supplements):
    cards = []
    for item in supplements:
        base = "/diagnostics/" + item["id"] + "/"
        cards.append(f'<section id="diagnostic-{item["id"]}"><h2>{html.escape(item["label"])}</h2>'
                     f'<p><strong>Annotated 2D diagnostic supplement</strong></p><p>{html.escape(item["stage"])}</p>'
                     f'<p class="hash">Historical board SHA-256: {item["board_sha256"]}</p>'
                     f'<p>Published {html.escape(item["published_at"])}</p>'
                     f'<a href="{base}image.png"><img src="{base}image.png" alt="Annotated 2D diagnostic; open full-size image"></a>'
                     f'<p>{html.escape(item["limitation"])}</p><a href="{base}index.html">Open diagnostic</a> · <a href="{base}manifest.json">Diagnostic provenance</a></section>')
    return ''.join(cards)


def page(index, supplements=(), *, diagnostic_only=False):
    escape = html.escape
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
        cards.append(f'<section><h2>{"Latest: " if position == 0 else ""}{escape(item["label"])}</h2><p>{escape(item["stage"])} · Rendered {escape(str(item["rendered_at"]))}</p><p class="hash">Board SHA-256: {item["board_sha256"]}</p>{badge}<p class="coverage">{escape(coverage_text)}</p>{viewer}<div class="images">{pictures}</div><details><summary>Model warnings ({len(item["warnings"])})</summary><ul>{warnings}</ul></details><a href="{base}manifest.json">Publication manifest</a></section>')
    return ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>PCB render gallery</title><style>body{font:16px system-ui;background:#111923;color:#e7eef7;margin:auto;padding:24px;max-width:1400px}a{color:#8fc9ff}li{overflow-wrap:anywhere}h1{margin-bottom:8px}section{background:#1b2735;padding:24px;margin:24px 0;border-radius:12px}.images{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;margin:20px 0}figure{margin:0}img{width:100%;border-radius:8px}figcaption{padding:8px}.hash,pre{overflow-wrap:anywhere;white-space:pre-wrap;font-size:13px}.badge{display:inline-block;padding:6px 10px;border-radius:6px;font-weight:600}.incomplete{background:#7a4b00;color:#fff}.unknown{background:#8a1c1c;color:#fff}.complete{background:#1e5a3a;color:#fff}.button{display:inline-block;padding:12px;background:#294968;border-radius:6px}@media(max-width:700px){body{padding:12px}section{padding:16px}.images{grid-template-columns:1fr}}</style>' + ('<h1>PCB diagnostic supplement</h1><p><a href="/">Back to render gallery</a></p>' if diagnostic_only else '<h1>PCB render gallery</h1><p>Native KiCad images · Latest candidate and retained history</p>') + '<p>Visual inspection only. Missing component models can make geometry incomplete; these images do not approve electrical or fabrication readiness.</p>' + ('<p><a href="#diagnostics">Diagnostic supplements</a></p>' if supplements else '') + (''.join(cards) or ('' if diagnostic_only else '<p>No completed render bundles have been published yet.</p>')) + ('<div id="diagnostics"><h2>Diagnostic supplements</h2>' + diagnostic_cards(supplements) + '</div>' if supplements else '') + '</html>').encode()


class GalleryHandler(SimpleHTTPRequestHandler):
    def send_head(self):
        root = Path(self.directory)
        relative = unquote(urlsplit(self.path).path).lstrip("/")
        if not relative:
            data = page(_json(root / "index.json") if (root / "index.json").exists() else [],
                        _json(root / "diagnostics.json") if (root / "diagnostics.json").exists() else [])
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return BytesIO(data)
        parts = Path(relative).parts
        allowed = False
        if len(parts) >= 3 and parts[0] == "bundles" and re.fullmatch(r"[a-f0-9]{12}-[a-f0-9]{12}", parts[1]):
            suffix = "/".join(parts[2:])
            allowed = suffix in {"manifest.json", "top.png", "bottom.png", "isometric.png", "viewer/index.html", "viewer/viewer.json", "viewer/board.glb", "viewer/viewer.css", "viewer/viewer.js"} or (suffix.startswith("viewer/vendor/") and ( _ASSETS / suffix.removeprefix("viewer/")).is_file())
        diagnostic_page = (len(parts) == 3 and parts[0] == "diagnostics"
                           and re.fullmatch(r"[a-f0-9]{12}-[a-f0-9]{12}", parts[1])
                           and parts[2] == "index.html")
        if len(parts) == 3 and parts[0] == "diagnostics" and re.fullmatch(r"[a-f0-9]{12}-[a-f0-9]{12}", parts[1]):
            allowed = parts[2] in {"manifest.json", "image.png", "index.html"}
        path = root / relative
        if diagnostic_page:
            path = path.with_name("manifest.json")
        if (not allowed or ".." in parts or not path.resolve().is_relative_to(root.resolve()) or not path.is_file() or any(p.is_symlink() for p in (path, *path.parents))):
            self.send_error(404)
            return None
        if diagnostic_page:
            data = page([], [{"id": parts[1], **redact(_json(path))}], diagnostic_only=True)
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
    supplement.add_argument("--board", type=Path, required=True)
    supplement.add_argument("--board-sha256", required=True)
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
        print(publish_supplement(args.root, args.image, args.packet, args.board,
                                board_sha256=args.board_sha256, label=args.label, stage=args.stage))
    else:
        with serve(args.root, args.bind, args.port) as httpd:
            httpd.serve_forever()


if __name__ == "__main__":
    main()
