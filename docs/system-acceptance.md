# Intent-to-manufacturing acceptance map

**Planning update, 2026-10-25:** implementation and board campaigns are paused by
the owner. The [reconciled recovery plan](planning/2026-10-25-recovery/recovery-plan.md)
and its Plane backlog govern the proposed execution order. The evidence below
remains historical capability/acceptance evidence; its next-capability wording
does not dispatch work or release the telemetry-pod hold.

**UNFINISHED — 2026-10-25, agent-545.** Correct operation through the ordinary
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
| Placement and routing feedback | Placement constraints + seed/route policy + captured planes → saved board, trials and final-placement reports; `placement_job`, Rust planner, routing adapter | Accepted acquisition and esp_prog route with zero native errors/opens. Public jobs and build producers exercise declared planes, targeted two-sided recovery, preserved native rules, differential matching/transitions, nominal outer-layer impedance, symmetric stripline, geometric buses, explicit single-ended length groups and nominal native-profile propagation-delay matching and coupled pair-to-pair and explicit AC-chain length matching; individual qualifications below bind the evidence and limits. Existing pours, placement-restricting rule areas, broader impedance/return paths, DDR/package lengths and fanout remain open. |
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

KRT source map: 69 capabilities, 107 tools and 127 hash-bound source files at
inventory revision `d24a7602012b96c5cfa610ea05a171646ad601bd` (v0.23.7), under
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
| D08 intra-pair portion | Public router matching controls; `router.intra_pair_match` in normal placement/build; `test_placement_job_matching` and captured `build/pair-matching/public-qualification/inputs/job.json` | Saved planar-copper difference 0.000787 mm meets the 0.1 mm request; the separate native 0.15 mm skew rule also passes, with zero opens/findings. Same-input stock d24a760 leaves a 0.197740 mm planar difference with matched settings. Insufficient room, tighter target, missing pairs and changed-control resume reject. Pair-to-pair groups now have separate qualified evidence below; explicit AC chains now have separate qualified evidence below. |

Completed capability milestone: coupled differential routing across a required
inner-layer transition. [Qualification](qualification/pair-transition-2026-10-24.md)
binds the normal public job, actual build producer, native keepout and layer
policy controls, saved projects and matched stock comparison. Both paths save
four vias, zero opens/findings and a 0.000786 mm planar difference while passing
the separate native 0.15 mm skew rule. All 48 required agent-ee modules have
passing evidence (977 tests, no skips); every router smoke case is accounted
for after explicit setup/fixture corrections, with initial failures retained.

| KRT feature | Implementation and executable exercise | Bounded result |
| --- | --- | --- |
| D02 coupled vias; portions of R02/R04 | Public `--pair-layers`, `router.pair_layers`, routing-only native keepout custody; `test_placement_job_transition`, router `test_pair_transition` | Original orthogonal four-layer article crosses an outer-layer barrier on In1.Cu, preserving anchors/rules. Outer-only routing, forbidden saved layers, invalid layer names, placement restrictions and changed-policy resume reject. Matched stock d24a760 retains a native skew violation; integrated owned routing passes. Through vias only; hybrid and wider technology coverage remain open. |

Completed capability milestone: nominal outer-layer differential impedance.
The [qualification](qualification/pair-impedance-2026-10-25.md) binds the public
placement job and actual build producer, explicit original stackup, final filled
reference, measured geometry and an independent KiCad analytical calculation.
The 100 ohm ±10% article saves zero opens/findings and six tracks at 0.325537 mm
width and 0.35 mm gap. Missing original dielectric data, reference slots, missing
coverage and connected neck/escape violations reject. All 983 required agent-ee
cases have passing evidence, with the initial external-input omission and its
correction preserved. The corrected neck test supersedes a faulty disconnected
control; production runtime files are unchanged by that test correction.

