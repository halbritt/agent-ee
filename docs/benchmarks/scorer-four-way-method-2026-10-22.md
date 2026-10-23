# Matched telemetry scorer comparison: method

Historical scalar comparison, superseded by the complete Rust-planner results in
[the result table](scorer-results-2026-10-22.md). Reproduction requires an isolated
checkout of `6f48b9f2653ff2d3b5a10c1a9c2a5a0b3f9b061c`; its C/PyPy experiment
tools are retired from the shipping tree.

The result table is in `scorer-results-2026-10-22.md`; machine-readable measurements and evidence hashes are in `scorer-four-way-2026-10-22.json`.

One frozen iter8n telemetry-pod input is executed 16 times, with four fresh workers per implementation. The four round orders are CPython/C/PyPy/Rust, C/Rust/CPython/PyPy, Rust/PyPy/C/CPython, and PyPy/CPython/Rust/C. Each implementation occupies every position once. Runs are serial on one isolated `c2d-highcpu-4` Spot VM (AMD EPYC 7B13).

All arms use the same board, native netlist, adjacent project/rules, stock models, request and 200,000-step budgets. Only the writable output directory changes; visuals are disabled uniformly. Source, input, model and runtime hashes are checked before and after every arm. There is no persistent worker or interpreter warmup. All 16 outputs must match the first CPython run; failures, refusals, fallback, missing backend invocation evidence or differences stop the comparison without retry.

The timer covers context/library loading and the complete `place_candidate` call. It includes PyPy startup and transport, board writing and native checks. Common production-module imports and final metadata serialization are outside the timer; the C adapter import is inside it. Reported speedup is CPython's mean divided by the implementation's mean. Four observations on this host/input do not establish a population confidence interval.

C retains the original kernel, packing and one-pose batch route from `50e586818acf6265039a3509acc910eb0158f357`; its fast adapter omits the full Python oracle inside timing. Rust uses its prepared scalar scorer under CPython. PyPy executes the original Python scorer and the entire pure planner in a child; KiCad stays in the CPython parent. Thus PyPy's result measures the complete planner runtime change.

Candidate board bytes must match exactly. The comparator verifies raw hash links before normalizing elapsed time, output paths, native dates and dependent hashes. It also normalizes ordering of complete native clearance records within their existing clearance slots. Other finding order, contents and multiplicity remain significant. Raw artifacts are retained unchanged. Independent auditing additionally compares full placement plans after removing only arrangement elapsed time, checks budgets/steps and recomputes timing arithmetic. Successful execution and matching outputs do not establish board acceptance.

Source pin: `1ce28a5c2332773c3ca5962990c041d79523441e`. Container image: `sha256:c61213c757f40b99bd36ccdb900572ae5aecf73e382258e203b0feeee1640845`. The container has four CPUs, 6 GiB RAM, no network and native admission. The arm timeout is 900 seconds; the campaign deadline is 80 minutes.

The C library was built with GCC 13.3.0 and the original flags `-O3 -std=c11 -shared -fPIC`. Rust uses the previously verified EPYC 7B13 build with Rust 1.99.1, optimization level 3, fat LTO, one codegen unit, aborting panics and native CPU targeting. PyPy is the pinned 3.12.15/PyPy 8.0.1 distribution. Library, runtime, source and input hashes are retained in the result JSON and raw manifest.

Inside the staged container at `/opt/agent-ee`:

```sh
export PYTHONPATH=src:tests:scripts PYTHONDONTWRITEBYTECODE=1
export KICAD10_3DMODEL_DIR=/opt/agent-ee/build/rust-scoring/telemetry-iter8n/stock-models
python3 -B scripts/telemetry_scorer_four_way.py \
  --board build/rust-scoring/telemetry-iter8n/inputs/seed.kicad_pcb \
  --netlist build/rust-scoring/telemetry-iter8n/inputs/native.xml \
  --request build/rust-scoring/telemetry-iter8n/request.json \
  --models "$KICAD10_3DMODEL_DIR" \
  --c-library build/four-way-scoring/placement_score_original.so \
  --rust-library build/rust-scoring/placement_score-cloud.so \
  --pypy build/pypy-scoring/pypy3.12-v8.0.1-linux64/bin/pypy3 \
  --pypy-root build/pypy-scoring/pypy3.12-v8.0.1-linux64 \
  --out build/four-way-scoring/matched
```

The frozen input/runtime payload and container image are prerequisites. The raw archive also retains the exact admission/container launch scripts and the 24-test preflight log. The result JSON records archive integrity, independent audit and VM/disk cleanup evidence.
