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
on `main`; the remote is `https://github.com/halbritt/agent-ee`, private.
Preserve unrelated work and use an isolated branch/worktree for changes. Do
not include credentials, host caches or undeclared source-project mutations.

## Development and checks

Use the system interpreter that imports pcbnew:

```sh
PYTHONPATH=src /usr/bin/python3 -m unittest discover -s tests -v
```

The recorded release has 63 tests. This is an observation at the recorded
implementation, not a permanent expected test count. Tests need the installed
native environment for their native/Bubblewrap paths; this repository does not
install those dependencies automatically.

| Test module | Protection |
| --- | --- |
| [test_candidate.py](../tests/test_candidate.py) | Captured identity, selected source changes, dependency changes and link restrictions |
| [test_checks.py](../tests/test_checks.py) | Native control/seed behavior, hierarchy, report parsing, passive modeling and simulation failure interpretation |
| [test_execution_failures.py](../tests/test_execution_failures.py) | Unavailable/failed/timeout subprocesses, sandbox boundary and invalid execution/review behavior |
| [test_reviewer_identity.py](../tests/test_reviewer_identity.py) | Exact role/candidate/run/packet binding and schema constraints |
| [test_findings.py](../tests/test_findings.py) | Singleton retention, unsupported allegations, evidence/dispositions, duplicate and closure invariants |
| [test_cli.py](../tests/test_cli.py) | Required work, outcome projection, interruption/budget preservation and correction behavior |

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
| `capture(scope_path, run_dir)` | Copy explicit selection, verify capture consistency, persist and return manifest |
| `verify_snapshot(run_dir)` | Verify exact copied-file set and candidate identity; return manifest or raise |
| `verify_source(run_dir)` | Also compare live scope and current selected source bytes |
| `execute(inputs, work, argv, timeout=120, stdin=None, network=False, extra_ro=())` | Isolated subprocess; persist execution/stdout/stderr/console; return execution record |
| `run_checks(run_dir, deadline)` | Ordered handlers; monotonic absolute deadline; persist each result |
| `build_packet(run_dir, checks, run_id)` | Construct/persist bounded packet with explicit omissions and identity |
| `initial_reviews(run_dir, packet, deadline)` | Return validated reviews plus per-role execution records |
| `validate_review(value, candidate_id, run_id, packet_sha256, run_dir)` | Exact schema/identity/citation validation and visible unsupported-defect downgrade |
| `collect_findings(reviews, checks)` | Preserve all claims and native check defects with namespaced IDs |
| `adjudicate(findings, decisions, run_dir)` | Return copied updated finding list; validate dispositions; CLI persists run-level events |
| `verify(run, current_source=True)` | Full CLI integrity/freshness check; return current projection |
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

Implement an explicit subscription adapter alongside the current `claude_call`
seam. A scope's `model` string alone does not choose another harness. Preserve
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
