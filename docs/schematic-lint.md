# Schematic readability lint (AGENTEE-45)

`schematic_lint` is a coordinator check that reports legibility and
presentation findings ERC does not produce. It reads the captured `.kicad_sch`
hierarchy and the native XML netlist retained by an earlier `netlist` check. It
launches no tool and never edits the design. Each finding names the sheet, the
UUIDs and references involved, coordinates in millimetres, what to move, and
that every existing connection must be preserved.

```json
{"id": "lint", "kind": "schematic_lint",
 "params": {"netlist_check": "netlist",
            "power_nets": ["+3V3"], "ground_nets": ["GND"],
            "symbols": {"Lint:R": {"class": "resistor"},
                        "Lint:C": {"class": "capacitor"},
                        "Lint:LED": {"class": "led"},
                        "Lint:LDO": {"class": "regulator", "input": "1", "output": "3"}},
            "rules": {"divider-order/1": "defect", "on-grid/1": "info"}}}
```

## Parameters

| Parameter | Default | Meaning |
| --- | --- | --- |
| `netlist_check` | required | Id of an earlier `netlist` check for the same candidate. |
| `rules` | every rule at its default severity | Rule id to `info`, `warning` or `defect`. Only listed rules run. |
| `power_nets`, `ground_nets` | `[]` | Native net names (as exported, e.g. `+3V3`, `/VREF`). Never inferred from names. |
| `symbols` | `{}` | `lib_id` to a declared class: `resistor`, `capacitor`, `led`, or `regulator` with its `input` and `output` pin numbers. |
| `grid_mm` | `1.27` | Placement grid, exact in nanometres. |
| `title_block` | `["title", "date", "rev"]` | Required title-block fields, from `title`, `date`, `rev`, `company`. |
| `diff_pairs` | `[["_DP","_DN"],["D+","D-"]]` | Suffix pairs that must appear together. Add `["_P","_N"]` explicitly; it is not a default because active-low names such as `RESET_N` have no partner. |
| `hidden_properties` | `["Footprint","Datasheet","Description"]` | Internal properties that must not be shown. |

An unknown rule id, severity or parameter makes the check
`execution=unsupported`. In scope/3, rules raised to `defect` are the
acceptance predicates `schematic-lint:<rule>`. A profile without a `defect`
rule is informational and has no acceptance predicate.

## Rules

| Rule | Default | Finding |
| --- | --- | --- |
| `symbol-overlap/1` | warning | Two symbol bodies intersect with positive area, including two symbols at the same origin. Names both. |
| `label-in-symbol/1` | warning | A local, global or hierarchical label anchor lies strictly inside a symbol body. |
| `wire-crosses-pin/1` | warning | A wire touches a pin's segment without ending on its connection point, and the pin's native net is not the wire's net. Hidden pins of non-power symbols are not drawn and are skipped. KiCad 10.0.7 does not connect a pin on a wire's interior (the native fixture exports it as `unconnected-(R8-Pad1)`). |
| `wire-overlap/1` | warning | Two collinear wires overlap with positive length. Wires that only share an endpoint are not flagged. |
| `two-pin-rails/1` | warning | A two-pin part with a power-net pin drawn below its ground-net pin. |
| `pull-resistor/1` | warning | A resistor with one pin on a rail draws its power pin below, or its ground pin above, the other pin. Divider members are left to the divider rule. |
| `divider-order/1` | warning | Resistors sharing a non-rail net, one to power and one to ground, with the supply resistor drawn below the ground resistor. One finding names both. |
| `regulator-capacitors/1` | warning | A capacitor between a regulator's declared input (or output) net and ground is drawn on the other side of the regulator, measured along the axis joining its input and output pins. |
| `led-chain-order/1` | warning | Three or more LEDs joined by two-terminal nets are not placed monotonically along one axis in connected order. |
| `rail-label/1` | warning | A hierarchical label carries a rail (a declared power or ground net, or the Value of any power symbol) instead of power symbols. |
| `diff-pair-naming/1` | warning | A native net or label name with one suffix of a pair has no partner name with the other suffix. |
| `on-grid/1` | info | A symbol origin, wire end, junction, no-connect, label, sheet rectangle corner or sheet pin is off the grid. The finding names the UUID. |
| `hidden-properties/1` | info | A non-empty property from `hidden_properties` is visible. |
| `reference-visible/1` | info | A non-virtual symbol has a hidden or empty Reference. |
| `reference-prefix/1` | info | The instance reference prefix differs from the Reference prefix of its library symbol. |
| `title-block/1` | info | A required title-block field is empty or missing, per sheet instance. |

