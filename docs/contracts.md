# Internal contracts (implementation agreement)

> Historical design, review or evaluation record. Status and proposed actions
> describe the recorded work, not the current implementation. Use the
> [current manual](README.md) for supported contracts and commands.

This is the historical builder agreement. The current
[manual](README.md), [scope reference](scope-reference.md) and
[result reference](result-reference.md) describe the implemented operator
contracts. Builder ownership assignments below record the original work split;
they are not ongoing restrictions on repository maintenance.

Python 3.12, standard library; installed KiCad `pcbnew` runs in `/usr/bin/python3`.
Implementation owners: coordinator candidate/executor/CLI/reviewers; Fable native
and engineering checks + fixtures; Argon finding validation/adjudication + tests.
Do not edit another owner's files without coordination. Shared filesystem/branch.

## Run layout

`candidate.json`: schema `agent-ee.candidate/1`, `candidate_id`, `scope`, `files`
(relative captured input name -> sha256), source locators, source git revisions.
`inputs/`: dereferenced copied input files, protected by Bubblewrap in subprocesses.
`work/<check-id>/`: writable scratch, execution.json, console.log, native outputs.
`checks/<id>.json`: validated check result (each written immediately).
`reviews/<role>.json`: validated initial review; raw stdout and execution separately.
`events/`: immutable finding dispositions. `result.json` and `REPORT.md`: projection.
Inputs, results and policy have distinct identities; all reviewer records carry
candidate_id, run_id, packet_sha256. Old run evidence is never auto-imported.

## Scope JSON

Schema `agent-ee.scope/1`; `name`, `scope` (text), `limitations` (string list),
`inputs`: list of `{path, at, include?, exclude?}`; `path` relative to scope file,
expanded `~` allowed, `at` relative to inputs. Explicit source roots are resolved;
symlinks encountered inside a selected tree are refused. Declare external
libraries as another input at their effective path. Overlap is refused.
`schematic` and `board` relative to inputs; `checks` list, described below;
`reviewers` optional backend definitions; `requirements` list of input-relative
text paths for packet context. Include all relevant config/models/requirements.
Templates at inputs/stock/template, symbols at inputs/stock/symbols, footprints
at inputs/stock/footprints; copy required stock libraries or all stock libraries.

## Checks API (Fable)

`agent_ee.checks.run_checks(run_dir: Path, deadline: float, **context) -> list[dict]`.
Deadline is `time.monotonic()` absolute. Load `candidate.json` and iterate
`scope.checks` through `agent_ee.check_registry.registry`. Each check has unique
safe `id` and `kind`; kinds initially `erc`, `drc`, `netlist`, `geometry`,
`rc_transient`. Parameters may be added and documented by Fable.
`agent_ee.native_run.run(intent, *, work, inputs=None, deadline=None, mounts=(),
baseline=None, ...)` runs an `Intent` (argv, timeout, stdin, network,
isolation) through one adapter; the sandbox adapter runs argv inside Bubblewrap
(via `agent_ee.executor.execute`), with inputs visible at `/candidate`, current
scratch at `/work`, Python package at `/app`. When `deadline` is supplied,
`native_run.clamp(intent, deadline)` cuts the timeout to the remaining budget and
enforces a 1 s launch floor (`native_run.LAUNCH_FLOOR_S = 1`): if
`deadline - time.monotonic() <= 1`, it refuses before launch by raising
`native_run.DeadlineExhausted`. Stage steps (`stage_receipts._run_bound_step`)
apply that 1 s floor once at admission via `native_run.clamp` before creating the
receipt directory (refusing with `ValueError('stage step deadline exhausted
before launch')`); once admitted, their version and stage runs pass
`deadline=None` to `native_run.run` with timeouts cut to the remaining admitted
step budget (floored at `0.001 s`), so budget spent in setup is recorded in the
receipt as an observed `timeout` rather than raising `DeadlineExhausted` after
the receipt directory exists (which would leave a receipt without an executor
record, reported as `cancelled`). `native_run.run` mounts the run's baseline at
`/evidence/baseline.json` when present and returns an `Execution` record
(`ok|failed|timeout|unavailable|interrupted|output_limit|callback_failed`),
exit_code, argv, sandbox_argv, console_sha256. For host and docker runs,
`native_run.completed(execution, *, text=False)` returns
`subprocess.CompletedProcess` or raises with `subprocess.run` parity: a fresh
copy of `execution.launch_error` (or `FileNotFoundError`) when `execution ==
"unavailable"`, and `subprocess.TimeoutExpired` when `execution == "timeout"`
with POSIX None-parity (`output` and `stderr` remain `bytes` even when
`text=True`, and a silent stream with `b""` is `None` rather than `b""`). No
passing fallback.
`agent_ee.storage`: digest(path), identity(JSON), read_json(path), write_json(path,value).
Historical note: the docstring claim in `scripts/k91_native_positive.py:13-16`
that the router pin is patched for `step()` while `attempt.PIN` and
`PINNED_CODE` on disk stay unchanged is stale after AGENTEE-539 moved the pin to
`src/agent_ee/data/kicad-route-pin.json` (`routing_adapter.pinned_layout` /
`qualified_layout`), and the `patch.object(attempt, 'PIN', ...)` /
`PINNED_CODE` context at `scripts/k91_native_positive.py:141` is inert; the
script's bytes are intentionally kept unchanged because
`tests/test_k91_harness_classifier.py:55` sha-pins the frozen v1 harness.

