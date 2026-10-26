#!/usr/bin/env python3
"""Judge a checks-only agent-ee run from its result.json, not the review exit code.

`agent-ee review --checks-only` intentionally exits 1 even when every check
passes, because independent reviews were not performed. Automation must read
the retained result instead. This gate prints a Markdown summary (verdict,
check table and the run's REPORT.md) to stdout and returns one verdict:

  checks_passed        every declared check executed and reported pass or info
  check_failed         every declared check executed; at least one reports a defect
  coverage_incomplete  a declared check is absent, unknown or not executed, or the
                       run errored, was interrupted or never finalized
  result_invalid       result.json is missing, unreadable or malformed, its checks or
                       check findings contradict the retained checks/<id>.json records,
                       or `agent-ee verify` refused the run (--verify-exit nonzero)

The verdict is derived from the retained native-check records: result.json's check
projection is used only after it is bound to checks/<id>.json, for every run state.

Without --expect the exit status is 0 only for checks_passed. With --expect it
is 0 only when the verdict and every --expect-check ID=OUTCOME match. The gate
judges native checks only; it is not design acceptance and runs no model review.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from agent_ee.run_record import bind_check_findings, bind_checks  # noqa: E402

VERDICTS = ("checks_passed", "check_failed", "coverage_incomplete", "result_invalid")
CHECK_OUTCOMES = ("pass", "info", "defect", "unknown")
EXPLANATIONS = {
    "checks_passed": "Every declared check executed and passed.",
    "check_failed": "Every declared check executed; at least one declared check reports a defect.",
    "coverage_incomplete": "Declared check coverage is incomplete; an unexecuted check is not a pass.",
    "result_invalid": "The run's result record is missing, malformed or inconsistent; nothing was accepted.",
}


def judge(run, verify_exit=None):
    """Return {"verdict", "reasons", "checks"} for a run directory."""
    if not str(run):
        return invalid("no run directory was produced for this review")
    path = Path(run) / "result.json"
    try:
        result = json.loads(path.read_text())
    except FileNotFoundError:
        return invalid(f"{path} does not exist")
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return invalid(f"cannot parse {path}: {exc}")
    if not isinstance(result, dict) or result.get("schema") != "agent-ee.result/1":
        return invalid("result.json is not an agent-ee.result/1 object")
    checks = result.get("checks")
    if not isinstance(checks, list) or not all(
            isinstance(c, dict) and all(isinstance(c.get(k), str) for k in ("id", "execution", "outcome"))
            for c in checks):
        return invalid("result.json checks are not a list of records with id, execution and outcome")
    ids = [c["id"] for c in checks]
    if len(ids) != len(set(ids)):
        return invalid("result.json records a check more than once")
    if any(c["outcome"] not in CHECK_OUTCOMES for c in checks):
        return invalid("result.json contains an unrecognized check outcome")
    if any(c["execution"] != "ok" and c["outcome"] != "unknown" for c in checks):
        return invalid("a check that did not execute claims a known outcome")
    try:
        # The projection is judged only once it equals the retained native-check evidence.
        bind_checks(run, checks)
        bind_check_findings(result)
    except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError) as exc:
        return invalid(f"result.json contradicts retained check evidence: {exc}", checks)

    incomplete, defects = [], []
    if result.get("state") not in ("complete", "incomplete") or not result.get("finished_utc"):
        incomplete.append(f"run state is {result.get('state')!r}; it was not finalized normally")
    if result.get("error"):
        incomplete.append("run error: " + str(result["error"]))
    if not result.get("candidate_id"):
        incomplete.append("no candidate was captured")
    if result.get("empirical_feedback"):
        incomplete.append("registered bench feedback supersedes this run")
    expected = result.get("expected_check_ids")
    if not isinstance(expected, list) or not expected or not all(isinstance(e, str) for e in expected):
        incomplete.append("the declared check inventory is unavailable")
    else:
        missing = [e for e in expected if e not in ids]
        if missing:
            incomplete.append("declared checks without a result: " + ", ".join(missing))
        extra = [i for i in ids if i not in expected]
        if extra:
            return invalid("result.json records undeclared checks: " + ", ".join(extra), checks)
    for check in checks:
        if check["execution"] != "ok" or check["outcome"] == "unknown":
            incomplete.append(f"check {check['id']}: execution {check['execution']}, outcome {check['outcome']}")
        elif check["outcome"] == "defect":
            defects.append(f"check {check['id']} failed: {check.get('summary', '')}")
    if verify_exit not in (None, 0):
        return invalid(f"agent-ee verify exited {verify_exit}; the record cannot be trusted as fresh evidence",
                       checks, incomplete + defects)
    if incomplete:
        return {"verdict": "coverage_incomplete", "reasons": incomplete + defects, "checks": checks}
    if defects:
        return {"verdict": "check_failed", "reasons": defects, "checks": checks}
    return {"verdict": "checks_passed", "reasons": [], "checks": checks}


def invalid(reason, checks=(), other_reasons=()):
    return {"verdict": "result_invalid", "reasons": [reason, *other_reasons], "checks": list(checks)}


def cell(value):
    return " ".join(str(value).split()).replace("|", "/")


def summary(run, judgment, review_exit=None):
    lines = [f"## agent-ee checks-only gate: `{judgment['verdict']}`", "",
             EXPLANATIONS[judgment["verdict"]], ""]
    lines += [f"- {cell(reason)}" for reason in judgment["reasons"]]
    if judgment["checks"]:
        lines += ["", "| Check | Kind | Execution | Result | Summary |", "| --- | --- | --- | --- | --- |"]
        for c in judgment["checks"]:
            lines.append(f"| {cell(c['id'])} | {cell(c.get('kind', ''))} | {cell(c['execution'])} | "
                         f"{cell(c['outcome'])} | {cell(c.get('summary', ''))} |")
    lines += ["", f"Run directory: `{run}`."]
    if review_exit is not None:
        lines.append(f"`agent-ee review --checks-only` exited {review_exit}; checks-only always exits "
                     "nonzero, so the verdict comes from result.json.")
    lines += ["Checks-only runs perform no independent review or adjudication; this is not design acceptance.", ""]
    report = Path(run) / "REPORT.md"
    if report.is_file():
        lines += ["---", "", report.read_text()]
    else:
        lines.append("No REPORT.md was produced.")
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run", help="checks-only run directory containing result.json")
    parser.add_argument("--verify-exit", type=int, help="exit status of `agent-ee verify RUN`")
    parser.add_argument("--review-exit", type=int, help="exit status of the review (reported, never judged)")
    parser.add_argument("--expect", choices=VERDICTS, help="succeed only for this verdict")
    parser.add_argument("--expect-check", action="append", default=[], metavar="ID=OUTCOME",
                        help="with --expect, also require this executed check outcome")
    args = parser.parse_args(argv)
    expected_checks = dict(item.split("=", 1) for item in args.expect_check if "=" in item)
    if len(expected_checks) != len(args.expect_check) or (expected_checks and not args.expect):
        parser.error("--expect-check takes ID=OUTCOME and requires --expect")
    judgment = judge(args.run, args.verify_exit)
    sys.stdout.write(summary(args.run, judgment, args.review_exit))
    if not args.expect:
        return 0 if judgment["verdict"] == "checks_passed" else 1
    recorded = {c["id"]: c for c in judgment["checks"]}
    mismatches = [cid for cid, want in expected_checks.items()
                  if cid not in recorded or recorded[cid]["execution"] != "ok" or recorded[cid]["outcome"] != want]
    if judgment["verdict"] != args.expect or mismatches:
        print(f"Expectation failed: wanted verdict {args.expect} with {expected_checks or 'no check constraints'};"
              f" got {judgment['verdict']}" + (f", mismatched checks {mismatches}" if mismatches else ""),
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
