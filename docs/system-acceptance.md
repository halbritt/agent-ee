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

Latest direct owner priority: "if you can get the whole thing working we can
then iterate on optimization". First qualify a complete correct run through
the normal build entrypoint. Placement quality, copper length, via count,
runtime and comparative optimization follow; required correctness and
manufacturing checks remain gates.

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
`agent-ee place run`. The new [build entrypoint](build-workflow.md),
`agent-ee build run`, has fresh native execution through schematic-to-PCB
generation and resumption; its complete manufacturing path remains unqualified. The following source
observations must not be read as fresh execution evidence.

| Stage | Real input → output; existing path | Required constraints and missing capability | Acceptance evidence / current state |
| --- | --- | --- | --- |
| Intent and requirements | Spec + authored requirement record → scope/baseline; `design_intent`, `design_dialog`, `requirement_scope`, `spec_record`, `build_workflow` | Distinguish requirements, assumptions, preferences, defaults and unresolved decisions; preserve each requirement's check mapping. RC regex parser in `generate.parse_spec` is not general design synthesis. | `agent-ee build run` now carries spec-record/map output into parts and actual native scaffolding; complete sourced engineering qualification remains open. The legacy requirements-start script still stops at absent parts. |
| Architecture, circuits, parts | Captured catalog/selection/CAD closure → `parts/1` → bound connectivity; `agent-ee parts compile`, `parts_stage`, `binding_fill`, connectivity IR and electrical checks | Select and substantiate topology, values, ratings, package/pin map and sourcing; carry calculations and provenance into downstream checks. Generic declared parts producer is connected to the public build; sourced architecture and full workflow qualification remain open. | Targeted native child-sheet exercise consumes actual parts output through schematic and PCB. Synthetic sourcing evidence only; no fresh sourced multiblock intent-to-parts run. |
| Native schematic | Connectivity intent + captured libraries/layout → native sheets; `schematic_emitter`, `schematic_stage`, `schematic_regenerate`, forward annotation | All pins accounted for; native connectivity agrees with selected parts; editable hierarchy and dependencies; stable regeneration. Explicit hierarchical stage output sets now supported; preserving manual edits during regeneration remains flat-only. | Fresh native stage exercise carries a child value edit and cross-sheet nets through receipts to the PCB; stale child and omitted output reject. Complete execution through the new build entrypoint remains unqualified. |
| Board/mechanics | Envelope + parts + scope/plan → native empty project, fixed mechanics; `agent-ee scaffold`, `scaffold.content_step` | Preserve outline, stackup, locks, holes, keepouts, heights/access and rule provenance; every footprint assigned a block/shared/fixed role. | Public scaffold can now consume bound intent/layout and preserve emitter sheet identities without replacing authored project rules. No fresh whole workflow proving all mechanical constraints survive placement and routing. |
| Placement/legalization | Native board + floorplan intent/groups → candidate poses/board; `placement_job`, `placer_runner`, `blocks`, Rust engine | Block and pin locality, permitted sides, anchors, body/courtyard/hole/access keepouts; final coordinates establish compliance. Distinct planners currently have different supported domains. | Saved-board edge enforcement and terminal-space preferences exercised. Latest esp_prog run passes all three declared edges and has zero open connections. Full intent qualification remains open. |
| Routing/repair | Placed native project + routing policy → saved copper; `routing_adapter`, `kicad_route_attempt`, `placement_job.route` | Retain valid geometry and all native rules; route selection/feedback must revise permitted placement/circuits and reject stale feedback. | Short explicit coupled-pair case and complete esp_prog routing pass native checks. Portfolio rejection/retry is covered by controller fault injection; this full board needed one routing trial. Broad KRT coverage and placement/circuit repair remain unqualified. |
| Electrical verification | Emitted native netlist + sourced requirements/models → individual checks; `checks`, passive/active SPICE, pin contracts, ratings/DC-path checks | Consequential functional electrical errors must fail even with clean ERC/DRC; missing/stale/skipped required checks cannot pass. | Historical RC wrong-value evidence and bounded electrical modules. Fresh integrated multiblock functional proof missing. |
| Physical/assembly verification | Final reloaded board + declared intent/parts/profile → native DRC, connectivity, placement and assembly reports | Refill zones; check final grouping/sides/pins/mechanics, preservation and every required coverage item. Annotated top/bottom views required. | Latest esp_prog has zero native errors/opens, 258 warnings and eight geometric via-in-pad sites; manufacturing disposition outstanding. Native 3D previews expose overlapping underside silkscreen; five legacy model bodies are unavailable. |
| Manufacturing | Accepted native project + profile/population/rotation inputs → Gerbers, drills, BOM, CPL, bound package; `fab_export`, `fab_stage`, `fab_package`, `fab_consistency`, `fab_roundtrip` | Exact native/package identity, current exports, layers/outline/drills, parts/population, coordinate/orientation validation. Physical and supplier-preview obligations explicit. | Existing release gates; no newly accepted package for this goal. |
| Operation | Installed CLI + captured inputs → bounded diagnostics, checkpoints, replay | One documented ordinary intent-to-package command; clean setup, interruption/resume, truthful failures; subscription routes only when inference is used. | Installation/doctor and placement-job checkpoint implementations exist. No integrated end-to-end resume qualification. |

