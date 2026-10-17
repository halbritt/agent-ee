# Maintainer guide

## Repository ownership and layout

The reusable package is `src/agent_ee`. `bin/agent-ee` is the system-Python
source launcher; `scripts/install.sh` installs discovery links. The Python
package's console entry point is declared in `pyproject.toml`. `skills/agent-ee`
is the operator skill, not an alternate implementation of orchestration.

Project choices belong in `scopes/`, engineering references in `references/`,
and disposable native fixtures in `fixtures/`. `tests/` exercises orchestration,
failure interpretation and representative native behavior. `evidence/` retains
observations and histories; `build/` is ignored disposable output.

The initial repository had no prior implementation or remote. The working
capability was developed on `build/review-capability` and integrated locally
on `main`; the remote is `https://github.com/halbritt/agent-ee`, now public.
Preserve unrelated work and use an isolated branch/worktree for changes. Do
not include credentials, host caches or undeclared source-project mutations.

## Development and checks

Use the system interpreter that imports pcbnew:

```sh
PYTHONPATH=src /usr/bin/python3 -m unittest discover -s tests -v
```

The original recorded release has 63 tests; the subscription-profile release has
69. This is an observation at the recorded implementation, not a permanent expected test count. Tests need the installed
native environment for their native/Bubblewrap paths; this repository does not
install those dependencies automatically. Tests that execute native tools skip
with a prerequisite reason when Bubblewrap/user namespaces, KiCad (including
`pcbnew` and fixture libraries), or ngspice are absent. Pure Python and simulated
failure tests still run. Test assertions and check failures are never converted
to skips. To require the toolchain, as CI should, run:

```sh
AGENT_EE_REQUIRE_TOOLCHAIN=1 PYTHONPATH=src /usr/bin/python3 -m unittest discover -s tests -v
```

The test-only preflight uses the production sandbox command without network
access. Only recognized namespace-denial diagnostics and explicit missing-tool
results can skip; malformed commands and other probe failures fail the suite. This mode changes only tests;
production execution still records unavailable/failed tools as incomplete work.

The [container image](container.md) provides the pinned toolchain. Project CI
runs the suite there with `AGENT_EE_REQUIRE_TOOLCHAIN=1` and no permitted skips.


| Test module | Protection |
| --- | --- |
| [test_toolchain.py](../tests/test_toolchain.py) | Real discovery with absent tools, strict-mode refusal, prerequisite diagnostics and preservation of assertions/production semantics |
| [test_candidate.py](../tests/test_candidate.py) | Captured identity, selected source changes, dependency changes and link restrictions |
| [test_checks.py](../tests/test_checks.py) | Native control/seed behavior, hierarchy, report parsing, passive modeling and simulation failure interpretation |
| [test_execution_failures.py](../tests/test_execution_failures.py) | Unavailable/failed/timeout subprocesses, sandbox boundary and invalid execution/review behavior |
| [test_reviewer_identity.py](../tests/test_reviewer_identity.py) | Exact role/candidate/run/packet binding and schema constraints |
| [test_findings.py](../tests/test_findings.py) | Singleton retention, unsupported allegations, evidence/dispositions, duplicate and closure invariants |
| [test_profiles.py](../tests/test_profiles.py) | Explicit allocation, profile freshness, subscription response handling, native adapter contracts and refusal of profile-only repair closure |
| [test_cli.py](../tests/test_cli.py) | Required work, outcome projection, interruption/budget preservation and correction behavior |
| [test_doctor.py](../tests/test_doctor.py) | Doctor version output and binary hashes, missing tools, unimportable pcbnew, unhashable binaries and `--checks-only` |
| [test_action_gate.py](../tests/test_action_gate.py) | CI/action verdicts from `result.json`: passing checks, defects, incomplete coverage, missing or malformed results and failed verification |

Avoid tests that only echo generated implementation text. Define the engineering
expected response and failure mechanism independently, then exercise actual
tool evidence. Mocked orchestration tests supplement that proof; they do not
demonstrate hardware truth or a working subscription route.

## Internal interfaces