Result shape: `{id, kind, execution: 'ok|failed|unavailable|timeout|malformed|not_run|interrupted|unsupported',
outcome: 'pass|defect|unknown|info', summary: str, evidence: [run-relative files],
details: dict, limitations: [str]}`. Successful native violations = execution ok,
outcome defect. Missing/failed check = unknown. Write checks/id.json immediately.
Keep native reports and source locators; rc simulation derives connectivity and
values from native exported XML netlist. A check has a defensible fixed criterion.
No inventing manufacturing/current/thermal requirements for donor geometry.

## Review and dispositions API (Argon)

`agent_ee.findings.validate_review(value, candidate_id, run_id, packet_sha256,
run_dir) -> dict`: raise ValueError for malformed/stale; retain unsupported
claims as concerns. Review schema `agent-ee.review/1`, exact candidate_id, run_id,
packet_sha256, role, `coverage` list of strings, `limitations` list of strings,
`findings` list. Each finding: `id` (reviewer-local safe ID), `title`, `location`,
`condition`, `mechanism`, `severity` (`blocking|major|minor|info`), `assessment`
(`defect|concern`), `evidence` list of `{path: run-relative, locator: string}`,
`reasoning` string. Citation path must exist inside inputs/, checks/ or this
run's work/check dirs (not other reviewer output); no traversal or live links.
Existence is a structural check, not proof of the claim. Empty evidence does not
make a defect: downgrade to concern visibly or reject malformed schema.

`collect_findings(reviews: list[dict], checks: list[dict]) -> list[dict]` retains
all claims with namespaced IDs (role/id; check/id), default status unresolved.
Preserve checks with outcome defect as findings; no majority elimination.

`adjudicate(findings: list[dict], decisions: dict, run_dir: Path) -> list[dict]`.
Decisions schema `agent-ee.decisions/1`, candidate_id, run_id,
`decisions`: list `{finding_id, disposition: accepted|noted|dismissed|deferred|duplicate|closed,
reason: string, evidence: [{path,locator}], duplicate_of?: id}`.
Require explanation, valid evidence paths; accepted/closed need evidence;
`accepted` requires an established `assessment=defect`. `noted` retains a supported
minor/info `assessment=concern` that does not establish a violated criterion;
it needs citations and a reason. Major/blocking or unsupported concerns cannot
be noted, and defects cannot be hidden as duplicates of noted observations.
duplicate target must exist and cannot hide unresolved/accepted defects.
In a scope/3 run, a check with no acceptance predicate and no acceptance mapping
is advisory. Its defect is accepted from its cited, unchanged, sealed failed
result with no requirement witness, and is marked `authority: advisory`. It never
witnesses a requirement, and an acceptance check defect cannot be a duplicate of
it (AGENTEE-217).
Closed in SAME candidate is invalid for a claimed repair. Closure needs explicit
new-candidate linkage + fresh affected checks; coordinate exact closure API.
Append events rather than deleting earlier findings/dispositions. The CLI owns
event persistence and freshness. Treat adjudicator reasons as attributed
engineering judgments; JSON validation alone cannot prove electrical truth.

## Review execution (coordinator)

Fresh subscriptions-only model invocations, bounded wall time. Tool-free initial
reviewers receive the same captured evidence packet with complementary lenses;
independent scratch and no initial cross-review. Packet includes native sources
as needed, check outputs, requirements, model limitations. Output/schema failure
stays visible. Existing Herdr builders do not count as independent fixture review.
Explicit decisions use evidence; no automatic majority vote or silent dismissal.

## Evaluation

`docs/evaluation-plan.md` predates code. Fixed RC requirement is 0.62..0.65 V
at 1ms after ideal 1V step. Seed 100k vs intended 10k with C=100n. Preserve
native ERC/DRC cleanliness when seeding, including saved PCB value parity.
Control concerns and missed seeds are reported. Real donor findings are open
investigation, not seeded expected prose. Repair only disposable copies.
