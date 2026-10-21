# GPU placement and routing: measured options for the owned backend

2026-10-21. Agent-510, with bounded investigations by Sol subagents. Implementation
recipient: agent-508. This supplements the [routability design](../2026-10-21-routability-design.md).
Its geometry, ownership, atomic repair, plane policy and native grading requirements
continue to apply. No production algorithm was changed for this investigation.

## Recommendation

Keep placement decisions and copper commits on the CPU. Accelerate pure numerical
batches only where real workloads pay for packing, transfers and dispatch. The
experiments support a compiled placement scorer and further study of resident
dense fields. They do not support integrating the tested GPU route-pattern scorer.

Implementation order:

1. Capture bounded timing and candidate traces at existing placement/routing seams.
2. Add exact compiled CPU placement scoring behind the existing lazy ranker.
3. Experiment with resident GPU scoring/fields if actual traces pass the gates below.
4. Consider GPU maze search only if search dominates after the main plan's
   correctness and repair work; it needs a separate graph-semantics experiment.

Routing functionality stays in the owned backend. CUDA/numerical libraries are
implementation dependencies; no upstream router is added. Freerouting needs no GPU
adapter work. A later repository merger can move the numerical modules with their
callers. Neither a merger nor a network service is needed for this work.

The **semantic architect** remains the producer of checked electrical/mechanical
intent in the main plan. GPU scores cannot waive those constraints. Sharing the
GPU with an architect model is a scheduling issue, not a reason to require a
learned placement or routing model.

## Evidence, identity and existing seams

Raw inputs, hashes and repetitions are retained under [experiments](experiments/)
and [results](results/). Proposed APIs below are explicitly marked as proposed.

| Item | Verified scope |
| --- | --- |
| agent-ee oracle source | `53e4d10f6c9a8a94867fd6e329bc466e85d9ea97`; whole `placer.py` SHA-256 `a2b37ba8b4f38319bf3216005fd268217ebf5e7c6a075ee6341924f64e8292c2` |
| kicad-route Python source | `557ae5758c06b050508dd8016f5c7b6abc2a83f8` |
| Native extension used for map export | Generation `84b30dc286f44df6b465ae8d40aa1363`, manifest build commit `f42b8796de2006bae147e65340da99462b5fd9be`; extension SHA-256 `a68e93acff252282e8836b0ac33e1347684f66db7af5633c90afd326fa9d60a6` |
| Revision qualification | Current Python and installed generation differ. Rust subtree diff between those commits is empty; that is not a fresh build attestation. Original native-input metadata names the Python revision/generation; [run manifest](results/run-manifest.json) adds compiled provenance. |
| Board | fome-mc telemetry-pod expansion iteration-7, 90 × 65 mm, 309 footprints, 1,061 pad records. [Saved board](experiments/candidate.kicad_pcb.gz) compressed SHA-256 `44a0a48cc60118e8aba8a4d85b1505aa9ef3924206d3029004f71184906388fa`; uncompressed `35f242c804038b7ec48740e6f894339e1c152fea95e6a15ddfe77b402030cba9` |
| Placement workload | [Actual pad geometry/oracle](experiments/placement-input.json), source pad/plan hashes included. U23 sampled at artificial ±2 mm offsets, 0.25 mm pitch, four rotations: 1,156 poses. **No placement legality admission.** |
| Routing workload | [Native base map](experiments/native-input.npz), [metadata](experiments/native-input.json): 651 × 901 cells, 0.1 mm grid, 138,697 blocked after an added finite fence; U23.1 `/PRE_6V3` net excluded. Twenty-one actual same-net queries. Thirty-two separately saved synthetic probes are unused in reported timing batches. |
| Machine | peecee-wsl, RTX 3090 Ti, 24,564 MiB, driver 610.88; i9-12900KS, 24 logical CPUs, WSL2 kernel 6.18.54.1. CPU arms pinned to logical CPU 2. |
| Software | Python 3.12.15, NumPy 2.5.4, CuPy 14.2.0, CUDA toolkit package 12.9.2.0; g++ 15.2.0, `-O3 -std=c++17 -shared -fPIC`, one CPU thread. [Environment](results/environment.txt). |

The base map comes from `build_base_obstacle_map(...,[root_net],static_base=True)`
and `GridObstacleMap.is_blocked`. It is not a fully prepared net-specific routing
snapshot. Experimental width/clearance are 0.1 mm, without imported project class
or custom rules. The graph has F.Cu only, four-neighbor motion, unit cell costs,
no vias/turn costs/history/proximity and a finite rectangle fence. Results imply
neither native rule compliance nor physical impossibility. Earlier pad-only
fallback inputs/results are retained and clearly labeled; recommendations use
the native base map.

