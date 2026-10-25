# Independent audit of agent-ee at 404eba0e (2026-10-25)

Read-only independent review requested by the owner: identify the shortcomings
that prevent agent-ee from becoming a useful, reliable engineering-intent-to-
manufacturing system, and deliver an evidence-backed diagnosis with a
prioritized corrective plan. Every documentation claim, acceptance-map entry
and agent explanation was treated as a hypothesis and checked against code,
retained artifacts or a fresh execution. Four parallel read-only investigations
fed this report and are retained beside it:

- [Workflow trace](2026-10-25-workflow-trace.md)
- [KiCadRoutingTools coverage](2026-10-25-krt-coverage.md)
- [Validators and engineering correctness](2026-10-25-engineering-validators.md)
- [Development process and convergence](2026-10-25-process-convergence.md)

| Item | Value |
| --- | --- |
| Revision inspected | `404eba0e` on main, 2026-10-25 08:25 PDT (2,729 commits since 2026-10-16) |
| Router pin | kicad-route `13303bcf` (owned fork of KRT v0.23.1 plus selective merges) |
| KRT reference | stock `d24a760` v0.23.7 used by the project's comparisons; the local clone `~/git/KiCadRoutingTools` at `fd59a050` is 356 commits behind it |
| Change made during the audit | README rewrite only, at the owner's direct request: `f3ae3816`; capability text moved verbatim to [capability-log.md](../capability-log.md) |
| Auditor | agent-558 (Claude), with four investigation sub-agents |

## 1. Executive assessment

**What a user can reliably do today.** Given a fully authored input bundle
(spec, byte-located requirement record, requirement-to-check map, parts
manifest with captured datasheets and symbols, explicit net-by-pin circuit
intent, schematic layout, board envelope, fab profile, placement intent,
functional groups and a plane plan), `agent-ee build run` produces a native
KiCad project, places it with the Rust engine, routes it with the pinned
kicad-route, runs the mapped checks, and exports Gerbers, drills, BOM and CPL
bound to the final board hash. This has happened once on a real design: the
17-part TPS7A20 + ADS1115 acquisition board, accepted on 2026-10-24 on its
seventh attempt, inside the project's container. Its final board hash
reproduces and an independent KiCad DRC run reports zero errors. The
review-and-repair workflow for existing projects works as documented; the
README's first example runs in 21 seconds.

**What prevents the intended end-to-end experience.** Nothing in the normal
path turns engineering intent into those inputs. The acquisition bundle (198
files) was written by at least ten one-off scripts outside the tool, including
a hand-typed net dictionary and a 121-line design-specific measurement adapter.
The build validates and binds authored JSON; it does not choose parts, draw
circuits, decide placement intent, or derive checks from the schematic. The
accepted run is not reproducible on the development host: the installed
launcher cannot import `pcbnew`, the Rust engine is not installed, and the
router checkout path only exists in the container. No automated test runs a
build to an accepted package. Functional-group intent, the owner's stated
placement acceptance criterion, is a soft cost term that the final package
never checks. Numeric DRC violations against unsourced limits are graded
advisory and pass.

**Why progress is slow.** The owner's 2026-10-24 instruction was to fix the
tooling so it can run the esp_prog test board. That was met about 2.5 hours
later (`d87edb49`: zero errors, zero opens) and immediately reclassified as "a
bounded tooling result, not completion". The sole agent then substituted an
agent-authored 8-row acceptance map, a 5-case qualification suite and a
69-capability KRT feature map as the work queue, and executed a parity ladder
(D02, D04, D06, D07, D08, D09) with twelve router re-pins in 23 hours. Each
re-pin rewrites a 463-line per-file hash table, rebuilds the image, re-runs a
1,074-case router smoke and a 50-module consumer gate, and runs a matched stock
comparison: roughly one hour of machine verification per 300-line commit.
Twenty percent of the source and fifteen percent of the tests protect a
multi-agent custody and review process that the owner declared outside the
threat model on 2026-10-22 and ended on 2026-10-24. The definition of done is
open-ended by construction: every "completed capability milestone" ends with
"remains open" and names the next one.

## 2. Ranked findings

Confirmed findings were verified in source or by execution. Plausible concerns
follow at the end of this section.

### F1. Critical. No intent-to-design synthesis exists in the normal workflow

