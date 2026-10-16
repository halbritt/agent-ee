"""Capture declared inputs; never follow undeclared live filesystem links."""

import fnmatch
import shutil
import subprocess
from pathlib import Path, PurePosixPath

from .storage import digest, identity, read_json, write_json


def safe_relative(value):
    p = PurePosixPath(value)
    if p.is_absolute() or ".." in p.parts or not p.parts:
        raise ValueError(f"unsafe candidate path: {value!r}")
    return p


def source_path(value, base):
    p = Path(value).expanduser()
    return (base / p).resolve() if not p.is_absolute() else p.resolve()


def selected_files(spec, base):
    source = source_path(spec["path"], base)
    at = safe_relative(spec["at"])
    if source.is_file():
        return [(source, str(at))]
    if not source.is_dir():
        raise FileNotFoundError(source)
    selected = []
    include = spec.get("include", ["**"])
    exclude = [".git", ".git/**", "**/.git/**", *spec.get("exclude", [])]
    for p in sorted(source.rglob("*")):
        rel = p.relative_to(source).as_posix()
        if any(fnmatch.fnmatch(rel, pat) for pat in exclude):
            continue
        if not any(fnmatch.fnmatch(rel, pat) for pat in include):
            continue
        if p.is_symlink():
            raise ValueError(f"declare symlink target as a separate input: {p}")
        if p.is_file():
            selected.append((p, str(at / rel)))
    if not selected:
        raise ValueError(f"input selects no files: {source}")
    return selected


def source_manifest(scope, base):
    files, sources, revisions = {}, {}, {}
    for spec in scope["inputs"]:
        source = source_path(spec["path"], base)
        proc = subprocess.run(["git", "-C", str(source if source.is_dir() else source.parent),
                               "rev-parse", "HEAD"], capture_output=True, text=True)
        revisions[spec["at"]] = proc.stdout.strip() if proc.returncode == 0 else None
        for path, target in selected_files(spec, base):
            if target in files:
                raise ValueError(f"overlapping input destinations: {target}")
            files[target] = digest(path)
            sources[target] = str(path)
    return files, sources, revisions


def capture(scope_path, run_dir):
    scope_path, run_dir = Path(scope_path).resolve(), Path(run_dir).resolve()
    scope_bytes = scope_path.read_bytes()
    scope = read_json(scope_path)
    if scope.get("schema") != "agent-ee.scope/1":
        raise ValueError("expected agent-ee.scope/1")
    before, sources, revisions = source_manifest(scope, scope_path.parent)
    inputs = run_dir / "inputs"
    inputs.mkdir()
    for rel, src in sources.items():
        dst = inputs / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        if digest(dst) != before[rel]:
            raise ValueError(f"input changed during capture: {src}")
    after, _, _ = source_manifest(scope, scope_path.parent)
    if before != after or scope_path.read_bytes() != scope_bytes:
        raise ValueError("source or scope changed during capture; use a new run")
    manifest = {"schema": "agent-ee.candidate/1", "scope": scope, "files": before,
                "sources": sources, "source_revisions": revisions,
                "scope_path": str(scope_path), "scope_sha256": digest(scope_path)}
    manifest["candidate_id"] = identity({"scope": scope, "files": before})
    write_json(run_dir / "candidate.json", manifest)
    return manifest


def verify_snapshot(run_dir):
    run_dir = Path(run_dir)
    m = read_json(run_dir / "candidate.json")
    root = run_dir / "inputs"
    actual = {}
    for p in root.rglob("*"):
        if p.is_symlink():
            raise ValueError(f"snapshot gained a live link: {p}")
        if p.is_file():
            actual[p.relative_to(root).as_posix()] = digest(p)
    if actual != m["files"] or identity({"scope": m["scope"], "files": actual}) != m["candidate_id"]:
        raise ValueError("candidate snapshot changed; evidence is stale")
    return m


def verify_source(run_dir):
    m = verify_snapshot(run_dir)
    scope_path = Path(m["scope_path"])
    if digest(scope_path) != m["scope_sha256"]:
        raise ValueError("review scope changed; evidence is stale")
    files, _, _ = source_manifest(m["scope"], scope_path.parent)
    if files != m["files"]:
        raise ValueError("declared source inputs changed; evidence applies only to the captured candidate")
    return m