GPU runs acquired/released the fleet's existing peecee GPU-only `marker` slot,
executing through `ssh peecee-wsl`. Lease IDs are saved. Ollama's resident
`qwen3.6:27b` was unloaded as authorized. Initial CuPy setup failed for missing CUDA
headers; installing `cupy-cuda12x[ctk]` into the isolated venv fixed it. That failed
probe is not a benchmark result. GPU clocks and Windows activity were uncontrolled;
these are one-session measurements, not population confidence intervals.

Paths below are relative to the repository and revision above.

| Existing code | Implication |
| --- | --- |
| agent-ee `src/agent_ee/placer.py::_pad_nets`, `_wire_cost` | Sum per nonground/nonempty source pad of nearest external same-net peer Manhattan distance. Duplicate pads stay distinct. KiCad positive quarter turn is `(x,y) → (y,-x)`. |
| Same file, `_wire_bounds`, `_ranked`, `_Search.scan` | Existing separable bounds/lazy heap avoid some exact scoring. Preserve full deterministic rank keys, admission and backjumping. |
| agent-ee `src/agent_ee/block_arrangement.py::_Arrangement` | Macro objective uses integer mean pad points per shared signal net. It is different from the tested per-pad objective and needs a separate oracle. |
| kicad-route `src/kicad_route/router/py_router/obstacle_map.py::build_base_obstacle_map`, `routing_context.py::prepare_obstacles_inplace`, `single_ended_loop.py::_route_on_prepared_map` | Capture after net preparation/endpoint overrides and before search. Public `blocked_cells` alone misses static bitmap and override behavior. |
| Owned `rust_router/src/obstacle_map.rs::is_blocked`, `is_blocked_with_margin`, `is_via_blocked_rung` | Preserve clearance halos, margins, exceptions and full-stack via legality; F.Cu occupancy alone is insufficient. |
| Owned `rust_router/src/router.rs::NodeStore`, `route_multi`, `stats_to_dict`; `types.rs::GridState` | Tiled sparse search keys coordinates/layer. Parent direction affects turn costs; optional prior vias affect later via choices. Weighted, bounded A* is not an exact Dijkstra oracle. Existing stats lack the needed stage wall times. |

Retained iteration-7 `packer-execution.json` reports 106.355 s process duration;
its placement plan reports 16.829 s macro search. Neither isolates scoring. A
separate iter8d routing log reports 24.07 s across three passes, with base-map
builds 2.56/0.22/0.10 s and per-net cache work 0.50/0.27/0.24 s. These different
runs/placements cannot establish CPU/GPU speedup. Exact paths are in the manifest.

Late execution update: kicad-route advanced to `3ae2c03` during this investigation.
Its `reports/routability-m1-20261021/` records KICADROUTE-90 campaign eligibility
released, with KICADROUTE-91 policy/identity still unfinished. The native terminal
audit reconciles the ten alleged historical lost nets: all were already incomplete
on the saved input; its capped 499-item DRC report made absence-based completion
inference wrong. None of the 53 truly complete input nets regressed. Six
footprint-owned keepouts also exist. This does not change the frozen kernel
measurements or qualify those historical routes. The earlier plan is corrected;
GPU work must use the revised grading contract when integration begins.

## Experiments

Performance runs use two warmups and seven measured repetitions. CPU/GPU arm order
alternates, device operations synchronize, and equality checks run outside timing.
Compilation/initial transfer are reported separately. No full placement or
routing campaign was rerun; no native completion or DRC improvement is claimed.

### Exact placement scores

The kernel has 12 relevant U23 source pads and 50 peer entries. All signed int64
scores match the current Python oracle and independent NumPy computation. Batches
above 1,156 repeat queries: throughput tests, not additional board opportunities.

| Candidates | Compiled CPU median ms | GPU upload + scoring + download median ms | GPU resident wall median ms |
| ---: | ---: | ---: | ---: |
| 1 | 0.0144 | 0.0759 | 0.0378 |
| 32 | 0.0197 | 0.0983 | 0.0444 |
| 256 | 0.0236 | 0.0749 | 0.0377 |
| 1,156 | 0.0634 | 0.0919 | 0.0385 |
| 16,384 repeated | 0.7009 | 0.3723 | 0.0718 |
| 65,536 repeated | 2.7298 | 0.5279 | 0.0806 |

