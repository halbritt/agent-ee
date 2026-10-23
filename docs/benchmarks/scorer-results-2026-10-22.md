# agent-ee: complete CPython, Rust and PyPy benchmark results

Measured 2026-10-22. This replaces the earlier abbreviated paste and includes the original CPython/Rust fixtures, scalar and batch scorer benchmarks, full telemetry runs, and PyPy measurements. Lower time is better; speedup = CPython time / candidate time, so values below 1x mean a slowdown.

## 1. Original CPython versus Rust: complete fixture packing

Median of five alternating runs after one warmup. Complete untraced `pack()` calls, not the full KiCad workflow. Shared Intel i5-11400F host, 12 logical CPUs, load approximately 21–24; Python 3.12.3, Rust 1.99.1 / LLVM 23.1.1, native-CPU build. These measurements must not be compared directly with the later AMD Spot timings.

| Fixture | CPython pack (ms) | Rust pack (ms) | Rust + full Python check (ms) | Rust speedup | Result |
| --- | --- | --- | --- | --- | --- |
| search-promotion | 2.169 | 2.426 | 2.484 | 0.89x | planned |
| search-anchor | 2.334 | 2.848 | 2.741 | 0.82x | planned |
| cluster-three-cap | 117.543 | 115.056 | 119.158 | 1.02x | packing_budget_exhausted |
| pod-80x50-90 | 1,851.542 | 918.723 | 2,383.532 | 2.02x | planned |
| pod-85x55-112 | 4,104.536 | 1,512.478 | 4,613.331 | 2.71x | planned |

All plans matched exactly, with no Rust fallback. `cluster-three-cap` measures the same budget-exhaustion result in both implementations. The two pod fixtures are generated cases, not the actual telemetry pod.

## 2. Original CPython versus Rust: fixture scorer replay

Same five-run fixture experiment. Replay includes native preparation, Python conversion and native-state release; library loading is excluded. Python evaluates the same pre-captured snapshots. The pod fixtures replay 8,192 retained queries each, a capped subset of their search traces. These are total replay times, not per-query latencies.

| Fixture | Queries | CPython (ms) | Rust scalar (ms) | Rust batch (ms) | Rust batch + full Python check (ms) | Scalar speedup |
| --- | --- | --- | --- | --- | --- | --- |
| search-promotion | 3 | 0.008 | 0.056 | 0.059 | 0.079 | 0.15x |
| search-anchor | 6 | 0.011 | 0.065 | 0.063 | 0.086 | 0.18x |
| cluster-three-cap | 501 | 0.519 | 1.060 | 0.717 | 1.540 | 0.49x |
| pod-80x50-90 | 8,192 | 101.391 | 36.682 | 42.622 | 150.399 | 2.76x |
| pod-85x55-112 | 8,192 | 62.452 | 42.990 | 38.994 | 103.370 | 1.45x |

The checked modes execute the full Python oracle as well as Rust. They are correctness modes, not the earlier compiled-C implementation.

## 3. Telemetry j6b2 scorer: original CPython versus Rust comparison

Actual captured j6b2 geometry on an isolated 4-vCPU AMD EPYC 7B13 Spot VM. Each row contains 95,904 generated queries: 296 movable footprints x 324 poses. Median of five alternating repetitions after one warmup per arm. Includes native preparation, conversion and release; excludes snapshot construction and library loading.

| Peer state | CPython (ms) | Rust scalar (ms) | Rust batch (ms) | Rust batch + full Python check (ms) | Scalar speedup | Batch speedup |
| --- | --- | --- | --- | --- | --- | --- |
| Fixed peers | 91.640 | 164.747 | 88.192 | 197.010 | 0.56x | 1.04x |
| All other peers (hypothetical) | 489.551 | 191.354 | 112.841 | 624.807 | 2.56x | 4.34x |

Every score matched exactly. Sparse fixed-peer scalar scoring is slower than CPython; dense scalar scoring is faster. The scalar API is used by placement; batch throughput is a separate boundary. Generated queries do not establish legal placements or actual search trajectories; the dense peer state includes captured off-board origins.

## 4. Complete telemetry iter8n workflow: CPython, Rust and PyPy

Two matched runs per backend, with order reversed for the second pair. Both experiments used the same frozen historical telemetry input and the same AMD Spot VM class, but different VMs. Each candidate is compared with its own contemporaneous CPython baseline; these are not a simultaneous three-way trial.

| Experiment | Backend | Pair 1 (s) | Pair 2 (s) | Mean (s) | Speedup vs paired CPython | Mean time reduction |
| --- | --- | --- | --- | --- | --- | --- |
| Rust experiment | CPython | 94.347 | 94.239 | 94.293 | 1.000x | 0.00% |
| Rust experiment | Rust scorer | 93.774 | 94.340 | 94.057 | 1.003x | 0.25% |
| PyPy experiment | CPython | 95.928 | 95.129 | 95.528 | 1.000x | 0.00% |
| PyPy experiment | PyPy planner + scorer | 69.545 | 70.232 | 69.889 | 1.367x | 26.84% |

