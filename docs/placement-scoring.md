# Rust placement scorer

The optional Rust scorer computes the same integer Manhattan cost as
`placer._wire_cost`: each physical signal pad contributes its distance to the
nearest same-net peer. Missing peer nets contribute zero; duplicate physical
pads contribute separately. The existing `_pad_nets` transformation supplies
the four rotations, including offsets from a captured nonzero rotation.

This ports the kernel from the unmerged AGENTEE-431 C experiment
(`50e586818acf6265039a3509acc910eb0158f357`). That experiment ran the full Python
oracle for every native batch and submitted single-pose batches from placement.
Its reported checked scoring and placement timings were slower than Python.
The Rust implementation prepares state once per generation and exposes a
scalar call for the lazy ranker. Full Python checking remains an explicit mode.

## Build and use

The build has no Cargo dependencies. It requires `rustc` and the system linker;
the development build was tested with Rust 1.99.1. Compilation never happens
on import or implicitly during placement.

```sh
mkdir -p build/scoring
PYTHONPATH=src python3 -m agent_ee.placement_scoring build/scoring/placement_score.so
```

To tune code generation for the machine that will run the library, append
`--native-cpu`. That build may require CPU instructions unavailable on another
host. The default build and container library use the compiler's portable
target. Both use optimization level 3, whole-program LTO and one codegen unit.

```python
from agent_ee import placer, placement_scoring

with placement_scoring.experimental("build/scoring/placement_score.so") as session:
    plan = placer.pack(capture, policy)
if session.events:
    print(session.events)  # The session fell back to Python; retain the reason.
```

Use `experimental(path, check=True)` to compare every native result with the
complete Python oracle. Fast mode does not recompute successful native results
in Python. Ordinary `pack()` calls use Python; the presence of the shared
library or `AGENT_EE_PLACEMENT_SCORE_LIBRARY` does not activate native scoring.
That environment variable selects a prebuilt library for tests and benchmarks.

The container builds the library with its existing pinned Rust build stage and
copies only the shared library into the runtime image. It adds no runtime
compiler or Python dependency.

## Hot path and exactness

Prepared Rust state owns translated peer coordinates (`peer - pad offset`) in
separate contiguous x/y arrays. Each scalar call reads that immutable state
without allocating. Scalar FFI calls retain the GIL to avoid a release/reacquire
cycle around a short kernel; batch calls release it. LLVM can vectorize the
inner nearest-peer reduction for the selected target CPU.

For `p` contributing pads, the ordinary integer path uses
`B = floor(i64::MAX / (4*p))`. When every translated coordinate and the query
lie in `[-B, B]`, each Manhattan distance is at most `4*B` and the total fits
signed 64 bits. This allows ordinary arithmetic in the inner loop. Otherwise,
the kernel uses signed 128-bit arithmetic. Scores exceeding signed 64 bits, or
inputs outside the ABI's signed 64-bit domain, fall back to Python's unbounded
integers. A failed batch is discarded in full.

The Python adapter validates array shapes, coordinates, rotations and the ABI
version before crossing the FFI boundary. Prepared allocations are freed when
their last Python owner is released. They have no explicit close operation
that could race a batch reader. The raw ABI requires valid aligned buffers
and live handles; it is not an interface for untrusted native pointers.

The placer rebuilds a scorer when `(generations_allocated, stack_generation)`
changes, including a suspended scan resumed after a push and pop. Candidate
bounds, exact ranking keys, lazy evaluation order, per-candidate checkpoints,
geometry predicates and the Stage-B objective retain their existing code.

A missing/incompatible library, numerical refusal or checked-mode mismatch
records a reason in `session.events` and selects Python for the rest of that
session. The benchmark treats any fallback as a failure, so timings cannot
silently measure Python under a Rust label.

## Validation and measurement

```sh
rustc --edition=2021 --test -C opt-level=3 -C overflow-checks=yes \
  src/agent_ee/data/placement_score.rs -o build/scoring/kernel-tests
build/scoring/kernel-tests
PYTHONPATH=src:tests:. python3 -m unittest tests.test_placement_scoring
PYTHONPATH=src:tests:. python3 scripts/placement_scoring_benchmark.py \
  --library build/scoring/placement_score.so --out build/scoring/measurement --runs 5
```

The Python tests exercise scores, complete ordered ranks, lazy trace payloads,
backtracking, overflow, lifetime and fallback. Native tests additionally check
both generated pod-scale fixture ranks beyond trace sampling limits and saved
board/DRC/receipt parity. Native toolchain requirements follow the existing
test-suite conventions.

The benchmark runs one warmup and alternating matched repetitions, checks
exact outputs outside timing, and retains raw samples, hashes and host/load
metadata. It reports prepared batch/scalar replay, full-oracle checking and
complete untraced `pack()` separately. Replay includes native preparation and
Python conversion/release costs. Library loading is excluded from replay and
included in placement; compilation is outside all measured arms.

`--no-native` runs only the three small unit fixtures. `--capture CAPTURE.json
--policy POLICY.json` measures a supplied captured board instead of generated
fixtures. Those options call `pack()`; they do not reproduce a whole
`placer_runner` pipeline with block arrangement, board export and native
validation. A supplied capture is not automatically a current target board.
Trace replay measures only retained samples when the trace is capped.

Report throughput improvements at the measured boundary. Fixture speedups,
shared-host diagnostics and scorer replay do not establish a current-board
placement or routing improvement. The command's successful exit asserts
parity and absence of fallback; it does not change the default backend.

## Measured results, 2026-10-22

Source `3cd1a5e2f52937d37f5f4ab5fdd999f12d862343`, Rust 1.99.1 / LLVM
23.1.1, `--native-cpu`, Python 3.12.3, Intel i5-11400F on proximal.
These are five alternating matched runs after one warmup, through
`native-admit`. The host was shared: load averages began at 24.05 and ended
at 21.60 on 12 logical CPUs. Treat the numbers as this session's observations.

| Fixture | Python pack median | Rust pack median | Placement speedup | Rust scalar replay speedup |
| --- | ---: | ---: | ---: | ---: |
| search-promotion | 2.169 ms | 2.426 ms | 0.89× | 0.15× |
| search-anchor | 2.334 ms | 2.848 ms | 0.82× | 0.18× |
| cluster-three-cap | 117.543 ms | 115.056 ms | 1.02× | 0.49× |
| pod-80x50-90 | 1,851.542 ms | 918.723 ms | 2.02× | 2.76× |
| pod-85x55-112 | 4,104.536 ms | 1,512.478 ms | 2.71× | 1.45× |

Every measured score and plan matched exactly, with no fallback. Setup costs
outweigh native scoring on the tiny requests. The larger fixtures improved on
every matched placement pair: wall time fell 43.6–56.7% for the 90-footprint
fixture and 57.2–69.1% for the 112-footprint fixture. Full Python verification
remained slower than ordinary Python placement on both larger fixtures.

[Raw samples and source/input/library hashes](benchmarks/placement-scoring-2026-10-22.json)
retain the measured boundaries and host metadata. Both pod cases are generated
fixtures; this measurement does not establish full-placement speed on the
current telemetry pod. No default-backend change follows from this report.

Verification covered 73 distinct Python tests across the scorer, benchmark,
native scorer/writer, geometry cost, search and placement trace, plus five Rust
tests with overflow checks enabled. Complete generated rank sequences on both
pod fixtures were compared beyond the trace sample cap. Saved board, DRC and
receipt parity passed. Documentation and file-origin checks passed. The Docker
recipe was reviewed but its image was not rebuilt in this worktree.
