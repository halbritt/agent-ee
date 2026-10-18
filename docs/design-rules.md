# Design-rule cards

`design_rules` (AGENTEE-62) keeps writer guidance and its grading in one
captured source. A rule pack holds versioned cards. The campaign gives every
card's text to the implementation writer. Only cards bound to a qualified
adapter are graded, and only by that adapter after the design exists. The check
never matches card text against the design.

A card has one of three enforcement modes:

- **adapter**: a closed, versioned connectivity rule in
  `agent_ee.design_rules.ADAPTERS`. The check runs it.
- **delegated**: an earlier check of a registered kind grades the card. That
  check must publish exactly one predicate.
- **advisory**: no executable mapping, an unknown adapter name or an
  unregistered check kind. The writer still sees the text, but the card has no
  predicate. It can never be enforced, so it can never satisfy an acceptance
  requirement.

The card fields follow circuit-synth's `DesignRule` dataclass (MIT): `id`,
`description`, `applies_to`, `rationale`, `examples`, `violations_cause` and
`priority`. circuit-synth injects these into prompts and enforces them by
substring match. agent-ee does not enforce by substring. The I2C pull-up window
follows the idea in atopile's `I2C.py`, which is inactive upstream. No code or
card text is copied from either project.

## Rule pack

The pack (`agent-ee.design-rule-pack/1`) is an immutable baseline dependency.
It has exactly these fields: `schema`, `id`, `version`,
`provenance {authored_by, date, license, derived_from}` and a nonempty `cards`
list. Each card has exactly these fields:

| Field | Meaning |
| --- | --- |
| `id` | `[A-Z][A-Z0-9_]{0,63}`, unique in the pack |
| `version` | Positive integer; change it when the card text or enforcement changes |
| `description` | The rule as given to the writer. It states only what the adapter grades |
| `applies_to`, `examples` | Nonempty text lists |
| `rationale`, `violations_cause` | Text |
| `priority` | `critical`, `important` or `recommended` |
| `enforcement` | `null`, `{"adapter": NAME}` or `{"check_kind": KIND}` |
| `parameters` | Validated by the adapter; must be `{}` for every other card |

[`fixtures/design-rules/pack.json`](../fixtures/design-rules/pack.json) is the
owned pack `mcu-peripheral-basics`. It has six adapter cards and one advisory
card, `I2C_BUS_CAPACITANCE`, because bus capacitance and rise time are not
checked.

## Adapters

Each adapter is a pure rule over the strict native inventory of an earlier
`netlist` check. The only inputs are the owner's role bindings and verified
datasheet facts. A counted part is a populated part whose library symbol the
bindings declare `resistor` or `capacitor`, with its two declared pins on the
two bound nets. Every item is `satisfied`, `violation`, `unknown` or
`not_applicable`.

| Adapter | Role group | Rule |
| --- | --- | --- |
| `i2c-pullup-window/1` | `i2c_buses` | Each SDA/SCL line has resistors to the bound rail whose parallel equivalent lies in `[min_ohm, max_ohm]`. The owned pack uses 900 Ω to 11 kΩ (1 kΩ −10 % to 10 kΩ +10 %). No pull-up is a defect that cites the line's native net. A Value that is not a plain resistance (`4.7k`, `4k7`, `470R`, `10 kΩ`) is unknown. |
| `i2c-address-unique/1` | `i2c_buses` | Each device resolves a 7-bit address from a verified `i2c_address` fact, either fixed or chosen by the net of a strap pin. Two devices with the same resolved address are a defect. A device without an address binding, a verified fact or a matching strap is unknown (maybe unfulfilled), never pass. A duplicate stays a defect beside unknown devices. |
| `crystal-load-caps/1` | `crystals` | Each listed crystal pin has a capacitor to the bound ground. The check reports presence or absence per pin and the capacitors found. |
| `reset-rc/1` | `resets` | Applies only when a verified `reset_external_rc` fact for the bound part number exists and the binding sets `requires: true`. The pin then needs a resistor to the rail and a capacitor to ground. No fact gives unknown. `requires: false` gives `not_applicable`. |
| `decoupling-to-ground/1` | `power_pins` | The net of each bound supply pin has at least one capacitor to ground. Proximity and per-pin dedication are not checked. |
| `usb-series-resistor/1` | `usb_lines` | Applies only through a verified `usb_external_series_resistor` fact, as for `reset-rc/1`. Many PHYs terminate internally, so the 22 Ω rule is not universal. A resistor must join the device pin net and the connector pin net; a direct connection is a defect. |

