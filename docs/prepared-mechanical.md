# Prepared mechanical grading (AGENTEE-511)

Stage A prepares the selected mechanical binding once per block/face problem,
then shares that qualification across initial packing, recovery, and compaction.
The source document, native owners, selection, complete initial capture, and side
state form the prepared identity. The snapshot has no mutable aliases to its
caller. Changed bindings or source inventories require preparation again.
Compaction may change the search outline; it cannot change another source
capture field when reusing preparation. Trial poses still undergo the existing
native-owner and pad-transform checks.

Python's `mechanical_reservations.prepare` returns `PreparedMechanical`;
`materialize` and `grade` accept either that object or the original binding.
Raw bindings retain their validation and refusal path. Rust's
`mechanical::PreparedMechanical` owns its JSON snapshot and qualified owners,
selected entries, and phases. Packing and arrangement searches reuse it in
candidate loops. Serialized policies, candidate schemas, grades, ordering,
budgets, and legacy-first search remain unchanged.

`LocalMechanical` reuses only exact courtyard/reservation and reservation/pair
contributions. It enumerates **all** potential pairs each time. An unchanged
input pair reuses its contribution; changed owners, sides, shapes, or newly
nearby members re-evaluate every affected pair. Materialization, native-owner
verification, and capture qualification conservatively run in full. Access,
RF, copper, electrical-locality, and group predicates retain their existing
authority; this cache does not replace or skip any of them.

Every placement commit and rollback compares the local result with the full
grade. `AGENT_EE_ASSERT_MECHANICAL=1` also compares **every** mechanical trial,
including rejected trials, against a fresh raw-binding grade. A stale placement
generation raises an error; rollback clears the contribution cache. Assert mode
is a correctness diagnostic with extra full-grading cost, not a benchmark mode.

Non-empty regressions include the actual JP1–JP5 custom-pad capture and its ten
access rows, native relocation and side changes, a newly introduced collision,
missing pads, unresolved coverage, and mutated bindings. Finite heights in these
frame witnesses are test declarations; they do not qualify production iron
clearance. Real ADC candidate parity and matched measurements are separate
retained delivery evidence. This slice does not complete C1 or W2.

## Bounded KRT v0.23.7 compatibility comparison

The read-only comparison used upstream
[`py_placer/placement/simple.py`](https://github.com/drandyhaas/KiCadRoutingTools/blob/v0.23.7/py_placer/placement/simple.py)
(`Model.touched`, `local`, `commit`, `snapshot`, `restore`, and `total`) and
[`simple_anneal.py`](https://github.com/drandyhaas/KiCadRoutingTools/blob/v0.23.7/py_placer/placement/simple_anneal.py).
This maps representations; it does not establish equivalent acceptance or better
pod performance. No upstream code is imported in this slice.

| Existing hard constraint | Relevant KRT representation | Compatibility boundary |
| --- | --- | --- |
| Mechanical reservation/member and reservation/pair intersections | Per-part indices, touched dependencies, local before/after contributions | Port the dependency/commit discipline; retain exact polygons, native owners, phases, required/advisory distinctions, and unknowns. New contacts require new-neighbour coverage. |
| Electrical locality | Net membership, claims, decaps, template and explicit-limit dependency maps in `touched` | The dependency index is reusable as a concept. Weighted costs and floating-point limits cannot replace exact source-bound hard predicates. |
| Native side and pad state | Live poses, committed spatial indices, snapshot/restore | Side changes invalidate both-face geometry and owner/pad dependencies. Native flip qualification, custom-pad restrictions, UUIDs, capture identity, and source refusals remain ours. |
| Owner access and RF reservations | Keep-outs, claims, zones, local overlap/halo pricing | Overlap penalties and permitted intermediate violations cannot replace hard admission. RF/access semantics and uncertain coverage need explicit hard constraints or full recomputation. |
| Commit/rollback and full reference | `commit` updates part/rectangle/net indices; `restore` resets pending/net state; `total` resynchronizes and fully prices | Port the lifecycle idea only after differential checks on accepted/rejected moves, side changes, and stale generations. Preserve exact grades and deterministic order; adopt no Metropolis or seed-selection policy. |

The tag's [MIT license](https://github.com/drandyhaas/KiCadRoutingTools/blob/v0.23.7/LICENSE)
names copyright (c) 2026 drandyhaas and requires retaining its copyright and
permission notice with copies or substantial portions. A future code port must
carry that notice and attribution. This note imports no source, dependencies,
search policy, or solver. It does not establish that porting outperforms adoption;
that needs a compatible adapter and a separate matched benchmark.