Pair 1 ran CPython first; pair 2 ran the candidate first. The Rust mean reduction of 0.25% does not establish meaningful acceleration; Rust was slightly slower in the reverse-order pair. PyPy reduced mean elapsed time by 26.84% in these two pairs.

Times cover complete `place_candidate`: arrangement, residual search, board writing, grading and native checks. PyPy startup and IPC are included; rendering is skipped in both arms. PyPy moves the entire pure planner to a child interpreter while KiCad remains in CPython, so its improvement cannot be attributed to the scorer alone.

All eight runs across the two experiments produced identical board bytes and the same 309 placements / 4,277 steps. All 32 artifacts per pair matched after documented normalization of timing, dependent hashes, native dates, scratch paths and clearance-finding order. Existing defects remain; parity is not board acceptance. Two runs per backend are not a statistical confidence interval.

## 5. Telemetry j6b2 scorer: warmed CPython, Rust batch and PyPy

Later experiment on a separate isolated AMD Spot VM. Same captured j6b2 geometry and 191,808 distinct queries across both states. Each row is the median of five measured passes of 95,904 queries in one warmed worker per backend/state. Warmup required at least five passes and two cumulative scorer seconds.

| Peer state | CPython (ms) | Rust batch under CPython (ms) | PyPy (ms) | PyPy vs CPython | PyPy vs Rust batch |
| --- | --- | --- | --- | --- | --- |
| Fixed peers | 91.209 | 89.692 | 18.281 | 4.99x | 4.91x |
| All other peers (hypothetical) | 481.182 | 115.538 | 57.777 | 8.33x | 2.00x |

All recorded score arrays matched the separate CPython oracle exactly. These prepared-snapshot queries exclude the live placer pad-transformation callback. Rust scalar was measured in section 3; it was not remeasured in this later three-backend experiment. PyPy was not measured on the five synthetic fixtures in sections 1–2.

## 6. Same later experiment: fresh-process and first-pass costs

Median of five fresh processes per backend/state. First-pass scoring has no prior scorer calls in that worker. Whole-process time additionally includes imports, loading, preparation, checking, output serialization and teardown; a fresh process does not imply a cold filesystem cache.

| Peer state | Backend | First scorer pass (ms) | Whole process (s) |
| --- | --- | --- | --- |
| Fixed peers | CPython | 92.562 | 0.522 |
| Fixed peers | Rust batch under CPython | 91.342 | 0.521 |
| Fixed peers | PyPy | 47.235 | 1.382 |
| All other peers (hypothetical) | CPython | 486.334 | 0.922 |
| All other peers (hypothetical) | Rust batch under CPython | 118.432 | 0.553 |
| All other peers (hypothetical) | PyPy | 85.300 | 1.422 |

The three-backend run order reversed the CPython and Rust positions but always left PyPy in the middle; this was not full counterbalancing. Five warmed samples came from one warmed worker per backend/state, not five independently warmed workers.

## Correctness, memory and status

- CPython/Rust validation covered 73 distinct Python tests plus five Rust tests, including overflow, fallback, complete ranks and native board/DRC/receipt parity. PyPy added nine actual-runtime tests, all passing without skips. Reports and comparisons received independent review.
- First PyPy full-workflow pair: child-process high-water RSS was 1.785 GiB with PyPy versus 0.897 GiB with CPython. These are separate high-water measurements, not simultaneous aggregate memory peaks.
- Current j6b2 refuses placement before scoring in CPython, Rust and PyPy because fixed C42 is outside the board. No current-j6b2 full-placement speedup is established. Full workflow timings above use historical iter8n.
- PyPy 8.0.1 / Python 3.12.15 remains explicit opt-in. Upstream labels this Python 3.12 build beta: https://doc.pypy.org/release-v8.0.1.html
- All evidence was retrieved and hash-verified before the experiment Spot VMs and boot disks were deleted. No new benchmark workload was run to prepare this corrected table.

## Retained source reports

The tables above are generated from these committed reports, rather than transcribed from chat. SHA-256 identifies each exact report:

- `docs/benchmarks/placement-scoring-2026-10-22.json` — `59c4a0903dca9cd40b45c4352dff0c3f28504d0313793c0ff64038433095fadd`
- `docs/benchmarks/telemetry-scorer-2026-10-22.json` — `30ce98cf2f04e8050b8d0d7b695f489e70c95086c1a77190bd698a6c5c968c77`
- `docs/benchmarks/pypy-placement-2026-10-22.json` — `ed896b3a688a7e8cf16470ac8474a934e5acbfc9b559b34f54547b922d25bc17`

Rust implementation: `3cd1a5e2f52937d37f5f4ab5fdd999f12d862343`. PyPy implementation: `897adc7d3ff434f0c931ea30bf17ef3eb3611687`. Full report revision before this consolidated table: `375486f7090bc90c74d12385f3b117d8ba928948`.