Symbols whose reference starts with `#` (KiCad virtual symbols such as power
ports) are exempt from the property rules. Power symbols carry their Value as
their net name; they are not netlist components.

The rules use names from tscircuit/checks (placement rules, ports and
reference text), SchGen (rotated/mirrored box overlap, labels in symbols, wire
overlap) and the circuit-synth review checklist. agent-ee implements them
itself. No upstream code, fixture or design file was copied or consulted.

## Geometry

Symbol definitions come from each sheet's embedded `lib_symbols`, which is what
KiCad draws. They are read with the shared S-expression reader and inheritance
resolver (`symbol_library`). Coordinates are exact integer nanometres. The
instance transform is the emitter's KiCad matrix: library y up, sheet y down,
counter-clockwise rotation, mirror applied on the sheet after rotation. Body
boxes are the body graphics (rectangle, polyline, circle) of the instance's
unit and body style 1, with pins, text and stroke width excluded. Pins carry
their full segment from the connection point into the body.

The [transform fixture](../fixtures/schematic-lint/generate.py) gives
hand-derived body boxes and pin positions for 0, 90, 180 and 270 degrees and
the mirrored forms. Native ERC pin positions from kicad-cli 10.0.7 match them.

Wire nets come from the native netlist. Wires join at shared endpoints, and
junctions, labels and sheet pins join a wire where they lie on it. A wire
group's net is the set of native nets of the pins at its endpoints, plus its
label and sheet-pin names, compared by basename. Rail, class and regulator pin
roles come only from the profile.

## Coverage and outcome

| Condition | Effect |
| --- | --- |
| `lib_id` absent from the sheet's `lib_symbols`, unresolved inheritance, unsupported transform or alternate body style | Missing coverage for that symbol on every library-dependent rule. |
| Body graphics outside the bounded subset, such as arcs | Missing coverage for that symbol on the overlap and label rules. |
| Symbol with no native component, or a pin without native membership | Missing coverage for that symbol on the net rules. |
| Native inventory refused | One design-level gap on every net rule, with `native_inventory: refused` in `lint.json`. The `netlist` producer audits only component identities, so it passes a design whose XML the lint's strict reader refuses. A pinless symbol, such as a mounting hole whose `libpart` has no `<pins>`, is one such case. |
| Comparison work limit (4,000,000 per sheet) exceeded | Missing coverage for that rule on that sheet. |
| Missing, failed or later `netlist` prerequisite | `execution=failed` or `unsupported`. An unresolved symbol ends here. KiCad still exports a netlist around it, but drops the component and invents `Net-(D1-Pad??)`. The producer then fails with `unexplained_omission`, and the lint does not run. |
| Unreadable sheet, broken hierarchy, or sheet instances that differ from the native hierarchy | `execution=malformed`. |

The outcome is `defect` if any finding's rule is at `defect`. Otherwise it is
`unknown` if any coverage is missing, `info` if there are findings, and `pass`
if there are none. A rule that needs no library definition (title block, grid,
properties) does not inherit a symbol's library gap. A proven defect stays a
defect beside unrelated missing coverage.

## Evidence

`work/<id>/lint.json` (schema `agent-ee.schematic-lint/1`) retains the
profile, sheet instances with file hashes, findings, coverage, per-rule counts
and the native XML hash. The result also cites the prerequisite's `design.xml`.

## Limits

This is presentation lint. Style findings never establish an electrical defect
unless the profile raises a rule to `defect`. Text extents, property positions,
fonts, arcs and bezier body graphics are not measured. Buses and bus entries are not linted. A sheet file used more
than once is linted once per instance. Parallel-RC alignment, root index sheets,
label stubs and visible-MPN policy from the source checklists are not
implemented.

Tests: `tests/test_schematic_lint.py` covers profile refusal, the
hand-checked transforms, offline geometry and presentation rules, and native
end-to-end runs through capture, `netlist` and `run_checks` for every finding
listed above.
