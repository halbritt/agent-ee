# Captured active-device SPICE checks

`active_spice` is separate from `rc_transient`. It accepts explicitly captured
subcircuit models and runs native ngspice transient and AC analyses against the
preceding native KiCad XML export. It does not discover models, infer model pin
order or replace missing active parts with ideal devices.

The [owned control scope](../fixtures/active-spice/control.scope.json) is a
complete declaration. Its amplifier model is synthetic, written for software
regression tests, and is **not vendor qualification**. Its schematic uses an
owned five-terminal amplifier symbol with explicit package pin numbers. The
accompanying board records the same components/nets but is unrouted, with
abstract pads; it is not a layout or fabrication fixture. Only the schematic
netlist and simulation checks are declared.

## Declaration

Use `params.contract: "active-spice/1"` and an explicit `dialect` of `ngspice`
or `ngspice-ps` (native PSpice compatibility, without model translation).
`netlist_check` must identify an earlier successful native `netlist` check for
the same candidate. `ground_net` selects one actual native net as SPICE zero;
all other nets receive generated identifiers, with a retained reverse map.
`temperature_c` is explicit and finite, above absolute zero and at most 1000 C.
This software range does not qualify a component's temperature range.

`model_files` lists the complete captured model closure, including nested
includes. Each populated non-passive component needs an `active_parts` entry:

- `model_file`: one file in that immutable list containing the actual header;
- `subcircuit`: the actual, unambiguous `.SUBCKT` name;
- `pin_map`: native symbol pin number to exact formal terminal token.

The mapping must cover **every native symbol pin and every formal exactly once**.
The generated X instance follows header order, never map or pin-number order.
Different pins on the same native net are valid. Missing/disconnected/NC pins,
missing symbol inventories and partial multi-unit bindings are unsupported.
No omitted-pin or automatic multi-channel decomposition contract is provided.
The handler reads the complete native `libparts` pin inventory directly from
retained XML; it does not depend on optional connectivity pin-metadata additions.
Duplicate native connections are refused. Known native R/C/L symbols use actual
positive scalar values; connectors, virtual power symbols and DNP parts are
explicitly inventoried as excluded. All other populated components need models.

`sources` declares voltage sources with unique safe `id`, native
`positive_net`/`negative_net` and finite `dc_v`. Optional positive
`ac_magnitude_v` sets a zero-phase AC excitation. An optional `step` declares
`initial_v`, `final_v`, nonnegative `delay_s` and positive `rise_s`; initial
voltage must equal DC bias. This is a finite-rise, piecewise-linear step, not an
instantaneous mathematical discontinuity. Supply names do not imply voltages.

Optional `current_sources` declare loads with unique `id` (distinct from voltage
source IDs), native `positive_net`/`negative_net` and finite `dc_a`. SPICE
current flows from the positive net through the source, so a positive `dc_a`
from an output to ground sinks load current. An optional `step` has
`initial_a` (equal to `dc_a`), `final_a`, `delay_s` and `rise_s`, with the same
finite-rise piecewise-linear meaning as a voltage step. Current sources carry no
AC excitation.

`analyses` has unique safe IDs (`models` and `version` are reserved) and at
least one measurement per analysis:

| Kind | Analysis fields | Supported measurement |
| --- | --- | --- |
| `transient` | Positive `step_s < stop_s`, at most one million nominal intervals | `observable: "voltage"` with `at_s`; `"voltage_change"` (value at `to_s` minus value at `from_s`); `"voltage_minimum"`/`"voltage_maximum"` over `from_s < to_s`. Explicit `positive_net`/`negative_net`, times in the simulated interval, `unit: "V"` |
| `ac` | `sweep: "dec"`, integer `points_per_decade` from 1 to 10000, positive `start_hz < stop_hz`, at most one million samples | `observable: "transfer_magnitude"`, explicit `output_positive_net`/`output_negative_net` and `input_positive_net`/`input_negative_net`, `at_hz` in the sweep, `unit: "V/V"` |
| `dc` | `source` names a declared voltage source; finite `start_v < stop_v`; integer `points` from 2 to 100001 | `observable: "voltage"` with `at_v`, or `"voltage_change"` (value at `to_v` minus value at `from_v`). Explicit `positive_net`/`negative_net`, sweep values in the interval, `unit: "V"` |

A DC sweep replaces the named source's DC value with the linear grid
`start_v + i * (stop_v - start_v) / (points - 1)`. Each retained sweep sample
must match its grid value to within 1e-9 of the span, and measurements use the
declared grid. Window extremes are taken over the linear interpolant, that is,
the window ends plus the retained samples inside it. Their accuracy between
samples depends on the declared resolution. The final transient sample must
match `stop_s` to within 1e-9 relative, and `stop_s` is then a valid time for a
measurement.

Measurement names are unique across the check. At least one finite `min` or
`max` creates a criterion; reversed limits are refused. Unbounded measurements
must explicitly set `informational: true`. An all-informational check returns
`info`, not `pass`.

[Role-keyed templates](spice-templates.md) can generate `sources`,
`current_sources` and `analyses` from a captured library, reference
architecture, targets and interface contract. A `template` object replaces
those fields, and unparseable values make measurements unsupported, never
substituted.

## Model containment and supported dialect

