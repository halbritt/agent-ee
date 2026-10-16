#!/usr/bin/python3
"""Pack complete recorded directories; restore without overwriting changed files."""

import argparse
import gzip
import hashlib
import json
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def pack(group, source_ref):
    source = (ROOT / "evidence" / group).resolve()
    if source.parent != ROOT / "evidence" or not source.is_dir():
        raise ValueError("group must name one recorded directory under evidence/")
    results = {}
    for path in source.rglob("result.json"):
        result = json.loads(path.read_text())
        if result.get("schema") != "agent-ee.result/1":
            continue
        if result["state"] == "running":
            raise ValueError(f"cannot archive a running review: {path}")
        for name, expected in result["policy_files"].items():
            blob = subprocess.run(["git", "show", f"{source_ref}:src/agent_ee/{name}"],
                                  cwd=ROOT, capture_output=True, check=True).stdout
            if hashlib.sha256(blob).hexdigest() != expected:
                raise ValueError(f"source ref does not reproduce recorded implementation: {name}")
        results[path.relative_to(source).as_posix()] = {k: result.get(k) for k in
            ("candidate_id", "run_id", "state", "outcome")}
    destination = ROOT / "evidence/archives" / (group + ".tar.gz")
    destination.parent.mkdir(exist_ok=True)
    if destination.exists():
        raise FileExistsError(destination)

    def normalize(info):
        if info.issym() or info.islnk():
            raise ValueError(f"live link in evidence: {info.name}")
        if Path(info.name).name == ".credentials.json":
            raise ValueError("credential contents must never be archived")
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        info.mtime = 0
        return info

    with destination.open("xb") as output, gzip.GzipFile(fileobj=output, mode="wb", mtime=0, filename="") as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as archive:
            archive.add(source, arcname=group, filter=normalize)
    manifest_path = ROOT / "evidence/archives/manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    manifest[group] = {"archive": destination.name, "sha256": sha(destination), "bytes": destination.stat().st_size,
                       "implementation_ref": source_ref, "reviews": results}
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(destination)


def restore(group):
    directory = ROOT / "evidence/archives"
    manifest = json.loads((directory / "manifest.json").read_text())[group]
    path = directory / manifest["archive"]
    if sha(path) != manifest["sha256"]:
        raise ValueError("archive checksum mismatch")
    target = ROOT / "evidence"
    with tarfile.open(path, "r:gz") as archive:
        # Check every retained path before creating any files.
        for info in archive.getmembers():
            rel = Path(info.name)
            if rel.is_absolute() or ".." in rel.parts or rel.parts[0] != group or not (info.isdir() or info.isfile()):
                raise ValueError(f"unsafe archive entry: {info.name}")
            dest = target / rel
            current = target
            for part in rel.parts:
                current = current / part
                if current.is_symlink():
                    raise ValueError(f"restore path traverses a link: {current}")
            if not dest.resolve().is_relative_to(target.resolve()):
                raise ValueError(f"unsafe restore target: {dest}")
            if info.isfile() and dest.exists():
                with archive.extractfile(info) as stream:
                    expected = hashlib.sha256(stream.read()).hexdigest()
                if sha(dest) != expected:
                    raise ValueError(f"refusing to overwrite changed evidence: {dest}")
        for info in archive.getmembers():
            dest = target / info.name
            if info.isdir():
                dest.mkdir(parents=True, exist_ok=True)
            elif not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(info) as source, dest.open("xb") as output:
                    while block := source.read(1024 * 1024):
                        output.write(block)
    print(target / group)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("pack")
    p.add_argument("group")
    p.add_argument("--source-ref", required=True)
    p = sub.add_parser("restore")
    p.add_argument("group")
    args = parser.parse_args()
    pack(args.group, args.source_ref) if args.command == "pack" else restore(args.group)
