# Operator guide

## First real review

Run from the installed checkout with the prerequisites in
[installation](installation.md). The output directory must not exist.

```sh
agent-ee doctor
agent-ee review --profile profiles/codex.json fixtures/rc/control.scope.json \
  --out build/operator-control --max-seconds 1200
agent-ee inspect build/operator-control
agent-ee verify build/operator-control
```

This invokes actual native tools and subscription inference. It is not a dry
run. Read `build/operator-control/REPORT.md` and the native evidence it cites.
The recorded control passed, but new model outputs are nondeterministic; inspect
your actual result rather than assuming it must match the demonstration prose.
The requirement is the captured ideal unloaded RC response and straight-track
width, not automotive or manufacturing qualification.

For a native integration check without inference, use a different fresh path:

```sh
agent-ee review --profile profiles/codex.json fixtures/rc/control.scope.json \
  --out build/operator-native --max-seconds 120 --checks-only
```

Expected command exit is **1**: checks-only is an incomplete adversarial review,
even when its ERC, DRC, netlist, geometry and response checks all execute as
expected. This is useful engineering evidence, not a completed review approval.

## Prepare a real design scope

Identify the saved root schematic, matching board, project settings/custom
rules, every referenced sheet, effective symbol/footprint tables and external
libraries. Include applicable requirement documents, assembly population,
simulation models and authoritative references needed for the review. Preserve
native files as the source of truth. Narrow the investigation when the available
inputs cannot justify a broader conclusion.

Create a scope following the [scope reference](scope-reference.md). Its source
paths resolve relative to the scope file; its destination and native paths
resolve inside captured `inputs/`. Reuse a fixture configuration only after
replacing its requirements and paths deliberately. Do not carry the fixture's
RC limits into an unrelated circuit.

For this workstation's real donor, the supplied command is:

```sh
agent-ee review --profile profiles/codex-baseline.json scopes/donor-case-alias.json \
  --out build/operator-donor --max-seconds 3600
```

This case-alias scope adds a byte-identical uppercase sheet filename only inside
the disposable capture. The original `scopes/donor.json` intentionally retains
the as-received hierarchy problem. Read the [original baseline](../evidence/BASELINE.md)
and [tool-enabled follow-up](../evidence/MODEL-BASELINE.md); the alias is not a
source-project repair. Both scopes need path edits on another machine.

Codex is a [provisional starting profile](../evidence/MODEL-SELECTION.md), not an
implicit default. Claude and Agy remain explicit options. The donor has known
defects and unknowns, and observed automatic adjudications needed assisted
reconciliation; inspect the actual outcome and preserve failed attempts.

## Inspect progress and final evidence

Checks and initial reviews are saved as they complete. From another shell,
invoke `agent-ee inspect RUN` while the review is running. Also inspect
`work/CHECK_ID/execution.json`, console logs and `review-executions/ROLE.json`
when available. The result projection can lag a currently executing subprocess.
An interrupted record marked `running` is not a liveness guarantee.

Read final evidence in this order:

1. `result.json` / `REPORT.md`: candidate ID, state, outcome and declared scope.
2. Failed/unknown checks and their actual commands/logs: determine whether a
   tool reported a criterion failure or failed to establish coverage.
3. Reviewer coverage and packet omissions: identify what was actually inspected.
4. Findings/dispositions: inspect the cited native artifact and operating
   condition, calculation, model or measurement behind each consequential claim.
5. Open questions and later prototype/bench requirements.

A report sentence is attributed engineering judgment. A valid file citation is
traceability; it is not electrical proof. Check part/package/pin identities,
population and module boundaries against the original design before acting.
The donor CAN2 investigation is an example: nominal resistor values without
DNP state would describe the wrong assembled circuit.

For an automated acceptance gate, run this from the checkout, after a review
has finished. It deliberately checks both freshness and the design outcome:

