# Native and engineering checks

For version-2 requirements, measurement/coupling interfaces, campaigns and bench
feedback, see the [closed-loop contract and command reference](closed-loop.md).

All handlers execute against the captured candidate, not the source checkout.
Their working directories are separate. The original tool commands, sandbox
arguments, exit status and console output remain in `work/CHECK_ID/`.
[checks.py](../src/agent_ee/checks.py) interprets native output and applies the
criteria; [geometry_probe.py](../src/agent_ee/geometry_probe.py) extracts saved
board facts through pcbnew.

[Stage-one intent import](design-intent.md) prepares source-bound requirements
and incomplete interface stubs for later capture. Its lint outcome covers only
the declared lexical checks; executable checks must supply acceptance evidence.

## Execution and engineering result

Every result separates `execution` from `outcome`. A tool can run successfully
and report a design violation: `execution=ok`, `outcome=defect`. A crash, missing
output, unsupported component or absent measurement means unknown coverage,
not a confirmed electrical defect and not a passing check.

The native commands below show sandbox paths; `/candidate` is the captured
`inputs/`, `/work` is this check's scratch. They document the emitted arguments,
not commands to paste into a host shell with nonexistent sandbox paths.

| Kind | Native/helper arguments | Individual ceiling |
| --- | --- | --- |
| `erc` | `kicad-cli sch erc --format json --severity-all --units mm -o /work/erc.json SCHEMATIC` | 900 s |
| `drc` | `kicad-cli pcb drc --format json --severity-all --schematic-parity --units mm -o /work/drc.json BOARD` | 900 s |
| `netlist` | `kicad-cli sch export netlist --format kicadxml -o /work/design.xml SCHEMATIC` | 600 s |
| `catalog` | `/usr/bin/python3 -m agent_ee.catalog_checks catalog /candidate/SNAPSHOT MANIFEST_SHA256 --out /work/catalog.json`; [snapshot integrity](catalog.md) only | 120 s |
| `part_selection` | Same runner with `part_selection` and `--requirements /candidate/REQUIREMENTS`; [declared catalog constraints](part-selection.md) | 120 s |
| `netlist_parity` | Coordinator compares full native XML with captured intent/libraries or a golden netlist; qualified virtual power markers omitted by XML are listed in the report ([details](netlist-parity.md)) | Enclosing run deadline |

| `netlist_extra` | Coordinator checks the earlier native netlist and captured topology policy; no additional tool launched ([details](netlist-extra.md)) | Enclosing run deadline |
| `hardware_interface` | Coordinator compares a captured owner contract with preceding full native XML; no additional tool launched | Enclosing run deadline |
| `footprint_assignment` | `/usr/bin/python3 -m agent_ee.footprint_library` for declared selections, or `agent_ee.native_binding` with an earlier native netlist | 60 s |
| `routing_rules` | `/usr/bin/python3 -m agent_ee.routing_native /work/rule-request.json`; staged KiCad DRC per constraint | 900 s outer call |
| `fab_profile` | Same runner; staged KiCad DRC per sourced limit plus a saved-board pcbnew inventory | 900 s outer call |
| `fab_consistency` | Coordinator reads the earlier export's sealed staging tree and version run, the captured board, the sealed netlist and earlier sealed geometry; no tool launched, no export regenerated ([details](fab-consistency.md)) | Enclosing run deadline |
| `package_consistency` | `/usr/bin/python3 -B -m agent_ee.package_consistency /candidate/CATALOG MANIFEST_SHA256 /work/request.json /work/catalog-rows.json`; coordinator then compares preceding native XML names through the captured table ([details](package-consistency.md)) | 600 s |
| `geometry` | `/usr/bin/python3 -B -m agent_ee.geometry_probe BOARD -o /work/geometry.json` | 600 s |
| `measurements` | Declared pinned producer argv, then `/usr/bin/python3 /candidate/ADAPTER /work/request.json /work/measurements.json` | Default 600 s per producer/parser call |
| `coupled_margins` | Coordinator joins earlier scalar results; no external solver launched | Enclosing run deadline |
| `rc_transient` | `ngspice -n -b -o /work/ngspice.log /work/control.sp` | 600 s |
| `schematic_lint` | Coordinator reads the captured sheets and the earlier netlist check's sealed `design.xml`; no tool launched ([details](schematic-lint.md)) | Enclosing run deadline |
| `fab_export` | `kicad-cli version`, then `pcb export stats`, `gerbers`, `drill`, `pos` on BOARD; see [fab export](fab-export.md) | 600 s per step |
| `assembly_readiness` | `kicad-cli sch export netlist --format kicadxml -o /work/design.xml SCHEMATIC`, then a coordinator join with the captured catalog; see [assembly readiness](assembly-readiness.md) | 600 s |

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

