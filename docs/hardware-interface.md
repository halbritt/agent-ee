# Declared hardware interfaces

`hardware_interface` checks an explicitly authored, captured
`agent-ee.interface/1` contract against a preceding successful native `netlist`
export. It protects only the listed schematic properties. The
[original fixture](../fixtures/hardware-interface/contract.json) is a complete
example, with [requirements](../fixtures/hardware-interface/requirements.md) and
[scope](../fixtures/hardware-interface/control.scope.json).

```json
{
  "id": "interface",
  "kind": "hardware_interface",
  "params": {"contract": "contract.json", "netlist_check": "netlist"}
}
```

The owner supplies obligations before evaluation. The coordinator does not infer
interfaces from connector/testpoint prefixes, symbol classes or the first
candidate. A contract's contents do not authenticate its author; the owner must
approve real requirements through the ordinary baseline process. Adding or
changing a contract creates a new baseline. No conversion grants historical
candidates interface coverage.

## Contract fields

The document has exactly `schema`, `components`, `terminals`, `allowed_edits`
and a nonempty `invariants` list. Duplicate JSON fields, unknown fields and
predicate kinds are refused.

- `components` maps owner logical IDs to `locations` and approved `symbols`.
  Each location has exact `sheet` (native hierarchical sheet-instance timestamp
  path, `/` for root) and `reference`. Each symbol is an exact `library:part`
  identity. Multiple locations are explicit permitted reference mappings;
  at most one can exist in a candidate. Locations cannot be shared by logical IDs.
- `terminals` maps logical IDs to a declared `component` ID and string `pin`.
  The native library part must inventory that pin uniquely. Unconnected known
  pins are distinguished from unknown pin mappings.
- `allowed_edits` is an explicit positive list. This version supports only
  `{component, property: "value", values: ["10k"]}` for a component with a
  `value` invariant. These exact strings extend the invariant's `equals` value.
  There are no wildcard exemptions, numeric range coercions or transparent links.
- Each invariant has a unique `id`, `requirement`, exact `source` object with
  captured `path` and `locator`, and `kind`. Requirement/source/check mappings
  must agree with the immutable scope baseline. All requirements bound to this
  check must have declared predicates; an unrelated obligation cannot borrow
  this capability. Version 1 requires exactly one requirement per check, because
  the existing sealed witness binds a whole check. Use separate check instances
  and contracts for independently witnessed requirements.

The supported kinds and additional fields are:

| Kind | Fields | Predicate |
| --- | --- | --- |
| `component` | `component`, Boolean `populated` | Required component exists with declared assembly state. |
| `value` | `component`, string `equals` | Exact value equals declaration or an explicitly allowed edit. |
| `named_net` | `terminal`, nonempty `names` | Terminal belongs to one exact permitted native net name. |
| `same_net` | Two unique `terminals` | Both known terminals belong to the same native conductor. |
| `different_net` | Two unique `terminals` | Both known terminals belong to distinct native conductors. |

Names are case-sensitive and retain leading slashes and hierarchical context.
Aliases must be explicitly listed in immutable `names`. Anonymous nets should
use terminal relations; generated anonymous names are not stable identities.
Disconnected terminals fail both same-net and different-net predicates because
neither establishes membership in a known conductor. Qualified native singleton
`unconnected-(reference-Padpin)` nets (including the native symbol pin-name
segment when present) represent disconnected pins, including explicit no-connect
markers. Their raw net codes, names and terminal locators remain in the report;
they cannot satisfy any conductor predicate. Normalization requires exact native
terminal/name agreement and one node, after duplicate/unknown-node validation.
Local named nets retain their sheet-qualified names. Exact global labels are
read from the captured native hierarchy to distinguish deliberately named singleton
conductors from generated no-connect names; their source locators are retained.
Connected anonymous nets retain membership; a name prefix alone never removes a
conductor.

R/C/L, ferrites, zero-ohm resistors, jumpers and semiconductors never merge native
conductors. A declared load/testpoint is an explicitly named component, not a
heuristically detected role. Presence does not establish loading, solderability,
probe access or electrical behavior. Separate functional measurements establish
transfer behavior. PCB realization still needs native parity/DRC and unconnected
checks; this check makes no claim that matching pad labels establish routing.

## Results and immutable evidence

The reader uses full native XML sheet, component UUID, library part pin inventory
and net-node membership. Every declared sheet path is validated, including unused
sheets. Duplicate net names or codes and absent/duplicate component value elements
are unsupported inventory. An explicitly empty value element is a known empty
string and can violate a nonempty value requirement. Duplicate references
(including across sheets), duplicate
pin numbers, missing parts, unresolved sheets, duplicate UUIDs within one sheet
instance and multiple net
memberships are unsupported. It refuses these cases rather than selecting an
arbitrary dictionary entry. Repeated instances of one child sheet may share
symbol UUIDs; their hierarchical sheet paths distinguish them. A complete export
can prove a required component
absent. Unknown symbol applicability or a missing declared pin in metadata cannot
be promoted to a defect or pass.

`work/<id>/interface.json` retains the candidate identity, contract/XML hashes,
expected declaration, resolved native identities, exact actual values/membership,
locators and `satisfied`, `violation` or `not_evaluated` status per invariant.
Complete evaluation with a violation yields `ok/defect`; complete satisfaction
produces `ok/pass`. Any unsupported mapping makes the whole check
`unsupported/unknown`, retaining known violations only as diagnostics.

Runtime registration and scope/3 capability are paired. Scope/3 reports exact
`interface:<invariant-id>` predicates and ordered netlist prerequisite. The
captured contract is immutable in both scope/2 and scope/3 and cannot be a
campaign repair target. Native design edits alone preserve the baseline ID.
The normal coordinator evidence seal, check finding and requirement witness
remain the only path to accepted defects; initial reviewer opinions have no
additional authority. Tampering with raw XML or the normalized report invalidates
the seal and failed-criterion witness.

The original synthetic corpus commits its requirements, contract and expected
mutation violations before native runs. Strict native tests exercise load/probe
deletion, depopulation, swapped pins, disconnection, explicit rename aliases,
unsupported mappings, seal tamper and a legitimate resistor repair with a loaded
ngspice response. Scripted independent initial reviews and central adjudication
test evidence authority; they do not establish live-model reliability or physical
validation. Undeclared topology is outside this contract's coverage.

The fixture includes an empty original board placeholder because the review CLI
requires a captured board path. No board check or PCB validity is claimed.
