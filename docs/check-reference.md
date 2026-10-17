# Native and engineering checks

For version-2 requirements, measurement/coupling interfaces, campaigns and bench
feedback, see the [closed-loop contract and command reference](closed-loop.md).

All handlers execute against the captured candidate, not the source checkout.
Their working directories are separate. The original tool commands, sandbox
arguments, exit status and console output remain in `work/CHECK_ID/`.
[checks.py](../src/agent_ee/checks.py) interprets native output and applies the
criteria; [geometry_probe.py](../src/agent_ee/geometry_probe.py) extracts saved
board facts through pcbnew.

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
| `geometry` | `/usr/bin/python3 -B -m agent_ee.geometry_probe BOARD -o /work/geometry.json` | 600 s |
| `measurements` | Declared pinned producer argv, then `/usr/bin/python3 /candidate/ADAPTER /work/request.json /work/measurements.json` | Default 600 s per producer/parser call |
| `coupled_margins` | Coordinator joins earlier scalar results; no external solver launched | Enclosing run deadline |
| `rc_transient` | `ngspice -n -b -o /work/ngspice.log /work/control.sp` | 600 s |

The remaining run budget can shorten every ceiling. A deadline-shortened native
timeout is recorded as interrupted coverage. There is no native network access.

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

No check parameters are currently interpreted. Required JSON lists are
`violations`, `unconnected_items`, and `schematic_parity`. A missing list is
malformed coverage. The console is also checked for known native messages that
indicate skipped schematic parity even when the CLI exits 0. Any item in the
three lists makes the criterion fail.

Zone fill is evaluated as saved. The implementation does not refill, save or
repair the board. Reported exclusions stay visible and are not dropped by this
all-severity audit. The [donor baseline](../evidence/BASELINE.md) distinguishes
these rule failures from independently established manufacturing failures.
Native DRC coverage also depends on the project's configured checks and ignored
rules; passing these checks is not full circuit function or fab qualification.

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
| `step_v` | Explicit value required when measurements are requested | Finite positive ideal step amplitude, volts; see current limitation below |
| `stop_ms` | 5.0 | Finite positive stop time, milliseconds |
| `calibration_inputs` | Optional captured path list; nonempty before bench ingestion | Immutable model applicability inputs for empirical feedback |
| `measurements` | Default empty | Entries with `name`, `at_ms`, optional `min`/`max` in volts |

Current limitation: deck validation defaults an omitted `step_v` to 1.0, but
metric-condition construction later reads the original parameter directly. With
any requested measurement, omission therefore raises `KeyError` after simulation
instead of producing a valid check. Set `step_v` explicitly in both scope versions.

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
Only `analyzer: "schematic"`, `rule_id: "RC-DET"`, low-pass records currently
have qualified bound semantics, for fields `cutoff_hz` and `time_constant_s`.
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