All roots and transitive dependencies must appear in `model_files`, be captured
with matching SHA-256, and have literal relative paths without traversal or
symlinks. That entire list becomes immutable baseline files under scope/2, even
when someone tries to list a model in campaign `mutable_inputs`. Undeclared
includes are refused before ngspice. Limits are 64 files, 2 MB combined model
text and include depth 16. Include cycles and repeated include expansion are refused. This bounds captured
model input, not every possible nonlinear solver cost; the normal run deadline
and per-invocation 600-second timeout also apply.

The initial dialect accepts ASCII `.SUBCKT`, `.ENDS`, `.MODEL`, `.PARAM`, and
captured `.INCLUDE` outside subcircuits, plus R/C/L, literal DC V/I, E/F/G/H,
D/Q/J/M/S and X device statements. Supported `.MODEL` types are D, NPN, PNP,
NJF, PJF, NMOS, PMOS, R/RES, C, SW and VSWITCH. E/G VALUE and TABLE expressions
may use V/I, abs, sqrt, exp, ln/log/log10, pow/pwr, min/max, limit, if,
sin/cos/tan/tanh. Unknown functions, model types and directives are refused;
this is not arbitrary PSpice or arbitrary behavioral-model support.
Duplicate model names in the same scope and explicit diode `level` selectors
are refused: ngspice can silently select a definition or fall back for unknown
diode levels. Diode models use the native default level only. Native
ngspice must still accept and solve the qualified model's device syntax.

`.ENDS` may be bare or name the open subcircuit, as many vendor libraries
write it. Other directives need their arguments, and a directive word after a
statement's first token, including through a continuation (`.ends` then
`+ .control`), is refused.

Continuations and directive case are normalized for validation. Generated
copies preserve original model text except validated include-path rewrites;
captured originals remain untouched. Alternate comment delimiters, quoted
expressions, `.LIB` sections, `.FUNC`, encrypted models, XSPICE/code models,
file-driven sources, user control blocks, analyses, measurements and termination
commands are unsupported. File-reading/output keywords and unrecognized function
calls are refused. Only the coordinator supplies the control script, executable
arguments, generated net IDs, numeric source declarations and output paths.
`ngspice -n -b` disables startup-file loading and runs in the existing networkless
sandbox, with validated staged models mounted read-only. No new dependency or
sandbox network permission is needed.

## Measurements and evidence

Each analysis has its own deck, sole coordinator control script, execution,
stdout/stderr, log and binary raw trace. Model/source hashes, generated hashes,
formal-header locators, native pin inventory, component UUID/sheet identity,
explicit pin-to-net-to-node bindings, exclusions and tool hash/version are
retained. These artifacts use the existing coordinator seal and witness gates.

The parser validates real/complex raw shape, vector names/types, finite samples,
strictly increasing time/frequency and complete declared analysis intervals.
Raw files are bounded to 128 MB and eight million point-variable pairs.
Time-domain samples use linear interpolation. AC samples interpolate real and
imaginary voltages linearly in frequency, then compute the magnitude of the
**complex differential output/input ratio**. Zero input denominators and
out-of-trace queries are malformed, not gain measurements. Choose sweep density
and transient step appropriate to an independently established error envelope;
interpolation does not certify accuracy between samples. The owned RC fixture's
100 Hz point is on the declared logarithmic grid, and its finite-rise transient
is compared with an independent analytic RC response with an explicit tolerance.

All analyses must finish before any metrics can pass. Missing models/maps,
unsupported dialect, or unsafe includes yield `unsupported/unknown`; unavailable
or wrong-candidate prerequisites yield `not_run/unknown`; solver errors yield
`failed/unknown`. Otherwise, simulator warnings and diagnostics reporting ignored,
unrecognized, unsupported, substituted, replaced, reset, clamped or out-of-range
semantics yield `unsupported/unknown` before measurement extraction. The check
examines the native log and stdout/stderr; no warning is qualified as harmless
under this contract. Logs and any produced raw trace remain evidence, but no
partial metrics or measurement record are emitted. This does not certify
arbitrary parameter semantics that ngspice changes without a diagnostic.
Missing/invalid traces or measurements yield
`malformed/unknown`. Timeout/unavailable/interrupted retain normal execution
semantics. Complete valid metrics are judged by the existing coordinator margin
and calibration functions. Complete bound violations are `ok/defect` and still
need sealed, mapped witnesses for accepted defects. Owner review remains
separate from implementation and peer review.

These checks establish only declared conditions using supplied models. Effects
absent from model coverage, tolerances, thermal behavior, PCB parasitics, EMC,
noise, faults, startup and hardware performance remain untested unless separately
modeled and checked. Manufacturer provenance does not establish model accuracy
or redistribution rights. Third-party models/designs must remain outside the
repository until the owner authorizes publication. The committed synthetic model
does not satisfy vendor-model acceptance by itself.

## Scope/3 coverage

The executable-coverage audit validates the active-SPICE declaration and its
preceding native netlist prerequisite, freezes every declared model file, and
counts only measurements with explicit bounds toward acceptance coverage.
Informational-only analyses cannot satisfy an acceptance requirement. Native
connectivity, complete populated-device bindings, include closure and solver
behavior still require runtime verification; the static audit does not certify
a model or its applicability.
