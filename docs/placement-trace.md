# Placement timing and replay trace (AGENTEE-430, G0)

G0 of the [GPU placement plan](research/2026-10-21-gpu-pcb/README.md#g0--bounded-stage-traces-24-days)
adds an opt-in, bounded trace of the placement pipeline. It records per-stage
monotonic durations, counters that reconcile with the plan, and a capped Stage-A
scoring sample that `replay` recomputes exactly. The record is a shared
[`pcb-trace-envelope/1`](trace-envelope.md). Its consumer-owned payload is
`agent-ee.placement-trace/1`. G1 (AGENTEE-431) compares a compiled scorer against
this trace. No placement decision reads the trace.

## Off by default

Tracing is on only inside `placement_trace.recording()`. While it is off, each
hook reads a context variable and finds `None`, and the placer, block arrangement
and writer run their untraced code. Three entry points turn tracing on:

```sh
# Pack a capture with the trace on; plan and trace go to fresh paths.
PYTHONPATH=src python3 -m agent_ee.placement_trace pack --capture probe.json \
  --policy policy.json --trace trace.json --plan plan.json [--experiment ID]
# The disposable writer with the trace on: a fresh file inside the writable root, not the result.
PYTHONPATH=src python3 -m agent_ee.placer_runner board.kicad_pcb design.xml --request request.json \
  --destination /root/proposal --result /root/result.json --trace /root/trace.json
# Recompute every sampled bound and exact cost; exit 0 only when all match.
PYTHONPATH=src python3 -m agent_ee.placement_trace replay trace.json
```

Python callers use `placement_trace.traced_pack(capture, policy)` or
`placer_runner.traced_place_candidate(...)`. Before anything is recorded or
written, `traced_place_candidate` checks the single-design-writer authorization
and the trace path, which must be a fresh, non-hidden, non-symlink file directly
inside the authorized writable root and must not be the candidate destination.
It also refuses impossible limits at this point. A refusal leaves no trace and no
candidate. The command line goes through the same check and also refuses the
result path. After these checks pass, the runner writes the trace even when
placement raises. The trace then has state `failed` with the error, partial
coverage and an unknown candidate identity, and the error is re-raised. Traces are
written as canonical JSON (sorted keys, no whitespace) to a path that must not
already exist. A write that fails after creating the file removes that file, so no
truncated trace is left. To roll back, turn the trace off.

The trace is an observer, and a failure to assemble or write it never replaces the
placement outcome:

- When placement raised, its own exception is re-raised. The trace error is
  attached as `placement_trace_error`, plus a note that names the path. This
  holds when the write itself is interrupted (`KeyboardInterrupt` or another
  `BaseException`): the interruption is the attached trace error, and the
  placement exception still propagates.
- When placement returned, `traced_place_candidate` raises
  `TracePersistenceError`. Its `report` is the placement report, and `__cause__`
  is the trace error. A placed candidate can already be published at its
  destination, so the caller must not read this error as "no candidate".
- The command line writes the placement report to `--result` as usual, prints
  the trace error to stderr and exits 3.

## Outputs are unchanged

The plan, the saved board and every receipt are the same with tracing on and off.
`tests/test_placement_trace.py` checks this with canonical JSON plan comparisons.
The fixtures are the search, anchor, three-capacitor cluster and copper-aligned
cases, plus 30 seeded random cluster cases. They cover the legacy, joint and
copper-aligned passes and typed failures. The test also compares the block
arrangement plan with tracing on and off. Its `block_arrangement.search.elapsed_s`
is the arrangement's own wall time and differs between any two runs, so the test
removes that field before comparing.

The native writer test compares candidate directories byte for byte. The board,
plan, probe, judgment and result files are identical with tracing on and off. In
the native DRC records, two facts differ between any two runs: the per-run staging
directory name and KiCad's report `date`. The test normalizes only those two facts
before comparing the rest.

The only change outside tracing is a placement generation stack in `_Search`.
`push` allocates a new id and `pop` restores the previous one, as `pop` restores
the peers, so the id names a partial-placement state. Search decisions never read it.

## Stages and boundaries

Durations are `time.perf_counter_ns` nanoseconds. Each row has a `boundary`
(`inclusive` covers its children; `exclusive` does not) and an `overlap`.
`nested` rows can also run inside the durations their `within` list names.

| Row | Parent | Boundary | Overlap | Covers |
|---|---|---|---|---|
| `pipeline` | — | inclusive | disjoint | the whole traced call |
| `prep` | pipeline | exclusive | nested in `stage_b.arrange` | writer capture/binding, then placer qualification and proofs before each search |
| `stage_a.search` | pipeline | inclusive | nested in `stage_b.arrange` | `_Search` construction and the unit loop, backjumps included |
| `stage_a.geometry` | stage_a.search | exclusive | disjoint | scan anchors, candidate axes, generation and locality constraints, without bounds |
| `stage_a.bounds` | stage_a.search | exclusive | disjoint | `_wire_bounds`, once per quarter turn |
| `stage_a.exact` | stage_a.search | exclusive | disjoint | each lazily resolved `_wire_cost` |
| `stage_a.rank` | stage_a.search | exclusive | disjoint | `_ranked` heap work, without exact resolution or sample recording |
| `stage_a.predicates` | stage_a.search | exclusive | disjoint | `blockers` for each non-doomed step, a raising one included |
| `stage_a.trace` | stage_a.search | exclusive | disjoint | the observer: recording each scan's request and each exact evaluation's sample |
| `stage_a.judge` | pipeline | exclusive | nested in `stage_b.arrange` | complete-proposal judge, group and locality grading |
| `stage_b.arrange` | pipeline | inclusive | disjoint | `block_arrangement.arrange`: admission, block search and residual Stage A |
| `stage_b.search` | stage_b.arrange | exclusive | disjoint | the block search loop |
| `native_checks` | pipeline | exclusive | disjoint | writer padstack refusal, pad reloads, native DRC, saved judgment, netlist binding, write scope |
| `export` | pipeline | exclusive | disjoint | `SaveBoard`, provenance, project models, images, result writes |

Stage B's centroid objective has its own oracle in the plan. This trace times
Stage B and counts its steps, but it samples only the Stage-A wire cost.

The cost of recording requests and samples is reported in `stage_a.trace`
(count: recorded scans plus exact evaluations). It is not included in
`stage_a.rank` or `stage_a.geometry`. The clock reads and counter increments of
the hooks themselves stay inside the stage they time.

Every span closes when its code raises, as well as when it returns. A failed or
interrupted run keeps the prep, search, judge, geometry, bounds, exact, rank,
predicate and Stage-B time it entered, so `stage_a.search` still covers its
children and `pipeline` its top-level rows. A raising exact resolution keeps its
time but is not counted as resolved. A raising bounds computation is a counted
`stage_a.bounds` span, not geometry time. A raising request or sample record is
counted in `stage_a.trace`, not in geometry or rank time.

## Counters

Counters are complete even when samples are capped:

- `stage_a.steps` is the sum of `_Search.steps` over the searches. It equals
  `doomed + admitted + rejected + aborted`, and for a plain `pack` the plan's
  `steps_used`. `stage_a.aborted` counts steps whose predicates raised. It is 0
  in a completed run unless a search failure was handled inside the placer. A
  trace written before this counter existed has no `stage_a.aborted`, and the
  reader treats it as 0.
  With block arrangement, `stage_a.steps + stage_b.steps` equals the plan's `steps_used`.
- `stage_a.candidates_generated` equals `stage_a.exact_resolved + stage_a.unresolved`.
  An unresolved candidate kept only its lower bound.
- `stage_a.scans`, `stage_a.searches` and `stage_a.generations` count work. Per-search
  steps and generations are in `generation.searches`.
- `stage_a.stale_samples` counts exact evaluations made at a generation other than
  their scan's. It is 0 in every fixture.

`payload.reconciliation` reports the step and candidate identities.

## Replay sample

For each scan, while the request limit allows, a request records the part, its
at-origin signal pads for each quarter turn (`_pad_nets` at the origin), and its
same-net peers at the scan's generation. It also records an `inputs_sha256` over
the pads and peers. For each exact evaluation, while the sample limit allows, a
sample records `request, generation, x_nm, y_nm, rotation_deg, local_cost,
bound_nm, cost_nm` (see `payload.sample_fields`). A non-integral locality cost is
stored exactly as the string `p/q`.

`placement_trace.stage_a_inputs(trace)` returns the scoring inputs of each sampled
scan: pads, peers, poses and the expected bounds and costs. This is the G1 replay
contract. Before it returns anything, it checks the evidence and raises
`ValueError` on any of these:

- an invalid envelope, including a duration whose `parent` is not a duration
  name or null, or whose `within` is not a list of duration names;
- an unsupported contract. The consumer must be `agent-ee.placement/1`, the
  objective `agent-ee.placer.wire-cost/1` and the generation scheme
  `agent-ee.placer.search-stack/1`. An `unknown` objective or generation is never
  assumed to be the supported one;
- a malformed payload: a request whose id is not its recording position, an
  unrecorded search, a generation the search never allocated, pads missing a
  quarter turn, peers that are not `[x, y]` points of the pad nets, or inputs that
  differ from their hash. A sample without its eight integer fields (`local_cost`
  may be `p/q`), with a rotation other than 0, 90, 180 or 270, or that names an
  unrecorded request is also malformed;
- a stale sample, whose generation is not its request's;
- a duplicated sample, a request with more samples than generated candidates, or
  samples and requests over their limits;
- evidence that does not reconcile. `sampling.recorded` and `sampling.bytes` must
  count and size the payload exactly. `sampling.dropped` must equal the scans and
  exact evaluations that the counters report and the payload does not hold. The
  counters must reconcile with the durations and the generation records. Deleting
  samples, or editing one counter, is refused even when the coverage label still
  says `complete`.

`replay` recomputes each sample with the placer's own `_wire_cost` and
`_wire_bounds`. Its `status` is `exact`, `mismatch` or `refused`. A refusal has
parity `none` and a reason. Only the typed `ValueError` from `stage_a_inputs`
becomes a refusal. Any other error in the reader or the scorer propagates, so an
implementation defect is never reported as a judgment on the evidence. Its `parity` is `complete` only for an exact replay of
a completed run that has complete, reconciled coverage and qualified `pcb` and
`rules` identities. An exact replay is `partial`, with a reason for each gap, in
these cases: a capped trace, a `failed` or `interrupted` run (whatever its coverage
label says), or an unknown `pcb` or `rules` identity. A trace with unknown fields
stays a valid, retained record. It is never certified as complete evidence.

A trace with no samples can still be complete, but only when the counters agree
that nothing was evaluated. For example, a plan that fails on a fixed conflict
before any search has zero scans and zero exact evaluations, and its exact replay
is `complete` with `samples_checked` 0. If the counters report scans or exact
evaluations that are neither in the payload nor counted as dropped, the same
empty payload is refused. If a declared cap dropped all samples, the replay is
`partial`.

## Limits

The defaults are 256 requests, 8192 samples and 8 MiB of serialized requests plus
samples. Set them with `--max-requests`, `--max-samples` and `--max-bytes`.

The limits apply while the trace records, not when it is exported. The recorder
keeps a byte account of everything it keeps. The account is an upper bound of the
serialized size, because it reserves each request's generated-candidate count. A
request is sized before its peers are read or copied. A request whose peer points
cannot fit, at a minimum of 6 bytes each, is dropped from its point count alone.
A request or sample that would take the account over the byte limit is dropped and
not kept. Samples of a dropped request are dropped too. Counters stay complete
whatever is dropped.

When a limit is reached, the dropped records are counted in `sampling.dropped`,
coverage becomes `partial`, and a reason names the limit. The smallest byte limit
is 28, the size of the empty sample set `{"requests":[],"samples":[]}`. A smaller
limit, or a limit that is not a non-negative integer, is refused before placement
starts. At 28 bytes the trace records timings and counters but keeps no requests
or samples.

## Overhead experiment gate

The provisional gate is a median trace overhead of at most 5% with tracing on. It
is an experiment gate, not a physical constraint. A new host or session is a new
measurement. Under the coordinator ruling on AGENTEE-430 (H6):

- The boundary includes export: recording, envelope assembly and export to a
  fresh file.
- The gate applies only to workloads whose tracing-off median is at least 100 ms
  (cluster-three-cap and the pod boards).
- It must be measured on an unloaded host, with a 1-minute load average below 2
  when the measurement starts and when it ends.
- Sub-100 ms unit fixtures are reported, not gated. For them `overhead()` reports
  `gate.applies: false` and `gate.met: null`, never met.

`scripts/placement_trace_overhead.py` runs `placement_trace.overhead`. It times
matched, alternating tracing-off and tracing-on `pack` runs (one warmup, five
runs) and checks that the plans are identical. It reports, for every fixture:

- the tracing-off median;
- the overhead to export (`median_overhead`);
- the recording alone (`median_recording_overhead`);
- export ms.

It also records the load average before and after the run. Its report-level
`gate.met` is true only when the host was unloaded, at least one fixture is
gated, and every gated fixture is within 5%. The fixtures are three unit
fixtures and the first two owned pod-scale qualification boards. The script
generates the pod boards natively, as in `scripts/qualify_pod_placement.py`.

Measured on 2026-10-22 on talos (12 threads) in the pinned CI image
(`ae-ci-exec`). The 1-minute load average was 0.99 before the run and 1.72 after:

| Fixture | Status | Median off ms | Overhead (to export) | Recording only | Export ms | Gated | Coverage | Trace bytes |
|---|---|---|---|---|---|---|---|---|
| search-promotion | planned | 2.63 | 23.64% | 8.04% | 0.41 | no (reported) | complete | 5965 |
| search-anchor | planned | 2.93 | 23.17% | 8.63% | 0.43 | no (reported) | complete | 6100 |
| cluster-three-cap | packing_budget_exhausted | 129.05 | 4.38% | 3.20% | 1.46 | yes: met | complete | 32764 |
| pod-80x50-90 | planned | 1285.91 | 8.34% | 6.25% | 26.10 | yes: exceeded | partial | 594479 |
| pod-85x55-112 | planned | 4780.75 | 6.36% | 5.70% | 31.10 | yes: exceeded | partial | 681200 |

**The gate is not met.** Both pod boards exceed 5% on an unloaded host, and
`gate.met` is false. Every plan was identical with tracing on and off. On the
pod boards, almost all of the overhead is recording: the per-evaluation exact
timing and sampling of up to 158,000 exact wire-cost evaluations. Export adds
1 to 2 points. Matched reruns from saved captures on the same host vary by
several points from round to round:

- pod-85x55-112: 3.0 to 4.3% before the R3 correction (f3732773), 5.9 to 7.0%
  after it.
- pod-80x50-90: 6.4 to 7.6% at both revisions.

The R3 correction adds one clock read for each exact evaluation, so that the
sample time is reported in `stage_a.trace` instead of in rank time. That costs
about one to three points on the largest pod. The gate was not met before the
correction either. `stage_a.exact` is not coarsened to pass the gate.

**Tracing off versus the pre-change parent.** On the same host and image, the
tracing-off `pack` median was compared with the pre-AGENTEE-430 parent
`0a07f01e`. The runs alternated between processes for each revision: 6 rounds of
5 runs, so 30 runs per fixture and revision. The plans were identical between
the two revisions on every fixture. The differences, all within ±0.9%, are
noise:

| Fixture | Parent ms | Head ms | Difference |
|---|---|---|---|
| search-promotion | 2.58 | 2.57 | −0.18% |
| search-anchor | 2.87 | 2.87 | −0.18% |
| cluster-three-cap | 126.30 | 126.47 | +0.13% |
| pod-80x50-90 | 1259.40 | 1248.47 | −0.87% |
| pod-85x55-112 | 4648.51 | 4679.31 | +0.66% |

The pod traces are partial because their exact evaluations exceed the
8192-sample limit. Exact wire cost is the larger share of Stage-A search time on
the pod boards. That is the score fraction the GPU plan listed as unmeasured.
These are single-host measurements. The plan's five matched real-workload replays
and its G1/G2 admissions still apply.

To reproduce:

```sh
ae-ci-exec . -- env AGENT_EE_SKIP_VISUALS=1 PYTHONPATH=src:tests:. \
  python3 scripts/placement_trace_overhead.py --runs 5
```
