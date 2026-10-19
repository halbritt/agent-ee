# Declaring and extending review coverage

The executable examples are `fixtures/rc/control.scope.json`,
`fixtures/rc/seeded.scope.json`, `scopes/donor.json`, and
`scopes/donor-case-alias.json`. The last two are project-specific input/scope
choices; the orchestration and checks contain no FOME-MC requirements.

## Existing checks

| Kind | Criterion and evidence |
| --- | --- |
| `land_pattern` | [Captured supplier land patterns](land-pattern.md) against native KiCad footprint copper, holes and pin numbering. Exact or bounded geometry evidence; unsupported shapes leave coverage incomplete. Does not establish supplier authenticity, approved-part identity or manufacturability. |
| `footprint_assignment` | [Captured library pad inventories and explicit assignments](footprint-libraries.md); declared symbol-pin/pad parity. With `netlist_check`, [native-bound](native-binding.md) exact binding of every native board component, its selection and its captured library pins. No physical fit. |
| `netlist_parity` | [Exact intent or golden netlist parity](netlist-parity.md) against full native XML; resolved sources must rederive from captured intent. No footprint or electrical adequacy. |
| `symbol_pins` | [Captured symbol context](symbol-context.md) and declared pin-reference coverage; no automatic instance binding or datasheet-function proof. |
| `netlist_extra` | [Captured direct-net topology](netlist-extra.md) for exact declared two-terminal classes and anchored rail pairs; hierarchical basename islands emit advisory warnings. |
| `regeneration_determinism` | [UUID-insensitive canonical diff](design-diff.md) of two captured boards, schematics, netlists or JSON probe outputs at a declared coordinate precision; byte differences recorded, not graded; unparseable sides malformed and missing producer outputs unknown. No electrical judgement of the differences. |
| `topology_rules` | [Requirement topology rules](topology-rules.md): requirement-tagged pin-net, no-connect, count, series, forbidden, element, value-equality and expected-component rules over the native netlist and owner-declared classes; absent names, unreadable values, unclassified parts and an exhausted matcher bound are unknown. No electrical adequacy, proximity or geometry. |
| `net_names` | [Net-name provenance](net-names.md): owner-declared required names with terminal anchors must each name exactly one native net; suggested collisions are disambiguated as information; missing level annotations are unknown. No label geometry or ERC. |
| `design_rules` | [Design-rule cards](design-rules.md): enforced cards of a captured rule pack, graded by closed connectivity adapters (I2C pull-up window and address uniqueness, crystal load caps, fact-gated reset RC and USB series resistors, decoupling) or by one delegated single-predicate check. Advisory cards are writer guidance only and cannot be mapped. |
| `package_consistency` | [Footprint against pinned catalog package names](package-consistency.md) through a captured, hashed mapping table; unmapped names are unknown, class mismatch follows explicit policy; no land-pattern geometry. |
| `schematic_lint` | [Schematic readability lint](schematic-lint.md) over embedded symbol geometry and native net membership: overlaps, wires through pins, rail/divider/regulator/LED placement, rail labels, grid, properties, references, title block and diff-pair names. Style findings unless a profile raises a rule to defect; unresolved symbols are missing coverage. |
| `interval_assertions` | [Worst-case interval assertions](interval-assertions.md): owner-declared relations over pinned catalog tolerances/ratings and declared requirement intervals, exact rational bounds, shared-symbol correlation; unknown operands and unbounded divisions are unknown and defects need an attained corner. No equation-to-netlist proof, temperature or aging. |
| `netclass_completeness` | [Captured Default netclass field presence](netclass-completeness.md), qualified for KiCad 10.0.7 settings schema 5; no value or rendering validation. |
| `fab_consistency` | [Sealed fab_export staging tree](fab-consistency.md) against its manifest, the pinned kicad-cli, the captured board, the export's sealed netlist and earlier sealed geometry: inventory, layers, PTH/NPTH drill, BOM/CPL/board designator parity, LCSC footprint uniqueness, CPL side and 3D-model resolution. No coordinate, Gerber geometry or DFM coverage. |
| `measurements` | Pinned installed producer, captured parser, candidate-bound scalar metrics and coordinator bounds; requires independently qualified models and parser semantics. |
| `coupled_margins` | Same-condition results from distinct declared domains; conjunctive limits, retaining calibrated source intervals and source facts. |
| `datasheet_facts` | Captured PDF hash/page/exact-quote/value verification with sandboxed hash/version-pinned Poppler. Textual support only; applicability and row/conditions remain engineer judgments. |
| `pin_map` | Native XML symbol pin names against verified pin-function facts and explicit engineer applicability; missing/unverified dependencies are unsupported. See [datasheet facts](datasheet-facts.md). |
| `erc` | Native all-severity ERC plus complete hierarchy coverage. Missing child files prevent a clean result. |
| `drc` | Native all-severity DRC, saved fill, and schematic parity. Reports retain exclusions and native items. A violation is distinct from execution failure. |
| `netlist` | Complete-hierarchy native XML export, with derived connectivity, DNP, native symbol identity, and sheet/UUID provenance. Informational rather than functional acceptance. |
| `dfm_geometry` | [Captured DFM thresholds](dfm-geometry.md) on exact saved-board pcbnew geometry: annular ring, via aspect ratio (saved thickness), acid traps, SMD bridge gap, courtyard spacing, diff-pair skew with arcs, united copper balance. Coded findings with coordinates and no score. Unevaluable items leave a rule unsupported. No etch, thermal relief, paste, panel, width or clearance coverage. |
| `placement_congestion` | [Pre-route placement and congestion report](placement-congestion.md): advisory ranked pad overlap/clearance, courtyard and outline issues and quarter-turn ratsnest suggestions, each with nearby parts, free space per direction and a verified axis move or rotation; congested regions with an uncalibrated RUDY/Poisson failure probability and computed severity bands. Never passes; `info` without a proved defect; failed probe is unknown. No routing, copper-pour or hole-clearance coverage. |
| `geometry` | Saved-board pcbnew extraction. `params.min_track_width_mm` retains its historical straight-segment meaning; `params.exclude_nets` records explicit exemptions. Opt-in `params.criterion: "copper-width/1"` covers straight tracks and native arcs with explicit coverage evidence (see below). |
| `active_spice` | Explicit captured subcircuits, complete native pin bindings, voltage/current sources and declared transient-voltage, DC-sweep and AC complex-transfer measurements; see the [contract and limits](active-spice.md). [Role templates](spice-templates.md) count only supported bounded measurements. Owned synthetic fixtures prove software mechanics, not vendor accuracy. |
| `passive_corners` | [Qualified first-order unloaded RC](passive-corners.md), complete independent tolerance endpoints and retained numerical envelope; no nominal empirical transfer. |
| `fab_export` | [JLCPCB fab package](fab-export.md) from the captured board through sandboxed `kicad-cli`: Gerbers, PTH/NPTH drills, BOM, CPL, deterministic zip and a board-bound manifest. `pass` means complete validated exports only; no DFM, rotation policy or cross-file consistency gate. |
| `rc_transient` | Prior `params.netlist_check` supplies actual native connectivity/values. A supported populated passive R/C/L network receives the declared `input_net`, `output_net`, `ground_net`, positive `step_v`, `stop_ms`, and named measurements with `at_ms`, optional `min`/`max`. |

