# kicad-route routing adapter (AGENTEE-93)

kicad-route is the routing stage of the [generator](generator-roadmap.md)
(decision 3). agent-ee does not route. The adapter runs one kicad-route
attempt as a coordinator step in the networkless sandbox, with the kicad-route
checkout and its state directory mounted read-only. It retains what the attempt
produced and decides whether the attempt's board may become a proposal. It
never decides whether the board is acceptable: that comes only from agent-ee's
own fresh checks on the captured proposal.

The adapter has two halves:

- [`kicad_route_attempt.py`](../src/agent_ee/kicad_route_attempt.py) runs
  inside the sandbox. It imports only the standard library and is mounted as a
  single read-only file, so its bytes are part of the step's tool identity.
- [`routing_adapter.py`](../src/agent_ee/routing_adapter.py) runs in the
  coordinator. `preflight()` checks the external tool, `step()` declares the
  attempt as an AGENTEE-95 `agent-ee.stage-step/1`, and `verify()` re-derives
  the outcome from the retained bytes.

Campaigns opt in with `routing_adapter` and run it through the existing step plan.
The coordinator verifies both the generic receipt and routing semantics before
adoption, and repeats those checks in `verify-campaign`.

## Pinned compatibility

The adapter supports kicad-route commit
`e4979160a19b89837f9a9e9e25db620f3449f40a` only (AGENTEE-187).
It requires `result.json` schema `kicad-route/route/1` and the exact top-level
result and attempt keys written by this pin. Unknown or absent result schema
versions are contradictions, even when the statuses and exit look successful.
Upstream `attempt.json` has no schema ID; its pinned shape includes `rules_files`.
The adapter checks the tool as well as its reports:

- HEAD is read from the checkout's git files. agent-ee does not run `git`
  there, because `git status` can rewrite the index.
- Every regular file under `bin/` and `src/` must match the pinned blob hashes
  exactly. An extra file, such as a `sitecustomize.py`, is refused. `__pycache__`
  is ignored because the router runs with `-B` and a private
  `PYTHONPYCACHEPREFIX` under `/work`, so cached bytecode in the checkout is
  never read or written.
- Any mismatch stops the step before the router runs. Nothing updates or
  repairs the external checkout.

The reported tool version is recorded as `unknown`: the pin has no version
flag, and `doctor` is not used because it looks up upstream releases. The
backend identity (`result.json` `backend`: KRT version, build, commit, Rust
router) is copied verbatim when a result exists. Nothing is filled in when it
does not.

## The step

`routing_adapter.step(project=, board=, schematic=, name=, label=, checkout=,
state=None, budget=300, timeout=None, backend='krt')` returns:

| Field | Value |
| --- | --- |
| `argv` | `/usr/bin/python3 -I -B /opt/agent-ee-routing/kicad_route_attempt.py --project P --board B --schematic S --name N --label L --budget T --timeout U --backend krt --state checkout` |
| `mounts` | adapter file at `/opt/agent-ee-routing/kicad_route_attempt.py`; checkout at `/opt/kicad-route`; a separate state directory, when there is one, at `/opt/kicad-route-state` |
| `source` | `/opt/kicad-route/src` |
| `writes` | `{"P/B": "routing/candidate/P/B"}`: the board is the only file the step can change |
| `artifacts` | `routing/adapter-report.json`, the router's stdout/stderr, and `routing/attempt/{result.json, attempt.json, RESULT.md, snapshot.json, validate, facts, work}` |
| `backend_report` | `routing/attempt/result.json`, key `backend` |
| `adoptable_exits` | `[0, 2]` |
| `timeout_s` | the attempt timeout plus 300 s for hashing and retention |

There is no `version_argv`. The coordinator, not the writer, declares the step.

Include a strict declaration in the campaign's frozen configuration. For example,
where `config` is an `agent-ee.campaign/1` object:

