# Datasheet pin electrical contracts

`pin_contracts` evaluates authored obligations on physical pins using the retained
native XML and `connectivity.json` from an earlier `netlist` check. It requires an
earlier `datasheet_facts` check. The evaluator runs in the existing networkless
Bubblewrap sandbox. No pin database, automatic datasheet miner or voltage solver
is bundled.

Parameters are exactly:

```json
{
  "manifest": "parts.json",
  "facts_check": "facts",
  "netlist_check": "netlist",
  "ground_nets": ["GND"],
  "power_nets": ["VDD"],
  "rails": {"VDD": [3.2, 3.4]},
  "classes": {
    "Owned:C": {"class": "capacitor", "pins": ["1", "2"]},
    "Owned:R": {"class": "resistor", "pins": ["1", "2"]},
    "Owned:IC": {"class": "other", "pins": ["1", "2", "3", "4"]}
  }
}
```

Rail names match complete native net names, including hierarchy. `rails` declares
volts as a scalar or closed `[minimum, maximum]` interval. Decimal strings are
accepted to preserve exact boundaries; booleans, nonfinite values, units in the
number and reversed intervals are rejected. Rail entries must name power-class
nets. Ground and power classes must be disjoint. These are captured scope
declarations, not measured rail values.

`classes` declares exact native symbol identities and complete selected physical
pin sets. Capacitors and resistors require two pins. `other` classifies known
non-R/C devices without asserting conduction through them. DNP devices cannot
satisfy capacitor or resistor presence. A missing element is a defect only when
attached populated components have known classes and complete terminal
inventories; an unclassified attached element leaves absence unknown.

## Contracts in the owned parts manifest

The [existing parts manifest](approved-parts.md) retains its schema and required
fields. Each logical part may additionally declare `pin_contract`, an ordinary
`{path, sha256}` captured blob. The manifest loader and dependency closure validate
and freeze this blob. A missing field records unsupported coverage. An explicit
empty `pins` list records empty coverage.

A contract file has exactly `schema`, `part_number`, `manufacturer` and `pins`:

```json
{
  "schema": "agent-ee.pin-contract/1",
  "part_number": "Owned-IC",
  "manufacturer": "Owned fixture",
  "pins": [
    {"pin": "1", "obligations": [
      {"kind": "requires_voltage", "facts": ["vdd-min", "vdd-max"]},
      {"kind": "should_have_decoupling_capacitor", "facts": ["vdd-cap"]}
    ]},
    {"pin": "2", "obligations": [
      {"kind": "must_be_connected", "facts": ["ground-connect"]}
    ]},
    {"pin": "3", "obligations": [
      {"kind": "capacitor_to_pin", "facts": ["bootstrap-cap"], "other_pin": "4"}
    ]},
    {"pin": "4", "obligations": [
      {"kind": "must_be_connected", "facts": ["switch-connect"]}
    ]}
  ]
}
```

Each obligation cites fact IDs in the preceding facts check's captured facts/1
or facts/2 document. The fact's `kind` must equal the obligation kind, its `pin`
must equal the physical pin number, and its part number and PDF path/hash must
match the manifest part's captured datasheet. The contract manufacturer and part
number must match that part exactly. Native MPN/manufacturer properties, using
the manifest's `property_fields`, and native `lib_id` must also match. No aliases,
pin-name matching, fuzzy MPN matching or reference-prefix guesses apply.

Voltage facts have `unit: "V"` and plain numeric `value`. One fact declares an
exact scalar; two declare minimum then maximum, including any authored tolerance.
There is no implicit tolerance or interval extrapolation. Presence facts have
empty `unit` and preserve the exact supporting text in `value`, such as
`"Connect to ground"`. The typed obligation kind is an authored assertion;
the checker does not translate prose into obligations or interpret negations.
A prose-only voltage requirement remains unknown even when its quote verifies.
Every bound fact must have passed actual `pdftotext` verification.
Citation occurrence proves neither interpretation nor manufacturer authenticity;
these contracts remain authored applicability declarations.

Two instances of the same MPN use the same obligations independently, with their
own native memberships. Repeated physical pins in a contract are ambiguous and
remain unbound. Absent native physical pins are unmatched. Native XML ambiguity
is refused by the shared inventory reader. Required missing instances, missing
contracts, uncovered pins and manufacturer/PDF mismatches remain unknown.
Optional absent instances are explicitly excluded. Present DNP instances have
unsupported electrical coverage.

