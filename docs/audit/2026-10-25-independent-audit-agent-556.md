# Independent agent-ee audit — agent-556, 2026-10-25

**agent-ee has demonstrated a useful engineering build engine, but it is not
yet a dependable engineering-intent-to-manufacturing system.** Its strongest
capabilities are native KiCad handling, captured evidence, bounded placement
and routing, and manufacturing-file custody. A real 17-part acquisition design
completed all seven build stages. Its principal weaknesses are incomplete
input authoring, loss of constraints between representations, and acceptance
checks that sometimes establish less than the requirement they are attached to.

Progress is slow largely because feature qualification has advanced ahead of
integration fundamentals. Successive routing capabilities are being added while
basic placement intent remains inconsistently represented. Broad qualification
goals, duplicated execution paths, and coarse implementation-freshness rules
compound that problem. The evidence does not justify a wholesale router or
placement-engine rewrite.

**Scope and revisions.** This was a single-agent, independent audit requested by
the owner. The investigation did not modify the project, dispatch implementation,
create tickets, or restart held campaigns. The owner subsequently requested that
these findings be saved in `docs/audit/`.

The executable revision inspected was
[`404eba0e322c2d711202ad30f8bee1c18269c2f8`](https://github.com/halbritt/agent-ee/tree/404eba0e322c2d711202ad30f8bee1c18269c2f8).
The owned router was `13303bcf92b46428ee285bd73c8ac398d9b4a808`.
Current upstream KiCadRoutingTools was inspected at
[`b232bac7bd31c25c13f7f2feff556d884bb73e75`](https://github.com/drandyhaas/KiCadRoutingTools/tree/b232bac7bd31c25c13f7f2feff556d884bb73e75).
The owned router's recorded extraction base was KRT `295f9ee1`; selective later
adoptions mean that base is provenance, not a complete capability description.

During the audit, `f3ae3816738abd916e1e04908c47b029cd28b816` changed documentation
only. I reviewed its clearer description of the generator and its limitations.
The source findings below remain tied to `404eba0e`. Concurrent audit documents
and implementation work were preserved. Experiments used isolated copies,
KiCad 10.0.7, and the existing native image
`agent-ee:exec-ae6de0276dff865a` with image ID
`sha256:9831643e8f7ff13f63ed99c8ac4e064fbef858356ea66b555b534f361fbc0c94`.
The installed Rust planner receipt matched all inspected snapshot source hashes.

**Evidence locations.** Fresh artifacts are retained on the audit host under
`/home/halbritt/.cache/agent-audits/agent-ee-556-20261025/`, referred to below as
`AUDIT_ROOT`. Its `audit-identity.json`, `audit-findings.json`,
`audit-recommendation.json`, and `audit-methodology.json` record revision,
observations, commands, recommendations, and methodological qualifications.
`source/`, `router/`, and `krt/` preserve the inspected trees. These host-local
artifacts are not included in this documentation commit; public readers can
inspect the pinned source links, but cannot independently inspect the retained
native results without access to that evidence store.

The findings, in priority order, are:

1. **High — unsupported placement intent can disappear without a refusal.
   Confirmed by a public-command execution.**

   I supplied `keepouts`, `assembly.sides: "B"`, and `blocks[].side: "B"` to a
   real native placement job. `agent-ee place run` returned exit 0, `completed`,
   and `eligible`. Both fixed connectors remained on the front; J1 at
   `(45, 50)` mm remained inside the declared front-side keepout
   `[43, 48, 47, 52]` mm.

   These are upstream KRT vocabulary fields, not implemented promises of the
   current agent-ee job. The defect is silently accepting unsupported
   declarations. An explicit unsupported-input error would be appropriate.

   `_capture_native` consumes selected intent fields without validating the
   complete document; final grading cannot recover omitted constraints.
   Capture-only probes also showed that adding each field produced an identical
   solver problem. See
   [capture code at the inspected revision](https://github.com/halbritt/agent-ee/blob/404eba0e322c2d711202ad30f8bee1c18269c2f8/src/agent_ee/placement_job.py#L596).
   Evidence: `AUDIT_ROOT/probes/ignored-intent.json` and
   `AUDIT_ROOT/probes/public-ignored-intent/{inputs/intent.json,inputs/job.json,summary.json,job/job-report.json}`.

   **Correction:** immediately validate the intent schema and reject unsupported
   fields. Then carry supported constraints through capture, search,
   materialization, routing, and independent final-native checks. This experiment
   establishes a placement-job defect; it does not establish that the complete
   manufacturing build would accept this article.

2. **High — the agent workflow does not reliably produce the engineering inputs
   that the build requires. Confirmed integration gap.**

   `build run` is a meaningful seven-stage pipeline. However, the caller must
   already supply requirements and check mappings, selected parts and sources,
   circuit connectivity, schematic layout, envelope, groups, placement intent,
   and applicable models.

   Structured inputs are reasonable. The missing product capability is a
   repeatable agent procedure that derives them from engineering intent,
   resolves assumptions, and checks their mutual consistency. The installed
   skill primarily guides review and repair. The intent/dialog modules capture
   or validate declarations; the legacy generator's narrow RC interpretation
   is not general circuit synthesis.

   The acquisition example demonstrates that an engineering agent can prepare
   these inputs. Its 198 captured source files also show how much preparation
   sits outside the normal command. See
   [build execution](https://github.com/halbritt/agent-ee/blob/404eba0e322c2d711202ad30f8bee1c18269c2f8/src/agent_ee/build_workflow.py#L257),
   [parts compilation](https://github.com/halbritt/agent-ee/blob/404eba0e322c2d711202ad30f8bee1c18269c2f8/src/agent_ee/parts_stage.py#L65),
   and the [agent skill](https://github.com/halbritt/agent-ee/blob/404eba0e322c2d711202ad30f8bee1c18269c2f8/skills/agent-ee/SKILL.md).

   **Correction:** build the agent-facing engineering workflow around these
   existing formats. It should produce decisions, sources, calculations, and
   explicit unresolved questions, rather than merely fill JSON fields.

3. **High — the placement search model loses side/layer distinctions and rejects
   legal geometry. Confirmed by differential checking.**

   I placed one SMT footprint on the front and another on the back at the same
   coordinates. Native KiCad DRC reported no geometric violations; the
   exact-source-matched Rust evaluator reported five pair conflicts. One expected
   unrouted connection remained, so this was a placement experiment rather than
   a completed board.

   Capture retains side information separately for preservation, but the
   solver's footprint and copper records omit it. `pair_terms` compares bodies
   and copper without their physical layers. This unnecessarily shrinks the
   legal placement space. See the
   [Rust model](https://github.com/halbritt/agent-ee/blob/404eba0e322c2d711202ad30f8bee1c18269c2f8/crates/placement-engine/src/job.rs#L170)
   and [collision evaluation](https://github.com/halbritt/agent-ee/blob/404eba0e322c2d711202ad30f8bee1c18269c2f8/crates/placement-engine/src/job.rs#L756).
   Evidence: `AUDIT_ROOT/probes/two-sided-{input.json,result.json,drc.json}` and
   `AUDIT_ROOT/probes/two-sided.kicad_pcb`.

   **Correction:** represent occupied layers explicitly, including through-hole
   copper and drills. Preserve native final validation. Existing refusals for
   unsupported outlines, orientations, pours, and placement rule areas should
   remain explicit until those cases are implemented.

4. **High — requirement coverage is stronger than requirement verification.
   Confirmed mismatch; broader consequences remain a concern.**

   The requirement machinery proves source coverage, identity, and
   configured-check coverage. It cannot establish that the selected check proves
   the requirement's meaning.

   A concrete example is `ACQ-DELIV-2`: clean setup, reproducibility,
   interruption/resumption, and another operator's ability to repeat the
   workflow. Its mapped check is only `netlist_parity`. That predicate cannot
   establish reproducibility. Separate retained manual evidence exists, but the
   acceptance mapping does not enforce it. The source records are
   `build/acquisition-release/inputs/spec-record.json` at the `ACQ-DELIV-2`
   entry and `build/acquisition-release/inputs/requirement-map.json` at
   `review-acq-deliv-2-topology`.

   The package stage deliberately runs `checks_only=True`, then derives build
   acceptance from check outcomes. Independent engineering review is available
   elsewhere but is not part of this path. This is disclosed behavior, not
   concealed review completion. See
   [`_review_candidate` and `_package`](https://github.com/halbritt/agent-ee/blob/404eba0e322c2d711202ad30f8bee1c18269c2f8/src/agent_ee/build_workflow.py#L188).

   **Correction:** give each requirement an appropriate evidence method.
   Distinguish engineering judgment, executable measurement, native legality,
   export consistency, and physical qualification. Require explicit engineering
   review of consequential assumptions and mappings; do not create a
   general-purpose validator for every prose clause.

5. **Medium–high — competing representations and execution paths repeatedly omit
   capabilities at integration boundaries. Confirmed architectural pressure.**

   The normal build uses `placement_job` and its Rust job model. Older placement,
   block-arrangement, and physical-intent modules retain richer contracts but
   are not interchangeable with that path. The placement job also invokes the
   pinned router directly rather than using the existing schematic-bound
   routing adapter.

   Recent corrections illustrate the consequence: fixed group members
   (`90c5009b`), hard locality (`740567a8`), planes (`d95035e1`), and native rules
   (`aa1d587c`) each needed explicit work to reach the normal path. The current
   missing intent fields continue this pattern. The
   [placement-job documentation](https://github.com/halbritt/agent-ee/blob/404eba0e322c2d711202ad30f8bee1c18269c2f8/docs/placement-job.md)
   explicitly records the parallel boundaries.

   Functional grouping remains especially weak: membership and distances are
   recorded, and important pin-locality constraints are enforced, but a general
   final grouping acceptance criterion is absent. A group name or membership
   census does not prove useful physical grouping.

   **Correction:** establish one authoritative placement contract and an explicit
   list of which public paths support it. Migrate callers before retiring old
   paths. Keep the prepared/incremental Rust search: the earlier repeated binding
   and compaction costs provide a concrete reason for that architecture, although
   they do not establish a current end-to-end speedup. The
   [architecture evidence](https://github.com/halbritt/agent-ee/blob/404eba0e322c2d711202ad30f8bee1c18269c2f8/docs/placement-architecture.md)
   distinguishes measured parent phases from unreliable profiler details.

6. **Medium — verification and resumption cause unnecessarily broad
   requalification. Confirmed mechanism; total wasted effort unmeasured.**

   `policy_files()` hashes every package Python module. Even snapshot-only
   verification compares that set against the current implementation. My attempt
   to verify the retained acquisition review refused with
   `review implementation changed; rerun under the current rules`, although the
   native files and package could still be inspected independently.

   Build resumption likewise binds broad runtime/source identity. An interrupted
   placement stage restarts from the preceding completed stage; the placement
   job's finer checkpointing is not connected to the build journal. See
   [freshness comparison](https://github.com/halbritt/agent-ee/blob/404eba0e322c2d711202ad30f8bee1c18269c2f8/src/agent_ee/cli.py#L219)
   and [build resumption](https://github.com/halbritt/agent-ee/blob/404eba0e322c2d711202ad30f8bee1c18269c2f8/src/agent_ee/build_workflow.py#L438).

   There is also a concrete budget problem: a retained plane-recovery attempt
   completed routing but exhausted its candidate share before native grading.
   A larger job budget succeeded. That is not evidence that the engineering
   constraints were impossible. See the
   [plane-feedback qualification](https://github.com/halbritt/agent-ee/blob/404eba0e322c2d711202ad30f8bee1c18269c2f8/docs/qualification/plane-feedback-2026-10-24.md).

   **Correction:** separate historical integrity verification from acceptance
   under current code; use relevant stage dependencies for invalidation; connect
   stage and subjob resumption; reserve time for final grading. Preserve rejection
   of genuinely stale acceptance.

7. **Medium — manufacturing custody is strong, but export semantics and assembly
   readiness remain narrower than manufacturing readiness. Confirmed limitation,
   not observed corrupt output.**

   The final package checks board identity, file hashes, population, BOM/CPL
   consistency, coordinates, layers, and archive contents. Those are valuable
   protections.

   `fab_consistency` explicitly excludes Gerber geometry. A separate
   `fab_roundtrip` implementation exists, but its prerequisite contract expects
   an earlier `fab_export`; terminal `fab_package` permits only bound `jlc_dfm`
   checks afterward. It therefore does not naturally inspect the exact final
   package. See the
   [declared limitation](https://github.com/halbritt/agent-ee/blob/404eba0e322c2d711202ad30f8bee1c18269c2f8/src/agent_ee/fab_consistency.py#L1078),
   [round-trip capability](https://github.com/halbritt/agent-ee/blob/404eba0e322c2d711202ad30f8bee1c18269c2f8/src/agent_ee/fab_roundtrip.py#L539),
   and [package ordering](https://github.com/halbritt/agent-ee/blob/404eba0e322c2d711202ad30f8bee1c18269c2f8/src/agent_ee/fab_package.py#L145).

   The acquisition release also leaves supplier preview and physical validation
   unperformed; supplier rotation correction is not declared.

   **Correction:** adapt the existing round-trip checker to consume the sealed
   final package. Keep its geometric limitations explicit. Publish separate
   states for file release, supplier assembly readiness, and tested hardware.

**The actual workflow is coherent after substantial engineering preparation.**
The following distinguishes who supplies decisions from what executes:

| Stage | Normal input → output | Decision responsibility and demonstrated boundary |
| --- | --- | --- |
| Intent and requirements | Authored specification, requirement record and map → captured scope | Agent/engineer interprets intent and selects evidence. Compiler checks declarations and coverage; general authoring workflow remains incomplete. |
| Circuit and components | Authored topology, parts, CAD, sourcing and ratings → bound connectivity and parts | Agent/engineer chooses architecture and components. Tooling validates selections; it does not generally design the circuit. |
| Schematic | Bound connectivity and layout → native schematic, netlist and footprints | Native emission and annotation are real. Acquisition demonstrated; hierarchy/edit cases have separate evidence. Arbitrary manual-edit preservation is limited. |
| Board definition | Envelope, stackup, rules and fixed parts → native project/PCB | Authored mechanical and fabrication decisions survive through captured native files. |
| Placement | Native board, groups and supported intent → placed native candidate | Deterministic search chooses poses. Locks, edges and hard locality have real checks; sides, keepouts and grouping have the gaps above. |
| Routing | Candidate, rules, optional planes and routing declarations → routed/refilled board | Pinned tooling routes and performs bounded recovery. It does not generally reconsider circuit architecture or component choice after failure. |
| Engineering verification | Final native design plus authored checks/models → individual results | ERC/DRC, topology, calculations and SPICE are useful within their declared models. Mapping and applicability remain engineering responsibilities. |
| Fabrication and assembly | Final candidate → sealed Gerbers, drills, BOM and CPL | Actual export and custody demonstrated. Supplier-specific interpretation and physical qualification remain separate. |

**Meaningful KRT coverage is mixed.** I inspected current upstream implementation,
not just command registration. Except where stated, this establishes availability
of upstream code, not its successful execution on these designs.

| Area | Assessment of agent-ee integration |
| --- | --- |
| Functional placement | Incomplete integration. Current KRT has richer floorplan declarations, side policies, keepouts, and an agent placement-planning workflow. Agent-ee has useful groups/locality/native preservation but the confirmed omissions above. |
| Routing and recovery | Demonstrated bounded capability, sometimes better. Normal routing and targeted thermal/dangling-track recovery have retained native evidence. Fresh regrading confirmed one owned heterogeneous-pair result clean where the retained stock result had 33 rule findings. |
| Power and planes | Partial. Declared planes and targeted recovery reach the normal path. Existing pours, broader plane repair strategies, and general return-path reasoning remain incomplete. |
| Differential and timing constraints | Several demonstrated bounded cases; broader coverage missing. Pair matching, transitions, explicit groups, and nominal delay matching have native evidence. AC-composed matching, broader DDR/package timing, and more general impedance cases remain open. |
| Buses | Bounded geometric equivalence. Native bus articles establish useful routing behavior. They do not establish general bus electrical/timing correctness or all current upstream bus algorithms. |
| Fanout | Upstream functionality available, normal-path qualification missing. KRT contains substantial QFN/QFP/BGA geometry and fanout logic. Presence of related owned modules does not demonstrate orchestrated fanout through `build run`. |
| Verification | Complementary strengths. Agent-ee adds strong requirement/artifact custody and native final checks. Neither native legality nor impedance equations alone establish physical signal integrity, EMC, thermal performance, or assembly success. |
| Automation | Different strengths. Agent-ee has stronger durable build/evidence machinery; upstream has a more developed placement-agent procedure. Agent-ee's missing intent-authoring workflow is the larger product gap. |

Relevant upstream implementations include
[floorplan validation](https://github.com/drandyhaas/KiCadRoutingTools/blob/b232bac7bd31c25c13f7f2feff556d884bb73e75/py_placer/placement/floorplan.py),
[AC-coupled net discovery](https://github.com/drandyhaas/KiCadRoutingTools/blob/b232bac7bd31c25c13f7f2feff556d884bb73e75/py_router/diff_xnet.py),
[impedance models](https://github.com/drandyhaas/KiCadRoutingTools/blob/b232bac7bd31c25c13f7f2feff556d884bb73e75/py_router/impedance.py),
and [BGA fanout](https://github.com/drandyhaas/KiCadRoutingTools/tree/b232bac7bd31c25c13f7f2feff556d884bb73e75/py_router/bga_fanout).

The fresh matched-result comparison concerns older stock revision `e358107`, not
a new routing comparison against `b232bac7`. `AUDIT_ROOT/matched-drc-recheck.json`
supports a narrow advantage, not general superiority. The owned board had zero
native findings and opens; the retained stock board had 18 track-width,
14 differential-gap, and one uncoupled-length finding, with zero opens.
Both inspected board files remained byte-identical through the recheck.

**Development is struggling to converge for several distinct reasons.**

The strongest explanation is integration and prioritization, rather than a
demonstrated fundamental geometric limit. Recent work has fixed real omissions
and produced real routing improvements. But the
[acceptance map at the audited revision](https://github.com/halbritt/agent-ee/blob/404eba0e322c2d711202ad30f8bee1c18269c2f8/docs/system-acceptance.md)
spans two/four/six layers, analog, power, clocks, buses, differential signals,
hierarchical placement, sides, keepouts, and QFN/BGA escape. Its next named
capability is AC-composed differential matching while basic intent handling
remains incomplete. Chasing a moving upstream implementation makes that finish
line still less finite.

Some infrastructure work was justified. The earlier placement measurements
identified repeated compaction/binding work. The ngspice inverse-switch problem
was a real simulator defect; the corrected runtime passed my targeted analytical
checks. Native rule preservation, plane connectivity, and thermal checks protect
actual engineering properties.

Other activity creates avoidable cost. The `404eba0e` router feature update
changed 13 files, including pins and declarations in installation, adapters,
contracts, tests, and documentation. Whole-package freshness then makes old
results unusable through ordinary verification. Many stage and feature tests
exist, but inspection found no automated public build test completing all seven
stages to accepted output. File or test counts alone are not measures of quality.

The obligation sources also matter:

| Obligation | Source and appropriate treatment |
| --- | --- |
| Sole-agent work and the esp_prog tooling case | Active owner direction. Historical dispatch/integration assignments do not apply. |
| Component voltage, stability, mounting and temperature limits | Manufacturer constraints. Preserve them and verify applicability. |
| Rail margin, effective capacitance, copper loss and reverse stress | Necessary derived engineering conditions. Use sourced calculations and applicable simulation, then physical tests where models cannot establish the claim. |
| Acquisition dimensions, operating envelope, locality limits and synthetic matching tolerances | Recorded engineering-agent selections unless supported by another authority. They can be reconsidered transparently; they are not automatically owner requirements. |
| Full physical qualification | Required before claims about actual hardware performance. It need not block an explicitly limited manufacturing-file release. |

Several historical refusals were appropriate. A nominal 1.6 mm board with
positive tolerance did not meet a connector's maximum thickness; changing
thickness exposed a resistor mounting-guideline issue. Nominal connector height
was not a guaranteed installed-height ceiling. Typical capacitor and current
data did not become guaranteed bounds merely because a validator could parse
them. These called for corrected assumptions, component choices, and scoped
evidence. The retained
[acquisition closure](https://github.com/halbritt/agent-ee/blob/404eba0e322c2d711202ad30f8bee1c18269c2f8/docs/qualification/acquisition-closure-2026-10-24.md)
distinguishes agent-selected requirements from component constraints.

Qualification designs should have distinct roles. The RC board is a useful
electrical negative control. The acquisition design is a reasonable first
complete multiblock release. esp_prog should remain the owner-named
imported-native routing test, with its remaining warnings and assembly
limitations reported honestly. Synthetic pair/bus boards are useful capability
tests, not substitutes for complete products. The held telemetry pod should
remain held.

**The smallest coherent corrective sequence is:**

1. Freeze a first-release milestone and its evidence contract. Use the
   acquisition workflow plus the required esp_prog tooling regression. Specify
   supported geometry, components, layers, operating conditions, and export
   scope. Preserve the broader owner goal as subsequent milestones rather than
   declaring it satisfied.
2. Repair the placement contract before adding more routing features. Reject
   unknown intent; preserve side/layer occupancy; connect supported keepouts,
   mechanical restrictions and hard grouping/locality requirements to
   final-native checks. Use the two audit probes as decisive negative/positive
   cases.
3. Make input authoring a supported agent workflow. Reuse the existing
   requirement, parts, circuit and build formats. Have a fresh agent or operator
   produce a small acquisition variant from an engineering brief, including
   sourced decisions and appropriate verification methods. Record every
   undocumented intervention needed.
4. Qualify the complete public build and final package. Add one meaningful
   native seven-stage regression, a semantic electrical negative, an
   ignored/violated-constraint negative, and an interruption/resumption case.
   Adapt existing export checking to the exact sealed package. These tests
   protect outcomes that the current feature tests miss.
5. Then reduce execution overhead and extend capability. Connect finer
   resumption, narrow invalidation, and consolidate pin declarations. Selectively
   reuse KRT placement constraints, fanout, plane recovery, and timing
   functionality. Defer broader DDR, AC-composed matching, advanced impedance,
   and BGA qualification until the first workflow remains dependable.

There is no evidence-based reason to remove the native checks, immutable
receipts, prepared Rust search, or owned router wholesale. Their demonstrated
value should survive consolidation.

**A practical first-release definition of done would require:**

- A fresh agent/operator can take a declared engineering brief through the
  supported public workflow without undocumented helper scripts.
- Every hard requirement is enforced, explicitly rejected as unsupported, or
  assigned an appropriate unresolved disposition.
- Circuit decisions and component applicability receive engineering review;
  meaningful electrical negatives fail even when ERC and topology parity pass.
- Final saved native files preserve identity, connectivity, rules, required
  placement relationships, and supported mechanical constraints; routing has
  zero opens and no undisposed required findings.
- The delivered fabrication/assembly files are generated from that exact
  accepted candidate and pass applicable consistency checks.
- Captured inputs and a fixed runtime reproduce the release; interruption
  resumes from verified work.
- The release states separately what is established about electrical behavior,
  geometric legality, manufacturing files, supplier assembly interpretation,
  and physical hardware.

This is a finite delivery milestone, not permission to waive manufacturer limits
or redefine the ultimate product scope.

**Fresh checks and retained evidence.** These are observations made during this
audit, not a claim to have rerun every qualification in the repository:

| Check performed | Command or method | Result and evidence under `AUDIT_ROOT` |
| --- | --- | --- |
| Build-review custody | `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /usr/bin/python3 -m unittest tests.test_build_review -v` from the source snapshot | Eight passed, no skips; `test-build-review.log`. |
| Corrected ngspice | `/usr/bin/python3 docker/ngspice/verify.py --out /audit/ngspice-corrected` inside the existing native image, with the scratch root mounted as `/audit` | Twenty analytical checks passed; `ngspice-corrected/qualification.json` and `ngspice-corrected.log`. |
| Acquisition PCB | `kicad-cli pcb drc --format json --severity-all --refill-zones --exit-code-violations -o REPORT BOARD` on the isolated native copy | Exit 0, no violations or opens; `acquisition-drc.json`. Captured project severities apply. No fresh schematic-parity command was run. |
| Acquisition schematic | `kicad-cli sch erc --format json --severity-all --exit-code-violations -o REPORT SCHEMATIC` on the isolated native copy | Exit 0, no violations; `acquisition-erc.json`. |
| Acquisition package | Read the retained package manifest and recompute its listed file hashes | All 17 listed files matched. This is retained-artifact integrity, not a full rebuild. |
| Ignored intent | `python3 -m agent_ee.cli place run /audit/probes/public-ignored-intent/inputs/job.json --out /audit/probes/public-ignored-intent/job` using the source snapshot in the native image | Exit 0, completed/eligible despite ignored submitted constraints; `probes/public-ignored-intent/summary.json`, stdout/stderr and full job directory. |
| Opposite-side placement | Native capture, exact-source Rust evaluation, and independent native DRC | Five solver pair conflicts against zero native geometric violations; `probes/two-sided-result.json`. One expected native open remained. |
| Retained heterogeneous pairs | Fresh native DRC on isolated owned and stock outputs; compare original and final file hashes | Owned clean; stock 33 findings; both zero opens and unchanged bytes. `matched-drc-recheck.json`. No fresh routing was performed. |

The command table records executions, not a portable turnkey tutorial.
`REPORT`, `BOARD`, and `SCHEMATIC` stand for the isolated artifact paths;
`/audit` was the container mount of the retained scratch root. The public
ignored-intent inputs were prepared with
[`tests.test_placement_job_rules.prepare`](https://github.com/halbritt/agent-ee/blob/404eba0e322c2d711202ad30f8bee1c18269c2f8/tests/test_placement_job_rules.py#L21),
then the three declarations described in finding 1 were added and their input
digest updated. The retained `inputs/job.json` contains the exact invocation
inputs and hashes.

The retained acquisition run was
`build/acquisition-release/release-build-v7`, produced under earlier source
revision `466e6feb`. Its actual accepted seven-stage report and individual check
results were inspected, and its native files were copied for fresh checks.
The copied PCB SHA-256 was
`d0a898a2a254c090ba1a4185320938241c255e79c587c82abb27a0bcafcd66e7`;
the schematic SHA-256 was
`fc552de4568240bb7e4093817c060c11bc00d832459e5c5ea004e5575f5cf294`.
`AUDIT_ROOT/inherited-release-summary.json` retains the inspected report data.
The final top render was also inspected; a render is not proof of physical fit.

**Remaining uncertainty is bounded.** I did not run the whole suite, a fresh
seven-stage build at the audited revision, or a fresh latest-upstream routing
comparison. The next investigations most likely to change this assessment are a
clean complete build at the corrected revision; one fresh-agent brief-to-inputs
exercise; opposite-side/keepout qualification after the contract fix; and matched
current-KRT experiments for the specific subsystem proposed for reuse.

Supplier preview and bench testing are still required for stronger assembly and
physical-performance claims. I found no fresh evidence establishing a new
electrical defect in the retained acquisition design; its model applicability
was not exhaustively re-audited. Historical accounts of the implementation
agent's broad goal were treated as repository-retained claims, not newly
authenticated owner instructions. The active audit authorization and sole-agent
direction controlled this investigation.

The three changes most likely to improve the project's outcome are:

1. **Make engineering constraints survive every stage.** Silent omission and
   false geometric conflicts directly undermine both trust and solver
   effectiveness.
2. **Complete the agent's brief-to-build-input workflow.** This turns the existing
   compiler and verification machinery into the intended product.
3. **Deliver and maintain one finite, fully exercised release before expanding
   parity.** A complete public-path regression and scoped requalification will
   make progress cumulative instead of repeatedly reopening the whole system.