Fresh baseline evidence is under `build/agent545/requirements-baseline/`:
requirements `completed`, parts `absent`, all later stages `not_reached`,
`done:false`. This is a real supported-environment run, not a mocked stage
ledger. Preserve its exact record and logs in the durable evidence bundle before
retiring build output. The retained predecessor evidence is
`~/.cache/agent-ee-evidence/agent544-tooling-repair-20261024/`; see
[placement-job report](benchmarks/placement-job-2026-10-24.md).

The interval calculation checker now accepts explicit, hash-verified source
bindings to the requirement compiler's captured specification. A real
spec-record → map → candidate capture → sandbox check exercise passes the
declared divider case and rejects a wrong-resistor case. Missing bindings,
wrong locators and altered source bytes reject. This closes the source-binding
interface; it does not derive equations from circuits or qualify the fresh
multiblock design. The focused interval, requirement-map, parts-stage and CLI
suite passes 70 tests in the native executor, with no skips; retained log:
`build/agent545/interval-source-final.log`.

The fresh sourced acquisition design now runs through public requirements,
parts, scaffold and schematic stages into a native 17-component PCB with three
functional blocks. Independent native review passes ERC, exact topology,
selected parts and assembly properties/rating provenance. It has 37 open
connections before placement and is not accepted. The complete package path
and functional electrical verification remain open.

Its first placement exposed a reusable defect: functional groups rejected
their fixed connector members. The corrected engine includes physical fixed
members in group identity/cost, starts clustered groups at fixed anchors, and
translates only movable members. The strict Rust build passes 100 tests; the
native job/guard/capture suite passes 43 tests with no skips. The second actual
attempt preserves all three fixed connectors and passes edge and native
clearance checks, but rejects two open connections at U2. The 73.6-second run
is at `build/agent545/acquisition-placement-02`; final board SHA-256
`bbd530173f2d5278b61226dc8ab193a2e1f6f859ed8d1f2a900be104850f3844`.
Proximity scoring is still a preference, not final pin-locality acceptance.
No manufacturing package or broad KRT parity is established by this result.

Named resistor/capacitor selection now checks the circuit nominal against the
exact captured supplier attribute, closing a bypass of generic selection's
value check. The public build regression consumes a named resistor through
native PCB generation and rejects its wrong-value variant in the parts stage.
The related parts, ratings, selection and native-binding suite passes 89 tests
with no skips (`build/agent545/named-nominal-final.log`). Supplier claims remain
distinct from verified datasheet applicability and functional circuit proof.

## KRT comparison inventory

