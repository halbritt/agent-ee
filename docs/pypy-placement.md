# PyPy placement experiment

The PyPy option runs the existing Python scorer and the complete pure planning
operation in a separate interpreter. Keeping the search loop with the scorer
avoids a process round trip for each candidate. KiCad capture, board writing,
grading and native validation remain in CPython. This changes the interpreter
for more than the scoring kernel, so a full-workflow improvement must not be
attributed to scoring alone.

Ordinary placement still uses CPython. No new interpreter is downloaded or
started implicitly. Install a PyPy distribution supporting Python 3.12 or later
and supply its executable explicitly:

```python
from agent_ee import placement_runtime, placer_runner

with placement_runtime.experimental("/opt/pypy/bin/pypy3") as session:
    result = placer_runner.place_candidate(board, netlist, destination, request)
print(session.calls)
```

There is one child process per planning operation. A targeted request can plan
once for its preview and again under the writer lock. Child startup, input
copying, identity checks and serialization are included in `session.calls`
wall time and in the full-runner benchmark. Each child records its interpreter
identity, source hashes, first successful Python scorer invocation when one
occurs, exit status, stderr and error information. The invocation probe removes
itself before calling the original scorer. A refusal before scoring has no
scorer invocation witness.

The child calls the same planning implementation with the same captured facts,
request, effective policy and netlist bytes. Search budgets stay unchanged.
Heartbeats are relayed to the parent. Worker failures propagate; there is no
automatic fallback to CPython. Transport inputs must be acyclic JSON-native
values with string object keys: tuples and other objects that serialization
would change are refused. Active placement tracing, an active Rust scorer and
nested runtime contexts are explicitly unsupported.

