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
Current limitations on zones, non-default netclasses and nonrectangular outlines
are implementation gaps to close, not convenient exclusions.

Mains isolation, flex/rigid-flex, blind/buried microvias, advanced packaging and
arbitrary microwave design are outside this bounded initial domain. Declared
controlled impedance, return paths and RF-module restrictions remain in scope.

## Actual production path and evidence

Normal entrypoint: [`agent-ee build run PLAN --out RUN`](build-workflow.md).
Each producer consumes the preceding receipt and actual native outputs. Explicit
`--through artwork` permits diagnosis of an incomplete manufacturing baseline;
default package execution still refuses missing required coverage. Passing
individual checks never means the whole design is accepted.

| Stage | Real inputs → outputs / production implementation | Required constraints, exercised capability and remaining acceptance |
| --- | --- | --- |
| Intent and requirements | Authored specification and requirement/check map → captured requirements/scope; `design_intent`, `spec_record`, `requirement_scope`, build requirements stage | Preserve requirements, assumptions, defaults and unresolved decisions. Fresh acquisition specification reaches all six preparation stages. General free-text engineering synthesis and complete requirement closure remain open. |
| Architecture, parts and circuits | Circuit/connectivity intent, captured sourcing/CAD/rating records → bound parts and native pin inventory; build parts stage, `parts_stage`, `binding_fill` | Source parts, topology and electrical calculations. Actual selected TPS7A20/ADS1115 circuit is carried into the board. Consequential reversed-diode negative rejects despite clean ERC. Loaded-current, capacitance, startup/removal and other electrical bounds still need closure. |
| Native schematic | Parts receipt + connectivity/layout → editable native project and dependent sheets; scaffold/schematic stages, `schematic_emitter`, forward annotation | Stable identity, exact pin/footprint/net agreement, all pins accounted for, native rules preserved. Fresh 17-part design passes ERC and exact topology. Hierarchical regeneration/child edits exercised separately; preserving arbitrary manual edits remains limited. |
| Board and mechanical intent | Native project + envelope, anchors, blocks, side and locality declarations → placed native PCB; scaffold, `placement_job` | All 17 acquisition footprints belong to three blocks; all three fixed connectors and eight hard pin distances pass after routing. Selected height maxima, complete body/hardware/access and broader side/keepout cases remain open. |
| Placement and routing feedback | Placement constraints + seed/route policy → saved board, trials, final-placement reports; `placement_job`, Rust planner, routing adapter | Acquisition and esp_prog both route with zero native errors/opens. Search/legalization/final reload enforce declared hard constraints; portfolio can reject and retry candidates. Zones, netclasses, broader multilayer/paired/bus/fanout/plane features and routed constraint coverage still require qualification. |
| Electrical verification | Final native netlist/PCB + source-bound calculations/models → individual checks; `electrical_topology`, interval checks, passive/active SPICE, `dc_path` | Native topology, selected parts and assembly checks pass acquisition. Corrected ngspice 47 passes 139 native regressions and unchanged actual startup. New whole-net copper model exercises two/four-layer native controls and actual supply/return copper; disconnected and exceeded-budget negatives reject. Physical material/contact bounds and component obligations remain open. Clean ERC is not functional acceptance. |
| Physical/assembly/fabrication checks | Final reloaded/refilled board + authoritative rules, population/profile/placement intent → native and independent results | Acquisition has zero unwaived DRC findings and zero center-in-pad vias. Final grouping/locality is checked. Complete mechanical/fabrication requirement coverage and remaining routing-feature cases are still unmet. |
| Manufacturing package | Accepted native identity + manufacturing profile → Gerbers, drills, BOM, CPL, documentation and bound validation; package stage, `fab_export`, `fab_package`, `fab_consistency` | Earlier targeted package exercise validates 9 plots/2 drills/12 BOM rows/17 placements and rejects stale coordinates. It used the prior circuit, so does not qualify the current TPS design. Current default build correctly refuses missing manufacturing coverage; no complete package is accepted. |
| Operation and reproducibility | Clean installation + captured plan → bounded run/journal/resume/diagnostics; installer, doctor, build workflow | Receipted resume, source/runtime drift refusal and targeted negative cases exercised. Corrected native-image 47 passes the targeted requalification and both ordinary RC integration gates. Full fresh intent-to-package run, complete-suite clean setup and interrupted/resumed qualification remain required. |

