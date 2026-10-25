# AGENTEE-528 fixed-mark identity repair — agent-567

Candidate based on dispatched `c6446c99b0d53617079328228a73508c73d3d03c`.
Chief agent-564 owns independent review, integration and issue closure. The
repair changes Rust's local native-reference lookup; capture, UUID/alias
derivation, native writing, geometry rules and routing remain unchanged.

## Traced consumers and selected behavior

| Boundary | Identity and behavior |
| --- | --- |
| `job::aliases` | Full native UUID uniqueness and deterministic UUID-order solver aliases. Unchanged. |
| `job::prepare` footprint rows | All footprint rows retain full UUID identity. Repeated `native_ref` is admitted only when every holder is a `fixed_copper_mark`. Component/component and component/mark collisions still abort. |
| Group anchor/members | Native-reference selectors must resolve to one physical footprint. Repeated mark labels refuse explicitly. |
| Proximity `ref`/`near` | Same cardinality resolver, before pad-UUID selection. |
| Locality `from`/`to` | Same cardinality resolver before captured native-pad/centroid lookup. A pad UUID does not bypass an ambiguous footprint reference. |
| Evaluation, pose output and native materialization | Full UUID addressing. Exact UUID coverage and protected position/rotation/side/moved-flag checks remain unchanged. |

Codebase Memory call/impact leads were checked against source with `rg` and
reads of `job.rs`, `locality.rs` and `placement_job.py`. All three name-selector
families consume the same map inside `prepare`; they do not reach the native
writer as names. The local lookup now retains every matching fixed-mark index
and refuses ambiguous selection. It introduces no alias/UUID selector syntax
or synthetic native reference. The documented capture locator `Ref*~2` still
resolves by base label plus exact native pose. A last-match lookup or deleting
marks would violate the retained identity/obstacle contract and was not selected.

## Verification and retained evidence

Local evidence, outside the disposable worktree:
`/home/halbritt/.cache/agent-evidence/agent-ee-528-agent-567/`.
`selected-result.json`, `native-artifacts/retained-inputs.json` and
`native-artifacts/real-control/summary.json` bind source, input and result hashes.

The established runtime was rebuilt as `agent-ee:exec-af30a0917f4a62f5`, image
`sha256:939f4bfb6bb45eaff5fc47d27b94f905ec979e31ec66a190fe6fb57ff96753e1`.
Its installed Rust binary SHA-256 is
`c073d457c89be2a608abf7bc10ab4a7972466381f022c005d77849ea73877d61`;
`scripts/build_placement_engine.py verify` passed against current source.
The full source/binary receipt is retained as `native-artifacts/planner-receipt.json`.

- The new positive regression failed before repair with `duplicate physical reference FID`.
- All 26 focused Rust job tests passed. The rebuilt image passed all 106 Rust tests, with none ignored.
- Nine focused native tests passed with `PYTHONPATH=src:tests python3 -m unittest test_placement_job.CaptureTests test_placement_job.NativeTransformTests -v` inside `ae-ci-exec`.
- The exact retained 21-object esp_prog problem passed real Rust `evaluate` mode with all UUIDs present and all aliases distinct. This mode calls the same `prepare` before its diagnostic branch; no real-article search ran.
- One diagnostic native save/reload preserved both original mark UUIDs ending `5d8c55cd` and `5d8c55f6`, their references/poses/sides/locks, and exact captured copper/mask geometry. Both copies remain present.
- Seventeen retained-input controls covered evaluation, duplicate actual-component reference, duplicate UUID, ambiguous group selector, protected Rust poses and ten native pose mutations. Focused Rust tests additionally exercised both proximity and locality selector directions. Every negative refused as expected.
- All six retained input copies matched their pre-control hashes afterward; all 204 original files also matched the frozen manifest. No input board was renamed, edited or deleted.

A first locality negative had an invalid integer centroid fixture; it was
corrected to the existing rational-pair representation. That obsolete container
snapshot failed its Rust test gate; its automatic identical build retries were
stopped. The first native unittest invocation omitted the `tests` helper path
and failed import. Corrected commands and both earlier failures are retained;
no failure was converted into a production fallback or skipped test.

## Limits

Initial piled placement remains infeasible in diagnostic evaluation. This
qualifies the parser/identity/native preservation boundary, not placement or
routing success. The unchanged public rich intent still refuses at
`intent.assembly.sides: side selection is unsupported` under AGENTEE-520.
No source filtering or stripped public input was used to bypass that refusal;
the diagnostic control consumes the explicitly authorized retained problem.
Full placement/routing, grouping compatibility, M1 release and hardware/supplier
qualification remain separate work. No full strict Python suite was run.
