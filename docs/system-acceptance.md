# Intent-to-manufacturing acceptance map

Status: **UNFINISHED**, opened 2026-10-24 by agent-545. This is the working
acceptance map for the owner's direct continuing delivery goal, not a release
certificate. A passing module test, historical ticket closure or routed board
does not complete a row. Native saved outputs and the normal user entrypoint
must establish every required result.

The owner authorized implementation, fresh verification and trunk integration
by one executing agent. A later direct instruction authorizes one research
sub-agent to map latest KRT; it does not recreate the historical dispatch
hierarchy. Live telemetry-pod attempts remain held. Purchases, orders, uploads,
physical bench measurements and supplier previews are not performed by this
software qualification. Requirements and their severities remain intact.

## Intended supported domain

The qualification target is conventional low-voltage FR-4 electronics: two,
four and six copper layers; through vias; surface-mount and through-hole parts;
regulated power, analog conditioning, ADC/digital interfaces, clocks, differential
signals and parallel buses. The workflow must support hierarchical native
schematics, multiple interacting functional blocks, declared top/bottom choices,
mechanical/access restrictions, QFN and conventional BGA escape, planes and
manufacturing profiles. Limits must follow measured geometry, tool contracts and
engineering requirements. Existing placement-job refusals of zones, non-default
netclasses and nonrectangular outlines are **gaps to close**, not exclusions
that satisfy this goal.

Mains isolation, flex/rigid-flex, blind/buried microvias, advanced packaging and
arbitrary microwave design are outside this bounded initial domain. This does
not exclude declared RF-module antenna keepouts, controlled impedance or return
paths. Software can deliver checked files with explicit physical/supplier
obligations; it cannot establish those obligations by declaring them done.

## Actual paths and acceptance

Baseline source: `6122503daf82180ceae07b598593830a96614587`.
`agent-ee` resolves to `agent_ee.cli:main`. Its production commands currently
center on review/campaign/scaffold. The seven-stage generators are separate
module/script entrypoints. This repair exposes the placement job as
`agent-ee place run`; the complete build entrypoint remains missing. The following source
observations must not be read as fresh execution evidence.