The measured distribution is PyPy 8.0.1 with Python 3.12.15. Upstream labels
the Python 3.12 build **beta** in its
[release notes](https://doc.pypy.org/release-v8.0.1.html). The full archive and
expanded distribution are pinned in the measurement evidence; an executable
hash alone does not identify the shared JIT engine.

## Telemetry measurements, 2026-10-22

Two pairs on the frozen telemetry-pod iter8n input, with run order reversed,
produced byte-identical candidate boards and matching normalized artifacts:

| Order | CPython runner | PyPy planner with CPython KiCad | Elapsed time reduction |
| --- | ---: | ---: | ---: |
| CPython first | 95.928 s | 69.545 s | 27.5% |
| PyPy first | 95.129 s | 70.232 s | 26.2% |
| Mean of two runs | 95.528 s | 69.889 s | 26.8% |

For context, the [earlier Rust experiment](benchmarks/telemetry-scorer-2026-10-22.json)
on the same frozen input and VM class measured 94.057 s for Rust against its
94.293 s CPython baseline: no meaningful demonstrated full-runner speedup.
That experiment used a different Spot VM; it is not a simultaneous three-way
full-runner comparison.

These are complete `place_candidate` times, including PyPy startup and IPC,
board writing, grading and native checks; visual rendering was skipped in
both arms. Both runs kept the original search budget, completed 4,277 steps
and placed the same 309 footprints. The retained board's outcome remains
`defect`: matching it establishes behavioral parity, not board acceptance.
Two pairs establish a repeatable improvement on this input and host, not a
confidence interval or a general performance guarantee.

The scorer-only experiment used current j6b2 captured geometry and the same
191,808 generated queries as the Rust comparison. All score arrays matched
exactly. Median time per 95,904-query pass after the declared warmup was:

| Peer state | CPython reference | PyPy reference | CPython/Rust batch | PyPy speedup over CPython |
| --- | ---: | ---: | ---: | ---: |
| Fixed peers | 91.209 ms | 18.281 ms | 89.692 ms | 4.99x |
| All other peers, hypothetical | 481.182 ms | 57.777 ms | 115.538 ms | 8.33x |

PyPy also beat the Rust **batch** comparator by 4.91x and 2.00x respectively.
The batch comparator is not the Rust scalar API used by placement. These
queries use prepared snapshots, not the live placer's pad transformation
callback. The dense state includes unplaced off-board origins and does not
represent an achieved placement.

There are startup and memory costs. Median fresh-process elapsed time was
1.382 s versus 0.522 s for fixed peers, and 1.422 s versus 0.922 s for dense
peers. Those times include loading, preparation, checking and serialization,
not just interpreter startup. A fresh process is not a cold filesystem cache.
The first full pair recorded a child-process high-water RSS of 1.785 GiB with
PyPy versus 0.897 GiB with CPython; these are separate process high-water
measurements, not simultaneous aggregate memory usage.

Five warm samples came from one warmed worker per backend and state. Cold
runs used five fresh workers per backend and state; in the three-backend
order reversal PyPy always occupied the middle position. No steady-state or
fully counterbalanced microbenchmark claim is made.

The current sealed j6b2 input still returns `fixed_conflict` at zero search
steps under both interpreters: fixed C42 is outside the board. The PyPy child
ran successfully but never invoked the scorer. This matched refusal provides
no full-placement performance result for j6b2.

All workloads ran serially under native admission on an isolated GCP
`c2d-highcpu-4` Spot VM with an AMD EPYC 7B13 and the existing
`agent-ee:ci-14ef7275` image. Source, input, runtime distribution and shared
engine identities were checked before and after each job. The implementation
pin is `897adc7d3ff434f0c931ea30bf17ef3eb3611687`. The Spot VM and its
boot disk were deleted after all evidence was retrieved and hash-verified.
The [machine-readable results](benchmarks/pypy-placement-2026-10-22.json)
retain raw timing samples, source and runtime pins, archive hashes, correctness
checks and teardown evidence.

## Reproduce a comparison

Run native work through the host admission wrapper. The outer interpreter
needs the existing KiCad bindings and `kicad-cli`. Each output directory must
be new; keep the frozen board and netlist outside the writable candidate root.

```sh
native-admit run --label pypy-pod-pair -- python3 scripts/telemetry_scorer_compare.py \
  --candidate-backend pypy --pypy "$PYPY" \
  --board "$BOARD" --netlist "$NETLIST" --request "$REQUEST" \
  --out build/pypy-scoring/python-first
```

Repeat with `--candidate-first` and a fresh output directory to reverse the
order. Each arm has the existing 900-second process limit; both skip visual
rendering. Candidate board bytes must match exactly. Raw hash links are checked
before the documented comparison-only normalization of arrangement elapsed
time, dependent hashes, native dates, scratch paths and clearance finding order.
See [the retained-board comparator](placement-scoring.md#comparing-a-retained-board).
A matching refusal is retained but cannot establish a placement speedup.

For the original scorer alone, under CPython and PyPy:

```sh
native-admit run --label pypy-scorer-grid -- python3 scripts/telemetry_scorer_interpreters.py \
  --capture "$CAPTURE" --policy "$POLICY" --pypy "$PYPY" \
  --out build/pypy-scoring/scorer-grid --runs 5
```

The scorer benchmark uses the same immutable snapshots and generated 9 by 9
query grids at four rotations as the Rust experiment. A separate CPython
process establishes the expected score arrays. Every measured worker checks
all its results against those arrays outside the score timer. The optional
`--library` argument adds the CPython/Rust prepared-batch comparator.

Five alternating cold processes per backend record first scorer-pass time and
whole-process time separately. Whole-process timing also includes imports,
input loading, snapshot creation, source verification, checking and output
serialization. Warm measurements use a separate worker per backend and state:
at least five warmup passes and two cumulative scorer seconds, bounded at
1,000 passes, then five measured passes. Failure to reach that declared warmup
stops the run. This is a specified warmup procedure, not proof of steady state.
Interpreter defaults govern garbage collection. Generated queries do not
establish legal placements or reproduce an actual search trace.

## Verification

```sh
AGENT_EE_PYPY="$PYPY" PYTHONPATH=src:tests \
  python3 -m unittest -v test_placement_runtime
```

Nine focused tests cover exact plans and nonempty heartbeat sequences, block
admission with a distinct effective policy, budget exhaustion, validation
errors, process cleanup, unsupported combinations and serialization refusal.
The actual pinned PyPy interpreter is required for the PyPy-specific tests.
