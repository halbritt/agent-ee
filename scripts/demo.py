#!/usr/bin/python3
"""Run the real RC control, defect and disposable saved-design repair demonstration."""

import argparse
import difflib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from agent_ee.run_record import link_correction, run_review, verify
from agent_ee.storage import digest, read_json, write_json


def demonstrate(out, seconds, profile):
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    plan = ROOT / "docs/evaluation-plan.md"
    write_json(out / "evaluation.json", {"plan": str(plan), "sha256": digest(plan),
        "expectations": {"control": "criteria_satisfied", "seed": "defects_or_unresolved",
                         "repair": "criteria_satisfied"}})
    subprocess.run([str(ROOT / "bin/agent-ee"), "doctor"], stdout=(out / "doctor.json").open("w"), check=True)
    control_scope = ROOT / "fixtures/rc/control.scope.json"
    seed_scope = ROOT / "fixtures/rc/seeded.scope.json"
    run_review(control_scope, out / "control", seconds, profile=profile)
    run_review(seed_scope, out / "seed", seconds, profile=profile)
    control, seed = read_json(out / "control/result.json"), read_json(out / "seed/result.json")
    by_id = {f["id"]: f for f in seed["findings"]}
    seed_findings = []
    for check in seed["checks"]:
        if check["kind"] != "rc_transient" or check["outcome"] != "defect":
            continue
        finding = by_id.get("check/" + check["id"])
        visited = set()
        while finding and finding.get("disposition") == "duplicate" and finding["id"] not in visited:
            visited.add(finding["id"])
            finding = by_id.get(finding.get("duplicate_of"))
        if finding and finding.get("disposition") == "accepted" and finding not in seed_findings:
            seed_findings.append(finding)
    if control["outcome"] != "criteria_satisfied" or not seed_findings:
        write_json(out / "observed.json", {"control_outcome": control["outcome"], "seed_outcome": seed["outcome"],
            "seed_detected": bool(seed_findings), "repair": "not attempted: prerequisites unmet"})
        raise ValueError("control/seed expectations not met; inspect preserved actual reviews")
    native = out / "disposable"
    shutil.copytree(ROOT / "fixtures/rc/seeded", native)
    mutation, patches = [], []
    for suffix in ("kicad_sch", "kicad_pcb"):
        path = native / ("rc_filter." + suffix)
        before = path.read_text()
        old, new = '(property "Value" "100k"', '(property "Value" "10k"'
        if before.count(old) != 1:
            raise ValueError(f"demo repair ambiguous in {path}: expected one seeded value field")
        sha_before = digest(path)
        after = before.replace(old, new, 1)
        path.write_text(after)
        patches += list(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
            fromfile="seed/" + path.name, tofile="disposable/" + path.name))
        mutation.append({"path": str(path), "before_sha256": sha_before, "after_sha256": digest(path)})
    (out / "repair.patch").write_text("".join(patches))
    write_json(out / "repair.json", {"authorization": "Owner task authorizes disposable fixture repairs",
        "finding_id": seed_findings[0]["id"], "saved_native_changes": mutation})
    config = read_json(seed_scope)
    for spec in config["inputs"]:
        path = Path(spec["path"]).expanduser()
        spec["path"] = str(native if spec["at"] == "project" else
                           (path if path.is_absolute() else (seed_scope.parent / path).resolve()))
    repaired_scope = out / "repaired.scope.json"
    write_json(repaired_scope, config)
    run_review(repaired_scope, out / "repair", seconds, profile=profile)
    repaired = read_json(out / "repair/result.json")
    native_checks = [c["id"] for c in repaired["checks"] if c["kind"] in ("erc", "drc", "rc_transient")]
    if repaired["outcome"] == "criteria_satisfied":
        for finding in seed_findings:
            print(json.dumps(link_correction(out / "seed", out / "repair", finding["id"], native_checks,
                "Disposable saved schematic and board restored to 10k; new native/ERC/DRC and response evidence meet original limits."),
                indent=2))
    for name in ("control", "seed", "repair"):
        verify(out / name)
    observed = {"control_outcome": control["outcome"], "control_concerns": control["findings"],
        "seed_outcome": seed["outcome"], "seed_detected": True,
        "seeded_defects": [{"parameter": "saved R1 value 100k instead of 10k", "detected": True,
            "supporting_findings": [f["id"] for f in seed_findings]}],
        "repair_outcome": repaired["outcome"], "repair_closed": repaired["outcome"] == "criteria_satisfied",
        "limitation": "One functional seed and one control are not a hardware correctness benchmark."}
    write_json(out / "observed.json", observed)
    if not observed["repair_closed"]:
        raise ValueError("repair not closed; inspect recorded new review")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", required=True)
    p.add_argument("--seconds-per-run", type=float, default=1200)
    p.add_argument("--profile", required=True)
    args = p.parse_args()
    demonstrate(args.out, args.seconds_per_run, args.profile)


if __name__ == "__main__":
    main()
