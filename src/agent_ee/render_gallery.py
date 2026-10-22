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
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .storage import digest
from .viewer import _ASSETS, _validate_glb


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


def publish(root, renders, *, label, stage, viewer=None):
    """Publish only completed manifest-bound native images and optional viewer."""
    manifest = _json(renders / "render-manifest.json")
    if manifest.get("schema") != "agent-ee.render3d/1" or manifest.get("status") != "completed":
        raise ValueError("a completed native render3d bundle is required")
    board_hash = manifest["board"]["sha256"]
    if not re.fullmatch(r"[a-f0-9]{64}", board_hash):
        raise ValueError("invalid board hash")
    images = manifest["images"]
    if {i["view"] for i in images} != {"top", "bottom", "isometric"} or len(images) != 3:
        raise ValueError("top, bottom and isometric images are required")
    root.mkdir(parents=True, exist_ok=True)
    (root / "bundles").mkdir(exist_ok=True)
    with (root / ".publish.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with tempfile.TemporaryDirectory(prefix=".publish-", dir=root) as temporary:
            pending = Path(temporary)
            record = {"label": label, "stage": stage, "board_sha256": board_hash,
                      "published_at": datetime.now(timezone.utc).isoformat(),
                      "rendered_at": manifest.get("finished_at"),
                      "model_coverage": manifest.get("model_coverage"),
                      "warnings": manifest.get("warnings", []), "images": [], "viewer": bool(viewer)}
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
                _copy(viewer, "board.glb", pending / "viewer/board.glb", metadata["model_sha256"])
                _validate_glb(pending / "viewer/board.glb")
                shutil.copytree(_ASSETS, pending / "viewer", dirs_exist_ok=True)
                _write(pending / "viewer/viewer.json", metadata)
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


def page(index):
    escape = html.escape
    cards = []
    for position, item in enumerate(index):
        base = "/bundles/" + item["id"] + "/"
        pictures = "".join(f'<a href="{base}{i["path"]}"><figure><img src="{base}{i["path"]}" alt="{i["view"]} native 3D view"><figcaption>{i["view"].title()}</figcaption></figure></a>' for i in item["images"])
        viewer = f'<a class="button" href="{base}viewer/index.html">Open interactive 3D viewer</a>' if item["viewer"] else '<p>Interactive viewer not published for this candidate.</p>'
        coverage = item["model_coverage"] or {}
        summary = coverage.get("summary", {})
        coverage_text = (f'Model availability checks: {summary.get("resolved", "unknown")} resolved; {summary.get("missing", "unknown")} missing; {summary.get("unresolved", "unknown")} unresolved; {summary.get("no_model", "unknown")} footprints without a model.')
        warnings = "".join('<li>' + escape(str(w)) + '</li>' for w in item["warnings"])
        cards.append(f'<section><h2>{"Latest: " if position == 0 else ""}{escape(item["label"])}</h2><p>{escape(item["stage"])} · Rendered {escape(str(item["rendered_at"]))}</p><p class="hash">Board SHA-256: {item["board_sha256"]}</p>{viewer}<div class="images">{pictures}</div><details><summary>Model coverage and limitations</summary><p>{escape(coverage_text)}</p><ul>{warnings}</ul></details><a href="{base}manifest.json">Publication manifest</a></section>')
    return ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>PCB render gallery</title><style>body{font:16px system-ui;background:#111923;color:#e7eef7;margin:auto;padding:24px;max-width:1400px}a{color:#8fc9ff}li{overflow-wrap:anywhere}h1{margin-bottom:8px}section{background:#1b2735;padding:24px;margin:24px 0;border-radius:12px}.images{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;margin:20px 0}figure{margin:0}img{width:100%;border-radius:8px}figcaption{padding:8px}.hash,pre{overflow-wrap:anywhere;white-space:pre-wrap;font-size:13px}.button{display:inline-block;padding:12px;background:#294968;border-radius:6px}@media(max-width:700px){body{padding:12px}section{padding:16px}.images{grid-template-columns:1fr}}</style><h1>PCB render gallery</h1><p>Native KiCad images · Latest candidate and retained history</p><p>Visual inspection only. Missing component models can make geometry incomplete; these images do not approve electrical or fabrication readiness.</p>' + (''.join(cards) or '<p>No completed render bundles have been published yet.</p>') + '</html>').encode()


class GalleryHandler(SimpleHTTPRequestHandler):
    def send_head(self):
        root = Path(self.directory)
        relative = unquote(urlsplit(self.path).path).lstrip("/")
        if not relative:
            data = page(_json(root / "index.json") if (root / "index.json").exists() else [])
            from io import BytesIO
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
        path = root / relative
        if (not allowed or ".." in parts or not path.resolve().is_relative_to(root.resolve()) or not path.is_file() or any(p.is_symlink() for p in (path, *path.parents))):
            self.send_error(404)
            return None
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
    add.add_argument("--label", required=True)
    add.add_argument("--stage", required=True)
    server = commands.add_parser("serve")
    server.add_argument("--bind", default="127.0.0.1")
    server.add_argument("--port", type=int, default=18510)
    args = parser.parse_args()
    if args.command == "publish":
        print(publish(args.root, args.renders, label=args.label, stage=args.stage, viewer=args.viewer))
    else:
        with serve(args.root, args.bind, args.port) as httpd:
            httpd.serve_forever()


if __name__ == "__main__":
    main()