| Stage | Real input → output; existing path | Required constraints and missing capability | Acceptance evidence / current state |
| --- | --- | --- | --- |
| Intent and requirements | Spec + authored requirement record → scope/baseline; `design_intent`, `design_dialog`, `requirement_scope`, `spec_record` | Distinguish requirements, assumptions, preferences, defaults and unresolved decisions; preserve each requirement's check mapping. RC regex parser in `generate.parse_spec` is not general design synthesis. | Fresh `scripts/rung1_stages.py run --requirement-record` compiles requirements, then stops at absent parts. General supported entrypoint missing. |
| Architecture, circuits, parts | Captured catalog/selection/CAD closure → `parts/1`; `catalog`, `parts_manifest`, `rung1_parts`, connectivity IR and electrical checks | Select and substantiate topology, values, ratings, package/pin map and sourcing; carry calculations and provenance into downstream checks. `rung1_parts` projects exactly three RC roles; stage driver lacks a declared parts producer. | Existing modules and historical AGENTEE-101; no fresh multiblock intent-to-parts run. |
| Native schematic | Connectivity intent + captured libraries/layout → native sheets; `schematic_emitter`, `schematic_stage`, `schematic_regenerate`, forward annotation | All pins accounted for; native connectivity agrees with selected parts; editable hierarchy and dependencies; stable regeneration. Mutable-intent stage currently flat-only. | Source inspected; stage-level native tests exist. Must exercise hierarchy and changed circuit through the integrated entrypoint. |
| Board/mechanics | Envelope + parts + scope/plan → native empty project, fixed mechanics; `agent-ee scaffold`, `scaffold.content_step` | Preserve outline, stackup, locks, holes, keepouts, heights/access and rule provenance; every footprint assigned a block/shared/fixed role. | Existing scaffold/campaign path. No fresh whole workflow proving these survive placement and routing. |
| Placement/legalization | Native board + floorplan intent/groups → candidate poses/board; `placement_job`, `placer_runner`, `blocks`, Rust engine | Block and pin locality, permitted sides, anchors, body/courtyard/hole/access keepouts; final coordinates establish compliance. Distinct planners currently have different supported domains. | Retained esp_prog run: zero native errors/opens, but CON1/CON2 declared east and measured nearest south; no complete intent qualification. |
| Routing/repair | Placed native project + routing policy → saved copper; `routing_adapter`, `kicad_route_attempt`, `placement_job.route` | Retain valid geometry and all native rules; route selection/feedback must revise permitted placement/circuits and reject stale feedback. | esp_prog route exercised. Routing-guided placement repair and broad KRT coverage not demonstrated. |
| Electrical verification | Emitted native netlist + sourced requirements/models → individual checks; `checks`, passive/active SPICE, pin contracts, ratings/DC-path checks | Consequential functional electrical errors must fail even with clean ERC/DRC; missing/stale/skipped required checks cannot pass. | Historical RC wrong-value evidence and bounded electrical modules. Fresh integrated multiblock functional proof missing. |
| Physical/assembly verification | Final reloaded board + declared intent/parts/profile → native DRC, connectivity, placement and assembly reports | Refill zones; check final grouping/sides/pins/mechanics, preservation and every required coverage item. Annotated top/bottom views required. | esp_prog retains 262 warnings and six geometric via-in-pad sites; requirements-based disposition outstanding. |
| Manufacturing | Accepted native project + profile/population/rotation inputs → Gerbers, drills, BOM, CPL, bound package; `fab_export`, `fab_stage`, `fab_package`, `fab_consistency`, `fab_roundtrip` | Exact native/package identity, current exports, layers/outline/drills, parts/population, coordinate/orientation validation. Physical and supplier-preview obligations explicit. | Existing release gates; no newly accepted package for this goal. |
| Operation | Installed CLI + captured inputs → bounded diagnostics, checkpoints, replay | One documented ordinary intent-to-package command; clean setup, interruption/resume, truthful failures; subscription routes only when inference is used. | Installation/doctor and placement-job checkpoint implementations exist. No integrated end-to-end resume qualification. |

Fresh baseline evidence is under `build/agent545/requirements-baseline/`:
requirements `completed`, parts `absent`, all later stages `not_reached`,
`done:false`. This is a real supported-environment run, not a mocked stage
ledger. Preserve its exact record and logs in the durable evidence bundle before
retiring build output. The retained predecessor evidence is
`~/.cache/agent-ee-evidence/agent544-tooling-repair-20261024/`; see
[placement-job report](benchmarks/placement-job-2026-10-24.md).

## KRT comparison inventory