For `rc_transient`, omitted `step_v` uses 1.0 V and omitted `stop_ms` uses
5.0 ms. The result records the effective amplitude in `details.step_v` without
adding defaults to the captured scope. Omitted `step_v` and explicit `1.0`
produce the same metric condition, but their different scopes retain distinct
candidate identities.

Condition text preserves historical explicit amplitude spellings: `1`, `1.0`
and `"1.00"` remain `1V`, `1.0V` and `1.00V`. Measurement times retain the
existing float conversion (`1`, `1.0` and `"1.00"` become `1.0ms`). There is no
condition or empirical-observation migration; use the original condition text
when matching retained observations. This implementation change alters the
runtime policy hash, so historical results require their original policy for
verification and do not become fresh evidence under the new implementation.

For broader routing-width coverage, declare all four parameters:

```json
{
  "criterion": "copper-width/1",
  "covered_primitives": ["PCB_TRACK", "PCB_ARC"],
  "min_track_width_mm": 0.20,
  "exclude_nets": []
}
```

`copper-width/1` evaluates the native board track collection, including arcs.
The covered class list must contain exactly `PCB_TRACK` and `PCB_ARC`.
Exclusions match net names exactly, including an explicitly listed empty net
name. Vias are counted separately; pads, zones and graphic copper shapes are
outside this routing-width criterion. Via drill/annular-ring and zone geometry
need separate criteria.