| KRT feature | Implementation and executable exercise | Bounded result |
| --- | --- | --- |
| D04 differential outer-layer portion | Native stackup solver plus saved-geometry/reference checker; `router.pair_impedance`; `test_placement_job_impedance` and router `test_pair_impedance` | Public job/build producer pass nominal 100 ohm ±10% and explicit 3 mm total terminal-escape limit. Matched stock `4cf6d29ddbe7d19b511352d0abbb26842f609be6`, newly observed on 2026-10-25, also passes. Parity on this article; no superiority. Uncoated coupled trunks only; single-ended, inner-layer, multilayer and coplanar impedance coverage remains open. |

Completed capability milestone: nominal symmetric differential stripline. The
[qualification](qualification/pair-stripline-2026-10-25.md) binds actual PTH
launches, both adjacent filled references, the complete coupled corridor, saved
geometry and an independent analytical cross-check. Native missing-plane, trunk
split, oversized launch opening, between-traces split and original-stackup
negatives reject. The general PCB-only snapshot operation now preserves native
footprint-library dependencies. The build-producer candidate has zero opens and
findings. All 987 required consumer cases and 1,064 router cases have passing
evidence, with original setup/pin failures retained and exact corrections named.

| KRT feature | Implementation and executable exercise | Bounded result |
| --- | --- | --- |
| D04 symmetric differential stripline; D05 reference checks | Existing impedance operation and `router.pair_impedance` on one inner layer; `test_placement_job_stripline` and router `test_pair_stripline` | Nominal 100 ohm ±10% with explicit 4 mm terminal-escape limit, both native GND planes and bounded PTH antipads. Matched stock `4cf6d29ddbe7d19b511352d0abbb26842f609be6` also passes. Parity on this article; no superiority or physical impedance claim. |

Completed capability milestone: geometric single-ended bus routing through the
normal placement job and actual build producer. The [qualification](qualification/bus-routing-2026-10-25.md)
records an eight-lane group, a deferred third-terminal tap, a separate AUX net,
a native obstacle, fixed functional blocks and a mixed protected pair. The
complete build candidate has zero opens and native findings. All 991 required
consumer cases and 1,074 router cases have passing evidence, retaining the
initial NaN assertion error and its exact corrected rerun.

| KRT feature | Implementation and executable exercise | Bounded result |
| --- | --- | --- |
| D09 geometric buses; R03 bus ordering/taps | Public `--bus` and normal `router.bus`; `test_placement_job_bus`, router `test_bus_routing` | Actual D0–D7 corridor, AUX excluded, D3 third terminal connected. Final 35 segments, 502.1307 mm and four vias; matched stock `4cf6d29ddbe7d19b511352d0abbb26842f609be6` has the same measurements and zero native findings. Parity on this article, without timing, electrical bus identity or superiority claims. |

Explicit single-ended group matching now passes the public placement job and
actual build producer. The [qualification](qualification/group-matching-2026-10-25.md)
records four unequal lanes, final native path coverage, connected-loop rejection,
preserved native rules and libraries, and exact-name/configuration repairs.
All 997 required consumer cases in 52 modules pass without skips. Router
`21c08ec` is integrated; the source-bound consumer gate covers this baseline.
The subsequent exact-input correction below closes the leading-hyphen gap.

| KRT feature | Implementation and executable exercise | Bounded result |
| --- | --- | --- |
| D06 explicit two-terminal length groups | Public `--length-match-group`, normal `router.length_match`; router `test_length_groups`, consumer `test_placement_job_length_groups` | Four useful paths within 0.1 mm; zero opens/native findings, 28 segments, 199.9525 mm and zero vias. Matched stock `4cf6d29` has the same geometry. This qualifies the explicit-group portion; DDR auto-groups, package lengths, absolute-window and broader multilayer coverage remain open. |

The [exact group input and replay correction](qualification/group-input-2026-10-25.md)
now carries option-like names through the public job and preserves groups in
stored commands. Native `-DQ0`/`--help` routing and changed-group resume rejection
pass. Router `68dda6e` closes 119 focused cases; all 998 required consumer cases
pass without skips. The original failures remain retained. Standalone placement
still reports its eight baseline library-context warnings; the complete build
producer includes the libraries and has zero native findings.

