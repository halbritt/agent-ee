# Intent-to-manufacturing acceptance map

**UNFINISHED — 2026-10-24, agent-545.** Correct operation through the ordinary
entrypoint comes before optimization. One executing agent owns implementation,
verification and integration. The requested KRT research and retired-campaign
cleanup are complete. Live telemetry-pod attempts remain held. Purchases,
orders, uploads, physical tests and supplier previews have not been performed.
Requirements and severities remain authoritative; defaults are agent selections.

## Supported qualification domain

Conventional low-voltage FR-4 electronics, two/four/six copper layers, through
vias, SMD and through-hole parts, regulated power, analog/ADC interfaces, clocks,
differential signals and buses. Required coverage includes native hierarchical
schematics, functional blocks, permitted board sides, pin locality, mechanical
and antenna keepouts, QFN/BGA escape, planes and manufacturing profiles.
Current limitations on existing placement-input zones and nonrectangular outlines
are implementation gaps to close, not convenient exclusions. Native netclasses/custom
rules now traverse normal placement and routing, with a bounded paired article qualified.

Mains isolation, flex/rigid-flex, blind/buried microvias, advanced packaging and
arbitrary microwave design are outside this bounded initial domain. Declared
controlled impedance, return paths and RF-module restrictions remain in scope.

## Actual production path and evidence

Normal entrypoint: [`agent-ee build run PLAN --out RUN`](build-workflow.md).
Each producer consumes the preceding receipt and actual native outputs. Explicit
`--through artwork` permits diagnosis of an incomplete manufacturing baseline;
default package execution still refuses missing required coverage. Passing
individual checks never means the whole design is accepted.
`agent-ee build review RUN --out DIAGNOSTIC` now selects the last completed
native candidate and its generated scope directly. The actual acquisition
positive and wrong-address negative have exercised this public command.

| Stage | Real inputs → outputs / production implementation | Required constraints, exercised capability and remaining acceptance |
| --- | --- | --- |
| Intent and requirements | Authored specification and requirement/check map → captured requirements/scope; `design_intent`, `spec_record`, `requirement_scope`, build requirements stage | Preserve requirements, assumptions, defaults and unresolved decisions. Exact clause partitions now connect partial coverage while retaining unresolved siblings and physical-only restrictions. The acquisition specification reaches all seven stages and its mandatory design clauses are closed by engineering review plus emitted-file checks. General free-text engineering synthesis remains open. |
| Architecture, parts and circuits | Circuit/connectivity intent, captured sourcing/CAD/rating records → bound parts and native pin inventory; build parts stage, `parts_stage`, `binding_fill` | Source parts, topology and electrical calculations. Actual selected TPS7A20/ADS1115 circuit is carried into the board. Consequential reversed-diode negative rejects despite clean ERC. The acquisition load, capacitance, startup/removal and other electrical obligations now have reviewed source/model evidence and final native checks; other circuit families remain unqualified. |
| Native schematic | Parts receipt + connectivity/layout → editable native project and dependent sheets; scaffold/schematic stages, `schematic_emitter`, forward annotation | Stable identity, exact pin/footprint/net agreement, all pins accounted for, native rules preserved. Fresh 17-part design passes ERC and exact topology. Hierarchical regeneration/child edits exercised separately; preserving arbitrary manual edits remains limited. |
| Board and mechanical intent | Native project + envelope, anchors, blocks, side and locality declarations → placed native PCB; scaffold, `placement_job` | All 17 acquisition footprints belong to three blocks; all three fixed connectors and eight hard pin distances pass after routing. Acquisition package bodies, height, connector access and board thickness are reviewed and checked. Broader side/hardware/keepout cases remain open. |
| Placement and routing feedback | Placement constraints + seed/route policy + captured planes → saved board, trials, final-placement reports; `placement_job`, Rust planner, routing adapter | Accepted acquisition with a bottom ground plane and esp_prog both route with zero native errors/opens. Declared outer/inner planes now reach the normal job and independent final native checks; two/four-layer feature cases pass. The retained two-sided acquisition variant now passes the normal job with automatic targeted thermal reservation and scoped dangling-net reroute; earlier failed attempts remain evidence. The four-layer/default-via regression and all 44 required pin modules pass. Native netclasses/custom rules now survive normal placement/routing and the build producer; a four-layer paired article and length/skew/rule-loading negatives pass. The asymmetric intra-pair matching article now passes the public job and build producer, with a connected-skew negative; all required pin gates have passing evidence. Existing-zone placement, pair layer transitions, inter-pair/time matching, buses/fanout and broader constraint coverage remain open. |
| Electrical verification | Final native netlist/PCB + source-bound calculations/models → individual checks; `topology_rules`, interval checks, passive/active SPICE, `dc_path` | Five independent ADC pin rules now enter the normal build's generated scope; wrong address-pin wiring rejects despite passing ERC and generator parity. Selected parts and assembly checks pass acquisition. Corrected ngspice 47 passes 139 native regressions and unchanged actual startup. Whole-net copper checks exercise two/four-layer controls, supply/return copper and disconnected/exceeded-budget negatives. Acquisition material/contact assumptions and component applicability are explicitly reviewed; model limits and unperformed physical obligations remain visible. Clean ERC is not functional acceptance. |
| Physical/assembly/fabrication checks | Final reloaded/refilled board + authoritative rules, population/profile/placement intent → native and independent results | Acquisition has zero unwaived DRC findings and zero center-in-pad vias. Final grouping/locality is checked. Acquisition mechanical/fabrication obligations are closed with dimensioned/source review and final checks. Remaining routing-feature cases are unmet. |
| Manufacturing package | Accepted native identity + manufacturing profile → Gerbers, drills, BOM, CPL, documentation and bound validation; package stage, `fab_export`, `fab_package`, `fab_consistency` | The 17-part acquisition design is accepted through the default seven-stage build: 37 passing checks plus informational netlist, 9 plots/2 drills/12 BOM rows/17 placements. The eleven engineering obligations are closed with explicit assumption corrections; changed exported coordinates reject as stale. Supplier preview and physical tests are unperformed. |
| Operation and reproducibility | Clean installation + captured plan → bounded run/journal/resume/diagnostics; installer, doctor, build workflow | Receipted resume, source/runtime drift refusal and targeted negative cases exercised. Corrected native-image 47 passes the targeted requalification and both ordinary RC integration gates. Fresh captured-intent-to-package acquisition run and CANCEL interruption/resumption now pass in fresh native containers. A rebuilt clean tool environment for the complete broader suite remains required. |