```python
import json
import subprocess
from pathlib import Path

run = Path("build/operator-control")
subprocess.run(["./bin/agent-ee", "verify", str(run)], check=True)
result = json.loads((run / "result.json").read_text())
if result["outcome"] != "criteria_satisfied":
    raise SystemExit("Review has defects, open questions or incomplete coverage")
print("Declared criteria satisfied for", result["candidate_id"])
```

## Investigate disagreements

Keep a supported singleton finding even when another reviewer reports no issue.
Investigate duplicate mechanisms, contradictory assumptions and missing evidence
against the captured source. Do not resolve through reviewer counts. A plausible
claim without adequate evidence remains a concern; a material unresolved
question stays deferred and blocks satisfaction of the declared review.

Use an operator decisions document for an explained disposition update. The
[result reference](result-reference.md) specifies the exact schema and restrictions.
Obtain the current candidate/run IDs from `result.json`, use exact finding IDs,
and cite evidence in this run. Then invoke:

```sh
agent-ee adjudicate build/operator-control build/operator-decisions.json
agent-ee inspect build/operator-control
```

This example requires an intentionally prepared decisions file; it is not
generated automatically by the command. Do not manufacture decisions merely to
remove blockers. A failing executable criterion cannot be waived into a pass.
If an applicable requirement, model, exclusion or rule must change, record that
decision and run a new review under the changed criteria.

## Authorized correction

Review does not modify a design. First confirm repair is authorized for that
project/candidate; the original task authorized disposable fixture repairs only.
Copy the selected design into a separate disposable workspace or use an isolated
project branch. Keep reviewer experiments away from the candidate under review.

Make and save the native change, inspect the actual saved diff, and create a new
scope pointing to that copy. Preserve original check specifications and declared
requirement bytes. Review the changed candidate in a new output directory. For
the RC example, affected verification includes native ERC, native DRC/parity and
the measured response, not just the author's explanation of the value change.

After the entire new review satisfies criteria, link the specific old accepted
finding to the new evidence:

```sh
agent-ee close-correction build/operator-old build/operator-repaired \
  --finding check/rc-step --checks erc,drc,rc-step \
  --reason 'Saved resistor value corrected; native parity and the unchanged response limits pass on the new candidate.'
```

This is a template for existing, verified runs and that actual finding ID. For
another mechanism, select the correct finding and affected checks and explain
their relationship. The command does not discover the complete affected-check
set. It refuses same-candidate closure, changed requirements/criteria, a
nonaccepted old finding, stale evidence, or a new review with unresolved defects.

The correction event is appended to the **old** run. Its original defect remains
true of the old candidate. The new report supplies the fresh evidence. If
another old finding describes the same defect without a duplicate link, inspect
and explicitly link its closure too; no text matching silently closes it.

The actual complete defect-and-repair exercise can be rerun with:

```sh
python3 scripts/demo.py --profile profiles/codex.json --out build/operator-demonstration --seconds-per-run 1200
```

It performs real control/seed reviews, at most one disposable value repair and a
fresh repair review. Failures remain recorded; it does not retry until passing.
See [evidence reproduction](reproducing-evidence.md) for expectations and outputs.

## Stop, recover and preserve

SIGINT interrupts work and preserves useful records. Depending on where it is
caught, later declared steps can still run; see the [CLI interruption behavior](cli-reference.md).
Examine process status and the outcome before starting a fresh run.
`agent-ee resume RUN --out NEW_RUN`
recaptures current inputs and reruns checks/reviewers; it spends inference again.
It does not repair source, continue an old provider session or inherit old
acceptance. If the scope moved, use `review` with a new explicit scope instead.

Keep complete run directories outside the source design. Commit readable
evidence and archive expanded inputs/configuration using the project's artifact
convention. Do not delete failed rounds merely because a later round succeeded.
Record which criteria remain unknown, and keep bench/prototype obligations
separate from this candidate's declared review result.