At 1,156, GPU including transfers is 1.45× slower than C++; at repeated 65,536 it
is 5.17× faster. At 1,156 CPU samples span 0.0543–0.0706 ms and GPU
0.0623–0.1796 ms; at 65,536, 2.6217–2.8724 and 0.2852–0.8518 ms. GPU event
timings omit dispatch and are not end-to-end wall times. Host preparation was
1.200 ms, context/compile 219.621 ms, static upload 2.579 ms. [Raw results](results/placement-result.json).

Same-host [current Python eager scoring](results/placement-python-result.json)
took 10.510 ms. The [lazy ranker](results/placement-lazy-result.json), including
bounds, emitted 1/10/100 ranks in 4.453/5.380/7.240 ms, requiring 371/459/653 exact
calls. This replay omits real candidate admission, geometry and locality work.
Compile the scorer first; preserve the ranker. Actual traces must justify GPU
batching. Do not hardcode 16,384 as a universal threshold from repeated data.

### Route-pattern evaluation

Eight patterns/query: two L shapes and six Z shapes at quarter/half/three-quarter
bends. CPU/CUDA evaluate the same cell sequences; blocked means infinity. Source
and segment junctions count once, actual revisits again. All candidate costs match.
Five additional CUDA fixture groups pass: weighted/reversed/degenerate paths,
stable ties, all-blocked map, unreachable wall and a detour missed by all patterns.

| Queries / patterns | Unique queries | CPU median ms | GPU total median ms | CPU packing ms, separate |
| ---: | ---: | ---: | ---: | ---: |
| 1 / 8 | 1 | 0.00412 | 0.09299 | 0.0227 |
| 32 / 256 | 21 | 0.00824 | 0.17821 | 0.3126 |
| 256 / 2,048 | 21 | 0.03341 | 0.24459 | 1.9081 |
| 1,024 / 8,192 | 21 | 0.12533 | 0.26459 | 12.8057 |

At 1,024 queries CPU spans 0.11710–0.13439 ms and GPU 0.24567–0.43613 ms.
Initial map upload/allocation was 115.952 ms; compile 14.688 ms. Packing is the
same for both arms but excluded from their totals. CPU's cheap early exits and
small workload favor it. **Do not integrate this CUDA scorer.** Prefix-based
constant-time segment queries remain an untested alternative, including their
field update cost. [Raw results and oracle](results/patterns-native-result.json).

Actual worked example: U23.1 `(185,220)` to C98.1 `(186,441)` has no valid L/Z
candidate. Independent Dijkstra finds cost 247, including the source cell, after
visiting 87,099 cells. Nine of ten sampled queries show pattern miss/maze success.
This demonstrates an incomplete pattern set on the exported graph; it does not
establish a legal connection under omitted PCB rules. Pattern failure must fall
back to maze search, never declare physical impossibility.

### Dense field construction

Four int64 row/column prefix arrays for occupancy and cell cost contain 586,551
cells each, 18,769,632 output bytes total. Every element equals NumPy `cumsum`.

| Arm | Median ms | Min–max ms |
| --- | ---: | ---: |
| CPU NumPy, four fields | 6.830 | 5.610–10.189 |
| GPU resident build | 0.602 | 0.522–0.613 |
| GPU upload + build + all outputs download | 2.931 | 2.555–3.198 |

The transfer path is 2.33× faster for this operation. First build took 376.974 ms;
roughly 97 repeated builds amortize that cold build against the median 3.899 ms
saving, before other setup. This arithmetic is not a router speedup prediction.
Existing sparse/incremental caches may avoid dense rebuilds. Prefix fields do not
implement congestion demand or ownership. Retain this as a candidate primitive
only when a real consumer and incremental CPU comparator exist. [Raw results](results/prefix-result.json).

## Primary-source decisions

The sources establish mechanisms on IC workloads. None establishes PCB gains here.