## Current integrated state and evidence

The [acquisition delivery milestone](qualification/acquisition-closure-2026-10-24.md)
is accepted: `build/acquisition-release/release-build-v7`, seven normal stages,
37 passing checks plus informational netlist, final PCB `d0a898a2a254…`, matching
fabrication/assembly outputs, eight power corner exercises, source/geometry
review, meaningful negatives and verified interrupted/accepted resumption.
Delivery includes the native project, operating instructions, captured inputs,
engineering conclusions, native 3D views and file identities. The public build
review command was integrated at `466e6feb`; its wrong-address negative passes
ERC/parity but fails the independent ADC tie rule. No mandatory acquisition
design question remains unresolved under the explicitly corrected specification.
The subsequent broader capability programme remains unfinished.

The records below describe earlier checkpoints, including then-valid refusals;
they are preserved as history and superseded for this acquisition release.

Integrated simulator repair: `54864697c224fa1e7464719a3bd9b67e47752adf`;
kicad-route `80e144437b48a3de77b226d0e9465c16b9ab8b7e`.
That iteration replaced ngspice 42 with corrected maintained 47. The supported
image passes 139 native regressions, 20 switch checks, both RC integration gates
and the unchanged actual acquisition startup case. The observed image and binary are recorded in the simulator report.

Integrated `7dba3a7c42cd701cd0cf0d2033dc4d21e91bb756` adds `dc_path/2` through the existing public review
workflow. The unchanged acquisition board's ideal copper bounds are 1.556 mV
supply and 3.551 mV return at declared 20 mA/25 °C. Every native conductor is
accounted for; full DRC/parity is a prerequisite. Independent two/four-layer
controls, disconnected-route and exceeded-bound cases are retained at
`build/agent545/dc-rail-public`. These nominal model conditions do not close
physical manufacturing tolerance, contact/spreading or full electrical acceptance.
Seventy relevant native tests pass with no skips; the committed-revision control
also passes both checks, evidence verification and the checks-only gate.

The actual fresh TPS acquisition build used agent-ee `9a12901a` and completed
requirements, parts, scaffold, schematic, placement-routing and artwork in
`build/agent545/acquisition-flow-build-native`. Final PCB SHA-256:
`e802bb4a77374a33acf1d40e4b0551befa3dc92c64dd1340103da44182a26087`.
Seed 6 won the bounded six-seed search: 279.455 s, 199 segments, 10 vias, 216.6868 mm.
Those are observations, not comparative optimization or manufacturing acceptance.
Independent review passes ERC, DRC, topology, selected parts and assembly data.
That run's default package resume rejected five missing manufacturing coverage
kinds. No native board or component values were substituted by hand.