Observed upstream main on 2026-10-24:
[`222bb144744c3ddd979674e528bf8647e9ced8d6`](https://github.com/drandyhaas/KiCadRoutingTools/tree/222bb144744c3ddd979674e528bf8647e9ced8d6).
Reference clone: `~/.cache/agent-ee-qualification/upstream/KiCadRoutingTools`.
This is distinct from agent-ee's installed kicad-route checkout
`b5774496d8466b67ffa0362b6665fee2108155a3` and its owned/patched engine.
The [detailed source-backed inventory](research/krt-2026-10-24/krt-latest-capability-map.md)
covers 69 capability rows, 107 declared tools and 127 hash-bound source files.
Its [proposed exercises](research/krt-2026-10-24/qualification-cases.md) remain
unexecuted; the source audit is not parity evidence. The following required
families are all **unqualified for parity** until matched exercises run.

| Required family | Upstream executable/source surface | Agent-ee evidence needed |
| --- | --- | --- |
| Functional placement and intent | `py_placer/place_simple.py`, `placement/{groups,floorplan,edge_seat,simple_anneal}.py`; seed portfolio and quick routing | Matched native inputs; final per-block/pin/edge/side checks; route-informed choice and repair |
| Multilayer routing and recovery | `py_router/route.py`, net ordering, Rust pathfinding, rip-up/reroute | Obstacle/keepout and layer-policy cases; failed route retained and successful recovery verified |
| Power and planes | `route_planes.py`, `repair_planes.py`, plane region/component machinery | Width/neck-down, filled connectivity, disconnected regions, stitching, return-path evidence |
| Differential and matching | `route_diff.py`, `impedance.py`, length/time matching | Pair polarity/width/gap, declared stackup, skew/delay and via-aware measurements |
| Buses | Bus detection/routing and guide corridors | Real constrained bus with final connection/order/matching checks |
| Fanout | BGA/QFN fanout and `place_fanout_clearance.py` | Native QFN/BGA cases with escape geometry, thermal/decap constraints and no unexplained connectivity changes |
| Cleanup/reconnection | `cleanup_pipeline.py`, KiCad-oracle reconnect | Preserve existing valid copper; reject orphan, gap and ripped-but-unrestored outcomes |
| Inspection/replay | Native checkers, renders, `run_plan.py`/headless plan | Actionable reports, annotated final views, replay without hidden manual intervention |

Matched comparisons must state input/rule hashes, observed software/environment,
seeds, CPU/memory and overall time budget. Correctness and required coverage
precede length/vias/time. Historical stock v0.23.7 results are preserved but are
not a current matched comparison. No superiority claim exists.

## Bounded qualification suite

1. **esp_prog regression:** retain original input and prior failures; verify
   connector intent, fixed fiducials, native geometry and current routing.
2. **Fresh multiblock acquisition interface:** requirements for protected
   low-voltage input power, regulation, analog filtering/ADC, host digital
   interface and mechanical connectors. Generate real circuits, parts,
   hierarchical schematic, board and manufacturing package from the preceding
   outputs. Component choices and numeric limits will be sourced and recorded
   before implementation; no fixture-native substitution.
3. **Four/six-layer interconnect case:** differential pair and parallel bus,
   stackup/impedance, time/length matching, planes and return paths, deliberate
   placement congestion requiring routing feedback.
4. **QFN/BGA escape case:** conventional packages, power/thermal connections,
   decoupling, plane islands, stitching, obstacle and existing-copper preservation.
5. **Negative/recovery variants of these designs:** incomplete route,
   consequential electrical error, violated placement requirement, stale or
   inconsistent manufacturing files; interrupted execution and clean resumption.

Cases 3 and 4 may be combined where one design genuinely covers both. Targeted
feature fixtures fill explicit gaps only. Each accepted case needs individually
passing mandatory checks, exact native/package hashes, runnable commands and
honest physical/supplier obligations. A missing case stays unmet.

## Current iteration and continuation

Most consequential integration finding: the existing requirements-start path
does not produce the parts stage; other generation entrypoints use RC-specific
preparation or require preassembled campaign inputs. This must be repaired by
composing and exercising existing producers through the public workflow, not by
renaming experimental records or installing adapters.

The first correction makes declared connector edges hard constraints, including
fixed connectors and declarations without numeric setbacks, and checks them
again on the saved candidate and final board. Four correct-edge and twelve
wrong-edge native controls establish acceptance/rejection despite clean DRC;
an asymmetric courtyard case establishes separate body-seat and packing geometry.
The public job now refuses completion when native verification fails or there
is no feasible incumbent. See the [iteration evidence](benchmarks/placement-intent-2026-10-24.md).

Fresh esp_prog runs preserve declared edges. The six-seed, 30,000-step run found
a feasible incumbent but rejected the final board with two open USB-pair nets:
the router proposed a coupled trunk with uncoupled terminal escapes, and the
existing pair policy correctly discarded it. Native rules and that guard were
not relaxed. Full grouping, manufacturing and matched stock parity remain open.

Next integration action after this bounded repair: compose the requirement,
parts/circuit and native schematic producers into one public resumable build
path, starting with an original multiblock design. Reuse current receipt,
preservation and check implementations. Keep all rows above open until native
exercises establish their full criteria.
