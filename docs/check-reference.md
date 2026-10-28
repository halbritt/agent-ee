# Native and engineering checks

The placement job also preserves independent drill reservations and declared
connector overhang (AGENTEE-515q2). Supported clearance inputs are the board
minimum, Default netclass and pad overrides; custom rule files and non-default
netclasses refuse capture. Capture reads rules in a fresh native subprocess,
including defaults for omitted fields, and refuses all board-level zones,
keepouts and rule areas before solving. Native sampled agreement does not establish
whole-board legality. See the [supported rules and controls](placement-job.md).

The experimental [whole-board placement job](placement-job.md) independently
protects native locks and captured fixed intent before materialization. Final
checks compare complete protected saved board content, including pad geometry,
footprint graphics, rules and outline; a mismatch is ineligible even with clean
DRC. Engine, routing and final DRC share one cancellation/deadline boundary.
This adds no registered check kind or production default.
Its pad-clearance search uses labelled conservative native outer polygons and
the native pad overrides, with exact polygon separation in Rust. Native DRC
remains authoritative; body/mask reservations and legacy copper boxes retain
their existing contracts. See the [clearance tests](placement-job.md#tests)
for the bounded USB1/fiducial agreement and soundness evidence.

The [strict-suite runner](maintainer-guide.md) accepts `--previous-log` to submit
modules longest-first from retained timings (AGENTEE-468), with alphabetical
fallback. This affects scheduling only; check verdicts and skip refusal are unchanged.

[Candidate model coverage](pcb-renders.md#proposal-project-model-closure-agentee-394)
records environment/default resolution and hashed proposal-local model files.
Missing model files remain explicit inspection gaps; coverage adds no check
kind and does not change engineering verdicts.
Inventory fixtures can inject explicit stock defaults independently of installed
host models; ordinary candidate coverage keeps native default discovery.

The [purpose review](purpose-review.md) asks independent reviewers to justify
constraints, reserves, empty fields and interface poses using captured geometry
and renders. It adds an advisory packet dimension, not a check kind or acceptance
limit; estimated savings remain concerns beside correctness findings. Purpose
findings carry an advisory status that cannot hold the run outcome, and visual
coverage is recorded per board side. `verify` re-derives that status and the
per-role purpose coverage from the retained reviews and adjudication records.

The [pinned fome-mc wideband fixture](fome-wbo1.md) exercises native ERC,
netlist export, `hardware_interface` and fresh `block_provenance`. Its coverage
ledger keeps upstream parity, active behavior, parts and heater geometry unknown;
its captured blocks remain unverified.

The [pod CAN reference-set audit](pod-can.md) connects U307/C308 from `wbo1`
to D4/R9 from `psu` and tests VCC/VIO separation with fresh native exports.
The historical audit retains whole sheets. A separate selected-reference producer
emits four parts and eight ports; fresh `block_provenance` exports both sources
and checks every selected physical identity, pin net and boundary binding.
Unplanned multi-source adaptation still refuses; source qualification stays open.

For version-2 requirements, measurement/coupling interfaces, campaigns and bench
feedback, see the [closed-loop contract and command reference](closed-loop.md).

The [pod evidence index](evidence-index.md) maps captured criteria to existing
check results, receipt history and outstanding obligations without running checks.
The map is flat with exactly keys 1-9 (Rev-2) or 1-11 (Rev-3: 10 concurrent
acquisition/wheel/fault, 11 logging/power-loss/full-media/tire interface, both
bench obligations that stay not met). Any other key set or a schema-wrapped map
refuses with `CriteriaMapError` (CLI exit 2). No check registration or verdict changes.

The [seeded benchmark closure audit](seeded-benchmark.md#dependency-closure)
requires exact tool and font inventories and verified licence files. Its CLI
reports `closed: false` and exits 1 when any licence evidence is unverified,
including an absent outer-host Docker licence inside an identified image.
This inventory audit adds no candidate check kind.

The [generation benchmark](generation-benchmark.md) reuses these handlers on
captured generated outputs, preserves failed execution separately from defects,
and leaves unknown score coverage explicit. Private prompt paths are refused
before execution; unsafe generated output records failed generation and unknown
checks. Only `prompt.md` can be selected, and byte-identical copies of evaluator
inputs or the reference are refused. Opt-in `--expect-pins` anchors task pins to
an externally retained digest at execution. Summaries replay scoring from retained
task, candidate and sealed check evidence, and count recorded failed generation
as failed even when its check outputs pass. A series freeze requires external
task pins and retained executor records bound into the candidate. Aggregate with
`--manifest` and `--expect-pins` verifies the externally retained series digest,
report and evidence seals before scoring; this refuses coordinated resealing,
generation-claim rewrites and task re-stratification. Unanchored aggregation
remains diagnostic. Board references support board-only
task pinning. It registers no new check kind.

The [schematic emitter](schematic-emitter.md#output) retains KiCad 10 private
symbol metadata and accepts an explicit boolean component `in_bom` policy.
These are producer features, with no check registration change.
The emitter also accepts canonical structured `PARAM_Datasheet` bindings up to
2048 bytes, using the existing binding parser; legacy `PARAM_Datasheet` text
retains its zero-to-256-character validation. Object-like values (leading `{`
after whitespace) must pass structured validation, with no free-text fallback.
A plain `Datasheet` exceeding 256 characters requires an exact URL match to a
validated canonical binding on the same component and is bounded to 2048 safe
ASCII bytes; its complete companion JSON must also fit 2048 bytes. Other
free-text fields retain their limits. JSON-looking legacy text behind Unicode
format prefixes or with array roots receives `suspicious_datasheet_text`, an
informational emission diagnostic with no structured-binding acceptance.
Native forced-save and export regressions cover the paired long URL and byte
preservation (AGENTEE-293, AGENTEE-296).
Native netlist inventory observes BOM exclusion independently of board membership.
Declared [copper features](fab-export.md#copper-features) have a qualified fab exclusion
policy (AGENTEE-289, AGENTEE-290); other non-DNP native exclusions remain refused.
Emission success alone does not establish BOM/CPL agreement.

The [file digest contract](result-reference.md) requires fresh byte reads for
verification. Its stat-unchanged mmap regressions control metadata observations
while retaining real mappings and hash reads (AGENTEE-294); they add no check kind.

The [block library](block-library.md#materialization) refuses board-region
materialization with `copper_unknown` and no candidate: placement checks cannot
establish that recorded copper was applied. Region imports containing zones also
refuse as unknown copper coverage (AGENTEE-403). These are producer refusals;
no check kind, schema or existing check verdict is changed.

All handlers execute against the captured candidate, not the source checkout.
Their working directories are separate. The original tool commands, sandbox
arguments, exit status and console output remain in `work/CHECK_ID/`.
[checks.py](../src/agent_ee/checks.py) interprets native output and applies the
criteria; [geometry_probe.py](../src/agent_ee/geometry_probe.py) extracts saved
board facts through pcbnew.

[Coordinator stage receipts](stage-receipts.md) can record artifact-only steps
with `writes: {}` and nonempty `artifacts`. They retain artifact hashes without
design outputs; they remain provenance and cannot satisfy an acceptance check.
An artifact-only step with a null artifact entry is refused with
`missing_artifact:<path>` and cannot be adopted.
Campaign [`pre_receipts`](stage-receipts.md#receipts-before-the-first-capture) bind
coordinator receipts made before the campaign to its first capture. Start copies and
revalidates them as one ordered custody chain; `verify-campaign` refuses changed, missing,
reordered or unbound imports.
They do not replace fresh checks or the declared-stage done binding.

The [placement campaign cancellation regression](placement-adapter.md) holds a
real sandboxed helper at its readiness marker until interruption (AGENTEE-345).
It temporarily installs Python's default SIGINT handler so background runners
that inherit ignored SIGINT still deliver the interrupt, then restores the prior
handler on cleanup. A 120 s helper deadline bounds a missing readiness marker.
It requires cancelled receipts, process cleanup and no next candidate. This test
synchronization change adds no check kind and changes no candidate verdict.

Test refusal diagnostics (AGENTEE-326) use an absolute
`AGENT_EE_REFUSED_RECEIPTS` override, the writable repository build directory,
or private per-user temporary storage. Retention errors preserve the original
refusal or fixture exception. See [receipt diagnostics](stage-receipts.md) and
`tests.test_receipt_diagnostics`; no check verdict or receipt schema changes.

The [pod driver](pod-driver.md) preflights its required placement helper, captured
packing-policy schema and values, and antenna-facts declaration before creating
output. These configuration refusals add no check kind or acceptance evidence.
The [mutable-intent schematic stage](intent-schematic-stage.md) adds no check kind:
its emission and annotation receipts bind authored inputs to native outputs.
Pin-resolution failures retain named diagnostic findings and refuse adoption;
checks still grade the freshly captured schematic and board.

[Stage-one intent import](design-intent.md) prepares source-bound requirements
and incomplete interface stubs for later capture. Its lint outcome covers only
the declared lexical checks; executable checks must supply acceptance evidence.
The [requirement-record compiler](requirement-scope.md) maps quoted spec
requirements to the check specifications on this page; it adds no check kind.
The [spec-record requirement map](requirement-map.md) accepts registered kinds
and uses their existing parameter and executable-coverage validators.
Limitations and bench obligations appear in the scope and run limitations,
never as passing checks; pod acceptance items 8 and 9 are bench-only.
[Spec capture](spec-record.md) records a markdown spec's table rows, bullets and
numbered items as requirement IDs with byte-exact locators; it adds no check
kind either. Tables that omit outer pipes are refused with a byte offset rather
than captured as prose.
Offline [decision capture](design-dialog.md) also prepares requirements without
adding a check kind. Answers that exceed the JSON decoder's recursion limit
produce `outcome: unknown` and an `answers_too_deep` finding, retain the raw
answers and their hash, and produce no derived requirements (CLI exit 1).

The routing-stage [plane producer](add-planes.md) adds no check kind. Its native
DRC gate refuses errors citing planned zones and `isolated_copper` violations
citing planned `always` zones at any severity, with exact zone UUIDs retained in
the refusal. Planned `never` zones retain isolated-copper warnings for later
campaign checks.

## Execution and engineering result

Every result separates `execution` from `outcome`. A tool can run successfully
and report a design violation: `execution=ok`, `outcome=defect`. A crash, missing
output, unsupported component or absent measurement means unknown coverage,
not a confirmed electrical defect and not a passing check.

Checks are executed once for each captured candidate; failed or malformed check
output is never schema-resubmitted. Campaigns and standalone reviews default to
one model-output resubmission per role under the
[bounded output rule](campaign-guide.md#bounded-output-resubmission);
`run_review(..., max_schema_resubmissions=0)` keeps a single model call.
Reviewer execution records carry their role before atomic publication;
`tests.test_reviewer_publication` injects cancellation before and after the
temporary-file rename, with and without an existing record. See
[reviewer cancellation](reviewer-profiles.md#subscription-adapters).
Adjudicator citations may reference the verified immutable `baseline.json` for
context, but accepted defects still require their sealed failed-check citation.
New reviewer and adjudicator `/2` citations require a resolvable JSON `pointer`,
text `lines` range or XML `xpath`; descriptive locators in retained `/1` records
keep their compatibility rule. See [response and citation rules](result-reference.md#structured-citations).
The packet lists evidence formats and locator kinds, including files omitted
from excerpts. Native KiCad text requires `lines`; unused JSON/XML fields must
be omitted. A refused citation names the file and supplied/required kinds in the
configured bounded resubmission request; unresolved locations remain refused.

Opt-in [staged scopes](staged-campaign.md) first check declared native content.
A successful native probe that confirms missing expected symbols, footprints or
tracks produces `ok/defect` with reason `stage_not_reached`. Earlier defects
block later handlers with `ok/defect`, reason `stage_blocked`; an earlier unknown
leaves later checks `not_run/unknown`. Once the stage opens, missing simulation
nets remain unknown coverage. Scopes without stage declarations keep their
ordinary handler results and progress rules.

Campaign regression protection includes prior `execution=ok` checks with either
`outcome=pass` or `outcome=info`. A later defect in either yields `regression`;
unknown coverage still stops as `incomplete`. Staged campaign configuration
loading refuses `finding_retry_limit` below 2 before checks or agents run; the
first finding appearance counts toward that limit. See the
[staged contract](staged-campaign.md#progress-and-repair).

Schematic presence excludes a symbol only when its embedded library definition
has a power flag and its instance declares both `in_bom no` and `on_board no`.
A `#` Reference that fails those conditions refuses the probe, even though
KiCad's XML exporter omits it. This disagreement is failed/unknown coverage,
never a passing probe or a `stage_not_reached` absence witness.

For [staged campaign implementations](stage-receipts.md#campaign-integration), declared coordinator
steps own their scheduled outputs. A writer byte change to one of those candidate paths stops the
implementation as `incomplete`, with `writer_step_output_edit` in `state.json.error`, before step execution or capture.
This is a writer-authority refusal; it does not produce a check verdict or a passing receipt.
The retained `implementations/N/implementation-prompt.txt` lists all declared stages, step names and
write mappings, with the scheduled outputs reserved for coordinator steps.

The native commands below show sandbox paths; `/candidate` is the captured
`inputs/`, `/work` is this check's scratch. They document the emitted arguments,
not commands to paste into a host shell with nonexistent sandbox paths.

The [datasheet facts check](datasheet-facts.md) requires both the declared value
in its quote and the quote on its cited PDF page to have complete token
boundaries. A quote that cuts a page token is rejected with
`quote_truncates_a_page_token`; any unverified citation leaves the check
`unsupported/unknown` and cannot support dependent checks.

The separate [datasheet footprint authoring tool](datasheet-footprint.md#fact-coverage)
also restricts coordinate citations to quoted pitch/span declarations. Pitch
scales must match centered geometric site indices; spans permit signed halves.
Other scales produce `unsupported_coordinate_scale` coverage gaps and prevent
library publication even if the quoted fact is verified. This tool adds no
registered check kind.

| Kind | Native/helper arguments | Individual ceiling |
| --- | --- | --- |
| `erc` | `kicad-cli version` (sealed under `kicad-version/`), then `kicad-cli sch erc --format json --severity-all --units mm -o /work/erc.json SCHEMATIC` | 60 s, 900 s |
| `drc` | `kicad-cli pcb drc --format json --severity-all --schematic-parity --units mm -o /work/drc.json BOARD` | 900 s |
| `netlist` | `kicad-cli sch export netlist --format kicadxml -o /work/design.xml SCHEMATIC` | 600 s |
| `catalog` | `/usr/bin/python3 -m agent_ee.catalog_checks catalog /candidate/SNAPSHOT MANIFEST_SHA256 --out /work/catalog.json`; [snapshot integrity](catalog.md) only | 600 s |
| `parts_subset` | `/usr/bin/python3 -m agent_ee.parts_subset /candidate MANIFEST /evidence/netlist/design.xml --schematic SHEETS --out /work/parts-subset.json`; [captured membership](approved-parts.md) | 120 s |
| `part_selection` | Same runner with `part_selection` and `--requirements /candidate/REQUIREMENTS`; [declared catalog constraints](part-selection.md) | 600 s |
| `netlist_parity` | Coordinator compares full native XML with captured intent/libraries or a golden netlist; qualified virtual power markers omitted by XML are listed in the report ([details](netlist-parity.md)) | Enclosing run deadline |

| `netlist_extra` | Coordinator checks the earlier native netlist and captured topology policy; no additional tool launched ([details](netlist-extra.md)) | Enclosing run deadline |
| `part_decisions` | Coordinator compares source-bound choices and captured deviations against the earlier native netlist ([details](part-decisions.md)) | Enclosing run deadline |
| `topology_rules` | Coordinator evaluates a captured requirement-tagged topology rule file against the earlier native netlist; no additional tool launched ([details](topology-rules.md)) | Enclosing run deadline |
| `net_names` | Coordinator checks the earlier native netlist against a captured owner name policy and the candidate's name-level annotation; no additional tool launched ([details](net-names.md)) | Enclosing run deadline |
| `regeneration_determinism` | Coordinator compares two captured boards, schematics, netlists or JSON probe outputs after removing IDs; no additional tool launched ([details](design-diff.md)) | Enclosing run deadline |
| `design_rules` | Coordinator grades the enforced cards of a captured rule pack against the earlier native netlist, owner role bindings and verified datasheet facts; no additional tool launched ([details](design-rules.md)) | Enclosing run deadline |
| `interval_assertions` | `/usr/bin/python3 -m agent_ee.interval_assertions /candidate DOCUMENT --snapshot SNAPSHOT --manifest-sha256 MANIFEST_SHA256 [--parts-manifest PARTS] --out /work/intervals.json`; exact rational worst-case evaluation of declared relations over pinned catalog and requirement intervals ([details](interval-assertions.md)) | 300 s |
| `hardware_interface` | Coordinator compares a captured owner contract with preceding full native XML; no additional tool launched | Enclosing run deadline |
| `footprint_assignment` | `/usr/bin/python3 -m agent_ee.footprint_library` for declared selections, or `agent_ee.native_binding` with an earlier native netlist | 60 s |
| `routing_equivalence` | `/usr/bin/python3 -m agent_ee.routing_equivalence`; native saved copper comparison | 300 s |
| `routing_rules` | `/usr/bin/python3 -m agent_ee.routing_native /work/rule-request.json`; staged KiCad DRC per constraint | 900 s outer call |
| `fab_profile` | Same runner; staged KiCad DRC per sourced limit plus a saved-board pcbnew inventory | 900 s outer call |
| `fab_roundtrip` | Coordinator re-parses the sealed export’s Gerber/Excellon bytes against earlier sealed geometry; no tool launched ([details](fab-roundtrip.md)) | Enclosing run deadline |
| `fab_consistency` | Coordinator reads the earlier export's sealed staging tree and version run, the captured board, the sealed netlist and earlier sealed geometry; no tool launched, no export regenerated ([details](fab-consistency.md)) | Enclosing run deadline |
| `package_consistency` | `/usr/bin/python3 -B -m agent_ee.package_consistency /candidate/CATALOG MANIFEST_SHA256 /work/request.json /work/catalog-rows.json`; coordinator then compares preceding native XML names through the captured table ([details](package-consistency.md)) | 600 s |
| `geometry` | `/usr/bin/python3 -B -m agent_ee.geometry_probe BOARD -o /work/geometry.json` | 600 s |
| `dfm_geometry` | Same probe with `--dfm`, then a coordinator evaluation against the captured profile; see [DFM geometry](dfm-geometry.md) | 900 s |
| `rf_geometry` | `/usr/bin/python3 -B -m agent_ee.rf_geometry BOARD --request /work/rf-request.json --out /work/rf-native.json`, then a coordinator evaluation against the captured declaration; see [RF geometry](rf-geometry.md) | 600 s |
| `antenna_placement` | `/usr/bin/python3 -B -m agent_ee.antenna_placement BOARD --request /work/antenna-request.json --out /work/antenna-native.json` when the footprint is captured, then a coordinator evaluation against the captured declaration and pinned sources; see [ESP32 antenna placement](antenna-placement.md) | 300 s |
| `placement_congestion` | `/usr/bin/python3 -B -m agent_ee.placement_probe BOARD --out /work/placement-probe.json`, then an offline coordinator analysis of the captured probe ([details](placement-congestion.md)) | 600 s |
| `measurements` | Declared pinned producer argv, then `/usr/bin/python3 /candidate/ADAPTER /work/request.json /work/measurements.json` | Default 600 s per producer/parser call |
| `coupled_margins` | Coordinator joins earlier scalar results; no external solver launched | Enclosing run deadline |
| `rc_transient` | `ngspice -n -b -o /work/ngspice.log /work/control.sp` | 600 s |
| `active_spice` | `/usr/bin/ngspice --version`, then `/usr/bin/ngspice -n -b -o /work/ngspice.log /work/control.sp` once per declared or [template-generated](spice-templates.md) analysis ([details](active-spice.md)) | 600 s per call, bounded by run deadline |
| `block_provenance` | Earlier board XML plus sandboxed source and instrumented boundary netlist exports; [block adaptation](block-adaptation.md) | 120 s per export, bounded by run deadline |
| `schematic_lint` | Coordinator reads the captured sheets and the earlier netlist check's sealed `design.xml`; no tool launched ([details](schematic-lint.md)) | Enclosing run deadline |
| `fab_export` | `kicad-cli version`, then `pcb export stats`, `gerbers`, `drill`, `pos` on BOARD, and `sch export bom --variant NAME` on the schematic only with a declared `bom_variant`; see [fab export](fab-export.md) | 600 s per step |
| `jlc_dfm` | Networkless Python parser over owner-captured source report and normalization; binds the declared zip SHA-256 to an earlier sealed package, then grades errors ([details](jlc-dfm.md)) | 60 s |
| `fab_package` | The `fab_export` steps into its own `work/ID/fab/`, then the `fab_consistency` gate on that tree and an artifact-hash recomputation, plus an optional `assembly_profile` orientation assessment; followed only by bound `jlc_dfm` and `fab_roundtrip` checks ([details](fab-package.md)) | 600 s per export step |
| `assembly_readiness` | `kicad-cli sch export netlist --format kicadxml -o /work/design.xml SCHEMATIC`, then a coordinator join with the captured catalog; see [assembly readiness](assembly-readiness.md) | 600 s |
| `cpl_rotation` | Coordinator reads the earlier export's verified CPL/BOM, earlier sealed geometry pad boxes, the captured board, policy and supplier footprints; no tool launched ([details](cpl-rotation.md)) | Enclosing run deadline |
| `jlc_cost_estimate` | Coordinator reads the earlier export's verified BOM/CPL/`pcb_summary.json`, the captured board pads, catalog snapshot and pricing table; informational only, no tool launched ([details](jlc-cost-estimate.md)) | Enclosing run deadline |

The remaining run budget can shorten every ceiling. A deadline-shortened native
timeout is recorded as interrupted coverage. There is no native network access.

Catalog reports bind the snapshot manifest pin, DB hash and as-of date.
Selection retains that verified identity when requirements loading fails;
unverifiable snapshots return failed/unknown with null date and DB hash.
Neither catalog check acquires data or establishes current supplier inventory.
## Captured catalog and part selection

The [catalog check](catalog.md) takes exactly `snapshot` and `manifest_sha256`.
It verifies the captured manifest and database and rederives normalized intervals
from retained raw attributes. Its scope/3 coverage is informational.

The [part-selection check](part-selection.md) also requires `requirements` and
`requirement_id`, mapped to exactly one baseline requirement sourced from that
requirements file. Capture both catalog files and the requirements file as
immutable inputs. Eligible rows must meet every declared constraint and
`max(build_quantity * quantity_per_board, stock_floor)` available stock. Ranking
prefers Basic, then Preferred, then Extended, followed by known price, lower
price, higher available stock and numeric LCSC ID. The report records matched
intervals, rejection reasons, exclusion counts and the deciding rank key.

The `switching_regulator` category supports SI input-voltage maximum, output
current, switching frequency and temperature constraints plus exact
`topology: {"kind":"equals","value":"sync"}` (or `async`) and the captured
package string. Only declared constraints affect eligibility; missing typed
claims exclude rows with per-attribute `unknown_attribute` reasons. The
[switching-regulator example](part-selection.md) and native DC-DC mapping use
the same snapshot reader, immutable baseline and sealed receipt path.

An empty selection returns `execution: ok`, `outcome: unknown`, `chosen: null`
and reason `no candidate`. Invalid requirements or snapshots return
`execution: failed`, `outcome: unknown`. An unparseable attribute excludes its
row as unknown. These results establish consistency with captured catalog
claims; upstream authenticity and datasheet correctness require separate evidence.

The [pinned native feed](catalog.md#pinned-jlcparts-acquisition) is acquired on the
host before capture; its explicit stocked-parts profile excludes zero-stock and
unsupported-category rows. A missing row cannot establish upstream unavailability.
The [full-feed runtime qualification](catalog.md#full-feed-runtime-qualification)
records the private full-feed measurements and reproduction command. Both helpers
now declare a 600-second ceiling because complete rederivation exceeded 120 seconds;
the enclosing run deadline can shorten it. A timeout or absent report remains unknown.

## Interval assertions

The [interval assertion check](interval-assertions.md) takes `assertions` and,
when any parameter cites the catalog, `snapshot` and `manifest_sha256`, plus an
optional `parts_manifest` pinning the same snapshot. Every requirement ID the
document cites must be a baseline requirement sourced from that document and
bound to the check. Each assertion is one predicate. An unknown operand, an
unbounded division or an unattained enclosure excess is unknown, never pass; a
defect needs an attained parameter corner outside the target. A malformed
expression or unit mismatch refuses the audit and is `execution: malformed` in
a run. `intervals.json` records the input hashes, every parameter interval, and
each assertion's computed interval, witness and operand origins.

## Approved parts membership

The [approved-parts manifest](approved-parts.md) uses `agent-ee.parts/1` and
pins generic catalog selection or [named-part records](approved-parts.md#named-ics-and-modules),
plus CAD and datasheet provenance. Named replay compares exact captured identity;
it does not rank parts or infer IC suitability. The `parts_subset` check
takes exactly `manifest`, `netlist_check` and `requirement_id`. Its requirement
must point to the manifest's captured declaration source. The full dependency
closure is immutable in scope/2 and scope/3.

Every populated purchased instance must match its declared symbol, footprint and
supplier identity, and every required sheet/reference selector must be present.
A mismatch or absent required selector is a defect; footprint findings retain
the reference and both footprint values. Missing or failed native evidence,
missing identity data and required unavailable provenance leave coverage unknown.
The per-reference table is retained in `work/CHECK_ID/parts-subset.json` and the
coordinator seals the check and its prerequisite XML. The predicate establishes
declared membership, not approval authenticity or component suitability.

Explicit [binding-fill source migration](binding-fill.md#explicit-source-migration-agentee-309) regenerates qualified legacy probe markers with a binding-fill/2 migration artifact before schematic emission. Admission checks retain their strict canonical requirement. Binding fill also projects declared copper pins, native nets and non-BOM flags without purchased-part acquisition (AGENTEE-309).

`parts_subset` also admits explicit [on-board copper declarations](approved-parts.md#declared-on-board-copper-features) with captured CAD, exact passive pins/nets and native non-BOM, non-DNP, on-board flags (AGENTEE-305). It shares the fab copper geometry and field rules; undeclared populated instances remain defects.

## Hierarchy validation

ERC and netlist walk root and child sheet file references before native execution.
Child paths resolve relative to the containing sheet; repeated instances count
separately even when they reuse the same file. Missing sheets, hierarchy cycles
and references escaping the candidate prevent a clean check. The source locator
is the captured root and referenced native child files. Filename case matters
on the demonstrated Linux filesystem.

The hierarchy extraction recognizes KiCad sheet-file property text; it is not a
general KiCad parser. Unusual future syntax needs an adapter/test update. ERC
also compares native reported sheet count with the expected instance count.

The [schematic emitter](schematic-emitter.md) also flattens captured `extends`
symbols through the shared connectivity resolver. Native ERC compares the embedded
inherited definition with its captured project library; native netlist tests cover
all 144 pins of a derived STM32 variant. Missing captured parents refuse emission
before these checks run; installed libraries cannot supply missing capture bytes.
AGENTEE-172 makes generator and rung-1 symbol captures subsets by default.
Emission shares a content-addressed parse only within its call; every declared
capture hash is still verified. Subset/source hashes remain in the capture and
step receipts. These are provenance and performance changes, with no new check
kind or acceptance credit. See [emitter cost and benchmark](schematic-emitter.md#library-subsets-and-emission-cost).

The opt-in [functional presentation](schematic-emitter.md#functional-presentation)
(AGENTEE-222) groups flat schematics and draws short local wires; AGENTEE-241
fans crowded edge-pin labels and packs frames tighter, with the same native
netlist and ERC on the real pod. Its internal
connectivity guard is diagnostic; native ERC, exact netlist parity and rendered
review remain required on the captured output. It adds no check kind or
acceptance credit, and preserves symbol and pin UUIDs across presentation modes.
AGENTEE-301 also separates detached Reference/Value fields from their labels and
extends vertical test-point labels beyond pin-name text. The emitter
[`--native-text-work` option](schematic-emitter.md#detached-text-and-native-measurement-agentee-301)
measures SVG before publication and refuses
unresolved overlaps. The separate measurement command reports label/text
overlaps across a whole native SVG; it is a diagnostic with
explicit unsupported/missing-text errors, not a registered check or electrical
verdict.

## ERC

Optional `params.waivers` names a captured, owner-approved
[exact violation disposition list](violation-waivers.md). The JSON report must contain a
`sheets` list covering the expected number of instances. Violations are counted
across sheets, grouped by severity/type, and retained in `erc.json`; ignored
check keys are reported. Any unwaived violation makes the check `defect`.
A defect also carries [`details.gate_findings`](staged-campaign.md#progress-and-repair):
one identity per distinct violation, with `entity` the sheet path plus the sorted
native item descriptions and `kind` the native violation type. No `suggested_fix`
is attached; if any violation lacks item descriptions or a type, the check
omits the list rather than report a partial one.

Violation locations are in `details.locations`. Their scale is chosen from the
sealed `kicad-cli version` run, never from the report alone. KiCad 10.0.0 to
10.0.6 write ERC JSON positions at 1/100 scale, so `regime: scale_x100`
multiplies them by 100. 10.0.7 and later 10.0.N are `native`. Any other version,
or a report whose `kicad_version` differs from the sealed version, is `withheld`:
items keep no positions and a limitation records the reason. `details.kicad_cli`
and `details.erc_coordinates` record the pinned binary and the regime applied.
Counts and outcome are unaffected. See
[KiCad report-format quirk guards](kicad-quirks.md).

An undriven power-input message describes the native symbol/connectivity rule.
It does not alone establish that a physical rail has no supply. A reviewer must
inspect pin types, actual connectivity, module boundaries and requirements
before making that stronger electrical claim.

## DRC and schematic parity

Required JSON lists are `violations`, `unconnected_items`, and
`schematic_parity`. A missing list is malformed coverage. The console is also
checked for known native messages that indicate skipped schematic parity even
when the CLI exits 0. Any item in the three lists makes the criterion fail.

The only parameter is the optional `projection: "placement-stage/1"`. It runs
the same single native DRC and retains the same raw `drc.json`, but grades only
`violations` and `schematic_parity`. Unconnected items are counted in
`details.ungraded`; a placed but unrouted board can pass this projection. Its
capability predicate is `native:drc:placement-stage/1`, distinct from the full
`native:drc:violations`. A scope/3 capability audit refuses a projection unless a
later full `drc` check exists, so connectivity is still graded before
acceptance. Any other projection value is unsupported. The
[placement checks](placement.md#placement-check-kind) consume either DRC result.

Under open [carried placement defects](#carried-silk-in-the-placement-projection-agentee-346)
the projection grades instead of refusing. A native `silk_overlap` or
`silk_over_copper` violation whose type and complete item-UUID set equal an
open carried defect is graded as carried rather than as a violation.

The [RC stage fixtures](rc-stage-fixtures.md) exercise this boundary, both
the control-derived variants and the original alternative: placed-unrouted
retains native unconnected items and passes the placement projection;
seeded-placement has a native R1/C1 courtyard overlap. Both remain defects
under full DRC.

Zone fill is evaluated as saved. The implementation does not refill, save or
repair the board. Reported exclusions stay visible and are not dropped by this
all-severity audit. The [donor baseline](../evidence/BASELINE.md) distinguishes
these rule failures from independently established manufacturing failures.
Native DRC coverage also depends on the project's configured checks and ignored
rules; passing these checks is not full circuit function or fab qualification.

Known limit (AGENTEE-400): native DRC prints four decimal places, and constraint
provenance matches a printed limit and grades a measured deficit with a
0.00005 mm (50 nm) tolerance. A sourced-limit violation smaller than 50 nm
therefore grades as not violated, and a printed limit within 50 nm of a sourced
value matches it. The report alone cannot resolve a smaller deficit. Copper-edge
actuals below 0.00005 mm remain structural; this limit does not weaken that rule.
It is a print-resolution limit, not a geometric proof, and the behaviour is
unchanged. See [native finding resolution](scaffold.md#native-drc-match-tolerance-known-limit-agentee-400).

The severities in force are the immutable project file's. ERC and DRC both record
`details.by_severity`, `details.by_type` and `details.ignored_checks`, the keys
kicad-cli reports at severity `ignore`. A warning is graded like an error. The
[fome-mc severity template](scaffold.md#fome-mc-severity-template) (AGENTEE-189)
carries fome-mc's pinned ERC and DRC severity maps. Optional
[owner-approved exact waivers](violation-waivers.md) (owner decision 2026-10-18)
report `details.waived` with reasons and original native severities, plus
`waived_by_severity` and `unwaived_by_severity`. Unmatched errors and warnings
still fail; without a waiver file every native item is unwaived. Native project
exclusions remain refused by severity capture; dispositions use the separate
captured immutable waiver record.

Those project settings remain immutable during a campaign. A writer's
`pcbnew.SaveBoard` may rewrite sibling `.kicad_pro`/`.kicad_prl` files and trip
the immutable-input guard; the diagnostic names this likely cause when the
same-stem board also changed. Follow the
[board-saving writer rule](campaign-guide.md#saving-boards-without-changing-project-siblings)
to restore or exclude siblings through the sandboxed step runner, or save to
staging and copy back only the board. The guard still refuses the changed
verification input; a stage receipt cannot waive it.

## Captured routing constraints

The [campaign routing step](routing-adapter.md#the-step) runs pinned kicad-route
`c4af095` (versioned `kicad-route/route/1` results; opt-in `route/2` differential pairs)
in the writer's sandbox with a read-only checkout and state directory. Its receipt
records provenance and applicability; it is not an acceptance check. The campaign
verifies the retained routing result before adopting an exit-0 or exit-2 board and
on replay. Exits 1, 3 and 4 produce no proposal. Fresh DRC (including parity and
unconnected items), `copper-width/1` and configured routing constraints still
determine the captured board's outcome.

The adapter's opt-in [per-net layer grade](routing-adapter.md#per-net-signal-layers-agentee-258)
checks all tracks, arcs and full via spans against declared per-net signal layers,
using the validated board's native inventory. Its existing `reference` declaration
checks plane fill under the full routed length. Defect or unknown grades withhold
the routed candidate. An absent per-net declaration is unknown for that obligation;
a passing global layer grade does not satisfy it. The item records the graded
board's `board_sha256` for candidate binding. This adds no scope check kind.
The [RF review](rf-review.md#per-net-rf-layer-requirements) grades a captured
per-net signal/reference requirement's own layers on the verified receipt's
retained native facts. It is `pass` only with passing reference continuity under
every routed track of the reviewed board; otherwise it is `defect` or `unknown`.

The [routing check](routing-rules.md) accepts a captured `params.routing_rules`
JSON document. It compiles explicit bounds into isolated native custom-rule
runs. Scope/2 and scope/3 bind exactly one requirement per routing check and
freeze its rule document and captured native rule sidecars. Complete route and
constraint coverage is required; unsupported topology or missing inputs are
unknown. General DRC and schematic parity remain separate obligations.
Native pad entry chains and unique off-centre through-via endpoints retain
native copper UUIDs and offsets in `routing-probe.json`; saved in-pad copper
remains included in native length measurements, without pad-centre extensions.
Ambiguous, branched, looped, crossing and other-net contacts still refuse; see
[native pad contacts](routing-rules.md#native-pad-contacts-agentee-185).
The evidence includes generated and staged effective rule hashes and the native
DRC reports. An impedance target remains unverified because no qualified stackup
and solver path is implemented, even when those inputs are named in the contract.

The [routing equivalence check](routing-equivalence.md) compares the captured
board with `params.reference_board`, an immutable captured routed output. It
normalizes native tracks, arcs, vias and zones in integer nanometres into sorted
multisets, preserving exact duplicate counts. It retains differing elements,
per-element duplicate counts and count differences, and both board hashes; UUID, ordering and
other byte-only changes are informational. Empty or unsupported native
inventories and failed execution remain unknown. This check does not replace
routing completeness, DRC or parity.

## Captured fabricator profiles

The [fab profile check](fab-profile.md) accepts a captured `params.fab_profile`
document whose limits each cite an exact quote in a captured UTF-8 source. It
runs sourced physical minima as isolated native rules and compares layer count,
fixed-axis outline extents and SMD presence with the saved board. Missing sources,
missing profiles and unqualified outlines are unknown. Preferred-tier overruns
are reported as extra charges, never defects. A `fab-profile/2` conjunctive rule
charges only vias whose drill and diameter are both under its bounds. Each term
runs its own native DRC, and the rule charges the intersection of the cited via
UUIDs. Out-of-range numeric profile bounds are unsupported in both descriptors
and execution. The [2026-10-19 JLCPCB via preset](fab-profile.md#dated-jlcpcb-standard-via-preset)
records the ordinary-process drill/diameter minima, a separate small-drill
surcharge and the joint small-via surcharge from host captures. A 0.25 mm drill
with a 0.4 mm diameter reports its saved via UUID as an extra charge; a 0.45 mm
diameter is outside that joint band. Its `/2` `drill_options` declaration checks
saved integer-nanometre drills against 0.15, 0.2 and 0.25 mm or the continuous
range from 0.3 mm. A drill below the sourced 0.15 mm minimum is a `via-drill`
defect (`below_minimum_vias`), never a surcharge. Other drills (including 0.22 and
0.28 mm) make drill grading unsupported and the check unknown, with saved via
UUIDs in `unsupported_vias`, typed `code=via_drill_off_table`, and the checked
`drill_table` with its capture path and sha256; beside a below-minimum drill, unknown takes
precedence and both lists are kept. Drill surcharge records then stay unevaluated.
Drilled PTH or NPTH pads have no captured table: the `via-drill` record keeps its
via result as `via_status` but is unknown with `code=hole_class_without_drill_table`
and `unsupported_hole_classes` (saved pad UUIDs and requested drill sizes), and
the check is unknown, never pass. Supplier process applicability
and pricing remain unqualified.
`/2` [silkscreen minima](fab-profile.md#silkscreen-limits-fab-profile2) grade
pad-to-silk (native `silk_over_copper`, measured to pad copper), stroke-font
silk text height and thickness, and saved silk stroke widths. Silk-to-mask
clearance has no qualified measurement and is always unknown
(`code=silk_to_mask_unmeasured`). Tented pads, outline-font or knockout text,
filled shapes and other silk classes are typed unknowns. Silk citations bind the
fact's role and value, so a recommendation cited as a minimum is unknown
(`source_role_mismatch`). Silk minima grade only when profile `applicability`
cites the source page's supplier base-material, process and copper-layer scope,
a captured profile `order` selection lies inside it, and the saved board matches
the order. The order selections must cite one captured order subject on one
capture and page that records each selection once; spliced or ambiguous orders
are never merged. The saved stackup must also classify completely, with one
material on every dielectric sublayer and every material mapped. Otherwise they
are unknown, with codes `silk_applicability_unsourced`, `silk_order_incoherent`,
`silk_order_unsourced`, `silk_stackup_unverified`, `silk_material_unverified`,
`silk_applicability_mismatch` or `silk_order_mismatch`. Sourced `advisories`
(recommendations, guide values) never grade. The
[2026-10-22 JLCPCB silkscreen profile](fab-profile.md#dated-jlcpcb-silkscreen-profile)
binds the capability Legend table and the silkscreen guide.
The [telemetry-pod profile](telemetry-pod-fab-profile.md) records
eight dated JLCPCB native/geometry limits plus a finished-thickness tolerance
from numeric/name captures. Envelope/2 checks the honest physical sum against
that captured tolerance and retains unbound mechanical obligations. The fab
check itself makes no thickness verdict; omitted DFM constraints stay explicit. No profile is selected
implicitly.

## DFM geometry rules

The [DFM geometry check](dfm-geometry.md) takes exactly `params.dfm_profile`.
That is a captured `agent-ee.dfm-profile/1` with an explicit threshold for each
rule. The rules are annular ring, via aspect ratio, acid trap, SMD bridge gap,
component courtyard spacing, differential-pair skew and copper balance, and each
is evaluated on the probe's exact `--dfm` facts. Findings carry a code,
coordinates, native UUIDs and references, plus per-finding `gate_findings`
identities. Each rule also records its definition-bound `false_negative_classes`
in `details.rules` and `limitations`. A rule that cannot evaluate an item, such
as one with no saved board thickness, a blind via, a custom PTH pad, an unfilled
zone or a missing courtyard, is `unsupported`, and the check becomes
`unsupported/unknown`.

## RF geometry

The [RF geometry check](rf-geometry.md) takes exactly `params.declaration`. That
is a captured `agent-ee.rf-geometry/1` record for one RF net. It may hold a
grounded-CPW section (width, gap and tolerance targets, read directly or by JSON
pointer from a captured calculation record in `mm` or `mil`, bound by a required
declared SHA-256; missing, malformed or mismatched hashes refuse capture), a
stitching section (pitch and band) and an explicit trunk/stub topology with per-branch
length bounds. A reported-only electrical length may take its effective
permittivity inline (a finite number at least 1.0) or from a captured solver
record, bound by path, SHA-256 and JSON pointer. A hash mismatch, a record without
`impedance_calc_status` 0 and `dResultValid` 1, or a value below 1.0 refuses, and a value absent at the
pointer is unknown.
The sandboxed pcbnew probe records facts only. The coordinator
grades:

- exact track width;
- the minimum gap to coplanar ground on exact pcbnew polygons, plus the
  maximum gap sampled along both edges;
- coplanar ground present beside every track side;
- reference-layer ground under the trace and both gaps;
- stitching-via pitch on both sides of each branch;
- routed length per branch;
- the return-corridor guard: reference-layer ground under every signal-layer RF
  track grown by a declared guard width per side, with no foreign antipad, RF
  via antipad, fill keepout or void inside. The width is declared with a
  recorded `basis` (inline or by JSON pointer into a captured record bound by
  its declared SHA-256; a mismatch refuses). It has no default. A `null` width
  is unknown, and a width that rounds to 0 nm refuses. Only the RF net's own
  pad clearance is excused, as in AGENTEE-231 reference continuity; RF-net
  copper on the reference layer is a defect with its own reason.

A missing coplanar ground or reference is a defect stated as an absent
structure, never graded as microstrip. A `null` bound, an unfilled zone or an
unrouted branch is unknown, with execution `unsupported`. The check is not RF
qualification, and the guard is a geometric guard result only.

## ESP32 outside-board antenna placement

The [antenna placement check](antenna-placement.md) (AGENTEE-453) takes exactly
`params.declaration`, a captured `agent-ee.antenna-placement-declaration/1`
record. It binds the reviewed `outside_board` policy for the Espressif
guidelines snapshot (section 1.4.7, page 33, Fig. 24/25), the module datasheet
and the stock footprint, each to its reviewed SHA-256 pin. It also names the
board edge the antenna must cross. If a source is captured with other bytes,
capture is refused. A source that is not captured is `unknown`. On the saved
board the check grades seven sections: sources, footprint identity and pose,
antenna area outside board material, feed point on the board edge, feed-side
corner on the declared edge, retained module keepout, and copper inside that
keepout. Missing evidence, a back-side or off-axis pose, and a body overhang
beyond the edge are `unknown`. Overlap, a wrong edge, a removed or altered rule
area, keepout copper and a pad whose native copper differs from the captured
footprint (for example one pad rotated in place) are defects. No clearance is
derived: the Figure 11-1 pin label is not a clearance. Espressif prints no
corner distance, so `manufacturer_corner_compliance` is always `unknown`. The
feed-side corner is graded only when the declaration carries the exact
agent-derived class-default `corner_policy` (an 18 mm slot taken from the
module width). That section, and any verdict it decides, has
`verdict_authority: agent-derived-class-default`, never
`manufacturer-sourced`. Without the policy the corner is `unknown`. This is a
bare-board gate. Housing pocket, in-housing clearance, nearby metal, support and
installed RF performance stay open obligations, carried by `bare_board_claim`,
the AGENTEE-449 composition point. The claim requires the unchanged candidate
snapshot, the immutable baseline, the complete sealed check set and a report
equal to its re-derivation from the sealed native measurement.

## Native netlist

No check parameters are currently interpreted. A complete hierarchy is required.
`design.xml` must exist, parse, and contain components and nets. It is retained
unchanged. Before producing connectivity, the check uses the shared S-expression
reader and [occurrence audit](native-binding.md#occurrence-identity) to match every
placed symbol's sheet UUID path and symbol UUID to the native component inventory
in both directions. Repeated sheets use separate occurrence paths; all placed
units of a multi-unit component must match. DNP, `on_board no` and `in_bom no`
components still require a match. An omitted `#` reference is exempt only when
its embedded definition is power and its occurrence reference is unambiguous.

A tool exit of 0 and matching sheet count are insufficient: KiCad can silently
omit the components of an unreadable child sheet. Any unmatched or ambiguous
occurrence gives execution `failed`, outcome `unknown`, with `hierarchy_gaps` and
`missing_refs` in details and affected references in the summary. No connectivity
projection is written or credited for that export. Checks that require this
netlist refuse the failed prerequisite. Identity validation reuses the native
XML reader without imposing consumers' pin inventory or topology-search limits.
This establishes component-instance coverage; wire/label connectivity equivalence
requires a separate declared predicate such as netlist parity.

`connectivity.json` derives:

- Components keyed by reference: value, footprint, DNP state, native `libsource`,
  sheet name, sheet UUID path and component UUID text.
- Nets keyed by exact exported net name: sorted `reference.pin` strings.

A successful export has outcome `info`; connectivity extraction is evidence,
not a functional acceptance limit. The native XML and original schematic remain
available to check the extraction. Simulation consumes this generated
connectivity from the same run; it does not use fixture-specific wiring.

## Additional netlist checks

`netlist_extra` requires `policy` and an earlier `netlist_check`. The
[captured topology policy](netlist-extra.md) declares exact symbol classes,
two-terminal pin inventories, exemptions and anchored rail pairs. One defect
record names each self-shorted passive or switch, or merged rail pair, with
its native pins and net identity. Unknown classes or unresolved declarations
keep the check `unsupported/unknown`; missing or malformed connectivity makes
execution `failed`.

Same-basename nets on distinct native net IDs emit one advisory per basename
with `severity=warning`, including `/a/SDA` and `/b/SDA`. These warnings retain
`status=informational` and cannot supply a defect witness. A shared global net
has one native ID and produces no island warning. The check covers direct-net
equivalence; it does not trace geometric wire paths, copper or impedance.

`part_decisions` requires `netlist_check`, `decisions`, `spec_record`, `spec`,
and nullable `deviations`. The [captured projection](part-decisions.md) becomes
immutable; deviations remain mutable. Exact native Partnumber/LCSC identities
must be present on populated, on-board symbols. An explicit substitution needs
a reason, captured evidence references, and a present replacement. Unresolved
part-decision exclusions withhold pass; explicitly evidenced non-part
constraints remain visible outside the predicate. Neither display values nor writer claims
are native part identity evidence.

`net_names` requires `netlist_check`, `policy` and `levels`. The immutable
[owner policy](net-names.md) declares required names with exported
`{reference, pin}` anchors. The mutable annotation marks every generated name
`required` or `suggested`. A required name that is absent, on two native nets,
split across its anchors, displaced by another name, sharing a net with another
required name, or downgraded to suggested is a defect. So is a `required` claim
the owner did not make. Suggested collisions get deterministic `<name>_<k>`
proposals that keep a trailing `_P`/`_N`, reported with `severity=info`. A named
net without a level, or an unresolved anchor, keeps the check
`unsupported/unknown`. Nothing is renamed.

`topology_rules` requires `rules` and an earlier `netlist_check`. The immutable
[rule file](topology-rules.md) declares exact symbol classes and rules tagged
with the baseline requirements that map the check: pin on net, pin unconnected,
at least N parts of a class between two nets, a series part between a pin and a
net, a forbidden class between two nets, a specific part between two nets,
value equality across references, and expected components matched to
references by bounded backtracking with bijective net roles. Only two-terminal
passives may match with swapped terminals. A violated rule makes the check
`ok/defect` and names the offending references or kind counts. An absent or
ambiguous net or reference, an unreadable value, an unclassified part that
could carry the structure, or an exhausted visit bound keeps the check
`unsupported/unknown`. A rule file that does not validate is `malformed`.

`regeneration_determinism` requires `format`, `reference` and `subject`, plus
`decimals` for boards and schematics. The [canonical design diff](design-diff.md)
drops UUIDs, timestamps, net codes and tool/date headers, rounds coordinates to
the declared decimals and keys children by reference, name or content. An empty
diff passes even when the bytes differ; the byte difference is recorded, not
graded. A non-empty diff is `ok/defect` with per-path before/after entries and
structural, placement and identity hashes. A side that does not parse is
`malformed`; a missing or unverified producer output is `not_run/unknown`,
never "no change". A path `reference` is an immutable baseline input and cannot
be the scope's writer-mutable board or schematic.

The [stable schematic regeneration helper](schematic-regeneration.md) uses this
same typed diff and hashes in its diagnostic change report. It is an opt-in
schematic-stage producer, not another check kind; ERC, netlist parity and review
still grade the regenerated candidate. A parse failure or human-edit conflict
produces no schematic output and leaves acceptance unknown.

`design_rules` requires `pack`, `bindings`, `netlist_check` and `cards`. It
also accepts `facts_check` (an earlier `datasheet_facts` check) and `delegated`.
The immutable [rule pack](design-rules.md) holds versioned cards, and the
immutable owner bindings anchor roles to exported `{reference, pin}`
terminals. Each enforced card publishes one predicate. An advisory card has no
qualified adapter or delegated check. Naming one in `cards` makes the check
unsupported, so scope/3 refuses it. Fact-gated cards (`RESET_EN_RC`,
`USB_SERIES_RESISTORS`, I2C addresses) are unknown without a verified
applicable fact. A missing role group is unknown. Any violated card is a
defect that cites the native net. With no defect, any unknown card keeps
the check `unsupported/unknown`.

## Intent and golden netlist parity

The [shared design-size ceilings](design-size-limits.md) bound electrical graph
inputs to 1,024 components, 2,048 nets and 8,192 terminals. Above-limit inputs
remain unknown; supported inputs undergo the same checks.

The [`netlist_parity` check](netlist-parity.md) compares a captured golden XML
netlist or captured connectivity intent with an earlier native `netlist` check.
Required parameters are `source`, `netlist_check`, `predicate` (`topology`,
`components`, or `instance`) and `multiset` (`exactly` or, for component counts,
`at_least`). Resolved intent requires its captured originating intent and libraries
so the coordinator can rederive it. One check maps to one baseline requirement.

Exact physical-pin incidence preserves identical component instances and ignores
reference and net names by default. Optional `anchors` declare fixed references
or sheet/UUID identities; `net_name_policy` reuses a captured AGENTEE-44 required-name
policy bound to a baseline requirement. Resolved anchors seed color refinement
and constrain the exact search. Reports retain the anchor set, policy hash,
contradictions and assignment step count. A contradicted anchor is a defect;
unresolved sheet/UUID identities and incomplete terminal evidence remain unknown.
Both anchor forms are refused for the component-only predicate. Missing or extra
parts, changed bindings and merged or split nets are defects. Ambiguous required
instance correspondence, unresolvable match keys and bounded-search exhaustion
are unknown. Scores describe a match;
they do not decide acceptance. Electrical adequacy and footprint suitability are
outside this check.

The full native `design.xml` supplies the graph. The retained `connectivity.json`
must parse and agree with the existing XML projection. Missing or unreadable
inputs and unsuccessful exports give execution `failed`; malformed or
contradictory inputs give `malformed`. These failures have outcome `unknown`.
The report records hashes of the source and both native artifacts; ordinary
coordinator evidence sealing also covers those artifacts.

## Saved-board geometry

| Parameter | Meaning |
| --- | --- |
| `min_track_width_mm` | Legacy optional straight-segment minimum; required finite positive minimum for copper-width/1 |
| `exclude_nets` | Exact exempt net names; legacy default empty, explicit list required for copper-width/1 |
| `criterion` | Optional `copper-width/1` selects versioned straight-track and arc coverage |
| `covered_primitives` | Required with copper-width/1: exactly `["PCB_TRACK", "PCB_ARC"]` |
| `net_constraints` | Optional with copper-width/1: captured [net-constraint record](net-constraints.md) whose per-net netclass minimums raise the limit for their nets |

Use a finite positive numeric width justified by an actual requirement. The
legacy handler converts the value to float but does not comprehensively reject invalid
or nonphysical width limits at scope-validation time. Do not assume a malformed
criterion is made meaningful by a check that executes.

Each footprint record carries pcbnew's `dnp`, `exclude_from_bom`,
`exclude_from_pos_files` and `board_only` attributes.

Without the new criterion or a width limit, geometry has outcome `info`. The
legacy check with a limit checks all
non-excluded items whose extracted type is `track`. The comparison permits
1e-9 mm numeric tolerance. No eligible track segments is malformed, not a
vacuous pass. At most 50 offending segments are copied into check details; the
full geometry remains in `geometry.json`. Vias, arcs, zones and pads are not
covered by this straight-segment width criterion.

The geometry file contains footprint reference/value/DNP/UUID/layer/position,
pad number/net/attribute/position/size/drill/UUID, track/via dimensions and nets,
zone net/layers/fill-state/minimum-thickness/UUID, and a connectivity count when
available. Coordinates and `width_mm` dimensions are millimetres, rounded to
four decimal places. Track items also retain `native_class` and exact `width_nm`. The `nets` counts describe pads; track/zone-only nets can have zero pad
counts. A failed unconnected-count query is recorded as unavailable information,
not silently replaced with zero. Other extraction failures fail the probe.
Fabrication facts also retain copper stack order, a proven straight outline,
exact pad/via `drill_nm` and native `flash_layers`, per-layer pad `flash_bbox_nm`,
`zone_fill_outlines` and `copper_conductor_shapes`; see [fab round-trip](fab-roundtrip.md).

With `criterion: "copper-width/1"`, declared tracks and native arcs are checked
using integer-nanometre widths without the legacy tolerance/rounding. Empty or
unsupported eligible geometry is `unsupported/unknown`, including when another
primitive is known narrow. Complete coverage is `ok/defect` for any below-limit
primitive, otherwise `ok/pass`. Candidate-bound `copper-width.json` retains all
primitive identities, values, limits, statuses and coverage counts alongside the
raw probe report. With `net_constraints`, each primitive's limit is the larger of
`min_track_width_mm` and its net's recorded netclass minimum. The record must
pass, and the captured board-sibling `.kicad_pro` must carry its classes and exact
patterns. Every recorded net must exist in the saved board, including nets in
classes that declare only clearance or impedance. Missing names appear in the
refusal. DRC setup also refuses unmatched emitted literal assignments. Both
policy files are immutable inputs; a missing or mismatched binding is
`unsupported/unknown` ([net constraints](net-constraints.md)). Vias remain separate; zones, pads and graphic copper shapes
are outside this routing-width criterion. See [coverage](coverage.md) for the
complete class/count contract. Opting in changes the scope/2 baseline identity;
existing baseline criteria keep their historical meaning.

The [GNSS RF controls](gnss-rf-path.md) exercise per-net width binding and
native maximum length. A single authorized W2 calculation returned valid geometry
after three failed W1 calculations. RF_50 binds `/POD_GNSS_RF`, from NEO-M9N
U3 pin 11 through the recorded source/native naming adaptation, and declares lower W1 width 0.334416796875 mm
and selected gap 0.2032 mm; upper W2 width 0.321716796875 mm stays source metadata.
The existing emitter rounds the minimum up to 0.335 mm. A pinned-router probe
passes this minimum and fails after one segment is narrowed to 0.334999 mm.
The pod map keeps impedance as a limitation and the emitter records the target
as `recorded_not_enforced`. Minimum width/clearance checks do not verify exact
coplanar gap or impedance. The RF maximum length remains unbound; the separate
synthetic controls' 15 mm is test-only.

Width is not ampacity. A zone's `filled` flag is not a full polygon/return-path
analysis. Review actual saved native polygons and connectivity when needed;
the control reviewers did that alongside native DRC evidence.

## Ideal passive transient

Although named `rc_transient`, the current generator accepts supported R/C/L
symbols. It is a bounded ideal passive-network check, not a general KiCad SPICE
exporter or active-circuit simulator.

| Parameter | Required/default | Meaning |
| --- | --- | --- |
| `netlist_check` | Required | Earlier native netlist check ID |
| `input_net` | Required | Exact exported net to drive |
| `output_net` | Required | Exact exported net whose voltage is measured |
| `ground_net` | Required | Exact net mapped to SPICE node 0 |
| `step_v` | 1.0 | Finite positive ideal step amplitude, volts |
| `stop_ms` | 5.0 | Finite positive stop time, milliseconds |
| `calibration_inputs` | Optional captured path list; nonempty before bench ingestion | Immutable model applicability inputs for empirical feedback |
| `measurements` | Default empty | Entries with `name`, `at_ms`, optional `min`/`max` in volts |

Deck generation and measurement conditions share validated parameters. Omitted
`step_v` uses 1.0 V, including for bounded measurements; `details.step_v` records
the effective amplitude. Defaults are not added to the captured scope. Omitted
`step_v` and explicit `1.0` produce matching conditions, while their different
scopes retain distinct candidate identities. Historical explicit amplitude
spellings remain unchanged; see [coverage](coverage.md) for condition and runtime
policy compatibility.

Measurement names are lowercased and must be unique, matching
`[a-z][a-z0-9_]{0,31}` after normalization. `at_ms` must be finite and in
`(0, stop_ms]`. Limits must be finite numeric values; two bounds must satisfy
`min <= max`. Limits are inclusive. A missing/null bound is unbounded on that
side. Unbounded measurements are informational; no bounded measurements means
the completed check is `info`, not a functional pass.

The prior netlist result must have execution `ok` and a generated connectivity
file. DNP parts are excluded and listed. Virtual `#` references and recognized
connectors carry no element and are listed as ignored. Other populated parts
must have a recognized native passive symbol class (`R`, `C`, `CP`, `L`,
including supported underscore-suffixed forms), a matching R/C/L reference
prefix, and exactly two connected pins. Unknown populated active devices are
unsupported; they are not replaced with ideal guesses. At least one populated
passive element is required. Net names map to sanitized SPICE names; collisions
or unusable names are refused. The node map and native source UUIDs are retained.

Values are sent to ngspice in raw SPICE scalar notation. **`M` means milli;
`Meg` means mega.** Embedded engineering notation such as `4k7` is unsupported.
This is not a complete KiCad value/units parser: some trailing unit text is
accepted and exponent syntax is restricted. Review original and parsed values
in check details and the generated deck. A native display value that looks
familiar is not sufficient evidence of the intended numerical model.

`deck.cir` contains the ideal passive network, a PWL source rising from 0 to
`step_v` over 1 ns, a transient command, and requested voltage measurements.
The requested transient increment is `stop_ms/1000` ms. Tolerances, parasitics,
temperature dependence, source impedance, component nonlinearities and external
loading are absent unless represented in the supported captured network.

The generated control script runs the deck and writes a binary raw trace. The
invocation intentionally avoids batch `-r`, which can skip `.meas` in the
demonstrated setup. A simulator exit 0 alone is insufficient:

- Known solver/error messages in the log cause failed coverage.
- Every requested measurement must be present, parse as a finite float and have
  its applicable bounds evaluated.
- The raw trace must have a valid binary/header shape, complete double values,
  finite values and at least two points.

A finite measured value outside a declared bound is `ok/defect`; a missing or
nonfinite measurement is `malformed/unknown`. Binary structure validation does
not prove the time axis, model applicability or waveform meets every undeclared
condition. Inspect the stimulus, deck, measurements and independent calculation.
The [evaluation](evaluation-plan.md) independently derives the demonstrated RC
response and its unchanged limits.

## Generic measurements and coupled margins

The [measurement-adapter reference](measurement-adapters.md) defines producer and
parser invocation, every supported parameter, request/report formats, retained
artifacts, margin arithmetic, domain/condition rules and failure paths. These
interfaces can host independently qualified engineering tools; installing
agent-ee does not install thermal, parasitic, DFM or supply-chain adapters. Declared
assembly and test access geometry is the separate [`dfa_dft`](dfa-dft.md) check.

agent-ee does not install thermal, parasitic, DFA/DFT or supply-chain adapters; the
built-in DFM geometry rules are the separate [`dfm_geometry`](dfm-geometry.md) check.

In scope/2, bounded `rc_transient` results also expose electrical scalar metrics
and margins for coupling and empirical correction. Unbounded measurements remain
informational. A native RC check with no bounded measurements does not run the
empirical correction stage. Declare bounds and a nonempty `calibration_inputs`
list before using bench
feedback. The condition string is emitted from the configured step amplitude and
measurement time; read it from `checks/CHECK_ID.json` when preparing a report.
[Bench feedback](bench-feedback.md) describes residual intervals and replay cases.

A scope/2 RC result can fail despite an in-range raw SPICE value when its
empirically corrected interval crosses a bound. Inspect `details.metrics`
(`nominal`, `lower`, `upper`), `details.margins` and `bench-replay.json` alongside
the raw `details.measurements`; they answer different questions.

## Installed kicad-happy

`kind: "kicad_happy"` runs the installed v2.3.1 schematic, full PCB, thermal
and EMC analyzers with schema `1.4.0`. The installed tree stays external.
No lifecycle, datasheet sync, distributor or SPICE-enhanced option is invoked.
Tool execution has no network and receives the immutable candidate read-only.

Parameters are explicit: `installation` is the installed checkout's absolute
path; `installation_sha256` pins the conservative `skills/` and
`.claude-plugin/` inventory (excluding Python/test caches). Compute it using
`agent_ee.kicad_happy.installation(path)["sha256"]` before authoring the scope.
The pin, rule declarations and bounds become baseline criteria in scope/2.
Changes require a new baseline. Symlinks in the inventoried tree are refused.

`rules` is a list of records with `id`, `analyzer`, `rule_id`, exact native
`components`, `field`, and finite `min` and/or `max`. `counted_rules` and
`blocking_rules` explicitly name rule IDs; blocking must be a subset of counted.
All counted rules must be evaluated. Undeclared native findings are informative.
The native scalar form qualifies `analyzer: "schematic"`, `rule_id: "RC-DET"`
low-pass records for `cutoff_hz` and `time_constant_s`. The separate
[qualified adapter form](kicad-happy.md) uses `adapter`, captured `assumptions`
and `assumptions_source` in place of an arbitrary field pointer. It covers the
explicit power, feedback, protection, derating, thermal and EMC predicates
documented there; other native rules remain informational.
For example, `components: ["R1", "C1"]`, `field: "cutoff_hz"`, `min: 150`,
`max: 170` checks the original RC fixture's cutoff. See the independent
[expectations](../fixtures/kicad-happy/EXPECTATIONS.md).

Raw per-analyzer JSON, argv, stdout/stderr, execution record, installation
inventory and executed Python helper hashes remain under `work/<id>/`.
`normalized.json` records native rule/severity, component/net/pin locators,
raw JSON path/pointer and explicit satisfied/violation/not-evaluated status.
An absent blocking evaluation is unsupported, never clean. Invalid JSON is
malformed; unrecognized envelopes/findings are unsupported; crashes remain
failed execution. `verify` rehashes the installed inventory, including helper
and data bytes, and refuses drift. The usual scope/2 seal and requirement/check
witness remain mandatory for accepting a coordinator finding.

Native qualification tests use `AGENT_EE_KICAD_HAPPY` to locate an installation
(default `$HOME/git/kicad-happy`). `AGENT_EE_REQUIRE_TOOLCHAIN=1` makes missing
native prerequisites or the installation a test failure. The demonstrated host
uses KiCad 10.0.7, ngspice 42, Bubblewrap 0.9.0 and Python 3.12.3; qualification
inside the common completion image remains separate.

## Installed kicad-happy severity gate

`kind: "kicad_happy_severity_gate"` runs the installed v2.3.1 schematic, full
PCB, cross-analysis and EMC analyzers offline in the sandbox. It requires a
scope/2 or scope/3 baseline and exactly `installation`, `installation_sha256`
and `dispositions` parameters. The installation pin follows the existing
`kicad_happy` inventory. `dispositions` is a captured immutable requirement file
using `agent-ee.happy-dispositions/1`; every warning or assumption-bearing finding ID requires its own
record with `disposition: accepted_risk|false_positive`, `rationale` and
`recorded_by`. No disposition can waive an error.

The AC4 false-positive regression reconstructs its proposed analyzer from a
retained MIT patch against the verified pinned installation and verifies the
resulting identity; it requires no host-only correction checkout or Git objects.
The installed v2.3.1 limitations remain blocking; see the
[limitation record](happy-severity-gate.md#known-analyzer-limitations-pinned-231).

Every error is `ok/defect`. A warning, or any severity with nonempty native
`assumptions` or `source_path: assumed`, is also a defect without an exact-ID
disposition. Assumption-bearing info accepts dispositions; plain info does not
and its dispositions stay unused. `undispositioned_warning_ids` includes both
warnings and assumptions. Valid reports with no errors and every warning and
assumption dispositioned are `ok/pass`; unknown,
missing or malformed analyzer output stays unknown. The exact disposition bytes,
raw reports and normalized native/disposition pointers are cited and sealed;
preflight and public `verify` recheck the installed pin. Scope/3 predicates are
`happy-severity:no-errors/1` and `happy-severity:warnings-dispositioned/1`.
See the [schema, failure boundaries and original fixtures](happy-severity-gate.md).
Thermal and SPICE are excluded from this kind. Existing `kicad_happy` stages and
policy are unchanged. This is a severity policy over emitted findings, not
complete analyzer rule coverage or electrical/EMC certification. Pinned 2.3.1
PP-001 and PR-001 have [known false positives](happy-severity-gate.md#known-analyzer-limitations-pinned-231)
that stay blocking errors until a requalified pin replaces them.

## Hardware interface invariants

The offline [io-map compiler](io-map.md) produces `agent-ee.interface/1`
contracts from pinout CSVs. `hardware_interface` binds their CSV hashes as
immutable inputs, checks `no_connect` predicates against native connectivity,
and keeps explicit unresolved terminals and predicates unknown. External
destination text supplies traceability only.
Invariant `source.sha256` optionally binds exact captured requirement bytes;
malformed or mismatched hashes are refused. Each local source path must use
one digest on all its invariants or omit it on all, including validation without
source bytes. A declared digest adds the requirement file to immutable inputs.
The compiler CLI always requires readable source bytes and refuses a missing
source with exit 2 and a typed error before writing a contract;
`--require-source-sha256` remains accepted. Digest-free legacy contracts retain
their existing validation. See [source options](io-map.md#requirements-source-provenance).
Requirement citations carry `qualification` (`unqualified-carryover` default for bare IDs, or `sourced` with a captured sha256 source); unqualified IDs are reported in `details.requirement_qualification` and never graded. Optional `qualification_record` check parameter pins a record that binds the complete citation set, statuses and original provenance. Every consumer loads it through one typed loader (pin, exact bytes, schema; null is never absent), binds it to the captured CSV rows and verifies origins per distinct path/sha256. Coverage and `details.requirement_history` report it. See [qualification](io-map.md#one-record-loader).
Concrete MCU rows require an explicit `MCU_Path=direct` declaration or the IDs
of declared [indirect paths](io-map.md#indirect-mcu-paths). Undeclared path names
are refused with their CSV row; TBD MCU rows keep an unresolved predicate without
asserting MCU-to-connector net equality. An `indirect_path` invariant passes only
when every conductor segment, intermediate part value, pin name and declared
direction is proven on the native netlist; it never asserts connector-to-MCU
`same_net` and makes no transfer, CAN-rate, firmware or BLE claim.

`hardware_interface` requires `contract` and an earlier `netlist_check`. The
[interface reference](hardware-interface.md) defines the captured normative
schema, immutable inputs, supported predicates and unsupported mapping policy.
It checks declared schematic properties and direct conductors, without inferring
interfaces from names or treating components as transparent shorts.
A library `no_connect` pin, hidden or visible, is proved isolated only by its
own explicit no-connect marker exactly on the pin, with every sheet item
modelled and nothing else within the 10 µm source margin; otherwise it is
unknown, never pass.

## Reference-block evidence validation

The [reference-block validator](reference-block-interface.md) is an offline
capture/validation command, outside the check registry. It compares every block
payload hash with the referenced run's candidate inventory and invokes the
public run verifier, including baseline and coordinator evidence sealing.
Changed sheet bytes are refused even when the block's local hashes are updated.
Missing, incomplete or escalated runs leave the block unverified.

Only passing `hardware_interface` requirements bound to the actual native block
occurrences can currently contribute coverage. A checked shadow sheet cannot
qualify an unused captured block. Optional layouts and other requirement check
families remain unverified. The complete parts manifest must pass the shared
`parts_manifest.validate_manifest` validator. Verification additionally requires
sealed passing `parts_subset` evidence for that exact mapped manifest and the
block's actual native sheet occurrences; the report records these bindings in
`parts_membership`. Missing evidence keeps the block unverified. Malformed run
records are refused (exit 2), distinct from valid unverified records (exit 1)
and unexpected internal errors (exit 3, with a diagnostic traceback).
Run provenance does not establish a containing campaign's current state.
Adapted blocks require fresh checks on the new board candidate.
The [FOME host attempts](fome-block-review-runs.md) bind incomplete reviewed runs
to the wbo1 and PSU records. Their metadata receipts omit host-only third-party
documents and cannot be supplied as complete run locations. A mapped local
object store verifies the pinned citations but gives no missing review credit.

## Footprint assignment

`footprint_assignment` requires captured `manifest` and `declaration` paths and
immutable `assembly_intent` records. The manifest declares complete pinned
footprint membership; every member and symbol library must be readable and
hash-valid. Empty selections, absent exact footprint names, missing or extra
electrical pad numbers, connector count mismatches and assembly mismatches are
defects. Duplicate thermal pad numbers count once; qualified paste-only and
unnumbered mechanical pads do not participate in electrical parity. Library
read or parse failures leave unknown coverage. An optional captured
`declarations` file (`agent-ee.footprint-declarations/1`, an immutable input)
can declare mechanical copper pads (MP), thermal-via pads, custom pad numbering,
assembly for attr-less footprints, and pinless symbols. Declared pads are
reported as mechanical. Undeclared cases keep their extra-pad defect or unknown
result, and a declaration that applies to nothing is unknown. A declared
thermal-via pad whose plated copper lacks a native `pad_prop_heatsink` or
`pad_prop_mechanical` property is also unknown, because KiCad's DRC would type
the SMD footprint as through hole
([owner declarations](footprint-libraries.md#owner-declarations)).

All indexed pads, including declared geometry and custom anchors, have a
rotation-invariant copper/drill reach bound of 1000 mm from the footprint
origin (AGENTEE-314). It includes pad translation, size, trapezoid delta and
drill offset where present, using integer ceilings and doubled half extents.
Over-limit geometry refuses before native loading with `library_coverage`,
failed execution and unknown coverage. Supported syntax and ordinary index
record fields are unchanged. See [pad magnitude bounds](footprint-libraries.md#pad-magnitude-bounds-agentee-314).

Declared custom-pad primitives share the native subset's coordinate, width,
extent and rotation-invariant reach bounds. Declared custom shapes also require
exactly one merged native copper outline on every copper layer (AGENTEE-324),
while retaining numbering-only records without `native_geometry`. Non-atom pad
positions, rotations, custom polygon vertices, drill dimensions and drill offsets
yield footprint-scoped `library_coverage`; valid members continue indexing.

The shared index also qualifies explicitly solid-connected SMD custom pads
with rectangular/circular anchors and bounded polygon, rectangle, circle or
line primitives through a sandboxed pcbnew load (AGENTEE-300). Primitive
coordinates and local copper extents must fit +/-1000 mm; widths must not
exceed 1000 mm. The sum of the pad translation magnitude, primitive radial reach
and half its stroke width must be at most 1000 mm from the footprint origin,
regardless of pad rotation. Integer ceilings prevent rounding down at the boundary.
Excess reach refuses before native loading with `library_coverage`.
The native merged polygon including the anchor must have exactly
one outline; pcbnew-reported holes within it are allowed (AGENTEE-304). Detached
islands refuse with `library_coverage` and no qualified footprint. It retains native
copper bounds and pad identity; unsupported constructs or native failures are
unknown. Numbering declarations alone never qualify geometry. See
[native custom pads](footprint-libraries.md#native-custom-pads-agentee-300).

Adding `netlist_check` binds each declaration to every native board component
and its captured whole-device pin inventory. Missing declarations or ambiguous
occurrence identities leave unknown coverage. DNP components still require
parity. Native-bound evidence uses `footprint:native-assignment-parity/1`;
declared-only evidence uses `footprint:assignment-parity/1`. Reports retain
per-reference findings and library member hashes. See the
[captured library contract](footprint-libraries.md) and
[native binding rules](native-binding.md) for parameters, exclusions and limits.

## Captured symbol pin references

`symbol_pins` takes one captured `declaration` (an `agent-ee.symbol-query/1`
reference list) and checks that each `(reference, lib_id, pin)` names a pin that
exists exactly, by name or number, on the selected symbol in the declared
captured libraries. A missing pin is a defect listing the actual pins; there is
no case, glob or nearest-name substitution. A missing symbol, missing library or
changed hash gives failed/unknown. The libraries are immutable inputs. See
[symbol context](symbol-context.md) for the query schema and the offline
`find-symbol`, `list-pins` and `find-footprint` tools.

Context preserves public and KiCad 10 private property names and values. Duplicate
names within a symbol, including public/private collisions, give failed/unknown
coverage with the conflicting name; inherited child overrides remain supported.

Number selectors may include `expected` with exact `name` and `electrical_type`
attributes. Attribute mismatches are defects, including swapped pins whose
numbers still exist. Such a query is immutable alongside its libraries and adds
the `symbol:pin-attributes/1` predicate; existence-only queries retain their
previous behavior. The [owned pod symbol fixture](../fixtures/telemetry-pod/symbols/README.md)
binds both symbols, datasheet citations and stock footprint declarations.

## Supplier land patterns

`land_pattern` takes a captured `criteria` path that binds supplier provenance,
raw EasyEDA geometry and KiCad library members. The check retains native
extraction and bounded overlap evidence through the coordinator seal; missing
or unsupported geometry cannot pass. See the [contract and qualification](land-pattern.md)
for exact parameters, pin-number comparisons and coverage limits. The actual
C11702/0402 comparison is a `low_accuracy` defect at IoU about 0.67. A
coordinator ruling (2026-10-22) amended the ticket's acceptance example to
match; see the [criterion 1 ruling](land-pattern.md#criterion-1-ruling-agentee-31-2026-10-22).
The absolute threshold is the only acceptance rule.

## Footprint and catalog package names

`package_consistency` requires an earlier `netlist_check`, a captured catalog
snapshot pinned by manifest hash, and a captured `agent-ee.package-map/1` table.
Each native component gets `match`, `mismatch`, `class_mismatch`, `unknown` or
`excluded`, naming the table rule that fired; a name outside the table is
unknown, never pass, and the table hash is recorded in the evidence. See
[package consistency](package-consistency.md) for parameters, rules and limits.

## Placement predicates

The [pod-scale fixture qualification](placer.md#pod-scale-qualification-agentee-181)
runs the existing four courtyard, containment, edge and footprint-keepout
evaluators over saved native proposals at 90–112 footprints on both sides.
It records runtime and byte/transform repeatability under `build/`; it adds no
registered predicate and carries no campaign acceptance authority.

The opt-in [targeted placement planner](placement-plans.md) (AGENTEE-55)
reuses these geometry judgments and the existing advisory score in the
disposable writer. It blocks ties, decreases and unproven geometry before
writing, then rejudges actual saved bytes. It adds no check kind or acceptance
authority. Cluster seeds use the same apply gates as capacitor rows; BGA
escape plans remain plan-only. AGENTEE-280 adds typed missing-key refusal,
preview/saved overhang parity and campaign selection among captured requests.
AGENTEE-287 requires equal judge policies throughout a selection, removes the
stale cluster `seed_only` flag, and reserves `IMU_AXES` markings in both the
pre-lease preview and leased targeted plan. Native regressions cover both
`targeted` and `targeted_plans` refusal on a reserved label and unmarked controls.

[Block arrangement](blocks.md#stage-b-arrangement-agentee-340) (AGENTEE-340)
is another writer request mode (`blocks`). It adds no check kind. The saved
board is judged, graded for groups and pad locality, and DRC-gated as every
placer plan is. In addition, the hard block rules are re-graded from the saved
bytes (`judgment.block_rules`). A failed hard rule refuses publication;
advisory rows are reported only. AGENTEE-355 changes only the search
([compaction](blocks.md#compaction-agentee-355), member-aligned origins and the
ranking cache); it adds no check kind and changes no gate. AGENTEE-359 stage-A
[recovery](blocks.md#recovery-agentee-359) is search only, too. The writer also
publishes `block-provenance.json`; a scope's `block_provenance` binds it to the
captured board and carries it to every initial reviewer
([review input](blocks.md#review-input-block-provenance-agentee-355)). It is
review input, not a check result, and an advisory block confers no acceptance.

`placement` evaluates one declared criterion per check against the saved-board
probe and the raw report of an earlier `drc` check; it never runs DRC itself.
See [placement checks](placement.md#placement-check-kind) for parameters,
authority and unknown boundaries, and [native qualification](placement.md#native-qualification)
for the acceptance tests and remaining campaign and fabrication-limit gaps.
A placer infeasibility proof bound by a soft envelope limit is `unknown`, not
`defect`, and names its [binding constraints](placer.md#constraint-provenance-agentee-266)
with their provenance class.
The [placer's declared edge margin](placer.md) constrains courtyard geometry;
it does not establish silkscreen or copper clearance. The
[rung-1 policy](rung1-qualified.md#placement-policy) preserves a 1.5 mm courtyard
inset and checks native placement DRC to cover J1's reference text too.
The [placement adapter's property resave rule](placement-adapter.md#property-font-resave-default-agentee-147)
admits KiCad's omitted 0.15 mm property-font thickness default during transform
comparison, including qualified side flips (AGENTEE-313); it does not grade placement
or relax field value, layer, visibility, size or justification checks.
Its [numeric token rule](placement-adapter.md#numeric-token-spellings-agentee-372)
(AGENTEE-372) compares numbers at identified argument slots of supported
footprint-child grammar positions, per graphic kind, as exact decimals, so a resave of `29.0` as `29` is no change while `29.001`
still refuses; names, values, pad numbers, unknown contexts and everything
outside footprints (board setup, stackup, rules, board graphics) stay exact
text; a side flip mirrors points and layers only at known positions and
refuses unknown subtrees holding geometry; a refused child names its first
differing token.
The [owned-zone write scope](placement-adapter.md#placement-write-scope)
(AGENTEE-178) admits rigid owner movement and refuses independent zone edits.
The [placer](placer.md#footprint-owned-keepouts-agentee-178) uses owned
footprint-forbidding polygons as exclusions for peer courtyards.
AGENTEE-212 adds `placement-groups/1` (declared group regions and anchor
distances). AGENTEE-361 classifies group regions and distance limits, and the
`edge-clearance/1` courtyard minimum. Unsourced limits are advisory, as in
`electrical-locality/1`: each measurement against one is an `advisories` entry
(`kind: unsourced_limit`, source class, measurement, `exceeded`), never a gate
finding. When only exceeded advisories remain, the check ends `execution: ok`,
`outcome: info`. Satisfied advisories (`exceeded: false`) keep `pass`. Real coverage gaps such as `courtyard_approximation` stay
unknown. Sourced violations and structural courtyard-outside-board defects
retain their verdicts. See
[constraint source records](placer.md#obstacle-and-group-sources-agentee-361).
AGENTEE-212 also adds an optional `envelope` on `outline-containment/1` that grades
envelope-declared fixed-courtyard overhangs. Curved courtyards are judged on
exact outer and inner bounds: pass on the outer, defect only on the inner;
a result only between them is `courtyard_approximation` (unknown), even where
native DRC is silent
([curved courtyards](placement.md#circular-and-arc-courtyards-agentee-212)). These proposal
rules do not change native DRC grading or the registered keepout predicate's
unknown boundary for footprint-owned areas.
A `defect` carries `details.gate_findings`, one identity per proved defect: the
reference (or `+`-joined overlapping references, or ratsnest metric) and the
defect kind; connector-edge violations use `connector_<kind>`. Only connector-edge
findings carry a `suggested_fix`, copied from the owner declaration the
predicate grades: `move` to the declared anchor, `rotate` to the declared
rotation, or `flip` to the declared side. Hints are advisory and never applied.

The `placement` criterion `imu-axes/1` checks an envelope-selected IMU transform
and a selected F.SilkS axis marking against captured declarations. It consumes an
earlier `datasheet_facts` result for part/footprint-specific axis mapping and the
ordinary earlier DRC result. Missing markers are defects; unreadable markings or
unverified axis facts are unknown. See [IMU axes](placement.md#imu-axes) for the
required parameters, qualified text and evidence limits.

`keep-apart/1` consumes declared `policy.keep_apart` pairs and minimum distances
from `placement-policy/1`. It grades projected courtyard separation,
including opposite sides. Outer-bound distance at least the minimum proves pass;
inner-bound distance below the minimum proves defect. A minimum between the
curve bounds yields `courtyard_approximation` naming the pair; missing or unproven
courtyards are unknown. Exact straight-polygon equality passes. It retains per-pair
verdicts and supplies `placement:keep-apart/1`. The pod spec gives no numeric
minimum: [keep-apart declarations](placement.md#keep-tall-and-heavy-parts-apart)
must supply it as an input.

## Pre-route placement congestion

`placement_congestion` (AGENTEE-57) is advisory. It runs the read-only placement
probe and reports ranked typed issues, congested regions and suggested moves:
`pad_overlap`, `courtyard_overlap`, `outside_outline`, `pad_clearance` and
`ratsnest_rotation`. Each region carries a failure probability, and its severity
band is computed from that probability on read. Proved pad, courtyard or outline
geometry, or a region above a declared `max_failure_probability`, is a `defect`.
A defect carries `gate_findings` with a `move` or `rotate` `suggested_fix`.
Otherwise the outcome is `info`; the check never passes. The capability has no
predicate, so only `informational` requirements may map it. In a full review the
adjudicator accepts its defect from the cited sealed result, with no requirement
witness. The finding is marked `authority: advisory` and the review still fails
([defects in a full review](placement-congestion.md#defects-in-a-full-review)).
A failed or unusable probe is unknown. See [placement congestion](placement-congestion.md)
for parameters, the clearance and congestion model, and validation.

## Board envelope predicates

`board_envelope` takes `criterion` and a captured immutable `envelope`
path. `outline/1` compares the exact declared Edge.Cuts polygon against saved
geometry and a native closed-contour probe. `layers/1` compares the exact ordered
copper layer set against saved and native enabled layers. Both `layers/1` and
`stackup/1` also compare explicit envelope copper `role` values against saved
KiCad layer types (`plane` maps to `power`, `signal` to `signal`). Missing role
declarations keep the previous verdicts. Role typing does not establish plane
net assignments or route completion. Each check binds one
field and one requirement, with predicates `envelope:outline/1` or
`envelope:layers/1`.
A field whose [constraint provenance](board-envelope.md#constraint-provenance)
is soft (assumed or owner-weakened) derives an informational obligation, so no
check is acceptance evidence for it until the owner confirms the limit.

`stackup/1` supplies `envelope:stackup/1` only when the scope's declared profile
matches the envelope reference and contains a source-verified stackup declaration.
Saved layer thicknesses use the profile's explicit exact matching tolerance;
nominal copper weights come from the cited declaration, with no oz-to-mm conversion.
A profile dielectric recorded as unpublished (`null`) binds the check, but its
envelope and saved thicknesses are unknown unless another field proves a defect.
Missing or unverified authority stays unknown and capture refuses it. Each
`placement` `connector-edge/1` check selects a fixed `reference` and supplies
`placement:connector-edge/1:<reference>`; a single fixed footprint may omit the
selector and retain the legacy predicate.

`mounting-holes/1` and `keepouts/1` also require `id`, selecting one declared item
and one requirement per check. Their predicates are
`envelope:mounting-holes/1:<id>` and `envelope:keepouts/1:<id>`. Holes compare the
unique same-reference footprint, inclusive position tolerance, exact round drill,
plating and whole-hole outline containment. Keepouts require a saved board-level
or footprint-owned rule area covering the complete polygon on every declared
layer with at least the declared forbid set. Native loading supplies transformed
board coordinates. The legacy `antenna: {reference, facts_check}` declaration
now stays unknown: AGENTEE-170's former minimum misread a pin number as a
clearance. Prior verified text facts cannot restore that invalid derivation.
Separate source-bound placement policy context and captured CAD geometry do not
establish a qualified minimum. Explicit polygon comparisons retain their
saved-declaration semantics. See the [native envelope contract](board-envelope.md#native-envelope-check)
for evidence, binding and failure boundaries. Envelope/2 preserves nominal
and physical thickness separately and derives each generator mechanical
obligation as unresolved; no existing criterion binds those obligations.
An obligation's `coverage` record can [stage](board-envelope.md#staged-obligations)
it for candidate generation. The record cites bound hole, keepout and
connector-edge witnesses, plus `outline-containment/1` for declared overhangs.
Its open qualification items keep `require_qualified` refusing final acceptance.
The shared [final guard](board-envelope.md#final-acceptance-guard)
`final_qualification` reads the captured baseline's coverage. While an item is
open, a review that would satisfy its criteria is `incomplete`, a `fab_package`
that would pass is `unsupported`/`unknown` with its exports kept as diagnostics,
and a campaign cannot accept or replay as done. A `connector-edge/1` pass proves
pose only, not part identity or mating and enclosure fit. Staging is admitted
only under a reviewed [source contract](board-envelope.md#source-contracts).
The contract fixes the minimum witnesses and open items, so coverage cannot
drop them. It also requires the telemetry pod's J1 `part-identity/1` witness,
taken from the scope's `parts_subset` check over the captured parts manifest
and footprint, with the complete manifest passing `qualify_manifest`. The
`corner-holes` witnesses are the source holes H1–H4 by identity. J1 board
thickness is not an open item. The contract screens J1 through the
`tht-thickness-fit/1` judge (TE 1.57 mm, K1 sheet 2) against the envelope
nominal, after the declaration passes the `tht-thickness-fit/1` declaration
validator. A refused declaration is `unknown`, never `pass`. The contract
requires `thickness-tolerance` open whenever the screen does not pass, and keeps
the source statement next to a current statement
([J1 thickness](board-envelope.md#source-contracts)). The final
guard verifies the candidate identity and baseline before it reads the scope's
envelope declaration.

`component-height/1` binds the optional envelope `max_component_height_mm` field
and supplies `envelope:component-height/1`. It grades every native footprint from
an explicitly selected verified datasheet height fact or captured resolved STEP
bounds read by native KiCad. Missing sources, unsupported models and unusable
facts are unknown; a part above the inclusive maximum is a defect. It retains
per-reference verdicts without exemptions for DNP or hand-fitted parts. An
optional `digikey_heights` declaration (AGENTEE-254) grades a reference from a
captured DigiKey `Height - Seated (Max)` bound to its saved MPN. A rounded value
straddling the maximum, or one that is absent, unparsed or unbound, is unknown.
An optional `package_map` (AGENTEE-274) makes a DigiKey package that differs
from the footprint a defect, and an unmapped one unknown. See
[component height](board-envelope.md#component-height) for parameter contracts,
source applicability, model transform qualification and AGENTEE-209.

`tht-thickness-fit/1` (AGENTEE-448) adds a captured immutable `thickness_fit`
declaration and supplies `envelope:tht-thickness-fit/1`. It grades each declared
through-hole part against the envelope's NOMINAL board thickness. Each part
cites the manufacturer's recommended nominal thickness, and may add explicit hard
limits or a wider documented fit range. Two tolerances are involved and are
never conflated. The fabricator's finished-thickness tolerance
(`finished_thickness_tolerance`) bounds the board the fab delivers and is never
read as component fit. The component-side fit band uses, per side, the explicit
hard limit if there is one. Otherwise it uses the default +/-10% screening band
around the recommendation (inclusive, exact), widened on that side by a
documented fit range that reaches further. A documented range never narrows the
band; only an explicit hard limit can. Inside passes; the default band
reports `screened (default +/-10% component fit)`. Outside an explicit hard
limit is a defect. Outside the widened screening band is
advisory: unknown, needing feature geometry or evidence, never a rejection. A
mix of defects and unknowns reports unknown, keeps every defect in
`defects` and names each defect in the summary before the unknown reasons. An uncited declaration is refused. A part not bound to one installed PTH footprint
of the declared part number is unknown. Undeclared parts and every other
criterion are unchanged. See
[through-hole thickness fit](board-envelope.md#through-hole-thickness-fit).

## Assembly and test access

`dfa_dft` evaluates one declared criterion per check over the sandboxed
saved-board probe. `connector-orientation/1` checks that each connector faces
its nearest edge, given its declared insertion direction. `testpoint-access/1`
checks that no declared test point lies under another courtyard on the same
side. `testpoint-coverage/1` requires a declared probe point on every rail and
acceptance-measured net, and writes `bringup_plan.json`. See
[assembly and test access](dfa-dft.md) for parameters, the plan schema and
unknown boundaries.

## Qualified passive tolerance endpoints

See [passive corners](passive-corners.md) for the optional `passive_corners`
contract, supported topology, immutable qualification inputs, bounded ensemble,
retained per-corner evidence and empirical-transfer refusal.

## JLC assembly readiness

See [assembly readiness](assembly-readiness.md) for `assembly_readiness`. It
reads its own native XML netlist export after every hierarchy file parses
completely. It binds every placed symbol occurrence of the hierarchy to a
netlist component, and the reverse; a child sheet that kicad-cli silently
drops fails. It then checks each populated component against the captured
catalog: required and any-of BOM properties, R/C rating properties, exact
LCSC binding, three-state stock (zero is a defect; missing, stale or
undeclared freshness is unknown), stock for quantity per board times boards,
and Basic/Preferred/Extended counts. DNP, `exclude_from_bom` and off-board
symbols stay distinct, and an empty schematic is `info`, not `pass`.
A [JLCPCB stock capture](jlc-stock-capture.md) comes from the owner-run
host-side producer, one rate-limited request per LCSC id. When it is the
catalog, the check re-derives every part from the captured raw responses. It
uses the generation receipt's completion time in campaigns (review time for
standalone checks) with a 24-hour maximum age. It fails a capture that is stale
or lacks an observation for a populated id. The [JLC catalog schema](jlc-catalog.md)
requires retained response hashes and requests; unknown keys and LCSC warehouse
sources are refused. Per-part hash verdicts and the evaluation-time witness are
retained as evidence.
An external part without an LCSC code can cite an owner-run
[DigiKey capture](digikey-capture.md) as its identity and purchase-source
evidence. That capture is a host-side preparation input; DigiKey stock is not
JLCPCB stock.
Optional captured `deviations` uses `agent-ee.part-deviations/1`. Valid hand-fit
records require an exact original MPN, purchase source, captured evidence,
stock reason and native `JLCPCB_Assembly=not_assembled`. They appear in
`declared_deviation`, outside stock and tier totals. Positive captured stock
for that MPN or native LCSC ID refuses the exception; NO_LCSC without a
declaration remains a defect. A record with `policy: owner_hand_fit` is
admitted regardless of stock only for an exact identity listed in the scope's
`hand_fit_policy`, a pinned captured owner decision cited by SHA-256, line and
exact quote. The whole cited Markdown unit must name that identity outside
every conditional clause. A conditional or ambiguous statement, such as
NEO-M9N "if unstocked", refuses the run. The identity still needs a fresh,
known-stock catalog row. When the owner text names only a part category ("the
AMPSEAL header"), an optional `selection` chain binds it to the exact identity
through a mandatory, complete chain of pinned captured sources: a reviewed
decision (its document hash, line, quote and category equal an entry of the
closed `REVIEWED_HAND_FIT_SELECTIONS` profile; today only the pod's D-PCB-10), a
parts-manifest entry pinning that footprint, and the part record the entry
names. No decision wording is interpreted: any other decision or revised
decision document, a missing manifest entry or unknown CAD is `unsupported`; a broken link refuses the run; a placed footprint or reference
other than the decision's is a defect. This is an assembly disposition only; it
grants no mechanical-fit or purchase approval. See the
[hand-fit contract](assembly-readiness.md#declared-hand-fit-deviations),
[owner hand-fit policy](assembly-readiness.md#owner-hand-fit-policy) and
[category selection chain](assembly-readiness.md#category-selection-chain).

## Schematic readability lint

`schematic_lint` reads the captured schematic hierarchy, the symbol definitions
embedded in each sheet, and the native XML of an earlier `netlist` check. It
reports overlap, wire, placement-topology, naming, grid and title-block
findings at `info` or `warning`; a profile can raise a rule to `defect`. A
symbol whose definition cannot be resolved is missing coverage, not a pass.
See [schematic lint](schematic-lint.md) for rules, parameters and coverage.

## Part-binding properties

`part_binding` parses the captured root schematic, every captured child sheet
occurrence and the board for `LCSC`, `Manufacturer`, `Partnumber`, `Datasheet`
and typed `PARAM_*` interval or set properties. It launches no tool. A
disagreement between two non-empty schematic and board values, or a malformed
`LCSC` value, is a defect. A missing or one-sided property is `unknown` for that
reference. Copper-layer pads keep a footprint bindable even with `virtual`,
`board_only` or `REF**` markers; the report retains the marker and records
`has_copper_pads`. Marked footprints without copper pads stay exempt, including
bare NPTH holes qualified by the existing native geometry reader. Scaffold holes
carry declared `AgentEE_Mechanical_MountingHole_NPTH` or
`AgentEE_Mechanical_MountingHole_PTH` identities; these confer no catalog binding
or geometry exemption. Empty or missing footprint IDs remain malformed, and
plated holes retain the ordinary copper binding obligation.
Unparseable `PARAM_*` JSON, ambiguous pad layers and an uncaptured child sheet
are `malformed`. See [part binding](part-binding.md) for the schema, markers and
per-reference outcomes. The [Rung-1 parts adapter](rung1-parts.md) projects
replayed `parts/1` catalog identities and normalized ratings into the owned
emitter; native export and forward annotation carry them onto footprints.
Its captured-parts scope declares `parts_subset`, `assembly_readiness` and
`package_consistency`. Unknown stock, ratings or unmapped packages stay unknown.

## JLCPCB fab package

With a counted campaign fab step, all three fab checks require its sealed
package and compare it with fresh sandbox output. Missing step output stays
unknown and can schedule a coordinator fab turn only when all other checks and
review are complete. See [declared fab steps](fab-export.md#declared-fab-steps).

The native `tests.test_fab_stage` fixture mounts a private tool source copy so
parallel host imports cannot change its receipted tree through bytecode caches.
The same module checks that a bytecode addition to an actual tool mount still
refuses adoption (AGENTEE-299); check verdicts and receipt validation are unchanged.

See [fab export](fab-export.md) for the `fab_export` staging layout, the
`agent-ee.fab-manifest/2` manifest, BOM and CPL rules, timestamp normalisation,
v1 refusals and outcome mapping. It requires an earlier `netlist_check`, refuses
completion unless BOM/CPL/board designators agree, and applies no CPL rotation
policy. Board footprint DNP flags decide assembly. A schematic/board DNP
disagreement is recorded in `details.dnp_disagreements` and does not block the
package. An optional declared `assembly_exclusion` rule
(`JLCPCB_Assembly=not_assembled`) leaves populated parts that carry the marker
in both the netlist and the board out of the BOM and CPL. The manifest lists
them in `designators.excluded`, including netlisted test pads whose footprints
carry native BOM/position exclusions. Native schematic exclusions remain
refused. Board-only mechanical items require all three attributes (`board_only`,
`exclude_from_bom`, `exclude_from_pos_files`), no netlist path or component and
no assigned pad net. Their drills remain exported and their sorted references
appear in `designators.mechanical` when nonempty. Consistency replays mechanical
admission and checks any declared mechanical set. Optional `copper_features: ["JP1"]` admits
explicitly declared surface copper features only after both native exclusions,
qualified pad/graphic layers, no model and the copper field allow-list are
verified. Only `Reference`, `Value`, `Footprint`, `Datasheet`, `Description` and
`ki_keywords`, `ki_description`, `ki_fp_filters` and `ki_locked` may contain
nonempty values; all other fields (including other `ki_*` names) must be
empty, whitespace or `~`. This covers footprint, hidden schematic, variant and
every retained native field occurrence. They must be absent from BOM/CPL;
consistency independently replays admission
([copper features](fab-export.md#copper-features)). Malformed field values are
unqualified content. Repeated export refusal findings appear once.
A marked part without the rule, or with
disagreeing sources, is `unsupported`. An optional declared `bom_variant`
(the generator's `--bom-variant`) is validated against the captured
`.kicad_pro` before any kicad-cli command and passed only to the native BOM
export; the CPL stays the default variant, and a variant that changes the
populated set is `unsupported` by name ([BOM variant](fab-export.md#bom-variant)). Launch preflight requires the native `kicad-cli` binary. Empty produced
native files are `failed/unknown`; pre-existing staging or native output
directories are refused before reuse.

Graphics and properties carrying a `(layers ...)` node are refused, including
when a separate `(layer ...)` names an admitted decorative or mask layer.
KiCad can interpret the extra token as plotted copper in either fab gate.

Declared copper features may include courtyard shapes and bounded own-side mask
openings within their pad/copper extent plus a 0.05 mm margin. The
[shape qualification](fab-export.md#copper-features) lists the supported geometry
and fail-closed limits; courtyard size never enlarges this mask envelope.

[`fab_consistency`](fab-consistency.md) consumes that sealed export, its netlist
and an earlier geometry probe. It checks plot/layer and PTH/NPTH inventory,
BOM/CPL/assembled-board reference parity (re-deriving rule exclusions from the
sealed netlist and the captured board), schematic/board DNP disagreement
(`designator_parity`), LCSC/footprint conflicts, CPL side,
captured/embedded 3D models, stock-library models through the optional
`model_libraries` map of captured pinned receipts, and recorded export-log
diagnostics. CPL anchors
must agree with exact native `position_nm` within 0.01 mm per axis, using
absolute board X and negative board Y on either side. Missing exact positions
or a missing sealed log is unknown coverage; broken hashes or malformed CSV
are `malformed/unknown`. Package disagreements are `ok/defect` and qualify only
through a sealed requirement/check witness. Rotation correction and DFM are
outside this check.

## JLCPCB CPL rotation

`cpl_rotation` takes `export_check`, `geometry_check`, a captured `policy`,
captured `supplier_footprints` provenance records and `source_tolerance_nm`.
It writes a JLC CPL whose rows are native pad-box midpoints, and it applies the
versioned policy. Every rotation is cross-checked against the pin-1 frame of
the captured supplier footprint, which detects quarter-turn offsets,
reflections and diode polarity. Each row appears in exactly one of
`applied_corrections` or `unmatched_footprints`. Missing geometry or a
duplicate designator never falls back to the anchor. The status stays
`PREVIEW_REQUIRED`. The generator fab stage runs it after `fab_export`, and
both rung-1 scopes declare it when `--cpl-inputs` captured a policy and supplier
set; their derived package manifest records the corrections and policy digest.
Missing policy or supplier coverage fails closed, and a status outside
`fab_package.DONE_ROTATION_STATUSES` prevents `done`. `PREVIEW_REQUIRED` is done
only as an operator obligation (AGENTEE-151): the records list every corrected
CPL designator with its side as `preview_obligations`, to confirm in the JLCPCB
placement preview before ordering. See
[CPL rotation](cpl-rotation.md#generator-fab-integration).

## JLCPCB cost estimate

`jlc_cost_estimate` takes `export_check`, a captured `catalog`, a captured
versioned `pricing_table` (`agent-ee.jlc-pricing/1`), `assembly_tier` and
`boards`. It reports PCB, assembly and component cost line items, each citing
its table entry or catalog row, plus unique Basic, Preferred, Extended and
unknown-class part counts. A part without snapshot class flags, or absent from
the snapshot, is counted `unknown`, never Basic. Its outcome is always `info`,
or `unknown` when `pcb_summary.json` is missing or unusable. Scope/3 declares
no predicate, so it can be mapped only to an informational requirement. The
report records the table version, capture date and staleness, and the input
hashes. A `thru_hole` pad with a native heatsink or mechanical property that
has its entire drill and annulus provably contained in same-numbered `smd`
copper is excluded and recorded in `tht_excluded`. Containment uses exact
integer nanometres, including the boundary; unsupported geometry stays counted.
Excluding these vias from THT joint counts is an unconfirmed pricing assumption,
not verified against a JLCPCB quote. Other `thru_hole` pads that may be vias are
still counted, and are reported in `tht_unclassified`. See
[JLCPCB cost estimate](jlc-cost-estimate.md).

## Scaffold preparation

The [stage-ordered driver](rung1-stages.md) grades each stage against retained
review sources. Later design adoptions leave earlier reviews verifiable;
changed or missing retained inputs still fail ordinary `agent-ee verify`.

[Empty-candidate scaffolding](scaffold.md) prepares inputs for these checks; its
receipt is provenance and cannot satisfy a requirement. `scaffold --scope`
preserves the explicit planning scope's checks and baseline. `scaffold --plan`
derives `erc`, `netlist`, `parts_subset`, `drc` and `fab_profile` checks, their
mappings and the project rules from the envelope, profile and parts manifest.
With captured `net_constraints` and `netclass_qualification` plan paths it also
emits per-net classes and width rules through the shared net-constraint emitter,
refuses values below the fab minimums, and derives `netclass_completeness` over
the output project. This presence check maps to the native requirement and runs
in the placement stage, before DRC. Native ERC/DRC run through the
ordinary `review` command. Staged absence is a coordinator defect only after a
successful native presence observation; hierarchical presence and uncovered
production envelope obligations continue to fail closed.

[Forward annotation](forward-annotation.md#evidence) preserves existing UUIDs
when adding hidden fields to retained fixed footprints. AGENTEE-215's native
scaffold regression compares two runs from identical pre-annotation input bytes
and checks UUID preservation, field visibility and pad layers after separate
process reloads. This is a writer regression control, not an acceptance check.

The [qualified rung-1 driver](rung1-qualified.md#empty-scaffold) runs its
scaffold step before the campaign and binds the after-tree to the first
capture with a pre-receipt. Its symbol subsets and optional package map
are prepared before that step; compiling the scope does not write inputs.

[`fab_roundtrip`](fab-roundtrip.md), with exactly `{export_check}`, separately
compares every enabled layer, round PTH/NPTH drill histograms, copper pad/via
flash counts, flashed pad extents (aperture macros evaluated exactly), Conductor
track/arc draw counts, zone-fill region counts and outline bounds within 1 µm.
It consumes earlier sealed export and geometry evidence. Missing layers/files and
count/extent/bound mismatches are named defects. Inch units and unknown Gerber
commands are malformed. Constructs outside KiCad 10's set (G75 arcs, G36/G37
regions, macro primitives 1/4/5/20/21) are unknown with a 1-based source line.
Artwork rendering is outside coverage.

## Captured block adaptation

`block_provenance` takes `instance` (a captured `agent-ee.block-instance/1` file)
and `netlist_check` (an earlier board-native netlist check). Scope/2 and scope/3
freeze the record and all source-block files. The copied child stays mutable.
A native semantic report lists component and net changes as information, while
missing or rebound boundary ports produce defects. Source run results never
qualify the board. See [block adaptation](block-adaptation.md) for scaffold
inputs, requirement mapping, boundary probes, retained evidence and coverage
limits.

Source blocks can retain [pinned external citations](reference-block-interface.md#external-source-citations)
for requirement and contract context without copying planning documents. Block
validation verifies their commit/path/blob and SHA-256 offline; unavailable Git
objects keep the block unverified. These citations supply no board-check credit.

[`fab_package`](fab-package.md) is the generation done gate (AGENTEE-94). Params
require `{netlist_check}` with optional `bom_variant`, `assembly_exclusion`,
`copper_features`, `probe_manifest` (delegated to export) and `model_libraries` (delegated to the
consistency gate). Model receipts and probe sources are immutable; probes require
an earlier matching `parts_subset` check. Only bound [`jlc_dfm`](jlc-dfm.md) checks may follow
it. An earlier `geometry` check is required. It runs the same export and consistency code on its
own staging tree and, on `pass`, records `details.artifacts`
(`agent-ee.fab-artifacts/1`: gerbers.zip, every drill file, bom.csv, cpl.csv and
manifest.json by sha256, bound to the candidate id and board digest). A missing,
empty or unparseable export file is `failed`, `malformed` or `unsupported` with
outcome `unknown`, or a gate `defect`; never `pass`. A scope with a `generation`
marker is refused at capture unless this check is last and an acceptance
requirement maps to it. When the scope declares one `cpl_rotation` check
(AGENTEE-126), the gate requires its passing, complete result bound to this
candidate, board and native `cpl.csv`, seals the corrected `work/CPL/cpl/cpl.csv`
in place of the native CPL, and records `rotation` (status `ok` or
`PREVIEW_REQUIRED`, policy sha256, report digest and, for `PREVIEW_REQUIRED`,
`preview_obligations`: every corrected CPL designator with its side, bound to
the policy sha256) in `details` and the artifact record; a correction defect is
a gate `defect`, any other non-pass is `unsupported`. Without one, `rotation` is
`{status: not_declared}`. Done predicates admit only
`fab_package.DONE_ROTATION_STATUSES` (`not_declared`, `ok`, `PREVIEW_REQUIRED`),
`PREVIEW_REQUIRED` only with its recomputed obligations (AGENTEE-151); see
[CPL rotation in the done gate](fab-package.md#cpl-rotation) and
[preview obligations](fab-package.md#preview-obligations-agentee-151).

## Datasheet pin electrical contracts

[`pin_contracts`](pin-contracts.md) takes exactly `manifest`, `facts_check`,
`netlist_check`, `ground_nets`, `power_nets`, `rails` and `classes`. It consumes
captured per-part contract blobs from the owned parts manifest, verified
page-cited facts and retained native connectivity. Voltage compatibility,
must-connect, no-connect, decoupling, pull-up, BOOT-to-SW capacitor and feedback
divider presence are separate predicates. Every physical instance is evaluated
independently. Missing, ambiguous or inapplicable facts and uncovered pins remain
unknown, including a manufacturer mismatch. Scope/2 and scope/3 freeze the
complete contract/fact/parts dependency closure. See the feature page for the
exact schema, evidence and limits.

## Saved supplier DFM report

[`jlc_dfm`](jlc-dfm.md) binds an owner-saved source report and normalized findings
to an earlier `fab_package` check. Params are exactly `{fab_check, report}`. Both
report files are immutable baseline inputs. Missing or mismatched evidence is
unknown; error findings are defects, warnings are advisory. Parsing runs in the
networkless executor; the owner performs the upload outside agent-ee.

## Manufactured copper probe identity

[Probe features](probe-features.md) extend the existing `parts_subset`, `part_binding` and `fab_export` checks without new predicates. `part_binding` accepts optional `probe_manifest` plus `netlist_check`; `fab_export` accepts optional `probe_manifest`. Both bind the complete source closure as immutable inputs. Required physical membership, pin/net identity, native schematic/board flags and saved pad geometry remain checked while qualified probes are absent from assembly CSV files. Marker-only, missing, swapped and byte-changed features are refused. New probe
properties carry versioned base64 rather than inline JSON so KiBot exports can
read them. Exact legacy identities produce a non-pass migration finding in
admission checks (including `fab_consistency`); only forward-annotation planning
allows them, and apply requires current encoded properties (AGENTEE-283).
Forward annotation migrates validated legacy source or retained-board fields to
canonical encoding and records exact changes in its plan and successful receipt
(AGENTEE-302). Final board validation requires canonical fields; admission checks
still refuse a legacy schematic, which must be regenerated before native parity.
Within one native XML component, marker field/property text must agree exactly;
mixed legacy/canonical encodings produce `probe_feature_mismatch` (AGENTEE-308).
Forward annotation alone does not establish immutable source approval. A native
integration regression applies edited declaration-bound markers, then requires
`part_binding` to report a probe binding defect and `fab_export` to refuse as
`malformed`/`unknown`, without a fab manifest. `parts_subset` checks the schematic
and native inventory, so an unchanged schematic still passes that check. An
edited schematic marker fails membership with `unavailable_captured_evidence`.
See the [migration and external KiBot regression](probe-features.md#migrating-legacy-inline-json-properties).

Probe admission also requires an earlier, passing `parts_subset` check using the same manifest and native netlist. Capabilities enforce that ordering; runtime consumers verify its sealed report and exact source hashes.

## Block-first integration

The [block-first coordinator gate](block-first.md) uses existing `erc`, `netlist`
and exact topology `netlist_parity` checks for each child sheet in its own
captured harness. Additional declared checks, including pin contracts, retain
their ordinary semantics. All block snapshots and sealed checks are revalidated
before integrated checks run. Missing, defective or stale block evidence refuses
publication of the proposed hierarchy; interface changes require a new block run.
This adds no check kind and supplies no reviewer or campaign acceptance.

## `schematic_render` — optional reviewer images

The [schematic render reference](schematic-renders.md) describes deterministic
PNG evidence with a 20 mm grid, native PDF/Poppler rasterisation and renderer-bound
per-sheet changed-pixel percentages and bounding boxes. The only parameter is
optional `baseline`, a captured directory containing a prior render manifest and
its artifacts. Those inputs are immutable verification dependencies. The check
always produces information or unknown coverage, never a pass or defect, and
cannot satisfy scope/3 acceptance requirements. Failed rendering retains missing
visual evidence without preventing review; malformed critique remains unusable.

## Captured datasheet source selection

`parts_subset` qualification also enforces any declared
[datasheet source selection](binding-fill.md#captured-datasheet-source-selection)
(AGENTEE-237). Rejected PDF hashes, missing or ambiguous selections, and bindings
that differ from the selected acquisition refuse before native projection.
The selection and acquisition dependencies are immutable; no new check kind or
predicate is introduced. Source selection alone does not verify electrical ratings.
The host-side [datasheet source cascade](datasheet-cascade.md) (AGENTEE-248)
produces such a selection, including an optional `failed` list. No check runs it,
and checks see only its captured outputs.

## Native net-name encoding (AGENTEE-220)

The board producers apply [native net-name encoding](forward-annotation.md#native-net-name-encoding-agentee-220)
to XML names before writing PCB nets (AGENTEE-220); the placer's netlist binding
and probe pad validation compare saved pads with the same encoding. This does not normalize the
XML inventory used by `netlist_parity`, or waive native DRC schematic parity.

## Source-derived passive ratings

`assembly_readiness` validates any `AGENT_EE_Ratings` provenance field written by
[binding fill](binding-fill.md#source-derived-passive-ratings) (AGENTEE-233).
A malformed record or a rating that differs from it is an `invalid_rating_provenance`
defect. Absent ratings remain `missing_rating_property` defects;
`require_passive_ratings` still defaults to true. No new check kind is added.
The optional `rating_provenance` parameter (AGENTEE-242) adds the
`assembly:rating-provenance` predicate. It names the captured binding-fill stage
receipt, its pinned sha256 and its `binding-fill.json` artifact. Each
`record_sha256` must then resolve to the receipted record, or the check reports
`invalid_rating_provenance`. Unavailable provenance is `unknown`, and so is an
`AGENT_EE_Ratings` field when no `rating_provenance` is declared
(`undeclared_rating_provenance`). Instances of a reused child sheet resolve by
hierarchical path (`/A/R101`, AGENTEE-252); an unrepresented instance stays
`invalid_rating_provenance`. See
[receipt-bound rating provenance](assembly-readiness.md#receipt-bound-rating-provenance).
Campaigns derive the declaration from the adopted receipt through the
[coordinator rating binding](stage-receipts.md#coordinator-rating-provenance) (AGENTEE-256).

## DigiKey operating temperature

`assembly_readiness` takes an optional `operating_temperature` parameter
(AGENTEE-254) with a declared ambient `min_c`/`max_c` and captured DigiKey
records bound to references. It adds the `assembly:operating-temperature`
predicate. A single unqualified or `TA` DigiKey range that contains the ambient
passes; one that does not is an `operating_temperature_out_of_range` defect.
Junction/case-only, absent, unparsed or unbound values are unknown, as is an
unpinned or uncaptured capture. The predicate is board-wide (AGENTEE-274): a
populated component without a `parts` binding makes it unknown
(`operating_temperature_unlisted`). Under the owner ruling of 2026-10-20,
AGENTEE-310 replaces exemptions with `reduced_acceptance`: a per-reference
accepted `min_c`/`max_c` range and owner citation in a dedicated captured
`operating-temperature-acceptances.txt`. Each ASCII line has the exact form
`U1 is accepted for operating temperature -40 to 85 degC.` The same DigiKey
binding and ambient predicate judge this range. A rating below the accepted
limit is a defect, never an exemption. A covering rating also requires captured,
hash-pinned `thermal_evidence` naming that reference in
`operating-temperature-thermal.txt`, with a line such as
`U1 expected operating temperature is -20 to 80 degC.` The expected range must
fit the rating; exceeding it is a defect, missing validation is unknown.
Both whole files must contain only canonical ASCII lines with LF separators,
ordered decimal ranges and one reference per line, unique across the file.
Citations quote the exact complete line. One invalid line makes all citations
from that file unknown. Legacy `exemptions`, including the old canonical
`is exempt from operating-temperature verification.` form, return unknown
with a migration message. A distinct `delegated_acceptance` record
(`authority: delegated_engineering`) covers selection exceptions decided by an
agent under a repository delegation. It binds a recorded JSON decision (`id`,
source, sha256, line, quote) and an `AGENTS.md` delegation (`kind:
repository_agents_md`, repository, owner commit, source, sha256, line, quote).
Authority comes only from grants pinned in agent-ee by owner commit and item
hash (`OWNER_GRANTS`), and from decisions registered there by member hash with
their reference, range and exact MPN and manufacturer (`DELEGATED_DECISIONS`).
A captured grant or decision that differs from the pinned bytes, a whole
grant or decision file that is not an approved owner snapshot (full-file
SHA-256), an unregistered decision or a different evaluated part is refused or
unknown. So the exact item placed in a newly composed, fenced, conditional or
rescoped context never authorizes.
Owner, withdrawn or non-autonomous decisions, `authority: owner`, unrecognized
kinds and self-citing or temperature-file sources are refused. Ratings and thermal
evidence are judged exactly as for owner acceptance
([details](assembly-readiness.md#delegated-engineering-acceptance)).
Native KiCad files, legacy design files, library tables and mutable deviations
remain refused as citation sources. An optional
`package_map` compares the DigiKey package with the footprint. A mismatch is a
`digikey_package_mismatch` defect, and an unmapped name is unknown. DigiKey
stock never counts as JLCPCB stock. See
[DigiKey operating temperature](assembly-readiness.md#digikey-operating-temperature).

## Saved native net-class assignment

`net_class_assignment` reloads a saved board and sibling project through pcbnew.
Parameters are captured `project`, `record` and `constraints` paths; see
[net constraints](net-constraints.md#campaign-application-and-native-assignment-check-agentee-232).
The check verifies each declared class, each exact net's effective assignment,
minimum width and clearance. Missing or unassigned nets and classes are defects;
unavailable native evidence stays unknown. Native KiCad pattern matching also
refuses declared-class patterns matching extra undeclared board nets, naming
each extra net, and legacy backslash-escaped slashes. This is independent of the
record hash comparison, including standalone scopes. Literal `/POD_GNSS_RF` is
accepted. The native report retains per-pattern matched net names.
Project and rules hashes must agree
with the record. The constraint document, record, project and emitted rules are
immutable baseline policy. Capability predicate: `netclass:effective-assignment`.
Impedance stays `recorded_not_enforced`; native DRC checks actual copper.

## Generated silkscreen geometry

The [packed-board producer policy](scaffold.md#packed-board-silkscreen-policy-agentee-276)
puts mounting-hole and dense chip references on fabrication layers and moves
silkscreen from envelope-declared overhanging fixed footprints there. The placer
reserves the native IMU axes text box on its own side. These are recorded design
geometry choices, not DRC waivers: all remaining silkscreen warnings and errors
retain the ordinary DRC verdict. Regeneration and fresh captured checks are
required for an older candidate.

## Historical datasheet acquisition

The existing `datasheet_facts` check accepts the offline historical
[`datasheet-capture/2` import](datasheet-capture.md#offline-historical-import-agentee-306).
It freezes the pinned source note along with the PDF and records the acquisition
method and unknown redirect provenance in its report and details. A verified
citation proves text occurrence only; it supplies no redirect-provenance or
release-policy credit. Live `datasheet-capture/1` validation remains unchanged.

The fab, `rung1_campaign`, `rung1_qualified`, `rung1_stages`, pod scaffold/constraint, envelope-content, schematic emission/annotation, `add_planes` and `binding_fill` step builders use [content-addressed source snapshots](stage-receipts.md#python-tool-source-snapshots) to isolate host bytecode writes. Receipt identity and mutation refusal checks still cover every mounted file. `tests.test_tool_source` discovers source-mount constructors across `src/agent_ee` and exercises every discovered builder against an original source fixture. It also proves prune-after-verified-reuse retention and exclusion of a concurrent prune during a real sandboxed step.

AGENTEE-327/329 host validation refuses mounts equal to, containing, or inside the
package `src` tree, including resolved parent and symlink aliases, with
`live_package_source:<at>` before execution. Retained receipt replay stays
host-independent. `tests.test_stage_receipts.StepDeclarationTests` covers refusal
and snapshot admission; `tests.test_tool_source` validates all registered step
builders against this runtime guard. AGENTEE-329 also refuses bind aliases of
`src`, its ancestors and descendants by device/inode (including ancestors of
the mount source). It binds opened, identity-checked descriptors using bwrap
`--ro-bind-fd` and verifies the actual mounted identities inside the sandbox
before executing either command, covering bwrap versions that canonicalize fd
sources to pathnames. Directory
sources containing hard links into live `src` refuse with `live_package_source:<at>`
(AGENTEE-349), including persistent links planted after inventory, by re-inventorying
the held descriptor before launches and after execution, and links planted and
removed during a run, detected by live entries' status-change times (AGENTEE-357),
with a missing baseline entry refused and a whole-second clock fence before launch so a
same-tick link cannot keep its time (AGENTEE-374). Mount, source and live tree walks are
descriptor-relative; a component replaced mid-walk refuses as `mount_source_changed:<at>`.
New receipts are `agent-ee.stage-receipt/3`; a `/1` receipt with a nested source whose
identity has one entry (possibly an escaped link-only record), or a missing or non-integer count, refuses on replay as
`legacy_source_identity:<source>` (AGENTEE-374). `/3` additionally binds artifact
file permissions and directory entries, including empty directories, and
recomputes them before adoption (AGENTEE-465). Historical `/1` and `/2` receipts
remain inspectable under their existing validation rules, but those with declared
artifacts must be rerun before adoption because they lack this identity.
Before/after identity of
declared mounts reads through held descriptors; post-run source replacement or
observation lookup failure raises typed `mount_source_changed:<at>`. Absolute and
upward-escaping mount symlinks retain link identity without reading host targets;
an executable through such a link has an unknown digest and is unadoptable, and a
declared `source` through one refuses as `source_link_escape:<source>`, including
from the same resolution in either strict run observation, leaving no receipt directory
(owner access is restored on each directory held by a no-follow `O_PATH` descriptor before it
is read, iteratively at any depth, even after the tool made directories unwritable; an
unremovable directory raises typed `cleanup_failed:<receipt dir>` over the refusal). The
executable is resolved and hashed through component-wise no-follow opens below
the held descriptor. Only
fd-form receipts carry the mount-guard observation; historical path-form replay
retains its lower assurance. Directory sources containing submounts are
conservatively refused, including same-device
bind mounts. The three qualified canonical routing helper files and the
mount-namespace authority limit are recorded in
[stage receipts](stage-receipts.md#python-tool-source-snapshots).

Acquired-PDF receipts from `datasheet_facts`, `pin_map` and `pin_contracts` retain
an `acquisitions` map with acquisition method and redirect provenance, including
in capture-time facts/2 capability descriptors. This metadata preserves the
existing check verdicts. See [consumer receipt provenance](datasheet-capture.md#consumer-receipt-provenance-agentee-311)
for live `/1` and historical `/2` semantics and the offline preparation consumers.

## Custom-pad placement preservation

The [disposable placer](placer.md#native-custom-pads-agentee-307) admits only
native-qualified custom pads, reusing the footprint index's bounded syntax.
Its save/reload gate preserves complete primitives, copper extents and pad/net
identity. Unsupported geometry remains unknown; courtyard packing supplies no
copper-clearance evidence. This adds no registered check or acceptance predicate.

AGENTEE-307 also qualifies writer-requested side flips of custom-pad footprints
and their footprint arcs through a sandboxed native reflection and save/reload.
[The placement write scope](placement-adapter.md#custom-pad-side-flips-agentee-307)
requires exact saved primitives, arc nodes and native merged-copper evidence;
mutation or missing native output refuses admission. This changes transform
admission only; check registration and placement grading remain unchanged.

AGENTEE-366 gates [placer candidates on copper extent](placer.md#copper-extent-gate-agentee-366)
as well as courtyard: pad copper beyond a part's courtyards must stay in the
outline and out of same-side footprint keep-outs during packing, block
arrangement and targeted plans. That copper is also held a structural setback
of the board `m_MaxError` plus 50 nm inside the outline, so it composes with the
AGENTEE-331 publication gate. The setback is typed unknown when the facts lack
`max_error_nm`, for in-courtyard copper as well. With the fact present, parts whose copper lies in their courtyards plan as before. This adds no registered check or acceptance predicate.
AGENTEE-418 adds [peer courtyards and pad keep-outs](placer.md#peer-courtyards-and-pad-keep-outs-agentee-418)
to that gate. Copper outside a courtyard may not meet a peer's courtyard or
such copper. Lands may not enter a rule area forbidding pads on one of their
copper layers, inner layers included. A fixed land in one is an input defect
(`in_pad_keepout`). A footprint keep-out on inner layers only binds no
footprint, matching native DRC. This adds no registered check either.

AGENTEE-371 reports [fixed parts' copper](placer.md#fixed-part-input-copper-agentee-371)
outside the outline, within the setback of it, or in a keep-out as a typed
input defect up front: `fixed_input_copper`, `outcome=defect`, with findings
naming the part, pad, copper bounds and outline or keep-out. An unknown setback
is `unsupported_geometry`, `outcome=unknown`. The fixed part is never moved.
A reference's declared `fixed_overhangs` exit edge waives the setback for its
own copper on that edge only (ruling 371c), reported as `fixed_overhang_copper`
input facts; copper past the outline or in a keep-out still refuses. A
failed copper-aligned pass is recorded as `search.copper_aligned_attempt`, a
diagnostic only. Every fixed part is checked whatever the `seed` (ruling 371d);
only an arranged block member at its exact admitted pose is excluded. This adds
no registered check or acceptance predicate.

## Copper feature consumers (AGENTEE-315)

AGENTEE-315 cross-checks `fab_export` and `fab_package` copper declarations
against every parts manifest using the same native netlist. Exact copper
reference equality and earlier passing, source-bound `parts_subset` evidence
are required when both contracts declare copper. Interval catalog mappings
exclude copper; fixed-footprint scaffolding and purchased-identity helpers
refuse copper with typed errors. See [approved parts](approved-parts.md#declared-on-board-copper-features).

Pin contracts retain copper instances as unsupported electrical evidence, with
an explicit reason instead of reading manufacturer or MPN fields.

## Placer through-hole publication gate

The [AGENTEE-318 placer gate](placer.md#through-hole-reservations-and-native-publication-gate-agentee-318)
compares native baseline and candidate DRC item identities before publication.
AGENTEE-325 binds collision identity to saved geometry and footprint transforms,
preserving rigid intra-footprint baseline findings. New `shorting_items`,
`clearance`, `pth_inside_courtyard` and inter-footprint `solder_mask_bridge`
findings refuse the proposal; baseline and intra-footprint mask bridges remain
reported and non-blocking. Cross-footprint shared native item KIIDs refuse
comparison. Any `.kicad_dru` rule-level `(severity ignore)` refuses publication,
including ignores of unrelated categories, because native `ignored_checks`
does not expose these rules. Added silk findings are recorded separately. Native
through-hole reservations constrain both packing and targeted plans without changing check
registration, verdict schemas or coordinator acceptance authority.

AGENTEE-317 marks every pad sharing a custom pad's native UUID unsupported,
including case, brace and dash aliases. Unique noncanonical UUID spellings
retain their previous unknown coverage. Non-object `custom_geometry` and
unqualified `proven` evidence return typed unknown packing coverage, even on
stock pads. Native write-scope controls refuse unsupported source arcs,
thermal connections and holes; reflection diagnostics show the actual
`ValueError` before native console noise. See `tests.test_placer_custom_pads`.

## Pad-level electrical locality

`placement` supports `criterion: electrical-locality/1`, `drc_check` naming the
earlier same-candidate native DRC, and `locality` naming a captured
`agent-ee.electrical-locality/1` document. Its immutable input and capability
predicate are respectively that path and `placement:electrical-locality/1`.
See the [declaration, verdicts and separate packer constraint](placer.md#pad-level-electrical-locality-agentee-322).
Locality documents and copied index snapshots require exact built-in JSON types;
subclasses and other Python objects refuse before schema or net-binding checks
(AGENTEE-356). Legacy paired obligations require supply and return, with
switch/inductor pairs optional. AGENTEE-503 also accepts explicit
`kind: single-leg` with exactly one nonempty `leg`; it never infers this form
from an omitted return. See the [single-leg contract](block-contract.md). The native copper-land centroid metric preserves proved defects
alongside unknowns; missing limits stay unknown; hard enforcement also requires
a citation. Optional captured pad `uuid` selectors
must also match the declared reference, number and net; unselected duplicate
pad numbers refuse. Native copper offsets participate in the distance, and
unqualified centroid geometry stays unknown. Coordinator packing derives its
obligations from the same immutable locality file, recording its raw hash in
`electrical_locality_binding`; the check judgment records `locality: {path,
sha256}` (AGENTEE-328). The engineer declares limits and citation locators;
the check never infers an electrical maximum from manufacturer spacing or prose.
Origin-group success supplies no electrical-locality acceptance.
AGENTEE-337 adds [numeric provenance and manufacturing floors](placer.md#locality-provenance-agentee-337):
legacy or unsourced bounds report defect-level `unsourced` advisories with `info`
when no hard defect/unknown exists; these never block publication. Datasheet
numeric/scale derivations and owner decision references retain hard constraints.
A well-formed reference to present source text with a SHA-256 mismatch instead
reports `source_record_integrity` unknown, reason `source record sha256 mismatch`
(AGENTEE-402 ruling 402a); packing and block admission refuse it. Absent records
and malformed hashes remain advisory/unsourced, a known boundary.
Captured JLCPCB different-net PCB clearance computes a necessary copper floor and
refuses hard sourced or explicit manufacturer bounds below it as `locality_infeasible`.
Assembly package spacing produces advisory estimates only, because native lands
do not establish physical body/lead extents. The exact
whole-footprint manufacturing minimum remains unqualified; unknown package
combinations never inherit an invented value.

AGENTEE-337 locality info summaries report total and exceeded advisory counts.
Joint-cluster bounds prune only with hard provenance. Missing advisory citations
and geometry-unknown manufacturer floors stay advisory; absent numeric maxima
and captured manufacturer-record integrity failures retain unknown coverage.
A sourced bound below the necessary copper floor is `locality_infeasible`.
Block candidates preserve source records and accept truthful advisory `info`
through production, validation and independent admission regrading; hard defects
and unknown coverage still refuse. Deferred block-locality rows retain those
advisories and report `info` for above-bound advisory measurements
(AGENTEE-402). See [blocks](blocks.md).
AGENTEE-401 requires explicit floor and provenance records in every stored block
locality measurement. Structural validation refuses missing or malformed records;
board admission still independently regrades locality before consumers trust it.

Block identity regressions (AGENTEE-373) exercise saved native custom-pad copper
on both sides at all quarter turns, contour start/winding relabeling, and all 16
B-side source/target [library materializations](block-library.md#materialization).
Moved copper vertices and changed courtyards retain their identity refusals.
These add coverage without changing any registered check or verdict.
AGENTEE-429 adds an achiral native fixture so F/B identity separation is asserted
by side alone, `canonical_ring` cases beyond relabeling, and a native B-side flip
in the materialization matrix; verdicts are unchanged.

AGENTEE-330 permits different `supply.from` and `return.from` references,
retaining the same capacitor destination and exact pad/net/UUID validation.
AGENTEE-334 adds `kind: buck-output-loop` with explicit `ic`, `inductor`, and
`return_net` declarations. Supply starts at that inductor; return starts at
that IC. Optional switch starts at the IC and ends at the same inductor.
Return endpoints use the declared return net, distinct from supply. AGENTEE-341
requires both switch endpoint nets to differ from return and both supply endpoint
nets. Distinct-source obligations require disjoint supply/return endpoint net
sets even without a kind. Inconsistent bindings refuse validation, including
direct `PlacementIndex` construction (AGENTEE-341). It deep-copies the document
first, copies its exact JSON values into fresh owned containers, validates that
snapshot, then indexes it (AGENTEE-356). Cyclic containers raise the typed JSON
`ValueError`. Containers nested deeper than 64 levels refuse with `locality
document nesting exceeds 64 levels` at the index (checked before copying,
including subclass storage), `validate()` and `grade()`, never `RecursionError`.
If the copy itself recurses too deeply (a deep value inserted concurrently after
the pre-walk, or a deep non-JSON tuple), the index raises the same depth error.
The captured locality file is decoded by `electrical_locality.load_json`, so
decoder recursion on deeply nested JSON text refuses with that error too: the
check reports `malformed` (`electrical locality refused: ...`) and
`placement_adapter.bind_locality` raises it as a `ValueError` (AGENTEE-363).
`validate()` checks a fresh snapshot (and still returns the caller's object);
`grade()` grades and reports (`declaration`) only its own validated snapshot and
never rereads the caller's document (AGENTEE-363). Its public pairs and endpoint maps are read-only at every level,
including endpoint fields.
Later caller mutations cannot alter its endpoint identities or cached keys
(AGENTEE-352/356). Untyped
same-source obligations retain their existing verdicts; disjoint untyped nets
alone do not establish component roles or detect swapped supply/return roles.
Both legs remain jointly graded and constrained; missing bounds stay unknown. See
[buck output-capacitor loops](placer.md#buck-output-capacitor-loops-agentee-330)
for the schema, retained owned evidence and committed pod packet locator.

Packing uses an [incremental locality index and bounded targets](placer.md#incremental-locality-search-and-diagnostics-agentee-332).
Multi-member components use [joint cluster search](placer.md#joint-locality-clusters-agentee-335)
after per-part search fails, with tightest-limit-first peers and only the remaining
step/time budget. Joint refusals retain the legacy typed failure and cluster
diagnostics in `search.joint_cluster_attempt`; only regraded completions replace it.
AGENTEE-347 regressions exercise complete-proposal locality rejection on both
engines and public `pack()`, whole-result legacy preservation under the cluster
guard (including `locality_clusters`, excluding only `joint_cluster_attempt`),
and a seeded 22-case differential including an independently regraded joint
completion (seed 2, case 55).
The per-rotation cap of 256 distinct locality origins refuses with
`packing_budget_exhausted`, `outcome=unknown`, empty placements and reason
`locality target budget exhausted`; it never truncates targets. Runner heartbeats
go to stderr, preserving the single JSON summary on `board_build` stdout.
Heartbeat and partial search results are diagnostics with no acceptance authority;
the full independent locality check still grades every declared role.

## Post-placement silk publication gate (AGENTEE-320)

The [artwork pass](silk-artwork.md) uses native field bounds to search for
position-only Reference/Value relocations after placement. Extra custom fields
need a footprint declaration. Every other text property and all graphics remain
unchanged. Fresh native DRC against the immutable baseline records added
`silk_overlap` and `silk_over_copper`. Silk-only proposals publish with typed
`placement_silkscreen_conflict` open defects, each with `verdict: defect` and
`status: open`, in both `native_drc_comparison.open_defects` and the placement
receipt `open_defects`. Their receipt outcome/verdict is `defect`. This separates
placement feasibility from fab legibility. The coordinator captures validated
helper receipts and artwork under `placement-evidence`, with ordered
`placement_provenance` links in successor scopes (AGENTEE-333). Verification/fab
stages and the coordinator ruling's 18 fab-family kinds refuse open carry with typed field/graphic
identities and source receipts, regardless of declared stage (AGENTEE-344).
This includes DRC, fab profile, DFM geometry, DFA/DFT, assembly readiness, cost,
roundtrip, parts/catalog/binding and land-pattern gates. The placement-stage DRC
projection instead grades exactly carried silk as carried and advances to
routing ([AGENTEE-346](#carried-silk-in-the-placement-projection-agentee-346)). Campaign acceptance
refuses open carry even without a declared downstream gate, including bench-feedback
epoch completion. A clean receipted
closure can survive masked copper track edits only when retained root nodes have
identical raw source bytes and the complete captured project/custom-rule path and
digest inventories remain unchanged, including non-board stems. Semicolons outside
strings and KiCad `#` line comments refuse equivalence. Exempt track spans must
match the positive writer grammar: balanced parentheses, `[A-Za-z0-9_.+-]+`
bare tokens, quoted strings without backslashes, embedded quotes or controls,
and only spaces, tabs, LF or CRLF between tokens. Hashes and semicolons anywhere
in a track span prevent exemption. Other track spans remain in
the raw-byte comparison, including mid-token quotes and multiline strings;
vias, zones and unknown track attributes still require fresh receipt evidence.
The evidence seal reconstructs these refusals; release artifact extraction also refuses open carry.
Uncarried baseline findings retain their existing verdicts. A board published
with open carry has a [title block lineage stamp](silk-artwork.md#board-lineage-stamp-agentee-351)
(AGENTEE-351). The same gates refuse it as unknown when the candidate has no
`placement-evidence` binding every stamp digest, for example after a re-scope
outside the campaign. Captured evidence closes carry only when its claimed
`silk_*` findings equal native DRC recomputed on the captured board in the
sandbox. A mismatch, or a verification that cannot run, is unknown. The seal and
release extraction recheck the witness `work/<check>/placement-lineage.json`.
At release artifact extraction, every board requires a sealed native DRC
witness showing no `silk_*` conflicts and no open carry. Until AGENTEE-348
authenticates imported lineage and stamps, neither waives this witness; open
carry still refuses first with its own typed message. `fab_package`, sealed release verification and campaign completion refuse
missing or unverifiable evidence as unknown. Standalone checks retain their
verdicts; there is no scheduler verdict rewrite. See the
[release contract](silk-artwork.md#board-lineage-stamp-agentee-351). Explicitly
ignored `silk_*` categories and missing tool coverage refuse publication. Other
footprints' same-side courtyard interiors reserve space for assembled bodies.
Side-flipped footprints' fields remain unchanged with typed conflicts. Source
project/rule bytes are restored after the native save. Placement-added
`silk_edge_clearance` remains reported with its existing grading. This
producer gate does not alter registered DRC verdicts or waive baseline defects.

Masked-track edits on nets whose names contain `#` or `;` (for example `CS#`
or `A;B`) never receive the exemption, even when KiCad writes the net name as a
quoted string. Such edits reopen closure and require a fresh receipt. This is a
conservative liveness bound of the writer grammar (AGENTEE-360).
A segment or arc refuses comparison with `ValueError` when a child is
list-headed (for example `((x))`) or when a value of a `start`, `end`, `mid`,
`width`, `layer`, `net`, `uuid` or `locked` child is a nested list (for example
`(start (1) 2)`), on any layer. Nested lists inside unknown track children are
not refused: those children stay in the exact comparison. The coordinator
retains the carried defect instead of letting an untyped error escape
(AGENTEE-364).

## Design-rule provenance (AGENTEE-331)

The [scaffold constraint-source import](scaffold.md#declared-design-rule-sources-agentee-331)
records every emitted numeric setting's class and citation in its receipt and
immutable project binding. Native DRC classifies findings against that binding
and captured custom rules. `default` or unresolved numeric limits remain reported
advisories. Owner, manufacturer, datasheet and engineering limits are hard only
when their citation resolves under the AGENTEE-348 citation model.
The DRC check retains the raw `drc.json`, raw counts, waiver keys and locators;
`drc-constraints.json` adds the source inventory and classified violations.
`details.advisory_findings_count`, `constraint_classes` and `graded_unwaived` separate numeric advisories
from graded issues without changing connectivity or parity treatment.
A native tool failure or missing/malformed output cannot become a pass.

AGENTEE-348 G4 gives both consumers one citation model (`source_authority`).
An owner limit is hard only at a pinned owner commit whose bytes match. A
manufacturer limit must cite a hash-pinned capture. A datasheet limit must cite
a captured record by hash, locator and quote. An engineering limit may use any
of these citations. A free-text label, an agent-named owner, or an uncommitted,
unresolvable or stale commit is advisory. It keeps its class label, emitted
value and `unsourced_reason`, and stays in the geometry inventory. See the
[citation model](scaffold.md#declared-design-rule-sources-agentee-331). After
review H1–H5, each citation binds the governed values exactly. A captured
`.kicad_dru` rule gets no owner authority from its file; it needs a resolving
declaration of its own payload. A generated custom rule qualifies against its
emitted constraints. A builtin manufacturer capture qualifies only the numeric
fact it records.

AGENTEE-425 adds the [unsourced geometry inventory](scaffold.md#unsourced-geometry-inventory-agentee-425)
to scaffold receipts and project bindings; board-build refreshes it on template
merge. It lists retained unsourced settings and potential router/zone-filler
consumers, including generated native constraints. Value-matched owner,
manufacturer and datasheet settings with a resolving citation are excluded. This disclosure changes no
native value, check registration or schema version. AGENTEE-437 requires a
nonempty citation for those exclusions and discloses shadowed generated forms
by position without changing name-based owner DRC authority. Before grading,
`finding_sources` re-derives and compares the bound inventory with the saved
project and native rules. Missing, stale or edited inventory refuses DRC
evaluation as unknown, even with no native violations. Valid inventories retain
their existing verdicts. Under ruling 437a, a bound emission snapshot and its
hash anchor the original inventory; native-save additions are equivalent only
when their values match the closed, cited KiCad 10.0.7 table. Removed/changed
settings and unknown or non-default additions remain stale. Projects without a
provenance binding remain supported.

The placement comparison retains classified baseline and candidate findings,
including baseline silk/text, and derives added numeric blockers from those
classifications. Structural shorts, PTH courtyard intrusion and introduced
inter-footprint mask bridges retain engineering citations and their existing
refusal behavior. Existing courtyard qualification and ignored-category refusals
remain. Review packets and prompts carry limit classes and citations. Owner
custom-rule bytes remain immutable, including when generated net rules are
composed with them. This metadata adds no capability or acceptance predicate.

Post-placement artwork reruns bind the same source inventory to every native
comparison. Classified baseline and final findings retain their citations across
native saves; advisory numeric additions remain reported without becoming
relocation regressions. The existing carried silk legibility contract is unchanged.

The actual numeric measurement is checked against every applicable sourced
minimum, so a larger default cannot mask a violated manufacturer floor.
`constraint_provenance.limits` lists the violated sourced settings, and the
strictest supplies the finding class. Co-located drilled holes and copper beyond
the board outline are structural engineering findings and always grade.
Owner custom rules cannot be demoted by a constraint declaration; such an input
raises `OwnerRuleReclassification`. Plane production and the board-build row
stand-in apply the same numeric-advisory policy and retain classified findings.
`tests.test_provenance_limits` covers masked native gaps, co-located holes,
zero/negative printed edge clearance, owner-rule declarations and both producer
paths. `tests.test_outline_containment` exercises saved copper polygon subtraction,
crossing and wholly outside pads/tracks, concave outlines and cutouts, round
copper whose bounding-box corners are outside, saved zone fills, captured DRC,
parity, plane refusal, and sublayout source preservation.
`tests.test_sublayout.SublayoutProvenanceTests` covers the sub-layout DRC delta
(AGENTEE-399): project rules reach both runs, and new findings grade by class.
The independent
`containment/copper-containment.json` witnesses are engineering/hard and are
included in `drc-constraints.json`, without rewriting the native report.
Four-decimal millimetre reports have a 50 nm comparison limit; printed copper-edge
actuals below it are structural. The saved polygon check also detects wholly
outside copper that native edge DRC may omit.

Baseline comparisons include enforcement changes and the transition to copper
outside the outline. The same native item identities cannot hide a newly hard
finding; identical pre-existing findings retain their baseline treatment. Non-collision
numeric findings include absolute geometry in their baseline identity. Collision
comparisons without saved geometry retain item-pair identity across wording changes
but bind it to the printed measured value: a worsened (or otherwise changed) actual
on the same items is a new finding, never baseline. A measurement comes only
from a numeric constraint type's final KiCad clause (`<limit> mm; actual <value> mm)`,
or `min spoke count N; actual M)` for thermal spokes); text in net names, rule
names or item descriptions never supplies one, and shorts, co-located holes, PTH
courtyard intrusion and other structural types are always unmeasured. An unmeasured collision
(a native short, co-located holes, PTH courtyard intrusion, or a clearance without that clause) on
items that also collide in the baseline, where either finding is hard, refuses with `CollisionMeasurementRefusal`
(unknown) instead of matching by item pair: only native saved geometry, or the
routed-artwork producer's silk-only write-scope proof bound to the digests of
both DRC'd boards (copper unchanged), can decide it. Unmeasured collisions
without a baseline counterpart remain new; advisory-only pairs keep item-pair
identity. Native publication still compares
the saved item geometry. Copper
outside the board material always refuses placement/plane publication, including
pre-existing witnesses.

The placer publication comparison independently refuses newly introduced copper
`items_not_allowed` findings (AGENTEE-424), even if the packing predicate allows
the pose or the finding has advisory numeric provenance. Native saved shapes
bind the affected item to each contacted board/peer area in absolute geometry;
only its own footprint area uses rigid local geometry to retain an unchanged
owner baseline through translation, quarter turns and a side flip (AGENTEE-426:
the flipped footprint is compared at its front-side local geometry, pad layers
restricted to the board's enabled layers). Missing contact geometry,
unsupported contacted contours or ignored category coverage refuses publication.
Footprint-only findings retain their prior treatment. The native regression is
`tests.test_placer_pad_keepout`; the authorized courtyard-only mutant in
`tests.test_placer_packing_scope` retains its refused candidate as diagnostics.
See [pad keep-outs](placer.md#peer-courtyards-and-pad-keep-outs-agentee-418).

[Native keep-out witness cost](placer.md#native-keep-out-witness-cost-agentee-427)
(AGENTEE-427) uses one native board load, UUID indexes and cached absolute/local
area geometry. A deterministic one-million-step computational guard refuses
publication as typed `keepout_work_limit` unknown coverage; it returns no partial
witnesses and adds no wall-clock default or physical design threshold. Native
absolute witnesses and verdicts remain unchanged within budget; owner-local
witnesses also canonicalize side flips as described above. The exact budget
boundary, 1,024-pad/64-area case, default exhaustion and retained failure fields
are exercised by `tests.test_placer_pad_keepout`.

Explicitly ignoring `holes_co_located` refuses grading as unavailable structural
coverage, even when the project has only default numeric hole limits.

`starved_thermal` uses dimensionless counts, with exact comparisons. Default
spoke-count findings remain advisory. Unconditional captured owner count minima
retain native file/hash/rule citations and gate against the reported actual
count, independently of the printed minimum. Missing measurements and ambiguous
conditional/layer-scoped sourced count findings refuse as unavailable coverage.

When the supplemental containment child is unavailable, its coverage remains
unknown and its failed execution is retained. A defect already proved by native
DRC retains its defect verdict; an otherwise passing DRC cannot pass without
containment coverage. Supplemental structural witnesses retain their class and citation in the
classified evidence cited by the collected coordinator finding.

## Carried silk in the placement projection (AGENTEE-346)

The DRC `placement-stage/1` projection is the one DRC exempt from the
carried-defect refusal. Exemption requires a placement-stage or unstaged
declaration; schematic, routing, verification, fab and release declarations
still refuse. Under the
AGENTEE-333 ruling it runs and grades instead. Each unwaived native
`silk_overlap` or `silk_over_copper` violation whose type and complete item-UUID
set equal an open carried `placement_silkscreen_conflict` is graded as carried.
It does not block, so the campaign advances to routing. The result keeps
`details.open_defects` (every open carry, including side-flip diagnostics) and
`details.carried_findings` (raw locators). A limitation and a summary clause
state the carry, and `placement-defects.json` is the witness. Nothing passes
silently: the full `drc`, every verification/fab/release check, campaign
acceptance and release extraction still refuse until a receipted closure
(AGENTEE-344/350).

These findings keep their grading and still block:

- collision, clearance, courtyard and mask violations;
- schematic parity;
- uncarried or partially matching silk conflicts;
- [structural copper containment](#design-rule-provenance-agentee-331) findings
  such as `copper_outside_outline`, whatever silk is carried.

Composition with AGENTEE-331 provenance (coordinator ruling 346c) is one policy,
`checks.grade_drc`, shared by the check and its seal replay. In the projection,
overlap-type silk conflicts are graded only by receipt-backed carry: an
unsourced default `min_silk_clearance` never turns an uncarried overlap into an
advisory pass. Numeric silk limits such as `silk_edge_clearance` are never
carried. They follow 331 provenance: advisory under an unsourced or default
limit, hard under a sourced one. The full `drc` keeps its released 331 grading.
Carried silk and numeric advisories are not a native defect, so when the
containment child is unavailable such a projection is `malformed`, never a pass.

Unconnected items stay ungraded. Missing or tampered carry evidence makes the
projection `malformed`/`unknown`, never a pass. The projection consumes a
receipted closure under the same AGENTEE-351 contract as the full gate: at
execution it recomputes native DRC on the captured board in the tool sandbox
and retains `placement-lineage.json` with its `placement-lineage/` report; at
replay it validates that retained witness, tool-free. Without that native
evidence a would-be closure still refuses. At freeze and at replay the
evidence seal reconstructs the verdict, whether or not the candidate captured
placement lineage, so a no-lineage projection cannot seal a pass over
structural containment or uncarried silk either. Before grading, the seal
replays the runtime admissibility of the native report (AGENTEE-407,
`checks.drc_admissible` and `checks.drc_net_binding`): a later full `drc`
declaration, the project's net constraint binding, the retained `console.log`
showing that schematic parity ran, constraint provenance for every native
violation including waived ones, and no sourced numeric limit explicitly
ignored. Where the check could only be `unsupported` or `malformed`, a retained
`ok` verdict is refused, even beside genuine containment output for the same
board. It reclassifies the check's own
`drc.json` against the captured immutable waiver input and derives the
receipt-backed carry, recomputes the numeric provenance from the captured
board inputs and reads the retained `containment/copper-containment.json` and
its execution record. Any graded finding outside that carry, including a
structural containment finding, must yield `defect`. A retained pass that
records containment as unavailable is refused. A retained outcome that differs is refused. The
retained `carried_findings` must equal the reconstructed carry exactly, so a
subset or an empty list cannot hide a carried finding. Open carry and the
witness are reconstructed too. A result that omits the carry is refused. There is no registration or capability change.
`test_placement_projection_carries_silk_to_routing_and_full_drc_refuses` runs
native placement and routing. In that run the projection passes with the carried
silk, and the earliest failing stage becomes routing. There the full DRC and
the verification check refuse the carry.
`test_projection_consumes_a_natively_closed_carry_and_seals` and
`NoLineageProjectionSealTests` are the native regressions for the closed-carry
and no-lineage seal paths; its
`test_forged_ok_over_runtime_unknown_evaluation_refuses` and
`test_seal_replays_the_runtime_parity_skip_refusal` cover the replayed
admissibility.

## Post-routing silk repair (AGENTEE-350)

The [routed artwork producer](silk-artwork.md#post-routing-artwork-producer-agentee-350)
is a verification-stage implementation step. It preserves every non-silk token
and permits only declared field positions and rigid translations of supported
silk graphics. Its receipt binds the source board, output board, tool source pin,
geometry proof and complete native DRC. Coordinator carry capture and replay
reuse the placement evidence envelope. Identity-bound closure can then remove
carried defects only when each marking is visible, unchanged in style and
silk layer, and wholly inside the qualified outline. Off-board and straddling
marks remain open even if native DRC is silent. Footprint-owned Edge.Cuts
participate in outline qualification. Straight slots and cutouts qualify as holes
inside one outer contour (AGENTEE-362); markings must lie wholly outside them.
Circles, arcs and rounded rectangles qualify through exact conservative bounds
(AGENTEE-368): superset polygons for holes, a subset polygon for the outer
contour. Touching, crossing, nested or unsupported contours establish no containment.
NPTH drills and pad slots are not outline holes; native DRC alone judges silk over them. Both producers seal a board-bound native containment witness in
the placement report. Closure and campaign replay read it without rerunning
pcbnew; absent or invalid witnesses close nothing. Remaining conflicts and
unavailable evidence keep their refusal. Cached-text translations refuse the
producer's scope proof. Unchanged nodes must keep their exact raw spelling.
Backslashes are accepted only as KiCad writer escapes (`\"`, `\\`, `\n`, `\r`)
inside quoted strings, so pod boards with escaped JSON properties attest. Any
other backslash refuses (tests: `test_scope_refuses_backslashes_even_when_parser_erases_them`,
`test_backslash_and_spelling_ambiguity_refuses` in `tests/test_routed_artwork.py`;
AGENTEE-434). An optional receipted target set of footprint reference and
field pairs restricts the repair to those fields; the proof refuses any other
silk move ([targeted repair](silk-artwork.md#targeted-repair-agentee-434)).
An exact field-pose request, bound to the source digest and to footprint and
field uuids, permits only its named source-to-destination poses. An angle may
change only where the request names it. Pose tokens must be unquoted plain
decimals, and an explicit `null` request refuses rather than selecting the
default search. Everything else must stay byte-exact
([field-pose request](silk-artwork.md#exact-field-pose-request-agentee-434)).
This adds no check registration or verdict waiver. The initial placer continues
to refuse routed boards. The native campaign regression proves downstream
refusal before the repair and passing verification geometry and native fab export after closure.
The closure-currency regression preserves closure after masked-track edits on a
board with a proven outline and retains the defect on the same board without
an outline.

## Declared placer obstacles (AGENTEE-338)

The [placer obstacle policy](placer.md#declared-obstacle-unions-and-reserved-gap-provenance-agentee-338)
accepts touching and overlapping declared polygons as their exact same-side union.
The policy still refuses invalid individual polygons. The pod's 0.25 mm reserved gap is an
agent-chosen advisory default, currently enforced against fixed courtyards and
obstacles; it has no captured assembly-rule authority. A capacity proof that
binds an unsourced gap is typed unknown and names `gap_nm` for owner confirmation.
Proofs independent of that gap retain their verdicts; authored fixture requirements
carry explicit fixture-author provenance. Qualified manufacturer gap requirements
remain hard. There is no registered-check change; the existing packing policy's
constraint provenance gains an optional value-bound `gap_nm` source entry.

The [unsourced policy-limit rule](placer.md#unsourced-policy-limits-agentee-358)
(AGENTEE-358) also makes an unsourced courtyard edge margin advisory for capacity
proofs. A dependent proof reports unknown and names `min_courtyard_edge_mm`;
a proof surviving without it retains its verdict. The audit table distinguishes
search radii, captured keepout geometry and computational guards. The low-level
packing provenance object accepts a value-bound `min_courtyard_edge_mm` source
with `value_nm`, class, enforcement and citation; check registration is unchanged.

AGENTEE-365 closes two source paths for the
[obstacle and group sources](placer.md#obstacle-and-group-sources-agentee-361).
The placement adapter and pod driver refuse per-obstacle `provenance` and
per-group `constraint_provenance` in the caller's packing policy, as they
already refuse a policy-level `constraint_provenance`. Hard block-arrangement
keep-outs and `no_stacking` shadows carry their rule's citation as
`arrangement_rule`/`hard`, so a residual capacity proof that rests on one
reports `defect` instead of `unknown`. No check registration changes.

AGENTEE-439 makes the campaign placement helper accept a captured
[blocks request](placement-adapter.md#captured-blocks-requests-agentee-439).
Its candidates must bind the captured netlist bytes, the allocation and the
exact request policy at binding time. The helper uses the captured netlist only
when a fresh in-sandbox export differs from it in nothing but the declared
`/export/design/date` and `/export/design/tool` stamps. Any other difference
refuses (driver exit 4) and no board is written. The campaign source pin
(`policy_files`) and the helper snapshot now include `agent_ee/data/*`.
Run results therefore record the data digests under `data/<name>`. No check
registration or verdict changes.

### Saved-placement reserve diagnostics (AGENTEE-380)

The [placer reserve-cost record](placer.md#advisory-reserve-cost-agentee-380)
is advisory saved-proposal evidence, not a registered acceptance check.
`placement-result.json` and arranged `block-provenance.json` carry per-side,
per-owner clipped reserve areas and `large_reserve` findings. Unsupported bodies
or outlines remain unknown. The unsourced 5% suggestion never affects check
verdicts. Saved proposals validate cheaper poses with native DRC, captured rules and the
existing write-scope preservation witness on scratch boards. Rejected and unknown
attempts remain visible; if no attempt validates and any attempt is unknown,
the alternative stays unknown with an incomplete search. Numeric-looking zone
names never waive structural native findings. Standalone geometric poses stay
hypothetical and unverified. The optimum is limited to the declared finite pose
set. A cheaper pose for an owner-fixed part is a `decision_required` record
naming the fixed declaration's source, the candidate pose and the saving; the
fixed part stays in place on the proposal. Rigid blocks are moved only whole,
with member relative poses kept, and validated natively the same way
(AGENTEE-384). A scope's optional `reserved_area` key carries the saved-board
record to reviewers without block provenance; the offline arrangement CLI
records a typed unknown instead of a measurement. Both packet ingress paths
validate the record first: the exact advisory authority, a known status, exact
non-negative ratios, and `measured` only when both sides and all their owners
are measured. Every record kind (the record, its threshold, sides, owners,
findings, alternatives, owner decisions, fixed sources, rigid blocks, moves,
native attempts and their validation witnesses) has a closed, per-status
allow-list of required and optional fields, each typed. An extra or missing
field, a wrong type, or a qualification incompatible with the status refuses the
record before any status rule runs (AGENTEE-384). Copied declarations are
checked against their own closed schemas at ingress: a fixed source's
`overhang_envelope` against the courtyard-overhang declaration schema the packer
uses (reference, source and typed edges, for the owner's own reference) and its
provenance against the placer's provenance entry rules (or the exact undeclared
default). A native witness carries no embedded write-scope report; a refused scope
is bound by `write_scope_sha256` to the retained `placement-write-scope.json`. `already_zero`, `not_searched`,
`no_cheaper_contained_pose`, `none_found` and `unknown` alternatives never carry
a selected pose, a measurement, a saving or a legal qualification; `unknown` is
qualified `hypothetical, unverified`. `legal under captured rules` is accepted
only on a `native_validated` alternative (or decision) whose selected pose is a
successful native attempt. Every finding's `reserve` must be
a valid measured owner equal to its side's owner row (side, owner, kind, id and
all fields); every alternative and owner decision with a saving needs exact
measurements with a positive saving equal to the owner area less the alternative
area, and the qualification of its status. `native_validated` needs its selected
pose to be the one `validated` native attempt, and `search_complete` is false
exactly when an attempt was unknown; unknown-only attempts are never published
as validated. A witness's `moves` name each reference once; a rigid block's
native attempts must carry them, and a `native_validated` rigid alternative's
selected `moves` must equal its validated witness's moves member by member, in
any order. A `decision_required` record must be unapplied and agree with its
alternative's pose, saving and qualification. The scope `board` must be captured and the record's
`board_sha256` must equal its digest; a malformed or unbound record refuses the
packet.

AGENTEE-420 retains unchanged blocking baseline findings as unknown pose
legality, using the shared native DRC comparison and saved item states. New or
moved blockers still reject, even alongside baseline findings. Completed native
attempts carry normalized candidate and baseline DRC content digests into the
cost record; normalization is specified in the feature reference. Advisory
measurement or search exceptions produce an explicit unknown record and cannot
refuse placement. Hard publication gates keep their existing verdicts.

## Access direction in placement containment (AGENTEE-379)

The existing `placement` check with `outline-containment/1` also grades native
[footprint access declarations](access-direction.md). Sourced declarations with
an inboard lane or an unexitable mouth are defects; malformed or unsupported
sourced geometry remains unknown. Uncited declarations are reported advisories
and never gate. Only a hard `manufacturer` or `datasheet` lane with a declared
`travel_nm` equal to its depth relaxes courtyard containment inside the lane
(AGENTEE-419). Other sourced lanes still gate, and their outside courtyard area
stays an `outside_outline` defect. The saved-board placement judge retains transformed lane
measurements and the shared fixed-overhang edge-sweep geometry for review.
This adds no check kind or independent acceptance authority.

## Candidate inspection images (AGENTEE-382)

[Candidate imaging](pcb-renders.md#candidate-inspection-images-agentee-382) runs
after saved placement and during coordinator capture, with its own 180-second
executor guard. Imaging elapsed time is excluded from the check/reviewer
`max_seconds` budget. It is not a registered
acceptance check. Review packets carry board-bound top/bottom PNG and copper,
silk and courtyard SVG paths and hashes, including every enabled inner layer.
Skipped or failed imaging remains explicit missing coverage and leaves every
existing check verdict unchanged. No image can establish geometric fit or waive
a native defect.

The shared test harness defaults incidental candidate imaging off with the
strict `AGENT_EE_SKIP_VISUALS=1` flag. Candidate-visual and 3D-render test modules
force `0` while they run, retaining native image/hash coverage. Production
continues to render by default; this test setting neither skips tests nor
changes a check verdict. Ordinary placement helper steps run with the typed
[sandbox render policy](pcb-renders.md#sandbox-render-policy-agentee-458)
`skip_visuals=True` and never image their proposal (AGENTEE-458).