`work/<check-id>/copper-width.json` retains candidate ID, board hash, pcbnew
version, primitive UUID/type/net/layer/width/limit/status, all proven offenders,
and eligible, checked, excluded and unsupported counts. Eligible means a
non-via track item not exempted by a known excluded net; it includes unsupported
items. Unsupported is a subset of eligible, so eligible = checked + unsupported.
The comparison uses native integer nanometres without the legacy report's
four-decimal rounding. Any unsupported or malformed eligible item, or an empty
eligible set, yields `execution: unsupported`, `outcome: unknown`, even when
other checked items are narrow. Complete coverage with a proven below-limit
track or arc yields a defect; complete coverage without offenders passes.

Checks without the new criterion retain straight-segment semantics, including
existing scope/2 baselines. Changing a criterion, its class coverage or exclusions
changes the captured baseline identity. Broader campaign acceptance therefore
requires a new baseline; an old passing review never gains arc coverage.
The native regression tests create actual `PCB_ARC` boards through pcbnew and
run the sandboxed probe. Malformed and future-class cases use synthetic parser
inputs; they do not claim native support for those classes or physical validation.

The passive generator excludes DNP parts and identifies passives through native
symbol class, rather than assuming a designator proves component identity.
Unmodeled active devices are unsupported, not replaced by an ideal approximation.
An unbounded measurement is informative. A bounded measurement is an explicit
criterion. Missing/non-finite measurements, invalid raw traces and solver errors
are unknown execution coverage rather than a passing experiment.

Values use raw SPICE scalar syntax, not general schematic display notation:
`Meg` denotes mega, while `M` follows SPICE's milli interpretation. Embedded-value
notations such as `4k7` are unsupported. Check the recorded original value,
parsed value and generated deck against the intended component/model before
using the simulation as electrical evidence. The demonstrations use unambiguous
`10k`, `100k` and `100n` values; broader value normalization is not demonstrated.

## Declared interface preservation

The [hardware interface check](hardware-interface.md) evaluates captured owner
predicates over full native schematic terminal inventory and direct conductors.
Unknown mappings yield incomplete coverage. It protects only declared identities,
population, exact values and net relations; it never infers obligations or treats
R/C/L as shorts. A global label spelled like a generated no-connect name preserves
a singleton conductor only with exact source-geometry proof. Source text, present
or absent, is not evidence: kicad-cli expands label and power-Value text
variables, so an unexpanded name source touching the pin, and geometry outside
the qualified domain, are unknown. Physical
access, PCB routing and transfer behavior need separate checks.

## Input and packet choices

`inputs` selects the actual native project, relevant external libraries, custom
rules, requirements, models and reference documents. Capture is explicit; it
does not discover every possible external dependency automatically. Keep the
selection current when adding symbols, footprints or models. Modified selected
files are captured as bytes even if their repository HEAD is unchanged.

`packet_inputs` selects original native text for the initial reviewers.
`requirements` selects captured requirement files. `packet_max_bytes` is a total
text budget between 1,024 and 1,000,000 bytes; each text artifact is additionally
limited to 100,000 bytes. `packet_artifacts` names required generated artifacts to
place early in the packet. Missing required artifacts fail the run; with no
explicit declaration, available evidence and failed-check diagnostics are supplied.
Every omitted binary and partial text extent is recorded. A reviewer cannot
claim to have inspected omitted data just because its hash/path is present.