These are implementation seams, not a promise of a stable external Python API.
Prefer the CLI for operator automation. Exact current definitions are in the
linked source modules in [architecture](architecture.md).

| Interface | Contract |
| --- | --- |
| `capture(scope_path, run_dir, profile_path=None)` | Copy explicit selection, verify capture consistency, persist and return manifest |
| `verify_snapshot(run_dir)` | Verify exact copied-file set and candidate identity; return manifest or raise |
| `verify_source(run_dir)` | Also compare live scope and current selected source bytes |
| `execute(inputs, work, argv, *, timeout=120, stdin=None, network=False, extra_ro=())` | Isolated subprocess; persist execution/stdout/stderr/console; return execution record |
| `run_checks(run_dir, deadline)` | Ordered handlers; monotonic absolute deadline; persist each result |
| `build_packet(run_dir, checks, run_id)` | Construct/persist bounded packet with explicit omissions and identity |
| `initial_reviews(run_dir, packet, deadline)` | Return validated reviews plus per-role execution records |
| `validate_review(value, candidate_id, run_id, packet_sha256, run_dir)` | Exact schema/identity/citation validation and visible unsupported-defect downgrade |
| `collect_findings(reviews, checks)` | Preserve all claims and native check defects with namespaced IDs |
| `adjudicate(findings, decisions, run_dir)` | Return copied updated finding list; validate dispositions; CLI persists run-level events |
| `verify(run, *, current_source=True)` | Full CLI integrity/freshness check; return current projection |
| `link_correction(previous, current, finding_id, check_ids, reason)` | Full CLI verification, closure invariants, persistent event and old report update |

`CheckError(execution, message)` is the native-handler boundary for an explicitly
unknown result. Unexpected errors propagate to the run crash boundary and remain
incomplete. The lower-level `validate_closure` helper validates snapshot and
finding/check invariants, but is not a substitute for the CLI's full runtime and
artifact verification. Do not expose a shortcut as equivalent operator closure.

## Add a check

1. Write independent expected outcomes, source evidence, operating conditions,
   limits and relevant missing/failed-result cases. Decide which claims need a
   datasheet, native mapping, calculation, simulation, geometry or bench data.
2. Add a handler taking `Ctx` in `checks.py` and register it in `CHECK_FUNCS`.
   Define every parameter, unit, bound, default and prerequisite. Preserve an
   actual fixed criterion rather than encoding expected fixture prose/names.
3. Use `ctx.candidate_path` for captured paths and `ctx.execute(argv, timeout)`
   for tools. Native files are at `/candidate`; only `/work` is writable. Use
   the shared executor instead of bypassing the isolation/logging boundary.
4. Add original evidence with `ctx.evidence`, check-specific `details`, and
   explicit `limitations`. Preserve the source relationship: candidate digest,
   native reference/pin/net/UUID, document page, model and measurement identity.
5. Set successful `execution=ok` and an honest `outcome`. Raise `CheckError` for
   missing, unsupported, malformed or failed work. Do not make an unreadable
   report an empty passing report or substitute an ideal active-device model.
6. Exercise an applicable success, a meaningful engineering defect and execution
   failure cases. Then perform a fresh actual agent review; record what the
   reviewers saw and omitted. Update the scope/check/operator documentation.

Cross-check dependencies must be explicit. The current check order is not an
automatic dependency scheduler. New native versions/report formats require
real probes, not just an added permissive parser fallback.

## Add a reference, extraction or model

Capture the original reference and its provenance/checksum when the review
depends on it. A derived text/table/image should retain the original source
path, page/region/native locator and its generation command. Select the material
for the packet and inspect omission notices. A PDF captured but omitted as
binary is not an inspected datasheet.

Define model applicability, unsupported behavior, stimulus, measurements and
acceptance limits before using simulation for closure. Keep original native
connectivity authoritative. The existing passive generator does not consume
arbitrary model files; a new model type requires an adapter, validation and
independently justified expected behavior.

## Add a reviewer backend

Claude, Codex and Agy adapters are implemented behind `reviewer_call`; see
[profiles](reviewer-profiles.md). Add another explicit subscription adapter there
only when needed. A model string alone does not choose another harness. Preserve
the same initial-context separation, role/candidate/run/packet binding, required
coverage/limitations and schema/citation validation.

