# Native and engineering checks

For version-2 requirements, measurement/coupling interfaces, campaigns and bench
feedback, see the [closed-loop contract and command reference](closed-loop.md).

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

[Stage-one intent import](design-intent.md) prepares source-bound requirements
and incomplete interface stubs for later capture. Its lint outcome covers only
the declared lexical checks; executable checks must supply acceptance evidence.
The [requirement-record compiler](requirement-scope.md) maps quoted spec
requirements to the check specifications on this page; it adds no check kind.
Offline [decision capture](design-dialog.md) also prepares requirements without
adding a check kind. Answers that exceed the JSON decoder's recursion limit
produce `outcome: unknown` and an `answers_too_deep` finding, retain the raw
answers and their hash, and produce no derived requirements (CLI exit 1).

## Execution and engineering result

Every result separates `execution` from `outcome`. A tool can run successfully
and report a design violation: `execution=ok`, `outcome=defect`. A crash, missing
output, unsupported component or absent measurement means unknown coverage,
not a confirmed electrical defect and not a passing check.

Checks are executed once for each captured candidate; failed or malformed check
output is never schema-resubmitted. Campaigns may resubmit only model records
under the [bounded output rule](campaign-guide.md#bounded-output-resubmission).
Adjudicator citations may reference the verified immutable `baseline.json` for
context, but accepted defects still require their sealed failed-check citation.
See [response and citation rules](result-reference.md).

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
| `net_names` | Coordinator checks the earlier native netlist against a captured owner name policy and the candidate's name-level annotation; no additional tool launched ([details](net-names.md)) | Enclosing run deadline |
| `regeneration_determinism` | Coordinator compares two captured boards, schematics, netlists or JSON probe outputs after removing IDs; no additional tool launched ([details](design-diff.md)) | Enclosing run deadline |
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
| `measurements` | Declared pinned producer argv, then `/usr/bin/python3 /candidate/ADAPTER /work/request.json /work/measurements.json` | Default 600 s per producer/parser call |
| `coupled_margins` | Coordinator joins earlier scalar results; no external solver launched | Enclosing run deadline |
| `rc_transient` | `ngspice -n -b -o /work/ngspice.log /work/control.sp` | 600 s |
| `block_provenance` | Earlier board XML plus sandboxed source and instrumented boundary netlist exports; [block adaptation](block-adaptation.md) | 120 s per export, bounded by run deadline |
| `schematic_lint` | Coordinator reads the captured sheets and the earlier netlist check's sealed `design.xml`; no tool launched ([details](schematic-lint.md)) | Enclosing run deadline |
| `fab_export` | `kicad-cli version`, then `pcb export stats`, `gerbers`, `drill`, `pos` on BOARD; see [fab export](fab-export.md) | 600 s per step |
| `fab_package` | The `fab_export` steps into its own `work/ID/fab/`, then the `fab_consistency` gate on that tree and an artifact-hash recomputation; declared last ([details](fab-package.md)) | 600 s per export step |
| `assembly_readiness` | `kicad-cli sch export netlist --format kicadxml -o /work/design.xml SCHEMATIC`, then a coordinator join with the captured catalog; see [assembly readiness](assembly-readiness.md) | 600 s |
| `cpl_rotation` | Coordinator reads the earlier export's verified CPL/BOM, earlier sealed geometry pad boxes, the captured board, policy and supplier footprints; no tool launched ([details](cpl-rotation.md)) | Enclosing run deadline |

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

## Approved parts membership

The [approved-parts manifest](approved-parts.md) uses `agent-ee.parts/1` and
pins catalog, selection, CAD and datasheet provenance. The `parts_subset` check
takes exactly `manifest`, `netlist_check` and `requirement_id`. Its requirement
must point to the manifest's captured declaration source. The full dependency
closure is immutable in scope/2 and scope/3.

Every populated native instance must match its declared symbol, footprint and
supplier identity, and every required sheet/reference selector must be present.
A mismatch or absent required selector is a defect; footprint findings retain
the reference and both footprint values. Missing or failed native evidence,
missing identity data and required unavailable provenance leave coverage unknown.
The per-reference table is retained in `work/CHECK_ID/parts-subset.json` and the
coordinator seals the check and its prerequisite XML. The predicate establishes
declared membership, not approval authenticity or component suitability.

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

## ERC

No check parameters are currently interpreted. The JSON report must contain a
`sheets` list covering the expected number of instances. Violations are counted
across sheets, grouped by severity/type, and retained in `erc.json`; ignored
check keys are reported. Any returned violation makes the check `defect`.
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

The [original RC stage fixtures](rc-stage-fixtures.md) exercise this boundary:
placed-unrouted retains native unconnected items and passes the placement
projection; seeded-placement has a native R1/C1 courtyard overlap. Both remain
defects under full DRC. The control-derived variants requested by AGENTEE-103
remain blocked by unresolved third-party rights.

Zone fill is evaluated as saved. The implementation does not refill, save or
repair the board. Reported exclusions stay visible and are not dropped by this
all-severity audit. The [donor baseline](../evidence/BASELINE.md) distinguishes
these rule failures from independently established manufacturing failures.
Native DRC coverage also depends on the project's configured checks and ignored
rules; passing these checks is not full circuit function or fab qualification.

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
in the writer's sandbox with a read-only checkout and state directory. Its receipt
records provenance and applicability; it is not an acceptance check. The campaign
verifies the retained routing result before adopting an exit-0 or exit-2 board and
on replay. Exits 1, 3 and 4 produce no proposal. Fresh DRC (including parity and
unconnected items), `copper-width/1` and configured routing constraints still
determine the captured board's outcome.

The [routing check](routing-rules.md) accepts a captured `params.routing_rules`
JSON document. It compiles explicit bounds into isolated native custom-rule
runs. Scope/2 and scope/3 bind exactly one requirement per routing check and
freeze its rule document and captured native rule sidecars. Complete route and
constraint coverage is required; unsupported topology or missing inputs are
unknown. General DRC and schematic parity remain separate obligations.
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
and execution. No real fabricator preset exists.

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

## Intent and golden netlist parity

The [`netlist_parity` check](netlist-parity.md) compares a captured golden XML
netlist or captured connectivity intent with an earlier native `netlist` check.
Required parameters are `source`, `netlist_check`, `predicate` (`topology`,
`components`, or `instance`) and `multiset` (`exactly` or, for component counts,
`at_least`). Resolved intent requires its captured originating intent and libraries
so the coordinator can rederive it. One check maps to one baseline requirement.

Exact physical-pin incidence preserves identical component instances and ignores
reference and net names. Missing or extra parts, changed bindings and merged or
split nets are defects. Ambiguous required instance correspondence, unresolvable
match keys and bounded-search exhaustion are unknown. Scores describe a match;
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
exact pad/via `drill_nm` and native `flash_layers`; see [fab round-trip](fab-roundtrip.md).

With `criterion: "copper-width/1"`, declared tracks and native arcs are checked
using integer-nanometre widths without the legacy tolerance/rounding. Empty or
unsupported eligible geometry is `unsupported/unknown`, including when another
primitive is known narrow. Complete coverage is `ok/defect` for any below-limit
primitive, otherwise `ok/pass`. Candidate-bound `copper-width.json` retains all
primitive identities, values, limits, statuses and coverage counts alongside the
raw probe report. With `net_constraints`, each primitive's limit is the larger of
`min_track_width_mm` and its net's recorded netclass minimum. The record must
pass, and the captured board-sibling `.kicad_pro` must carry its classes and exact
patterns. Both are immutable inputs; a missing or mismatched binding is
`unsupported/unknown` ([net constraints](net-constraints.md)). Vias remain separate; zones, pads and graphic copper shapes
are outside this routing-width criterion. See [coverage](coverage.md) for the
complete class/count contract. Opting in changes the scope/2 baseline identity;
existing baseline criteria keep their historical meaning.

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

## Hardware interface invariants

`hardware_interface` requires `contract` and an earlier `netlist_check`. The
[interface reference](hardware-interface.md) defines the captured normative
schema, immutable inputs, supported predicates and unsupported mapping policy.
It checks declared schematic properties and direct conductors, without inferring
interfaces from names or treating components as transparent shorts.

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
result, and a declaration that applies to nothing is unknown
([owner declarations](footprint-libraries.md#owner-declarations)).

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

## Supplier land patterns

`land_pattern` takes a captured `criteria` path that binds supplier provenance,
raw EasyEDA geometry and KiCad library members. The check retains native
extraction and bounded overlap evidence through the coordinator seal; missing
or unsupported geometry cannot pass. See the [contract and qualification](land-pattern.md)
for exact parameters, pin-number comparisons and coverage limits. The actual
C11702/0402 comparison does not meet the requested 0.98 threshold. The absolute
threshold is the only acceptance rule. Any other acceptance criterion for that
pair is an owner decision.

## Footprint and catalog package names

`package_consistency` requires an earlier `netlist_check`, a captured catalog
snapshot pinned by manifest hash, and a captured `agent-ee.package-map/1` table.
Each native component gets `match`, `mismatch`, `class_mismatch`, `unknown` or
`excluded`, naming the table rule that fired; a name outside the table is
unknown, never pass, and the table hash is recorded in the evidence. See
[package consistency](package-consistency.md) for parameters, rules and limits.

## Placement predicates

`placement` evaluates one declared criterion per check against the saved-board
probe and the raw report of an earlier `drc` check; it never runs DRC itself.
See [placement checks](placement.md#placement-check-kind) for parameters,
authority and unknown boundaries, and [native qualification](placement.md#native-qualification)
for the acceptance tests and remaining campaign and fabrication-limit gaps.
A `defect` carries `details.gate_findings`, one identity per proved defect: the
reference (or `+`-joined overlapping references, or ratsnest metric) and the
defect kind; connector-edge violations use `connector_<kind>`. Only connector-edge
findings carry a `suggested_fix`, copied from the owner declaration the
predicate grades: `move` to the declared anchor, `rotate` to the declared
rotation, or `flip` to the declared side. Hints are advisory and never applied.

## Board envelope predicates

`board_envelope` takes exactly `criterion` and a captured immutable `envelope`
path. `outline/1` compares the exact declared Edge.Cuts polygon against saved
geometry and a native closed-contour probe. `layers/1` compares the exact ordered
copper layer set against saved and native enabled layers. Each check binds one
field and one requirement, with predicates `envelope:outline/1` or
`envelope:layers/1`.

`stackup/1` is refused as executable acceptance coverage: KiCad thickness does
not establish nominal copper ounces. Direct execution retains diagnostic facts
and returns unknown. Complete scope/3 envelope capture still requires that
manufacturing authority; holes, keepouts and multiple fixed connectors also
remain unsupported for acceptance. See the [native envelope contract](board-envelope.md#native-envelope-check)
for evidence, binding and failure boundaries.

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
bare NPTH holes qualified by the existing native geometry reader.
Unparseable `PARAM_*` JSON, ambiguous pad layers and an uncaptured child sheet
are `malformed`. See [part binding](part-binding.md) for the schema, markers and
per-reference outcomes. The [Rung-1 parts adapter](rung1-parts.md) projects
replayed `parts/1` catalog identities and normalized ratings into the owned
emitter; native export and forward annotation carry them onto footprints.
Its captured-parts scope declares `parts_subset`, `assembly_readiness` and
`package_consistency`. Unknown stock, ratings or unmapped packages stay unknown.

## JLCPCB fab package

See [fab export](fab-export.md) for the `fab_export` staging layout, the
`agent-ee.fab-manifest/1` manifest, BOM and CPL rules, timestamp normalisation,
v1 refusals and outcome mapping. It requires an earlier `netlist_check`, refuses
completion unless BOM/CPL/board designators agree, and applies no CPL rotation
policy. Board footprint DNP flags decide assembly. A schematic/board DNP
disagreement is recorded in `details.dnp_disagreements` and does not block the
package. An optional declared `assembly_exclusion` rule
(`JLCPCB_Assembly=not_assembled`) leaves populated parts that carry the marker
in both the netlist and the board out of the BOM and CPL. The manifest lists
them in `designators.excluded`. A marked part without the rule, or with
disagreeing sources, is `unsupported`. Launch preflight requires the native `kicad-cli` binary. Empty produced
native files are `failed/unknown`; pre-existing staging or native output
directories are refused before reuse.

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
`PREVIEW_REQUIRED`. See [CPL rotation](cpl-rotation.md).

## Scaffold preparation

[Empty-candidate scaffolding](scaffold.md) prepares inputs for these checks; its
receipt is provenance and cannot satisfy a requirement. `scaffold --scope`
preserves the explicit planning scope's checks and baseline. `scaffold --plan`
derives `erc`, `netlist`, `parts_subset`, `drc` and `fab_profile` checks, their
mappings and the project rules from the envelope, profile and parts manifest. Native ERC/DRC run through the
ordinary `review` command. Staged absence is a coordinator defect only after a
successful native presence observation; hierarchical presence and uncovered
production envelope obligations continue to fail closed.

[`fab_roundtrip`](fab-roundtrip.md), with exactly `{export_check}`, separately
compares every enabled layer, round PTH/NPTH drill histograms, copper pad/via
flash counts and outline bounds within 1 µm. It consumes earlier sealed export
and geometry evidence. Missing layers/files and count/bound mismatches are named
defects. Inch units and unknown Gerber commands are malformed; unsupported
constructs are unknown with a 1-based source line. Artwork is outside coverage.

## Captured block adaptation

`block_provenance` takes `instance` (a captured `agent-ee.block-instance/1` file)
and `netlist_check` (an earlier board-native netlist check). Scope/2 and scope/3
freeze the record and all source-block files. The copied child stays mutable.
A native semantic report lists component and net changes as information, while
missing or rebound boundary ports produce defects. Source run results never
qualify the board. See [block adaptation](block-adaptation.md) for scaffold
inputs, requirement mapping, boundary probes, retained evidence and coverage
limits.

[`fab_package`](fab-package.md) is the generation done gate (AGENTEE-94). Params
are exactly `{netlist_check}` and it must be the last declared check; an earlier
`geometry` check is required. It runs the same export and consistency code on its
own staging tree and, on `pass`, records `details.artifacts`
(`agent-ee.fab-artifacts/1`: gerbers.zip, every drill file, bom.csv, cpl.csv and
manifest.json by sha256, bound to the candidate id and board digest). A missing,
empty or unparseable export file is `failed`, `malformed` or `unsupported` with
outcome `unknown`, or a gate `defect`; never `pass`. A scope with a `generation`
marker is refused at capture unless this check is last and an acceptance
requirement maps to it.

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
