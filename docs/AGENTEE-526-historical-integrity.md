# AGENTEE-526 historical-integrity preparation

Agent-675, 2026-10-27. Inspected base: `64da514a09ca7f5b152ed78a594ff4d68120847e`.
This delivery contains characterization tests and retained-copy observations.
AGENTEE-526, KICADROUTE-102 and M1 remain open. Telemetry-pod remains held.
Coordinator agent-669 owns production allocation and serial integration after
independent review. No production file is changed by this delivery.

## Existing boundaries

[run_record.verify_records](../src/agent_ee/run_record.py) selects
`candidate.verify_snapshot` when `current_source=False`, but still compares
current policy and runtime fingerprints. Both required-policy and unrelated
module identity changes therefore refuse snapshot review verification.
[The CLI](../src/agent_ee/cli.py) maps `verify --snapshot-only` to this argument
and prints `fresh` followed by the stored outcome on success. That flag is not
an implemented non-accepting historical-integrity mode.

[retained_run.snapshot](../src/agent_ee/retained_run.py) already checks the
captured source inventory, completed stage identities and final candidate
selection without observing today's policy or native runtime.
`verify_retained` adds captured-plan and judgment-inventory custody checks.
Neither constitutes present-day release qualification. The original acquisition
v7 has no captured `build-plan.json`; `verify_retained` returns `None` for it.
Its historical `accepted:true` field is retained unchanged and does not acquire
current authority through these reads.

[kicad_route_attempt.verify_state](../src/agent_ee/kicad_route_attempt.py)
already compares installed source with the pinned checkout's `engine.SOURCE_ROOT`,
checks the installation record's source/module identities and recomputes manifest
drift. [Existing generation tests](../tests/test_kicad_route_generations.py)
cover an internally healthy stale generation being refused against that source.
Router `statemanifest.verify` itself compares installed files with their own
manifest; it takes no explicit expected-release identity argument at inspected
router commit `c4af095ed07593b63d7886c139fcb7e5c2e774f4`.
These are different contracts. The agent-ee adapter implements part of the
KICADROUTE-102 preservation requirement; that does not complete the router's
public expected-source/binary/schema interface.

## Reproductions

Durable private evidence directory:
`/home/halbritt/.local/share/agent-ee/fleet-20261027-agent669/lane-526/`.
`probe.py`, `observations.json`, `probe.log`, `native-admission-events.json`
and `native-execution.json` retain the method, inputs, results and runtime.
`acquisition-v7/` preserves an unmodified copy. `router-stale-copy/` preserves
the constructed stale generation; original router metadata is retained separately.
No retained design or binary is added to the repository.

| Observation | Actual result |
| --- | --- |
| Unchanged acquisition v7 | Seven stage identities and source custody verify; current acceptance is not assessed. |
| Current runtime versus acquisition history | Python, KiCad CLI, pcbnew binary and native version fields match; policy source inventory differs. |
| Copied `viewer.py` identity edit | Snapshot review verification refuses `review implementation changed`; retained build custody remains unchanged. |
| Copied `routing_rules.py` identity edit | Same refusal; retained build custody remains unchanged. |
| Retained `source/requirement-map.json` tamper | Refuses `retained build source identity changed`; completed stage bytes themselves remain unchanged. |
| Final package candidate board tamper | Refuses `completed stage identity changed: package`; only the package stage tree differs. |
| Package review drill-map artifact tamper | Same package-stage refusal; only the package stage tree differs. |
| Exact installed router generation derived from qualified runtime | Manifest drift and adapter qualification problems are both empty. |
| Copied router extension bytes changed | Both manifest integrity and adapter qualification report `krt.extension_module.sha256` drift. |
| Constructed stale generation with matching source metadata | Manifest drift is empty; adapter refuses source expectation and installation record qualification. |

The two copied policy edits append comments. They demonstrate identity drift,
not altered policy semantics. Acquisition's old review already differs from
the base policy, so these exact-copy probes cannot identify which current source
changes should invalidate which stages. The isolated tests below provide a
matching baseline for the policy-drift distinction.

The stale router case changes the copied `VERSION`, recomputes its source digest
and updates copied manifest/installation metadata, following the existing fixture
method. Its old commit label is synthetic. Nothing is rebuilt, and this is not a
claim that the changed source produced the retained binary. It demonstrates the
observable difference between self-integrity and a frozen source expectation.