Record the requested model, actual available harness metadata, route, command,
binary identity, output and failure reason. An auth/quota failure stops that
operation. Owner subscription routing rules prohibit an OpenRouter fallback
for covered models; propagate that rule to subprocesses/agents as well.

The new harness must respect read-only inputs, isolated scratch and bounded
process-group cancellation. Review its environment, credential access, settings,
tools, MCP, hooks, network needs and cleanup explicitly. Test malformed output,
identity mismatch, provider failure, interruption and exhausted budget. Exercise
actual inference through the intended route before claiming integration.

## Policy, schema and release changes

The implementation hashes every Python module and records runtime fingerprints.
Python edits invalidate old policy freshness, including edits unrelated to one
particular check. A process also compares loaded policy with on-disk policy, so
do not change implementation files underneath an active demonstration and then
claim the resulting run exercised a consistent version.

Changes to schema `/1` fields require corresponding updates to validators,
runtime output schemas, CLI projection, tests, examples and manual.
There is no automatic migration of accepted evidence. Declare changed criteria,
requirements, model interpretation and exclusions; rerun the needed review.

Before publishing a change, verify documentation links/commands against source,
run checks appropriate to the change, review actual evidence/limitations, and
preserve useful failed iterations. Record consequential decisions in
[decisions.md](decisions.md). Keep the [acceptance index](../evidence/README.md)
clear about which implementation and which candidate each demonstration used.

## Evidence packaging

Expanded `inputs/`, native scratch `home/`, and generated `kicad-config/` are
ignored under `evidence/`; the complete bytes are retained in compressed archives.
Readable reports, native outputs, interpreted records and archive checksums are
committed directly. `.gitattributes` preserves raw evidence bytes and avoids
whitespace rewrites of native logs/patches. Do not normalize them after hashing.

`scripts/evidence_archive.py pack GROUP --source-ref COMMIT` packs a direct
directory `evidence/GROUP`, refuses a running record/existing archive, and checks
that the chosen Git ref reproduces every recorded Python policy file. Commit
the implementation first and use its matching ref. The helper verifies policy
matching; it does not independently re-establish electrical correctness or all
artifact freshness. Perform the normal verification before packaging.

Archives and their manifest must move together. New rounds use new names rather
than overwriting prior evidence. Restore/replay caveats and commands are in
[evidence reproduction](reproducing-evidence.md).

## Closed-loop seams and preservation rules

The baseline and campaign additions are explicit interfaces with separate ownership:

| Interface | Responsibility |
| --- | --- |
| `compile_baseline(scope, files)` / `load_baseline(run)` | Construct or reconstruct immutable requirements, criteria, baseline file hashes and scope/3 coverage |
| `describe_check(spec, check_specs=..., files=..., scope=...)` / `compile_coverage(scope, files)` | Pure scope/3 capability descriptions and executable acceptance coverage; never tool execution or inference |
| `freeze_evidence(run, checks)` / `verify_evidence(run, checks, required=...)` | Seal coordinator outputs before review; reconstruct that seal during verification |
| `validate_witnesses(run, witnesses, evidence)` | Bind a claimed defect to a mapped, executed, failed check and unchanged raw evidence |
| `check_measurements(ctx)` | Execute declared producer and captured parser, validate identities/provenance and evaluate limits |
| `check_coupled_margins(ctx)` | Join earlier same-condition metrics from distinct domains and evaluate every bound |
| `writer_lock(*directories)` | Context-managed nonblocking process leases, including enclosing campaign roots |
| `advance(history, result, manifest, max_iterations)` | Pure convergence decision and iteration record; no model calls or file mutation |
| `run_campaign(config_path, out)` / `verify_campaign(root)` | Own repair orchestration and verification of retained historical projections |
| `ingest(run, report_path, out)` | Validate and capture sample/calibration evidence; lower-level helper, not a standalone complete feedback workflow |
| `bench_review(run, report, out, max_seconds, review_out=...)` | Register source supersession and run fresh verification; `review_out` is an internal keyword-only placement hook |
| `campaign_feedback(root, report, out, max_seconds)` | Hold campaign ownership, admit feedback and continue a calibrated epoch |