```python
config["routing_adapter"] = {
    "schema": "agent-ee.routing-adapter/1",
    "project": "project", "board": "rc_filter.kicad_pcb", "schematic": "rc_filter.kicad_sch",
    "name": "rc_filter", "label": "routing", "checkout": "/opt/kicad-route", "budget": 120,
}
```

The declaration allows only the fields shown plus `state`, `timeout` and `backend`.
Defaults match `step()`: state is the checkout, backend is KRT, budget is 300 s,
and timeout is twice the budget plus 300 s. No argv, mount, environment or pin override
is accepted. The selected board and schematic must equal the scope's identities,
and the board must be in `mutable_inputs`. The scope must be staged and declare
routing. The campaign dispatches exactly one routing entry only when routing is
its earliest failing stage; explicit routing `implementation_steps` cannot accompany
this opt-in. The writer must leave the complete placed source tree unchanged on
that turn. A writer edit stops before the router starts.

The first captured candidate freezes `state.json.routing_binding`: the canonical
step, production adapter hash and pin, executable, source tree and complete mount
identities. The adapter hash must match the captured policy. Before launch, a
changed checkout or backend state is refused rather than rebaselined. Receipt
observations before and after execution must equal this binding. Replay first
checks the anchored receipt chain, then the routing binding and exact executor
command, including read-only mount source/target pairs and network isolation.
It does not require the external checkout still to exist.
Generic stage-step programs, including historical rung scripts, retain their
existing behavior and do not produce the adapter's qualified routing evidence.

Inside the sandbox the attempt script accepts only those options. Each value
must match a fixed pattern. The script does not use argparse, because its usage
exit 2 would read as a partial route. It then runs:

```text
/usr/bin/python3 -B -X pycache_prefix=/work/routing/pycache /opt/kicad-route/bin/kicad-route
  run --out /work/routing/out --json --backend krt --dir /candidate/P --board B --schematic S
  --name N --label L --protect existing --budget T
```

`--out` names the sandbox's writable output directory; the pin accepts it
before or after `run`. `--json` produces a versioned record on stdout. The router reads the
read-only `/candidate` copy, so it cannot edit the design it routes. Its
environment is rebuilt from constants: `PATH`, `HOME`/`TMPDIR`/XDG under
`/work/routing`, `LANG`, `PYTHONDONTWRITEBYTECODE`, `PYTHONPYCACHEPREFIX`,
`PYTHONNOUSERSITE`, `KICAD_ROUTE_STATE` set to the read-only mount, and
`GIT_OPTIONAL_LOCKS=0` with no system or global git configuration. Nothing is
inherited. `--backend-arg`, `--backend-env`, `--force`, `--canary`, `--rev`,
`KICAD_ROUTE_LEDGER` and `KICAD_ROUTE_OUT` are never passed.

## Router targets from the scaffold producer

