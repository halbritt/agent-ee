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

Campaign integration, the AGENTEE-95 receipt module and the shared stage gate
are separate items. Root integrates them; this page describes the adapter and
the interface it expects from them.

## Pinned compatibility

The adapter supports kicad-route commit
`08179cdc97d08864da127f14d53ac46cf5a27851` only. That commit has no versioned
result schema, no `--version` flag and no machine run locator, so the adapter
checks the tool instead of trusting it:

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

Inside the sandbox the attempt script accepts only those options. Each value
must match a fixed pattern. The script does not use argparse, because its usage
exit 2 would read as a partial route. It then runs:

```text
/usr/bin/python3 -B -X pycache_prefix=/work/routing/pycache /opt/kicad-route/bin/kicad-route
  --out /work/routing/out run --backend krt --dir /candidate/P --board B --schematic S
  --name N --label L --protect existing --budget T
```

`--out` is a global option and comes before `run`. The router reads the
read-only `/candidate` copy, so it cannot edit the design it routes. Its
environment is rebuilt from constants: `PATH`, `HOME`/`TMPDIR`/XDG under
`/work/routing`, `LANG`, `PYTHONDONTWRITEBYTECODE`, `PYTHONPYCACHEPREFIX`,
`PYTHONNOUSERSITE`, `KICAD_ROUTE_STATE` set to the read-only mount, and
`GIT_OPTIONAL_LOCKS=0` with no system or global git configuration. Nothing is
inherited. `--backend-arg`, `--backend-env`, `--force`, `--canary`, `--rev`,
`KICAD_ROUTE_LEDGER` and `KICAD_ROUTE_OUT` are never passed.

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
| 4 | no attempt directory, a `[route] refused:` line, no `result.json` | `refused` | none; coverage `missing_routing` | 4 |
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
- the board hash equals both `delivered_board_sha256` and
  `validated_board_sha256`, the board kicad-route graded after its refill.

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
library tables. KiCad library directories come from the image's
`/usr/share/kicad`, as the pin hard-codes. That is recorded, not qualified as
catalogue authority. The report's `identity` covers every field.

`verify(run, inputs)` rebuilds the outcome from the retained bytes and raises
`ValueError` if:

- the report identity or a retained hash differs;
- the inputs differ from the coordinator's pre-step capture;
- the retained `result.json` and `attempt.json` classify differently;
- the delivered board is not the board the router validated;
- a non-eligible attempt has a delivered board;
- the report is missing.

A contradiction may sit over reports that alone look eligible, because some
contradictions come from the file system.

## Acceptance stays with agent-ee checks

A routing receipt, `result.json` and kicad-route's own validation are
provenance and diagnostics, not check witnesses. kicad-route's validation
permits pre-existing DRC findings. After capture, the campaign's ordinary
checks decide:

- DRC with schematic parity and zero unconnected items;
- `copper-width/1`;
- the compiled routing rules (AGENTEE-63, not yet integrated);
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

`verify()` is the routing-specific check root should call before adoption and
when verifying a campaign. `test_kicad_route_stage_receipt.py` runs against
`agent_ee.stage_receipts` once both are integrated and is skipped until then.

## Qualification on the original placed RC

The placed RC is the hand-made [RC control](../fixtures/rc/PROVENANCE.md) with
every track, via and zone removed through pcbnew. Footprint placement, outline,
schematic, project and library tables are unchanged.
[`test_kicad_route_native.py`](../tests/test_kicad_route_native.py) runs the
real router on it when `AGENT_EE_KICAD_ROUTE_CHECKOUT` names a pinned checkout.
[`test_kicad_route_adapter.py`](../tests/test_kicad_route_adapter.py) covers
exits 0 to 4, contradictions, timeout and cancellation with a coordinator-owned
fixture router. Those tests qualify adapter policy, not KRT behaviour.

## Limits

- One pin. A new kicad-route commit needs a reviewed new pin and fixture
  qualification. Nothing upgrades automatically.
- When the state is the checkout itself, the read-only mount includes the
  checkout's `.git` and `build/` directories. Host-side kicad-route runs by
  others still change directory metadata there, for example a transient git
  lock. AGENTEE-95 mount identity hashes contents and links, not times.
- A real exit 4 cannot occur in a fresh private OUT without `--canary`, which
  the adapter never passes. Exit 4 and the contradiction cases are covered by
  the fixture only.
- KRT byte-level repeatability is not established. Two identical attempts on
  the placed RC gave the same segment geometry but different new-segment UUIDs
  and file order.
