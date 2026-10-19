# Role-keyed SPICE measurement templates

An [`active_spice`](active-spice.md) check can take its stimulus, loads,
analyses and measurements from a captured, versioned template library instead
of a hand-written testbench. The template is chosen by the block's circuit
role. Terminals come from [hardware-interface](hardware-interface.md) bindings,
operating conditions from the reference architecture, and every limit from the
scope targets. Nothing is defaulted or guessed. A value that does not parse
exactly makes the measurements using it unsupported. The expansion produces
ordinary `active-spice/1` analyses, so the existing model, sandbox, trace and
evidence rules apply.

The owned [regulator fixture](../fixtures/active-spice/ldo/control.scope.json)
is a complete example. Its regulator model is synthetic and checks only that
the software works; it is **not vendor qualification**.

## Declaration

Template mode replaces `sources` and `analyses` with `template`. The other
`active-spice/1` fields stay explicit: `model_files`, `active_parts`,
`ground_net`, `temperature_c`, `netlist_check` and `dialect`.

```json
{
  "id": "regulator",
  "kind": "active_spice",
  "params": {
    "contract": "active-spice/1", "dialect": "ngspice", "netlist_check": "netlist",
    "ground_net": "/GND", "temperature_c": 25, "model_files": ["design/ldo.lib"],
    "active_parts": {"U1": {"model_file": "design/ldo.lib", "subcircuit": "ldo",
                            "pin_map": {"1": "IN", "2": "GND", "3": "OUT"}}},
    "template": {"library": "design/spice-templates.json",
                 "architecture": "design/architecture.json",
                 "targets": "design/targets.json",
                 "interface": "design/interface.json",
                 "block": "ldo1"}
  }
}
```

All four files must be captured. Template mode needs a scope/2 or scope/3
baseline. `architecture` must be listed in `baseline.reference_architectures`
and `targets` in `baseline.targets`. The library and interface contract also
become immutable baseline inputs, so changing any of them creates a new
baseline. JSON documents with duplicate fields are refused.

## Library

The shipped library is `src/agent_ee/data/spice-templates.json`
(`agent-ee.spice-templates/1`, version `2026-10-18.1`). To capture it, write it
into the design inputs:

```sh
python -m agent_ee.spice_templates library > design/spice-templates.json
```

| Role | Terminals | Analyses and measurements |
| --- | --- | --- |
| `ldo` | `vin`, `vout`, `gnd` | DC sweep of `vin` from `vin_min` to `vin_max` (101 points): output at both ends within `vout_min`..`vout_max`, line regulation within ±`line_regulation_max`. Load-step transient (`load_light` to `load_heavy` current at `load_step_delay`, `load_step_rise`): load regulation within ±`load_regulation_max`, window minimum at least `vout_transient_min`. AC: input-to-output transfer at `ripple_frequency` at most `ripple_transfer_max`. |
| `amplifier` | `in`, `out`, `gnd`, `vpos`, `vneg` | AC gain at `gain_frequency` within `gain_min`..`gain_max`; gain at `bandwidth_frequency` at least `bandwidth_gain_min`. |
| `filter` | `in`, `out`, `gnd`, `vpos`, `vneg` | AC passband gain at `passband_frequency` within `passband_gain_min`..`passband_gain_max`; stopband gain at `stopband_frequency` at most `stopband_gain_max`. |

The LDO stimulus also needs the conditions `vin_nominal` (DC bias for the AC
and transient analyses), `load_step_stop` and `load_step_resolution`. The
amplifier and filter need `input_bias`, `supply_positive` and `supply_negative`.
The amplifier and filter templates assume dual supplies. A single-supply design
has no template here.

A template has `description`, `terminals`, `sources`, optional
`current_sources` and `analyses`, using the `active-spice/1` field names. Leaf
values may be literals or references:

- `{"terminal": "vout"}` is allowed only in net fields. A literal net name in a
  template is refused.
- `{"condition": "vin_min", "unit": "V"}` is allowed in stimulus and analysis
  fields and in measurement locators.
- `{"target": "vout_min", "unit": "V"}` is allowed in measurement locators and
  bounds. Bounds must be target references, and `"negate": true` gives the
  negated value (for symmetric bounds).

Allowed units are `V`, `A`, `s`, `Hz` and `V/V`. Literal sweep ranges and point
densities in the library are versioned template data. A target frequency
outside the template's sweep makes that measurement unsupported.

## Architecture, targets and terminals

`agent-ee.spice-architecture/1` maps block IDs to `role`, `terminals`
(template terminal to interface terminal ID) and `conditions`.
`agent-ee.spice-targets/1` maps block IDs to named limits. Condition and target
values are strings with units:

```json
{"schema": "agent-ee.spice-targets/1",
 "blocks": {"ldo1": {"vout_min": "3.234 V", "line_regulation_max": "5 mV",
                     "ripple_transfer_max": "-50 dB"}}}
```

A template terminal resolves only if the interface terminal has a component
pin and exactly one `named_net` invariant with exactly one name. When the check
runs, that component's single native location must have that pin on that native
net. If the interface contradicts the native netlist, the whole check is
unsupported. Templates never guess a supply from a net name.

## Exact values

A value is a decimal number, an optional space, an optional SI prefix
(`p n u m k M G`) and the expected unit, for example `4.5 V`, `10 mA`, `100 ns`,
`1 kHz`. `V/V` values take no prefix. They may also be written in `dB`, which
converts to `10^(dB/20)`. Values are decoded exactly as decimals before
conversion to binary floating point. Values that do not fit this form are
unsupported. That includes `4k7`, `10m` (no unit, and SPICE `m` versus `M`
ambiguity), `2.2uF 25V`, lowercase units, `MEG`, bare numbers, overflow and
underflow. Nothing is substituted for them.

Native passive values follow the `active-spice/1` rule: a scalar SPICE value
that the dialect cannot represent, such as `4k7` or `2.2uF 25V`, makes the
whole check unsupported. One deck contains every component, so no measurement
can be isolated from it.

## Outcomes

Expansion writes `template-expansion.json` and records it in the result's
`details.template`. It holds the library path, hash and version, the
architecture, targets and interface hashes, terminal bindings, and one record
per template measurement. Each record has `execution: "supported"`, with the
parsed condition and target values, or `"unsupported"`, with a reason.

- Unsupported stimulus or analysis fields make every dependent measurement
  unsupported. Unsupported locators or limits affect only their own
  measurement. A missing condition or target is unsupported, not defaulted.
- Supported measurements still run. If any template measurement is unsupported
  and none of the supported ones violates a bound, the check is
  `unsupported/unknown`. A complete bound violation remains `ok/defect`, and
  the summary also names the unsupported measurements.
- A role with no template in the library, or a block whose measurements are
  all unsupported, gives `unsupported/unknown`.
- Missing or misnamed subcircuit models, unbound components and unsafe model
  text give `unsupported/unknown` under the active-spice contract. That is
  missing coverage, never a pass.
- Under scope/3 coverage, only supported bounded measurements become
  `active-spice:` predicates.

Template mode adds no tool, network access or dependency. Simulation is the
same pinned ngspice in the networkless sandbox, with captured model, netlist
and template bytes. The templates establish declared conditions on supplied
models only. They do not cover startup, stability margins beyond the declared
points, thermal behavior, tolerances or hardware performance.