Reviewers and adjudicator use explicit [profiles](reviewer-profiles.md). Claude,
Codex and Agy subscription adapters support ordinary investigative tools. Every
declared role must complete. More agents or model families do not prove independence.
There is no automatic provider or account fallback. Initial review scheduling
runs at most two calls concurrently while retaining role-specific schemas,
contexts and sandbox mounts. Scripted sleeping subprocess tests exercise overlap,
asymmetric failure/timeouts, retained artifacts, queued harness stops and the
adjudication barrier. Isolated coordinator processes receiving actual SIGINT
exercise cancellation during subprocess launch and waiting, queued-role suppression,
partial artifacts, and absence of surviving scripted child processes. These tests
establish scheduling behavior, not live-model latency or engineering review quality.

## Adding an engineering check

1. Write independent expected outcomes, applicable operating conditions and
   evidence requirements before implementing the check. Specify which claims a
   calculation, datasheet, model, native geometry or measurement can establish.
2. Implement a handler in `src/agent_ee/checks.py` and register its kind in
   `CHECK_FUNCS`. Use `Ctx` to record native commands/results and
   `executor.execute` for isolated subprocess work. Candidate files are at
   `/candidate`, writable scratch at `/work`; Python helpers are available at
   `/app`. Dependencies/configuration that affect results need recorded identity.
3. Retain original tool/source artifacts. An extraction must identify its source
   through the candidate manifest and useful native locators such as component,
   pin, net, UUID, document page or measurement. Captured PDF-derived facts need
   a path/page relationship to the captured original, not merely a model summary.
4. Return separate `execution` and design `outcome`, evidence paths, details and
   limitations. Raise `CheckError` for unsupported, failed or malformed work;
   `run_checks` persists each completed result. Never substitute successful
   completion for an unmeasured requirement.
5. Exercise the useful success case and consequential failure boundaries. Then
   review a fresh candidate with actual agent judgments. Tests of the coordinator
   do not demonstrate the electrical truth of a new check.

Changing checks, limits, models, exclusions or requirements creates new evidence
obligations. This implementation conservatively reruns instead of selectively
reusing acceptance. Its current correction command requires the complete new
review to satisfy criteria, even if one local repair's affected checks pass; it
does not close individual repairs while other blocking findings remain.

## Coverage that remains outside the demonstrations

The separate [passive-corner qualification](passive-corners.md) covers only its
explicit unloaded first-order RC domain.

The nominal RC demonstration does not establish active-device behavior, component
tolerances, thermal limits, manufacturing approval, EMC or vehicle transients.
The donor's modules and assembled harness introduce behavior beyond the captured
carrier. Startup/reset/brownout safety, ignition energy, wideband loop stability,
potting temperatures and bench performance need dedicated requirements, sources,
checks/models and physical validation. Record these as limits or material open
questions; do not turn an unrequested condition into a speculative blocking
defect on a narrower valid control.

## Scope/2 and empirical coverage