The subsequent manufacturing-bound build at `ab1f7e93` connects those existing
check kinds and regenerates all six stages. Its final PCB is
`8b441809e8ca8073f0aeb2bc90563452ccee60bbe227562a3e10b433a9873992`;
all 20 declared independent checks execute successfully. A stale-CPL copy
rejects, and default package resumption still refuses the eleven unresolved
authored requirements. See the [current manufacturing qualification](qualification/acquisition-manufacturing-2026-10-24.md).
Nominal power-removal checks also pass both loaded and open external-load cases;
vendor-model limitations and physical bounds remain explicit.

The [normal plane workflow correction](qualification/normal-plane-workflow-2026-10-24.md)
repairs a reproduced silently ignored input. Two-layer outer and four-layer inner
plane jobs pass through the public CLI; 111 relevant strict tests pass without
skips. The fresh multiblock two-sided pour attempt remains rejected: its native
route leaves opens and starved thermal relief. Bounded spoke reservation and
owned-reroute diagnostics do not complete it. Those failures remain evidence,
not accepted output. A fresh bottom-plane-only engineering selection completes
all six normal stages in 291.585 s, then passes all 20 declared checks and
matching package consistency. Its final board is `7eeedc50a26a…`; default package
resumption still refuses the original eleven unresolved requirements. Existing
`dc_path/2` bounds do not yet accept zones.

The [compound requirement correction](qualification/requirement-clauses-2026-10-24.md)
at `e68d2051` connects independent native ADC pin rules and exact distance-clause
coverage without rewriting the original specification. All 145 relevant strict
regressions pass. A fresh six-stage build routes completely and passes all 21
declared checks; final PCB `b1cf2a2564dd…`. A wrong address-pin input still passes
ERC and generator parity but fails the independent rule. Default package resume
continues to refuse all eleven unfinished original items, now naming the two
remaining sibling clauses explicitly. At that checkpoint diagnostic review used a small driver; the subsequent
public `build review` integration replaces it.

Immutable evidence under `~/.cache/agent-ee-qualification/agent545/`:

| Bundle | Files | SHA-256 of SHA256SUMS | Meaning |
| --- | ---: | --- | --- |
| `requirement-clauses-20261024` |7304|`5ec730b1272a59c4e47532bee2e364b910a767b5484a2d2b91232538d9ec2b83`|145 strict tests, unchanged-spec six-stage acquisition build, 21 declared checks and diagnostic exports, wrong-address negative, partial-coverage package refusal. One internal link has separately verified custody.|
| `normal-plane-workflow-20261024` |11402|`04dcbcd26826dce35f973d7905892446aaf81b6e9a382bfc31d55c4030d67c08`|Normal outer/inner plane jobs, 111 strict tests, rejected two-sided acquisition route, successful bottom-plane six-stage build and 20 declared checks, matching diagnostic exports, stale-CPL rejection and renders. Nine internal links have separately verified custody.|
| `acquisition-manufacturing-20261024` |6857|`a05dec9a96b7932e6949c2021c26b6616385f30f6e81a48770830fa899238fbd`|Current TPS manufacturing inputs, regenerated six-stage build, 20 declared checks, stale-CPL rejection, default refusal, two nominal removal cases and native renders. One internal symbolic link has separately recorded and verified custody.|
| `acquisition-dc-rail-20261024` |5003|`e0e6e339ed3c70115e8189fc2667996f9d4d7d2b9dc9ca699fa53dd5f97723d5`|Whole-net model, 70 native regressions, ordinary two/four-layer controls and negatives, exact acquisition copper review and committed-revision control.|
| `acquisition-power-spice47-20261024` |2670|`6c9c1f0deb24b9f15af3b9d594833b5e691df9c33d6756c05b11b193abc90c77`|Current-limited source repair, vendor models, stock failures, generic correction, native regressions, RC gates and unchanged actual power review.|
| `acquisition-flow-20261024` |3350|`344e5dd763a406f512da7d00de4d181b847413259cc27769aec9cc3bf82e8961`|Six-stage actual flow, independent checks, default manufacturing refusal, diagnostic and vendor-comment regressions.|
| `acquisition-artwork-package-20261024` |1305|`d60242bbbd6d6be12a0e8f73d7315693c39d12cdd739b3bbc373f3444591facd`|Earlier circuit's artwork, targeted package, stale-CPL rejection and 17 model renders.|
| `acquisition-electrical-20261024` |5034|`1e96ee1ea63cf709944850bb965e33ce9a53a224bffe39e1985aaada8d545183`|Sourcing, topology negative, interval unknowns and DFM regressions.|