## Separate predicates and evidence

| Obligation kind | Executable predicate |
| --- | --- |
| `requires_voltage` | Every verified same-net provider interval lies wholly within the required interval, including equal endpoints. Missing or unverified providers are unknown. |
| `provides_voltage` | Supplies a page-cited interval to consumers on the same native net; alone it supplies no acceptance capability. |
| `must_be_connected` | The native net contains more than one physical terminal. A singleton is a defect. |
| `do_not_connect` | The native net contains only this physical terminal. A multi-terminal net is a defect. |
| `should_have_decoupling_capacitor` | A populated declared capacitor directly joins this net to a ground-class net. |
| `capacitor_to_pin` | A populated declared capacitor directly joins this net to `other_pin` of the same native component, such as BOOT to SW. |
| `needs_external_pullup` | A populated declared resistor directly joins this net to a power-class net. |
| `feedback_divider` | Two distinct populated declared resistors join this net to a power-class and a ground-class net. No ratio is inferred. |

`pin-contracts.json` retains one record per obligation per instance, with status,
physical pin, native net identity, fact ID, PDF hash, physical page, locator and
quote. Voltage comparisons include both consumer and provider pins and source
citations. Scope rail declarations have a scope locator. Separate coverage
records name unsupported instances and uncovered pins. Counts report passing,
defective, unknown, ambiguous and unsupported coverage. Any unknown coverage
makes the overall result `unsupported/unknown`; proven defects remain visible in
the diagnostic records. An empty evaluation cannot pass.

Scope/2 and scope/3 freeze the parts manifest, its existing complete dependency
closure, contract files, fact records and their PDF/acquisition dependencies.
Scope/3 publishes one `pin-contracts:<kind>` predicate per declared obligation
kind other than `provides_voltage`, with explicit ordered prerequisites. A check
containing only providers has no executable acceptance bound. Runtime results
and sandbox outputs use the existing coordinator evidence seal and witness
qualification. Unknown checks cannot establish accepted defects.

This check validates declared part identity and electrical contracts. Canonical
catalog/selection and CAD suitability qualification remain the separate
`parts_subset` check. Capacitance, ESR, placement, resistor values, power-up
sequencing, analog behavior and driver contention are outside coverage. No
voltage propagates through a resistor, regulator, divider or any other component.
The owner must author and review obligations per IC.

## Owned qualification fixtures and mechanism references

`tests/test_pin_contracts.py` authors synthetic facts, PDF text and connectivity.
It replays an AP2112K-1.8 provider at 1.8 V against a F1C100S consumer requiring
2.5 V and a TPS54302 BOOT-to-SW obligation. Those names identify the mechanism
being tested; the synthetic quotes and pins do not claim to reproduce vendor
specifications. Native integration exports original KiCad fixtures, verifies
owned PDF citations and exercises scope/3 seals, public verification, failure
coverage and tamper refusal. No third-party design, library or datasheet content
was copied.

The ticket's mechanism references are
[tscircuit source pin attributes](https://github.com/tscircuit/circuit-json/blob/main/src/source/properties/source_pin_attributes.ts),
[parts-engine reconciliation](https://github.com/tscircuit/parts-engine/blob/main/lib/datasheets/reconcile-datasheet-circuit-json.ts),
[voltage compatibility](https://github.com/tscircuit/checks/blob/main/lib/check-source-pin-voltage-compatibility.ts)
and [must-connect checks](https://github.com/tscircuit/checks/blob/main/lib/check-pin-must-be-connected.ts).
These are provenance links supplied by the ticket. This implementation was
written against agent-ee's contracts without retrieving or copying upstream
code or fixtures; it does not assert their current license or behavior. The
[file-origin inventory](file-origins.json) records this distinction.

```sh
AGENT_EE_REQUIRE_TOOLCHAIN=1 PYTHONPATH=src python3 -m unittest tests.test_pin_contracts -v
python3 scripts/check_docs.py
python3 scripts/check_origins.py
```

Copper feature instances (`kind: "copper_feature"`) remain unsupported purchased-
part electrical evidence. Their coverage rows state that reason without reading
manufacturer or MPN fields (AGENTEE-315).
