"""Render source-bound KiCad still images in isolated native processes."""

import concurrent.futures
import contextvars
import datetime
import math
import os
import re
import shutil
import struct
import subprocess
import tempfile
import time
import zlib
from pathlib import Path

from . import native_run
from .candidate import verify_snapshot
from .storage import digest, write_json
from .viewer import _source_board, _stage_board


_VIEWS = {"top": ("top", None), "bottom": ("bottom", None),
          "isometric": ("top", "315,0,45")}
_REQUIRED = ("--width", "--height", "--side", "--background", "--quality",
             "--floor", "--use-board-stackup-colors", "--rotate", "--define-var")


class RenderError(ValueError):
    """A native render failed; the output manifest retains its diagnostics."""


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _probe(command):
    try:
        execution = native_run.run(
            native_run.Intent('probe', command, timeout=20, isolation='host'), work=None
        )
        result = native_run.completed(execution, text=True)
    except subprocess.TimeoutExpired as exc:
        raise RenderError("KiCad render capability probe timed out after 20 seconds") from exc
    if result.returncode:
        raise RenderError("KiCad with native pcb render support is required: "
                          + (result.stderr or result.stdout).strip())
    return result.stdout


def _png_info(path):
    """Check every chunk CRC, complete zlib data and all scanline filters."""
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise RenderError("native renderer did not produce a PNG")
    offset, header, compressed, ended = 8, None, bytearray(), False
    while offset < len(data):
        if offset + 12 > len(data):
            raise RenderError("truncated PNG chunk")
        length, kind = struct.unpack_from(">I4s", data, offset)
        end = offset + 12 + length
        if end > len(data):
            raise RenderError("truncated PNG chunk data")
        payload = data[offset + 8:offset + 8 + length]
        crc = struct.unpack_from(">I", data, offset + 8 + length)[0]
        if zlib.crc32(kind + payload) & 0xFFFFFFFF != crc:
            raise RenderError("PNG chunk CRC mismatch")
        if header is None:
            if kind != b"IHDR" or length != 13:
                raise RenderError("PNG must begin with a complete IHDR")
            header = struct.unpack(">IIBBBBB", payload)
        elif kind == b"IHDR":
            raise RenderError("duplicate PNG IHDR")
        if kind == b"IDAT":
            compressed.extend(payload)
        if kind == b"IEND":
            if length or end != len(data):
                raise RenderError("invalid PNG end marker or trailing data")
            ended = True
            break
        offset = end
    if not header or not ended or not compressed:
        raise RenderError("incomplete PNG: image data or IEND missing")
    width, height, depth, color, compression, filtering, interlace = header
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
    depths = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
    if (not width or not height or color not in channels or depth not in depths[color]
            or compression or filtering or interlace not in (0, 1)):
        raise RenderError("unsupported or invalid PNG image header")
    passes = [(0, 0, 1, 1)] if not interlace else [
        (0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4),
        (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2)]
    scanlines = []
    for x, y, dx, dy in passes:
        cols, rows = max(0, (width - x + dx - 1) // dx), max(0, (height - y + dy - 1) // dy)
        if cols and rows:
            scanlines.append((rows, 1 + (cols * channels[color] * depth + 7) // 8))
    expected = sum(rows * size for rows, size in scanlines)
    try:
        inflater = zlib.decompressobj()
        decoded = inflater.decompress(compressed, expected + 1)
    except zlib.error as exc:
        raise RenderError("invalid compressed PNG image data") from exc
    if len(decoded) != expected or not inflater.eof or inflater.unused_data:
        raise RenderError("PNG image data is incomplete or has an invalid decoded length")
    offset = 0
    for rows, size in scanlines:
        if any(decoded[offset + row * size] > 4 for row in range(rows)):
            raise RenderError("invalid PNG scanline filter")
        offset += rows * size
    return {"width": width, "height": height, "sha256": digest(path)}





def _unchanged(source, board, board_hash, project, project_hash):
    if digest(board) != board_hash:
        raise RenderError("saved board changed during rendering")
    actual_project_hash = digest(project) if project.is_file() else None
    if actual_project_hash != project_hash:
        raise RenderError("KiCad project changed during rendering")
    if source.is_dir():
        verify_snapshot(source)


def render_board(source: Path, out: Path, *, width=4096, height=3072, quality="high",
                 jobs=3, timeout=1200, models=None, fetch_models=False, progress=None,
                 views=("top", "bottom", "isometric")) -> Path:
    """Render selected PNG views (all three by default); retain partial failures.

    Returns the completed manifest. Native failures raise RenderError; Ctrl-C
    terminates and waits for every child, saves an interrupted manifest, and
    re-raises KeyboardInterrupt. Stock model downloads require fetch_models.
    """
    source = Path(source).expanduser().resolve()
    out = Path(out).expanduser().absolute()
    if out.exists() or out.is_symlink():
        raise FileExistsError(f"render output must be a new directory: {out}")
    if (type(width) is not int or type(height) is not int or width <= 0 or height <= 0
            or type(jobs) is not int or not 1 <= jobs <= 3
            or type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0):
        raise ValueError("render width/height and timeout must be positive; jobs must be 1, 2 or 3")
    if quality not in ("basic", "high"):
        raise ValueError("render quality must be basic or high")
    if (not isinstance(views, (tuple, list)) or not views or
            any(not isinstance(view, str) or view not in _VIEWS for view in views) or len(set(views)) != len(views)):
        raise ValueError("render views must be distinct top, bottom or isometric names")
    views = tuple(views)
    board, candidate_id, relative = _source_board(source)
    if source.is_dir() and out.resolve().is_relative_to(source):
        raise ValueError("render output must be outside the retained run directory")
    model_dir = Path(models).expanduser().resolve() if models is not None else None
    if fetch_models and model_dir is None:
        raise ValueError("fetch_models requires an explicit models directory")
    if model_dir is not None and not fetch_models and not model_dir.is_dir():
        raise ValueError(f"models directory does not exist: {model_dir}")
    if model_dir is not None and model_dir.exists() and not model_dir.is_dir():
        raise ValueError(f"models path is not a directory: {model_dir}")
    if fetch_models and source.is_dir() and model_dir.is_relative_to(source):
        raise ValueError("model downloads must be outside the retained run directory")
    board_hash = digest(board)
    project = board.with_suffix(".kicad_pro")
    project_hash = digest(project) if project.is_file() else None
    records = {view: {"view": view, "status": "pending", "path": f"{board.stem}-3d-{view}.png",
                      "stdout": f"{view}.stdout.log", "stderr": f"{view}.stderr.log"} for view in views}
    manifest = {"schema": "agent-ee.render3d/1", "status": "running", "started_at": _now(),
                "board": {"name": board.name, "path": str(board), "sha256": board_hash},
                "project": {"path": str(project), "sha256": project_hash} if project_hash else None,
                "candidate_id": candidate_id, "captured_board": relative,
                "settings": {"width": width, "height": height, "quality": quality, "jobs": jobs,
                             "timeout_seconds": timeout, "projection": "orthographic", "background": "opaque",
                             "floor": True, "use_board_stackup_colors": True},
                "views": records, "images": [], "warnings": [], "errors": []}
    out.mkdir(parents=True)
    manifest_path = out / "render-manifest.json"
    write_json(manifest_path, manifest)
    started = time.monotonic()
    cancel = native_run.Cancellation()
    stop = cancel.cancelled
    interrupted = False
    executor = None
    futures = {}

    def cancel_processes():
        cancel.cancel()

    def render_view(view, snapshot, cli, environment, work_root):
        record = records[view]
        began = time.monotonic()
        record["started_at"] = _now()
        try:
            working = work_root / view
            working.mkdir()
            staged = working / snapshot.name
            shutil.copyfile(snapshot, staged)
            snapshot_project = snapshot.with_suffix(".kicad_pro")
            if snapshot_project.is_file():
                shutil.copyfile(snapshot_project, staged.with_suffix(".kicad_pro"))
            config = working / "config"
            config.mkdir()
            env = dict(environment, KICAD_CONFIG_HOME=str(config))
            side, rotation = _VIEWS[view]
            command = [cli, "pcb", "render", "--width", str(width), "--height", str(height),
                       "--quality", quality, "--side", side, "--background", "opaque", "--floor",
                       "--use-board-stackup-colors", "--define-var", f"KIPRJMOD={board.parent}"]
            if rotation:
                command += ["--rotate", rotation]
            command += ["--output", str(out / record["path"]), str(staged)]
            record.update(command=command, config_directory=str(config), status="running")
            write_json(out / f"{view}.json", record)
            with (out / record["stdout"]).open("wb") as stdout, (out / record["stderr"]).open("wb") as stderr:
                if stop.is_set():
                    record["status"] = "interrupted"
                    return

                def launched(pid):
                    record["pid"] = pid

                execution = native_run.run(
                    native_run.Intent(
                        'render3d', command, timeout=timeout, isolation='host', cwd=working, env=env
                    ),
                    work=None,
                    cancel=cancel,
                    on_launch=launched,
                    sink=stdout,
                    stderr_sink=stderr,
                )
                if execution['execution'] == 'unavailable':
                    raise FileNotFoundError(execution.stdout.decode())
                if execution.returncode is not None:
                    record["returncode"] = execution.returncode
                if execution['execution'] == 'timeout':
                    record.update(status="timeout", error=f"native render timed out after {timeout}s")
                    return
            if stop.is_set():
                record.update(status="interrupted", error="render interrupted")
            elif record["returncode"]:
                record.update(status="failed", error=f"native render exited {record['returncode']}")
            else:
                record.update(_png_info(out / record["path"]))
                record["status"] = "completed"
        except (OSError, ValueError) as exc:
            record.update(status="failed", error=str(exc))
        finally:
            record.update(duration_seconds=round(time.monotonic() - began, 3), finished_at=_now())
            write_json(out / f"{view}.json", record)

    try:
        cli = shutil.which("kicad-cli")
        if cli is None:
            raise RenderError("kicad-cli is missing; install KiCad with native pcb render support")
        help_text = _probe([cli, "pcb", "render", "--help"])
        missing = [flag for flag in _REQUIRED if flag not in help_text]
        if missing:
            raise RenderError("KiCad native render lacks required options: " + ", ".join(missing))
        manifest["kicad"] = {"executable": cli, "version": _probe([cli, "version"]).strip()}
        initial = out / "snapshot"
        initial.mkdir()
        snapshot, rewrites = _stage_board(board, initial)
        _unchanged(source, board, board_hash, project, project_hash)
        staged_project = snapshot.with_suffix(".kicad_pro")
        if project_hash and digest(staged_project) != project_hash:
            raise RenderError("KiCad project changed while capturing the render snapshot")
        manifest["staged_board_sha256"] = digest(snapshot)
        manifest["relative_model_paths_resolved"] = rewrites
        manifest["snapshot"] = {"board": {"path": f"snapshot/{snapshot.name}", "sha256": digest(snapshot)},
                                "project": {"path": f"snapshot/{staged_project.name}",
                                            "sha256": digest(staged_project)} if project_hash else None}
        with tempfile.TemporaryDirectory(prefix="agent-ee-render3d-") as temporary:
            work_root = Path(temporary)
            env = dict(os.environ, KIPRJMOD=str(board.parent))
            if model_dir is not None:
                variables = {f"KICAD{version}_3DMODEL_DIR" for version in range(6, 11)} | {"KISYS3DMOD"}
                variables.update(re.findall(r"\$\{(KICAD[0-9]+_3DMODEL_DIR)\}", snapshot.read_text()))
                for variable in variables:
                    env[variable] = str(model_dir)
            from .models3d import fetch_stock_models, inspect_models
            if fetch_models:
                if progress:
                    progress("Fetching explicitly requested stock 3D models")
                manifest["models_fetch"] = "models-fetch.json"
                try:
                    receipt = fetch_stock_models(snapshot, model_dir, env=env)
                except (OSError, ValueError, KeyboardInterrupt) as exc:
                    write_json(out / "models-fetch.json", {
                        "schema": "agent-ee.models-fetch/1", "cache": str(model_dir),
                        "status": "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
                        "error": "interrupted" if isinstance(exc, KeyboardInterrupt) else str(exc),
                        "cache_receipt": str(model_dir / ".agent-ee-models.json")})
                    raise
                write_json(out / "models-fetch.json", receipt)
            coverage = inspect_models(snapshot, env)
            manifest["model_coverage"] = coverage
            model_warnings = list(coverage.get("warnings", []))
            for footprint in coverage.get("footprints", []):
                reference = footprint.get("reference", "unknown footprint")
                if footprint["status"] == "no_model":
                    model_warnings.append(f"{reference}: no 3D model is declared")
                for model in footprint.get("models", []):
                    if model["status"] in ("missing", "unresolved"):
                        model_warnings.append(f"{reference}: {model['status']} 3D model {model['path']}")
            manifest["warnings"].extend(model_warnings)
            manifest["model_environment"] = {key: value for key, value in env.items()
                                             if key.endswith("_3DMODEL_DIR") or key in ("KISYS3DMOD", "KIPRJMOD")}
            write_json(manifest_path, manifest)
            if progress:
                summary = coverage.get("summary", {})
                progress("3D model coverage: " + ", ".join(f"{key}={value}" for key, value in summary.items()))
                for warning in model_warnings[:20]:
                    progress(f"Model warning: {warning}")
                if len(model_warnings) > 20:
                    progress(f"{len(model_warnings) - 20} additional model warnings are retained in render-manifest.json")
                progress(f"Rendering {quality} quality in {jobs} isolated native process(es); this can take several minutes")
            executor = concurrent.futures.ThreadPoolExecutor(max_workers=jobs, thread_name_prefix="render3d")
            try:
                for view in views:
                    if progress:
                        progress(f"Queued {view} render ({width}×{height}, {quality})")
                    future = executor.submit(contextvars.copy_context().run, render_view, view, snapshot, cli, env, work_root)
                    futures[future] = view
                for future in concurrent.futures.as_completed(futures):
                    future.result()
                    if progress:
                        view = futures[future]
                        progress(f"{view}: {records[view]['status']} ({records[view]['duration_seconds']:.1f}s)")
            except BaseException:
                cancel_processes()
                raise
            finally:
                executor.shutdown(wait=True, cancel_futures=True)
                executor = None
    except KeyboardInterrupt:
        interrupted = True
        cancel_processes()
        manifest["errors"].append("render interrupted")
    except (OSError, ValueError) as exc:
        cancel_processes()
        manifest["errors"].append(str(exc))
    finally:
        if executor is not None:
            cancel_processes()
            executor.shutdown(wait=True, cancel_futures=True)
        for record in records.values():
            if record["status"] in ("pending", "running"):
                record["status"] = "interrupted" if interrupted else "not_run"
            write_json(out / f"{record['view']}.json", record)
        try:
            _unchanged(source, board, board_hash, project, project_hash)
            manifest["source_unchanged"] = True
        except (OSError, ValueError) as exc:
            manifest["source_unchanged"] = False
            manifest["errors"].append(str(exc))
        for view, record in records.items():
            for log in (record["stdout"], record["stderr"]):
                log_path = out / log
                if log_path.is_file():
                    for line in log_path.read_text(errors="replace").splitlines():
                        if re.search(r"warn|error|could not|cannot|unable|not found|missing|failed", line, re.I):
                            manifest["warnings"].append(f"{view}: {line.strip()}")
            if record["status"] == "completed":
                manifest["images"].append({key: record[key] for key in ("view", "path", "width", "height", "sha256")})
                if (record["width"], record["height"]) != (width, height):
                    manifest["warnings"].append(f"{view}: KiCad produced {record['width']}×{record['height']} for requested {width}×{height}")
            elif record.get("error"):
                manifest["errors"].append(f"{view}: {record['error']}")
        manifest["status"] = ("interrupted" if interrupted else "timeout" if any(
            r["status"] == "timeout" for r in records.values()) else "failed")
        if len(manifest["images"]) == len(views) and not manifest["errors"] and manifest["source_unchanged"]:
            manifest["status"] = "completed"
        manifest.update(finished_at=_now(), duration_seconds=round(time.monotonic() - started, 3))
        write_json(manifest_path, manifest)
    if interrupted:
        raise KeyboardInterrupt
    if manifest["status"] != "completed":
        raise RenderError(f"3D rendering {manifest['status']}; diagnostics retained in {manifest_path}: "
                          + "; ".join(manifest["errors"]))
    return manifest_path