See [electrical disposition](qualification/acquisition-electrical-2026-10-24.md),
[simulator correction](benchmarks/ngspice-inverse-switch-2026-10-24.md),
[native copper qualification](benchmarks/dc-rail-2026-10-24.md) and
[retained iteration history](system-qualification-history.md).

## Bounded qualification suite and negatives

1. **esp_prog:** retain the regression and saved routing/placement evidence.
   Route complete; 258 warnings/eight geometric via-in-pad sites remain undisposed.
   Native 3D previews were provided; these are not physical-fit evidence.
2. **Fresh multiblock acquisition interface:** actual sourced protected power,
   regulation, analog filters/ADC, digital host and connectors. The seven-stage native design and matching manufacturing package are accepted
   under the explicitly corrected engineering contract; supplier preview and physical tests remain unperformed.
3. **Four/six-layer interconnect:** differential pair, parallel bus, stackup,
   impedance/time/length matching, planes/return paths and routing feedback.
4. **QFN/BGA escape:** conventional escape, thermal/power connections, decoupling,
   islands/stitching, obstacles and existing-copper preservation. May combine
   with case 3 where the same native design actually exercises both.
5. **Negatives/recovery:** incomplete route and violated placement already reject
   in actual jobs; consequential diode error and stale package reject independently.
   Extend to final suite, clean setup and interrupted full-build resumption.

KRT source map: 69 capabilities, 107 tools and 127 hash-bound source files at observed
latest `d24a7602012b96c5cfa610ea05a171646ad601bd` (v0.23.7), under
[research/krt-2026-10-24](research/krt-2026-10-24). This maps features; it does
not demonstrate parity. The prior matched esp_prog seed 10 comparison leaves
44 opens in both tools. No broad equivalence or superiority claim is established.

## Exact next work

Completed capability milestone: fine-pitch two-sided ground-plane recovery. The
retained three-block acquisition variant passes the normal public placement job,
including saved-board refill/connectivity and final block/side/locality checks.
The accepted acquisition regression also passes; see [plane feedback evidence](qualification/plane-feedback-2026-10-24.md).
The final pin gate passes (945 checks without skips), including the corrected
four-layer implicit-via-size mismatch. A matched 300-second stock-KRT exercise
leaves one thermal-spoke error; the production agent-ee routing function repairs
it and passes. This is one bounded correctness result, not broad KRT parity. Preserve the earlier rejected attempts and the accepted acquisition
package; no manufacturing acceptance is claimed for the standalone two-sided
routing article.

Completed capability milestone: native netclasses/custom-rule custody and paired
routing through the public placement job and actual build placement producer.
See [native-rule qualification](qualification/native-rules-2026-10-24.md).
The four-layer article preserves its fixed anchors and native rules; both legs
measure 9.8125 mm, with zero opens/findings/skew and no vias. Matched stock KRT
produces the same measured geometry. Missing/changed rules, violated length/skew,
malformed native rules and changed-rule resume all reject. Fully fixed placement
no longer crashes. This does not establish pair layer transitions or tuning.

Completed capability milestone: intra-pair length matching on an asymmetric
four-layer-enabled article through the public job and real build producer.
The [qualification record](qualification/pair-matching-2026-10-24.md) binds
original native rules, controls, positive/negative outputs and matched stock
measurements. All 46 required modules have passing evidence after correcting
three stale pin/documentation expectations. Matching is opt-in, and final native
copper determines the result.

| KRT feature | Supported implementation and executable evidence | Observed result and remaining coverage |
| --- | --- | --- |
| D08 intra-pair portion | Public router matching controls; `router.intra_pair_match` in normal placement/build; `test_placement_job_matching` and captured `build/pair-matching/public-qualification/inputs/job.json` | Native skew 0.000787 mm against 0.15 mm rule and 0.1 mm request; zero opens/findings. Same-input stock d24a760 leaves 0.197740 mm skew with matched settings. Insufficient room, tighter target, missing pairs and changed-control resume reject. AC-coupled legs and pair-to-pair matching remain open. |

Next named capability: coupled differential routing across a required layer
transition on a bounded four-layer native article. Exercise an obstacle that
requires paired vias, preserve mechanical anchors and native width/gap/skew
rules, and inspect the actual saved geometry through the normal job. Any missing
workflow operation that blocks this case is part of that milestone. Keep the
accepted acquisition, simple pair, asymmetric matching and esp_prog evidence as
regressions. Bus/time/impedance, return-path and QFN/BGA cases remain required.
No required missing, stale or unknown check can pass.