The [scaffold producer](scaffold.md#default-class-clearance) writes only fab
minimums as DRC floors (owner decision 2026-10-18). It records preferred limits as
router targets in its receipt's `derivation.router_targets`. The preferred Default
track width and via size are applied as the Default class routing defaults in the
project file the router receives. agent-ee has not established how the pinned
router uses class widths; its result is graded only by agent-ee's own checks. The preferred
clearance is not applied, because a class clearance is a DRC floor. The pinned
adapter does not pass it on either: `default_clearance_mm` and
`clearance_margin_mm` are fixed to `null` in the expected settings. A router that
consumes the clearance target needs a new adapter protocol and its own pinned
validation.

## Before the router runs

The attempt is `not_run` (exit 64), and the router does not start, when:

- `/work/routing` already exists, since every attempt needs a fresh private OUT;
- the candidate lacks the board or schematic, or contains a link;
- the checkout is not the pin or its code differs;
- an installed backend input is missing. The report names the exact path, for
  example `KICAD_ROUTE_STATE/.venv-krt/bin/python`;
- a write probe succeeds. The script tries to create a file in the state
  directory, in each backend component the pin reads (`.venv-krt`,
  `vendor/krt`), and in the checkout when it is separate. Every probe must be
  refused by the OS. A probe file that was created is removed and reported.

Tree identities of the tool code and the state components are recorded before
and after the attempt. Any difference makes the attempt a contradiction.

## Finding the attempt

The pin writes `OUT/runs/<name>/<label>/<UTC stamp>/` with a mutable `latest`
link beside it. It has no top-level result. The adapter looks only inside its
fresh OUT and requires exactly one real stamp directory. It never follows
`latest` or a path named in a report. Any other entry under `runs/`, a second
attempt, or a `latest` that does not name the attempt is a contradiction.
Report paths are untrusted strings, resolved against the router's known
working directory, and must land on the expected contained location.

## Exit semantics

The numeric exit is never trusted alone. Each exit needs its exact status
combination in a `result.json` and `attempt.json` of the pinned shape, plus the
fixed settings (`protect: existing`, empty backend args and environment, all
nets).

| Router exit | Required observation | Classification | Proposal | Adapter exit |
| --- | --- | --- | --- | --- |
| 0 | process PASS, validation PASS, routing COMPLETE, contained candidate | `complete` | `eligible` | 0 |
| 2 | process PASS, validation PASS, routing INCOMPLETE, contained candidate | `incomplete` | `eligible_partial`; coverage `routing_incomplete` | 2 |
| 1 | process FAIL with no candidate, or no `result.json` (early failure) | `backend_failure` | none; coverage `missing_routing` | 1 |
| 3 | process PASS, validation FAIL or NOT_RUN | `validation_failed` | none; the board is hashed as `rejected_candidate_sha256` and never copied | 3 |
| 4 | no attempt directory, a `kicad-route/refusal/1` machine record, no `result.json` | `refused` | none; coverage `missing_routing` | 4 |
| anything else | for example exit 2 without a result (usage error), exit 0 with INCOMPLETE, unknown fields or statuses, an escaping or linked candidate | `contradiction` | none; coverage `unknown` | 65 |
| — | the adapter's timeout or a cancellation (SIGTERM) | `timeout`, `cancelled` | none; the router's process group is killed | 75, 143 |
| — | an unexpected fault in the adapter itself | no report | none; `verify()` refuses the attempt | 70 |

The pin's own `overall_exit` returns 2 for any routing status other than
COMPLETE after validation passes. The adapter accepts only INCOMPLETE. An
AGENTEE-95 receipt adopts only exits 0 and 2. Every other exit is
non-adoptable, so the pre-step design stays the iteration's diagnostic input
and the stage stops incomplete. No routed board is substituted.

For 0 and 2 the candidate must also bind to the attempt and its inputs:

- `run_dir` and both candidate paths resolve to the discovered attempt;
- the board is reached without links;
- the candidate and reference trees contain only regular files;
- every reference snapshot file equals the captured input byte for byte, and
  `snapshot.json` and the input fingerprint agree with them;
- the candidate changes no project file other than the board, and the router
  restored none;
- the board hash equals both `candidate_board_sha256` and
  `validated_board_sha256`, the board kicad-route graded after its refill;
- `delivered_board_sha256` is a SHA-256 digest of the pre-refill board, and
  `refill_modified_board` agrees with whether that digest differs.

`kicad-cli pcb drc --save-board` writes `<board>.kicad_prl` beside the board.
It is the one extra candidate file allowed. It is hashed as editor state and is
never delivered.

## Retention and the report

All outcomes write `routing/adapter-report.json`
(`agent-ee.kicad-route-attempt/1`), including timeouts, cancellations and
refusals. The router's stdout and stderr are kept. The attempt's
`result.json`, `attempt.json`, `RESULT.md`, `snapshot.json`, `validate/`,
`facts/` and `work/` logs are byte copies under `routing/attempt/`. Design files
there (boards, project and rule files, including KRT's project writeback) are
hashed under `omitted_design_files` and not copied. The only board that leaves
an attempt is `routing/candidate/P/B` of an eligible exit. Missing reports are
listed under `missing_artifacts`; none is invented. The raw OUT stays in the
step's work directory.

The report records the request, the fixed argv and environment, the process
exit or signal, the discovery, the retained file hashes, the full input
inventory, the code and state identities before and after, the write probes,
the executables (`kicad-route`, Python, `kicad-cli`) and the KiCad template
library tables. For an eligible attempt it also records `candidate_files`, the
hash of every file in the attempt's candidate project directory, because the
candidate tree itself is a design file and is not retained. KiCad library
directories come from the image's `/usr/share/kicad`, as the pin hard-codes.
That is recorded, not qualified as catalogue authority. Every report has the
same fields, whatever the outcome. Its `identity` covers every field.

## Verification

`verify(run, inputs=None, *, receipt=None, layout=None)` rebuilds the outcome
from the retained bytes, the pin and the adapter's constants, and raises
`ValueError` on any disagreement. The report's identity and retained hashes
only show that the bytes agree with each other. They authenticate nothing.

The authority for the bytes is the AGENTEE-95 receipt, anchored by the
campaign. `verify` does not validate the receipt and does not replace it. The
caller runs `stage_receipts.validate_receipt` first, then passes the validated
receipt. The receipt supplies the inputs, and the report must agree with:

- the receipt's exit, which must be the adapter exit;
- its output for the board, which must be the delivered candidate's hash, or
  null when nothing was delivered;
- its hash of `routing/adapter-report.json`;
- its backend identity, when the retained `result.json` reports one;
- its step declaration: the routing stage, this request, the declared write,
  artifacts and mounts, and no version probe.

A report paired with another attempt's receipt is refused.

`layout` overrides the production sandbox paths and pin. Only the fixture
router's tests pass it. Without it, a report that claims any pin other than
`e497916` is refused, so a fixture attempt never verifies as a kicad-route
attempt.

For every outcome, `verify` refuses a report when:

- a field is missing, unknown or malformed, or a retained hash differs;
- the inputs differ from the captured pre-step candidate;
- the classification, proposal, coverage and adapter exit do not match;
- the reported router exit differs from the observed process exit;
- the claimed pin, adapter compatibility or `kicad-route` path is not the pin's;
- the tool version is anything but the pinned `unknown`, or the
  reproducibility label is not the backend's;
- the backend identity differs from the retained `result.json` `backend`, or is
  claimed without one;
- the missing-artifact list differs from what is retained;
- a non-eligible attempt has a delivered board, or, other than a
  contradiction, a bound candidate inventory;
- an attempt other than exit 3 has a rejected-candidate hash.

When the router ran, it also refuses:

- a commit or executable hash that is not the pin's code;
- a write probe set that is not every state directory, each refused by the OS;
- an argv, environment, working directory or timeout other than the fixed
  ones;
- a discovered attempt directory other than `out/runs/<name>/<label>/<stamp>`.

Each classification other than a contradiction, timeout or cancellation must be
re-derived from the retained `result.json`, `attempt.json`, the router's
stdout and the observed exit. Malformed retained reports, discovery problems or
changed tool state make it a contradiction.

For exits 0 and 2, `verify` re-checks every candidate binding the retained
evidence supports:

- `delivered_board_sha256` and `validated_board_sha256` both equal the
  delivered board, and `restored_project_files` is empty;
- `run_dir` and the candidate paths in `result.json` resolve to the discovered
  attempt;
- the retained `snapshot.json` names the board and schematic, every snapshot
  file equals the captured input, and its recomputed fingerprint is the
  result's input fingerprint;
- the candidate inventory is exactly the snapshot with only the board changed,
  plus at most the board's editor-state file;
- every expected report is retained.

A contradiction may sit over reports that alone look eligible, because some
contradictions come from the file system. It never delivers a board, so
`verify` does not re-derive why it is a contradiction.

The candidate inventory and the before/after tool identities are the adapter's
own in-sandbox observations. `verify` checks them for consistency but cannot
re-hash trees that are not retained. The receipt's mount identities are the
authority for the adapter, checkout and state bytes.

## Acceptance stays with agent-ee checks

A routing receipt, `result.json` and kicad-route's own validation are
provenance and diagnostics, not check witnesses. kicad-route's validation
permits pre-existing DRC findings. After capture, the campaign's ordinary
checks decide:

- DRC with schematic parity and zero unconnected items;
- `copper-width/1`;
- the configured compiled routing rules (AGENTEE-63);
- preservation of placement and outline.

Freerouting attempts carry the label "attempt only; never a reproducible
measurement". KRT attempts carry "deterministic backend, repeatability not
qualified by this attempt".

## AGENTEE-95 interface

The adapter uses the AGENTEE-95 step and receipt as drafted on
`impl/stage-receipts` and adds no receipt of its own. It relies on the
following from that draft:

- The coordinator runs the step after the writer session. The writer never
  invokes the router or supplies its argv, mounts or environment.
- The declared mounts are read-only. The receipt hashes each mount, the
  executable and the source tree before and after; a change is a violation.
- Only exits 0 and 2 can be adopted, and only the declared write is copied
  back, and only while the design still equals the receipt's inputs.
- Every other exit, timeout and cancellation keeps its receipt, with missing
  artifacts recorded as null, and stops the iteration incomplete.
- Applicability compares the whole delivered tree with the receipt's inputs
  plus its outputs. A later writer edit to the board makes the receipt
  `not_applicable`.

`verify_campaign_receipt(receipt_dir, expected=..., binding=...)` checks the
implementation/source/index binding, frozen tool identity, command and mounts,
then calls `verify(run, receipt=receipt)` after `validate_receipt`, both before
adoption and when verifying a campaign. Generic stage-receipt checks do not
interpret the routing report. A crashed or killed adapter with no report remains
an unadoptable receipt with unknown routing coverage; a missing report never
authorizes a proposal. `test_kicad_route_stage_receipt.py` and the receipt case in
`test_kicad_route_native.py` import `agent_ee.stage_receipts` unconditionally;
a missing or broken module fails their collection.

## Qualification on the original placed RC

The placed RC is the existing [RC control](../fixtures/rc/PROVENANCE.md) with
every track, via and zone removed through pcbnew. Footprint placement, outline,
schematic, project and library tables are unchanged.
[`test_kicad_route_native.py`](../tests/test_kicad_route_native.py) runs the
real router on it when `AGENT_EE_KICAD_ROUTE_CHECKOUT` names a pinned checkout,
once directly under the executor and once as an AGENTEE-95 receipt. Each run
verifies with the production pin, then forges the retained validated-board hash
on the real bytes and checks that `verify` refuses it.

The [standard image](container.md#pinned-routing-tool) installs the pinned
checkout at `/opt/kicad-route` and sets the variable, so strict CI runs these
tests. Their prerequisite, `require_kicad_route()` in
[tests/toolchain.py](../tests/toolchain.py), behaves as follows:

| Configuration | Without `AGENT_EE_REQUIRE_TOOLCHAIN` | With `AGENT_EE_REQUIRE_TOOLCHAIN=1` |
| --- | --- | --- |
| Variable unset | Class skipped: `AGENT_EE_KICAD_ROUTE_CHECKOUT is unset` | Failure |
| Set; HEAD, pinned code or KRT state files fail `preflight` | Failure naming every problem | Failure |
| Set; KRT's venv cannot import `grid_router`, numpy, scipy or shapely | Failure with the import error | Failure |

`NativePrerequisiteTest` exercises the first two rows in a child unittest
process; the native class covers the broken-runtime row with a pinned copy of
the code and an empty venv.
[`test_kicad_route_adapter.py`](../tests/test_kicad_route_adapter.py) covers
exits 0 to 4, contradictions, timeout and cancellation with a coordinator-owned
fixture router. Its retained-verification cases forge one claim at a time with
fresh hashes and identity. Those tests qualify adapter policy, not KRT
behaviour.

[`test_routing_campaign.py`](../tests/test_routing_campaign.py) exercises exits
0 through 4 through the campaign writer and checks the anchored implementation
record and receipt applicability for each exit. It also rejects altered command,
mount, timeout and adoption declarations, and a rehashed execution record claiming
a writable state mount. Its native case routes the placed RC inside a staged
campaign, checks the full host checkout identity and OS-refused write probes, and
runs fresh DRC and `copper-width/1` checks on the captured proposal. Reviewers are
scripted in these orchestration tests; native tool evidence is produced by the
installed KiCad and pinned KRT processes.

## Pin qualification record (AGENTEE-187, 2026-10-18)

The previous pin was `08179cdc97d08864da127f14d53ac46cf5a27851`.
The new pin is the local upstream HEAD captured at implementation start,
`e4979160a19b89837f9a9e9e25db620f3449f40a`, rather than the older `a471b36`
mentioned when the ticket was filed. `PINNED_CODE` was regenerated from this
commit's Git blobs under `bin/` and `src/`. The image installer fetches the same
commit; KRT remains `v0.23.1` at `295f9ee197517e8323726853361eb137cc08ba9e`
with its existing binary and wheel hashes.

Review of the new `cli.run_attempt`, `contract`, and `validate` code identified
these changes: the versioned route record, additional provenance and sandbox
fields, `attempt.rules_files`, `settings.barrier`, machine refusal records,
and separate hashes before and after refill. The adapter adopts these without
relaxing the fixed routing settings, input binding or project preservation.
The full code pin still rejects added, removed or changed source files.

Reproduce qualification with a detached clone at the new pin and the existing
KRT state installed, setting `AGENT_EE_KICAD_ROUTE_CHECKOUT` to that clone:

```sh
AGENT_EE_REQUIRE_TOOLCHAIN=1 PYTHONPATH=src python3 -m unittest tests.test_kicad_route_native -v
AGENT_EE_REQUIRE_TOOLCHAIN=1 PYTHONPATH=src python3 -m unittest tests.test_kicad_route_adapter -v
```

The native suite qualifies the original placed RC directly and through a stage
receipt, refuses a forged validated-board hash, and compares two fresh runs
with `routing_equivalence.compare_boards`. Equality is normalized copper geometry;
UUID and file-order differences are recorded separately. It also demonstrates
that a duplicate or moved segment is unequal. Policy tests include
`test_unknown_result_version_refused`, missing schemas and changed attempt
shapes, and acceptance of a refilled candidate only when its graded hashes bind.
Independent integration review remains the coordinator's responsibility.

## Limits

- One pin. A new kicad-route commit needs a reviewed new pin and fixture
  qualification. Nothing upgrades automatically.
- When the state is the checkout itself, the read-only mount includes the
  checkout's `.git` and `build/` directories. Host-side kicad-route runs by
  others still change directory metadata there, for example a transient git
  lock. AGENTEE-95 mount identity hashes contents and links, not times.
- Pre-routing refusals include usage, input, ledger, canary and state failures.
  The policy fixture covers known machine refusals, unknown refusal versions
  and obsolete human output; the last two cannot establish a refusal verdict.
- KRT byte-level repeatability is not established. Two identical attempts on
  the placed RC gave the same segment geometry but different new-segment UUIDs
  and file order. The [routing equivalence check](routing-equivalence.md)
  tests normalized copper geometry equality and records byte differences
  without failing them. The native repeat test compares two real KRT outputs
  and identifies a moved segment.