When no counted part is found, an undeclared, populated two-pin part across
the same two nets makes the item unknown rather than a defect. Parts with more
than two pins, such as connectors, ICs and resistor arrays, are not counted.

## Role bindings

The bindings (`agent-ee.design-rule-bindings/1`) are an immutable owner
dependency with exactly `schema`, `classes` and `roles`. `classes` maps exact
`library:symbol` IDs to `{class, pins}`, using the
[netlist-extra class rule](netlist-extra.md). `roles` may contain `i2c_buses`,
`crystals`, `resets`, `power_pins` and `usb_lines`. A declared group must be
nonempty. A missing group is an unbound role, so its cards are unknown. Every
anchor is an exported `{reference, pin}` terminal, never a net name.

```json
{
  "schema": "agent-ee.design-rule-bindings/1",
  "classes": {"Device:R": {"class": "resistor", "pins": ["1", "2"]},
              "Device:C": {"class": "capacitor", "pins": ["1", "2"]}},
  "roles": {
    "i2c_buses": [{"id": "I2C0", "sda": {"reference": "U1", "pin": "3"},
                   "scl": {"reference": "U1", "pin": "4"}, "rail": {"reference": "U1", "pin": "1"},
                   "devices": [{"reference": "U2", "part_number": "OWN-TS1", "address": {"strap": {
                     "pin": "5", "options": [{"terminal": {"reference": "U2", "pin": "2"}, "fact": "ts1-addr-gnd"}]}}}]}],
    "crystals": [{"reference": "Y1", "pins": ["1", "3"], "ground": {"reference": "U1", "pin": "2"}}],
    "resets": [{"id": "U1-EN", "terminal": {"reference": "U1", "pin": "5"}, "part_number": "OWN-MCU1",
                "rail": {"reference": "U1", "pin": "1"}, "ground": {"reference": "U1", "pin": "2"},
                "applicability": {"fact": "mcu1-en-rc", "requires": true}}]
  }
}
```

An I2C bus lists every attached device. A device's address may be
`{"fact": ID}` or `{"strap": {pin, options}}`.

## Check parameters

```json
{"id": "rules", "kind": "design_rules", "params": {
  "pack": "pack.json", "bindings": "bindings.json", "netlist_check": "netlist",
  "facts_check": "facts", "cards": ["I2C_PULLUP_WINDOW", "RESET_EN_RC"]}}
```

`cards` lists the enforced cards. Each card publishes one predicate,
`design-rule:<card>:<adapter>` or `design-rule:<card>:delegated:<check>`. If
`cards` names an advisory card, the whole check is unsupported. Scope/3
coverage then refuses it, so an advisory card cannot reach an acceptance
mapping. `facts_check` is optional and names an earlier `datasheet_facts`
check. Only facts that check marked verified are used, and the fact's part
number must equal the bound part number. If it is absent, fact-gated items are
unknown. `delegated` maps a delegated card to `{check, predicate}`, where
`check` is an earlier check of the card's kind and `predicate` is that check's
single predicate.

## Result

`work/<id>/design-rules.json` (`agent-ee.design-rule-result/1`) has one record
per pack card: `pass`, `defect`, `unknown` or `not_applicable` for enforced
cards, `advisory` or `not_enforced` otherwise. It also holds the native net
locators of each item and hashes of the XML, connectivity, pack and bindings.
Any defect makes the check `ok/defect`, and the result location names each
violated item and its native net. With no defect, any unknown makes it
`unsupported/unknown`. Advisory cards never change the outcome.

## Writer guidance

During a campaign, the implementation prompt receives a `design_rule_cards`
JSON block. It carries each card's text, priority, `graded_by` (the check ID,
or `null` for cards that are advisory or not enforced) and `advisory`, with the
pack hash. The block is appended inside `campaign.implement` before the prompt
is retained as `implementation-prompt.txt`. The fixed resubmission notice is
still appended only by `review_run.structured_call`. The text is guidance,
not evidence. The writer remains the only design writer, and fresh checks grade
every proposal.

## Limits

These are exported-net connectivity rules. They do not check proximity,
routing, bus capacitance, rise time, value tolerance or capacitor values.
Applicability and addresses come only from verified datasheet facts and owner
bindings. Citation verification proves only that the quoted text exists, so
interpreting a fact remains the owner's job.