Initial observed upstream main on 2026-10-24:
[`222bb144744c3ddd979674e528bf8647e9ced8d6`](https://github.com/drandyhaas/KiCadRoutingTools/tree/222bb144744c3ddd979674e528bf8647e9ced8d6).
Reference clone: `~/.cache/agent-ee-qualification/upstream/KiCadRoutingTools`.
This is distinct from the initial installed kicad-route checkout
`b5774496d8466b67ffa0362b6665fee2108155a3` and its owned/patched engine.
The [detailed source-backed inventory](research/krt-2026-10-24/krt-latest-capability-map.md)
covers 69 capability rows, 107 declared tools and 127 hash-bound source files.
Its [proposed exercises](research/krt-2026-10-24/qualification-cases.md) remain
unexecuted; the source audit is not parity evidence. The following required
families are all **unqualified for parity** until matched exercises run.

The later observed main is `d24a7602012b96c5cfa610ea05a171646ad601bd`;
the [sealed additive source audit](research/krt-2026-10-24/d24a760/delta.md)
finds only a documentation change. A
[matched native pair diagnosis](benchmarks/differential-escape-2026-10-24.md)
now exercises stock and owned engines on identical esp_prog inputs. Both fail
the required terminal-coupling condition. This establishes a matched failure,
not capability parity or a passing qualification design.

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
   Direct owner request: after the board passes its required checks, provide
   top and bottom 3D renders of the accepted saved native board. Renders do not
   replace verification or prove physical assembly fit.
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
preparation or require preassembled campaign inputs. The new public build now composes the existing producers and has executed
requirements through a real native PCB with resumption. Its complete placement,
verification and manufacturing path must still be exercised and repaired.

The [generic parts producer](parts-stage.md) now composes existing manifest,
binding and connectivity validators and exposes `agent-ee parts compile`. Its
ordinary receipted output reaches hierarchical schematic emission and native
forward annotation. It also repairs silent omission of child component bindings
in `binding_fill`. This producer is now invoked by the public build. A complete build through
manufacturing and a fresh sourced multiblock engineering run remain unmet.

The build now carries passive-rating evidence from the adopted parts receipt
into the assembly check's immutable inputs. Authored requirement/check mappings
remain unchanged apart from the derived receipt binding, with both original and
derived baseline identities retained. A real resumed child-sheet build exposed
and repaired the checker's sheet-name/UUID lookup mismatch; independent native
assembly verification now resolves both passive records. Modified report bytes
refuse provenance verification and build resumption. This is synthetic integration
evidence, not sourcing or electrical acceptance of the fresh design.

The scaffold-to-emitter boundary now derives native filenames, occurrence and
document UUIDs from actual emitter output using paired `--intent`/`--layout`
inputs. The native integration case consumes the parts producer's real output,
creates every schematic through public scaffolding, and preserves project rules
through emission and annotation. Empty nested sheets are covered. This removes
manual native placeholder assembly from that exercise; it does not close the
complete workflow or electrical/manufacturing qualification.

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

The integrated hierarchy repair removes the flat-only stage-runner and presence-gate
obstructions to hierarchical schematic production. The
[native hierarchy exercise](benchmarks/hierarchical-stages-2026-10-24.md) carries
an edited child sheet into the real PCB and exercises public scaffold/review
gates. It is a targeted feature qualification, not the fresh multiblock system
run. Parts/circuit production and the complete build entrypoint remain next.

The [terminal-space preference](benchmarks/differential-escape-2026-10-24.md)
now survives native capture and Rust placement search, with 99 strict Rust and
82 native regression tests passing. A fresh public esp_prog run has a feasible
placement with clear inferred terminal regions, but still rejects two open pair
connections. The current dependency update to kicad-route
`be1f3a831ac275bb0dc5f8b70bfb9a1afa5e9bfa` fixes short-leg deferral and
generated pad tails, qualified through its normal native entrypoint. Agent-ee's
39 re-pin checks pass with the rebuilt executor. Its placement controller now
tries each distinct eligible shortlist candidate after a routing/final-check
failure, retaining every trial. Native job/guard/resume checks pass 39 tests;
three focused controller checks also pass. These invocations overlap.

A fresh six-seed, 100,000-step, 1,200-second public esp_prog exercise completed
in 720.816 seconds at `build/agent545/board-run-portfolio`. Native checking
rejected the cheapest placement for two solder-mask bridges and selected
seed 11. Its first routing trial passed: zero native errors/opens, all three
declared edges preserved, required coupled pair routed without pad-net swaps.
See the [routing portfolio evidence](benchmarks/routing-portfolio-2026-10-24.md)
for source identity, commands, test results, renders and remaining checks.
The increased search budget is an agent-selected experiment, not a matched
performance comparison. Manufacturing acceptance remains open. The source audit
and earlier matched failures do not close KRT parity. Parts/circuit production and the complete
build entrypoint remain required independent work.