All acquisition tamper cases restore original bytes. Final custody and report
digest are checked after restoration. Changed stage trees are observations of
retained bytes, not an implementation of transitive dependency invalidation.
The original, retained and tested acquisition copies have identical complete
5,674-file byte inventories; `preservation.json` binds `acquisition-inventory.json`.

Selected identities:

- Acquisition report: `6451b3f3a27c8fe494e27e707a1f43eb837c67688e2dd0009c684183e8d5a109`.
- Final acquisition board: `d0a898a2a254c090ba1a4185320938241c255e79c587c82abb27a0bcafcd66e7`.
- Frozen router source: `f031a238cc5b85d99ea431de7ea28fcd825753aa2303d865a7a433ca8036e0c4`.
- Installed extension: `76436e899bb85bf40785df862f5e41438376ff221a767dea0e5a249bc5c97087`.
- Native image: `sha256:63cb53f57f8ca334f7bb202564247c20fa9ff3799aef052840e109733ebbe4bb`.
- Successful probe admission lease: `ec7996c1b0c0`; container
  `33d2ea89746a78254f208e608d11aa7c20ce6c3f19df2b861848c812888231d9` exited zero.

Docker event history binds the probe container to that lease and image tag;
the retained image inspection records the image ID and build-input digest.

## Tests and limits

[test_historical_integrity_526.py](../tests/test_historical_integrity_526.py)
uses real captured files and the existing public verification API, with supplied
filesystem/runtime observations and no internal mocks. Five tests cover unchanged
incomplete history, changed live requirements, unrelated/required policy drift,
runtime replacement and retained input/artifact tampering. The policy test
explicitly characterizes today's defect; it must be reconciled when the new
historical-integrity contract is implemented.

Run from the assigned worktree:

```sh
AE_CI_MAX_WAIT=300 ae-ci-exec . -- python3 scripts/strict_suite.py --jobs 1 \
  --summary build/lane526/final-summary.txt test_historical_integrity_526
```

Result: five tests pass, zero skips. The earlier single-test fixture probe also
passed. The retained-copy experiment ran through `ae-ci-exec` with one worker.
Its first attempt failed before observations because the standalone script lacked
the repository import path; `probe-import-failure.log` retains that failure.
The corrected attempt completed all stated assertions and exited zero.
No routing producer, new acquisition build or full suite was run.

## Proposed production allocation

The coordinator received this proposal before any production edit:

1. Own `run_record.verify_records` and an explicit public historical-verification
   boundary. Reuse retained snapshot/artifact/lineage checks. Return an explicit
   non-accepting historical result without treating the stored outcome as current
   acceptance. Keep current verification the default. Simply skipping the two
   fingerprint comparisons is insufficient: later verification recomputes current
   RF, purpose, baseline and outcome semantics too.
2. Assign the reserved `cli.py` consumer separately so historical success is
   visibly integrity-only. Check `diagnostics`, `evidence_index`, `campaign`,
   `bench_transactions`, `retained_run` and other callers before changing existing
   `current_source=False` behavior; it is already used beyond the CLI.
3. Keep `retained_run.snapshot` and `verify_retained` as the existing build-custody
   readers. Change their presentation only if the explicit historical API needs it.
4. Reconcile current expectation declarations before assigning a release manifest.
   `build_workflow._runtime` includes all `policy_files`; the routing adapter owns
   the pinned wrapper/source map and generation attestation; `dc_path.TOOLS`
   supplies model-specific native expectations. A source-to-stage consumer map
   and tests for omitted required files are prerequisites to narrowing those sets.

No duplicate acceptance engine or new manifest is proposed in this slice. Current
stage invalidation, a non-accepting historical public mode, the router's exported
expectation contract and manifest consolidation remain acceptance work. Argon
review and coordinator integration remain required for this delivery.

## Doctrine scope

Validated release `d3e0c0d4ccd1920b2e045c156f1cf0db4fc5f04f` supplied
packet `pkt-2520cd3cb410a818` (corpus `corpus-2026-07-12-a11702cc9217`).
Packet content SHA-256:
`2520cd3cb410a8188de7e9afcaa5208458606544629a8a3a590473744efc3771`;
doctrine `doctrine-f6bbb5196a3f8bf9`, retriever `retriever-ec995ecdd083b2c8`.
The retained packet and four typed evidence records support characterization and
preserving authority boundaries. Material missing inputs for a production
recommendation include the accepted historical API contract, complete consumer
dependencies and permission to change reserved files; this slice abstains from
production implementation. Other packet obligations concerning ingest, dedup,
concurrency, text codecs and optimization are outside the delivered test change.
`doctrine-obligations.json` records each unmet obligation and its classification.
