# Rust placement planner

`agent-ee-placement` is a standalone implementation of the complete pure planning
operation behind `placer_runner._plan_local`. It handles capture qualification,
policy and provenance checks, supplied block admission and arrangement, residual
packing, targeted placement and selection, exact geometry, electrical locality,
candidate ranking, search budgets, feasibility proofs, and final plan grading.
It does not invoke Python or another planner.

The existing runner still uses KiCad to capture a board, apply the proposed
placements, and independently validate the resulting board. Those operations
remain inside the common end-to-end benchmark for every implementation.

Build and check from the repository root:

```sh
native-admit run --weight 1 --class test --label rust-planner-build -- \
  cargo build --release --locked --manifest-path crates/placement-engine/Cargo.toml

native-admit run --weight 1 --class test --label rust-planner-unit -- \
  cargo test --locked --manifest-path crates/placement-engine/Cargo.toml

native-admit run --weight 1 --class test --label rust-planner-parity -- \
  python3 scripts/placement_engine_parity.py build/rust-planner/parity \
  --generate --suite core --suite proof --suite geometry --suite block \
  --binary crates/placement-engine/target/release/agent-ee-placement
```

The parity directory must be new. The driver first freezes Python results and
input hashes, then gives the Rust process only its input. It compares complete
plans, including numeric types and refusal diagnostics; only arrangement elapsed
time is omitted. The oracle is outside the native implementation and benchmark
timer.

The executable reads one JSON object from stdin with schema
`agent-ee.rust-planning-input/1`, `facts`, `request`, optional effective `policy`,
and optional `netlist_xml`. It writes schema `agent-ee.rust-planning-output/1`
containing either `plan` (exit 0) or `error` (exit 1). A search refusal is a plan,
not a process failure. Native XML is carried verbatim for identity checks.
With `--events`, newline-delimited progress records precede the final result.
The Python adapter forwards those records and preserves ordinary validation
errors as `ValueError`; transport failures remain explicit process errors.

Opt in through `placement_rust_runtime.experimental(binary_path)` around the
existing runner, or pass `--rust-planner` to
`scripts/telemetry_scorer_four_way.py`. The process adapter records executable
and input hashes, startup and IPC time, and failures. It has no Python fallback.

Coordinates and costs retain arbitrary precision. Common candidate-queue integers
use an inline representation; checked machine-integer geometric predicates fall
back to exact arithmetic when needed. Geometry is prepared once per pose, broad
phase bounds reject disjoint shapes, and the candidate heap resolves exact wire
costs lazily while preserving the reference ordering and step accounting.