## ERC

No check parameters are currently interpreted. The JSON report must contain a
`sheets` list covering the expected number of instances. Violations are counted
across sheets, grouped by severity/type, and retained in `erc.json`; ignored
check keys are reported. Any returned violation makes the check `defect`.

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

Zone fill is evaluated as saved. The implementation does not refill, save or
repair the board. Reported exclusions stay visible and are not dropped by this
all-severity audit. The [donor baseline](../evidence/BASELINE.md) distinguishes
these rule failures from independently established manufacturing failures.
Native DRC coverage also depends on the project's configured checks and ignored
rules; passing these checks is not full circuit function or fab qualification.

## Captured routing constraints

The [routing check](routing-rules.md) accepts a captured `params.routing_rules`
JSON document. It compiles explicit bounds into isolated native custom-rule
runs. Scope/2 and scope/3 bind exactly one requirement per routing check and
freeze its rule document and captured native rule sidecars. Complete route and
constraint coverage is required; unsupported topology or missing inputs are
unknown. General DRC and schematic parity remain separate obligations.

## Captured fabricator profiles

The [fab profile check](fab-profile.md) accepts a captured `params.fab_profile`
document whose limits each cite an exact quote in a captured UTF-8 source. It
runs sourced physical minima as isolated native rules and compares layer count,
fixed-axis outline extents and SMD presence with the saved board. Missing sources,
missing profiles and unqualified outlines are unknown. Preferred-tier overruns
are reported as extra charges, never defects. No real fabricator preset exists.

## Native netlist

No check parameters are currently interpreted. A complete hierarchy is required.
`design.xml` must exist, parse, and contain components and nets. It is retained
unchanged. `connectivity.json` derives:

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

With `criterion: "copper-width/1"`, declared tracks and native arcs are checked
using integer-nanometre widths without the legacy tolerance/rounding. Empty or
unsupported eligible geometry is `unsupported/unknown`, including when another
primitive is known narrow. Complete coverage is `ok/defect` for any below-limit
primitive, otherwise `ok/pass`. Candidate-bound `copper-width.json` retains all
primitive identities, values, limits, statuses and coverage counts alongside the
raw probe report. Vias remain separate; zones, pads and graphic copper shapes
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
agent-ee does not install thermal, parasitic, DFM/DFA/DFT or supply-chain adapters.

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

## Footprint assignment

`footprint_assignment` requires captured `manifest` and `declaration` paths and
immutable `assembly_intent` records. The manifest declares complete pinned
footprint membership; every member and symbol library must be readable and
hash-valid. Empty selections, absent exact footprint names, missing or extra
electrical pad numbers, connector count mismatches and assembly mismatches are
defects. Duplicate thermal pad numbers count once; qualified paste-only and
unnumbered mechanical pads do not participate in electrical parity. Library
read or parse failures leave unknown coverage.

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
authority and unknown boundaries.

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

## JLCPCB fab package

See [fab export](fab-export.md) for the `fab_export` staging layout, the
`agent-ee.fab-manifest/1` manifest, BOM and CPL rules, timestamp normalisation,
v1 refusals and outcome mapping. It requires an earlier `netlist_check`, refuses
completion unless BOM/CPL/board designators agree, and applies no CPL rotation
policy. Launch preflight requires the native `kicad-cli` binary. Empty produced
native files are `failed/unknown`; pre-existing staging or native output
directories are refused before reuse.

[`fab_consistency`](fab-consistency.md) consumes that sealed export, its netlist
and an earlier geometry probe. It checks plot/layer and PTH/NPTH inventory,
BOM/CPL/assembled-board reference parity, LCSC/footprint conflicts, CPL side,
captured/embedded 3D models and recorded export-log diagnostics. CPL anchors
must agree with exact native `position_nm` within 0.01 mm per axis, using
absolute board X and negative board Y on either side. Missing exact positions
or a missing sealed log is unknown coverage; broken hashes or malformed CSV
are `malformed/unknown`. Package disagreements are `ok/defect` and qualify only
through a sealed requirement/check witness. Rotation correction and DFM are
outside this check.