## Current integrated state and evidence

Integrated simulator repair: `54864697c224fa1e7464719a3bd9b67e47752adf`;
kicad-route `80e144437b48a3de77b226d0e9465c16b9ab8b7e`.
This iteration replaces ngspice 42 with corrected maintained 47. The supported
image passes 139 native regressions, 20 switch checks, both RC integration gates
and the unchanged actual acquisition startup case. The observed image and binary are recorded in the simulator report.

The next integrated feature adds `dc_path/2` through the existing public review
workflow. The unchanged acquisition board's ideal copper bounds are 1.556 mV
supply and 3.551 mV return at declared 20 mA/25 °C. Every native conductor is
accounted for; full DRC/parity is a prerequisite. Independent two/four-layer
controls, disconnected-route and exceeded-bound cases are retained at
`build/agent545/dc-rail-public`. These nominal model conditions do not close
physical manufacturing tolerance, contact/spreading or full electrical acceptance.

The actual fresh TPS acquisition build used agent-ee `9a12901a` and completed
requirements, parts, scaffold, schematic, placement-routing and artwork in
`build/agent545/acquisition-flow-build-native`. Final PCB SHA-256:
`e802bb4a77374a33acf1d40e4b0551befa3dc92c64dd1340103da44182a26087`.
Seed 6 won the bounded six-seed search: 279.455 s, 199 segments, 10 vias, 216.6868 mm.
Those are observations, not comparative optimization or manufacturing acceptance.
Independent review passes ERC, DRC, topology, selected parts and assembly data.
The default package resume remains rejected with five missing manufacturing
coverage kinds. No native board or component values were substituted by hand.

Immutable evidence under `~/.cache/agent-ee-qualification/agent545/`:

| Bundle | Files | SHA-256 of SHA256SUMS | Meaning |
| --- | ---: | --- | --- |
| `acquisition-power-spice47-20261024` |2670|`6c9c1f0deb24b9f15af3b9d594833b5e691df9c33d6756c05b11b193abc90c77`|Current-limited source repair, vendor models, stock failures, generic correction, native regressions, RC gates and unchanged actual power review.|
| `acquisition-flow-20261024` |3350|`344e5dd763a406f512da7d00de4d181b847413259cc27769aec9cc3bf82e8961`|Six-stage actual flow, independent checks, default manufacturing refusal, diagnostic and vendor-comment regressions.|
| `acquisition-artwork-package-20261024` |1305|`d60242bbbd6d6be12a0e8f73d7315693c39d12cdd739b3bbc373f3444591facd`|Earlier circuit's artwork, targeted package, stale-CPL rejection and 17 model renders.|
| `acquisition-electrical-20261024` |5034|`1e96ee1ea63cf709944850bb965e33ce9a53a224bffe39e1985aaada8d545183`|Sourcing, topology negative, interval unknowns and DFM regressions.|

See [electrical disposition](qualification/acquisition-electrical-2026-10-24.md),
[simulator correction](benchmarks/ngspice-inverse-switch-2026-10-24.md) and
[native copper qualification](benchmarks/dc-rail-2026-10-24.md), and
[retained iteration history](system-qualification-history.md).

## Bounded qualification suite and negatives

1. **esp_prog:** retain the regression and saved routing/placement evidence.
   Route complete; 258 warnings/eight geometric via-in-pad sites remain undisposed.
   Native 3D previews were provided; these are not physical-fit evidence.
2. **Fresh multiblock acquisition interface:** actual sourced protected power,
   regulation, analog filters/ADC, digital host and connectors. Six stages pass;
   functional/mechanical/manufacturing acceptance remains open.
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

Close the acquisition electrical and mechanical requirements, attach every
required manufacturing check and complete its normal default package build.
Proceed to the remaining representative/KRT cases. A milestone ends an iteration,
not this goal; no required missing, skipped, stale or unknown check can pass.