| Source / section | Applicable mechanism and limitation | Decision |
| --- | --- | --- |
| [DREAMPlace, DAC 2019](https://research.nvidia.com/sites/default/files/pubs/2019-06_DREAMPlace:-Deep-Learning//54_1_Lin_DREAMPLACE.pdf), formulation/operators | Parallel analytic wirelength/density. A deep-learning toolkit does not require a trained placement model. Objective/legalizer differ from PCB constraints. | Borrow batching; no drop-in IC placer/runtime dependency. |
| [ABCDPlace, TCAD 2020](https://yibolin.com/publications/papers/ABCDPLACE_TCAD2020_Lin.pdf), §§III–IV | Independent matching/swaps/reordering. Net independence does not imply PCB mechanical/access independence. | Pure parallel costs; deterministic admission with existing legality. |
| [GAMER, TCAD 2023](https://appsrv.cse.cuhk.edu.hk/~sjlin/GAMER%20TCAD2023.pdf), §§III–V, V-C/V-E; [local copy](sources/GAMER-TCAD2023.pdf) | Directional prefix relaxation, fused searches. Bounded sweeps can miss optimal paths; fine stage is still global routing. Layer-direction assumptions differ. | Study numerical primitives; no scalar-sweep equivalence or convergence claim for our A*. |
| [FastGR, IJCAI 2023](https://www.ijcai.org/proceedings/2023/0720.pdf), Fig. 3/stages | GPU pattern work; maze rerouting remains CPU. Kernel gain exceeds flow gain. | Small analogous test completed; this CUDA mapping fails the performance gate. |
| [InstantGR, ICCAD 2024](https://shijulin.github.io/files/1239_Final_Manuscript.pdf), §§2–4; [code](https://github.com/cuhk-eda/InstantGR) | Restricted DAGs, resource-conflict batches at much larger net counts. Representative endpoint overlap allows missed conflicts. | Exact conflicts can schedule proposals. Approximate overlap cannot authorize PCB copper. |

The additional owner briefs' GPU ambitions fit the owned direction. Claimed
speedups, learned quality gains and recovered-net counts remain hypotheses.
GPU execution alone fixes neither greedy ownership nor invalid rollback/pin access.

## Proposed interfaces, state and algorithms

### Exact score batches

Proposed `src/agent_ee/placement_scoring.py::score_batch(snapshot, candidate_ids,
poses_nm_quarterturn) -> scores_nm` is a narrow numerical contract. Reference
mode calls existing functions; an optional owned compiled implementation follows
agent-ee packaging. Do not move placement policy into the router just to share a
Rust build. Stage-B has a distinct centroid objective/version and its own oracle.

`ScoreSnapshot` contains schema/objective version, board/placement hash, generation,
ground-policy hash, source-pad offsets and per-pad peer spans in signed int64 nm.
Each qualifying pad remains a term; empty/no-peer pads add zero. Check coordinate
and sum bounds before narrowing; unsupported rotations use the reference with an
explicit reason. Geometry/electrical-locality admission stays in the search.

```text
snapshot = peer index for current placement generation and objective
when ranker requests unresolved costs:
    take a bounded deterministic window of candidate IDs
    scores = exact_cpu_batch(snapshot, poses)
    reject if generation changed
    cache exact scores by (generation, source footprint, candidate ID)
    let original ranker emit with its original complete key
    run existing legality/admission
on accepted move or backjump:
    advance generation; invalidate peer-derived scores
```

Start with whole-generation invalidation; optimize to reverse net/source indices
only if measured. Prefetch cannot admit earlier or change ties. Complexity is
O(candidates × relevant pad/peer pairs), memory linear in packed inputs/output.
No score is a feasibility certificate.

Proposed experimental `score_backend=reference|cpu|cuda|auto` defaults to current
behavior until parity passes, then CPU. Explicit CUDA failure is visible; auto
records a fallback reason and recomputes the complete pending batch on CPU.
Partial device output is discarded. Reference mode remains the replay/rollback path.

### Optional resident GPU work

A proposed process-local `GpuSession` owns kernels/buffers and a memory cap. No
per-candidate SSH call. Remote research ships an immutable batch; any future remote
production flow must amortize transport across a whole search/campaign and measure it.

Resident identity includes board hash, placement epoch, rule hash, grid frame,
layers, width/clearance class, net/exemption identity, copper epoch, history epoch
and objective version. Geometry/rule changes invalidate all derived fields.
Copper commits invalidate intersecting fields/proposals. Missing delta support
means rebuild or CPU, never stale reuse. Net-specific masks cannot be shared
without preserving their exceptions. Keep arrays resident for GPU consumers;
download full arrays only for diagnostics/oracles when practical.

Proposed auto admission requires five matched real-workload replays with ≥1.2×
median stage gain including packing/transfers and ≤10% worst observed stage
regression, plus ≥5% end-to-end gain. These are **provisional engineering gates**,
not predictions. Calibrate per host/workload; do not stall the ranker to invent a
large batch. Four measured fields take 18.8 MB/layer; masks, replicas and double
buffers add memory. Reject over-budget allocation before launch.

Routing outputs proposed `RouteProposal(snapshot_id, query_id, resources, cost,
status, budget_used)`. Distinguish found, restricted-set miss, budget exhausted and
device error. CPU rechecks current width/clearance halos, holes, keepouts, via
rungs, ownership and protected copper before the main plan's atomic transaction.
Admit multiple proposals deterministically, revalidating after each commit.
Relaxed paths through removable foreign copper remain repair proposals; fixed
copper remains fixed. Preserve signal layer policy and native filled-zone checks.
The validated incumbent is independent of GPU exploratory state; device loss
discards pending proposals without changing committed geometry/connectivity.

Fleet scheduling must reserve the physical GPU against conflicting workloads;
slot-specific leases need that additional check before any shared production GPU
service. Ollama need not be disabled globally.

### Conditional maze prototype

Define a finite explicit graph before writing a GPU solver. Start with the exported
four-neighbor scalar graph and CPU BFS/Dijkstra; then add diagonals/corner rules,
headings for bends, layers and complete-stack via legality independently. Heading
state alone cannot encode arbitrary prior-via history. Represent it faithfully
or declare the solver a proposal heuristic requiring CPU validation.

Iterate to a proven fixed point for exact-graph comparisons, or return budget
exhausted/unknown. Fixed sweep counts cannot prove unreachable. One million cells
× four layers × eight headings × eight distance/parent bytes is already 256 MB
per search, before masks/scratch. Prefer bounded windows and shared immutable
masks; local failure is never full-board impossibility. No GPU maze prototype
was run in this study; its gate is below.

## Dependency-ordered implementation milestones

Effort is engineer-days, excluding unrelated electrical repairs. All new files and
configurations here are proposed. Existing main-plan M1/M4 remain prerequisites
for safe route proposal commitment.

### G0 — bounded stage traces (2–4 days)

Problem: retained totals do not isolate useful GPU work. In agent-ee, instrument
`placer.py::_Search.scan`, `_wire_cost`, `_wire_bounds`, `_ranked` and
`block_arrangement.py::_Arrangement`. In kicad-route, instrument
`single_ended_loop.py::_route_on_prepared_map`,
`routing_context.py::prepare_obstacles_inplace` and Rust `router.rs::stats_to_dict`.
Separate geometry, bounds, exact score, ranking, obstacle prep, search, commit,
export and native grading. Add aggregate counters plus one capped sample export
with source/binary hashes and generation. No search/score changes.

Add proposed `tests/test_placement_scoring_trace.py`; run it with existing
`tests/test_placer.py`, `tests/test_placer_search.py`, `tests/test_block_arrangement.py`
using `uv run --with pytest pytest ...`. Routing checks use
`cargo test --manifest-path src/kicad_route/router/rust_router/Cargo.toml` plus
its prepared-map fixture tests. Gates: tracing off identical; enabled rank and
geometry identical; counts reconcile; export size capped; five matched short
replays show provisional ≤5% median overhead. Limit each initial full target
baseline to 1.25× its comparable retained duration; timeout remains an observation.
Configuration off by default, additive versioned output, rollback by disabling.
Placement/routing instrumentation can run in parallel with main-plan correctness work.

### G1 — exact compiled CPU score (3–6 days, after G0)

Add proposed `placement_scoring.py`, owned optional compiled implementation and
`tests/test_placement_scoring.py`; adapt score callers only. Retain bounds,
ranker, geometry, locality and backjumping. Stage-A first; Stage-B separate oracle.
Packaging and preserving lazy behavior are the main uncertainties.

Replay this dataset and actual G0 requests. Test rotations/nonzero initial rotation,
duplicate pads, missing peers/ground-only, negative coordinates, multi-pin shared
nets, move/backjump invalidation and integer overflow refusal. Require every score
and complete rank sequence identical. Run
`uv run --with pytest pytest tests/test_placement_scoring.py tests/test_placer.py tests/test_block_arrangement.py`.
Five matched replays must show provisional ≥2× score-stage median gain and no
full placement regression >5%. Optional extension absence retains recorded
reference operation; reference backend is rollback. No native completion gain
is required for this behavior-preserving acceleration.

### G2 — resident CUDA score/field study (3-day initial limit; 3–7 if promoted)

Depends on real G0 traces; score promotion compares against G1. Extend this research
directory first, adding residency/compact output and comparing incremental CPU
field work. No dense rebuild without a consumer. Test exact results, stale epoch,
device loss, memory cap and deterministic ties. Five matched cold/warm complete
replays must meet the 1.2× stage/5% overall gate above. Failure retains research
only. CUDA remains optional and disabled by default; CPU fallback discards device
state. Field research can run alongside G1, but integration needs a measured consumer.

### G3 — GPU maze feasibility (conditional, 5-day first experiment)

Start only if G0 shows search ≥50% of matched backend runtime after preparation/
repair fixes and several real searches can batch without delaying dependencies.
Proposed research `maze_oracle.py`/`maze_cuda.py`: scalar exact graph, then the
state extensions above. Tests: open field, wall, long serpentine beyond fixed
sweep counts, diagonal pinch, via blocked on nonsignal layer, multisource escape.
Require reached-set/distance/path parity, unknown reported separately, and
provisional ≥1.5× total search gain including export/transfers at actual windows.
Stop otherwise. Estimate 1–2 weeks scalar study, 3–6 more weeks for faithful PCB
state/integration, with substantial abandonment risk. CPU fallback remains;
GPU reachability never marks a native connection complete.

### G4 — native-board integration (3–5 days after successful prototype)

Use existing placement callers or the owned prepared-search/transaction caller,
campaign incumbent and grading. No Freerouting changes. First compare routing on
identical frozen placement, then placement under identical router settings, then
end-to-end. Five matched orders/seeds, baseline-derived equal budgets. Preserve
inputs/outputs/settings/logs/native DRC. Keep GPU opt-in until all gates pass and
record backend choice per run. Rollback restores CPU and best validated board.

Acceptance preserves hard constraints and introduces no shorts/clearance errors
or lost required connections. Prioritize remaining native connections, then
incomplete nets, then route quality/runtime; never conceal connection loss in a
composite score. Exact-score acceleration requires identical chosen placement.

## Validation, falsification and remaining uncertainty

Incomplete nets are distinct required net identities with disconnected native
copper components; remaining connections are native unconnected items/edges,
recorded separately. Record DRC categories with introduced/pre-existing identity,
length in mm, vias, per-layer copper, process/backend/native-check time and repair
attempts/victims/rollbacks. Preserve expected connectivity and starting copper.
KiCad connectivity/DRC remains final authority; a pass does not authorize signal
routing on planes or electrical-rule changes.

Detect saturated native reports. Absence from a capped report is not proof of net
completion; require native terminal-component coverage and report unknown when
coverage is incomplete. Do not label a component deficit as a complete DRC count
unless that correspondence has been verified for the saved board.

This study emits no routed board and claims no measured change in native
completion, DRC, trace length, vias or layer use. Falsifiers and stop conditions:

- Small actual scoring fraction or compiled CPU removing most cost ends GPU score
  integration, regardless of repeated-query throughput.
- An incremental CPU operation that avoids dense rebuild ends the corresponding
  GPU field proposal unless a different measured consumer justifies it.
- Faithful maze state exceeding memory or destroying batching efficiency ends
  that prototype; retain owned CPU search.
- Any parity, epoch or native-connectivity failure blocks promotion regardless of
  speed. GPU cannot relax required widths, clearances, drills or expected nets.

Unmeasured: actual scan score fraction, Stage-B GPU objective, GPU maze/via/layer
legality, end-to-end GPU boards and cross-host variation. The pattern scorer is
rejected for integration; dense fields and large resident batches are experiments.
Primary correctness/blocker/transaction work must not wait for GPU research.

## Reproduction and first implementation task

See [commands](REPRODUCE.md), [prompt](PROMPT.md), [manifest](results/run-manifest.json)
and `python3 docs/research/2026-10-21-gpu-pcb/verify.py`. Saved inputs replay without
moving repository heads; the exporter records observed source/binary identities.
GPU errors are visible; CPU self-tests run independently.

**First task: implement G0's bounded placement scoring trace in agent-ee.** Add
opt-in aggregate timing/counts around `_Search.scan`'s existing bounds, exact cost
and rank operations, plus one capped replay of real candidate IDs, poses, peer
identity, objective and placement generation. Acceptance: disabled output
unchanged; enabled rank/placement unchanged; exact `_wire_cost` replay; skipped
and resolved candidates counted; stale generations rejected; capture bounded;
provisional ≤5% median overhead in five matched short runs. Make no placement
algorithm or GPU dependency change in this task.
