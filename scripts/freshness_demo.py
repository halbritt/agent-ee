#!/usr/bin/python3
"""Temporarily mutate only an explicit disposable design; demonstrate stale refusal."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "bin/agent-ee"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def verify(run):
    argv = [str(CLI), "verify", str(run)]
    p = subprocess.run(argv, capture_output=True, text=True)
    return {"argv": argv, "exit_code": p.returncode, "stdout": p.stdout, "stderr": p.stderr}


def demonstrate(run, disposable_root, original_value, changed_value, out):
    run, disposable_root = Path(run).resolve(), Path(disposable_root).resolve()
    result = json.loads((run / "result.json").read_text())
    if result["outcome"] != "criteria_satisfied":
        raise ValueError("freshness challenge requires an actually passing review first")
    baseline = verify(run)
    if baseline["exit_code"] != 0:
        raise ValueError("review must be current before the mutation")
    manifest = json.loads((run / "candidate.json").read_text())
    originals, mutations = {}, []
    for key in ("schematic", "board"):
        path = Path(manifest["sources"][manifest["scope"][key]])
        if not path.resolve().is_relative_to(disposable_root):
            raise ValueError("source is outside the explicitly supplied disposable directory")
        data = path.read_bytes()
        old = f'(property "Value" "{original_value}"'.encode()
        new = f'(property "Value" "{changed_value}"'.encode()
        if data.count(old) != 1:
            raise ValueError(f"mutation must identify exactly one saved value field: {path}")
        originals[path] = data
        mutations.append({"path": str(path), "before_sha256": sha(data),
                          "changed_sha256": sha(data.replace(old, new, 1))})
    try:
        for path, data in originals.items():
            path.write_bytes(data.replace(old, new, 1))
        changed = verify(run)
        if changed["exit_code"] != 2:
            raise ValueError("changed source was not refused as stale")
    finally:
        for path, data in originals.items():
            path.write_bytes(data)
    restored = verify(run)
    if restored["exit_code"] != 0:
        raise ValueError("exact restored source should match the original candidate again")
    observation = {"expected": "a passing old result cannot accept the changed saved design",
        "candidate_id": result["candidate_id"], "old_review_outcome": result["outcome"],
        "mutations": mutations, "before": baseline, "changed": changed, "restored": restored,
        "source_bytes_restored": all(path.read_bytes() == data for path, data in originals.items()),
        "interpretation": "No acceptance was claimed for the changed design; restoring exact bytes restores the original candidate only."}
    Path(out).write_text(json.dumps(observation, indent=2) + "\n")
    print(out)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("run")
    p.add_argument("--disposable-root", required=True)
    p.add_argument("--from-value", required=True)
    p.add_argument("--to-value", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()
    demonstrate(args.run, args.disposable_root, args.from_value, args.to_value, args.out)