Nominal native-profile propagation-delay matching passes the public placement
job and existing build producer. The [qualification](qualification/delay-matching-2026-10-25.md)
records two paths on different layers, explicit original dielectric sheets,
authored timing profiles, used via spans, preserved existing copper, native path
coverage and final skew. Router `48f02187` is integrated. All 1,003 required consumer cases in 53
modules pass without skips, including the five new public timing cases. The
clean native runtime, final source and per-case roster are retained; no
whole-product completion follows.

| KRT feature | Implementation and executable exercise | Bounded result |
| --- | --- | --- |
| D07 nominal single-ended profile timing | Existing public group operation with `--group-delay-tolerance`, normal `router.length_match.tolerance_ps`; router `test_delay_groups`, consumer `test_placement_job_delay_groups` | Six-layer article meets 1 ps skew: 323.78/323.00 ps. Final build has zero opens/findings. Missing original dielectric/profile/coverage, ignored via height, connected timing failure and changed-requirement resume reject. Matched current stock `e358107` remains connected but violates the same native timing limit at 42.90 ps. Nominal authored rates only; no physical signal-integrity, fabrication or broad superiority claim. |

Completed bounded capability milestone: coupled pair-to-pair length matching.
Router `13303bcf` is integrated; five public job/build cases and all 1,003 prior
consumer cases pass, totaling 1,008 distinct cases in 54 modules without skips. See [qualification](qualification/inter-pair-matching-2026-10-25.md).
The native four-layer article checks every useful P/N path, per-pair widths and
gaps, intra-pair skew and coupling extent. The heterogeneous-class build has
zero findings/opens; matched stock `e358107` has 33 native geometry findings
and zero opens. Insufficient room, strict authored coupling and changed-request
resume reject. This is software geometry qualification, not physical SI or
manufacturing acceptance. Existing connected geometry and atomic failed-group
discard have native router evidence.

| KRT feature | Implementation and executable exercise | Bounded result |
| --- | --- | --- |
| D08 pair-to-pair geometry | Existing `router.length_match` complete-pair groups; router `test_inter_pair_matching`, consumer `test_placement_job_inter_pair` | Four useful paths span 0.0611 mm against 0.1 mm. Mixed native pair classes retain widths/gaps with zero final build findings/opens; matched stock `e358107` has 33 geometry findings. Strict authored coupling, incomplete pairs, insufficient room, batch failure and changed-request resume reject. Coupled time-domain composition remains open; explicit AC chains have qualified evidence below. |

Completed bounded capability milestone: AC-composed differential-pair matching.
Router `c4af095ed07593b63d7886c139fcb7e5c2e774f4` is integrated. All 263
required router cases and 1,013 distinct consumer cases in 55 modules have
passing evidence with zero skips, including the five new public/build cases.
The [AC qualification record](qualification/ac-matching-2026-10-25.md) retains
the initial prerequisite/test-expectation failures and their exact rechecks.
Normal public placement and the real build producer preserve the explicit
capacitor-chain connectivity and independently measure the final composed paths.
The build candidate has zero native findings/opens. Insufficient tuning room,
invalid inputs and changed-request resume reject. This is copper-geometry
qualification; package delay, physical signal integrity and manufacturing
acceptance are not established by this article.

| KRT feature | Implementation and executable exercise | Bounded result |
| --- | --- | --- |
| D08 explicit AC-chain geometry | `router.ac_match`, native chain preflight and composed final checker; router `test_ac_matching`, consumer `test_placement_job_ac_matching` | P = N = 62.3307 mm with 0.0002 mm rounding bound, within 0.1 mm. Connected unmatched negative rejects. Matched stock `e358107` has 0.1162 mm skew despite clean ordinary DRC. Existing sections and each native pair class are preserved. Broader multilayer/time-domain composition remains open. |

Existing successful workflows remain regressions. Asymmetric/multilayer/
coplanar impedance, broader propagation-time and multilayer composed matching,
fanout, existing placement-input pours, broader return-path recovery
and circuit synthesis remain required subsequent work. Latest stock KRT also
adds an optional OR-Tools bus path; prior geometric bus evidence does not qualify
every new stock bus feature. No missing, stale or unknown required check can pass.
