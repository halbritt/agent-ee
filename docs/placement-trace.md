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
`placer_runner.traced_place_candidate(...)`. The runner writes the trace even
when placement raises. The trace then has state `failed` with the error, partial
coverage and an unknown candidate identity, and the error is re-raised. A trace
path that already exists is refused. To roll back, turn the trace off.

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
| `stage_a.rank` | stage_a.search | exclusive | disjoint | `_ranked` heap work without exact resolution |
| `stage_a.predicates` | stage_a.search | exclusive | disjoint | `blockers` for each non-doomed step |
| `stage_a.judge` | pipeline | exclusive | nested in `stage_b.arrange` | complete-proposal judge, group and locality grading |
| `stage_b.arrange` | pipeline | inclusive | disjoint | `block_arrangement.arrange`: admission, block search and residual Stage A |
| `stage_b.search` | stage_b.arrange | exclusive | disjoint | the block search loop |
| `native_checks` | pipeline | exclusive | disjoint | writer padstack refusal, pad reloads, native DRC, saved judgment, netlist binding, write scope |
| `export` | pipeline | exclusive | disjoint | `SaveBoard`, provenance, project models, images, result writes |

Stage B's centroid objective has its own oracle in the plan. This trace times
Stage B and counts its steps, but it samples only the Stage-A wire cost.

## Counters

Counters are complete even when samples are capped:

- `stage_a.steps` is the sum of `_Search.steps` over the searches. It equals
  `doomed + admitted + rejected`, and for a plain `pack` the plan's `steps_used`.
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
contract. It refuses an invalid envelope, a request whose inputs differ from their
hash, and a sample whose generation is not its request's (stale).

`replay` recomputes each sample with the placer's own `_wire_cost` and
`_wire_bounds`. Its `status` is `exact`, `mismatch` or `refused`. Its `parity` is
`complete` only for an exact replay of an envelope with complete coverage. A capped
trace replays as `partial`.

## Limits

The defaults are 256 requests, 8192 samples and 8 MiB of serialized requests plus
samples. Set them with `--max-requests`, `--max-samples` and `--max-bytes`. When a
limit is reached, the dropped records are counted in `sampling.dropped`, coverage
becomes `partial`, and a reason names the limit. Requests are kept first, then
their samples, in recording order, up to the byte limit. Samples of a dropped
request are dropped too.

## Overhead experiment gate

The provisional gate is a median trace overhead of at most 5% with tracing on. It
is an experiment gate, not a physical constraint. A new host or session is a new
measurement.

`scripts/placement_trace_overhead.py` runs `placement_trace.overhead`. It times
matched, alternating tracing-off and tracing-on `pack` runs (one warmup, five
runs) and checks that the plans are identical. The fixtures are three unit
fixtures and the first two owned pod-scale qualification boards. The script
generates the pod boards natively, as in `scripts/qualify_pod_placement.py`. The
overhead column is recording overhead. The envelope column adds the envelope
assembly after the run.

Measured 2026-10-22 on agent-ee-gate-c4 in the pinned CI image (`ae-ci-exec`,
Python 3.12.3, x86_64), in two sessions:

| Fixture | Parts | Status | Steps | Exact | Median off ms | Overhead | With envelope | Coverage | Envelope bytes | Exact share of Stage-A search |
|---|---|---|---|---|---|---|---|---|---|---|
| search-promotion | 3 | planned | 3 | 3 | 1.8 / 1.9 | 4.80% / 4.03% | 15.79% / 14.32% | complete | 5810 | 1% |
| search-anchor | 3 | planned | 6 | 6 | 1.9 / 1.9 | 3.22% / 4.69% | 13.11% / 15.01% | complete | 5945 | 1% |
| cluster-three-cap | 4 | packing_budget_exhausted | 500 | 501 | 76.6 / 76.2 | 2.26% / 2.52% | 2.92% / 3.21% | complete | 32604 | 2% |
| pod-80x50-90 | 90 | planned | 182 | 34595 | 692.9 / 697.5 | 3.74% / 3.59% | 5.11% / 4.99% | partial | 594315 | 53% |
| pod-85x55-112 | 112 | planned | 128 | 157833 | 2481.5 / 2504.3 | 3.23% / 2.38% | 3.72% / 2.87% | partial | 681039 | 75% |

Every fixture met the gate for recording overhead in both sessions, and every plan
was identical. With envelope assembly included, the pod boards are within 5% except
pod-80x50-90 in one session (5.11%). On the 2 ms unit fixtures, envelope assembly
is a fixed cost of about 0.2 ms and exceeds the gate. The pod traces are partial
because their exact evaluations exceed the 8192-sample limit. Exact wire cost
takes 53% and 75% of Stage-A search time on the pod boards, which is the score
fraction the GPU plan listed as unmeasured. These are single-host measurements.
The plan's five matched real-workload replays and its G1/G2 admissions still apply.

To reproduce:

```sh
ae-ci-exec . -- env AGENT_EE_SKIP_VISUALS=1 PYTHONPATH=src:tests:. \
  python3 scripts/placement_trace_overhead.py --runs 5
```