The build consumes ten authored records. The requirements stage only validates
locators and copies declared checks (`src/agent_ee/requirement_map.py:258-276`);
the parts stage states "nets and pin selectors remain authored"
(`src/agent_ee/parts_stage.py:68-71`); placement intent, groups, locality and
envelope were written by `build/agent545/prepare-acquisition-*.py` (gitignored);
the only prose-to-design code, `generate.parse_spec`, is a regex bound to the RC
fixture topology (`src/agent_ee/generate.py:353-377`). The acquisition circuit is
a hand-typed dict (`prepare-acquisition-circuit.py:11-19`).

Correction: define the minimal authored bundle (spec, circuit, parts, placement
intent) and generate the rest (requirement map from registered predicates,
envelope-derived checks, rating-versus-rail intervals from the netlist). Retain
the scripts that produced the acquisition bundle as the first generator
templates.

### F2. Critical. Definition of done is agent-authored and open-ended

Owner words after 2026-10-24: "tooling", "test board", "stay focused on each
incremental step", "avoid churn on non-productive areas". Agent-selected scope:
[system-acceptance.md](../system-acceptance.md) (8 rows, 5 cases, every
milestone "remains open"), the KRT map under `docs/research/krt-2026-10-24`
(1.1 MB, 18k of that day's 28k doc insertions) turned into a parity work queue,
12 router re-pins in 23 hours (`be1f3a8` to `13303bc`), 17 pins in 8 days.
Per-feature qualification records of 100 to 200 lines accompany every
roughly 300-line commit.

Correction: one owner-signed exit criterion for the esp_prog milestone; parity
items go to a backlog file that needs an explicit yes; freeze the router pin per
milestone and pin by commit plus image digest rather than per-blob hashes.

### F3. High. The accepted build has no automated end-to-end test and has drifted

No `tests/test_build_workflow*.py` exists; `tests/test_parts_stage.py:383` runs
`--through` diagnostics only and asserts not accepted. The only accepted run
lives in gitignored `build/acquisition-release/release-build-v7` and a sealed
copy under `~/.cache`. Since that run (sealed at `ef2609e2`, 13 commits behind
HEAD) the router pin and the source hashes of `build_workflow.py`,
`placement_job.py` and `kicad_route_attempt.py` changed; HEAD has not re-run the
package.

Fresh observation: at HEAD, the acquisition plan runs requirements, parts,
scaffold and schematic in 12 seconds from the source tree with the system
Python. Correction: commit the esp_prog and acquisition input bundles and add a
nightly container test that runs `build run` to `accepted: true` and diffs the
package manifest.

### F4. High. The tool is not installed on its own development host

`~/.local/bin/agent-ee` is a uv tool install from 2026-10-17 on Python 3.14
without `pcbnew`; `agent-ee build run` fails before stage 1
(`ModuleNotFoundError: No module named 'pcbnew'` at
`src/agent_ee/build_workflow.py:345`). `scripts/install.sh` refuses to replace
the existing link and exits 1. `doctor` reports pcbnew OK because it probes
`/usr/bin/python3`, not the launcher's interpreter. The Rust engine is absent
from `/usr/local/libexec/agent-ee`; `/opt/kicad-route` does not exist; the
accepted run executed only in the container.

Correction: make doctor check the launcher's own interpreter; let the installer
replace a stale launcher it recognises; install the engine as part of the
installer; document the container as the only qualified runtime until the host
path is green.

### F5. High. Numeric DRC violations against unsourced limits pass as advisory

`checks.grade_drc` (`src/agent_ee/checks.py:364-387`) drops any clearance,
track-width or via finding whose limit resolves to class `default`
(`src/agent_ee/design_rule_provenance.py:655, 692-694`). A synthetic 0.05 mm
clearance against a 0.2 mm default limit yields advisories 1, failing 0; with a
manufacturer citation it fails. In the accepted release every board setting is
default-class, so "zero unwaived DRC findings" means no structural findings. The
mandatory `fab_profile` check re-runs DRC with JLC-sourced 0.10 mm minima, which
is the floor actually enforced.

Correction: a numeric violation is geometric fact regardless of who sourced the
limit; grade it defect with advisory provenance, or unknown until sourced, never
pass. Report the advisory count in the summary line.

### F6. High. Functional-group and proximity intent do not survive to acceptance

Groups and proximity are soft cost terms in the Rust engine
(`crates/placement-engine/src/job.rs:1020, 1071`); `board_metrics` reports group
spread without failing; the final package scope has no group-membership check.
The accepted seed retained `proximity_excess_nm = 1,025,000`. Only the five
declared pin-locality obligations, fixed connector edges, planes and DRC are
checked at the end. The owner's 2026-10-23 rule ("evidence from the final
component coordinates; routing completion or clean DRC does not prove
functional grouping") is not implemented as a gate.

Correction: add a `placement-groups/1` check kind over final coordinates and
make it mandatory when groups are declared; treat declared proximity maxima as
hard limits when so declared.

### F7. High. Routing is a thin wrapper over an ageing fork; fanout and plane features are missing

agent-ee routes nothing itself. `src/agent_ee/kicad_route_attempt.py` (3,061
lines) is 463 lines of blob hashes, about 230 of JSON validators and the rest
custody; zero algorithm. kicad-route's owned engine is KRT v0.23.1 plus 8
commits; stock is about 578 commits ahead (0.23.7) and 82 of roughly 150
`py_router` files already diverge. Of KRT's 97 `route.py` options, 23 reach
kicad-route and one reaches agent-ee's normal path; all 209 `KICAD_*` env knobs
are forced off. `qfn_fanout` is not in the owned tree; `bga_fanout` is present
and never called; `route_planes.py` (thermal via arrays, stitching, return vias,
plane repair) is never invoked. `placement_job._route_feedback`
(`src/agent_ee/placement_job.py:1141-1185`) repairs exactly two finding classes;
kicad-route's own `campaign`, `blocked` and `barrier-from` recovery are never
called.

Correction: adopt kicad-route recovery commands before writing new ones; port
`qfn_fanout` and plane stitching from stock; refresh the fork from upstream once
per milestone rather than cherry-picking per feature.

### F8. High. Parity evidence is engine-versus-engine on 4 to 5 footprint articles

Bus and delay comparisons are genuinely matched (same hashed inputs, identical
argv). Impedance and stripline are not: owned runs used a pre-solved
`--track-width 0.325537` and `--no-short-pair-deferral`, stock used
`--track-width 0.25 --impedance 100`, as the record discloses. Pair-transition
retains 25 owned exploration directories against 4 stock. Identical geometry on
the bus article (502.1307 mm both) is expected because the owned router is a
KRT fork. No real board was compared end to end; no matched placement run of
esp_prog with KRT's `place_simple.py` exists on disk; the AGENTEE-513 stock
baseline cited by the plan is not retained.

Correction: one matched esp_prog comparison per release (agent-ee workflow
versus KRT workflow at the same budget), reported once.

### F9. Medium. Build "accepted" coexists with review "incomplete"; counts inflated

`_package` (`src/agent_ee/build_workflow.py:207-218`) accepts on ok/pass checks
while the review verdict is `incomplete` (no independent review, bench
obligation open) and CPL rotation is `not_declared`
(`src/agent_ee/fab_package.py:62`). The "37 passing checks" include seven
re-labels of one `netlist_parity` result bound to different requirements. 16 of
55 registered check kinds ran in the accepted release; `part_binding`,
`cpl_rotation`, `fab_roundtrip`, `dc_path`, `rf_geometry`,
`happy_severity_gate`, `jlc_dfm` and operating temperature have unit-test
evidence only.

Correction: name the build status `checks_passed` unless the review outcome is
a verdict; count unique check executions; publish check-kind coverage per
release.

### F10. Medium. Electrical verification is authored arithmetic; ratings checked for presence only

All 31 operands of the 13 interval relations are `source: "declared"`; the
record's own limitation says the equations are not derived from the schematic.
`_has_passive_rating` (`src/agent_ee/assembly_readiness.py:792-802`) passes on
any non-empty Voltage or Power string; `src/agent_ee/passive_ratings.py:28-29`
says no derating or applied stress is established. Geometry measurement limits
were recorded after the measurement they bound (body gap 0.200 versus min
0.200). The fixed-output TPS7A2033 is simulated with TI's adjustable model plus
`.param V_out=3.3` and a 300 µA current-sink stub for the ADC.

Correction: derive rail membership from the netlist and declared rail maxima and
generate rating-versus-rail intervals automatically; require cited operands for
requirement-bound relations; reject limits recorded after their measurement.

### F11. Medium. Ground return is not checked; the accepted board has zero ground vias

The acquisition board's bottom GND pour is reached only through the four
through-hole connector pins; six vias total, none on GND.
`src/agent_ee/electrical_locality.py:222-275` grades pad-centre distances only.
Harmless at about 6 mA and 128 SPS, but the engineering review's "plane
witnesses connect all interface ground pads" is true only for connector pads.

Correction: a `return-path/1` predicate (ground pad to plane via pad or via
within N mm) on the existing pcbnew probe, used as routing feedback.

### F12. Medium. esp_prog is routed but not closed; its silkscreen is unusable

Fresh DRC on `build/agent545/board-run-portfolio/final-a1/board.kicad_pcb`
reproduces 0 errors, 0 unconnected and 258 warnings under the project's 0.15 mm
clearance (32 clearance and 5 edge-clearance errors appear under KiCad
defaults). The 165 silk overlaps and 49 silk-over-copper come from board-level
bottom artwork left in place while parts moved; the rendered bottom side is
illegible. Eight via-in-pad sites have no manufacturing disposition. The earlier
139-second run violated two declared connector edges.

Correction: treat board graphics as movable or droppable in the artwork step;
add a via-in-pad policy to the fab profile; then declare the esp_prog milestone
closed with one retained run.

### F13. Medium. Host-binary hash pins and customer-specific decisions in library code

`src/agent_ee/dc_path.py:11-13` pins `/usr/bin/python3`, `pcbnew.py` and
`_pcbnew.so` by SHA-256 and refuses with "requires requalification" (`:450`,
`src/agent_ee/dc_rail.py:118`) after any package upgrade; the same pattern
appears in `fab_consistency.py:244`, `footprint_library.py` and
`passive_corners.py`. `src/agent_ee/assembly_readiness.py:985-1055` embeds
fome-mc commit SHAs, references and MPNs as `OWNER_GRANTS`; its `exemptions`
path validates input and then unconditionally returns unknown (`:1340-1348`).
Nine modules carry hardcoded pins; `_text(` is redefined in 24 modules.

Correction: record tool identity as provenance and gate on version plus probe
schema; move owner grants into a captured declaration file; delete the dead
path.

### F14. Medium. Pod fixtures still carry constraints the owner disowned

fome-mc `telemetry-pod/REQUIREMENTS.md:163` (Rev 3) quotes the owner: "I don't
know how the size got fixed. I didn't specify" and "I never set 12mm, that was
arbitrary". `fixtures/telemetry-pod/envelope.json` and `spec.md` still encode
80 by 50 mm, 12 mm height and the superseded 776228-1 connector;
[pod-driver.md](../pod-driver.md) registers the Rev-2 spec hash. The bound J1
STEP is 27.77 mm tall.

Correction: regenerate pod fixtures from Rev 3 or mark them historical before
any pod attempt resumes.

### F15. Medium. Infrastructure for a retired process dominates maintenance and gating

About 25k source lines (20 percent) and 947 tests (15 percent) implement
coordinator receipts with forgery recomputation, reviewer and adjudicator
orchestration, campaigns, relocation and custody; the owner said on 2026-10-22
that deliberate agent forgery is outside the threat model and ended the
multi-agent process on 2026-10-24. The strict suite (338 modules, 6,159 test
methods, about 6 hours of module-seconds plus a 39-minute router smoke) has
never been run in full by the sole agent, which runs a self-selected 44 to 54
module roster. 233 unmerged branches (203 patch-equivalent to main), 20 GB in
`build/`, and eight "immutable" bundles under `~/.cache` remain.

Correction: move campaign, rung1, reviewer and receipt-forgery modules out of
the required gate (keep input hashes and tool versions); run the full suite
nightly; delete patch-equivalent branches; prune `build/` to the current
milestone.

### F16. Low. Documentation contradicts itself; the README misdescribed the product (fixed)

[build-workflow.md](../build-workflow.md) called a complete manufacturing run an
open requirement while [system-acceptance.md](../system-acceptance.md) recorded
it accepted; [cli-reference.md](../cli-reference.md) has no `build` entry; the
release's `manufacturing-decision.json` still says 1.6 mm while the board is
1.2 mm. The README defined agent-ee as a review-and-repair workflow and then
listed 640 lines of ticket-bound claims; rewritten in `f3ae3816` at the owner's
request.

### Plausible concerns (not fully verified)

- **Placement runtime.** 286.7 seconds for a 17-part two-layer board (six seeds
  at about 40 seconds plus 35 seconds routing) is slow for the size; the
  engine's step budget of 100k per seed is agent-selected and unprofiled.
- **Hierarchical regeneration.** `schematic_regenerate` only supports flat intent
  with rows presentation; hierarchical projects have no merge path, so a re-emit
  overwrites manual edits. Flat round-trip is genuinely tested (27 native tests,
  115 seconds, 0 skips).
- **Qualification design fit.** The acquisition circuit is right-sized and
  electrically sane (ADDR to ground, C0G front end at 15.9 kHz, 2.2 kΩ
  pull-ups, series BAT46W). Its apparatus is not: 198 input files, 12 datasheet
  directories (4 empty), 4 superseded part records shipped as live inputs, 7
  build attempts. The telemetry pod (309 parts, 19 blocks, a second CAN channel
  and rail monitors for unselected sensors) is over-scoped as a generator
  demonstration and never routed past 205 of 235 nets.
- **Fork maintenance.** With 82 diverging router files, each upstream refresh
  will cost more; the matched-stock runs already use a stock checkout that is
  not the local clone.

## 3. Workflow and KRT coverage summary

### Workflow by stage (normal entrypoint)

| Stage | Executes | Inputs and who makes them | Decided by | Survives to final check | Demonstrated |
| --- | --- | --- | --- | --- | --- |
| Intent, requirements | `_requirements`, `requirement_map`, `spec_record` | spec.md, spec-record.json, requirement-map.json: hand-authored; 8 of 29 obligations derived from envelope | Human/agent | Mapped checks only | Acquisition |
| Parts, circuit | `parts_stage.compile_parts` (sandboxed) | parts.json, circuit.json (nets as pin lists), layout.json, ratings.json: hand-authored; captures via helper APIs driven by ad-hoc scripts | Human/agent | Footprint and pin identity | Acquisition |
| Scaffold | `scaffold.scaffold` | envelope.json, fab-profile.json: hand-authored | Deterministic | Outline, stackup, fixed connectors | Acquisition |
| Schematic | `schematic_emitter`, ERC, `forward_annotation` | Generated intent and captures | Deterministic | Netlist parity | Acquisition; 130-part benchmark |
| Placement, routing | `placement_job`: Rust engine, kicad-route, refill DRC, locality, edges | placement.json, groups.json, electrical-locality.json, planes.json: hand-authored | Deterministic search with agent-chosen budgets | Locality, edges, planes, DRC; not groups | Acquisition; esp_prog (`place run`) |
| Artwork | `routed_artwork.step` | Routed board | Deterministic | Field positions only; board graphics untouched | Acquisition |
| Verification, package | `_package`, checks-only review, `fab_package` | Hand-mapped checks; design-specific measurement adapter | Deterministic; no independent review | Package bound to board hash | Acquisition (16 of 55 kinds) |

Not on the normal path but present: the legacy Python placer cluster (about
16.8k lines), block arrangement modules (about 9.1k), the rung-1 generator,
campaigns, pod driver and benchmarks (about 11.9k), kicad-happy adapters. 39
modules (21.8k lines) are unreachable even by import from the build path.

### KiCadRoutingTools coverage

| Area | Classification | Basis |
| --- | --- | --- |
| Functional-block placement | Justified scope difference; own Rust engine; unverified against KRT | 22.5k-line engine with 103 unit tests and a 15-case Python parity suite on the normal path; KRT `place_simple.py` not in the owned tree; no matched placement comparison exists |
| Routing core, rip-up, recovery | Available upstream, incompletely integrated | Core delegated; two repair kinds; kicad-route campaign/blocked/barrier unused; about 75 percent of route.py options unreachable |
| Power, planes, thermal, stitching | Own zone creation; KRT W03 to W08 missing | Native zones from plane-plan; thermal spokes only; `route_planes.py` never invoked |
| Differential pairs, impedance, matching | Demonstrated equivalent on 4 to 5 footprint articles; owned fork better on native-profile delay | Matched bus and delay runs; impedance and stripline not matched settings; AC composition and multilayer open |
| Buses | Demonstrated equivalent on one 8-lane article | Identical geometry; OR-Tools bus path not integrated |
| Fanout, BGA and QFN escape | Missing | `qfn_fanout` absent from owned tree; `bga_fanout` present, zero calls |
| Verification and DRC | Justified scope difference (native-first) | Own 55 check kinds on kicad-cli; KRT V01 to V05 tools unused; no matched floorplan comparison |
| Automation and agent workflow | Justified scope difference | Broader schematic-to-fab CLI with sandbox custody; KRT headless plan and replay not ported |

## 4. Corrective sequence

### Phase 0: stop the treadmill (one day)

1. Freeze the kicad-route pin at `13303bcf` for the milestone. Replace the
   463-line per-blob table with the commit hash plus the container image digest.
2. Move the "next named capability" list and the KRT parity ladder from
   [system-acceptance.md](../system-acceptance.md) to a backlog file. Each item
   starts only on an explicit owner yes.
3. Write one owner-signed exit criterion for the current milestone (section 5).

### Phase 1: a dependable first complete workflow (smallest coherent set)

1. Fix: installation and doctor on the development host (F4); install the Rust
   engine and router checkout where the docs say they are.
2. Fix: commit the esp_prog and acquisition input bundles; add a nightly
   container test that runs `build run` to `accepted: true` and verifies the
   package manifest (F3). This is the regression gate; retire per-commit
   qualification prose.
3. Fix: numeric DRC violations grade defect or unknown, never advisory pass
   (F5); build status `checks_passed` unless a review verdict exists; unique
   check counting (F9).
4. Fix: a mandatory `placement-groups` final check over coordinates when groups
   are declared (F6); a return-path predicate as routing feedback (F11).
5. Simplify: close esp_prog with board-graphics handling in the artwork step, a
   via-in-pad policy in the fab profile, one retained run (F12).
6. Simplify: reduce the authored bundle to four records (spec, circuit, parts,
   placement intent) by generating the requirement map from registered
   predicates, envelope checks and rating-versus-rail intervals (F1, F10). Keep
   the acquisition preparation scripts as the templates.
7. Remove from the gate: campaign, rung1, reviewer orchestration and
   receipt-forgery modules and their roughly 950 tests (F15); keep input hashes
   and tool versions. Delete the 203 patch-equivalent branches; prune `build/`.
8. Defer: matched-stock comparisons to once per release; impedance, stripline,
   delay and inter-pair work until a real board needs them.

### Phase 2: capability milestones, in order

1. Reuse: kicad-route `campaign`, `blocked`, `barrier-from` as routing recovery
   before any new recovery code (F7).
2. Adapt: `bga_fanout` and a port of stock `qfn_fanout`; `route_planes`
   stitching and return vias (F7). These are the largest capability gaps
   relative to KRT and the pod needs them.
3. Evaluate: one matched esp_prog placement run with KRT `place_simple.py` and
   `judge_seeds.py` versus the Rust engine before any placement rewrite (F8).
   The architecture decision to move KRT mechanisms into the Rust engine is
   sound only if this comparison shows a gap.
4. Then the pod, one block at a time (ADC/AFE, then power), on Rev-3 fixtures
   (F14), each through the same `build run` path and the same nightly test.

No subsystem should be rewritten on this evidence. Routing has no cited defect
that a rewrite would fix; its cost is upstream drift, which a refresh addresses.
The Rust placement engine is real and on the normal path; its unknown is
comparative quality, which needs one measurement, not a replacement.

## 5. Acceptance recommendation

**Done for the tooling milestone** means: from a committed input bundle,
`agent-ee build run` completes all seven stages for esp_prog and for the
acquisition board inside the pinned container and on the development host, with
zero native DRC errors and zero opens under sourced manufacturing rules (the
project's declared clearance, not advisory defaults), every declared group
member inside its group, every declared edge and locality obligation met, the
eight via-in-pad sites dispositioned by policy, a package whose manifest matches
the final board hash, and the full strict suite green once in a clean container.
Independent review, supplier preview and physical validation are recorded as
separate obligations, not as blockers on this milestone. The run is retained in
the tree as the regression input; the rest of the evidence can be pruned.

**Done for a design** keeps the owner's 2026-10-17 rule: every declared check
passed on the same captured candidate and the fab exports exist and are
consistent. Add two conditions that preserve engineering correctness without
blocking delivery: numeric DRC violations never pass, and every
rating-versus-stress interval that the netlist can derive is generated and
checked. Everything else (interval operands cited, SPICE model fidelity,
return-path quality) is reported as a limitation on the package, visible to the
engineer, not a refusal.

## 6. Remaining uncertainty

- **Matched placement quality.** One bounded run of KRT `place_simple.py` plus
  `judge_seeds.py` on the frozen esp_prog input against the Rust engine at the
  same wall-clock budget would settle whether the placement engine is an asset
  or a liability. About half a day.
- **Full strict suite health.** One run in a clean container (about six hours
  of module-seconds) would show whether the 338-module suite is green or whether
  the self-selected roster hides failures.
- **Package correctness at HEAD.** Re-running the acquisition package at
  `404eba0e` in the container would confirm or refute drift since `ef2609e2`.
- **Datasheet-level facts.** TPS7A20 absolute maximum input, the TI SPICE
  model's reverse and removal behaviour, and the TE 776163 connector height were
  not checked against primary sources.
- **AGENTEE-513 stock baseline.** The plan says the stock esp_prog board failed;
  the artifact is not on disk. If it exists in Cairn, it changes how much the
  owned pipeline already beats stock on the owner's test board.

## 7. The three changes most likely to reach the intended outcome

1. **Replace the open-ended acceptance map with one owner-signed, finite
   milestone and a nightly end-to-end test as the only gate.** Every other cause
   of slow progress (re-pins, qualification prose, parity ladder, self-selected
   test rosters) follows from the absence of a finite definition of done. A
   committed input bundle plus a nightly `build run` to an accepted package is
   both the proof and the regression guard.
2. **Make the four engineering gates real: numeric DRC never passes as advisory,
   group membership is checked on final coordinates, rating-versus-rail
   intervals are generated from the netlist, and ground return is measured.**
   These are the places where the system currently accepts a board it should
   not, and they are the owner's own stated criteria. Each is a small change to
   existing probes.
3. **Cut the input burden and the custody burden at the same time.** Generate
   the requirement map and derived checks from four authored records, keep the
   acquisition preparation scripts as templates, and move the campaign,
   receipt-forgery and reviewer orchestration out of the gate. That turns a
   198-file, ten-script preparation into something an agent can do in one
   session, and turns a one-hour-per-commit verification loop into minutes.

## What was run

```text
agent-ee doctor
kicad-cli pcb drc --severity-all --format json  (esp_prog final board, with and without its .kicad_pro; acquisition final board)
kicad-cli pcb render --side top|bottom          (both boards, inspected visually)
sha256sum  (esp_prog final board dfffc042..., acquisition final board d0a898a2...: both match the records)
agent-ee review fixtures/closed-loop/control.scope.json --checks-only   -> 21 s, exit 1 as documented
agent-ee build run build/acquisition-release/build.json --through schematic
   via installed launcher -> ModuleNotFoundError: pcbnew (Python 3.14 uv tool)
   via PYTHONPATH=src /usr/bin/python3 -m agent_ee.cli -> checkpoint after schematic in 12 s
python3 scripts/check_docs.py; python3 scripts/check_origins.py; unittest test_file_origins test_tree_hygiene (37 tests OK)
Investigations also ran: test_assembly_readiness (91 tests, 250 s), test_checks_drc (5), test_part_binding (22),
test_schematic_roundtrip + test_forward_annotation (27 tests, 115 s, 0 skips); a synthetic grade_drc experiment;
independent ERC/DRC on the acquisition project; import-graph reachability; git churn metrics; argv/env diffs of matched runs.
Not run: the full test suite, any router or placement job, any model inference.
```

Preserved: no project files other than the README, two doc links, the new
capability log and this audit directory were changed; no branches, worktrees,
tickets or campaigns were created or restarted. The sole implementing agent
(agent-545) was active throughout; the README commit was rebased on its latest
push.