Keyword-only markers and exact signatures remain in the source; these table
entries identify responsibilities rather than promise positional-call stability.
Prefer supported CLI entry points to bypassing locking or registration wrappers.
Do not expose `ingest` alone as equivalent to feedback: it does not by itself
supersede old acceptance or execute fresh checks/reviews.

Maintain these boundaries when extending behavior:

- Full baseline access must survive packet excerpt limits and every sandbox
  adapter. Captured requirements and verification programs cannot be repair targets.
- Initial reviewers receive current candidate and tool facts, not other reviews,
  implementation prose or prior campaign judgments. Empirical prediction records
  must remain tool-only when exposed as captured input.
- Only the parent creates the authoritative evidence seal and acceptance
  projection. A parser supplies numbers and raw-output references, not pass/fail
  authority. Generic tool programs require separate qualification.
- Convergence keys must remain comparable within an epoch. Baseline changes,
  silent new criteria or erased history cannot stand in for progress.
- Feedback registration is durable before re-review and can survive later failure.
  Do not restore old acceptance in an error handler or drop previous residuals.
- Parent leases, process-group cleanup and failure classifications must remain
  valid under nested campaign operations and interruptions.

## Closed-loop test protection

| Test module | Observable contract |
| --- | --- |
| [test_baseline.py](../tests/test_baseline.py) | Full baseline despite packet budget; source/evidence/witness integrity and check-only adjudication |
| [test_measurements.py](../tests/test_measurements.py) | Actual sandbox producer/parser execution, request/raw isolation, pinned binary, domain/condition/bounds and coupling |
| [test_convergence.py](../tests/test_convergence.py) | Protected criteria and metric margins, stalls, finite limits and execution-vs-design classification |
| [test_locking.py](../tests/test_locking.py) | Competing process refusal, exception cleanup and enclosing campaign ownership |
| [test_empirical.py](../tests/test_empirical.py) | Historical bias/uncertainty propagation and model/condition applicability |
| [test_applicability.py](../tests/test_applicability.py) | Exact design inventories, provenance closure, omission/tamper refusal, unqualified prediction diagnostics and strict migration |
| [test_campaign.py](../tests/test_campaign.py) | Real coordinator with scripted model replies: repair, fresh review, history, restricted write authority and accumulated empirical epochs |

The final implementation verification recorded 98 passing tests. That count is
historical, not a requirement that forbids adding tests. Scripted replies test
orchestration and data contracts; real subscription execution, model review
quality and actual prototype behavior need their own evidence.

## Checking documentation changes

Run the local checker:

```sh
python3 scripts/check_docs.py
```

The command above assumes the repository root as the shell working directory;
the script resolves documentation paths from its own location. It checks local
Markdown links and heading anchors, parses JSON/Python examples, checks shell
syntax with `bash -n`, and counts Mermaid blocks. It does not execute examples,
fetch external URLs, render diagrams or verify prose against implementation.
Run interface examples separately with their real validators and render changed
Mermaid diagrams before reviewing their meaning and readability.


Scope/3 execution extensions require matching focused capability validators in
`capabilities.py`. Include prerequisites and immutable verification dependencies;
unknown kinds fail closed even when execution is registered. The registry
agreement test catches missing descriptors. Preserve historical scope/1 and
scope/2 semantics and baseline/1 serialization when changing this interface.
See [descriptor extension requirements](coverage.md#capability-descriptors-for-new-handlers).

The opt-in bench transaction module owns pinned preparation, publication intents,
phase receipts and explicit inference attempt identities. `verify_records` checks
an explicit result projection; the public `verify` wrapper additionally holds a
shared lease and validates pending bench publications. Recovery can call the
projection validator only after proving the exact original evidence inventory
and sole intended event delta. It must not rehash an arbitrary tree through
`save_result` to make a stale projection pass. Transaction completion retains an
immutable terminal result snapshot so later empirical supersession can preserve
the earlier review identity without claiming its old acceptance is current.
