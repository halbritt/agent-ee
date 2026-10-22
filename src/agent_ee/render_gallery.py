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
from .render3d import RenderError, _png_info
from .storage import digest
from .viewer import _ASSETS, _validate_glb

_VIEWS = ("top", "bottom", "isometric")
_ABSOLUTE = re.compile(r"(?<![\w.}\])/:])(?:/[^\s'\"<>]+|[A-Za-z]:\\[^\s'\"<>]+)")


def redact(value):
    """Replace absolute host paths in any JSON value with <redacted>/basename."""
    if isinstance(value, str):
        return _ABSOLUTE.sub(lambda m: "<redacted>/" + re.split(r"[\\/]", m.group().rstrip("/\\"))[-1], value)
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    return value


def _json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _copy(source, relative, target, expected):
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("unsafe artifact path")
    file = source / relative
    if any(p.is_symlink() for p in (file, *file.parents)) or not file.is_file():
        raise ValueError("artifact must be a regular file without symlinks")
    if digest(file) != expected:
        raise ValueError("artifact hash mismatch")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(file, target)
    if digest(target) != expected:
        raise ValueError("artifact changed while copying")


def _verify_provenance(manifest, renders, candidate):
    """Cross-check the render manifest against its own records, files and retained candidate."""
    if manifest.get("errors") or manifest.get("source_unchanged") is not True:
        raise ValueError("render manifest records errors or a changed source")
    candidate_id, captured = manifest.get("candidate_id"), manifest.get("captured_board")
    board = manifest["board"]
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
    snapshot = (manifest.get("snapshot") or {}).get("board") or {}
    staged = renders / str(snapshot.get("path", ""))
    if (not snapshot or manifest.get("staged_board_sha256") != snapshot.get("sha256")
            or not staged.is_file() or digest(staged) != snapshot["sha256"]):
        raise ValueError("render snapshot board is missing or does not match the manifest")


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
        coverage = item["model_coverage"] or {}
        summary = coverage.get("summary") if isinstance(coverage.get("summary"), dict) else {}
        counts = [summary.get(k) for k in ("resolved", "missing", "unresolved", "disabled", "no_model")]
        known = all(isinstance(c, int) and not isinstance(c, bool) for c in counts)
        if not known:
            badge = '<p class="badge unknown" role="status">Model coverage unknown: do not treat this geometry as complete</p>'
        elif any(counts[1:]):
            badge = '<p class="badge incomplete" role="status">Incomplete models: geometry is missing components</p>'
        else:
            badge = '<p class="badge complete">All declared 3D models resolved</p>'
        coverage_text = (f'Models: {summary.get("resolved", "unknown")} resolved; {summary.get("missing", "unknown")} missing; {summary.get("unresolved", "unknown")} unresolved; {summary.get("disabled", "unknown")} disabled; {summary.get("no_model", "unknown")} footprints without a model.')
        warnings = "".join('<li>' + escape(str(w)) + '</li>' for w in item["warnings"])
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
