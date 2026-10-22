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

## Comparing a retained board

The captured-geometry benchmark generates a 9 by 9 grid at four rotations for
each movable footprint. It measures fixed-peer and hypothetical all-other-peer
states separately, including native preparation, argument conversion and
release. Both consume the same immutable snapshots; snapshot creation and
library loading are outside these scorer timings. These queries do not assert
legal placements or reproduce a search trace. Batch throughput is separate
from the scalar API used by the lazy placer.

Run native work through the host's admission wrapper, with the matching KiCad
Python interpreter and `kicad-cli` on PATH. Every output directory must be new:

```sh
native-admit run --label scorer-replay -- python3 scripts/telemetry_scorer_replay.py \
  --capture "$CAPTURE" --policy "$POLICY" --library "$SCORER_LIBRARY" \
  --out build/scoring/captured-replay --runs 5
native-admit run --label scorer-pod-pair -- python3 scripts/telemetry_scorer_compare.py \
  --board "$BOARD" --netlist "$NETLIST" --request "$REQUEST" \
  --library "$SCORER_LIBRARY" --out build/scoring/pod-pair-python-first
```

The second command runs the complete `place_candidate` pipeline, capped at
900 seconds per arm. It includes library loading, arrangement, residual
placement, saving and native checks; both arms skip visual rendering. Repeat
with a fresh output directory and `--rust-first` to reverse the order.
It preserves the supplied policy and changes only the authorized scratch
root and the rendering flag. A matched refusal exits 2 and cannot establish
placement speedup. A one-shot probe records whether Rust actually scored a
pose; it removes itself before the first native call.

Raw evidence remains unchanged. Comparison verifies the original board, plan
and provenance hash links before normalizing arrangement elapsed time and
its dependent hashes, native DRC dates and temporary staging paths. Candidate
board bytes must match exactly. Native clearance findings can arrive in a
different order: comparison sorts only their subsequences in named native
finding lists, preserving all fields, duplicates, other finding positions and
nested item order. Source manifests support explicitly pinned
archives on disposable hosts, including verification of all listed files;
ordinary checkouts record their observed Git revision and module hashes.

## Telemetry pod comparison on Spot, 2026-10-22

The full retained telemetry-pod workflow showed no meaningful speedup. Two
matched pairs used the unchanged historical `iter8n` request, including its
18 admitted blocks, with the run order reversed for the second pair:

| Run order | Python runner | Rust runner |
| --- | ---: | ---: |
| Python, then Rust | 94.3468 s | 93.7740 s |
| Rust, then Python | 94.2394 s | 94.3401 s |
| Mean | 94.2931 s | 94.0570 s |

The observed mean reduction is 0.25%, with Rust slightly slower in the reverse
pair. Two observations per backend do not establish a reliable improvement.
These times cover `place_candidate`, including arrangement, residual packing,
board export and native validation. Both arms skip visual rendering. There
was no dedicated full-runner warmup.

All four runs produced byte-identical candidate boards and the same complete
309-placement plan, apart from recorded arrangement elapsed time: 4,277 steps,
of which 101 were arrangement steps. Both Rust runs demonstrably invoked the
native scorer and recorded no fallback. All eight retained baseline/final-candidate
DRC execution records report success. All four resulting boards retained outcome
`defect`; parity does not establish board acceptance.

The original comparators reported differences from native DRC clearance
finding order. Independent review verified that the complete records, nested
item order and multiplicities were equal. Separate comparison reports apply
the narrow ordering normalization described above to `baseline/native-drc.json`,
`native-drc.json`, `pre-artwork-drc.json` and the named DRC comparison lists.
All 32 retained artifacts per pair then compare equal. The original failed
comparison manifests and all raw evidence remain unchanged.

The current sealed `j6b2` emission could not provide a full-placement timing:
both arms refused fixed C42 before any search step or scorer invocation. Its
captured origin is outside the 90 by 65 mm board. An earlier historical
`iter8d` request also failed before scoring because its C31 block identity
predates the current canonical courtyard format. No constraints or block
identities were changed to make either input pass.

Generated scorer queries on the current `j6b2` geometry did show a benefit
when many peers contribute. Each state covers 95,904 queries, with five
alternating repetitions after one warmup per arm. Every score matched exactly.

| Peer state | Prepared Python median | Rust scalar median | Rust batch median |
| --- | ---: | ---: | ---: |
| Fixed peers | 91.640 ms | 164.747 ms (0.56×) | 88.192 ms (1.04×) |
| Hypothetical all-other peers | 489.551 ms | 191.354 ms (2.56×) | 112.841 ms (4.34×) |

The scalar column uses the API integrated into lazy placement; batch is a
separate throughput measurement. Python consumes the same prepared immutable
snapshots. Timings include native preparation, validation, argument conversion,
result materialization and release, but exclude snapshot creation and library
loading. The dense state includes captured unplaced origins: 300 of 309 are
outside the board bounds. The grid queries are not a legal-placement search
trace. In the fixed-peer state, 166 of 296 sources have no matching peers and
the scalar adapter is 1.80 times slower than the prepared Python reference.

Measurements used source `e015e053efda2325d81e8b3eb7670dd614515249` on an
isolated GCP `c2d-highcpu-4` Spot instance: four vCPUs, 8 GiB RAM, AMD EPYC
7B13, Python 3.12.3 and KiCad 10.0.7. The existing VM image was
`agent-ee-gate-20261022-0352`, with runtime image `agent-ee:ci-14ef7275`.
Rust 1.99.1 compiled the library for that CPU. Native admission serialized the
runs; the container had no CPU quota. Source and input hashes were verified
before and after each pair. The 18 scorer tests also passed against the cloud
library without skips. The VM and its boot disk were deleted after downloading
and verifying the evidence.

[Telemetry measurements, source pins and artifact hashes](benchmarks/telemetry-scorer-2026-10-22.json)
retain both successful pairs, both rejected inputs, raw scorer samples,
comparison rules and teardown evidence. The implementation remains opt-in.
