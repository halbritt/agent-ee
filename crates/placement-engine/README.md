# Rust placement planner

`agent-ee-placement` is a standalone implementation of the complete pure planning
operation behind `placer_runner._plan_local`. It handles capture qualification,
policy and provenance checks, supplied block admission and arrangement, residual
packing, targeted placement and selection, exact geometry, electrical locality,
candidate ranking, search budgets, feasibility proofs, and final plan grading.
It does not invoke Python or another planner. For block requests, the caller still
uses Python `blocks.pack_block` to produce Stage-A candidates before this boundary.
Rust admits those supplied candidates, arranges them in Stage B, and packs the
residual parts. The end-to-end pipeline therefore still includes Python Stage A,
KiCad capture, board writing and validation.

The existing runner still uses KiCad to capture a board, apply the proposed
placements, and independently validate the resulting board. Those operations
remain inside the common end-to-end benchmark for every implementation.

The CI image builds and tests the planner using pinned Rust 1.99.1 and the
committed Cargo.lock. Dependencies are fetched with `--locked`; compilation and
unit tests run with `--frozen` and networking disabled. The installed binary and
build receipt live under `/usr/local/libexec/agent-ee/`. See
[installation](../../docs/installation.md) for a local build.

The committed `test_placement_engine_parity` regression suite compares complete
plans and refusal diagnostics against the independent Python planner. It also
checks real telemetry-pod inputs recovered from the pinned fixture object store.
Numeric types must match; only block-arrangement elapsed time is omitted.
Expected results stay outside the Rust process.

The executable reads one JSON object from stdin with schema
`agent-ee.rust-planning-input/1`, `facts`, `request`, optional effective `policy`,
and optional `netlist_xml`. It writes schema `agent-ee.rust-planning-output/1`
containing either `plan` (exit 0) or `error` (exit 1). A search refusal is a plan,
not a process failure. Native XML is carried verbatim for identity checks.
With `--events`, newline-delimited progress records precede the final result.
The Python adapter forwards those records and preserves ordinary validation
errors as `ValueError`; transport failures remain explicit process errors.

Normal `placer_runner` requests use this executable. Discovery checks an explicit
`AGENT_EE_RUST_PLANNER` override, the installed location, then `agent-ee-placement`
on PATH. An invalid override fails without selecting another binary. No build or
Python fallback occurs during planning. The fixed installed location works in
the production sandbox, which clears the caller environment and mounts `/usr`.

For explicit reference execution, use `placement_runtime.python_reference()`;
`placer_runner --trace` selects that reference for Python tracing. Development
callers can select a built binary with `placement_rust_runtime.using(binary_path)`.
The adapter forwards native heartbeats, records executable/input hashes and
process errors, and reaps its owned child on interruption.

The [matched benchmark](../../docs/benchmarks/scorer-results-2026-10-22.md) is
historical evidence at source commit `0995743e`. Its four-way comparison tools
remain at result commit `6f48b9f2`; C, scalar-Rust and PyPy execution paths are
retired from the shipping tree. Those measurements are not a new benchmark of
later integration changes.

Coordinates and costs retain arbitrary precision. Common candidate-queue integers
use an inline representation; checked machine-integer geometric predicates fall
back to exact arithmetic when needed. Geometry is prepared once per pose, broad
phase bounds reject disjoint shapes, and the candidate heap resolves exact wire
costs lazily while preserving the reference ordering and step accounting.