The [baseline contract](scope-reference.md#version-2-baseline) requires every check
and requirement ID to be mapped. It preserves complete normative sources and
criteria, but does not infer missing engineering requirements. Scope/2 accepted
defects must be coordinator check findings backed by sealed evidence.

The [campaign](campaign-guide.md) provides bounded iteration and regression
protection. [Bench feedback](bench-feedback.md) adds measured residual envelopes
at declared operating conditions; generated replay cases test those retained
observations against unchanged limits. Neither facility invents unmeasured fault
conditions or qualifies a new physical model from provenance text alone.

Use [generic adapters](measurement-adapters.md) when an installed solver or
captured manufacturing/telemetry tool can establish an additional criterion.
Record what it actually computes and what remains out of scope. A domain string
such as `thermal` is declared tool routing metadata, not independent proof of
thermal qualification.

## Test environment coverage

Native-dependent tests preflight their sandbox and installed prerequisites.
Unavailable prerequisites produce explicit skips by default;
`AGENT_EE_REQUIRE_TOOLCHAIN=1` makes them fail. A skipped test supplies no native
execution evidence. Tests that read the owner's fome-mc object store follow the
same rule; a strict run names them as required tests not executed and fails
([container](container.md#required-external-inputs)). Parser, orchestration and failure tests that do not require
those tools continue to run. See the [maintainer guide](maintainer-guide.md#development-and-checks)
for the strict command. Production missing-tool classification is unchanged.

## Opt-in executable coverage audit

[Scope/3](scope-reference.md#version-3-executable-acceptance-coverage) audits
configured predicates before launch. Netlist export and geometry without a width
limit are supported informational checks. ERC/DRC have Boolean violation
predicates; captured sibling `.kicad_pro` and `.kicad_dru` files are immutable
verification dependencies because they define native rules. Geometry requires a valid positive finite limit; the optional
`copper-width/1` contract also requires exact track/arc class coverage and explicit
net exclusions. Passive transient checks require an earlier native netlist,
valid simulation parameters, and at least one bounded measurement to cover an
acceptance obligation. Unbounded transient measurements remain informational.

Generic measurement checks require a captured parser, pinned producer declaration,
captured program inputs and nonempty limits with matching metric unit/condition
identities. Candidate program arguments must appear in immutable `tool.inputs`.
Coupled margins require earlier measurement-producing sources, statically
resolvable metric identities, distinct domains and consistent conditions.
Adapters, producer inputs and calibration inputs are immutable baseline files
and cannot become campaign repair targets.

This audit establishes that a configured predicate can report a violation.
It does not establish requirement completeness, model qualification, runtime
coverage, electrical truth or a passing design. Native execution still decides
whether the current design is supported and meets the declared bounds.

The native scope/3 test corpus uses independently committed expectations in
`tests/fixtures/capability-mutations.json`. Matched RC controls and wrong values,
narrow straight tracks/arcs and native rule defects run through KiCad/ngspice,
independent initial reviewer fixtures and centralized scripted adjudication.
Malformed schematics and unsupported value notation are unknown coverage and are
not counted as detected physical defects. The RC voltage oracle uses the
independent analytical step equation. These are coordinator harness tests, not
live-model detection performance or hardware validation.

### Capability descriptors for new handlers

Execution registration alone never grants scope/3 capability. A new kind remains
unsupported until a focused pure validator in `capabilities.py` validates its
actual parameters, prerequisites, predicates and immutable dependencies. The
registry/execution agreement test intentionally requires this follow-up when
handlers are integrated. The installed-analyzer, datasheet and active-SPICE
descriptors now enforce these contracts:

- Installed analyzer: supported contract/version and explicit known blocking
  rules, captured rule/program inputs and retained tool identity. Actual evaluated
  rule coverage remains a runtime obligation; an empty blocking set is informational.
- Facts verification: captured facts/PDF dependencies and citation support
  semantics, distinguished from an electrical consumer's pin-function predicate.
  A citation verifier that returns unsupported rather than defects for invalid
  support is informational. A pin-map consumer needs declared mappings and
  earlier verified facts/native connectivity; failed verification cannot pass.
- Active SPICE: supported analyses and bounded measurements, earlier native
  netlist, explicit model/subcircuit/pin maps and captured recursive model
  dependencies. Runtime device coverage and model containment remain mandatory.
  If static validation requires file contents, capture and baseline verification
  must use the same narrow read-only captured-input interface.

Descriptor semantics are coordinator policy. Changes require capability version
and policy review; old evidence never gains new audited coverage automatically.

## Installed analyzer coverage

The `kicad_happy` check invokes the owner's installed v2.3.1 analyzers and
retains schema `1.4.0` outputs. Its [adapter contracts](kicad-happy.md) define the
exact supported topology, native branches, immutable operating assumptions and
skip behavior. Running all four analyzers does not qualify every detector.

| Category | Qualified criterion | Limit |
| --- | --- | --- |
| Passive networks | Native low-pass RC cutoff/time constant bounds. | Other networks informative. |
| Power trees | Complete native non-ground typed power-pin inventory, source uniqueness and declared domains. | No keyword load/default-current supply-adequacy claim. |
| Regulator feedback | Ideal six-pin ground-referenced TPS7A4501 noninverting LDO divider math with captured Vref and native connectivity/provenance. | No fixed/unknown/heuristic-sweep Vref or physical regulator model. |
| Protection | Explicit native USB VBUS ESD-presence subcheck. | Keyword/connectivity heuristic only; no surge/withstand rating. |
| Derating | Explicit operating/rated voltage fraction for selected power/ground capacitors. | No resistor power, IC abs-max or capacitance/DC-bias coverage. |
| Thermal | Declared heuristic LDO estimate, generic SOT-223/SOT-23 table values and unity board correction. | No datasheet thermal resistance, physical heat-flow or whole-board thermal pass. |
| EMC | Exact ES-001 nearest electrically associated connector distance branch, <=50 mm eligibility and 15 mm threshold. | No ES-002 zero-via coverage, routing/ESD physics or EMC certification. |

Original [owned fixture assumptions](../fixtures/kicad-happy/qualified/requirements.md)
and [RC expectations](../fixtures/kicad-happy/EXPECTATIONS.md) distinguish real
native observations from synthetic parser boundary cases. Unrecognized envelopes
and missing counted evaluations remain unsupported. Source assumptions and check
parameters remain immutable scope/2 baseline inputs; the pure capability helper
is registered in scope/3 with captured requirement-source ownership; historical
scope/2 evidence remains unchanged.

Full strict host checks and owner review remain separate from qualification in
the common completion image. A passing declared proxy never certifies hardware.


Pre-inference drift tests count scripted provider dispatches below the production
guard. They cover initial, adjudication and implementation refusal, independent
concurrent calls and the adjudication barrier, a real invalid Bubblewrap option,
Claude executable override mounting, and real SIGINT during a slow networkless
probe. Retained receipt tampering is checked separately. Unused-harness tests
replace an unused harness after checks and before reviewer and writer launch,
and the review and campaign complete with recorded observations. Replacing the
used harness or its tool host still refuses. A mixed-harness campaign (claude
writer, codex reviewers) refuses `verify-campaign` when the writer's harness
changes after completion and reports an unused harness once. These tests establish
coordinator behavior, not provider quota, live-model quality or elimination of
external upgrade races. See [preflight receipts](result-reference.md#pre-inference-environment-receipts).

### Exact empirical applicability

New bench ingestion emits calibration/2 with exact captured design-dependency
hashes. Unchanged designs replay residual envelopes against unchanged bounds.
Changed values, connectivity, PCB bytes, libraries, unclassified inputs or file
inventory make calibrated acceptance unavailable and produce a structured
recalibration request. Validated coordinator evidence is excluded by reconstructed
provenance closure, never by a directory prefix or author list. Executed nominal
predictions remain unqualified diagnostics for fresh bench recovery. Historical
calibration/1 does not acquire scope/3 qualification. See [bench feedback](bench-feedback.md).
These contracts do not authenticate instruments or establish physical validation.

## Concern evidence replay

[Standalone evidence requests](evidence-requests.md) bind unresolved concerns to
exact retained candidates. The dedicated Linux x86-64 Python replay lane has
networkless read-only isolation, single-process resource limits and bounded logs.
It supports no arbitrary shell, external tool, writable artifact or aggregate
job-memory budget. Requests and replay assertions supply no acceptance coverage;
graduation exports are inert proposals. Existing sealed coordinator check
witnesses remain the only scope/2 and scope/3 acceptance path.

## Packet navigation

Packet navigation tests cover owned native RC source locators, candidate rename
refresh, public verification after tampering, disconnected pins, duplicate
references, arc/unknown primitive coverage, bounded views and explicit oversized
entry pointers. Synthetic cross-domain records verify exact unit/condition
preservation and unresolved entity scope. Peer marker tests verify the common
index excludes undeclared files and preserves the existing adjudicator-only
experiment mount. These are navigation/coordinator checks; model missed-defect
performance and physical correctness are not established by an index.

Ambiguous packet-index regression fixtures cover missing native references and
pin numbers, duplicate scoped UUIDs and net codes, multiple-net and duplicate
same-net pin membership, and undeclared sheet paths. They assert unresolved
coverage, retained raw access, and suppression of qualified consumer/requirement/
coupling links; distinct declared sheets can reuse a symbol UUID. Public verify
also rejects stale navigation after deliberate native-inventory corruption even
when outer artifact hashes are refreshed. Corruption fixtures are software
boundary tests, not native-tool measurements or model-performance evidence.

## PCB DC conduction

The optional [DC path check](dc-path.md) qualifies ideal single-layer straight
and circular-arc paths and bounded endpoint-connected series/parallel networks.
Materials, temperature, terminal contacts, current and dimensional intervals
must be captured explicitly. Unsupported copper or incomplete branches refuse
coverage. Native owned fixtures test rho*L/A, independent network solutions,
actual narrowed/lengthened trace violations and sealed evidence tampering.
These results do not qualify pad spreading, vias, zones, RLC extraction,
self-heating or arbitrary-board/hardware behavior.

## Bench transaction recovery coverage

`test_bench_transactions.py` exercises the real coordinator and sandboxed
synthetic measurement producer/parser with scripted independent reviewers.
It covers duplicate/conflicting requests, captured-parent epoch closure,
source supersession, explicit replacement attempts, full external-target leases,
coherent reader refusal, completed-result adoption and exactly-once campaign
history projection. A process exits immediately before and after every fsync
and rename in the prelaunch transaction; recovery runs in another process and
must reconcile deterministically or refuse with an explicit ambiguity on an
unsuperseded source, after which a separately identified operation proceeds.
Three processes released together contend for supersession, review start and
replacement attempts; each commits exactly once. Recovery after the bench
report and raw files are deleted proves no reacquisition. Recovery has a
provider boundary assertion. These tests model process interruption,
not physical power loss, instrument authenticity or live-provider reliability.

## Packet-navigation measurement attempt

A private pilot on 2026-10-17 compared source snapshots `1ce6a52` and
`d0f7755` with identical captured control/seed inputs, requirements, native check
results and Codex subscription profile. Four native preparation runs verified
successfully. Only native subprocess elapsed time was excluded from exact
engineering-result comparison; raw durations were retained. The first live
control run stopped with both initial reviewers timed out at their 300-second
limits, empty console logs, and no session or token-usage records. Origin-snapshot
verification succeeded. No adjudicator, treatment sample, replacement sample or
fallback followed. The attempt therefore establishes neither token savings,
latency improvement nor recognition performance. An offline reproduction subsequently showed that the executor abandoned
pending prompt-pipe writes after its first polling timeout. Private regular-file
input fixes that transport defect; six native input tests and the existing
cancellation/preflight suite pass. No live comparison was repeated, so the
measurement acceptance remains incomplete.

The private manifest SHA-256 is
`b2a73834bd2f21d62694d3ff6929bc6dc3bf6c70052163b75596d514a7508072`;
the stopped report SHA-256 is
`6b521b3b526161e92577e92296d7197046d525ed7d3b4365540cc5852009ac86`.
These identifiers support retained-record comparison; the records and captured
third-party fixture inputs have not been republished as benchmark evidence.

## Writer-side stage receipts

`test_stage_receipts.py` runs an original test step program in the real sandbox.
It covers the following:

- coordinator-observed executable, source-tree and version identity, and an unknown version when no probe exists or the probe prints nothing;
- exact input and output hashes and adoption;
- partial, failed, crashed, timed-out and cancelled steps;
- out-of-scope writes and inventory changes;
- verbatim backend reports, and malformed reports that keep the process receipt: `NaN`, `Infinity`,
  float overflow, duplicate keys, overdeep nesting, invalid UTF-8, truncation, oversized integers and
  size, including on failure, timeout and cancellation;
- links planted by the tool, against an owned host-only sentinel. The cases are ancestor and leaf links in write
  sources, artifacts and backend reports, links to candidate and retained files, broken links,
  links and a FIFO inside a declared artifact directory beside a valid nested output, and links in the design copy.
  The sentinel is never hashed, adopted or modified;
- links planted at every executor record name against the host-only sentinel, an unwritable `/work`,
  and forged records after an interrupt. These give a retained `refused` receipt, never an adoptable one,
  and the sentinel is unchanged;
- adoption that refuses a replaced output or ancestor even when its bytes match, and a receipt
  object that differs from the retained receipt;
- forged, tampered and foreign receipts;
- exact whole-tree applicability.

Campaign tests bind a receipt to the captured proposal. They show that a late change after binding fails
`verify-campaign`, that a failed step stops the repair without substitution, and
that undeclared or unanchored receipts are refused. A step whose backend report is `NaN` or truncated
leaves a verifiable campaign.

`test_rung1_campaign.py` and `test_rung1_qualified.py` run the rung 1 `write-scaffold` step
(builder `scaffold.native_files`) in the real sandbox. The scaffold files are the step's
adopted outputs and byte-equal to the shared builder's, a failing step keeps its `failed` receipt and
writes no project, and the record's scaffold binding is exact against the first captured
candidate. For rung1_qualified the imported pre-receipt passes `verify_pre_receipts` against a
real capture of its staged scope and refuses a changed board or a planted generation file. The rung 1 steps (`emit-schematic`, `build-board`, `route-krt`, `export-fab`) are
declared and validated there. They run in the driver's dry-run rehearsal and in a real
campaign, not in the unit tests. No scaffold, placement helper or kicad-route execution is
qualified.

## Synthetic seeded benchmark

The [benchmark utilities](seeded-benchmark.md) freeze five public source commits
and 30 synthetic assembly/connectivity/geometry predictions before scored runs.
Exact wire edits reject redundant disconnections and unintended conductor changes;
protection deletion preserves remaining schematic and saved-copper groups. These
structural gates are preparation, not detector performance. Nine value cases are
assembly contracts and fourteen wiring cases intentionally change only schematic
conductors. Native-clean controls and functional electrical failures are not
claimed. Complete external-library/model and redistribution closure is unqualified.
Reports keep misses, unsupported execution, unrelated findings and unresolved
control findings separate. No adjudicated false-alarm rate or model-review
performance is established by this preregistration. Third-party source files and
generated candidates/results remain scratch-only pending publication approval.

## Captured routing constraints

The [routing check](routing-rules.md) compiles explicit immutable bounds into
isolated native KiCad DRC invocations. Qualified straight-track/two-layer-via
routes need complete topology and finite-width copper-contact coverage; arcs,
zones, copper graphics and unsupported topology remain unknown. Every routing
check binds one conjunctive requirement. Partial coverage cannot witness a
defect, even if a supported constraint failed. Impedance has no qualified solver.
Public pipeline tests use native tools with scripted reviewer transport; they do
not establish physical qualification or live-model detection performance.

## Captured fabricator profiles

The [fab profile check](fab-profile.md) binds each limit to a captured source
quote and one conjunctive fabrication requirement. Quote occurrence is not proof
that the typed value is correct, authentic or applicable. Board size and edge
clearance need one simple straight-segment outline; size uses fixed saved axes.
A missing, flat or crossing outline, a curved outline or a cutout is unknown.
SMT support means only that saved SMD pads are present. Only owned synthetic
profiles are tested; no real fabricator standard is qualified.

## Offline catalog and part-selection preparation

[Catalog capture](catalog.md) checks snapshot integrity and rederives normalized
quantities from retained raw attributes before [selection](part-selection.md).
Repinned contradictory quantities fail closed. This proves internal consistency,
not upstream authenticity. Selection compares declared intervals and allocates
snapshot stock; it is not circuit acceptance or purchasing authorization.
The real upstream catalog and exact Konnect fixture remain unqualified.
The informational `catalog` check and bounded `part_selection` check now use
the existing sandbox, immutable baseline and coordinator seal. Selection binds
one requirement sourced from its captured requirements file. Unknown selection
cannot establish a defect witness; checks-only success is not campaign acceptance.

## Intent and golden netlist parity

The [netlist parity check](netlist-parity.md) compares full native XML physical-pin
incidence and exact component multiplicity with a captured source. It preserves
identical instances, bounds exact graph search, and leaves ambiguous instance
correspondence unknown unless explicit immutable anchors resolve it. Descriptive
scores do not grant conformance. Missing or malformed producer/source evidence
cannot pass; footprint adequacy and electrical behavior remain outside coverage.
