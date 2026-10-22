#!/usr/bin/env python3
"""Reproduce the consumer sample locally: the RC fixture, seeded and control.

No hosted run is used. The seeded scope must give `check_failed` with an
`rc-step` defect; the control scope must give `checks_passed`. The verdict is
judged from result.json by scripts/action_gate.py, never from the review exit.

  consumer_sample.py judge seeded RUN_DIR    judge a retained run directory
  consumer_sample.py judge control RUN_DIR
  consumer_sample.py run seeded|control      run `agent-ee review --checks-only`
                                             into a new directory, then judge it

`run` needs the native toolchain (the container image or a host with kicad-cli
and ngspice). `judge` runs `agent-ee verify` first and refuses a run it rejects;
--display-only skips that and never reports acceptance. `run` refuses an existing
output directory.
"""
import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import action_gate  # noqa: E402

CASES = {
    "seeded": ("fixtures/rc/seeded.scope.json", "check_failed", ["rc-step=defect"]),
    "control": ("fixtures/rc/control.scope.json", "checks_passed", ["rc-step=pass"]),
}


def expectation(case, run):
    """The action_gate arguments that state what `case` must produce."""
    _, verdict, checks = CASES[case]
    argv = [str(run), "--expect", verdict]
    for item in checks:
        argv += ["--expect-check", item]
    return argv


def judge(case, run, verify=True, review_exit=None):
    """Accept a retained run only after `agent-ee verify` accepted its evidence.

    verify=False is a display-only projection of result.json: it prints the
    verdict but never reports acceptance (exit 2 even when the projection matches).
    """
    argv = expectation(case, run)
    if review_exit is not None:
        argv += ["--review-exit", str(review_exit)]
    if verify:
        done = subprocess.run([str(ROOT / "bin" / "agent-ee"), "verify", str(run)], cwd=ROOT)
        argv += ["--verify-exit", str(done.returncode)]
    code = action_gate.main(argv)
    if not verify and code == 0:
        print("display only: evidence was not verified, so this run is not accepted", file=sys.stderr)
        return 2
    return code


def run(case, max_seconds, out=None):
    scope = ROOT / CASES[case][0]
    base = Path(out) if out else Path(tempfile.mkdtemp(prefix=f"agent-ee-sample-{case}-"))
    target = base / "run" if out is None else base
    if target.exists() or target.is_symlink():
        print(f"agent-ee: {target} already exists; a run needs a new directory so old evidence is never judged as new",
              file=sys.stderr)
        return 2
    done = subprocess.run([str(ROOT / "bin" / "agent-ee"), "review", str(scope), "--out", str(target),
                           "--checks-only", "--max-seconds", str(max_seconds)], cwd=ROOT)
    print(f"run directory: {target}", file=sys.stderr)
    return judge(case, target, review_exit=done.returncode)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="mode", required=True)
    j = sub.add_parser("judge")
    j.add_argument("case", choices=CASES)
    j.add_argument("run")
    j.add_argument("--display-only", action="store_true",
                   help="skip `agent-ee verify`; print the projection but never accept (exit 2 on a match)")
    r = sub.add_parser("run")
    r.add_argument("case", choices=CASES)
    r.add_argument("--max-seconds", type=int, default=900)
    r.add_argument("--out", help="new run directory (default: a fresh temporary directory)")
    args = parser.parse_args(argv)
    if args.mode == "judge":
        return judge(args.case, args.run, verify=not args.display_only)
    return run(args.case, args.max_seconds, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
