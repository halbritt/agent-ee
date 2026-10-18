# Net constraints as netclasses (AGENTEE-47)

`agent_ee.net_constraints` turns per-net electrical constraints declared in the
intent into native KiCad 10 netclasses. The output feeds routing and DRC, and
the [`copper-width/1`](check-reference.md#saved-board-geometry) check reads the
per-net minimums from it. It is a stage 3 transform that feeds stages 4 and 5 of the
[generator roadmap](generator-roadmap.md).

```sh
PYTHONPATH=src python3 -m agent_ee.net_constraints constraints.json \
  --project template/board.kicad_pro --envelope requirements/envelope.json \
  [--rules template/board.kicad_dru] --out NEW_DIRECTORY
```

The command runs offline and uses only the standard library. The same inputs
always produce the same bytes. Exit codes:

- 0: every declared constraint was emitted. The output directory holds
  `board.kicad_pro`, `board.kicad_dru` and `net-constraints.record.json`, with
  basenames taken from `--project`.
- 1: outcome is unknown. Only the record is written, and its `findings` name
  the cause. No project bytes are produced, so nothing falls back to a silent
  default.
- 2: the input was refused, or input/output failed. The output directory must
  not already exist.

## Constraint document

```json
{
  "schema": "agent-ee.net-constraints/1",
  "temperature_rise_c": 10,
  "classes": [
    {"name": "POWER_IN", "requirement": "R-width", "nets": ["/IN"], "current_ma": 2000},
    {"name": "USB", "requirement": "R-usb", "nets": ["USB_D+", "USB_D-"],
     "track_width_mm": 0.2, "clearance_mm": 0.15,
     "diff_pair": {"gap_mm": 0.15}, "impedance_ohm": 90}
  ]
}
```

| Field | Meaning |
| --- | --- |
| `name` | Netclass name: letters, digits, `_ . + -`, at most 64 characters. Names equal to `Default` in any letter case are refused |
| `requirement` | The requirement ID this class implements |
| `nets` | Exact native net names, for example `/IN`. A net may appear in only one class. Names cannot contain quotes, backslashes or control characters |
| `current_ma` | Continuous current per net. The track width follows IPC-2152 (see below) |
| `track_width_mm` | Explicit minimum width. If `current_ma` is also given, the larger of the two wins |
| `clearance_mm` | Netclass clearance, which KiCad DRC enforces natively |
| `diff_pair` | `gap_mm` and optional `via_gap_mm`. The class must hold exactly two nets that differ only by a native `+`/`-` or `P`/`N` suffix. `diff_pair_width` is set equal to the track width |
| `impedance_ohm` | Target impedance. It is recorded only (see below) |

Each class must declare at least one constraint. A top-level
`temperature_rise_c` is required whenever any class declares a current, and is
refused when none does. Every number must be finite and positive. Unknown
fields, duplicate JSON keys, `NaN` and booleans used as numbers are refused.
Nets that the document does not declare stay in Default.

## Track width from current

The minimum width comes from a published curve fit of the IPC-2152 generic chart
(the smps.us trace-width calculator fit, as cited by the AGENTEE-47 worker on
2026-10-18; the six coefficients are restated in `net_constraints.METHOD`, no code or
chart data is copied, and the IPC-2152 standard itself is not in this repository)
(3 oz copper on a 0.070 in polyimide board, still air, no planes):

    A [mil²] = (117.555·ΔT^-0.913 + 1.15) · I[A]^(0.84·ΔT^-0.108 + 1.159)

Width = A / copper thickness. No modifiers are applied for board thickness,
copper planes or copper thickness. IPC-2152 treats the unmodified generic chart
as the conservative case. The fit reproduces the chart point 10 A at 20 °C as
513.1 mil² against roughly 500 mil² read from the chart (within the fit's stated
3%). It was interpolated over 2 to 100 °C and 0 to 30 A. Inputs outside that
range give outcome unknown with code `outside_fit_range`.

Copper thickness comes from the `agent-ee.envelope/1` stackup passed with
`--envelope`, validated by the [board envelope](board-envelope.md) validator.
For each copper layer the thinner of two values is used: the declared
`thickness_mm`, or `copper_weight_oz` × 0.034798 mm (1.37 mil per ounce). The
thinnest copper layer on the board then governs, because a netclass width applies
on every layer. Widths are rounded up to 0.001 mm. Declaring 2000 mA at 10 °C on
the RC fixture's 1 oz outer copper gives 54.55 mil², and therefore 1.012 mm.

A current with no `--envelope` gives outcome unknown with code
`current_without_stackup`.

## Impedance

This command does not compute impedance geometry. If an envelope is supplied,
the target is recorded with status `recorded_not_enforced` together with the
envelope hash. Without an envelope, the outcome is unknown with code
`impedance_without_stackup`, and no project is written. A target with status
`recorded_not_enforced` remains unverified. A routing-rule
[`impedance` constraint](routing-rules.md) still reports unsupported.

## Native output

The template must be a qualified KiCad 10 project: `net_settings` schema 5 with
a complete Default class, checked with the same key set as
[netclass completeness](netclass-completeness.md). It must contain no other class
and empty `netclass_assignments` and `netclass_patterns`. The supplied
[native project](../fixtures/netclass-completeness/native.kicad_pro) qualifies.
In the output, Default is carried through unchanged.

- **Classes.** Each emitted class receives every Default key. Default values
  fill in anything the intent leaves undeclared. Priorities are assigned in
  class-name order.
- **Net assignments.** Nets are assigned through `netclass_patterns`. KiCad
  matches a pattern either as an anchored regular expression or as a wildcard,
  so every character other than a letter, digit or `_` is backslash-escaped.
  The pattern then matches only the literal name. Native tests confirm that
  `USB_D+` does not capture `USB_DD`.
- **DRC rules.** A netclass width alone is only KiCad's default routing width.
  DRC holds tracks only to the board minimum, so each class with a minimum also
  gets a rule `agent_ee_netclass_<name>`: condition `A.NetClass == '<name>'`,
  constraint `track_width (min …)`. This rule goes in the board-sibling
  `.kicad_dru`. A `--rules` template is kept byte-for-byte and the generated
  rules are appended to it. A template that already uses the reserved rule
  prefix is refused.

Place the project and rules beside the board as `<board>.kicad_pro` and
`<board>.kicad_dru`, for example as the [scaffold](scaffold.md) project
template. Keep them, and the record, as immutable inputs.

## Constraint record

`net-constraints.record.json` (`agent-ee.net-constraint-record/1`) holds:

- the hashes of the source, template, rules template, envelope, emitted project
  and emitted rules;
- the method, `temperature_rise_c` and the stackup copper layers;
- each class: declared fields, `ipc2152` area, width, governing layer and
  thickness, `min_track_width_mm`, rule name and impedance status;
- the net-to-class map;
- `findings`, `outcome`, `acceptance_ready: false` and the limitations.

## Copper-width binding

A `geometry` check with `criterion: "copper-width/1"` may add
`"net_constraints": "<captured record path>"`. The record must have outcome
`pass`. The board-sibling `.kicad_pro` must be captured, carry every recorded
class with exactly the recorded escaped patterns, and have a `track_width` at
least the recorded minimum. Each track or arc is then held to the larger of
`min_track_width_mm` and its net's recorded minimum.

The record and the project become immutable baseline inputs in scope/2 and
scope/3. A record or project that is missing, does not pass, or does not match
makes the capability unsupported, and execution gives `unsupported/unknown`.
`copper-width.json` stores `net_limits_mm`, each primitive's limit, and the
record and project hashes.

## Evidence and limits

`tests/test_net_constraints.py` uses the routed RC fixture with the emitted
project and rules:

- Production native DRC with schematic parity reports exactly one violation, a
  0.25 mm `/IN` track below its `POWER_IN` rule minimum of 1.012 mm.
- `copper-width/1` reports the same track as a defect with limit 1.012 mm. It
  holds `/OUT` to 0.25 mm and leaves `GND` at the 0.2 mm global minimum.
- Widening `/IN` to 1.012 mm makes both checks pass.
- pcbnew resolves `/IN` to `POWER_IN`, `/OUT` to `SIGNAL` and `GND` to `Default`.

Unit tests cover:

- the IPC-2152 width against a fit restated independently in the test;
- the case where the thinnest copper layer governs;
- Default completeness;
- unknown outcomes for impedance or current without a stackup, and for inputs
  outside the fit range;
- refusals, binding failures on a tampered project, and CLI exit codes.

What these tests do not establish:

- The fit is a third-party approximation of the standard's chart, not the
  standard itself. Check safety-critical widths against IPC-2152.
- Vias, zones, pads and copper pours are outside the width rule.
- Current is assumed to be continuous and per net.
- Fusing current, pulsed loads and ambient derating are not modelled.
